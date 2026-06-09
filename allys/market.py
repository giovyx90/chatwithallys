from redis.asyncio import Redis
from decimal import Decimal, InvalidOperation
import json

from allys.db import Database
from allys.sentiment import score_text


class MarketService:
    def __init__(self, db: Database, redis: Redis):
        self.db = db
        self.redis = redis

    async def process_message(self, chat_id: int, text: str) -> float:
        sentiment = score_text(text)
        touched = self.db.record_stock_mentions(chat_id, None, text, sentiment)
        if touched:
            await self.redis.publish(f"market:{chat_id}", json.dumps({"type": "stock_signal", "symbols": touched}))
        return sentiment

    async def process_user_message(self, chat_id: int, user_id: int | None, text: str) -> float:
        sentiment = score_text(text)
        touched = self.db.record_stock_mentions(chat_id, user_id, text, sentiment)
        if touched:
            await self.redis.publish(f"market:{chat_id}", json.dumps({"type": "stock_signal", "symbols": touched}))
        return sentiment

    async def aggregate_group(self, chat_id: int) -> list[dict]:
        updates = self.db.aggregate_stock_window(chat_id)
        if updates:
            await self.redis.publish(
                f"market:{chat_id}",
                json.dumps({"type": "stock_ticks", "assets": [{"symbol": item["symbol"], "price": item["price"]} for item in updates]}),
            )
        return updates

    def propose_candidates(self, chat_id: int) -> list[dict]:
        return self.db.propose_stock_candidates(chat_id)

    def portfolio_text(self, chat_id: int, user_id: int) -> str:
        portfolio = self.db.user_portfolio(chat_id, user_id)
        lines = [f"Crowns disponibili: {portfolio['crowns']}"]
        if not portfolio["holdings"]:
            lines.append("Nessuna posizione aperta.")
        for item in portfolio["holdings"]:
            lines.append(f"{item['symbol']}: {item['quantity']} @ avg {item['avg_price']} | prezzo {item['price']} | PnL {item['pnl']}")
        return "\n".join(lines)

    def trade_text(self, chat_id: int, user_id: int, side: str, symbol: str, raw_quantity: str) -> str:
        try:
            quantity = Decimal(raw_quantity.replace(",", "."))
        except (InvalidOperation, AttributeError):
            return "Quantita non valida. Esempio: /compra MEME 2"
        try:
            trade = self.db.trade_asset(chat_id, user_id, symbol, side, quantity)
        except ValueError as exc:
            return str(exc)
        verb = "Comprato" if side == "buy" else "Venduto"
        return f"{verb} {trade['quantity']} {trade['symbol']} a {trade['price']} Crowns. Totale: {trade['total']} + fee {trade['fee']}."

    def quote_text(self, chat_id: int, user_id: int, side: str, symbol: str, raw_quantity: str) -> str:
        try:
            quantity = Decimal(raw_quantity.replace(",", "."))
            quote = self.db.trade_quote(chat_id, user_id, symbol, side, quantity)
        except (InvalidOperation, AttributeError, ValueError) as exc:
            return str(exc) if isinstance(exc, ValueError) else "Quantita non valida."
        verb = "comprare" if side == "buy" else "vendere"
        return f"Per {verb} {quote['quantity']} {quote['symbol']}: prezzo {quote['price']}, totale {quote['total']}, fee {quote['fee']}."
