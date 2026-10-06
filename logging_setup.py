"""Логирование без секретов и лишних персональных данных.

Приложение само не пишет в логи тексты сообщений и данные заявок. Фильтр —
второй рубеж: маскирует ключ API, e-mail и телефоны, если они всё же попадут
в сообщение (например, из текста исключения).
"""

import logging
import re

import config

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d")
_KEY = re.compile(r"(AIza[\w-]{20,}|sk-[\w-]{16,}|Bearer\s+[\w.-]{16,})")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if config.AI_API_KEY:
            msg = msg.replace(config.AI_API_KEY, "[secret]")
        msg = _KEY.sub("[secret]", msg)
        msg = _EMAIL.sub("[email]", msg)
        msg = _PHONE.sub("[phone]", msg)
        record.msg, record.args = msg, None
        return True


def setup() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(config.LOG_LEVEL)
    # httpx/openai логируют URL запросов — не нужно в INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
