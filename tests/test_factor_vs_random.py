"""因子 vs 随机：全套逻辑的坐标系。

这个实验回答的是全项目优先级最高的问题——**现役因子到底值不值钱**。
所以它的判据必须先被证明是对的（在假数据上），才准被信（在真数据上）。

三条线各钉一件事：
1. `topn_daily` 覆盖的确实是策略真正会买的那一段；
2. `compare` 在纯零假设下**不会**报出显著（假阳性闸）；
3. `compare` 在真信号下**一定**报出显著（灵敏度闸）。
第 2 条和第 3 条必须同时成立——只满足其一，实验就是废的。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.factor_vs_random import PREREG, WIDTHS, compare, topn_daily
from scripts.head_bucket_probe import BUCKET


# ── topn_daily：它圈定的必须是策略真正买的那一段 ──────────────────────

def _daily(n_days=300, seed=1):
    """一份 15 桶的逐日毛收益表。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {b: rng.normal(0.0, 0.02, n_days) for b in range(15)},
        index=pd.date_range("2020-01-01", periods=n_days, freq="D"),
    )


def test_topn_10_is_exactly_bucket_zero():
    """榜宽 10 = 桶 0，与策略的默认 `top_n=10` 一致。"""
    d = _daily()
    assert topn_daily(d, 10).equals(d[0].dropna())


def test_topn_100_is_the_first_ten_buckets_equally_weighted():
    """榜宽 100 = 桶 0..9 的**等权**均值。

    注意是等权：策略实际买的是等权 top-N，
    按因子分数加权等于给一个未验证的量再套一层未验证的权重。
    """
    d = _daily()
    expect = d[[b for b in range(10)]].mean(axis=1).dropna()
    assert topn_daily(d, 100).equals(expect)


def test_topn_never_reaches_past_the_last_bucket():
    """榜宽大于可得排名数时，不得凭空造列。"""
    d = _daily()[[0, 1, 2]]
    for n in (10, 50, 100, 1000):
        s = topn_daily(d, n)
        assert s.notna().all(), f"榜宽 {n} 在只有 3 桶时产生了 NaN"


def test_widths_are_multiples_of_bucket_size():
    """宽度与桶大小必须整除，否则 topn_daily 的截断是隐形的。"""
    assert all(n % BUCKET == 0 for n in WIDTHS), (
        f"WIDTHS={WIDTHS} 与 BUCKET={BUCKET} 不整除——"
        f"实际取到的桶数会静默少一截")


# ── 假阳性闸：纯零假设下不得报显著 ────────────────────────────────────

def test_compare_is_silent_under_a_pure_null():
    """因子臂 = 随机臂（同一份数据复制）→ 差值恒为 0 → t 必须是 None。

    这是最强的零假设：不是「两个独立的零假设样本」，
    而是**字面意义上的同一序列**。任何非 None 的 t 都是实现有 bug。
    """
    d = _daily(n_days=400, seed=2)
    rows = compare(d, d.copy())
    for r in rows:
        assert r["paired_t"] is None, f"榜宽 {r['n']}：同一序列配对 t={r['paired_t']}，应为 None"
        assert r["diff_annual"] == 0.0
        assert r["verdict"] == "样本不足", "t=None 时的判定不能是任何实质结论"


def test_compare_does_not_manufacture_signal_from_independent_noise():
    """两条**独立**的零假设序列 → 差值是纯噪声 → 不得判为「有微弱价值」。

    4 个宽度 = 4 次比较，单次阈值 3.0 时整族假阳性率约 1.2%。
    这里要求**一个都不中**，而不是「别中太多」——
    因为中一个就足以让人在真实数据上误以为因子有效。
    """
    rows = compare(_daily(n_days=400, seed=3), _daily(n_days=400, seed=4))
    positives = [r for r in rows if r["verdict"] == "有微弱价值"]
    assert not positives, (
        "纯噪声下报出了显著："
        + ", ".join(f"N={r['n']} t={r['paired_t']:.2f}" for r in positives))


# ── 灵敏度闸：真信号必须被抓到 ────────────────────────────────────────

def test_compare_detects_a_real_head_edge():
    """给桶 0 加一个稳定的正偏移 → 窄榜宽必须显著。

    只加在桶 0：宽榜宽会把 10 个桶平均掉，效应被稀释，
    所以这个测试同时钉住了「**效应随榜宽增大而衰减**」这个物理事实。
    """
    # 效应量给到 +0.004/日（≈ +100%/年）。看起来夸张，但合成数据的两臂是
    # **各自独立抽样**的，差值方差 = 两臂方差之和；真实场景里两臂共用同一批
    # 股票池，差值方差要小得多。**用真实场景的温和效应量去设计合成测试，
    # 测试会天然偏弱**——这里的夸张是在补偿合成分布的额外噪声，不是在
    # 降低通过标准。
    rng = np.random.default_rng(6)
    n = 1200
    factor = pd.DataFrame({0: rng.normal(0.004, 0.015, n)})
    for b in range(1, 15):
        factor[b] = rng.normal(0.0, 0.015, n)
    random = pd.DataFrame({b: rng.normal(0.0, 0.015, n) for b in range(15)})

    rows = {r["n"]: r for r in compare(factor, random)}
    assert rows[10]["paired_t"] >= 3.0, f"桶0 的真信号没抓到：t={rows[10]['paired_t']:.2f}"
    assert rows[10]["verdict"] == "有微弱价值"


def test_real_edge_attenuates_as_the_basket_widens():
    """同一个信号，窄榜显著、宽榜被稀释——这个衰减必须是单调的。

    如果它不是单调的，说明 `topn_daily` 在某个宽度上取错了桶数。
    """
    rng = np.random.default_rng(6)
    n = 1200
    factor = pd.DataFrame({0: rng.normal(0.004, 0.015, n)})
    for b in range(1, 15):
        factor[b] = rng.normal(0.0, 0.015, n)
    random = pd.DataFrame({b: rng.normal(0.0, 0.015, n) for b in range(15)})

    ts = [r["diff_annual"] for r in sorted(compare(factor, random), key=lambda r: r["n"])]
    assert ts == sorted(ts, reverse=True), (
        f"效应量未随榜宽单调衰减：{['%.4f' % x for x in ts]}"
        f"——topn_daily 在某个宽度上取错了桶数")


def test_compare_flags_a_harmful_factor():
    """因子系统性更差 → 判定必须是「因子有害」，不能被写成「无价值」。

    「没价值」和「有害」在决策上完全不同：前者是不用管，
    后者是要把现役权重撤下来。混为一谈会让一个正在亏钱的因子
    以「尚无定论」的名义继续挂着。
    """
    rng = np.random.default_rng(8)
    n = 900
    factor = pd.DataFrame({b: rng.normal(-0.003, 0.015, n) for b in range(15)})
    random = pd.DataFrame({b: rng.normal(0.0, 0.015, n) for b in range(15)})

    rows = {r["n"]: r for r in compare(factor, random)}
    for n in WIDTHS:
        assert rows[n]["paired_t"] <= -3.0, f"N={n} 的有害信号没抓到"
        assert rows[n]["verdict"] == "因子有害"


def test_verdict_boundaries_are_exactly_at_plus_minus_three():
    """判据的边界就是预登记里写的 ±3.0，不许有隐藏的松紧。"""
    assert PREREG["threshold"] == 3.0
    assert PREREG["verdicts"]["有微弱价值"] == "t ≥ 3.0"
    assert PREREG["verdicts"]["因子有害"] == "t ≤ −3.0"


def test_prereg_declares_that_it_cannot_promote_weights():
    """这个脚本不许写 `weights.json`——它的产出会被别处当成晋级依据。"""
    blob = " ".join(str(v) for v in PREREG.values())
    assert "不写 weights.json" in blob, "预登记里没有「不写 weights.json」这条硬边界"


@pytest.mark.parametrize("short", [[], [0.01, 0.02]])
def test_compare_refuses_to_conclude_from_too_few_days(short):
    """有效日不足 → 一律「样本不足」，不得因为点估计为正就报显著。"""
    d = pd.DataFrame({b: short for b in range(15)})
    for r in compare(d, d.copy()):
        assert r["verdict"] == "样本不足"
        assert r["paired_t"] is None
