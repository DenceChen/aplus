"""`scripts/forward_validate.py` 的三条接线守卫。

## 为什么这些要有守卫

前瞻验证是本仓**唯一**没有被三堵墙挡住的判决路径
（成本墙 / σ 墙 / 观测墙都挡不住「还没发生的数据」）。
而它此前有**两处**会让它产不出判决的缺陷，且**两处都不报错**：

1. `train_ratio=0.999` ⇒ `split_window` 的切分点落在 `n-1`
   ⇒ **测试段只有 1 天** ⇒ `race()` 把 K>1 的候选全判成
   「不足 3 个调仓周期，无法测量」⇒ **一个候选都测不了**。
2. 评估用的是**整个面板**（从最早的注册日算起），不是**该批次自己的**
   前瞻窗口 ⇒ 晚注册的批次会被注册**之前**的行情评价
   ⇒ 前瞻验证的「前瞻」被破坏。

这两条都不会崩、不会报警，只会让 `forward_results.json` 安静地长出
「样本不足」——与「样本真的还不够」**逐位同形**。

## 守卫打在哪一层

**接线层**（`evaluate_batch` 收到了什么 `_dates` / 什么 `train_ratio`），
不是产物层：产物层只对上一次落盘有效，对当前代码零覆盖。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "forward_validate.py"


def _load():
    spec = importlib.util.spec_from_file_location("_forward_validate_probe",
                                                  SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fv():
    return _load()


def _fake_race(captured):
    def _race(xs, candidates, evaluate, **kw):
        captured["dates"] = list(xs.get("_dates") or [])
        captured.update(kw)
        return {"table": [], "rejected": []}
    return _race


def test_each_batch_is_evaluated_on_its_own_forward_window(fv, monkeypatch):
    """**批次只看它自己的窗口**——注册日之前的数据不许进评估。

    这是「前瞻」二字的全部含义。整面板评估会让晚注册的批次
    被注册**之前**的行情评价，而那段数据注册时已经看过了。
    """
    captured = {}
    monkeypatch.setattr(fv, "race", _fake_race(captured))
    xs = {"_dates": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-19"],
          "_daily_map": {}, "_market": {}}
    win = ["2026-10-14", "2026-10-19"]
    fv.evaluate_batch(xs, win, [{"weights": {"bm": 1.0}, "rebalance_every": 21,
                                 "buffer_multiple": None}])
    assert captured["dates"] == win, (
        f"评估用的是 {captured['dates']}，应当只用该批次自己的窗口 {win}")
    assert "2026-10-12" not in captured["dates"]


def test_the_test_segment_covers_the_whole_window(fv, monkeypatch):
    """**测试段必须覆盖整段窗口**，否则 K>1 的候选全部「无法测量」。

    `split_window(dates, train_ratio, embargo)`：
    `cut = max(1, min(n-1, int(n*train_ratio)))`。
    `train_ratio=0.999` ⇒ `cut = n-1` ⇒ **测试段 1 天**。
    ⇒ 用**实际传进去的** `train_ratio` 复算一遍，别写死一个期望值。
    """
    from aplus.evolve_race import MIN_TEST_PERIODS, split_window

    captured = {}
    monkeypatch.setattr(fv, "race", _fake_race(captured))
    xs = {"_dates": [], "_daily_map": {}, "_market": {}}
    win = [f"2026-{11 + i // 28:02d}-{i % 28 + 1:02d}" for i in range(64)]
    fv.evaluate_batch(xs, win, [{"weights": {"bm": 1.0}, "rebalance_every": 21,
                                 "buffer_multiple": None}])
    tr = captured["train_ratio"]
    _train, test = split_window(win, tr, captured["embargo_days"])
    assert len(test) >= len(win) - 1, (
        f"测试段只有 {len(test)} 天（窗口 {len(win)} 天）"
        f" ⇒ K=21 的候选攒不到 {MIN_TEST_PERIODS} 个调仓周期")
    assert len(test) // 21 >= MIN_TEST_PERIODS, (
        f"测试段 {len(test)} 天 ⇒ K=21 只有 {len(test) // 21} 个周期，"
        f"不足 {MIN_TEST_PERIODS} ⇒ 前瞻验证产不出判决")


def test_the_pit_bm_is_attached_to_the_panel(fv):
    """**PIT BM 必须挂进横截面**——否则含 `bm` 的候选会让整个前瞻验证崩。

    `aplus/factor.py::composite_score` 对缺失的因子列会 **KeyError**。
    判据落在 **AST 的调用点**上（`main` 的函数体里真的调了它），
    不是「源码里出现过这个串」——后者会被注释满足（仓里第 13 种形状）。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main_fn = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = set()
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    assert "attach_pit_bm" in called, (
        "main() 里没有调 attach_pit_bm ⇒ 含 bm 的候选会让前瞻验证 KeyError")
