from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import threading
from pathlib import Path
from typing import BinaryIO, Generator

from sqlalchemy import Engine, URL, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import config
from models import Base


try:
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # Linux and macOS
    _msvcrt = None


_WRITE_LOCK_MUTEX = threading.RLock()
_WRITE_LOCK_STATE = threading.local()


def _database_url() -> URL:
    if config.mysql_enabled:
        return URL.create(
            drivername="mysql+pymysql",
            username=config.database_username,
            password=config.database_password,
            host=config.database_host,
            port=config.database_port,
            database=config.database_name,
            query={"charset": config.database_charset},
        )

    db_path = Path(config.database_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return URL.create(drivername="sqlite+pysqlite", database=str(db_path))


def _create_database_engine() -> Engine:
    if config.mysql_enabled:
        return create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=config.database_pool_size,
            pool_recycle=config.database_pool_recycle_seconds,
        )

    engine = create_engine(
        _database_url(),
        connect_args={"check_same_thread": False, "timeout": 10.0},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA cache_size = -64000")
        finally:
            cursor.close()

    return engine


engine = _create_database_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
    # MySQL handles concurrent writers transactionally. The file lock only
    # serializes processes that share a local SQLite database file.
    if config.mysql_enabled:
        yield
        return

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


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
    }


def _sqlite_has_unique_room_id(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(meetings)"):
        if not row["unique"]:
            continue
        columns = [
            item["name"]
            for item in conn.execute(
                f"PRAGMA index_info({_quote_identifier(row['name'])})"
            )
        ]
        if columns == ["room_id"]:
            return True
    return False


def _rebuild_legacy_meetings(conn: sqlite3.Connection, columns: set[str]) -> None:
    defaults = {
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
    ordered_columns = list(defaults)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS meetings_new")
    conn.execute(
        """
        CREATE TABLE meetings_new (
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
        )
        """
    )
    insert_columns = ", ".join(_quote_identifier(name) for name in ordered_columns)
    select_columns = ", ".join(
        _quote_identifier(name) if name in columns else defaults[name]
        for name in ordered_columns
    )
    conn.execute(
        f"INSERT INTO meetings_new ({insert_columns}) "
        f"SELECT {select_columns} FROM meetings"
    )
    conn.execute("DROP TABLE meetings")
    conn.execute("ALTER TABLE meetings_new RENAME TO meetings")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _upgrade_legacy_sqlite_schema() -> None:
    raw = engine.raw_connection()
    dbapi_conn = raw.driver_connection
    old_row_factory = dbapi_conn.row_factory
    dbapi_conn.row_factory = sqlite3.Row
    try:
        meeting_exists = dbapi_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meetings'"
        ).fetchone()
        if meeting_exists:
            columns = _sqlite_columns(dbapi_conn, "meetings")
            additions = {
                "password_required": "INTEGER NOT NULL DEFAULT 0",
                "series_id": "TEXT",
                "recurrence_type": "TEXT",
                "recurrence_interval": "INTEGER",
                "recurrence_count": "INTEGER",
                "recurrence_index": "INTEGER",
            }
            for name, definition in additions.items():
                if name not in columns:
                    dbapi_conn.execute(
                        f"ALTER TABLE meetings ADD COLUMN {_quote_identifier(name)} {definition}"
                    )
                    columns.add(name)
            if _sqlite_has_unique_room_id(dbapi_conn):
                _rebuild_legacy_meetings(dbapi_conn, columns)

        users_exists = dbapi_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if users_exists:
            user_columns = _sqlite_columns(dbapi_conn, "users")
            additions = {
                "email_notifications_enabled": "INTEGER NOT NULL DEFAULT 1",
                "job_title": "TEXT NOT NULL DEFAULT ''",
                "phone_number": "TEXT NOT NULL DEFAULT ''",
                "custom_theme_color": "TEXT",
            }
            for name, definition in additions.items():
                if name not in user_columns:
                    dbapi_conn.execute(
                        f"ALTER TABLE users ADD COLUMN {_quote_identifier(name)} {definition}"
                    )
        dbapi_conn.commit()
    except Exception:
        dbapi_conn.rollback()
        raise
    finally:
        dbapi_conn.row_factory = old_row_factory
        raw.close()


def init_db() -> None:
    with process_write_lock():
        if not config.mysql_enabled:
            _upgrade_legacy_sqlite_schema()
        Base.metadata.create_all(engine)
        # ``create_all`` does not add indexes to tables that already existed.
        # Create model-declared indexes separately so legacy SQLite databases
        # receive the same indexes as fresh SQLite/MySQL installations.
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(engine, checkfirst=True)


@contextmanager
def get_connection():
    """Deprecated SQLite-only helper for legacy inspection scripts and tests.

    All application CRUD uses ``session_scope`` and mapped ORM entities.
    """
    if config.mysql_enabled:
        raise RuntimeError("get_connection is only available for SQLite maintenance")
    raw = engine.raw_connection()
    dbapi_conn = raw.driver_connection
    old_row_factory = dbapi_conn.row_factory
    dbapi_conn.row_factory = sqlite3.Row
    try:
        yield dbapi_conn
        dbapi_conn.commit()
    except Exception:
        dbapi_conn.rollback()
        raise
    finally:
        dbapi_conn.row_factory = old_row_factory
        raw.close()
