"""Runtime ИИ-консультанта: цикл «модель → tools → ответ».

Работает через OpenAI-совместимый клиент (по умолчанию Google AI Studio).
Без `AI_API_KEY` включается демо-режим без модели: поиск по базе знаний и
черновик заявки из текста вида «Имя: …, контакт: …, задача: …».
"""

import logging
import re
from dataclasses import dataclass

import ai_client
import config
from agent import knowledge, tools
from sessions import Session

log = logging.getLogger(__name__)

UNAVAILABLE = "Консультант сейчас недоступен. Попробуйте позже или оставьте обычную заявку."
RATE_LIMITED = (
    "Лимит запросов к ИИ-консультанту исчерпан. Попробуйте позже "
    "или воспользуйтесь меню: услуги, FAQ и заявка работают без него."
)


def is_mock() -> bool:
    return not ai_client.is_configured()


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
        text = RATE_LIMITED if type(e).__name__ == "RateLimitError" else UNAVAILABLE
    session.ai_history += [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": text},
    ]
    del session.ai_history[: -config.MAX_HISTORY_MESSAGES]
    return AgentResult(text, draft_updated=session.draft is not None and session.draft is not before)


def _tool_call_dict(call) -> dict:
    d = {
        "id": call.id,
        "type": "function",
        "function": {"name": call.function.name, "arguments": call.function.arguments},
    }
    # Gemini 3 требует вернуть thought_signature вместе с вызовом tool.
    extra = (call.model_extra or {}).get("extra_content")
    if extra:
        d["extra_content"] = extra
    return d


def _model_reply(session: Session, user_text: str) -> str:
    system = config.SOUL_PATH.read_text(encoding="utf-8")
    messages = [{"role": "system", "content": system}, *session.ai_history]
    messages.append({"role": "user", "content": user_text})
    for _ in range(config.MAX_TOOL_ITERATIONS):
        resp = ai_client.chat(messages, tools.TOOL_SCHEMAS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip() or "Не удалось сформировать ответ."
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [_tool_call_dict(c) for c in msg.tool_calls],
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
            hits = knowledge.search(found["задача"], limit=1)
            service = hits[0][0].title if hits and hits[0][0].file == "services.md" else ""
            err = tools.prepare_lead_draft(
                session, found["имя"], found["контакт"], found["задача"], service,
                summary="Демо-режим: сводка модели недоступна.",
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
