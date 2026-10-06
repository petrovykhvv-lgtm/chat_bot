"""Резервная копия SQLite в data/backups/ с ограничением числа копий.

Использует встроенный механизм резервного копирования SQLite: копия согласована
даже при работающем сервере. Права на копии 600.

Запуск (из корня проекта): python scripts/backup_db.py
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def backup(keep: int | None = None) -> Path:
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"нет базы данных: {config.DB_PATH}")
    keep = config.BACKUP_KEEP if keep is None else keep
    dest_dir = config.DB_PATH.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    dest = dest_dir / time.strftime("bot-%Y%m%d-%H%M%S.sqlite3", time.gmtime())

    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.chmod(dest, 0o600)

    old = sorted(dest_dir.glob("bot-*.sqlite3"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return dest


if __name__ == "__main__":
    try:
        path = backup()
    except FileNotFoundError as e:
        sys.exit(str(e))
    print(f"копия сохранена: {path}")
