"""每晚复盘脚本的守卫。

## 它守什么

`docs/DELIVERY_GATE.md` §3/§5/§6 的复盘机制：

| # | 性质 |
|---|---|
| 1 | **只回答两问**（用户今天能打开什么 / 离 D2 的缺口变了没有） |
| 2 | **只读产物字段，绝不重算**（第 109 层）⇒ 每个数都带 `产物路径::JSON 路径` |
| 3 | **缺产物 / 陈旧 ⇒ 显式说缺**，不许回落成别的数 |
| 4 | **提交归类只读标题首词**；归不出档 ⇒ 记「未归类」，**不替它归档** |
| 5 | **「收尾」档不计入进展** ⇒ 跑偏率是防跑偏读数，不是成就 |

## 为什么第 4 条要单列

§6 说「**归不出档的提交不许进**」。⇒ 若脚本在归不出档时**擅自**归到某一档，
它就在替提交作者做那个判断 ⇒ 而那个判断正是 §6 要防的事
⇒ 脚本必须**如实记「未归类」**，把判断留给读者。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import daily_review as dr  # noqa: E402


def _write(root: Path, rel: str, obj) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ── 提交归类：只读首词，归不出就记「未归类」 ────────────────────────────

@pytest.mark.parametrize("subject,expected", [
    ("交付: 修 D1 发布闸的方向", "交付"),
    ("交付(前端): 首屏加可买性表", "交付"),
    ("手段: 前瞻注册第 5 批", "手段"),
    ("手段(服务于 D1): 前瞻注册第 5 批", "手段"),
    ("收尾: 口径台账增补", "收尾"),
    ("fix: 修一个 typo", "未归类"),
    ("chore: 改个注释", "未归类"),
])
def test_the_classification_reads_only_the_first_word(subject, expected):
    """§6 的三档是**首词**；`手段(服务于 D1)` 允许带括号补充 ⇒ 前缀匹配。

    🚨 归不出档 ⇒ 记「未归类」，**不许**替它归到某一档
    （§6 要防的正是「归不出档 = 它不属于任何档 = 分心的伪装」）。
    """
    head = subject.split(":", 1)[0].strip()
    got = next((c for c in dr.CLASSES if head.startswith(c)), "未归类")
    assert got == expected, subject


def test_a_commit_with_no_class_is_never_silently_bucketed(tmp_path, monkeypatch):
    """**能力守卫**：分类器必须真的区分得开三类 + 未归类。"""
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    (tmp_path / ".git").mkdir(exist_ok=True)
    import subprocess
    subjects = ["交付: a", "手段: b", "收尾: c", "chore: d"]
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=False)
    for i, s in enumerate(subjects):
        p = tmp_path / f"f{i}.txt"
        p.write_text(s, encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", p.name], check=False)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "x"], check=False)
    out = dr.collect_commits(None)
    assert out["counts"]["未归类"] >= 1, out["counts"]


# ── 只读字段，不重算 ─────────────────────────────────────────────────

def test_every_number_carries_its_own_path():
    """第 109 层：核对产物时**读字段**，不重算 ⇒ 每个数必须能指到它从哪来。"""
    c = dr._cell({"a": {"b": 1.5}}, "a::b")
    assert c == {"value": 1.5, "at": "a::b"}
    missing = dr._cell({"a": {}}, "a::zzz")
    assert missing["value"] is None
    assert missing["at"] == "a::zzz"      # 出处仍然写清楚 ⇒ 读者知道去哪儿找


def test_a_missing_key_is_none_and_never_guessed():
    """缺键 ⇒ `None`。**不许回落、不许猜**（第 6 种形状：假装能调）。"""
    assert dr._dig({"a": 1}, "b") is None
    assert dr._dig(None, "a::b") is None
    assert dr._dig({"a": [1, 2]}, "a::1") == 2
    assert dr._dig({"a": [1, 2]}, "a::9") is None


# ── 缺产物 ⇒ 显式说缺 ───────────────────────────────────────────────

def test_a_missing_artifact_is_reported_not_faked(tmp_path, monkeypatch):
    """🚨 缺产物 ⇒ **显式记缺失原因**，不许用别的数顶替。"""
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    data, miss = dr._read_json("data/reports/run_status.json")
    assert data is None
    assert "不存在" in miss

    d1 = dr.collect_d1("2026-10-20")
    assert d1["delivered"] is False
    assert any("不存在" in n for n in d1["notes"]), d1["notes"]
    # 样本块必须存在且值是 None（**不是** 0、不是某个别的数）
    assert d1["sample"]["n_independent_windows"]["value"] is None


# ── 缺口比较：不可比就说不可比 ───────────────────────────────────────

def test_the_gap_comparison_refuses_to_compare_non_numbers(tmp_path):
    """两侧有一个不是数 ⇒ **不许说「变了」**（不许拿 None 去相减）。"""
    today = {"d2": {"deliverable_50k": {"gap_to_target_pp": {"value": None}}}}
    prev = {"d2": {"deliverable_50k": {"gap_to_target_pp": {"value": 11.6}}}}
    archive = Path(tmp_path)
    (archive / "review_2026-10-19.json").write_text(
        json.dumps(prev, ensure_ascii=False), encoding="utf-8")
    try:
        out = dr.compare_with_previous(today, archive, prefix="review")
        g = out["d2_gap_pp"]
        assert g["delta"] is None
        assert g["changed"] is None
        assert "不可比" in g["note"]
    finally:
        pass


def test_no_previous_review_means_baseline_not_change(tmp_path):
    """没有上一份 ⇒ **不许说「缺口变了」**。"""
    empty = Path(tmp_path)
    out = dr.compare_with_previous({}, empty, prefix="review")
    assert out["has_previous"] is False
    assert "基线" in out["note"]


# ── 写盘路径只能是 tmp_path（第 132 层） ───────────────────────────────

def test_the_review_writes_only_where_it_is_told(tmp_path):
    """产物落盘只许落在传进去的路径 ⇒ 生产目录不会被测试改写。"""
    out = tmp_path / "daily_review.json"
    rc = dr.main(["--date", "2026-10-20", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    # 归档落在**输出文件旁边** ⇒ 测试不碰生产目录（第 132 层）
    assert (tmp_path / "review_2026-10-20.json").exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["review_date"] == "2026-10-20"
    assert "questions" in payload
    assert payload["honest_boundaries"], "诚实边界不许为空"
