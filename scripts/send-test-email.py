#!/usr/bin/env python3
"""通过 backend/.env 中的 SMTP 配置发送一封测试邮件。"""

from __future__ import annotations

import argparse
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from config import config  # noqa: E402
from mailer import create_smtp_ssl_context, normalize_email_address  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用 backend/.env 中的 SMTP 配置发送测试邮件。"
    )
    parser.add_argument("recipient", help="接收测试邮件的邮箱地址")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="仅用于测试：跳过 SMTP 服务器证书验证",
    )
    return parser.parse_args()


def build_message(recipient: str) -> EmailMessage:
    sender = normalize_email_address(config.email_from or config.smtp_username)
    if sender is None:
        raise ValueError("EMAIL_FROM 或 SMTP_USERNAME 必须是有效邮箱地址")

    timezone_name = config.email_timezone or "UTC"
    sent_at = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S")

    message = EmailMessage()
    message["Subject"] = "会议管理系统 SMTP 测试邮件"
    message["From"] = formataddr((config.email_from_name or "会议管理系统", sender))
    message["To"] = recipient
    message.set_content(
        "您好，\n\n"
        "这是一封会议管理系统发送的 SMTP 测试邮件。\n"
        f"发送时间：{sent_at} ({timezone_name})\n\n"
        "如果您收到此邮件，说明邮件服务器配置正常。\n"
    )
    return message


def send_test_email(recipient: str, *, insecure: bool = False) -> None:
    recipient = normalize_email_address(recipient) or ""
    if not recipient:
        raise ValueError("收件邮箱地址无效")
    if not config.smtp_host:
        raise ValueError("SMTP_HOST 尚未配置")
    if config.smtp_use_ssl and config.smtp_use_tls:
        raise ValueError("SMTP_USE_SSL 和 SMTP_USE_TLS 不能同时为 true")

    timeout = max(1, config.smtp_timeout_seconds)
    tls_context = create_smtp_ssl_context(not insecure)
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
        smtp.send_message(build_message(recipient))


def main() -> int:
    args = parse_args()
    try:
        if args.insecure:
            print("警告：本次测试已跳过 SMTP 服务器证书验证", file=sys.stderr)
        send_test_email(args.recipient, insecure=args.insecure)
    except Exception as exc:
        print(f"发送失败：{exc}", file=sys.stderr)
        return 1

    print(f"测试邮件已提交给 SMTP 服务器：{args.recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
