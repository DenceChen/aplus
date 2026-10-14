"""成交窗口（D+1 买 / D+2 卖）内的可交易性闸门。

## 这个文件在钉什么

信号日那一侧的可买性由 `filter_universe` 用 `panel.can_buy` 管住了
（`panel.py:551 → predict.py:91-96`）。**成交窗口内部此前一道闸都没有**：
`_next_bars` 判了「两根 bar 停不停牌」，就不管 D+1 是不是一字涨停买不进、
D+2 是不是一字跌停卖不掉。而全平台 `can_sell` 在生产代码里**零调用点**。

买卖两端完全不对称地偏乐观：`close` 从不参与成交价判定，
于是「涨停收盘记成买进了」和「跌停收盘记成卖掉了」同时发生。

## 四条钉死的规则

1. **方向敏感**。`_next_bars` 收到的是涨腿+跌腿混在一起的名单，它分不清
   哪只是真持仓。跌腿是**回避清单**，平台从不持有——对跌腿判 `can_sell`
   等于凭空惩罚一笔没做过的交易，更糟的是：一只在 D+2 一字跌停的股票进跌腿
   只说明**预测对了**，把它当成「卖不掉」删掉，等于删掉最准的样本。
   所以涨跌停规则**只作用于涨腿**（`directions="up"`）。
2. **停牌卖出 → 持有到复牌**，不是删观测。旧代码 `continue` 掉这一行，
   `verify.py` 的 `merge(how="left")` 给出 `actual_pct=NaN`，
   `judge` 记 `no_data`，`rolling_stats` 的 `.mean()` 静默跳过——
   **停牌那笔亏损凭空蒸发了**（`test_suspended_loss_...` 直接量这件事）。
3. **真的再也卖不掉（退市/永久停牌）→ 整笔作废并计数可见**，
   走 `result.attrs["unfillable_execution"]`，与既有的 `unfillable` 同一套机制。
   不计数 = 这笔不成交的观测从统计里彻底消失，且没有任何痕迹。
4. **`_raw_bars` 扩展到 OHLC**，但涨跌停必须用**除权还原后**的前收盘价判，
   否则 10 送 10 会被打成跌停（不复权 −50%）。旧 docstring 用
   「收益走复权价所以不能判涨跌停」这个**错误前提**得到了「不判」这个正确结论。

## 已知且**故意不修**的缺口

ST 的价格笼子是 ±5%，比板块限制窄。`price_limit(code)` 只吃代码前缀，
**判不出 ST**，所以 `can_buy`/`can_sell` 里的涨跌停一律按 10%/20%/30% 算，
ST 的 ±5% 板完全缺席。修它要改 `panel.py`（需要 ST 状态进签名），不在本任务
边界内。见 `test_st_five_percent_band_is_not_applied_and_the_gap_is_real`。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd
import pytest

from aplus.adjust import hfq_close
from aplus.backtest import (EXEC_GATE_REASONS, _bar_flags, _bars_tradable,
                            _exec_stats, _market_by_day, _next_bars, _raw_bars,
                            build_cross_sections, evaluate_weights)
from aplus.panel import can_buy, can_sell, price_limit
from aplus.verify import rolling_stats, verify_predictions

# `_raw_bars(full=True)` 的行序，测试直接按位置取列
_RAW_ROW_NAMES = ["open", "high", "low", "close", "volume", "amount", "prev_close"]

# 窗口下标（12 根 K 线）：
#   D     = 索引 5（信号日）
#   进场  = 索引 6（D+1 收盘买）
#   出场  = 索引 7（D+2 收盘卖）
#   8..11 留给「复牌后持有」的扩展窗口
_N = 12
_DATES = pd.bdate_range("2026-01-01", periods=_N).strftime("%Y-%m-%d").tolist()
_DAY = _DATES[5]
_ENTRY = 6
_EXIT = 7


# ── 合成行情工具 ────────────────────────────────────────────────────────────

def _limit_px(prev_close: float, code: str, up: bool) -> float:
    """交易所口径的涨跌停价（ROUND_HALF_UP 到分；numpy 的 round 是银行家舍入）。"""
    limit = Decimal(str(price_limit(code)))
    band = Decimal(str(prev_close)) * (Decimal(1) + limit if up else Decimal(1) - limit)
    return float(band.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _seal_at(df: pd.DataFrame, i: int, code: str, up: bool = True) -> pd.DataFrame:
    """把第 i 根做成封在限价上的**一字板**：开=高=低=收=涨跌停价。"""
    px = _limit_px(float(df["close"].iloc[i - 1]), code, up)
    df.loc[i, ["open", "high", "low", "close"]] = px
    df.loc[i, ["volume", "amount"]] = [5.6e6, 6.4e7]
    return df


def _suspend_at(df: pd.DataFrame, i: int) -> pd.DataFrame:
    """把第 i 根做成停牌日：volume=0、amount=0、价格冻结在上一根。"""
    px = float(df["close"].iloc[i - 1])
    df.loc[i, ["open", "high", "low", "close"]] = px
    df.loc[i, ["volume", "amount"]] = [0.0, 0.0]
    return df


def _reopen_at(df: pd.DataFrame, i: int, px: float) -> pd.DataFrame:
    """复牌那根：收盘取复牌价 `px`，**刻意做成有价格发现的 bar**。

    不可写成 `open=high=low=close=px`——那是一字板，`can_sell` 的规则 2
    会判成「全天零价格发现、卖不掉」，与「复牌可正常卖出」是两件事。
    """
    prev = float(df["close"].iloc[i - 1])
    df.loc[i, "open"] = prev
    df.loc[i, "high"] = max(prev, px) * 1.002
    df.loc[i, "low"] = min(prev, px) * 0.998
    df.loc[i, "close"] = px
    df.loc[i, ["volume", "amount"]] = [8.8e6, 9.7e7]
    return df


def _daily(code: str, closes, start: str = "2026-01-01") -> pd.DataFrame:
    """合成日线。`closes` 可以只给收盘价（OHLC 齐收盘、正常成交），
    也可以给 `(open, high, low, close, volume, amount)` 的逐根元组。"""
    n = len(closes)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    cols = {"open": [], "high": [], "low": [], "close": [], "volume": [], "amount": []}
    for item in closes:
        if isinstance(item, tuple):
            o, h, low, c, v, amt = item
        else:
            o = h = low = c = float(item)
            v, amt = 1e6, 1e7
        cols["open"].append(o); cols["high"].append(h); cols["low"].append(low)
        cols["close"].append(c); cols["volume"].append(v); cols["amount"].append(amt)
    return pd.DataFrame({"date": dates, "code": [code] * n, **cols,
                         "tradestatus": [1] * n, "isST": [0] * n, "turn": [1.0] * n})


def _flat(code: str, prices=None) -> pd.DataFrame:
    """默认样本：12 根 10.00 的平价 bar（各根独立、不触发任何涨跌停）。"""
    return _daily(code, list(prices) if prices is not None else [10.0] * _N)


def _run(daily_map: dict, codes, directions=None, exec_stats=None):
    """走一次 `_next_bars`，返回 `(bars, exec_stats)`。"""
    stats = exec_stats if exec_stats is not None else _exec_stats()
    bars = _next_bars(daily_map, list(codes), _DAY, entry_lag=1,
                      factors_map={}, directions=directions, exec_stats=stats)
    return bars, stats


# ════════════════════════════════════════════════════════════════════════════
# A. 方向敏感：涨跌停规则只作用于涨腿（真持仓）
# ════════════════════════════════════════════════════════════════════════════

def test_down_leg_keeps_a_one_word_limit_down_sample():
    """跌腿样本在 D+2 一字跌停 → **必须保留**。

    它进跌腿只说明**预测对了**。当「卖不掉」把它删掉，等于系统性删掉最准的
    样本——多空价差会被算得比实际更小，而方向恰好与「闸门要更严」相反。
    """
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)      # 10.00 → 9.00 一字跌停
    bars, _ = _run({code: df}, [code], directions="down")
    assert len(bars) == 1, "跌腿的一字跌停样本被当成「卖不掉」删掉了"
    assert float(bars["close"].iloc[0]) == pytest.approx(9.0)


def test_down_leg_result_is_judged_on_its_real_return():
    """保留下来的跌腿样本必须按**真实收益**判定，而不是被悄悄剔除。"""
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)
    bars, _ = _run({code: df}, [code], directions="down")
    preds = pd.DataFrame([{"code": code, "direction": "down"}])
    res = verify_predictions(preds, bars)
    assert res["actual_pct"].iloc[0] == pytest.approx(-0.10)
    assert res["result"].iloc[0] == "hit", (
        "跌 10% 的跌腿样本必须判 hit——它是平台最想避开的正确预测")
    assert rolling_stats(res)["avg_down_return"] == pytest.approx(-0.10)


def test_up_leg_drops_a_one_word_limit_down_exit():
    """同样的 K 线，涨腿**不成交**：D+2 一字跌停 = 卖不掉。

    这才是闸门要拦的东西：持仓在跌停板上变现不出来，回测却按跌停价
    记了一笔「成功卖出」。
    """
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)
    bars, stats = _run({code: df}, [code], directions="up")
    assert bars.empty, "涨腿持仓在 D+2 一字跌停仍被记成卖出——闸门没生效"
    assert stats["sell_void"] == 1


def test_up_leg_limit_up_exit_is_still_sellable():
    """D+2 **涨停收盘**（有价格发现，非一字板）卖得掉——买卖两端判据不能对称照抄。

    `can_sell` 拒的是「跌停收盘」和「一字板」，不拒「涨停收盘」：早盘持筹者
    挂卖单在涨停板上照样成交。把 `can_buy` 的规则整套搬到 `can_sell` 会误杀
    所有涨停票的变现能力——那正是策略赚钱时最常见的持仓状态。
    """
    code = "600001.SH"
    df = _flat(code)
    df.loc[_EXIT, ["open", "high", "low", "close"]] = [10.5, 11.0, 10.4, 11.0]
    df.loc[_EXIT, ["volume", "amount"]] = [8.8e6, 9.7e7]
    assert can_sell(code, 10.5, 11.0, 10.4, 11.0, 8.8e6, 9.7e7, 10.0)
    bars, stats = _run({code: df}, [code], directions="up")
    assert len(bars) == 1, "涨停收盘被当成卖不掉——买卖两端判据被对称照抄了"
    assert float(bars["close"].iloc[0]) == pytest.approx(11.0)
    assert stats["sell_void"] == 0


def test_up_leg_entry_on_a_sealed_limit_up_is_not_bought():
    """D+1 一字涨停 → **买不进**，整笔不成交。"""
    code = "600001.SH"
    df = _seal_at(_flat(code), _ENTRY, code, up=True)       # 进场那根封在 11.00
    bars, stats = _run({code: df}, [code], directions="up")
    assert bars.empty, "D+1 一字涨停仍被记成买入——闸门没生效"
    assert stats["buy_unfillable"] == 1


def test_down_leg_ignores_a_sealed_limit_up_entry():
    """跌腿不判涨停：跌腿是回避清单，涨停只说明它涨了——正是该回避的。"""
    code = "600001.SH"
    df = _seal_at(_flat(code), _ENTRY, code, up=True)
    bars, stats = _run({code: df}, [code], directions="down")
    assert len(bars) == 1, "跌腿样本因涨停被删——那是在删一个正确的「别碰」"
    assert stats["buy_unfillable"] == 0


# ════════════════════════════════════════════════════════════════════════════
# B. 停牌卖出 → 持有到复牌，而不是删掉这笔亏损
# ════════════════════════════════════════════════════════════════════════════

def test_suspended_exit_is_held_until_the_first_tradable_bar():
    """D+2 停牌、D+3 复牌 → 成交价取**复牌那根**，不是删掉这笔。"""
    code = "600001.SH"
    df = _flat(code)
    df = _suspend_at(df, _EXIT)
    df = _reopen_at(df, _ENTRY + 2, 10.5)
    bars, stats = _run({code: df}, [code], directions="up")
    assert len(bars) == 1, "复牌可卖却整笔作废——等于凭空丢一笔亏损"
    assert float(bars["close"].iloc[0]) == pytest.approx(10.5)
    assert float(bars["prev_close"].iloc[0]) == pytest.approx(10.0)
    assert stats["sell_extended"] == 1
    assert stats["sell_void"] == 0


def test_held_until_resumption_really_reprices_at_the_resumption_bar():
    """停牌期间价格冻结，复牌跳空必须被真实计入——这正是被吞掉的那笔亏损。"""
    code = "600001.SH"
    df = _flat(code)
    df = _suspend_at(df, _EXIT)
    # 复牌 −8%：落在主板 ±10% 价格笼子**内**，所以是可成交的正常 bar。
    # （−30% 会被 `can_sell` 判成「收在跌停价以下」，那是另一个问题。）
    df = _reopen_at(df, _ENTRY + 2, 9.2)
    bars, _ = _run({code: df}, [code], directions="up")
    assert float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1.0 \
        == pytest.approx(-0.08)


def test_suspended_loss_no_longer_vanishes_from_the_basket():
    """把「亏损凭空蒸发」整条链路量出来。

    旧路径：停牌那行被 `continue` 掉 → `merge(how="left")` → `actual_pct=NaN`
    → `judge` 记 `no_data` → `rolling_stats` 的 `.mean()` 静默跳过。
    篮子里一只股票亏 −8%，`avg_up_return` 却报 **0.0**。
    """
    held, halted = "600001.SH", "600002.SH"
    dm = {held: _flat(held)}
    df = _flat(halted)
    df = _suspend_at(df, _EXIT)
    df = _reopen_at(df, _ENTRY + 2, 9.2)
    dm[halted] = df
    bars, _ = _run(dm, [held, halted], directions="up")
    preds = pd.DataFrame({"code": [held, halted], "direction": ["up", "up"]})
    stats = rolling_stats(verify_predictions(preds, bars))

    assert stats["up_basket_n"] == 2, "停牌样本仍在被丢弃"
    assert stats["avg_up_return"] == pytest.approx(-0.04), (
        f"avg_up_return={stats['avg_up_return']:+.4%}：−8% 那笔没有进入篮子均值")

    # 变异验证：旧算法把停牌那行 `continue` 掉，**确实**给出 0.0。
    # 两者之差就是这条测试的鉴别力。
    legacy = rolling_stats(verify_predictions(preds, bars[bars["code"] == held]))
    assert legacy["up_basket_n"] == 1
    assert legacy["avg_up_return"] == 0.0, (
        "前提变了：这条测试已不再复现「亏损凭空蒸发」，测不出东西")


def test_permanently_unfillable_exit_is_counted_and_visible():
    """复牌后再无任何可成交 bar（退市 / 永久停牌）→ 整笔作废**且必须计数**。

    亏损无上界（连一个负号都没记下）。不计数就等于从统计里彻底消失。
    """
    code = "600001.SH"
    df = _flat(code)
    for i in (_EXIT, _ENTRY + 2, _ENTRY + 3, _ENTRY + 4, _ENTRY + 5):
        df = _suspend_at(df, i)
    bars, stats = _run({code: df}, [code], directions="up")
    assert bars.empty
    assert stats["sell_void"] == 1, "永远卖不掉的持仓没有进计数——这笔观测凭空消失"
    assert stats["sell_extended"] == 0


def test_execution_stats_are_per_reason_not_a_single_bucket():
    """拦截必须**分原因**计数：买不进 / 延期成交 / 作废是三件不同的事。

    合成后只知道「少了 N 条」时，无法判断是闸门太严还是数据出了问题。
    """
    assert set(_exec_stats()) == set(EXEC_GATE_REASONS) | {"codes"}

    buy = "600001.SH"
    held = "600002.SH"
    voided = "600003.SH"
    dm = {buy: _seal_at(_flat(buy), _ENTRY, buy, up=True),
          held: _flat(held)}
    df = _flat(voided)
    for i in (_EXIT, _ENTRY + 2, _ENTRY + 3, _ENTRY + 4, _ENTRY + 5):
        df = _suspend_at(df, i)
    dm[voided] = df

    _, stats = _run(dm, list(dm), directions="up", exec_stats=_exec_stats())
    assert stats["buy_unfillable"] == 1
    assert stats["sell_void"] == 1
    assert stats["sell_extended"] == 0
    assert stats["price_implausible"] == 0
    assert set(stats["codes"]) == {buy, voided}


def test_suspended_entry_is_dropped_not_held():
    """停牌在**进场**那侧：买不进，没有「等复牌再买」这回事。"""
    code = "600001.SH"
    df = _flat(code)
    df = _suspend_at(df, _ENTRY)
    df = _suspend_at(df, _ENTRY + 1)
    bars, stats = _run({code: df}, [code], directions="up")
    assert bars.empty
    assert stats["buy_unfillable"] == 1
    assert stats["sell_extended"] == 0


def test_down_leg_suspension_keeps_the_existing_behaviour():
    """跌腿维持「只判停牌、不延期」的既有行为。"""
    code = "600001.SH"
    df = _flat(code)
    df = _suspend_at(df, _EXIT)
    df = _reopen_at(df, _ENTRY + 2, 7.0)
    bars, stats = _run({code: df}, [code], directions="down")
    assert bars.empty, "跌腿不应新增持有语义"
    assert stats["sell_extended"] == 0
    assert stats["sell_void"] == 0


def test_long_hold_beyond_forty_percent_is_not_discarded_as_a_dirty_bar():
    """延长持有跨过 ±40% **总**收益时必须保留 —— 逐步判，不是拿总收益判。

    `MAX_PLAUSIBLE_RETURN` 判的是**单根 bar 的脏价格**。把它套在延长持有的
    总收益上，等于把「单日 ±40%」误当成「任意持有期 ±40%」。而本函数让卖出
    延到复牌后的第一根可交易 bar，中间可以夹着一串跌停板——每一步都合法，
    累计却轻松越过 ±40%。真按总收益判，被扔掉的恰恰是**退市风险股**那批
    最惨的样本，正是本项修复要消灭的「亏损凭空蒸发」。

    构造：10.00 进场 → 停牌 1 天 → 连吃 6 个一字跌停 → 第 8 天恢复交易收 5.00。
    总收益 −50%，逐步最大 −10%（一字跌停的真实幅度），没有一根是脏价格。
    """
    code = "600001.SH"
    n = 15
    df = _daily(code, [10.0] * n)
    df = _suspend_at(df, _EXIT)                      # index 7 停牌
    for i in range(8, 14):                           # 6 个一字跌停
        df = _seal_at(df, i, code, up=False)
    df = _reopen_at(df, 14, 5.0)                     # 恢复交易，但已腰斩

    bars, stats = _run({code: df}, [code], directions="up")
    assert stats["sell_extended"] == 1
    assert stats["price_implausible"] == 0
    assert len(bars) == 1, (
        "逐步都合法的 −50% 延长持有被当成脏价格扔掉——最惨的样本又凭空消失了")
    ret = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1.0
    assert ret < -0.40, f"样本没造出来：实际收益只有 {ret:.2%}"


def test_a_single_dirty_bar_still_voids_an_extended_hold():
    """逐日步判**不能**放松成「延长持有一律放行」：脏 bar 照样要拦。

    复牌后第二天单根 −96%（复牌跳空被源站记成价格跳空，见
    `tests/test_implausible_return.py` 的 600766.SH）——这一根不真实，
    整笔成交不可信，作废并计数。
    """
    code = "600001.SH"
    closes = [10.0] * 7 + [2.0] + [2.0] * 4
    df = _daily(code, closes)
    df = _suspend_at(df, _EXIT)                 # index 7 停牌
    bars, stats = _run({code: df}, [code], directions="up")
    assert bars.empty, "延长持有里的一根脏 bar 没被拦下"
    assert stats["price_implausible"] == 1
    assert stats["sell_extended"] == 1          # 延后确实发生了，只是作废


# ════════════════════════════════════════════════════════════════════════════
# C. `_raw_bars` / `_bar_flags`：闸门需要哪几列，就补哪几列
# ════════════════════════════════════════════════════════════════════════════

def test_raw_bars_default_is_still_two_rows():
    """默认签名必须**逐位保持** `(volume, amount)` 两行。

    `aplus/factor_ic.py` 直接 `from aplus.backtest import _raw_bars` 并调用
    `_raw_bars(s)`；改了默认形状就把 IC 诊断的快路径一起改了。
    """
    raw = _raw_bars(_flat("600001.SH"))
    assert raw.shape == (2, _N)


def test_raw_bars_full_carries_every_column_the_gate_needs():
    """`can_buy`/`can_sell` 的签名是 `(o, h, l, c, volume, amount, prev_close)`，
    少一列就只能降级——所以 full 模式必须是完整 7 列。"""
    raw = _raw_bars(_flat("600001.SH"), full=True)
    assert raw.shape == (len(_RAW_ROW_NAMES), _N)


def test_prev_close_comes_from_the_stocks_own_previous_bar():
    """前收盘取**该股自身时间轴上前一根**，绝不跨股票取。"""
    raw = _raw_bars(_daily("600001.SH", [10.0, 12.0, 9.0]), full=True)
    prev = raw[_RAW_ROW_NAMES.index("prev_close")]
    assert not np.isfinite(prev[0]), "首根没有前收盘，不能默认成自己"
    assert prev[1] == pytest.approx(10.0)
    assert prev[2] == pytest.approx(12.0)


def test_bar_flags_match_the_scalar_predicates():
    """向量路径与逐根标量路径必须**逐位一致**——否则横截面过滤和回测两套口径。"""
    code = "300001.SZ"                                    # 创业板 20%
    df = _flat(code, [10.0, 11.0, 12.2, 9.8, 13.16, 10.5,
                      10.5, 12.6, 10.0, 10.1, 10.2, 10.3])
    df = _seal_at(df, 4, code, up=True)
    df = _seal_at(df, 7, code, up=False)
    df = _suspend_at(df, 9)
    raw = _raw_bars(df, full=True)
    buyable, sellable = _bar_flags(code, df, raw)
    assert buyable is not None and sellable is not None

    for i in range(len(df)):
        px = float(df["close"].iloc[i])
        o, h, lo = (float(df[c].iloc[i]) for c in ("open", "high", "low"))
        v, amt = float(df["volume"].iloc[i]), float(df["amount"].iloc[i])
        prev = float(df["close"].iloc[i - 1]) if i else np.nan
        assert bool(buyable[i]) == can_buy(code, o, h, lo, px, v, amt, prev), \
            f"第 {i} 根的买得进/买不进判断与标量口径不一致"
        assert bool(sellable[i]) == can_sell(code, o, h, lo, px, v, amt, prev), \
            f"第 {i} 根的卖得掉/卖不掉判断与标量口径不一致"


def test_bar_flags_fall_back_to_suspension_only_when_ohlc_is_missing():
    """OHLC 缺列 → `None`，由调用方退回「只判停牌」，**不是**默认放行涨跌停。

    没有 `high`/`low` 就判不出一字板；此时若假装「买得进」，等于把闸门
    悄悄关掉还不留痕迹。
    """
    code = "600001.SH"
    df = _flat(code)
    df.loc[_ENTRY, ["open", "high", "low", "close"]] = 11.0     # 只有收盘在涨停价
    df = df.drop(columns=["high", "low"])
    assert _bar_flags(code, df, _raw_bars(df)) is None


def test_bar_flags_stay_none_when_the_frame_is_empty():
    assert _bar_flags("600001.SH", _flat("600001.SH").iloc[0:0],
                      np.zeros((2, 0))) is None


def test_bars_tradable_contract_is_unchanged_for_factor_ic():
    """`_bars_tradable(raw, entry, exit_i)` 的签名与语义是 `factor_ic` 的公共契约。"""
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)            # 一字跌停：不是停牌
    assert _bars_tradable(_raw_bars(df), _ENTRY, _EXIT) is True
    df2 = _suspend_at(_flat(code), _EXIT)
    assert _bars_tradable(_raw_bars(df2), _ENTRY, _EXIT) is False
    assert _bars_tradable(np.zeros((2, 0)), 0, 0) is True  # 越界 → 既有行为


# ════════════════════════════════════════════════════════════════════════════
# D. 除权日不得被误判成涨跌停
# ════════════════════════════════════════════════════════════════════════════

def test_ex_rights_day_is_not_mislabelled_as_a_limit_move():
    """10 送 10 的除权日必须照常成交。

    不复权口径下 5.05 → 5.00 看着像「差一分钱没到跌停」，加上旧 docstring
    举的现金分红例（20 → 19 恰好 = ST 的 −5%），**任何**在不复权价上做的
    涨跌停判定都会把除权日打成不可成交。
    """
    code = "600001.SH"
    df = _daily(code, [10.0, 10.1, 5.0, 5.1])
    dates = df["date"].tolist()
    # 因子表：上市基准 1.0（早于日线起点，避免 attach_factor 判为基准未知）
    # + 除权日跳到 2.0（10 送 10）
    events = pd.DataFrame({"date": [dates[0], dates[2]], "adjust_factor": [1.0, 2.0]})
    assert np.allclose(hfq_close(df, events).to_numpy(float),
                       [10.0, 10.1, 10.0, 10.2])

    stats = _exec_stats()
    bars = _next_bars({code: df}, [code], dates[1], entry_lag=1,
                      factors_map={code: events}, directions="up",
                      exec_stats=stats)
    assert len(bars) == 1, "除权除息日被判成不可成交——用不复权价判涨跌停的错误"
    assert stats["buy_unfillable"] == 0
    assert stats["sell_void"] == 0
    # 收益走**后复权**：10.1 → 10.0 买、10.0 → 10.2 卖 = +2%
    assert (float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1.0) \
        == pytest.approx(0.02)


# ════════════════════════════════════════════════════════════════════════════
# E. 已知缺口：ST 的 ±5% 价格笼子没接上（**故意不修**，见文件头）
# ════════════════════════════════════════════════════════════════════════════

def test_st_five_percent_band_is_not_applied_and_the_gap_is_real():
    """把 ST 缺口**量出来**并钉住现状：`price_limit(code)` 只吃代码前缀。

    缺口**不在**一字板那一侧——`panel._limit_close_flags` 的 `on_band`
    额外认了 ±5%，所以封在 ±5% 的**一字** ST 板会被 `sealed` 拦下（买卖两端）。
    真正漏的是 `at_limit_up` / `at_limit_dn` 这两条：它们只按
    10%/20%/30% 判，于是一根**收盘在 ±5%、但盘中有价格发现**的 ST bar
    既不是一字板、也不算涨停收盘 → `can_buy` / `can_sell` 直接放行。

    真实场景：一只主板票在 D+1 收盘封在 +5.00（涨停价），回测照常记成「买进了」。
    修它要改 `aplus/panel.py`（ST 状态得进 `price_limit` 的签名），
    不在本任务边界内——但**不许假装这个缺口不存在**。
    """
    assert price_limit("600001.SH") == 0.10          # 主板，看不出是不是 ST

    code = "600001.SH"
    df = _flat(code)
    # 收盘 10.50 = 主板 10% 涨停价 11.00 之下、ST +5% 涨停价之上；
    # 但开≠高≠低（有价格发现），所以不是一字板。
    df.loc[_ENTRY, ["open", "high", "low", "close"]] = [10.20, 10.55, 10.15, 10.50]
    df.loc[_ENTRY, ["volume", "amount"]] = [5.6e6, 6.4e7]
    assert can_buy(code, 10.20, 10.55, 10.15, 10.50, 5.6e6, 6.4e7, 10.00)

    bars, _ = _run({code: df}, [code], directions="up")
    # 现状：放行。若哪天 panel.py 接上了 ST，本测试会失败——那正是应该失败
    # 的时候（失败信息即「缺口已修」），届时本节的说明也要同步更新。
    assert len(bars) == 1, (
        "ST 的 ±5% 已被识别——panel.py 那边补上了 ST 状态，"
        "本文件的缺口说明与断言都应同步更新")


# ════════════════════════════════════════════════════════════════════════════
# F. 回归地面：不传方向 / 逐股方向 / 市场基准 / 生产接线
# ════════════════════════════════════════════════════════════════════════════

def test_default_no_direction_keeps_the_legacy_suspension_only_gate():
    """不传 `directions` → 与改动前**逐位相同**的「只判停牌」行为。

    `aplus/factor_ic.py::test_forward_returns_matches_next_bars` 把
    `forward_returns` 与 `_next_bars`（默认参数）钉成逐位相等；这条默认值
    一动，IC 就在量另一件事，而数字看上去照样正常。
    """
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)
    bars, _ = _run({code: df}, [code])
    assert len(bars) == 1, "默认路径不再等于旧的停牌闸门"
    assert float(bars["close"].iloc[0]) == pytest.approx(9.0)


def test_execution_window_directions_are_per_code():
    """涨腿+跌腿混传时必须**逐股**判定，不能整批套一个口径。"""
    up_code, down_code = "600001.SH", "600002.SH"
    up_df = _seal_at(_flat(up_code), _EXIT, up_code, up=False)
    down_df = _seal_at(_flat(down_code), _EXIT, down_code, up=False)
    bars, _ = _run({up_code: up_df, down_code: down_df},
                   [up_code, down_code],
                   directions={up_code: "up", down_code: "down"})
    assert list(bars["code"]) == [down_code], "整批套了一个口径"


def test_market_benchmark_stays_on_the_legacy_path():
    """市场基准**不走**涨腿闸门。

    基准量的是「市场当天怎么走」，不是「我们那 10 只能不能成交」。
    一旦给它也套上持有延长（实测最长 731 根 ≈ 34 个月），超额就变成
    「市场 1 天 vs 我们 34 个月」，没有意义。
    """
    code = "600001.SH"
    df = _seal_at(_flat(code), _EXIT, code, up=False)     # 10.00 → 9.00 一字跌停
    market = _market_by_day({code: df}, _DATES)
    assert market[_DAY] == pytest.approx(-0.10), "市场基准被闸门改动了"
    # 同一根 K 线在涨腿口径下确实不可成交——证明基准走的是另一条路径
    bars, _ = _run({code: df}, [code], directions="up")
    assert bars.empty


# ── 生产接线：`evaluate_weights` 必须真的把闸门打开 ─────────────────────────

def _seed_and_run(tmp_path, execution_gate: bool):
    """6 只股票，drift 最高的三只在最后三根 K 线上封成**一字涨停**。

    信号日取索引 86：其进场 bar（87）正好是封板那根。封板本身发生在信号日
    **之后**，所以信号日这三只**在池子里**（`filter_universe` 只判信号日），
    进场时才被成交闸门拦下——这正是「买得到 ≠ 买得进」最容易被违反的窗口。

    窗口起点取 `dates[60]`：`filter_universe` 要求 `listed_days > 60`，
    从头跑会得到一个空池子（测不到任何东西）。
    """
    from aplus.store import LocalStore

    n_bars = 90
    store = LocalStore(tmp_path)
    for i in range(6):
        code = f"{600000 + i + 1:06d}.SH"
        dates = pd.bdate_range("2026-01-01", periods=n_bars).strftime("%Y-%m-%d")
        close = 10.0 * (1 + 0.02 - i * 0.002) ** np.arange(n_bars)
        df = pd.DataFrame({
            "date": dates, "code": [code] * n_bars, "open": close,
            "high": close * 1.01, "low": close * 0.99, "close": close,
            "volume": np.full(n_bars, 1e6), "amount": np.full(n_bars, 1e7),
            "tradestatus": [1] * n_bars, "isST": [0] * n_bars, "turn": [1.0] * n_bars,
        })
        if i < 3:                                   # 动量最高的三只 = 涨篮子
            for k in (n_bars - 3, n_bars - 2, n_bars - 1):
                df = _seal_at(df, k, code, up=True)
        store.upsert_daily(code, df)

    dates = store.read_daily("600001.SH")["date"].tolist()
    sections = build_cross_sections(store, dates[60], dates[-1])
    return evaluate_weights(sections, weights={"mom20": 1.0}, top_n=3, bottom_n=3,
                            execution_gate=execution_gate), dates


def test_evaluate_weights_wires_the_gate_into_the_up_leg(tmp_path):
    """接线测试：`execution_gate=True` 必须真的拦住「D+1 一字涨停」的买进。

    断言的不是具体股票，而是**不变量**：闸门开着时，涨腿里不存在
    「收益率是涨停级别」的成交——那笔交易在现实中根本不存在。

    注意比的是**有成交的行**（`actual_pct` 非空），不是行数：
    `verify_predictions` 是 `merge(how="left")`，被闸门拦下的代码仍以
    `no_data` 留在结果里（对「没买进」是正确的语义），但 `rolling_stats`
    的 `.mean()` 会跳过它们——所以只有非空行才代表真实发生的交易。
    """
    on, dates = _seed_and_run(tmp_path / "on", True)
    off, _ = _seed_and_run(tmp_path / "off", False)
    d = dates[86]                                       # 进场 bar = 87（封板）

    assert on.attrs["unfillable_execution"] > 0, (
        "闸门开着却一次都没触发——`evaluate_weights` 没把 directions 传下去")
    assert on.attrs["unfillable_execution_by"]["buy_unfillable"] > 0

    filled = lambda f: f[(f["direction"] == "up") & (f["pred_date"] == d)
                         & f["actual_pct"].notna()]
    up_on, up_off = filled(on), filled(off)
    assert len(up_off) >= 1, "前提变了：旧路径在这一天没有涨腿成交，测不出闸门"
    assert float(up_off["actual_pct"].max()) > 0.05, (
        "前提变了：旧路径在这一天没有涨停级别的买进，测不出闸门")

    assert len(up_on) < len(up_off), "闸门开着却没少任何一笔涨腿成交"
    assert not (up_on["actual_pct"] > 0.05).any(), (
        "闸门开着，仍有涨停级别的买进——买不进的那笔没被拦下")