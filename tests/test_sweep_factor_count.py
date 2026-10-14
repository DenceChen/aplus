"""定理守卫**必须能响**——一个只会通过的检查等于没有检查。

## 为什么这一节短却要紧

`scripts/sweep_factor_count.py` 的全部价值在于它能说出
「**因子集缩小，组合 IC 却变大了，这只能是 bug**」。

这正是 2026-10-14 那个 P0 被抓住的方式：逐因子剔除时
**去掉任何一个**因子组合 IC 都近乎翻倍，而
`ρ(w) = (wᵀΣ_f)/√(wᵀRw)` 在 `w ∝ R⁻¹Σ_f` 处的值就是**多重相关**，
少一个因子**只可能降不可能升**。

> 若守卫只会通过，那么下一个 P0 会**又一次**从它眼皮底下走过去，
> 而这次的结果读起来仍然像一份好消息。

所以两条断言缺一不可：
- 正例：真实数据上守卫**成立**且退出码为 0
- 反例：**构造**一条违反单调性的链，守卫**必须报 False** 且退出码**非零**
"""
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.sweep_factor_count import check_monotone, IC_NEED, JUDGED_FROM

SCRIPT = Path("scripts/sweep_factor_count.py")
MATRICES = Path("data/reports/ic_matrices.parquet")


def test_a_monotone_chain_passes_every_step():
    """正例：递增的均值链，每一段都该判「成立」。"""
    chain = [(1, 0.060), (2, 0.068), (3, 0.070), (8, 0.071)]
    assert check_monotone(chain) == [True, True, True]


def test_a_chain_that_rises_when_the_factor_set_shrinks_must_be_caught():
    """反例：**k 变小而 IC 变大** —— 这在数学上不可能。

    这条链**逐位还原** 2026-10-14 那个 P0 的形状：
    剔掉一个因子（k=8→7）之后组合 IC 不降反升（+0.13 比 +0.07），
    而实际只有 k=7 读到了**错标**的 R 才可能这样。
    守卫若在这里还判「成立」，它就是装饰。

    > 第一版我把链写成 `[(1,.06),(2,.12),(3,.07)]` 然后断言 `[False, True]`。
    > 实际是 `[True, False]`——**违规在第二步**。
    > 我按直觉猜了期望值而不是读链，**又一次把「看起来像」当成了「对」**。
    > 这在数学上只有一种解法：**要么读，要么别猜**。
    """
    bug_shape = [(7, 0.130), (8, 0.0709)]
    assert check_monotone(bug_shape) == [False], \
        "剔除一个因子反而变强，必须被判为矛盾——它正是那个 P0 的形状"
    # 违规出现在**任意**一段都要抓到，不只是首段
    assert check_monotone([(1, 0.060), (2, 0.120), (3, 0.070)]) == [True, False]


def test_tiny_numeric_noise_must_not_trip_the_guard():
    """浮点噪声**不是**矛盾。

    门槛算出来是「不增」，实现里有 `lstsq` 兜底与重算，
    最后一位的 ±1e-15 波动是正常的。若守卫不容忍它，
    它迟早会因为一件**无关**的事被人关掉——
    **会误报的守卫比不报更糟**，因为它训练人忽略它。
    """
    chain = [(1, 0.060000000000000), (2, 0.060000000000001)]
    assert check_monotone(chain) == [True]


def test_an_empty_chain_is_vacuously_ok():
    """只有一个元素时没有相邻段可比——不该崩，也不该假装有结论。"""
    assert check_monotone([(8, 0.071)]) == []
    assert check_monotone([]) == []


@pytest.mark.skipif(not MATRICES.is_file(), reason="抽取表不在")
def test_on_the_real_table_the_guard_holds_and_the_exit_code_is_zero(tmp_path):
    """端到端：真表上守卫生效，且**没有**被绕过去。

    退出码这条断言是重点——守卫破了必须 `return 2`，
    否则它只是一行打印出来的字。
    """
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrices", str(MATRICES)],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    out = r.stdout + r.stderr
    assert "【矛盾 = 有 bug】" not in out, out
    assert r.returncode == 0, f"退出码 {r.returncode}\n{out}"
    assert f"{IC_NEED:.5f}" in out, "门槛必须打印出来，否则无法核对用的是同一个"
    assert str(JUDGED_FROM) in out


def test_the_old_positional_table_must_be_refused_not_scanned(tmp_path):
    """旧的位置格式表必须**响亮拒绝**，而不是扫出一堆错标的数。

    这一条与 `test_verdict_composite` 里那条同源：
    扫描脚本读的是同一张表，所以它**同样**会被那份错标的 R 骗到。
    """
    import pandas as pd

    legacy = pd.DataFrame([
        {"date": "2021-06-01", "sf__a": 0.1, "sf__b": 0.2,
         **{f"R__{i}{j}": (1.0 if i == j else 0.2)
            for i in range(2) for j in range(2)}},
    ])
    p = tmp_path / "legacy.parquet"
    legacy.to_parquet(p, index=False)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrices", str(p), "--factors", "a,b"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "旧的位置格式" in out, out