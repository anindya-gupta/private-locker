"""
Core encryption engine for Vault.

Uses Argon2id for key derivation and AES-256-GCM for encryption.
Three separate derived keys: database, files, credentials.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SALT_LENGTH = 32
NONCE_LENGTH = 12
KEY_LENGTH = 32  # 256 bits
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # 64 MB
ARGON2_PARALLELISM = 4


class KeyPurpose(Enum):
    DATABASE = b"vault-db-v1\x00\x00\x00\x00\x00"
    FILES = b"vault-file-v1\x00\x00\x00"
    CREDENTIALS = b"vault-cred-v1\x00\x00\x00"


@dataclass(frozen=True)
class DerivedKeys:
    db_key: bytes
    file_key: bytes
    cred_key: bytes
    salt: bytes


def derive_master_key(password: str, salt: Optional[bytes] = None) -> tuple[bytes, bytes]:
    """Derive a 256-bit master key from the password using Argon2id."""
    if salt is None:
        salt = secrets.token_bytes(SALT_LENGTH)

    master_key = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_LENGTH,
        type=Type.ID,
    )
    return master_key, salt


def derive_purpose_key(master_key: bytes, purpose: KeyPurpose) -> bytes:
    """Derive a purpose-specific key from the master key via HKDF-like construction."""
    return hashlib.blake2b(
        master_key,
        digest_size=KEY_LENGTH,
        person=purpose.value,
    ).digest()


def derive_all_keys(password: str, salt: Optional[bytes] = None) -> DerivedKeys:
    """Derive all three purpose keys from a master password."""
    master_key, used_salt = derive_master_key(password, salt)
    return DerivedKeys(
        db_key=derive_purpose_key(master_key, KeyPurpose.DATABASE),
        file_key=derive_purpose_key(master_key, KeyPurpose.FILES),
        cred_key=derive_purpose_key(master_key, KeyPurpose.CREDENTIALS),
        salt=used_salt,
    )


def encrypt(data: bytes, key: bytes) -> bytes:
    """
    Encrypt data with AES-256-GCM.
    Returns: nonce (12 bytes) || ciphertext+tag
    """
    nonce = secrets.token_bytes(NONCE_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(blob: bytes, key: bytes) -> bytes:
    """
    Decrypt AES-256-GCM blob.
    Expects: nonce (12 bytes) || ciphertext+tag
    """
    if len(blob) < NONCE_LENGTH + 16:  # nonce + minimum tag
        raise ValueError("Encrypted data is too short")
    nonce = blob[:NONCE_LENGTH]
    ciphertext = blob[NONCE_LENGTH:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def encrypt_file(filepath: str, key: bytes) -> bytes:
    """Read a file and return its encrypted contents."""
    with open(filepath, "rb") as f:
        plaintext = f.read()
    return encrypt(plaintext, key)


def decrypt_file(encrypted_data: bytes, key: bytes) -> bytes:
    """Decrypt file contents."""
    return decrypt(encrypted_data, key)


def generate_verification_token(password: str, salt: bytes) -> bytes:
    """
    Generate a token that can verify the master password without storing it.
    We encrypt a known plaintext with the DB key — on unlock, we try to
    decrypt it. If it succeeds and matches, the password is correct.
    """
    keys = derive_all_keys(password, salt)
    known_plaintext = b"VAULT_VERIFY_TOKEN_V1"
    return encrypt(known_plaintext, keys.db_key)


def verify_password(password: str, salt: bytes, token: bytes) -> bool:
    """Verify a master password against a stored verification token."""
    return verify_password_and_derive_keys(password, salt, token) is not None


def verify_password_and_derive_keys(password: str, salt: bytes, token: bytes) -> Optional[DerivedKeys]:
    """
    Verify password and return derived keys if valid. Use this during unlock
    to avoid deriving keys twice (Argon2id is expensive, especially on small VMs).
    """
    try:
        keys = derive_all_keys(password, salt)
        plaintext = decrypt(token, keys.db_key)
        if plaintext == b"VAULT_VERIFY_TOKEN_V1":
            return keys
        return None
    except Exception:
        return None


# ===== RSA Key Pairs for Cross-User Sharing =====

def generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Generate an RSA-2048 key pair. Returns (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def rsa_encrypt(plaintext: bytes, public_pem: bytes) -> bytes:
    """Encrypt data with an RSA public key (OAEP + SHA-256)."""
    public_key = serialization.load_pem_public_key(public_pem)
    return public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def rsa_decrypt(ciphertext: bytes, private_pem: bytes) -> bytes:
    """Decrypt data with an RSA private key."""
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return private_key.decrypt(
        ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
