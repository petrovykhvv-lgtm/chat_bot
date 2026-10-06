"""Проверка полей заявки. Общая для bot-flow и tools ИИ-консультанта."""

import re

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_TG = re.compile(r"^@[A-Za-z0-9_]{4,32}$")


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def check_name(value: object) -> tuple[str | None, str | None]:
    """Возвращает (значение, ошибка)."""
    v = clean(value)
    if not 2 <= len(v) <= 80:
        return None, "Имя должно быть от 2 до 80 символов."
    return v, None


def check_contact(value: object) -> tuple[str | None, str | None]:
    v = clean(value)
    digits = re.sub(r"\D", "", v)
    is_phone = bool(re.fullmatch(r"\+?[\d\s().-]{7,25}", v)) and 7 <= len(digits) <= 15
    if not (_EMAIL.match(v) or _TG.match(v) or is_phone):
        return None, "Укажите e-mail, телефон или Telegram (например, @nickname)."
    return v, None


def check_description(value: object) -> tuple[str | None, str | None]:
    v = clean(value)
    if not 5 <= len(v) <= 1000:
        return None, "Опишите задачу в 5–1000 символах."
    return v, None
