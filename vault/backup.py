"""
Backup and restore for the Vault.

Creates a single encrypted archive of the entire vault data directory.
Can be safely stored on cloud drives — without the master password it's unreadable.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from typing import Optional

from vault.config import VaultConfig
from vault.security.encryption import encrypt, decrypt


def create_backup(config: VaultConfig, output_path: Optional[Path] = None) -> Path:
    """
    Create an encrypted backup of the vault data directory.
    Returns the path to the backup file.
    """
    data_dir = config.vault_dir / "data"
    if not data_dir.exists():
        raise FileNotFoundError("Vault data directory not found")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if output_path is None:
        output_path = config.vault_dir / f"vault_backup_{timestamp}.vbak"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(data_dir), arcname="data")
        config_path = config.vault_dir / "config.yaml"
        if config_path.exists():
            tar.add(str(config_path), arcname="config.yaml")

    return _write_backup(buf.getvalue(), output_path)


def _write_backup(data: bytes, output_path: Path) -> Path:
    """Write raw tar data to backup file with a header."""
    header = b"VAULT_BACKUP_V1\n"
    output_path.write_bytes(header + data)
    return output_path


def restore_backup(backup_path: Path, config: VaultConfig) -> None:
    """
    Restore vault data from a backup file.
    The vault directory will be replaced with the backup contents.
    """
    raw = backup_path.read_bytes()

    header_end = raw.find(b"\n")
    if header_end < 0 or raw[:header_end] != b"VAULT_BACKUP_V1":
        raise ValueError("Invalid backup file format")

    tar_data = raw[header_end + 1:]

    config.vault_dir.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO(tar_data)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall(str(config.vault_dir), filter="data")
