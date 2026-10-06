"""Уведомление менеджера о новой заявке в Telegram (необязательно).

Включается, если заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID. Сбой отправки не
мешает сохранению заявки. Токен и тексты заявок в лог не пишутся.
"""

import logging
import threading

import config

log = logging.getLogger(__name__)

SOURCE_TITLE = {"bot_flow": "обычная заявка", "ai_consultant": "заявка из ИИ-консультанта"}


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def format_lead(lead_id: int, source: str, lead: dict) -> str:
    lines = [
        f"Новая заявка №{lead_id} ({SOURCE_TITLE.get(source, source)})",
        f"Имя: {lead['name']}",
        f"Контакт: {lead['contact']}",
        f"Услуга: {lead.get('service') or 'не определена'}",
        f"Задача: {lead['problem']}",
    ]
    if lead.get("summary"):
        lines.append(f"Сводка: {lead['summary']}")
    if lead.get("missing_info"):
        lines.append(f"Уточнить: {lead['missing_info']}")
    return "\n".join(lines)


def _send(text: str) -> None:
    import httpx

    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=8,
        )
        if r.status_code != 200:
            log.warning("telegram notify failed: http %s", r.status_code)
    except Exception as e:  # noqa: BLE001 — только тип ошибки: в тексте может быть токен
        log.warning("telegram notify failed: %s", type(e).__name__)


def notify_lead(lead_id: int, source: str, lead: dict, *, background: bool = True) -> None:
    if not enabled():
        return
    text = format_lead(lead_id, source, lead)
    if background:
        threading.Thread(target=_send, args=(text,), daemon=True).start()
    else:
        _send(text)
