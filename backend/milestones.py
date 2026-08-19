from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from database import process_write_lock, session_scope
from models import Milestone, MilestonePin, MilestoneRelatedUser, User


MILESTONE_STATUSES = {"planned", "in_progress", "completed"}
MAX_RELATED_USERS = 100


class MilestoneError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        raise MilestoneError("里程碑标题不能为空")
    if len(title) > 160:
        raise MilestoneError("里程碑标题不能超过 160 个字符")
    return title


def _normalize_description(value: Any) -> str:
    description = str(value or "").strip()
    if len(description) > 2000:
        raise MilestoneError("里程碑描述不能超过 2000 个字符")
    return description


def _normalize_due_date(value: Any) -> str:
    due_date = str(value or "").strip()
    try:
        parsed = date.fromisoformat(due_date)
    except ValueError as exc:
        raise MilestoneError("截止日期格式无效") from exc
    return parsed.isoformat()


def _normalize_status(value: Any, default: str = "planned") -> str:
    status = str(value or default).strip()
    if status not in MILESTONE_STATUSES:
        raise MilestoneError("里程碑状态无效")
    return status


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise MilestoneError("全局范围设置无效")


def _normalize_related_user_ids(value: Any, default_user_id: int) -> list[int]:
    if value is None:
        return [default_user_id]
    if not isinstance(value, list):
        raise MilestoneError("相关用户格式无效")

    related_user_ids: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            raise MilestoneError("相关用户格式无效")
        try:
            user_id = int(item)
        except (TypeError, ValueError) as exc:
            raise MilestoneError("相关用户格式无效") from exc
        if user_id < 1:
            raise MilestoneError("相关用户格式无效")
        if user_id not in seen:
            seen.add(user_id)
            related_user_ids.append(user_id)

    if not related_user_ids:
        raise MilestoneError("请至少选择一名相关用户")
    if len(related_user_ids) > MAX_RELATED_USERS:
        raise MilestoneError(f"相关用户不能超过 {MAX_RELATED_USERS} 人")
    return related_user_ids


def _validate_active_users(session: Session, user_ids: list[int]) -> None:
    active_ids = set(
        session.scalars(
            select(User.id).where(User.status == "active", User.id.in_(user_ids))
        ).all()
    )
    if active_ids != set(user_ids):
        raise MilestoneError("部分相关用户不存在或不可用")


def _replace_related_users(
    session: Session,
    milestone_id: int,
    user_ids: list[int],
) -> None:
    existing = session.scalars(
        select(MilestoneRelatedUser).where(
            MilestoneRelatedUser.milestone_id == milestone_id
        )
    ).all()
    for related_user in existing:
        session.delete(related_user)
    session.add_all(
        [
            MilestoneRelatedUser(milestone_id=milestone_id, user_id=user_id)
            for user_id in user_ids
        ]
    )
    session.flush()


def _related_users(session: Session, milestone_id: int) -> list[dict[str, Any]]:
    users = session.scalars(
        select(User)
        .join(MilestoneRelatedUser, User.id == MilestoneRelatedUser.user_id)
        .where(MilestoneRelatedUser.milestone_id == milestone_id)
        .order_by(func.lower(User.display_name), func.lower(User.username))
    ).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "displayName": user.display_name,
            "email": user.email,
        }
        for user in users
    ]


def _public_milestone(
    session: Session,
    milestone: Milestone,
    creator: User,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    creator_id = int(milestone.created_by)
    is_pinned = session.get(
        MilestonePin, (milestone.id, int(current_user["id"]))
    ) is not None
    return {
        "id": milestone.id,
        "title": milestone.title,
        "description": milestone.description,
        "dueDate": milestone.due_date,
        "status": milestone.status,
        "isGlobal": bool(milestone.is_global),
        "createdBy": {
            "id": creator_id,
            "username": creator.username,
            "displayName": creator.display_name,
        },
        "relatedUsers": _related_users(session, milestone.id),
        "isPinned": is_pinned,
        "canEdit": creator_id == int(current_user["id"])
        or current_user.get("role") == "admin",
        "createdAt": milestone.created_at,
        "updatedAt": milestone.updated_at,
    }


def _fetch_milestone_row(
    session: Session, milestone_id: int
) -> tuple[Milestone, User] | None:
    return session.execute(
        select(Milestone, User)
        .join(User, User.id == Milestone.created_by)
        .where(Milestone.id == milestone_id)
    ).one_or_none()


def _is_visible(
    session: Session,
    milestone: Milestone,
    current_user: dict[str, Any],
) -> bool:
    if bool(milestone.is_global) or milestone.created_by == int(current_user["id"]):
        return True
    return session.get(
        MilestoneRelatedUser, (milestone.id, int(current_user["id"]))
    ) is not None


def list_milestones(current_user: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in MILESTONE_STATUSES:
        raise MilestoneError("里程碑状态参数无效")

    current_user_id = int(current_user["id"])
    related_exists = exists().where(
        MilestoneRelatedUser.milestone_id == Milestone.id,
        MilestoneRelatedUser.user_id == current_user_id,
    )
    statement = (
        select(Milestone, User)
        .join(User, User.id == Milestone.created_by)
        .where(
            or_(
                Milestone.is_global.is_(True),
                Milestone.created_by == current_user_id,
                related_exists,
            )
        )
    )
    if normalized_status:
        statement = statement.where(Milestone.status == normalized_status)
    statement = statement.order_by(Milestone.due_date, Milestone.id)

    with session_scope() as session:
        rows = session.execute(statement).all()
        return [
            _public_milestone(session, milestone, creator, current_user)
            for milestone, creator in rows
        ]


def get_milestone(milestone_id: int, current_user: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as session:
        row = _fetch_milestone_row(session, milestone_id)
        if row is None or not _is_visible(session, row[0], current_user):
            raise MilestoneError("里程碑不存在", 404)
        return _public_milestone(session, row[0], row[1], current_user)


def create_milestone(payload: dict[str, Any], current_user: dict[str, Any]) -> dict[str, Any]:
    title = _normalize_title(payload.get("title"))
    description = _normalize_description(payload.get("description"))
    due_date = _normalize_due_date(payload.get("dueDate", payload.get("due_date")))
    status = _normalize_status(payload.get("status"))
    is_global = _normalize_bool(payload.get("isGlobal", payload.get("is_global")), False)
    related_user_ids = [] if is_global else _normalize_related_user_ids(
        payload.get("relatedUserIds", payload.get("related_user_ids")),
        int(current_user["id"]),
    )
    now = _utc_now_text()

    with process_write_lock():
        with session_scope() as session:
            if related_user_ids:
                _validate_active_users(session, related_user_ids)
            milestone = Milestone(
                title=title,
                description=description,
                due_date=due_date,
                status=status,
                is_global=is_global,
                created_by=int(current_user["id"]),
                created_at=now,
                updated_at=now,
            )
            session.add(milestone)
            session.flush()
            if related_user_ids:
                _replace_related_users(session, milestone.id, related_user_ids)
            creator = session.get(User, milestone.created_by)
            return _public_milestone(session, milestone, creator, current_user)


def update_milestone(
    milestone_id: int,
    payload: dict[str, Any],
    current_user: dict[str, Any],
) -> dict[str, Any]:
    with process_write_lock():
        with session_scope() as session:
            row = _fetch_milestone_row(session, milestone_id)
            if row is None:
                raise MilestoneError("里程碑不存在", 404)
            milestone, creator = row
            if milestone.created_by != int(current_user["id"]) and current_user.get("role") != "admin":
                raise MilestoneError("只有创建者或管理员可以修改里程碑", 403)

            milestone.title = _normalize_title(payload.get("title", milestone.title))
            milestone.description = _normalize_description(
                payload.get("description", milestone.description)
            )
            milestone.due_date = _normalize_due_date(
                payload.get("dueDate", payload.get("due_date", milestone.due_date))
            )
            milestone.status = _normalize_status(payload.get("status", milestone.status))
            is_global = _normalize_bool(
                payload.get("isGlobal", payload.get("is_global")),
                bool(milestone.is_global),
            )
            related_ids_provided = "relatedUserIds" in payload or "related_user_ids" in payload
            if is_global:
                related_user_ids: list[int] = []
            elif related_ids_provided:
                related_user_ids = _normalize_related_user_ids(
                    payload.get("relatedUserIds", payload.get("related_user_ids")),
                    int(current_user["id"]),
                )
            else:
                related_user_ids = [
                    user["id"] for user in _related_users(session, milestone_id)
                ] or [int(current_user["id"])]

            if related_user_ids:
                _validate_active_users(session, related_user_ids)
            milestone.is_global = is_global
            milestone.updated_at = _utc_now_text()
            _replace_related_users(session, milestone_id, related_user_ids)
            return _public_milestone(session, milestone, creator, current_user)


def update_milestone_pin(
    milestone_id: int,
    pinned: Any,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(pinned, bool):
        raise MilestoneError("置顶设置无效")

    with process_write_lock():
        with session_scope() as session:
            row = _fetch_milestone_row(session, milestone_id)
            if row is None or not _is_visible(session, row[0], current_user):
                raise MilestoneError("里程碑不存在", 404)
            key = (milestone_id, int(current_user["id"]))
            pin = session.get(MilestonePin, key)
            if pinned and pin is None:
                session.add(
                    MilestonePin(
                        milestone_id=milestone_id,
                        user_id=int(current_user["id"]),
                        created_at=_utc_now_text(),
                    )
                )
            elif not pinned and pin is not None:
                session.delete(pin)
            session.flush()
            return _public_milestone(session, row[0], row[1], current_user)


def delete_milestone(milestone_id: int, current_user: dict[str, Any]) -> dict[str, Any]:
    with process_write_lock():
        with session_scope() as session:
            milestone = session.get(Milestone, milestone_id)
            if milestone is None:
                raise MilestoneError("里程碑不存在", 404)
            if milestone.created_by != int(current_user["id"]) and current_user.get("role") != "admin":
                raise MilestoneError("只有创建者或管理员可以删除里程碑", 403)
            session.delete(milestone)
    return {"deleted": True, "id": milestone_id}
