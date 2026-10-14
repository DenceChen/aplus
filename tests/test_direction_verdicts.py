"""方向污染闸的**解除条件**：独立验证集上被证实，闸门可以开。

## 闸门原本在防什么

方向污染闸（2026-10-11 19:00 加）拦的是：
「在测试段 A 上看到反转更好 → 于是决定试反转 → 再用测试段 A 验收它」。
训练/测试切分与 Bonferroni 都挡不住这一种。

## 但闸门不能变成一扇焊死的门

它的目的是**要求独立证据**，不是**永久禁止某个方向**。
2026-10-11 19:42 我用 **2019-01~2023-12** 做了验证——
那段数据我**从未看过任何一次赛马结果**（历次赛马都是近 2 年），
而方向认识来自 2024-10~2026-10。两者是分开的：

    发现集 2024-10 ~ 2026-10  → 产生假设「反转可能更好」
    验证集 2019-01 ~ 2023-12  → 检验该假设（t=+5.52，广度t=+3.77）

这就是标准的发现集 / 验证集设计，**方法论上是干净的**。
继续把方向锁死等于因噎废食——那不是严谨，是僵化。

## 解除的三个硬条件

1. 验证集与发现集**时间不重叠**（且重叠为 0 是硬要求）。
2. 验证集上的 t 必须过 **Bonferroni 门槛**（不能是「看起来正」）。
3. **必须留档**：验证集窗口、日期、t 值写进 `DIRECTION_VERDICTS`，
   下一个接手的人一眼能看到「这个方向是怎么被解除的」。

## 解除记录不写在代码注释里

它写在 `UNVERIFIED_DIRECTIONS` 的对照表里，并被测试钉住。
注释会过期，测试不会。
"""
import pytest

from aplus.evolve_race import (
    DIRECTION_VERDICTS, direction_blocked,
)


def test_verdict_records_validation_window():
    """解除记录必须写清：发现集窗口、验证集窗口、t 值。"""
    v = DIRECTION_VERDICTS["negative_momentum"]
    assert v["discovery_window"] == ["2024-10-01", "2026-10-09"]
    assert v["validation_window"] == ["2019-01-02", "2023-12-29"]
    assert v["t"] > 1.96


def test_discovery_and_validation_windows_must_not_overlap():
    """验证集与发现集**不得重叠**——重叠就不是独立验证，是自证。"""
    for name, v in DIRECTION_VERDICTS.items():
        d0, d1 = v["discovery_window"]
        v0, v1 = v["validation_window"]
        assert v1 < d0 or v0 > d1, f"{name}: 发现集与验证集重叠 {v}"


def test_verified_direction_is_no_longer_blocked():
    assert direction_blocked({"mom20": -1.0}, {"negative_momentum"}) is False
    assert direction_blocked({"mom20": -0.5, "turnratio": -0.5},
                             {"negative_momentum"}) is False


def test_direction_without_verdict_is_still_blocked():
    """没有解除记录的方向照旧拦截。"""
    assert direction_blocked({"ivol": -1.0}, {"low_ivol"}) is True


def test_verified_direction_does_not_unblock_other_directions():
    assert direction_blocked({"ivol": -1.0}, {"negative_momentum"}) is False


def test_low_ivol_stays_blocked_because_its_old_evidence_was_leaked():
    """ivol 的历史证据来自前视泄露期，未在干净数据上重验 → 继续拦截。

    这条很容易被顺手放过：既然反转解除了，「把 ivol 也一起解除」显得很自然。
    但 ivol 解除的前提是「在一段没被看过的数据上 t 过门槛」，
    这件事**没有做过**——它的老成绩是泄露产物。
    """
    assert "low_ivol" not in DIRECTION_VERDICTS
    assert direction_blocked({"ivol": -1.0}, {"low_ivol"}) is True


def test_t_threshold_is_enforced_when_registering():
    """登记解除时 t 不达标必须报错，不能靠手改数字绕过。"""
    from aplus.evolve_race import register_direction_verdict
    with pytest.raises(ValueError, match="t"):
        register_direction_verdict("fake", discovery_window=["2024-01-01", "2024-12-31"],
                                   validation_window=["2023-01-01", "2023-12-31"],
                                   t=1.2, note="不够显著")


def test_overlapping_windows_are_rejected_at_registration():
    from aplus.evolve_race import register_direction_verdict
    with pytest.raises(ValueError, match="重叠"):
        register_direction_verdict("fake2",
                                   discovery_window=["2023-01-01", "2024-12-31"],
                                   validation_window=["2024-06-01", "2025-12-31"],
                                   t=9.0, note="自证")
