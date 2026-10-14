"""低频净值口径：算术年化 ≠ 复利终值，两者的差距就是本文件要钉住的东西。

## 起因

`signcorrect_freq_sweep` 报出旧默认（**错号**配置）在 K=63 上年超额
+95.0%、t=6.67。若可兑现，那是 10 只 A 股多头 4.7 年约 **60 倍**——
不是「很强」，是**不可能**。

于是必须问：年化口径本身对不对？

答案是：算术年化把单日 `+1%` 与 `−1%` 当成可以线性相加，而复利下它们是
**0**。`年化 = 逐日均值 × 244` 在日超额 ~0.39% 时给 +95%，而复利给
`(1.0039)^1154 ≈ +87 倍`——这不是精度问题，是**量级**问题。

所以本文件把 `_daily_equity`（复利）钉死，并明确它与扫描脚本口径的分工：
**复利终值是不需要相信任何年化口径的数字**。

## 三条测试

1. 复利必须真的复利（不是简单求和，也不是取最后一行）。
2. 同一天多只成分股先聚成日均，再进复利——不能逐行复利。
3. 成本必须先扣再复利；扣在复利之后是错的（成本是乘性的）。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lowfreq_plausibility import _daily_equity, _market_equity  # noqa: E402


def _pred(rows):
    df = pd.DataFrame(rows, columns=["pred_date", "code", "direction",
                                     "actual_pct", "market_pct", "cost"])
    df["result"] = "hit"
    return df


def _flat(days, per_day=1, ret=0.01, mkt=0.0, cost=0.0):
    rows = []
    for i, d in enumerate(days):
        for j in range(per_day):
            rows.append((d, f"{i}{j}", "up", ret, mkt, cost))
        rows.append((d, f"{i}d", "down", 0.0, mkt, 0.0))
    return _pred(rows)


# ---------- 1. 必须真的复利 ----------

def test_equity_is_compounded_not_summed():
    """连涨 3 天各 +1% → 终值 1.01³ = 1.030301，不是 1.03。"""
    eq = _daily_equity(_flat(["2023-01-03", "2023-01-04", "2023-01-05"]))
    assert float(eq.iloc[-1]) == pytest.approx(1.01 ** 3, rel=1e-9), (
        f"终值应是复利 {1.01**3:.6f}，实得 {float(eq.iloc[-1]):.6f}")
    assert float(eq.iloc[-1]) != pytest.approx(1.03, rel=1e-6)


def test_compounding_turns_a_zero_sum_sequence_into_a_loss():
    """+1% 后 −1%：**算术**累计恰为 0，**复利**终值是 0.9999，亏 0.01%。

    这条钉的是 `σ²/2` 那个波动拖累真的落在净值上，而不是只活在
    「算术年化 vs 复利年化」的口头对比里。

    写这条测试时我原本断言「±1% 往返精确回到 1.0」——**那是错的**，
    实测 1.01 × 0.99 = 0.9999。要精确往返，第二天的跌幅得是
    −r/(1+r) = −0.9901% 而不是 −1%。真实的涨跌就是这样：涨 1% 要跌
    0.9901% 才回本。这个细节正是复利口径的全部要害。
    """
    rows = [("2023-01-03", "A", "up", 0.01, 0.0, 0.0),
            ("2023-01-03", "d", "down", 0.0, 0.0, 0.0),
            ("2023-01-04", "A", "up", -0.01, 0.0, 0.0),
            ("2023-01-04", "d", "down", 0.0, 0.0, 0.0)]
    eq = _daily_equity(_pred(rows))

    assert sum([0.01, -0.01]) == 0.0, "算术口径下这段是零和的"
    assert float(eq.iloc[-1]) == pytest.approx(1.01 * 0.99, rel=1e-12), (
        f"复利终值必须是 0.9999，实得 {float(eq.iloc[-1])}")
    assert float(eq.iloc[-1]) < 1.0, (
        "零和的收益序列在复利下必然亏损——这正是波动拖累")


def test_equity_curve_has_one_point_per_day_in_order():
    eq = _daily_equity(_flat(["2023-01-05", "2023-01-03", "2023-01-04"]))
    assert list(eq.index) == ["2023-01-03", "2023-01-04", "2023-01-05"], (
        "必须按日期升序——乱序会让复利把不相邻的日子乘在一起")


# ---------- 2. 逐日聚合 ----------

def test_multiple_constituents_are_averaged_before_compounding():
    """同一天 10 只成分股先聚成**日均**，再进复利。

    逐行复利会把一天的 10 只当 10 个交易日，终值变成 10 次方——
    终值 1.01^30 而不是 1.01^3，量级完全错。
    """
    eq = _daily_equity(_flat(["2023-01-03", "2023-01-04", "2023-01-05"],
                             per_day=10, ret=0.01))
    assert len(eq) == 3, f"必须 3 个点（3 天），实得 {len(eq)}"
    assert float(eq.iloc[-1]) == pytest.approx(1.01 ** 3, rel=1e-9)


# ---------- 3. 成本先扣后复利 ----------

def test_cost_is_deducted_before_compounding():
    """成本是**乘性**的：先扣再乘，不是复利完再减一次。

    3 天各 +1%、每日成本 0.1% → 净值 (1+0.01−0.001)³。
    若实现成「先按毛收益复利再减 0.003」，得到的是另一种东西，
    在低频（成本小但天数多）下差异会被放大。
    """
    eq = _daily_equity(_flat(["2023-01-03", "2023-01-04", "2023-01-05"],
                             ret=0.01, cost=0.001))
    assert float(eq.iloc[-1]) == pytest.approx(1.009 ** 3, rel=1e-9), (
        f"净值因子是 1+0.01−0.001=1.009，实得 {float(eq.iloc[-1])}")


def test_down_leg_never_enters_the_long_equity():
    """跌腿是**回避清单**，不建仓，不得进净值曲线。

    进了就是凭空捏出一笔没做过的交易。构造：涨腿 +1%/日、跌腿 −50%/日，
    若跌腿进了曲线，终值必然崩。
    """
    rows = []
    for d in ("2023-01-03", "2023-01-04", "2023-01-05"):
        rows.append((d, "A", "up", 0.01, 0.0, 0.0))
        rows.append((d, "B", "down", -0.50, 0.0, 0.0))
    eq = _daily_equity(_pred(rows))
    assert float(eq.iloc[-1]) == pytest.approx(1.01 ** 3, rel=1e-9), (
        f"跌腿混进了多头净值：{float(eq.iloc[-1])}")


# ---------- 大盘对照 ----------

def test_market_equity_uses_the_same_dates_and_is_daily():
    eq = _market_equity(_flat(["2023-01-03", "2023-01-04"], mkt=0.05))
    assert len(eq) == 2
    assert float(eq.iloc[-1]) == pytest.approx(1.05 ** 2, rel=1e-9)


def test_market_equity_dedups_repeated_rows_per_day():
    """同一天多行带同一个 market_pct，必须去重后再复利。

    不去重就是 (1+mkt)^行数 而不是 ^(天数)，大盘这条基准线自己就会虚高，
    相对超额的分子分母同时被污染。
    """
    rows = [("2023-01-03", f"{i}", "up", 0.0, 0.01, 0.0) for i in range(10)]
    rows.append(("2023-01-03", "d", "down", 0.0, 0.01, 0.0))
    eq = _market_equity(_pred(rows))
    assert len(eq) == 1, f"同一天必须只有 1 个点，实得 {len(eq)}"
    assert float(eq.iloc[-1]) == pytest.approx(1.01, rel=1e-9)