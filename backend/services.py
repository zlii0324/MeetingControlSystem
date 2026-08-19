from __future__ import annotations

import secrets
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import config
from crypto import decrypt_password, encrypt_password, hash_password, verify_password
from database import process_write_lock, session_scope
from groups import resolve_group_attendee_emails
from jitsi_auth import create_jitsi_token
from mailer import (
    normalize_email_address,
    send_meeting_cancellation_notifications,
    send_meeting_invitation_notifications,
    send_meeting_removal_notifications,
    send_meeting_update_notifications,
)
from models import AccessLog, Meeting, MeetingAttendee, User, model_to_dict


STATUSES = {"Scheduled", "Running", "Finished", "Cancelled"}
RECURRENCE_TYPES = {"weekly", "biweekly", "every_n_days", "monthly"}
MAX_RECURRENCE_COUNT = 1000
MAX_RECURRENCE_INTERVAL_DAYS = 365
MAX_ATTENDEES = 500
ALL_ATTENDEES_TOKEN = "@all"
GROUP_ATTENDEES_PREFIX = "@group:"


class MeetingError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def with_process_write_lock(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with process_write_lock():
            return function(*args, **kwargs)

    return wrapper


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

    if len(attendees) > MAX_ATTENDEES:
        raise MeetingError(f"参会者不能超过 {MAX_ATTENDEES} 人")
    return attendees


def _expand_special_attendees(
    session: Session,
    attendees: list[str],
    actor_user: dict[str, Any],
) -> list[str]:
    has_all_token = any(attendee.casefold() == ALL_ATTENDEES_TOKEN for attendee in attendees)
    has_group_token = any(
        attendee.casefold().startswith(GROUP_ATTENDEES_PREFIX) for attendee in attendees
    )
    if not has_all_token and not has_group_token:
        return attendees

    active_user_emails: list[str] = []
    if has_all_token:
        user_rows = session.scalars(
            select(User)
            .where(User.status == "active")
            .order_by(func.lower(User.display_name), func.lower(User.username))
        ).all()
        active_user_emails = [
            email
            for user in user_rows
            if (email := normalize_email_address(user.email)) is not None
        ]

    expanded: list[str] = []
    seen: set[str] = set()
    for attendee in attendees:
        normalized_attendee = attendee.casefold()
        if normalized_attendee == ALL_ATTENDEES_TOKEN:
            candidates = active_user_emails
        elif normalized_attendee.startswith(GROUP_ATTENDEES_PREFIX):
            try:
                group_id = int(attendee.split(":", 1)[1])
            except (IndexError, ValueError) as exc:
                raise MeetingError("用户组选择无效") from exc
            candidates = resolve_group_attendee_emails(session, group_id, actor_user)
        else:
            candidates = [attendee]
        for candidate in candidates:
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            expanded.append(candidate)

    if len(expanded) > MAX_ATTENDEES:
        raise MeetingError(f"参会者不能超过 {MAX_ATTENDEES} 人")
    return expanded


def _meeting_includes_user_as_attendee(meeting: dict[str, Any], user: dict[str, Any]) -> bool:
    username = str(user.get("username") or "").strip().casefold()
    identifiers = {
        str(user.get("email") or "").strip().casefold(),
        username,
        f"@{username}" if username else "",
        str(user.get("displayName") or user.get("display_name") or "").strip().casefold(),
    }
    identifiers.discard("")
    return any(
        str(attendee).strip().casefold() in identifiers
        for attendee in meeting.get("attendees", [])
    )


def _resolve_attendee_email_addresses(
    session: Session,
    attendees: list[str],
) -> list[str]:
    identifier_emails: dict[str, set[str]] = {}
    opted_out_emails: set[str] = set()
    users = session.scalars(select(User).where(User.status == "active")).all()
    for user in users:
        email = normalize_email_address(user.email)
        if email is None:
            continue
        if not bool(user.email_notifications_enabled):
            opted_out_emails.add(email.casefold())
            continue
        identifiers = {
            str(user.username or "").strip().casefold(),
            f"@{str(user.username or '').strip().casefold()}",
            str(user.display_name or "").strip().casefold(),
            email.casefold(),
        }
        for identifier in identifiers:
            if identifier:
                identifier_emails.setdefault(identifier, set()).add(email)

    resolved: list[str] = []
    seen: set[str] = set()
    for attendee in attendees:
        direct_email = normalize_email_address(attendee)
        if direct_email:
            candidates = {direct_email}
        else:
            candidates = identifier_emails.get(str(attendee).strip().casefold(), set())
        if len(candidates) != 1:
            continue
        email = next(iter(candidates))
        if email.casefold() in opted_out_emails:
            continue
        if email in seen:
            continue
        seen.add(email)
        resolved.append(email)
    return resolved


def jitsi_url(room_id: str) -> str:
    return config.normalized_jitsi_base_url + room_id


def jitsi_join_url(
    room_id: str,
    *,
    display_name: str | None = None,
    email: str | None = None,
    user_id: str | int | None = None,
) -> str:
    url = jitsi_url(room_id)
    if not config.jitsi_jwt_enabled:
        return url
    token = create_jitsi_token(
        room_id,
        display_name=display_name,
        email=email,
        user_id=user_id,
    )
    return f"{url}?jwt={token}"


def access_url(room_id: str) -> str:
    base_url = config.meeting_link_origin.strip()
    if base_url:
        return f"{base_url.rstrip('/')}/join/{room_id}"
    return f"/join/{room_id}"


def meeting_url(room_id: str, password_required: bool = False) -> str:
    if password_required or config.jitsi_jwt_enabled:
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
    join_verification_required = password_required or config.jitsi_jwt_enabled
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
        "jitsiUrl": None if join_verification_required else jitsi_url(room_id),
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


def _get_attendees(session: Session, meeting_id: int) -> list[str]:
    return list(
        session.scalars(
            select(MeetingAttendee.display_name)
            .where(MeetingAttendee.meeting_id == meeting_id)
            .order_by(MeetingAttendee.sort_order, MeetingAttendee.id)
        ).all()
    )


def _replace_attendees(session: Session, meeting_id: int, attendees: list[str]) -> None:
    existing = session.scalars(
        select(MeetingAttendee).where(MeetingAttendee.meeting_id == meeting_id)
    ).all()
    for attendee in existing:
        session.delete(attendee)
    now = isoformat(utc_now())
    session.add_all(
        [
            MeetingAttendee(
                meeting_id=meeting_id,
                display_name=attendee,
                sort_order=index,
                created_at=now,
            )
            for index, attendee in enumerate(attendees)
        ]
    )
    session.flush()


def _attach_attendees(session: Session, meeting: dict[str, Any]) -> dict[str, Any]:
    meeting["attendees"] = _get_attendees(session, meeting["id"])
    return meeting


def _refresh_expired_status(session: Session, meeting: dict[str, Any]) -> dict[str, Any]:
    if meeting["status"] in {"Cancelled", "Finished"}:
        return meeting

    end_time = parse_datetime(meeting["end_time"], "endTime")
    if end_time and utc_now() > end_time:
        now = isoformat(utc_now())
        model = session.get(Meeting, meeting["id"])
        model.status = "Finished"
        model.finished_at = now
        model.updated_at = now
        session.flush()
        meeting = model_to_dict(model)
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


@with_process_write_lock
def list_meetings(attendee_user: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as session:
        if status and status not in STATUSES:
            raise MeetingError("会议状态参数无效")
        models = session.scalars(
            select(Meeting).order_by(Meeting.start_time.desc(), Meeting.id.desc())
        ).all()
        meetings = [
            _attach_attendees(
                session,
                _refresh_expired_status(session, model_to_dict(model)),
            )
            for model in models
        ]
    public_meetings = [
        public_meeting(meeting)
        for meeting in meetings
        if _meeting_includes_user_as_attendee(meeting, attendee_user)
    ]
    if status:
        return [meeting for meeting in public_meetings if meeting["status"] == status]
    return public_meetings


@with_process_write_lock
def get_meeting(meeting_id: int) -> dict[str, Any]:
    with session_scope() as session:
        model = session.get(Meeting, meeting_id)
        if model is None:
            raise MeetingError("会议不存在", 404)
        meeting = _refresh_expired_status(session, model_to_dict(model))
        meeting = _attach_attendees(session, meeting)
    return public_meeting(meeting)


@with_process_write_lock
def _create_meeting(
    payload: dict[str, Any],
    actor_user: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str | None, int]:
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

    default_attendees = [actor_user["email"]] if actor_user.get("email") else []
    attendees = normalize_attendees(
        payload["attendees"] if "attendees" in payload else default_attendees
    )
    password_required = parse_bool(payload_value(payload, "passwordRequired", "password_required"), False)
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

    with session_scope() as session:
        try:
            attendees = _expand_special_attendees(session, attendees, actor_user)
            while session.scalar(
                select(Meeting.id).where(Meeting.room_id == room_id).limit(1)
            ) is not None:
                room_id = generate_room_id()

            for index in range(occurrence_count):
                occurrence_room_id = room_id
                occurrence_start_time = (
                    recurrence_start_time(start_time, recurrence, index) if recurrence else start_time
                )
                occurrence_end_time = recurrence_end_time(occurrence_start_time, duration_seconds)
                model = Meeting(
                    room_id=occurrence_room_id,
                    title=title,
                    host_name=host_name,
                    mail_owner=mail_owner,
                    start_time=isoformat(occurrence_start_time),
                    end_time=isoformat(occurrence_end_time),
                    duration_seconds=duration_seconds,
                    status="Scheduled",
                    password_required=password_required,
                    password_hash=password_hash_value,
                    password_encrypted=password_encrypted_value,
                    max_occupants=max_occupants,
                    lobby_enabled=False,
                    meeting_url=jitsi_url(occurrence_room_id),
                    series_id=series_id,
                    recurrence_type=recurrence["type"] if recurrence else None,
                    recurrence_interval=recurrence["interval"] if recurrence else 1,
                    recurrence_count=recurrence["count"] if recurrence else None,
                    recurrence_index=index if recurrence else None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
                session.flush()
                meeting_id = model.id
                meeting_ids.append(meeting_id)
                _replace_attendees(session, meeting_id, attendees)
        except IntegrityError as exc:
            raise MeetingError("Room ID 冲突，请重试") from exc
        models = session.scalars(
            select(Meeting)
            .where(Meeting.id.in_(meeting_ids))
            .order_by(func.coalesce(Meeting.recurrence_index, 0), Meeting.id)
        ).all()
        meetings = [
            _attach_attendees(session, model_to_dict(model)) for model in models
        ]
        notification_recipients = _resolve_attendee_email_addresses(session, attendees)

    response = public_meeting(meetings[0], include_password=password)
    if recurrence:
        response["createdCount"] = len(meetings)
        response["seriesMeetings"] = [public_meeting(meeting) for meeting in meetings]
    return response, notification_recipients, password, len(meetings)


def create_meeting(payload: dict[str, Any], actor_user: dict[str, Any]) -> dict[str, Any]:
    response, notification_recipients, password, occurrence_count = _create_meeting(
        payload,
        actor_user,
    )
    response["emailNotification"] = send_meeting_invitation_notifications(
        response,
        notification_recipients,
        password=password,
        recurrence_count=occurrence_count,
    )
    return response


def _normalize_recurrence_scope(scope: str | None, action: str) -> str:
    normalized_scope = str(scope or "single").strip().lower()
    aliases = {"": "single", "meeting": "single", "one": "single", "future": "following"}
    normalized_scope = aliases.get(normalized_scope, normalized_scope)
    if normalized_scope not in {"single", "following", "series"}:
        raise MeetingError(f"{action}范围无效")
    return normalized_scope


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _combine_notification_results(*results: dict[str, Any]) -> dict[str, Any]:
    requested = sum(int(result.get("requested", 0)) for result in results)
    sent = sum(int(result.get("sent", 0)) for result in results)
    if requested == 0:
        status = "not_requested"
    elif sent == requested:
        status = "sent"
    elif sent > 0:
        status = "partial"
    elif all(
        result.get("status") in {"disabled", "not_requested"}
        for result in results
    ):
        status = "disabled"
    else:
        status = "failed"
    return {"status": status, "requested": requested, "sent": sent}


def _not_requested_notification() -> dict[str, Any]:
    return {"status": "not_requested", "requested": 0, "sent": 0}


def _scope_meetings(
    session: Session,
    selected: dict[str, Any],
    scope: str,
) -> list[dict[str, Any]]:
    if scope != "single" and not selected.get("series_id"):
        raise MeetingError("非周期会议只能操作本次会议")
    if scope == "single":
        rows = [selected]
    elif scope == "following":
        models = session.scalars(
            select(Meeting)
            .where(
                Meeting.series_id == selected["series_id"],
                func.coalesce(Meeting.recurrence_index, 0)
                >= (selected.get("recurrence_index") or 0),
            )
            .order_by(func.coalesce(Meeting.recurrence_index, 0), Meeting.id)
        ).all()
        rows = [model_to_dict(model) for model in models]
    else:
        models = session.scalars(
            select(Meeting)
            .where(Meeting.series_id == selected["series_id"])
            .order_by(func.coalesce(Meeting.recurrence_index, 0), Meeting.id)
        ).all()
        rows = [model_to_dict(model) for model in models]

    return [
        _attach_attendees(session, _refresh_expired_status(session, dict(meeting)))
        for meeting in rows
    ]


def _meeting_recipient_union(
    session: Session,
    meetings: list[dict[str, Any]],
) -> list[str]:
    return _ordered_unique(
        [
            email
            for meeting in meetings
            for email in _resolve_attendee_email_addresses(session, meeting.get("attendees", []))
        ]
    )


@with_process_write_lock
def _update_meeting(
    meeting_id: int,
    payload: dict[str, Any],
    actor_user: dict[str, Any],
    scope: str | None = None,
) -> tuple[dict[str, Any], dict[str, list[str]], list[str], str | None]:
    normalized_scope = _normalize_recurrence_scope(scope, "修改")
    with session_scope() as session:
        selected_model = session.get(Meeting, meeting_id)
        if selected_model is None:
            raise MeetingError("会议不存在", 404)

        selected = _attach_attendees(
            session,
            _refresh_expired_status(session, model_to_dict(selected_model)),
        )
        if selected["status"] in {"Finished", "Cancelled"}:
            raise MeetingError("已结束或已取消的会议不能修改", 409)
        target_meetings = [
            meeting
            for meeting in _scope_meetings(session, selected, normalized_scope)
            if meeting["status"] not in {"Finished", "Cancelled"}
        ]
        if not target_meetings:
            raise MeetingError("没有可修改的会议", 409)

        title = str(payload.get("title", selected["title"])).strip()
        host_name = str(
            payload.get("hostName") or payload.get("host_name") or selected["host_name"]
        ).strip()
        if not title:
            raise MeetingError("会议标题不能为空")
        if not host_name:
            raise MeetingError("主持人不能为空")
        mail_owner = payload.get("mailOwner", payload.get("mail_owner", selected["mail_owner"]))
        mail_owner = str(mail_owner).strip() if mail_owner else None

        selected_start = parse_datetime(selected["start_time"], "startTime")
        selected_end = parse_datetime(selected["end_time"], "endTime")
        requested_start = parse_datetime(
            payload.get("startTime") or payload.get("start_time"), "startTime"
        ) or selected_start
        requested_end = parse_datetime(
            payload.get("endTime") or payload.get("end_time"), "endTime"
        ) or selected_end
        if not selected_start or not selected_end or not requested_start or not requested_end:
            raise MeetingError("会议时间无效")
        if requested_end <= requested_start:
            raise MeetingError("结束时间必须晚于开始时间")
        start_delta = requested_start - selected_start
        end_delta = requested_end - selected_end

        max_occupants = int(
            payload_value(
                payload,
                "maxOccupants",
                "max_occupants",
                default=selected["max_occupants"],
            )
            or selected["max_occupants"]
        )
        if max_occupants < 1 or max_occupants > 500:
            raise MeetingError("最大参会人数必须在 1 到 500 之间")

        attendees_present = "attendees" in payload
        attendees = normalize_attendees(payload.get("attendees")) if attendees_present else []
        if attendees_present:
            attendees = _expand_special_attendees(session, attendees, actor_user)
        previous_notification_recipients = _meeting_recipient_union(session, target_meetings)

        password_setting_present = "passwordRequired" in payload or "password_required" in payload
        password_required = (
            parse_bool(
                payload_value(payload, "passwordRequired", "password_required"),
                is_password_required(selected),
            )
            if password_setting_present
            else is_password_required(selected)
        )
        new_password: str | None = None
        shared_password_hash: str | None = None
        shared_password_encrypted: str | None = None
        if password_setting_present and password_required and any(
            not is_password_required(meeting)
            or not meeting.get("password_hash")
            or not meeting.get("password_encrypted")
            for meeting in target_meetings
        ):
            new_password = generate_password()
            shared_password_hash = hash_password(new_password)
            shared_password_encrypted = encrypt_password(new_password, config.password_secret)

        changes: list[str] = []
        if any(meeting["title"] != title for meeting in target_meetings):
            changes.append("会议名称")
        if any(meeting["host_name"] != host_name for meeting in target_meetings):
            changes.append("主持人")
        if start_delta or end_delta:
            changes.append("会议时间")
        if password_setting_present and any(
            is_password_required(meeting) != password_required for meeting in target_meetings
        ):
            changes.append("会议链接")

        now = isoformat(utc_now())
        for meeting in target_meetings:
            original_start = parse_datetime(meeting["start_time"], "startTime")
            original_end = parse_datetime(meeting["end_time"], "endTime")
            if not original_start or not original_end:
                raise MeetingError("会议时间无效")
            updated_start = original_start + start_delta
            updated_end = original_end + end_delta
            if updated_end <= updated_start:
                raise MeetingError("结束时间必须晚于开始时间")

            target_password_required = password_required if password_setting_present else is_password_required(meeting)
            if password_setting_present and not target_password_required:
                password_hash_value = ""
                password_encrypted_value = ""
            elif shared_password_hash and shared_password_encrypted:
                password_hash_value = shared_password_hash
                password_encrypted_value = shared_password_encrypted
            else:
                password_hash_value = meeting["password_hash"]
                password_encrypted_value = meeting["password_encrypted"]

            model = session.get(Meeting, meeting["id"])
            model.title = title
            model.host_name = host_name
            model.mail_owner = mail_owner
            model.start_time = isoformat(updated_start)
            model.end_time = isoformat(updated_end)
            model.duration_seconds = int((updated_end - updated_start).total_seconds())
            model.password_required = target_password_required
            model.password_hash = password_hash_value
            model.password_encrypted = password_encrypted_value
            model.max_occupants = max_occupants
            model.updated_at = now
            if attendees_present:
                _replace_attendees(session, meeting["id"], attendees)

        affected_ids = [meeting["id"] for meeting in target_meetings]
        session.flush()
        updated_models = session.scalars(
            select(Meeting)
            .where(Meeting.id.in_(affected_ids))
            .order_by(func.coalesce(Meeting.recurrence_index, 0), Meeting.id)
        ).all()
        updated_meetings = [
            _attach_attendees(session, model_to_dict(model))
            for model in updated_models
        ]
        updated_by_id = {meeting["id"]: meeting for meeting in updated_meetings}
        selected_updated = updated_by_id[meeting_id]
        current_notification_recipients = _meeting_recipient_union(session, updated_meetings)

    previous_recipient_set = set(previous_notification_recipients)
    current_recipient_set = set(current_notification_recipients)
    recipient_groups = {
        "added": [email for email in current_notification_recipients if email not in previous_recipient_set],
        "removed": [email for email in previous_notification_recipients if email not in current_recipient_set],
        "retained": [email for email in current_notification_recipients if email in previous_recipient_set],
    }
    notification_password = new_password
    if (
        (recipient_groups["added"] or (changes and recipient_groups["retained"]))
        and is_password_required(selected_updated)
        and not notification_password
    ):
        try:
            notification_password = decrypt_password(
                selected_updated["password_encrypted"],
                config.password_secret,
            )
        except Exception:
            notification_password = None

    response = public_meeting(selected_updated, include_password=new_password)
    response.update(
        affectedIds=affected_ids,
        affectedCount=len(affected_ids),
        affectedMeetings=[public_meeting(meeting) for meeting in updated_meetings],
        updateScope=normalized_scope,
        changes=changes,
    )
    return response, recipient_groups, changes, notification_password


def update_meeting(
    meeting_id: int,
    payload: dict[str, Any],
    actor_user: dict[str, Any],
    scope: str | None = None,
) -> dict[str, Any]:
    response, recipient_groups, changes, notification_password = _update_meeting(
        meeting_id,
        payload,
        actor_user,
        scope,
    )
    invitation_result = (
        send_meeting_invitation_notifications(
            response,
            recipient_groups["added"],
            password=notification_password,
            recurrence_count=response["affectedCount"],
        )
        if recipient_groups["added"]
        else _not_requested_notification()
    )
    update_result = (
        send_meeting_update_notifications(
            response,
            recipient_groups["retained"],
            changes,
            scope=response["updateScope"],
            password=notification_password if "会议链接" in changes else None,
        )
        if changes and recipient_groups["retained"]
        else _not_requested_notification()
    )
    removal_result = (
        send_meeting_removal_notifications(
            response,
            recipient_groups["removed"],
            scope=response["updateScope"],
        )
        if recipient_groups["removed"]
        else _not_requested_notification()
    )
    response["emailNotifications"] = {
        "invitation": invitation_result,
        "update": update_result,
        "removal": removal_result,
    }
    response["emailNotification"] = _combine_notification_results(
        invitation_result,
        update_result,
        removal_result,
    )
    return response


@with_process_write_lock
def _cancel_meeting(
    meeting_id: int,
    scope: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    normalized_scope = _normalize_recurrence_scope(scope, "取消")
    with session_scope() as session:
        selected_model = session.get(Meeting, meeting_id)
        if selected_model is None:
            raise MeetingError("会议不存在", 404)

        selected = _attach_attendees(
            session,
            _refresh_expired_status(session, model_to_dict(selected_model)),
        )
        scoped_meetings = _scope_meetings(session, selected, normalized_scope)
        target_meetings = [
            meeting
            for meeting in scoped_meetings
            if meeting["status"] not in {"Finished", "Cancelled"}
        ]
        notification_recipients = _meeting_recipient_union(session, target_meetings)
        now = isoformat(utc_now())
        target_ids = [meeting["id"] for meeting in target_meetings]
        if target_ids:
            for target_id in target_ids:
                model = session.get(Meeting, target_id)
                model.status = "Cancelled"
                model.cancelled_at = now
                model.updated_at = now
            session.flush()
            updated_models = session.scalars(
                select(Meeting)
                .where(Meeting.id.in_(target_ids))
                .order_by(func.coalesce(Meeting.recurrence_index, 0), Meeting.id)
            ).all()
            affected_meetings = [
                _attach_attendees(session, model_to_dict(model))
                for model in updated_models
            ]
        else:
            affected_meetings = []
        selected_updated = _attach_attendees(
            session, model_to_dict(session.get(Meeting, meeting_id))
        )

    response = public_meeting(selected_updated)
    response.update(
        ids=target_ids,
        seriesId=selected_updated.get("series_id"),
        cancelled=True,
        cancelledCount=len(target_ids),
        cancellationScope=normalized_scope,
        alreadyCancelled=not target_ids,
        affectedMeetings=[public_meeting(meeting) for meeting in affected_meetings],
    )
    return response, notification_recipients


def delete_meeting(meeting_id: int, scope: str | None = None) -> dict[str, Any]:
    response, notification_recipients = _cancel_meeting(meeting_id, scope)
    response["emailNotification"] = (
        send_meeting_cancellation_notifications(
            response,
            notification_recipients,
            scope=response["cancellationScope"],
        )
        if notification_recipients
        else _not_requested_notification()
    )
    return response


@with_process_write_lock
def get_meeting_by_room(room_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        models = session.scalars(
            select(Meeting)
            .where(Meeting.room_id == room_id)
            .order_by(
                Meeting.start_time,
                func.coalesce(Meeting.recurrence_index, 0),
                Meeting.id,
            )
        ).all()
        if not models:
            return None
        meetings = [
            _refresh_expired_status(session, model_to_dict(model))
            for model in models
        ]
        meeting = _select_meeting_for_room(meetings)
        return meeting


@with_process_write_lock
def get_meeting_for_reservation(meeting_id: int) -> dict[str, Any]:
    with session_scope() as session:
        model = session.get(Meeting, meeting_id)
        if model is None:
            raise MeetingError("会议不存在", 404)
        meeting = _refresh_expired_status(session, model_to_dict(model))
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
    if not password_required and not config.jitsi_jwt_enabled:
        payload["jitsiUrl"] = jitsi_url(meeting["room_id"])
    return payload


@with_process_write_lock
def verify_join_password(
    room_id: str,
    password: str | None,
    ip_address: str | None,
    user_agent: str | None,
    display_name: str | None = None,
    email: str | None = None,
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
        return {
            "jitsiUrl": jitsi_join_url(
                meeting["room_id"],
                display_name=display_name,
                email=email,
            )
        }

    if not password or not verify_password(str(password), meeting["password_hash"]):
        log_access(room_id, meeting["id"], ip_address, user_agent, False, "密码错误")
        raise MeetingError("密码错误", 403)

    log_access(room_id, meeting["id"], ip_address, user_agent, True)
    return {
        "jitsiUrl": jitsi_join_url(
            meeting["room_id"],
            display_name=display_name,
            email=email,
        )
    }


@with_process_write_lock
def log_access(
    room_id: str,
    meeting_id: int | None,
    ip_address: str | None,
    user_agent: str | None,
    success: bool,
    fail_reason: str | None = None,
) -> None:
    with session_scope() as session:
        session.add(
            AccessLog(
                meeting_id=meeting_id,
                room_id=room_id,
                ip_address=ip_address,
                user_agent=user_agent,
                join_time=isoformat(utc_now()),
                success=success,
                fail_reason=fail_reason,
            )
        )


@with_process_write_lock
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
    with session_scope() as session:
        model = session.get(Meeting, meeting["id"])
        model.status = "Running"
        if mail_owner is not None:
            model.mail_owner = mail_owner
        model.last_started_at = now_text
        model.updated_at = now_text
        session.flush()
        updated_meeting = model_to_dict(model)

    log_access(room_name, meeting["id"], ip_address, user_agent, True)
    return 201, reservation_payload(updated_meeting)


@with_process_write_lock
def finish_conference(meeting_id: int) -> dict[str, Any]:
    now = isoformat(utc_now())
    with session_scope() as session:
        model = session.get(Meeting, meeting_id)
        if model is None:
            raise MeetingError("会议不存在", 404)
        if model.status != "Cancelled":
            model.status = "Finished"
            model.finished_at = now
            model.updated_at = now
        session.flush()
        return public_meeting(model_to_dict(model))


def list_access_logs(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    with session_scope() as session:
        rows = session.execute(
            select(AccessLog, Meeting.title)
            .outerjoin(Meeting, Meeting.id == AccessLog.meeting_id)
            .order_by(AccessLog.id.desc())
            .limit(limit)
        ).all()
        return [
            {**model_to_dict(access_log), "title": title}
            for access_log, title in rows
        ]
