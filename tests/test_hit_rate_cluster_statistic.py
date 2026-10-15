"""守卫（源码层）：命中率的**独立观测单位必须是成交窗口**，不是交易行。

**预注册**：`docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md`

## 为什么这条守卫值钱

`hit_rate_diagnosis.json` 的区间此前按**行**算：up 腿 45 行里只有 **3 个**
独立成交窗口（`run_daily.py:290-293` 逐字记载周五/六/日三次出单落进同一窗口）
⇒ 45 次「独立重抽样」是假的 ⇒ 区间被压窄 ⇒ **把「测不出方向」显示成「边缘显著」**。

## 为什么不与产物层守卫放同一个文件

产物层守卫读的是**上一次落盘的 JSON** ⇒ 源码变异不会立刻改它，
而产物新鲜度会因任何源码变异而红 ⇒ 混进同一个文件等于给**每一条**变异发免费信用
（`CLAUDE.md` 157 层的纪律）。产物层那半在 `test_hit_rate_cluster_artifact.py`。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import diagnose_hit_rate as dhr  # noqa: E402


# ── 夹具 ───────────────────────────────────────────────────────────────
# ⚠️ **夹具不许读真产物**：真产物每天长几行，拿它当测试输入等于
#   「拿现场当标准答案」（`diagnose_hit_rate.py::diagnose_verdicts` 的
#   docstring 已经写明这条）。这里造的是**形状与实测一致**的合成表。

def _row(hit: int, pred: str, exit_: str | None, code: str,
         direction: str = "up", market: float = 0.0) -> dict:
    r = {"code": code, "direction": direction, "result": "hit" if hit else "miss",
         "actual_pct": 0.01 if hit else -0.01, "market_pct": market,
         "adjusted_score": 0.5, "pred_date": pred}
    if exit_ is not None:
        r["exit_date"] = exit_
    return r


def _clustered_up_fixture() -> pd.DataFrame:
    """up 腿的实测形状：3 个窗口，行数 29 / 6 / 10，hit 15 / 2 / 5。"""
    rows, i = [], 0
    for pred, exit_, n, h in [("2026-10-09", "2026-10-13", 29, 15),
                              ("2026-10-10", "2026-10-14", 6, 2),
                              ("2026-10-11", "2026-10-15", 10, 5)]:
        for k in range(n):
            rows.append(_row(hit=int(k < h), pred=pred, exit_=exit_,
                             code=f"{i:06d}.SZ"))
            i += 1
    return pd.DataFrame(rows)


def _one_row_per_window(hits: list[int]) -> pd.DataFrame:
    """每个交易日自成一个成交窗口（`W == N` ⇒ 行级口径**没有**被虚增）。"""
    return pd.DataFrame([
        _row(hit=h, pred=f"2026-10-{9 + i:02d}", exit_=f"2026-10-{13 + i:02d}",
             code=f"{i:06d}.SZ")
        for i, h in enumerate(hits)])


def _all_blocks(out: dict) -> dict:
    """产物里所有带推断量的块（overall / by_direction.* / with_market.*）。"""
    blocks = {"overall": out["overall"]}
    blocks.update({f"by_direction.{k}": v for k, v in out["by_direction"].items()})
    if "with_market" in out:
        blocks.update({f"with_market.{k}": v for k, v in out["with_market"].items()})
    return blocks


def _pooled_from_windows(block: dict) -> float:
    """合并率 = Σhits/Σn（用块自报的逐窗口两半，不去重算率公式）。"""
    w = block["windows"]
    return sum(x["hits"] for x in w) / sum(x["n"] for x in w)


# 产物里 half_width / half_width_row_level / inflation_factor 的落盘精度。
# ⚠️ 下面所有容差都由它**推导**，不许手打经验值（第 112 层）：
#   把落盘精度改成 2 位时，守卫要自己变松；而写死的 rel_tol 会让守卫
#   悄悄开始接受一条偏 1e-3 的实现 —— 那正是「恒真的另一种写法」。
QUANTUM = 1e-6


def _ratio_tol(*values: float) -> float:
    """比值的相对容差 = 各侧四舍五入误差之和（每侧 ≤ QUANTUM/2）。"""
    return sum(QUANTUM / v for v in values)


# ══════════════ G1：区间必须是聚类口径，且行级口径并排留着 ══════════════

def test_the_headline_interval_is_the_clustered_one_and_is_wider():
    """`ci95` 必须是**聚类**口径，且半宽严格大于行级半宽。

    🚨 **钉的是关系，不是存在**：写成 `ci95_row_level = ci95`（两个口径塌成一个）
    也能满足「两个键都在」⇒ 那正是本缺陷的另一种形态。
    """
    out = dhr.diagnose_verdicts(_clustered_up_fixture())
    b = out["overall"]
    assert b["n_windows"] == 3, b["n_windows"]
    assert b["half_width"] is not None and b["half_width_row_level"] is not None, b
    assert b["half_width"] > b["half_width_row_level"], (
        f"聚类半宽 {b['half_width']} 必须严格大于行级半宽 {b['half_width_row_level']}："
        "45 行只落在 3 个成交窗口上，按行算等于把一次开平仓当 45 次独立重抽样")


def test_the_two_calibers_differ_by_exactly_the_window_stacking_factor():
    """两个口径的差必须**只**来自 `n → W` 这一处改动。

    ⇒ 判据是代数恒等式 `hw聚类 / hw行级 == sqrt(N / W)`：
    它不需要知道任何正确答案，只需要「除了聚类什么都没变」。
    ⚠️ 方向是 **N/W 不是 W/N** —— 行越多、窗口越少，聚类口径越宽。
      写反了它会拿 `0.258` 去比一个 `3.87`，**红得毫无意义**。
    """
    b = dhr.diagnose_verdicts(_clustered_up_fixture())["overall"]
    N, W = b["n"], b["n_windows"]
    ratio = b["half_width"] / b["half_width_row_level"]
    assert math.isclose(ratio, math.sqrt(N / W),
                        rel_tol=_ratio_tol(b["half_width"], b["half_width_row_level"])), (
        f"半宽比 {ratio:.6f} ≠ sqrt(N/W)={math.sqrt(N / W):.6f} ⇒ "
        "两个口径之间还有别的改动 ⇒ 「聚类」这个说法解释不了它们的差")


def test_a_clipped_interval_says_so_and_still_reports_the_true_half_width():
    """命中率区间必须落在 [0,1]，而**截断过这件事本身要落盘**。

    ⇒ 半宽是从 `ci95` 反推不出来的（截断会把 105% 变成 100%），
      所以必须单列 `half_width`，并在截断时明确标注 —— 否则
      「半宽比 = sqrt(W/N)」这条恒等式会被截断悄悄破坏，而读者看不出来。
    """
    b = dhr.diagnose_verdicts(_clustered_up_fixture())["overall"]
    assert b["ci95_clipped"] is True, "这个夹具的聚类区间必定冲出 [0,1]（半宽 0.57）"
    assert 0.0 <= b["ci95"][0] <= b["ci95"][1] <= 1.0
    assert b["ci95"][1] < b["rate"] + b["half_width"], "上界被截断了却没标"
    # 未截断的夹具：`half_width` 是区间的**半**宽 ⇒ 整个跨度 = 2×half_width
    b2 = dhr.diagnose_verdicts(_one_row_per_window([1, 0, 1, 0]))["overall"]
    assert b2["ci95_clipped"] is False, b2["ci95"]
    assert math.isclose(b2["ci95"][1] - b2["ci95"][0], 2 * b2["half_width"],
                        rel_tol=_ratio_tol(b2["half_width"]))


def test_every_block_carries_both_calibers_not_only_the_headline():
    """每个块都要有两个口径：只给 overall 并排、其余块单口径 ⇒ 读者会按错的量读某一层。"""
    for name, b in _all_blocks(dhr.diagnose_verdicts(_clustered_up_fixture())).items():
        for k in ("ci95", "ci95_row_level", "ci95_caliber", "ci95_row_level_caliber"):
            assert k in b, f"{name} 缺 {k}"
        assert b["ci95_row_level_caliber"] != b["ci95_caliber"], (
            f"{name} 两个口径的标注文字相同 ⇒ 读者分不出自己在读哪一个")
        assert "虚增" in b["ci95_row_level_caliber"], (
            f"{name} 行级口径没标「被窗口叠加虚增」⇒ 预注册 C3 要求它自带标注")


# ══════════════ G2：聚类键真的读了 exit_date ══════════════

def test_three_pred_dates_sharing_one_exit_date_collapse_into_one_window():
    """**判别式**：同一批行，3 个 `pred_date` 共享 1 个 `exit_date`。

    若实现偷用 `pred_date`，这里会给出 3 个窗口并**照样算出区间** ⇒ 守卫变红。
    """
    rows = [_row(hit=1, pred=f"2026-10-{9 + i}", exit_="2026-10-13",
                 code=f"{i:06d}.SZ") for i in range(3)]
    out = dhr.diagnose_verdicts(pd.DataFrame(rows))
    assert out["overall"]["n"] == 3
    assert out["overall"]["n_windows"] == 1, out["overall"]["n_windows"]
    assert out["overall"]["ci95"] is None, (
        "只有 1 个独立成交窗口 ⇒ 窗口间方差估不出来 ⇒ 必须拒绝出区间，"
        "返回 [0,1] 或 [p,p] 都是「看起来有结论」的假象")
    assert out["overall"]["mde_rate_points"] is None


def test_rows_without_an_exit_date_fall_back_to_pred_date_in_a_separate_group():
    """缺 `exit_date` 的历史行退回 `pred_date`，且**加前缀单独成组**。

    🚨 不加前缀的话，`pred_date='2026-10-13'` 的旧行会与
      `exit_date='2026-10-13'` 的新行**混进同一个窗口** ⇒ 那是把两个不同的量合并。
    """
    rows = [_row(hit=1, pred="2026-10-09", exit_="2026-10-13", code="000001.SZ"),
            _row(hit=0, pred="2026-10-09", exit_=None, code="000002.SZ")]
    out = dhr.diagnose_verdicts(pd.DataFrame(rows))
    wins = [w["window"] for w in out["overall"]["windows"]]
    assert len(wins) == 2, wins
    assert "legacy:2026-10-09" in wins, wins


def test_the_window_rates_are_reported_with_their_own_denominators():
    """逐窗口的 `n` 必须落盘 —— 只报窗口率不报 n，读者无法判断哪个窗口在说话。"""
    out = dhr.diagnose_verdicts(_clustered_up_fixture())
    wins = out["overall"]["windows"]
    assert [w["n"] for w in wins] == [29, 6, 10], wins
    assert [w["hits"] for w in wins] == [15, 2, 5], wins
    assert sum(w["n"] for w in wins) == out["overall"]["n"], \
        "逐窗口 n 之和必须等于块的行数，否则两个口径的分母不是同一个"


# ══════════════ G3：不许用简单平均冒充整体命中率 ══════════════

def test_the_unweighted_mean_of_window_rates_is_not_the_headline_rate():
    """每窗口 n 不等 ⇒ 简单平均 ≠ 合并率 ⇒ 它必须单列且**明确不是**整体命中率。"""
    b = dhr.diagnose_verdicts(_clustered_up_fixture())["overall"]
    mean = b["unweighted_mean_of_window_rates"]
    assert mean is not None
    assert not math.isclose(mean, b["rate"], rel_tol=1e-3), (
        "夹具刻意让两者不同（29/6/10 加权）⇒ 相等说明实现把简单平均冒充成了整体")
    assert math.isclose(b["rate"], _pooled_from_windows(b), rel_tol=1e-3), \
        "整体命中率必须是 Σhits/Σn，不是窗口率的平均"
    assert "不是整体命中率" in b["unweighted_mean_note"], (
        "简单平均那一列必须自带「这不是整体命中率」的标注，"
        "否则它会被下一读成整体")


# ══════════════ G4：行级虚增的标注必须有有实 ══════════════

def test_the_row_level_label_is_false_when_no_window_stacking_actually_happened():
    """🚨 恒 `True` 的标注是装饰。

    ⇒ 反控：每行自成一窗（`W == N`）时，行级口径**并没有**被虚增，
      标注必须是 `False` 且膨胀因子必须 = 1。
    """
    b = dhr.diagnose_verdicts(_one_row_per_window([1, 0] * 4))["overall"]
    assert b["n_windows"] == b["n"] == 8, b
    assert b["row_level_inflated_by_window_stacking"] is False, \
        "没有窗口叠加 ⇒ 标注成 True 是谎话，且会让读者忽略真发生叠加的那些块"
    assert math.isclose(b["inflation_factor"], 1.0, rel_tol=1e-9), b["inflation_factor"]


def test_the_row_level_label_is_true_when_windows_really_stack():
    b = dhr.diagnose_verdicts(_clustered_up_fixture())["overall"]
    assert b["row_level_inflated_by_window_stacking"] is True
    assert b["inflation_factor"] > 1.0
    assert math.isclose(b["inflation_factor"], math.sqrt(b["n"] / b["n_windows"]),
                        rel_tol=_ratio_tol(b["inflation_factor"]))


# ══════════════ C4：MDE 必须与点估计并排，且映射受判 ══════════════

def test_mde_is_printed_beside_the_point_estimate_in_every_block():
    for name, b in _all_blocks(dhr.diagnose_verdicts(_clustered_up_fixture())).items():
        for k in ("rate", "mde_rate_points", "abs_gap_to_50pct", "gap_over_mde"):
            assert k in b, f"{name} 缺 {k}"
        assert b["mde_rate_points"] is not None, f"{name} 有区间却没有 MDE"
        assert math.isclose(b["abs_gap_to_50pct"], abs(b["rate"] - 0.5), rel_tol=1e-3)


def test_the_unmeasurable_note_appears_exactly_when_the_point_is_below_one_mde():
    """🚨 钉的是**条件 → 标注的映射**，不是「标注这个键存在」。

    写死一句无关的话也能满足存在性断言；这里两条夹具一正一反：
    `gap/MDE < 1` 必须带「连符号都定不了」，`≥ 1` 必须**不带**。
    """
    b = dhr.diagnose_verdicts(_clustered_up_fixture())["overall"]
    assert b["gap_over_mde"] is not None and b["gap_over_mde"] < 1.0, (
        f"夹具本该是「测不到」的一格，实测 gap/MDE={b['gap_over_mde']}")
    assert "连符号都定不了" in b["measurement_note"]

    # 反控：8 个独立窗口、7 中 1 不中 ⇒ 离 50% 已超过 1 个 MDE
    b2 = dhr.diagnose_verdicts(_one_row_per_window([1] * 7 + [0]))["overall"]
    assert b2["gap_over_mde"] >= 1.0, b2["gap_over_mde"]
    assert "连符号都定不了" not in b2["measurement_note"], (
        "这个点估计离 50% 已有 1 个 MDE 以上 ⇒ 「连符号都定不了」是假话")


# ══════════════ C5：措辞 ══════════════

BANNED_WORDING = ("达标", "未达标", "过关", "及格")


def _banned_in(blob: str) -> list[str]:
    return [w for w in BANNED_WORDING if w in blob]


def test_no_value_in_the_product_carries_pass_fail_wording():
    """禁「达标/未达标」类措辞（预注册 C5）。

    ⚠️ 查的是 `json.dumps` 之后的**值**，不是源码文本 ——
      源码里的中文注释本来就会解释「为什么禁这个词」，子串会把判据变成笑话。
    """
    out = dhr.diagnose_verdicts(_clustered_up_fixture())
    assert _banned_in(json.dumps(out, ensure_ascii=False)) == [], \
        "产物正文出现交付式措辞"


def test_the_wording_detector_actually_catches_a_planted_word():
    """🚨 正控：探测器本身必须被验。

    措辞守卫在「产物里本来就没有那些词」时**恒绿** ⇒ 它可能早就坏了
    （词表清空、比对写错、只扫了不相关的一层），而症状与「检查通过」逐位相同。
    ⇒ 把一个禁词**植入**一份复制品，断言探测器抓得到。
    """
    out = dhr.diagnose_verdicts(_clustered_up_fixture())
    assert _banned_in(json.dumps(out, ensure_ascii=False)) == [], \
        "正控的前提被破坏：真实产物里已经有关键词，植入就不构成对照了"
    out["overall"]["evidence"] = "命中率已达标，可以发布"
    assert _banned_in(json.dumps(out, ensure_ascii=False)) != [], \
        "探测器对着植入的「达标」没反应 ⇒ 这条措辞守卫是装饰，不是守卫"
