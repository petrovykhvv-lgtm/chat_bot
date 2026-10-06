# Вектор — чат-бот рекламного агентства с ИИ-консультантом

Один Python-runtime (FastAPI) с простым web-интерфейсом чата. Работает локально и поднимается на VPS тем же способом. Данные хранятся в SQLite.

## Спецификация MVP

Полная версия — [docs/SPEC.md](docs/SPEC.md): легенда, граница MVP, услуги, FAQ, данные заявки, ограничения ИИ, план по слоям.


**Пользовательский сценарий**

1. Запуск проекта локально, открытие чата.
2. Главное меню и поле ввода.
3. `Услуги` и `FAQ` (четыре категории) — ответы из `knowledge/` без обращения к модели. Меню, услуги, FAQ, обычная заявка и обратная связь работают без ключа API и при исчерпанном лимите провайдера.
4. Обычная заявка через `bot-flow`: услуга → имя → контакт → задача → подтверждение. Источник `bot_flow`.
5. `ИИ-консультант`: вопросы по услугам и правилам, ответы строятся на `knowledge/` через OpenAI-совместимый API (по умолчанию Google AI Studio).
6. «Помоги с заявкой» → черновик → `Отправить заявку` / `Изменить` / `Отмена`. Без нажатия кнопки подтверждения заявка не сохраняется. Источник `ai_consultant`.
7. `Обратная связь` — отдельная таблица `feedback`.

**Вне рамок:** домен, публичная ссылка, кабинет управления, production-контур, авторизация.

## Архитектура

| Файл | Назначение |
|---|---|
| `server.py` | FastAPI: `/`, `/api/chat`, `/api/health`, статика |
| `bot_flow.py` | Сценарии меню, FAQ, заявки, обратной связи; маршрутизация в ИИ |
| `ai_client.py` | Подключение к AI Studio API (ключ, адрес, модель только из `.env`), лог расхода токенов |
| `agent_runtime.py` | Цикл «модель → tools → ответ»; демо-режим без ключа; сообщение при исчерпанном лимите |
| `agent/tools.py` | `search_knowledge`, `read_knowledge_file`, `prepare_lead_draft`, `save_confirmed_lead` |
| `agent/knowledge.py` | Разбор, поиск и безопасное чтение `knowledge/` |
| `agent/soul.md` | Системный промпт консультанта |
| `knowledge/` | Услуги, FAQ, правила |
| `db.py` | SQLite `data/bot.sqlite3`: `leads` (`session_id`, `source`, `service`, `contact`, `problem_text`, `agent_summary`, `missing_info`, `status`, `created_at`), `feedback` (`session_id`, `message_text`, `created_at`) |
| `static/` | HTML/CSS/JS интерфейса |
| `deploy/chatbot.service` | systemd-юнит для VPS |

**Безопасность**

- `read_knowledge_file` читает только `.md`/`.txt` внутри `knowledge/`. Запрещены `..`, абсолютные пути, скрытые файлы (`.env`), симлинки наружу.
- Модель получает три tools. `save_confirmed_lead` ей не выдаётся: runtime вызывает его только после нажатия кнопки пользователем, а флаг подтверждения выставляет лишь обработчик кнопки.
- Текст заявки в черновике показывает runtime из проверенных полей, а не модель.
- Содержимое базы знаний и сообщения пользователя в промпте названы данными, а не инструкциями.
- В логи не пишутся тексты сообщений и данные заявок, только id и источник. Ключ API, e-mail и телефоны маскируются фильтром логов.
- `.env` не хранится в репозитории (есть только `.env.example`).

## Локальный запуск

Нужен Python 3.13.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # вписать AI_API_KEY
python server.py
```

Откройте http://127.0.0.1:8000.

Ключ создаётся бесплатно на https://aistudio.google.com/apikey. Без ключа ИИ-консультант работает в демо-режиме: отвечает фрагментами базы знаний, а черновик заявки собирает из сообщения вида `Имя: Анна, контакт: anna@example.com, задача: нужна таргетированная реклама`.

## Проверка

```bash
pip install -r requirements-dev.txt
pytest                         # тесты tools, подтверждения, источников заявок
python scripts/show_db.py      # содержимое leads и feedback
```

Ручной прогон: `Услуги` → `FAQ` → `Оставить заявку` → `ИИ-консультант` (вопрос, затем «Помоги с заявкой») → `Отправить заявку` → `Обратная связь` → `python scripts/show_db.py`: в `leads` видны `bot_flow` и `ai_consultant`, в `feedback` — отзыв.

Скриншоты лежат в `evidence/screenshots/`.

## Лимиты и биллинг AI Studio

- Бесплатный тариф ограничивает число запросов **по каждой модели отдельно**. Проверено 2026-10-06: для `gemini-3.6-flash` лимит 20 запросов в сутки на проект. Один вопрос консультанту тратит 2–3 запроса (поиск по базе знаний и ответ), поэтому хватает на несколько вопросов. После исчерпания приходит ошибка 429, бот показывает «Лимит запросов исчерпан», а меню, услуги, FAQ, заявка и обратная связь продолжают работать.
- По умолчанию используется `gemini-3.5-flash-lite`: проверена работа с tools, на серии из 30+ запросов лимит не сработал. Модель меняется через `AI_MODEL` в `.env`. Часть моделей (`gemini-3.8-flash`, `gemini-3.1-flash-lite`) временами отвечает 503 «высокий спрос»: клиент повторяет запрос до трёх раз.
- Расход токенов пишется в лог (`llm request ok: prompt_tokens=… completion_tokens=…`), тексты сообщений не логируются.
- Остаток квоты и биллинг смотрите в личном кабинете: https://aistudio.google.com/usage (или https://ai.dev/rate-limit). Платёжная карта для бесплатного тарифа не нужна; платный тариф включается в проекте Google Cloud по желанию.

## Проверка консультанта на реальной модели

```bash
python scripts/check_agent.py            # все вопросы-ловушки
python scripts/check_agent.py скидк .env # только кейсы по части названия
```

Скрипт проверяет: ответ по цене из базы; скидки, услуги и гарантии, которых в базе нет; просьбы прочитать `.env` и выполнить shell-команду; промпт-инъекцию; вопрос вне темы; подбор услуги с уточняющим вопросом.

## Запуск на VPS

Ubuntu 24.04, Python 3.13 (например, через `deadsnakes`):

```bash
sudo apt install -y python3.13 python3.13-venv git
sudo useradd -r -m -d /opt/chatbot chatbot
sudo -u chatbot git clone https://github.com/petrovykhvv-lgtm/chat_bot.git /opt/chatbot
cd /opt/chatbot
sudo -u chatbot python3.13 -m venv .venv
sudo -u chatbot .venv/bin/pip install -r requirements.txt
sudo -u chatbot cp .env.example .env && sudo -u chatbot nano .env    # AI_API_KEY
sudo cp deploy/chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chatbot
```

Проверка, что runtime запущен без ошибок:

```bash
systemctl status chatbot --no-pager
journalctl -u chatbot -n 30 --no-pager
curl -s http://127.0.0.1:8000/api/health     # {"status":"ok","ai":"model"}
```

Интерфейс слушает `127.0.0.1`. Чтобы открыть его с компьютера без публикации порта, используйте SSH-туннель: `ssh -L 8000:127.0.0.1:8000 user@vps`, затем http://127.0.0.1:8000.
