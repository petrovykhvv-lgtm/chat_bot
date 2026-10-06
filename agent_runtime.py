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
from agent import guard, knowledge, tools
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


def _guarded(session: Session, user_text: str, reply: str) -> str:
    """Блокирует ответ, если в нём есть цифры, которых нет в базе знаний и в словах пользователя."""
    user_texts = [m["content"] for m in session.ai_history if m["role"] == "user"] + [user_text]
    bad = guard.violations(reply, user_texts)
    if bad:
        log.warning("guard blocked a reply: %d unsupported figure(s)", len(bad))
        return guard.BLOCKED_TEXT
    return reply


def _draft_context(d: dict) -> str:
    return (
        "Текущий черновик заявки (пользователь нажал «Изменить» и хочет что-то поправить). "
        "Возьми его значения, внеси правку пользователя и вызови prepare_lead_draft заново "
        "со ВСЕМИ полями; если правка неясна, задай один уточняющий вопрос.\n"
        f"name={d['name']}; contact={d['contact']}; service={d['service']}; "
        f"problem={d['problem']}; summary={d['summary']}; missing_info={d['missing_info']}"
    )


def respond(session: Session, user_text: str) -> AgentResult:
    before = session.draft
    session.current_text = user_text
    try:
        text = _mock_reply(session, user_text) if is_mock() else _model_reply(session, user_text)
        if not is_mock():
            text = _guarded(session, user_text, text)
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
    messages = [{"role": "system", "content": system}]
    if session.draft and session.draft_editing:
        messages.append({"role": "system", "content": _draft_context(session.draft)})
    messages += session.ai_history
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


_EDIT = re.compile(r"(имя|контакт|задача|услуга)\s*[:\-—]\s*(.+)", re.IGNORECASE)


def _mock_edit(session: Session, user_text: str) -> str | None:
    """Правка одного поля черновика в демо-режиме: «контакт — name@mail.ru»."""
    m = _EDIT.search(user_text)
    if not (session.draft and session.draft_editing and m):
        return None
    d = dict(session.draft)
    key = {"имя": "name", "контакт": "contact", "задача": "problem", "услуга": "service"}[m.group(1).lower()]
    d[key] = m.group(2).strip()
    err = tools.prepare_lead_draft(
        session, d["name"], d["contact"], d["problem"], d["service"], d["summary"], d["missing_info"]
    )
    return "Обновил черновик. Проверьте его ниже." if session.draft is not d and "не создан" not in err else err


def _mock_reply(session: Session, user_text: str) -> str:
    edited = _mock_edit(session, user_text)
    if edited:
        return edited
    if "заявк" in user_text.lower() or _FIELD.search(user_text):
        found = {k.lower(): v.strip() for k, v in _FIELD.findall(user_text)}
        if {"имя", "контакт", "задача"} <= found.keys():
            hits = [h for h, _ in knowledge.search(found["задача"], limit=20) if h.file == "services.md"]
            service = hits[0].title if hits else ""
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
