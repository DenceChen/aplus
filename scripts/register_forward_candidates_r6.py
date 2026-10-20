"""把**基本面族 A**冻结进**第六批**前瞻台账（2026-10-21）。

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-21.md`（§2 D-1 / §4.1 / §7 顺序）
       + `docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §8.2 B4

## 这一批为什么必须**先于判决**存在

预注册 §5.2 第 191 行逐字：

> 「`n_A` = 候选组数 × K 档数，**由 `aplus/forward.py::register` 的候选表长度算出来，
> 不许手打**。」

⇒ ⇒ **顺序不可调换**：先注册（候选集**冻结**）→ 再从注册表长度算 `n_A` → 再判。
⇒ 注册**必须先于**看到判决数据 ⇒ 「注册即冻结」这条纪律是**顺序保证**，
不是事后补记。

## 候选集（**十组，一个不多一个不少**）

族 A 的**五个**因子 × **两个** K = 10 组：

| 因子 | 权重 | K | 角色 |
|---|---|---|---|
| `bm` | `{bm: 1.0}` | 21 / 63 | 族 A 成员（F1） |
| `ep` | `{ep: 1.0}` | 21 / 63 | 族 A 成员（F2）——**不因选择偏差而丢**（D-1） |
| `roe` | `{roe: 1.0}` | 21 / 63 | 族 A 成员（F3） |
| `size` | `{size: 1.0}` | 21 / 63 | **对照因子**（F4，§2.5）：BM/EP 的最强竞争解释 |
| `q` | `{q: 1.0}` | 21 / 63 | 族 A 成员（F5） |

🚨 **K 写死 {21, 63}**，**不许从 `aplus/evolve.py::LIVE_REBALANCE_EVERY` 派生**
（它已是 63 ⇒ 派生会少一组 ⇒ 本批的假设数从 10 掉到 5 ⇒
Bonferroni 门槛被悄悄放松 ⇒ **冻结的批次被改写** ⇒ 违反「注册即冻结」）。
⇒ 预注册 §4.3 明写族 A 的判据是 **K ∈ {21, 63}** 两个 K ⇒ 本批与它一致。

## 为什么把 `ep` 也放进前瞻（**本批最要紧的一条**）

轮次 A 实测：EP 的因子值分布在 500 只覆盖池与全池之间**测出了选择偏差**
（`data/reports/fundamental_coverage_axes_2026-10-20.json`：
最大分档占比偏差 5.556pp > 随机零假设 95 分位 4.444pp）。
对照臂分解：**selection +5.556pp / vintage +0.000pp** ⇒ 偏差来自「**挑了哪些票**」，
**不是因子本身**。

⇒ 而 500 只覆盖池是**臂 R（后修值历史臂）**的池子；**前瞻臂的池子是全池 5,460 只**
（`aplus/forward.py` 的窗口从注册日之后起，PIT 帧逐日给出全池因子）
⇒ **偏差在前瞻臂上不存在**。

⇒ ⇒ 所以 EP 的正确处理**不是**「把它从族里踢掉」，
而是「**让它进族、并且让它在前瞻臂上被测**」——
否则就把一个**可修的缓存问题**变成一个**永久的因子缺口**。

## ⚠️ 这一批需要前瞻面板提供 F2/F3/F4/F5 四列

`aplus/factor.py::composite_score` 对**缺失的因子列会 KeyError**
⇒ 若前瞻面板只有 `bm` 列（F1 由 `aplus/factor_fundamental.py::attach_pit_bm` 提供），
本批的 8 组候选会**当场崩**，而崩的是**整个前瞻验证**（连老批次一起陪葬）。

⇒ 因此本批落盘的同时，`scripts/forward_validate.py` 必须把
`ep / roe / size / q` 四列**挂进前瞻面板**
（`scripts/fundamental_family_factors.py::attach_forward_factors`，
口径与 `attach_pit_bm` **同构**：那天没有 PIT 快照 ⇒ 该列**全为 NaN**
⇒ `composite_score` 把那天剔掉 ⇒ **那天不出信号**，不是出零信号）。

## 这一批是「检验」，不是「采纳」

冻结候选 ≠ 相信候选。前瞻验证只裁决，**不晋级实盘**（预注册 §8.3）。
本批的判决对象是**族 A 的五因子 × 两个 K**；
门槛由 `aplus/evolve_race.py::bonferroni_t` 从**本批候选数**推导，**不许手打**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus.forward import days_to_verdict, register  # noqa: E402

REPORTS = ROOT / "data" / "reports"

#: 注册日 = 2026-10-21（预注册 §4.2 臂 F 从 2026-10-21 起）。
ON = "2026-10-21"

#: 🚨 **K 写死**，不从 `LIVE_REBALANCE_EVERY` 派生（见模块 docstring）。
BATCH6_K = (21, 63)

#: 本批的因子（预注册 §2.1 F1–F5，**顺序即 §2.1**）。
#: `size` 是**对照因子**（§2.5），但**必须每批都带上**——
#: 它是 BM/EP 的最强竞争解释（石川的 Banz 效应 + 壳价值论）。
BATCH6_FACTORS = ("bm", "ep", "roe", "size", "q")

CANDS = [
    {"weights": {f: 1.0}, "rebalance_every": k, "buffer_multiple": None}
    for f in BATCH6_FACTORS for k in BATCH6_K
]

NOTE = """第六批前瞻验证候选：**基本面族 A（F1–F5）**，
**在看到任何 2026-10-21 之后的行情之前**冻结。

## 为什么是这一批

预注册 `docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-21.md` §5.2 第 191 行逐字：
「`n_A` = 候选组数 × K 档数，**由 `aplus/forward.py::register` 的候选表长度算出来，
不许手打**」⇒ ⇒ **顺序不可调换**：先注册（候选集冻结）→ 再从注册表长度算 `n_A` → 再判。

## 十组候选是什么

族 A 的**五个**因子 × **两个** K（21 / 63）= 10 组，每组权重 = `{因子: 1.0}`。

- F1 `bm` = log(BPS/close)、F2 `ep` = log(EPSJB/close)、
  F3 `roe` = ROEJQ、F4 `size` = -log(close × a_shares)、F5 `q` = MGJYXJJE/BPS。
- 🚨 **F4 `size` 是对照因子**（预注册 §2.5：它是 BM/EP 的最强竞争解释，
  **不进入**基本面族的「晋级」），但**必须每批都带上**。
- 🚨 **K 写死 {21, 63}**，不从 `LIVE_REBALANCE_EVERY` 派生
  （它已是 63 ⇒ 派生会少一组 ⇒ 本批假设数从 10 掉到 5 ⇒ 门槛被悄悄放松）。

## 为什么 `ep` 也在里面（**最要紧的一条**）

轮次 A 实测 EP 有**选择偏差**：500 只覆盖池 vs 全池的因子分档占比
最大绝对偏差 **5.556pp** > 随机零假设 95 分位 **4.444pp**；
对照臂分解 = **selection +5.556pp / vintage +0.000pp**
⇒ 偏差来自「**挑了哪些票**」，**不是因子本身**。

⇒ 500 只池是**臂 R（后修值历史臂）**的池子；**前瞻臂的池子是全池 5,460 只**
⇒ **偏差在前瞻臂上不存在** ⇒ 把 EP 踢出族，就把一个**可修的缓存问题**
变成一个**永久的因子缺口**。

## ⚠️ 前瞻面板必须提供 F2/F3/F4/F5 四列

`aplus/factor.py::composite_score` 对缺失的因子列会 **KeyError**
⇒ 本批的 8 组候选需要前瞻面板额外挂上 `ep / roe / size / q`
（`scripts/fundamental_family_factors.py::attach_forward_factors`；
口径与 `aplus/factor_fundamental.py::attach_pit_bm` 同构：
那天没有 PIT 快照 ⇒ 该列全为 NaN ⇒ 那天**不出信号**，不是出零信号）。

## 这是「检验」，不是「采纳」

冻结候选 ≠ 相信候选。前瞻验证**只裁决，不晋级实盘**（预注册 §8.3）。
本批的判决对象是族 A 五因子 × 两个 K；门槛由
`aplus/evolve_race.py::bonferroni_t` 从**本批候选数（10）**推导。

## 仍然不能回答的

① 不能回答「年化能否到 20%」（前瞻底稿是逐笔收益，没有净值/年化字段）；
② 不能回答「哪个因子更好」（K 不同 ⇒ 独立周期数不同 ⇒ 跨臂比 t 是两个不同量的比）；
③ 不能在本批上出**采纳**结论（后修值历史臂的读数不可引用）；
④ 不能替代臂 F 的判决力要求：**3 个独立调仓周期**
（K=21 要 63 个交易日、K=63 要 189 个交易日）。
"""

EVIDENCE = ("docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md §2.1/§2.5/§4.3/§5.2/§8.2 "
            "+ docs/PREREG_FUNDAMENTAL_FAMILY_ROUNDB_2026-10-21.md §2 D-1 / §4.1 / §7 "
            "/ data/reports/fundamental_coverage_axes_2026-10-20.json::factor_distributions "
            "（EP 选择偏差 5.556pp > 随机 95 分位 4.444pp；selection +5.556pp / vintage +0.000pp）"
            "/ data/reports/fundamental_factor_family_cross_section_2026-10-20.json"
            "::family_a_n_A_inputs")


def main(reports_dir=None) -> int:
    """登记第六批。

    `reports_dir` 存在的唯一理由是**可测**：`tests/` 必须用 `tmp_path` 跑注册，
    不许把测试输出写进生产台账（本仓第 132 层：测试把自己的输出写进生产路径，
    再让新加的章替它洗成合法）。默认值仍是生产路径。
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
