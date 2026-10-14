"""因子面板：把「每只股票 × 每个预测日」的横截面重算，压缩成「每只股票算一次」。

## 为什么

走前回测的瓶颈不在排序，在**因子计算**：

```
for day in 540 个交易日:            # 540
    for code in 5447 只股票:        # × 5447 = 295 万次
        sliced = daily[date <= day]      # 切一份历史副本
        sliced = attach_factor(sliced)   # 重新合并复权因子
        factors = compute_factors(sliced) # 重算 21 天滚动窗口
```

但五个因子**全是滚动窗口**（mom20 / rev5 / vol20 / vsur / turn20），每一根 K 线
的因子值与「今天是哪天」无关——它只依赖这根 K 线**往前**的窗口。于是：

- 每只股票的因子可以**整段向量化算一次**（对 ~1882 行跑一次 shift/rolling）
- 任一预测日 D 的横截面 = 按 D 在每只股票的时间轴上**定位那一行**

## as-of 防未来函数没有被削弱

滚动窗口天然只向后看（`shift` / `rolling` 均不含中心对称或未来项），
「D 日的因子 = D 那行预先算好的值」与「算到 D 为止再取末行」**逐位等价**。
这不是近似，是同一组数字的两种求法。测试用逐行对比钉死这一点。

## 与既有实现的语义对齐（任何偏差都会让历史结论失效）

| 因子 | 标量实现 | 向量实现 |
|---|---|---|
| mom20 | `close[-1]/close[-21]-1` | `c/c.shift(20)-1` |
| rev5 | `-(close[-1]/close[-6]-1)` | `-(c/c.shift(5)-1)` |
| vol20 | `pct_change().dropna().tail(20).std(ddof=0)` | `pct_change().rolling(20).std(ddof=0)` |
| vsur | `vol[-1]/vol[-21:-1].mean()` | `v/v.shift(1).rolling(20).mean()` |
| turn20 | `turn.tail(20).mean()` | `turn.rolling(20).mean()` |

min_history=41 > 20，故 `rolling(20)` 的首个窗口永远落在 pct_change 的 NaN 之后，
`dropna()` 与否不会造成分歧——这是等价成立的前提。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .adjust import attach_factor
from .factor import ivol_series, rsi_series

FACTOR_COLUMNS = ["mom20", "rev5", "vol20", "vsur", "turn20", "ivol",
                 "turnratio", "rsi"]
# 面板里随行携带、供横截面组装与过滤使用的原始列。
# `buyable` = 该股在**该根 K 线收盘**买不进（停牌 / 一字板 / 涨停收盘 / 价格不可信）。
# 它必须随行携带而不能到过滤时才去读原始日线：横截面过滤在 5447 × 每日 上跑，
# 逐行回查会把「每只算一次」的面板优势退化成逐日重算。
CARRY_COLUMNS = ["volume", "isST", "listed_days", "buyable"]

# 《因子投资：方法与实践》3.1.2 节的量价数据处理纪律
MIN_SAMPLE_RATIO = 2 / 3      # 窗口内有效样本低于此比例 → 作缺失，不用小样本硬算
RESUME_CLAMP = 0.10           # 复牌首日无涨跌停，实测单日 +593.72%；压到 ±10%

# 合法单日收益的绝对上限。A 股真实涨跌停：主板 ±10%、创业板/科创板 ±20%、
# 北交所 ±30%；**±40% 之外不存在合法收益**。
#
# 为什么因子层也要这道闸：2026-10-11 实测 `302132.SZ` 在 2025-02-18 有
# **+462.51%** 的复权收益（因子 1.0 → 5.9757，缩股被源站记成价格跳空）。
# 它的 20 日动量会因此冲到榜首——与当天真实发生过的 `mom20=+903.72%`
# 进入用户建仓清单是**同一个失效路径**。
# 回测侧有 `_next_bars` 的成交闸门，实盘横截面**原本一道都没有**。
MAX_PLAUSIBLE_RETURN = 0.40

# 书 3.8 节异常换手率的窗口：近 21 日 ÷ 近 252 日（一年）。
TURN_RATIO_SHORT = 21
TURN_RATIO_LONG = 252

# 长窗口最少需要多少比例的非空值（2026-10-14，缺陷 26）。
#
# ## 为什么不能沿用 pandas 默认
#
# `rolling(252).mean()` 的 `min_periods` 默认等于窗口长度，也就是
# **252 根里一根都不能缺**。而源站的 `turn` 列有零散缺失，于是
# 一只上市三年的老股票，中间只缺 1 天，整个因子就变成 NaN。
#
# ## 实测的代价（2025-03-20 全市场真实横截面）
#
#     已上市 ≥252 日的股票 5235 只 → 因子可算 4753、算不出 482
#     这 482 只的 `turn` 覆盖率**中位 96.0%**：
#         只缺 1 根 122 只 / 缺 2~6 根 85 只 / 缺 7~20 根 122 只 / 缺 >20 根 153 只
#
# 这直接造成了赛马里 `turnratio` 候选 `n_ranked` 从 4904 掉到 4567（−6.5%），
# 进而被股票池闸拦下——**闸拦的是一个数据完整性瑕疵，不是一个方法论分歧**。
#
# ## 门槛为什么取 0.9
#
# 0.9 是一个**先验的完整性判据**，不是为了让某个候选过闸而调出来的数：
# 90% 覆盖的 252 日均值与 100% 覆盖的差别远小于换手率本身的噪声，
# 而把它调到 0.99 只能多救回 122 只里的一小部分。
# 无缺失样本的结果与原实现**逐位相同**（有测试钉死），所以修复不改变任何
# 已经算出来的数，只把「本来有 96% 历史」的那些股票放回来。
MIN_TURN_COVERAGE = 0.9



# ── 可交易性：这一笔到底买不买得进 / 卖不卖得掉 ──────────────────────────────
#
# 回测口径是 D 收盘出信号 → **D+1 收盘买** → D+2 收盘卖（ENTRY_LAG=1）。
# 「能不能成交」此前全平台一处都没有：涨停收盘、一字板、停牌都被当成正常
# 交易记了收益，于是回测系统性偏乐观。
#
# 一字板 `open == high == low == close`：开盘即封在涨停价、全天没打开过，
# 收盘价就是涨停价——收盘挂单排不进去。实测 600825.SH 连续 9 天一字板
# （5.84→12.53）、603221.SH 10 次进涨篮子。

# 涨跌幅限制（按板块）。取值依据是交易所价格笼子的挂牌规则，不是拟合出来的。
LIMIT_MAIN = 0.10        # 主板：60xxxx（沪）/ 00xxxx、001xxx、002xxx（深）
LIMIT_GROWTH = 0.20      # 创业板 300xxx/301xxx、科创板 688xxx/689xxx
LIMIT_BEIJING = 0.30     # 北交所：43xxxx / 83xxxx / 87xxxx / 92xxxx

# ST 的价格笼子是 ±5%，比板块限制更窄。`filter_universe` 的 isST 列 + 名称
# 双信号在**进篮子之前**就把它剔掉了，但 `price_limit` 仍要认它——
# 否则 ST 的一字板（封在 ±5%）会被当成「没封板」而漏放。
LIMIT_ST = 0.05

# 涨跌停判定的价格容差，单位：**元**。
#
# 交易所的涨跌停价是 `round(prev_close × (1 ± limit), 2)`——四舍五入到**分**，
# 所以它与未取整值 `prev × (1 ± limit)` 的差**恒不超过半分钱**。
# 于是「收盘价 == 交易所涨跌停价」这件事，可以精确地写成：
#
#     |close − prev × (1 ± limit)| <= 0.005
#
# 不去复刻交易所的取整（numpy 的 `round` 是**银行家舍入**，`round(11.385, 2)`
# 给 11.38 而不是 11.39），因为一旦取整方式和交易所差一分钱，判据就会
# 在最关键的边界上出错——而 600825.SH 2026-10-08 的真实涨停价就是 11.39。
# 用「未取整值 + 半分钱容差」既等价，又不受舍入模式影响。
#
# 取 0.005 而不是更大：再大就会把「差一分钱没到涨停价」（**买得进**）也误杀，
# 让过滤器自己变成新的偏差源，且方向与修复目标相反。
PRICE_TOL = 0.005
# 浮点比较的相对松弛：0.005 在 float64 上实际是 0.005000000000000782，
# 直接用 `<= PRICE_TOL` 会把**恰好**半分的真实涨停价判成不是涨停。
FLOAT_SLACK = 1e-9


def price_limit(code: str) -> float:
    """按股票代码前缀判定当日涨跌幅限制（10% / 20% / 30%）。

    前缀判定而非按交易所字段：日线 parquet 里没有「板块」列，而代码前缀
    是稳定的（300/301 创业板、688/689 科创板、43/83/87/92 北交所）。
    未知前缀一律按主板 10%——最保守的一档，宁可少剔不可多剔。
    """
    head = str(code or "").split(".")[0]
    if head[:3] in ("300", "301", "688", "689"):
        return LIMIT_GROWTH
    if head[:2] in ("43", "83", "87", "92"):
        return LIMIT_BEIJING
    return LIMIT_MAIN


def _price_limit_many(codes) -> np.ndarray:
    """`price_limit` 的向量化版（逐元素调用同一份映射，不另立一套）。"""
    if isinstance(codes, str):
        return np.asarray(price_limit(codes), dtype=float)
    return np.asarray([price_limit(c) for c in codes], dtype=float)


def _as_float(x) -> np.ndarray:
    """标量或序列 → 1-D float 数组；None 变 NaN（缺列 / 缺值统一走 NaN 语义）。"""
    if x is None:
        return np.asarray([np.nan], dtype=float)
    return np.atleast_1d(np.asarray(x, dtype=float))


def _all_scalar(code, o, high, low, close, volume, amount, prev_close) -> bool:
    """入参是否全是标量（决定返回 `bool` 还是布尔数组）。

    不能用结果数组的 `ndim` 判断：内部会把标量广播成长度 1 的数组，
    那样标量调用也会返回 `array([True])`——`if can_buy(...)` 虽然还能用，
    但 `can_buy(...) is True` 会静默为假。
    """
    if not isinstance(code, str):
        return False
    return all(np.ndim(x) == 0 for x in (o, high, low, close, volume, amount, prev_close))


def _limit_close_flags(code, o, high, low, close, volume, amount, prev_close):
    """共用的前置判据 + 涨跌停/封板标记。

    返回 `(休市, 封板, 涨停收盘, 跌停收盘, prev 已知)` 五个布尔数组。

    **判据顺序是有意的**：封板排在涨跌停之前，因为封板是涨跌停的**真子集**
    （封在限价上的零价格发现 bar），先判它可以避免同一根 bar 被两条规则
    各记一次账（`unfillable` 计数必须是一笔一笔能对上的）。
    """
    o, high, low = _as_float(o), _as_float(high), _as_float(low)
    close = _as_float(close)
    vol = _as_float(volume)
    amt = _as_float(amount)
    prev = _as_float(prev_close)
    limit = np.atleast_1d(_price_limit_many(code)).astype(float)
    n = max(len(x) for x in (o, high, low, close, vol, amt, prev, limit))
    o, high, low, close = (np.broadcast_to(x, (n,)).astype(float) for x in (o, high, low, close))
    vol = np.broadcast_to(vol, (n,)).astype(float)
    amt = np.broadcast_to(amt, (n,)).astype(float)
    prev = np.broadcast_to(prev, (n,)).astype(float)
    limit = np.broadcast_to(limit, (n,)).astype(float)
    prev_known = np.isfinite(prev) & (prev > 0)

    # ① 停牌：零成交就没有价格发现。
    #    volume 在全库 283 只抽样里**从不为 NaN**，是唯一可靠的停牌信号。
    #    amount 只在「存在且已知」时参与判定——实测 59.4% 的末根 K 线 amount
    #    是 NaN、另有 8.8% 整列缺失，把缺失当成 0 会误杀三分之二的股票。
    amt_known = np.isfinite(amt)
    suspended = (vol <= 0) | (amt_known & (amt <= 0))

    # 涨跌停价：未取整的 `prev × (1 ± limit)`，容差半分钱（依据见 PRICE_TOL）。
    # 涨停取「≥ 限价」（达到即可），封板取「**恰好落在**限价上」——见 sealed 说明。
    tol = PRICE_TOL + FLOAT_SLACK
    band_up = prev * (1.0 + limit)
    band_dn = prev * (1.0 - limit)
    at_limit_up = prev_known & (close >= band_up - tol)
    at_limit_dn = prev_known & (close <= band_dn + tol)

    # ② 一字板（封板）：`open == high == low == close` **且** 收在涨跌停价上。
    #
    # 为什么必须同时要求「在限价上」：真实 A 股里全天价格一动不动的 bar 只有
    # 两类——封在限价上的板，或行情冻结的脏 bar。只判 `o == h == l == c`
    # 会把「全天平盘且日涨 0.05%」这种**买得进**的 bar 也一锅端，方向与修复
    # 目标正好相反（实测这类合成/低流动性形态占平盘 bar 的大头）。
    #
    # 「恰好等于」而非「越过」：越过限价的那一支是脏价格，不是封板——
    # 实测 600766.SH 单日 −96.43%（复牌跳空被源站记成价格跳空）若按「≤ 跌停价」
    # 判会被误当成跌停板，而它其实是 `test_implausible_return` 钉死的拒绝成交样本。
    flat = (o == high) & (high == low) & (low == close)
    st_up = prev * (1.0 + LIMIT_ST)
    st_dn = prev * (1.0 - LIMIT_ST)
    on_band = prev_known & (
        (np.abs(close - band_up) <= tol) | (np.abs(close - band_dn) <= tol)
        | (np.abs(close - st_up) <= tol) | (np.abs(close - st_dn) <= tol))
    sealed = flat & on_band
    return suspended, sealed, at_limit_up, at_limit_dn, prev_known


def can_buy(code, open, high, low, close, volume, amount=None, prev_close=None) -> bool:
    """**收盘这一笔买不买得进**（输入只用该日及该日之前的数据，无任何未来信息）。

    `code` 传字符串返回 `bool`，传序列返回布尔数组（横截面过滤走向量路径，
    与逐行标量路径共用同一份判据——见 tests/test_tradability）。

    ## 规则表

    | # | 判据 | 依据 |
    |---|---|---|
    | 1 | `volume <= 0` 或「`amount` 已知且 `<= 0`」 | 停牌：没有价格发现。amount 缺失**不等于** 0 |
    | 2 | `open == high == low == close` **且**收在涨跌停价上 | 一字板：全天零价格发现，收盘价不是可成交价 |
    | 3 | `close >= prev×(1+limit) − 0.005` | 涨停收盘：收盘集合竞价排不进队 |
    | 4 | `prev_close` 缺失 | **信号缺失不默认放行**：判不出就没法声称买得进 |

    涨停阈值：`prev × (1 + limit) ± 半分钱`，`limit` 按板块取 10% / 20% / 30%
    （`price_limit`）。**不复刻交易所的取整**，理由见 `PRICE_TOL`。
    规则 2 的封板价额外认 ±5% 一档（`LIMIT_ST`，ST 的价格笼子）。

    ## 为什么**不**在这里判「价格不可信」

    `MAX_PLAUSIBLE_RETURN(±40%)` 这道闸门已经存在两处，且都在**后复权价**上
    （`_factor_series` 与 `_next_bars`）——那里才是正确的口径。在**不复权价**
    上再判一次会把每一个合法的除权日打成不可成交：10 送 10 在不复权价上是
    −50%，而那一天**照常交易**（`tests/test_exrights_return.py` 钉死了这一点）。
    脏价格照样拦得住：`test_implausible_return` 的 600766.SH −96.43% 走的是
    后复权收益率那道闸门，与本函数无关。

    ## 为什么买不进也要剔除，而不是记一笔

    实测（2026-04-09~2026-09-30，mom20 top-10 涨篮子）信号日一字板样本占
    1.83%，次日收益均值 +0.82%；其余样本只有 +0.11%。**表观超额里有一块
    来自根本成交不了的交易。**
    """
    scalar = _all_scalar(code, open, high, low, close, volume, amount, prev_close)
    suspended, sealed, at_limit_up, _, prev_known = _limit_close_flags(
        code, open, high, low, close, volume, amount, prev_close)
    ok = ~(suspended | sealed | at_limit_up) & prev_known
    return bool(ok[0]) if scalar else ok


def can_sell(code, open, high, low, close, volume, amount=None, prev_close=None) -> bool:
    """**收盘这一笔卖不卖得掉**——`can_buy` 的对称问题。

    `ENTRY_LAG=1` 在 D+2 收盘卖；若 D+2 是一字跌停，跌停价上全是卖单、
    买单进不去，**你也卖不掉**。回测却会按跌停价记一笔成交——
    与买入端完全对称的偏乐观。

    规则 1/2/3/5 与 `can_buy` 相同（停牌、价格不可信、一字板、prev 缺失）；
    规则 4 换成「`close <= round(prev×(1−limit), 2) + 0.005`」——**跌停收盘卖不掉**。

    涨停收盘则**卖得掉**（早盘持筹者挂卖单能成交），所以两侧规则必须分开写：
    把买入端判据原样套到卖出端，会误杀所有涨停票的持仓变现能力。
    """
    scalar = _all_scalar(code, open, high, low, close, volume, amount, prev_close)
    suspended, sealed, _, at_limit_dn, prev_known = _limit_close_flags(
        code, open, high, low, close, volume, amount, prev_close)
    ok = ~(suspended | sealed | at_limit_dn) & prev_known
    return bool(ok[0]) if scalar else ok


def buyable_series(merged: pd.DataFrame, code: str) -> pd.Series:
    """单只股票**整条时间轴**上的「当日收盘买不买得进」（向量化，一次算完）。

    与 `FactorPanel` 的因子一次算完同理：逐日重算会把 5447 × 540 次调用
    变成瓶颈。`prev_close` 取该股自身时间轴上前一根，绝不跨股票取。
    """
    close = merged["close"].astype(float)
    prev = close.shift(1)
    has_amount = "amount" in merged.columns
    return pd.Series(
        can_buy(code,
                merged["open"].to_numpy(float) if "open" in merged.columns else np.nan,
                merged["high"].to_numpy(float) if "high" in merged.columns else np.nan,
                merged["low"].to_numpy(float) if "low" in merged.columns else np.nan,
                close.to_numpy(float),
                merged["volume"].to_numpy(float) if "volume" in merged.columns else np.nan,
                merged["amount"].to_numpy(float) if has_amount else None,
                prev.to_numpy(float)),
        index=merged.index, name="buyable")


def _min_samples(window: int, ratio: float = MIN_SAMPLE_RATIO) -> int:
    """窗口内**至少**要有多少个有效样本。

    必须**向上取整**：判据是「≥ 窗口的 2/3」，20 × 2/3 = 13.33，
    `int(13.33)` = 13 比判据松；`ceil` 才是 13.33 的含义。
    21 × 2/3 = 14 恰好整除，所以这个取整错误一直没被发现。
    """
    return int(np.ceil(window * ratio))


def market_return_series(ret_map: dict) -> pd.Series:
    """等权市场日收益 = 各股日收益的横截面均值。

    IVOL 需要它做时序回归（个股收益对市场收益），所以必须**遍历全部股票**才能
    得到——这是 FactorPanel 必须走两趟的原因，见 build_panel。
    """
    if not ret_map:
        return pd.Series(dtype=float)
    frame = pd.DataFrame({c: s for c, s in ret_map.items() if s is not None and len(s)})
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.mean(axis=1, skipna=True).dropna()


def _safe_ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """安全比值：分母为 0 → NaN；结果非有限 → NaN。**绝不返回 inf。**

    ## 为什么必须挡

    除以 0 的比值是「无定义」，不是「无穷大」。写成 `inf` 会一路活到
    `factor.cross_section_z` 里的 `scores.std()`——inf 进 std 就得到 inf/nan，
    **那一天该因子的整个横截面塌成 0**，排序信号凭空消失，而且不崩不报，只是
    那一列从此没有区分度。这类静默污染最难发现。

    真实触发场景：`vsur = volume / 前20日均量`，停牌股连续 20 日零成交时
    分母就是 0（实测 102 只抽样里 6 只命中，`000029.SZ` 有 429 天）。

    NaN 才是诚实的表示——下游 `notna` / `isfinite` 会正常把它剔除；
    inf 不会，它会一路传播。
    """
    out = num / den.replace(0.0, np.nan)
    return out.where(np.isfinite(out), np.nan)


def _factor_series(merged: pd.DataFrame, mkt: pd.Series | None = None) -> pd.DataFrame:
    """单只股票的**全历史**因子序列（向量化，一次算完）。

    ## 量价数据处理纪律（《因子投资：方法与实践》3.1.2 节）

    1. **停牌日不得填 0**：A 股停牌日的行情文件里 close 是上一日收盘，算出收益率为
       0。但「收益为 0」不等于「这天没波动」——**填 0 会系统性低估波动率**。
       这里把零成交（volume == 0）的日子标为缺失，不参与波动率与回归计算。
       （动量类不受影响：基期取上一个非停牌日的价格，逻辑上通顺。）
    2. **最少交易日**：窗口内有效样本须 ≥ 窗口的 2/3 才计算，否则整段作缺失。
       滥用停牌的公司若用三五个点硬算，得到的波动率偏差极大且无从察觉。
    3. **复牌首日异常值压缩**：长期停牌复牌不设涨跌停，实测出现过单日 +593.72%。
       日收益率钳到 ±10% 再进入波动率与 IVOL——否则一次复牌就能毁掉整段估计。
    """
    close = merged["close"].astype(float)
    adj = merged.get("adjust_factor")
    if adj is not None:
        # 只在**因子列本身缺失**时退回原价，绝不在 adj_close 为 NaN 时回填。
        # attach_factor 打的 NaN 表示「该段复权基准未知」（见 adjust.UNKNOWN_SCALE_MIN_FACTOR），
        # 回填 close 就等于把编造值请回来——001914.SZ 实测会在首事件日造出
        # +491% 的假收益，然后凭这个动量进入用户榜单。
        adj = pd.to_numeric(adj, errors="coerce").fillna(1.0)
        adj_close = close * adj
    else:
        adj_close = close

    volume = merged["volume"].astype(float) if "volume" in merged.columns else None
    prior_mean = volume.shift(1).rolling(20).mean() if volume is not None else None

    # 书 3.8 节异常换手率：mean(turn, 21) / mean(turn, 252)
    # 长窗口用**覆盖率门槛**而不是 pandas 默认的「一根不缺」——理由见
    # `MIN_TURN_COVERAGE` 上方注释（缺陷 26）。短窗口（21 日）保持默认：
    # 样本本来就小，放宽会真的改变因子值，而那不是这次要修的东西。
    turnratio = np.nan
    if "turn" in merged.columns:
        t = merged["turn"].astype(float)
        short = t.rolling(TURN_RATIO_SHORT).mean()
        long = t.rolling(
            TURN_RATIO_LONG,
            min_periods=int(TURN_RATIO_LONG * MIN_TURN_COVERAGE),
        ).mean()
        turnratio = _safe_ratio(short, long) - 1.0

    ret_raw = adj_close.pct_change()
    # ±40% 之外不存在合法单日收益（涨跌停上限 + 源站把缩股记成跳空的已知缺陷）。
    # 这里**作废**而不是钳制：钳制会把 +462% 变成 +40%，动量仍是虚高的，
    # 且「数据有问题」这件事被永久掩盖。作废 → mom20 变 NaN →
    # 该股自然退出横截面（`build_predictions` 丢弃 score 为 NaN 的行）。
    ret_raw = ret_raw.mask(ret_raw.abs() > MAX_PLAUSIBLE_RETURN)
    suspended = (volume <= 0) if volume is not None else pd.Series(False, index=merged.index)
    # 真实波动只认真正发生过的交易
    ret_real = ret_raw.where(~suspended)
    # 波动率与 IVOL 的**唯一**输入：同时剔停牌、压复牌极值。
    # 两者都不可省——2026-10-11 审计发现：
    #   · 只压极值不剔停牌 → 停牌日的 0 灌进方差，系统性**低估**波动率
    #     （且是方向性错误，不是保守偏差）
    #   · 只剔停牌不压极值 → 单日 +593% 进 std，把整段波动率抬一个量级
    #     （实测 vol20 被抬到 1.317，是未复牌基线 0.0002 的 6000 倍）
    ret_clamped = ret_real.clip(-RESUME_CLAMP, RESUME_CLAMP)

    vol_window = 20
    vol20 = ret_clamped.rolling(
        vol_window, min_periods=_min_samples(vol_window)).std(ddof=0)

    # 复牌日口径的动量（书 3.1.2 第 2 点：**复牌首日**异常值压缩）
    #
    # 为什么不用 `_safe_ratio(adj_close, adj_close.shift(20)) - 1`：
    # 那个写法在数学上等价，但**输入是原始价比值**。复牌首日没有涨跌停，
    # 实测单日 +593.72% 会原样进窗口，把 20 日动量推到数倍量级。
    # 排序只认序不认幅度，于是一只刚复牌暴涨的股票会被排到最前——
    # 不管它真实的基本面。而 `mom20` 的 IC 是 -0.0357（t=-9.40，1821 日实测）：
    # mom20 越高、次日越差。复牌极值不只是噪声，
    # 它是把策略送到**它最确定会亏的那一端**。
    #
    # ## 为什么只压**复牌日**，不是所有超过 10% 的日子
    #
    # 创业板 ±20% 的涨停是真实、可交易、有信息的事件。
    # 全局 `clip(±10%)` 会把它压成 +10%，从而**系统性削平所有信号**——
    # `tests/test_implausible_return.py::test_factor_layer_still_keeps_legitimate_limit_up_moves`
    # 正是钉这一条，它拦住了第一版实现（那一版压得太宽）。
    #
    # 三层口径，各管各的，互不替代：
    #   >±40%      数据不可信 → **作废**（NaN，该股退出横截面）
    #   复牌首日    合法但任意 → **压到 ±10%**
    #   普通交易日  真实涨跌停 → **不动**
    resume_day = (~suspended) & (suspended.shift(1).fillna(False).astype(bool))
    ret_resume_clamped = ret_real.where(~resume_day,
                                        ret_real.clip(-RESUME_CLAMP, RESUME_CLAMP))
    ret_mom = ret_resume_clamped.fillna(0.0)

    def _chain_momentum(window):
        """过去 `window` 日的连乘收益 - 1。输入是**复牌日压缩后**的日收益。"""
        return ((1.0 + ret_mom).rolling(window, min_periods=_min_samples(window))
                .apply(np.prod, raw=True) - 1.0)

    out = pd.DataFrame({
        "mom20": _chain_momentum(20),
        "rev5": -_chain_momentum(5),
        "vol20": vol20,
        "vsur": (_safe_ratio(volume, prior_mean)
                 if prior_mean is not None else np.nan),
        "turn20": (merged["turn"].astype(float).rolling(20).mean()
                   if "turn" in merged.columns else np.nan),
        # 书 3.8 节的异常换手率：近 21 日均换手 ÷ 近 252 日均换手。
        # 与 `turn20`（水平值）**并存**，不替代——子代理审计认为水平值
        # 「混入市值暴露」，但 `turn` 源站口径本就是换手率百分比、已尺度无关，
        # 该前提站不住。比值究竟更好还是只是把信号压平，让样本外赛马裁决。
        "turnratio": turnratio,
        # RSI（Wilder 1978，14 日）。用**后复权**价算——不复权价在除权日跳空，
        # 会造出假的暴跌/暴涨污染分子分母（书 3.1.2：一律用后复权）。
        # 向量化实现 `aplus.factor.rsi_series`，与标量 `rsi` 逐位一致（有测试钉死）。
        # 真实横截面实测与 mom20 的 Spearman 相关 +0.8648，是动量的有界平滑版本；
        # 是否值得当独立因子只能靠样本外赛马裁决，不靠推理。
        "rsi": rsi_series(adj_close),
        "ret_clamped": ret_clamped,
    })

    # ── 特质性波动率 IVOL ──
    # 口径（《因子投资：方法与实践》5.3 节）：用过去 21 个交易日个股超额收益率对
    # 因子收益率做时序回归，取**残差收益率的波动率**。
    # 我们没有 FF3 的 SMB（需市值），用市场模型（对市场收益回归）——即 Ang et al.
    # (2006) 的原始定义，同样是「剔除系统性后的残差波动」。
    if mkt is None or len(mkt) == 0:
        out["ivol"] = np.nan
        return out

    m = pd.Series(mkt).reindex(merged["date"].to_numpy())
    m.index = merged.index
    m = m.astype(float)
    r = ret_clamped
    # 口径与实盘慢路径**共用** `aplus.factor.ivol_series`——两份实现曾相差约 4%，
    # 足以改变横截面排序（见 tests/test_backtest.py::test_fast_path_equals_original_slow_path）。
    #
    # 事故（2026-10-11，本轮最严重）：此处原写
    #     out["ivol"] = ivol_series(r, m).iloc[-1]
    # `r`/`m` 是**全历史**序列，`.iloc[-1]` 取的是**数据末端**的 ivol，
    # 再由 pandas 按索引把标量广播给窗口内**每一天**。
    # 等价于「回测里任意历史日的 ivol = 数据末端的 ivol」——整条时间轴前视泄露。
    # 该 bug 不崩、不报 nan、每列都有区分度，只靠「污染未来看 D 是否变」才抓得到。
    # 现在是**逐点序列**，与 mom20/rev5/vol20 同样按行对齐，无任何广播。
    out["ivol"] = ivol_series(r, m)
    return out


class FactorPanel:
    """窗口内所有预测日的横截面原料（每只股票只算一次因子）。

    存的是「窗口内每个日期对应的那一行」——即已按 as-of 定位好的取值，
    不是全历史副本，内存 ≈ 5447 × 540 × 8 × 4B ≈ 94 MB。
    """

    def __init__(self, daily_map: dict, factors_map: dict | None, dates: list,
                 mkt: pd.Series | None = None):
        self.dates = list(dates)
        self.codes: list[str] = []
        self._idx: list[np.ndarray] = []          # 每只股票在窗口各日的行号（-1 = 无数据）
        self._vals: list[np.ndarray] = []         # 对应的因子 + 携带列取值
        self.mkt = mkt

        date_arr = np.asarray(self.dates, dtype="U10")
        last_dates: list[np.ndarray] = []        # 每只股票在窗口各日的**真实**末根日期
        for code, daily in daily_map.items():
            if daily is None or len(daily) == 0:
                continue
            events = (factors_map or {}).get(code)
            merged = (attach_factor(daily, events)
                      if events is not None and len(events) else daily)
            merged = merged.sort_values("date", kind="mergesort")

            # 每个窗口日 D 在该股票时间轴上「≤D 的最后一行」
            idx = np.searchsorted(merged["date"].to_numpy(dtype="U10"), date_arr,
                                  side="right") - 1
            idx[idx < 0] = -1

            factors = _factor_series(merged, mkt=mkt)
            carry = pd.DataFrame({
                "volume": (merged["volume"].astype(float)
                           if "volume" in merged.columns else np.nan),
                "isST": (pd.to_numeric(merged["isST"], errors="coerce")
                         if "isST" in merged.columns else np.nan),
                "listed_days": np.arange(1, len(merged) + 1, dtype=float),
                # 当日收盘买不进（停牌 / 一字板 / 涨停收盘 / 价格不可信）。
                # 整段向量化一次算完，与因子同一趟扫过，不在过滤时回查原始日线。
                "buyable": buyable_series(merged, code).to_numpy(float),
            })
            values = pd.concat([factors, carry], axis=1)
            cols = FACTOR_COLUMNS + CARRY_COLUMNS

            safe = np.where(idx < 0, 0, idx)
            self._idx.append(idx)
            self._vals.append(values[cols].to_numpy(dtype="float64")[safe])
            # 真实末根日期：退市/长期停牌股的最后一根 K 线可能停在数年前，
            # 绝不能拿「预测日本身」冒充（见 cross_section 的事故记录）。
            self.dates_axis = merged["date"].to_numpy(dtype="U10")
            last_dates.append(self.dates_axis[safe])
            self.codes.append(code)

        self._idx_mat = np.vstack(self._idx) if self._idx else np.zeros((0, len(self.dates)), int)
        self._vals_mat = (np.stack(self._vals) if self._vals
                          else np.zeros((0, len(self.dates), len(FACTOR_COLUMNS) + len(CARRY_COLUMNS))))
        self._last_dates = (np.stack(last_dates) if last_dates
                            else np.empty((0, len(self.dates)), dtype="U10"))

    def cross_section(self, i: int, names_map: dict | None = None,
                      min_history: int = 41) -> pd.DataFrame:
        """第 i 个预测日的横截面（未过滤），列结构与 cross_rows_from_map 一致。"""
        names_map = names_map or {}
        idx = self._idx_mat[:, i]
        vals = self._vals_mat[:, i, :]
        keep = idx >= 0
        # listed_days = 该股截至该日已有多少根 K 线（-1 行时为 0）
        listed = np.where(keep, vals[:, len(FACTOR_COLUMNS) + 2], 0.0)
        keep &= listed >= min_history

        codes = np.asarray(self.codes, dtype=object)[keep]
        # last_date 必须是该股**真实**的末根 K 线日期，不能拿预测日冒充。
        #
        # 事故（2026-10-11）：此前这里写死 `np.full(..., self.dates[i])`，
        # 于是 `filter_universe` 的 `max_stale_days=30` 在回测路径上
        # **完全是空操作**——退市股（最后交易日停在 2023 年）与长期停牌股
        # 照常进入横截面并被选中。实测某日跌篮子 10 只里 9 只是已退市股，
        # 它们没有次日 bar → `actual_pct` 全是 NaN →
        # 「篮子平均收益」实际是**剩下那一只**的收益，
        # 再被 `.mean()` 静默跳过 NaN 放大 → 多空价差虚报到 **+9.84%**。
        #
        # 一个被自己文档里的过滤器保护不了的过滤器，等于没有。
        last_date = self._last_dates[keep, i] if self._last_dates.size else \
            np.full(int(keep.sum()), self.dates[i], dtype=object)
        rows = pd.DataFrame({
            "code": codes,
            "name": [names_map.get(c, c) for c in codes],
            **{c: vals[keep, j] for j, c in enumerate(FACTOR_COLUMNS)},
            # **NaN 是「未知」，不是 0（不是 ST）**——不许在这里折成 0。
            # 曾经写成 `np.where(np.isnan(...), 0, ...)`，于是末根被降级源削掉
            # isST 的股票（2026-10-09 那批，3342 只）被折成「不是 ST」直接放行，
            # 实测 2026-10-11 的 20 只推荐里有 6 只处在 ST 筛查盲区。
            # 未知该怎么处置由 `filter_universe` 决定（它会按日计数上报），
            # 面板只负责**如实传递**——这里一折，后面就再也看不到真相了。
            "isST": vals[keep, len(FACTOR_COLUMNS) + 1],
            "volume": vals[keep, len(FACTOR_COLUMNS)],
            "listed_days": listed[keep].astype(int),
            "last_date": last_date,
            # 该股在该根 K 线收盘买不进。`filter_universe` 据此剔除「涨停 /
            # 一字板 / 停牌」的票——推荐一只买不进的股票是无效输出。
            "buyable": vals[keep, len(FACTOR_COLUMNS) + 3] > 0,
        })
        return rows


def build_panel(store, start: str, end: str, names_map: dict | None = None,
                daily_map: dict | None = None, factors_map: dict | None = None) -> FactorPanel:
    """构建窗口面板。daily_map / factors_map 传入可复用（赛马只建一次）。

    **两趟扫描**：IVOL 需要等权市场收益序列，而市场收益必须遍历**全部**股票才能
    得到——单只股票算因子时市场序列尚不存在。故第一趟收集个股日收益，第二趟才算因子。
    代价是 `attach_factor` 跑两遍（约把面板耗时翻倍），换来的是内存有界：
    缓存全部 adjusted close 需要约 190 MB，叠加面板本身容易顶到 GB 级。
    """
    if daily_map is None:
        if factors_map is None:
            from .snapshot import load_factors_map

            factors_map = load_factors_map(store)
        daily_map = {p.stem: store.read_daily(p.stem)
                     for p in sorted((store.root / "master" / "daily").glob("*.parquet"))}
    # 入参防线：`end` 是**开区间**（回测窗口语义，[start, end)）。
    # start == end 会得到 dates == []，上层 cross_section(0) 抛 IndexError，
    # 实盘横截面静默变成 0 行（2026-10-11 事故）。空面板必须当场炸，
    # 不能让问题飘到几百行之外再冒出来。
    #
    # 类型也要先查：`start` 是 NaN（float）时 `start >= end` 自己就抛
    # `TypeError: '>=' not supported between instances of 'float' and 'str'`，
    # **这道防线自己先炸成一句看不懂的错**（2026-10-11 上午，
    # 该 TypeError 曾让整个 `[update]` 步骤失败 → 5460 只票一只没更新，
    # 而上层记成 `partial` 后继续用陈旧数据出单）。
    for label, v in (("start", start), ("end", end)):
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"build_panel 的 {label} 必须是 'YYYY-MM-DD' 字符串，"
                f"收到 {v!r}（类型 {type(v).__name__}）。"
                f"它多半来自 `df['date'].max()` 在空表/全 NaN 列上返回的 NaN。"
            )
    if start >= end:
        raise ValueError(
            f"build_panel 的窗口是半开区间 [start, end)，要求 start < end，"
            f"收到 start={start!r} end={end!r}（相等时窗口为空 → 上层 IndexError）。"
        )
    ref = max(daily_map.values(), key=len)
    dates = [d for d in sorted(ref["date"].unique()) if start <= d < end]

    # 第一趟：收集个股日收益 → 等权市场收益序列（IVOL 的回归基准）
    ret_map: dict = {}
    for code, daily in daily_map.items():
        if daily is None or len(daily) == 0:
            continue
        events = (factors_map or {}).get(code)
        merged = (attach_factor(daily, events)
                  if events is not None and len(events) else daily)
        merged = merged.sort_values("date", kind="mergesort")
        close = merged["close"].astype(float)
        adj = merged.get("adjust_factor")
        if adj is not None:
            adj = pd.to_numeric(adj, errors="coerce").fillna(1.0)
            adj_close = close * adj
        else:
            adj_close = close
        ret = adj_close.pct_change().clip(-RESUME_CLAMP, RESUME_CLAMP)
        ret_map[code] = pd.Series(ret.to_numpy(), index=merged["date"].to_numpy())
    mkt = market_return_series(ret_map)
    del ret_map

    return FactorPanel(daily_map, factors_map, dates, mkt=mkt)
