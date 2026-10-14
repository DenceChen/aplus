"""全市场日期轴 = 全体并集，不许拿一只票代表全体。

## 这条守卫在钉什么

`aplus/panel.py::build_panel` 原来用
`ref = max(daily_map.values(), key=len)` 取「行数最多的那一只」的日期
当**全市场**日期轴。

在 1883 行全体齐平的数据集上，它与真实并集**逐位相同** ——
所以它能活过无数轮测试。不是碰巧对，是**在「全体等长」这个不变量下必然对**。

2026-10-15 的数据修复打破了这个不变量：
`000043.SZ` 从 221 行补到 6127 行（1994-09-28 起），
而它的**整段历史都在判决窗口 `[2021-01-01, 2026-10-12)` 之前**
⇒ 裁剪剩 **0 天** ⇒ 面板空 ⇒ 上层一路飘到「🚨 没有任何判决年份」，
**看起来像没数据，而数据完好无损**。

## 三条纪律

1. **不加载真实面板** —— 全部用几十行的合成数据。
2. **断言锚在行为上** —— 复刻真实失效（一只票的历史离群），
   而不是断言源码里没有某个字符串（第 13 种形状）。
3. **变异后才算数** —— 把 `window_dates` 退回单票版本，守卫必须变红。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.panel import market_date_axis, window_dates


def _daily(dates, close=None, code="X"):
    n = len(dates)
    close = np.asarray(close if close is not None
                       else 10.0 * np.exp(np.cumsum(np.full(n, 0.001))), dtype=float)
    return pd.DataFrame({
        "date": list(dates), "code": code,
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1e6), "amount": np.full(n, 1e7),
        "tradestatus": np.ones(n), "isST": np.zeros(n),
        "turn": np.full(n, 0.01), "pctChg": np.zeros(n),
    })


# ─────────────────── ① 今天的失效，逐位复刻 ───────────────────
def test_a_stock_with_pre_window_history_cannot_empty_the_window():
    """★ 2026-10-15 的真实失效：`000043.SZ` 历史长到 1994 年，
    整段落在判决窗口之前。

    单票版会选中它 ⇒ 窗口内 0 天 ⇒ 面板静默变空 ⇒ 报告变成
    「🚨 没有任何判决年份」，而数据完好无损。
    """
    win = pd.bdate_range("2021-01-04", periods=200).strftime("%Y-%m-%d")
    old = pd.bdate_range("1994-09-28", periods=1000).strftime("%Y-%m-%d")

    dm = {"LONG": _daily(old, code="LONG")}          # 离群：1000 行，全在窗口外
    for i in range(5):
        dm[f"S{i}"] = _daily(win, code=f"S{i}")       # 正常：200 行

    got = window_dates(dm, "2021-01-01", "2021-12-31")
    assert got == sorted(set(win)), "★ 并集轴必须完全由窗口内的正常票决定"
    assert len(got) == 200

    # 正控：换成单票代表全体（今天之前的实现）必然归零
    ref = max(dm.values(), key=len)
    legacy = [d for d in sorted(ref["date"].unique()) if "2021-01-01" <= d < "2021-12-31"]
    assert legacy == [], "★ 正控：旧实现在这个构造上确实是 0 天"


def test_a_day_the_longest_stock_missed_is_still_a_trading_day():
    """★ `scripts/audit_calendar/ref_risk.py` 当初担心的**较轻形态**：
    被选中那只自身停牌 ⇒ 那天从整个回测里消失。

    该脚本已把这个问题识别、量化、写成审计脚本，然后**从未被修** ——
    直到数据变更把它的**极端版本**引爆。两条形态都要堵。

    ⚠️ 构造要小心：**被停牌的那只必须仍然是最长的那只**。
      写成「缺一天的那只更短」的话，`max(key=len)` 选中的恰好是完整的
      另一只，正控就没复现原故障 —— 我第一版就是这么写错的。
    真实形态是：它横跨**更长**的区间（所以行数最多），
    中间停牌一天，而那天**别的票还在交易**。
    """
    full = list(pd.bdate_range("2021-01-04", periods=12).strftime("%Y-%m-%d"))
    a = [d for d in full if d != full[5]]            # 停牌一天 ⇒ 11 行，横跨 12 天
    b = full[2:]                                     # 后 10 天上市 ⇒ 10 行，无缺口

    dm = {"A": _daily(a, code="A"), "B": _daily(b, code="B")}
    got = window_dates(dm, "2021-01-01", "2021-12-31")
    assert got == sorted(full), "★ 只要有一只票在交易，那天就是交易日"

    ref = max(dm.values(), key=len)                  # 正控：旧实现
    assert ref["code"].iloc[0] == "A", "★ 正控：必须选中停牌那只 A"
    assert full[5] not in set(ref["date"]), "★ 正控：旧实现在 A 的轴上丢了这一天"
    legacy = [d for d in sorted(ref["date"].unique()) if "2021-01-01" <= d < "2021-12-31"]
    assert len(legacy) == 11 and len(got) == 12


def test_market_date_axis_is_the_union_not_the_longest_stock():
    dm = {"A": _daily(pd.bdate_range("2021-01-04", periods=3).strftime("%Y-%m-%d")),
          "B": _daily(pd.bdate_range("2020-01-02", periods=400).strftime("%Y-%m-%d"))}
    axis = market_date_axis(dm)
    assert axis == sorted(set(dm["A"]["date"]) | set(dm["B"]["date"]))
    assert axis[0] == dm["B"]["date"].min()
    assert len(axis) == 400          # 400 天的 B 完全覆盖了 A 的 3 天


# ─────────────────── ② 空轴必须当场炸，且诊断必须有用 ───────────────────
def test_a_window_outside_the_data_raises_rather_than_returning_nothing():
    """★ 返回 `[]` 会让实盘横截面静默变成 0 行（2026-10-11 事故的形态）。
    空面板必须当场炸，不能让问题飘到几百行之外再冒出来。"""
    dm = {"A": _daily(pd.bdate_range("2021-01-04", periods=5).strftime("%Y-%m-%d"))}
    with pytest.raises(ValueError):
        window_dates(dm, "2019-01-01", "2019-12-31")


def test_the_error_names_the_real_date_axis_so_it_can_be_actioned():
    """★ 报错文本本身是产物：只说「空」的话，人还得回头自己查是哪一段时间有数据。

    ⇒ 断言它**同时**含真实轴的首末与长度 —— 这三个是外部锚，
    不是「消息里有 ValueError」这种自我满足的断言。"""
    dm = {"A": _daily(pd.bdate_range("2021-01-04", periods=5).strftime("%Y-%m-%d"))}
    with pytest.raises(ValueError) as e:
        window_dates(dm, "2019-01-01", "2019-12-31")
    msg = str(e.value)
    assert "2021-01-04" in msg and "2021-01-08" in msg, msg
    assert "5 天" in msg, msg


def test_an_empty_daily_map_also_raises_with_a_readable_axis():
    with pytest.raises(ValueError) as e:
        window_dates({}, "2021-01-01", "2021-12-31")
    assert "无" in str(e.value)


# ─────────────────── ③ 实盘的 as_of：不能由一只票决定 ───────────────────
# ─────────────────── ④ 接线：build_panel 必须真的调它 ───────────────────
def test_build_panel_really_runs_on_the_union_axis():
    """★ **本文件最重要的一条**，而且它是补上去的，不是一开始就想到的。

    变异验证时把 `build_panel` 里的 `dates = window_dates(...)` 退回
    单票版，**另外 6 条守卫全绿** —— 因为它们要么直接调 `window_dates`，
    要么走 `snapshot`。**函数本身被测得很好，接线却没人钉**：
    `build_panel` 可以完全不调它而门禁照样全绿。

    这与「monkeypatch 的是 A、被测的读的是 B」同族：
    **被测对象与接线之间没有通路。**
    ⇒ 必须有一条走真实 `build_panel` 的守卫，
    而且断言的是 `panel.dates` 本身，不是 `window_dates` 的返回值。

    它同时是 2026-10-15 那次事故的**逐位复刻**：
    单票版在这个构造上给出 `panel.dates == []`，而 `build_panel`
    **不抛异常** —— 空面板一路飘到「🚨 没有任何判决年份」。
    """
    from aplus.panel import build_panel

    win = pd.bdate_range("2021-01-04", periods=300).strftime("%Y-%m-%d")
    old = pd.bdate_range("1994-09-28", periods=1500).strftime("%Y-%m-%d")

    dm = {"LONG": _daily(old, code="LONG")}
    for i in range(4):
        dm[f"S{i}"] = _daily(win, code=f"S{i}")

    panel = build_panel(None, "2021-01-01", "2021-12-31",
                        daily_map=dm, factors_map={})
    # 期望按**半开区间 [start, end)** 构造 —— end 之后的交易日本就不该进来。
    # （第一版这里写成了 `sorted(set(win))` 全量，拿到 259 vs 300，
    #   一度以为接线没生效；实际是断言自己不懂开区间。）
    expect = sorted(d for d in win if "2021-01-01" <= d < "2021-12-31")
    assert 0 < len(expect) < len(win), "★ 正控前提：窗口确实切掉了一部分"
    assert list(panel.dates) == expect, \
        f"★ build_panel 必须建立在并集轴上，实得 {len(panel.dates)} 天，期望 {len(expect)}"

    # 正控：旧实现在同一构造上给 0 天，且**不抛异常**（那正是静默的形态）
    ref = max(dm.values(), key=len)
    legacy = [d for d in sorted(ref["date"].unique()) if "2021-01-01" <= d < "2021-12-31"]
    assert legacy == []


def test_the_live_as_of_is_the_last_day_any_stock_traded():
    """★ 同一个坑的第二处，后果更重：`build_cross_section` 的
    `as_of = all_dates[-1]` 也取自那只离群票 ⇒ 实盘会拿 **7 年前**的截面出单，
    而 `panel.dates` 非空、`target` 找得到、`cross_section` 正常返回 ——
    **全程不报错**。

    这里用一个假的 store 走真实路径，不用源码子串断言。
    """
    import aplus.snapshot as snap

    win = pd.bdate_range("2024-01-02", periods=300).strftime("%Y-%m-%d")
    old = pd.bdate_range("2005-01-03", periods=2000).strftime("%Y-%m-%d")

    files: dict[str, pd.DataFrame] = {
        "600000.SH": _daily(old, code="600000.SH"),   # 离群：2000 行，全在 2010 年前
        "000001.SZ": _daily(win, code="000001.SZ"),
        "000002.SZ": _daily(win, code="000002.SZ"),
        "000003.SZ": _daily(win, code="000003.SZ"),
    }

    class _Store:
        root = None                                    # 由下面注入

        def read_daily(self, code):
            return files[code]

    store = _Store()
    store.root = _FakeRoot(files)

    # ★ 锚是**名单里有谁**，不是 `listed_days` ——
    #   离群票自己有 2000 行历史，`listed_days` 会被它顶到 2000，
    #   两个 as_of 下**完全相同**，区分度为零。
    #   而「2024 年才上市的三只票在不在名单里」在两个 as_of 下截然不同。
    got = snap.build_cross_section(store)
    legacy_as_of = files["600000.SH"]["date"].max()          # 旧实现会给的 as_of
    legacy = snap.build_cross_section(store, as_of=legacy_as_of)

    fresh = {"000001.SZ", "000002.SZ", "000003.SZ"}
    assert not got.empty, "★ 实盘横截面必须非空"
    assert market_date_axis(files)[-1] == win[-1], "★ 正控前提：市场末根在 2025 年"
    assert legacy_as_of < win[-1], "★ 正控前提：离群票的末根远早于市场末根"

    assert fresh <= set(got["code"]), f"★ 实盘名单必须含 2024 年才上市的三只，实得 {set(got['code'])}"
    # ★ 正控：若 as_of 取离群票的末根，这三只**当时还不存在**，
    #   而返回的横截面**照样非空、照样长得完全正常** —— 那正是静默失效的形态。
    assert not (fresh & set(legacy["code"])), \
        f"★ 正控：as_of={legacy_as_of} 时这三只不该存在，却出现了：{set(legacy['code'])}"
    assert not legacy.empty, "★ 正控前提：旧 as_of 也返回非空帧 —— 所以空/非空分不出对错"


class _FakeRoot:
    """只提供 `build_cross_section` 真正用到的那几个接口。

    ★ 必须区分 `master/daily` 与 `master/factor` 两条路径 ——
      真实库里 factor 目录是空的（因子由 daily 现算），
      守卫若把两者混同就会走上一条生产上不存在的路径。
    """

    def __init__(self, files, parts=()):
        self._files = files
        self._parts = parts

    def __truediv__(self, other):
        return _FakeRoot(self._files, self._parts + (str(other),))

    def glob(self, pattern):
        if self._parts[-2:] == ("master", "factor"):
            return []
        return sorted((_P(f"{k}.parquet") for k in self._files),
                      key=lambda p: p.name)


class _P:
    def __init__(self, name):
        self.name = name
        self.stem = name[:-len(".parquet")]

    def __lt__(self, other):
        return self.name < other.name

    def __str__(self):
        return self.name
