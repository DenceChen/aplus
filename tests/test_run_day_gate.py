"""交易日闸的守卫：非交易日一个字都不发布（2026-10-17）。

## 为什么这道闸存在
`com.aplus.daily.plist` 的 `StartCalendarInterval` **只有 `Hour=18, Minute=30`，
没有 `Weekday`** ⇒ 每天跑，含周六周日。
而 `run_daily._pipeline` 原本只有 `today = date.today().isoformat()`，零交易日校验。

实测已犯 3 次：2026-10-10(六) / 10-11(日) 各出了一份完整 10 涨 10 跌，
三天名单**全部映射到同一个 `(entry, exit)`** ⇒ 前瞻底稿被重复名单污染。

## 🚨 本文件所有涉及写盘的测试一律用 `tmp_path`
写这份文件时我当场犯了 CLAUDE.md 第 132 层：探针里传了生产根 `'data'`，
`run_day_gate` 把真的 `data/reports/run_status.json` 覆盖掉了
（已 `git checkout HEAD --` 逐位恢复）。
⇒ 判据不许挂在人的记忆上：**测试的写盘路径只能是 `tmp_path`。**

## 🚨 绝不跑 `run_daily.py::main()` 验证
那是 20 分钟的流水线，且会真的出名单。判据必须是**可问**的
（CLAUDE.md 第 124 层）⇒ 这里只 import 模块 + 调模块级 `run_day_gate`。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.trading_calendar import (  # noqa: E402
    RUN_DAY_LABELS, RUN_DAY_TRADING, RUN_DAY_WEEKEND,
    RUN_DAY_WORKDAY_NOT_IN_STORE, RUN_DAY_TRADING_DAY_PARTIAL,
    Coverage, classify_run_day,
)


# ── A. 判据本身：纯函数，三态 ─────────────────────────────────────

# 2026-10-16 是实测存在的最后一个交易日（`trading_days()` 的 max）。
IN_STORE = "2026-10-16"


def test_a_day_in_the_store_is_a_trading_day():
    assert classify_run_day(date(2026, 10, 16), {IN_STORE}) == RUN_DAY_TRADING


@pytest.mark.parametrize("day", [date(2026, 10, 10), date(2026, 10, 11)])
def test_a_weekend_absent_from_the_store_is_a_weekend(day):
    assert classify_run_day(day, {IN_STORE}) == RUN_DAY_WEEKEND


def test_a_workday_absent_from_the_store_is_not_silently_a_weekend():
    """周一不在仓里 ⇒ **不是** WEEKEND。

    这条是最贵的分岔：把它判成 WEEKEND 会让「采集失败」静悄悄变成
    「今天不发布」，而两种误判的代价差一个数量级
    （错过一个交付日 vs 一个交付日凭空消失且零告警）。
    """
    assert (date(2026, 10, 19).weekday(),
            classify_run_day(date(2026, 10, 19), {IN_STORE})
            ) == (0, RUN_DAY_WORKDAY_NOT_IN_STORE)


def test_a_three_states_are_mutually_exclusive_and_all_reachable():
    """防「三态塌成两态」：`.get()` 返回 None 时 `None is not None` 为假
    ⇒ 缺状态被当成「测过了、没问题」（第 120 层）。"""
    reached = {
        classify_run_day(date(2026, 10, 16), {IN_STORE}),
        classify_run_day(date(2026, 10, 10), {IN_STORE}),
        classify_run_day(date(2026, 10, 19), {IN_STORE}),
    }
    assert reached == {RUN_DAY_TRADING, RUN_DAY_WEEKEND,
                       RUN_DAY_WORKDAY_NOT_IN_STORE}


def test_every_state_has_a_label_that_says_it_out_loud():
    """🚨 `WORKDAY_NOT_IN_STORE` 的标签必须**明说与法定节假日分不开**。

    这是第 176 层「文档/文案承担语义」的形态：那句「仓里没有任何东西能
    区分法定节假日与采集失败」是这条判据**唯一**的诚实性来源，
    它只存在于这个字符串里。删掉它而代码不动，判决仍全绿。
    """
    label = RUN_DAY_LABELS[RUN_DAY_WORKDAY_NOT_IN_STORE]
    assert "节假日" in label and "采集失败" in label
    # ⚠️ 2026-10-20：第四态 `TRADING_DAY_PARTIAL` 加进来了（覆盖率判据）。
    #    三个**旧**状态的文案与语义一个字没改 —— 这里只多认一档。
    assert set(RUN_DAY_LABELS) == {RUN_DAY_TRADING, RUN_DAY_WEEKEND,
                                   RUN_DAY_WORKDAY_NOT_IN_STORE,
                                   RUN_DAY_TRADING_DAY_PARTIAL}


# ── B. 闸的副作用：写盘路径只能是 tmp_path ────────────────────────

def _load_run_daily():
    spec = importlib.util.spec_from_file_location(
        "run_daily_under_test", ROOT / "scripts" / "run_daily.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _NullRun:
    def log(self, *a, **k):        # noqa: ANN001 — 探针，不该有签名
        pass


@pytest.fixture(scope="module")
def RD():
    return _load_run_daily()


@pytest.fixture
def cal(RD, monkeypatch, tmp_path):
    """把 `trading_days` **与** `coverage_detail` 换成同一份合成仓。

    ⚠️ 合成而非真实：真实日历要读 40 个 parquet（0.27 s）且
    **明天就会变**（仓里每天推进）⇒ 钉在它上面等于钉在一个每天漂移的
    事实上，而「日历内容」不是这道闸要守的性质（性质是**三态分流**）。

    🚨 2026-10-20：**两个口径必须来自同一个仓**。
    闸门接上覆盖率之后，`classify_run_day` 的四态判据里
    **覆盖率取代了二值问题**（`coverage` 不为 `None` 时 `known` 不参与判决）
    ⇒ 只换掉 `trading_days` 会让判据**一半读合成仓、一半读真仓**
    ⇒ 两端口径不同源（真仓的覆盖率会把「不在库里」判成「在库里」，
    而这个错**不报错**、只让守卫生效）。
    ⇒ 这里**两个都换**，合成的仓语义统一为「只含 2026-10-16」。
    """
    monkeypatch.setattr(RD, "trading_days", lambda: {IN_STORE})

    def _coverage(day, *a, **k):
        # 合成仓里只有 2026-10-16 有数据 ⇒ 那天满格，其余为 0
        c = 1.0 if str(day) <= IN_STORE else 0.0
        return Coverage(c, 1 if c else 0, 1)

    monkeypatch.setattr(RD, "coverage_detail", _coverage)
    return tmp_path


def _status(root: Path) -> dict:
    import json
    return json.loads((root / "reports" / "run_status.json").read_text())


def test_b_a_weekend_exits_zero_and_records_why(RD, cal):
    rc, verdict = RD.run_day_gate("2026-10-17", str(cal), run=_NullRun())
    assert (rc, verdict) == (0, RUN_DAY_WEEKEND)
    assert _status(cal)["status"] == "skipped"


def test_b_a_workday_absent_from_the_store_exits_two(RD, cal):
    """rc=2 而非 0：「仓里没有这一天」必须有人来看。"""
    rc, verdict = RD.run_day_gate("2026-10-19", str(cal), run=_NullRun())
    assert (rc, verdict) == (2, RUN_DAY_WORKDAY_NOT_IN_STORE)


def test_b_a_partially_covered_workday_is_refused_and_says_how_much(RD, cal,
                                                                   monkeypatch):
    """🚨 第四态：工作日、仓里**部分**股票有今天的数据 ⇒ 拒绝发布。

    ## 为什么这一态必须单独存在
    「仓里只采到一半」与「仓里完全没今天」在产物里**长得一样**
    ⇒ 两者都可能被读成「今天不是工作日」而**继续出一张残缺榜**。
    一张残缺榜比没有榜更坏：缺掉的那些票拿不到今天的因子 ⇒ 它们不在
    横截面里 ⇒ 横截面被悄悄缩小 ⇒ **名次变了而没有任何东西会报**。

    ## 为什么必须报出分子分母
    只报「50%」的话，读者无法判断是「1/2 只」还是「95/200 只」
    ⇒ `outcome_reason` 里必须带**百分比**。
    """
    monkeypatch.setattr(RD, "coverage_detail",
                        lambda day, *a, **k: Coverage(0.5, 100, 200))
    rc, verdict = RD.run_day_gate("2026-10-19", str(cal), run=_NullRun())
    assert (rc, verdict) == (2, RUN_DAY_TRADING_DAY_PARTIAL)
    st = _status(cal)
    assert st["outcome"] == "partial_coverage"
    assert "50.0%" in st["outcome_reason"]
    # 闸门拦下时**一个字都不许发布**（沿用周末那条的纪律）
    published = [p.name for p in (cal / "reports").rglob("*")
                 if p.is_file() and "run_status" not in p.name]
    assert published == []


def test_b_the_gate_reads_both_calibers_from_the_same_store(RD, monkeypatch, tmp_path):
    """🚨 覆盖率判据**取代**了二值问题 ⇒ 两个口径必须同源。

    若闸门从真仓取覆盖率、而日历取合成仓（或反过来），则
    「日历说不在库里、覆盖率说满格」那个格子里会出现**互相矛盾**的读数
    ⇒ 判决由**另一个仓**决定 ⇒ 而这个错**不报错**、只让守卫生效。
    """
    calls = {"td": 0, "cov": 0}

    def _td(*a, **k):
        calls["td"] += 1
        return {IN_STORE}

    def _cov(day, *a, **k):
        calls["cov"] += 1
        return Coverage(1.0, 1, 1)

    monkeypatch.setattr(RD, "trading_days", _td)
    monkeypatch.setattr(RD, "coverage_detail", _cov)
    rc = RD.run_day_gate(IN_STORE, str(tmp_path), run=_NullRun())
    assert rc is None                      # 满覆盖 ⇒ 正常发布
    assert calls["td"] == 1 and calls["cov"] == 1, (
        f"两个口径必须都被读一次：td={calls['td']} cov={calls['cov']}"
        " —— 少读一个就是「判据只跑了一半」")


def test_b_a_trading_day_returns_none_and_writes_nothing(RD, cal):
    assert RD.run_day_gate(IN_STORE, str(cal), run=_NullRun()) is None
    assert not (cal / "reports" / "run_status.json").exists()


def test_b_a_skipped_day_publishes_nothing_at_all(RD, cal):
    """🚨 交付形态闸：周末跑完闸之后，`reports/` 里**不许出现任何名单**。

    2026-10-10/11 的实际后果是三份重复名单进了 `verification.parquet`
    ⇒ 前瞻判决被污染。这里钉的是「一个字都不写进 predictions/verification」。
    """
    RD.run_day_gate("2026-10-17", str(cal), run=_NullRun())
    published = [p.name for p in (cal / "reports").rglob("*")
                 if p.is_file() and "run_status" not in p.name]
    assert published == []


def test_b_the_two_rejections_really_diverge(RD, cal):
    """正控：上面两条的夹具若哪天不再分岔，它们会**一起**恒绿。"""
    weekend = RD.run_day_gate("2026-10-17", str(cal), run=_NullRun())
    workday = RD.run_day_gate("2026-10-19", str(cal), run=_NullRun())
    assert weekend != workday
    assert RUN_DAY_TRADING not in (weekend, workday)


def test_b_the_gate_never_defaults_its_data_root(RD):
    """`data_root` **不许有默认值**。

    给了默认值，某次调用就会静悄悄用上「生产目录」——写这份守卫时
    我就是这么把真的 `run_status.json` 覆盖掉的（第 132 层）。
    ⇒ 少传参数必须 `TypeError`。
    """
    with pytest.raises(TypeError):
        RD.run_day_gate("2026-10-17", run=_NullRun())      # type: ignore[call-arg]


# ── C. 接线层：闸的位置是**承重**的 ───────────────────────────────

def _pipeline_body() -> ast.FunctionDef:
    tree = ast.parse((ROOT / "scripts" / "run_daily.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_pipeline":
            return node
    raise AssertionError("run_daily.py 里已经没有 _pipeline 了 —— "
                         "解析器或代码变了，先搞清楚是哪种再改判据")


def _dotted(node: ast.expr) -> str | None:
    """`run_day_gate` → `"run_day_gate"`；`subprocess.run` → `"subprocess.run"`。

    🚨 第一版这里取的是 `node.func.attr`，于是 `subprocess.run(...)` 拿到的是
    `"run"` 而探针返回 `None` ⇒ **探针坏了的输出与「代码没接线」逐位同形**。
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _call_line(fn: ast.FunctionDef, callee: str, first_arg: str | None = None):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func) != callee:
            continue
        if first_arg is not None:
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value != first_arg:
                continue
        return node.lineno
    return None


def test_c_the_probe_really_finds_all_three_call_sites():
    """正控（第 186 层）：AST 探针坏了会**永远绿**，症状与「守住了」同形。"""
    fn = _pipeline_body()
    assert _call_line(fn, "run_step", "update") is not None
    assert _call_line(fn, "run_step", "verify") is not None
    assert _call_line(fn, "run_day_gate") is not None


def test_c_the_gate_runs_after_the_update_and_before_the_verify():
    """🚨 **这是全文件最承重的一条断言。**

    闸若放在 `step_incremental_update` **之前**，则每个真正的交易日
    （例：周一 18:30，库最新停在上周五）都会得到「工作日不在库里」
    ⇒ rc=2 ⇒ **合法交易日反而一天都不出单**。
    症状是「什么都不发布」，而那与「周末不发布」**逐位同形**。
    """
    fn = _pipeline_body()
    update = _call_line(fn, "run_step", "update")
    gate = _call_line(fn, "run_day_gate")
    verify = _call_line(fn, "run_step", "verify")
    assert update < gate < verify, (
        f"闸的位置错了：update@{update} / gate@{gate} / verify@{verify}。"
        "闸必须在 update 之后（更新正是让今天成为仓里交易日的那一步），"
        "且必须在 verify 之前（verify 会写前瞻底稿）。")


def _guard_daily_main() -> ast.FunctionDef:
    tree = ast.parse((ROOT / "scripts" / "guard_daily.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("guard_daily.py 里没有 main")


def test_c_the_1930_guard_also_has_the_gate():
    """🚨 形状 17 的第二处调用点：`guard_daily` 19:30 会**自动补跑**
    `run_daily.py`，而它的判据是 `status.date != today`。

    ⇒ 只修 `run_daily`，19:30 会把周末那一次**再触发一遍**。
    """
    fn = _guard_daily_main()
    assert _call_line(fn, "classify_run_day") is not None


def test_c_the_1930_guard_checks_the_day_before_it_can_spawn_run_daily():
    """顺序也是承重的：闸必须在 `subprocess.run` **之前**，
    否则它判出「周末」时已经把孩子起了。"""
    fn = _guard_daily_main()
    gate = _call_line(fn, "classify_run_day")
    spawn = _call_line(fn, "subprocess.run")
    assert gate is not None and spawn is not None
    assert gate < spawn


def _raises_system_exit(body: list) -> bool:
    return any(isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
               and _dotted(n.exc.func) == "SystemExit" for n in body)


def test_c_the_gate_result_is_raised_under_exactly_the_none_check():
    """🚨 「闸接上了」与「闸接上了**并且拦得住**」是两个命题。

    `if _gated is not None:` 改成 `if False and ...` 时，调用点还在（位置守卫绿）、
    `raise SystemExit` 还在（存在性守卫绿）、返回值仍被算出来（B 组直接调它，也绿）
    ⇒ 没有任何东西会响，而闸已经不拦了。

    ⇒ 钉的是**条件本身的形状**：它必须是一个 `is not None` 的比较，
    而不是任何能把退出变成空操作的东西。

    ⚠️ 刻意**不钉左侧变量名** —— 钉拼写会让一次良性重命名变红，
    而过严的守卫和恒真的一样有害（它会把人推向「改本来正确的代码」）。
    """
    fn = _pipeline_body()
    gate = _call_line(fn, "run_day_gate")
    guards = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.If) and _raises_system_exit(n.body)):
            continue
        t = n.test
        guards.append((n.lineno, ast.unparse(t),
                       isinstance(t, ast.Compare) and len(t.ops) == 1
                       and isinstance(t.ops[0], ast.IsNot)
                       and isinstance(t.comparators[0], ast.Constant)
                       and t.comparators[0].value is None))
    assert any(gate < ln and ok for ln, _, ok in guards), (
        f"闸的 raise 不在「`x is not None`」这个条件之下 —— gate@{gate}，"
        f"found={guards}。闸存在但不拦，比没有闸更难发现。")


def test_c_the_1930_guard_only_exempts_the_weekend():
    """🚨 **只许豁免 `WEEKEND`**，一个不多。

    `WORKDAY_NOT_IN_STORE` 恰恰是 19:30 这个守卫要去修的缺陷（采集失败）
    ⇒ 把它也豁免掉，等于「让守卫去跳过它本该修的缺陷」。
    而这是**看起来最像正确修法**的一个改法（两个调用点看起来不一致 ⇒
    统一它们），所以必须用语法位置钉死，不能靠读注释。
    """
    fn = _guard_daily_main()
    targets = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and _dotted(node.left) == "verdict":
            targets |= {ast.unparse(c) for c in node.comparators}
    assert targets == {"RUN_DAY_WEEKEND"}, (
        f"guard_daily 拿 verdict 比的东西不是 RUN_DAY_WEEKEND，是 {targets}。")
    # ⚠️ 这里比的是**标识符**（代码里的真名），不是运行期的值
    #    （`RUN_DAY_WEEKEND == "WEEKEND"`）—— 两者是不同的东西，
    #    第一版拿值去比标识符，当场红。


def test_c_neither_call_site_imports_a_second_calendar_implementation():
    """两份实现并存 = 形状 17（同一语义被处理 N−1 次）的**瞬时态**。

    搬家当天这是活的：新模块已建而旧定义还在，模块内后定义者**遮蔽**
    了 import 进来的那个 —— 不报错、不告警，新模块只是没人用的死代码。
    """
    gv = (ROOT / "scripts" / "guard_vintage.py").read_text(encoding="utf-8")
    tree = ast.parse(gv)
    assert not [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "trading_days"]
    assert not [n for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "_CAL"]
    rd = ast.parse((ROOT / "scripts" / "run_daily.py").read_text(encoding="utf-8"))
    imported = {(a.name if isinstance(n, ast.ImportFrom) else a.asname)
                for n in ast.walk(rd) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names}
    assert "trading_days" in imported
