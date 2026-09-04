"""Quando Allys puo' aprire bocca da sola.

Un bot che interviene a caso diventa insopportabile in due giorni. Qui la
decisione e' esplicita e conservativa: servono *tutte* le condizioni base
(conversazione viva, Allys zitta da un po', quota giornaliera libera) e poi una
probabilita' che sale solo se la chat e' davvero accesa.

Tutto puro e deterministico dato un ``rng``: si testa senza database e senza
Telegram.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

# Livelli di autonomia impostabili per gruppo.
LEVELS = ("off", "bassa", "media", "alta")

# Per ogni livello: quanto deve stare zitta prima di riprovare, quante volte al
# giorno puo' intervenire, quanti messaggi nuovi servono, e quanto e' probabile.
_PROFILES: dict[str, dict[str, float]] = {
    "off": {"cooldown_minutes": 0, "daily_cap": 0, "min_new_messages": 0, "base_chance": 0.0},
    "bassa": {"cooldown_minutes": 180, "daily_cap": 2, "min_new_messages": 40, "base_chance": 0.25},
    "media": {"cooldown_minutes": 75, "daily_cap": 5, "min_new_messages": 18, "base_chance": 0.45},
    "alta": {"cooldown_minutes": 35, "daily_cap": 10, "min_new_messages": 10, "base_chance": 0.7},
}

# La conversazione e' "viva" se l'ultimo messaggio umano e' recente.
_ALIVE_SECONDS = 8 * 60


@dataclass(frozen=True)
class ChatterState:
    """Fotografia della chat nel momento in cui decidiamo."""

    quiet: bool = False
    seconds_since_last_human: float = 0.0
    seconds_since_last_allys: float = 10**6
    new_messages_since_allys: int = 0
    unique_speakers: int = 0
    energy: float = 0.0            # quanto sono carichi i messaggi (0..3)
    interventions_today: int = 0
    pending_question: bool = False  # e' rimasta una domanda per aria
    market_move: float = 0.0        # variazione % piu' forte in borsa nella finestra


@dataclass(frozen=True)
class Decision:
    speak: bool
    kind: str = "silenzio"   # commento | domanda | borsa
    reason: str = ""
    urge: float = 0.0


def profile(level: str) -> dict[str, float]:
    return _PROFILES.get(level, _PROFILES["media"])


def urge_score(state: ChatterState) -> float:
    """Quanta voglia/motivo ha di parlare, 0..1."""
    urge = 0.0
    urge += min(0.35, state.new_messages_since_allys / 60.0)
    urge += min(0.2, state.unique_speakers / 12.0)
    urge += min(0.25, state.energy / 6.0)
    if state.pending_question:
        urge += 0.2
    if abs(state.market_move) >= 0.12:
        urge += 0.2
    return round(min(1.0, urge), 4)


def blocking_reason(state: ChatterState, level: str) -> str | None:
    """Il primo motivo per cui *non* puo' parlare, o None se la strada e' libera.

    Sta separato da ``decide`` perche' e' tutto deterministico e non costa nulla:
    lo scheduler lo usa come filtro prima di andare a leggere dati piu' cari.
    """
    conf = profile(level)
    if level == "off" or conf["daily_cap"] <= 0:
        return "autonomia disattivata"
    if state.quiet:
        return "e' stata zittita"
    if state.seconds_since_last_human > _ALIVE_SECONDS:
        return "la chat e' ferma, non la sveglia lei"
    if state.interventions_today >= conf["daily_cap"]:
        return "quota giornaliera esaurita"
    if state.seconds_since_last_allys < conf["cooldown_minutes"] * 60:
        return "ha parlato da poco"
    if state.new_messages_since_allys < conf["min_new_messages"]:
        return "troppo pochi messaggi nuovi"
    return None


def decide(state: ChatterState, level: str, rng: random.Random | None = None) -> Decision:
    """Decide se e come intervenire spontaneamente."""
    conf = profile(level)
    blocked = blocking_reason(state, level)
    if blocked:
        return Decision(False, reason=blocked)

    urge = urge_score(state)
    chance = min(0.95, conf["base_chance"] * (0.4 + urge))
    randomizer = rng or random
    if randomizer.random() > chance:
        return Decision(False, reason="ha deciso di lasciar correre", urge=urge)

    if abs(state.market_move) >= 0.12:
        kind = "borsa"
    elif state.pending_question:
        kind = "domanda"
    else:
        kind = "commento"
    return Decision(True, kind=kind, reason="conversazione viva e spazio libero", urge=urge)


def prompt_for(kind: str) -> str:
    """Cosa chiediamo al modello quando parte lei."""
    if kind == "borsa":
        return (
            "Nessuno ti ha chiamata: stai commentando di tua iniziativa un movimento "
            "della borsa del gruppo. Una frase sola, da cronista sarcastica, sul titolo "
            "che si e' mosso. Niente saluti, niente domande di servizio."
        )
    if kind == "domanda":
        return (
            "Nessuno ti ha chiamata: nella chat e' rimasta una domanda per aria e tu "
            "hai qualcosa di utile da dire. Rispondi in una o due frasi, concrete, "
            "come se ti fossi intromessa perche' sapevi la risposta."
        )
    return (
        "Nessuno ti ha chiamata: ti stai intromettendo nella conversazione perche' hai "
        "una battuta o un'osservazione che ci sta. Una frase sola, sul pezzo, che "
        "riprende quello che si stanno dicendo. Niente saluti, non presentarti, non "
        "chiedere se serve aiuto."
    )


def parse_level(value: str) -> str | None:
    lowered = (value or "").strip().lower()
    return lowered if lowered in LEVELS else None
