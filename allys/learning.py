"""Come Allys impara dal gruppo, senza riaddestrare nulla.

Il modello locale resta lo stesso: quello che cambia e' *cosa gli mettiamo
davanti*. Qui vive la parte pura di questo apprendimento continuo:

- ``extract_lexicon``: i tormentoni veri del gruppo (parole e coppie di parole
  che usano in tanti, non solo uno che spamma);
- ``reaction_delta`` / ``reply_delta``: quanto una risposta di Allys e' piaciuta,
  letto dai segnali gratis che Telegram ci da' (reaction, risposte, sentiment);
- ``select_style_examples``: gli scambi andati bene diventano esempi few-shot;
- ``tone_adjustment``: se il gruppo la bastona, Allys si accorcia e si fa piu'
  utile; se la premia, si allunga un filo.

Nessun I/O: tutto testabile, ``db.py`` fa le query e ``bot.py`` cuce insieme.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from allys.sentiment import score_text

# Parole troppo comuni per essere un tormentone di gruppo.
_LEXICON_STOPWORDS = {
    "allys", "questo", "questa", "quello", "quella", "questi", "queste",
    "sono", "siamo", "siete", "essere", "avere", "come", "cosa", "quando",
    "perche", "perché", "della", "delle", "degli", "dello", "anche", "solo",
    "tipo", "adesso", "ancora", "sempre", "niente", "nulla", "molto", "troppo",
    "dopo", "prima", "verso", "senza", "sopra", "sotto", "dove", "quale",
    "quali", "quanto", "tutto", "tutti", "tutte", "tutta", "fatto", "fare",
    "detto", "dire", "vedere", "visto", "andare", "voglio", "posso", "devo",
    "praticamente", "comunque", "pero", "però", "quindi", "allora", "magari",
    "https", "http", "www", "youtube", "telegram", "grazie", "scusa",
}

_WORD_RE = re.compile(r"[a-zà-ù0-9][a-zà-ù0-9'_]{2,}", re.IGNORECASE)

# Reaction Telegram lette come voto sulla risposta di Allys.
_REACTION_SCORES: dict[str, float] = {
    "😂": 1.2, "🤣": 1.2, "🔥": 1.0, "❤": 1.0, "❤️": 1.0, "🥰": 1.0,
    "👍": 0.8, "🎉": 0.8, "🤩": 0.9, "👏": 0.8, "💯": 1.0, "🏆": 1.0,
    "🤯": 0.7, "😍": 1.0, "🕊": 0.3, "😇": 0.4, "🤝": 0.5, "⚡": 0.5,
    "👎": -1.0, "💩": -1.4, "🤮": -1.4, "🥱": -1.0, "🤡": -0.8,
    "😐": -0.5, "🙄": -0.7, "😴": -0.9, "🤨": -0.4, "💔": -0.8,
}

# Frasi con cui il gruppo dice, di fatto, "hai rotto".
_SHUTUP_MARKERS = (
    "zitta", "zitto", "silenzio", "taci", "smettila", "basta allys",
    "che palle", "hai rotto", "sei noiosa", "sei noioso", "non ti ha chiesto",
    "nessuno ti ha chiesto", "spam", "smetti",
)


# Solo questi valgono come richiesta esplicita di silenzio: "che palle" e' un
# voto negativo, non un ordine di sparire.
_EXPLICIT_SHUTUP = ("zitta", "zitto", "silenzio", "taci", "smettila", "smetti", "basta allys")


def looks_like_shutup(text: str) -> bool:
    """Il messaggio sta dicendo ad Allys di smetterla?"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _SHUTUP_MARKERS)


def is_explicit_shutup(text: str) -> bool:
    """Distingue "stai zitta" (ordine) da "che palle" (solo malumore)."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _EXPLICIT_SHUTUP)


def extract_lexicon(
    messages: list[dict[str, Any]],
    min_users: int = 2,
    min_count: int = 3,
    limit: int = 8,
) -> list[str]:
    """I tormentoni del gruppo: parole/bigrammi ripetuti da piu' persone.

    Il vincolo ``min_users`` e' il punto chiave: cosi' un singolo che ripete la
    stessa parola cento volte non detta il lessico di tutti.
    """
    counts: dict[str, int] = {}
    users: dict[str, set[str]] = {}
    for row in messages:
        author = str(row.get("username") or row.get("user_id") or "anon").lower()
        if author == "allys":
            continue
        tokens = [
            token.lower()
            for token in _WORD_RE.findall(str(row.get("text") or ""))
            if token.lower() not in _LEXICON_STOPWORDS and len(token) >= 4
        ]
        seen_here: set[str] = set()
        grams = list(tokens)
        grams += [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
        for gram in grams:
            if gram in seen_here:
                continue
            seen_here.add(gram)
            counts[gram] = counts.get(gram, 0) + 1
            users.setdefault(gram, set()).add(author)

    ranked = [
        (gram, count)
        for gram, count in counts.items()
        if count >= min_count and len(users.get(gram, ())) >= min_users
    ]
    # I bigrammi valgono di piu': "skill issue" dice piu' di "skill".
    ranked.sort(key=lambda item: (item[1] * (1.6 if " " in item[0] else 1.0)), reverse=True)

    chosen: list[str] = []
    used_words: set[str] = set()
    for gram, _count in ranked:
        # Un tormentone per concetto: se una parola e' gia' rappresentata, salta.
        # Cosi' non escono insieme "skill issue" e "issue clamoroso".
        words = set(gram.split())
        if words & used_words:
            continue
        chosen.append(gram)
        used_words |= words
        if len(chosen) >= limit:
            break
    return chosen


def reaction_delta(emoji: str) -> float:
    """Quanto vale una reaction messa su un messaggio di Allys."""
    return _REACTION_SCORES.get((emoji or "").strip(), 0.0)


def reactions_delta(old: list[str], new: list[str]) -> float:
    """Differenza di gradimento tra il vecchio e il nuovo set di reaction."""
    added = list(new)
    for emoji in old:
        if emoji in added:
            added.remove(emoji)
    removed = list(old)
    for emoji in new:
        if emoji in removed:
            removed.remove(emoji)
    return sum(reaction_delta(e) for e in added) - sum(reaction_delta(e) for e in removed)


def reply_delta(reply_text: str) -> float:
    """Quanto vale il fatto che qualcuno *risponda* a un messaggio di Allys.

    Rispondere e' gia' un segnale positivo (l'ha coinvolto), ma se la risposta e'
    "stai zitta" il segnale si ribalta di brutto.
    """
    text = (reply_text or "").strip()
    if not text:
        return 0.0
    if looks_like_shutup(text):
        return -1.5
    engagement = 0.35
    sentiment = score_text(text)
    return round(engagement + (sentiment * 0.4), 4)


@dataclass(frozen=True)
class StyleExample:
    """Uno scambio andato bene, pronto per essere usato come esempio."""

    prompt: str
    reply: str
    score: float


def select_style_examples(
    rows: list[dict[str, Any]],
    limit: int = 3,
    min_score: float = 0.6,
    max_chars: int = 160,
) -> list[StyleExample]:
    """Prende gli scambi meglio riusciti e li rende esempi brevi e puliti."""
    scored: list[StyleExample] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda item: float(item.get("score") or 0), reverse=True):
        score = float(row.get("score") or 0)
        if score < min_score:
            break
        reply = " ".join(str(row.get("reply_text") or "").split())
        prompt = " ".join(str(row.get("prompt_text") or "").split())
        if not reply:
            continue
        key = reply.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        scored.append(
            StyleExample(prompt=prompt[:max_chars], reply=reply[:max_chars], score=round(score, 3))
        )
        if len(scored) >= limit:
            break
    return scored


def format_style_block(examples: list[StyleExample], lexicon: list[str]) -> str:
    """Il blocco di prompt che porta lo stile del gruppo dentro al modello."""
    parts: list[str] = []
    if lexicon:
        parts.append(
            "Modi di dire di questo gruppo (usali se cadono naturali, non forzarli): "
            + ", ".join(lexicon[:8])
            + "."
        )
    if examples:
        lines = ["Tue risposte passate che in questo gruppo hanno funzionato. "
                 "Imita il taglio e la lunghezza, non ricopiare il contenuto:"]
        for example in examples:
            if example.prompt:
                lines.append(f'- gli scrivono "{example.prompt}" e tu rispondi "{example.reply}"')
            else:
                lines.append(f'- "{example.reply}"')
        parts.append("\n".join(lines))
    return "\n".join(parts)


def tone_adjustment(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Regola lunghezza e propensione a essere utile in base al gradimento.

    E' l'unico parametro che Allys "impara" davvero nel tempo: se il gruppo la
    bastona si accorcia e si fa piu' concreta, se la premia si scioglie un po'.
    """
    samples = int((stats or {}).get("samples") or 0)
    average = float((stats or {}).get("average") or 0.0)
    if samples < 5:
        return {"length_factor": 1.0, "helpful_bonus": 0.0, "verdict": "in ascolto"}
    if average <= -0.4:
        return {"length_factor": 0.6, "helpful_bonus": 0.25, "verdict": "il gruppo la trova pesante"}
    if average < 0.15:
        return {"length_factor": 0.85, "helpful_bonus": 0.1, "verdict": "gradimento tiepido"}
    if average > 0.8:
        return {"length_factor": 1.15, "helpful_bonus": -0.05, "verdict": "il gruppo la adora"}
    return {"length_factor": 1.0, "helpful_bonus": 0.0, "verdict": "gradimento buono"}
