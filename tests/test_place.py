import pytest

from allys.place import CANVAS_SIZE, COOLDOWN_SECONDS, MINECRAFT_COLORS, PlaceError, PlaceService


def test_minecraft_palette_has_16_canonical_colors():
    assert len(MINECRAFT_COLORS) == 16
    assert [color["id"] for color in MINECRAFT_COLORS] == list(range(16))
    assert MINECRAFT_COLORS[0] == {"id": 0, "name": "white", "hex": "#F9FFFE"}
    assert MINECRAFT_COLORS[-1] == {"id": 15, "name": "black", "hex": "#1D1D21"}


def test_place_constants_match_contract():
    assert CANVAS_SIZE == 1_000_000
    assert COOLDOWN_SECONDS == 30


def test_pixel_validation_rejects_out_of_range_values():
    with pytest.raises(PlaceError) as coordinate:
        PlaceService._validate_pixel(1000, 0, 1)
    assert coordinate.value.code == "invalid_coordinate"

    with pytest.raises(PlaceError) as color:
        PlaceService._validate_pixel(4, 4, 16)
    assert color.value.code == "invalid_color"


def test_token_hash_is_stable_and_non_plaintext():
    token = "session-token"
    hashed = PlaceService._hash_token(token)
    assert hashed == PlaceService._hash_token(token)
    assert hashed != token
    assert len(hashed) == 64
