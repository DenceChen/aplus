"""`/api/d2-verdict` 必须**逐字**透传判决产物（2026-10-20）。

## 它是什么

年化 20% 的**判决档**判决（`market_return + net_excess_bt003`），
真身是 `data/reports/basket_nav_01_2026-10-16.json`。

## 为什么端点不许**算**

产物里**已经有**三样现成的东西：
`verdict_reading`（点估计 c）、`verdict_mde_annual`（带宽半宽 M）、
`verdict_upper_one_sided_95`（**真的** 95% 单侧上界 U）、
`verdict_cases_fired`（**真的**触发了哪几条规则）。

⇒ 端点里再算一遍 `c − M` / `c + M` / CI 就是**第二份实现**：
公式抄错时界面照常渲染、HTTP 200、零告警，而它说的是一件与产物不同的事
（第 137 层：两份必须同步的东西中间没有锁）。
⇒ 端点只负责**把产物自带的值递出去**。

## 三条守卫

| 守卫 | 问什么 | 变异能杀 |
|---|---|---|
| 数字逐字 | 响应里**每一个数**都逐字取自产物 | 端点里算一个 `c − M` 出来 |
| 契约键集 | 响应只含**判决档这一族**的字段 | 顺手把 5 万交付档那一族也递出去 |
| 404 | 产物不在 ⇒ 404 + 一句话原因，**不是空对象** | 把 404 改成返回 `{}` |

## 两条线不许混

| 端点 | 它给的是 | 口径 |
|---|---|---|
| `/api/d2-verdict` | **判决档**（1395 天 / 5.717 年 / 宽度 10 / K=21） | 本文件守的就是它 |
| `/api/d2-gap` | **5 万本金 + 交付档宽度 10** 的缺口台账 | **另一个量**，不可混引 |

⇒ 本端点**刻意不递** `gap_to_target_by_benchmark`（5 万交付档那一族）
与 `excess_legs_own_mde`（超额腿那一族）——递了就是邀请前端混用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.webapp import create_app  # noqa: E402

ARTIFACT_NAME = "basket_nav_01_2026-10-16.json"

#: 契约键集：前端按这些名字写。**判决档这一族**，不含 5 万交付档那一族。
CONTRACT_KEYS = {
    "source",
    "verdict_reading", "verdict_mde_annual", "verdict_upper_one_sided_95",
    "target", "verdict_rule", "verdict_text", "verdict_cases_fired",
    "verdict_tier_arith_ann", "verdict_tier_cagr", "verdict_tier_label",
    "verdict_tier_n_days", "verdict_tier_years",
    "caliber",
}


def _all_numbers(obj):
    """递归收集对象里**所有**数字（int/float，排除 bool）。"""
    out = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return {float(obj)}
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _all_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _all_numbers(v)
    return out


@pytest.fixture
def artifact(tmp_path):
    """把**真产物**复制进一个临时 data root（不改生产目录）。"""
    src = _ROOT / "data" / "reports" / ARTIFACT_NAME
    assert src.exists(), f"真产物不在：{src} —— 守卫的前提失效"
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / ARTIFACT_NAME).write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    return json.loads(src.read_text(encoding="utf-8"))


def test_the_endpoint_passes_every_verdict_field_through_verbatim(tmp_path, artifact):
    """★ 契约里的每个字段都与产物**逐字相同**（不是重算出来的）。"""
    body = TestClient(create_app(tmp_path, llm=object())).get("/api/d2-verdict").json()
    assert body["verdict_reading"] == artifact["verdict_reading"]
    assert body["verdict_mde_annual"] == artifact["verdict_mde_annual"]
    assert body["verdict_upper_one_sided_95"] == artifact["verdict_upper_one_sided_95"]
    assert body["target"] == artifact["target"]
    assert body["verdict_rule"] == artifact["verdict_rule"]
    assert body["verdict_text"] == artifact["verdict_text"]
    assert body["verdict_cases_fired"] == artifact["verdict_cases_fired"]
    t1 = artifact["tiers"][1]
    assert body["verdict_tier_arith_ann"] == t1["arith_ann"]
    assert body["verdict_tier_cagr"] == t1["cagr"]
    assert body["verdict_tier_label"] == t1["tier"]
    assert body["verdict_tier_n_days"] == t1["n_days"]
    assert body["verdict_tier_years"] == t1["years"]


def test_not_a_single_number_in_the_response_is_computed(tmp_path, artifact):
    """★ 端点里**一个数都不许算**。

    判据：响应里的每一个数都必须**逐字出现在产物里**。
    ⇒ 端点若自己算一个 `c − M`（= −0.1212…）出来，它**不在产物的数集里** ⇒ 红。

    ⚠️ 这条比「断言某几个键的值」强：它挡住的是**新算出来的键**，
    而不只是改错了已有键的值。
    """
    body = TestClient(create_app(tmp_path, llm=object())).get("/api/d2-verdict").json()
    allowed = _all_numbers(artifact)
    for key, val in body.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        assert float(val) in allowed, (
            f"响应里的 {key}={val!r} **不在产物的数集里** ⇒ 它是端点算出来的。"
            "判决产物自带 verdict_mde_annual 与 verdict_upper_one_sided_95，"
            "端点再算一遍就是第二份实现（第 137 层）")


def test_the_response_carries_only_the_verdict_tier_family(tmp_path, artifact):
    """★ **两条线不许混**：本端点只给判决档这一族。

    5 万交付档那一族（`gap_to_target_by_benchmark`）与超额腿那一族
    （`excess_legs_own_mde`）**不递**——递了就是邀请前端把它们
    和 `/api/d2-gap` 的数放在同一张图上比较，而它们**不是同一个量**。
    """
    body = TestClient(create_app(tmp_path, llm=object())).get("/api/d2-verdict").json()
    assert set(body) == CONTRACT_KEYS, (
        f"响应键集变了：多出 {sorted(set(body) - CONTRACT_KEYS)} / "
        f"少了 {sorted(CONTRACT_KEYS - set(body))}")
    assert "gap_to_target_by_benchmark" not in body
    assert "excess_legs_own_mde" not in body
    # 也不许把产物里那一族整个塞进来
    assert "gap_to_target_by_benchmark" not in json.dumps(body, ensure_ascii=False)


def test_a_missing_artifact_is_404_with_a_reason_not_an_empty_object(tmp_path):
    """★ 产物不在 ⇒ **404 + 一句话原因**。

    返回空对象在界面上与「判决读不出来」**长得一模一样**，
    而后者要求人去看（形状 23 的方向：假绿）。
    """
    r = TestClient(create_app(tmp_path, llm=object())).get("/api/d2-verdict")
    assert r.status_code == 404, f"期望 404，实得 {r.status_code}：{r.text[:200]}"
    detail = r.json().get("detail", "")
    assert detail and ARTIFACT_NAME in detail, (
        f"404 的原因没说是哪个产物缺了：{detail!r}")
    assert r.json() != {}, "404 不许带一个空对象当载荷"


def test_the_mde_is_delivered_so_the_frontend_can_label_the_band(tmp_path, artifact):
    """★ `verdict_mde_annual` 必须**一起给**。

    前端要画「带 = [c−M, U]」时，`c−M` 那一头是**算术**出来的下界，
    上界取产物自带的 95% 单侧上界 ⇒ 少了 MDE，前端就只知道自己瞎编的带宽。
    """
    body = TestClient(create_app(tmp_path, llm=object())).get("/api/d2-verdict").json()
    assert body["verdict_mde_annual"] is not None
    assert body["verdict_upper_one_sided_95"] is not None
    assert body["verdict_reading"] is not None
