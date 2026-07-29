from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from config import config
from crypto import hash_password, verify_password
from database import get_connection, process_write_lock


ROLES = {"admin", "scheduler"}
USER_STATUSES = {"pending", "active", "rejected", "disabled"}
PASSWORD_RESET_STATUSES = {"pending", "approved", "rejected"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")


class AuthError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_username(value: Any) -> str:
    username = str(value or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AuthError("用户名需为 3-40 位字母、数字、点、横线或下划线")
    return username


def normalize_display_name(value: Any) -> str:
    display_name = str(value or "").strip()
    if not display_name:
        raise AuthError("用户昵称（真实姓名）不能为空")
    if len(display_name) > 80:
        raise AuthError("用户昵称（真实姓名）不能超过 80 个字符")
    return display_name


def normalize_email(value: Any) -> str | None:
    email = str(value or "").strip().lower()
    if not email:
        return None
    if len(email) > 120 or "@" not in email:
        raise AuthError("邮箱格式无效")
    return email


def normalize_required_email(value: Any) -> str:
    email = normalize_email(value)
    if not email:
        raise AuthError("邮箱不能为空")
    return email


def normalize_account_identifier(value: Any) -> tuple[str, str]:
    identifier = str(value or "").strip()
    if not identifier:
        raise AuthError("请输入用户名或邮箱")
    if "@" in identifier:
        return "email", normalize_required_email(identifier)
    return "username", normalize_username(identifier)


def normalize_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 8:
        raise AuthError("密码至少需要 8 位")
    if len(password) > 256:
        raise AuthError("密码过长")
    return password


def normalize_register_message(value: Any) -> str:
    message = str(value or "").strip()
    if not message:
        raise AuthError("请填写给管理员的留言")
    if len(message) > 240:
        raise AuthError("给管理员的留言不能超过 240 个字符")
    return message


def normalize_role(value: Any, default: str = "scheduler") -> str:
    role = str(value or default).strip()
    if role not in ROLES:
        raise AuthError("用户角色无效")
    return role


def normalize_user_status(value: Any, default: str = "active") -> str:
    status = str(value or default).strip()
    if status not in USER_STATUSES:
        raise AuthError("用户状态无效")
    return status


def normalize_reset_message(value: Any) -> str:
    message = str(value or "").strip()
    if not message:
        raise AuthError("请填写找回密码说明")
    if len(message) > 240:
        raise AuthError("找回密码说明不能超过 240 个字符")
    return message


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_temporary_password() -> str:
    return f"Mcs-{secrets.token_urlsafe(9)}"


def user_payload(row: dict[str, Any] | sqlite3.Row, include_review_fields: bool = False) -> dict[str, Any]:
    data = dict(row)
    payload = {
        "id": data["id"],
        "username": data["username"],
        "displayName": data["display_name"],
        "email": data["email"],
        "role": data["role"],
        "status": data["status"],
        "createdAt": data["created_at"],
        "updatedAt": data["updated_at"],
        "lastLoginAt": data.get("last_login_at"),
    }
    if include_review_fields:
        payload.update(
            {
                "registerMessage": data.get("register_message"),
                "approvedBy": data.get("approved_by"),
                "approvedAt": data.get("approved_at"),
                "rejectedAt": data.get("rejected_at"),
            }
        )
    return payload


def password_reset_request_payload(row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    payload = {
        "id": data["id"],
        "userId": data["user_id"],
        "username": data["username"],
        "displayName": data["display_name"],
        "email": data["email"],
        "message": data["message"],
        "status": data["status"],
        "requestedAt": data["requested_at"],
        "reviewedBy": data.get("reviewed_by"),
        "reviewedAt": data.get("reviewed_at"),
    }
    return payload


def _fetch_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def _fetch_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def _fetch_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _active_admin_count(conn: sqlite3.Connection, exclude_user_id: int | None = None) -> int:
    if exclude_user_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND status = 'active'"
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
              FROM users
             WHERE role = 'admin'
               AND status = 'active'
               AND id != ?
            """,
            (exclude_user_id,),
        ).fetchone()
    return int(row["count"])


def _would_remove_active_admin(user: sqlite3.Row, new_role: str | None = None, new_status: str | None = None) -> bool:
    role = new_role or user["role"]
    status = new_status or user["status"]
    return user["role"] == "admin" and user["status"] == "active" and (role != "admin" or status != "active")


def _ensure_another_active_admin(conn: sqlite3.Connection, user_id: int) -> None:
    if _active_admin_count(conn, exclude_user_id=user_id) < 1:
        raise AuthError("至少需要保留一个可用管理员账号", 409)


def _ensure_email_available(conn: sqlite3.Connection, email: str | None, user_id: int | None = None) -> None:
    if not email:
        return
    existing = _fetch_user_by_email(conn, email)
    if existing and existing["id"] != user_id:
        raise AuthError("邮箱已被使用", 409)


def register_user(payload: dict[str, Any]) -> dict[str, Any]:
    username = normalize_username(payload.get("username"))
    display_name = normalize_display_name(payload.get("displayName") or payload.get("display_name"))
    email = normalize_required_email(payload.get("email"))
    password = normalize_password(payload.get("password"))
    register_message = normalize_register_message(
        payload.get("registerMessage") or payload.get("register_message")
    )
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            if _fetch_user_by_username(conn, username):
                raise AuthError("用户名已存在", 409)
            _ensure_email_available(conn, email)

            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, email, password_hash, role, status,
                    register_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'scheduler', 'pending', ?, ?, ?)
                """,
                (
                    username,
                    display_name,
                    email,
                    hash_password(password),
                    register_message,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()

    return user_payload(row, include_review_fields=True)


def create_admin_user(
    *,
    username: str,
    display_name: str,
    password: str,
    email: str,
) -> dict[str, Any]:
    normalized_username = normalize_username(username)
    normalized_display_name = normalize_display_name(display_name)
    normalized_email = normalize_required_email(email)
    normalized_password = normalize_password(password)
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            if _fetch_user_by_username(conn, normalized_username):
                raise AuthError("用户名已存在", 409)
            _ensure_email_available(conn, normalized_email)

            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, email, password_hash, role, status,
                    approved_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'admin', 'active', ?, ?, ?)
                """,
                (
                    normalized_username,
                    normalized_display_name,
                    normalized_email,
                    hash_password(normalized_password),
                    now,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()

    return user_payload(row, include_review_fields=True)


def login_user(
    payload: dict[str, Any],
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    identifier_type, identifier = normalize_account_identifier(
        payload.get("account") or payload.get("username") or payload.get("email")
    )
    password = str(payload.get("password") or "")

    with process_write_lock():
        with get_connection() as conn:
            row = (
                _fetch_user_by_email(conn, identifier)
                if identifier_type == "email"
                else _fetch_user_by_username(conn, identifier)
            )
            if row is None or not verify_password(password, row["password_hash"]):
                raise AuthError("用户名、邮箱或密码错误", 401)

            if row["status"] == "pending":
                raise AuthError("账号待管理员审核", 403)
            if row["status"] == "rejected":
                raise AuthError("账号申请已被拒绝", 403)
            if row["status"] == "disabled":
                raise AuthError("账号已停用", 403)
            if row["status"] != "active":
                raise AuthError("账号状态异常", 403)

            now = utc_now()
            now_text = isoformat(now)
            ttl_days = max(1, config.session_ttl_days)
            expires_at = isoformat(now + timedelta(days=ttl_days))
            token = secrets.token_urlsafe(32)
            conn.execute(
                """
                INSERT INTO user_sessions (
                    user_id, token_hash, expires_at, created_ip, user_agent,
                    created_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    token_hash(token),
                    expires_at,
                    ip_address,
                    user_agent,
                    now_text,
                    now_text,
                ),
            )
            conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now_text, now_text, row["id"]))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()

    return {"token": token, "expiresAt": expires_at, "user": user_payload(row)}


def get_authenticated_user(token: str | None) -> dict[str, Any]:
    if not token:
        raise AuthError("请先登录", 401)

    hashed_token = token_hash(token)
    now_text = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT users.*, user_sessions.id AS session_id
                  FROM user_sessions
                  JOIN users ON users.id = user_sessions.user_id
                 WHERE user_sessions.token_hash = ?
                   AND user_sessions.revoked_at IS NULL
                   AND user_sessions.expires_at > ?
                """,
                (hashed_token, now_text),
            ).fetchone()
            if row is None:
                raise AuthError("请先登录", 401)

            if row["status"] != "active":
                conn.execute(
                    """
                    UPDATE user_sessions
                       SET revoked_at = ?
                     WHERE id = ?
                    """,
                    (now_text, row["session_id"]),
                )
                raise AuthError("账号不可用", 403)

            conn.execute(
                "UPDATE user_sessions SET last_seen_at = ? WHERE id = ?",
                (now_text, row["session_id"]),
            )

    return user_payload(row)


def logout_token(token: str | None) -> None:
    if not token:
        return

    now_text = isoformat(utc_now())
    with process_write_lock():
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE user_sessions
                   SET revoked_at = ?
                 WHERE token_hash = ?
                   AND revoked_at IS NULL
                """,
                (now_text, token_hash(token)),
            )


def revoke_user_sessions(user_id: int) -> None:
    now_text = isoformat(utc_now())
    with process_write_lock():
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE user_sessions
                   SET revoked_at = ?
                 WHERE user_id = ?
                   AND revoked_at IS NULL
                """,
                (now_text, user_id),
            )


def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    username = normalize_username(payload.get("username"))
    display_name = normalize_display_name(payload.get("displayName") or payload.get("display_name"))
    email = normalize_required_email(payload.get("email"))
    password = normalize_password(payload.get("password"))
    role = normalize_role(payload.get("role"), "scheduler")
    status = normalize_user_status(payload.get("status"), "active")
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            if _fetch_user_by_username(conn, username):
                raise AuthError("用户名已存在", 409)
            _ensure_email_available(conn, email)

            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, email, password_hash, role, status,
                    approved_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    display_name,
                    email,
                    hash_password(password),
                    role,
                    status,
                    now if status == "active" else None,
                    now,
                    now,
                ),
            )
            row = _fetch_user_by_id(conn, int(cursor.lastrowid))

    return user_payload(row, include_review_fields=True)


def list_users(status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in USER_STATUSES:
        raise AuthError("用户状态参数无效")

    with get_connection() as conn:
        if normalized_status:
            rows = conn.execute(
                """
                SELECT *
                  FROM users
                 WHERE status = ?
                 ORDER BY created_at DESC, id DESC
                """,
                (normalized_status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                  FROM users
                 ORDER BY
                    CASE status
                      WHEN 'pending' THEN 0
                      WHEN 'active' THEN 1
                      WHEN 'disabled' THEN 2
                      ELSE 3
                    END,
                    created_at DESC,
                    id DESC
                """
            ).fetchall()

    return [user_payload(row, include_review_fields=True) for row in rows]


def search_user_directory(query: Any, limit: int = 20) -> list[dict[str, Any]]:
    normalized_query = str(query or "").strip().casefold()
    if len(normalized_query) > 120:
        raise AuthError("搜索内容不能超过 120 个字符")
    normalized_limit = max(1, min(int(limit), 50))

    with get_connection() as conn:
        if normalized_query:
            escaped_query = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{escaped_query}%"
            rows = conn.execute(
                """
                SELECT id, username, display_name, email
                  FROM users
                 WHERE status = 'active'
                   AND (
                        LOWER(username) LIKE ? ESCAPE '\\'
                     OR LOWER(display_name) LIKE ? ESCAPE '\\'
                     OR LOWER(email) LIKE ? ESCAPE '\\'
                   )
                 ORDER BY
                    CASE
                      WHEN LOWER(username) = ? THEN 0
                      WHEN LOWER(email) = ? THEN 0
                      WHEN LOWER(display_name) = ? THEN 1
                      WHEN LOWER(username) LIKE ? ESCAPE '\\' THEN 2
                      WHEN LOWER(display_name) LIKE ? ESCAPE '\\' THEN 3
                      ELSE 4
                    END,
                    display_name COLLATE NOCASE ASC,
                    username COLLATE NOCASE ASC
                 LIMIT ?
                """,
                (
                    pattern,
                    pattern,
                    pattern,
                    normalized_query,
                    normalized_query,
                    normalized_query,
                    f"{escaped_query}%",
                    f"{escaped_query}%",
                    normalized_limit,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, username, display_name, email
                  FROM users
                 WHERE status = 'active'
                 ORDER BY display_name COLLATE NOCASE ASC, username COLLATE NOCASE ASC
                 LIMIT ?
                """,
                (normalized_limit,),
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


def get_user(user_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        row = _fetch_user_by_id(conn, user_id)
        if row is None:
            raise AuthError("用户不存在", 404)
    return user_payload(row, include_review_fields=True)


def update_user(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_user_by_id(conn, user_id)
            if row is None:
                raise AuthError("用户不存在", 404)

            display_name = (
                normalize_display_name(payload.get("displayName") or payload.get("display_name"))
                if "displayName" in payload or "display_name" in payload
                else row["display_name"]
            )
            email = normalize_required_email(payload.get("email")) if "email" in payload else row["email"]
            role = normalize_role(payload.get("role"), row["role"]) if "role" in payload else row["role"]
            status = (
                normalize_user_status(payload.get("status"), row["status"])
                if "status" in payload
                else row["status"]
            )

            if _would_remove_active_admin(row, role, status):
                _ensure_another_active_admin(conn, user_id)
            _ensure_email_available(conn, email, user_id)

            now = isoformat(utc_now())
            conn.execute(
                """
                UPDATE users
                   SET display_name = ?,
                       email = ?,
                       role = ?,
                       status = ?,
                       updated_at = ?,
                       approved_at = CASE
                         WHEN status != 'active' AND ? = 'active' THEN ?
                         ELSE approved_at
                       END
                 WHERE id = ?
                """,
                (display_name, email, role, status, now, status, now, user_id),
            )
            row = _fetch_user_by_id(conn, user_id)

    if row["status"] != "active":
        revoke_user_sessions(user_id)
    return user_payload(row, include_review_fields=True)


def delete_user(user_id: int, admin_user_id: int) -> dict[str, Any]:
    if user_id == admin_user_id:
        raise AuthError("不能删除当前登录的管理员账号", 409)

    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_user_by_id(conn, user_id)
            if row is None:
                raise AuthError("用户不存在", 404)
            if row["role"] == "admin" and row["status"] == "active":
                _ensure_another_active_admin(conn, user_id)

            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    return {"id": user_id, "deleted": True}

def change_own_password(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    current_password = str(payload.get("currentPassword") or payload.get("current_password") or "")
    new_password = normalize_password(payload.get("newPassword") or payload.get("new_password"))
    confirm_password = str(payload.get("confirmPassword") or payload.get("confirm_password") or "")

    if new_password != confirm_password:
        raise AuthError("两次输入的新密码不一致")

    if current_password == new_password:
        raise AuthError("新密码不能与旧密码相同")

    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_user_by_id(conn, user_id)

            if row is None:
                raise AuthError("用户不存在", 404)

            if row["status"] != "active":
                raise AuthError("账号不可用", 403)

            if not verify_password(current_password, row["password_hash"]):
                raise AuthError("当前密码错误", 401)

            conn.execute(
                """
                UPDATE users
                   SET password_hash = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (hash_password(new_password), now, user_id),
            )

    revoke_user_sessions(user_id)
    return {"ok": True}


def reset_user_password(user_id: int) -> dict[str, Any]:
    temporary_password = generate_temporary_password()
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = _fetch_user_by_id(conn, user_id)
            if row is None:
                raise AuthError("用户不存在", 404)

            conn.execute(
                """
                UPDATE users
                   SET password_hash = ?,
                       status = CASE WHEN status = 'disabled' THEN status ELSE 'active' END,
                       updated_at = ?
                 WHERE id = ?
                """,
                (hash_password(temporary_password), now, user_id),
            )
            row = _fetch_user_by_id(conn, user_id)

    revoke_user_sessions(user_id)
    return {"user": user_payload(row, include_review_fields=True), "temporaryPassword": temporary_password}


def request_password_reset(payload: dict[str, Any]) -> dict[str, Any]:
    identifier_type, identifier = normalize_account_identifier(
        payload.get("account") or payload.get("username") or payload.get("email")
    )
    message = normalize_reset_message(payload.get("message") or payload.get("resetMessage"))
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            user = (
                _fetch_user_by_email(conn, identifier)
                if identifier_type == "email"
                else _fetch_user_by_username(conn, identifier)
            )
            if user is None:
                raise AuthError("用户不存在", 404)
            if user["status"] == "pending":
                raise AuthError("账号仍在待审核，请等待管理员处理", 409)
            if user["status"] == "rejected":
                raise AuthError("账号申请已被拒绝，不能找回密码", 409)

            existing = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 WHERE user_id = ?
                   AND password_reset_requests.status = 'pending'
                 ORDER BY password_reset_requests.id DESC
                 LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            if existing:
                return password_reset_request_payload(existing)

            cursor = conn.execute(
                """
                INSERT INTO password_reset_requests (user_id, message, status, requested_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (user["id"], message, now),
            )
            row = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 WHERE password_reset_requests.id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

    return password_reset_request_payload(row)


def list_password_reset_requests(status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in PASSWORD_RESET_STATUSES:
        raise AuthError("密码找回状态参数无效")

    with get_connection() as conn:
        if normalized_status:
            rows = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 WHERE password_reset_requests.status = ?
                 ORDER BY requested_at DESC, password_reset_requests.id DESC
                """,
                (normalized_status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 ORDER BY
                    CASE password_reset_requests.status
                      WHEN 'pending' THEN 0
                      WHEN 'approved' THEN 1
                      ELSE 2
                    END,
                    requested_at DESC,
                    password_reset_requests.id DESC
                """
            ).fetchall()

    return [password_reset_request_payload(row) for row in rows]


def approve_password_reset_request(request_id: int, admin_user_id: int) -> dict[str, Any]:
    temporary_password = generate_temporary_password()
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM password_reset_requests
                 WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise AuthError("密码找回申请不存在", 404)
            if row["status"] != "pending":
                raise AuthError("密码找回申请已处理", 409)

            user = _fetch_user_by_id(conn, row["user_id"])
            if user is None:
                raise AuthError("用户不存在", 404)
            if user["status"] == "disabled":
                raise AuthError("账号已停用，不能找回密码", 409)

            conn.execute(
                """
                UPDATE users
                   SET password_hash = ?,
                       status = 'active',
                       updated_at = ?
                 WHERE id = ?
                """,
                (hash_password(temporary_password), now, row["user_id"]),
            )
            conn.execute(
                """
                UPDATE password_reset_requests
                   SET status = 'approved',
                       reviewed_by = ?,
                       reviewed_at = ?
                 WHERE id = ?
                """,
                (admin_user_id, now, request_id),
            )
            request_row = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 WHERE password_reset_requests.id = ?
                """,
                (request_id,),
            ).fetchone()
            user_row = _fetch_user_by_id(conn, row["user_id"])

    revoke_user_sessions(int(row["user_id"]))
    return {
        "request": password_reset_request_payload(request_row),
        "user": user_payload(user_row, include_review_fields=True),
        "temporaryPassword": temporary_password,
    }


def reject_password_reset_request(request_id: int, admin_user_id: int) -> dict[str, Any]:
    now = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM password_reset_requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                raise AuthError("密码找回申请不存在", 404)
            if row["status"] != "pending":
                raise AuthError("密码找回申请已处理", 409)

            conn.execute(
                """
                UPDATE password_reset_requests
                   SET status = 'rejected',
                       reviewed_by = ?,
                       reviewed_at = ?
                 WHERE id = ?
                """,
                (admin_user_id, now, request_id),
            )
            request_row = conn.execute(
                """
                SELECT password_reset_requests.*, users.username, users.display_name, users.email
                  FROM password_reset_requests
                  JOIN users ON users.id = password_reset_requests.user_id
                 WHERE password_reset_requests.id = ?
                """,
                (request_id,),
            ).fetchone()

    return password_reset_request_payload(request_row)


def approve_user(user_id: int, admin_user_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    role = normalize_role((payload or {}).get("role"), "scheduler")
    now_text = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("用户不存在", 404)
            if row["status"] == "active":
                raise AuthError("用户已通过审核", 409)
            if row["role"] == "admin" and role != "admin":
                raise AuthError("管理员角色不能在审核时降级", 409)

            conn.execute(
                """
                UPDATE users
                   SET status = 'active',
                       role = ?,
                       approved_by = ?,
                       approved_at = ?,
                       rejected_at = NULL,
                       updated_at = ?
                 WHERE id = ?
                """,
                (role, admin_user_id, now_text, now_text, user_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return user_payload(row, include_review_fields=True)


def reject_user(user_id: int) -> dict[str, Any]:
    now_text = isoformat(utc_now())

    with process_write_lock():
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("用户不存在", 404)
            if row["role"] == "admin":
                raise AuthError("不能拒绝管理员账号", 409)
            if row["status"] == "active":
                raise AuthError("已通过审核的账号不能直接拒绝", 409)

            conn.execute(
                """
                UPDATE users
                   SET status = 'rejected',
                       rejected_at = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (now_text, now_text, user_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    revoke_user_sessions(user_id)
    return user_payload(row, include_review_fields=True)
