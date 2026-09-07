"""SQLite 저장소.

수집은 필터링하지 않는다. 클래식 공연을 통째로 쌓아두고
관심목록 매칭은 조회 시점에 한다. 그래야 나중에 관심사가 바뀌어도
과거 데이터를 다시 긁을 필요가 없다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS performances (
    id             TEXT PRIMARY KEY,   -- "kopis:PF300176" 처럼 source 접두
    source         TEXT NOT NULL,
    native_id      TEXT NOT NULL,
    title          TEXT NOT NULL,
    venue          TEXT,               -- 공연시설 (예술의전당)
    hall           TEXT,               -- 홀 (콘서트홀)
    area           TEXT,
    date_from      TEXT,               -- YYYY-MM-DD
    date_to        TEXT,
    time_info      TEXT,
    cast_names     TEXT,
    crew_names     TEXT,
    genre          TEXT,
    state          TEXT,
    price          TEXT,
    poster         TEXT,
    url            TEXT,
    ticket_url     TEXT,               -- 예매처 직행 링크 (KOPIS relateurl)
    program        TEXT,               -- 상세페이지 본문 전체 (자유검색용)
    program_core   TEXT,               -- 그 중 곡목 구간만 (관심목록 매칭용)
    program_source TEXT,
    first_seen     TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_perf_date  ON performances(date_from);
CREATE INDEX IF NOT EXISTS idx_perf_venue ON performances(venue);

CREATE TABLE IF NOT EXISTS notified (
    perf_id     TEXT NOT NULL,
    watch_name  TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (perf_id, watch_name)
);
"""

FIELDS = [
    "id", "source", "native_id", "title", "venue", "hall", "area",
    "date_from", "date_to", "time_info", "cast_names", "crew_names",
    "genre", "state", "price", "poster", "url", "ticket_url",
    "program", "program_core", "program_source",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """이미 만들어진 DB에 새 컬럼을 더한다. CREATE TABLE IF NOT EXISTS로는 안 붙는다."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(performances)")}
    for col in FIELDS:
        if col not in have:
            conn.execute(f"ALTER TABLE performances ADD COLUMN {col} TEXT")
    conn.commit()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert(conn: sqlite3.Connection, rows: Iterable[dict]) -> tuple[int, int]:
    """저장. (신규 건수, 갱신 건수) 반환.

    병합 규칙: **비어 있는 값으로 기존 값을 덮어쓰지 않는다.**

    목록 API는 출연진/가격/시간/곡목을 주지 않는다. 그래서 단순 UPDATE로 쓰면
    sync를 돌 때마다 보강 단계가 애써 채운 값이 전부 날아가고, 다음 실행에서
    limit 만큼만 다시 채워진다 (실제로 출연진 수가 553 -> 71 -> 248로 널뛰었다).
    소스가 값을 모른다는 것과 값이 없다는 것은 다르므로, 모르면 두는 게 맞다.
    """
    new = updated = 0
    for row in rows:
        rec = {f: row.get(f) for f in FIELDS}
        cur = conn.execute(
            f"SELECT {','.join(FIELDS)} FROM performances WHERE id=?", (rec["id"],)
        )
        existing = cur.fetchone()
        if existing is None:
            conn.execute(
                f"INSERT INTO performances ({','.join(FIELDS)}, first_seen, updated_at) "
                f"VALUES ({','.join('?' * len(FIELDS))}, ?, ?)",
                [rec[f] for f in FIELDS] + [_now(), _now()],
            )
            new += 1
        else:
            for col in FIELDS:
                if not rec.get(col) and existing[col]:
                    rec[col] = existing[col]
            sets = ",".join(f"{f}=?" for f in FIELDS if f != "id")
            conn.execute(
                f"UPDATE performances SET {sets}, updated_at=? WHERE id=?",
                [rec[f] for f in FIELDS if f != "id"] + [_now(), rec["id"]],
            )
            updated += 1
    conn.commit()
    return new, updated


def set_program(conn: sqlite3.Connection, perf_id: str, program: str, source: str,
                core: str = "") -> None:
    conn.execute(
        "UPDATE performances SET program=?, program_core=?, program_source=?, updated_at=? "
        "WHERE id=?",
        (program, core, source, _now(), perf_id),
    )
    conn.commit()


def all_performances(conn: sqlite3.Connection, upcoming_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM performances"
    args: list = []
    if upcoming_only:
        sql += " WHERE date_to >= ?"
        args.append(datetime.now().strftime("%Y-%m-%d"))
    sql += " ORDER BY date_from"
    return conn.execute(sql, args).fetchall()


def needs_program(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """곡목이 아직 없는 예정 공연."""
    return conn.execute(
        "SELECT * FROM performances WHERE (program IS NULL OR program='') "
        "AND date_to >= ? ORDER BY date_from",
        (datetime.now().strftime("%Y-%m-%d"),),
    ).fetchall()


def already_notified(conn: sqlite3.Connection, perf_id: str, watch: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM notified WHERE perf_id=? AND watch_name=?", (perf_id, watch)
    ).fetchone() is not None


def mark_notified(conn: sqlite3.Connection, perf_id: str, watch: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO notified (perf_id, watch_name, notified_at) VALUES (?,?,?)",
        (perf_id, watch, _now()),
    )
    conn.commit()
