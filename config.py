"""Настройки приложения. Читаются из переменных окружения и `.env`."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _path(env_name: str, default: str) -> Path:
    p = Path(os.getenv(env_name, default))
    return p if p.is_absolute() else BASE_DIR / p


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DB_PATH = _path("DB_PATH", "data/bot.sqlite3")
KNOWLEDGE_DIR = _path("KNOWLEDGE_DIR", "knowledge")
SOUL_PATH = BASE_DIR / "agent" / "soul.md"

# OpenAI-совместимый клиент. По умолчанию — Google AI Studio (Gemini).
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv(
    "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
)
AI_MODEL = os.getenv("AI_MODEL", "gemini-3.5-flash-lite")
AI_PROJECT = os.getenv("AI_PROJECT", "").strip() or None  # для Yandex AI Studio: ID каталога
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "30"))

MAX_MESSAGE_LEN = 2000
MAX_TOOL_ITERATIONS = 5
MAX_HISTORY_MESSAGES = 20

# Защита от перегрузки и расхода квоты модели.
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))  # запросов с одного IP
AI_RATE_PER_MIN = int(os.getenv("AI_RATE_PER_MIN", "10"))  # вопросов консультанту с одной сессии
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "300"))  # вопросов консультанту в сутки на весь сервер

# Хранение данных.
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "365"))  # заявки и отзывы
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "6"))  # состояние диалогов
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))  # сколько копий базы хранить

# Уведомление менеджера о новой заявке в Telegram (необязательно).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
