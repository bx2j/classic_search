"""저장된 예당 본문에서 시간/가격을 재파싱한다. 재크롤링 없음."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from concert_watch import config, store
from concert_watch.sources import sac

conn = store.connect(config.DB_PATH)
rows = conn.execute(
    "select id, program, time_info, price from performances "
    "where source='sac' and program is not null and program != ''").fetchall()

updated = t = pr = 0
for r in rows:
    f = sac.parse_fields(r["program"])
    sets, args = [], []
    if f.get("time_info") and not (r["time_info"] or "").strip():
        sets.append("time_info=?"); args.append(f["time_info"]); t += 1
    if f.get("price") and not (r["price"] or "").strip():
        sets.append("price=?"); args.append(f["price"]); pr += 1
    if sets:
        args.append(r["id"])
        conn.execute("update performances set " + ",".join(sets) + " where id=?", args)
        updated += 1
conn.commit()

def cnt(where):
    return conn.execute("select count(*) from performances where " + where).fetchone()[0]

print(f"예당 {len(rows)}건 재파싱 -> {updated}건 갱신 (시간 {t} / 가격 {pr})")
have = cnt("time_info is not null and time_info != ''")
print("전체 time_info:", have, "/", cnt("1=1"))
