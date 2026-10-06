"""Сессии чата в памяти процесса (один runtime — один процесс)."""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

LEAD_HELP_TEXT = "Помоги мне оформить заявку."  # текст кнопки «Помоги с заявкой»
MAX_SESSIONS = 500
TTL_SECONDS = 6 * 3600


@dataclass
class Session:
    id: str
    state: str = "menu"
    form: dict = field(default_factory=dict)  # поля обычной заявки
    ai_history: list[dict] = field(default_factory=list)
    draft: dict | None = None  # черновик заявки ИИ-консультанта
    draft_editing: bool = False  # пользователь нажал «Изменить», ждём правку
    current_text: str = ""  # сообщение пользователя, обрабатываемое сейчас
    user_confirmed: bool = False  # выставляется только обработчиком кнопки
    last_seen: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        self.state = "menu"
        self.form = {}
        self.ai_history = []
        self.draft = None
        self.draft_editing = False
        self.current_text = ""
        self.user_confirmed = False


class SessionStore:
    def __init__(self) -> None:
        self._items: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        now = time.time()
        with self._lock:
            for sid in [k for k, v in self._items.items() if now - v.last_seen > TTL_SECONDS]:
                del self._items[sid]
            s = self._items.get(session_id)
            if s is None:
                s = self._items[session_id] = Session(session_id)
                while len(self._items) > MAX_SESSIONS:
                    self._items.popitem(last=False)
            s.last_seen = now
            self._items.move_to_end(session_id)
            return s


store = SessionStore()
