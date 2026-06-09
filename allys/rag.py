import hashlib
import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from allys.config import Settings
from allys.ollama import OllamaClient

logger = logging.getLogger("allys.rag")


class RagMemory:
    def __init__(self, settings: Settings, ollama: OllamaClient):
        self.settings = settings
        self.ollama = ollama
        self.client = QdrantClient(url=settings.qdrant_url)

    async def ensure_collection(self) -> None:
        collections = self.client.get_collections().collections
        if any(item.name == self.settings.qdrant_collection for item in collections):
            return
        sample = await self.ollama.embed("probe")
        self.client.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config=VectorParams(size=len(sample), distance=Distance.COSINE),
        )

    async def remember(self, chat_id: int, message_id: int, text: str, payload: dict[str, Any]) -> None:
        try:
            await self.ensure_collection()
            vector = await self.ollama.embed(text[:4000])
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=[
                    PointStruct(
                        id=stable_id(chat_id, message_id),
                        vector=vector,
                        payload={"chat_id": chat_id, "text": text, **payload},
                    )
                ],
            )
        except Exception:
            logger.exception("rag remember failed for chat_id=%s message_id=%s", chat_id, message_id)

    async def search(self, chat_id: int, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            await self.ensure_collection()
            vector = await self.ollama.embed(query)
            query_filter = Filter(must=[FieldCondition(key="chat_id", match=MatchValue(value=chat_id))])
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.settings.qdrant_collection,
                    query=vector,
                    query_filter=query_filter,
                    limit=limit,
                )
                hits = response.points
            else:
                hits = self.client.search(
                    collection_name=self.settings.qdrant_collection,
                    query_vector=vector,
                    query_filter=query_filter,
                    limit=limit,
                )
            return [{"score": hit.score, **(hit.payload or {})} for hit in hits]
        except Exception:
            logger.exception("rag search failed for chat_id=%s", chat_id)
            return []


def stable_id(chat_id: int, message_id: int) -> int:
    digest = hashlib.sha256(f"{chat_id}:{message_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=False)
