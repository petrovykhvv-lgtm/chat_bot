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
    assert len(faq["buttons"]) > 2
    assert "4 часов" in " ".join(chat(client, action="faq:0")["messages"])


def test_sources_and_feedback_are_separate(client):
    # обычная заявка
    chat(client, action="lead_start")
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
    fb = db.fetch_all("feedback")
    assert len(fb) == 1 and fb[0]["message"] == "Удобный интерфейс"


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
