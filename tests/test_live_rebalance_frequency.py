"""实盘换仓周期：**降低损耗**，不是提高收益 —— 两件事不许被说成一件事。

## 决策依据（`data/reports/k_sweep_summary.json`，6 年，**2026-10-15 P0 结清后重跑**）

成本取**实测**（从毛/净两条序列反推），不是名义公式；
`(毛超额 − 净) / 实测成本` 五档均 = **1.000**（P0 之前是 1.90~1.98）。

| K | 次数/年 | 名义成本 | **实测成本** | 毛超额 | **净** |
|---|---|---|---|---|---|
| 5 | 48.8 | 26.84% | 24.99% | 9.78% | **−15.20%** |
| 10 | 24.4 | 13.42% | 13.17% | 9.90% | **−3.26%** |
| **21** | **11.6** | **6.39%** | **6.29%** | **6.78%** | **+0.49%** |
| 42 | 5.8 | 3.20% | 3.06% | 1.13% | **−1.94%** |
| 63 | 3.9 | 2.13% | 1.92% | 6.04% | **+4.12%** |

🚨 下面这组数（`−42.0 / −16.7 / −5.9 / −5.1 / +2.0`）是**重复扣成本**的产物，已全部作废。

门槛随成本同步收紧（`hurdle_in_report` = 目标 20% + 该档成本）：
K=5 → 46.84%，K=10 → 33.42%，K=21 → **26.39%**，K=42 → 23.20%，K=63 → 22.13%。
**五档 `hit_years` 全部 = 0**；最好一档 `ratio` 仅 **0.296**（K=10）。

⚠️ **K=63 的净最高（+4.12%）不是因为策略变好** ——
毛超额那一列 K=63 只有 6.04%，比 K=10 的 9.90% **更低**。
拉长持有期买到的是**损耗下降**，不是 **edge 上升**。
⇒ `test_decision_basis_table_matches_the_committed_artifact` 把这张表钉在产物上，
不许再漂。

日志里另有（**两条都列，不挑大的说**）：
`[cost] K=1 下成本吃掉毛超额的最坏比例 222%`（12 组候选，来源 `ic_guided_race_7y.json`）
与 `858%`（9 组候选，来源 `ic_guided_race_7y_corrected.json`）；
`[down] K=1 下跌腿日均收益最坏 +0.16% …「10 跌」那篮子平均每天**在涨**，产品承诺未兑现`
—— 那个 **`+0.16%` 是原始涨跌幅，不是我的盈亏**（空头腿 `gross_pct` 不带符号）。

⇒ **日频换仓在 5 万本金 + 0.55% 单边成本下是结构性不可能盈利**。
改换仓周期只把损耗从"吃掉全部毛超额"降到"吃掉一多半"。
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_live_fallback_uses_monthly_not_daily():
    """回落路径的换仓周期必须是 21，不是 1。

    ⚠️ 注意与 `DEFAULT_REBALANCE_EVERY` **故意不同**：
    那个是赛马口径（候选之间要比日频），这个是实盘口径。
    合并成一个常量会把赛马悄悄改成月频。
    """
    from aplus.evolve import DEFAULT_REBALANCE_EVERY, LIVE_REBALANCE_EVERY, load_weights

    assert LIVE_REBALANCE_EVERY == 21, (
        f"实盘换仓周期变成 {LIVE_REBALANCE_EVERY} —— "
        "选 21 是因为它是 K 扫描里逐年数字齐全、与既有结论同口径的一档"
    )
    assert DEFAULT_REBALANCE_EVERY == 1, (
        "赛马默认被改成非 1 ⇒ 候选之间不再可比，"
        "所有历史赛马排名作废"
    )
    assert DEFAULT_REBALANCE_EVERY != LIVE_REBALANCE_EVERY, (
        "两个常量合并了 —— 赛马口径与实盘口径必须能独立演进"
    )

    cfg = load_weights("/nonexistent-reports-dir")
    assert cfg["execution"]["rebalance_every"] == 21, (
        f"回落路径实际下发 {cfg['execution']['rebalance_every']}，应为 21"
    )


def test_k_sweep_actually_supports_the_chosen_k():
    """★ 选 21 必须**由产物支撑**，不是拍脑袋。

    外部锚：K 扫描报告里必须真的存在 K=21 这一行，
    且它的成本显著低于 K=1 那一档 —— 否则这个决定没有依据。
    """
    p = _ROOT / "data" / "reports" / "k_sweep_summary.json"
    assert p.exists(), "K 扫描产物不见了 —— 决策依据必须可复核"
    rows = {int(r["k"]): r for r in json.loads(p.read_text())["rows"]}

    assert 21 in rows and 63 in rows, f"K 扫描缺少关键档位，实有 {sorted(rows)}"
    # 成本必须随 K 单调下降（否则「省成本」这个理由本身不成立）
    ks = sorted(rows)
    costs = [rows[k]["avg_cost_real"] for k in ks]
    assert costs == sorted(costs, reverse=True), (
        f"年成本没有随 K 下降：{dict(zip(ks, costs))}"
    )
    # 选中的那一档必须真的比日频便宜一个量级
    assert rows[21]["avg_cost_real"] < rows[5]["avg_cost_real"] / 3, (
        f"K=21 成本 {rows[21]['avg_cost_real']:.4f} 相对 K=5 "
        f"{rows[5]['avg_cost_real']:.4f} 并没有实质下降"
    )


def test_the_hurdle_is_not_relaxed_alongside_the_frequency():
    """★ 降 K 是提速，砍前瞻验证是作弊。

    门槛必须**跟着成本收紧**（K 越小、门槛越高），
    而不是为了让某一档够线而放松门槛。
    """
    p = _ROOT / "data" / "reports" / "k_sweep_summary.json"
    rows = {int(r["k"]): r for r in json.loads(p.read_text())["rows"]}

    for k, r in rows.items():
        assert abs(r["hurdle_recomputed"] - r["hurdle_in_report"]) < 1e-12, (
            f"K={k} 的门槛有两份且不一致"
        )
        # 门槛 = 20% 目标 + 成本，随成本下降而下降，但**永不低于 20%**
        assert r["hurdle_in_report"] >= 0.20 - 1e-9, (
            f"K={k} 门槛 {r['hurdle_in_report']} < 20% 目标本身 ⇒ 目标被放松了"
        )
    # 五档全部不够线 —— 这个结论必须留在产物里，不许被"调频率"悄悄抹掉
    assert all(r["hit_years"] == 0 for r in rows.values()), (
        "某一档开始够线了 —— 需要重新走一遍判决，而不是沿用旧结论"
    )


def test_decision_basis_table_matches_the_committed_artifact():
    """★ 决策依据那张表必须**逐格**等于产物里抽出来的数。

    ## 为什么这条必须存在

    那张表写在**本文件的 docstring** 里——也就是**决策记录的正文**。
    2026-10-15 的 P0（成本被扣两次）结清后，
    产物里的净收益全变了，而**这张表一个字都没跟着变**，
    它还印着 `−42.0 / −16.7 / −5.9 / −5.1 / +2.0` 这组**已作废**的数。

    同一批作废数字当时散落在 `CLAUDE.md`、`aplus/evolve.py` 的注释、
    本文件、`docs/TODO_measure_sigma_r.md` **四处**——
    这是第 7 层（口径标签）的一个新变体：
    **标签跟着数字走了，但没人保证数字本身是最新的。**

    「口径必须跟着数字离开脚本」的反面还有一句：
    **数字离开脚本之后，必须有一条通道把它拉回来。**

    ## 为什么是逐格而不是「方向对就行」

    本条在 P0 结清时**一次就抓到了**：三份文件同时漂移。
    写成「K 越大成本越薄」这种方向性断言，三处一起错的时候照样绿。

    ## 外部锚

    逐格的值只有一个来源：入库的 `k_sweep_summary.json`
    （`.gitignore` 第 27 行 `!data/reports/*.json` 把 JSON 结论放回仓库）。
    没有第二个实现可以对照 ⇒ 这条守卫防的是**漂移**，不是**算错**。
    """
    import re

    p = _ROOT / "data" / "reports" / "k_sweep_summary.json"
    assert p.is_file(), (
        "k_sweep_summary.json 不在库里 —— 决策依据无从核对，"
        "而 CLAUDE.md 旧版曾把「报告不入库」写成惯例，与 .gitignore 相反"
    )
    rows = {int(r["k"]): r for r in json.loads(p.read_text(encoding="utf-8"))["rows"]}

    doc = __doc__ or ""
    # 只认表格行：`| K | 次数/年 | ... | 净 |` 之后、表格结束之前的行
    body = doc.split("| K | 次数/年 |", 1)[1].split("\n\n", 1)[0]
    # ★ 表格里用的是排版减号 U+2212，不是 ASCII '-'.
    #   第一版直接 float() ⇒ ValueError，守卫红在一个**解析错误**上，
    #   而不是一个语义错误上 —— **红的原因必须是我要验的那件事。**
    tr = str.maketrans("−", "-")
    parsed = {}
    for line in body.splitlines():
        cells = [c.strip().strip("*").translate(tr) for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not cells[0].isdigit():
            continue
        parsed[int(cells[0])] = {
            "rounds": float(cells[1]),
            "nominal": float(cells[2].rstrip("%")) / 100,
            "real": float(cells[3].rstrip("%")) / 100,
            "gross": float(cells[4].rstrip("%")) / 100,
            "net": float(cells[5].rstrip("%")) / 100,
        }
    assert set(parsed) == set(rows), (
        f"docstring 表格的 K 档 {sorted(parsed)} 与产物 {sorted(rows)} 对不上")

    for k, q in parsed.items():
        r = rows[k]
        assert abs(q["rounds"] - r["rounds_per_year"]) < 0.05, f"K={k} 次/年漂了"
        assert abs(q["nominal"] - r["rounds_per_year"] * 0.0055) < 5e-4, f"K={k} 名义成本漂了"
        assert abs(q["real"] - r["avg_cost_real"]) < 5e-4, f"K={k} 实测成本漂了"
        assert abs(q["gross"] - r["avg_gross"]) < 5e-4, f"K={k} 毛超额漂了"
        assert abs(q["net"] - r["avg_net_real"]) < 5e-4, f"K={k} 净漂了"

    # ★ 会计恒等式：这张表自己就该证明成本只扣一次
    for k, r in rows.items():
        ident = (r["avg_gross"] - r["avg_net_real"]) / r["avg_cost_real"]
        assert abs(ident - 1.0) < 1e-3, (
            f"K={k} 的 (毛−净)/实测成本 = {ident:.3f} ≠ 1.000 ⇒ 成本又被扣了第二次")
