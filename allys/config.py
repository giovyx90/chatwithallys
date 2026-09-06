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
    ollama_gpu_base_url: str = Field("", alias="OLLAMA_GPU_BASE_URL")
    ollama_gpu_chat_model: str = Field("", alias="OLLAMA_GPU_CHAT_MODEL")
    ollama_gpu_probe_seconds: int = Field(30, alias="OLLAMA_GPU_PROBE_SECONDS")
    ollama_gpu_predict_scale: float = Field(1.6, alias="OLLAMA_GPU_PREDICT_SCALE")
    # Quanto aspettare il modello prima di rinunciare: sulla CPU della VPS una
    # risposta corta sta sotto il minuto, oltre non interessa piu' a nessuno.
    ollama_timeout_seconds: int = Field(120, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_gpu_timeout_seconds: int = Field(60, alias="OLLAMA_GPU_TIMEOUT_SECONDS")
    ollama_max_queue: int = Field(2, alias="OLLAMA_MAX_QUEUE")
    ollama_keep_alive: str = Field("30m", alias="OLLAMA_KEEP_ALIVE")
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
    giphy_api_key: str = Field("", alias="GIPHY_API_KEY")
    tenor_api_key: str = Field("", alias="TENOR_API_KEY")
    meme_reddit_fallback: bool = Field(True, alias="MEME_REDDIT_FALLBACK")
    telegram_auto_webhook: bool = Field(True, alias="TELEGRAM_AUTO_WEBHOOK")
    market_report_time: str = Field("21:00", alias="MARKET_REPORT_TIME")

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
