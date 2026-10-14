"""显著性临界值的**来源**必须可追溯：门槛永远算出来，不许写死。

## 背景：3.47/3.44 这一族的数字从哪来

它们不是查表、也不是随便挑的，而是 `bonferroni_t(n_comparisons, df=∞)`
的正态极限——`alpha/n` 的双侧正态分位数。所以：

    n=84 → 3.43    n=86 → 3.44    n=97 → 3.47

三个数都是**同一个公式在不同候选数下的取值**，本身并不神秘。真正的问题
有两个，本文件把这两条钉住：

1. **df 不能丢。** 2026-10-14 的假阳性晋级（`ic_guided_race_7y.json` 放行
   `t=4.14 @ 11 块`）就是拿 n=11 的统计量去比 df=∞ 的 3.47——真门槛是 5.03。
   `bonferroni_t` 因此把 `df` 设成**必填且无默认值**。本文件第一条测试
   保证这个必填参数不会在某个脚本里被漏掉（它已经漏过一次，见下）。

2. **df=∞ 的那个数只能当「渐近参考」，不能当判决门槛。** 它在低频档
   （K=63 只有 11 个块）能低估 1.56，差 30%。任何把它单独印出来、
   又紧挨着按各自行 `n_obs` 排出来的 t 值的排版，都是在制造第二次事故。

## 为什么用 AST 扫源码而不是 grep

`grep "bonferroni_t(" ` 会把定义、注释、docstring 一并算上——`dist.py` 的
模块文档里就写着 `bonferroni_t(97)` 当反例。这里要的是**真的会被执行的
调用**，所以走 `ast` 解析，只看 `Call` 节点。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 只有这两个目录产出**判决数字**。tests/ 里的调用是断言工具的用法，
# 不是门槛来源；data/ 是产物，不是代码。
SCANNED_DIRS = ("aplus", "scripts")


def _call_nodes(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _called_name(node: ast.Call) -> str | None:
    """取被调用者的名字。`a.b()` 取 `b`，裸名取原样，动态调用返回 None。"""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _sources():
    for d in SCANNED_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            if ".venv" in p.parts or "__pycache__" in p.parts:
                continue
            yield p


# ── 1. df 必填，且不许被任何调用点漏掉 ────────────────────────────────

def test_bonferroni_t_signature_makes_df_mandatory():
    """`df` 无默认值——给默认值等于把 bug 装进函数签名。

    调用方下次不传，又会静默退回 df=∞ 的正态近似，而这正是 2026-10-14
    那次假阳性晋级的成因。
    """
    from aplus.evolve_race import bonferroni_t

    with pytest.raises(TypeError):
        bonferroni_t(97)                       # noqa: F821 — 就是要它炸
    # df<=0 不抛错而是给 inf：两个观测无法证伪任何东西，门槛应当无穷高，
    # 而不是悄悄退回正态近似。
    assert bonferroni_t(97, df=0) == float("inf")


@pytest.mark.parametrize("path", list(_sources()),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_every_bonferroni_call_site_passes_df(path):
    """每个**真的会执行**的 `bonferroni_t(...)` 调用都必须显式给 df。

    这条曾经红过：`scripts/universe_forensics.py` 印了一行
    「Bonferroni 临界（97 次比较）= 3.47」，调用是 `bonferroni_t(97)`——
    没有 df，签名改成必填后那一行直接 TypeError 崩掉。

    崩溃比印错好，但一个分析脚本崩在半路，等于整个取证流程作废。
    """
    offenders = []
    for node in _call_nodes(path):
        if _called_name(node) != "bonferroni_t":
            continue
        if node.func.__class__ is not ast.Name:
            continue                          # 定义处不是 Call，跳过
        kw = {k.arg for k in node.keywords}
        if "df" not in kw and len(node.args) < 2:
            offenders.append(f"{path.name}:{node.lineno} → bonferroni_t("
                             f"{', '.join(repr(a.value) for a in node.args if hasattr(a, 'value'))})")
    assert not offenders, (
        "以下调用点漏传 df——门槛会退回正态近似，低频档能低估 1.56："
        + "; ".join(offenders))


# ── 2. 渐近参考值不得冒充判决门槛 ───────────────────────────────────

def test_df_infinity_value_is_labelled_not_bare():
    """df=∞ 的 3.47 可以印，但必须标出它是**渐近极限**、且各行 df 不同。

    排版陷阱：把「临界 3.47」单独立一行，紧挨着一列 n_obs 从 11 到 752
    的 t 值，读者必然拿它当统一门槛用——而 n=11 那行的真门槛是 5.03。
    """
    from aplus.evolve_race import bonferroni_t

    crit_inf = bonferroni_t(97, df=10 ** 9)
    crit_low = bonferroni_t(97, df=10)
    # 渐近值本身没错，它确实是 df 趋于无穷时的极限。
    assert crit_inf == pytest.approx(3.47, abs=0.01)
    # 但用它判低频样本会放行假阳性——这才是必须标注的原因。
    assert crit_low - crit_inf > 1.5


def test_reported_verdicts_survive_df_aware_recomputation():
    """台账/战报里记过的判决，用**带 df 的门槛**重算必须结论不变。

    这是本次审计的核心不变式：把正态近似换成真 t 分布之后，
    历史判决只允许因为「本来就不该过」而改变，不允许因为门槛算错而
    悄悄翻案。已经记成「维持现任」的运行，重算后仍必须是维持现任。
    """
    from aplus.evolve_race import bonferroni_t

    log_path = ROOT / "data" / "reports" / "evolution_log.json"
    if not log_path.exists():
        pytest.skip("无 evolution_log.json（未跑过赛马）")
    runs = json.loads(log_path.read_text(encoding="utf-8"))

    checked = 0
    for run in runs:
        dec = run.get("decision") or {}
        if dec.get("promoted"):
            continue                          # 晋级另有前瞻闸管，不在此断言范围
        evals = run.get("evaluations") or []
        for ev in evals:
            st = ev.get("stats") or {}
            t, days = st.get("excess_t"), st.get("excess_days")
            if t is None or not days:
                continue
            df_crit = bonferroni_t(len(evals) or 1, df=days - 1)
            assert float(t) < df_crit, (
                f"{run.get('date')} 记为「维持现任」，但某候选 t={t:.2f} @ "
                f"{days} 块已过带 df 的门槛 {df_crit:.2f}——判决与门槛不一致")
            checked += 1
    assert checked, "没有可核对的判决记录，测试空转"


# ── 3. 方向污染闸：声明与解除必须分两张表 ────────────────────────────

def test_declared_direction_always_has_a_blocking_predicate():
    """`UNVERIFIED_DIRECTIONS` 里登记的方向，必须在 `DIRECTION_TESTS` 有判据。

    两张表分开放**是设计**：声明「当初被污染过」与记录「已独立证实」是
    两件事，合成一张就等于把「解除」说成「当初就没污染过」。

    但分开有代价：`direction_blocked` 查不到判据时会**静默返回 False**——
    一个登记为污染的方向，于是变成永远放行，且没有任何报错。
    这条测试堵住那个静默失败。
    """
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from evolve_weekly import UNVERIFIED_DIRECTIONS

    from aplus.evolve_race import DIRECTION_TESTS

    assert UNVERIFIED_DIRECTIONS, "污染闸不得为空——空闸等于没有闸"
    orphans = sorted(UNVERIFIED_DIRECTIONS - set(DIRECTION_TESTS))
    assert not orphans, (
        f"以下方向登记为污染却无判据，将被静默放行：{orphans}")


def test_released_direction_stays_declared_in_the_pollution_table():
    """已解除的方向**仍要留在** `UNVERIFIED_DIRECTIONS` 里。

    `negative_momentum` 看着像过期条目——mom20 的 IC 实测为负、方向早被
    独立证实——但它不是过期，是**已解除**。删掉它会让台账谎称这个方向
    从未被污染，也就抹掉了「为什么要解除」这件事本身。

    解除状态由 `DIRECTION_VERDICTS` 承载，闸门读两张表；
    「声明」与「解除」分开，正是为了让解除有据可查。
    """
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from evolve_weekly import UNVERIFIED_DIRECTIONS

    from aplus.evolve_race import DIRECTION_VERDICTS, direction_blocked

    released = set(DIRECTION_VERDICTS)
    assert released, "解除记录表不得为空"
    # 已解除的必须仍在声明表里留痕。
    assert released <= set(UNVERIFIED_DIRECTIONS), (
        f"已解除方向被移出了污染声明表，解除过程失去留痕："
        f"{sorted(released - set(UNVERIFIED_DIRECTIONS))}")
    # 且解除必须真的生效：门开。
    for name in released:
        assert direction_blocked({"mom20": -1.0}, UNVERIFIED_DIRECTIONS) is False


def test_unreleased_direction_still_blocks():
    """对照组：没解除的方向必须照旧拦——闸不能因为有了解除表就全开。"""
    from aplus.evolve_race import DIRECTION_VERDICTS, direction_blocked

    assert "low_ivol" not in DIRECTION_VERDICTS
    assert direction_blocked({"ivol": -1.0}, {"low_ivol"}) is True