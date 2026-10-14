"""实盘出单必须支持**低频调仓 + 缓冲区**，否则测出来的结论落不了地。

## 事故（2026-10-11 20:30）

独立验证（2019-2023 干净数据，24 组）给出的结论是决定性的：

| 纯反转 `{mom20:-1.0}` | 年成本 | 年超额 | 净 | t |
|---|---|---|---|---|
| 日频 K=1 | 246.3% | +77.4% | −168.9% | 6.32 |
| **月频 K=21** | 35.8% | +113.4% | **+77.6%** | **9.19** |
| **季频 K=63** | 12.2% | +117.9% | **+105.7%** | **11.17** |

同一组权重、同样的信号，**只改调仓频率，净值从 −169% 变成 +106%**。

而 `scripts/run_daily.py::step_predict_and_allocate` 里：
**没有任何持仓概念**——每天 `build_predictions` 从零选 Top10，
昨天的持仓在字典里根本不存在。于是实盘被迫日频全换，
而日频的成本是 246%~351%（5 万本金 / 单只 5000 元时 5 元最低佣金是主导项）。

**测出「月频更好」却让实盘继续日频全换，等于测了个寂寞。**

## 设计上的三条硬约束

1. **默认行为必须逐位不变**（`rebalance_every=1` + 无缓冲）。
   否则今天已验证的名单会静默改变，而没人知道变了。
2. **非调仓日必须原样沿用上期篮子**，且**不产生任何成本**——
   低频若被重复收费，频率对比完全失去意义。
3. **沿用的持仓必须重新过一遍当日的可交易性**。
   上期榜上的票今天可能停牌、可能一字涨停。
   拿一份三天前的名单直接发给用户，等于推他买不进的股票
   （2026-10-11 已经踩过一次：600825.SH 新华传媒，连续 9 个一字涨停）。
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from aplus.rebalance import buffer_hold, fill_to_top_n, is_rebalance_day
from aplus.predict import build_predictions, filter_universe
from aplus.snapshot import COLUMNS


def _universe(n=120, seed=1):
    rng = __import__("numpy").random.default_rng(seed)
    rows = []
    for i in range(n):
        rows.append({
            "code": f"{600000+i:06d}.SH", "name": f"股{i}",
            "mom20": float(rng.normal(0, 0.4)), "rev5": float(rng.normal(0, 0.05)),
            "vol20": float(abs(rng.normal(0.02, 0.01))), "vsur": float(abs(rng.normal(1, 0.3))),
            "turn20": float(abs(rng.normal(2, 0.5))), "turnratio": float(rng.normal(0, 0.1)),
            "ivol": float(abs(rng.normal(0.02, 0.005))), "rsi": float(rng.uniform(10, 90)),
            "isST": 0, "volume": float(rng.uniform(1e5, 1e7)),
            "listed_days": int(rng.integers(60, 900)),
            "last_date": "2026-10-09", "buyable": bool(rng.random() > 0.03),
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def _ranked(u, weights):
    from aplus.factor import composite_score
    sc = composite_score(u, weights)
    return list(u.assign(_s=sc).dropna(subset=["_s"]).sort_values("_s", ascending=False)["code"])


# ---------- 调仓日判定 ----------

def test_every_trading_day_is_a_rebalance_day():
    dates = pd.bdate_range("2026-10-01", periods=10).strftime("%Y-%m-%d").tolist()
    assert all(is_rebalance_day(dates, d, 1) for d in dates)


def test_monthly_rebalance_hits_once_per_21_days():
    dates = pd.bdate_range("2026-10-01", periods=63).strftime("%Y-%m-%d").tolist()
    hits = [d for d in dates if is_rebalance_day(dates, d, 21)]
    assert len(hits) == 3, f"63 个交易日 / 21 应命中 3 次，实得 {len(hits)}"


def test_quarterly_rebalance_hits_once_per_63_days():
    dates = pd.bdate_range("2026-10-01", periods=126).strftime("%Y-%m-%d").tolist()
    hits = [d for d in dates if is_rebalance_day(dates, d, 63)]
    assert len(hits) == 2


# ---------- 缓冲区 ----------

def test_buffer_keeps_names_still_inside_the_band():
    ranked = [f"S{i}" for i in range(50)]
    held = ["S0", "S1", "S2"]
    out = buffer_hold(held, ranked, top_n=10, buffer_multiple=3.0)
    assert "S0" in out and "S2" in out, "仍在 30 名宽带内的持仓被误卖"


def test_buffer_drops_names_that_fell_out_of_the_band():
    ranked = [f"S{i}" for i in range(50)]
    held = ["S45"]
    assert buffer_hold(held, ranked, top_n=10, buffer_multiple=3.0) == []


def test_fill_to_top_n_backfills_to_exactly_n():
    ranked = [f"S{i}" for i in range(50)]
    kept = buffer_hold(["S0"], ranked, top_n=10, buffer_multiple=3.0)
    filled = fill_to_top_n(kept, ranked, 10)
    assert len(filled) == 10
    assert filled[0] == "S0", "保留下来的必须排在最前"


def test_buffer_is_not_identity():
    """缓冲区必须真的改变篮子——恒等参数等于没有这个参数。"""
    ranked = [f"S{i}" for i in range(50)]
    naive = ranked[:10]
    with_buffer = fill_to_top_n(buffer_hold(["S12", "S13"], ranked, 10, 3.0), ranked, 10)
    assert with_buffer != naive, "缓冲区与朴素取前 10 完全相同 → 参数无效"


# ---------- 非调仓日沿用 ----------

def test_selection_is_deterministic():
    """同一天的横截面必须给出**逐位相同**的篮子。

    这一点是低频调仓的前提：非调仓日要沿用上期篮子，
    调仓日之间的选股结果就不能有任何随机性，否则「沿用」无从谈起，
    复查时也对不上账。
    """
    u = filter_universe(_universe(120), "2026-10-09")
    a_up, a_dn = build_predictions(u, top_n=10, bottom_n=10, weights={"mom20": -1.0})
    b_up, b_dn = build_predictions(u, top_n=10, bottom_n=10, weights={"mom20": -1.0})
    assert a_up["code"].tolist() == b_up["code"].tolist()
    assert a_dn["code"].tolist() == b_dn["code"].tolist()


def test_reused_basket_is_refiltered_for_tradability():
    """沿用的持仓必须重新过当日可交易性。

    上一期榜上的票今天可能一字涨停或停牌。直接照抄三天前的名单，
    等于把 2026-10-11 那次（600825.SH 连续 9 个一字涨停）重演一遍。
    """
    u = _universe(60, seed=3)
    # 把当期最好的 5 只标成买不进
    from aplus.factor import composite_score
    sc = composite_score(u, {"mom20": -1.0})
    top5 = list(u.assign(_s=sc).sort_values("_s", ascending=False)["code"])[:5]
    u.loc[u["code"].isin(top5), "buyable"] = False
    kept = filter_universe(u, "2026-10-09")
    assert not set(top5) & set(kept["code"]), "买不进的票没被剔掉"


def test_missing_previous_basket_falls_back_to_fresh_selection():
    """首日没有上期持仓时必须退回正常选股，不能返回空。"""
    up, _ = build_predictions(filter_universe(_universe(120), "2026-10-09"),
                              top_n=10, bottom_n=10, weights={"mom20": -1.0})
    assert len(up) == 10
