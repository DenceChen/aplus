"""市场择时的**可交付上界**（oracle ceiling）—— D2 那 94% beta 的天花板。

## 为什么算这个

实测（`data/reports/basket_nav_01_2026-10-16.json::gap_to_target_by_benchmark`）：
账户年化 **+10.5563%**，其中市场 **9.9471%**、选股超额 **0.5541%**
（比值口径 `((1+c_bk)/(1+c_mkt))^(1/years) − 1`，取自
`excess_contribution_ratio::bt003.annual_pct`）⇒ **约 94.2% 是市场 beta**。
⚠️ **这三个数不可相减**：复利年化相减得 +0.61pp，那是错的
（分数减分数得到的是差，不是比值的年化）—— 产物 `excess_contribution_note` 逐字禁止这种加法。
⚠️ 原稿这里写的是 `0.5543%` 且把三者写成可加的等式 ⇒ 与产物差 0.0002pp
且**教读者一个被明文禁止的口径**；那是 2026-10-20 一次对抗核查抓出来的。
而选股超额那 0.55pp 已被三条独立实验一致判死
（`PLACEBO_NULL` / `NO_LEVER` / `NOT_PASSED_POWERLESS`），
且榜宽实验证明「到 20% 净所需本金」五档全 `null`（毛超额 ≤ 7.57% < 目标）。

⇒ **未被攻的是那 94%**：市场暴露本身。
石川 `cn_factor_lines.txt:15834-15836` 把「择时」定义为
「在时序上判断市场整体涨跌从而**动态控制仓位**」——
与「因子择时」是**两个对象**，而 7.5.6 的判决标题逐字是「**因子择时**很难」，
正文把市场择时**并置为另一个对象**（「和市场择时（market timing）一样」）。

## 这个脚本回答什么、不回答什么

**回答**：「如果有人能**完美预知**市场涨跌方向，零杠杆账户最多能到多少年化？」
⇒ 这是**上界**。上界若 < 20%，市场择时这条**永久封口**，不必投 GB 级算力。

**不回答**：「市场择时能不能赚钱？」—— 那需要真实信号 + 预注册判据。
🚨 产物里逐字标注：本文件**不是判决**，是**上界**；
oracle 用的是**块内未来信息**（未来函数），**没有任何实现者能拿到**。

## 为什么不需要预注册

预注册管的是「**够不够线**」这类判决门槛。一个自带「**上限**」标签的量
不是判决门槛 —— 无论算出来是 5% 还是 80%，它都不构成「跑过数据后改判据」。

## 论证结构：两个方向都取对 D2 不利的一侧

① 信号取 **oracle**（不可能实现的完美）⇒ **最松**的一侧；
② 成本取**满换手单日的实测扣减**（换 10 只股票的费率），
   而 0/1 仓位切换**不换股**、真实成本更低 ⇒ **偏贵**的一侧。

⇒ 两侧同时对 D2 不利 ⇒ **「够不到 20%」这一支**若成立，它对**任何**真实信号
与**任何**真实成本都成立。这是双向钉死。
🚨 而**「够得到」那一支得不到同样的保证** —— 取 max 的那一处（下一段）
是对市场择时**有利**的 ⇒ 上界够得到 20% 时，**本文件不给封口**。
两个方向各自的实例化判决在
`ceiling.direction_statements.closing_logic`（**按字段算出来的，不是写死的散文**）。

⚠️ 另有一处**故意对市场择时有利**：「取全部 (K × 仓位 × 摩擦 × 计费) 组合里
年化最高的一个」。这是**赢家的诅咒**（第 129 层：两个量争同一个 max），
而取 max 只会让上界**更宽松**。
⇒ 这个方向**只在一个方向上是安全的**：当上界仍够不到 20% 时，
「更宽松也没用」⇒ 封口单向成立；当上界够得到 20% 时，
宽松的是**结论所依赖的那个数**，且**没有任何一处在为它做收紧** ⇒ 不封口。
⚠️ 这两句**曾经在这里写死成「而宽松的上界仍然够不到 20% ⇒ 封口结论单向成立」**
—— 那是一句**与同文件 `best_annual_pct` 逐字相反的散文**（差 9.2 倍），
而散文不在任何 provenance 机制覆盖范围内（第 142 层）⇒ 没有任何东西会红。
⇒ 现在实例化的那句话由 `direction_statements.winner_curse_direction` 从字段算出，
两个分支都有句子，字段变则句子跟着变。

## 口径纪律

- 只**读字段**：`market_return` 逐日市场收益（1395 日），
  出自 `wf_weights_backtest_k21.daily.parquet`。
  🚨 **它是 5460 池随机抽 150 只**（`MARKET_SAMPLE=150` + 种子 42）的等权日收益，
  **不是全池**。全池等权基准的年化是 `market_nav_caliber.json::equal_weight
  .geometric_annual_pct`（+9.9471%），两者**不可并列引用**（第 7 层）。
- 年化常数 `244` 从 `aplus.hurdle::DAYS_PER_YEAR` import（CLAUDE.md：
  仓里另有 250 / 252 两套，**不是同一个量**）。
- 摩擦档从产物自身反推，不手打。

用法：
    .venv/bin/python scripts/market_timing_ceiling.py
"""
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.hurdle import DAYS_PER_YEAR  # noqa: E402

# 第 163 层：**复用仓里已有的量具，不手搓**。`required_excess` 的 docstring
# 逐字记着它自己在第 181 层两次犯错的完整经过，而那两条守卫
# （充分性锚 / `yrs` 不变性 / 正控）钉在它身上 —— 手搓一份副本
# 就会得到一份**没有守卫的同一个公式**，而两份副本之间不会有任何东西会红。
from scripts.measure_basket_nav_01 import required_excess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = "scripts/market_timing_ceiling.py"
UPSTREAM = ROOT / "data" / "reports" / "wf_weights_backtest_k21.daily.parquet"
OUT = ROOT / "data" / "reports" / "market_timing_ceiling.json"

# 用户的常设交付目标（年化 20%）。**不是门槛，是被比较的那一头**。
TARGET_ANNUAL = 0.20

K_GRID = (1, 5, 10, 21, 42, 63)

# 仓位档：(空仓时的仓位, 满仓时的仓位)。
# 0/1 是「空仓/满仓」；0.5/1 是「半仓/满仓」—— 更接近可交付形态（不清仓）。
EXPOSURE_ARMS = {"zero_or_one": (0.0, 1.0), "half_or_one": (0.5, 1.0)}


def _sha256_of(path: Path) -> str:
    """输入的**字节**指纹。⚠️ 不用 mtime：mtime 是代理不是证据
    （本仓变异台「改源码 → 跑测试 → 写回」会把 mtime 顶到现在而内容逐位相同）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def date_axis(df: pd.DataFrame) -> pd.Series:
    """取日期轴，**显式区分三种形状**（实测本仓那份 parquet 是第三种）。

    第 18 层：单点依赖要问「它靠哪个不变量活着」。这里的三种是：
      A  DatetimeIndex  —— pandas 读回来就带 dtype
      B  普通 Index/列，**值是 ISO 日期字符串** ← 本仓现状（名字 `date`、dtype object）
      C  普通 RangeIndex —— **根本没有日期**，此时任何「窗口」都是编的

    ⚠️ B **不许转成 datetime 再用**：转了就分不清「文件里存的是日期」
    与「我手上一串看起来像日期的字符串」。先按字符串校验，再报告。
    """
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="date")
    if isinstance(df.index, pd.RangeIndex) or df.index.name != "date":
        raise KeyError(
            "找不到日期轴：索引既不是 DatetimeIndex、也不叫 'date'。"
            f" index.name={df.index.name!r} dtype={df.index.dtype}")
    raw = pd.Series(df.index.astype(str), name="date")
    bad = [v for v in raw.head(200) if len(v) != 10 or v[4] != "-" or v[7] != "-"]
    if bad:
        raise ValueError(f"索引叫 'date' 但内容不像 ISO 日期，例：{bad[:3]!r}")
    return raw


def block_returns(daily: np.ndarray, k: int) -> np.ndarray:
    """把逐日收益聚成 K 日块的**块累计收益**（复利）。

    尾部不足 K 日的残块**丢弃**（不进块数、不进收益）——
    它的观测单位与整块不同，混进来就是第 7 种形状。
    """
    n_full = len(daily) // k
    if n_full == 0:
        return np.array([], dtype=float)
    return np.prod(1.0 + daily[: n_full * k].reshape(n_full, k), axis=1) - 1.0


def oracle_nav(block_ret: np.ndarray, lo: float, hi: float,
               cost_per_switch: float, charge_every_block: bool) -> dict:
    """oracle 的净值路径，逐块推进。

    ⚠️ **oracle 的决策用块内全部信息**（块末才知道该块涨跌）⇒ **未来函数**。
    这正是「上界」的含义，**不是可实现的策略**。

    计费时机两档：
      - `charge_on_switch_only`：只在仓位档位改变时扣一次（0/1 切换的真实会计）；
      - `charge_every_block`：每块都扣（把「持有」也收费，刻意更保守）。
    首块不扣（`prev is None`）—— 建仓前没有持仓可卖。
    """
    if len(block_ret) == 0:
        return {"nav": None, "n_blocks": 0}
    up = block_ret > 0.0
    expo = np.where(up, hi, lo)
    nav = 1.0
    prev = None
    switches = 0
    charged = 0
    for e, u, r in zip(expo, up, block_ret):
        changed = prev is not None and (u != prev)
        if charge_every_block or changed:
            nav *= (1.0 - cost_per_switch)
            charged += 1
        if changed:
            switches += 1
        nav *= (1.0 + e * r)
        prev = u
    return {"nav": nav, "n_blocks": int(len(block_ret)),
            "n_switches": switches, "n_charges": charged,
            "fully_in_frac": float(up.mean())}


def annualize(total_return: float, n_blocks: int, k: int) -> float:
    """**复利**年化。仓内另有算术口径（`mean × 244`），那是**另一个量**（第 7 层）。
    `years = n_blocks × k / 244`，244 从 `aplus.hurdle` import，不手打。"""
    years = n_blocks * k / DAYS_PER_YEAR
    if years <= 0 or (1.0 + total_return) <= 0:
        return float("nan")
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def cost_tiers(df: pd.DataFrame) -> dict:
    """摩擦档**从产物自身反推**（第 109 层：不用公式重算既有产物列，只读字段）。

    `gross_excess − net_excess_*` 的逐日差 = 当日被扣掉的成本；
    其**最大值** = 一次**满换手**的单日成本（K 日调仓日换的是整篮）。
    这正是 0/1 仓位切换（卖光 / 买满）要付的钱 ⇒ 本脚本取的就是它。
    """
    out = {}
    for tier, col in (("bt003", "net_excess_bt003"), ("real", "net_excess_real")):
        d = (df["gross_excess"] - df[col]).to_numpy(dtype=float)
        out[tier] = {
            "full_turnover_single_day_cost": float(np.abs(d).max()),
            "median_day_cost": float(np.median(np.abs(d))),
            "frac_days_above_90pct_of_max": float((np.abs(d) >= 0.9 * np.abs(d).max()).mean()),
            "n_days_at_max": int((np.abs(d) >= np.abs(d).max() - 1e-15).sum()),
        }
    out["caliber"] = (
        "「满换手单日成本」= 逐日 |gross−net| 的最大值，即调仓日换掉整篮要付的钱。"
        "0/1 仓位切换**不换股**（只卖不买回股票代码），真实成本**低于**此值 "
        "⇒ 本脚本取的是**偏贵**的一侧，这是刻意的不利方向。")
    return out


def measure(df: pd.DataFrame) -> dict:
    dates = date_axis(df)
    market = df["market_return"].to_numpy(dtype=float)
    tiers = cost_tiers(df)
    out = {
        "upstream": {
            "path": str(UPSTREAM.relative_to(ROOT)),
            "n_days": int(len(df)),
            "date_first": str(dates.min()),
            "date_last": str(dates.max()),
            "date_index_shape": ("字符串 Index（名字 date，dtype object）"
                                 if not isinstance(df.index, pd.DatetimeIndex)
                                 else "DatetimeIndex"),
            "date_axis_caveat": (
                "⚠️ 末位日 2026-10-09 ≠ 判决窗口末端 2026-10-12 —— "
                "这份逐日表是**涨腿幸存日**（MIN_BASKET 过滤后）的日期轴，"
                "不是交易日历。因此这里的「天数」是**幸存日数**不是交易日数，"
                "而 block_returns 按位置切块 ⇒ 块里是幸存日的连续段。"
                "本文件只用它算上界（逐日收益序列本身），不用它数交易日。"),
            "market_return_mean_daily": float(market.mean()),
            "market_return_caliber": (
                "5460 池随机抽 150 只（MARKET_SAMPLE=150 + 种子 42）的等权日收益，"
                "**不是全池**。全池等权基准年化见 market_nav_caliber.json::"
                "equal_weight.geometric_annual_pct，两者不可并列引用。"),
        },
        "cost_tiers": tiers,
        "annualization": {
            "days_per_year": DAYS_PER_YEAR,
            "source": "aplus.hurdle.DAYS_PER_YEAR",
            "formula": "复利 (1+总收益)^(1/years)−1，years = n_blocks × k / 244",
            "note": "仓内另有算术口径 mean×244（与 250/252 两套）；那是另一个量，不可混用",
        },
        "by_k": {},
    }
    for k in K_GRID:
        br = block_returns(market, k)
        n = len(br)
        row = {
            "n_blocks": n,
            "years": n * k / DAYS_PER_YEAR,
            "rounds_per_year": DAYS_PER_YEAR / k,
            "block_up_frac": float((br > 0).mean()) if n else None,
            "arms": {},
        }
        # 对照臂：完全不减仓（buy & hold）。**证明 oracle 确实 > 基准**，
        # 否则「择时无用」这句话没有对照。建仓成本未扣（一次性，可忽略）。
        row["always_full_annual_pct"] = (
            annualize(float(np.prod(1.0 + br)) - 1.0, n, k) if n else None)
        for arm_name, (lo, hi) in EXPOSURE_ARMS.items():
            for tier in ("bt003", "real"):
                cost = tiers[tier]["full_turnover_single_day_cost"]
                for charge, cname in ((False, "charge_on_switch_only"),
                                      (True, "charge_every_block")):
                    r = oracle_nav(br, lo, hi, cost, charge)
                    if r["nav"] is None:
                        continue
                    tr = r["nav"] - 1.0
                    ann = annualize(tr, r["n_blocks"], k)
                    row["arms"][f"{arm_name}|{tier}|{cname}"] = {
                        "lo_exposure": lo, "hi_exposure": hi,
                        "cost_per_switch": cost,
                        "charge_every_block": charge,
                        "total_return": tr,
                        "annual_pct": ann,
                        "n_switches": r["n_switches"],
                        "fully_in_frac": r["fully_in_frac"],
                        "gap_to_target_pp": (ann - TARGET_ANNUAL) * 100.0,
                        "reaches_target": bool(ann >= TARGET_ANNUAL),
                        "excess_over_always_full_pp": (
                            (ann - row["always_full_annual_pct"]) * 100.0
                            if row["always_full_annual_pct"] is not None else None),
                    }
        out["by_k"][str(k)] = row
    return out


def capture_requirement(target: float, oracle_annual: float, hold_annual: float,
                        yrs: float) -> dict:
    """真实信号要拿到 oracle 的**百分之几**，才能把「一直满仓」推到 `target`。

    🚨 **两半各走各的路，所以落两个口径、不落一个数**（第 7 层 / 第 183 层）：
    仓位切换的收益是**乘性**的（复利），不是加性的 ⇒
    「捕获了 oracle 增量的百分之几」**取决于在哪个空间里数它**。

    - `log`（**判决臂**）：捕获在**对数收益**上成比例。
      这是「信号只带一部分信息」的自然模型 —— 每次调仓对总对数收益的
      贡献按比例缩减。复利本身就是对数可加的，所以这一支量纲是对的。
    - `pp`（**敏感性臂，不是判决臂**）：捕获在**年化百分点差额**上成比例。
      记录它是因为它是别人最可能心算出来的那一支；两臂差着量级，
      而**它们各自都对**，所以交叉核对必须带 role 标签（第 183 层）。

    两臂都调**同一个** `required_excess()`（仓里唯一的换算尺子），
    本函数不另写换算公式（第 163 层）；`yrs` 只被它校验、不进公式（第 181 层）。

    ⚠️ **本函数不是判决量**：它回答「需要捕获多少」，
    「实际捕获到了多少」一个字都没测 —— 那需要真实信号 + 预注册判据。
    """
    req = required_excess(target, hold_annual, yrs)
    orc = required_excess(oracle_annual, hold_annual, yrs)
    return {
        "caliber": (
            "判定的两半：req = (1+目标)/(1+一直满仓) − 1，"
            "orc = (1+上界)/(1+一直满仓) − 1，"
            "**两半都调 measure_basket_nav_01.required_excess（仓里唯一尺子）**；"
            "capture_log = ln(1+req)/ln(1+orc)，capture_pp = (目标−满仓)/(上界−满仓)。"
            "⚠️ 两臂量纲不同（对数可加 vs 年化差额），不可互相代替，引用必须带 role。"
        ),
        "required_gain_excess": req,
        "oracle_gain_excess": orc,
        "log": {
            "role": "判决臂",
            "space": "对数收益（复利可加）",
            "fraction": math.log1p(req) / math.log1p(orc),
        },
        "pp": {
            "role": "敏感性臂（非判决臂）",
            "space": "年化百分点差额（加性）",
            "fraction": (target - hold_annual) / (oracle_annual - hold_annual),
        },
    }


def direction_statements(ceiling: dict, capture: dict) -> dict:
    """把**判读**做成 `ceiling` 的**计算结果**，而不是写死的散文（第 142 层）。

    🚨 这里修的 P0：`what_this_is_not[1]` 曾逐字写「即便完美预知方向…也够不到 20%」，
    而**同一文件**的 `best_annual_pct = 1.845…`、`best_reaches_target = true`
    ⇒ **散文断言了字段的反面**，差 **9.2 倍**。
    两边都是真值且**永不冲突**：散文不在任何 provenance 机制覆盖范围内（形状 22），
    没有任何东西会因为它与字段矛盾而变红。

    ⇒ 修法不是给散文配守卫（守卫要读散文，而散文正是被怀疑的那个），
    是**让散文从一个字段算出来** ⇒ 字段变、句子跟着变，结构上不可能脱钩。
    每句话都按 `best_reaches_target` 分支，两个分支都必须有句子
    （写死的单边句就是本缺陷的原始形态）。
    """
    reaches = bool(ceiling["best_reaches_target"])
    pct = ceiling["best_annual_pct"] * 100.0
    return {
        "reaches_target": reaches,
        "closing_logic": (
            f"上界够得到 20%（{pct:.2f}%/年）⇒ 市场择时**不能**封口；"
            "下一步是真实信号 + 预注册判据。"
            if reaches else
            f"上界够不到 20%（{pct:.2f}%/年）⇒ 在零杠杆 + 5 万本金 + 现有摩擦量级下封口。"
        ),
        "winner_curse_direction": (
            "⚠️ K 是跑完 6 档后取 max，即**赢家的诅咒**（第 129 层）。"
            "取 max 只会让上界**更宽松**，而宽松的上界够得到 20% "
            "⇒ 本文件**不封口**市场择时（方向安全但结论不成立）。"
            if reaches else
            "⚠️ K 是跑完 6 档后取 max，即**赢家的诅咒**（第 129 层）。"
            "取 max 只会让上界**更宽松**，而它仍够不到 20% ⇒ 封口结论单向成立。"
        ),
        "not_evidence_of_inefficacy": (
            "这不是「市场择时无效」的证据。它只说明：即便完美预知方向，"
            f"在零杠杆 + 5 万本金 + 现有摩擦量级下年化可达 {pct:.2f}%，"
            f"{'够得到' if reaches else '够不到'} 20%。"
        ),
        "not_measured": (
            "真实信号需要捕获 oracle 的 "
            f"{capture['log']['fraction']:.1%}（对数口径，判决臂）才能达标；"
            "本文件一个字都没测**捕获到了多少** —— "
            "那需要真实信号 + 预注册判据，是下一步，不能由本文件代答。"
        ),
    }


def summarize(res: dict) -> dict:
    best_k = best_arm = None
    best_ann = -1e18
    for k, row in res["by_k"].items():
        for arm, a in row["arms"].items():
            if a["annual_pct"] > best_ann:
                best_ann, best_k, best_arm = a["annual_pct"], k, arm
    always_full = {k: row["always_full_annual_pct"] for k, row in res["by_k"].items()}
    best_hold = max(v for v in always_full.values() if v is not None)

    # ⚠️ `yrs` 只被 `required_excess` **校验**、不进公式（第 181 层实测：
    #   带幂那次在 y=5.717 上差 −8.36pp）。日期轴是**涨腿幸存日**的轴而不是
    #   交易日历（见 `upstream.date_axis_caveat`），而本函数的结果与它无关 ——
    #   传它只为让那道校验真的是真的，不是为了用它的值。
    yrs = res["upstream"]["n_days"] / float(DAYS_PER_YEAR)
    capture = capture_requirement(TARGET_ANNUAL, best_ann, best_hold, yrs)

    ceiling = {
        "best_k": best_k,
        "best_arm": best_arm,
        "best_annual_pct": best_ann,
        "best_gap_to_target_pp": (best_ann - TARGET_ANNUAL) * 100.0,
        "best_excess_over_hold_pp": (best_ann - best_hold) * 100.0,
        "best_reaches_target": bool(best_ann >= TARGET_ANNUAL),
        "best_hold_annual_pct": best_hold,
        "best_hold_reaches_target": bool(best_hold >= TARGET_ANNUAL),
        "selection": "全部 (K × 仓位档 × 摩擦档 × 计费时机) 组合中年化最高的一个",
        "capture_requirement": capture,
    }
    # 判读由**字段算出来**，不是写死的散文（第 142 层）⇒ 字段变、句子跟着变。
    stmt = direction_statements(ceiling, capture)
    ceiling["direction_statements"] = stmt
    ceiling["selection_warning"] = (
        "这是**上界**里最高的一个，而上界用了未来函数 ⇒ 真实可达值严格低于它。"
        + stmt["winner_curse_direction"]
    )
    return ceiling


def main():
    # 🚨 第 125 层：抓在**开跑之前**，比对在**盖章那一刻**。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    t0 = time.time()
    df = pd.read_parquet(UPSTREAM)
    res = measure(df)
    res["ceiling"] = summarize(res)
    res["target_annual"] = TARGET_ANNUAL
    res["verdict_about_market_timing"] = (
        "CLOSED_BELOW_TARGET" if not res["ceiling"]["best_reaches_target"]
        else "ABOVE_TARGET_NEEDS_PREREG")
    stmt = res["ceiling"]["direction_statements"]
    res["what_this_is_not"] = [
        "这不是判决。上界用块内未来信息（oracle），没有任何实现者能拿到。",
        stmt["not_evidence_of_inefficacy"],
        stmt["not_measured"],
        "market_return 是 5460 池随机抽 150 只的等权日收益，不是全池。",
        "本文件没预注册，因为上界是无阈值的量、自带「上限」标签；"
        "预注册管的是「够不够线」那类判决门槛（CLAUDE.md）。",
    ]
    res["elapsed_s"] = round(time.time() - t0, 2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(OUT, GENERATOR, root=ROOT, data_root=str(ROOT / "data"),
                     expect_bytes=expect_bytes,
                     extra={"source_parquet": str(UPSTREAM.relative_to(ROOT)),
                            "source_parquet_sha256": _sha256_of(UPSTREAM)})

    c = res["ceiling"]
    print("=" * 74)
    print("市场择时的可交付上界（oracle，未来函数）—— 这是上界，不是判决")
    print("=" * 74)
    print(f"目标年化        {TARGET_ANNUAL:.2%}")
    print(f"上界最高一档    K={c['best_k']}  {c['best_arm']}")
    print(f"  年化          {c['best_annual_pct']:.2%}"
          f"   缺口 {c['best_gap_to_target_pp']:+.2f}pp")
    print(f"  比「一直满仓」多 {c['best_excess_over_hold_pp']:+.2f}pp"
          f"（对照臂 {c['best_hold_annual_pct']:.2%}）")
    print(f"  够不够 20%    {'够得到' if c['best_reaches_target'] else '够不到'}")
    print(f"判决            {res['verdict_about_market_timing']}")
    # 判读**由字段算出**（第 142 层）⇒ CLI 与产物共用同一份句子，
    # 打印出来的判读不可能与落盘字段脱钩。
    print(f"判读            {res['ceiling']['direction_statements']['closing_logic']}")
    cap = res["ceiling"]["capture_requirement"]
    print(f"要达标需捕获 oracle 的 "
          f"{cap['log']['fraction']:.1%}（{cap['log']['role']}·{cap['log']['space']}）"
          f" / {cap['pp']['fraction']:.1%}（{cap['pp']['role']}·{cap['pp']['space']}）")
    print("-" * 74)
    for k in K_GRID:
        row = res["by_k"][str(k)]
        print(f"K={k:<3} 块数={row['n_blocks']:<5} 年数={row['years']:<6.2f} "
              f"满仓对照 {row['always_full_annual_pct']:>8.2%}   最好的三档：")
        for key, a in sorted(row["arms"].items(),
                             key=lambda kv: -kv[1]["annual_pct"])[:3]:
            print(f"      {key:<40} {a['annual_pct']:>8.2%}"
                  f"  切换{a['n_switches']:>4}"
                  f"  满仓{a['fully_in_frac']:>5.1%}"
                  f"  缺 {a['gap_to_target_pp']:+.2f}pp")
    print("-" * 74)
    print(f"耗时 {res['elapsed_s']}s → {OUT}")


if __name__ == "__main__":
    main()
