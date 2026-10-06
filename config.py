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
AI_MODEL = os.getenv("AI_MODEL", "gemini-3.6-flash")
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "30"))

MAX_MESSAGE_LEN = 2000
MAX_TOOL_ITERATIONS = 5
MAX_HISTORY_MESSAGES = 20
