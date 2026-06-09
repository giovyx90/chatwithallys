import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import edge_tts

from allys.config import Settings
from allys.db import Database
from allys.ollama import OllamaClient


WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def parse_podcast_config(arg: str) -> tuple[bool, str, str | None, str] | str:
    parts = arg.strip().lower().split()
    if parts == ["off"]:
        return False, "weekly", None, "21:00"
    if len(parts) == 2 and parts[0] == "daily" and valid_time(parts[1]):
        return True, "daily", None, parts[1]
    if len(parts) == 3 and parts[0] == "weekly" and parts[1] in WEEKDAYS and valid_time(parts[2]):
        return True, "weekly", parts[1], parts[2]
    return "Uso: /podcast_config weekly friday 21:00 | daily 21:00 | off"


def valid_time(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


class PodcastService:
    def __init__(self, settings: Settings, db: Database, ollama: OllamaClient):
        self.settings = settings
        self.db = db
        self.ollama = ollama
        self.output_dir = Path("data/podcasts")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate(
        self,
        chat_id: int,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, str | None]:
        if progress:
            await progress("Raccolgo i messaggi recenti per il podcast.")
        recent = self.db.recent_messages(chat_id, limit=120)
        digest = "\n".join(f"{row.get('username') or 'anon'}: {row['text']}" for row in recent[-80:])
        if progress:
            await progress("Scrivo lo script.")
        script = await self.ollama.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Scrivi un mini telegiornale satirico italiano di 1-2 minuti per un gruppo Telegram, "
                        "stile TG5 leggero: naturale, parlato, con frasi scorrevoli da conduttrice. "
                        "Non scrivere indicazioni di scena, asterischi, markdown, titoli tecnici, sigle tra parentesi "
                        "o nomi dei parlanti. Non usare @username: se serve scrivi @/. "
                        "Deve essere direttamente leggibile ad alta voce."
                    ),
                },
                {"role": "user", "content": digest or "Settimana povera di eventi. Inventati un recap leggero."},
            ],
            num_predict=520,
            temperature=0.8,
        )
        script = clean_spoken_script(script)
        if progress:
            await progress("Creo l'audio MP3.")
        audio_path = await self.tts(chat_id, script)
        self.db.create_podcast(chat_id, script, audio_path, "created")
        if progress:
            await progress("Podcast pronto, lo invio.")
        return script, audio_path

    async def tts(self, chat_id: int, script: str) -> str | None:
        out = self.output_dir / f"{chat_id}-{int(datetime.now().timestamp())}.mp3"
        communicate = edge_tts.Communicate(script[:4500], "it-IT-ElsaNeural")
        await communicate.save(str(out))
        return str(out)

    def due_groups(self) -> list[dict]:
        now = datetime.now(ZoneInfo(self.settings.podcast_timezone))
        return self.db.due_podcast_groups(now)


async def run_ffmpeg_passthrough(path: str) -> str:
    await asyncio.sleep(0)
    return path


def clean_spoken_script(script: str) -> str:
    text = script.replace("*", "")
    text = re.sub(r"(?im)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?im)^\s*(conduttrice|speaker|voce|narratore|giornalista)\s*:\s*", "", text)
    text = re.sub(r"\[(?:musica|sigla|jingle|applausi|pausa|intro|outro)[^\]]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\((?:musica|sigla|jingle|applausi|pausa|intro|outro)[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?im)^\s*-{2,}\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
