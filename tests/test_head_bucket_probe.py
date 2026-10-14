"""排名分桶探针的坐标系必须钉死。

分桶边界是 `head_bucket_probe.py` 全部结论的坐标系：桶 0 就是策略真正买的那
10 只，「头部反了」这句话指的正是它。边界一旦被改，历史所有相关判断都要重看，
所以它必须有自己的测试，而不是散落在报告里靠人记。
"""
import pytest

from scripts.head_bucket_probe import BUCKET, bucket_of


def test_rank_zero_is_the_head_bucket():
    """排名 1（0-indexed 的 0）必须落在桶 0——策略买的就是这一桶。

    偏一格的后果不是数字难看，而是**判决对象换了人**：
    「桶0 低于其余每一桶」如果实际在检验桶1，等于答的是另一个问题。
    """
    assert bucket_of(0) == 0
    assert bucket_of(BUCKET - 1) == 0        # 第 10 名仍是头部
    assert bucket_of(BUCKET) == 1            # 第 11 名进入第二桶


def test_bucket_boundaries_are_exactly_at_multiples_of_bucket_size():
    for k in range(20):
        assert bucket_of(k * BUCKET) == k
        assert bucket_of(k * BUCKET - 1) == k - 1 if k else True


def test_buckets_partition_every_rank_without_gaps():
    """任意长度的排名序列，桶号必须连续且不跳号。"""
    ranks = list(range(1000))
    seen = {bucket_of(r) for r in ranks}
    assert seen == set(range(len(seen)))


def test_paired_t_is_a_paired_test_not_two_independent_ones():
    """配对 t 必须比两个独立 t 敏感——共同的时间波动该被消掉。

    这里的两条序列**逐日完全同步**（`b = a + 常数`），差值序列的方差为 0。
    此时配对 t 在数学上不可估，返回 `None` 是**诚实答案**；如果实现里写了
    `/ 0` 的兜底分支，它会吐出一个 inf 或一个巨大的假 t 值——
    那正是「两个高度相关的序列各自做独立 t，然后比较」这个错误做法的
    典型产物。这个测试就是钉住「宁可缺证据，不可造证据」。
    """
    import math

    from scripts.head_bucket_probe import _paired_t
    import pandas as pd
    import numpy as np

    rng = np.random.default_rng(7)
    a = pd.Series(rng.normal(0.0005, 0.02, 400))      # 强烈的共同波动
    b = a + 0.002                                      # 恒定偏移

    def indep_t(x: pd.Series) -> float:
        return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))

    assert _paired_t(a, b) is None, "常数差值的配对 t 必须为 None，而不是 inf"
    # 反证：同一个 a 单独做独立 t 会被自身波动压得很小——
    # 若有人拿它当「显著性证据」，那证据是被共同波动稀释出来的假象。
    assert indep_t(a) < 1.0, "独立 t 被共同波动压制，这正是必须用配对的理由"


def test_paired_t_returns_none_on_too_few_days():
    from scripts.head_bucket_probe import _paired_t
    import pandas as pd
    assert _paired_t(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0])) is None


def test_paired_t_detects_a_real_consistent_edge():
    from scripts.head_bucket_probe import _paired_t
    import pandas as pd
    import numpy as np

    rng = np.random.default_rng(11)
    a = pd.Series(rng.normal(0.001, 0.01, 500))
    b = a - rng.normal(0.004, 0.01, 500)      # a 系统性高于 b 0.4%/日
    t = _paired_t(a, b)
    assert t is not None and t > 3.0, f"应有强正差，实得 t={t}"


@pytest.mark.parametrize("bad", [[], [1.0], [1.0, 2.0]])
def test_paired_t_refuses_to_guess_from_too_few_observations(bad):
    """样本不足时返回 None 而不是一个大数——宁可缺证据，不可造证据。"""
    from scripts.head_bucket_probe import _paired_t
    import pandas as pd
    assert _paired_t(pd.Series(bad, dtype=float), pd.Series(bad, dtype=float)) is None