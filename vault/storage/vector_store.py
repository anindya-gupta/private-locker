"""
Vector store for semantic search over documents.

Supports two backends:
  - Qdrant (preferred for multi-tenant SaaS)
  - ChromaDB (fallback for self-hosted single-user)

Qdrant is tried first; if not installed, falls back to ChromaDB.
Both use sentence-transformers for local embedding generation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

QDRANT_COLLECTION = "vault_documents"
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension


def _get_embedding_model(model_name: str):
    """Lazy-load sentence-transformers model (shared across instances)."""
    if not hasattr(_get_embedding_model, "_cache"):
        _get_embedding_model._cache = {}
    if model_name not in _get_embedding_model._cache:
        from sentence_transformers import SentenceTransformer
        _get_embedding_model._cache[model_name] = SentenceTransformer(model_name)
    return _get_embedding_model._cache[model_name]


class VectorStore:
    def __init__(self, persist_dir: Path, embedding_model: str = "all-MiniLM-L6-v2"):
        self._persist_dir = persist_dir
        self._embedding_model = embedding_model
        self._backend: Optional[str] = None
        self._qdrant_client = None
        self._chroma_collection = None

    def initialize(self) -> None:
        if self._try_qdrant():
            return
        self._try_chromadb()

    def _try_qdrant(self) -> bool:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            qdrant_url = os.environ.get("QDRANT_URL")
            if qdrant_url:
                self._qdrant_client = QdrantClient(url=qdrant_url)
            else:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                self._qdrant_client = QdrantClient(path=str(self._persist_dir / "qdrant"))

            collections = [c.name for c in self._qdrant_client.get_collections().collections]
            if QDRANT_COLLECTION not in collections:
                self._qdrant_client.create_collection(
                    collection_name=QDRANT_COLLECTION,
                    vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
                )

            self._backend = "qdrant"
            logger.info("Vector store: Qdrant initialized at %s", self._persist_dir)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("Qdrant init failed, falling back to ChromaDB: %s", e)
            return False

    def _try_chromadb(self) -> bool:
        try:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.Client(Settings(
                persist_directory=str(self._persist_dir),
                is_persistent=True,
                anonymized_telemetry=False,
            ))
            self._chroma_collection = client.get_or_create_collection(
                name="vault_documents",
                metadata={"hnsw:space": "cosine"},
            )
            self._backend = "chromadb"
            logger.info("Vector store: ChromaDB initialized at %s", self._persist_dir)
            return True
        except ImportError:
            logger.warning("No vector store backend installed. Semantic search disabled. "
                           "Install qdrant-client or chromadb.")
            return False

    @property
    def available(self) -> bool:
        return self._backend is not None

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = _get_embedding_model(self._embedding_model)
        return model.encode(texts, show_progress_bar=False).tolist()

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        if not self.available:
            return
        try:
            if self._backend == "qdrant":
                from qdrant_client.models import PointStruct
                vector = self._embed([text])[0]
                payload = metadata or {}
                payload["_text"] = text
                self._qdrant_client.upsert(
                    collection_name=QDRANT_COLLECTION,
                    points=[PointStruct(id=doc_id, vector=vector, payload=payload)],
                )
            else:
                self._chroma_collection.upsert(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[metadata or {}],
                )
        except Exception as e:
            logger.error("Failed to index document %s: %s", doc_id, e)

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        if not self.available:
            return []
        try:
            if self._backend == "qdrant":
                vector = self._embed([query])[0]
                hits = self._qdrant_client.search(
                    collection_name=QDRANT_COLLECTION,
                    query_vector=vector,
                    limit=n_results,
                    with_payload=True,
                )
                docs = []
                for hit in hits:
                    payload = hit.payload or {}
                    text = payload.pop("_text", None)
                    docs.append({
                        "id": hit.id,
                        "text": text,
                        "metadata": payload,
                        "distance": 1.0 - hit.score,
                    })
                return docs
            else:
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=n_results,
                )
                docs = []
                for i, doc_id in enumerate(results["ids"][0]):
                    docs.append({
                        "id": doc_id,
                        "text": results["documents"][0][i] if results["documents"] else None,
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else None,
                    })
                return docs
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            return []

    def delete_document(self, doc_id: str) -> None:
        if not self.available:
            return
        try:
            if self._backend == "qdrant":
                from qdrant_client.models import PointIdsList
                self._qdrant_client.delete(
                    collection_name=QDRANT_COLLECTION,
                    points_selector=PointIdsList(points=[doc_id]),
                )
            else:
                self._chroma_collection.delete(ids=[doc_id])
        except Exception as e:
            logger.error("Failed to delete document %s from vector store: %s", doc_id, e)
