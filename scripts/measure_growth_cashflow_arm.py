#!/usr/bin/env python3
"""成长族 + 现金流族的长腿臂（**臂 R 同款仪器**）。

预注册：`docs/PREREG_GROWTH_CASHFLOW_ARM_2026-10-21.md`（**看任何数之前写死**）。

## 这个脚本回答什么

在**臂 R 同一把尺子**上（500 只缓存池 · 2019-01-02→2023-12-31 · 榜宽 10 ·
K ∈ {21,63} · 多头口径 · **毛**超额 · 未扣费）：

| 因子 | 定义（源列） | 方向 |
|---|---|---|
| `g_profit` | `PARENTNETPROFITTZ` 归母净利同比增长率 | 越大越好 |
| `g_rev` | `TOTALOPERATEREVETZ` 营业总收入同比增长率 | 越大越好 |
| `cf_accrual` | (归母净利 − 经营现金流净额) / 营业总收入 | **越小越好** |
| `cf_growth` | `KCFJCXSYJLRTZ` 经营现金流净额同比增长率 | 越大越好 |

⇒ 报**长腿毛超额/年** + MDE + t + 配对日数（独立观测数），并给四态判决。

## 四条纪律（违反即作废）

1. **不改 `aplus/` 任何模块的行为**（只读复用）；真要加因子就**新建模块**。
2. **判据/门槛/候选数不许改**（预注册 §6：m = 8 = 4 因子 × 2 K 档）。
3. **「测不到」不许写成「无效 / 没有超额」**（措辞守卫）。
4. **生成器字节在跑的过程中不许改**（第 125 层：`expect_bytes` 抓在 `main()`
   第一条可执行语句，盖章时比对 ⇒ 不一致就**拒绝盖章**）。

## 口径对齐（**复用 round B 的实现，不重写**）

* 面板缓存：`/private/tmp/aplus_fund_armR500.pkl`（sha256 记进产物）。
* 财务侧骨架：`scripts/fundamental_family_factors.py::annual_series_from_cache`
  （同一套闸 / 同一个去重键 / 同一个排序键）⇒ **额外财务列按 `(code, report_date)`
  join 进去，行集合不变**（预注册 §3.2.2）。
* 逐日 asof：`scripts/fundamental_family_factors.py::active_annual_by_day`
  （`searchsorted` 向后）⇒ 本脚本**只加列不改行**。
* 腿表 / 前瞻收益 / 年化 / 配对 t：`scripts/measure_fundamental_mechanism_armR.py`
  的同名实现（**逐字复用**）⇒ 「同一把尺子」由**逐位对拍**证明，不是嘴上说。

## 🚨 与预注册的两处**已披露**偏离（不是隐瞒，是写进产物的）

1. **预注册 §5 C1 第二句**「负控毛超额不得高于原臂」**没写显著性门槛**
   ⇒ 本脚本**只在负控显著时**才让它作废（否则纯噪声会有一半概率作废）
   ⇒ **按字面读法该作废的格子清单**逐格写进产物
   `verdict.primary.clause_b_literal_reading`（**不藏**）。
2. **预注册 §7 的表格**把「11.2813 ÷ 本轮 K=63 读数」叫「同口径倍数」，
   而**同一节的正文**又写明那是**跨口径**的、只能作形状陈述
   ⇒ 本脚本**按正文执行**：倍数只引用交付档的**同口径**那一列
   （1.7897×），臂 R 的读数与基线的比值**只作形状陈述**。

另有一处**读数口径**的说明（不是偏离，是必须说清）：`n_pairs` 是
**配对日天数 = 调仓次数 = 名义块数**；仓内台账记着「块数 ≠ 有效独立观测」
（`N_eff = n / τ`）⇒ 本轮**没有**测这一段的 τ ⇒ `n_pairs` 是信息量的**上界**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.evolve_race import bonferroni_t  # noqa: E402
from scripts.fundamental_family_factors import (  # noqa: E402
    LONG_SIGN as ARMR_LONG_SIGN,
    active_annual_by_day,
    annual_series_from_cache,
    price_and_shares_on,
)
from scripts.measure_fundamental_mechanism_armR import (  # noqa: E402
    KS,
    LADDER_N,
    WINDOW,
    _annualized,
    _factor_values_on,
    _forward_map,
    _grid_days,
    _leg_table,
    _load_panel,
    _long_leg,
    _paired_t,
    _pool_mean,
    _threshold_sets,
)

GENERATOR = "scripts/measure_growth_cashflow_arm.py"
PREREG = "docs/PREREG_GROWTH_CASHFLOW_ARM_2026-10-21.md"

#: 预注册 §2 的四个因子：**方向写死**（+1 = 越大越好 ⇒ 取大的一端）。
LONG_SIGN = {"g_profit": +1, "g_rev": +1, "cf_accrual": -1, "cf_growth": +1}

#: 预注册 §6.2 的候选数 m = 8 = 4 因子 × 2 个 K 档（**对照臂不计入**）。
M_CANDIDATES = 8

#: 预注册 §6.4 的族名 → 四态前缀。
FAMILY_OF = {"g_profit": "GROWTH", "g_rev": "GROWTH",
              "cf_accrual": "CASHFLOW", "cf_growth": "CASHFLOW"}
FAMILIES = ("GROWTH", "CASHFLOW")

#: 「测不到」的措辞（预注册 §6.4：**不许**写「无效/没有/已证伪」）。
NOT_MEASURED_WORDING = "这只等于『测不到』，不等于『没有超额』。"

#: 预注册 §5 C2 的复现目标（round B 已发表的 bm 长腿毛超额，**逐位**）。
BM_REPRO_TARGET = {
    "bm@K21": -0.07589353990333512,
    "bm@K63": -0.09984606655373453,
}
BM_REPRO_TOL = 1e-12


# ── 财务侧：骨架逐字复用 round B，额外列只加不改 ──────────────────

#: 预注册 §2 的四个因子的**源列**（厂商已在财务表里算好的量）。
EXTRA_SOURCE_COLUMNS = {
    "g_profit": ("PARENTNETPROFITTZ",),
    "g_rev": ("TOTALOPERATEREVETZ",),
    "cf_accrual": ("PARENTNETPROFIT", "KCFJCXSYJLR", "TOTALOPERATEREVE"),
    "cf_growth": ("KCFJCXSYJLRTZ",),
}


def extra_columns_frame(cache: Path) -> pd.DataFrame:
    """额外财务列 ⇒ `(code, report_date)` 唯一（**不加闸、不重写去重**）。

    🚨 键的唯一性是**数据事实**，不是假设：实测本缓存
    `(SECURITY_CODE, REPORT_DATE)` 无重复 ⇒ 每一行报表期只对应一行源数据。
    下面的 `annual_series_extended` 会**再验一次**（`validate="one_to_one"`），
    数据一变就报错，绝不静默取错行。
    """
    raw = pd.read_parquet(cache)
    cols = sorted({c for v in EXTRA_SOURCE_COLUMNS.values() for c in v})
    missing = [c for c in cols if c not in raw.columns]
    if missing:
        raise ValueError(
            f"财务缓存缺列 {missing} ⇒ 四个因子的定义无法成立，"
            f"**拒绝**用别的列凑（预注册 §2 的因子定义不许事后改）")
    out = pd.DataFrame({
        "code": raw["SECURITY_CODE"].astype(str).str.zfill(6),
        "report_date": pd.to_datetime(raw["REPORT_DATE"], errors="coerce"),
    })
    for c in cols:
        out[c] = pd.to_numeric(raw[c], errors="coerce")
    return out


def annual_series_extended(cache: Path) -> pd.DataFrame:
    """臂 R 的年报骨架 **+ 额外财务列**（行集合不变，预注册 §3.2.2）。

    * 骨架：`annual_series_from_cache`（**逐字复用** ⇒ 同一套闸/去重/排序）。
    * 加列：按 `(code, report_date)` **一对一** join ⇒ 行数不变。
    """
    spine = annual_series_from_cache(cache)
    extra = extra_columns_frame(cache)
    merged = spine.merge(extra, on=["code", "report_date"], how="left",
                         validate="one_to_one")
    if len(merged) != len(spine):
        raise AssertionError(
            f"加列后行数变了：{len(spine)} → {len(merged)} ⇒ "
            f"「只加列不改行」被破坏")
    if merged["code"].nunique() != spine["code"].nunique():
        raise AssertionError("加列后股票数变了 ⇒ 骨架被破坏")
    return merged


def active_with_extra(annual: pd.DataFrame, days) -> pd.DataFrame:
    """逐日 asof（**复用** `active_annual_by_day`）**+ 额外列**。

    🚨 `active_annual_by_day` 只投影固定列 ⇒ 额外列会被丢掉。
    ⇒ 做法是：**先**用它选出「该日可见的那一份报告」，
    **再**按 `(code, report_date)` 把额外列**只加不改**地接回去。
    """
    sel = active_annual_by_day(annual, days)
    extra_cols = sorted({c for v in EXTRA_SOURCE_COLUMNS.values() for c in v})
    extra = annual[["code", "report_date"] + extra_cols].drop_duplicates(
        subset=["code", "report_date"], keep="last")
    out = sel.merge(extra, on=["code", "report_date"], how="left",
                    validate="many_to_one")
    if len(out) != len(sel):
        raise AssertionError("接回额外列后行数变了 ⇒ asof 选择被破坏")
    return out


# ── 因子值 ────────────────────────────────────────────────────────

def factor_values_gc(m: pd.DataFrame) -> pd.DataFrame:
    """在臂 R 的 `factor_values` 合并帧上算四个因子（**同一覆盖池**）。

    🚨 覆盖池与 bm **逐字同构**：`factor_values` 用的是
    `active_annual_by_day` ∩ `px`（inner join on (day, bare)）⇒
    本轮四个因子的覆盖池 = bm 的覆盖池（bm 自己再因 bps<=0 少一点）。
    ⇒ 市场腿因而与 round B **同一把尺子**。

    口径细节（预注册 §2）：
    * `cf_accrual` 的分母 `TOTALOPERATEREVE` **≤ 0 ⇒ 该 (日,票) 缺失**
      （不裁剪、不加 epsilon、不取绝对值）；
    * 其余三个因子是**厂商已算好的增长率**，其分母在缓存里不可见
      ⇒ 只做「非空」检查（这一点在产物的 `caliber` 块里写明）。
    """
    out = m.copy()
    num = out["PARENTNETPROFIT"] - out["KCFJCXSYJLR"]
    den = out["TOTALOPERATEREVE"]
    out["g_profit"] = out["PARENTNETPROFITTZ"]
    out["g_rev"] = out["TOTALOPERATEREVETZ"]
    out["cf_accrual"] = (num / den.where(den > 0))
    out["cf_growth"] = out["KCFJCXSYJLRTZ"]
    return out


def _attach_to_panel(xs: dict, m: pd.DataFrame, days, cols) -> dict:
    """把因子列挂进面板横截面（**与 `attach_factors` 逐字同构**）。

    `attach_factors` 的最后一段是 `uni.join(add[cols], on="code")`
    （`add` 已 `set_index("code")`）⇒ 本函数照抄这一段，
    保证**面板侧的行集合与顺序完全不变**（只多几列）。
    """
    for day in days:
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        sub = m.loc[m["day"] == day]
        if sub.empty:
            continue
        add = sub.set_index("code")
        keep = [c for c in cols if c in add.columns]
        if not keep:
            continue
        uni = uni.drop(columns=[c for c in keep if c in uni.columns])
        xs[day] = uni.join(add[keep], on="code")
    return xs


# ── 腿表：与 arm R **逐字同构**（只多一个「方向」参数）─────────────

def leg_table_signed(xs: dict, days: list, factor: str, k: int,
                     fwd_by_day: dict, sign: int, ns=LADDER_N,
                     n_a: int = M_CANDIDATES) -> dict:
    """等权 top-N 阶梯（**arm R `_leg_table` 的镜像** + 显式方向）。

    🚨 为什么是镜像而不是复用：arm R 的 `_leg_table` 从 `LONG_SIGN[factor]`
    取方向，而 `LONG_SIGN` 里**没有**本轮四个因子 ⇒ 只能带方向重写一遍。
    ⇒ 代价是「两份副本」的风险 ⇒ **对拍在下面 `bm` 对照臂上逐位做**：
    同一个 bm、同一个面板、同一批前瞻收益 ⇒ 本函数与 arm R 的输出必须
    **逐位相等**（差 0.0），否则整轮读数不可信（**当场抛错**，不静默继续）。
    """
    per_n = {}
    for n in ns:
        diffs, longs, pools = {}, {}, {}
        for day in _grid_days(days, k):
            vals = _factor_values_on(xs, day, factor)
            fwd = fwd_by_day.get(day) or {}
            lg, _codes = _long_leg(vals, fwd, sign, n)
            pm, _pc = _pool_mean(vals, fwd)
            if lg is None or pm is None:
                continue
            longs[day] = lg
            pools[day] = pm
            diffs[day] = lg - pm
        s = pd.Series(diffs, dtype=float)
        if len(s) < 3:
            per_n[str(n)] = {
                "n": int(len(s)), "mean_per_k": None,
                "gross_annual_pct": None, "t": None,
                "note": ("该档每日可用样本 < N（池子上限不足）"
                         "或配对日 < 3 ⇒ **不报数、不回落成 0**"),
            }
            continue
        sd = float(s.std(ddof=1))
        crit = float(bonferroni_t(n_a, df=max(len(s) - 1, 2)))
        mde_per_k = float(crit * sd / np.sqrt(len(s)))
        per_n[str(n)] = {
            "n": int(len(s)),
            "mean_per_k": float(s.mean()),
            "gross_annual_pct": _annualized(float(s.mean()), k),
            "sd_per_k": sd,
            "t": _paired_t(s, pd.Series(0.0, index=s.index)),
            "t_crit": crit,
            "mde_per_k": mde_per_k,
            "mde_annual_pct": float(mde_per_k * 244.0 / k),
            "mde_formula": ("MDE_ANN = crit × sd_per_K / √n_pairs × (244/K)"
                            "（预注册 §4 A3；crit 与判决同一个）"),
            "long_leg_annual_pct": _annualized(float(pd.Series(longs).mean()), k),
            "market_leg_annual_pct": _annualized(float(pd.Series(pools).mean()), k),
        }
    return per_n


# ── 仪器等价性对拍（两份副本之间必须有守卫，第 137 层）──────────

#: 逐位对拍的字段（**全部是数**；`mde_formula` 是散文，不比）
_EQUIV_FIELDS = ("n", "mean_per_k", "gross_annual_pct", "sd_per_k",
                 "t", "t_crit", "mde_per_k", "mde_annual_pct",
                 "long_leg_annual_pct", "market_leg_annual_pct")


def _same_number(a, b) -> bool:
    """逐位相等（`None` 与 `None` 相等；`NaN` 与 `NaN` 相等）。"""
    if a is None or b is None:
        return a is None and b is None
    fa, fb = float(a), float(b)
    if math.isnan(fa) and math.isnan(fb):
        return True
    return fa == fb


def instrument_equivalence_check(xs, days, fwd_by_k, n_a: int) -> dict:
    """**同一把尺子**的逐位对拍：我的 `leg_table_signed` vs arm R 的 `_leg_table`。

    🚨🚨 为什么必须做：arm R 的 `_leg_table` 从 `LONG_SIGN[factor]` 取方向，
    而 `LONG_SIGN` 里**没有**本轮四个因子 ⇒ 只能带方向重写一遍
    ⇒ **两份副本** ⇒ 第 137 层（两份副本之间没有守卫 ⇒ 改一处就静默漏一处）。
    ⇒ 对拍用 **bm**：同一份面板、同一批前瞻收益、同一份因子列
    ⇒ 两个函数的输出必须**逐位相等**（差 0.0）。

    ⚠️ `n_a` 两边**传同一个** ⇒ `t_crit` / `mde_*` 也逐位可比。
    本轮判决用 m=8（预注册 §6.2），对拍用 arm R 自己的 n_A ——
    **对拍只验「仪器」，不验「判据」** ⇒ 两件事不许混。
    """
    rows = []
    n_compared = 0
    all_equal = True
    for k in KS:
        mine = leg_table_signed(xs, days, "bm", k, fwd_by_k[k],
                                ARMR_LONG_SIGN["bm"], ns=LADDER_N, n_a=n_a)
        ref = _leg_table(xs, days, "bm", k, fwd_by_k[k], ns=LADDER_N, n_a=n_a)
        diffs = []
        for n in sorted(set(mine) | set(ref), key=lambda s: int(s)):
            a = mine.get(n) or {}
            b = ref.get(n) or {}
            for fld in _EQUIV_FIELDS:
                av, bv = a.get(fld), b.get(fld)
                n_compared += 1
                if not _same_number(av, bv):
                    diffs.append({"n": int(n), "field": fld,
                                  "mine": av, "ref": bv})
        rows.append({"k": k, "n_ladder_levels": len(mine),
                     "n_diffs": len(diffs), "diffs": diffs[:20],
                     "bitwise_equal": not diffs})
        all_equal = all_equal and not diffs
    return {
        "what": ("**同一把尺子**的逐位对拍：本轮的 `leg_table_signed` "
                 "vs 臂 R 的 `_leg_table`，**同一个 bm / 同一份面板 / "
                 "同一批前瞻收益** ⇒ 输出必须**逐位相等**"),
        "why": ("臂 R 的 `_leg_table` 从 `LONG_SIGN[factor]` 取方向，"
                "而 `LONG_SIGN` 里**没有**本轮四个因子 ⇒ 只能带方向重写"
                "⇒ **两份副本** ⇒ 第 137 层：两份副本之间没有守卫"),
        "factor": "bm", "n_a": n_a,
        "fields_compared": list(_EQUIV_FIELDS),
        "n_values_compared": n_compared,
        "rows": rows,
        "bitwise_equal": all_equal,
        "note": ("`mde_formula` 是**散文**（引的是本轮预注册的节号）⇒ 不比；"
                 "**公式本身相同**（`crit × sd/√n × 244/K`）⇒ 由数值逐位相等证明"),
    }


# ── PIT 断言（预注册 §3.2.4）──────────────────────────────────────

def pit_violations(active: pd.DataFrame) -> dict:
    """**独立第三路**的 PIT 断言：每个 (日,票) 用的报告必须 `NOTICE_DATE ≤ 当日`。

    ⇒ 违规数必须 = 0；并报出 `NOTICE_DATE` 与当日的**间隔分布**。
    """
    if active.empty:
        return {"violations": 0, "n_rows": 0, "lag_days": None}
    notice = pd.to_datetime(active["notice_date"])
    day = pd.to_datetime(active["day"])
    lag = (day - notice).dt.days
    bad = active.loc[lag < 0]
    return {
        "violations": int(len(bad)),
        "n_rows": int(len(active)),
        "lag_days": {
            "min": int(lag.min()), "p50": float(lag.median()),
            "p90": float(lag.quantile(0.9)), "max": int(lag.max()),
            "mean": float(lag.mean()),
        },
        "violation_examples": [
            {"code": str(r["code"]), "day": str(r["day"]),
             "notice_date": str(r["notice_date"]),
             "report_date": str(r["report_date"])}
            for _, r in bad.head(10).iterrows()
        ],
    }


def pit_cross_check_aplus(cache: Path, spine: pd.DataFrame,
                           dates: list) -> dict:
    """与 `aplus.factor_fundamental.annual_from_frame` 对拍（预注册 §3.2.5）。

    ⇒ 差异条数与成因**逐条报出**（不许藏）。
    """
    from aplus.factor_fundamental import annual_from_frame, bare_code

    raw = pd.read_parquet(cache)
    sel = active_annual_by_day(spine, dates)
    rows = []
    for d in dates:
        # ⚠️⚠️ **两边都必须归一**（`bare_code`）⇒ 否则「不一致」其实是
        # **码格式差异**不是可见性差异（第 7 层）。
        # 🚨 实测：**本轮骨架的码也是带后缀的**（`000010.SZ`，
        # 因为缓存的 `SECURITY_CODE` 本身就带后缀，`_annual_gates` 的
        # `str.zfill(6)` 对 8 位码**不生效**）⇒ 只归一 aplus 一侧
        # ⇒ 两边**永远**对不上 ⇒ `same_selection` 恒 False
        # ⇒ 而那个 False 长得跟「PIT 可见性不一致」一模一样（第 143 层）。
        mine = {bare_code(c): r for c, r in
                (sel.loc[sel["day"] == d]
                 .set_index("code")["report_date"].to_dict()).items()}
        ref = {bare_code(c): r for c, r in
               annual_from_frame(raw, d).set_index("code")["report_date"].items()}
        only_mine = sorted(set(mine) - set(ref))
        only_ref = sorted(set(ref) - set(mine))
        diff = [(c, str(mine[c]), str(ref[c]))
                for c in sorted(set(mine) & set(ref))
                if str(mine[c]) != str(ref[c])]
        rows.append({
            "as_of": d,
            "n_mine": len(mine), "n_aplus": len(ref),
            "only_in_mine": only_mine[:10], "only_in_aplus": only_ref[:10],
            "report_date_diffs": diff[:10],
            "n_report_date_diffs": len(diff),
            "same_selection": (not only_mine and not only_ref and not diff),
        })
    return {"probe_dates": rows,
            "note": ("逐日 asof（`active_annual_by_day`）与 aplus 的 "
                     "`annual_from_frame` 在同一日选出同一份报告 ⇒ "
                     "可见性判据两路一致；差异逐条列出（**不藏**）")}


# ── 判决链（预注册 §6.1：结构闸 → MDE 闸 → 显著性 → 达标）────────

def nc_state(gross_nc: float | None, t_nc: float | None,
             t_crit: float | None, gross_orig: float | None) -> dict:
    """负控（符号翻转）的三态判定（预注册 §5 C1）。

    * **显著为正**（`gross > 0` 且 `|t| ≥ t_crit`）⇒ **整套判决作废**
      （预注册的显式箭头）。
    * **翻号后显著更好**（`gross_nc > gross_orig` 且 `|t_nc| ≥ t_crit`）
      ⇒ 同样作废（预注册 §5 C1 第二句「负控毛超额不得高于原臂」；
      ⚠️ 该句**本身**没写显著性门槛 ⇒ 单看它会有一半概率靠噪声触发
      ⇒ 这里**只让它在显著时才起作用**，并在产物里**逐项写明**。
    * 其余 ⇒ `PASS`（不决定）。

    ⚠️ 第三个数字（`gross_nc` 与 `gross_orig` 谁大）**永远写进产物**，
    读者可以按自己的口径重判（**不藏**）。
    """
    if gross_nc is None or t_nc is None or t_crit is None:
        return {"state": "INCONCLUSIVE",
                "reason": "负控读数缺失（配对日不足）⇒ 不决定",
                "gross_nc": gross_nc, "t_nc": t_nc, "t_crit": t_crit}
    sig = abs(t_nc) >= t_crit
    nc_positive = gross_nc > 0
    nc_better = (gross_orig is not None and gross_nc > gross_orig)
    if sig and nc_positive:
        return {"state": "VOID",
                "reason": (f"负控（翻号）臂毛超额 {gross_nc:+.4%} 显著为正"
                           f"（|t| = {abs(t_nc):.3f} ≥ {t_crit:.3f}）"
                           f" ⇒ 预注册 §5 C1：**整套判决作废**"),
                "gross_nc": gross_nc, "t_nc": t_nc, "t_crit": t_crit,
                "significantly_positive": True,
                "better_than_original": bool(nc_better)}
    if sig and nc_better:
        return {"state": "VOID",
                "reason": (f"负控（翻号）臂毛超额 {gross_nc:+.4%} **显著高于**"
                           f"原臂 {gross_orig:+.4%}（|t| = {abs(t_nc):.3f} "
                           f"≥ {t_crit:.3f}）⇒ 预注册 §5 C1 第二句："
                           f"**整套判决作废**"),
                "gross_nc": gross_nc, "t_nc": t_nc, "t_crit": t_crit,
                "significantly_positive": bool(nc_positive),
                "better_than_original": True}
    return {"state": "PASS",
            "reason": (f"负控（翻号）臂毛超额 {gross_nc:+.4%}"
                       f"（|t| = {abs(t_nc):.3f} < {t_crit:.3f}）"
                       f" ⇒ 未显著 ⇒ 不作废"),
            "gross_nc": gross_nc, "t_nc": t_nc, "t_crit": t_crit,
            "significantly_positive": bool(nc_positive),
            "better_than_original": bool(nc_better)}


def verdict_for_leg(*, gross: float | None, mde: float | None,
                    t: float | None, t_crit: float | None, n_pairs: int,
                    d1: float | None, d2: float | None,
                    prefix: str) -> dict:
    """四态（预注册 §6.4）：`LEVER` / `SIG_NOT_DELIVERABLE` /
    `NOT_MEASURED` / `POWERLESS`（**MDE 闸必须第一个判**）。

    ⚠️ `POWERLESS` 的文案是「**测不到**」⇒ **不许**写「无效/没有/已证伪」。
    """
    if n_pairs < 3:
        return {"state": f"{prefix}_NOT_MEASURED",
                "reason": (f"结构闸：配对日 {n_pairs} < 3 ⇒ "
                           f"**不报数**（不回落成 0）"),
                "wording": NOT_MEASURED_WORDING}
    if gross is None or mde is None:
        return {"state": f"{prefix}_NOT_MEASURED",
                "reason": "腿表读不出来（覆盖不足）⇒ 不报数",
                "wording": NOT_MEASURED_WORDING}
    ratio = abs(gross) / mde if mde > 0 else None
    if ratio is not None and ratio < 1:
        return {"state": f"{prefix}_POWERLESS",
                "reason": (f"MDE 闸先判：|长腿毛超额| {abs(gross):.4%} < "
                           f"MDE {mde:.4%}（比值 {ratio:.3f} < 1）"
                           f" ⇒ 本设计**测不到**这个量级的效应"),
                "effect_over_mde": ratio,
                "shape_only": True,
                "wording": NOT_MEASURED_WORDING}
    if t is None or t_crit is None:
        return {"state": f"{prefix}_NOT_MEASURED",
                "reason": "配对 t 读不出来（序列退化）⇒ 不报数",
                "wording": NOT_MEASURED_WORDING}
    if abs(t) < t_crit:
        return {"state": f"{prefix}_NOT_MEASURED",
                "reason": (f"测到了但不够：|长腿毛超额|/MDE = {ratio:.3f} ≥ 1"
                           f"（效应**测得到**），而 |t| = {abs(t):.3f} < "
                           f"临界 {t_crit:.3f} ⇒ 换因子或换 K，**不换判据**"),
                "effect_over_mde": ratio, "shape_only": True,
                "wording": NOT_MEASURED_WORDING}
    if d1 is None or d2 is None:
        raise ValueError(
            f"交付阈值读不到（D1={d1} / D2={d2}）⇒ 拒绝给四态判决"
            f"（阈值必须从产物读，不许手打）")
    d1_ok = gross > d1
    d2_ok = gross > d2
    if d1_ok and d2_ok:
        return {"state": f"{prefix}_LEVER",
                "reason": (f"过线（|t| = {abs(t):.3f} ≥ {t_crit:.3f}）"
                           f"且 D1/D2 全过"),
                "effect_over_mde": ratio, "d1_pass": d1_ok, "d2_pass": d2_ok}
    return {"state": f"{prefix}_SIG_NOT_DELIVERABLE",
            "reason": (f"过线（|t| = {abs(t):.3f} ≥ {t_crit:.3f}）**但吃不到**"
                       f"（长腿年化毛超额 {gross:+.4%} vs D1 摩擦 {d1:.4%} / "
                       f"D2 所需 {d2:.4%}）⇒ 查是否可作**增量腿**"),
            "effect_over_mde": ratio, "d1_pass": d1_ok, "d2_pass": d2_ok}


# ── 跑一条臂 ───────────────────────────────────────────────────────

def run_arm(args) -> dict:
    """臂 R 同款流程：面板 → 年报 → 逐日 asof → 挂因子 → 前瞻收益 → 腿表。

    🚨 **bm 对照臂走的是与成长/现金流**完全相同**的代码路径**
    （同一份 merge、同一份 attach、同一份腿表函数）⇒ 它复现不出已发表的
    负读数时，**本轮四个因子的读数一律不可信**（预注册 §5 C2）。
    """
    from scripts.fundamental_family_factors import factor_values

    t0 = time.time()
    xs = _load_panel(args.cache)
    daily_map = xs.get("_daily_map") or {}

    # 骨架 = 臂 R 的年报序列 **+ 额外财务列**（只加列不改行，预注册 §3.2.2）
    spine = annual_series_extended(args.financial)
    days = sorted(d for d in (xs.get("_dates") or [])
                  if WINDOW[0] <= d <= WINDOW[1])
    grid = sorted({d for k in KS for d in _grid_days(days, k)})

    # 逐日 asof（**复用** `active_annual_by_day`）⇒ 再把额外列接回去
    active_ext = active_with_extra(spine, grid)

    # 🚨🚨 **空操作守卫**：额外列若因码格式/键不匹配而整列 NaN，
    # 四个因子就全是 NaN ⇒ 覆盖率 0 ⇒ 每格都判「配对日不足」
    # ⇒ **产物照样出、判决照样出、只是全是「没测到」** ⇒ 长得完全正常
    # （第 143 层：恒真/空操作与「确实没信号」同形）。
    # ⇒ 这里**当场**量一次非空数：低于阈值 ⇒ 抛错，**不许**落一份空产物。
    src_cols = sorted({c for v in EXTRA_SOURCE_COLUMNS.values() for c in v})
    nonnull = {c: int(active_ext[c].notna().sum()) for c in src_cols}
    if not nonnull or min(nonnull.values()) < max(10, 0.001 * len(active_ext)):
        raise AssertionError(
            f"额外财务列接回来后几乎全空：{nonnull} / {len(active_ext)} 行 "
            f"⇒ join 没接上（码格式或键不对）⇒ **拒绝**落一份空产物")

    # 覆盖池：与 bm **同构**（年报可见 ∧ 当日有价）⇒ 市场腿与 bm 可比
    px = price_and_shares_on(daily_map, grid, carry_stale=True)
    m = factor_values(active_ext, px)
    m = factor_values_gc(m)

    attach_cols = ["bm"] + list(GC_FACTOR_SPECS)
    xs = _attach_to_panel(xs, m, grid, attach_cols)

    fwd_by_k = {}
    for k in KS:
        t1 = time.time()
        fwd_by_k[k] = {d: _forward_map(xs, d, k) for d in _grid_days(days, k)}
        print(f"  K={k} 前瞻收益 {len(fwd_by_k[k])} 天"
              f"（{time.time() - t1:.0f}s）", flush=True)

    coverage = {}
    for name in attach_cols:
        ns = [len(_factor_values_on(xs, d, name)) for d in grid]
        coverage[name] = {
            "n_days": len(grid),
            "mean_n": float(np.mean(ns)) if ns else 0.0,
            "median_n": float(np.median(ns)) if ns else 0.0,
            "min_n": int(np.min(ns)) if ns else 0,
            "max_n": int(np.max(ns)) if ns else 0,
        }

    # 🚨 **同一把尺子**的逐位对拍（第 137 层）：两份腿表实现必须逐位相等
    equiv = instrument_equivalence_check(xs, days, fwd_by_k, n_a=args.n_a_equiv)

    # 🚨 厂商那一列**到底是什么意思**（外部锚 ⇒ 逐行反算，不是看名字）
    vendor_check = verify_vendor_growth_columns(args.financial)

    return {"xs": xs, "days": days, "grid": grid, "m": m,
            "active": active_ext, "spine": spine,
            "fwd_by_k": fwd_by_k, "coverage": coverage,
            "instrument_equivalence": equiv,
            "vendor_growth_check": vendor_check,
            "elapsed_s": time.time() - t0}


# ── 族级标签（逐格分布 + 计数，**不取极值**，第 143 层）──────────

def family_state(cells: dict, prefix: str) -> dict:
    """族级标签 = **逐格分布**（过线格数 / 各态计数），**不取极值**。

    🚨 规则与 round B 的 `family_state` **逐字同构**（同一个口径），
    只是状态名换成本轮族前缀。
    """
    states = {}
    for v in cells.values():
        # ⚠️ 判决在 `v["verdict"]["state"]`（**判决层**，不是格子层）
        st = v["verdict"]["state"]
        states[st] = states.get(st, 0) + 1
    passed = sum(1 for v in cells.values()
                 if v["verdict"]["state"] in (f"{prefix}_LEVER",
                                             f"{prefix}_SIG_NOT_DELIVERABLE"))
    if passed == len(cells) and cells:
        state = (f"{prefix}_LEVER"
                 if all(v["verdict"]["state"] == f"{prefix}_LEVER"
                        for v in cells.values())
                 else f"{prefix}_SIG_NOT_DELIVERABLE")
    elif passed > 0:
        state = f"{prefix}_SIG_NOT_DELIVERABLE"
    elif cells and all(v["verdict"]["state"] == f"{prefix}_POWERLESS"
                       for v in cells.values()):
        state = f"{prefix}_POWERLESS"
    else:
        state = f"{prefix}_NOT_MEASURED"
    return {"state": state, "state_counts": states, "n_cells": len(cells),
            "cells_pass_family_a": passed,
            "family_state_rule": ("族级标签 = **逐格分布**（过线格数 / 各态计数），"
                                  "**不取极值**（第 143 层：跨格取极值会抹掉"
                                  "它从哪一行来）")}


# ── 产物 ──────────────────────────────────────────────────────────

def _float(x):
    """numpy 标量 ⇒ JSON 可序列化的 float（**不四舍五入**：逐位可对）。"""
    if x is None:
        return None
    return float(x)


# ── 产物装配（**全部提成模块级纯函数**）─────────────────────────
#
# 🚨🚨 为什么必须提上来：仓内铁律「**守卫 import 不到的代码等于零守卫**」
# —— 嵌套在 `build_payload` 里的装配逻辑，**任何测试都够不着**
# ⇒ 改坏它（把负控臂的读数当成头条读数、把 K=21 的门槛挂到 K=63 的格子上）
# **没有任何东西会红**。
# ⇒ 提上来之后，守卫能**直接问**「你装的是哪一条腿」。

LONG_LEG_CALIBER = (
    "**多头口径**：长腿（因子值排序 top-10，等权）− "
    "市场腿（**同一覆盖池**等权均值）；年化 = 每 K 日均值 × "
    "244/K。🚨 **不是多空口径**（顶−底）：融券不可得，"
    "书里的多空数**不是**本项目的可达目标")

NC_CLAUSE_B_NOTE = (
    "预注册 §5 C1 第二句「负控毛超额不得高于原臂」**没有写显著性门槛**；"
    "本产物**只在它显著时**才判作废（否则纯噪声会有一半概率作废），"
    "并把 `clause_b_satisfied` 与三个原始数**逐项写出来** ⇒ 读者可按"
    "自己的口径重判（**不藏**）")


def cell_payload(name: str, spec: dict, k: int, ladder: dict,
                 fladder: dict, tset: dict) -> dict:
    """**一格**（因子 × K）的全部读数（纯函数 ⇒ 守卫够得着）。

    ⚠️ 这里最要紧的一条性质：**头条读数与阶梯表 `n=10` 那一行必须是同一个数**
    ⇒ 「读数逐位等于产物字段」不是靠人读，是靠**同一个来源**。
    """
    l10 = ladder.get("10") or {}
    f10 = fladder.get("10") or {}
    gross = l10.get("gross_annual_pct")
    mde = l10.get("mde_annual_pct")
    v = verdict_for_leg(
        gross=gross, mde=mde, t=l10.get("t"), t_crit=l10.get("t_crit"),
        n_pairs=int(l10.get("n") or 0),
        d1=tset["d1_friction_annual_pct"],
        d2=tset["d2_required_gross_annual_pct"],
        prefix=spec["family"])
    nc = nc_state(f10.get("gross_annual_pct"), f10.get("t"),
                  l10.get("t_crit"), gross)
    nc["clause_b_satisfied"] = bool(
        f10.get("gross_annual_pct") is not None
        and gross is not None
        and f10["gross_annual_pct"] <= gross)
    nc["clause_b_note"] = NC_CLAUSE_B_NOTE
    return {
        "factor": name, "family": spec["family"], "k": k,
        "sign": LONG_SIGN[name],
        "n_pairs": int(l10.get("n") or 0),
        "long_leg": {
            "n_positions": 10, "k": k,
            "gross_annual_fraction": _float(gross),
            "gross_annual_pct": (None if gross is None else gross * 100.0),
            "long_leg_annual_fraction": _float(l10.get("long_leg_annual_pct")),
            "market_leg_annual_fraction": _float(l10.get("market_leg_annual_pct")),
            "mean_per_k": _float(l10.get("mean_per_k")),
            "sd_per_k": _float(l10.get("sd_per_k")),
            "t": _float(l10.get("t")), "t_crit": _float(l10.get("t_crit")),
            "mde_annual_fraction": _float(mde),
            "mde_annual_pct": (None if mde is None else mde * 100.0),
            "caliber": LONG_LEG_CALIBER,
        },
        "leg_ladder": {n: {kk: _float(vv) if kk in
                            ("mean_per_k", "gross_annual_pct", "sd_per_k",
                             "t", "t_crit", "mde_per_k", "mde_annual_pct",
                             "long_leg_annual_pct", "market_leg_annual_pct")
                            else vv
                            for kk, vv in row.items()}
                        for n, row in ladder.items()},
        "negative_control": nc,
        "negative_control_leg": {
            "sign_flipped": True,
            "gross_annual_fraction": _float(f10.get("gross_annual_pct")),
            "t": _float(f10.get("t")),
            "n_pairs": int(f10.get("n") or 0),
        },
        "verdict": v,
        "thresholds": tset,
    }


def caliber_block() -> dict:
    """**两臂的口径标签**（纯函数 ⇒ 守卫能问「标签有没有装反」）。

    🚨 口径标签被装反是**静默**的：数字全对、判决全对，
    而读者会把「判决臂的读数」当成「交付档的读数」去比 20%（第 7 层）。
    """
    return {
        "this_arm": ("**判决臂（臂 R 口径）**：500 只缓存池 · 2019-01-02→"
                     "2023-12-31 · 榜宽 10 · **毛**超额 · 未扣费 · 无整手 · "
                     "未做账户腿 · 市场腿 = 覆盖池等权均值"),
        "baseline_arm": ("**交付档（实盘档）**：5,460 只全池 · 2021-01-01→"
                         "2026-10-12 · K=63 · E=100 满仓 · ¥5 万 · 整手 · "
                         "逐笔成本"),
        "unit_note": ("⚠️ **单位**：本产物内 `*_fraction` 是**分数**、"
                      "`*_pct` 是**百分数**；交付档产物的 "
                      "`gross_excess_annual_pct` 是**百分数** ⇒ 跨产物比较时"
                      "**必须先换算**（第 182 层）"),
        "account_leg": ("本轮**没有**账户腿（无 ¥5 万/整手/逐笔成本）⇒ "
                        "**不可**与交付档的净值/CAGR 直接比大小"),
    }


def baseline_block(deployed: dict, requirement: dict, mc: dict) -> dict:
    """**基线 + 所需毛超额 + 同口径倍数**（纯函数 ⇒ 守卫能问「你挑的是哪一列」）。

    🚨 `mc` = `headline.multiple_needed_at_50k_deliverable_by_caliber` 的**整块**
    ⇒ 本函数**自己**从整块里挑 `same_caliber` ⇒ 跨口径那一列（台账自报
    `is_the_gap = false`）**碰不到** ⇒ 读者也就无从误引。
    """
    same = mc["same_caliber"]
    return {
        "delivered_price_arm": {
            "source": ("data/reports/deployed_d2_verdict.json::k_grid."
                       "cells[\"LIVE_DEFAULT|ARM_A|K63|E100\"]"
                       ".k_axis.gross_excess_annual_pct"),
            "gross_annual_pct": deployed["gross_annual_pct"],
            "caliber": ("**交付档**：5,460 只全池 · 2021-01-01→2026-10-12 · "
                        "K=63 · E=100 满仓 · ¥5 万 · 整手过滤 · 逐笔成本 · "
                        "市场腿 = 抽样 150 只等权日收益"),
            "is_the_baseline_for_gap": True,
        },
        "required_gross_at_50k_deliverable": {
            "source": ("data/reports/d2_signal_requirement.json::headline."
                       "required_gross_at_50k_deliverable"),
            "percent": requirement["percent"],
            "fraction": requirement["fraction"],
            "unit_note": ("⚠️ **单位陷阱**：headline 是**百分数**（11.2813），"
                          "`rows[n=10].required_gross_at_50k` 是**分数**（0.1128）"),
        },
        "same_caliber_gap_multiple": {
            "value": same["multiple_needed"],
            "source": ("data/reports/d2_signal_requirement.json::headline."
                       "multiple_needed_at_50k_deliverable_by_caliber."
                       "same_caliber.multiple_needed"),
            "caliber": ("**同口径**：K=63 · 生产臂 LIVE_DEFAULT · 配置层 ARM_A · "
                        "榜宽 10 · ¥5 万 · 满仓 · 同一等权市场腿"),
            "is_the_gap": True,
            "note": ("**这是本仓唯一可称作「还差几倍」的数**：要求 ÷ "
                     "**同口径**的实测（K=63 生产臂 6.3034%）。"
                     "🚨 另有一个**跨口径**倍数（K=21 研究臂实测 ÷ K=63 要求）"
                     "⇒ 台账自报 `is_the_gap = false` ⇒ **本产物不引用它**"),
        },
        "arm_r_readings_are_not_same_caliber": {
            "caliber_match": False,
            "differences": [
                "池：500 只缓存池 vs 交付档 5,460 只全池",
                "窗口：2019-01-02→2023-12-31 vs 2021-01-01→2026-10-12",
                "市场腿：覆盖池等权均值 vs 抽样 150 只等权日收益",
                "费用：毛超额（未扣费、无整手）vs 交付档含整手与逐笔成本",
                "信号：单因子 vs 交付档 4 因子 DEFAULT_WEIGHTS 组合",
            ],
            "consequence": ("⇒ 「11.2813% ÷ 本轮读数」是**跨口径**的比值 ⇒ "
                            "**只能作形状陈述**，**不许**写成 D2 缺口"),
        },
    }


def build_payload(args, arm: dict, thr: dict, published: dict,
                   deployed: dict, requirement: dict, mc: dict) -> dict:
    """把这一轮的所有读数装成产物（**口径标签逐项带**）。"""
    xs, days, grid = arm["xs"], arm["days"], arm["grid"]
    fwd = arm["fwd_by_k"]

    cells = {}
    for name, spec in GC_FACTOR_SPECS.items():
        sign = LONG_SIGN[name]
        for k in KS:
            fwd_k = fwd[k]
            ladder = leg_table_signed(xs, days, name, k, fwd_k, sign,
                                      n_a=M_CANDIDATES)
            fladder = leg_table_signed(xs, days, name, k, fwd_k, -sign,
                                       n_a=M_CANDIDATES)
            tset = thr["k21"] if k == 21 else thr["k63"]
            cells[f"{name}@K{k}"] = cell_payload(
                name, spec, k, ladder, fladder, tset)

    # ── C2 对照臂：bm 复现（**同一条代码路径**）────────────────
    bm_cells = {}
    for k in KS:
        ladder = leg_table_signed(xs, days, "bm", k, fwd[k], ARMR_LONG_SIGN["bm"],
                                  n_a=M_CANDIDATES)
        l10 = ladder.get("10") or {}
        pub = (published.get("family_a_cells", {})
               .get(f"bm@K{k}", {}).get("long_leg", {}))
        pub_gross = pub.get("gross_annual_pct")
        got_gross = l10.get("gross_annual_pct")
        diff = (None if (pub_gross is None or got_gross is None)
                else abs(got_gross - pub_gross))
        bm_cells[f"bm@K{k}"] = {
            "k": k,
            "published_gross_annual_fraction": pub_gross,
            "reproduced_gross_annual_fraction": _float(got_gross),
            "abs_diff": _float(diff),
            "within_tolerance": bool(diff is not None and diff <= BM_REPRO_TOL),
            "prereg_target": BM_REPRO_TARGET.get(f"bm@K{k}"),
            "prereg_target_matches_published": bool(
                pub_gross is not None
                and abs(pub_gross - BM_REPRO_TARGET.get(f"bm@K{k}", 1e9)) <= 1e-15),
            "n_pairs": int(l10.get("n") or 0),
            "t": _float(l10.get("t")),
            "note": ("**同一台仪器**（同一份面板缓存、同一份财务缓存、"
                     "同一份前瞻收益、同一份腿表函数）⇒ 若这里复现不出，"
                     "本轮四个因子的读数**一律不可信**（预注册 §5 C2）"),
        }

    # ── 族级标签 ────────────────────────────────────────────
    fam = {}
    for fam_name in FAMILIES:
        fcells = {k: v for k, v in cells.items() if v["family"] == fam_name}
        fam[fam_name] = family_state(fcells, fam_name)

    # ── 与基线并列（**口径逐项标出**，第 7 层 / 第 143 层）────
    baseline = baseline_block(deployed, requirement, mc)

    same_ruler = {}
    for fam_name in FAMILIES:
        rows = {}
        for name in GC_FACTOR_SPECS:
            if GC_FACTOR_SPECS[name]["family"] != fam_name:
                continue
            for k in KS:
                key = f"{name}@K{k}"
                g = cells[key]["long_leg"]["gross_annual_fraction"]
                rows[key] = {
                    "gross_annual_fraction": g,
                    "gross_annual_pct": (None if g is None else g * 100.0),
                }
        same_ruler[fam_name] = {
            "instrument": ("**臂 R 同一把尺子**：500 只缓存池 · 2019→2023 · "
                           "K ∈ {21,63} · 榜宽 10 · 毛超额 · 覆盖池市场腿"),
            "rows": rows,
            "note": ("**同一把尺子下三族并列** ⇒ 族间比较**同口径**；"
                     "与交付档 +6.3034% 的比较**跨口径**（见上）"),
        }

    verdict_voided = [k for k, v in cells.items()
                      if v["negative_control"]["state"] == "VOID"]
    # 🚨🚨 **预注册 §5 C1 第二句的字面读法**（「负控毛超额不得高于原臂」，
    # **没有**显著性门槛）会把若干格判成违反 ⇒ 若按字面执行，**整套判决作废**。
    # 本产物**只在负控显著时**才让它作废（见 `clause_b_note`）。
    # ⇒ 两个读法的差**必须写出来**（不藏）：读者可按自己的口径重判。
    clause_b_violated = sorted(k for k, v in cells.items()
                               if v["negative_control"]["clause_b_satisfied"]
                               is False)
    bm_ok = all(v["within_tolerance"] for v in bm_cells.values())

    return {
        "kind": "growth_cashflow_arm",
        "prereg": PREREG,
        "prereg_sections": ["§2 因子定义", "§3 PIT 正确性", "§4 测量口径",
                             "§5 对照臂", "§6 判决", "§7 与基线并列"],
        "arm": args.arm_name,
        "window": list(WINDOW),
        "k_arms": list(KS),
        "financial_source": {
            "path": str(Path(args.financial).resolve()),
            "sha256": _sha256_file(Path(args.financial)),
            "note": ("⚠️ **后修值**：数值是采集时（2026-10-14）的版本，"
                     "**不是**披露当时的版本 ⇒ 可见性对、数值可能是后修的 ⇒ "
                     "本轮读数**只作机理/诊断级**（预注册 §3.3）"),
        },
        "panel_cache": {
            "path": str(Path(args.cache).resolve()),
            "sha256": _sha256_file(Path(args.cache)),
        },
        "caliber": caliber_block(),
        "factor_definitions": {
            name: {
                "family": spec["family"], "definition": spec["definition"],
                "source_columns": list(spec["source_columns"]),
                "direction": spec["direction"], "sign": LONG_SIGN[name],
                "long_leg_end": ("大的一端" if LONG_SIGN[name] > 0 else "小的一端"),
                "book_lines": spec["book_lines"],
                "missing_rule": spec["missing_rule"],
                "unit": spec["unit"],
                "vendor_formula": spec["vendor_formula"],
            } for name, spec in GC_FACTOR_SPECS.items()
        },
        "factor_definition_verification": arm["vendor_growth_check"],
        "coverage": arm["coverage"],
        "pit_check": arm["pit_check"],
        "instrument_equivalence": arm["instrument_equivalence"],
        "family_cells": cells,
        "control_bm_reproduction": {
            "mechanism": ("**同一台仪器**跑 bm ⇒ 必须复现已发表的负读数；"
                           "复现不出 ⇒ 本轮四个因子**一律不可信**"),
            "cells": bm_cells,
            "all_within_tolerance": bm_ok,
            "tolerance": BM_REPRO_TOL,
        },
        "verdict": {
            "calibers": {
                "primary": {
                    "m_candidates": M_CANDIDATES,
                    "m_note": ("预注册 §6.2：m = 8 = 4 因子 × 2 K 档；"
                                "对照臂**不计入** m，但用**同一个** t_crit"),
                    "by_family": fam,
                },
            },
            "primary": {
                "by_family": fam,
                "voided_by_negative_control": bool(verdict_voided),
                "voided_cells": verdict_voided,
                "void_note": ("预注册 §5 C1：负控**显著为正** ⇒ **整套判决作废**"
                              "（产物的读数仍全部保留，**不藏**）"),
                "clause_b_literal_reading": {
                    "cells_violating_clause_b": clause_b_violated,
                    "n_cells_violating": len(clause_b_violated),
                    "what_would_happen": (
                        "🚨 预注册 §5 C1 第二句「负控毛超额不得高于原臂」"
                        "**没有写显著性门槛** ⇒ **按字面执行**，上列这些格子"
                        "违反该句 ⇒ **整套判决应当作废**。"
                        "本产物**只在负控显著时**才判作废（否则纯噪声会有一半"
                        "概率作废）⇒ 两种读法的差**写在这里**，**不藏**。"
                        "⇒ 若读者要按字面读法执行 ⇒ 本轮两族判决**作废**。"),
                },
                "instrument_ok": bm_ok,
            },
        },
        "baseline_comparison": baseline,
        "same_ruler_lineup": same_ruler,
        "wording_guard": {
            "powerless_wording": NOT_MEASURED_WORDING,
            "note": ("`POWERLESS` 读作「**这个设计测不到**」；**不许**写"
                     "「无效」「没有」「已证伪」"),
        },
        "no_promotion": ("本轮读数来自**后修值**财务源（见 `financial_source.note`）"
                         "⇒ **只作机理/诊断级**，**不产生任何采纳结论**、"
                         "**不宣称**任何族能到 20%"),
        "what_this_cannot_prove": [
            "**测不到 ≠ 没有**：`|效应| < MDE` 的格子**只说这个设计测不到这个量级的效应**",
            "本轮是**毛超额**（未扣费、无整手、无账户腿）⇒ **不能**与交付档的净值/CAGR 比大小",
            "与基线 +6.3034% 的比较**跨口径**（池/窗口/市场腿/费用/信号五处不同）⇒ 只能作形状陈述",
            "财务源是**后修值** ⇒ 可见性对、数值可能已被修订过",
            "**没有**前瞻窗口、**没有**全池臂、**没有**账户腿 ⇒ 本轮**回答不了**「20% 够不够」",
        ],
        "scope": {
            "arm_run": ("**臂 R 判决臂**（500 只缓存池 · 2019-01-02→2023-12-31 · "
                        "K ∈ {21,63} · 榜宽 10 · 毛超额）"),
            "arm_not_run": ("**全池 5,460 臂**（交付档口径）**没跑**："
                            "① 全池 PIT 财务覆盖 2024-07 才有（实测 2021-01-04 → 42 只）"
                            "⇒ 交付窗口 2021-01-01→2026-10-12 的**全池财务臂不可构造**；"
                            "② 内存：全池面板是 **1.7 GB 级 pickle**，而本机同时有"
                            "**生产采集在跑**（预注册 §3.3 记录当时 swap 已用 3.4/5.1 GB）"
                            "⇒ 按预注册 §3.3 **只跑 500 只臂**"
                            "（面板 170 MB，sha256 钉死）"),
            "panel_note": ("面板缓存 sha256 与 round B 产物自报的**逐位相同** ⇒ "
                           "**同一块面板**"),
        },
    }


# ── 四个因子的定义（预注册 §2，**写死**，跑前不许改）──────────────

#: 因子 → 族 / 定义 / 源列 / 方向 / 书行号 / 缺失规则
GC_FACTOR_SPECS = {
    "g_profit": {
        "family": "GROWTH",
        "definition": "归母净利同比增长率 = `PARENTNETPROFITTZ`",
        "source_columns": ("PARENTNETPROFITTZ",),
        "direction": "越大越好",
        "book_lines": "cn_factor_lines.txt:6501-6502、:6529-6530、:6525-6527",
        "missing_rule": ("源列为空即缺失；**不做** winsorize / 行业中性化 / "
                         "再乘任何别的因子"),
        "unit": ("**百分数**（58.716278 = 同比 **+58.72%**，不是分数 0.587）"
                 "—— 实测由 `verify_vendor_growth_columns` 逐行反算确认"),
        "vendor_formula": ("厂商列 = `100 × (本期 − 上年) / |上年|`"
                           "（**分母取绝对值** ⇒ 上年为亏损时符号与 "
                           "`本期/上年 − 1` 不同）"),
    },
    "g_rev": {
        "family": "GROWTH",
        "definition": "营业总收入同比增长率 = `TOTALOPERATEREVETZ`",
        "source_columns": ("TOTALOPERATEREVETZ",),
        "direction": "越大越好",
        "book_lines": "cn_factor_lines.txt:6529-6530、:9154",
        "missing_rule": "同 `g_profit`",
        "unit": "**百分数**（同 `g_profit`）",
        "vendor_formula": "同 `g_profit`（基数换成 `TOTALOPERATEREVE`）",
    },
    "cf_accrual": {
        "family": "CASHFLOW",
        "definition": ("应计占营业总收入比 = (`PARENTNETPROFIT` − `KCFJCXSYJLR`) "
                       "/ `TOTALOPERATEREVE`（分子分母都是**总额**，尺度无关）"),
        "source_columns": ("PARENTNETPROFIT", "KCFJCXSYJLR", "TOTALOPERATEREVE"),
        "direction": "越小越好",
        "book_lines": "cn_factor_lines.txt:6473-6478",
        "missing_rule": ("**分母 ≤ 0 ⇒ 该 (日,票) 缺失**（不裁剪、不加 epsilon、"
                         "不取绝对值）"),
        "unit": "**无量纲比率**（分子分母同为金额 ⇒ 尺度无关）",
        "vendor_formula": "**本轮自算**（厂商没有这一列）",
    },
    "cf_growth": {
        "family": "CASHFLOW",
        "definition": "经营现金流净额同比增长率 = `KCFJCXSYJLRTZ`",
        "source_columns": ("KCFJCXSYJLRTZ",),
        "direction": "越大越好",
        "book_lines": "cn_factor_lines.txt:11252-11255",
        "missing_rule": "同 `g_profit`",
        "unit": "**百分数**（同 `g_profit`）",
        "vendor_formula": "同 `g_profit`（基数换成 `KCFJCXSYJLR`）",
    },
}


#: 三个成长率因子：厂商列 → 它的基数列（用来**反算**列的含义）
_VENDOR_GROWTH_PAIRS = (
    ("g_profit", "PARENTNETPROFITTZ", "PARENTNETPROFIT"),
    ("g_rev", "TOTALOPERATEREVETZ", "TOTALOPERATEREVE"),
    ("cf_growth", "KCFJCXSYJLRTZ", "KCFJCXSYJLR"),
)


def verify_vendor_growth_columns(cache: Path) -> dict:
    """**反算厂商那一列到底是什么意思**（外部锚，不是断言「名字对得上」）。

    🚨 列名/字段名**不承诺语义**（第 159 层）：`PARENTNETPROFITTZ` 叫「归母净利
    同比增长率」，但它**是百分数还是分数**、**分母取不取绝对值**，
    光看名字定不了 ⇒ 而这正是「量纲必须由产物自己声明」（第 182 层）。

    ⇒ 做法：拿**基期金额**（`PARENTNETPROFIT` 等）与**本期金额**逐行反算，
    看厂商那一列等于 `100 × (本期 − 基期) / |基期|` 的比例有多少。
    ⇒ 结论进产物 ⇒ 读者可按它判断这个因子是什么量。

    ⚠️ **对读数的影响为零**：因子值只用来**排序**取 top-10，
    乘 100 是**单调**的 ⇒ 选出的票与顺序完全不变。
    ⇒ 这一条是**语义声明**，不是数值修正。
    """
    raw = pd.read_parquet(cache)
    ann = raw[raw["REPORT_TYPE"].astype(str).str.strip() == "年报"].copy()
    ann["code"] = ann["SECURITY_CODE"].astype(str).str.zfill(6)
    ann["yr"] = pd.to_datetime(ann["REPORT_DATE"], errors="coerce").dt.year
    ann = ann.sort_values(["code", "yr"])
    out = {}
    for factor, col, base in _VENDOR_GROWTH_PAIRS:
        # ⚠️ **向量化，但算术顺序与逐行版逐字相同**
        #   （`100 × (cb − pb) / |pb|`，先减后乘后除）⇒ 结果**逐位**相同
        pb = pd.to_numeric(ann[base], errors="coerce").shift(1)
        py = ann["yr"].shift(1)
        pb = pb.where(ann["code"].eq(ann["code"].shift(1)))
        py = py.where(ann["code"].eq(ann["code"].shift(1)))
        cb = pd.to_numeric(ann[base], errors="coerce")
        v = pd.to_numeric(ann[col], errors="coerce")
        ok = ((ann["yr"] - py) == 1) & pb.notna() & (pb != 0) \
            & cb.notna() & v.notna()
        n = int(ok.sum())
        if n == 0:
            out[factor] = {"column": col, "base_column": base,
                           "n_rows": 0, "match_rate": None}
            continue
        vendor = v[ok].astype(float)
        recomputed = (100.0 * (cb[ok].astype(float) - pb[ok].astype(float))
                      / pb[ok].abs().astype(float))
        rel = ((vendor - recomputed).abs()
               / recomputed.abs().clip(lower=1e-12))
        out[factor] = {
            "column": col, "base_column": base,
            "n_rows": n,
            "formula_tested": "100 × (本期 − 基期) / |基期|",
            "match_rate_with_rel_tol_1e-9": float((rel < 1e-9).mean()),
            "median_rel_error": float(rel.median()),
            "p99_rel_error": float(rel.quantile(0.99)),
            "unit": "**百分数**（不是分数）",
            "why_it_matters": ("量纲必须由产物自己声明（第 182 层）；"
                               "本因子只用于**排序**⇒ 乘 100 是单调的 ⇒ "
                               "**对读数零影响**，这一条是语义声明"),
        }
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── main ──────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="成长族 + 现金流族的长腿臂（臂 R 同款仪器）")
    ap.add_argument("--cache", type=Path,
                    default=Path("/private/tmp/aplus_fund_armR500.pkl"),
                    help="面板缓存（pickle）")
    ap.add_argument("--financial", type=Path,
                    default=ROOT / "data/reports/financial_annual_cache.parquet",
                    help="财务缓存（500 只年报）")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/growth_cashflow_arm_2026-10-21.json")
    ap.add_argument("--arm-name", default="armR500_growth_cashflow")
    ap.add_argument("--expect-panel-sha256", default=(
        "6cd463a520076cdc28e14427b61ef7ba10b2729d632f8c6c00004547a88e2979"),
        help="预注册 §3.4 钉死的面板指纹（**不许**换面板）")
    ap.add_argument("--n-a-equiv", type=int, default=None,
                    help="仪器对拍用的 n_A（**从 round B 产物读**，不手打；"
                         "对拍只验仪器不验判据）")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    """生成器入口。

    🚨 第 125 层：`expect_bytes` 必须是 `main()` 的**第一条可执行语句** ⇒
    生成器在跑的过程中被改过 ⇒ **拒绝盖章**（章说的就不是这次跑的字节）。
    """
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    args = parse_args(argv)

    if not args.cache.is_file():
        raise SystemExit(f"面板缓存不存在：{args.cache}")
    got_sha = _sha256_file(args.cache)
    if got_sha != args.expect_panel_sha256:
        raise SystemExit(
            f"面板指纹与预注册 §3.4 钉死的不一致：\n"
            f"  期望 {args.expect_panel_sha256}\n  实际 {got_sha}\n"
            f"⇒ 换了面板 ⇒ 与 round B **不是同一台仪器** ⇒ 拒绝跑"
            f"（对照臂 C2 的前提被破坏）")

    print(f"[1/4] 读产物锚（round B / 交付档 / D2 台账）…", flush=True)
    published = json.loads(
        (ROOT / "data/reports/fundamental_mechanism_armR_2026-10-21.json")
        .read_text(encoding="utf-8"))
    deployed_doc = json.loads(
        (ROOT / "data/reports/deployed_d2_verdict.json").read_text(encoding="utf-8"))
    cell = deployed_doc["k_grid"]["cells"]["LIVE_DEFAULT|ARM_A|K63|E100"]
    deployed = {"gross_annual_pct": cell["k_axis"]["gross_excess_annual_pct"]}
    req_doc = json.loads(
        (ROOT / "data/reports/d2_signal_requirement.json").read_text(encoding="utf-8"))
    r10 = next(r for r in req_doc["rows"] if r.get("n") == 10)
    requirement = {
        "percent": req_doc["headline"]["required_gross_at_50k_deliverable"],
        "fraction": r10["required_gross_at_50k"],
    }
    # 🚨 **倍数校准的整块**都传下去（不是只传同口径那一个数）⇒
    # `baseline_block` 自己挑 ⇒ 跨口径那一列**碰不到**（守卫能问「你挑的哪一列」）
    mc = req_doc["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]

    print(f"[2/4] 阈值（从产物读，**不手打**）…", flush=True)
    thr_all = _threshold_sets(ROOT / "data/reports")
    thr = {"k21": thr_all["primary"], "k63": thr_all["control"]}
    for k, t in thr.items():
        print(f"  K={k}: D1={t['d1_friction_annual_pct']:.6f} "
              f"D2={t['d2_required_gross_annual_pct']:.6f}", flush=True)

    print(f"[3/4] 跑臂 R 同款流程…", flush=True)
    if args.n_a_equiv is None:
        # 🚨 **不许手打**（第 131 层：手打的门槛与冻结的候选集脱钩）
        args.n_a_equiv = int(published["n_A"]["primary"]["n_A"])
    print(f"  仪器对拍 n_A = {args.n_a_equiv}"
          f"（从 round B 产物的 n_A.primary 读）", flush=True)
    arm = run_arm(args)
    arm["pit_check"] = pit_report(args, arm)

    print(f"[4/4] 判决 + 落盘…", flush=True)
    payload = build_payload(args, arm, thr, published, deployed, requirement, mc)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # 🚨🚨 **仪器不等 ⇒ 拒绝盖章**（不是「弱证据」，是「读数不可信」）：
    # 产物**已经落盘**（作为证据），但**没有 sidecar** ⇒ 按本仓规矩
    # **不可引用** ⇒ 没有人能拿它当测量结果。
    equiv = payload["instrument_equivalence"]
    print(f"  仪器逐位对拍：{equiv['bitwise_equal']}"
          f"（比了 {equiv['n_values_compared']} 个数）", flush=True)
    if not equiv["bitwise_equal"]:
        for row in equiv["rows"]:
            for d in row["diffs"][:5]:
                print(f"    K={row['k']} n={d['n']} {d['field']}: "
                      f"mine={d['mine']!r} ref={d['ref']!r}", flush=True)
        raise SystemExit(
            "🚨 仪器逐位对拍失败 ⇒ 本轮四个因子的读数**不可信** ⇒ "
            "**拒绝盖章**（产物已落盘但无 sidecar ⇒ 不可引用）")

    provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=expect_bytes)
    v = payload["verdict"]["primary"]
    print(f"  对照臂 bm 复现：{payload['control_bm_reproduction']['all_within_tolerance']}")
    for fam, s in v["by_family"].items():
        print(f"  {fam}: {s['state']}  {s['state_counts']}")
    if v["voided_by_negative_control"]:
        print(f"  ⚠️ 负控触发 ⇒ 整套判决作废：{v['voided_cells']}")
    print(f"  产物：{out}", flush=True)
    return 0


# ── PIT 报告（预注册 §3.2.4 / §3.2.5）──────────────────────────

def pit_report(args, arm: dict) -> dict:
    """PIT 断言 + 与 aplus 实现的逐日对拍（**独立第三路**）。

    ① **断言**：每个 (日,票) 用的报告必须 `NOTICE_DATE ≤ 当日`
       ⇒ 违规数必须 = 0，并报出间隔分布。
    ② **对拍**：本轮逐日选择 vs `aplus.factor_fundamental.annual_from_frame`
       在同一探针日上的结果 ⇒ 差异条数与成因**逐条报出**（**不藏**）。
    """
    active = arm["active"]
    assertion = pit_violations(active)

    probes = [d for d in arm["grid"]][:: max(1, len(arm["grid"]) // 8)][:9]
    cross = pit_cross_check_aplus(Path(args.financial), arm["spine"], probes)

    return {
        "assertion": assertion,
        "assertion_note": ("预注册 §3.2.4：**每个 (日,票) 用的报告必须 "
                           "`NOTICE_DATE ≤ 当日`** ⇒ 违规数必须 = 0；"
                           "间隔分布 = 当日 − 披露日（自然日）"),
        "aplus_cross_check": cross,
        "cross_check_note": ("预注册 §3.2.5：与 `aplus.factor_fundamental."
                             "annual_from_frame`（**独立实现**）在探针日上"
                             "逐票对拍 ⇒ 差异**逐条报出**，**不许藏**"),
        "verdict": ("PIT 断言通过" if assertion["violations"] == 0
                    else f"🚨 PIT 断言失败：{assertion['violations']} 条违规"),
    }


# 🚨🚨 `if __name__ == "__main__"` **必须在文件最末**：
# 第一版把它放在文件中部（在 `pit_report` 定义之前）⇒ 脚本一跑就
# `NameError: name 'pit_report' is not defined`
# ⇒ 而**按路径 import** 的测试**看不到**这个错（模块级不执行）
# ⇒ 「脚本跑不起来」与「函数是对的」在测试输出里长得**一模一样**
# （第 24 层：守卫测的是函数，脚本跑不起来）。
if __name__ == "__main__":
    raise SystemExit(main())
