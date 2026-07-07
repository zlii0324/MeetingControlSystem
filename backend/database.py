from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from config import config


def get_connection() -> sqlite3.Connection:
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meetings (
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
                series_id TEXT,
                recurrence_type TEXT,
                recurrence_interval INTEGER,
                recurrence_count INTEGER,
                recurrence_index INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_started_at TEXT,
                finished_at TEXT,
                cancelled_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_meetings_room_id ON meetings(room_id);
            CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
            CREATE INDEX IF NOT EXISTS idx_meetings_start_time ON meetings(start_time);

            CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER,
                room_id TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                join_time TEXT NOT NULL,
                success INTEGER NOT NULL,
                fail_reason TEXT,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS meeting_attendees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_meeting_attendees_meeting_id
                ON meeting_attendees(meeting_id);
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(meetings)").fetchall()
        }
        if "password_required" not in columns:
            conn.execute(
                "ALTER TABLE meetings ADD COLUMN password_required INTEGER NOT NULL DEFAULT 1"
            )
        recurrence_columns = {
            "series_id": "TEXT",
            "recurrence_type": "TEXT",
            "recurrence_interval": "INTEGER",
            "recurrence_count": "INTEGER",
            "recurrence_index": "INTEGER",
        }
        for column_name, column_type in recurrence_columns.items():
            if column_name not in columns:
                conn.execute(f"ALTER TABLE meetings ADD COLUMN {column_name} {column_type}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_series_id ON meetings(series_id)")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def fetch_one(query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
        return row_to_dict(row)


def fetch_all(query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]
