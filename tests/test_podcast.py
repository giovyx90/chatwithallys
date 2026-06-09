from allys.podcast import parse_podcast_config


def test_parse_podcast_off() -> None:
    assert parse_podcast_config("off") == (False, "weekly", None, "21:00")


def test_parse_daily() -> None:
    assert parse_podcast_config("daily 21:00") == (True, "daily", None, "21:00")


def test_parse_weekly() -> None:
    assert parse_podcast_config("weekly friday 21:00") == (True, "weekly", "friday", "21:00")


def test_parse_invalid() -> None:
    assert isinstance(parse_podcast_config("weekly nope 99:99"), str)
