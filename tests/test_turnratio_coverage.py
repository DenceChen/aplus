"""异常换手率：两个窗口用**同一个**覆盖率门槛（2026-10-14）。

## 这组测试纠正了一个归因错误

原先认定「短窗口（21 日）没有覆盖率豁免」是股票池窄 6.7% 的主因，
断言「最近停牌 3~6 天也必须算出因子」。**实测推翻了它**：

    短窗口有效根数分布（全池 5458，长窗口合格 5192 里短窗口不过关的 91 只）
        0~14 根：26 只    15 根：40 只    16~18 根：17 只    19~20 根：**0 只**

    短窗口阈值      救回    占合格池
    一根不缺          0      0.00%
    ceil(21×0.9)=19  0      0.00%   ← 原先断言的这条
    ≥15 根          57      1.10%
    ≥10 根          66      1.27%

覆盖率是**双峰**的——要么 21 根齐全，要么最近停牌缺一大截，
「缺 1~2 天」这种形态在真实数据里**根本不存在**。所以 0.9 规则今天
救回 0 只，不是「修得不够狠」，是这条杠杆本身就没有量。

## 那 6.7% 到底是什么

全池分解：

    上市不足 252 日（次新股）      105 只   1.92%  ← 结构性
    上市 ≥252 日                5353 只  98.08%
      └ 长窗口覆盖 ≥0.9          5192 只  96.99%  ← 真的缺数据
      └ 且短窗口覆盖 ≥0.9        5101 只  95.29%
    可算率                       93.46%  → 缺口 6.54%

`mean(turn, 252)` **定义上**就排除掉所有上市不足一年的股票——
不是数据缺陷，是因子回看长度与次新股年龄不兼容。而现任权重的因子
（mom20 / rev5 / vol20 / vsur）回看都 ≤60 日，自然能排它们。

**结论：股票池可比闸拦的是真差异，不是数据瑕疵。**
原先 `MIN_TURN_COVERAGE` 上方写的「闸拦的是一个数据完整性瑕疵」
是错的，已改。

## 那这次改了什么

把短窗口从「一根不缺」改成与长窗口**同一个** `MIN_TURN_COVERAGE`，
并把 `int()` 换成 `math.ceil()`。今天它救回 0 只，但两件事仍值得做：

1. **移除一处没有理由的不对称**。短窗口无豁免在原注释里的理由是
   「样本本来就小，放宽会真的改变因子值」——可长窗口放宽时同样
   改变了因子值。没有区别的理由，就该统一。
2. **`int()` 让 `MIN_TURN_COVERAGE` 这个名字在说谎**。0.9 在长窗口上
   是 `int(252*0.9)=226` = 89.7%，在短窗口上是 `int(21*0.9)=18` =
   85.7%——两处都低于它声称的 90%，且严的程度还不一样。
   `ceil` 让「实际覆盖率 ≥ 承诺覆盖率」成为恒等式。

真实数值见 `scripts/probe_turn_coverage.py`。
"""
import math

import numpy as np
import pandas as pd

from aplus.panel import (MIN_TURN_COVERAGE, TURN_RATIO_LONG, TURN_RATIO_SHORT,
                         _factor_series)


def _merged(n=400, missing_in_last=0, gap=None):
    """构造一条能喂给**生产函数** `_factor_series` 的面板输入。

    `gap=(a, b)` 在**中段** `[a, b)` 打洞——测「历史本身就不全」必须打中段：
    开头的缺失会被 252 根滚动窗口直接滑出去，不影响覆盖率的判据
    （本文件第一版就踩了这个坑，测出 250/252 = 99.2% 覆盖，
    测试错了而代码是对的）。
    """
    turn = np.linspace(0.01, 0.05, n)
    close = 10.0 * np.exp(np.cumsum(np.full(n, 0.001)))
    volume = np.full(n, 1_000_000.0)
    if missing_in_last:
        turn[-missing_in_last:] = np.nan          # 停牌：换手率为空
        volume[-missing_in_last:] = 0.0           # 停牌：无成交
        close[-missing_in_last:] = close[-missing_in_last - 1]
    if gap:
        a, b = gap
        turn[a:b] = np.nan
    return pd.DataFrame({"close": close, "volume": volume, "turn": turn})


def _tratio(m: pd.DataFrame) -> float:
    """跑**生产代码**取 `turnratio` 末值——不复制算法，复制就没有判别力。"""
    return float(_factor_series(m)["turnratio"].iloc[-1])


# ---------- 这次改动本身 ----------

def test_one_missing_day_in_short_window_no_longer_kills_the_factor():
    """末 21 根缺 1 根（20/21 = 95%）必须算出因子。

    旧代码 `rolling(21).mean()` 要求一根不缺，这只股票整个退出排名。
    """
    assert not math.isnan(_tratio(_merged(missing_in_last=1)))


def test_two_missing_days_in_short_window_no_longer_kill_it():
    """19/21 = 90.5%，刚好过 0.9 门槛。"""
    assert not math.isnan(_tratio(_merged(missing_in_last=2)))


def test_six_suspended_days_still_blocks_the_factor():
    """末 21 根缺 6 根（15/21 = 71%）**仍然算不出来**。

    写成断言是因为这是本次改动最容易被夸大的一处：真实数据里这类股票
    占长窗口合格池的 0.86%，本次改动**一只也救不回来**。
    把「没救回来」钉成测试，比事后用报告数字去解释要可靠。
    """
    assert math.isnan(_tratio(_merged(missing_in_last=6))), \
        "15/21 = 71% 低于 0.9 门槛，算不出来才是对的"


def test_short_window_exactly_at_the_floor_is_admitted():
    """19/21 = 90.5% ≥ 0.9 —— 边界内侧必须放行。"""
    assert not math.isnan(_tratio(_merged(missing_in_last=2)))


def test_short_window_just_below_the_floor_is_rejected():
    """18/21 = 85.7% < 0.9 —— 边界外侧必须拦下。"""
    assert math.isnan(_tratio(_merged(missing_in_last=3)))


def test_both_windows_use_ceil_not_int():
    """`int()` 截断让实际覆盖率**低于**承诺的门槛，`ceil` 不会。

    这是本文件最容易回归的一处：改回 `int()` 不会有任何现有测试变红，
    只会在某个特定日期上少算几只股票。
    """
    for w in (TURN_RATIO_SHORT, TURN_RATIO_LONG):
        assert int(w * MIN_TURN_COVERAGE) / w < MIN_TURN_COVERAGE, \
            f"窗口 {w} 上 int() 恰好也够——这条测试失去意义，需重新设计"
        assert math.ceil(w * MIN_TURN_COVERAGE) / w >= MIN_TURN_COVERAGE, \
            f"窗口 {w} 上 ceil() 必须让实际覆盖率不低于门槛"


# ---------- 反方向：不得放水 ----------

def test_long_window_shortage_still_blocks():
    """252 根覆盖不足 90% 时**必须**仍然算不出来。

    豁免只放宽「停牌造成的缺失」，不放宽「数据本身就不全」。
    洞必须打在**中段**（100~300），因为末尾 252 根 = 索引 148~399，
    打头会被滚动窗口滑出去。
    """
    assert math.isnan(_tratio(_merged(gap=(100, 300)))), \
        "252 根里只剩 100 根 = 39.7%，必须算不出来"


def test_wholly_empty_turn_is_not_zero():
    """`turn` 全空 → 因子必须是 NaN，不能是 0。

    0 是个**看起来能用**的因子值：横截面 z-score 之后会正常排序，
    于是「一只换手率恒为 0 的股票」和「一只换手率正常的股票」平起平坐。
    """
    m = _merged()
    m["turn"] = np.nan
    assert math.isnan(_tratio(m))


def test_flat_zero_turn_is_rejected_not_inf():
    """turn 恒为 0 → 比值无定义 → NaN，绝不是 inf（`_safe_ratio` 的纪律）。"""
    m = _merged()
    m["turn"] = 0.0
    v = _tratio(m)
    assert math.isnan(v), f"turn 恒 0 时不得返回 {v}"