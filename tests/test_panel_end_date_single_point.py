"""★ 第 23 种形状：**一只票定全池窗口末端**（形状 18 的族，2026-10-15 实测登记）

## 长什么样

    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

`sorted` ⇒ `files[0]` **恒是 `000001.SZ`**（6 位代码字典序）。
于是**一只票的末端日期决定了整个面板的回测窗口末端**。

## 为什么今天没出事 —— 因为运气

实测（2026-10-15，仓库 5460 只全池逐只读 `date` 列）：

    2026-10-14 → 5215 只 (95.51%)   ← 众数，也是 files[0] 的值
    2022-06-22 →    6 只 ( 0.11%)
    2026-10-12 →    5 只 ( 0.09%)
    … 其余 240 只末端散落在 2022–2025

⇒ 单点**恰好等于众数**，依赖当前**无害**。但它正确**不是设计出来的**。

## ⚠️ 这不是假想 —— 它已经发生过

`scripts/run_daily.py:105` 的注释原话：

    `files[0]` 是 000001.SZ，而它正是 INCIDENTS 里记的
    「只剩 17 行」截断缺陷的受害者之一 —— 拿一只可能坏掉的票
    当全市场的判据，等于让一只票决定 5459 只的命运。

那一行的缓解**只挡 `None`（读不出来）**，**不挡「读得出来、但比池子末端旧」**
—— 而后者才是这里这一族真正会发生的形态。

## 本文件干什么

本文件**不修那 17 处**（改了会动既有窗口，属「引入新问题」）。
本文件只把那条**运气**钉成一条**可测的守卫**：

    files[0] 的末端  ==  抽样前沿（max）

**哪天这条不成立了，红灯会告诉你那一刻窗口被谁偷偷改了。**
它今天绿，但绿的意思是「前提被验证过」，不是「前提被消除了」。

## 断言为什么这么写（两次踩坑后定下的）

1. 不能断言「全池末端一致」—— 停牌/退市尾票本来就落后，实测 4.49%。
   那会得到一条**永远红**的守卫，而永远红的守卫会让人去改对的地方。
2. 不能断言「`files[0]` 等于众数」—— 那要先全池扫（~60 s），进不了门禁。
   ⇒ 改用**等距抽样 64 只**（实测 0.23 s），断言「不落后于前沿」。
   抽样能看见落后者吗？见 `test_the_sample_can_actually_see_a_lagging_stock`
   —— 那是这条守卫的正控：**没有正控，它可能恒真**。
"""
from __future__ import annotations

import ast
import pathlib
import re

import pandas as pd
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DAILY = _ROOT / "data" / "master" / "daily"

_SAMPLE_N = 64

# ★ 冻结名单：把 `files[0]` 当时间轴的脚本（AST 扫出的真值，与 `grep -rn 'files\[0\]' scripts/`
#   手工数的 16 处**两个独立方法一致**）。**新增即红** ——
#   不是禁止新增，而是新增时必须有人**当场判断**它是不是又一个单点依赖。
#   （与 `DEFAULT_GENERATORS` 同一手法：名单会腐，但腐的方式是逼人思考。）
#   `run_daily.py` / `audit_survivorship/audit.py` 不在内：那是「探针」语义
#   （读不出来就换一只票），不是拿单点当全池窗口。
_KNOWN_END_FROM_FILES0 = {
    "scripts/backtest_walk_forward_weights.py",
    "scripts/candidate_block_audit.py",
    "scripts/evolve_race_composite.py",
    "scripts/evolve_race_fast.py",
    "scripts/evolve_weekly.py",
    "scripts/execution_lag_ab.py",
    "scripts/extract_ic_matrices.py",
    "scripts/factor_ic_diagnostic.py",
    "scripts/factor_width_mde.py",
    "scripts/forward_validate.py",
    "scripts/ic_guided_race.py",
    "scripts/measure_bridge_error.py",
    "scripts/placebo_control.py",
    "scripts/significance_check.py",
    "scripts/universe_forensics.py",
    "scripts/universe_isolation.py",
}

# ⚠️ 这个正则**匹配的是参数**（`files[0]`），不是整条调用。
#   第一版写成 `read_parquet\(\s*files\[0\]\s*\)` 然后拿它去 `search(ast.unparse(a))`
#   —— `a` 是参数，unparse 出来只有 `files[0]`，永远不含 `read_parquet(` ⇒ **扫出 0 处**。
#   若不是手里已有 grep 的 17 行做交叉验证，它会让我把 16 处真实站点**从记录里删掉**，
#   再冻一份空名单当真相。★ 探针自身有 bug 时，它返回的「空」长得和「不存在」一模一样。
_PATTERN = re.compile(r"files\[0\]")


def _panel_files() -> list[pathlib.Path]:
    if not _DAILY.is_dir():
        pytest.skip(f"★ 主库不在 {_DAILY}，本守卫只对真实面板有意义")
    files = sorted(_DAILY.glob("*.parquet"))
    if not files:
        pytest.skip("★ 主库为空")
    return files


def _end(p: pathlib.Path) -> str:
    try:
        return str(pd.read_parquet(p, columns=["date"])["date"].max())
    except Exception as exc:  # 读坏 = 另一种形态，交给 run_daily 那条线
        return f"<ERR {type(exc).__name__}>"


def _sample(files: list[pathlib.Path]) -> list[pathlib.Path]:
    stride = max(1, len(files) // _SAMPLE_N)
    return files[::stride][:_SAMPLE_N]


# ── 正控：抽样本身必须真的包含被审查的对象 ──────────────────────────

def test_the_sample_really_contains_the_single_point_under_judgment():
    """★ 没有这条，下面那条守卫可能在审一个**根本没进样本**的文件 —— 恒真。"""
    files = _panel_files()
    s = _sample(files)
    assert len(s) >= 8, f"★ 样本只有 {len(s)} 只，抽样本身不可信"
    assert files[0] in s, (
        f"★ `files[0]`={files[0].stem} 不在样本里 ⇒ 下面那条守卫在审空气。"
        f"（stride={max(1, len(files)//_SAMPLE_N)} 把第 0 项跳过了）"
    )


def test_the_sample_can_actually_see_a_lagging_stock():
    """★ 正控之二：抽样里**确实存在**落后于 `files[0]` 的票。

    实测 61/64 一致、3 只落后 —— 所以「全池一致」是**假的**，
    这条守卫若改成断言一致率 100% 就会永远红。
    """
    files = _panel_files()
    s = _sample(files)
    front = _end(files[0])
    # ⚠️ 每个文件只读一次：第一版在推导式里写了 `_end(p)` 两次 ⇒ 64 变 128 次读盘
    ends = {p.stem: _end(p) for p in s}
    laggers = [k for k, v in ends.items() if not v.startswith("<ERR") and v < front]
    assert laggers, (
        f"★ 样本里一只落后的票都没有 ⇒ 前沿=max 的断言可能恒真"
        f"（front={front}，样本 {len(s)} 只）"
    )


# ── 主守卫 ───────────────────────────────────────────────────────

def test_the_single_point_is_not_behind_the_panel_frontier():
    """★ 核心：`files[0]` 的末端 **不得落后于** 抽样前沿。

    落后的后果是**静默**的：窗口悄悄缩到单点那天，产物里没有任何字段
    记录「窗口末端由一只票决定」。
    """
    files = _panel_files()
    front_end = _end(files[0])
    assert not front_end.startswith("<ERR"), f"★ files[0] 读坏了：{front_end}"

    ahead = [(p.stem, _end(p)) for p in _sample(files)
             if not _end(p).startswith("<ERR") and _end(p) > front_end]
    assert not ahead, (
        f"★ 单点滞后：`files[0]`({files[0].stem}) 末端 = {front_end}，"
        f"但抽样里 {len(ahead)} 只更晚，例如 {ahead[:3]}。\n"
        f"   ⇒ 那 17 处 `end = args.end or ...files[0]...` 此刻会把窗口"
        f"**悄悄缩到 {front_end}**，而产物里没有任何字段能让人看出这一点。"
        f"\n   修法是取 max over all，不是取第一只。"
    )


# ── 增长守卫：名单冻结，扩围必须有人判断 ────────────────────────────

def _scripts_using_files0_as_time_axis() -> set[str]:
    found = set()
    for py in sorted((_ROOT / "scripts").rglob("*.py")):
        src = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # 只认「`read_parquet(files[0])` 且结果被取 max」这一形态
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name != "read_parquet":
                continue
            if any(_PATTERN.search(ast.unparse(a)) for a in node.args):
                found.add(str(py.relative_to(_ROOT)))
                break
    return found


def test_the_frozen_list_matches_what_is_actually_in_the_tree():
    """★ 名单与树必须一致 —— 变了要么是有人新增（要判断），要么是有人删了（要解释）。

    首次运行时把 `_KNOWN_END_FROM_FILES0` 换成扫描结果即可（本次已替换）。
    """
    actual = _scripts_using_files0_as_time_axis()
    # run_daily / audit_survivorship 是「探针」语义（读不出来就换票），不算时间轴
    actual -= {"scripts/run_daily.py", "scripts/audit_survivorship/audit.py"}
    assert actual == _KNOWN_END_FROM_FILES0, (
        f"★ 单点依赖名单变了。\n"
        f"   新增：{sorted(actual - _KNOWN_END_FROM_FILES0)}\n"
        f"   消失：{sorted(_KNOWN_END_FROM_FILES0 - actual)}\n"
        f"   ⇒ 新增的每一处都当场判断一次：它是不是又一个「一只票定全池」的窗口？"
    )


def test_the_three_sites_without_an_end_override_are_still_exactly_three():
    """★ 其中 3 处**连 `--end` 兜底都没有**（实测扫描），条件最苛刻 ⇒ 单独钉住。

    这 3 处即便 `--end` 为空也无路可退，只能吃单点。
    """
    unguarded = set()
    for py in sorted((_ROOT / "scripts").rglob("*.py")):
        src = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            tgt = ast.unparse(node.targets[0]) if node.targets else ""
            if tgt != "end":
                continue
            seg = ast.unparse(node.value)
            if "files[0]" in seg and "args.end" not in seg:
                unguarded.add(str(py.relative_to(_ROOT)))
    expected = {
        "scripts/significance_check.py",
        "scripts/execution_lag_ab.py",
        "scripts/evolve_weekly.py",
    }
    assert unguarded == expected, (
        f"★ 无兜底的单点依赖集合变了。\n"
        f"   新增：{sorted(unguarded - expected)}\n"
        f"   消失：{sorted(expected - unguarded)}\n"
        f"   ⇒ 消失的也要解释：是被修好了，还是被改名躲过了扫描？"
    )
