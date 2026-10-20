"""把**当前实盘那一支**冻结进**第五批**前瞻台账（2026-10-20）。

## 这一批要解决什么

前四批（2026-10-12 ×2、2026-10-14、2026-10-20）共 19 组候选，
**没有一组是「当前实盘那一支」**。

实盘跑的权重来自 `aplus/evolve.py::load_weights` 的**回落分支**：
`data/reports/weights.json` **不存在** ⇒ 走 `aplus/evolve.py:275` 的 fallback
（`:281 "source": "fallback"`、`:288 "caveat_kind": "unraced"`）。
所以实盘那一支 = `aplus/factor.py:61 DEFAULT_WEIGHTS`
+ `aplus/evolve.py:79 LIVE_REBALANCE_EVERY`
+ `buffer_multiple = None`（`aplus/evolve.py:90`：不写 `buffer_multiple`
就是**没有缓冲**）。

台账里最接近的两条**都不是它**：

- 第 1 批 ① `{mom20:+1.0, rev5:+0.3, vol20:+0.5, vsur:+0.5}` K=1
  —— **2026-10-14 符号翻转之前**的旧配置，且 K=1；
- 第 2 批 ④ / 第 3 批 ② —— **8 因子**（多 `turn20/turnratio/ivol/rsi`），
  那是判决路径的 IC 定权，不是实盘出单的那一组。

程序核对（`aplus.evolve.candidate_key` 对前四批 19 组去重后的 18 个唯一键）
与本批三臂的三个键**无一相同**。

⇒ **前瞻底稿（`data/reports/verification.parquet`，实测 160 行 /
8 个 `pred_date` / 6 个 `entry_date`）测的候选族里，
没有一个是「当前实盘那一支」。**

⇒ 而前瞻是 D1 唯一还能解锁判决的路（D1 闸门现在判 `NOT_PASSED_POWERLESS`，
判决臂 up 的检出力下限 `null_abs_rho_q95_power_floor` = 0.2647，
这份样本连这个量级的关联都分不出来）⇒ **它此刻测错了对象。**

⇒ **这一批是「检验」，不是「采纳」。** 冻结实盘配置不等于相信实盘配置；
它的作用是让**从未被看过的交易日**去回答
「实盘真正在跑的那一套，前瞻到底有没有证据」。

## 三组候选，以及每组为什么这么配

| # | 名字 | weights | K | buffer | 它回答哪个问题 |
|---|---|---|---|---|---|
| ① | 实盘那一支 | `DEFAULT_WEIGHTS` | `LIVE_REBALANCE_EVERY` | `None` | **实盘这套信号，前瞻有没有证据** |
| ② | 实盘配置 + 更慢换仓 | `DEFAULT_WEIGHTS` | 63 | `None` | **成本臂**：摩擦砍到约 1/3 之后还剩多少 |
| ③ | 实盘配置 + 缓冲对照 | `DEFAULT_WEIGHTS` | `LIVE_REBALANCE_EVERY` | `BUFFER_MULTIPLE` | **单参数对照**：缓冲算法在实盘信号上是否有害 |

### ② 的理由是**成本算术**，不是拟合

年调仓次数 = `aplus/hurdle.py::rounds_per_year(k)` = 244/k：
K=21 → 11.6190 次/年；K=63 → 3.8730 次/年 ⇒ **恰好 3.0000×**。
单边每轮成本 `aplus/hurdle.py::COST_REAL_50K = 0.0055`
⇒ 名义年摩擦 **6.3905% → 2.1302%**。

🚨 **前提必须写明**：这个「1/3」只在**每次调仓的换手幅度不变**时成立；
K 越长 ⇒ 持仓被持有越久 ⇒ 换手幅度**可能不同**。
⇒ 3.0000× 是**算术比值，不是实测**。

🚨 **不许声称 ② 超额更好**：K 越大 ⇒ 独立观测越少 ⇒ 判决越慢
（63 vs 189 个交易日）。**② 是成本臂，不是「更好的策略臂」。**
仓内实测过的 K 扫描是**化石**（`k_sweep_summary.json` 的 `freshness()` 判 stale）
⇒ 「K=63 更好/更差」今天**没有出处**，本批从零开始验。

### ③ 的理由：单参数对照

① 与 ③ **只差一个参数**（`buffer_multiple`：`None` → `BUFFER_MULTIPLE`），
权重与 K 完全相同 ⇒ 受控对照。
缓冲把换手压低（`aplus/evolve.py:91`：16.91% → 0.57%），
**方向是让净更好看**；而 `aplus/evolve.py:88-92` 明确禁止「默认开启缓冲」
（会让所有历史结论在无人察觉的情况下被改写，且改写方向一致）
⇒ 必须在**实盘这组信号**上单独问一次。

⚠️ **命名订正**：③ 的参数是 `buffer_multiple = 3.0` —— **3.0 是有缓冲**
（`None` 才是无缓冲）⇒ **① 才是实盘那一支（无缓冲）**，
③ 是加了缓冲的对照臂。订正留在原地，否则后人去找一条「不存在的无缓冲臂」。

## 参数来源铁律：候选清单里**没有一个手打字面量**

`DEFAULT_WEIGHTS` / `LIVE_REBALANCE_EVERY` / `BUFFER_MULTIPLE` 全部 import。

**为什么**：手打的那份是**化石** —— 2026-10-14 符号翻转之后
`aplus/factor.py` 的 `mom20/vol20/vsur` 三个符号全变了，
而台账第 1 批 ① 至今仍是**翻转前**的 `{+1.0, +0.3, +0.5, +0.5}`。
**手打的常量不会跟着源码变，import 的会。**

⚠️ **参数将来变了要开新批次，不许改本批**（注册即冻结）。
本批冻结的是**今天实盘跑的那一组**。

## 门槛：分别算（按份注册），且由候选数推导

`aplus/forward.py:298-310` 已经按**本份注册自己的候选数**算
`t_crit = bonferroni_t(len(raw), df=...)` ⇒ 本批 N=3。

**为什么不与前四批合并算**（这是必须写下来的选择）：

1. **机制**：既有实现就是这么算的，seal 口径与门槛口径必须与前四批一致；
2. **决定性**：**合并算会让已冻结批次的门槛随后来者变** ——
   每加一批，前四批的判决门槛就变一次 ⇒ 冻结批次的判决口径被后来者改写
   ⇒ 违反「注册即冻结」。分别算时，第 1 批的门槛只由第 1 批自己的 6 组决定，
   **永远不变**；
3. **假设不同**：每份注册有自己的 `registered_on`、窗口与假设集。

🚨 **代价（诚实边界）**：分别算**不控制**「人跨批次挑一个最好的」。
⇒ **判决只能在批内做**；跨批次的「哪一批更好」不构成判决。

门槛**由候选数推导，不许手打**：`aplus/evolve_race.py::bonferroni_t`。
刚注册时窗口为空 ⇒ df=2 ⇒ 暂定 `t_crit = bonferroni_t(3, 2) = 7.6488`
（`forward_status` 标 `t_crit_provisional = true`）；窗口变长 ⇒ df 变大 ⇒ 门槛下降
（同一 α，自由度变大，这是 t 分布的正确行为，不是放水）。

## 幂等

`aplus/forward.py::register` 的批次身份 = `注册日 + 候选三元组集合`（与顺序无关）
⇒ 同一天重跑**不会**插第二次。
⚠️ 但若 `DEFAULT_WEIGHTS` / `LIVE_REBALANCE_EVERY` / `BUFFER_MULTIPLE` 变了，
三元组就变了 ⇒ 那**是**新的一批（本批是冻结的，新配置要开新批次）。

## 预注册

`docs/PREREG_FORWARD_BATCH5_2026-10-20.md`
（⚠️ 该文件明写「补记于动手当日，不是事前登记」）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.evolve import LIVE_REBALANCE_EVERY  # noqa: E402
from aplus.factor import DEFAULT_WEIGHTS  # noqa: E402
from aplus.forward import days_to_verdict, register  # noqa: E402
from aplus.rebalance import BUFFER_MULTIPLE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"
ON = "2026-10-20"

# 候选清单里**没有一个手打字面量**（见模块 docstring「参数来源铁律」）。
# 三组的权重都是**同一份** DEFAULT_WEIGHTS，只有 K 与 buffer 两个参数不同
# ⇒ 任何两组的差别都是**受控**的（单参数对照）。
CANDS = [
    # ── ① 实盘那一支 ──────────────────────────────────────────────
    # weights = DEFAULT_WEIGHTS（import，不手打）
    # K = LIVE_REBALANCE_EVERY = 21（import，不手打）
    # buffer_multiple = None（实盘**没有缓冲**：`aplus/evolve.py:90`）
    # 判决需 3×21 = 63 个交易日。
    {"weights": dict(DEFAULT_WEIGHTS),
     "rebalance_every": LIVE_REBALANCE_EVERY, "buffer_multiple": None},

    # ── ② 成本臂：同一个权重，只把 K 拉长到 63 ────────────────────
    # 年调仓次数 244/21 = 11.6190 → 244/63 = 3.8730（**恰好 3.0000×**）
    # ⇒ 名义年摩擦 6.3905% → 2.1302%（`aplus/hurdle.py` 的 244/k 与 0.0055）
    # 🚨 这是**成本算术**，不是拟合；且**不许**声称它超额更好。
    # 判决需 3×63 = 189 个交易日（约 9 个月）—— 本批最贵的一格。
    {"weights": dict(DEFAULT_WEIGHTS),
     "rebalance_every": 63, "buffer_multiple": None},

    # ── ③ 单参数对照：只把缓冲打开 ────────────────────────────────
    # BUFFER_MULTIPLE = 3.0（import，不手打）
    # 问的是「缓冲算法在**实盘这组信号**上是不是也有害」。
    {"weights": dict(DEFAULT_WEIGHTS),
     "rebalance_every": LIVE_REBALANCE_EVERY,
     "buffer_multiple": BUFFER_MULTIPLE},
]

NOTE = """第五批前瞻验证候选：**在看到任何 2026-10-21 之后的行情之前**冻结。

## 为什么要有这一批

前四批共 19 组候选**没有一组是「当前实盘那一支」**：
实盘权重来自 `aplus/evolve.py::load_weights` 的回落分支
（`data/reports/weights.json` 不存在 ⇒ `source="fallback"`、`caveat_kind="unraced"`），
即 `aplus/factor.py:61 DEFAULT_WEIGHTS` + `aplus/evolve.py:79 LIVE_REBALANCE_EVERY`
+ `buffer_multiple=None`。台账里最接近的第 1 批 ① 是**符号翻转之前**的旧配置，
第 2/3 批那组是 **8 因子**判决权重，不是实盘出单的那一组。

⇒ 前瞻底稿（`verification.parquet`，实测 160 行 / 8 个出单日 / 6 个独立成交窗口）
测的候选族里，**没有一个是真正在交易的那一支**。
⇒ 而 D1 闸门现在判 `NOT_PASSED_POWERLESS`（检出力下限 |ρ|=0.2647），
前瞻是它唯一还能解锁判决的路 ⇒ **它此刻测错了对象。**

## 这一批是「检验」，不是「采纳」

冻结实盘配置不等于相信实盘配置。它的作用是让**从未被看过的交易日**
去回答「实盘真正在跑的那一套，前瞻到底有没有证据」。

## 三组候选

① DEFAULT_WEIGHTS / K=21 / buf=None —— **实盘那一支**
② DEFAULT_WEIGHTS / K=63 / buf=None —— **成本臂**（年调仓次数 11.6190→3.8730，
   名义年摩擦 6.3905%→2.1302%，**恰好 3.0000×**；这是成本算术，
   🚨 **不许**声称它超额更好）
③ DEFAULT_WEIGHTS / K=21 / buf=3.0 —— **单参数对照**（缓冲在实盘信号上是否有害）

⚠️ ① 与 ② 与 ③ 的差别都是**单参数**（K 或 buffer），权重完全相同
⇒ 任何两组的比较都是受控的。
⚠️ ① 才是实盘那一支（**无缓冲**）；③ 是**有缓冲**的对照臂。

## 参数来源

三个参数全部 **import**，候选清单里**没有一个手打字面量**：
`aplus.factor.DEFAULT_WEIGHTS` / `aplus.evolve.LIVE_REBALANCE_EVERY` /
`aplus.rebalance.BUFFER_MULTIPLE`。
手打的那份是**化石**（2026-10-14 符号翻转后 `mom20/vol20/vsur` 三个符号全变了，
而第 1 批 ① 至今仍是翻转前的 `{+1.0,+0.3,+0.5,+0.5}`）。
⚠️ 参数将来变了要**开新批次**，不许改本批。

## 门槛：分别算（按份注册）

`aplus/forward.py` 按**本份注册自己的候选数**算 Bonferroni 门槛 ⇒ 本批 **N=3**。
**不与前四批合并算**，理由：① 既有实现如此，口径必须一致；
② **合并算会让已冻结批次的门槛随后来者变** ⇒ 冻结批次的判决口径被改写 ⇒
违反「注册即冻结」；③ 每份注册有自己的注册日、窗口与假设集。
🚨 代价：分别算**不控制**跨批次挑选 ⇒ **判决只能在批内做**。

门槛由 `aplus/evolve_race.py::bonferroni_t` 从候选数**推导**，不许手打。
刚注册时窗口为空 ⇒ df=2 ⇒ 暂定 t_crit = 7.6488（`t_crit_provisional=true`）。

## 判决对象

本批预注册的判决对象**只有 ①**。②③ 是对照臂，只用来读 ① 的解释，
**不**做「谁更好」的判决（K 不同 ⇒ 独立周期数不同 ⇒ 跨臂比 t 是两个不同量的比）。
三臂全部测不出方向时的写法、以及四条失败分支，写在
`docs/PREREG_FORWARD_BATCH5_2026-10-20.md` §5。

## 仍不能回答的

① 不晋级（前瞻验证只裁决）；② 不能回答年化能否到 20%（前瞻底稿是逐笔收益，
没有净值/年化字段）；③ 不能回答 ②③ 谁更好；
④ ② 没有任何可引用的历史证据（`k_sweep_summary.json` 判 stale）；
⑤ 不能把 ① 的显著当成「实盘已验收」（实盘权重 `caveat_kind="unraced"`）。
"""

EVIDENCE = ("aplus/factor.py:61 DEFAULT_WEIGHTS + aplus/evolve.py:79 "
            "LIVE_REBALANCE_EVERY + aplus/evolve.py:275 load_weights 的回落分支"
            "（data/reports/weights.json 不存在 ⇒ source=fallback、"
            "caveat_kind=unraced）"
            " / data/reports/verification.parquet（160 行 / 8 出单日 / 6 独立成交窗口）"
            " / data/reports/score_ranking_power.json（::verdict=NOT_PASSED_POWERLESS，"
            "::by_score_col.score.arms.up._stats.null_abs_rho_q95_power_floor=0.2647）"
            " / aplus/hurdle.py::rounds_per_year + COST_REAL_50K（成本算术 244/k、0.0055）"
            " / aplus/rebalance.py:43 BUFFER_MULTIPLE"
            " / docs/PREREG_FORWARD_BATCH5_2026-10-20.md（预注册）")


def main(reports_dir=None) -> int:
    """登记第五批。

    `reports_dir` 存在的唯一理由是**可测**：`tests/test_forward_candidates_r5.py`
    必须用 `tmp_path` 跑注册，不许把测试输出写进生产台账
    （本仓第 132 层：测试把自己的输出写进生产路径，再让新加的章替它洗成合法）。
    默认值仍是生产路径，所以命令行直接跑与 r4 的行为完全一致。
    """
    reports = Path(reports_dir) if reports_dir is not None else REPORTS
    rec = register(reports, ON, CANDS, note=NOTE, evidence=EVIDENCE)
    print(f"已登记 {len(rec['candidates'])} 组 · 注册日 {rec['registered_on']}")
    print(f"封印 sha256 = {rec.get('sealed_sha256')}")
    print(f"本批 Bonferroni 门槛按 {len(rec['candidates'])} 组校正")
    for c in rec["candidates"]:
        k = c["rebalance_every"]
        print(f"  K={k:<3} buf={str(c['buffer_multiple']):<5} "
              f"判决需 {days_to_verdict(k):>3} 个交易日  {c['weights']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
