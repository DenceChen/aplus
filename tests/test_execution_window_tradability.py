"""成交窗口（D+1 买 / D+2 卖）的可交易性闸门（TDD）。

## 这组测试在钉什么

信号日那一侧的可交易性由 `filter_universe` → `panel.can_buy` 管着
（`tests/test_tradability.py` 钉死）。**成交窗口这一侧此前只判停牌**：
`_next_bars` 的 `_bars_tradable` 只看 `volume <= 0` / `amount <= 0`，
涨停收盘、一字板、跌停收盘在 D+1 / D+2 当天一律照常成交。

成交口径是**收盘价**（`entry = pos`、`exit_i = entry + 1`，`open` 列在成交
路径上从未被读取），所以真实场景是：

    D+1 收盘封涨停 → 收盘挂单排不进队 → 买不进
    D+2 收盘封跌停 → 跌停价上全是卖单 → 卖不掉

两者此前都被当成正常交易记了收益，回测系统性偏乐观。

## 四条口径，每条都有一根钉子

1. **方向感知**：`can_buy` / `can_sell` 只对**涨腿**（真持仓）生效。
   跌腿是**回避清单**——平台从没做过那笔交易。一只票在 D+2 一字跌停只说明
   **预测对了**；把它当成「卖不掉」删掉，等于删掉最准的样本。
   → `test_down_leg_keeps_*` 系列。

2. **卖端停牌 = 持有到复牌，不是删除观测**：现状是 `continue` 把整笔从
   `rows` 里删掉，`verify` 的 `merge(how="left")` 让 `actual_pct = NaN`，
   `rolling_stats` 的 `.mean()` 静默跳过——**停牌卖出的亏损凭空消失**。
   → `test_suspended_*` 系列，其中一条用合成篮子复现「−30% 消失」。

3. **作废必须计数可见**：复牌后再无任何可成交 bar（退市 / 永久停牌）时整笔
   作废，走 `result.attrs["unfillable_execution"]`——与既有的 `unfillable`
   同一套机制。**「删掉了多少」不可读，就等于这个闸门没上线。**
   → `test_*_is_counted_and_visible`。

4. **缺列不得静默改口径**：没有 OHLC 时退回既有行为（只判停牌），
   不能让「数据缺列」变成「整池全被拦下」。
   → `test_flags_fall_back_*`。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.backtest import (_bars_tradable, _next_bars, _raw_bars)

START = "2026-01-01"


def _dates(n: int) -> list:
    return pd.bdate_range(START, periods=n).strftime("%Y-%m-%d").tolist()


def _daily(code: str, bars: list, n: int | None = None) -> pd.DataFrame:
    """bars: [(open, high, low, close, volume, amount), ...]，一行一根 K 线。

    少给参数时用 `close` 补齐其余四列（`open=high=low=close`），
    以便只关心收盘序列的用例写起来短。
    """
    rows = []
    for b in bars:
        if len(b) == 2:                       # (close, volume) 简写
            c, v = b
            rows.append((c, c, c, c, v, v * c * 10 if v > 0 else 0.0))
        else:
            rows.append(b)
    if n is not None:
        while len(rows) < n:                 # 尾部补一根平淡的 bar
            rows.append((10.0, 10.0, 10.0, 10.0, 1e6, 1e7))
    dates = _dates(len(rows))
    return pd.DataFrame({
        "date": dates, "code": [code] * len(rows),
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "volume": [r[4] for r in rows], "amount": [r[5] for r in rows],
    })


def _limit_px(prev_close: float, code: str, up: bool) -> float:
    """交易所口径的涨跌停价（四舍五入到分）。"""
    from decimal import ROUND_HALF_UP, Decimal

    from aplus.panel import price_limit

    lim = Decimal(str(price_limit(code)))
    band = Decimal(str(prev_close)) * (Decimal(1) + lim if up else Decimal(1) - lim)
    return float(band.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _seal_at(df: pd.DataFrame, i: int, up: bool = True) -> pd.DataFrame:
    """把第 i 根做成**封在限价上的一字板**（开=高=低=收=涨跌停价）。"""
    px = _limit_px(float(df["close"].iloc[i - 1]), df["code"].iloc[0], up)
    df.loc[i, ["open", "high", "low", "close"]] = px
    df.loc[i, ["volume", "amount"]] = [5.6e6, 6.4e7]
    return df


def _suspend_at(df: pd.DataFrame, i: int) -> pd.DataFrame:
    """把第 i 根做成停牌（零成交 → 无价格发现）。"""
    df.loc[i, ["volume", "amount"]] = [0.0, 0.0]
    return df


# ── A. 方向感知 ────────────────────────────────────────────────────────────

# 8 根 K 线；D = dates[5] → entry = 第 6 根、exit = 第 7 根。
_N = 8
_DAY = _dates(_N)[5]

# D+2 一字跌停：整根封在跌停价上（开=高=低=收=跌停价），卖不掉。
_SEALED_DOWN = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
_SEALED_DOWN[_N - 2] = (7.0, 7.0, 7.0, 7.0, 5.6e6, 6.4e7)     # 一字跌停
_SEALED_DOWN[_N - 1] = (7.0, 7.0, 7.0, 7.0, 5.6e6, 6.4e7)


def _sealed_down_daily(code="600001.SH"):
    df = _daily(code, _SEALED_DOWN)
    return _seal_at(df, _N - 2, up=False)


def test_down_leg_keeps_a_one_word_limit_down_sample():
    """**跌腿在 D+2 一字跌停的样本必须保留。**

    跌腿是**回避清单**——平台从没持有过这只票，从来不存在「卖不掉」这回事。
    一只票在 D+2 一字跌停，只说明**预测对了**。

    把它按「卖不掉」删掉，等于把最准的样本从统计里抹掉：
    实测 1126 次闸门拦截里约 1000 次落在跌腿，按错误口径处理会把
    「多空广度」虚高成一个与预测能力无关的数字。
    """
    dm = {"600001.SH": _sealed_down_daily()}
    bars = _next_bars(dm, ["600001.SH"], _DAY, entry_lag=1, factors_map={},
                      directions="down")
    assert len(bars) == 1, "跌腿的一字跌停样本被当成「卖不掉」删掉了"


def test_down_leg_result_is_judged_on_its_real_return():
    """保留下来的跌腿样本，`result` 必须仍按它**真实的涨跌**判定。

    「保留下来」不等于「保留一个错的值」——如果这条腿因为跌停被判成 `hit`，
    那它测的仍是可执行性而不是预测准度，只是换了个地方作弊。
    """
    from aplus.verify import verify_predictions

    dm = {"600001.SH": _sealed_down_daily()}
    bars = _next_bars(dm, ["600001.SH"], _DAY, entry_lag=1, factors_map={},
                      directions="down")
    preds = pd.DataFrame([{"code": "600001.SH", "direction": "down"}])
    res = verify_predictions(preds, bars)
    assert res["result"].iloc[0] == "hit", "跌停后它确实跌了，预测「看空」是对的"


def test_down_leg_ignores_a_limit_up_exit():
    """跌腿**不判涨停**：涨停收盘卖得掉，且我们根本没持有过这只票。

    拿 `can_sell` 原样套到跌腿上会误杀所有涨停票的「回避有效性」测量。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
    bars_data[_N - 2] = (11.0, 11.0, 11.0, 11.0, 5.6e6, 6.4e7)
    df = _seal_at(_daily("600001.SH", bars_data), _N - 2, up=True)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="down")
    assert len(out) == 1, "跌腿不该判涨停"


def test_up_leg_drops_a_one_word_limit_down_exit_that_never_resumes():
    """涨腿真持有：D+2 一字跌停且此后**再无任何可成交 bar** → 整笔作废。

    与跌腿形成对照：同样的 K 线，涨腿必须作废，跌腿必须保留。
    这两条合起来才证明闸门是**方向感知**的，而不是无差别删样本。
    """
    df = _daily("600001.SH", _SEALED_DOWN)      # 尾部两根都封死，无复牌
    df = _seal_at(df, _N - 2, up=False)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up")
    assert out.empty, "涨腿持仓卖不掉却仍被记成了已实现收益"


def test_up_leg_entry_on_a_sealed_limit_up_is_not_bought():
    """D+1 一字涨停 → **建不了仓**，那笔交易根本不存在。

    盘后回看这是最标准的乐观偏误：涨停票次日收益最高，而收盘根本排不进队。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
    bars_data[_N - 3] = (11.0, 11.0, 11.0, 11.0, 5.6e6, 6.4e7)
    df = _seal_at(_daily("600001.SH", bars_data), _N - 3, up=True)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up")
    assert out.empty, "D+1 一字涨停仍被记成了建仓"


def test_up_leg_limit_up_exit_is_still_sellable():
    """**防呆**：涨停收盘**卖得掉**（早盘持筹者挂卖单能成交）。

    这条价值在于「不要为了拦住跌停而把涨停也拦了」——那会把所有涨停票
    的变现能力误杀，方向与修复目标正好相反。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
    bars_data[_N - 2] = (11.0, 11.0, 11.0, 11.0, 5.6e6, 6.4e7)
    df = _seal_at(_daily("600001.SH", bars_data), _N - 2, up=True)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up")
    assert len(out) == 1, "涨停收盘卖不掉——can_buy 的判据被误套到了卖端"


# ── B. 卖出停牌 = 持有到复牌 ────────────────────────────────────────────────

def test_suspended_exit_is_held_until_the_first_tradable_bar():
    """D+2 停牌、D+3 复牌 → **持有到复牌那根**并用它算收益，不是删掉观测。

    「卖不掉」不等于「这笔不存在」。你确实持有、也确实被动多拿了一天，
    复牌那天的价格发现才是这笔交易的真实结果。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * (_N + 1)
    bars_data[_N - 2] = (10.0, 10.0, 10.0, 10.0, 0.0, 0.0)      # D+2 停牌
    df = _daily("600001.SH", bars_data)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up")
    assert len(out) == 1, "停牌的卖出观测被整笔删掉了"
    assert float(out["close"].iloc[0]) == 10.0
    assert float(out["prev_close"].iloc[0]) == 10.0


def test_held_until_resumption_really_reprices_at_the_resumption_bar():
    """持有到复牌后必须用**复牌那根的收盘价**算收益，而不是沿用停牌前的价。

    这正是「停牌卖出的亏损凭空消失」的机制：现价沿用 → 收益记成 0。
    这里让复牌当天暴跌 30%，收益必须真的是 −30%。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * (_N + 1)
    bars_data[_N - 2] = (10.0, 10.0, 10.0, 10.0, 0.0, 0.0)      # D+2 停牌
    bars_data[_N - 1] = (7.0, 7.0, 7.0, 7.0, 1e6, 7e6)           # D+3 复牌 −30%
    df = _daily("600001.SH", bars_data)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up")
    assert len(out) == 1
    got = float(out["close"].iloc[0]) / float(out["prev_close"].iloc[0]) - 1.0
    assert got == pytest.approx(-0.30), "复牌跳空的亏损被沿用停牌前价格抹平了"


def test_suspended_loss_no_longer_vanishes_from_the_basket():
    """合成篮子复现审计的原始事故：A 正常、B 在 D+2 停牌后复牌收 7.7（−30%）。

    旧行为：引擎只返回 `['A']`，`avg_up_return = 0.0`，
    那笔 −30% **不出现在任何统计量里**——连负号都没有。

    这是本组测试里最重要的一条：它证明修复的是**统计口径**，
    而不是多了一个布尔判据。
    """
    from aplus.backtest import evaluate_weights
    from aplus.store import LocalStore

    n = 40
    # A：温和上涨；B：D+10 停牌、D+11 复牌暴跌到 7.7（相对买入价 11.0 是 −30%）。
    def _path(base, dip_at=None, dip_to=None):
        rows = [(base, base, base, base, 1e6, base * 1e6 * 10)
                for _ in range(n)]
        if dip_at is not None:
            rows[dip_at] = (base, base, base, base, 0.0, 0.0)
            rows[dip_at + 1] = (dip_to, dip_to, dip_to, dip_to, 1e6, dip_to * 1e6)
        return rows

    store = LocalStore(None)
    store.upsert_daily("600001.SH", _daily("600001.SH", _path(11.0), n))
    store.upsert_daily("600002.SH", _daily("600002.SH", _path(11.0, 10, 7.7), n))
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = __import__("aplus.backtest", fromlist=["build_cross_sections"]) \
        .build_cross_sections(store, dates[20], dates[-3])
    res = evaluate_weights(xs, {"mom20": 1.0}, top_n=1, bottom_n=1)
    up = res[res["direction"] == "up"]
    assert len(up) >= 1
    # B 的 −30% 必须出现在涨腿的观测里，且篮子均值被它拉低（可为负）。
    assert (up["actual_pct"] < 0).any(), \
        "停牌复牌的亏损仍不在任何观测里——avg_up_return 会静默按 0 报"
    assert res.attrs["unfillable_execution"] >= 0  # 计数机制必须存在


def test_permanently_unfillable_exit_is_counted_and_visible():
    """复牌后再无任何可成交 bar → 整笔作废，**且必须计数可见**。

    退市 / 永久停牌是真实存在的一类样本（实测 1200 只穷举 1,699,528 个
    窗口：45 笔复牌后再无可成交 bar）。它们既没有可定的收益，又不能
    悄悄消失——亏损无上限，连负号都没记的那种消失最危险。
    """
    from aplus.backtest import _exec_stats

    stats = _exec_stats()
    df = _seal_at(_daily("600001.SH", _SEALED_DOWN), _N - 2, up=False)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="up", exec_stats=stats)
    assert out.empty
    assert stats["sell_void"] == 1, "作废的卖出没有被计数"


def test_execution_stats_are_per_reason_not_a_single_bucket():
    """拦截必须**按原因分桶**。

    「买不进」和「卖不掉且永不复牌」是两种完全不同的事：前者说明选股
    撞上了封板，后者说明持仓被埋。合成成一个数之后，
    「这批数字变大了」无法回答「是哪一类变大了」。
    """
    from aplus.backtest import _exec_stats

    stats = _exec_stats()
    assert set(stats) >= {"buy_unfillable", "sell_extended", "sell_void",
                          "price_implausible", "codes"}


def test_down_leg_suspension_keeps_the_existing_behaviour():
    """跌腿只判停牌——且沿用既有行为（**停牌即不观测**），不引入持有延长。

    跌腿是回避清单：平台没持有过，不存在「被停牌埋住」这回事。
    把延长逻辑套到跌腿上会把「回避清单」变成「第二本账」。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * (_N + 1)
    bars_data[_N - 2] = (10.0, 10.0, 10.0, 10.0, 0.0, 0.0)      # D+2 停牌
    df = _daily("600001.SH", bars_data)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={}, directions="down")
    assert out.empty, "跌腿不应因为停牌就延长持有——它根本没持有过"


# ── C. `_raw_bars` / 旗标 ───────────────────────────────────────────────────

def test_raw_bars_full_carries_every_column_the_gate_needs():
    """`_raw_bars(full=True)` 必须给出判 `can_buy` / `can_sell` 的全部列。

    判据是**逐条**的：少一列，对应的判据就静默失效，且没有任何报错。
    """
    df = _daily("600001.SH", [(10.0, 11.0, 9.0, 10.5, 1e6, 1e7)])
    raw = _raw_bars(df, full=True)
    names = _RAW_ROW_NAMES
    assert raw.shape[0] == len(names)
    for row, name in enumerate(names):
        assert name in df.columns or name == "prev_close", f"{name} 无来源"
    assert raw[names.index("close")][0] == 10.5
    # prev_close 取该股自身时间轴上一根，绝不跨股票取。
    assert np.isnan(raw[names.index("prev_close")][0])


def test_prev_close_comes_from_the_stocks_own_previous_bar():
    df = _daily("600001.SH", [(10.0, 1e6), (12.0, 1e6), (13.0, 1e6)])
    raw = _raw_bars(df, full=True)
    prev = raw[_RAW_ROW_NAMES.index("prev_close")]
    assert np.isnan(prev[0])
    assert prev[1] == 10.0
    assert prev[2] == 12.0


def test_bar_flags_match_the_scalar_predicates():
    """逐股旗标必须与 `panel.can_buy` / `can_sell` **逐位一致**。

    同一份判据的两条路径一旦分叉，信号日与成交窗口就在量两种东西，
    而报告看上去完全正常。
    """
    from aplus.backtest import _bar_flags
    from aplus.panel import can_buy, can_sell

    df = _daily("600001.SH", [(10.0, 1e6), (12.0, 1e6), (13.0, 1e6), (11.0, 0.0)])
    raw = _raw_bars(df, full=True)
    buy, sell = _bar_flags("600001.SH", df, raw, prev_close=None)
    rows = {n: i for i, n in enumerate(_RAW_ROW_NAMES)}
    for i in range(len(df)):
        args = {n: raw[rows[n]][i] for n in ("open", "high", "low", "close",
                                            "volume", "amount")}
        p = raw[rows["prev_close"]][i]
        assert bool(buy[i]) == bool(can_buy("600001.SH", prev_close=p, **args))
        assert bool(sell[i]) == bool(can_sell("600001.SH", prev_close=p, **args))


def test_flags_fall_back_to_suspension_only_when_ohlc_is_missing():
    """缺 OHLC 列 → 退回既有行为（只判停牌），**不得**整池拦下。

    `can_buy` 在缺列时会把 `prev_close` 判成未知 → 全部 False。若不退回，
    一次数据缺列就会让整池观测归零，而且**没有任何报错**。
    """
    from aplus.backtest import _bar_flags

    df = _daily("600001.SH", [(10.0, 1e6), (11.0, 1e6)]).drop(columns=["high"])
    raw = _raw_bars(df)                       # legacy 两列
    flags = _bar_flags("600001.SH", df, raw, prev_close=None)
    assert flags is None, "缺列时必须明确返回 None，让调用方退回旧口径"


def test_flags_stay_none_when_the_frame_is_empty():
    from aplus.backtest import _bar_flags

    df = _daily("600001.SH", [])
    assert _bar_flags("600001.SH", df, _raw_bars(df), prev_close=None) is None


def test_bars_tradable_contract_is_unchanged_for_factor_ic():
    """`_raw_bars` / `_bars_tradable` 是 `aplus.factor_ic` 的公开契约。

    `factor_ic` 用同一个 `_bars_tradable` 算 IC 的前瞻收益样本；一旦签名或
    语义漂移，IC 就在量另一件事，而 IC 表照样有均值、有 t 值、有星号。
    """
    df = _daily("600001.SH", [(10.0, 1e6), (11.0, 0.0), (12.0, 1e6)])
    raw = _raw_bars(df)
    assert raw.shape == (2, 3)               # 仍是 (volume, amount)
    assert not _bars_tradable(raw, 1, 2)
    assert _bars_tradable(raw, 0, 2)


# ── D. 除权除息必须还原后再判涨跌停 ────────────────────────────────────────

def test_ex_rights_day_is_not_mislabelled_as_a_limit_move():
    """10 送 10 当天不复权价腰斩——**照常交易**，不得判成跌停卖不掉。

    这正是旧注释里「不判涨跌停」的真实理由，但它推出来的结论错了：
    错的不是「不判」，而是「拿**未经除权还原**的 prev_close 去判」。
    交易所的涨跌停价是按**除权后前收盘价**算的，正确还原后
    腰斩根本不触及跌停线。这条测试证明闸门可以既严格又不误伤。
    """
    from aplus.adjust import attach_factor

    raw_closes = [10.0, 10.1, 5.0, 5.1]              # 第 3 根 10:1 拆股
    df = _daily("600001.SH", [(c, 1e6) for c in raw_closes])
    events = pd.DataFrame({"date": [_dates(4)[2]], "adjust_factor": [2.0]})
    fm = {"600001.SH": events}
    day = _dates(4)[1]                                # D = 第 2 根 → entry 第 3 根、exit 第 4 根

    out = _next_bars({"600001.SH": df}, ["600001.SH"], day, entry_lag=1,
                     factors_map=fm, directions="up")
    assert len(out) == 1, "除权日被当成跌停板拦下了——闸门用了未还原的 prev_close"
    got = float(out["close"].iloc[0]) / float(out["prev_close"].iloc[0]) - 1.0
    assert got == pytest.approx(5.1 / 5.0 - 1.0)


# ── E. 已知缺口：ST ±5% ─────────────────────────────────────────────────────

def test_st_five_percent_band_is_not_applied_and_the_gap_is_real():
    """**已知缺口的显式记录**：ST 的 ±5% 价格笼子在本闸门里**没有生效**。

    `price_limit(code)` 在**信息上**就无从知道 ST 状态（它只吃 code），
    所以 ST 一字板（封在 ±5%）会被判成「没封板」而漏放。

    影响面很小但不为零：`filter_universe` 在**进篮子之前**已用 isST 列 +
    名称双信号把 ST 剔掉了，所以本闸门漏放的都是「信号日不是 ST、
    窗口内变成 ST」的票。这条测试把缺口钉在这里，而不是假装它不存在。
    """
    code = "600001.SH"
    df = _daily(code, [(10.0, 1e6)] * _N)
    df = _seal_at(df, _N - 3, up=True)              # 封在 +10%（主板档）
    out = _next_bars({code: df}, [code], _DAY, entry_lag=1, factors_map={},
                     directions="up")
    assert out.empty, "主板 ±10% 的封板必须被拦下（本闸门生效的部分）"

    # 同一形态但只封到 +5%（ST 档）：主板档判据看不见它 → 漏放。
    st_df = _daily(code, [(10.0, 1e6)] * _N)
    st_df.loc[_N - 3, ["open", "high", "low", "close"]] = 10.5
    st_df.loc[_N - 3, ["volume", "amount"]] = [1e6, 1e7]
    st_out = _next_bars({code: st_df}, [code], _DAY, entry_lag=1, factors_map={},
                        directions="up")
    assert len(st_out) == 1, \
        "ST ±5% 封板本应被漏放（price_limit 拿不到 ST 状态）——若它被拦下，" \
        "说明 ST 支持已在本仓实现，先更新 backtest.py 的缺口注释"


# ── F. 回归地基：默认口径与生产接线 ────────────────────────────────────────

def test_default_no_direction_keeps_the_legacy_suspension_only_gate():
    """不传 `directions` → **既有行为**（只判停牌），不静默改口径。

    单元测试里大量夹具是 `open=high=low=close` 的合成价格序列，其中
    不少根**恰好**落在涨停价上（合成数据的巧合，不是真实封板）。
    让默认口径变成「严格」会把这些夹具全部误判为不可成交，
    而它们的本意是测滞后口径 / 价格可信度 / 除权，不是测可交易性。

    代价是：**默认不等于生产口径**。生产两个调用点都显式传 directions，
    由 `test_evaluate_weights_*` 钉死。
    """
    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
    bars_data[_N - 3] = (11.0, 11.0, 11.0, 11.0, 5.6e6, 6.4e7)
    df = _seal_at(_daily("600001.SH", bars_data), _N - 3, up=True)
    out = _next_bars({"600001.SH": df}, ["600001.SH"], _DAY, entry_lag=1,
                     factors_map={})
    assert len(out) == 1, "默认口径被悄悄改成了严格闸门"


def test_execution_window_directions_are_per_code():
    """`directions` 传 dict → 逐 code 判定，同一批 K 线按腿分流。

    生产调用点一次传 up + down 两腿的并集，必须能逐只区分，
    否则「同一只票同时在两榜」时会按错误一侧的判据处理。
    """
    dm = {"600001.SH": _sealed_down_daily()}
    out = _next_bars(dm, ["600001.SH"], _DAY, entry_lag=1, factors_map={},
                     directions={"600001.SH": "down"})
    assert len(out) == 1
    out2 = _next_bars(dm, ["600001.SH"], _DAY, entry_lag=1, factors_map={},
                      directions={"600001.SH": "up"})
    assert out2.empty


def test_market_benchmark_stays_on_the_legacy_path():
    """市场基准**不接**涨跌停闸门——它量的是「市场那天涨了多少」。

    让基准也持有延长，会把一笔长达数月的持仓收益混进一个日度基准里，
    单只 −85% 的极端样本足以把当日基准拉偏 0.5 个百分点。
    闸门要修的是**我们声称要交易的那个篮子**，不是参照系。
    """
    from aplus.backtest import _market_by_day

    bars_data = [(10.0, 10.0, 10.0, 10.0, 1e6, 1e7)] * _N
    bars_data[_N - 2] = (11.0, 11.0, 11.0, 11.0, 5.6e6, 6.4e7)
    df = _seal_at(_daily("600001.SH", bars_data), _N - 2, up=True)
    mkt = _market_by_day({"600001.SH": df}, [_DAY])
    assert mkt[_DAY] == pytest.approx(10.0 / 11.0 - 1.0), \
        "市场基准的取价口径被改动了"


_RAW_ROW_NAMES = ["open", "high", "low", "close", "volume", "amount", "prev_close"]
