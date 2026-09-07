"""concert-watch CLI.

  python -m concert_watch sync            수집 + 곡목 보강 + 관심목록 매칭 + 알림
  python -m concert_watch matches         현재 매칭 전체 보기 (알림 안 보냄)
  python -m concert_watch search 브람스    로컬 DB 자유 검색
  python -m concert_watch slack-test      웹훅 점검
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import argparse
import logging
import re
from difflib import SequenceMatcher
import sys

from . import config, net, notify, store
from .match import load_watches, find_matches, normalize, haystack
from .sources import kopis, lotte, sac

log = logging.getLogger("concert_watch")

# 수집 단계에서는 아무것도 버리지 않는다.
#
# 처음엔 "예당/롯데는 전용 소스가 있으니 KOPIS 쪽 중복을 버리자"고 했는데 틀렸다.
# 롯데 자체 캘린더는 기획공연만 싣고 대관공연을 빠뜨린다 (자체 35건 vs KOPIS 46건).
# 실제로 '라쉬코프스키 라흐마니노프 협주곡 전곡'이 대관이라 자체 API에 없었다.
# 그래서 전부 저장하고, 겹치는 건 조회 시점에 합친다.

# 같은 공연이면 이 순서로 대표를 고른다 (곡목을 가진 쪽이 우선).
_SOURCE_RANK = {"sac": 0, "lotte": 1, "kopis": 2}


# 같은 공연으로 볼 제목 유사도 하한.
# 실측: 같은 공연 0.44~1.00 / 다른 공연 0.00~0.06 이라 사이가 넉넉하다.
#   "KBS교향악단 제830회 정기연주회" vs "제830회 KBS교향악단 정기연주회"  -> 0.71 (어순)
#   "2026 서울시향 최수열의..."      vs "서울시향 최수열의..."             -> 1.00 (연도 접두)
#   "한국 가곡의 밤"                 vs "한국 가곡의 밤: 우리 가곡..."      -> 1.00 (부제)
#   "롯콘 마티네 <대니 구...>"       vs "제830회 KBS교향악단..."           -> 0.05 (다른 공연)
TITLE_SIM = 0.4


def _venue_key(venue: str) -> str:
    """공연장 이름을 병합용으로 정규화. '예술의전당 [서울]' -> '예술의전당'."""
    v = re.sub(r"\s*\[[^\]]*\]\s*$", "", venue or "").strip()
    return normalize(v)


def _same_concert(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:          # 한쪽이 다른 쪽을 통째로 포함
        return True
    return SequenceMatcher(None, na, nb).ratio() >= TITLE_SIM


def _dedupe(rows):
    """같은 공연이면 한 건으로 합친다. 고르는 게 아니라 **필드별로 병합**한다.

    소스마다 아는 게 다르기 때문이다.
      - 예술의전당: 곡목이 있다 (상세 크롤링)
      - 롯데콘서트홀: 캘린더 API에 시간·출연진 필드가 아예 없다
      - KOPIS: 곡목은 없지만 출연진/시간(dtguidance)/가격을 준다
    하나만 고르면 롯데 공연에서 KOPIS가 아는 시간을 통째로 버리게 된다.

    제목 앞부분만 비교하면 어순이 바뀌거나 부제가 붙은 경우를 놓친다.
    그래서 (공연장, 날짜)로 묶은 뒤 제목 유사도로 다시 나눈다. 날짜·장소만으로
    합치면 안 된다. 같은 홀에서 하루에 마티네와 정기연주회가 같이 열린다.
    """
    buckets: dict[tuple, list] = {}
    for r in rows:
        buckets.setdefault((_venue_key(r["venue"]), r["date_from"] or ""), []).append(r)

    merged_rows = []
    for members in buckets.values():
        clusters: list[list] = []
        for r in members:
            for c in clusters:
                if _same_concert(c[0]["title"] or "", r["title"] or ""):
                    c.append(r)
                    break
            else:
                clusters.append([r])

        for cluster in clusters:
            # 대표(곡목을 가진 쪽 우선)가 id/url/제목을 제공한다
            primary = sorted(
                cluster,
                key=lambda r: (not bool(r["program"]), _SOURCE_RANK.get(r["source"], 9)),
            )[0]
            merged = dict(primary)
            for other in cluster:
                if other is primary:
                    continue
                for col in other.keys():
                    if not merged.get(col) and other[col]:
                        merged[col] = other[col]
            merged_rows.append(merged)
    return merged_rows


def _by_date(rows):
    """중복 제거 후 날짜순. **표시용 정렬은 항상 이것.**

    처음엔 관심 지역을 정렬 1순위로 뒀는데 잘못이었다. 목록이
    서울 전부 -> 경기 전부 -> 인천 ... 순으로 나와서 날짜가 앞뒤로 튀었다.
    무엇을 보러 갈지 고를 때 필요한 건 시간순이다. 지역 우선순위는
    보강 크롤링 예산을 어디에 먼저 쓸지(_by_region)에만 쓴다.
    """
    return sorted(
        _dedupe(rows),
        key=lambda r: (r["date_from"] or "9999", r["venue"] or "", r["title"] or ""),
    )


def _by_region(rows):
    """관심 지역 먼저. 곡목 보강 순서를 정할 때만 쓴다(표시용 아님)."""
    return sorted(rows, key=lambda r: (config.region_rank(r["area"]),
                                       r["date_from"] or ""))


def _warn_unconfigured() -> None:
    """설정이 비어 알림이 안 나가는 상황을 조용히 넘기지 않는다.

    .env.example 에 SLACK_BOT_TOKEN 을 빠뜨린 적이 있는데, 그대로 배포하면
    콘솔로만 출력되고 슬랙은 조용해서 한참 뒤에야 알게 된다.
    """
    if not config.KOPIS_KEY:
        log.warning("KOPIS_KEY 가 비어 있습니다 - 전국 수집 없이 공연장 직접 수집만 합니다.")
    if not (config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL) and not config.SLACK_WEBHOOK:
        log.warning(
            "슬랙 설정이 없어 콘솔로만 출력합니다. .env 에 "
            "SLACK_BOT_TOKEN + SLACK_CHANNEL(예: #classic_search) 또는 "
            "SLACK_WEBHOOK 을 넣으세요."
        )
    elif config.SLACK_BOT_TOKEN and not config.SLACK_CHANNEL:
        log.warning("SLACK_BOT_TOKEN 은 있는데 SLACK_CHANNEL 이 비어 있습니다 "
                    "(예: SLACK_CHANNEL=#classic_search).")


def cmd_sync(args) -> int:
    _warn_unconfigured()
    conn = store.connect(config.DB_PATH)
    days = args.days or config.LOOKAHEAD_DAYS

    if not config.KOPIS_KEY:
        log.warning("KOPIS_KEY 없음 - 예술의전당만 수집합니다.")
    else:
        print(f"[1/4] KOPIS 전국 클래식 공연 수집 (향후 {days}일)...")
        rows = kopis.list_performances(config.KOPIS_KEY, days=days)
        n, u = store.upsert(conn, rows)
        print(f"      {len(rows)}건 (신규 {n} / 갱신 {u})")

    print(f"[2/4] 공연장 직접 수집 (향후 {days}일)...")
    for label, mod in (("예술의전당", sac), ("롯데콘서트홀", lotte)):
        try:
            vrows = mod.list_performances(days=days)
            n, u = store.upsert(conn, vrows)
            print(f"      {label}: {len(vrows)}건 (신규 {n} / 갱신 {u})")
        except Exception as e:  # noqa: BLE001
            log.warning("      %s 수집 실패: %s", label, e)

    print("[3/4] 곡목·출연진 보강 (관심 지역 우선)...")
    pending = _by_region(store.needs_program(conn))
    sac_pending = [r for r in pending if r["source"] == "sac"]
    # 출연진이든 예매링크든 아직 없으면 상세를 한 번 더 받는다
    kopis_pending = [r for r in pending if r["source"] == "kopis"
                     and not (r["cast_names"] and r["ticket_url"])]
    limit = args.limit
    done = 0
    for row in sac_pending[:limit]:
        try:
            text = sac.fetch_program(row["native_id"])
            if text:
                store.set_program(conn, row["id"], text, "sac:show_view",
                                  core=sac.program_core(text))
                extra = sac.parse_fields(text)
                merged = dict(row)
                merged.update({k: v for k, v in extra.items() if v and k in store.FIELDS})
                store.upsert(conn, [merged])
                done += 1
        except Exception as e:  # noqa: BLE001
            log.debug("SAC 보강 실패 %s: %s", row["id"], e)
    kdone = 0
    for row in kopis_pending[:limit]:
        try:
            d = kopis.detail(config.KOPIS_KEY, row["native_id"])
            if d:
                merged = dict(row)
                merged.update({k: v for k, v in d.items() if v and k in store.FIELDS})
                store.upsert(conn, [merged])
                kdone += 1
        except Exception as e:  # noqa: BLE001
            log.debug("KOPIS 보강 실패 %s: %s", row["id"], e)
    print(f"      예당 곡목 {done}건, KOPIS 상세 {kdone}건 보강")

    print("[4/4] 관심목록 매칭...")
    watches = load_watches(config.WATCHLIST_PATH)
    rows = _by_date(store.all_performances(conn, upcoming_only=True))
    hits = find_matches(rows, watches)
    if args.resend:
        fresh = list(hits)          # 이미 보낸 것도 다시 보낸다 (표시 형식이 바뀐 경우 등)
        print(f"      매칭 {len(hits)}건 (--resend: 전부 재발송)")
    else:
        fresh = [(r, w) for r, w in hits
                 if not store.already_notified(conn, r["id"], w.name)]
        print(f"      매칭 {len(hits)}건 (신규 {len(fresh)}건)")

    if fresh and not args.dry_run:
        notify.send(fresh, bot_token=config.SLACK_BOT_TOKEN,
                    channel=config.SLACK_CHANNEL, webhook=config.SLACK_WEBHOOK)
        for r, w in fresh:
            store.mark_notified(conn, r["id"], w.name)
    elif fresh:
        notify.to_console(fresh)
        print("(--dry-run: 알림 기록을 남기지 않았습니다)")
    else:
        print("      새로 알릴 공연 없음")
    return 0


def cmd_matches(args) -> int:
    conn = store.connect(config.DB_PATH)
    watches = load_watches(config.WATCHLIST_PATH)
    rows = _by_date(store.all_performances(conn, upcoming_only=not args.all))
    hits = find_matches(rows, watches)
    if args.weekend:
        hits = [(r, w) for r, w in hits if notify.is_weekend(r)]
    notify.to_console(hits)
    return 0


def cmd_search(args) -> int:
    conn = store.connect(config.DB_PATH)
    needle = normalize(args.query)
    rows = _by_date(store.all_performances(conn, upcoming_only=not args.all))
    found = [r for r in rows if needle in normalize(haystack(r, full=True))]
    if args.weekend:
        found = [r for r in found if notify.is_weekend(r)]
    print(f"'{args.query}' → {len(found)}건\n")
    for r in found:
        venue, hall = (r["venue"] or "").strip(), (r["hall"] or "").strip()
        where = venue if (not hall or hall == venue) else f"{venue} {hall}"
        region = " " if config.is_priority(r["area"]) else "·"
        wk = notify.WEEKEND_MARK_TTY if notify.is_weekend(r) else " "
        when = notify._with_weekday(r["date_from"])
        print(f" {region}{wk} {when}  {where:<24} {r['title'][:44]}")
        if r["cast_names"]:
            print(f"{'':14}출연: {r['cast_names'][:80]}")
        print(f"{'':14}{r['url']}")
    return 0


def cmd_slack_test(args) -> int:
    channel = args.channel or config.SLACK_CHANNEL
    msg = "🎹 concert-watch 연결 테스트입니다. 이 메시지가 보이면 정상입니다."
    if config.SLACK_BOT_TOKEN:
        if not channel:
            print("채널이 지정되지 않았습니다. .env에 SLACK_CHANNEL=#채널명 을 넣거나 "
                  "--channel '#채널명' 으로 지정하세요.")
            return 1
        ok, err = notify.post_message(config.SLACK_BOT_TOKEN, channel, text=msg)
        print(f"봇 토큰 전송 -> {channel}: " + ("성공" if ok else f"실패 ({err})"))
        if not ok:
            print("  not_in_channel  : 비공개 채널입니다. 채널에서 /invite @봇이름")
            print("  channel_not_found: 채널명을 확인하세요 (#없이 이름만도 됩니다)")
            print("  missing_scope    : OAuth & Permissions에서 chat:write 추가 후 재설치")
        return 0 if ok else 1
    if config.SLACK_WEBHOOK:
        ok = notify.to_slack_webhook(config.SLACK_WEBHOOK, [])
        print("웹훅 설정됨 (빈 알림은 전송하지 않습니다)")
        return 0
    print("SLACK_BOT_TOKEN 도 SLACK_WEBHOOK 도 .env에 없습니다.")
    return 1


def _force_utf8() -> None:
    """윈도우 콘솔 기본 코드페이지(cp949)로는 한글/이모지 출력이 깨진다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_doctor(args) -> int:
    """설정·DB·네트워크·슬랙을 한 번에 점검한다.

    '로그가 비었다', '되는 것 같은데 알림이 안 온다' 같은 상황에서
    어디가 끊겼는지 한 화면으로 보려고 만들었다.
    """
    import os
    import sqlite3
    from datetime import datetime

    ok = True

    def line(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK ' if good else 'FAIL'}] {label:<22} {detail}")

    print("── 환경 ──")
    print(f"  python      {sys.version.split()[0]}  ({sys.executable})")
    print(f"  작업 디렉터리 {config.ROOT}")
    print(f"  시간대       {datetime.now().astimezone().tzname()}  "
          f"(현재 {datetime.now():%Y-%m-%d %H:%M})")
    print(f"  TZ 환경변수  {os.environ.get('TZ', '(미설정)')}")

    print("── 설정 ──")
    line(".env 파일", (config.ROOT / ".env").exists(), str(config.ROOT / ".env"))
    line("KOPIS_KEY", bool(config.KOPIS_KEY),
         f"{config.KOPIS_KEY[:6]}…" if config.KOPIS_KEY else "비어 있음")
    slack_ready = bool(config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL) or bool(config.SLACK_WEBHOOK)
    line("슬랙 설정", slack_ready,
         f"채널={config.SLACK_CHANNEL or '(없음)'} "
         f"토큰={'있음' if config.SLACK_BOT_TOKEN else '없음'} "
         f"웹훅={'있음' if config.SLACK_WEBHOOK else '없음'}")
    line("watchlist.yml", config.WATCHLIST_PATH.exists(),
         f"{len(load_watches(config.WATCHLIST_PATH))}개 항목"
         if config.WATCHLIST_PATH.exists() else "없음")

    print("── 데이터베이스 ──")
    exists = config.DB_PATH.exists()
    size = config.DB_PATH.stat().st_size // 1024 if exists else 0
    line("concerts.db", exists, f"{size:,}KB" if exists else "아직 없음(첫 실행 전이면 정상)")
    if exists:
        c = sqlite3.connect(config.DB_PATH)
        q = lambda w: c.execute("select count(*) from performances where " + w).fetchone()[0]
        try:
            print(f"         공연 {q('1=1')}건 / 곡목 {q(chr(34) + 'program_core' + chr(34) + ' is not null and program_core != ' + chr(39) + chr(39))}건")
        except sqlite3.Error as e:
            print(f"         (조회 실패: {e})")
        n = c.execute("select count(*) from notified").fetchone()[0]
        print(f"         알림 기록 {n}건")

    print("── 네트워크 ──")
    for name, url in (("KOPIS", "http://www.kopis.or.kr/"),
                      ("예술의전당", "https://www.sac.or.kr/"),
                      ("롯데콘서트홀", "https://www.lotteconcerthall.com/"),
                      ("슬랙", "https://slack.com/api/api.test")):
        try:
            r = net._session.get(url, timeout=15)
            line(name, r.status_code < 500, f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            line(name, False, f"{type(e).__name__}: {str(e)[:60]}")

    if config.SLACK_BOT_TOKEN:
        print("── 슬랙 인증 ──")
        try:
            r = net._session.post("https://slack.com/api/auth.test",
                                  headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
                                  timeout=15)
            d = r.json()
            line("auth.test", bool(d.get("ok")),
                 f"team={d.get('team')} bot={d.get('user')}" if d.get("ok") else d.get("error"))
        except Exception as e:  # noqa: BLE001
            line("auth.test", False, str(e)[:60])

    print()
    print("결과:", "이상 없음" if ok else "위 FAIL 항목을 확인하세요")
    return 0 if ok else 1


def main(argv=None) -> int:
    _force_utf8()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="concert_watch", description="공연 곡목 추적기")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="수집 + 보강 + 매칭 + 알림")
    s.add_argument("--days", type=int, default=None, help="수집 기간(일)")
    s.add_argument("--limit", type=int, default=200, help="1회 보강 상한")
    s.add_argument("--dry-run", action="store_true", help="알림 기록을 남기지 않음")
    s.add_argument("--resend", action="store_true",
                   help="이미 보낸 공연도 다시 발송 (표시 형식이 바뀌었을 때)")
    s.set_defaults(func=cmd_sync)

    m = sub.add_parser("matches", help="현재 매칭 보기")
    m.add_argument("--all", action="store_true", help="지난 공연도 포함")
    m.add_argument("--weekend", action="store_true", help="주말·공휴일 공연만")
    m.set_defaults(func=cmd_matches)

    q = sub.add_parser("search", help="로컬 DB 자유 검색")
    q.add_argument("query")
    q.add_argument("--all", action="store_true", help="지난 공연도 포함")
    q.add_argument("--weekend", action="store_true", help="주말·공휴일 공연만")
    q.set_defaults(func=cmd_search)

    d = sub.add_parser("doctor", help="설정·DB·네트워크·슬랙 한 번에 점검")
    d.set_defaults(func=cmd_doctor)

    t = sub.add_parser("slack-test", help="슬랙 연결 점검")
    t.add_argument("--channel", default="", help="보낼 채널 (예: '#공연알림')")
    t.set_defaults(func=cmd_slack_test)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
