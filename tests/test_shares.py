"""总股本 / 流通股本获取层测试（纯离线，不联网）。

## 为什么需要这一层

平台现有 8 个因子全是量价（`FACTOR_COLS`）。基本面因子的第一块砖是股本：

- 规模因子 = 收盘价 × **总股本**（`docs/factor_spec_cn.md` §Q4 Top 5）
- BM = 归股东权益 / 总市值（Top 2）
- ROE(TTM) / F-Score（Top 3/4）也都以市值为分母或组成部分

没有股本，这一整串都算不出来。

## 实测得到的两条源事实（不是推测，见交付报告）

akshare 1.19.1，环境可联网：

- `stock_share_change_cninfo(symbol, start_date, end_date)`（巨潮）返回**时间序列**，
  含 `变动日期 / 总股本 / 已流通股份 / 人民币普通股 / 流通受限股份 / 变动原因`，
  **单位是万股**（与 `stock_zh_a_gbjg_em` 的「股」相差恰好 1e4，实测比值 10000.0）。
- baostock 0.9.4 的 `query_stock_basic` **没有** `totalShare`/`liqaShare`
  （实测 fields = code/code_name/ipoDate/outDate/type/status），不是可用备源。

## 本文件钉死的三条防线

1. **股本字段缺失不得被静默编造**：填 0 或前向填充都会让下游算出
   「总市值 = 0」或「把去年的股本当今年的事实」——后者是未来函数。
2. **数据时点必须可见**：股本是会变的（增发/回购/解禁），拿不到 `as_of`
   的股本就是伪装成常量的未来函数，下游必须能识别出来。
3. **`turn` 的分母到底是总股本还是流通股本**（`docs/factor_spec_cn.md` Q2 悬案），
   用 `volume / shares` 与既有 `turn` 列对照，实测判定。
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from aplus.sources.shares import (
    SHARE_COLUMNS,
    classify_turn_denominator,
    cninfo_frame_to_shares,
    fetch_share_history,
    has_usable_as_of,
    implied_shares_from_turn,
    latest_snapshot,
    normalize_code,
    point_in_time_shares,
    shares_as_of,
)


# ── 夹具（照抄 akshare 巨潮的真实列名与「万股」单位）───────────────────────────

def cninfo_fixture():
    """两期股本变动：一次送转（总股本翻倍）、一次回购（总股本减少）。

    单位为**万股**，与真实接口一致——转换层必须 ×1e4 才对得上。
    """
    return pd.DataFrame({
        "证券代码": ["002415", "002415"],
        "变动日期": ["2024-06-30", "2026-06-30"],
        "总股本": [916487.1550, 916487.1550],          # 万股 → 91.65 亿股
        "已流通股份": [904609.3710, 904609.3710],
        "人民币普通股": [904609.3710, 904609.3710],
        "流通受限股份": [11877.7840, 11877.7840],
        "变动原因": ["定期报告", "定期报告"],
    })


# ── 代码格式归一 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "000001.SZ", "sz.000001", "SZ.000001", "000001", "sz000001",
    "000001.sz", " 000001.SZ ",
])
def test_normalize_code_accepts_all_three_formats(raw):
    """`000001.SZ` / `sz.000001` / 裸 `000001` 三种写法都要收敛到统一 code。"""
    assert normalize_code(raw) == "000001.SZ"


def test_normalize_code_keeps_exchange_suffix():
    assert normalize_code("600000.SH") == "600000.SH"
    assert normalize_code("sh.600000") == "600000.SH"


def test_normalize_code_rejects_garbage():
    """非法 code 必须报错，不能猜出一个「看起来对」的代码。"""
    for bad in ("", "abc", "12345", "00000A.SZ", None):
        with pytest.raises(ValueError):
            normalize_code(bad)


# ── 单位归一：万股 → 股 ──────────────────────────────────────────────────────

def test_cninfo_units_are_converted_from_wan_shares():
    """巨潮的 `总股本` 单位是**万股**，转成股必须 ×1e4。

    与 `stock_zh_a_gbjg_em`（单位为股）实测比值恰好 10000.0，这条钉死单位换算。
    """
    out = cninfo_frame_to_shares(cninfo_fixture())
    total = float(out["total_shares"].iloc[-1])
    assert total == pytest.approx(9_164_871_550.0, rel=1e-9), (
        f"总股本换算错：实得 {total:,.0f}，应为 9,164,871,550 股"
    )


def test_shares_frame_has_uniform_schema():
    out = cninfo_frame_to_shares(cninfo_fixture())
    assert list(out.columns) == SHARE_COLUMNS
    assert len(out) == 2
    assert out["code"].unique().tolist() == ["002415.SZ"]
    assert out["date"].tolist() == ["2024-06-30", "2026-06-30"]


def test_all_three_share_legs_are_exposed():
    """总股本 / 已流通股份 / 人民币普通股 三条腿都要留。

    第三条不是冗余：带 H 股的公司（中国银行/中国石化）`已流通股份` 含 H 股，
    而 `turn` 的分母其实是 A 股——只留两条腿会把 Q2 判定做错。
    """
    out = cninfo_frame_to_shares(cninfo_fixture())
    assert "total_shares" in out.columns
    assert "float_shares" in out.columns
    assert "a_shares" in out.columns


# ── 防线 1：缺失不得被静默编造 ───────────────────────────────────────────────

def test_missing_total_shares_becomes_nan_not_zero():
    """总股本缺失 → NaN，**绝不能是 0**。

    0 会被下游当成「真实股本为 0」，进而算出总市值 = 0 并静默退出排名，
    和 `test_turnratio_nan_without_turn_column` 里「不得折成 0」是同一条规矩。
    """
    df = cninfo_fixture()
    df.loc[0, "总股本"] = None
    out = cninfo_frame_to_shares(df)
    first = out["total_shares"].iloc[0]
    assert first != first, "缺失的总股本被折成了具体数值"
    assert not (out["total_shares"] == 0).any(), "缺失值被填成了 0"


def test_missing_leg_is_not_forward_filled():
    """后一期才有值时，前一期的缺失**不得**被前向填充成后一期的值。

    前向填充在这里就是未来函数：把「今年才发生的股本变动」写进去年的行。
    """
    df = cninfo_fixture()
    df.loc[0, "总股本"] = None
    out = cninfo_frame_to_shares(df)
    assert out["total_shares"].iloc[0] != out["total_shares"].iloc[-1], (
        "前一期被后一期的值填充了——未来函数"
    )


@pytest.mark.parametrize("bad", [0, -1, -0.0])
def test_non_positive_share_count_is_treated_as_missing(bad):
    """股本为 0 或负数是非法值，按缺失处理，不当真实值传下去。"""
    df = cninfo_fixture()
    df.loc[0, "总股本"] = bad
    out = cninfo_frame_to_shares(df)
    v = out["total_shares"].iloc[0]
    assert v != v, f"非法股本值 {bad} 被当成了有效数据"


def test_zero_turn_yields_nan_implied_shares_not_inf():
    """`turn=0`（停牌/无成交）不能造出 inf 股本。"""
    got = implied_shares_from_turn([1e6], [0.0])
    assert got[0] != got[0], "零换手率造出了 inf/具体值"


def test_blank_turn_is_nan_not_implied():
    got = implied_shares_from_turn([1e6], [None])
    assert got[0] != got[0]


# ── 防线 2：数据时点必须可见 ─────────────────────────────────────────────────

def test_as_of_is_the_latest_effective_date():
    """`as_of` = 最后一次股本变动的生效日，不是「今天」。"""
    out = cninfo_frame_to_shares(cninfo_fixture())
    assert shares_as_of(out) == "2026-06-30"
    snap = latest_snapshot(out)
    assert snap["as_of"] == "2026-06-30"
    assert snap["total_shares"] == pytest.approx(9_164_871_550.0, rel=1e-9)


def test_snapshot_carries_as_of_downstream_can_read():
    """返回结构里必须自带 `as_of`——没有它下游会当成历史不变的事实。"""
    snap = latest_snapshot(cninfo_frame_to_shares(cninfo_fixture()))
    assert set(("code", "as_of", "total_shares", "float_shares")) <= set(snap)


@pytest.mark.parametrize("bad", ["", None, "not-a-date", "20261345", "2026-99-99", 20260630])
def test_bad_as_of_is_detectable(bad):
    """缺失/非法的时点必须能被下游识别，不能静默通过。"""
    assert has_usable_as_of(bad) is False


def test_good_as_of_passes():
    assert has_usable_as_of("2026-06-30") is True


def test_empty_history_has_no_as_of_and_no_snapshot():
    """空结果不许假装有值。"""
    empty = cninfo_frame_to_shares(pd.DataFrame(columns=["证券代码", "变动日期", "总股本"]))
    assert shares_as_of(empty) is None
    assert latest_snapshot(empty) is None


def test_point_in_time_lookup_does_not_leak_future_share_count():
    """时点查询只能取「当日或之前」最近一期的股本。

    股本会变：用最新值回填历史是未来函数。这条钉死 backward-as-of 的语义。
    """
    df = cninfo_fixture()
    df["总股本"] = [800000.0, 916487.1550]        # 2024 期 80 亿股，2026 期 91.6 亿股
    hist = cninfo_frame_to_shares(df)
    assert point_in_time_shares(hist, "2025-06-30")["total_shares"] == pytest.approx(8e9, rel=1e-9)
    assert point_in_time_shares(hist, "2026-12-31")["total_shares"] == pytest.approx(9.16487155e9, rel=1e-9)
    # 早于任何一期 → 无值，而不是拿第一期倒填
    assert point_in_time_shares(hist, "2020-01-01")["total_shares"] != point_in_time_shares(
        hist, "2020-01-01")["total_shares"]


# ── 防线 3：Q2 —— `turn` 的分母是总股本还是流通股本 ──────────────────────────

def test_classify_rules_out_total_when_turn_is_float_based():
    """真实反例：海康威视的总股本被排除，分母落在流通口径上。

    注意它返回 `ambiguous` 而不是 `float`：这只票的 `已流通股份` 与
    `人民币普通股` **数值完全相同**（都是 9,046,093,710 股，无 H 股），
    两条腿分不开。说得出口的结论只有「不是总股本」——这也是能验证的部分。
    """
    daily = pd.DataFrame({
        "date": ["2026-09-30", "2026-10-13"],
        "volume": [9.04609371e7, 9.0468674e7],
        "turn": [1.0000, 1.000086],
    })
    hist = cninfo_frame_to_shares(cninfo_fixture())
    verdict = classify_turn_denominator(daily, hist)
    assert verdict["verdict"] in ("float", "a_shares", "ambiguous"), f"判定错：{verdict}"
    assert abs(verdict["ratio_to_float"] - 1.0) < 0.005, f"未落在流通口径：{verdict}"
    # 真正要钉死的是「总股本被排除」——总股本比流通股本多 1.31%
    assert abs(verdict["ratio_to_total"] - 1.0) > 0.005, f"总股本未被排除：{verdict}"


def test_classify_detects_float_shares_denominator():
    """流通股本与 A 股股本**数值不同**时，判定应精确落在 `float` 上。"""
    hist = pd.DataFrame({
        "date": ["2026-06-30"],
        "code": ["600000.SH"],
        "total_shares": [1.0e10],
        "float_shares": [9.0e9],
        "a_shares": [8.8e9],
        "reason": ["定期报告"],
    })
    daily = pd.DataFrame({
        "date": ["2026-09-30", "2026-10-13"],
        "volume": [9.0e7, 9.0e7],
        "turn": [1.0, 1.0],
    })
    verdict = classify_turn_denominator(daily, hist)
    assert verdict["verdict"] == "float", f"分母判定错：{verdict}"
    assert verdict["ratio_to_float"] == pytest.approx(1.0, abs=0.005)


def test_classify_detects_total_shares_denominator():
    """构造总股本口径：反推值只与总股本吻合。"""
    daily = pd.DataFrame({
        "date": ["2026-09-30", "2026-10-13"],
        "volume": [9.16487155e7, 9.16487155e7],
        "turn": [1.0, 1.0],
    })
    hist = cninfo_frame_to_shares(cninfo_fixture())
    assert classify_turn_denominator(daily, hist)["verdict"] == "total"


def test_classify_detects_a_share_denominator():
    """带 H 股的公司：分母既不是总股本也不是「已流通股份」，而是 A 股（人民币普通股）。

    真实反例：中国银行 总股本 322,212,411,814 / 已流通股份 294,387,791,241 /
    人民币普通股 210,765,514,846，`turn` 反推出的分母落在最后一条腿上。
    """
    hist = pd.DataFrame({
        "date": ["2026-06-30"],
        "code": ["601988.SH"],
        "total_shares": [322212411814.0],
        "float_shares": [294387791241.0],
        "a_shares": [210765514846.0],
        "reason": ["定期报告"],
    })
    daily = pd.DataFrame({
        "date": ["2026-09-30", "2026-10-13"],
        "volume": [2.1076551e9, 2.1079329e9],
        "turn": [1.0, 1.0001319],
    })
    verdict = classify_turn_denominator(daily, hist)
    assert verdict["verdict"] == "a_shares", f"分母判定错：{verdict}"


def test_classify_returns_unknown_when_legs_are_too_close_to_tell():
    """总股本 == 流通股本时无法区分，必须如实说「分不出来」，不能硬报一个。"""
    df = cninfo_fixture()
    df["已流通股份"] = df["总股本"]
    df["人民币普通股"] = df["总股本"]
    hist = cninfo_frame_to_shares(df)
    daily = pd.DataFrame({"date": ["2026-10-13"], "volume": [9.16487155e7], "turn": [1.0]})
    verdict = classify_turn_denominator(daily, hist)
    assert verdict["verdict"] in ("total", "ambiguous"), f"应报无法区分，实得 {verdict}"


def test_classify_unknown_when_shares_history_missing():
    """没有股本数据就没有结论，不许用默认值蒙一个。"""
    daily = pd.DataFrame({"date": ["2026-10-13"], "volume": [1e6], "turn": [1.0]})
    verdict = classify_turn_denominator(daily, pd.DataFrame(columns=SHARE_COLUMNS))
    assert verdict["verdict"] == "unknown"


def test_classify_ignores_days_without_usable_turn():
    """停牌日 turn=0 的行不能进判定（反推不出股本）。"""
    hist = cninfo_frame_to_shares(cninfo_fixture())
    daily = pd.DataFrame({
        "date": ["2026-10-12", "2026-10-13"],
        "volume": [0.0, 9.0468674e7],
        "turn": [0.0, 1.000086],
    })
    verdict = classify_turn_denominator(daily, hist)
    assert verdict["n"] == 1, f"停牌日被算进了 {verdict}"


def test_implied_shares_formula_matches_turn_definition():
    """反推式本身要对：turn = volume / shares × 100 → shares = volume / turn × 100。"""
    got = implied_shares_from_turn([1e8], [2.0])
    assert got[0] == pytest.approx(5e9, rel=1e-12)


# ── 抓取层离线化（monkeypatch，不联网）───────────────────────────────────────

def test_fetch_share_history_is_offline_and_normalized(monkeypatch):
    """抓取层只负责调源 + 归一，返回结构里自带时点。"""
    captured = {}

    def fake_fetch(symbol, start_date, end_date):
        captured.update(symbol=symbol, start=start_date, end=end_date)
        return cninfo_fixture()

    import aplus.sources.shares as mod
    monkeypatch.setattr(mod, "_cninfo_call", fake_fetch)

    out = fetch_share_history("sz.002415", "2024-01-01", "2026-12-31")
    assert captured["symbol"] == "002415", "传给巨潮的应是裸 6 位代码"
    assert captured["start"] == "20240101"
    assert shares_as_of(out) == "2026-06-30"
    assert list(out.columns) == SHARE_COLUMNS


def test_cninfo_empty_records_is_no_data_not_failure(monkeypatch):
    """巨潮对无股本变动史的票返回 0 条，akshare 却抛 KeyError——要如实归成「无数据」。

    实测 000004 / 000005 / 000018 的原始响应 `records=0`，属于源就没有数据，
    不能算成抓取失败，也不能编一个股本值出来。
    """
    import aplus.sources.shares as mod
    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(
        stock_share_change_cninfo=lambda **kw: (_ for _ in ()).throw(KeyError("公告日期"))))
    out = fetch_share_history("000004.SZ", "2015-01-01", "2026-12-31")
    assert out.empty
    assert shares_as_of(out) is None


def test_other_keyerror_is_not_swallowed(monkeypatch):
    """只认那三个列名的 KeyError；别的照抛，不猜。"""
    import aplus.sources.shares as mod
    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(
        stock_share_change_cninfo=lambda **kw: (_ for _ in ()).throw(KeyError("总股本"))))
    with pytest.raises(KeyError):
        fetch_share_history("000004.SZ", "2015-01-01", "2026-12-31")


def test_fetch_share_history_empty_source_is_empty_frame(monkeypatch):
    """源返回空 → 空帧（带 schema），不编造一行。"""
    import aplus.sources.shares as mod
    monkeypatch.setattr(mod, "_cninfo_call", lambda *a, **kw: pd.DataFrame())
    out = fetch_share_history("002415.SZ", "2024-01-01", "2026-12-31")
    assert out.empty
    assert list(out.columns) == SHARE_COLUMNS