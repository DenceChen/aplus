"""可交易性（买不进 / 卖不掉）测试。TDD RED 先行。

## 这组测试在钉什么

回测口径是 **D 收盘出信号 → D+1 收盘买 → D+2 收盘卖**（`ENTRY_LAG=1`），
但「这一笔能不能成交」此前**全平台没有任何一处代码处理过**：

- 一字涨停：`open == high == low == close`，开盘即封在涨停价、全天没打开，
  收盘价就是涨停价。收盘挂单排不进去 → **买不进**。
- 停牌：`volume == 0`（且 `amount == 0`）→ 根本没有价格发现 → 买不进。
- 一字跌停的对称问题：D+2 收盘卖，若 D+2 一字跌停，**你也卖不掉**。

2026-10-11 实测（快照 `/tmp/aplus_snap_1610`，信号日 2026-04-09~2026-09-30，
mom20 top-10 涨篮子）：篮子里「信号日一字板」的样本占 1.83%，而它们的次日
收益均值 **+0.82%**、其余样本只有 **+0.11%**——**动量因子的表观超额里有
一块来自根本成交不了的交易**。把它们留在篮子里，回测系统性偏乐观。

## 为什么不能用 `tradestatus` / `turn` 判停牌

抽样 283 只股票的**最后一根 K 线**：`turn` 缺失 63.6%、`isST` / `tradestatus`
缺失 59.4%、`amount` 缺失 59.4% 且另有 8.8% 整列不存在。
**三分之二的股票，最新一根 K 线的元数据是空的**——用 `tradestatus == 0`
判停牌会漏掉全部。所以停牌必须用 `volume == 0` 判，OHLC 自足。
同理，`amount` 缺失**不得**当成「成交额为 0」，否则会误杀三分之二的股票。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.panel import (
    LIMIT_BEIJING,
    LIMIT_GROWTH,
    LIMIT_MAIN,
    PRICE_TOL,
    can_buy,
    can_sell,
    price_limit,
)

# 真实样本（快照数据，2026-10-08）：600825.SH 主板一字板 10.35 → 11.39
LIMIT_UP_600825 = (11.39, 11.39, 11.39, 11.39, 5.6e6, 6.4e7, 10.35)
# 科创板 688137.SH：20% 涨跌幅，主板的 10% 阈值在这里判不出来
LIMIT_UP_688 = (120.0, 120.0, 120.0, 120.0, 1e6, 1.2e8, 100.0)


# ── 1. 停牌 ────────────────────────────────────────────────────────────────

def test_suspended_volume_zero_cannot_buy():
    """停牌日 volume == 0：没有任何价格发现，这笔买不进。"""
    assert not can_buy("600001.SH", 10.0, 10.0, 10.0, 10.0,
                       volume=0.0, amount=0.0, prev_close=10.0)


def test_suspended_amount_zero_cannot_buy():
    """amount == 0 是停牌的第二个独立信号（源站有 volume 却漏 amount 的情形）。"""
    assert not can_buy("600001.SH", 10.0, 10.0, 10.0, 10.0,
                       volume=0.0, amount=0.0, prev_close=10.0)
    assert not can_buy("600001.SH", 10.0, 10.0, 10.0, 10.0,
                       volume=5e5, amount=0.0, prev_close=10.0)


def test_missing_amount_is_not_treated_as_suspended():
    """**最容易写错的一条**。

    实测 283 只股票的末根 K 线有 59.4% 的 `amount` 是 NaN、另有 8.8% 整列缺失。
    若把「amount 缺失」当成「成交额为 0」，等于把三分之二的股票全部误杀，
    横截面直接塌掉。缺失 = 不知道，**不等于** 0。
    """
    assert can_buy("600001.SH", 10.0, 10.1, 9.9, 10.05, volume=5e5,
                   amount=np.nan, prev_close=10.0)
    assert can_buy("600001.SH", 10.0, 10.1, 9.9, 10.05, volume=5e5,
                   amount=None, prev_close=10.0)


def test_suspended_stock_cannot_be_sold_either():
    """停牌同样卖不掉——只能等复牌，而复牌跳空（实测次日收益均值 −1.02%）。"""
    assert not can_sell("600001.SH", 10.0, 10.0, 10.0, 10.0,
                        volume=0.0, amount=0.0, prev_close=10.0)


# ── 2. 一字板 ──────────────────────────────────────────────────────────────

def test_one_word_board_cannot_buy():
    """一字板 `open == high == low == close`：全天零价格发现，收盘价不是可成交价。"""
    assert not can_buy("600825.SH", *LIMIT_UP_600825[:5],
                       amount=LIMIT_UP_600825[5], prev_close=LIMIT_UP_600825[6])


def test_one_word_board_cannot_sell_either():
    """一字板对**卖**端同样不可成交（无论封在涨停还是跌停）。

    「开==高==低==收」意味着全天没有一笔价格发现，这个收盘价不是任何一笔
    真实成交价，拿它记一笔买入或卖出都是编的。
    """
    assert not can_sell("600825.SH", *LIMIT_UP_600825[:5],
                        amount=LIMIT_UP_600825[5], prev_close=LIMIT_UP_600825[6])
    assert not can_sell("600001.SH", 9.0, 9.0, 9.0, 9.0,
                        volume=1e6, amount=9e6, prev_close=10.0)


def test_board_with_intraday_range_is_not_one_word():
    """盘中打开过的板不是一字板：全天有价格发现，收盘价是可成交价。"""
    assert can_buy("600825.SH", 10.80, 11.39, 10.60, 11.30,
                   volume=5.6e6, amount=6.4e7, prev_close=10.35)


# ── 3. 涨停收盘（买不进）────────────────────────────────────────────────────

def test_limit_up_close_main_board_cannot_buy():
    """真实样本 `600825.SH` 2026-10-08：prev 10.35 → 涨停价 round(10.35×1.1,2)=11.39。

    源站 pctChg 记 10.0483%（它按未取整价算），交易所口径就是 11.39。
    """
    assert price_limit("600825.SH") == LIMIT_MAIN
    assert not can_buy("600825.SH", 10.90, 11.39, 10.85, 11.39,
                       volume=5.6e6, amount=6.4e7, prev_close=10.35)


def test_limit_up_close_growth_board_cannot_buy():
    """创业板 / 科创板涨跌幅 20%：主板的 10% 阈值在这里**判不出来**，必须按板块取。"""
    assert price_limit("300001.SZ") == LIMIT_GROWTH
    assert price_limit("688137.SH") == LIMIT_GROWTH
    assert not can_buy("688137.SH", *LIMIT_UP_688[:5],
                       amount=LIMIT_UP_688[5], prev_close=LIMIT_UP_688[6])
    # 同一只股票只涨 10% → 不构成涨停，买得进
    assert can_buy("688137.SH", 105.0, 110.0, 104.0, 110.0,
                   volume=1e6, amount=1.1e8, prev_close=100.0)


def test_limit_up_close_beijing_board_cannot_buy():
    """北交所 ±30%。"""
    assert price_limit("830799.BJ") == LIMIT_BEIJING
    assert price_limit("430047.BJ") == LIMIT_BEIJING
    assert not can_buy("830799.BJ", 125.0, 130.0, 124.0, 130.0,
                       volume=1e6, amount=1.3e8, prev_close=100.0)


def test_limit_close_below_limit_is_fillable():
    """只差一分钱不到涨停价 → 买得进。容差不得大到把「逼近涨停」也一锅端。"""
    assert can_buy("600001.SH", 10.50, 10.99, 10.40, 10.99,
                   volume=1e6, amount=1.1e7, prev_close=10.00)


def test_limit_price_tolerance_is_exactly_half_a_cent():
    """涨跌停判定用**未取整的** `prev×(1±limit)` 加半分钱容差，不复刻交易所取整。

    交易所涨停价 = `round(prev×(1+limit), 2)`，它与未取整值的差**恒不超过半分钱**，
    于是「收盘 == 涨停价」可精确写成 `|close − prev×(1+limit)| <= 0.005`。

    为什么不用 `round()`：numpy 是**银行家舍入**，`round(11.385, 2) = 11.38`，
    而 600825.SH 2026-10-08 的真实涨停价是 **11.39**——差一分钱，
    而那一分钱正好压在判据的边界上。

    依据：半分钱是**能承受的最大值**。再大就会把「差一分钱没到涨停价」
    （**买得进**）也误杀——那是把能买的票剔掉，让过滤器自己变成新的偏差源。
    """
    assert PRICE_TOL == 0.005
    # 真实样本：prev 10.35 → 涨停价 11.39，|11.39 − 11.385| 恰好半分钱 → 必须命中。
    # 差值在 float64 上是 0.005000000000000782（比半分钱大 8e-16），
    # 所以实现里必须带一点浮点松弛，否则最关键的边界样本会被漏判。
    assert abs(11.39 - 10.35 * 1.1) <= PRICE_TOL + 1e-9
    assert not can_buy("600825.SH", 11.0, 11.39, 11.0, 11.39,
                       volume=5.6e6, amount=6.4e7, prev_close=10.35)
    # 边界：低于限价半分钱（仍在容差内）→ 判为涨停
    assert not can_buy("600001.SH", 10.5, 11.0, 10.4, 11.0 - PRICE_TOL / 2,
                       volume=1e6, amount=1.1e7, prev_close=10.0)
    # 再低半分钱（恰好一个容差之外）→ 不是涨停，买得进
    assert can_buy("600001.SH", 10.5, 10.99, 10.4, 11.0 - PRICE_TOL * 2,
                   volume=1e6, amount=1.1e7, prev_close=10.0)


def test_ex_rights_day_is_not_treated_as_untradable():
    """**不复权价上的巨幅跳空不是「买不进」的证据**。

    10 送 10 在不复权价上是 −50%，而那一天照常交易。若在 `can_buy` 里加一条
    「原始涨跌幅 > ±40% → 不可买」，会把每一个合法的除权日打成不可成交——
    实测 2623 只股票受影响。±40% 那道闸门已经在**后复权价**上（`_factor_series`
    与 `_next_bars`），口径正确，本函数不重复判。
    """
    # 10 送 10 除权日：不复权价 −50%，但当天照常交易 → 买得进
    assert can_buy("600001.SH", 10.0, 10.0, 5.0, 5.0,
                   volume=1e6, amount=1e7, prev_close=10.0)
    assert can_buy("600001.SH", 5.0, 5.2, 4.9, 5.1, volume=1e6,
                   amount=5.1e6, prev_close=5.0)
    # 脏价格（600766.SH 单日 −96.43%）在本函数里同样判为不可成交——
    # 结论对，但**理由**不同：它不是「封在跌停价」，而是收盘价远低于跌停价。
    # 真正拦它的闸门在 `_next_bars` 的后复权收益率上（test_implausible_return）。


# ── 4. 跌停对卖出端的对称问题 ──────────────────────────────────────────────

def test_limit_down_close_cannot_sell():
    """D+2 收盘卖：若 D+2 一字跌停，你卖不掉（回测却会按跌停价记一笔成交）。

    与买入端**对称**——只堵买不堵卖，等于把不可执行的平仓记成已实现收益。
    """
    assert not can_sell("600001.SH", 9.00, 9.00, 9.00, 9.00,
                        volume=1e6, amount=9e6, prev_close=10.00)
    # 盘中打开过的跌停收盘（开=低=收，但 high 在上面）→ 仍卖不掉
    assert not can_sell("600001.SH", 9.30, 9.35, 9.00, 9.00,
                        volume=1e6, amount=9e6, prev_close=10.00)


def test_limit_down_close_growth_board_cannot_sell():
    """创业板 / 科创板 −20% 跌停同样卖不掉（主板的 10% 阈值判不出来）。"""
    assert not can_sell("300001.SZ", 82.0, 80.5, 80.0, 80.0,
                        volume=1e6, amount=8e7, prev_close=100.0)
    # 只跌 10% → 未达 20% 跌停，卖得掉
    assert can_sell("300001.SZ", 91.0, 91.0, 90.0, 90.0,
                    volume=1e6, amount=9e7, prev_close=100.0)


def test_buy_and_sell_are_asymmetric_at_the_limit():
    """涨停收盘 → 买不进、**卖得掉**（早盘持筹者可以挂卖单成交）。

    两侧规则必须分开写：把买入端判据原样套到卖出端，会误杀所有涨停票的
    持仓变现能力。
    """
    up = dict(code="600001.SH", open=10.90, high=11.39, low=10.85,
              close=11.39, volume=5e6, amount=5.5e7, prev_close=10.35)
    assert not can_buy(**up) and can_sell(**up)
    down = dict(code="600001.SH", open=9.35, high=9.50, low=9.31,
                close=9.31, volume=5e6, amount=5.5e7, prev_close=10.35)
    assert can_buy(**down) and not can_sell(**down)


# ── 5. 判据缺失与价格不可信 ────────────────────────────────────────────────

def test_missing_prev_close_is_not_assumed_fillable():
    """信号缺失时不能默认放行（与 `is_st_name` 的双信号取「或」同一纪律）。

    没有前一收盘就**无法证明**没封涨停，也就无法声称这笔买得进。
    面板路径要求 listed_days ≥ 41，实测这类行占比≈0，fail-closed 不会误杀整池。
    """
    assert not can_buy("600001.SH", 10.0, 10.1, 9.9, 10.05, volume=1e6,
                       amount=1e7, prev_close=np.nan)
    assert not can_sell("600001.SH", 10.0, 10.1, 9.9, 10.05, volume=1e6,
                        amount=1e7, prev_close=np.nan)


def test_implausible_raw_jump_is_not_mislabelled_as_a_sealed_board():
    """封板判据用「恰好落在限价上」，**不是**「越过限价」。

    脏价格（源站把缩股记成价格跳空）在不复权价上会越过跌停限价。
    若封板也用「≤ 跌停价」，600766.SH 的 −96.43% 会被误认成一块跌停板——
    而它根本不是板：封板必须同时是 `open == high == low == close`。
    这条测试把这个区分钉死：封板标记为假，**结论**（不可卖）仍由跌停规则给出。
    """
    from aplus.panel import _limit_close_flags

    flags = _limit_close_flags("600766.SH", 0.35, 0.35, 0.35, 0.35,
                               1e5, 3.5e4, 9.84)
    suspended, sealed, at_up, at_dn, prev_known = (bool(np.asarray(f)[0]) for f in flags)
    assert not suspended and prev_known
    assert not sealed, "越过跌停限价的脏价格被误判成封板"
    assert at_dn and not at_up
    assert not can_sell("600766.SH", 0.35, 0.35, 0.35, 0.35, volume=1e5,
                        amount=3.5e4, prev_close=9.84)


# ── 6. 向量化：逐行判定与标量判定必须逐位一致 ──────────────────────────────

def test_vectorized_matches_scalar_row_by_row():
    """横截面过滤走的是向量路径，标量是它的真值——两者必须逐位一致。"""
    codes = ["600001.SH", "300002.SZ", "688003.SH", "600004.SH", "830799.BJ"]
    o = np.array([11.39, 10.0, 120.0, 10.0, 10.0])
    h = np.array([11.39, 10.1, 120.0, 10.2, 10.3])
    lo = np.array([11.39, 9.9, 99.0, 9.8, 9.7])
    c = np.array([11.39, 10.05, 120.0, 10.0, 10.0])
    v = np.array([5.6e6, 1e6, 1e6, 0.0, 1e6])
    amt = np.array([6.4e7, 1e7, 1.2e8, np.nan, 1e7])
    prev = np.array([10.35, 10.0, 100.0, 10.0, 10.0])
    vec = can_buy(codes, o, h, lo, c, volume=v, amount=amt, prev_close=prev)
    vec_sell = can_sell(codes, o, h, lo, c, volume=v, amount=amt, prev_close=prev)
    for k, code in enumerate(codes):
        args = dict(code=code, open=o[k], high=h[k], low=lo[k], close=c[k],
                    volume=v[k], amount=amt[k], prev_close=prev[k])
        assert bool(vec[k]) == can_buy(**args), f"{code} 买：向量与标量不一致"
        assert bool(vec_sell[k]) == can_sell(**args), f"{code} 卖：向量与标量不一致"
    # 一字板买不进卖不掉 / 正常票两侧都行 / 科创板 +20% 买不进但卖得掉 /
    # 停牌两侧都不行 / 北交所平盘可成交
    assert vec.tolist() == [False, True, False, False, True]
    assert vec_sell.tolist() == [False, True, True, False, True]


# ── 7. 阈值取值依据 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,limit", [
    ("600001.SH", LIMIT_MAIN), ("601001.SH", LIMIT_MAIN),
    ("000001.SZ", LIMIT_MAIN), ("002001.SZ", LIMIT_MAIN),
    ("300001.SZ", LIMIT_GROWTH), ("301001.SZ", LIMIT_GROWTH),
    ("688001.SH", LIMIT_GROWTH), ("689009.SH", LIMIT_GROWTH),
    ("830799.BJ", LIMIT_BEIJING), ("430047.BJ", LIMIT_BEIJING),
])
def test_price_limit_by_board(code, limit):
    """板块 → 涨跌幅的映射：主板 10%、创业板/科创板 20%、北交所 30%。

    取值依据是交易所价格笼子的挂牌规则，不是拟合出来的。
    ST ±5% 不在此列：`filter_universe` 的名称/列双信号在**进篮子之前**
    就把它们剔掉了，回测与实盘两条路径都不会让 ST 走到成交闸门。
    """
    assert price_limit(code) == limit


def test_price_limit_values_are_the_exchange_limits():
    """把三个档位的数值本身钉死——它们是判据的依据，不是实现细节。"""
    assert (LIMIT_MAIN, LIMIT_GROWTH, LIMIT_BEIJING) == (0.10, 0.20, 0.30)


# ── 8. 接入横截面过滤（回测与预测共用的一条路径）────────────────────────────

def _stock(code, n=90, drift=0.001):
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    close = 10.0 * (1 + drift) ** np.arange(n)
    o, h, lo, c = close.copy(), close * 1.01, close * 0.99, close.copy()
    v, amt = np.full(n, 1e6), np.full(n, 1e7)
    return pd.DataFrame({
        "date": dates, "code": [code] * n, "open": o, "high": h, "low": lo,
        "close": c, "volume": v, "amount": amt,
        "tradestatus": [1] * n, "isST": [0] * n, "turn": [1.0] * n,
    })


def _limit_px(prev_close: float, code: str, up: bool) -> float:
    """交易所口径的涨跌停价（四舍五入到分，银行家舍入会差一分钱）。"""
    from decimal import ROUND_HALF_UP, Decimal

    limit = Decimal(str(price_limit(code)))
    band = Decimal(str(prev_close)) * (Decimal(1) + limit if up else Decimal(1) - limit)
    return float(band.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _seal(df: pd.DataFrame, i: int, code: str, up: bool = True) -> pd.DataFrame:
    """把第 i 根做成**封在限价上的一字板**（开=高=低=收=涨跌停价）。"""
    px = _limit_px(float(df["close"].iloc[i - 1]), code, up)
    df.loc[i, ["open", "high", "low", "close"]] = px
    df.loc[i, ["volume", "amount"]] = [5.6e6, 6.4e7]
    return df


def _suspend(df: pd.DataFrame, i: int) -> pd.DataFrame:
    """把第 i 根做成停牌日（volume == 0、amount == 0、收盘冻结在上一日收盘）。"""
    px = float(df["close"].iloc[i - 1])
    df.loc[i, ["open", "high", "low", "close"]] = px
    df.loc[i, ["volume", "amount"]] = [0.0, 0.0]
    return df


DATES = pd.bdate_range("2026-01-01", periods=90).strftime("%Y-%m-%d").tolist()


def _universe_from(daily_map):
    from aplus.panel import FactorPanel
    from aplus.predict import filter_universe

    panel = FactorPanel(daily_map, None, DATES)
    return filter_universe(panel.cross_section(len(DATES) - 1))


def test_filter_universe_drops_limit_up_stock():
    """信号日一字涨停的股票必须退出横截面：它是「买不到」，不是「会跌」。"""
    dm = {"600001.SH": _stock("600001.SH"),
          "600825.SH": _seal(_seal(_stock("600825.SH", drift=0.02), 88,
                                  "600825.SH"), 89, "600825.SH")}
    kept = _universe_from(dm)
    assert "600825.SH" not in set(kept["code"]), "涨停票仍在横截面里——推荐了买不进的股票"
    assert "600001.SH" in set(kept["code"])


def test_filter_universe_drops_suspended_stock():
    """停牌股（volume==0）不可买。

    实测 `688146.SH` 收盘价冻结在 389.99 连续三日（2026-06-23~25）、
    mom20 因此算出 +190%，而它根本不可买。
    """
    dm = {"600001.SH": _stock("600001.SH"),
          "688146.SH": _suspend(_stock("688146.SH", drift=0.05), 89)}
    kept = _universe_from(dm)
    assert "688146.SH" not in set(kept["code"])


def test_missing_tradability_column_must_not_wipe_out_the_pool():
    """横截面**没有**可交易性列时，过滤必须是「不做」而不是「全杀」。

    实盘慢路径 `cross_rows_from_map` 不产出该列（`aplus/snapshot.py` 不在本文件
    的改动范围内），fail-closed 在那条路径上等于把整池清空、平台直接不可用。
    本测试把这条钉死：该列缺失时是**空操作**，接线缺口必须是显式已知的，
    不得用 fail-closed 的形式伪装成已生效。
    """
    from aplus.predict import filter_universe

    df = pd.DataFrame({
        "code": [f"60000{i + 1}.SH" for i in range(5)],
        "name": [f"股票{i}" for i in range(5)],
        "mom20": [0.1 * i for i in range(5)], "rev5": [0.0] * 5,
        "vol20": [0.2] * 5, "vsur": [1.0] * 5, "isST": [0] * 5,
        "listed_days": [100] * 5, "volume": [1e6] * 5,
    })
    assert len(filter_universe(df)) == 5, "缺列时不得清空股票池"


# ── 9. 接入回测 ────────────────────────────────────────────────────────────

def _seed(tmp_path, n_stocks=6, n_fillable=2, n_bars=90):
    """写入 n_stocks 只股票（各 n_bars 根 K 线）。

    前 `n_fillable` 只全程可买；其余在**倒数第三个交易日**一字涨停。
    它们的动量最高（drift 越大），若被剔除就会顶掉本来该进涨篮子的票——
    这正是「买不到 ≠ 会跌」最容易被违反的场景。

    涨停日取 `n_bars - 3` 而不是最后一根：信号日 D 必须还能取到 D+1/D+2
    两根 bar，否则 `_next_bars` 会因「卖价不存在」整天跳过，测不到过滤。
    """
    from aplus.store import LocalStore

    store = LocalStore(tmp_path)
    for i in range(n_stocks):
        code = f"{600000 + i + 1:06d}.SH"
        df = _stock(code, n_bars, drift=0.02 - i * 0.002)
        if i >= n_fillable:
            df = _seal(df, n_bars - 3, code)
        store.upsert_daily(code, df)
    return store


# 涨停信号日：倒数第三根 K 线，其后仍有 D+1/D+2 两根可成交 bar
LIMIT_UP_DAY = -3


def test_unfillable_stocks_are_excluded_from_both_baskets(tmp_path):
    """**买不到的票绝不能被顺手塞进跌篮子。**

    那等于把一个「缺失」当成一个「看空信号」，凭空造出虚假的多空价差：
    跌篮子里混进「涨停买不进」，两边一减，价差凭空出现。

    正确处理：从涨、跌**两边都排除**，并计入 `unfillable` 计数。
    """
    from aplus.backtest import build_cross_sections
    from aplus.predict import build_predictions

    store = _seed(tmp_path, n_stocks=6, n_fillable=2)
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    day = dates[LIMIT_UP_DAY]
    universe = xs[day]
    fillable = {"600001.SH", "600002.SH"}
    assert set(universe["code"]) <= fillable, \
        f"涨停票没被剔除：{universe['code'].tolist()}"
    assert set(universe["code"]) == fillable
    up, down = build_predictions(universe, top_n=10, bottom_n=10, weights={"mom20": 1.0})
    assert set(up["code"]) <= fillable
    assert set(down["code"]) <= fillable, \
        "买不到的票被塞进跌篮子——凭空造出了多空价差"
    assert xs["_unfillable"].get("600003.SH", 0) > 0, \
        "剔除必须记入 unfillable 计数，否则「剔除」不可见、无法监控"
    assert xs["_unfillable"].get("600001.SH", 0) == 0, "可买的股票不该被计入"


def test_scoreable_count_reflects_post_filter_universe(tmp_path):
    """`scoreable_count` 必须反映**剔除后**的池子。

    否则「能打出分数的股票数」虚高，跨候选的排名不可比——那正是这个数
    存在的唯一理由（见 `scoreable_count` 的苹果橘子问题）。
    """
    from aplus.backtest import build_cross_sections, evaluate_weights, scoreable_count

    store = _seed(tmp_path, n_stocks=6, n_fillable=2)
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    day = dates[LIMIT_UP_DAY]
    assert scoreable_count(xs[day], {"mom20": 1.0}) == 2, "横截面本身仍算得出 6 只"
    res = evaluate_weights(xs, {"mom20": 1.0}, top_n=10, bottom_n=10, dates=[day])
    assert res.attrs["n_ranked"] == 2, \
        f"可打分数量虚高：{res.attrs['n_ranked']}（剔除后只剩 2 只）"
    assert res.attrs.get("unfillable", 0) > 0, "剔除数必须外露，否则静默"


def test_min_basket_days_are_dropped_not_padded(tmp_path):
    """剔除后成分不足的日子沿用 `verify.MIN_BASKET`，不另起标准。

    一天只买得起 1 只与买满 10 只必须同等对待：前者衡量的是个别标的的运气，
    不是一个篮子。
    """
    from aplus.backtest import build_cross_sections, evaluate_weights
    from aplus.evolve_race import _daily_series
    from aplus.verify import MIN_BASKET

    store = _seed(tmp_path, n_stocks=10, n_fillable=2)   # 当天只剩 2 只 < MIN_BASKET
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    res = evaluate_weights(xs, {"mom20": 1.0}, top_n=10, bottom_n=10)
    series = list(_daily_series(res, "up").index)
    limit_day = dates[LIMIT_UP_DAY]
    up_limit = res[(res["pred_date"] == limit_day) & (res["direction"] == "up")]
    assert len(up_limit) == 2, "涨停票仍进了篮子"
    assert 2 < MIN_BASKET, "本用例要构造出成分不足的一天"
    assert dates[LIMIT_UP_DAY - 3] in series, "对照：成交窗口完全干净那天必须在"
    assert limit_day not in series, \
        "成分不足那天仍进入了日度序列——MIN_BASKET 没生效"


def test_sell_side_gate_drops_suspended_exit_from_backtest():
    """D+2 停牌 → 该笔**不成交**，不得按上一日收盘价记一笔已实现收益。

    只堵买不堵卖，回测仍在给不可执行的平仓记收益。停牌是成交窗口内唯一
    与价格口径无关的判据（`volume == 0` 时根本没有价格），所以它是这里
    唯一该拦的东西——理由见 `_next_bars` 的「成交窗口内的可交易性闸门」。
    """
    from aplus.backtest import _next_bars

    n = 12
    # D = dates[5] → entry = 第 6 根、exit = 第 7 根；把第 7 根做成停牌
    dm = {"600001.SH": _suspend(_stock("600001.SH", n), 7)}
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    out = _next_bars(dm, ["600001.SH"], dates[5], entry_lag=1, factors_map={})
    assert out.empty, "D+2 停牌仍被记成成交——没有标的物却记了平仓收益"


def test_buy_side_gate_drops_suspended_entry_from_backtest():
    """D+1 停牌 → 建不了仓。实测停牌次日收益 −1.02%（复牌跳空）。"""
    from aplus.backtest import _next_bars

    n = 12
    dm = {"600001.SH": _suspend(_stock("600001.SH", n), 6)}
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    out = _next_bars(dm, ["600001.SH"], dates[5], entry_lag=1, factors_map={})
    assert out.empty, "D+1 停牌仍被记成建仓"


def test_fill_gate_does_not_mislabelled_ex_rights_as_a_limit_move():
    """成交窗口内**不判**涨停/跌停——除权除息日在不复权价上就是限价幅度。

    10 送 10：不复权 −90%，现金分红 20 → 19：不复权 −5%（**恰好等于 ST 跌停价**）。
    两者当天都在正常交易，`tests/test_exrights_return.py` 钉死了必须照常成交。
    在这里按不复权价判限价 = 用错误的理由得到正确的结论。
    """
    from aplus.backtest import _next_bars

    n = 8
    closes = [100.0, 100.5, 100.0, 10.0, 10.2, 10.1, 10.0, 10.3]   # 第 3 根 10:1 拆股
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    daily = pd.DataFrame({
        "date": dates, "code": ["600000.SH"] * n, "open": closes,
        "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * n, "amount": [1e7] * n,
    })
    out = _next_bars({"600000.SH": daily}, ["600000.SH"], dates[0], entry_lag=1,
                     factors_map={})
    assert len(out) == 1, "拆股日被当成跌停板拦下了"


def test_sell_side_gate_keeps_normal_exit():
    """正常 D+2 仍必须成交——卖端闸门不得把整条链路堵死。"""
    from aplus.backtest import _next_bars

    n = 12
    dm = {"600001.SH": _stock("600001.SH", n)}
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    out = _next_bars(dm, ["600001.SH"], dates[5], entry_lag=1, factors_map={})
    assert len(out) == 1 and out["code"].iloc[0] == "600001.SH"


def test_buy_side_gate_keeps_normal_entry():
    """正常 D+1 买入仍必须成交。"""
    from aplus.backtest import _next_bars

    n = 12
    dm = {"600001.SH": _stock("600001.SH", n)}
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    out = _next_bars(dm, ["600001.SH"], dates[5], entry_lag=1, factors_map={})
    assert len(out) == 1
    assert out["prev_close"].iloc[0] > 0