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


def _format_meeting_time(value: Any) -> str:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return normalized or "未指定"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    target_timezone, timezone_name = _email_timezone()
    return f"{parsed.astimezone(target_timezone).strftime('%Y-%m-%d %H:%M')} ({timezone_name})"


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
    start_time = _format_meeting_time(meeting.get("startTime") or meeting.get("start_time"))
    end_time = _format_meeting_time(meeting.get("endTime") or meeting.get("end_time"))

    body_lines = [
        "您好，",
        "",
        "您已被添加为以下会议的参与人员：",
        "",
        f"会议：{title}",
        f"主持人：{host_name}",
        f"开始时间：{start_time}",
        f"结束时间：{end_time}",
    ]
    if recurrence_count > 1:
        body_lines.append(f"周期会议：共 {recurrence_count} 场，使用同一个参与链接")
    if access_url:
        body_lines.append(f"参与链接：{access_url}")
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
    result: dict[str, Any] = {
        "status": "not_requested",
        "requested": len(normalized_recipients),
        "sent": 0,
    }
    if not normalized_recipients:
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

            for recipient in normalized_recipients:
                try:
                    smtp.send_message(
                        _build_invitation_message(
                            meeting,
                            recipient,
                            password,
                            recurrence_count,
                        )
                    )
                    sent_count += 1
                except Exception:
                    logger.exception("Failed to send meeting invitation to %s", recipient)
    except Exception:
        logger.exception("Failed to connect to SMTP server for meeting notifications")

    result["sent"] = sent_count
    if sent_count == len(normalized_recipients):
        result["status"] = "sent"
    elif sent_count:
        result.update(status="partial", message="部分通知邮件发送失败")
    else:
        result.update(status="failed", message="通知邮件发送失败")
    return result
