#!/usr/bin/env python3
"""判决窗口的**逐年分布**——补 `regime_control_sweep.py` 不产出的那一半。

## 为什么需要这个脚本

`docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md` 第三节约束 3 逐字：
「**必须同时报逐年分布**。理由：选段的 +105.23%/年 极可能来自窗口内含
2021 微盘牛。只报一个总分会把这个成因藏起来——而这正是本预注册存在的意义。」

⚠️ **但 `scripts/regime_control_sweep.py` 的产物里没有逐年字段**
（`_metrics` 只返回 `n_days / annual_cost / annual_up_excess /
annual_breadth / annual_tilt / hit_rate`，且落盘时把 `_pred` 这个
DataFrame 剔除了）⇒ **原跑法无法满足该约束。**

⇒ 本脚本**不修改** `regime_control_sweep.py`，而是 import 它的
`_panel` / `SIGN_FIXED`，用**同一个** `evaluate_weights` 重放
K=21 的两条臂，然后按 `pred_date` 的年份分组、逐年调用
`aplus.verify.rolling_stats`。

## 它承诺什么、不承诺什么

| | |
|---|---|
| ✅ 承诺 | 用与原跑法**完全相同**的 `evaluate_weights` 参数重放同一条路径 |
| ✅ 承诺 | 只跑 `K = 21`（`LIVE_REBALANCE_EVERY`），两个 `buffer_multiple` 都出 |
| ⚠️ 不承诺 | 不重跑判决、不改任何判据、不改 `PREREG` 三条门槛 |
| ⚠️ 不承诺 | **它不是预注册产物**——逐年分布是「原跑法产不出来、由事后补跑补上」的 |

## 缓存纪律（教训直接来自 `sweep.py:124-125`）

```python
# 2026-10-14：原为 `if cache.exists() and not rebuild`——那次假重跑
# 就是从这里流出去的。
```

⚠️ **默认 `--reuse` 为 False（重建）**，与 `sweep.py` 的默认值一致。
⚠️ 打 `--reuse` 之前先确认缓存窗口与本脚本的 `--start/--end` 一致——
`PC.load_cache` 会校验窗口，但**校验失败时的行为必须自己看清**，
不能默认它拦住了。

## 跑法

```
.venv/bin/python scripts/regime_control_by_year.py \
    --start 2021-01-04 --end 2026-10-09 \
    --out data/reports/regime_control_judge_window_by_year_2026-10-15.json
```
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from aplus.backtest import evaluate_weights  # noqa: E402
from aplus.verify import rolling_stats  # noqa: E402
from aplus import provenance  # noqa: E402

# ── 直接从文件加载 `regime_control_sweep`，不改它、不触发它的 main ──
_spec = importlib.util.spec_from_file_location(
    "_rcs", ROOT / "scripts" / "regime_control_sweep.py")
_rcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rcs)

SIGN_FIXED = _rcs.SIGN_FIXED
WRONG_SIGN = _rcs.WRONG_SIGN
DAYS_PER_YEAR = _rcs.DAYS_PER_YEAR
_panel = _rcs._panel
_metrics = _rcs._metrics          # 原跑法自己的度量函数——用它做**对拍**
_daily_series = _rcs._daily_series

# 预注册第二节的门槛：由 `aplus/hurdle.py` 推导，不在本文件写字面量。
from aplus.hurdle import hurdle_rate, COST_REAL_50K  # noqa: E402
from aplus.evolve import LIVE_REBALANCE_EVERY  # noqa: E402

HURDLE = hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K,
                     rounds_per_year=244 / LIVE_REBALANCE_EVERY,
                     benchmark_annual=0.0)


def pooled(pred: pd.DataFrame) -> dict:
    """**总体**的净超额日度序列 + 年化点估计/sd/SE —— 预注册第四节的 ±1σ 只在这里。

    ## 为什么主指标要报「净」和「毛」两个数

    `regime_control_sweep.py::_metrics` 的
    `annual_up_excess = rolling_stats(...)["up_excess_vs_market"] × 244`
    追到 `aplus/verify.py:236` 是 `mean(actual_pct − cost)` ⇒ **它已经是净值**。

    而 `docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md` 第二节逐字问的是
    「单腿多头的**毛**超额是多少？」⇒ **预注册问的量与产物给的量不是同一个。**

    ⇒ 本函数**两个都给**，不替任何一边下判决（layer 91：判据一个字不改）。
    毛 = 净 + 摩擦，而 `cost` 列已是双边口径（`backtest.py:82`
    `turnover_ratio=(n_in+n_out)/denom`、`:76` 「首期建仓只付买侧故为 cost/2」）。

    ⚠️ 年 sd 必须乘 **√244**，不是 ×244（`tests/test_annualization_is_a_sqrt_of_time.py`：
    「三个长得一样的年化乘数里乘错了一个」）。SE 乘 ×244 是对的。
    """
    if not len(pred):
        return {}
    s_net = _daily_series(pred, "up_excess")      # 日度**净**超额 = 净值 − 市场
    n = len(s_net)
    sd_day = float(s_net.std(ddof=1)) if n > 1 else 0.0
    mean_net_annual = float(s_net.mean()) * DAYS_PER_YEAR
    se_annual = sd_day / (n ** 0.5) * DAYS_PER_YEAR if n > 1 else 0.0

    up = pred[pred["direction"] == "up"]
    n_days = len(set(up["pred_date"])) if len(up) else 0
    # 复刻 `_metrics` 的年成本算法：按日均摊再按年缩放（不是 sum()/n_days）。
    annual_cost = (float(up.groupby("pred_date")["cost"].mean().sum())
                   / max(n_days / DAYS_PER_YEAR, 1e-9)) if n_days else 0.0

    return {
        "n_days": n_days,
        "n_series": n,
        "annual_cost_pct": annual_cost,
        # 产物字段（净）
        "annual_up_excess_NET_pct": mean_net_annual,
        # 预注册问的量（毛）
        "annual_up_excess_GROSS_pct": mean_net_annual + annual_cost,
        "annual_sd_pct": sd_day * (DAYS_PER_YEAR ** 0.5),
        "se_annual_pct": se_annual,
        "ci95_lo_NET_pct": mean_net_annual - 1.96 * se_annual,
        "ci95_hi_NET_pct": mean_net_annual + 1.96 * se_annual,
        "ci95_lo_GROSS_pct": mean_net_annual + annual_cost - 1.96 * se_annual,
        "ci95_hi_GROSS_pct": mean_net_annual + annual_cost + 1.96 * se_annual,
        "plus1sd_NET_pct": mean_net_annual + se_annual,
        "plus1sd_GROSS_pct": mean_net_annual + annual_cost + se_annual,
    }


def by_year(pred: pd.DataFrame) -> dict:
    """逐年跑一遍 `rolling_stats`。空年不出现（不是 0，是不存在）。"""
    out = {}
    if not len(pred):
        return out
    years = sorted({str(d)[:4] for d in pred["pred_date"]})
    for y in years:
        sub = pred[pred["pred_date"].str.startswith(y)]
        st = rolling_stats(sub)
        up = sub[sub["direction"] == "up"]
        p = pooled(sub)
        out[y] = {
            # 逐年 n_days：独立成交窗口数才是样本量，条目数会虚增。
            "n_pred": int(len(sub)),
            "n_days": int(len(set(up["pred_date"]))) if len(up) else 0,
            "annual_up_excess": float(st["up_excess_vs_market"]) * DAYS_PER_YEAR,
            "annual_breadth": float(st["excess_breadth"]) * DAYS_PER_YEAR,
            "annual_tilt": float(st["excess_tilt"]) * DAYS_PER_YEAR,
            "hit_rate": float(st["overall_hit_rate"]),
            # 逐年 sd/se —— ⚠️ 一年只有 ~244 个点、**约 11 个调仓**，
            # 这个 SE 只能当量纲看，**不许据此对单年下判决**。
            "annual_up_excess_NET_pct": p.get("annual_up_excess_NET_pct"),
            "annual_up_excess_GROSS_pct": p.get("annual_up_excess_GROSS_pct"),
            "annual_sd_pct": p.get("annual_sd_pct"),
            "se_annual_pct": p.get("se_annual_pct"),
            # 逐年也记 `flat` 数——判决窗口有没有同一口径问题，未测。
            "n_flat": int((sub["result"] == "flat").sum()) if "result" in sub else None,
            "n_no_data": int((sub["result"] == "no_data").sum()) if "result" in sub else None,
        }
    return out


def _check_parity(path: Path, k: int, buf, ref: dict, parity: dict) -> None:
    """把**重放出来的** `_metrics` 与 sweep **落盘的**逐位比。

    ⚠️ 这一步不是自证：sweep 的产物是另一条路径（`sweep()` 循环内直接调
    `_metrics` 后落盘）写出的，本脚本是 importlib 重放后自己调。
    「同一份数据两种表述结果必须相同」（形状 20 的加强版判据）——
    只有当缺陷**只在一侧成立**时这个差分才有判别力；
    这里的缺陷若是我改了参数，那两侧会**一起**变 ⇒ 差分为 0 ⇒ 恒真。

    ⇒ 所以 mismatch 不为空时必须**报出来**，不许当装饰。

    ⚠️ `k` 是**显式入参**，不是从 `ref` 里取。
    第一版写了 `ref["_k"]`——而 `_metrics()` 的返回值里**没有 `_k` 这个键**
    （只有 `n_days/annual_cost/annual_up_excess/annual_breadth/annual_tilt/
    hit_rate/_pred`）⇒ 一旦 `--parity-against` 生效就是 `KeyError`，
    而这条路径**在写对拍之前从没被跑过**（形状 9：新分支从未被执行）。
    ⚠️ 更值钱的是它属于「符号 grep 核验过存在」却**没核验调用契约**那一族：
    `_rcs._metrics` 确实存在，但「存在」不等于「返回我以为的那几个键」。
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("rows") or []
    for r in rows:
        if r.get("rebalance_every") != k and r.get("rebalance_every") is not None:
            continue
        rb, fb = r.get("buffer_multiple"), buf
        if (rb is None) != (fb is None):
            continue
        if rb is not None and abs(float(rb) - float(fb)) > 1e-12:
            continue
        for key, mine in (("annual_up_excess", ref["annual_up_excess"]),
                          ("annual_cost", ref["annual_cost"])):
            theirs = r["sign_fixed"].get(key)
            if theirs is None:
                continue
            if abs(float(theirs) - float(mine)) > 0.0:
                parity["mismatches"].append(
                    {"buffer_multiple": buf, "field": key,
                     "sweep_artifact": float(theirs),
                     "replayed_here": float(mine),
                     "abs_diff": abs(float(theirs) - float(mine))})
        return
    parity["mismatches"].append(
        {"buffer_multiple": buf, "field": "<row-not-found>",
         "sweep_artifact": None, "replayed_here": None,
         "abs_diff": None})


def hurdle_quadrant(p: dict) -> dict:
    """四种「主指标 × 门槛」口径的并排判决。

    ⚠️ 预注册第二节问的是**毛**超额，而 `_metrics` 给的是**净**（`verify.py:236`
    `mean(actual_pct − cost)`）⇒ 这是两条不同的路，必须都印出来。
    ⚠️ 门槛 `0.2639047619047619` 的成本项 `rounds_per_year × cost_per_round`
    假设**每轮满换手**，而实测 `annual_cost_pct` 是这个策略的真实换手
    ⇒ 实测口径的门槛 = `0.20 + annual_cost_pct`。

    ⚠️⚠️ **本函数一个数都不重算**——全部读 `pooled()` 已产出的字段。
    第一版在这里手写 `(gross + (gross - net))` 想算 `gross + se`，
    算出来是 `gross + cost`，**差了一个成本项**。
    ⇒ `compare-like-calibers-or-not-at-all` + 「核对产物只读字段，
    禁止用公式重算『验证』」：**产物已经有的量不许在下游再写一遍。**

    layer 91：**判据一个字不改**。本函数只做并排，不替任何一边下判决。
    """
    if not p:
        return {}
    h_meas = 0.20 + p["annual_cost_pct"]
    return {
        "hurdle_nominal_pct": HURDLE,
        "hurdle_measured_cost_pct": h_meas,
        "hurdle_derivation": (
            "名义=hurdle_rate(target=0.20,cost_per_round=COST_REAL_50K,"
            "rounds_per_year=244/21,benchmark_annual=0.0)；"
            "实测=0.20+annual_cost_pct（成本项换成这个信号的真实换手）"),
        "cases": {
            "NET_vs_nominal": p["annual_up_excess_NET_pct"] - HURDLE,
            "GROSS_vs_nominal": p["annual_up_excess_GROSS_pct"] - HURDLE,
            "NET_vs_measured": p["annual_up_excess_NET_pct"] - h_meas,
            "GROSS_vs_measured": p["annual_up_excess_GROSS_pct"] - h_meas,
            "plus1sd_NET_vs_nominal": p["plus1sd_NET_pct"] - HURDLE,
            "plus1sd_GROSS_vs_nominal": p["plus1sd_GROSS_pct"] - HURDLE,
            "plus1sd_NET_vs_measured": p["plus1sd_NET_pct"] - h_meas,
            "plus1sd_GROSS_vs_measured": p["plus1sd_GROSS_pct"] - h_meas,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--start", default="2021-01-04")
    ap.add_argument("--end", default="2026-10-09")
    ap.add_argument("--k", type=int, default=LIVE_REBALANCE_EVERY)
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_by_year_panel_cache.pkl"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/regime_control_by_year.json"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用缓存。**默认重建**——见文件头「缓存纪律」")
    ap.add_argument("--parity-against", type=Path, default=None,
                    help="regime_control_sweep 的产物路径。给了就把本脚本"
                         "重放出来的 annual_up_excess 与它**逐位比对**——"
                         "证明两条路径同源，而不是各算一遍")
    args = ap.parse_args(argv)

    # 缓存文件名带窗口，避免不同窗口共用一个缓存文件。
    if not args.cache.name.endswith(".pkl"):
        raise SystemExit(f"缓存名须以 .pkl 结尾：{args.cache}")
    cache = args.cache.with_name(f"{args.cache.stem}_{args.start}_{args.end}.pkl")

    t0 = time.time()
    # 第 125 层：开跑时抓一次字节哈希，写盘那一刻对不上就拒盖。
    expect = provenance.code_file_sha256(ROOT, "scripts/regime_control_by_year.py")
    xs = _panel(args.data_root, cache, args.reuse,
                start=args.start, end=args.end)
    dates = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"逐年重放 K={args.k}｜{len(dates)} 个交易日｜{time.time()-t0:.0f}s",
          flush=True)

    arms = []
    parity = {"checked": bool(args.parity_against), "mismatches": [], "note": "",
              "parity_against": str(args.parity_against) if args.parity_against else None}
    for buf in (None, 3.0):
        row = {"buffer_multiple": buf}
        for name, w in (("sign_fixed", SIGN_FIXED), ("wrong_sign", WRONG_SIGN)):
            pred = evaluate_weights(xs, w, top_n=10, bottom_n=10, dates=dates,
                                    rebalance_every=args.k,
                                    buffer_multiple=buf)
            row[name] = by_year(pred) if len(pred) else {}
            # ★ 对拍：用**原跑法自己的** `_metrics`，与 sweep 的落盘值逐位比。
            row[f"pooled_{name}"] = pooled(pred) if len(pred) else {}
            if len(pred) and name == "sign_fixed":
                ref = _metrics(pred)          # 含 `_pred`，是原跑法的那份
                row["_metrics_annual_up_excess"] = ref["annual_up_excess"]
                row["_metrics_annual_cost"] = ref["annual_cost"]
                if args.parity_against:
                    _check_parity(args.parity_against, args.k, buf, ref, parity)
        # 两臂之差——与 `sweep.py:210-213` 同构。
        ys = sorted(set(row["sign_fixed"]) & set(row["wrong_sign"]))
        row["diff_by_year"] = {
            y: row["sign_fixed"][y]["annual_up_excess"]
               - row["wrong_sign"][y]["annual_up_excess"] for y in ys
        }
        row["verdict_by_year"] = {
            y: ("ABOVE_HURDLE" if v >= HURDLE else "BELOW_HURDLE")
            for y, v in row["diff_by_year"].items()
        }
        row["n_years_negative_diff"] = sum(
            1 for v in row["diff_by_year"].values() if v < 0)
        # ★ 四个 buffer×口径格并排**落盘**——判决要读它，不是读代码。
        row["hurdle_quadrant"] = hurdle_quadrant(row.get("pooled_sign_fixed") or {})
        arms.append(row)
        print(f"  buffer={buf}: {row['diff_by_year']}", flush=True)

    # 对拍结果必须**原样印出**——mismatches 非空就是非空，不许因为
    # 「看起来对」就略过（第 112 层：恒真/空操作的守卫与通过的守卫长得一样）。
    if parity["checked"]:
        n_bad = len(parity["mismatches"])
        if n_bad:
            print(f"\n🚨 对拍 mismatch {n_bad} 处（逐条）：", flush=True)
            for m in parity["mismatches"]:
                print(f"   {m}", flush=True)
        else:
            print("\n对拍 0 mismatch（逐位相同）", flush=True)

    out = {
        "kind": "regime_control_by_year",
        "provenance_note": (
            "⚠️ **本产物是事后补跑，不是预注册跑法的产物。**"
            "`regime_control_sweep.py` 不输出逐年分布，"
            "预注册第三节约束 3 只能由本脚本补上。"
            "它不重跑判决、不改 `PREREG` 三条门槛。"),
        "window": [args.start, args.end],
        "rebalance_every": args.k,
        "hurdle_from_hurdle_rate": HURDLE,
        "hurdle_derivation": (
            "hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K, "
            "rounds_per_year=244/LIVE_REBALANCE_EVERY, benchmark_annual=0.0)"),
        "labels_note": (
            "⚠️ `annual_up_excess` 是**长账户对市场的净超额**"
            "（`up_excess_vs_market × 244`，而 `verify.py:236` 追下去是 "
            "`mean(actual_pct − cost)` ⇒ **已经扣过成本**），"
            "与 `annual_breadth`（多空价差宽度）**不是同一个量**，不许互相比较。\n"
            "⚠️ 而预注册第二节逐字问的是「**毛**超额」——"
            "⇒ `pooled_*.annual_up_excess_GROSS_pct` 才是它问的那个量，"
            "两者都报，见 `arms[*].hurdle_quadrant`。"),
        "parity": parity,
        "arms": arms,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(args.out, "scripts/regime_control_by_year.py", root=ROOT,
                     data_root=args.data_root, expect_bytes=expect)
    print(f"\n写出 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
