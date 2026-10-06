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
from sessions import Session

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


def prepare_lead_draft(session: Session, name: str, contact: str, description: str) -> str:
    """Готовит черновик. Ничего не сохраняет в БД."""
    fields, errors = {}, []
    for key, check, value in (
        ("name", validation.check_name, name),
        ("contact", validation.check_contact, contact),
        ("description", validation.check_description, description),
    ):
        ok, err = check(value)
        if err:
            errors.append(f"{key}: {err}")
        else:
            fields[key] = ok
    if errors:
        return "Черновик не создан. Уточните у пользователя: " + " ".join(errors)
    session.draft = fields
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
    lead_id = db.save_lead(db.SOURCE_AI, d["name"], d["contact"], d["description"])
    session.draft = None
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
                    "description": {"type": "string"},
                },
                "required": ["name", "contact", "description"],
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
                session, args.get("name"), args.get("contact"), args.get("description")
            )
    except Exception as e:  # noqa: BLE001 — не роняем диалог из-за tool
        log.warning("tool %s failed: %s", name, type(e).__name__)
        return "Ошибка выполнения инструмента."
    log.warning("model requested unknown tool: %s", name[:40])
    return "Ошибка: неизвестный инструмент."
