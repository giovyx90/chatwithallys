import io

from PIL import Image

from allys.charts import fmt_pct, fmt_price, render_asset, render_board, render_portfolio

ROWS = [
    {"symbol": "DRAMA", "name": "Drama Holdings", "price": 14.2, "change_pct": 0.18,
     "history": [10, 11, 10.4, 12, 13.8, 14.2]},
    {"symbol": "LAG", "name": "Lag Industries", "price": 3.1, "change_pct": -0.09,
     "history": [4, 3.8, 3.4, 3.1]},
]


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_board_e_un_png_valido() -> None:
    image = _open(render_board("Gruppo di prova", ROWS))
    assert image.format == "PNG"
    assert image.width == 900
    assert image.height > 200


def test_board_regge_il_gruppo_senza_aziende() -> None:
    image = _open(render_board("Gruppo vuoto", []))
    assert image.format == "PNG"


def test_board_regge_una_serie_piatta_o_assente() -> None:
    piatto = [{"symbol": "FLAT", "name": "Flat", "price": 1, "change_pct": 0, "history": [1, 1, 1]}]
    assert _open(render_board("x", piatto)).format == "PNG"
    vuoto = [{"symbol": "NEW", "name": "Nuova", "price": 1, "change_pct": 0, "history": []}]
    assert _open(render_board("x", vuoto)).format == "PNG"


def test_asset_card() -> None:
    image = _open(render_asset(
        {"symbol": "DRAMA", "name": "Drama Holdings", "price": 14.2, "change_pct": 0.18},
        [10, 11, 12, 14.2],
        {"mentions": 12, "unique_users": 4, "volume": 90, "manipulation_risk": 0.2},
    ))
    assert image.format == "PNG"


def test_asset_card_senza_storico() -> None:
    assert _open(render_asset({"symbol": "X", "name": "X", "price": 1, "change_pct": 0}, [])).format == "PNG"


def test_portfolio_pieno_e_vuoto() -> None:
    pieno = render_portfolio("Giovanni", 240, [
        {"symbol": "DRAMA", "quantity": 12, "avg_price": 9.1, "price": 14.2, "pnl": 61.2},
    ])
    assert _open(pieno).format == "PNG"
    assert _open(render_portfolio("Nessuno", 0, [])).format == "PNG"


def test_formattazione_numeri() -> None:
    assert fmt_pct(0.182) == "+18.2%"
    assert fmt_pct(-0.05) == "-5.0%"
    assert fmt_price(1.5) == "1.50"
    assert fmt_price("non un numero") == "0.00"
