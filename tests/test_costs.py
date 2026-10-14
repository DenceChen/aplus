"""交易成本模型——**先钉住判据的形状，再钉住数字**。

## 这条测试存在的理由

`backtest.STOCK_COST_DEFAULT = 0.003` 是个**平费率**。它在 100 万本金上
大致成立，在 5 万本金上系统性偏低——因为**最低佣金 ¥5/笔**在这个量级开始咬人。

一条永远绿的测试会教所有人忽略测试。所以这里的测试要能在两种情况下红：

1. **费率改动**（有人把万三改成万二而忘了更新文档）
2. **口径被悄悄改回平费率**（有人为了「让数字好看」把最低佣金去掉）

第 2 种是真正危险的那个：它不会报错，只会让所有结论偏乐观。

## 名字里的原则

`test_..._not_...` 那几条是本文件的核心——
**它们断言的是「一件坏事没有发生」，而不是「一件好事发生了」**。
后者容易被优化掉，前者不会。
"""
import pytest

from aplus import costs as C
from aplus.backtest import STOCK_COST_DEFAULT


# ── 最低佣金：非线性在哪 ────────────────────────────────────────────

def test_the_minimum_commission_actually_binds_at_small_notional():
    """**这条是整个模块存在的理由。**

    ¥5,000 × 万三 = ¥1.50，但按 ¥5 收——实际费率 0.10%，3.3 倍。
    """
    fee = C.FeeSchedule()
    assert fee.commission(5_000.0) == C.COMMISSION_MIN == 5.0
    assert fee.commission(5_000.0) > 5_000.0 * C.COMMISSION_RATE * 3


def test_the_minimum_commission_stops_binding_above_the_threshold():
    """超过 ¥16,667 之后费率生效，最低佣金不再咬人。"""
    fee = C.FeeSchedule()
    just_above = C.COMMISSION_MIN_BINDS_BELOW + 1.0
    assert fee.commission(just_above) > C.COMMISSION_MIN
    assert fee.commission(100_000.0) == pytest.approx(100_000.0 * C.COMMISSION_RATE)


def test_the_break_even_threshold_is_where_the_two_regimes_meet():
    """两条曲线的交点必须正好是阈值——否则 `COMMISSION_MIN_BINDS_BELOW` 是装饰。"""
    fee = C.FeeSchedule()
    x = C.COMMISSION_MIN_BINDS_BELOW
    assert fee.commission(x) == pytest.approx(C.COMMISSION_MIN)
    assert fee.commission(x) == pytest.approx(x * C.COMMISSION_RATE)


# ── 非线性：逐笔 vs 先求和 ───────────────────────────────────────────

def test_rebalancing_is_computed_per_trade_not_on_the_sum():
    """**先求和会把成本算少约三分之一。**

    ¥25,000 分 5 笔 ¥5,000 → 每笔吃 ¥5 最低，佣金共 5 × 2 × ¥5 = ¥50。
    合成 1 笔 ¥25,000 → 佣金只要 2 × ¥7.50 = ¥15。

    ## 注意比值是 1.34 而不是 3.3

    佣金部分差 3.3 倍，但**往返总价只差 1.34 倍**——
    因为价差（0.15%/边）是线性的，先求和时**一模一样**，
    而它才是往返成本的大头。

    我第一版断言写的是 `per_trade > naive * 2`，**被自己的测试抓出来了**：
    我把佣金的比值当成了总成本的比值。
    写这种断言时，「哪个量的比值」必须先想清楚，
    否则它会用一个真实存在、但方向错误的数字给人虚假的安全感。
    """
    fee = C.FeeSchedule()
    per_trade = fee.rebalance(25_000.0, 5)     # 138.00
    naive = fee.round_trip(25_000.0)            # 103.00
    assert per_trade > naive
    assert per_trade / naive == pytest.approx(1.34, abs=0.01)
    # 差值恰好是佣金部分：5 笔 × 2 边 × ¥5 = ¥50，减去合成口径的 2 × ¥7.50 = ¥15
    assert per_trade - naive == pytest.approx(5 * 2 * C.COMMISSION_MIN
                                              - 2 * (25_000.0 * C.COMMISSION_RATE))


def test_a_larger_position_stops_paying_the_minimum():
    """**同一个 5 万本金，仓位越少，单笔越大，越不吃最低佣金。**

    这是「本金小」不等于「成本高」的调节阀：
    ¥25,000 分 5 笔 = ¥138.00，分 2 笔 = ¥108.00，**省 22%**。

    分 2 笔时单笔 ¥12,500，万三只有 ¥3.75 仍低于 ¥5，
    所以**还在吃最低佣金，但只吃 2 次**——降本来自「少交几次」而非「费率下降」。

    所以「成本高」不是小本金的宿命，是**仓位切分方式**的结果，
    而仓位切分是策略设计者能动的杠杆。
    """
    fee = C.FeeSchedule()
    five = fee.rebalance(25_000.0, 5)         # 138.00
    two = fee.rebalance(25_000.0, 2)          # 108.00
    assert two < five
    # 逐笔费率同样下降（这次两个量都是金额，比值才有意义）
    assert (two / 25_000.0) < (five / 25_000.0)
    assert five / two == pytest.approx(1.28, abs=0.01)


# ── 各项费率的归属 ───────────────────────────────────────────────────

def test_stamp_duty_is_charged_on_the_sell_side_only():
    """印花税只在卖出侧收。双边都收会让卖出成本虚高 1/4。"""
    fee = C.FeeSchedule()
    n = 50_000.0
    assert fee.sell(n) - fee.buy(n) == pytest.approx(n * C.STAMP_DUTY_SELL)


def test_a_round_trip_costs_more_than_twice_a_one_way():
    """卖出侧比买入侧贵——**不对称是常态**，对称假设会低估。"""
    fee = C.FeeSchedule()
    assert fee.round_trip(50_000.0) > 2 * fee.buy(50_000.0)


def test_zero_and_negative_notional_cost_nothing():
    """空仓不该凭空产生成本——`max(notional*rate, 5)` 会给 0 收 ¥5。"""
    fee = C.FeeSchedule()
    assert fee.commission(0.0) == 0.0
    assert fee.round_trip(0.0) == 0.0
    assert fee.rebalance(0.0, 5) == 0.0
    # 负数是上游的 bug 信号，但不能变成负成本（那会变成「收入」）
    assert fee.commission(-100.0) == 0.0


def test_zero_positions_is_not_an_infinite_cost():
    """`n_positions <= 0` 时除法会炸或无穷大——必须显式返回 0。"""
    assert C.FeeSchedule().rebalance(25_000.0, 0) == 0.0


# ── 与回测口径的对照（这条会红，而且它**应该**红） ───────────────────

def test_the_backtest_constant_understates_the_real_cost_at_50k():
    """**characterization test：钉住「口径不一致」这个事实本身。**

    `STOCK_COST_DEFAULT = 0.003` 是双边 0.3%；
    5 万本金下真实双边约 0.55%。回测**把成本算低了约 0.25 个百分点/轮**。

    ## 这条测试现在是**绿的**（初版文档写成红的，那是错的）

    它断言的是**差异存在**（`0.003 < 0.0055`），不是差异不存在。
    所以它是绿的——而它的价值恰恰在于**一旦有人把口径改对，它立刻变红**：

        「口径已经一致了（回测 0.003 vs 实测 0.0055）——把本测试改成断言相等，
          并在注释里记下重跑日期。」

    ## 为什么不直接改成 `== 0.0055`

    因为 `STOCK_COST_DEFAULT` 一旦改成 capital-aware 模型，
    **所有已发布的回测数字都得重跑**。在那之前把断言改绿，
    等于用「断言通过」掩盖「已知口径不符」。

    **一个红着的测试是一个待办事项；一个被改绿去适配现状的测试是一个谎言。**

    ## 这个差异对结论意味着什么

    方向要说清楚：回测成本偏低 → 已报净收益偏乐观。
    而已发布的策略结论是**零结果或负的**，扣掉 0.25%/轮只会更差——
    **结论方向不变，但绝对数字必须重算。**

    （初版这里写「这条测试现在红着，是故意的」——实际是绿的。
      一个连自己的状态都描述错的注释，正是本文件开头批评的那种化石。
      写完就该跑一遍看看它到底红不红。）
    """
    fee = C.FeeSchedule()
    per_pos = 50_000.0 * 0.5 / 5          # ¥5,000
    real_round_trip = fee.round_trip(per_pos) / per_pos
    assert STOCK_COST_DEFAULT < real_round_trip, (
        f"口径已经一致了（回测 {STOCK_COST_DEFAULT} vs 实测 {real_round_trip:.4f}）"
        "——把本测试改成断言相等，并在注释里记下重跑日期。"
        "在那之前不要动 STOCK_COST_DEFAULT，否则所有已发布数字失去意义。")
    assert real_round_trip / STOCK_COST_DEFAULT > 1.5, (
        "差距缩小到 1.5 倍以内，说明口径已经接近，值得重新审视本测试")


# ── 调仓频率的量级 ───────────────────────────────────────────────────

def test_daily_rebalancing_is_mathematically_out_of_reach():
    """**日频调仓要 158% 的毛收益才能拿到 20% 净值。**

    这条测试钉住的是一个**设计级约束**，不是实现细节：
    它把「每天推荐」和「每天交易」在代码里分开了。
    用户的原话是「每天推荐」——推荐可以日频，持仓周期不行。
    """
    g = C.gross_needed_for_net(50_000.0, 0.5, 5, C.TRADING_DAYS_PER_YEAR)
    assert g > 1.5, (
        f"日频所需毛收益 {g:.0%} 已不再是「不可能」的量级——"
        "重算摩擦参数，并重新审视这条约束是否还成立")


def test_friction_falls_monotonically_with_slower_rebalancing():
    """调仓越慢摩擦越低——**单调性**比具体数值更重要，
    因为它决定了「降低调仓频率」这个动作的收益是确定方向、只是幅度未知。"""
    rows = C.table()
    for a, b in zip(rows, rows[1:]):
        assert a["annual_cost_on_capital"] > b["annual_cost_on_capital"]
        assert a["gross_needed"] > b["gross_needed"]
    # 一年调 12 次 vs 4 次，摩擦应当约 3 倍
    by_label = {r["label"]: r for r in rows}
    ratio = (by_label["每月"]["annual_cost_on_capital"]
             / by_label["每季"]["annual_cost_on_capital"])
    assert ratio == pytest.approx(3.0, rel=1e-9)


def test_the_table_reaches_monthly_and_annual_targets_at_some_frequency():
    """**存在一个可行区间——目标不是不可能，是有约束的。**

    每月 26.6% / 每季 22.2%，都高于 20% 但在量级内（不是 158%）。
    这条测试的意义是：**可行性是一个区间，不是一个点**，
    而区间一旦找到就该写进设计约束而不是每次重算。
    """
    g = {r["label"]: r["gross_needed"] for r in C.table()}
    assert g["每月"] < 0.30
    assert g["每季"] < 0.25
    assert g["日频"] > 1.5


# ── 换算口径（这类换算最容易错一倍） ─────────────────────────────────

def test_the_capital_and_equity_views_differ_by_the_equity_weight():
    """摩擦按本金计；换算到权益口径要**除以权益比例**。

    分母搞错会让数字看着合理、实际差一倍——
    这是所有「本金 vs 仓位」换算最常见的翻车点。
    """
    on_capital = C.annual_friction(50_000.0, 0.5, 5, 12)
    on_equity = on_capital / 0.5
    assert on_equity == pytest.approx(on_capital * 2)


def test_gross_needed_converts_the_drag_back_onto_the_equity_base():
    """`gross_needed = net_target + 摩擦/权益`。

    如果这里忘了换算，摩擦会按本金加到毛收益上——
    而毛收益是相对权益定义的，两个口径混用直接让答案偏一半。
    """
    g = C.gross_needed_for_net(50_000.0, 0.5, 5, 12)
    drag = C.annual_friction(50_000.0, 0.5, 5, 12) / 0.5
    assert g == pytest.approx(0.20 + drag)


def test_a_zero_equity_weight_is_rejected_not_silently_divided_by():
    """`/ 0` 会抛 ZeroDivisionError，但**报一个说人话的错**才有用。"""
    with pytest.raises(ValueError, match="equity_weight"):
        C.gross_needed_for_net(50_000.0, 0.0, 5, 12)


# ── 文档里的数字必须还能被算出来 ─────────────────────────────────────

def test_the_docstring_numbers_match_what_the_code_produces():
    """**注释里的数字是最容易变成化石的东西。**

    docstring 声称「日频 138% / 权益、信号须给出 158%」。
    这条测试让它**在代码变而注释没变时变红**——
    于是化石的形成过程本身是被监控的。
    """
    by_label = {r["label"]: r for r in C.table()}
    assert by_label["日频"]["annual_cost_on_equity"] == pytest.approx(1.38, abs=0.005)
    assert by_label["日频"]["gross_needed"] == pytest.approx(1.58, abs=0.005)
    assert by_label["每月"]["annual_cost_on_capital"] == pytest.approx(0.0331, abs=0.0005)