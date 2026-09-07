"""알림 전송.

세 가지 경로를 지원하고, 설정된 것 중 앞의 것을 쓴다.
  1) 봇 토큰(SLACK_BOT_TOKEN + SLACK_CHANNEL) - chat.postMessage
  2) 웹훅(SLACK_WEBHOOK)
  3) 콘솔

토큰과 웹훅 URL은 그 자체가 자격증명이다. 로그에 찍지 않는다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import json
import logging
import time
import re
from datetime import date

from . import net
from . import holidays
from .match import _col

log = logging.getLogger(__name__)

POST_URL = "https://slack.com/api/chat.postMessage"

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
WEEKEND_MARK = "🟠"       # 슬랙: 주말
HOLIDAY_MARK = "🔴"       # 슬랙: 공휴일
WEEKEND_MARK_TTY = "★"    # 콘솔: 주말
HOLIDAY_MARK_TTY = "◆"    # 콘솔: 공휴일


def _to_date(iso: str):
    try:
        y, m, d = (int(x) for x in (iso or "").split("-"))
        return date(y, m, d)
    except (ValueError, AttributeError):
        return None


_DT_ONE = re.compile(r"^[월화수목금토일]요일\s*\(([^)]+)\)$")


def _clean_time(time_info: str, single_day: bool) -> str:
    """KOPIS dtguidance는 '금요일(19:30)' 형태라 날짜 옆 요일과 겹친다.

    하루짜리 공연이면 요일을 떼고 시각만 남긴다.
    여러 날 공연('금요일(19:00), 토요일(19:00)')은 요일이 정보이므로 그대로 둔다.
    """
    t = (time_info or "").strip()
    if single_day:
        m = _DT_ONE.match(t)
        if m:
            return m.group(1).strip()
    return t


def _with_weekday(iso: str) -> str:
    """'2026-10-06' -> '2026-10-06(화)'. 요일은 소스가 주든 말든 날짜에서 계산한다."""
    d = _to_date(iso)
    return f"{iso}({_WEEKDAYS[d.weekday()]})" if d else (iso or "?")


# 이보다 긴 공연은 주말/공휴일 표시를 하지 않는다.
# 몇 달짜리 전시·장기공연은 어차피 모든 주말과 공휴일에 걸쳐서, 표시해봐야
# 정보가 안 된다. 실제로 4개월짜리 전시가 '신정 공연'으로 잡히는 걸 봤다.
MAX_SPAN_FOR_MARK = 7


def _span(row):
    """공연이 걸치는 날짜들. 너무 긴 공연은 앞 60일만 본다."""
    start = _to_date(row["date_from"])
    if start is None:
        return []
    end = _to_date(row["date_to"]) or start
    if end < start:
        end = start
    days = (end - start).days
    if days > MAX_SPAN_FOR_MARK:
        return []
    return [date.fromordinal(start.toordinal() + i) for i in range(days + 1)]


def day_kind(row) -> tuple[str, str]:
    """(종류, 공휴일이름). 종류는 'holiday' | 'weekend' | ''.

    공휴일이 주말보다 우선한다. 개천절이 토요일이면 '개천절'로 보여주는 게
    '토요일'보다 정보량이 많기 때문이다.
    여러 날 공연은 하루라도 걸리면 그 종류로 친다. 평일 저녁에 못 가는
    사람에게 중요한 건 '갈 수 있느냐'지 시작일이 무슨 요일이냐가 아니다.
    """
    days = _span(row)
    for d in days:
        nm = holidays.name(d)
        if nm:
            return "holiday", nm
    for d in days:
        if d.weekday() >= 5:
            return "weekend", ""
    return "", ""


def is_weekend(row) -> bool:
    """주말 또는 공휴일. 이름은 주말이지만 '쉬는 날' 전체를 뜻한다."""
    return day_kind(row)[0] != ""


def _marks(row, tty: bool) -> tuple[str, str]:
    """(표시기호, 공휴일이름)"""
    kind, nm = day_kind(row)
    if kind == "holiday":
        return (HOLIDAY_MARK_TTY if tty else HOLIDAY_MARK), nm
    if kind == "weekend":
        return (WEEKEND_MARK_TTY if tty else WEEKEND_MARK), ""
    return "", ""


def _fmt_line(row, *, mark: str = "", holiday: str = "") -> str:
    single = not row["date_to"] or row["date_to"] == row["date_from"]
    when = _with_weekday(row["date_from"])
    if not single:
        when += f" ~ {_with_weekday(row['date_to'])}"
    if holiday:                       # 날짜 바로 뒤 - "2026-10-03(토) 개천절 19:30"
        when += f" {holiday}"
    t = _clean_time(row["time_info"], single)
    if t:
        when += f" {t[:40]}"
    if mark:
        when = f"{mark} {when}"

    # 공연시설과 홀 이름이 같은 경우(롯데콘서트홀)는 한 번만 쓴다
    venue, hall = (row["venue"] or "").strip(), (row["hall"] or "").strip()
    where = venue if (not hall or hall == venue) else f"{venue} {hall}"

    bits = [f"*{row['title']}*", f"{when} · {where}"]
    if row["cast_names"]:
        bits.append(f"출연: {row['cast_names'][:120]}")
    if row["price"]:
        bits.append(f"가격: {row['price'][:100]}")
    if row["state"]:
        bits.append(f"상태: {row['state']}")
    return chr(10).join(bits)


def _blocks(hits) -> list[dict]:
    kinds = [day_kind(r)[0] for r, _ in hits]
    wk, hl = kinds.count("weekend"), kinds.count("holiday")
    head = f"🎹 새 공연 {len(hits)}건"
    tags = []
    if wk:
        tags.append(f"{WEEKEND_MARK} 주말 {wk}건")
    if hl:
        tags.append(f"{HOLIDAY_MARK} 공휴일 {hl}건")
    if tags:
        head += "  (" + " · ".join(tags) + ")"
    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": head, "emoji": True},
    }]
    for row, watch in hits[:40]:          # 슬랙 블록 상한(50) 여유
        blocks.append({"type": "divider"})
        mark, hol = _marks(row, tty=False)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"`{watch.name}`" + chr(10)
                             + _fmt_line(row, mark=mark, holiday=hol)},
        })
        buttons = []
        tick = _col(row, "ticket_url")
        if tick:
            buttons.append({
                "type": "button",
                "style": "primary",
                "text": {"type": "plain_text", "text": "🎫 예매하기"},
                "url": tick,
            })
        if row["url"]:
            buttons.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "공연 정보"},
                "url": row["url"],
            })
        if buttons:
            blocks.append({"type": "actions", "elements": buttons})
    return blocks


def to_console(hits) -> None:
    if not hits:
        print("새로 알릴 공연이 없습니다.")
        return
    bar = "=" * 70
    kinds = [day_kind(r)[0] for r, _ in hits]
    wk, hl = kinds.count("weekend"), kinds.count("holiday")
    tags = []
    if wk:
        tags.append(f"{WEEKEND_MARK_TTY} 주말 {wk}건")
    if hl:
        tags.append(f"{HOLIDAY_MARK_TTY} 공휴일 {hl}건")
    print(chr(10) + bar)
    print(f"🎹 새 공연 {len(hits)}건" + ("   (" + " · ".join(tags) + ")" if tags else ""))
    print(bar)
    for row, watch in hits:
        mark, hol = _marks(row, tty=True)
        print(f"{chr(10)}[{watch.name}]")
        print(_fmt_line(row, mark=mark, holiday=hol).replace("*", ""))
        tick = _col(row, "ticket_url")
        if tick:
            print(f"  🎫 예매: {tick}")
        if row["url"]:
            print(f"  → {row['url']}")
    print()


def post_message(token: str, channel: str, *, text: str,
                 blocks: list[dict] | None = None,
                 retries: int = 3) -> tuple[bool, str]:
    """chat.postMessage 한 번. (성공여부, 오류코드)를 돌려준다.

    슬랙은 채널당 초당 1건 수준으로 제한한다. 연속 발송하면 429/ratelimited가
    나므로 Retry-After를 존중해 재시도한다. 실제로 재발송을 연달아 하다가
    조용히 실패한 적이 있다.
    """
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    last = ""
    for attempt in range(retries):
        try:
            r = net._session.post(
                POST_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=25,
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "2") or 2)
                log.warning("슬랙 레이트리밋. %d초 후 재시도", wait)
                time.sleep(wait)
                last = "ratelimited"
                continue
            d = r.json()
            if d.get("ok"):
                return True, ""
            last = str(d.get("error") or "")
            if last == "ratelimited":
                time.sleep(2 ** attempt + 1)
                continue
            return False, last          # 스코프/채널 문제는 재시도해도 소용없다
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return False, last


def to_slack_bot(token: str, channel: str, hits) -> bool:
    if not hits:
        return True
    ok, err = post_message(token, channel,
                           text=f"새 공연 {len(hits)}건", blocks=_blocks(hits))
    if not ok:
        log.error("슬랙 전송 실패(%s). 흔한 원인: "
                  "not_in_channel=비공개 채널이면 봇을 초대해야 함, "
                  "channel_not_found=채널명 오타, "
                  "missing_scope=chat:write 권한 없음", err)
    return ok


def to_slack_webhook(webhook: str, hits) -> bool:
    if not hits:
        return True
    payload = {"text": f"새 공연 {len(hits)}건", "blocks": _blocks(hits)}
    try:
        r = net._session.post(webhook, data=json.dumps(payload).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, timeout=20)
        if r.status_code != 200 or r.text.strip() != "ok":
            log.error("슬랙 웹훅 실패: %s", r.status_code)
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.error("슬랙 웹훅 예외: %s", e)
        return False


def send(hits, *, bot_token: str = "", channel: str = "", webhook: str = "") -> None:
    """설정된 경로 중 앞의 것을 쓰고, 실패하면 콘솔로 떨어뜨린다."""
    if bot_token and channel and to_slack_bot(bot_token, channel, hits):
        print(f"슬랙({channel})으로 {len(hits)}건 전송했습니다.")
        return
    if webhook and to_slack_webhook(webhook, hits):
        print(f"슬랙 웹훅으로 {len(hits)}건 전송했습니다.")
        return
    to_console(hits)
