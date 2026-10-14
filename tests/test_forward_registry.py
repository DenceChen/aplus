"""前瞻验证台账：唯一干净的晋级路径。

## 为什么必须是「注册」而不是「跑一次看看」

到这里为止，**平台上每一段可用行情都被我看过了**：

- 2019-01 ~ 2023-12：独立验证（反转方向的来源）
- 2024-10 ~ 2026-10：2 年赛马，看过三轮
- 2019-01 ~ 2026-10：全历史赛马，测试段与 2 年赛马重叠

再跑一次赛马，无论换多好的协议，都不是独立验证——
是**拿同一段数据再自证一次**。方向污染闸数不了这一种：
它数「跑了几组」，数不了「事先就知道该往哪猜」。

唯一干净的路是：**从今天起，把候选冻结下来，让新产生的交易日去检验它们。**

## 三条硬规则

1. **注册即冻结**。注册之后不能删、不能改、不能加。
   要加新候选 = 开一份**新的**注册，从那天起单独计时。
   （否则「每次看到新结果就换一个候选」会伪装成前瞻验证。）
2. **窗口严格晚于注册日**，且留隔离带。
   注册日当天的数据我已经能看到了（今天出单的依据就是它）。
3. **候选集越小，门槛越紧**。前瞻台账通常只有个位数候选，
   Bonferroni 会比 84 组赛马宽得多——这是优势，不是缺陷。

## 与七道闸的关系

前瞻验证**复用** `oos_decision` 的闸，不另造一套。
区别只在于窗口：`[注册日+隔离带, 最新]`，
以及候选池小到 Bonferroni 不会把真信号压死。
"""


import json

import pytest


def _cand(w, every=1, buf=None):
    return {"weights": dict(w), "rebalance_every": every, "buffer_multiple": buf}


CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
REV = {"mom20": -0.5, "turnratio": -0.5}
MOM_LOWTURN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0}


# ── 追加式，注册后不可改 ────────────────────────────────────────

def test_register_then_load(tmp_path):
    from aplus.forward import load_registry, register
    reg = register(tmp_path, "2026-10-12", [_cand(CHAMP), _cand(REV, 21)],
                   note="全历史赛马候选", evidence="race_composite_full_7y.json")
    assert reg["registered_on"] == "2026-10-12"
    assert len(load_registry(tmp_path)) == 1


def test_registry_is_append_only(tmp_path):
    """再注册一次 → 两份都在；第一份一个字都不许变。"""
    from aplus.forward import load_registry, register
    register(tmp_path, "2026-10-12", [_cand(CHAMP)], note="第一批")
    first = json.dumps(load_registry(tmp_path)[0], ensure_ascii=False, sort_keys=True)
    register(tmp_path, "2026-11-01", [_cand(REV, 21)], note="第二批")
    rows = load_registry(tmp_path)
    assert len(rows) == 2
    assert json.dumps(rows[0], ensure_ascii=False, sort_keys=True) == first, \
        "已有注册被改写了——前瞻台账一旦可改就不再是前瞻"


def test_empty_candidate_list_is_rejected(tmp_path):
    from aplus.forward import register
    with pytest.raises(ValueError, match="候选"):
        register(tmp_path, "2026-10-12", [], note="空的")


def test_duplicate_candidate_within_one_registration_is_deduped(tmp_path):
    from aplus.forward import register
    reg = register(tmp_path, "2026-10-12",
                   [_cand(REV, 21), _cand(REV, 21), _cand(REV, 5)],
                   note="重复的")
    assert len(reg["candidates"]) == 2


def test_same_weights_different_frequency_both_kept(tmp_path):
    """频率不同就是不同的策略，去重必须看三元组。"""
    from aplus.forward import register
    reg = register(tmp_path, "2026-10-12", [_cand(REV, 1), _cand(REV, 21)],
                   note="频率轴")
    assert len(reg["candidates"]) == 2


def test_registration_requires_a_date(tmp_path):
    from aplus.forward import register
    with pytest.raises(ValueError, match="注册日期"):
        register(tmp_path, "", [_cand(CHAMP)], note="没有日期")


def test_bad_frequency_is_rejected(tmp_path):
    from aplus.forward import register
    with pytest.raises(ValueError, match="rebalance_every"):
        register(tmp_path, "2026-10-12", [_cand(REV, 0)], note="频率为 0")


# ── 前瞻窗口：严格晚于注册日 ────────────────────────────────────

def test_forward_window_excludes_the_registration_day():
    """注册日当天的数据我已经能看到了（今天出单就靠它）。"""
    from aplus.forward import forward_window
    dates = [f"2026-10-{d:02d}" for d in range(1, 32)]
    w = forward_window(dates, "2026-10-12", embargo_days=2)
    assert w and min(w) > "2026-10-12"
    assert "2026-10-12" not in w


def test_forward_window_applies_embargo():
    from aplus.forward import forward_window
    dates = [f"2026-10-{d:02d}" for d in range(1, 32)]
    w0 = forward_window(dates, "2026-10-12", embargo_days=0)
    w2 = forward_window(dates, "2026-10-12", embargo_days=2)
    assert len(w0) - len(w2) == 2


def test_forward_window_empty_before_registration():
    from aplus.forward import forward_window
    dates = [f"2026-10-{d:02d}" for d in range(1, 12)]
    assert forward_window(dates, "2026-10-12", embargo_days=2) == []


# ── 状态：还剩多少前瞻证据 ──────────────────────────────────────

def test_forward_status_counts_remaining_evidence(tmp_path):
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-10-12", [_cand(CHAMP), _cand(REV, 21)], note="x")
    st = forward_status(tmp_path, as_of="2026-11-20",
                        dates=[f"2026-10-{d:02d}" for d in range(1, 32)]
                        + [f"2026-11-{d:02d}" for d in range(1, 21)])
    assert st["registrations"] == 1
    assert st["n_candidates"] == 2
    assert st["days_since_registration"] > 0
    assert st["days_in_window"] > 0


def test_forward_status_reports_min_observations_needed(tmp_path):
    """K=63 的候选需要至少 3 个调仓周期才可测——不足时要**说不足**，不能给个数字。"""
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-10-12", [_cand(REV, 63)], note="季频")
    st = forward_status(tmp_path, as_of="2026-10-30",
                        dates=[f"2026-10-{d:02d}" for d in range(13, 31)])
    assert st["candidates"][0]["measurable"] is False
    assert st["candidates"][0]["test_periods"] < 3


def test_forward_status_marks_measurable_once_there_are_enough_days(tmp_path):
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-10-12", [_cand(REV, 5)], note="周频")
    dates = [f"2026-10-{d:02d}" for d in range(1, 32)] + \
            [f"2026-11-{d:02d}" for d in range(1, 21)]
    st = forward_status(tmp_path, as_of="2026-11-20", dates=dates)
    row = st["candidates"][0]
    assert row["measurable"] is True
    assert row["test_periods"] == row["window_days"] // 5


# ── 面板窗口为空时必须安静退出，而不是崩在 build_panel ────────────

def test_needs_forward_panel_false_when_no_day_after_registration():
    """注册日 == 最新交易日 → 窗口必空 → 不建面板。

    `build_cross_sections` 的区间是半开的 `[start, end)`，
    start == end 会直接抛 `start < end`。定时任务每天跑一次，
    在窗口攒够之前**每一天都会崩**，而没人盯着 stderr。
    """
    from aplus.forward import needs_forward_panel
    assert needs_forward_panel("2026-10-12", "2026-10-12") is False


def test_needs_forward_panel_true_on_first_trading_day_after():
    from aplus.forward import needs_forward_panel
    assert needs_forward_panel("2026-10-12", "2026-10-13") is True


def test_needs_forward_panel_false_when_data_predates_registration():
    """数据比注册日还早（回放历史 / 时钟回拨）→ 也不能建面板。"""
    from aplus.forward import needs_forward_panel
    assert needs_forward_panel("2026-10-12", "2026-10-09") is False


def test_needs_forward_panel_compares_as_strings_not_datetimes():
    """日期是 'YYYY-MM-DD' 字符串，字典序与时间序一致。
    真日期对象/None 混进来时不得抛异常——这是定时任务的入口。"""
    from aplus.forward import needs_forward_panel
    assert needs_forward_panel("2026-10-12", None) is False
    assert needs_forward_panel(None, "2026-10-12") is False


def test_forward_window_excludes_registration_day_with_zero_embargo():
    """**隔离带为 0 时**注册日也必须被排除。

    这条是判别用例：`test_forward_window_excludes_the_registration_day`
    用的 `embargo_days=2`，而隔离带本身就把注册日挤掉了——
    于是把 `>` 改成 `>=` 它照样绿。一条测不出区别的测试，
    比没有测试更危险：它让变异体报告「测试覆盖到了」。
    """
    from aplus.forward import forward_window
    dates = [f"2026-10-{d:02d}" for d in range(1, 32)]
    w = forward_window(dates, "2026-10-12", embargo_days=0)
    assert w[0] == "2026-10-13"
    assert "2026-10-12" not in w


def test_invalid_rebalance_rejected_through_dedupe():
    """`rebalance_every=0` 进不去台账。

    校验实际发生在 `split_weights` 里（`dedupe` 不再重复一遍），
    但**对外行为**必须由这条测试钉住：这样的候选会算出一个
    永远没有调仓日的序列，静默全零。
    """
    from aplus.forward import dedupe
    with pytest.raises(ValueError, match="rebalance_every"):
        dedupe([_cand({"mom20": 1.0}, every=0)])


# ── 空批次不得落盘：文件会无意义地无限增长 ──────────────────────────

def test_merge_results_skips_empty_batch(tmp_path):
    """窗口为空时**不追加**任何东西。

    实测（2026-10-13）：前瞻窗口因隔离带仍有 0 天，
    `merge_results` 却每跑一次就 append 一个空列表——
    一天下来文件里堆了 5 个 `[]`，看不出每个代表什么、
    也不知道「跑过了但没数据」和「从没跑过」的区别。
    """
    from aplus.forward import merge_results
    merge_results(tmp_path, [])
    p = tmp_path / "forward_results.json"
    assert json.loads(p.read_text(encoding="utf-8")) == []

    merge_results(tmp_path, [{"as_of": "2026-10-14", "table": []}])
    assert len(json.loads(p.read_text(encoding="utf-8"))) == 1


def test_merge_results_appends_nonempty_batches(tmp_path):
    """每次调用追加**一批**（一批 = 当天各份注册的结果）。"""
    from aplus.forward import merge_results
    merge_results(tmp_path, [{"as_of": "2026-10-14", "table": [{"w": 1}]}])
    merge_results(tmp_path, [{"as_of": "2026-10-15", "table": [{"w": 2}]}])
    got = json.loads((tmp_path / "forward_results.json").read_text(encoding="utf-8"))
    assert [batch[0]["as_of"] for batch in got] == ["2026-10-14", "2026-10-15"]


def test_merge_results_on_empty_dir_still_writes_file(tmp_path):
    """第一次调用即使为空也要留下文件——调用方要拿到路径。"""
    from aplus.forward import merge_results
    p = merge_results(tmp_path, [])
    assert p.exists()
