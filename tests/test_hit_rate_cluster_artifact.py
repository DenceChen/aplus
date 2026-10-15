"""守卫（**产物层**）：落盘的 `hit_rate_diagnosis.json` 必须真的是聚类口径。

**预注册**：`docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md` §5 G6

## 为什么必须与源码层守卫**分文件**

产物层守卫读的是**上一次落盘的 JSON**，源码怎么改它都看不见；
而产物新鲜度会因为**任何**源码变异而红 ⇒ 两者混进同一个文件，
等于给**每一条**变异发免费信用（`CLAUDE.md` 第 157 层的纪律）。
源码层那半在 `test_hit_rate_cluster_statistic.py`。

## 这一层能抓而源码层抓不到什么

源码层守卫验的是「函数在合成夹具上算出什么」；这一层验的是
**读者真正会读到的那份文件**。两者之间隔着落盘、序列化与一次真实运行 ——
产物一旦不同步，源码层**全绿**。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data/reports/hit_rate_diagnosis.json"
NULL_RATE = 0.5

# 与落盘精度一致（半宽落 6 位）。⚠️ 写成推导式而不是经验值：
#   精度变了守卫要自己变松；写死的 rel_tol 会让它悄悄开始接受一条偏 1e-3 的产物。
QUANTUM = 1e-6


def _ratio_tol(*values: float) -> float:
    return sum(QUANTUM / v for v in values)


def _artifact() -> dict:
    assert ART.is_file(), f"产物不存在：{ART}（先真跑 scripts/diagnose_hit_rate.py）"
    return json.loads(ART.read_text(encoding="utf-8"))


def _blocks(a: dict) -> dict:
    """产物里所有带推断量的块。"""
    out = {"overall": a["overall"]}
    out.update({f"by_direction.{k}": v for k, v in a["by_direction"].items()})
    out.update({f"with_market.{k}": v for k, v in a.get("with_market", {}).items()})
    return out


#: `scripts/measure_portfolio_nav.py:180-181` 逐字写死的两个剔除原因。
#: 抄的是**键集**（契约面），不是**数值**（随底稿漂移的快照）。
_CONSUMER_EXCLUSION_KEYS = frozenset({"no_data", "flat"})


def _consumer_contract_violation(a: dict) -> str | None:
    """验消费者（`measure_portfolio_nav.py`）依赖的**契约面**；完好返回 `None`。

    抽成函数是为了让**对照臂能真的调用它** —— 若把断言留在测试体里，
    对照臂就只能再抄一份（第 73 层的副本搬进了测试）。
    """
    td = a.get("trading_days")
    if type(td) is not int or td <= 0:      # 🚨 `type is` 而非 isinstance：bool 是 int 的子类
        return f"trading_days 必须是正 int，实测 {td!r}（type={type(td).__name__}）"

    ex = a.get("excluded")
    if not isinstance(ex, dict):
        return f"excluded 必须是 dict，实测 {type(ex).__name__}"
    extra, miss = set(ex) - _CONSUMER_EXCLUSION_KEYS, _CONSUMER_EXCLUSION_KEYS - set(ex)
    if extra or miss:
        return (f"excluded 的键集与消费者不符：多出 {sorted(extra)}、缺 {sorted(miss)}"
                " ⇒ measure_portfolio_nav.py:180-181 只算这两个，多出来的会静默漏掉")
    for k, v in ex.items():
        if type(v) is not int or v < 0:
            return f"excluded[{k!r}] 必须是非负 int，实测 {v!r}"
    return None


# ══════════════ 两个口径必须在**落盘的那份**里并存 ══════════════

def test_every_block_in_the_landed_artifact_carries_both_calibers():
    a = _artifact()
    blocks = _blocks(a)
    assert len(blocks) >= 5, f"块数 {len(blocks)} 异常 ⇒ 产物结构变了，先查清再改守卫"
    for name, b in blocks.items():
        for k in ("ci95", "ci95_row_level", "ci95_caliber", "ci95_row_level_caliber",
                  "half_width", "half_width_row_level"):
            assert k in b, f"{name} 缺 {k} ⇒ 产物是**旧格式**，没重跑"
        assert b["ci95_row_level_caliber"] != b["ci95_caliber"], f"{name} 两个标注相同"
        assert "虚增" in b["ci95_row_level_caliber"], \
            f"{name} 行级口径没标「被窗口叠加虚增」"
        assert b["half_width"] > b["half_width_row_level"], (
            f"{name} 聚类半宽 {b['half_width']} 没有大于行级半宽 "
            f"{b['half_width_row_level']} ⇒ 这份产物不是按成交窗口算的")


def test_every_landed_interval_is_exactly_its_own_point_estimate_plus_its_half_width():
    """🚨 `ci95` 与 `half_width` 必须在产物里**互为对账**。

    这条是被一次变异逼出来的：把 `ci95` 换成行级那一个、`half_width` 留在聚类值上，
    守卫**全绿** —— 因为上面两条读的是 `half_width`，而读者读的是 `ci95`。
    ⇒ 「两个字段都在」不等于「它们说的是同一件事」。

    截断规则写在这里：区间必须落在 [0,1]，且 `ci95_clipped` 必须如实说它截没截。
    """
    for name, b in _blocks(_artifact()).items():
        for ci, hw, key, clipped_key in (
                ("ci95", "half_width", "ci95_caliber", "ci95_clipped"),
                ("ci95_row_level", "half_width_row_level",
                 "ci95_row_level_caliber", None)):
            if b[ci] is None:
                assert b[hw] is None, f"{name}: {ci} 是 None 而 {hw} 不是 ⇒ 半个口径"
                continue
            assert hw is not None, name
            lo = max(0.0, b["rate"] - b[hw])
            hi = min(1.0, b["rate"] + b[hw])
            assert math.isclose(b[ci][0], round(lo, 4), abs_tol=1e-4), \
                f"{name}.{ci} 下界 {b[ci][0]} 与 rate-half_width {round(lo, 4)} 对不上"
            assert math.isclose(b[ci][1], round(hi, 4), abs_tol=1e-4), \
                f"{name}.{ci} 上界 {b[ci][1]} 与 rate+half_width {round(hi, 4)} 对不上"
            if clipped_key is not None:
                assert b[clipped_key] is bool(lo != b["rate"] - b[hw]
                                              or hi != b["rate"] + b[hw]), \
                    f"{name}.{clipped_key} 与实际截断情况不符"
                assert b[ci][1] <= 1.0 and b[ci][0] >= 0.0, f"{name}.{ci} 越出 [0,1]"


def test_the_landed_difference_between_calibers_is_purely_the_window_stacking():
    """产物里两个口径的差必须**只**来自 `n → W`。

    ⇒ 代数恒等式。它不需要知道任何正确答案，只需要「除了聚类什么都没变」。
    """
    for name, b in _blocks(_artifact()).items():
        if b["half_width"] is None:
            continue
        ratio = b["half_width"] / b["half_width_row_level"]
        assert math.isclose(ratio, math.sqrt(b["n"] / b["n_windows"]),
                            rel_tol=_ratio_tol(b["half_width"], b["half_width_row_level"])), (
            f"{name} 半宽比 {ratio:.6f} ≠ sqrt(N/W)={math.sqrt(b['n'] / b['n_windows']):.6f}")


def test_the_window_count_in_the_landed_artifact_is_a_measurement_not_a_typed_number():
    """🚨 对账守卫：从**真实底稿**重数一遍成交窗口，看它等不等于产物里的 `n_windows`。

    上面三条都验「产物内部自洽」，而一份自洽的产物完全可以把
    `n_windows` 写成行数、把 `windows` 编成每行一个 ——
    那样三个字段**互相**都对得上，而独立观测数是错的（整条修复的核心）。

    ⚠️ 底稿 parquet **不入库**（`.gitignore` 只放 JSON 回去）⇒ 缺它时 skip。
      这是本仓既有做法（`test_diagnose_hit_rate.py` 同款），
      但 skip 与「通过」在门禁输出里长得一样 ⇒ 这里把 skip 的理由写进说明。
    """
    import pandas as pd

    ver = ROOT / "data/reports/verification.parquet"
    if not ver.is_file():
        pytest.skip("verification.parquet 尚未生成（它不入库）⇒ 无法对账成交窗口数")
    a = _artifact()
    df = pd.read_parquet(ver)
    m = df[df["result"].isin(["hit", "miss"])]
    # ⚠️ 只在底稿带 `exit_date` 时对账：旧行退回 `pred_date` 并加 `legacy:`
    #   前缀，分组规则见 `diagnose_hit_rate.py::_cluster_of`。
    key = m["exit_date"] if m["exit_date"].notna().any() else m["pred_date"]
    assert a["n_windows"] == int(key.nunique()), (
        f"产物说 {a['n_windows']} 个成交窗口，底稿数出来是 {int(key.nunique())} 个 "
        f"⇒ 窗口数不是量出来的，是写上去的")
    assert a["verdicts"] == len(m), "判定数与底稿不一致 ⇒ 产物比底稿旧"


def test_the_window_denominators_add_up_to_the_block_denominator():
    """逐窗口 `n` 之和必须等于块的 `n`。

    🚨 不等就说明窗口表与区间**不是同一批行算的** ——
      而两个数都在产物里，读者没有任何办法发现。
    """
    for name, b in _blocks(_artifact()).items():
        assert sum(w["n"] for w in b["windows"]) == b["n"], name
        assert sum(w["hits"] for w in b["windows"]) == b["hits"], name
        assert b["n_windows"] == len(b["windows"]), name


# ══════════════ 🚨 本缺陷本身：把「测不出」显示成「边缘显著」 ══════════════

def test_no_block_may_be_directional_while_its_point_sits_inside_one_mde():
    """「测不出方向」不许被任何一层区间显示成「有方向」。

    ⚠️ **如实标注：这条不是旧产物的原样形状。**
    旧 `up.ci95 = [0.3428, 0.6349]` **也跨过了 0.5** ——
    旧产物的错不是「判成了显著」，是**半宽窄了 3.87 倍**，
    读起来像「离 50% 只差 15 个点」而实际是「差 ±57 个点都定不了」。
    那条由上面两条（半宽比恒等式 + ci95/half_width 对账）守着。

    这里守的是**同族的下一格**：若某天底稿变多、点估计真的远离 50%，
    但 `gap_over_mde < 1`（80% 检出力下测不到），区间仍然**不许**排除 0.5。
    它今天近乎恒真 ⇒ 记在这里是为了不让它变成一个「看起来在守」的装饰。
    """
    for name, b in _blocks(_artifact()).items():
        if b["gap_over_mde"] is None or b["ci95"] is None:
            continue
        if b["gap_over_mde"] < 1.0:
            assert b["ci95"][0] < NULL_RATE < b["ci95"][1], (
                f"{name} 离 50% 只有 {b['gap_over_mde']} 个 MDE，"
                f"区间却是 {b['ci95']}（排除 0.5）⇒ 把「测不出」显示成了「有方向」")


def test_every_point_estimate_is_printed_beside_its_mde():
    """MDE 必须与点估计同块落盘（第 16/160 层：不许只说「不显著」）。"""
    for name, b in _blocks(_artifact()).items():
        assert "mde_rate_points" in b and "abs_gap_to_50pct" in b, name
        assert b["measurement_note"], f"{name} 没有 measurement_note"
        if b["gap_over_mde"] is not None and b["gap_over_mde"] < 1.0:
            assert "连符号都定不了" in b["measurement_note"], (
                f"{name} 测不到却没写「连符号都定不了」⇒ 把『测不出』写成了『没有』")


def test_the_row_level_label_is_false_exactly_when_no_window_stacking_happened():
    """反控的一半：恒 `True` 的标注是装饰。

    ⇒ 真产物里若有一块 `n_windows == n`，那个块的标注必须是 `False`。
      （今天的真产物每一块都叠加了，所以这条**今天近乎恒真** ——
      它守的是「实现不许把这个布尔写死」，不是今天的数。）
    """
    for name, b in _blocks(_artifact()).items():
        assert b["row_level_inflated_by_window_stacking"] == (b["n_windows"] < b["n"]), name
        if b["n_windows"] == b["n"]:
            assert math.isclose(b["inflation_factor"], 1.0, rel_tol=1e-9), name


# ══════════════ 措辞与兼容面 ══════════════

def test_no_value_in_the_landed_artifact_carries_pass_fail_wording():
    blob = json.dumps(_artifact(), ensure_ascii=False)
    for word in ("达标", "未达标", "过关", "及格"):
        assert word not in blob, f"产物正文出现交付式措辞「{word}」"


def test_the_consumer_contract_of_the_older_report_is_untouched():
    """`measure_portfolio_nav.py` 读 `trading_days` 与 `excluded`：
    前者参与不变式 `0 < n_days <= trading_days`，后者在 `:188` **逐字对账**
    （消费者的 `raise` 守着那一半）。

    这里守的是**键集与类型**这个契约面，**不**守数值 ——
    数值随底稿漂移（实测 `no_data` 已从 8 走到 1），钉它就是第 73 层的副本，
    而副本一红就只会被人原地更新，永远不消灭（预注册 §6.1/§6.2）。"""
    a = _artifact()
    why = _consumer_contract_violation(a)
    assert why is None, why
    assert a["verdicts"] > 0


# ══════════════ 契约面的探测器不许是装饰（第 112/117 层） ══════════════

def test_the_contract_checker_rejects_a_key_the_consumer_does_not_know():
    """正控 A：消费者 `:180-181` 只算 `no_data`/`flat` ⇒ 多出来的键会**静默漏掉**。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "halt": 2}
    assert _consumer_contract_violation(a) is not None


def test_the_contract_checker_rejects_a_stringified_count():
    """正控 B：`"1"` 与 `1` 混过去不会被任何人看见 ⇒ 类型必须被钉。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "no_data": str(a["excluded"]["no_data"])}
    assert _consumer_contract_violation(a) is not None


def test_the_contract_checker_accepts_a_different_but_legal_count():
    """🚨 反控 P6′：数值**必须**能变。
    若把 `no_data` 加 7 反而会红，说明这条守卫已经退回成快照守卫 ——
    那正是本次要消灭的缺陷（第 73 层），**绿才是守好**。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "no_data": a["excluded"]["no_data"] + 7}
    assert _consumer_contract_violation(a) is None


def test_the_contract_checker_rejects_a_boolean_trading_days():
    """正控 C：`isinstance(True, int)` 在 Python 里为真 ⇒ 旧断言对 `True` 放行。"""
    a = dict(_artifact())
    a["trading_days"] = True
    assert _consumer_contract_violation(a) is not None
