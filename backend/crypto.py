from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

from cryptography.fernet import Fernet


HASH_NAME: Final[str] = "pbkdf2_sha256"
HASH_ITERATIONS: Final[int] = 260_000


def _fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_password(password: str, secret: str) -> str:
    return _fernet_from_secret(secret).encrypt(password.encode("utf-8")).decode("utf-8")


def decrypt_password(token: str, secret: str) -> str:
    return _fernet_from_secret(secret).decrypt(token.encode("utf-8")).decode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS,
    ).hex()
    return f"{HASH_NAME}${HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
    except ValueError:
        return False

    if algorithm != HASH_NAME:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    ).hex()
    return hmac.compare_digest(digest, expected)

