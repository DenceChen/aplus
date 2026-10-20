"""股息率 PIT 管道：PIT 可见性、窗口归属、缺数据三道闸。

## 为什么这三道闸是本管道的第一优先级

`aplus/factor_fundamental.py` 的模块 docstring 写死了本仓的教训：
财报 PIT 之所以要做「出路 B（逐日快照）」，是因为源站每个报告期只存一行、
**没有版本维度** ⇒ 服务的必然是**后修值**。
`aplus/financial.py` 因此写死「在白名单过滤或攒 vintage 之前，
BM / ROE / FAR 一律不实现」。

分红这条路**有一个财报没有的优点**：公告日是记录自带的字段 ⇒
今天采到的全量历史 + 「公告日 ≤ D」过滤 = **能重建任意历史日 D 的 PIT 帧**。
⇒ 因子可以**今天就**在 2021–2026 的判决窗口上跑，不必等几年攒快照。

🚨 但这个优点**同时是它最危险的地方**：
「能重建」只保证**事件的出现时刻**对，不保证**金额**是当时那个金额。
⇒ 覆盖产物里必须把这两件事分开写，不许混成一句「已做 PIT」。

## 本文件守的三件事

1. **PIT 可见性** —— 公告日在 D 之后的方案，D 那一帧**取不到**；
   公告日在 D 之前的**取得到**（反向正控，防「守卫是恒真的装饰」）。
2. **窗口归属** —— 归属按**除权除息日**落进滚动 365 天窗口，
   **与方案所属会计年度无关**（「2025 年度方案在 2026 年公告」这类跨年）。
3. **缺数据** —— 没采到的票 ⇒ **NaN**，**不许编 0**（编 0 会让它看起来像
   「零股息」⇒ 被排序逻辑当成一个合法的低分值）。

## 🚨 本文件**不许**写生产产物路径

所有落盘都用 `tmp_path`。生产路径是
`data/reports/dividend_pit/` 与 `data/reports/dividend_pit_coverage_2026-10-21.json`
—— 测试写进去 = 污染（第 132 层：测试把自己的输出写进生产产物路径，
而新加的章替它洗成了合法）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent

# ── 载入被测脚本 ────────────────────────────────────────────────────

_SCRIPT = ROOT / "scripts" / "collect_dividend_pit.py"


def _load():
    spec = importlib.util.spec_from_file_location("collect_dividend_pit", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_dividend_pit"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ── 夹具 ───────────────────────────────────────────────────────────

def _event(code="600000", announce="2021-03-20", operate="2021-06-15",
           cash=0.32, after_tax="0.288或0.32",
           cash_status=M.CASH_PARSED):
    """一条分红方案事件（形状与 `normalize_events` 的输出逐字一致）。"""
    return {
        "code": code,
        "bs_code": f"sh.{code}",
        "announce_year": int(announce[:4]) if announce else 0,
        "plan_announce_date": announce,
        "agm_date": "",
        "operate_date": operate,
        "pay_date": "",
        "cash_ps_before_tax": cash,
        "cash_status": cash_status,
        "cash_ps_after_tax_raw": after_tax,
        "stock_ps": "0.000000",
        "reserve_to_stock_ps": "",
        "cash_stock_desc": "10派3.2元（含税，扣税后2.88或3.2元）",
        "collected_at": "2026-10-21T00:00:00+00:00",
    }


def _events(*rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=M.EVENT_COLUMNS)
    return pd.DataFrame(list(rows))[M.EVENT_COLUMNS]


# ══════════════════════════════════════════════════════════════════
# 1. PIT 可见性守卫
# ══════════════════════════════════════════════════════════════════

def test_a_plan_announced_after_the_signal_date_is_not_visible_in_that_frame():
    """**公告日在 D 之后的方案，D 那一帧取不到。**

    这是本仓 PIT 的第一道闸。构造：公告日 2021-03-20，信号日 2021-03-19。
    """
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15"))
    frame = M.pit_dividend_frame_from_events(ev, "2021-03-19")
    assert len(frame) == 0, "D 之前未公告的方案在 D 那一帧可见 ⇒ 前视"


def test_a_plan_announced_on_the_signal_date_is_visible():
    """**反向正控**：公告日 = D 当天 ⇒ 必须可见。

    🚨 **没有这一条，上面那条守卫是恒真的** ——
    一个「把所有行都滤掉」的过滤器同样能让「D−1 不可见」成立。
    这条是**能力守卫**（形状 120：承重函数的第一条测试必须是能力守卫）。
    """
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15"))
    frame = M.pit_dividend_frame_from_events(ev, "2021-03-20")
    assert len(frame) == 1, "公告日当天必须可见，否则守卫是装饰"


def test_a_plan_announced_before_the_signal_date_is_visible():
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15"))
    frame = M.pit_dividend_frame_from_events(ev, "2021-04-01")
    assert len(frame) == 1


def test_a_plan_with_no_announcement_date_is_never_visible():
    """**公告日缺失 ⇒ 永远不可见。**

    🚨 缺失若被当成「早就知道了」⇒ 那条记录在 D 之前就带着**未来的金额**出现。
    🚨 也不许把缺失压成空串：空串在字符串比较里小于任何日期，
    正好会**通过** `≤ as_of` 这道闸 ⇒ 前视。
    """
    ev = _events(_event(announce=None, operate="2021-06-15"))
    for as_of in ("2021-03-19", "2021-06-15", "2030-01-01"):
        frame = M.pit_dividend_frame_from_events(ev, as_of)
        assert len(frame) == 0, f"公告日缺失的记录在 {as_of} 可见 ⇒ 前视"


def test_the_pit_anchor_is_the_announcement_date_not_the_ex_dividend_date():
    """**锚是公告日，不是除权除息日。**

    若锚错成除权日：一条 2021-03-20 公告、2021-06-15 除权的方案，
    在 2021-04-01 那一帧会**取不到** ⇒ 整个方案被往后推了一个季度，
    TTM 分子在 Q2 里会整条消失。
    """
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15"))
    frame = M.pit_dividend_frame_from_events(ev, "2021-04-01")
    assert len(frame) == 1, "锚被换成了除权日 ⇒ 公告到除权之间的信息全丢"


def test_the_pit_filter_has_exactly_one_implementation():
    """🚨 **过滤规则只有一份实现**（自证与生产共用）。

    两份副本之间没有守卫（第 7 层 / 第 182 层）：
    自证用一份、生产用另一份 ⇒ 自证可能自证了一个**没被使用**的规则，
    而那种自证照样全绿。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    # 「可见性」判定只应在一个函数体里出现。
    marker = "anchor.notna() & (anchor.astype(str) <= str(as_of))"
    assert src.count(marker) == 1, "PIT 可见性判定出现了多份副本"
    # 生产入口必须复用那个内核，而不是自己再写一遍。
    body = src.split("def pit_dividend_frame(", 1)[1].split("\ndef ", 1)[0]
    assert "pit_dividend_frame_from_events(" in body, \
        "pit_dividend_frame 没走统一内核"


# ══════════════════════════════════════════════════════════════════
# 2. 年化 / 窗口归属守卫
# ══════════════════════════════════════════════════════════════════

def test_a_fiscal_2025_plan_announced_in_2026_belongs_to_the_2026_window():
    """**跨年归属**：2025 年度方案在 2026 年公告 ⇒ 属 2026 窗口，不属 2025。

    这正是用户点名的那个夹具。用**财年**分桶会把一条 2026 年除权的方案
    记到 2025 年去，而 2025 年那天它**还没公告** ⇒ 前视。
    """
    ev = _events(_event(announce="2026-03-25", operate="2026-06-20", cash=0.40))
    covered = {"600000"}
    # 2026-07-01 那一帧：已公告、已除权 ⇒ 计入。
    y2026 = M.ttm_cash_dividend(ev, "2026-07-01", covered_codes=covered)
    assert y2026.loc[0, "ttm_cash_ps"] == pytest.approx(0.40)
    # 2025-07-01 那一帧：**未来**的方案既不可见也不该在窗口里。
    y2025 = M.ttm_cash_dividend(ev, "2025-07-01", covered_codes=covered)
    assert y2025.loc[0, "status"] == M.STATUS_COLLECTED_NO_PAYOUT
    assert y2025.loc[0, "ttm_cash_ps"] == 0.0


def test_a_plan_announced_in_march_is_counted_only_from_its_ex_date():
    """**未除权的不进 TTM 分子。**

    2021-03-20 公告、2021-06-15 除权：
    - 2021-04-01：已公告但**未除权** ⇒ 不在窗口内 ⇒ 不计；
    - 2021-07-01：已除权 ⇒ 计入。
    """
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15", cash=0.32))
    covered = {"600000"}
    before = M.ttm_cash_dividend(ev, "2021-04-01", covered_codes=covered)
    assert before.loc[0, "ttm_cash_ps"] == 0.0
    after = M.ttm_cash_dividend(ev, "2021-07-01", covered_codes=covered)
    assert after.loc[0, "ttm_cash_ps"] == pytest.approx(0.32)


def test_the_ttm_window_is_365_days_with_an_exclusive_left_edge():
    """**窗口 = (D−365, D]**，左开右闭。

    边界是夹具钉死的。D = 2021-06-15 时 D−365 = **2020-06-15**（2020-06-15 → 2021-06-15
    正好 365 天，闰日 2020-02-29 不在这个区间里）⇒ 除权日 2020-06-15 **不算**；
    除权日 2020-06-16（D−364）算。
    """
    covered = {"600000"}
    d = "2021-06-15"
    on_edge = _events(_event(announce="2020-06-15", operate="2020-06-15", cash=1.0))
    assert M.ttm_cash_dividend(on_edge, d, covered_codes=covered).loc[
        0, "ttm_cash_ps"] == 0.0, "窗口左边界必须是开边"
    just_inside = _events(_event(announce="2020-06-16", operate="2020-06-16",
                                 cash=1.0))
    assert M.ttm_cash_dividend(just_inside, d, covered_codes=covered).loc[
        0, "ttm_cash_ps"] == 1.0, "D−364 必须落在窗口里"


def test_several_plans_in_the_window_are_summed():
    """年度 + 中期两笔 ⇒ 分子是**和**（实测 600519 在 2024 年确有 2 行）。"""
    ev = _events(
        _event(code="600519", announce="2024-04-03", operate="2024-06-28",
               cash=30.876),
        _event(code="600519", announce="2024-11-09", operate="2024-12-20",
               cash=23.882),
    )
    out = M.ttm_cash_dividend(ev, "2025-01-15", covered_codes={"600519"})
    assert out.loc[0, "ttm_cash_ps"] == pytest.approx(30.876 + 23.882)
    assert out.loc[0, "n_plans_in_window"] == 2


# ══════════════════════════════════════════════════════════════════
# 3. 缺数据守卫
# ══════════════════════════════════════════════════════════════════

def test_a_code_that_was_never_collected_is_missing_not_zero():
    """🚨 **没采到 ⇒ NaN，不是 0。**

    编 0 会让它看起来像「零股息」⇒ 排序逻辑当成一个合法的低分值 ⇒ 被动持有。
    """
    ev = _events(_event(code="600000"))
    out = M.ttm_cash_dividend(ev, "2021-07-01", covered_codes=set(),
                              codes={"600000"})
    assert out.loc[0, "status"] == M.STATUS_NOT_COLLECTED
    assert pd.isna(out.loc[0, "ttm_cash_ps"]), "没采到的票被编成了 0"


def test_a_collected_code_with_no_payout_in_the_window_is_zero():
    """**三态必须分得开**：采到了、这只票有分红史、但窗口里没分红 ⇒ 0 是事实。"""
    ev = _events(_event(announce="2019-04-01", operate="2019-06-01", cash=0.5))
    out = M.ttm_cash_dividend(ev, "2021-07-01", covered_codes={"600000"})
    assert out.loc[0, "status"] == M.STATUS_COLLECTED_NO_PAYOUT
    assert out.loc[0, "ttm_cash_ps"] == 0.0


def test_a_collected_code_with_no_dividend_history_at_all_is_still_missing():
    """采到了这只票，但**一条分红记录都没有** ⇒ 仍算缺数据。

    分不清「这票从不分红」与「源站压根没这只票的数据」⇒ 不许编 0。
    """
    out = M.ttm_cash_dividend(_events(), "2021-07-01", covered_codes={"600000"})
    assert out.loc[0, "status"] == M.STATUS_NOT_COLLECTED
    assert pd.isna(out.loc[0, "ttm_cash_ps"])


def test_dividend_yield_is_nan_when_the_close_is_missing():
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15", cash=0.32))
    out = M.dividend_yield(ev, {}, "2021-07-01", covered_codes={"600000"})
    assert pd.isna(out.loc[0, "dividend_yield"])


def test_dividend_yield_never_fills_a_missing_dividend_with_zero():
    ev = _events(_event(code="600000", announce="2021-03-20",
                        operate="2021-06-15", cash=0.32))
    out = M.dividend_yield(ev, {"600000": 10.0, "600001": 20.0},
                           "2021-07-01", covered_codes={"600000"})
    got = out.set_index("code").loc["600001", "dividend_yield"]
    assert pd.isna(got), "没采到的票被算成了 0% 股息率"


def test_dividend_yield_divides_the_ttm_dividend_by_the_unadjusted_close():
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15", cash=0.32))
    out = M.dividend_yield(ev, {"600000": 10.0}, "2021-07-01",
                           covered_codes={"600000"})
    assert out.loc[0, "dividend_yield"] == pytest.approx(0.032)


def test_the_denominator_is_the_unadjusted_close_and_not_an_adjusted_one():
    """🚨 **分母是不复权 close** —— 函数**不许**读任何复权因子。

    行为级钉法：喂一个**带** `adjust_factor` 列的 Series 与一个不带的，
    结果必须**逐位相同**。若函数内部偷偷用复权价，两者就会不同。
    （用「源码里有没有 adjust_factor 字样」来钉是第 13 层的坑 ——
    子串会被注释与文案满足。）
    """
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15", cash=0.32))
    plain = pd.Series({"600000": 10.0})
    with_adj = pd.DataFrame({"close": [10.0], "adjust_factor": [3.7]},
                             index=["600000"])
    a = M.dividend_yield(ev, plain, "2021-07-01", covered_codes={"600000"})
    b = M.dividend_yield(ev, with_adj["close"], "2021-07-01",
                         covered_codes={"600000"})
    assert a.loc[0, "dividend_yield"] == b.loc[0, "dividend_yield"]
    # 正控：喂一个**真被复权**的价（10 × 3.7）必须给出不同的收益率，
    # 否则上面那条「逐位相同」可能是恒真的。
    c = M.dividend_yield(ev, {"600000": 37.0}, "2021-07-01",
                         covered_codes={"600000"})
    assert c.loc[0, "dividend_yield"] != a.loc[0, "dividend_yield"]


def test_the_numerator_is_before_tax_and_survives_a_messy_after_tax_field():
    """**分子是税前**。源站税后字段实测形如 `'0.288或0.32'`（带中文）。"""
    ev = _events(_event(announce="2021-03-20", operate="2021-06-15",
                        cash=0.32, after_tax="0.288或0.32"))
    out = M.ttm_cash_dividend(ev, "2021-07-01", covered_codes={"600000"})
    assert out.loc[0, "ttm_cash_ps"] == pytest.approx(0.32)


def test_an_unparsable_cash_amount_becomes_none_not_zero():
    assert M.parse_cash_ps("0.32") == pytest.approx(0.32)
    assert M.parse_cash_ps("") is None
    assert M.parse_cash_ps(None) is None
    assert M.parse_cash_ps("None") is None
    assert M.parse_cash_ps("10派3.2元") is None, "解析失败被编成了 0"


def test_normalize_events_turns_an_unparsable_cash_amount_into_none_not_zero():
    """🚨 **归一化层**也必须守住「解析失败 ≠ 0」。

    ⚠️ 上一版只测了 `parse_cash_ps` 这个**纯函数** ——
    变异把归一化里的 `parse_cash_ps(v)` 换成「失败就给 0.0」时，
    那条测试**照样全绿**（它没经过归一化这一层）。
    ⇒ 「同一个语义在同一函数里被特判 N 次，N+1 处几乎必然是漏的」
    （本仓形状 17）。
    """
    raw = pd.DataFrame([{
        "code": "sh.600000",
        M.PLAN_ANNOUNCE_COL: "2021-03-20",
        M.OPERATE_COL: "2021-06-15",
        M.CASH_BEFORE_TAX_COL: "10派3.2元",
        M.CASH_AFTER_TAX_COL: "0.288或0.32",
    }])
    out = M.normalize_events(raw, "sh.600000", 2021, "2026-10-21T00:00:00+00:00")
    assert pd.isna(out.loc[0, "cash_ps_before_tax"]), "解析失败被编成了 0"
    # 税后字段带中文也**不许**炸（它只留档，不进计算）。
    assert out.loc[0, "cash_ps_after_tax_raw"] == "0.288或0.32"


def test_normalize_events_keeps_the_announcement_date_as_the_pit_anchor():
    raw = pd.DataFrame([{
        "code": "sh.600000",
        M.PLAN_ANNOUNCE_COL: "2021-03-20",
        M.OPERATE_COL: "2021-06-15",
        M.CASH_BEFORE_TAX_COL: "0.32",
    }])
    out = M.normalize_events(raw, "sh.600000", 2021, "2026-10-21T00:00:00+00:00")
    assert out.loc[0, "plan_announce_date"] == "2021-03-20"
    assert out.loc[0, "operate_date"] == "2021-06-15"
    assert out.loc[0, "code"] == "600000"


def test_normalize_events_never_turns_a_missing_announcement_date_into_a_string():
    """🚨 **空日期 ⇒ None**，不是空串。

    空串在字符串比较里**小于任何日期** ⇒ 会**通过** `≤ as_of` 这道闸 ⇒ 前视。
    """
    raw = pd.DataFrame([{
        "code": "sh.600000",
        M.PLAN_ANNOUNCE_COL: "",
        M.OPERATE_COL: "2021-06-15",
        M.CASH_BEFORE_TAX_COL: "0.32",
    }])
    out = M.normalize_events(raw, "sh.600000", 2021, "2026-10-21T00:00:00+00:00")
    # pandas 会把缺失表示成 `None` / `pd.NA` / `NaN`（取决于 dtype）——
    # **判据是「是不是缺失」，不是「是不是 None」**。
    assert pd.isna(out.loc[0, "plan_announce_date"]), \
        "空日期没有变成缺失值"
    assert not (out.loc[0, "plan_announce_date"] == ""), \
        "空日期被压成了空串（空串会通过 ≤ as_of 那道闸 ⇒ 前视）"


# ══════════════════════════════════════════════════════════════════
# 8. 源站把同一个方案返回两遍（本轮实测踩到）
# ══════════════════════════════════════════════════════════════════

def test_resolve_cash_distinguishes_no_cash_from_unreadable():
    """🚨 **「没有现金」与「读不出来」必须是两个值。**

    | 输入 | 期望 |
    |---|---|
    | 税前字段可解析 | `(值, "parsed")` |
    | 税前字段空 + 描述含「派」 | `(None, "unreadable")` ← 确实有现金，数字读不出 |
    | 税前字段空 + 描述只有「转/送」 | `(0.0, "no_cash")` ← 事实上的零 |
    | 税前字段空 + 描述也空 | `(None, "unreadable")` ← 分不清 ⇒ 缺数据 |
    """
    assert M.resolve_cash("0.32", "10派3.2元（含税，扣税后2.88或3.2元）") == \
        (pytest.approx(0.32), M.CASH_PARSED)
    assert M.resolve_cash("", "10派0.75元（含税，扣税后0.675或0.75元）") == \
        (None, M.CASH_UNREADABLE)
    assert M.resolve_cash("", "10转4.9") == (0.0, M.CASH_NO_CASH)
    assert M.resolve_cash("", "") == (None, M.CASH_UNREADABLE)
    # 正控：可解析的值**不能**被当成 no_cash（否则真分红被抹成 0）。
    assert M.resolve_cash("0.075", "10派0.75元")[1] == M.CASH_PARSED


def test_a_plan_returned_twice_by_the_source_becomes_one_event():
    """🚨 **源站把同一个方案返回两遍**（一遍带金额、一遍金额为空）。

    实测命中：002403 与 300261 的 2021 年度方案
    （描述两行完全相同：`10派0.75元（含税，扣税后0.675或0.75元）`）。

    🚨 **不去重 ⇒ TTM 分子重复计入** ⇒ 股息率虚高 ⇒ 因子被高估；
    而其中一行还是 NaN ⇒ `sum(min_count=1)` 跳过它 ⇒ 数字**看着正常**。
    """
    raw = pd.DataFrame([
        {"code": "sh.002403", M.PLAN_ANNOUNCE_COL: "2021-04-28",
         M.OPERATE_COL: "2021-07-20", M.CASH_BEFORE_TAX_COL: "0.075",
         "dividCashStock": "10派0.75元（含税，扣税后0.675或0.75元）"},
        {"code": "sh.002403", M.PLAN_ANNOUNCE_COL: "2021-04-28",
         M.OPERATE_COL: "2021-07-20", M.CASH_BEFORE_TAX_COL: "",
         "dividCashStock": "10派0.75元（含税，扣税后0.675或0.75元）"},
    ])
    out = M.normalize_events(raw, "sz.002403", 2021, "2026-10-21T00:00:00+00:00")
    assert len(out) == 1, f"同一个方案留下了 {len(out)} 行 ⇒ TTM 会重复计入"
    assert out.loc[0, "cash_ps_before_tax"] == pytest.approx(0.075)
    assert out.loc[0, "cash_status"] == M.CASH_PARSED


def test_a_duplicated_plan_does_not_double_count_in_the_ttm_window():
    """**端到端**：去重之后 TTM 分子**只计一次**。"""
    raw = pd.DataFrame([
        {"code": "sh.002403", M.PLAN_ANNOUNCE_COL: "2021-04-28",
         M.OPERATE_COL: "2021-07-20", M.CASH_BEFORE_TAX_COL: "0.075",
         "dividCashStock": "10派0.75元"},
        {"code": "sh.002403", M.PLAN_ANNOUNCE_COL: "2021-04-28",
         M.OPERATE_COL: "2021-07-20", M.CASH_BEFORE_TAX_COL: "",
         "dividCashStock": "10派0.75元"},
    ])
    ev = M.normalize_events(raw, "sz.002403", 2021, "2026-10-21T00:00:00+00:00")
    out = M.ttm_cash_dividend(ev, "2021-08-01", covered_codes={"002403"})
    assert out.loc[0, "ttm_cash_ps"] == pytest.approx(0.075)


def test_a_pure_stock_transfer_plan_counts_as_zero_cash_not_as_missing():
    """**纯送转 ⇒ 0 是事实**（688199 的 `10转4.9` 实测如此）。"""
    raw = pd.DataFrame([{
        "code": "sh.688199", M.PLAN_ANNOUNCE_COL: "2024-03-30",
        M.OPERATE_COL: "2024-06-19", M.CASH_BEFORE_TAX_COL: "",
        "dividCashStock": "10转4.9",
    }])
    ev = M.normalize_events(raw, "sh.688199", 2024, "2026-10-21T00:00:00+00:00")
    assert ev.loc[0, "cash_status"] == M.CASH_NO_CASH
    assert ev.loc[0, "cash_ps_before_tax"] == 0.0
    out = M.ttm_cash_dividend(ev, "2024-07-01", covered_codes={"688199"})
    assert out.loc[0, "status"] == M.STATUS_OBSERVED
    assert out.loc[0, "ttm_cash_ps"] == 0.0


def test_a_window_with_one_unreadable_cash_is_missing_not_a_partial_sum():
    """🚨 **部分和不许当数报出来。**

    `sum(min_count=1)` 会跳过 NaN ⇒ 报出来的是**低于真值**的部分和
    ⇒ 股息率偏低 ⇒ 高股息票被系统性看低 ⇒ **掩盖**因子的效果。
    ⇒ 整只票标成缺数据。
    """
    ev = _events(
        _event(code="600000", announce="2021-03-20", operate="2021-06-15",
               cash=0.30),
        _event(code="600000", announce="2021-08-20", operate="2021-11-15",
               cash=None),
    )
    ev["cash_status"] = [M.CASH_PARSED, M.CASH_UNREADABLE]
    out = M.ttm_cash_dividend(ev, "2021-12-01", covered_codes={"600000"})
    assert out.loc[0, "status"] == M.STATUS_UNREADABLE_CASH
    assert pd.isna(out.loc[0, "ttm_cash_ps"]), "部分和被当成 TTM 报了出来"


def test_the_plan_identity_key_excludes_the_amount():
    """🚨 **金额不是方案身份的一部分。**

    含金额的后果：源站把同一方案返回两遍（金额字段一遍有一遍无）⇒ 两行都活 ⇒
    TTM 重复计入。
    """
    assert "cash_ps_before_tax" not in M.EVENT_KEY
    assert list(M.EVENT_KEY) == ["code", "plan_announce_date", "operate_date"]


def test_a_plan_with_two_different_readable_amounts_keeps_the_smaller_one():
    """🚨 **同一方案、两个不同的可解析金额 ⇒ 取较小者。**

    取较大者会**夸大**股息率 ⇒ 高股息票被高估 ⇒ **乐观方向**的偏差
    （会让因子看起来比实际更有效）。
    ⇒ 取较小者（保守），并把冲突数报出来。
    """
    raw = pd.DataFrame([
        {"code": "sh.600000", M.PLAN_ANNOUNCE_COL: "2021-03-20",
         M.OPERATE_COL: "2021-06-15", M.CASH_BEFORE_TAX_COL: "0.075",
         "dividCashStock": "10派0.75元"},
        {"code": "sh.600000", M.PLAN_ANNOUNCE_COL: "2021-03-20",
         M.OPERATE_COL: "2021-06-15", M.CASH_BEFORE_TAX_COL: "0.05",
         "dividCashStock": "10派0.5元"},
    ])
    out = M.normalize_events(raw, "sh.600000", 2021, "2026-10-21T00:00:00+00:00")
    assert len(out) == 1
    assert out.loc[0, "cash_ps_before_tax"] == pytest.approx(0.05), \
        "冲突时取了较大的金额 ⇒ 系统性夸大股息率"
    assert out.attrs.get("n_amount_conflicts") == 1, "金额冲突没有被计数"


# ══════════════════════════════════════════════════════════════════
# 4. 串行 / 限速（baostock 铁律）
# ══════════════════════════════════════════════════════════════════

def test_the_collector_never_issues_two_requests_at_once(tmp_path):
    """🚨 **禁并发** —— 用假 fetcher 量「最大并发数」，钉成 1。

    不是靠注释声称。并发会把 baostock 拉黑（小时级）。
    """
    state = {"cur": 0, "max": 0}
    raw = pd.DataFrame([{
        "code": "sh.600000",
        M.PLAN_ANNOUNCE_COL: "2021-03-20",
        M.OPERATE_COL: "2021-06-15",
        M.CASH_BEFORE_TAX_COL: "0.32",
    }])

    def fake_fetcher(bs_code, year):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        try:
            return raw, "ok"
        finally:
            state["cur"] -= 1

    pool = {"600000": "sh.600000", "600001": "sh.600001"}
    res = M.collect(pool, tmp_path, "2026-10-21", years=(2020, 2021),
                    sleep=0, fetcher=fake_fetcher)
    assert state["max"] == 1, f"采集器出现了并发（最大 {state['max']}）"
    assert res["n_calls"] == 4


def test_a_failed_cell_is_not_the_same_as_an_empty_cell(tmp_path):
    """🚨 **「没查成」与「那票没分红」必须分开记账。**

    合并 ⇒ 没查成的票变成「零股息」⇒ 编造。
    """
    def fake_fetcher(bs_code, year):
        if year == 2021:
            return pd.DataFrame(), "error:10004006:年份类别不正确"
        return pd.DataFrame(), "empty"

    pool = {"600000": "sh.600000"}
    res = M.collect(pool, tmp_path, "2026-10-21", years=(2020, 2021),
                    sleep=0, fetcher=fake_fetcher)
    assert len(res["failed"]) == 1
    assert res["failed"][0]["year"] == 2021
    assert res["empty_cells"] == ["600000:2020"]
    assert not (set(res["empty_cells"]) & {f["code"] for f in res["failed"]})


# ══════════════════════════════════════════════════════════════════
# 5. 落盘形状：照抄仓里的 vintage 布局
# ══════════════════════════════════════════════════════════════════

def test_the_snapshot_uses_the_warehouse_vintage_layout(tmp_path):
    """**分区布局与完整性校验整条复用 `aplus.vintage`。**

    另起一套就得另写一套摘要与完整性校验，而那份校验是本仓目前
    唯一能自证「这份数据没被动过」的机制。
    """
    from aplus import vintage

    def fake_fetcher(bs_code, year):
        return pd.DataFrame([{
            "code": bs_code,
            M.PLAN_ANNOUNCE_COL: "2021-03-20",
            M.OPERATE_COL: "2021-06-15",
            M.CASH_BEFORE_TAX_COL: "0.32",
        }]), "ok"

    pool = {"600000": "sh.600000"}
    res = M.collect(pool, tmp_path, "2026-10-21", years=(2021,),
                    sleep=0, fetcher=fake_fetcher)
    vintage.write_snapshot(tmp_path, "2026-10-21", res["df"])

    assert vintage.available_days(tmp_path) == ["2026-10-21"]
    # 分区文件名沿用 vintage 的模块级常量（**不是**新造一个）。
    assert (tmp_path / "date=2026-10-21" /
            vintage.PARTITION_NAME).is_file()
    # 完整性校验整条复用 ⇒ 白送一条外部锚。
    rep = vintage.verify_integrity(tmp_path)
    assert rep["ok"], rep
    # 账本是追加式的 jsonl。
    assert (tmp_path / vintage.MANIFEST_NAME).is_file()


def test_the_production_paths_are_never_written_by_this_file():
    """🚨 **测试不许写生产产物路径**（第 132 层）。

    ## 为什么用 AST 而不是「字符串不在文件里」

    第一版就是这么写的（`assert "data/reports/..." not in src`），
    **当场被本文件自己的 docstring 判红** ——
    那句路径就写在模块 docstring 里（解释「生产路径是哪个」）。
    ⇒ 这是第 13 层的教科书案例：**子串断言 + 同一文件里存在文档 = 守卫被文档满足**。
    ⇒ 而且它**方向是反的**：真出事的那次，测试恰恰**需要**提到那个路径。

    改成 AST：只看**真正被写盘函数吃掉的字符串常量**
    （`write_text` / `write_bytes` / `to_parquet` / `open` 的实参），
    docstring 是 `ast.Expr` 里的常量，**不是调用实参** ⇒ 抓不到也误报不了。
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    writers = {"write_text", "write_bytes", "to_parquet", "open"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(
            func, "id", "")
        if name not in writers:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("data/reports") or \
                        arg.value.startswith(str(ROOT)):
                    offenders.append(f"{name}({arg.value!r})")
    assert not offenders, f"测试往生产路径写了盘：{offenders}"


def test_the_output_domain_is_the_whole_pool_not_just_the_collected_codes():
    """🚨 **没采到的票必须出现在输出里**（值 = NaN）。

    只报「采到了的那些」⇒ 没采到的票**从横截面里消失**，
    而它们恰恰是**高股息候选**（银行/煤炭这类最容易被采到）
    ⇒ 采样偏差会**系统性抬高**样本的平均股息率。
    """
    ev = _events(_event(code="600000", announce="2021-03-20",
                        operate="2021-06-15", cash=0.32))
    out = M.dividend_yield(ev, {"600000": 10.0, "600001": 20.0, "600002": 30.0},
                           "2021-07-01", covered_codes={"600000"})
    assert set(out["code"]) == {"600000", "600001", "600002"}
    missing = out.set_index("code").loc["600002"]
    assert missing["status"] == M.STATUS_NOT_COLLECTED
    assert pd.isna(missing["dividend_yield"])


def test_the_default_snapshot_root_is_inside_the_repo():
    """**默认根必须是仓内**（不许指向仓外快照）。"""
    assert M.DIVIDEND_ROOT.is_relative_to(ROOT)
    assert M.DIVIDEND_ROOT.name == "dividend_pit"


# ══════════════════════════════════════════════════════════════════
# 6. PIT 自证本身不许是装饰
# ══════════════════════════════════════════════════════════════════

def test_the_pit_self_proof_passes_on_a_well_formed_event_set():
    ev = _events(
        _event(code="600000", announce="2021-03-20", operate="2021-06-15"),
        _event(code="600001", announce="2021-04-25", operate="2021-07-20"),
        _event(code="600002", announce="2022-03-25", operate="2022-06-20"),
    )
    rep = M.pit_visibility_self_proof(ev)
    assert rep["ok"], rep
    assert rep["n_checked"] == 3
    assert rep["visible_at_anchor"] == 3
    assert rep["invisible_before_anchor"] == 3


def test_the_self_proof_fails_when_the_visibility_rule_is_broken(monkeypatch):
    """🚨 **自证必须能失败** —— 否则它是一枚装饰。

    做法：把生产用的过滤内核换成「全放行」（模拟锚被换掉），
    自证必须**红**。若自证在规则被破坏时仍绿 ⇒ 它什么都没验。
    """
    ev = _events(_event(code="600000", announce="2021-03-20",
                        operate="2021-06-15"))

    def broken(events, as_of):
        return events.copy()

    monkeypatch.setattr(M, "pit_dividend_frame_from_events", broken)
    rep = M.pit_visibility_self_proof(ev)
    assert not rep["ok"], "可见性规则被破坏，自证却仍然通过 ⇒ 自证是装饰"
    assert rep["invisible_before_anchor"] == 0


def test_the_self_proof_cannot_pass_on_an_empty_event_set():
    """**空事件集不许判通过**（否则「什么都没采到」会被写成「PIT 正确」）。"""
    rep = M.pit_visibility_self_proof(_events())
    assert not rep["ok"]
    assert rep["n_checked"] == 0


# ══════════════════════════════════════════════════════════════════
# 7. 杂项
# ══════════════════════════════════════════════════════════════════

def test_baostock_code_conversion():
    assert M.baostock_code("000001.SZ") == "sz.000001"
    assert M.baostock_code("600000.SH") == "sh.600000"
    with pytest.raises(ValueError):
        M.baostock_code("600000")


def test_the_cost_account_is_computed_not_hand_written():
    """**成本账由函数算出来**，不许手打数字进文档。"""
    acct = M.cost_account(5460, 8, 0.173, 0.05)
    assert acct["n_calls"] == 5460 * 8
    assert acct["estimated_seconds"] == pytest.approx(5460 * 8 * (0.173 + 0.05))
    assert acct["estimated_hours"] == pytest.approx(
        5460 * 8 * (0.173 + 0.05) / 3600.0)


def test_the_collector_never_concurrently_writes_and_reads_the_same_partition(tmp_path):
    """**续跑**：已落盘的分区必须被读回来并参与去重，而不是被覆盖。"""
    from aplus import vintage

    def fake_fetcher(bs_code, year):
        return pd.DataFrame([{
            "code": bs_code,
            M.PLAN_ANNOUNCE_COL: "2021-03-20",
            M.OPERATE_COL: "2021-06-15",
            M.CASH_BEFORE_TAX_COL: "0.32",
        }]), "ok"

    pool = {"600000": "sh.600000"}
    first = M.collect(pool, tmp_path, "2026-10-21", years=(2021,),
                      sleep=0, fetcher=fake_fetcher)
    vintage.write_snapshot(tmp_path, "2026-10-21", first["df"])
    n_first = len(vintage.pit_frame(tmp_path, "2026-10-21"))

    second = M.collect(pool, tmp_path, "2026-10-21", years=(2021,),
                       sleep=0, fetcher=fake_fetcher)
    assert second["dup_dropped"] >= 1, "续跑没有把重复行记下来"
    assert len(second["df"]) == n_first, "续跑把已有行弄丢了或翻倍了"


# ══════════════════════════════════════════════════════════════════
# 10. 分片 / 配额闸 / 续跑（长跑采集的三个生死开关）
# ══════════════════════════════════════════════════════════════════

def test_the_shard_split_is_deterministic_disjoint_and_complete():
    """🚨 **分片必须确定性、可复现、可断点续采。**

    - 同一份清单 ⇒ 同一片（否则「昨天采到一半」接不上）；
    - 各片**互不相交**且**并起来正好是全池**（否则漏采/重复采）。
    """
    codes = [f"{i:06d}" for i in range(100)]
    a = M.shard_codes(codes, 1, 3)
    b = M.shard_codes(codes, 2, 3)
    c = M.shard_codes(codes, 3, 3)
    assert M.shard_codes(codes, 1, 3) == a, "同一份清单切出不同的片"
    assert set(a) & set(b) == set() and set(b) & set(c) == set()
    assert sorted(a + b + c) == sorted(codes), "三片并起来不等于全池"
    assert len(a) == len(b) == len(c) == 34 or sum(
        len(x) for x in (a, b, c)) == 100


def test_the_shard_split_sorts_its_input_itself():
    """🚨 **分片函数自己必须排序**，不能指望调用方传进来的已排序。

    变异验证抓到：把函数里的 `codes = sorted(codes)` 删掉，**全部测试照样绿**
    —— 因为主路径传进来的确实是排好序的。
    ⇒ 只有**传乱序进去**才钉得住这一层。
    """
    codes = [f"{i:06d}" for i in range(100)]
    shuffled = list(reversed(codes))
    assert M.shard_codes(shuffled, 1, 3) == M.shard_codes(codes, 1, 3), \
        "分片没有自己排序 ⇒ 传乱序会切出不同的片 ⇒ 续跑接不上"
    assert M.shard_codes(shuffled, 2, 5) == M.shard_codes(codes, 2, 5)


def test_the_shard_number_is_one_based():
    """🚨 **分片号从 1 起。** 混用 0 起与 1 起 ⇒ 「第 1 片」会变成第二片。"""
    with pytest.raises(ValueError):
        M.parse_shard("0/3")
    with pytest.raises(ValueError):
        M.parse_shard("4/3")
    assert M.parse_shard("1/3") == (1, 3)
    assert M.parse_shard("auto") is None


def test_the_quota_gate_stops_the_run_instead_of_overrunning(tmp_path):
    """🚨 **配额闸：超了当场停。**

    baostock 日限 5 万次、禁并发、超限**黑名单 6 小时起** ⇒
    超额的后果是**黑名单**，不是「明天补回来」⇒
    ⇒ 不许「今天多跑点明天少跑」。
    """
    n_calls = {"n": 0}

    def fake_fetcher(bs_code, year):
        n_calls["n"] += 1
        return pd.DataFrame(), "empty"

    pool = {f"{i:06d}": f"sh.{i:06d}" for i in range(10)}
    res = M.collect(pool, tmp_path, "2026-10-21", years=(2020, 2021),
                    sleep=0, fetcher=fake_fetcher, max_calls=7)
    assert n_calls["n"] == 7, f"配额闸没挡住：调了 {n_calls['n']} 次"
    assert res["quota_stopped"] is True
    # 停下来的位置也要记账 ⇒ 明天从那里接着走。
    assert len(res["cells"]) == 7


def test_a_same_day_rerun_does_not_reconsume_the_quota(tmp_path):
    """🚨 **同一天重跑不许重复采、不许把额度用两次。**

    机制 = **单元账**（逐 (票, 年) 记状态）⇒ 已采过的格子不再调源站。
    """
    n_calls = {"n": 0}

    def fake_fetcher(bs_code, year):
        n_calls["n"] += 1
        return pd.DataFrame(), "empty"

    pool = {f"{i:06d}": f"sh.{i:06d}" for i in range(6)}
    first = M.collect(pool, tmp_path, "2026-10-21", years=(2020,),
                      sleep=0, fetcher=fake_fetcher)
    assert n_calls["n"] == 6
    second = M.collect(pool, tmp_path, "2026-10-21", years=(2020,),
                        sleep=0, fetcher=fake_fetcher, cells=first["cells"])
    assert n_calls["n"] == 6, "续跑又调了一次源站 ⇒ 额度被用两次"
    assert second["n_skipped_resume"] == 6


def test_a_failed_cell_is_retried_not_skipped(tmp_path):
    """🚨 **失败的单元必须重试。**

    「没查成」与「查过了、那票没分红」是**两个事实**。
    把 error 也当「已采过」跳过 ⇒ 这一格**永远补不上**
    ⇒ 数据缺口永久留下，而进度账还以为它跑过了。
    """
    n_calls = {"n": 0}

    def fake_fetcher(bs_code, year):
        n_calls["n"] += 1
        if bs_code == "sh.000000":
            return pd.DataFrame(), "error:10004006:年份类别不正确"
        return pd.DataFrame(), "empty"

    pool = {"000000": "sh.000000", "000001": "sh.000001"}
    first = M.collect(pool, tmp_path, "2026-10-21", years=(2020,),
                      sleep=0, fetcher=fake_fetcher)
    assert first["cells"]["000000|2020"] == "error"
    assert first["cells"]["000001|2020"] == "empty"
    n_before = n_calls["n"]
    second = M.collect(pool, tmp_path, "2026-10-21", years=(2020,),
                        sleep=0, fetcher=fake_fetcher, cells=first["cells"])
    # 只重试失败的那一格（1 次），成功的那格跳过。
    assert n_calls["n"] - n_before == 1, "失败单元没有被重试"
    assert second["n_skipped_resume"] == 1


def test_the_auto_picker_keeps_the_same_shard_on_the_same_day(tmp_path):
    """🚨 **同一天重跑只续跑那一片**，不许当天再起一片（额度不许用两次）。"""
    M.append_progress(tmp_path, {"day": "2026-10-21", "shard_i": 2,
                                  "n_shards": 3, "complete": True})
    assert M.pick_shard_auto(tmp_path, "2026-10-21", 3) == 2
    # 换一天 ⇒ 挑「第一片没跑完的」。
    assert M.pick_shard_auto(tmp_path, "2026-10-22", 3) == 1


def test_the_auto_picker_advances_past_completed_shards(tmp_path):
    for i in (1, 2):
        M.append_progress(tmp_path, {"day": "2026-10-21", "shard_i": i,
                                      "n_shards": 3, "complete": True})
    assert M.pick_shard_auto(tmp_path, "2026-10-22", 3) == 3


def test_the_auto_picker_cycles_when_every_shard_is_complete(tmp_path):
    """🚨 **全池采完之后作业继续每日跑**（维持「当时读到的」那一层逐日累积）。"""
    for i, d in ((1, "2026-10-19"), (2, "2026-10-20"), (3, "2026-10-21")):
        M.append_progress(tmp_path, {"day": d, "shard_i": i, "n_shards": 3,
                                      "complete": True})
    # 最久没跑的是片 1。
    assert M.pick_shard_auto(tmp_path, "2026-10-22", 3) == 1


def test_the_auto_picker_ignores_progress_from_a_different_shard_count(tmp_path):
    """🚨 **片数一改，老的 `complete` 就是假的。**

    片数变了 ⇒ 切分边界变了 ⇒ 老的 `shard=1-of-5/` 是**孤儿目录**；
    若把老的 `complete` 也算进来，「这一片跑过了」就是**假的**
    ⇒ 那一段永远不会被重采 ⇒ **静默的覆盖空洞**。
    """
    M.append_progress(tmp_path, {"day": "2026-10-21", "shard_i": 1,
                                  "n_shards": 5, "complete": True})
    # 现在用 3 片 ⇒ 第 1 片**没跑过**（老的记录不算数）。
    assert M.pick_shard_auto(tmp_path, "2026-10-22", 3) == 1
    # 用 5 片 ⇒ 第 1 片跑过了 ⇒ 挑第 2 片。
    assert M.pick_shard_auto(tmp_path, "2026-10-22", 5) == 2


def test_a_broken_cells_ledger_is_not_silently_treated_as_empty(tmp_path):
    """🚨 **单元账坏了 ⇒ 报错，不许当空的**（那会让这一片从头重采、白烧额度）。"""
    p = tmp_path / "_cells.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        M.read_cells(p)


def test_the_announced_layer_records_only_the_plans_announced_that_day(tmp_path):
    """🚨 **「当天新公告」那一层只放当天公告的方案。**

    这是「当时读到的」那一层的落点：逐日忠实记录
    「当天新公告了哪些方案、当天读到的金额是多少」。
    """
    raw = pd.DataFrame([
        {"code": "sh.600000", M.PLAN_ANNOUNCE_COL: "2026-10-21",
         M.OPERATE_COL: "2026-12-20", M.CASH_BEFORE_TAX_COL: "0.32",
         "dividCashStock": "10派3.2元"},
        {"code": "sh.600001", M.PLAN_ANNOUNCE_COL: "2026-10-20",
         M.OPERATE_COL: "2026-12-21", M.CASH_BEFORE_TAX_COL: "0.10",
         "dividCashStock": "10派1元"},
    ])
    ev = M.normalize_events(raw, "sh.600000", 2026, "2026-10-21T00:00:00+00:00")
    ann = ev[ev["plan_announce_date"].astype(str) == "2026-10-21"]
    assert len(ann) == 1
    assert ann.loc[0, "code"] == "600000"
    # 另一只票的公告日是昨天 ⇒ 不进今天这一层。
    assert "600001" not in set(ann["code"])


def test_the_announced_partition_is_written_even_when_it_is_empty(tmp_path):
    """🚨 **空分区也是记录。**

    「今天跑了但零条新公告」与「今天没跑」在文件系统上**长得一样**
    ⇒ 不落空分区 ⇒ 那一天变成**静默的空洞**，读的人无法区分。
    """
    from aplus import vintage
    empty = pd.DataFrame(columns=M.EVENT_COLUMNS)
    M.write_announced_layer(tmp_path / "announced", "2026-10-21", empty)
    assert vintage.available_days(tmp_path / "announced") == ["2026-10-21"], \
        "空分区没落 ⇒ 「跑过但零条」与「没跑」无法区分"
    rep = vintage.verify_integrity(tmp_path / "announced")
    assert rep["ok"], rep
    assert len(M.load_announced(tmp_path, "2026-10-21")) == 0


def test_the_resume_path_runs_end_to_end(tmp_path):
    """🚨 **续跑路径必须能跑通**（它是 launchd 每天都会走的那条）。

    ## 这个守卫是为一条**真 bug** 写的

    第一版报告里有一段「本次 0 调用 ⇒ 沿用上一轮实测单次耗时」的分支，
    它引用了 `prev_path` —— 而 `prev_path` 在**后面**才被赋值
    ⇒ **UnboundLocalError**。
    🚨 **它只在续跑时炸**（本次调用数 > 0 时那个分支根本不走）
    ⇒ **第一天真采集测不出来** ⇒ 而那**恰恰是 launchd 每天都会走的**。

    ⇒ 做法：注入假 fetcher，**连跑两次**：
    第一次真采（造出单元账与产物），第二次 0 调用（走进那个分支）。
    """
    from aplus import provenance

    calls = {"n": 0}

    def fake_fetcher(bs_code, year):
        calls["n"] += 1
        return pd.DataFrame(), "empty"

    day = "2026-10-21"
    out = tmp_path / "coverage.json"
    args = M._parse_args([
        "--root", str(tmp_path / "store"),
        "--codes", "600000,000001",
        "--day", day,
        "--out", str(out),
        "--no-lock",
        "--max-codes", "2",
    ])
    root = Path(args.root)
    years = M.DEFAULT_YEARS

    # 第一遍：真采
    rc1 = M._run(args, root, day, years, None, fetcher=fake_fetcher)
    assert rc1 == 0, "第一遍采集没跑通"
    assert calls["n"] > 0
    assert out.is_file(), "覆盖产物没落盘"

    # 第二遍：**续跑**（0 调用）⇒ 走「沿用上一轮实测」那个分支
    calls["n"] = 0
    rc2 = M._run(args, root, day, years, None, fetcher=fake_fetcher)
    assert rc2 == 0, "续跑路径炸了 —— 这正是 launchd 每天会走的那条"
    assert calls["n"] == 0, "续跑又调了源站 ⇒ 额度被用两次"

    import json as _json
    rep = _json.loads(out.read_text(encoding="utf-8"))
    assert rep["scope"]["n_calls"] == 0
    assert rep["scope"]["n_skipped_resume"] > 0
    # 单次耗时必须**沿用上一轮**（不是 0，否则成本账会说「预计 0 秒」）
    assert rep["scope"]["per_call_mean_s"] > 0
    assert rep["scope"]["per_call_mean_s_source"] == "previous_run_carried_forward"


def test_the_deliverables_have_no_character_level_corruption():
    """🚨 **中文散文被字符级损坏**（本仓第 92 层）—— 而且专挑注释。

    本轮实测抓到 **3 处** U+FFFD 替换字符，全在中文注释/散文里
    （代码本身照常跑，**没有任何报错**）。
    ⇒ 交付物里出现 U+FFFD ⇒ 那一处的内容已经**不可读**。

    ⚠️ 判据是**码点**，不是「看起来乱不乱」——
    损坏后的字符仍然是合法的 UTF-8，`compile()` 通过、`pytest` 全绿。
    """
    files = [
        ROOT / "scripts" / "collect_dividend_pit.py",
        ROOT / "tests" / "test_dividend_pit.py",
        ROOT / "docs" / "PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md",
        ROOT / "docs" / "com.aplus.dividendpit.plist",
    ]
    for f in files:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        # 🚨 **用转义写法，不写字面量** —— 否则这条守卫会**把自己判死**
        # （它自己的源码里就有那个字符）。这与「守卫不许给自己发信号」同族。
        bad = [(i + 1, line) for i, line in enumerate(text.splitlines())
               if "\ufffd" in line]
        assert not bad, f"{f.name} 有 {len(bad)} 处字符级损坏：{bad[:3]}"


def test_the_sweep_ledger_is_not_the_coverage_ledger(tmp_path):
    """🚨 **两份单元账必须分开命名。**

    「这只票采到了没有」取决于**采了几个年份**：
    - `_cells.json` = 全年份（8 年）⇒「采到了」是真命题；
    - `_cells_sweep.json` = 只有当年 ⇒「只采了当年」。

    ⇒ 若合并，消费者（因子脚本）会把「只采了当年」的票判成「完全覆盖」
    ⇒ 在 2021 年那种**需要 2019–2021 三年**的信号日上，
    它会被算出一个**偏小的 TTM 股息率**却标成「已观测」
    ⇒ **静默的覆盖不足**（方向：低估高股息 ⇒ 掩盖因子效果）。
    """
    full = M.cells_path(tmp_path, "2026-10-21", kind="full")
    sweep = M.cells_path(tmp_path, "2026-10-21", kind="sweep")
    assert full != sweep
    assert full.name == M.CELLS_LEDGER_NAME
    assert sweep.name == M.CELLS_SWEEP_LEDGER_NAME
    # 「只采了当年」的账**不许**落在覆盖账的名字上。
    assert sweep.name != M.CELLS_LEDGER_NAME


def test_the_concurrency_lock_refuses_a_second_collector(tmp_path):
    """🚨 **禁并发要由工具强制，不靠约定。**

    baostock 禁并发 ⇒ 两个采集同时跑会撞源站限制、互相覆盖同一个
    `date=` 分区、并把额度用两次。
    """
    lock = M.acquire_lock(tmp_path)
    assert lock is not None and lock.is_file()
    # 第二次取锁必须失败（持有者就是本进程 ⇒ 活着且命令行含脚本名）。
    assert M.acquire_lock(tmp_path) is None
    M.release_lock(lock)
    # 放掉之后能再取。
    lock2 = M.acquire_lock(tmp_path)
    assert lock2 is not None
    M.release_lock(lock2)


def test_a_stale_lock_from_a_dead_process_is_taken_over(tmp_path):
    """🚨 **锁文件不能永久卡死采集。**

    持有者已死（PID 被回收）⇒ 锁必须能被接管。
    ⇒ 否则一次崩溃就永久停掉每日采集。
    """
    import os
    lock_path = tmp_path / M.LOCK_NAME
    lock_path.write_text(json.dumps({"pid": 999999, "cmdline": "dead"}),
                         encoding="utf-8")
    lock = M.acquire_lock(tmp_path)
    assert lock is not None, "过期锁没有被接管 ⇒ 采集永久卡死"
    M.release_lock(lock)

PREREG = ROOT / "docs" / "PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md"
COVERAGE = ROOT / "data" / "reports" / "dividend_pit_coverage_2026-10-21.json"
WIDTH_MDE = ROOT / "data" / "reports" / "factor_width_mde.json"


def test_the_prereg_cites_the_artifact_values_exactly():
    """🚨 **文档里引用的产物数字必须与产物一致（口径与容差写死）。**

    「手打进解释性文字的数值会静默过期」（本仓第 140 层）：
    产物会重跑，散文不会 ⇒ 散文的数会变成传闻，而它**读起来完全正常**。

    ## 口径

    - **分数类**（`gross_annual_pct` / `annual_sd_pct` / `MDE_annual_pct` /
      `hurdle_width_aware_annual_pct`）⇒ 文档写**百分数**、**4 位小数**
      ⇒ 绝对容差 **5e-7**（百分数口径）。
    - **无量纲类**（`t_blocked`）⇒ 文档写**小数**、**4 位小数**
      ⇒ 绝对容差 **5e-5**。

    ⚠️ **容差必须写出来**（本仓第 178 层：没定阈值的「一致」等于没有判据）：
    1e-16 与 1.54 在没有阈值时是同一件事。
    """
    import json
    if not PREREG.is_file() or not WIDTH_MDE.is_file():
        pytest.skip("预注册或产物不在盘上")
    doc = PREREG.read_text(encoding="utf-8")
    w = json.loads(WIDTH_MDE.read_text(encoding="utf-8"))["widths"]["10"]
    pct_keys = ("gross_annual_pct", "annual_sd_pct", "MDE_annual_pct",
                "hurdle_width_aware_annual_pct")
    for key in pct_keys:
        want = f"{w[key] * 100:.4f}%"
        assert want in doc, \
            f"预注册没有引用 {key} 的真值 {want}（产物是 {w[key]!r}）"
    # t 是无量纲的，文档写 4 位小数。
    want_t = f"{w['t_blocked']:.4f}"
    assert want_t in doc, \
        f"预注册没有引用 t_blocked 的真值 {want_t}（产物是 {w['t_blocked']!r}）"


def test_the_prereg_hurdle_table_is_reproducible_from_the_repo_primitives():
    """🚨 **预注册里的门槛必须能从仓内原语重算出来。**

    预注册 §4.2 的四个门槛是**手打进文档的** ⇒ 它们会与代码漂移。
    ⇒ 这里**现场重算**并要求与文档里的字符串一致。
    """
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aplus import costs, hurdle
    if not PREREG.is_file():
        pytest.skip("预注册不在盘上")
    doc = PREREG.read_text(encoding="utf-8")
    for K, N in ((21, 10), (21, 30), (63, 10), (63, 30)):
        r = hurdle.rounds_per_year(K)
        per_round = costs.annual_friction(50_000, 1.0, N, 1)
        h = hurdle.hurdle_rate(0.20, per_round, r)
        pct = f"{h * 100:.4f}%"
        assert pct in doc, f"预注册 §4.2 缺 K={K} N={N} 的门槛 {pct}"


def test_the_prereg_states_that_the_book_numbers_are_long_short_not_reachable():
    """🚨 **「书里的多空数不是本项目的可达目标」必须写在预注册里。**

    这条是本仓最贵的一条教训（书里的 IVOL 1.27%/月、t=8.64 是**多空**口径，
    空腿散户做不了）。它一旦被漏掉，下一轮就会拿书里的数当目标。
    """
    if not PREREG.is_file():
        pytest.skip("预注册不在盘上")
    doc = PREREG.read_text(encoding="utf-8")
    for needle in ("多空", "不可达", "9885", "做空"):
        assert needle in doc, f"预注册里没有「{needle}」——多空/长腿的警告丢了"
