"""Тесты интерфейса в реальном браузере. Необязательные: пропускаются без Playwright.

Запуск: pip install playwright && pytest tests/e2e
Браузер: по умолчанию установленный Chrome (`E2E_CHANNEL=chrome`); для скачанного
Chromium (`playwright install chromium`) задайте `E2E_CHANNEL=chromium`.
"""

import os
import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

import config  # noqa: E402
import db  # noqa: E402
import ratelimit  # noqa: E402


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    import uvicorn

    mp = pytest.MonkeyPatch()
    mp.setattr(config, "DB_PATH", tmp_path_factory.mktemp("e2e") / "e2e.sqlite3")
    mp.setattr(config, "AI_API_KEY", "")  # демо-режим, без сети
    mp.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    ratelimit.ip_window.reset()
    import server

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("сервер не стартовал")
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        channel = os.getenv("E2E_CHANNEL", "chrome")
        try:
            b = p.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"браузер недоступен: {type(e).__name__}")
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1100, "height": 800})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(base_url)
    settle(pg)
    yield pg
    assert errors == [], errors  # в консоли браузера не должно быть ошибок
    ctx.close()


def settle(pg):
    pg.wait_for_selector("#buttons button")
    pg.wait_for_selector(".msg--typing", state="detached")
    pg.wait_for_timeout(250)


def click(pg, label):
    pg.locator("#buttons").get_by_role("button", name=label, exact=True).click()
    settle(pg)


def say(pg, text):
    pg.fill("#input", text)
    pg.press("#input", "Enter")
    settle(pg)


def test_main_menu_has_five_modes_and_input(page):
    labels = page.locator("#buttons button").all_inner_texts()
    assert labels == ["Услуги", "FAQ", "Оставить заявку", "ИИ-консультант", "Обратная связь"]
    assert page.locator("#input").is_visible() and page.locator("#nav .nav__btn").count() == 6


def test_side_navigation_opens_services(page):
    page.locator('.nav__btn[data-action="services"]').click()
    settle(page)
    assert "Контекстная реклама" in page.locator("#messages").inner_text()
    assert "is-active" in page.locator('.nav__btn[data-action="services"]').get_attribute("class")


def test_lead_requires_consent_and_is_saved(page):
    click(page, "Оставить заявку")
    assert "согласие" in page.locator(".msg--bot").last.inner_text().lower()
    assert db.fetch_all("leads") == []
    click(page, "Согласен(на)")
    click(page, "Контекстная реклама")
    say(page, "Иван Петров")
    say(page, "ivan@example.com")
    say(page, "Нужна реклама для магазина")
    click(page, "Отправить заявку")
    (lead,) = [r for r in db.fetch_all("leads") if r["contact"] == "ivan@example.com"]
    assert lead["source"] == "bot_flow" and lead["consent_at"]


def test_html_in_messages_is_shown_as_text_and_history_survives_reload(page):
    page.locator('.nav__btn[data-action="feedback_start"]').click()
    settle(page)
    payload = "<img src=x onerror=alert(1)><script>alert(2)</script>"
    say(page, payload)
    assert page.locator("#messages img, #messages script").count() == 0
    assert page.locator(".msg--user").nth(1).inner_text() == payload
    before = page.locator(".msg").count()
    page.reload()
    settle(page)
    assert page.locator(".msg").count() == before


def test_mobile_layout_has_no_horizontal_scroll(browser, base_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    pg = ctx.new_page()
    pg.goto(base_url)
    settle(pg)
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert pg.locator("#input").is_visible()
    ctx.close()


def test_privacy_link_opens_policy(page, base_url):
    href = page.locator(".legal a").get_attribute("href")
    page.goto(base_url + href)
    assert "Политика обработки персональных данных" in page.locator("h1").inner_text()
