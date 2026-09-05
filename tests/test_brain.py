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


def test_transcript_uses_real_names() -> None:
    """Tra amici il nome serve: senza, Allys non puo' prendere in giro nessuno."""
    messages = [
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "ciao a tutti @luigi"},
        {"user_id": 2, "username": "anna", "display_name": "Anna", "text": "che si dice?"},
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "niente di che"},
    ]
    transcript = format_transcript(messages, speaker_aliases(messages))
    assert transcript.count("Mario:") == 2
    assert "Anna:" in transcript
    assert "@luigi" in transcript
    assert "utente A" not in transcript


def test_transcript_ripiega_sullo_username_senza_nome() -> None:
    messages = [{"user_id": 7, "username": "giovyx90", "text": "presente"}]
    assert "giovyx90:" in format_transcript(messages, speaker_aliases(messages))


def test_omonimi_restano_persone_diverse() -> None:
    """Due amici con lo stesso nome: l'id Telegram e' l'unica chiave sicura."""
    messages = [
        {"user_id": 1, "display_name": "Luca", "text": "io dico di si"},
        {"user_id": 2, "display_name": "Luca", "text": "io dico di no"},
    ]
    assert len(speaker_aliases(messages)) == 2


def test_anonimato_ancora_disponibile() -> None:
    messages = [
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "ciao @luigi"},
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "ci sei?"},
    ]
    transcript = format_transcript(messages, limit=10, anonymize=True)
    assert "Mario" not in transcript
    assert "@luigi" not in transcript
    assert transcript.count("utente A") == 2


def test_system_prompt_mentions_mode_and_guardrails() -> None:
    prompt = build_system_prompt("roast", "chaos", "carico e positivo")
    assert "Allys" in prompt
    assert "per nome" in prompt
    assert "@/" not in prompt
    assert "@/" in build_system_prompt("roast", "chaos", "sereno", anonymize=True)
    helpful = build_system_prompt("helpful", "soft", "teso", "Borsa: dati...")
    assert "Borsa" in helpful


def test_response_budget() -> None:
    assert response_budget("help") >= response_budget("greeting")


def test_bot_messages_labeled_as_allys() -> None:
    messages = [
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "ciao allys"},
        {"username": "Allys", "text": "ehila, come va?"},
        {"user_id": 1, "username": "mario99", "display_name": "Mario", "text": "tutto bene"},
    ]
    aliases = speaker_aliases(messages)
    transcript = format_transcript(messages, aliases)
    assert "Allys: ehila" in transcript
    assert transcript.count("Mario:") == 2
    assert aliases["allys"] == "Allys"
