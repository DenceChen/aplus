"""研究员候选的执行参数轴测试。

## 这条轴为什么必须存在（2026-10-14 接手 mcode 时发现）

mcode 的 `ic_guided_race` 证明了**执行参数是决定性的**，而不是细节：

    同一组权重 `{mom20:-1.0}`，2019-2023 独立验证
      日频净 −168.9%   周频 −5.3%   月频 +77.6%   季频 +105.7%

    **276 个百分点的差距全部来自两个执行参数。**

而 `evolve_weekly.py` 现在对研究员候选写死：

    # 研究员提的是**纯权重**，一律按日频 / 无缓冲入场。

`ic_guided_race_7y.json` 里 `ic_sign_all` 同权重的三档实测印证了这一点：

    K=1  超额 +0.01%  t=0.21   ← 按日频入场就是这个数，被成本吃光
    K=5  超额 +0.13%  t=3.34   ← 过闸
    K=21 超额 +0.10%  t=3.23   ← 过闸

**所以：研究员即使提出了正确的权重组合，只要强制日频入场，
就等于亲手把它送进成本的地狱**——测出来的必然是「超额 ≤ 0」。

## 判据

研究员**可以**提执行参数，也**必须能**提纯权重（两种形状都合法）。
纯权重仍按日频入场（那是它的默认语义，不许静默改写）。
"""
import json

import pytest

from aplus.evolve_researcher import (
    EXECUTION_GRID,
    parse_candidates,
    validate_candidates,
)


# ---------- 复合形状：权重 + 执行参数 ----------

def test_validate_accepts_compound_shape_with_execution():
    raw = [{"weights": {"mom20": -1.0}, "rebalance_every": 63}]
    out = validate_candidates(raw)
    assert out == [{"weights": {"mom20": -1.0}, "rebalance_every": 63,
                    "buffer_multiple": None}]


def test_validate_plain_weights_shape_still_works():
    """纯权重必须继续能用——历史脚本、回测、台账全是这个形状。"""
    out = validate_candidates([{"mom20": 1.0, "rev5": 0.3}])
    assert out == [{"mom20": 1.0, "rev5": 0.3}]


def test_validate_rejects_execution_outside_grid():
    """执行参数必须在实测网格内——跑没测过的格子不是探索，是浪费。"""
    out = validate_candidates([{"weights": {"mom20": -1.0}, "rebalance_every": 7}])
    assert out == []


def test_validate_rejects_buffer_on_high_frequency():
    """K≥21 加缓冲区在实测里完全无效（成本 35.8%→35.4%、12.2%→12.1%），
    `BUFFER_GRID` 不含该格子 —— 跑它不是探索，是跑一个已知答案的格子。"""
    out = validate_candidates([
        {"weights": {"mom20": -1.0}, "rebalance_every": 21, "buffer_multiple": 3.0},
    ])
    assert out == []


def test_validate_accepts_buffer_on_daily_frequency():
    """K=1 加缓冲是**实测最有效**的一格：成本 246.3% → 122.5%（砍掉一半）。
    日频恰恰最需要缓冲，不是最不需要。"""
    out = validate_candidates([
        {"weights": {"mom20": -1.0}, "rebalance_every": 1, "buffer_multiple": 3.0},
    ])
    assert len(out) == 1
    assert out[0]["buffer_multiple"] == 3.0


def test_validate_accepts_buffer_on_low_frequency():
    out = validate_candidates([
        {"weights": {"mom20": -1.0}, "rebalance_every": 5, "buffer_multiple": 3.0},
    ])
    assert len(out) == 1
    assert out[0]["buffer_multiple"] == 3.0


def test_validate_still_drops_unknown_factors_inside_compound():
    out = validate_candidates([
        {"weights": {"mom20": -1.0, "fake_factor": 1.0}, "rebalance_every": 21},
    ])
    assert len(out) == 1
    assert "fake_factor" not in out[0]["weights"]


def test_validate_dedupes_same_weights_different_frequency():
    """`{mom20:-1} 日频` 与 `{mom20:-1} 月频` 不是同一组策略——
    去重必须看三元组，否则同一组权重的两个频率只留一个，
    另一个的执行轴差异被抹掉。"""
    raw = [
        {"weights": {"mom20": -1.0}, "rebalance_every": 1},
        {"weights": {"mom20": -1.0}, "rebalance_every": 63},
    ]
    out = validate_candidates(raw)
    assert len(out) == 2


# ---------- prompt 契约 ----------

def test_prompt_advertises_execution_axis():
    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt({
        "current_weights": {"mom20": 1.0},
        "baseline_stats": {"overall_hit_rate": 0.445, "hit_rate_up": 0.45,
                           "hit_rate_down": 0.44, "up_excess_vs_market": 0.0,
                           "judged": 100},
    })
    # 必须明确告诉研究员可用的频率档与缓冲区档
    for k in EXECUTION_GRID:
        assert str(k) in prompt
    assert "rebalance_every" in prompt
    assert "buffer_multiple" in prompt


def test_prompt_only_cites_verified_evidence():
    """prompt 里出现的实测数字必须能在报告里对上。

    2026-10-14 教训：prompt 曾写「日频成本占比实测 222%」——
    这个数字在 `data/reports/` 下**任何文件里都查不到**，
    是从上下文里捡来的二手数字。把未溯源的数字当证据喂给研究员，
    等于让它基于一个不存在的事实编推理。

    本测试锁住三组**可复核**的数字：7y 赛马测试段的成本占比与 t 值、
    以及 2019-2023 独立验证的净值。任何新加的数字都要先能被复核。
    """
    from pathlib import Path

    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt({
        "current_weights": {"mom20": 1.0},
        "baseline_stats": {"overall_hit_rate": 0.445, "hit_rate_up": 0.45,
                           "hit_rate_down": 0.44, "up_excess_vs_market": 0.0,
                           "judged": 100},
    })

    race = json.loads(
        (Path(__file__).resolve().parent.parent
         / "data" / "reports" / "ic_guided_race_7y.json").read_text(encoding="utf-8"))
    sign_all = {r["rebalance_every"]: r["test_stats"]
                for r in race["table"] if r.get("source") == "ic_sign_all"}

    # prompt 承诺给研究员看的 K=1 成本占比与 t 值，必须等于报告实测值
    assert f'{sign_all[1]["cost_share"]:.1%}' in prompt, "K=1 成本占比与实测不符"
    assert f't={sign_all[1]["excess_t"]:.2f}' in prompt, "K=1 的 t 值与实测不符"
    assert f'{sign_all[5]["cost_share"]:.1%}' in prompt, "K=5 成本占比与实测不符"
    assert f't={sign_all[5]["excess_t"]:.2f}' in prompt, "K=5 的 t 值与实测不符"


def test_parse_candidates_handles_compound_shape():
    reply = '[{"weights": {"mom20": -1.0, "rev5": 1.0}, "rebalance_every": 63}]'
    cands = parse_candidates(reply)
    assert len(cands) == 1
    assert cands[0]["rebalance_every"] == 63