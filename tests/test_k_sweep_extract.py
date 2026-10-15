"""`k_sweep_extract` 的测试。

重点不是「表格能不能打印」，而是**三条自查会不会真的把坏数据拦下来**。
每条自查都做一次变异：把检查改松，必须变红。
"""
from __future__ import annotations

import ast
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


def _repo_imports() -> set[str]:
    """本模块 import 到的**仓内**模块名（AST，不是字符串）。

    ⚠️ 第一版是 `"from aplus" not in src` —— **全文字符串匹配**，
    那是形状 13（断言能被注释满足）＋形状 123（字面量命中 ≠ 结构事实）两族：
    ① docstring 里写一句「本模块不 import aplus」就会把守卫判红；
    ② 真写一行 `from aplus import provenance` 它确实红 ——
       **但它红的理由是「import 了」，不是「借到了数」**，
       而这两件事在这里不等价。
    """
    tree = ast.parse(Path(KSE.__file__).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.split(".")[0] == "aplus")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "aplus":
            out.add(node.module)
            # 🚨🚨 第一版漏了 `names` —— 于是 `from aplus import hurdle`
            #   （最该抓的形态：模块名藏在 names 里，node.module 只是父包 `aplus`）
            #   被判成「只 import 了 aplus」，**逐位放过**。
            #   实测：变异 `from aplus import provenance` → `..., hurdle`，
            #   28 条守卫全绿。同形的 `from aplus.hurdle import rate` 才抓得住。
            #   ⇒ 这正是「守卫测的是它写的那个形态」：两种写法在 AST 里不是同一个节点。
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


# 🚫 借这些就真的自证了：它们**算得出门槛/成本/年化乘数**，
#   而「第二台测量仪」的全部价值就是这些数必须从外部锚**自己**推。
#   加新名字之前先问：它有没有算术？（`provenance` 只做 sha256/git，答案是没有。）
_SELF_CERTIFYING = {
    "aplus.hurdle", "aplus.costs", "aplus.factor", "aplus.composite",
    "aplus.ic_to_return", "aplus.backtest",
}


def test_the_import_detector_sees_both_spellings_of_the_same_import():
    """**检测器是一个判决，判决要被测。**

    🚨 不加这一条，下面两条成对守卫可以悄悄退化，而 pytest 把退化显示得
    和「一切正常」一模一样（28 passed）—— 这就是「守卫失效时外观完全正常」。

    实证：第一版检测器只读 `ImportFrom.node.module`，漏读 `names`，
    于是 `from aplus import hurdle` 被当成「只 import 了父包 `aplus`」而放过，
    **而 `from aplus.hurdle import rate` 抓得住** ——
    两种写法语义完全相同，在 AST 里却不是同一个节点。
    """
    src = Path(KSE.__file__).read_text(encoding="utf-8")

    def names_of(source: str) -> set[str]:
        tree = ast.parse(source)
        out: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(a.name for a in node.names
                           if a.name.split(".")[0] == "aplus")
            elif isinstance(node, ast.ImportFrom) and \
                    (node.module or "").split(".")[0] == "aplus":
                out.add(node.module)
                out.update(f"{node.module}.{a.name}" for a in node.names)
        return out

    from_import = names_of("from aplus import hurdle\n")
    dotted = names_of("from aplus.hurdle import hurdle_rate\n")
    assert "aplus.hurdle" in from_import, "从父包 import 子模块必须看得见子模块名"
    assert "aplus.hurdle" in dotted, "点号形态必须看得见"
    # 纯 sidecar 那个合法 import 本身也要认得出来，否则白名单是空的
    assert "aplus.provenance" in names_of("from aplus import provenance\n")


def test_the_module_borrows_no_computation_from_the_repo():
    """第二台测量仪必须与被测对象无关 —— **借一个算术模块就退化成自证**。"""
    got = _repo_imports() & _SELF_CERTIFYING
    assert not got, (
        f"k_sweep_extract import 了会算出本模块自己要重算的量的模块：{sorted(got)}\n"
        "   ⇒ 它不再是第二台测量仪，而是拿被测对象验被测对象。"
    )


def test_the_only_repo_import_allowed_is_the_sidecar_stamper():
    """`aplus.provenance` 是唯一允许的仓内 import，理由是它**不含算术**。

    ★ 2026-10-15：给它盖章把这份产物从「不可引用」变成可引用，
      而旧守卫判的是「一个 import 都不许有」⇒ **过严的守卫和恒真的一样有害**，
      它会把人推向「为了变绿而把盖章摘掉」，也就是让产物重新退回不可引用。

    这条把例外**显式钉住**：以后谁想把别的模块加进来，会在这里红，
    而不是靠「都禁了」这种一刀切的形态被动发现。
    """
    got = _repo_imports() - {"aplus"}
    assert got <= {"aplus.provenance"}, (
        f"k_sweep_extract 多 import 了 {sorted(got - {'aplus.provenance'})}。"
        "加之前先答：这个模块有没有算术？没有的话在这里显式登记并写清理由。"
    )


def test_the_three_anchors_are_still_literals_not_imported():
    """把「不许借算术」落到**它真正要保的那三个数**上。

    这是本条的**正控**：前两条守的是 import 列表，
    而这一条守的是「万一有人把常量从别处搬来，那也是借」。
    """
    tree = ast.parse(Path(KSE.__file__).read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assigned.update(t.id for t in node.targets if isinstance(t, ast.Name))
    assert {"TARGET_NET", "COST_REAL_50K", "DAYS_PER_YEAR"} <= assigned, (
        "三个外部锚必须是本模块的字面量常量 —— 这正是「不借本仓任何数」的落点"
    )
    # ⚠️ 这里**不能**断言「三个都是 float」—— `DAYS_PER_YEAR = 244` 本来就是 int，
    #   而我第一版写 `isinstance(..., float)` 当场红。
    #   那是我先臆想了「长什么样才对」再去让代码确认（第 98 层）。
    #   真正要守的是「它们是**数值字面量**，不是从别处算出来的」，
    #   所以按**赋值处的 AST 常量节点**判，而不是按运行期类型。
    #
    # 🚨 第二版用 `for…else` + `break`（找到一个就停）⇒ **只要求「有一个锚是字面量」**。
    #   变异实测：`DAYS_PER_YEAR = 244` 后面接一句
    #   `DAYS_PER_YEAR = DAYS_PER_YEAR + 0 * len(TARGET_NET)`
    #   （合法、可运行、数值不变）⇒ **29 条全绿**。
    #   ⇒ 数值没变、行为完全一样，而守卫放行了 —— 这是「恒真守卫」的教科书形态：
    #   它守的是「至少有一个」，而意图是「**每一个**」。
    # 🚨🚨 第三版仍存活：判据是「**出现过**一次字面量赋值就算过」，
    #   而变异是在字面量**后面**再补一句非字面量赋值 —— 于是它照样绿。
    #   ⇒ 判据必须是「**每一次**赋值都是字面量」，不是「至少有一次」。
    #   形态上就是 `all(赋值都是常量)`，而不是 `any(有常量)`：
    #   后者永远为真（模块总得有一处定义），所以它是恒真的。
    anchors = {"TARGET_NET", "COST_REAL_50K", "DAYS_PER_YEAR"}
    non_literal: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)} & anchors
        if not names:
            continue
        is_literal = (isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, (int, float))
                      and not isinstance(node.value.value, bool))
        if is_literal:
            continue
        rendered = ast.unparse(node.value)[:70]
        for n in names:
            non_literal.setdefault(n, []).append(rendered)

    assert not non_literal, (
        f"外部锚 {sorted(non_literal)} 存在**非字面量**赋值：{non_literal} —— "
        "「不借本仓任何数」的落点就是这三个常量本身。"
        "哪怕算出来的数值一模一样，也说明**这份门槛的来源不再是外部锚**。"
    )


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
