"""The GPU box comes and goes; Allys must never notice out loud."""

import asyncio

import pytest

from allys import ollama as ollama_module
from allys.ollama import OllamaClient


class FakeSettings:
    ollama_base_url = "http://ollama:11434"
    ollama_chat_model = "qwen3:4b"
    ollama_embed_model = "nomic-embed-text"
    ollama_gpu_base_url = "http://host.docker.internal:11435"
    ollama_gpu_chat_model = "qwen3:8b"
    ollama_gpu_probe_seconds = 30
    ollama_gpu_predict_scale = 2.0
    ollama_timeout_seconds = 90.0


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpx:
    """Stands in for the httpx module inside allys.ollama."""

    def __init__(self):
        self.gpu_up = True
        self.gpu_chat_fails = False
        self.calls: list[tuple[str, dict]] = []

    def AsyncClient(self, **_kwargs):  # noqa: N802 - mimics httpx
        outer = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, url):
                outer.calls.append(("GET", {"url": url}))
                if "11435" in url and not outer.gpu_up:
                    raise ConnectionRefusedError(url)
                return FakeResponse({"models": []})

            async def post(self, url, json):
                outer.calls.append(("POST", {"url": url, **json}))
                is_gpu = "11435" in url
                if is_gpu and (not outer.gpu_up or outer.gpu_chat_fails):
                    raise ConnectionRefusedError(url)
                if url.endswith("/api/embed"):
                    return FakeResponse({"embeddings": [[0.1, 0.2]]})
                who = "gpu" if is_gpu else "vps"
                return FakeResponse({"message": {"content": f"ciao dal {who}"}})

        return _Client()


@pytest.fixture
def fake_httpx(monkeypatch):
    fake = FakeHttpx()
    monkeypatch.setattr(ollama_module, "httpx", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def test_usa_la_gpu_quando_e_accesa(fake_httpx):
    client = OllamaClient(FakeSettings())
    assert run(client.chat([{"role": "user", "content": "ehi"}])) == "ciao dal gpu"
    assert client.last_backend == "gpu"


def test_ripiega_sulla_vps_quando_il_pc_e_spento(fake_httpx):
    fake_httpx.gpu_up = False
    client = OllamaClient(FakeSettings())
    assert run(client.chat([{"role": "user", "content": "ehi"}])) == "ciao dal vps"
    assert client.last_backend == "vps"


def test_ripiega_se_il_pc_sparisce_a_meta_richiesta(fake_httpx):
    """The probe says yes, then the chat call dies: the user still gets a reply."""
    fake_httpx.gpu_chat_fails = True
    client = OllamaClient(FakeSettings())
    assert run(client.chat([{"role": "user", "content": "ehi"}])) == "ciao dal vps"
    assert client.last_backend == "vps"
    assert client._gpu_up is False


def test_senza_gpu_configurata_non_sonda_nulla(fake_httpx):
    class NoGpu(FakeSettings):
        ollama_gpu_base_url = "   "

    client = OllamaClient(NoGpu())
    assert run(client.chat([{"role": "user", "content": "ehi"}])) == "ciao dal vps"
    assert not [call for call in fake_httpx.calls if call[0] == "GET"]


def test_la_sonda_e_in_cache(fake_httpx):
    client = OllamaClient(FakeSettings())
    for _ in range(3):
        run(client.chat([{"role": "user", "content": "ehi"}]))
    probes = [call for call in fake_httpx.calls if call[0] == "GET"]
    assert len(probes) == 1


def test_la_gpu_puo_rispondere_piu_a_lungo(fake_httpx):
    client = OllamaClient(FakeSettings())
    run(client.chat([{"role": "user", "content": "ehi"}], num_predict=50))
    post = next(call[1] for call in fake_httpx.calls if call[0] == "POST")
    assert post["model"] == "qwen3:8b"
    assert post["options"]["num_predict"] == 100


def test_gli_embedding_restano_sempre_sulla_vps(fake_httpx):
    """Moving them would mean a second vector space inside the same Qdrant collection."""
    client = OllamaClient(FakeSettings())
    run(client.embed("ciao"))
    posts = [call[1]["url"] for call in fake_httpx.calls if call[0] == "POST"]
    assert posts and all("11435" not in url for url in posts)


def test_status_dice_chi_sta_rispondendo(fake_httpx):
    client = OllamaClient(FakeSettings())
    assert run(client.status()) == {
        "active": "gpu",
        "model": "qwen3:8b",
        "gpu_configured": True,
        "gpu_up": True,
    }
    fake_httpx.gpu_up = False
    assert run(client.status())["active"] == "vps"
