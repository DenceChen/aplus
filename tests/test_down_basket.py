"""跌篮子的度量：涨腿之外，跌腿必须**可测**，且能判断排序是不是只在单边有效。

## 现状的盲区

平台每天输出「10 涨 + 10 跌」，但 `rolling_stats` 只统计 `direction == "up"`：
`avg_up_return` / `up_excess_vs_market`。**跌腿完全没有数字**。

于是「我们能不能看空」这件事，从未被测量过。

## 为什么跌腿不只是「命中率好看」

跌腿没有直接收益——A 股散户基本融不到券，我们不做空。跌篮子的作用有两个：

1. **回避清单**：它说「这些票别碰」。价值 = 回避掉的亏损，即 `市场 − 该股收益`。
2. **排序的对照组**（更关键）：涨篮子和跌篮子来自**同一次排序的两端**。

## 广度 vs 单侧偏度——本测试文件的核心

两个新量，各自回答一个不同的问题（**注意：这两个数不能区分「单因子」与「真本事」**，
纯单因子排序同样有正的多空价差）：

    广度 breadth = up_excess + down_excess = E[r_up] − E[r_down]   ← 多空价差
    偏度 tilt   = up_excess − down_excess

**广度（多空价差）的价值：对大盘方向免疫。**
涨腿超额 `E[r_up] − E[r_mkt]` 在大盘上涨时会自动变大——选股毫无本事、
只是踩在 beta 上，也能得到一个漂亮的正数。多空价差把市场项消掉了，
衡量的才是排序本身把两端分开的能力。这也是它**必须做 t 检验**的原因：
`+0.03%` 这种数字在样本外已经被证明与随机不可区分。

**偏度的价值：披露市场暴露。**
`E[r_up] + E[r_down] − 2 E[r_mkt] ≠ 0` 说明排序两端**合起来**就偏离了市场，
即名单整体含有 beta / 市值等系统性暴露。纯 beta 排序正是如此。
这不是 bug，是用户按名单建仓时**必须知情**的风险。

两者都不花额外算力（同一批预测的另一半），却把「涨腿 +0.03%」这种
无法与随机区分的数字，推进成可以下统计检验的命题。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.evolve_race import breadth_tstat, down_excess_tstat
from aplus.verify import rolling_stats


BASKET = 10   # 与 top_n/bottom_n 一致：成分不足的日子会被 t 检验丢弃


def _rows(days: int, up_ret: float, down_ret: float, market: float,
           up_cost: float = 0.0) -> pd.DataFrame:
    """构造 days 天的合成判定结果：每天每腿各 BASKET 只，收益固定。

    成分数必须够：`aplus.verify.MIN_BASKET` 会把不足门槛的日子从
    t 检验序列里剔除（事故：9/10 成分无次日 bar 时 `.mean()` 静默按 1 只算，
    多空价差虚报到 +9.84%）。夹具若每腿只有 1 只，测的就不是生产路径了。
    """
    out = []
    for i in range(days):
        d = f"2024-01-{i + 1:02d}"
        for k in range(BASKET):
            out.append({"pred_date": d, "code": f"U{i:03d}_{k}", "direction": "up",
                        "actual_pct": up_ret, "market_pct": market, "cost": up_cost,
                        "result": "hit" if up_ret > 0 else "miss"})
            out.append({"pred_date": d, "code": f"D{i:03d}_{k}", "direction": "down",
                        "actual_pct": down_ret, "market_pct": market, "cost": 0.0,
                        "result": "hit" if down_ret < 0 else "miss"})
    return pd.DataFrame(out)


# ── rolling_stats 必须报出跌腿 ──────────────────────────────────────────────

def test_rolling_stats_reports_down_basket():
    st = rolling_stats(_rows(10, up_ret=0.02, down_ret=-0.03, market=0.01))
    assert st["avg_down_return"] == pytest.approx(-0.03)
    # 跌腿价值 = 回避掉的亏损 = 市场 − 该股收益
    assert st["down_excess_vs_market"] == pytest.approx(0.04)
    assert st["hit_rate_down"] == pytest.approx(1.0)


def test_rolling_stats_zero_when_no_down_basket():
    """只有涨腿的历史记录（旧数据）不得崩，退化为 0。"""
    df = _rows(5, up_ret=0.01, down_ret=-0.02, market=0.0)
    only_up = df[df["direction"] == "up"].reset_index(drop=True)
    st = rolling_stats(only_up)
    assert st["avg_down_return"] == 0.0
    assert st["down_excess_vs_market"] == 0.0
    assert st["excess_breadth"] == pytest.approx(st["up_excess_vs_market"])


def test_breadth_is_sum_and_tilt_is_difference():
    """广度 = 两端之和；单侧偏度 = 两端之差。"""
    st = rolling_stats(_rows(10, up_ret=0.02, down_ret=-0.03, market=0.01))
    up = st["up_excess_vs_market"]          # +0.01
    down = st["down_excess_vs_market"]      # +0.04
    assert st["excess_breadth"] == pytest.approx(up + down)
    assert st["excess_tilt"] == pytest.approx(up - down)


def test_pure_single_factor_has_zero_tilt():
    """纯单因子排序（两端对称）→ **偏度 tilt ≈ 0**，但广度 > 0。

    推导：两端取自同一因子 f 的对称分位时
        up_excess   = β(f_high − f_mean)
        down_excess = β(f_mean − f_low)
        tilt = up_excess − down_excess = β(f_high + f_low − 2 f_mean) ≈ 0

    而 breadth = up_excess + down_excess = β(f_high − f_low) > 0。
    所以**纯单因子同样有正的多空价差**——这两个数不能用来区分
    「单因子」与「真本事」，它们各自回答的是别的问题（见模块 docstring）。
    """
    st = rolling_stats(_rows(10, up_ret=0.03, down_ret=-0.03, market=0.0))
    assert st["up_excess_vs_market"] == pytest.approx(0.03)
    assert st["down_excess_vs_market"] == pytest.approx(0.03)
    assert st["excess_tilt"] == pytest.approx(0.0), (
        "两端对称时偏度必须为 0——非 0 说明公式写错了"
    )
    assert st["excess_breadth"] == pytest.approx(0.06), "广度就是多空价差"


def test_breadth_is_market_direction_immune():
    """**这是广度相对涨腿超额的核心价值**：大盘涨跌幅不影响多空价差。

    涨腿超额 `E[r_up] − E[r_mkt]` 在大盘上涨时自动变大——选股毫无本事、
    只是踩在 beta 上，也会得到一个漂亮的正数。多空价差把市场项消掉了。

    构造：同一套选股能力，只改大盘整体涨 3%，涨腿超额跟着涨，多空价差不动。
    """
    flat = rolling_stats(_rows(10, up_ret=0.02, down_ret=-0.02, market=0.0))
    bull = rolling_stats(_rows(10, up_ret=0.05, down_ret=0.01, market=0.03))

    # 大盘 +3%：涨腿超额 0.02 → 0.05，跟着动了
    assert flat["up_excess_vs_market"] == pytest.approx(0.02)
    assert bull["up_excess_vs_market"] == pytest.approx(0.02)
    # 多空价差只取决于两端差：2 分 vs 4 分 —— 选股没变，价差却变了
    assert bull["excess_breadth"] == pytest.approx(0.04)
    assert flat["excess_breadth"] == pytest.approx(0.04)
    # 真正对大盘免疫的是「两端之差」这个量本身
    assert (bull["avg_up_return"] - bull["avg_down_return"]) == pytest.approx(
        flat["avg_up_return"] - flat["avg_down_return"])


def test_tilt_detects_market_exposure_in_the_ranking():
    """排序两端**合起来**跑赢市场 → tilt 显著为正，说明含有市场暴露。

    纯 beta 排序就是这样：涨跌两端都偏多头，只是程度不同。
    这不是 bug，是必须**如实披露**的风险——用户按名单建仓就是在承担这个 beta。
    """
    st = rolling_stats(_rows(10, up_ret=0.06, down_ret=0.02, market=0.0))
    assert st["up_excess_vs_market"] == pytest.approx(0.06)
    assert st["down_excess_vs_market"] == pytest.approx(-0.02)
    assert st["excess_tilt"] == pytest.approx(0.08), "偏度应为两端超额之差"
    assert st["excess_breadth"] == pytest.approx(0.04)



def test_down_leg_has_no_trading_cost():
    """跌腿是回避清单，不产生交易 → 不得扣成本。

    扣了就等于凭空惩罚一个我们没做过的仓位，指标会系统性偏保守。
    """
    df = _rows(10, up_ret=0.0, down_ret=-0.02, market=0.0)
    df["cost"] = 0.003                       # 故意给跌腿也挂上成本
    st = rolling_stats(df)
    assert st["avg_down_return"] == pytest.approx(-0.02), "跌腿被扣了不该扣的成本"


# ── 统计检验：广度必须可做 t 检验，否则又回到「看起来是正的」 ──────────────

def test_breadth_tstat_is_none_without_enough_days():
    df = _rows(2, up_ret=0.02, down_ret=-0.02, market=0.0)
    assert breadth_tstat(df) == (None, 2)


def test_breadth_tstat_is_none_on_empty():
    assert breadth_tstat(pd.DataFrame()) == (None, 0)


def test_breadth_tstat_positive_for_consistent_two_sided_skill():
    rng = np.random.default_rng(3)
    n = 120
    up = rng.normal(0.004, 0.01, n)          # 涨篮子持续跑赢市场
    down = rng.normal(-0.004, 0.01, n)       # 跌篮子持续跑输市场
    rows = []
    for i in range(n):
        for k in range(BASKET):
            rows.append({"pred_date": f"d{i:03d}", "code": f"U{k}", "direction": "up",
                         "actual_pct": up[i], "market_pct": 0.0, "cost": 0.0})
            rows.append({"pred_date": f"d{i:03d}", "code": f"D{k}", "direction": "down",
                         "actual_pct": down[i], "market_pct": 0.0, "cost": 0.0})
    t, days = breadth_tstat(pd.DataFrame(rows))
    assert days == n
    assert t is not None and t > 3.0, f"稳定双边超额应显著，t={t}"


def test_breadth_tstat_undefined_when_ranking_separates_nothing():
    """排序**完全不区分两端**时逐日广度恒为 0，标准差为 0 → 必须返回 None。

    绝不能返回一个「除以极小标准差」的巨大 t 值——那正是当初
    「30 组随机权重里也有 1 组能过闸」的同类陷阱。无法测量就说无法测量。
    """
    n = 100
    rng = np.random.default_rng(5)
    shock = rng.normal(0, 0.01, n)
    rows = []
    for i in range(n):
        # 两端收益完全相同 → 多空价差每天都恰好为 0
        for k in range(BASKET):
            for direction in ("up", "down"):
                rows.append({"pred_date": f"d{i:03d}", "code": f"{direction}{k}",
                             "direction": direction, "actual_pct": shock[i],
                             "market_pct": 0.0, "cost": 0.0})
    t, days = breadth_tstat(pd.DataFrame(rows))
    assert days == n
    assert t is None, f"标准差为 0 时必须返回 None 而非 inf，实际 t={t}"


def test_down_excess_tstat_sign_convention():
    """跌腿 t 值以「市场 − 跌篮子」为正方向（回避有效 = 正）。"""
    rng = np.random.default_rng(9)
    n = 60
    down = rng.normal(-0.03, 0.01, n)
    rows = []
    for i in range(n):
        for k in range(BASKET):
            for direction in ("up", "down"):
                rows.append({"pred_date": f"d{i:03d}", "code": f"{direction}{k}",
                             "direction": direction,
                             "actual_pct": 0.0 if direction == "up" else down[i],
                             "market_pct": 0.0, "cost": 0.0})
    t, days = down_excess_tstat(pd.DataFrame(rows))
    assert days == n
    assert t > 5.0, f"持续回避有效应有显著正 t，实际 {t}"


def test_down_excess_tstat_undefined_when_avoidance_never_works():
    """跌篮子与大盘同步（无回避能力）→ 逐日超额恒为 0 → None。"""
    df = _rows(40, up_ret=0.0, down_ret=0.0, market=0.0)
    assert down_excess_tstat(df) == (None, 40)
