"""Tests for encryption engine — key derivation, encrypt/decrypt, password verification."""

from __future__ import annotations

import pytest

from vault.security.encryption import (
    decrypt,
    derive_all_keys,
    derive_master_key,
    encrypt,
    generate_verification_token,
    verify_password,
)


class TestKeyDerivation:
    def test_derive_produces_keys(self):
        keys = derive_all_keys("password123")
        assert len(keys.db_key) == 32
        assert len(keys.file_key) == 32
        assert len(keys.cred_key) == 32
        assert len(keys.salt) == 32

    def test_all_keys_are_different(self):
        keys = derive_all_keys("password123")
        assert keys.db_key != keys.file_key
        assert keys.file_key != keys.cred_key
        assert keys.db_key != keys.cred_key

    def test_same_password_same_salt_same_keys(self):
        salt = b"\x01" * 32
        keys1 = derive_all_keys("password", salt)
        keys2 = derive_all_keys("password", salt)
        assert keys1.db_key == keys2.db_key

    def test_different_passwords_different_keys(self):
        salt = b"\x01" * 32
        keys1 = derive_all_keys("password1", salt)
        keys2 = derive_all_keys("password2", salt)
        assert keys1.db_key != keys2.db_key


class TestEncryptDecrypt:
    def test_roundtrip(self):
        key = derive_all_keys("test").db_key
        plaintext = b"Hello, World!"
        ciphertext = encrypt(plaintext, key)
        assert ciphertext != plaintext
        decrypted = decrypt(ciphertext, key)
        assert decrypted == plaintext

    def test_different_nonces(self):
        key = derive_all_keys("test").db_key
        c1 = encrypt(b"data", key)
        c2 = encrypt(b"data", key)
        assert c1 != c2, "Each encryption should produce a unique nonce"

    def test_wrong_key_fails(self):
        keys1 = derive_all_keys("right", b"\x01" * 32)
        keys2 = derive_all_keys("wrong", b"\x01" * 32)
        ciphertext = encrypt(b"secret", keys1.db_key)
        with pytest.raises(Exception):
            decrypt(ciphertext, keys2.db_key)

    def test_corrupted_data_fails(self):
        key = derive_all_keys("test").db_key
        ciphertext = encrypt(b"data", key)
        corrupted = ciphertext[:10] + b"\xff" + ciphertext[11:]
        with pytest.raises(Exception):
            decrypt(corrupted, key)

    def test_too_short_data_fails(self):
        key = derive_all_keys("test").db_key
        with pytest.raises(ValueError, match="too short"):
            decrypt(b"\x00" * 10, key)

    def test_empty_plaintext(self):
        key = derive_all_keys("test").db_key
        ct = encrypt(b"", key)
        assert decrypt(ct, key) == b""

    def test_large_data(self):
        key = derive_all_keys("test").db_key
        big = b"x" * (10 * 1024 * 1024)
        ct = encrypt(big, key)
        assert decrypt(ct, key) == big


class TestPasswordVerification:
    def test_correct_password(self):
        salt = b"\x02" * 32
        token = generate_verification_token("mypass", salt)
        assert verify_password("mypass", salt, token) is True

    def test_wrong_password(self):
        salt = b"\x02" * 32
        token = generate_verification_token("mypass", salt)
        assert verify_password("wrongpass", salt, token) is False

    def test_corrupted_token(self):
        salt = b"\x02" * 32
        token = generate_verification_token("mypass", salt)
        corrupted = token[:5] + b"\xff" + token[6:]
        assert verify_password("mypass", salt, corrupted) is False
