"""
Per-user agent pool for multi-tenant Vault.

Lazily creates and caches VaultAgent instances per user. Each user's agent
operates on their isolated vault directory (own DB, files, vector store).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from vault.agent import VaultAgent
from vault.config import VaultConfig
from vault.security.session import Session
from vault.users import UserRecord

logger = logging.getLogger(__name__)


class AgentPool:
    """Thread-safe pool of per-user VaultAgent instances."""

    def __init__(self, base_config: VaultConfig) -> None:
        self._base_config = base_config
        self._agents: dict[str, VaultAgent] = {}
        self._lock = threading.Lock()

    def _make_config(self, user: UserRecord) -> VaultConfig:
        """Create a VaultConfig pointing at a user's vault directory."""
        return VaultConfig(
            vault_dir=user.vault_dir,
            llm_provider=self._base_config.llm_provider,
            llm_model=self._base_config.llm_model,
            ollama_model=self._base_config.ollama_model,
            paranoid_mode=self._base_config.paranoid_mode,
            session_timeout=self._base_config.session_timeout,
            ocr_enabled=self._base_config.ocr_enabled,
            embedding_model=self._base_config.embedding_model,
            smtp_host=self._base_config.smtp_host,
            smtp_port=self._base_config.smtp_port,
            smtp_user=self._base_config.smtp_user,
            smtp_password=self._base_config.smtp_password,
            smtp_from=self._base_config.smtp_from,
        )

    def get(self, user: UserRecord, session: Session) -> VaultAgent:
        with self._lock:
            ag = self._agents.get(user.user_id)
            if ag is not None:
                ag.session = session
                return ag

            user_config = self._make_config(user)
            user_config.ensure_dirs()
            dummy = Session()
            dummy.configure(user.salt, user.verification_token, self._base_config.session_timeout)
            ag = VaultAgent(user_config, dummy)
            ag.initialize()

            username_from_meta: Optional[str] = None
            if ag.db._conn:
                try:
                    row = ag.db._conn.execute(
                        "SELECT value FROM meta WHERE key = 'username'"
                    ).fetchone()
                    username_from_meta = row["value"] if row else None
                except Exception:
                    pass

            if not username_from_meta and ag.db._conn:
                try:
                    ag.db._conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        ("username", user.username),
                    )
                    ag.db._conn.commit()
                except Exception:
                    pass

            ag.session = session
            self._agents[user.user_id] = ag
            logger.info("Loaded agent for user %s (%s)", user.username, user.user_id)
            return ag

    def evict(self, user_id: str) -> None:
        with self._lock:
            ag = self._agents.pop(user_id, None)
            if ag:
                ag.shutdown()

    def shutdown_all(self) -> None:
        with self._lock:
            for ag in self._agents.values():
                try:
                    ag.shutdown()
                except Exception:
                    pass
            self._agents.clear()
