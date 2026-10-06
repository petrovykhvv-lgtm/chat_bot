"""Runtime ИИ-консультанта: цикл «модель → tools → ответ».

Работает через OpenAI-совместимый клиент (по умолчанию Google AI Studio).
Без `AI_API_KEY` включается демо-режим без модели: поиск по базе знаний и
черновик заявки из текста вида «Имя: …, контакт: …, задача: …».
"""

import json
import logging
import re
from dataclasses import dataclass

import config
from agent import knowledge, tools
from sessions import Session

log = logging.getLogger(__name__)

_client = None


def is_mock() -> bool:
    return not config.AI_API_KEY


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=config.AI_API_KEY,
            base_url=config.AI_BASE_URL,
            timeout=config.AI_TIMEOUT,
        )
    return _client


@dataclass
class AgentResult:
    text: str
    draft_updated: bool = False


def respond(session: Session, user_text: str) -> AgentResult:
    before = session.draft
    try:
        text = _mock_reply(session, user_text) if is_mock() else _model_reply(session, user_text)
    except Exception as e:  # noqa: BLE001
        # В лог — только тип ошибки: в тексте исключения могут быть ключ или данные.
        log.error("agent failed: %s", type(e).__name__)
        text = "Консультант сейчас недоступен. Попробуйте позже или оставьте обычную заявку."
    session.ai_history += [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": text},
    ]
    del session.ai_history[: -config.MAX_HISTORY_MESSAGES]
    return AgentResult(text, draft_updated=session.draft is not None and session.draft is not before)


def _model_reply(session: Session, user_text: str) -> str:
    system = config.SOUL_PATH.read_text(encoding="utf-8")
    messages = [{"role": "system", "content": system}, *session.ai_history]
    messages.append({"role": "user", "content": user_text})
    client = _get_client()
    for _ in range(config.MAX_TOOL_ITERATIONS):
        resp = client.chat.completions.create(
            model=config.AI_MODEL, messages=messages, tools=tools.TOOL_SCHEMAS
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip() or "Не удалось сформировать ответ."
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments},
                    }
                    for c in msg.tool_calls
                ],
            }
        )
        for c in msg.tool_calls:
            result = tools.execute_tool(session, c.function.name, c.function.arguments)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
    return "Не удалось подготовить ответ. Переформулируйте вопрос."


# --- демо-режим без модели -------------------------------------------------

_FIELD = re.compile(r"(имя|контакт|задача)\s*[:\-—]\s*([^,;\n]+)", re.IGNORECASE)


def _mock_reply(session: Session, user_text: str) -> str:
    if "заявк" in user_text.lower() or _FIELD.search(user_text):
        found = {k.lower(): v.strip() for k, v in _FIELD.findall(user_text)}
        if {"имя", "контакт", "задача"} <= found.keys():
            err = tools.prepare_lead_draft(
                session, found["имя"], found["контакт"], found["задача"]
            )
            return (
                "Подготовил черновик заявки. Проверьте его ниже."
                if session.draft
                else err
            )
        return (
            "Помогу с заявкой. Напишите одним сообщением: "
            "«Имя: …, контакт: …, задача: …»."
        )
    hits = knowledge.search(user_text, limit=1)
    if not hits:
        return "В базе знаний нет ответа на этот вопрос. Оставьте заявку — ответит менеджер."
    sec = hits[0][0]
    return f"{sec.title}\n{sec.body}\n\n(Демо-режим: ответ без модели, по базе знаний.)"
