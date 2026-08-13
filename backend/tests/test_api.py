from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

APP_MODULES = [
    "config",
    "database",
    "auth",
    "jitsi_auth",
    "mailer",
    "groups",
    "services",
    "calendar_feed",
    "milestones",
    "app",
]
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
    # Keep the real backend/.env from leaking JWT settings into isolated tests.
    monkeypatch.setenv("JITSI_JWT_APP_ID", "")
    monkeypatch.setenv("JITSI_JWT_APP_SECRET", "")
    monkeypatch.setenv("JITSI_JWT_SUBJECT", "meet.jitsi")
    monkeypatch.setenv("JITSI_JWT_TTL_SECONDS", "7200")


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
            "jobTitle": "高级工程师",
            "phoneNumber": "+61 400 000 101",
            "password": "OliviaPass123",
            "role": "scheduler",
            "status": "active",
        },
        {
            "username": "oliver",
            "displayName": "Oliver Li",
            "email": "oliver@example.com",
            "jobTitle": "产品经理",
            "phoneNumber": "+61 400 000 102",
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
    assert {item["jobTitle"] for item in items} == {"高级工程师", "产品经理"}
    assert all(
        set(item) == {"id", "username", "displayName", "email", "jobTitle", "phoneNumber"}
        for item in items
    )

    title_response = client.get("/api/users/directory?q=产品经理")
    assert title_response.status_code == 200
    assert [item["username"] for item in title_response.get_json()["items"]] == ["oliver"]


def test_regular_user_can_view_active_directory_sorted_by_name_pinyin(client):
    users = [
        ("zhangsan", "张三", "+61 400 000 103"),
        ("lisi", "李四", "+61 400 000 101"),
        ("wangwu", "王五", "+61 400 000 102"),
    ]
    for username, display_name, phone_number in users:
        response = client.post(
            "/api/admin/users",
            json={
                "username": username,
                "displayName": display_name,
                "email": f"{username}@example.com",
                "jobTitle": "工程部",
                "phoneNumber": phone_number,
                "password": "DirectoryPass123",
                "role": "scheduler",
                "status": "active",
            },
        )
        assert response.status_code == 201

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"username": "lisi", "password": "DirectoryPass123"},
    ).status_code == 200

    response = client.get("/api/users/directory?q=工程部")

    assert response.status_code == 200
    directory = response.get_json()["items"]
    assert [item["displayName"] for item in directory] == ["李四", "王五", "张三"]
    assert [item["phoneNumber"] for item in directory] == [
        "+61 400 000 101",
        "+61 400 000 102",
        "+61 400 000 103",
    ]

    phone_search = client.get("/api/users/directory?q=000%20102").get_json()["items"]
    assert [item["displayName"] for item in phone_search] == ["王五"]


def test_meeting_link_uses_random_room_id_without_title_or_time(client):
    response = client.post(
        "/api/meetings",
        json={
            "title": "项目同步",
            "hostName": "Alice",
            "startTime": "2048-04-20T17:55:00Z",
            "endTime": "2048-04-20T18:55:00Z",
        },
    )

    assert response.status_code == 201
    created = response.get_json()
    assert re.fullmatch(r"mcs-[0-9a-f]{12}", created["roomId"])
    assert "xiang-mu-tong-bu" not in created["roomId"]
    assert "2048" not in created["roomId"]
    assert created["meetingUrl"] == f"https://meet.wusupower.com/{created['roomId']}"


def test_duplicate_random_room_ids_are_regenerated(client, monkeypatch):
    import services

    room_ids = iter(["mcs-fixed000001", "mcs-fixed000001", "mcs-next0000002"])
    monkeypatch.setattr(services, "generate_room_id", lambda: next(room_ids))
    payload = {
        "title": "项目同步",
        "hostName": "Alice",
        "startTime": "2048-04-20T17:55:00Z",
        "endTime": "2048-04-20T18:55:00Z",
    }

    first = client.post("/api/meetings", json=payload).get_json()
    second = client.post("/api/meetings", json=payload).get_json()

    assert first["roomId"] == "mcs-fixed000001"
    assert second["roomId"] == "mcs-next0000002"


def test_user_group_visibility_and_member_permissions(client):
    users = {}
    for username in ("alice", "bob", "charlie"):
        response = client.post(
            "/api/admin/users",
            json={
                "username": username,
                "displayName": username.title(),
                "email": f"{username}@example.com",
                "password": f"{username.title()}Pass123",
                "role": "scheduler",
                "status": "active",
            },
        )
        assert response.status_code == 201
        users[username] = response.get_json()["user"]

    admin_group = client.post(
        "/api/groups",
        json={"name": "管理员组", "description": "只包含系统管理员"},
    ).get_json()["group"]
    assert admin_group["includesCurrentUser"] is True
    assert admin_group["currentUserGroupRole"] == "admin"

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "alice", "password": "AlicePass123"},
    ).status_code == 200
    assert client.get("/api/groups").get_json()["items"] == []

    created_response = client.post(
        "/api/groups",
        json={"name": "产品组", "description": "产品与研发成员"},
    )
    assert created_response.status_code == 201
    product_group = created_response.get_json()["group"]
    assert product_group["includesCurrentUser"] is True
    assert product_group["currentUserGroupRole"] == "admin"
    assert product_group["canAddMembers"] is True
    assert product_group["canRemoveMembers"] is True
    assert [member["username"] for member in product_group["members"]] == ["alice"]

    add_bob = client.post(
        f"/api/groups/{product_group['id']}/members",
        json={"userId": users["bob"]["id"], "groupRole": "member"},
    )
    assert add_bob.status_code == 201

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "bob", "password": "BobPass123"},
    ).status_code == 200
    visible_groups = client.get("/api/groups").get_json()["items"]
    assert [group["name"] for group in visible_groups] == ["产品组"]
    assert visible_groups[0]["currentUserGroupRole"] == "member"
    assert visible_groups[0]["canAddMembers"] is True
    assert visible_groups[0]["canRemoveMembers"] is False

    add_charlie = client.post(
        f"/api/groups/{product_group['id']}/members",
        json={"userId": users["charlie"]["id"], "groupRole": "member"},
    )
    assert add_charlie.status_code == 201
    assert {member["username"] for member in add_charlie.get_json()["group"]["members"]} == {
        "alice",
        "bob",
        "charlie",
    }
    assert client.delete(
        f"/api/groups/{product_group['id']}/members/{users['charlie']['id']}"
    ).status_code == 403
    assert client.patch(
        f"/api/groups/{product_group['id']}",
        json={"name": "不允许普通成员修改"},
    ).status_code == 403

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "alice", "password": "AlicePass123"},
    ).status_code == 200
    remove_charlie = client.delete(
        f"/api/groups/{product_group['id']}/members/{users['charlie']['id']}"
    )
    assert remove_charlie.status_code == 200
    assert {member["username"] for member in remove_charlie.get_json()["group"]["members"]} == {
        "alice",
        "bob",
    }

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "admin", "password": ADMIN_PASSWORD},
    ).status_code == 200
    all_groups = client.get("/api/groups").get_json()["items"]
    inclusion = {group["name"]: group["includesCurrentUser"] for group in all_groups}
    assert inclusion == {"产品组": False, "管理员组": True}

    update_response = client.patch(
        f"/api/groups/{product_group['id']}",
        json={"name": "产品研发组", "description": "系统管理员已更新"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["group"]["name"] == "产品研发组"
    delete_response = client.delete(f"/api/groups/{product_group['id']}")
    assert delete_response.status_code == 200
    assert delete_response.get_json() == {"deleted": True, "id": product_group["id"]}


def test_group_can_add_multiple_members_in_one_request(client):
    users = []
    for username in ("batch-alice", "batch-bob", "batch-charlie"):
        response = client.post(
            "/api/admin/users",
            json={
                "username": username,
                "displayName": username.title(),
                "email": f"{username}@example.com",
                "password": "BatchMemberPass123",
                "role": "scheduler",
                "status": "active",
            },
        )
        assert response.status_code == 201
        users.append(response.get_json()["user"])

    group = client.post("/api/groups", json={"name": "批量添加测试组"}).get_json()["group"]
    response = client.post(
        f"/api/groups/{group['id']}/members",
        json={"userIds": [users[0]["id"], users[1]["id"]], "groupRole": "member"},
    )

    assert response.status_code == 201
    assert {member["username"] for member in response.get_json()["group"]["members"]} == {
        "admin",
        "batch-alice",
        "batch-bob",
    }

    add_with_existing = client.post(
        f"/api/groups/{group['id']}/members",
        json={"userIds": [users[1]["id"], users[2]["id"]], "groupRole": "member"},
    )
    assert add_with_existing.status_code == 201
    assert {member["username"] for member in add_with_existing.get_json()["group"]["members"]} == {
        "admin",
        "batch-alice",
        "batch-bob",
        "batch-charlie",
    }


def test_meeting_can_expand_a_visible_user_group(client):
    alice_response = client.post(
        "/api/admin/users",
        json={
            "username": "group-alice",
            "displayName": "Group Alice",
            "email": "group-alice@example.com",
            "password": "GroupAlicePass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert alice_response.status_code == 201
    alice = alice_response.get_json()["user"]
    outsider_response = client.post(
        "/api/admin/users",
        json={
            "username": "group-outsider",
            "displayName": "Group Outsider",
            "email": "group-outsider@example.com",
            "password": "GroupOutsiderPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert outsider_response.status_code == 201
    group = client.post(
        "/api/groups",
        json={"name": "会议邀请组"},
    ).get_json()["group"]
    assert client.post(
        f"/api/groups/{group['id']}/members",
        json={"userId": alice["id"], "groupRole": "member"},
    ).status_code == 201

    meeting_response = client.post(
        "/api/meetings",
        json={
            "title": "整组会议",
            "hostName": "Admin",
            "attendees": [group["selectionValue"], "external@example.com"],
        },
    )

    assert meeting_response.status_code == 201
    meeting = meeting_response.get_json()
    assert set(meeting["attendees"]) == {
        "admin@example.com",
        "group-alice@example.com",
        "external@example.com",
    }
    assert group["selectionValue"] not in meeting["attendees"]

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "group-outsider", "password": "GroupOutsiderPass123"},
    ).status_code == 200
    forbidden = client.post(
        "/api/meetings",
        json={
            "title": "越权整组邀请",
            "hostName": "Outsider",
            "attendees": [group["selectionValue"]],
        },
    )
    assert forbidden.status_code == 403


def test_overlapping_groups_only_invite_each_member_once(client, monkeypatch):
    import services

    shared_user_response = client.post(
        "/api/admin/users",
        json={
            "username": "shared-member",
            "displayName": "Shared Member",
            "email": "shared-member@example.com",
            "password": "SharedMemberPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert shared_user_response.status_code == 201
    shared_user = shared_user_response.get_json()["user"]

    groups = []
    for name in ("重叠组一", "重叠组二"):
        group = client.post("/api/groups", json={"name": name}).get_json()["group"]
        add_response = client.post(
            f"/api/groups/{group['id']}/members",
            json={"userId": shared_user["id"], "groupRole": "member"},
        )
        assert add_response.status_code == 201
        groups.append(group)

    deliveries = []

    def record_notification(meeting, recipients, **kwargs):
        deliveries.append(list(recipients))
        return {
            "status": "sent",
            "requested": len(recipients),
            "sent": len(recipients),
        }

    monkeypatch.setattr(services, "send_meeting_invitation_notifications", record_notification)
    response = client.post(
        "/api/meetings",
        json={
            "title": "两个重叠用户组会议",
            "hostName": "Admin",
            "attendees": [group["selectionValue"] for group in groups],
        },
    )

    assert response.status_code == 201
    meeting = response.get_json()
    assert meeting["attendees"] == ["admin@example.com", "shared-member@example.com"]
    assert len(meeting["attendees"]) == len(set(meeting["attendees"]))
    assert deliveries == [["admin@example.com", "shared-member@example.com"]]
    assert meeting["emailNotification"] == {"status": "sent", "requested": 2, "sent": 2}


def test_registration_requires_admin_approval(anonymous_client):
    register_response = anonymous_client.post(
        "/api/auth/register",
        json={
            "username": "nina",
            "displayName": "Nina",
            "email": "nina@example.com",
            "phoneNumber": "+61 400 555 010",
            "password": "NinaPass123",
            "registerMessage": "我是行政部 Nina，需要预约会议。",
        },
    )

    assert register_response.status_code == 201
    registered = register_response.get_json()["user"]
    assert registered["status"] == "pending"
    assert registered["phoneNumber"] == "+61 400 555 010"
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
    assert response.get_json()["user"]["phoneNumber"] == ""

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
            "jobTitle": "项目专员",
            "phoneNumber": "+61 400 123 456",
            "password": "MikePass123",
            "role": "scheduler",
            "status": "active",
        },
    )

    assert create_response.status_code == 201
    created = create_response.get_json()["user"]
    assert created["jobTitle"] == "项目专员"
    assert created["phoneNumber"] == "+61 400 123 456"

    update_response = client.patch(
        f"/api/admin/users/{created['id']}",
        json={
            "displayName": "Mike Chen",
            "jobTitle": "高级项目经理",
            "phoneNumber": "+61 400 654 321",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["user"]["displayName"] == "Mike Chen"
    assert update_response.get_json()["user"]["jobTitle"] == "高级项目经理"
    assert update_response.get_json()["user"]["phoneNumber"] == "+61 400 654 321"

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


def test_email_password_reset_sends_single_use_link_and_revokes_sessions(client, monkeypatch):
    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "rachel",
            "displayName": "Rachel",
            "email": "rachel@example.com",
            "password": "RachelPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert create_response.status_code == 201

    import auth

    deliveries = []

    def fake_send_password_reset_email(recipient, display_name, reset_url, expires_minutes):
        deliveries.append((recipient, display_name, reset_url, expires_minutes))
        return {"status": "sent", "requested": 1, "sent": 1}

    monkeypatch.setattr(auth, "send_password_reset_email", fake_send_password_reset_email)

    client.post("/api/auth/logout")
    login_response = client.post(
        "/api/auth/login",
        json={"account": "rachel@example.com", "password": "RachelPass123"},
    )
    assert login_response.status_code == 200

    request_response = client.post(
        "/api/auth/email-password-reset",
        json={"email": "rachel@example.com"},
    )
    assert request_response.status_code == 202
    generic_message = request_response.get_json()["message"]
    assert "如果该邮箱与有效账号匹配" in generic_message
    assert len(deliveries) == 1
    recipient, display_name, reset_url, expires_minutes = deliveries[0]
    assert recipient == "rachel@example.com"
    assert display_name == "Rachel"
    assert expires_minutes == 30
    raw_token = reset_url.split("resetToken=", 1)[1]

    from database import get_connection

    with get_connection() as conn:
        token_row = conn.execute(
            "SELECT token_hash, used_at FROM password_reset_tokens"
        ).fetchone()
    assert token_row["token_hash"] == auth.token_hash(raw_token)
    assert token_row["token_hash"] != raw_token
    assert token_row["used_at"] is None

    unknown_response = client.post(
        "/api/auth/email-password-reset",
        json={"email": "unknown@example.com"},
    )
    assert unknown_response.status_code == 202
    assert unknown_response.get_json()["message"] == generic_message
    assert len(deliveries) == 1

    reset_response = client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "newPassword": "RachelNewPass456",
            "confirmPassword": "RachelNewPass456",
        },
    )
    assert reset_response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    old_login = client.post(
        "/api/auth/login",
        json={"account": "rachel@example.com", "password": "RachelPass123"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/auth/login",
        json={"account": "rachel@example.com", "password": "RachelNewPass456"},
    )
    assert new_login.status_code == 200

    reused_response = client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "newPassword": "AnotherPass789",
            "confirmPassword": "AnotherPass789",
        },
    )
    assert reused_response.status_code == 400
    assert reused_response.get_json()["message"] == "重置链接无效或已过期"


def test_create_meeting_returns_password_once(client):
    response = client.post(
        "/api/meetings",
        json={
            "title": "周会",
            "hostName": "William",
            "passwordRequired": True,
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


def test_create_meeting_defaults_to_no_password(client):
    response = client.post(
        "/api/meetings",
        json={"title": "默认无密码会议", "hostName": "Admin"},
    )

    assert response.status_code == 201
    created = response.get_json()
    assert created["passwordRequired"] is False
    assert "password" not in created
    assert created["attendees"] == ["admin@example.com"]
    assert created["jitsiUrl"] == created["meetingUrl"]
    assert created["meetingUrl"].startswith("https://meet.wusupower.com/")

    explicitly_empty = client.post(
        "/api/meetings",
        json={"title": "主动移除自己", "hostName": "Admin", "attendees": []},
    ).get_json()
    assert explicitly_empty["attendees"] == []


def test_jitsi_jwt_is_signed_for_only_the_verified_room(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setenv("JITSI_JWT_APP_ID", "meeting-control-test")
    monkeypatch.setenv("JITSI_JWT_APP_SECRET", "jwt-test-secret")
    monkeypatch.setenv("JITSI_JWT_SUBJECT", "meet.jitsi")
    monkeypatch.setenv("JITSI_JWT_TTL_SECONDS", "900")
    reset_app_modules()

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        create_logged_in_admin(test_client)
        created = test_client.post(
            "/api/meetings",
            json={
                "title": "JWT 测试会议",
                "hostName": "Admin",
                "passwordRequired": True,
            },
        ).get_json()

        assert created["meetingUrl"] == created["accessUrl"]
        assert created["jitsiUrl"] is None

        verified = test_client.post(
            f"/api/public/meetings/{created['roomId']}/verify",
            json={
                "password": created["password"],
                "displayName": "JWT Tester",
                "email": "jwt-tester@example.test",
            },
        )

        jitsi_url = verified.get_json()["jitsiUrl"]
        parsed_url = urlsplit(jitsi_url)
        token = parse_qs(parsed_url.query)["jwt"][0]

        valid_token = test_client.get(
            "/internal/jitsi/token/validate",
            headers={"X-Jitsi-Token": token, "X-Jitsi-Room": created["roomId"]},
        )
        assert valid_token.status_code == 204

        signature = token.split(".")[2]
        tampered_index = len(signature) // 2
        replacement = "A" if signature[tampered_index] != "A" else "B"
        tampered_token = (
            token[: token.rfind(".") + 1]
            + signature[:tampered_index]
            + replacement
            + signature[tampered_index + 1 :]
        )
        invalid_token = test_client.get(
            "/internal/jitsi/token/validate",
            headers={
                "X-Jitsi-Token": tampered_token,
                "X-Jitsi-Room": created["roomId"],
            },
        )
        assert invalid_token.status_code == 403

        # A 32-byte HS256 signature has two unused bits in its final Base64URL
        # character. Reject a non-canonical spelling even when it decodes to
        # the same signature bytes.
        base64url_alphabet = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        final_index = base64url_alphabet.index(signature[-1])
        alternate_signature = signature[:-1] + base64url_alphabet[final_index + 1]
        assert base64.urlsafe_b64decode(
            alternate_signature + "=" * (-len(alternate_signature) % 4)
        ) == base64.urlsafe_b64decode(
            signature + "=" * (-len(signature) % 4)
        )
        noncanonical_token = token[: token.rfind(".") + 1] + alternate_signature
        noncanonical_response = test_client.get(
            "/internal/jitsi/token/validate",
            headers={
                "X-Jitsi-Token": noncanonical_token,
                "X-Jitsi-Room": created["roomId"],
            },
        )
        assert noncanonical_response.status_code == 403

        wrong_room = test_client.get(
            "/internal/jitsi/token/validate",
            headers={"X-Jitsi-Token": token, "X-Jitsi-Room": "another-room"},
        )
        assert wrong_room.status_code == 403

    assert verified.status_code == 200
    encoded_header, encoded_payload, encoded_signature = token.split(".")

    def decode_segment(value: str) -> dict:
        padded = value + "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    header = decode_segment(encoded_header)
    payload = decode_segment(encoded_payload)
    expected_signature = hmac.new(
        b"jwt-test-secret",
        f"{encoded_header}.{encoded_payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    actual_signature = base64.urlsafe_b64decode(
        encoded_signature + "=" * (-len(encoded_signature) % 4)
    )

    assert parsed_url.path == f"/{created['roomId']}"
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert hmac.compare_digest(actual_signature, expected_signature)
    assert payload["aud"] == "meeting-control-test"
    assert payload["iss"] == "meeting-control-test"
    assert payload["sub"] == "meet.jitsi"
    assert payload["room"] == created["roomId"]
    assert payload["exp"] - payload["iat"] == 900
    assert payload["context"]["user"]["name"] == "JWT Tester"
    assert payload["context"]["user"]["email"] == "jwt-tester@example.test"


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
            "passwordRequired": True,
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


def test_user_can_disable_own_meeting_email_notifications(client, monkeypatch):
    import services

    current_user = client.get("/api/auth/me").get_json()["user"]
    assert current_user["emailNotificationsEnabled"] is True
    assert current_user["customThemeColor"] is None

    invalid_color_response = client.patch(
        "/api/auth/preferences",
        json={"customThemeColor": "blue"},
    )
    assert invalid_color_response.status_code == 400
    assert invalid_color_response.get_json()["message"] == "自定义颜色格式应为 #xxxxxx"

    color_response = client.patch(
        "/api/auth/preferences",
        json={"customThemeColor": "#12AbEf"},
    )
    assert color_response.status_code == 200
    assert color_response.get_json()["user"]["customThemeColor"] == "#12abef"
    assert client.get("/api/auth/me").get_json()["user"]["customThemeColor"] == "#12abef"

    preference_response = client.patch(
        "/api/auth/preferences",
        json={"emailNotificationsEnabled": False},
    )
    assert preference_response.status_code == 200
    assert preference_response.get_json()["user"]["emailNotificationsEnabled"] is False
    assert client.get("/api/auth/me").get_json()["user"]["emailNotificationsEnabled"] is False

    deliveries = []

    def record_notification(meeting, recipients, **kwargs):
        deliveries.append(list(recipients))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    monkeypatch.setattr(services, "send_meeting_invitation_notifications", record_notification)
    response = client.post(
        "/api/meetings",
        json={
            "title": "邮件首选项测试",
            "hostName": "Admin",
            "attendees": ["admin@example.com", "external@example.com"],
        },
    )

    assert response.status_code == 201
    assert deliveries == [["external@example.com"]]
    assert response.get_json()["emailNotification"] == {
        "status": "sent",
        "requested": 1,
        "sent": 1,
    }

    clear_color_response = client.patch(
        "/api/auth/preferences",
        json={"customThemeColor": None},
    )
    assert clear_color_response.status_code == 200
    assert clear_color_response.get_json()["user"]["customThemeColor"] is None


def test_regular_user_can_update_own_profile_without_admin_access(client):
    for username, email in (("alice-profile", "alice-profile@example.com"), ("bob-profile", "bob-profile@example.com")):
        response = client.post(
            "/api/admin/users",
            json={
                "username": username,
                "displayName": username,
                "email": email,
                "phoneNumber": "+86",
                "password": "ProfilePass123",
                "role": "scheduler",
                "status": "active",
            },
        )
        assert response.status_code == 201

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"username": "alice-profile", "password": "ProfilePass123"},
    ).status_code == 200

    update_response = client.patch(
        "/api/auth/profile",
        json={
            "displayName": "Alice Zhang",
            "phoneNumber": "+86 138 0013 8000",
            "email": "alice.zhang@example.com",
            "role": "admin",
        },
    )

    assert update_response.status_code == 200
    updated = update_response.get_json()["user"]
    assert updated["displayName"] == "Alice Zhang"
    assert updated["phoneNumber"] == "+86 138 0013 8000"
    assert updated["email"] == "alice.zhang@example.com"
    assert updated["role"] == "scheduler"
    assert client.get("/api/admin/users").status_code == 403

    duplicate_email_response = client.patch(
        "/api/auth/profile",
        json={"email": "bob-profile@example.com"},
    )
    assert duplicate_email_response.status_code == 409
    assert duplicate_email_response.get_json()["message"] == "邮箱已被使用"


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
            "passwordRequired": True,
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


def test_update_meeting_sends_update_removal_and_invitation_once(client, monkeypatch):
    import services

    created = client.post(
        "/api/meetings",
        json={
            "title": "变更通知测试",
            "hostName": "原主持人",
            "passwordRequired": False,
            "attendees": ["stay@example.com", "remove@example.com"],
            "startTime": "2048-01-01T09:00:00Z",
            "endTime": "2048-01-01T10:00:00Z",
        },
    ).get_json()
    deliveries = {"invitation": [], "update": [], "removal": []}

    def record_invitation(meeting, recipients, **kwargs):
        deliveries["invitation"].append((list(recipients), kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    def record_update(meeting, recipients, changes, **kwargs):
        deliveries["update"].append((list(recipients), list(changes), kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    def record_removal(meeting, recipients, **kwargs):
        deliveries["removal"].append((list(recipients), kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    monkeypatch.setattr(services, "send_meeting_invitation_notifications", record_invitation)
    monkeypatch.setattr(services, "send_meeting_update_notifications", record_update)
    monkeypatch.setattr(services, "send_meeting_removal_notifications", record_removal)

    response = client.put(
        f"/api/meetings/{created['id']}",
        json={
            "hostName": "新主持人",
            "passwordRequired": True,
            "attendees": ["stay@example.com", "STAY@example.com", "new@example.com"],
            "startTime": "2048-01-01T11:00:00Z",
            "endTime": "2048-01-01T12:30:00Z",
        },
    )

    assert response.status_code == 200
    updated = response.get_json()
    assert updated["changes"] == ["主持人", "会议时间", "会议链接"]
    assert deliveries["invitation"] == [
        (["new@example.com"], {"password": updated["password"], "recurrence_count": 1})
    ]
    assert deliveries["update"][0][0] == ["stay@example.com"]
    assert deliveries["update"][0][1] == ["主持人", "会议时间", "会议链接"]
    assert deliveries["update"][0][2]["scope"] == "single"
    assert deliveries["update"][0][2]["password"] == updated["password"]
    assert deliveries["removal"] == [(["remove@example.com"], {"scope": "single"})]
    assert updated["emailNotification"] == {"status": "sent", "requested": 3, "sent": 3}

    duplicate_response = client.put(
        f"/api/meetings/{created['id']}",
        json={
            "hostName": "新主持人",
            "passwordRequired": True,
            "attendees": ["stay@example.com", "new@example.com"],
            "startTime": "2048-01-01T11:00:00Z",
            "endTime": "2048-01-01T12:30:00Z",
        },
    )
    assert duplicate_response.status_code == 200
    duplicate = duplicate_response.get_json()
    assert duplicate["changes"] == []
    assert duplicate["emailNotification"] == {
        "status": "not_requested",
        "requested": 0,
        "sent": 0,
    }
    assert len(deliveries["invitation"]) == 1
    assert len(deliveries["update"]) == 1
    assert len(deliveries["removal"]) == 1


def test_meeting_list_only_includes_meetings_where_current_user_is_an_attendee(client):
    visible = client.post(
        "/api/meetings",
        json={
            "title": "我参加的会议",
            "hostName": "Someone Else",
            "attendees": ["admin@example.com"],
        },
    ).get_json()
    hidden = client.post(
        "/api/meetings",
        json={
            "title": "仅由我创建但未参加",
            "hostName": "Admin",
            "attendees": ["someone@example.com"],
        },
    ).get_json()

    assert hidden["mailOwner"] == "admin@example.com"
    listed = client.get("/api/meetings").get_json()["items"]
    assert [meeting["id"] for meeting in listed] == [visible["id"]]


def test_personal_calendar_subscription_only_contains_attendee_meetings(client):
    visible = client.post(
        "/api/meetings",
        json={
            "title": "个人日历可见会议",
            "hostName": "Admin",
            "attendees": ["admin@example.com"],
            "passwordRequired": True,
            "startTime": "2048-05-01T09:00:00Z",
            "endTime": "2048-05-01T10:00:00Z",
        },
    ).get_json()
    client.post(
        "/api/meetings",
        json={
            "title": "个人日历不可见会议",
            "hostName": "Another Host",
            "attendees": ["someone-else@example.com"],
            "startTime": "2048-05-02T09:00:00Z",
            "endTime": "2048-05-02T10:00:00Z",
        },
    )

    subscription_response = client.post("/api/calendar/subscription")
    assert subscription_response.status_code == 200
    subscription = subscription_response.get_json()
    assert subscription["subscriptionUrl"].startswith(
        "http://localhost:5173/api/calendar/subscriptions/"
    )
    assert subscription["subscriptionUrl"].endswith(".ics")
    assert subscription["webcalUrl"].startswith("webcal://localhost:5173/")
    assert client.post("/api/calendar/subscription").get_json()["subscriptionUrl"] == subscription[
        "subscriptionUrl"
    ]

    token = subscription["subscriptionUrl"].rsplit("/", 1)[1].removesuffix(".ics")
    from database import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT token_hash, token_encrypted FROM calendar_subscription_tokens"
        ).fetchone()
    assert token not in row["token_hash"]
    assert token not in row["token_encrypted"]

    client.post("/api/auth/logout")
    feed_path = urlsplit(subscription["subscriptionUrl"]).path
    feed_response = client.get(feed_path)
    assert feed_response.status_code == 200
    assert feed_response.content_type == "text/calendar; charset=utf-8"
    feed = feed_response.get_data(as_text=True)
    assert "BEGIN:VCALENDAR\r\n" in feed
    assert f"UID:meeting-{visible['id']}@meeting-control-system" in feed
    assert "SUMMARY:个人日历可见会议" in feed
    assert "个人日历不可见会议" not in feed
    assert "DTSTART:20480501T090000Z" in feed
    assert visible["accessUrl"] in feed
    assert visible["password"] not in feed
    assert "END:VCALENDAR\r\n" in feed

    assert client.get("/api/calendar/subscriptions/not-a-real-token.ics").status_code == 404


def test_all_attendees_expands_to_every_active_user(client):
    assert client.post(
        "/api/admin/users",
        json={
            "username": "alice",
            "displayName": "Alice",
            "email": "alice@example.com",
            "password": "AlicePass123",
            "role": "scheduler",
            "status": "active",
        },
    ).status_code == 201
    assert client.post(
        "/api/admin/users",
        json={
            "username": "disabled-user",
            "displayName": "Disabled User",
            "email": "disabled@example.com",
            "password": "DisabledPass123",
            "role": "scheduler",
            "status": "disabled",
        },
    ).status_code == 201

    response = client.post(
        "/api/meetings",
        json={
            "title": "全员会议",
            "hostName": "Admin",
            "attendees": ["@all", "external@example.com", "ADMIN@example.com"],
        },
    )

    assert response.status_code == 201
    created = response.get_json()
    assert set(created["attendees"]) == {
        "admin@example.com",
        "alice@example.com",
        "external@example.com",
    }
    assert "@all" not in created["attendees"]
    assert "disabled@example.com" not in created["attendees"]
    assert [meeting["id"] for meeting in client.get("/api/meetings").get_json()["items"]] == [
        created["id"]
    ]


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
            "startTime": "2026-08-07T00:00:00Z",
            "endTime": "2026-08-07T02:00:00Z",
            "accessUrl": "https://meeting.example.com/join/example",
        },
        ["alice@example.com"],
        password="1234",
    )
    reset_result = mailer.send_password_reset_email(
        "alice@example.com",
        "Alice",
        "https://meeting.example.com/?resetToken=one-time-token",
        30,
    )
    event_meeting = {
        "title": "项目例会",
        "hostName": "新主持人",
        "mailOwner": "admin@example.com",
        "startTime": "2026-08-07T01:00:00Z",
        "endTime": "2026-08-07T02:30:00Z",
        "accessUrl": "https://meeting.example.com/join/updated",
    }
    update_result = mailer.send_meeting_update_notifications(
        event_meeting,
        ["alice@example.com", "ALICE@example.com"],
        ["会议时间", "主持人", "会议链接", "会议时间"],
        scope="following",
        password="5678",
    )
    cancellation_result = mailer.send_meeting_cancellation_notifications(
        event_meeting,
        ["bob@example.com"],
        scope="single",
    )
    removal_result = mailer.send_meeting_removal_notifications(
        event_meeting,
        ["carol@example.com"],
        scope="series",
    )

    assert result == {"status": "sent", "requested": 1, "sent": 1}
    assert reset_result == {"status": "sent", "requested": 1, "sent": 1}
    assert update_result == {"status": "sent", "requested": 1, "sent": 1}
    assert cancellation_result == {"status": "sent", "requested": 1, "sent": 1}
    assert removal_result == {"status": "sent", "requested": 1, "sent": 1}
    assert ("connect", "smtp.example.com", 587, 10) in smtp_events
    assert ("starttls",) in smtp_events
    assert ("login", "mailer@example.com", "app-password") in smtp_events
    assert len(sent_messages) == 5
    assert sent_messages[0]["To"] == "alice@example.com"
    assert sent_messages[0]["Reply-To"] == "admin@example.com"
    assert "项目例会" in sent_messages[0]["Subject"]
    invitation_content = sent_messages[0].get_content()
    assert "会议时间：2026年08月07日8点" in invitation_content
    assert "会议时长：2小时" in invitation_content
    assert "会议链接：https://meeting.example.com/join/example" in invitation_content
    assert "会议密码：1234" in invitation_content
    assert "开始时间：" not in invitation_content
    assert "结束时间：" not in invitation_content
    assert sent_messages[1]["To"] == "alice@example.com"
    assert "设置新密码" in sent_messages[1]["Subject"]
    assert "resetToken=one-time-token" in sent_messages[1].get_content()
    assert "30 分钟后失效" in sent_messages[1].get_content()
    assert sent_messages[2]["Subject"] == "[会议更新] 项目例会"
    assert "影响范围：本次及后续会议" in sent_messages[2].get_content()
    assert "变更内容：会议时间、主持人、会议链接" in sent_messages[2].get_content()
    assert "会议密码：5678" in sent_messages[2].get_content()
    assert sent_messages[3]["Subject"] == "[会议取消] 项目例会"
    assert "影响范围：仅本次会议" in sent_messages[3].get_content()
    assert "会议链接：" not in sent_messages[3].get_content()
    assert sent_messages[4]["Subject"] == "[参会移除] 项目例会"
    assert "影响范围：整个会议系列" in sent_messages[4].get_content()


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
        json={"title": "项目同步", "hostName": "Alice", "passwordRequired": True},
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


def test_cancelled_meeting_is_retained_and_rejected_by_reservation(client):
    created = client.post("/api/meetings", json={"title": "取消测试", "hostName": "Bob"}).get_json()

    delete_response = client.delete(f"/api/meetings/{created['id']}")
    assert delete_response.status_code == 200
    cancelled = delete_response.get_json()
    assert cancelled["cancelled"] is True
    assert cancelled["cancelledCount"] == 1
    assert cancelled["ids"] == [created["id"]]
    assert cancelled["status"] == "Cancelled"

    detail_response = client.get(f"/api/meetings/{created['id']}")
    assert detail_response.status_code == 200
    assert detail_response.get_json()["status"] == "Cancelled"

    response = client.post("/conference", data={"name": created["roomId"]})
    assert response.status_code == 403
    assert response.get_json()["message"] == "会议已取消"


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
            "attendees": ["admin@example.com"],
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
            "passwordRequired": True,
            "attendees": ["Alice", "Bob", "admin@example.com"],
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
    assert all(meeting["attendees"] == ["Alice", "Bob", "admin@example.com"] for meeting in series)

    listed = client.get("/api/meetings").get_json()["items"]
    assert len(listed) == 4
    assert {meeting["seriesId"] for meeting in listed} == {created["seriesId"]}


def test_recurring_meeting_supports_single_following_and_series_updates(client):
    created = client.post(
        "/api/meetings",
        json={
            "title": "范围修改测试",
            "hostName": "初始主持人",
            "startTime": "2048-01-01T09:00:00Z",
            "endTime": "2048-01-01T10:00:00Z",
            "recurrence": {"enabled": True, "type": "every_n_days", "interval": 3, "count": 4},
        },
    ).get_json()
    series_ids = [meeting["id"] for meeting in created["seriesMeetings"]]

    single = client.put(
        f"/api/meetings/{series_ids[1]}",
        json={"hostName": "仅本次主持人"},
    ).get_json()
    assert single["updateScope"] == "single"
    assert single["affectedIds"] == [series_ids[1]]
    assert client.get(f"/api/meetings/{series_ids[0]}").get_json()["hostName"] == "初始主持人"
    assert client.get(f"/api/meetings/{series_ids[1]}").get_json()["hostName"] == "仅本次主持人"

    following = client.put(
        f"/api/meetings/{series_ids[1]}?scope=following",
        json={
            "hostName": "后续主持人",
            "startTime": "2048-01-05T09:00:00Z",
            "endTime": "2048-01-05T10:00:00Z",
        },
    ).get_json()
    assert following["updateScope"] == "following"
    assert following["affectedIds"] == series_ids[1:]
    assert [meeting["startTime"] for meeting in following["affectedMeetings"]] == [
        "2048-01-05T09:00:00.000Z",
        "2048-01-08T09:00:00.000Z",
        "2048-01-11T09:00:00.000Z",
    ]
    assert client.get(f"/api/meetings/{series_ids[0]}").get_json()["startTime"] == (
        "2048-01-01T09:00:00.000Z"
    )

    whole_series = client.put(
        f"/api/meetings/{series_ids[2]}?scope=series",
        json={"title": "整个系列新标题", "hostName": "系列主持人"},
    ).get_json()
    assert whole_series["updateScope"] == "series"
    assert whole_series["affectedIds"] == series_ids
    assert whole_series["affectedCount"] == 4
    assert {
        client.get(f"/api/meetings/{meeting_id}").get_json()["title"]
        for meeting_id in series_ids
    } == {"整个系列新标题"}


def test_cancel_single_occurrence_is_idempotent_and_series_can_cancel_remaining(
    client,
    monkeypatch,
):
    import services

    created = client.post(
        "/api/meetings",
        json={
            "title": "单次取消测试",
            "hostName": "Admin",
            "attendees": ["member@example.com", "MEMBER@example.com"],
            "startTime": "2048-02-01T09:00:00Z",
            "endTime": "2048-02-01T10:00:00Z",
            "recurrence": {"enabled": True, "type": "weekly", "count": 3},
        },
    ).get_json()
    series_ids = [meeting["id"] for meeting in created["seriesMeetings"]]
    deliveries = []

    def record_cancellation(meeting, recipients, **kwargs):
        deliveries.append((list(recipients), kwargs))
        return {"status": "sent", "requested": len(recipients), "sent": len(recipients)}

    monkeypatch.setattr(services, "send_meeting_cancellation_notifications", record_cancellation)

    cancelled_once = client.delete(f"/api/meetings/{series_ids[1]}").get_json()
    assert cancelled_once["cancelledCount"] == 1
    assert cancelled_once["ids"] == [series_ids[1]]
    assert deliveries == [(["member@example.com"], {"scope": "single"})]
    assert [
        client.get(f"/api/meetings/{meeting_id}").get_json()["status"]
        for meeting_id in series_ids
    ] == ["Scheduled", "Cancelled", "Scheduled"]

    duplicate_cancel = client.delete(f"/api/meetings/{series_ids[1]}").get_json()
    assert duplicate_cancel["alreadyCancelled"] is True
    assert duplicate_cancel["cancelledCount"] == 0
    assert duplicate_cancel["emailNotification"]["requested"] == 0
    assert len(deliveries) == 1

    cancelled_series = client.delete(
        f"/api/meetings/{series_ids[1]}?scope=series"
    ).get_json()
    assert cancelled_series["cancelledCount"] == 2
    assert cancelled_series["ids"] == [series_ids[0], series_ids[2]]
    assert deliveries[-1] == (["member@example.com"], {"scope": "series"})
    assert {
        client.get(f"/api/meetings/{meeting_id}").get_json()["status"]
        for meeting_id in series_ids
    } == {"Cancelled"}


def test_cancel_recurring_meeting_from_selected_occurrence_forward(client):
    created = client.post(
        "/api/meetings",
        json={
            "title": "后续取消测试",
            "hostName": "Admin",
            "startTime": "2048-03-01T09:00:00Z",
            "endTime": "2048-03-01T10:00:00Z",
            "recurrence": {"enabled": True, "type": "weekly", "count": 3},
        },
    ).get_json()
    series_ids = [meeting["id"] for meeting in created["seriesMeetings"]]

    response = client.delete(f"/api/meetings/{series_ids[1]}?scope=following")

    assert response.status_code == 200
    assert response.get_json()["ids"] == series_ids[1:]
    assert [
        client.get(f"/api/meetings/{meeting_id}").get_json()["status"]
        for meeting_id in series_ids
    ] == ["Scheduled", "Cancelled", "Cancelled"]


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


def test_cancel_recurring_series_retains_cancelled_occurrences(client):
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
    assert payload["cancelled"] is True
    assert payload["cancelledCount"] == 3
    assert payload["ids"] == series_ids
    assert payload["seriesId"] == created["seriesId"]
    listed = client.get("/api/meetings").get_json()["items"]
    assert {meeting["id"] for meeting in listed} == set(series_ids)
    assert {meeting["status"] for meeting in listed} == {"Cancelled"}
    for meeting_id in series_ids:
        detail_response = client.get(f"/api/meetings/{meeting_id}")
        assert detail_response.status_code == 200
        assert detail_response.get_json()["status"] == "Cancelled"


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


def test_process_write_lock_supports_windows_locking_api(tmp_path, monkeypatch):
    import database

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        def __init__(self):
            self.calls = []

        def locking(self, file_descriptor, mode, byte_count):
            self.calls.append((file_descriptor, mode, byte_count))

    fake_msvcrt = FakeMsvcrt()
    lock_path = tmp_path / "windows-compatible.lock"
    monkeypatch.setattr(database, "_fcntl", None)
    monkeypatch.setattr(database, "_msvcrt", fake_msvcrt)
    monkeypatch.setattr(database, "database_lock_path", lambda: lock_path)

    with database.process_write_lock():
        assert lock_path.exists()

    assert [call[1:] for call in fake_msvcrt.calls] == [
        (fake_msvcrt.LK_LOCK, 1),
        (fake_msvcrt.LK_UNLCK, 1),
    ]


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


def test_public_urls_use_localhost_origin_by_default(tmp_path, monkeypatch):
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
        response = test_client.post(
            "/api/meetings",
            json={"title": "默认域名", "hostName": "Alice", "passwordRequired": True},
        )
        subscription_response = test_client.post("/api/calendar/subscription")

    assert response.status_code == 201
    created = response.get_json()
    assert created["accessUrl"].startswith("http://localhost:5173/join/")
    assert created["meetingUrl"] == created["accessUrl"]
    assert subscription_response.status_code == 200
    assert subscription_response.get_json()["subscriptionUrl"].startswith(
        "http://localhost:5173/api/calendar/subscriptions/"
    )


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


def test_milestones_are_filtered_by_related_user_creator_and_global_scope(client):
    created_users = {}
    for username in ("bob", "charlie"):
        response = client.post(
            "/api/admin/users",
            json={
                "username": username,
                "displayName": username.title(),
                "email": f"{username}@example.com",
                "password": f"{username.title()}Pass123",
                "role": "scheduler",
                "status": "active",
            },
        )
        assert response.status_code == 201
        created_users[username] = response.get_json()["user"]

    current_user = client.get("/api/auth/me").get_json()["user"]
    default_response = client.post(
        "/api/milestones",
        json={"title": "管理员私有节点", "dueDate": "2048-06-01"},
    )
    assert default_response.status_code == 201
    default_milestone = default_response.get_json()["milestone"]
    assert default_milestone["status"] == "planned"
    assert default_milestone["isGlobal"] is False
    assert [user["id"] for user in default_milestone["relatedUsers"]] == [current_user["id"]]

    assigned_response = client.post(
        "/api/milestones",
        json={
            "title": "Bob 相关节点",
            "description": "只分配给 Bob",
            "dueDate": "2048-06-02",
            "status": "in_progress",
            "relatedUserIds": [created_users["bob"]["id"]],
        },
    )
    assert assigned_response.status_code == 201
    assigned_milestone = assigned_response.get_json()["milestone"]

    global_response = client.post(
        "/api/milestones",
        json={
            "title": "全局发布节点",
            "dueDate": "2048-06-03",
            "isGlobal": True,
            "relatedUserIds": [999999],
        },
    )
    assert global_response.status_code == 201
    global_milestone = global_response.get_json()["milestone"]
    assert global_milestone["relatedUsers"] == []

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "bob", "password": "BobPass123"},
    ).status_code == 200

    bob_items = client.get("/api/milestones").get_json()["items"]
    assert {item["id"] for item in bob_items} == {
        assigned_milestone["id"],
        global_milestone["id"],
    }
    assert next(item for item in bob_items if item["id"] == assigned_milestone["id"])["canEdit"] is False
    assert client.patch(
        f"/api/milestones/{assigned_milestone['id']}",
        json={"status": "completed"},
    ).status_code == 403

    bob_owned_response = client.post(
        "/api/milestones",
        json={
            "title": "Bob 创建但分配给 Charlie",
            "dueDate": "2048-06-04",
            "relatedUserIds": [created_users["charlie"]["id"]],
        },
    )
    assert bob_owned_response.status_code == 201
    bob_owned = bob_owned_response.get_json()["milestone"]
    assert bob_owned["canEdit"] is True
    assert bob_owned["id"] in {
        item["id"] for item in client.get("/api/milestones").get_json()["items"]
    }

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "charlie", "password": "CharliePass123"},
    ).status_code == 200
    charlie_ids = {item["id"] for item in client.get("/api/milestones").get_json()["items"]}
    assert charlie_ids == {global_milestone["id"], bob_owned["id"]}
    assert client.delete(f"/api/milestones/{bob_owned['id']}").status_code == 403

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "bob", "password": "BobPass123"},
    ).status_code == 200
    update_response = client.patch(
        f"/api/milestones/{bob_owned['id']}",
        json={"status": "completed", "description": "已经交付"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["milestone"]["status"] == "completed"
    assert client.delete(f"/api/milestones/{bob_owned['id']}").status_code == 200
    assert bob_owned["id"] not in {
        item["id"] for item in client.get("/api/milestones").get_json()["items"]
    }


def test_milestone_pins_are_private_to_each_visible_user(client):
    bob_response = client.post(
        "/api/admin/users",
        json={
            "username": "pin-bob",
            "displayName": "Pin Bob",
            "email": "pin-bob@example.com",
            "password": "PinBobPass123",
            "role": "scheduler",
            "status": "active",
        },
    )
    assert bob_response.status_code == 201

    milestone = client.post(
        "/api/milestones",
        json={"title": "共享置顶测试", "dueDate": "2048-07-01", "isGlobal": True},
    ).get_json()["milestone"]
    assert milestone["isPinned"] is False

    invalid_pin = client.patch(f"/api/milestones/{milestone['id']}/pin", json={"pinned": "yes"})
    assert invalid_pin.status_code == 400

    admin_pin = client.patch(
        f"/api/milestones/{milestone['id']}/pin",
        json={"pinned": True},
    )
    assert admin_pin.status_code == 200
    assert admin_pin.get_json()["milestone"]["isPinned"] is True

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "pin-bob", "password": "PinBobPass123"},
    ).status_code == 200
    bob_milestone = next(
        item
        for item in client.get("/api/milestones").get_json()["items"]
        if item["id"] == milestone["id"]
    )
    assert bob_milestone["isPinned"] is False
    assert client.patch(
        f"/api/milestones/{milestone['id']}/pin",
        json={"pinned": True},
    ).get_json()["milestone"]["isPinned"] is True

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "admin", "password": ADMIN_PASSWORD},
    ).status_code == 200
    assert client.patch(
        f"/api/milestones/{milestone['id']}/pin",
        json={"pinned": False},
    ).get_json()["milestone"]["isPinned"] is False

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"account": "pin-bob", "password": "PinBobPass123"},
    ).status_code == 200
    bob_milestone = next(
        item
        for item in client.get("/api/milestones").get_json()["items"]
        if item["id"] == milestone["id"]
    )
    assert bob_milestone["isPinned"] is True
