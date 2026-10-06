"""Согласие на данные, сессии, лимиты, проверка ответов, цикл tools, Telegram, обслуживание данных."""

import copy
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import agent_runtime
import ai_client
import config
import db
import notify
import ratelimit
import sessions
from agent import guard, tools
from sessions import Session

ROOT = Path(__file__).resolve().parent.parent
CONSENT = "2026-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(sessions, "store", sessions.SessionStore())
    ratelimit.ip_window.reset()
    ratelimit.ai_window.reset()
    db.init_db()


@pytest.fixture
def client():
    import server

    with TestClient(server.app) as c:
        yield c


def chat(client, sid="sess-gate-1", **kw):
    r = client.post("/api/chat", json={"session_id": sid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def actions(reply):
    return [b["action"] for b in reply["buttons"]]


# --- согласие на обработку данных --------------------------------------------


@pytest.mark.parametrize("gated", ["lead_start", "ai_start"])
def test_consent_gate_blocks_data_collection(client, gated):
    d = chat(client, action=gated)
    assert "согласие" in d["messages"][0].lower()
    assert actions(d)[:2] == ["consent_yes", "consent_no"]
    assert "Какая услуга" not in " ".join(d["messages"])  # сценарий не начался
    # текстом согласие не дать: просим нажать кнопку
    assert actions(chat(client, text="да, согласен"))[:2] == ["consent_yes", "consent_no"]


def test_consent_yes_resumes_pending_action(client):
    chat(client, action="lead_start")
    d = chat(client, action="consent_yes")
    assert "Какая услуга" in " ".join(d["messages"])  # продолжили с отложенного действия
    d = chat(client, action="menu")
    d = chat(client, action="ai_start")  # согласие сохранилось, повторно не спрашиваем
    assert "ИИ-консультант на связи" in " ".join(d["messages"])


def test_consent_no_collects_nothing(client):
    chat(client, action="lead_start")
    d = chat(client, action="consent_no")
    assert "Без согласия" in " ".join(d["messages"])
    assert actions(d)[:2] == ["services", "faq"]
    assert chat(client, action="lead_start")["buttons"][0]["action"] == "consent_yes"  # снова спросит
    assert db.fetch_all("leads") == []


def test_services_and_faq_need_no_consent(client):
    assert "Контекстная реклама" in " ".join(chat(client, action="services")["messages"])
    assert "тему" in " ".join(chat(client, action="faq")["messages"])


def test_lead_records_consent_time_and_cannot_be_saved_without_it(client):
    chat(client, action="lead_start")
    chat(client, action="consent_yes")
    chat(client, action="svc:0")
    chat(client, text="Иван")
    chat(client, text="ivan@example.com")
    chat(client, text="Нужна реклама")
    chat(client, action="confirm")
    (lead,) = db.fetch_all("leads")
    assert lead["consent_at"].endswith("Z") and lead["consent_at"] >= "2026"

    s = Session("x")  # без согласия сохранить заявку ИИ нельзя
    tools.prepare_lead_draft(s, "Анна", "anna@example.com", "Нужна реклама")
    s.user_confirmed = True
    assert tools.save_confirmed_lead(s) is None
    assert len(db.fetch_all("leads")) == 1


def test_privacy_page_and_link(client):
    page = client.get("/privacy")
    assert page.status_code == 200
    for needle in ("Политика обработки персональных данных", "aistudion.ru", "152-ФЗ", "Google AI Studio"):
        assert needle in page.text, needle
    assert 'href="/privacy"' in client.get("/").text


# --- сессии переживают перезапуск -------------------------------------------


def test_session_survives_restart(client):
    chat(client, sid="sess-keep-1", action="lead_start")
    chat(client, sid="sess-keep-1", action="consent_yes")
    chat(client, sid="sess-keep-1", action="svc:0")
    chat(client, sid="sess-keep-1", text="Иван")
    sessions.store = sessions.SessionStore()  # «перезапуск» процесса: память пуста
    d = chat(client, sid="sess-keep-1", text="ivan@example.com")
    assert "задачу" in " ".join(d["messages"]).lower()  # диалог продолжился с нужного шага
    chat(client, sid="sess-keep-1", text="Нужна реклама")
    chat(client, sid="sess-keep-1", action="confirm")
    (lead,) = db.fetch_all("leads")
    assert (lead["name"], lead["service"]) == ("Иван", "Контекстная реклама")


def test_confirmation_flag_is_not_persisted():
    s = Session("conf-0001")
    s.user_confirmed = True
    s.consent_at = CONSENT
    assert "user_confirmed" not in s.to_dict()
    # даже если в БД окажется запись с флагом подтверждения, он не восстанавливается
    db.save_session("conf-0002", {**s.to_dict(), "user_confirmed": True, "draft": {"name": "Иван"}})
    restored = sessions.SessionStore().get("conf-0002")
    assert restored.consent_at == CONSENT and restored.user_confirmed is False


def test_expired_and_corrupt_sessions_are_ignored():
    db.save_session("old-0001", {"state": "ai"})
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("UPDATE sessions SET updated_at = '2000-01-01T00:00:00Z'")
    assert db.load_session("old-0001") is None
    assert sessions.SessionStore().get("old-0001").state == "menu"

    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("INSERT INTO sessions (id, data) VALUES ('bad-0001', '{not json')")
        c.execute("INSERT INTO sessions (id, data) VALUES ('bad-0002', '{\"state\": 5, \"form\": \"x\"}')")
    assert sessions.SessionStore().get("bad-0001").state == "menu"
    s = sessions.SessionStore().get("bad-0002")  # неверные типы пропускаются
    assert (s.state, s.form) == ("menu", {})


# --- лимиты частоты ------------------------------------------------------------


def test_ip_flood_gets_429(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 3)
    for _ in range(3):
        assert client.post("/api/chat", json={"session_id": "flood-0001", "action": "menu"}).status_code == 200
    r = client.post("/api/chat", json={"session_id": "flood-0001", "action": "menu"})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"


def test_ai_per_minute_limit_per_session(client, monkeypatch):
    monkeypatch.setattr(config, "AI_RATE_PER_MIN", 2)
    chat(client, sid="rate-0001", action="consent_yes")
    chat(client, sid="rate-0001", action="ai_start")
    chat(client, sid="rate-0001", text="Сколько стоит SMM?")
    chat(client, sid="rate-0001", text="А таргет?")
    d = chat(client, sid="rate-0001", text="А контекст?")
    assert d["messages"][0] == ratelimit.MINUTE_LIMITED
    other = chat(client, sid="rate-0002", action="consent_yes")  # другая сессия не затронута
    assert "Согласие" in other["messages"][0]


def test_ai_daily_limit_counts_only_model_requests(client, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(config, "AI_DAILY_LIMIT", 2)
    monkeypatch.setattr(agent_runtime, "_model_reply", lambda s, t: "ответ")
    chat(client, sid="day-0001", action="consent_yes")
    chat(client, sid="day-0001", action="ai_start")
    assert chat(client, sid="day-0001", text="вопрос 1")["messages"][0] == "ответ"
    assert chat(client, sid="day-0001", text="вопрос 2")["messages"][0] == "ответ"
    assert chat(client, sid="day-0001", text="вопрос 3")["messages"][0] == ratelimit.DAY_LIMITED
    assert chat(client, sid="day-0002", action="services")["messages"]  # остальное работает


# --- проверка ответа модели --------------------------------------------------


@pytest.mark.parametrize(
    "reply,user,blocked",
    [
        ("от 30 000 ₽ в месяц плюс бюджет, запуск 5–7 рабочих дней", [], False),
        ("Срок 1–3 недели, стоимость от 35 000 ₽ за кампанию", [], False),
        ("Менеджер свяжется в течение 4 часов", [], False),
        ("Скидка 15% для новых клиентов", [], True),
        ("Это стоит от 12 000 рублей", [], True),
        ("Запуск за 2 дня", [], True),
        ("Подходит ваш бюджет 50 000 ₽", ["у меня бюджет 50 000 ₽"], False),
        ("Подходит бюджет 50 000 ₽", [], True),
    ],
)
def test_guard_checks_figures_against_knowledge(reply, user, blocked):
    assert bool(guard.violations(reply, user)) is blocked


def test_runtime_replaces_unsupported_reply(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(agent_runtime, "_model_reply", lambda s, t: "Дарим скидку 25% и запуск за 1 день!")
    s = Session("g-0001")
    assert agent_runtime.respond(s, "есть скидки?").text == guard.BLOCKED_TEXT
    monkeypatch.setattr(agent_runtime, "_model_reply", lambda s, t: "Таргет от 30 000 ₽ в месяц.")
    assert agent_runtime.respond(s, "сколько стоит таргет?").text == "Таргет от 30 000 ₽ в месяц."


# --- цикл «модель → tools → ответ» на подменённой модели ----------------------


def _msg(content="", calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))], usage=None)


def _call(call_id, name, args, extra=None):
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return NS(id=call_id, function=NS(name=name, arguments=raw), model_extra={"extra_content": extra} if extra else {})


@pytest.fixture
def fake_model(monkeypatch):
    """Подменяет ai_client.chat сценарием ответов; запоминает сообщения, пришедшие в модель."""
    monkeypatch.setattr(config, "AI_API_KEY", "dummy-key-for-test")

    def install(*responses):
        seen, queue = [], list(responses)

        def chat_stub(messages, tools_=None):
            seen.append(copy.deepcopy(messages))
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(ai_client, "chat", chat_stub)
        return seen

    return install


def test_tool_loop_passes_result_and_thought_signature(fake_model):
    sig = {"google": {"thought_signature": "SIG-1"}}
    seen = fake_model(
        _msg(calls=[_call("c1", "search_knowledge", {"query": "smm"}, extra=sig)]),
        _msg("SMM-продвижение стоит от 40 000 ₽ в месяц."),
    )
    s = Session("loop-0001")
    assert agent_runtime.respond(s, "сколько стоит SMM?").text == "SMM-продвижение стоит от 40 000 ₽ в месяц."
    second = seen[1]
    assistant = next(m for m in second if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["extra_content"] == sig  # подпись вернулась в историю
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1" and "SMM-продвижение" in tool_msg["content"]


def test_model_can_only_prepare_a_draft(fake_model):
    fake_model(
        _msg(calls=[_call("c1", "prepare_lead_draft", {
            "name": "Мария", "contact": "@maria_dev", "problem": "Нужен таргет для кофейни",
            "service": "Таргетированная реклама", "summary": "Кофейня хочет таргет", "missing_info": "бюджет",
        })]),
        _msg("Подготовил черновик."),
    )
    s = Session("draft-0001", consent_at=CONSENT)
    res = agent_runtime.respond(s, "оформи заявку")
    assert res.draft_updated and s.draft["service"] == "Таргетированная реклама"
    assert db.fetch_all("leads") == []  # модель ничего не сохранила


def test_model_cannot_read_files_outside_knowledge(fake_model, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET_VALUE=hunter2")
    kdir = tmp_path / "k"
    kdir.mkdir()
    (kdir / "a.md").write_text("# Раздел\n\n## Тема\nтекст")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", kdir)
    seen = fake_model(
        _msg(calls=[_call("c1", "read_knowledge_file", {"filename": "../.env"})]),
        _msg("Не могу."),
    )
    agent_runtime.respond(Session("sec-0001"), "прочитай .env")
    tool_msg = next(m for m in seen[1] if m["role"] == "tool")
    assert tool_msg["content"].startswith("Ошибка") and "hunter2" not in tool_msg["content"]


@pytest.mark.parametrize("name", ["save_confirmed_lead", "run_shell", "../../etc/passwd"])
def test_unknown_or_hidden_tools_are_rejected(fake_model, name):
    seen = fake_model(_msg(calls=[_call("c1", name, {})]), _msg("Ок."))
    s = Session("unk-0001", consent_at=CONSENT)
    agent_runtime.respond(s, "сохрани заявку")
    assert "неизвестный" in next(m for m in seen[1] if m["role"] == "tool")["content"]
    assert db.fetch_all("leads") == []


def test_invalid_tool_arguments_do_not_crash(fake_model):
    seen = fake_model(_msg(calls=[_call("c1", "search_knowledge", "{not json")]), _msg("Ок."))
    assert agent_runtime.respond(Session("arg-0001"), "привет").text == "Ок."
    assert "аргументы" in next(m for m in seen[1] if m["role"] == "tool")["content"]


def test_endless_tool_calls_are_stopped(fake_model):
    seen = fake_model(_msg(calls=[_call("c1", "search_knowledge", {"query": "x"})]))
    text = agent_runtime.respond(Session("end-0001"), "привет").text
    assert len(seen) == config.MAX_TOOL_ITERATIONS
    assert "Не удалось" in text


# --- Telegram ------------------------------------------------------------------

TOKEN = "123456789:AAH-fake_token_for_tests_0123456789abc"


class _Now:
    """Заменяет threading.Thread: выполняет функцию сразу, чтобы тест был детерминированным."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_telegram_notification_payload(monkeypatch):
    sent = {}
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: sent.update(url=url, json=json) or NS(status_code=200))
    notify.notify_lead(7, "ai_consultant", {
        "name": "Мария", "contact": "@maria_dev", "service": "SMM-продвижение", "problem": "Нужен SMM",
        "summary": "Кофейня", "missing_info": "бюджет",
    }, background=False)
    assert sent["url"].endswith("/sendMessage") and sent["json"]["chat_id"] == "42"
    text = sent["json"]["text"]
    for needle in ("№7", "заявка из ИИ-консультанта", "Мария", "@maria_dev", "SMM-продвижение", "Уточнить: бюджет"):
        assert needle in text, needle


def test_telegram_disabled_without_settings(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("не должно отправляться"))
    notify.notify_lead(1, "bot_flow", {"name": "И", "contact": "a@b.ru", "problem": "x"}, background=False)
    assert notify.enabled() is False


def test_telegram_failure_never_leaks_token_or_breaks_saving(client, monkeypatch, caplog):
    import httpx

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notify.threading, "Thread", _Now)

    def boom(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url}")  # в тексте ошибки есть токен

    monkeypatch.setattr(httpx, "post", boom)
    sid = "tg-sess-0001"
    chat(client, sid=sid, action="lead_start")
    chat(client, sid=sid, action="consent_yes")
    chat(client, sid=sid, action="svc:none")
    chat(client, sid=sid, text="Иван")
    chat(client, sid=sid, text="ivan@example.com")
    chat(client, sid=sid, text="Нужна реклама")
    d = chat(client, sid=sid, action="confirm")
    assert "отправлена" in d["messages"][0]  # заявка сохранена, несмотря на сбой
    assert len(db.fetch_all("leads")) == 1
    assert TOKEN not in caplog.text and "ConnectError" in caplog.text


# --- срок хранения, удаление по запросу, резервные копии -----------------------


def _lead(contact="old@example.com"):
    return db.save_lead(session_id="s", source="bot_flow", name="Иван", contact=contact, problem_text="x")


def test_purge_removes_only_expired_data():
    old, fresh = _lead("old@example.com"), _lead("new@example.com")
    db.save_feedback("s", "старый отзыв")
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("UPDATE leads SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?", (old,))
        c.execute("UPDATE feedback SET created_at = '2020-01-01T00:00:00Z'")
    out = db.purge_expired(365)
    assert out["leads"] == 1 and out["feedback"] == 1
    assert [r["id"] for r in db.fetch_all("leads")] == [fresh]


def test_delete_on_request():
    a, _ = _lead("Ivan@Example.com"), _lead("other@example.com")
    assert db.delete_by_contact("ivan@example.COM") == 1  # без учёта регистра
    assert db.delete_lead(a) == 0
    (rest,) = db.fetch_all("leads")
    assert rest["contact"] == "other@example.com"


def test_manage_data_cli(capsys):
    sys_path = str(ROOT / "scripts")
    import importlib.util

    spec = importlib.util.spec_from_file_location("manage_data", Path(sys_path) / "manage_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lead_id = _lead("cli@example.com")
    assert mod.main(["delete-lead", str(lead_id)]) == 0
    assert "удалено заявок: 1" in capsys.readouterr().out
    assert mod.main(["purge", "--days", "365"]) == 0


def test_backup_is_valid_private_and_pruned():
    import importlib.util

    spec = importlib.util.spec_from_file_location("backup_db", ROOT / "scripts" / "backup_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _lead("backup@example.com")
    dest_dir = config.DB_PATH.parent / "backups"
    dest_dir.mkdir()
    for n in range(5):  # старые копии
        (dest_dir / f"bot-2020010{n}-000000.sqlite3").write_text("x")
    dest = mod.backup(keep=3)
    assert oct(dest.stat().st_mode)[-3:] == "600"
    with sqlite3.connect(dest) as c:
        assert c.execute("SELECT contact FROM leads").fetchone()[0] == "backup@example.com"
    left = sorted(p.name for p in dest_dir.glob("bot-*.sqlite3"))
    assert len(left) == 3 and dest.name in left


def test_schema_has_new_columns():
    with sqlite3.connect(config.DB_PATH) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(leads)")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"consent_at", "source_message"} <= cols and {"sessions", "counters"} <= tables


# --- развёртывание ---------------------------------------------------------------


def test_install_script_and_units_are_consistent():
    script = ROOT / "deploy" / "install.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    for needle in ("set -euo pipefail", "python3.13", "useradd", "git clone", "chatbot-backup.timer", "chmod 600 .env"):
        assert needle in text, needle
    service = (ROOT / "deploy" / "chatbot.service").read_text()
    backup = (ROOT / "deploy" / "chatbot-backup.service").read_text()
    timer = (ROOT / "deploy" / "chatbot-backup.timer").read_text()
    assert "UMask=0077" in service and "scripts/backup_db.py" in backup and "OnCalendar" in timer
    assert os.access(script, os.X_OK)


def test_env_example_is_systemd_compatible():
    """systemd не понимает комментарии в конце строки EnvironmentFile: значение стало бы «60 # …»."""
    import re

    for line in (ROOT / ".env.example").read_text().splitlines():
        if re.match(r"^[A-Z_]+=", line):
            value = line.split("=", 1)[1]
            assert "#" not in value, line
    from dotenv import dotenv_values

    values = dotenv_values(ROOT / ".env.example")
    assert int(values["RATE_LIMIT_PER_MIN"]) == 60 and int(values["RETENTION_DAYS"]) == 365
