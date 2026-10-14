"""月频判决脚本必须**机械执行预注册的判据**，不许有可调的口径。

## 背景

`docs/TODO_measure_sigma_r.md` 在**看到数据之前**预注册了判据：

    过关所需 rev5 低尾 IC（σ_r = 0.02656 实测）= 0.04167
    8 年里 0 年低于它 → 月频可行；任何一年低于它 → 那个年不过线

预注册的全部意义，就是**结果出来之后标准不能动**。
若判决逻辑是人临场判断的，「把标准调到刚好能过」就会发生——
而**支持既有目标的数恰恰最不该被质疑**（第 63 条的教训）。

所以这个判据必须是**一段没有旋钮的代码**。

## 三条纪律

1. **不许回落**：某年缺低尾档时**报错**，绝不悄悄用全截面 `all` 顶上。
   回落会让读者以为看到的是低尾，实际看到的是全截面——
   而这正是 2026-10-14 那个 bug 的形态（`yearly` 只聚合 `all`）。
2. **不许静默跳过**：缺档必须**报出是哪几年**。
3. **判决必须跟着 σ_r 走**：σ_r 实测改成 0.02656 后判决线随之变，
   硬编码判决线会让 σ_r 的修正**不起作用**。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/verdict_monthly_hurdle.py")


def _run(report: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(report), *extra],
        capture_output=True, text=True)


# ── 一、缺档必须报错，绝不回落 ────────────────────────────────────

def test_a_report_missing_the_tail_bucket_is_refused(tmp_path):
    """**本文件的核心断言**：`factor_ic_7y.json` 的 `yearly` 只有
    `ic_mean`（即 `all`），缺 `tail_low`。

    脚本必须**报错退出**，而不是拿 `all` 顶上——
    顶上来的话，读者会以为在看低尾，实际在看全截面，
    而全截面逐年 IC 八年里 0 年够到门槛，结论会整个翻号。
    """
    payload = {"yearly": [
        {"factor": "rev5", "year": str(y), "n_days": 200,
         "ic_mean": 0.03, "hit": 0.6}          # ← 没有 tail_low
        for y in range(2019, 2027)
    ]}
    p = tmp_path / "no_tail.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    r = _run(p, "--factor", "rev5")
    assert r.returncode != 0, "缺 low_tail 档必须失败退出"
    assert "tail_low" in (r.stdout + r.stderr), (
        "报错要说清缺的是哪一档，否则读者不知道发生了什么"
    )


def test_the_real_old_report_is_still_refused():
    """**真实的老报告**（2026-10-12 生成）必须被拒绝。

    这条不依赖临时文件——它钉的是**已发布的报告**仍然缺档这件事。
    一旦重跑生成了三档，它会转绿，那是**预期**的。
    """
    p = Path("data/reports/factor_ic_7y.json")
    if not p.is_file():
        pytest.skip("报告不在（data/ 被 gitignore）")
    payload = json.loads(p.read_text(encoding="utf-8"))
    rows = payload.get("yearly") or []
    if not rows:
        pytest.skip("报告没有 yearly 段")
    if "tail_low" in rows[0]:
        pytest.skip("报告已重跑，三档齐全——本测试的前提已消失")

    r = _run(p, "--factor", "rev5")
    assert r.returncode != 0


# ── 二、判决线的唯一真相源是 σ_r ──────────────────────────────────

def test_the_decision_threshold_is_derived_from_the_measured_sigma_r():
    """判决线**不许硬编码**——它必须由 σ_r 算出来。

    σ_r 从 0.023 改成实测 0.02656 后，所需 IC 从 0.0447 降到 0.0417。
    若脚本里写死 0.0417，那 σ_r 的修正在判决里就**不起作用**，
    而报告里看不出任何区别——**又一次静默失效**。

    > 只查**可执行代码**，不查 docstring：模块顶部**故意**写了
    > 「预注册值 = 0.04167」来说明来源。
    > 文档必须能说出那个数，所以检查范围排除注释与文档字符串。
    """
    import ast

    from aplus.hurdle import DAYS_PER_YEAR, hurdle_rate, rounds_per_year
    from aplus.ic_to_return import required_ic

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    # 收集所有**被引用**的字面量节点（赋值右侧、比较、参数……）
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert any(abs(v - 0.04167) < 1e-9 for v in literals) is False, (
        f"判决线被写死成字面量 {[v for v in literals if 0.04 < v < 0.05]}——"
        "σ_r 一改它就该自动跟着变"
    )

    # 且它算出来的数确实等于预注册的那个
    need = hurdle_rate(rounds_per_year=rounds_per_year(21))
    got = required_ic(need / DAYS_PER_YEAR, sigma_r=0.02656)
    assert got == pytest.approx(0.04167, abs=5e-5), (
        f"判决线 {got:.5f} 与预注册的 0.04167 不符——"
        "若是有意改动，预注册必须同步更新，否则就是在事后调标准"
    )


# ── 三、三档齐全时判决能跑完，并给出可核对的数字 ──────────────────

def test_a_complete_report_produces_a_per_year_verdict(tmp_path):
    """三档齐全 → 逐年列出、逐条对照，**不出现任何模糊表述**。

    特意让 2023 年不过线：判决必须是「有几年不过」，
    而不是「大概差一点」——后者正是第 63 条的形式。
    """
    years = {str(y): 0.048 if y != 2023 else 0.030 for y in range(2019, 2027)}
    payload = {"yearly": [
        {"factor": "rev5", "year": y, "n_days": 240,
         "all": 0.026, "tail_low": ic, "tail_high": -0.02,
         "tail_low_hit": 0.6, "tail_high_hit": 0.4}
        for y, ic in years.items()
    ]}
    p = tmp_path / "full.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    r = _run(p, "--factor", "rev5")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "1 / 8" in out, f"应报出 1 个不过线年份，实际输出：\n{out}"
    assert "2023" in out and "❌" in out, "不过线的那年必须被点名"
    # 门槛数字必须打出来，读者才能自己核对
    assert "26.4" in out or "26.3" in out, f"门槛必须打印：\n{out}"


def test_every_year_passing_is_reported_as_such(tmp_path):
    """**反向**：全部过年时要直说，且必须**同时说明这不是充分条件**。

    「全部过关」是最容易被当成结论的读数——
    而它只过了**信号侧**这一关，成本假设与实盘滑点还没进账。
    """
    payload = {"yearly": [
        {"factor": "rev5", "year": str(y), "n_days": 240,
         "all": 0.026, "tail_low": 0.050, "tail_high": -0.02}
        for y in range(2019, 2027)
    ]}
    p = tmp_path / "allpass.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    r = _run(p, "--factor", "rev5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 / 8" in r.stdout
    assert "不是充分条件" in r.stdout, (
        "全过年时必须声明「这不是充分条件」——"
        "否则「每个单独年份都够得着」会被当成「月频可行」"
    )
