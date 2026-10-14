"""改进台账：每次自进化必须留下**可对比的前后数字**。

## 为什么要有这个

用户在「进化」tab 问：「是不是记录了每次自进化改进带来的提升？」

事实是：那个 tab 记的是**候选赛马评估**，跟「改了什么、修好了什么」毫无关系。
而我这一整天做的绝大部分工作——可交易性、ivol 前视泄露、缩尾并列、
交叉校验容差、单日空面板——**根本不会出现在任何界面里**。

更糟的是：这些修复**大多让数字变差**。
可交易性过滤把涨篮子超额从 +0.1965% 打到 −0.0067%；
ivol 泄露修好后，两轮赛马结论直接作废。
一个只记录「提升」的台账会系统性地把这些**记成负功**；
一个什么都不记的界面，则让真正修好的东西隐形。

## 三条不可协商的规则

1. **必须有前后两个实测数字**。只有定性描述（「修好了 X」）直接拒绝入账——
   那正是自我表扬的形态。
2. **方向必须诚实**。数字变差也要入账，标 `worse`。理由：把虚高修成真实
   是进步；把真实藏起来是退步。不许用 `better` 掩盖。
3. **作废的结论必须跟着记**。`invalidates` 字段写明哪些历史结论因这次修复
   不再可信——不写就等于旧结论悄悄继续流传。
"""
import json

import pytest

from aplus.ledger import append_improvement, load_ledger, record_improvement


def _entry(**kw):
    base = dict(
        title="可交易性过滤", category="correctness", severity="critical",
        metric="涨篮子日均超额", before=0.001965, after=-0.000067,
        direction="worse", date="2026-10-11",
        note="52% 的超额来自买不进的交易（一字涨停/停牌）。",
        invalidates=["「涨篮子超额 +0.1965%」"],
    )
    base.update(kw)
    return base


def test_record_requires_before_and_after(tmp_path):
    """只写定性描述的条目必须被拒——那是自我表扬的形态。"""
    with pytest.raises(ValueError, match="前后"):
        record_improvement(tmp_path, _entry(before=None))
    with pytest.raises(ValueError, match="前后"):
        record_improvement(tmp_path, _entry(after=None))


def test_record_requires_metric(tmp_path):
    with pytest.raises(ValueError, match="metric|指标"):
        record_improvement(tmp_path, _entry(metric=""))


def test_direction_must_be_honest(tmp_path):
    """方向由前后数字**推导**，不能由作者声明——否则必然全是 better。"""
    e = load_ledger(tmp_path, _entry(before=0.001, after=-0.002, direction="better"))
    assert e[0]["direction"] == "worse", "数字变差却标 better = 谎报"
    e = load_ledger(tmp_path, _entry(before=-0.002, after=0.001, direction="worse"))
    assert e[0]["direction"] == "better"


def test_direction_flat_when_unchanged(tmp_path):
    e = load_ledger(tmp_path, _entry(before=0.001, after=0.001, direction="better"))
    assert e[0]["direction"] == "flat", "诚实性修复常不改数字，别硬吹成提升"


def test_lower_is_better_metrics(tmp_path):
    """成本 / 并列数 / 错误码这类指标：**下降才是改善**。

    事故（2026-10-11）：最初版本一律按「数字变大=变好」推导方向，
    于是灌入真实数据后 11 条里 6 条被标成 `worse`——
    包括「年成本 16.91% → 0.57%」「Top10 并列 9 → 0」「HTTP 500 → 200」
    「隔离股票 13 → 0」。

    这些全是**实实在在的改善**。一个把改善标成退步的台账，
    比没有台账更有害：它会让人怀疑改善本身，然后不再信任数字。

    作者仍然无权声明「这条是变好还是变坏」——那正是谎报的入口。
    作者只声明**这个指标朝哪边算好**（`higher_is_better`），那是事实而非评价。
    """
    e = load_ledger(tmp_path, _entry(
        title="调仓缓冲区压低换手", metric="日频年化交易成本",
        before=0.1691, after=0.0057, higher_is_better=False, direction="worse"))
    assert e[0]["direction"] == "better"
    e2 = load_ledger(tmp_path, _entry(
        title="修 500", metric="HTTP 状态码",
        before=500, after=200, higher_is_better=False, direction="worse"))
    assert e2[0]["direction"] == "better"


def test_higher_is_better_false_flips_worse(tmp_path):
    """反向指标若真的**上升**，那就是 worse，不许因为"数字变大"说成改善。"""
    e = load_ledger(tmp_path, _entry(
        metric="日频年化交易成本", before=0.0057, after=0.1691,
        higher_is_better=False, direction="better"))
    assert e[0]["direction"] == "worse"


def test_direction_is_never_author_declared(tmp_path):
    """作者写了 `direction='better'` 但数字其实变差 → 必须以数字为准。"""
    e = load_ledger(tmp_path, _entry(
        metric="成本", before=0.1, after=0.2,
        higher_is_better=False, direction="better"))
    assert e[0]["direction"] == "worse", "作者自称 better 不作数"


def test_seed_script_has_no_mislabeled_directions():
    """灌入脚本里每条都必须声明指标语义，否则会默认按「越大越好」误判。"""
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "seed_evolution_ledger.py"
    spec = importlib.util.spec_from_file_location("seed_led", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for e in mod.ENTRIES:
        assert "higher_is_better" in e, (
            f"「{e['title']}」未声明 higher_is_better——"
            f"默认按越大越好会把它判反（{e['before']} → {e['after']}）"
        )


def test_invalidates_is_preserved(tmp_path):
    """作废的结论必须跟着一起记，否则旧结论会悄悄继续流传。"""
    record_improvement(tmp_path, _entry())
    assert load_ledger(tmp_path)[0]["invalidates"] == ["「涨篮子超额 +0.1965%」"]


def test_append_does_not_overwrite(tmp_path):
    record_improvement(tmp_path, _entry(title="A"))
    record_improvement(tmp_path, _entry(title="B"))
    rows = load_ledger(tmp_path)
    assert [r["title"] for r in rows] == ["A", "B"], "台账是追加的，不是覆盖的"


def test_load_empty_is_empty_list_not_error(tmp_path):
    assert load_ledger(tmp_path) == []


def test_load_tolerates_corrupt_file(tmp_path):
    """坏文件不许让整个 tab 500——台账是辅助信息，不是主链路。"""
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "evolution_ledger.json").write_text("{ broken", encoding="utf-8")
    assert load_ledger(tmp_path) == []


def test_summary_counts_by_direction(tmp_path):
    record_improvement(tmp_path, _entry(title="A", before=1, after=2))
    record_improvement(tmp_path, _entry(title="B", before=1, after=-2))
    record_improvement(tmp_path, _entry(title="C", before=1, after=1))
    from aplus.ledger import ledger_summary
    s = ledger_summary(tmp_path)
    assert s == {"total": 3, "better": 1, "worse": 1, "flat": 1,
                 "invalidated_claims": 3}


def test_summary_counts_empty_invalidates_as_zero(tmp_path):
    record_improvement(tmp_path, _entry(invalidates=[]))
    from aplus.ledger import ledger_summary
    assert ledger_summary(tmp_path)["invalidated_claims"] == 0


def test_entry_without_invalidates_key_is_safe(tmp_path):
    """`invalidates` 键**整个不存在**时也必须安全。

    区分两种「没有作废结论」：给空列表（作者明确表示没有），
    与根本不写这个键（作者压根没这个概念）。后者更常见，
    `len(None)` 会直接 TypeError 把整个 summary 端点带崩。
    """
    e = _entry()
    e.pop("invalidates")
    record_improvement(tmp_path, e)
    from aplus.ledger import ledger_summary
    assert ledger_summary(tmp_path)["invalidated_claims"] == 0
    assert load_ledger(tmp_path)[0].get("invalidates") in (None, [])
