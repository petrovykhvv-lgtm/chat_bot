"""SQLite: таблицы `leads` и `feedback`."""

import sqlite3
from contextlib import closing

import config

SOURCE_BOT_FLOW = "bot_flow"
SOURCE_AI = "ai_consultant"
STATUS_NEW = "new"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('bot_flow', 'ai_consultant')),
    service       TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    contact       TEXT NOT NULL,
    problem_text  TEXT NOT NULL,
    agent_summary TEXT NOT NULL DEFAULT '',
    missing_info  TEXT NOT NULL DEFAULT '',
    source_message TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    message_text  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        if "source_message" not in cols:  # база создана до фазы 4
            conn.execute("ALTER TABLE leads ADD COLUMN source_message TEXT NOT NULL DEFAULT ''")


def save_lead(
    *,
    session_id: str,
    source: str,
    name: str,
    contact: str,
    problem_text: str,
    service: str = "",
    agent_summary: str = "",
    missing_info: str = "",
    source_message: str = "",
) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO leads (session_id, source, service, name, contact, problem_text,"
            " agent_summary, missing_info, source_message, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, source, service, name, contact, problem_text,
             agent_summary, missing_info, source_message, STATUS_NEW),
        )
        return int(cur.lastrowid)


def save_feedback(session_id: str, message_text: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO feedback (session_id, message_text) VALUES (?, ?)",
            (session_id, message_text),
        )
        return int(cur.lastrowid)


def counts_by_kind() -> dict[str, int]:
    """Сколько записей каждого вида: обычные заявки, заявки ИИ, обратная связь."""
    with closing(_connect()) as conn:
        out = {SOURCE_BOT_FLOW: 0, SOURCE_AI: 0}
        for r in conn.execute("SELECT source, COUNT(*) AS n FROM leads GROUP BY source"):
            out[r["source"]] = r["n"]
        out["feedback"] = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        return out


def fetch_all(table: str) -> list[sqlite3.Row]:
    if table not in {"leads", "feedback"}:
        raise ValueError("unknown table")
    with closing(_connect()) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
