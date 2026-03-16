"""Tests for FileVault — encrypted file storage, retrieval, listing, deletion."""

from __future__ import annotations

import pytest

from vault.storage.file_vault import FileVault


class TestFileVault:
    def test_store_and_retrieve(self, file_vault, keys):
        data = b"PDF binary content here"
        ref = file_vault.store(data, keys.file_key, "report.pdf")
        retrieved, name = file_vault.retrieve(ref, keys.file_key)
        assert retrieved == data
        assert name == "report.pdf"

    def test_file_is_encrypted_on_disk(self, file_vault, keys):
        data = b"Sensitive medical data"
        ref = file_vault.store(data, keys.file_key, "medical.pdf")
        enc_path = file_vault._dir / f"{ref}.enc"
        assert enc_path.exists()
        raw = enc_path.read_bytes()
        assert raw != data, "File on disk should be encrypted"

    def test_retrieve_nonexistent(self, file_vault, keys):
        with pytest.raises(FileNotFoundError):
            file_vault.retrieve("nonexistent-id", keys.file_key)

    def test_delete_file(self, file_vault, keys):
        ref = file_vault.store(b"data", keys.file_key, "test.pdf")
        assert file_vault.exists(ref) is True
        assert file_vault.delete(ref) is True
        assert file_vault.exists(ref) is False

    def test_delete_nonexistent(self, file_vault, keys):
        assert file_vault.delete("fake-id") is False

    def test_list_files(self, file_vault, keys):
        ref1 = file_vault.store(b"a", keys.file_key, "a.pdf")
        ref2 = file_vault.store(b"b", keys.file_key, "b.pdf")
        files = file_vault.list_files()
        assert ref1 in files
        assert ref2 in files
        assert len(files) == 2

    def test_wrong_key_decrypt_fails(self, file_vault, keys):
        from vault.security.encryption import derive_all_keys
        wrong_keys = derive_all_keys("different-password", b"\x05" * 32)
        ref = file_vault.store(b"secret data", keys.file_key, "secret.pdf")
        with pytest.raises(Exception):
            file_vault.retrieve(ref, wrong_keys.file_key)

    def test_various_file_types(self, file_vault, keys):
        for name in ["image.png", "doc.docx", "spreadsheet.xlsx", "archive.zip"]:
            data = f"content of {name}".encode()
            ref = file_vault.store(data, keys.file_key, name)
            retrieved, orig = file_vault.retrieve(ref, keys.file_key)
            assert retrieved == data
            assert orig == name

    def test_large_file(self, file_vault, keys):
        data = b"\x00" * (5 * 1024 * 1024)  # 5MB
        ref = file_vault.store(data, keys.file_key, "large.bin")
        retrieved, _ = file_vault.retrieve(ref, keys.file_key)
        assert retrieved == data

    def test_empty_file(self, file_vault, keys):
        ref = file_vault.store(b"", keys.file_key, "empty.txt")
        retrieved, name = file_vault.retrieve(ref, keys.file_key)
        assert retrieved == b""
        assert name == "empty.txt"
