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
import tempfile
from pathlib import Path

import pytest

from aplus.composite import r_column
from tests.icmat_synthetic import extract_table

SCRIPT = Path("scripts/verdict_composite.py")


def _run(*extra):
    """跑判决脚本，`--out` 由**本函数**强制注入，调用点不许自己传。

    ⚠️★ 第 132 层：`--out` 的默认值是**生产产物路径**
    `data/reports/verdict_composite.json`（`verdict_composite.py:89`），
    而下面这些测试喂的是**合成 parquet**（`_synthetic_table`）。
    2026-10-15 实测：`tests/test_verdict_composite.py`
    的 `test_the_control_arm_is_restricted_to_the_same_years_as_the_main_arm`
    不传 `--out` ⇒ 每次跑门禁都把生产产物覆盖成
    「4 个随机因子、years=2026..2028、90 天/年」的数据，
    然后 `provenance.stamp()` **给它盖上真章**——
    章的 `data_fp` 取自固定的 `data/master/daily`，与 `--matrices` 无关
    ⇒ `freshness()` 判 **fresh + 字节证据**。
    ⇒ 实测后果：`data/reports/verdict_composite.json` 及其 sidecar
      **被污染的版本已经进了 git**（`8e4b327`），
      `config.matrices` 字面写着
      `/private/var/folders/…/pytest-of-airboom/pytest-2330/…/mat.parquet`，
      而 CLAUDE.md 第 127 层那组数字（6 年 / +0.005011 / 5-of-6）
      在当前仓库里**没有任何文件能复现**。

    🚨 **不许改成「在每个调用点记得传 `--out`」**——
      那是第 124 层骂过的「绕过手段是把判据挂在人的记忆上」：
      下一个新加的测试就会漏，而且是静默地把生产结论换掉。
    🚨 也**不许**用「临时目录里没有 sidecar」来解释章为什么可信——
      `stamp()` 是照章不误地盖的，问题不在它，它只是**照实记了错的输入**。
    ⇒ 所以注入点在 `_run` 内部，并且调用点传 `--out` 会**直接报错**，
      让「有人想绕过」变成会响的事。
    """
    if any(a == "--out" or a.startswith("--out=") for a in extra):
        raise AssertionError(
            "`_run` 已经把 --out 钉在临时目录上；调用点不许自己传。\n"
            "（这条不是为了洁癖：脚本的 --out 默认值是生产产物路径，"
            "而这里的输入是合成 parquet —— 见本函数 docstring 的第 132 层。）"
        )
    with tempfile.TemporaryDirectory() as td:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra,
             "--out", str(Path(td) / "verdict.json")],
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
    """推导出来的数必须**等于**预注册写下的 **0.01389**。

    对不上说明 σ_r 或成本假设被动过——预注册必须同步更新，
    否则就是事后改标准。

    ## 🚨 门槛 2026-10-14 从 0.04167 改成 0.01389，**而且是往松的方向**

    这是本项目最需要自证清白的一次改动，所以把账摊开：

    | 环节 | 改前 | 改后 | 动了吗 |
    |---|---|---|---|
    | σ_r 实测 | 0.02656 | 0.02656 | **没动** |
    | 成本 `COST_REAL_50K` | 0.0055 | 0.0055 | **没动** |
    | K / 调仓次数 | 21 / 11.6 | 21 / 11.6 | **没动** |
    | 目标 | 20% | 20% | **没动** |
    | 判决年份 | 2021~2026 | 2021~2026 | **没动** |
    | **K(q) 常数** | **0.9332** | **2.7996** | **改了 —— 这里是 bug** |

    两个外部锚 σ_r 与成本都**原封不动**；
    变的只有「IC 怎么翻译成分位价差」这一步，而那一步的公式
    **分母写错了，差整 3 倍**（`2φ(z_q)/(1−2q)` 应为 `2φ(z_q)/q`）。

    证据有两条，且**互相独立**：
    1. 五个 q 值上实测价差 / 旧常数精确等于 `(1−2q)/q`；
    2. 旧常数在 `q→0.5` 处**发散**，而那里真值 1.5958 显然有限。

    > **方向朝松，所以更要说清楚**：
    > 如果我只是想让判决更好看而调松了门槛，
    > 这条测试存在的全部意义就是让它**当场红**——
    > 数字一旦对不上，必须能立刻指出「是 σ_r/成本被动过」还是
    > 「是换算常数修对了」。**两者都红，但只有后者是对的**，
    > 所以这条测试必须把三个锚（σ_r、成本、K）都单独钉住。
    """
    from aplus.hurdle import COST_REAL_50K, DAYS_PER_YEAR, hurdle_rate, rounds_per_year
    from aplus.ic_to_return import required_ic

    # 三个外部锚**逐一钉死**——它们没动，动的是别的地方
    assert COST_REAL_50K == 0.0055
    assert rounds_per_year(21) == pytest.approx(244 / 21)

    need = hurdle_rate(rounds_per_year=rounds_per_year(21))
    got = required_ic(need / DAYS_PER_YEAR, sigma_r=0.02656)
    assert got == pytest.approx(0.013890, abs=5e-5)

    # 反向核对：门槛 IC 代回去，应当**恰好**还原出年毛超额门槛。
    # 这一条让「改松了门槛」不可能悄悄发生——
    # 门槛 IC 与收益门槛必须**互为反函数**。
    from aplus.ic_to_return import annualise, implied_spread
    assert annualise(implied_spread(got, sigma_r=0.02656,
                                    kind="spearman")) == pytest.approx(need, rel=1e-9)


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

def test_a_table_with_no_judged_years_is_refused_not_declared_passing(tmp_path):
    """只有 2026 一年 → **没有任何判决年份** → 必须拒绝退出。

    这条是**反向**测试，比端到端那条更重要：
    若脚本在这种输入下照常打印「不及格年份：0 / 0」，
    读者会把它读成「全部过关」——而真实含义是「根本没得判」。
    零判决年必须与零失败年区分开。
    """
    # 表**当场造**，不靠 `/tmp` 里的遗留文件。
    # 那张遗留表有三重问题：文件不在就**静默 skip**（于是「测试通过」
    # 可能等于「什么都没测」）、它**不受列名格式变更约束**
    # （抽取脚本改了格式它还在，于是测试仍在测旧格式）、
    # 以及它**来自别的会话**而没人知道它是否还兼容。
    smoke = tmp_path / "only_2026.parquet"
    extract_table(start="2026-06-01").to_parquet(smoke, index=False)

    r = _run("--matrices", str(smoke), "--factors", "f1,f2,f3")
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
                    rec[r_column(str(a), str(b))] = float(C[a, b])
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

def test_the_control_arm_is_restricted_to_the_same_years_as_the_main_arm(tmp_path):
    """两臂**必须**在同一年份集合上比较。

    等权臂不需要历史，所以它能判主臂「无历史而不判」的年份——
    若放任它多判，比较就成了「不同的年份集合」对打，结论没有意义。

    > 这条是被真实的 `KeyError` 逼出来的：抽取表只有 2026 一年时，
    > 等权臂判 2026，主臂说「无历史」不判，脚本在 `wf_by_year[y]` 上崩。
    >
    > **要构造出这个情形，数据起点必须晚于判决起始年**——
    > 若数据从 2019 起，主臂在 2021+ 永远有历史，测不出来。
    """
    p = _synthetic_table(tmp_path, years=range(2026, 2029))
    r = _run("--matrices", str(p), "--factors", "0,1,2,3")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "等权臂多出的" in out, (
        f"等权臂多判的年份必须**显式报出**并排除，不能静默混入：\n{out}"
    )
    assert "两臂年份集合必须一致" in out
    assert "2026" in out
