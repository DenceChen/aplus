"""走前回测：历史区间上重放「预测 → 次日验证」闭环。

与每日实盘走完全相同的代码路径（cross_rows_from_map → filter_universe →
build_predictions → verify_predictions），唯一的差别是 as_of 被钉在每个历史
交易日上——这正是「回测是迭代抓手」的根：调参前后跑同一段代码，结果可比。
"""
import math

import numpy as np
import pandas as pd

from .adjust import hfq_close
from .panel import MAX_PLAUSIBLE_RETURN, build_panel, can_buy, can_sell
from .factor import composite_score
from .predict import PRED_COLUMNS, build_predictions, filter_universe
from .rebalance import buffer_hold, fill_to_top_n, is_rebalance_day
from .snapshot import cross_rows_from_map
from .verify import verify_predictions

MARKET_SAMPLE = 150  # 市场平均收益的抽样规模

# 成交滞后（单位：交易日）。1 = 信号 D 收盘 → D+1 收盘买 → D+2 收盘卖。
# 这是 A 股 T+1 下的**最短可成交**持有窗口；0 是旧的前视口径，仅供 A/B 对照。
ENTRY_LAG = 1

# 股票侧交易成本。2026-10-11 之前**整条链路完全没有成本项**（backtest/verify/portfolio
# 三个文件 fee|cost|commission 零命中），而策略是每日 Top10 轮动、接近满换手——
# 日均超额测得 +0.034%，在零成本口径下就已与 0 不可区分。
#
# 依据：Ernest Chan《Quantitative Trading》p96–100（Example 3.7）证明一个公开策略
# 加 5bp 成本可从夏普 +0.96 掉到 −2.16；成本必须是回测的一等公民。
#
# 口径与转债侧 `bond_horizon.COST_WAN15` 对齐：万15/边、双边合计 0.003。
# 默认取保守档而非 0 或乐观档——默认 0 会让任何漏传 cost 的调用方静默拿到
# 「策略有 alpha」的结论，那与转债侧默认 0.0003 而判定 0.003 是同一类事故。
STOCK_COST_DEFAULT = 0.003



def cost_drag(prev_codes, cur_codes, top_n: int,
              cost: float = STOCK_COST_DEFAULT) -> float:
    """一期调仓的成本拖累（占该期收益的比例）。

    与 `bond_horizon.cheap_rotation_backtest` 的换手公式同口径，保真优先于命名洁癖：

        turnover_ratio = (新进只数 + 退出只数) / (2 × max(top_n, 本期只数, 上期只数))
        cost_drag      = turnover_ratio × cost

    **首期建仓只付买侧**（没有卖出发生），故为 cost/2。

    分母取 max(...)：池子可能只有 2 只但 top_n=10，若用实际只数当分母，
    每期成本会被系统性低估。

    cost_drag > 0 时表示本期净收益 = 毛收益 − cost_drag。
    """
    if cost < 0:
        raise ValueError(f"cost 不得为负，收到 {cost}")
    cur = list(cur_codes or [])
    if not cur:
        return 0.0
    prev = list(prev_codes or [])
    if not prev:
        # 首期建仓：只有买入，没有卖出
        return float(cost) / 2.0
    n_in = len(set(cur) - set(prev))
    n_out = len(set(prev) - set(cur))
    denom = 2.0 * max(top_n, len(cur), len(prev))
    if denom <= 0:
        return 0.0
    return float((n_in + n_out) / denom * cost)


# 成交窗口闸门的拦截原因。**分原因**计数，不是单一桶：合成后只知道
# 「少了 N 条」时，无法判断是闸门太严还是数据出了问题。
#
# 其中 `sell_extended` **不是**拦截——那笔成交了，只是延后到复牌首根可卖 bar。
# 它照样必须被记录（持有期从 1 天变成 N 天是件大事），但不得计入
# `unfillable_execution` 总数，否则那个数会虚报「有 N 笔没成交」。
EXEC_GATE_REASONS = ("buy_unfillable", "sell_extended", "sell_void", "price_implausible")
EXEC_UNFILLABLE_REASONS = ("buy_unfillable", "sell_void", "price_implausible")

# `_raw_bars(full=True)` 的行序。`can_buy`/`can_sell` 的签名就是
# (o, h, l, c, volume, amount, prev_close)，少一列就只能整体降级。
RAW_ROW_NAMES = ("open", "high", "low", "close", "volume", "amount", "prev_close")


def _exec_stats() -> dict:
    """成交窗口闸门的拦截计数。`codes` 记到**代码**粒度，便于定位是哪几只。"""
    return {**{reason: 0 for reason in EXEC_GATE_REASONS}, "codes": {}}


def _note_exec(stats: dict | None, reason: str, code: str) -> None:
    """记一笔拦截。`stats` 为 None 时静默跳过（调用方不关心计数）。"""
    if not isinstance(stats, dict) or reason not in stats:
        return
    stats[reason] = int(stats[reason]) + 1
    codes = stats.get("codes")
    if isinstance(codes, dict):
        codes[code] = codes.get(code, 0) + 1


def _direction_of(directions, code: str):
    """逐股解析方向：`None` = 旧路径，`"up"/"down"` = 整批，`dict` = 逐股。"""
    if directions is None:
        return None
    if isinstance(directions, str):
        return directions
    return directions.get(code)


def _next_bars(daily_map: dict, codes: list, after_date: str,
               entry_lag: int = ENTRY_LAG, factors_map: dict | None = None,
               adj_cache: dict | None = None, directions=None,
               exec_stats: dict | None = None) -> pd.DataFrame:
    """成交窗口：信号日之后的第 `entry_lag` 根收盘买入，再下一根收盘卖出。

    ## 为什么默认 entry_lag = 1（而不是旧口径的 0）

    旧口径返回 `close = D+1 收盘`、`prev_close = D 收盘`，于是
    `actual_pct = D+1收盘 / D收盘 - 1` —— **收益窗口从 D 收盘开始**。
    可信号是用「含 D 收盘」的数据算出来的：D 收盘那一刻因子还没出来，
    更不可能在 D 收盘成交。**窗口起点落在信息集之内 = 一根 K 线的前视。**

    它衡量的不是「策略赚不赚钱」，而是「一笔你无法执行的交易赚不赚钱」。
    而 `up_excess_vs_market` 恰恰是决定加不加仓的依据。

    正确口径：**D 收盘出信号 → D+1 收盘买入 → D+2 收盘卖出**。
    持有 1 个交易日，且 A 股 T+1 合法（D+1 买、D+2 卖；D+1 买 D+1 卖才违法）。

    `entry_lag=0` 保留旧口径，**仅供 A/B 对照**，任何判定都不得使用。

    市场基准（`_market_by_day`）走同一个函数、同一个 lag —— 否则超额就是拿
    策略的 D+1→D+2 去减市场的 D→D+1，两段不同期，超额没有意义。

    ## 为什么用后复权价（factors_map 必传）

    `close` 是**不复权**价。除权除息当天它会直接跳空（10 送 10 即腰斩），
    用它算持有收益 = 凭空记一笔从未发生的亏损。全库实测 2623 只股票受影响，
    最极端一根 K 线造出 67 个百分点——正是 `evolution_race.json` 里那些
    −27.8%「日超额」的来源。详见 tests/test_exrights_return.py。

    返回列 `adjusted` 自报本次是否真的用上了因子表：无因子表时降级为不复权，
    但必须**可见**，否则数值看着正常、实际混着除权跳空，无从发现。

    ## 成交窗口内的可交易性闸门：**按方向分流**

    信号日那一侧由 `filter_universe` 用 `panel.buyable_series` 管住。成交窗口
    **内部**此前一道闸都没有：`close` 从不参与成交价判定，于是

        D+1 收盘封涨停 → 收盘挂单排不进队 → 回测却记成「买进了」
        D+2 收盘封跌停 → 跌停价上全是卖单 → 回测却记成「卖掉了」

    买卖两端同时偏乐观，而 `panel.can_sell` 在生产代码里**零调用点**。

    `directions` 决定判谁：

    - `"up"`（**真持仓**）：进场 bar 须 `can_buy`，出场 bar 须 `can_sell`。
      出场 bar **停牌**时持有到复牌首根可卖 bar（见下）；封跌停/一字板
      则**不成交**。
    - `"down"`（**回避清单**，平台从不持有）：维持原样，**只判停牌**。
      对跌腿判 `can_sell` 等于凭空惩罚一笔没做过的交易；更糟的是，一只票
      在 D+2 一字跌停只说明**预测对了**，把它当「卖不掉」删掉等于删掉最准
      的样本——方向恰好与「闸门要更严」相反。
    - `None`：旧路径（只判停牌）。`aplus/factor_ic.py` 的
      `test_forward_returns_matches_next_bars` 把默认口径钉成逐位相等。

    ### 停牌卖出为什么是「持有到复牌」而不是「删掉」

    旧代码对停牌是 `continue`，那一行从 `rows` 里消失 →
    `verify.py` 的 `merge(how="left")` 给出 `actual_pct=NaN` →
    `judge` 记 `no_data` → `rolling_stats` 的 `.mean()` 静默跳过。
    **停牌卖出的亏损就这么凭空蒸发了**，而篮子里少一只股票这件事没人看得见。

    复牌后再无任何可卖 bar（退市 / 永久停牌）才整笔作废，且必须计数——
    那笔亏损无上界，连一个负号都没记下。

    ### 涨跌停必须判在**除权还原后**的前收盘价上

    交易所的涨停价按当日「前收盘价」算，而除权日的前收是**除权后**的：
    10 送 10 那天的前收是 10.1/2 而不是 10.1。拿不复权的前一根 close 当
    基准会把每一个除权日打成跌停（见 `_prev_reference`）。本节此前写着
    「收益走复权价所以不判涨跌停」——**理由是错的，结论恰好是对的**，
    已由 `tests/test_execution_window_tradability.py` 钉成新的正确路径。

    ## 已知缺口：ST 的 ±5% 价格笼子没接上

    `panel.price_limit(code)` 只吃代码前缀，**判不出 ST**，所以
    `can_buy`/`can_sell` 的 `at_limit_up` / `at_limit_dn` 一律按
    10%/20%/30% 算。`panel.py` 的注释写着「`price_limit` 仍要认它」——
    **那句话不成立**。

    缺口范围要说准，不要夸大：**一字板那一侧是好的**——`_limit_close_flags`
    的 `on_band` 额外认了 ±5%，所以封在 ±5% 的一字 ST 板会被 `sealed`
    拦下（买卖两端都拦）。真正漏的是「收盘在 ±5%、但盘中**有**价格发现」
    的 bar：它既不是一字板，`at_limit_up`/`at_limit_dn` 又因为带宽算错而
    不成立 → `can_buy`/`can_sell` 直接放行。真实场景就是一只主板票在
    D+1 收盘封在 +5.00（ST 涨停价），回测照常记成「买进了」。

    修它要改 `aplus/panel.py`（ST 状态得进 `price_limit` 的签名），
    不在本文件的改动范围内。影响面有限：`filter_universe` 的
    `isST != 1` + 名称前缀双信号在**进篮子之前**就剔掉了 ST，
    只有「信号日摘帽、D+1 复牌成 ST」这类窄缝会漏。
    """
    if entry_lag < 0:
        raise ValueError(f"entry_lag 不得为负，收到 {entry_lag}")
    factors_map = factors_map or {}
    if adj_cache is None:
        adj_cache = {}
    rows = []
    for code in dict.fromkeys(codes):          # 去重：小池子时同股可能同现涨跌两榜
        df = daily_map.get(code)
        if df is None or df.empty:
            continue
        key = (code, id(df))
        hit = adj_cache.get(key)
        if hit is None:
            hit = _build_bar_cache(code, df, factors_map.get(code))
            adj_cache[key] = hit
        dates, closes, has_adj, raw, buyable, sellable = hit
        pos = int(np.searchsorted(dates, after_date, side="right"))   # 首根 > D
        # ── 该票在信号日**必须已经存在**（`pos == 0` ⟺ D 之前一根 K 线都没有）──
        #
        # 事故（2026-10-14 修复）：`pos` 对一只**尚未上市**的票返回 0，于是
        # `entry = 0` 指向它**上市后的头一根** K 线，`actual_pct` 记成那个
        # 收益——并且对上市前的**每一个**信号日重复一次。
        #
        # 单票验算完全正确（`close/prev_close − 1` 与厂商 `pctChg` 逐位吻合，
        # 实测 102/106/133 只零不一致），错的是**纳入了哪些票**——
        # 而那只在聚合层看得见，于是长期无人察觉。
        #
        # 市场基准受害最重：`_market_by_day` 从 `daily_map` 全部键随机抽样，
        # 「D 当天有 K 线」的群体均值 +0.0656%/日，「上市前」群体 −3.47%/日
        # （1214 天**没有一天为正**）。两者混在一起把基准从 +0.066%/日
        # 拖到 −0.402%/日——**偏差 −0.468%/日 ≈ −114%/年**，
        # 而 `up_excess_vs_market` 是本平台主指标，被系统性地虚增约 114 个
        # 百分点/年。数字看不出坏：−0.40%/日 是个体面的数。
        #
        # 停牌**不受影响**：`dates[pos-1] <= D` 由 searchsorted 保证，
        # 所以只有「D 之前完全没有历史」的上市前股票被挡下，D 当天停牌但
        # 此前存在的票照常计入（涨腿「持有到复牌」的语义原样保留）。
        if pos == 0:
            continue                            # 信号日尚未上市 → 当日不存在
        entry = pos + entry_lag - 1
        exit_i = entry + 1
        if entry < 0 or exit_i >= len(dates):
            continue                            # 卖价不存在 → 不成交，不拿更早的凑
        extended = False
        if _direction_of(directions, code) == "up":
            exit_i, reason = _up_leg_exit(raw, buyable, sellable,
                                          entry, exit_i, len(dates))
            if reason:                       # 含 sell_extended：延后成交也要留痕
                _note_exec(exec_stats, reason, code)
            if exit_i < 0:                    # 真的没成交 → 不进观测
                continue
            extended = exit_i > entry + 1
        elif not _bars_tradable(raw, entry, exit_i):
            continue                            # 跌腿/旧路径：买入或卖出那根停牌 → 不成交
        entry_px, exit_px = float(closes[entry]), float(closes[exit_i])
        # 价格不可信 → 不成交。详见 MAX_PLAUSIBLE_RETURN 的事故记录。
        # 先查价格有效性再做除法：0 或负价会让比值变成 inf/负数，比算出来
        # 再判断更危险。
        if entry_px <= 0 or exit_px <= 0:
            continue
        plausible = (_hold_is_plausible(closes, entry, exit_i) if extended
                     else abs(exit_px / entry_px - 1.0) <= MAX_PLAUSIBLE_RETURN)
        if not plausible:
            _note_exec(exec_stats, "price_implausible", code)
            continue                            # ±40% 之外不存在合法单日收益
        rows.append({"code": code, "close": exit_px,
                     "prev_close": entry_px, "adjusted": has_adj})
    return pd.DataFrame(rows)


def _build_bar_cache(code: str, df: pd.DataFrame, events) -> tuple:
    """每股一次的成交闸门缓存：`dates, closes, has_adj, raw, buyable, sellable`。

    ## 内存：为什么缓存的是**布尔标记**而不是 7 列 OHLC

    实测全库 5460 个 parquet、共 **8,716,724 行**日线（adj_cache 覆盖全部时）。
    float64 每列每行 8 字节（十进制 MB）：

        2 列（volume/amount，既有）  8,716,724 × 2 × 8 = 139.47 MB
        7 列（full OHLC + prev）     8,716,724 × 7 × 8 = 488.14 MB
        差额                          = **348.67 MB**

    （原 `_raw_bars` 注释写的是「~190 MB」，**实测错了**，差 1.8 倍。）

    348 MB 不算致命但也不该白付——这些列在整段回测里**只被读一次**
    （建缓存时算一遍标记），之后就再也不碰了。所以这里缓存的是
    `buyable`/`sellable` 两个**布尔数组**（各 1 字节/行）：

        8,716,724 × 2 × 1 = 17.43 MB   → 省下 331.24 MB

    代价只是「缓存里拿不回原始 OHLC」——而闸门本来也只在建缓存时判一次。
    完整 7 列仍以 `full=True` 提供，供需要逐根 OHLC 的调用方使用
    （测试、`_bar_flags` 的独立调用）。

    另一条被考虑过并否决的路：只对「会被拦截的候选」按需回读原始 parquet。
    否决理由是它把「建缓存时一次向量化」换成「每次 miss 一次磁盘 IO」，
    而闸门开启后 `sell_extended` 的持有延长会让同一只股票在多个日期反复
    被查——实测持有期均值 9.6 根、最长 731 根（≈34 个月），按需回读的
    放大效应不可控。省 331 MB 换一次可预测的 17 MB，值。

    复权基准未知（`attach_factor` 把首段置 NaN）时 `_prev_reference`
    退回不复权前收，理由见该函数。
    """
    has_adj = events is not None and len(events) > 0
    sorted_df = df.sort_values("date", kind="stable")
    closes = (hfq_close(sorted_df, events).to_numpy(float) if has_adj
              else sorted_df["close"].to_numpy(float))
    prev_ref = _prev_reference(sorted_df, closes)
    flags = _bar_flags(code, sorted_df, _raw_bars(sorted_df, full=True), prev_ref)
    buyable, sellable = flags if flags else (None, None)
    return (sorted_df["date"].to_numpy(dtype="U10"), closes, has_adj,
            _raw_bars(sorted_df), buyable, sellable)


def _prev_reference(sorted_df: pd.DataFrame, closes: np.ndarray) -> np.ndarray:
    """**除权还原后**的前收盘价序列——涨跌停价就是按它算的。

    交易所的涨停价 = 当日「前收盘价」×(1+limit)，而除权日的前收是除权后的
    价格：10 送 10 那天按 10.1 算涨停会得到 11.11，而交易所认的是
    10.1/2 = 5.05 → 5.55。用不复权的前一根 close 当基准，会把每一个除权日
    都打成「低于跌停价」，而那一天**照常交易**。

    推导：`closes` 是后复权价（`hfq_close`），`factor = closes / raw_close`，
    于是除权还原后的前收 = `hfq_prev / factor[当日]`。

    复权基准未知（`attach_factor` 在首个因子 > `UNKNOWN_SCALE_MIN_FACTOR`
    且晚于日线首根时把前面整段置 NaN）时**退回不复权前收**而不是判成
    「买不进」——信号缺失不等于这笔交易不可成交，静默 fail-closed 会在
    摘帽/新上市那段窗口里凭空杀掉整池。
    """
    n = len(closes)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    raw_close = (np.asarray(sorted_df["close"].astype(float))
                 if "close" in sorted_df.columns
                 else np.full(n, np.nan))
    raw_prev = np.full(n, np.nan)
    raw_prev[1:] = raw_close[:-1]
    factor = np.divide(closes, raw_close, out=np.full(n, np.nan),
                       where=raw_close > 0)
    out[1:] = closes[:-1] / factor[1:]
    bad = ~np.isfinite(out)
    out[bad] = raw_prev[bad]
    return out


def _raw_bars(sorted_df: pd.DataFrame, full: bool = False) -> np.ndarray:
    """单只股票的成交闸门原始列矩阵。

    默认 `(volume, amount)` 两行——**逐位保持改动前的形状**，因为
    `aplus/factor_ic.py` 直接 `from aplus.backtest import _raw_bars` 并调用
    `_raw_bars(s)`，且 `tests/test_factor_ic.py` 把它的快路径与 `_next_bars`
    钉成逐位相等。改默认形状会连 IC 诊断一起改掉。

    `full=True` 返回 `RAW_ROW_NAMES` 的完整 7 行，够 `can_buy`/`can_sell`
    逐根判定（少一列就只能整体降级成「只判停牌」）。

    内存取舍见 `_build_bar_cache`：全库 8,716,724 行时 full 矩阵比 2 行
    多 **348.67 MB**（8,716,724 × 5 × 8，十进制），所以**不缓存它**，
    建完布尔标记就丢。
    """
    def col(name):
        return (np.asarray(sorted_df[name].astype(float)) if name in sorted_df.columns
                else np.full(len(sorted_df), np.nan))

    if not full:
        return np.vstack([col("volume"), col("amount")])
    prev = np.full(len(sorted_df), np.nan)
    if len(sorted_df) > 1:
        prev[1:] = col("close")[:-1]
    return np.vstack([*(col(name) for name in RAW_ROW_NAMES[:-1]), prev])


def _bar_flags(code: str, sorted_df: pd.DataFrame, raw: np.ndarray,
               prev_close=None):
    """逐根算 `can_buy` / `can_sell`（向量化），返回 `(buyable, sellable)`。

    与逐根标量调用**逐位一致**（`tests/test_execution_window_tradability.py`
    有对照测试）——否则横截面过滤和回测会是两套口径。

    OHLC 缺列 / 帧为空 / raw 不是 full 形状 → 返回 `None`，由调用方退回
    「只判停牌」。**不是**默认放行涨跌停：没有 `high`/`low` 就判不出一字板，
    此时假装「买得进」等于把闸门悄悄关掉还不留痕迹。

    `prev_close` 默认取 raw 里 shift(1) 后的收盘；`_build_bar_cache` 会把
    **除权还原后**的前收传进来（见 `_prev_reference`）。
    """
    if raw is None or len(raw) < len(RAW_ROW_NAMES):
        return None
    if len(sorted_df) == 0:
        return None
    if any(name not in sorted_df.columns
           for name in ("open", "high", "low", "close")):
        return None
    idx = {name: i for i, name in enumerate(RAW_ROW_NAMES)}
    o, high, low, close = (raw[idx[name]] for name in ("open", "high", "low", "close"))
    vol, amt = raw[idx["volume"]], raw[idx["amount"]]
    prev = raw[idx["prev_close"]] if prev_close is None \
        else np.asarray(prev_close, dtype=float)
    return (np.asarray(can_buy(code, o, high, low, close, vol, amt, prev), dtype=bool),
            np.asarray(can_sell(code, o, high, low, close, vol, amt, prev), dtype=bool))


def _up_leg_exit(raw: np.ndarray, buyable, sellable, entry: int, exit_i: int,
                 n_bars: int):
    """涨腿成交窗口的闸门。返回 `(实际卖出的 bar 下标 或 -1, 拦截原因 或 None)`。

    - 进场 bar 买不进 → 不成交（`buy_unfillable`）。没有「等复牌再买」这回事。
    - 出场 bar 停牌 → **持有到复牌首根可卖 bar**（`sell_extended`），
      而不是删掉这笔观测——删掉会让这笔亏损从统计里凭空蒸发。
    - 出场 bar 封跌停 / 一字板 → 卖不掉，不成交（`sell_void`）。
    - 复牌后再无任何可卖 bar（退市 / 永久停牌）→ 整笔作废（`sell_void`），
      且必须由调用方计数：亏损无上界，不留痕就等于从统计里消失。
    """
    if buyable is None:                    # OHLC 缺列 → 退回只判停牌
        return (exit_i, None) if _bars_tradable(raw, entry, exit_i) else (-1, None)
    if not buyable[entry]:
        return -1, "buy_unfillable"
    if sellable[exit_i]:
        return exit_i, None
    if not _bar_suspended(raw, exit_i):
        # 不是停牌 —— 是跌停收盘 / 一字板。买不进卖不掉即不成交，
        # 这是「表观超额里有一块来自无法执行的交易」的那一块。
        return -1, "sell_void"
    for j in range(exit_i + 1, n_bars):
        if sellable[j]:
            return j, "sell_extended"
    return -1, "sell_void"


def _hold_is_plausible(closes: np.ndarray, entry: int, exit_i: int) -> bool:
    """延长持有的**逐日相邻步**是否都落在 `MAX_PLAUSIBLE_RETURN` 内。

    `MAX_PLAUSIBLE_RETURN` 判的是**单根 bar 的脏价格**（复牌跳空被源站
    记成价格跳空这类）。把它原样套在延长持有的**总收益**上，就是把
    「单日 ±40% 的上界」误当成「任意持有期 ±40% 的上界」——实测强制延长
    均值 9.6 根、最长 731 根（≈34 个月），那么长的持有期里总收益越界是
    **常态**，于是最惨的那批样本会被这道闸门重新扔掉，正是本项修复要
    消灭的「亏损凭空蒸发」。

    所以延长持有按逐日步判：脏 bar 照样拦得住（某一根自己跳 60% 就出局），
    而 N 天累计跌 80% 只是 N 个各 −5% 的正常交易日，必须留下。
    """
    if exit_i - entry <= 1:
        return True
    seg = np.asarray(closes[entry:exit_i + 1], dtype=float)
    if not np.all(np.isfinite(seg)) or np.any(seg <= 0):
        return False
    return bool(np.all(np.abs(seg[1:] / seg[:-1] - 1.0) <= MAX_PLAUSIBLE_RETURN))


def _bar_suspended(raw: np.ndarray, i: int) -> bool:
    """第 i 根是否停牌——`_bars_tradable` 的单 bar 版本（含缺失列的放行行为）。"""
    try:
        if float(raw[0][i]) <= 0:
            return True
        amt = float(raw[1][i])
        return bool(np.isfinite(amt) and amt <= 0)
    except (IndexError, ValueError, TypeError):
        return False


def _bars_tradable(raw: np.ndarray, entry: int, exit_i: int) -> bool:
    """买入 bar 与卖出 bar 是否都有价格（`_raw_bars` 的配套判定）。

    **只判停牌**，理由见 `_next_bars` 的「成交窗口内的可交易性闸门」一节：
    在不复权价上判涨跌停会把除权除息日误认成限价，而本函数记的是后复权收益。

    `volume` 在全库抽样里从不为 NaN；`amount` 缺失一律按「未知」处理，
    不参与判定（实测 59.4% 的末根 K 线 amount 是 NaN、8.8% 整列缺失）。
    """
    vol_col, amt_col = raw[0], raw[1]
    try:
        for i in (entry, exit_i):
            if float(vol_col[i]) <= 0:
                return False
            amt = float(amt_col[i])
            if np.isfinite(amt) and amt <= 0:
                return False
    except (IndexError, ValueError, TypeError):
        return True              # 原始列缺失 → 保持既有行为，不静默改变口径
    return True


def _resolve_maps(store, daily_map, factors_map):
    if daily_map is None:
        if factors_map is None:
            from .snapshot import load_factors_map

            factors_map = load_factors_map(store)
        daily_map = {p.stem: store.read_daily(p.stem)
                     for p in sorted((store.root / "master" / "daily").glob("*.parquet"))}
    return daily_map, factors_map


def _market_by_day(daily_map: dict, dates: list, sample_size: int = MARKET_SAMPLE,
                   entry_lag: int = ENTRY_LAG, factors_map: dict | None = None,
                   adj_cache: dict | None = None) -> dict:
    """市场基准日收益——随机分层抽样、固定种子，与权重**无关**，故整窗只算一次。

    （此前 list()[:150] 取的是文件名序前 150 只 sh.600xxx 老工业股，基准系统性
    偏斜 → 超额测量失真；固定随机种子保证可复现。）

    entry_lag 必须与策略侧一致，否则超额是拿两段不同期相减，没有意义；
    复权口径同理——基准若用不复权价，超额就是拿复权收益减不复权收益。
    """
    import random as _random

    rng = _random.Random(42)
    all_codes = list(daily_map)
    sample_codes = rng.sample(all_codes, min(sample_size, len(all_codes)))
    out = {}
    for day in dates:
        market = _next_bars(daily_map, sample_codes, day, entry_lag=entry_lag,
                            factors_map=factors_map, adj_cache=adj_cache)
        out[day] = float((market["close"] / market["prev_close"] - 1).mean()) if len(market) else 0.0
    return out


def build_cross_sections(store, start: str, end: str, names_map: dict | None = None,
                         daily_map: dict | None = None, factors_map: dict | None = None,
                         entry_lag: int = ENTRY_LAG) -> dict:
    """窗口内每日横截面**算一次**（因子面板 + 股票过滤）。

    复权挂载、因子计算、股票过滤全都与权重无关——只有 build_predictions 依赖
    权重。赛马 N 组候选却要重跑 N 次整条链路，是自进化跑不动的唯一原因。
    面板把「每只 × 每日」的重算压成「每只一次」（见 aplus/panel.py）。

    entry_lag 随横截面一起存进结果，供 evaluate_weights 保证策略与市场基准
    使用**同一成交窗口**（两者不匹配时超额无意义）。复权因子表同理。

    返回 {date: universe_df, "_dates": [...], "_market": {date: 基准收益}, ...}。
    """
    daily_map, factors_map = _resolve_maps(store, daily_map, factors_map)
    if not daily_map:
        return {"_dates": [], "_daily_map": {}, "_market": {}, "_entry_lag": entry_lag,
                "_factors_map": {}, "_adj_cache": {}, "_unadjusted": {},
                "_unfillable": {}, "_unknown_st": {}}
    panel = build_panel(store, start, end, names_map, daily_map, factors_map)
    adj_cache: dict = {}
    sections: dict = {
        "_dates": panel.dates,
        "_daily_map": daily_map,
        "_entry_lag": entry_lag,
        "_factors_map": factors_map or {},
        "_adj_cache": adj_cache,
        "_unadjusted": {},          # 未复权降级的 code → 次数，必须可见而非静默
        # 买不进的 code → 被剔除的天数。「剔除」本身必须可监控：否则一次口径
        # 变更在报告里完全看不出来。
        "_unfillable": {},
        # ST 状态未知的 code → 天数。与 unfillable 同理：放行不等于没问题，
        # 落在盲区的规模必须能被读到（2026-10-12 缺陷 24）。
        "_unknown_st": {},
        "_market": _market_by_day(daily_map, panel.dates, entry_lag=entry_lag,
                                  factors_map=factors_map, adj_cache=adj_cache),
    }
    for i, day in enumerate(panel.dates):
        sections[day] = filter_universe(panel.cross_section(i, names_map),
                                        unfillable=sections["_unfillable"],
                                        unknown_st=sections["_unknown_st"])
    return sections


def scoreable_count(universe: pd.DataFrame, weights) -> int:
    """这组权重**能打出分数**的股票数（`composite_score` 里 min_count 的等价物）。

    ## 为什么必须显式统计

    `composite_score` 用 `min_count=len(weights)`：**任一因子 NaN → 合成分 NaN**，
    NaN 的股票被直接踢出排名。这是设计意图（宁缺毋滥），但它带来一个
    **藏在底下的苹果橘子问题**：

    实测 1693 只股票（占全池 31%）的日线缺 `turn` 列 → `turn20` 恒为 NaN →
    **任何含 turn20 权重的组合永远无法选中它们**。

    于是「现任 v1」跑在 5447 只的池子上，「含 turn20 的候选」跑在 ~3754 只的池子上，
    两者的超额根本不可比，而表格看上去却像在同一批标的上比较。
    这个数必须打印出来，否则跨候选的排名就是假的。
    """
    if not callable(weights):
        keys = [k for k in (weights or {})]
    else:
        return -1                      # 可调用权重逐日变化，无法静态判定
    if not keys or universe is None or universe.empty:
        return 0
    missing = [k for k in keys if k not in universe.columns]
    if missing:
        return 0
    return int(universe[keys].notna().all(axis=1).sum())


def _held_predictions(universe: pd.DataFrame, held_up: list, held_down: list):
    """非调仓日：原样返回上期持仓，不重新选股。

    返回列必须与 `build_predictions` 对齐，否则下游 `_next_bars` /
    `verify_predictions` 会对不上。
    """
    by_code = universe.set_index("code", drop=False)

    def _slice(codes, direction):
        keep = [c for c in (codes or []) if c in by_code.index]
        if not keep:
            return None
        sub = by_code.loc[keep].reset_index(drop=True)
        sub["direction"] = direction
        return sub

    up, down = _slice(held_up, "up"), _slice(held_down, "down")
    if up is None or down is None:
        return None, None
    cols = [c for c in PRED_COLUMNS if c in up.columns or c in down.columns]
    return up.reindex(columns=cols), down.reindex(columns=cols)


def _attach_exec_attrs(result: pd.DataFrame, exec_stats: dict) -> pd.DataFrame:
    """把成交窗口闸门的拦截计数写进 `attrs`——与既有 `unfillable` 同一套机制。

    「一笔观测从统计里消失」和「这笔观测因为不可成交而被剔除」在报告里必须
    **长得不一样**：前者是 bug（亏损凭空蒸发），后者是必须被看见的口径。
    分原因计数是为了区分「闸门太严」和「数据出了问题」。

    注意 `price_implausible` 也在这里：那道闸门一直存在，但它拦掉的样本
    此前是完全不可见的。`sell_extended` 出现在 `by` 里但**不进**总数——
    那笔是成交了、只是延后到复牌首根可卖 bar，不是「没成交」。

    两个计数各回答一个问题：
        unfillable_execution       有多少笔**没成交**（进不了统计）
        unfillable_execution_by    每个原因各多少（含延后成交，供口径体检）
    """
    by = {reason: int(exec_stats.get(reason, 0)) for reason in EXEC_GATE_REASONS}
    result.attrs["unfillable_execution"] = int(
        sum(by[reason] for reason in EXEC_UNFILLABLE_REASONS))
    result.attrs["unfillable_execution_by"] = by
    result.attrs["unfillable_execution_codes"] = len(exec_stats.get("codes") or {})
    return result


def evaluate_weights(cross_sections: dict, weights=None, top_n: int = 10,
                     bottom_n: int = 10, dates: list | None = None,
                     rebalance_every: int = 1,
                     buffer_multiple: float | None = None,
                     execution_gate: bool = True) -> pd.DataFrame:
    """在**已算好的**横截面上跑一组权重（与 walk_forward 逐行等价）。

    dates 可限定子区间——样本外验证（训练段选权重、测试段验收）就靠它，
    避免「用历史最优权重」这种变相数据泄露。

    ## 调仓频率（2026-10-11）

    `rebalance_every=1`（默认）与接入频率轴之前**逐位相同**——有测试钉死，
    历史结论不会因新增参数而失效。

    非调仓日**不重新选股**，原样持有上期篮子，且**不产生成本**：
    低频若被重复收费，频率对比就完全失去意义。

    `buffer_multiple` 与频率正交：它在**调仓日**决定哪些老持仓值得保留
    （仍在 top_n × buffer_multiple 宽带内就留），削减的是换手**幅度**。

    成本为什么值得做成参数：5 万本金 / 10 只持仓 / 单只 5000 元时，
    5 元最低佣金是主导项，一次往返 12.60 元 = 0.2520%；
    实测日频换手 27.50% → 年成本 **16.91%**，而所有已测因子效应都在 ±1% 以内。
    书的 3.1.4 口径是**每月末调仓**——只跑日频，就永远在验证一件书没声称过的事。

    ## `execution_gate`（默认 True）

    打开后，成交窗口内部（D+1 买 / D+2 卖）按 `panel.can_buy` / `can_sell`
    判定可成交性，只作用于**涨腿**；跌腿维持原样（见 `_next_bars`）。
    被拦下的观测按原因计数写进 `attrs["unfillable_execution*"]`——
    **不计数等于从统计里彻底消失且不留任何痕迹**。

    `False` 是 A/B 对照用的旧口径，生产路径不得传。默认值是 True：
    fail-open 的默认值会让「忘了传闸门」和「闸门生效」在报告里长得一模一样。
    """
    daily_map = cross_sections.get("_daily_map") or {}
    market = cross_sections.get("_market") or {}
    entry_lag = cross_sections.get("_entry_lag", ENTRY_LAG)
    cost = cross_sections.get("_cost", STOCK_COST_DEFAULT)
    factors_map = cross_sections.get("_factors_map") or {}
    adj_cache = cross_sections.setdefault("_adj_cache", {})
    unadjusted = cross_sections.setdefault("_unadjusted", {})
    use_dates = dates if dates is not None else cross_sections.get("_dates", [])
    exec_stats = _exec_stats()
    out = []
    prev_up: list | None = None          # 上一期的涨篮子，用于算换手
    held_up: list | None = None          # 跨调仓周期持有的篮子
    held_down: list | None = None
    for day in use_dates:
        universe = cross_sections.get(day)
        if universe is None or universe.empty:
            continue
        rebalance = is_rebalance_day(use_dates, day, rebalance_every)
        if rebalance or held_up is None:
            day_weights = weights(universe) if callable(weights) else weights
            up, down = build_predictions(universe, top_n=top_n, bottom_n=bottom_n,
                                         weights=day_weights)
            if buffer_multiple and held_up is not None:
                sc = composite_score(universe, day_weights)
                ranked_up = list(universe.assign(_s=sc).dropna(subset=["_s"])
                                 .sort_values("_s", ascending=False)["code"])
                ranked_dn = list(reversed(ranked_up))
                held_up = fill_to_top_n(
                    buffer_hold(held_up, ranked_up, top_n, buffer_multiple),
                    ranked_up, top_n)
                held_down = fill_to_top_n(
                    buffer_hold(held_down, ranked_dn, bottom_n, buffer_multiple),
                    ranked_dn, bottom_n)
                up, down = _held_predictions(universe, held_up, held_down)
                if up is None or down is None:
                    continue
            held_up, held_down = list(up["code"]), list(down["code"])
        else:
            up, down = _held_predictions(universe, held_up, held_down)
            if up is None or down is None:
                continue
        preds = pd.concat([up, down], ignore_index=True)
        if preds.empty:
            continue
        codes = list(dict.fromkeys(preds["code"].tolist()))
        directions = None
        if execution_gate:
            # 同股同时出现在涨跌两榜时（小池子 top_n+bottom_n > 池子）按
            # **涨腿**判——那才是真实持仓，它买不进/卖不掉才是真问题。
            up_codes = set(up["code"])
            directions = {c: ("up" if c in up_codes else "down") for c in codes}
        bars = _next_bars(daily_map, codes, day, entry_lag=entry_lag,
                          factors_map=factors_map, adj_cache=adj_cache,
                          directions=directions, exec_stats=exec_stats)
        if bars.empty:
            continue
        # 缺因子表 → 走不复权回退。记进 `_unadjusted` 让调用方能看见，
        # 否则这类样本会静默混进除权跳空，数值看着正常、实则失真。
        if "adjusted" in bars.columns:
            for code in bars.loc[~bars["adjusted"].astype(bool), "code"]:
                unadjusted[code] = unadjusted.get(code, 0) + 1
        res = verify_predictions(preds, bars)
        res["pred_date"] = day
        res["market_pct"] = market.get(day, 0.0)
        # 交易成本：只按**涨腿**计（我们实际建仓的一侧），跌腿是回避清单不产生交易。
        # 成本挂在 `cost` 列上，由 rolling_stats / excess_tstat 在算净超额时扣除——
        # 不在 verify_predictions 里直接改 actual_pct，因为那是「市场实际怎么走」，
        # 毛收益必须保持原样，否则「预测准不准」这件事也被成本污染了。
        # **非调仓日没有换手 → 成本为 0。** 低频若被重复收费，
        # 日/周/月对比就完全失去意义。
        first_hold = prev_up is None
        drag = (cost_drag(prev_up, list(up["code"]), top_n, cost=cost)
                if (rebalance or first_hold) else 0.0)
        res["cost"] = 0.0
        if len(up):
            res.loc[res["direction"] == "up", "cost"] = drag
        if rebalance or first_hold:
            prev_up = list(up["code"])
        out.append(res)

    if not out:
        empty = pd.DataFrame(columns=["pred_date", "code", "direction", "actual_pct",
                                      "result", "market_pct", "cost"])
        return _attach_exec_attrs(empty, exec_stats)
    result = pd.concat(out, ignore_index=True)
    result = _attach_exec_attrs(result, exec_stats)
    # 逐日记录可打分股票数（跨候选的池子差异必须可见，见 scoreable_count）
    # ——它统计的是**剔除买不进之后**的横截面，否则这个数虚高。
    unfillable = cross_sections.get("_unfillable") or {}
    result.attrs["unfillable"] = int(sum(unfillable.values()))
    result.attrs["unfillable_codes"] = len(unfillable)
    # ST 状态未知的「股票·天」数：与 unfillable 同理——放行没问题，
    # 但「有多少只处在 ST 筛查盲区」必须能被读到（2026-10-12 缺陷 24）。
    unknown_st = cross_sections.get("_unknown_st") or {}
    result.attrs["unknown_st"] = int(sum(unknown_st.values()))
    result.attrs["unknown_st_codes"] = len(unknown_st)
    if callable(weights):
        result.attrs["n_ranked"] = -1
    else:
        result.attrs["n_ranked"] = sampled_ranked_count(cross_sections, use_dates, weights)
        counts = [len(cross_sections.get(d)) for d in use_dates
                  if cross_sections.get(d) is not None
                  and not cross_sections.get(d).empty]
        result.attrs["n_universe"] = int(sum(counts) / len(counts)) if counts else 0
    return result


# 取样点数：够代表整段，又不至于给 1881 天的窗口做无谓计算
N_RANKED_SAMPLES = 5


def sampled_ranked_count(cross_sections: dict, use_dates: list, weights) -> int:
    """整段窗口里**可打分股票数**的典型值（跨若干等距日期取中位数）。

    ## 为什么不能只看第一天

    原来的实现是 `scoreable_count(cross_sections[use_dates[0]], weights)`——
    **单点**。而 `n_ranked` 是第六道闸（股票池可比）的输入。

    跑 7.8 年全历史赛马时窗口从 `2019-01-02` 开始，**那是数据集的第一天**：
    那天没有任何股票有可计算的 `mom20`（20 日窗口需要 20 个交易日历史），
    于是报 **0**。那次没造成误判（闸门读的是测试段的数），
    但它说明**闸门的输入可以是一个退化样本，而闸门不会知道**。

    ## 为什么取中位数而不是平均

    「少数日子不可算」是常态（停牌、新股、因子刚够窗口）。
    平均会被那些日子拉低，而中位数仍反映典型的那几天——
    我们要问的是「这组权重大致能在多少只股票上排名」，
    不是「把所有坏日子都算进去平均一下」。

    ## 为什么不是所有日子都算

    1881 个交易日逐日算一遍没有额外信息，只是把闸门的输入算三遍。
    """
    days = [d for d in (use_dates or []) if cross_sections.get(d) is not None]
    if not days:
        return 0
    if len(days) <= N_RANKED_SAMPLES:
        picks = days
    else:
        step = (len(days) - 1) / (N_RANKED_SAMPLES - 1)
        picks = [days[int(round(i * step))] for i in range(N_RANKED_SAMPLES)]
    counts = sorted(scoreable_count(cross_sections[d], weights) for d in picks)
    return int(counts[len(counts) // 2])


def walk_forward(store, start: str, end: str, top_n: int = 10, bottom_n: int = 10,
                 names_map: dict | None = None, weights: dict | None = None,
                 daily_map: dict | None = None, factors_map: dict | None = None) -> pd.DataFrame:
    """单组权重的走前回测 = build_cross_sections + evaluate_weights。

    多组权重请用 build_cross_sections + evaluate_weights，**别循环调本函数**。
    """
    daily_map, factors_map = _resolve_maps(store, daily_map, factors_map)
    if not daily_map:
        return pd.DataFrame(columns=["pred_date", "code", "direction", "actual_pct",
                                     "result", "market_pct"])
    xs = build_cross_sections(store, start, end, names_map=names_map,
                              daily_map=daily_map, factors_map=factors_map)
    return evaluate_weights(xs, weights, top_n=top_n, bottom_n=bottom_n)


def _finite_or_none(value):
    """NaN → None，其余原样返回。

    `.mean()` 对**全空**输入返回 NaN 而不是 None——而 NaN 不是合法 JSON，
    Starlette 的 JSONResponse 用 `allow_nan=False`，遇到它整页 500
    （2026-10-11：485 行里 20 行坏点，回测标签页直接打不开）。

    判空只能判**行数**，坏点在**值**上，所以这条防线必须落在计算之后。
    同时它也是语义要求：`0.0` 是「恰好持平」，`None` 是「算不出来」，
    消费方必须能区分这两者。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def daily_series(results: pd.DataFrame) -> list:
    """回测/验证结果 → 按日聚合序列（图表数据）。

    涨跌两腿都给：跌篮子的「回避超额」= 市场 − 跌篮子收益，正 = 回避有效。
    只给涨腿的话，图表上看不出「排序到底能不能分开两端」。

    **返回值必须是严格 JSON 可序列化的**（无 NaN / 无 Infinity）——
    「算不出来」一律是 `None`，见 `_finite_or_none`。
    """
    if results is None or results.empty:
        return []
    out = []
    for day, g in results.groupby("pred_date"):
        judged = int(g["result"].isin(["hit", "miss"]).sum())
        hit_rate = _finite_or_none((g["result"] == "hit").sum() / judged) if judged else None
        up = g[g["direction"] == "up"]
        down = g[g["direction"] == "down"]
        up_excess = _finite_or_none((up["actual_pct"] - up["market_pct"]).mean()) if len(up) else None
        down_excess = _finite_or_none((down["market_pct"] - down["actual_pct"]).mean()) if len(down) else None
        breadth = _finite_or_none(up_excess + down_excess) \
            if (up_excess is not None and down_excess is not None) else None
        out.append({"date": day, "judged": judged, "hit_rate": hit_rate,
                    "up_excess": up_excess, "down_excess": down_excess,
                    "breadth": breadth})
    return out
