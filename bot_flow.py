"""Предсказуемые сценарии чата без обращения к модели + маршрутизация в ИИ-режим.

`handle()` принимает текст или действие (кнопку) и возвращает ответ вида
{"messages": [...], "buttons": [{"label", "action"}], "placeholder": str}.
"""

import logging

import agent_runtime
import db
import validation
from agent import knowledge, tools
from sessions import Session

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
    text = "Выберите раздел в меню или напишите «меню»."
    return _reply([prefix, text] if prefix else ["Здравствуйте! Я помощник рекламного агентства «Вектор».", text], MENU_BUTTONS)


def _draft_text(d: dict) -> str:
    return (
        "Черновик заявки:\n"
        f"Имя: {d['name']}\nКонтакт: {d['contact']}\nЗадача: {d['description']}"
    )


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
        secs = knowledge.load_sections("faq.md")
        buttons = [(x.title, f"faq:{i}") for i, x in enumerate(secs)] + [BACK]
        return _reply("Частые вопросы. Выберите вопрос:", buttons)
    if action.startswith("faq:"):
        secs = knowledge.load_sections("faq.md")
        try:
            sec = secs[int(action[4:])]
        except (ValueError, IndexError):
            return _menu()
        buttons = [("Другие вопросы", "faq"), ("Оставить заявку", "lead_start"), BACK]
        return _reply([sec.title, sec.body], buttons)
    if action == "lead_start":
        s.reset()
        s.state = "lead_name"
        return _reply("Оформим заявку. Как вас зовут?", [("Отмена", "menu")], "Ваше имя")
    if action == "feedback_start":
        s.reset()
        s.state = "feedback"
        return _reply("Напишите отзыв или пожелание — одним сообщением.", [("Отмена", "menu")], "Ваш отзыв")
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
        return _ai_turn(s, "Помоги мне оформить заявку.")
    if action in ("confirm", "edit", "cancel"):
        return _on_confirm_action(s, action)
    return _menu()


def _on_confirm_action(s: Session, action: str):
    if s.state == "lead_review":
        return _lead_review_action(s, action)
    if s.state == "ai" and s.draft:
        return _draft_action(s, action)
    return _menu("Сейчас нечего подтверждать.")


def _lead_review_action(s: Session, action: str):
    if action == "confirm":
        f = s.form
        lead_id = db.save_lead(db.SOURCE_BOT_FLOW, f["name"], f["contact"], f["description"])
        log.info("lead saved id=%s source=%s", lead_id, db.SOURCE_BOT_FLOW)
        s.reset()
        return _reply(
            f"Заявка №{lead_id} отправлена ({SOURCE_LABEL[db.SOURCE_BOT_FLOW]}). Менеджер свяжется с вами.",
            MENU_BUTTONS,
        )
    if action == "edit":
        s.state = "lead_name"
        return _reply("Хорошо, введём данные заново. Как вас зовут?", [("Отмена", "menu")], "Ваше имя")
    s.reset()
    return _menu("Заявка отменена.")


def _draft_action(s: Session, action: str):
    if action == "confirm":
        s.user_confirmed = True  # единственное место, где выставляется подтверждение
        lead_id = tools.save_confirmed_lead(s)
        if lead_id is None:
            return _menu("Не удалось сохранить заявку.")
        return _reply(
            f"Заявка №{lead_id} отправлена ({SOURCE_LABEL[db.SOURCE_AI]}). Менеджер свяжется с вами.",
            [("Задать вопрос", "ai_start"), BACK],
        )
    if action == "edit":
        return _reply(
            "Напишите, что нужно изменить, например: «контакт — name@mail.ru».",
            [("Отмена", "cancel"), BACK],
            "Что изменить?",
        )
    s.draft = None
    return _reply(
        "Черновик отменён. Можете задать другой вопрос.",
        [("Помоги с заявкой", "ai_lead_help"), BACK],
    )


# --- текст ----------------------------------------------------------------


def _on_text(s: Session, text: str):
    if text.lower() in ("меню", "/menu", "/start"):
        return _on_action(s, "menu")
    st = s.state
    if st == "lead_name":
        value, err = validation.check_name(text)
        if err:
            return _reply(err, [("Отмена", "menu")], "Ваше имя")
        s.form["name"], s.state = value, "lead_contact"
        return _reply("Как с вами связаться? E-mail, телефон или Telegram.", [("Отмена", "menu")], "Контакт")
    if st == "lead_contact":
        value, err = validation.check_contact(text)
        if err:
            return _reply(err, [("Отмена", "menu")], "Контакт")
        s.form["contact"], s.state = value, "lead_desc"
        return _reply("Коротко опишите задачу.", [("Отмена", "menu")], "Описание задачи")
    if st == "lead_desc":
        value, err = validation.check_description(text)
        if err:
            return _reply(err, [("Отмена", "menu")], "Описание задачи")
        s.form["description"], s.state = value, "lead_review"
        return _reply([_draft_text(s.form), "Отправить заявку?"], CONFIRM_BUTTONS)
    if st == "lead_review":
        return _reply("Выберите действие кнопкой ниже.", CONFIRM_BUTTONS)
    if st == "feedback":
        msg = validation.clean(text)
        if len(msg) < 3:
            return _reply("Слишком коротко. Напишите отзыв подробнее.", [("Отмена", "menu")], "Ваш отзыв")
        fb_id = db.save_feedback(msg)
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
    if s.draft and result.draft_updated:
        return _reply(messages, CONFIRM_BUTTONS, "Продолжить диалог…")
    return _reply(messages, [("Помоги с заявкой", "ai_lead_help"), BACK], "Ваш вопрос консультанту")
