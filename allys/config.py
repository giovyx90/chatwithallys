from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    public_base_url: str = Field(..., alias="PUBLIC_BASE_URL")
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(..., alias="TELEGRAM_WEBHOOK_SECRET")
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field("redis://redis:6379/0", alias="REDIS_URL")
    qdrant_url: str = Field("http://qdrant:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field("allys_memory", alias="QDRANT_COLLECTION")
    ollama_base_url: str = Field("http://ollama:11434", alias="OLLAMA_BASE_URL")
    ollama_chat_model: str = Field("qwen3:8b", alias="OLLAMA_CHAT_MODEL")
    ollama_embed_model: str = Field("nomic-embed-text", alias="OLLAMA_EMBED_MODEL")
    podcast_timezone: str = Field("Europe/Rome", alias="PODCAST_TIMEZONE")
    predictions_base_url: str = Field("https://predictions.giovyx-server.it", alias="PREDICTIONS_BASE_URL")
    predictions_session_secret: str = Field("", alias="PREDICTIONS_SESSION_SECRET")
    owner_telegram_ids: str = Field("8401422869", alias="OWNER_TELEGRAM_IDS")
    feature_arcade: bool = Field(False, alias="ALLY_FEATURE_ARCADE")
    feature_place: bool = Field(False, alias="ALLY_FEATURE_PLACE")
    feature_market: bool = Field(False, alias="ALLY_FEATURE_MARKET")
    feature_predictions: bool = Field(False, alias="ALLY_FEATURE_PREDICTIONS")
    feature_credits: bool = Field(False, alias="ALLY_FEATURE_CREDITS")
    feature_podcast: bool = Field(True, alias="ALLY_FEATURE_PODCAST")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @field_validator("public_base_url")
    @classmethod
    def strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def owner_ids(self) -> set[int]:
        return {int(item.strip()) for item in self.owner_telegram_ids.split(",") if item.strip().isdigit()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
