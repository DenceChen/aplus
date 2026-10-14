"""ST 过滤必须**双信号**：`isST` 列 + 股票名称前缀。

## 事故经过（2026-10-11 实测）

用户当日推荐名单里出现了 `*ST岭南`（002717.SZ）和 `*ST住亭`（301030.SZ）。
`filter_universe` 的过滤条件是 `isST == 0`，按设计它们本该被剔除。

逐层挖下去：

| 数据状态 | 股票数 | 占全池 |
|---|---|---|
| `isST` 列**不存在**（腾讯备源写入的文件） | 1693 | 31.1% |
| `isST` 列存在但最新值是 **NaN**（`_upsert` 把两个源 concat，有列的那天有数、没列的那天是 NaN） | 3112 | 57.1% |
| `isST` 最新值可用 | 642 | 11.8% |

**合计 4805 只（88.2%）的 ST 状态不可判定**。而 `aplus/panel.py` 的
`cross_section` 把 NaN 一律当 0 —— 「不知道」被当成「不是 ST」，
于是这些股票畅通无阻地进了推荐名单。

拿**独立来源**交叉验证（`data/names.json` 的股票简称，来自 baostock 股票列表）：

- 名称含 `ST`/`*ST` 的股票共 **310** 只
- 其中 **178** 只 `isST` 不可判定 → 现在正被当作普通股票推荐
- 另有 **45** 只 `isST` 列写着 0，但名称是 ST → **列本身也是错的**

`isST` 与名称矛盾样例：`000023.SZ *ST深天`（列值 0）、`000040.SZ *ST旭蓝`、
`000046.SZ *ST泛海`、`000540.SZ *ST中天`。

## 为什么这不是「数据脏一点」

ST/*ST 在 A 股是 **5% 涨跌停**、且带强制退市风险。给 5 万本金的账户推一只
即将退市的股票，损失可以是整个账户的量级。这条过滤是**安全边界**，不是性能优化。

## 本测试的立场

在数据补齐之前，**名称前缀是唯一可信的兜底**。名称是 baostock 股票列表里的
官方简称，`*ST` 前缀在 A 股是无歧义的。规则取「**或**」：

    剔除 = (isST == 1)  或  (名称匹配 ^(S\\*?)?\\*?ST)

绝不能反过来写成「两个信号都说是才剔除」——那样 178 只照样进来。
也绝不能要求 `isST == 0` 才保留——那会砍掉 88% 的正常股票。

数据补齐（`scripts/backfill_gaps.py`）之后，`isST` 列恢复可信，本规则依然保留
作为第二道防线——两个独立信号取「或」，只会更严，不会更松。
"""
from __future__ import annotations

import re

import pandas as pd
import pytest

from aplus.predict import is_st_name, filter_universe

# A 股 ST 命名的全部形态：ST / *ST / SST / S*ST（未完成股改）
ST_NAME = re.compile(r"^(S\*?)?\*?ST")


def _cs(names, is_st=None, **kw):
    n = len(names)
    return pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)],
        "name": names,
        "isST": (is_st if is_st is not None else [0] * n),
        "volume": [1e6] * n,
        "listed_days": [500] * n,
        "last_date": ["2026-10-09"] * n,
        **kw,
    })


# ── 名称识别 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nm", ["ST东北高", "*ST九鼎", "SST华塑", "S*ST佳通",
                                "*STST中装", "st小写", "*St混合"])
def test_st_name_pattern_matches_all_a_share_forms(nm):
    """大小写不敏感——源站简称在不同接口里大小写不统一。"""
    assert is_st_name(nm), f"{nm} 应识别为 ST"


@pytest.mark.parametrize("nm", ["浦发银行", "STO佳讯"[:0] or "平安银行",
                                "北方导航", "中国石化", "*ST", "ST",
                                "东方锆业", "金发科技"])
def test_normal_names_are_not_st(nm):
    assert not is_st_name(nm), f"{nm} 被误判成 ST"


def test_empty_or_nan_name_is_not_st():
    """名称缺失时不得当成 ST（那会把整池误杀），也不得当成非 ST（那等于没过滤）。

    正确行为：返回 False（不当成 ST），由 isST 列兜底；真正的风险在于
    两个信号都没有——所以名称缺失比例必须被单独监控。
    """
    assert is_st_name(None) is False
    assert is_st_name(float("nan")) is False
    assert is_st_name("") is False


# ── 过滤行为：核心事故防线 ──────────────────────────────────────────────────

def test_st_by_name_is_removed_even_when_isst_column_is_zero():
    """**本事故的钉死测试**：名称是 *ST，isST 列却写着 0 → 必须剔除。

    若这个用例变红，用户账户里就会出现退市风险股。
    """
    df = _cs(["平安银行", "*ST中天", "北方导航"])
    kept = filter_universe(df)
    assert "*ST中天" not in set(kept["name"])
    assert set(kept["name"]) == {"平安银行", "北方导航"}


def test_st_by_name_is_removed_when_isst_column_is_nan():
    """88.2% 的股票 isST 是 NaN —— 那不是「不是 ST」，那是「不知道」。

    `panel.cross_section` 把 NaN 填成 0，于是名称成为唯一防线。
    """
    df = _cs(["平安银行", "*ST岭南", "*ST住亭"], is_st=[0, float("nan"), float("nan")])
    kept = filter_universe(df)
    assert set(kept["name"]) == {"平安银行"}


def test_st_by_column_is_removed_when_name_is_normal():
    """反向：列说是 ST、名称正常 → 仍然剔除（两个信号取「或」）。"""
    df = _cs(["平安银行", "北方导航"], is_st=[0, 1])
    kept = filter_universe(df)
    assert set(kept["name"]) == {"平安银行"}


def test_sst_and_s_star_st_forms_are_removed():
    """未完成股改的 `SST` / `S*ST` 同样是风险警示，不能漏。"""
    df = _cs(["平安银行", "SST华塑", "S*ST佳通"])
    kept = filter_universe(df)
    assert set(kept["name"]) == {"平安银行"}


def test_non_st_universe_is_not_shrunk_by_the_new_rule():
    """防呆：正常股票一只都不能被新规则误杀。

    若这条变红，说明正则太宽——那比漏掉 ST 更糟。
    """
    names = ["浦发银行", "平安银行", "中国石化", "北方导航", "金发科技",
             "东方财富", "ST中天".replace("ST", "深"), "华友钴业"]
    df = _cs(names)
    kept = filter_universe(df)
    assert len(kept) == len(names)


def test_filter_still_drops_new_listings_and_zero_volume():
    """新规则不得挤掉原有的次新股/停牌过滤。"""
    df = _cs(["平安银行", "次新股", "停牌股"],
             listed_days=[500, 10, 500], volume=[1e6, 1e6, 0])
    kept = filter_universe(df)
    assert set(kept["name"]) == {"平安银行"}
