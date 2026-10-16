"""`scripts/verdict_horizon_d1.py` 的判据测试。

## 为什么这组测试必须含**变异臂**

判据 A 是「逐位复算产物的 `half_width`」。它天生容易**恒绿**：
只要代码和产物一起错，两边照样逐位相等。

⇒ 本文件的核心不是「跑得过」，而是**改坏产物后必须报红**
（记忆 `guards-only-count-after-mutation`：测试通过≠在守东西）。

## 三个反控各钉一个不同的失效形状

1. `rate` 字段被改成非 `hits/n` ⇒ 判据 A 必须仍为真
   （钉「实现用的是原值不是显示值」——这正是第一版栽的地方）
2. `half_width` 被改 ⇒ 判据 A 必须为假
3. `n_windows` 被改 ⇒ 判据 A 必须为假
   （钉「W 进了公式」，防止有人把 W 写死成 3）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verdict_horizon_d1 import (
    SRC,
    binomial_se,
    bitwise_readback,
    cluster_se,
    required_W,
)


@pytest.fixture(scope="module")
def overall() -> dict:
    return json.loads(SRC.read_text(encoding="utf-8"))["overall"]


def _mutated(overall: dict, **changes) -> dict:
    out = dict(overall)
    out.update(changes)
    return out


# ── 正控：真产物必须通过 ────────────────────────────────────────────
def test_real_artifact_passes_criterion_A(overall: dict) -> None:
    bitwise, recomputed, in_artifact, p = bitwise_readback(overall)
    assert bitwise is True
    assert recomputed == in_artifact
    # p 必须是 hits/n 的原值，不是 round 过的显示值
    assert p == pytest.approx(overall["hits"] / overall["n"])
    assert p != overall["rate"]          # 两者确实不同，否则反控 #1 无意义


# ── 反控 1：rate 字段被改成别的值，判据 A 必须**仍然通过** ──────────
def test_criterion_A_ignores_the_rounded_display_field(overall: dict) -> None:
    """钉「实现用 hits/n 而不是 rate」。

    第一版用 `float(o["rate"])` 复算，差 1 ulp（0.565313 vs 0.565312），
    判据 A 假性失败。这个反控保证那个 bug 不会回来。
    """
    broken = _mutated(overall, rate=0.9)          # 显示字段被改成任意值
    bitwise, recomputed, in_artifact, _ = bitwise_readback(broken)
    assert bitwise is True
    assert recomputed == in_artifact


# ── 反控 2：half_width 被改 ⇒ 必须报红 ──────────────────────────────
def test_criterion_A_reds_when_half_width_is_tampered(overall: dict) -> None:
    broken = _mutated(overall, half_width=0.1)
    assert bitwise_readback(broken)[0] is False


# ── 反控 3：n_windows 被改 ⇒ 必须报红（钉「W 进了公式」）────────────
def test_criterion_A_reds_when_n_windows_is_tampered(overall: dict) -> None:
    broken = _mutated(overall, n_windows=99)
    assert bitwise_readback(broken)[0] is False


# ── 反控 4：hits/n 被改 ⇒ 必须报红（钉「分子分母都进公式」）──────────
def test_criterion_A_reds_when_hits_is_tampered(overall: dict) -> None:
    broken = _mutated(overall, hits=overall["hits"] + 1)
    assert bitwise_readback(broken)[0] is False


# ── 反解本身 ────────────────────────────────────────────────────────
def test_required_W_law_is_inverse_sqrt() -> None:
    """`required_W(W, hw, hw)` ⇒ W 不变；`required_W(4W, hw, hw)` ⇒ 4W。"""
    assert required_W(3, 0.5, 0.5) == 3
    assert required_W(12, 0.5, 0.5) == 12
    # 半宽砍一半 ⇒ 窗口数 ×4
    assert required_W(3, 0.5, 0.25) == 12


def test_required_W_rejects_nonpositive_target() -> None:
    with pytest.raises(ValueError):
        required_W(3, 0.5, 0.0)


def test_binomial_se_matches_production_formula() -> None:
    import math
    assert binomial_se(0.5, 4) == pytest.approx(math.sqrt(0.5 * 0.5 / 4))


def test_cluster_se_refuses_W_below_2() -> None:
    """与产物同一规矩：W<2 估不出窗口间方差。"""
    with pytest.raises(ValueError):
        cluster_se([0.5])


# ── 对照臂方向：实测是欠离散，必须如实报出 ──────────────────────────
def test_control_arm_reports_underdispersion_as_such(overall: dict) -> None:
    """实测 sd(窗口率)=0.184 < 二项预期 ⇒ 欠离散 ⇒ 反解出的 W 是**上界**。

    这条钉住的是「别悄悄把对照臂的定性反过来说」——
    反了会让读者以为真实所需窗口数比判据臂更小。
    """
    rates = [float(w["rate"]) for w in overall["windows"]]
    p = overall["hits"] / overall["n"]
    W = overall["n_windows"]
    sd_win = cluster_se(rates) * W ** 0.5
    sd_bin = binomial_se(p, W) * W ** 0.5
    assert sd_win < sd_bin, "若上游产物变了导致不再欠离散，本测试会红——那是需要重读结论的信号"


def test_ladder_is_monotone_in_target_half_width(overall: dict) -> None:
    """目标半宽越宽，所需窗口数越少。"""
    hw = float(overall["half_width"])
    W = int(overall["n_windows"])
    seq = [required_W(W, hw, t) for t in (0.02, 0.05, 0.10, 0.20)]
    assert seq == sorted(seq, reverse=True)
