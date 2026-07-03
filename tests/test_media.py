import random

from allys.media import (
    media_type_for_url,
    parse_giphy,
    parse_reddit_memes,
    parse_tenor,
    pick_subreddit,
)


def test_media_type_for_url() -> None:
    assert media_type_for_url("https://x/y.gif") == "gif"
    assert media_type_for_url("https://x/y.mp4?sig=1") == "video"
    assert media_type_for_url("https://x/y.jpg") == "photo"
    assert media_type_for_url("https://x/y.png") == "photo"


def test_pick_subreddit_maps_topics() -> None:
    assert pick_subreddit("guarda che bug nel codice") == "ProgrammerHumor"
    assert pick_subreddit("nuovo server minecraft") == "minecraftmemes"
    # senza match -> uno dei default, deterministico col seed
    assert pick_subreddit("boh a caso", random.Random(0)) in {"memes", "dankmemes", "meme"}


def test_parse_giphy() -> None:
    payload = {
        "data": [
            {"images": {"downsized_medium": {"url": "https://media.giphy.com/a.gif"}}},
            {"images": {"original": {"url": "https://media.giphy.com/b.gif"}}},
            {"images": {}},
        ]
    }
    assert parse_giphy(payload) == ["https://media.giphy.com/a.gif", "https://media.giphy.com/b.gif"]


def test_parse_tenor() -> None:
    payload = {"results": [{"media_formats": {"gif": {"url": "https://media.tenor.com/x.gif"}}}]}
    assert parse_tenor(payload) == ["https://media.tenor.com/x.gif"]


def test_parse_reddit_skips_nsfw_and_spoiler() -> None:
    payload = {
        "memes": [
            {"url": "https://i.redd.it/ok.jpg", "nsfw": False, "spoiler": False},
            {"url": "https://i.redd.it/bad.jpg", "nsfw": True, "spoiler": False},
            {"url": "https://i.redd.it/spoil.jpg", "nsfw": False, "spoiler": True},
        ]
    }
    assert parse_reddit_memes(payload) == ["https://i.redd.it/ok.jpg"]


def test_parse_reddit_single_object() -> None:
    assert parse_reddit_memes({"url": "https://i.redd.it/x.png", "nsfw": False}) == ["https://i.redd.it/x.png"]
