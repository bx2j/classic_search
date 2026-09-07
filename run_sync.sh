#!/usr/bin/env bash
# concert-watch 일일 동기화 (리눅스/cron 용).
# cron은 PATH도 작업 디렉터리도 물려주지 않으므로 여기서 직접 잡는다.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# 서버가 UTC면 date.today()가 하루 어긋나 수집 구간이 밀린다.
export TZ="Asia/Seoul"

PY="${APP_DIR}/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

exec "$PY" -m concert_watch sync >> "$APP_DIR/sync.log" 2>&1
