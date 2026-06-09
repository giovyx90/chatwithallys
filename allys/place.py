from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from allys.config import Settings
from allys.db import Database


WIDTH = 1000
HEIGHT = 1000
CANVAS_SIZE = WIDTH * HEIGHT
COOLDOWN_SECONDS = 30
SESSION_TTL = timedelta(hours=2)

MINECRAFT_COLORS = [
    {"id": 0, "name": "white", "hex": "#F9FFFE"},
    {"id": 1, "name": "orange", "hex": "#F9801D"},
    {"id": 2, "name": "magenta", "hex": "#C74EBD"},
    {"id": 3, "name": "light_blue", "hex": "#3AB3DA"},
    {"id": 4, "name": "yellow", "hex": "#FED83D"},
    {"id": 5, "name": "lime", "hex": "#80C71F"},
    {"id": 6, "name": "pink", "hex": "#F38BAA"},
    {"id": 7, "name": "gray", "hex": "#474F52"},
    {"id": 8, "name": "light_gray", "hex": "#9D9D97"},
    {"id": 9, "name": "cyan", "hex": "#169C9C"},
    {"id": 10, "name": "purple", "hex": "#8932B8"},
    {"id": 11, "name": "blue", "hex": "#3C44AA"},
    {"id": 12, "name": "brown", "hex": "#835432"},
    {"id": 13, "name": "green", "hex": "#5E7C16"},
    {"id": 14, "name": "red", "hex": "#B02E26"},
    {"id": 15, "name": "black", "hex": "#1D1D21"},
]


class PlaceError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True)
class PlaceSession:
    user_id: int
    username: str | None
    source_chat_id: int | None


class PlaceService:
    canvas_key = "place:canvas:v1"
    last_seq_key = "place:last_seq:v1"
    cooldown_prefix = "place:cooldown:"
    channel = "place:updates:v1"

    def __init__(self, settings: Settings, db: Database, redis: Redis):
        self.settings = settings
        self.db = db
        self.redis = redis
        self._ready = asyncio.Lock()
        self._initialized = False

    async def ensure_ready(self) -> None:
        async with self._ready:
            if self._initialized and await self.redis.strlen(self.canvas_key) == CANVAS_SIZE:
                return
            if await self.redis.strlen(self.canvas_key) == CANVAS_SIZE:
                self._initialized = True
                return

            snapshot = self.db.latest_place_snapshot()
            if snapshot:
                canvas = bytearray(bytes(snapshot["data"]))
                last_seq = int(snapshot["last_seq"])
                if len(canvas) != CANVAS_SIZE:
                    canvas = bytearray(CANVAS_SIZE)
                    last_seq = 0
            else:
                canvas = bytearray(CANVAS_SIZE)
                last_seq = 0

            while True:
                events = self.db.place_events_after(last_seq, limit=10000)
                if not events:
                    break
                for event in events:
                    canvas[self._index(int(event["x"]), int(event["y"]))] = int(event["color_id"])
                    last_seq = int(event["seq"])

            await self.redis.set(self.canvas_key, bytes(canvas))
            await self.redis.set(self.last_seq_key, str(last_seq).encode())
            self._initialized = True

    def create_session(self, user_id: int, username: str | None, source_chat_id: int | None) -> str:
        token = secrets.token_urlsafe(32)
        self.db.create_place_session(
            self._hash_token(token),
            user_id,
            username,
            source_chat_id,
            datetime.now(UTC) + SESSION_TTL,
        )
        return token

    def validate_session(self, token: str | None) -> PlaceSession:
        if not token:
            raise PlaceError("invalid_session", "Sessione mancante.")
        row = self.db.place_session(self._hash_token(token))
        if not row:
            raise PlaceError("invalid_session", "Sessione non valida o scaduta.")
        return PlaceSession(int(row["user_id"]), row.get("username"), row.get("source_chat_id"))

    async def meta(self) -> dict[str, Any]:
        await self.ensure_ready()
        return {
            "width": WIDTH,
            "height": HEIGHT,
            "cooldownSeconds": COOLDOWN_SECONDS,
            "lastSeq": await self.last_seq(),
            "palette": MINECRAFT_COLORS,
        }

    async def snapshot(self) -> bytes:
        await self.ensure_ready()
        data = await self.redis.get(self.canvas_key)
        if not data or len(data) != CANVAS_SIZE:
            raise PlaceError("server_busy", "Canvas non pronto.")
        return bytes(data)

    async def events_after(self, seq: int, limit: int = 5000) -> list[dict[str, Any]]:
        await self.ensure_ready()
        return [self._public_event(row) for row in self.db.place_events_after(max(0, seq), min(limit, 10000))]

    async def pixel_info(self, x: int, y: int) -> dict[str, Any]:
        await self.ensure_ready()
        self._validate_coordinate(x, y)
        color_id = await self.pixel_color(x, y)
        event = self.db.latest_place_event_at(x, y)
        if not event:
            return {"x": x, "y": y, "colorId": color_id, "placedBy": None, "event": None}
        return {
            "x": x,
            "y": y,
            "colorId": color_id,
            "placedBy": self._display_username(event.get("username"), int(event["user_id"])),
            "event": self._public_event(event, include_owner=True),
        }

    async def pixel_color(self, x: int, y: int) -> int:
        data = await self.redis.getrange(self.canvas_key, self._index(x, y), self._index(x, y))
        if not data:
            return 0
        return int(data[0])

    async def place_pixel(
        self,
        token: str | None,
        x: int,
        y: int,
        color_id: int,
        source_chat_id: int | None = None,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        session = self.validate_session(token)
        self._validate_pixel(x, y, color_id)

        cooldown_key = f"{self.cooldown_prefix}{session.user_id}"
        if not await self.redis.set(cooldown_key, b"1", ex=COOLDOWN_SECONDS, nx=True):
            ttl = await self.redis.ttl(cooldown_key)
            raise PlaceError("cooldown", "Aspetta il cooldown.", retry_after=max(1, int(ttl)))

        try:
            event = self.db.create_place_event(
                x,
                y,
                color_id,
                session.user_id,
                session.username,
                source_chat_id if source_chat_id is not None else session.source_chat_id,
            )
        except Exception as exc:
            await self.redis.delete(cooldown_key)
            raise PlaceError("server_busy", "Scrittura non riuscita.") from exc

        await self.redis.setrange(self.canvas_key, self._index(x, y), bytes([color_id]))
        public = self._public_event(event)
        await self.redis.set(self.last_seq_key, str(public["seq"]).encode())
        await self.redis.publish(self.channel, json.dumps({"type": "update", "event": public}, default=str))
        return {"event": public, "cooldownSeconds": COOLDOWN_SECONDS}

    async def last_seq(self) -> int:
        raw = await self.redis.get(self.last_seq_key)
        if not raw:
            return 0
        return int(raw.decode() if isinstance(raw, bytes) else raw)

    async def save_snapshot(self) -> None:
        await self.ensure_ready()
        data = await self.snapshot()
        self.db.create_place_snapshot(data, await self.last_seq())

    async def pubsub(self):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel)
        return pubsub

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _index(x: int, y: int) -> int:
        return y * WIDTH + x

    @staticmethod
    def _validate_pixel(x: int, y: int, color_id: int) -> None:
        PlaceService._validate_coordinate(x, y)
        if color_id < 0 or color_id >= len(MINECRAFT_COLORS):
            raise PlaceError("invalid_color", "Colore non valido.")

    @staticmethod
    def _validate_coordinate(x: int, y: int) -> None:
        if x < 0 or x >= WIDTH or y < 0 or y >= HEIGHT:
            raise PlaceError("invalid_coordinate", "Coordinate fuori canvas.")

    @staticmethod
    def _display_username(username: str | None, user_id: int) -> str:
        if username:
            return f"@{username}"
        return f"utente {user_id}"

    @staticmethod
    def _public_event(row: dict[str, Any], include_owner: bool = False) -> dict[str, Any]:
        event = {
            "seq": int(row["seq"]),
            "x": int(row["x"]),
            "y": int(row["y"]),
            "colorId": int(row["color_id"]),
            "createdAt": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        }
        if include_owner:
            event["placedBy"] = PlaceService._display_username(row.get("username"), int(row["user_id"]))
        return event
