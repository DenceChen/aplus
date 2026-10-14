#!/usr/bin/env python
"""幸存者偏差审计（只读）。

方法：
  A. 股本表 shares/*.parquet 只有 21 个文件且**不含 delist_date** —— 这是本次
     审计的第一个发现（README/base.py 假设错了），改用 baostock 登记簿。
  B. 落盘 daily/*.parquet 的末根 K 线日期分布 vs 全库最新日。
  C. 与 baostock `query_stock_basic()` 权威登记簿对拍：退市股是否在池中、
     池中的"停更"股是否都能被登记簿解释。

用法：
    .venv/bin/python scripts/audit_survivorship/audit.py --online
    .venv/bin/python scripts/audit_survivorship/audit.py          # 离线
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DAILY = ROOT / "data" / "master" / "daily"
SHARES = ROOT / "data" / "master" / "shares"


# ── 权威登记簿 ──────────────────────────────────────────────────────────────

def fetch_registry(online: bool) -> pd.DataFrame:
    """baostock 上市/退市登记簿：type=1（股票），outDate 非空即已退市。

    注意：这不是「当前在市」列表 —— 实测含 status=0 的退市股。
    """
    if not online:
        raise SystemExit("--online required for registry")
    import baostock as bs
    import time

    # baostock 的 next() 会静默截断：一次拉全量 8995 行实测只有一次成功，
    # 重复调用会只回 278 行且 error_code 仍为 '0'。必须重试到拿到全量为止。
    best = None
    for attempt in range(1, 13):
        lg = bs.login()
        if lg.error_code != "0":
            time.sleep(3)
            continue
        rs = bs.query_stock_basic()
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        print(f"    registry attempt {attempt}: {len(rows)} rows", flush=True)
        if best is None or len(rows) > len(best):
            best = rows
            best_fields = rs.fields
        if len(best) >= 8000:
            break
        time.sleep(3)

    df = pd.DataFrame(best, columns=best_fields)
    df = df[df["type"] == "1"].copy()
    df["code"] = (df["code"].str.split(".").str[1] + "."
                  + df["code"].str.split(".").str[0].str.upper())
    df["delist_date"] = df["outDate"].replace("", None)
    return df[["code", "code_name", "ipoDate", "delist_date", "status"]]


# ── 池子侧 ─────────────────────────────────────────────────────────────────

def scan_pool() -> pd.DataFrame:
    """每个 daily parquet 的首/末 K 线 + 行数。"""
    recs = []
    for p in sorted(DAILY.glob("*.parquet")):
        code = p.stem
        try:
            df = pd.read_parquet(p, columns=["date"])
        except Exception as exc:  # noqa: BLE001
            recs.append({"code": code, "first": None, "last": None, "rows": 0,
                         "err": repr(exc)[:120]})
            continue
        d = pd.to_datetime(df["date"], errors="coerce")
        recs.append({
            "code": code,
            "first": None if d.isna().all() else str(d.min().date()),
            "last": None if d.isna().all() else str(d.max().date()),
            "rows": int(d.notna().sum()),
            "err": None,
        })
    return pd.DataFrame(recs)


def check_shares_dir() -> dict:
    """Method A：股本表到底带不带 delist_date。"""
    files = sorted(SHARES.glob("*.parquet"))
    cols = None
    with_out = []
    if files:
        df = pd.read_parquet(files[0])
        cols = list(df.columns)
        for p in files:
            d = pd.read_parquet(p)
            if "delist_date" in d.columns:
                with_out.append((p.stem, int(d["delist_date"].notna().sum())))
    return {"n_files": len(files), "files": [p.stem for p in files],
            "columns": cols, "with_delist_col": with_out}


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--stale-years", type=float, default=1.0)
    args = ap.parse_args()

    print("=" * 72)
    print("METHOD A — data/master/shares/*.parquet 是否带 delist_date")
    print("=" * 72)
    info = check_shares_dir()
    print(f"  文件数: {info['n_files']}")
    print(f"  列: {info['columns']}")
    print(f"  含 delist_date 列的文件: {info['with_delist_col'] or '无 —— 该表不是上市/退市登记簿'}")
    print("  结论: 股本表 = 巨潮股本变动时间序列，不能用于退市判定（见 shares.py:43 SHARE_COLUMNS）。")

    print()
    print("=" * 72)
    print("METHOD B/C — 全池扫描 + 权威登记簿对拍")
    print("=" * 72)
    pool = scan_pool()
    print(f"  daily parquet 总数: {len(pool)}  读盘失败: {int(pool['err'].notna().sum())}")
    maxdate = pool["last"].dropna().max()
    print(f"  全库最新交易日 (max last): {maxdate}")

    # 前缀分布
    pref = Counter(c[:2] for c in pool["code"])
    print(f"  代码前缀分布: {dict(sorted(pref.items(), key=lambda kv: -kv[1]))}")

    if args.online:
        reg = fetch_registry(True)
        reg.to_csv(ROOT / "scripts" / "audit_survivorship" / "registry.csv", index=False)
        print(f"\n  登记簿 type=1 总数: {len(reg)}")
        print(f"  其中已退市 (outDate 非空): {int(reg['delist_date'].notna().sum())}")
        print(f"  其中仍在市: {int(reg['delist_date'].isna().sum())}")

        in_pool = set(pool["code"])
        reg_codes = set(reg["code"])
        dele = reg[reg["delist_date"].notna()]
        dele_codes = set(dele["code"])

        print(f"\n  [对拍1] 池子 ∩ 登记簿 = {len(in_pool & reg_codes)}")
        print(f"          池子 - 登记簿 = {len(in_pool - reg_codes)}")
        print(f"          登记簿 - 池子 = {len(reg_codes - in_pool)}")
        orphans = sorted(in_pool - reg_codes)
        print(f"          池子中不在登记簿的代码: {len(orphans)}")
        print(f"          例: {orphans[:15]}")

        print(f"\n  [对拍2] 已退市代码在池中的: {len(dele_codes & in_pool)} / {len(dele_codes)}")
        missing = sorted(dele_codes - in_pool)
        print(f"          退市但缺文件: {len(missing)}")
        print(f"          例: {missing[:20]}")

        # 逐只核对退市股末行 vs 登记簿退市日
        pmap = pool.set_index("code")
        joined = dele[dele["code"].isin(in_pool)].copy()
        joined["file_last"] = joined["code"].map(pmap["last"])
        joined["ok"] = joined["file_last"] <= joined["delist_date"]
        print(f"\n  [对拍3] 退市股末根 K 线 <= 登记簿退市日: {int(joined['ok'].sum())} / {len(joined)}")
        bad = joined[~joined["ok"]]
        if len(bad):
            print(f"          不符者（应为空）:\n{bad[['code','code_name','delist_date','file_last']].to_string(index=False)}")

        # METHOD B 分布：按末根日期距 maxdate
        pool2 = pool[pool["last"].notna()].copy()
        pool2["lag_days"] = (pd.Timestamp(maxdate) - pd.to_datetime(pool2["last"])).dt.days
        thresh = int(args.stale_years * 365)
        stale = pool2[pool2["lag_days"] > thresh]
        print(f"\n  [METHOD B] 末根 K 线距 {maxdate} 超过 {args.stale_years} 年: {len(stale)} / {len(pool2)}"
              f" ({len(stale)/max(1,len(pool2)):.2%})")
        yr = pool2["last"].str[:4].value_counts().sort_index()
        print("  末根 K 线年份分布（近 12 年）:")
        for y, n in yr.items():
            if int(y) >= int(maxdate[:4]) - 12:
                mark = ""
                if int(y) in dele_codes_years(dele):
                    mark = "  <-- 池中含该年退市股" if False else ""
                print(f"    {y}: {n:5d}{mark}")

        print(f"\n  [METHOD C] 退市股按退市年份:")
        dy = dele["delist_date"].str[:4].value_counts().sort_index()
        for y, n in dy.items():
            if int(y) >= 2015:
                print(f"    {y}: {n}")

        # 停更但登记簿说仍在市 —— 才是"需要解释"的真问题
        live = reg[reg["delist_date"].isna()]
        stale_codes = set(stale["code"])
        unexplained = sorted(stale_codes - dele_codes)
        print(f"\n  [收尾] 末根停更 >{args.stale_years}年 且不在退市名单: {len(unexplained)}")
        print(f"          例: {unexplained[:20]}")

    else:
        st = pool[pool["last"].notna()]
        st = st.copy()
        st["lag_days"] = (pd.Timestamp(maxdate) - pd.to_datetime(st["last"])).dt.days
        thresh = int(args.stale_years * 365)
        print(f"\n  末根距 {maxdate} > {args.stale_years} 年: {(st['lag_days']>thresh).sum()} / {len(st)}")
        print("  (离线模式无登记簿对拍，加 --online 复跑)")


def dele_codes_years(dele):
    return set()


if __name__ == "__main__":
    main()