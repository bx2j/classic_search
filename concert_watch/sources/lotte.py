"""롯데콘서트홀.

월간 캘린더가 JSON API로 열려 있다:
    GET /product/performance/month/calendar?q=YYYY-MM   -> Tag.Weeks 안에 공연 dict

한계: 상세페이지(209KB)에 곡목이 텍스트로 없다. 프로그램이 포스터 이미지에만
들어있어서 OCR 없이는 못 뽑는다. 그래서 목록 레벨(제목/날짜/장소)까지만 채운다.
다만 롯데 공연명은 곡명을 품는 경우가 많아
    "[클래식 레볼루션 2026] 수원시향의 브람스 교향곡 제1번"
제목만으로도 매칭이 꽤 걸린다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import json
from datetime import date, timedelta

from .. import net

CAL_URL = "https://www.lotteconcerthall.com/product/performance/month/calendar"
REFERER = {"Referer": "https://www.lotteconcerthall.com/product/performance/month"}


def _months(start: date, days: int) -> list[str]:
    """기간이 걸치는 모든 YYYY-MM."""
    end = start + timedelta(days=days)
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _iso(v: str | None) -> str:
    """'20260824' / '2026/08/24' / '2026-08-24T..' 를 모두 'YYYY-MM-DD'로."""
    if not v:
        return ""
    d = v.strip().replace("/", "-")[:10]
    digits = d.replace("-", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return d


def _collect(node, acc: list[dict]) -> None:
    if isinstance(node, dict):
        if "PerformanceID" in node:
            acc.append(node)
        for v in node.values():
            _collect(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect(v, acc)


def list_performances(*, days: int = 180, start: date | None = None) -> list[dict]:
    start = start or date.today()
    rows: dict[str, dict] = {}
    for ym in _months(start, days):
        data = json.loads(net.get_text(CAL_URL, params={"q": ym}, headers=REFERER))
        found: list[dict] = []
        _collect((data.get("Tag") or {}).get("Weeks"), found)
        for it in found:
            pid = it.get("PerformanceID")
            if not pid:
                continue
            begin = _iso(it.get("PlayBeginDate"))
            end = _iso(it.get("PlayEndDate")) or begin
            rows[str(pid)] = {
                "id": f"lotte:{pid}",
                "source": "lotte",
                "native_id": str(pid),
                "title": (it.get("Title") or "").strip(),
                "venue": "롯데콘서트홀",
                "hall": (it.get("Place") or "").strip(),
                "area": "서울특별시",
                "date_from": begin,
                "date_to": end,
                "genre": it.get("CategoryName") or "",
                "state": it.get("ProductionTypeName") or "",
                "url": it.get("DetailsUrl") or f"https://www.lotteconcerthall.com/product/ko/performance/{pid}",
                "poster": it.get("ImageUrl") or "",
            }
    return list(rows.values())
