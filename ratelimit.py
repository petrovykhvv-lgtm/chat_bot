"""Ограничение частоты запросов: защита от перегрузки и от расхода квоты модели."""

import threading
import time
from collections import defaultdict, deque

import config
import db


class SlidingWindow:
    """Не более `limit` событий за `window` секунд на ключ (IP, сессия)."""

    def __init__(self, window: float = 60.0) -> None:
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # не даём словарю расти бесконечно
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


ip_window = SlidingWindow()
ai_window = SlidingWindow()

MINUTE_LIMITED = "Вы пишете слишком часто. Подождите минуту и задайте вопрос снова."
DAY_LIMITED = (
    "На сегодня лимит вопросов консультанту исчерпан. Услуги, FAQ и заявка работают как обычно, "
    "а консультант снова будет доступен завтра."
)


def allow_ip(ip: str) -> bool:
    return ip_window.allow(ip, config.RATE_LIMIT_PER_MIN)


def check_ai(session_id: str, *, counts_toward_daily: bool) -> str | None:
    """Возвращает текст отказа, если консультанту нельзя отвечать сейчас, иначе None."""
    if not ai_window.allow(session_id, config.AI_RATE_PER_MIN):
        return MINUTE_LIMITED
    if counts_toward_daily:
        day = time.strftime("ai:%Y-%m-%d", time.gmtime())
        if db.get_counter(day) >= config.AI_DAILY_LIMIT:
            return DAY_LIMITED
        db.incr_counter(day)
    return None
