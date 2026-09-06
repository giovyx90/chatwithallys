from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from allys.config import Settings


class BrainUnavailable(RuntimeError):
    """Nessun backend ha prodotto una risposta.

    Chi chiama decide cosa farne: quasi sempre la risposta giusta e' tacere.
    Un tempo qui tornava una frase di scuse, che finiva in chat e nella
    cronologia come se Allys l'avesse detta davvero: bastava un modello lento
    per riempire il gruppo di scuse.
    """


class BrainBusy(BrainUnavailable):
    """C'e' gia' troppa gente in coda davanti al modello."""


@dataclass(frozen=True)
class Backend:
    """One place Allys can think. `predict_scale` lets the fast brain talk longer."""

    name: str
    base_url: str
    chat_model: str
    predict_scale: float = 1.0
    timeout: float = 120.0

    def scaled(self, num_predict: int) -> int:
        return max(24, int(round(num_predict * self.predict_scale)))


class OllamaClient:
    """Routes chat to the GPU box when it is up, otherwise to the always-on VPS.

    Embeddings never move: they must stay in the vector space Qdrant was built with.
    """

    def __init__(self, settings: Settings):
        self.home = Backend(
            name="vps",
            base_url=settings.ollama_base_url.rstrip("/"),
            chat_model=settings.ollama_chat_model,
            timeout=float(settings.ollama_timeout_seconds),
        )
        gpu_url = (settings.ollama_gpu_base_url or "").strip().rstrip("/")
        self.gpu: Backend | None = None
        if gpu_url:
            self.gpu = Backend(
                name="gpu",
                base_url=gpu_url,
                chat_model=settings.ollama_gpu_chat_model or settings.ollama_chat_model,
                predict_scale=settings.ollama_gpu_predict_scale,
                timeout=float(settings.ollama_gpu_timeout_seconds),
            )
        self.embed_model = settings.ollama_embed_model
        self.keep_alive = str(settings.ollama_keep_alive)
        self._probe_seconds = max(5, settings.ollama_gpu_probe_seconds)
        self._probe_timeout = 2.0
        self._gpu_up = False
        self._gpu_checked_at = 0.0
        self.last_backend = self.home.name

        # Il modello risponde a una richiesta alla volta: su CPU le altre
        # aspettano comunque dentro Ollama, ma li' nessuno le conta e scadono
        # tutte insieme. Le teniamo in fila qui, e oltre la coda diciamo subito
        # di no invece di accumulare risposte che arriveranno fuori tempo.
        self._max_waiting = max(0, int(settings.ollama_max_queue))
        self._waiting = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._gate: asyncio.Semaphore | None = None
        self._probe_lock_obj: asyncio.Lock | None = None

    # -- concorrenza -------------------------------------------------------

    def _bind_loop(self) -> None:
        """Lega lucchetti e semaforo al loop corrente.

        I test girano piu' ``asyncio.run`` sullo stesso client, e una primitiva
        asyncio creata in un loop non si puo' usare in un altro.
        """
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._gate = asyncio.Semaphore(1)
        self._probe_lock_obj = asyncio.Lock()
        self._waiting = 0

    @property
    def _probe_lock(self) -> asyncio.Lock:
        self._bind_loop()
        assert self._probe_lock_obj is not None
        return self._probe_lock_obj

    # -- backend selection -------------------------------------------------

    def _mark_gpu(self, up: bool) -> None:
        self._gpu_up = up
        self._gpu_checked_at = time.monotonic()

    async def gpu_available(self, force: bool = False) -> bool:
        if self.gpu is None:
            return False
        fresh = (time.monotonic() - self._gpu_checked_at) < self._probe_seconds
        if fresh and not force:
            return self._gpu_up
        async with self._probe_lock:
            # another coroutine may have probed while we waited for the lock
            if not force and (time.monotonic() - self._gpu_checked_at) < self._probe_seconds:
                return self._gpu_up
            try:
                async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
                    response = await client.get(f"{self.gpu.base_url}/api/tags")
                    response.raise_for_status()
            except Exception:
                self._mark_gpu(False)
            else:
                self._mark_gpu(True)
        return self._gpu_up

    async def backends(self) -> list[Backend]:
        if await self.gpu_available():
            return [self.gpu, self.home]  # type: ignore[list-item]
        return [self.home]

    async def status(self) -> dict[str, Any]:
        gpu_up = await self.gpu_available(force=True)
        active = self.gpu if (gpu_up and self.gpu) else self.home
        return {
            "active": active.name,
            "model": active.chat_model,
            "gpu_configured": self.gpu is not None,
            "gpu_up": gpu_up,
        }

    # -- inference ---------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        num_predict: int = 56,
        temperature: float = 0.75,
        timeout: float | None = None,
    ) -> str:
        """Una risposta vera, oppure ``BrainUnavailable``. Mai una scusa.

        ``timeout`` serve ai testi lunghi (podcast, recap): li aspetta chi li ha
        chiesti, quindi possono prendersi piu' del minuto concesso a una battuta.
        """
        self._bind_loop()
        assert self._gate is not None
        if self._waiting > self._max_waiting:
            raise BrainBusy("troppe richieste in coda davanti al modello")
        self._waiting += 1
        try:
            async with self._gate:
                return await self._chat_anywhere(messages, num_predict, temperature, timeout)
        finally:
            self._waiting -= 1

    async def _chat_anywhere(
        self,
        messages: list[dict[str, str]],
        num_predict: int,
        temperature: float,
        timeout: float | None = None,
    ) -> str:
        last_error: Exception | None = None
        for backend in await self.backends():
            try:
                content = await self._chat_on(backend, messages, num_predict, temperature, timeout)
            except Exception as error:  # the GPU box can vanish mid-sentence
                last_error = error
                if backend.name == "gpu":
                    self._mark_gpu(False)
                    continue
                raise BrainUnavailable(str(error) or backend.name) from error
            self.last_backend = backend.name
            return content
        raise BrainUnavailable(str(last_error) if last_error else "nessun backend disponibile")

    async def _chat_on(
        self,
        backend: Backend,
        messages: list[dict[str, str]],
        num_predict: int,
        temperature: float,
        timeout: float | None = None,
    ) -> str:
        async with httpx.AsyncClient(timeout=timeout or backend.timeout) as client:
            response = await client.post(
                f"{backend.base_url}/api/chat",
                json={
                    "model": backend.chat_model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "keep_alive": self.keep_alive,
                    "options": {
                        "num_predict": backend.scaled(num_predict),
                        "temperature": temperature,
                    },
                },
            )
            response.raise_for_status()
        data: dict[str, Any] = response.json()
        message = data.get("message", {})
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("thinking") or "").strip()
        if not content:
            raise BrainUnavailable(f"{backend.name} ha risposto senza testo")
        return content

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.home.base_url}/api/embed",
                json={"model": self.embed_model, "input": text},
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{self.home.base_url}/api/embeddings",
                    json={"model": self.embed_model, "prompt": text},
                )
            response.raise_for_status()
        data: dict[str, Any] = response.json()
        if "embedding" in data:
            return [float(value) for value in data["embedding"]]
        embeddings = data.get("embeddings") or []
        if embeddings and isinstance(embeddings[0], list):
            return [float(value) for value in embeddings[0]]
        raise ValueError("embedding response missing vector")
