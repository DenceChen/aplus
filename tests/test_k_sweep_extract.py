"""`k_sweep_extract` 的测试。

重点不是「表格能不能打印」，而是**三条自查会不会真的把坏数据拦下来**。
每条自查都做一次变异：把检查改松，必须变红。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import k_sweep_extract as KSE  # noqa: E402


def _year(y: int, gross: float, daily: float | None = None) -> dict:
    daily = gross / KSE.DAYS_PER_YEAR if daily is None else daily
    return {
        "year": y,
        "gross": {"daily_excess": daily, "annual_pct": gross},
        "real": {"annual_net_pct": gross - 0.06, "annual_cost_pct": 0.06,
                 "t_blocked": 0.5, "hit_threshold": False},
        "bt003": {"daily_excess": daily, "annual_gross_pct": gross},
    }


def _doc(k: int = 21, gross=(0.05, 0.07), **over) -> dict:
    d = {
        "rebalance_every": k,
        "years": [_year(2021 + i, g) for i, g in enumerate(gross)],
        "hurdle_gross": KSE.hurdle_gross(k),
        "cost_rescale_linear_check": {"linear": True},
    }
    d.update(over)
    return d


# ── 门槛重算：只依赖三个外部锚 ─────────────────────────────────

def test_the_hurdle_only_depends_on_the_three_external_anchors():
    """K 越大门槛越低，但**永远高于 TARGET_NET** —— 门槛不能塌到目标以下。"""
    for k in (5, 10, 21, 42, 63, 244):
        h = KSE.hurdle_gross(k)
        assert h > KSE.TARGET_NET
        assert h == pytest.approx(KSE.TARGET_NET + KSE.COST_REAL_50K * KSE.DAYS_PER_YEAR / k)


def test_the_hurdle_costs_a_year_of_trading_at_the_real_rate():
    """年成本 = 单次成本 × 244/K。K=21 → 6.39%/年。"""
    assert KSE.hurdle_gross(21) == pytest.approx(0.20 + 0.0055 * 244 / 21)
    assert KSE.hurdle_gross(21) == pytest.approx(0.2639047619047619)


def test_the_module_imports_nothing_from_the_repo():
    """第二台测量仪必须与被测对象无关 —— 借一个 import 就退化成自证。"""
    src = Path(KSE.__file__).read_text(encoding="utf-8")
    assert "import aplus" not in src and "from aplus" not in src


# ── 摘要 ────────────────────────────────────────────────────────

def test_summarize_averages_and_keeps_per_year_detail():
    row = KSE.summarize(_doc(21, (0.05, 0.07, -0.01)))
    assert row["avg_gross"] == pytest.approx((0.05 + 0.07 - 0.01) / 3)
    assert row["per_year_gross"] == {2021: 0.05, 2022: 0.07, 2023: -0.01}
    assert row["hit_years"] == 0 and row["years"] == 3


def test_the_ratio_is_measured_over_the_recomputed_hurdle():
    """"门槛倍数"的分母必须是**重算**的门槛，否则这个数毫无意义。"""
    doc = _doc(21, (0.05, 0.07))
    row = KSE.summarize(doc)
    assert row["ratio"] == pytest.approx(0.06 / KSE.hurdle_gross(21))
    # 报告里存了别的门槛值也不能改分母
    doc["hurdle_gross"] = 0.001
    assert KSE.summarize(doc)["ratio"] == pytest.approx(0.06 / KSE.hurdle_gross(21))


# ── 自查 1：年化 = 日频 × 244 ───────────────────────────────────

def test_selfcheck_passes_on_a_consistent_report():
    assert KSE.selfcheck(KSE.summarize(_doc())) == []


def test_selfcheck_catches_a_year_that_is_not_daily_times_244():
    doc = _doc()
    doc["years"][0]["bt003"]["annual_gross_pct"] = 0.05 * 100  # 忘了 ×244
    bad = KSE.selfcheck(KSE.summarize(doc))
    assert len(bad) == 1 and "annual_gross_pct" in bad[0]


def test_selfcheck_catches_a_drift_of_a_single_ulp():
    """★ 容差必须是 **0**。

    这条测试是「容差有没有被测过」的守门：只要容差 > 0，
    一个 ULP 级的偏差就会被放过 —— 而 ULP 级的偏差恰恰是
    「公式悄悄换了一个常数」的表现。
    （实测六份真实报告的漂移恰好是 0，所以 0 容差不会误伤。）
    """
    doc = _doc()
    y = doc["years"][0]["bt003"]
    exact = y["daily_excess"] * KSE.DAYS_PER_YEAR
    y["annual_gross_pct"] = math.nextafter(exact, math.inf)  # 只挪最后一位
    assert abs(y["annual_gross_pct"] - y["daily_excess"] * KSE.DAYS_PER_YEAR) > 0, (
        "构造的偏差本身就是 0，这条测试什么也证明不了")
    assert len(KSE.selfcheck(KSE.summarize(doc))) == 1


def test_the_zero_tolerance_is_the_one_actually_in_use():
    """守卫的强度由**最弱输入**决定 —— 这里把容差钉死，别让人日后放宽。"""
    import inspect
    sig = inspect.signature(KSE.selfcheck)
    assert sig.parameters["drift_tol"].default == 0.0
    assert sig.parameters["hurdle_tol"].default == 0.0


def test_selfcheck_catches_a_hurdle_that_is_off_by_one_ulp():
    doc = _doc(21)
    doc["hurdle_gross"] = math.nextafter(doc["hurdle_gross"], math.inf)
    assert len(KSE.selfcheck(KSE.summarize(doc))) == 1


# ── 自查 2：重算门槛必须与报告存的吻合 ──────────────────────────

def test_selfcheck_catches_a_report_whose_stored_hurdle_is_different():
    doc = _doc()
    doc["hurdle_gross"] = 0.20  # 忘了加年成本
    bad = KSE.selfcheck(KSE.summarize(doc))
    assert len(bad) == 1 and "门槛" in bad[0]


def test_selfcheck_catches_a_missing_stored_hurdle():
    doc = _doc()
    del doc["hurdle_gross"]
    bad = KSE.selfcheck(KSE.summarize(doc))
    assert len(bad) == 1 and "不吻合" in bad[0]


# ── 自查 3：成本缩放线性 ────────────────────────────────────────

def test_selfcheck_catches_a_nonlinear_cost_rescale():
    doc = _doc()
    doc["cost_rescale_linear_check"]["linear"] = False
    bad = KSE.selfcheck(KSE.summarize(doc))
    assert len(bad) == 1 and "非线性" in bad[0]


# ── 三条自查之间不能串味 ────────────────────────────────────────

def test_each_isolated_defect_triggers_exactly_one_selfcheck():
    """★ 「恰好一条」是比「至少一条」更强的断言。

    串味的典型后果是：一条缺陷同时报两三条，
    读的人以为有三个问题，实际只有一个 —— 于是去查错的方向。
    """
    cases = {
        "annual_gross_pct": lambda d: d["years"][0].__setitem__(
            "bt003", {**d["years"][0]["bt003"], "annual_gross_pct": 99.0}),
        "hurdle_gross": lambda d: d.__setitem__("hurdle_gross", 0.001),
        "nonlinear": lambda d: d["cost_rescale_linear_check"].__setitem__("linear", False),
    }
    for key, break_it in cases.items():
        doc = _doc()
        break_it(doc)
        bad = KSE.selfcheck(KSE.summarize(doc))
        assert len(bad) == 1, f"{key} 这一项缺陷引出了 {len(bad)} 条告警：{bad}"


def test_all_three_defects_together_are_all_reported():
    """三条都坏时不能只报一条 —— 报一条会让读者以为「就这一个毛病」。"""
    doc = _doc()
    doc["years"][0]["bt003"]["annual_gross_pct"] = 99.0
    doc["hurdle_gross"] = 0.001
    doc["cost_rescale_linear_check"]["linear"] = False
    assert len(KSE.selfcheck(KSE.summarize(doc))) == 3


# ── CLI：没有可读报告就不出表 ───────────────────────────────────

def test_main_refuses_to_print_a_table_when_no_report_exists(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        KSE.main([str(tmp_path / "nope.json")])
    assert "不出表" in str(e.value)
    assert "K |" not in capsys.readouterr().out


def test_main_prints_a_sorted_table(tmp_path, capsys):
    for k in (21, 5):
        p = tmp_path / f"k{k}.json"
        p.write_text(json.dumps(_doc(k)), encoding="utf-8")
    assert KSE.main([str(tmp_path / "k21.json"), str(tmp_path / "k5.json")]) == 0
    out = capsys.readouterr().out
    assert out.index("| 5 |") < out.index("| 21 |")


def test_main_refuses_to_print_when_a_selfcheck_fails(tmp_path, capsys):
    doc = _doc()
    doc["hurdle_gross"] = 0.001
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SystemExit):
        KSE.main([str(p)])
    assert "K |" not in capsys.readouterr().out, "自查没过仍然打了表 = 守卫形同虚设"


def test_main_writes_the_json_summary_with_the_anchors_it_used(tmp_path):
    p = tmp_path / "k21.json"
    p.write_text(json.dumps(_doc(21)), encoding="utf-8")
    out = tmp_path / "sub" / "sweep.json"
    assert KSE.main([str(p), "--json-out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["targets"]["TARGET_NET"] == 0.20
    assert saved["targets"]["COST_REAL_50K"] == 0.0055
    assert saved["targets"]["DAYS_PER_YEAR"] == 244
    assert saved["rows"][0]["k"] == 21


def test_the_format_table_shows_the_year_denominator_not_just_a_count():
    out = KSE.fmt_table([KSE.summarize(_doc(21))])
    assert "| 够线年数 |" in out and "0/2" in out, (
        "「0/2」和「0」是两回事：分母不见了，读者会以为跑了一年还是没数")

# ── 缺失的 t：**缺失就是缺失**，不能当 0 ───────────────────────────

def _doc_with_missing_t(k=63, t_values=(0.3, 0.5, None, 0.9)):
    # ★ 必须按 `t_values` 的长度造年份数：`_doc` 默认只有 2 年，
    #   `zip` 会把 4 个 t 静默截成 2 个 —— 夹具悄悄少给一半数据，
    #   而测试照样「跑过了」（下面 `n_t_obs == 3` 那条一开始报 1 就是这个）。
    doc = _doc(k, gross=tuple(0.01 * (i + 1) for i in range(len(t_values))))
    assert len(doc["years"]) == len(t_values), "夹具年份数与 t_values 不一致"
    for y, tv in zip(doc["years"], t_values):
        y["real"]["t_blocked"] = tv
    return doc


def test_a_missing_t_is_excluded_from_the_mean_not_counted_as_zero():
    """★ `t_blocked` 在调仓块不足 3 个的年份是 `None`（K=63 的 2026 就是）。

    `None` 当 0 = 把「没算出来」读成「t=0」，
    而 0 恰好是最容易被当成「无效应」的那个数 ——
    **一个缺失被静默翻译成了一个结论**。
    """
    row = KSE.summarize(_doc_with_missing_t())
    assert row["n_t_obs"] == 3, f"参与平均的年份数错了：{row['n_t_obs']}"
    assert row["avg_t_real"] == pytest.approx((0.3 + 0.5 + 0.9) / 3)


def test_the_table_shows_how_many_years_actually_had_a_t():
    """分母不见了，读者会以为 6 年都有值。"""
    cell = KSE._t_cell(KSE.summarize(_doc_with_missing_t()))
    assert "3/4" in cell, cell
    full = KSE._t_cell(KSE.summarize(_doc()))
    assert "/" not in full, f"全都算出来了就不该再挂一个分母：{full}"


def test_a_year_with_no_t_at_all_shows_a_dash_not_a_zero():
    row = KSE.summarize(_doc_with_missing_t(t_values=(None, None, None, None)))
    assert row["n_t_obs"] == 0
    assert KSE._t_cell(row) == "—", "把「无值」渲染成 0.00 就是谎报"


def test_nan_t_is_treated_the_same_as_missing():
    doc = _doc_with_missing_t(t_values=(0.3, float("nan"), 0.9, 0.5))
    row = KSE.summarize(doc)
    assert row["n_t_obs"] == 3 and row["avg_t_real"] == pytest.approx((0.3 + 0.9 + 0.5) / 3)


def test_a_single_year_t_renders_a_dash_when_it_is_missing():
    """★ 逐年表是同一个洞的**第二个出口**。

    第一版只在聚合处（`_finite_mean` / `_t_cell`）修了 `None`，
    逐年表里原样 `.2f` 格式化 `None` ⇒ 崩在**已经算完之后**，
    把总表和 5 个 K 的逐年结果一起带走（真实 K=63 报告就是如此）。
    ⇒ 这个 helper 的存在意义就是让两处出口不可能再各写各的。
    """
    assert KSE._fmt_t(None) == "—"
    assert KSE._fmt_t(float("nan")) == "—"
    assert KSE._fmt_t(0.5) == "0.50"


def test_the_cli_survives_a_report_with_a_missing_year_t(tmp_path, capsys):
    """端到端：缺失 t 的报告必须能**跑完并落盘 JSON**。

    这是本次事故的完整形状 —— 崩溃发生在最后一步，
    于是「前三张表都打对了」给人留下了「路径通了」的错觉。
    """
    rep = tmp_path / "wf_k63.json"
    rep.write_text(json.dumps(_doc_with_missing_t(k=63)), encoding="utf-8")
    out = tmp_path / "k_sweep_summary.json"

    rc = KSE.main([str(rep), "--json-out", str(out)])

    assert rc == 0, "缺失 t 不该让抽取失败"
    assert out.is_file(), "崩溃发生在写 JSON 之前 ⇒ 全部结果被丢弃"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["rows"][0]["n_t_obs"] == 3
    assert "—" in capsys.readouterr().out, "缺失的那一年必须显示出来，而不是消失"
