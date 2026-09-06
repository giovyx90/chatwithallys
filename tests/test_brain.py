import random

from allys import brain
from allys.brain import (
    build_system_prompt,
    choose_mode,
    classify_intent,
    format_transcript,
    group_mood,
    response_budget,
    speaker_aliases,
)


def test_classify_intent() -> None:
    assert classify_intent("come si compra un'azione?") in {"question", "help", "minigame"}
    assert classify_intent("mi aiuti a capire?") in {"help", "question"}
    assert classify_intent("ciao allys") == "greeting"
    assert classify_intent("che figata questo gruppo") == "banter"
    assert classify_intent("quanto vale DRAMA in borsa") == "minigame"


def test_choose_mode_forces_helpful_on_questions() -> None:
    rng = random.Random(1)
    assert choose_mode("question", "chaos", "neutro", rng) == "helpful"
    assert choose_mode("help", "chaos", "carico e positivo", rng) == "helpful"
    assert choose_mode("minigame", "chaos", "teso", rng) == "helpful"


def test_choose_mode_soft_leans_helpful() -> None:
    helpful = sum(
        choose_mode("banter", "soft", "neutro", random.Random(seed)) == "helpful"
        for seed in range(200)
    )
    chaotic = sum(
        choose_mode("banter", "chaos", "neutro", random.Random(seed)) == "helpful"
        for seed in range(200)
    )
    assert helpful > chaotic


def test_tense_mood_softens() -> None:
    calm = sum(
        choose_mode("banter", "medium", "neutro", random.Random(seed)) == "helpful"
        for seed in range(200)
    )
    tense = sum(
        choose_mode("banter", "medium", "teso", random.Random(seed)) == "helpful"
        for seed in range(200)
    )
    assert tense >= calm


def test_group_mood_reads_sentiment() -> None:
    rows = [{"sentiment": 1.2}, {"sentiment": 0.8}, {"sentiment": 1.0}]
    assert group_mood(rows)["label"] in {"carico e positivo", "sereno"}
    assert group_mood([])["label"] == "silenzio"


def test_transcript_is_anonymized() -> None:
    messages = [
        {"username": "mario", "text": "ciao a tutti @luigi"},
        {"username": "anna", "text": "che si dice?"},
        {"username": "mario", "text": "niente di che"},
    ]
    aliases = speaker_aliases(messages)
    transcript = format_transcript(messages, aliases)
    assert "mario" not in transcript
    assert "@luigi" not in transcript
    assert "@/" in transcript
    # Stesso interlocutore -> stesso alias
    assert transcript.count("utente A") == 2


def test_system_prompt_mentions_mode_and_guardrails() -> None:
    prompt = build_system_prompt("roast", "chaos", "carico e positivo")
    assert "Allys" in prompt
    assert "@/" in prompt
    helpful = build_system_prompt("helpful", "soft", "teso", "Borsa: dati...")
    assert "Borsa" in helpful


def test_response_budget() -> None:
    assert response_budget("help") >= response_budget("greeting")


def test_bot_messages_labeled_as_allys() -> None:
    messages = [
        {"username": "mario", "text": "ciao allys"},
        {"username": "Allys", "text": "ehila, come va?"},
        {"username": "mario", "text": "tutto bene"},
    ]
    aliases = speaker_aliases(messages)
    transcript = format_transcript(messages, aliases)
    assert "Allys: ehila" in transcript
    # Mario resta un utente anonimo e stabile
    assert transcript.count("utente A") == 2
    assert "Allys" not in aliases.get("u:mario", "")


# --- non ripetersi -----------------------------------------------------------
# Nel gruppo era finita cosi': "Vuoi che ti dica X? Bene, ma non e' diventato
# dittatore di Liberty Bay, no?" per tre messaggi diversi di fila. Le sue
# risposte stanno nella trascrizione, e il modello le prendeva per lo stile giusto.


def test_riconosce_la_risposta_riciclata() -> None:
    prima = [
        "Vuoi che ti dica a che gioco vuoi giocare? Bene, ma non e' diventato dittatore di Atlantis, no?"
    ]
    assert brain.looks_recycled(
        "Vuoi che ti dica che ne penso? Bene, ma non e' diventato dittatore di Liberty Bay, no?", prima
    )
    assert brain.looks_recycled("Ah, ma non e' diventato dittatore di Liberty Bay, no?", prima)


def test_una_risposta_nuova_passa() -> None:
    prima = [
        "Vuoi che ti dica a che gioco vuoi giocare? Bene, ma non e' diventato dittatore di Atlantis, no?"
    ]
    assert not brain.looks_recycled(
        "Mbappe dittatore lo vedo solo se il regime prevede rigori a favore.", prima
    )
    assert not brain.looks_recycled("Dodici.", prima)
    assert not brain.looks_recycled("qualsiasi cosa", [])


def test_le_sue_ultime_frasi_tornano_come_divieto() -> None:
    history = [
        {"username": "mario", "text": "allys ci sei?"},
        {"username": brain.BOT_AUTHOR, "text": "Sempre sul pezzo."},
        {"username": "luca", "text": "e allora?"},
        {"username": brain.BOT_AUTHOR, "text": "Sempre sul pezzo, no?"},
    ]
    said = brain.recent_self_replies(history)
    assert said == ["Sempre sul pezzo, no?", "Sempre sul pezzo."]  # dalla piu' recente

    block = brain.format_self_replies_block(said)
    assert "Non ripeterle" in block and "Sempre sul pezzo" in block
    assert brain.format_self_replies_block([]) == ""


def test_il_prompt_le_chiede_di_rispondere_nel_merito() -> None:
    prompt = brain.build_system_prompt("helpful", "medium", "sereno")
    assert "MERITO" in prompt
    assert "Vuoi che ti dica" in prompt  # la formula da non usare, per nome


def test_riconosce_la_domanda_rimbalzata() -> None:
    assert brain.bounces_the_question("Vuoi che ti dica che ne penso? Bene, ma...")
    assert brain.bounces_the_question("Vuoi sapere se ho mai governato un paese? No.")
    assert brain.bounces_the_question("Ah, ti stai chiedendo se ci sono stata?")
    assert not brain.bounces_the_question("No, ma ci sono andata vicino: solo come avatar.")
    assert not brain.bounces_the_question("Vuoi una mano con il gioco? Dimmi pure.")
