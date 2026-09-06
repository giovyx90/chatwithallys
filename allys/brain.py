"""Il cervello conversazionale di Allys.

Qui vivono le funzioni *pure* che rendono Allys piu sveglia e adattiva:
- capire l'intento del messaggio (domanda, aiuto, chiacchiera, saluto...);
- leggere l'umore recente del gruppo;
- scegliere il tono (utile vs roast) in modo furbo e adattivo per gruppo;
- costruire una persona coerente e un system prompt ricco;
- trasformare gli ultimi messaggi in una trascrizione anonimizzata, cosi Allys
  vede il *filo* della conversazione e non solo tre frammenti di memoria.

Tutto qui e sincrono e senza I/O: cosi e testabile e ``bot.py`` resta snello.
"""

from __future__ import annotations

import random
import re
from typing import Any

from allys.sentiment import mood_summary

# Numero di messaggi recenti da dare in pasto al modello come contesto vivo.
HISTORY_TURNS = 12

# Quante delle sue ultime risposte le rimettiamo davanti come "gia' detto".
# Le sue risposte stanno nella trascrizione come quelle di tutti, e un modello
# piccolo che vede tre volte la stessa struttura la considera il modo giusto di
# parlare: e' cosi' che nasceva il pappagallo ("Vuoi che ti dica X? Bene, ma...").
SELF_REPLY_MEMORY = 5

# Username sentinella con cui salviamo i messaggi di Allys, cosi la trascrizione
# diventa un vero botta-e-risposta invece di soli messaggi utente.
BOT_AUTHOR = "Allys"

_QUESTION_RE = re.compile(r"\?|\b(come|perche|perché|quando|dove|quanto|quale|quali|cosa|chi|puoi|sai|mi\s+spieghi|consigli|aiut)\w*", re.IGNORECASE)
_HELP_MARKERS = (
    "aiuto", "aiutami", "aiutare", "spiega", "spiegami", "come si", "come faccio",
    "consiglio", "consigli", "suggerisci", "guida", "tutorial", "non capisco",
    "non riesco", "problema", "errore", "bug", "come funziona",
)
_GREETING_MARKERS = ("ciao", "buongiorno", "buonasera", "salve", "ehi", "hey", "yo ", "ola")
_MINIGAME_MARKERS = (
    "borsa", "azioni", "azienda", "aziende", "crowns", "corone", "portfolio",
    "compra", "vendi", "prezzo", "minigio", "arcade", "place", "podcast",
    "prediction", "invest", "quota", "mercato",
)


def classify_intent(text: str) -> str:
    """Ritorna l'intento dominante del messaggio."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return "banter"
    if any(marker in lowered for marker in _MINIGAME_MARKERS):
        return "minigame"
    if any(marker in lowered for marker in _HELP_MARKERS):
        return "help"
    if _QUESTION_RE.search(lowered):
        return "question"
    if len(lowered) <= 24 and any(lowered.startswith(g) or f" {g}" in f" {lowered}" for g in _GREETING_MARKERS):
        return "greeting"
    return "banter"


def group_mood(recent_messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Umore recente del gruppo a partire dai sentiment gia salvati."""
    scores: list[float] = []
    for row in recent_messages:
        value = row.get("sentiment")
        if value is None:
            continue
        try:
            scores.append(float(value))
        except (TypeError, ValueError):
            continue
    return mood_summary(scores)


# Quanto Allys tende a essere "utile" invece che "roast" nella pura chiacchiera.
_HELPFUL_BASE = {"soft": 0.82, "medium": 0.58, "chaos": 0.34}


def choose_mode(
    intent: str,
    roast_level: str,
    mood_label: str,
    rng: random.Random | None = None,
) -> str:
    """Sceglie 'helpful' o 'roast' in modo adattivo.

    - domande, richieste d'aiuto e minigiochi -> sempre utile;
    - saluti -> quasi sempre utile/caloroso;
    - chiacchiera -> dipende dal roast_level, ma se il gruppo e teso Allys
      alza la probabilita di essere utile (revamp positivo: non infierisce
      quando l'aria e gia pesante).
    """
    if intent in {"question", "help", "minigame"}:
        return "helpful"
    randomizer = rng or random
    helpful_ratio = _HELPFUL_BASE.get(roast_level, 0.58)
    if intent == "greeting":
        helpful_ratio = min(0.95, helpful_ratio + 0.25)
    if mood_label in {"teso", "un po' giu"}:
        helpful_ratio = min(0.95, helpful_ratio + 0.2)
    elif mood_label == "carico e positivo":
        # Gruppo su di giri: un po' piu di spazio alla battuta.
        helpful_ratio = max(0.2, helpful_ratio - 0.1)
    return "helpful" if randomizer.random() < helpful_ratio else "roast"


def persona_line(roast_level: str, mood_label: str) -> str:
    """Una riga di persona adattata a gruppo e umore."""
    tone = {
        "soft": "calorosa e simpatica, ironica solo con affetto",
        "medium": "sveglia e pungente al punto giusto, ma sempre dalla parte del gruppo",
        "chaos": "irriverente e caustica, battute taglienti ma mai cattiveria vera",
    }.get(roast_level, "sveglia e pungente al punto giusto")
    mood_hint = {
        "carico e positivo": "Il gruppo e su di giri: cavalca l'energia.",
        "sereno": "L'aria e serena: tono leggero.",
        "teso": "L'aria e tesa: smorza, non infierire, semmai sdrammatizza.",
        "un po' giu": "Il gruppo e giu di corda: sii piu calorosa e incoraggiante.",
        "neutro": "",
        "silenzio": "",
    }.get(mood_label, "")
    line = f"La tua personalita e {tone}."
    if mood_hint:
        line += f" {mood_hint}"
    return line


def build_system_prompt(
    mode: str,
    roast_level: str,
    mood_label: str,
    minigame_context: str = "",
    style_block: str = "",
    spontaneous: bool = False,
) -> str:
    """System prompt ricco e coerente."""
    guardrails = (
        "Rispondi SEMPRE in italiano, breve (1-3 frasi), come in una chat tra amici. "
        "Niente markdown, niente asterischi, niente elenchi puntati. "
        "Non fare doxxing, incitamento all'odio, minacce o attacchi personali gravi. "
        "Non citare nomi propri, username o dati personali del gruppo: se ti riferisci a "
        "qualcuno usa solo '@/'. Non inventare fatti sulle persone."
    )
    # Le tre cose che facevano sembrare Allys un pappagallo: rimbalzare la
    # domanda, ripetere le parole di chi scrive, riusare la propria ultima frase.
    substance = (
        "Rispondi NEL MERITO dell'ultimo messaggio: la risposta vera sta nella prima frase. "
        "Non rigirare la domanda a chi te l'ha fatta e non aprire mai con formule tipo "
        "'Vuoi che ti dica'. Non ricopiare le parole del messaggio per riempire la frase. "
        "Se non sai una cosa, ammettilo in poche parole invece di girarci intorno. "
        "Cambia formula ogni volta: se le tue ultime risposte si assomigliano, questa deve "
        "suonare diversa gia' dalle prime parole."
    )
    style = persona_line(roast_level, mood_label)
    if mode == "roast":
        mode_line = (
            "Modalita ROAST: rispondi con una battuta arguta e contestuale, "
            "sfruttando quello che si e detto nella conversazione. Fine, non volgare."
        )
    else:
        mode_line = (
            "Modalita UTILE: rispondi in modo concreto e sveglio, aggiungendo valore. "
            "Un pizzico di ironia va bene, ma prima aiuti davvero."
        )
    prompt = (
        "Sei Allys, l'anima AI di una chat Telegram di gruppo. Conosci il gruppo, "
        "segui il filo del discorso e hai memoria di cosa si e detto.\n"
        f"{style}\n{mode_line}\n{guardrails}\n{substance}"
    )
    if spontaneous:
        prompt += (
            "\nStai parlando di tua iniziativa: nessuno ti ha chiamata. Quindi una frase "
            "sola, che aggiunge qualcosa. Se non hai niente di buono da dire, dillo corto."
        )
    if style_block:
        # Il pezzo che rende Allys *di questo gruppo*: lessico e risposte
        # che qui hanno gia' funzionato davvero.
        prompt += f"\n{style_block}"
    if minigame_context:
        prompt += (
            "\nContesto sui minigiochi del gruppo (usalo solo se pertinente, con tono "
            f"pratico):\n{minigame_context}"
        )
    return prompt


def speaker_aliases(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Mappa ogni interlocutore a un alias anonimo stabile (utente A, B, ...).

    Cosi il modello puo seguire *chi dice cosa* senza mai vedere nomi reali.
    """
    aliases: dict[str, str] = {}
    human_index = 0
    for row in messages:
        key = _speaker_key(row)
        if key in aliases:
            continue
        if key == "allys":
            aliases[key] = BOT_AUTHOR
        else:
            aliases[key] = f"utente {chr(65 + (human_index % 26))}"
            human_index += 1
    return aliases


def _speaker_key(row: dict[str, Any]) -> str:
    username = row.get("username")
    if username is not None and str(username).lower() == BOT_AUTHOR.lower():
        return "allys"
    if username:
        return f"u:{str(username).lower()}"
    return "anon"


def format_transcript(
    messages: list[dict[str, Any]],
    aliases: dict[str, str] | None = None,
    limit: int = HISTORY_TURNS,
) -> str:
    """Trascrizione anonimizzata degli ultimi messaggi, in ordine cronologico."""
    window = messages[-limit:] if limit else messages
    if not window:
        return ""
    resolved = aliases or speaker_aliases(window)
    lines: list[str] = []
    for row in window:
        alias = resolved.get(_speaker_key(row), "utente")
        text = sanitize(row.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{alias}: {text[:280]}")
    return "\n".join(lines)


_MENTION_RE = re.compile(r"@[A-Za-z0-9_]{2,32}")


def sanitize(text: str) -> str:
    """Sostituisce le menzioni @username con @/ (guardrail privacy)."""
    return _MENTION_RE.sub("@/", text or "")


# -- non ripetersi ------------------------------------------------------------


class RepeatedReply(RuntimeError):
    """Il modello ha riscritto per l'ennesima volta la stessa risposta.

    Meglio il silenzio: una battuta gia' sentita tre volte pesa piu' di una
    risposta mancata.
    """



def recent_self_replies(messages: list[dict[str, Any]], limit: int = SELF_REPLY_MEMORY) -> list[str]:
    """Le ultime cose dette da Allys, dalla piu' recente alla piu' vecchia."""
    replies: list[str] = []
    for row in reversed(messages):
        if _speaker_key(row) != "allys":
            continue
        text = sanitize(str(row.get("text") or "")).strip()
        if text:
            replies.append(text)
        if len(replies) >= limit:
            break
    return replies


def format_self_replies_block(replies: list[str]) -> str:
    """Le sue ultime frasi, messe davanti come divieto invece che come esempio."""
    if not replies:
        return ""
    lines = [
        "Cose che hai gia' detto poco fa in questa chat. Non ripeterle e non riusarne "
        "la struttura: questa risposta deve attaccare in modo diverso.",
    ]
    lines += [f'- "{reply[:180]}"' for reply in replies]
    return "\n".join(lines)


_ECHO_STOPWORDS = {"che", "non", "ma", "e", "il", "la", "di", "a", "in", "un", "una", "per", "no", "si"}


def _echo_words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-zà-ù0-9']+", (text or "").lower()) if word]


def _shingles(words: list[str], size: int = 4) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def looks_recycled(reply: str, previous: list[str]) -> bool:
    """La risposta e' l'ennesima variante di quelle appena date?

    Due segnali, entrambi visti nel gruppo: lo stesso attacco ("Vuoi che ti
    dica...") e lo stesso pezzo di frase riciclato in coda ("ma non e' diventato
    dittatore di Liberty Bay, no?").
    """
    words = _echo_words(reply)
    if not words:
        return False
    for old in previous:
        old_words = _echo_words(old)
        if not old_words:
            continue
        if _same_opening(words, old_words):
            return True
        shared = _shingles(words) & _shingles(old_words)
        if not shared:
            continue
        # Una manciata di parole in comune capita; mezza frase no.
        if len(shared) >= 3 or len(shared) >= 0.4 * max(1, len(_shingles(words))):
            return True
    return False


def _same_opening(words: list[str], old_words: list[str], size: int = 3) -> bool:
    """Stesso incipit: e' quello che il gruppo legge come 'ha detto la stessa cosa'."""
    if len(words) < size or len(old_words) < size:
        return False
    opening = words[:size]
    if set(opening) <= _ECHO_STOPWORDS:
        return False
    return opening == old_words[:size]


def response_budget(intent: str) -> int:
    """Quanti token concedere alla risposta in base all'intento."""
    return {
        "help": 220,
        "question": 200,
        "minigame": 200,
        "greeting": 90,
        "banter": 130,
    }.get(intent, 150)
