"""成交时点：信号在 D 收盘算出，最早只能在 D+1 收盘成交（TDD）。

## 这条测试在钉什么

旧口径 `_next_bars` 返回 `close = D+1 收盘`、`prev_close = D 收盘`，
于是 `actual_pct = D+1收盘 / D收盘 - 1` —— **收益窗口从 D 收盘开始算起**。

但信号是用「含 D 收盘」的数据算出来的。D 收盘那一刻，因子还没算出来，
更不可能在 D 收盘成交。**窗口的起点落在信息集之内，这是一根 K 线的前视。**

后果不是「数字偏乐观一点」，而是：
- 命中率与超额衡量的都是一笔**你无法执行的交易**
- `up_excess_vs_market` 是决定加不加仓的依据，据此交易等于追一个拿不到的数

## 正确口径

信号 D 收盘算出 → **D+1 收盘买入** → **D+2 收盘卖出**。
持有 1 个交易日，且 A 股 T+1 合法（D+1 买、D+2 卖）。

市场基准必须用**同一个窗口**（`_market_by_day` 共用 `_next_bars`），否则超额
是拿策略的 D+1→D+2 去减市场的 D→D+1，两段不同期，超额没有意义。
"""
import pandas as pd
import pytest

from aplus.backtest import _next_bars


def _dm(**closes):
    """构造 daily_map：代码 → DataFrame（日期 d0..d{n-1}，收盘给定）。"""
    out = {}
    for code, series in closes.items():
        out[code] = pd.DataFrame({
            "date": [f"2026-01-{i + 1:02d}" for i in range(len(series))],
            "open": [s * 1.0 for s in series],
            "close": list(series),
            "volume": [1e6] * len(series),
        })
    return out


DAY = "2026-01-05"          # 第 5 根 K 线（D）
ENTRY = "2026-01-06"        # D+1


def test_default_entry_is_next_bar_not_signal_bar():
    """默认口径：买入价必须是 D+1 收盘，绝不能是 D 收盘。"""
    dm = _dm(A=[100.0, 101, 102, 103, 110, 121, 133, 144])
    bars = _next_bars(dm, ["A"], DAY)
    assert len(bars) == 1
    # D=110, D+1=121, D+2=133 → 买入 121，卖出 133
    assert bars["prev_close"].iloc[0] == 121.0, "买入价必须是 D+1 收盘"
    assert bars["close"].iloc[0] == 133.0, "卖出价必须是再下一根收盘"
    assert bars["close"].iloc[0] / bars["prev_close"].iloc[0] - 1 == pytest.approx(133 / 121 - 1)


def test_legacy_lag_zero_reproduces_old_numbers():
    """entry_lag=0 保留旧口径，仅供 A/B 对照——证明修复确实挪了一根。"""
    dm = _dm(A=[100.0, 101, 102, 103, 110, 121, 133, 144])
    bars = _next_bars(dm, ["A"], DAY, entry_lag=0)
    assert bars["prev_close"].iloc[0] == 110.0
    assert bars["close"].iloc[0] == 121.0


def test_entry_price_never_uses_signal_day_close():
    """防前视的正面表述：成交窗口不得包含信号日。"""
    dm = _dm(A=[100.0, 110, 120, 130, 140, 150, 160, 170])
    bars = _next_bars(dm, ["A"], DAY)
    assert bars["prev_close"].iloc[0] > 140.0, "买入价不得是信号日收盘 140"


def test_no_trade_when_exit_bar_missing():
    """D+2 不存在（数据末尾）→ 不成交，不得拿更早的价格凑。"""
    dm = _dm(A=[100.0, 110, 120, 130, 140, 150])
    assert _next_bars(dm, ["A"], DAY).empty


def test_market_benchmark_uses_same_window():
    """市场基准与策略必须同窗口——超额才有意义。"""
    from aplus.backtest import _market_by_day
    dm = _dm(A=[100.0, 110, 120, 130, 140, 150, 160],
             B=[100.0, 110, 120, 130, 140, 150, 160])
    mkt = _market_by_day(dm, [DAY])
    # 同窗口下 A/B 走势相同 → 基准 = D+1→D+2
    assert mkt[DAY] == pytest.approx(160 / 150 - 1)


def test_corporate_action_does_not_fabricate_a_fake_crash():
    """除权横跨信号日与买入日之间，不得伪造暴跌。

    收盘序列：D=140，其后 D+1 除权跌到 10.0，D+2 收 11.0。
    - 旧口径（entry_lag=0）：买入 D 的 140、卖出 D+1 的 10.0
      → 若无防护是 **−92.9% 假崩盘**
    - 新口径（entry_lag=1）：买入 D+1 的 10.0、卖出 D+2 的 11.0 → **+10.0% 真实涨幅**

    同一天、同一个真实走势，两个口径给出天壤之别——这正是必须修的理由。

    本测试的夹具**故意不给复权因子表**（源站把缩股记成价格跳空的情形），
    于是 `MAX_PLAUSIBLE_RETURN` 那道闸门会先一步把旧口径挡下来：
    假崩盘不只是被纠正，而是**根本没机会被记进账**。这比"算对了"更彻底。
    真实数据里因子表是有记录的，正确的除权收益本来就在合法区间内。
    """
    dm = _dm(A=[100.0, 110, 120, 130, 140.0, 10.0, 11.0, 13.0])
    bars = _next_bars(dm, ["A"], DAY)
    assert bars["prev_close"].iloc[0] == 10.0, "买入价应是除权后的 D+1 收盘"
    assert bars["close"].iloc[0] == 11.0
    traded = bars["close"].iloc[0] / bars["prev_close"].iloc[0] - 1
    assert traded == pytest.approx(0.10)

    legacy = _next_bars(dm, ["A"], DAY, entry_lag=0)
    assert len(legacy) == 0, (
        "旧口径跨除权日的 −92.9% 假崩盘没有被价格可信度闸门挡住——"
        "那笔亏损会被直接记进回测"
    )


def test_entry_lag_shift_still_changes_the_window_without_corporate_action():
    """没有除权时，entry_lag 必须照旧移动整个成交窗口。

    上一条用除权场景证明"旧口径更糟"，但那条被价格闸门提前挡住了；
    这条用**正常价格**证明 entry_lag 本身的行为没有被改动。
    """
    dm = _dm(A=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0])
    lag0 = _next_bars(dm, ["A"], DAY, entry_lag=0)
    lag1 = _next_bars(dm, ["A"], DAY, entry_lag=1)
    assert lag0["prev_close"].iloc[0] != lag1["prev_close"].iloc[0]
    assert lag0["prev_close"].iloc[0] == pytest.approx(104.0)
    assert lag1["prev_close"].iloc[0] == pytest.approx(105.0)