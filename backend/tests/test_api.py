from __future__ import annotations

import os
import re
import sqlite3
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
    assert {meeting["seriesId"] for meeting in series} == {created["seriesId"]}
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
    for module_name in ["config", "database"]:
        sys.modules.pop(module_name, None)

    from database import init_db

    init_db()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(meetings)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(meetings)").fetchall()}

    assert {"series_id", "recurrence_type", "recurrence_interval", "recurrence_count", "recurrence_index"} <= columns
    assert "idx_meetings_series_id" in indexes


def test_cors_allows_multiple_frontend_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://127.0.0.1:5173,http://localhost:5173")

    for module_name in ["config", "database", "services", "app"]:
        sys.modules.pop(module_name, None)

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        for origin in ["http://127.0.0.1:5173", "http://localhost:5173"]:
            response = test_client.get("/api/health", headers={"Origin": origin})
            assert response.headers["Access-Control-Allow-Origin"] == origin
