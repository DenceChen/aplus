"""冠军 `{mom20:-0.5, turnratio:-0.5}` 对随机的检验。

这个脚本的全部价值在于回答「赛马报的 t=3.96 是不是真的」，
而它的答案几乎完全取决于**登记口径**。所以最要紧的测试不是
「能不能跑出数」，而是「数按什么判据算」。

真值在跑之前先写死：登记成 1 次比较，临界 2.00，冠军活；
登记成 85 次（赛马 84 + 本次 1），临界 3.64，冠军勉强活但余量只剩 8%。
**这就是最贵的一处，必须由测试钉住，不能靠人记。**
"""
import pytest

from scripts.turnratio_vs_random import CHAMPION, K, N_FAMILY, TOP_N


def test_candidate_family_counts_the_84_that_nominated_it():
    """族大小必须是 85，不是 1。

    `turnratio` 是被 `evolve_race` 的 84 组挑出来的。**提名这个动作本身就是
    一次选择**，选择带来的偏差必须计入族大小。若登记成 1 次比较，
    临界从 3.64 掉到 2.00——而冠军报的 t 恰好是 3.96，
    **登记口径直接决定这个冠军死还是活**。
    """
    assert N_FAMILY == 85, (
        f"候选族登记成 {N_FAMILY}——赛马提名用了 84 组，"
        f"把它们漏掉就是用口径去救一个已经被选出来的冠军")


def test_family_accounting_makes_the_gate_materially_stricter():
    """必须真的更严，而不只是数字不同。

    「更严」得是量级上的严：2.00 → 3.64 差 1.8 倍，
    足以把一个 t=3.96 的冠军从「轻松过」变成「余量 8%」。
    少几个百分点的话，这条纪律就是装饰品。
    """
    from aplus.evolve_race import bonferroni_t
    strict = bonferroni_t(N_FAMILY, df=56)
    naive = bonferroni_t(1, df=56)
    assert strict > naive * 1.5, (
        f"族校正后临界 {strict:.2f} 相对单次比较的 {naive:.2f} "
        f"提升不足 1.5 倍——这条纪律没起作用")


def test_family_correction_alone_would_not_have_killed_the_champion():
    """**族校正不足以判死冠军——这正是必须另做对随机检验的原因。**

    初版这条测试写成「赛马 t=3.96 落在两条判据线之间」，跑之前这么以为，
    实测不是：3.96 > 3.64，族校正后它照样过闸。

    所以真正的差别不在登记口径，而在**度量对象**：

        赛马  t=+3.96   因子 − **大盘**
        本次  t=−0.65   因子 − **随机**

    换一个随机篮子当基准，冠军从 +3.96 变成 −0.65，**符号都反了**。
    也就是说 3.96 量的不是选股能力，是「买这 10 只比买大盘平均强多少」——
    而大盘平均自己在涨。任何只跟大盘比的闸，都无法排除「随便买也这么赚」。

    这条测试把这个事实钉住，免得下次看到「差值没检出」就以为
    族校正已经替我们挡掉了这一类错误。**它挡不掉。**
    """
    from aplus.evolve_race import bonferroni_t
    race_t = 3.96
    assert race_t > bonferroni_t(N_FAMILY, df=56), (
        f"赛马 t={race_t} 已低于族校正线 {bonferroni_t(N_FAMILY, df=56):.2f}——"
        f"若这成立，「族校正挡不住、必须换基准」的结论需要重写")
    assert race_t > bonferroni_t(1, df=56), (
        f"赛马 t={race_t} 连单次比较线都过不了——先核对它到底是怎么报的")


def test_candidate_and_horizon_match_the_registered_forward_batch():
    """冠军必须与前瞻验证第1、2批里冻结的完全一致。

    不一致的话，本脚本检验的是一个**没被冻结过**的配置，
    而那个配置的判决权仍然无处安放。
    """
    import json
    from pathlib import Path

    reg = json.loads(
        Path("data/reports/forward_registry.json").read_text(encoding="utf-8"))
    frozen = {(tuple(sorted(c["weights"].items())), c["rebalance_every"])
              for b in reg for c in b.get("candidates", [])}
    key = (tuple(sorted(CHAMPION.items())), K)
    assert key in frozen, (
        f"{CHAMPION} @ K={K} 不在前瞻验证的冻结名单里——"
        f"要么它不该被检验，要么冻结名单没同步")


def test_declares_that_it_cannot_promote_weights():
    """判据里必须写死「不写 weights.json」。

    这个配置早已冻结进前瞻验证，它的判决权在未来数据。
    在样本内窗口把它落盘 = 绕过前瞻验证，而那正是本项目最贵的一条铁律。
    """
    import inspect

    import scripts.turnratio_vs_random as m
    src = inspect.getsource(m)
    assert "不写 weights.json" in src or "no_promotion" in src
    assert "不能作为采纳依据" in src, (
        "必须写明本窗口对 turnratio 是**样本内**的——"
        "赛马训练段就在这段里，即便过闸也只能作诊断")


def test_carries_a_power_warning_for_k21():
    """K=21 非重叠只有约 57 个观测，阴性结论的证明力有限。

    不写这条警告，输出里的「不可区分」会被读成「没有效应」，
    而它只能读作「没有**大**效应」。
    """
    import inspect

    import scripts.turnratio_vs_random as m
    assert "不能否掉存在小效应" in inspect.getsource(m)


def test_horizon_and_breadth_are_not_silently_changed():
    """K 与榜宽必须与赛马一致，否则测的是另一个假设。"""
    assert K == 21, "赛马胜出档位是 K=21；改 K 等于换假设"
    assert TOP_N == 10
    assert CHAMPION == {"mom20": -0.5, "turnratio": -0.5}