"""LLM 进化研究员测试：TDD RED 先行。

设计：研究员不直接改系统——只读证据（进化日志/基线/当前权重），产出候选权重
（JSON），经合法性校验后进赛马。LLM 失败 → 只用内置候选（降级路径）。
"""
import pandas as pd
import pytest

from aplus.evolve_researcher import (
    build_research_prompt,
    parse_candidates,
    research_candidates,
    validate_candidates,
)


def _evidence():
    return {
        "current_weights": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
        "baseline_stats": {"overall_hit_rate": 0.445, "hit_rate_up": 0.451, "hit_rate_down": 0.438,
                           "up_excess_vs_market": -0.0063, "judged": 1253},
        "best_challenger": {"weights": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5, "turn20": -0.5},
                            "stats": {"overall_hit_rate": 0.512, "up_excess_vs_market": -0.0061}},
        "market_summary": "2026-07 至 10 月市场震荡，涨组合平均跑输市场 0.6%/日",
    }


# ---------- 提示构造 ----------

def test_prompt_contains_evidence_and_contract():
    prompt = build_research_prompt(_evidence())
    # 关键证据数字必须进入提示（44.5% 基线）
    assert "44.5%" in prompt
    assert "turn20" in prompt
    # 输出契约：JSON 数组 + 字段约束
    assert "JSON" in prompt
    for token in ("mom20", "rev5", "vol20", "vsur", "turn20"):
        assert token in prompt  # 合法因子名清单


# ---------- 候选解析 ----------

def test_parse_candidates_from_clean_json():
    reply = '思考过程……最终候选：\n```json\n[{"mom20": 1.1, "rev5": 0.2, "vol20": 0.4, "vsur": 0.3, "turn20": -0.7}]\n```'
    cands = parse_candidates(reply)
    assert len(cands) == 1
    assert cands[0]["turn20"] == -0.7


def test_parse_candidates_plain_json_without_fence():
    reply = '[{"mom20": 1.0, "rev5": 0.0}]'
    cands = parse_candidates(reply)
    assert len(cands) == 1


def test_parse_candidates_garbage_returns_empty():
    assert parse_candidates("我认为应该加强动量。") == []


# ---------- 合法性校验 ----------

def test_validate_candidates_drops_unknown_factors_and_extremes():
    raw = [
        {"mom20": 1.0, "rev5": 0.3, "fake_factor": 9.9},          # 未知因子：丢弃该因子而非整个候选
        {"mom20": 99.0, "rev5": 0.3},                              # 权重越界 [-3,3]：整个候选剔除
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},   # 合法
    ]
    valid = validate_candidates(raw)
    assert len(valid) == 2
    assert "fake_factor" not in valid[0]
    assert all(abs(v) <= 3.0 for c in valid for v in c.values())


def test_validate_candidates_empty_dedup():
    raw = [{"mom20": 1.0, "rev5": 0.3}, {"rev5": 0.3, "mom20": 1.0}]  # 同一候选两种键序
    assert len(validate_candidates(raw)) == 1


# ---------- 研究员编排：claude -p 优先，直连链兜底 ----------

def test_research_candidates_prefers_claude_p():
    calls = {}

    def fake_claude(prompt):
        calls["claude_prompt"] = prompt
        return '[{"mom20": 1.2, "rev5": 0.1}]'

    class FakeDirect:
        def chat(self, messages, system=""):
            calls["direct"] = True
            return "[]"

    cands = research_candidates(_evidence(), claude_runner=fake_claude, direct=FakeDirect())
    assert cands == [{"mom20": 1.2, "rev5": 0.1}]
    assert "direct" not in calls  # claude -p 成功则直连链未触碰
    assert "44.5%" in calls["claude_prompt"]  # 证据注入


def test_research_candidates_falls_back_when_claude_p_fails():
    def broken_claude(prompt):
        raise RuntimeError("quota exhausted")

    class FakeDirect:
        def chat(self, messages, system=""):
            return '[{"mom20": 0.9}]'

    cands = research_candidates(_evidence(), claude_runner=broken_claude, direct=FakeDirect())
    assert cands == [{"mom20": 0.9}]


def test_research_candidates_validates_llm_output():
    def fake_claude(prompt):
        return '[{"mom20": 99}]'  # 权重越界 → 剔除

    assert research_candidates(_evidence(), claude_runner=fake_claude, direct=None) == []


def test_research_candidates_returns_empty_when_all_fail():
    def broken(prompt):
        raise RuntimeError("down")

    assert research_candidates(_evidence(), claude_runner=broken, direct=None) == []
