import random
import re
from dataclasses import dataclass
from typing import Any


MEME_MODE_PROBABILITY = {
    "off": 0.0,
    "low": 0.15,
    "medium": 0.25,
    "high": 0.45,
}

_UNVIEWABLE_MEDIA_URLS: set[str] = set()

EXPLICIT_MEDIA_WORDS = {"gif", "meme", "memino", "video", "link", "reaction", "reazione"}
STOPWORDS = {
    "allys",
    "questo",
    "questa",
    "quello",
    "quella",
    "sono",
    "come",
    "cosa",
    "quando",
    "perche",
    "perché",
    "della",
    "delle",
    "degli",
    "anche",
    "solo",
    "tipo",
}


@dataclass(frozen=True)
class MemeLink:
    category: str
    title: str
    image_url: str
    keywords: tuple[str, ...]
    media_type: str = "photo"


MEME_LINKS = [
    MemeLink(
        "confusion",
        "Confused",
        "https://i.imgur.com/N0N0G7C.png",
        ("confuso", "capire", "brain", "matematica", "wtf"),
    ),
    MemeLink(
        "skill_issue",
        "Skill issue",
        "https://i.imgur.com/8fK4QwS.png",
        ("skill", "errore", "fallito", "problema", "bug"),
    ),
    MemeLink(
        "cope",
        "Copium",
        "https://i.imgur.com/N9WfA7N.png",
        ("cope", "rosicare", "scusa", "negare", "perdere"),
    ),
    MemeLink(
        "drama",
        "This is fine",
        "https://i.imgur.com/3uJvYJf.png",
        ("drama", "fuoco", "casino", "disastro", "panico"),
    ),
    MemeLink(
        "celebration",
        "Let's go",
        "https://i.imgur.com/qNQ8t1L.png",
        ("vinto", "bene", "grande", "gg", "successo"),
    ),
    MemeLink(
        "reaction",
        "Surprised",
        "https://i.imgur.com/QrQ6q4r.png",
        ("sorpresa", "ovvio", "shock", "assurdo"),
    ),
    MemeLink(
        "coding",
        "Works on my machine",
        "https://i.imgur.com/4d8Jw1Q.png",
        ("codice", "server", "deploy", "bug", "api"),
    ),
    MemeLink(
        "minecraft",
        "Minecraft",
        "https://i.imgur.com/7ZgL5bO.png",
        ("minecraft", "server", "creeper", "place", "blocco"),
    ),
    MemeLink(
        "generic",
        "Meme",
        "https://i.imgur.com/Lh9Qf6p.png",
        ("meme", "reaction", "lol", "ridere"),
    ),
]


def extract_keywords(text: str, limit: int = 12) -> list[str]:
    words = []
    for word in re.findall(r"[a-zA-Z0-9_àèéìòù]{3,}", (text or "").lower()):
        if word not in STOPWORDS and word not in words:
            words.append(word)
    return words[:limit]


def tags_for_media(caption: str | None) -> list[str]:
    return extract_keywords(caption or "", limit=16)


def explicit_media_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in EXPLICIT_MEDIA_WORDS)


def should_attach_meme(mode: str | None, prompt: str, reply: str, rng: random.Random | None = None) -> bool:
    probability = MEME_MODE_PROBABILITY.get(mode or "medium", 0.25)
    if probability <= 0:
        return False
    if explicit_media_request(prompt):
        probability = max(probability, 0.85)
    randomizer = rng or random
    return randomizer.random() < probability


def desired_media_types(prompt: str) -> list[str] | None:
    lowered = (prompt or "").lower()
    if "gif" in lowered:
        return ["animation", "gif_document"]
    if "video" in lowered:
        return ["video", "video_note", "video_document"]
    return None


def pick_meme_link(query: str) -> MemeLink | None:
    keywords = set(extract_keywords(query, limit=20))
    ranked: list[tuple[int, MemeLink]] = []
    for item in MEME_LINKS:
        if item.image_url in _UNVIEWABLE_MEDIA_URLS:
            continue
        score = sum(1 for keyword in item.keywords if keyword in keywords)
        if score:
            ranked.append((score, item))
    if not ranked and keywords:
        ranked.append((1, next(item for item in MEME_LINKS if item.category == "generic")))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if ranked else None


def meme_caption(reply: str, link: MemeLink | None = None, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", reply or "").strip()
    if link and len(text) < max_chars - 18:
        suffix = f" · {link.title}"
        if len(text) + len(suffix) <= max_chars:
            text = f"{text}{suffix}" if text else link.title
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;: ")
    return f"{clipped}..."


def media_debug_line(row: dict[str, Any]) -> str:
    caption = (row.get("caption") or "").strip()
    label = caption[:70] if caption else "(senza caption)"
    return f"#{row['id']} {row['media_type']} · {label}"


def is_unviewable_media_error(error: Exception) -> bool:
    text = str(error).lower()
    markers = (
        "content not viewable",
        "failed to get http url content",
        "wrong type of the web page content",
        "wrong file identifier/http url specified",
    )
    return any(marker in text for marker in markers)


def remember_unviewable_media(link: MemeLink) -> None:
    _UNVIEWABLE_MEDIA_URLS.add(link.image_url)
