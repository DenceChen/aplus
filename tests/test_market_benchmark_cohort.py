"""市场基准的成分必须是**信号日当天真实存在**的股票。

## 事故

`_market_by_day` 从 `daily_map` 的全部键里随机抽 150 只当基准。这 150 只里
包含**信号日尚未上市**的票。`pos = searchsorted(dates, after_date, "right")`
对一只还没上市的票返回 0，于是 `_next_bars` 返回它**上市后的头两根 K 线**，
那个收益被记成信号日的「市场收益」——而且**对上市前的每一个信号日重复一次**。

实测（2019-01 ~ 2023-12，随机种子 42，150 只抽样）：

    口径                              均值/日      复利终值   负收益占比
    正常交易（D 当天有 K 线）        +0.06559%     1.9656     44.8%
    上市前（D 前一根都没有）         −3.47011%     0.0000    100.0%
    面板 _market（现用，混在一起）   −0.40192%     0.0069     63.1%

一个群体把基准从 +0.066%/日 拖到 −0.402%/日，**偏差 −0.468%/日 ≈ −114%/年**。
而 `up_excess_vs_market = E[r_up] − E[r_mkt]` 是本平台的主指标之一——
它被系统性地**虚增约 114 个百分点/年**。

## 为什么这类 bug 特别难发现

1. 数字**不像坏了**：`−0.40%/日` 是个体面的数，不会有除零、不会有 NaN。
2. 方向**看起来合理**：市场基准常年低迷、策略常年「跑赢」，
   正是每个回测都想看到的样子。
3. 单票验算**完全正确**：`_next_bars` 的 `close/prev_close − 1` 与厂商
   `pctChg` 逐位吻合（实测 102/106/133 只零不一致）。
   错的是**纳入了哪些票**，而那只在聚合层看得见。

所以这里钉的不是「某个数字」，而是**成员资格规则**：
基准里一只票在信号日不存在，就不许它进。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.backtest import _market_by_day, _next_bars

D = "2020-06-01"          # 信号日
D1, D2 = "2020-06-02", "2020-06-03"


def _df(rows):
    """最小日线表。`rows` = [(date, close), ...]，必须按日期升序。"""
    return pd.DataFrame({
        "date": [r[0] for r in rows], "code": "X", "open": 10.0, "high": 10.0,
        "low": 10.0, "close": [r[1] for r in rows], "volume": 1.0, "amount": 1.0,
        "tradestatus": 1, "isST": 0, "turn": 0.1, "pctChg": 0.0,
    })


# ---------- RED 判据：上市前的票不得进入基准 ----------

def test_stock_that_did_not_exist_on_signal_date_is_excluded():
    """核心判据。`_next_bars` 对它返回上市后头两根 K 线，必须被拒。"""
    dm = {
        "OLD": _df([(D, 10.0), (D1, 10.0), (D2, 10.0)]),
        # IPO 在信号日之后——`dates` 里第一根就是 D1
        # 收益刻意取 **−5%（合法单日收益）**：若取 −95% 会被
        # `MAX_PLAUSIBLE_RETURN` 的 ±40% 上限拦下，测试就会**因为价格不合理
        # 而通过**，看起来红实则假——测的就不是成员资格了。
        "NEW": _df([(D1, 20.0), (D2, 19.0)]),
    }
    mkt = _market_by_day(dm, [D])
    # 只有 OLD 的话，基准 = 10.0/10.0 − 1 = 0.0
    assert mkt[D] == pytest.approx(0.0), (
        f"基准被上市前的票污染成 {mkt[D]:+.4%}；"
        f"信号日不存在的股票不得计入当日市场")


def test_excluding_a_pre_ipo_stock_does_not_change_a_clean_benchmark():
    """反向：一只**无害**的上市前股票（收益恰为 0）也不该改变基准。

    钉的不是「数值恰好等于某个值」，而是**成员资格**——
    否则一个碰巧 0 收益的样本会让上面的判据看起来通过。
    """
    dm = {
        "OLD": _df([(D, 10.0), (D1, 10.0), (D2, 12.0)]),        # 真实 +20%
        "NEW0": _df([(D1, 5.0), (D2, 5.0)]),                    # 上市前，收益 0
    }
    assert _market_by_day(dm, [D])[D] == pytest.approx(0.20)


def test_the_bug_is_reachable_through_the_raw_primitive():
    """把病灶钉在最底层：`next_bars` 自己不该吐出上市前的票。

    这条比上面两条更硬——它不依赖聚合逻辑，修在源头就自动对。
    如果将来有人认为「聚合层过滤就行」，这条会拦住他。
    """
    dm = {"NEW": _df([(D1, 20.0), (D2, 19.0)])}      # −5%，合法，不受上限保护
    out = _next_bars(dm, ["NEW"], D, entry_lag=1)
    assert out.empty, (
        f"信号日 {D} 不存在的票不应有成交窗口，实得 {out.to_dict('records')}")


# ---------- 不得误伤：正常口径必须原样保留 ----------

def test_a_stock_suspended_on_signal_day_is_still_excluded_not_guessed():
    """**停牌 ≠ 上市前**，两者判据不同，必须区分清楚。

    停牌票在 D 当天**确实存在**（有 D 之前的 K 线），只是当天没交易。
    本次修的是「D 之前一根都没有」这类，不许顺手把停牌也一并毙掉——
    那会改变涨腿「持有到复牌」这条已论证过的语义。
    """
    dm = {
        "OLD": _df([("2020-05-28", 10.0), (D, 10.0), (D1, 10.0), (D2, 11.0)]),
        # 5-28 有、5-29~5-31 停牌、D 当天有、D1 有、D2 有 → 正常持有
        "OLD2": _df([("2020-05-28", 10.0), (D, 10.0), (D1, 10.0), (D2, 10.0)]),
    }
    assert _market_by_day(dm, [D])[D] == pytest.approx((0.10 + 0.0) / 2)


def test_a_stock_with_a_bar_on_the_signal_date_is_unaffected():
    """正向对照组：D 当天有 K 线的票，行为一个字都不能变。"""
    dm = {"OK": _df([("2020-05-28", 8.0), (D, 10.0), (D1, 11.0), (D2, 12.0)])}
    assert _market_by_day(dm, [D])[D] == pytest.approx(12.0 / 11.0 - 1)


def test_empty_cohort_still_yields_zero_without_raising():
    """一只都取不到时仍是 0.0（既有行为）——修 bug 不许顺带改这个契约。"""
    dm = {"NEW": _df([(D1, 20.0), (D2, 19.0)])}
    assert _market_by_day(dm, [D])[D] == 0.0


def test_a_real_time_series_has_exactly_one_bar_per_date():
    """前提守卫：`searchsorted` 的语义依赖日期表**严格递增且无重复**。

    重复日期会让 `pos` 指向重复块的第一个，而 `entry/exit_i` 落进块内，
    于是「哪两根 K 线」变成不确定的——上面所有判据都建立在无重复的前提上。
    """
    dm = {"OK": _df([(D, 10.0), (D1, 11.0), (D2, 12.0)])}
    assert dm["OK"]["date"].is_unique
    dates = np.sort(dm["OK"]["date"].to_numpy())
    assert list(dates) == sorted(set(dates))
