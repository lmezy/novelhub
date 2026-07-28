"""Cookie encryption using AES-GCM with COOKIE_SECRET."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings


def _derive_key() -> bytes:
    """Derive a 256-bit AES key from COOKIE_SECRET."""
    salt = b"novelhub_cookie_salt"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000,
    )
    return kdf.derive(settings.COOKIE_SECRET.encode("utf-8"))


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
