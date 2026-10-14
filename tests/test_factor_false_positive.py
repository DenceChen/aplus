"""`is_factor_corrupt` 的「首行必须为 1.0」判据会误报借壳上市股。

## 为什么必须修

这条判据是用来抓 2026-10-11 第二类因子损坏的：因子表只剩「最近」几条事件，
早期事件丢失，`attach_factor` 在首事件之前按 1.0 填充，于是在除权日凭空
造出十倍量级的假跳空 → `mom20` 冲到 +903% → 错误标的进入用户建仓清单。
判据本身有效，留着。

但它有一条**已知误报**：`baostock` 必在上市日返回一条 `backAdjustFactor = 1.0`
的基准事件；借壳/重组上市的公司，上市日那天因子**已经累积到大于 1**
（那天的基准就是重组后的股本）。

实测 3 只（0.16%）：

| 代码 | 名称 | 首事件日 | 首事件因子 |
|---|---|---|---|
| 001872.SZ | 招商港口 | 2018-12-26（借壳上市） | 3.526478 |
| 001914.SZ | 招商积余 | 2019-06-06 | 6.411102 |
| 600018.SH | 上港集团 | 2006-10-26 | 9.778728 |

原代码接受这点误报的理由是「0.16% 远低于 1% 告警阈值」——那在**只统计、
不阻断**的体检里成立。但 2026-10-11 我把因子体检改成了**闸门**（有损坏就
不发布推荐，见 tests/test_factor_gate.py）。阈值立刻失效：

    损坏 3/5447（0.06%）→ 闸门阻断 → 每天都不出名单

**一个天天误报的闸门等于没有闸门**——它只会被绕过，或者被忽略。

## 正确的判据

真正要抓的破坏是「`attach_factor` 在早期按 1.0 填充，导致除权日假跳空」。
它成立的前提是：**首事件落在日线历史的起点之后**，于是早期那段确实被填成 1.0。

借壳股的首事件在日线起点**之前**（或当天），全程都被正确前向填充覆盖，
根本没有 1.0 填充段——它们是健康的。

所以判据改成「首事件日期 > 日线首根日期」，用日线数据判定，不靠猜。
"""
from __future__ import annotations

import pandas as pd
import pytest

from aplus.adjust import is_factor_corrupt

FACTOR_START_DAILY = "2019-01-02"


def _f(rows):
    return pd.DataFrame({"date": [r[0] for r in rows],
                         "adjust_factor": [r[1] for r in rows]})


# ── 已知误报的三只借壳股必须判为健康 ────────────────────────────────────────

@pytest.mark.parametrize("code,first_event,factor", [
    ("001872.SZ", "2018-12-26", 3.526478),   # 招商港口 借壳
    ("600018.SH", "2006-10-26", 9.778728),   # 上港集团
])
def test_backdoor_listing_is_not_corrupt(code, first_event, factor):
    """借壳上市股首事件因子 >1，但首事件在日线起点**之前** → 健康。

    若本用例变红，闸门会天天阻断（实测这类误报 0.06%，恰好等于全池损坏率）。
    """
    f = _f([(first_event, factor), ("2020-01-02", factor + 0.1)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is False, \
        f"{code} 被误判为损坏（首事件 {first_event} 早于日线起点）"


# ── 旧文档把这只当成了误报，实测它是真损坏 ──────────────────────────────────

def test_001914_is_genuinely_corrupt_not_a_false_positive():
    """**旧 docstring 的判断是错的，这里按实测纠正。**

    001914.SZ（招商积余）曾被列为「借壳上市误报」。实测否定了这一点：

    - 日线起点 2019-01-02，因子首事件 **2019-06-06**（晚 155 天）
    - `attach_factor` 在 2019-01-02 ~ 2019-06-05 按 **1.0** 填充
    - 2019-06-06 因子跳到 **6.4111**，复权收益 = **+491.25%**

    这是实打实的假跳空，不是误报。`scripts/repair_factors.py` 重取后事件数
    仍是 11 条不变——源站确实只给这些，早期基准无法从该源恢复。

    所以它**必须**被判损坏，并挡在推荐之外。宁可少一只股票，不可在榜单里
    放一个动量虚高 491% 的标的。
    """
    f = _f([("2019-06-06", 6.411102), ("2020-01-02", 6.511102),
            ("2021-01-04", 6.9)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is True, (
        "001914.SZ 的早期基准是未知的 1.0 填充，会造出 +491% 假收益，"
        "不能当成借壳误报放行"
    )


# ── 真正的残缺仍必须抓住 ────────────────────────────────────────────────────

def test_truncated_factor_table_is_still_corrupt():
    """只剩最近一条事件（首事件远晚于日线起点）→ 早期被填 1.0 → 必须判损坏。

    这是 2026-10-11 第二类损坏的形态，**判据不能因为修误报而放松**。
    """
    f = _f([("2026-07-15", 10.146)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is True


def test_first_event_on_the_first_daily_bar_is_healthy():
    """首事件恰好等于日线首根日期 → 没有任何 1.0 填充段 → 健康。"""
    f = _f([("2019-01-02", 3.5), ("2020-01-02", 3.6)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is False


def test_grace_period_absorbs_normal_fetch_lag():
    """首事件比日线起点晚一点属正常（源站基准事件本就在上市日，不是取数日）。

    留 10 个自然日的宽限，避免把「首事件晚了两天」这种无害情形判成损坏。
    """
    f = _f([("2019-01-09", 5.0), ("2020-01-02", 5.1)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is False


def test_beyond_grace_is_corrupt():
    f = _f([("2019-03-01", 5.0), ("2020-01-02", 5.1)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is True


# ── 不给日线首日时，保持旧口径（宁可错杀） ──────────────────────────────────

def test_without_daily_date_falls_back_to_strict_one_point_zero():
    """拿不到日线首日 → 退回旧的「首行必须 1.0」判据。

    方向是**保守**（更严）：不知道时宁可判成损坏，也不放行未验证的数据。
    """
    f = _f([("2018-12-26", 3.526478)])
    assert is_factor_corrupt(f) is True
    assert is_factor_corrupt(_f([("2018-12-26", 1.0)])) is False


# ── 其他判据不受影响 ────────────────────────────────────────────────────────

def test_missing_file_is_corrupt():
    assert is_factor_corrupt(None, first_daily_date=FACTOR_START_DAILY) is True


def test_empty_table_is_healthy():
    """空表 = 无除权事件 = 恒为 1.0，健康。"""
    assert is_factor_corrupt(pd.DataFrame({"date": [], "adjust_factor": []}),
                             first_daily_date=FACTOR_START_DAILY) is False


def test_nan_and_nonpositive_still_corrupt():
    for bad in ([1.0, float("nan")], [1.0, 0.0], [1.0, -1.0]):
        f = _f([("2019-01-02", bad[0]), ("2020-01-02", bad[1])])
        assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is True


def test_non_monotonic_still_corrupt():
    """后复权因子只增不减，倒退即源站伪事件。"""
    f = _f([("2019-01-02", 2.0), ("2020-01-02", 1.5)])
    assert is_factor_corrupt(f, first_daily_date=FACTOR_START_DAILY) is True
