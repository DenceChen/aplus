"""`filter_universe` 在 `name` 列缺失时不得依赖 `~False` 的副产品。

## 这条在挡什么

`filter_universe` 的 ST 名称兜底写成：

    st_by_name = df["name"].map(is_st_name) if "name" in df.columns else False
    ...
    mask &= (df["isST"] != 1) & ~st_by_name & (df["listed_days"] > 60) & (df["volume"] > 0)

`name` 列缺失时 `st_by_name` 是**标量 Python 布尔** `False`，
于是 `~st_by_name` 走的是 Python 的 `~False` → `-1`，而不是布尔取反。

现在能跑对，**纯属巧合**：`-1` 在 numpy 里是非零值，所以
`bool_series & -1` 等价于「与全 True 取与」，恰好是「不做名称过滤」的本意。

但 Python 3.16 会移除 `~` 对 bool 的支持（实测已发 DeprecationWarning）：

    Bitwise inversion '~' on bool is deprecated and will be removed in
    Python 3.16. This returns the bitwise inversion of the underlying
    int object and is usually not what you expect from negating a bool.

**靠一个即将消失的语言特性撑着的正确行为，不是正确行为。**
一旦 `~False` 改为抛异常或改语义，ST 名称兜底会静默变成别的意思——
而它兜的是 `isST` 列对 88.2% 股票不可判定的那 88.2%。

## 为什么测试断言「不发警告」而不是断言结果

结果目前**就是对的**，断言结果等于什么都没钉。真正要钉的是
**达成这个结果所依赖的机制已经废弃**。所以判据是「这条路径不产生
关于 `~` 的废弃警告」——把「哪天会坏」钉成「今天就不能走这条路」。
"""
import warnings

import numpy as np
import pandas as pd
import pytest

from aplus.predict import filter_universe


def _frame(with_name: bool) -> pd.DataFrame:
    df = pd.DataFrame({
        "code": ["000001", "000002", "600519"],
        "isST": [0, 1, np.nan],
        "listed_days": [5000, 5000, 5000],
        "volume": [1_000_000, 1_000_000, 1_000_000],
        "buyable": [True, True, True],
        "last_date": ["2024-01-02"] * 3,
    })
    if with_name:
        df["name"] = ["平安银行", "*ST岭南", "贵州茅台"]
    return df


def test_missing_name_column_emits_no_bitwise_inversion_deprecation():
    """`name` 缺失这条路径不得触发 `~bool` 废弃警告。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        filter_universe(_frame(with_name=False))
    bad = [str(w.message) for w in caught
           if "Bitwise inversion" in str(w.message)
           or ("'and' operations between boolean" in str(w.message))]
    assert not bad, (
        "ST 名称兜底正走在一个即将消失的语言特性上（`~False` → -1）：\n  "
        + "\n  ".join(bad))


def test_missing_name_column_still_does_not_filter_by_name():
    """缺 `name` 列时**不得**因为 ST 兜底而误杀股票。

    反向纪律与正向同等重要：`is_st_name` 的约定是「名称缺失返回 False」
    （不当成 ST），因为误杀整池比漏掉 ST 更糟。这里守住同一侧。
    """
    out = filter_universe(_frame(with_name=False))
    assert set(out["code"]) == {"000001", "600519"}, (
        "缺 name 列时只有 isST==1 的那只被剔除；其余必须留下")


def test_with_name_column_still_rejects_st_by_name():
    """有 `name` 列时 ST 兜底必须照常生效——这是它存在的全部理由。"""
    out = filter_universe(_frame(with_name=True))
    assert set(out["code"]) == {"000001", "600519"}, (
        "*ST岭南 必须被名称兜底剔除（它的 isST 恰好也是 1，"
        "但 45 只股票的 isST 写着 0 而名称是 ST，那才是这条判据的价值）")


@pytest.mark.parametrize("isst,named_st,expect", [
    (0, "ST岭南", False),      # isST 说不是，名称说是 → 名称兜底必须推翻它
    (1, "平安银行", False),
    (0, "平安银行", True),
])
def test_st_signals_are_ored_not_anded(isst, named_st, expect):
    """两个 ST 信号取「或」，绝不能要求都说是才剔，也绝不能要求 isST==0 才留。"""
    df = pd.DataFrame({
        "code": ["X"], "isST": [isst], "listed_days": [5000],
        "volume": [1_000_000], "buyable": [True],
        "last_date": ["2024-01-02"], "name": [named_st],
    })
    out = filter_universe(df)
    assert (len(out) == 1) is expect, (
        f"isST={isst} 名称={named_st} → 期望{'保留' if expect else '剔除'}")