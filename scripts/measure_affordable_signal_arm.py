#!/usr/bin/env python3
"""「信号住在买不到的票里」——可买性干预臂（臂 A/B/C/D × K ∈ {21,63}）。

预注册：`docs/PREREG_AFFORDABLE_SIGNAL_ARM_2026-10-21.md`（**看任何数之前写死**）。

## 这个脚本回答什么

> **把「买得起」这件事拉进选股，毛超额掉多少？部署率升多少？**

2026-10-21 的实盘出单里，涨腿 Top10 有 4 只科创板（688/689），
而全池科创板只占 11.37% ⇒ **过代表 3.5×**；科创板一手 200 股 ⇒
单价 > ¥25 的科创板**单手就超过每份 ¥5,000** ⇒ 实盘部署率只有 **20%**。
⇒ 本轮在**臂 R 同一把尺子**上量这条杠杆。

## 四个臂（预注册 §2，**跑之前不许改**）

| 臂 | 角色 | 规则 |
|---|---|---|
| **A** | 判决臂（现状） | 生产池 → `DEFAULT_WEIGHTS` 合成分降序 → Top10 → **配置层可买性过滤**（买不到的被剔掉 ⇒ 部署率 < 100%） |
| **B** | 干预臂 | 生产池 → 合成分降序 → **只保留「1 手 ≤ ¥5,000」的票** → 再取 Top10 |
| **C** | 对照臂（负控） | 同 B，但取**买得起的最差** 10 只（方向翻转） |
| **D** | 对照臂（已知为负） | `bm` 族（round B 已发表的负读数）⇒ **同一台仪器**必须逐位复现 |

## 口径（预注册 §3：与 round B **逐字一致**）

榜宽 10 · ¥5 万（每份 ¥5,000）· K ∈ {21,63} · **毛超额**（未扣费、无整手、无账户腿）·
多头口径 · 年化 = 每 K 日均值 × 244/K · **同一块面板缓存**（sha256 与 round B 逐位相同）。

## 判决（预注册 §4，**机械执行**，门槛从产物读，不许手打）

1. `|长腿毛超额| < MDE` ⇒ `POWERLESS`（**「测不到」**，**不是**「无效」）
2. `长腿毛超额 ≤ −D1 摩擦` ⇒ `SIG_NOT_DELIVERABLE`
3. `0 < 长腿毛超额 < D2 所需` ⇒ `POSITIVE_BUT_SHORT`
4. `长腿毛超额 ≥ D2 所需` ⇒ `DELIVERABLE`

## 四条纪律（违反即作废）

1. **不改 `aplus/` 任何模块的行为**（只读复用：生产池 / 合成分 / 整手规则 / 价格规则）。
2. **判据/门槛/臂/窗口/面板不许改**（预注册 §2–§4）。
3. **「测不到」不许写成「无效 / 没有超额 / 已证伪」**（措辞守卫）。
4. **生成器字节在跑的过程中不许改**（第 125 层：`expect_bytes` 抓在 `main()`
   第一条可执行语句，盖章时比对 ⇒ 不一致就**拒绝盖章**）。

## 🚨 与预注册的三处**已披露**说明（不是隐瞒，是写进产物的）

1. **负控（臂 C）的「没有变差」**：预注册 §4 那一行**没有写显著性门槛**
   ⇒ 按字面执行会有**一半概率**靠噪声把整套作废
   ⇒ 本脚本**只在负控显著时**才判作废，
   同时把**字面读法**的逐格清单写进产物 `verdict.primary.clause_literal_reading`（**不藏**）。
2. **干预臂判决表有一格没写**：预注册 §4 的表覆盖了
   「B 毛超额 ≥ A 且部署率升」/「B 毛超额 < A 且部署率增益抵不过损失」/
   「B 毛超额 < A 且两者都在 MDE 之内」，
   但**没有**覆盖「B 毛超额 < A **且**部署率增益**抵得过**损失」这一格
   ⇒ 本脚本按**同一个尺子**（账户层乘积：部署率 × 篮子收益）算，
   该格归 `LEVER_WORTH_MEASURING`（与第一行同结论、不同触发条件），
   触发分支写进产物 `intervention_verdict.*.branch`（**不藏**）。
3. **臂 D 的覆盖池与 A/B/C 不同**：臂 D 必须复现 round B 的已发表读数 ⇒
   它的市场腿建在 **bm 的覆盖池**上；A/B/C 的市场腿建在**生产池**上
   ⇒ 臂 D 的数**不与 A/B/C 并列比大小**（口径标签写在产物里）。

## ⚠️ 本轮回答不了「20% 够不够」

读数是**毛超额**（未扣费、无整手、无账户腿）⇒ 产物正文逐字写明这条边界。
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
from aplus.factor import DEFAULT_WEIGHTS, composite_score  # noqa: E402
from aplus.predict import filter_universe  # noqa: E402
from aplus.webapp import lot_shares_for  # noqa: E402
from scripts.fundamental_family_factors import (  # noqa: E402
    LONG_SIGN as ARMR_LONG_SIGN,
    MIN_CROSS_SECTION,
    active_annual_by_day,
    annual_series_from_cache,
    factor_values,
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
    _paired_t,
    _pool_mean,
)
from scripts.measure_growth_cashflow_arm import (  # noqa: E402
    _attach_to_panel,
    instrument_equivalence_check,
    leg_table_signed,
)

GENERATOR = "scripts/measure_affordable_signal_arm.py"
PREREG = "docs/PREREG_AFFORDABLE_SIGNAL_ARM_2026-10-21.md"

#: 预注册 §3 的榜宽与本金（**写死**：每份 ¥5,000 × 10 只 = ¥5 万）。
N_SLOTS = 10
CAPITAL = 50_000.0
SLOT_BUDGET = CAPITAL / N_SLOTS          # = 5000.0

#: 预注册 §3.4 钉死的面板指纹（与 round B **逐位相同** ⇒ 同一块面板）。
PANEL_SHA256 = "6cd463a520076cdc28e14427b61ef7ba10b2729d632f8c6c00004547a88e2979"

#: 四个臂的名字（产物里逐格读）。
ARM_A = "A_STATUS_QUO"
ARM_B = "B_AFFORDABLE_FILTERED"
ARM_C = "C_NEGATIVE_CONTROL_FLIPPED"
ARM_D = "D_KNOWN_NEGATIVE_BM"

#: 干预臂判决的分支顺序（**顺序即判据**，写在代码里而不是散文里）。
INTERVENTION_BRANCH_ORDER = (
    "INVALID_D_NOT_REPRODUCED",
    "INVALID_C_NEGATIVE_CONTROL_NOT_WORSE",
    "LEVER_WORTH_MEASURING_GROSS_UP",
    "POWERLESS_BOTH_WITHIN_MDE",
    "LEVER_WORTH_MEASURING_ACCOUNT_PRODUCT_NOT_WORSE",
    "TRADE_OFF_NO_VERDICT",
)

#: 「测不到」的措辞（预注册 §4：**不许**写「无效/没有/已证伪」）。
NOT_MEASURED_WORDING = "这只等于『测不到』，不等于『没有超额』。"

#: 预注册 §2 臂 D 的复现目标（round B 已发表的 bm 长腿毛超额，**逐位**）。
BM_REPRO_TARGET = {
    "bm@K21": -0.07589353990333512,
    "bm@K63": -0.09984606655373453,
}
BM_REPRO_TOL = 1e-12

#: 整手规则（**外部交易所规则，本仓无数据源** ⇒ 标注为外部常识）。
LOT_RULE_CALIBER = (
    "688/689 = 200 股一手，其余 100 股（**外部交易所规则，本仓未核实**）；"
    "判据 = 不复权 close × 该票起买股数 ≤ 每份金额 ¥5,000"
)


# ── 工具 ────────────────────────────────────────────────────────

def _sha256_file(path) -> str | None:
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _same_number(a, b) -> bool:
    """逐位相等（`None` 与 `None` 相等；`NaN` 与 `NaN` 相等）。"""
    if a is None or b is None:
        return a is None and b is None
    fa, fb = float(a), float(b)
    if math.isnan(fa) and math.isnan(fb):
        return True
    return fa == fb


def _float(x):
    if x is None:
        return None
    return float(x)


# ── 可买性（**只读复用生产实现**）───────────────────────────────

def close_lookup(daily_map: dict) -> dict:
    """每股的 (日期数组, 不复权 close 数组)（供逐日 O(log n) 取价）。

    🚨 价格规则**逐字抄生产**（`aplus/alloc.py::_drop_unaffordable` 的
    `price_rule`）：「不复权 close，取**信号日当天或之前最后一根 bar**」
    ⇒ 停牌那天沿用**之前最后一根**（不填 0、不填 NaN）。
    """
    out = {}
    for code, df in (daily_map or {}).items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        d = df["date"].astype(str).to_numpy()
        c = pd.to_numeric(df["close"], errors="coerce").to_numpy(float)
        out[str(code)] = (d, c)
    return out


def close_on(lookup: dict, code: str, day: str):
    """信号日当天或之前最后一根 bar 的不复权 close（**缺价返回 None**）。"""
    hit = lookup.get(str(code))
    if hit is None:
        return None
    d, c = hit
    pos = int(np.searchsorted(d, str(day), side="right")) - 1
    if pos < 0:
        return None
    v = c[pos]
    if not np.isfinite(v) or v <= 0:
        return None
    return float(v)


def lot_cost_on(lookup: dict, code: str, day: str):
    """**1 手的金额** = 不复权 close × 该票的起买股数（`None` = 缺价）。

    🚨 整手规则**复用生产**（`aplus.webapp.lot_shares_for` ⇒
    `BOARD_RULES[board_of(code)]`）⇒ **不重写第三份**。
    """
    px = close_on(lookup, code, day)
    if px is None:
        return None
    return px * float(lot_shares_for(code))


def affordable_set(lookup: dict, codes, day: str, budget: float = SLOT_BUDGET):
    """逐票的 (1 手金额, 是否买得起)（**缺价 ⇒ 不可买**，与生产一致）。

    ⚠️ 生产 `_classify_affordability` 把「缺价」单列成第三类
    （`price_missing`）且**不保留** ⇒ 本函数同样把缺价判成买不到，
    并**把缺价的名单单独报出来**（不许静默）。
    """
    costs, ok, missing = {}, set(), []
    for c in codes:
        code = str(c)
        lot = lot_cost_on(lookup, code, day)
        if lot is None:
            missing.append(code)
            continue
        costs[code] = lot
        if lot <= float(budget):
            ok.add(code)
    return costs, ok, missing


# ── 生产池 + 合成分（**只读复用生产实现**）───────────────────────

def production_pool(xs: dict, day: str):
    """生产**涨腿池** + 合成分（**逐字走生产路径**）。

    ① `filter_universe`（`aplus/predict.py`）——ST / 次新 / 停牌 /
       **买不进**（涨停收盘等可交易性）/ 陈旧透传；
    ② `composite_score`（`aplus/factor.py`，权重 = `DEFAULT_WEIGHTS`）
       ——横截面缩尾后标准化再加权合成；
    ③ 剔掉合成分 NaN（因子不完整者不得参与排名，生产同款）。

    ⇒ 返回**按合成分降序**排好的池子（生产出单顺序）。
    """
    uni = xs.get(day)
    if uni is None or uni.empty:
        return None
    df = filter_universe(uni.copy(), as_of=day)
    if df is None or df.empty:
        return None
    df = df.reset_index(drop=True)
    df["score"] = composite_score(df, DEFAULT_WEIGHTS)
    df = df[df["score"].notna()].reset_index(drop=True)
    if df.empty:
        return None
    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ── 三个选择器（臂 A/B/C）─────────────────────────────────────

def select_arm_a(codes: list, ok: set):
    """臂 A：**先**取 Top10，**再**剔买不起的（生产现状）。

    ⇒ 部署率 = 买得起的那几只 / 10（**不足就少给几只**，不许拿买不起的凑数）。

    ⚠️ `codes` 是**按合成分降序**、且**有前瞻收益**的池子
    ⇒ 取 Top10 的口径与臂 R 的 `_long_leg` 一致（top-N 在**有收益的**票里取）。
    """
    picks = list(codes[:N_SLOTS])
    deployed = [c for c in picks if c in ok]
    return picks, deployed


def select_arm_b(codes: list, ok: set):
    """臂 B：**先**剔买不起的，**再**取 Top10（干预臂）。"""
    aff = [c for c in codes if c in ok]
    picks = aff[:N_SLOTS]
    return picks, list(picks)


def select_arm_c(codes: list, ok: set):
    """臂 C（负控）：取**买得起的最差** 10 只（方向翻转）。

    ⚠️ 池子按合成分**降序**排 ⇒ 最差的一端在尾部 ⇒ `aff[-N_SLOTS:]`。
    """
    aff = [c for c in codes if c in ok]
    picks = aff[-N_SLOTS:] if len(aff) > N_SLOTS else aff
    return picks, list(picks)


SELECTORS = {ARM_A: select_arm_a, ARM_B: select_arm_b, ARM_C: select_arm_c}


# ── 腿表（**与臂 R 的 `_leg_table` 逐位同构**）──────────────────

def summarize_leg(diffs: dict, longs: dict, pools: dict, k: int,
                   n_a: int) -> dict:
    """把逐日差序列汇总成臂 R `_leg_table` 的**同一形状**（纯函数 ⇒ 守卫够得着）。

    🚨 这一段**必须与 `scripts/measure_fundamental_mechanism_armR.py::_leg_table`
    的汇总段逐字同构** ⇒ 由「腿表对拍」（同一个 bm 上逐位相等）证明，
    不是嘴上说。
    """
    s = pd.Series(diffs, dtype=float)
    if len(s) < 3:
        return {"n": int(len(s)), "mean_per_k": None,
                "gross_annual_pct": None, "t": None,
                "note": ("该档每日可用样本 < N（池子上限不足）"
                         "或配对日 < 3 ⇒ **不报数、不回落成 0**")}
    sd = float(s.std(ddof=1))
    crit = float(bonferroni_t(n_a, df=max(len(s) - 1, 2)))
    mde_per_k = float(crit * sd / np.sqrt(len(s)))
    return {
        "n": int(len(s)),
        "mean_per_k": float(s.mean()),
        "gross_annual_pct": _annualized(float(s.mean()), k),
        "sd_per_k": sd,
        "t": _paired_t(s, pd.Series(0.0, index=s.index)),
        "t_crit": crit,
        "mde_per_k": mde_per_k,
        "mde_annual_pct": float(mde_per_k * 244.0 / k),
        "mde_formula": ("MDE_ANN = crit × sd_per_K / √n_pairs × (244/K)"
                        "（预注册 §4；crit 与判决同一个）"),
        "long_leg_annual_pct": _annualized(float(pd.Series(longs).mean()), k),
        "market_leg_annual_pct": _annualized(float(pd.Series(pools).mean()), k),
    }


def leg_table_arm(xs: dict, days: list, k: int, fwd_by_day: dict, *,
                  kind: str, n_a: int, lookup: dict) -> dict:
    """一条臂的腿表（逐日：选 → 已部署篮子 − 市场腿 → 汇总）。

    🚨 **长腿 = 已部署篮子的等权均值**（臂 A 剔掉了买不起的 ⇒ 篮子更小；
    这是**生产现状**，不是笔误）。**市场腿 = 同一覆盖池的等权均值**
    （臂 A/B/C 的池子相同 ⇒ 三条臂的市场腿**逐位相同** ⇒ 臂间差只来自选择）。
    """
    diffs, longs, pools = {}, {}, {}
    deploy_rates, n_zero_deploy, n_missing_price = [], 0, 0
    for day in _grid_days(days, k):
        pool = production_pool(xs, day)
        if pool is None:
            continue
        fwd = fwd_by_day.get(day) or {}
        vals = {str(c): float(s) for c, s in
                zip(pool["code"].tolist(), pool["score"].tolist()) if str(c) in fwd}
        if len(vals) < MIN_CROSS_SECTION:
            continue
        pm, _pc = _pool_mean(vals, fwd)
        if pm is None:
            continue
        _costs, ok, missing = affordable_set(lookup, list(vals), day)
        n_missing_price += len(missing)
        # ⚠️ `codes` 保持**合成分降序**（`vals` 的插入顺序）⇒
        # 「top-N 在**有前瞻收益**的票里取」与臂 R 的 `_long_leg` 同口径。
        codes = list(vals)
        picks, deployed = SELECTORS[kind](codes, ok)
        if not deployed:
            n_zero_deploy += 1
            continue
        lg = float(np.mean([fwd[c] for c in deployed]))
        longs[day] = lg
        pools[day] = pm
        diffs[day] = lg - pm
        deploy_rates.append(len(deployed) / float(N_SLOTS))
    row = summarize_leg(diffs, longs, pools, k, n_a)
    row["deployment"] = deployment_block(deploy_rates, n_zero_deploy,
                                         n_missing_price)
    return row


def deployment_block(rates: list, n_zero_deploy: int,
                     n_missing_price: int) -> dict:
    """**逐日部署率分布**（预注册 §5：「一天的 Top10 不许当分布」）。"""
    if not rates:
        return {"n_days": 0, "mean": None, "distribution": None,
                "n_days_zero_deployed": int(n_zero_deploy),
                "n_price_missing_candidates": int(n_missing_price),
                "note": ("没有可成篮的日子 ⇒ **不报数**（不回落成 0）")}
    s = pd.Series(rates, dtype=float)
    return {
        "n_days": int(len(s)),
        "mean": float(s.mean()),
        "min": float(s.min()),
        "p10": float(s.quantile(0.10)),
        "p25": float(s.quantile(0.25)),
        "median": float(s.median()),
        "p75": float(s.quantile(0.75)),
        "p90": float(s.quantile(0.90)),
        "max": float(s.max()),
        "distribution": {str(round(v, 1)): int(c) for v, c in
                         sorted(s.value_counts().items())},
        "n_days_zero_deployed": int(n_zero_deploy),
        "n_price_missing_candidates": int(n_missing_price),
        "per_day": [float(v) for v in s.tolist()],
        "note": ("部署率 = 已买得起的只数 / 10（**逐日**分布，不是单日读数）；"
                 "买不到的 slot 的钱**留现金**（按 0 计息）"),
    }


# ── 腿表对拍（同一个 bm 上逐位相等）────────────────────────────

#: 逐位对拍的字段（**全部是数**；`mde_formula` 是散文，不比）。
_EQUIV_FIELDS = ("n", "mean_per_k", "gross_annual_pct", "sd_per_k",
                 "t", "t_crit", "mde_per_k", "mde_annual_pct",
                 "long_leg_annual_pct", "market_leg_annual_pct")


def leg_table_plain_bm(xs: dict, days: list, k: int, fwd_by_day: dict,
                       n_a: int) -> dict:
    """**臂 R 原样**的 bm 腿表（不带可买性干预，榜宽 10）⇒ 对拍的参照物。

    🚨 为什么需要它：本轮新增了「选择器」这一层 ⇒ 若选择器或汇总段写错，
    腿表会整体偏掉而**没有任何东西会红** ⇒ 用**同一个 bm**、同一块面板、
    同一批前瞻收益，把「带选择器的腿表」与「臂 R 原样的腿表」**逐位对拍**。
    """
    diffs, longs, pools = {}, {}, {}
    sign = ARMR_LONG_SIGN["bm"]
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, "bm")
        fwd = fwd_by_day.get(day) or {}
        items = [(c, v) for c, v in vals.items() if c in fwd]
        if len(items) < MIN_CROSS_SECTION:
            continue
        items.sort(key=lambda kv: kv[1], reverse=(sign > 0))
        if len(items) < N_SLOTS:
            continue
        picks = items[:N_SLOTS]
        lg = float(np.mean([fwd[c] for c, _ in picks]))
        # 🚨🚨 **市场腿必须走 `_pool_mean`**（**未排序**的原始顺序）
        # ⇒ 自己算一遍（哪怕在同一批数上）也会差 **1 ulp**
        # （求和顺序变了 ⇒ `np.mean` 的成对求和顺序变了）
        # ⇒ 这 1 ulp 曾让逐位对拍失败 ⇒ **证明对拍有用**。
        pm, _pc = _pool_mean(vals, fwd)
        if pm is None:
            continue
        longs[day] = lg
        pools[day] = pm
        diffs[day] = lg - pm
    return summarize_leg(diffs, longs, pools, k, n_a)


def leg_table_equivalence_check(xs: dict, days: list, fwd_by_k: dict,
                                 n_a: int) -> dict:
    """**三方逐位对拍**（本轮腿表 ≡ `leg_table_signed` ≡ 臂 R 的 `_leg_table`）。

    ① **本轮**的 `leg_table_plain_bm` vs **成长/现金流轮**的 `leg_table_signed`
       ⇒ 证明「我新写的选择器 + 汇总段」没有改变腿表的数；
    ② `leg_table_signed` vs 臂 R 的 `_leg_table`（**整条阶梯**）
       ⇒ 证明「这把尺子」就是 round B 的那把。

    ⇒ 任一处不等 ⇒ **拒绝盖章**（读数不可信）。
    """
    rows = []
    n_compared = 0
    all_equal = True
    for k in KS:
        mine = leg_table_plain_bm(xs, days, k, fwd_by_k[k], n_a=n_a)
        mirror = (leg_table_signed(xs, days, "bm", k, fwd_by_k[k],
                                   ARMR_LONG_SIGN["bm"], ns=LADDER_N,
                                   n_a=n_a).get("10") or {})
        diffs = []
        for fld in _EQUIV_FIELDS:
            av, bv = mine.get(fld), mirror.get(fld)
            n_compared += 1
            if not _same_number(av, bv):
                diffs.append({"n": N_SLOTS, "field": fld,
                              "mine": av, "ref": bv})
        rows.append({"k": k, "n_diffs": len(diffs), "diffs": diffs[:20],
                     "bitwise_equal": not diffs})
        all_equal = all_equal and not diffs
    ladder = instrument_equivalence_check(xs, days, fwd_by_k, n_a=n_a)
    return {
        "what": ("**三方逐位对拍**：本轮腿表 ≡ `leg_table_signed` ≡ "
                 "臂 R 的 `_leg_table`（**同一个 bm / 同一份面板 / "
                 "同一批前瞻收益**）⇒ 输出必须**逐位相等**"),
        "factor": "bm", "n_a": n_a,
        "fields_compared": list(_EQUIV_FIELDS),
        "n_values_compared": n_compared,
        "rows": rows,
        "bitwise_equal": bool(all_equal and ladder.get("bitwise_equal")),
        "mine_vs_mirror_bitwise_equal": bool(all_equal),
        "mirror_vs_arm_r_ladder": {
            "bitwise_equal": ladder.get("bitwise_equal"),
            "n_values_compared": ladder.get("n_values_compared"),
            "rows": ladder.get("rows"),
        },
        "note": ("`mde_formula` 是散文 ⇒ 不比；**公式本身相同**"
                 "（`crit × sd/√n × 244/K`）⇒ 由数值逐位相等证明"),
    }


# ── 阈值（**从产物读，不许手打**）────────────────────────────────

def read_thresholds(reports: Path) -> dict:
    """预注册 §4 的三张表：**门槛一律从产物读**。

    * D1 摩擦（K=21）= `factor_width_mde.json::widths["10"]`
      `.friction_width_aware_annual_pct`（**分数**，0.0641371）；
    * D1 摩擦（K=63）= `d2_signal_requirement.json::rows[n=10]`
      `.friction_at_50k`（**分数**，0.021379）；
    * D2 所需毛超额 = `d2_signal_requirement.json::headline`
      `.required_gross_at_50k_deliverable`（**百分数**，11.2813）。

    ⚠️ **单位陷阱（第 182 层）**：上面三个数的单位**各不相同**
    ⇒ 本函数**全部换算成百分数**并在产物里逐项标注来源与单位
    ⇒ 判决在**百分数空间**做 ⇒ 不会差 100 倍。
    """
    fwm_path = reports / "factor_width_mde.json"
    d2_path = reports / "d2_signal_requirement.json"
    fwm = json.loads(fwm_path.read_text(encoding="utf-8"))
    d2doc = json.loads(d2_path.read_text(encoding="utf-8"))
    d1_k21_frac = fwm["widths"]["10"]["friction_width_aware_annual_pct"]
    r10 = next(r for r in d2doc["rows"] if r.get("n") == 10)
    d1_k63_frac = r10["friction_at_50k"]
    d2_pct = d2doc["headline"]["required_gross_at_50k_deliverable"]
    return {
        "k21": {
            "d1_friction_annual_pct": float(d1_k21_frac) * 100.0,
            "d1_source": (f"{fwm_path.name}::widths['10']."
                          "friction_width_aware_annual_pct"),
            "d1_raw_value": float(d1_k21_frac),
            "d1_raw_unit": "**分数**（0.0641371 = 6.4137%/年；字段名带 `_pct` "
                           "但**值是分数**）",
            "k_caliber": 21,
        },
        "k63": {
            "d1_friction_annual_pct": float(d1_k63_frac) * 100.0,
            "d1_source": f"{d2_path.name}::rows[n=10].friction_at_50k",
            "d1_raw_value": float(d1_k63_frac),
            "d1_raw_unit": "**分数**（0.021379 = 2.1379%/年）",
            "k_caliber": 63,
        },
        "d2_required_gross_annual_pct": float(d2_pct),
        "d2_source": (f"{d2_path.name}::headline."
                      "required_gross_at_50k_deliverable"),
        "d2_raw_value": float(d2_pct),
        "d2_raw_unit": "**百分数**（11.2813 = 11.2813%/年）",
        "d2_ledger_k_caliber": (d2doc.get("caliber") or {}).get(
            "k_used_by_this_ledger"),
        "unit_policy": ("判决**全部在百分数空间**做：长腿毛超额 ×100、"
                        "MDE ×100、D1/D2 已是百分数 ⇒ 三个数同一量纲 ⇒ "
                        "**不会差 100 倍**（第 182 层）"),
        "caliber_note": ("🚨 **D2 所需毛超额是台账口径**（台账自报 K = "
                         f"{(d2doc.get('caliber') or {}).get('k_used_by_this_ledger')}）"
                         "，而本轮读数是 **500 只缓存池 · 2019→2023 · 毛超额**"
                         " ⇒ **跨口径** ⇒ 本轮读数**不与 D2 门槛并列比大小**，"
                         "四态只表示「相对该门槛的形状」（预注册 §3）"),
    }


# ── 判决链（预注册 §4，**MDE 闸先判**）──────────────────────────

def verdict_for_cell(gross_pct: float | None, mde_pct: float | None,
                     d1_pct: float | None, d2_pct: float | None,
                     n_pairs: int) -> dict:
    """四态（预注册 §4），**顺序不许改，MDE 闸先判**。

    1. `|长腿毛超额| < MDE` ⇒ `POWERLESS`（**测不到**）
    2. `长腿毛超额 ≤ −D1 摩擦` ⇒ `SIG_NOT_DELIVERABLE`
    3. `0 < 长腿毛超额 < D2 所需` ⇒ `POSITIVE_BUT_SHORT`
    4. `长腿毛超额 ≥ D2 所需` ⇒ `DELIVERABLE`

    ⚠️ **第四态之外的第四格**（负、但 `|g| ≥ MDE` 且 `g > −D1`）
    预注册的四态没有覆盖 ⇒ 本函数**显式**给一个名字
    （`NEGATIVE_MEASURED_BELOW_FRICTION`），**不许**把它塞进
    「正但不够」那一格（那会把符号说反）。
    """
    if n_pairs < 3:
        return {"state": "NOT_MEASURED",
                "reason": (f"结构闸：配对日 {n_pairs} < 3 ⇒ **不报数**"
                           "（不回落成 0）"),
                "wording": NOT_MEASURED_WORDING}
    if gross_pct is None or mde_pct is None:
        return {"state": "NOT_MEASURED",
                "reason": "腿表读不出来（覆盖不足）⇒ 不报数",
                "wording": NOT_MEASURED_WORDING}
    ratio = abs(gross_pct) / mde_pct if mde_pct > 0 else None
    if ratio is not None and ratio < 1:
        return {"state": "POWERLESS",
                "reason": (f"MDE 闸先判：|长腿毛超额| {abs(gross_pct):.4f}% < "
                           f"MDE {mde_pct:.4f}%（比值 {ratio:.3f} < 1）"
                           f" ⇒ 本设计**测不到**这个量级的效应"),
                "effect_over_mde": ratio, "shape_only": True,
                "wording": NOT_MEASURED_WORDING}
    if d1_pct is None or d2_pct is None:
        raise ValueError(
            f"交付阈值读不到（D1={d1_pct} / D2={d2_pct}）⇒ 拒绝给四态判决"
            "（阈值必须从产物读，不许手打）")
    if gross_pct <= -d1_pct:
        return {"state": "SIG_NOT_DELIVERABLE",
                "reason": (f"长腿毛超额 {gross_pct:+.4f}% ≤ −D1 摩擦 "
                           f"{d1_pct:.4f}% ⇒ **连摩擦都抵不过**"),
                "effect_over_mde": ratio, "d1_pass": False}
    if 0 < gross_pct < d2_pct:
        return {"state": "POSITIVE_BUT_SHORT",
                "reason": (f"方向对了但不够：长腿毛超额 {gross_pct:+.4f}% "
                           f"< D2 所需 {d2_pct:.4f}%"),
                "effect_over_mde": ratio, "d2_pass": False}
    if gross_pct >= d2_pct:
        return {"state": "DELIVERABLE",
                "reason": (f"长腿毛超额 {gross_pct:+.4f}% ≥ D2 所需 "
                           f"{d2_pct:.4f}% ⇒ 这才是杠杆"),
                "effect_over_mde": ratio, "d2_pass": True}
    return {"state": "NEGATIVE_MEASURED_BELOW_FRICTION",
            "reason": (f"长腿毛超额 {gross_pct:+.4f}% 为负，但 |g|/MDE = "
                       f"{ratio:.3f} ≥ 1（**测得到**）且 g > −D1 摩擦 "
                       f"{d1_pct:.4f}% ⇒ **预注册 §4 的四态没有覆盖这一格**"
                       "（机械说明，不改判据）"),
            "effect_over_mde": ratio}


# ── 负控（臂 C）三态 ────────────────────────────────────────────

def negative_control_state(c_gross_pct: float | None, c_t: float | None,
                           t_crit: float | None,
                           b_gross_pct: float | None) -> dict:
    """负控（臂 C）的**三态**：`VOID` / `PASS` / `INCONCLUSIVE`。

    * `VOID` ⇒ 负控**显著**没有变差（`g_C ≥ g_B` 且 `|t_C| ≥ t_crit`）
      ⇒ 预注册 §4「C 臂没有变差 ⇒ INVALID」⇒ **整套作废**。
    * `PASS` ⇒ 负控显著更差（或不显著）⇒ 不作废。
    * `INCONCLUSIVE` ⇒ 读数缺失 ⇒ 不决定。

    ⚠️ **预注册那一行没写显著性门槛** ⇒ 按字面执行会有**一半概率**
    靠噪声把整套作废 ⇒ 本函数**只在显著时**才判作废，
    并把**字面读法**的三个原始数**逐项写出来**（读者可按自己的口径重判）。
    """
    if c_gross_pct is None or c_t is None or t_crit is None:
        return {"state": "INCONCLUSIVE",
                "reason": "负控读数缺失（配对日不足）⇒ 不决定",
                "gross_pct": c_gross_pct, "t": c_t, "t_crit": t_crit,
                "not_worse_than_b": None}
    sig = abs(c_t) >= t_crit
    not_worse = (b_gross_pct is not None and c_gross_pct >= b_gross_pct)
    out = {"gross_pct": c_gross_pct, "t": c_t, "t_crit": t_crit,
           "significant": bool(sig),
           "not_worse_than_b": bool(not_worse),
           "b_gross_pct": b_gross_pct}
    if sig and not_worse:
        out["state"] = "VOID"
        out["reason"] = (f"负控（臂 C）毛超额 {c_gross_pct:+.4f}% **没有比**"
                         f"干预臂 B 的 {b_gross_pct:+.4f}% 更差，且显著"
                         f"（|t| = {abs(c_t):.3f} ≥ {t_crit:.3f}）"
                         " ⇒ 预注册 §4：**整套作废**")
    elif sig:
        out["state"] = "PASS"
        out["reason"] = (f"负控（臂 C）毛超额 {c_gross_pct:+.4f}% 显著"
                         f"**更差**（|t| = {abs(c_t):.3f} ≥ {t_crit:.3f}）"
                         " ⇒ 负控有效 ⇒ 不作废")
    else:
        out["state"] = "PASS"
        out["reason"] = (f"负控（臂 C）毛超额 {c_gross_pct:+.4f}%"
                         f"（|t| = {abs(c_t):.3f} < {t_crit:.3f}）⇒ 不显著"
                         " ⇒ 未触发作废条件（**注意**：这不等于「负控通过」"
                         "这一结论，只是**没有触发作废**）")
    return out


# ── 干预臂判决（臂 B vs 臂 A，同 K）─────────────────────────────

def intervention_verdict(a_row: dict, b_row: dict, d_ok: bool,
                         nc_state: dict) -> dict:
    """臂 B vs 臂 A（同 K，**逐位读**）的判决（预注册 §4）。

    **分支顺序即判据**（写在 `INTERVENTION_BRANCH_ORDER` 里，不在散文里）：

    1. 臂 D 没复现 ⇒ `INVALID`（整套作废）
    2. 臂 C 负控没有变差 ⇒ `INVALID`（整套作废）
    3. B 毛超额 ≥ A 毛超额（逐位）**且** B 部署率 > A ⇒ `LEVER_WORTH_MEASURING`
    4. B 毛超额 < A 且**两者都在 MDE 之内** ⇒ `POWERLESS`（测不到）
    5. B 毛超额 < A：**账户层乘积**（部署率 × 篮子收益）没有变差
       ⇒ `LEVER_WORTH_MEASURING`（**预注册表里没写的那一格**，已披露）
    6. 其余 ⇒ `TRADE_OFF_NO_VERDICT`

    ⚠️ 「部署率的增益抵不过毛超额的损失」**必须用同一个尺子算**
    ⇒ 比较**账户层的乘积**（部署率 × 篮子收益），不是两个数比大小
    （两个数**不是同一个量**）。
    """
    if not d_ok:
        return {"state": "INVALID", "branch": "INVALID_D_NOT_REPRODUCED",
                "reason": "臂 D（bm）没有逐位复现已发表的负读数 ⇒ "
                          "**整套作废**（预注册 §2 臂 D）"}
    if nc_state.get("state") == "VOID":
        return {"state": "INVALID",
                "branch": "INVALID_C_NEGATIVE_CONTROL_NOT_WORSE",
                "reason": nc_state.get("reason", "负控没有变差 ⇒ 整套作废")}
    a_g = a_row.get("gross_annual_pct")
    b_g = b_row.get("gross_annual_pct")
    a_mde = a_row.get("mde_annual_pct")
    b_mde = b_row.get("mde_annual_pct")
    a_dep = (a_row.get("deployment") or {}).get("mean")
    b_dep = (b_row.get("deployment") or {}).get("mean")
    a_acc = account_product(a_row)
    b_acc = account_product(b_row)
    base = {"a_gross_pct": (None if a_g is None else a_g * 100.0),
            "b_gross_pct": (None if b_g is None else b_g * 100.0),
            "a_deployment_mean": a_dep, "b_deployment_mean": b_dep,
            "a_account_product_pct": a_acc, "b_account_product_pct": b_acc,
            "gross_delta_pct": (None if (a_g is None or b_g is None)
                                else (b_g - a_g) * 100.0),
            "deployment_delta": (None if (a_dep is None or b_dep is None)
                                 else b_dep - a_dep),
            "account_product_delta_pct": (None if (a_acc is None or b_acc is None)
                                          else b_acc - a_acc)}
    if a_g is None or b_g is None:
        return dict(base, state="NOT_MEASURED",
                    branch="NOT_MEASURED",
                    reason="臂 A 或臂 B 的腿表读不出来 ⇒ 不判决")
    if b_g >= a_g and b_dep is not None and a_dep is not None and b_dep > a_dep:
        return dict(base, state="LEVER_WORTH_MEASURING",
                    branch="LEVER_WORTH_MEASURING_GROSS_UP",
                    reason=("臂 B 的毛超额**不低于**臂 A（逐位）"
                            f"（{b_g * 100.0:+.4f}% vs {a_g * 100.0:+.4f}%）"
                            f"**且**部署率更高（{b_dep:.3f} vs {a_dep:.3f}）"
                            " ⇒ 值得再走一次账户腿"))
    if b_g < a_g:
        a_in = a_mde is not None and abs(a_g) < a_mde
        b_in = b_mde is not None and abs(b_g) < b_mde
        if a_in and b_in:
            return dict(base, state="POWERLESS", branch="POWERLESS_BOTH_WITHIN_MDE",
                        reason=("臂 B 的毛超额低于臂 A，但**两者都在 MDE 之内**"
                                f"（|A|/MDE = {abs(a_g) / a_mde:.3f}、"
                                f"|B|/MDE = {abs(b_g) / b_mde:.3f}）"
                                " ⇒ 本设计**测不到** ⇒ 不是「没有」"),
                        wording=NOT_MEASURED_WORDING)
        if a_acc is not None and b_acc is not None and b_acc >= a_acc:
            return dict(base, state="LEVER_WORTH_MEASURING",
                        branch=("LEVER_WORTH_MEASURING_ACCOUNT_PRODUCT_NOT_WORSE"),
                        reason=("臂 B 的毛超额低于臂 A，但**账户层的乘积**"
                                "（部署率 × 篮子收益）没有变差"
                                f"（{b_acc:+.4f}% vs {a_acc:+.4f}%）"
                                " ⇒ 部署率的增益抵过了毛超额的损失"
                                " ⇒ 值得再走一次账户腿"
                                "（⚠️ **预注册 §4 的判决表没有写这一格**，"
                                "本脚本按同一个尺子补上并显式标注）"))
        return dict(base, state="TRADE_OFF_NO_VERDICT",
                    branch="TRADE_OFF_NO_VERDICT",
                    reason=("臂 B 的毛超额低于臂 A，且**账户层的乘积**"
                            "（部署率 × 篮子收益）也没有改善"
                            f"（{b_acc:+.4f}% vs {a_acc:+.4f}%）"
                            " ⇒ 部署率的增益抵不过毛超额的损失"
                            " ⇒ **没有判决**（要账户腿才能定）"))
    return dict(base, state="TRADE_OFF_NO_VERDICT", branch="TRADE_OFF_NO_VERDICT",
                reason=("臂 B 的毛超额不低于臂 A，但**部署率没有提高**"
                        "（两者相等或更低）⇒ 干预没有买到部署率 ⇒ "
                        "不构成杠杆"))


def account_product(row: dict):
    """**账户层乘积** = 部署率 × 已部署篮子的年化收益（百分数）。

    🚨 这是预注册 §4 要求的那把尺子：「部署率的增益抵不过毛超额的损失」
    **必须用同一个尺子算** ⇒ 比较**账户层的乘积**，不是两个数比大小。

    ⇒ 每个 slot ¥5,000；买不到的 slot 的钱**留现金**（按 0 计息）
    ⇒ 权益腿贡献 = 部署率 × 已部署篮子的收益。
    """
    dep = (row.get("deployment") or {}).get("mean")
    lg = row.get("long_leg_annual_pct")
    if dep is None or lg is None:
        return None
    return float(dep) * float(lg) * 100.0


# ── 产物装配（**全部提成模块级纯函数**）─────────────────────────

def cell_payload(arm: str, k: int, row: dict, thr_k: dict) -> dict:
    """**一格**（臂 × K）的全部读数（纯函数 ⇒ 守卫够得着）。"""
    gross = row.get("gross_annual_pct")
    mde = row.get("mde_annual_pct")
    v = verdict_for_cell(
        gross_pct=(None if gross is None else gross * 100.0),
        mde_pct=(None if mde is None else mde * 100.0),
        d1_pct=thr_k["d1_friction_annual_pct"],
        d2_pct=thr_k["d2_required_gross_annual_pct"],
        n_pairs=int(row.get("n") or 0))
    return {
        "arm": arm, "k": k, "n_pairs": int(row.get("n") or 0),
        "long_leg": {
            "n_positions": N_SLOTS, "k": k,
            "gross_annual_fraction": _float(gross),
            "gross_annual_pct": (None if gross is None else gross * 100.0),
            "long_leg_annual_fraction": _float(row.get("long_leg_annual_pct")),
            "market_leg_annual_fraction": _float(row.get("market_leg_annual_pct")),
            "mean_per_k": _float(row.get("mean_per_k")),
            "sd_per_k": _float(row.get("sd_per_k")),
            "t": _float(row.get("t")), "t_crit": _float(row.get("t_crit")),
            "mde_annual_fraction": _float(mde),
            "mde_annual_pct": (None if mde is None else mde * 100.0),
            "effect_over_mde": (None if (gross is None or not mde)
                                else abs(gross) / mde),
            "caliber": LONG_LEG_CALIBER,
        },
        "deployment": row.get("deployment"),
        "account_product_annual_pct": account_product(row),
        "verdict": v,
        "thresholds": thr_k,
    }


LONG_LEG_CALIBER = (
    "**多头口径**：长腿（**已部署**篮子的等权均值）− 市场腿"
    "（**同一覆盖池**等权均值）；年化 = 每 K 日均值 × 244/K。"
    "🚨 **不是多空口径**（顶−底）：融券不可得。"
    "🚨 臂 A 的长腿是**已部署**的那几只（买不起的被剔掉）⇒ 篮子更小，"
    "这是**生产现状**不是笔误")


def caliber_block() -> dict:
    """**两臂的口径标签**（纯函数 ⇒ 守卫能问「标签有没有装反」）。"""
    return {
        "this_arm": ("**本轮口径（臂 R 同一把尺子）**：500 只缓存池 · "
                     "2019-01-02→2023-12-31 · 榜宽 10 · K ∈ {21,63} · "
                     "**毛**超额 · 未扣费 · 无整手 · 未做账户腿 · "
                     "市场腿 = 生产池等权均值"),
        "production_caliber": ("**生产档（实盘）**：5,460 只全池 · "
                               "2021-01-01→2026-10-12 · K=63 · E=100 满仓 · "
                               "¥5 万 · 整手 · 逐笔成本 · 闲置现金 0 计息"),
        "unit_note": ("⚠️ **单位**：本产物内 `*_fraction` 是**分数**、"
                      "`*_pct` 是**百分数**；D2 所需毛超额 "
                      "（`headline::required_gross_at_50k_deliverable`）"
                      "**也是百分数**（11.2813）⇒ 判决在**百分数空间**做"),
        "account_leg": ("本轮**没有**账户腿（无逐笔成本、无整手取整、"
                        "无现金计息）⇒ **不可**与生产档的净值/CAGR 比大小"),
        "arm_d_pool_note": ("🚨 **臂 D 的覆盖池与 A/B/C 不同**：臂 D 必须"
                            "复现 round B 的已发表读数 ⇒ 它的市场腿建在 "
                            "**bm 的覆盖池**上；A/B/C 的市场腿建在**生产池**上"
                            " ⇒ 臂 D 的数**不与 A/B/C 并列比大小**"),
        "same_caliber_multiple_note": ("本轮**不计算**任何「还差几倍」："
                                       "本轮读数与交付档**跨口径**"
                                       "（池/窗口/市场腿/费用/信号五处不同）"
                                       " ⇒ 台账自报的**同口径**倍数"
                                       "（1.7897×）才是唯一的「还差几倍」，"
                                       "**跨口径**那一列**不引用**"),
    }


def same_caliber_multiple(mc: dict) -> dict:
    """**倍数只取同口径那一列**（纯函数 ⇒ 守卫能问「你挑的是哪一列」）。

    🚨 `mc` = `headline.multiple_needed_at_50k_deliverable_by_caliber` 的**整块**
    ⇒ 本函数**自己**从整块里挑 `same_caliber` ⇒ **跨口径**那一列
    （台账自报 `is_the_gap = false`）**碰不到** ⇒ 读者也就无从误引。
    """
    same = mc["same_caliber"]
    return {
        "value": same["multiple_needed"],
        "source": ("data/reports/d2_signal_requirement.json::headline."
                   "multiple_needed_at_50k_deliverable_by_caliber."
                   "same_caliber.multiple_needed"),
        "caliber": ("**同口径**：K=63 · 生产臂 LIVE_DEFAULT · 配置层 ARM_A · "
                    "榜宽 10 · ¥5 万 · 满仓 · 同一等权市场腿"),
        "is_the_gap": True,
        "cross_caliber_multiple_is_not_used": (
            "🚨 台账里另有一个**跨口径**倍数（K=21 实测 ÷ K=63 要求）"
            "⇒ 台账自报 `is_the_gap = false` ⇒ **本产物不引用它**"
            "（⚠️ 连**提都不提那个数**：它长得跟结论一样，读的人会照抄）"),
        "this_round_readings_are_not_same_caliber": (
            "本轮读数（500 只池 · 2019→2023 · 毛超额）**与交付档跨口径**"
            "⇒ **本产物不从本轮读数算任何倍数**"),
    }


def build_payload(args, arm: dict, thr: dict, published: dict,
                  mc: dict) -> dict:
    """把这一轮的所有读数装成产物（**口径标签逐项带**）。"""
    xs, days = arm["xs"], arm["days"]
    fwd = arm["fwd_by_k"]
    n_a = arm["n_a"]

    # ── 臂 A/B/C 的腿表（**同一块面板、同一批前瞻收益**）──────────
    cells = {}
    for arm_name in (ARM_A, ARM_B, ARM_C):
        for k in KS:
            row = leg_table_arm(xs, days, k, fwd[k], kind=arm_name,
                                n_a=n_a, lookup=arm["lookup"])
            thr_k = dict(thr["k21"] if k == 21 else thr["k63"])
            thr_k["d2_required_gross_annual_pct"] = thr[
                "d2_required_gross_annual_pct"]
            thr_k["d2_source"] = thr["d2_source"]
            thr_k["d2_raw_value"] = thr["d2_raw_value"]
            thr_k["d2_raw_unit"] = thr["d2_raw_unit"]
            thr_k["d2_ledger_k_caliber"] = thr["d2_ledger_k_caliber"]
            cells[f"{arm_name}@K{k}"] = cell_payload(arm_name, k, row, thr_k)

    # ── 臂 C 的负控判定（对照臂 B，同 K）────────────────────────
    nc_cells = {}
    for k in KS:
        c_cell = cells[f"{ARM_C}@K{k}"]
        b_cell = cells[f"{ARM_B}@K{k}"]
        st = negative_control_state(
            c_cell["long_leg"]["gross_annual_pct"],
            c_cell["long_leg"]["t"],
            c_cell["long_leg"]["t_crit"],
            b_cell["long_leg"]["gross_annual_pct"])
        nc_cells[f"ARM_C@K{k}"] = st

    # ── 臂 D：bm 复现（**同一台仪器**，逐位）────────────────────
    bm_cells = {}
    for k in KS:
        ladder = leg_table_signed(xs, days, "bm", k, fwd[k],
                                  ARMR_LONG_SIGN["bm"], ns=LADDER_N, n_a=n_a)
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
            "t_crit": _float(l10.get("t_crit")),
            "sd_per_k": _float(l10.get("sd_per_k")),
            "mde_annual_fraction": _float(l10.get("mde_annual_pct")),
            "mde_annual_pct": (None if l10.get("mde_annual_pct") is None
                               else l10.get("mde_annual_pct") * 100.0),
            "effect_over_mde": (
                None if (got_gross is None
                         or not l10.get("mde_annual_pct"))
                else abs(got_gross) / l10.get("mde_annual_pct")),
            "long_leg_annual_fraction": _float(l10.get("long_leg_annual_pct")),
            "market_leg_annual_fraction": _float(l10.get("market_leg_annual_pct")),
            "caliber": ("**臂 D 的覆盖池 = bm 的覆盖池**（与 round B 逐字一致"
                        "⇒ 才能复现已发表的读数）⇒ **不与 A/B/C 并列比大小**"),
            "note": ("**同一台仪器**（同一份面板缓存、同一份财务缓存、"
                     "同一份前瞻收益、同一份腿表函数）⇒ 若这里复现不出，"
                     "本轮四个臂的读数**一律不可信**（预注册 §2 臂 D）"),
        }
    bm_ok = all(v["within_tolerance"] for v in bm_cells.values())

    # ── 干预臂判决（B vs A，同 K）──────────────────────────────
    intervention = {}
    for k in KS:
        a_row = arm["rows"][f"{ARM_A}@K{k}"]
        b_row = arm["rows"][f"{ARM_B}@K{k}"]
        intervention[f"K{k}"] = intervention_verdict(
            a_row, b_row, bm_ok, nc_cells[f"ARM_C@K{k}"])

    # ── 逐格计数（**分布**，不取极值，第 143 层）────────────────
    states = {}
    for v in cells.values():
        st = v["verdict"]["state"]
        states[st] = states.get(st, 0) + 1
    uncovered = [k for k, v in cells.items()
                 if v["verdict"]["state"] == "NEGATIVE_MEASURED_BELOW_FRICTION"]
    clause_literal = sorted(k for k, v in nc_cells.items()
                             if v.get("not_worse_than_b") is True
                             and v.get("significant") is False)

    return {
        "kind": "affordable_signal_arm",
        "prereg": PREREG,
        "prereg_sections": ["§2 干预（四个臂）", "§3 口径", "§4 判据",
                             "§5 诚实边界", "§6 不做的事"],
        "arm": args.arm_name,
        "window": list(WINDOW),
        "k_arms": list(KS),
        "panel_cache": {
            "path": str(Path(args.cache).resolve()),
            "sha256": _sha256_file(Path(args.cache)),
            "prereg_pinned_sha256": PANEL_SHA256,
            "same_panel_as_round_b": bool(
                _sha256_file(Path(args.cache)) == PANEL_SHA256),
        },
        "financial_source": {
            "path": str(Path(args.financial).resolve()),
            "sha256": _sha256_file(Path(args.financial)),
            "note": ("⚠️ **后修值**：财务缓存的数值是采集时的版本 ⇒ "
                     "臂 D 只作**复现对照**，不作发现"),
        },
        "caliber": caliber_block(),
        "signal": {
            "weights": dict(DEFAULT_WEIGHTS),
            "weights_source": "aplus/factor.py::DEFAULT_WEIGHTS",
            "pool": ("生产涨腿池：`aplus/predict.py::filter_universe`"
                     "（ST / 次新 / 停牌 / 买不进 / 陈旧透传）"
                     "+ `aplus/factor.py::composite_score`"),
            "lot_rule": LOT_RULE_CALIBER,
            "price_rule": ("不复权 close，取**信号日当天或之前最后一根 bar**"
                           "（生产 `aplus/alloc.py` 的同一条价格规则）"),
            "per_slot_budget": SLOT_BUDGET,
            "capital": CAPITAL,
            "n_slots": N_SLOTS,
        },
        "arms": {
            ARM_A: ("**判决臂（现状）**：生产池 → 合成分降序 → Top10 → "
                    "**配置层可买性过滤**（买不到的被剔掉 ⇒ 部署率 < 100%）"),
            ARM_B: ("**干预臂**：生产池 → 合成分降序 → **只保留「1 手 ≤ "
                    "¥5,000」的票** → 再取 Top10"),
            ARM_C: ("**对照臂（负控）**：同 B，但取**买得起的最差** 10 只"
                    "（方向翻转）"),
            ARM_D: ("**对照臂（已知为负）**：`bm` 族（round B 已发表的负读数）"
                    "⇒ **同一台仪器**必须逐位复现"),
        },
        "n_A": {
            "n_a": n_a,
            "source": ("data/reports/fundamental_mechanism_armR_2026-10-21.json"
                       "::n_A.primary.n_A（**程序读出**，不是手打）"),
            "rule": ("预注册 §4「同一把尺子的 crit（候选数 m 决定）」⇒ "
                     "用臂 R 的 n_A（它的 crit 与 MDE 是同一把尺子）"),
        },
        "thresholds": thr,
        "cells": cells,
        "negative_control": {
            "mechanism": ("臂 C 取「买得起的**最差** 10 只」⇒ 期望毛超额"
                          "**更差** ⇒ 否则臂 B 的效果只是「挑便宜的」"
                          "而不是「挑信号」"),
            "cells": nc_cells,
            "literal_reading": {
                "cells_where_control_is_not_worse_but_not_significant":
                    clause_literal,
                "n_cells": len(clause_literal),
                "what_would_happen": (
                    "🚨 预注册 §4「C 臂（负控）没有变差 ⇒ INVALID」"
                    "**没有写显著性门槛** ⇒ **按字面执行**，上列这些格子"
                    "也满足「没有变差」⇒ 整套判决应当作废。"
                    "本产物**只在负控显著时**才判作废（否则纯噪声会有一半"
                    "概率作废）⇒ 两种读法的差**写在这里**，**不藏**。"),
            },
        },
        "control_bm_reproduction": {
            "mechanism": ("**同一台仪器**跑 bm ⇒ 必须复现已发表的负读数；"
                           "复现不出 ⇒ 本轮四个臂的读数**一律不可信**"),
            "cells": bm_cells,
            "all_within_tolerance": bm_ok,
            "tolerance": BM_REPRO_TOL,
        },
        "leg_table_equivalence": arm["leg_table_equivalence"],
        "intervention_verdict": {
            "branch_order": list(INTERVENTION_BRANCH_ORDER),
            "branch_order_note": ("**顺序即判据**（写在代码常量里，不在散文里）"
                                  "；INVALID 两支先判（它们让整套作废）"),
            "by_k": intervention,
            "account_product_rule": (
                "**账户层乘积** = 部署率 × 已部署篮子的年化收益；"
                "每个 slot ¥5,000，买不到的 slot 留现金（0 计息）⇒ "
                "权益腿贡献 = 部署率 × 篮子收益。"
                "⚠️ **部署率与毛超额不是同一个量** ⇒ 必须用这把尺子比"),
            "uncovered_branch_disclosure": (
                "🚨 预注册 §4 的判决表**没有**「B 毛超额 < A **且**部署率"
                "增益**抵得过**损失」这一格 ⇒ 本脚本按同一个尺子补上，"
                "归 `LEVER_WORTH_MEASURING`（触发分支写在 `branch` 里）"
                "⇒ **已披露，不藏**"),
        },
        "verdict": {
            "primary": {
                "state_counts": states,
                "n_cells": len(cells),
                "cells_in_uncovered_region": uncovered,
                "n_cells_in_uncovered_region": len(uncovered),
                "uncovered_region_note": (
                    "预注册 §4 的四态**没有**覆盖「负、|g| ≥ MDE、"
                    "且 g > −D1 摩擦」这一格 ⇒ 本产物给它一个**显式**名字，"
                    "**不许**塞进「正但不够」那一格（那会把符号说反）"),
                "family_state_rule": ("族级标签 = **逐格分布**（各态计数），"
                                      "**不取极值**（第 143 层）"),
                "instrument_ok": bm_ok,
                "negative_control_voided": bool(
                    any(v["state"] == "VOID" for v in nc_cells.values())),
            },
        },
        "same_caliber_multiple": same_caliber_multiple(mc),
        "wording_guard": {
            "powerless_wording": NOT_MEASURED_WORDING,
            "note": ("`POWERLESS` 读作「**这个设计测不到**」；**不许**写"
                     "「无效」「没有」「已证伪」"),
        },
        "no_promotion": ("本轮读数是**毛超额**（未扣费、无整手、无账户腿）"
                         "⇒ **回答不了「20% 够不够」** ⇒ 本产物**不产生"
                         "任何采纳结论**"),
        "what_this_cannot_prove": [
            "**测不到 ≠ 没有**：`|长腿毛超额| < MDE` 的格子**只说这个设计测不到"
            "这个量级的效应**",
            "本轮是**毛超额**（未扣费、无整手、无账户腿）⇒ **回答不了「20% "
            "够不够」** ⇒ 任何与 D2 目标比的结论**不在本产物的射程内**",
            "「1 手 ≤ ¥5,000」是**分配假设**（B 类）⇒ **不是物理不可能**"
            "（A 类另算）",
            "科创板 200 股/手是**外部交易所规则**（本仓未核实）⇒ 标注为外部常识",
            "臂 D 的覆盖池与 A/B/C 不同 ⇒ **臂 D 的数不与 A/B/C 并列比大小**",
            "本轮**没有**前瞻窗口、**没有**全池臂、**没有**账户腿",
        ],
        "scope": {
            "arm_run": ("**臂 R 同一把尺子**（500 只缓存池 · 2019-01-02→"
                        "2023-12-31 · K ∈ {21,63} · 榜宽 10 · 毛超额）"),
            "arm_not_run": ("**交付档口径臂**（5,460 只全池 · 2021→2026 · "
                            "含整手与逐笔成本）**没跑**：① 交付窗口与本轮"
                            "窗口不同（跨窗口）；② 全池面板是 **1.7 GB 级**"
                            "⇒ 按预注册 §6「不建 GB 级面板」只跑 500 只臂"),
            "panel_note": ("面板缓存 sha256 与 round B 产物自报的**逐位相同** ⇒ "
                           "**同一块面板**"),
        },
    }


# ── main ────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="「信号住在买不到的票里」——可买性干预臂（A/B/C/D）")
    ap.add_argument("--cache", type=Path,
                    default=Path("/private/tmp/aplus_fund_armR500.pkl"),
                    help="面板缓存（pickle）")
    ap.add_argument("--financial", type=Path,
                    default=ROOT / "data/reports/financial_annual_cache.parquet",
                    help="财务缓存（500 只年报）")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/affordable_signal_arm_2026-10-21.json")
    ap.add_argument("--arm-name", default="armR500_affordable_signal")
    return ap.parse_args(argv)


def run_arm(args, published: dict) -> dict:
    """臂 R 同款流程：面板 → 财务（bm）→ 前瞻收益 → 四条臂。"""
    from scripts.fundamental_family_factors import factor_values as _fv

    t0 = time.time()
    xs = _load_panel(args.cache)
    daily_map = xs.get("_daily_map") or {}
    lookup = close_lookup(daily_map)

    days = sorted(d for d in (xs.get("_dates") or [])
                  if WINDOW[0] <= d <= WINDOW[1])
    grid = sorted({d for k in KS for d in _grid_days(days, k)})

    # ── 臂 D 的财务侧（bm）：**逐字复用臂 R 的骨架** ──────────────
    spine = annual_series_from_cache(args.financial)
    active = active_annual_by_day(spine, grid)
    px = price_and_shares_on(daily_map, grid, carry_stale=True)
    m = _fv(active, px)
    xs = _attach_to_panel(xs, m, grid, ["bm"])

    # 🚨 **空操作守卫**：bm 整列接不上 ⇒ 臂 D 复现不出 ⇒ 整套不可信
    # ⇒ 与其落一份「全是复现不出」的产物，不如当场报错。
    n_bm = sum(len(_factor_values_on(xs, d, "bm")) for d in grid)
    if n_bm < 100:
        raise AssertionError(
            f"bm 因子挂载后几乎全空（{n_bm} 个取值 / {len(grid)} 天）"
            "⇒ join 没接上（码格式或键不对）⇒ **拒绝**落一份空产物")

    fwd_by_k = {}
    for k in KS:
        t1 = time.time()
        fwd_by_k[k] = {d: _forward_map(xs, d, k) for d in _grid_days(days, k)}
        print(f"  K={k} 前瞻收益 {len(fwd_by_k[k])} 天"
              f"（{time.time() - t1:.0f}s）", flush=True)

    n_a = int(published["n_A"]["primary"]["n_A"])
    rows = {}
    for arm_name in (ARM_A, ARM_B, ARM_C):
        for k in KS:
            rows[f"{arm_name}@K{k}"] = leg_table_arm(
                xs, days, k, fwd_by_k[k], kind=arm_name, n_a=n_a,
                lookup=lookup)

    equiv = leg_table_equivalence_check(xs, days, fwd_by_k, n_a)
    return {"xs": xs, "days": days, "grid": grid, "lookup": lookup,
            "fwd_by_k": fwd_by_k, "n_a": n_a, "rows": rows,
            "leg_table_equivalence": equiv, "elapsed_s": time.time() - t0}


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
    if got_sha != PANEL_SHA256:
        raise SystemExit(
            f"面板指纹与预注册 §3.4 钉死的不一致：\n"
            f"  期望 {PANEL_SHA256}\n  实际 {got_sha}\n"
            f"⇒ 换了面板 ⇒ 与 round B **不是同一台仪器** ⇒ 拒绝跑"
            f"（对照臂 D 的前提被破坏）")

    print("[1/4] 读产物锚（round B / D2 台账）…", flush=True)
    published = json.loads(
        (ROOT / "data/reports/fundamental_mechanism_armR_2026-10-21.json")
        .read_text(encoding="utf-8"))
    req_doc = json.loads(
        (ROOT / "data/reports/d2_signal_requirement.json").read_text(encoding="utf-8"))
    mc = req_doc["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]

    print("[2/4] 阈值（从产物读，**不手打**）…", flush=True)
    thr = read_thresholds(ROOT / "data/reports")
    print(f"  K=21: D1={thr['k21']['d1_friction_annual_pct']:.4f}%  "
          f"K=63: D1={thr['k63']['d1_friction_annual_pct']:.4f}%  "
          f"D2={thr['d2_required_gross_annual_pct']:.4f}%", flush=True)

    print("[3/4] 跑臂 R 同款流程…", flush=True)
    arm = run_arm(args, published)

    print("[4/4] 判决 + 落盘…", flush=True)
    payload = build_payload(args, arm, thr, published, mc)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    equiv = payload["leg_table_equivalence"]
    print(f"  腿表逐位对拍：{equiv['bitwise_equal']}"
          f"（比了 {equiv['n_values_compared']} 个数）", flush=True)
    if not equiv["bitwise_equal"]:
        for row in equiv["rows"]:
            for d in row["diffs"][:5]:
                print(f"    K={row['k']} n={d['n']} {d['field']}: "
                      f"mine={d['mine']!r} ref={d['ref']!r}", flush=True)
        raise SystemExit(
            "🚨 腿表逐位对拍失败 ⇒ 本轮读数**不可信** ⇒ **拒绝盖章**"
            "（产物已落盘但无 sidecar ⇒ 不可引用）")

    provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     extra={
                         "arm": args.arm_name,
                         "window": list(WINDOW),
                         "k_arms": list(KS),
                         "panel_cache": str(Path(args.cache).resolve()),
                         "panel_cache_sha256": got_sha,
                         "financial_source": str(Path(args.financial).resolve()),
                         "financial_source_sha256": _sha256_file(
                             Path(args.financial)),
                         "n_a": arm["n_a"],
                     })
    print(f"  对照臂 bm 复现：{payload['control_bm_reproduction']['all_within_tolerance']}")
    print(f"  逐格状态计数：{payload['verdict']['primary']['state_counts']}")
    for k, v in payload["intervention_verdict"]["by_k"].items():
        print(f"  干预判决 {k}: {v['state']}  ({v.get('branch')})")
    print(f"  产物：{out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
