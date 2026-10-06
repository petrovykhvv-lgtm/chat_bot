"""Tools ИИ-консультанта.

Модели доступны три: `search_knowledge`, `read_knowledge_file`,
`prepare_lead_draft`. `save_confirmed_lead` модели не выдаётся: его вызывает
runtime после нажатия пользователем кнопки «Отправить заявку».
"""

import json
import logging

import db
import validation
from agent import knowledge
from sessions import LEAD_HELP_TEXT, Session

log = logging.getLogger(__name__)


def search_knowledge(query: str) -> str:
    hits = knowledge.search(str(query)[:200])
    if not hits:
        return "Ничего не найдено в базе знаний."
    return "\n\n".join(f"[{s.file} / {s.title}]\n{s.body}" for s, _ in hits)


def read_knowledge_file(filename: str) -> str:
    try:
        return knowledge.read_file(filename)
    except knowledge.KnowledgeAccessError as e:
        files = ", ".join(knowledge.list_files())
        return f"Ошибка: {e} Доступные файлы: {files}"


def _service_or_empty(value: object) -> str:
    """Приводит услугу к названию из каталога; неизвестное — пустая строка."""
    v = validation.clean(value).lower()
    for title in knowledge.service_titles():
        if v == title.lower():
            return title
    return ""


def _source_message(session: Session) -> str:
    """Исходный запрос: до трёх последних содержательных реплик пользователя.

    При правке черновика сохраняется исходный запрос первого черновика.
    """
    if session.draft and session.draft.get("source_message"):
        return session.draft["source_message"]
    texts = [m["content"] for m in session.ai_history if m["role"] == "user"]
    texts.append(session.current_text)
    texts = [t for t in texts if t and t != LEAD_HELP_TEXT]
    return " / ".join(texts[-3:])[:400]


def prepare_lead_draft(
    session: Session,
    name: str,
    contact: str,
    problem: str,
    service: str = "",
    summary: str = "",
    missing_info: str = "",
) -> str:
    """Готовит черновик. Ничего не сохраняет в БД."""
    fields, errors = {}, []
    for key, check, value in (
        ("name", validation.check_name, name),
        ("contact", validation.check_contact, contact),
        ("problem", validation.check_description, problem),
    ):
        ok, err = check(value)
        if err:
            errors.append(f"{key}: {err}")
        else:
            fields[key] = ok
    if errors:
        return "Черновик не создан. Уточните у пользователя: " + " ".join(errors)
    fields["service"] = _service_or_empty(service)
    fields["summary"] = validation.clean(summary)[:500]
    fields["missing_info"] = validation.clean(missing_info)[:300]
    fields["source_message"] = _source_message(session)
    session.draft = fields
    session.draft_editing = False
    session.user_confirmed = False
    return (
        "Черновик подготовлен и показан пользователю с кнопками подтверждения. "
        "Заявка ещё НЕ сохранена; сохранить её может только пользователь."
    )


def save_confirmed_lead(session: Session) -> int | None:
    """Сохраняет черновик, только если пользователь подтвердил его кнопкой."""
    if not session.draft or not session.user_confirmed:
        return None
    d = session.draft
    lead_id = db.save_lead(
        session_id=session.id,
        source=db.SOURCE_AI,
        name=d["name"],
        contact=d["contact"],
        problem_text=d["problem"],
        service=d["service"],
        agent_summary=d["summary"],
        missing_info=d["missing_info"],
        source_message=d["source_message"],
    )
    session.draft = None
    session.draft_editing = False
    session.user_confirmed = False
    log.info("lead saved id=%s source=%s", lead_id, db.SOURCE_AI)
    return lead_id


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Поиск по базе знаний компании (услуги, FAQ, правила).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Поисковый запрос"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_knowledge_file",
            "description": "Прочитать файл базы знаний целиком (например, services.md).",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_lead_draft",
            "description": (
                "Подготовить черновик заявки. Вызывать, когда известны имя, контакт и задача. "
                "Только готовит черновик: сохраняет пользователь кнопкой."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "contact": {"type": "string", "description": "E-mail, телефон или @telegram"},
                    "problem": {"type": "string", "description": "Задача пользователя его словами"},
                    "service": {
                        "type": "string",
                        "description": "Название услуги из services.md или пустая строка, если не ясно",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Сводка для менеджера в 1–2 предложения",
                    },
                    "missing_info": {
                        "type": "string",
                        "description": "Что менеджеру ещё нужно уточнить (бюджет, сроки…), либо пусто",
                    },
                },
                "required": ["name", "contact", "problem"],
            },
        },
    },
]


def execute_tool(session: Session, name: str, raw_args: str) -> str:
    """Выполняет tool по запросу модели. Неизвестные tools отклоняются."""
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return "Ошибка: аргументы должны быть JSON-объектом."
    try:
        if name == "search_knowledge":
            return search_knowledge(args.get("query", ""))
        if name == "read_knowledge_file":
            return read_knowledge_file(args.get("filename", ""))
        if name == "prepare_lead_draft":
            return prepare_lead_draft(
                session,
                args.get("name"),
                args.get("contact"),
                args.get("problem"),
                args.get("service", ""),
                args.get("summary", ""),
                args.get("missing_info", ""),
            )
    except Exception as e:  # noqa: BLE001 — не роняем диалог из-за tool
        log.warning("tool %s failed: %s", name, type(e).__name__)
        return "Ошибка выполнения инструмента."
    log.warning("model requested unknown tool: %s", name[:40])
    return "Ошибка: неизвестный инструмент."
