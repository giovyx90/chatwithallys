from typing import Any

import httpx

from allys.config import Settings


class OllamaClient:
    def __init__(self, settings: Settings):
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.chat_model = settings.ollama_chat_model
        self.embed_model = settings.ollama_embed_model

    async def chat(self, messages: list[dict[str, str]], num_predict: int = 56, temperature: float = 0.75) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": num_predict, "temperature": temperature},
                },
            )
            response.raise_for_status()
        data: dict[str, Any] = response.json()
        message = data.get("message", {})
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("thinking") or "").strip()
        return content or "Il cervello locale sta facendo una pausa, riprovo tra un attimo."

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": text},
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
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
