from contextlib import asynccontextmanager
from datetime import UTC
from decimal import Decimal, InvalidOperation
import asyncio
import json
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, FSInputFile, Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis

from allys.bot import PODCAST_TASKS, build_dispatcher, setup_router
from allys.config import get_settings
from allys.db import Database
from allys.market import MarketService
from allys.ollama import OllamaClient
from allys.place import PlaceError, PlaceService
from allys.podcast import PodcastService
from allys.rag import RagMemory
from allys.tma import validate_init_data, validate_session


settings = get_settings()
logger = logging.getLogger("allys.telegram")
db = Database(settings)
ollama = OllamaClient(settings)
rag = RagMemory(settings, ollama)
redis = Redis.from_url(settings.redis_url, decode_responses=True)
redis_binary = Redis.from_url(settings.redis_url, decode_responses=False)
market = MarketService(db, redis)
podcasts = PodcastService(settings, db, ollama)
place = PlaceService(settings, db, redis_binary)
bot = Bot(settings.telegram_bot_token)
features = {
    "arcade": settings.feature_arcade,
    "place": settings.feature_place,
    "market": settings.feature_market,
    "predictions": settings.feature_predictions,
    "credits": settings.feature_credits,
    "podcast": settings.feature_podcast,
}
dispatcher = build_dispatcher(
    setup_router(
        db,
        rag,
        ollama,
        market,
        podcasts,
        place,
        settings.public_base_url,
        settings.telegram_webhook_secret,
        settings.predictions_base_url,
        settings.predictions_session_secret,
        settings.owner_ids,
        features,
        settings.giphy_api_key,
        settings.tenor_api_key,
        settings.meme_reddit_fallback,
    )
)
scheduler = AsyncIOScheduler(timezone=settings.podcast_timezone)
_USER_GROUP_DISCOVERY_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_USER_GROUP_DISCOVERY_TTL = 120


async def setup_telegram_commands() -> None:
    private_commands = [
        BotCommand(command="start", description="Apri guida"),
        BotCommand(command="allys", description="Stato e comandi"),
        BotCommand(command="arcade", description="Apri Allys Arcade"),
        BotCommand(command="borsa", description="Apri la borsa sociale"),
        BotCommand(command="azioni", description="Lista aziende del gruppo"),
        BotCommand(command="prezzo", description="Prezzo di una azienda"),
        BotCommand(command="portfolio", description="Portfolio e saldo Crowns"),
        BotCommand(command="compra", description="Compra azioni"),
        BotCommand(command="vendi", description="Vendi azioni"),
        BotCommand(command="lavora", description="+25 Crowns ogni ora"),
        BotCommand(command="daily", description="+100 Crowns ogni 24h"),
        BotCommand(command="place", description="Apri Minecraft Place"),
        BotCommand(command="podcast", description="Stato podcast"),
        BotCommand(command="podcast_now", description="Genera podcast ora"),
        BotCommand(command="predictions", description="Apertura bot Predictions"),
        BotCommand(command="recap", description="Recap AI della chat"),
        BotCommand(command="mood", description="Umore del gruppo"),
        BotCommand(command="memoria", description="Gestione memoria"),
    ]
    group_commands = [
        BotCommand(command="allys", description="Stato e comandi"),
        BotCommand(command="allys_status", description="Stato del bot"),
        BotCommand(command="arcade", description="Apri Allys Arcade"),
        BotCommand(command="borsa", description="Apri la borsa sociale"),
        BotCommand(command="azioni", description="Lista aziende"),
        BotCommand(command="prezzo", description="Prezzo azienda"),
        BotCommand(command="portfolio", description="Portfolio Crowns"),
        BotCommand(command="compra", description="Compra azioni"),
        BotCommand(command="vendi", description="Vendi azioni"),
        BotCommand(command="lavora", description="+25 Crowns ogni ora"),
        BotCommand(command="daily", description="+100 Crowns ogni 24h"),
        BotCommand(command="place", description="Apri Minecraft Place"),
        BotCommand(command="podcast", description="Stato podcast"),
        BotCommand(command="podcast_now", description="Genera podcast ora"),
        BotCommand(command="roast_level", description="Configura roast"),
        BotCommand(command="predictions", description="Predictions nel mini gioco"),
        BotCommand(command="recap", description="Recap AI della chat"),
        BotCommand(command="mood", description="Umore del gruppo"),
        BotCommand(command="memoria", description="Gestione memoria"),
    ]
    admin_commands = group_commands + [
        BotCommand(command="allys_on", description="Riaccendi Allys"),
        BotCommand(command="allys_off", description="Spegni Allys"),
        BotCommand(command="allys_pause", description="Pausa temporanea"),
        BotCommand(command="podcast_config", description="Configura podcast"),
        BotCommand(command="meme_mode", description="Configura meme"),
        BotCommand(command="meme_stats", description="Statistiche meme"),
        BotCommand(command="meme_clear", description="Svuota media meme"),
        BotCommand(command="azienda_crea", description="Quota azienda"),
        BotCommand(command="azienda_approva", description="Approva azienda"),
        BotCommand(command="azienda_pausa", description="Pausa azienda"),
        BotCommand(command="azienda_modifica", description="Modifica azienda"),
        BotCommand(command="azienda_elimina", description="Elimina azienda"),
        BotCommand(command="azienda_reset_prezzi", description="Reset prezzi a 1 Crowns"),
    ]
    try:
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeAllChatAdministrators())
    except TelegramUnauthorizedError:
        logger.error("Bot token non autorizzato: impossibile impostare i comandi Telegram. Verifica il token in .env.")


async def run_due_podcasts() -> None:
    for group in podcasts.due_groups():
        chat_id = int(group["chat_id"])
        current = PODCAST_TASKS.get(chat_id)
        if current and not current.done():
            logger.info("scheduled podcast skipped because manual podcast is running for chat_id=%s", chat_id)
            continue
        if db.has_podcast_today(chat_id):
            logger.info("scheduled podcast skipped because one was already created today for chat_id=%s", chat_id)
            continue
        notice = await bot.send_message(chat_id, "Podcast programmato avviato. Ci sto lavorando.")

        async def progress(text: str) -> None:
            try:
                await notice.edit_text(text)
            except Exception:
                logger.info("scheduled podcast progress update skipped for chat_id=%s: %s", chat_id, text)

        try:
            _, audio_path = await podcasts.generate(chat_id, progress=progress)
            if audio_path:
                await bot.send_audio(chat_id, FSInputFile(audio_path), caption="Podcast programmato di Allys.")
                await notice.delete()
            else:
                await notice.edit_text("Podcast generato, ma audio non disponibile.")
        except Exception:
            logger.exception("failed to generate scheduled podcast for chat_id=%s", chat_id)
            await notice.edit_text("Podcast programmato non riuscito: errore durante generazione o audio.")


async def run_daily_prediction_markets() -> None:
    now = __import__("datetime").datetime.now(ZoneInfo(settings.podcast_timezone))
    if now.strftime("%H:%M") != "16:00":
        return
    day = now.date().isoformat()
    for chat_id in db.active_group_ids():
        key = f"daily_prediction:{chat_id}:{day}"
        if db.app_state_get(key):
            continue
        recent = db.recent_messages(chat_id, limit=120)
        digest = "\n".join(f"{row.get('username') or 'anon'}: {row['text']}" for row in recent[-80:])
        try:
            question = await ollama.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Genera una sola domanda prediction market satirica, in italiano, per un gruppo Telegram. "
                            "Deve avere risposta SI o NO, massimo 120 caratteri, niente markdown."
                        ),
                    },
                    {"role": "user", "content": digest or "Gruppo silenzioso: crea una domanda ironica generica."},
                ],
                num_predict=80,
                temperature=0.85,
            )
            question = question.strip().strip('"')[:160]
            if len(question) < 8:
                question = "Domani il gruppo riuscira a non creare caos per piu di dieci minuti?"
            db.create_prediction_market(chat_id, question, None, "local")
            db.app_state_set(key, "created")
            await bot.send_message(chat_id, f"Mercato AI delle 16:00:\n{question}\nAprilo con /predictions")
        except Exception:
            logger.exception("failed to create daily prediction market for chat_id=%s", chat_id)


async def run_stock_windows() -> None:
    if not settings.feature_market:
        return
    for chat_id in db.active_group_ids():
        try:
            await market.aggregate_group(chat_id)
        except Exception:
            logger.exception("failed to aggregate stock window for chat_id=%s", chat_id)


async def run_stock_candidate_generation() -> None:
    if not settings.feature_market:
        return
    now = __import__("datetime").datetime.now(ZoneInfo(settings.podcast_timezone))
    if now.strftime("%H:%M") != "12:00":
        return
    day = now.date().isoformat()
    for chat_id in db.active_group_ids():
        key = f"stock_candidates:{chat_id}:{day}"
        if db.app_state_get(key):
            continue
        try:
            created = market.propose_candidates(chat_id)
            db.app_state_set(key, "done")
            if created:
                lines = ["Nuove aziende candidate dalla memoria chat:"]
                lines.extend(f"{item['symbol']} - {item['name']}" for item in created)
                lines.append("Admin: /azienda_approva SYMBOL per quotarle.")
                await bot.send_message(chat_id, "\n".join(lines))
        except Exception:
            logger.exception("failed to propose stock candidates for chat_id=%s", chat_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    await setup_telegram_commands()
    if settings.feature_place:
        await place.ensure_ready()
        scheduler.add_job(place.save_snapshot, "interval", minutes=5, id="place-snapshot", max_instances=1)
    if settings.feature_podcast:
        scheduler.add_job(run_due_podcasts, "interval", minutes=1, id="due-podcasts", max_instances=1)
    if settings.feature_predictions:
        scheduler.add_job(run_daily_prediction_markets, "interval", minutes=1, id="daily-predictions", max_instances=1)
    if settings.feature_market:
        scheduler.add_job(run_stock_windows, "interval", minutes=1, id="stock-windows", max_instances=1)
        scheduler.add_job(run_stock_candidate_generation, "interval", minutes=1, id="stock-candidates", max_instances=1)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await bot.session.close()
    await redis.aclose()
    await redis_binary.aclose()


app = FastAPI(title="Chat With Allys", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="invalid telegram secret")
    raw_update = await request.json()
    message = (
        raw_update.get("message")
        or raw_update.get("edited_message")
        or raw_update.get("channel_post")
        or raw_update.get("edited_channel_post")
    )
    if message:
        chat = message.get("chat", {})
        sender = message.get("from", {})
        logger.info(
            "telegram update_id=%s chat_id=%s chat_type=%s user_id=%s text=%r keys=%s",
            raw_update.get("update_id"),
            chat.get("id"),
            chat.get("type"),
            sender.get("id"),
            message.get("text"),
            sorted(message.keys()),
        )
    else:
        logger.info("telegram update_id=%s keys=%s", raw_update.get("update_id"), sorted(raw_update.keys()))
    update = Update.model_validate(raw_update, context={"bot": bot})
    try:
        await dispatcher.feed_update(bot, update)
    except Exception:
        logger.exception("failed to process telegram update_id=%s", raw_update.get("update_id"))
    return {"ok": True}


@app.get("/api/tma/health")
async def tma_health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/tma/bootstrap")
async def tma_bootstrap(payload: dict) -> dict:
    require_feature("arcade")
    data = tma_user(payload)
    await refresh_user_group_memberships(int(data["user"]["id"]), force=True)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    user = data["user"]
    db.ensure_group(chat_id)
    db.touch_user(chat_id, user_id, user.get("username"), display_name(user))
    return {
        "telegram": data,
        "group": db.group_settings(chat_id),
        "balance": str(db.credit_balance(chat_id, user_id)),
        "assets": db.assets(chat_id),
        "stockPortfolio": db.user_portfolio(chat_id, user_id),
        "predictions": db.prediction_markets(chat_id),
        "leaderboard": db.leaderboard(chat_id),
        "features": {"credits": True, "dailyClaim": True, "predictions": True, "place": True, "market": True},
    }


@app.get("/api/groups/mine")
async def api_my_groups(initData: str = "", session: str = "") -> dict[str, Any]:
    require_feature("arcade")
    data = validate_auth(initData, session)
    user_id = int(data["user"]["id"])
    rows = await refresh_user_group_memberships(user_id, force=True)
    return {
        "userId": user_id,
        "groups": [
            {
                "chat_id": int(row["chat_id"]),
                "title": row["title"] or row.get("fallback_title"),
                "last_seen_at": row["last_seen_at"],
                "points": int(row["points"] or 0),
                "message_count": int(row["message_count"] or 0),
            }
            for row in rows
        ],
    }


@app.post("/api/credits/daily-claim")
async def api_daily_claim(payload: dict) -> dict:
    require_feature("credits")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    result = db.daily_claim(chat_id, user_id)
    await redis.publish(f"arcade:{chat_id}:{user_id}", json.dumps({"type": "balance", "balance": result["balance"]}))
    return result


@app.post("/api/credits/work")
async def api_work_claim(payload: dict) -> dict:
    require_feature("credits")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    result = db.work_claim(chat_id, user_id)
    await redis.publish(f"arcade:{chat_id}:{user_id}", json.dumps({"type": "balance", "balance": result["balance"]}))
    return result


@app.get("/api/arcade/leaderboard")
async def api_leaderboard(chat_id: int) -> dict:
    require_feature("arcade")
    return db.leaderboard(chat_id)


@app.websocket("/api/arcade/ws")
async def arcade_ws(websocket: WebSocket, chat_id: int, initData: str = "", session: str = "") -> None:
    if not settings.feature_arcade:
        await websocket.accept()
        await websocket.send_json({"type": "error", "code": "feature_disabled"})
        await websocket.close()
        return
    await websocket.accept()
    try:
        data = validate_auth(initData, session)
        chat_id = authorized_chat_id({"chatId": chat_id}, data)
        user_id = int(data["user"]["id"])
    except (ValueError, HTTPException):
        await websocket.send_json({"type": "error", "code": "invalid_session"})
        await websocket.close()
        return
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"arcade:{chat_id}:{user_id}", f"market:{chat_id}", f"predictions:{chat_id}")
    try:
        await websocket.send_json({"type": "balance", "balance": str(db.credit_balance(chat_id, user_id))})
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if message and message.get("data"):
                raw = message["data"]
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"type": "event", "value": raw}
                try:
                    await websocket.send_json(payload)
                except (WebSocketDisconnect, RuntimeError):
                    break
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.close()


@app.get("/api/groups/{chat_id}/assets")
async def api_assets(chat_id: int) -> dict:
    require_feature("market")
    return {"assets": db.assets(chat_id)}


@app.get("/api/groups/{chat_id}/assets/{symbol}/history")
async def api_history(chat_id: int, symbol: str) -> dict:
    require_feature("market")
    return {"history": db.price_history(chat_id, symbol.upper())}


@app.post("/api/groups/{chat_id}/trade")
async def api_trade(chat_id: int, payload: dict) -> dict:
    require_feature("market")
    telegram = tma_user(payload)
    chat_id = authorized_chat_id({**payload, "chatId": chat_id}, telegram)
    user_id = int(telegram["user"]["id"])
    side = payload.get("side")
    symbol = payload.get("symbol", "")
    try:
        quantity = Decimal(str(payload.get("quantity", "0")).replace(",", "."))
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="Quantita non valida.") from exc
    try:
        result = db.trade_asset(chat_id, user_id, symbol, side, quantity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    portfolio = db.user_portfolio(chat_id, user_id)
    await redis.publish(f"arcade:{chat_id}:{user_id}", json.dumps({"type": "balance", "balance": portfolio["credits"]}))
    await redis.publish(f"market:{chat_id}", json.dumps({"type": "stock_trade", "symbol": result["symbol"]}))
    return {"trade": result, "portfolio": portfolio, "assets": db.assets(chat_id)}


@app.get("/api/stocks")
async def api_stocks(chat_id: int) -> dict:
    require_feature("market")
    return {"assets": db.assets(chat_id)}


@app.get("/api/stocks/candidates")
async def api_stock_candidates(chat_id: int) -> dict:
    require_feature("market")
    return {"assets": [asset for asset in db.assets(chat_id, include_candidates=True) if asset["status"] == "candidate"]}


@app.get("/api/stocks/portfolio")
async def api_stock_portfolio(chat_id: int, initData: str = "", session: str = "") -> dict:
    require_feature("market")
    data = tma_user({"initData": initData, "session": session})
    chat_id = authorized_chat_id({"chatId": chat_id}, data)
    return db.user_portfolio(chat_id, int(data["user"]["id"]))


@app.get("/api/stocks/{symbol}")
async def api_stock_detail(symbol: str, chat_id: int) -> dict:
    require_feature("market")
    asset = db.asset(chat_id, symbol)
    if not asset:
        raise HTTPException(status_code=404, detail="Azienda non trovata.")
    return {"asset": asset, "history": db.price_history(chat_id, symbol.upper(), limit=60), "portfolio": None}


@app.get("/api/stocks/{symbol}/history")
async def api_stock_history(symbol: str, chat_id: int, range: str = Query(default="24h")) -> dict:
    require_feature("market")
    return {"history": db.price_history(chat_id, symbol.upper(), limit=240, since=history_since(range))}


@app.post("/api/stocks/trade/quote")
async def api_stock_quote(payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    try:
        quantity = Decimal(str(payload.get("quantity", "0")).replace(",", "."))
        quote = db.trade_quote(chat_id, user_id, str(payload.get("symbol", "")), str(payload.get("side", "")), quantity)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"quote": quote}


@app.post("/api/stocks/trade")
async def api_stock_trade(payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    return await api_trade(chat_id, payload)


@app.post("/api/stocks/candidates/{symbol}/approve")
async def api_stock_candidate_approve(symbol: str, payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    if not await api_can_manage_chat(chat_id, int(data["user"]["id"])):
        raise HTTPException(status_code=403, detail="Solo admin gruppo.")
    return {"asset": db.set_asset_status(chat_id, symbol, "listed")}


@app.post("/api/stocks")
async def api_stock_create(payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    if not await api_can_manage_chat(chat_id, int(data["user"]["id"])):
        raise HTTPException(status_code=403, detail="Solo admin gruppo.")
    aliases = payload.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [item.strip() for item in aliases.split(",") if item.strip()]
    try:
        asset = db.create_asset(chat_id, str(payload.get("symbol", "")), str(payload.get("name", "")), aliases, str(payload.get("status", "listed")), payload.get("description"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"asset": asset}


@app.patch("/api/stocks/{symbol}")
async def api_stock_patch(symbol: str, payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    if not await api_can_manage_chat(chat_id, int(data["user"]["id"])):
        raise HTTPException(status_code=403, detail="Solo admin gruppo.")
    updates: dict[str, object] = {}
    status = str(payload.get("status", "")).strip()
    if status:
        updates["status"] = status
    if "name" in payload:
        updates["name"] = str(payload["name"]).strip()
    if "description" in payload:
        updates["description"] = str(payload["description"]).strip()
    if "theme" in payload:
        updates["theme"] = str(payload["theme"]).strip()
    if "aliases" in payload:
        aliases = payload["aliases"] or []
        if isinstance(aliases, str):
            aliases = [item.strip() for item in aliases.split(",") if item.strip()]
        elif not isinstance(aliases, list):
            raise HTTPException(status_code=400, detail="aliases deve essere una lista o stringa.")
        updates["aliases"] = [str(item).strip() for item in aliases if str(item).strip()]
    if "price" in payload:
        updates["price"] = payload.get("price")

    if not updates:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare.")

    try:
        return {"asset": db.update_asset(chat_id, symbol, **updates)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/stocks/{symbol}")
async def api_stock_delete(symbol: str, payload: dict) -> dict:
    require_feature("market")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    if not await api_can_manage_chat(chat_id, int(data["user"]["id"])):
        raise HTTPException(status_code=403, detail="Solo admin gruppo.")
    try:
        return {"asset": db.delete_asset(chat_id, symbol)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/market/{chat_id}/assets")
async def api_market_assets(chat_id: int) -> dict:
    require_feature("market")
    return {"assets": db.assets(chat_id)}


@app.get("/api/market/{chat_id}/assets/{symbol}/history")
async def api_market_history(chat_id: int, symbol: str) -> dict:
    require_feature("market")
    return {"history": db.price_history(chat_id, symbol.upper())}


@app.post("/api/market/{chat_id}/trade")
async def api_market_trade(chat_id: int, payload: dict) -> dict:
    require_feature("market")
    return await api_trade(chat_id, payload)


@app.get("/api/predictions")
async def api_predictions(chat_id: int) -> dict:
    require_feature("predictions")
    return {"markets": db.prediction_markets(chat_id)}


@app.post("/api/predictions/create")
async def api_prediction_create(payload: dict) -> dict:
    require_feature("predictions")
    data = tma_user(payload)
    scope = payload.get("scope", "local")
    user_id = int(data["user"]["id"])
    question = str(payload.get("question", "")).strip()
    if len(question) < 8:
        raise HTTPException(status_code=400, detail="Domanda troppo corta.")
    if scope == "global":
        if user_id not in settings.owner_ids:
            raise HTTPException(status_code=403, detail="Solo owner bot.")
        market_row = db.create_prediction_market(None, question, user_id, "global")
    else:
        chat_id = authorized_chat_id(payload, data)
        if not await api_can_manage_chat(chat_id, user_id):
            raise HTTPException(status_code=403, detail="Solo admin gruppo.")
        market_row = db.create_prediction_market(chat_id, question, user_id, "local")
        await redis.publish(f"predictions:{chat_id}", json.dumps({"type": "prediction_created", "marketId": int(market_row["id"])}))
    return {"market": market_row}


@app.post("/api/predictions/{market_id}/buy")
async def api_prediction_buy(market_id: int, payload: dict) -> dict:
    require_feature("predictions")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    try:
        credits = Decimal(str(payload.get("credits", "0")).replace(",", "."))
        trade = db.trade_prediction(market_id, chat_id, user_id, str(payload.get("outcome", "")), credits)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    balance = str(db.credit_balance(chat_id, user_id))
    await redis.publish(f"arcade:{chat_id}:{user_id}", json.dumps({"type": "balance", "balance": balance}))
    await redis.publish(f"predictions:{chat_id}", json.dumps({"type": "prediction_trade", "marketId": market_id}))
    return {"trade": trade, "balance": balance, "markets": db.prediction_markets(chat_id)}


@app.post("/api/predictions/{market_id}/cashout")
async def api_prediction_cashout(market_id: int, payload: dict) -> dict:
    require_feature("predictions")
    data = tma_user(payload)
    chat_id = authorized_chat_id(payload, data)
    user_id = int(data["user"]["id"])
    try:
        shares = Decimal(str(payload.get("shares", "0")).replace(",", "."))
        trade = db.cashout_prediction(market_id, chat_id, user_id, str(payload.get("outcome", "")), shares)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    balance = str(db.credit_balance(chat_id, user_id))
    return {"trade": trade, "balance": balance, "positions": db.prediction_positions(chat_id, user_id), "markets": db.prediction_markets(chat_id)}


@app.post("/api/predictions/{market_id}/resolve")
async def api_prediction_resolve(market_id: int, payload: dict) -> dict:
    require_feature("predictions")
    data = tma_user(payload)
    user_id = int(data["user"]["id"])
    market_row = db.prediction_market(market_id)
    if not market_row:
        raise HTTPException(status_code=404, detail="Mercato non trovato.")
    if market_row["scope"] == "global":
        allowed = user_id in settings.owner_ids
    else:
        allowed = await api_can_manage_chat(int(market_row["chat_id"]), user_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="Non autorizzato.")
    try:
        resolved = db.resolve_prediction(market_id, str(payload.get("outcome", "")), user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved["chat_id"]:
        await redis.publish(f"predictions:{resolved['chat_id']}", json.dumps({"type": "prediction_resolved", "marketId": market_id}))
    return {"market": resolved}


@app.get("/api/predictions/positions")
async def api_prediction_positions(chat_id: int, initData: str = "", session: str = "") -> dict:
    require_feature("predictions")
    try:
        data = validate_auth(initData, session)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid Telegram Mini App data") from exc
    chat_id = authorized_chat_id({"chatId": chat_id}, data)
    return {"positions": db.prediction_positions(chat_id, int(data["user"]["id"]))}


@app.get("/api/place/meta")
async def place_meta() -> dict:
    require_feature("place")
    return await place.meta()


@app.get("/api/place/snapshot")
async def place_snapshot() -> Response:
    require_feature("place")
    try:
        return Response(await place.snapshot(), media_type="application/octet-stream")
    except PlaceError as exc:
        return place_error_response(exc)


@app.get("/api/place/events")
async def place_events(after: int = Query(default=0, ge=0), limit: int = Query(default=5000, ge=1, le=10000)) -> dict:
    require_feature("place")
    return {"events": await place.events_after(after, limit)}


@app.get("/api/place/pixels/{x}/{y}")
async def place_pixel_info(x: int, y: int):
    require_feature("place")
    try:
        return await place.pixel_info(x, y)
    except PlaceError as exc:
        return place_error_response(exc)


@app.post("/api/place/pixels", response_model=None)
async def place_pixels(payload: dict):
    require_feature("place")
    try:
        return await place.place_pixel(
            payload.get("session"),
            int(payload.get("x")),
            int(payload.get("y")),
            int(payload.get("colorId")),
        )
    except (TypeError, ValueError) as exc:
        return place_error_response(PlaceError("invalid_coordinate", "Coordinate o colore non validi."))
    except PlaceError as exc:
        return place_error_response(exc)


@app.websocket("/api/place/ws")
async def place_ws(websocket: WebSocket, session: str | None = None) -> None:
    if not settings.feature_place:
        await websocket.accept()
        await websocket.send_json({"type": "error", "code": "feature_disabled"})
        await websocket.close()
        return
    await websocket.accept()
    await place.ensure_ready()
    await websocket.send_json({"type": "hello", "meta": await place.meta(), "canPlace": bool(session)})
    pubsub = await place.pubsub()

    async def forward_updates() -> None:
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message and message.get("data"):
                    payload = json.loads(message["data"].decode())
                    await websocket.send_json(payload)
                await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(place.channel)
            await pubsub.close()

    async def receive_commands() -> None:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "place":
                continue
            try:
                result = await place.place_pixel(
                    session,
                    int(payload.get("x")),
                    int(payload.get("y")),
                    int(payload.get("colorId")),
                )
                await websocket.send_json({"type": "ack", **result})
            except (TypeError, ValueError):
                await websocket.send_json({"type": "error", "code": "invalid_coordinate", "message": "Coordinate o colore non validi."})
            except PlaceError as exc:
                await websocket.send_json(place_error_payload(exc))

    forward_task = asyncio.create_task(forward_updates())
    receive_task = asyncio.create_task(receive_commands())
    try:
        done, pending = await asyncio.wait({forward_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except WebSocketDisconnect:
        forward_task.cancel()
        receive_task.cancel()


def place_error_payload(exc: PlaceError) -> dict:
    payload = {"type": "error", "code": exc.code, "message": exc.message}
    if exc.retry_after is not None:
        payload["retryAfter"] = exc.retry_after
    return payload


def place_error_response(exc: PlaceError) -> JSONResponse:
    status = {
        "invalid_session": 403,
        "cooldown": 429,
        "invalid_color": 400,
        "invalid_coordinate": 400,
        "server_busy": 503,
    }.get(exc.code, 400)
    return JSONResponse(place_error_payload(exc), status_code=status)


def tma_user(payload: dict) -> dict:
    init_data = payload.get("initData", "")
    if not init_data:
        raise HTTPException(status_code=403, detail="missing authentication")

    try:
        init_payload = validate_init_data(init_data, settings.telegram_bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid Telegram Mini App data") from exc

    session = payload.get("session", "")
    if session:
        try:
            session_data = validate_session(session, settings.telegram_webhook_secret)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="invalid signed session") from exc
        init_user = init_payload.get("user", {}).get("id")
        session_user = session_data.get("user", {}).get("id")
        if init_user is None or session_user is None or str(init_user) != str(session_user):
            raise HTTPException(status_code=403, detail="auth mismatch")
        init_chat = init_payload.get("chat_id") or (init_payload.get("chat") or {}).get("id")
        session_chat = session_data.get("chat_id")
        if init_chat is not None and session_chat is not None and str(init_chat) != str(session_chat):
            raise HTTPException(status_code=403, detail="auth mismatch")

    return init_payload


def validate_auth(init_data: str = "", session: str = "") -> dict:
    return tma_user({"initData": init_data, "session": session})


async def refresh_user_group_memberships(user_id: int, force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    cached = _USER_GROUP_DISCOVERY_CACHE.get(user_id)
    if not force and cached is not None:
        timestamp, cached_rows = cached
        if now - timestamp < _USER_GROUP_DISCOVERY_TTL:
            return cached_rows

    rows = db.user_groups(user_id)
    known = {int(row["chat_id"]) for row in rows}
    known_groups = db.all_groups()
    should_probe = force or len(rows) < len(known_groups)
    if should_probe:
        for row in known_groups:
            candidate_chat_id = int(row["chat_id"])
            if candidate_chat_id in known:
                continue
            try:
                member = await bot.get_chat_member(candidate_chat_id, user_id)
            except Exception:
                continue
            if member.status in {"member", "administrator", "creator", "restricted"}:
                db.touch_user(
                    candidate_chat_id,
                    user_id,
                    None,
                    None,
                    points=0,
                )
                known.add(candidate_chat_id)
    rows = db.user_groups(user_id)
    _USER_GROUP_DISCOVERY_CACHE[user_id] = (now, rows)
    return rows


def authorized_chat_id(payload: dict, auth: dict) -> int:
    requested = payload.get("chatId") or payload.get("chat_id")
    signed_chat = auth.get("chat_id")
    if signed_chat is None and isinstance(auth.get("chat"), dict):
        signed_chat = auth["chat"].get("id")
    if signed_chat is None:
        raise HTTPException(status_code=403, detail="missing authentication")
    user_id = auth.get("user", {}).get("id")
    if requested is None:
        return int(signed_chat)
    try:
        requested_int = int(requested)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="chat_id richiesto") from exc
    if int(signed_chat) != requested_int:
        if user_id is not None and db.user_has_group(int(user_id), requested_int):
            return requested_int
        raise HTTPException(status_code=403, detail="auth mismatch")
    return requested_int


def display_name(user: dict) -> str:
    return " ".join(part for part in [user.get("first_name"), user.get("last_name")] if part)


def history_since(range_value: str) -> __import__("datetime").datetime:
    now = __import__("datetime").datetime.now(UTC)
    return {
        "1h": now - __import__("datetime").timedelta(hours=1),
        "24h": now - __import__("datetime").timedelta(hours=24),
        "7d": now - __import__("datetime").timedelta(days=7),
        "30d": now - __import__("datetime").timedelta(days=30),
    }.get(range_value, now - __import__("datetime").timedelta(hours=24))


async def api_can_manage_chat(chat_id: int, user_id: int) -> bool:
    if user_id in settings.owner_ids:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        logger.exception("failed to verify Telegram admin chat_id=%s user_id=%s", chat_id, user_id)
        return False
    return member.status in {"creator", "administrator"}


def require_feature(name: str) -> None:
    if not features.get(name, False):
        raise HTTPException(status_code=503, detail={"code": "feature_disabled", "feature": name})


@app.get("/app/{path:path}")
async def miniapp(path: str = ""):
    root = Path("miniapp_dist")
    target = root / path
    if path and target.exists() and target.is_file():
        return FileResponse(target)
    return FileResponse(root / "index.html")
