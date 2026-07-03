"""Analisi sentiment leggera ma espressiva per i messaggi di gruppo.

Resta sincrona e senza dipendenze (viene chiamata su OGNI messaggio, quindi non
puo permettersi una chiamata LLM), ma e molto piu ricca del semplice conteggio
di dieci parole: pesa il lessico IT/EN, le emoji, gli intensificatori, la
negazione, le risate e i segnali di enfasi (maiuscole, punti esclamativi).

Il contratto pubblico resta invariato:
- ``score_text(text) -> float`` con segno coerente (positivo/negativo);
- ``mentioned_symbols(text, symbols) -> list[str]``.
La borsa continua a usare ``score_text``; il segno e la scala (circa -3..3)
restano compatibili con l'aggregazione dei prezzi esistente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Lessico pesato. Valori piu alti = sentimento piu forte.
POSITIVE_LEXICON: dict[str, float] = {
    # forte
    "goat": 1.6, "based": 1.5, "top": 1.4, "leggenda": 1.6, "capolavoro": 1.7,
    "fenomeno": 1.5, "grandissimo": 1.6, "spettacolo": 1.5, "bomba": 1.4,
    "epico": 1.5, "stupendo": 1.5, "fantastico": 1.5, "incredibile": 1.3,
    "pazzesco": 1.4, "mitico": 1.4, "genio": 1.4, "amore": 1.3, "gioia": 1.3,
    # medio
    "grande": 1.0, "forte": 1.0, "bello": 1.0, "bella": 1.0, "ottimo": 1.1,
    "ottima": 1.1, "win": 1.1, "vinto": 1.1, "vittoria": 1.1, "successo": 1.1,
    "perfetto": 1.1, "figata": 1.2, "fico": 1.0, "figo": 1.0, "wow": 1.0,
    "evviva": 1.1, "grazie": 0.8, "complimenti": 1.1, "bravo": 1.0, "brava": 1.0,
    "gg": 1.0, "carino": 0.8, "simpatico": 0.8, "orgoglioso": 1.0,
    # inglese / gerghi
    "great": 1.1, "nice": 0.9, "love": 1.2, "awesome": 1.4, "cool": 0.9,
    "poggers": 1.3, "pog": 1.2, "cracked": 1.1, "clean": 0.8, "godlike": 1.5,
    # soft
    "bene": 0.6, "buono": 0.6, "ok": 0.3, "okay": 0.3, "tranquillo": 0.4,
    "contento": 0.9, "felice": 1.0, "sereno": 0.6, "meglio": 0.6, "top1": 1.2,
}

NEGATIVE_LEXICON: dict[str, float] = {
    # forte
    "schifo": 1.6, "vergogna": 1.5, "disastro": 1.5, "tragico": 1.5,
    "orribile": 1.6, "pessimo": 1.5, "pessima": 1.5, "terribile": 1.5,
    "imbarazzante": 1.4, "vomito": 1.6, "merda": 1.6, "cesso": 1.4,
    "osceno": 1.5, "penoso": 1.4, "ridicolo": 1.3, "patetico": 1.4,
    # medio
    "cringe": 1.2, "fail": 1.2, "male": 1.0, "brutto": 1.0, "brutta": 1.0,
    "trash": 1.2, "perso": 1.0, "sconfitta": 1.1, "rotto": 0.9, "flop": 1.2,
    "noia": 0.9, "noioso": 1.0, "scarso": 1.1, "scarsa": 1.1, "delusione": 1.2,
    "deluso": 1.1, "triste": 1.0, "tristezza": 1.1, "odio": 1.4, "rabbia": 1.2,
    "incazzato": 1.2, "arrabbiato": 1.1, "stanco": 0.7, "stufo": 1.0,
    # inglese / gerghi
    "bad": 1.0, "trash1": 1.2, "worst": 1.4, "hate": 1.4, "sad": 1.0,
    "mid": 0.9, "cope": 0.9, "copium": 1.0, "ratio": 0.8, "rip": 0.8,
    "skill": 0.5,  # "skill issue" gestito da bigrammi sotto
    # soft
    "boh": 0.5, "mah": 0.5, "meh": 0.6, "peccato": 0.6, "peggio": 0.8,
    "problema": 0.5, "bug": 0.5, "errore": 0.5,
}

# Bigrammi/espressioni intere (piu affidabili delle singole parole).
POSITIVE_PHRASES: dict[str, float] = {
    "che bello": 1.2, "troppo forte": 1.4, "ben fatto": 1.1, "gran bel": 1.1,
    "sei un mito": 1.5, "la svolta": 1.0, "ci sta": 0.5, "lets go": 1.3,
    "let's go": 1.3, "w allys": 1.2,
}
NEGATIVE_PHRASES: dict[str, float] = {
    "skill issue": 1.3, "che schifo": 1.6, "che palle": 1.2, "che noia": 1.1,
    "non ci siamo": 1.0, "fa cagare": 1.6, "fa pena": 1.3, "l allys": 0.0,
    "non mi piace": 1.2, "non va": 0.8, "che tristezza": 1.3,
}

INTENSIFIERS: dict[str, float] = {
    "molto": 1.4, "troppo": 1.5, "super": 1.5, "assai": 1.3, "davvero": 1.3,
    "veramente": 1.3, "tanto": 1.3, "cosi": 1.2, "estremamente": 1.7,
    "mega": 1.5, "ultra": 1.6, "iper": 1.6, "proprio": 1.2, "veramente1": 1.0,
}
DAMPENERS: dict[str, float] = {
    "poco": 0.6, "leggermente": 0.7, "abbastanza": 0.8, "quasi": 0.8,
    "forse": 0.85, "un po": 0.75, "unpo": 0.75,
}
NEGATIONS = {"non", "no", "mai", "niente", "nessun", "nessuno", "senza", "manco"}

POSITIVE_EMOJI = {
    "😀", "😃", "😄", "😁", "😆", "😊", "🙂", "😉", "😍", "🥰", "😘", "🤩",
    "😎", "🤣", "😂", "🥳", "👍", "🔥", "💪", "❤️", "❤", "💜", "💚", "✨",
    "🙌", "👏", "💯", "🏆", "🚀", "😻", "🤟", "👑", "😇", "🤙",
}
NEGATIVE_EMOJI = {
    "😞", "😔", "😢", "😭", "😠", "😡", "🤬", "😤", "👎", "💩", "🤮", "🤢",
    "😩", "😫", "😒", "🙄", "😬", "😰", "😨", "☠️", "💀", "🥶", "😱", "🤡",
    "😾", "😿", "❌",
}

_WORD_RE = re.compile(r"[a-zA-Zàèéìòùáíóú0-9']+")
_MAX_ABS = 3.0


@dataclass(frozen=True)
class Sentiment:
    """Risultato dettagliato dell'analisi."""

    score: float          # segno + intensita, clampato a [-3, 3]
    magnitude: float      # quanto "carico" e il messaggio (>= 0)
    label: str            # positivo | negativo | neutro
    positives: int        # numero di segnali positivi trovati
    negatives: int        # numero di segnali negativi trovati


def _laughter_bonus(lowered: str) -> float:
    # ahah / hahaha / lol / lmao => positivo leggero
    if re.search(r"(?:a?ha){2,}|(?:ah){2,}|lol|lmao|ahah|ihih|eheh", lowered):
        return 0.7
    return 0.0


def _emphasis_multiplier(text: str) -> float:
    mult = 1.0
    exclaims = text.count("!")
    if exclaims:
        mult *= min(1.5, 1.0 + 0.12 * exclaims)
    # PAROLE IN MAIUSCOLO (urlare) amplificano
    shouty = re.findall(r"\b[A-ZÀÈÉÌÒÙ]{3,}\b", text)
    if len(shouty) >= 1:
        mult *= min(1.4, 1.0 + 0.1 * len(shouty))
    return mult


def analyze(text: str) -> Sentiment:
    raw = text or ""
    lowered = raw.lower()
    tokens = _WORD_RE.findall(lowered)

    total = 0.0
    positives = 0
    negatives = 0

    # Frasi intere (bigrammi/trigrammi) prima delle singole parole.
    for phrase, weight in POSITIVE_PHRASES.items():
        if weight and phrase in lowered:
            total += weight
            positives += 1
    for phrase, weight in NEGATIVE_PHRASES.items():
        if weight and phrase in lowered:
            total -= weight
            negatives += 1

    # Parole singole con negazione e intensificatori nella finestra precedente.
    for i, token in enumerate(tokens):
        pos = POSITIVE_LEXICON.get(token)
        neg = NEGATIVE_LEXICON.get(token)
        if pos is None and neg is None:
            continue
        value = pos if pos is not None else -neg

        window = tokens[max(0, i - 3):i]
        negated = any(w in NEGATIONS for w in window)
        boost = 1.0
        for w in window:
            if w in INTENSIFIERS:
                boost *= INTENSIFIERS[w]
            elif w in DAMPENERS:
                boost *= DAMPENERS[w]
        value *= boost
        if negated:
            # "non male" -> lievemente positivo, "non bello" -> negativo
            value *= -0.6

        total += value
        if value > 0:
            positives += 1
        elif value < 0:
            negatives += 1

    # Emoji.
    for ch in raw:
        if ch in POSITIVE_EMOJI:
            total += 0.9
            positives += 1
        elif ch in NEGATIVE_EMOJI:
            total -= 0.9
            negatives += 1

    # Risate = positivita leggera.
    laughter = _laughter_bonus(lowered)
    if laughter:
        total += laughter
        positives += 1

    # Enfasi (maiuscole / punti esclamativi) amplifica il segnale esistente.
    total *= _emphasis_multiplier(raw)

    score = max(-_MAX_ABS, min(_MAX_ABS, total))
    magnitude = min(_MAX_ABS, abs(total))
    if score > 0.35:
        label = "positivo"
    elif score < -0.35:
        label = "negativo"
    else:
        label = "neutro"
    return Sentiment(
        score=round(score, 4),
        magnitude=round(magnitude, 4),
        label=label,
        positives=positives,
        negatives=negatives,
    )


def score_text(text: str) -> float:
    """Punteggio sentiment con segno (compatibile con la vecchia API/borsa)."""
    return analyze(text).score


def mood_summary(scores: list[float]) -> dict[str, float | str]:
    """Riassume l'umore di un insieme di punteggi (per feature /mood)."""
    if not scores:
        return {"label": "silenzio", "average": 0.0, "energy": 0.0}
    average = sum(scores) / len(scores)
    energy = sum(abs(s) for s in scores) / len(scores)
    if average > 0.5:
        label = "carico e positivo"
    elif average > 0.15:
        label = "sereno"
    elif average < -0.5:
        label = "teso"
    elif average < -0.15:
        label = "un po' giu"
    else:
        label = "neutro"
    return {"label": label, "average": round(average, 3), "energy": round(energy, 3)}


def mentioned_symbols(text: str, symbols: list[str]) -> list[str]:
    upper = text.upper()
    return [symbol for symbol in symbols if symbol in upper or f"${symbol}" in upper]
