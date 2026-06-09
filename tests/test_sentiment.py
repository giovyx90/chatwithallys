from allys.sentiment import mentioned_symbols, score_text


def test_score_text() -> None:
    assert score_text("grande top win") > 0
    assert score_text("cringe fail schifo") < 0


def test_mentioned_symbols() -> None:
    assert mentioned_symbols("DRAMA vola e $MEME pure", ["DRAMA", "MEME", "COPE"]) == ["DRAMA", "MEME"]
