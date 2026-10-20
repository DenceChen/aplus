"""**信号作用域 / 归属**（signal scoping）的守卫 —— 2026-10-21

预注册：`docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`

## 这个文件守的是什么

前瞻底稿 `verification.parquet` 是**追加式**的。若 D1 的**信号定义**变了
（改权重 / 改榜宽 / 改可买性过滤），同一本底稿里会**同时**装着**两个信号**的行。

⇒ **闸门读整本底稿 = 判的是另一个信号。**

本文件钉五件事：

| 守卫 | 钉住什么 |
|---|---|
| G1 能力 | 归属**真的会切**（两个信号各若干窗口 ⇒ 只取出当前信号那一段） |
| G2 缺证据 | 底稿**既无** `signal_id` **也无** `weights_hash` ⇒ **无任何归属证据** ⇒ 显式缺失 |
| G3 不回落 | 不可归属 ⇒ 判决**不许**变成「通过」 |
| G4 共用入口 | 8 个消费者**必须**走**同一个**过滤函数（AST 扫，锚在**调用点**） |
| G5 逐行比对 | `weights_hash` 基准**必须逐行**比；不匹配/空值**剔出并计数** |
| G6 覆盖范围 | 失败态的作用域块**必须**带整本底稿的日期范围 |
| G7 上游契约 | 上游产物缺块 / 状态不可用 / 身份不符 ⇒ 下游**拒绝外推** |

## 🚨 三个纪律

1. **测试不许写生产产物路径**：全部用 `tmp_path`。
2. **AST 扫，锚在调用点**：子串能被**注释**满足（第 13 层的坑）⇒
   守卫认的是 `ast.Call` 节点，注释在 AST 里**根本不存在**。
3. **不许用「子进程没崩」当通过**：断言必须落在**产物字段**上。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aplus import signal_scope as SS  # noqa: E402

#: 生产报告目录 —— **只用来解析当前身份**，测试的**数据**一律在 tmp_path。
PROD_REPORTS = ROOT / "data" / "reports"

#: 8 个消费者。**直接读底稿**的 6 个 + **读上游产物**的 2 个。
DIRECT_CONSUMERS = (
    "score_ranking_power.py",
    "diagnose_hit_rate.py",
    "forward_d2_verdict.py",
    "measure_downleg_ranking.py",
    "measure_portfolio_nav.py",
    "measure_portfolio_exec.py",
)
INDIRECT_CONSUMERS = (
    "forward_d2_horizon.py",
    "verdict_horizon_d1.py",
)
ALL_CONSUMERS = DIRECT_CONSUMERS + INDIRECT_CONSUMERS

#: 各消费者**必须**在 `main` 里调用的作用域函数。
REQUIRED_CALL = {
    **{name: "scope_ledger" for name in DIRECT_CONSUMERS},
    **{name: "upstream_scope_verdict" for name in INDIRECT_CONSUMERS},
}

#: 当前权重的指纹（**由 `aplus/reports.py` 算**，本测试不手打）。
CURRENT_WEIGHTS_FP = "c2178335be6d"


# ——————————————————————————————————————————————————————————————
# 夹具
# ——————————————————————————————————————————————————————————————

def _current_weights_fingerprint() -> str:
    """当前权重的指纹（**真算**，不手打）。

    ⚠️ `weights_fingerprint` 只覆盖**权重**，不覆盖 `top_n`/`bottom_n`
    ⇒ 它的证明力**弱于**信号指纹（见 `attribution_block` 的 `note`）。
    """
    return SS.resolve_current_weights_fingerprint(PROD_REPORTS)


def _current_signal_id() -> str:
    """当前信号 id（**真算**，不手打）。"""
    return SS.resolve_current_signal_id(PROD_REPORTS)


def _window_rows(w: int, direction: str, n: int, score_base: float) -> list[dict]:
    """一个成交窗口的 n 行（`w` = 窗口序号，从 0 起）。"""
    pred = f"2026-10-{9 + w:02d}"
    entry = f"2026-10-{12 + w:02d}"
    exit_ = f"2026-10-{13 + w:02d}"
    rows = []
    for i in range(n):
        sc = float(score_base + i)
        rows.append({
            "code": f"{w:02d}{direction[0].upper()}{i:03d}.SZ",
            "name": f"票{w}{direction}{i}",
            "direction": direction,
            "score": sc,
            "adjusted_score": sc,
            "result": "hit" if i % 2 == 0 else "miss",
            "pred_date": pred,
            "entry_date": entry,
            "exit_date": exit_,
            "gross_pct": 0.01 if i % 2 == 0 else -0.01,
            "market_pct": 0.002,
            "cost": 0.003,
        })
    return rows


def _ledger(n_windows: int = 4, per_day: int = 10,
            weights_hash: str | None = None) -> pd.DataFrame:
    """一份**合成**底稿：n_windows 个独立成交窗口 × 两腿 × per_day 只。

    ⚠️ 刻意**不读生产底稿**：「拿现场当标准答案」——现场自己每天在变。
    `weights_hash=None` ⇒ **不带**那一列（用来测「无任何归属证据」）。
    """
    rows: list[dict] = []
    for w in range(n_windows):
        rows += _window_rows(w, "up", per_day, 1.0 + w * 100)
        rows += _window_rows(w, "down", per_day, 1.0 + w * 100)
    df = pd.DataFrame(rows)
    if weights_hash is not None:
        df["weights_hash"] = weights_hash
    return df


def _write_ledger(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def _with_signals(df: pd.DataFrame, per_window: list[str]) -> pd.DataFrame:
    """给**每个成交窗口**打一个 `signal_id`（按窗口，**不按行序**）。

    ⚠️ 按行序切会切出「半个窗口」⇒ 窗口数与行数的关系全乱
    ⇒ 必须按 `entry_date`（= 独立成交窗口）切。
    """
    out = df.copy()
    order = sorted(out["entry_date"].astype(str).unique())
    assert len(order) == len(per_window), (
        f"窗口数 {len(order)} 与给的 signal 列表 {len(per_window)} 不匹配")
    out["signal_id"] = out["entry_date"].astype(str).map(
        dict(zip(order, per_window)))
    return out


def _with_weights_hash(df: pd.DataFrame, per_window: list[str | None]) -> pd.DataFrame:
    """给**每个成交窗口**打一个 `weights_hash`（`None` ⇒ 该列留空）。"""
    out = df.copy()
    order = sorted(out["entry_date"].astype(str).unique())
    assert len(order) == len(per_window), (
        f"窗口数 {len(order)} 与给的列表 {len(per_window)} 不匹配")
    mapping = dict(zip(order, per_window))
    out["weights_hash"] = out["entry_date"].astype(str).map(mapping)
    return out


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    """跑一个消费者脚本（**只写 tmp_path**）。"""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, cwd=str(ROOT))


# ——————————————————————————————————————————————————————————————
# G0 正控：守卫本身不是装饰
# ——————————————————————————————————————————————————————————————

def test_g0_the_shared_module_defines_everything_the_consumers_need():
    """正控：共享入口**真的**定义了全部作用域函数。"""
    # ⚠️ 2026-10-21：作用域模块从 `scripts/` 搬进 `aplus/`
    # （**库不该反向依赖 `scripts/`** —— `aplus/webapp.py` 也要用它）
    src = (ROOT / "aplus" / "signal_scope.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"scope_ledger", "scope_block", "attribution_block",
            "upstream_scope_verdict", "resolve_attribution_context"} <= funcs, (
        f"共享入口缺函数：{funcs}")


def test_g0_the_weights_fingerprint_is_computed_not_typed():
    """正控：权重指纹**由 `aplus/reports.py` 算**，且**与底稿里的一列同格式**。

    🚨 指纹**必须**是 12 位十六进制 —— 底稿的 `weights_hash` 就是这个格式
    （`aplus/reports.py::weights_fingerprint` 的实现）⇒ 不匹配就**永远**零命中。
    """
    fp = _current_weights_fingerprint()
    assert fp and len(fp) == 12, f"当前权重指纹是 {fp!r}（期望 12 位十六进制）"
    int(fp, 16)  # 必须是合法十六进制


def test_g0_the_contract_signal_id_resolves():
    """正控：契约真的落地且**当前信号 id 算得出来**。

    🚨 **这是契约的守卫**：契约没落地/坏了 ⇒ 这一条红 ⇒ 归属只能退到
    `weights_hash` 基准（**更弱的证据**）。
    """
    sid = _current_signal_id()
    assert isinstance(sid, str) and sid.strip(), f"当前信号 id 是 {sid!r}"


def test_g0_the_weights_source_is_the_same_one_the_contract_uses():
    """**两个指纹的权重来源必须是同一个**（否则就是两份真相源）。

    ⇒ 用契约自己支持的 `weights=` 覆盖来对拍：
    `current_signal_id(dir, weights=W)` 必须 == `current_signal_id(dir)`
    （当 `W` 就是 `load_weights` 给的那一组时）。
    """
    from aplus.evolve import load_weights
    from aplus import reports
    w = load_weights(PROD_REPORTS)["weights"]
    assert reports.current_signal_id(PROD_REPORTS, weights=w) == \
        reports.current_signal_id(PROD_REPORTS), (
        "🚨 本模块的权重来源与契约的权重来源**不是同一组** ⇒ "
        "两个指纹会在不同的真相上算 ⇒ 归属会分错")


# ——————————————————————————————————————————————————————————————
# G1 能力守卫：归属真的会切
# ——————————————————————————————————————————————————————————————

def test_g1_a_two_signal_ledger_yields_only_the_current_signal_segment():
    """**能力守卫**：两个信号各若干窗口 ⇒ 当前信号那一段**必须**被单独取出。

    🚨 这条不许退化成「形状守卫」：一个恒真的过滤（返回全部）会通过
    「作用域非空」这类弱断言，但**通不过**本条。
    """
    df = _ledger(n_windows=4, per_day=10)
    df = _with_signals(df, ["sigA", "sigA", "sigA", "sigB"])
    scoped = SS.scope_ledger(df, "sigA", _current_weights_fingerprint())

    assert scoped.status == SS.STATUS_OK, scoped.reason
    assert scoped.attribution_basis == SS.BASIS_SIGNAL_ID
    assert scoped.coverage["rows_in_scope"] == 60, scoped.coverage
    assert scoped.coverage["rows_other_signals"] == 20, scoped.coverage
    assert scoped.coverage["ledger_rows_total"] == 80
    # 🚨 **逐行**核对：作用域内**没有一行**属于别的信号。
    assert set(scoped.frame["signal_id"]) == {"sigA"}
    # ⇒ 过滤**不是恒等**：作用域 ≠ 整本底稿。
    assert len(scoped.frame) < len(df)
    # ⇒ 两个信号的窗口数**分别**报出来（不是合在一起）。
    assert scoped.coverage["entry_dates_in_scope"] == 3
    assert scoped.coverage["ledger_entry_dates"] == 4


def test_g1_the_other_signal_segment_is_reachable_by_asking_for_it():
    """反向正控：换成另一个信号 id ⇒ 取到的是**另一段**（不是同一段）。"""
    df = _ledger(n_windows=4, per_day=10)
    df = _with_signals(df, ["sigA", "sigA", "sigA", "sigB"])
    a = SS.scope_ledger(df, "sigA", _current_weights_fingerprint())
    b = SS.scope_ledger(df, "sigB", _current_weights_fingerprint())
    assert set(a.frame["code"]) != set(b.frame["code"])
    assert a.coverage["rows_in_scope"] == 60
    assert b.coverage["rows_in_scope"] == 20
    assert b.coverage["rows_other_signals"] == 60


def test_g1_the_weights_hash_basis_also_splits_row_by_row():
    """**能力守卫（第二基准）**：`weights_hash` 基准**必须**逐行切。

    ⇒ 这就是老底稿（没有 `signal_id` 列）走的路。
    """
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=4, per_day=10)
    df = _with_weights_hash(df, [fp, fp, "OTHER_FP", fp])
    scoped = SS.scope_ledger(df, None, fp)   # **没有** signal_id ⇒ 走 weights_hash

    assert scoped.status == SS.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH, scoped.reason
    assert scoped.attribution_basis == SS.BASIS_WEIGHTS_HASH
    assert scoped.coverage["rows_in_scope"] == 60, scoped.coverage
    assert scoped.coverage["rows_other_signals"] == 20, scoped.coverage
    assert set(scoped.frame["weights_hash"]) == {fp}


# ——————————————————————————————————————————————————————————————
# G2 缺证据守卫：显式缺失，不许放行全部
# ——————————————————————————————————————————————————————————————

def test_g2_a_ledger_without_any_attribution_column_is_explicitly_missing():
    """底稿**既无** `signal_id` **也无** `weights_hash` ⇒ **无任何归属证据**。

    🚨 「无证据 ⇒ 当作全部都是当前信号」是**第 6 种形状（假装能调）**。
    """
    df = _ledger(n_windows=3, per_day=10)
    assert "signal_id" not in df.columns
    assert "weights_hash" not in df.columns
    scoped = SS.scope_ledger(df, "sigA", _current_weights_fingerprint())

    assert scoped.status == SS.STATUS_MISSING_SIGNAL_ID_COLUMN, scoped.status
    assert scoped.has_evidence is False
    assert len(scoped.frame) == 0, "🚨 无证据时竟放行了行 —— 那是回落"
    assert scoped.coverage["rows_in_scope"] == 0
    # ⇒ 整本底稿的数**照报**（读者要知道它是什么）。
    assert scoped.coverage["ledger_rows_total"] == 60
    assert scoped.coverage["ledger_entry_dates"] == 3
    assert SS.VERDICT_NOT_DECIDABLE not in ("PASS", "NOT_PASSED_MEASURED")
    assert SS.scope_block(scoped)["verdict_if_not_attributable"] == \
        SS.VERDICT_NOT_DECIDABLE


def test_g2_the_scope_block_names_the_date_range_of_the_ledger_it_refuses():
    """G6：失败态的作用域块**必须**带整本底稿的日期范围。

    「不可用于判决」这句话**必须**同时告诉读者「它是什么」。
    """
    df = _ledger(n_windows=3, per_day=10)
    scoped = SS.scope_ledger(df, "sigA", _current_weights_fingerprint())
    block = SS.scope_block(scoped)
    rng = block["ledger_date_range"]
    assert rng["pred_date_min"] == "2026-10-09"
    assert rng["pred_date_max"] == "2026-10-11"
    assert rng["entry_date_min"] == "2026-10-12"
    assert rng["entry_date_max"] == "2026-10-14"
    assert "2026-10-12" in scoped.reason, scoped.reason


# ——————————————————————————————————————————————————————————————
# G5 逐行比对守卫：空值/不匹配 ⇒ 剔出并计数
# ——————————————————————————————————————————————————————————————

def test_g5_rows_with_a_blank_value_are_unattributable_not_current():
    """该列为空/NaN ⇒ **不可归属** ⇒ 剔出作用域并**单独计数**。

    这一格就是**过渡期**：机制落地前写的行（没有该列的值）
    与落地后写的行同处一表。
    """
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=2, per_day=10)
    df = _with_weights_hash(df, [fp, None])
    scoped = SS.scope_ledger(df, None, fp)

    assert scoped.status == SS.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH, scoped.reason
    assert scoped.coverage["rows_in_scope"] == 20, scoped.coverage
    assert scoped.coverage["rows_unattributed"] == 20, scoped.coverage
    assert scoped.coverage["ledger_rows_total"] == 40
    assert scoped.rows_unattributable == 20
    # ⇒ 不可归属的行**不许**混进作用域。
    assert set(scoped.frame["weights_hash"]) == {fp}


def test_g5_rows_with_a_different_value_are_counted_as_unmatched():
    """该列的值**不等于**当前值 ⇒ **不匹配** ⇒ 剔出并**单独计数**。

    🚨 这一条与「空值」**分开计数**（`rows_unmatched` vs `rows_unattributable`）
    —— 两个成因不同，混在一起就看不出是哪一种。
    """
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=2, per_day=10)
    df = _with_weights_hash(df, [fp, "OTHER_FP"])
    scoped = SS.scope_ledger(df, None, fp)

    assert scoped.coverage["rows_in_scope"] == 20, scoped.coverage
    assert scoped.coverage["rows_other_signals"] == 20, scoped.coverage
    assert scoped.coverage["rows_unattributed"] == 0, scoped.coverage
    assert scoped.rows_unmatched == 20


def test_g5_an_entirely_unattributable_ledger_is_not_usable():
    """全部行都没有可用的归属值 ⇒ 作用域为空。"""
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=2, per_day=10)
    df = _with_weights_hash(df, [None, None])
    scoped = SS.scope_ledger(df, None, fp)
    assert scoped.status == SS.STATUS_NO_MATCHING_SIGNAL, scoped.status
    assert len(scoped.frame) == 0
    assert scoped.coverage["rows_unattributed"] == 40
    # ⇒ 「有列」**不等于**「有归属」⇒ 判决层必须能看出这两件事。
    assert scoped.has_evidence is True
    assert scoped.usable is False


def test_g5_the_attribution_block_says_which_evidence_was_used():
    """产物里的**归属依据**块必须说清「证据覆盖到哪一层」。"""
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=2, per_day=10, weights_hash=fp)
    scoped = SS.scope_ledger(df, None, fp)
    block = SS.attribution_block(scoped)
    assert block["basis"] == SS.BASIS_WEIGHTS_HASH
    assert block["current_weights_fingerprint"] == fp
    assert block["rows_matched"] == 40
    assert block["rows_unmatched"] == 0
    assert block["rows_unattributable"] == 0
    # 🚨 **权重指纹的边界必须写在脸上**：它**不**证明「同一个信号」。
    assert "top_n" in block["note"], block["note"]
    assert "不**证明「**同一个信号" in block["note"] or \
        "不**证明「同一个信号" in block["note"], block["note"]


# ——————————————————————————————————————————————————————————————
# G4 共用入口守卫（AST，锚在调用点）
# ——————————————————————————————————————————————————————————————

def _module_tree(name: str) -> ast.Module:
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))


def _main_func(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("该脚本没有模块级 `main` 函数")


def _imported_from_signal_scope(tree: ast.Module) -> set[str]:
    """这个模块从 `signal_scope` import 了哪些名字。

    ⚠️ 认的是**语法位置**（`ast.ImportFrom`），不是全文子串 ——
    子串会被**注释**满足（第 13 层）。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "")
            if mod == "signal_scope" or mod.endswith(".signal_scope"):
                names |= {a.name for a in node.names}
    return names


def _calls_in_main(main: ast.FunctionDef) -> set[str]:
    """`main` 里被调用到的、**裸名**调用的函数名。"""
    out: set[str] = set()
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
    return out


@pytest.mark.parametrize("name", ALL_CONSUMERS, ids=list(ALL_CONSUMERS))
def test_g4_every_consumer_calls_the_shared_entry_point_inside_main(name):
    """8 个消费者**必须**在 `main` 里调用**同一个**作用域入口。

    ⚠️ 锚在**调用点**（`ast.Call` 落在 `main` 的函数体里）——
    注释、docstring、甚至模块顶部的 import 都**不满足**本条。
    """
    tree = _module_tree(name)
    imported = _imported_from_signal_scope(tree)
    required = REQUIRED_CALL[name]
    assert required in imported, (
        f"{name} 没有从 `signal_scope` import {required!r}（imported={imported}）"
        "⇒ 它要么自己写了一遍过滤（第 97 层），要么根本没做归属分桶。")

    main = _main_func(tree)
    called = _calls_in_main(main)
    assert required in called, (
        f"{name} 的 `main` 里**没有**调用 {required}(...) ⇒ "
        f"归属只在别处被调用（main 里的调用点 = {sorted(called)}）"
        "⇒ 判决路径**没有**经过归属过滤。")


@pytest.mark.parametrize("name", ALL_CONSUMERS, ids=list(ALL_CONSUMERS))
def test_g4_every_consumer_imports_the_scope_module_from_the_same_place(name):
    """所有消费者的作用域入口**必须**来自**同一个**模块。"""
    tree = _module_tree(name)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "")
            if mod == "signal_scope" or mod.endswith(".signal_scope"):
                mods.add(mod)
    assert mods, f"{name} 没有从 signal_scope import 任何东西"
    assert len(mods) == 1, f"{name} 从**多个**地方 import 作用域：{mods}"


@pytest.mark.parametrize("name", DIRECT_CONSUMERS, ids=list(DIRECT_CONSUMERS))
def test_g4_the_read_result_is_what_gets_filtered(name):
    """**数据流守卫**：读出来的那个变量**必须**是喂进 `scope_ledger` 的那个。

    🚨 「import 了 `scope_ledger`」**不等于**「归属接在读口上」——
    一个把过滤挂在别处的接线会通过上一条守卫，却让判决**照旧读整本底稿**。
    ⇒ 本条认的是**实参**：读口的返回值 ⇒ `scope_ledger` 的第一个实参。
    """
    main = _main_func(_module_tree(name))
    read_names: set[str] = set()
    scope_args: set[str] = set()
    for node in ast.walk(main):
        if isinstance(node, ast.Assign):
            is_read = (isinstance(node.value, ast.Call)
                       and isinstance(node.value.func, ast.Attribute)
                       and node.value.func.attr == "read_parquet")
            if is_read:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        read_names.add(tgt.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "scope_ledger" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name):
                scope_args.add(first.id)
    assert read_names, (
        f"{name} 的 `main` 里**没有**「读底稿 ⇒ 赋给一个变量」这一步 ⇒ "
        "归属的数据流无从核对")
    assert scope_args & read_names, (
        f"{name}：读口产出 {sorted(read_names)}，而 `scope_ledger` 吃的是 "
        f"{sorted(scope_args)} ⇒ **读出来的不是被归属过滤的那个** ⇒ "
        "底稿的**整本**仍会流进判决")


def test_g4_no_consumer_reimplements_the_attribution():
    """**没有任何消费者自己定义**归属函数（两份副本之间没有守卫）。"""
    for name in ALL_CONSUMERS:
        tree = _module_tree(name)
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for forbidden in ("scope_ledger", "scope_block", "attribution_block",
                          "upstream_scope_verdict", "resolve_attribution_context"):
            assert forbidden not in defined, (
                f"{name}**自己定义了** {forbidden}() ⇒ 归属逻辑有了第二份副本 "
                "⇒ 两份副本之间没有守卫（第 97 层）。"
                "⇒ 只能 import `aplus/signal_scope.py` 的那一份。")


# ——————————————————————————————————————————————————————————————
# G3 不回落守卫：判决不许变成「通过」
# ——————————————————————————————————————————————————————————————

@pytest.mark.parametrize(
    "script,out_name,extra",
    [
        ("score_ranking_power.py", "gate.json", []),
        ("measure_downleg_ranking.py", "gate.json",
         ["--gate-out", "{tmp}/downleg_gate.json"]),
    ],
    ids=["score_ranking_power", "measure_downleg_ranking"])
def test_g3_an_unattributable_ledger_never_produces_a_passing_verdict(
        script, out_name, extra, tmp_path):
    """归属不可用 ⇒ 判决**不许**变成「通过」。

    ⚠️ 这条跑的是**真脚本**（子进程）⇒ 它验的是**调用点**上的行为，
    不是「函数存在」（记忆：探针跑被调函数、缺陷在调用点）。
    """
    df = _ledger(n_windows=3, per_day=10)   # **没有**任何归属列
    ledger = _write_ledger(df, tmp_path / "verification.parquet")
    out = tmp_path / out_name
    argv = ["--verification", str(ledger),
            "--reports-dir", str(PROD_REPORTS),
            "--out", str(out)]
    for e in extra:
        argv += e.replace("{tmp}", str(tmp_path)).split()
    p = _run(script, *argv)
    assert p.returncode == 0, f"{script} 退出码 {p.returncode}\n{p.stderr[-2000:]}"

    doc = json.loads(out.read_text(encoding="utf-8"))
    if script == "score_ranking_power.py":
        # 涨腿闸门：判决就在 `--out` 那份里。
        gate = doc
    else:
        # 🚨 跌腿的**闸门产物是另一个文件**（`--gate-out`）⇒ 判决读**它**。
        #    （`--out` 那份是对照臂/诊断，字段名是 `verdict_about_downleg`。）
        gate = json.loads((tmp_path / "downleg_gate.json").read_text(
            encoding="utf-8"))
    assert gate["verdict"] != "PASS", f"{script} 竟判 PASS：{gate.get('verdict')}"
    assert gate.get("publish_allowed") is not True
    if script == "score_ranking_power.py":
        assert gate["publish_allowed"] is False
        assert gate["verdict"] == SS.VERDICT_NOT_DECIDABLE
    else:
        # 跌腿闸门用**语义不同**的字段名（不许与涨腿臂混用）。
        assert gate["avoidance_list_publish_allowed"] is False
        assert gate["verdict"] == SS.VERDICT_NOT_DECIDABLE
    # ⇒ 归属块在，且状态是「不可用」。
    assert gate["signal_scope"]["status"] in (
        SS.STATUS_MISSING_SIGNAL_ID_COLUMN, SS.STATUS_NO_MATCHING_SIGNAL)
    assert gate["attribution"]["basis"] == SS.BASIS_NONE
    assert doc["attribution"]["basis"] == SS.BASIS_NONE


def test_g3_the_d1_gate_measures_only_the_attributed_rows(tmp_path):
    """**端到端**：两个权重指纹各一段 ⇒ 闸门**只**测当前指纹那一段。

    ⇒ 这是「归属真的接在调用点上」的直接证据：
    产物里的 `n_rows_total` 必须等于**归属内**的行数，
    **不是**整本底稿的行数。
    """
    fp = _current_weights_fingerprint()
    df = _ledger(n_windows=4, per_day=10)
    df = _with_weights_hash(df, [fp, fp, fp, "OTHER_FP"])
    ledger = _write_ledger(df, tmp_path / "verification.parquet")
    out = tmp_path / "gate.json"
    p = _run("score_ranking_power.py",
             "--verification", str(ledger),
             "--reports-dir", str(PROD_REPORTS),
             "--out", str(out))
    assert p.returncode == 0, p.stderr[-2000:]
    doc = json.loads(out.read_text(encoding="utf-8"))
    scope = doc["signal_scope"]
    assert scope["status"] == SS.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH, scope
    assert scope["rows_in_scope"] == 60, scope
    assert scope["rows_other_signals"] == 20, scope
    assert doc["attribution"]["basis"] == SS.BASIS_WEIGHTS_HASH
    assert doc["attribution"]["current_weights_fingerprint"] == fp
    # ⇒ 判决读到的行数 = **归属内**的行数，不是整本底稿的。
    measured = doc["by_score_col"]["score"]["n_rows_total"]
    assert measured == 60, (
        f"闸门读了 {measured} 行，而归属内只有 60 行 ⇒ "
        "归属**没有**接在调用点上（scope 那一层是装饰）")


def test_g3_the_real_ledger_is_attributed_to_the_current_weights(tmp_path):
    """**真底稿**：它的 `weights_hash` 那一列的值必须**逐行**对上当前指纹。

    🚨 这条是**只读**的（不写任何生产产物）⇒ 它是「今天到底归属到多少行」
    的**可核对**答案。
    """
    src = PROD_REPORTS / "verification.parquet"
    if not src.is_file():
        pytest.skip("verification.parquet 尚未生成")
    fp = _current_weights_fingerprint()
    df = pd.read_parquet(src)
    scoped = SS.scope_ledger(df, None, fp)
    assert scoped.has_evidence, "真底稿**没有**任何归属列？"
    assert scoped.coverage["ledger_rows_total"] == len(df)
    assert scoped.coverage["rows_in_scope"] == int(
        (df["weights_hash"].astype("string").str.strip() == fp).sum())
    assert scoped.coverage["rows_in_scope"] > 0, "真底稿一行都归属不上？"


# ——————————————————————————————————————————————————————————————
# G7 上游契约守卫
# ——————————————————————————————————————————————————————————————

def test_g7_an_upstream_without_a_scope_block_is_refused():
    """上游产物**没有** `signal_scope` 块 ⇒ **拒绝外推**。

    🚨 「块缺失」**不许**回落成「按上游的老口径继续用」⇒ 那是第 6 种形状。
    """
    res = SS.upstream_scope_verdict({"verdict": "POWERLESS", "n_blocks": 5},
                                     "sigA")
    assert res["ok"] is False
    assert "signal_scope" in res["reason"]


def test_g7_an_upstream_that_is_not_usable_is_refused():
    """上游的状态不可用 ⇒ **拒绝外推**（上游本身就不可判定）。"""
    upstream = {"signal_scope": {"status": SS.STATUS_MISSING_SIGNAL_ID_COLUMN,
                                 "current_signal_id": None}}
    res = SS.upstream_scope_verdict(upstream, "sigA")
    assert res["ok"] is False
    assert SS.STATUS_MISSING_SIGNAL_ID_COLUMN in res["reason"]


def test_g7_an_upstream_from_another_signal_is_refused():
    """上游是**别的信号**的判决 ⇒ **拒绝外推**。"""
    upstream = {"signal_scope": {"status": SS.STATUS_OK,
                                 "current_signal_id": "sigOTHER"}}
    res = SS.upstream_scope_verdict(upstream, "sigA")
    assert res["ok"] is False
    assert "sigOTHER" in res["reason"]


def test_g7_an_upstream_with_a_different_weights_fingerprint_is_refused():
    """信号 id 不可比 ⇒ 退到**权重指纹**；权重指纹也不一样 ⇒ 拒绝外推。"""
    upstream = {"signal_scope": {
        "status": SS.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH,
        "current_signal_id": None,
        "current_weights_fingerprint": "OTHER_FP"}}
    res = SS.upstream_scope_verdict(upstream, None, "c2178335be6d")
    assert res["ok"] is False
    assert "OTHER_FP" in res["reason"]


def test_g7_two_unknown_identities_are_not_read_as_equal():
    """🚨 **两个都算不出 ⇒ 拒绝外推**（不许把「两个都是 None」读成「一样」）。

    ⇒ 「查不到 ≠ 没有」（第 123 层）。
    """
    upstream = {"signal_scope": {
        "status": SS.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH,
        "current_signal_id": None,
        "current_weights_fingerprint": None}}
    res = SS.upstream_scope_verdict(upstream, None, None)
    assert res["ok"] is False
    assert "不可知" in res["reason"]


def test_g7_an_upstream_from_the_same_signal_is_accepted():
    """正控：同信号的、状态可用的上游**必须**被接受（否则守卫恒真）。"""
    upstream = {"signal_scope": {"status": SS.STATUS_OK,
                                 "current_signal_id": "sigA"}}
    res = SS.upstream_scope_verdict(upstream, "sigA")
    assert res["ok"] is True, res["reason"]


@pytest.mark.parametrize("script", INDIRECT_CONSUMERS, ids=list(INDIRECT_CONSUMERS))
def test_g7_an_indirect_consumer_refuses_to_extrapolate_from_a_foreign_upstream(
        script, tmp_path):
    """两个**间接**消费者：上游归属不对 ⇒ **拒绝出结论**。

    ⚠️ 它们**不读底稿** ⇒ 只能靠上游自报 ⇒ 这条守卫验的正是
    「上游契约真的被接线」，不是「函数存在」。
    """
    upstream = {
        "verdict": "POWERLESS",
        "n_blocks": 5,
        "n_pred_dates": 5,
        "abs_mean_over_mde": 0.4,
        "t_stat": 0.9,
        "ci_method": "normal_approx_z=1.96",
        "blocks": [],
        "sensitivity_student_t": {"t_crit_95": 2.02},
        "source_parquet": "/nowhere/verification.parquet",
        "source_parquet_sha256": "0" * 64,
        # 🚨 上游自报：**别的信号**。
        "signal_scope": {"status": SS.STATUS_OK, "current_signal_id": "sigOTHER"},
    }
    up = tmp_path / "upstream.json"
    up.write_text(json.dumps(upstream, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / f"{script}.json"

    if script == "forward_d2_horizon.py":
        argv = ["--data-root", str(tmp_path), "--upstream", str(up),
                "--out", str(out)]
    else:
        argv = ["--src", str(up), "--out", str(out),
                "--reports-dir", str(PROD_REPORTS)]
    p = _run(script, *argv)
    assert p.returncode == 0, f"{script} 退出码 {p.returncode}\n{p.stderr[-2000:]}"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == SS.VERDICT_NOT_DECIDABLE, doc.get("verdict")
    # ⇒ 拒绝外推 ⇒ **不许**出现那些外推量。
    assert "scenarios" not in doc, "🚨 拒绝外推却仍然写了外推表"
    assert "horizon" not in doc, "🚨 拒绝外推却仍然写了反解梯子"
