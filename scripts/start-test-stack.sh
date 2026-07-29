#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_FILE="$PROJECT_DIR/compose.test.yml"

cd "$PROJECT_DIR"
docker compose -f "$COMPOSE_FILE" up -d --build
docker compose -f "$COMPOSE_FILE" exec -T app-backend \
  python manage.py create-admin \
  --username admin \
  --display-name "Docker 测试管理员" \
  --email admin@example.test \
  --password TestAdmin123 \
  --if-not-exists

printf '\n测试服务已启动：\n'
printf '  应用：http://localhost:8080\n'
printf '  邮件：http://localhost:8025\n'
printf '  Jitsi：http://localhost:8000\n'
printf '  管理员：admin@example.test / TestAdmin123\n'
