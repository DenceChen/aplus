"""可转债低价因子的持有期经济学（路线 A 第三阶段，PLATFORM.md §7.5）。

二阶段结论（迭代日志 2026-10-11「第二阶段收盘」）：cheap 低价深度因子对随机
+0.091%/天是唯一存活方向，但按万 15/边双边 = -0.3%/次换手的费用模型，若每日
全换手，任何 ≤0.1% 量级的毛超额都养不起。转债无 T+1——**任何持有期合法**——
本模块回答：每 h 个交易日调仓一次的低价 TopN 轮动，换手摊薄后是否转正。

口径（写死，全模块一致；与二阶段 bond_backtest 的差异全部在此声明）：
- 收益口径 = **D+1 开盘买入，持有 h 根该债自身 K 线后收盘卖出**（贴近实际调仓：
  信号日 D 收盘后算因子，次日开盘成交；h=1 精确退化为二阶段的 D+1 open→close）
- 数据先于 h 根结束（强赎/摘牌）：按**末根收盘**卖出（摘牌代理，不引入幸存者
  剔除——末根收盘通常贴近强赎兑付价）
- 调仓网格：交易日历（「仍新鲜」的最长债锚定，同 build_bond_cross_sections 的
  ref 逻辑）每 hold_days 根一调；**末段不足 hold_days 的不完整期丢弃**；空池期
  跳过（等价于该期空仓）
- 费用模型：cost = 一次**完整换手**的双边费率（买+卖合计；万 15/边 → 0.003，
  万 1.5/边 → 0.0003）。每期按换手比例收：
  turnover_ratio = (新进只数 + 退出只数) / (2 × max(top_n, 当期只数, 上期只数))
  → 期费用 = turnover_ratio × cost；首期建仓 = cost/2（只付买侧）。
  对随机对照同样按其自身换手计费——低价池稳定 vs 随机近全换，费用差本身就是
  「换手摊薄」问题的一部分
- universe 过滤与二阶段同漏斗：≥41 根（as-of）/ is_active 30 自然日 / 末根
  volume×close > 500 万 / D+1 可成交。差异：cheap 只需 close，不要求 mom20/vsur
  等 20 日窗口因子有效（新债头 20 天也可入池——低价效应本就不需要动量历史）
- 每期收益**连乘**复利；净年化按覆盖交易日数（250/日历）折算
"""
import random

import numpy as np
import pandas as pd

from .bond_backtest import (
    LIQUIDITY_MIN_VALUE,
    MIN_LISTED_BARS,
    attach_bond_factors,
)
from .sources.bond_convert import is_active

TRADING_DAYS_PER_YEAR = 250

# 费率档位（cost = 一次**完整换手**的双边费率：买+卖合计）
# 默认取保守档而非乐观档：默认值是「不读 docstring 也会拿到」的那一个，
# 曾经默认 0.0003（低保费通道）而判定用 0.003（万 15/边）——同一个模块里两套
# 费率，任何漏传 cost 的调用方都会静默拿到乐观两倍的结果。宁可默认保守。
COST_WAN15 = 0.003    # 万 15/边 × 双边——平台保守口径（第三阶段主判定）
COST_WAN1_5 = 0.0003  # 万 1.5/边 × 双边——低保费通道敏感性档，不作判定


def horizon_return(daily_df: pd.DataFrame, signal_date: str, h: int) -> float | None:
    """单债持有期收益：D+1 开盘买入、持有 h 根自身 K 线后收盘卖出。

    信号日 D 收盘后决策 → 买入 = D 之后第一根 K 线的 open；卖出 = 自买入起
    第 h 根的 close（h=1 即二阶段 D+1 open→close 当日兑现）。
    数据先于 h 根结束 → 按末根 close 卖出（强赎/摘牌代理）。
    D 后无 K 线或买卖价 ≤0 → None（不可成交）。
    """
    if h < 1:
        raise ValueError(f"h 必须 ≥1，收到 {h}")
    df = daily_df.sort_values("date", kind="stable").reset_index(drop=True)
    dates = np.asarray(df["date"].tolist(), dtype="U10")
    pos = int(np.searchsorted(dates, signal_date, side="right"))
    if pos >= len(df):
        return None  # 无 D+1（末日信号或已摘牌）
    o = float(df["open"].iloc[pos])
    c = float(df["close"].iloc[min(pos + h - 1, len(df) - 1)])
    if not (o > 0 and c > 0):
        return None
    return c / o - 1.0


def _horizon_return_np(open_arr, close_arr, pos: int, h: int) -> float:
    """horizon_return 的 numpy 快路径（帧已按 date 排序、pos 为 D 后首根）。

    与 horizon_return 在同帧同信号日严格同义（tests/test_bond_horizon.py
    test_gross_equals_mean_of_horizon_returns 钉死等价）。
    """
    c = float(close_arr[min(pos + h - 1, len(close_arr) - 1)])
    o = float(open_arr[pos])
    return c / o - 1.0


def build_horizon_cache(bond_daily_map: dict, ref_code: str | None = None) -> dict:
    """attach 一次、全 h/种子/费率复用的缓存（1087 只 × 640 根只算一遍因子）。

    返回 {"calendar": [交易日…], "frames": {code: (dates, open, close, cheap)},
    "pools": {day: [(code, cheap, pos)…]}}——pools 惰性构建逐日记忆化。

    日历锚默认 = （末日, 根数）最大的帧（仍新鲜的满历史债，同二阶段 ref 逻辑）。

    **ref_code：钉死日历来源（纸面账户必须用）**
    一次性的回测无所谓「锚会漂」；但每天都要跑的纸面账户不行——参考债一旦停牌，
    它的数据就缺该交易日，日历随之少一天，**所有臂的调仓网格整体偏移**，相位
    锚点当场失效。钉住后由调用方把 code 存进 state 永久复用。
    钉住的 ref 若被摘牌/不再更新（缺失或落后于全池最新日）→ 自动回退到最新鲜的帧，
    并在返回里如实标记 ref_fallback / ref_fallback_reason，供体检发现网格位移。
    """
    frames = {}
    for code, daily in (bond_daily_map or {}).items():
        if daily is None or daily.empty:
            continue
        a = attach_bond_factors(daily)
        frames[code] = (
            np.asarray(a["date"].tolist(), dtype="U10"),
            a["open"].to_numpy(dtype=float),
            a["close"].to_numpy(dtype=float),
            a["volume"].to_numpy(dtype=float),
            a["cheap"].to_numpy(dtype=float),
        )
    calendar: list[str] = []
    cache = {"calendar": calendar, "frames": frames, "pools": {}}
    if not frames:
        return cache
    freshest = max(str(frames[c][0][-1]) for c in frames)
    ref = max(frames, key=lambda c: (str(frames[c][0][-1]), len(frames[c][0])))
    if ref_code:
        if ref_code not in frames:
            ref, reason = ref, "missing"
        elif str(frames[ref_code][0][-1]) < freshest:
            ref, reason = ref, "stale"
        else:
            ref, reason = ref_code, None
        if reason:
            cache["ref_fallback"] = True
            cache["ref_fallback_reason"] = reason
    cache["calendar_ref"] = ref
    cache["calendar"] = [str(d) for d in frames[ref][0]]
    return cache


def _pool_of(cache: dict, day: str) -> list:
    """day 的合格宇宙（二阶段同漏斗），返回 [(code, cheap, D后首根pos)…]。"""
    pools = cache["pools"]
    if day in pools:
        return pools[day]
    rows = []
    for code, (dates, o, c, v, cheap) in cache["frames"].items():
        pos = int(np.searchsorted(dates, day, side="right"))
        if pos < MIN_LISTED_BARS or pos >= len(dates):
            continue  # 上市不满 41 天 / 无 D+1 可成交（末日信号）
        if not is_active(str(dates[pos - 1]), as_of=day):
            continue  # 摘牌/长停（末根距今 >30 自然日）
        if v[pos - 1] * c[pos - 1] <= LIQUIDITY_MIN_VALUE:
            continue  # 流动性门槛
        if not (o[pos] > 0 and c[pos] > 0 and v[pos] > 0):
            continue  # 坏数据/零成交
        rows.append((code, float(cheap[pos - 1]), pos))
    pools[day] = rows
    return rows


def cheap_rotation_backtest(daily_map: dict, start: str, end: str,
                            top_n: int = 10, hold_days: int = 1,
                            cost: float = COST_WAN15, random_seed: int | None = None,
                            cache: dict | None = None) -> dict:
    """低价 TopN 轮动：每 hold_days 个交易日调仓一次，返回持有期经济学全套指标。

    - 持仓 = 当日 cheap 深度 TopN（并列按代码序，确定性）；random_seed 给随机
      对照模式（同漏斗同网格同费用口径，只换选券这一步）
    - 每期净收益 = 持有期毛收益 - 换手比例 × cost（口径见模块 docstring）
    - 净年化按覆盖交易日折算：n_days = 末期卖出日 - 首期买入日 + 1（含空仓日）
    返回 {"n_periods","n_days","gross_per_period","cost_per_period","net_per_period",
    "total_return","annualized_return","max_drawdown","avg_turnover_names",
    "periods":[{"day","cal_idx","gross","cost","net","turnover_names","n_picks","picks"}…]}
    """
    if hold_days < 1:
        raise ValueError(f"hold_days 必须 ≥1，收到 {hold_days}")
    if top_n < 1:
        raise ValueError(f"top_n 必须 ≥1，收到 {top_n}")
    if cost < 0:
        raise ValueError(f"cost 不得为负，收到 {cost}")
    if cache is None:
        cache = build_horizon_cache(daily_map)
    cal = [d for d in cache["calendar"] if start <= d < end]
    rng = random.Random(random_seed) if random_seed is not None else None

    periods, prev_picks = [], []
    for i in range(0, len(cal), hold_days):
        if i + hold_days > len(cal) - 1:
            break  # 末段不足 hold_days 的不完整期丢弃（收益期不可比）
        day = cal[i]
        pool = _pool_of(cache, day)
        if not pool:
            continue  # 空池期跳过（空仓）
        if rng is not None:
            codes = [r[0] for r in pool]
            picks = rng.sample(codes, min(top_n, len(codes)))
        else:
            picks = [r[0] for r in sorted(pool, key=lambda r: (-r[1], r[0]))[:top_n]]
        frames = cache["frames"]
        rets = [_horizon_return_np(frames[p][1], frames[p][2],
                                   next(r[2] for r in pool if r[0] == p), hold_days)
                for p in picks]
        gross = float(sum(rets) / len(rets))
        n_in = len(set(picks) - set(prev_picks))
        n_out = len(set(prev_picks) - set(picks))
        denom = max(top_n, len(picks), len(prev_picks))
        cost_drag = (n_in + n_out) / (2.0 * denom) * cost
        periods.append({
            "day": day, "cal_idx": i, "gross": gross, "cost": float(cost_drag),
            "net": float(gross - cost_drag), "turnover_names": n_out,
            "n_picks": len(picks), "picks": list(picks),
        })
        prev_picks = picks

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
    rebalances = periods[1:]  # 换手只数：首期建仓不计入平均
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
    })
    return out
