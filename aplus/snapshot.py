"""横截面快照：主库 → 全市场因子横截面（predict 的直接输入）。

- 历史不足 min_history（因子窗口需要 41 天）的股票剔除
- as_of 支持「以历史某日为快照日」——回测复用同一条代码路径（防未来函数的根）
- cross_rows_from_map 接受预加载的内存缓存（回测全区间只读一次盘）

## `buyable` 为什么在这条路径上（2026-10-11）

`aplus/panel.py` 早就实现了 `can_buy`，回测横截面也早就带上了 `buyable`，
但**实盘走的是本文件**，而这里的 `COLUMNS` 没有这一列——
于是 `filter_universe` 看不到它。

后果不是理论上的：2026-10-11 的实盘名单第一是 `600825.SH 新华传媒`，
连续 **9 个一字涨停**、综合分 7.56、二十日动量 +141.9%。
**收盘价就是涨停价，排单进不去。** 回测已经不为这种票记账，
实盘却还在推它——刚修好的防线在真正的出单口形同虚设。

判据直接复用 `panel.buyable_series`，**不在这里重写一份**：
两条路径对同一根 K 线给出不同判断，就是本轮修掉的每一类事故的共同形状。
"""
import pandas as pd

from .adjust import attach_factor
from .factor import compute_factors
from .panel import market_return_series
from .panel import buyable_series

FACTOR_WINDOW_MIN = 41  # mom20 需要 21 天 + vol20 需要 21 天，取交集上限

COLUMNS = ["code", "name", "mom20", "rev5", "vol20", "vsur", "turn20", "turnratio",
           "ivol", "rsi", "isST", "volume", "listed_days", "last_date", "buyable"]

FACTOR_COLS = ["mom20", "rev5", "vol20", "vsur", "turn20", "turnratio", "ivol", "rsi"]


def _ret_map_for(daily_map: dict, factors_map: dict | None = None) -> dict:
    """daily_map → {code: 后复权日收益序列}，供 `market_return_series` 用。

    ## 必须先附复权因子

    市场收益是 IVOL 的回归自变量，用不复权价算出来的市场序列会让 beta 偏，
    进而让 ivol 偏。实测这一处就造成快/慢路径 ivol 相差约 **4%**
    （0.01843 vs 0.02241），足以改变横截面排序。
    面板那边用的是 `build_panel` 内部**已附因子**的 `ret_map`，
    这里若偷懒不附，两条路径就不是同一个因子了。

    同时剔除「当日无成交」：停牌日收益为 0 参与均值会把市场收益系统性下偏
    （与 `aplus.panel.market_return_series` 的取数口径保持一致）。
    """
    factors_map = factors_map or {}
    out = {}
    for code, daily in daily_map.items():
        if daily is None or daily.empty:
            continue
        s = daily.copy()
        s["date"] = s["date"].astype(str)
        ev = factors_map.get(code)
        if ev is not None and not ev.empty:
            s = attach_factor(s, ev)
        adj = s["close"] * s.get("adjust_factor", 1.0)
        r = adj.pct_change()
        if "volume" in s.columns:
            r = r.where(s["volume"].astype(float) > 0)
        r.index = pd.Index(s["date"].to_numpy())
        out[code] = r.dropna()
    return out


def cross_rows_from_map(daily_map: dict, as_of: str | None = None,
                        min_history: int = FACTOR_WINDOW_MIN, names_map: dict | None = None,
                        factors_map: dict | None = None) -> pd.DataFrame:
    names_map = names_map or {}
    factors_map = factors_map or {}
    # IVOL 需要等权市场日收益（个股收益对市场收益回归）。单股算不出市场，
    # 所以先遍历一遍 daily_map 把它求出来——口径与 `aplus.panel.market_return_series`
    # 一致，否则回测与实盘的 ivol 不是一个东西。
    mkt = market_return_series(_ret_map_for(daily_map, factors_map))
    rows = []
    for code, daily in daily_map.items():
        if daily is None or daily.empty:
            continue
        sliced = daily if as_of is None else daily[daily["date"] <= as_of]
        if len(sliced) < min_history:
            continue
        events = factors_map.get(code)
        if events is not None and not events.empty:
            sliced = attach_factor(sliced, events)  # 除权保护：动量/波动走后复权口径
        factors = compute_factors(sliced, mkt=mkt)
        last = sliced.iloc[-1]
        # **NaN 是「未知」，不是 0（不是 ST）。**
        # 原先这里写 `int(isst) if pd.notna(isst) else 0`（2026-10-11 的回归），
        # 与 `panel.py` 的同一处折法一样，让「缺数据」冒充「不是 ST」。
        # 两处是同一件事的两份实现——面板改成如实传递，慢路径必须同步，
        # 否则同一个未知在两条路径上会得到两种说法。
        isst = last.get("isST")
        isst = float(isst) if pd.notna(isst) else float("nan")
        # 可交易性：判据复用 panel.buyable_series，取**快照日那一根**。
        # 注意 sliced 已经过 attach_factor（附了复权因子列），而 buyable_series
        # 只读 open/high/low/close/volume —— 复权不改变日内 OHLC 的相对关系，
        # 且这里刻意用**未复权**价判涨跌停：交易所价格笼子只对不复权价成立。
        buyable = bool(buyable_series(sliced, code).iloc[-1])
        rows.append({
            "code": code,
            "name": names_map.get(code, code),
            **factors.to_dict(),
            "isST": isst,
            "volume": float(last.get("volume", 0) or 0),
            "listed_days": len(sliced),
            "last_date": str(last["date"]),  # 退市股冻结检测
            "buyable": buyable,
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def load_factors_map(store) -> dict:
    """全市场因子事件表（内存缓存用；无因子的股票不在 map 里，按 1.0 处理）。"""
    return {
        path.stem: store.read_factor(path.stem)
        for path in sorted((store.root / "master" / "factor").glob("*.parquet"))
    }


def panel_window_for(all_dates: list[str], as_of: str, width: int = 2
                     ) -> tuple[str, str]:
    """算出「**包含** as_of 的 `build_panel` 合法窗口」。

    ## 三个必须同时满足的约束

    1. `build_panel` 的 `end` 是**开区间**（回测窗口语义）。要包含 as_of，
       `end` 必须取 as_of 之后的一个日期，故用 `next_day()` 推到下一天。
    2. `as_of` 可能**不是交易日**（实盘 cron 在周末/节假日照常触发，
       2026-10-11 就是周日）。此时必须回退到 `<= as_of` 的最后一个交易日，
       否则窗口为空、当天没有名单。
    3. 窗口必须有界。因子在全历史上算（`_factor_series` 吃整条时间轴），
       `dates` 只决定横截面轴的宽度；给全历史会得到
       5447 × 1700 × 12 × 8B ≈ **889 MB** 的矩阵，直接顶爆内存。

    `width=2` 是最小可验证宽度：至少两根，面板轴上有两天可断言锚定正确。
    """
    from datetime import date, timedelta

    candidates = [d for d in all_dates if d <= as_of]
    if not candidates:
        raise ValueError(
            f"as_of={as_of!r} 早于全部交易日（最早 {min(all_dates)!r}），无横截面可算。"
        )
    target = candidates[-1]
    left = candidates[-width] if len(candidates) >= width else candidates[0]
    nxt = date.fromisoformat(target) + timedelta(days=1)
    return left, nxt.isoformat()


def build_cross_section(
    store,
    as_of: str | None = None,
    min_history: int = FACTOR_WINDOW_MIN,
    names_map: dict | None = None,
) -> pd.DataFrame:
    """实盘与回测**必须用同一个因子实现**（2026-10-11 决策）

    
    此前实盘走 `cross_rows_from_map`（逐股重算），回测走 `FactorPanel`（向量化）。
    两条路径各自实现一份因子，只要窗口、`ddof`、裁剪阈值、样本下限有一处不同，
    **同一个权重在回测和实盘里就不是同一个因子**。
    
    实测过一次：`ivol` 两条路径相差约 4%~21%，足以改变横截面排序。
    逐字段去对齐是打地鼠——新增因子时必然再漂一次。
    因此实盘**直接复用面板**：单一实现，口径不可能再分叉。
    `cross_rows_from_map` 保留为**参照实现**，由
    `tests/test_backtest.py::test_fast_path_equals_original_slow_path` 持续对照。
    """
    from .panel import build_panel

    daily_map = {
        path.stem: store.read_daily(path.stem)
        for path in sorted((store.root / "master" / "daily").glob("*.parquet"))
    }
    if not daily_map:
        return pd.DataFrame(columns=COLUMNS)
    factors_map = load_factors_map(store)

    ref = max(daily_map.values(), key=lambda d: len(d) if d is not None else 0)
    all_dates = sorted(ref["date"].astype(str).unique())
    if as_of is None:
        as_of = all_dates[-1]
    start, end = panel_window_for(all_dates, as_of)
    panel = build_panel(store, start, end, names_map=names_map,
                        daily_map=daily_map, factors_map=factors_map)
    if not panel.dates:
        return pd.DataFrame(columns=COLUMNS)
    # 末尾锚定到目标日：实盘要的是 as_of，不是窗口里的任意一天
    target = [d for d in panel.dates if d <= as_of][-1]
    i = panel.dates.index(target)
    df = panel.cross_section(i, names_map, min_history)
    cols = [c for c in COLUMNS if c in df.columns]
    if "name" not in df.columns and names_map:
        df["name"] = df["code"].map(names_map).fillna(df["code"])
    return df.reindex(columns=cols)
