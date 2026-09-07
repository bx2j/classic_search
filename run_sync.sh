#!/usr/bin/env bash
# concert-watch 일일 동기화 (리눅스/cron 용).
# cron은 PATH도 작업 디렉터리도 물려주지 않으므로 여기서 직접 잡는다.
#
# 로그 리다이렉션을 맨 위에서 건다. 마지막 줄에만 걸면 그 전에 죽었을 때
# (파이썬 못 찾음 등) 로그가 텅 비어서 원인을 알 수 없다. 실제로 겪었다.

set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || {
    echo "ERROR: 스크립트 위치를 찾을 수 없습니다" >&2
    exit 1
}
cd "$APP_DIR" || { echo "ERROR: cd $APP_DIR 실패" >&2; exit 1; }

LOG="$APP_DIR/sync.log"

# 로그가 너무 커지면 한 번 접는다 (cron이 매일 붙이므로)
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 5000000 ]; then
    mv -f "$LOG" "$LOG.1"
fi

exec >> "$LOG" 2>&1        # ← 이 줄 이후 모든 출력(오류 포함)이 로그로 간다

echo "================ $(date '+%Y-%m-%d %H:%M:%S %Z') 시작 ================"

# 서버가 UTC면 date.today()가 하루 어긋나 수집 구간이 밀린다.
export TZ="Asia/Seoul"
export PYTHONIOENCODING="utf-8"

PY="$APP_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "가상환경이 없습니다($PY). 시스템 python3 를 찾습니다."
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "ERROR: 파이썬을 찾을 수 없습니다."
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
echo "python : $PY ($("$PY" --version 2>&1))"
echo "workdir: $APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "경고: .env 가 없습니다. cp .env.example .env 후 값을 채우세요."
fi

"$PY" -m concert_watch sync
rc=$?
echo "================ 종료 코드 $rc ================"
exit $rc
