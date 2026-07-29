#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_DIR / "compose.test.yml"
APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8080").rstrip("/")
MAIL_URL = os.getenv("TEST_MAIL_URL", "http://localhost:8025").rstrip("/")
ADMIN_ACCOUNT = os.getenv("TEST_ADMIN_ACCOUNT", "admin@example.test")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "TestAdmin123")


def fail(message: str) -> None:
    raise RuntimeError(message)


def decode_json(response: Any) -> dict[str, Any]:
    return json.loads(response.read().decode("utf-8"))


def json_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with opener.open(request, timeout=10) as response:
            return decode_json(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        fail(f"{method} {url} 返回 {error.code}: {detail}")


def wait_for_mail(opener: urllib.request.OpenerDirector, recipient: str) -> dict[str, Any]:
    for _ in range(30):
        mailbox = json_request(opener, f"{MAIL_URL}/api/v1/messages")
        for message in mailbox.get("messages", []):
            addresses = {item.get("Address") for item in message.get("To", [])}
            if recipient in addresses:
                return message
        time.sleep(0.2)
    fail(f"测试收件箱中没有找到发给 {recipient} 的邮件")


def main() -> int:
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "app-backend",
            "python",
            "manage.py",
            "create-admin",
            "--username",
            "admin",
            "--display-name",
            "Docker 测试管理员",
            "--email",
            ADMIN_ACCOUNT,
            "--password",
            ADMIN_PASSWORD,
            "--if-not-exists",
        ],
        cwd=PROJECT_DIR,
        check=True,
    )

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    health = json_request(opener, f"{APP_URL}/api/health")
    if health.get("status") != "ok":
        fail("应用健康检查失败")

    login = json_request(
        opener,
        f"{APP_URL}/api/auth/login",
        method="POST",
        payload={"account": ADMIN_ACCOUNT, "password": ADMIN_PASSWORD},
    )
    if login.get("user", {}).get("username") != "admin":
        fail("管理员邮箱登录失败")

    suffix = uuid.uuid4().hex[:10]
    username = f"smoke-{suffix}"
    recipient = f"smoke-{suffix}@example.test"
    created_user = json_request(
        opener,
        f"{APP_URL}/api/admin/users",
        method="POST",
        payload={
            "username": username,
            "displayName": f"Docker Smoke {suffix}",
            "email": recipient,
            "password": "SmokeTest123",
            "role": "scheduler",
            "status": "active",
        },
    )
    if created_user.get("user", {}).get("email") != recipient:
        fail("创建测试参与者失败")

    meeting = json_request(
        opener,
        f"{APP_URL}/api/meetings",
        method="POST",
        payload={
            "title": f"Docker 联通测试 {suffix}",
            "hostName": "Docker 测试管理员",
            "attendees": [recipient],
            "passwordRequired": True,
        },
    )
    notification = meeting.get("emailNotification", {})
    if notification.get("status") != "sent" or notification.get("sent") != 1:
        fail(f"邀请邮件发送失败：{notification}")

    room_id = meeting["roomId"]
    public_meeting = json_request(opener, f"{APP_URL}/api/public/meetings/{room_id}")
    if public_meeting.get("passwordRequired") is not True:
        fail("参与页面没有要求会议密码")

    verified = json_request(
        opener,
        f"{APP_URL}/api/public/meetings/{room_id}/verify",
        method="POST",
        payload={"password": meeting["password"]},
    )
    jitsi_url = verified.get("jitsiUrl", "")
    if not jitsi_url.startswith("http://localhost:8000/"):
        fail(f"应用返回的 Jitsi 地址不正确：{jitsi_url}")

    with urllib.request.urlopen(jitsi_url, timeout=10) as response:
        if response.status != 200 or b"Jitsi Meet" not in response.read():
            fail("Jitsi 会议页面不可用")

    bosh_request = urllib.request.Request(
        "http://localhost:8000/http-bind",
        data=(
            b"<body rid='123456789' xmlns='http://jabber.org/protocol/httpbind' "
            b"to='meet.jitsi' xml:lang='en' wait='1' hold='1' ver='1.6'/>"
        ),
        method="POST",
        headers={"Content-Type": "text/xml; charset=utf-8"},
    )
    with urllib.request.urlopen(bosh_request, timeout=10) as response:
        if response.status != 200 or b"<body" not in response.read():
            fail("Jitsi BOSH 信令入口不可用")

    message = wait_for_mail(opener, recipient)
    expected_subject = f"[会议邀请] Docker 联通测试 {suffix}"
    if message.get("Subject") != expected_subject:
        fail(f"邮件主题不正确：{message.get('Subject')}")

    print("Docker 三服务端到端测试通过")
    print(f"  登录账号：{ADMIN_ACCOUNT}")
    print(f"  测试参与者：{recipient}")
    print(f"  应用参与链接：{meeting['accessUrl']}")
    print(f"  Jitsi 会议链接：{jitsi_url}")
    print(f"  邮件主题：{message['Subject']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"测试失败：{error}", file=sys.stderr)
        raise SystemExit(1)
