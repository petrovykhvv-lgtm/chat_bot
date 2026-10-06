"""Проверка ответа модели против базы знаний.

Промпт просит не выдумывать цены, сроки и проценты, но модель может ошибиться.
Здесь вторая линия защиты в коде: суммы в рублях, проценты и сроки из ответа должны
встречаться в `knowledge/` (или в сообщениях самого пользователя). Иначе ответ
блокируется и заменяется безопасным текстом.
"""

import re

from agent import knowledge

BLOCKED_TEXT = (
    "Я не могу подтвердить эти цифры по базе знаний. Точную стоимость и сроки назовёт менеджер: "
    "оставьте заявку, и он свяжется с вами."
)

_NUM = r"\d{1,3}(?:[  ]\d{3})+|\d+"
_MONEY = re.compile(rf"({_NUM})\s*(?:₽|руб)", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|процент)", re.IGNORECASE)
_DURATION = re.compile(
    r"(\d+)(?:\s*[–-]\s*(\d+))?\s*(?:рабоч\w*\s+)?(дн\w*|недел\w*|месяц\w*|час\w*)", re.IGNORECASE
)
_UNIT_PREFIXES = (("дн", "day"), ("нед", "week"), ("мес", "month"), ("час", "hour"))


def _norm(num: str) -> str:
    return re.sub(r"[  ]", "", num).replace(",", ".")


def _unit(word: str) -> str:
    w = word.lower()
    return next((u for prefix, u in _UNIT_PREFIXES if w.startswith(prefix)), w)


def facts(text: str) -> dict[str, set]:
    """Суммы, проценты и сроки, упомянутые в тексте."""
    durations = set()
    for a, b, unit in _DURATION.findall(text):
        for n in (a, b):
            if n:
                durations.add((n, _unit(unit)))
    return {
        "money": {_norm(m) for m in _MONEY.findall(text)},
        "percent": {_norm(m) for m in _PERCENT.findall(text)},
        "duration": durations,
    }


def _knowledge_text() -> str:
    return "\n".join(knowledge.read_file(name) for name in knowledge.list_files())


def violations(reply: str, user_texts: list[str] | None = None) -> list[str]:
    """Что в ответе не подтверждено базой знаний или словами пользователя."""
    known = facts(_knowledge_text())
    said = facts("\n".join(user_texts or []))
    found = facts(reply)
    bad = []
    for kind, label in (("money", "сумма"), ("percent", "процент"), ("duration", "срок")):
        for item in sorted(found[kind] - known[kind] - said[kind], key=str):
            bad.append(f"{label}: {item}")
    return bad
