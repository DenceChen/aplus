"""调仓频率与缓冲区的行为约束。

## 这些测试在防什么

1. **`buffer_hold` 不是恒等参数。** 一个「看起来有用、实则恒等」的缓冲区
   会让回测报告里的换手率下降，却对持仓毫无影响——于是成本下降被误当成 alpha。
   所以必须有一条测试直接断言缓冲区**改变了篮子内容**。
2. **未来信息。** 缓冲区只能用当日排名，不能看未来几天谁掉出去。
3. **默认路径不变。** `every=1` 必须与修复前的日频行为逐位一致，
   否则历史结论会被悄悄改写。
4. **样本量不足时要拒绝比较**，而不是给出一个必然无意义的频率。
"""
import pytest

from aplus.rebalance import (BUFFER_MULTIPLE, buffer_hold, effective_rebalance_every,
                             fill_to_top_n, is_rebalance_day, rebalance_dates,
                             summarize_turnover)


# ── rebalance_dates ────────────────────────────────────────────────────────
def test_every_one_is_identity():
    d = ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert rebalance_dates(d, 1) == d


def test_monthly_like_every_takes_every_nth_from_the_start():
    d = [f"d{i}" for i in range(20)]
    assert rebalance_dates(d, 21) == ["d0"]      # 窗口短于一个周期 → 只首期
    assert rebalance_dates(d, 5) == ["d0", "d5", "d10", "d15"]


def test_is_rebalance_day_agrees_with_rebalance_dates():
    """两个函数必须给出同一套答案，否则调用方会算出矛盾的换手率。"""
    d = [f"d{i}" for i in range(17)]
    for every in (1, 2, 3, 5, 21):
        picked = set(rebalance_dates(d, every))
        got = {x for x in d if is_rebalance_day(d, x, every)}
        assert picked == got, f"every={every} 两函数不一致"


def test_unknown_day_is_never_a_rebalance_day():
    assert is_rebalance_day(["d0", "d1"], "不存在的日", 1) is False


# ── buffer_hold / fill_to_top_n ────────────────────────────────────────────
def test_buffer_hold_is_not_vacuous():
    """防回归：缓冲区必须真的改变结果。取前 10 名但当前持仓里有一只排在第 15 名，
    带宽 3×10=30 时它应被留下。若实现退化成「只取 top_n」，本测试会红。"""
    ranked = [f"s{i:02d}" for i in range(40)]
    current = ["s00", "s15", "s09"]     # s15 在 top-10 之外，但在 30 名之内
    kept = buffer_hold(current, ranked, top_n=10)
    assert "s15" in kept, "带宽内的已有持仓被踢掉了 → 缓冲区恒等，失去意义"
    assert "s00" in kept and "s09" in kept


def test_buffer_hold_drops_positions_that_fell_out_of_band():
    ranked = [f"s{i:02d}" for i in range(40)]
    current = ["s35"]                     # 35 > 30，掉出带宽
    assert buffer_hold(current, ranked, top_n=10) == []


def test_buffer_hold_empty_position_returns_empty():
    ranked = [f"s{i:02d}" for i in range(40)]
    assert buffer_hold([], ranked, top_n=10) == []


def test_fill_to_top_n_keeps_buffered_positions_first():
    ranked = [f"s{i:02d}" for i in range(40)]
    kept = buffer_hold(["s12", "s13"], ranked, top_n=10)
    final = fill_to_top_n(kept, ranked, top_n=10)
    assert len(final) == 10
    assert final[:2] == ["s12", "s13"], "已持有必须排在前面，否则 cost_drag 算错换手"
    assert len(set(final)) == 10, "补足过程引入了重复"


def test_fill_does_not_duplicate():
    ranked = ["a", "b", "c"]
    final = fill_to_top_n(["a", "b"], ranked, top_n=3)
    assert final == ["a", "b", "c"]


def test_fill_truncates_when_already_full():
    ranked = [f"s{i:02d}" for i in range(40)]
    kept = [f"s{i:02d}" for i in range(12)]
    assert len(fill_to_top_n(kept, ranked, top_n=10)) == 10


def test_buffer_multiple_one_degrades_to_plain_top_n():
    """带宽=1 时缓冲区必须退化成原来的 top_n 语义——否则这个参数不可解释。"""
    ranked = [f"s{i:02d}" for i in range(40)]
    current = ["s11"]
    assert buffer_hold(current, ranked, top_n=10, buffer_multiple=1.0) == []


# ── effective_rebalance_every ──────────────────────────────────────────────
def test_sample_too_small_refuses_to_compare():
    """测试段只剩 2 个调仓周期 → 返回 0，让调用方拒绝比较。"""
    dates = [f"d{i}" for i in range(60)]
    assert effective_rebalance_every(dates, requested=21, train_ratio=0.6) == 0


def test_sample_large_enough_keeps_request():
    dates = [f"d{i}" for i in range(600)]
    assert effective_rebalance_every(dates, requested=21, train_ratio=0.6) == 21


def test_daily_always_usable():
    dates = [f"d{i}" for i in range(5)]
    assert effective_rebalance_every(dates, requested=1, train_ratio=0.6) == 1


# ── summarize_turnover ─────────────────────────────────────────────────────
def test_turnover_is_zero_for_constant_basket():
    b = {f"d{i}": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"] for i in range(10)}
    s = summarize_turnover(b, top_n=10)
    assert s["mean"] == pytest.approx(0.0)
    assert s["hold_overlap"] == pytest.approx(1.0)


def test_turnover_is_one_for_fully_disjoint_baskets():
    b = {"d0": [f"a{i}" for i in range(10)], "d1": [f"b{i}" for i in range(10)]}
    s = summarize_turnover(b, top_n=10)
    assert s["mean"] == pytest.approx(1.0)


def test_half_overlap_turnover():
    old = [f"x{i}" for i in range(5)]
    new = old + [f"y{i}" for i in range(5)]
    s = summarize_turnover({"d0": old, "d1": new}, top_n=10)
    # new 含全部 old ⇒ 5 进 **0 出**，分母 2×10 ⇒ 5/20 = 0.25
    # （不是 0.5——「进」与「出」是两笔账，只补不减只算进）
    assert s["mean"] == pytest.approx(0.25)
    assert s["hold_overlap"] == pytest.approx(0.5)


def test_single_day_has_no_turnover():
    s = summarize_turnover({"d0": ["a"]}, top_n=10)
    assert s["mean"] == 0.0 and s["n_days"] == 1


def test_buffered_strategy_has_lower_turnover_than_naive():
    """端到端护栏：同一段排名下，加缓冲区必须显著降低换手。

    场景必须是**真实的**排名波动——变化集中在 top-10 边界（第 10/11 名互换），
    这正是日频横截面最主要的换手来源。

    反面教材（本文件第一版就踩了）：让排名整体后移一位，
    等于把原第 1 名直接甩到第 60 名。此时缓冲区丢弃它是**正确**行为，
    测试却会断言「换手应当下降」，于是一条好实现被打成红。
    **测试场景不真实时，红的是测试，不是代码。**
    """
    ranked = [f"s{i:02d}" for i in range(60)]
    naive, buffered, held = {}, {}, []
    for i in range(20):
        d = f"d{i}"
        # 每期只交换第 10 / 第 11 名——边界处的一次微小换位
        if i > 0:
            ranked[9], ranked[10] = ranked[10], ranked[9]
        naive[d] = ranked[:10]
        held = fill_to_top_n(buffer_hold(held, ranked, 10), ranked, 10)
        buffered[d] = held
    naive_t = summarize_turnover(naive, 10)["mean"]
    buf_t = summarize_turnover(buffered, 10)["mean"]
    assert buf_t < naive_t, f"缓冲区未降低换手：naive={naive_t} buffered={buf_t}"


def test_buffer_multiple_default_is_three():
    assert BUFFER_MULTIPLE == 3.0, "该值在看结果之前已固定，改它需重跑全部历史结论"
