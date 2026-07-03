from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta
from random import uniform
from typing import Any
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from allys.config import Settings


def re_words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-zA-Z0-9_àèéìòù]{3,}", (text or "").lower()) if word not in {"allys", "http", "https", "www"}]


def normalize_symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())[:8]


DEFAULT_SUPPLY_CAP = Decimal("100")


SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
  chat_id BIGINT PRIMARY KEY,
  title TEXT,
  bot_enabled BOOLEAN NOT NULL DEFAULT true,
  paused_until TIMESTAMPTZ,
  roast_level TEXT NOT NULL DEFAULT 'medium',
  spontaneous_chance NUMERIC NOT NULL DEFAULT 0,
  helpful_ratio NUMERIC NOT NULL DEFAULT 0.30,
  podcast_enabled BOOLEAN NOT NULL DEFAULT false,
  podcast_frequency TEXT NOT NULL DEFAULT 'weekly',
  podcast_day TEXT,
  podcast_time TEXT NOT NULL DEFAULT '21:00',
  last_podcast_at TIMESTAMPTZ,
  meme_mode TEXT NOT NULL DEFAULT 'medium',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS group_users (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  username TEXT,
  display_name TEXT,
  points BIGINT NOT NULL DEFAULT 0,
  message_count BIGINT NOT NULL DEFAULT 0,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS users_balance (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  username TEXT,
  display_name TEXT,
  credits_balance NUMERIC NOT NULL DEFAULT 0,
  last_daily_claim_at TIMESTAMPTZ,
  last_work_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS credit_ledger (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  amount NUMERIC NOT NULL,
  reason TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS credit_farming_daily (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  day DATE NOT NULL,
  earned NUMERIC NOT NULL DEFAULT 0,
  message_count BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT,
  username TEXT,
  text TEXT NOT NULL,
  sentiment NUMERIC NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS member_profiles (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  profile TEXT NOT NULL DEFAULT '',
  messages_at_update BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS chat_media (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  media_type TEXT NOT NULL,
  file_id TEXT NOT NULL,
  file_unique_id TEXT,
  caption TEXT,
  tags TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
  username TEXT,
  uses BIGINT NOT NULL DEFAULT 0,
  disabled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (chat_id, file_unique_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_media_chat_enabled ON chat_media (chat_id, disabled_at, created_at DESC);

CREATE TABLE IF NOT EXISTS assets (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  theme TEXT,
  aliases TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
  status TEXT NOT NULL DEFAULT 'listed',
  supply_cap NUMERIC NOT NULL DEFAULT 100,
  outstanding_shares NUMERIC NOT NULL DEFAULT 50,
  price NUMERIC NOT NULL DEFAULT 1,
  volume NUMERIC NOT NULL DEFAULT 0,
  volatility_score NUMERIC NOT NULL DEFAULT 1,
  manipulation_risk NUMERIC NOT NULL DEFAULT 0,
  last_signal_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (chat_id, symbol)
);

CREATE TABLE IF NOT EXISTS price_ticks (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  price NUMERIC NOT NULL,
  pct_change NUMERIC NOT NULL DEFAULT 0,
  volume NUMERIC NOT NULL DEFAULT 0,
  unique_users INTEGER NOT NULL DEFAULT 0,
  mentions INTEGER NOT NULL DEFAULT 0,
  sentiment NUMERIC NOT NULL DEFAULT 0,
  manipulation_risk NUMERIC NOT NULL DEFAULT 0,
  reason TEXT,
  signals JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock_mentions (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  user_id BIGINT,
  text_hash TEXT NOT NULL,
  sentiment NUMERIC NOT NULL DEFAULT 0,
  weight NUMERIC NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stock_mentions_window ON stock_mentions (chat_id, symbol, created_at);

CREATE TABLE IF NOT EXISTS stock_trade_cooldowns (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  last_trade_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id, symbol)
);

CREATE TABLE IF NOT EXISTS holdings (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  quantity NUMERIC NOT NULL DEFAULT 0,
  avg_price NUMERIC NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, user_id, symbol)
);

CREATE TABLE IF NOT EXISTS trades (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity NUMERIC NOT NULL,
  price NUMERIC NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prediction_markets (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT,
  scope TEXT NOT NULL DEFAULT 'local',
  question TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  yes_pool NUMERIC NOT NULL DEFAULT 100,
  no_pool NUMERIC NOT NULL DEFAULT 100,
  created_by BIGINT,
  closes_at TIMESTAMPTZ,
  resolved_outcome TEXT,
  resolved_by BIGINT,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prediction_markets_chat_status ON prediction_markets (chat_id, status);

CREATE TABLE IF NOT EXISTS prediction_positions (
  market_id BIGINT NOT NULL REFERENCES prediction_markets(id) ON DELETE CASCADE,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  outcome TEXT NOT NULL,
  shares NUMERIC NOT NULL DEFAULT 0,
  avg_price NUMERIC NOT NULL DEFAULT 0,
  PRIMARY KEY (market_id, user_id, outcome)
);

CREATE TABLE IF NOT EXISTS prediction_trades (
  id BIGSERIAL PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES prediction_markets(id) ON DELETE CASCADE,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  side TEXT NOT NULL,
  outcome TEXT NOT NULL,
  credits NUMERIC NOT NULL,
  fee NUMERIC NOT NULL DEFAULT 0,
  shares NUMERIC NOT NULL DEFAULT 0,
  price NUMERIC NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS podcasts (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  script TEXT NOT NULL,
  audio_path TEXT,
  status TEXT NOT NULL DEFAULT 'created',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS place_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  username TEXT,
  source_chat_id BIGINT,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS place_events (
  seq BIGSERIAL PRIMARY KEY,
  x INTEGER NOT NULL CHECK (x >= 0 AND x < 1000),
  y INTEGER NOT NULL CHECK (y >= 0 AND y < 1000),
  color_id SMALLINT NOT NULL CHECK (color_id >= 0 AND color_id < 16),
  user_id BIGINT NOT NULL,
  username TEXT,
  source_chat_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_place_events_created_at ON place_events (created_at);

CREATE TABLE IF NOT EXISTS place_snapshots (
  id BIGSERIAL PRIMARY KEY,
  data BYTEA NOT NULL,
  last_seq BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

MIGRATIONS = [
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS bot_enabled BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS paused_until TIMESTAMPTZ",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS meme_mode TEXT NOT NULL DEFAULT 'medium'",
    "ALTER TABLE groups ALTER COLUMN spontaneous_chance SET DEFAULT 0",
    "UPDATE groups SET spontaneous_chance = 0 WHERE spontaneous_chance <> 0",
    "ALTER TABLE users_balance ADD COLUMN IF NOT EXISTS last_work_at TIMESTAMPTZ",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS theme TEXT",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT ARRAY[]::text[]",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'listed'",
    "ALTER TABLE assets ALTER COLUMN price SET DEFAULT 1",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS volume NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS volatility_score NUMERIC NOT NULL DEFAULT 1",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS manipulation_risk NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS last_signal_at TIMESTAMPTZ",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS supply_cap NUMERIC NOT NULL DEFAULT 100",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS outstanding_shares NUMERIC NOT NULL DEFAULT 50",
    "UPDATE assets SET supply_cap = GREATEST(COALESCE(supply_cap, 100), 1), outstanding_shares = LEAST(GREATEST(COALESCE(outstanding_shares, COALESCE(supply_cap, 100) / 2), 0), COALESCE(supply_cap, 100))",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS pct_change NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS volume NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS unique_users INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS mentions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS sentiment NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS manipulation_risk NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE price_ticks ADD COLUMN IF NOT EXISTS signals JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fee NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS total NUMERIC NOT NULL DEFAULT 0",
]


DEFAULT_ASSETS = [
    ("DRAMA", "Drama S.p.A.", "Discussioni accese, lore di gruppo e colpi di scena.", ["drama", "litigio", "casino"]),
    ("COPE", "Copium Labs", "Speranze, scuse, recuperi impossibili e ottimismo creativo.", ["cope", "copium", "scusa"]),
    ("MEME", "Meme Holding", "Meme, reaction e tormentoni ricorrenti.", ["meme", "gif", "sticker"]),
    ("LURK", "Lurker Capital", "Presenze silenziose, gente che legge e non scrive.", ["lurk", "lurker", "visualizzato"]),
    ("ROAST", "Roast Industries", "Battute, frecciatine e roast leggeri.", ["roast", "insulto", "skill issue"]),
]

STOPWORDS = {
    "che", "con", "per", "una", "uno", "del", "della", "sono", "non", "hai", "del", "nel", "all", "gli",
    "come", "quando", "dove", "allys", "questo", "quello", "https", "http", "www", "anche", "solo", "tipo",
    "alla", "dalla", "dello", "delle", "degli", "nella", "nelle", "quel", "quella", "quelli", "questa",
    "queste", "questi", "ciao", "bene", "male", "raga", "ragazzi", "bro", "comunque", "quindi", "perche",
    "perchè", "pero", "però", "oggi", "ieri", "domani", "sempre", "ancora", "tutto", "tutti", "tutte",
    "fare", "fatto", "fai", "faccio", "dice", "detto", "messaggio", "gruppo", "chat", "bot", "and",
    "the", "for", "dei", "sul", "sei", "sta", "sto", "siamo", "siete", "loro", "mio", "mia", "tuo",
    "tua", "suo", "sua", "ahah", "haha", "ahahah", "foto", "video", "sticker", "quanto", "allora",
    "senza", "verso", "molto", "poco", "ogni", "devo", "devi", "deve", "puoi", "posso", "cosi",
    "così", "cioe", "cioè", "vabbè", "vabbe", "andare", "arrivano", "tanto", "thinking",
}


class Database:
    def __init__(self, settings: Settings):
        self.database_url = settings.database_url

    @staticmethod
    def _dec(value: Any, default: str = "0") -> Decimal:
        if value is None:
            return Decimal(default)
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def _supply_ratio(self, row: dict[str, Any]) -> Decimal:
        supply_cap = self._dec(row.get("supply_cap"), "100")
        outstanding = self._dec(row.get("outstanding_shares"), "0")
        if supply_cap <= 0:
            return Decimal("1")
        ratio = outstanding / supply_cap
        if ratio < Decimal("0"):
            return Decimal("0")
        return min(ratio, Decimal("1"))

    def _supply_multiplier(self, row: dict[str, Any]) -> Decimal:
        ratio = self._supply_ratio(row)
        # Regola: se la maggior parte delle azioni è già in circolazione, il titolo è più economico.
        # ratio = 0 -> prezzo max, ratio = 1 -> prezzo min.
        return (Decimal("0.5") + (Decimal("1") - ratio)).quantize(Decimal("0.0001"))

    def _current_price(self, row: dict[str, Any], price_override: Any | None = None) -> Decimal:
        base = self._dec(price_override if price_override is not None else row.get("price"), "1")
        multiplier = self._supply_multiplier(row)
        return (base * multiplier).quantize(Decimal("0.0001"))

    def _hydrate_asset_price(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["price"] = self._current_price(row)
        return row

    def _hydrate_asset_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._hydrate_asset_price(self._normalize_supply_bounds(row)) for row in rows]

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA)
            for migration in MIGRATIONS:
                conn.execute(migration)
            reset_done = conn.execute("SELECT value FROM app_state WHERE key = 'economy_reset_v1'").fetchone()
            if not reset_done:
                conn.execute("TRUNCATE TABLE users_balance, credit_ledger, credit_farming_daily RESTART IDENTITY")
                conn.execute("TRUNCATE TABLE holdings, trades RESTART IDENTITY")
                conn.execute(
                    """
                    INSERT INTO app_state (key, value)
                    VALUES ('economy_reset_v1', 'done')
                    ON CONFLICT (key) DO NOTHING
                    """
                )
            prices_reset = conn.execute("SELECT value FROM app_state WHERE key = 'assets_start_price_v1'").fetchone()
            if not prices_reset:
                conn.execute("UPDATE assets SET price = 1, updated_at = now() WHERE price IS DISTINCT FROM 1")
                conn.execute("UPDATE assets SET outstanding_shares = COALESCE(LEAST(supply_cap / 2, outstanding_shares), supply_cap / 2)")
                conn.execute(
                    """
                    INSERT INTO app_state (key, value)
                    VALUES ('assets_start_price_v1', 'done')
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """
                )

    def app_state_get(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else None

    def app_state_set(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (key, value),
            )

    def ensure_group(self, chat_id: int, title: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO groups (chat_id, title)
                VALUES (%s, %s)
                ON CONFLICT (chat_id)
                DO UPDATE SET title = COALESCE(EXCLUDED.title, groups.title), updated_at = now()
                RETURNING *
                """,
                (chat_id, title),
            ).fetchone()
        self.ensure_assets(chat_id)
        return row

    def ensure_assets(self, chat_id: int) -> None:
        with self.connect() as conn:
            for symbol, name, description, aliases in DEFAULT_ASSETS:
                supply_cap = DEFAULT_SUPPLY_CAP
                outstanding = supply_cap / 2
                conn.execute(
                    """
                    INSERT INTO assets (chat_id, symbol, name, description, theme, aliases, status, supply_cap, outstanding_shares, price)
                    VALUES (%s, %s, %s, %s, %s, %s, 'candidate', %s, %s, 1)
                    ON CONFLICT (chat_id, symbol) DO NOTHING
                    """,
                    (chat_id, symbol, name, description, symbol.lower(), aliases, supply_cap, outstanding),
                )

    def all_groups(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            query = (
                "SELECT chat_id, title, COALESCE(title, CAST(chat_id AS text)) AS fallback_title "
                "FROM groups ORDER BY updated_at DESC"
            )
            if limit is not None:
                query = query + " LIMIT %s"
                rows = conn.execute(query, (int(limit),)).fetchall()
            else:
                rows = conn.execute(query).fetchall()
        return rows

    def touch_user(
        self, chat_id: int, user_id: int, username: str | None, display_name: str | None, points: int = 1
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO group_users (chat_id, user_id, username, display_name, points, message_count)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET username = COALESCE(EXCLUDED.username, group_users.username),
                              display_name = COALESCE(EXCLUDED.display_name, group_users.display_name),
                              points = group_users.points + EXCLUDED.points,
                              message_count = group_users.message_count + 1,
                              last_seen_at = now()
                """,
                (chat_id, user_id, username, display_name, points),
            )
            conn.execute(
                """
                INSERT INTO users_balance (chat_id, user_id, username, display_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET username = COALESCE(EXCLUDED.username, users_balance.username),
                              display_name = COALESCE(EXCLUDED.display_name, users_balance.display_name),
                              updated_at = now()
                """,
                (chat_id, user_id, username, display_name),
            )

    def user_groups(self, user_id: int, include_private: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            exclusion = "" if include_private else "AND gu.chat_id <> %s"
            args: list[Any] = [user_id]
            if not include_private:
                args.append(user_id)
            return conn.execute(
                """
                SELECT
                    g.chat_id,
                    g.title,
                    COALESCE(g.title, CAST(g.chat_id AS text)) AS fallback_title,
                    gu.last_seen_at,
                    gu.points,
                    gu.message_count
                FROM group_users gu
                JOIN groups g ON g.chat_id = gu.chat_id
                WHERE gu.user_id = %s
                """ + exclusion + """
                ORDER BY gu.last_seen_at DESC NULLS LAST, g.updated_at DESC
                """,
                tuple(args),
            ).fetchall()

    def user_has_group(self, user_id: int, chat_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM group_users WHERE chat_id = %s AND user_id = %s LIMIT 1",
                (chat_id, user_id),
            ).fetchone()
            return bool(row)

    def credit_balance(self, chat_id: int, user_id: int) -> Decimal:
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO users_balance (chat_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT (chat_id, user_id) DO NOTHING
                RETURNING credits_balance
                """,
                (chat_id, user_id),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT credits_balance FROM users_balance WHERE chat_id = %s AND user_id = %s",
                    (chat_id, user_id),
                ).fetchone()
        return Decimal(row["credits_balance"])

    def add_credits(
        self,
        chat_id: int,
        user_id: int,
        amount: Decimal,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> Decimal:
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO users_balance (chat_id, user_id, credits_balance)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (chat_id, user_id)
                    DO UPDATE SET credits_balance = users_balance.credits_balance + EXCLUDED.credits_balance,
                                  updated_at = now()
                    RETURNING credits_balance
                    """,
                    (chat_id, user_id, amount),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (chat_id, user_id, amount, reason, Jsonb(metadata or {})),
                )
        return Decimal(row["credits_balance"])

    def farm_message_credits(
        self,
        chat_id: int,
        user_id: int,
        amount: Decimal = Decimal("0.5"),
        daily_cap: Decimal = Decimal("25"),
    ) -> Decimal:
        today = datetime.now(UTC).date()
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO credit_farming_daily (chat_id, user_id, day, earned, message_count)
                    VALUES (%s, %s, %s, 0, 0)
                    ON CONFLICT (chat_id, user_id, day) DO NOTHING
                    RETURNING earned
                    """,
                    (chat_id, user_id, today),
                ).fetchone()
                current = row or conn.execute(
                    """
                    SELECT earned FROM credit_farming_daily
                    WHERE chat_id = %s AND user_id = %s AND day = %s
                    FOR UPDATE
                    """,
                    (chat_id, user_id, today),
                ).fetchone()
                earned = Decimal(current["earned"])
                award = min(amount, max(Decimal(0), daily_cap - earned))
                conn.execute(
                    """
                    UPDATE credit_farming_daily
                    SET earned = earned + %s, message_count = message_count + 1
                    WHERE chat_id = %s AND user_id = %s AND day = %s
                    """,
                    (award, chat_id, user_id, today),
                )
                if award > 0:
                    balance = conn.execute(
                        """
                        INSERT INTO users_balance (chat_id, user_id, credits_balance)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (chat_id, user_id)
                        DO UPDATE SET credits_balance = users_balance.credits_balance + EXCLUDED.credits_balance,
                                      updated_at = now()
                        RETURNING credits_balance
                        """,
                        (chat_id, user_id, award),
                    ).fetchone()
                    conn.execute(
                        """
                        INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                        VALUES (%s, %s, %s, 'message_farm', %s)
                        """,
                        (chat_id, user_id, award, Jsonb({"day": today.isoformat()})),
                    )
                    return Decimal(balance["credits_balance"])
        return self.credit_balance(chat_id, user_id)

    def daily_claim(self, chat_id: int, user_id: int, amount: Decimal = Decimal("100")) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO users_balance (chat_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id, user_id) DO UPDATE SET updated_at = now()
                    RETURNING credits_balance, last_daily_claim_at
                    """,
                    (chat_id, user_id),
                ).fetchone()
                last_claim = row["last_daily_claim_at"]
                now = datetime.now(UTC)
                if last_claim and (now - last_claim).total_seconds() < 86400:
                    return {
                        "claimed": False,
                        "balance": str(row["credits_balance"]),
                        "nextClaimAt": (last_claim.timestamp() + 86400),
                    }
                balance = conn.execute(
                    """
                    UPDATE users_balance
                    SET credits_balance = credits_balance + %s, last_daily_claim_at = %s, updated_at = now()
                    WHERE chat_id = %s AND user_id = %s
                    RETURNING credits_balance, last_daily_claim_at
                    """,
                    (amount, now, chat_id, user_id),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                    VALUES (%s, %s, %s, 'daily_claim', '{}'::jsonb)
                    """,
                    (chat_id, user_id, amount),
                )
        return {
            "claimed": True,
            "balance": str(balance["credits_balance"]),
            "nextClaimAt": (balance["last_daily_claim_at"].timestamp() + 86400),
        }

    def work_claim(self, chat_id: int, user_id: int, amount: Decimal = Decimal("25")) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO users_balance (chat_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id, user_id) DO UPDATE SET updated_at = now()
                    RETURNING credits_balance, last_work_at
                    """,
                    (chat_id, user_id),
                ).fetchone()
                last_work = row["last_work_at"]
                now = datetime.now(UTC)
                if last_work and (now - last_work).total_seconds() < 3600:
                    return {
                        "claimed": False,
                        "balance": str(row["credits_balance"]),
                        "retryAfter": int(3600 - (now - last_work).total_seconds()),
                        "nextWorkAt": last_work.timestamp() + 3600,
                    }
                balance = conn.execute(
                    """
                    UPDATE users_balance
                    SET credits_balance = credits_balance + %s, last_work_at = %s, updated_at = now()
                    WHERE chat_id = %s AND user_id = %s
                    RETURNING credits_balance, last_work_at
                    """,
                    (amount, now, chat_id, user_id),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                    VALUES (%s, %s, %s, 'work_claim', '{}'::jsonb)
                    """,
                    (chat_id, user_id, amount),
                )
        return {
            "claimed": True,
            "balance": str(balance["credits_balance"]),
            "retryAfter": 3600,
            "nextWorkAt": balance["last_work_at"].timestamp() + 3600,
        }

    def leaderboard(self, chat_id: int, limit: int = 20) -> dict[str, Any]:
        with self.connect() as conn:
            local = conn.execute(
                """
                SELECT user_id, username, display_name, credits_balance
                FROM users_balance
                WHERE chat_id = %s
                ORDER BY credits_balance DESC
                LIMIT %s
                """,
                (chat_id, limit),
            ).fetchall()
            global_rows = conn.execute(
                """
                SELECT user_id, max(username) AS username, max(display_name) AS display_name,
                       sum(credits_balance) AS credits_balance
                FROM users_balance
                GROUP BY user_id
                ORDER BY sum(credits_balance) DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return {"local": local, "global": global_rows}

    def add_message(
        self, chat_id: int, user_id: int | None, username: str | None, text: str, sentiment: float
    ) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO messages (chat_id, user_id, username, text, sentiment)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (chat_id, user_id, username, text, sentiment),
            ).fetchone()
            return int(row["id"])

    def recent_messages(self, chat_id: int, limit: int = 60) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT username, text, sentiment, created_at
                FROM messages
                WHERE chat_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (chat_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def member_recent_texts(self, chat_id: int, user_id: int, limit: int = 40) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT text
                FROM messages
                WHERE chat_id = %s AND user_id = %s AND length(text) >= 3
                ORDER BY id DESC
                LIMIT %s
                """,
                (chat_id, user_id, limit),
            ).fetchall()
        return [row["text"] for row in reversed(rows)]

    def get_member_profile(self, chat_id: int, user_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT profile FROM member_profiles WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            ).fetchone()
        return (row["profile"] if row else "") or ""

    def member_profile_due(self, chat_id: int, user_id: int, threshold: int = 25) -> bool:
        """True se il membro ha accumulato abbastanza nuovi messaggi da giustificare
        un aggiornamento del profilo (evita chiamate LLM inutili)."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT gu.message_count AS message_count,
                       COALESCE(mp.messages_at_update, 0) AS messages_at_update,
                       mp.profile AS profile
                FROM group_users gu
                LEFT JOIN member_profiles mp
                  ON mp.chat_id = gu.chat_id AND mp.user_id = gu.user_id
                WHERE gu.chat_id = %s AND gu.user_id = %s
                """,
                (chat_id, user_id),
            ).fetchone()
        if not row:
            return False
        message_count = int(row["message_count"] or 0)
        at_update = int(row["messages_at_update"] or 0)
        # Prima volta: serve un minimo di messaggi; poi ogni `threshold` nuovi.
        if row["profile"] is None or not (row["profile"] or "").strip():
            return message_count >= max(8, threshold // 2)
        return message_count - at_update >= threshold

    def set_member_profile(self, chat_id: int, user_id: int, profile: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO member_profiles (chat_id, user_id, profile, messages_at_update, updated_at)
                VALUES (
                    %s, %s, %s,
                    COALESCE((SELECT message_count FROM group_users WHERE chat_id = %s AND user_id = %s), 0),
                    now()
                )
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET profile = EXCLUDED.profile,
                              messages_at_update = EXCLUDED.messages_at_update,
                              updated_at = now()
                """,
                (chat_id, user_id, profile.strip()[:600], chat_id, user_id),
            )

    def add_chat_media(
        self,
        chat_id: int,
        message_id: int,
        media_type: str,
        file_id: str,
        file_unique_id: str | None,
        caption: str | None,
        tags: list[str],
        username: str | None,
    ) -> None:
        if not file_id:
            return
        unique_value = file_unique_id or f"{media_type}:{file_id}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_media (chat_id, message_id, media_type, file_id, file_unique_id, caption, tags, username)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id, file_unique_id)
                DO UPDATE SET caption = COALESCE(EXCLUDED.caption, chat_media.caption),
                              tags = EXCLUDED.tags,
                              username = COALESCE(EXCLUDED.username, chat_media.username),
                              disabled_at = NULL
                """,
                (chat_id, message_id, media_type, file_id, unique_value, caption, tags, username),
            )

    def search_chat_media(
        self,
        chat_id: int,
        query: str,
        media_types: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        terms = [term.lower() for term in re_words(query)[:10]]
        if not terms:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *,
                       cardinality(tags) AS tag_count
                FROM chat_media
                WHERE chat_id = %s
                  AND disabled_at IS NULL
                  AND (%s::text[] IS NULL OR media_type = ANY(%s::text[]))
                  AND created_at > now() - interval '180 days'
                ORDER BY uses ASC, created_at DESC
                LIMIT 80
                """,
                (chat_id, media_types, media_types),
            ).fetchall()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            haystack = " ".join([row.get("caption") or "", " ".join(row.get("tags") or [])]).lower()
            score = sum(3 for term in terms if term in (row.get("tags") or []))
            score += sum(1 for term in terms if term in haystack)
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (item[0], -int(item[1]["uses"])), reverse=True)
        return [row for _, row in ranked[:limit]]

    def mark_chat_media_used(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE chat_media SET uses = uses + 1 WHERE id = %s", (media_id,))

    def disable_chat_media(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE chat_media SET disabled_at = now() WHERE id = %s", (media_id,))

    def clear_chat_media(self, chat_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute("DELETE FROM chat_media WHERE chat_id = %s RETURNING id", (chat_id,)).fetchall()
        return len(row)

    def chat_media_stats(self, chat_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT media_type, count(*) AS count
                FROM chat_media
                WHERE chat_id = %s AND disabled_at IS NULL
                GROUP BY media_type
                ORDER BY media_type
                """,
                (chat_id,),
            ).fetchall()
        total = sum(int(row["count"]) for row in rows)
        return {"total": total, "by_type": rows}

    def group_settings(self, chat_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM groups WHERE chat_id = %s", (chat_id,)).fetchone()

    def set_roast_level(self, chat_id: int, level: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE groups SET roast_level = %s, updated_at = now() WHERE chat_id = %s",
                (level, chat_id),
            )

    def set_meme_mode(self, chat_id: int, mode: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE groups SET meme_mode = %s, updated_at = now() WHERE chat_id = %s",
                (mode, chat_id),
            )

    def set_bot_enabled(self, chat_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET bot_enabled = %s, paused_until = NULL, spontaneous_chance = 0, updated_at = now()
                WHERE chat_id = %s
                """,
                (enabled, chat_id),
            )

    def pause_bot(self, chat_id: int, paused_until: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET bot_enabled = true, paused_until = %s, spontaneous_chance = 0, updated_at = now()
                WHERE chat_id = %s
                """,
                (paused_until, chat_id),
            )

    def clear_bot_pause(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE groups SET paused_until = NULL, updated_at = now() WHERE chat_id = %s",
                (chat_id,),
            )

    def set_podcast_config(
        self, chat_id: int, enabled: bool, frequency: str = "weekly", day: str | None = None, time: str = "21:00"
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET podcast_enabled = %s, podcast_frequency = %s, podcast_day = %s,
                    podcast_time = %s, updated_at = now()
                WHERE chat_id = %s
                """,
                (enabled, frequency, day, time, chat_id),
            )

    def assets(self, chat_id: int, include_candidates: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*,
                       COALESCE((SELECT pct_change FROM price_ticks t
                                 WHERE t.chat_id = a.chat_id AND t.symbol = a.symbol
                                 ORDER BY t.id DESC LIMIT 1), 0) AS last_pct_change
                FROM assets a
                WHERE chat_id = %s
                  AND (%s OR status = 'listed')
                  AND status <> 'delisted'
                ORDER BY status = 'listed' DESC, symbol
                """,
                (chat_id, include_candidates),
            ).fetchall()
        return self._hydrate_asset_rows(rows)

    def asset(self, chat_id: int, symbol: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE chat_id = %s AND symbol = %s",
                (chat_id, symbol.upper()),
            ).fetchone()
        return self._hydrate_asset_price(self._normalize_supply_bounds(row)) if row else None

    def create_asset(
        self,
        chat_id: int,
        symbol: str,
        name: str,
        aliases: list[str],
        created_status: str = "candidate",
        description: str | None = None,
        supply_cap: str | int | float | Decimal | None = None,
    ) -> dict[str, Any]:
        symbol = normalize_symbol(symbol)
        clean_aliases = [item.strip().lower() for item in aliases if item.strip()]
        clean_name = name.strip()[:80]
        if not clean_name and clean_aliases:
            clean_name = f"{clean_aliases[0].capitalize()} Holdings"
        if not symbol:
            source = clean_name or " ".join(clean_aliases)
            words = [word for word in re_words(source) if word not in STOPWORDS]
            symbol_source = words[0][:5] if words and len(words[0]) >= 4 else "".join(word[:2] for word in words[:3]) or source[:5]
            symbol = normalize_symbol(symbol_source)
        if not symbol:
            raise ValueError("Symbol non valido.")
        if not clean_name:
            clean_name = f"{symbol} Holdings"
        if not clean_aliases:
            clean_aliases = [word for word in re_words(clean_name) if word not in STOPWORDS][:5] or [symbol.lower()]
        supply_cap_value = self._dec(supply_cap, str(DEFAULT_SUPPLY_CAP)) if supply_cap is not None else DEFAULT_SUPPLY_CAP
        if supply_cap_value <= 0:
            raise ValueError("Il cap azioni deve essere positivo.")
        outstanding = (supply_cap_value / 2).quantize(Decimal("0.0001"))
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO assets (chat_id, symbol, name, description, theme, aliases, status, supply_cap, outstanding_shares, price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id, symbol)
                DO UPDATE SET name = EXCLUDED.name,
                              description = COALESCE(EXCLUDED.description, assets.description),
                              aliases = EXCLUDED.aliases,
                              status = EXCLUDED.status,
                              price = COALESCE(EXCLUDED.price, assets.price),
                              supply_cap = COALESCE(EXCLUDED.supply_cap, assets.supply_cap),
                              outstanding_shares = COALESCE(EXCLUDED.outstanding_shares, assets.outstanding_shares),
                              updated_at = now()
                RETURNING *
                """,
                (
                    chat_id,
                    symbol,
                    clean_name,
                    description,
                    clean_aliases[0] if clean_aliases else symbol.lower(),
                    clean_aliases,
                    created_status,
                    supply_cap_value,
                    outstanding,
                    Decimal("1"),
                ),
            ).fetchone()
        return self._hydrate_asset_price(self._normalize_supply_bounds(row))

    def update_asset(
        self,
        chat_id: int,
        symbol: str,
        name: str | None = None,
        description: str | None = None,
        theme: str | None = None,
        aliases: list[str] | None = None,
        status: str | None = None,
        price: str | int | float | Decimal | None = None,
        supply_cap: str | int | float | Decimal | None = None,
        outstanding_shares: str | int | float | Decimal | None = None,
    ) -> dict[str, Any]:
        if not symbol:
            raise ValueError("Symbol mancante.")
        symbol = symbol.upper()
        assignments: list[str] = []
        values: list[Any] = []

        if name is not None and str(name).strip():
            assignments.append("name = %s")
            values.append(str(name).strip()[:80])

        if description is not None:
            clean_description = str(description).strip() or None
            assignments.append("description = %s")
            values.append(clean_description)

        if theme is not None:
            assignments.append("theme = %s")
            values.append(str(theme).strip()[:80] or None)

        if aliases is not None:
            assignments.append("aliases = %s")
            values.append([item.strip().lower() for item in aliases if item.strip()][:12])

        if status is not None:
            status = status.strip().lower()
            if status not in {"candidate", "listed", "paused", "delisted"}:
                raise ValueError("Stato non valido.")
            assignments.append("status = %s")
            values.append(status)

        if price is not None:
            try:
                new_price = Decimal(str(price).replace(",", "."))
            except InvalidOperation as exc:
                raise ValueError("Prezzo non valido.") from exc
            if new_price < 0:
                raise ValueError("Il prezzo non può essere negativo.")
            assignments.append("price = %s")
            values.append(new_price)

        if supply_cap is not None:
            try:
                parsed_supply_cap = Decimal(str(supply_cap).replace(",", "."))
            except InvalidOperation as exc:
                raise ValueError("Cap azioni non valido.") from exc
            if parsed_supply_cap <= 0:
                raise ValueError("Il cap azioni deve essere positivo.")
            assignments.append("supply_cap = %s")
            values.append(parsed_supply_cap)

        if outstanding_shares is not None:
            try:
                parsed_outstanding = Decimal(str(outstanding_shares).replace(",", "."))
            except InvalidOperation as exc:
                raise ValueError("Azioni in circolazione non valide.") from exc
            if parsed_outstanding < 0:
                raise ValueError("Le azioni in circolazione non possono essere negative.")
            assignments.append("outstanding_shares = %s")
            values.append(parsed_outstanding)

        if not assignments:
            raise ValueError("Specificare almeno un campo da modificare.")

        values.extend([chat_id, symbol])
        query = (
            f"UPDATE assets SET {', '.join(assignments)}, updated_at = now() "
            "WHERE chat_id = %s AND symbol = %s RETURNING *"
        )
        with self.connect() as conn:
            row = conn.execute(query, tuple(values)).fetchone()
            if not row:
                raise ValueError("Azienda non trovata.")
            row = self._normalize_supply_bounds(row)
            return self._hydrate_asset_price(row)

    def _normalize_supply_bounds(self, row: dict[str, Any]) -> dict[str, Any]:
        supply_cap = self._dec(row.get("supply_cap"), str(DEFAULT_SUPPLY_CAP))
        if supply_cap <= 0:
            supply_cap = DEFAULT_SUPPLY_CAP
        outstanding = self._dec(row.get("outstanding_shares"), str(supply_cap / 2))
        if outstanding < 0:
            outstanding = Decimal("0")
        if outstanding > supply_cap:
            outstanding = supply_cap
        if self._dec(row.get("supply_cap"), str(DEFAULT_SUPPLY_CAP)) != supply_cap or self._dec(row.get("outstanding_shares"), str(supply_cap / 2)) != outstanding:
            with self.connect() as conn:
                conn.execute(
                    "UPDATE assets SET supply_cap = %s, outstanding_shares = %s, updated_at = now() WHERE chat_id = %s AND symbol = %s",
                    (supply_cap, outstanding, int(row["chat_id"]), row["symbol"]),
                )
                row["supply_cap"] = supply_cap
                row["outstanding_shares"] = outstanding
        return row

    def delete_asset(self, chat_id: int, symbol: str) -> dict[str, Any]:
        if not symbol:
            raise ValueError("Symbol mancante.")
        symbol = symbol.upper()
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute("SELECT * FROM assets WHERE chat_id = %s AND symbol = %s", (chat_id, symbol)).fetchone()
                if not row:
                    raise ValueError("Azienda non trovata.")
                conn.execute("DELETE FROM holdings WHERE chat_id = %s AND symbol = %s", (chat_id, symbol))
                conn.execute("DELETE FROM stock_mentions WHERE chat_id = %s AND symbol = %s", (chat_id, symbol))
                conn.execute("DELETE FROM price_ticks WHERE chat_id = %s AND symbol = %s", (chat_id, symbol))
                conn.execute("DELETE FROM stock_trade_cooldowns WHERE chat_id = %s AND symbol = %s", (chat_id, symbol))
                conn.execute("DELETE FROM assets WHERE chat_id = %s AND symbol = %s", (chat_id, symbol))
        return row

    def reset_asset_prices(self, chat_id: int, symbol: str | None = None) -> int:
        if symbol:
            symbol = symbol.upper()
            with self.connect() as conn:
                result = conn.execute(
                    """
                    UPDATE assets
                    SET price = 1,
                        outstanding_shares = LEAST(
                            supply_cap,
                            GREATEST(
                                0,
                                CASE
                                    WHEN supply_cap IS NULL OR supply_cap <= 0 THEN 0
                                    ELSE supply_cap / 2
                                END
                            )
                        ),
                        updated_at = now()
                    WHERE chat_id = %s AND symbol = %s
                    """,
                    (chat_id, symbol),
                )
                if result.rowcount == 0:
                    raise ValueError("Azienda non trovata.")
                return int(result.rowcount)

        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE assets
                SET price = 1,
                    outstanding_shares = LEAST(
                        supply_cap,
                        GREATEST(
                            0,
                            CASE
                                WHEN supply_cap IS NULL OR supply_cap <= 0 THEN 0
                                ELSE supply_cap / 2
                            END
                        )
                    ),
                    updated_at = now()
                WHERE chat_id = %s
                """,
                (chat_id,),
            )
            return int(result.rowcount)

    def set_asset_status(self, chat_id: int, symbol: str, status: str) -> dict[str, Any]:
        if status not in {"candidate", "listed", "paused", "delisted"}:
            raise ValueError("Stato azienda non valido.")
        with self.connect() as conn:
            row = conn.execute(
                """
                UPDATE assets
                SET status = %s, updated_at = now()
                WHERE chat_id = %s AND symbol = %s
                RETURNING *
                """,
                (status, chat_id, symbol.upper()),
            ).fetchone()
        if not row:
            raise ValueError("Azienda non trovata.")
        return self._hydrate_asset_price(self._normalize_supply_bounds(row))

    def record_stock_mentions(self, chat_id: int, user_id: int | None, text: str, sentiment: float) -> list[str]:
        assets = self.assets(chat_id, include_candidates=False)
        text_l = (text or "").lower()
        touched: list[str] = []
        if not text_l.strip():
            return touched
        text_hash = __import__("hashlib").sha256(re.sub(r"\s+", " ", text_l).encode()).hexdigest()[:24]
        with self.connect() as conn:
            for asset in assets:
                aliases = [asset["symbol"].lower(), asset["name"].lower(), *[item.lower() for item in asset.get("aliases") or []]]
                if not any(alias and alias in text_l for alias in aliases):
                    continue
                duplicate_count = conn.execute(
                    """
                    SELECT count(*) AS count
                    FROM stock_mentions
                    WHERE chat_id = %s AND symbol = %s AND text_hash = %s AND created_at > now() - interval '2 hours'
                    """,
                    (chat_id, asset["symbol"], text_hash),
                ).fetchone()["count"]
                user_recent = conn.execute(
                    """
                    SELECT count(*) AS count
                    FROM stock_mentions
                    WHERE chat_id = %s AND symbol = %s AND user_id IS NOT DISTINCT FROM %s
                      AND created_at > now() - interval '2 hours'
                    """,
                    (chat_id, asset["symbol"], user_id),
                ).fetchone()["count"]
                holding = Decimal(0)
                if user_id:
                    row = conn.execute(
                        "SELECT quantity FROM holdings WHERE chat_id = %s AND user_id = %s AND symbol = %s",
                        (chat_id, user_id, asset["symbol"]),
                    ).fetchone()
                    holding = Decimal(row["quantity"]) if row else Decimal(0)
                weight = Decimal("1") / Decimal(1 + int(user_recent))
                if int(duplicate_count) > 0:
                    weight *= Decimal("0.15")
                if holding > 0:
                    weight *= max(Decimal("0.25"), Decimal("1") - min(Decimal("0.75"), holding / Decimal("50")))
                conn.execute(
                    """
                    INSERT INTO stock_mentions (chat_id, symbol, user_id, text_hash, sentiment, weight)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (chat_id, asset["symbol"], user_id, text_hash, Decimal(str(sentiment)), weight.quantize(Decimal("0.0001"))),
                )
                touched.append(asset["symbol"])
        return touched

    def aggregate_stock_window(self, chat_id: int, window_minutes: int = 15, max_move: Decimal = Decimal("0.45")) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        fallback_since = now - timedelta(minutes=window_minutes)
        updates: list[dict[str, Any]] = []
        with self.connect() as conn:
            assets = conn.execute(
                "SELECT * FROM assets WHERE chat_id = %s AND status = 'listed' ORDER BY symbol FOR UPDATE",
                (chat_id,),
            ).fetchall()
            for asset in assets:
                asset = self._normalize_supply_bounds(asset)
                last_tick = conn.execute(
                    """
                    SELECT created_at
                    FROM price_ticks
                    WHERE chat_id = %s AND symbol = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (chat_id, asset["symbol"]),
                ).fetchone()
                if last_tick and (now - last_tick["created_at"]).total_seconds() < window_minutes * 60:
                    continue
                since = last_tick["created_at"] if last_tick else fallback_since
                rows = conn.execute(
                    """
                    SELECT user_id, sentiment, weight
                    FROM stock_mentions
                    WHERE chat_id = %s AND symbol = %s AND created_at > %s
                    """,
                    (chat_id, asset["symbol"], since),
                ).fetchall()
                trades = conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN side = 'buy' THEN quantity ELSE 0 END), 0) AS buy_qty,
                        COALESCE(SUM(CASE WHEN side = 'sell' THEN quantity ELSE 0 END), 0) AS sell_qty
                    FROM trades
                    WHERE chat_id = %s AND symbol = %s AND created_at > %s
                    """,
                    (chat_id, asset["symbol"], since),
                ).fetchone()
                if not rows and not (trades["buy_qty"] or trades["sell_qty"]):
                    continue
                weighted_mentions = sum((Decimal(row["weight"]) for row in rows), Decimal(0))
                unique_users = len({row["user_id"] for row in rows if row["user_id"] is not None})
                avg_sentiment = sum((Decimal(row["sentiment"]) * Decimal(row["weight"]) for row in rows), Decimal(0)) / max(
                    weighted_mentions, Decimal("0.0001")
                )
                spam_ratio = Decimal(len(rows)) / Decimal(max(unique_users, 1))
                manipulation_risk = min(Decimal("1"), max(Decimal("0"), (spam_ratio - Decimal("3")) / Decimal("8")))
                supply_ratio = self._supply_ratio(asset)
                attention = min(Decimal("1"), weighted_mentions / Decimal("20"))
                participation = min(Decimal("1"), Decimal(unique_users) / Decimal("6"))
                trade_buy = Decimal(str(trades["buy_qty"] or 0))
                trade_sell = Decimal(str(trades["sell_qty"] or 0))
                trade_pressure = min(Decimal("1"), (abs(trade_buy - trade_sell)) / Decimal("12"))
                trade_direction = (
                    Decimal("1") if trade_buy > trade_sell else Decimal("-1") if trade_sell > trade_buy else Decimal("0")
                )
                trade_component = Decimal("0.00")
                if trade_pressure > Decimal("0") and trade_direction != 0:
                    intensity = Decimal(str(round(uniform(0.10, 0.45), 4)))
                    scarcity_boost = Decimal("1") + min(Decimal("0.75"), Decimal("1") - supply_ratio)
                    trade_component = trade_direction * (trade_pressure * intensity * scarcity_boost)
                raw_move = (
                    (attention * Decimal("0.06"))
                    + (participation * Decimal("0.05"))
                    + (avg_sentiment * Decimal("0.035"))
                    + trade_component
                )
                raw_move *= Decimal("1") - (manipulation_risk * Decimal("0.75"))
                raw_move *= Decimal("1") - (supply_ratio * Decimal("0.35"))
                pct_change = max(-max_move, min(max_move, raw_move)).quantize(Decimal("0.0001"))
                effective_price = self._current_price(asset)
                new_effective_price = (effective_price * (Decimal("1") + pct_change)).quantize(Decimal("0.0001"))
                if new_effective_price <= Decimal("0"):
                    new_effective_price = Decimal("0.10")
                effective_multiplier = self._supply_multiplier(asset)
                new_price = (new_effective_price / effective_multiplier).quantize(Decimal("0.0001"))
                if new_price <= Decimal("0"):
                    new_price = Decimal("0.10") / effective_multiplier
                volume = Decimal(asset["volume"]) + weighted_mentions
                signals = {
                    "windowMinutes": window_minutes,
                    "weightedMentions": str(weighted_mentions.quantize(Decimal("0.0001"))),
                    "uniqueUsers": unique_users,
                    "avgSentiment": str(avg_sentiment.quantize(Decimal("0.0001"))),
                    "spamRatio": str(spam_ratio.quantize(Decimal("0.0001"))),
                    "participation": str(participation.quantize(Decimal("0.0001"))),
                    "tradePressure": str(trade_pressure.quantize(Decimal("0.0001"))),
                    "tradeDirection": "buy" if trade_direction > 0 else "sell" if trade_direction < 0 else "flat",
                    "tradeDelta": str((trade_buy - trade_sell).quantize(Decimal("0.0001"))),
                    "supply": {
                        "supplyCap": str(asset["supply_cap"]),
                        "outstanding": str(asset["outstanding_shares"]),
                        "ratio": str(supply_ratio.quantize(Decimal("0.0001"))),
                        "effectiveMultiplier": str(effective_multiplier.quantize(Decimal("0.0001"))),
                    },
                    "rule": "quando il gruppo parla davvero del tema il prezzo sale; spam e holder pesanti pesano poco",
                }
                conn.execute(
                    """
                    UPDATE assets
                    SET price = %s, volume = %s, manipulation_risk = %s, last_signal_at = now(), updated_at = now()
                    WHERE chat_id = %s AND symbol = %s
                    """,
                    (new_price, volume, manipulation_risk, chat_id, asset["symbol"]),
                )
                tick = conn.execute(
                    """
                    INSERT INTO price_ticks
                    (chat_id, symbol, price, pct_change, volume, unique_users, mentions, sentiment, manipulation_risk, reason, signals)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'chat window', %s)
                    RETURNING *
                    """,
                    (
                        chat_id,
                        asset["symbol"],
                        new_effective_price,
                        pct_change,
                        weighted_mentions,
                        unique_users,
                        len(rows),
                        avg_sentiment,
                        manipulation_risk,
                        Jsonb(signals),
                    ),
                ).fetchone()
                updates.append({"symbol": asset["symbol"], "price": str(new_effective_price), "tick": tick})
        return updates

    def propose_stock_candidates(self, chat_id: int, limit: int = 3) -> list[dict[str, Any]]:
        recent = self.recent_messages(chat_id, limit=300)
        counts: dict[str, int] = {}
        for row in recent:
            for word in re_words(row["text"]):
                if word in STOPWORDS or len(word) < 4:
                    continue
                counts[word] = counts.get(word, 0) + 1
        created: list[dict[str, Any]] = []
        for word, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
            if count < 4 or len(created) >= limit:
                break
            symbol = normalize_symbol(word[:5])
            if self.asset(chat_id, symbol):
                continue
            name = f"{word.capitalize()} Holdings"
            created.append(self.create_asset(chat_id, symbol, name, [word], "candidate", f"Azienda nata dalle discussioni su {word}."))
        return created

    def price_history(self, chat_id: int, symbol: str, limit: int = 80, since: datetime | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT price, pct_change, volume, unique_users, mentions, sentiment, manipulation_risk, reason, signals, created_at
                FROM price_ticks
                WHERE chat_id = %s AND symbol = %s
                  AND (%s::timestamptz IS NULL OR created_at >= %s)
                ORDER BY id DESC
                LIMIT %s
                """,
                (chat_id, symbol.upper(), since, since, limit),
            ).fetchall()
        return list(reversed(rows))

    def user_portfolio(self, chat_id: int, user_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            user = conn.execute(
                "SELECT credits_balance FROM users_balance WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            ).fetchone()
            holdings = conn.execute(
                """
                SELECT h.symbol, h.quantity, h.avg_price, a.price, a.supply_cap, a.outstanding_shares, a.name
                FROM holdings h
                JOIN assets a ON a.chat_id = h.chat_id AND a.symbol = h.symbol
                WHERE h.chat_id = %s AND h.user_id = %s
                ORDER BY h.symbol
                """,
                (chat_id, user_id),
            ).fetchall()
        hydrated_holdings = []
        for row in holdings:
            row = self._hydrate_asset_price(dict(row))
            market_value = Decimal(row["quantity"]) * Decimal(row["price"])
            row["market_value"] = market_value.quantize(Decimal("0.0001"))
            row["pnl"] = (((Decimal(row["price"]) - Decimal(row["avg_price"])) * Decimal(row["quantity"])).quantize(Decimal("0.0001")))
            hydrated_holdings.append(row)
        total_value = sum((Decimal(row["market_value"]) for row in hydrated_holdings), Decimal(0))
        return {"credits": str(user["credits_balance"]) if user else "0", "crowns": str(user["credits_balance"]) if user else "0", "holdings": hydrated_holdings, "stockValue": str(total_value)}

    def trade_quote(self, chat_id: int, user_id: int, symbol: str, side: str, quantity: Decimal, fee_rate: Decimal = Decimal("0.02")) -> dict[str, Any]:
        _ = user_id
        asset = self.asset(chat_id, symbol)
        if not asset or asset["status"] != "listed":
            raise ValueError("Azienda non quotata.")
        if quantity <= 0:
            raise ValueError("La quantita deve essere maggiore di zero.")
        if side not in {"buy", "sell"}:
            raise ValueError("Operazione non valida.")
        if side == "buy":
            available = self._dec(asset["supply_cap"], "0") - self._dec(asset["outstanding_shares"], "0")
            if quantity > available:
                raise ValueError("Cap azioni raggiunto: non puoi comprare così tante azioni ora.")
        price = self._dec(asset["price"])
        total = (price * quantity).quantize(Decimal("0.0001"))
        fee_amount = (total * fee_rate).quantize(Decimal("0.0001"))
        debit = total + fee_amount if side == "buy" else Decimal(0)
        credit = total - fee_amount if side == "sell" else Decimal(0)
        return {
            "symbol": asset["symbol"],
            "side": side,
            "quantity": str(quantity),
            "price": str(price),
            "total": str(total),
            "fee": str(fee_amount),
            "debit": str(debit),
            "credit": str(credit),
        }

    def trade_asset(self, chat_id: int, user_id: int, symbol: str, side: str, quantity: Decimal, fee_rate: Decimal = Decimal("0.02")) -> dict[str, Any]:
        symbol = symbol.upper()
        if quantity <= 0:
            raise ValueError("La quantita deve essere maggiore di zero.")
        if side not in {"buy", "sell"}:
            raise ValueError("Operazione non valida.")
        with self.connect() as conn:
            with conn.transaction():
                cooldown = conn.execute(
                    """
                    SELECT last_trade_at
                    FROM stock_trade_cooldowns
                    WHERE chat_id = %s AND user_id = %s AND symbol = %s
                    FOR UPDATE
                    """,
                    (chat_id, user_id, symbol),
                ).fetchone()
                now = datetime.now(UTC)
                if cooldown and (now - cooldown["last_trade_at"]).total_seconds() < 30:
                    raise ValueError("Aspetta 30 secondi tra trade dello stesso titolo.")
                asset = conn.execute(
                    "SELECT * FROM assets WHERE chat_id = %s AND symbol = %s FOR UPDATE",
                    (chat_id, symbol),
                ).fetchone()
                if not asset or asset["status"] != "listed":
                    raise ValueError("Azienda non quotata.")
                asset = self._normalize_supply_bounds(asset)
                user = conn.execute(
                    "SELECT credits_balance FROM users_balance WHERE chat_id = %s AND user_id = %s FOR UPDATE",
                    (chat_id, user_id),
                ).fetchone()
                if not user:
                    inserted = conn.execute(
                        """
                        INSERT INTO users_balance (chat_id, user_id)
                        VALUES (%s, %s)
                        ON CONFLICT (chat_id, user_id) DO UPDATE SET updated_at = now()
                        RETURNING credits_balance
                        """,
                        (chat_id, user_id),
                    ).fetchone()
                    if not inserted:
                        raise ValueError("Saldo Crowns non trovato. Apri /arcade o scrivi nel gruppo.")
                    user = inserted

                price = self._current_price(asset)
                total = (price * quantity).quantize(Decimal("0.0001"))
                fee = (total * fee_rate).quantize(Decimal("0.0001"))
                available = self._dec(asset["supply_cap"], "0") - self._dec(asset["outstanding_shares"], "0")

                if side == "buy":
                    if quantity > available:
                        raise ValueError("Cap azioni raggiunto: non puoi comprare così tante azioni ora.")
                    debit = total + fee
                    if Decimal(user["credits_balance"]) < debit:
                        raise ValueError("Crowns insufficienti.")
                    holding = conn.execute(
                        "SELECT quantity, avg_price FROM holdings WHERE chat_id = %s AND user_id = %s AND symbol = %s FOR UPDATE",
                        (chat_id, user_id, symbol),
                    ).fetchone()
                    old_qty = Decimal(holding["quantity"]) if holding else Decimal(0)
                    old_avg = Decimal(holding["avg_price"]) if holding else Decimal(0)
                    new_qty = old_qty + quantity
                    new_avg = ((old_qty * old_avg) + (total + fee)) / new_qty
                    new_outstanding = self._dec(asset["outstanding_shares"]) + quantity
                    conn.execute(
                        "UPDATE users_balance SET credits_balance = credits_balance - %s, updated_at = now() WHERE chat_id = %s AND user_id = %s",
                        (debit, chat_id, user_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO holdings (chat_id, user_id, symbol, quantity, avg_price)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (chat_id, user_id, symbol)
                        DO UPDATE SET quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price
                        """,
                        (chat_id, user_id, symbol, new_qty, new_avg),
                    )
                    conn.execute(
                        "UPDATE assets SET outstanding_shares = %s WHERE chat_id = %s AND symbol = %s",
                        (new_outstanding, chat_id, symbol),
                    )
                    ledger_amount = -debit
                else:
                    holding = conn.execute(
                        "SELECT quantity FROM holdings WHERE chat_id = %s AND user_id = %s AND symbol = %s FOR UPDATE",
                        (chat_id, user_id, symbol),
                    ).fetchone()
                    current_qty = Decimal(holding["quantity"]) if holding else Decimal(0)
                    if current_qty < quantity:
                        raise ValueError("Quantita insufficiente in portfolio.")
                    new_qty = current_qty - quantity
                    credit = total - fee
                    new_outstanding = self._dec(asset["outstanding_shares"]) - quantity
                    if new_outstanding < 0:
                        raise ValueError("Movimento non valido sul supply della società.")
                    conn.execute(
                        "UPDATE users_balance SET credits_balance = credits_balance + %s, updated_at = now() WHERE chat_id = %s AND user_id = %s",
                        (credit, chat_id, user_id),
                    )
                    if new_qty == 0:
                        conn.execute(
                            "DELETE FROM holdings WHERE chat_id = %s AND user_id = %s AND symbol = %s",
                            (chat_id, user_id, symbol),
                        )
                    else:
                        conn.execute(
                            "UPDATE holdings SET quantity = %s WHERE chat_id = %s AND user_id = %s AND symbol = %s",
                            (new_qty, chat_id, user_id, symbol),
                        )
                    conn.execute(
                        "UPDATE assets SET outstanding_shares = %s WHERE chat_id = %s AND symbol = %s",
                        (new_outstanding, chat_id, symbol),
                    )
                    ledger_amount = credit

                conn.execute(
                    "UPDATE assets SET volume = volume + %s, updated_at = now() WHERE chat_id = %s AND symbol = %s",
                    (quantity, chat_id, symbol),
                )
                conn.execute(
                    """
                    INSERT INTO stock_trade_cooldowns (chat_id, user_id, symbol, last_trade_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id, user_id, symbol) DO UPDATE SET last_trade_at = EXCLUDED.last_trade_at
                    """,
                    (chat_id, user_id, symbol, now),
                )
                trade = conn.execute(
                    """
                    INSERT INTO trades (chat_id, user_id, symbol, side, quantity, price, fee, total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (chat_id, user_id, symbol, side, quantity, price, fee, total),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        chat_id,
                        user_id,
                        ledger_amount,
                        f"stock_{side}",
                        Jsonb(
                            {
                                "symbol": symbol,
                                "quantity": str(quantity),
                                "total": str(total),
                                "fee": str(fee),
                                "outstanding_after": str(
                                    self._dec(asset["outstanding_shares"]) + quantity
                                    if side == "buy"
                                    else self._dec(asset["outstanding_shares"]) - quantity
                                ),
                            }
                        ),
                    ),
                )
                if fee > 0:
                    conn.execute(
                        """
                        INSERT INTO credit_ledger (chat_id, user_id, amount, reason, metadata)
                        VALUES (%s, %s, %s, 'stock_fee_burn', %s)
                        """,
                        (chat_id, user_id, -fee, Jsonb({"symbol": symbol, "fee_rate": str(fee_rate)})),
                    )
        return {
            "symbol": symbol,
            "side": side,
            "quantity": str(quantity),
            "price": str(price),
            "total": str(total),
            "fee": str(fee),
            "id": int(trade["id"]),
        }

    def create_prediction_market(
        self,
        chat_id: int | None,
        question: str,
        created_by: int | None,
        scope: str = "local",
        closes_at: datetime | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            return conn.execute(
                """
                INSERT INTO prediction_markets (chat_id, scope, question, created_by, closes_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (chat_id, scope, question, created_by, closes_at),
            ).fetchone()

    def prediction_markets(self, chat_id: int | None = None, include_global: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if chat_id is None:
                return conn.execute(
                    """
                    SELECT *
                    FROM prediction_markets
                    WHERE scope = 'global'
                    ORDER BY status = 'open' DESC, id DESC
                    LIMIT 80
                    """
                ).fetchall()
            return conn.execute(
                """
                SELECT *
                FROM prediction_markets
                WHERE chat_id = %s OR (%s AND scope = 'global')
                ORDER BY status = 'open' DESC, id DESC
                LIMIT 80
                """,
                (chat_id, include_global),
            ).fetchall()

    def active_group_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT chat_id FROM groups ORDER BY updated_at DESC").fetchall()
        return [int(row["chat_id"]) for row in rows]

    def prediction_market(self, market_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM prediction_markets WHERE id = %s", (market_id,)).fetchone()

    def trade_prediction(
        self,
        market_id: int,
        chat_id: int,
        user_id: int,
        outcome: str,
        credits: Decimal,
        fee_rate: Decimal = Decimal("0.02"),
    ) -> dict[str, Any]:
        outcome = outcome.upper()
        if outcome not in {"YES", "NO"}:
            raise ValueError("Outcome non valido.")
        if credits <= 0:
            raise ValueError("Credits non validi.")
        with self.connect() as conn:
            with conn.transaction():
                market = conn.execute("SELECT * FROM prediction_markets WHERE id = %s FOR UPDATE", (market_id,)).fetchone()
                if not market or market["status"] != "open":
                    raise ValueError("Mercato non aperto.")
                balance = conn.execute(
                    "SELECT credits_balance FROM users_balance WHERE chat_id = %s AND user_id = %s FOR UPDATE",
                    (chat_id, user_id),
                ).fetchone()
                if not balance:
                    raise ValueError("Saldo Credits non trovato.")
                fee = (credits * fee_rate).quantize(Decimal("0.0001"))
                total_debit = credits + fee
                if Decimal(balance["credits_balance"]) < total_debit:
                    raise ValueError("Credits insufficienti.")
                yes_pool = Decimal(market["yes_pool"])
                no_pool = Decimal(market["no_pool"])
                old_pool = yes_pool if outcome == "YES" else no_pool
                shares = credits / max(Decimal("0.0001"), old_pool / (yes_pool + no_pool))
                new_yes = yes_pool + credits if outcome == "YES" else yes_pool
                new_no = no_pool + credits if outcome == "NO" else no_pool
                conn.execute(
                    "UPDATE users_balance SET credits_balance = credits_balance - %s, updated_at = now() WHERE chat_id = %s AND user_id = %s",
                    (total_debit, chat_id, user_id),
                )
                conn.execute(
                    "UPDATE prediction_markets SET yes_pool = %s, no_pool = %s WHERE id = %s",
                    (new_yes, new_no, market_id),
                )
                conn.execute(
                    """
                    INSERT INTO prediction_positions (market_id, chat_id, user_id, outcome, shares, avg_price)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_id, user_id, outcome)
                    DO UPDATE SET shares = prediction_positions.shares + EXCLUDED.shares,
                                  avg_price = EXCLUDED.avg_price
                    """,
                    (market_id, chat_id, user_id, outcome, shares, credits / shares),
                )
                trade = conn.execute(
                    """
                    INSERT INTO prediction_trades (market_id, chat_id, user_id, side, outcome, credits, fee, shares, price)
                    VALUES (%s, %s, %s, 'buy', %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (market_id, chat_id, user_id, outcome, credits, fee, shares, credits / shares),
                ).fetchone()
        return trade

    def cashout_prediction(
        self,
        market_id: int,
        chat_id: int,
        user_id: int,
        outcome: str,
        shares: Decimal,
        fee_rate: Decimal = Decimal("0.02"),
    ) -> dict[str, Any]:
        outcome = outcome.upper()
        if outcome not in {"YES", "NO"} or shares <= 0:
            raise ValueError("Cash-out non valido.")
        with self.connect() as conn:
            with conn.transaction():
                market = conn.execute("SELECT * FROM prediction_markets WHERE id = %s FOR UPDATE", (market_id,)).fetchone()
                if not market or market["status"] != "open":
                    raise ValueError("Mercato non aperto.")
                position = conn.execute(
                    """
                    SELECT shares FROM prediction_positions
                    WHERE market_id = %s AND user_id = %s AND outcome = %s
                    FOR UPDATE
                    """,
                    (market_id, user_id, outcome),
                ).fetchone()
                if not position or Decimal(position["shares"]) < shares:
                    raise ValueError("Shares insufficienti.")
                yes_pool = Decimal(market["yes_pool"])
                no_pool = Decimal(market["no_pool"])
                pool = yes_pool if outcome == "YES" else no_pool
                total_pool = yes_pool + no_pool
                price = max(Decimal("0.0001"), pool / total_pool)
                gross = shares * price
                fee = (gross * fee_rate).quantize(Decimal("0.0001"))
                credit = gross - fee
                new_pool = max(Decimal("1"), pool - gross)
                if outcome == "YES":
                    yes_pool = new_pool
                else:
                    no_pool = new_pool
                conn.execute("UPDATE prediction_markets SET yes_pool = %s, no_pool = %s WHERE id = %s", (yes_pool, no_pool, market_id))
                conn.execute(
                    """
                    UPDATE prediction_positions
                    SET shares = shares - %s
                    WHERE market_id = %s AND user_id = %s AND outcome = %s
                    """,
                    (shares, market_id, user_id, outcome),
                )
                conn.execute(
                    "UPDATE users_balance SET credits_balance = credits_balance + %s, updated_at = now() WHERE chat_id = %s AND user_id = %s",
                    (credit, chat_id, user_id),
                )
                trade = conn.execute(
                    """
                    INSERT INTO prediction_trades (market_id, chat_id, user_id, side, outcome, credits, fee, shares, price)
                    VALUES (%s, %s, %s, 'cashout', %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (market_id, chat_id, user_id, outcome, gross, fee, shares, price),
                ).fetchone()
        return trade

    def resolve_prediction(self, market_id: int, outcome: str, resolved_by: int | None) -> dict[str, Any]:
        outcome = outcome.upper()
        if outcome not in {"YES", "NO", "CANCEL"}:
            raise ValueError("Risoluzione non valida.")
        with self.connect() as conn:
            with conn.transaction():
                market = conn.execute("SELECT * FROM prediction_markets WHERE id = %s FOR UPDATE", (market_id,)).fetchone()
                if not market or market["status"] != "open":
                    raise ValueError("Mercato non aperto.")
                conn.execute(
                    """
                    UPDATE prediction_markets
                    SET status = 'resolved', resolved_outcome = %s, resolved_by = %s, resolved_at = now()
                    WHERE id = %s
                    """,
                    (outcome, resolved_by, market_id),
                )
                if outcome == "CANCEL":
                    rows = conn.execute(
                        "SELECT chat_id, user_id, outcome, shares, avg_price FROM prediction_positions WHERE market_id = %s",
                        (market_id,),
                    ).fetchall()
                    for row in rows:
                        refund = Decimal(row["shares"]) * Decimal(row["avg_price"])
                        conn.execute(
                            "UPDATE users_balance SET credits_balance = credits_balance + %s WHERE chat_id = %s AND user_id = %s",
                            (refund, row["chat_id"], row["user_id"]),
                        )
                else:
                    winners = conn.execute(
                        "SELECT chat_id, user_id, shares FROM prediction_positions WHERE market_id = %s AND outcome = %s",
                        (market_id, outcome),
                    ).fetchall()
                    total_shares = sum((Decimal(row["shares"]) for row in winners), Decimal(0))
                    payout_pool = Decimal(market["yes_pool"]) + Decimal(market["no_pool"])
                    if total_shares > 0:
                        for row in winners:
                            payout = payout_pool * (Decimal(row["shares"]) / total_shares)
                            conn.execute(
                                "UPDATE users_balance SET credits_balance = credits_balance + %s WHERE chat_id = %s AND user_id = %s",
                                (payout, row["chat_id"], row["user_id"]),
                            )
                resolved = conn.execute("SELECT * FROM prediction_markets WHERE id = %s", (market_id,)).fetchone()
        return resolved

    def prediction_positions(self, chat_id: int, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT p.*, m.question, m.status, m.resolved_outcome
                FROM prediction_positions p
                JOIN prediction_markets m ON m.id = p.market_id
                WHERE p.chat_id = %s AND p.user_id = %s AND p.shares > 0
                ORDER BY p.market_id DESC
                """,
                (chat_id, user_id),
            ).fetchall()

    def due_podcast_groups(self, now: datetime) -> list[dict[str, Any]]:
        weekday = now.strftime("%A").lower()
        hhmm = now.strftime("%H:%M")
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM groups
                WHERE podcast_enabled = true
                  AND podcast_time = %s
                  AND (
                    podcast_frequency = 'daily'
                    OR (podcast_frequency = 'weekly' AND podcast_day = %s)
                  )
                  AND (last_podcast_at IS NULL OR last_podcast_at::date < %s::date)
                """,
                (hhmm, weekday, now),
            ).fetchall()

    def has_podcast_today(self, chat_id: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM podcasts
                WHERE chat_id = %s
                  AND status = 'created'
                  AND created_at::date = %s::date
                LIMIT 1
                """,
                (chat_id, now),
            ).fetchone()
        return row is not None

    def create_podcast(self, chat_id: int, script: str, audio_path: str | None, status: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO podcasts (chat_id, script, audio_path, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (chat_id, script, audio_path, status),
            ).fetchone()
            conn.execute("UPDATE groups SET last_podcast_at = %s WHERE chat_id = %s", (datetime.now(UTC), chat_id))
            return int(row["id"])

    def create_place_session(
        self,
        token_hash: str,
        user_id: int,
        username: str | None,
        source_chat_id: int | None,
        expires_at: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO place_sessions (token_hash, user_id, username, source_chat_id, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (token_hash, user_id, username, source_chat_id, expires_at),
            )

    def place_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM place_sessions
                WHERE token_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > now()
                """,
                (token_hash,),
            ).fetchone()

    def create_place_event(
        self,
        x: int,
        y: int,
        color_id: int,
        user_id: int,
        username: str | None,
        source_chat_id: int | None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            return conn.execute(
                """
                INSERT INTO place_events (x, y, color_id, user_id, username, source_chat_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING seq, x, y, color_id, user_id, username, source_chat_id, created_at
                """,
                (x, y, color_id, user_id, username, source_chat_id),
            ).fetchone()

    def place_events_after(self, seq: int, limit: int = 5000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT seq, x, y, color_id, user_id, username, source_chat_id, created_at
                FROM place_events
                WHERE seq > %s
                ORDER BY seq
                LIMIT %s
                """,
                (seq, limit),
            ).fetchall()

    def latest_place_event_at(self, x: int, y: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT seq, x, y, color_id, user_id, username, source_chat_id, created_at
                FROM place_events
                WHERE x = %s AND y = %s
                ORDER BY seq DESC
                LIMIT 1
                """,
                (x, y),
            ).fetchone()

    def latest_place_snapshot(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT data, last_seq, created_at
                FROM place_snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

    def create_place_snapshot(self, data: bytes, last_seq: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO place_snapshots (data, last_seq)
                VALUES (%s, %s)
                """,
                (data, last_seq),
            )
