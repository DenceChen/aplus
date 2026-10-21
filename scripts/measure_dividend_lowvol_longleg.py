#!/usr/bin/env python
"""红利/低波（股息率 + 低波）**长腿**篮子的判决测量器。

**预注册**：`docs/PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md`（事前写死，
本文写下时本轮收益一个都没算过）。

**产物**：`data/reports/dividend_lowvol_longleg.json`（+ sidecar）。

## 它回答的唯一问题

**「股息率 / 低波 IVOL / 等权 z 合成」这三个因子定义，在 12 个臂上
（3 定义 × 2 档 K × 2 档 N），**长腿**（涨腿 top-N）有没有可测的超额，
以及在 5 万本金下账户层长什么样。**

## 它**不**回答的问题（🚨 写死在产物里）

* **不**回答「红利/低波能不能到年化 20%」。
* **不**回答「红利/低波因子有没有效」——覆盖不全时**只能**说「测不到」
  或「覆盖不足，本轮不可判」，**不许**说「无效 / 没有 alpha / 已证伪」。
* **不**把「多空」口径的数当长腿的数（石川书里的 IVOL 1.27%/月是多空，
  `data/books/cn_factor_lines.txt:9884-9885`；空头腿 A 股散户做不了）。

## 硬约束（违反即作废）

1. **公式不许重写**：账户引擎与判决函数从
   `scripts/measure_deployed_d2.py` import（`simulate_account` / `build_steps` /
   `account_leg` / `tier_stats` / `verdict_for` / `build_price_map`）；
   分块 t 从 `scripts/backtest_walk_forward_weights.py` import；
   账户层 `tier()` / `judge()` 从 `scripts/measure_basket_nav_01.py` import。
   🚨 **本仓反复吃亏的形状就是「两份副本之间没有守卫」。**
2. **窗口钉死** `--start 2021-01-01 --end 2026-10-12`；`--end` 留空 ⇒ 拒绝（形状 23）。
3. **`--data-root` 必须是仓内 `data/`**（仓外 ⇒ rc=2 拒绝，不回落）。
4. **不改 `aplus/` 生产代码**。
5. **`expect_bytes` 必须在 `main()` 的第一条可执行语句取**（第 125 层）。
6. **盖章**：`provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=..., extra={...})`。
7. **判决链顺序**（预注册 §4.3）：`INVALID`（负控通过）→ Gate 0 结构闸 →
   **Gate 1 MDE 闸** → Gate 2 显著性 → Gate 3 达标。
   ⇒ **MDE 闸必须第一个判**（第 16 层）。
8. **`t_crit` 取真 t 分布**（`aplus.dist.t_crit_two_sided(0.05/12, df)`），不查表。

## 因子定义（照预注册 §2，别自己改）

| 腿 | 定义 | 方向 | 出处 |
|---|---|---|---|
| 股息率 | TTM 每股税前现金分红 ÷ **不复权** close | 越高越好 | `scripts/collect_dividend_pit.py::dividend_yield` |
| 低波 | `aplus/factor.py::ivol_series` | **取负**（低 IVOL ⇒ 更高预期收益） | 石川 `:9845-9850`, `:9870-9885` |
| 合成 | 等权 z：`z(股息率) + z(−IVOL)`，各占一半 | —— | 预注册 §2.3 |

## 池口径（`--pool`，三种，**产物里自报**）

| `--pool` | 池 | 用途 |
|---|---|---|
| `full`（默认） | `data/master/daily` 全部 5,460 只 | **预注册口径**（§4.1）。GB 级面板。 |
| `covered` | **事件层单元账**里采到了的票 | 降本跑：池内股息率可算率 100%。 |
| `sampled` | 按代码序取前 `--pool-size` 只 | 降本跑（不保证池内可算率）。 |

🚨 **`covered` / `sampled` 都不是预注册的池** ⇒ 产物里
`artifact_meta.is_prereg_pool = false` ⇒ **不可当测量引用**。
⇒ 但**覆盖率闸的分母永远是全池**（5,460）⇒ 降本跑的覆盖率闸**必然不过**
⇒ 所有臂 = `NO_VERDICT`。**这不是 bug，这是设计**：降本跑的定位是
**验证管线跑得通**，不是出判决。

## 单位（🚨 第 97/138 层）

**本产物的所有 `*_pct` 字段都是「百分数」**（15.31 = 15.31%），
并**另**给 `*_fraction` 字段。
⚠️ **仓内另一些产物把 `_pct` 用作分数**
（`factor_width_mde.json::widths["10"].gross_annual_pct` = 0.0481 = 4.81%；
`.MDE_annual_pct` = 0.1531 = 15.31%）⇒ **跨产物比值前先核单位**。

## 覆盖率闸（Gate 0 的第三条件）

预注册 §4.3 的 Gate 0 写了两个结构条件，§7 的失败表另有一条
「**覆盖不足** ⇒ `NO_VERDICT`」⇒ 把覆盖率闸**放进 Gate 0**（结构闸）。
阈值 `2/3` 取自 `aplus/panel.py::MIN_SAMPLE_RATIO`（本仓自己的
「样本够不够用」锚）⇒ **门槛不许手打**。

## 本轮的诚实边界（🚨 必须进产物）

* **股息率的全池数据还在采**（分片采集，每日 ≤15,000 次 baostock 调用）。
  ⇒ 本脚本跑在「已采到的那部分」上 ⇒ 覆盖率**远低于**全池。
  ⇒ 产物必须写清：覆盖了多少只 / 全池多少只 / 覆盖是**哪些**票 /
  **覆盖偏差的方向**。
* **事件层读到的金额是「今天读到的」，不是「当时公告的」**
  （预案 → 股东大会可能改额）⇒ 回测吃的是「带修订风险的历史」。
  ⇒ 产物必须写明，且**不许**与「当时读到的」那一层混引。
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

import numpy as np
import pandas as pd


# ── 仓库根（不写死 `parents[1]`，见 `measure_deployed_d2.py` 的同款理由）────

def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parents[1], *here.parents, Path.cwd(), *Path.cwd().parents):
        if (cand / "aplus" / "hurdle.py").exists():
            return cand
    raise SystemExit("找不到仓库根（缺 aplus/hurdle.py）")


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus.hurdle import DAYS_PER_YEAR as R          # noqa: E402  244，唯一真相源
from aplus.hurdle import hurdle_rate, rounds_per_year  # noqa: E402
from aplus.costs import annual_friction               # noqa: E402
from aplus.dist import t_crit_two_sided               # noqa: E402
from aplus.forward import MIN_TEST_PERIODS            # noqa: E402
from aplus.verify import MIN_BASKET                   # noqa: E402
from aplus.panel import MIN_SAMPLE_RATIO              # noqa: E402
from aplus import provenance                          # noqa: E402

GENERATOR = "scripts/measure_dividend_lowvol_longleg.py"
DEFAULT_OUT = ROOT / "data" / "reports" / "dividend_lowvol_longleg.json"
PREREG = "docs/PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md"

#: 判决窗口（钉死，与全仓 20+ 份产物同口径）。
WINDOW_START_PINNED = "2021-01-01"
WINDOW_END_PINNED = "2026-10-12"

#: 用户目标（年化 20% 净值）。
TARGET = 0.20

#: 本金与满仓判决口径（预注册 §4.2：`equity_weight = 1.0` 是**满仓判决口径**）。
CAPITAL = 50_000.0
EQUITY_WEIGHT = 1.0

#: **多重比较的候选数 m**（预注册 §4.4：3 定义 × 2 档 K × 2 档 N = 12）。
M_CANDIDATES = 12

#: 随机对照臂的重复次数（预注册 §6 C2）。
RANDOM_REPS = 100

#: 随机对照臂的种子基数（预注册 §6 C2：`rep_seed = 20261021 + r`）。
RANDOM_SEED_BASE = 20261021

#: 因子定义 → `composite_score` 的权重字典。
#:
#: 🚨 **等权 z 合成**：`composite_score` 用 `robust_z` 逐列标准化后按
#: `sum(|w|)` 归一 ⇒ `{"divyield": 0.5, "ivol": -0.5}` 恰好是
#: `(z(股息率) − z(IVOL)) / 2` ⇒ **两条腿各占一半**（预注册 §2.3）。
#: 🚨 **不许**按 IC 定权（判决窗口内重新拟合 ⇒ 第 91 层）。
FACTOR_DEFS: dict[str, dict] = {
    "dividend": {
        "weights": {"divyield": 1.0},
        "label_cn": "股息率（单腿）",
        "required_cols": ["divyield"],
    },
    "ivol": {
        # 🚨 IVOL **取负**：石川书做多**最低** IVOL 组（`:9870-9885`）
        # ⇒ 低 IVOL ⇒ 更高预期收益。
        "weights": {"ivol": -1.0},
        "label_cn": "低波 IVOL（单腿，方向取负）",
        "required_cols": ["ivol"],
    },
    "composite": {
        "weights": {"divyield": 0.5, "ivol": -0.5},
        "label_cn": "红利+低波 等权 z 合成（主臂因子定义）",
        "required_cols": ["divyield", "ivol"],
    },
}


class Arm:
    """一个候选臂。**候选集写死**（预注册 §5），事后不许加臂来捞主臂。

    🚨 **刻意不用 `@dataclass`**：`dataclasses` 在装饰时会去
    `sys.modules[cls.__module__].__dict__` 取命名空间，而本仓的测试按
    `importlib.util.spec_from_file_location(...)` 加载脚本时**不把模块登记进
    `sys.modules`** ⇒ `@dataclass` 在那条路径上直接 `AttributeError` 崩掉。
    ⇒ 用一个普通类（本仓 `measure_deployed_d2.py` 的同款纪律）。
    """

    __slots__ = ("arm_id", "factor_def", "k", "n", "role", "is_main")

    def __init__(self, arm_id: str, factor_def: str, k: int, n: int,
                 role: str, is_main: bool = False):
        self.arm_id = arm_id
        self.factor_def = factor_def
        self.k = int(k)
        self.n = int(n)
        self.role = role
        self.is_main = bool(is_main)

    def __repr__(self) -> str:                       # pragma: no cover
        return (f"Arm({self.arm_id!r}, {self.factor_def!r}, "
                f"K={self.k}, N={self.n}, role={self.role!r}, "
                f"is_main={self.is_main})")


#: 候选表（预注册 §5，**先验门**，写死不许改）。
ARMS: tuple[Arm, ...] = (
    Arm("A1", "dividend", 21, 10, "腿 1 隔离"),
    Arm("A2", "ivol", 21, 10, "腿 2 隔离"),
    Arm("A3", "composite", 21, 10, "★ 主臂（合成 × 月频 × 10 只）", is_main=True),
    Arm("A4", "composite", 63, 10, "低频档"),
    Arm("A5", "composite", 21, 30, "宽榜档"),
    Arm("A6", "composite", 63, 30, "低频宽榜"),
    Arm("A7", "dividend", 63, 10, "—"),
    Arm("A8", "dividend", 21, 30, "—"),
    Arm("A9", "dividend", 63, 30, "—"),
    Arm("A10", "ivol", 63, 10, "—"),
    Arm("A11", "ivol", 21, 30, "—"),
    Arm("A12", "ivol", 63, 30, "—"),
)

#: 注入横截面的股息率列名（**不是**仓内既有的因子名 ⇒ 不会撞 `FACTOR_COLUMNS`）。
DIVYIELD_COL = "divyield"

#: 🚨 **池覆盖率闸**。
#:
#: 预注册 §4.3 的 Gate 0 写了两个结构条件（独立调仓周期数、每日可算成分数），
#: §7 的失败表另有一条「**覆盖不足** ⇒ `NO_VERDICT`」。
#: ⇒ 把覆盖率闸**放进 Gate 0**（结构闸，不是性能闸），是**摆位**不是改判据。
#:
#: 阈值 `MIN_SAMPLE_RATIO = 2/3` 取自 `aplus/panel.py` —— 那是本仓自己的
#: 「样本够不够用」的锚（「窗口内有效样本低于此比例 ⇒ 作缺失，不用小样本硬算」）。
#: ⇒ **同一个比例口径**用在池覆盖率上：池子覆盖不到 2/3 ⇒ 那不是预注册的那个池，
#: 测的是**另一个对象** ⇒ 不出判决。
COVERAGE_MIN = MIN_SAMPLE_RATIO

#: 判决标签（四/五态；`INVALID` 是负控触发的整套作废态）。
LABEL_INVALID = "INVALID"
LABEL_NO_VERDICT = "NO_VERDICT"
LABEL_POWERLESS = "POWERLESS"
LABEL_NOT_SIGNIFICANT = "NOT_SIGNIFICANT"
LABEL_BELOW_HURDLE = "EXCESS_BELOW_HURDLE"
LABEL_MEETS = "MEETS_HURDLE"

#: 🚨 **失败文案先定死**（预注册 §7）。不许事后改。
#:
#: 🚨 这些串里**不许**出现「无效」「没有 alpha」「已证伪」「接近」「达不到」。
#: 「测不到」与「没有」是**两件事**（第 16 层）：POWERLESS 写「测不到」，
#: 不许写「无效」。
WORDING: dict[str, str] = {
    LABEL_INVALID: "**整套判决作废**（负控通过 ⇒ 量测管线有偏，不是因子结论）。",
    LABEL_NO_VERDICT: ("**本轮覆盖不足，不可判**（**不出判决**：数据/样本不足）。"
                       "「不出判决」与「判决为否」必须分开；"
                       "「测不到」不等于「没有」。"),
    LABEL_POWERLESS: ("在能测的量级上**测不到**；这不等于『没有超额』——"
                      "只等于『测不到』。"),
    LABEL_NOT_SIGNIFICANT: "在 MDE 的量级上未达显著；效应大小只能给区间。",
    LABEL_BELOW_HURDLE: ("长腿超额**不足以**覆盖 20% 目标 + 摩擦"
                         "（缺口的方向与量级另算）。"),
    LABEL_MEETS: ("长腿毛超额达到门槛（毛口径），且在 MDE 之上、"
                  "且通过 Bonferroni 显著性检验。"),
}

#: 🚨 **失败文案守卫的禁用词**（预注册 §7）。
#:
#: ⚠️ **中文断词不许用 `\b`**（JS/正则的 `\b` 只认 ASCII 单词字符，
#: `乙\b` 恒不成立 ⇒ 判据恒假 = 守卫是装饰）。这里用**逐字子串**。
#: ⚠️ 「没有超额」**不在**禁用表里 —— 它是预注册 §4.3 Gate 1 逐字要求的写法。
FORBIDDEN_PHRASES = ("无效", "没有 alpha", "已证伪", "接近", "达不到")

#: 负控三态（预注册 §6 C4：「应当显著为负。若它也显著为正 ⇒ 量测管线有偏」）。
NC_FAILED_AS_EXPECTED = "failed_as_expected"   # 翻转后显著为负 ⇒ 按预期失败 ⇒ 管线正常
NC_PASSED = "passed"                            # 翻转后显著为正 ⇒ 负控「通过」⇒ 整套作废
NC_INCONCLUSIVE = "inconclusive"                # 不显著 ⇒ 不决定


# ── 共享脚本的加载（按路径，不靠 cwd 的偶然状态）────────────────────

def _load_script(name: str, alias: str):
    """把 `scripts/<name>` 当**模块**加载（`measure_deployed_d2.py` 的同款纪律）。"""
    spec = importlib.util.spec_from_file_location(alias, ROOT / "scripts" / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_MD2 = None
_BN = None
_MBN = None
_FA = None
_CDP = None


def load_measure_deployed_d2():
    """**账户引擎 + 账户判决**的唯一来源（不许重写第二份实现）。"""
    global _MD2
    if _MD2 is None:
        _MD2 = _load_script("measure_deployed_d2.py", "_divlowvol_md2")
    return _MD2


def load_backtest_walk_forward():
    global _BN
    if _BN is None:
        _BN = _load_script("backtest_walk_forward_weights.py", "_divlowvol_bn")
    return _BN


def load_measure_basket_nav_01():
    global _MBN
    if _MBN is None:
        _MBN = _load_script("measure_basket_nav_01.py", "_divlowvol_mbn")
    return _MBN


def load_factor_attribution():
    global _FA
    if _FA is None:
        _FA = _load_script("factor_attribution.py", "_divlowvol_fa")
    return _FA


def load_collect_dividend_pit():
    global _CDP
    if _CDP is None:
        _CDP = _load_script("collect_dividend_pit.py", "_divlowvol_cdp")
    return _CDP


# ── 纯函数：门槛（由仓内原语重算，**不许手打**）───────────────────────

def hurdle_for(k: int, n: int, *, capital: float = CAPITAL,
               equity_weight: float = EQUITY_WEIGHT,
               target: float = TARGET) -> dict:
    """该臂的**毛超额门槛**（毛口径）+ 它的推导链。

    ```
    毛超额门槛 = hurdle_rate(target = 0.20,
                             cost_per_round = costs.annual_friction(资本, 敞口, N, 1),
                             rounds_per_year = hurdle.rounds_per_year(K))
    ```

    🚨 **`cost_per_round` 的 `rebalances_per_year = 1` 不是笔误**：
    `annual_friction` 返回的是「按本金比例的年化摩擦」，把它乘回
    `rounds_per_year(K)` 才还原出「年摩擦」。
    ⇒ 单轮摩擦 = `annual_friction(…, 1)`，年摩擦 = 单轮 × 年轮次。

    ✅ **独立对拍**：`K=21 / N=10` 的结果必须与带章产物
    `data/reports/factor_width_mde.json::widths["10"].hurdle_width_aware_annual_pct`
    **逐位相同**（预注册 §4.2 的那个例子）。
    """
    cost_per_round = annual_friction(capital, equity_weight, n, 1)
    rpy = rounds_per_year(k)
    annual_cost = rpy * cost_per_round
    h = hurdle_rate(target=target, cost_per_round=cost_per_round,
                    rounds_per_year=rpy)
    return {
        "hurdle_annual_pct": h * 100.0,
        "annual_cost_pct": annual_cost * 100.0,
        "cost_per_round_pct": cost_per_round * 100.0,
        "rounds_per_year": rpy,
        "formula": ("hurdle_rate(target=0.20, "
                    "cost_per_round=aplus.costs.annual_friction(资本, 敞口, N, 1), "
                    "rounds_per_year=aplus.hurdle.rounds_per_year(K))"),
        "caliber": ("**毛超额**口径（目标 20% + 年摩擦）。"
                    "🚨 与「账户净值口径」的门槛**不是同一个量**，"
                    "跨口径禁止比值（本仓第 143 层）。"),
        "equity_weight": equity_weight,
        "capital": capital,
    }


def t_crit_for(df: int) -> float:
    """Bonferroni 校正后的双侧 t 临界值（**真 t 分布**，不查表）。

    `df = 独立观测数 − 1`（观测单位 = **一次调仓**）。
    """
    if df is None or df <= 0:
        return float("inf")
    return t_crit_two_sided(0.05 / M_CANDIDATES, int(df))


def block_means(daily: pd.Series, block: int) -> np.ndarray:
    """**复用** `aplus.evolve_race._block_means`（不许自己再写一份切块）。"""
    from aplus.evolve_race import _block_means
    return np.asarray(_block_means(daily, block), dtype=float)


def mde_annual_pct(daily: pd.Series, block: int) -> dict:
    """**年化 MDE（分块口径）**。

    ## 为什么不是 `power_floor` 按天的那一份

    预注册 §4.3 Gate 1 逐字写：「MDE 由 `scripts/factor_attribution.py::power_floor`
    算 …… **不许**用「按天 SE」当 MDE —— K 日调仓的真实独立观测只有 n/K，
    **分块 t 才是诚实的那一个**」。

    ⇒ 本函数把 `power_floor` **喂块均值**而不是逐日序列：
    `power_floor(block_means)["MDE_annual_pct"]`
    = `t_power × 244 × sd(块均值) / √(块数)`
    = **以「一次调仓」为观测单位**的年化 MDE。

    ⚠️ **仓内既有产物 `factor_width_mde.json` 的 MDE 是按天 SE 那一份**
    （实测：其 `MDE_annual_pct / gross_annual_pct = 3.182`
    = `t_power / t_naive`，而 `t_naive` 是按天 t）
    ⇒ **本产物与它不可直接比大小**，本产物更严（这正是预注册要求的）。
    """
    fa = load_factor_attribution()
    bm = block_means(daily, block)
    if bm.size < 2:
        return {"mde_annual_fraction": None, "mde_annual_pct": None,
                "n_independent_obs": int(bm.size),
                "reason": "块数不足 2，算不出 MDE"}
    pf = fa.power_floor(bm, days_per_year=R)
    # 🚨🚨 **单位**：`power_floor` 的 `MDE_annual_pct` 是**分数**
    # （仓内既有产物 `factor_width_mde.json::widths["10"].MDE_annual_pct`
    # = 0.1531 = 15.31%）⇒ 直接当百分数用会**小 100 倍**
    # （第 97/138 层：字段名带 pct 而值是分数）。
    # ⇒ 本函数**同时**给两个，并显式命名。
    return {
        "mde_annual_fraction": float(pf["MDE_annual_pct"]),
        "mde_annual_pct": float(pf["MDE_annual_pct"]) * 100.0,
        "n_independent_obs": int(bm.size),
        "t_naive_on_blocks": float(pf["t_naive"]),
        "method": ("power_floor(块均值) ⇒ 观测单位 = 一次调仓"
                   "（预注册 §4.3：不许用按天 SE 当 MDE）"),
        "t_power": float(fa.T_POWER_TARGET),
        "t_crit_5pct": float(fa.T_CRIT_5PCT),
    }


def annual_sd(daily: pd.Series) -> dict:
    """**年化 sd**（仓内口径：sd × √T，不是 sd × T）。

    ⚠️ 与 MDE **不是同一个量**：sd 是「持有一年那一个观测」的波动，
    MDE 是「由 n 天估出的年化率」的标准误（两者差 √R 倍）。

    🚨 **单位**：`power_floor` / `factor_width_mde.json` 那一族把
    `annual_sd_pct` 写成**分数**（0.1307 = 13.07%）⇒ 本函数**同时**给
    分数与百分数，并显式命名（第 97/138 层）。
    """
    s = pd.Series(daily).astype(float).dropna()
    if len(s) < 2:
        return {"annual_sd_fraction": None, "annual_sd_pct": None}
    frac = float(s.std(ddof=1)) * math.sqrt(R)
    return {"annual_sd_fraction": frac, "annual_sd_pct": frac * 100.0}


def hit_rate_per_trade(results: pd.DataFrame) -> dict:
    """**逐笔**命中率：涨腿成交笔数里 `actual_pct > 0` 的占比。"""
    if results is None or len(results) == 0:
        return {"hit_rate": None, "n_trades": 0, "n_hits": 0}
    up = results[results["direction"] == "up"]
    a = up["actual_pct"].dropna()
    n = int(len(a))
    hits = int((a > 0).sum())
    return {
        "hit_rate": (hits / n) if n else None,
        "n_trades": n,
        "n_hits": hits,
        "caliber": "**逐笔**口径（涨腿成交笔数里 actual_pct > 0 的占比）",
    }


def hit_rate_by_score_bucket(pairs: pd.DataFrame, n_buckets: int = 4) -> dict:
    """**排序力**：命中率随分数的分布（按分数**降序**分桶）。

    ## 为什么要有它

    超额收益那一列是**差序列**、方差极大 ⇒ 在 6 年日频上常常测不出符号。
    而「**分数越高 ⇒ 命中率越高**」是**同一个因子的另一种问法**，
    它的分母小得多（逐笔命中/不中）⇒ 常常能先给出方向。
    ⇒ 它是**能力守卫**的对象（能给出非 `None` 的单调性判决），
    也是**反向正控**的对象（方向写反 ⇒ 守卫会红）。

    ## 方向（写死在代码里，不许靠文档推断）

    * 桶 0 = **分数最高**的一桶，桶 `n_buckets−1` = 分数最低的一桶。
    * ⇒ **期望**：命中率**随桶号递增**（高分桶命中率高）⇒ `monotone = "increasing"`。
    * ⇒ 命中率先增后减/先减后增 ⇒ `monotone = "none"`。

    ⚠️ **这是「排序力」不是「alpha」**：命中率单调上升说明分数能排序，
    **不**说明这个排序能到 20%（账户口径是另一件事）。
    """
    if pairs is None or len(pairs) == 0 or "score" not in pairs.columns:
        return {"monotone": None, "buckets": [], "n": 0,
                "reason": "没有可用的（分数, 收益）配对"}
    df = pairs.copy()
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["score", "actual_pct"])
    n = int(len(df))
    if n < 4:
        return {"monotone": None, "buckets": [], "n": n,
                "reason": f"配对数 {n} < 4，算不出分桶单调性"}
    nb = max(1, min(int(n_buckets), n))
    # 🚨 **按秩分桶**（不是按分数值分桶）：分数分布是肥尾的，
    # 按值分桶会让某一桶塞进几乎全部样本、其余桶空着。
    df = df.assign(_r=df["score"].rank(method="first", ascending=False))
    # 🚨 **桶边界必须覆盖全部秩**（1 … n）：用 `np.arange(1, nb+2)` 只会覆盖
    # 前 nb 个秩 ⇒ 其余样本的桶号是 NaN ⇒ **它们会被整段丢掉**
    # （第一版就是这样：8 个样本、2 个桶 ⇒ 只有 3 个进了桶）。
    # ⇒ 边界取 `linspace(1, n+1, nb+1)` 且 `right=False`（左闭右开），
    # 最后一个桶刚好收住秩 n。
    edges = np.linspace(1.0, float(n) + 1.0, nb + 1)
    df["_b"] = pd.cut(df["_r"], bins=edges, labels=list(range(nb)), right=False)
    rows = []
    for b in range(nb):
        sub = df[df["_b"] == b]
        if sub.empty:
            rows.append({"bucket": b, "n": 0, "hit_rate": None,
                         "mean_score": None})
            continue
        rows.append({
            "bucket": b,
            "n": int(len(sub)),
            "hit_rate": float((sub["actual_pct"] > 0).mean()),
            "mean_score": float(sub["score"].mean()),
        })
    rates = [r["hit_rate"] for r in rows if r["hit_rate"] is not None]
    monotone = None
    if len(rates) >= 2:
        # 🚨🚨 **方向**：桶 0 = **分数最高** ⇒ 桶号递增 = 分数**递减**。
        # ⇒ 「命中率随分数递增」在**桶号**上是**不增**。
        # ⇒ 第一版把这两个写反了（把「桶号递增」当成「分数递增」）
        # ⇒ 真有排序力的因子会被判成 `decreasing`（第 20 层：
        # **判据写反 ⇒ 拦住目标信号**）。
        up_with_score = all(rates[i] >= rates[i + 1] - 1e-12
                            for i in range(len(rates) - 1))
        down_with_score = all(rates[i] <= rates[i + 1] + 1e-12
                              for i in range(len(rates) - 1))
        if up_with_score and not down_with_score:
            monotone = "increasing"        # 命中率**随分数**递增
        elif down_with_score and not up_with_score:
            monotone = "decreasing"        # 命中率**随分数**递减
        else:
            monotone = "none"
    hi = rows[0]["hit_rate"] if rows else None
    lo = rows[-1]["hit_rate"] if rows else None
    return {
        "monotone": monotone,
        "buckets": rows,
        "n": n,
        "hit_rate_high_score_bucket": hi,
        "hit_rate_low_score_bucket": lo,
        "direction_convention": ("桶 0 = **分数最高** ⇒ "
                                 "`increasing` = **命中率随分数递增**"
                                 "（= 桶号方向不增）（**期望方向**）"),
        "caliber": ("**排序力**（命中率随分数单调），**不是** alpha；"
                    "账户口径能不能到 20% 是另一件事。"),
    }


def score_hit_pairs(xs: dict, dates: list[str], arm: Arm,
                    res: pd.DataFrame | None) -> pd.DataFrame:
    """逐笔（分数, 收益）配对：把横截面的合成分对到成交结果上。"""
    from aplus.factor import composite_score
    if res is None or len(res) == 0:
        return pd.DataFrame(columns=["pred_date", "code", "score", "actual_pct"])
    w = FACTOR_DEFS[arm.factor_def]["weights"]
    up = res[res["direction"] == "up"]
    by_day = {str(d): g for d, g in up.groupby("pred_date")}
    rows: list[dict] = []
    for d in dates:
        g = by_day.get(str(d))
        if g is None or g.empty:
            continue
        uni = xs.get(d)
        if uni is None or uni.empty:
            continue
        sc = composite_score(uni, w)
        smap = {str(c): float(s) for c, s in
                zip(uni["code"], np.asarray(sc, dtype=float))}
        for _i, r in g.iterrows():
            s = smap.get(str(r["code"]))
            a = r["actual_pct"]
            if s is None or not np.isfinite(s):
                continue
            if a is None or not np.isfinite(a):
                continue
            rows.append({"pred_date": str(d), "code": str(r["code"]),
                         "score": s, "actual_pct": float(a)})
    return pd.DataFrame(rows, columns=["pred_date", "code", "score", "actual_pct"])


# ── 纯函数：判决链（顺序即判据）──────────────────────────────────

def negative_control_state(t_flipped: float | None, t_crit: float) -> str:
    """符号翻转负控的三态判定（预注册 §6 C4）。

    * 翻转后 **显著为负** ⇒ `failed_as_expected`（按预期失败 ⇒ 管线正常）
    * 翻转后 **显著为正** ⇒ `passed`（负控通过 ⇒ **整套判决作废**）
    * 其余 ⇒ `inconclusive`（不决定）

    🚨 **方向不许写反**：负控「通过」= 翻号之后结果**没跟着翻** ⇒ 管线有偏。
    """
    if t_flipped is None or not np.isfinite(t_flipped):
        return NC_INCONCLUSIVE
    if t_flipped >= t_crit:
        return NC_PASSED
    if t_flipped <= -t_crit:
        return NC_FAILED_AS_EXPECTED
    return NC_INCONCLUSIVE


def decide(*, gross_annual_pct: float | None,
           mde_annual_pct: float | None,
           t_blocked: float | None,
           t_crit: float,
           hurdle_annual_pct: float,
           n_independent_obs: int,
           n_scoreable_min: int,
           n_positions: int,
           coverage_ratio: float | None,
           coverage_min: float = COVERAGE_MIN,
           negative_control: str | None = None) -> dict:
    """**判决链**（预注册 §4.3 的顺序）。

    ```
    0  负控通过                      ⇒ INVALID      （整套作废）
    1  Gate 0 结构闸                  ⇒ NO_VERDICT   （不出判决）
         · 独立调仓周期数 < MIN_TEST_PERIODS
         · 每日可算成分数 < max(MIN_BASKET, N)
         · 池覆盖率 < coverage_min
    2  Gate 1 MDE 闸                  ⇒ POWERLESS
         · |毛超额年化| ≤ MDE
    3  Gate 2 显著性闸                ⇒ NOT_SIGNIFICANT
         · 分块 t < t_crit(Bonferroni)
    4  Gate 3 达标闸                  ⇒ EXCESS_BELOW_HURDLE
         · 毛超额 < 门槛
    5  全部通过                        ⇒ MEETS_HURDLE
    ```

    🚨 **MDE 闸必须第一个判**（第 16 层）：同一句「没测出显著」，
    作用在门槛上是判决，作用在小效应上是废话。
    ⇒ 「不显著」必须与它的 MDE **并排**印出。

    🚨 **Gate 0 里的 `max(MIN_BASKET, N)`**：预注册 §4.3 只写了 `MIN_BASKET`，
    但 §4.3 的备注自己指出「N=10 的篮子只要有 3 只成分就成立」的问题。
    ⇒ **选不出 N 只 ⇒ 那不是选股，是整池** ⇒ 加一条 `≥ N` 的结构条件
    （**加的是结构闸，不是性能闸**；不改变任何性能判据）。
    """
    checks: list[dict] = []

    def _add(gate: str, ok: bool, detail: str) -> bool:
        checks.append({"gate": gate, "passed": bool(ok), "detail": detail})
        return bool(ok)

    # ── 0 负控 ────────────────────────────────────────────────────
    if negative_control == NC_PASSED:
        _add("negative_control", False,
             "符号翻转负控显著为正 ⇒ 量测管线有偏 ⇒ 整套判决作废")
        return {"label": LABEL_INVALID, "gate": "negative_control",
                "wording": WORDING[LABEL_INVALID], "checks": checks}

    # ── 1 Gate 0 结构闸 ──────────────────────────────────────────
    ok = _add("G0_independent_periods",
              n_independent_obs >= MIN_TEST_PERIODS,
              f"独立调仓周期数 {n_independent_obs} ≥ MIN_TEST_PERIODS "
              f"{MIN_TEST_PERIODS}")
    need_scoreable = max(MIN_BASKET, int(n_positions))
    ok = _add("G0_min_basket",
              n_scoreable_min >= need_scoreable,
              f"每日可算成分数最小值 {n_scoreable_min} ≥ "
              f"max(MIN_BASKET {MIN_BASKET}, N {n_positions}) = {need_scoreable}"
              ) and ok
    if coverage_ratio is None:
        ok = _add("G0_coverage", False, "池覆盖率不可知") and ok
    else:
        ok = _add("G0_coverage", coverage_ratio >= coverage_min,
                  f"池覆盖率 {coverage_ratio:.6f} ≥ coverage_min {coverage_min:.6f}"
                  f"（锚：aplus/panel.py::MIN_SAMPLE_RATIO）") and ok
    if not ok:
        return {"label": LABEL_NO_VERDICT, "gate": "G0_structure",
                "wording": WORDING[LABEL_NO_VERDICT], "checks": checks}

    # ── 2 Gate 1 MDE 闸 ──────────────────────────────────────────
    if gross_annual_pct is None or mde_annual_pct is None:
        _add("G1_mde", False, "毛超额或 MDE 不可算")
        return {"label": LABEL_NO_VERDICT, "gate": "G1_mde",
                "wording": WORDING[LABEL_NO_VERDICT], "checks": checks}
    _add("G1_mde", abs(gross_annual_pct) > mde_annual_pct,
         f"|毛超额 {gross_annual_pct:.6f}%| vs MDE {mde_annual_pct:.6f}%"
         f"（年化，分块口径）")
    if abs(gross_annual_pct) <= mde_annual_pct:
        return {"label": LABEL_POWERLESS, "gate": "G1_mde",
                "wording": WORDING[LABEL_POWERLESS], "checks": checks}

    # ── 3 Gate 2 显著性闸 ────────────────────────────────────────
    if t_blocked is None or not np.isfinite(t_blocked):
        _add("G2_significance", False, "分块 t 不可算（退化序列或块数不足）")
        return {"label": LABEL_NOT_SIGNIFICANT, "gate": "G2_significance",
                "wording": WORDING[LABEL_NOT_SIGNIFICANT], "checks": checks}
    _add("G2_significance", t_blocked >= t_crit,
         f"分块 t {t_blocked:.6f} vs t_crit {t_crit:.6f}"
         f"（真 t 分布，Bonferroni p=0.05/{M_CANDIDATES}）")
    if t_blocked < t_crit:
        return {"label": LABEL_NOT_SIGNIFICANT, "gate": "G2_significance",
                "wording": WORDING[LABEL_NOT_SIGNIFICANT], "checks": checks}

    # ── 4 Gate 3 达标闸 ──────────────────────────────────────────
    _add("G3_hurdle", gross_annual_pct >= hurdle_annual_pct,
         f"毛超额 {gross_annual_pct:.6f}% vs 门槛 {hurdle_annual_pct:.6f}%（毛口径）")
    if gross_annual_pct < hurdle_annual_pct:
        return {"label": LABEL_BELOW_HURDLE, "gate": "G3_hurdle",
                "wording": WORDING[LABEL_BELOW_HURDLE], "checks": checks}

    return {"label": LABEL_MEETS, "gate": "passed",
            "wording": WORDING[LABEL_MEETS], "checks": checks}


def suite_note(n_passed: list, coverage_min: float) -> str:
    """**套级说明串** —— 必须由**实际状态**算出来（2026-10-21）。

    🚨 **原写法是写死的常量** ⇒ 不管发生了什么，它都断言
    「负控「通过」⇒ 整套判决作废」
    ⇒ 而实测 `invalidated_by = []`（**没有任何臂的负控通过**）
    ⇒ ⇒ **产物声称了一个没发生的原因**
    ⇒ ⇒ 它把读的人（**包括协调方自己**）导向了错误的理由
    ⇒ ⇒ **机器生成的解释同样会脱钩** ⇒ 散文那个病不豁免机器。

    ⇒ 抽成**模块级函数**（不嵌在 `main()` 里）⇒ 守卫能**直接调它**做正反两向
    ⇒ 不用重跑 25 分钟的产物就能验。
    """
    if n_passed:
        return ("负控「通过」（翻转后显著为正）⇒ **整套判决作废**；"
                "作废它的臂：" + "、".join(n_passed)
                + "。⚠️ 只对**通过覆盖闸**的臂生效 —— "
                  "一个覆盖不足的池上的负控本身不说明什么。")
    return ("**没有**任何臂的负控「通过」⇒ 整套判决**不是**被负控作废的。"
            "⇒ 本产物**没有套级判决**：判决在**逐臂**上（`arms.*.verdict`）。"
            "⇒ 逐臂的两种「不可判」各有各的原因："
            "**覆盖不足**（逐日可算成分数 ÷ 全池 < "
            f"{coverage_min:.4f} ⇒ 池不够 ⇒ 不出判决）"
            "与**测不到**（MDE ≫ |毛超额| ⇒ 只支持形状陈述）。")


def suite_invalid_arms(arms: list, results: dict, controls: dict,
                       n_pool_full: int, coverage_min: float = COVERAGE_MIN) -> list[str]:
    """**整套作废**的触发臂列表。

    ## 🚨 作用域：只对**通过覆盖闸**的臂生效

    一个 0.92% 覆盖的池上的负控**本身不说明什么** ⇒ 拿它去作废整套判决
    是**用一把不准的尺子去否决一切**（第 112 层：过严的守卫和恒真的一样有害，
    而且更坏 —— 它会让人去改本来正确的东西）。
    ⇒ 负控「通过」只有在**那个臂自己测得动**的时候才作废整套。

    ⚠️ 反过来也成立：若某个臂**通过覆盖闸**而它的负控「通过」⇒
    **整套判决作废**（预注册 §6：「C4/C5 是负控，它们必须失败；
    若它们通过，整套判决作废」）。
    """
    out: list[str] = []
    for arm in arms:
        nc = (controls.get("negative_control") or {}).get(arm.arm_id) or {}
        if nc.get("state") != NC_PASSED:
            continue
        sc = (results.get(arm.arm_id) or {}).get("scoreable") or {}
        n_min = sc.get("min")
        cov = ((n_min / n_pool_full) if (n_min is not None and n_pool_full)
               else None)
        if cov is not None and cov >= coverage_min:
            out.append(arm.arm_id)
    return out


def wording_guard_findings(payload: dict, scope_keys: tuple[str, ...]) -> list[str]:
    """**失败文案守卫**：在被指定的键里扫禁用词。

    ⚠️ **中文断词不用 `\b`**（`\b` 只认 ASCII 单词字符 ⇒ 中文判据恒假）。
    这里用逐字子串。
    """
    findings: list[str] = []
    for key in scope_keys:
        node = payload.get(key)
        if node is None:
            continue
        stack = [(key, node)]
        while stack:
            path, val = stack.pop()
            if isinstance(val, str):
                for bad in FORBIDDEN_PHRASES:
                    if bad in val:
                        findings.append(f"{path}: 出现禁用词「{bad}」")
            elif isinstance(val, dict):
                for k, v in val.items():
                    stack.append((f"{path}.{k}", v))
            elif isinstance(val, list):
                for i, v in enumerate(val):
                    stack.append((f"{path}[{i}]", v))
    return findings


# ── 纯函数：覆盖与偏差方向 ───────────────────────────────────────

def coverage_bias_note(coverage_ratio: float | None, n_covered: int,
                       n_pool: int, n_scoreable_min: int,
                       n_positions: int) -> dict:
    """覆盖不足时的**偏差方向**说明（必须说清「往哪个方向推」）。

    ## 方向怎么推

    * **没采到的票 = 显式 NaN** ⇒ 它们**不进分母**（不进横截面排名）。
    * ⇒ 测量对象的池子从「全池」缩成「采到的那部分」。
    * ⇒ 若采到的那部分是**按代码顺序系统抽样**（本仓采集器的分片方式），
      则**高股息与低股息的票被抽中的概率大体相同** ⇒
      **抽样本身不产生系统性方向**；但 **池子缩小 ⇒ top-N 的抽样误差变大**
      ⇒ 效应估计的**方差变大 ⇒ MDE 变大 ⇒ 更容易测不到**（方向：偏保守）。
    * ⚠️ **但有一条方向明确**：若被采到的票**恰好集中在高股息簇**
      （银行/煤炭/公用事业），则样本的平均股息率**系统性偏高**
      ⇒ 而 top-N 是在**样本内部**排序 ⇒ **相对排序**受的影响小，
      **绝对水平**受的影响大 ⇒ 报出的股息率**水平**偏乐观，
      **排序力**的方向不因此改变。
    """
    if coverage_ratio is None:
        direction = "覆盖率不可知 ⇒ 偏差方向不可判。"
    elif coverage_ratio >= COVERAGE_MIN:
        direction = ("覆盖率达标 ⇒ 本轮没有覆盖偏差方向的修正项；"
                     "但**事件层金额仍是「今天读到的」**，修订风险另算。")
    else:
        direction = (
            "**本轮覆盖不足，偏差方向「未定」—— 有两条方向相反的通道，"
            "谁占上风没有证据**：\n"
            "① **保守通道**（可测性变差）：没采到的票显式 NaN ⇒ 不进横截面排名 ⇒ "
            "可算池从全池缩成采到的那部分 ⇒ top-N 的抽样误差变大 ⇒ "
            "MDE 变大 ⇒ **更容易测不到**。\n"
            "② **非保守通道**（水平被偏置）：若被采到的票在股息率上**不是**全池的"
            "无偏样本，则样本的股息率**绝对水平**与**排序边界**都会偏 ⇒ "
            "测到的效应可能被**放大或扭曲**，**不是**保守方向。\n"
            "🚨 **本仓的采集机制（`shard_codes`：把**按代码排序**的全池切成连续一段）"
            "在股息率维度上是**无偏抽样**（与是否分红无关）⇒ 通道 ② 理论上不成立；"
            "但**本轮无法验证**（全池没有分红数据，采到的部分就是全部）⇒ "
            "**方向仍判为「未定」**。\n"
            "🚨 **唯一站得住的结论只有一句**："
            "**本轮读数不是全池的读数** ⇒ 判决只能是「测不到」或"
            "「本轮覆盖不足，不可判」，**不许**给「因子有效/无效」的结论。")
    return {
        "coverage_ratio": coverage_ratio,
        "n_covered_codes": n_covered,
        "n_pool_codes": n_pool,
        "coverage_min": COVERAGE_MIN,
        "coverage_min_source": ("aplus/panel.py::MIN_SAMPLE_RATIO = 2/3"
                                 "（本仓自己的「样本够不够用」锚）"),
        "n_scoreable_min": n_scoreable_min,
        "selection_fraction": ((n_positions / n_scoreable_min)
                               if n_scoreable_min else None),
        "selection_fraction_note": (
            "**top-N 占可算池的比例**。这个数大 ⇒ 那不是选股，"
            "是「池子里的一小撮」；⇒ 与全池口径的 top-N **不是同一个量**。"),
        "bias_direction": direction,
    }


# ── 数据层：分红 PIT 与覆盖 ───────────────────────────────────────

def collect_dividend_root(data_root: Path) -> Path:
    """分红快照根（**随 `--data-root` 走**，不许写死仓内路径）。"""
    return Path(data_root) / "reports" / "dividend_pit"


def snapshot_fingerprint(root: Path) -> dict:
    """快照的**身份指纹**（路径 + 大小 + mtime + sha256）。

    🚨 采集器**正在跑**时快照会变 ⇒ 数字必须能指回**哪一份字节**。
    """
    files: list[dict] = []
    if root.is_dir():
        for p in sorted(root.rglob("*.parquet")):
            try:
                b = p.read_bytes()
            except OSError:
                continue
            files.append({
                "path": str(p.relative_to(root)),
                "bytes": len(b),
                "sha256": hashlib.sha256(b).hexdigest(),
                "mtime": p.stat().st_mtime,
            })
    return {
        "root": str(root),
        "n_parquet": len(files),
        "files": files,
        "collector_lock_held": (root / ".lock").is_file(),
    }


def covered_codes_from_ledger(root: Path) -> tuple[set[str], dict]:
    """从**单元账**（`_cells.json`）读出「采到了哪些票」。

    ## 为什么必须读账而不是读事件表

    「一只票在某年没有分红方案」与「这只票还没采到」在事件表里**长得一样**
    ⇒ 只看事件表会把「没采到」读成「零分红」⇒ 编 0 ⇒ 前视。
    ⇒ **采到域只能从单元账读**（`cells[票|年] ∈ {ok, empty}`）。

    🚨 「采到了」的定义 = **该票至少有一格被记过**（`ok` 或 `empty`）。
    `error` 的格**不算**采到（那是「没查成」⇒ 缺数据）。
    """
    cdp = load_collect_dividend_pit()
    codes: set[str] = set()
    n_cells_ok = n_cells_empty = n_cells_error = 0
    ledgers: list[str] = []
    # 🚨🚨 **只读事件层的单元账**（`shard_roots` = 根本身或 `root/shard=*`），
    # **绝不**用 `rglob` 把 `root/daily/`（采集 A「全池当年视图」）
    # 与 `root/announced/`（当天新公告层）一起扫进来。
    # ⇒ 那两层是**另外两个量**（第 7 层：两份各自都对的数不许混比）：
    #   采集 A 只覆盖**当年**（1 年），采集 B 覆盖**判决窗口的整段历史**（8 年）。
    # ⇒ 混进来会让「采到了」看起来像全池 ⇒ **覆盖率闸形同虚设**。
    for event_root in cdp.shard_roots(root):
        for p in sorted(Path(event_root).glob("date=*/_cells.json")):
            try:
                data = cdp.read_cells(p)
            except Exception:                                  # noqa: BLE001
                continue
            # 🚨 `read_cells` **直接返回 cells 字典**（不是整个 payload）。
            cells = data if isinstance(data, dict) else {}
            ledgers.append(str(p.relative_to(root)))
            for key, status in cells.items():
                code = str(key).split("|", 1)[0]
                if status in ("ok", "empty"):
                    codes.add(code)
                    if status == "ok":
                        n_cells_ok += 1
                    else:
                        n_cells_empty += 1
                elif status == "error":
                    n_cells_error += 1
    return codes, {
        "n_codes_covered": len(codes),
        "n_cells_ok": n_cells_ok,
        "n_cells_empty": n_cells_empty,
        "n_cells_error": n_cells_error,
        "ledgers": ledgers,
        "source": "单元账 `_cells.json`（cells[票|年] ∈ {ok, empty} ⇒ 采到）",
    }


def _bare(code: str) -> str:
    from aplus.factor_fundamental import bare_code
    return bare_code(code)


class CloseLookup:
    """按代码存日期/收盘价数组，按需 `searchsorted` 取「≤ D 的最后一根」。

    🚨 **不复权 close**：股息率的分母必须是真实掏出去的钱
    （复权价只给收益链用）。`aplus/store.py::read_daily` 返回的就是不复权 close。
    """

    def __init__(self, daily_map: dict):
        self._arr: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for code, df in (daily_map or {}).items():
            if df is None or df.empty:
                continue
            s = df.sort_values("date", kind="stable")
            self._arr[_bare(code)] = (
                s["date"].to_numpy(dtype="U10"),
                s["close"].to_numpy(float),
            )

    def close_on(self, code: str, date: str) -> float | None:
        arr = self._arr.get(_bare(code))
        if arr is None:
            return None
        dates, closes = arr
        i = int(np.searchsorted(dates, str(date), side="right")) - 1
        if i < 0:
            return None
        v = float(closes[i])
        return v if np.isfinite(v) and v > 0 else None

    @property
    def codes(self) -> list[str]:
        return sorted(self._arr)


def divyield_by_date(events: pd.DataFrame, covered: set[str],
                     closes: CloseLookup, dates: list[str]) -> dict:
    """逐日股息率（**只对采到的票出数**；其余是 NaN ⇒ 不进横截面）。

    🚨 **三态透出**：`observed` / `collected_no_payout_in_window` /
    `not_collected` —— `dividend_yield` 原样透出，**缺数据一律 NaN，绝不编 0**
    （编 0 会让它看起来像「零股息」⇒ 被排序逻辑当成一个合法的低分值）。
    """
    cdp = load_collect_dividend_pit()
    out: dict[str, dict[str, float]] = {}
    status_counts: dict[str, int] = {}
    covered_sorted = sorted(covered)
    for d in dates:
        vis = cdp.pit_dividend_frame_from_events(events, d)
        ttm = cdp.ttm_cash_dividend(vis, d, covered_codes=covered,
                                    codes=covered_sorted)
        row: dict[str, float] = {}
        for _i, rec in ttm.iterrows():
            code = str(rec["code"])
            st = str(rec["status"])
            status_counts[st] = status_counts.get(st, 0) + 1
            cash = rec["ttm_cash_ps"]
            px = closes.close_on(code, d)
            if st == cdp.STATUS_NOT_COLLECTED or st == cdp.STATUS_UNREADABLE_CASH:
                row[code] = float("nan")
                continue
            if cash is None or not np.isfinite(float(cash)) or px is None:
                row[code] = float("nan")
                continue
            row[code] = float(cash) / float(px)
        out[d] = row
    return {"by_date": out, "status_counts": status_counts}


# ── 测量层：一个臂 ───────────────────────────────────────────────

def _top_codes_by_score(universe: pd.DataFrame, score: pd.Series, n: int) -> list[str]:
    """按**分数降序**取 top-N 的代码（**名次序**，不是行序）。

    🚨 **行序 ≠ 名次序**：第一版探针用行序取 top-N ⇒ 注入前后完全一样
    ⇒ 探针恒真（「未采到的票永远被排除」）⇒ 那是**装饰**，不是守卫。
    """
    df = universe.assign(_s=np.asarray(score, dtype=float))
    df = df[np.isfinite(df["_s"])].sort_values("_s", ascending=False)
    return [str(c) for c in df["code"].tolist()[:int(n)]]


def nan_exclusion_probe(xs: dict, dates: list[str], arm: Arm,
                       sample_days: int = 20) -> dict:
    """🚨 **未采到的票是不是「显式 NaN」而不是「被编成了别的值」**？

    ## 要证的命题

    「未采到的票**不进排名**」—— 而且它的缺席**是 NaN 造成的**，
    不是被当成「零股息」这种合法低分值。

    ## 两条证据，缺一不可

    1. **基线缺席**：那行（`divyield` = NaN）**不在** top-N 里。
    2. **注入高值后进榜**：把那行注入**池内最大的股息率** ⇒ 它**进了** top-N。

    ⇒ 两条合起来才是「**它本来是可以被选中的，只是因为 NaN 才没被选中**」
    ⇒ **排除是 NaN 的功劳**。
    ⚠️ 只有第 1 条**没有判别力**：被当成「零股息」也一样会缺席。

    ## 🚨 为什么「注入 0」单独**没有**判别力（任务书给的那个探针）

    若那票被当成「零股息」⇒ 它的分数**很低** ⇒ **进不了 top-N**
    ⇒ 读数**照样不变** ⇒ 与「它被正确排除」**逐位相同**
    ⇒ 那个探针**分不出这两种情形**（第 190 层同族）。
    ⇒ 本函数**照样跑**「注入 0」那一半并把它的读数印出来，
    但**明确标注它不承载判别力**；判别力全部来自「注入高值」那一半。

    ## 🚨 第一版的探针是**恒真的**（已修）

    第一版取 top-N 用的是**行序**（`uni.loc[score.notna(), "code"][:n]`）
    ⇒ 注入前后行序**完全一样** ⇒ 探针**永远**报「已排除」
    ⇒ 它对「那行其实进了排名」**结构上失明**。
    ⇒ 现在按**分数降序**取（`_top_codes_by_score`），
    并且守卫里有一条**反向正控**（有限高值那一行本来就该进 top-N）。
    """
    from aplus.factor import composite_score
    w = FACTOR_DEFS[arm.factor_def]["weights"]
    if arm.factor_def == "ivol":
        return {"applicable": False,
                "reason": "该臂的因子定义不含股息率 ⇒ 没有「未采到」这一说"}
    checked = 0
    n_nan_rows = 0
    n_baseline_contains = 0          # 基线里那行进了 top-N 的天数（**必须为 0**）
    n_entered_after_high = 0         # 注入高值后那行进了 top-N 的天数（**必须 > 0**）
    n_changed_high = 0
    n_changed_zero = 0
    for d in dates[:max(int(sample_days), 1)]:
        uni = xs.get(d)
        if uni is None or uni.empty:
            continue
        if DIVYIELD_COL not in uni.columns:
            return {"applicable": False, "reason": "横截面里没有股息率列"}
        nan_mask = uni[DIVYIELD_COL].isna().to_numpy()
        n_nan_rows += int(nan_mask.sum())
        if not nan_mask.any():
            continue
        nan_codes = {str(c) for c in uni.loc[nan_mask, "code"]}
        base = composite_score(uni, w)
        base_top = set(_top_codes_by_score(uni, base, arm.n))
        if nan_codes & base_top:
            n_baseline_contains += 1
        finite = uni[DIVYIELD_COL].dropna()
        hi_val = float(finite.max()) if len(finite) else 1.0
        for tag, val in (("zero", 0.0), ("high", hi_val)):
            mod = uni.copy()
            mod.loc[nan_mask, DIVYIELD_COL] = val
            sc2 = composite_score(mod, w)
            top2 = set(_top_codes_by_score(mod, sc2, arm.n))
            changed = (top2 != base_top)
            if tag == "high":
                if changed:
                    n_changed_high += 1
                if nan_codes & top2:
                    n_entered_after_high += 1
            else:
                if changed:
                    n_changed_zero += 1
        checked += 1
    if checked == 0:
        return {"applicable": False,
                "reason": ("横截面里**没有**未采到的票（池 = 采到的子集）"
                           "⇒ 排除是**结构性的**（那票根本不在面板里）"
                           "⇒ 没有可注入的对象（也不需要）")}
    excluded = bool(n_baseline_contains == 0 and n_entered_after_high > 0)
    return {
        "applicable": True,
        "n_days_checked": checked,
        "n_nan_rows_seen": n_nan_rows,
        "n_days_nan_row_in_baseline_topn": n_baseline_contains,
        "n_days_nan_row_entered_after_high_injection": n_entered_after_high,
        "n_days_changed_by_high_injection": n_changed_high,
        "n_days_changed_by_zero_injection": n_changed_zero,
        "excluded": excluded,
        "verdict": ("**未采到的票被显式排除**"
                    "（基线缺席 + 注入池内最大值后能进榜 ⇒ 缺席是 NaN 造成的）"
                    if excluded else
                    "🚨 **排除没被证实**（基线里它就进了榜，或注入高值它也进不去）"),
        "why_zero_injection_alone_cannot_prove_it": (
            "🚨 **只注入 0 没有判别力**：被当成「零股息」⇒ 分数低 ⇒ 进不了 top-N ⇒ "
            "读数照样不变 ⇒ 与「被正确排除」**逐位相同**。"
            "⇒ 判别力全部来自「注入高值」那一半。"),
    }


def nan_exclusion_probe_end_to_end(xs: dict, dates: list[str], arm: Arm,
                                   res_baseline: pd.DataFrame | None) -> dict:
    """**产物级**的同一探针：把未采到的票的股息率**就地编成 0** 重跑整条臂
    ⇒ 毛超额必须**逐位不变**。

    ⚠️ 只在横截面里**确实存在**未采到的票时才有意义
    （`--pool covered` 时池 = 采到的子集 ⇒ 排除是**结构性的**，没有可注入的行）。
    """
    w = FACTOR_DEFS[arm.factor_def]["weights"]
    n_nan_codes = 0
    for d in dates:
        uni = xs.get(d)
        if uni is None or uni.empty or DIVYIELD_COL not in uni.columns:
            continue
        n_nan_codes = max(n_nan_codes, int(uni[DIVYIELD_COL].isna().sum()))
    if n_nan_codes == 0:
        return {"applicable": False,
                "reason": ("横截面里**没有**未采到的票（池 = 采到的子集）"
                           "⇒ 排除是**结构性的**（那票根本不在面板里）"
                           "⇒ 注入探针无从下手（且不需要）")}
    if res_baseline is None:
        return {"applicable": False, "reason": "基线没有结果"}
    return {"applicable": True, "n_nan_codes_in_pool": n_nan_codes,
            "note": "端到端注入探针（需要池里有未采到的票）"}


def scoreable_counts(xs: dict, dates: list[str], weights: dict) -> dict:
    """逐日**可算成分数**。

    🚨 **复用 `aplus/backtest.py::scoreable_count`**（本仓的单一真相源）：
    它就是「这个横截面里能打出分数的股票数」那一个数
    （任一因子 NaN ⇒ 该股不可打分 ⇒ 不进排名，设计意图：宁缺毋滥）。
    ⇒ **不许**在这里另写一份（两份副本之间没有守卫）。
    """
    from aplus.backtest import scoreable_count
    counts: list[int] = []
    for d in dates:
        uni = xs.get(d)
        if uni is None or uni.empty:
            continue
        counts.append(int(scoreable_count(uni, weights)))
    if not counts:
        return {"min": None, "median": None, "mean": None, "max": None,
                "n_days": 0, "daily": []}
    arr = np.asarray(counts, dtype=float)
    return {"min": int(arr.min()), "median": float(np.median(arr)),
            "mean": float(arr.mean()), "max": int(arr.max()),
            "n_days": int(arr.size), "daily": counts}


def _perm_transform(rep_seed: int):
    """随机对照臂的 `score_transform`：**逐日置换分数**。

    🚨 **同池、同时轴、同成本模型**：置换发生在 `composite_score` 之后、
    `notna` 过滤之前（`aplus/predict.py` 的注入点）⇒
    **能进池的票一个不少、日期轴一天不差、成本模型一模一样**，
    只有「谁排前面」是随机的。

    🚨 **不许 `hash(day)`**（`PYTHONHASHSEED` ⇒ 不可复现的阴性等于没跑）
    ⇒ 用 `sha256(rep_seed|day)` 取种子。
    """
    def tf(day, df):
        if df is None or "score" not in df.columns:
            return df
        seed = int(hashlib.sha256(f"{rep_seed}|{day}".encode("utf-8")).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)
        vals = df["score"].to_numpy(dtype=float).copy()
        # 🚨 **只置换有限值，NaN 的位置原样不动**：
        # `rng.shuffle` 会把 NaN 也换到别的行去 ⇒ **「谁有资格进池子」被换掉了**
        # ⇒ 随机臂与策略臂的池子就不再是同一个 ⇒ 测到的差分不清来自排序还是池子。
        finite = np.isfinite(vals)
        perm = rng.permutation(int(finite.sum()))
        out_vals = vals.copy()
        out_vals[finite] = vals[finite][perm]
        out = df.copy()
        out["score"] = out_vals
        return out
    return tf


def sign_swap_diagnostic(orig_res: pd.DataFrame | None,
                         flip_res: pd.DataFrame | None, k: int) -> dict:
    """**机制层**的负控诊断：把 score 取负之后，选股**真的**换了吗？

    ## 为什么需要它（预注册 §6 C4 的一个洞）

    预注册的 C4 判据是「翻转后的**超额**显著为正 ⇒ 管线有偏」。
    🚨 **但超额里有市场腿**：市场涨 30% 的年份里，即使**最低分**的一档
    也可能跑赢抽样市场 ⇒ **翻转后的超额也可能显著为正** ⇒
    预注册的判据在**单边市**里可能**误触发**（把整套判决作废）。

    ⇒ 本诊断**去掉市场腿**：比较「原臂涨腿逐日收益」与「翻转臂涨腿逐日收益」
    的**差**。同一个池、同一条日期轴 ⇒ 差值里**没有大盘**。
    ⇒ **期望**：差值**显著为正**（因子把更好的那一半挑了出来）。
    ⇒ 若差值 ≈ 0 ⇒ 「把符号翻过来，选股结果没变」⇒ **管线不看符号**。

    ⚠️ 本诊断**不替代**预注册的 C4 判据（INVALID 仍由 C4 触发），
    它是**把 C4 的误触发暴露出来**的读数。
    """
    bn = load_backtest_walk_forward()
    if orig_res is None or flip_res is None or not len(orig_res) or not len(flip_res):
        return {"available": False, "reason": "原臂或翻转臂没有结果"}

    def _up_daily(res):
        up = res[res["direction"] == "up"]
        if up.empty:
            return None
        s = up.groupby("pred_date")["actual_pct"].mean().astype(float)
        return s[np.isfinite(s)]

    a = _up_daily(orig_res)
    b = _up_daily(flip_res)
    if a is None or b is None or not len(a) or not len(b):
        return {"available": False, "reason": "涨腿逐日收益算不出来"}
    common = a.index.intersection(b.index)
    if len(common) < 3:
        return {"available": False, "reason": f"共同日期只有 {len(common)} 天"}
    d = (a.loc[common] - b.loc[common]).astype(float)
    t, n = bn._t(d, block=k)
    return {
        "available": True,
        "mean_swap_annual_pct": float(d.mean()) * R * 100.0,
        "t_swap": t,
        "n_independent_obs": n,
        "n_common_days": int(len(common)),
        "caliber": ("**去掉市场腿**的差（原臂涨腿 − 翻转臂涨腿），"
                    "逐日配对 ⇒ 没有大盘污染。"),
        "expectation": ("t_swap 应**显著为正**（符号翻了 ⇒ 选股换了 ⇒ "
                        "收益差是实的）；t_swap ≈ 0 ⇒ 管线不看符号。"),
    }


def _negate_transform():
    """符号翻转负控的 `score_transform`：**把 score 取负**。

    🚨 置换**分数**而不是改权重：这样池子、日期轴、执行闸、成本全部逐位相同，
    **唯一的差别是「谁排前面」反了** ⇒ 差异只能来自方向。
    """
    def tf(day, df):
        if df is None or "score" not in df.columns:
            return df
        out = df.copy()
        out["score"] = -df["score"].to_numpy(dtype=float)
        return out
    return tf


def run_arm(xs: dict, dates: list[str], arm: Arm, *,
            score_transform=None) -> pd.DataFrame | None:
    """跑一个臂 ⇒ `evaluate_weights` 的结果表。

    ## 🚨 **逐年跑**（与 `measure_deployed_d2.run_config` 同口径）

    `evaluate_weights(dates=ydates)` 的**调仓网格是按 `ydates` 的下标取模**的
    （`aplus/rebalance.py::is_rebalance_day`）⇒ 传整窗日期与传逐年日期
    **调仓日不同** ⇒ 分块 t 的观测数不同。
    ⇒ 本函数与仓内既有测量器**同样逐年跑再拼**，
    免得「同一个 K」在两处落在不同的调仓日上。
    """
    from aplus.backtest import evaluate_weights
    w = FACTOR_DEFS[arm.factor_def]["weights"]
    by_year: dict[int, list[str]] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    frames = []
    for _y in sorted(by_year):
        res_y = evaluate_weights(xs, weights=w, top_n=arm.n, bottom_n=arm.n,
                                 dates=by_year[_y], rebalance_every=arm.k,
                                 execution_gate=True,
                                 score_transform=score_transform)
        if res_y is not None and not res_y.empty:
            frames.append(res_y)
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    return out if not out.empty else None


def rebalance_days_for(dates: list[str], arm: Arm) -> set[str]:
    """该臂的**调仓日**（逐年日历，与 `evaluate_weights` 内部同口径）。"""
    from aplus.rebalance import is_rebalance_day
    by_year: dict[int, list[str]] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    out: set[str] = set()
    for _y, ydates in by_year.items():
        for d in ydates:
            if is_rebalance_day(ydates, d, arm.k):
                out.add(d)
    return out


def measure_arm(res: pd.DataFrame | None, arm: Arm, *,
                n_scoreable: dict, xs: dict | None = None,
                dates: list[str] | None = None) -> dict:
    """一个臂的**因子层**测量（毛/净/sd/MDE/分块 t/命中率/detectable）。"""
    bn = load_backtest_walk_forward()
    md2 = load_measure_deployed_d2()
    out: dict = {
        "arm_id": arm.arm_id,
        "factor_def": arm.factor_def,
        "factor_label_cn": FACTOR_DEFS[arm.factor_def]["label_cn"],
        "k": arm.k, "n": arm.n, "role": arm.role,
        "is_main_arm": arm.is_main,
        "weights": dict(FACTOR_DEFS[arm.factor_def]["weights"]),
        "scoreable": n_scoreable,
    }
    if res is None:
        out["note"] = "该臂没有可判定的逐日序列（面板为空或无成交观测）"
        return out

    gross_parts = bn._daily(res.assign(cost=0.0), return_parts=True)
    if gross_parts is None:
        out["note"] = "该臂没有可判定的逐日序列"
        return out
    gross, market = gross_parts
    st = md2.tier_stats(gross.to_numpy(float))
    out["gross_excess_annual_pct"] = float(st["arith_ann"]) * 100.0
    out["gross_excess_note"] = ("**算术** mean × 244（超额腿的年化一律读这一列；"
                                 "🚨 超额是**差序列**，对它算复利 CAGR 得到的是"
                                 "另一个量，仓库别处实测差 ~10pp）")
    _sd = annual_sd(gross)
    out["annual_sd_pct"] = _sd["annual_sd_pct"]
    out["annual_sd_fraction"] = _sd["annual_sd_fraction"]
    mde = mde_annual_pct(gross, arm.k)
    out["mde_annual_pct"] = mde["mde_annual_pct"]
    out["mde_annual_fraction"] = mde["mde_annual_fraction"]
    out["mde_method"] = mde["method"]
    # 🚨 **分块 ingredients**（落进产物 ⇒ 外部可对账）：
    # `t_blocked`（`_tstat` 算）与 `mde_annual_pct`（`power_floor(块均值)` 算）
    # 是**两条不同的代码路径**算出来的 ⇒ 二者的**一致性**是一条真交叉核对
    # （第 176 层：恒真的恒等式定不了语义，而这两条**不是**同一个函数）。
    bm = block_means(gross, arm.k)
    out["block_ingredients"] = {
        "n_blocks": int(bm.size),
        "block_mean_daily": (float(bm.mean()) if bm.size else None),
        "block_sd_daily": (float(bm.std(ddof=1)) if bm.size > 1 else None),
        "t_power": float(load_factor_attribution().T_POWER_TARGET),
        "reconciliation": ("mde_annual_pct == t_power × R × block_sd_daily / √n_blocks；"
                           "t_blocked == (block_mean_daily × R) / (t_power × mde_annual_pct/100)"
                           " —— 两条**不同代码路径**算出来的 ⇒ 可对账"),
    }
    out["n_independent_obs"] = mde["n_independent_obs"]
    t_blocked, n_obs = bn._t(gross, block=arm.k)
    out["t_blocked"] = t_blocked
    out["n_independent_obs_from_t"] = n_obs
    out["detectable"] = bool(out["gross_excess_annual_pct"] is not None
                              and out["mde_annual_pct"] is not None
                              and abs(out["gross_excess_annual_pct"]) > out["mde_annual_pct"])
    out["detectable_note"] = ("|毛超额年化| > 年化 MDE（分块口径）"
                               "⇒ 这个样本量**能分辨**这个量级的效应")
    out["hit_rate"] = hit_rate_per_trade(res)
    # 排序力（命中率随分数单调）—— 能力守卫的对象
    if xs is not None and dates is not None:
        pairs = score_hit_pairs(xs, dates, arm, res)
        out["sorting_power"] = hit_rate_by_score_bucket(pairs)
    else:
        out["sorting_power"] = {"monotone": None, "buckets": [], "n": 0,
                                "reason": "没有横截面，排序力没算"}

    # 净（成本档）—— 判决用 `bt003`（预注册 C2）
    nets = bn._net_by_cost(res, None, arm.n, arm.k)
    out["net_by_cost_model"] = {}
    for tag, ser in nets.items():
        if ser is None or not len(ser):
            continue
        s2 = md2.tier_stats(ser.to_numpy(float))
        out["net_by_cost_model"][tag] = {
            "model": ("turnover × 0.003（aplus/backtest.py::STOCK_COST_DEFAULT，"
                      "**无 ¥5 固定额**）" if tag == "bt003" else
                      "turnover × real_cost_for(n, K)（**无 ¥5 固定额**）"),
            "annual_pct": float(s2["arith_ann"]) * 100.0,
            "mde_annual_pct": float(s2["mde_as_annual_pct"]) * 100.0,
        }
    out["gross_series"] = gross
    out["market_series"] = market
    return out


def build_bundle(res: pd.DataFrame, daily_map: dict, price_map: dict,
                 rebalance_days: set[str], arm: Arm) -> dict:
    """账户引擎要的那份 bundle（逐票收益 + 调仓日篮子 + 整手股数）。

    🚨 **复用 `measure_deployed_d2` 的口径**：篮子的调仓日由
    `aplus/rebalance.py::is_rebalance_day` 判，**不是**「买的是否为空」
    （空篮子的调仓日仍然要清仓）。
    """
    md2 = load_measure_deployed_d2()
    name_ret: dict[str, dict] = {}
    baskets: dict[str, list] = {}
    lot_sizes: dict[str, int] = {}
    entry_px = price_map["entry"]
    up = res[res["direction"] == "up"]
    for day, grp in up.groupby("pred_date"):
        rr: dict[str, float] = {}
        for _i, r in grp.iterrows():
            if r["actual_pct"] is not None and np.isfinite(r["actual_pct"]):
                rr[str(r["code"])] = float(r["actual_pct"])
                lot_sizes[str(r["code"])] = md2.lot_shares(str(r["code"]))
        name_ret[str(day)] = rr
        if str(day) in rebalance_days:
            px = entry_px.get(str(day)) or {}
            baskets[str(day)] = [(c, px.get(c)) for c in rr]
    return {"name_ret": name_ret, "baskets": baskets, "lot_sizes": lot_sizes}


def account_for(bundle: dict, dates_all: list[str], arm: Arm, *,
                capital: float = CAPITAL) -> dict:
    """账户层（**复用 `measure_deployed_d2` 的引擎与判决**）。

    * 仓数 = 该臂的 N；敞口 = 1.0（满仓判决口径，预注册 §4.2）
    * 每份金额 = 本金 × 敞口 ÷ 仓数 = 50,000 ÷ N
    * 整手取整 + 逐笔成本（`aplus/costs.py::FeeSchedule`）
    """
    md2 = load_measure_deployed_d2()
    n_slots = arm.n
    slot = md2.slot_budget_for(capital, EQUITY_WEIGHT, n_slots)
    leg = md2.account_leg(bundle, dates_all, label=f"{arm.arm_id}_account",
                           n_slots=n_slots, equity_weight=EQUITY_WEIGHT,
                           slot_budget=slot, capital=capital)
    tier = leg.get("tier") or {}
    leg["caliber"] = {
        "n_positions": n_slots,
        "equity_weight": EQUITY_WEIGHT,
        "slot_budget": slot,
        "slot_budget_formula": "本金 × 权益敞口 ÷ 仓数（**从配置派生**）",
        "account_model": ("整手取整 + 逐日盯市 + 逐笔成本"
                          "（aplus/costs.py::FeeSchedule）"),
        "note": ("**账户腿读 `tier.cagr`（复利）**；"
                 "超额腿读 `gross_excess_annual_pct`（算术）——"
                 "两者**不是同一个量**，不许混引。"),
    }
    leg["account_annual_ceiling_pct"] = tier.get("cagr")
    leg["arith_ann_pct"] = (None if tier.get("arith_ann") is None
                            else tier["arith_ann"] * 100.0)
    leg["mde_annual_pct"] = (None if tier.get("mde_as_annual_pct") is None
                             else tier["mde_as_annual_pct"] * 100.0)
    leg["lot_investable_rate"] = leg.get("lot_investable_rate")
    return leg


# ── CLI ───────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="红利/低波 长腿篮子的判决测量器（预注册 §4.3 判决链）")
    ap.add_argument("--start", default=WINDOW_START_PINNED)
    ap.add_argument("--end", default=WINDOW_END_PINNED)
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--pool", choices=("full", "covered", "sampled"), default="full",
                    help=("full = 全池（预注册口径）；"
                          "covered = 只用已采到分红的票；"
                          "sampled = 按代码顺序取前 N 只（**缩小池**的降本跑法，"
                          "🚨 产物会标 `pool_scope=sampled`，**不是**预注册的池）"))
    ap.add_argument("--pool-size", type=int, default=300,
                    help="`--pool sampled` 时取多少只（按代码顺序）")
    ap.add_argument("--random-reps", type=int, default=RANDOM_REPS,
                    help="随机对照臂的重复次数（预注册 = 100）")
    ap.add_argument("--no-random", action="store_true",
                    help="跳过随机对照臂（🚨 产物会写明「没跑」）")
    ap.add_argument("--random-arms", default="auto",
                    help=("随机对照臂跑哪些臂：`auto` = 主臂 + **通过覆盖闸**的臂；"
                          "`none` = 不跑；否则是逗号分隔的臂 id 清单。"
                          "⚠️ 成本：100 rep × 每个臂 ≈ 全池 1 小时 ⇒ "
                          "**12 个臂全跑要 ~20 小时** ⇒ 产物里会报实际跑了几个。"))
    ap.add_argument("--only", default="", help="只跑这些臂 id（逗号分隔）")
    return ap.parse_args(argv)


def validate_args(args) -> None:
    """**默认末端是一个判据，判据必须能被直接问**（形状 23 / 第 124 层）。"""
    if not str(args.end).strip():
        print("🚨 `--end` 不许留空：末端必须显式钉死（形状 23：一只票定全池窗口）。"
              f"当前默认 {WINDOW_END_PINNED}。", file=sys.stderr)
        raise SystemExit(2)
    if not str(args.start).strip():
        print("🚨 `--start` 不许留空。", file=sys.stderr)
        raise SystemExit(2)
    root = Path(args.data_root).resolve()
    try:
        root.relative_to(ROOT.resolve())
    except ValueError:
        print(f"🚨 `--data-root` 必须指向仓内（当前 {root}）。"
              "快照会漂且不自证（`ic_guided_race` 的老毛病）。", file=sys.stderr)
        raise SystemExit(2)


# ── 主流程 ────────────────────────────────────────────────────────

def main(argv=None) -> int:
    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**（`main()` 第一行）；传 None 会让 stamp() 整项跳过字节检查。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    args = parse_args(argv)
    validate_args(args)

    t0 = time.time()
    from aplus.backtest import ENTRY_LAG, build_cross_sections
    from aplus.snapshot import load_factors_map
    from aplus.store import LocalStore

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空", file=sys.stderr)
        return 2
    n_pool_full = len(files)
    print(f"[1/7] 读日线 {n_pool_full} 只 …", flush=True)

    div_root = collect_dividend_root(Path(args.data_root))
    covered, ledger = covered_codes_from_ledger(div_root)
    fingerprint = snapshot_fingerprint(div_root)
    print(f"    分红快照根 {div_root}（{fingerprint['n_parquet']} 份 parquet，"
          f"采到 {ledger['n_codes_covered']} 只）", flush=True)

    n_take = max(int(args.pool_size), 1)
    if args.pool == "covered":
        if not covered:
            print("🚨 `--pool covered` 但**事件层的单元账**里一只票都没采到"
                  "（采集器的采集 B 还没落盘 ⇒ 事件层不存在 ⇒ "
                  "股息率因子整条不可算）", file=sys.stderr)
            return 2
        keep = {f"{c}.SZ" for c in covered} | {f"{c}.SH" for c in covered}
        files = [p for p in files if p.stem in keep or _bare(p.stem) in covered]
        # 🚨 `--pool-size` 同时当**上限**：面板是 GB 级 ⇒ 降本跑必须能限量。
        # ⇒ 取**代码序最前**的 N 只（可复现，不看收益）。
        files = files[:n_take]
    elif args.pool == "sampled":
        files = files[:n_take]
    if not files:
        print(f"🚨 `--pool {args.pool}` 没取到票", file=sys.stderr)
        return 2
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    n_pool_built = len(daily_map)
    print(f"    日线 {n_pool_built} 只（pool={args.pool}；全池 {n_pool_full} 只）"
          f"（{time.time() - t0:.0f}s）", flush=True)

    print("[2/7] 建横截面面板 …", flush=True)
    xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    if not dates:
        print("🚨 面板为空，拒绝出判决", file=sys.stderr)
        return 2
    print(f"    横截面 {len(dates)} 个交易日"
          f"（{dates[0]} → {dates[-1]}）（{time.time() - t0:.0f}s）", flush=True)

    print("[3/7] 读分红事件层 + 逐日股息率 …", flush=True)
    cdp = load_collect_dividend_pit()
    events = cdp.load_events(div_root)
    n_events = int(len(events))
    closes = CloseLookup(daily_map)
    div = divyield_by_date(events, covered, closes, dates)
    by_date = div["by_date"]

    # 注入股息率列（**没采到的票 ⇒ NaN ⇒ 不进横截面排名**）
    n_injected = 0
    for d in dates:
        uni = xs.get(d)
        if uni is None or uni.empty:
            continue
        row = by_date.get(d) or {}
        vals = []
        for c in uni["code"]:
            v = row.get(_bare(str(c)))
            vals.append(float("nan") if v is None else float(v))
        uni[DIVYIELD_COL] = np.asarray(vals, dtype=float)
        n_injected += 1
    print(f"    事件 {n_events} 条；注入股息率列 {n_injected} 个交易日；"
          f"状态 {div['status_counts']}（{time.time() - t0:.0f}s）", flush=True)

    from aplus.rebalance import is_rebalance_day
    rebalance_days = sorted({d for d in dates if is_rebalance_day(dates, d, 1)})

    print("[4/7] 建价格表 …", flush=True)
    md2 = load_measure_deployed_d2()
    price_map = md2.build_price_map(daily_map, rebalance_days)
    print(f"    价格表 {len(price_map['entry'])} 日（{time.time() - t0:.0f}s）",
          flush=True)

    arms = list(ARMS)
    if args.only.strip():
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        arms = [a for a in arms if a.arm_id in want]
    if not arms:
        print("🚨 `--only` 没选中任何臂", file=sys.stderr)
        return 2

    print(f"[5/7] 跑 {len(arms)} 个臂 …", flush=True)
    results: dict[str, dict] = {}
    res_frames: dict[str, pd.DataFrame] = {}
    for arm in arms:
        w = FACTOR_DEFS[arm.factor_def]["weights"]
        n_score = scoreable_counts(xs, dates, w)
        res = run_arm(xs, dates, arm)
        m = measure_arm(res, arm, n_scoreable=n_score, xs=xs, dates=dates)
        results[arm.arm_id] = m
        if res is not None:
            res_frames[arm.arm_id] = res
        print(f"    [{arm.arm_id}] {arm.factor_def} K={arm.k} N={arm.n} "
              f"毛超额={m.get('gross_excess_annual_pct')} "
              f"MDE={m.get('mde_annual_pct')} t={m.get('t_blocked')} "
              f"可算成分 min={n_score['min']}（{time.time() - t0:.0f}s）", flush=True)

    print("[5b/7] 缺数据排除探针 …", flush=True)
    main_arm = next((a for a in arms if a.is_main), arms[0])
    nan_probe = nan_exclusion_probe(xs, dates, main_arm)
    nan_probe_e2e = nan_exclusion_probe_end_to_end(
        xs, dates, main_arm, res_frames.get(main_arm.arm_id))
    if nan_probe.get("applicable"):
        _pv = (f"    探针：基线缺席 {nan_probe.get('n_days_nan_row_in_baseline_topn')} 天、"
               f"注入高值后能进榜 {nan_probe.get('n_days_nan_row_entered_after_high_injection')} 天"
               f" ⇒ {'**未采到的票被显式排除**' if nan_probe.get('excluded') else '🚨 排除没被证实'}")
    else:
        _pv = f"    探针：不适用（{nan_probe.get('reason')}）"
    print(_pv, flush=True)

    print("[6/7] 对照臂（符号翻转负控 + 随机臂）…", flush=True)
    controls: dict = {"negative_control": {}, "random_control": {}}
    for arm in arms:
        res_flip = run_arm(xs, dates, arm, score_transform=_negate_transform())
        t_flip = None
        n_flip = 0
        if res_flip is not None:
            bn = load_backtest_walk_forward()
            g = bn._daily(res_flip.assign(cost=0.0))
            if g is not None and len(g):
                t_flip, n_flip = bn._t(g, block=arm.k)
        tc = t_crit_for(int(results[arm.arm_id].get("n_independent_obs") or 0) - 1)
        state = negative_control_state(t_flip, tc)
        swap = sign_swap_diagnostic(res_frames.get(arm.arm_id), res_flip, arm.k)
        controls["negative_control"][arm.arm_id] = {
            "t_flipped": t_flip,
            "n_independent_obs": n_flip,
            "t_crit": tc,
            "state": state,
            "sign_swap_diagnostic": swap,
            "market_leg_caveat": (
                "🚨 预注册 §6 C4 的判据（「翻转后**超额**显著为正 ⇒ 管线有偏」）"
                "在**单边市**里可能**误触发**：超额里含市场腿，"
                "市场大涨的年份里最低分那档也可能跑赢抽样市场。"
                "⇒ INVALID 仍**逐字**按预注册触发（不许改判据），"
                "而 `sign_swap_diagnostic`（**去掉市场腿**）把误触发暴露出来。"),
            "caliber": ("符号翻转负控：把 score 取负重跑；"
                        "**池、日期轴、执行闸、成本逐位相同**，"
                        "唯一差别是「谁排前面」反了。"),
            "rule": ("翻转后显著为正 ⇒ 负控通过 ⇒ 整套判决作废（INVALID）；"
                     "翻转后显著为负 ⇒ 按预期失败 ⇒ 管线正常。"),
        }
        print(f"    [负控 {arm.arm_id}] t_flipped={t_flip} ⇒ {state}", flush=True)

    # 随机对照臂（预注册 §6 C2：同池、同日期轴、同 K、同成本模型，100 rep）
    # ⚠️ **它只对「通过覆盖闸」的臂自动跑**（判据是数据覆盖，不是收益）；
    # **主臂（A3）永远在名单里**（预注册的主臂必须有随机对照，
    # 否则「主臂是不是随便选都行」这一问就没人答）。
    eligible = []
    for arm in arms:
        m = results[arm.arm_id]
        sc = m.get("scoreable") or {}
        cov = ((sc.get("min") or 0) / n_pool_full) if n_pool_full else None
        if arm.is_main or (cov is not None and cov >= COVERAGE_MIN):
            eligible.append(arm)
    if args.random_arms.strip() and args.random_arms.strip().lower() != "auto":
        want_r = {x.strip() for x in args.random_arms.split(",") if x.strip()}
        if want_r == {"none"}:
            eligible = []
        else:
            eligible = [a for a in eligible if a.arm_id in want_r]
    controls["random_control"].setdefault("arms", [])
    if args.no_random:
        controls["random_control"]["status"] = "skipped_by_flag"
    elif not eligible:
        controls["random_control"]["status"] = (
            "本轮**没跑**随机臂（`--random-arms none`）")
    else:
        controls["random_control"]["status"] = "ran"
        controls["random_control"]["n_reps"] = int(args.random_reps)
        controls["random_control"]["seed_base"] = RANDOM_SEED_BASE
        for arm in eligible:
            vals = []
            for r in range(int(args.random_reps)):
                seed = RANDOM_SEED_BASE + r
                rr = run_arm(xs, dates, arm, score_transform=_perm_transform(seed))
                if rr is None:
                    continue
                bn = load_backtest_walk_forward()
                g = bn._daily(rr.assign(cost=0.0))
                if g is None or not len(g):
                    continue
                vals.append(float(g.mean()) * R * 100.0)
            if not vals:
                continue
            arr = np.asarray(vals, dtype=float)
            strat = results[arm.arm_id].get("gross_excess_annual_pct")
            pct = (float((arr < strat).sum() + 0.5 * (arr == strat).sum()) / arr.size
                   if strat is not None else None)
            controls["random_control"]["arms"].append({
                "arm_id": arm.arm_id,
                "n_reps": int(arr.size),
                "gross_annual_pct_distribution": {
                    "min": float(arr.min()), "p5": float(np.percentile(arr, 5)),
                    "p25": float(np.percentile(arr, 25)),
                    "median": float(np.median(arr)),
                    "p75": float(np.percentile(arr, 75)),
                    "p95": float(np.percentile(arr, 95)),
                    "max": float(arr.max()),
                },
                "strategy_gross_annual_pct": strat,
                "strategy_percentile_rank": pct,
                "caliber": ("**同池随机抽 N 只，同 K，同日期轴，同成本模型**；"
                            "种子 = 20261021 + r（🚨 不许 `hash(day)`）。"
                            "🚨 百分位排名只作**形状陈述**，"
                            "随机臂分布本身也有 MDE。"),
            })
            print(f"    [随机 {arm.arm_id}] {arr.size} rep，"
                  f"中位 {np.median(arr):.4f}% p95 {np.percentile(arr, 95):.4f}%",
                  flush=True)

    print("[7/7] 判决链 + 落盘 …", flush=True)
    payload = assemble(results, controls, arms, args, dates, res_frames,
                       daily_map, price_map, rebalance_days,
                       n_pool_full=n_pool_full, n_pool_built=n_pool_built,
                       covered=covered, ledger=ledger, fingerprint=fingerprint,
                       events=events, div_status=div["status_counts"],
                       nan_probe=nan_probe, nan_probe_e2e=nan_probe_e2e)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                              default=_json_default), encoding="utf-8")
    provenance.stamp(
        out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        data_root=Path(args.data_root),
        extra={
            "data_root": str(Path(args.data_root).resolve()),
            "prereg": PREREG,
            "dividend_snapshot_root": str(div_root),
            "dividend_snapshot_parquet_sha256": {
                f["path"]: f["sha256"] for f in fingerprint["files"]},
            "n_pool_full": n_pool_full,
            "pool_scope": args.pool,
        })
    print(f"    → {out}（{time.time() - t0:.0f}s）", flush=True)
    _print_headline(out)
    return 0


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Series):
        return o.to_dict()
    if isinstance(o, pd.DataFrame):
        return o.to_dict()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


# ── 汇总 ───────────────────────────────────────────────────────────

def assemble(results: dict, controls: dict, arms: list[Arm], args, dates,
             res_frames: dict, daily_map: dict, price_map: dict,
             rebalance_days: list[str], *, n_pool_full: int, n_pool_built: int,
             covered: set[str], ledger: dict, fingerprint: dict,
             events: pd.DataFrame, div_status: dict,
             nan_probe: dict | None = None, nan_probe_e2e: dict | None = None) -> dict:
    """把读数组装成产物。"""
    bn = load_backtest_walk_forward()
    md2 = load_measure_deployed_d2()

    dates_all = list(dates)

    arm_blocks: dict = {}
    for arm in arms:
        m = dict(results[arm.arm_id])
        m.pop("gross_series", None)
        m.pop("market_series", None)
        sc = m.get("scoreable") or {}
        n_scoreable_min = sc.get("min")
        coverage_ratio = ((n_scoreable_min / n_pool_full)
                          if (n_scoreable_min is not None and n_pool_full)
                          else None)
        h = hurdle_for(arm.k, arm.n)
        n_obs = m.get("n_independent_obs") or 0
        tc = t_crit_for(int(n_obs) - 1)
        nc = (controls.get("negative_control") or {}).get(arm.arm_id) or {}
        nc_state = nc.get("state")

        # 账户层
        acct = None
        if arm.arm_id in res_frames:
            bundle = build_bundle(res_frames[arm.arm_id], daily_map, price_map,
                                  rebalance_days_for(dates, arm), arm)
            acct = account_for(bundle, dates_all, arm)

        verdict = decide(
            gross_annual_pct=m.get("gross_excess_annual_pct"),
            mde_annual_pct=m.get("mde_annual_pct"),
            t_blocked=m.get("t_blocked"),
            t_crit=tc,
            hurdle_annual_pct=h["hurdle_annual_pct"],
            n_independent_obs=int(n_obs),
            n_scoreable_min=(n_scoreable_min or 0),
            n_positions=arm.n,
            coverage_ratio=coverage_ratio,
            coverage_min=COVERAGE_MIN,
            negative_control=nc_state,
        )
        m.update({
            "hurdle": h,
            "t_crit_bonferroni": tc,
            "t_crit_source": (f"aplus.dist.t_crit_two_sided(0.05/{M_CANDIDATES}, "
                              f"df={int(n_obs) - 1})（**真 t 分布**，不查表）"),
            "coverage_ratio": coverage_ratio,
            "verdict": verdict,
            "verdict_wording": verdict["wording"],
            "negative_control": nc,
            "account": acct,
        })
        arm_blocks[arm.arm_id] = m

    # 整套作废判定：任一**通过覆盖闸**的臂的负控「通过」⇒ 整套作废
    n_passed = suite_invalid_arms(arms, results, controls, n_pool_full,
                                  COVERAGE_MIN)
    suite_invalid = bool(n_passed)
    suite_label = LABEL_INVALID if suite_invalid else None

    # 🚨 **两个覆盖率不是同一个量**（第 7 层）：
    #   * `data_coverage_ratio` = **采到的票数 / 全池票数**（数据侧）
    #   * `scoreable_coverage_ratio` = **每日可算成分数 / 全池票数**（测量侧，逐臂）
    # ⇒ 顶层只报**数据侧**那个；测量侧那个在**每个臂**里各报各的。
    data_cov = ((len(covered) / n_pool_full) if n_pool_full else None)
    coverage = coverage_bias_note(
        coverage_ratio=data_cov,
        n_covered=len(covered), n_pool=n_pool_full,
        n_scoreable_min=min(
            [(arm_blocks[a.arm_id].get("scoreable") or {}).get("min") or 0
             for a in arms] or [0]),
        n_positions=arms[0].n)
    coverage["data_coverage_ratio"] = data_cov
    coverage["scoreable_coverage_ratio_note"] = (
        "**逐臂**的「每日可算成分数 / 全池票数」在 `arms.*.coverage_ratio`；"
        "顶层这个是**数据侧**的（采到的票 / 全池票）——"
        "🚨 **两个不是同一个量**，不许混引。")
    coverage["n_pool_built"] = n_pool_built
    coverage["pool_scope"] = getattr(args, "pool", "full")
    # 🚨 **这一跑的覆盖口径**（三件都要说清：实际多少只 / 占全池多少 /
    # 采到的年份范围 / 未采到的是显式 NaN 还是别的）
    _pool_bare = {_bare(k) for k in (daily_map or {})}
    coverage["cross_section_size"] = n_pool_built
    coverage["cross_section_fraction_of_pool"] = (
        (n_pool_built / n_pool_full) if n_pool_full else None)
    coverage["covered_in_cross_section"] = len(
        {c for c in covered if _bare(c) in _pool_bare})
    coverage["uncovered_in_cross_section"] = max(
        0, n_pool_built - coverage["covered_in_cross_section"])
    coverage["collected_year_range"] = (
        {"announce_year_min": int(events["announce_year"].min()),
         "announce_year_max": int(events["announce_year"].max()),
         "operate_date_min": str(events["operate_date"].dropna().min()),
         "operate_date_max": str(events["operate_date"].dropna().max()),
         "n_years": int(events["announce_year"].nunique())}
        if (events is not None and len(events) and "announce_year" in events.columns)
        else None)
    coverage["uncovered_are"] = (
        "**显式 NaN**（不在排名里）—— 见 `nan_exclusion_probe`"
        if (nan_probe or {}).get("applicable")
        else "**结构性排除**（池 = 采到的子集 ⇒ 未采到的票根本不在面板里）")
    coverage["nan_exclusion_probe"] = nan_probe or {}
    coverage["nan_exclusion_probe_end_to_end"] = nan_probe_e2e or {}

    payload = {
        "artifact_meta": {
            "generated_by": GENERATOR,
            "prereg": PREREG,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "window": {"start": args.start, "end": args.end,
                       "start_actual": str(dates[0]), "end_actual": str(dates[-1]),
                       "n_days": len(dates),
                       "years": len(dates) / R},
            "R_days_per_year": R,
            "R_source": "aplus.hurdle.DAYS_PER_YEAR",
            "data_root": str(Path(args.data_root).resolve()),
            "pool_scope": args.pool,
            "is_prereg_pool": bool(args.pool == "full"),
            "n_pool_full": n_pool_full,
            "n_pool_built": n_pool_built,
            "pool_scope_note": (
                "🚨 `pool_scope = sampled` ⇒ **这不是预注册 §4.1 的池**"
                "（预注册的池 = `data/master/daily` 的全部 5,460 只）"
                "⇒ 本产物**只是降本跑**（省 GB 级面板），"
                "**不可**当测量引用。`pool_scope = full` 才是预注册口径。"),
            "m_candidates": M_CANDIDATES,
        },
        "caliber": {
            "what_this_is": ("红利/低波 **长腿**（涨腿 top-N）篮子的判决测量。"
                             "⚠️ **不是**多空口径（顶−底）。"),
            "long_short_warning": (
                "石川书里的 IVOL 1.27%/月（t=8.64，`data/books/cn_factor_lines.txt:9884-9885`）"
                "是**多空**口径（做多最低 IVOL 组 + **做空**最高 IVOL 组）"
                "⇒ **空头腿 A 股散户做不了** ⇒ **不可达**。"
                "本产物只测**长腿**，长腿的先验是「不知道」。"),
            "factor_defs": {k: {"weights": v["weights"], "label_cn": v["label_cn"],
                                "required_cols": v["required_cols"]}
                            for k, v in FACTOR_DEFS.items()},
            "dividend_yield": (
                "TTM 每股**税前**现金分红 ÷ **不复权** close；"
                "PIT 锚 = **预案公告日**（`dividPlanAnnounceDate`）；"
                "TTM 窗口按**除权除息日**滚动 365 天；"
                "**缺数据一律 NaN，绝不编 0**。"),
            "ivol": ("`aplus/factor.py::ivol_series`（面板列 `ivol`），"
                     "方向**取负**（石川 `:9845-9850`, `:9870-9885`）。"),
            "composite": "等权 z：z(股息率) + z(−IVOL)，两条腿各占一半（预注册 §2.3）。",
            "excess_caliber": ("**长腿 − 市场腿**（多头减市场），"
                               "🚨 **不是**多空价差（顶−底）——"
                               "差的那部分整份落在空头腿。"),
            "annualization": ("超额腿年化 = **算术** mean × 244；"
                              "年化 sd = sd × √244；"
                              "年化 MDE = 分块口径（观测单位 = 一次调仓）。"),
            "pct_unit": ("🚨 **本产物的所有 `*_pct` 字段都是「百分数」"
                         "（15.31 表示 15.31%）**。"
                         "⚠️ **仓内另一些产物把 `_pct` 用作分数**"
                         "（`factor_width_mde.json::widths['10'].gross_annual_pct`"
                         " = 0.0481 = 4.81%；`.MDE_annual_pct` = 0.1531 = 15.31%）"
                         "⇒ **跨产物比值前先核单位**（第 97/138 层）。"
                         "⇒ 本产物**另**给 `*_fraction` 字段，两种口径并排。"),
            "account_leg": ("账户腿读 `tier.cagr`（复利）；"
                            "超额腿读 `gross_excess_annual_pct`（算术）——"
                            "**两个不是同一个量**。"),
            "cost_models": {
                "bt003": "turnover × 0.003（**无 ¥5 固定额**）",
                "real": "turnover × real_cost_for(n, K)（**无 ¥5 固定额**）",
                "per_trade_chain": ("**逐笔**（aplus/costs.py::FeeSchedule，"
                                    "佣金万三/最低 ¥5、印花税卖出侧 0.05%、"
                                    "过户费、价差 0.15%/边）"),
            },
            "lot_size": {"main_and_growth": "100 股/手",
                         "star_market_688_689": "200 股/手",
                         "evidence_level": ("🚨 **外部交易所规则**，本仓**无数据源**")},
        },
        "wording_policy": {
            "forbidden_phrases": list(FORBIDDEN_PHRASES),
            "scope": ("**本脚本自己写的判决文案**（`arms.*.verdict.wording` 与 "
                      "`suite_verdict.wording`）。"),
            "why_scope_is_limited": (
                "⚠️ **账户层的 `account.verdict.cases` 来自仓内权威判决函数**"
                "`scripts/measure_basket_nav_01.py::judge`（**逐字引用，不重写**）"
                "⇒ 它的句子里**含**「接近」「达不到」这两个串 —— "
                "而那句话本身**是在禁止**说这两个词。"
                "⇒ 若把禁用词守卫扫到全产物，它会去判**仓库的权威判决函数**有罪，"
                "那是**把守卫的作用域搞错**（守卫过严 = 恒真/永远红 = 没人敢开）。"
                "⇒ 作用域显式写在这里，**不许**偷偷扩大。"),
            "rule": ("「测不到」与「没有」是两件事（第 16 层）："
                     "POWERLESS 写「测不到」，**不写**「无效」。"),
        },
        "candidate_set": {
            "m": M_CANDIDATES,
            "frozen": True,
            "note": ("候选集在**看到任何收益之前**写死（预注册 §5）；"
                     "事后**不许**加臂来捞主臂。"),
            "arms": [{"arm_id": a.arm_id, "factor_def": a.factor_def,
                      "k": a.k, "n": a.n, "role": a.role,
                      "is_main_arm": a.is_main} for a in arms],
            "main_arm": next((a.arm_id for a in arms if a.is_main), None),
        },
        "coverage": {
            **coverage,
            "covered_codes": sorted(covered),
            "sampling_mechanism": (
                "采集器按**分片**采（`scripts/collect_dividend_pit.py`，"
                "`shard_codes` 对**按代码排序的全池**做等距切分）"
                "⇒ 采样与股息率**无关** ⇒ 抽样本身**不带系统性方向**。"),
            "ledger": ledger,
            "snapshot_fingerprint": fingerprint,
            "two_legs_warning": (
                "🚨 **事件层读到的金额是「今天读到的」，不是「当时公告的」**"
                "（预案 → 股东大会可能改额）⇒ 本回测吃的是"
                "**带修订风险的历史**。⇒ 要「当时读到的」得靠 "
                "`aplus.vintage` 的 `date=` 分区**逐日累积**。"
                "⇒ **两条腿不许混引**。"),
            "divyield_status_counts": div_status,
            "n_events_loaded": int(len(events)),
        },
        "arms": arm_blocks,
        "controls": controls,
        "suite_verdict": {
            "label": suite_label,
            "invalidated_by": n_passed,
            "wording": WORDING[LABEL_INVALID] if suite_invalid else None,
            # 🚨 说明串由**实际状态**算出来（见 `suite_note` 的 docstring：
            # 原写法是写死的常量 ⇒ 声称了一个没发生的原因 ⇒ 误导了读的人）。
            "note": suite_note(n_passed, COVERAGE_MIN),
        },
        "what_this_cannot_prove": [
            "「测不到」**不等于**「没有超额」—— 只等于在这个样本量下测不到。",
            "🚨 **本产物不回答「红利/低波因子有没有效」**："
            "池覆盖不足时**只能**给「测不到」或「覆盖不足，本轮不可判」。",
            "🚨 **本产物不回答「能不能到年化 20%」**：那是账户口径的另一个问题，"
            "且本轮账户 MDE 远大于目标。",
            "🚨 **本产物不承诺**任何策略族「一定能到 20%」，"
            "也**不承诺**红利/低波会有效。",
            "**事件层金额带修订风险**（今天读到的 ≠ 当时公告的）。",
            "**超额腿不是账户净值**；账户净值只从 `arms.*.account.tier.cagr` 读。",
            "**MDE 是分块口径**（观测单位 = 一次调仓）⇒ "
            "与仓内既有产物 `factor_width_mde.json` 的**按天 SE 口径 MDE 不可直接比大小**。",
        ],
    }
    return payload


def _print_headline(out: Path) -> None:
    d = json.loads(out.read_text(encoding="utf-8"))
    cov = d.get("coverage") or {}
    print("\n=== 覆盖 ===")
    print(f"  覆盖 {cov.get('n_covered_codes')} / 全池 {cov.get('n_pool_codes')} 只"
          f"（{cov.get('coverage_ratio')}）")
    print("\n=== 各臂 ===")
    for aid, m in (d.get("arms") or {}).items():
        v = m.get("verdict") or {}
        print(f"  {aid:4s} 毛超额={m.get('gross_excess_annual_pct')} "
              f"MDE={m.get('mde_annual_pct')} t={m.get('t_blocked')} "
              f"⇒ {v.get('label')}")
    print("\n=== 账户腿（判决档 A3）===")
    a3 = (d.get("arms") or {}).get("A3") or {}
    acct = a3.get("account") or {}
    t = acct.get("tier") or {}
    print(f"  cagr={t.get('cagr')}  年化 MDE={t.get('mde_as_annual_pct')}  "
          f"投入率={acct.get('lot_investable_rate')}")


if __name__ == "__main__":
    raise SystemExit(main())
