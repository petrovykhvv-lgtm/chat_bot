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
6. «Помоги с заявкой» → структурированный черновик (услуга, задача, контакт, что известно, чего не хватает, исходный запрос) → `Отправить заявку` / `Изменить` / `Отмена`. Без нажатия кнопки подтверждения заявка не сохраняется; `Изменить` возвращает к уточнению, `Отмена` ничего не записывает. Источник `ai_consultant`.
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

Скриншоты локального прогона лежат в `evidence/screenshots/`:

| Файл | Что показывает |
|---|---|
| `01-chat-main-menu.png` | главное меню и поле ввода |
| `02-botflow-faq-lead.png` | FAQ по категориям и обычная заявка через `bot-flow` |
| `03-ai-consultant.png` | ответ по базе знаний, отказ на просьбы прочитать `.env` и файл вне `knowledge/`, структурированный черновик с кнопками |
| `04-sqlite-leads-feedback.png` | `leads` с разными `source` и отдельная таблица `feedback` |

`05-vps-runtime.png` — запуск на VPS: Python 3.13.16 в `venv`, сервис `active (running)`, health `"ai":"model"`, права 600 на `.env` и базу, счётчики по видам, журнал старта без секретов.

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

Проверено на Ubuntu 26.04 (2 vCPU, 4 ГБ). Интерфейс слушает только `127.0.0.1`: домен, публичная ссылка и открытый порт не нужны. Один процесс `python server.py` под управлением systemd.

> **Регион сервера.** Google AI Studio API недоступен из ряда стран, включая РФ: с российского VPS приходит `User location is not supported for the API use`. Проверено: с VPS в Финляндии с тем же ключом всё работает. Меню, услуги, FAQ, заявка и обратная связь работают на любом сервере и без ключа. Для сервера в РФ используйте OpenAI-совместимого провайдера, доступного из РФ (например, Yandex AI Studio): см. закомментированные `AI_BASE_URL`, `AI_MODEL`, `AI_PROJECT` в `.env.example`.

**1. Python 3.13.** Если в репозиториях дистрибутива его нет (как в Ubuntu 26.04), ставим через `uv`:

```bash
apt-get update && apt-get install -y python3-venv python3-pip git
python3 -m venv /opt/uv-tool && /opt/uv-tool/bin/pip install uv
UV_PYTHON_INSTALL_DIR=/opt/python /opt/uv-tool/bin/uv python install 3.13
ln -sf "$(ls -d /opt/python/cpython-3.13*/bin/python3.13 | head -1)" /usr/local/bin/python3.13
python3.13 --version
```

**2. Код, окружение, зависимости** (от имени отдельного пользователя без shell):

```bash
useradd -r -s /usr/sbin/nologin -d /opt/chatbot chatbot
mkdir -p /opt/chatbot && chown chatbot:chatbot /opt/chatbot && cd /opt/chatbot
runuser -u chatbot -- git clone https://github.com/petrovykhvv-lgtm/chat_bot.git .
runuser -u chatbot -- python3.13 -m venv .venv
runuser -u chatbot -- .venv/bin/pip install -r requirements.txt
```

**3. `.env`** (настоящий файл только на сервере, в репозитории его нет). Ключ вводится скрыто, в историю команд и логи не попадает:

```bash
cd /opt/chatbot
runuser -u chatbot -- cp .env.example .env && chmod 600 .env
read -rs -p "AI_API_KEY: " K && echo && sed -i "s|^AI_API_KEY=.*|AI_API_KEY=$K|" .env && unset K
```

**4. Сервис:**

```bash
cp deploy/chatbot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now chatbot
```

SQLite создаётся при старте в `/opt/chatbot/data/bot.sqlite3` (права 600 благодаря `UMask=0077` в юните). Каталог данных закройте для остальных: `chmod 750 /opt/chatbot/data`.

Проверка после запуска: `systemctl status chatbot`, затем `curl -s http://127.0.0.1:8000/api/health`. В ответе `"ai":"model"` значит, что ключ подхвачен, `"ai":"demo"` — ключ пуст (бот работает без модели).

**Управление**

```bash
systemctl status chatbot --no-pager      # состояние
systemctl stop chatbot                   # остановить
systemctl start chatbot                  # запустить
systemctl restart chatbot                # перезапустить (после правки .env)
journalctl -u chatbot -n 50 --no-pager   # логи
journalctl -u chatbot -f                 # логи в реальном времени
curl -s http://127.0.0.1:8000/api/health # {"status":"ok","ai":"model"}
```

**Обновление версии**

```bash
cd /opt/chatbot
runuser -u chatbot -- git pull
runuser -u chatbot -- .venv/bin/pip install -r requirements.txt
cp deploy/chatbot.service /etc/systemd/system/ && systemctl daemon-reload
systemctl restart chatbot
```

**Проверка заявок на сервере**

```bash
cd /opt/chatbot && runuser -u chatbot -- .venv/bin/python scripts/show_db.py
```

Чтобы открыть интерфейс с компьютера без публикации порта, используйте SSH-туннель: `ssh -L 8000:127.0.0.1:8000 user@vps`, затем http://127.0.0.1:8000.
