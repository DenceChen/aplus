"""复利口径的收益：**这是 20% 目标唯一能对标的尺子**（2026-10-14）。

## 为什么必须先补这把尺子

用户的目标是「年化 20%」。年化收益有**两种**，它们在这个项目里差着十几个点：

- **算术口径** = 日均收益 × 252
- **复利口径** = 把日收益真正连乘，再对年数开根号（CAGR）

两者之差是 `σ²/2 × 252`——**波动越大，差得越远**。实测本项目 10 只股票
等权组合的日波动是 3.44%（年化 54.6%），于是：

    算术 − 复利 = 0.0344²/2 × 252 = **14.91%/年**

这不是精度问题，是量级问题。拿它对 20% 的目标，会得出完全相反的结论。

## 实测证据（现任权重的回测，2024-10-14 ~ 2026-10-09）

    日均净收益（算术）    -0.68224%
    算术×252 年化         -171.92%   ← 报告里的口径
    复利累计              -96.96%
    真实 CAGR             -84.70%   ← 复利口径
    年化波动              54.6%

算术值 −171.9% 在物理上不可能（年化跌幅超过 100%），一眼就能看出口径错了；
而它确实印在报告里。

## 为什么此前没人发现

报告里**没有任何一个累计或年化字段**——`rolling_stats` 只出均值，
从不连乘。全项目唯一存在的 `annualized_return` 在
`bond_excess_analysis.json`，那是债券策略的，而且是**训练段**的
（测试段年化 −12.89%）。

## 本文件的判别力在哪

`test_zero_mean_high_vol_must_not_report_zero_return` 是关键一条：
均值**恰好为 0**、波动很大的一串收益，算术口径会报出「年化 0%，
刚好不达标」；复利口径必须报出一个**明显为负**的数。
只把算术值换个标签的实现，这条立刻红。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.verify import compounded_stats, daily_portfolio_returns


def _hist(daily, n_up=10, direction="up", cost=0.0, start="2024-01-02"):
    """把「每天一个收益」摊成逐笔记录，形状与 `rolling_stats` 的输入一致。"""
    d0 = pd.Timestamp(start)
    rows = []
    for i, r in enumerate(daily):
        for k in range(n_up):
            rows.append({
                "pred_date": d0 + pd.Timedelta(days=i),
                "code": f"{i:04d}.SZ{k:02d}",
                "direction": direction,
                "actual_pct": r,
                "cost": cost,
                "result": "hit" if r > 0 else "miss",
                "market_pct": 0.0,
            })
    return pd.DataFrame(rows)


def _zero_mean(sigma, n, seed):
    """**样本均值精确为 0**、波动 `sigma` 的收益序列。

    ## 为什么必须去均值，而不是直接 `rng.normal(0, sigma, n)`

    本文件第一版写的是 `rng.normal(0.0, 0.03, 300)`，然后断言
    `arith_ann ≈ 0`。实测样本均值是 **−0.396%/日** → 算术年化 **−99.7%**。
    300 个样本的均值偏离 0 的量级是 σ/√n = 0.03/17.3 ≈ 0.17%，
    随机抽到 −0.396% 是小概率但完全可能——而且**它是合法的**。

    于是那条断言的前提根本不是「免费」的：它要求随机序列的样本均值恰好
    落回 0，测试就在赌随机数，赌输了就得到一条**测不出任何东西**的测试
    （红的原因是构造没对上，与实现对错无关）。

    减掉自身均值把前提变成恒等式。这样测的才是真正要测的东西：
    **均值恰好为 0 的高波动序列，复利口径必须报出明显为负。**
    """
    x = np.random.default_rng(seed).normal(0.0, sigma, n)
    return list(x - x.mean())


# ---------- 复利本身 ----------

def test_total_return_is_the_product_not_the_mean():
    """+10% 后要回到原点，第二天必须 −9.0909%（−10/110），不是 −10%。

    这条也钉住一个常见错误：`(1+a)(1+b)` 里 b 是**相对上一天的净值**，
    不是相对最初本金。写成 `−10%` 会得到累计 −1%，看起来像复利算错了，
    其实是输入就错了。
    """
    up, down = 0.10, -0.10 / 1.10
    r = daily_portfolio_returns(_hist([up, down]))
    st = compounded_stats(_hist([up, down]))
    assert st["total_return"] == pytest.approx(0.0, abs=1e-9)
    assert st["arith_daily"] == pytest.approx((up + down) / 2, abs=1e-9)
    assert st["arith_ann"] == pytest.approx((up + down) / 2 * 252, abs=1e-9)


def test_cagr_uses_actual_trading_days_over_252():
    """252 个交易日 +1%/日 → 累计约 +11.9%，年化约 +11.9%（1.0 年）。"""
    n = 252
    st = compounded_stats(_hist([0.01] * n))
    assert st["n_days"] == n
    assert st["years"] == pytest.approx(1.0, abs=1e-9)
    assert st["cagr"] == pytest.approx(st["total_return"], abs=1e-6)
    assert st["cagr"] > 0.11


def test_cagr_on_a_partial_year_is_not_an_arithmetic_reannualization():
    """126 天（半年）+1%/日：累计约 +6.3%，年化必须**高于**累计，不能等于累计。"""
    st = compounded_stats(_hist([0.01] * 126))
    assert st["years"] == pytest.approx(0.5, abs=1e-9)
    assert st["total_return"] == pytest.approx(1.01**126 - 1, rel=1e-9)
    assert st["cagr"] > st["total_return"], "半年收益必须年化，不能直接当成年化报"


# ---------- 这条是判别力所在 ----------

def test_zero_mean_high_vol_must_not_report_zero_return():
    """均值**恰为 0**、波动 3%/日：算术口径会说「年化 0%，刚好不达标」，那是错的。"""
    st = compounded_stats(_hist(_zero_mean(0.03, 300, seed=7)))
    assert st["arith_ann"] == pytest.approx(0.0, abs=1e-9), \
        "构造前提：算术年化**精确**为 0（见 `_zero_mean` 的说明）"
    assert st["cagr"] < -0.05, (
        f"零均值高波动的复利年化必须是明显负数，实得 {st['cagr']:.2%}。"
        "若这里接近 0，说明只是把算术值换了个标签。")


def test_volatility_drag_is_reported_and_explains_the_gap():
    """`arith_ann − cagr` 必须被报出来，且约等于 σ²/2 × 252。"""
    rng = np.random.default_rng(11)
    st = compounded_stats(_hist(list(rng.normal(0.0005, 0.034, 300))))
    expect = st["ann_vol"] ** 2 / 2
    assert st["vol_drag"] == pytest.approx(st["arith_ann"] - st["cagr"], abs=1e-9)
    assert st["vol_drag"] > 0
    assert st["vol_drag"] == pytest.approx(expect, rel=0.35), (
        f"σ²/2 = {expect:.4f}，实测拖累 {st['vol_drag']:.4f}，差得太远")


def test_a_single_day_must_not_report_an_annualised_number():
    """只测 1 天时，`arith_ann` / `vol_drag` 都必须为 `None`。

    ## 这条是被真实 API 输出逼出来的

    概览页接上复利字段后，2026-10-14 实测 `/api/status` 返回：

        n_days    = 1
        arith_ann = **-681.93%**      ← 单日 −2.71% × 252
        cagr      = **-99.90%**       ← (1−0.027)^252 − 1
        vol_drag  = -582.03%

    这三个数**在数学上都是对的**，在**解读上全是错的**：一个交易日的收益
    年化，得到的不是业绩而是噪音。而且它们会真的显示在概览页上。

    所以单日样本下所有**需要跨期**的量一律 `None`。`total_return`
    （当日实际涨跌）仍然要给——那个是事实。
    """
    one_day = pd.DataFrame([{
        "pred_date": pd.Timestamp("2026-10-12"), "code": f"{i}.SZ",
        "direction": "up", "actual_pct": -0.027, "cost": 0.0,
        "result": "miss", "market_pct": 0.0,
    } for i in range(10)])
    st = compounded_stats(one_day)

    assert st["n_days"] == 1
    assert st["total_return"] == pytest.approx(-0.027, rel=1e-6), \
        "当日的实际涨跌是事实，必须给"
    assert st["arith_ann"] is None, \
        f"1 天的样本不得年化（实得 {st['arith_ann']}）"
    assert st["ann_vol"] is None, "1 天估不出波动"
    assert st["vol_drag"] is None, "波动没估出来时，拖累也无从谈起"
    assert st["cagr"] is None, \
        f"1 天的 CAGR 恒等于单日收益的 252 次方，实得 {st['cagr']} —— 不可解读"


# ---------- 缺数据的日子不许变成「持平」 ----------

def test_days_with_too_few_valid_picks_are_dropped_not_zeroed():
    """成分不足的日子若填 0，等于宣称「那天组合没动」——那是编造。

    ## 为什么两段必须错开 `start`

    本文件第一版让 `full`(100 天) 和 `thin`(50 天) 都从 `2024-01-02` 起算，
    于是自然日**重叠**：重叠的 50 天变成 11 只成分，反而**过了**
    `MIN_BASKET`，于是 `n_days == 100`——测试红了，但原因是两段日期撞了，
    与「成分不足的日子该不该被丢弃」这个问题毫无关系。

    这种测试比没有测试更糟：它红了会让人去改**实现**，
    而实现本来是对的。错开 100 天后，两段各占 50 天，
    薄的那 50 天只有 1 只成分，必须被丢弃。
    """
    full = _hist([0.01] * 50)                                   # 10 只，日 1..50
    thin = _hist([0.01] * 50, n_up=1, start="2024-03-01")       # 1 只 < MIN_BASKET
    mixed = pd.concat([full, thin], ignore_index=True)

    # 先钉住前提：两段日期**不重叠**，否则这条测试测的是别的东西
    assert not set(full["pred_date"]) & set(thin["pred_date"]), \
        "两段 hist 的日期重叠了，本测试的前提被破坏"
    assert mixed.groupby("pred_date").size().eq(1).sum() == 50

    st = compounded_stats(mixed)
    assert st["n_days"] == 50, "只有 50 天成分够，另外 50 天必须丢弃而不是当 0"
    assert st["cagr"] > 0, "若把成分不足的 50 天当成 0 收益，日数会虚增、CAGR 被稀释"


def test_cost_is_netted_exactly_once():
    """毛 +0.2% − 成本 0.1% = 净 +0.1%；不得扣两次，也不得漏扣。"""
    st = compounded_stats(_hist([0.002], cost=0.001))
    assert st["arith_daily"] == pytest.approx(0.001, abs=1e-9)


# ---------- 空输入不许给出假数 ----------

def test_empty_history_yields_none_not_zero():
    """没有数据时返回 None。「0% 收益」和「没测过」是两件事。"""
    for h in (pd.DataFrame(), _hist([0.01]).iloc[0:0]):
        st = compounded_stats(h)
        assert st["cagr"] is None and st["total_return"] is None


def test_down_leg_is_not_a_short_position():
    """跌腿是**回避清单**，不做空。它不能被当成空头腿算出「多空收益」。

    这条与 `rolling_stats` 的既有约定一致：跌腿不扣成本，因为那是一个
    我们从未持有过的仓位。同样的道理，它也不该被复利成一条收益曲线。
    """
    st = compounded_stats(_hist([0.01] * 100, direction="down"))
    assert st["cagr"] is None, "只给跌腿时不得凭空生成一条组合曲线"
