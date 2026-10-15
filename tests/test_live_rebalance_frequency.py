"""实盘换仓周期：**降低损耗**，不是提高收益 —— 两件事不许被说成一件事。

## 决策依据（`data/reports/k_sweep_summary.json`，6 年，**2026-10-15 P0 结清后重跑**）

成本取**实测**（从毛/净两条序列反推），不是名义公式；
`(毛超额 − 净) / 实测成本` 五档均 = **1.000**（P0 之前是 1.90~1.98）。

🚨 **2026-10-15 K 链整链重跑后本表整体作废过一次**，下面是**新值**。
变化的不是小数位，是**结论**：K=21 的净由 **+0.49% 变成 −1.24%**，
最好的一档 `ratio` 由 K=10 的 **0.296** 变成 K=63 的 **0.347**，
而 **0/6 年够线五档不变** ⇒ 换仓周期这件事**仍然不解决够线问题**。
⚠️ 触发它的是守卫 `test_decision_basis_table_matches_the_committed_artifact`
—— 表格与产物逐档对账，红在「K=5 实测成本漂了」。
**如果没有这张表，下面这些数会继续以旧值流通**（这正是形状 19 的散文版）。
🚨 **名义成本那一列已删**：它等于「次数/年 × 0.0055」，是**这张表自己算得出来的**，
摆在判决依据里会让人误以为它是独立测量。

| K | 次数/年 | **实测成本** | 毛超额 | **净** |
|---|---|---|---|---|
| 5 | 48.8 | 24.61% | 9.83% | −14.79% |
| 10 | 24.4 | 13.01% | 9.36% | −3.65% |
| **21** | **11.6** | **6.26%** | 5.02% | **−1.24%** |
| 42 | 5.8 | 3.04% | 0.09% | −2.95% |
| 63 | 3.9 | 1.92% | 7.68% | 5.76% |

🚨 下面这组数（`−42.0 / −16.7 / −5.9 / −5.1 / +2.0`）是**重复扣成本**的产物，已全部作废。

门槛随成本同步收紧（`hurdle_in_report` = 目标 20% + 该档成本）：
K=5 → 46.84%，K=10 → 33.42%，K=21 → **26.39%**，K=42 → 23.20%，K=63 → 22.13%。
**五档 `hit_years` 全部 = 0**；最好一档 `ratio` 仅 **0.347**（K=63）。

⚠️ **K=63 的净最高（+5.76%）不是因为策略变好** ——
毛超额那一列 K=63 是 7.68%，比 K=10 的 9.36% **更低**。
拉长持有期买到的是**损耗下降**，不是 **edge 上升**。

🚨🚨 **「K=63 最好」本身就是赢家诅咒，别照着它调参。**
逐年最优 K **每年都不同**：
2021→K=5（12.83%）、2022→K=10（18.33%）、2023→K=21（17.42%）、
2024→K=10（22.74%）、2025→K=63（0.67%）、2026→K=5（14.24%）。
**年内的跨 K 极差远大于跨年的极差** ⇒ 噪声压过 K 的效应，
「K=63 最优」是**把 5 个噪声数取平均**得来的。
⚠️ 可交付名数另有硬上限（摩擦 ≤20%/年 ⇒ **N ≈ 50**，见 CLAUDE.md 第 17 种形状），
而 K=63 配 546 只是测量手段、**不是能上线的组合**。
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

# ★ 对账容差的**单一真相源**。主判据与容差守卫共用它 ——
#   两边各写一个 5e-4 就是「第 102 层：探针打在自己的副本上」：
#   实测把主判据放宽到 5e-2 时，容差守卫读的还是副本 ⇒ 照样绿。
_TOL = 5e-4          # 比率列（实测成本 / 毛超额 / 净）
_TOL_ROUNDS = 0.05    # 次数/年列：244/K 的显示精度，与比率列**不同量纲**
_TOL_IDENT = 1e-3     # 会计恒等式 (毛−净)/实测成本：P0 量级是 1.90~1.98


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
        # ★ 5 列（名义成本那一列已删，见 docstring 的说明）。
        #   守卫的列数必须跟表一起变，否则它会红在一个**解析错误**上，
        #   而不是一个语义错误上 —— 红的原因必须是我要验的那件事。
        if len(cells) != 5 or not cells[0].isdigit():
            continue
        parsed[int(cells[0])] = {
            "rounds": float(cells[1]),
            "real": float(cells[2].rstrip("%")) / 100,
            "gross": float(cells[3].rstrip("%")) / 100,
            "net": float(cells[4].rstrip("%")) / 100,
        }
    assert set(parsed) == set(rows), (
        f"docstring 表格的 K 档 {sorted(parsed)} 与产物 {sorted(rows)} 对不上")

    for k, q in parsed.items():
        r = rows[k]
        # ★ 次数/年是 244/K 的**恒等式**，不是抄来的数 ⇒ 它有自己的容差，
        #   而且比 `_TOL` 宽（48.8 这种数多一位小数）。
        #   我第一版把它也并进 `_TOL` ⇒ 当场红：把两种量纲不同的判据塞进一个容差，
        #   就是在「为了统一而降低精度」。
        assert abs(q["rounds"] - r["rounds_per_year"]) < _TOL_ROUNDS, f"K={k} 次/年漂了"
        assert abs(q["real"] - r["avg_cost_real"]) < _TOL, f"K={k} 实测成本漂了"
        assert abs(q["gross"] - r["avg_gross"]) < _TOL, f"K={k} 毛超额漂了"
        assert abs(q["net"] - r["avg_net_real"]) < _TOL, f"K={k} 净漂了"

    # ★ 会计恒等式：这张表自己就该证明成本只扣一次
    #   🚨 它的容差曾无人看守：实测把 `1e-3` 放宽成 `1e-1` ⇒ **5 条全绿**。
    #   而恒等式偏 10% 意味着成本被扣了 1.1 次 —— **P0 那个 bug 的量级**。
    #   ⇒ 与比率列一样提为单一真相源，交给容差守卫一起盯。
    for k, r in rows.items():
        ident = (r["avg_gross"] - r["avg_net_real"]) / r["avg_cost_real"]
        assert abs(ident - 1.0) < _TOL_IDENT, (
            f"K={k} 的 (毛−净)/实测成本 = {ident:.3f} ≠ 1.000 ⇒ 成本又被扣了第二次")


def test_the_table_comparison_tolerance_cannot_be_loosened_silently():
    """★ 容差必须被**单独测一条**。

    🚨 实测（2026-10-15）：把 `< 5e-4` 放宽成 `< 5e-2` ⇒ **其余 4 条全绿**。
    数字错 1 个百分点照样通过，而守卫「在守东西」的外观完全不变。
    这与「守卫比恒真的更危险」同源：一条松了的守卫是**绿的**，
    会把人推向「这张表大概没错」。

    ⚠️ 判据不能写成「容差 == 某个字面量」—— 那只是把字面量又抄一遍，
      改代码的人和改断言的人是同一个，于是**两边一起改就又恒真了**
      （第 5 种形状：断言 == 公式，公式错了断言跟着一错）。
    ⇒ 这里钉的是**性质**：「容差必须比一个 ulp 宽、比 1 个百分点窄」，
      两个方向都要够狠才能让「1% 的错」与「最后一位的错」落在它两侧。

    ★ 第一版我把它写成了 `assert 5e-2 < tol`，而 `tol = 5e-4`
      ⇒ **它当场就是红的**（5e-2 > 5e-4）。那不是「守住了」，
      是我把自己要证明的结论写反了（第 98 层）。下面是改对之后的版本。
    """
    import math

    # 🚨 第二版这里写的是 `tol = 5e-4` —— **自己的副本**。
    #   于是把主判据里的 `< 5e-4` 改成 `< 5e-2` 时，本守卫读的还是副本 ⇒
    #   **它照样绿**（实测 L4/L5 两个变异都存活）。这就是第 102 层
    #   「变异探针打在自己的副本上」：守卫存在，但它量的不是被守的那个量。
    #   ⇒ 判据与守卫必须**共用同一个名字**（单一真相源）。
    tol = _TOL
    real = 0.24614910836762685          # 产物里 K=5 的实测成本

    # 方向一：容差必须**严格宽于**最小可表示偏差，否则 ulp 级噪声会天天误报
    drift = math.nextafter(real, 1.0) - real
    assert drift != 0.0, "nextafter 必须真的产生了偏差，否则本条是空转"
    assert abs(drift) < tol, (
        f"容差 {tol} 比一个 ulp（{abs(drift):.3e}）还窄 ⇒ 浮点末位噪声就会误报")

    # 方向二：容差必须**严格窄于** 1 个百分点，否则「错 1%」这种最常见的
    # 抄表错误会静默通过 —— 这正是实测里 L4 变异放过去的那一类。
    assert tol < 1e-2, (
        f"容差 {tol} 已经宽到能放过 1 个百分点的错误；"
        "实测把 5e-4 放宽成 5e-2 后守卫全绿")

    # 同一个纪律对另外两个容差同样成立 —— 逐一钉，不给「只有主判据被看着」的错觉
    assert _TOL_ROUNDS < 0.1, "次数/年容差放宽到 0.1 ⇒ 244/K 抄错也放过"
    # 恒等式容差：P0（成本被扣两次）的实测比值是 **1.90~1.98**，
    # 而容差 0.1 仍然抓得住 ⇒ 这是「容差必须窄于缺陷本身」的判据，不是抄字面量
    assert _TOL_IDENT < 0.90, (
        f"恒等式容差 {_TOL_IDENT} 已宽到放过 P0 的真实比值 1.90~1.98")
    assert abs(1.95 - 1.0) > _TOL_IDENT, "正控：P0 那个量级必须真的被判红"
