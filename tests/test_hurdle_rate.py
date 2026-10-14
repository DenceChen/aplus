"""20% 净值目标的**频率门槛**——一道算术题，不是一道策略题。

## 为什么它该是个模块而不是注释

策略讨论里「要不要降频」被当成调参问题反复重开。但它其实是算术：
在某个成本模型下，**达到 20% 净值所需的毛超额**，只取决于调仓频率。

把答案写成可执行的函数之后：
- 「日频能不能做」不用再讨论，答案是算出来的
- 成本口径一改，所有频率的门槛自动跟着变
- 任何宣称自己「差一点到 20%」的策略，都能立刻核对它差在哪

## 这道题对用户目标的直接回答

5 万本金、A 股、**年化 20% 净值**、每天 10 涨 10 跌。

注意「每天**推荐**」不等于「每天**交易**」。推荐可以是日频，
交易频率是另一个变量——而这个变量决定了整个目标是否可能。
"""
import pytest

from aplus.hurdle import (
    DAYS_PER_YEAR, hurdle_rate, rounds_per_year, feasible_k, max_affordable_daily,
)


# ── 门槛的定义 ──────────────────────────────────────────────────────

def test_hurdle_is_the_target_plus_the_cost():
    """门槛 = 目标净值 + 年成本。**没有第二条路**——毛超额是唯一的输入。"""
    assert hurdle_rate(target=0.20, cost_per_round=0.003, rounds_per_year=0) == \
        pytest.approx(0.20)


def test_no_rebalancing_means_no_cost_and_no_hurdle_above_target():
    """不调仓就没有成本拖累。门槛塌回目标本身。"""
    assert hurdle_rate(0.20, 0.003, 0) == pytest.approx(0.20)
    assert hurdle_rate(0.20, 0.003, 0) < hurdle_rate(0.20, 0.003, 244)


def test_hurdle_rises_linearly_in_rounds():
    """每多调仓一次就多一份成本，门槛线性上升——没有规模效应可以摊薄。"""
    a = hurdle_rate(0.20, 0.003, 100)
    b = hurdle_rate(0.20, 0.003, 200)
    assert b - a == pytest.approx(100 * 0.003)


def test_hurdle_rises_in_cost():
    """成本模型变贵，所有频率的门槛同比抬升——这正是口径必须统一的原因。"""
    assert (hurdle_rate(0.20, 0.0055, 244)
            > hurdle_rate(0.20, 0.003, 244))


# ── 这道题对 5 万本金的答案 ─────────────────────────────────────────

def test_daily_rebalancing_needs_an_absurd_gross_excess():
    """**日频在任何真实成本模型下都是不可能的。**

    这条不是策略判断，是算术：244 次调仓 × 每轮双边成本。
    要 20% 净值，毛超额得超过 100%/年——A 股上不存在这种东西，
    而任何声称存在的方法都需要先解释它的换手为什么这么低。
    """
    assert hurdle_rate(0.20, 0.003, rounds_per_year(1)) > 0.90
    assert hurdle_rate(0.20, 0.0055, rounds_per_year(1)) > 1.50


def test_monthly_is_the_cheapest_frequency_that_could_still_compete():
    """月频（K=21，约 12 次/年）是门槛落到**可竞争区间**的地方。

    再降频收益也有限——门槛已贴近目标本身，
    省下的那点成本填不平策略本身的不足。
    """
    m = hurdle_rate(0.20, 0.0055, rounds_per_year(21))
    assert 0.25 < m < 0.30, f"月频门槛 {m:.1%}，与目标的距离才是可竞争的量级"
    assert m < hurdle_rate(0.20, 0.0055, rounds_per_year(5)), "月频必须明显优于周频"


def test_k_semantics_are_unambiguous():
    """**K 是「每 K 个交易日调仓一次」，不是「每年 K 次」。**

    第一版把 K 直接当年调仓次数，于是日频那行印出了季频的门槛：
    244 次调仓标成 244，K=63（季频）算成 63 次/年——门槛差 4 倍。
    换算入口必须只有一个。
    """
    assert rounds_per_year(1) == DAYS_PER_YEAR
    assert rounds_per_year(5) == pytest.approx(48.8, abs=0.1)
    assert rounds_per_year(21) == pytest.approx(11.6, abs=0.1)
    assert rounds_per_year(63) == pytest.approx(3.9, abs=0.1)
    # 更长的 K 必须意味着**更低**的年成本——单调性是这条语义的直接后果
    costs = [hurdle_rate(0.20, 0.0055, rounds_per_year(k)) for k in (1, 5, 21, 63)]
    assert costs == sorted(costs, reverse=True)


def test_feasible_frequencies_excludes_daily_under_the_real_cost_model():
    """给定一个可达到的毛超额，返回够得着的 K。

    这是给「每天推荐 10 涨 10 跌」这句话一个可执行的解读：
    推荐日频是可行的，**交易日频不可行**。
    """
    # 反转族实测最好的一行毛超额是 −9.1%，任何频率都够不着 20%
    assert feasible_k(gross_excess=-0.091, cost_per_round=0.0055) == []
    # 30% 的毛超额够得着月频（门槛 26.4%）与季频，但够不着周频（46.8%）
    got = feasible_k(gross_excess=0.30, cost_per_round=0.0055)
    assert got == [21, 63], f"30% 毛超额的可交易频率应恰是月频与季频，实得 {got}"
    assert 1 not in got and 5 not in got, "日频/周频在实况成本下不可能"


def test_max_affordable_daily_is_zero_when_the_signal_is_too_small():
    """信号太小时，答案是「一次都不调仓」而不是某个具体频率。"""
    assert max_affordable_daily(0.10, 0.0055) == 0
    assert max_affordable_daily(0.30, 0.0055) >= 21


# ── 负成本 / 非法输入 ───────────────────────────────────────────────

def test_negative_cost_is_an_error_not_a_free_lunch():
    """负成本会让门槛低于目标，**读起来像「调仓越多越好」**。

    这种错误不会让代码崩溃，只会让结论整个反号——比崩溃危险得多。
    """
    with pytest.raises(ValueError):
        hurdle_rate(0.20, -0.003, 244)


def test_negative_rounds_is_an_error():
    with pytest.raises(ValueError):
        hurdle_rate(0.20, 0.003, -1)
