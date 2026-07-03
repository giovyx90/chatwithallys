"""Ricerca meme/GIF che funziona davvero.

Il vecchio fallback usava URL imgur hardcoded (spesso morti) e Telegram
rispondeva "content not viewable". Qui invece:

1. cerchiamo su sorgenti reali e affidabili:
   - Giphy (se ``GIPHY_API_KEY`` e configurata) -> GIF pertinenti alla query;
   - Tenor (se ``TENOR_API_KEY`` e configurata);
   - fallback keyless su Reddit (meme-api.com) mappando la query a un subreddit;
2. VALIDIAMO l'URL scelto (status 200, content-type immagine/gif/video,
   dimensione ragionevole) *prima* di passarlo a Telegram.

Cosi non mandiamo mai un media rotto: se nulla e valido, il chiamante ripiega
sul testo. Le funzioni di parsing/selezione sono pure e testabili; solo
``fetch_working_meme`` fa I/O.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("allys.media")

# Tipi di media accettati e limiti.
_MAX_BYTES = 18 * 1024 * 1024
_OK_CONTENT_TYPES = ("image/", "video/")

# Mappa parole chiave -> subreddit per il fallback keyless (Reddit meme-api).
_SUBREDDIT_MAP: list[tuple[tuple[str, ...], str]] = [
    (("minecraft", "creeper", "blocco", "server", "block"), "minecraftmemes"),
    (("codice", "code", "deploy", "bug", "api", "programm", "dev"), "ProgrammerHumor"),
    (("calcio", "calcetto", "gol", "partita", "football", "soccer"), "soccercirclejerk"),
    (("gatto", "gatti", "cat", "cats", "micio"), "catsstandingup"),
    (("cane", "dog", "doggo", "cagnol"), "dogpictures"),
    (("anime", "manga", "weeb"), "Animemes"),
    (("cringe", "imbarazz"), "cringe"),
    (("drama", "casino", "litig"), "trippinthroughtime"),
]
_DEFAULT_SUBREDDITS = ("memes", "dankmemes", "meme")


def pick_subreddit(query: str, rng: random.Random | None = None) -> str:
    """Sceglie un subreddit coerente con la query (per il fallback Reddit)."""
    lowered = (query or "").lower()
    for keywords, subreddit in _SUBREDDIT_MAP:
        if any(word in lowered for word in keywords):
            return subreddit
    randomizer = rng or random
    return randomizer.choice(_DEFAULT_SUBREDDITS)


def media_type_for_url(url: str) -> str:
    """Deduce il tipo di media da inviare a Telegram dall'estensione URL."""
    lowered = url.lower().split("?")[0]
    if lowered.endswith(".gif"):
        return "gif"
    if lowered.endswith((".mp4", ".webm", ".mov")):
        return "video"
    return "photo"


@dataclass(frozen=True)
class RemoteMedia:
    url: str
    media_type: str  # "gif" | "video" | "photo"
    title: str = ""


def parse_giphy(payload: dict[str, Any]) -> list[str]:
    """Estrae URL GIF diretti da una risposta Giphy search."""
    urls: list[str] = []
    for item in (payload or {}).get("data", []) or []:
        images = item.get("images") or {}
        for key in ("downsized_medium", "downsized", "original", "fixed_height"):
            candidate = (images.get(key) or {}).get("url")
            if candidate:
                urls.append(candidate)
                break
    return urls


def parse_tenor(payload: dict[str, Any]) -> list[str]:
    """Estrae URL GIF diretti da una risposta Tenor v2 search."""
    urls: list[str] = []
    for item in (payload or {}).get("results", []) or []:
        formats = item.get("media_formats") or {}
        for key in ("gif", "mediumgif", "tinygif"):
            candidate = (formats.get(key) or {}).get("url")
            if candidate:
                urls.append(candidate)
                break
    return urls


def parse_reddit_memes(payload: dict[str, Any]) -> list[str]:
    """Estrae URL immagine dalla risposta di meme-api.com, saltando nsfw/spoiler."""
    urls: list[str] = []
    memes = (payload or {}).get("memes")
    if memes is None and (payload or {}).get("url"):
        memes = [payload]
    for item in memes or []:
        if item.get("nsfw") or item.get("spoiler"):
            continue
        url = item.get("url")
        if url and not url.lower().split("?")[0].endswith((".gifv",)):
            urls.append(url)
    return urls


async def _validate(client: httpx.AsyncClient, url: str) -> bool:
    """Controlla che l'URL sia realmente scaricabile come immagine/video."""
    try:
        async with client.stream("GET", url, follow_redirects=True, timeout=8.0) as response:
            if response.status_code not in (200, 206):
                return False
            content_type = (response.headers.get("content-type") or "").lower()
            if not content_type.startswith(_OK_CONTENT_TYPES):
                return False
            length = response.headers.get("content-length")
            if length and int(length) > _MAX_BYTES:
                return False
            return True
    except Exception:
        logger.info("meme url validation failed for %s", url)
        return False


async def _search_giphy(client: httpx.AsyncClient, api_key: str, query: str) -> list[str]:
    response = await client.get(
        "https://api.giphy.com/v1/gifs/search",
        params={"api_key": api_key, "q": query or "meme", "limit": 15, "rating": "pg-13", "lang": "it"},
        timeout=8.0,
    )
    response.raise_for_status()
    return parse_giphy(response.json())


async def _search_tenor(client: httpx.AsyncClient, api_key: str, query: str) -> list[str]:
    response = await client.get(
        "https://tenor.googleapis.com/v2/search",
        params={"key": api_key, "q": query or "meme", "limit": 15, "media_filter": "gif", "contentfilter": "medium"},
        timeout=8.0,
    )
    response.raise_for_status()
    return parse_tenor(response.json())


async def _search_reddit(client: httpx.AsyncClient, query: str, rng: random.Random | None = None) -> list[str]:
    subreddit = pick_subreddit(query, rng)
    response = await client.get(f"https://meme-api.com/gimme/{subreddit}/20", timeout=8.0)
    response.raise_for_status()
    return parse_reddit_memes(response.json())


async def fetch_working_meme(
    query: str,
    giphy_api_key: str = "",
    tenor_api_key: str = "",
    reddit_fallback: bool = True,
    rng: random.Random | None = None,
    client: httpx.AsyncClient | None = None,
) -> RemoteMedia | None:
    """Cerca e VALIDA un meme reale. Ritorna None se nulla e utilizzabile."""
    randomizer = rng or random
    own_client = client is None
    http = client or httpx.AsyncClient(headers={"User-Agent": "AllysBot/1.0"})
    try:
        providers = []
        if giphy_api_key:
            providers.append(lambda: _search_giphy(http, giphy_api_key, query))
        if tenor_api_key:
            providers.append(lambda: _search_tenor(http, tenor_api_key, query))
        if reddit_fallback:
            providers.append(lambda: _search_reddit(http, query, randomizer))

        for provider in providers:
            try:
                candidates = await provider()
            except Exception:
                logger.info("meme provider failed, trying next")
                continue
            randomizer.shuffle(candidates)
            for url in candidates[:6]:
                if await _validate(http, url):
                    return RemoteMedia(url=url, media_type=media_type_for_url(url))
        return None
    finally:
        if own_client:
            await http.aclose()
