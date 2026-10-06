"""Обслуживание данных: срок хранения и удаление по запросу субъекта.

Запуск (из корня проекта):
  python scripts/manage_data.py purge [--days N]     удалить заявки и отзывы старше срока (по умолчанию RETENTION_DAYS)
  python scripts/manage_data.py delete-lead ID       удалить заявку по номеру
  python scripts/manage_data.py delete-contact X     удалить все заявки с таким контактом (запрос на удаление данных)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    purge = sub.add_parser("purge")
    purge.add_argument("--days", type=int, default=config.RETENTION_DAYS)
    sub.add_parser("delete-lead").add_argument("lead_id", type=int)
    sub.add_parser("delete-contact").add_argument("contact")
    args = parser.parse_args(argv)

    db.init_db()
    if args.cmd == "purge":
        print("удалено:", db.purge_expired(args.days))
    elif args.cmd == "delete-lead":
        print("удалено заявок:", db.delete_lead(args.lead_id))
    else:
        print("удалено заявок:", db.delete_by_contact(args.contact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
