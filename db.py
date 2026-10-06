"""SQLite: таблицы `leads` и `feedback`."""

import sqlite3
from contextlib import closing

import config

SOURCE_BOT_FLOW = "bot_flow"
SOURCE_AI = "ai_consultant"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL CHECK (source IN ('bot_flow', 'ai_consultant')),
    name        TEXT NOT NULL,
    contact     TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)


def save_lead(source: str, name: str, contact: str, description: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO leads (source, name, contact, description) VALUES (?, ?, ?, ?)",
            (source, name, contact, description),
        )
        return int(cur.lastrowid)


def save_feedback(message: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("INSERT INTO feedback (message) VALUES (?)", (message,))
        return int(cur.lastrowid)


def fetch_all(table: str) -> list[sqlite3.Row]:
    if table not in {"leads", "feedback"}:
        raise ValueError("unknown table")
    with closing(_connect()) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
