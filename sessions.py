"""Сессии чата: в памяти процесса, с копией в SQLite (переживают перезапуск)."""

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import config
import db

log = logging.getLogger(__name__)

LEAD_HELP_TEXT = "Помоги мне оформить заявку."  # текст кнопки «Помоги с заявкой»
MAX_SESSIONS = 500

# Поля, которые сохраняются в БД, и допустимые типы. Флаг подтверждения (user_confirmed)
# намеренно не сохраняется: подтвердить заявку можно только свежим нажатием кнопки.
_TYPES = {
    "state": str,
    "form": dict,
    "ai_history": list,
    "draft": (dict, type(None)),
    "draft_editing": bool,
    "consent_at": str,
    "pending_action": str,
}
_PERSISTED = tuple(_TYPES)


@dataclass
class Session:
    id: str
    state: str = "menu"
    form: dict = field(default_factory=dict)  # поля обычной заявки
    ai_history: list[dict] = field(default_factory=list)
    draft: dict | None = None  # черновик заявки ИИ-консультанта
    draft_editing: bool = False  # пользователь нажал «Изменить», ждём правку
    consent_at: str = ""  # когда пользователь согласился на обработку данных
    pending_action: str = ""  # действие, отложенное до получения согласия
    current_text: str = ""  # сообщение пользователя, обрабатываемое сейчас
    user_confirmed: bool = False  # выставляется только обработчиком кнопки
    last_seen: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        """Сброс диалога. Согласие на обработку данных сохраняется."""
        self.state = "menu"
        self.form = {}
        self.ai_history = []
        self.draft = None
        self.draft_editing = False
        self.pending_action = ""
        self.current_text = ""
        self.user_confirmed = False

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in _PERSISTED}

    @classmethod
    def from_dict(cls, session_id: str, data: dict) -> "Session":
        """Восстанавливает сессию; поля неверного типа (повреждённая запись) пропускаются."""
        s = cls(session_id)
        for key, types in _TYPES.items():
            if key in data and isinstance(data[key], types):
                setattr(s, key, data[key])
        return s


class SessionStore:
    def __init__(self) -> None:
        self._items: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        now = time.time()
        ttl = config.SESSION_TTL_HOURS * 3600
        with self._lock:
            for sid in [k for k, v in self._items.items() if now - v.last_seen > ttl]:
                del self._items[sid]
            s = self._items.get(session_id)
            if s is None:
                s = self._restore(session_id) or Session(session_id)
                self._items[session_id] = s
                while len(self._items) > MAX_SESSIONS:
                    self._items.popitem(last=False)
            s.last_seen = now
            self._items.move_to_end(session_id)
            return s

    @staticmethod
    def _restore(session_id: str) -> Session | None:
        try:
            data = db.load_session(session_id)
        except Exception as e:  # noqa: BLE001 — БД недоступна: работаем из памяти
            log.warning("session restore failed: %s", type(e).__name__)
            return None
        return Session.from_dict(session_id, data) if data else None

    @staticmethod
    def save(session: Session) -> None:
        try:
            db.save_session(session.id, session.to_dict())
        except Exception as e:  # noqa: BLE001
            log.warning("session save failed: %s", type(e).__name__)


store = SessionStore()
