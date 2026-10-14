"""合成判决脚本必须机械执行预注册判据，且门槛**不许硬编码**。

与 `tests/test_verdict_monthly_hurdle.py` 是同一套纪律的第二次应用。
第一处：预注册的是单因子月频路线（已否）。
第二处：预注册的是合成路线——**在抽取任何一天之前**就写进了
`docs/TODO_measure_sigma_r.md`。

预注册全部的意义：结论不能随结果漂移。若判决逻辑临场判断，
「把标准调到刚好能过」就会发生——而**支持既有目标的数最不该被质疑**
（第 63 条）。

## 两条纪律

1. **门槛由 σ_r 推导**：写成字面量的话，σ_r 的修正在判决里就不起作用，
   而输出里看不出任何区别——又一次静默失效。
2. **判决年不许包含等权年**：2019/2020 只有等权、没有历史，
   拿它们判决等于承认「你事先就知道它们的 IC」。
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/verdict_composite.py")
SMOKE = Path("/tmp/icmat_smoke.parquet")


def _run(*extra):
    return subprocess.run([sys.executable, str(SCRIPT), *extra],
                          capture_output=True, text=True)


# ── 一、门槛的唯一真相源是 σ_r ──────────────────────────────────

def test_the_threshold_is_derived_not_hardcoded():
    """判决线**不许硬编码**——它必须由 σ_r 算出来。

    σ_r 从 0.023 改成实测 0.02656 后，所需 IC 从 0.0447 降到 0.0417。
    写死 0.0417 的话，σ_r 的修正在判决里**不起作用**。

    > 只查**可执行代码**，不查 docstring / 注释——文档必须能说出
    > 那个数来说明来源，检查范围要排除它们。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    hard = [v for v in literals if 0.04 < v < 0.05]
    assert not hard, (
        f"判决线被写死成字面量 {hard}——σ_r 一改它就该自动跟着变"
    )


def test_the_derived_threshold_equals_the_pre_registered_one():
    """推导出来的数必须**等于**预注册写下的 0.04167。

    对不上说明 σ_r 或成本假设被动过——预注册必须同步更新，
    否则就是事后改标准。
    """
    from aplus.hurdle import DAYS_PER_YEAR, hurdle_rate, rounds_per_year
    from aplus.ic_to_return import required_ic

    need = hurdle_rate(rounds_per_year=rounds_per_year(21))
    got = required_ic(need / DAYS_PER_YEAR, sigma_r=0.02656)
    assert got == pytest.approx(0.04167, abs=5e-5)


def test_equal_weight_years_are_excluded_from_the_judgement_window():
    """判决起始年必须晚于等权年——只报不判。"""
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, int)}
    assert 2021 in consts, "判决起始年 2021 必须显式写出"
    # EQUAL_WEIGHT_YEARS 里不能出现比 JUDGED_FROM 大的年份
    judged_from = 2021
    for line in src.splitlines():
        if line.startswith("EQUAL_WEIGHT_YEARS"):
            years = {int(y) for y in line.split("(")[1].split(")")[0].split(",")}
            assert max(years) < judged_from, (
                f"等权年 {sorted(years)} 里有 >= 判决起始年 {judged_from} 的——"
                "那等于「无历史可用」的年份也参与判决"
            )


# ── 二、脚本必须能跑完，并给出逐年数字 ──────────────────────────

@pytest.mark.skipif(not SMOKE.is_file(), reason="冒烟抽取表不在")
def test_a_table_with_no_judged_years_is_refused_not_declared_passing():
    """只有 2026 一年 → **没有任何判决年份** → 必须拒绝退出。

    这条是**反向**测试，比端到端那条更重要：
    若脚本在这种输入下照常打印「不及格年份：0 / 0」，
    读者会把它读成「全部过关」——而真实含义是「根本没得判」。
    零判决年必须与零失败年区分开。
    """
    r = _run("--matrices", str(SMOKE))
    assert r.returncode != 0
    assert "没有任何判决年份" in (r.stdout + r.stderr)


def _synthetic_table(tmp_path, years=range(2019, 2026), k=4, seed=3):
    """造一份与抽取脚本同列名的多年表（不依赖真实数据）。"""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    recs = []
    for y in years:
        for _ in range(90):
            A = rng.normal(size=(500, k))
            C = np.full((k, k), 0.3)
            np.fill_diagonal(C, 1.0)
            F = A @ np.linalg.cholesky(C).T
            beta = np.array([(-1.0) ** i for i in range(k)])
            ret = F @ beta + rng.normal(scale=1.5, size=500)
            rec = {"date": f"{y}-{rng.integers(1, 13):02d}-"
                          f"{rng.integers(1, 28):02d}"}
            for j in range(k):
                rec[f"sf__{j}"] = float(np.corrcoef(F[:, j], ret)[0, 1])
            for a in range(k):
                for b in range(k):
                    rec[f"R__{a}{b}"] = float(C[a, b])
            recs.append(rec)
    p = tmp_path / "mat.parquet"
    pd.DataFrame(recs).to_parquet(p, index=False)
    return p


def test_the_script_runs_and_prints_a_per_year_verdict(tmp_path):
    """端到端跑通：门槛打印、逐年列出、权重口径说清、判决分母正确。

    判决分母必须是**判决年数**（2019/2020 只报不判），
    而不是抽取表里出现的所有年份。
    """
    p = _synthetic_table(tmp_path)
    r = _run("--matrices", str(p), "--factors", "0,1,2,3")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "过关所需组合 IC" in out
    assert "判决年份：5 个（2021 ~ 2025）" in out, (
        f"判决分母必须是判决年（表里有 7 年，含 2019/2020 等权年），"
        f"不是抽取到的所有年：\n{out}"
    )
    assert "滚动扩展窗" in out and "等权" in out
    assert "样本外 vs 单因子" in out, f"第二问必须一起答：\n{out}"


def test_a_missing_matrix_table_fails_loudly():
    """抽取表不存在必须**报错退出**并指出先跑哪个脚本——
    不许 print 一个空判决让人以为「没有不过线的年份」。
    """
    r = _run("--matrices", "/tmp/绝对不存在.parquet")
    assert r.returncode != 0
    assert "extract_ic_matrices" in (r.stdout + r.stderr), (
        "报错要指路——让人知道该跑哪个脚本，而不是只说文件不存在"
    )