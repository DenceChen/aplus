"""合成权重里**负权重的方向不得被归一化分母吃掉**。

## 事故（2026-10-11，单因子探针赛马）

补了 8 组单因子探针后，26 组候选赛马里有三对结果**逐位相同**：

```
21  {'turnratio': -1.0}   训练 -0.126  测试 0.268  超额 -0.22%
22  {'turnratio':  1.0}   训练 -0.126  测试 0.268  超额 -0.22%
```

`{ivol: -1.0}` 与 `{ivol: 1.0}`、`{ivol:-0.5, vol20:-0.5}` 亦然。

## 根因

`composite_score` 原本是

```python
z.mul(weights).sum(axis=1, min_count=len(weights)) / sum(weights.values())
```

`sum(weights)` 对单因子负权是 **−1**，于是 `(−z) / (−1) = +z`：
**你把权重从 +1 改成 −1，排序一点都不变**。

分母的用意是「把合成分归一到加权平均」，这对**正权重**没问题（乘个正常数不影响排序），
但对负权重就成了符号翻转。

## 影响面

不是只有单因子。凡是 `sum(weights) < 0` 的候选，整组方向都被反转：

- `{mom20: -1.0}` → 实际按**正向动量**排序
- `{turnratio: -1.0}` / `{ivol: -1.0}` / `{ivol:-0.5, vol20:-0.5}`
- `{mom20: -0.5, turnratio: -0.5, ivol: -0.3}`

也就是说，**《因子投资》第 3.5 节那条「A 股 20 日是反转、动量无效」的实证，
到这一刻为止我们其实一次都没真正测过**——测的全是被反转过的动量。
"反转探针看起来毫无作用" 这个结论，是 bug 造出来的。

## 修法

分母改用 **权重绝对值之和**：`sum(|w|)`。这样合成分是真正的加权平均
（各 z 值量纲一致，平均后仍在 z 的尺度上），且**不翻转任何单个权重的符号**。

对正权重候选：分母从 `sum(w)` 变成 `sum(|w|)`，两者相等，排序**逐位不变**——
历史结论不会因为这次修复而失效。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.factor import composite_score


def _cs(values):
    n = len(values)
    return pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)], "name": [""] * n,
        "f": np.asarray(values, dtype=float),
    })


VALUES = [3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0, 10.0]


# ── 核心：负权重必须真的翻转排序 ────────────────────────────────────────────

def test_single_negative_weight_inverts_ranking():
    """`-1` 与 `+1` 必须给出**相反**的排序。

    这是本测试的落点：事故现场这两组结果逐位相同。
    """
    df = _cs(VALUES)
    up = composite_score(df, {"f": 1.0})
    down = composite_score(df, {"f": -1.0})
    assert up.tolist() != down.tolist(), "负权重没有翻转排序——符号被分母吃掉了"
    assert up.rank().tolist() == pytest.approx(down.rank().tolist()[::-1]) or \
        np.allclose(np.argsort(up.to_numpy()), np.argsort(down.to_numpy())[::-1])


def test_negative_weight_puts_high_values_at_the_bottom():
    df = _cs(VALUES)
    score = composite_score(df, {"f": -1.0})
    assert score.idxmax() == df.index[VALUES.index(min(VALUES))], \
        "负权重下，原值最小的股票应排最前"


def test_positive_weighted_average_uses_absolute_values_in_denominator():
    """多因子、含负权：每个权重的符号都必须如实贡献，不得被整体翻转。"""
    n = 40
    rng = np.random.default_rng(7)
    df = pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)], "name": [""] * n,
        "a": rng.normal(0, 1, n), "b": rng.normal(0, 1, n),
    })
    w = {"a": -1.0, "b": 0.5}
    score = composite_score(df, w)
    z = df["a"] / df["a"].std(ddof=0)
    # 缩尾会轻微改动分位点，故用相关性而非逐位相等来断言
    corr_a = np.corrcoef(df["a"], score)[0, 1]
    assert corr_a < -0.6, f"负权因子与合成分仍正相关（corr={corr_a:.2f}）——方向被翻转"


def test_positive_weight_candidates_are_bitwise_unchanged():
    """正权重候选必须与「旧公式（`sum(w)` 分母）」逐位相同。

    这是回归地基：修复不得让历史结论失效。`sum(w) == sum(|w|)` 对纯正权重
    恒成立，所以分母换了也不该改变任何一个数字。

    手工对照里必须**用同一套标准化**——否则测的是标准化带来的差异，
    而不是本次修复。标准化本身在 2026-10-11 换过一次（`winsorize`+`cross_section_z`
    → `robust_z`，因为硬 clip 让 53 只股票同分、Top10 里 9 只并列），
    所以这里的参照也必须是 `robust_z`；否则这条测试会把「分母修复」
    和「标准化修复」两件事混在一起，绿了也说明不了任何问题。
    """
    from aplus.factor import robust_z

    n = 60
    rng = np.random.default_rng(13)
    df = pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)], "name": [""] * n,
        "a": rng.normal(0, 1, n), "b": rng.normal(0.5, 1, n),
        "c": rng.normal(-0.2, 1, n),
    })
    w = {"a": 1.0, "b": 0.3, "c": 0.5}
    got = composite_score(df, w)
    z = df[["a", "b", "c"]].apply(robust_z, axis=0)
    # 旧公式：分母用 sum(w)
    expect = (z["a"] * w["a"] + z["b"] * w["b"] + z["c"] * w["c"]) / sum(w.values())
    assert np.allclose(got.to_numpy(), expect.to_numpy(), atol=1e-12), (
        "纯正权重候选的结果被改动了 —— 历史结论会因此失效"
    )


def test_negative_weight_direction_not_flipped_by_denominator():
    """负权候选的整组方向不得被分母吃掉（`sum(w)` → `sum(|w|)` 的原意）。"""
    from aplus.factor import robust_z

    n = 60
    rng = np.random.default_rng(21)
    df = pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)], "name": [""] * n,
        "a": rng.normal(0, 1, n),
    })
    w = {"a": -1.0}
    got = composite_score(df, w)
    z = df[["a"]].apply(robust_z, axis=0)
    # 分母 sum(|w|) = 1，分子 z×(−1) = −z
    expect = (z["a"] * w["a"]) / sum(abs(v) for v in w.values())
    assert np.allclose(got.to_numpy(), expect.to_numpy(), atol=1e-12)
    assert got.idxmax() == df["a"].idxmin(), "负权候选方向被翻转"


def test_zero_weights_do_not_divide_by_zero():
    """全零权重会除以 0 —— 旧代码同样会崩，但修复不该引入新的崩法。"""
    df = _cs(VALUES)
    try:
        out = composite_score(df, {"f": 0.0})
    except ZeroDivisionError:
        pytest.fail("零权重除以 0")
    assert out.isna().all() or (out == 0).all()
