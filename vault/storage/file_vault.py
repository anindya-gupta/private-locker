"""
Encrypted file storage.

Each file is individually encrypted with AES-256-GCM
and stored on disk. No plaintext file ever touches the filesystem.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from vault.security.encryption import decrypt, encrypt


class FileVault:
    def __init__(self, vault_dir: Path):
        self._dir = vault_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, data: bytes, key: bytes, original_filename: str) -> str:
        """
        Encrypt and store a file. Returns a reference ID used to retrieve it.
        The original filename extension is preserved (encrypted) in metadata.
        """
        file_id = str(uuid.uuid4())
        ext = Path(original_filename).suffix
        meta = f"{original_filename}".encode("utf-8")

        enc_data = encrypt(data, key)
        enc_meta = encrypt(meta, key)

        data_path = self._dir / f"{file_id}.enc"
        meta_path = self._dir / f"{file_id}.meta"

        data_path.write_bytes(enc_data)
        meta_path.write_bytes(enc_meta)

        return file_id

    def retrieve(self, file_id: str, key: bytes) -> tuple[bytes, str]:
        """Decrypt and return file contents and original filename."""
        data_path = self._dir / f"{file_id}.enc"
        meta_path = self._dir / f"{file_id}.meta"

        if not data_path.exists():
            raise FileNotFoundError(f"File {file_id} not found in vault")

        enc_data = data_path.read_bytes()
        plaintext = decrypt(enc_data, key)

        original_name = "unknown"
        if meta_path.exists():
            enc_meta = meta_path.read_bytes()
            original_name = decrypt(enc_meta, key).decode("utf-8")

        return plaintext, original_name

    def delete(self, file_id: str) -> bool:
        """Permanently delete an encrypted file."""
        data_path = self._dir / f"{file_id}.enc"
        meta_path = self._dir / f"{file_id}.meta"
        deleted = False
        for path in (data_path, meta_path):
            if path.exists():
                path.unlink()
                deleted = True
        return deleted

    def exists(self, file_id: str) -> bool:
        return (self._dir / f"{file_id}.enc").exists()

    def list_files(self) -> list[str]:
        """Return all stored file IDs."""
        return [p.stem for p in self._dir.glob("*.enc")]
