"""转债增量同步的**探针不能是单点**——2026-10-15 实测事故的回归台。

## 事故：整库被一只死债冻了 4 个交易日

`sync_bond_daily` 扫完 1087 个转债文件的末根日期后，
只挑 `probe = max(lasts, key=lambda c: lasts[c])` **一只**去问源：
它没前进，就对**全库**返回 `no_new_session`，打印「无新交易日（非交易日）」。

实测（2026-10-15）：

| 债 | 库内末根 | 源端末根 |
|---|---|---|
| `110075.SH`（**被选中的探针**） | 2026-10-09 | **2026-10-09** |
| `128142.SZ` | 2026-10-09 | **2026-10-15** |
| `127115.SZ` | 2026-10-09 | **2026-10-15** |

库里有 **310 只债并列在 2026-10-09**，而 `max()` 在并列时返回**迭代顺序里的第一个**
（字典序最小）——恰好就是 110075.SH，一只在 10-09 之后停止交易的债。

★ **这不是「偶尔踩雷」，是确定性永久冻结**：
  `max()` 的结果是确定的 ⇒ 只要那只债还在库里，探针就永远是它。
  实测转库因此错过 10-10 / 10-13 / 10-14 三个交易日，
  而 `bond_paper.py`（**唯一能裁决 20% 候选的样本外审判台**）在这段时间拿不到任何新数据。

## 失效的形状：**两个状态被压成了一个**

    「市场没开」   —— 真结论，此时全库都不该动
    「这只债死了」 —— 假结论，此时**其余 1086 只该动却没动**

两个状态产生**完全相同**的返回值 `no_new_session` ⇒
下游无从区分，且日志把后者说成前者。
（与本仓 `scripts/cap_exposure.py::control_arm_note` 修过的是同一族：
**三态不许实现成两态。**）

## 因此修法不是「换个探针」，是三件事

1. 探针从 **1 只** 变成法定人数（quorum）；
2. `new_latest` 取**各探针源端日期的最大值**，不是探针自己那只的；
3. **`no_new_session` 必须自带它的法定人数证据** ——
   否定性断言（市场没开）要能被证伪，而不是只能被相信
   （第「守卫不许给自己发信号」层：告警不许只凭自己存在就成立）。

## 本文件只测第 3 条之外的行为，不测网络

全部用假源 + `tmp_path`，不碰网络、不碰真实转债库。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.bond_sync import sync_bond_daily  # noqa: E402


class FakeSource:
    """按 `plan` 回答「这只债源端最晚到哪天」的假源。

    `plan`: {code: 源端末根日期字符串}；未列出的债按 `default` 回答。
    `calls`: 记录被问过哪些探针——用来钉「探针只问了一只」这个回归。
    """

    def __init__(self, plan: dict | None = None, default: str = "2026-10-09",
                 n_bars: int = 3):
        self.plan = dict(plan or {})
        self.default = default
        self.n_bars = n_bars
        self.calls: list[str] = []

    def fetch_daily(self, code, start, end):
        self.calls.append(code)
        last = self.plan.get(code, self.default)
        return pd.DataFrame({
            "date": pd.date_range(end=last, periods=self.n_bars).strftime("%Y-%m-%d"),
            "code": code,
            "open": [100.0] * self.n_bars,
            "high": [101.0] * self.n_bars,
            "low": [99.0] * self.n_bars,
            "close": [100.5] * self.n_bars,
            "volume": [1000.0] * self.n_bars,
        })


def _make_lib(tmp_path: Path, codes: list[str], last: str = "2026-10-09") -> Path:
    """造一个只有 `codes` 的转债库，全部停在 `last`。"""
    daily = tmp_path / "bonds" / "master" / "daily"
    # `exist_ok=True`：一个测试里可能要摆**两个互不相干的场景**（各自的库）
    daily.mkdir(parents=True, exist_ok=True)
    for c in codes:
        pd.DataFrame({
            "date": pd.date_range(end=last, periods=2).strftime("%Y-%m-%d"),
            "code": c,
            "open": [100.0, 100.2], "high": [101.0, 101.2],
            "low": [99.0, 99.2], "close": [100.5, 100.7],
            "volume": [1000.0, 1100.0],
        }).to_parquet(daily / f"{c}.parquet")
    return tmp_path


# 事故里的五只：110075 死在 10-09，其余前进到 10-15。
# ⚠️ 110075 必须是**字典序最小**的，否则钉不住「max() 挑并列的第一个」这条。
_TIED = ["110075.SH", "110076.SH", "110077.SH", "110081.SH", "110084.SH",
         "110085.SH", "110086.SH", "110087.SH"]
_DEAD = "110075.SH"
# 空帧探针：不推进，也不带日期（腾讯对摘牌债就是返这种帧）
_DEAD_OTHERS = ["110077.SH", "110081.SH"]


def test_a_dead_probe_does_not_freeze_the_whole_library(tmp_path):
    """🚨 本次事故的回归：死的那只排在探针第一位，其余都在前进。

    修复前：全库返回 `no_new_session`，四个交易日的转债数据凭空消失，
    `bond_paper.py` 断供——而日志写的是「无新交易日」。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({c: "2026-10-15" for c in _TIED if c != _DEAD}, default="2026-10-09")

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["status"] == "ok", (
        f"一只死债被当成『市场没开』，整库冻结：{r}")
    assert r["new_latest"] == "2026-10-15", r
    # 死债不该被当成新数据写回去
    assert src.plan.get(_DEAD) is None


def test_the_probe_asks_more_than_one_bond(tmp_path):
    """钉住「探针不是单点」——单点是最原始的实现，必须不可能复活。

    事故里 `_DEAD` 被问到 1 次、全库 0 次推进；
    只断言 status 的话，把探针改回 `max(...)` 再挑一只别的死债就能躲过。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({c: "2026-10-15" for c in _TIED if c != _DEAD})

    sync_bond_daily(tmp_path, sleep=0, source=src)

    assert len(src.calls) > 1, (
        f"整轮只问了 {len(src.calls)} 只债（{src.calls}）⇒ 探针退回单点了")
    assert _DEAD in src.calls, (
        "死债没有被探到 ⇒ 测不到『探针恰好是死的那只』这个事故形状")


def test_new_latest_is_the_max_across_probes_not_the_first_one(tmp_path):
    """各探针源端日期不一致时，最新的那只说了算。

    只问一只的实现里，`new_latest` 就是**那一只**的日期；
    而库里落后于它的债会被判成「已经是最新新新」⇒ 永远补不上。
    """
    _make_lib(tmp_path, _TIED)
    plan = {"110076.SH": "2026-10-12", "110077.SH": "2026-10-15",
            "110081.SH": "2026-10-13", "110084.SH": "2026-10-15",
            "110085.SH": "2026-10-14", "110086.SH": "2026-10-15",
            "110087.SH": "2026-10-15"}
    src = FakeSource(plan, default="2026-10-09")

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["new_latest"] == "2026-10-15", (
        f"new_latest 取了某一只探针的日期而不是全体最大：{r}")
    assert r.get("stale", 0) == len(_TIED), (
        f"落后于最新交易日的债本该全部重取，实际只取 {r.get('stale')} 只：{r}")


def test_a_genuinely_closed_market_still_reports_no_new_session(tmp_path):
    """🚨 反向护栏：修法**不许**把 `no_new_session` 变成不可达状态。

    这是本仓第 112 层通则的另一半——过宽的修复和原 bug 一样坏。
    真·非交易日（源端全体没前进）时必须照旧返回 `no_new_session`，
    否则等于把「跳过」永远打开，成了另一个方向的静默失败。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({}, default="2026-10-09")          # 一只都没前进

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["status"] == "no_new_session", r


def test_the_no_new_session_claim_carries_its_own_evidence(tmp_path):
    """🚨 否定性断言必须自带证伪材料（本文件存在的主要理由）。

    「市场没开」是一句**否定**断言，而事故里它是被**一只债的沉默**撑起来的。
    修法要求：返回的字典必须带上
      ① 问了哪几只（`probe_codes`）
      ② 有几只真的查过（`probe_fetched`）
      ③ 它们各自源端到哪天（`probe_source_dates`）
    —— 有了这三样，「没开」才**可被独立重算**，而不是只能被相信。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({}, default="2026-10-09")

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["status"] == "no_new_session"
    for key in ("probe_codes", "probe_fetched", "probe_source_dates"):
        assert key in r, (
            f"`no_new_session` 没有携带 `{key}` ⇒ 这句否定断言无法被证伪：{r}")
    assert r["probe_fetched"] > 0, (
        f"声称『没开新交易日』却一只债都没真正查过：{r}")
    # 而且这些证据必须与真实探到的相符，不是照抄常量
    assert set(r["probe_codes"]) <= set(_TIED), r["probe_codes"]
    assert r["probe_fetched"] == len(src.calls), (
        f"报称查了 {r['probe_fetched']} 只，源实际被问了 {len(src.calls)} 次：{r}")


def test_the_ok_branch_also_reports_the_quorum(tmp_path):
    """`ok` 分支同样要报法定人数——否则「推进了」也没有可核对的证据。

    事故的对偶：一句话说得自信，但没人能拿它重算。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({c: "2026-10-15" for c in _TIED if c != _DEAD})

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["status"] == "ok", r
    assert r.get("probe_advanced", 0) >= 1, (
        f"`ok` 分支没报『有几只探针真的前进了』⇒ 前进与否同样不可核对：{r}")


def test_probe_advanced_counts_the_ones_that_moved_not_the_ones_that_answered(tmp_path):
    """🚨 `probe_advanced` 必须数**前进的**，不是**答了话的**。

    这条是变异 BS3 的直接对手：`advanced = sorted(src_dates)` 看起来很像，
    但它数的是「问到结果的只数」。**在「全都答了话」的夹具下两者逐位相同**
    （都是 5）⇒ 只断言 `>= 1` 的守卫对它完全无感。

    ⇒ 判据必须挑一个**让两者必然不等**的夹具：这里死的那只排在探针第一位，
      于是答话的 5 只、前进的 4 只。
    """
    _make_lib(tmp_path, _TIED)
    src = FakeSource({c: "2026-10-15" for c in _TIED if c != _DEAD})

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    probes = r["probe_codes"]
    answered = r["probe_fetched"]
    expected = sum(1 for c in probes if src.plan.get(c, src.default) > r["latest"])
    assert answered > expected, (
        f"这个夹具本该让『答话数』与『前进数』不同，实测 {answered} == {expected}"
        f" ⇒ 判据无判别力：{r}")
    assert r["probe_advanced"] == expected, (
        f"报称 {r['probe_advanced']} 只探针前进，实际 {expected} 只前进"
        f"（答话 {answered} 只）⇒ 这个字段数的是答话的还是前进的？{r}")


def test_probes_returning_empty_frames_are_not_counted_as_advancing(tmp_path):
    """🚨 空帧的探针既不算「前进」，也不能把 `ok` 分支拖进 `probe_empty`。

    覆盖两件事（变异 BS7 / BS8 各打一件）：
      ① 返回空帧的探针**不进入** `probe_source_dates`
         —— 否则 `new_latest = max(src_dates.values())` 会被一只空债的
            陈旧日期拉回去，于是刚补上的日子又被判成「已经是最新的」；
      ② 全部探针都空 ⇒ `probe_empty`，且**必须携带证据**
         —— 「问不出结果」与「市场没开」是两件事，事故就栽在这个区分上。
    """
    class EmptySome(FakeSource):
        def fetch_daily(self, code, start, end):
            if code in _DEAD_OTHERS:
                return pd.DataFrame(columns=["date", "code", "open", "high",
                                             "low", "close", "volume"])
            return super().fetch_daily(code, start, end)

    # 夹具 A：一只空 + 一只死 + 其余前进 ⇒ 必须仍走 `ok`，且空的那只不在日期表里
    _make_lib(tmp_path, _TIED)
    src = EmptySome({c: "2026-10-15" for c in _TIED if c != _DEAD})
    r = sync_bond_daily(tmp_path, sleep=0, source=src)
    assert r["status"] == "ok", f"一只空帧探针就否掉全局：{r}"
    assert _DEAD_OTHERS[0] not in r["probe_source_dates"], (
        f"空帧探针被写进了源端日期表：{r['probe_source_dates']}")

    # 夹具 B：全空 ⇒ `probe_empty`（不是 `no_new_session`！），且带证据
    _make_lib(tmp_path, _TIED, last="2026-10-09")
    class AllEmpty(FakeSource):
        def fetch_daily(self, code, start, end):
            return pd.DataFrame(columns=["date", "code", "open", "high",
                                         "low", "close", "volume"])
    AllEmpty()          # plan 空 ⇒ 全部落到 default
    r2 = sync_bond_daily(tmp_path, sleep=0, source=AllEmpty())
    assert r2["status"] == "probe_empty", (
        f"全部探针问不出结果却报了别的状态（『市场没开』是另一件事）：{r2}")
    for key in ("probe_codes", "probe_fetched", "probe_source_dates"):
        assert key in r2, f"`probe_empty` 没携带 `{key}` ⇒ 同样不可核对：{r2}"


def test_a_probe_raising_does_not_freeze_everything_either(tmp_path):
    """探针抛异常时，其余探针的结论仍须有效。

    事故的第二个面：一只债的网络故障和一只债的死亡，
    在单探针设计里都表现为「全库没前进」。
    """
    class OneDead( FakeSource ):
        def fetch_daily(self, code, start, end):
            if code == _DEAD:
                raise RuntimeError("网络故障")
            return super().fetch_daily(code, start, end)

    _make_lib(tmp_path, _TIED)
    src = OneDead({c: "2026-10-15" for c in _TIED if c != _DEAD})

    r = sync_bond_daily(tmp_path, sleep=0, source=src)

    assert r["status"] == "ok", (
        f"一只探针抛异常就让整库停更：{r}")
    assert r["new_latest"] == "2026-10-15", r
