from __future__ import annotations

from contextlib import contextmanager
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

APP_MODULES = ["config", "database", "auth", "mailer", "services", "app"]
ADMIN_PASSWORD = "AdminPass123!"


def reset_app_modules() -> None:
    for module_name in APP_MODULES:
        sys.modules.pop(module_name, None)


def configure_test_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("JITSI_BASE_URL", "https://meet.wusupower.com/")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("MEETING_LINK_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("EMAIL_NOTIFICATIONS_ENABLED", "false")


def create_logged_in_admin(test_client) -> None:
    from auth import create_admin_user

    create_admin_user(
        username="admin",
        display_name="Admin",
        email="admin@example.com",
        password=ADMIN_PASSWORD,
    )
    response = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200


@pytest.fixture()
def anonymous_client(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    reset_app_modules()

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client

    os.environ.pop("DATABASE_PATH", None)


@pytest.fixture()
def client(anonymous_client):
    create_logged_in_admin(anonymous_client)
    yield anonymous_client


def test_management_api_requires_login(anonymous_client):
    response = anonymous_client.get("/api/meetings")

    assert response.status_code == 401
    assert response.get_json()["message"] == "请先登录"

    directory_response = anonymous_client.get("/api/users/directory?q=alice")
    assert directory_response.status_code == 401


def test_user_directory_searches_only_active_users(client):
    active_users = [
        {
            "username": "olivia",
            "displayName": "Olivia Chen",
            "email": "olivia@example.com",
            "password": "OliviaPass123",
            "role": "scheduler",
            "status": "active",
        },
        {
            "username": "oliver",
            "displayName": "Oliver Li",
            "email": "oliver@example.com",
            "password": "OliverPass123",
            "role": "scheduler",
            "status": "active",
        },
    ]
    for user in active_users:
        assert client.post("/api/admin/users", json=user).status_code == 201
    assert client.post(
        "/api/auth/register",
        json={
            "username": "pending-olivia",
            "displayName": "Pending Olivia",
            "email": "pending-olivia@example.com",
            "password": "PendingPass123",
            "registerMessage": "等待审核",
        },
    ).status_code == 201

    response = client.get("/api/users/directory?q=oliv")

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {item["username"] for item in items} == {"olivia", "oliver"}
    assert {item["email"] for item in items} == {"olivia@example.com", "oliver@example.com"}
    assert all(set(item) == {"id", "username", "displayName", "email"} for item in items)


def test_registration_requires_admin_approval(anonymous_client):
    register_response = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "nina",
            "displayName": "Nina",
            "email": "nina@example.com",
            "password": "NinaPass123",
            "registerMessage": "我是行政部 Nina，需要预约会议。",
        },
    )

    assert register_response.status_code == 201
    registered = register_response.get_json()["user"]
    assert registered["status"] == "pending"
    assert registered["registerMessage"] == "我是行政部 Nina，需要预约会议。"

    pending_login = anonymous_client.post(
        "/api/auth/login",
        json={"username": "nina", "password": "NinaPass123"},
    )
    assert pending_login.status_code == 403
    assert pending_login.get_json()["message"] == "账号待管理员审核"

    create_logged_in_admin(anonymous_client)
    pending_users = anonymous_client.get("/api/admin/users?status=pending").get_json()["items"]
    assert [user["username"] for user in pending_users] == ["nina"]

    approve_response = anonymous_client.post(f"/api/admin/users/{registered['id']}/approve")
    assert approve_response.status_code == 200
    assert approve_response.get_json()["user"]["status"] == "active"

    login_response = anonymous_client.post(
        "/api/auth/login",
        json={"username": "nina", "password": "NinaPass123"},
    )
    assert login_response.status_code == 200
    assert login_response.get_json()["user"]["role"] == "scheduler"


def test_registration_requires_unique_email(anonymous_client):
    missing_email = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "noemail",
            "displayName": "No Email",
            "password": "NoEmailPass123",
            "registerMessage": "我需要申请会议管理权限。",
        },
    )
    assert missing_email.status_code == 400
    assert missing_email.get_json()["message"] == "邮箱不能为空"

    first = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "eva",
            "displayName": "Eva",
            "email": "shared@example.com",
            "password": "EvaPass123",
            "registerMessage": "我是 Eva，需要预约会议。",
        },
    )
    assert first.status_code == 201

    duplicate = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "evan",
            "displayName": "Evan",
            "email": "shared@example.com",
            "password": "EvanPass123",
            "registerMessage": "我是 Evan，需要预约会议。",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["message"] == "邮箱已被使用"


def test_user_password_is_stored_as_hash(anonymous_client):
    password = "LisaPass123"
    response = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "lisa",
            "displayName": "Lisa",
            "email": "lisa@example.com",
            "password": password,
            "registerMessage": "我是财务部 Lisa，需要预约会议权限。",
        },
    )

    assert response.status_code == 201

    from database import get_connection

    with get_connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username = 'lisa'").fetchone()

    assert row["password_hash"] != password
    assert row["password_hash"].startswith("pbkdf2_sha256$")


def test_admin_can_manage_users_and_reset_password(client):
    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "mike",
            "displayName": "Mike",
            "email": "mike@example.com",
            "password": "MikePass123",
            "role": "scheduler",
            "status": "active",
        },
    )

    assert create_response.status_code == 201
    created = create_response.get_json()["user"]

    update_response = client.patch(
        f"/api/admin/users/{created['id']}",
        json={"displayName": "Mike Chen", "role": "scheduler", "status": "active"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["user"]["displayName"] == "Mike Chen"

    reset_response = client.post(f"/api/admin/users/{created['id']}/reset-password")
    assert reset_response.status_code == 200
    temporary_password = reset_response.get_json()["temporaryPassword"]
    assert temporary_password.startswith("Mcs-")

    client.post("/api/auth/logout")
    login_response = client.post(
        "/api/auth/login",
        json={"username": "mike", "password": temporary_password},
    )
    assert login_response.status_code == 200

    admin_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert admin_login.status_code == 200
    delete_response = client.delete(f"/api/admin/users/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.get_json() == {"deleted": True, "id": created["id"]}


def test_last_active_admin_cannot_be_removed(client):
    self_delete = client.delete("/api/admin/users/1")
    assert self_delete.status_code == 409

    demote_response = client.patch("/api/admin/users/1", json={"role": "scheduler"})
    assert demote_response.status_code == 409
    assert demote_response.get_json()["message"] == "至少需要保留一个可用管理员账号"

    disable_response = client.patch("/api/admin/users/1", json={"status": "disabled"})
    assert disable_response.status_code == 409
    assert disable_response.get_json()["message"] == "至少需要保留一个可用管理员账号"


def test_password_reset_request_requires_admin_approval(client):
    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "nora",
            "displayName": "Nora",
            "email": "nora@example.com",
            "password": "NoraPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    user = create_response.get_json()["user"]

    request_response = client.post(
        "/api/auth/password-reset-requests",
        json={"account": "nora", "message": "我忘记密码了，请帮忙重置。"},
    )
    assert request_response.status_code == 201
    reset_request = request_response.get_json()["request"]
    assert reset_request["status"] == "pending"
    assert reset_request["userId"] == user["id"]

    pending_items = client.get("/api/admin/password-reset-requests?status=pending").get_json()["items"]
    assert [item["username"] for item in pending_items] == ["nora"]

    approve_response = client.post(f"/api/admin/password-reset-requests/{reset_request['id']}/approve")
    assert approve_response.status_code == 200
    temporary_password = approve_response.get_json()["temporaryPassword"]

    client.post("/api/auth/logout")
    login_response = client.post(
        "/api/auth/login",
        json={"username": "nora", "password": temporary_password},
    )
    assert login_response.status_code == 200


def test_password_reset_request_accepts_email(client):
    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "olivia",
            "displayName": "Olivia",
            "email": "olivia@example.com",
            "password": "OliviaPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert create_response.status_code == 201

    request_response = client.post(
        "/api/auth/password-reset-requests",
        json={"account": "olivia@example.com", "message": "我想用邮箱找回密码。"},
    )
    assert request_response.status_code == 201
    payload = request_response.get_json()["request"]
    assert payload["username"] == "olivia"
    assert payload["email"] == "olivia@example.com"

    approve_response = client.post(f"/api/admin/password-reset-requests/{payload['id']}/approve")
    assert approve_response.status_code == 200
    temporary_password = approve_response.get_json()["temporaryPassword"]

    client.post("/api/auth/logout")
    login_response = client.post(
        "/api/auth/login",
        json={"account": "olivia@example.com", "password": temporary_password},
    )
    assert login_response.status_code == 200
    assert login_response.get_json()["user"]["username"] == "olivia"


def test_create_meeting_returns_password_once(client):
    response = client.post(
        "/api/meetings",
        json={
            "title": "周会",
            "hostName": "William",
            "maxOccupants": 12,
            "attendees": ["Alice", "Bob", "alice"],
        },
    )

    assert response.status_code == 201
    created = response.get_json()
    assert created["meetingUrl"] == created["accessUrl"]
    assert created["accessUrl"].startswith("http://localhost:5173/join/")
    assert created["jitsiUrl"] is None
    assert re.fullmatch(r"\d{4}", created["password"])
    assert created["attendees"] == ["Alice", "Bob"]
    assert "lobbyEnabled" not in created

    detail = client.get(f"/api/meetings/{created['id']}").get_json()
    assert "password" not in detail
    assert detail["attendees"] == ["Alice", "Bob"]
    assert "lobbyEnabled" not in detail


def test_create_meeting_notifies_email_attendees_and_registered_users(client, monkeypatch):
    import services

    client.post(
        "/api/admin/users",
        json={
            "username": "alice",
            "displayName": "Alice Chen",
            "email": "alice@example.com",
            "password": "AlicePass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    sent = []

    def record_notification(meeting, recipients, **kwargs):
        sent.append((meeting, recipients, kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    monkeypatch.setattr(services, "send_meeting_invitation_notifications", record_notification)

    response = client.post(
        "/api/meetings",
        json={
            "title": "邮件通知测试",
            "hostName": "Admin",
            "attendees": ["external@example.com", "Alice Chen", "无法识别的姓名"],
        },
    )

    assert response.status_code == 201
    created = response.get_json()
    assert created["mailOwner"] == "admin@example.com"
    assert created["emailNotification"] == {"status": "sent", "requested": 2, "sent": 2}
    assert len(sent) == 1
    assert sent[0][1] == ["external@example.com", "alice@example.com"]
    assert sent[0][2]["password"] == created["password"]


def test_update_meeting_only_notifies_new_attendees(client, monkeypatch):
    import services

    sent = []

    def record_notification(meeting, recipients, **kwargs):
        sent.append((meeting, recipients, kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    monkeypatch.setattr(services, "send_meeting_invitation_notifications", record_notification)
    created = client.post(
        "/api/meetings",
        json={
            "title": "新增参会者测试",
            "hostName": "Admin",
            "attendees": ["first@example.com"],
        },
    ).get_json()
    sent.clear()

    response = client.put(
        f"/api/meetings/{created['id']}",
        json={"attendees": ["first@example.com", "second@example.com"]},
    )

    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0][1] == ["second@example.com"]
    assert sent[0][2]["password"] == created["password"]


def test_mailer_sends_invitation_through_configured_smtp(monkeypatch):
    from types import SimpleNamespace

    import mailer

    smtp_events = []
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            smtp_events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            smtp_events.append(("close",))

        def ehlo(self):
            smtp_events.append(("ehlo",))

        def starttls(self, context):
            assert context is not None
            smtp_events.append(("starttls",))

        def login(self, username, password):
            smtp_events.append(("login", username, password))

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(
        mailer,
        "config",
        SimpleNamespace(
            email_notifications_enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="mailer@example.com",
            smtp_password="app-password",
            smtp_use_tls=True,
            smtp_use_ssl=False,
            smtp_verify_certificate=True,
            smtp_timeout_seconds=10,
            email_from="meetings@example.com",
            email_from_name="会议管理系统",
            email_timezone="Asia/Shanghai",
        ),
    )
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    result = mailer.send_meeting_invitation_notifications(
        {
            "title": "项目例会",
            "hostName": "Admin",
            "mailOwner": "admin@example.com",
            "startTime": "2048-01-01T09:00:00Z",
            "endTime": "2048-01-01T10:00:00Z",
            "accessUrl": "https://meeting.example.com/join/example",
        },
        ["alice@example.com"],
        password="1234",
    )

    assert result == {"status": "sent", "requested": 1, "sent": 1}
    assert ("connect", "smtp.example.com", 587, 10) in smtp_events
    assert ("starttls",) in smtp_events
    assert ("login", "mailer@example.com", "app-password") in smtp_events
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "alice@example.com"
    assert sent_messages[0]["Reply-To"] == "admin@example.com"
    assert "项目例会" in sent_messages[0]["Subject"]
    assert "会议密码：1234" in sent_messages[0].get_content()


def test_mailer_supports_ssl_with_explicitly_unverified_certificate(monkeypatch):
    from types import SimpleNamespace

    import mailer

    smtp_events = []

    class FakeSMTPSSL:
        def __init__(self, host, port, timeout, context):
            smtp_events.append(
                ("connect", host, port, timeout, context.check_hostname, context.verify_mode)
            )

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            smtp_events.append(("close",))

        def login(self, username, password):
            smtp_events.append(("login", username, password))

        def send_message(self, message):
            smtp_events.append(("send", message["To"]))

    monkeypatch.setattr(
        mailer,
        "config",
        SimpleNamespace(
            email_notifications_enabled=True,
            smtp_host="mail.example.com",
            smtp_port=465,
            smtp_username="mailer@example.com",
            smtp_password="app-password",
            smtp_use_tls=False,
            smtp_use_ssl=True,
            smtp_verify_certificate=False,
            smtp_timeout_seconds=10,
            email_from="meetings@example.com",
            email_from_name="会议管理系统",
            email_timezone="Asia/Shanghai",
        ),
    )
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTPSSL)

    result = mailer.send_meeting_invitation_notifications(
        {
            "title": "SSL 邮件测试",
            "hostName": "Admin",
            "startTime": "2048-01-01T09:00:00Z",
            "endTime": "2048-01-01T10:00:00Z",
        },
        ["alice@example.com"],
    )

    assert result == {"status": "sent", "requested": 1, "sent": 1}
    assert ("connect", "mail.example.com", 465, 10, False, 0) in smtp_events
    assert ("login", "mailer@example.com", "app-password") in smtp_events
    assert ("send", "alice@example.com") in smtp_events


def test_reservation_allocates_existing_room(client):
    created = client.post(
        "/api/meetings",
        json={"title": "项目同步", "hostName": "Alice"},
    ).get_json()

    response = client.post(
        "/conference",
        data={
            "name": created["roomId"],
            "start_time": "2048-04-20T17:55:12.000Z",
            "mail_owner": "alice@example.com",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["id"] == created["id"]
    assert payload["name"] == created["roomId"]
    assert re.fullmatch(r"\d{4}", payload["password"])
    assert payload["duration"] == 86400
    assert "lobby" not in payload


def test_deleted_meeting_is_removed_and_rejected_by_reservation(client):
    created = client.post("/api/meetings", json={"title": "删除测试", "hostName": "Bob"}).get_json()

    delete_response = client.delete(f"/api/meetings/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.get_json() == {"deleted": True, "id": created["id"]}

    detail_response = client.get(f"/api/meetings/{created['id']}")
    assert detail_response.status_code == 404

    response = client.post("/conference", data={"name": created["roomId"]})
    assert response.status_code == 403
    assert response.get_json()["message"] == "会议不存在"


def test_create_meeting_uses_process_write_lock(client, monkeypatch):
    import services

    lock_events = []

    @contextmanager
    def recording_lock():
        lock_events.append("enter")
        try:
            yield
        finally:
            lock_events.append("exit")

    monkeypatch.setattr(services, "process_write_lock", recording_lock)

    response = client.post("/api/meetings", json={"title": "进程锁测试", "hostName": "Alice"})

    assert response.status_code == 201
    assert lock_events == ["enter", "exit"]


def test_meeting_status_is_derived_from_time_window(client):
    created = client.post(
        "/api/meetings",
        json={
            "title": "进行中状态测试",
            "hostName": "Alice",
            "startTime": "2020-01-01T09:00:00Z",
            "endTime": "2099-01-01T10:00:00Z",
        },
    ).get_json()

    assert created["status"] == "Running"
    detail = client.get(f"/api/meetings/{created['id']}").get_json()
    assert detail["status"] == "Running"
    running_items = client.get("/api/meetings?status=Running").get_json()["items"]
    assert [item["id"] for item in running_items] == [created["id"]]


def test_create_recurring_meeting_inserts_series(client):
    response = client.post(
        "/api/meetings",
        json={
            "title": "周期晨会",
            "hostName": "Nina",
            "attendees": ["Alice", "Bob"],
            "startTime": "2048-01-01T09:00:00Z",
            "endTime": "2048-01-01T10:30:00Z",
            "recurrence": {
                "enabled": True,
                "type": "every_n_days",
                "interval": 3,
                "count": 4,
            },
        },
    )

    assert response.status_code == 201
    created = response.get_json()
    assert created["createdCount"] == 4
    assert created["isRecurring"] is True
    assert created["recurrence"]["type"] == "every_n_days"
    assert created["recurrence"]["interval"] == 3
    assert created["recurrence"]["count"] == 4
    assert created["recurrence"]["index"] == 0
    assert re.fullmatch(r"\d{4}", created["password"])

    series = created["seriesMeetings"]
    assert len(series) == 4
    assert len({meeting["id"] for meeting in series}) == 4
    assert {meeting["seriesId"] for meeting in series} == {created["seriesId"]}
    assert {meeting["roomId"] for meeting in series} == {created["roomId"]}
    assert {meeting["accessUrl"] for meeting in series} == {created["accessUrl"]}
    assert {meeting["meetingUrl"] for meeting in series} == {created["meetingUrl"]}
    assert [meeting["recurrence"]["index"] for meeting in series] == [0, 1, 2, 3]
    assert [meeting["startTime"] for meeting in series] == [
        "2048-01-01T09:00:00.000Z",
        "2048-01-04T09:00:00.000Z",
        "2048-01-07T09:00:00.000Z",
        "2048-01-10T09:00:00.000Z",
    ]
    assert all(meeting["attendees"] == ["Alice", "Bob"] for meeting in series)

    listed = client.get("/api/meetings").get_json()["items"]
    assert len(listed) == 4
    assert {meeting["seriesId"] for meeting in listed} == {created["seriesId"]}


def test_shared_recurring_url_resolves_to_next_available_occurrence(client):
    created = client.post(
        "/api/meetings",
        json={
            "title": "同链接系列",
            "hostName": "Nina",
            "passwordRequired": False,
            "startTime": "2048-03-01T09:00:00Z",
            "endTime": "2048-03-01T10:00:00Z",
            "recurrence": {
                "enabled": True,
                "type": "weekly",
                "count": 2,
            },
        },
    ).get_json()
    room_id = created["roomId"]
    first, second = created["seriesMeetings"]

    detail = client.get(f"/api/public/meetings/{room_id}").get_json()
    assert detail["roomId"] == room_id
    assert detail["startTime"] == first["startTime"]
    assert detail["jitsiUrl"] == created["meetingUrl"]

    delete_response = client.delete(f"/api/meetings/{first['id']}")
    assert delete_response.status_code == 200

    detail = client.get(f"/api/public/meetings/{room_id}").get_json()
    assert detail["roomId"] == room_id
    assert detail["startTime"] == second["startTime"]


def test_delete_recurring_series_removes_all_meetings(client):
    created = client.post(
        "/api/meetings",
        json={
            "title": "系列删除测试",
            "hostName": "Chris",
            "startTime": "2048-02-01T09:00:00Z",
            "endTime": "2048-02-01T10:00:00Z",
            "recurrence": {
                "enabled": True,
                "type": "weekly",
                "count": 3,
            },
        },
    ).get_json()
    series_ids = [meeting["id"] for meeting in created["seriesMeetings"]]

    delete_response = client.delete(f"/api/meetings/{series_ids[1]}?scope=series")

    assert delete_response.status_code == 200
    payload = delete_response.get_json()
    assert payload["deleted"] is True
    assert payload["deletedCount"] == 3
    assert payload["ids"] == series_ids
    assert payload["seriesId"] == created["seriesId"]
    assert client.get("/api/meetings").get_json()["items"] == []
    for meeting_id in series_ids:
        assert client.get(f"/api/meetings/{meeting_id}").status_code == 404


def test_init_db_migrates_existing_meetings_table(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                host_name TEXT NOT NULL,
                mail_owner TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                status TEXT NOT NULL,
                password_required INTEGER NOT NULL DEFAULT 1,
                password_hash TEXT NOT NULL,
                password_encrypted TEXT NOT NULL,
                max_occupants INTEGER NOT NULL DEFAULT 30,
                lobby_enabled INTEGER NOT NULL DEFAULT 0,
                meeting_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_started_at TEXT,
                finished_at TEXT,
                cancelled_at TEXT
            );
            """
    )

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    reset_app_modules()

    from database import init_db

    init_db()

    assert Path(f"{db_path}.lock").exists()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(meetings)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(meetings)").fetchall()}
        unique_room_id_indexes = []
        for row in conn.execute("PRAGMA index_list(meetings)").fetchall():
            if not row[2]:
                continue
            index_name = row[1]
            quoted_index_name = '"' + index_name.replace('"', '""') + '"'
            index_columns = [
                item[2]
                for item in conn.execute(f"PRAGMA index_info({quoted_index_name})").fetchall()
            ]
            if index_columns == ["room_id"]:
                unique_room_id_indexes.append(index_name)

    assert {"series_id", "recurrence_type", "recurrence_interval", "recurrence_count", "recurrence_index"} <= columns
    assert "idx_meetings_series_id" in indexes
    assert unique_room_id_indexes == []


def test_create_meeting_supports_legacy_required_recurrence_interval(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-recurrence.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                host_name TEXT NOT NULL,
                mail_owner TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('Scheduled', 'Running', 'Finished', 'Cancelled')
                ),
                password_required INTEGER NOT NULL DEFAULT 1,
                password_hash TEXT NOT NULL,
                password_encrypted TEXT NOT NULL,
                max_occupants INTEGER NOT NULL DEFAULT 30,
                lobby_enabled INTEGER NOT NULL DEFAULT 0,
                meeting_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_started_at TEXT,
                finished_at TEXT,
                cancelled_at TEXT,
                recurrence_type TEXT,
                recurrence_interval INTEGER NOT NULL DEFAULT 1,
                recurrence_end TEXT,
                series_id TEXT,
                recurrence_count INTEGER,
                recurrence_index INTEGER
            );
            """
        )

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("MEETING_LINK_ORIGIN", "http://localhost:5173")
    reset_app_modules()

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        create_logged_in_admin(test_client)
        response = test_client.post("/api/meetings", json={"title": "旧库兼容", "hostName": "Alice"})
        recurring_response = test_client.post(
            "/api/meetings",
            json={
                "title": "旧库同链接系列",
                "hostName": "Alice",
                "startTime": "2048-04-01T09:00:00Z",
                "endTime": "2048-04-01T10:00:00Z",
                "recurrence": {"enabled": True, "type": "weekly", "count": 2},
            },
        )

    assert response.status_code == 201
    assert response.get_json()["title"] == "旧库兼容"
    assert recurring_response.status_code == 201
    recurring_payload = recurring_response.get_json()
    assert {meeting["roomId"] for meeting in recurring_payload["seriesMeetings"]} == {
        recurring_payload["roomId"]
    }


def test_access_url_uses_bookmeeting_origin_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    monkeypatch.delenv("MEETING_LINK_ORIGIN", raising=False)

    reset_app_modules()

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        create_logged_in_admin(test_client)
        response = test_client.post("/api/meetings", json={"title": "默认域名", "hostName": "Alice"})

    assert response.status_code == 201
    created = response.get_json()
    assert created["accessUrl"].startswith("http://bookmeeting.wusupower.com/join/")
    assert created["meetingUrl"] == created["accessUrl"]


def test_cors_allows_multiple_frontend_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://127.0.0.1:5173,http://localhost:5173")

    reset_app_modules()

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        for origin in ["http://127.0.0.1:5173", "http://localhost:5173"]:
            response = test_client.get("/api/health", headers={"Origin": origin})
            assert response.headers["Access-Control-Allow-Origin"] == origin
