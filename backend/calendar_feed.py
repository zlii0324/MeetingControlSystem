from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from config import config
from crypto import decrypt_password, encrypt_password
from database import process_write_lock, session_scope
from models import CalendarSubscriptionToken, User
from sqlalchemy import select
from services import list_meetings


CALENDAR_PRODUCT_ID = "-//Wusupower//Meeting Control System//EN"


class CalendarFeedError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _subscription_urls(token: str) -> tuple[str, str]:
    path = f"/api/calendar/subscriptions/{token}.ics"
    http_url = f"{config.meeting_link_origin.rstrip('/')}{path}"
    webcal_url = http_url.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1)
    return http_url, webcal_url


def ensure_calendar_subscription(user: dict[str, Any]) -> dict[str, Any]:
    user_id = int(user["id"])
    with process_write_lock():
        with session_scope() as session:
            row = session.get(CalendarSubscriptionToken, user_id)
            token: str | None = None
            created_at: str | None = None
            if row is not None:
                try:
                    token = decrypt_password(row.token_encrypted, config.password_secret)
                    created_at = row.created_at
                except Exception:
                    session.delete(row)
                    session.flush()

            if not token:
                token = secrets.token_urlsafe(32)
                created_at = _utc_timestamp()
                session.add(
                    CalendarSubscriptionToken(
                        user_id=user_id,
                        token_hash=_token_hash(token),
                        token_encrypted=encrypt_password(token, config.password_secret),
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )

    subscription_url, webcal_url = _subscription_urls(token)
    return {
        "subscriptionUrl": subscription_url,
        "webcalUrl": webcal_url,
        "createdAt": created_at,
    }


def _subscription_user(token: str) -> dict[str, Any]:
    normalized_token = str(token or "").strip()
    if not normalized_token or len(normalized_token) > 200:
        raise CalendarFeedError("日历订阅地址无效", 404)
    with session_scope() as session:
        user = session.scalar(
            select(User)
            .join(
                CalendarSubscriptionToken,
                CalendarSubscriptionToken.user_id == User.id,
            )
            .where(
                CalendarSubscriptionToken.token_hash == _token_hash(normalized_token),
                User.status == "active",
            )
        )
        if user is None:
            raise CalendarFeedError("日历订阅地址无效", 404)
        return {
            "id": user.id,
            "username": user.username,
            "displayName": user.display_name,
            "email": user.email,
            "jobTitle": user.job_title,
            "role": user.role,
            "status": user.status,
        }


def _parse_datetime(value: Any) -> datetime:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ical_datetime(value: Any) -> str:
    return _parse_datetime(value).strftime("%Y%m%dT%H%M%SZ")


def _escape_ical_text(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _fold_ical_line(line: str) -> list[str]:
    folded: list[str] = []
    remaining = line
    first_line = True
    while remaining:
        byte_limit = 75 if first_line else 74
        byte_count = 0
        split_at = 0
        for index, character in enumerate(remaining):
            character_bytes = len(character.encode("utf-8"))
            if byte_count + character_bytes > byte_limit:
                break
            byte_count += character_bytes
            split_at = index + 1
        if split_at == 0:
            split_at = 1
        chunk = remaining[:split_at]
        folded.append(chunk if first_line else f" {chunk}")
        remaining = remaining[split_at:]
        first_line = False
    return folded or [""]


def build_personal_calendar(token: str) -> tuple[str, str]:
    user = _subscription_user(token)
    meetings = sorted(
        list_meetings(user),
        key=lambda meeting: (meeting["startTime"], int(meeting["id"])),
    )
    calendar_name = f"{user['displayName']} · 个人会议"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{CALENDAR_PRODUCT_ID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_ical_text(calendar_name)}",
        "X-WR-TIMEZONE:UTC",
    ]

    for meeting in meetings:
        meeting_link = meeting.get("accessUrl") or meeting.get("meetingUrl") or ""
        description = f"主持人：{meeting.get('hostName') or '-'}"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:meeting-{meeting['id']}@meeting-control-system",
                f"DTSTAMP:{_ical_datetime(meeting.get('updatedAt') or meeting['startTime'])}",
                f"LAST-MODIFIED:{_ical_datetime(meeting.get('updatedAt') or meeting['startTime'])}",
                f"DTSTART:{_ical_datetime(meeting['startTime'])}",
                f"DTEND:{_ical_datetime(meeting['endTime'])}",
                f"SUMMARY:{_escape_ical_text(meeting['title'])}",
                f"DESCRIPTION:{_escape_ical_text(description)}",
                f"LOCATION:{_escape_ical_text(meeting_link)}",
                f"URL:{_escape_ical_text(meeting_link)}",
                "TRANSP:OPAQUE",
                "STATUS:CANCELLED" if meeting.get("status") == "Cancelled" else "STATUS:CONFIRMED",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    folded_lines = [folded for line in lines for folded in _fold_ical_line(line)]
    return "\r\n".join(folded_lines) + "\r\n", calendar_name
