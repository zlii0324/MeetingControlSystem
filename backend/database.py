from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from config import config


MEETING_COLUMNS = [
    "id",
    "room_id",
    "title",
    "host_name",
    "mail_owner",
    "start_time",
    "end_time",
    "duration_seconds",
    "status",
    "password_required",
    "password_hash",
    "password_encrypted",
    "max_occupants",
    "lobby_enabled",
    "meeting_url",
    "series_id",
    "recurrence_type",
    "recurrence_interval",
    "recurrence_count",
    "recurrence_index",
    "created_at",
    "updated_at",
    "last_started_at",
    "finished_at",
    "cancelled_at",
]

MEETING_COLUMN_DEFAULTS = {
    "id": "NULL",
    "room_id": "''",
    "title": "'Untitled Meeting'",
    "host_name": "'Unknown Host'",
    "mail_owner": "NULL",
    "start_time": "'1970-01-01T00:00:00.000Z'",
    "end_time": "'1970-01-01T00:00:00.000Z'",
    "duration_seconds": "0",
    "status": "'Scheduled'",
    "password_required": "1",
    "password_hash": "''",
    "password_encrypted": "''",
    "max_occupants": "30",
    "lobby_enabled": "0",
    "meeting_url": "''",
    "series_id": "NULL",
    "recurrence_type": "NULL",
    "recurrence_interval": "1",
    "recurrence_count": "NULL",
    "recurrence_index": "NULL",
    "created_at": "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
    "updated_at": "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
    "last_started_at": "NULL",
    "finished_at": "NULL",
    "cancelled_at": "NULL",
}


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _create_meetings_table_sql(table_name: str = "meetings") -> str:
    if table_name not in {"meetings", "meetings_new"}:
        raise ValueError("unexpected meetings table name")

    return f"""
    CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id TEXT NOT NULL,
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
    """


def get_connection() -> sqlite3.Connection:
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _meeting_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("PRAGMA table_info(meetings)").fetchall()
    }


def _has_unique_room_id_constraint(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(meetings)").fetchall():
        if not row["unique"]:
            continue

        index_name = row["name"]
        columns = [
            index_row["name"]
            for index_row in conn.execute(
                f"PRAGMA index_info({_quote_identifier(index_name)})"
            ).fetchall()
        ]
        if columns == ["room_id"]:
            return True
    return False


def _recreate_meetings_without_unique_room_id(
    conn: sqlite3.Connection,
    existing_columns: set[str],
) -> None:
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS meetings_new")
    conn.execute(_create_meetings_table_sql("meetings_new"))

    insert_columns = ", ".join(_quote_identifier(column) for column in MEETING_COLUMNS)
    select_columns = []
    for column in MEETING_COLUMNS:
        if column in existing_columns:
            select_columns.append(_quote_identifier(column))
        else:
            select_columns.append(f"{MEETING_COLUMN_DEFAULTS[column]} AS {_quote_identifier(column)}")

    conn.execute(
        f"""
        INSERT INTO meetings_new ({insert_columns})
        SELECT {", ".join(select_columns)}
          FROM meetings
        """
    )
    conn.execute("DROP TABLE meetings")
    conn.execute("ALTER TABLE meetings_new RENAME TO meetings")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            _create_meetings_table_sql("meetings")
            + """

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

        columns = _meeting_columns(conn)
        if "password_required" not in columns:
            conn.execute(
                "ALTER TABLE meetings ADD COLUMN password_required INTEGER NOT NULL DEFAULT 1"
            )
            columns.add("password_required")
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
                columns.add(column_name)

        if _has_unique_room_id_constraint(conn):
            _recreate_meetings_without_unique_room_id(conn, columns)

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_meetings_room_id ON meetings(room_id);
            CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
            CREATE INDEX IF NOT EXISTS idx_meetings_start_time ON meetings(start_time);
            CREATE INDEX IF NOT EXISTS idx_meetings_series_id ON meetings(series_id);
            """
        )


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
