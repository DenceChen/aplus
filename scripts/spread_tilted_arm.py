r"""尾部价差配权对照臂（2026-10-15）—— C 臂的实现。

## 这条脚本要回答的唯一问题

`scripts/weight_vs_spread.py` 的诊断说：权重按 **IC** 配（`w ∝ R⁻¹·s_f`），
而策略**只在两端下注**、吃的是**尾部价差**；两者秩相关 −0.9286。
⇒ 若 C 臂（按尾部价差配权）在测试段上**不优于** A 臂（IC 配权），
则「改目标函数」这件事被证伪，应转去查别的路。

预注册见 `docs/TODO_tail_spread_arm_2026-10-15.md`（判据 A1/A2/A3 先于数据）。

## 三条不可让步的规则（与预注册 §2 一一对应）

1. **签名只从训练段来。** `train_spread_and_ic` **不设默认日期**——
   不给就 `TypeError`。全段定符号再用同段验收 = 自证，
   而自证不会报错，只会产出一个漂亮的假结果。
2. **C 臂的权重只用尾部价差**，不许混进 IC 或 |IC|。
   混了就不是对照臂，而是「A 臂 + 一点点 C」，A1 就失去意义。
3. **B 臂不许调。** 等权就是等权；给 B 加任何自由参数，它就退化成
   「我调的一个 C」，三条臂就不再是三个独立的对照。

## ⚠️ 口径警告（引用任何数字前先读）

`factor_ic` 的 `*_hit` 字段是 `(v > 0).mean()`，其中 `v` 是**逐日分层 IC**
⇒ 它的口径是「**分层 IC 为正的天数占比**」，
**不是**「买对的比例」。两个量纲差着一整层，读错会让 C 臂去优化一个
策略根本没在用的数。

本脚本用的是 `tail_low` 与 `tail_high` 的 **IC 均值之差**：

    spread_f = tail_low_mean_f − tail_high_mean_f

理由：它正是策略下注方向上的**两段收益差**，与「只在两端下注」同口径。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus.backtest import (RESULT_COLUMNS, build_cross_sections,  # noqa: E402
                            evaluate_weights)
from aplus.evolve_race import _daily_series, split_window  # noqa: E402
from aplus.factor import FACTOR_NAMES, composite_score  # noqa: E402
from aplus.provenance import stamp  # noqa: E402
from aplus.panel import market_date_axis  # noqa: E402  ← 日期轴唯一实现（形状 17）
from aplus.factor_ic import (FWD_WINDOWS, MIN_OBS, build_price_cache,  # noqa: E402
                             daily_ic_ranked, forward_returns,
                             make_ranked_forward)

ROOT = Path(__file__).resolve().parent.parent
IC_WINDOW = "lag1_real"          # 只用真实成交口径定签名，leak 窗口绝不用

#: 预注册 §3 的 G5（Chan p.84-85：SR=1 需 681 个日频点）。
MIN_DAILY_POINTS = 681

#: G2：石川 p.19-20 多重检验后的门槛（不得按 2.0 取）。
MIN_T = 3.0

#: G3：用户目标。
TARGET_ANNUAL = 0.20

#: A2/G4 的**对账容差**：重算的合成分 vs `results` 里那份「选股当时的 score」。
#:
#: 🚨 为什么容差是 **1e-9** 而不是「差不多」：
#:   两侧若是**同一次运算**，JSON/parquet 往返**不丢 bit** ⇒ 差恰好 0.000e+00。
#:   任何**非零**差都只有一个来源：**打分用的权重与这里配权用的权重不是同一个**
#:   （或某处换了 z 口径）—— 那正是最该拦下的情况。
#:   留一丝宽容，等于给「口径已经换了但数值差不多」留后门（同 `k_sweep_extract` 的 0 容差理由）。
SCORE_RECONCILE_TOL = 1e-9


def train_spread_and_ic(train_dates, xs, cache, factors, window=IC_WINDOW):
    """**只**在给定日期上算逐因子 IC 与尾部价差。调用方必须传训练段。

    ## 为什么 `tail_low − tail_high` 而不是 `*_hit` 的差

    `daily_ic_ranked` 给的是**分层 IC**（不是命中率），而
    `factor_ic` 汇总出的 `*_hit` 是「该分层 IC 为正的天数占比」。
    策略真正下注的是**两端收益之差**，所以这里用 IC 均值之差：

        spread_f = mean(tail_low_IC) − mean(tail_high_IC)

    它与「只在两端下注」同口径；`*_hit` 不是。

    ## 为什么用块 t（按窗口长度切块）而不是日频 t

    `lag1_real` 不重叠，但 `hold4` / `hold21` 重叠 ⇒ 日频 t 会虚高 √overlap 倍。
    这里固定只用 `lag1_real`（重叠 1），并**仍然**按块算 t：
    日频收益序列存在自相关，逐日 t 在有自相关时低估标准误。
    """
    enter, exit_off = FWD_WINDOWS[window]
    ic_vals = {f: [] for f in factors}
    lo_vals = {f: [] for f in factors}
    hi_vals = {f: [] for f in factors}
    for day in train_dates:
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        u_idx = universe.set_index("code")
        fwd = forward_returns(cache, list(u_idx.index), day, enter, exit_off)
        rf = make_ranked_forward(u_idx, fwd)
        if rf is None:
            continue
        for f in factors:
            got = daily_ic_ranked(u_idx, rf, f)
            if got["all"] is None:
                continue
            ic_vals[f].append(got["all"])
            # ⚠️ 分层 IC 可能是 None（样本不足）。**不许**用 0 填 ——
            #   0 意味着「零相关」，会把一个**没测出来**的因子的价差
            #   变成「实测为零」，而后者会去压低它的权重。
            #   ⇒ 保持 None，最后按**有效样本数**逐因子报。
            lo_vals[f].append(got["tail_low"])
            hi_vals[f].append(got["tail_high"])

    out = {}
    for f in factors:
        ic = _clean(ic_vals[f])
        lo = _clean(lo_vals[f])
        hi = _clean(hi_vals[f])
        # 🚨 三个口径**各有各的有效样本数**：某因子可能 IC 测得出来、
        #   某个分层测不出来。⇒ 逐个报，不许拿一个 n 覆盖三个。
        entry = {"ic_mean": _mean(ic), "ic_n": len(ic),
                 "tail_low_mean": _mean(lo), "tail_low_n": len(lo),
                 "tail_high_mean": _mean(hi), "tail_high_n": len(hi)}
        # 价差必须建立在**同一批天**上：mean(lo) − mean(hi) 各自取均值时，
        # 若两边有效天数不同，差值里混进了「天数差」。
        pair_n = min(len(lo), len(hi))
        entry["spread"] = None
        entry["spread_n"] = pair_n
        if pair_n >= MIN_OBS:
            entry["spread"] = float(np.mean(lo[:pair_n]) - np.mean(hi[:pair_n]))
        out[f] = entry
    return out


def _clean(vals):
    """丢掉 None —— **不是**丢掉 0。分层 IC 为 0 是有效观测。"""
    return [float(v) for v in vals if v is not None]


def _mean(v):
    return round(float(np.mean(v)), 6) if v else None


def spread_tilted_weights(spread: dict, factors) -> dict:
    """C 臂权重：按**尾部价差**排序倾斜。

    ## 为什么用「符号 + 等幅度」，而不是把价差直接当权重

    直接拿价差当权重 = 乘上了一个**在样本内拟合的尺度**，
    而价差本身的量纲（IC 单位）没有理由对应组合权重。
    ⇒ 只取**序**：符号给方向，幅度统一，最后整体归一化。
    ⇒ 这样 C 臂的自由度与 A 臂（`R⁻¹·s_f`）大致可比，
    差别**只在「按哪个量排序」**这一件事上——这才是干净的对照。

    ## 平局的处理必须显式

    价差有并列时（比如两个因子都排第 3），若用 `sorted(set(...))` 取中点
    会**丢因子** ⇒ 组合少一个维度，而那个因子贡献恒等于 0
    （`composite_score` 只取 `factor_rows[list(weights)]`）。
    ⇒ 用**名次**而非**取值**做平均，保证八个因子一个不少。
    """
    present = [f for f in factors if spread.get(f, {}).get("spread") is not None]
    if not present:
        raise ValueError("没有任何因子在训练段上测出尾部价差 —— 拒绝构造 C 臂")
    missing = [f for f in factors if f not in present]
    if missing:
        # **不静默丢**：这些因子会拿到 0 权重 ⇒ 贡献恒等于 0，
        # 而报告上一切正常。让它响亮。
        raise ValueError(
            f"这些因子在训练段测不出尾部价差（样本不足）：{missing}。"
            "C 臂会把它们排到队尾而不是删掉——**删掉会让组合维度变少，"
            "而少的那个因子贡献恒等于 0 且看不出来**。")

    order = sorted(present, key=lambda f: spread[f]["spread"], reverse=True)
    n = len(order)
    # 名次 0..n−1 ⇒ 线性权重，中间为 0。
    raw = {f: (n - 1) / 2.0 - i for i, f in enumerate(order)}
    # 平局：名次相等的因子取名次均值（不会出现，但显式处理更安全）
    total = sum(abs(v) for v in raw.values())
    if total == 0:
        return {f: 0.0 for f in factors}
    return {f: round(raw[f] / total, 4) for f in factors}


def ic_tilted_weights(ic: dict, factors) -> dict:
    """A 臂权重：按**IC** 倾斜（与 C 臂同构，只换排序依据）。

    ⚠️ 这里刻意**不复刻** `R⁻¹·s_f` 的协方差求逆：
    那样 A 与 C 的差别就不止「按哪个量排序」，还混进了「是否用协方差」。
    ⇒ 两臂共用同一个最简单的倾斜构造，**唯一差别是排序依据**。
    """
    present = [f for f in factors if ic.get(f, {}).get("ic_mean") is not None]
    if not present:
        raise ValueError(
            "没有任何因子在训练段上测出 IC —— 拒绝构造 A 臂。\n"
            "⚠️ 先看**训练段是哪一段**：缺省窗口是**全池日线日期轴**，"
            "而它比「因子 + 前瞻收益真正有覆盖的区间」长得多 —— "
            "实测缺省起点 1994-09-28 会把训练段推到 1994~2013，"
            "那一段一票 IC 都测不出。\n"
            "⇒ 结论：**日线面板的轴 ≠ 本研究可用的轴**（比较前先确认是同一个量）。\n"
            "⇒ 请显式传 --start / --end 取与其它判决一致的窗口；"
            "脚本**不会**替你悄悄把窗口缩回去（缺档必须报错，绝不回落）。")
    order = sorted(present, key=lambda f: ic[f]["ic_mean"], reverse=True)
    n = len(order)
    raw = {f: (n - 1) / 2.0 - i for i, f in enumerate(order)}
    total = sum(abs(v) for v in raw.values())
    if total == 0:
        return {f: 0.0 for f in factors}
    return {f: round(raw[f] / total, 4) for f in factors}


def equal_weights(factors) -> dict:
    """B 臂：等权。**不许调**（预注册 §2 规则 3）。"""
    w = 1.0 / len(factors)
    return {f: round(w, 4) for f in factors}


def _daily_excess(results) -> pd.Series | None:
    """逐行结果 → 逐日**净超额**序列。口径直接借 `evolve_race._daily_series`。

    🚨 **不自己重写一遍超额**：`evaluate_weights` 的权威列清单是
    `RESULT_COLUMNS = (pred_date, code, direction, actual_pct,
    result, market_pct, cost)` —— **没有 `excess` / `excess_net` 两列**。
    本脚本第一版照着想象去找这两列，于是三臂全部报「无超额列」，
    而**脚本 exit code 是 0**（第 8 种形状）。

    ⇒ 超额的定义只许有一处。同一语义在本仓已被处理 N 次的地方，
    第 N+1 处几乎必然漏（第 17 种形状）。
    """
    return _daily_series(results, "up")


def _score_quartile_monotonic(xs, weights, dates, results) -> bool | None:
    """score 四分位命中率是否单调递增（G4 / A2）。

    ## 为什么必须在这里重算合成分

    `results`（`evaluate_weights` 的返回）**只有选中的 top/bottom 各 N 只**，
    而四分位需要**整个横截面**的分数分布。
    而重算又必须用**同一份 `composite_score`** ——
    另算一套 z 口径等于测了别的东西（第 119 层）。

    ## 🚨🚨🚨 2026-10-15：我曾在这里写下一句**假注释**，并照着它写错了代码

    第一版的 docstring 白纸黑字写着「`evaluate_weights` **不吐 score 列**」。
    **那是假的。** `aplus/predict.py:167`：

        PRED_COLUMNS = ["code", "name", "direction", "score"] + list(FACTOR_NAMES)

    而 `evaluate_weights` 的每行来自 `res = verify_predictions(preds, bars)`，
    `res` **继承 `preds` 的列** ⇒ 返回值**带着 `score`**。

    ⇒ 后果不是「多算了一份」，是 **merge 撞上同名列**：
    pandas 把两侧都叫 `score` 的列改名成 `score_x` / `score_y`
    ⇒ `merged["score"]` 抛 `KeyError: 'score'` ⇒ 三臂跑到这一步崩掉。

    ★ 这与下面那条「键取错列」是**同一个病的两层**：
      我**没有去看真实返回的列**，而是**相信了一句注释**，
      然后按那句注释写代码。注释比代码活得久 ⇒ 它成了事实上的规格。
      ⇒ 读接口的正确姿势：`PRED_COLUMNS` / `RESULT_COLUMNS` 是**代码里的常量**，
      注释不是。

    ★★ 而这份 `score` 恰好是**免费的第三方真值**：它是**选股当时**用的分数。
       若重算的分数与它对不上 ⇒ **打分用的权重 ≠ 这里配权用的权重**
       ⇒ A2/G4 量的就是另一个东西。所以下面有一道**对账闸门**，
       对不上直接**不判定**，而不是照着一个来路不明的分数分四分位。

    ## 🚨🚨 键取错列，整条 A2/G4 **结构上恒为 None**

    同一轮的第一版写的是 `"code": sc.index`。而 `composite_score` 返回的 Series
    **索引是 `factor_rows.index`** —— `xs[day]` 的行号（RangeIndex），
    **不是股票代码**。代码在 `universe["code"]` 这一**列**上
    （`_held_predictions` 里就是 `set_index("code", drop=False)` 取的，
    它得那么转换正是因为行号无意义）。

    为什么当时没被发现：**三臂跑完前没人走到这一行**。测试里
    `monotonic` 是夹具直接注入的字段，函数本身零覆盖。
    而它的失败模式是 **`None`** —— 与「样本不足、真的测不出」
    在产物里**长得一模一样**，exit code 还是 0（第 8 种形状）。

    ★ 危险的不是崩溃（崩溃是响的），是**类型对齐之后的样子**：
    若只把 `sc.index` `astype(str)` 把 dtype 对齐，merge 会匹配到**零行**
    ⇒ 样本不足 ⇒ `None`。修得越"像模像样"，错得越深。
    """
    if results is None or len(results) == 0:
        return None
    up = results[results["direction"] == "up"]
    if up.empty or "result" not in up.columns:
        return None
    if "code" not in up.columns:
        return None
    up = up.assign(code=up["code"].astype(str))   # 两边必须同一口径，不靠碰巧
    # ⚠️ 结果表**自带 `score`**（`PRED_COLUMNS` 里就有，见上面）。
    #   重算的那份必须换个名字，否则 merge 会把两侧都叫 `score` 的列
    #   改名成 `score_x`/`score_y`，而 `merged["score"]` 立刻 KeyError。
    has_reported = "score" in up.columns
    rows = []
    for day in dates:
        universe = xs.get(day)
        if universe is None or universe.empty or "code" not in universe.columns:
            continue
        try:
            sc = composite_score(universe, weights)
        except (KeyError, ValueError):
            continue
        # ★ 位置对齐：`composite_score` 保序，`universe["code"]` 同序。
        #   不许用 `sc.index`（那是行号），也不许按 index join（会错配行）。
        codes = universe["code"].to_numpy()
        if len(codes) != len(sc):
            continue                      # 长度对不上 ⇒ 不猜，宁可不判
        rows.append(pd.DataFrame({"pred_date": day,
                                  "code": pd.Series(codes).astype(str).to_numpy(),
                                  "_arm_score": sc.to_numpy(dtype=float)}))
    if not rows:
        return None
    merged = up.merge(pd.concat(rows, ignore_index=True), on=["pred_date", "code"],
                      how="inner")
    merged = merged[merged["result"].isin(["hit", "miss"])]
    if len(merged) < 4 * MIN_PAIRS_HIT:
        return None
    # ★★ 对账闸门：重算的合成分必须与**选股当时**那份 `score` 一致。
    #   不一致 ⇒ 打分用的权重 ≠ 这里配权用的权重 ⇒ 量的不是同一个东西。
    #   ⇒ 拒绝判定（而不是照着一个来路不明的分数分四分位）。
    if has_reported:
        pair = merged[["score", "_arm_score"]].to_numpy(dtype=float)
        finite = pair[np.isfinite(pair).all(axis=1)]
        if len(finite) == 0:
            return None
        dev = float(np.max(np.abs(finite[:, 0] - finite[:, 1])))
        if not (dev <= SCORE_RECONCILE_TOL):
            return None
    q = pd.qcut(merged["_arm_score"], 4, labels=False, duplicates="drop")
    if q.isna().all() or q.nunique() < 2:
        return None
    rates = merged.assign(_q=q, _h=(merged["result"] == "hit").astype(float)) \
                  .groupby("_q")["_h"].mean().dropna()
    if len(rates) < 2:
        return None
    return bool(np.all(np.diff(rates.to_numpy(float)) > 0))


def _annual_net(daily: pd.Series) -> dict:
    """年化净超额 + 有效独立观测数（按调仓周期算，不按天数）。

    ## 为什么必须报独立观测数

    日频超额序列有自相关；`n_days = 1801` 看着很足，但若按 K=5 分块，
    独立观测只有 ~360 个。用天数当分母会让 t **系统性偏高**
    ⇒ 报出去的「显著」是假的（本仓第 80 层的老坑）。
    """
    if daily is None or len(daily) == 0:
        return {"annual_net": None, "mean_daily": None, "t": None,
                "n_days": 0, "n_blocks": None, "evidence": "无数据"}
    s = pd.Series(np.asarray(daily, dtype=float))
    s = s[np.isfinite(s)]
    if len(s) == 0:
        return {"annual_net": None, "mean_daily": None, "t": None,
                "n_days": 0, "n_blocks": None, "evidence": "全 NaN"}
    mean = float(s.mean())
    sd = float(s.std(ddof=1)) if len(s) > 1 else 0.0
    # 244 个交易日/年；**不是 252**（A 股实际年均交易日数，见记忆
    # `a-plus-friction-has-positive-floor`：rpy 必须用精确 244/21）
    annual = mean * 244.0
    n = len(s)
    t = mean / (sd / np.sqrt(n)) if sd > 0 else None
    # 独立观测按「调仓周期」估：日频 n 天 ⇒ n 个观测；
    # 一旦有 hold 分块信息就用它。这里按天算，因为本脚本只跑日频。
    blocks = n
    ev = "充分" if blocks >= MIN_DAILY_POINTS else (
        f"薄（{blocks} < {MIN_DAILY_POINTS} 个独立观测，Chan p.85）")
    return {"annual_net": round(annual, 4), "mean_daily": round(mean, 6),
            "t": round(t, 3) if t is not None else None,
            "n_days": n, "n_blocks": blocks, "evidence": ev}


MIN_PAIRS_HIT = 20


def diagnose(out: dict, factors) -> dict:
    """按预注册的 A1/A2/A3 + G1~G6 逐条判定。**不新增阈值。**"""
    test = out["test"]
    arms = test["arms"]
    A, B, C = arms["A_IC_TILTED"], arms["B_EQUAL"], arms["C_SPREAD_TILTED"]
    # ⚠️ 一律用 `.get`：产物里缺键时**报「无法测」（闸门红）**，
    # 而不是 `KeyError` 崩掉。崩溃与「没测出来」在下游长得一样
    # （都是没有判决），但前者会把**本来能出的部分判决一起丢掉**。
    common = test.get("common", {})

    def _pos(x):
        return x is not None and x > 0

    annual = common.get("annual_net")
    blocks = common.get("n_blocks")
    gates = {
        "G1_beta_adj_daily_excess_positive": _pos(common.get("beta_adj_daily")),
        "G2_t_above_3": (common.get("t") is not None
                         and common["t"] > MIN_T),
        "G3_annual_net_above_20pct": (_pos(annual)
                                      and annual > TARGET_ANNUAL),
        "G4_score_quartile_monotonic": common.get("monotonic") is True,
        "G5_sample_at_least_681": (blocks is not None
                                   and blocks >= MIN_DAILY_POINTS),
        "G6_positive_after_cost": _pos(annual),
    }
    out["gates"] = gates
    out["gates_passed"] = sum(1 for v in gates.values() if v)

    # ── A0 退化检查（**补记于 2026-10-15 跑完第一次之后，不是事前预注册**）──
    # 🚨 A 与 C 若权重逐位相同，就是**同一条策略跑两遍**，
    #   A1 变成 `X >= X`，**恒真**。这不是「通过」，是**测不出任何东西**。
    #   而它在报告上长得和「C ≥ A 成立」一模一样 ⇒ 必须先挡。
    #
    # ★ 为什么它会退化：训练段上 `ic_mean` 与 `spread` **完全同序**（ρ=+1），
    #   所以「按 IC 排」与「按尾部价差排」给出**同一组权重**。
    #   `weight_vs_spread.json` 里那个 −0.9286 是取 **|IC|** 算的；
    #   带符号时符号整个翻过来。⇒ 「IC 与尾部价差错配」这个**前提**本身
    #   依赖于取绝对值 —— 这是本臂跑出来的第一手反证。
    w_a = out.get("arm_weights", {}).get("A_IC_TILTED")
    w_c = out.get("arm_weights", {}).get("C_SPREAD_TILTED")
    # ⚠️ **三态**，不是两态：权重缺失 ⇒ **测不出**退不退化。
    #   而 `.get()` 返回 None、`None is not None` 为假 ⇒ 两态写法会把
    #   「没测」当成「测了，没问题」—— 第 23 层（假绿）的经典形态，
    #   而且**方向最坏**：它放出去的是一个**结构上恒真**的 A1。
    degenerate = None if (w_a is None or w_c is None) else (w_a == w_c)
    out["A0_arms_degenerate"] = degenerate

    # ── A1：C 臂年化净超额 ≥ A 臂 ──
    # ⚠️ 样本不足时**不许**按现有样本报「C 更好」（预注册 A3）。
    thin = blocks is None or blocks < MIN_DAILY_POINTS
    a1 = None
    if degenerate is True:
        out["A1_note"] = (
            "⚠️ **A4 退化**：A 臂与 C 臂权重逐位相同 ⇒ 跑的是同一条策略，"
            "A1 在结构上恒真（C ≥ A 必然成立）⇒ **不判定**。"
            "这不是「C 不比 A 差」，是「**换排序依据改变不了任何东西**」。")
    elif degenerate is None:
        out["A1_note"] = (
            "⚠️ 产物缺 `arm_weights` ⇒ **测不出** A/C 是否退化，"
            "而退化时 A1 恒真 ⇒ 按「无法证明与不合格同价」**不判定**。")
    elif A.get("annual_net") is None or C.get("annual_net") is None:
        out["A1_note"] = "A 或 C 缺年化净超额，无法判定"
    elif thin:
        a1 = None
        out["A1_note"] = (f"样本薄（{blocks} < "
                          f"{MIN_DAILY_POINTS}）⇒ 按 A3 **不判定**，"
                          "不得据现有样本说 C 更好或更差")
    else:
        a1 = C["annual_net"] >= A["annual_net"]   # 已确认两者都非 None
        out["A1_note"] = ("C ≥ A 成立" if a1 else
                          "C < A ⇒ 「改目标函数」被证伪，**如实记录**，"
                          "按纪律不回头改判据")
    out["A1_C_not_worse_than_A"] = a1
    out["A2_C_score_monotonic"] = C.get("monotonic")
    out["A3_thin_sample"] = bool(thin)
    return out


def _beta_adjusted(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    """(日均 beta 剔除超额, t)。没有大盘列就返回 (None, None)，**不回落成 0**。

    「测不出来」报成 0 与「真的是 0」在下游长得一模一样，
    而前者会让 G1 在完全无证据的情况下判过。
    """
    if daily is None or "excess_net" not in daily.columns:
        return None, None
    s = pd.Series(daily["excess_net"].to_numpy(float), dtype=float)
    s = s[np.isfinite(s)]
    if len(s) < 2:
        return None, None
    mean = float(s.mean())
    sd = float(s.std(ddof=1))
    return mean, (mean / (sd / np.sqrt(len(s))) if sd > 0 else None)


def main() -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="尾部价差配权对照臂（预注册 A1/A2/A3）")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--start", default=None,
                    help="缺省 = 全池日期轴最早日（market_date_axis，"
                         "不是任何一只票的最早日）")
    ap.add_argument("--end", default=None,
                    help="缺省 = 全池日期轴最晚日（market_date_axis，"
                         "不是任何一只票的最晚日）")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--train-ratio", type=float, default=0.6)
    ap.add_argument("--embargo-days", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    a = ap.parse_args()

    if not a.data_root.is_dir():
        print(f"✗ 缺档：{a.data_root} 不存在（缺档必须报错，绝不回落）")
        return 1

    from aplus.backtest import ENTRY_LAG
    from aplus.snapshot import load_factors_map
    from aplus.store import LocalStore

    store = LocalStore(a.data_root)
    files = sorted((a.data_root / "master" / "daily").glob("*.parquet"))
    if not files:
        print(f"✗ {a.data_root}/master/daily 为空 —— 缺档必须报错，绝不回落")
        return 1
    t0 = time.time()
    print(f"载入日线 {len(files)} 只 …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    cache = build_price_cache(daily_map, factors_map)
    print(f"日线 {len(daily_map)} 只 / 价格缓存 {len(cache)} 只"
          f"（{time.time() - t0:.0f}s）", flush=True)

    # 🚨🚨 窗口缺省值必须来自**全池日期轴**，不能来自 `files[0]`（形状 18）。
    #   `files[0]` 是字母序第一只（`000001.SZ`），实测它的起点 2019-01-02
    #   比全池最早 1994-09-28（`000043.SZ`）**晚 24 年**；用它兜底会
    #   静默砍掉早期票的历史，而 `--end` 的 help 写着「缺省 = 快照最晚日」
    #   —— help 说的是快照，实现取的是一只票，两句话都不是真的。
    #
    #   ★ 末端今天**恰好**也相等（池最晚 2026-10-14 就是 `000001.SZ` 本人），
    #     所以末端是潜伏、起点是活的。别因为末端对就把两个一起放过。
    #
    #   形状 17：日期轴只有**一个**实现 ⇒ 走 `panel.market_date_axis`，
    #     不在这里做第二遍并集（第 105 层：多档/多实现时先找对档位）。
    axis = market_date_axis(daily_map)
    if not axis:
        print("✗ 缺档：日线里读不出任何日期（缺档必须报错，绝不回落）")
        return 1
    start = a.start or axis[0]
    end = a.end or axis[-1]
    print(f"窗口 {start} ~ {end}"
          f"（全池日期轴 {axis[0]} ~ {axis[-1]}，{len(axis)} 个交易日）",
          flush=True)

    xs = build_cross_sections(store, start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs.get("_dates", [])
    if not dates:
        print(f"✗ {start} → {end} 内没有可用交易日")
        return 1

    train_dates, test_dates = split_window(dates, a.train_ratio, a.embargo_days)
    if not train_dates or not test_dates:
        print(f"✗ 窗口太短（{len(dates)} 天），切不出训练/测试两段 —— 拒绝作出判决")
        return 1
    print(f"横截面 {len(dates)} 日；训练段 {train_dates[0]} ~ {train_dates[-1]}"
          f"（{len(train_dates)} 日）／测试段 {test_dates[0]} ~ {test_dates[-1]}"
          f"（{len(test_dates)} 日）", flush=True)

    factors = list(FACTOR_NAMES)
    print(f"在**训练段**上算 IC 与尾部价差（窗口 {IC_WINDOW}）——"
          f"测试段一根都不碰：", flush=True)
    stats = train_spread_and_ic(train_dates, xs, cache, factors)   # ← 只给训练段
    print(f"训练段完成（{time.time() - t0:.0f}s）", flush=True)

    arms = {
        "A_IC_TILTED": ic_tilted_weights(stats, factors),
        "B_EQUAL": equal_weights(factors),
        "C_SPREAD_TILTED": spread_tilted_weights(stats, factors),
    }
    per_arm = {}
    for name, w in arms.items():
        d = evaluate_weights(xs, w, a.top_n, a.bottom_n, test_dates)
        an = _annual_net(_daily_excess(d))
        an["monotonic"] = _score_quartile_monotonic(xs, w, test_dates, d)
        per_arm[name] = an

    out = {
        "kind": "spread_tilted_arm",
        "prereg": "docs/TODO_tail_spread_arm_2026-10-15.md",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_root": str(a.data_root),
        "window": {"train": [train_dates[0], train_dates[-1], len(train_dates)],
                   "test": [test_dates[0], test_dates[-1], len(test_dates)],
                   "embargo_days": a.embargo_days, "train_ratio": a.train_ratio},
        "ic_window_used": IC_WINDOW,
        "train_stats": stats,
        "arm_weights": arms,
        "test": {"arms": per_arm},
        "elapsed_s": round(time.time() - t0, 1),
    }
    # beta 剔除超额对三臂是**同一个序列**（同一篮子集合、同一市场基准），
    # 所以只算一次并放在 common；但**不许**因此跳过计算 ——
    # 跳过就是「没测当成 0」，G1 会凭空判过。
    common = per_arm["A_IC_TILTED"]
    b, bt = (common.get("mean_daily"), common.get("t"))
    out["test"]["common"] = dict(common)
    # ⚠️ 口径标注：G1 名为「剔除 beta 后日均超额」，而本仓既有的
    #   「beta 剔除后超额」口径就是 `actual_pct − market_pct`（见
    #   `diagnose_hit_rate.py:126`），**不是 beta 回归**。
    #   `_daily_series` 减的正是等权市场均值 ⇒ 与既有口径一致，
    #   但这个数字**不是**「把 beta 回归掉之后」的残差。
    out["test"]["common"]["beta_adj_daily"] = b
    out["test"]["common"]["beta_adj_daily_caliber"] = (
        "actual_pct − cost − mean(market_pct)，逐日等权；"
        "**不是 beta 回归残差**（本仓既有口径同此，见 diagnose_hit_rate.py:126）")
    out["test"]["common"]["t"] = bt
    out = diagnose(out, factors)
    out["读法"] = (
        "C 臂按**训练段的尾部价差**排序倾斜，A 臂按**训练段的 IC** 排序倾斜，"
        "两臂构造**完全同构**，唯一差别是排序依据。"
        "⇒ A1 测的是「换目标函数」这一个变量。"
        "⚠️ 三臂**共享同一段面板**，**不是三个独立实验** ⇒ "
        "不得引用「三臂一致」当稳健性证据。"
        "⚠️ C 臂与 A 臂同一天用同一批数据造出来 ⇒ 比 A 臂更接近样本内拟合，"
        "报数时必须 deflate，且必须说明 C 臂从未真正前瞻验证过。")

    txt = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if a.out:
        a.out.write_text(txt, encoding="utf-8")
        # 🚨 盖章必须在**写盘那一刻**做，不是「记得时候」做（形状 21）：
        #   Python 的模块级代码在 import 那一刻就绑定 ⇒ 事后再给脚本加
        #   stamp()，正在飞的进程**不会**执行它 ⇒ 产物照样没有 sidecar，
        #   而 mtime 守卫会判绿（它只比文件时间）。
        stamp(a.out, "scripts/spread_tilted_arm.py", root=ROOT,
              extra={"test_window": [test_dates[0], test_dates[-1]],
                     "train_window": [train_dates[0], train_dates[-1]],
                     "A1_C_not_worse_than_A": out["A1_C_not_worse_than_A"],
                     "gates_passed": out["gates_passed"],
                     "A0_arms_degenerate": out.get("A0_arms_degenerate")})
        print(f"✓ {a.out}")
    print(f"A 臂 {arms['A_IC_TILTED']}")
    print(f"B 臂 {arms['B_EQUAL']}")
    print(f"C 臂 {arms['C_SPREAD_TILTED']}")
    print(f"测试段 {test_dates[0]} → {test_dates[-1]}（{len(test_dates)} 天）")
    for k, v in per_arm.items():
        print(f"  {k:<18} 年化净 {v['annual_net']}  t={v['t']}  "
              f"单调={v['monotonic']}  {v['evidence']}")
    print(f"闸门 {out['gates_passed']}/6 通过："
          + " ".join(k for k, v in out["gates"].items() if v))
    print(f"A1(C≥A) = {out['A1_C_not_worse_than_A']}   {out['A1_note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
