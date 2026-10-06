import pytest
from fastapi.testclient import TestClient

import config
import db
from agent import knowledge, tools
from sessions import Session


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "AI_API_KEY", "")  # демо-режим, без сети
    db.init_db()


# --- защита tools ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../.env", ".env", "/etc/passwd", "..", "a/../../.env", "~/x.md", "faq.md\x00.txt", "C:\\x.md", "", None, 5],
)
def test_read_rejects_unsafe_paths(name):
    with pytest.raises(knowledge.KnowledgeAccessError):
        knowledge.read_file(name)
    assert tools.read_knowledge_file(name).startswith("Ошибка")


def test_read_rejects_symlink_outside(tmp_path, monkeypatch):
    kdir = tmp_path / "k"
    kdir.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("TOPSECRET")
    (kdir / "link.md").symlink_to(secret)
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", kdir)
    assert "TOPSECRET" not in tools.read_knowledge_file("link.md")


def test_read_allows_knowledge_file():
    assert "Контекстная реклама" in knowledge.read_file("services.md")


def test_search_finds_prices():
    assert "Контекстная реклама" in tools.search_knowledge("сколько стоит контекстная реклама")


def test_unknown_tool_rejected():
    s = Session("x")
    assert "неизвестный" in tools.execute_tool(s, "save_confirmed_lead", "{}")
    assert "неизвестный" in tools.execute_tool(s, "read_env", "{}")


# --- confirmation flow ------------------------------------------------------


def test_prepare_draft_does_not_save():
    s = Session("x")
    tools.prepare_lead_draft(s, "Анна", "anna@example.com", "Нужна реклама")
    assert s.draft and db.fetch_all("leads") == []


def test_save_requires_user_confirmation():
    s = Session("x")
    tools.prepare_lead_draft(s, "Анна", "anna@example.com", "Нужна реклама")
    assert tools.save_confirmed_lead(s) is None
    assert db.fetch_all("leads") == []


def test_invalid_draft_rejected():
    s = Session("x")
    assert "не создан" in tools.prepare_lead_draft(s, "А", "нет", "x")
    assert s.draft is None


# --- сценарии через HTTP ----------------------------------------------------


@pytest.fixture
def client():
    import server

    with TestClient(server.app) as c:
        yield c


def chat(client, sid="sess-0001", **kw):
    r = client.post("/api/chat", json={"session_id": sid, **kw})
    assert r.status_code == 200
    return r.json()


def test_menu_services_faq(client):
    d = chat(client, action="start")
    assert [b["action"] for b in d["buttons"]][:2] == ["services", "faq"]
    assert "Контекстная реклама" in " ".join(chat(client, action="services")["messages"])
    faq = chat(client, action="faq")
    assert len(faq["buttons"]) > 4
    cat = chat(client, action="faqc:1")  # «Стоимость»
    assert "Стоимость" in cat["messages"][0]
    ans = chat(client, action=cat["buttons"][0]["action"])
    assert "рекламный бюджет" in " ".join(ans["messages"]).lower()


def test_sources_and_feedback_are_separate(client):
    # обычная заявка
    chat(client, action="lead_start")
    chat(client, action="svc:0")
    chat(client, text="Иван")
    chat(client, text="ivan@example.com")
    d = chat(client, text="Нужна реклама в VK")
    assert [b["action"] for b in d["buttons"]] == ["confirm", "edit", "cancel"]
    assert db.fetch_all("leads") == []  # до подтверждения не сохраняется
    chat(client, action="confirm")

    # заявка из ИИ-консультанта
    chat(client, action="ai_start")
    d = chat(client, text="Имя: Мария, контакт: @maria_dev, задача: запуск рекламы")
    assert [b["action"] for b in d["buttons"]] == ["confirm", "edit", "cancel"]
    assert len(db.fetch_all("leads")) == 1
    chat(client, action="confirm")

    # обратная связь
    chat(client, action="feedback_start")
    chat(client, text="Удобный интерфейс")

    leads = db.fetch_all("leads")
    assert [(r["source"], r["name"]) for r in leads] == [
        ("bot_flow", "Иван"),
        ("ai_consultant", "Мария"),
    ]
    assert leads[0]["service"] == "Контекстная реклама" and leads[0]["status"] == "new"
    assert leads[0]["session_id"] == "sess-0001"
    fb = db.fetch_all("feedback")
    assert len(fb) == 1 and fb[0]["message_text"] == "Удобный интерфейс"
    assert fb[0]["session_id"] == "sess-0001"


def test_cancel_draft_saves_nothing(client):
    chat(client, action="ai_start")
    chat(client, text="Имя: Олег, контакт: oleg@example.com, задача: сайт")
    chat(client, action="cancel")
    chat(client, action="confirm")  # подтверждать больше нечего
    assert db.fetch_all("leads") == []


def test_bad_session_id_and_long_text_rejected(client):
    assert client.post("/api/chat", json={"session_id": "../x", "text": "hi"}).status_code == 422
    assert (
        client.post("/api/chat", json={"session_id": "sess-0001", "text": "a" * 5000}).status_code
        == 422
    )


# --- фаза 2: схема, FAQ, автономность bot-flow -----------------------------


def test_schema_columns():
    import sqlite3

    with sqlite3.connect(config.DB_PATH) as conn:
        cols = lambda t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
    assert {"session_id", "source", "service", "contact", "problem_text",
            "agent_summary", "missing_info", "status", "created_at"} <= cols("leads")
    assert {"session_id", "message_text", "created_at"} <= cols("feedback")


def test_source_check_constraint():
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.save_lead(session_id="s", source="other", name="Иван", contact="a@b.ru", problem_text="x")


def test_faq_has_four_categories_with_answers(client):
    d = chat(client, action="faq")
    cats = [b for b in d["buttons"] if b["action"].startswith("faqc:")]
    assert len(cats) >= 4
    for c in cats:
        qs = chat(client, action=c["action"])
        q = next(b for b in qs["buttons"] if b["action"].startswith("faq:"))
        assert chat(client, action=q["action"])["messages"][1]


def test_catalog_has_3_to_5_services_with_price():
    secs = knowledge.load_sections("services.md")
    assert 3 <= len(secs) <= 5
    assert all("от" in s.body and "₽" in s.body for s in secs)


def test_unknown_input_does_not_break_flow(client):
    assert "Не понял" in " ".join(chat(client, text="абракадабра")["messages"])
    assert chat(client, action="no_such_action")["buttons"]
    chat(client, action="lead_start")
    assert "кнопкой" in " ".join(chat(client, text="что-то")["messages"])  # шаг выбора услуги
    chat(client, action="svc:none")
    assert "Имя" in " ".join(chat(client, text="я")["messages"])  # валидация, шаг не сдвинулся
    assert chat(client, text="меню")["buttons"][0]["action"] == "services"


def test_bot_flow_works_without_model(client, monkeypatch):
    import agent_runtime

    def boom(*a, **k):
        raise AssertionError("bot-flow не должен обращаться к модели")

    monkeypatch.setattr(agent_runtime, "respond", boom)
    monkeypatch.setattr(agent_runtime, "_model_reply", boom)
    chat(client, action="services")
    chat(client, action="faq")
    chat(client, action="lead_start")
    chat(client, action="svc:none")
    chat(client, text="Иван")
    chat(client, text="+7 900 123-45-67")
    chat(client, text="Нужна реклама для кафе")
    chat(client, action="confirm")
    chat(client, action="feedback_start")
    chat(client, text="Всё понятно")
    assert len(db.fetch_all("leads")) == 1 and len(db.fetch_all("feedback")) == 1


def test_ai_failure_degrades_gracefully(client, monkeypatch):
    import agent_runtime

    monkeypatch.setattr(config, "AI_API_KEY", "dummy-key-for-test")

    def limit(*a, **k):
        raise RuntimeError("429 quota exceeded")

    monkeypatch.setattr(agent_runtime, "_model_reply", limit)
    chat(client, action="ai_start")
    d = chat(client, text="Сколько стоит реклама?")
    assert "недоступен" in d["messages"][0] and d["buttons"]


# --- фаза 3: ИИ-консультант и безопасные tools ------------------------------


def test_model_gets_only_safe_tools():
    names = {t["function"]["name"] for t in tools.TOOL_SCHEMAS}
    assert names == {"search_knowledge", "read_knowledge_file", "prepare_lead_draft"}
    # save_confirmed_lead существует, но модели не выдаётся
    assert callable(tools.save_confirmed_lead)


def test_search_stays_inside_knowledge(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET_VALUE")
    kdir = tmp_path / "k"
    kdir.mkdir()
    (kdir / "a.md").write_text("# Раздел\n\n## Тема\nтекст про рекламу")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", kdir)
    assert "SECRET_VALUE" not in tools.search_knowledge("SECRET_VALUE .env ../.env")
    assert knowledge.list_files() == ["a.md"]


def test_no_secrets_in_source():
    import re
    from pathlib import Path

    pattern = re.compile(r"AIza[\w-]{20,}|AQ\.[\w.-]{20,}|sk-[\w-]{20,}")
    for f in Path(config.BASE_DIR).rglob("*"):
        if f.suffix in {".py", ".md", ".js", ".html", ".service", ".txt"} and not any(
            p in f.parts for p in (".venv", ".git")
        ):
            assert not pattern.search(f.read_text(encoding="utf-8")), f


def test_soul_defines_role_tone_and_limits():
    soul = config.SOUL_PATH.read_text(encoding="utf-8")
    for needle in ("Роль", "Тон", "search_knowledge", "не выполняешь команды", ".env", "скидк", "оставить заявку"):
        assert needle in soul, needle


def test_rate_limit_message(client, monkeypatch):
    import agent_runtime

    class RateLimitError(Exception):
        pass

    def limited(*a, **k):
        raise RateLimitError("429")

    monkeypatch.setattr(config, "AI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(agent_runtime, "_model_reply", limited)
    chat(client, action="ai_start")
    d = chat(client, text="Сколько стоит реклама?")
    assert "Лимит" in d["messages"][0]
