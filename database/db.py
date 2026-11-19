"""SQLite connection and safe parameterized query helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, List, Optional, Sequence, Union

from config import DEFAULT_DB_PATH
from database.models import SCHEMA_STATEMENTS


def resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve database file path and ensure its parent directory exists."""
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def get_connection(
    db_path: Optional[Union[str, Path]] = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a configured SQLite connection."""
    target = resolve_db_path(db_path)
    conn = sqlite3.connect(str(target), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Union[str, Path]] = None) -> Path:
    """Create all database tables and indexes if they do not exist."""
    target = resolve_db_path(db_path)
    with get_connection(target) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
    return target


def fetch_all(
    query: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None,
) -> List[dict]:
    """Execute a parameterized SELECT query and return list of dicts."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def fetch_one(
    query: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    """Execute a parameterized SELECT query and return a single dict or None."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row is not None else None


def execute_write(
    query: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None,
) -> int:
    """Execute a parameterized INSERT/UPDATE/DELETE statement and return rowcount."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(query, params)
        return cursor.rowcount


def execute_many(
    query: str,
    rows: Iterable[Sequence[Any]],
    db_path: Optional[Union[str, Path]] = None,
) -> int:
    """Execute a parameterized batch write safely inside a single transaction."""
    with get_connection(db_path) as conn:
        cursor = conn.executemany(query, rows)
        return cursor.rowcount
