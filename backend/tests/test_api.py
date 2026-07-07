from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("JITSI_BASE_URL", "https://meet.wusupower.com/")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    for module_name in ["config", "database", "services", "app"]:
        sys.modules.pop(module_name, None)

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client

    os.environ.pop("DATABASE_PATH", None)


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

    detail = client.get(f"/api/meetings/{created['id']}").get_json()
    assert "password" not in detail
    assert detail["attendees"] == ["Alice", "Bob"]


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
