"""관심목록 매칭.

설계 의도: 곡목을 구조화(작곡가/작품번호 파싱)하지 않는다.
그건 "아무 곡이나 검색"을 만들 때나 필요한 비용이고,
"내가 지정한 것들"을 추적하는 데는 정규화된 문자열 포함검사면 충분하다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml


def normalize(s: str | None) -> str:
    """공백 제거 + 소문자 + 유니코드 정규화.

    '협주곡 제2번' / '협주곡제2번' / '협주곡  제 2 번' 을 같게 만든다.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    return re.sub(r"\s+", "", s)


LINE_SEP = chr(10)   # 개행. 이스케이프 없이 쓰려고 상수로 둔다

DEFAULT_WINDOW = 80   # 정규화 후 문자 수


def _spans(text: str, group: list[str]) -> list[tuple[int, int]]:
    """그룹 내 어떤 표기든 등장한 모든 위치."""
    out: list[tuple[int, int]] = []
    for term in group:
        t = normalize(term)
        if not t:
            continue
        start = 0
        while (i := text.find(t, start)) != -1:
            out.append((i, i + len(t)))
            start = i + 1
    return sorted(out)


# 매칭 구간 근처에 이런 말이 있으면 곡목이 아니라 연주자 약력일 가능성이 높다.
# ("...라흐마니노프 협주곡 전곡을 편곡해 완주했으며", "...협주곡 음반이 발매되어")
# 곡목 구간 분리(sources/sac.py의 program_core)가 실패한 페이지를 위한 2차 방어선.
BIO_MARKERS = (
    "음반", "발매", "녹음", "완주", "역임", "수상", "데뷔", "졸업", "교수",
    "초청", "협연했", "연주했", "선보였", "활동하고", "재직",
)


@dataclass
class Watch:
    name: str
    groups: list[list[str]]        # AND(그룹) of OR(값)
    window: int = DEFAULT_WINDOW   # 0이면 근접 조건 없음(문서 전체)
    exclude: tuple[str, ...] = BIO_MARKERS

    def matches(self, haystack: str) -> bool:
        """모든 그룹이 매칭되고, 서로 `window` 안에 모여 있어야 한다.

        근접 조건이 없으면 이런 오탐이 난다 (실제로 겪은 사례):
            Robert Schumann - Arabeske, Op. 18
            Sergei Rachmaninoff - Two Pieces for Piano Six Hands
        '라흐마니노프'와 'Op.18'이 문서에 각각 존재하지만 서로 다른 곡이다.
        곡목은 한 줄에 '작곡가 - 작품명 Op.번호' 형태로 붙어 나오므로
        좁은 창 안에 함께 있는지를 보면 대부분 걸러진다.
        """
        h = normalize(haystack)
        per_group = [_spans(h, g) for g in self.groups]
        if any(not spans for spans in per_group):
            return False
        if len(per_group) == 1 or not self.window:
            return True

        w = self.window
        anchors, others = per_group[0], per_group[1:]
        for a_start, a_end in anchors:
            lo, hi = a_start - w, a_end + w
            if not all(any(s < hi and e > lo for s, e in spans) for spans in others):
                continue
            if self._looks_like_bio(h, lo, hi):
                continue
            return True
        return False

    def _looks_like_bio(self, h: str, lo: int, hi: int) -> bool:
        if not self.exclude:
            return False
        chunk = h[max(0, lo): hi]
        return any(normalize(m) in chunk for m in self.exclude)


def load_watches(path: Path) -> list[Watch]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Watch] = []
    for item in data.get("watches") or []:
        groups = [g if isinstance(g, list) else [g] for g in item.get("all", [])]
        if not groups:
            continue
        window = item.get("window", DEFAULT_WINDOW)
        ex = item.get("exclude", BIO_MARKERS)
        if ex is False or ex is None:
            ex = ()
        out.append(Watch(name=str(item["name"]), groups=groups,
                         window=int(window), exclude=tuple(ex)))
    return out


def _col(row, name: str):
    """sqlite3.Row는 없는 컬럼에 IndexError를 낸다. 스키마가 달라도 죽지 않게."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def haystack(row, *, full: bool = False) -> str:
    """매칭 대상 텍스트.

    기본은 곡목 구간(program_core)만 본다. 본문 전체를 쓰면 연주자 약력의
    과거 연주 이력까지 걸려서 오탐이 쏟아진다. 실제로 겪은 예:
        "2019년 BBC 심포니와 연주한 라흐마니노프 피아노 협주곡 2번 음반이 발매되어"
    이건 프로필이지 이번 공연의 곡목이 아니다.

    full=True는 자유검색(search) 전용으로, 본문 전체를 대상으로 한다.
    """
    if full:
        body = _col(row, "program") or ""
    else:
        body = _col(row, "program_core") or ""
        if not body:                       # 곡목 구간을 못 찾은 경우만 폴백
            body = _col(row, "program") or ""
    parts = [
        _col(row, "title"), _col(row, "cast_names"), _col(row, "crew_names"),
        body, _col(row, "venue"), _col(row, "hall"), _col(row, "genre"),
    ]
    return LINE_SEP.join(p for p in parts if p)


def collapse(hits):
    """(공연, 관심항목) 쌍들을 공연 단위로 합친다.

    한 공연이 여러 관심항목에 걸릴 수 있다. 예: '라흐마니노프 in 뉴욕'은
    협주곡 2번과 3번을 모두 연주해서 두 항목에 걸린다. 매칭은 맞지만
    목록에 같은 공연이 두 줄로 나오면 안 된다.

    반환: [(공연, [관심항목, ...]), ...]  - 입력 순서 유지
    """
    order: list = []
    groups: dict = {}
    for row, w in hits:
        key = row["id"]
        if key not in groups:
            groups[key] = (row, [])
            order.append(key)
        names = groups[key][1]
        if all(x.name != w.name for x in names):
            names.append(w)
    return [groups[k] for k in order]


def find_matches(rows, watches: list[Watch]) -> list[tuple[object, Watch]]:
    hits = []
    for row in rows:
        text = haystack(row)
        for w in watches:
            if w.matches(text):
                hits.append((row, w))
    return hits
