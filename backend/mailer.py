from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import config


logger = logging.getLogger(__name__)


def create_smtp_ssl_context(verify_certificate: bool) -> ssl.SSLContext:
    if verify_certificate:
        return ssl.create_default_context()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def normalize_email_address(value: Any) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value or "\n" in raw_value or "\r" in raw_value:
        return None

    _display_name, address = parseaddr(raw_value)
    address = address.strip().lower()
    if not address or "@" not in address or len(address) > 254:
        return None

    local_part, domain = address.rsplit("@", 1)
    if not local_part or not domain or "." not in domain:
        return None
    return address


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").splitlines()).strip()


def _email_timezone() -> tuple[timezone | ZoneInfo, str]:
    timezone_name = config.email_timezone or "UTC"
    try:
        return ZoneInfo(timezone_name), timezone_name
    except ZoneInfoNotFoundError:
        logger.warning("Unknown EMAIL_TIMEZONE %s; falling back to UTC", timezone_name)
        return timezone.utc, "UTC"


def _parse_meeting_time(value: Any) -> datetime | None:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    target_timezone, _timezone_name = _email_timezone()
    return parsed.astimezone(target_timezone)


def _format_meeting_time(value: Any) -> str:
    parsed = _parse_meeting_time(value)
    if parsed is None:
        return "未指定"
    result = f"{parsed.year}年{parsed.month:02d}月{parsed.day:02d}日{parsed.hour}点"
    if parsed.minute:
        result += f"{parsed.minute:02d}分"
    return result


def _format_meeting_duration(start_value: Any, end_value: Any) -> str:
    start_time = _parse_meeting_time(start_value)
    end_time = _parse_meeting_time(end_value)
    if start_time is None or end_time is None or end_time <= start_time:
        return "未指定"

    total_minutes = max(1, round((end_time - start_time).total_seconds() / 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minutes}分钟"


def _build_invitation_message(
    meeting: dict[str, Any],
    recipient: str,
    password: str | None,
    recurrence_count: int,
) -> EmailMessage:
    title = _one_line(meeting.get("title")) or "未命名会议"
    host_name = _one_line(meeting.get("hostName") or meeting.get("host_name")) or "未指定"
    access_url = str(
        meeting.get("accessUrl")
        or meeting.get("meetingUrl")
        or meeting.get("meeting_url")
        or ""
    ).strip()
    start_value = meeting.get("startTime") or meeting.get("start_time")
    end_value = meeting.get("endTime") or meeting.get("end_time")
    start_time = _format_meeting_time(start_value)
    duration = _format_meeting_duration(start_value, end_value)

    body_lines = [
        "您好，",
        "",
        "您已被添加为以下会议的参与人员：",
        "",
        f"会议：{title}",
        f"会议时间：{start_time}",
        f"会议时长：{duration}",
        f"主持人：{host_name}",
    ]
    if recurrence_count > 1:
        body_lines.append(f"周期会议：共 {recurrence_count} 场，使用同一个参与链接")
    if access_url:
        body_lines.append(f"会议链接：{access_url}")
    if password:
        body_lines.append(f"会议密码：{password}")
    body_lines.extend(["", "此邮件由会议管理系统自动发送，请勿直接回复。"])

    sender_address = normalize_email_address(config.email_from or config.smtp_username)
    if sender_address is None:
        raise ValueError("EMAIL_FROM 或 SMTP_USERNAME 必须是有效邮箱")

    message = EmailMessage()
    message["Subject"] = f"[会议邀请] {title}"
    message["From"] = formataddr((config.email_from_name or "会议管理系统", sender_address))
    message["To"] = recipient
    reply_to = normalize_email_address(meeting.get("mailOwner") or meeting.get("mail_owner"))
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content("\n".join(body_lines))
    return message


def _build_meeting_notification_message(
    meeting: dict[str, Any],
    recipient: str,
    *,
    notification_type: str,
    changes: list[str] | None = None,
    scope: str = "single",
    password: str | None = None,
) -> EmailMessage:
    title = _one_line(meeting.get("title")) or "未命名会议"
    host_name = _one_line(meeting.get("hostName") or meeting.get("host_name")) or "未指定"
    access_url = str(
        meeting.get("accessUrl")
        or meeting.get("meetingUrl")
        or meeting.get("meeting_url")
        or ""
    ).strip()
    start_value = meeting.get("startTime") or meeting.get("start_time")
    end_value = meeting.get("endTime") or meeting.get("end_time")
    scope_label = {
        "single": "仅本次会议",
        "following": "本次及后续会议",
        "series": "整个会议系列",
    }.get(scope, "仅本次会议")
    notification_content = {
        "update": ("[会议更新]", "以下会议安排已更新："),
        "cancellation": ("[会议取消]", "以下会议已取消："),
        "removal": ("[参会移除]", "您已被移出以下会议："),
    }
    subject_prefix, intro = notification_content[notification_type]

    body_lines = [
        "您好，",
        "",
        intro,
        "",
        f"影响范围：{scope_label}",
    ]
    if changes:
        body_lines.append(f"变更内容：{'、'.join(dict.fromkeys(changes))}")
    body_lines.extend(
        [
            f"会议：{title}",
            f"会议时间：{_format_meeting_time(start_value)}",
            f"会议时长：{_format_meeting_duration(start_value, end_value)}",
            f"主持人：{host_name}",
        ]
    )
    if notification_type != "cancellation" and access_url:
        body_lines.append(f"会议链接：{access_url}")
    if notification_type == "update" and password:
        body_lines.append(f"会议密码：{password}")
    body_lines.extend(["", "此邮件由会议管理系统自动发送，请勿直接回复。"])

    sender_address = normalize_email_address(config.email_from or config.smtp_username)
    if sender_address is None:
        raise ValueError("EMAIL_FROM 或 SMTP_USERNAME 必须是有效邮箱")

    message = EmailMessage()
    message["Subject"] = f"{subject_prefix} {title}"
    message["From"] = formataddr((config.email_from_name or "会议管理系统", sender_address))
    message["To"] = recipient
    reply_to = normalize_email_address(meeting.get("mailOwner") or meeting.get("mail_owner"))
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content("\n".join(body_lines))
    return message


def _build_password_reset_message(
    recipient: str,
    display_name: str,
    reset_url: str,
    expires_minutes: int,
) -> EmailMessage:
    sender_address = normalize_email_address(config.email_from or config.smtp_username)
    if sender_address is None:
        raise ValueError("EMAIL_FROM 或 SMTP_USERNAME 必须是有效邮箱")

    safe_display_name = _one_line(display_name) or "用户"
    message = EmailMessage()
    message["Subject"] = "[会议管理系统] 设置新密码"
    message["From"] = formataddr((config.email_from_name or "会议管理系统", sender_address))
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                f"{safe_display_name}，您好：",
                "",
                "我们收到了您的密码找回请求。请打开下面的链接设置新密码：",
                reset_url,
                "",
                f"此链接将在 {expires_minutes} 分钟后失效，并且只能使用一次。",
                "如果不是您本人发起的请求，请忽略此邮件，原密码不会改变。",
                "",
                "此邮件由会议管理系统自动发送，请勿直接回复。",
            ]
        )
    )
    return message


def _send_email_messages(messages: list[EmailMessage], failure_message: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_requested",
        "requested": len(messages),
        "sent": 0,
    }
    if not messages:
        return result

    if not config.email_notifications_enabled:
        result["status"] = "disabled"
        return result

    if not config.smtp_host or normalize_email_address(config.email_from or config.smtp_username) is None:
        result.update(status="failed", message="邮件服务未配置完整")
        logger.error("Email notifications are enabled but SMTP_HOST or sender address is missing")
        return result
    if config.smtp_use_ssl and config.smtp_use_tls:
        result.update(status="failed", message="SMTP SSL/TLS 配置冲突")
        logger.error("SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled")
        return result

    tls_context = create_smtp_ssl_context(config.smtp_verify_certificate)
    if not config.smtp_verify_certificate:
        logger.warning("SMTP certificate verification is disabled")

    sent_count = 0
    try:
        timeout = max(1, config.smtp_timeout_seconds)
        if config.smtp_use_ssl:
            smtp_connection = smtplib.SMTP_SSL(
                config.smtp_host,
                config.smtp_port,
                timeout=timeout,
                context=tls_context,
            )
        else:
            smtp_connection = smtplib.SMTP(
                config.smtp_host,
                config.smtp_port,
                timeout=timeout,
            )

        with smtp_connection as smtp:
            if not config.smtp_use_ssl:
                smtp.ehlo()
                if config.smtp_use_tls:
                    smtp.starttls(context=tls_context)
                    smtp.ehlo()
            if config.smtp_username:
                smtp.login(config.smtp_username, config.smtp_password)

            for message in messages:
                try:
                    smtp.send_message(message)
                    sent_count += 1
                except Exception:
                    logger.exception("Failed to send email to %s", message.get("To", "unknown"))
    except Exception:
        logger.exception("Failed to connect to SMTP server")

    result["sent"] = sent_count
    if sent_count == len(messages):
        result["status"] = "sent"
    elif sent_count:
        result.update(status="partial", message="部分邮件发送失败")
    else:
        result.update(status="failed", message=failure_message)
    return result


def send_meeting_invitation_notifications(
    meeting: dict[str, Any],
    recipients: list[str],
    *,
    password: str | None = None,
    recurrence_count: int = 1,
) -> dict[str, Any]:
    normalized_recipients = list(
        dict.fromkeys(
            address
            for recipient in recipients
            if (address := normalize_email_address(recipient)) is not None
        )
    )
    if not normalized_recipients:
        return {"status": "not_requested", "requested": 0, "sent": 0}
    if not config.email_notifications_enabled:
        return {"status": "disabled", "requested": len(normalized_recipients), "sent": 0}
    if normalize_email_address(config.email_from or config.smtp_username) is None:
        logger.error("Email notifications are enabled but sender address is missing")
        return {
            "status": "failed",
            "requested": len(normalized_recipients),
            "sent": 0,
            "message": "邮件服务未配置完整",
        }

    messages = [
        _build_invitation_message(meeting, recipient, password, recurrence_count)
        for recipient in normalized_recipients
    ]
    return _send_email_messages(messages, "通知邮件发送失败")


def _send_meeting_event_notifications(
    meeting: dict[str, Any],
    recipients: list[str],
    *,
    notification_type: str,
    changes: list[str] | None = None,
    scope: str = "single",
    password: str | None = None,
) -> dict[str, Any]:
    normalized_recipients = list(
        dict.fromkeys(
            address
            for recipient in recipients
            if (address := normalize_email_address(recipient)) is not None
        )
    )
    if not normalized_recipients:
        return {"status": "not_requested", "requested": 0, "sent": 0}
    if not config.email_notifications_enabled:
        return {"status": "disabled", "requested": len(normalized_recipients), "sent": 0}
    if normalize_email_address(config.email_from or config.smtp_username) is None:
        logger.error("Email notifications are enabled but sender address is missing")
        return {
            "status": "failed",
            "requested": len(normalized_recipients),
            "sent": 0,
            "message": "邮件服务未配置完整",
        }
    messages = [
        _build_meeting_notification_message(
            meeting,
            recipient,
            notification_type=notification_type,
            changes=changes,
            scope=scope,
            password=password,
        )
        for recipient in normalized_recipients
    ]
    failure_messages = {
        "update": "会议更新邮件发送失败",
        "cancellation": "会议取消邮件发送失败",
        "removal": "参会移除邮件发送失败",
    }
    return _send_email_messages(messages, failure_messages[notification_type])


def send_meeting_update_notifications(
    meeting: dict[str, Any],
    recipients: list[str],
    changes: list[str],
    *,
    scope: str = "single",
    password: str | None = None,
) -> dict[str, Any]:
    if not changes:
        return {"status": "not_requested", "requested": 0, "sent": 0}
    return _send_meeting_event_notifications(
        meeting,
        recipients,
        notification_type="update",
        changes=changes,
        scope=scope,
        password=password,
    )


def send_meeting_cancellation_notifications(
    meeting: dict[str, Any],
    recipients: list[str],
    *,
    scope: str = "single",
) -> dict[str, Any]:
    return _send_meeting_event_notifications(
        meeting,
        recipients,
        notification_type="cancellation",
        scope=scope,
    )


def send_meeting_removal_notifications(
    meeting: dict[str, Any],
    recipients: list[str],
    *,
    scope: str = "single",
) -> dict[str, Any]:
    return _send_meeting_event_notifications(
        meeting,
        recipients,
        notification_type="removal",
        scope=scope,
    )


def send_password_reset_email(
    recipient: str,
    display_name: str,
    reset_url: str,
    expires_minutes: int,
) -> dict[str, Any]:
    normalized_recipient = normalize_email_address(recipient)
    if normalized_recipient is None:
        return {"status": "not_requested", "requested": 0, "sent": 0}
    if not config.email_notifications_enabled:
        return {"status": "disabled", "requested": 1, "sent": 0}
    if normalize_email_address(config.email_from or config.smtp_username) is None:
        logger.error("Email notifications are enabled but sender address is missing")
        return {
            "status": "failed",
            "requested": 1,
            "sent": 0,
            "message": "邮件服务未配置完整",
        }

    message = _build_password_reset_message(
        normalized_recipient,
        display_name,
        reset_url,
        expires_minutes,
    )
    return _send_email_messages([message], "密码找回邮件发送失败")
