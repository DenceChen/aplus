"""审计 1：DB 里真实的交易日历长什么样。

两趟：
  A) 全目录 parquet 元数据扫描 -> 复刻 panel.py 的 `ref = max(daily_map, key=len)`
     找出回测面板真正用的那一只参考股。
  B) 分层抽样读 date 列 -> 抽并集日历。

只读。不写任何生产文件。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/airboom/opencode/aplus")
DAILY = ROOT / "data" / "master" / "daily"
OUT = ROOT / "scripts" / "audit_calendar"


def scan_all_rows() -> list[tuple[str, int]]:
    """只读 parquet metadata 拿行数，不读列。"""
    out = []
    for p in sorted(DAILY.glob("*.parquet")):
        try:
            n = pq.ParquetFile(p).metadata.num_rows
        except Exception as exc:  # noqa: BLE001
            print(f"UNREADABLE {p.stem}: {exc}", file=sys.stderr)
            n = -1
        out.append((p.stem, n))
    return out


def pick_stratified_sample(all_rows: list[tuple[str, int]]) -> list[str]:
    """50 只大盘 + 面板参考股。"""
    codes = [c for c, n in all_rows if n > 0]
    sh = sorted(c for c in codes if c.startswith("60") or c.startswith("68"))
    sz = sorted(c for c in codes if c.startswith("00") or c.startswith("30"))
    sample = sh[:25] + sz[:25]
    return sample


def main():
    print("== A) 扫描全部 parquet 行数 ==", flush=True)
    all_rows = scan_all_rows()
    json.dump(all_rows, open(OUT / "rowcounts.json", "w"))

    # 复刻 panel.py:655  ref = max(daily_map.values(), key=len)
    # daily_map 来自 sorted(glob) -> dict 顺序 = 文件名字典序 -> max() 取首个最大值
    best = max((n for _, n in all_rows), default=-1)
    ref = next(c for c, n in all_rows if n == best)
    print(f"panel.py 参考股 ref = {ref}  (rows={best})")

    sample = pick_stratified_sample(all_rows)
    sample = [ref] + [c for c in sample if c != ref]
    print(f"抽样 N = {len(sample)}（50 只大盘 + ref）")
    json.dump({"ref": ref, "ref_rows": best, "sample": sample},
              open(OUT / "sample_frame.json", "w"), indent=2)

    print("\n== B) 读 date 列 ==", flush=True)
    per_stock: dict[str, list[str]] = {}
    for c in sample:
        p = DAILY / f"{c}.parquet"
        try:
            col = pq.read_table(p, columns=["date"]).column("date").to_pylist()
        except Exception as exc:  # noqa: BLE001
            print(f"  {c}: FAIL {exc}")
            continue
        ds = sorted({str(x)[:10] for x in col if x is not None})
        per_stock[c] = ds
        print(f"  {c}: {len(ds)} 行 {ds[0] if ds else '-'} .. {ds[-1] if ds else '-'}")

    # ref 的日历 = 面板日期轴
    ref_dates = per_stock[ref]
    union = sorted({d for ds in per_stock.values() for d in ds})
    json.dump({"ref": ref, "ref_dates": ref_dates, "union": union,
               "per_stock_counts": {c: len(d) for c, d in per_stock.items()}},
              open(OUT / "db_calendar.json", "w"))

    print(f"\nref 日历      : {len(ref_dates)} 天  {ref_dates[0]} .. {ref_dates[-1]}")
    print(f"50 只并集日历 : {len(union)} 天  {union[0]} .. {union[-1]}")
    extra = sorted(set(union) - set(ref_dates))
    print(f"并集里有、ref 里没有的日期: {len(extra)}")
    for d in extra:
        holders = [c for c, ds in per_stock.items() if d in set(ds)]
        print(f"  {d}  仅 {len(holders)}/{len(per_stock)} 只: {holders[:6]}")

    by_year = defaultdict(int)
    for d in union:
        by_year[d[:4]] += 1
    print("\n== 并集日历 年度交易日数 ==")
    for y in sorted(by_year):
        print(f"  {y}: {by_year[y]}")

    by_year_ref = defaultdict(int)
    for d in ref_dates:
        by_year_ref[d[:4]] += 1
    print("\n== ref（面板日期轴）年度交易日数 ==")
    for y in sorted(by_year_ref):
        print(f"  {y}: {by_year_ref[y]}")


if __name__ == "__main__":
    main()
