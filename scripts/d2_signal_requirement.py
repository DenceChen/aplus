#!/usr/bin/env python3
"""D2（年化 20%）达标所必需的**毛超额** —— 门槛的换算，不是新测量。

预注册：`docs/PREREG_D2_SIGNAL_REQUIREMENT_2026-10-16.md`

# 这个脚本算的是什么、不是什么

账户年化 ≡ 市场腿 + 净超额（口径逐字来自
`basket_nav_01_2026-10-16.json::caliber.verdict_tier_is`）。
达标所需净超额 `x*` 已由 `required_excess()` 给出；**本脚本给的是毛额门槛**

    g*(N) = x* + annual_friction(C, 1.0, N, 244/K)

即：**要让账户到 20%，信号本身必须交出多少毛超额。** 这个数此前仓里不存在。

🚨 **它不是判决。** `g_obs < g*` 不等于「D2 达不到」，只等于
「当前配置下、当前实测信号交不出门槛要求的量」。预注册 §4 全文。

# 三条硬口径（预注册 §2.1）

- 摩擦**只调** `aplus.costs.annual_friction`，不在本文件重写公式
  （第 5 层：断言 == 公式，公式错了断言跟着一错）
- `equity_weight = 1.0`（单边多头）；0.5 会让摩擦减半、口径改变
- `rounds_per_year = 244/K`，**不取整**（K=21 时取整 12 会抬摩擦下界 4.787% → 4.944%）。
  🚨 **K 从 `aplus/evolve.py::LIVE_REBALANCE_EVERY` 来（单一真相源）**，不写死 ——
  写死 `21` 会让 K 变更后本台账静默留在旧 K 口径上（同一个页面上两块数字来自两个 K）。

# 🚨 量纲（预注册 §2.2，仓里最贵的一条）

上游三份产物**三种量纲，无一声明**：

| 文件::键 | 值 | 量纲 |
|---|---|---|
| `factor_width_mde.json::widths[N].gross_annual_pct` | 0.0481 | **分数**（名字带 `_pct`） |
| `factor_width_mde.json::widths[N].net_width_aware_annual_pct` | −0.016030 | **分数** |
| `market_nav_caliber.json::equal_weight.geometric_annual_pct` | 9.9471 | **真百分数** |
| `basket_nav_01_2026-10-16.json::...required_excess_annual` | 0.091434 | **分数** |

判定**只用绝对外部锚**「年化收益率不可能低于 −100%」，不用字段名、不用容差
（第 182 层：量纲守卫钉住生产者，对消费者零覆盖）。
本文件里每一次读法都是**显式的一处换算**，不许有第二个入口。

# D_NO_DATA 绝不回落（预注册 §3 分支 D）

上游任一键缺失 → `require()` 抛 `KeyError` 并点名缺哪个键。
**不许 `.get(..., 默认值)`**：缺低尾档就用全截面顶上 = 口径不一致。

# 🚨 同一个 `headline` 里**有两个**「还差几倍」（2026-10-21 补）

`headline.multiple_needed_at_50k_deliverable` = **2.3450** 这个数**不是缺口**：
它的**分子**是 **K=63** 的要求（`LEDGER_K`），
而它的**分母**是 `factor_width_mde.json::widths["10"]` —— **K=21 的研究臂**
（8 因子逐年 IC 定权）⇒ **K 与信号两处口径都不同**（第 7 层：
两个各自都对的数放一起比）。

⇒ 本脚本现在**并排给两列**：

| 列 | 口径 | 倍数 |
|---|---|---|
| `same_caliber` | **同口径**：K=63 · 生产臂 `LIVE_DEFAULT` · 榜宽 10 · ¥5 万 · 满仓 · 同一等权市场腿 | **≈1.79×** |
| `cross_caliber` | **跨口径**：K=21 研究臂 ÷ K=63 的要求（**老的那一列，保留并显式标注**） | 2.3450（**不是缺口**） |

⚠️ **要求那一个数一个字没动**：`required_gross_at_50k_deliverable` 仍是
**11.2813%**（判据的输入，预注册的）⇒ 本节**只加一列读数**，不改判据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance
from aplus.costs import (
    COMMISSION_MIN,
    COMMISSION_RATE,
    SPREAD_ONE_WAY,
    STAMP_DUTY_SELL,
    TRANSFER_FEE,
    annual_friction,
)
# 🚨 K 的**单一真相源**：`aplus/evolve.py::LIVE_REBALANCE_EVERY`。
# 此前本文件把 `244/21` **写死** ⇒ K 从 21 改成 63 之后它没跟着变
# ⇒ 页面 `/api/d2-gap` 给的是 **K=21 口径**的缺口，而同一页上的
# `deployed_d2_verdict.json::verdict`（实盘真相）已经是 K=63 口径
# ⇒ **同一个页面上两块数字来自两个不同的 K** ⇒ 打架。
# ⇒ 现在 K 只从这一个常量来，写死的 `21` 没有了。
# ⚠️ **「不取整」这条纪律一字未动**：改的只是「那个 K 从哪里来」，
# 不是「K 怎么用」。`244/K` 仍是**精确分数**（不 round）。
from aplus.evolve import LIVE_REBALANCE_EVERY

REPORTS = ROOT / "data" / "reports"
GENERATOR = "scripts/d2_signal_requirement.py"
OUT = REPORTS / "d2_signal_requirement.json"

# 预注册 §2.1 C3：精确 244/K，**不取整**
# 🚨 K 来自 `aplus/evolve.py::LIVE_REBALANCE_EVERY`（单一真相源），
# **不再写死 21** ⇒ 改 K 时本台账自动跟着改，不会留下 K=21 的化石读数。
ROUNDS_PER_YEAR = 244 / LIVE_REBALANCE_EVERY
#: 本台账用的 K（**判决档那个是 21** ⇒ 两组数不可混比，见 caliber 块）
LEDGER_K = int(LIVE_REBALANCE_EVERY)
# 预注册 §2.1 C2：单边多头口径
EQUITY_WEIGHT = 1.0
# 预注册 §2.1 C1：唯一摩擦入口
FRICTION = annual_friction
# 「年化收益率不可能低于 −100%」——**绝对外部锚**，不需要公式也不需要容差
IMPOSSIBLE_RETURN_FLOOR = -1.0
# 判决臂是 equal_weight（预注册）；cap_weight 是对照臂（第 183 层）
ARM = "equal_weight"
# 可交付形态：每天 10 涨 10 跌 ⇒ 涨腿 10 只
DELIVERABLE_N = 10
# 5 万本金
CAPITAL = 50_000.0
# 供「本金 → ∞」用的替身：只要求 > COMMISSION_MIN_BINDS_BELOW，
# 使最低佣金不生效，于是摩擦退化成纯比例费率 = **可证的正下界**
INFINITE_CAPITAL = 1e12

# ── 生产臂（同一 K / 同一信号 / 同一榜宽 / 同一市场腿）────────────────
# 🚨 台账这一族算的是 **K=63**（实盘配置）的「所需毛超额」（`LEDGER_K`），
#    而 `measured_gross_best.at_deliverable_width` 那个 4.8108% 来自
#    `factor_width_mde.json::widths["10"]` —— **K=21 的研究臂**（8 因子逐年 IC 定权）
#    ⇒ 分子分母的 **K 与信号两处口径都不同** ⇒ 2.3450 **不是「还差几倍」**。
# ⇒ 生产臂 = `deployed_d2_verdict.json::verdict.config` 那一格
#    （它的 `production_sources.signal` 逐字写着
#    「aplus/factor.py::DEFAULT_WEIGHTS（weights.json 不存在 ⇒ 回落）」）。
# ⚠️ 生产臂那一格的标签**由口径常量拼出来**，不许手打
#    （K 一变标签跟着变 ⇒ 不会静默停在旧 K 的格上 —— 与 `LEDGER_K` 同一真相源）。
PRODUCTION_SIGNAL = "LIVE_DEFAULT"
PRODUCTION_FILTER_ARM = "ARM_A"
# 对照臂（**非生产、非判决**）：同 K / 同榜宽 / 同市场腿，**信号不同**
# ⇒ 它们的倍数只作对照，**不许**替代生产臂那个
# （第 183 层：加对照臂，不换判据臂 —— 预注册的判决臂不许因为某个臂好看就换）。
CONTROL_ARMS = (("BACKTEST_IC", "ARM_A"), ("BACKTEST_IC", "ARM_C"))
DEPLOYED_ARTIFACT_NAME = "deployed_d2_verdict.json"


def require(container: dict, *path: str):
    """按路径取值，缺键就点名抛 —— **绝不回落**（预注册 §3 分支 D）。"""
    cur = container
    for i, key in enumerate(path):
        if key not in cur:
            raise KeyError(
                f"{GENERATOR}: 缺上游键 {' -> '.join(path[:i + 1])}"
                f"（分支 D_NO_DATA，绝不回落）"
            )
        cur = cur[key]
    return cur


def calib_frac(pct: float, scale: float = 100.0) -> float:
    """真百分数 → 分数。`scale` 显式必传且**在本仓只有 100 这一个取值**
    （写出来是为了让「除以 100」这一步在代码里可见，而不是藏在读法里）。"""
    return pct / scale


def net_excess(gross: float, friction: float) -> float:
    """净超额 = 毛超额 − 摩擦。**两条腿都必须是同量纲的年分数**。"""
    return gross - friction


def friction_at(capital: float, n_positions: int,
                equity_weight: float = EQUITY_WEIGHT) -> float:
    """摩擦的唯一入口。第 5 层：公式住在 `aplus.costs`，这里一行都不重写。"""
    return FRICTION(capital, equity_weight, n_positions, ROUNDS_PER_YEAR)


def required_gross(required_excess: float, capital: float, n_positions: int,
                   equity_weight: float = EQUITY_WEIGHT) -> float:
    """`g* = x* + friction`。**只做一次加法。**

    ⚠️ 任何一次再乘/除 `years` 就是**第二次年化**（第 181 层：好动机给出错误数）。
    `required_excess` 与 `friction` 各自**已经是年分数**，这里不碰时间。
    """
    return required_excess + friction_at(capital, n_positions, equity_weight)


def friction_floor(n_positions: int,
                   equity_weight: float = EQUITY_WEIGHT) -> float:
    """本金 → ∞ 时的摩擦下界。

    ★ **可证 > 0**：`COMMISSION_MIN_BINDS_BELOW = 5/0.0003 = ¥16,667`，
    每笔本金一旦超过它，佣金 = `per × rate` 且与本金无关 ⇒ 剩下的成本
    （印花税 0.05% 卖出 + 过户费 + 价差 0.15% 单边 × 2 轮）**与本金无关**
    ⇒ **本金买不到零成本**（第 5 层的正面陈述，不是估计）。
    """
    return friction_at(INFINITE_CAPITAL, n_positions, equity_weight)


def ceiling_net_excess(gross: float, n_positions: int) -> float:
    """**净超额的绝对上限**（本金 → ∞，最低佣金不再起作用）。

    同一个 N 上比：毛超额 − **该 N 自己的**摩擦下界。
    ⚠️ 不许拿 N≤3 的下界去抵 N=10 的毛超额（跨档比较，第 7 层）。
    """
    return net_excess(gross, friction_floor(n_positions))


# ── 闭式：与上面那条**推导路径不同**，所以才有判别力 ──────────────
# 代码路径：per = notional/N → N 次 round_trip(per) 求和
# 闭式路径：N × (notional/N) × K = notional × K，**N 直接约掉**
#   K = 2×(commission + transfer_fee + spread) + stamp_duty
#     = 2×(0.0003 + 0.00001 + 0.0015) + 0.0005 = 0.00412
# ⇒ friction_floor = equity_weight × K × rounds_per_year
#
# ⚠️ 容差由推导给，不拍脑袋：float64 朴素求和 n 项的相对误差界 O(n·ε)，
#   最大档 n=546 时 546 × 2.22e-16 ≈ 1.2e-13 ⇒ 取 1e-12 留一个数量级余量。
#   （第 178 层：没有阈值的「一致」等于没有判据。）
FLOOR_REL_TOL = 1e-12
_ONE_WAY_LINEAR = COMMISSION_RATE + TRANSFER_FEE + SPREAD_ONE_WAY
_ROUND_TRIP_LINEAR = 2 * _ONE_WAY_LINEAR + STAMP_DUTY_SELL


def friction_floor_closed_form(equity_weight: float = EQUITY_WEIGHT) -> float:
    """摩擦下界的**代数闭式**，**不含 `n_positions`** —— 这正是要证明的性质。

    只在最低佣金不咬人时成立（即 `per > COMMISSION_MIN / COMMISSION_RATE`），
    而 `INFINITE_CAPITAL = 1e12` 对任何 realistic N 都满足。
    """
    return equity_weight * _ROUND_TRIP_LINEAR * ROUNDS_PER_YEAR


def friction_floor_is_width_independent(widths) -> tuple[bool, float]:
    """实测下界在五档上是否**同一个数**，以及最大档间相对差。

    ⚠️ 这条**不是**恒真：`friction_at` 里若有人改过 `rebalance` 的均分逻辑
    （例如先求和再乘费率，`costs.py` 的 docstring 明写那是错的），
    实测下界就会随 N 变 ⇒ 这里立刻返回 False。
    """
    vals = [friction_floor(n) for n in widths]
    lo, hi = min(vals), max(vals)
    rel = 0.0 if lo == 0 else abs(hi - lo) / abs(lo)
    return (rel <= FLOOR_REL_TOL), rel


def friction_floor_matches_closed_form(n_positions: int) -> tuple[bool, float]:
    """实测下界 vs 闭式，**两条推导路径**互相对拍（第 5 层的正解）。"""
    got = friction_floor(n_positions)
    want = friction_floor_closed_form()
    rel = abs(got - want) / abs(want)
    return (rel <= FLOOR_REL_TOL), rel


def decide(rows: list[dict]) -> str:
    """预注册 §3 的四分支。**顺序是判据**（§3.2）：C 必须先于 A/B。

    - `C` = 全部档 `|g_obs| / MDE < 1` ⇒ **连符号都定不了** ⇒ 只准形状陈述。
      先检它，是为了**不**输出「达不到」这个乐观方向的结论（第 160 层）。
    - `B` = 全部档 `g_obs ≥ g*`（配置侧能封口），先于更弱的 `A`。
    """
    if all(r["measured_gross_over_mde_abs"] is not None
           and r["measured_gross_over_mde_abs"] < 1.0 for r in rows):
        return "C_UNMEASURABLE"
    shortfalls = [r["measured_over_required"] for r in rows]
    if all(s is not None and s >= 1.0 for s in shortfalls):
        return "B_CONFIG_COULD_CLOSE_IT"
    if any(s is not None and s < 1.0 for s in shortfalls):
        return "A_SIGNAL_SHORTFALL_IS_SIGNAL_SIDE"
    return "D_NO_DATA"


# ── 同口径那一列（2026-10-21 补）────────────────────────────────────
# ⚠️ 这一族**全部是纯函数**：不写盘、不读全局状态 ⇒ 守卫可以直接 import
#    本模块调用它们 ⇒ **变异改生成器时守卫立刻红**（第 96 层：
#    「读落盘 json 的守卫与代码毫无关系」）。


def production_cell_label(k: int = LEDGER_K,
                          equity_weight: float = EQUITY_WEIGHT) -> str:
    """生产臂那一格的标签 —— **由口径常量拼出来**，不手打。

    ⚠️ 标签里带着 K 与满仓档 ⇒ K 一变标签跟着变 ⇒ 不会静默停在旧 K 的格上。
    """
    return (f"{PRODUCTION_SIGNAL}|{PRODUCTION_FILTER_ARM}"
            f"|K{k}|E{int(round(equity_weight * 100))}")


def k_axis_measurement(deployed: dict, cell_label: str) -> dict:
    """从 `deployed_d2_verdict.json::k_grid.cells[label].k_axis` **逐位**读一格。

    ⚠️ **只读不改算**：实测毛超额**不许**用公式重算（第 109 层）。
    """
    k_axis = require(deployed, "k_grid", "cells", cell_label, "k_axis")
    return {
        "cell": cell_label,
        "k": require(k_axis, "K"),
        "signal": require(k_axis, "signal"),
        "filter_arm": require(k_axis, "filter_arm"),
        "n_positions": require(k_axis, "n_positions"),
        "equity_weight": require(k_axis, "equity_weight"),
        "measured_gross_annual_pct": require(k_axis, "gross_excess_annual_pct"),
        "source": (f"data/reports/{DEPLOYED_ARTIFACT_NAME}::k_grid.cells"
                   f'["{cell_label}"].k_axis.gross_excess_annual_pct'),
    }


def check_ledger_caliber(meas: dict) -> None:
    """**任何**一格都要过的口径自检：K / 榜宽 / 敞口。

    ⚠️ 这一条**不是**恒真：`deployed_d2_verdict.json::k_grid` 里有
    K=5/10/21/42/63 与 E50/E100 共 40 格 ⇒ 拿错一格（K 不符 / 半仓）
    这里立刻响 ⇒ **跨口径的数进不来**（第 7 层）。
    """
    for name, key, want in (("K", "k", LEDGER_K),
                            ("n_positions", "n_positions", DELIVERABLE_N),
                            ("equity_weight", "equity_weight", EQUITY_WEIGHT)):
        got = meas[key]
        if got != want:
            raise AssertionError(
                f"{GENERATOR}: 那一格（{meas['cell']}）的 {name} = {got!r}，"
                f"与台账要求的口径 {want!r} 不同 ⇒ **跨口径**，"
                f"不许拿它算倍数（第 7 层：两个各自都对的数放一起比）")


def check_production_arm(meas: dict) -> None:
    """生产臂那一格还要**信号与臂**都对上（配置层 ARM_A = 生产读数）。"""
    check_ledger_caliber(meas)
    for name, key, want in (("signal", "signal", PRODUCTION_SIGNAL),
                            ("filter_arm", "filter_arm", PRODUCTION_FILTER_ARM)):
        got = meas[key]
        if got != want:
            raise AssertionError(
                f"{GENERATOR}: 生产臂那一格（{meas['cell']}）的 {name} = {got!r}，"
                f"与生产口径 {want!r} 不同 ⇒ 它**不是生产臂**")


def check_production_cell(deployed: dict) -> str:
    """我们拼出来的那一格标签必须**逐字等于**上游 `verdict.config.cell`。

    ⚠️ 上游那一格是**生产判决格**（它的 `production_sources.signal` 逐字写着
    「`aplus/factor.py::DEFAULT_WEIGHTS`（weights.json 不存在 ⇒ 回落）」）。
    ⇒ 自己挑一格 = 有「挑一个好看的臂」的空间（第 91 层）⇒ 不许。
    """
    label = production_cell_label()
    upstream = require(deployed, "verdict", "config", "cell")
    if label != upstream:
        raise AssertionError(
            f"{GENERATOR}: 拼出来的生产格标签 {label!r} ≠ 上游 "
            f"`verdict.config.cell` {upstream!r} ⇒ "
            f"要么标签拼错，要么上游的生产配置变了 ⇒ **停止**，"
            f"不许拿另一格算倍数（分支 D_NO_DATA，绝不回落）")
    return label


def check_same_market_leg(calib: dict, deployed: dict) -> None:
    """两条腿必须是**同一腿**：等权、同样大小的抽样、同样种子。

    ⚠️ 这一条**不是**恒真：`market_nav_caliber.json::sample`（size/pool/seed）
    与 `deployed_d2_verdict.json::caliber.market_leg`（散文）各说各的；
    一边改成 300 只 / 另一个种子 ⇒ 这里立刻响。
    """
    size = require(calib, "sample", "size")
    seed = require(calib, "sample", "seed")
    desc = require(deployed, "caliber", "market_leg")
    for token, what in ((str(size), "抽样只数"), (str(seed), "种子")):
        if token not in desc:
            raise AssertionError(
                f"{GENERATOR}: 实测那一腿的市场基准（{desc!r}）里找不到"
                f"{what} {token!r} ⇒ 它与要求那一腿（{size} 只 / 种子 {seed}）"
                f"**不是同一腿** ⇒ 分子分母跨口径，不许算倍数（第 7 层）")


def multiple_needed(required_gross_pct: float, measured_gross_pct: float) -> float:
    """倍数 = 要求 ÷ 实测。**只做一次除法**。

    ⚠️ 分子分母必须**同口径**（同 K / 同信号 / 同榜宽 / 同市场腿）——
       跨口径的比值不是「还差几倍」（第 7 层）。
    ⚠️ 分母为 0 ⇒ **报错**，不许回落成 0（回落 = 一个看起来正常的 0）。
    """
    if not measured_gross_pct:
        raise ValueError(
            f"{GENERATOR}: 实测毛超额为 0 ⇒ 倍数无定义（不许回落成 0）")
    return required_gross_pct / measured_gross_pct


def build_multiple_calibers(width_artifact: dict, calib: dict,
                            deployed: dict, deliverable_row: dict) -> dict:
    """`headline` 里那**并排两列**的倍数块（纯函数，不写盘）。

    ⚠️ 老的那一列（跨口径，K=21 研究臂 ÷ K=63 的要求）**保留**，
       但**必须**带上「它不是缺口」与「它的 K 是几」两个标签。
    """
    required_pct = deliverable_row["required_gross_at_50k"] * 100.0

    prod = k_axis_measurement(deployed, production_cell_label())
    check_production_arm(prod)
    check_same_market_leg(calib, deployed)
    same_multiple = multiple_needed(required_pct, prod["measured_gross_annual_pct"])

    controls = []
    for signal, arm in CONTROL_ARMS:
        label = (f"{signal}|{arm}|K{LEDGER_K}"
                 f"|E{int(round(EQUITY_WEIGHT * 100))}")
        meas = k_axis_measurement(deployed, label)
        check_ledger_caliber(meas)
        if meas["signal"] == PRODUCTION_SIGNAL:
            raise AssertionError(
                f"{GENERATOR}: 对照臂（{label}）的信号与生产臂相同 ⇒ "
                f"它就不是对照臂了（对照臂的作用是**显式不同**）")
        controls.append({
            "role": "⚠️ 对照臂（非生产、非判决）",
            "caliber_name": (f"对照臂·K={LEDGER_K}·信号 {meas['signal']}·"
                             f"臂 {meas['filter_arm']}·榜宽 {DELIVERABLE_N}·"
                             "¥5万·满仓·等权市场腿"),
            "cell": meas["cell"],
            "k": meas["k"],
            "signal": meas["signal"],
            "filter_arm": meas["filter_arm"],
            "n_positions": meas["n_positions"],
            "equity_weight": meas["equity_weight"],
            "measured_gross_annual_pct": meas["measured_gross_annual_pct"],
            "measured_gross_source": meas["source"],
            "multiple_needed": multiple_needed(
                required_pct, meas["measured_gross_annual_pct"]),
        })

    cross_k = require(width_artifact, "k_rebalance_every")
    cross_measured_pct = deliverable_row["measured_gross_annual"] * 100.0
    cross_multiple = deliverable_row["multiple_needed_at_50k"]

    return {
        "⚠️_read_this_before_quoting_any_multiple": (
            "🚨 **这个块里有两个倍数，口径不同。**"
            f"只有 `same_caliber` 那个（**同口径**：K={LEDGER_K} · 生产臂 "
            f"`{PRODUCTION_SIGNAL}` · 榜宽 {DELIVERABLE_N} · ¥5万 · 满仓 · "
            "同一等权市场腿）是「还差几倍」。"
            f"`cross_caliber` 那个把 **K={cross_k} 研究臂的实测**除以 "
            f"**K={LEDGER_K} 的要求** ⇒ **K 与信号两处口径都不同** ⇒ "
            "它**不是**缺口（第 7 层：两个各自都对的数放一起比）。"),

        "requirement": {
            "caliber_name": (f"要求（K={LEDGER_K} · 榜宽 {DELIVERABLE_N} · "
                             "¥5万 · 满仓(equity_weight=1.0) · 等权市场腿）"),
            "k": LEDGER_K,
            "n_positions": DELIVERABLE_N,
            "capital": CAPITAL,
            "equity_weight": EQUITY_WEIGHT,
            "rounds_per_year": ROUNDS_PER_YEAR,
            "market_leg": {
                "description": "等权（抽样面板等权日收益）",
                "sample_size": require(calib, "sample", "size"),
                "sample_pool": require(calib, "sample", "pool"),
                "sample_seed": require(calib, "sample", "seed"),
                "source": "data/reports/market_nav_caliber.json::sample",
                "deployed_side_description": require(
                    deployed, "caliber", "market_leg"),
            },
            "required_gross_at_50k_deliverable_pct": required_pct,
            "source": ("data/reports/d2_signal_requirement.json::headline."
                       "required_gross_at_50k_deliverable"),
            "is_the_preregistered_criterion_input": True,
            "was_changed_by_this_ledger": False,
        },

        "same_caliber": {
            "is_the_gap": True,
            "caliber_name": (f"同口径·K={LEDGER_K}·生产臂 {PRODUCTION_SIGNAL}·"
                             f"配置层 {PRODUCTION_FILTER_ARM}·榜宽 {DELIVERABLE_N}·"
                             "¥5万·满仓·等权市场腿"),
            "cell": prod["cell"],
            "k": prod["k"],
            "signal": prod["signal"],
            "filter_arm": prod["filter_arm"],
            "n_positions": prod["n_positions"],
            "equity_weight": prod["equity_weight"],
            "capital": CAPITAL,
            "market_leg": {
                "description": "等权（抽样面板等权日收益）—— 与 requirement 同一腿",
                "sample_size": require(calib, "sample", "size"),
                "sample_pool": require(calib, "sample", "pool"),
                "sample_seed": require(calib, "sample", "seed"),
                "source": "data/reports/market_nav_caliber.json::sample",
                "deployed_side_description": require(
                    deployed, "caliber", "market_leg"),
            },
            "measured_gross_annual_pct": prod["measured_gross_annual_pct"],
            "measured_gross_source": prod["source"],
            "multiple_needed": same_multiple,
            "is_the_production_verdict_cell": True,
        },

        "control_arms": controls,

        "cross_caliber": {
            "is_the_gap": False,
            "caliber_name": f"跨口径（K={cross_k} 研究臂）—— ⚠️ **不是缺口**",
            "k_of_measured": cross_k,
            "k_of_requirement": LEDGER_K,
            "the_two_calibers_differ_in": "K 与信号（研究臂 8 因子逐年 IC 定权 ≠ 生产臂 DEFAULT_WEIGHTS）",
            "signal_of_measured": "研究臂（8 因子逐年 IC 定权）",
            "measured_gross_annual_pct": cross_measured_pct,
            "measured_gross_source": ('data/reports/factor_width_mde.json::'
                                      'widths["10"].gross_annual_pct'),
            "multiple_needed": cross_multiple,
            "legacy_key": "headline.multiple_needed_at_50k_deliverable",
            "⚠️_why_it_is_not_a_gap": (
                f"分子是 K={LEDGER_K} 的要求，分母是 K={cross_k} 研究臂的实测 ⇒ "
                "K 与信号两处口径都不同 ⇒ 这个比值**不是**「还差几倍」"
                "（第 7 层：两个各自都对的数放一起比）。"
                f"同口径的那一列在 `same_caliber`。"),
            "is_the_legacy_number_kept": True,
        },
    }


def main() -> int:
    # 第 125 层：开跑抓一次字节，盖章时比对 —— 拒绝盖「写盘那一刻恰好是这些字节」
    # 的章。必须落在**第一条执行语句**（第 131 层：放在盖章前更坏 ⇒ 检查恒真）。
    expect = provenance.code_file_sha256(ROOT, GENERATOR)

    width = json.loads((REPORTS / "factor_width_mde.json").read_text())
    nav = json.loads((REPORTS / "basket_nav_01_2026-10-16.json").read_text())
    calib = json.loads((REPORTS / "market_nav_caliber.json").read_text())
    # 🚨 第四份上游（2026-10-21 补）：**同口径**那一列的实测毛超额来自这里。
    #    它的 `verdict.config` 那一格就是**生产判决格**
    #    （`production_sources.signal` = `aplus/factor.py::DEFAULT_WEIGHTS`）。
    deployed = json.loads((REPORTS / DEPLOYED_ARTIFACT_NAME).read_text())

    # —— 量纲：每一处读法显式，换算不留第二入口 ——
    c_mkt = calib_frac(require(calib, ARM, "geometric_annual_pct"))
    x_star = require(nav, "gap_to_target_by_benchmark", ARM,
                     "required_excess_annual")
    target = require(nav, "target")

    # ✅ 对拍 1（**跨文件、推导路径不同** ⇒ 不是恒等式自证）：
    # 上游自己记的 c_mkt 来源写着 `geometric_annual_pct / 100.0`，
    # 而这里是「从 market_nav_caliber.json 读真百分数再除 100」
    # —— 两份独立产物必须逐位相等。
    c_mkt_crosscheck_diff = abs(
        c_mkt - require(nav, "gap_to_target_by_benchmark", ARM, "c_mkt_fraction"))

    # ✅ 对拍 2（**充分性锚**：外部锚，不需公式也不需容差）：
    #   `x* = (1+T)/(1+c_mkt) − 1` 无幂 ⇔ `(1+c)(1+x) == 1+T`。
    #   带幂式在这里差 8.36pp（第 181 层）。
    sufficiency_gap = (1 + c_mkt) * (1 + x_star) - (1 + target)
    sufficiency_anchor_ok = abs(sufficiency_gap) < 1e-12

    # ✅ 对拍 3（**量纲的绝对外部锚**，不依赖上面任何一条）：
    #   年化收益率不可能低于 −100%。若 c_mkt 被误读成 0.099471→0.9947 之类，
    #   这条立刻响；它**不需要公式也不需要容差**。
    unit_anchor_ok = IMPOSSIBLE_RETURN_FLOOR < min(
        c_mkt, x_star, *(
            w["gross_annual_pct"] for w in width["widths"].values()))

    rows = []
    for key in sorted(require(width, "widths"), key=int):
        w = require(width, "widths", key)
        n = require(w, "n")
        gross = require(w, "gross_annual_pct")            # 分数
        mde = require(w, "MDE_annual_pct")                # 名字带 pct，值是分数
        gross_over_mde = abs(gross) / mde if mde else None

        g_star_5w = required_gross(x_star, CAPITAL, n)
        g_star_inf = required_gross(x_star, INFINITE_CAPITAL, n)
        cap = ceiling_net_excess(gross, n)

        rows.append({
            "n": n,
            "measured_gross_annual": gross,
            "measured_net_at_50k": net_excess(
                gross, friction_at(CAPITAL, n)),
            "measured_net_ceiling": cap,
            "mde_annual": mde,
            "measured_gross_over_mde_abs": gross_over_mde,
            "detectable": abs(gross) > mde,

            "friction_at_50k": friction_at(CAPITAL, n),
            "friction_floor": friction_floor(n),
            "required_gross_at_50k": g_star_5w,
            "required_gross_at_infinite_capital": g_star_inf,
            "shortfall_at_50k_pp": (g_star_5w - gross) * 100.0,
            "shortfall_at_infinite_capital_pp": (g_star_inf - gross) * 100.0,
            "measured_over_required": (gross / g_star_inf) if g_star_inf else None,
            "multiple_needed_at_50k": (g_star_5w / gross) if gross else None,
            "multiple_needed_at_infinite_capital":
                (g_star_inf / gross) if gross else None,

            # ✅ 对拍 4：本脚本的摩擦 vs 上游产物自己报的摩擦（推导路径不同：
            # 上游走自己的成本链，本脚本走 `aplus.costs.annual_friction`）。
            "upstream_friction_width_aware": require(
                w, "friction_width_aware_annual_pct"),
            "friction_diff_vs_upstream": friction_at(CAPITAL, n) - require(
                w, "friction_width_aware_annual_pct"),
        })

    # ✅ 对拍 5：摩擦下界的**闭式 vs 实测**，两条推导路径（第 5 层的正解）。
    #    闭式里 `n_positions` 根本不存在 —— 「与榜宽无关」因此是**被算出来的**，
    #    不是被声称的（第 181 层：散文里的公式没有守卫）。
    widths = [r["n"] for r in rows]
    floor_width_independent, floor_width_rel = friction_floor_is_width_independent(widths)
    floor_matches_closed_form, floor_vs_closed_rel = friction_floor_matches_closed_form(
        DELIVERABLE_N)
    if not floor_width_independent:
        raise AssertionError(
            f"{GENERATOR}: 摩擦下界随榜宽变化，最大相对差 {floor_width_rel:.3e} > "
            f"{FLOOR_REL_TOL:.0e} ⇒ '下界与榜宽无关' 这条前提已被破坏，"
            f"整个 g*(∞) 的解读作废"
        )
    if not floor_matches_closed_form:
        raise AssertionError(
            f"{GENERATOR}: 实测摩擦下界与闭式不符，相对差 {floor_vs_closed_rel:.3e} > "
            f"{FLOOR_REL_TOL:.0e}"
        )

    branch = decide(rows)
    deliverable = next(r for r in rows if r["n"] == DELIVERABLE_N)
    by_multiple_inf = sorted(
        (r for r in rows if r["multiple_needed_at_infinite_capital"] is not None),
        key=lambda r: r["multiple_needed_at_infinite_capital"])
    cheapest = by_multiple_inf[0]     # 最接近门槛的档
    hardest = by_multiple_inf[-1]     # 离门槛最远的档
    best_ceiling = max(rows, key=lambda r: r["measured_net_ceiling"])
    best_gross = max(rows, key=lambda r: r["measured_gross_annual"])

    # ★ 天花板这一族**依赖榜宽**，而榜宽必须跟着数字一起离开脚本（第 7 层）。
    #   机制：measured_net_ceiling = 毛超额 − 摩擦下界，而 friction_floor 与 N 无关
    #   （产物自己的 friction_floor_is_provable_positive 写了闭式 N×(notional/N)×K
    #   ⇒ N 约掉）⇒ max(net_ceiling) ≡ 毛超额最高那一档。
    #   🚨 那正是**最不可交付**的一档：5 万本金下年摩擦三位数、账户净为负。
    #   ⇒ 任何「离 20% 还差 X」都必须说清是哪个宽度下的 X。
    # 🚨 2026-10-18 自查（本轮新引入的缺陷，已被审计打出）：
    #   这张表原先把**无限本金钱**叫 `account_annual_ceiling_pct` / `gap_to_target_pp`，
    #   而同一行里紧挨着的 `measured_net_at_50k_pct` / `friction_at_50k_pct` 都带
    #   `_at_50k` ⇒ **一个平铺元素里装了两个本金口径，只有一个带名字**。
    #   失效方向 = **乐观**：宽度 546 那行读成账户 +12.7285%，而 5 万真读数是
    #   **−113.4543%**（符号翻转）；宽度 10 读成 9.9708% 而真值 8.3441%。
    #   而**上方那个块级 dict 用的是正确约定**（`at_infinite_capital` / `at_deliverable_width`
    #   / `at_deliverable_width_and_50k`）⇒ 缺陷不是缺约定，是**这张表把约定丢了**。
    #   修法：两个口径都进表，且**口径名进键名**（不许只靠旁边的 _at_50k 邻居暗示）。
    ceiling_by_width = [
        {
            "n": r["n"],
            "is_the_deliverable_width": r["n"] == DELIVERABLE_N,
            # —— 无限本金口径（减的是 friction_floor，与 N 无关）——
            "account_annual_at_infinite_capital_pct":
                (c_mkt + r["measured_net_ceiling"]) * 100.0,
            "gap_to_target_at_infinite_capital_pp":
                (target - (c_mkt + r["measured_net_ceiling"])) * 100.0,
            # —— ¥5 万口径（减的是 friction_at_50k，**随 N 变**）——
            # ⚠️ 这两个是**账户**收益（c_mkt + 净超额），不是净超额本身；
            #    净超额单列在 measured_net_at_50k_pct（第 168 层：两个量纲同段共存）
            "account_annual_at_50k_pct":
                (c_mkt + r["measured_net_at_50k"]) * 100.0,
            "gap_to_target_at_50k_pp":
                (target - (c_mkt + r["measured_net_at_50k"])) * 100.0,
            # —— 两个本金口径之差（随 N 变，实测五档 1.63 / 6.27 / 22.54 / 45.78 / 126.18pp）——
            "capital_closes_pp_at_50k":
                (r["measured_net_ceiling"] - r["measured_net_at_50k"]) * 100.0,
            "measured_gross_annual_pct": r["measured_gross_annual"] * 100.0,
            "measured_net_at_50k_pct": r["measured_net_at_50k"] * 100.0,
            "friction_at_50k_pct": r["friction_at_50k"] * 100.0,
            "abs_gross_over_mde": r["measured_gross_over_mde_abs"],
        }
        for r in sorted(rows, key=lambda r: r["n"])
    ]

    # ★ 单一真相源：CLI、caliber 散文、产物三处都读这五个值。
    #   「低估」= 两个缺口之差（不是「离 20% 的差」减掉「离 20% 的差」——
    #   我第一版就这么写，算出 17.30pp 而真值 2.7577pp，第 181 层第三次复发）。
    ceiling_infinite_pct = (c_mkt + best_ceiling["measured_net_ceiling"]) * 100.0
    ceiling_deliverable_pct = (c_mkt + deliverable["measured_net_ceiling"]) * 100.0
    gap_infinite_pp = target * 100.0 - ceiling_infinite_pct
    gap_deliverable_pp = target * 100.0 - ceiling_deliverable_pct
    understatement_pp = gap_deliverable_pp - gap_infinite_pp

    # ★★ 第五个口径（2026-10-18 补）：交付档 N=10 **且本金 ¥5 万**。
    #   🚨 上面两个 ceiling_* **都是无限本金钱** —— measured_net_ceiling 减的是
    #      friction_floor，而 friction_floor = friction_at(INFINITE_CAPITAL, N)
    #      （width-invariant：`notional × K` 把 N 约掉）。
    #      ⇒ 「交付档」只回答了**宽度**，没回答**本金**；
    #      而用户是 5 万 ⇒ 把 9.9708% 叫「5 万下真正可交付的那个」是**错标签**
    #      （第 7 层 / [[names-do-not-promise-semantics]]）。
    #   5 万口径要减 friction_at_50k ⇒ 用 measured_net_at_50k
    #   ⚠️ 那个字段是**净超额**（毛超额 − 摩擦），**不是账户收益**；
    #      账户收益 = c_mkt + 净超额 —— 下面这一行就是那个加法。
    ceiling_deliverable_50k_pct = (c_mkt + deliverable["measured_net_at_50k"]) * 100.0
    gap_deliverable_50k_pp = target * 100.0 - ceiling_deliverable_50k_pct
    #   把 5 万成本扣进 ∞ 本金数，幅度恰是这个差（不是「低估」，是两个口径之差）。
    capital_imported_into_infinite_number_pp = (
        ceiling_deliverable_pct - ceiling_deliverable_50k_pct
    )

    max_friction_diff = max(abs(r["friction_diff_vs_upstream"]) for r in rows)
    # ⚠️ 拿 argmax 的**那一行**，不只是 max 的值 —— 否则产物报得出数、报不出宽度
    #   （`gap_left_after_infinite_capital_pp` 曾在裸数状态漏掉榜宽，同一块里
    #   `measured_gross_best` 已经带了 `is_the_supremum_over_widths` + `width`）。
    worst_gap_row = max(rows, key=lambda r: r["shortfall_at_infinite_capital_pp"])
    max_shortfall_inf = worst_gap_row["shortfall_at_infinite_capital_pp"]

    # ── 2026-10-21 补：**同口径**那一列的倍数（只加，不改既有任何值）──
    # ✅ 对拍 6（**生产格的一致性**）：我们拼出来的那一格标签必须**逐字等于**
    #   `deployed_d2_verdict.json::verdict.config.cell`（产物自己认的那一格）
    #   ⇒ 不一致 ⇒ 我们要么算错了标签，要么上游换了生产配置 ⇒ 立刻响，
    #   **不许**默默拿另一格算倍数（第 7 层）。
    check_production_cell(deployed)
    multiple_calibers = build_multiple_calibers(
        width, calib, deployed, deliverable)

    out = {
        "artifact": OUT.name,
        "generator": GENERATOR,
        "prereg": "docs/PREREG_D2_SIGNAL_REQUIREMENT_2026-10-16.md",

        "question": "要让账户年化到 20%，信号本身必须交出多少毛超额？",
        "branch": branch,
        "branch_order_is_the_criterion":
            "预注册 §3.2：C_UNMEASURABLE 必须先于 A/B 被检 —— 否则输出"
            "乐观方向的结论（把「仪器看不清」说成「东西不行」）",

        "account_leg": {
            "arm": ARM,
            "c_mkt_annual": c_mkt,
            "required_excess_annual": x_star,
            "target": target,
            "sufficiency_anchor_form": "(1 + c_mkt) * (1 + x_star) == 1 + target",
            "sufficiency_anchor_gap": sufficiency_gap,
            "sufficiency_anchor_ok": sufficiency_anchor_ok,
            "c_mkt_crosscheck_diff": c_mkt_crosscheck_diff,
            "unit_anchor_ok": unit_anchor_ok,
            "unit_anchor_form": "年化收益率不可能低于 −100%（绝对锚，不需公式/容差）",
            "max_friction_diff_vs_upstream": max_friction_diff,
        },

        "rows": rows,

        "headline": {
            "required_gross_at_infinite_capital_best": min(
                r["required_gross_at_infinite_capital"] for r in rows) * 100.0,
            "required_gross_at_infinite_capital_hardest": max(
                r["required_gross_at_infinite_capital"] for r in rows) * 100.0,
            "required_gross_at_50k_deliverable":
                deliverable["required_gross_at_50k"] * 100.0,
            "measured_gross_best": {
                "is_the_supremum_over_widths": True,
                "width": best_gross["n"],
                "at_infinite_capital":
                    best_gross["measured_gross_annual"] * 100.0,
                "at_deliverable_width":
                    deliverable["measured_gross_annual"] * 100.0,
                "deliverable_width": DELIVERABLE_N,
            },
            "multiple_needed_at_infinite_capital": {
                "cheapest_width": cheapest["n"],
                "cheapest": cheapest["multiple_needed_at_infinite_capital"],
                "hardest_width": hardest["n"],
                "hardest": hardest["multiple_needed_at_infinite_capital"],
            },
            "multiple_needed_at_50k_deliverable":
                deliverable["multiple_needed_at_50k"],

            # 🚨 2026-10-21 补：**并排两列**的倍数。
            #   `multiple_needed_at_50k_deliverable`（上面那个裸 float）**保留**
            #   —— 前端与既有守卫读它 ⇒ **值一个字不许改**；
            #   但它**跨口径**（K=21 研究臂 ÷ K=63 的要求）⇒ 这里给它标注。
            #   同口径的那一列（生产臂）在这里，**口径名 + 产物路径**齐备。
            "multiple_needed_at_50k_deliverable_by_caliber":
                multiple_calibers,

            # 🚨 这三个键以前是**裸数**：`account_annual_ceiling_pct` = 12.7285%
            #   与 `measured_gross_best` = 7.5685%，而它们的 argmax 都是 n=546
            #   —— 5 万本金下年摩擦 130.97%、账户净 −113.45% 的那一档。
            #   裸数被读者拿去和 20% 对比 ⇒ **在乐观方向低估缺口 2.7577pp**。
            #   修法不是改 max（跨档上确界本身在数学上成立），是**把榜宽装进值里**
            #   —— 这正是相邻两键 `multiple_needed_at_infinite_capital`
            #   早就在用的写法（cheapest_width / hardest_width）。
            "account_annual_ceiling_pct": {
                "is_the_supremum_over_widths": True,
                "width": best_ceiling["n"],
                # 🚨 这三个 ceiling 值**全部是无限本金钱**：
                #   measured_net_ceiling = 毛超额 − friction_floor，
                #   而 friction_floor = friction_at(INFINITE_CAPITAL, N)
                #   （闭式 notional×K 把 N 约掉 ⇒ 实测五档相对极差 1.45e-16）。
                #   ⇒ 「deliverable_width」只回答**宽度**，不回答**本金**。
                "all_three_ceilings_are_infinite_capital": True,
                "at_infinite_capital": ceiling_infinite_pct,
                "at_deliverable_width": ceiling_deliverable_pct,
                "at_deliverable_width_and_50k": ceiling_deliverable_50k_pct,
                "deliverable_width": DELIVERABLE_N,
                "gap_to_target_at_infinite_capital_pp": gap_infinite_pp,
                "gap_to_target_at_deliverable_width_pp": gap_deliverable_pp,
                "gap_to_target_at_deliverable_width_and_50k_pp": gap_deliverable_50k_pp,
                "capital_imported_into_the_infinite_capital_number_pp":
                    capital_imported_into_infinite_number_pp,
                "the_bare_number_understated_the_gap_by_pp": understatement_pp,
                "the_bare_number_understatement_is_in_the_optimistic_direction": True,
                "that_width_is_undeliverable_at_50k":
                    f"宽度 {best_ceiling['n']} 在 ¥5万 下年摩擦 "
                    f"{best_ceiling['friction_at_50k'] * 100:.4f}%、净超额 "
                    f"{best_ceiling['measured_net_at_50k'] * 100:.4f}%"
                    f"（交付档是 {deliverable['friction_at_50k'] * 100:.4f}% / "
                    f"{deliverable['measured_net_at_50k'] * 100:.4f}%）"
                    f"⇒ 与 20% 对比：**5 万本金**要用 "
                    f"`at_deliverable_width_and_50k`"
                    f"（{ceiling_deliverable_50k_pct:.4f}%，"
                    f"差 {gap_deliverable_50k_pp:.4f}pp）；"
                    f"`at_deliverable_width` 是**本金 → ∞**，"
                    f"不能当「5 万真能交付的那个」",
            },
            "account_annual_ceiling_is_a_point_estimate": {
                "at_infinite_capital":
                    f"这个天花板（宽度 {best_ceiling['n']}）由 |g_obs|/MDE = "
                    f"{best_ceiling['measured_gross_over_mde_abs']:.2f} 的点估计撑起 "
                    f"⇒ 连符号都定不了 ⇒ 只支持形状陈述，不支持水平陈述（预注册 §3.1）",
                "at_deliverable_width":
                    f"交付档（宽度 {DELIVERABLE_N}）由 |g_obs|/MDE = "
                    f"{deliverable['measured_gross_over_mde_abs']:.2f} 的点估计撑起 "
                    f"⇒ 同样连符号都定不了（预注册 §3.1）",
            },
            "account_annual_ceiling_by_width": ceiling_by_width,

            # 🚨 下面两个键以前都是**裸 float**，而它们**都随榜宽变**：
            #   capital_can_close_at_most_pp 实测五档 1.63 / 6.27 / 22.54 / 45.78 /
            #   **126.18pp** —— 读成「宽度无关」会把本金说成几乎没用（在交付档 N=10
            #   成立，在 N=546 上差 77 倍，是错的）。
            #   gap_left_after_infinite_capital_pp 是**跨宽上确界**（argmax 在 N=10，
            #   但那是巧合：宽度集或 DELIVERABLE_N 一变就换档，值却看不出来）。
            #   同一块里的 `measured_gross_best` / `account_annual_ceiling_pct`
            #   **已经**用了 dict+width 约定 ⇒ 这两个是被漏掉的，不是没有约定可用。
            "capital_can_close_at_most_pp": {
                "width": DELIVERABLE_N,
                "is_width_dependent": True,
                "per_width_pp": {
                    str(r["n"]): (
                        r["shortfall_at_50k_pp"]
                        - r["shortfall_at_infinite_capital_pp"]
                    )
                    for r in sorted(rows, key=lambda r: r["n"])
                },
                "at_deliverable_width":
                    deliverable["shortfall_at_50k_pp"]
                    - deliverable["shortfall_at_infinite_capital_pp"],
                "spread_max_over_min": (
                    max(
                        r["shortfall_at_50k_pp"]
                        - r["shortfall_at_infinite_capital_pp"]
                        for r in rows
                    )
                    / min(
                        r["shortfall_at_50k_pp"]
                        - r["shortfall_at_infinite_capital_pp"]
                        for r in rows
                    )
                ),
                "reading_it_as_width_agnostic_is_wrong_because":
                    "它是**交付档**的数：宽度 546 上本金能关掉 "
                    f"{max(r['shortfall_at_50k_pp'] - r['shortfall_at_infinite_capital_pp'] for r in rows):.4f}pp",
            },
            "gap_left_after_infinite_capital_pp": {
                "is_the_supremum_over_widths": True,
                "width": worst_gap_row["n"],
                "deliverable_width": DELIVERABLE_N,
                "at_supremum": max_shortfall_inf,
                "at_deliverable_width":
                    deliverable["shortfall_at_infinite_capital_pp"],
                "the_supremum_coinciding_with_the_deliverable_width":
                    worst_gap_row["n"] == DELIVERABLE_N,
                "per_width_pp": {
                    str(r["n"]): r["shortfall_at_infinite_capital_pp"]
                    for r in sorted(rows, key=lambda r: r["n"])
                },
            },
        },

        "friction_floor_is_provable_positive": {
            "value": friction_floor(DELIVERABLE_N),
            "why": "COMMISSION_MIN_BINDS_BELOW = 5/0.0003 = ¥16,667；每笔本金"
                   "超过它后佣金 = per × rate 与本金无关 ⇒ 下界 > 0",
            "consequence": "本金买不到零成本 ⇒ 加本金不能把 g* 压到 x* 以下 ⇒ "
                           "剩下的缺口不可能由本金封口",

            # 🚨 下面这三块是为了让「五档 g*(∞) 完全相同」**不像一个 bug**。
            #    那五个数相同是**算出来的性质**，不是笔误；机制在此逐字给出。
            "why_it_is_the_same_number_on_every_width": {
                "one_line": "equity_weight=1.0 ⇒ 一轮调仓的总成交额 = 全部权益，"
                            "与 N 无关；成本按成交额线性 ⇒ 每轮摩擦与 N 无关",
                "derivation": [
                    "代码路径：per = notional/N，然后 N 次 round_trip(per) 求和",
                    "闭式路径：N × (notional/N) × K = notional × K，**N 直接约掉**",
                    "K = 2×(commission + transfer_fee + spread) + stamp_duty"
                    f" = {_ROUND_TRIP_LINEAR}",
                    f"⇒ friction_floor = equity_weight × K × (244/{LEDGER_K})"
                    f" = {friction_floor_closed_form():.6f}"
                    f"（即 {friction_floor_closed_form() * 100:.4f}%/年）",
                ],
                "checked_not_asserted": {
                    "closed_form": friction_floor_closed_form(),
                    "measured_at_deliverable_width": friction_floor(DELIVERABLE_N),
                    "relative_diff": floor_vs_closed_rel,
                    "tolerance": FLOOR_REL_TOL,
                    "tolerance_derivation": "float64 朴素求和 n 项的相对误差界 O(n·ε)；"
                                           f"最大档 n={max(widths)} ⇒ "
                                           f"{max(widths)} × 2.22e-16 ≈ 1.2e-13 ⇒ "
                                           "取 1e-12 留一个数量级余量",
                },
                "width_independence": {
                    "widths_checked": widths,
                    "max_relative_spread": floor_width_rel,
                    "tolerance": FLOOR_REL_TOL,
                    "note": "这一条**不是**恒真：`costs.py::rebalance` 若被改成"
                            "「先求和再乘费率」（它自己的 docstring 明写那是错的），"
                            "实测下界立刻随 N 变 ⇒ 这里会 AssertionError",
                },
                "consequence": "★ **放宽榜宽不能降低达标所需的毛超额**。"
                               f"N={by_multiple_inf[0]['n']} 只需 "
                               f"×{by_multiple_inf[0]['multiple_needed_at_infinite_capital']:.2f}"
                               f" 而 N={by_multiple_inf[-1]['n']} 需 "
                               f"×{by_multiple_inf[-1]['multiple_needed_at_infinite_capital']:.2f}，"
                               "差别**全部来自毛超额更高**，与门槛无关",
            },
        },

        "caliber": {
            "account_annual_is": "market_return + net_excess（上游 caliber 原文）",
            "required_excess_is_a_DIFFERENCE_not_a_nav_series":
                "x* 是「超额差」的年率，**不是净值序列**；直接对它算 CAGR 会错 ~10pp",
            "net_excess_is_also_a_difference":
                "measured_net_* 与 measured_net_ceiling 同为差序列，不是净值",
            "rounds_per_year": ROUNDS_PER_YEAR,
            "rounds_per_year_note": (
                f"精确 244/{LEDGER_K}，不取整；"
                f"取整 {round(ROUNDS_PER_YEAR)} 会抬摩擦下界"),
            "k_used_by_this_ledger": LEDGER_K,
            "k_source": "aplus/evolve.py::LIVE_REBALANCE_EVERY（单一真相源，不写死）",
            "⚠️_this_ledger_K_is_63_but_the_verdict_tier_is_K21_do_not_compare_sizes": (
                f"🚨 **本台账算的是 K={LEDGER_K}（实盘配置）的「所需毛超额」**，"
                "而 `basket_nav_01_2026-10-16.json::tiers[1]`（**判决档**）"
                "是 **K=21** 的口径 ⇒ **两组数不可直接比大小**。"
                "理由：毛额门槛 = x* + 摩擦，而摩擦随 K 变（K 越大 ⇒ 年调仓次数越少 ⇒ "
                "摩擦越低 ⇒ 门槛越松）⇒ K=63 的门槛**低于** K=21 的门槛，"
                "这是**口径差**，不是「更接近达标」。"
                "⇒ 引用本台账的数时必须同时带「K=63」这一句；"
                "引用判决档那个数时必须带「K=21」这一句；"
                "**两者的差不可解释成「离 20% 更近了」**（第 129 层：门槛松弛 ≠ 达标）"),
            "equity_weight": EQUITY_WEIGHT,
            "equity_weight_is_the_FULL_POSITION_verdict_caliber_not_live":
                "⚠️ 本文件的 EQUITY_WEIGHT=1.0 是**满仓判决口径**（预注册 §2.1 C2）。"
                "实盘是**半仓**：`aplus/alloc.py::build_allocation(max_positions=5, "
                "per_stock_cap=0.10)` ⇒ equity_weight=0.5，"
                "`deployed_account_nav.json` 已记此债。"
                "⇒ 两个口径下的「所需毛超额」**不可互相代替**："
                f"半仓的摩擦正好是满仓的一半（{friction_floor(DELIVERABLE_N) * 100:.6f}%"
                f" → {friction_floor_closed_form(0.5) * 100:.6f}%/年，实测比值 "
                f"{friction_floor_closed_form(0.5) / friction_floor(DELIVERABLE_N):.10f}），"
                f"g*(∞) 相应降到 {(x_star + friction_floor_closed_form(0.5)) * 100:.6f}%。"
                "**本文件全部数字是满仓口径**，引用时必须带这一句；"
                "不要拿它去论证实盘半仓的可行性，反之亦然（第 7 层："
                "两个各自都对的数放一起比）",
            "friction_calibrated_at_one_width_only":
                "COST_REAL_50K 是 N=10 标定值；本脚本用逐笔成本链，不用它",
            "the_two_multiples_in_the_headline_have_DIFFERENT_calibers": (
                f"🚨 `headline.multiple_needed_at_50k_deliverable`（"
                f"{deliverable['multiple_needed_at_50k']:.4f}）是**跨口径**的："
                f"分子是 **K={LEDGER_K}** 的要求，分母是 **K="
                f"{require(width, 'k_rebalance_every')} 研究臂**的实测"
                "（`factor_width_mde.json::widths[\"10\"]`，8 因子逐年 IC 定权）"
                "⇒ **K 与信号两处口径都不同** ⇒ 它**不是**「还差几倍」"
                "（第 7 层：两个各自都对的数放一起比）。"
                "⇒ 同口径的那一列（生产臂 LIVE_DEFAULT · 同 K · 同榜宽 · 同市场腿）"
                "在 `headline.multiple_needed_at_50k_deliverable_by_caliber."
                "same_caliber`；**引用任何倍数都必须带口径名**。"),
            "the_same_caliber_column_is_ADDED_not_a_criterion_change": (
                f"⚠️ 本节**只加一列读数**：要求那一个数 "
                f"`required_gross_at_50k_deliverable` = "
                f"{deliverable['required_gross_at_50k'] * 100:.4f}% **一个字未动**"
                "（判据的输入，预注册的）⇒ 门槛、MDE 口径、20% 目标、"
                "要求毛超额的算法**全部未改**（事后补记见预注册文档 §6，"
                "**明写不是预注册**）。"),
            "the_ceiling_family_is_WIDTH_DEPENDENT_and_says_which_width":
                "⚠️ `account_annual_ceiling_pct` / `measured_gross_best` "
                "取的是 max(rows, key=…) ⇒ 跨榜宽**上确界**，落在 n="
                f"{best_gross['n']}；而 `friction_floor` 与 N 无关 ⇒ "
                "该 max ≡ 毛超额最高档，而那正是 5 万下最不可交付的一档"
                f"（摩擦 {best_ceiling['friction_at_50k'] * 100:.4f}%/年）。"
                "⇒ 这两个键的**值是 dict、宽度装在里面**（与相邻的 "
                "`multiple_needed_at_infinite_capital` 同一约定）；"
                f"**与 20% 对比只许用 `at_deliverable_width`（宽度 {DELIVERABLE_N}）**，"
                "逐档全表见 `account_annual_ceiling_by_width`。"
                "⚠️ 此前这两个键是**裸 float** 且无宽度标记 ⇒ "
                f"读者拿 {ceiling_infinite_pct:.4f}% 去比 20% 会得到"
                f"「只差 {gap_infinite_pp:.4f}pp」，"
                f"而交付档的真读数是差 {gap_deliverable_pp:.4f}pp"
                f" ⇒ **乐观方向低估 {understatement_pp:.4f}pp**（第 7 层）",
            "upstream_units_differ": {
                "factor_width_mde_*_pct": "分数（字段名带 _pct 但值是分数）",
                "market_nav_caliber.geometric_annual_pct": "真百分数",
                "basket_nav_01.required_excess_annual": "分数",
            },
            "unit_anchor_used":
                "绝对外部锚「年化收益率不可能低于 −100%」——不用字段名、不用容差",
            "the_BY_WIDTH_table_has_TWO_capital_calibers_and_both_are_NAMED":
                f"⚠️ `account_annual_ceiling_by_width` 每一行同时装**两个本金口径**："
                f"`*_at_infinite_capital_*`（减 friction_floor，与 N 无关）"
                f"和 `*_at_50k_*`（减 friction_at_50k，**随 N 变**）。"
                f"此前表里只有后者带 `_at_50k` 字样、前者叫裸的 "
                f"`account_annual_ceiling_pct` ⇒ **一个平铺元素两个口径只有一个有名**；"
                f"失效方向**乐观**：宽度 546 读成账户 "
                f"{ceiling_infinite_pct:.4f}% 而 5 万真读数是 "
                f"{(c_mkt + best_ceiling['measured_net_at_50k']) * 100:.4f}%（**符号翻转**），"
                f"宽度 {DELIVERABLE_N} 读成 {ceiling_deliverable_pct:.4f}% 而真值 "
                f"{ceiling_deliverable_50k_pct:.4f}%。"
                f"⇒ 两个口径都进表且**口径名进键名**，不靠邻居暗示（第 168 层）。"
                f"⚠️ 同理，`capital_can_close_at_most_pp` 与 "
                f"`gap_left_after_infinite_capital_pp` 也随宽变"
                f"（前者实测五档极差 ×"
                f"{max(r['shortfall_at_50k_pp'] - r['shortfall_at_infinite_capital_pp'] for r in rows) / min(r['shortfall_at_50k_pp'] - r['shortfall_at_infinite_capital_pp'] for r in rows):.1f}）"
                f"⇒ 都改成 dict 带 `width` + `per_width_pp`",
            "two_capital_calibers_is_not_a_typo":
                "「本金 → ∞」与「本金 ¥5 万」是**两个问题**，不是同一个数的两个近似；"
                "差额随榜宽从 1.63pp 涨到 126.18pp ⇒ 榜越宽、本金越要紧（摩擦按 "
                "notional×K 计而毛超额按 N 摊薄）",
            "units_in_THIS_block_headline":
                f"⚠️ `headline` 里的数**全是真百分数**（`_pct` 结尾的也是），"
                f"而同一份产物的 `rows[*]` 与 `account_leg` 里同名的量是**分数**"
                f"（第 168 层：同名字段两个量纲，100 倍差）。"
                f"具体：`headline.required_gross_at_infinite_capital_*` = "
                f"{deliverable['required_gross_at_infinite_capital'] * 100:.4f}% "
                f"vs `rows[0].required_gross_at_infinite_capital` = "
                f"{deliverable['required_gross_at_infinite_capital']!r}；"
                f"`headline.measured_gross_best.*` = "
                f"{deliverable['measured_gross_annual'] * 100:.4f}% vs "
                f"`rows[0].measured_gross_annual` = "
                f"{deliverable['measured_gross_annual']!r}。"
                f"⇒ 唯一无歧义的判法仍是绝对外部锚「年化不可能低于 −100%」，"
                f"分数读法会让 13.93% 读成 0.1393%/年（第 134 层：1e-14 量级免于被质疑，"
                f"本例是 100 倍，更容易被放过）",
        },

        "what_this_does_not_prove": [
            "g_obs < g* 不等于「D2 达不到」——只等于「当前配置下交不出门槛要的量」",
            "五档 detectable 全为 False ⇒ 连「实测毛超额是不是正的」都定不了 ⇒ "
            "本文件的水平陈述全部只支持形状（第 160 层）",
            "不重跑回测 ⇒ 本产物不比上游更新；上游 stale 则本产物继承同一 staleness",
            "不改 LIVE_REBALANCE_EVERY、不写 weights.json、不改任何生产路径",
            "★ 不改 CLAUDE.md 的 D2 缺口 9.4437pp —— 那是 c 对 20% 的差，"
            "本文件是同一件事的毛额口径，两者不可互相代替",
            f"★ 不把 `account_annual_ceiling_pct.at_infinite_capital` "
            f"（宽度 {best_ceiling['n']}）当成「策略最多能做到多少」—— "
            f"那个宽度在 ¥5万 下年摩擦 {best_ceiling['friction_at_50k'] * 100:.4f}%、"
            f"净超额 {best_ceiling['measured_net_at_50k'] * 100:.4f}%；"
            f"⚠️ 且 `at_deliverable_width` 也**不是** 5 万读数（它是本金 → ∞）—— "
            f"5 万可交付读数是宽度 {DELIVERABLE_N} 的 "
            f"`at_deliverable_width_and_50k` = "
            f"{ceiling_deliverable_50k_pct:.4f}%（逐档表见 "
            f"`account_annual_ceiling_by_width`）",
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    provenance.stamp(OUT, GENERATOR, expect_bytes=expect, extra={"source": [
        "data/reports/factor_width_mde.json",
        "data/reports/basket_nav_01_2026-10-16.json",
        "data/reports/market_nav_caliber.json",
        f"data/reports/{DEPLOYED_ARTIFACT_NAME}",
    ]})

    print(f"branch = {branch}")
    print(f"锚：充分性 gap={sufficiency_gap:+.3e} ok={sufficiency_anchor_ok} | "
          f"c_mkt 跨文件差={c_mkt_crosscheck_diff:.3e} | "
          f"量纲锚 ok={unit_anchor_ok} | 摩擦 vs 上游最大差={max_friction_diff:.3e}")
    for r in rows:
        print(f"  N={r['n']:>3}  g_obs={r['measured_gross_annual']*100:>7.4f}%  "
              f"g*(5万)={r['required_gross_at_50k']*100:>7.4f}%  "
              f"g*(∞)={r['required_gross_at_infinite_capital']*100:>7.4f}%  "
              f"净上限={r['measured_net_ceiling']*100:>7.4f}%  "
              f"需 ×{r['multiple_needed_at_50k']:.2f}(5万)/"
              f"×{r['multiple_needed_at_infinite_capital']:.2f}(∞)")
    h = out["headline"]
    ceil_h = h["account_annual_ceiling_pct"]
    cap_h = h["capital_can_close_at_most_pp"]
    gapinf_h = h["gap_left_after_infinite_capital_pp"]
    mc = h["multiple_needed_at_50k_deliverable_by_caliber"]
    same_c = mc["same_caliber"]
    cross_c = mc["cross_caliber"]
    print(f"  ★ 还差几倍：**同口径**（K={same_c['k']}·生产臂 {same_c['signal']}·"
          f"榜宽 {same_c['n_positions']}·¥5万·满仓·同一市场腿）= "
          f"**×{same_c['multiple_needed']:.4f}**"
          f"（实测毛超额 {same_c['measured_gross_annual_pct']:.4f}% ÷ "
          f"要求 {mc['requirement']['required_gross_at_50k_deliverable_pct']:.4f}%）")
    print(f"    ⚠️ **跨口径**（K={cross_c['k_of_measured']} 研究臂 ÷ "
          f"K={cross_c['k_of_requirement']} 的要求）= "
          f"×{cross_c['multiple_needed']:.4f} ⇒ **不是缺口**，"
          f"它虚高 "
          f"×{cross_c['multiple_needed'] / same_c['multiple_needed']:.4f}"
          f"（{cross_c['k_of_measured']} vs {cross_c['k_of_requirement']}）")
    for c in mc["control_arms"]:
        print(f"    · 对照臂 {c['cell']}：×{c['multiple_needed']:.4f}"
              f"（{c['role']}）")
    print(f"  本金最多省 {cap_h['at_deliverable_width']:.4f}pp"
          f"（宽度 N={cap_h['width']}；⚠️ 随宽变，实测五档极差 ×"
          f"{cap_h['spread_max_over_min']:.1f}）；"
          f"加到 ∞ 还差 {gapinf_h['at_supremum']:.4f}pp"
          f"（跨档上确界，落在 N={gapinf_h['width']}）")
    print(f"  ★ 账户天花板（交付档 N={ceil_h['deliverable_width']}，**本金 ¥5 万**）"
          f"= {ceil_h['at_deliverable_width_and_50k']:.4f}% "
          f"⇒ 离 20% 还差 {ceil_h['gap_to_target_at_deliverable_width_and_50k_pp']:.4f}pp"
          f"　（这才是「5 万下真正可交付的那个」）")
    print(f"    ⚠️ 同宽度但**本金 → ∞** = {ceil_h['at_deliverable_width']:.4f}%"
          f"（差 {ceil_h['gap_to_target_at_deliverable_width_pp']:.4f}pp）——"
          f"∞ 本金少付摩擦 {ceil_h['capital_imported_into_the_infinite_capital_number_pp']:.4f}pp"
          f"，**不是 5 万能拿到的数**")
    print(f"  ⚠️ 跨档上确界（宽度 N={ceil_h['width']}，5 万下摩擦 "
          f"{best_ceiling['friction_at_50k'] * 100:.4f}%、净超额 "
          f"{best_ceiling['measured_net_at_50k'] * 100:.4f}%）= "
          f"{ceil_h['at_infinite_capital']:.4f}% ⇒ **不可交付**，"
          f"裸数引用会把缺口在乐观方向低估 "
          f"{ceil_h['the_bare_number_understated_the_gap_by_pp']:.4f}pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
