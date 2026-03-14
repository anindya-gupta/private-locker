"""
Local vector store for semantic search over documents.

Uses ChromaDB with sentence-transformers for embeddings.
All stored locally — no external calls for embeddings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, persist_dir: Path, embedding_model: str = "all-MiniLM-L6-v2"):
        self._persist_dir = persist_dir
        self._embedding_model = embedding_model
        self._client = None
        self._collection = None

    def initialize(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.Client(Settings(
                persist_directory=str(self._persist_dir),
                is_persistent=True,
                anonymized_telemetry=False,
            ))
            self._collection = self._client.get_or_create_collection(
                name="vault_documents",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("Vector store initialized at %s", self._persist_dir)
        except ImportError:
            logger.warning("ChromaDB not installed. Semantic search disabled. Install with: pip install chromadb")
            self._client = None
            self._collection = None

    @property
    def available(self) -> bool:
        return self._collection is not None

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        if not self.available:
            return
        try:
            self._collection.upsert(
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
            results = self._collection.query(
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
            self._collection.delete(ids=[doc_id])
        except Exception as e:
            logger.error("Failed to delete document %s from vector store: %s", doc_id, e)
