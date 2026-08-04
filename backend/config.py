from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
ENV_FILE = BASE_DIR / ".env"
DEFAULT_MEETING_LINK_ORIGIN = "http://localhost:5173"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ENV_FILE
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default

    return value.lower() in {"1", "true", "yes", "on"}


def _as_str(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def _as_origin_list(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())


def _as_cookie_samesite(name: str, default: str) -> str:
    value = os.getenv(name, "").strip() or default
    if value not in {"Lax", "Strict", "None"}:
        raise ValueError(f"{name} must be one of Lax, Strict, None")
    return value


load_dotenv()


@dataclass(frozen=True)
class Config:
    database_path: str = _as_str(
        "DATABASE_PATH",
        str(INSTANCE_DIR / "meetings.sqlite3"),
    )
    jitsi_base_url: str = _as_str("JITSI_BASE_URL", "https://meet.wusupower.com/")
    room_prefix: str = _as_str("ROOM_PREFIX", "mcs")
    frontend_origins: tuple[str, ...] = _as_origin_list("FRONTEND_ORIGIN")
    meeting_link_origin: str = _as_str(
        "MEETING_LINK_ORIGIN",
        DEFAULT_MEETING_LINK_ORIGIN,
    ).rstrip("/")
    password_secret: str = _as_str(
        "PASSWORD_SECRET",
        "development-secret-change-before-production",
    )
    default_duration_hours: int = _as_int("DEFAULT_DURATION_HOURS", 24)
    early_join_minutes: int = _as_int("RESERVATION_EARLY_JOIN_MINUTES", 15)
    max_default_occupants: int = _as_int("DEFAULT_MAX_OCCUPANTS", 30)
    enable_cors: bool = _as_bool("ENABLE_CORS", True)
    session_cookie_name: str = _as_str("SESSION_COOKIE_NAME", "mcs_session")
    session_ttl_days: int = _as_int("SESSION_TTL_DAYS", 7)
    session_cookie_secure: bool = _as_bool("SESSION_COOKIE_SECURE", False)
    session_cookie_samesite: str = _as_cookie_samesite("SESSION_COOKIE_SAMESITE", "Lax")
    email_notifications_enabled: bool = _as_bool("EMAIL_NOTIFICATIONS_ENABLED", False)
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = _as_int("SMTP_PORT", 587)
    smtp_username: str = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = _as_bool("SMTP_USE_TLS", True)
    smtp_use_ssl: bool = _as_bool("SMTP_USE_SSL", False)
    smtp_verify_certificate: bool = _as_bool("SMTP_VERIFY_CERTIFICATE", True)
    smtp_timeout_seconds: int = _as_int("SMTP_TIMEOUT_SECONDS", 10)
    email_from: str = os.getenv("EMAIL_FROM", "").strip()
    email_from_name: str = _as_str("EMAIL_FROM_NAME", "会议管理系统")
    email_timezone: str = _as_str("EMAIL_TIMEZONE", "Asia/Shanghai")
    password_reset_url_origin: str = _as_str(
        "PASSWORD_RESET_URL_ORIGIN",
        _as_str("MEETING_LINK_ORIGIN", DEFAULT_MEETING_LINK_ORIGIN),
    ).rstrip("/")
    password_reset_token_ttl_minutes: int = _as_int("PASSWORD_RESET_TOKEN_TTL_MINUTES", 30)
    password_reset_cooldown_seconds: int = _as_int("PASSWORD_RESET_COOLDOWN_SECONDS", 60)

    @property
    def normalized_jitsi_base_url(self) -> str:
        return self.jitsi_base_url.rstrip("/") + "/"

    @property
    def frontend_origin(self) -> str:
        return self.frontend_origins[0] if self.frontend_origins else ""


config = Config()
