#!/usr/bin/env python3
r"""Y5 —— **净值口径**的随机臂：账户年化 +10.5563% 在 100 次随机抽样里排第几。

## 它问的是哪一个量（🚨 与 `placebo_control.py` 的 Y0 完全不同族）

| | Y0 `placebo_control` | **Y5 本脚本** |
|---|---|---|
| 统计量 | 分块 **t**（超额口径） | **账户净值复利 CAGR** |
| 被问的数 | `t(策略) = 2.44` 是否高于随机臂的 t | 账户年化 **+10.5563%** 是否高于随机臂 |
| 判决入口 | `hit_threshold`（毛超额 vs 门槛 26.39%） | §18.1 已判 **`|c|/M = 0.4656 < 1` ⇒ 连符号都定不了** |

⇒ **Y5 不是 Y0 的重跑**：Y0 判的是「排序有没有信息量」，
Y5 判的是「**账户净值这一个数**相对随机臂的位置」——
而那正是 §18 判决卡住的地方。两条实验**互不替代**。

## 预注册

`docs/PREREG_7Y_NET_NAV_2026-10-16.md`：

- **§10** 四要素（抽样域 / 次数 / 年化 / 分位）+ §10.7 成本 + §10.8 落盘契约
- **§19** 实现期补记（**写于跑任何一个 rep 之前**）：四分支优先级常量、
  `mde_resolution` 的定义修正（`|s−p95|` → **p95 自身的 bootstrap CI95 半宽**）、
  策略臂自身 MDE 的处置、**冒烟 reps=3** 的数字与理由

## 三处最容易写错的地方

1. 🚨 **`net_excess_*` 是差序列，不是净值序列**（§18.1：直接当净值算 CAGR 差 10.01pp）。
   账户净值必须**逐日** `market_return + net_excess_<tag>` 相加。
2. 🚨 **`E-下` 必须可达**。§10.6 原判据 `p95 − p5 < |s − p95|` 会让 `s < p5` 时
   恒真 ⇒ **否证出口永不可达**（实测 18 组采样 `E-下 = 0`）。修法见 §19.1。
3. 🚨 **`p95 − p5` 换成带宽**后判据不再自噬（与 `s` 无关）⇒ 四条分支**全部可达**。

## 用法

```bash
.venv/bin/python scripts/measure_random_arm_nav.py --smoke      # 3 rep，独立路径，判决为 null
.venv/bin/python scripts/measure_random_arm_nav.py              # 100 rep（数小时，须后台）
```

⚠️ 面板 GB 级、600 遍 `evaluate_weights` ⇒ **必须后台跑**，
且与赛马同跑会 OOM（CLAUDE.md 环境节）。
守卫测试全部用**合成横截面**，不碰面板。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import begin_run, configure_noisy_stderr  # noqa: E402

configure_noisy_stderr()

import aplus.verify as verify_mod  # noqa: E402
import backtest_walk_forward_weights as bw  # noqa: E402
from aplus import provenance  # noqa: E402
from aplus.backtest import STOCK_COST_DEFAULT, build_cross_sections  # noqa: E402
from aplus.composite import unpack  # noqa: E402
from aplus.factor import FACTOR_NAMES, composite_score  # noqa: E402
from aplus.hurdle import DAYS_PER_YEAR as R  # noqa: E402
from aplus.predict import build_predictions  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
# ★ 逐年权重、run_year 全部从主回测 import —— 一件都不自己写。
#   自己写一遍 = 造第二台测量仪，两台之间的差永远查不出来。
from backtest_walk_forward_weights import (ENTRY_LAG, K, WINDOW_END_PINNED,  # noqa: E402
                                           run_year, weights_by_year)
# ★ 复利/算术年化**也**从 ① 的判决脚本 import：逐位对拍 `verdict_reading` 是
#   本脚本的外部锚，重写一遍公式就是对拍自己。
from factor_width_mde import scaled_min_basket  # noqa: E402
from measure_basket_nav_01 import tier as _tier  # noqa: E402
# ★ 随机臂的置换器**从 placebo import**（保域：NaN 原地不动，只换顺序）。
#   🚨 `make_shuffle_list` **不保域**（每行都赋有限值 ⇒ notna 剔不掉）
#   ⇒ 它的抽样域 ⊇ 策略臂的域，两臂可能压根不可比（§10.2 先验检查）。
from placebo_control import make_shuffle_score  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

PREREG = "docs/PREREG_7Y_NET_NAV_2026-10-16.md"
SECTIONS = "§10 + §19"

# ── 复现 `wf_weights_backtest_k21.json` 的配置，一个字不改 ───────────────
# 🚨 这些不是自由参数：**对拍** `basket_nav_01_2026-10-16.json::verdict_reading`
#   要求本脚本跑出的判决档 cagr 逐位等于 0.10556283216930074。
#   任何一个数对不上 ⇒ `verdict=None`，不出结论（§10.3）。
TOP_N = 10
BOTTOM_N = 10
JUDGEMENT_START = "2021-01-01"

# ── 次数与种子（§10.4 Y5-R1/R2）─────────────────────────────────────────
#: 第 r 个 rep 的种子 = 本值 + r。冒烟用**完全相同**的种子 ⇒ 冒烟是正式跑的**前缀**。
Y5_SEED_BASE = 20261016
N_RANDOM_REPS = 100
#: 冒烟次数（§19.5，写于看到任何 rep 数字之前）。
#: 🚨 n=3 时 p95 == max、p5 == min ⇒ **冒烟数字不得进入任何判决**。
N_SMOKE_REPS = 3

#: p95 的 bootstrap 次数与种子（§10.5 Y5-Q2 要求落 p95 的 bootstrap 95% 区间）。
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = Y5_SEED_BASE + 900_000

# ── 成本档（§10.7 C2：三档并排，判决用中档）────────────────────────────
#: 判决档 = 生产 `STOCK_COST_DEFAULT`（与 `basket_nav_01` 的 `bt003` 同一档）。
JUDGE_TAG = "bt003"
#: 零成本档与高成本档**要临时登记进生产表**（`bw._tier_cost` 的 table）。
#: 加了标签而忘了登记费率 ⇒ `KeyError` 当场崩，不静默少算一档。
EXTRA_COST_TAGS = ("zero", "hi006")


def _extra_tier_cost(tag: str) -> float:
    """三档费率。🚨 零档是 `0.0`，不是 `1e-18` 之类的占位 ——
    零成本是一个**真实口径**（毛超额 + 基准本身），不是「没算」。"""
    if tag == "zero":
        return 0.0
    if tag == "hi006":
        return 2.0 * STOCK_COST_DEFAULT
    raise KeyError(f"未登记的成本档 {tag!r}")


# ── 分位网格（§10.5 Y5-Q1）──────────────────────────────────────────────
QUANTILES = (0.0, 5.0, 25.0, 50.0, 75.0, 95.0, 100.0)
QUANTILE_KEYS = ("min", "p5", "p25", "median", "p75", "p95", "max")
#: 诊断用：单个 rep 的日期清单最多落盘多少个（总数另存，不截断总数）。
#: 🚨 只截断**明细**，不截断**计数** —— 截断计数会让「16/100」这类数
#: 在产物里变得不可核对，而那正是本诊断要回答的东西。
DIAG_MAX_DATES = 40


# ═══════════════════════════════════════════════════════════════════════
# 四分支的**判定顺序**本身就是判据（§19.2）
# ═══════════════════════════════════════════════════════════════════════
#:
#: 顺序规则：**按结论的严厉程度降序**，且「分辨率不足」必须排在最前 ——
#: 它是**门**不是**结论**（后面三条位置性主张在分辨率不足时全部无根据）。
#:
#: 🚨 为什么顺序会有人来改、而预注册必须先写死：
#:   「重叠度高」这个指标**不区分**「独立条件偶然同真（该重排）」
#:   与「特例-一般嵌套（本就该这样）」。这里 E-判不出 与 E-上/E-下 在
#:   带宽 < 分辨率时**同时**为真 ⇒ 顺序一换，判决类别就换。
#:   ⇒ 提到模块级常量 + 穷举守卫钉住，而不是留在书写位置里。
_BRANCH_ORDER = ("E-判不出", "E-上", "E-下", "E-内")

#: 四条分支的**原文**，逐字落进产物（读者要能对着预注册核）。
# 🚨 §19.4 的代价：这个判决**对绝对水平零信息**。
# 提到模块级是为了让守卫能在**运行时**问它（第 124 层：判据不可问就没有守卫）——
# 它原本嵌在 `_run()` 里，只能靠子串从源码里刨，而子串会被注释满足（第 13 层）。
WHAT_THIS_CANNOT_PROVE = [
    "**不能**证明账户年化 +10.56% 本身测出来了 —— "
    "|c|/M = 0.4656 < 1 ⇒ 绝对水平那一半仍是「连符号都定不了」（§18）",
    "**不能**把 E-上 读成因子有效 —— 它只说账户净值这一个数相对随机臂偏高",
    "**不能**替代前瞻：100 个 rep 共用**同一段 6 年历史**，"
    "它们之间不独立 ⇒ p5/p95 是「历史内重抽」的分布，不是「未来」的分布",
    "**不能**证明 20%/年可达 —— 本判决对绝对水平零信息",
    "🚨 冒烟产物（--smoke）的任何数字都**不得**进入判决",
]

BRANCH_TEXT = {
    "E-判不出": ("随机臂分布的 p95−p5 带宽小于它自己的 bootstrap 95% 半宽 ⇒ "
                  "上下尾都定不准 ⇒ 任何位置性主张都无根据（**这是门，不是结论**）"),
    "E-上": "账户年化高于随机臂分布的 p95（⚠️ 不等于因子有效，也不等于账户绝对水平可信）",
    "E-下": "账户年化低于随机臂分布的 p5（**否证出口**：稳定跑输随机）",
    "E-内": "账户年化落在随机臂的 [p5, p95] 内（与随机不可区分）",
}


def band(rec: dict) -> float:
    """`p95 − p5`。🚨 **只看带宽，不看 `s`** —— 这正是 §19.1 修掉的自噬：
    原判据 `band < |s − p95|` 让「`s` 越极端越判不出」，越想证伪越说不出来。"""
    return float(rec["p95"] - rec["p5"])


def mde_resolution(boots: np.ndarray) -> float:
    """`mde_resolution` = **p95 自身的 bootstrap 95% 区间半宽**（§19.1）。

    它**是**「p95 这个分位数本身的估计误差」，
    **不是**「策略值离上尾多远」——后者另落 `dist_to_p95`。
    🚨 两者在 §10.6 的字面表述里被混成一句（第 177 层：键名/名不承诺语义）。

    `boots` 是每次重采样算出的 p95 序列。
    """
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float((hi - lo) / 2.0)


def bootstrap_p95(values: np.ndarray, seed: int, draws: int) -> np.ndarray:
    """重采样算 p95。🚨 种子来自常量，**不许字面量**（第 73 层）。"""
    rng = np.random.default_rng(int(seed))
    n = len(values)
    return np.percentile(
        rng.choice(values, size=(int(draws), n), replace=True), 95.0, axis=1)


def branch_conditions(rec: dict) -> dict:
    """四条分支的布尔条件 —— **全部**求值后一起落盘。

    ⚠️ 它们**不互斥**：`E-判不出` 与 `E-上`/`E-下` 在带宽 < 分辨率时同真。
    ⇒ 判决码按 `_BRANCH_ORDER` 取第一条命中，而**哪个条件成立过**
    在 `conditions` 里看得见（与 `placebo_control` 同规矩）。
    """
    s = float(rec["strategy_cagr"])
    return {
        "E-判不出": bool(band(rec) < float(rec["mde_resolution"])),
        "E-上": bool(s > float(rec["p95"])),
        "E-下": bool(s < float(rec["p5"])),
        "E-内": bool(not (s > float(rec["p95"]) or s < float(rec["p5"]))),
    }


def date_axis_diff(tb_keys, strat_keys) -> dict:
    """**为什么** `same_date_axis` 为 False —— 纯诊断，**不进任何判决**。

    ★ 2026-10-16 新增（预注册 `PREREG_7Y_NET_NAV` §21.3 的欠账：
      「让脚本把『哪一天、哪几只票没 bar』落盘，而不是继续想」）。

    🚨 `same_date_axis` 用的是 `list == list` ⇒ **对顺序敏感**。
      所以「日期轴不同」至少有两类成因，而**它们的修法完全不同**：

      | 类别 | `same_set` | 含义 | 机理 |
      |---|---|---|---|
      | ① 集合不同 | `False` | 真的有日子不在随机臂的日期轴上 | 成分下限（`count < MIN_BASKET` ⇒ 整天消失） |
      | ② 仅顺序不同 | `True` | 集合相同、只是次序不同 | 跨年 chunk 拼接次序，**与成分无关** |

      只记 `not same_axis` 会把这两类压成同一个 `True` ——
      症状相同、方向相反（第 12 层的「断言对、成因错」）。

    ⚠️ **本函数不回答「成分下限是不是成因」**：机理已证，**成因未找到**
      （实测 16% 的 rep 触发，而按机理需要 10 只里 8 只以上无下一根 bar，
      差 8 倍以上 ⇒ 必有一个未识别通道）。
      ⇒ 拿到本函数的输出**不等于**拿到了成因。
    """
    tb_keys = list(tb_keys)
    strat_keys = list(strat_keys)
    s_set, t_set = set(strat_keys), set(tb_keys)
    missing = [d for d in strat_keys if d not in t_set]
    extra = [d for d in tb_keys if d not in s_set]
    same_len = len(tb_keys) == len(strat_keys)
    n_pos = sum(1 for a, b in zip(strat_keys, tb_keys) if a != b) if same_len else None
    return {
        "same_set": bool(s_set == t_set),
        "n_missing_from_random": len(missing),
        "n_extra_in_random": len(extra),
        "n_len_diff": len(tb_keys) - len(strat_keys),
        "n_positions_differ": n_pos,
        "n_positions_why_null": (
            None if same_len else
            "两轴长度不等 ⇒ `zip` 只截到公共前缀，位置差会少算 ⇒ "
            "宁可给 None 也不给一个读起来可比、实际不可比的数（第 18 层）"),
        "missing_dates_head": missing[:DIAG_MAX_DATES],
        "extra_dates_head": extra[:DIAG_MAX_DATES],
        "why_heads_only": (f"明细只留前 {DIAG_MAX_DATES} 个；总数在汇总的 *_total 字段里"),
    }


def market_identity(tb_market, strat_market) -> dict:
    """两臂 `market` 的**逐位身份** —— 纯观测，**不进任何分支**。

    ★ 2026-10-16 从逐 rep 循环里的 `if rep == 0:` 提取到模块级。
      此前 100 个 rep 只量了第 0 个，其余 99 个在产物里是 **`None`** ——
      而 `None` 与「量过、结果是 False」**逐位同形**（第 7 层）。

    🚨 **`max_abs_diff == 0` 不蕴含 `identical == True`**：
      `nanmax` 跳过 NaN 位置，而 `array_equal` 对 NaN 判不等
      ⇒ 两臂同位置都是 NaN 时，差为 `0.0` 而 `identical` 为 `False`。
      **两个各自正确的数在这里指向不同的东西** ⇒ 两者都落盘，**不许合并**。

    🚨 **长度不等一律 `raise`**，不返回 `inf`。
      长度耦合是**可证的**：`chunk_to_daily` 的 `market` 由 `keys` 逐项生成
      ⇒ `len(market) ≡ len(keys)`；调用点又先判过 `same_date_axis`
      （即 `tb["keys"] == strat_keys`）⇒ 等长恒成立。
      ⇒ 旧代码那条 `else float("inf")` **结构上不可达**；
      而它一旦可达，落盘的会是一个「看起来很正常、实际代表无穷大」的数。

    ⚠️ **本函数不回答「为什么会差」**：差**是多少**是观测，
      差**来自浮点加法次序还是某条数据路径**是另一件事，这里看不见。
    """
    a = np.asarray(tb_market, float)
    b = np.asarray(strat_market, float)
    if len(a) != len(b):
        raise ValueError(
            f"market 长度不等（随机臂 {len(a)} vs 策略臂 {len(b)}）⇒ 逐位比较无定义。"
            "调用点必须先判 same_date_axis（true ⇒ len(market) ≡ len(keys) "
            "≡ len(strat['market'])）")
    d = np.abs(a - b)
    finite = d[np.isfinite(d)]
    return {
        "identical": bool(np.array_equal(a, b)),
        "max_abs_diff": float(finite.max()) if finite.size else float("nan"),
    }


def summarize_market_identity(per_rep: list) -> dict:
    """把逐 rep 的 `market` 身份**数**成一个汇总块（§10.8 落盘契约）。

    ★ 2026-10-16 新增。此前这块写的是 `checked_on: "rep 0"` ＋ 两处
      `per_rep[0].get(...)` ⇒ ① 一个**键的内建值在替覆盖范围作保证**
      （第 179 层：改成 `"all reps"` 仍是内建保证 ⇒ **删键优于改值**，
      缺键是响亮的、撒谎的键是安静的）② 汇总与逐 rep 记录是
      **两份副本口径**（第 112 层）⇒ 现在三个计数全部数出来，
      **产物里不许写死任何数字**（第 73 层：写死的数会被底稿漂移打脸）。

    🚨 **「不可比」与「不一样」是两个计数，不许合并**（第 7 层）：
      未比较的 rep **不进入** `n_reps_bitwise_different`
      ⇒ 前者是窗口没对齐，后者是两臂连基准都变了，处置完全不同。
    """
    rows = [r for r in per_rep if "market_identical_to_strategy" in r]
    same = [r for r in rows if r["market_identical_to_strategy"]]
    diff = [r for r in rows if not r["market_identical_to_strategy"]]
    diffs = [float(r["market_max_abs_diff"]) for r in rows
             if r.get("market_max_abs_diff") is not None]
    return {
        "n_reps_total": int(len(per_rep)),
        "n_reps_compared": int(len(rows)),
        "n_reps_not_compared": int(len(per_rep) - len(rows)),
        "n_reps_bitwise_identical": int(len(same)),
        "n_reps_bitwise_different": int(len(diff)),
        "max_abs_diff_over_compared_reps": max(diffs) if diffs else None,
        "why_not_compared": (
            "日期轴与策略臂不同的 rep **未被比较**（不是「比较了且不同」）："
            "两个数组逐位比的是**不同的日子**，那个差不是「基准变了」的量（第 7 层）"),
        "why_it_matters": ("随机臂只置换 score、不改 market ⇒ 若为假，"
                           "两臂差的不只是选股（连基准都变了）"),
    }


def same_date_axis(tb: dict, strat: dict, strat_keys: list) -> bool:
    """随机臂的**日期轴**是否与策略臂**逐日相同**（§10.3 的 Y5-A3）。

    🚨 **必须只比日期轴**。`market` 逐位相等**不是**它的证明：日期**部分重叠**时
    两表长度可以**恰好相同**而日期**不同** ⇒ 那就是在两个不同样本上比 CAGR
    （第 7 种形状：两份各自正确的数被当成同一个量）。
    ⇒ 本函数**刻意不读 `market`**；`market` 的逐位比较是 `_run()` 里**另一处独立**的检查。

    提到模块级是为了让「有没有偷偷顺带证明」变成**可问**的判据（第 124 层：
    判据不可问就等于没有守卫）—— 它原本是 `_run()` 里的一行赋值。
    """
    return bool(tb["keys"] == strat_keys)


def verdict_is_adopted(branch: str, verdict) -> bool:
    """判决是否**被采用**（压住时 `branch` 必须与 `verdict` 一起置 None）。

    🚨 键名/内容互相打脸的形态（第 179 层）：判决被压住时若仍把 `branch` 写出去，
    产物就出现「`verdict: null`（没说话）而 `branch: "E-上"`（我判了 E-上）」这种
    自相矛盾的一对，而两条**单独读都成立**。
    ⇒ 这里只回答**一个**问题：**这个码有没有被采用**。
    """
    return verdict is not None


def append_rep_scale_note(verdict, suppressed: list, note: str):
    """给判决句加一条规模标注；**判决已被压住时只记 `suppressed`，不许复活它**。

    🚨 2026-10-16 从 `_run()` 提取（第 124 层：「判据不可问就等于没有守卫」）。
    提取前这段是**嵌套在 `_run()` 里**的四行 ⇒ 守卫只能读 AST 文本，
    没法用夹具问出**行为**；提取后「`verdict=None` 进去还是 `None` 出来」
    是 1 毫秒可问、可变异的事实。

    🚨 **这个函数的存在理由是一个潜伏缺陷**（预注册 §20.5/§20.6）：
    它原来是 `verdict = f"{verdict}　🚨 仅 N rep…"`。
    而 `verdict` 会被上面四条压成 `None`（冒烟 / 对拍失败 / 先验检查不过 /
    日期轴不同）⇒ f-string 会把 `None` 拼成**字面量** `'None　🚨 仅 5 rep…'`
    ⇒ `verdict_is_adopted` 的判据 `verdict is not None` 判 **True**
    ⇒ 产物里出现一对自相矛盾的键（第 179 层）：
    `branch: "E-内"`（说「我判了 E-内」）配 `verdict: "None 🚨 …"`。

    ⚠️ 那两条路径**互斥且都必须有证据**，少一条本函数就会退化成半装饰：
    ① `verdict` 是真字符串 ⇒ 返回值**必须含 `note`**（否则标注根本没写进去）；
    ② `verdict is None` ⇒ 返回值**必须仍是 `None`**，且 `suppressed` **必须增长**
    （否则缺陷原样复现，而这条正是杀掉变异 Y11 的那一条）。
    ⇒ **不许**把 ② 写成「返回值不是 `None` 的字符串就算过」——
    那正是原缺陷的形状（用**内容**去判断**存在性**，第 179 层）。
    """
    if verdict is not None:
        return verdict + note
    suppressed.append(note + "；判决已被压住 ⇒ 不再复活")
    return None


def decide_branch(rec: dict, conds: dict) -> str:
    """按 `_BRANCH_ORDER` 取第一条命中。

    🚨 **只读 `conds`，一个条件都不重算** —— `decide_branch` 与
    `branch_conditions` 分开，是为了让「顺序」成为**唯一**被测的性质，
    而不是一个被测性质混在条件求值里。
    """
    for name in _BRANCH_ORDER:
        if conds[name]:
            return name
    raise RuntimeError(f"四条分支全不命中（顺序或条件表坏了）：conds={conds}")


# ═══════════════════════════════════════════════════════════════════════
# 抽样域先验检查（§10.2）—— 不可比的对照臂比没有更坏
# ═══════════════════════════════════════════════════════════════════════
DOMAIN_RULE = ("U_t ≜ { filter_universe 后 composite_score 非 NaN }。"
               "随机臂用 placebo_control.make_shuffle_score（保域置换）："
               "NaN **原地不动**，只换顺序 ⇒ 它的域与策略臂**逐日相同**。"
               "🚨 不许用 make_shuffle_list：它给每行赋有限值 ⇒ notna 剔不掉 ⇒ 域 ⊇ 策略域。")


def domain_probe(xs: dict, wmap: dict, score_transform_factory,
                 label: str) -> dict:
    """逐判决日打印 `|U_策略|` / `|U_随机|` / 相等布尔 / 差异规模，
    并**在真实横截面上**验两腿构造上不相交。

    🚨 合成横截面造不出 `min_history=41` 的 NaN 结构 ⇒ 先验检查**必须真跑**
       （§19.5）。守卫用合成夹具，那是另一件事。
    🚨 `build_predictions` 是**生产函数**，本脚本不许另写一遍取 top/bottom 的路径 ——
       「另写更快的路径」= 测的是另一个东西（`placebo_control` 的 same 铁律）。
    """
    days = sorted(xs["_dates"])
    per_day = []
    for day in days:
        universe = xs.get(day)
        if universe is None or not len(universe):
            continue
        year = int(str(day)[:4])
        w = wmap.get(year, {}).get("weights")
        if w is None:
            continue
        scores = composite_score(universe, w)
        n_scored = int(np.count_nonzero(pd.notna(scores)))
        st = score_transform_factory() if score_transform_factory else None
        up, down = build_predictions(universe, top_n=TOP_N, bottom_n=BOTTOM_N,
                                     weights=w, score_transform=st, as_of=day)
        c_up = set(up["code"].astype(str))
        c_down = set(down["code"].astype(str))
        per_day.append({
            "day": str(day),
            "n_scored": n_scored,
            "n_up": int(len(up)),
            "n_down": int(len(down)),
            "overlap": len(c_up & c_down),
        })
    return {"label": label, "n_days": len(per_day),
            "days": per_day,
            "overlap_all_zero": bool(all(d["overlap"] == 0 for d in per_day)),
            "max_overlap": max((d["overlap"] for d in per_day), default=0)}


def compare_domains(strategy: dict, random_: dict) -> dict:
    """逐日印 `|U_策略|` vs `|U_随机|` + 相等布尔 + 差异规模。"""
    by_day = {d["day"]: d for d in random_["days"]}
    rows, gaps, n_eq = [], [], 0
    for d in strategy["days"]:
        o = by_day.get(d["day"])
        if o is None:
            continue
        a, b = abs(d["n_scored"]), abs(o["n_scored"])
        eq = bool(a == b)
        n_eq += int(eq)
        gaps.append(abs(a - b))
        rows.append({"day": d["day"], "abs_u_strategy": a,
                     "abs_u_random": b, "equal": eq, "gap": abs(a - b)})
    return {
        "rule": DOMAIN_RULE,
        "n_days_compared": len(rows),
        "n_days_domains_identical": n_eq,
        "all_identical": bool(rows and n_eq == len(rows)),
        "max_gap": int(max(gaps)) if gaps else 0,
        "mean_gap": (float(sum(gaps) / len(gaps)) if gaps else 0.0),
        "note": ("🚨 差异**不等于**不可比：域相同而分数多重集不同 ⇒ 选出的股票必然不同，"
                 "那正是随机臂的定义。真正要查的是 `overlap_all_zero`"
                 "（两腿构造上不相交）与 `all_identical`（域一致 ⇒ 两臂是同一个池）。"),
    }


# ═══════════════════════════════════════════════════════════════════════
# 成本档临时登记（不改冻结生成器的字节，第 125 层）
# ═══════════════════════════════════════════════════════════════════════
def patched_tier_cost(table: dict):
    def _f(tag: str, top_n: int, rebalance_every: int) -> float:
        if tag in table:
            return table[tag]
        return bw_real_tier_cost(tag, top_n, rebalance_every)
    return _f


bw_real_tier_cost = bw._tier_cost


def arm_run(xs, wmap, judged, by_year, score_transform) -> list:
    """跑一整臂（全部判决年）并返回逐年 chunk。⚠️ 面板 GB 级 ⇒ 守卫够不着这里，
    所以判决逻辑**全部**提到模块级纯函数（与 `placebo_control.run_arms` 同源）。"""
    chunks: list = []
    for y in judged:
        run_year(xs, wmap[y]["weights"], by_year[y], TOP_N, BOTTOM_N, K,
                 daily_out=chunks, score_transform=score_transform)
    return chunks


def chunk_to_daily(chunks: list) -> dict:
    """把逐年 chunk 拼成逐日表（**按 `str(d)[:10]` 去重，后写覆盖先写**）。

    🚨 必须照抄 `factor_width_mde.series_from_chunks` 的去重语义 ——
       同一格式化的日子跨年重复时只留一个。
    """
    idx: dict[str, int] = {}
    market: dict[str, float] = {}
    net: dict[str, dict[str, float]] = {t: {} for t in bw.COST_TIER_TAGS}
    for ch in chunks:
        mkt = ch.get("market")
        nbc = ch.get("net_by_cost") or {}
        for i, d in enumerate(ch["dates"]):
            key = str(d)[:10]
            idx[key] = i
            if mkt is not None:
                market[key] = float(mkt[i])
            for tag, ser in nbc.items():
                if tag in net:
                    net[tag][key] = float(ser[i])
    keys = sorted(idx)
    return {
        "keys": keys,
        "market": np.array([market.get(k, np.nan) for k in keys], float),
        "net": {t: np.array([net[t].get(k, np.nan) for k in keys], float)
                for t in net},
    }


def account_daily(tbl: dict, tag: str) -> np.ndarray:
    """账户净值日收益 = `market` + 该档的**净超额差序列**（§18.1 的硬约束）。"""
    return tbl["market"] + tbl["net"][tag]


def annualize(r: np.ndarray) -> dict:
    """**委托** `measure_basket_nav_01.tier` —— 不重写 CAGR。

    🚨 `tier()` 返回的是**点估计 + 它自己的 MDE**，MDE 是**绝对水平**的
       （`±22.6738%/年` 那个），§19.3 明确它**不许**参与分支计算。
    """
    t = _tier("", r)
    return {"cagr": t["cagr"], "arith_ann": t["arith_ann"],
            "compounded_total": t["compounded_total"], "n_days": t["n_days"],
            "years": t["years"], "ann_vol": t["ann_vol"],
            "vol_drag": t["vol_drag"],
            "mde_own_as_annual_pct": t["mde_as_annual_pct"]}


def count_at_least(values, threshold) -> int:
    """**随机臂中 ≥ 策略值的个数**（产物里 `rank_rule` 逐字写的就是这句）。

    🚨 2026-10-16 真实事故：这一行原来是内联的
    `int(np.count_nans(random_arr >= strat_cagr))`，冒烟**跑了 724.6 秒**、
    3 个 rep 全跑完，在**写盘前最后一刻**抛
    `AttributeError: module 'numpy' has no attribute 'count_nans'` ⇒ 产物没落盘。

    两层后果，一层比一层贵：

    ① **它算的不是上面那句声称的量**。`count_nan*` 数的是 **NaN**，
       而传进去的 `values >= threshold` 是**布尔数组** ⇒ 字段名、
       `rank_rule`、CLI 打印三处说「≥ 策略值的个数」，只有代码在数 NaN。
    ② 🚨 **它炸了反而是运气好**：若那个名字碰巧存在，它返回
       **NaN 的个数 = 0** ⇒ `n_ge=0`、`rank_pct=0.0`、`rank_rule` 照常说
       「≥ 策略值的个数」⇒ **产物照常出数、判决照常发，而值是 0**
       （第 16 层假绿最坏方向）。

    ⇒ **本函数在模块级**而不是 `_run()` 内部：嵌套定义的东西守卫
    import 不到 ⇒ 判决逻辑对任何变异都无保护，而变异台一条都不会喊。

    **NaN 的处置是「不计入」，且这是有理由的**：`nan >= x` 在 numpy 里
    是 `False`（不是报错、也不是 True）⇒ 它天然被排除 ⇒ 产物里
    「保守计数」那句成立。🚨 这条性质必须显式钉住 —— 一旦哪天有人
    改成 `count_nonzero(~np.isnan(...)) & ...` 之类，它会**静默**变成
    「NaN 计入」，而 `rank_pct` 只会在分母不变的情况下少几个，
    看不出任何异常。
    """
    arr = np.asarray(values, dtype=float)
    return int(np.count_nonzero(arr >= threshold))


def _parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=JUDGEMENT_START,
                    help="★ 必须与判决窗口一致（2021-01-01），不得改成 2019")
    ap.add_argument("--end", default=WINDOW_END_PINNED)
    ap.add_argument("--reps", type=int, default=N_RANDOM_REPS,
                    help="正式 100；冒烟 3（--smoke）")
    ap.add_argument("--smoke", action="store_true",
                    help="冒烟：3 rep、独立产物路径、**判决与分支一律 null**")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--matrices", type=Path,
                    default=ROOT / "data/reports/ic_matrices.parquet")
    ap.add_argument("--out", type=Path, default=None)
    return ap.parse_args(argv)


def _run(args) -> int:
    # ★ 第 125 层：开跑时抓一次本文件的字节，盖章时比对。必须抓在**这里**、
    #   `_run` 的**第一行**（放 `main()` 是第 131 层那三颗雷，
    #   放盖章**前**更坏 ⇒ 开跑的字节变成盖章的字节，检查恒真）。
    expect_bytes = provenance.code_file_sha256(ROOT, "scripts/measure_random_arm_nav.py")
    if not str(args.start).startswith("2021"):
        print("🚨 窗口与判决窗口（2021 起）**不是同一个量**。", file=sys.stderr)
        return 2
    if not args.matrices.is_file():
        print(f"🚨 抽取表不存在：{args.matrices}", file=sys.stderr)
        return 2
    if args.smoke and args.reps != N_SMOKE_REPS:
        print(f"🚨 --smoke 的 reps 必须 = {N_SMOKE_REPS}（预注册 §19.5 的数字），"
              f"收到 {args.reps}。", file=sys.stderr)
        return 2
    if not args.smoke and args.reps > N_RANDOM_REPS:
        print(f"🚨 --reps={args.reps} > 预注册的 {N_RANDOM_REPS}。", file=sys.stderr)
        return 2

    lin = bw._rescale_check()
    if not lin["linear"]:
        print(f"🚨 成本非线性，at_cost 的缩放不再精确：{lin}", file=sys.stderr)
        return 2

    factors = list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)
    unpack(df, factors)
    wmap = weights_by_year(df, factors)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空")
        return 1

    t0 = time.time()
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        print("🚨 没有任何判决年份")
        return 1

    print(f"\n判决年 {judged}｜N={TOP_N}｜K={K}｜窗口 {args.start}~{args.end}")
    print(f"随机臂 {args.reps} 个 rep，种子 {Y5_SEED_BASE}+rep"
          f"（{Y5_SEED_BASE} … {Y5_SEED_BASE + args.reps - 1}），逐日期独立置换")
    print(f"🚨 随机臂走**同一条** run_year → evaluate_weights → build_predictions；"
          f"唯一差别是 score_transform 一个入参。")
    print("─" * 96)

    # ── 三档成本临时登记（try/finally 还原，第 125 层）────────────────
    orig_tags = bw.COST_TIER_TAGS
    orig_tier = bw._tier_cost
    orig_min_basket = verify_mod.MIN_BASKET
    table = {"zero": _extra_tier_cost("zero"),
             "bt003": STOCK_COST_DEFAULT,
             "hi006": _extra_tier_cost("hi006")}
    try:
        bw.COST_TIER_TAGS = tuple(table)
        bw._tier_cost = patched_tier_cost(dict(table))
        verify_mod.MIN_BASKET = scaled_min_basket(TOP_N)

        # ── 策略臂 ──────────────────────────────────────────────────
        strat_chunks = arm_run(xs, wmap, judged, by_year, None)
        strat = chunk_to_daily(strat_chunks)
        print(f"策略臂跑完（{time.time() - t0:.0f}s）", flush=True)

        # ── 先验检查（策略臂 + rep0 的随机臂）─────────────────────────
        dom_s = domain_probe(xs, wmap, lambda: None, "strategy")
        dom_r = domain_probe(xs, wmap,
                             lambda: make_shuffle_score(Y5_SEED_BASE), "random_rep0")
        dom_cmp = compare_domains(dom_s, dom_r)
        print(f"先验检查：{dom_cmp['n_days_compared']} 天，"
              f"域逐日相等 {dom_cmp['n_days_domains_identical']} 天，"
              f"最大差 {dom_cmp['max_gap']}｜两腿交集："
              f"策略 {dom_s['overlap_all_zero']} / 随机 {dom_r['overlap_all_zero']}"
              f"（最大 {max(dom_s['max_overlap'], dom_r['max_overlap'])}）", flush=True)
        if not dom_cmp["all_identical"] or not dom_s["overlap_all_zero"] \
                or not dom_r["overlap_all_zero"]:
            print("🚨 先验检查不通过 ⇒ **不出判决**：对照臂与观测臂不可比，"
                  "或两腿相交。", file=sys.stderr)

        # ── 随机臂 ──────────────────────────────────────────────────
        reps, per_rep, dates_ok = [], [], []
        strat_keys = list(strat["keys"])
        for rep in range(int(args.reps)):
            seed = Y5_SEED_BASE + rep
            ch = arm_run(xs, wmap, judged, by_year, make_shuffle_score(seed))
            tb = chunk_to_daily(ch)
            # 🚨 §10.3 Y5-A3「窗口逐日相同」是**独立的一条**，不是
            #   `market` 逐位相等的副产品：日期**部分重叠**时两表长度可以**恰好相同**
            #   而日期**不同** ⇒ 就是在两个不同样本上比 CAGR（第 7 种形状）。
            #   ⇒ 这里比的是**日期轴本身**（`same_date_axis` 刻意不读 `market`）。
            same_axis = same_date_axis(tb, strat, strat_keys)
            dates_ok.append(bool(same_axis))
            rec = {"rep": rep, "seed": int(seed),
                   "dates_identical_to_strategy": bool(same_axis),
                   "n_days": int(len(tb["keys"]))}
            if not same_axis:
                # ★ 2026-10-16 加的**纯观测**（不改任何判据臂）。判据逻辑**不得**
                #   内联在 `_run()` 里：内联 ⇒ 守卫 import 不到 ⇒ 等于零守卫
                #   （`code-a-guard-cannot-import-is-effectively-unguarded`）。
                #   所以真正的计算在模块级 `date_axis_diff()`，这里只负责调用。
                rec["date_axis_diff"] = date_axis_diff(tb["keys"], strat_keys)
            for tag in table:
                a = annualize(account_daily(tb, tag))
                rec[tag] = a
            if same_axis:
                mi = market_identity(tb["market"], strat["market"])
                rec["market_identical_to_strategy"] = mi["identical"]
                rec["market_max_abs_diff"] = mi["max_abs_diff"]
            else:
                # 🚨 **不写 False**：不可比 ≠ 不一样（第 7 层）。
                #    留白 + 说清理由，比冒充「比较了且不一致」诚实。
                rec["market_not_compared"] = (
                    "日期轴与策略臂不同 ⇒ 逐位比较无意义（比的是不同的日子）")
            per_rep.append(rec)
            reps.append(rec[JUDGE_TAG]["cagr"])
            print(f"  rep {rep:3d} seed={seed} 判决档 CAGR "
                  f"{rec[JUDGE_TAG]['cagr']:+.4%}/年"
                  f"{'' if same_axis else '　🚨 日期轴与策略臂不一致'}", flush=True)

        # ── 策略臂读数（三档并排）──────────────────────────────────
        strat_arms = {tag: annualize(account_daily(strat, tag)) for tag in table}
    finally:
        bw.COST_TIER_TAGS = orig_tags
        bw._tier_cost = orig_tier
        verify_mod.MIN_BASKET = orig_min_basket

    strat_cagr = strat_arms[JUDGE_TAG]["cagr"]
    #: 逐 rep 的「日期轴与策略臂逐位相同」。🚨 这是 `Y5-A3` 的**独立**判据。
    axes_all_same = bool(dates_ok) and all(dates_ok)
    # ★ 2026-10-16 加的**纯观测**（预注册 `PREREG_7Y_NET_NAV` §21.3 登记的欠账：
    #   「让脚本把『哪一天、哪几只票没 bar』落盘，而不是继续想」）。
    #: 把逐 rep 的 `date_axis_diff` 汇总成一句**可核对的形状陈述**。
    #: ⚠️ 这一块**不参与任何判决** —— `_BRANCH_ORDER` 与四条分支条件一个字未动。
    bad = [r["date_axis_diff"] for r in per_rep if r.get("date_axis_diff")]
    axis_diag = {
        "n_reps_bad": len(bad),
        "n_reps_same_set_only_reordered": sum(1 for d in bad if d["same_set"]),
        "n_reps_set_differs": sum(1 for d in bad if not d["same_set"]),
        "missing_dates_total": int(sum(d["n_missing_from_random"] for d in bad)),
        "extra_dates_total": int(sum(d["n_extra_in_random"] for d in bad)),
        "len_diff_total": int(sum(d["n_len_diff"] for d in bad)),
        "worst_rep_missing": (max(d["n_missing_from_random"] for d in bad)
                              if bad else 0),
        "verdict_relevant": False,
        "why_not_in_verdict": (
            "本块是**诊断**，不是判据。加它的时候 `_BRANCH_ORDER` 与四条分支条件"
            "一个字未动；它只回答「日期轴为什么不一致」，不回答「策略臂年化够不够 20%」。"
            "⚠️ 在成因查清之前，它连「成分下限过滤」这个机理都**不能**当成已确认 —— "
            "机理（`count < MIN_BASKET` ⇒ 整天消失）已证，**成因未找到**。"),
        "reads": (
            "`n_reps_set_differs > 0` ⇒ 有日子真的不在随机臂的日期轴上；"
            "`n_reps_same_set_only_reordered > 0` ⇒ 集合相同、只是顺序不同 "
            "⇒ 那与成分无关，是拼接次序，机理完全不同。两者混为一谈会让修法跑偏。"),
    }
    # ── 对拍外部锚（§10.3）：逐位相等才算口径一致 ---------------------
    anchor_path = ROOT / "data/reports/basket_nav_01_2026-10-16.json"
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor_cagr = float(anchor["verdict_reading"])
    bitwise = bool(strat_cagr == anchor_cagr)

    random_arr = np.array([r for r in reps if r is not None], float)
    qs = np.percentile(random_arr, QUANTILES) if len(random_arr) else np.full(len(QUANTILE_KEYS), np.nan)
    quantile_block = dict(zip(QUANTILE_KEYS, (float(v) for v in qs)))
    boots = bootstrap_p95(random_arr, BOOTSTRAP_SEED, BOOTSTRAP_DRAWS) \
        if len(random_arr) >= 2 else np.array([np.nan])

    rec = {
        "strategy_cagr": strat_cagr,
        "p5": quantile_block["p5"], "p95": quantile_block["p95"],
        "mde_resolution": mde_resolution(boots),
    }
    conds = branch_conditions(rec)
    branch = decide_branch(rec, conds)

    n_ge = count_at_least(random_arr, strat_cagr)
    rank_pct = (100.0 * n_ge / len(random_arr)) if len(random_arr) else None

    # ★ 判决句。`branch` 是四选一的**码**、`branch_text` 是那条分支的**通用解释**，
    #   而 `verdict` 是**这一跑**的判决（带本跑的数字）。
    #   ⇒ 三者不是同一个量的三份副本；把 verdict 留成 None 等于「跑完了但没说话」。
    verdict = f"{branch}｜策略臂 {strat_cagr:+.4%}/年，随机臂 p5 {quantile_block['p5']:+.4%}"
    verdict += f" ~ p95 {quantile_block['p95']:+.4%}（n={len(random_arr)}）"
    if rank_pct is not None:
        verdict += f"，策略臂 ≥ 随机臂的 {n_ge}/{len(random_arr)} 个（第 {rank_pct:.1f} 百分位）"
    suppressed = []
    if args.smoke:
        suppressed.append(f"冒烟（§19.5）：只跑 {args.reps} 个 rep，"
                          f"n=3 时 p95==max、p5==min ⇒ **数字不得进入任何判决**")
        verdict = None
    if not bitwise:
        suppressed.append(f"对拍失败：本脚本判决档 cagr={strat_cagr!r} != "
                          f"`basket_nav_01_2026-10-16.json::verdict_reading`={anchor_cagr!r}")
        verdict = None
    if not (dom_cmp["all_identical"] and dom_s["overlap_all_zero"]
            and dom_r["overlap_all_zero"]):
        suppressed.append("先验检查不通过（域不一致或两腿相交）")
        verdict = None
    if not axes_all_same:
        n_bad = sum(1 for ok in dates_ok if not ok)
        suppressed.append(f"🚨 {n_bad}/{len(dates_ok)} 个随机臂的**日期轴**与策略臂不同"
                          f"（Y5-A3 窗口逐日相同被破坏 ⇒ 分位数跨样本不可比）")
        verdict = None
    if not args.smoke and int(args.reps) < N_RANDOM_REPS:
        # ⚠️ rep 数不足压的是「预注册规模」，不是「分支可算性」⇒ branch 仍成立。
        #   但既然 verdict 是判决句，就不许它在非预注册规模下被当成正式判决读。
        verdict = append_rep_scale_note(
            verdict, suppressed,
            f"🚨 **仅 {args.reps} rep，非预注册的 {N_RANDOM_REPS}，"
            f"不得作为正式判决引用**")

    own_mde = strat_arms[JUDGE_TAG]["mde_own_as_annual_pct"]
    verdict_20 = {
        "strategy_cagr": strat_cagr,
        "abs_over_own_mde": (abs(strat_cagr) / own_mde) if own_mde else None,
        "sign_determinable": bool(own_mde and abs(strat_cagr) >= own_mde),
        "note": ("🚨 这是**绝对水平**的不确定度。它**不许**参与 `branch` 计算 —"
                 "拿绝对量的 MDE 去判相对位置是用 A 标定的仪器量 B。"
                 "本判决对「账户年化 +10.56% 本身测出来了」零信息。"),
    }

    out = {
        "kind": "Y5_net_nav_random_arm",
        "prereg": PREREG, "prereg_sections": SECTIONS,
        "generated": date.today().isoformat(),
        "smoke": bool(args.smoke),
        "reps": int(args.reps), "reps_preregistered": N_RANDOM_REPS,
        "smoke_reps_preregistered": N_SMOKE_REPS,
        "seed_base": Y5_SEED_BASE,
        "seed_rule": ("第 rep 个种子 = Y5_SEED_BASE + rep（§10.4 Y5-R2）；"
                      "逐日种子由 (rep 种子, 信号日, 盐) 派生 ⇒ 逐日期独立。"
                      "🚨 不许 hash(day)（PYTHONHASHSEED ⇒ 不可复现的阴性等于没跑）"),
        "window": {"start": args.start, "end": args.end,
                   "start_actual": str(dates[0])[:10] if dates else None,
                   "end_actual": str(dates[-1])[:10] if dates else None,
                   "n_days": int(len(strat["keys"]))},
        "config": {"top_n": TOP_N, "bottom_n": BOTTOM_N, "k_rebalance_every": K,
                   "min_basket": int(scaled_min_basket(TOP_N)),
                   "min_basket_note": "由 `scaled_min_basket(TOP_N)` 算出（不许手打）",
                   "cross_section_source": "build_cross_sections（同 placebo）"},
        "legs_relation": "同一次抽样的两端（build_predictions 同一张 ranked 做 head/tail）"
                         "⇒ 构造上不相交，**不是**两次独立抽样。口径不许改写成后者。",
        "domain_rule": DOMAIN_RULE,
        "domain_check": {
            "strategy": {k: v for k, v in dom_s.items() if k != "days"},
            "random_rep0": {k: v for k, v in dom_r.items() if k != "days"},
            "comparison": dom_cmp,
            "per_day_probe_is_in_this_run_only": (
                "逐日明细不落盘（1395 行 × 2 会让产物读不下去）；"
                "落盘的是汇总 + 判定布尔。明细要复现请重跑 --smoke。"),
        },
        "date_axis_diagnosis": axis_diag,
        "window_identity_across_arms": {
            "rule": "Y5-A3：每个随机臂的**日期轴**必须与策略臂逐位相同",
            "all_reps_dates_identical": axes_all_same,
            "n_reps_dates_identical": int(sum(1 for ok in dates_ok if ok)),
            "n_reps": len(dates_ok),
            "why_not_derived_from_market": (
                "日期**部分重叠**时两表长度可以**恰好相同**而内容不同 ⇒ "
                "『market 逐位相等』证明不了『同一天』，必须单比日期轴（第 7 种形状）"),
        },
        "market_identical_across_arms": summarize_market_identity(per_rep),
        "annualization": {
            "primary": "compounded",
            "function": "measure_basket_nav_01.tier（import，不重写）",
            "DAYS_PER_YEAR": R,
            "DAYS_PER_YEAR_source": "aplus.hurdle.DAYS_PER_YEAR",
            "arithmetic_sensitivity": "arith_ann 与 cagr 并排落盘",
            "both_calibers_disagree": bool(
                abs(strat_arms[JUDGE_TAG]["arith_ann"] - strat_cagr) > 1e-12),
            "⚠️_not_the_same_quantity": (
                f"仓内另有 {aplus_costs_days()} 与 {verify_days()} 两套年化常数，"
                f"本判决用 {R}（aplus.hurdle）"),
        },
        "quantiles": {
            **quantile_block,
            "grid": list(QUANTILES),
            "n_ge_strategy": n_ge,
            "n_random_arms": int(len(random_arr)),
            "rank_of_strategy_percent": rank_pct,
            "rank_rule": "n_ge = 随机臂中 ≥ 策略值的个数（保守计数）",
        },
        "p95_bootstrap_ci95": {
            "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "half_width": float(mde_resolution(boots)),
        },
        "mde": {
            "mde_resolution": float(rec["mde_resolution"]),
            "what_it_is": "**p95 这个分位数本身**的 bootstrap 95% 区间半宽",
            "what_it_is_NOT": "🚨 **不是**「策略值离上尾多远」（那个数在 dist_to_p95）",
            "band_p95_minus_p5": float(band(rec)),
            "dist_to_p95": float(strat_cagr - rec["p95"]),
            "strategy_own_mde": own_mde,
            "strategy_own_mde_does_not_enter_branch": True,
        },
        "branch_order": list(_BRANCH_ORDER),
        "branch_conditions": conds,
        "branch": (branch if verdict_is_adopted(branch, verdict) else None),
        # 🚨 `branch` 为 null 时**不许**把某条分支的解释挂在它下面 ——
        #   那是第 179 层的形态：键名说「没出判决」、内容说「E-上 意味着…」。
        #   ⇒ 解释随 `branch` 一起置 null，而**算出来的**那条码另存一个带
        #     「未被采用」限定的键，两句话单独读都成立、合起来不矛盾。
        "branch_text": (BRANCH_TEXT[branch]
                        if verdict_is_adopted(branch, verdict) else None),
        "branch_computed_but_not_adopted": (
            None if verdict_is_adopted(branch, verdict)
            else {"code": branch, "text": BRANCH_TEXT[branch],
                  "why_not_adopted": "判决被压住 ⇒ 该码**未被采用**，"
                                     "见 verdict_suppressed_reasons"}),
        "strategy_own_mde_verdict": verdict_20,
        "arms": {"strategy": strat_arms, "random": per_rep},
        "cost": {
            "tiers": {tag: float(table[tag]) for tag in table},
            "judge_tag": JUDGE_TAG,
            "realized_cost_is_used_for_judgment": True,
            "note": ("三档由 `annual_friction` / STOCK_COST_DEFAULT **一次性**给出，"
                     "全程不许叠加第二次（第 82 层）。两臂逐档相同。"),
        },
        "external_anchor": {
            "path": "data/reports/basket_nav_01_2026-10-16.json",
            "field": "verdict_reading",
            "expected": anchor_cagr,
            "recomputed_here": strat_cagr,
            "bitwise_equal": bitwise,
            "rule": "不相等 ⇒ verdict=None，不出结论",
        },
        "verdict": verdict,
        "verdict_suppressed_reasons": suppressed,
        "what_this_cannot_prove": list(WHAT_THIS_CANNOT_PROVE),
    }

    out_path = args.out or (ROOT / "data/reports/random_arm_nav_smoke.json"
                            if args.smoke else ROOT / "data/reports/random_arm_nav.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float),
                        encoding="utf-8")
    provenance.stamp(out_path, "scripts/measure_random_arm_nav.py", root=ROOT,
                     extra={"branch": out["branch"], "verdict": verdict,
                            "reps": int(args.reps), "smoke": bool(args.smoke),
                            "test_window": out["window"],
                            "strategy_cagr": strat_cagr,
                            "anchor_bitwise_equal": bitwise,
                            "expect_bytes_checked": True},
                     expect_bytes=expect_bytes)

    print(f"\n随机臂判决档 CAGR：min {quantile_block['min']:+.2%}｜"
          f"p5 {quantile_block['p5']:+.2%}｜中位 {quantile_block['median']:+.2%}｜"
          f"p95 {quantile_block['p95']:+.2%}｜max {quantile_block['max']:+.2%}")
    if rank_pct is None:
        print(f"策略臂 {strat_cagr:+.4%}/年（🚨 随机臂一个数都没有 ⇒ 排不出名次）")
    else:
        print(f"策略臂 {strat_cagr:+.4%}/年 ≥ 随机臂的 {n_ge}/{len(random_arr)} 个"
              f"（第 {rank_pct:.1f} 百分位）")
    # ⚠️ 判决被压住时**先**打那句，**不打**分支码 ⇒ 终端与产物说同一件事
    #   （否则「读到的是 E-上」与 `branch: null` 逐位同形地打脸）。
    if verdict is None:
        print(f"🚨 判决被压住（**未采用**任何分支码）：{'；'.join(suppressed)}")
        print(f"　（算出来的码是 {branch}，但它**不是本产物的判决**）")
    else:
        print(f"带宽 {band(rec):+.4%} vs 分辨率 {rec['mde_resolution']:.4%}"
              f" ⇒ {branch}｜{BRANCH_TEXT[branch]}")
    print(f"\n→ {out_path.relative_to(ROOT)}｜总耗时 {time.time() - t0:.0f}s")
    return 0


def aplus_costs_days() -> int:
    """`aplus/costs.py` 的年化常数 —— 只为在产物里说清「另有两套不是同一个量」。"""
    from aplus import costs
    for nm in ("DAYS_PER_YEAR", "TRADING_DAYS", "DAYS"):
        if hasattr(costs, nm):
            return int(getattr(costs, nm))
    return -1


def verify_days() -> int:
    """`verify.py` 的年化常数（同上）。"""
    import aplus.verify as v
    for nm in ("DAYS_PER_YEAR", "TRADING_DAYS", "DAYS"):
        if hasattr(v, nm):
            return int(getattr(v, nm))
    return -1


def main(argv=None) -> int:
    args = _parse_args(argv)
    with begin_run("measure_random_arm_nav", reps=args.reps, smoke=args.smoke,
                   start=args.start):
        return _run(args)


if __name__ == "__main__":
    sys.exit(main())
