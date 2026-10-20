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
from aplus.factor_fundamental import attach_pit_bm
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


#: 测试段/训练段的切分比例。**0.0 ⇒ 整段都是测试段**（`split_window` 的
#: 切分点落在第 1 天，测试段 = 窗口 − 1 天）。
#:
#: 🚨 原值是 `0.999`，而 `split_window` 的实现是
#: `cut = max(1, min(n-1, int(n*train_ratio)))` ⇒ `cut = n-1`
#: ⇒ **测试段只有 1 天** ⇒ `race()` 会把 K>1 的候选全判成
#: 「测试段仅 1 个交易日，K=21 只剩 0 个调仓周期，不足 3 个——无法测量」
#: ⇒ **前瞻验证一个候选都测不了**。
#: 而本行上面的注释写的意图恰恰是「整段都是测试段」——**意图与实现不符**。
TRAIN_RATIO = 0.0


def evaluate_batch(xs: dict, win: list, candidates: list) -> dict:
    """在**该批次自己的**前瞻窗口 `win` 上评估一组候选。

    ## 为什么窗口必须按批次切

    `aplus/forward.py` 的规则逐字写着：「窗口严格晚于注册日，并留隔离带。
    注册日当天的数据**已经**被看过了。拿它当验证样本，等于用答案去答自己出的卷。」

    🚨 此前这里传的是**整个面板**（从最早的注册日算起），
    于是 2026-10-20 注册的批次 4 会被 10-12~10-19 的行情评价——
    那段数据在注册时**已经看过了** ⇒ 前瞻验证的前瞻性被破坏。

    ## 为什么 `train_ratio = 0.0`

    见 `TRAIN_RATIO` 的注释：`0.999` 给的是 1 天的测试段 ⇒ 全部候选被剔除。
    """
    sub = {**xs, "_dates": list(win)}
    return race(sub, candidates, evaluate_factory(xs), top_n=10, bottom_n=10,
                train_ratio=TRAIN_RATIO, embargo_days=0)


def _latest_trading_day(files) -> str:
    """全池里最新的交易日。

    ## 为什么不许拿一只票代表全池（2026-10-15 实测）

    原本这里取 `files[0]`（字母序第一只票）的末日期。它今天恰好等于
    全池最新，**那是运气不是性质**：300 只随机样本里有 12 只比它更旧
    （`600747.SH` 停在 2019-12-12）。

    一旦 `000001.SZ` 停牌，`latest` 静默退回旧日期 ⇒ 面板的半开区间
    `[start, latest)` 把真实最新日吃掉 ⇒ 窗口恒为 0 天 ⇒
    `forward_results.json` 一直写 `[]`。而这个症状与「样本还没攒够」
    **逐位同形**（第 148 层）：无报错、无非空输出、无非零退出码。

    只读 `date` 列，不整表读入——这是全池扫描，唯一成本就在这里。

    ## 输出格式必须钉死为 `YYYY-MM-DD`

    主库现役 parquet 的 `date` 是 **str** 列（实测 `dtype=str`），所以
    `str(max)` 恰好就是纯日期。但那只是**现役写入方**的巧合：一旦哪天
    某条链路写成 datetime 列，`str(Timestamp)` 会变成
    `'2026-10-15 00:00:00'`，而下游全是**字符串比较**
    （`needs_forward_panel`、`forward_window`），`'2026-10-15 00:00:00' >
    '2026-10-15'` 为真会让窗口凭空多算一天。

    所以这里显式归一成日期，不依赖 dtype 的巧合（第 155 层）。
    """
    best = None
    for p in files:
        try:
            m = pd.read_parquet(p, columns=["date"])["date"].max()
        except Exception:  # noqa: BLE001 - 单只坏文件不该让全池取不到日期
            continue
        if m is None or (isinstance(m, float) and pd.isna(m)):
            continue          # 空文件的 max() 是 NaN，不能参与比较
        if best is None or m > best:
            best = m

    if best is None:
        raise RuntimeError(
            f"全池 {len(list(files))} 个 parquet 里一个日期都读不出来——"
            f"主库坏了。别让 latest 变成 'None' 字符串：那会让下游的"
            f"needs_forward_panel 拿到一个非空字符串而静默跳过整轮，"
            f"症状与「样本还没攒够」逐位同形（第 148 层）"
        )
    # 归一成 `YYYY-MM-DD`：dtype 是 str 还是 datetime 都给同一种字面值
    return pd.Timestamp(best).strftime("%Y-%m-%d")


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
    latest = _latest_trading_day(files)
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
            # ── PIT 版 BM（批次 4 起）挂进横截面 ─────────────────────
            # `aplus/factor_fundamental.py::attach_pit_bm` 的财务侧**只**来自
            # `vintage.pit_frame`：拿的是那天源站当时服务的帧。
            #
            # 🚨 为什么必须挂：**每一天都要有 `bm` 列**。
            # `aplus/factor.py::composite_score` 对缺失的因子列会 **KeyError**
            # ⇒ 某天没有 `bm` 列，含 `bm` 的候选当场崩，而崩的是**整个前瞻验证**
            # （连不带 `bm` 的老批次一起陪葬）。
            # 没有 PIT 快照的日子该列**全为 NaN** ⇒ `composite_score` 的
            # `min_count` 规则把那天全部剔掉 ⇒ 那天**不出信号**（不是出零信号）。
            vintage_root = args.data_root / "reports" / "financial_vintage"
            bm_report = attach_pit_bm(xs, root=vintage_root, daily_map=daily_map)
            _bm_gap = bm_report.get("unavailable_days") or []
            print(f"前瞻面板 {start} ~ {latest}：{len(xs['_dates'])} 个交易日"
                  f" · PIT BM 可用 {bm_report.get('n_with_bm')} 天"
                  + (f" · 无 PIT 快照（BM 列全 NaN）{len(_bm_gap)} 天："
                     f"{_bm_gap[0]} ~ {_bm_gap[-1]}" if _bm_gap else ""),
                  flush=True)
            dates = list(xs["_dates"])

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
        # ── 每批只看**它自己的**前瞻窗口 ──────────────────────────
        # 理由与 `evaluate_batch` 的 docstring 逐字相同（不重复一份）。
        rr = evaluate_batch(xs, win, r["candidates"])
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