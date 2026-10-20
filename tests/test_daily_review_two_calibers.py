"""夜间复盘必须**两个口径并排**报「离 20% 差多少」。

## 这条守卫在守什么

`scripts/daily_review.py` 是用户**每天早上真会读**的那一份（21:40 cron 落盘）。
它原先只报**理想档**（台账的公式上界：假设 **100% 部署** ⇒ **不含**整手与
闲置现金的代价）⇒ 用户看到的缺口**偏乐观**。

2026-10-21 实测的两个口径：

| 口径 | 缺口 | 载体 |
|---|---|---|
| **理想档**（公式上界） | 7.3800pp | `d2_signal_requirement.json::headline::account_annual_ceiling_pct::…50k_pp` |
| **实盘档**（含整手与闲置现金） | 9.5755pp | `deployed_d2_verdict.json::verdict::gap_to_target_pp` |

⇒ **两个都要报，并标明是哪个** ⇒ 只报理想档就是**骗用户**。

⚠️ 这条守卫**不判断哪个数更好** ⇒ 它只保证
① 两档都在；② 实盘档的值**逐位等于**产物字段（**只读**，不许重算）。
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "daily_review_two_calibers", ROOT / "scripts" / "daily_review.py")
DR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DR)

DEPLOYED = ROOT / "data" / "reports" / "deployed_d2_verdict.json"


def _d2():
    return DR.collect_d2()


def test_the_review_carries_both_calibers():
    """两档都在，且**各自带标签**（不许合成一个数）。"""
    d2 = _d2()
    assert "deliverable_50k" in d2, "理想档不在复盘里"
    assert "deployed_live" in d2, "实盘档不在复盘里"
    assert "理想档" in d2["deliverable_50k"]["what"], (
        f"理想档没标口径：{d2['deliverable_50k']['what']!r}")
    assert "实盘档" in d2["deployed_live"]["what"], (
        f"实盘档没标口径：{d2['deployed_live']['what']!r}")


def test_the_deployed_caliber_is_read_from_the_artifact_not_recomputed():
    """实盘档的值必须**逐位等于**产物字段（不许用公式重算「验证」）。"""
    assert DEPLOYED.exists(), f"实盘档产物不在盘上：{DEPLOYED.name}"
    art = json.loads(DEPLOYED.read_text(encoding="utf-8"))["verdict"]
    d2 = _d2()
    for key in ("cagr", "mde_annual", "gap_to_target_pp",
                "upper_one_sided_95", "measured_sign_determinable"):
        cell = d2["deployed_live"][key]
        assert cell["value"] == art[key], (
            f"实盘档的 `{key}` 与产物不一致：{cell['value']!r} ≠ {art[key]!r}")
        assert cell["at"], f"`{key}` 没有出处 ⇒ 读者无从核对"


def test_the_question_answers_name_both_calibers():
    """q2 是**用户直接读到的那一句** ⇒ 它必须同时说出两档。"""
    d2 = _d2()
    payload = {
        "generated_at": "1970-01-01T00:00:00",
        "review_date": "1970-01-01",
        "questions": {
            "q1_what_can_the_user_open_today": "x",
            "q2_how_far_from_20pct": (
                f"{d2['deliverable_50k']['gap_to_target_pp']['value']} pp"
                f"（理想档）｜{d2['deployed_live']['gap_to_target_pp']['value']} pp"
                f"（实盘档）"),
        },
        "d1": {}, "d2": d2, "commits": {}, "honest_boundaries": [],
        "vs_previous": None,
    }
    # `build()` 里拼 q2 的那段逻辑：两档都要在里面
    import inspect
    src = inspect.getsource(DR.build)
    assert "deployed_live" in src, (
        "build() 拼 q2 时**没有**用实盘档 ⇒ 屏上那句「离 20% 差多少」仍偏乐观")
    assert "deliverable_50k" in src, "build() 拼 q2 时没有理想档"
    assert "理想档" in src and "实盘档" in src, (
        "q2 的两档**没有标明是哪个口径** ⇒ 读者无从判断该信哪个")
    assert payload  # 形状检查（上面的断言才是判据）


def test_a_missing_deployed_artifact_says_so_instead_of_zero():
    """缺档必须**说清缺了**，绝不回落成 0（0 会被读成「缺口 0，已达标」）。"""
    d2 = _d2()
    dep = d2["deployed_live"]
    if dep["cagr"]["value"] is None:
        assert dep.get("missing_reason"), "缺档却没有说明原因"
        assert dep["gap_to_target_pp"]["value"] is None, (
            "缺档时缺口回落成了数 ⇒ 会被读成「缺口 0，已达标」")
