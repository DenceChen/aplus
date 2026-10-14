"""审计 1c：DB 日历 vs 上交所权威日历。

权威源：akshare tool_trade_date_hist_sina（上交所官方交易日历，1990-12-19 起）。
DB 源：data/master/daily 的 ref 股日期轴 + 全量普查。

三类差异：
  FALSE_POSITIVE  DB 有、权威无  -> 非交易日被当成交易日（危险）
  FALSE_NEGATIVE  权威有、DB 无  -> 交易日漏掉
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import akshare as ak

OUT = Path("/Users/airboom/opencode/aplus/scripts/audit_calendar")

auth_df = ak.tool_trade_date_hist_sina()
auth = {str(d)[:10] for d in auth_df["trade_date"]}
print(f"权威日历：{len(auth)} 天  {min(auth)} .. {max(auth)}")

db = json.load(open(OUT / "db_calendar.json"))
ref_dates = set(db["ref_dates"])
union = set(db["union"])
per_date = dict(json.load(open(OUT / "per_date_counts.json")))

db_max = max(union)
auth_win = {d for d in auth if min(union) <= d <= db_max}
print(f"同窗口 [{min(union)}, {db_max}] 权威交易日 = {len(auth_win)}")

for name, cal in (("面板日期轴(ref=000001.SZ)", ref_dates), ("50 只并集", union)):
    fp = sorted(cal - auth_win)
    fn = sorted(auth_win - cal)
    print(f"\n===== {name} | {len(cal)} 天 =====")
    print(f"  FALSE POSITIVE（非交易日被当交易日）= {len(fp)}")
    for d in fp:
        print(f"    {d}  成交股票数={per_date.get(d,'N/A')}")
    print(f"  FALSE NEGATIVE（漏掉的交易日）      = {len(fn)}")
    for d in fn:
        print(f"    {d}")

print("\n== 同窗口逐日权威/DB 计数 ==")
ay = defaultdict(int)
for d in auth_win:
    ay[d[:4]] += 1
dy = defaultdict(int)
for d in union:
    dy[d[:4]] += 1
print(f"{'年':<6}{'权威':>6}{'DB':>6}{'差':>6}")
for y in sorted(ay):
    print(f"{y:<6}{ay[y]:>6}{dy.get(y,0):>6}{dy.get(y,0)-ay[y]:>6}")

print("\n== 权威日历里、DB 完全没有任何股票交易的日期（跨全量普查）==")
all_db = {d for d, n in per_date.items() if n > 0}
fp_all = sorted(all_db - auth_win)
fn_all = sorted(auth_win - all_db)
print(f"全量 5460 只并集 FALSE POSITIVE = {len(fp_all)}")
for d in fp_all:
    print(f"    {d}  成交股票数={per_date[d]}")
print(f"全量 5460 只并集 FALSE NEGATIVE = {len(fn_all)}")
for d in fn_all:
    print(f"    {d}")

json.dump({"fp_ref": sorted(ref_dates - auth_win),
           "fn_ref": sorted(auth_win - ref_dates),
           "fp_all": fp_all, "fn_all": fn_all},
          open(OUT / "diff.json", "w"), indent=2)
