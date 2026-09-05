"""Prova del cablaggio: i flussi nuovi girano davvero, con stub al posto di
Postgres e Telegram.

I test puri (learning, autonomy, charts) coprono le decisioni; qui si controlla
che ``bot.py`` chiami le cose giuste nell'ordine giusto: il silenzio arriva al
database, i voti finiscono sulla risposta giusta, le immagini partono e
l'autonomia si ferma quando deve.
"""

from __future__ import annotations

import asyncio
import types
from datetime import UTC, datetime, timedelta

import pytest

import allys.bot as bot_module


class FakeDB:
    def __init__(self) -> None:
        self.quiet: tuple | None = None
        self.scores: dict[int, float] = {}
        self.recorded: list[tuple] = []
        self.group = {
            "chat_id": -100, "title": "Gruppo Test", "bot_enabled": True,
            "roast_level": "medium", "meme_mode": "off", "autonomy_level": "alta",
            "quiet_until": None, "paused_until": None,
        }

    def group_settings(self, chat_id): return dict(self.group)
    def ensure_group(self, chat_id, title=None): return dict(self.group)
    def touch_user(self, *a, **k): pass
    def add_message(self, *a, **k): return 1

    def set_quiet(self, chat_id, until, by):
        self.quiet = (until, by)
        self.group["quiet_until"] = until
        self.group["quiet_by"] = by

    def clear_quiet(self, chat_id):
        self.quiet = None
        self.group["quiet_until"] = None

    def record_allys_reply(self, chat_id, message_id, prompt, reply, mode="", intent="", spontaneous=False):
        self.recorded.append((message_id, reply, intent, spontaneous))
        self.scores[message_id] = 0.0

    def bump_reply_score(self, chat_id, message_id, delta, signal):
        if message_id not in self.scores:
            return None
        self.scores[message_id] += delta
        return self.scores[message_id]

    def best_reply_examples(self, chat_id, limit=3, days=60):
        return [{"prompt_text": "che si fa", "reply_text": "niente, come sempre", "score": 2.0}]

    def lexicon_messages(self, chat_id, limit=400):
        return [{"username": u, "text": "skill issue clamoroso"} for u in ("a", "b", "c")]

    def reply_feedback_stats(self, chat_id, days=21):
        return {"samples": 30, "average": -0.9, "total": 40}

    def recent_messages(self, chat_id, limit=60):
        return [{"username": "mario", "text": "ma quindi stasera?", "sentiment": 0.4}]

    def chat_activity(self, chat_id, window_hours=6):
        return {"new_messages": 40, "unique_speakers": 5, "energy": 2.0, "pending_question": True,
                "seconds_since_last_human": 30.0, "seconds_since_last_allys": 99_999.0}

    def market_movers(self, chat_id, hours=3, limit=3):
        return [{"symbol": "DRAMA", "name": "Drama Holdings", "price": 14.2,
                 "change_pct": 0.31, "history": [10, 14.2]}]

    def board_rows(self, chat_id, hours=24, points=28):
        return [{"symbol": "DRAMA", "name": "Drama Holdings", "price": 14.2,
                 "change_pct": 0.31, "history": [10, 12, 14.2]}]

    def asset_card(self, chat_id, symbol, hours=48, points=60):
        return {"asset": {"symbol": symbol, "name": "Drama Holdings", "price": 14.2, "change_pct": 0.31},
                "history": [10, 12, 14.2],
                "stats": {"mentions": 5, "unique_users": 3, "volume": 12, "manipulation_risk": 0.1}}

    def spontaneous_today(self, chat_id): return 0


class FakeOllama:
    def __init__(self) -> None:
        self.last_system = ""

    async def chat(self, messages, num_predict=56, temperature=0.75):
        self.last_system = messages[0]["content"]
        return "Comunque quella pizzeria fa schifo, cambiamo."


class FakeMessage:
    def __init__(self, text="", chat_id=-100, reply_to=None, chat_type="supergroup"):
        self.text = text
        self.caption = None
        self.message_id = 500
        self.chat = types.SimpleNamespace(id=chat_id, type=chat_type, title="Gruppo Test")
        self.from_user = types.SimpleNamespace(id=7, username="mario", first_name="Mario", last_name=None)
        self.reply_to_message = reply_to
        self.sent: list[tuple] = []

    async def answer(self, text, **kwargs):
        self.sent.append(("text", text))
        return FakeMessage(text)

    async def answer_photo(self, photo, caption=None, **kwargs):
        self.sent.append(("photo", len(photo.data), caption))
        return FakeMessage(caption or "")


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def me(self):
        return types.SimpleNamespace(id=999)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(("text", text))
        return FakeMessage(text)

    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        self.sent.append(("photo", len(photo.data), caption))
        return FakeMessage(caption or "")


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()
    ollama = FakeOllama()
    monkeypatch.setattr(bot_module.services, "db", db, raising=False)
    monkeypatch.setattr(bot_module.services, "ollama", ollama, raising=False)
    monkeypatch.setattr(bot_module.services, "public_base_url", "https://allys.test", raising=False)
    monkeypatch.setattr(bot_module.services, "features", {"market": True}, raising=False)
    bot_module._STYLE_CACHE.clear()
    bot_module._TONE_CACHE.clear()
    bot_module._LAST_ALLYS_MESSAGE_ID.clear()
    return types.SimpleNamespace(db=db, ollama=ollama, bot=FakeBot())


def test_style_block_porta_lessico_ed_esempi_nel_prompt(wired) -> None:
    block = bot_module.style_block(-100)
    assert "skill issue" in block
    assert "niente, come sempre" in block


def test_tono_si_accorcia_se_il_gruppo_e_scontento(wired) -> None:
    assert bot_module.tone_for(-100)["length_factor"] < 1.0


async def test_zitta_a_voce_silenzia_e_vale_come_voto_negativo(wired) -> None:
    bot_module._LAST_ALLYS_MESSAGE_ID[-100] = 42
    wired.db.scores[42] = 0.0
    message = FakeMessage("allys stai zitta")

    handled = await bot_module.handle_shutup_request(message, wired.bot, message.text)

    assert handled is True
    assert wired.db.quiet is not None
    assert wired.db.scores[42] < 0
    assert message.sent and "sparisco" in message.sent[0][1]


async def test_che_palle_non_silenzia(wired) -> None:
    message = FakeMessage("allys che palle")
    assert await bot_module.handle_shutup_request(message, wired.bot, message.text) is False
    assert wired.db.quiet is None


async def test_lo_zitta_di_un_altro_bot_non_la_riguarda(wired) -> None:
    message = FakeMessage("state zitti un attimo")
    assert await bot_module.handle_shutup_request(message, wired.bot, message.text) is False


def test_quiet_status_riporta_il_silenzio_dei_membri(wired) -> None:
    wired.db.set_quiet(-100, datetime.now(UTC) + timedelta(minutes=30), "Mario")
    stato = bot_module.quiet_status(wired.db.group_settings(-100))
    assert stato and "zitta fino a" in stato and "Mario" in stato


async def test_una_risposta_ad_allys_diventa_un_voto(wired) -> None:
    wired.db.scores[42] = 0.0
    replied = types.SimpleNamespace(from_user=types.SimpleNamespace(id=999), message_id=42)

    await bot_module.learn_from_reply(FakeMessage("ahahah grande", reply_to=replied), wired.bot, "ahahah grande")

    assert wired.db.scores[42] > 0


async def test_una_risposta_a_un_umano_non_conta(wired) -> None:
    wired.db.scores[42] = 0.0
    replied = types.SimpleNamespace(from_user=types.SimpleNamespace(id=123), message_id=42)

    await bot_module.learn_from_reply(FakeMessage("ahahah grande", reply_to=replied), wired.bot, "ahahah grande")

    assert wired.db.scores[42] == 0.0


async def test_il_listino_parte_come_immagine(wired) -> None:
    message = FakeMessage("/borsa")
    assert await bot_module.send_board_image(message, -100, "Gruppo Test") is True
    kind, size, _caption = message.sent[0]
    assert kind == "photo" and size > 5_000


async def test_la_scheda_titolo_porta_la_ricevuta_come_didascalia(wired) -> None:
    message = FakeMessage("/compra DRAMA 2")
    assert await bot_module.send_asset_image(message, -100, "DRAMA", caption="Comprato 2 DRAMA.") is True
    assert message.sent[0][2] == "Comprato 2 DRAMA."


async def test_parla_da_sola_e_archivia_l_intervento(wired) -> None:
    assert await bot_module.maybe_speak_spontaneously(wired.bot, -100) is True
    message_id, reply, kind, spontaneous = wired.db.recorded[-1]
    assert spontaneous is True
    assert kind == "borsa"  # la borsa si era mossa del 31%
    assert reply
    assert "iniziativa" in wired.ollama.last_system


async def test_zittita_non_parla_da_sola(wired) -> None:
    wired.db.set_quiet(-100, datetime.now(UTC) + timedelta(minutes=30), "Mario")
    assert await bot_module.maybe_speak_spontaneously(wired.bot, -100) is False


async def test_autonomia_off_non_parla_da_sola(wired) -> None:
    wired.db.group["autonomy_level"] = "off"
    assert await bot_module.maybe_speak_spontaneously(wired.bot, -100) is False


async def test_chiusura_di_borsa(wired) -> None:
    assert await bot_module.send_market_report(wired.bot, -100) is True
    kind, size, caption = wired.bot.sent[-1]
    assert kind == "photo" and size > 5_000 and caption


def test_una_scusa_sola_quando_il_cervello_e_giu() -> None:
    """Con Ollama in timeout falliva ogni messaggio, e ogni fallimento era una
    scusa in chat: in un canale attivo diventa spam peggiore del silenzio."""
    bot_module._BRAIN_ERROR_NOTIFIED.clear()
    messaggi = [FakeMessage("allys ci sei?") for _ in range(5)]
    for msg in messaggi:
        asyncio.run(bot_module.notify_brain_error(msg))
    inviati = [m for m in messaggi if m.sent]
    assert len(inviati) == 1
    assert "pausa" in inviati[0].sent[0][1]


def test_la_scusa_torna_dopo_che_il_cervello_si_e_ripreso() -> None:
    bot_module._BRAIN_ERROR_NOTIFIED.clear()
    primo = FakeMessage("allys ci sei?")
    asyncio.run(bot_module.notify_brain_error(primo))
    assert primo.sent

    zittita = FakeMessage("allys ci sei?")
    asyncio.run(bot_module.notify_brain_error(zittita))
    assert not zittita.sent

    bot_module.clear_brain_error(zittita.chat.id)
    dopo_ripresa = FakeMessage("allys ci sei?")
    asyncio.run(bot_module.notify_brain_error(dopo_ripresa))
    assert dopo_ripresa.sent


def test_chat_diverse_ricevono_ognuna_la_sua_scusa() -> None:
    bot_module._BRAIN_ERROR_NOTIFIED.clear()
    gruppo = FakeMessage("allys?", chat_id=-100)
    canale = FakeMessage("allys?", chat_id=-200)
    asyncio.run(bot_module.notify_brain_error(gruppo))
    asyncio.run(bot_module.notify_brain_error(canale))
    assert gruppo.sent and canale.sent
