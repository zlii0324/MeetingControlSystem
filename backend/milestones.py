from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from database import get_connection, process_write_lock


MILESTONE_STATUSES = {"planned", "in_progress", "completed"}
MAX_RELATED_USERS = 500


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


def _validate_active_users(conn: sqlite3.Connection, user_ids: list[int]) -> None:
    placeholders = ",".join("?" for _ in user_ids)
    rows = conn.execute(
        f"SELECT id FROM users WHERE status = 'active' AND id IN ({placeholders})",
        tuple(user_ids),
    ).fetchall()
    if {int(row["id"]) for row in rows} != set(user_ids):
        raise MilestoneError("部分相关用户不存在或不可用")


def _replace_related_users(
    conn: sqlite3.Connection,
    milestone_id: int,
    user_ids: list[int],
) -> None:
    conn.execute("DELETE FROM milestone_related_users WHERE milestone_id = ?", (milestone_id,))
    conn.executemany(
        "INSERT INTO milestone_related_users (milestone_id, user_id) VALUES (?, ?)",
        [(milestone_id, user_id) for user_id in user_ids],
    )


def _related_users(conn: sqlite3.Connection, milestone_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT users.id, users.username, users.display_name, users.email
          FROM milestone_related_users
          JOIN users ON users.id = milestone_related_users.user_id
         WHERE milestone_related_users.milestone_id = ?
         ORDER BY users.display_name COLLATE NOCASE ASC, users.username COLLATE NOCASE ASC
        """,
        (milestone_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "email": row["email"],
        }
        for row in rows
    ]


def _public_milestone(
    conn: sqlite3.Connection,
    row: sqlite3.Row | dict[str, Any],
    current_user: dict[str, Any],
) -> dict[str, Any]:
    milestone = dict(row)
    creator_id = int(milestone["created_by"])
    return {
        "id": milestone["id"],
        "title": milestone["title"],
        "description": milestone["description"],
        "dueDate": milestone["due_date"],
        "status": milestone["status"],
        "isGlobal": bool(milestone["is_global"]),
        "createdBy": {
            "id": creator_id,
            "username": milestone["creator_username"],
            "displayName": milestone["creator_display_name"],
        },
        "relatedUsers": _related_users(conn, int(milestone["id"])),
        "isPinned": conn.execute(
            "SELECT 1 FROM milestone_pins WHERE milestone_id = ? AND user_id = ?",
            (milestone["id"], current_user["id"]),
        ).fetchone() is not None,
        "canEdit": creator_id == int(current_user["id"]) or current_user.get("role") == "admin",
        "createdAt": milestone["created_at"],
        "updatedAt": milestone["updated_at"],
    }


def _fetch_milestone_row(conn: sqlite3.Connection, milestone_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT milestones.*, users.username AS creator_username,
               users.display_name AS creator_display_name
          FROM milestones
          JOIN users ON users.id = milestones.created_by
         WHERE milestones.id = ?
        """,
        (milestone_id,),
    ).fetchone()


def _is_visible(conn: sqlite3.Connection, row: sqlite3.Row, current_user: dict[str, Any]) -> bool:
    if bool(row["is_global"]) or int(row["created_by"]) == int(current_user["id"]):
        return True
    return conn.execute(
        """
        SELECT 1
          FROM milestone_related_users
         WHERE milestone_id = ? AND user_id = ?
         LIMIT 1
        """,
        (row["id"], current_user["id"]),
    ).fetchone() is not None


def list_milestones(current_user: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in MILESTONE_STATUSES:
        raise MilestoneError("里程碑状态参数无效")

    params: list[Any] = [current_user["id"], current_user["id"]]
    status_sql = ""
    if normalized_status:
        status_sql = " AND milestones.status = ?"
        params.append(normalized_status)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT milestones.*, users.username AS creator_username,
                   users.display_name AS creator_display_name
              FROM milestones
              JOIN users ON users.id = milestones.created_by
             WHERE (
                    milestones.is_global = 1
                 OR milestones.created_by = ?
                 OR EXISTS (
                      SELECT 1
                        FROM milestone_related_users
                       WHERE milestone_related_users.milestone_id = milestones.id
                         AND milestone_related_users.user_id = ?
                 )
             )
             {status_sql}
             ORDER BY milestones.due_date ASC, milestones.id ASC
            """,
            tuple(params),
        ).fetchall()
        return [_public_milestone(conn, row, current_user) for row in rows]


def get_milestone(milestone_id: int, current_user: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        row = _fetch_milestone_row(conn, milestone_id)
        if row is None or not _is_visible(conn, row, current_user):
            raise MilestoneError("里程碑不存在", 404)
        return _public_milestone(conn, row, current_user)


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
        with get_connection() as conn:
            if related_user_ids:
                _validate_active_users(conn, related_user_ids)
            cursor = conn.execute(
                """
                INSERT INTO milestones (
                    title, description, due_date, status, is_global,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    due_date,
                    status,
                    1 if is_global else 0,
                    current_user["id"],
                    now,
                    now,
                ),
            )
            milestone_id = int(cursor.lastrowid)
            if related_user_ids:
                _replace_related_users(conn, milestone_id, related_user_ids)
            row = _fetch_milestone_row(conn, milestone_id)
            return _public_milestone(conn, row, current_user)


def update_milestone(
    milestone_id: int,
    payload: dict[str, Any],
    current_user: dict[str, Any],
) -> dict[str, Any]:
    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_milestone_row(conn, milestone_id)
            if row is None:
                raise MilestoneError("里程碑不存在", 404)
            if int(row["created_by"]) != int(current_user["id"]) and current_user.get("role") != "admin":
                raise MilestoneError("只有创建者或管理员可以修改里程碑", 403)

            title = _normalize_title(payload.get("title", row["title"]))
            description = _normalize_description(payload.get("description", row["description"]))
            due_date = _normalize_due_date(
                payload.get("dueDate", payload.get("due_date", row["due_date"]))
            )
            status = _normalize_status(payload.get("status", row["status"]))
            is_global = _normalize_bool(
                payload.get("isGlobal", payload.get("is_global")),
                bool(row["is_global"]),
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
                related_user_ids = [user["id"] for user in _related_users(conn, milestone_id)]
                if not related_user_ids:
                    related_user_ids = [int(current_user["id"])]

            if related_user_ids:
                _validate_active_users(conn, related_user_ids)
            now = _utc_now_text()
            conn.execute(
                """
                UPDATE milestones
                   SET title = ?, description = ?, due_date = ?, status = ?,
                       is_global = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    title,
                    description,
                    due_date,
                    status,
                    1 if is_global else 0,
                    now,
                    milestone_id,
                ),
            )
            _replace_related_users(conn, milestone_id, related_user_ids)
            updated_row = _fetch_milestone_row(conn, milestone_id)
            return _public_milestone(conn, updated_row, current_user)


def update_milestone_pin(
    milestone_id: int,
    pinned: Any,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(pinned, bool):
        raise MilestoneError("置顶设置无效")

    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_milestone_row(conn, milestone_id)
            if row is None or not _is_visible(conn, row, current_user):
                raise MilestoneError("里程碑不存在", 404)

            if pinned:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO milestone_pins (milestone_id, user_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (milestone_id, current_user["id"], _utc_now_text()),
                )
            else:
                conn.execute(
                    "DELETE FROM milestone_pins WHERE milestone_id = ? AND user_id = ?",
                    (milestone_id, current_user["id"]),
                )

            return _public_milestone(conn, row, current_user)


def delete_milestone(milestone_id: int, current_user: dict[str, Any]) -> dict[str, Any]:
    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_milestone_row(conn, milestone_id)
            if row is None:
                raise MilestoneError("里程碑不存在", 404)
            if int(row["created_by"]) != int(current_user["id"]) and current_user.get("role") != "admin":
                raise MilestoneError("只有创建者或管理员可以删除里程碑", 403)
            conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    return {"deleted": True, "id": milestone_id}
