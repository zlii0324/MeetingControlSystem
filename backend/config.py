from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
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


load_dotenv()


@dataclass(frozen=True)
class Config:
    database_path: str = os.getenv(
        "DATABASE_PATH",
        str(INSTANCE_DIR / "meetings.sqlite3"),
    )
    jitsi_base_url: str = os.getenv("JITSI_BASE_URL", "https://meet.wusupower.com/")
    room_prefix: str = os.getenv("ROOM_PREFIX", "mcs")
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    password_secret: str = os.getenv(
        "PASSWORD_SECRET",
        "development-secret-change-before-production",
    )
    default_duration_hours: int = _as_int("DEFAULT_DURATION_HOURS", 24)
    early_join_minutes: int = _as_int("RESERVATION_EARLY_JOIN_MINUTES", 15)
    max_default_occupants: int = _as_int("DEFAULT_MAX_OCCUPANTS", 30)
    enable_cors: bool = _as_bool("ENABLE_CORS", True)

    @property
    def normalized_jitsi_base_url(self) -> str:
        return self.jitsi_base_url.rstrip("/") + "/"


config = Config()

