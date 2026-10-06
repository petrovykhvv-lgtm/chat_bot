"""Печатает содержимое таблиц leads и feedback (для проверки сохранения).

Запуск: python scripts/show_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

db.init_db()
for table in ("leads", "feedback"):
    rows = db.fetch_all(table)
    print(f"== {table} ({len(rows)}) ==")
    for r in rows:
        print(" | ".join(f"{k}={r[k]}" for k in r.keys()))

counts = db.counts_by_kind()
print("== по видам ==")
print(f"обычные заявки (bot_flow): {counts[db.SOURCE_BOT_FLOW]}")
print(f"заявки ИИ-консультанта (ai_consultant): {counts[db.SOURCE_AI]}")
print(f"обратная связь (таблица feedback): {counts['feedback']}")
