"""轮次 B（基本面族臂 R 机理全集 + 族 A 判决）的守卫。

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_ROUNDB_2026-10-21.md`
       + `docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md`

## 这个文件守什么

| 守卫 | 守的是哪一层 |
|---|---|
| `n_A` **从注册表长度算**，不许手打 | 判决口径（预注册 §5.2 第 191 行逐字） |
| **MDE 闸必须第一个判** | 判决链顺序（委托方 §2 D-3） |
| 四态标签互斥且取自 §5.4 | 判决语义 |
| 「测不到」不许写成「没有」 | 措辞（§5.5 / §7.3） |
| 截尾 IC 的 t 是**它自己**的 t | 机理①的判据（B1 的核心） |
| D2 阈值是**分数**不是百分数 | 量纲（本轮实测踩到的那一个） |
| F1–F5 的定义逐字照抄预注册 | 因子定义 |
| 裸 6 位码 ↔ 带后缀码的 join | 数据连接（本轮实测踩到的那一个） |
| 年报序列与 `aplus` 的口径**逐位相等** | 口径漂移 |
| 第 6 批注册**冻结**且 K 写死 {21,63} | 冻结机制 |

## 三条纪律（照抄仓内既有惯例）

1. **不许用子串断言去查「有没有手打常量」**（本仓第 13 条）：
   脚本 docstring 里常逐字引用字面量 ⇒ 文本匹配会被注释满足
   ⇒ 这里一律用**行为**守卫（改输入 ⇒ 输出必须跟着变）。
2. **不许读生产产物当判据**（第 96 层「读快照的守卫等于没守」）：
   生产台账只作**输入**被复制进 `tmp_path`；断言落在 `tmp_path` 那份上。
3. **不许把「测不到」写成「没有」**（§7.3）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "measure_fundamental_mechanism_armR.py"
FACTORS_MOD = ROOT / "scripts" / "fundamental_family_factors.py"
R6 = ROOT / "scripts" / "register_forward_candidates_r6.py"
PROD_REGISTRY = ROOT / "data" / "reports" / "forward_registry.json"
ARTIFACT = ROOT / "data" / "reports" / "fundamental_mechanism_armR_2026-10-21.json"


def _fresh(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _armr():
    return _fresh(SCRIPT, "_armr_under_test")


def _ff():
    return _fresh(FACTORS_MOD, "_ff_under_test")


# ── 1. n_A 从注册表长度算（不许手打）──────────────────────────────

def test_n_A_is_the_registry_candidate_count_not_a_literal(tmp_path):
    """预注册 §5.2 第 191 行逐字：「n_A … 由候选表长度算出来，**不许手打**」。

    守卫是**行为**的：造一份 7 组候选的台账 ⇒ `n_A` 必须是 7；
    造一份 11 组的 ⇒ 必须是 11。**手打的数做不到这件事。**
    """
    from aplus.forward import register

    mod = _armr()
    for n_cand, expect in ((7, 7), (11, 11)):
        d = tmp_path / f"reg{n_cand}"
        d.mkdir()
        cands = [{"weights": {"bm": 1.0}, "rebalance_every": k,
                  "buffer_multiple": None} for k in range(1, n_cand + 1)]
        register(d, "2026-10-21", cands)
        got = mod._read_registry_n_a(d, "2026-10-21")
        assert got["n_A"] == expect, (
            f"n_A 必须是注册表的候选数（{expect}），实际 {got['n_A']}"
            f" ⇒ 这个数是手打的或读错了地方")
        assert "forward_registry.json" in got["source"]


def test_n_A_refuses_to_guess_when_the_batch_is_missing_or_duplicated(tmp_path):
    """**缺档必须报错，绝不回落**（仓内铁律）：批次不是恰好 1 份 ⇒ 报错。"""
    from aplus.forward import register

    mod = _armr()
    d = tmp_path / "reg"
    d.mkdir()
    cands = [{"weights": {"bm": 1.0}, "rebalance_every": 21,
              "buffer_multiple": None}]
    try:
        mod._read_registry_n_a(d, "2026-10-21")
        raise AssertionError("台账里没有这一批 ⇒ 必须报错，不许回落成 0")
    except ValueError:
        pass


# ── 2. 判决链顺序：MDE 闸第一 ──────────────────────────────────────

def _cell(mean_ic, ic_sd, n_ic, ic_t, t_crit):
    return {"mean_ic": mean_ic, "ic_sd": ic_sd, "n_ic": n_ic, "ic_t": ic_t,
            "t_crit": t_crit, "mde_ic": t_crit * ic_sd / np.sqrt(n_ic)}


def test_the_MDE_gate_is_judged_before_significance():
    """🚨 委托方 §2 D-3：**MDE 闸必须第一个判**。

    构造一个「|t| 很大但 |IC| < MDE」的格子 ⇒ 必须判 `POWERLESS`（测不到），
    **不许**因为 t 大就判「测到了且不够」（`NOT_MEASURED`）。
    """
    mod = _armr()
    cell = _cell(mean_ic=0.010, ic_sd=0.20, n_ic=55, ic_t=9.99, t_crit=2.93)
    assert cell["mean_ic"] < cell["mde_ic"]
    v = mod._verdict_for_cell(cell, 10, 54, 0.06, 0.15, 0.02)
    assert v["state"] == "FUNDAMENTAL_POWERLESS", (
        f"MDE 闸先判 ⇒ 必须 POWERLESS，实际 {v['state']}")
    assert "测不到" in v["reason"]


def test_a_measured_but_not_significant_cell_is_NOT_MEASURED():
    """测到了（|IC|/MDE ≥ 1）但 t 不过线 ⇒ `NOT_MEASURED`（**换因子/换 K，不换判据**）。"""
    mod = _armr()
    cell = _cell(mean_ic=0.090, ic_sd=0.18, n_ic=55, ic_t=1.50, t_crit=2.93)
    assert cell["mean_ic"] >= cell["mde_ic"]
    v = mod._verdict_for_cell(cell, 10, 54, 0.06, 0.15, 0.02)
    assert v["state"] == "FUNDAMENTAL_NOT_MEASURED"


def test_a_significant_but_undeliverable_cell_is_SIG_NOT_DELIVERABLE():
    """族 A 过线但 D1/D2 不过 ⇒ `FUNDAMENTAL_SIG_NOT_DELIVERABLE`（**有 edge 但吃不到**）。"""
    mod = _armr()
    cell = _cell(mean_ic=0.090, ic_sd=0.18, n_ic=55, ic_t=3.50, t_crit=2.93)
    v = mod._verdict_for_cell(cell, 10, 54, 0.06, 0.15, 0.01)
    assert v["state"] == "FUNDAMENTAL_SIG_NOT_DELIVERABLE"
    assert v["d1_pass"] is False


def test_a_missing_threshold_refuses_to_issue_a_verdict():
    """🚨 **缺档必须报错，绝不回落**：阈值读不到 ⇒ 拒绝发判决。

    回落成「吃不到」或「能吃到」都是在**没有判据**的情况下发判决。
    """
    mod = _armr()
    cell = _cell(mean_ic=0.090, ic_sd=0.18, n_ic=55, ic_t=3.50, t_crit=2.93)
    try:
        mod._verdict_for_cell(cell, 10, 54, None, 0.15, 0.01)
        raise AssertionError("阈值缺失 ⇒ 必须报错，不许回落")
    except ValueError:
        pass


# ── 3. 机理①：截尾 IC 的 t 是**它自己**的 t ───────────────────────

def test_a_positive_mean_series_cannot_have_a_negative_t():
    """🚨 正均值序列的 t **不可能为负**。

    旧产物 `bm_ic_attribution.json::trimmed_ic::t` = −1.6119787074299516，
    而它的 `trimmed_ic::mean` = +0.067197 ⇒ 那个 t **不是**截尾 IC 的 t
    （它被 G 节尾部对照循环的 `t` 覆盖了 —— 变量遮蔽）。
    """
    mod = _armr()
    from scripts.fundamental_ic import ic_tstat
    s = pd.Series([0.05, 0.07, 0.06, 0.08, 0.07, 0.09, 0.06])
    t, n = ic_tstat(s)
    assert n == len(s) and t is not None and t > 0


def test_the_artifact_trimmed_t_is_positive_and_its_mean_is_positive():
    """产物里 `trimmed_ic::t` 与 `trimmed_ic::mean` **必须同号**。"""
    if not ARTIFACT.exists():
        return
    d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    rep = (d.get("replication_check") or {}).get("bm_trimmed_ic") or {}
    if not rep:
        return
    assert rep["new_mean"] > 0 and rep["new_t"] > 0, (
        f"截尾 IC 的 mean({rep['new_mean']}) 与 t({rep['new_t']}) 不同号 "
        f"⇒ t 又被别的量覆盖了")
    assert rep["mean_bitwise_equal"] is True, (
        "复现臂的截尾均值必须与旧产物**逐位相等**（同一代码路径）")


# ── 4. 量纲：D2 是分数，headline 是百分数 ──────────────────────────

def test_the_D2_threshold_is_a_fraction_and_matches_the_percent_headline():
    """🚨 本轮实测踩到的量纲坑：`headline::required_gross_at_50k_deliverable`
    是**百分数**（15.5571 = 15.5571%），`rows[n=10]::required_gross_at_50k`
    是**分数**（0.155571）⇒ 差 **100 倍**。

    拿百分数去和「年化毛超额」比 ⇒ 「毛超额 > 所需」**恒假**
    ⇒ 每个过线格都会被误判成「吃不到」。
    """
    mod = _armr()
    thr = mod._threshold_sets(ROOT / "data" / "reports")
    for name in ("primary", "control_working_tree"):
        t = thr.get(name)
        if not t:
            continue
        d2 = t["d2_required_gross_annual_pct"]
        assert 0 < d2 < 1, (
            f"{name} 的 D2 必须是分数（0~1），实际 {d2} ⇒ 单位错了 100 倍")
        head = t.get("d2_headline_percent_form")
        if head is not None:
            assert abs(head / 100.0 - d2) < 1e-9, (
                f"headline（百分数 {head}）与 rows（分数 {d2}）必须相差恰好 100 倍")


# ── 5. 因子定义逐字照抄预注册 §2.1 ────────────────────────────────

def test_the_factor_definitions_match_the_prereg():
    """F1–F5 的定义逐字照抄 2026-10-20 预注册 §2.1（**数值对拍**，不查文本）。"""
    ff = _ff()
    ann = pd.DataFrame([{
        "day": "2020-01-02", "code": "000001.SZ",
        "bps": 10.0, "eps": 2.0, "roe": 12.0, "cfps": 3.0}])
    px = pd.DataFrame([{"day": "2020-01-02", "code": "000001.SZ",
                        "close": 20.0, "a_shares": 100.0}])
    out = ff.factor_values(ann, px).iloc[0]
    assert abs(out["bm"] - np.log(10.0 / 20.0)) < 1e-12
    assert abs(out["ep"] - np.log(2.0 / 20.0)) < 1e-12
    assert abs(out["q"] - 3.0 / 10.0) < 1e-12
    assert abs(out["size"] - (-np.log(20.0 * 100.0))) < 1e-12
    assert abs(out["roe"] - 12.0) < 1e-12


def test_the_frozen_factor_list_is_exactly_F1_to_F5():
    ff = _ff()
    assert ff.FACTORS == ("bm", "ep", "roe", "size", "q")
    assert ff.FAMILY_A_PREREG == ("bm", "ep", "roe", "q")


# ── 6. 裸 6 位码 ↔ 带后缀码（join 的静默零命中）────────────────────

def test_a_bare_financial_code_joins_a_suffixed_panel_code():
    """🚨 财务帧的 `SECURITY_CODE` 是**裸 6 位**（`000001`），
    面板的 `xs[day]["code"]` 是**带后缀**（`000001.SZ`）
    ⇒ 直接 merge **零行命中**，而 merge 静默返回空表
    ⇒ 因子「看起来存在」但一个数都没有。
    """
    ff = _ff()
    ann = pd.DataFrame([{"day": "2020-01-02", "code": "000001",
                         "bps": 10.0, "eps": 2.0, "roe": 12.0, "cfps": 3.0}])
    px = pd.DataFrame([{"day": "2020-01-02", "code": "000001.SZ",
                        "close": 20.0, "a_shares": 100.0}])
    out = ff.factor_values(ann, px)
    assert len(out) == 1, "裸码与带后缀码必须 join 上（否则因子全 NaN 且不报错）"
    assert np.isfinite(out.iloc[0]["bm"])
    assert out.iloc[0]["code"] == "000001.SZ"


# ── 7. 年报序列与 aplus 的口径逐位相等 ────────────────────────────

def test_the_pit_annual_series_agrees_with_aplus_annual_from_frame():
    """本模块自己实现了年报可见性判据（要多留三列）⇒ **必须**与
    `aplus/factor_fundamental.py::annual_from_frame` **逐位相等**
    ⇒ 口径漂了就在这里当场红，而不是在判决里悄悄错掉。
    """
    from aplus.factor_fundamental import (annual_from_frame, pit_frame)

    ff = _ff()
    root = ROOT / "data" / "reports" / "financial_vintage"
    if not root.exists():
        return
    frame = pit_frame(root, "2026-10-20")
    mine = ff.annual_series_from_pit(frame, "2026-10-20")
    ref = annual_from_frame(frame, "2026-10-20")
    latest = (mine.sort_values(["code", "notice_date", "report_date"])
                   .drop_duplicates(subset=["code"], keep="last"))
    assert len(latest) == len(ref)
    a = latest.sort_values("code").reset_index(drop=True)
    b = ref.sort_values("code").reset_index(drop=True)
    assert a["code"].equals(b["code"])
    for col in ("notice_date", "report_date", "bps"):
        assert bool(((a[col] == b[col]) | (a[col].isna() & b[col].isna())).all()), \
            f"列 {col} 与 aplus 不一致 ⇒ 口径漂了"


# ── 8. 前瞻第 6 批：冻结 + K 写死 {21,63} ─────────────────────────

def test_batch6_is_frozen_with_exactly_ten_candidates_and_two_Ks():
    """第 6 批（2026-10-21）：族 A 五因子 × K ∈ {21, 63} = **10 组**。

    🚨 **K 不许从 `LIVE_REBALANCE_EVERY` 派生**（它已是 63 ⇒ 会少一组
    ⇒ 假设数从 10 掉到 5 ⇒ Bonferroni 门槛被悄悄放松）。
    """
    from aplus.forward import load_registry, registry_tamper_report

    rows = load_registry(ROOT / "data" / "reports")
    batches = [r for r in rows if str(r.get("registered_on")) == "2026-10-21"]
    assert len(batches) == 1, f"2026-10-21 的批次必须恰好 1 份，实际 {len(batches)}"
    cands = batches[0]["candidates"]
    assert len(cands) == 10
    assert sorted({c["rebalance_every"] for c in cands}) == [21, 63]
    assert sorted({tuple(c["weights"]) for c in cands}) == [
        ("bm",), ("ep",), ("q",), ("roe",), ("size",)]
    assert all(c["weights"][next(iter(c["weights"]))] == 1.0 for c in cands)
    rep = registry_tamper_report(ROOT / "data" / "reports")
    assert batches[0].get("sealed_sha256")
    assert rep["tampered"] == [], "台账有被篡改的批次"


def test_batch6_registration_is_idempotent(tmp_path):
    """同一批跑第二遍**不得**插第二次（候选数虚增 ⇒ 门槛被自己抬高）。"""
    r6 = _fresh(R6, "_r6_under_test")
    r6.main(reports_dir=tmp_path)
    r6.main(reports_dir=tmp_path)
    from aplus.forward import load_registry
    rows = load_registry(tmp_path)
    assert len(rows) == 1
    assert len(rows[0]["candidates"]) == 10


def test_batch6_K_is_not_derived_from_the_live_rebalance_frequency():
    """🚨 **K 写死 {21,63}**，不许从 `LIVE_REBALANCE_EVERY` 派生。

    守卫是**行为**的：把 `LIVE_REBALANCE_EVERY` 改成 5
    ⇒ 第 6 批的 K 集合必须**不变**（仍是 {21,63}）。
    """
    import aplus.evolve as ev
    r6 = _fresh(R6, "_r6_under_test")
    before = sorted({c["rebalance_every"] for c in r6.CANDS})
    old = ev.LIVE_REBALANCE_EVERY
    try:
        ev.LIVE_REBALANCE_EVERY = 5
        r6b = _fresh(R6, "_r6_under_test2")
        after = sorted({c["rebalance_every"] for c in r6b.CANDS})
    finally:
        ev.LIVE_REBALANCE_EVERY = old
    assert before == after == [21, 63]


# ── 9. 措辞：「测不到」不许写成「没有」 ────────────────────────────

def test_the_artifact_never_writes_no_excess_as_a_conclusion():
    """§5.5 / §7.3：禁止出现「没有超额收益 / 证明没有 alpha / 已证伪 alpha /
    超额不存在」这类措辞（与 `tests/test_factor_width_mde.py` 同款）。"""
    if not ARTIFACT.exists():
        return
    text = ARTIFACT.read_text(encoding="utf-8")
    for bad in ("没有超额收益", "证明没有 alpha", "已证伪 alpha", "超额不存在"):
        assert bad not in text, f"产物里出现了 {bad!r} —— 本轮只能说「测不到」"


def test_the_artifact_states_the_powerless_wording_and_the_short_leg_caliber():
    """产物必须写明「测不到」的文案，且长腿是**多头口径**（不是多空）。"""
    if not ARTIFACT.exists():
        return
    d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    g = d["wording_guard"]["powerless_wording"]
    assert "测不到" in g and "没有超额" in g
    ll = (d["family_a_cells"].get("bm@K21") or {}).get("long_leg") or {}
    assert "多头口径" in ll.get("caliber", "")
    assert "多空" in ll.get("caliber", "")
    assert ll.get("n_positions") == 10


def test_the_artifact_does_not_promise_the_family_can_reach_20_percent():
    """🚨 委托方硬约束：**不许**许诺「基本面族一定能到 20%」。"""
    if not ARTIFACT.exists():
        return
    d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    np_ = d.get("no_promotion", "")
    assert "不宣称" in np_ or "不许" in np_ or "不产生任何采纳结论" in np_
    assert "20%" in np_, "产物必须明写 D2 的墙（否则就是回避）"


# ── 10. F4 `size` 不需要财报（对照因子不能在它最该说话的那条臂上失声）──

def test_size_is_computed_for_every_priced_stock_not_only_those_with_reports():
    """🚨 `size = -log(close × a_shares)` **不需要财报**。

    第一版把它和 bm/ep/roe/q 一起从「当年可见的年报」那一侧算
    ⇒ 全池臂上它的覆盖率跟着财报**塌成 18 只**（本该 5,460 只）
    ⇒ **对照因子在它最该说话的那条臂上失声**。
    """
    ff = _ff()
    codes = ["000001.SZ", "000002.SZ"]
    daily = pd.DataFrame({
        "date": ["2020-01-02"] * 2,
        "code": codes,
        "close": [20.0, 9.0],
        "volume": [1.0e7, 2.0e7],
        "turn": [1.0, 2.0],
    })
    daily_map = {c: daily[daily["code"] == c].reset_index(drop=True)
                 for c in codes}
    # 只有 000001 有年报（000002 没有）
    annual = pd.DataFrame([{
        "code": "000001", "notice_date": pd.Timestamp("2019-04-30"),
        "report_date": pd.Timestamp("2018-12-31"), "report_year": 2018,
        "bps": 10.0, "eps": 2.0, "roe": 12.0, "cfps": 3.0}])
    xs = {"2020-01-02": pd.DataFrame({"code": codes}),
          "_dates": ["2020-01-02"], "_daily_map": daily_map}
    out = ff.attach_factors(xs, annual, ["2020-01-02"], daily_map)
    u = out["2020-01-02"].set_index("code")
    assert "size" in u.columns
    assert np.isfinite(u.loc["000002.SZ", "size"]), (
        "没有年报的票也必须有 `size`（它只需要价格）")
    assert np.isfinite(u.loc["000001.SZ", "size"])
    assert np.isnan(u.loc["000002.SZ", "bm"]), (
        "没有年报的票**不该**有 `bm`（它需要年报）")
    assert np.isfinite(u.loc["000001.SZ", "bm"])
