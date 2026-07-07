from __future__ import annotations

import secrets
import sqlite3
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any

from config import config
from crypto import decrypt_password, encrypt_password, hash_password, verify_password
from database import get_connection


STATUSES = {"Scheduled", "Running", "Finished", "Cancelled"}
RECURRENCE_TYPES = {"weekly", "biweekly", "every_n_days", "monthly"}
MAX_RECURRENCE_COUNT = 1000
MAX_RECURRENCE_INTERVAL_DAYS = 365


class MeetingError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_datetime(value: str | None, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None

    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MeetingError(f"{field_name} 时间格式无效，请使用 ISO 8601 格式") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def generate_room_id() -> str:
    return f"{config.room_prefix}-{uuid.uuid4().hex[:12]}"


def generate_password() -> str:
    return f"{secrets.randbelow(10000):04d}"


def payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def parse_int(value: Any, field_name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MeetingError(f"{field_name} 必须是整数") from exc
    return parsed


def recurrence_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value = payload_value(payload, *keys, default=None)
    if value is not None:
        return value

    recurrence = payload.get("recurrence")
    if isinstance(recurrence, dict):
        return payload_value(recurrence, *keys, default=default)
    return default


def parse_recurrence(payload: dict[str, Any]) -> dict[str, Any] | None:
    recurrence_type_value = recurrence_value(payload, "recurrenceType", "recurrence_type", "type")
    recurrence_enabled_value = recurrence_value(
        payload,
        "recurrenceEnabled",
        "recurrence_enabled",
        "isRecurring",
        "is_recurring",
        "enabled",
    )
    recurrence_enabled = parse_bool(
        recurrence_enabled_value,
        False,
    )

    if recurrence_enabled_value is not None and not recurrence_enabled:
        return None
    if not recurrence_enabled and not recurrence_type_value:
        return None

    recurrence_type = str(recurrence_type_value or "weekly").strip()
    if recurrence_type not in RECURRENCE_TYPES:
        raise MeetingError("周期类型无效")

    count = parse_int(
        recurrence_value(payload, "recurrenceCount", "recurrence_count", "count", default=12),
        "生成次数",
        12,
    )
    if count < 2:
        raise MeetingError("周期性会议至少需要生成 2 次")
    if count > MAX_RECURRENCE_COUNT:
        raise MeetingError(f"周期性会议一次最多生成 {MAX_RECURRENCE_COUNT} 次")

    if recurrence_type == "every_n_days":
        interval = parse_int(
            recurrence_value(payload, "recurrenceInterval", "recurrence_interval", "interval", default=1),
            "周期间隔",
            1,
        )
        if interval < 1 or interval > MAX_RECURRENCE_INTERVAL_DAYS:
            raise MeetingError(f"每 N 天的间隔必须在 1 到 {MAX_RECURRENCE_INTERVAL_DAYS} 天之间")
    elif recurrence_type == "biweekly":
        interval = 2
    else:
        interval = 1

    return {
        "type": recurrence_type,
        "interval": interval,
        "count": count,
    }


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def recurrence_start_time(start_time: datetime, recurrence: dict[str, Any], index: int) -> datetime:
    recurrence_type = recurrence["type"]
    interval = recurrence["interval"]

    if recurrence_type == "weekly":
        return start_time + timedelta(weeks=index)
    if recurrence_type == "biweekly":
        return start_time + timedelta(weeks=index * 2)
    if recurrence_type == "every_n_days":
        return start_time + timedelta(days=index * interval)
    if recurrence_type == "monthly":
        return add_months(start_time, index)
    raise MeetingError("周期类型无效")


def recurrence_end_time(start_time: datetime, duration_seconds: int) -> datetime:
    return start_time + timedelta(seconds=duration_seconds)


def normalize_attendees(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw_items = value.replace(";", "\n").replace(",", "\n").splitlines()
    elif isinstance(value, list):
        raw_items = value
    else:
        raise MeetingError("参会者名单格式无效")

    attendees: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("displayName") or item.get("email") or "").strip()
        else:
            name = str(item).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        attendees.append(name)

    if len(attendees) > 200:
        raise MeetingError("参会者不能超过 200 人")
    return attendees


def jitsi_url(room_id: str) -> str:
    return config.normalized_jitsi_base_url + room_id


def access_url(room_id: str) -> str:
    base_url = (config.frontend_origin or "").strip()
    if base_url:
        return f"{base_url.rstrip('/')}/join/{room_id}"
    return f"/join/{room_id}"


def meeting_url(room_id: str, password_required: bool = True) -> str:
    if password_required:
        return access_url(room_id)
    return jitsi_url(room_id)


def is_password_required(meeting: dict[str, Any]) -> bool:
    return bool(meeting.get("password_required", 1))


def effective_status(meeting: dict[str, Any]) -> str:
    if meeting["status"] in {"Cancelled", "Finished"}:
        return meeting["status"]

    start_time = parse_datetime(meeting["start_time"], "startTime")
    end_time = parse_datetime(meeting["end_time"], "endTime")
    now = utc_now()
    if end_time and now > end_time:
        return "Finished"
    if start_time and end_time and start_time <= now <= end_time:
        return "Running"
    return "Scheduled"


def public_meeting(meeting: dict[str, Any], include_password: str | None = None) -> dict[str, Any]:
    password_required = is_password_required(meeting)
    room_id = meeting["room_id"]
    data = {
        "id": meeting["id"],
        "roomId": room_id,
        "title": meeting["title"],
        "hostName": meeting["host_name"],
        "mailOwner": meeting.get("mail_owner"),
        "startTime": meeting["start_time"],
        "endTime": meeting["end_time"],
        "durationSeconds": meeting["duration_seconds"],
        "status": effective_status(meeting),
        "maxOccupants": meeting["max_occupants"],
        "passwordRequired": password_required,
        "accessUrl": access_url(room_id),
        "jitsiUrl": None if password_required else meeting["meeting_url"],
        "meetingUrl": meeting_url(room_id, password_required),
        "createdAt": meeting["created_at"],
        "updatedAt": meeting["updated_at"],
        "lastStartedAt": meeting.get("last_started_at"),
        "finishedAt": meeting.get("finished_at"),
        "cancelledAt": meeting.get("cancelled_at"),
        "attendees": meeting.get("attendees", []),
        "seriesId": meeting.get("series_id"),
        "isRecurring": bool(meeting.get("series_id")),
        "recurrence": None,
    }
    if meeting.get("series_id"):
        data["recurrence"] = {
            "seriesId": meeting.get("series_id"),
            "type": meeting.get("recurrence_type"),
            "interval": meeting.get("recurrence_interval"),
            "count": meeting.get("recurrence_count"),
            "index": meeting.get("recurrence_index"),
        }
    if include_password is not None:
        data["password"] = include_password
    return data


def _get_attendees(conn: sqlite3.Connection, meeting_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT display_name
          FROM meeting_attendees
         WHERE meeting_id = ?
         ORDER BY sort_order ASC, id ASC
        """,
        (meeting_id,),
    ).fetchall()
    return [row["display_name"] for row in rows]


def _replace_attendees(conn: sqlite3.Connection, meeting_id: int, attendees: list[str]) -> None:
    conn.execute("DELETE FROM meeting_attendees WHERE meeting_id = ?", (meeting_id,))
    now = isoformat(utc_now())
    conn.executemany(
        """
        INSERT INTO meeting_attendees (meeting_id, display_name, sort_order, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [(meeting_id, attendee, index, now) for index, attendee in enumerate(attendees)],
    )


def _attach_attendees(conn: sqlite3.Connection, meeting: dict[str, Any]) -> dict[str, Any]:
    meeting["attendees"] = _get_attendees(conn, meeting["id"])
    return meeting


def _refresh_expired_status(conn: sqlite3.Connection, meeting: dict[str, Any]) -> dict[str, Any]:
    if meeting["status"] in {"Cancelled", "Finished"}:
        return meeting

    end_time = parse_datetime(meeting["end_time"], "endTime")
    if end_time and utc_now() > end_time:
        now = isoformat(utc_now())
        conn.execute(
            """
            UPDATE meetings
               SET status = 'Finished', finished_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (now, now, meeting["id"]),
        )
        meeting = dict(conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting["id"],)).fetchone())
    return meeting


def _select_meeting_for_room(meetings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not meetings:
        return None

    now = utc_now()
    earliest_datetime = datetime.min.replace(tzinfo=timezone.utc)
    joinable: list[tuple[datetime, int, dict[str, Any]]] = []
    upcoming: list[tuple[datetime, int, dict[str, Any]]] = []
    fallback: list[tuple[datetime, int, dict[str, Any]]] = []

    for meeting in meetings:
        start_time = parse_datetime(meeting["start_time"], "startTime") or earliest_datetime
        end_time = parse_datetime(meeting["end_time"], "endTime") or start_time
        fallback.append((start_time, int(meeting["id"]), meeting))

        if meeting["status"] in {"Cancelled", "Finished"}:
            continue

        earliest_join_time = start_time - timedelta(minutes=config.early_join_minutes)
        if earliest_join_time <= now <= end_time:
            joinable.append((start_time, int(meeting["id"]), meeting))
        elif start_time > now:
            upcoming.append((start_time, int(meeting["id"]), meeting))

    if joinable:
        return sorted(joinable, key=lambda item: (item[0], item[1]))[0][2]
    if upcoming:
        return sorted(upcoming, key=lambda item: (item[0], item[1]))[0][2]
    return sorted(fallback, key=lambda item: (item[0], item[1]), reverse=True)[0][2]


def list_meetings(status: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if status and status not in STATUSES:
            raise MeetingError("会议状态参数无效")

        rows = conn.execute(
            """
            SELECT * FROM meetings
             ORDER BY start_time DESC, id DESC
            """
        ).fetchall()

        meetings = [_attach_attendees(conn, _refresh_expired_status(conn, dict(row))) for row in rows]
        conn.commit()
    public_meetings = [public_meeting(meeting) for meeting in meetings]
    if status:
        return [meeting for meeting in public_meetings if meeting["status"] == status]
    return public_meetings


def get_meeting(meeting_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise MeetingError("会议不存在", 404)

        meeting = _refresh_expired_status(conn, dict(row))
        meeting = _attach_attendees(conn, meeting)
        conn.commit()
    return public_meeting(meeting)


def create_meeting(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    host_name = str(payload.get("hostName") or payload.get("host_name") or "").strip()
    mail_owner = str(payload.get("mailOwner") or payload.get("mail_owner") or "").strip() or None

    if not title:
        raise MeetingError("会议标题不能为空")
    if not host_name:
        raise MeetingError("主持人不能为空")

    start_time = parse_datetime(payload.get("startTime") or payload.get("start_time"), "startTime") or utc_now()
    end_time = parse_datetime(payload.get("endTime") or payload.get("end_time"), "endTime")
    if end_time is None:
        end_time = start_time + timedelta(hours=config.default_duration_hours)

    if end_time <= start_time:
        raise MeetingError("结束时间必须晚于开始时间")

    max_occupants = int(
        payload_value(payload, "maxOccupants", "max_occupants", default=config.max_default_occupants)
        or config.max_default_occupants
    )
    if max_occupants < 1 or max_occupants > 500:
        raise MeetingError("最大参会人数必须在 1 到 500 之间")

    attendees = normalize_attendees(payload.get("attendees"))
    password_required = parse_bool(payload_value(payload, "passwordRequired", "password_required"), True)
    room_id = generate_room_id()
    password = generate_password() if password_required else None
    password_hash_value = hash_password(password) if password else ""
    password_encrypted_value = encrypt_password(password, config.password_secret) if password else ""
    now = isoformat(utc_now())
    duration_seconds = int((end_time - start_time).total_seconds())
    recurrence = parse_recurrence(payload)
    occurrence_count = recurrence["count"] if recurrence else 1
    series_id = uuid.uuid4().hex if recurrence else None
    meeting_ids: list[int] = []

    with get_connection() as conn:
        try:
            while conn.execute("SELECT 1 FROM meetings WHERE room_id = ? LIMIT 1", (room_id,)).fetchone():
                room_id = generate_room_id()

            for index in range(occurrence_count):
                occurrence_room_id = room_id
                occurrence_start_time = (
                    recurrence_start_time(start_time, recurrence, index) if recurrence else start_time
                )
                occurrence_end_time = recurrence_end_time(occurrence_start_time, duration_seconds)
                cursor = conn.execute(
                    """
                    INSERT INTO meetings (
                        room_id, title, host_name, mail_owner, start_time, end_time,
                        duration_seconds, status, password_required, password_hash, password_encrypted,
                        max_occupants, lobby_enabled, meeting_url, series_id, recurrence_type,
                        recurrence_interval, recurrence_count, recurrence_index, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        occurrence_room_id,
                        title,
                        host_name,
                        mail_owner,
                        isoformat(occurrence_start_time),
                        isoformat(occurrence_end_time),
                        duration_seconds,
                        1 if password_required else 0,
                        password_hash_value,
                        password_encrypted_value,
                        max_occupants,
                        0,
                        jitsi_url(occurrence_room_id),
                        series_id,
                        recurrence["type"] if recurrence else None,
                        recurrence["interval"] if recurrence else 1,
                        recurrence["count"] if recurrence else None,
                        index if recurrence else None,
                        now,
                        now,
                    ),
                )
                meeting_id = int(cursor.lastrowid)
                meeting_ids.append(meeting_id)
                _replace_attendees(conn, meeting_id, attendees)
        except sqlite3.IntegrityError as exc:
            raise MeetingError("Room ID 冲突，请重试") from exc

        placeholders = ",".join("?" for _ in meeting_ids)
        rows = conn.execute(
            f"""
            SELECT * FROM meetings
             WHERE id IN ({placeholders})
             ORDER BY COALESCE(recurrence_index, 0) ASC, id ASC
            """,
            tuple(meeting_ids),
        ).fetchall()
        meetings = [_attach_attendees(conn, dict(row)) for row in rows]

    response = public_meeting(meetings[0], include_password=password)
    if recurrence:
        response["createdCount"] = len(meetings)
        response["seriesMeetings"] = [public_meeting(meeting) for meeting in meetings]
    return response


def update_meeting(meeting_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise MeetingError("会议不存在", 404)

        meeting = _refresh_expired_status(conn, dict(row))
        if meeting["status"] in {"Finished", "Cancelled"}:
            raise MeetingError("已结束或已取消的会议不能修改", 409)

        title = str(payload.get("title", meeting["title"])).strip()
        host_name = str(payload.get("hostName") or payload.get("host_name") or meeting["host_name"]).strip()
        mail_owner = payload.get("mailOwner", payload.get("mail_owner", meeting["mail_owner"]))
        mail_owner = str(mail_owner).strip() if mail_owner else None
        start_time = parse_datetime(payload.get("startTime") or payload.get("start_time"), "startTime")
        end_time = parse_datetime(payload.get("endTime") or payload.get("end_time"), "endTime")

        start_time = start_time or parse_datetime(meeting["start_time"], "startTime")
        end_time = end_time or parse_datetime(meeting["end_time"], "endTime")
        if start_time is None or end_time is None:
            raise MeetingError("会议时间无效")
        if end_time <= start_time:
            raise MeetingError("结束时间必须晚于开始时间")

        max_occupants = int(
            payload_value(payload, "maxOccupants", "max_occupants", default=meeting["max_occupants"])
            or meeting["max_occupants"]
        )
        if max_occupants < 1 or max_occupants > 500:
            raise MeetingError("最大参会人数必须在 1 到 500 之间")

        attendees_present = "attendees" in payload
        attendees = normalize_attendees(payload.get("attendees")) if attendees_present else []
        password_required = is_password_required(meeting)
        new_password: str | None = None
        password_hash_value = meeting["password_hash"]
        password_encrypted_value = meeting["password_encrypted"]
        if "passwordRequired" in payload or "password_required" in payload:
            password_required = parse_bool(
                payload_value(payload, "passwordRequired", "password_required"),
                password_required,
            )
            if password_required and (
                not is_password_required(meeting)
                or not password_hash_value
                or not password_encrypted_value
            ):
                new_password = generate_password()
                password_hash_value = hash_password(new_password)
                password_encrypted_value = encrypt_password(new_password, config.password_secret)
            if not password_required:
                password_hash_value = ""
                password_encrypted_value = ""
        duration_seconds = int((end_time - start_time).total_seconds())
        now = isoformat(utc_now())

        conn.execute(
            """
            UPDATE meetings
               SET title = ?,
                   host_name = ?,
                   mail_owner = ?,
                   start_time = ?,
                   end_time = ?,
                   duration_seconds = ?,
                   password_required = ?,
                   password_hash = ?,
                   password_encrypted = ?,
                   max_occupants = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                title,
                host_name,
                mail_owner,
                isoformat(start_time),
                isoformat(end_time),
                duration_seconds,
                1 if password_required else 0,
                password_hash_value,
                password_encrypted_value,
                max_occupants,
                now,
                meeting_id,
            ),
        )
        if attendees_present:
            _replace_attendees(conn, meeting_id, attendees)
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        meeting = _attach_attendees(conn, dict(row))
        conn.commit()

    return public_meeting(meeting, include_password=new_password)


def delete_meeting(meeting_id: int, scope: str | None = None) -> dict[str, Any]:
    normalized_scope = str(scope or "single").strip().lower()
    if normalized_scope in {"", "meeting", "one"}:
        normalized_scope = "single"
    if normalized_scope not in {"single", "series"}:
        raise MeetingError("删除范围无效")

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise MeetingError("会议不存在", 404)

        if normalized_scope == "series" and row["series_id"]:
            rows = conn.execute(
                """
                SELECT id
                  FROM meetings
                 WHERE series_id = ?
                 ORDER BY COALESCE(recurrence_index, 0) ASC, id ASC
                """,
                (row["series_id"],),
            ).fetchall()
            meeting_ids = [item["id"] for item in rows]
            conn.execute("DELETE FROM meetings WHERE series_id = ?", (row["series_id"],))
            return {
                "id": meeting_id,
                "ids": meeting_ids,
                "seriesId": row["series_id"],
                "deleted": True,
                "deletedCount": len(meeting_ids),
            }

        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    return {"id": meeting_id, "deleted": True}


def get_meeting_by_room(room_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meetings
             WHERE room_id = ?
             ORDER BY start_time ASC, COALESCE(recurrence_index, 0) ASC, id ASC
            """,
            (room_id,),
        ).fetchall()
        if not rows:
            return None

        meetings = [_refresh_expired_status(conn, dict(row)) for row in rows]
        meeting = _select_meeting_for_room(meetings)
        conn.commit()
        return meeting


def get_meeting_for_reservation(meeting_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise MeetingError("会议不存在", 404)

        meeting = _refresh_expired_status(conn, dict(row))
        conn.commit()
    return meeting


def reservation_payload(meeting: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": meeting["id"],
        "name": meeting["room_id"],
        "mail_owner": meeting["mail_owner"] or meeting["host_name"],
        "start_time": meeting["start_time"],
        "duration": meeting["duration_seconds"],
        "max_occupants": meeting["max_occupants"],
    }
    if is_password_required(meeting) and meeting["password_encrypted"]:
        payload["password"] = decrypt_password(meeting["password_encrypted"], config.password_secret)
    return payload


def public_join_meeting(room_id: str) -> dict[str, Any]:
    meeting = get_meeting_by_room(room_id.strip())
    if meeting is None:
        raise MeetingError("会议不存在", 404)

    password_required = is_password_required(meeting)
    payload: dict[str, Any] = {
        "roomId": meeting["room_id"],
        "title": meeting["title"],
        "hostName": meeting["host_name"],
        "startTime": meeting["start_time"],
        "endTime": meeting["end_time"],
        "status": effective_status(meeting),
        "passwordRequired": password_required,
    }
    if not password_required:
        payload["jitsiUrl"] = meeting["meeting_url"]
    return payload


def verify_join_password(
    room_id: str,
    password: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    room_id = room_id.strip()
    meeting = get_meeting_by_room(room_id)
    if meeting is None:
        log_access(room_id, None, ip_address, user_agent, False, "会议不存在")
        raise MeetingError("会议不存在", 404)

    if meeting["status"] == "Cancelled":
        log_access(room_id, meeting["id"], ip_address, user_agent, False, "会议已取消")
        raise MeetingError("会议已取消", 403)
    if meeting["status"] == "Finished":
        log_access(room_id, meeting["id"], ip_address, user_agent, False, "会议已结束")
        raise MeetingError("会议已结束", 403)

    if not is_password_required(meeting):
        log_access(room_id, meeting["id"], ip_address, user_agent, True)
        return {"jitsiUrl": meeting["meeting_url"]}

    if not password or not verify_password(str(password), meeting["password_hash"]):
        log_access(room_id, meeting["id"], ip_address, user_agent, False, "密码错误")
        raise MeetingError("密码错误", 403)

    log_access(room_id, meeting["id"], ip_address, user_agent, True)
    return {"jitsiUrl": meeting["meeting_url"]}


def log_access(
    room_id: str,
    meeting_id: int | None,
    ip_address: str | None,
    user_agent: str | None,
    success: bool,
    fail_reason: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO access_logs (
                meeting_id, room_id, ip_address, user_agent, join_time, success, fail_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id,
                room_id,
                ip_address,
                user_agent,
                isoformat(utc_now()),
                1 if success else 0,
                fail_reason,
            ),
        )


def allocate_conference(
    room_name: str,
    mail_owner: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[int, dict[str, Any]]:
    room_name = room_name.strip()
    if not room_name:
        log_access("", None, ip_address, user_agent, False, "缺少会议房间名")
        raise MeetingError("缺少会议房间名", 400)

    meeting = get_meeting_by_room(room_name)
    if meeting is None:
        log_access(room_name, None, ip_address, user_agent, False, "会议不存在")
        raise MeetingError("会议不存在", 403)

    if meeting["status"] == "Cancelled":
        log_access(room_name, meeting["id"], ip_address, user_agent, False, "会议已取消")
        raise MeetingError("会议已取消", 403)

    if meeting["status"] == "Finished":
        log_access(room_name, meeting["id"], ip_address, user_agent, False, "会议已结束")
        raise MeetingError("会议已结束", 403)

    now = utc_now()
    start_time = parse_datetime(meeting["start_time"], "startTime")
    end_time = parse_datetime(meeting["end_time"], "endTime")
    if start_time is None or end_time is None:
        log_access(room_name, meeting["id"], ip_address, user_agent, False, "会议时间无效")
        raise MeetingError("会议时间无效", 500)

    earliest = start_time - timedelta(minutes=config.early_join_minutes)
    if now < earliest:
        log_access(room_name, meeting["id"], ip_address, user_agent, False, "会议尚未开始")
        raise MeetingError("会议尚未开始", 403)

    if now > end_time:
        log_access(room_name, meeting["id"], ip_address, user_agent, False, "会议已超过预约时间")
        raise MeetingError("会议已超过预约时间", 403)

    if meeting["status"] == "Running":
        log_access(room_name, meeting["id"], ip_address, user_agent, True)
        return 409, {"conflict_id": meeting["id"]}

    now_text = isoformat(now)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE meetings
               SET status = 'Running',
                   mail_owner = COALESCE(?, mail_owner),
                   last_started_at = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (mail_owner, now_text, now_text, meeting["id"]),
        )
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting["id"],)).fetchone()

    log_access(room_name, meeting["id"], ip_address, user_agent, True)
    return 201, reservation_payload(dict(row))


def finish_conference(meeting_id: int) -> dict[str, Any]:
    now = isoformat(utc_now())
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is None:
            raise MeetingError("会议不存在", 404)

        if row["status"] != "Cancelled":
            conn.execute(
                """
                UPDATE meetings
                   SET status = 'Finished', finished_at = ?, updated_at = ?
                 WHERE id = ?
                """,
                (now, now, meeting_id),
            )
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()

    return public_meeting(dict(row))


def list_access_logs(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT access_logs.*, meetings.title
              FROM access_logs
              LEFT JOIN meetings ON meetings.id = access_logs.meeting_id
             ORDER BY access_logs.id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
