from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from pypinyin import Style, lazy_pinyin
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import config
from crypto import hash_password, verify_password
from database import process_write_lock, session_scope
from mailer import send_password_reset_email
from models import (
    PasswordResetRequest,
    PasswordResetToken,
    User,
    UserSession,
    model_to_dict,
)


ROLES = {"admin", "scheduler"}
USER_STATUSES = {"pending", "active", "rejected", "disabled"}
PASSWORD_RESET_STATUSES = {"pending", "approved", "rejected"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
DEFAULT_PHONE_NUMBER = ""


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


def normalize_job_title(value: Any) -> str:
    job_title = str(value or "").strip()
    if len(job_title) > 80:
        raise AuthError("职称不能超过 80 个字符")
    return job_title


def normalize_phone_number(value: Any) -> str:
    phone_number = str(value or "").strip()
    if len(phone_number) > 40:
        raise AuthError("电话号码不能超过 40 个字符")
    return "" if re.fullmatch(r"\+\d{1,4}", phone_number) else phone_number


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


def _as_user_data(user: User | dict[str, Any]) -> dict[str, Any]:
    return model_to_dict(user) if isinstance(user, User) else dict(user)


def user_payload(
    user: User | dict[str, Any], include_review_fields: bool = False
) -> dict[str, Any]:
    data = _as_user_data(user)
    payload = {
        "id": data["id"],
        "username": data["username"],
        "displayName": data["display_name"],
        "email": data["email"],
        "jobTitle": data.get("job_title", ""),
        "phoneNumber": normalize_phone_number(data.get("phone_number", "")),
        "emailNotificationsEnabled": bool(data.get("email_notifications_enabled", True)),
        "customThemeColor": data.get("custom_theme_color"),
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


def password_reset_request_payload(
    request: PasswordResetRequest | dict[str, Any],
    user: User | None = None,
) -> dict[str, Any]:
    data = model_to_dict(request) if isinstance(request, PasswordResetRequest) else dict(request)
    return {
        "id": data["id"],
        "userId": data["user_id"],
        "username": user.username if user else data["username"],
        "displayName": user.display_name if user else data["display_name"],
        "email": user.email if user else data["email"],
        "message": data["message"],
        "status": data["status"],
        "requestedAt": data["requested_at"],
        "reviewedBy": data.get("reviewed_by"),
        "reviewedAt": data.get("reviewed_at"),
    }


def _fetch_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def _fetch_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def _fetch_user_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def _active_admin_count(session: Session, exclude_user_id: int | None = None) -> int:
    statement = select(func.count()).select_from(User).where(
        User.role == "admin", User.status == "active"
    )
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return int(session.scalar(statement) or 0)


def _would_remove_active_admin(
    user: User, new_role: str | None = None, new_status: str | None = None
) -> bool:
    role = new_role or user.role
    status = new_status or user.status
    return user.role == "admin" and user.status == "active" and (
        role != "admin" or status != "active"
    )


def _ensure_another_active_admin(session: Session, user_id: int) -> None:
    if _active_admin_count(session, exclude_user_id=user_id) < 1:
        raise AuthError("至少需要保留一个可用管理员账号", 409)


def _ensure_email_available(
    session: Session, email: str | None, user_id: int | None = None
) -> None:
    if not email:
        return
    existing = _fetch_user_by_email(session, email)
    if existing and existing.id != user_id:
        raise AuthError("邮箱已被使用", 409)


def _invalidate_reset_tokens(session: Session, user_id: int, used_at: str) -> None:
    tokens = session.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for token in tokens:
        token.used_at = used_at


def update_own_preferences(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if "emailNotificationsEnabled" in payload:
        enabled = payload["emailNotificationsEnabled"]
    else:
        enabled = payload.get("email_notifications_enabled")
    enabled_present = "emailNotificationsEnabled" in payload or "email_notifications_enabled" in payload

    if enabled_present and not isinstance(enabled, bool):
        raise AuthError("邮件提醒设置无效")
    color_present = "customThemeColor" in payload or "custom_theme_color" in payload
    if not enabled_present and not color_present:
        raise AuthError("没有可更新的首选项")

    custom_color: str | None = None
    if color_present:
        raw_color = payload.get("customThemeColor", payload.get("custom_theme_color"))
        if raw_color is not None and str(raw_color).strip():
            custom_color = str(raw_color).strip().lower()
            if not re.fullmatch(r"#[0-9a-f]{6}", custom_color):
                raise AuthError("自定义颜色格式应为 #xxxxxx")

    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if enabled_present:
                user.email_notifications_enabled = bool(enabled)
            if color_present:
                user.custom_theme_color = custom_color
            user.updated_at = isoformat(utc_now())
            session.flush()
            return user_payload(user)


def update_own_profile(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "displayName", "display_name", "jobTitle", "job_title",
        "phoneNumber", "phone_number", "email",
    }
    if not any(key in payload for key in fields):
        raise AuthError("没有可更新的个人资料")

    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if "displayName" in payload or "display_name" in payload:
                user.display_name = normalize_display_name(
                    payload.get("displayName") or payload.get("display_name")
                )
            if "jobTitle" in payload or "job_title" in payload:
                user.job_title = normalize_job_title(
                    payload.get("jobTitle") or payload.get("job_title")
                )
            if "phoneNumber" in payload or "phone_number" in payload:
                user.phone_number = normalize_phone_number(
                    payload.get("phoneNumber") or payload.get("phone_number")
                )
            if "email" in payload:
                email = normalize_required_email(payload.get("email"))
                _ensure_email_available(session, email, user_id)
                user.email = email
            user.updated_at = isoformat(utc_now())
            session.flush()
            return user_payload(user)


def _new_user(
    *, username: str, display_name: str, email: str, job_title: str,
    phone_number: str, password: str, role: str, status: str,
    now: str, register_message: str | None = None,
) -> User:
    return User(
        username=username,
        display_name=display_name,
        email=email,
        job_title=job_title,
        phone_number=phone_number,
        password_hash=hash_password(password),
        role=role,
        status=status,
        register_message=register_message,
        approved_at=now if status == "active" else None,
        created_at=now,
        updated_at=now,
    )


def register_user(payload: dict[str, Any]) -> dict[str, Any]:
    username = normalize_username(payload.get("username"))
    display_name = normalize_display_name(payload.get("displayName") or payload.get("display_name"))
    email = normalize_required_email(payload.get("email"))
    job_title = normalize_job_title(payload.get("jobTitle") or payload.get("job_title"))
    phone_number = normalize_phone_number(payload.get("phoneNumber", payload.get("phone_number", "")))
    password = normalize_password(payload.get("password"))
    message = normalize_register_message(payload.get("registerMessage") or payload.get("register_message"))
    now = isoformat(utc_now())

    with process_write_lock():
        with session_scope() as session:
            if _fetch_user_by_username(session, username):
                raise AuthError("用户名已存在", 409)
            _ensure_email_available(session, email)
            user = _new_user(
                username=username, display_name=display_name, email=email,
                job_title=job_title, phone_number=phone_number, password=password,
                role="scheduler", status="pending", now=now, register_message=message,
            )
            session.add(user)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AuthError("用户名或邮箱已存在", 409) from exc
            return user_payload(user, include_review_fields=True)


def create_admin_user(
    *, username: str, display_name: str, password: str, email: str,
    job_title: str = "", phone_number: str = DEFAULT_PHONE_NUMBER,
) -> dict[str, Any]:
    return _create_user_record(
        username=normalize_username(username),
        display_name=normalize_display_name(display_name),
        email=normalize_required_email(email),
        job_title=normalize_job_title(job_title),
        phone_number=normalize_phone_number(phone_number),
        password=normalize_password(password),
        role="admin",
        status="active",
    )


def _create_user_record(**values: Any) -> dict[str, Any]:
    now = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            if _fetch_user_by_username(session, values["username"]):
                raise AuthError("用户名已存在", 409)
            _ensure_email_available(session, values["email"])
            user = _new_user(now=now, **values)
            session.add(user)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AuthError("用户名或邮箱已存在", 409) from exc
            return user_payload(user, include_review_fields=True)


def login_user(
    payload: dict[str, Any], ip_address: str | None, user_agent: str | None
) -> dict[str, Any]:
    identifier_type, identifier = normalize_account_identifier(
        payload.get("account") or payload.get("username") or payload.get("email")
    )
    password = str(payload.get("password") or "")
    with process_write_lock():
        with session_scope() as session:
            user = (
                _fetch_user_by_email(session, identifier)
                if identifier_type == "email"
                else _fetch_user_by_username(session, identifier)
            )
            if user is None or not verify_password(password, user.password_hash):
                raise AuthError("用户名、邮箱或密码错误", 401)
            messages = {
                "pending": "账号待管理员审核",
                "rejected": "账号申请已被拒绝",
                "disabled": "账号已停用",
            }
            if user.status != "active":
                raise AuthError(messages.get(user.status, "账号状态异常"), 403)

            now = utc_now()
            now_text = isoformat(now)
            expires_at = isoformat(now + timedelta(days=max(1, config.session_ttl_days)))
            raw_token = secrets.token_urlsafe(32)
            session.add(
                UserSession(
                    user_id=user.id,
                    token_hash=token_hash(raw_token),
                    expires_at=expires_at,
                    created_ip=ip_address,
                    user_agent=user_agent,
                    created_at=now_text,
                    last_seen_at=now_text,
                )
            )
            user.last_login_at = now_text
            user.updated_at = now_text
            session.flush()
            return {"token": raw_token, "expiresAt": expires_at, "user": user_payload(user)}


def get_authenticated_user(token: str | None) -> dict[str, Any]:
    if not token:
        raise AuthError("请先登录", 401)
    now_text = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            row = session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.token_hash == token_hash(token),
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now_text,
                )
            ).one_or_none()
            if row is None:
                raise AuthError("请先登录", 401)
            user_session, user = row
            if user.status != "active":
                user_session.revoked_at = now_text
                raise AuthError("账号不可用", 403)
            user_session.last_seen_at = now_text
            return user_payload(user)


def logout_token(token: str | None) -> None:
    if not token:
        return
    with process_write_lock():
        with session_scope() as session:
            user_session = session.scalar(
                select(UserSession).where(
                    UserSession.token_hash == token_hash(token),
                    UserSession.revoked_at.is_(None),
                )
            )
            if user_session:
                user_session.revoked_at = isoformat(utc_now())


def revoke_user_sessions(user_id: int) -> None:
    now_text = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            sessions = session.scalars(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                )
            ).all()
            for user_session in sessions:
                user_session.revoked_at = now_text


def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    return _create_user_record(
        username=normalize_username(payload.get("username")),
        display_name=normalize_display_name(payload.get("displayName") or payload.get("display_name")),
        email=normalize_required_email(payload.get("email")),
        job_title=normalize_job_title(payload.get("jobTitle") or payload.get("job_title")),
        phone_number=normalize_phone_number(payload.get("phoneNumber", payload.get("phone_number", ""))),
        password=normalize_password(payload.get("password")),
        role=normalize_role(payload.get("role"), "scheduler"),
        status=normalize_user_status(payload.get("status"), "active"),
    )


def list_users(status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in USER_STATUSES:
        raise AuthError("用户状态参数无效")
    statement = select(User)
    if normalized_status:
        statement = statement.where(User.status == normalized_status).order_by(
            User.created_at.desc(), User.id.desc()
        )
    else:
        statement = statement.order_by(
            case(
                (User.status == "pending", 0), (User.status == "active", 1),
                (User.status == "disabled", 2), else_=3,
            ),
            User.created_at.desc(), User.id.desc(),
        )
    with session_scope() as session:
        users = session.scalars(statement).all()
        return [user_payload(user, include_review_fields=True) for user in users]


def _directory_sort_key(user: User) -> tuple[str, str, str]:
    display_name = str(user.display_name or "").strip()
    pinyin_name = "".join(lazy_pinyin(display_name, style=Style.NORMAL)).casefold()
    return pinyin_name, display_name.casefold(), str(user.username or "").casefold()


def search_user_directory(query: Any, limit: int = 500) -> list[dict[str, Any]]:
    normalized_query = str(query or "").strip().casefold()
    if len(normalized_query) > 120:
        raise AuthError("搜索内容不能超过 120 个字符")
    normalized_limit = max(1, min(int(limit), 500))
    statement = select(User).where(User.status == "active")
    if normalized_query:
        statement = statement.where(
            or_(
                User.username.icontains(normalized_query, autoescape=True),
                User.display_name.icontains(normalized_query, autoescape=True),
                User.email.icontains(normalized_query, autoescape=True),
                User.job_title.icontains(normalized_query, autoescape=True),
                User.phone_number.icontains(normalized_query, autoescape=True),
            )
        )
    with session_scope() as session:
        users = sorted(session.scalars(statement).all(), key=_directory_sort_key)[:normalized_limit]
        return [
            {
                "id": user.id, "username": user.username,
                "displayName": user.display_name, "email": user.email,
                "jobTitle": user.job_title,
                "phoneNumber": normalize_phone_number(user.phone_number),
            }
            for user in users
        ]


def get_user(user_id: int) -> dict[str, Any]:
    with session_scope() as session:
        user = _fetch_user_by_id(session, user_id)
        if user is None:
            raise AuthError("用户不存在", 404)
        return user_payload(user, include_review_fields=True)


def update_user(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    should_revoke = False
    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            old_status = user.status
            display_name = normalize_display_name(payload.get("displayName") or payload.get("display_name")) if "displayName" in payload or "display_name" in payload else user.display_name
            email = normalize_required_email(payload.get("email")) if "email" in payload else user.email
            job_title = normalize_job_title(payload.get("jobTitle") or payload.get("job_title")) if "jobTitle" in payload or "job_title" in payload else user.job_title
            phone_number = normalize_phone_number(payload.get("phoneNumber") or payload.get("phone_number")) if "phoneNumber" in payload or "phone_number" in payload else user.phone_number
            role = normalize_role(payload.get("role"), user.role) if "role" in payload else user.role
            status = normalize_user_status(payload.get("status"), user.status) if "status" in payload else user.status
            if _would_remove_active_admin(user, role, status):
                _ensure_another_active_admin(session, user_id)
            _ensure_email_available(session, email, user_id)
            now = isoformat(utc_now())
            user.display_name, user.email = display_name, email
            user.job_title, user.phone_number = job_title, phone_number
            user.role, user.status, user.updated_at = role, status, now
            if old_status != "active" and status == "active":
                user.approved_at = now
            should_revoke = status != "active"
            session.flush()
            result = user_payload(user, include_review_fields=True)
    if should_revoke:
        revoke_user_sessions(user_id)
    return result


def delete_user(user_id: int, admin_user_id: int) -> dict[str, Any]:
    if user_id == admin_user_id:
        raise AuthError("不能删除当前登录的管理员账号", 409)
    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.role == "admin" and user.status == "active":
                _ensure_another_active_admin(session, user_id)
            session.delete(user)
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
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.status != "active":
                raise AuthError("账号不可用", 403)
            if not verify_password(current_password, user.password_hash):
                raise AuthError("当前密码错误", 401)
            user.password_hash = hash_password(new_password)
            user.updated_at = now
            _invalidate_reset_tokens(session, user_id, now)
    revoke_user_sessions(user_id)
    return {"ok": True}


def reset_user_password(user_id: int) -> dict[str, Any]:
    temporary_password = generate_temporary_password()
    now = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_id(session, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            user.password_hash = hash_password(temporary_password)
            if user.status != "disabled":
                user.status = "active"
            user.updated_at = now
            _invalidate_reset_tokens(session, user_id, now)
            result = user_payload(user, include_review_fields=True)
    revoke_user_sessions(user_id)
    return {"user": result, "temporaryPassword": temporary_password}


def request_password_reset(payload: dict[str, Any]) -> dict[str, Any]:
    identifier_type, identifier = normalize_account_identifier(
        payload.get("account") or payload.get("username") or payload.get("email")
    )
    message = normalize_reset_message(payload.get("message") or payload.get("resetMessage"))
    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_email(session, identifier) if identifier_type == "email" else _fetch_user_by_username(session, identifier)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.status == "pending":
                raise AuthError("账号仍在待审核，请等待管理员处理", 409)
            if user.status == "rejected":
                raise AuthError("账号申请已被拒绝，不能找回密码", 409)
            request = session.scalar(
                select(PasswordResetRequest)
                .where(PasswordResetRequest.user_id == user.id, PasswordResetRequest.status == "pending")
                .order_by(PasswordResetRequest.id.desc()).limit(1)
            )
            if request is None:
                request = PasswordResetRequest(
                    user_id=user.id, message=message, status="pending",
                    requested_at=isoformat(utc_now()),
                )
                session.add(request)
                session.flush()
            return password_reset_request_payload(request, user)


def request_email_password_reset(payload: dict[str, Any]) -> dict[str, Any]:
    email = normalize_required_email(payload.get("email"))
    now = utc_now()
    now_text = isoformat(now)
    cooldown_start = isoformat(now - timedelta(seconds=max(0, config.password_reset_cooldown_seconds)))
    expires_minutes = max(1, config.password_reset_token_ttl_minutes)
    raw_token: str | None = None
    token_id: int | None = None
    user_data: dict[str, Any] | None = None
    with process_write_lock():
        with session_scope() as session:
            user = _fetch_user_by_email(session, email)
            if user is not None and user.status == "active":
                recent = session.scalar(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == user.id,
                        PasswordResetToken.used_at.is_(None),
                        PasswordResetToken.expires_at > now_text,
                        PasswordResetToken.created_at >= cooldown_start,
                    ).order_by(PasswordResetToken.id.desc()).limit(1)
                )
                if recent is None:
                    raw_token = secrets.token_urlsafe(32)
                    reset_token = PasswordResetToken(
                        user_id=user.id, token_hash=token_hash(raw_token),
                        expires_at=isoformat(now + timedelta(minutes=expires_minutes)),
                        created_at=now_text,
                    )
                    session.add(reset_token)
                    session.flush()
                    token_id = reset_token.id
                    user_data = _as_user_data(user)

    if raw_token and token_id and user_data:
        reset_url = f"{config.password_reset_url_origin}/?{urlencode({'resetToken': raw_token})}"
        delivery = send_password_reset_email(
            user_data["email"], user_data["display_name"], reset_url, expires_minutes
        )
        with process_write_lock():
            with session_scope() as session:
                tokens = session.scalars(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == user_data["id"],
                        PasswordResetToken.used_at.is_(None),
                    )
                ).all()
                for reset_token in tokens:
                    if delivery["status"] != "sent" or reset_token.id != token_id:
                        reset_token.used_at = now_text
    return {"ok": True, "message": "如果该邮箱与有效账号匹配，密码重置邮件将很快发送。"}


def reset_password_with_email_token(payload: dict[str, Any]) -> dict[str, Any]:
    raw_token = str(payload.get("token") or "").strip()
    if not raw_token or len(raw_token) > 256:
        raise AuthError("重置链接无效或已过期", 400)
    new_password = normalize_password(payload.get("newPassword") or payload.get("new_password"))
    confirm = str(payload.get("confirmPassword") or payload.get("confirm_password") or "")
    if new_password != confirm:
        raise AuthError("两次输入的新密码不一致")
    now_text = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            row = session.execute(
                select(PasswordResetToken, User)
                .join(User, User.id == PasswordResetToken.user_id)
                .where(
                    PasswordResetToken.token_hash == token_hash(raw_token),
                    PasswordResetToken.used_at.is_(None),
                    PasswordResetToken.expires_at > now_text,
                )
            ).one_or_none()
            if row is None or row[1].status != "active":
                raise AuthError("重置链接无效或已过期", 400)
            user = row[1]
            user.password_hash = hash_password(new_password)
            user.updated_at = now_text
            _invalidate_reset_tokens(session, user.id, now_text)
            user_id = user.id
    revoke_user_sessions(user_id)
    return {"ok": True}


def list_password_reset_requests(status: str | None = None) -> list[dict[str, Any]]:
    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in PASSWORD_RESET_STATUSES:
        raise AuthError("密码找回状态参数无效")
    statement = select(PasswordResetRequest, User).join(User, User.id == PasswordResetRequest.user_id)
    if normalized_status:
        statement = statement.where(PasswordResetRequest.status == normalized_status).order_by(
            PasswordResetRequest.requested_at.desc(), PasswordResetRequest.id.desc()
        )
    else:
        statement = statement.order_by(
            case(
                (PasswordResetRequest.status == "pending", 0),
                (PasswordResetRequest.status == "approved", 1), else_=2,
            ),
            PasswordResetRequest.requested_at.desc(), PasswordResetRequest.id.desc(),
        )
    with session_scope() as session:
        return [password_reset_request_payload(request, user) for request, user in session.execute(statement).all()]


def approve_password_reset_request(request_id: int, admin_user_id: int) -> dict[str, Any]:
    temporary_password = generate_temporary_password()
    now = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            request = session.get(PasswordResetRequest, request_id)
            if request is None:
                raise AuthError("密码找回申请不存在", 404)
            if request.status != "pending":
                raise AuthError("密码找回申请已处理", 409)
            user = session.get(User, request.user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.status == "disabled":
                raise AuthError("账号已停用，不能找回密码", 409)
            user.password_hash = hash_password(temporary_password)
            user.status, user.updated_at = "active", now
            request.status, request.reviewed_by, request.reviewed_at = "approved", admin_user_id, now
            _invalidate_reset_tokens(session, user.id, now)
            session.flush()
            user_id = user.id
            result = {
                "request": password_reset_request_payload(request, user),
                "user": user_payload(user, include_review_fields=True),
                "temporaryPassword": temporary_password,
            }
    revoke_user_sessions(user_id)
    return result


def reject_password_reset_request(request_id: int, admin_user_id: int) -> dict[str, Any]:
    with process_write_lock():
        with session_scope() as session:
            request = session.get(PasswordResetRequest, request_id)
            if request is None:
                raise AuthError("密码找回申请不存在", 404)
            if request.status != "pending":
                raise AuthError("密码找回申请已处理", 409)
            user = session.get(User, request.user_id)
            request.status = "rejected"
            request.reviewed_by = admin_user_id
            request.reviewed_at = isoformat(utc_now())
            return password_reset_request_payload(request, user)


def approve_user(user_id: int, admin_user_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    role = normalize_role((payload or {}).get("role"), "scheduler")
    now = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.status == "active":
                raise AuthError("用户已通过审核", 409)
            if user.role == "admin" and role != "admin":
                raise AuthError("管理员角色不能在审核时降级", 409)
            user.status, user.role = "active", role
            user.approved_by, user.approved_at = admin_user_id, now
            user.rejected_at, user.updated_at = None, now
            return user_payload(user, include_review_fields=True)


def reject_user(user_id: int) -> dict[str, Any]:
    now = isoformat(utc_now())
    with process_write_lock():
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if user.role == "admin":
                raise AuthError("不能拒绝管理员账号", 409)
            if user.status == "active":
                raise AuthError("已通过审核的账号不能直接拒绝", 409)
            user.status, user.rejected_at, user.updated_at = "rejected", now, now
            result = user_payload(user, include_review_fields=True)
    revoke_user_sessions(user_id)
    return result
