"""可转债 T+0 日内策略回测（路线 A 第二阶段，PLATFORM.md §7.5）。

股票侧终局发现（2026-10-11 凌晨迭代日志）：日频选股的盘中超额 +0.14~0.34%/天
真实存在，但 h=1（当日开盘买收盘卖）在 T+1 制度下违法。可转债 T+0 使该口径
合法——本模块把股票 walk_forward 的结构在转债上复刻，口径差异全部是制度性的：

- 收益口径 = **D+1 的 open→close**（当日买当日卖，不是 close 对 close）
- 因子集 mom20/rev5/vol20/vsur 复用 aplus.factor（转债无 turn 列、无复权——
  强赎/转股是价格事件，原始价即正确口径），新增 cheap（低价深度，115 下方
  债底保护效应；用深度而非 0/1：二元列的 z-score 分组内并列会让 TopN 退化成
  固定代码序队列，测的是 10 只特定债而不是低价效应）
- universe 过滤：is_active 摘牌检测（30 自然日）+ 最新一根 volume×close > 500 万
  流动性门槛 + 上市满 41 天（41 = 因子窗口 21 天上限 × 新债波动隔离的保守取整，
  与股票侧 FACTOR_WINDOW_MIN 一致）
- 随机对照（random_seed）：同漏斗、同费用、同口径，只在最后一步「选券」换成
  随机——这是股票侧调查中把 +0.29% 盘中边际钉死为真实存在的关键武器
"""
import random

import pandas as pd

from .factor import composite_score, momentum, reversal, volatility, volume_surge
from .sources.bond_convert import is_active

BOND_FACTOR_NAMES = ("mom20", "rev5", "vol20", "vsur", "cheap")
CHEAP_THRESHOLD = 115.0
LIQUIDITY_MIN_VALUE = 5_000_000.0  # 最新一根 volume×close 门槛（元口径随 volume 单位，横截面一致）
MIN_LISTED_BARS = 41  # 上市满 41 天（K 线根数代理——上市日无独立数据源）
DEFAULT_COST_PER_TRADE = 0.0015  # 佣金+滑点合计约万 15/边（转债无印花税）

WALK_COLUMNS = [
    "pred_date", "code", "trade_date", "open", "close", "intraday", "score",
    "mom20", "rev5", "vol20", "vsur", "cheap",
]


def intraday_return(row) -> float:
    """T+0 日内收益：当日收盘卖 / 当日开盘买 - 1。"""
    return float(row["close"]) / float(row["open"]) - 1.0


def cheap_depth(price: float, threshold: float = CHEAP_THRESHOLD) -> float:
    """低价深度：threshold 下方深度（115-100 → 15.0），上方为 0（不惩罚）。

    用连续深度而非 0/1：二元列 z-score 后组内全并列，TopN 会退化成固定
    代码序的 10 只债（测量 bug，见模块 docstring）。
    """
    return max(0.0, threshold - float(price))


def bond_factors(daily: pd.DataFrame) -> pd.Series:
    """单只转债日线 → 因子向量（末根口径）。

    需 ≥21 行（mom20/vol20/vsur 窗口）；walk_forward 上游以 41 根门槛过滤。
    转债无 turn 列 → 无 turn20；无复权 → 原始 close 即因子口径。
    """
    close = daily["close"]
    return pd.Series({
        "mom20": momentum(close),
        "rev5": reversal(close, window=5),
        "vol20": volatility(close, window=20),
        "vsur": volume_surge(daily["volume"], window=20),
        "cheap": cheap_depth(float(close.iloc[-1])),
    })


def attach_bond_factors(daily: pd.DataFrame) -> pd.DataFrame:
    """向量化全历史因子列（回测性能路径：每债算一次，逐日切片零重复计算）。

    与 bond_factors 在任意截断点严格同义（tests/test_bond_backtest.py 钉死）——
    滚动窗口与 aplus.factor 标量函数逐项对齐；量能突变的前置均量为 0/NaN → NaN。
    """
    df = daily.sort_values("date", kind="stable").reset_index(drop=True).copy()
    close, volume = df["close"], df["volume"]
    pct = close.pct_change()
    prior_mean = volume.shift(1).rolling(20, min_periods=20).mean()
    vsur = (volume / prior_mean).where(prior_mean.notna() & (prior_mean != 0))
    df["mom20"] = close / close.shift(20) - 1.0
    df["rev5"] = -(close / close.shift(5) - 1.0)
    df["vol20"] = pct.rolling(20, min_periods=20).std(ddof=0)
    df["vsur"] = vsur
    df["cheap"] = (CHEAP_THRESHOLD - close).clip(lower=0.0)
    return df


def build_bond_cross_sections(bond_daily_map: dict, start: str, end: str) -> list:
    """逐信号日的合格宇宙横截面（权重无关——赛马多组权重只算一次）。

    每日入池条件（同股票侧 as-of 语义）：
    - 该债 date≤day 的 K 线 ≥41 根（上市满 41 天）
    - 最新一根距今 ≤30 自然日（is_active 摘牌/长停检测——过期因子不得入池，
      即使 D+1 有可执行 K 线）
    - 最新一根 volume×close > 500 万（流动性门槛）
    - 存在 D+1 K 线且 open/close/volume 均 >0（可成交；末日信号与坏数据剔除）
    返回 [{"day", "cross": DataFrame[因子], "next": {code: D+1 行}} ...]。
    """
    attached = {}
    for code, daily in (bond_daily_map or {}).items():
        if daily is None or daily.empty:
            continue
        a = attach_bond_factors(daily)
        attached[code] = (pd.Index(a["date"]), a)
    if not attached:
        return []
    # 交易日历取「末根最新」的帧（活跃全历史债）——退市长历史债不会霸占日历
    ref_code = max(attached, key=lambda c: (attached[c][0][-1], len(attached[c][1])))
    out = []
    for day in (d for d in attached[ref_code][0] if start <= d < end):
        rows, nexts = [], {}
        for code, (didx, a) in attached.items():
            pos = didx.searchsorted(day, side="right")
            if pos < MIN_LISTED_BARS:
                continue
            last = a.iloc[pos - 1]
            if not is_active(last["date"], as_of=day):
                continue
            if float(last["volume"]) * float(last["close"]) <= LIQUIDITY_MIN_VALUE:
                continue
            if pos >= len(a):
                continue  # 末日信号无 D+1（含摘牌后）
            nxt = a.iloc[pos]
            n_open, n_close, n_vol = float(nxt["open"]), float(nxt["close"]), float(nxt["volume"])
            if not (n_open > 0 and n_close > 0 and n_vol > 0):
                continue  # 坏数据/零成交不可成交
            rows.append({
                "code": code,
                "mom20": float(last["mom20"]), "rev5": float(last["rev5"]),
                "vol20": float(last["vol20"]), "vsur": float(last["vsur"]),
                "cheap": float(last["cheap"]),
            })
            nexts[code] = {"date": str(nxt["date"]), "open": n_open, "close": n_close,
                           "intraday": n_close / n_open - 1.0}
        if rows:
            out.append({"day": str(day), "cross": pd.DataFrame(rows), "next": nexts})
    return out


def bond_walk_forward(bond_daily_map: dict, start: str, end: str, weights: dict | None = None,
                      top_n: int = 10, random_seed: int | None = None,
                      cross_sections: list | None = None) -> pd.DataFrame:
    """转债 T+0 走前回测：信号日 D 因子排名 → D+1 开盘买收盘卖。

    weights 给因子权重（打分模式）；random_seed 给随机对照模式（同漏斗随机
    选券，score 置 NaN）——两者必须给且只给一个的语义：都不给即参数错误。
    cross_sections 可注入预构建横截面（赛马多组权重复用，结果与自建完全一致）。
    """
    if weights is None and random_seed is None:
        raise ValueError("weights 与 random_seed 至少给一个（random_seed 走随机对照口径）")
    rng = random.Random(random_seed) if random_seed is not None else None
    if cross_sections is None:
        cross_sections = build_bond_cross_sections(bond_daily_map, start, end)

    out = []
    for entry in cross_sections:
        cross, nexts = entry["cross"], entry["next"]
        if rng is not None:
            # 随机对照与打分模式同漏斗：因子不完整者同样剔除（宁缺毋滥）
            pool = cross.dropna(subset=list(BOND_FACTOR_NAMES))
            picked_codes = set(rng.sample(list(pool["code"]), min(top_n, len(pool))))
            picks = pool[pool["code"].isin(picked_codes)].copy()
            picks["score"] = float("nan")
        else:
            cross = cross.copy()
            cross["score"] = composite_score(cross, weights)
            cross = cross[cross["score"].notna()]
            picks = cross.sort_values("score", ascending=False).head(top_n).copy()
        for r in picks.itertuples(index=False):
            n = nexts[r.code]
            out.append({
                "pred_date": entry["day"], "code": r.code,
                "trade_date": n["date"], "open": n["open"], "close": n["close"],
                "intraday": n["intraday"], "score": float(r.score),
                "mom20": r.mom20, "rev5": r.rev5, "vol20": r.vol20,
                "vsur": r.vsur, "cheap": r.cheap,
            })
    return pd.DataFrame(out, columns=WALK_COLUMNS)


def bond_portfolio_sim(results: pd.DataFrame, equity: float = 1.0,
                       cost_per_trade: float = DEFAULT_COST_PER_TRADE) -> dict:
    """T+0 组合净值：每日权益 × 当日入选均值盘中收益 - 双边费用，复利。

    费用模型：佣金+滑点合计 cost_per_trade/边，一开一平收两次
    （转债佣金约万 0.5~2、无印花税，万 15/边是含滑点的保守口径）。
    """
    empty = {
        "n_days": 0, "n_trades": 0, "avg_daily_gross": 0.0, "avg_daily_net": 0.0,
        "total_return": 0.0, "annualized_return": 0.0, "max_drawdown": 0.0,
    }
    if results is None or results.empty or "intraday" not in results.columns:
        return empty
    daily = results.groupby("pred_date")["intraday"].mean().sort_index()
    n = len(daily)
    if n == 0:
        return empty
    net = equity * (daily - 2.0 * cost_per_trade)
    nav = (1.0 + net).cumprod()
    total = float(nav.iloc[-1] - 1.0)
    peak, mdd = 1.0, 0.0  # 峰值从初始资本 1.0 起算（与股票侧净值曲线语义一致）
    for v in nav:
        peak = max(peak, v)
        mdd = max(mdd, float((peak - v) / peak))
    return {
        "n_days": int(n),
        "n_trades": int(len(results)),
        "avg_daily_gross": float(daily.mean()),
        "avg_daily_net": float(net.mean()),
        "total_return": total,
        "annualized_return": float((1.0 + total) ** (250.0 / n) - 1.0) if total > -1 else -1.0,
        "max_drawdown": mdd,
    }
