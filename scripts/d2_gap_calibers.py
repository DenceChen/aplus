"""D2 缺口陈述的**口径对照表** —— 把三处互相打架的数摆到一张表上，并**程序化对账**。

## 起因

`CLAUDE.md` 判决节连着三句说的是**同一个问题**，而三句里的数分属**三个互不相容的
口径**，且**没有一个在判决链上**：

| CLAUDE.md 写的 | 真实出处 | 能不能引用 |
|---|---|---|
| 「账户天花板 **11%/年毛超额**」 | `bridge_error.json` | 🚨 **无 sidecar ⇒ 不可引用** |
| 「门槛 **26.39%/年**」 | `aplus/hurdle.py::hurdle_rate` | ✅ 代码唯一入口 |
| 「实测毛超额 **4.81%/年**」 | `factor_width_mde.json` | ⚠️ 带章，但 `code_dirty=true` |
| 「毛超额上限 11%/年 vs 门槛 26.39%/年 ⇒ **差 3.9 倍**」 | —— | ❌ **比值与被比数不自洽** |

🚨 **判决节主句里的三个数（11 / 26.39 / 3.9）没有一个能组合出 3.9**：

```
26.39 / 11    = 2.399     ← 这才是被声称的那对数
26.39 / 6.78  = 3.892     ← 3.9 实际来自这里
```

⇒ **比值的两个操作数是「门槛 ÷ K=21 毛超额」，不是「门槛 ÷ 11% 天花板」。**
⇒ 这不是笔误（第 137 层手抄错），是**三个口径被并成了一句话**
（第 7 种形状：两个各自正确的数当成同一个量）。

## 为什么 11% 与 11.6 会被混起来

`CLAUDE.md` K 扫描表的表头是
`| K | 次/年 | 名义成本/yr | 实测成本/yr | 毛超额/yr | 净/yr | 门槛 | …`，
K=21 那一行第二格是 **11.6**。

⇒ **11.6 是「次/年」（调仓次数）**，**11%/年是散文里的「毛超额上限」**。
**两个不同的量，形状上只差一个小数点，相隔约 60 行。**
⇒ 且 `26.39% = 20% + 11.6 × 0.55%` ⇒ **11.6 正是该门槛的乘数本身**
⇒ 同一行里「11.6」与「26.39%」**互为因果，不是两条独立证据**。

## 本脚本做 / 不做

**做**：读出每个口径的数、**指到 `产物路径 :: JSON 路径`**、标每份产物的
**可引用状态**、对那句「3.9 倍」做**程序化对账**、**AST 普查 `hurdle_rate` 调用点**。

**不做**：
- ❌ **不重算任何产物字段来「验证」它**（第 109 层：用公式重算验的是「我对公式的理解」，
  不是「产物里那个数是不是上游那个数原样搬过来的」）
- ❌ **不重算 `hurdle_rate`** —— 它是 `aplus/hurdle.py` 的唯一入口（第 144 层）
- ❌ **不出 D2 判决** —— 判决在 `basket_nav_01_*.json::verdict_rule`，且**已经出了**
- ❌ **不手打任何散文数字**（第 142 层）—— 比值全部由本脚本算

🚨 **本文件第一版写着「9 条仓内调用点」，AST 实测是 15 条**，而会话摘要把同一件事
写成 16 条 ⇒ **三个人口版本、三个数** ⇒ **手数表格在散文里必错，只能程序数**。
本版改为 `hurdle_call_sites()` **AST 实测**，并把「本文件刻意不吃默认值」单列。

## 三个「不能引用」的理由**互不相同**（必须分开标）

| 产物 | 理由 |
|---|---|
| `bridge_error.json` | 🚨 **无 sidecar** ⇒ `unknown` = **不许引用** |
| `k_sweep_summary.json` | ⚠️ **有章但 `code_dirty=true`** ⇒ `trustworthy` 恒 `False` |
| `factor_width_mde.json` | ⚠️ 同上 |

⇒ 压成一句「都不可引用」是错的：三种失效原因会被后人当成一种统一处理。

## 用法

```bash
.venv/bin/python scripts/d2_gap_calibers.py
```
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aplus import provenance  # noqa: E402
from aplus.hurdle import (  # noqa: E402
    COST_REAL_50K,
    DAYS_PER_YEAR,
    TARGET_NET,
    hurdle_rate,
    rounds_per_year,
)

GENERATOR = "scripts/d2_gap_calibers.py"
BN_REL = "data/reports/basket_nav_01_2026-10-16.json"
FWM_REL = "data/reports/factor_width_mde.json"
KS_REL = "data/reports/k_sweep_summary.json"
BE_REL = "data/reports/bridge_error.json"

# 判决臂的 K。**不许有默认值**：少传参数必须 TypeError，
# 否则「判决臂 vs 敏感性臂」的区分会被某次调用悄悄抹掉（第 119 / 182 层）。
VERDICT_ARM = "equal_weight"
VERDICT_K = 21

# ⚠️ 本文件是**全仓唯一**刻意以「不传 `rounds_per_year`」的方式调用
# `hurdle_rate` 的地方 —— 目的正是测出那个默认值的量纲。
# ⇒ 「生产调用点必须显式传参」这条守卫**必须按文件名豁免它**，
#   否则那条守卫从第一天起就是红的，而**永远红的守卫与恒真的一样有害**：
#   它会把人推向「改 `hurdle_rate` 的默认值」这个**错误修法**
#   （`evolve_race.py:534` 用**三个位置参数**调用，参数插中间会静默改语义）。
DELIBERATE_DEFAULT_PROBE = GENERATOR

_SKIP_DIRS = {".venv", ".git", "node_modules", ".testtmp",
              "__pycache__", ".pytest_cache"}


def read_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def cite(rel: str, *path: str) -> dict:
    """读**一个**产物字段，附出处。**只读不重算**（第 109 层）。"""
    node = read_json(rel)
    for key in path:
        node = node[key]
    return {"value": node, "source": f"{rel} :: {'.'.join(path)}"}


def derived(name: str, value, why: str) -> dict:
    """标出**这个数是本脚本算的**，不是产物自报的（第 109 层）。

    🚨 不许把 derived 与「产物自报」混列 —— 两者读起来同样确定，
    而一个是「上游搬过来的」，一个是「我算的」。上游漂了 derived 会跟着漂。
    """
    return {"value": value, "derived_by": f"{GENERATOR} :: {name}", "why": why}


def citable(rel: str) -> dict:
    """一份产物的可引用状态。**三种状态互不相同**，不许压成一个标签。"""
    stamp = provenance.read_stamp(ROOT / rel)
    if stamp is None:
        return {
            "citable": False,
            "state": "NO_SIDECAR",
            "why": "无 sidecar ⇒ 按 aplus/provenance.py 自己的规矩，"
                   "`unknown`（解析不出生成器）= **不许引用**",
        }
    dirty = stamp.get("code_dirty")
    return {
        "citable": not dirty,
        "state": "STAMPED_BUT_DIRTY" if dirty else "STAMPED_CLEAN",
        "generator": stamp.get("generator"),
        "stamped_at": stamp.get("stamped_at"),
        "code_dirty": dirty,
        "why": ("带章，但 `code_dirty=true` ⇒ 按本仓规矩 `trustworthy` 恒 `False`，"
                "引用时章与脏两句都要带" if dirty else "带章且工作区干净"),
    }


def hurdle_call_sites() -> dict:
    """AST 普查 `hurdle_rate` 的全部调用点。

    量的是「**有没有显式传 `rounds_per_year`**」，因为该参数默认取
    `DAYS_PER_YEAR`（**交易日数**）而它要的是**年调仓次数**（= 244/K）⇒ 量纲错。

    🚨 **用 AST 不用 grep**：注释与字符串会满足子串断言（第 13 层）。
    🚨 **不手数**：第一版的「9 条」与摘要的「16 条」都是手数的，
    实测是 15 条 ⇒ 三个版本、三个数。
    """
    prod_explicit: list[dict] = []
    prod_missing: list[dict] = []
    self_missing: list[dict] = []
    test_explicit = 0
    test_missing: list[dict] = []

    for path in sorted(ROOT.rglob("*.py")):
        if _SKIP_DIRS & set(path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        is_test = rel.startswith("tests/")
        is_prod = (not is_test) and (rel.startswith("aplus/")
                                      or rel.startswith("scripts/"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else None)
            if name != "hurdle_rate":
                continue
            # 显式传参 = 关键字里有它，或位置参数达到第 3 个
            explicit = ("rounds_per_year" in {k.arg for k in node.keywords if k.arg}
                        or len(node.args) >= 3)
            rec = {"at": f"{rel}:{node.lineno}", "explicit": explicit}
            if is_test:
                test_explicit += int(explicit)
                if not explicit:
                    test_missing.append(rec)
            elif is_prod:
                if explicit:
                    prod_explicit.append(rec)
                elif rel == DELIBERATE_DEFAULT_PROBE:
                    self_missing.append(rec)     # 刻意走陷阱的探针
                else:
                    prod_missing.append(rec)

    return {
        "n_prod_explicit_rounds_per_year": len(prod_explicit),
        "n_prod_silently_taking_the_default": len(prod_missing),
        "silently_taking_the_default_sites": prod_missing,
        "n_test_explicit": test_explicit,
        "test_silently_taking_the_default": test_missing,
        "exempted_on_purpose": {
            "file": DELIBERATE_DEFAULT_PROBE,
            "n_sites": len(self_missing),
            "sites": self_missing,
            "why": "本文件**刻意**不传 `rounds_per_year`，目的正是测出"
                   "那个默认值的量纲。豁免按**文件名**、不是按注释。",
        },
        "method": "AST 遍历 `ast.Call`（grep 会被注释/字符串满足，第 13 层）",
        "verdict": ("生产调用点全部显式传参，零处静默回落"
                    if not prod_missing else
                    "🚨 生产上存在静默吃默认值的调用点"),
    }


def account_gap(bn: dict) -> dict:
    """**基准无关**的 D2 缺口。依据是产物 `verdict_rule` 原文，不是恒等式推理
    （恒等式定不了语义，第 176 层）。"""
    c = bn["verdict_reading"]
    m = bn["verdict_mde_annual"]
    t = bn["target"]
    return {
        "c": cite(BN_REL, "verdict_reading"),
        "m": cite(BN_REL, "verdict_mde_annual"),
        "u_upper_one_sided_95": cite(BN_REL, "verdict_upper_one_sided_95"),
        "t": cite(BN_REL, "target"),
        "gap_pp": derived("account_gap", (t - c) * 100.0,
                          "`target − verdict_reading`，**不是产物字段**"),
        # 🚨 正向语义（|c| ≥ m ⇒ 符号定得了）。键名不许与内容反着说（第 179 层）。
        # 🚨🚨 **且不许与产物撞名**：`basket_nav_01` 里 `sign_determinable*`
        # 已有 8 处同名字段，而它们各自在比**别的两个数**
        # （`excess_contribution_ratio.bt003.*` 量 annual_pct vs 该腿自身 MDE；
        #  `excess_legs_own_mde.毛超额.*` 量算术毛超额 vs 算术 MDE；
        #  `gap_to_target_by_basis` 四处量 |c| vs benchmark MDE）。
        # ⇒ 本键带 `account_` 前缀，把「账户口径」写进名字。
        #    **绝不改产物字段名** —— 改名会波及全部下游复制点（第 73 层）。
        "account_sign_determinable": abs(c) >= m,
        "account_sign_determinable_why":
            "`abs(c) >= m`。为假时**连符号都定不了**：既不许说「达不到」，"
            "也不许说「接近」⇒ **这是 D2 唯一可引用的缺口陈述。**",
        "verdict_rule_verbatim": bn["verdict_rule"],
    }


def required_by_benchmark(bn: dict) -> dict:
    """两个**基准口径**下的「所需毛超额」+ 一个加性口径。同一目标差数倍。"""
    g = bn["gap_to_target_by_benchmark"]
    equal, cap = g[VERDICT_ARM], g["cap_weight"]
    default = hurdle_rate()          # 基准=0.0（签名默认值），量纲错的探针
    arm_hurdle = hurdle_rate(rounds_per_year=rounds_per_year(VERDICT_K))
    return {
        VERDICT_ARM: {
            "role": equal["role"],
            "c_mkt_fraction": equal["c_mkt_fraction"],
            "required_excess_annual": equal["required_excess_annual"],
            "source": f"{BN_REL} :: gap_to_target_by_benchmark.{VERDICT_ARM}",
            "note": "🚨 **判决臂是预注册且被守卫钉着的**（第 91 层：看过数据不许换判据）"
                    "⇒ 读数难看**不是**换臂的理由 ⇒ **加对照臂并显式标 role**（第 183 层）。",
        },
        "cap_weight": {
            "role": cap["role"],
            "c_mkt_fraction": cap["c_mkt_fraction"],
            "required_excess_annual": cap["required_excess_annual"],
            "source": f"{BN_REL} :: gap_to_target_by_benchmark.cap_weight",
            "note": "⚠️ 敏感性臂。**不是判决臂** ⇒ 不许拿它的读数去说「差 N 倍」。",
        },
        "hurdle_rate_default_benchmark_0": {
            "role": "⚠️ **不是基准口径，是「基准=0」的加性口径**"
                    "（`hurdle_rate` 的签名默认值 `benchmark_annual=0.0`）",
            "required_excess_annual": default,
            "source": "aplus/hurdle.py :: hurdle_rate()（**代码**，不是产物）",
            "note": "🚨 **这个默认值量纲是错的** ⇒ 见 `hurdle_rate_signature_trap`。",
        },
        "spread_between_the_two_benchmark_arms_x": derived(
            "required_by_benchmark.spread_between_the_two_benchmark_arms_x",
            cap["required_excess_annual"] / equal["required_excess_annual"],
            "两个**基准口径**下的 `required_excess_annual` 相除 ⇒ "
            "⇒ **同一个「年化 20%」目标，所需毛超额随基准而变**（第 183 层）。"
            "🚨 **这个比值不许被当成「有一个错了」** —— 是问的问题不同。"),
        "note": "⇒ 但**达标判决本身不需要基准**：账户 `market + net_excess ≡ 篮子净`"
                "（依据是生成器 caliber 两句原文，**不是**恒等式推理）。",
    }


def measured_by_caliber(bn: dict, fwm: dict) -> dict:
    """两种**实测口径**。判决链用的是**比值年化**，不是算术。"""
    arith = bn["excess_legs_own_mde"]["毛超额"]["arith_ann"]
    w10 = fwm["widths"]["10"]["gross_annual_pct"]
    ratio_ann = bn["excess_contribution_ratio"]["bt003"]["annual_pct"]
    per = bn["gap_to_target_by_benchmark"][VERDICT_ARM]["per_combo"]["bt003"]
    equal = bn["gap_to_target_by_benchmark"][VERDICT_ARM]
    return {
        "arith_mean_times_R": {
            "annual": arith,
            "source": f"{BN_REL} :: excess_legs_own_mde.毛超额.arith_ann",
            "role_verbatim": bn["caliber"]["arith_role"],
            "usable_for_verdict": False,
        },
        "excess_contribution_ratio": {
            "annual": ratio_ann,
            "source": f"{BN_REL} :: excess_contribution_ratio.bt003.annual_pct",
            "role": "★ **判决链在用的那一个**"
                    f"（`caliber.verdict_tier_is` = `{bn['caliber']['verdict_tier_is']}`）",
            "usable_for_verdict": True,
        },
        "one_number_two_copies_no_guard": {
            "arith_ann": arith,
            "factor_width_mde_widths_10_gross_annual_pct": w10,
            "bit_identical": arith == w10,
            "relative_diff": abs(arith - w10) / abs(arith),
            "note": "🚨 **两份产物各存同一个数，而两份副本之间没有任何守卫**"
                    "（第 112 层单点依赖）。**判据是逐位相等，不是「差不多」。**",
        },
        "naming_debt_annual_pct_holds_a_fraction": {
            "field": f"{BN_REL} :: excess_contribution_ratio.bt003.annual_pct",
            "name_says": "百分数",
            "value_is": "**分数**（与 `arith_ann` / `required_excess_annual` 同尺度）",
            "how_we_know_same_scale":
                "该字段 ÷ 判决臂 `required_excess_annual` = "
                "`per_combo.bt003.measured_over_required`，而后者是**无量纲比值**"
                "⇒ 分子分母必同尺度",
            "note": "🚨 **第 97 层复发**（字段名带 `pct` 而值是分数）。"
                    "**本轮不改名** —— 改名波及所有下游复制点而**不修任何东西**"
                    "⇒ 登记为债。⚠️ 消费者照名字当百分数读会**差 100 倍**。",
        },
        "arith_over_ratio": derived(
            "measured_by_caliber.arith_over_ratio",
            arith / ratio_ann,
            "**算术口径 ÷ 比值口径**（第 7 层：两个各自正确的数**不是同一个量**）。"
            "🚨 而产物 `caliber.arith_role` **逐字声明算术口径不许下判决**"
            "⇒ **判决链里引的 4.81% 是算术口径，而产物自己说算术不许下判决。**"),
        "measured_over_required": {
            "value": per["measured_over_required"],
            "measured_sign_determinable": per["measured_sign_determinable"],
            "measured_abs_over_mde": per["measured_abs_over_mde"],
            "neither_side_is_a_point_estimate": equal[
                "neither_side_is_a_point_estimate"],
            "note": "🚨🚨 **不许把 6.06% 说成「离目标还有多远」**："
                    "分子分母**都不是点估计**（`neither_side_is_a_point_estimate=True`）"
                    "⇒ **最远只能说「差一个数量级以上」的形状陈述**（第 160 层）。",
        },
    }


def docs_reconciliation() -> dict:
    """对 `CLAUDE.md` 那句「差 3.9 倍」做**程序化对账**。

    🚨 本块的两个操作数（`ceiling` / `k21_gross`）是**从 CLAUDE.md 散文里抄来的**，
    **不是产物值** —— 它们**正是被质疑的对象** ⇒ 逐字标注，不许当真相源。
    唯一从代码算来的是门槛（`aplus.hurdle.hurdle_rate` 唯一入口）。
    """
    hurdle = hurdle_rate(rounds_per_year=rounds_per_year(VERDICT_K))
    # ↓↓↓ 散文值（CLAUDE.md 判决节 / K 扫描表），**不是产物读数**
    ceiling = 0.11       # 「账户天花板 11%/年毛超额」——出自无 sidecar 的 bridge_error.json
    k21_gross = 0.0678   # K 扫描表 K=21 行的「毛超额/yr」——出自 code_dirty 的 k_sweep_summary.json
    # ↑↑↑ 散文值
    on_ceiling = hurdle / ceiling
    on_k21 = hurdle / k21_gross
    return {
        "claim_in_claude_md": {
            "text": "毛超额上限 11%/年 vs 门槛 26.39%/年 ⇒ 差 3.9 倍，"
                    "不是差一个数量级。",
            "operands_are_PROSE_not_artifact": {
                "ceiling_pct": ceiling * 100,
                "ceiling_source": "CLAUDE.md 判决节散文 ⇒ 其真实出处是 "
                                  "`bridge_error.json`（🚨 无 sidecar ⇒ 不可引用）",
                "k21_gross_pct": k21_gross * 100,
                "k21_gross_source": "CLAUDE.md K 扫描表 K=21 行 ⇒ 其真实出处是 "
                                    f"`{KS_REL}`（⚠️ 带章但 `code_dirty=true`）",
                "stated_ratio": 3.9,
            },
            "hurdle_here_is_computed_not_typed":
                f"aplus.hurdle.hurdle_rate(rounds_per_year="
                f"rounds_per_year({VERDICT_K}))",
        },
        "ratio_of_the_stated_operands": derived(
            "docs_reconciliation.ratio_of_the_stated_operands", on_ceiling,
            "门槛 ÷ 11%。**11% 是散文值，不是产物值** ⇒ 这个比值带的是"
            "「散文里的 11」的口径，不许当测量结果引用。"),
        "ratio_stated_claims": 3.9,
        "self_consistent": abs(on_ceiling - 3.9) < 0.05,
        "where_3_9_actually_comes_from": derived(
            "docs_reconciliation.where_3_9_actually_comes_from", on_k21,
            "门槛 ÷ K=21 毛超额。**6.78% 同样来自散文。**"),
        "conclusion": "🚨 **被比的两个数（11 / 26.39）与比值的两个操作数（26.39 / 6.78）"
                      "来自两个不同的量对。** ⇒ 不是笔误，是**三个口径被并成了一句话**"
                      "（第 7 种形状）。**该句作为判决依据不成立。**",
        "why_11_and_11_6_collide": {
            # `derived` 而不是 `cite`：它是**代码算出来的**，不是从产物读的。
            # 两个来源返回长得一样的 `{"value", …}` 字典 ⇒ 读者分不出
            # 「上游搬过来的」与「本脚本算的」（第 109 层）。
            "rounds_per_year_at_verdict_k": derived(
                "docs_reconciliation.rounds_per_year_at_verdict_k",
                rounds_per_year(VERDICT_K),
                f"aplus.hurdle.rounds_per_year({VERDICT_K}) —— "
                "K → 年调仓次数走 `aplus.hurdle` 唯一入口，不许手打（244/21 不取整）"),
            "note": "⇒ K 扫描表 K=21 那行的「次/年」= **11.6**，"
                    "而判决节散文写「11%/年毛超额」。"
                    "**两个不同的量，形状上只差一个小数点，相隔约 60 行。**"
                    "⇒ 且 `26.39% = 目标 + 次/年 × 单边成本` ⇒ "
                    "**11.6 正是这个门槛的乘数本身** ⇒ "
                    "同一行里「11.6」与门槛**互为因果，不是两条独立证据**。",
        },
        "disposition": "按第 25 层：**标注作废，不抹掉。**"
                       "撤回理由就是本块 —— 那句要被改成带口径标签的对照表。",
    }


def hurdle_signature_trap() -> dict:
    """`hurdle_rate` 的**默认值是个陷阱**：默认 `rounds_per_year = DAYS_PER_YEAR`
    ⇒ 一年调仓 244 次 ⇒ 门槛被算成 **154.2%**，而不是判决臂的 26.39%。"""
    rpy = rounds_per_year(VERDICT_K)
    default = hurdle_rate()
    arm = hurdle_rate(rounds_per_year=rpy)
    census = hurdle_call_sites()
    return {
        "default_rounds_per_year": DAYS_PER_YEAR,
        "hurdle_with_default": default,
        "hurdle_with_verdict_arm": arm,
        "default_over_verdict_arm": default / arm,
        "dimensionally_wrong_because":
            "`DAYS_PER_YEAR` 是**交易日数**，而这个参数要的是**年调仓次数**"
            "（= DAYS_PER_YEAR / K）",
        "call_site_census": census,
        "consequence":
            "🚨 生产上**没有一处**吃过这个默认值"
            f"（{census['n_prod_explicit_rounds_per_year']} 处生产调用点"
            "**全部显式传参**，0 处静默回落）"
            "⇒ **它从未被走过，但它活着、错的、且无守卫。**"
            "⇒ **下一个省略它的调用点会静默拿到 "
            f"{default / arm:.4f} 倍的门槛。**",
        "why_not_changed_this_round":
            "🚨 **不改默认值、不改签名**：`aplus/evolve_race.py:534` 用**三个位置参数**"
            "调用 ⇒ 把 `rounds_per_year` 往中间插会**静默改语义**（第 182 层）。"
            "⇒ 处置是**加守卫**（钉「生产调用点必须显式传参」，并豁免本文件的探针），"
            "不是改默认值。",
    }


def build() -> dict:
    bn, fwm = read_json(BN_REL), read_json(FWM_REL)
    return {
        "what_this_is": "D2 缺口陈述的**口径对照表** —— **不是 D2 判决**。"
                        "D2 判决在 basket_nav_01 的 verdict_rule，且**已经出了**。",
        "verdict_benchmark_arm": VERDICT_ARM,
        "verdict_k": VERDICT_K,
        "the_only_citable_d2_gap": account_gap(bn),
        "required_gross_excess_by_caliber": required_by_benchmark(bn),
        "measured_gross_excess_by_caliber": measured_by_caliber(bn, fwm),
        "docs_reconciliation": docs_reconciliation(),
        "hurdle_rate_signature_trap": hurdle_signature_trap(),
        "citable_status": {
            BN_REL: citable(BN_REL),
            FWM_REL: citable(FWM_REL),
            KS_REL: citable(KS_REL),
            BE_REL: citable(BE_REL),
        },
        "what_this_cannot_prove": [
            "🚨 **不许把任何两个口径下的数相除当「差 N 倍」** —— "
            "第 7 层：两个各自正确的数不是同一个量。本块存在的**全部意义**"
            "就是防止这件事。",
            "🚨 **不许把 `measured_over_required` 说成量化结论** —— "
            "分子分母**都不是点估计**（产物自己禁止）。"
            "最远只能说「差一个数量级以上」的**形状**陈述。",
            "🚨 **不许引用 `bridge_error.json` 的任何数** —— 无 sidecar。",
            "🚨 **引用 `factor_width_mde.json` / `k_sweep_summary.json` 时，"
            "必须同时说「带章」与「`code_dirty=true`」两句。**",
            "🚨 **本表不换判据臂** —— 判决臂是预注册的，"
            "敏感性臂的读数差很多倍是**要报告的事实**，不是缺陷（第 183 层）。",
            "⚠️ **本表只做口径对照，不出 D2 判决**，也不重跑任何回测；"
            "所有产物字段**只读不重算**（第 109 层）。",
        ],
        "caliber": {
            "R_days_per_year": DAYS_PER_YEAR,
            "R_source": "aplus.hurdle.DAYS_PER_YEAR",
            "cost_per_round": COST_REAL_50K,
            "target_net": TARGET_NET,
            "single_entry_for_hurdle":
                "门槛走 `aplus.hurdle.hurdle_rate` 唯一入口，**不许手打**（第 144 层）。",
            "no_hand_typed_prose_numbers":
                "所有比值由本脚本算；唯二手抄的 `ceiling` / `k21_gross` "
                "**在产物里逐字标为散文值**，因为它们正是被质疑的对象（第 142 层）。",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/reports/d2_gap_calibers_2026-10-17.json")
    args = ap.parse_args(argv)

    # 必须早于任何工作（第 125 层：放晚了就白抓）
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=expect_bytes)
    print(f"已写 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
