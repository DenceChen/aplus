#!/usr/bin/env python3
"""判决窗口的 `MIN_BASKET` 对照臂 **+ 主指标两个口径的逐位分解**。

## 它为什么存在（两条独立的债，一次重放一起偿）

`docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md` **附则一**第三节逐字：

| # | 动作 | 约束 |
|---|---|---|
| 3 | 跑完后加 `MIN_BASKET` 对照臂，产物写**另一个文件** | 不覆盖第一份 |
| 4 | **两个判决并列印出，不许选一个** | 若落在不同档位，必须同时给 |

⇒ 本脚本就是那条对照臂。**判据一个字不改**（附则一第三节末逐字：
「无论对照臂结果如何，本文第二节的四条判决规则一个字不改」）。

⚠️ **债二（本次新发现，与附则一独立）**：`data/reports/` 里同一个主指标
**有两个数**，来源是两个**数学上不等价**的算法：

| | 算法 | 在哪 |
|---|---|---|
| **A** | `mean_row(up净) − mean_row(market)`，两边都**逐行** | `sweep._metrics` → `aplus/verify.py::rolling_stats` |
| **B** | `mean_daily(up净) − mean_daily(market)`，**逐日**先对齐 | `by_year.pooled` → `sweep._daily_series` |

实测（`regime_control_judge_window_by_year_2026-10-15.json`）：
K=21 / buffer=None 时 **A = +0.747990%**、**B = −2.903525%**，**差 3.65pp**。
⚠️ 2021 一年三个数并列：**净 8.654% / A 10.878% / 毛 12.118%**
⇒ **A 既不是净也不是毛**，它是一个**第三种口径**。

⇒ 光说「两个数不一样」没有用。本脚本把差**分解成两项并断言逐位闭合**：

```
A − B = 244 × [ (mean_row(up净) − mean_daily(up净))
              − (mean_row(market) − mean_daily(market)) ]
```

⚠️ **闭合不上就 `AssertionError`**——不许带着没解释的差出产物
（纪律「缺档必须报错，绝不回落」）。

## 它承诺什么、不承诺什么

| | |
|---|---|
| ✅ 承诺 | 用与原跑法**完全相同**的 `evaluate_weights` 参数重放同一条路径 |
| ✅ 承诺 | 只跑 `K = 21` 的 `sign_fixed` 一条腿（主指标是它的**绝对值**，不依赖 wrong_sign） |
| ✅ 承诺 | 两个 `buffer_multiple` 都出，不挑 |
| ⚠️ 不承诺 | **不改** `regime_control_sweep.py` / `verify.py` / `_metrics` 一个字节 |
| ⚠️ 不承诺 | **它不是预注册产物**——对照臂与分解都是事后补跑 |
| ⚠️ 不承诺 | **不裁定 A/B 谁对**——那是判决文档的事，本脚本只给数 |

## 跑法

```
.venv/bin/python scripts/regime_control_min_basket_control.py \
    --start 2021-01-04 --end 2026-10-09 \
    --out data/reports/regime_control_min_basket_control_2026-10-15.json
```
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from aplus.backtest import evaluate_weights  # noqa: E402
from aplus.verify import MIN_BASKET  # noqa: E402  ← 从常量导入，不写字面量 3

from aplus.hurdle import hurdle_rate, COST_REAL_50K  # noqa: E402
from aplus.evolve import LIVE_REBALANCE_EVERY  # noqa: E402
from aplus import provenance  # noqa: E402

# ── 直接从文件加载另两个脚本，不改它们、不触发它们的 main ──
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_rcs = _load("_rcs", ROOT / "scripts" / "regime_control_sweep.py")
# ★ `pooled()` 是 B 口径（逐日）的**权威实现**，且它的产物已对拍过 0 mismatch
# ⇒ 本脚本的 B 直接**引用**它，不重算 sd/se（层 109：核对产物只读字段）。
_rcby = _load("_rcby", ROOT / "scripts" / "regime_control_by_year.py")
pooled_ref = _rcby.pooled

SIGN_FIXED = _rcs.SIGN_FIXED
DAYS_PER_YEAR = _rcs.DAYS_PER_YEAR
_panel = _rcs._panel
_metrics = _rcs._metrics
_daily_series = _rcs._daily_series

# 预注册第二节的门槛：由 `aplus/hurdle.py` 推导，不在本文件写字面量。
HURDLE = hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K,
                     rounds_per_year=244 / LIVE_REBALANCE_EVERY,
                     benchmark_annual=0.0)

#: 生成器路径 —— 登记在 `aplus/provenance.py::DEFAULT_GENERATORS`，
#: **只从写盘点读，绝不从输出文件名反推**（第 123 层）。
GENERATOR = "scripts/regime_control_min_basket_control.py"


def _daily_parts(pred: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """逐日的三样东西：**up 篮子净**、**market**、**up 有效成分数**。

    ★ 口径逐字照抄 `sweep._daily_series:176-182` 与 `verify.py:396-398`：

    ```
    s_up  = up.groupby("pred_date")["_x"].mean()        # groupby.mean 跳过 NaN
    mkt   = pred.groupby("pred_date")["market_pct"].mean()   # 全部行
    size  = up.groupby("pred_date")["_x"].count()      # = verify.py 的 by_day.count()
    ```

    ⚠️ 三者都在**同一个 groupby 索引**上（`pred_date`）⇒ 下游的 `.reindex`
    是同集合运算，不会各丢各的。
    """
    up = pred[pred["direction"] == "up"]
    net = up.assign(_x=up["actual_pct"] - up["cost"])
    s_up = net.groupby("pred_date")["_x"].mean()
    mkt = pred.groupby("pred_date")["market_pct"].mean()
    size = net.groupby("pred_date")["_x"].count()
    return s_up, mkt, size


def decompose(pred: pd.DataFrame) -> dict:
    """把 `A − B` 分解成两项，并**断言逐位闭合**。

    - `A` = 逐行口径（`rolling_stats` / `_metrics` 用的那个）
    - `B` = 逐日口径（`_daily_series` / `by_year.pooled` 用的那个）
    - `MIN_BASKET` 对照臂 = 逐日口径 + `size >= MIN_BASKET`
    """
    s_up, mkt, size = _daily_parts(pred)
    # `_daily_series` 的真实结果（决定 B 与对照臂的日期集合，本脚本不另定一套）
    series_b = _daily_series(pred, "up_excess")
    days = series_b.index                      # sub(fill_value=nan).dropna() 之后的集合

    # ── B 及其两项分量（都在 `days` 这同一个集合上，分解才闭合）──
    up_b = s_up.reindex(days)
    mkt_b = mkt.reindex(days)
    b_up = float(up_b.mean())
    b_mkt = float(mkt_b.mean())
    b_annual = (b_up - b_mkt) * DAYS_PER_YEAR

    # ── A 及其两项分量（逐行，逐字复刻 `rolling_stats`）──
    # ⚠️⚠️ 第一版在这里自己算 `a_annual = (a_up − a_mkt) × 244`，**两个变异活了下来**：
    #   把 `a_up` 或 `a_mkt` 换成**逐日**均值（即把 A 悄悄改成 B 的口径），
    #   `resid` 仍为 0（B 对拍也不查 A）——因为 `resid` 是**代数恒等式**，
    #   A 与分解项一起变，恒等式照样闭合。
    #   ⇒ **预注册第二节字面写的主指标，其口径当时没有任何守卫钉住。**
    #
    # 修法：**A 的点估计不再自算**，直接引用 `_metrics()` 的返回值——
    # 它就是主产物 `rows[i].sign_fixed.annual_up_excess` 那个数。
    # 下面的 `a_up`/`a_mkt` 只**用于分解**，并断言二者与点估计逐位锁死。
    up = pred[pred["direction"] == "up"]
    a_up = float((up["actual_pct"] - up["cost"]).mean())
    a_mkt = float(pred["market_pct"].mean())
    a_annual_rebuilt = (a_up - a_mkt) * DAYS_PER_YEAR
    a_annual = float(_metrics(pred)["annual_up_excess"])
    if not math.isclose(a_annual_rebuilt, a_annual, rel_tol=1e-12, abs_tol=1e-15):
        raise AssertionError(
            f"A 的点估计与它的分解**锁不死**：`_metrics` 给 {a_annual!r}，"
            f"而按 `mean_row(up净) − mean_row(market)` 重建得 {a_annual_rebuilt!r}。\n"
            "⇒ 说明 `rolling_stats` 的真实算法不是本函数以为的那个 —— "
            "分解式已失效，不许用它解释 A − B 的差。")

    # ── 闭合断言：分解必须逐位成立，否则不许出产物 ──
    resid = a_annual - b_annual - DAYS_PER_YEAR * ((a_up - b_up) - (a_mkt - b_mkt))
    if not math.isclose(resid, 0.0, abs_tol=1e-12):
        raise AssertionError(
            f"A−B 的两项分解**不闭合**，残差 {resid:.3e}/年。\n"
            "⇒ 说明差值还有第三个来源，本函数不知道它是什么。\n"
            "⇒ 纪律「缺档必须报错，绝不回落」：不带未解释的差出产物。")

    # ── MIN_BASKET 对照臂：逐日 + `size >= MIN_BASKET`（照抄 verify.py:398）──
    sz = size.reindex(days)
    keep = sz >= MIN_BASKET
    c_annual = float((up_b[keep] - mkt_b[keep]).mean()) * DAYS_PER_YEAR if keep.any() else None

    # 被丢掉的日子贡献了多少 ⇒ MIN_BASKET 到底在挡什么污染
    dropped = days[~keep]
    contrib = ((up_b - mkt_b).loc[dropped] * DAYS_PER_YEAR).sum() if len(dropped) else 0.0

    sd_all = float(series_b.std(ddof=1)) if len(series_b) > 1 else 0.0
    n_all = len(series_b)
    ser_c = (up_b - mkt_b)[keep].dropna()
    sd_c = float(ser_c.std(ddof=1)) if len(ser_c) > 1 else 0.0

    def _pack(point: float | None, sd_day: float, n: int) -> dict:
        """⚠️ `sd_day` 是**日** sd，不是年 sd。

        三条年化乘数各不相同（层 93，守卫 `tests/test_annualization_is_a_sqrt_of_time.py`）：

        ```
        均值 × T          （年化均值）
        sd   × √T         （年化 sd）    ← 本函数内部做的
        SE   × T / √n     （标准误，n 是样本天数）
        ```

        ⚠️ 第一版把年化 sd 当日 sd 用、直接 `se = sd/√n × T` ⇒ **高估 √244 ≈ 15.6 倍**。
        """
        sd_annual = sd_day * math.sqrt(DAYS_PER_YEAR)
        if point is None or n <= 1:
            return {"annual_pct": point, "n_days": n, "sd_annual_pct": sd_annual,
                    "se_annual_pct": None, "plus1sd_pct": None}
        se = sd_day / math.sqrt(n) * DAYS_PER_YEAR
        return {"annual_pct": point, "n_days": n, "sd_annual_pct": sd_annual,
                "se_annual_pct": se, "plus1sd_pct": point + se}

    # ★ B 的 sd/se **不自己算**——直接引用 `by_year.pooled()`（已对拍 0 mismatch）。
    # 这样「读快照的守卫等于没守」不成立：B 的数字有一个已对拍的独立实现背书。
    ref_b = pooled_ref(pred)

    return {
        "A_rowwise": _pack(a_annual, sd_all, n_all),
        "B_daily": _pack(b_annual, sd_all, n_all),
        "C_daily_min_basket": _pack(c_annual, sd_c, len(ser_c)),
        "B_vs_pooled_ref": {
            "note": "B 与 `regime_control_by_year.py::pooled()` 的逐位对拍",
            "annual_pct": b_annual,
            "ref_annual_up_excess_NET_pct": ref_b["annual_up_excess_NET_pct"],
            "ref_sd_annual_pct": ref_b["annual_sd_pct"],
            "ref_se_annual_pct": ref_b["se_annual_pct"],
            "closes": (math.isclose(b_annual, ref_b["annual_up_excess_NET_pct"],
                                    rel_tol=1e-12)
                       and math.isclose(sd_all * math.sqrt(DAYS_PER_YEAR),
                                        ref_b["annual_sd_pct"], rel_tol=1e-12)
                       and math.isclose(_pack(b_annual, sd_all, n_all)["se_annual_pct"],
                                        ref_b["se_annual_pct"], rel_tol=1e-12)),
        },
        "decomposition": {
            "identity": ("A − B = 244 × [ (mean_row(up净) − mean_daily(up净))"
                         " − (mean_row(market) − mean_daily(market)) ]"),
            "term_up_rows_minus_daily": a_up - b_up,
            "term_market_rows_minus_daily": a_mkt - b_mkt,
            "reconstructed_A_minus_B": DAYS_PER_YEAR * ((a_up - b_up) - (a_mkt - b_mkt)),
            "observed_A_minus_B": a_annual - b_annual,
            "residual_per_year": resid,
            "closes_bitwise": math.isclose(resid, 0.0, abs_tol=1e-12),
        },
        "basket_size": {
            "min": int(size.min()), "median": float(size.median()),
            "max": int(size.max()),
            "n_days_lt_min_basket": int((sz < MIN_BASKET).sum()),
            "n_days_lt_top_n": int((sz < 10).sum()),
            "min_basket_constant": MIN_BASKET,
            "dropped_days_contribution_annual_pct": contrib,
        },
    }


def verdict(point: float | None, plus1sd: float | None) -> str:
    """预注册第二节**逐字**的四条判决规则，一个字不改。

    ```
    annual_up_excess(K=21) ≥ 门槛 且逐年不出现符号翻转 → REACHES_HURDLE
    ≥ 门槛 但逐年有 ≥1 年为负                        → REACHES_BUT_UNSTABLE
    < 门槛 但 +1σ ≥ 门槛                              → UNDERPOWERED
    < 门槛 且 +1σ < 门槛                              → INSUFFICIENT
    ```

    ⚠️ 前两条的分档靠**逐年的主指标符号**，而逐年数据在
    `regime_control_judge_window_by_year_*.json::sign_fixed[*].annual_up_excess`
    （本脚本不重算逐年 ⇒ 不造第二个实现）。

    ⇒ 所以点估计 ≥ 门槛时本函数**只能**交出「两档里落在哪一档还没定」这个事实，
    ⚠️ **不许**在这里发明一个既不是 `REACHES_HURDLE` 也不是
    `REACHES_BUT_UNSTABLE` 的字符串充数——那会让判决文档引到一个假档位。
    """
    if point is None:
        return "INSUFFICIENT"
    if point >= HURDLE:
        return ("NEEDS_YEARLY_SIGN__SPLIT_BETWEEN_REACHES_HURDLE"
                "_AND_REACHES_BUT_UNSTABLE")
    if plus1sd is not None and plus1sd >= HURDLE:
        return "UNDERPOWERED"
    return "INSUFFICIENT"


def _a_parity(ref_annual: float, a_annual_pct: float) -> dict:
    """对拍①：**本脚本的 A 逐位等于主产物的 `sign_fixed.annual_up_excess`**。

    ## 为什么它必须是独立函数（第 124 层：判据不可问就等于没有守卫）

    第一版把这段内联在 `main()` 里，于是它的前提只能靠**跑一次 10 分钟**
    （面板 270s + 重放 293s）来验 —— 而真实发生的事是：跑完了、被硬闸拒写、
    才知道自己把一个**已经年化**的数又除了一次 244。
    ⇒ 「跑一次问一次」的问题不是慢，是**没人愿意为一条判据付 10 分钟**，
    于是这条判据事实上**从未被单独问过**。

    ⚠️ 两条量在同一个函数里是**同一个数**：
    `a_annual_pct` 来自 `_pack(a_annual, …)` 的 `annual_pct`，
    而 `a_annual = float(_metrics(pred)["annual_up_excess"])` ⇒ **已经是年化**。
    ⇒ 这里**不许**再乘/除 `DAYS_PER_YEAR`：那一层换算在
    `decompose()` 里的 `a_annual_rebuilt = (a_up − a_mkt) * DAYS_PER_YEAR`
    已经做完了。
    """
    return {
        "_metrics_annual_up_excess": ref_annual,
        "A_rowwise_annual_pct": a_annual_pct,
        "closes": math.isclose(ref_annual, a_annual_pct, rel_tol=1e-12),
    }


def _sha(rel: str) -> str:
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def main(argv=None) -> int:
    # 第 125 层：`stamp()` 只证明「盖章那一刻盘上的字节」，而这份产物要跑几十分钟。
    # ⇒ **开跑时**先抓一次字节哈希，写盘那一刻对不上就**拒盖**
    # （那枚章会变成**反向的错误证据**）。
    # 🚨 **必须落在 `main()` 的第一条可执行语句**（第 131 层）：
    # 放在 `parse_args()` 之后就晚了一拍 —— 那几行里若盘上的字节被换掉，
    # 盖出来的章指的是**另一份**字节。
    expect = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--start", default="2021-01-04")
    ap.add_argument("--end", default="2026-10-09")
    ap.add_argument("--k", type=int, default=LIVE_REBALANCE_EVERY)
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_min_basket_panel_cache.pkl"))
    ap.add_argument("--out", type=Path, default=Path(
        "data/reports/regime_control_min_basket_control_2026-10-15.json"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用面板缓存。**默认重建**——见文件头「缓存纪律」")
    args = ap.parse_args(argv)

    if not args.cache.name.endswith(".pkl"):
        raise SystemExit(f"缓存名须以 .pkl 结尾：{args.cache}")
    cache = args.cache.with_name(f"{args.cache.stem}_{args.start}_{args.end}.pkl")

    t0 = time.time()
    xs = _panel(args.data_root, cache, args.reuse, start=args.start, end=args.end)
    dates = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"对照臂重放 K={args.k}｜{len(dates)} 个交易日｜{time.time()-t0:.0f}s",
          flush=True)

    # ⚠️ 取数层的**代码哈希**随产物落盘：`--reuse` 时 `panel_cache.load_cache`
    # 跳过 `_check_fingerprint`（`:300-301`）⇒ 那道保护在复用路径上是空的，
    # 必须由调用方自己补（纪律「守卫要测对层」——用哈希，不用 mtime）。
    code_hashes = {rel: _sha(rel) for rel in (
        "scripts/regime_control_sweep.py", "aplus/backtest.py",
        "aplus/panel_cache.py", "aplus/verify.py")}

    arms = []
    for buf in (None, 3.0):
        pred = evaluate_weights(xs, SIGN_FIXED, top_n=10, bottom_n=10,
                                dates=dates, rebalance_every=args.k,
                                buffer_multiple=buf)
        if not len(pred):
            arms.append({"buffer_multiple": buf, "empty": True})
            continue
        ref = _metrics(pred)                    # ★ 原跑法自己的度量函数
        d = decompose(pred)

        rows = {}
        for key in ("A_rowwise", "B_daily", "C_daily_min_basket"):
            p = d[key]
            rows[key] = {**p, "verdict": verdict(p["annual_pct"], p["plus1sd_pct"])}

        arms.append({
            "buffer_multiple": buf,
            "parity": {
                # 对拍①的实现见 `_a_parity()`——抽成独立函数是为了让它
                # **1 毫秒可问、可变异**（第 124 层），而它在第一版内联在
                # `main()` 里，于是只能靠跑一次 10 分钟来问，问出来的第一件事
                # 是「它一直是错的」。
                **_a_parity(ref["annual_up_excess"],
                             rows["A_rowwise"]["annual_pct"]),
                "_metrics_annual_cost": ref["annual_cost"],
            },
            # 对拍②：B 与 by_year 已对拍过的 `pooled()` 逐位比
            "B_vs_pooled_ref": d["B_vs_pooled_ref"],
            "calibers": rows,
            "decomposition": d["decomposition"],
            "basket_size": d["basket_size"],
        })
        print(f"  buffer={buf}: A={rows['A_rowwise']['annual_pct']:+.6%} "
              f"B={rows['B_daily']['annual_pct']:+.6%} "
              f"C={rows['C_daily_min_basket']['annual_pct']:+.6%} "
              f"| 残差={d['decomposition']['residual_per_year']:.1e} "
              f"| B对拍={d['B_vs_pooled_ref']['closes']} "
              f"| 成分数 {d['basket_size']['min']}~{d['basket_size']['max']}",
              flush=True)

    # ⚠️ 任何一处对拍不闭合 = **不许写产物**（纪律「缺档必须报错，绝不回落」）。
    # 症状与「守对了」逐位相同：数字照样能出，而引用它的人会拿到一个
    # 与主产物/与已对拍实现**不同的**判决基线。
    bad = []
    for a in arms:
        if a.get("empty"):
            continue
        if not a["parity"]["closes"]:
            bad.append(f"buffer={a['buffer_multiple']}: A 与主产物不闭合")
        if not a["B_vs_pooled_ref"]["closes"]:
            bad.append(f"buffer={a['buffer_multiple']}: B 与 by_year.pooled() 不闭合")
    if bad:
        raise AssertionError(
            "🚨 对拍不闭合，**不写产物**：\n  " + "\n  ".join(bad) +
            "\n⇒ 本脚本的 A/B 会与它们各自的权威实现不同 ⇒ 判决不得引用本产物。")

    out = {
        "kind": "regime_control_min_basket_control",
        "provenance_note": (
            "⚠️ **本产物是事后补跑，不是预注册跑法的产物。**"
            "它是 PREREG 附则一第三节第 3 条要求的 MIN_BASKET 对照臂，"
            "外加 A/B 两个口径的逐位分解。不改任何判据、不覆盖主产物。"),
        "window": [args.start, args.end],
        "rebalance_every": args.k,
        "min_basket_constant": MIN_BASKET,
        "min_basket_source": "aplus/verify.py:19（导入，不写字面量）",
        "hurdle_from_hurdle_rate": HURDLE,
        "hurdle_derivation": (
            "hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K, "
            "rounds_per_year=244/LIVE_REBALANCE_EVERY, benchmark_annual=0.0)"),
        "caliber_note": (
            "A=逐行（`rolling_stats`：mean_row(up净) − mean_row(market)）；"
            "B=逐日（`_daily_series`：mean_daily(up净) − mean_daily(market)）；"
            "C=逐日 + `size >= MIN_BASKET`。⚠️ 预注册第二节字面写的是 "
            "`sign_fixed.annual_up_excess`，即 **A**。"
            "⚠️ 本脚本**不裁定** A/B 谁是「对」的口径——只给数与分解。"),
        "code_sha256": code_hashes,
        "arms": arms,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(args.out, GENERATOR, root=ROOT,
                     data_root=args.data_root, expect_bytes=expect)
    print(f"\n写出 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
