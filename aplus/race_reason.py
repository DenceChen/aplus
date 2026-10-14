"""显著性否决理由里那些**可以当场复算**的数字。

## 为什么单独一个模块

`oos_decision` 的否决理由里曾经硬编码一句：

    「实测随机权重也能过这道闸（30 组里 1 组），故判为噪音」

它抄自 `scripts/significance_check.py` 在 **另一窗口、另一组权重、
另一个判据**下的一次性运行（`data/reports/significance.json` 可查），
而且那个判据是 `up_excess_vs_market > 0`——一个**零假设下期望过闸率 0.5**
的弱闸，跟 t 闸门没有因果关系。

一句话、一次数值，跨窗口跨权重跨判据地播报「闸门挡不住随机」。
方向恰好是反的：Bonferroni 校正后整池的期望假阳性恒等于 α。
把这个数放到函数外面、给它一个名字，是为了让**它必须被复算**，
而不是被抄写。
"""
from __future__ import annotations


def expected_false_positives(n_comparisons: int, alpha: float = 0.05) -> float:
    """整池的期望假阳性组数。

        E[假阳性] = n × P(|t| > t_crit | H0) = n × (alpha / n) = alpha

    **与候选池大小无关。** 这是 Bonferroni 的定义性质，也是「门槛随
    候选数收紧」没有放松全局错误率的证明：收紧的是**单点**门槛
    （`bonferroni_t`），减少的是**比较次数**（`alpha / n`），两者相消。

    参数留着 `n_comparisons` 不是为了算它——是为了让调用方**被迫**
    把本轮的比较次数说出来，从而意识到这个数其实与它无关。
    """
    return float(alpha)
