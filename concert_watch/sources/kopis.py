"""KOPIS 공연예술통합전산망 — 전국 공연의 뼈대를 얻는다.

주는 것 : 공연명, 공연시설/홀, 지역, 기간, 시간, 출연진, 가격, 예매링크
안 주는 것: 곡목. `sty`(소개글)는 실측 결과 항상 비어 있었다.
           곡목은 sources/sac.py 같은 공연장별 보강 단계가 채운다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import re
import html as _html
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from .. import net

BASE = "http://www.kopis.or.kr/openApi/restful/pblprfr"

# 장르 코드
GENRE_CLASSICAL = "CCCA"   # 서양음악(클래식)
GENRE_KOREAN = "CCCC"      # 한국음악(국악)

ROWS = 100                 # API 최대


def _text(el, tag: str) -> str:
    node = el.find(tag)
    if node is None or node.text is None:
        return ""
    return _html.unescape(node.text).strip()


def _split_venue(fcltynm: str) -> tuple[str, str]:
    """'예술의전당 (콘서트홀)' -> ('예술의전당', '콘서트홀')

    KOPIS는 시설명 끝에 '[서울]' 같은 지역 꼬리표를 붙이기도 한다. 그대로 두면
    '예술의전당'과 '예술의전당 [서울]'이 다른 공연장으로 취급돼 중복이 남는다.
    """
    v = re.sub(r"\s*\[[^\]]*\]\s*$", "", fcltynm or "").strip()
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", v)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return v, ""


def _iso(d: str) -> str:
    """'2026.10.06' -> '2026-10-06'"""
    return d.replace(".", "-") if d else ""


def list_performances(key: str, *, days: int = 180,
                      genre: str = GENRE_CLASSICAL,
                      start: date | None = None) -> list[dict]:
    """기간 내 공연 목록. 페이지를 끝까지 돈다."""
    start = start or date.today()
    end = start + timedelta(days=days)
    out: list[dict] = []
    page = 1
    while True:
        params = {
            "service": key,
            "stdate": start.strftime("%Y%m%d"),
            "eddate": end.strftime("%Y%m%d"),
            "cpage": page,
            "rows": ROWS,
            "shcate": genre,
        }
        xml = net.get_text(BASE, params=params)
        root = ET.fromstring(xml)
        dbs = root.findall("db")
        for db in dbs:
            mt20id = _text(db, "mt20id")
            if not mt20id:
                continue
            venue, hall = _split_venue(_text(db, "fcltynm"))
            out.append({
                "id": f"kopis:{mt20id}",
                "source": "kopis",
                "native_id": mt20id,
                "title": _text(db, "prfnm"),
                "venue": venue,
                "hall": hall,
                "area": _text(db, "area"),
                "date_from": _iso(_text(db, "prfpdfrom")),
                "date_to": _iso(_text(db, "prfpdto")),
                "genre": _text(db, "genrenm"),
                "state": _text(db, "prfstate"),
                "poster": _text(db, "poster"),
                "url": f"https://www.kopis.or.kr/mob/db/pblprfrView.do?mt20Id={mt20id}",
            })
        if len(dbs) < ROWS:
            break
        page += 1
        if page > 100:          # 폭주 방지
            break
    return out


def detail(key: str, mt20id: str) -> dict:
    """상세 조회. 출연진/시간/가격/예매링크를 채운다."""
    xml = net.get_text(f"{BASE}/{mt20id}", params={"service": key})
    root = ET.fromstring(xml)
    db = root.find("db")
    if db is None:
        return {}
    venue, hall = _split_venue(_text(db, "fcltynm"))
    links = [_html.unescape(e.text or "") for e in db.iter("relateurl")]
    return {
        "title": _text(db, "prfnm"),
        "venue": venue,
        "hall": hall,
        "area": _text(db, "area"),
        "date_from": _iso(_text(db, "prfpdfrom")),
        "date_to": _iso(_text(db, "prfpdto")),
        "time_info": _text(db, "dtguidance"),
        "cast_names": _text(db, "prfcast"),
        "crew_names": _text(db, "prfcrew"),
        "price": _text(db, "pcseguidance"),
        "state": _text(db, "prfstate"),
        "genre": _text(db, "genrenm"),
        "poster": _text(db, "poster"),
        "ticket_links": [u for u in links if u.startswith("http")],
        "ticket_url": _pick_ticket(links),
    }


# 예매처 우선순위. 실제 예매가 되는 곳을 앞에 둔다.
_TICKET_HOSTS = ("interpark", "yes24", "ticketlink", "melon", "maketicket", "sacticket")


def _pick_ticket(links: list[str]) -> str:
    """여러 링크 중 예매처를 고른다. 없으면 첫 http 링크."""
    http = [u for u in links if u.startswith("http")]
    for host in _TICKET_HOSTS:
        for u in http:
            if host in u.lower():
                return u
    return http[0] if http else ""
