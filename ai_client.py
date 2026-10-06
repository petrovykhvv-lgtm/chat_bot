"""Подключение к AI Studio API (OpenAI-совместимый эндпоинт).

Ключ, адрес и модель берутся только из окружения (`.env`), в коде секретов нет.
"""

import logging

import config

log = logging.getLogger(__name__)

_client = None


def is_configured() -> bool:
    return bool(config.AI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=config.AI_API_KEY,
            base_url=config.AI_BASE_URL,
            timeout=config.AI_TIMEOUT,
            max_retries=3,  # временные 429/503 у провайдера
        )
    return _client


def chat(messages: list[dict], tools: list[dict] | None = None):
    """Один запрос к модели. В лог пишется только расход токенов, без текстов."""
    kwargs = {"model": config.AI_MODEL, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    resp = _get_client().chat.completions.create(**kwargs)
    usage = getattr(resp, "usage", None)
    if usage:
        log.info(
            "llm request ok: model=%s prompt_tokens=%s completion_tokens=%s",
            config.AI_MODEL,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
    return resp
