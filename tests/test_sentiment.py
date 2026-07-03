from allys.sentiment import analyze, mentioned_symbols, mood_summary, score_text


def test_score_text() -> None:
    assert score_text("grande top win") > 0
    assert score_text("cringe fail schifo") < 0


def test_mentioned_symbols() -> None:
    assert mentioned_symbols("DRAMA vola e $MEME pure", ["DRAMA", "MEME", "COPE"]) == ["DRAMA", "MEME"]


def test_negation_flips_sign() -> None:
    assert score_text("non e bello per niente") < 0
    # "non male" resta non negativo (lieve positivo o neutro)
    assert score_text("dai non e male") >= 0


def test_intensifier_amplifies() -> None:
    assert score_text("molto bello") > score_text("bello")
    assert abs(score_text("troppo schifo")) > abs(score_text("schifo")) - 0.001


def test_emoji_and_laughter() -> None:
    assert score_text("ahahah") > 0
    assert score_text("che disastro 😭") < 0
    assert score_text("grande 🔥🚀") > score_text("grande")


def test_phrases_detected() -> None:
    assert score_text("skill issue totale") < 0
    assert score_text("sei un mito") > 0


def test_labels_and_bounds() -> None:
    strong = analyze("CAPOLAVORO ASSOLUTO 🔥🔥🔥")
    assert strong.label == "positivo"
    assert -3.0 <= strong.score <= 3.0
    assert analyze("il tavolo e marrone").label == "neutro"


def test_mood_summary() -> None:
    assert mood_summary([]).get("label") == "silenzio"
    assert mood_summary([1.5, 1.0, 0.8])["label"] in {"carico e positivo", "sereno"}
    assert mood_summary([-1.5, -1.0])["label"] == "teso"
