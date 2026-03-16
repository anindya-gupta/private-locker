"""
Configuration management for Vault.

Stores non-sensitive settings in a YAML config file.
Sensitive data (keys, passwords) are NEVER stored here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_VAULT_DIR = Path(os.environ.get("VAULT_DIR", str(Path.home() / ".vault")))


@dataclass
class VaultConfig:
    vault_dir: Path = field(default_factory=lambda: DEFAULT_VAULT_DIR)
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    ollama_model: str = "llama3.1:8b"
    paranoid_mode: bool = False
    session_timeout: int = 300
    ocr_enabled: bool = True
    embedding_model: str = "all-MiniLM-L6-v2"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @property
    def db_path(self) -> Path:
        return self.vault_dir / "data" / "vault.db"

    @property
    def files_dir(self) -> Path:
        return self.vault_dir / "data" / "files"

    @property
    def chroma_dir(self) -> Path:
        return self.vault_dir / "data" / "chroma"

    @property
    def config_path(self) -> Path:
        return self.vault_dir / "config.yaml"

    @property
    def salt_path(self) -> Path:
        return self.vault_dir / "data" / ".salt"

    @property
    def token_path(self) -> Path:
        return self.vault_dir / "data" / ".verify_token"

    def ensure_dirs(self) -> None:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "data").mkdir(exist_ok=True)
        self.files_dir.mkdir(exist_ok=True)
        self.chroma_dir.mkdir(exist_ok=True)

    def save(self) -> None:
        self.ensure_dirs()
        data = {
            "vault_dir": str(self.vault_dir),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "ollama_model": self.ollama_model,
            "paranoid_mode": self.paranoid_mode,
            "session_timeout": self.session_timeout,
            "ocr_enabled": self.ocr_enabled,
            "embedding_model": self.embedding_model,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_user": self.smtp_user,
            "smtp_password": self.smtp_password,
            "smtp_from": self.smtp_from,
        }
        with open(self.config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    @classmethod
    def load(cls, vault_dir: Optional[Path] = None) -> VaultConfig:
        base = vault_dir or Path(os.environ.get("VAULT_DIR", str(DEFAULT_VAULT_DIR)))
        config_path = base / "config.yaml"
        if not config_path.exists():
            return cls(vault_dir=base)
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            vault_dir=Path(data.get("vault_dir", str(base))),
            llm_provider=data.get("llm_provider", "openai"),
            llm_model=data.get("llm_model", "gpt-4o-mini"),
            ollama_model=data.get("ollama_model", "llama3.1:8b"),
            paranoid_mode=data.get("paranoid_mode", False),
            session_timeout=data.get("session_timeout", 300),
            ocr_enabled=data.get("ocr_enabled", True),
            embedding_model=data.get("embedding_model", "all-MiniLM-L6-v2"),
            smtp_host=os.environ.get("SMTP_HOST", data.get("smtp_host", "")),
            smtp_port=int(os.environ.get("SMTP_PORT", data.get("smtp_port", 587))),
            smtp_user=os.environ.get("SMTP_USER", data.get("smtp_user", "")),
            smtp_password=os.environ.get("SMTP_PASSWORD", data.get("smtp_password", "")),
            smtp_from=os.environ.get("SMTP_FROM", data.get("smtp_from", "")),
        )


config = VaultConfig.load()
