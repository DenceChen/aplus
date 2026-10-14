"""实盘持仓状态机：把「每天重新选」和「真的换手」在**出单层面**也拆开。

## 为什么要拆（不是已经有 `aplus/rebalance.py` 了吗）

`aplus/rebalance.py` 有 `rebalance_dates` / `buffer_hold` / `fill_to_top_n`，
但那只在**回测**里被调用。实盘 `run_daily.py` 每天 `build_predictions` 从零选一遍，
**没有上期持仓、没有调仓日历、没有缓冲区**。

后果不是「少赚一点」，是**测的和做的对不上**：
权重文件声明「每 21 个交易日调一次仓」，实盘却每天全换，
年成本从 0.35% 变成 3.5%（独立验证：日频净 −168.9%、月频 +77.6%，方向都反了）。

## 但**推荐**不能跟着降频

用户要的是「**每天**推荐下个交易日会涨的 10 只 + 会跌的 10 只」。
那是**排序**产品，每天都要有。调仓频率管的是**交易**，不是推荐。

把两者混为一谈会有两个坏结果：

- 为了遵守月频而只在调仓日出单 → 用户连续 20 天拿不到名单
- 为了每天出单而每天全换 → 测的是月频、��的是日频

所以正确形态是：**每天照常出 10 涨 10 跌（排序）**，
另外附一段**持仓指令**说明今天该不该动手、该动哪几只。

## 硬约束

- `every=1` 时状态机**必须完全退化为恒等**：出单与持仓指令都和今天之前逐字相同。
  有测试钉死，否则「加了持仓层」会静默改写全部历史行为。
- 状态落盘在 `reports/holdings.json`，**坏了就当没有**并从头建仓——
  宁可多付一次成本，也不能拿着半截状态继续算换手。
- 配置（频率/缓冲）变了 → 强制重新建仓。旧的持仓是按旧配置挑的。
"""

import json

import pytest


def _state(**kw):
    base = {"as_of": "2026-10-09", "days_since_rebalance": 0, "every": 21,
            "buffer_multiple": None, "up": ["000001.SZ"], "down": ["600000.SH"]}
    base.update(kw)
    return base


# ── 恒等：every=1 时状态机不许改变任何东西 ────────────────────────

def test_daily_config_is_always_due():
    from aplus.live_rebalance import is_due
    assert is_due(None, 1) is True
    assert is_due(_state(every=1, days_since_rebalance=0), 1) is True
    assert is_due(_state(every=1, days_since_rebalance=999), 1) is True


def test_daily_config_never_becomes_not_due():
    """every=1 恒为调仓日。否则「加了持仓层」会静默改写全部历史行为。"""
    from aplus.live_rebalance import is_due
    for n in range(0, 30):
        assert is_due(_state(every=1, days_since_rebalance=n), 1) is True


# ── 月频：每隔 K 个交易日调一次 ────────────────────────────────────

def test_monthly_is_not_due_one_day_after_rebalance():
    from aplus.live_rebalance import is_due
    assert is_due(_state(every=21, days_since_rebalance=1), 21) is False


def test_monthly_is_due_on_the_21st_day():
    from aplus.live_rebalance import is_due
    assert is_due(_state(every=21, days_since_rebalance=20), 21) is True


def test_advance_counts_one_trading_day():
    from aplus.live_rebalance import advance
    s = advance(_state(every=21, days_since_rebalance=0), "2026-10-12")
    assert s["days_since_rebalance"] == 1
    assert s["as_of"] == "2026-10-12"


def test_advance_resets_counter_on_rebalance_day():
    from aplus.live_rebalance import advance
    s = advance(_state(every=5, days_since_rebalance=4), "2026-10-12")
    assert s["days_since_rebalance"] == 0


# ── 配置变化必须强制建仓 ──────────────────────────────────────────

def test_frequency_change_forces_a_fresh_rebalance():
    """旧持仓是按旧配置挑的，继续拿着就是拿一套策略的仓位跑另一套策略。"""
    from aplus.live_rebalance import is_due
    assert is_due(_state(every=1), 21) is True


def test_buffer_change_forces_a_fresh_rebalance():
    from aplus.live_rebalance import is_due
    assert is_due(_state(every=21, buffer_multiple=None), 21, buffer_multiple=3.0) is True
    assert is_due(_state(every=21, buffer_multiple=3.0), 21, buffer_multiple=3.0) is False


def test_missing_state_forces_rebalance():
    from aplus.live_rebalance import is_due
    assert is_due(None, 21) is True


# ── 持仓指令 ─────────────────────────────────────────────────────

def test_instruction_on_due_day_tells_user_to_trade():
    from aplus.live_rebalance import instruction
    txt = instruction(due=True, up=["A", "B"], down=["C"], every=21,
                      buffer_multiple=None, held_up=[], held_down=[])
    assert "调仓" in txt and "买入" in txt


def test_instruction_on_hold_day_tells_user_to_do_nothing():
    from aplus.live_rebalance import instruction
    txt = instruction(due=False, up=["A"], down=["C"], every=21,
                      buffer_multiple=None, held_up=["X", "Y"], held_down=["Z"])
    assert "不动" in txt or "无需" in txt
    assert "X" in txt, "持有清单必须显示出来，否则用户不知道手里是什么"


def test_daily_instruction_has_no_hold_section():
    """every=1 时不要多出任何新东西——行为必须与今天之前逐字相同。"""
    from aplus.live_rebalance import instruction
    txt = instruction(due=True, up=["A"], down=["C"], every=1,
                      buffer_multiple=None, held_up=["X"], held_down=["Z"])
    assert txt == ""


# ── 落盘 ─────────────────────────────────────────────────────────

def test_state_round_trips(tmp_path):
    from aplus.live_rebalance import load_state, save_state
    reports = tmp_path / "reports"
    reports.mkdir()
    s = _state(up=["000001.SZ", "600519.SH"])
    save_state(reports, s)
    got = load_state(reports)
    assert got["up"] == ["000001.SZ", "600519.SH"]
    assert got["every"] == 21


def test_missing_state_file_returns_none(tmp_path):
    from aplus.live_rebalance import load_state
    assert load_state(tmp_path / "reports") is None


def test_corrupt_state_file_returns_none_not_a_partial(tmp_path):
    """宁可多付一次成本，也不能拿半截状态继续算换手。"""
    from aplus.live_rebalance import load_state
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "holdings.json").write_text("{这不是 JSON", encoding="utf-8")
    assert load_state(reports) is None


def test_state_missing_required_keys_returns_none(tmp_path):
    from aplus.live_rebalance import load_state
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "holdings.json").write_text(json.dumps({"as_of": "2026-10-09"}),
                                           encoding="utf-8")
    assert load_state(reports) is None


def test_every_must_be_a_positive_int(tmp_path):
    from aplus.live_rebalance import save_state
    reports = tmp_path / "reports"
    reports.mkdir()
    with pytest.raises(ValueError):
        save_state(reports, _state(every=0))


def test_state_with_invalid_counter_is_rejected(tmp_path):
    """**回归测试**（变异测试发现）：上一条只测了「整个字段缺失」，
    被 `REQUIRED_KEYS` 挡住，于是 `days_since_rebalance` 的**类型/范围**校验
    那一支从未被单独检验过——删掉它测试照样全绿。"""
    import json as _json
    from aplus.live_rebalance import load_state
    reports = tmp_path / "reports"
    reports.mkdir()
    for bad in (-1, "3", 3.5, True):
        (reports / "holdings.json").write_text(_json.dumps({
            "as_of": "2026-10-09", "days_since_rebalance": bad, "every": 21,
            "up": [], "down": []}), encoding="utf-8")
        assert load_state(reports) is None, f"计数 {bad!r} 应判为坏状态"


def test_state_with_invalid_every_is_rejected(tmp_path):
    import json as _json
    from aplus.live_rebalance import load_state
    reports = tmp_path / "reports"
    reports.mkdir()
    for bad in (0, -5, "21", 2.5):
        (reports / "holdings.json").write_text(_json.dumps({
            "as_of": "2026-10-09", "days_since_rebalance": 0, "every": bad,
            "up": [], "down": []}), encoding="utf-8")
        assert load_state(reports) is None, f"频率 {bad!r} 应判为坏状态"


def test_state_never_regresses_to_an_earlier_day(tmp_path):
    """**回归**：`guard_daily` 补跑历史日期时会再跑一遍出单，
    那一遍的 `as_of` 比现有状态**早**。无脑覆盖会让持仓状态退化，
    下一次 `is_due` 拿旧的计数去算今天该不该调仓——
    而用户看到的是「今天的名单」，状态却是三天前的。

    补跑不该让状态倒着走：**旧的一天不覆盖新的一天**。
    """
    import json as _json
    from aplus.live_rebalance import load_state, save_state
    reports = tmp_path / "reports"
    reports.mkdir()
    save_state(reports, _state(as_of="2026-10-12", days_since_rebalance=3))
    save_state(reports, _state(as_of="2026-10-09", days_since_rebalance=0))
    got = load_state(reports)
    assert got["as_of"] == "2026-10-12", (
        f"状态被回退到 {got['as_of']}——补跑历史日期不该倒退持仓状态")
    assert got["days_since_rebalance"] == 3
