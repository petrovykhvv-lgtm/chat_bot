"""SQLite: заявки, отзывы, состояние диалогов, счётчики, обслуживание данных."""

import json
import sqlite3
from contextlib import closing

import config

SOURCE_BOT_FLOW = "bot_flow"
SOURCE_AI = "ai_consultant"
STATUS_NEW = "new"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('bot_flow', 'ai_consultant')),
    service        TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL,
    contact        TEXT NOT NULL,
    problem_text   TEXT NOT NULL,
    agent_summary  TEXT NOT NULL DEFAULT '',
    missing_info   TEXT NOT NULL DEFAULT '',
    source_message TEXT NOT NULL DEFAULT '',
    consent_at     TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'new',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    message_text  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS counters (
    key  TEXT PRIMARY KEY,
    n    INTEGER NOT NULL DEFAULT 0
);
"""

# Колонки, добавленные после первых версий: база старой версии дополняется сама.
_LEADS_ADDED = {
    "source_message": "TEXT NOT NULL DEFAULT ''",
    "consent_at": "TEXT NOT NULL DEFAULT ''",
}


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        for name, ddl in _LEADS_ADDED.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {ddl}")


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
    consent_at: str = "",
) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO leads (session_id, source, service, name, contact, problem_text,"
            " agent_summary, missing_info, source_message, consent_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, source, service, name, contact, problem_text,
             agent_summary, missing_info, source_message, consent_at, STATUS_NEW),
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


# --- состояние диалогов (переживает перезапуск) ----------------------------


def save_session(session_id: str, data: dict) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (id, data, updated_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
            " ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (session_id, json.dumps(data, ensure_ascii=False)),
        )


def load_session(session_id: str) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT data FROM sessions WHERE id = ? AND updated_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
            (session_id, f"-{config.SESSION_TTL_HOURS} hours"),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --- счётчики (суточный лимит консультанта) -------------------------------


def incr_counter(key: str) -> int:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO counters (key, n) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET n = n + 1", (key,)
        )
        return int(conn.execute("SELECT n FROM counters WHERE key = ?", (key,)).fetchone()[0])


def get_counter(key: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT n FROM counters WHERE key = ?", (key,)).fetchone()
        return int(row[0]) if row else 0


# --- обслуживание: срок хранения, удаление по запросу --------------------


def purge_expired(retention_days: int | None = None) -> dict[str, int]:
    """Удаляет заявки и отзывы старше срока хранения, устаревшие диалоги и счётчики."""
    days = config.RETENTION_DAYS if retention_days is None else retention_days
    cutoff = f"-{days} days"
    with closing(_connect()) as conn, conn:
        out = {}
        for table in ("leads", "feedback"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)", (cutoff,)
            )
            out[table] = cur.rowcount
        cur = conn.execute(
            "DELETE FROM sessions WHERE updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
            (f"-{config.SESSION_TTL_HOURS} hours",),
        )
        out["sessions"] = cur.rowcount
        conn.execute("DELETE FROM counters WHERE key LIKE 'ai:%' AND key < ?", ("ai:" + _days_ago(7),))
        return out


def _days_ago(n: int) -> str:
    with closing(_connect()) as conn:
        return conn.execute("SELECT date('now', ?)", (f"-{n} days",)).fetchone()[0]


def delete_lead(lead_id: int) -> int:
    with closing(_connect()) as conn, conn:
        return conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,)).rowcount


def delete_by_contact(contact: str) -> int:
    """Удаляет все заявки с точно таким контактом (запрос субъекта на удаление данных)."""
    with closing(_connect()) as conn, conn:
        return conn.execute("DELETE FROM leads WHERE lower(contact) = lower(?)", (contact.strip(),)).rowcount
