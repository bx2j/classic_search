"""설정 로딩. .env 우선, 없으면 환경변수."""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

KOPIS_KEY = os.environ.get("KOPIS_KEY", "").strip()
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "").strip()
# 봇 토큰 방식. chat:write.public 스코프가 있으면 공개 채널은 초대 없이도 쓸 수 있다.
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "").strip()
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "180"))

# 관심 지역. 수집은 전국을 그대로 하고, 이 목록은 "우선순위"로만 쓴다.
#   - 곡목 보강 크롤링 예산을 이 지역에 먼저 쓴다
#   - 알림/조회 결과에서 위쪽에 배치한다
# 여기 없는 지역도 수집되고 알림도 간다. 순서만 뒤로 밀릴 뿐이다.
_DEFAULT_REGIONS = (
    "서울특별시,경기도,인천광역시,"          # 수도권
    "대전광역시,세종특별자치시,충청남도,충청북도"  # 대전·천안·청주 벨트
)
REGION_PRIORITY = [
    r.strip() for r in os.environ.get("REGION_PRIORITY", _DEFAULT_REGIONS).split(",")
    if r.strip()
]


def region_rank(area: str | None) -> int:
    """우선 지역이면 목록상 순번, 아니면 맨 뒤."""
    if not area:
        return len(REGION_PRIORITY) + 1
    for i, r in enumerate(REGION_PRIORITY):
        if r in area or area in r:
            return i
    return len(REGION_PRIORITY)


def is_priority(area: str | None) -> bool:
    return region_rank(area) < len(REGION_PRIORITY)

DB_PATH = ROOT / "concerts.db"
WATCHLIST_PATH = ROOT / "watchlist.yml"
