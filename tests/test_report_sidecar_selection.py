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

import json
from pathlib import Path


from aplus.factor_ic import (_find_ic_report, weight_sign_conflicts,
                             yearly_sign_consistency)
from aplus.provenance import SIDECAR_SUFFIX, report_jsons

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
