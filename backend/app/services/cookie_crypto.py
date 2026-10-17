"""Cookie encryption using AES-GCM with COOKIE_SECRET."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings

import logging

logger = logging.getLogger(__name__)


def safe_decrypt_cookie(value: str) -> str:
    """Decrypt cookie data, falling back to plaintext for legacy unencrypted cookies."""
    try:
        return decrypt_cookie(value)
    except Exception:
        logger.debug("Cookie value appears to be plaintext (legacy), using as-is")
        return value


_derived_key_cache: bytes | None = None


def _derive_key() -> bytes:
    """Derive a 256-bit AES key from COOKIE_SECRET.

    PBKDF2 with 100k iterations costs ~50-100ms; every synced chapter used to
    pay it on each encrypt/decrypt.  The inputs (secret + fixed salt) never
    change within a process, so the derived key is cached module-level.  The
    salt is a fixed domain-separation constant, not a per-record secret, so
    caching it carries no cryptographic downside.
    """
    global _derived_key_cache
    if _derived_key_cache is None:
        salt = b"novelhub_cookie_salt"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000,
        )
        _derived_key_cache = kdf.derive(settings.COOKIE_SECRET.encode("utf-8"))
    return _derived_key_cache


def encrypt_cookie(plaintext: str) -> str:
    """Encrypt cookie data. Returns base64( nonce + ciphertext )."""
    key = _derive_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_cookie(encrypted: str) -> str:
    """Decrypt cookie data."""
    key = _derive_key()
    raw = base64.b64decode(encrypted)
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
