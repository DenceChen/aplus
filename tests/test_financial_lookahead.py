"""财务因子**不得前视**：对齐日期必须是 `NOTICE_DATE`，绝不是 `UPDATE_DATE`。

## 这条测试在防什么

A 股财报有两个日期：

| 字段 | 含义 | 在回测里的合法性 |
|---|---|---|
| `NOTICE_DATE` | **首次披露日**——这天市场才第一次看到这份财报 | ✅ 唯一可用的对齐基准 |
| `UPDATE_DATE` | **最近一次修正日**——这天财报被改过（可能是今天） | ⛔ 用它对齐 = 前视偏差 |

用 `UPDATE_DATE` 对齐的形状，和 `tests/test_ivol_no_leak.py` 记录的那次事故
**完全同构**：不崩、不报 NaN、横截面每一列都正常，只是**每个历史日的因子值
都掺了「事后才知道的修正结果」**。区别只在于 ivol 那次是代码 bug，
这次是**数据源属性**——源里根本没有原始披露值，只有后修值。

`docs/PLATFORM.md:18` 已经把规矩写死：
「不可得（如复权基准、**后修财务**、当前搜索结果）→ 禁止入回测」。
本文件把那行散文变成**会红的断言**。

## 为什么这里必须是「协议」而不是「当前实现」

`docs/factor_spec_cn.md` §1.5.1 的实测结论是：这个源做不到真 PIT，
「在 A/B 决议之前，不写 FAR / BM / ROE 的任何代码」。
所以本文件**不假设**某个已存在的财务因子实现——它钉的是**协议**：

1. 数据层必须**同时**保留两个日期，且解析成 datetime（能区分 ≠ 只有一个）；
2. 对齐入口 `visible_as_of` **只**按 `NOTICE_DATE` 过滤；
3. 任何因子路径上的模块出现 `UPDATE_DATE` 字面量，**当场失败**。

第 3 条是元测试：`tests/test_factor_gate.py` 那种闸门若自己失效，
就是「天天绿灯的假闸门」。所以 `test_guard_scanner_is_not_vacuous`
用一棵**故意植入**违规的临时树证明扫描器真的会响——否则「没扫出来」
与「压根没扫」不可区分。

## 口径声明（诚实标注）

本文件全部离线（纯 DataFrame），不联网。真实数据上的比例见
`scripts/diag_financial_lookahead.py`，那里才联网。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from aplus.financial import (
    ALIGN_DATE_COL,
    NOTICE_DATE_COL,
    REPORT_DATE_COL,
    UPDATE_DATE_COL,
    is_never_revised,
    normalize_financial,
    revision_lag_days,
    visible_as_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 因子计算/消费路径上的模块。这里的任何一个出现 `UPDATE_DATE`
#: 字面量，都意味着有人把修正日接进了因子流水线——立即失败。
FACTOR_PATH_MODULES = (
    "aplus/factor.py",
    "aplus/panel.py",
    "aplus/snapshot.py",
    "aplus/predict.py",
    "aplus/history.py",
    "aplus/backtest.py",
    "aplus/factor_ic.py",
    "aplus/portfolio.py",
    "aplus/rebalance.py",
    "aplus/forward.py",
    "aplus/store.py",
)

#: 合法的例外：财务数据层**自己**必须知道 UPDATE_DATE 存在，
#: 否则算不出修订滞后、也做不了 §1.5.1 的诊断。
#: 它对齐用的仍然是 NOTICE_DATE —— 由 `visible_as_of` 的测试钉死。
AUDITED_MODULES = ("aplus/financial.py",)


def _raw() -> pd.DataFrame:
    """三条记录：一条从未修订、一条被大幅修订、一条只差一天。"""
    return pd.DataFrame({
        "SECURITY_CODE": ["600519", "600519", "000001"],
        "REPORT_DATE": ["2019-12-31", "2020-12-31", "2021-12-31"],
        "REPORT_TYPE": ["年报", "年报", "年报"],
        "NOTICE_DATE": ["2020-04-28", "2021-04-27", "2022-03-16"],
        "UPDATE_DATE": ["2020-04-28", "2022-06-30", "2022-03-17"],
        "ROEJQ": [33.55, 29.86, 12.4],
    })


# ── 数据层：必须能区分两个日期 ─────────────────────────────────────────────

def test_normalize_keeps_notice_and_update_as_separate_columns():
    """两个日期必须**都在**，不能只留一个。

    只留 `NOTICE_DATE` 会让诊断算不出滞后；只留 `UPDATE_DATE` 是前视偏差。
    丢掉任何一个，数据层就失去了「区分」的能力——这是本文件所有其它断言的前提。
    """
    out = normalize_financial(_raw())
    assert NOTICE_DATE_COL in out.columns
    assert UPDATE_DATE_COL in out.columns
    assert REPORT_DATE_COL in out.columns


def test_normalize_parses_both_dates_to_datetime():
    """akshare 返回的是 `'2020-04-28 00:00:00'` 这样的**字符串**。

    不解析成 datetime，比较就会退化成字符串比较——而字符串比较下
    `'2020-04-28' > '2020-4-28'` 之类的结果不可信。必须实测 dtype。
    """
    out = normalize_financial(_raw())
    for col in (REPORT_DATE_COL, NOTICE_DATE_COL, UPDATE_DATE_COL):
        assert out[col].dtype.kind == "M", f"{col} 不是 datetime: {out[col].dtype}"


def test_normalize_drops_rows_with_unusable_dates():
    """日期缺失/不可解析的行不能进入对齐——静默当成 1970 会制造 50 年前视。"""
    raw = _raw()
    raw.loc[0, NOTICE_DATE_COL] = None
    raw.loc[1, NOTICE_DATE_COL] = "not-a-date"
    out = normalize_financial(raw)
    assert len(out) == 1, f"应只保留 1 行干净记录，实得 {len(out)}"


def test_normalize_rejects_frame_without_notice_date():
    """缺 `NOTICE_DATE` 的输入必须报错，不能悄悄降级成别的列。

    「降级」在这里意味着用修正日冒充披露日——那正是本文件要防的事。
    """
    with pytest.raises(ValueError):
        normalize_financial(_raw().drop(columns=[NOTICE_DATE_COL]))


# ── 协议：对齐只认 NOTICE_DATE（不可让步） ─────────────────────────────────

def test_align_column_is_notice_date():
    """**不可让步的协议断言**。

    `ALIGN_DATE_COL` 是因子对齐唯一允许使用的日期列。
    它必须字面等于 `NOTICE_DATE`——一旦有人改成 `UPDATE_DATE` 或加了个
    「自动挑更保守的那个」的逻辑，这行就红。
    """
    assert ALIGN_DATE_COL == "NOTICE_DATE"


def test_visible_as_of_reveals_on_notice_date_not_update_date():
    """核心正例：2020 年报的 `UPDATE_DATE` 是 2022-06-30，
    但它 **2021-04-27 就已公开**。`as_of=2021-06-01` 必须能看到它。"""
    out = visible_as_of(normalize_financial(_raw()), "2021-06-01")
    assert "2020-12-31" in set(out[REPORT_DATE_COL].dt.strftime("%Y-%m-%d")), (
        "2020 年报在 NOTICE_DATE 之后就该可见；看不到 = 对齐基准用错了"
    )


def test_visible_as_of_is_blind_to_update_date():
    """把 `UPDATE_DATE` 改成 2099 年，**对齐决策与因子取值**必须逐行不变。

    这是泄露测试的标准形状（`test_ivol_no_leak.py` 同款）：
    改未来值，看当下变不变。变了就说明修正日接进了流水线。

    比较时**排除 `UPDATE_DATE` 列本身**——`visible_as_of` 刻意把它作为
    诊断列原样透出（诊断代码要靠它算滞后）。豁免的必须是「修正日对
    **可见性**无影响」，不是「修正日不存在」。
    """
    base = normalize_financial(_raw())
    poisoned = base.copy()
    poisoned[UPDATE_DATE_COL] = pd.Timestamp("2099-12-31")

    def _aligned(df):
        out = visible_as_of(df, "2021-06-01").reset_index(drop=True)
        return out.drop(columns=[UPDATE_DATE_COL])

    pd.testing.assert_frame_equal(_aligned(base), _aligned(poisoned))


def test_visible_as_of_passes_update_date_through_for_diagnostics_only():
    """透出的 `UPDATE_DATE` 必须是**原值**——诊断需要它，且不得被改写。

    与上一条配对：既不能拿它对齐，也不能把它抹掉。
    """
    out = visible_as_of(normalize_financial(_raw()), "2021-06-01")
    assert UPDATE_DATE_COL in out.columns
    assert pd.Timestamp("2022-06-30") in set(out[UPDATE_DATE_COL])


def test_visible_as_of_hides_report_until_notice_date():
    """反例：`as_of` 落在披露日**之前**时，未来才披露的报告不可见。

    这是负向检验。若这条也红（例如实现成「全都返回」），说明测试数据本身失效。
    """
    out = visible_as_of(normalize_financial(_raw()), "2020-01-01")
    assert len(out) == 0


def test_visible_as_of_does_not_leak_undisclosed_peer():
    """2021 年报（披露日 2022-03-16）在 as_of=2021-06-01 时不可见。"""
    out = visible_as_of(normalize_financial(_raw()), "2021-06-01")
    assert "2021-12-31" not in set(out[REPORT_DATE_COL].dt.strftime("%Y-%m-%d"))


# ── 诊断量：修订滞后 ─────────────────────────────────────────────────────

def test_revision_lag_days_computes_positive_for_revised():
    """2019 年报从未修订 → 滞后 0 天；2020 年报滞后 429 天。"""
    norm = normalize_financial(_raw())
    lag = revision_lag_days(norm)
    year = norm[REPORT_DATE_COL].dt.year
    assert lag.loc[year == 2019].iloc[0] == 0
    revised = lag.loc[year == 2020].iloc[0]
    assert revised == 429, f"滞后天数算错: {revised}"


def test_is_never_revised_marks_untouched_reports_only():
    """`UPDATE_DATE == NOTICE_DATE` 的白名单——§1.5.1 出路 A 的判据。

    只有这批记录，「今天服务的值」才等于「首次披露值」，PIT 由构造成立。
    """
    norm = normalize_financial(_raw())
    flag = is_never_revised(norm)
    year = norm[REPORT_DATE_COL].dt.year
    # 用 bool() 显式落回 Python bool：numpy.bool_ 与 True 是两个不同对象，
    # 直接写 `is True` 会因为实现细节（dtype）而不是语义红/绿。
    assert bool(flag.loc[year == 2019].iloc[0]) is True
    assert bool(flag.loc[year == 2020].iloc[0]) is False


def test_update_before_notice_is_flagged_not_silently_accepted():
    """`UPDATE_DATE < NOTICE_DATE` 在现实里不存在（倒填）。
    若真出现，必须能被看见——静默取 abs 会把方向性 bug 藏起来。"""
    raw = _raw()
    raw.loc[0, UPDATE_DATE_COL] = "2019-01-01"
    lag = revision_lag_days(normalize_financial(raw))
    assert lag.min() < 0, "倒填的负滞后没有被暴露出来"


# ── 元测试：闸门本身不能是假闸门 ─────────────────────────────────────────

def _scan(files: dict) -> list:
    """返回 `(路径, 行号, UPDATE_DATE)` 三元组列表；忽略注释与 docstring 行。"""
    hits = []
    pat = re.compile(r"\bUPDATE_DATE\b")
    for path, text in files.items():
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if pat.search(line):
                hits.append((path, i, stripped))
    return hits


def _read_repo_module(rel: str) -> str:
    p = REPO_ROOT / rel
    assert p.exists(), f"因子路径模块不存在: {rel}（改名了就同步更新本列表）"
    return p.read_text(encoding="utf-8")


def test_no_update_date_in_factor_path():
    """**闸门**：因子流水线的任何模块都不得出现 `UPDATE_DATE` 字面量。

    命中即意味着修正日被接进了回测路径 —— 这会让所有含财务因子的结论作废。
    """
    files = {rel: _read_repo_module(rel) for rel in FACTOR_PATH_MODULES}
    hits = _scan(files)
    assert not hits, (
        "因子路径上出现 UPDATE_DATE（前视偏差）：\n"
        + "\n".join(f"  {p}:{i}: {s}" for p, i, s in hits)
    )


def test_guard_scanner_is_not_vacuous():
    """证明上一个测试**真的会响**。

    没有这一条，「没扫出来」和「压根没扫」不可区分——仓库历史上已经吃过
    「测试本身失效」的亏（见 `test_ivol_no_leak.py` 顶部的三起事故）。
    植入两棵假树：违规的必须被抓，干净的必须放过。
    """
    dirty = {"aplus/factor.py": "X = df['UPDATE_DATE']\n"}
    assert _scan(dirty), "扫描器对植入的 UPDATE_DATE 无反应 → 闸门是假闸门"

    clean = {"aplus/factor.py": "X = df['NOTICE_DATE']\n"}
    assert not _scan(clean), "扫描器把 NOTICE_DATE 误报成 UPDATE_DATE → 闸门恒红"

    commented = {"aplus/factor.py": "# 曾经用过 UPDATE_DATE，2026-10-14 移除\n"}
    assert not _scan(commented), "扫描器读注释 → 闸门会被历史说明误伤"


def test_audited_module_defines_update_date_but_aligns_on_notice():
    """财务数据层**必须**知道 `UPDATE_DATE`（否则算不出诊断），
    但它的对齐入口只认 `NOTICE_DATE`。

    两者同时成立才合法：既不装作没这回事，也不拿它对齐。
    """
    src = _read_repo_module("aplus/financial.py")
    assert UPDATE_DATE_COL in src, "财务层不认识 UPDATE_DATE → 修订滞后算不出来"
    assert re.search(r'ALIGN_DATE_COL\s*=\s*"NOTICE_DATE"', src), (
        "财务层的对齐列不是字面量 NOTICE_DATE"
    )


def test_audited_modules_are_not_in_factor_path_list():
    """结构性防呆：`aplus/financial.py` 不得同时出现在两张名单里。

    否则「豁免」会把闸门自己豁免掉——这是豁免机制最常见的失效方式。
    """
    overlap = set(AUDITED_MODULES) & set(FACTOR_PATH_MODULES)
    assert not overlap, f"模块同时被豁免和被扫描：{overlap}"
