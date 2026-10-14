#!/usr/bin/env python3
"""抓取股本（总股本 / 流通股本 / A 股股本）到主库 `data/master/shares/`。

## 背景

基本面因子的第一块砖。`docs/factor_spec_cn.md` §Q4：规模因子 = 收盘价 × 总股本，
BM = 归股东权益 / 总市值，ROE(TTM) / F-Score 也都拿市值当分母。现有 8 个因子
（`snapshot.FACTOR_COLS`）全是量价，算不出这些。

## 为什么落成独立的 `shares/` 表，而不是给 `master/daily` 加两列

股本是会变的（增发、回购、解禁）。手上有的是**时间序列**（巨潮的股本变动史），
只有一份快照。往 `master/daily` 上按日期展开列，等于要用「今天知道的股本」
去描述三年前的行情——那是未来函数。所以股本单独成表，每行自带生效日。

## 写入路径

复用 `LocalStore._upsert`（即 `master/{kind}/{code}.parquet` + `_merge_preserving_fidelity`），
不另发明一条写入路径。这样既拿到「空帧拒写」与「完整度只增不减」两道既有防线，
也避免重演 `docs/INCIDENTS_2026-10-11.md` 缺陷 18 那类降级写入把好数据削空的坑。

并发互斥复用 `bootstrap.lock`——股本表也写在 `data/master/` 下，
和 `run_daily` / `backfill_gaps` / `daily_update` / `repair_tail_cols` 同属一个库，
必须同一把锁。

用法：
    scripts/fetch_shares.py --dry-run --limit 50     # 试跑，不写库
    scripts/fetch_shares.py --limit 200              # 真写
    scripts/fetch_shares.py --diagnose 601988.SH    # 只跑 Q2 判定
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from aplus.sources.shares import (  # noqa: E402
    classify_turn_denominator,
    fetch_share_history,
    latest_snapshot,
)
from aplus.store import LocalStore  # noqa: E402
from aplus.write_lock import LockBusy, WriteLock  # noqa: E402

# 巨潮对连续请求敏感，这里给足间隔（与 backfill_factors 的 0.35s 同量级，略保守）
SLEEP = 0.5


def _daily_path(daily_dir: Path, code: str) -> Path:
    return daily_dir / f"{code}.parquet"


def _write_shares(store: LocalStore, code: str, history: pd.DataFrame) -> bool:
    """按既有写入路径落库。返回是否真的写进去了。

    空帧**拒写**（`LocalStore._upsert` 自身的规矩）：源挂了不能把已有数据洗掉。
    """
    if history is None or history.empty:
        return False
    store._upsert(store._master_path("shares", code), history)  # noqa: SLF001
    return True


def diagnose(daily_dir: Path, codes: list[str], start: str) -> None:
    """Q2：既有 `turn` 列的分母到底是总股本还是流通股本。

    做法是实测而非推理：取每只票最近一段日线，用 `volume × 100 / turn`
    反推出分母，再按时点 join 巨潮股本，看它落在哪条腿上。
    """
    print(f"\n{'代码':<12}{'n':>4}  {'隐含股本':>18} {'/总股本':>9} {'/流通':>9} {'/A股':>9}  判定")
    print("-" * 90)
    tally: dict[str, int] = {}
    for code in codes:
        dpath = _daily_path(daily_dir, code)
        if not dpath.exists():
            print(f"{code:<12}  无本地日线")
            continue
        daily = pd.read_parquet(dpath)
        daily = daily[(daily["date"] >= start) & daily["turn"].gt(0)].dropna(subset=["turn", "volume"])
        try:
            history = fetch_share_history(code, start, "2099-12-31")
        except Exception as exc:  # noqa: BLE001
            print(f"{code:<12}  股本抓取失败：{exc!r}"[:120])
            continue
        verdict = classify_turn_denominator(daily, history)
        implied = float((daily["volume"] * 100.0 / daily["turn"]).median()) if not daily.empty else float("nan")
        tally[verdict["verdict"]] = tally.get(verdict["verdict"], 0) + 1
        print(f"{code:<12}{verdict['n']:>4}  {implied:>18,.0f} "
              f"{verdict['ratio_to_total']:>9.4f} {verdict['ratio_to_float']:>9.4f} "
              f"{verdict['ratio_to_a']:>9.4f}  {verdict['verdict']}")
    print("\n合计：", tally if tally else "无")


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取股本到 data/master/shares/")
    ap.add_argument("--data-root", default=None, help="默认 <repo>/data")
    ap.add_argument("--start", default="2015-01-01", help="股本历史起点（默认 2015，够基本面因子用）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只；0 = 全部")
    ap.add_argument("--sleep", type=float, default=SLEEP)
    ap.add_argument("--diagnose", nargs="*", metavar="CODE",
                    help="只对这些代码跑 Q2 判定（不带代码则跑本地库全量抽样）")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写")
    args = ap.parse_args()

    root = Path(args.data_root) if args.data_root else Path(__file__).resolve().parent.parent / "data"
    store = LocalStore(root)
    daily_dir = store.root / "master" / "daily"
    codes = sorted(p.stem for p in daily_dir.glob("*.parquet"))
    if not codes:
        print(f"{daily_dir} 下没有日线文件，先跑 bootstrap_history。")
        return 1

    # ── Q2 判定模式（只读，不写库，不上锁）────────────────────────────────
    if args.diagnose is not None:
        targets = args.diagnose or codes[::max(1, len(codes) // 60)][:60]
        print(f"Q2 判定：{len(targets)} 只（{args.start} 起）")
        diagnose(daily_dir, targets, args.start)
        return 0

    if args.limit:
        codes = codes[: args.limit]
    print(f"目标股票：{len(codes)} 只｜股本历史起点：{args.start}")

    # 真落库时**先握锁再抓**：写库全程都在锁内，与 run_daily / backfill_gaps /
    # daily_update / repair_tail_cols 共用同一把 bootstrap.lock。
    lock = WriteLock(store.root / "bootstrap.lock")
    if args.dry_run:
        print("[dry-run] 不写库，不取写锁。")
    else:
        try:
            lock.acquire()
        except LockBusy as exc:
            print(f"[shares] {exc}\n换个时间再试；确认无人在跑之后才考虑手工清锁。")
            return 2

    written = skipped = failed = 0
    as_of_counter: dict[str, int] = {}
    try:
        if not args.dry_run:
            shares_dir = store.root / "master" / "shares"
            shares_dir.mkdir(parents=True, exist_ok=True)
            print(f"落库目录：{shares_dir}")

        for i, code in enumerate(codes, 1):
            try:
                history = fetch_share_history(code, args.start, "2099-12-31")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if failed <= 10:
                    print(f"[{i}] {code}: {exc!r}", flush=True)
                time.sleep(args.sleep)
                continue

            if history.empty:
                skipped += 1
            else:
                snap = latest_snapshot(history)
                as_of = snap["as_of"] if snap else "?"
                as_of_counter[as_of] = as_of_counter.get(as_of, 0) + 1
                total = snap["total_shares"]
                if args.dry_run:
                    written += 1
                else:
                    written += _write_shares(store, code, history)
                if i <= 10:
                    print(f"[{i}] {code}: {len(history)} 期，最新 as_of={as_of}，"
                          f"总股本={total:,.0f} 股（{total / 1e8:.2f} 亿股）", flush=True)
            time.sleep(args.sleep)
            if i % 100 == 0:
                print(f"... {i}/{len(codes)}（写入 {written} / 无数据 {skipped} / 失败 {failed}）", flush=True)
    finally:
        if not args.dry_run:
            lock.release()

    verb = "将写入" if args.dry_run else "已写入"
    print(f"\n{verb} {written} 只；无股本数据 {skipped} 只；抓取失败 {failed} 只")
    if as_of_counter:
        print(f"数据时点分布（as_of = 最后一次股本变动生效日）：{dict(sorted(as_of_counter.items()))}")
        print(f"最新时点：{max(as_of_counter)}")

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据。去掉 --dry-run 才会真正落库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())