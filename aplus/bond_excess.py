"""转债低价轮动的**样本外超额口径**——「策略失效」还是「市场 beta」的切分。

## 要回答的问题

`scripts/bond_param_race.py` 的裁决是：低价轮动 h=5/top10/cost=0.003 训练段
+38.45%、测试段 -13.18%，**0/15 组参数通过样本外验收**。但这个裁决只看了
**绝对收益**。测试段同��参照股票 600000.SH 约 -29.85%——如果市场同期在崩，
策略的绝对亏损可能只是 beta，而不是选券失效。本模块提供超额口径来切分。

## 基准构造（在看到任何收益之前定死）

**基准 = EW-POOL：等权持有当期合格宇宙的全池**，与策略跑在**同一套代码**里
（`run_rotation` 引擎 + 同一网格 + 同一成交口径 + 同一费用公式），唯一差别是
选券一步：策略取 cheap TopN，基准取全部。选这个基准的理由固定如下，不因结果
调整：

1. 策略是**这个池的子集**。若池本身亏钱，策略的绝对亏损首先是 beta；只有
   策略净值跑赢池净值，才是真正的选券 alpha。这是对本模块结论最不利（最难跑出
   正超额）的基准构造方式：等权、不做任何权重优化、不挑基准。
2. 与策略共用引擎 → 网格、成交（信号日 D 收盘决策 / D+1 开盘买 / 持有 h 根后
   收盘卖）、跳空强赎代理、空池跳过、换手计费全部逐项同义，不存在口径差。
3. 费用用**策略自己的换手公式**计（见 `run_rotation`），EW-POOL 只把 `top_n`
   换成当期池规模本身，因此两边的费率口径完全同构。

超额同时给三个口径，主判据是**几何超额**（复利可比）：
- `excess_total` = (1+策略净值)/(1+基准净值) - 1  ← 主判据
- `excess_ann`   = 上式按同一 n_days 折算年化
- `excess_per_period` = 策略 net_per_period - 基准 net_per_period（算术，与
  bond_param_race 的 `exc_per_period` 口径一致）

## 无未来数据（本模块的硬红线）

信号只在信号日 D 收盘可得，成交价一律 D+1。`build_horizon_cache` /
`attach_bond_factors` 的所有列都是**纯滚动/点值**（cheap 是固定先验阈值
115.0 减收盘价的点值，不是全样本统计量），本模块**不新增任何横截面标准化**——
选券就是按 D 当天的 `cheap` 点值排序，不用任何全窗口均值/标准差。
`tests/test_bond_excess.py` 用「截断数据重跑结果不变」+「追加未来 K 线不改
变历史信号」两条把这个性质钉死。

`pool_period_distribution` 是**事后市场环境描述**（测试段转债宇宙的收益
分布），只在报告里当背景数字打印，**绝不进入任何选券或仓位决策**。
"""
import pandas as pd

from .bond_horizon import (
    TRADING_DAYS_PER_YEAR,
    _horizon_return_np,
    _pool_of,
)

# ---------------------------------------------------------------------------
# 口径常量（在看到收益前固定）
# ---------------------------------------------------------------------------

#: 基准名。写死进 JSON 报告，报告读者不需要读代码就知道基准是什么。
BENCHMARK_NAME = "ew_pool"
BENCHMARK_DESC = (
    "等权持有当期合格宇宙全池（信号日 D 收盘定池，D+1 开盘买入，持有 h 根后收盘卖），"
    "与策略同一网格/同一费用公式；策略净收益 − 基准净收益 = 超额"
)

#: 默认参数组 = bond_param_race 的已知口径，用来复核既有数字。
DEFAULT_TOP_N = 10
DEFAULT_HOLD_DAYS = 5
DEFAULT_COST = 0.003

#: 样本外切分参数，与 scripts/bond_param_race.py 完全一致（不可在此改）。
TRAIN_RATIO = 0.6
EMBARGO_DAYS = 5


# ---------------------------------------------------------------------------
# 选券器（策略臂 / 基准臂的唯一差别就在这里）
# ---------------------------------------------------------------------------

def select_cheap_top_n(top_n: int):
    """策略选券：cheap 深度 TopN，并列按代码序（确定性，与模块内策略一致）。

    注意这里**只**用池里每行自带的 `cheap` 点值（信号日 D 收盘的
    max(0, 115-close)），不接收任何外部统计量——全窗口均值/标准差若出现在
    这里即为未来数据泄露。
    """
    def _select(pool):
        return [r[0] for r in sorted(pool, key=lambda r: (-r[1], r[0]))[:top_n]]
    return _select


def select_full_pool(pool):
    """基准选券：整个池等权（EW-POOL）。

    池的顺序由 `_pool_of` 的遍历决定（frames 的插入序），与信号日无关，
    因此选券是确定的；收益是等权平均，与顺序无关。
    """
    return [r[0] for r in pool]


# ---------------------------------------------------------------------------
# 共用引擎（策略臂与基准臂都必须走这里，这是「同口径」的机械保证）
# ---------------------------------------------------------------------------

def run_rotation(cache: dict, start: str, end: str, select,
                 hold_days: int = DEFAULT_HOLD_DAYS,
                 cost: float = DEFAULT_COST) -> dict:
    """在给定缓存上跑一条轮动净值；`select(pool) -> [code…]` 是唯一的臂差异。

    网格、成交、跳空代理、空池跳过、换手计费逐项对齐
    `aplus.bond_horizon.cheap_rotation_backtest`（该函数是本引擎的上游模型，
    tests 里钉死二者逐期同义）。本函数**不读** `select` 之外的任何未来信息。

    费用：与策略同式 turnover_ratio = (新进 + 退出) / (2 × max(目标只数,
    当期只数, 上期只数)) × cost；首期建仓只付买侧 = cost/2。
    EW-POOL 臂的目标只数就是当期池规模本身（denom = max(|当期|, |上期|)）。
    """
    if hold_days < 1:
        raise ValueError(f"hold_days 必须 ≥1，收到 {hold_days}")
    if cost < 0:
        raise ValueError(f"cost 不得为负，收到 {cost}")
    cal = [d for d in cache["calendar"] if start <= d < end]
    frames = cache["frames"]

    periods, prev_picks = [], []
    for i in range(0, len(cal), hold_days):
        if i + hold_days > len(cal) - 1:
            break  # 末段不足 hold_days 的不完整期丢弃（与策略一致）
        day = cal[i]
        pool = _pool_of(cache, day)
        if not pool:
            continue  # 空池期跳过（等价于空仓）
        picks = list(select(pool))
        if not picks:
            continue
        pos_of = {r[0]: r[2] for r in pool}
        rets = [_horizon_return_np(frames[p][1], frames[p][2], pos_of[p], hold_days)
                for p in picks]
        gross = float(sum(rets) / len(rets))
        n_in = len(set(picks) - set(prev_picks))
        n_out = len(set(prev_picks) - set(picks))
        denom = max(len(picks), len(prev_picks))
        cost_drag = (n_in + n_out) / (2.0 * denom) * cost if prev_picks else cost / 2.0
        periods.append({
            "day": day, "cal_idx": i, "gross": gross, "cost": float(cost_drag),
            "net": float(gross - cost_drag), "turnover_names": n_out,
            "n_picks": len(picks), "picks": list(picks),
        })
        prev_picks = picks

    res = _summarize(periods, hold_days)
    res["config"] = {"start": start, "end": end, "hold_days": hold_days,
                     "cost": cost, "arm": getattr(select, "__name__", "select")}
    return res


def _summarize(periods: list, hold_days: int) -> dict:
    """连乘复利 + 覆盖交易日年化 + 最大回撤（口径与 bond_horizon 一致）。"""
    out = {
        "n_periods": len(periods), "n_days": 0, "gross_per_period": 0.0,
        "cost_per_period": 0.0, "net_per_period": 0.0, "total_return": 0.0,
        "annualized_return": 0.0, "max_drawdown": 0.0, "avg_turnover_names": 0.0,
        "periods": periods,
    }
    if not periods:
        return out
    n_days = (periods[-1]["cal_idx"] + hold_days) - (periods[0]["cal_idx"] + 1) + 1
    gross_s = pd.Series([p["gross"] for p in periods])
    cost_s = pd.Series([p["cost"] for p in periods])
    net_s = pd.Series([p["net"] for p in periods])
    nav = (1.0 + net_s).cumprod()
    total = float(nav.iloc[-1] - 1.0)
    peak, mdd = 1.0, 0.0
    for v in nav:
        peak = max(peak, float(v))
        mdd = max(mdd, float((peak - v) / peak))
    rebalances = periods[1:]
    out.update({
        "n_days": int(n_days),
        "gross_per_period": float(gross_s.mean()),
        "cost_per_period": float(cost_s.mean()),
        "net_per_period": float(net_s.mean()),
        "total_return": total,
        "annualized_return": float((1.0 + total) ** (TRADING_DAYS_PER_YEAR / n_days) - 1.0)
        if total > -1 else -1.0,
        "max_drawdown": mdd,
        "avg_turnover_names": float(pd.Series([p["turnover_names"] for p in rebalances]).mean())
        if rebalances else 0.0,
        "mean_n_picks": float(pd.Series([p["n_picks"] for p in periods]).mean()),
    })
    return out


# ---------------------------------------------------------------------------
# 超额
# ---------------------------------------------------------------------------

def excess_of(strategy: dict, benchmark: dict) -> dict:
    """策略净收益 − 基准净收益（三个口径同时给，主判据是几何超额）。

    几何超额 = (1+策略总收益)/(1+基准总收益) - 1，可连乘、可跨段比较。
    年化用**策略的 n_days** 折算，保证两臂窗口一致。
    """
    s_tot, b_tot = strategy["total_return"], benchmark["total_return"]
    geo = (1.0 + s_tot) / (1.0 + b_tot) - 1.0 if (1.0 + b_tot) > 0 else None
    n_days = strategy["n_days"]
    ann = None
    if geo is not None and n_days > 0 and geo > -1:
        ann = float((1.0 + geo) ** (TRADING_DAYS_PER_YEAR / n_days) - 1.0)
    return {
        "excess_total": geo,
        "excess_ann": ann,
        "excess_per_period": float(strategy["net_per_period"] - benchmark["net_per_period"]),
        "strategy_total": s_tot, "benchmark_total": b_tot,
        "strategy_ann": strategy["annualized_return"],
        "benchmark_ann": benchmark["annualized_return"],
        "n_days": n_days,
        "n_periods": strategy["n_periods"],
        "benchmark_n_periods": benchmark["n_periods"],
        "verdict": ("超额为正（跑赢市场）" if geo is not None and geo > 0
                    else "超额为负（真实失效）"),
    }


# ---------------------------------------------------------------------------
# 事后环境描述（只进报告，绝不进决策）
# ---------------------------------------------------------------------------

def selection_skill(cache: dict, strategy: dict, hold_days: int) -> dict:
    """策略选中的券 vs 当期池的**毛**收益差（费前，剔除费用口径干扰）。

    对两个基准同时给：
    - `vs_pool_mean`：池均值。EW-POOL 基准的费前对应物——等权组合的毛收益就是
      池均值，这是主判据。
    - `vs_pool_median`：池中位数。对右尾离群值稳健的参照，回答「策略是否至少
      跑赢了典型转债」。

    `t_stat` = mean / (std / sqrt(n))，n 为期数；|t| < 2 视为与 0 不可区分。
    这解释了为什么「负超额」不能直接读成「显著跑输」。

    警告：事后统计，**不进入任何决策**，只用于解释结论的统计强度。
    """
    import numpy as np

    frames = cache["frames"]
    rows = []
    for p in strategy["periods"]:
        pool = _pool_of(cache, p["day"])
        pos_of = {r[0]: r[2] for r in pool}
        allr = np.array([_horizon_return_np(frames[cd][1], frames[cd][2], pos_of[cd],
                                           hold_days)
                         for cd in pos_of], dtype=float)
        picks = np.array([_horizon_return_np(frames[k][1], frames[k][2], pos_of[k],
                                            hold_days)
                          for k in p["picks"]], dtype=float)
        rows.append((float(picks.mean()), float(allr.mean()),
                     float(np.median(allr)), float(allr.max()), int(len(allr))))
    if not rows:
        return {"n_periods": 0, "note": "无期"}
    df = pd.DataFrame(rows, columns=["pick", "mean", "median", "max", "n_pool"])

    def _leg(base):
        d = (df["pick"] - df[base]).to_numpy(dtype=float)
        t = (float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))
             if len(d) > 1 and d.std(ddof=1) > 0 else None)
        return {"per_period": float(d.mean()), "t_stat": t,
                "win_rate": float((d > 0).mean())}

    return {
        "n_periods": int(len(df)),
        "mean_pool_size": float(df["n_pool"].mean()),
        "pool_max_per_period": float(df["max"].mean()),
        "vs_pool_mean": _leg("mean"),
        "vs_pool_median": _leg("median"),
        "note": "费前事后统计；t 绝对值 <2 视为与 0 不可区分",
    }


def pool_period_distribution(cache: dict, start: str, end: str,
                             hold_days: int) -> dict:
    """测试段转债宇宙每期收益的横截面分布——**事后市场环境描述**。

    警告：本函数用到了整段数据的结果，输出**仅**用于报告里刻画「测试段市场
    长什么样」。任何选券/仓位决策都不得调用它（策略臂与基准臂都不调）。
    """
    cal = [d for d in cache["calendar"] if start <= d < end]
    frames = cache["frames"]
    all_rets = []
    for i in range(0, len(cal), hold_days):
        if i + hold_days > len(cal) - 1:
            break
        pool = _pool_of(cache, cal[i])
        if not pool:
            continue
        for code, _cheap, pos in pool:
            all_rets.append(_horizon_return_np(frames[code][1], frames[code][2],
                                               pos, hold_days))
    s = pd.Series(all_rets, dtype=float)
    if s.empty:
        return {"n_obs": 0, "note": "空池"}
    return {
        "n_obs": int(len(s)),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "std": float(s.std(ddof=0)),
        "p10": float(s.quantile(0.10)),
        "p25": float(s.quantile(0.25)),
        "p75": float(s.quantile(0.75)),
        "p90": float(s.quantile(0.90)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
        "frac_negative": float((s < 0).mean()),
        "note": "事后市场环境描述，不进入任何决策",
    }


def equity_reference(daily: pd.DataFrame, start: str, end: str) -> dict | None:
    """参照股票的简单区间收益（[start, end) 内 首收 → 末收），仅作背景。"""
    if daily is None or daily.empty:
        return None
    df = daily.sort_values("date", kind="stable")
    seg = df[(df["date"] >= start) & (df["date"] < end)]
    if len(seg) < 2:
        return None
    c0, c1 = float(seg["close"].iloc[0]), float(seg["close"].iloc[-1])
    return {"start_close": c0, "end_close": c1, "total_return": c1 / c0 - 1.0,
            "n_days": int(len(seg)), "first": str(seg["date"].iloc[0]),
            "last": str(seg["date"].iloc[-1])}


# ---------------------------------------------------------------------------
# 窗口切分（与 bond_param_race 完全一致，隔离带不可调）
# ---------------------------------------------------------------------------

def split_segments(cache: dict, train_ratio: float = TRAIN_RATIO,
                   embargo_days: int = EMBARGO_DAYS) -> dict:
    """训练/测试切分 + 全窗口。右端点用「段末次日」以覆盖段内最后一天。"""
    from .evolve_race import split_window

    cal = cache["calendar"]
    train_dates, test_dates = split_window(cal, train_ratio, embargo_days)
    return {
        "train": (train_dates[0], _next_day(train_dates[-1])),
        "test": (test_dates[0], _next_day(test_dates[-1])),
        "full": (cal[0], _next_day(cal[-1])),
        "train_raw": train_dates, "test_raw": test_dates,
    }


def _next_day(day: str) -> str:
    return (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")