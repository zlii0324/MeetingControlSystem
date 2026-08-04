from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from database import get_connection, process_write_lock


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
        if user_id in seen:
            continue
        seen.add(user_id)
        user_ids.append(user_id)

    if not user_ids:
        raise GroupError("请选择要添加的用户")
    if len(user_ids) > MAX_MEMBERS_PER_ADD:
        raise GroupError(f"一次最多添加 {MAX_MEMBERS_PER_ADD} 名用户")
    return user_ids


def _fetch_group(conn: sqlite3.Connection, group_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM user_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise GroupError("用户组不存在", 404)
    return row


def _membership_role(conn: sqlite3.Connection, group_id: int, user_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT member_role
          FROM user_group_members
         WHERE group_id = ? AND user_id = ?
        """,
        (group_id, user_id),
    ).fetchone()
    return row["member_role"] if row else None


def _require_group_visibility(
    conn: sqlite3.Connection,
    group_id: int,
    actor: dict[str, Any],
) -> tuple[sqlite3.Row, str | None]:
    group = _fetch_group(conn, group_id)
    role = _membership_role(conn, group_id, int(actor["id"]))
    if not _is_system_admin(actor) and role is None:
        raise GroupError("你无权查看该用户组", 403)
    return group, role


def _require_group_manager(
    conn: sqlite3.Connection,
    group_id: int,
    actor: dict[str, Any],
) -> tuple[sqlite3.Row, str | None]:
    group, role = _require_group_visibility(conn, group_id, actor)
    if not _is_system_admin(actor) and role != "admin":
        raise GroupError("只有组管理员可以执行此操作", 403)
    return group, role


def _member_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"],
        "email": row["email"],
        "jobTitle": row["job_title"],
        "status": row["status"],
        "groupRole": row["member_role"],
        "addedAt": row["member_created_at"],
    }


def _group_payload(
    conn: sqlite3.Connection,
    group: sqlite3.Row,
    actor: dict[str, Any],
) -> dict[str, Any]:
    member_rows = conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.email, u.job_title, u.status,
               gm.member_role, gm.created_at AS member_created_at
          FROM user_group_members gm
          JOIN users u ON u.id = gm.user_id
         WHERE gm.group_id = ?
         ORDER BY
               CASE gm.member_role WHEN 'admin' THEN 0 ELSE 1 END,
               u.display_name COLLATE NOCASE ASC,
               u.username COLLATE NOCASE ASC
        """,
        (group["id"],),
    ).fetchall()
    members = [_member_payload(row) for row in member_rows]
    actor_id = int(actor["id"])
    current_membership = next((member for member in members if member["id"] == actor_id), None)
    current_role = current_membership["groupRole"] if current_membership else None
    system_admin = _is_system_admin(actor)
    can_manage = system_admin or current_role == "admin"
    return {
        "id": group["id"],
        "name": group["name"],
        "description": group["description"],
        "createdBy": group["created_by"],
        "createdAt": group["created_at"],
        "updatedAt": group["updated_at"],
        "memberCount": len(members),
        "members": members,
        "includesCurrentUser": current_membership is not None,
        "currentUserGroupRole": current_role,
        "canAddMembers": system_admin or current_role in GROUP_ROLES,
        "canRemoveMembers": can_manage,
        "canManageGroup": can_manage,
        "canDeleteGroup": can_manage,
        "selectionValue": f"@group:{group['id']}",
    }


def list_groups(actor: dict[str, Any]) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if _is_system_admin(actor):
            rows = conn.execute(
                "SELECT * FROM user_groups ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT g.*
                  FROM user_groups g
                  JOIN user_group_members gm ON gm.group_id = g.id
                 WHERE gm.user_id = ?
                 ORDER BY g.name COLLATE NOCASE ASC, g.id ASC
                """,
                (actor["id"],),
            ).fetchall()
        return [_group_payload(conn, row, actor) for row in rows]


def get_group(group_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        group, _role = _require_group_visibility(conn, group_id, actor)
        return _group_payload(conn, group, actor)


@with_group_write_lock
def create_group(payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    name = _normalize_group_name(payload.get("name"))
    description = _normalize_group_description(payload.get("description"))
    now = utc_timestamp()
    with get_connection() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO user_groups (name, description, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, description, actor["id"], now, now),
            )
            group_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO user_group_members (
                    group_id, user_id, member_role, added_by, created_at, updated_at
                )
                VALUES (?, ?, 'admin', ?, ?, ?)
                """,
                (group_id, actor["id"], actor["id"], now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise GroupError("用户组名称已存在", 409) from exc
        group = _fetch_group(conn, group_id)
        return _group_payload(conn, group, actor)


@with_group_write_lock
def update_group(group_id: int, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        group, _role = _require_group_manager(conn, group_id, actor)
        name = _normalize_group_name(payload.get("name", group["name"]))
        description = _normalize_group_description(payload.get("description", group["description"]))
        try:
            conn.execute(
                """
                UPDATE user_groups
                   SET name = ?, description = ?, updated_at = ?
                 WHERE id = ?
                """,
                (name, description, utc_timestamp(), group_id),
            )
        except sqlite3.IntegrityError as exc:
            raise GroupError("用户组名称已存在", 409) from exc
        updated = _fetch_group(conn, group_id)
        return _group_payload(conn, updated, actor)


@with_group_write_lock
def delete_group(group_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        _require_group_manager(conn, group_id, actor)
        conn.execute("DELETE FROM user_groups WHERE id = ?", (group_id,))
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

    with get_connection() as conn:
        group, actor_role = _require_group_visibility(conn, group_id, actor)
        can_manage = _is_system_admin(actor) or actor_role == "admin"
        if requested_role == "admin" and not can_manage:
            raise GroupError("只有组管理员可以添加其他组管理员", 403)
        placeholders = ",".join("?" for _ in user_ids)
        users = conn.execute(
            f"SELECT id, status FROM users WHERE id IN ({placeholders})",
            tuple(user_ids),
        ).fetchall()
        users_by_id = {int(user["id"]): user for user in users}
        if len(users_by_id) != len(user_ids):
            raise GroupError("部分用户不存在", 404)
        if any(users_by_id[user_id]["status"] != "active" for user_id in user_ids):
            raise GroupError("只能添加状态正常的用户")

        existing_rows = conn.execute(
            f"""
            SELECT user_id
              FROM user_group_members
             WHERE group_id = ? AND user_id IN ({placeholders})
            """,
            (group_id, *user_ids),
        ).fetchall()
        existing_user_ids = {int(row["user_id"]) for row in existing_rows}
        new_user_ids = [user_id for user_id in user_ids if user_id not in existing_user_ids]
        if not new_user_ids:
            raise GroupError("所选用户已经在组内", 409)

        now = utc_timestamp()
        try:
            conn.executemany(
                """
                INSERT INTO user_group_members (
                    group_id, user_id, member_role, added_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (group_id, user_id, requested_role, actor["id"], now, now)
                    for user_id in new_user_ids
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise GroupError("添加用户组成员失败", 409) from exc
        conn.execute(
            "UPDATE user_groups SET updated_at = ? WHERE id = ?",
            (now, group_id),
        )
        updated = _fetch_group(conn, group_id)
        return _group_payload(conn, updated, actor)


def _ensure_not_last_group_admin(
    conn: sqlite3.Connection,
    group_id: int,
    user_id: int,
) -> None:
    member = conn.execute(
        """
        SELECT member_role
          FROM user_group_members
         WHERE group_id = ? AND user_id = ?
        """,
        (group_id, user_id),
    ).fetchone()
    if member is None:
        raise GroupError("该用户不在组内", 404)
    if member["member_role"] != "admin":
        return
    admin_count = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM user_group_members
         WHERE group_id = ? AND member_role = 'admin'
        """,
        (group_id,),
    ).fetchone()["count"]
    if admin_count <= 1:
        raise GroupError("用户组至少需要保留一名组管理员", 409)


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
    with get_connection() as conn:
        _require_group_manager(conn, group_id, actor)
        if role == "member":
            _ensure_not_last_group_admin(conn, group_id, user_id)
        cursor = conn.execute(
            """
            UPDATE user_group_members
               SET member_role = ?, updated_at = ?
             WHERE group_id = ? AND user_id = ?
            """,
            (role, utc_timestamp(), group_id, user_id),
        )
        if cursor.rowcount == 0:
            raise GroupError("该用户不在组内", 404)
        conn.execute(
            "UPDATE user_groups SET updated_at = ? WHERE id = ?",
            (utc_timestamp(), group_id),
        )
        updated = _fetch_group(conn, group_id)
        return _group_payload(conn, updated, actor)


@with_group_write_lock
def remove_group_member(
    group_id: int,
    user_id: int,
    actor: dict[str, Any],
) -> dict[str, Any]:
    with get_connection() as conn:
        _require_group_manager(conn, group_id, actor)
        _ensure_not_last_group_admin(conn, group_id, user_id)
        cursor = conn.execute(
            "DELETE FROM user_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        if cursor.rowcount == 0:
            raise GroupError("该用户不在组内", 404)
        conn.execute(
            "UPDATE user_groups SET updated_at = ? WHERE id = ?",
            (utc_timestamp(), group_id),
        )
        updated = _fetch_group(conn, group_id)
        return _group_payload(conn, updated, actor)


def resolve_group_attendee_emails(
    conn: sqlite3.Connection,
    group_id: int,
    actor: dict[str, Any],
) -> list[str]:
    _require_group_visibility(conn, group_id, actor)
    rows = conn.execute(
        """
        SELECT u.email
          FROM user_group_members gm
          JOIN users u ON u.id = gm.user_id
         WHERE gm.group_id = ? AND u.status = 'active'
         ORDER BY
               CASE gm.member_role WHEN 'admin' THEN 0 ELSE 1 END,
               u.display_name COLLATE NOCASE ASC,
               u.username COLLATE NOCASE ASC
        """,
        (group_id,),
    ).fetchall()
    return [str(row["email"]).strip() for row in rows if str(row["email"] or "").strip()]
