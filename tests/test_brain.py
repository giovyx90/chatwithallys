import random

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
