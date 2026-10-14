"""持仓权重方案：等权 vs 波动率倒数加权 —— 预注册 `docs/TODO_basket_weighting_2026-10-15.md`。

## 这个实验为什么值得做（它首先是一次**诊断**）

石川 7.5.3：**若所有持仓两两相关系数都等于 1，则 1/σ 加权与等权完全等价。**
⇒ 「1/σ 能不能压低 sd」这个问题，**等价于**「这个篮子有几个独立赌注」。

- 降得下来 ⇒ 有效独立赌注数 > 10，还有一整个降 sd 的空间没用
- 降不下来 ⇒ ρ≈1 ⇒ 「加榜宽」「风险平价」「多因子分散」**三条路同时封死**

**所以零结果比正结果更有决策价值**，这是预注册它的首要理由。

## ★ 为什么三个臂只需要跑**一次**回测

选股与仓位在本项目架构上是**解耦**的：`evaluate_weights` 只吐逐行的
`actual_pct`，等权聚合发生在下游（`aplus.evolve_race._daily_series`）。

⇒ 三个臂**共用同一次选股结果**，只换聚合时乘在行上的权重。
这不只是省时间：若每个臂各跑一次选股，臂间的差就混进了「选股不同」这个
额外变量，而那正是本实验**不许变**的东西。

## ★ 加权聚合不会扭曲成本口径

`cost` 是**组合级**常数（`cost_drag` 算一次，写给涨腿每一行）。
加权后 `Σw·(r−c) = Σw·r − c·Σw = Σw·r − c`（因 `Σw=1`）⇒ 成本仍是一次。

## 防泄露：窗口右端严格 ≤ 决策日 D

预注册 §4 最容易犯、也最难发现的错：`entry_lag=1`（D+1 买 / D+2 卖）下，
用「决策日往前 60 天」的自然写法很容易取到 D+1。
⇒ `trailing_sigma` **只接受日期上界**，窗口是 `[D-window, D]` 闭区间，
且守卫直接查这一点（`tests/test_basket_weighting.py`），不靠 review。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
# ★ **两个都要插。** 只插 `scripts/` 时，命令行直接跑
#   `python scripts/basket_weighting.py` 必然 `ModuleNotFoundError: No module named 'aplus'`
#   （实测 2026-10-15）—— 因为脚本运行时 `sys.path[0]` 是 `scripts/`，仓库根不在里面。
#   ⚠️ 而 `tests/test_basket_weighting.py` 用 `importlib.util.spec_from_file_location`
#   导入，**自带仓库根**，于是**绕过了这一层依赖** ⇒ 守卫全绿、脚本根本跑不起来。
#   对照兄弟脚本 `ic_guided_race.py:48-49`：它两个都插。
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aplus import verify as verify_mod                     # noqa: E402
from aplus.backtest import build_cross_sections, evaluate_weights  # noqa: E402
from aplus.costs import annual_friction                    # noqa: E402
from backtest_walk_forward_weights import (                # noqa: E402
    ENTRY_LAG, FACTOR_NAMES, _daily, _t, at_cost, real_cost_for,
    rounds_per_year, weights_by_year,
)

# ── 预注册 §4：写死，不许事后调 ────────────────────────────────
WINDOW = 60                 # σ 的交易日窗口（K=21 的 3 倍）
MIN_SIGMA = 1e-4            # 防 1/σ 爆炸
MAX_DROP = 0.20             # σ 不可用的票超过 20% ⇒ 该臂作废
MAIN_N = 100                # 主档榜宽（唯一已测出显著 t=2.42 的档）
MAIN_K = 21                 # 实盘换仓周期
JUDGED_FROM = "2021-01-01"  # 必须与判决窗口一致

ARMS = ("A0_equal", "A1_inv_total", "A2_inv_excess")

#: 本金。`factor_width_mde.py` 里也有一个同值的常量 ——
#: 那是脚本级常量不是 `aplus.costs` 的，两处独立持有是**故意的**：
#: 库的常量若被实验改掉，库的守卫会跟着一起变绿。⚠️ 两处若不等，
#: 「成本」这个词就变成了两个量（CLAUDE.md 第 7 种形状）。
CAPITAL_50K = 50_000.0


# ───────────────────────── σ 的计算 ─────────────────────────
def trailing_sigma(daily_map: dict, code: str, upto, window: int = WINDOW,
                   market: dict | None = None) -> float:
    """`upto`（含）往前 `window` 个交易日的收益 sd。**NaN 表示不可用。**

    ★ 两行的顺序：**先砍未来，再取窗口**。

    ⚠️ 我第一版把这条的因果写错了，守卫也跟着测错了东西。原文是
      「顺序反了窗口就含 D 之后的行 —— 真实泄露」。**那是假的**：
      下面 `tmp[tmp.date <= upto]` 这一行在**任何顺序下**都会把 D 之后
      的行滤掉，所以顺序**不可能**造成泄露。
      顺序颠倒真正造成的是**窗口变陈旧/变短**：`.tail()` 先落在全历史末尾
      （可能在 `upto` 之后很远），滤完只剩更早的几十行；未来若长过
      `window+1` 天则**滤空** ⇒ 返回 NaN ⇒ 该票被静默剔除出篮子。
      这是**另一种失效**，症状是「某些票莫名其妙不见了」。

    ⇒ 结论：**防泄露来自过滤本身（与顺序无关），顺序决定取哪个窗口。**
      测顺序的探针必须是「**把 D 之后的行删掉**」，而不是「把 D 之后的行
      改成 1e9」—— 后者打不中（被污染的行照样被滤掉，两边窗口一致）。

    `market` 给了就算**超额** sd（对市场），没给就是**原始** sd。
    """
    df = daily_map.get(code)
    if df is None or df.empty or "close" not in df.columns:
        return float("nan")
    dates = pd.to_datetime(df["date"] if "date" in df.columns else df.index)
    tmp = pd.DataFrame({"date": dates,
                        "close": pd.to_numeric(df["close"],
                                               errors="coerce").to_numpy()})
    tmp = tmp.dropna().sort_values("date")
    # ★ 顺序：先砍未来，再取窗口
    tmp = tmp[tmp["date"] <= pd.Timestamp(upto)]
    if len(tmp) < 4:
        return float("nan")
    tmp = tmp.tail(window + 1)        # window+1 个收盘价 ⇒ window 个收益
    ret = tmp["close"].pct_change().to_numpy()[1:]
    if market is not None:
        mk = np.array([float(market.get(str(d.date()), np.nan))
                       for d in tmp["date"]], dtype=float)[1:]
        ret = ret - mk
    ret = ret[np.isfinite(ret)]
    if len(ret) < 3:
        return float("nan")
    s = float(np.std(ret, ddof=1))
    return s if np.isfinite(s) else float("nan")


def arm_weights(rows: pd.DataFrame, mode: str, daily_map: dict,
                market: dict, as_of: str, window: int = WINDOW) -> tuple[dict, int]:
    """给某个决策日 D 的涨腿行算权重。返回 `(权重表, 被剔除的行数)`。

    ★ **D 是显式入参，不是从表里刨出来的。**
      第一版写成 `rows["date"].iloc[0]`，而调用方 `_weighted_daily` 建的
      `work` 里那一列叫 `d` ⇒ `KeyError: 'date'`。

      ⇒ 那个 `KeyError` 的价值不在于它崩了，在于它暴露了**契约是隐式的**：
      「判决日是哪一天」这件事，不由谁决定、只由**哪一列碰巧叫 date** 决定。
      只要调用方把列一改名，同一份数据就会用不同的 σ 窗口算出不同的权重，
      而报告上完全看不出来。**显式参数让「用哪一天」变成调用方必须声明的事。**

    ★ 只在**调仓日**有意义吗？—— 不是。本实验对**每日**的持仓组合加权：
      非调仓日持有上期篮子，重新加权就等于**无交易地偷换权重**，
      那是不存在的策略。所以调用方必须只在调仓日调用本函数，
      由 `_weighted_daily` 负责把上期权重沿用到下一次调仓。

    ⇒ 这条限制必须写在函数契约里，否则「无成本再平衡」会以
      「提高收益」的形式回来 —— 那是本项目最贵的一类假收益。
    """
    if mode == "equal":
        w = {c: 1.0 for c in rows["code"]}
        return w, 0
    use_market = mode.endswith("excess")
    w, dropped = {}, 0
    for code in rows["code"]:
        s = trailing_sigma(daily_map, code, as_of, window,
                           market=market if use_market else None)
        if not np.isfinite(s) or s <= MIN_SIGMA:
            dropped += 1
            continue
        w[code] = 1.0 / s
    tot = sum(w.values())
    if not tot:
        return {}, dropped
    return {k: v / tot for k, v in w.items()}, dropped


# ─────────────────── 逐日加权聚合 ───────────────────
def _weighted_daily(leg: pd.DataFrame, mode: str, daily_map: dict,
                    market_map: dict, window: int, rebalance_every: int,
                    min_basket: int) -> tuple[pd.Series, dict]:
    """把逐行结果聚成逐日**净超额**序列（口径与 `_daily_series` 逐字一致）。

    与 `aplus.evolve_race._daily_series` 的三处必须完全相同：
      ① `net = actual_pct − cost`（只有涨腿付成本）
      ② 逐日减市场
      ③ 成分不足 `min_basket` 的日子**丢掉**
    否则臂间不可比，而表格看上去完全正常。
    """
    leg = leg.dropna(subset=["actual_pct"])
    if leg.empty:
        return pd.Series(dtype=float), {"days": 0}
    net = leg["actual_pct"].to_numpy(dtype=float)
    if "cost" in leg.columns:
        net = net - leg["cost"].to_numpy(dtype=float)
    work = pd.DataFrame({"pred_date": leg["pred_date"].to_numpy(),
                         "code": leg["code"].to_numpy(),
                         "net": net,
                         "mkt": leg["market_pct"].to_numpy(dtype=float),
                         "d": pd.to_datetime(leg["date"]).to_numpy()})
    work = work.dropna(subset=["net"])

    daily, held = [], {}
    n_dropped_total = 0
    dates = sorted(work["pred_date"].unique())
    for i, dt in enumerate(dates):
        day = work[work["pred_date"] == dt]
        is_rebal = (i % rebalance_every == 0)
        # ★ 不按 mode 特判。第一版写 `if mode != "equal" and (...)`，
        #   而 `arm_weights(..., "equal", ...)` 会返回全 1 权重 —— 于是等权臂
        #   **永远不进这个分支**，`held` 恒为 `{}`，
        #   下面 `if not held: continue` 把每一天都丢掉 ⇒ **A0 基线产出空序列**。
        #   而 A0 空 ⇒ `decide` 拿到 NaN 的 sd_A0 ⇒ **拒绝出判决**。
        #   特判在这里不是「更快」，是让基线臂静默消失。
        if is_rebal or not held:
            w, dropped = arm_weights(day, mode, daily_map, market_map, dt, window)
            n_dropped_total += dropped
            if len(day) and dropped / len(day) > MAX_DROP:
                continue
            held = w
        if not held:
            continue
        # 只对**当日真实存在且有收益**的行归一化 —— 与 `groupby.mean()` 跳过 NaN 同义
        idx = [j for j, c in enumerate(day["code"]) if c in held
               and np.isfinite(day["net"].iloc[j])]
        if len(idx) < min_basket:
            continue
        wsum = sum(held[day["code"].iloc[j]] for j in idx)
        if wsum <= 0:
            continue
        port = sum(held[day["code"].iloc[j]] * day["net"].iloc[j] for j in idx) / wsum
        daily.append((dt, port - float(np.nanmean(day["mkt"].to_numpy(dtype=float)))))
    s = pd.Series(dict(daily), dtype=float).sort_index()
    return s, {"days": int(len(s)), "dropped": int(n_dropped_total)}


# ───────────────────────── 判决 ─────────────────────────
def decide(arms: dict, per_year: dict) -> tuple[str, str]:
    """**判决函数**——预注册 §6，看到任何数据之前写死。

    `arms`: `{臂名: {"sd0":…, "drop_pct":…, "mean_cost":…, "n_days":…}}`
    `per_year`: `{臂名: {年: drop_pct}}`（逐年降幅，用于一致性硬要求）

    分支：
      SD_WORKS    降幅 ≥0.20 且 均值代价 ≤ 降幅/2，且逐年 ≥4/6 为正
      SD_NEUTRAL  最好一臂 降幅 < 0.10
      SD_TRADEOFF 降幅 ≥0.10 但 均值代价 > 降幅/2
      SD_MIXED    逐年方向不一致
    """
    base_sd = arms["A0_equal"]["sd"]
    if not base_sd or not np.isfinite(base_sd):
        return None, "基线臂 sd 不可用，拒绝出判决"
    rows = {}
    for name in ARMS:
        a = arms[name]
        sd0 = a.get("sd")
        if not sd0 or not np.isfinite(sd0):
            continue
        drop = (base_sd - sd0) / base_sd
        # ★ 均值代价除以 **sd_A0**（不是 mean_A0）：两个均值都是 6%~9%，
        #   除以均值会得到很大的相对数、看起来像「牺牲了很多」；
        #   除以 sd 才是「以风险为单位衡量收益损失」——风险平价的原始命题。
        cost = (arms["A0_equal"]["mean"] - a.get("mean", np.nan)) / base_sd
        yrs = per_year.get(name, {})
        pos = sum(1 for v in yrs.values() if v is not None and v > 0)
        rows[name] = {"drop_pct": drop, "mean_cost_sd_units": cost,
                      "years_positive": pos, "n_years": len(yrs)}
    if not rows:
        return None, "没有任何臂产出可用数字"

    best = max(rows, key=lambda k: rows[k]["drop_pct"])
    bd, bc, bpos, bn = (rows[best]["drop_pct"], rows[best]["mean_cost_sd_units"],
                        rows[best]["years_positive"], rows[best]["n_years"])

    # 🚨 逐年一致性是**硬要求**，先判它：
    #   66 个独立观测上的一个均值差不可信，逐年一致性是唯一不额外要数据
    #   就能拿到的稳健性证据。
    if bn and bpos < max(1, int(np.ceil(bn * 0.67))):
        return "SD_MIXED", (f"最好一臂 {best} 逐年降幅仅 {bpos}/{bn} 年为正 "
                            f"⇒ 方向不稳，本档作废")

    if bd < 0.10:
        return "SD_NEUTRAL", (
            f"最好一臂 {best} 的 sd 降幅只有 {bd:+.1%}（< 10%）"
            f"⇒ ρ≈1，持仓≈一个赌注。"
            f"⚠️ 这**不等于**「低波动没用」——那是另一个量、另一个问题。"
            f"下一步：停止一切「靠分散化降 sd」的路。")
    if bc > bd / 2:
        return "SD_TRADEOFF", (
            f"最好一臂 {best} 降幅 {bd:+.1%}，但均值代价 {bc:+.3f} sd 单位 "
            f"> 降幅的一半 ⇒ 降噪要赔收益。本项目的约束是要**测量能力**"
            f"而不是要好看 ⇒ 记为取舍，不采用。")
    if bd < 0.20:
        return "SD_TRADEOFF", (
            f"最好一臂 {best} 降幅 {bd:+.1%}，未达 0.20 的 SD_WORKS 门槛"
            f"（但已 >0.10，不是 NEUTRAL）⇒ 记为取舍，不采用。")
    return "SD_WORKS", (
        f"最好一臂 {best} 降幅 {bd:+.1%} ≥ 0.20，且均值代价 {bc:+.3f} sd 单位 "
        f"≤ 降幅的一半，逐年 {bpos}/{bn} 年为正 ⇒ 仪器真的变好了。"
        f"★ 注意：sd 降只说明**仪器**变好，不说明 alpha 变大。")


# ───────────────────────── 主流程 ─────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--start", default=JUDGED_FROM)
    ap.add_argument("--end", default="")
    ap.add_argument("--n", type=int, default=MAIN_N)
    ap.add_argument("--k", type=int, default=MAIN_K)
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/basket_weighting.json"))
    args = ap.parse_args(argv)

    if not args.start.startswith("2021"):
        print(f"🚨 判决窗口必须从 2021 起（得到 {args.start}）——"
              f"与 `factor_width_mde` 的口径必须一致，否则臂间不可比。", file=sys.stderr)
        return 2

    # ★ 原来这里写的是 `from aplus.snapshot import Snapshot` —— 实测
    #   `aplus/snapshot.py` **存在**（9531 字节）却**没有定义 `Snapshot`**：
    #   `ImportError: cannot import name 'Snapshot'`。模块搬走/改名时调用点没跟着改。
    #   正确的类按 **API 面**判定，不靠猜：`build_cross_sections(store, …)` 把 store
    #   透传给 `_resolve_maps` → `load_factors_map(store)`，后者只用 `store.root`
    #   与 `store.read_factor` —— `LocalStore` 两个都有，且 `.root` 就是主库根。
    from aplus.store import LocalStore

    t0 = time.time()
    store = LocalStore(args.data_root)
    xs = build_cross_sections(store, args.start, args.end or None)
    daily_map = xs.get("_daily_map") or {}
    market_map = xs.get("_market") or {}
    print(f"面板 {len(daily_map)} 只（{time.time()-t0:.0f}s）", flush=True)
    if not daily_map:
        print("🚨 面板为空，拒绝出判决", file=sys.stderr)
        return 2

    dates = sorted(d for d in xs["_dates"] if d >= args.start)
    years = sorted({d[:4] for d in dates})
    orig_min = verify_mod.MIN_BASKET
    orig_mb = args.n
    try:
        verify_mod.MIN_BASKET = max(1, orig_min * orig_mb // 10) if orig_min else 1
        frames = []
        for y in years:
            yd = [d for d in dates if d[:4] == y]
            if len(yd) < 60:
                continue
            res = evaluate_weights(xs, weights=None, top_n=args.n,
                                   bottom_n=args.n, dates=yd,
                                   rebalance_every=args.k, execution_gate=True)
            if res is None or res.empty:
                continue
            frames.append(res)
            print(f"  {y}: {len(res)} 行", flush=True)
        if not frames:
            print("🚨 无任何年份产出，拒绝出判决", file=sys.stderr)
            return 2
        allrows = pd.concat(frames, ignore_index=True)
    finally:
        verify_mod.MIN_BASKET = orig_min       # ⚠️ 必须还原（同进程后续任务会静默按错下限跑）

    leg = allrows[allrows["direction"] == "up"]
    arms, per_year = {}, {}
    for name in ARMS:
        mode = "equal" if name == "A0_equal" else name.split("_", 1)[1]
        s, meta = _weighted_daily(leg, mode, daily_map, market_map,
                                  WINDOW, args.k, verify_mod.MIN_BASKET)
        if len(s) < 60:
            print(f"{name}: 样本 {len(s)} 天，不构成测量")
            continue
        sd = float(np.std(s.to_numpy(dtype=float), ddof=1) * np.sqrt(244))
        arms[name] = {"sd": sd, "mean": float(np.mean(s) * 244),
                      "n_days": meta["days"], "dropped": meta["dropped"]}
        py = {}
        for y in years:
            sub = s[s.index.str[:4] == y]
            if len(sub) < 20:
                continue
            py[y] = float(np.std(sub.to_numpy(dtype=float), ddof=1))
        per_year[name] = py

    if "A0_equal" not in arms:
        print("🚨 基线臂缺失，拒绝出判决", file=sys.stderr)
        return 2

    base_sd = arms["A0_equal"]["sd"]
    for name in arms:
        arms[name]["sd_drop_pct"] = (base_sd - arms[name]["sd"]) / base_sd
    py_drop = {}
    for name, py in per_year.items():
        if "A0_equal" not in py:
            py_drop[name] = {}
            continue
        b = py["A0_equal"]
        py_drop[name] = {y: (b - v) / b if b else None for y, v in py.items()}

    verdict, why = decide(arms, py_drop)
    out = {
        "verdict": verdict, "verdict_why": why,
        "config": {"n": args.n, "k": args.k, "window": WINDOW,
                   "start": args.start, "min_sigma": MIN_SIGMA,
                   "max_drop": MAX_DROP, "capital": CAPITAL_50K,
                   "friction_annual_pct": float(
                       100 * annual_friction(CAPITAL_50K, 1.0, args.n,
                                             rounds_per_year(args.k)))},
        "arms": arms, "per_year_sd": per_year, "per_year_sd_drop": py_drop,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'臂':<14}{'sd/年':>9}{'降幅':>9}{'均值代价/sd':>14}{'天数':>7}")
    for name in ARMS:
        if name not in arms:
            continue
        a = arms[name]
        cost = (arms["A0_equal"]["mean"] - a["mean"]) / base_sd
        print(f"{name:<14}{a['sd']:>8.2f}%{a['sd_drop_pct']:>+8.1%}{cost:>14.3f}{a['n_days']:>7}")
    print(f"\n判决：{verdict}\n{why}")
    print(f"\n产物 → {args.out}")
    return 0


# 测试别名：语义上「这就是判决函数本身」，不是替身。
decide_for_test = decide
trailing_sigma_for_test = trailing_sigma

if __name__ == "__main__":
    raise SystemExit(main())
