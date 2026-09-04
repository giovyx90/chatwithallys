"""La borsa del gruppo disegnata come immagine, non come mini app.

Una Mini App vuole un tap, un caricamento e la voglia di guardarla: in chat
nessuno lo fa. Un PNG invece si vede mentre scorri. Qui dentro ci sono i
"cartelli" che Allys manda in chat:

- ``render_board``: il listino del gruppo, una riga per azienda con sparkline;
- ``render_asset``: la scheda di un titolo, con il grafico del prezzo;
- ``render_portfolio``: le posizioni di una persona con il PnL.

Tutto con Pillow (leggero, gira su CPU senza problemi) e a partire da semplici
dizionari, cosi' il rendering si testa senza database.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

# Disegniamo a 2x e rimpiccioliamo: bordi e linee vengono lisci senza dipendenze.
SUPERSAMPLE = 2
WIDTH = 900

BG = (14, 17, 22)
CARD = (22, 27, 34)
CARD_ALT = (26, 32, 40)
GRID = (38, 45, 55)
TEXT = (230, 237, 243)
MUTED = (125, 135, 146)
UP = (63, 185, 80)
DOWN = (248, 81, 73)
FLAT = (139, 148, 158)
ACCENT = (88, 166, 255)

_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)
_FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

_FONT_CACHE: dict[tuple[int, bool], Any] = {}


def _font_path(bold: bool) -> str | None:
    env = os.environ.get("ALLYS_FONT_BOLD" if bold else "ALLYS_FONT_REGULAR")
    candidates = (env,) + (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR)
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def font(size: int, bold: bool = False):
    key = (size, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path = _font_path(bold)
    try:
        loaded = ImageFont.truetype(path, size * SUPERSAMPLE) if path else ImageFont.load_default()
    except OSError:
        loaded = ImageFont.load_default()
    _FONT_CACHE[key] = loaded
    return loaded


class Canvas:
    """ImageDraw con coordinate in unita' logiche (il 2x lo gestisce lui)."""

    def __init__(self, width: int, height: int, background=BG):
        self.width = width
        self.height = height
        self.image = Image.new("RGB", (width * SUPERSAMPLE, height * SUPERSAMPLE), background)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def _s(self, value: float) -> float:
        return value * SUPERSAMPLE

    def rect(self, box: Sequence[float], fill=None, outline=None, width: int = 1) -> None:
        self.draw.rectangle([self._s(v) for v in box], fill=fill, outline=outline, width=int(self._s(width)))

    def rrect(self, box: Sequence[float], radius: float, fill=None, outline=None, width: int = 1) -> None:
        self.draw.rounded_rectangle(
            [self._s(v) for v in box],
            radius=self._s(radius),
            fill=fill,
            outline=outline,
            width=int(self._s(width)),
        )

    def line(self, points: Iterable[float], fill=TEXT, width: float = 1) -> None:
        self.draw.line([self._s(v) for v in points], fill=fill, width=max(1, int(self._s(width))), joint="curve")

    def polygon(self, points: Iterable[float], fill=None) -> None:
        self.draw.polygon([self._s(v) for v in points], fill=fill)

    def text(self, xy: Sequence[float], value: str, size: int = 14, bold: bool = False,
             fill=TEXT, anchor: str = "la") -> None:
        self.draw.text(
            (self._s(xy[0]), self._s(xy[1])), value, font=font(size, bold), fill=fill, anchor=anchor
        )

    def text_width(self, value: str, size: int = 14, bold: bool = False) -> float:
        return self.draw.textlength(value, font=font(size, bold)) / SUPERSAMPLE

    def to_png(self) -> bytes:
        final = self.image.resize((self.width, self.height), Image.LANCZOS)
        buffer = io.BytesIO()
        final.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_price(value: Any) -> str:
    price = _num(value)
    if price >= 1000:
        return f"{price:,.0f}".replace(",", ".")
    if price >= 100:
        return f"{price:.1f}"
    return f"{price:.2f}"


def fmt_pct(value: Any) -> str:
    pct = _num(value) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def change_color(value: Any) -> tuple[int, int, int]:
    pct = _num(value)
    if pct > 0.001:
        return UP
    if pct < -0.001:
        return DOWN
    return FLAT


def _spark_points(values: Sequence[float], x: float, y: float, w: float, h: float) -> list[float]:
    """Trasforma una serie di prezzi in punti dentro un rettangolo."""
    series = [_num(v) for v in values if v is not None]
    if len(series) < 2:
        series = (series or [1.0]) * 2
    low = min(series)
    high = max(series)
    span = high - low
    if span <= 0:
        span = max(abs(high), 1.0) * 0.02
        low -= span / 2
    step = w / (len(series) - 1)
    points: list[float] = []
    for index, value in enumerate(series):
        px = x + index * step
        py = y + h - ((value - low) / span) * h
        points.extend([px, py])
    return points


def _draw_series(canvas: Canvas, values: Sequence[float], box: Sequence[float],
                 color, line_width: float = 2, fill_alpha: int = 46) -> None:
    x, y, w, h = box
    points = _spark_points(values, x, y, w, h)
    if fill_alpha:
        polygon = points + [x + w, y + h, x, y + h]
        canvas.polygon(polygon, fill=(*color, fill_alpha))
    canvas.line(points, fill=color, width=line_width)


def _header(canvas: Canvas, title: str, subtitle: str) -> None:
    canvas.rect([0, 0, canvas.width, 74], fill=CARD)
    canvas.text([28, 20], title, size=22, bold=True)
    canvas.text([28, 48], subtitle, size=12, fill=MUTED)
    stamp = datetime.now().strftime("%d/%m %H:%M")
    canvas.text([canvas.width - 28, 20], stamp, size=12, fill=MUTED, anchor="ra")


def _footer(canvas: Canvas, y: float, note: str) -> None:
    canvas.text([28, y], note, size=11, fill=MUTED)


def render_board(group_title: str, rows: list[dict[str, Any]], note: str = "") -> bytes:
    """Il listino del gruppo: una riga per azienda, con sparkline e delta."""
    rows = rows[:12]
    row_h = 62
    height = 74 + max(1, len(rows)) * row_h + 54
    canvas = Canvas(WIDTH, height)
    _header(canvas, "BORSA DEL GRUPPO", group_title or "")

    if not rows:
        canvas.text([28, 120], "Nessuna azienda quotata.", size=16, fill=MUTED)
        canvas.text([28, 146], "Gli admin possono usare /azienda_crea SYMBOL Nome", size=12, fill=MUTED)
        return canvas.to_png()

    y = 74
    for index, row in enumerate(rows):
        background = CARD if index % 2 == 0 else CARD_ALT
        canvas.rect([0, y, WIDTH, y + row_h], fill=background)
        color = change_color(row.get("change_pct"))

        # Barretta di stato a sinistra: si vede il verso al volo.
        canvas.rrect([16, y + 14, 20, y + row_h - 14], radius=2, fill=color)

        canvas.text([34, y + 13], str(row.get("symbol") or "?"), size=17, bold=True)
        name = str(row.get("name") or "")
        canvas.text([34, y + 36], name[:34], size=11, fill=MUTED)

        history = row.get("history") or []
        _draw_series(canvas, history, [300, y + 14, 240, row_h - 28], color, line_width=1.6)

        canvas.text([640, y + 20], fmt_price(row.get("price")), size=17, bold=True, anchor="ra")
        canvas.text([648, y + 22], "Crowns", size=9, fill=MUTED)

        pct_text = fmt_pct(row.get("change_pct"))
        badge_w = max(64.0, canvas.text_width(pct_text, 13, True) + 22)
        canvas.rrect([WIDTH - 28 - badge_w, y + 16, WIDTH - 28, y + 44], radius=8, fill=(*color, 38))
        canvas.text([WIDTH - 28 - badge_w / 2, y + 30], pct_text, size=13, bold=True, fill=color, anchor="mm")
        y += row_h

    canvas.rect([0, y, WIDTH, height], fill=BG)
    movers = [r for r in rows if _num(r.get("change_pct")) != 0]
    if not note and movers:
        best = max(movers, key=lambda r: _num(r.get("change_pct")))
        worst = min(movers, key=lambda r: _num(r.get("change_pct")))
        note = (
            f"Meglio: {best.get('symbol')} {fmt_pct(best.get('change_pct'))}   ·   "
            f"Peggio: {worst.get('symbol')} {fmt_pct(worst.get('change_pct'))}"
        )
    _footer(canvas, y + 20, note or "I prezzi si muovono con quello che dite in chat.")
    return canvas.to_png()


def render_asset(asset: dict[str, Any], history: Sequence[float], stats: dict[str, Any] | None = None) -> bytes:
    """La scheda di un titolo: prezzo grande, grafico, segnali che lo muovono."""
    stats = stats or {}
    height = 420
    canvas = Canvas(WIDTH, height)
    symbol = str(asset.get("symbol") or "?")
    _header(canvas, symbol, str(asset.get("name") or ""))

    color = change_color(asset.get("change_pct"))
    price_text = fmt_price(asset.get("price"))
    canvas.text([28, 92], price_text, size=40, bold=True)
    price_w = canvas.text_width(price_text, 40, True)
    canvas.text([28 + price_w + 10, 122], "Crowns", size=12, fill=MUTED)
    canvas.text([28, 148], fmt_pct(asset.get("change_pct")) + "  nelle ultime 24h", size=14, bold=True, fill=color)

    chart_x, chart_y, chart_w, chart_h = 28, 186, WIDTH - 56, 150
    canvas.rrect([chart_x, chart_y, chart_x + chart_w, chart_y + chart_h], radius=10, fill=CARD)
    series = [_num(v) for v in history if v is not None]
    if len(series) >= 2:
        for step in range(1, 4):
            grid_y = chart_y + (chart_h / 4) * step
            canvas.line([chart_x + 10, grid_y, chart_x + chart_w - 10, grid_y], fill=GRID, width=1)
        _draw_series(
            canvas, series,
            [chart_x + 12, chart_y + 22, chart_w - 24, chart_h - 40],
            color, line_width=2.4,
        )
        canvas.text([chart_x + 12, chart_y + 6], f"max {fmt_price(max(series))}", size=10, fill=MUTED)
        canvas.text([chart_x + 12, chart_y + chart_h - 18], f"min {fmt_price(min(series))}", size=10, fill=MUTED)
    else:
        canvas.text([chart_x + 20, chart_y + 64], "Storico ancora troppo corto.", size=13, fill=MUTED)

    tiles = [
        ("Menzioni", str(stats.get("mentions", "-"))),
        ("Persone", str(stats.get("unique_users", "-"))),
        ("Volume", fmt_price(stats.get("volume", 0))),
        ("Rischio spam", f"{_num(stats.get('manipulation_risk')) * 100:.0f}%"),
    ]
    tile_w = (WIDTH - 56 - 3 * 12) / 4
    for index, (label, value) in enumerate(tiles):
        x = 28 + index * (tile_w + 12)
        canvas.rrect([x, 354, x + tile_w, 404], radius=10, fill=CARD)
        canvas.text([x + 14, 361], label, size=10, fill=MUTED)
        canvas.text([x + 14, 377], value, size=16, bold=True)
    return canvas.to_png()


def render_portfolio(display_name: str, crowns: Any, holdings: list[dict[str, Any]]) -> bytes:
    """Le posizioni di una persona, con il PnL messo in chiaro."""
    holdings = holdings[:10]
    row_h = 56
    height = 74 + 84 + max(1, len(holdings)) * row_h + 36
    canvas = Canvas(WIDTH, height)
    _header(canvas, "PORTAFOGLIO", display_name or "")

    invested = sum(_num(h.get("quantity")) * _num(h.get("price")) for h in holdings)
    pnl = sum(_num(h.get("pnl")) for h in holdings)
    summary = [
        ("Liquidita", f"{fmt_price(crowns)} Crowns", TEXT),
        ("Posizioni", f"{fmt_price(invested)} Crowns", TEXT),
        ("PnL", f"{'+' if pnl >= 0 else ''}{fmt_price(pnl)}", UP if pnl >= 0 else DOWN),
    ]
    tile_w = (WIDTH - 56 - 2 * 12) / 3
    for index, (label, value, color) in enumerate(summary):
        x = 28 + index * (tile_w + 12)
        canvas.rrect([x, 90, x + tile_w, 146], radius=10, fill=CARD)
        canvas.text([x + 16, 99], label, size=10, fill=MUTED)
        canvas.text([x + 16, 115], value, size=18, bold=True, fill=color)

    y = 164
    if not holdings:
        canvas.text([28, y + 10], "Nessuna posizione aperta.", size=15, fill=MUTED)
        canvas.text([28, y + 34], "Compra con /compra SYMBOL quantita", size=12, fill=MUTED)
        return canvas.to_png()

    for index, holding in enumerate(holdings):
        background = CARD if index % 2 == 0 else CARD_ALT
        canvas.rrect([28, y, WIDTH - 28, y + row_h - 8], radius=8, fill=background)
        holding_pnl = _num(holding.get("pnl"))
        color = UP if holding_pnl > 0 else DOWN if holding_pnl < 0 else FLAT
        canvas.text([44, y + 9], str(holding.get("symbol") or "?"), size=16, bold=True)
        canvas.text([44, y + 29], f"{fmt_price(holding.get('quantity'))} @ {fmt_price(holding.get('avg_price'))}",
                    size=11, fill=MUTED)
        canvas.text([WIDTH - 200, y + 18], fmt_price(holding.get("price")), size=15, anchor="ra")
        canvas.text([WIDTH - 200, y + 5], "prezzo", size=9, fill=MUTED, anchor="ra")
        pnl_text = f"{'+' if holding_pnl >= 0 else ''}{fmt_price(holding_pnl)}"
        canvas.text([WIDTH - 48, y + 17], pnl_text, size=16, bold=True, fill=color, anchor="ra")
        y += row_h
    return canvas.to_png()
