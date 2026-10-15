"""★ 报告的挑选规则：`.provenance.json` 边车**永远不是报告**。

## 这条守卫存在的理由（一次真实事故）

`aplus/factor_ic.py` 的 `_find_ic_report()` 用
`sorted(reports.glob("factor_ic*.json"))[-1]` 取「最新的报告」。
而 provenance 边车的文件名是 `主报告名 + ".provenance.json"` ——
它是主文件名的**超长前缀**，字典序必然排在主文件后面。

于是 `factor_ic_7y_v2.json.provenance.json` 把真正的
`factor_ic_7y_v2.json` 顶掉了。真报告有 `kind/window/factors/windows/
fwd_windows/tail_q/bonferroni_crit`，边车只有 4 个 key
（`code_sha/code_dirty/generator/stamped_at`）⇒ 取因子时 KeyError
⇒ 被 `except` 吞掉 ⇒ 返回 `unknown=True`。

**用户可见的后果**：概览页「符号已按实测 IC 校正」这句话当时**没有数据支撑**。
更糟的是它对外报出的报告名就是那个边车名，一眼就能看出数据不对，
但没人看。

## 为什么这类测试特别容易假绿

`tests/test_yearly_sign_claim.py` 里有 5 条测试守这个函数，它们当时**全绿**
—— 因为 `consistency()` 拿 `unknown` 就 `pytest.skip`。
**skip 不是通过。** 一个「数据不可得就跳过」的守卫，
恰好在最需要它的时候（数据在、只是选错了文件）集体退场。

⇒ 所以这里不测「函数返回了什么」，测**选择规则本身**：
在一个只放了主报告 + 边车的目录里，被选中的必须是有 `factors` 的那个。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


from aplus.factor_ic import (_find_ic_report, weight_sign_conflicts,
                             yearly_sign_consistency)
from aplus.provenance import SIDECAR_SUFFIX, report_jsons

#: 本守卫扫**全仓源码**，所以根是仓库根而不是 tests/。
ROOT = Path(__file__).resolve().parents[1]

# 够用的最小真报告：只需要 `_find_ic_report` 的下游能认出它是报告。
_REAL = {"kind": "factor_ic", "window": ["2018-01-01", "2025-12-31"],
         "factors": {}, "windows": {}, "fwd_windows": {}, "tail_q": {},
         "bonferroni_crit": 0.05}
_SIDECAR = {"code_sha": "abc123", "code_dirty": False,
            "generator": "aplus.factor_ic", "stamped_at": "2026-10-14T00:00:00Z"}


def _write_pair(d: Path, stem: str) -> None:
    """摆出一对「主报告 + 边车」，边车名**必然**字典序更大。"""
    (d / f"{stem}.json").write_text(json.dumps(_REAL), encoding="utf-8")
    (d / f"{stem}.json{SIDECAR_SUFFIX}").write_text(
        json.dumps(_SIDECAR), encoding="utf-8")


def test_a_sidecar_must_never_be_picked_as_the_report(tmp_path: Path):
    """★ 核心守卫：被挑中的必须是真报告，不是它的边车。"""
    _write_pair(tmp_path, "factor_ic_demo")
    got = _find_ic_report(tmp_path)
    assert got is not None, "目录里有报告却返回 None"
    assert got.name == "factor_ic_demo.json", (
        f"挑中的是 {got.name} —— 它是边车（{SIDECAR_SUFFIX}），不是报告。"
        "边车名以主报告名为前缀，字典序必然更大，"
        "所以任何 `sorted(glob(...))[-1]` 都必须先排除它。"
    )


def test_the_real_reports_directory_yields_a_usable_ic_report():
    """★ 真实数据上的端到端后果：不是「结构异常」，而是真的能核对符号。

    这条在事故发生时是**红**的：`_find_ic_report` 选中边车 ⇒
    `unknown=True`。
    """
    got = _find_ic_report("data/reports")
    assert got is not None, "data/reports 里找不到 factor_ic 报告"
    assert not got.name.endswith(SIDECAR_SUFFIX), f"选中的是边车 {got.name}"
    assert "factors" in json.loads(got.read_text(encoding="utf-8")), (
        f"{got.name} 里没有 factors —— 它不是一份可用的 IC 报告")


def test_sign_conflicts_are_not_silently_unknown_on_real_data():
    """★ 概览页那句「符号已按实测 IC 校正」必须真的有数据支撑。

    `unknown=True` 的含义是「没做核对」，而界面把它渲染成「已核对」。
    这两件事在事故期间是同一件事。
    """
    from aplus.evolve import load_weights
    w = load_weights(Path("data/reports"))["weights"]
    got = weight_sign_conflicts(Path("data/reports"), w)
    assert got.get("unknown") is not True, (
        f"符号核对被静默跳过：note={got.get('note')!r} "
        f"report={got.get('report')!r} —— "
        "界面此时显示「已按实测 IC 校正」，但其实一次都没比过")


def test_an_empty_directory_returns_none_and_does_not_fall_back(tmp_path: Path):
    """docstring 承诺「不回落」——空目录就该是 None，不是去别处找一个。

    ⚠️ 没有 `parametrize`：这条只有一个被测函数，单值参数化只是噪音，
    而且写错签名会变成 collection error（那不是 failed，是根本没跑）。
    """
    assert _find_ic_report(tmp_path) is None


# ── report_jsons() 本身：它是「排除边车」这条约定的**唯一实现** ──────────────
# 这三条是第 73 层的正面对策：约定散在四个调用点各写一遍 `if endswith`，
# 就会漂移成今天这样（factor_ic 漏了、webapp 漏了、champion_health 漏了）。
# ⇒ 约定收进 provenance.report_jsons()，守卫收在这里。

def test_report_jsons_drops_sidecars_for_every_pattern(tmp_path: Path):
    _write_pair(tmp_path, "race_alpha")
    _write_pair(tmp_path, "factor_ic_beta")
    got = {p.name for p in report_jsons(tmp_path, "race_*.json", "factor_ic*.json")}
    assert got == {"race_alpha.json", "factor_ic_beta.json"}, (
        f"边车没被排掉，实测返回：{sorted(got)}")


def test_report_jsons_defaults_to_all_json_but_still_drops_sidecars(tmp_path: Path):
    _write_pair(tmp_path, "gamma")
    (tmp_path / "plain.json").write_text("{}", encoding="utf-8")
    got = {p.name for p in report_jsons(tmp_path)}
    assert got == {"gamma.json", "plain.json"}, f"实测返回：{sorted(got)}"


def test_report_jsons_on_the_real_directory_returns_no_sidecar():
    """★ 真实目录上对照：裸 glob 会多出 5 份边车，report_jsons 一份都不给。"""
    from pathlib import Path as _P
    raw = [p for p in _P("data/reports").glob("*.json")]
    raw_sidecars = {p.name for p in raw if p.name.endswith(SIDECAR_SUFFIX)}
    got = {p.name for p in report_jsons("data/reports")}
    assert not (got & raw_sidecars), (
        f"report_jsons 仍返回了边车：{sorted(got & raw_sidecars)}")
    assert len(got) == len(raw) - len(raw_sidecars), (
        f"数量对不上：裸 glob {len(raw)}（含 {len(raw_sidecars)} 边车），"
        f"report_jsons {len(got)}")


# ══ 手拼 sidecar 路径：真名带 `.json`，写漏一段就是「静默没有边车」 ══
#
# 🚨 **本文件这条是我在同一个会话里当场踩的**：
# 查 `data/reports/factor_width_mde.provenance.json` ⇒ `No such file`，
# 而真名是 `factor_width_mde**.json**.provenance.json`。
#
# 与第 123 层同族（**查不到 ≠ 没有**），但更贵一层：
# `ls` 的失败输出看起来就像「这份产物没有 sidecar」
# ⇒ 那是一条关于**可引用性**的判决（按本仓规矩 `unknown` = 不许引用）。
#
# ⇒ 与其记在记忆里，不如让**全仓任何一处手拼**都过一次 `sidecar_path()`。

def _hand_written_sidecar_names(root: Path):
    """扫出**字面量里手拼**、且**少了 `主报告名 + ".json"`** 的边车文件名。

    ## 三种必须**放行**的正当形态

    | 形态 | 例子 | 为什么放行 |
    |---|---|---|
    | ① 常量自身 | `'.provenance.json'` | 它是 `SIDECAR_SUFFIX` 的**值**，不是文件名 |
    | ② 拼接 | `p.name + '.provenance.json'` | 源码里**没有**完整字面量，扫不到 |
    | ③ **glob 通配符** | `'*.provenance.json'` | **正确写法**：真名本身以它结尾，通配符匹配到的就是真名 |

    ⚠️ ③ 是**实测补的**：第一版只判 `stem.endswith(".json")` ⇒ 把
    `tests/test_provenance.py` 两处 `glob("*.provenance.json")` 报成缺陷
    ⇒ **过严守卫会把人推向「改本来正确的代码」**（第 112 层）。
    ⚠️ 而通配符**恰好**以 `stem` 不含 `.json` 的形态出现，所以判据必须是
    「**没有通配符字符**且 stem 不含 `.json`」**两个条件同时成立**才算缺陷。
    """
    bad = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or ".mutation_backup" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue                                   # 变异体：不是本守卫的职责
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            v = node.value
            if not v.endswith(SIDECAR_SUFFIX) or v == SIDECAR_SUFFIX:
                continue                                # ① 常量自身的值
            stem = v[: -len(SIDECAR_SUFFIX)]
            if any(ch in stem for ch in "*?["):
                continue                                # ③ glob 通配符（正确写法）
            if stem.endswith(".json"):
                continue                                # ② 完整的真名
            bad.append(f"{path.relative_to(root)}:{node.lineno}  {v!r}")
    return bad


def _sidecar_built_from_stem(root: Path):
    """扫出「**用 `.stem` 拼**边车名」的地方 —— 那是最真实的失效形态。

    🚨 **实测**：变异把 `report_path.name + ".provenance.json"`
    改成 `report_path.stem + ".provenance.json"`（`name` → `stem`），
    产物从 `report.json.provenance.json` 变成 `report.provenance.json`
    ⇒ **指向不存在的文件**，而 `with_name` 正常返回、`.exists()` 返 False、
    **不抛任何异常** ⇒ 静默「没有边车」。

    ⚠️ 这个形态**没有任何字面量可扫**（源码里只有 `".provenance.json"` 一段）
    ⇒ 必须扫 **AST 的 `+` 表达式**：左边是 `X.stem`，右边是以 `SIDECAR_SUFFIX`
    结尾的字符串。

    ⇒ 与字面量那条**互补**，不是重复：一条守「写死整个名字」，
    一条守「用 stem 拼名字」—— 两者的源码形态互不可见。"""
    bad = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or ".mutation_backup" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
                continue
            terms = [node.left, node.right]
            has_suffix = any(isinstance(x, ast.Constant) and isinstance(x.value, str)
                             and x.value.endswith(SIDECAR_SUFFIX) for x in terms)
            if not has_suffix:
                continue
            for x in terms:
                if isinstance(x, ast.Attribute) and x.attr == "stem":
                    bad.append(f"{path.relative_to(root)}:{node.lineno}  "
                               f"`{ast.unparse(x)} + …{SIDECAR_SUFFIX}`")
    return bad


def test_no_source_file_hand_writes_a_sidecar_path_that_is_missing_the_json_suffix():
    """真名是 `主报告名.json.provenance.json`，不是 `主报告名.provenance.json`。

    ⚠️ **判据是「字面量」，不是子串**（第 13 种形状）：
    源码里到处都有 `".provenance.json"` 这段（拼接、`endswith`、docstring），
    全文扫会命中上百处**正当**写法。真正危险的只有**整个文件名写死**的那一种。
    ⇒ 判据条件化在「字面量恰好等于一个 `*.provenance.json` 且 `*` 不含 `.json`」。"""
    offenders = _hand_written_sidecar_names(ROOT)
    assert not offenders, (
        "这些字面量指向的边车文件**不存在**（真名多一段 `.json`）：\n"
        + "\n".join(offenders)
        + "\n⇒ 读它会静默返回「没有边车」，而那是一条关于可引用性的判决"
          "（本仓规矩 `unknown` = 不许引用）。改用 `provenance.sidecar_path()`。"
    )
    stems = _sidecar_built_from_stem(ROOT)
    assert not stems, (
        "这些地方用 `.stem` 拼边车名 ⇒ 得到 `主报告名.provenance.json`\n"
        "（少了 `.json` 那一段）⇒ **指向不存在的文件**，"
        "而 `with_name` 不报错、`.exists()` 静默返 False：\n"
        + "\n".join(stems)
        + "\n⇒ 要拼也得用 `.name`（`provenance.sidecar_path()` 更省事）。"
    )


def test_the_stem_detector_sees_a_stem_built_sidecar(tmp_path):
    """正反控：`.stem + …` 必被抓，`.name + …` 必须放过。"""
    (tmp_path / "bad.py").write_text(
        "sc = report_path.with_name(report_path.stem + '.provenance.json')\n",
        encoding="utf-8")
    (tmp_path / "ok.py").write_text(
        "sc = report_path.with_name(report_path.name + '.provenance.json')\n",
        encoding="utf-8")
    (tmp_path / "ok2.py").write_text(
        "sc = report_path.with_name(report_path.name + '.other.json')\n",
        encoding="utf-8")
    bad = _sidecar_built_from_stem(tmp_path)
    assert len(bad) == 1 and "bad.py" in bad[0], bad


def test_that_detector_really_sees_a_hand_written_name(tmp_path):
    """★ 探测器的正反控（第 131 层：只验探测器 = 假信用）。

    正例 = 手拼漏 `.json`；反例 = 两种正当形态（拼接 / 带 `.json`）。
    ⚠️ 若探测器收窄到永远返回空，上面那条主守卫会**恒绿**。"""
    # 正例：手拼漏了 `主报告名 + ".json"` 那一段 ⇒ 指向不存在的文件
    (tmp_path / "bad.py").write_text(
        "SIDECAR = 'report.provenance.json'\n", encoding="utf-8")
    (tmp_path / "bad2.py").write_text(
        "SC = 'factor_width_mde.provenance.json'\n", encoding="utf-8")
    # 反例 ①：拼接（源码里没有完整字面量，扫不到）
    (tmp_path / "ok_concat.py").write_text(
        "SIDECAR = p.name + '.provenance.json'\n", encoding="utf-8")
    # 反例 ②：glob 通配符 —— **正确写法**，第一版没收窄到它而误报
    (tmp_path / "ok_glob.py").write_text(
        "REPORTS = sorted(root.glob('*.provenance.json'))\n", encoding="utf-8")
    # 反例 ③：常量自身的值
    (tmp_path / "ok_const.py").write_text(
        "SUFFIX = '.provenance.json'\n", encoding="utf-8")
    bad = _hand_written_sidecar_names(tmp_path)
    assert sorted(b.split(":")[0].split("/")[-1] for b in bad) == \
        ["bad.py", "bad2.py"], bad
