"""Предсказуемые сценарии чата без обращения к модели + маршрутизация в ИИ-режим.

`handle()` принимает текст или действие (кнопку) и возвращает ответ вида
{"messages": [...], "buttons": [{"label", "action"}], "placeholder": str}.
Меню, услуги, FAQ, обычная заявка и обратная связь не обращаются к модели:
они работают и без ключа API, и при исчерпанном лимите провайдера.
"""

import logging

import agent_runtime
import db
import validation
from agent import knowledge, tools
from sessions import LEAD_HELP_TEXT, Session

log = logging.getLogger(__name__)

MENU_BUTTONS = [
    ("Услуги", "services"),
    ("FAQ", "faq"),
    ("Оставить заявку", "lead_start"),
    ("ИИ-консультант", "ai_start"),
    ("Обратная связь", "feedback_start"),
]
CONFIRM_BUTTONS = [("Отправить заявку", "confirm"), ("Изменить", "edit"), ("Отмена", "cancel")]
BACK = ("В меню", "menu")
CANCEL = [("Отмена", "menu")]
NO_SERVICE = "Пока не знаю"

SOURCE_LABEL = {db.SOURCE_BOT_FLOW: "обычная заявка", db.SOURCE_AI: "заявка из ИИ-консультанта"}


def _reply(messages, buttons=(), placeholder="Напишите сообщение…"):
    if isinstance(messages, str):
        messages = [messages]
    return {
        "messages": messages,
        "buttons": [{"label": l, "action": a} for l, a in buttons],
        "placeholder": placeholder,
    }


def _menu(prefix: str | None = None):
    hint = "Выберите раздел в меню или напишите «меню»."
    head = prefix or "Здравствуйте! Я помощник рекламного агентства «Вектор»."
    return _reply([head, hint], MENU_BUTTONS)


def _draft_text(d: dict) -> str:
    """Структурированный черновик. Собирает runtime из проверенных полей, не модель."""
    if "summary" not in d:  # обычная заявка: короткая форма
        lines = ["Черновик заявки:", f"Имя: {d['name']}", f"Контакт: {d['contact']}"]
        if d.get("service"):
            lines.append(f"Услуга: {d['service']}")
        lines.append(f"Задача: {d['problem']}")
        return "\n".join(lines)
    lines = [
        "Черновик заявки",
        f"Услуга: {d['service'] or 'не определена'}",
        f"Задача: {d['problem']}",
        f"Контакт: {d['name']}, {d['contact']}",
        f"Что известно: {d['summary'] or '—'}",
        f"Чего не хватает: {d['missing_info'] or 'менеджер ничего не уточняет'}",
    ]
    if d.get("source_message"):
        lines.append(f"Исходный запрос: «{d['source_message']}»")
    return "\n".join(lines)


def handle(s: Session, text: str | None, action: str | None):
    with s.lock:
        if action:
            return _on_action(s, action)
        text = (text or "").strip()
        if not text:
            return _menu()
        return _on_text(s, text)


# --- кнопки ---------------------------------------------------------------


def _on_action(s: Session, action: str):
    if action in ("start", "menu"):
        s.reset()
        return _menu()
    if action == "services":
        s.reset()
        secs = knowledge.load_sections("services.md")
        body = "\n\n".join(f"{x.title}\n{x.body}" for x in secs) or "Раздел пока пуст."
        return _reply(["Наши услуги:", body], [("Оставить заявку", "lead_start"), BACK])
    if action == "faq":
        s.reset()
        cats = list(knowledge.faq_categories())
        buttons = [(c, f"faqc:{i}") for i, c in enumerate(cats)] + [BACK]
        return _reply("Частые вопросы. Выберите тему:", buttons)
    if action.startswith("faqc:"):
        return _faq_category(action[5:])
    if action.startswith("faq:"):
        return _faq_answer(action[4:])
    if action == "lead_start":
        s.reset()
        s.state = "lead_service"
        titles = knowledge.service_titles()
        buttons = [(t, f"svc:{i}") for i, t in enumerate(titles)] + [(NO_SERVICE, "svc:none"), BACK]
        return _reply("Оформим заявку. Какая услуга вас интересует?", buttons, "Или напишите название услуги")
    if action.startswith("svc:") and s.state == "lead_service":
        return _pick_service(s, action[4:])
    if action == "feedback_start":
        s.reset()
        s.state = "feedback"
        return _reply("Напишите отзыв или пожелание — одним сообщением.", CANCEL, "Ваш отзыв")
    if action == "ai_start":
        s.reset()
        s.state = "ai"
        note = " (демо-режим без модели)" if agent_runtime.is_mock() else ""
        return _reply(
            f"ИИ-консультант на связи{note}. Спросите об услугах, ценах, сроках или правилах работы.",
            [("Помоги с заявкой", "ai_lead_help"), BACK],
            "Ваш вопрос консультанту",
        )
    if action == "ai_lead_help":
        if s.state != "ai":
            return _menu()
        return _ai_turn(s, LEAD_HELP_TEXT)
    if action in ("confirm", "edit", "cancel"):
        return _on_confirm_action(s, action)
    return _menu()


def _faq_category(raw: str):
    cats = knowledge.faq_categories()
    names = list(cats)
    try:
        name = names[int(raw)]
    except (ValueError, IndexError):
        return _menu()
    base = sum(len(cats[n]) for n in names[: names.index(name)])
    buttons = [(q.title, f"faq:{base + i}") for i, q in enumerate(cats[name])]
    return _reply(f"{name}. Выберите вопрос:", buttons + [("Другие темы", "faq"), BACK])


def _faq_answer(raw: str):
    flat = [q for qs in knowledge.faq_categories().values() for q in qs]
    try:
        sec = flat[int(raw)]
    except (ValueError, IndexError):
        return _menu()
    buttons = [("Другие темы", "faq"), ("Оставить заявку", "lead_start"), BACK]
    return _reply([sec.title, sec.body], buttons)


def _pick_service(s: Session, raw: str):
    titles = knowledge.service_titles()
    if raw == "none":
        s.form["service"] = ""
    else:
        try:
            s.form["service"] = titles[int(raw)]
        except (ValueError, IndexError):
            return _menu()
    s.state = "lead_name"
    return _reply("Как вас зовут?", CANCEL, "Ваше имя")


def _on_confirm_action(s: Session, action: str):
    if s.state == "lead_review":
        return _lead_review_action(s, action)
    if s.state == "ai" and s.draft:
        return _draft_action(s, action)
    return _menu("Сейчас нечего подтверждать.")


def _lead_review_action(s: Session, action: str):
    if action == "confirm":
        f = s.form
        lead_id = db.save_lead(
            session_id=s.id,
            source=db.SOURCE_BOT_FLOW,
            name=f["name"],
            contact=f["contact"],
            problem_text=f["problem"],
            service=f.get("service", ""),
        )
        log.info("lead saved id=%s source=%s", lead_id, db.SOURCE_BOT_FLOW)
        s.reset()
        return _reply(
            f"Заявка №{lead_id} отправлена ({SOURCE_LABEL[db.SOURCE_BOT_FLOW]}). Менеджер свяжется с вами.",
            MENU_BUTTONS,
        )
    if action == "edit":
        s.state = "lead_name"
        return _reply("Хорошо, введём данные заново. Как вас зовут?", CANCEL, "Ваше имя")
    s.reset()
    return _menu("Заявка отменена.")


def _draft_action(s: Session, action: str):
    if action == "confirm":
        if s.draft_editing:
            return _reply(
                "Вы нажали «Изменить»: напишите правку, и я обновлю черновик, либо нажмите «Отмена».",
                [("Отмена", "cancel"), BACK],
                "Что изменить?",
            )
        s.user_confirmed = True  # единственное место, где выставляется подтверждение
        lead_id = tools.save_confirmed_lead(s)
        if lead_id is None:
            return _menu("Не удалось сохранить заявку.")
        return _reply(
            f"Заявка №{lead_id} отправлена ({SOURCE_LABEL[db.SOURCE_AI]}). Менеджер свяжется с вами.",
            [("Задать вопрос", "ai_start"), BACK],
        )
    if action == "edit":
        s.draft_editing = True
        return _reply(
            "Что нужно изменить? Например: «контакт — name@mail.ru» или «услуга — SMM-продвижение». "
            "Подтвердить заявку можно будет после обновления черновика.",
            [("Отмена", "cancel"), BACK],
            "Что изменить?",
        )
    s.draft = None
    s.draft_editing = False
    return _reply(
        "Черновик отменён. Можете задать другой вопрос.",
        [("Помоги с заявкой", "ai_lead_help"), BACK],
    )


# --- текст ----------------------------------------------------------------


def _on_text(s: Session, text: str):
    if text.lower() in ("меню", "/menu", "/start"):
        return _on_action(s, "menu")
    st = s.state
    if st == "lead_service":
        low = text.lower()
        titles = knowledge.service_titles()
        for i, t in enumerate(titles):
            if low == t.lower():
                return _pick_service(s, str(i))
        return _reply(
            "Выберите услугу кнопкой ниже или напишите её название точно.",
            [(t, f"svc:{i}") for i, t in enumerate(titles)] + [(NO_SERVICE, "svc:none"), BACK],
            "Или напишите название услуги",
        )
    if st == "lead_name":
        value, err = validation.check_name(text)
        if err:
            return _reply(err, CANCEL, "Ваше имя")
        s.form["name"], s.state = value, "lead_contact"
        return _reply("Как с вами связаться? E-mail, телефон или Telegram.", CANCEL, "Контакт")
    if st == "lead_contact":
        value, err = validation.check_contact(text)
        if err:
            return _reply(err, CANCEL, "Контакт")
        s.form["contact"], s.state = value, "lead_desc"
        return _reply("Коротко опишите задачу.", CANCEL, "Описание задачи")
    if st == "lead_desc":
        value, err = validation.check_description(text)
        if err:
            return _reply(err, CANCEL, "Описание задачи")
        s.form["problem"], s.state = value, "lead_review"
        return _reply([_draft_text(s.form), "Отправить заявку?"], CONFIRM_BUTTONS)
    if st == "lead_review":
        return _reply("Выберите действие кнопкой ниже.", CONFIRM_BUTTONS)
    if st == "feedback":
        msg = validation.clean(text)
        if len(msg) < 3:
            return _reply("Слишком коротко. Напишите отзыв подробнее.", CANCEL, "Ваш отзыв")
        fb_id = db.save_feedback(s.id, msg)
        log.info("feedback saved id=%s", fb_id)
        s.reset()
        return _reply(f"Спасибо! Отзыв №{fb_id} сохранён.", MENU_BUTTONS)
    if st == "ai":
        return _ai_turn(s, text)
    return _menu("Не понял. Выберите пункт меню.")


def _ai_turn(s: Session, text: str):
    result = agent_runtime.respond(s, text)
    messages = [result.text]
    if result.draft_updated and s.draft:
        messages.append(_draft_text(s.draft))
        return _reply(messages, CONFIRM_BUTTONS, "Продолжить диалог…")
    if s.draft and s.draft_editing:  # правка не дала нового черновика: продолжаем уточнение
        return _reply(messages, [("Отмена", "cancel"), BACK], "Что изменить?")
    return _reply(messages, [("Помоги с заявкой", "ai_lead_help"), BACK], "Ваш вопрос консультанту")
