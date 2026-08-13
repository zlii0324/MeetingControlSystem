from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from config import config


class JitsiJwtError(ValueError):
    pass


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url_segment(value: str) -> bytes:
    if not value or "=" in value:
        raise JitsiJwtError("JWT 格式无效")

    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise JitsiJwtError("JWT 格式无效") from exc

    # Reject alternate Base64URL spellings of the same bytes. In particular,
    # this prevents changing unused bits in the final signature character.
    if _base64url(decoded) != value:
        raise JitsiJwtError("JWT 格式无效")
    return decoded


def _numeric_claim(payload: dict[str, Any], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JitsiJwtError(f"JWT {name} 无效")
    return float(value)


def verify_jitsi_token(
    token: str,
    room_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a Jitsi JWT before serving the room's web application."""
    if not config.jitsi_jwt_enabled:
        raise JitsiJwtError("Jitsi JWT 未配置")

    normalized_room = str(room_id or "").strip()
    if not normalized_room:
        raise JitsiJwtError("Jitsi 房间名不能为空")

    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        raise JitsiJwtError("JWT 格式无效")
    encoded_header, encoded_payload, encoded_signature = parts

    try:
        header = json.loads(_decode_base64url_segment(encoded_header).decode("utf-8"))
        payload = json.loads(_decode_base64url_segment(encoded_payload).decode("utf-8"))
        signature = _decode_base64url_segment(encoded_signature)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JitsiJwtError("JWT 格式无效") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise JitsiJwtError("JWT 格式无效")
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise JitsiJwtError("JWT 算法无效")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = hmac.new(
        config.jitsi_jwt_app_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise JitsiJwtError("JWT 签名无效")

    audience = payload.get("aud")
    audience_matches = audience == config.jitsi_jwt_app_id or (
        isinstance(audience, list) and config.jitsi_jwt_app_id in audience
    )
    if payload.get("iss") != config.jitsi_jwt_app_id or not audience_matches:
        raise JitsiJwtError("JWT 签发方无效")
    if payload.get("sub") != config.jitsi_jwt_subject:
        raise JitsiJwtError("JWT 域无效")
    if payload.get("room") != normalized_room:
        raise JitsiJwtError("JWT 房间无效")

    current_seconds = (now or datetime.now(timezone.utc)).timestamp()
    if current_seconds >= _numeric_claim(payload, "exp"):
        raise JitsiJwtError("JWT 已过期")
    if current_seconds < _numeric_claim(payload, "nbf"):
        raise JitsiJwtError("JWT 尚未生效")

    return payload


def create_jitsi_token(
    room_id: str,
    *,
    display_name: str | None = None,
    email: str | None = None,
    user_id: str | int | None = None,
    now: datetime | None = None,
) -> str:
    """Create the HS256 JWT expected by Jitsi's Prosody token auth module."""
    if not config.jitsi_jwt_enabled:
        raise JitsiJwtError("Jitsi JWT 未配置")

    normalized_room = str(room_id or "").strip()
    if not normalized_room:
        raise JitsiJwtError("Jitsi 房间名不能为空")

    issued_at = now or datetime.now(timezone.utc)
    issued_at_seconds = int(issued_at.timestamp())
    token_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "aud": config.jitsi_jwt_app_id,
        "iss": config.jitsi_jwt_app_id,
        "sub": config.jitsi_jwt_subject,
        "room": normalized_room,
        "iat": issued_at_seconds,
        "nbf": issued_at_seconds - 10,
        "exp": issued_at_seconds + config.jitsi_jwt_ttl_seconds,
        "jti": token_id,
    }

    context_user: dict[str, str] = {}
    if user_id is not None and str(user_id).strip():
        context_user["id"] = str(user_id).strip()[:128]
    if display_name is not None and str(display_name).strip():
        context_user["name"] = str(display_name).strip()[:80]
    if email is not None and str(email).strip():
        context_user["email"] = str(email).strip()[:120]
    if context_user:
        context_user.setdefault("id", token_id)
        payload["context"] = {"user": context_user}

    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    encoded_payload = _base64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(
        config.jitsi_jwt_app_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"
