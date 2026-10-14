"""逐日 t 检验在低频候选上是**伪显著**——必须按调仓周期分块。

## 事故（2026-10-11 20:40）

独立验证（2019-01 ~ 2023-12，962 个交易日）的频率对比表：

| 纯 20 日反转 `{mom20:-1.0}` | 年成本 | 年毛超额 | t（逐日） |
|---|---|---|---|
| 日频 K=1 | 246.3% | +77.4% | 6.32 |
| 周频 K=5 | 98.2% | +92.8% | 8.01 |
| 月频 K=21 | 35.8% | +113.4% | 9.19 |
| 季频 K=63 | 12.2% | +117.9% | **11.17** |

同一组权重、同样的信号，**毛超额几乎不动，t 却涨了 77%**。
这就是伪显著的指纹。

## 为什么必然如此

K 日调仓时，一天里「选了谁」这件事在 K 天内**不变**。
逐日超额序列 `e_t = 实际篮子 − 市场`，其中由选股产生的成分在整块内高度自相关。
而 `excess_tstat` 把 962 个交易日当成 962 个**独立观测**：

    t_虚高 = t_真实 × sqrt(n_观测 / n_独立)

K=63 时 n_独立 ≈ 962/63 ≈ **15**，虚高倍数上限 sqrt(63) ≈ 7.9 倍。

（实测没到 7.9 倍，因为逐日序列里还留着个股噪声；
但方向是确定的：**t 随 K 单调上升，就是没有按独立观测算**。）

## 正确做法

把逐日序列切成**互不重叠的 K 日块**，块内取均值，对块均值做 t 检验。
块均值才是策略真正的「一次决策」的观测单位。

- 尾部不足 K 天的零头**丢掉**：不满的块均值方差更大，混进去会污染方差估计
- 块数 < 3 → 返回 `None`：无法测量就说无法测量，不给凭空的 t
- **K=1 时逐位等于原行为**：所有历史结论不受影响
"""
import numpy as np
import pandas as pd
import pytest

from aplus.evolve_race import _block_means, _tstat


def _series(values) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2020-01-01", periods=len(values)))


def test_block_equals_one_reproduces_plain_tstat_bitwise():
    """K=1 必须与不传 block 逐位相同——否则所有历史 t 值被静默改写。"""
    s = _series([0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.0, 0.015])
    a, na = _tstat(s)
    b, nb = _tstat(s, block=1)
    assert a == b and na == nb


def test_block_aggregation_equals_t_on_period_means():
    """分块 t 必须**逐位等于**对手算块均值的 t——这不是近似，是同一个数。

    构造：4 个 6 日块，块内取值完全相同、块间缓慢上漂。
    「块内完全相同」是 K 日调仓的理想化极限（选股不变），
    也是分块聚合所假设的最坏情形。
    """
    levels = [0.20, 0.30, 0.40, 0.50]
    x = np.concatenate([np.full(6, v) for v in levels])
    s = _series(x)

    t_daily, n_daily = _tstat(s)
    t_block, n_block = _tstat(s, block=6)

    lv = np.array(levels)
    t_hand = lv.mean() / (lv.std(ddof=1) / np.sqrt(len(lv)))

    assert n_daily == 24 and n_block == 4
    assert t_block == pytest.approx(t_hand), "分块 t 必须等于块均值序列的 t"
    # 虚高倍数至少是 √K。比值**略高于** √K=2.449（实测 2.769）：
    # 两个标准差用的自由度不同——逐日 23、分块只有 3，
    # 样本越少单个观测的误差棒越宽，分块估计因此更保守。
    ratio = t_daily / t_block
    assert np.sqrt(6) <= ratio <= np.sqrt(6) * 1.2, (
        f"虚高倍数 {ratio:.3f} 应在 √K 附近（略高因 ddof 自由度差异）")


def test_within_block_noise_shrinks_the_inflation():
    """块内仍有噪声时，虚高倍数小于 √K —— 所以真实数据上的倍数必须实测，
    不能拿 √K 当结论。这条测试钉住「倍数随噪声单调下降」这个方向。"""
    pattern = np.array([0.08, 0.00, 0.04, 0.04, 0.04, 0.00])
    pattern = pattern - pattern.mean()
    levels = [0.20, 0.30, 0.40, 0.50]

    def ratio(noise):
        x = np.concatenate([np.full(6, v) + noise * pattern for v in levels])
        td, _ = _tstat(_series(x))
        tb, _ = _tstat(_series(x), block=6)
        return abs(td) / abs(tb)

    assert ratio(1.0) > ratio(3.0) > ratio(10.0)
    assert ratio(1.0) > np.sqrt(6), "无噪声时必须顶到 √K 上限"


def test_block_mean_equals_hand_computed_value():
    s = _series([0.0, 0.10, 0.20, 0.30, 0.40, 0.60])
    m = _block_means(s, 3)
    # 第 2 块 = (0.30+0.40+0.60)/3 = 0.4333…（不是 0.40）
    assert list(m) == pytest.approx([0.10, 1.30 / 3])


def test_incomplete_trailing_block_is_dropped():
    """零头块方差更大，混进去会污染方差估计。"""
    s = _series([0.0, 0.10, 0.20, 0.30, 0.40, 0.60, 0.80])
    m = _block_means(s, 3)
    assert len(m) == 2, "7 天切 3 天块只有 2 个完整块，第 7 天必须丢掉"


def test_too_few_blocks_returns_none_instead_of_inventing_a_t():
    """2 个块无法区分「有效」与「无效」——宁可说测不出来。"""
    s = _series([0.01, 0.02, 0.01, 0.02, 0.01, 0.02])
    t, n = _tstat(s, block=3)
    assert t is None and n == 2


def test_constant_series_returns_none_not_a_huge_t():
    """**回归测试**：老代码写 `if sd <= 0`，而 pandas 的 `std(ddof=1)` 对常数序列
    返回 3.5e-18 而非 0，于是 `sd <= 0` 从未成立，t = **3.1e16**。
    一个测不出差别的序列，凭空报出一个能闯过显著性硬闸的巨大 t。"""
    s = _series([0.02] * 30)
    assert _tstat(s)[0] is None
    assert _tstat(s, block=5)[0] is None


def test_near_constant_series_is_also_refused():
    """变异系数小于 DEGENERATE_CV 一律判「测不出差别」。"""
    s = _series([0.02] * 29 + [0.0200000000001])
    assert _tstat(s)[0] is None


# ── 接线：分块必须一路传到 excess_tstat ──────────────────────────
# 只测 `_tstat` / `_block_means` 是不够的。变异测试里有一个变异体
# 「把 `excess_tstat` 里的 `block=rebalance_every` 改成 `block=1`」
# 曾经**存活**——因为没有任何测试从 `excess_tstat` 走一遍。
# 下面这三条补上那条链路。

def _fake_results(dates, up_ret, down_ret=None):
    import pandas as pd
    rows = []
    for i, d in enumerate(dates):
        for j in range(10):
            rows.append({"pred_date": d, "code": f"{j:06d}.SZ", "direction": "up",
                         "actual_pct": up_ret[i], "result": 1, "market_pct": 0.0,
                         "cost": 0.0})
        if down_ret is not None:
            for j in range(10):
                rows.append({"pred_date": d, "code": f"{j:06d}.SH", "direction": "down",
                             "actual_pct": down_ret[i], "result": 0, "market_pct": 0.0,
                             "cost": 0.0})
    return pd.DataFrame(rows)


def test_excess_tstat_forwards_frequency_to_blocking():
    """`excess_tstat(pred, rebalance_every=K)` 必须真的按 K 分块。"""
    from aplus.evolve_race import excess_tstat
    dates = [f"2024-01-{i:02d}" for i in range(1, 25)]
    # 块内完全相同、块间递增：只有按块聚合才会给出 4 个观测
    ret = [0.01] * 6 + [0.02] * 6 + [0.03] * 6 + [0.04] * 6
    res = _fake_results(dates, ret)
    _, n_days = excess_tstat(res)
    _, n_blocks = excess_tstat(res, rebalance_every=6)
    assert n_days == 24
    assert n_blocks == 4, "频率必须传到分块逻辑，否则低频候选继续用虚高的逐日 t"


def test_breadth_tstat_also_forwards_frequency():
    from aplus.evolve_race import breadth_tstat
    dates = [f"2024-01-{i:02d}" for i in range(1, 25)]
    ret = [0.01] * 6 + [0.02] * 6 + [0.03] * 6 + [0.04] * 6
    res = _fake_results(dates, ret, down_ret=[-0.01] * 24)
    _, n_daily = breadth_tstat(res)
    _, n_blocks = breadth_tstat(res, rebalance_every=6)
    assert n_daily == 24 and n_blocks == 4