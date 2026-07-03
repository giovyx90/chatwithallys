import asyncio
import hashlib
import hmac
import html
import json
import random
import logging
import re
import time
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, Message, Poll, ReactionTypeEmoji, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from allys.db import Database
from allys.market import MarketService
from allys.memes import (
    MEME_MODE_PROBABILITY,
    desired_media_types,
    media_debug_line,
    meme_caption,
    should_attach_meme,
    tags_for_media,
)
from allys.ollama import OllamaClient
from allys.place import PlaceService
from allys.podcast import PodcastService, parse_podcast_config
from allys.rag import RagMemory
from allys.brain import (
    BOT_AUTHOR,
    HISTORY_TURNS,
    build_system_prompt,
    choose_mode,
    classify_intent,
    format_transcript,
    group_mood,
    response_budget,
)
from allys.media import fetch_working_meme
from allys.sentiment import mood_summary


router = Router()
logger = logging.getLogger("allys.bot")
PODCAST_TASKS: dict[int, asyncio.Task] = {}
_PRIVATE_USER_GROUP_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_PRIVATE_USER_GROUP_CACHE_TTL = 120


class Services:
    db: Database
    rag: RagMemory
    ollama: OllamaClient
    market: MarketService
    podcast: PodcastService
    place: PlaceService
    public_base_url: str
    app_session_secret: str
    predictions_base_url: str
    predictions_session_secret: str
    owner_ids: set[int]
    features: dict[str, bool]
    giphy_api_key: str
    tenor_api_key: str
    meme_reddit_fallback: bool


services = Services()

# Stato in-memory per le risposte proattive: quando Allys ha parlato l'ultima
# volta in una chat e quando ha fatto l'ultima risposta proattiva (anti-spam).
_LAST_ALLYS_REPLY_AT: dict[int, float] = {}
_LAST_PROACTIVE_AT: dict[int, float] = {}
_PROACTIVE_WINDOW_SECONDS = 150
_PROACTIVE_COOLDOWN_SECONDS = 90

# Emoji di reazione mappate all'umore del messaggio (solo emoji ammesse da Telegram).
_POSITIVE_REACTIONS = ["🔥", "👍", "🥰", "😁", "🎉", "🤩", "👏", "💯"]
_NEGATIVE_REACTIONS = ["😭", "😢", "🤨", "🤡", "😱", "👀"]
_NEUTRAL_REACTIONS = ["🤔", "👀", "🗿", "😐"]


def setup_router(
    db: Database,
    rag: RagMemory,
    ollama: OllamaClient,
    market: MarketService,
    podcast: PodcastService,
    place: PlaceService,
    public_base_url: str,
    app_session_secret: str = "",
    predictions_base_url: str = "https://predictions.giovyx-server.it",
    predictions_session_secret: str = "",
    owner_ids: set[int] | None = None,
    features: dict[str, bool] | None = None,
    giphy_api_key: str = "",
    tenor_api_key: str = "",
    meme_reddit_fallback: bool = True,
) -> Router:
    services.db = db
    services.rag = rag
    services.ollama = ollama
    services.market = market
    services.podcast = podcast
    services.place = place
    services.public_base_url = public_base_url
    services.app_session_secret = app_session_secret
    services.predictions_base_url = predictions_base_url.rstrip("/")
    services.predictions_session_secret = predictions_session_secret
    services.owner_ids = owner_ids or set()
    services.features = features or {}
    services.giphy_api_key = giphy_api_key
    services.tenor_api_key = tenor_api_key
    services.meme_reddit_fallback = meme_reddit_fallback
    return router


def add_app_button(builder: InlineKeyboardBuilder, message: Message, text: str, url: str) -> None:
    if message.chat.type == "private":
        builder.button(text=text, web_app=WebAppInfo(url=url))
    else:
        builder.button(text=text, url=url)


def masked_link(url: str, label: str = "clicca qui") -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def _group_label(group: dict[str, Any]) -> str:
    title = group.get("title") or str(group.get("chat_id", ""))
    if not title:
        title = "Gruppo"
    return f"{title} ({group.get('chat_id', 'chat')})"


def _display_name(user: Any) -> str:
    return " ".join(part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part).strip()


async def _discover_user_groups(
    bot: Bot,
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    now = time.time()
    cached = _PRIVATE_USER_GROUP_CACHE.get(user_id)
    if not force and cached is not None:
        ts, rows = cached
        if now - ts < _PRIVATE_USER_GROUP_CACHE_TTL:
            return rows

    known_rows = services.db.user_groups(user_id)
    known_ids = {int(row["chat_id"]) for row in known_rows}
    all_groups = services.db.all_groups()
    if force or len(known_rows) < len(all_groups):
        for row in all_groups:
            chat_id = int(row["chat_id"])
            if chat_id in known_ids:
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
            except Exception:
                continue
            if member.status in {"member", "administrator", "creator", "restricted"}:
                services.db.touch_user(chat_id, user_id, username, display_name, points=0)
                known_ids.add(chat_id)
    rows = services.db.user_groups(user_id)
    _PRIVATE_USER_GROUP_CACHE[user_id] = (now, rows)
    return rows


def app_session_url(message: Message, view: str | None = None, chat_id: int | None = None) -> str:
    target_chat_id = int(chat_id) if chat_id is not None else int(message.chat.id)
    base = f"{services.public_base_url}/app/arcade?chat_id={target_chat_id}"
    if view:
        base += f"&view={view}"
    return base


async def _send_private_miniapp_link(
    message: Message,
    bot: Bot,
    title: str,
    url: str,
    fallback_text: str,
) -> None:
    if message.chat.type == "private":
        builder = InlineKeyboardBuilder()
        builder.button(text=title, web_app=WebAppInfo(url=url))
        await message.answer(f"{title}: {masked_link(url, 'clicca qui per aprirla')}.", parse_mode="HTML", reply_markup=builder.as_markup())
        return
    try:
        builder = InlineKeyboardBuilder()
        builder.button(text=title, web_app=WebAppInfo(url=url))
        await bot.send_message(
            message.from_user.id,
            f"{fallback_text} Aprila dal pulsante qui sotto.",
            reply_markup=builder.as_markup(),
        )
        await message.answer("Ti ho inviato il link in privato.")
    except TelegramForbiddenError:
        await message.answer("Non posso scriverti in privato. Aprimi qui prima in DM con /start e poi riprova.")


def _local_brain_error_text() -> str:
    return "Il cervello locale sta facendo una pausa: riprovo tra poco."


def _match_group_reference(
    groups: list[dict[str, Any]],
    request: str,
) -> dict[str, Any] | None:
    target = (request or "").strip()
    if not target:
        return None
    target_lower = target.lower()
    exact_chat_id = target if target.lstrip("-").isdigit() else None
    if exact_chat_id:
        for group in groups:
            if str(group.get("chat_id")) == exact_chat_id:
                return group
    exact_title = next(
        (group for group in groups if (group.get("title") or "").lower() == target_lower),
        None,
    )
    if exact_title:
        return exact_title
    partial = [
        group
        for group in groups
        if target_lower and target_lower in (group.get("title") or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    return None


@router.message(Command("allys"))
@router.message(Command("start"))
@router.channel_post(Command("allys"))
@router.channel_post(Command("start"))
async def allys_help(message: Message) -> None:
    group = await ensure_context(message)
    quiet = quiet_status(group)
    await message.answer(
        f"Sono Allys. Stato: {quiet or 'attiva'}.\n"
        "Chat intelligente in gruppo/DM: rispondo solo se scrivi Allys o mi scrivi in risposta.\n"
        "Seguo il filo del discorso e mi adatto all'umore del gruppo.\n"
        "Comandi base: /allys, /allys_status, /recap, /mood, /borsa, /portfolio, /prezzo, /azioni, /lavora, /daily, /memoria, /roast_level\n"
        "Comandi giochi: /market (nella Mini App), /arcade, /place, /podcast, /podcast_config, /podcast_now\n"
        "Admin: /allys_off /allys_on /allys_pause 30m, /meme_mode, /azienda_crea, /azienda_approva, /azienda_pausa, /azienda_modifica, /azienda_elimina, /azienda_reset_prezzi"
    )


@router.message(Command("allys_status"))
@router.channel_post(Command("allys_status"))
async def allys_status(message: Message) -> None:
    group = await ensure_context(message)
    await message.answer(f"Stato Allys: {quiet_status(group) or 'attiva'}. Rispondo solo se scrivi Allys o fai reply a un mio messaggio.")


@router.message(Command("allys_off"))
@router.channel_post(Command("allys_off"))
async def allys_off(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono spegnermi.")
        return
    services.db.set_bot_enabled(message.chat.id, False)
    await message.answer("Ok, mi spengo per questo gruppo. Mi riaccendete con /allys_on.")


@router.message(Command("allys_on"))
@router.channel_post(Command("allys_on"))
async def allys_on(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono riaccendermi.")
        return
    services.db.set_bot_enabled(message.chat.id, True)
    await message.answer("Sono di nuovo attiva. Rispondo solo se scrivete Allys o fate reply a un mio messaggio.")


@router.message(Command("allys_pause"))
@router.channel_post(Command("allys_pause"))
async def allys_pause(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono mettermi in pausa.")
        return
    duration = parse_duration(command_arg(message))
    if not duration:
        await message.answer("Uso: /allys_pause 30m | 2h | 1d")
        return
    paused_until = datetime.now(UTC) + duration
    services.db.pause_bot(message.chat.id, paused_until)
    await message.answer(f"Ok, pausa fino a {paused_until.strftime('%Y-%m-%d %H:%M')} UTC. /allys_on per riattivarmi prima.")


@router.message(Command("borsa"))
async def borsa(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not message.from_user:
        await message.answer("Il profilo non è disponibile in questo messaggio.")
        return
    if message.chat.type == "private":
        requested_argument = command_arg(message).strip()
        groups = await _discover_user_groups(
            bot,
            message.from_user.id,
            message.from_user.username,
            _display_name(message.from_user),
            force=True,
        )
        requested_group = _match_group_reference(groups, requested_argument)
        if requested_group:
            url = app_session_url(message, "market", int(requested_group["chat_id"]))
            await _send_private_miniapp_link(
                message,
                bot,
                f"Apri Allys Borsa ({requested_group.get('title') or requested_group.get('chat_id')})",
                url,
                "Ho preparato la tua borsa personale.",
            )
            return
        if requested_argument:
            requested = requested_argument
            if requested.lstrip("-").isdigit():
                numeric_group_id = int(requested)
                try:
                    member = await bot.get_chat_member(numeric_group_id, message.from_user.id)
                except Exception:
                    member = None
                if member and member.status in {"member", "administrator", "creator", "restricted"}:
                    services.db.touch_user(
                        numeric_group_id,
                        message.from_user.id,
                        message.from_user.username,
                        _display_name(message.from_user),
                        points=0,
                    )
                    url = app_session_url(message, "market", numeric_group_id)
                    await _send_private_miniapp_link(
                        message,
                        bot,
                        f"Apri Allys Borsa ({requested})",
                        url,
                        "Ho preparato la tua borsa personale.",
                    )
                    return
            await message.answer(
                "Non ho trovato il gruppo richiesto. Prova con il nome esatto del gruppo o con /borsa e scegli tra i pulsanti."
            )
            return
        if not groups:
            await message.answer(
                "Non vedo gruppi associati a questo profilo. Apri Allys in un gruppo e usa /borsa, oppure passa un gruppo con:"
                "\n/borsa <chat_id> o /borsa <nome gruppo>."
            )
            return
        if len(groups) == 1:
            url = app_session_url(message, "market", int(groups[0]["chat_id"]))
            await _send_private_miniapp_link(
                message,
                bot,
                f"Apri Allys Borsa ({groups[0].get('title') or groups[0].get('chat_id')})",
                url,
                "Ho preparato la tua borsa personale.",
            )
            return
        builder = InlineKeyboardBuilder()
        for group in groups:
            url = app_session_url(message, "market", int(group["chat_id"]))
            add_app_button(builder, message, _group_label(group), url)
        lines = ["Scegli il gruppo dove aprire la Borsa:"]
        for group in groups:
            lines.append(f"- {group.get('title') or group.get('chat_id')} (id: {group['chat_id']})")
        lines.append("Comando rapido: /borsa <chat_id> o /borsa <nome gruppo>.")
        await message.answer("\n".join(lines), reply_markup=builder.as_markup())
        return
    url = app_session_url(message, "market")
    await _send_private_miniapp_link(
        message,
        bot,
        "Apri Allys Borsa",
        url,
        "Ho preparato la tua borsa personale.",
    )


@router.message(Command("azioni"))
async def azioni(message: Message) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    assets = services.db.assets(message.chat.id, include_candidates=True)
    if not assets:
        await message.answer("Nessuna azienda. Admin: /azienda_crea MCFT | Minecraft SpA | minecraft, blocchi")
        return
    lines = ["Aziende del gruppo:"]
    for asset in assets[:20]:
        badge = "quotata" if asset["status"] == "listed" else asset["status"]
        lines.append(f"{asset['symbol']} [{badge}] {asset['name']} - {asset['price']} Crowns")
    await message.answer("\n".join(lines))


@router.message(Command("prezzo"))
async def prezzo(message: Message) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    symbol = command_arg(message).strip().upper()
    if not symbol:
        await message.answer("Uso: /prezzo MEME")
        return
    asset = services.db.asset(message.chat.id, symbol)
    if not asset:
        await message.answer("Azienda non trovata.")
        return
    await message.answer(
        f"{asset['symbol']} - {asset['name']}\n"
        f"Prezzo: {asset['price']} Crowns\n"
        f"Stato: {asset['status']} | rischio manipolazione {asset.get('manipulation_risk', 0)}"
    )


@router.message(Command("lavora"))
async def lavora(message: Message) -> None:
    if not feature("credits"):
        await disabled(message)
        return
    await ensure_context(message)
    result = services.db.work_claim(message.chat.id, message.from_user.id)
    if result["claimed"]:
        await message.answer(f"Hai lavorato durissimo: +25 Crowns. Saldo: {result['balance']}.")
    else:
        await message.answer(f"Hai gia lavorato. Riprova tra {max(1, result['retryAfter'] // 60)} min.")


@router.message(Command("daily"))
async def daily(message: Message) -> None:
    if not feature("credits"):
        await disabled(message)
        return
    await ensure_context(message)
    result = services.db.daily_claim(message.chat.id, message.from_user.id)
    if result["claimed"]:
        await message.answer(f"Daily: +100 Crowns. Saldo: {result['balance']}.")
    else:
        await message.answer("Daily gia riscattato oggi.")


@router.message(Command("arcade"))
async def arcade(message: Message, bot: Bot) -> None:
    if not feature("arcade"):
        await disabled(message)
        return
    await ensure_context(message)
    if not message.from_user:
        await message.answer("Il profilo non è disponibile in questo messaggio.")
        return
    if message.chat.type == "private":
        groups = await _discover_user_groups(
            bot,
            message.from_user.id,
            message.from_user.username,
            _display_name(message.from_user),
        )
        if not groups:
            await message.answer(
                "Non vedo gruppi associati a questo profilo. Entra in un gruppo con Allys e prova /arcade da lì."
            )
            return
        if len(groups) == 1:
            url = app_session_url(message, chat_id=int(groups[0]["chat_id"]))
            await _send_private_miniapp_link(
                message,
                bot,
                f"Apri Allys Arcade ({groups[0].get('title') or groups[0].get('chat_id')})",
                url,
                "Ho preparato Allys Arcade per te.",
            )
            return
        builder = InlineKeyboardBuilder()
        for group in groups:
            url = app_session_url(message, chat_id=int(group["chat_id"]))
            add_app_button(builder, message, _group_label(group), url)
        await message.answer("Scegli il gruppo dove aprire Arcade:", reply_markup=builder.as_markup())
        return
    url = app_session_url(message)
    await _send_private_miniapp_link(
        message,
        bot,
        "Apri Allys Arcade",
        url,
        "Ti ho inviato Allys Arcade in privato.",
    )


@router.message(Command("place"))
async def place(message: Message) -> None:
    if not feature("place"):
        await disabled(message)
        return
    await ensure_context(message)
    if not message.from_user:
        url = f"{services.public_base_url}/app/place"
        await message.answer(f"Minecraft Place: {masked_link(url, 'clicca qui per aprirlo')}.", parse_mode="HTML")
        return
    token = services.place.create_session(
        message.from_user.id,
        message.from_user.username,
        message.chat.id,
    )
    url = f"{services.public_base_url}/app/place?session={token}"
    builder = InlineKeyboardBuilder()
    add_app_button(builder, message, "Apri Place", url)
    await message.answer(f"Minecraft Place: {masked_link(url, 'clicca qui per aprirlo')}. Sessione valida 2 ore, cooldown 30s.", reply_markup=builder.as_markup(), parse_mode="HTML")


@router.channel_post(Command("place"))
async def place_channel(message: Message) -> None:
    if not feature("place"):
        await disabled(message)
        return
    await ensure_context(message)
    url = f"{services.public_base_url}/app/place"
    builder = InlineKeyboardBuilder()
    builder.button(text="Apri Place", url=url)
    await message.answer(f"Minecraft Place viewer-only: {masked_link(url, 'clicca qui per guardarlo')}.", reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(Command("predictions"))
async def predictions(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    lines = [
        "Predictions ora vive nel bot dedicato.",
        "Apri la Mini App: puoi creare mercati locali anche senza admin.",
    ]
    url = predictions_app_url(message)
    builder = InlineKeyboardBuilder()
    add_app_button(builder, message, "Apri Predictions", url)
    lines.append(f"{masked_link(url, 'clicca qui per aprirla')}.")
    await message.answer("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(Command("portfolio"))
async def portfolio(message: Message) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    await message.answer(services.market.portfolio_text(message.chat.id, message.from_user.id))


@router.message(Command("compra"))
@router.message(Command("buy"))
async def buy(message: Message) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    parts = command_arg(message).split()
    if len(parts) != 2:
        await message.answer("Uso: /compra MEME 2")
        return
    await message.answer(services.market.trade_text(message.chat.id, message.from_user.id, "buy", parts[0], parts[1]))


@router.message(Command("vendi"))
@router.message(Command("sell"))
async def sell(message: Message) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    parts = command_arg(message).split()
    if len(parts) != 2:
        await message.answer("Uso: /vendi MEME 2")
        return
    await message.answer(services.market.trade_text(message.chat.id, message.from_user.id, "sell", parts[0], parts[1]))


@router.message(Command("azienda_crea"))
async def azienda_crea(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono creare aziende.")
        return
    parsed = parse_asset_create(command_arg(message))
    if not parsed:
        await message.answer(
            "Creo aziende cosi:\n"
            "/azienda_crea Minecraft SpA | minecraft, blocchi, creeper\n"
            "/azienda_crea MCFT | Minecraft SpA | minecraft, blocchi\n"
            "/azienda_crea Drama Holdings\n\n"
            "Il simbolo lo genero io se non lo scrivi."
        )
        return
    symbol, name, aliases = parsed
    try:
        asset = services.db.create_asset(
            message.chat.id,
            symbol,
            name,
            aliases,
            "listed",
            f"Azienda creata dagli admin su {', '.join(aliases[:3])}.",
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Azienda quotata: {asset['symbol']} - {asset['name']} a {asset['price']} Crowns.")


@router.message(Command("azienda_approva"))
async def azienda_approva(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono approvare aziende.")
        return
    symbol = command_arg(message).strip().upper()
    if not symbol:
        await message.answer("Uso: /azienda_approva MEME")
        return
    try:
        asset = services.db.set_asset_status(message.chat.id, symbol, "listed")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"{asset['symbol']} quotata in borsa.")


@router.message(Command("azienda_pausa"))
async def azienda_pausa(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono mettere in pausa aziende.")
        return
    symbol = command_arg(message).strip().upper()
    if not symbol:
        await message.answer("Uso: /azienda_pausa MEME")
        return
    try:
        asset = services.db.set_asset_status(message.chat.id, symbol, "paused")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"{asset['symbol']} in pausa: niente trade finche non viene riapprovata.")


@router.message(Command("azienda_modifica"))
async def azienda_modifica(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono modificare aziende.")
        return
    parsed = parse_asset_update(command_arg(message))
    if not parsed:
        await message.answer(
            "Uso: /azienda_modifica MEME | Nuovo nome | 1.50 | alias1,alias2 | listed\n"
            "Oppure /azienda_modifica MEME | name=Nuovo nome | price=1.5 | aliases=alias1,alias2 | status=paused | supply_cap=100 | outstanding_shares=50"
        )
        return
    symbol, changes = parsed
    try:
        asset = services.db.update_asset(message.chat.id, symbol, **changes)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    changes_text = ", ".join(f"{key}={value}" for key, value in changes.items())
    await message.answer(f"Azienda aggiornata: {asset['symbol']} ({asset['name']}). Campi: {changes_text}.")


@router.message(Command("azienda_elimina"))
async def azienda_elimina(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono eliminare aziende.")
        return
    symbol = command_arg(message).strip().upper()
    if not symbol:
        await message.answer("Uso: /azienda_elimina MEME")
        return
    try:
        asset = services.db.delete_asset(message.chat.id, symbol)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"{asset['symbol']} - {asset['name']} eliminata.")


@router.message(Command("azienda_reset_prezzi"))
async def azienda_reset_prezzi(message: Message, bot: Bot) -> None:
    if not feature("market"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo admin possono resettare i prezzi.")
        return
    target = command_arg(message).strip().upper()
    if target:
        try:
            updated = services.db.reset_asset_prices(message.chat.id, target)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(f"{target}: prezzo riportato a 1 Crowns.")
        return
    updated = services.db.reset_asset_prices(message.chat.id)
    await message.answer(f"Reset prezzi completato: {updated} aziende a 1 Crowns.")


@router.message(Command("podcast"))
@router.channel_post(Command("podcast"))
async def podcast_status(message: Message) -> None:
    if not feature("podcast"):
        await disabled(message)
        return
    group = await ensure_context(message)
    status = "on" if group["podcast_enabled"] else "off"
    await message.answer(
        f"Podcast: {status}\nFrequenza: {group['podcast_frequency']} {group.get('podcast_day') or ''} {group['podcast_time']}"
    )


@router.message(Command("podcast_config"))
@router.channel_post(Command("podcast_config"))
async def podcast_config(message: Message, bot: Bot) -> None:
    if not feature("podcast"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin del gruppo possono configurare il podcast.")
        return
    arg = command_arg(message)
    parsed = parse_podcast_config(arg)
    if isinstance(parsed, str):
        await message.answer(parsed)
        return
    enabled, frequency, day, time = parsed
    services.db.set_podcast_config(message.chat.id, enabled, frequency, day, time)
    await message.answer("Configurazione podcast aggiornata.")


@router.message(Command("podcast_now"))
@router.channel_post(Command("podcast_now"))
async def podcast_now(message: Message, bot: Bot) -> None:
    if not feature("podcast"):
        await disabled(message)
        return
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin del gruppo possono generare podcast manuali.")
        return
    current = PODCAST_TASKS.get(message.chat.id)
    if current and not current.done():
        current.cancel()
        await message.answer("Ok, cancello la richiesta podcast precedente e tengo l'ultima.")
    elif services.db.has_podcast_today(message.chat.id):
        await message.answer("Podcast gia inviato oggi. Per non spammare ne mando massimo uno al giorno.")
        return
    notice = await message.answer("Avvio podcast. Ti aggiorno qui.")
    task = asyncio.create_task(run_manual_podcast(message.chat.id, notice, bot))
    PODCAST_TASKS[message.chat.id] = task


async def run_manual_podcast(chat_id: int, notice: Message, bot: Bot) -> None:
    async def progress(text: str) -> None:
        try:
            await notice.edit_text(text)
        except Exception:
            logger.info("podcast progress update skipped: %s", text)

    try:
        _, audio_path = await services.podcast.generate(chat_id, progress=progress)
    except asyncio.CancelledError:
        try:
            await notice.edit_text("Podcast annullato: e arrivata una richiesta piu recente.")
        except Exception:
            logger.info("podcast cancel notice skipped for chat_id=%s", chat_id)
        raise
    except Exception:
        logger.exception("failed to generate podcast for chat_id=%s", chat_id)
        await notice.edit_text("Podcast non riuscito: ho avuto un errore durante generazione o audio.")
    else:
        if audio_path:
            await bot.send_audio(chat_id, audio=FSInputFile(audio_path), caption="Podcast fresco di forno.")
            await notice.delete()
        else:
            await notice.edit_text("Podcast generato, ma audio non disponibile.")
    finally:
        if PODCAST_TASKS.get(chat_id) is asyncio.current_task():
            PODCAST_TASKS.pop(chat_id, None)


@router.message(Command("roast_level"))
async def roast_level(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await is_group_admin(bot, message):
        await message.answer("Solo gli admin possono cambiare il livello roast.")
        return
    level = command_arg(message).lower()
    if level not in {"soft", "medium", "chaos"}:
        await message.answer("Uso: /roast_level soft|medium|chaos")
        return
    services.db.set_roast_level(message.chat.id, level)
    await message.answer(f"Roast level impostato a {level}.")


@router.message(Command("memoria"))
async def memoria(message: Message) -> None:
    await ensure_context(message)
    text = command_arg(message)
    if not text:
        await message.answer("Uso: /memoria testo da ricordare")
        return
    row_id = services.db.add_message(message.chat.id, message.from_user.id, message.from_user.username, text, 0)
    await services.rag.remember(message.chat.id, row_id, text, {"manual": True})
    await message.answer("Memoria salvata per questo gruppo.")


@router.message(Command("meme_mode"))
async def meme_mode(message: Message, bot: Bot) -> None:
    group = await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono cambiare meme mode.")
        return
    mode = command_arg(message).lower()
    if mode not in MEME_MODE_PROBABILITY:
        await message.answer(f"Uso: /meme_mode off|low|medium|high\nAttuale: {group.get('meme_mode') or 'medium'}")
        return
    services.db.set_meme_mode(message.chat.id, mode)
    await message.answer(f"Meme mode impostata a {mode}.")


@router.message(Command("meme_stats"))
async def meme_stats(message: Message) -> None:
    await ensure_context(message)
    stats = services.db.chat_media_stats(message.chat.id)
    lines = [f"Media meme indicizzati: {stats['total']}"]
    for row in stats["by_type"]:
        lines.append(f"- {row['media_type']}: {row['count']}")
    await message.answer("\n".join(lines))


@router.message(Command("meme_clear"))
async def meme_clear(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono svuotare i media meme.")
        return
    count = services.db.clear_chat_media(message.chat.id)
    await message.answer(f"Archivio meme svuotato: {count} media rimossi.")


@router.message(Command("meme_test"))
async def meme_test(message: Message, bot: Bot) -> None:
    await ensure_context(message)
    if not await can_manage_chat(bot, message):
        await message.answer("Solo gli admin possono testare la ricerca meme.")
        return
    query = command_arg(message)
    if not query:
        await message.answer("Uso: /meme_test testo da cercare")
        return
    rows = services.db.search_chat_media(message.chat.id, query, limit=5)
    lines = ["Risultati meme (media del gruppo):"]
    lines.extend(media_debug_line(row) for row in rows)
    if not rows:
        lines.append("Nessun media del gruppo per questa query.")
    remote = None
    try:
        remote = await fetch_working_meme(
            query,
            giphy_api_key=getattr(services, "giphy_api_key", ""),
            tenor_api_key=getattr(services, "tenor_api_key", ""),
            reddit_fallback=getattr(services, "meme_reddit_fallback", True),
        )
    except Exception:
        remote = None
    if remote:
        lines.append(f"Fallback online (validato, {remote.media_type}): {remote.url}")
    else:
        lines.append("Fallback online: nessun media valido trovato adesso.")
    await message.answer("\n".join(lines))


@router.message(Command("recap"))
@router.channel_post(Command("recap"))
async def recap(message: Message) -> None:
    await ensure_context(message)
    recent = [row for row in services.db.recent_messages(message.chat.id, limit=60) if (row.get("text") or "").strip()]
    if len(recent) < 4:
        await message.answer("Non c'e ancora abbastanza da riassumere. Scrivete un po' e poi richiamatemi con /recap.")
        return
    transcript = format_transcript(recent, limit=50)
    notice = await message.answer("Sto ripassando la conversazione...")
    try:
        summary = await services.ollama.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Sei Allys. Riassumi in italiano cosa si e detto nella chat in massimo 5 punti "
                        "brevi, vivaci e ironici quando serve. Niente nomi propri o username: usa '@/'. "
                        "Niente markdown pesante, al massimo dei trattini a inizio riga."
                    ),
                },
                {"role": "user", "content": f"Trascrizione anonimizzata:\n{transcript}\n\nFammi un recap sveglio di cosa mi sono persa."},
            ],
            num_predict=340,
            temperature=0.6,
        )
    except Exception:
        logger.exception("failed to build recap for chat_id=%s", message.chat.id)
        await notice.edit_text("Non sono riuscita a fare il recap, riprovo tra poco.")
        return
    text = sanitize_mentions((summary or "").strip())[:1500] or "Recap non disponibile."
    await notice.edit_text(text)


@router.message(Command("mood"))
@router.channel_post(Command("mood"))
async def mood_cmd(message: Message) -> None:
    await ensure_context(message)
    recent = services.db.recent_messages(message.chat.id, limit=40)
    scores = []
    for row in recent:
        value = row.get("sentiment")
        if value is None:
            continue
        try:
            scores.append(float(value))
        except (TypeError, ValueError):
            continue
    summary = mood_summary(scores)
    if summary["label"] == "silenzio":
        await message.answer("Silenzio totale: non ho abbastanza messaggi recenti per leggere l'umore.")
        return
    face = {
        "carico e positivo": "🔥",
        "sereno": "🙂",
        "neutro": "😐",
        "un po' giu": "🌧",
        "teso": "⚡",
    }.get(str(summary["label"]), "😐")
    await message.answer(
        f"Umore del gruppo: {summary['label']} {face}\n"
        f"Media sentiment {summary['average']} · energia {summary['energy']} (ultimi {len(scores)} messaggi)."
    )


@router.message(F.poll)
async def poll_comment(message: Message) -> None:
    group = await ensure_context(message)
    if is_quiet(group):
        return
    poll: Poll = message.poll
    if not poll.options:
        return
    option = random.choice(poll.options).text
    await message.answer(shorten_reply(f"Scelgo {sanitize_mentions(option)}", max_chars=120))


@router.message(F.animation | F.video | F.video_note | F.document)
async def media_message(message: Message, bot: Bot) -> None:
    group = await ensure_context(message)
    await remember_chat_media(message)
    caption = message.caption or ""
    if caption:
        sentiment = await services.market.process_user_message(message.chat.id, message.from_user.id if message.from_user else None, caption)
        row_id = services.db.add_message(message.chat.id, message.from_user.id if message.from_user else None, message.from_user.username if message.from_user else None, caption, sentiment)
        if should_remember(caption) and message.from_user:
            await services.rag.remember(message.chat.id, row_id, caption, {"username": message.from_user.username, "media": True})
        if caption and not is_quiet(group) and await should_reply(message, bot, group):
            try:
                reply = await build_reply(message, group)
                await send_allys_reply(message, group, reply)
            except Exception:
                logger.exception("failed to build media AI reply for chat_id=%s", message.chat.id)
                await message.answer(_local_brain_error_text())


@router.message(F.text)
async def group_text(message: Message, bot: Bot) -> None:
    group = await ensure_context(message)
    text = message.text or ""
    if feature("credits") and message.from_user and len(text.strip()) >= 2:
        services.db.farm_message_credits(message.chat.id, message.from_user.id)
    sentiment = await services.market.process_user_message(message.chat.id, message.from_user.id if message.from_user else None, text)
    row_id = services.db.add_message(message.chat.id, message.from_user.id, message.from_user.username, text, sentiment)
    if should_remember(text):
        await services.rag.remember(message.chat.id, row_id, text, {"username": message.from_user.username})
    replied = False
    if not is_quiet(group) and await should_reply(message, bot, group):
        try:
            reply = await build_reply(message, group)
            await send_allys_reply(message, group, reply)
            replied = True
        except Exception:
            logger.exception("failed to build AI reply for chat_id=%s", message.chat.id)
            await message.answer(_local_brain_error_text())
    if not replied:
        await maybe_react(message, bot, group, sentiment)
    if message.from_user:
        try:
            if services.db.member_profile_due(message.chat.id, message.from_user.id):
                asyncio.create_task(maybe_update_member_profile(message.chat.id, message.from_user.id))
        except Exception:
            logger.info("member profile due-check failed for chat_id=%s", message.chat.id)


@router.channel_post(F.text)
async def channel_text(message: Message, bot: Bot) -> None:
    group = await ensure_context(message)
    text = message.text or ""
    sentiment = await services.market.process_message(message.chat.id, text)
    row_id = services.db.add_message(message.chat.id, None, None, text, sentiment)
    if should_remember(text):
        await services.rag.remember(message.chat.id, row_id, text, {"channel": True})
    if not is_quiet(group) and text and "allys" in text.lower():
        try:
            reply = await build_reply(message, group)
            await send_allys_reply(message, group, reply)
        except Exception:
            logger.exception("failed to build channel AI reply for chat_id=%s", message.chat.id)
            await message.answer(_local_brain_error_text())


async def ensure_context(message: Message) -> dict[str, Any]:
    group = services.db.ensure_group(message.chat.id, message.chat.title)
    if message.from_user:
        display_name = " ".join(part for part in [message.from_user.first_name, message.from_user.last_name] if part)
        services.db.touch_user(message.chat.id, message.from_user.id, message.from_user.username, display_name)
    return group


async def should_reply(message: Message, bot: Bot, group: dict[str, Any]) -> bool:
    me = await bot.me()
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id == me.id:
            return True
    if message.text and "allys" in message.text.lower():
        return True
    # Risposta proattiva: se Allys ha parlato da poco in questa chat e arriva un
    # follow-up (una domanda), interviene anche senza essere chiamata per nome,
    # con una finestra temporale e un cooldown anti-spam.
    now = time.time()
    last_allys = _LAST_ALLYS_REPLY_AT.get(message.chat.id, 0.0)
    last_proactive = _LAST_PROACTIVE_AT.get(message.chat.id, 0.0)
    if (now - last_allys) <= _PROACTIVE_WINDOW_SECONDS and (now - last_proactive) >= _PROACTIVE_COOLDOWN_SECONDS:
        if _looks_like_followup(message.text or ""):
            _LAST_PROACTIVE_AT[message.chat.id] = now
            return True
    return False


async def build_reply(message: Message, group: dict[str, Any]) -> str:
    text = message.text or message.caption or ""
    chat_id = message.chat.id
    roast_level = group.get("roast_level", "medium")

    # Contesto vivo: gli ultimi messaggi (il corrente e gia salvato ed e l'ultimo).
    recent = services.db.recent_messages(chat_id, limit=HISTORY_TURNS + 2)
    history = recent[:-1] if recent else []
    mood = group_mood(recent)
    mood_label = str(mood.get("label", "neutro"))

    intent = classify_intent(text)
    serious_minigame = is_minigame_query(text) or intent == "minigame"
    if serious_minigame:
        intent = "minigame"
    mode = choose_mode(intent, roast_level, mood_label)

    docs = await services.rag.search(chat_id, text)
    memory = "\n".join(f"- {sanitize_mentions(doc.get('text') or '')}" for doc in docs[:3])
    minigame_ctx = build_minigame_context(message) if serious_minigame else ""
    system = build_system_prompt(mode, roast_level, mood_label, minigame_ctx)

    profile = ""
    if message.from_user:
        try:
            profile = services.db.get_member_profile(chat_id, message.from_user.id)
        except Exception:
            profile = ""

    transcript = format_transcript(history, limit=HISTORY_TURNS)
    user_parts: list[str] = []
    if memory:
        user_parts.append(f"Cose che ricordi di questo gruppo:\n{memory}")
    if profile:
        user_parts.append(f"Cosa sai di chi ti scrive (uso interno, non citare nomi ne dati personali):\n{profile}")
    if transcript:
        user_parts.append(f"Come si sta svolgendo la conversazione (anonimizzata):\n{transcript}")
    user_parts.append(f"Rispondi a quest'ultimo messaggio, restando nel filo del discorso:\n{sanitize_mentions(text)}")

    reply = await services.ollama.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        num_predict=response_budget(intent),
        temperature=0.68 if mode == "helpful" else 0.88,
    )
    max_chars = 420 if intent in {"help", "question", "minigame"} else 300
    return shorten_reply(sanitize_mentions(reply), max_chars=max_chars)


def is_minigame_query(text: str) -> bool:
    lowered = (text or "").lower()
    markers = [
        "borsa", "azioni", "azienda", "aziende", "crowns", "corone", "portfolio", "compra", "vendi",
        "prezzo", "minigio", "arcade", "place", "podcast", "prediction", "consigli", "invest",
    ]
    return any(marker in lowered for marker in markers)


def build_minigame_context(message: Message) -> str:
    lines = [
        "Valuta: Crowns.",
        "Guadagno: /lavora +25 ogni ora, /daily +100 ogni 24h, messaggi validi con cap anti-spam.",
        "Regola borsa: se il gruppo parla davvero di un tema/azienda, il prezzo tende a salire; spam e ripetizioni dello stesso utente valgono poco.",
        "Borsa: /borsa apre dashboard, /azioni lista aziende, /prezzo SYMBOL, /compra SYMBOL quantita, /vendi SYMBOL quantita, /portfolio.",
        "Place: /place apre canvas Minecraft globale 1000x1000 con cooldown 30s.",
        "Podcast: /podcast, /podcast_now, /podcast_config per admin.",
        "Predictions: /predictions apre il bot/prodotto dedicato.",
    ]
    if feature("market"):
        try:
            assets = services.db.assets(message.chat.id, include_candidates=True)
            listed = [asset for asset in assets if asset["status"] == "listed"][:8]
            candidates = [asset for asset in assets if asset["status"] == "candidate"][:5]
            if listed:
                lines.append(
                    "Aziende quotate: "
                    + "; ".join(
                        f"{asset['symbol']} {asset['name']} prezzo {asset['price']} rischio {asset.get('manipulation_risk', 0)}"
                        for asset in listed
                    )
                )
            if candidates:
                lines.append("Candidate admin: " + "; ".join(f"{asset['symbol']} {asset['name']}" for asset in candidates))
            if message.from_user:
                portfolio = services.db.user_portfolio(message.chat.id, message.from_user.id)
                holdings = portfolio.get("holdings") or []
                lines.append(f"Saldo utente: {portfolio.get('crowns', '0')} Crowns.")
                if holdings:
                    lines.append(
                        "Portfolio utente: "
                        + "; ".join(
                            f"{item['symbol']} qty {item['quantity']} avg {item['avg_price']} prezzo {item['price']} pnl {item['pnl']}"
                            for item in holdings[:8]
                        )
                    )
        except Exception:
            logger.exception("failed to build minigame context for chat_id=%s", message.chat.id)
            lines.append("Borsa: dati temporaneamente non disponibili.")
    return "\n".join(lines)


async def send_allys_reply(message: Message, group: dict[str, Any], reply: str) -> None:
    try:
        await _deliver_allys_reply(message, group, reply)
    finally:
        _remember_allys_message(message.chat.id, reply)


def _remember_allys_message(chat_id: int, reply: str) -> None:
    """Salva la risposta di Allys tra i messaggi, cosi la cronologia e un vero
    botta-e-risposta e le repliche successive tengono conto di cosa ha detto."""
    text = (reply or "").strip()
    if not text:
        return
    try:
        services.db.add_message(chat_id, None, BOT_AUTHOR, text, 0)
        _LAST_ALLYS_REPLY_AT[chat_id] = time.time()
    except Exception:
        logger.info("could not persist Allys reply for chat_id=%s", chat_id)


async def _deliver_allys_reply(message: Message, group: dict[str, Any], reply: str) -> None:
    prompt = message.text or message.caption or ""
    if not should_attach_meme(group.get("meme_mode") or "medium", prompt, reply):
        await message.answer(reply)
        return
    query = f"{prompt} {reply}"
    media_types = desired_media_types(prompt)
    rows = services.db.search_chat_media(message.chat.id, query, media_types=media_types, limit=5)
    if rows:
        row = random.choice(rows[:3])
        caption = meme_caption(reply)
        try:
            if row["media_type"] in {"animation", "gif_document"}:
                await message.answer_animation(row["file_id"], caption=caption)
            elif row["media_type"] == "video_note":
                await message.answer_video_note(row["file_id"])
            elif row["media_type"] in {"video", "video_document"}:
                await message.answer_video(row["file_id"], caption=caption)
            else:
                await message.answer_document(row["file_id"], caption=caption)
            services.db.mark_chat_media_used(int(row["id"]))
            return
        except TelegramBadRequest:
            logger.info("disabling invalid meme media id=%s chat_id=%s", row["id"], message.chat.id)
            services.db.disable_chat_media(int(row["id"]))
        except Exception:
            logger.exception("failed to send meme media id=%s chat_id=%s", row["id"], message.chat.id)

    # Fallback su sorgente reale (Giphy/Tenor/Reddit) con URL VALIDATO: se nulla
    # e utilizzabile ripieghiamo sul testo, senza mai mandare un media rotto.
    remote = None
    try:
        remote = await fetch_working_meme(
            query,
            giphy_api_key=getattr(services, "giphy_api_key", ""),
            tenor_api_key=getattr(services, "tenor_api_key", ""),
            reddit_fallback=getattr(services, "meme_reddit_fallback", True),
        )
    except Exception:
        logger.info("working meme fetch failed for chat_id=%s", message.chat.id)
    if remote:
        caption = meme_caption(reply)
        try:
            if remote.media_type == "gif":
                await message.answer_animation(remote.url, caption=caption)
            elif remote.media_type == "video":
                await message.answer_video(remote.url, caption=caption)
            else:
                await message.answer_photo(remote.url, caption=caption)
            return
        except Exception:
            logger.info("validated meme url still rejected by Telegram, falling back to text")
    await message.answer(reply)


async def maybe_react(message: Message, bot: Bot, group: dict[str, Any], sentiment: float) -> None:
    """Ogni tanto Allys reagisce con un'emoji invece di rispondere: vivace e leggero."""
    if is_quiet(group) or not message.from_user:
        return
    if (group.get("meme_mode") or "medium") == "off":
        return
    magnitude = abs(sentiment)
    probability = 0.04
    if magnitude >= 1.0:
        probability = 0.13
    if magnitude >= 2.0:
        probability = 0.22
    if random.random() > probability:
        return
    if sentiment >= 0.5:
        emoji = random.choice(_POSITIVE_REACTIONS)
    elif sentiment <= -0.5:
        emoji = random.choice(_NEGATIVE_REACTIONS)
    else:
        emoji = random.choice(_NEUTRAL_REACTIONS)
    try:
        await bot.set_message_reaction(message.chat.id, message.message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
    except Exception:
        logger.info("could not set reaction for chat_id=%s", message.chat.id)


async def maybe_update_member_profile(chat_id: int, user_id: int) -> None:
    """Aggiorna in background un profilo breve del membro (memoria a lungo termine).

    Estrae solo interessi/tono/tormentoni, MAI nomi o dati personali sensibili.
    """
    try:
        if not services.db.member_profile_due(chat_id, user_id):
            return
        texts = services.db.member_recent_texts(chat_id, user_id, limit=40)
        if len(texts) < 6:
            return
        joined = "\n".join(f"- {sanitize_mentions(text)[:200]}" for text in texts if text.strip())
        profile = await services.ollama.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Estrai un profilo BREVE (max 4 righe, un punto per riga) di un utente di chat "
                        "a partire dai suoi messaggi: interessi, tono, tormentoni, argomenti ricorrenti. "
                        "NON includere nomi propri, username o dati personali/sensibili (salute, religione, "
                        "politica, orientamento, indirizzi). Solo aspetti utili a conversare. Italiano, niente markdown."
                    ),
                },
                {"role": "user", "content": f"Messaggi recenti:\n{joined}\n\nProfilo:"},
            ],
            num_predict=170,
            temperature=0.4,
        )
        profile = sanitize_mentions((profile or "").strip())
        if profile:
            services.db.set_member_profile(chat_id, user_id, profile)
    except Exception:
        logger.info("member profile update failed for chat_id=%s user_id=%s", chat_id, user_id)


def _looks_like_followup(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return False
    if not (3 <= len(stripped) <= 220):
        return False
    return "?" in stripped or classify_intent(stripped) in {"question", "help"}


async def remember_chat_media(message: Message) -> None:
    media_type = None
    file_id = None
    file_unique_id = None
    if message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
        file_unique_id = message.animation.file_unique_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
    elif message.video_note:
        media_type = "video_note"
        file_id = message.video_note.file_id
        file_unique_id = message.video_note.file_unique_id
    elif message.document:
        mime = message.document.mime_type or ""
        name = (message.document.file_name or "").lower()
        if mime == "image/gif" or name.endswith(".gif"):
            media_type = "gif_document"
        elif mime.startswith("video/") or name.endswith((".mp4", ".webm", ".mov")):
            media_type = "video_document"
        if media_type:
            file_id = message.document.file_id
            file_unique_id = message.document.file_unique_id
    if not media_type or not file_id:
        return
    caption = message.caption or ""
    services.db.add_chat_media(
        message.chat.id,
        message.message_id,
        media_type,
        file_id,
        file_unique_id,
        sanitize_mentions(caption) if caption else None,
        tags_for_media(caption),
        message.from_user.username if message.from_user else None,
    )


def should_remember(text: str) -> bool:
    return len(text) > 40 or any(marker in text.lower() for marker in ["ricorda", "storico", "mai dimenticare"])


def sanitize_mentions(text: str) -> str:
    return re.sub(r"@[A-Za-z0-9_]{2,32}", "@/", text)


def shorten_reply(text: str, max_chars: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    clipped = compact[:max_chars].rsplit(" ", 1)[0].rstrip(".,;: ")
    return f"{clipped}..."


def command_arg(message: Message) -> str:
    return (message.text or "").partition(" ")[2].strip()


def parse_asset_create(arg: str) -> tuple[str, str, list[str]] | None:
    raw = (arg or "").strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], split_aliases(" ".join(parts[2:]))
    if len(parts) == 2:
        if re.fullmatch(r"[A-Za-z0-9]{2,8}", parts[0]):
            return parts[0], parts[1], split_aliases(parts[1])
        return "", parts[0], split_aliases(parts[1])
    aliases = split_aliases(parts[0])
    if "," in parts[0] and aliases:
        return "", f"{aliases[0].capitalize()} Holdings", aliases
    return "", parts[0], aliases


def parse_asset_update(arg: str) -> tuple[str, dict[str, Any]] | None:
    raw = (arg or "").strip()
    if not raw:
        return None

    match = re.match(r"^\s*([A-Za-z0-9]{2,10})\s*(?:\|\s*|\s+)(.*)$", raw)
    if not match:
        return None

    symbol = match.group(1).upper()
    tail = (match.group(2) or "").strip()
    if not tail:
        return None

    updates: dict[str, Any] = {}
    key_values: dict[str, str] = {}

    for chunk in [part.strip() for part in tail.split("|") if part.strip()]:
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            key_values[key.strip().lower()] = value.strip()

    if key_values:
        if "name" in key_values or "nome" in key_values:
            updates["name"] = key_values.get("name") or key_values.get("nome")
        if "description" in key_values:
            updates["description"] = key_values["description"]
        if "descr" in key_values:
            updates["description"] = key_values["descr"]
        if "theme" in key_values or "tema" in key_values:
            updates["theme"] = key_values.get("theme") or key_values.get("tema")
        if "aliases" in key_values or "alias" in key_values:
            values = key_values.get("aliases") or key_values.get("alias") or ""
            updates["aliases"] = split_aliases(values)
        if "status" in key_values or "stato" in key_values:
            updates["status"] = key_values.get("status") or key_values.get("stato")
        if "price" in key_values or "prezzo" in key_values:
            price = key_values.get("price") or key_values.get("prezzo")
            if price:
                updates["price"] = price
        if "supply_cap" in key_values or "cap" in key_values:
            cap = key_values.get("supply_cap") or key_values.get("cap")
            if cap:
                updates["supply_cap"] = cap
        if "outstanding" in key_values or "outstanding_shares" in key_values:
            outstanding = key_values.get("outstanding") or key_values.get("outstanding_shares")
            if outstanding:
                updates["outstanding_shares"] = outstanding

    if not key_values:
        chunks = [part.strip() for part in tail.split("|")]
        if len(chunks) > 0 and chunks[0]:
            updates["name"] = chunks[0]
        if len(chunks) > 1 and chunks[1]:
            updates["price"] = chunks[1]
        if len(chunks) > 2 and chunks[2]:
            updates["aliases"] = split_aliases(chunks[2])
        if len(chunks) > 3 and chunks[3]:
            updates["status"] = chunks[3]
        if len(chunks) > 4 and chunks[4]:
            updates["supply_cap"] = chunks[4]
        if len(chunks) > 5 and chunks[5]:
            updates["outstanding_shares"] = chunks[5]

    return (symbol, updates) if updates else None


def split_aliases(value: str) -> list[str]:
    aliases = [item.strip().lower() for item in re.split(r"[,;]+", value or "") if item.strip()]
    if len(aliases) <= 1:
        aliases = [item.strip().lower() for item in re.findall(r"[a-zA-Z0-9_àèéìòù]{3,}", value or "")]
    seen: list[str] = []
    for alias in aliases:
        if alias not in seen:
            seen.append(alias)
    return seen[:12]


def predictions_app_url(message: Message) -> str:
    base = f"{services.predictions_base_url}/app?chat_id={message.chat.id}"
    if not message.from_user or not services.predictions_session_secret:
        return base
    payload = {
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "exp": int(time.time()) + 7 * 86400,
    }
    body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    sig = hmac.new(services.predictions_session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{base}&session={body}.{sig}"


def is_quiet(group: dict[str, Any]) -> bool:
    return quiet_status(group) is not None


def quiet_status(group: dict[str, Any]) -> str | None:
    if not group.get("bot_enabled", True):
        return "spenta"
    paused_until = group.get("paused_until")
    if paused_until and paused_until > datetime.now(UTC):
        return f"in pausa fino a {paused_until.strftime('%Y-%m-%d %H:%M')} UTC"
    if paused_until:
        services.db.clear_bot_pause(int(group["chat_id"]))
    return None


def parse_duration(value: str) -> timedelta | None:
    match = re.fullmatch(r"\s*(\d{1,4})\s*([mhd])\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        return None
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


async def is_group_admin(bot: Bot, message: Message) -> bool:
    if not message.from_user:
        return False
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in {"creator", "administrator"}


async def can_manage_chat(bot: Bot, message: Message) -> bool:
    if message.chat.type == "channel":
        return True
    return await is_group_admin(bot, message)


def build_dispatcher(router_: Router) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router_)
    return dispatcher


def feature(name: str) -> bool:
    return bool(getattr(services, "features", {}).get(name, False))


async def disabled(message: Message) -> None:
    await message.answer("Funzione temporaneamente disattivata.")
