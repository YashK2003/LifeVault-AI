"""Encryption helpers for LifeVault.

This module provides the cryptographic primitives used by the storage layer,
including passphrase-based key derivation and AES-256-GCM encryption for
sensitive document content.
"""

import os
import base64
import json
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NONCE_SIZE = 12          # 96 bits — standard for AES-GCM
SALT_SIZE = 16           # 128 bits for PBKDF2 salt
KEY_SIZE = 32            # 256 bits for AES-256
PBKDF2_ITERATIONS = 600_000  # OWASP recommended minimum (2024)


# ---------------------------------------------------------------------------
# Key Derivation
# ---------------------------------------------------------------------------

def generate_salt() -> bytes:
    """Generate a cryptographically random salt for PBKDF2."""
    return os.urandom(SALT_SIZE)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """
    Derive a 256-bit encryption key from a user passphrase using PBKDF2.

    Args:
        passphrase: User-provided passphrase (should be strong)
        salt: Random salt (stored alongside the vault, not secret)

    Returns:
        32-byte derived key suitable for AES-256-GCM
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def verify_passphrase(passphrase: str, salt: bytes, verification_token: bytes) -> bool:
    """
    Verify a passphrase by attempting to decrypt a known token.
    Used during vault unlock to confirm the user has the right passphrase.

    Args:
        passphrase: User-provided passphrase to verify
        salt: Stored salt from vault creation
        verification_token: Encrypted known-plaintext stored at vault creation

    Returns:
        True if passphrase is correct, False otherwise
    """
    try:
        key = derive_key(passphrase, salt)
        decrypted = decrypt_data(verification_token, key)
        return decrypted == b"LIFEVAULT_VERIFICATION"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Encryption / Decryption
# ---------------------------------------------------------------------------

def encrypt_data(plaintext: bytes, key: bytes) -> bytes:
    """
    Encrypt data using AES-256-GCM.

    Args:
        plaintext: Raw bytes to encrypt
        key: 256-bit encryption key

    Returns:
        nonce (12 bytes) || ciphertext — ready for storage

    The nonce is generated fresh for every encryption call, ensuring
    that identical plaintexts produce different ciphertexts.
    """
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # Prepend nonce to ciphertext for self-contained storage
    return nonce + ciphertext


def decrypt_data(encrypted: bytes, key: bytes) -> bytes:
    """
    Decrypt AES-256-GCM encrypted data.

    Args:
        encrypted: nonce (12 bytes) || ciphertext as produced by encrypt_data
        key: 256-bit encryption key (same key used for encryption)

    Returns:
        Original plaintext bytes

    Raises:
        cryptography.exceptions.InvalidTag: If key is wrong or data is tampered
    """
    nonce = encrypted[:NONCE_SIZE]
    ciphertext = encrypted[NONCE_SIZE:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# Convenience helpers for string/JSON data
# ---------------------------------------------------------------------------

def encrypt_string(text: str, key: bytes) -> bytes:
    """Encrypt a UTF-8 string. Returns raw encrypted bytes."""
    return encrypt_data(text.encode("utf-8"), key)


def decrypt_string(encrypted: bytes, key: bytes) -> str:
    """Decrypt back to a UTF-8 string."""
    return decrypt_data(encrypted, key).decode("utf-8")


def encrypt_json(data: dict, key: bytes) -> bytes:
    """Encrypt a JSON-serializable dict. Returns raw encrypted bytes."""
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return encrypt_data(json_bytes, key)


def decrypt_json(encrypted: bytes, key: bytes) -> dict:
    """Decrypt back to a Python dict."""
    json_bytes = decrypt_data(encrypted, key)
    return json.loads(json_bytes.decode("utf-8"))


# ---------------------------------------------------------------------------
# Base64 helpers (for storing encrypted bytes in SQLite TEXT columns)
# ---------------------------------------------------------------------------

def bytes_to_b64(data: bytes) -> str:
    """Encode bytes to URL-safe base64 string for DB storage."""
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64_to_bytes(b64_string: str) -> bytes:
    """Decode URL-safe base64 string back to bytes."""
    return base64.urlsafe_b64decode(b64_string.encode("ascii"))
