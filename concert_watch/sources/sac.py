"""예술의전당 — 곡목(레퍼토리)을 얻는 곳.

KOPIS가 주지 않는 곡목이 상세페이지에 텍스트로 들어있다. 실측 예:
    라흐마니노프｜피아노 협주곡 제2번 c단조 Op. 18
    S. Rachmaninoff  Piano Concerto No. 2 in C minor, Op. 18

목록은 /site/main/show/dataList (JSON, 서버 렌더링) 로 전수 열거한다.
show_list 페이지 자체는 JS로 그려지므로 긁어봐야 소용없다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import html as _html
import json
import re
from datetime import date, timedelta

from .. import net

LIST_URL = "https://www.sac.or.kr/site/main/show/dataList"
VIEW_URL = "https://www.sac.or.kr/site/main/show/show_view?SN={sn}"
REFERER = {"Referer": "https://www.sac.or.kr/site/main/show/show_list"}

PAGE_SIZE = 100


def list_performances(*, days: int = 180, start: date | None = None) -> list[dict]:
    start = start or date.today()
    end = start + timedelta(days=days)
    out: list[dict] = []
    page = 1
    while True:
        params = {
            "cp": page,
            "PAGE_SIZE": PAGE_SIZE,
            "BEGIN_DATE": start.isoformat(),
            "END_DATE": end.isoformat(),
        }
        data = json.loads(net.get_text(LIST_URL, params=params, headers=REFERER))
        items = (data.get("paging") or {}).get("result") or []
        for it in items:
            sn = it.get("SN")
            if not sn:
                continue
            out.append({
                "id": f"sac:{sn}",
                "source": "sac",
                "native_id": str(sn),
                "title": _html.unescape(it.get("PROGRAM_SUBJECT") or "").strip(),
                "venue": "예술의전당",
                "hall": it.get("PLACE_NAME") or "",
                "area": "서울특별시",
                "date_from": (it.get("BEGIN_DATE") or "").replace(".", "-"),
                "date_to": (it.get("END_DATE") or "").replace(".", "-"),
                "genre": it.get("CATEGORY_SECONDARY_NAME") or "",
                "state": it.get("SALE_STATE_CODE_NAME") or "",
                "price": it.get("PRICE_INFO") or "",
                "url": VIEW_URL.format(sn=sn),
            })
        total_page = ((data.get("paging") or {}).get("totalPage")) or 1
        if page >= total_page or not items:
            break
        page += 1
        if page > 60:           # 폭주 방지
            break
    return out


# 상세페이지 상단은 "라벨 다음 줄에 값" 구조다.
#   기간 / 2026-10-06(화) / 시간 / 19:30 / 장소 / 콘서트홀 / 관람시간(분) / 100
_LABELS = {
    "시간": "time_info",
    "장소": "hall",
    "가격": "price",
}

_STRIP_TAGS = re.compile(r"(?is)<(script|style|noscript).*?</\1>")
_TAG = re.compile(r"<[^>]+>")


def _page_text(html_src: str) -> str:
    t = _STRIP_TAGS.sub(" ", html_src)
    t = _TAG.sub("\n", t)
    t = _html.unescape(t)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    dedup: list[str] = []
    for ln in lines:                     # 연속 중복 제거
        if not dedup or dedup[-1] != ln:
            dedup.append(ln)
    return "\n".join(dedup)


def parse_fields(body: str) -> dict:
    """본문 상단의 라벨-값 쌍에서 시간/장소/가격 등을 뽑는다.

    KOPIS는 dtguidance로 '화요일(19:30)'을 주지만 예당 목록 API는 시간을 주지
    않는다. 상세페이지에는 있으므로 곡목을 긁는 김에 같이 가져온다.
    """
    out: dict = {}
    lines = [ln.strip() for ln in body.split(chr(10))]
    for i, ln in enumerate(lines[:80]):        # 상단 정보 블록만 본다
        key = _LABELS.get(ln)
        if not key or key in out:
            continue
        for nxt in lines[i + 1: i + 3]:        # 빈 줄 하나 정도는 건너뛴다
            if nxt and nxt not in _LABELS:
                out[key] = nxt
                break
    return out


def fetch_program(sn: str | int, *, max_chars: int = 8000) -> str:
    """상세페이지에서 본문(곡목 포함)을 뽑는다.

    곡목만 정규식으로 골라내려 하지 않고 본문을 통째로 저장한다.
    표기가 공연마다 제각각이라, 매칭은 정규화된 문자열 포함검사로
    처리하는 편이 훨씬 안정적이다 (match.py 참고).
    """
    body = _page_text(net.get_text(VIEW_URL.format(sn=sn)))

    # 페이지 상단 전역 내비게이션을 잘라낸다. 본문은 마지막 'MY PICK' 뒤에서 시작한다.
    i = body.rfind("MY PICK")
    if i >= 0:
        body = body[i + len("MY PICK"):]

    # 하단 푸터 제거
    for stop in ("이용약관", "개인정보처리방침", "COPYRIGHT", "관람평("):
        j = body.find(stop)
        if j > 400:
            body = body[:j]
            break

    return body.strip()[:max_chars]


# 곡목 구간의 시작/끝 표지. 상세페이지는 대체로
#   ... 작품소개 → [프로그램/PROGRAM] → 곡목 → [PROFILE/출연/약력] → 연주자 소개 ...
# 순서로 되어 있다.
_PROG_START = ("PROGRAM", "Program", "프로그램", "공연·전시 상세 정보", "작품소개")
# 곡목 뒤에 붙는 것들. 한국 공연 상세페이지는 대개
#   곡목 → "*프로그램은 ... 변경될 수 있습니다" 안내문 → 연주자 약력
# 순서라, 그 안내문이 가장 신뢰할 만한 경계다.
_PROG_END = (
    "*프로그램은", "※프로그램은", "※ 프로그램은", "프로그램은 연주자",
    "변경될 수 있습니다",
    "PROFILE", "Profile", "프로필", "약력", "출연자 소개", "아티스트 소개",
    "출연진 소개",
)

# 곡목 목록이 이보다 길면 구간 분리에 실패한 것으로 본다.
# 실제 곡목은 길어야 2천자 안쪽이고, 그보다 크면 약력까지 딸려온 것이다.
_PROG_MAX = 2500


def program_core(body: str) -> str:
    """본문에서 곡목 구간만 잘라낸다.

    연주자 약력에 적힌 과거 연주/음반 이력이 곡목으로 오인되는 것을 막는다.
    실제로 겪은 오탐:
        "2019년 BBC 심포니와 연주한 라흐마니노프 피아노 협주곡 2번 음반이 발매되어"
    이건 프로필이지 이번 공연의 곡목이 아니다.

    표지를 못 찾으면 빈 문자열을 돌려준다. 그 경우 매칭은 본문 전체로 폴백한다.
    """
    if not body:
        return ""
    start = -1
    for mark in _PROG_START:
        i = body.find(mark)
        if i >= 0 and (start < 0 or i < start):
            start = i
    if start < 0:
        return ""
    seg = body[start:]
    end = len(seg)
    for mark in _PROG_END:
        j = seg.find(mark)
        if 0 < j < end:
            end = j
    core = seg[:end].strip()
    # 경계를 못 찾아 본문을 통째로 들고 온 경우는 곡목으로 인정하지 않는다.
    # (빈 값을 주면 match.py가 본문 전체로 폴백하는데, 그러면 오탐이 늘어난다.
    #  차라리 앞부분만 남긴다 - 곡목은 거의 항상 섹션 맨 앞에 나온다.)
    return core[:_PROG_MAX]
