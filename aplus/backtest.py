"""走前回测：历史区间上重放「预测 → 次日验证」闭环。

与每日实盘走完全相同的代码路径（cross_rows_from_map → filter_universe →
build_predictions → verify_predictions），唯一的差别是 as_of 被钉在每个历史
交易日上——这正是「回测是迭代抓手」的根：调参前后跑同一段代码，结果可比。
"""
import math

import numpy as np
import pandas as pd

from .adjust import hfq_close
from .panel import MAX_PLAUSIBLE_RETURN, build_panel
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


def _next_bars(daily_map: dict, codes: list, after_date: str,
               entry_lag: int = ENTRY_LAG, factors_map: dict | None = None,
               adj_cache: dict | None = None) -> pd.DataFrame:
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

    ## 成交窗口内的可交易性闸门：**只判停牌**

    信号日那一侧由 `filter_universe` 用 `panel.buyable_series` 管住
    （一字板 / 涨停收盘 / 停牌，见 `aplus.panel.can_buy`）。成交窗口内部
    这里**只判停牌**，不判涨停/跌停——这不是遗漏，是口径：

    本函数记录的收益走的是**后复权价**（`hfq_close`），而涨跌停限价只对
    **不复权价**成立。在不复权价上判涨跌停，会把除权除息日当成限价：

        10 送 10   → 不复权 −50%，看起来像「跌停价以下」
        每 10 股派 1 元（20 → 19）→ 不复权 −5%，**恰好等于 ST 的跌停价**

    `tests/test_exrights_return.py` 钉死了这两笔必须照常成交
    （它们当天都在正常交易），`tests/test_implausible_return.py` 也钉死了
    「创业板 +20% 的涨停必须保留」。用错误的理由得到正确的结论同样是 bug。

    停牌是唯一与价格口径无关的判据：`volume == 0` 时**根本没有价格**，
    买进卖出都没有标的物。实测停牌次日收益均值 −1.02%（复牌跳空），
    把停牌日按上一日收盘价记一笔持仓是凭空造出来的。

    `panel.can_buy` / `can_sell` 里的涨停、跌停、封板三条规则在
    `tests/test_tradability.py` 有逐条测试，并在信号日路径上生效；
    **D+1 / D+2 当天才封板的成交尚未拦截**——已知缺口，见交付报告。
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
            events = factors_map.get(code)
            has_adj = events is not None and len(events) > 0
            sorted_df = df.sort_values("date", kind="stable")
            closes = (hfq_close(sorted_df, events).to_numpy(float) if has_adj
                      else sorted_df["close"].to_numpy(float))
            hit = (sorted_df["date"].to_numpy(dtype="U10"), closes, has_adj,
                   _raw_bars(sorted_df))
            adj_cache[key] = hit
        dates, closes, has_adj, raw = hit
        pos = int(np.searchsorted(dates, after_date, side="right"))   # 首根 > D
        entry = pos + entry_lag - 1
        exit_i = entry + 1
        if entry < 0 or exit_i >= len(dates):
            continue                            # 卖价不存在 → 不成交，不拿更早的凑
        if not _bars_tradable(raw, entry, exit_i):
            continue                            # 买入或卖出那根停牌 → 不成交
        entry_px, exit_px = float(closes[entry]), float(closes[exit_i])
        # 价格不可信 → 不成交。详见 MAX_PLAUSIBLE_RETURN 的事故记录。
        # 先查价格有效性再做除法：0 或负价会让比值变成 inf/负数，比算出来
        # 再判断更危险。
        if entry_px <= 0 or exit_px <= 0:
            continue
        if abs(exit_px / entry_px - 1.0) > MAX_PLAUSIBLE_RETURN:
            continue                            # ±40% 之外不存在合法单日收益
        rows.append({"code": code, "close": exit_px,
                     "prev_close": entry_px, "adjusted": has_adj})
    return pd.DataFrame(rows)


def _raw_bars(sorted_df: pd.DataFrame) -> np.ndarray:
    """单只股票的 (volume, amount) 矩阵——成交闸门唯一需要的两列。

    刻意**不**缓存 OHLC：涨跌停判断在不复权价上不成立（见 `_next_bars`
    的「成交窗口内的可交易性闸门」），留在这里只会诱使后来者把它接上。
    全市场每只多缓存 5 列 float64 会让 adj_cache 凭空多占 ~190 MB。
    """
    def col(name):
        return (np.asarray(sorted_df[name].astype(float)) if name in sorted_df.columns
                else np.full(len(sorted_df), np.nan))

    return np.vstack([col("volume"), col("amount")])


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


def evaluate_weights(cross_sections: dict, weights=None, top_n: int = 10,
                     bottom_n: int = 10, dates: list | None = None,
                     rebalance_every: int = 1,
                     buffer_multiple: float | None = None) -> pd.DataFrame:
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
    """
    daily_map = cross_sections.get("_daily_map") or {}
    market = cross_sections.get("_market") or {}
    entry_lag = cross_sections.get("_entry_lag", ENTRY_LAG)
    cost = cross_sections.get("_cost", STOCK_COST_DEFAULT)
    factors_map = cross_sections.get("_factors_map") or {}
    adj_cache = cross_sections.setdefault("_adj_cache", {})
    unadjusted = cross_sections.setdefault("_unadjusted", {})
    use_dates = dates if dates is not None else cross_sections.get("_dates", [])
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
        bars = _next_bars(daily_map, preds["code"].tolist(), day, entry_lag=entry_lag,
                          factors_map=factors_map, adj_cache=adj_cache)
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
        return pd.DataFrame(columns=["pred_date", "code", "direction", "actual_pct",
                                     "result", "market_pct", "cost"])
    result = pd.concat(out, ignore_index=True)
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
