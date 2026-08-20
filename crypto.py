"""Small authenticated-encryption helper for plugin secrets."""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:v1:"


def _key():
    configured = os.environ.get("EVENTSLOTARR_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            Fernet(configured.encode("ascii"))
            return configured.encode("ascii")
        except Exception as ex:
            raise ValueError("EVENTSLOTARR_ENCRYPTION_KEY is not a valid Fernet key") from ex

    try:
        from django.conf import settings
        secret = settings.SECRET_KEY
    except Exception as ex:
        raise RuntimeError("Dispatcharr SECRET_KEY is unavailable") from ex

    digest = hashlib.sha256(str(secret).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(value):
    if not value or is_encrypted(value):
        return value
    token = Fernet(_key()).encrypt(str(value).encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value):
    if not value:
        return value
    if not is_encrypted(value):
        return value
    try:
        token = value[len(_PREFIX):].encode("ascii")
        return Fernet(_key()).decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as ex:
        raise ValueError("EventSlotarr secret could not be decrypted") from ex
