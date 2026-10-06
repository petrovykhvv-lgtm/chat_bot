#!/usr/bin/env bash
# Установка и обновление чат-бота на Ubuntu/Debian одним скриптом. Запуск от root.
# Безопасно запускать повторно: скрипт обновляет код, зависимости и сервисы.
#
#   curl -fsSL https://raw.githubusercontent.com/petrovykhvv-lgtm/chat_bot/main/deploy/install.sh -o install.sh
#   less install.sh      # посмотрите, что он делает
#   bash install.sh
#
# Переменные (необязательно): APP_DIR, REPO_URL, BRANCH.
# Ключ AI Studio скрипт спросит скрыто, если он ещё не задан (нужен терминал).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/chatbot}"
REPO_URL="${REPO_URL:-https://github.com/petrovykhvv-lgtm/chat_bot.git}"
BRANCH="${BRANCH:-main}"
APP_USER="chatbot"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\n==> %s\n' "$*"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root (sudo bash install.sh)"; exit 1; }

say "1/6 Системные пакеты"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl >/dev/null

say "2/6 Python 3.13"
if ! command -v python3.13 >/dev/null; then
  [ -x /opt/uv-tool/bin/uv ] || { python3 -m venv /opt/uv-tool && /opt/uv-tool/bin/pip install -q uv; }
  UV_PYTHON_INSTALL_DIR=/opt/python /opt/uv-tool/bin/uv python install 3.13
  ln -sf "$(ls -d /opt/python/cpython-3.13*/bin/python3.13 | head -1)" /usr/local/bin/python3.13
fi
python3.13 --version

say "3/6 Пользователь и код"
id "$APP_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d "$APP_DIR" "$APP_USER"
mkdir -p "$APP_DIR" && chown "$APP_USER:$APP_USER" "$APP_DIR"
git config --global --get-all safe.directory | grep -qx "$APP_DIR" || git config --global --add safe.directory "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  as_app git -C "$APP_DIR" pull -q --ff-only origin "$BRANCH"
else
  as_app git clone -q -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
as_app git -C "$APP_DIR" log --oneline | head -1

say "4/6 Окружение и зависимости"
[ -d "$APP_DIR/.venv" ] || as_app python3.13 -m venv "$APP_DIR/.venv"
as_app "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "5/6 Настройки (.env)"
cd "$APP_DIR"
[ -f .env ] || as_app cp .env.example .env
chown "$APP_USER:$APP_USER" .env && chmod 600 .env
if ! grep -q '^AI_API_KEY=.\{10,\}' .env; then
  if [ -t 0 ]; then
    read -rs -p "AI_API_KEY (Enter — пропустить, консультант будет в демо-режиме): " KEY; echo
    if [ -n "$KEY" ]; then sed -i "s|^AI_API_KEY=.*|AI_API_KEY=$KEY|" .env; fi
    unset KEY
  else
    echo "AI_API_KEY не задан и терминала нет: консультант будет в демо-режиме (задайте ключ в $APP_DIR/.env)."
  fi
fi
mkdir -p data && chown "$APP_USER:$APP_USER" data && chmod 750 data

say "6/6 Сервисы"
install -m 644 deploy/chatbot.service deploy/chatbot-backup.service deploy/chatbot-backup.timer /etc/systemd/system/
if [ "$APP_DIR" != "/opt/chatbot" ]; then
  sed -i "s|/opt/chatbot|$APP_DIR|g" /etc/systemd/system/chatbot.service /etc/systemd/system/chatbot-backup.service
fi
systemctl daemon-reload
systemctl enable chatbot chatbot-backup.timer >/dev/null 2>&1
systemctl restart chatbot
systemctl start chatbot-backup.timer

PORT="$(grep '^PORT=' .env | cut -d= -f2 || true)"; PORT="${PORT:-8000}"
for _ in $(seq 1 15); do
  curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break || sleep 1
done
echo
echo "Сервис:   $(systemctl is-active chatbot)"
echo "Health:   $(curl -s "http://127.0.0.1:$PORT/api/health" || echo недоступен)"
echo "Таймер копий базы: $(systemctl is-active chatbot-backup.timer)"
echo "Логи:     journalctl -u chatbot -n 50 --no-pager"
