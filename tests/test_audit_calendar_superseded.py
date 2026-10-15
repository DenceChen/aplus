"""审计副本已作废：脚本、快照、数据三者已经不对应（第 126 层）。

## 这条守卫在防什么

2026-10-14 11:17，`scripts/audit_calendar/extract_calendar.py` 跑出了
`db_calendar.json`，回答「回测面板用的哪一只参考股」。
它回答的是 `ref = max(daily_map, key=len)` —— **生产当时确实这么干**。

2026-10-15 两件事同时发生：

1. **生产改了。** `panel.py::window_dates` 改走 `market_date_axis`（全体并集），
   对空窗口抛 `ValueError`。⇒ 「面板的参考股」**这个对象今天不存在**。
2. **数据改了。** 修复把 `000043.SZ` 从 221 行补到 6127 行。⇒ 那个
   `max(key=len)` 今天选中的**换人了**。

于是：**提交在仓里的脚本，已经无法重现提交在仓里的 JSON。**
快照写着 `ref = 000001.SZ / 1884 行`，今天跑同一个脚本会得到
`000043.SZ / 6127 行`（`000001.SZ` 自己也 1884 → 1885，补了一个交易日）。

**当时没有任何守卫会发现这件事** —— 快照读出来永远是合法的 JSON，
数字看着完全正常，没有报错。

★ 这正是「读快照的守卫等于没守」的另一个面：产物一落盘，代码与产物的关系就断了。
区别是这次断的不是**代码**，是**输入** ⇒ 即使代码一个字没动，快照也会过期。

## 为什么不删

第 25 层：撤回已提交结论需要独立判决，不是顺手做的。
而且底层数据（`miss_by_stock.json` 的停牌/缺口普查）**并没有因此作废**，
它只是不再回答「谁当 ref」这个问题。所以这里做的是**把失效变成显式拒绝**：
不带 `--ack-superseded`，两个脚本一律 rc=2。

## 守卫为什么是「成对」的

最后一条守卫把闸门的严格程度**绑定在前提上**：前提若哪天复活（生产又用
`max(...)` 当日期轴），闸门**必须自己松开**，否则它就变成了一条
「过严的守卫」—— 而过严的守卫和恒真的一样有害：它是绿的，会把人推向
「去改本来正确的代码」。

⇒ 这条守卫不是「闸门必须在」，是「**闸门当且仅当前提为假时必须在**」。
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit_calendar"
SUPERSEDED = ["extract_calendar.py", "ref_risk.py"]


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    """用本仓 venv 的解释器跑，避免 `python` 可能指向别处。"""
    return subprocess.run(
        [sys.executable, str(AUDIT / script), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )


def _selects_a_ref_by_row_count(source: str) -> bool:
    """这段源码里是否**真的在代码里**用 `max(..., key=…)` 挑代表。

    ⚠️ 不能用 `'max(daily_map' in source` —— `panel.py` 的 docstring 和
    报错信息里都**逐字提到**这句（670 / 716 行），那是在讲**已经改掉的历史**。
    用字面量 grep 会把「历史说明」读成「当前实现」，这跟第 123 层
    「用文件名反推生成器名」是同一族错误：拿文本匹配的命中当成结构事实。

    ⚠️⚠️ 也不能只认「有没有 `max()` 调用」—— 我**第一版就是这么写的**，
    结果 `panel.py:218` 那个算 OHLCV 列宽的 `max(len(x) for x in …)` 把
    前提判成了「还活着」⇒ 下面两条成对守卫**一直在 skip**，而 pytest 把
    skip 显示得和「前提为假所以跳过」一模一样 ⇒ **守卫失效时外观完全正常**。
    这就是「读快照的守卫等于没守」的第三个面。

    ⇒ 谓词必须只认 `max(..., key=…)` 这一种形态：`key=` 才是「按键挑最大」
    的签名；不带 `key=` 的 `max(...)` 是「逐元素求最大」，与日期轴无关。
    """
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "max"
        and any(kw.arg == "key" for kw in node.keywords)
        for node in ast.walk(tree)
    )


def _panel_still_picks_a_ref_by_row_count() -> bool:
    return _selects_a_ref_by_row_count((ROOT / "aplus" / "panel.py").read_text())


# ── ① 前提检测器本身要被测 ────────────────────────────────────────────

def test_the_premise_detector_discriminates_and_is_not_vacuous():
    """检测器是一个**判决**，所以判决要被测。

    没有这一条，上面那条成对守卫可以悄悄退化成一个恒假谓词 + 恒真 skip，
    而一切看起来正常（6 passed / 2 skipped）。
    """
    # 前提为真：认得出来
    assert _selects_a_ref_by_row_count("ref = max(daily_map.values(), key=len)")
    # 前提为假：无 key= 的 max 是逐元素求最大，不是「挑代表」
    assert not _selects_a_ref_by_row_count(
        "w = max(len(x) for x in (o, high, low, close))")
    # 前提为假：docstring 里逐字提到也不算数（第 123 层族）
    assert not _selects_a_ref_by_row_count(
        '"""原实现是 `ref = max(daily_map.values(), key=len)`。"""\n'
        "def f():\n    return 1\n")


def test_the_premise_is_currently_false_so_the_gate_is_currently_required():
    """把当前事实钉住：**今天的生产确实不用 `max(key=…)` 挑代表。**

    ⚠️ 如果哪天这条红了，先读 `aplus/panel.py` 是不是真的改回了 `max(key=…)`。
    **不要**为了让成对守卫好过就把这条改绿 —— 前提复活时闸门该撤，不是该被绕过。
    """
    assert not _panel_still_picks_a_ref_by_row_count()


# ── ① 作废闸门：不承认就不许跑 ────────────────────────────────────────

@pytest.mark.parametrize("script", SUPERSEDED)
def test_the_superseded_audit_refuses_to_run_without_an_explicit_ack(script):
    proc = _run(script)
    assert proc.returncode == 2, f"{script} 不该在没有 ack 时跑起来"
    err = proc.stderr
    assert "作废" in err, "拒绝必须说清**哪一半作废**，否则人只会当成环境坏了"
    assert "--ack-superseded" in err, "拒绝必须给出出口，否则它是死路"


@pytest.mark.parametrize("script", SUPERSEDED)
def test_the_refusal_does_not_overwrite_the_committed_snapshot(script, monkeypatch):
    """拒绝必须发生在**任何写盘之前**。

    ⚠️ 这条不是装饰：如果闸门放在 `json.dump` 之后，脚本会先覆盖掉
    `db_calendar.json` / `miss_by_stock.json`，再打印「已作废」——
    那等于**用一条警告换走了原始证据**。
    """
    snapshot = AUDIT / "db_calendar.json"
    before = snapshot.read_bytes()
    mtime = snapshot.stat().st_mtime
    proc = _run(script)
    assert proc.returncode == 2
    assert snapshot.read_bytes() == before
    assert snapshot.stat().st_mtime == mtime


# ── ② 快照已经重现不出来了（这才是真问题） ────────────────────────────

def test_the_committed_snapshot_names_a_stock_the_script_no_longer_picks():
    """不是「脚本改了」，是**输入变了**：脚本一个字没动，选中也换了人。"""
    snapshot = json.loads((AUDIT / "sample_frame.json").read_text())
    pyarrow_parquet = pytest.importorskip("pyarrow.parquet")

    rows = {}
    for p in sorted((ROOT / "data" / "master" / "daily").glob("*.parquet")):
        try:
            rows[p.stem] = pyarrow_parquet.ParquetFile(p).metadata.num_rows
        except Exception:  # noqa: BLE001
            continue
    assert rows, "daily 目录读不出行数 —— 守卫没测到东西"

    today_pick = max(rows, key=lambda c: (rows[c],))
    assert snapshot["ref"] != today_pick, (
        "今天 `max(key=len)` 选中的又是 {snap[ref]} 了 —— 若这是因为数据又被修过，"
        "本条守卫必须先被重新审：它的前提是「快照 ≠ 今天」".format(
            snap=snapshot)
    )


def test_the_snapshot_row_count_is_stale_for_the_stock_it_names():
    """哪怕选中的还是同一只，它记的行数也已经过期。"""
    snapshot = json.loads((AUDIT / "sample_frame.json").read_text())
    pyarrow_parquet = pytest.importorskip("pyarrow.parquet")

    path = ROOT / "data" / "master" / "daily" / f"{snapshot['ref']}.parquet"
    if not path.is_file():
        pytest.skip(f"{snapshot['ref']} 已不在库里")
    now = pyarrow_parquet.ParquetFile(path).metadata.num_rows
    assert now != snapshot["ref_rows"], (
        f"{snapshot['ref']} 快照记 {snapshot['ref_rows']} 行，今天是 {now} 行。"
        " 守卫第一次见到「仍然相等」时，先确认数据是不是又变了，"
        "**别直接改断言让它变绿**"
    )


# ── ③ 闸门的严格程度必须绑在前提上 ────────────────────────────────────

@pytest.mark.parametrize("script", SUPERSEDED)
def test_the_gate_is_exactly_as_strict_as_the_premise_is_false(script):
    """前提若复活，闸门**必须自己松开**。

    一条永远为真的守卫（无论前提如何都要求 rc==2）同样有害：
    它看起来在守东西，实际只会在生产回归时挡住正确的工作。
    """
    if _panel_still_picks_a_ref_by_row_count():
        pytest.skip("生产又用 max(...) 当日期轴了 ⇒ 前提复活 ⇒ 闸门应当被撤掉")
    assert _run(script).returncode == 2
