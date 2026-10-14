"""`breadth_t` 是不是**门禁的构造物**——若成立，本项目最强的一条证据作废。

## 起点：子代理提出了一个可能推翻核心结论的观察

子代理实测：把 down 腿里 ≤−9.9% 的**封跌停尾**剔掉之后，

    广度  +41.46%（t=+1.48）  →  −42.62%（t=−1.71）

**符号翻转**。若成立，`breadth_t` 不能当作排序能力的度量。

但那是**探索性代码**跑出来的。按本项目第 8 起的纪律，
未经正式验证的探索结果**既不能当结论用，也不能顺手否掉**——
所以本文件把那个疑问写成**可执行的测试**：跑一遍就知道。

## 机制：两条腿面对的门禁**天生不对称**

`aplus/backtest.py` 的 `_up_leg_exit` 对 up 腿判 `can_sell`：

| 腿 | 门禁 | 一字跌停（≤−9.9%）时 |
|---|---|---|
| **up**（真持仓） | `can_buy` + `can_sell` | **不成交 → 持有到复牌 → 该样本不按 −10% 计入** |
| **down**（回避清单） | 只判停牌 | **原样保留，−10% 照记** |

于是**同一个 −10% 的封跌停**：

- 出现在 down 腿 → 计入（预测对了就该算对）
- 出现在 up 腿 → **被门禁吃掉**（真持仓卖不掉，不按当日价记）

`breadth = E[r_up] − E[r_down]` 里，**极端负值只在分母侧出现**。
分母的左尾比分子厚 → 价差被**机械地做大**。

> 这不是 bug——`backtest.py:172` 写明了理由：
> 「对跌腿判 `can_sell` 等于凭空惩罚一笔没做过的交易」。
> 那个理由本身站得住。**问题在于 `breadth` 把两条门禁不同的腿相减。**

## 三条纪律

1. **不许顺手否掉**：本文件先复现现象，确认它是真的，再谈结论。
2. **区分「机制存在」与「量级够大」**：前者用合成数据证明，
   后者必须实测——**别用合成数据的量级去否定真实结论**。
3. **门禁不对称是设计，不是缺陷**；要问的是
   **`breadth_t` 还能不能当证据用**，不是「该不该改门禁」。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.evolve_race import breadth_tstat


def _results(rows) -> pd.DataFrame:
    """最小可用的 results 表——`_daily_series` 只认这几列。"""
    return pd.DataFrame(rows)


#: `_daily_series` 会丢掉成分不足 `MIN_BASKET`(=3) 的日子，
#: 所以**每天每条腿至少要 3 只**，否则整段被丢掉、`n_obs = 0`。
BASKET = 3


def _leg(pred_dates, day_rets, direction, market=0.0, codes=None):
    """构造一条腿。

    `day_rets[i]` 是**第 i 天该腿全部成分的收益列表**
    （长度必须 ≥ `BASKET`）。用列表而不是标量，
    是因为 `_daily_series` 逐日 `.mean()` 后还要 `.count() >= MIN_BASKET`。
    """
    rows = []
    for i, rets in enumerate(day_rets):
        for j, r in enumerate(rets):
            rows.append({
                "pred_date": pred_dates[i],
                "direction": direction,
                "actual_pct": r,
                "market_pct": market,
                "code": (codes[i][j] if codes
                         else f"{direction[0].upper()}{i:03d}{j}"),
                **({"cost": 0.0} if direction == "up" else {}),
            })
    return rows


# ── 一、机制：极端负值只在 down 腿存在时，价差被做大 ──────────────

def test_a_one_sided_tail_makes_the_spread_look_better_than_it_is():
    """**本文件的核心断言。**

    两条腿的真实排序能力**完全相同**（都用同一个随机收益池），
    唯一差别是：**极端负值只出现在 down 腿**——
    也就是 up 腿被门禁吃掉了它的左尾。

    若此时 `breadth_t` 仍然显著为正，那么这个正 t
    **度量的是门禁不对称，不是排序能力**。

    > 对照组 `test_symmetric_tails_leave_the_spread_unchanged`
    > 用同样的池子但两端都有尾——那时价差应当**回到 0 附近**。
    > 两个测试合起来才证明「差异来自门禁，不来自信号」。
    """
    rng = np.random.default_rng(2026)
    n = 120
    dates = [f"2023-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]

    # 同一天，up 与 down 各一只篮子；**收益完全相同**（零排序能力）
    flat = [[0.0] * BASKET for _ in range(n)]
    up = _leg(dates, flat, "up")
    down = _leg(dates, [list(d) for d in flat], "down")

    # 只给 down 腿加极端负值——模拟「封跌停尾只活在一侧」
    tail = rng.choice(n, size=20, replace=False)
    tail_set = set(int(i) for i in tail)
    for i in tail_set:
        down[i * BASKET:(i + 1) * BASKET] = [
            dict(row, actual_pct=-0.099) for row in
            down[i * BASKET:(i + 1) * BASKET]]

    res = _results(up + down)
    t, n_obs = breadth_tstat(res)

    assert n_obs > 0, "必须有可测样本"
    assert t is not None and t > 0, (
        f"预期正 t（down 腿的左尾机械地推高价差），实得 {t}"
    )
    # **零排序能力**却拿到正的广度——这就是「构造物」的证据
    assert t > 2.0, (
        f"零排序能力下 t 仅 {t:.2f}，未复现子代理观察到的量级；"
        "若合成数据复现不出来，说明真实效应另有来源，别硬套"
    )


def test_symmetric_tails_leave_the_spread_unchanged():
    """**对照组**：两端都有同样的尾 → 价差回到 0。

    两个测试合起来才排除「t 为正是因为池子本身有漂移」。
    """
    rng = np.random.default_rng(2026)
    n = 120
    dates = [f"2023-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]

    flat = [[0.0] * BASKET for _ in range(n)]
    up = _leg(dates, [list(d) for d in flat], "up")
    down = _leg(dates, [list(d) for d in flat], "down")

    tail = set(int(i) for i in rng.choice(n, size=20, replace=False))
    for i in tail:
        for leg in (up, down):
            leg[i * BASKET:(i + 1) * BASKET] = [
                dict(row, actual_pct=-0.099) for row in
                leg[i * BASKET:(i + 1) * BASKET]]

    t, _ = breadth_tstat(_results(up + down))
    assert t is None or abs(t) < 0.5, (
        f"两端对称时价差应当抵消，t={t}——"
        "若不为零，说明还有第三个来源在起作用"
    )


# ── 二、口径核对：分子分母确实是同一批日子 ────────────────────────

def test_both_legs_must_cover_the_same_days():
    """**不对称的第二个来源：两腿覆盖的日子不同。**

    若 up 腿因门禁少了 30 天，那 30 天只进分母——
    与上面那条尾的机制**同源**，但来自天数而非幅度。

    所以本测试钉住：`breadth` 只在**两天都有**时才算。
    现有实现用 `pd.concat(...).dropna()` 保证这一点，
    这条测试防止将来有人改成 `fillna(0)`——
    那会让缺失的日子变成「零价差」，**又是一次静默的构造**。
    """
    dates = [f"2023-01-{1 + i:02d}" for i in range(0, 20)]
    up = _leg(dates[:15], [[0.01] * BASKET] * 15, "up")   # 只有 15 天
    down = _leg(dates, [[0.0] * BASKET] * 20, "down")     # 有 20 天

    t, n_obs = breadth_tstat(_results(up + down))
    assert n_obs == 15, (
        f"只该统计两天都有的 15 天，实得 {n_obs}——"
        "缺失日被补成 0 会让价差被稀释"
    )


# ── 三、真正要答的问题（需实测，此处只钉住「不许假装答了」）──────

def test_the_real_report_has_not_yet_been_re_examined_for_this_asymmetry():
    """**诚实的边界**：本文件证明机制存在，**不**证明真实报告里它够大。

    真实量级必须在**逐腿收益**上实测，而现有报告只有汇总行。
    这条测试的作用是：一旦有人补上实测，让它转绿并写下结论；
    **红着就说明真实量级仍未验证**。

    > 与 `test_yearly_tail_decomposition.py` 里那条故意红的测试同形——
    > **待办的可执行形式，比文档里的一行字更难被忽略**。
    """
    import json
    from pathlib import Path

    path = Path("data/reports/FOSSAL_reversal_freq_2019_2023.json")
    if not path.is_file():
        pytest.skip("报告不在（data/ 被 gitignore）")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("breadth_gate_asymmetry") is not None, (
        "报告里还没有「门禁不对称实测」这一节。"
        "机制已由本文件证明，但**真实量级未测**——"
        "在补上之前，不能引用 breadth_t 当作排序能力的证据。"
    )
