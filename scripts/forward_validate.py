"""前瞻验证：在**从未看过**的交易日上检验已冻结的候选。

## 它和赛马的区别只有一处

赛马在「已经看过」的历史窗口上裁决；前瞻验证在「还没发生」的交易日上裁决。
除此之外**复用同一套闸**（`oos_decision`），不另造标准。

候选集在 `data/reports/forward_registry.json` 里**冻结**——
要加新候选就开一份新注册并从那天起单独计时，
而不是把台账打开把表现差的那个删掉。

## 每次运行都会做什么

1. 读台账，取 `[注册日+隔离带, 最新交易日]` 的窗口
2. 每个候选跑一次 `evaluate_weights`（频率与缓冲按注册时的值）
3. 用**分块 t**（K 日调仓 → 块均值），因为逐日 t 在低频下虚高 1.0–2.0 倍
4. 样本不够就说不够（`MIN_TEST_PERIODS`），**不给看起来能用的 t 值**
5. 结果追加写进 `forward_results.json`

## 不做什么

**不晋级。** 前瞻验证通过只说明「值得考虑」，
真正写 `weights.json` 仍要过一遍 `oos_decision` 的全部闸门（`N_GATES` 道），且由人工确认。
理由：前瞻窗口天然短，第一批样本几乎不可能一次就够判决力；
自动晋级会把「样本攒够了一点」误当成「已经验证过了」。

用法：
    .venv/bin/python scripts/forward_validate.py
    .venv/bin/python scripts/forward_validate.py --status-only
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve_race import (breadth_tstat, down_excess_tstat, excess_tstat,
                               race)
from aplus.forward import (FORWARD_EMBARGO_DAYS, forward_status, forward_window,
                       load_registry, needs_forward_panel,
                           merge_results)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent


def evaluate_factory(xs):
    def ev(_xs, w, dates, tn, bn, rebalance_every=1, buffer_multiple=None):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates,
                               rebalance_every=rebalance_every,
                               buffer_multiple=buffer_multiple)
        stats = dict(rolling_stats(res))
        t, n_obs = excess_tstat(res, rebalance_every=rebalance_every)
        stats["excess_t"] = t
        stats["excess_days"] = n_obs
        bt, _ = breadth_tstat(res, rebalance_every=rebalance_every)
        stats["breadth_t"] = bt
        dt, _ = down_excess_tstat(res, rebalance_every=rebalance_every)
        stats["down_t"] = dt
        stats["n_ranked"] = int(res.attrs.get("n_ranked", -1))
        return {"stats": stats, "objective": 0.0, "n": int(len(res))}
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--status-only", action="store_true",
                    help="只看攒够了多少样本，不建面板（快，几秒）")
    ap.add_argument("--ignore-run-status", action="store_true",
                    help="不检查当日全流程是否已成功（手工复算历史时用）")
    args = ap.parse_args()

    reports = args.data_root / "reports"
    rows = load_registry(reports)
    if not rows:
        print("前瞻台账是空的——先跑 scripts/register_forward_candidates.py")
        return 1

    # ── 当日全流程还没成功 → 跳过，不产出任何结果 ────────────────────
    # 2026-10-13 实测踩到的：每日任务 18:30 起、**20:21 才跑完**，
    # 而前瞻任务排在 19:45 ——那一刻数据还停在昨天，于是窗口 0 天、
    # `forward_results.json` 写下一个空数组。看起来「跑了」，
    # 实际什么都没攒，而且**明天会照样再来一遍**。
    #
    # 靠钟点排序是靠不住的：每日任务耗时从 1 小时到 2 小时不等。
    # 判据只能是「今天的全流程有没有成功」，那是事实而不是时刻。
    if not args.status_only and not args.ignore_run_status:
        try:
            from aplus.reports import read_run_status
            status = read_run_status(reports) or {}
        except Exception as exc:  # noqa: BLE001
            status = {}
            print(f"[forward] 读不到 run_status（不拦，按现有数据继续）：{exc!r}")
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        if status.get("date") != today or status.get("status") != "ok":
            got = status.get("date") or "无记录"
            state = status.get("status") or "未知"
            print(f"[forward] 今日（{today}）全流程尚未成功完成（记录：{got} / {state}），"
                  f"本次跳过——不拿昨天的数据假装攒到了今天的证据")
            return 0

    store = LocalStore(args.data_root)
    files = sorted((args.data_root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    latest = str(pd.read_parquet(files[0])["date"].max())
    dates = None
    if args.status_only:
        dates = []
    else:
        # 只为「前瞻窗口」建面板：窗口从最早的注册日之后开始，
        # 数据量远小于全历史赛马，跑得快很多。
        earliest = min(r["registered_on"] for r in rows if r.get("registered_on"))
        # 窗口为空是**正常状态**（第一批候选就是今天注册的），
        # 不该以 `start < end` 异常的形式出现——那会让定时任务
        # 在攒够样本之前每天崩一次，而没人盯着 stderr。
        if not needs_forward_panel(earliest, latest):
            print(f"前瞻窗口尚无交易日：注册于 {earliest}，最新交易日 {latest}；"
                  f"窗口必须严格晚于注册日（另需 {FORWARD_EMBARGO_DAYS} 个交易日隔离带）")
            dates = []
        else:
            start = str(earliest)
            daily_map = {p.stem: store.read_daily(p.stem) for p in files}
            factors_map = load_factors_map(store)
            xs = build_cross_sections(store, start, latest, daily_map=daily_map,
                                      factors_map=factors_map, entry_lag=ENTRY_LAG)
            dates = list(xs["_dates"])
            print(f"前瞻面板 {start} ~ {latest}：{len(dates)} 个交易日", flush=True)

    st = forward_status(reports, as_of=latest, dates=dates)
    print(f"\n前瞻台账：{st['registrations']} 份注册 · {st['n_candidates']} 组候选 · "
          f"窗口已积累 {st['days_in_window']} 个交易日")
    for r in st["registrations_detail"]:
        print(f"  注册于 {r['registered_on']}（{r['note'].splitlines()[0][:40]}）")
        print(f"    窗口 {r['window_start']} ~ {r['window_end']}"
              f"（{r['window_days']} 日）")
        for c in r["candidates"]:
            flag = "可测" if c["measurable"] else \
                f"**样本不足**（需 {c['needed_periods']} 个调仓周期）"
            print(f"    K={c['rebalance_every']:<3} {str(c['buffer_multiple']):<5} "
                  f"{c['test_periods']:>3} 个周期 · {flag} · {c['weights']}")
    if args.status_only:
        return 0

    results = []
    for r in st["registrations_detail"]:
        if not r["candidates"]:
            continue
        win = forward_window(dates, r["registered_on"])
        if not win:
            continue
        rr = race(xs, r["candidates"], evaluate_factory(xs), top_n=10, bottom_n=10,
                  train_ratio=0.999, embargo_days=0)   # 整段都是「测试段」
        results.append({"as_of": latest, "registered_on": r["registered_on"],
                        "window_days": len(win),
                        "table": [{"weights": x["weights"],
                                   "rebalance_every": x.get("rebalance_every", 1),
                                   "buffer_multiple": x.get("buffer_multiple"),
                                   "stats": x["test"]["stats"]} for x in rr["table"]]})
    out = merge_results(reports, results)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())