from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import threading
from pathlib import Path
from typing import Any, BinaryIO, Generator, Iterable

try:
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # Linux and macOS
    _msvcrt = None

from config import config


_WRITE_LOCK_MUTEX = threading.RLock()
_WRITE_LOCK_STATE = threading.local()

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
    "password_required": "0",
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
        password_required INTEGER NOT NULL DEFAULT 0,
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


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -64000")
        with conn:
            yield conn
    finally:
        conn.close()


def database_lock_path() -> Path:
    return Path(f"{config.database_path}.lock")


def _acquire_file_lock(lock_file: BinaryIO) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
        return

    if _msvcrt is not None:
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_LOCK, 1)
        return

    raise RuntimeError("当前操作系统不支持进程文件锁")


def _release_file_lock(lock_file: BinaryIO) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
        return

    if _msvcrt is not None:
        lock_file.seek(0)
        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)
        return

    raise RuntimeError("当前操作系统不支持进程文件锁")


@contextmanager
def process_write_lock() -> Generator[None, None, None]:
    depth = getattr(_WRITE_LOCK_STATE, "depth", 0)
    if depth:
        _WRITE_LOCK_STATE.depth = depth + 1
        try:
            yield
        finally:
            _WRITE_LOCK_STATE.depth -= 1
        return

    lock_path = database_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK_MUTEX:
        with lock_path.open("a+b") as lock_file:
            _acquire_file_lock(lock_file)
            _WRITE_LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _WRITE_LOCK_STATE.depth = 0
                _release_file_lock(lock_file)


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
    with process_write_lock():
        _init_db_locked()


def _init_db_locked() -> None:
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

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                job_title TEXT NOT NULL DEFAULT '',
                phone_number TEXT NOT NULL DEFAULT '',
                email_notifications_enabled INTEGER NOT NULL DEFAULT 1,
                custom_theme_color TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'scheduler')),
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'active', 'rejected', 'disabled')
                ),
                register_message TEXT,
                approved_by INTEGER,
                approved_at TEXT,
                rejected_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT,
                FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                created_ip TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS password_reset_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
                requested_at TEXT NOT NULL,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS calendar_subscription_tokens (
                user_id INTEGER PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                token_encrypted TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS user_group_members (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                member_role TEXT NOT NULL CHECK (member_role IN ('admin', 'member')),
                added_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id),
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (added_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('planned', 'in_progress', 'completed')
                ),
                is_global INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS milestone_related_users (
                milestone_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (milestone_id, user_id),
                FOREIGN KEY (milestone_id) REFERENCES milestones(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_password_reset_requests_status
                ON password_reset_requests(status);
            CREATE INDEX IF NOT EXISTS idx_password_reset_requests_user_id
                ON password_reset_requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id
                ON password_reset_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_expires_at
                ON password_reset_tokens(expires_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_subscription_tokens_hash
                ON calendar_subscription_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_user_group_members_user_id
                ON user_group_members(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_group_members_group_id
                ON user_group_members(group_id);
            CREATE INDEX IF NOT EXISTS idx_milestones_due_date ON milestones(due_date);
            CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
            CREATE INDEX IF NOT EXISTS idx_milestones_created_by ON milestones(created_by);
            CREATE INDEX IF NOT EXISTS idx_milestone_related_users_user_id
                ON milestone_related_users(user_id);
            """
        )

        columns = _meeting_columns(conn)
        if "password_required" not in columns:
            conn.execute(
                "ALTER TABLE meetings ADD COLUMN password_required INTEGER NOT NULL DEFAULT 0"
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

        user_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "email_notifications_enabled" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN email_notifications_enabled INTEGER NOT NULL DEFAULT 1"
            )
        if "job_title" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN job_title TEXT NOT NULL DEFAULT ''")
        if "phone_number" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN phone_number TEXT NOT NULL DEFAULT ''")
        if "custom_theme_color" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN custom_theme_color TEXT")

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
