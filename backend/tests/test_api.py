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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "meetings.sqlite3"))
    monkeypatch.setenv("PASSWORD_SECRET", "test-secret")
    monkeypatch.setenv("JITSI_BASE_URL", "https://meet.wusupower.com/")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("MEETING_LINK_ORIGIN", "http://localhost:5173")

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
    assert "lobbyEnabled" not in created

    detail = client.get(f"/api/meetings/{created['id']}").get_json()
    assert "password" not in detail
    assert detail["attendees"] == ["Alice", "Bob"]
    assert "lobbyEnabled" not in detail


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
    for module_name in ["config", "database"]:
        sys.modules.pop(module_name, None)

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
    for module_name in ["config", "database", "services", "app"]:
        sys.modules.pop(module_name, None)

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
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

    for module_name in ["config", "database", "services", "app"]:
        sys.modules.pop(module_name, None)

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        response = test_client.post("/api/meetings", json={"title": "默认域名", "hostName": "Alice"})

    assert response.status_code == 201
    created = response.get_json()
    assert created["accessUrl"].startswith("http://bookmeeting.wusupower.com/join/")
    assert created["meetingUrl"] == created["accessUrl"]


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
