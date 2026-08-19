from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import process_write_lock, session_scope
from models import User, UserGroup, UserGroupMember


GROUP_ROLES = {"admin", "member"}
MAX_GROUP_NAME_LENGTH = 80
MAX_GROUP_DESCRIPTION_LENGTH = 500
MAX_MEMBERS_PER_ADD = 100


class GroupError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def with_group_write_lock(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with process_write_lock():
            return function(*args, **kwargs)

    return wrapper


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _is_system_admin(actor: dict[str, Any]) -> bool:
    return actor.get("role") == "admin" and actor.get("status") == "active"


def _normalize_group_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise GroupError("用户组名称不能为空")
    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise GroupError(f"用户组名称不能超过 {MAX_GROUP_NAME_LENGTH} 个字符")
    return name


def _normalize_group_description(value: Any) -> str:
    description = str(value or "").strip()
    if len(description) > MAX_GROUP_DESCRIPTION_LENGTH:
        raise GroupError(f"用户组说明不能超过 {MAX_GROUP_DESCRIPTION_LENGTH} 个字符")
    return description


def _normalize_member_user_ids(payload: dict[str, Any]) -> list[int]:
    raw_user_ids = payload.get("userIds")
    if raw_user_ids is None:
        raw_user_ids = [payload.get("userId")]
    if not isinstance(raw_user_ids, list):
        raise GroupError("请选择要添加的用户")

    user_ids: list[int] = []
    seen: set[int] = set()
    for value in raw_user_ids:
        try:
            user_id = int(value)
        except (TypeError, ValueError) as exc:
            raise GroupError("所选用户无效") from exc
        if user_id < 1:
            raise GroupError("所选用户无效")
        if user_id not in seen:
            seen.add(user_id)
            user_ids.append(user_id)

    if not user_ids:
        raise GroupError("请选择要添加的用户")
    if len(user_ids) > MAX_MEMBERS_PER_ADD:
        raise GroupError(f"一次最多添加 {MAX_MEMBERS_PER_ADD} 名用户")
    return user_ids


def _fetch_group(session: Session, group_id: int) -> UserGroup:
    group = session.get(UserGroup, group_id)
    if group is None:
        raise GroupError("用户组不存在", 404)
    return group


def _membership_role(session: Session, group_id: int, user_id: int) -> str | None:
    return session.scalar(
        select(UserGroupMember.member_role).where(
            UserGroupMember.group_id == group_id,
            UserGroupMember.user_id == user_id,
        )
    )


def _require_group_visibility(
    session: Session,
    group_id: int,
    actor: dict[str, Any],
) -> tuple[UserGroup, str | None]:
    group = _fetch_group(session, group_id)
    role = _membership_role(session, group_id, int(actor["id"]))
    if not _is_system_admin(actor) and role is None:
        raise GroupError("你无权查看该用户组", 403)
    return group, role


def _require_group_manager(
    session: Session,
    group_id: int,
    actor: dict[str, Any],
) -> tuple[UserGroup, str | None]:
    group, role = _require_group_visibility(session, group_id, actor)
    if not _is_system_admin(actor) and role != "admin":
        raise GroupError("只有组管理员可以执行此操作", 403)
    return group, role


def _group_payload(
    session: Session,
    group: UserGroup,
    actor: dict[str, Any],
) -> dict[str, Any]:
    rows = session.execute(
        select(User, UserGroupMember)
        .join(UserGroupMember, User.id == UserGroupMember.user_id)
        .where(UserGroupMember.group_id == group.id)
        .order_by(
            case((UserGroupMember.member_role == "admin", 0), else_=1),
            func.lower(User.display_name),
            func.lower(User.username),
        )
    ).all()
    members = [
        {
            "id": user.id,
            "username": user.username,
            "displayName": user.display_name,
            "email": user.email,
            "jobTitle": user.job_title,
            "status": user.status,
            "groupRole": membership.member_role,
            "addedAt": membership.created_at,
        }
        for user, membership in rows
    ]
    actor_id = int(actor["id"])
    current_membership = next((member for member in members if member["id"] == actor_id), None)
    current_role = current_membership["groupRole"] if current_membership else None
    system_admin = _is_system_admin(actor)
    can_manage = system_admin or current_role == "admin"
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "createdBy": group.created_by,
        "createdAt": group.created_at,
        "updatedAt": group.updated_at,
        "memberCount": len(members),
        "members": members,
        "includesCurrentUser": current_membership is not None,
        "currentUserGroupRole": current_role,
        "canAddMembers": system_admin or current_role in GROUP_ROLES,
        "canRemoveMembers": can_manage,
        "canManageGroup": can_manage,
        "canDeleteGroup": can_manage,
        "selectionValue": f"@group:{group.id}",
    }


def list_groups(actor: dict[str, Any]) -> list[dict[str, Any]]:
    with session_scope() as session:
        statement = select(UserGroup)
        if not _is_system_admin(actor):
            statement = statement.join(
                UserGroupMember, UserGroupMember.group_id == UserGroup.id
            ).where(UserGroupMember.user_id == int(actor["id"]))
        groups = session.scalars(
            statement.order_by(func.lower(UserGroup.name), UserGroup.id)
        ).all()
        return [_group_payload(session, group, actor) for group in groups]


def get_group(group_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as session:
        group, _role = _require_group_visibility(session, group_id, actor)
        return _group_payload(session, group, actor)


@with_group_write_lock
def create_group(payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    name = _normalize_group_name(payload.get("name"))
    description = _normalize_group_description(payload.get("description"))
    now = utc_timestamp()
    with session_scope() as session:
        try:
            group = UserGroup(
                name=name,
                description=description,
                created_by=int(actor["id"]),
                created_at=now,
                updated_at=now,
            )
            session.add(group)
            session.flush()
            session.add(
                UserGroupMember(
                    group_id=group.id,
                    user_id=int(actor["id"]),
                    member_role="admin",
                    added_by=int(actor["id"]),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
        except IntegrityError as exc:
            raise GroupError("用户组名称已存在", 409) from exc
        return _group_payload(session, group, actor)


@with_group_write_lock
def update_group(group_id: int, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as session:
        group, _role = _require_group_manager(session, group_id, actor)
        group.name = _normalize_group_name(payload.get("name", group.name))
        group.description = _normalize_group_description(
            payload.get("description", group.description)
        )
        group.updated_at = utc_timestamp()
        try:
            session.flush()
        except IntegrityError as exc:
            raise GroupError("用户组名称已存在", 409) from exc
        return _group_payload(session, group, actor)


@with_group_write_lock
def delete_group(group_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as session:
        group, _role = _require_group_manager(session, group_id, actor)
        session.delete(group)
    return {"id": group_id, "deleted": True}


@with_group_write_lock
def add_group_member(
    group_id: int,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    user_ids = _normalize_member_user_ids(payload)
    requested_role = str(payload.get("groupRole") or "member").strip().lower()
    if requested_role not in GROUP_ROLES:
        raise GroupError("组内角色无效")

    with session_scope() as session:
        group, actor_role = _require_group_visibility(session, group_id, actor)
        can_manage = _is_system_admin(actor) or actor_role == "admin"
        if requested_role == "admin" and not can_manage:
            raise GroupError("只有组管理员可以添加其他组管理员", 403)

        users = session.scalars(select(User).where(User.id.in_(user_ids))).all()
        users_by_id = {user.id: user for user in users}
        if len(users_by_id) != len(user_ids):
            raise GroupError("部分用户不存在", 404)
        if any(users_by_id[user_id].status != "active" for user_id in user_ids):
            raise GroupError("只能添加状态正常的用户")

        existing_user_ids = set(
            session.scalars(
                select(UserGroupMember.user_id).where(
                    UserGroupMember.group_id == group_id,
                    UserGroupMember.user_id.in_(user_ids),
                )
            ).all()
        )
        new_user_ids = [user_id for user_id in user_ids if user_id not in existing_user_ids]
        if not new_user_ids:
            raise GroupError("所选用户已经在组内", 409)

        now = utc_timestamp()
        session.add_all(
            [
                UserGroupMember(
                    group_id=group_id,
                    user_id=user_id,
                    member_role=requested_role,
                    added_by=int(actor["id"]),
                    created_at=now,
                    updated_at=now,
                )
                for user_id in new_user_ids
            ]
        )
        group.updated_at = now
        try:
            session.flush()
        except IntegrityError as exc:
            raise GroupError("添加用户组成员失败", 409) from exc
        return _group_payload(session, group, actor)


def _ensure_not_last_group_admin(
    session: Session,
    group_id: int,
    user_id: int,
) -> UserGroupMember:
    member = session.get(UserGroupMember, (group_id, user_id))
    if member is None:
        raise GroupError("该用户不在组内", 404)
    if member.member_role != "admin":
        return member
    admin_count = session.scalar(
        select(func.count()).select_from(UserGroupMember).where(
            UserGroupMember.group_id == group_id,
            UserGroupMember.member_role == "admin",
        )
    )
    if int(admin_count or 0) <= 1:
        raise GroupError("用户组至少需要保留一名组管理员", 409)
    return member


@with_group_write_lock
def update_group_member(
    group_id: int,
    user_id: int,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    role = str(payload.get("groupRole") or "").strip().lower()
    if role not in GROUP_ROLES:
        raise GroupError("组内角色无效")
    with session_scope() as session:
        group, _actor_role = _require_group_manager(session, group_id, actor)
        member = (
            _ensure_not_last_group_admin(session, group_id, user_id)
            if role == "member"
            else session.get(UserGroupMember, (group_id, user_id))
        )
        if member is None:
            raise GroupError("该用户不在组内", 404)
        now = utc_timestamp()
        member.member_role = role
        member.updated_at = now
        group.updated_at = now
        session.flush()
        return _group_payload(session, group, actor)


@with_group_write_lock
def remove_group_member(
    group_id: int,
    user_id: int,
    actor: dict[str, Any],
) -> dict[str, Any]:
    with session_scope() as session:
        group, _actor_role = _require_group_manager(session, group_id, actor)
        member = _ensure_not_last_group_admin(session, group_id, user_id)
        session.delete(member)
        group.updated_at = utc_timestamp()
        session.flush()
        return _group_payload(session, group, actor)


def resolve_group_attendee_emails(
    session: Session,
    group_id: int,
    actor: dict[str, Any],
) -> list[str]:
    _require_group_visibility(session, group_id, actor)
    rows = session.execute(
        select(User.email)
        .join(UserGroupMember, User.id == UserGroupMember.user_id)
        .where(UserGroupMember.group_id == group_id, User.status == "active")
        .order_by(
            case((UserGroupMember.member_role == "admin", 0), else_=1),
            func.lower(User.display_name),
            func.lower(User.username),
        )
    ).scalars()
    return [email.strip() for email in rows if email and email.strip()]
