"""**真回测**：`R⁻¹Σ_f` 逐年权重，跑一遍真实的钱能赚多少。

## 为什么这个脚本存在

判决侧（`verdict_composite.py`）已经过了：组合 IC 0.0709，**0/6 不及格**。
它打印的「年毛超额」走的是 `ic_to_return.py` 那座**桥**，
而那座桥的常数错了 3 倍、形状也不成立（见
`docs/TODO_measure_sigma_r.md` 的撤回段）。

于是摆着两个互相矛盾、都为真的结论：

| 来源 | 说的是 |
|---|---|
| 信号侧判决 | 排序**强到够线** |
| `ic_guided_race_7y_corrected.json` | 所有 8 因子臂的**日净超额全为负** |

> **信号侧判决永远只能回答「排序有没有用」，不能回答「能不能赚钱」。**
> 后者只有真回测能答。而 `R⁻¹Σ_f` 权重**从未进过 `evaluate_weights`** ——
> 这个脚本补上那一格。

## 它**不是**又一座桥

刻意保留的四件真回测要素，一件都不省：

1. **可成交性闸门**（`execution_gate=True`，默认）—— 按 `panel.can_buy/can_sell`
   判成交，被拦下的**按原因计数**（`attrs["unfillable_execution*"]`）。
   计数缺失 = 这些观测**从统计里消失且不留痕迹**。
2. **成本** —— 按**换手**算，且**只在调仓日**收一次（非调仓日不换手 → 0）。
3. **Top-N 选股** —— 真买 10 只，不是买一整个分位。
4. **逐日聚合 → 分块 t** —— 观测单位是「一次调仓决策」，不是「一天」。

## 两档成本都要报（这是本脚本最容易被跳过的一步）

`evaluate_weights` 默认用 `STOCK_COST_DEFAULT = 0.003`，
而它**只是四项成本里的价差那项**（0.0015×2），佣金、印花税、过户费全缺席。
真实现况（5 万本金 / 单笔 5000）是 **0.0055**，**低估 1.84 倍**。

只报 0.003 那档，等于让读者以为「净值已经扣干净了」——
而门槛 `hurdle_rate` 用的恰恰是 0.0055。**两档混用 = 口径不一致**
（静默失效形状 ②）。

因为 `cost_drag = turnover × cost` 对 cost **线性**，
把输出里 `cost` 列按 `0.0055/0.003` 缩放**精确**等价于换成本率重跑，
不必（也不该）改引擎。`tests/test_backtest_walk_forward_weights.py`
用线性性做了变异验证。

用法：
    .venv/bin/python scripts/backtest_walk_forward_weights.py \
        --matrices data/reports/ic_matrices.parquet \
        --out data/reports/wf_weights_backtest.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import (ENTRY_LAG, STOCK_COST_DEFAULT, build_cross_sections,
                            cost_drag, evaluate_weights)
from aplus.composite import unpack, walk_forward
from aplus.factor import FACTOR_NAMES, robust_z
from aplus.hurdle import COST_REAL_50K, DAYS_PER_YEAR, hurdle_rate, rounds_per_year
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus import provenance

ROOT = Path(__file__).resolve().parents[1]

#: 与判决的 K=21 同一口径：月频调仓。
K = 21

_VERDICT = None


def _verdict():
    """延迟载入 —— 模块级常量必须在函数定义**之前**算出来，反之不行。"""
    global _VERDICT
    if _VERDICT is None:
        path = Path(__file__).resolve().parent / "verdict_composite.py"
        spec = importlib.util.spec_from_file_location("_verdict_composite", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VERDICT = mod
    return _VERDICT


def weights_by_year(df: pd.DataFrame, factors: list[str]) -> dict[int, dict]:
    """**判决用的那一份逐年权重** —— 单一真相源就在这里。

    刻意调用 `walk_forward` 而不是自己重算。自己重算等于
    「在测量台旁边另造一台测量仪」，两台之间的差永远查不出来。
    """
    rows = walk_forward(df, factors,
                        need_ic=_verdict().monthly_ic_threshold()[1],
                        judged_from=2021, equal_weight_years=(2019, 2020))
    return {r["year"]: r for r in rows if r.get("weights") is not None}


COST_FLAT_CALIBRATED_N = 10
"""`COST_REAL_50K` 的标定名数 —— **不是**「一个成本参数」，是「10 个仓位的成本」。

见 `real_cost_for`。
"""

#: 本项目的产品本金（用户口径）。成本是**固定额 + 费率**的混合体，
#: 所以成本必然依赖本金 —— 把这两个数字写在这里是为了让「换本金」这件事
#: 必须**改两处**，而不是悄悄只改一处。
COST_REAL_CAPITAL = 50_000.0
COST_REAL_EQUITY_WEIGHT = 1.0   # 本策略满仓权益，无杠杆、无现金缓冲


def real_cost_for(top_n: int, k: int | None = None) -> float:
    """返回 `top_n` 个持仓下**每轮双边**的真实现况成本（收益率比例）。

    ★ 第 17 种形状（2026-10-15）：`COST_REAL_50K = 0.0055` 是一个**标定值**，
      它的标定配置是 **N=10**（本金 ¥50,000 ⇒ 每只 ¥5,000）。
      A 股单边最低佣金 **¥5/笔是每笔固定额**（`costs.COMMISSION_MIN`），
      名数一变，每笔变小，最低佣金占的比例就变，而且**非线性**
      （`COMMISSION_MIN_BINDS_BELOW = 5/万三 = ¥16,667`）。

      实测低估倍数（本金 5 万、K=21）：N=21 **1.4×**、N=100 **4.3×**、
      N=200 **7.9×**、N=546 **20.5×**。

    ⇒ **本函数是 `run_year` 取实况成本的唯一入口。**
      标定名数上**逐位**返回旧常数（所以 K 扫描五档的结论一个数都不用改）；
      别的名数上**按名数逐笔算**（`costs.annual_friction` / 调仓次数）。
      ⇒ 结构上**不存在第二条能把那个常数拿到别的名数上的代码路径**。

    ⚠️ 上一版这里是 `raise`（拒绝在标定名数之外量）——
      **过度设计，当场被榜宽实验打脸**：它也调 `run_year(xs, …, n, n, k)`，
      于是一个**正当**的宽榜取证被自己的守卫挡住。
      教训：**「让错误表达不出来」的正解是换成对的尺子，不是禁止测量。**
      只有当「正确的尺子」根本不存在时，拒绝才是对的修法。
    """
    from aplus.costs import annual_friction
    from aplus.hurdle import rounds_per_year

    if top_n == COST_FLAT_CALIBRATED_N:
        return COST_REAL_50K
    kk = k if k is not None else 21
    rpy = rounds_per_year(kk)
    return annual_friction(COST_REAL_CAPITAL, COST_REAL_EQUITY_WEIGHT,
                           top_n, rpy) / rpy


def at_cost(results: pd.DataFrame, cost: float) -> pd.DataFrame:
    """把输出换算到 `cost` 口径。

    `evaluate_weights` 已经把 `cost_drag` 落在 `cost` 列上了，
    而 `cost_drag = turnover × cost` 对 cost **线性**，
    所以按 `cost / STOCK_COST_DEFAULT` 缩放**精确**等价于换成本率重跑。

    > 线性性是这里唯一的假设，`tests/` 里对它做了变异验证：
    > 若哪天 `cost_drag` 里出现了 `min(1, …)` 之类的非线性项，
    > 这条缩放就不再精确，而**它不会报错**——只会安静地算错。
    """
    out = results.copy()
    if cost != STOCK_COST_DEFAULT and "cost" in out.columns:
        out["cost"] = out["cost"] * (cost / STOCK_COST_DEFAULT)
    return out


def _daily(results: pd.DataFrame, direction: str = "up",
           weights_col: str | None = None):
    """逐日单腿净超额序列 —— **复用 `evolve_race` 的那一份**，不自己写。

    它已经处理了三件容易错的事：逐日先聚合再检验、
    成分不足 `MIN_BASKET` 的日子丢掉、只有实际建仓的涨腿付成本。
    自己重写一遍就是第 22 层静默失效。

    `weights_col` 是市值加权实验（预注册 `TODO_cap_weighted_benchmark`）的入口 ——
    基准相减就发生在 `_daily_series` 里，所以端点只能在那里加。
    """
    from aplus.evolve_race import _daily_series
    return _daily_series(results, direction, weights_col=weights_col)


def _t(daily, block: int):
    """分块 t —— 观测单位是**一次调仓决策**，不是一天。

    K 日调仓时，一天里「选了谁」在 K 天内不变。
    把 K×n 个交易日当成 K×n 个独立观测，等于把同一个决策数了 K 遍。
    """
    from aplus.evolve_race import _tstat
    return _tstat(daily, block=block)


def _rescale_check() -> dict:
    """实测一遍成本线性性 —— 让这个假设在**报告里**有数，不只在注释里。"""
    prev, cur = ["a", "b", "c"], ["b", "c", "d"]
    a = cost_drag(prev, cur, 3, cost=STOCK_COST_DEFAULT)
    b = cost_drag(prev, cur, 3, cost=COST_REAL_50K)
    ratio = b / a if a else float("nan")
    return {"cost_drag_at_default": a, "cost_drag_at_real": b, "ratio": ratio,
            "expected_ratio": COST_REAL_50K / STOCK_COST_DEFAULT,
            "linear": bool(abs(ratio - COST_REAL_50K / STOCK_COST_DEFAULT) < 1e-12)}


def _up_exposure(res, xs, days, factors) -> dict:
    """涨腿逐日对每个因子的暴露 = **持仓票**的稳健 z 在横截面上的均值。

    ## 三个必须说清的选择

    **1. 用 `robust_z` 而不是 `rank_zscore`。**
    策略排序走的是 `composite_score` → `robust_z`（见 `aplus/factor.py`）。
    暴露若换成 `rank_zscore`，就变成「用另一把尺子量这把尺子量出来的东西」——
    数字照样能算，但和合成权重不在同一个空间里，读数无从对照。

    **2. 排名在**全横截面**上做，均值只取持仓票。**
    先在全体上定 z，再对持仓取均值 ⇒ 这个值是「组合相对全市场的倾斜」。
    顺序反过来（先取持仓再排名）不会让读数「偏大」，而是让它**恒等于 0** ——
    `robust_z` 是仿射变换，**永远以自身均值为中心**，在 10 只票内部排名，
    那 10 只的 z 均值按构造就是 0。
    实测（200 只正态、取 mom20 最强 10 只、30 个种子）：
    全横截面排名 **1.83 ~ 2.28**，持仓内排名 **−0.00 ~ 0.02**。
    ⇒ 这个 bug 会让**全部 8 个 β 塌成 0，把所有超额都归给 α**，
    得出「这信号几乎全是 α」的相反结论。**它不会让任何断言变红**，
    所以必须有反向测试盯着。

    **3. 只算涨腿。**
    账户真实建仓的是涨腿；空头腿在 A 股融不到券，不存在。
    分解的因变量本身就是涨腿超额，解释变量必须是同一组持仓。

    `days` 传的是**已经进过统计的天**（`gross.index`），
    不是全部调仓日 —— 成分不足 `MIN_BASKET` 的日子已被上游丢掉，
    暴露若覆盖更宽的天数，回归的 X 和 y 就不再是同一批观测。
    """
    if res is None or res.empty or not len(days):
        return {f: [float("nan")] * len(days) for f in factors}
    up = res[res["direction"] == "up"]
    held = {d: set(g["code"]) for d, g in up.groupby("pred_date")}
    out = {f: [] for f in factors}
    for day in days:
        uni = xs.get(day)
        codes = held.get(day)
        if uni is None or uni.empty or not codes or "code" not in uni.columns:
            for f in factors:
                out[f].append(float("nan"))
            continue
        # 只对**因子列**做，和 `composite_score` 的写法逐字一致
        # （`factor_rows[list(weights)].apply(robust_z, axis=0)`）。
        # 对整张表 apply 也能跑通——字符串列会被 `robust_z` 变成全 NaN——
        # 但那是**靠它的宽容度兜着**，哪天它改成对字符串抛错就整条线断。
        cols = [f for f in factors if f in uni.columns]
        if not cols:
            for f in factors:
                out[f].append(float("nan"))
            continue
        # 逐列显式构造，而不是 `uni[cols].apply(robust_z, axis=0)`：
        # **单列时 `apply(axis=0)` 会退化成 Series**（不是 DataFrame），
        # 于是后面的 `z[mask]` 与 `.columns` 全部失效 —— 8 因子时正常、
        # 只留 1 个因子就崩，而「只留 1 个因子」正是最常做的诊断跑法。
        z = pd.DataFrame({f: robust_z(uni[f]) for f in cols})
        sel = z[uni["code"].isin(codes)]
        for f in factors:
            out[f].append(float(sel[f].mean())
                          if f in sel.columns and sel[f].notna().any()
                          else float("nan"))
    return out


def realized_cost_and_net(gross_daily, daily_with_cost):
    """★ 从「同一天的毛/净两条序列」反推**实际**成本，并给出净额。

    ## 为什么必须由两条序列相减，而不是套名义公式

    原实现是 `net = annual_gross_pct − rounds_per_year(k) × c`。
    两处错叠在一起：

    ① `annual_gross_pct` 来自 `_daily(at_cost(res, c))`，
       而 `_daily_series` 里写着 `net = net - leg["cost"]`
       （`aplus/evolve_race.py:249-250`）—— **成本已经扣过一次了**。
    ② 减掉的是 `rounds_per_year(k) × c` 这个**名义**公式，
       不是 `cost` 列里实测出来的实际换手成本。

    结果：`毛超额 − 净 ≈ 2 × 名义成本`，且**五档一致**（实测 1.90–1.98×）。
    而 `hit_threshold` 用的是同一个错的 `annual_net_pct` ——
    判决与被判决的对象用的是同一个错误数，**自洽，于是全绿**（第 5 层同族）。

    ## 改成由数据反推

    两条序列走的是**同一段代码、同一个 MIN_BASKET 过滤、同一批日子**，
    所以 `gross_daily − daily_with_cost` 精确等于逐日实际成本，
    不依赖任何关于换手的假设。

    返回 `(实际年成本, 净额, 自查漂移)`。
    漂移 = `含成本年化 + 实际年成本 − 无成本年化`，正确实现恒为 0。
    """
    import numpy as _np

    gross_annual = float(_np.mean(gross_daily)) * DAYS_PER_YEAR
    with_cost_annual = float(_np.mean(daily_with_cost)) * DAYS_PER_YEAR
    realized = gross_annual - with_cost_annual
    return realized, with_cost_annual, gross_annual - with_cost_annual - realized


def _daily_frame(chunks: list, factors) -> "pd.DataFrame":
    """把逐年落盘的 chunk 拼成一张表：日期 + 毛超额 + 逐日因子暴露。

    ## 为什么抽成独立函数（第 66 层失效的直接修法）

    这段逻辑第一次写出来时是内联在 `main()` 里的，
    `pd.concat([...Series...])` 返回 **Series** 而不是 DataFrame，
    `to_parquet` 当场崩 —— 而**在它被真实执行之前，一次都没发现**，
    因为 K=5/10/42 跑的是更早的版本。

    抽出来之后它能被单元测试直接跑：
    「返回类型必须是 DataFrame」这条断言，
    由一个几毫秒的测试守住，而不是由下一个 sweep 的 3.5 分钟守住。

    ## 三个必须写死的细节

    - **日期先转字符串再建表。** 晚一步就是拿 Timestamp 索引去
      reindex 字符串索引，`reindex` 全变 NaN —— 不报错，
      而回归会安静地把 NaN 行丢掉，于是分解「看起来正常」。
    - **先按列聚合，再一次性建表。** 绝不能
      `pd.concat([每段一根Series...], axis=1)` ——
      **`axis=1` 是按索引对齐的 join，不是堆叠**：
      6 个年份就是 6 根同名的 `expo_mom20`，每根只在自己那一段有值，
      其余全 NaN；而 `df["expo_mom20"]` 返回的是 **DataFrame**，
      下游 `frame[["expo_mom20"]]` 照样不报错，只是拿到二维数组。
      **单段 chunk 的测试永远看不见这个 bug。**
    - **后写覆盖先写**（= `keep="last"`）。同一天跨年边界可能出现两次。
    """
    def _label(d):
        """`Timestamp` → `2023-01-03`（**不带时分秒**），其余走 `str`。

        `str(Timestamp)` 会给出 `2023-01-03 00:00:00`，
        落盘之后再读回来按日期 join 就对不上 —— 而 join 对不上
        表现为一整列 NaN，没有任何异常。
        """
        return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

    # `{列名: {日期: 值}}`。dict 保留插入顺序 ⇒ 列序 = 写入序。
    # ⚠️ 列名**必须**显式给：`pd.Series(...)` 不给名字时列名就是 `0`，
    # 而下游 `decompose_alpha_beta.py` 读的正是 `gross_excess` 那一列。
    cols: dict = {}
    for c in chunks:
        labels = [_label(d) for d in c["dates"]]
        wanted = [("gross_excess", c["excess"])]
        wanted += [(f"expo_{f}", c.get("exposure", {}).get(f)) for f in factors]
        for name, vals in wanted:
            if vals is None:
                continue          # 该年缺这个因子 → 该段日期留 NaN
            bucket = cols.setdefault(name, {})
            for lab, v in zip(labels, vals):
                bucket[lab] = v   # 后一次覆盖前一次

    labels = sorted({lab for bucket in cols.values() for lab in bucket})
    df = pd.DataFrame(
        {name: [bucket.get(lab, float("nan")) for lab in labels]
         for name, bucket in cols.items()},
        index=pd.Index(labels, name="date"))
    # 某因子在**所有**年份里都没测 ⇒ 补一整列 NaN。
    # 填 0 不行：那会把「没这个因子」读成「完全不暴露」。
    for f in factors:
        if f"expo_{f}" not in df.columns:
            df[f"expo_{f}"] = float("nan")
    return df


def run_year(xs, weights, dates, top_n: int, bottom_n: int,
             rebalance_every: int, daily_out: list | None = None,
             score_transform=None,
             benchmark_cap: bool = False, portfolio_cap: bool = False,
             cap_ctx=None) -> dict:
    """跑一个年份，返回三个口径的逐年数字。

    `daily_out` 非空时，把**毛超额逐日序列 + 逐日因子暴露**追加进去。
    α/β 分解需要这两样，而它们只在这里产生 ——
    让它们落到 `daily_out`，是为了**同一台测量仪出两次数**。
    另写一个脚本重算逐日超额 = 造第二台测量仪，两台之间的差永远查不出来。

    ⚠️ 暴露**必须在 `xs` 还在手上时算完**。落盘之后要重算就得
    再加载一次 GB 级面板，而那正是本项目 OOM 的唯一来源
    （实测并发面板作业 RSS 2.9 GB）。

    ## `score_transform`（安慰剂钩子，默认 None）

    三个安慰剂臂**必须**从这里走，与 P0 完全同一条路径
    （预注册 `docs/TODO_placebo_control_2026-10-15.md` §4）。
    它只往 `evaluate_weights` 透传，**不在这里加任何分支** ——
    这一层一旦长出自己的逻辑，安慰剂与 P0 的差就分不清是信号还是这段代码。

    ## `benchmark_cap` / `portfolio_cap`（市值加权，默认全关）

    预注册 `docs/TODO_cap_weighted_benchmark_2026-10-15.md`：口径两端都要换。
    本函数是三处实验（P0 / 安慰剂 / 榜宽）与市值加权实验的**同一个**入口 ——
    三臂只许在这里分叉，分叉点之后不许有第二段逻辑。
    全关时逐位等于 2026-10-15 之前的行为（守卫：
    `test_market_by_day_with_cap_table_off_is_bitwise_unchanged` 等）。
    """
    res = evaluate_weights(xs, weights=weights, top_n=top_n, bottom_n=bottom_n,
                           dates=dates, rebalance_every=rebalance_every,
                           execution_gate=True, score_transform=score_transform,
                           portfolio_cap=portfolio_cap, cap_ctx=cap_ctx,
                           benchmark_cap=benchmark_cap)
    if res is None or res.empty:
        return {"n_rows": 0, "n_days": 0}

    wcol = "cap" if portfolio_cap else None
    gross = _daily(res.assign(cost=0.0), weights_col=wcol)
    out = {"n_rows": int(len(res)), "n_days": int(len(dates))}
    for tag, c in (("bt003", STOCK_COST_DEFAULT),
                   ("real", real_cost_for(top_n, rebalance_every))):
        daily = _daily(at_cost(res, c), weights_col=wcol)
        t, n_obs = _t(daily, block=rebalance_every)
        mean = float(daily.mean()) if daily is not None and len(daily) else float("nan")
        sd = float(daily.std(ddof=1)) if daily is not None and len(daily) > 1 else float("nan")
        # ★ 成本只扣一次，且由数据反推（2026-10-15 P0 修复）
        realized, net_annual, drift = realized_cost_and_net(gross, daily)
        out[tag] = {
            "cost_per_round": c,
            "daily_excess": mean, "daily_sd": sd,
            # ⚠️ 名字是历史包袱：`annual_gross_pct` 实为**已扣成本**的超额年化。
            # 改名会波及所有下游消费者（旧报告的字段名必须能读），
            # 所以保留字段名，在这里把口径钉死。
            "annual_gross_pct": mean * DAYS_PER_YEAR,
            "t_blocked": t, "n_independent_obs": n_obs,
            # 名义值保留作对照（= 换手理想为 1 时的成本），判决一律用实测值
            "annual_cost_nominal_pct": (rounds_per_year(rebalance_every) * c
                                        if "cost" in res.columns else 0.0),
            "annual_cost_pct": realized,
            "_selfcheck_cost_single_deduction": drift,
        }
        out[tag]["annual_net_pct"] = net_annual
        out[tag]["hit_threshold"] = bool(out[tag]["annual_net_pct"] >= hurdle_rate(
            rounds_per_year=rounds_per_year(rebalance_every)))

    gm = float(gross.mean()) if gross is not None and len(gross) else float("nan")
    out["gross"] = {"daily_excess": gm, "annual_pct": gm * DAYS_PER_YEAR}

    if daily_out is not None and gross is not None and len(gross):
        days = list(gross.index)
        daily_out.append({
            "dates": days,
            "excess": [float(v) for v in np.asarray(gross, float)],
            "exposure": _up_exposure(res, xs, days, list(FACTOR_NAMES)),
        })
    out["unfillable_execution"] = {
        k[len("unfillable_execution"):]: v
        for k, v in getattr(res, "attrs", {}).items()
        if k.startswith("unfillable_execution")}
    out["unadjusted_touch"] = dict(getattr(res, "attrs", {}).get("unadjusted", {}) or {})
    out["cap_fallback_days"] = int(getattr(res, "attrs", {}).get("cap_fallback_days", 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--factors", default="")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/wf_weights_backtest.json"))
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    ap.add_argument("--rebalance-every", type=int, default=K)
    args = ap.parse_args()

    if not args.matrices.is_file():
        raise SystemExit(f"抽取表不存在：{args.matrices}；先跑 scripts/extract_ic_matrices.py")

    lin = _rescale_check()
    if not lin["linear"]:
        print(f"🚨 成本非线性，`at_cost` 的缩放不再精确：{lin}", file=sys.stderr)
        return 2
    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)
    unpack(df, factors)
    wmap = weights_by_year(df, factors)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"\n权重来自判决同一份抽取表，判决年 "
          f"{sorted(y for y in wmap if y >= 2021)}，{len(factors)} 个因子")
    print(f"成本口径：回测默认 {STOCK_COST_DEFAULT:.2%} / "
          f"真实现况 {COST_REAL_50K:.2%}（缩放比 {lin['ratio']:.4f}，已验证线性）")

    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"\n日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）\n", flush=True)

    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        raise SystemExit("没有任何判决年份")

    rounds = rounds_per_year(args.rebalance_every)
    need = hurdle_rate(rounds_per_year=rounds)
    print(f"{'年':<6}{'日数':>6}{'组合IC':>10}{'毛超额/年':>12}"
          f"{'净@0.3%/年':>13}{'净@0.55%/年':>13}{'t(分块)':>9}{'独立观测':>9}{'够线':>7}")
    print("─" * 95)

    rows = []
    daily_chunks: list = []
    for y in judged:
        r = run_year(xs, wmap[y]["weights"], by_year[y],
                     args.top_n, args.bottom_n, args.rebalance_every,
                     daily_out=daily_chunks)
        if r.get("n_rows", 0) == 0:
            print(f"{y:<6}空")
            continue
        ic = wmap[y].get("ic_mean")
        t_real = r["real"]["t_blocked"]
        n_obs = r["real"]["n_independent_obs"]
        print(f"{y:<6}{r['n_days']:>6}{(ic if ic is not None else float('nan')):>10.5f}"
              f"{r['gross']['annual_pct']:>12.2%}"
              f"{r['bt003']['annual_net_pct']:>13.2%}"
              f"{r['real']['annual_net_pct']:>13.2%}"
              f"{(t_real if t_real is not None else float('nan')):>9.2f}"
              f"{n_obs:>9}"
              f"{'✓' if r['real']['hit_threshold'] else '✗':>7}")
        r["year"] = y
        r["composite_ic"] = ic
        r["weights"] = wmap[y]["weights"]
        r["weights_source"] = wmap[y].get("weights_source")
        rows.append(r)
        print(f"      不可成交计数：{r['unfillable_execution'] or '（无）'}"
              f"   未复权触碰：{r['unadjusted_touch'] or '（无）'}", flush=True)

    if daily_chunks:
        # 逐日毛超额 + 逐日因子暴露落盘。
        #
        # **毛超额**而不是净值 —— α/β 分解问的是「排序的信息量」，
        # 成本与它无关，把成本混进来会让分解同时回答两个问题。
        #
        # 暴露与超额**同一张表**：分开放两个文件就得靠日期对齐拼回来，
        # 而「对齐错了」肉眼完全看不出来。
        dg = _daily_frame(daily_chunks, FACTOR_NAMES)
        if dg.empty:
            raise SystemExit("逐日产物为空 —— 后续任何引用都不可信")
        dg.to_parquet(args.out.with_suffix(".daily.parquet"))
        # ⚠️ 这里的行数**不等于**上面那句「横截面 N 个交易日」——
        #   `_daily_series` 会丢掉成分不足 `MIN_BASKET` 的日子。
        #   两个数并排打印却不写差在哪，读起来就像「少了一天」。
        # ⚠️ 2026-10-15：这一行原来写的是 `for r in results`，
        # 而 `results` 在 `main()` 里**从来不存在**（逐年结果是 `rows`）。
        # 它是 2026-10-14 加「逐日落盘」时埋的，**崩在 JSON 写盘之前** ——
        # 于是 parquet 落了、主报告没落，而 `parquet 的 mtime 更新`
        # 读起来就像「这轮跑完了」（第 8 条：完成与否由进程状态判断）。
        # 修法不是加 `if`，是让**写盘顺序**保证主报告先于副产物可被误读：
        # 现在崩点与写盘点都受同一条路径保护（见 `bad_drift` 拒绝落盘）。
        dropped = sum(int(r["n_days"]) for r in rows) - len(dg)
        print(f"→ {args.out.with_suffix('.daily.parquet')}（{len(dg)} 行逐日序列 × "
              f"1 毛超额 + {len(FACTOR_NAMES)} 因子暴露；"
              f"比横截面少 {dropped} 行，差额 = `_daily_series` 丢掉的日子）")

    if rows:
        print("─" * 95)
        for key, label in (("gross", "毛超额"), ("bt003", "净 @0.003"),
                           ("real", "净 @0.0055")):
            vals = [r[key]["annual_pct" if key == "gross" else "annual_net_pct"] for r in rows]
            ts = [r[key]["t_blocked"] for r in rows if r[key].get("t_blocked") is not None]
            hit = sum(1 for r in rows if key != "gross" and r[key]["hit_threshold"])
            tail = f"   t 均 {np.mean(ts):.2f}" if ts else ""
            print(f"{label:<14} 年均 {np.mean(vals):>8.2%}  最低 {np.min(vals):>8.2%}"
                  f"  最高 {np.max(vals):>8.2%}  够线 {hit}/{len(rows)}{tail}")
        print(f"\n门槛：年毛超额 {need:.2%}（= 目标 20% + 成本 {COST_REAL_50K:.2%}"
              f" × {rounds:.1f} 次/年）")

    # ★ 成本只扣一次的会计自查（2026-10-15 P0）。
    # 恒等式「含成本年化 + 实测年成本 = 无成本年化」若不成立，
    # 说明成本被扣了两次或用了名义公式 —— 那正是刚修掉的那个 bug。
    # 在**写盘之前**拦，因为它一旦落盘，下游 11 个模块都会读到它。
    bad_drift = [(r["year"], k, r[k].get("_selfcheck_cost_single_deduction"))
                 for r in rows for k in ("bt003", "real")
                 if r.get(k) and abs(r[k].get("_selfcheck_cost_single_deduction") or 0) > 1e-12]
    if bad_drift:
        print(f"🚨 成本被重复扣减：{bad_drift[:4]} —— 拒绝落盘。",
              file=sys.stderr)
        return 2

    res = {
        "kind": "wf_weights_backtest", "top_n": args.top_n,
        "bottom_n": args.bottom_n, "rebalance_every": args.rebalance_every,
        "rounds_per_year": rounds, "hurdle_gross": need,
        "cost_bt_default": STOCK_COST_DEFAULT, "cost_real": COST_REAL_50K,
        "cost_rescale_linear_check": lin,
        "factors": factors,
        "note": ("净超额 = 涨篮子 − 市场，日频序列按调仓周期分块后求 t；"
                 "观测单位是「一次调仓决策」。可成交性闸门已开。"
                 "两个成本口径都报：回测默认低估真实现况 1.84 倍。"),
        "years": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    # ★ 溯源章。逐档文件名走 `PATTERN_GENERATORS`（不是逐条登记），
    #   而**登记 ≠ 盖章** —— 两者之间没有自动通路（第 6 种形状）。
    provenance.stamp(args.out, "scripts/backtest_walk_forward_weights.py", root=ROOT,
                     data_root=args.data_root)
    print(f"\n→ {args.out}\n总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
