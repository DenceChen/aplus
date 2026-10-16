"""市场基准污染审计 —— `up_excess_vs_market` 量到的是不是 alpha。

## 怀疑

`_market_by_day`（aplus/backtest.py:500）这样抽样：

    all_codes = list(daily_map)                          # 全部 parquet 文件
    sample_codes = rng.sample(all_codes, 150)

**不过滤**。它只经 `_next_bars` 的 `pos == 0` 一道闸（挡掉上市前），
而策略侧 `filter_universe` 明确剔除 ST、次新（`listed_days > 60`）、
无成交。于是基准篮子里的票有一部分是策略**明令不许买**的。

`evolve_race.py:540` 的 `excess_t` 是**阻塞闸**，它的输入就是
`up_excess_vs_market`。如果基准被污染，「回避垃圾股」会变成巨额"超额"，
而那和因子有效性无关。

## 但这只是怀疑

上一段刚被推翻的教训是：**没有标准误的均值表看起来像结论**。
所以本脚本不输出"污染了/没污染"的判断，而是输出**两个可比的数**：

    raw      = 150 只抽样票的日均收益（现状口径）
    filtered = 同批票里**通过 filter_universe 闸门**的那些的日均收益
    gap      = raw − filtered，逐日配对 t

`gap` 就是基准每天被污染掉的量。它的年化值直接等于
`up_excess_vs_market` 被虚增的百分点/年。

## 三条硬边界

1. **只诊断，不修**。本脚本不改 `_market_by_day`。改了之后所有历史
   `evolution_ledger` 条目里的超额数字都换了坐标系，必须另行处理。
2. **同批票**。两个口径用的是**同一批抽样**、同一天、同一个 lag，
   唯一差别是过不过闸——配对，噪声抵消。
3. **1 日前瞻**，与 `head_bucket_probe` 同口径。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus.backtest import BENCH_SEED, MARKET_SAMPLE, benchmark_cohort  # noqa: E402
from aplus.panel import CARRY_COLUMNS  # noqa: E402
from scripts.head_bucket_probe import (  # noqa: E402
    DAYS_PER_YEAR, _next_bars, _paired_t,
)

# ★ 基准抽样种子。**必须与 `_market_by_day` 是同一个数**，否则抽到的不是同一批票，
#   「raw vs filtered」就成了两次独立抽样之间的比较，配对失效。
# 🚨 2026-10-16：此前这里是**独立写死的 `42` 字面量**（本文件的第二份副本）——
#   改 `BENCH_SEED` 而不动这里，两边就静默错开，且**没有任何东西会红**。
#   现在它是 `BENCH_SEED` 的**别名**而非副本 ⇒ 单一真相源。
# ⚠️ 别名保留是为了不动下游 `MARKET_SEED` 的 3 处引用
#   （`audit_window` 的 RNG 实参 / 产物 `market_seed` / 本文件守卫的 import）。
MARKET_SEED = BENCH_SEED


def gate_row(daily_map: dict, code: str, day: str) -> dict | None:
    """从**原始日线帧**取信号日那根 bar 的闸门列。

    ## 为什么不能直接读 `_next_bars` 的输出

    实测 `_next_bars` 只返回 `code / close / prev_close / adjusted`——
    **四道闸门列一个都没有**。`CARRY_COLUMNS` 只喂 `panel.cross_section`
    那条路，`_market_by_day` 走的是 `daily_map` 原始帧。

    这不只是本脚本的麻烦：**它说明基准污染是架构性的，不是疏忽**——
    基准「想过滤都过滤不了」，除非先把列接进 `_next_bars`。
    所以本函数从原始帧重建同一份信息，语义与 `_next_bars` 的取数一致
    （同样的 `searchsorted(side="right") - 1`、同样的 `pos == 0` 跳过）。

    `listed_days` 用 K 线根数，与 `aplus/panel.py:579` 的
    `np.arange(1, len(merged)+1)` 同口径。
    """
    df = daily_map.get(code)
    if df is None or df.empty:
        return None
    dates = df["date"].to_numpy()
    pos = int(np.searchsorted(dates, day, side="right")) - 1
    if pos <= 0:                       # 信号日尚未有 K 线 → 该股当日不存在
        return None
    r = df.iloc[pos]
    return {
        "code": code,
        "isST": _num(r, "isST", 0.0),
        "listed_days": float(pos + 1),
        "volume": _num(r, "volume", 0.0),
    }


def _num(row, key: str, default: float) -> float:
    """取数值，**缺列或不可解析**才用默认。

    绝不能写成 `row.get(key, default) or default`——`or` 在数值上是错的：
    **0 是 falsy**，`0 or 1e9` 返回 1e9，于是上市 0 天的次新股会被当成
    上市 27 年的老股放行。那正是本脚本要抓的那类污染，却被闸门自己放过。

    `or` 只在默认语义是 `None`/空串时成立；用在数值上是把「合法的零」
    偷换成「缺失」。
    """
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else f          # NaN 也走默认


def passes_filters(row, min_listed_days: int = 60) -> bool:
    """复刻 `filter_universe` 的硬闸。

    **必须逐条对齐** `aplus/predict.py` 的 `filter_universe`：
        (isST != 1) & (listed_days > 60) & (volume > 0)

    这里不含名称过滤（ST 前缀），因为原始帧不带 name；
    名称那一层会让结果更保守（见下文 `已知缺口`），不会让结论更乐观。
    """
    if int(_num(row, "isST", 0.0)) == 1:
        return False
    if _num(row, "listed_days", float("inf")) <= min_listed_days:
        return False
    return _num(row, "volume", 0.0) > 0


def audit_window(xs: dict, dates: list, sample_size: int = MARKET_SAMPLE) -> list[dict]:
    """逐日记录 raw / filtered 两个日均收益。"""
    daily_map = xs.get("_daily_map") or {}
    entry_lag = xs.get("_entry_lag", 1)
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})

    sample_codes = benchmark_cohort(daily_map, sample_size, MARKET_SEED)

    rows = []
    for day in dates:
        bars = _next_bars(daily_map, sample_codes, day, entry_lag=entry_lag,
                          factors_map=factors_map, adj_cache=adj_cache)
        if bars.empty:
            continue
        bars = bars.copy()
        bars["actual_pct"] = bars["close"] / bars["prev_close"] - 1.0
        bars = bars.dropna(subset=["actual_pct"])
        if bars.empty:
            continue
        # 闸门逐股现取：`_next_bars` 的输出没有这些列（见 `gate_row` 的说明）。
        ok, n_gate = [], 0
        for code in bars["code"]:
            g = gate_row(daily_map, code, day)
            if g is None:              # 信号日没有自己的 bar → 不可判定，不放行
                ok.append(False)
                continue
            n_gate += 1
            ok.append(passes_filters(g))
        ok = pd.Series(ok, index=bars.index)
        rows.append({
            "pred_date": day,
            "raw": float(bars["actual_pct"].mean()),
            "n_raw": len(bars),
            "n_gate": n_gate,
            "n_pass": int(ok.sum()),
            # 全被闸门否掉的日子不能进均值——否则 filtered 变成空样本的
            # 静默兜底，而空样本的均值不是任何东西。
            "filtered": float(bars.loc[ok, "actual_pct"].mean()) if ok.any() else None,
        })
    return rows


def verdict(rows: list[dict], threshold: float = 3.0) -> dict:
    """raw − filtered 的逐日配对 t。"""
    df = pd.DataFrame(rows)
    if df.empty:
        return {"n_days": 0, "verdict": "样本为空——不要据此下任何结论"}
    both = df.dropna(subset=["filtered"])
    if len(both) < 3:
        return {"n_days": len(both), "verdict": "有效日不足"}

    t = _paired_t(both["raw"], both["filtered"])
    raw_ann = float(both["raw"].mean()) * DAYS_PER_YEAR
    fil_ann = float(both["filtered"].mean()) * DAYS_PER_YEAR
    gap_ann = (raw_ann - fil_ann)
    pass_rate = float(both["n_pass"].mean()) / float(both["n_raw"].mean())

    if t is None:
        v = "**污染量不可估**（两序列逐日完全相同）——先别改代码，先弄清为什么"
    elif t <= -threshold:
        v = (f"**基准被显著拉低 {gap_ann:+.1%}/年**（t={t:.2f}）——"
             f"`up_excess_vs_market` 至少虚增了这么多")
    elif t >= threshold:
        v = (f"**基准反而被拉高 {-gap_ann:+.1%}/年**（t={t:.2f}）——"
             f"污染方向与怀疑相反，结论要重写")
    else:
        v = f"污染不显著（t={t:.2f}，|gap| < 噪声）——原口径可留，但需记录"

    return {
        "n_days": int(len(both)),
        "raw_annual": raw_ann, "filtered_annual": fil_ann, "gap_annual": gap_ann,
        "pass_rate": pass_rate, "paired_t": t, "verdict": v,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/market_benchmark_audit.json"))
    args = ap.parse_args()

    import pickle
    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    dates = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"本段 {len(dates)} 个交易日", flush=True)

    rows = audit_window(xs, dates)
    if not rows:
        print("样本为空——**不要据此下任何结论**", flush=True)
        return 1

    res = verdict(rows)
    print(f"\n{'='*88}\n市场基准污染审计（{args.start} ~ {args.end}）\n{'='*88}")
    print(f"  有效日          {res['n_days']}")
    print(f"  抽样票过闸率    {res.get('pass_rate', float('nan')):.1%}")
    print(f"  raw 年均        {res.get('raw_annual', float('nan')):+.2%}")
    print(f"  filtered 年均   {res.get('filtered_annual', float('nan')):+.2%}")
    print(f"  gap 年均        {res.get('gap_annual', float('nan')):+.2%}")
    t = res.get("paired_t")
    print(f"  逐日配对 t      {t if t is not None else float('nan'):+.2f}")
    print(f"\n  → {res['verdict']}")

    out = {"kind": "market_benchmark_audit",
           "carry_columns": list(CARRY_COLUMNS),
           "market_seed": MARKET_SEED, "sample_size": MARKET_SAMPLE,
           "window": [args.start, args.end],
           "scope": ("只诊断，不改 _market_by_day。已知缺口：未含名称(ST 前缀)过滤——原始日线帧不带 name，审计因此偏保守（判污染的更少），结论方向安全"),
           "architecture_finding": ("_next_bars 只返回 code/close/prev_close/adjusted，四道闸门列一个都没有。CARRY_COLUMNS 只喂 panel.cross_section。**基准污染是架构性的：想过滤都过滤不了**，除非先把列接进 _next_bars"),
           "result": res, "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
