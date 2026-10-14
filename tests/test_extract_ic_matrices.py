"""抽取脚本的**写入端**必须与 `unpack` 的读取端对得上。

## 为什么这一节不能省

`tests/test_composite.py` 全是**读**端：它自己造长表，
所以无论抽取脚本写什么列名，测试都绿。
于是抽取脚本曾经把 `R__ab` 写成**位置**下标，而读取端按**顺序**读——
**两边的测试各自都通过，只有真数据才炸**。

这是「测了 A 却用了自己造的 A」的自欺：**fixture 和被测对象
共用同一个错误假设时，两边同时绿，什么也证明不了。**

所以这里**反过来**：让抽取脚本真的写（`tests/icmat_synthetic.extract_table`），
用它写出来的表去喂 `unpack`。能往返，才算真的接上了。

## 规模说明

只需 220 只股票 × 3 个因子的合成数据——
`extract` 的所有真逻辑（`forward_returns` 的成交闸门、
`make_ranked_forward` 的排秩、`rank_zscore` 的并列平均、
`daily_stats` 的退化检测）都被真正跑过。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.composite import unpack
from tests.icmat_synthetic import extract_table, load_extract_module

@pytest.fixture(scope="module")
def extracted():
    mod = load_extract_module()
    factors = ["f1", "f2", "f3"]
    return extract_table(factors), factors, mod


def test_the_columns_it_writes_are_exactly_the_ones_unpack_reads(extracted):
    """**写入端与读取端的列名必须逐字相同**，且**带因子名**。

    这一条是「往返」的最小形态：能读回来 = 接上了；
    读回来的 R 是**主子矩阵** = 没被位置平移。
    """
    df, factors, _ = extracted

    # 不传子集也要能读
    _, R_all, sf_all = unpack(df, factors)
    assert R_all.shape == (len(df), len(factors), len(factors))
    assert sf_all.shape == (len(df), len(factors))

    # 关键：打乱顺序 + 取子集，读回来必须是**重排后**的同一块
    shuffled = [factors[2], factors[0], factors[1]]
    _, R_sub, sf_sub = unpack(df, shuffled)
    perm = [factors.index(f) for f in shuffled]
    assert np.allclose(sf_sub, sf_all[:, perm])
    assert np.allclose(R_sub, R_all[:, perm][:, :, perm])

    # 且每个子集的 R 的**对角线**必须全 1（自相关的定义）。
    # ⚠️ 这条在位置错标下**恒真**——错标后读到的还是别人的对角线，
    # 仍然是 1.0。所以它是「别悄悄退化」的看门狗，**不是守卫**。
    # 守卫在上面那条 `np.allclose(R_sub, R_all[:, perm][:, :, perm])`。
    for i in range(len(shuffled)):
        assert np.allclose(R_sub[:, i, i], 1.0)


def test_a_subset_run_must_never_touch_columns_it_did_not_ask_for(extracted):
    """抽取时给 3 个因子，读时就只能有这 3 个的交叉项。

    反向断言：`--factors a,b` 绝不能因为表里还有 `c` 就
    把 `c` 混进 R——那正是「位置索引」悄悄发生的地方。
    """
    df, factors, _ = extracted
    two = factors[:2]
    _, R_two, _ = unpack(df, two)
    assert R_two.shape == (len(df), 2, 2)

    # 表里**确实**还有第三个因子（所以「碰巧读对」不是理由）
    assert f"sf__{factors[2]}" in df.columns
    assert f"R__{factors[2]}__{factors[0]}" in df.columns


def test_writing_with_a_non_default_order_must_not_change_the_table(extracted):
    """抽取时的因子顺序**也是书写**，不是语义。

    若抽取按调用顺序给 R 编号，那么换一种写法写出来的表就会
    与原来那份**不可比**——而两份表看上去一模一样。
    """
    rev = ["f3", "f2", "f1"]
    df_rev = extract_table(rev)

    df_std, _, _ = extracted
    assert "R__f3__f2" in df_rev.columns
    # 两张表**按名字**取，数值必须逐位相同
    common = [c for c in df_std.columns if c.startswith("R__f1__")]
    assert common
    for c in common:
        assert np.allclose(df_std[c].to_numpy(float),
                           df_rev[c].to_numpy(float)), c