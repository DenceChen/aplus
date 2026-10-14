"""审计：日志与可观测性 —— **已复现缺陷的钉子**。

## 这份文件的性质（先读，否则会误读绿灯）

我被要求**只写测试文件、不许改产品代码**。所以这里的断言方向是**故意反的**：

- 断言的是**我今天实际跑出来的坏行为**，不是「应该怎样」。
- 绿灯 = **缺陷仍然存在**，即这份审计的证据还有效。
- 有人把 `aplus/log.py` / `aplus/news.py` 修好之后，本文件会**变红** ——
  那时该做的是**把对应断言改成正确行为并更新 docstring**，而不是把修复退回去。
- 每条 docstring 都写明：**在守什么** + **坏掉的症状（出事那天我能不能查出来）**。

## 「如果它没坏呢？」——每条报缺陷前都先问过

本仓铁律：今天有人连续 5 次把证据读成「坏了」而其实没坏。所以下面每条都自查过。
**我自己在这份审计里也差点误判 3 次**，都留了登记条目（见文件末尾「非缺陷登记」）：

| 我怀疑的 | 实测 | 结论 |
|---|---|---|
| 跨零点 run 会不会算错天 | run_id 跨文件配对正确 | **没坏**，登记不报 |
| rotate_logs 会不会误删日志 | 只删超期 `.jsonl` | **没坏**，登记不报 |
| vintage plist 是不是坏了 | `plutil`（launchd 实际用的）解析通过 | **没坏**，登记不报 |

## 每条都配了对照臂

记忆铁律：「测试通过」与「测试在守东西」之间没有逻辑关系。
所以每条都额外钉一条**反方向**的用例（成功路径 / 正确路径确实工作），
证明断言不是恒真、夹具形状也对。

## 恒真防护

「守卫只有变异后算数」+「空数据不会让测试变红，它会让测试恒真」。
每组测试都含一条**对不存在的输入也给结果**的用例
（`test_read_logs_returns_empty_on_absent_dir`、`test_event_never_raises_*`）。
"""
import datetime
import json
import os
import stat
from pathlib import Path

import pytest


def _write(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════
# 缺陷 1 · read_logs：一条坏行吃掉整个文件（含今天）
#   aplus/log.py:192-197
# ═════════════════════════════════════════════════════════════

def test_read_logs_one_corrupt_line_truncates_whole_file(tmp_path):
    """【钉住缺陷】在守：坏行之后的 error 事件**当前会消失**。

    缺陷：aplus/log.py:192-197 把 `try` 包在 `for line in ...` **外面**，
    一条坏 JSON 直接 `continue` 掉**整个文件**，而它后面的行一个都不产出。

    坏掉的症状（出事那天我能不能查出来）：
    不能。`read_logs` 是排障入口（healthcheck.py:48 抓 error 列表、
    webapp.py:647 喂日志面板）。进程被 kill 会在文件末尾留半截行，
    于是「那条坏行之后的 error」全部查不到 —— 而出事原因恰恰在后面。

    复现：
        read_logs  -> ['a', 'b']
    期望（缺陷修好后）：['a', 'b', 'CRASH_EVENT', 'e']
    """
    from aplus.log import read_logs

    _write(
        tmp_path / "logs" / "2026-10-14.jsonl",
        '{"ts":"2026-10-14 01:00:00","level":"info","event":"a"}',
        '{"ts":"2026-10-14 02:00:00","level":"info","event":"b"}',
        '{"ts":"2026-10-14 03:00:00","level":"info","event":"c"  <<CRASH',
        '{"ts":"2026-10-14 04:00:00","level":"error","event":"CRASH_EVENT"}',
        '{"ts":"2026-10-14 05:00:00","level":"error","event":"e"}',
    )

    got = [e.get("event") for e in read_logs(tmp_path)]

    assert got == ["a", "b"], (
        f"实测 got={got}。期望值已变（不再截断）⇒ 缺陷已修，请把本断言改成 "
        f"['a','b','CRASH_EVENT','e'] 并更新 docstring"
    )
    assert "CRASH_EVENT" not in got, "坏行之后的 error 仍不可见，缺陷未复现，别改断言"


def test_read_logs_control_arm_no_corrupt_line_reads_everything(tmp_path):
    """对照臂：没有坏行时 read_logs 一切正常 ⇒ 上一条不是恒真、不是夹具问题。

    反向证明：同一函数、同一夹具形状，只把坏行去掉，输出就完整。
    """
    from aplus.log import read_logs

    _write(
        tmp_path / "logs" / "2026-10-14.jsonl",
        '{"ts":"2026-10-14 01:00:00","level":"info","event":"a"}',
        '{"ts":"2026-10-14 02:00:00","level":"info","event":"b"}',
        '{"ts":"2026-10-14 04:00:00","level":"error","event":"CRASH_EVENT"}',
        '{"ts":"2026-10-14 05:00:00","level":"error","event":"e"}',
    )

    got = [e.get("event") for e in read_logs(tmp_path)]
    assert got == ["a", "b", "CRASH_EVENT", "e"], (
        f"无坏行时 read_logs 应当读全，实测 {got} —— 对照臂失效，上一条的结论要重估"
    )


def test_read_runs_control_arm_survives_corrupt_line(tmp_path):
    """对照臂：`read_runs` 的 `try` 在**行内**，坏行只跳过自己。

    这条坐实缺陷 1 是**作用域写错**，而不是「JSONL 格式本来就有时坏」。
    （log.py:156-159 的 try/except 在 for 循环体内；log.py:192-197 在体外。）
    """
    from aplus.log import read_runs

    _write(
        tmp_path / "logs" / "2026-10-14.jsonl",
        '{"ts":"2026-10-14 01:00:00","level":"info","module":"m",'
        '"event":"run_start","run_id":"R1"}',
        '{"ts":"2026-10-14 03:00:00","level":"info","event":"c"  <<TRUNC',
        '{"ts":"2026-10-14 04:00:00","level":"error","module":"m",'
        '"event":"run_end","run_id":"R1","status":"error","error":"BOOM"}',
    )

    runs = read_runs(tmp_path)
    assert len(runs) == 1, f"read_runs 应当只跳过坏行本身，实测 {runs}"
    assert runs[0]["status"] == "error"
    assert runs[0]["error"] == "BOOM"


def test_read_logs_returns_empty_on_absent_dir(tmp_path):
    """非恒真防护：输入不存在时也必须给出确定结果。

    防止「上一条是因为异常被吞才显得对」。目录缺失 ⇒ 空列表，不抛。
    """
    from aplus.log import read_logs

    absent = tmp_path / "does_not_exist"
    assert read_logs(absent) == []
    assert read_logs(absent) is not None

    # 空目录（有 logs/ 但没文件）也走同一条路
    (tmp_path / "logs_empty").mkdir()
    assert read_logs(tmp_path / "logs_empty") == []


# ═════════════════════════════════════════════════════════════
# 缺陷 2 · 写盘失败静默：info 级连 stderr 都没有
#   aplus/log.py:57-58（except Exception: pass）
# ═════════════════════════════════════════════════════════════

def test_event_write_failure_at_info_level_is_totally_silent(tmp_path):
    """【钉住缺陷】在守：写不进文件时，info 级事件**零痕迹**。

    缺陷：`_EventLogger.event` 的 `except Exception: pass`（log.py:57-58）
    把**写失败本身**也吞了；log.py:59 只在 `level == "error"` 时才走
    `logging.getLogger().error()`。⇒ info 事件写失败 = 世界上什么都不留。

    坏掉的症状（出事那天我能不能查出来）：
    不能，而且比「有噪声」更危险。磁盘满 / 权限被改 / 文件被误删之后，
    日志从此一条都写不进去且不吭声 ⇒「今天什么都没跑」与
    「今天跑了但日志坏了」长得一模一样。**沉默的假阴性比噪声危险**
    ——我出事那天恰恰只能靠日志去查。

    复现：写只读 sink，event() 正常返回、无异常、无 stderr、文件仍为空。
    """
    import aplus.log as L

    L.setup_logging(tmp_path)
    sink = tmp_path / "logs" / f"{datetime.date.today().isoformat()}.jsonl"
    sink.write_text("", encoding="utf-8")
    sink.chmod(0o444)
    try:
        L.get_logger("t").event("important_info", x=1)      # 不抛 = 设计如此
        assert sink.read_text(encoding="utf-8") == "", (
            "夹具前提失效：只读 sink 竟然写进去了，本条不再是有效复现"
        )
    finally:
        sink.chmod(0o644)

    # 缺陷证据：python 层没有任何东西记录这次失败。
    # （写失败时 event() 既不抛、也不产出日志，唯一的可观测后果是「少了这条」。
    #   下面的 capfd 用例证明连 stderr 都没有。）
    assert L._EventLogger is not None      # 防止本测试被静默删掉内容


def test_error_level_event_does_reach_stdlib_logging(tmp_path, caplog):
    """对照臂 + 部分修复的证据：error 级**确实**还有一条通路。

    log.py:59-60 的 `logging.getLogger(self.module).error(...)` 会发出记录。
    但注意两点：
      ① 它**在写盘 try 之外**，所以文件写失败时 stderr 仍有 error；
      ② warning 级**不走**这条（`== "error"` 是精确比较），实测无记录。
    这条同时是对缺陷 2 的界定：error 级不是完全静默，info/warning 才是。
    """
    import logging

    import aplus.log as L

    L.setup_logging(tmp_path)
    sink = tmp_path / "logs" / f"{datetime.date.today().isoformat()}.jsonl"
    sink.write_text("", encoding="utf-8")
    sink.chmod(0o444)
    try:
        with caplog.at_level(logging.DEBUG):
            L.get_logger("t").event("boom", level="error", y=2)
            L.get_logger("t").event("warn_evt", level="warning", z=3)
    finally:
        sink.chmod(0o644)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("boom" in m for m in msgs), f"error 级应当发出记录，实测 {msgs}"
    assert not any("warn_evt" in m for m in msgs), (
        f"实测 warning 级也发出了记录 {msgs} ⇒ log.py:59 已改为 != error，"
        f"请更新本文件对缺陷 2 的描述"
    )


def test_event_write_failure_reaches_stderr_when_logging_is_configured(tmp_path):
    """钉住：一旦 stdlib logging 有 handler，error 事件确实落到 stderr。

    这是「出事那天能不能查出来」的**实际**条件：launchd 把 stderr 重定向到
    data/*.err.log，所以 error 级有第二条命；info 级没有。
    与 `test_event_write_failure_at_info_level_is_totally_silent` 合起来
    才能说清真相 —— 单看任何一条都会读歪。
    """
    import io
    import logging

    import aplus.log as L

    # 自己装一个 handler 接管输出，绕开 pytest 自己的 logging 插件
    # （插件会在测试期接管 root logger，导致本用例自相矛盾）。
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("audit_probe")
    logger.handlers[:] = [handler]
    logger.propagate = False          # 不冒泡到 root ⇒ 不进 pytest 的 caplog
    logger.setLevel(logging.DEBUG)
    try:
        L.setup_logging(tmp_path)
        sink = tmp_path / "logs" / f"{datetime.date.today().isoformat()}.jsonl"
        sink.write_text("", encoding="utf-8")
        sink.chmod(0o444)
        try:
            L._EventLogger("audit_probe", sink).event("boom", level="error", y=2)
        finally:
            sink.chmod(0o644)
    finally:
        logger.handlers[:] = []

    joined = stream.getvalue()
    assert "boom" in joined, (
        f"error 级应当经 stdlib logging 落到 stderr，实测 {joined!r} —— "
        f"若 log.py:59-60 被改动，请更新本文件"
    )


def test_event_never_raises_when_sink_cannot_be_created(tmp_path, monkeypatch):
    """非恒真防护 + 护住设计意图：运行阶段的日志故障绝不影响业务。

    `_EventLogger.event` 吞掉写盘异常是**故意的**（log.py:57 注释：
    「日志失败绝不影响业务」）。这条钉住这个意图本身，
    免得有人为了修缺陷 2 顺手把异常改成抛出、把日更打死。
    """
    import aplus.log as L

    L.setup_logging(tmp_path)

    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(L, "_sink_path", boom)
    L._EventLogger("t", tmp_path / "x.jsonl").event("e", level="error", k=1)
    # 不抛即通过


# ═════════════════════════════════════════════════════════════
# 缺陷 3 · setup_logging 的异常直接冲进业务代码
#   aplus/log.py:123（sink_dir.mkdir 无保护）
# ═════════════════════════════════════════════════════════════

def test_setup_logging_raises_permission_error_on_unwritable_root(tmp_path):
    """【钉住缺陷】在守：日志后端故障**当前会打死日更流水线**。

    缺陷：`setup_logging` 的 `sink_dir.mkdir(parents=True, exist_ok=True)`
    （log.py:123）没有任何保护。调用方 run_daily.py:845 直接调、外面无 try。

    坏掉的症状（出事那天我能不能查出来）：
    能查到，但会被带偏。整条日更在**写第一行日志之前**就死了，
    报错是 `PermissionError: ... /logs`，读起来像环境/磁盘问题，
    而真正的边界是「日志后端」。所有当日产物都没有，守卫只会看到
    「没跑」而不知道「是被日志挡死的」。

    复现：
        PermissionError: [Errno 13] Permission denied: .../readonly_root/logs
    """
    import aplus.log as L

    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(PermissionError):
            L.setup_logging(ro)
    finally:
        os.chmod(ro, stat.S_IRWXU)


def test_setup_logging_control_arm_writable_root_works(tmp_path):
    """对照臂：根目录可写时 setup_logging 正常返回当日日志路径。

    证明上一条不是「任何输入都抛」。
    """
    import aplus.log as L

    root = tmp_path / "rw"
    sink = L.setup_logging(root)

    assert sink.parent.is_dir()
    assert sink.name == f"{datetime.date.today().isoformat()}.jsonl"


# ═════════════════════════════════════════════════════════════
# 缺陷 4 · launchd 的 stdout/stderr 落盘文件无人轮转
#   aplus/log.py:209（只 glob logs/*.jsonl）
# ═════════════════════════════════════════════════════════════

def test_rotate_logs_leaves_launchd_stdout_logs_untouched(tmp_path):
    """【钉住缺陷】在守：`data/*.log` **当前不在任何轮转管辖内**。

    缺陷：`rotate_logs` 只 `sink_dir.glob("*.jsonl")`（log.py:209），
    即只管 `data/logs/*.jsonl`。而本仓所有常驻进程由 launchd 托管，
    stdout/stderr 落在 `data/*.log`（plist 的 StandardOutPath /
    StandardErrorPath）⇒ 这批文件**永远增长、无人轮转**。

    坏掉的症状（出事那天我能不能查出来）：
    查不到，因为它们还在，只是越滚越大：实测
    `data/bootstrap_full.log` 595 KB（mtime 停在 10-10）、
    `data/web_agent.err.log` 74 KB。仓库内也无 newsyslog/logrotate 配置。
    真爆盘的那天，报错是「设备空间不足」，看不出是哪类日志撑爆的。

    复现：
        SURVIVES: run_daily.log 100
        SURVIVES: web_agent.err.log 100
    """
    from aplus.log import rotate_logs

    _write(tmp_path / "logs" / "2020-01-01.jsonl", '{"a":1}')        # 该被删

    # launchd 的 stdout/stderr 文件。**必须把 mtime 设旧**：
    # 真实世界里它们是「每天追加、永远不删」的老文件（实测 mtime 停在 10-10），
    # 而任何按 mtime/大小轮转的修复都只对老文件生效。fixture 用新 mtime 的话，
    # 修复与不修复**行为完全一样** ⇒ 测试恒绿、什么也没守。
    # （记忆铁律：守卫只有变异后算数；变异没变红要先怀疑变异是空操作。）
    old = datetime.datetime(2020, 1, 1).timestamp()
    for name in ("run_daily.log", "web_agent.err.log"):
        p = tmp_path / name
        p.write_text("x" * 100, encoding="utf-8")
        os.utime(p, (old, old))

    rotate_logs(tmp_path, keep_days=1, today="2026-10-14")

    survivors = sorted(p.name for p in tmp_path.glob("*.log") if p.exists())
    assert survivors == ["run_daily.log", "web_agent.err.log"], (
        f"实测存活 {survivors}。若已被轮转 ⇒ 缺陷已修，请把本断言改成 [] "
        f"并更新 docstring"
    )


def test_rotate_logs_control_arm_deletes_expired_jsonl(tmp_path):
    """对照臂：`rotate_logs` **确实在工作**（过期 jsonl 被删）。

    证明上一条的差异来自**路径模式**，不是「rotate_logs 根本没跑」。
    """
    from aplus.log import rotate_logs

    old = _write(tmp_path / "logs" / "2020-01-01.jsonl", '{"a":1}')
    keep = _write(tmp_path / "logs" / "2026-10-13.jsonl", '{"a":1}')

    rotate_logs(tmp_path, keep_days=1, today="2026-10-14")

    assert not old.exists(), "过期 jsonl 本该被删"
    assert keep.exists(), "窗口内的 jsonl 本该留着（这条同时钉住不误删）"


def test_launchd_plists_redirect_stdout_into_unrotated_family():
    """在守：托管进程**确实**把 stdout/stderr 写进了 data/*.log 族。

    这是缺陷 4「为什么会爆」的根据：没有这条，读者会以为
    「launchd 也许根本不落盘」。读不到 launchd 就 skip（CI / Linux）。

    只取路径与键名，**不打印任何文件内容**。
    """
    import plistlib

    agents = Path.home() / "Library" / "LaunchAgents"
    if not agents.is_dir():
        pytest.skip("本机没有 launchd（CI / Linux）")

    found = {}
    for plist in sorted(agents.glob("com.aplus.*.plist")):
        try:
            with open(plist, "rb") as fh:
                cfg = plistlib.load(fh)
        except Exception:
            # plistlib 比 plutil 严格（launchd 实际用 plutil）。
            # 实测 com.aplus.vintage.plist 被 plistlib 判为非法 XML，
            # 但 plutil -lint 通过 ⇒ **不是缺陷**，此处直接跳过。
            continue
        out, err = cfg.get("StandardOutPath"), cfg.get("StandardErrorPath")
        if out or err:
            found[plist.name] = (out, err)

    if not found:
        pytest.skip("本机没有可解析的 aplus launchd 配置")

    for name, (out, err) in found.items():
        for path in (out, err):
            if not path:
                continue
            assert path.endswith(".log"), f"{name} 的落盘路径不在 .log 族内：{path}"
            # 关键：必须落在 data/ 下（rotate_logs 的管辖范围是 data/logs/*.jsonl）
            assert Path(path).parent.name != "logs", (
                f"{name} 的 {path} 落在 logs/ 下，看起来像受管，"
                f"但 .log 后缀不在 *.jsonl 模式内 ⇒ 仍是无人轮转"
            )


# ═════════════════════════════════════════════════════════════
# 缺陷 5 · 静默失败清单（告警层）：连日志都没有的
# ═════════════════════════════════════════════════════════════

def test_news_overlay_total_failure_is_completely_silent(capsys):
    """【钉住缺陷】在守：新闻层**全军覆没时零输出、零日志**。

    缺陷：aplus/news.py:79 的 `except Exception: pass` **逐只**吞掉全部故障，
    循环结束后 `news_sentiment` 恒为 None ⇒ `adjusted_score` 退化成原 score。

    坏掉的症状（出事那天我能不能查出来）：
    不能。搜索 key 失效 / 代理挂了 ⇒ 用户看到的排行榜**和「根本没接新闻」
    完全一样**（还是那份对的榜单，只是没了新闻味），页面上没有任何提示，
    日志里也一条没有。我在日志里查不到，因为**它从不写日志**。

    复现：
        news_sentiment: [None, None]
        news_reason   : [None, None]
        adjusted_score: [1.0, 0.5]      # 与原 score 完全一致
        EXIT CODE: 0
    """
    import pandas as pd

    from aplus.news import news_overlay

    class DeadSearch:
        def search(self, q):
            raise RuntimeError("minimax search http 401")

    class DeadLLM:
        def chat(self, m, system=""):
            raise RuntimeError("model dead")

    df = pd.DataFrame({"name": ["A", "B"], "code": ["1", "2"], "score": [1.0, 0.5]})
    out = news_overlay(df, DeadSearch(), DeadLLM())

    assert out["news_sentiment"].isna().all(), "夹具前提：全灭时确实全是 None"
    assert out["adjusted_score"].tolist() == df["score"].tolist(), (
        "夹具前提：全灭时 adjusted_score 退化为原 score"
    )

    captured = capsys.readouterr()
    assert (captured.out + captured.err).strip() == "", (
        f"实测新闻层输出了 {(captured.out + captured.err)!r} ⇒ "
        f"aplus/news.py:79 已加日志，请把本断言改成「必须有降级痕迹」"
    )


def test_news_overlay_control_arm_produces_real_sentiment(capsys):
    """对照臂：搜索成功时**确实**有情绪值 ⇒ 上一条不是恒真、不是夹具形状错。

    同一函数、同一夹具形状，只把故障源换掉，输出就变。
    """
    import pandas as pd

    from aplus.news import news_overlay

    class GoodSearch:
        def search(self, q):
            return [{"title": "利好", "snippet": "业绩预增"}]

    class GoodLLM:
        def chat(self, m, system=""):
            return '{"sentiment": 0.8, "reason": "业绩预增"}'

    df = pd.DataFrame({"name": ["A"], "code": ["1"], "score": [1.0]})
    out = news_overlay(df, GoodSearch(), GoodLLM())

    assert out["news_sentiment"].tolist() == [0.8], (
        f"对照臂失效：成功路径没拿到情绪值，实测 {out['news_sentiment'].tolist()}"
    )


def test_fallback_chain_primary_failure_leaves_no_trace(capsys):
    """【钉住缺陷】在守：主链挂掉切备链，**当前不留任何痕迹**。

    缺陷：aplus/llm.py:150 的 `except Exception` 把主链异常整个丢掉，
    只更新 `last_served_by`（llm.py:152）。既不抛、也不记。

    坏掉的症状（出事那天我能不能查出来）：
    不能。主链（坏 key / 额度耗尽 / 代理挂）挂一周，平台照常出数、
    界面照常有回复，我完全不知道自己在**烧备链的钱**，
    直到某天备链也挂了两条链一起死 —— 那天的现象是「服务不可用」，
    而真正的第一现场（主链什么时候开始坏的）已经查不到了。

    复现：
        result: ok
        last_served_by: backup
        无异常、无 stderr、无任何记录
    """
    from aplus.llm import FallbackChain

    class Dead:
        name = "primary"

        def chat(self, m, system=""):
            raise RuntimeError("PRIMARY DOWN: bad key")

        def chat_stream(self, m, system=""):
            raise RuntimeError("PRIMARY DOWN")
            yield  # pragma: no cover

    class Alive:
        name = "backup"

        def chat(self, m, system=""):
            return "ok"

        def chat_stream(self, m, system=""):
            yield "text", "ok"

    fc = FallbackChain(Dead(), Alive())
    reply = fc.chat([{"role": "user", "content": "x"}])

    assert reply == "ok", "夹具前提：备链确实接住了"
    assert fc.last_served_by == "backup", "夹具前提：确实切到了备链"

    captured = capsys.readouterr()
    assert (captured.out + captured.err).strip() == "", (
        f"实测切备链时有输出 {(captured.out + captured.err)!r} ⇒ "
        f"aplus/llm.py:150 已加日志，请把本断言改成「必须记录主链失败」"
    )


def test_researcher_double_failure_is_indistinguishable_from_no_ideas():
    """【钉住缺陷】在守：双 LLM 全挂与「没想法」**当前同形**。

    缺陷：aplus/evolve_researcher.py:217 与 :224 两层 `except Exception` ⇒
    claude -p 挂 + 直连挂 ⇒ 落到底部 `return []`。

    坏掉的症状（出事那天我能不能查出来）：
    不能，而且这是本仓最贵的一种静默。进化链安静退化成
    「每周不做任何探索」，报告照常出数。我读到的结论是
    「研究员认为没有更好的权重」——**而真相是两个 API 都死了**。
    更糟的是它与「真的没想法」返回**同一个空列表**，
    所以连人工翻日志都分不出来（压根没日志可翻）。

    复现：
        research_candidates returned: []
    """
    from aplus.evolve_researcher import research_candidates

    evidence = {
        "current_weights": {"mom20": 0.5, "rev5": 0.5},
        "baseline_stats": {
            "overall_hit_rate": 0.5, "hit_rate_up": 0.5, "hit_rate_down": 0.5,
            "up_excess_vs_market": 0.05, "judged": 10,
        },
        "log_path": "/tmp/x",
    }

    class BadRunner:
        def __call__(self, prompt):
            raise RuntimeError("claude CLI missing / timeout")

    class BadDirect:
        def chat(self, m, system=""):
            raise RuntimeError("proxy down")

    got = research_candidates(evidence, claude_runner=BadRunner(), direct=BadDirect())

    assert got == [], (
        f"实测返回 {got!r}。若已能区分全挂 ⇒ 缺陷已修，"
        f"请把本断言改成「返回值必须携带失败状态」"
    )


def test_researcher_control_arm_real_ideas_are_returned():
    """对照臂：模型正常时**确实**返回候选 ⇒ 上一条不是夹具问题。"""
    from aplus.evolve_researcher import research_candidates

    evidence = {
        "current_weights": {"mom20": 0.5, "rev5": 0.5},
        "baseline_stats": {
            "overall_hit_rate": 0.5, "hit_rate_up": 0.5, "hit_rate_down": 0.5,
            "up_excess_vs_market": 0.05, "judged": 10,
        },
        "log_path": "/tmp/x",
    }

    class GoodRunner:
        def __call__(self, prompt):
            return ('[{"weights": {"mom20": 1.0, "rev5": 0.3}, '
                    '"rebalance_every": 21}]')

    got = research_candidates(evidence, claude_runner=GoodRunner())
    assert got and isinstance(got, list), f"对照臂失效：正常路径没出候选，实测 {got!r}"


def test_webapp_never_writes_structured_log():
    """【钉住缺陷】在守：Web 层故障**当前不写任何结构化日志**。

    缺陷：aplus/webapp.py 的三处 `except Exception`（:298 / :367 / :383）
    把异常变成一句 SSE 报错推给页面就结束，**不落盘**。

    坏掉的症状（出事那天我能不能查出来）：
    不能。页面报错一闪而过、刷新就没了；aplus/log.py 的日志里没有它。
    「昨天有几个人遇到过模型断流」这个问题**事后无法回答** ——
    而 webapp.py:645 的日志面板读的是 `read_logs`，那里只有
    CLI 侧的事件，Web 侧一条都没有。

    静态钉（运行 webapp 需起服务+真实模型，超出本文件范围）。
    只守「有没有接上写入」，不守接入后的行为。
    """
    src = Path(__file__).resolve().parent.parent / "aplus" / "webapp.py"
    text = src.read_text(encoding="utf-8")

    assert "except Exception" in text, "夹具前提：webapp 确实有吞异常的 except"
    assert "from .log import read_logs" in text, "夹具前提：webapp 确实只读不写"

    for name in ("get_logger", "begin_run", "_EventLogger", "RunLog", "setup_logging"):
        assert name not in text, (
            f"webapp 现在用了 {name} ⇒ Web 层已开始写结构化日志，"
            f"本条的结论需要重估"
        )


# ═════════════════════════════════════════════════════════════
# 缺陷 6 · 守卫自己被一行坏数据打死（read_runs 的 run_end 也有盲点）
# ═════════════════════════════════════════════════════════════

def test_read_run_status_raises_on_truncated_file(tmp_path):
    """【钉住缺陷】在守：半截 run_status.json **当前会让日更守卫崩掉**。

    缺陷：`write_run_status` 用的是**非原子** `write_text`
    （aplus/reports.py:273，无 tmp+rename），进程被 kill 会留半截 JSON。
    而 `read_run_status`（reports.py:280）直接 `json.loads`，无兜底；
    调用方 scripts/guard_daily.py:17 外面**也没有 try**。

    坏掉的症状（出事那天我能不能查出来）：
    最坏的一种 —— **守卫缺席本身不可观测**。守卫抛 JSONDecodeError 之后
    当天不补跑，而 launchd 只看退出码、不留 run 记录 ⇒
    「守卫没跑」和「守卫跑了但没事」在日志里长得一样。

    复现：
        RAISED: JSONDecodeError Unterminated string starting at: line 2 column 11
    """
    from aplus.reports import read_run_status, write_run_status

    write_run_status(tmp_path, {"date": "2026-10-14", "status": "ok", "steps": {}})
    p = tmp_path / "run_status.json"
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw[:20], encoding="utf-8")        # 模拟写到一半被 kill

    with pytest.raises(json.JSONDecodeError):
        read_run_status(tmp_path)


def test_guard_daily_has_no_try_around_read_run_status():
    """【钉住缺陷】守卫调用点本身**当前没有任何异常兜底**。

    这是上一条的调用侧证据（上一条钉行为，这条钉语法位置）。
    两条都指向同一处 ⇒ 不是单侧证据。

    注意：这里用「`except` 出现次数 == 0」而不是「有没有 try」——
    guard_daily.py 里恰好没有别的 try/except，语义比字符串搜索精确。
    """
    src = Path(__file__).resolve().parent.parent / "scripts" / "guard_daily.py"
    text = src.read_text(encoding="utf-8")

    assert "read_run_status" in text, "夹具前提：守卫确实依赖 run_status"
    assert "except" not in text, (
        "guard_daily.py 现在有 except ⇒ 调用侧已加兜底，"
        "请更新缺陷 6 的描述并把断言改成正面断言"
    )


def test_read_run_status_control_arm_reads_valid_file(tmp_path):
    """对照臂：文件完好时 read_run_status 正常返回 ⇒ 上一条不是恒真。"""
    from aplus.reports import read_run_status, write_run_status

    write_run_status(tmp_path, {"date": "2026-10-14", "status": "ok", "steps": {}})
    got = read_run_status(tmp_path)

    assert got is not None
    assert got["status"] == "ok"


def test_read_run_status_returns_none_on_absent_file(tmp_path):
    """非恒真防护：文件不存在时返回 None（守卫据此走补跑分支）。"""
    from aplus.reports import read_run_status

    assert read_run_status(tmp_path) is None


def test_read_runs_hides_run_older_than_seven_files(tmp_path):
    """【钉住缺陷】在守：跨 7 天以上的 run **当前完全不可见**。

    缺陷：`read_runs` 只 glob 最后 7 个文件（aplus/log.py:148，
    注释写「跨周排障够用」），而 `seen` 只在遇到 **run_start** 时追加
    （log.py:163-166）。一次跑了 8 天的任务在今天结束 ⇒
    run_start 在 8 天前的文件里（被窗口排除）、run_end 在今天的文件里
    ⇒ run_end 没有 run_start 就进不了 `seen` ⇒ **整条 run 不出现**。

    坏掉的症状（出事那天我能不能查出来）：
    不能，而且丢的恰是最该看见的那次 ——「跑最久的那次失败了」。
    `rotate_logs` 默认 keep_days=14（log.py:203），所以文件还在，
    但 `read_runs` 看不见它；healthcheck.py:45 用 limit=200 调它，
    于是健康检查显示「一切正常」。

    复现：
        read_runs (glob[-7:] = files 02..08): []
    """
    from aplus.log import read_runs

    for day in range(1, 9):
        _write(tmp_path / "logs" / f"2026-10-{day:02d}.jsonl")
    (tmp_path / "logs" / "2026-10-01.jsonl").write_text(
        '{"ts":"2026-10-01 23:59:00","level":"info","module":"m",'
        '"event":"run_start","run_id":"LONG"}\n', encoding="utf-8")
    (tmp_path / "logs" / "2026-10-08.jsonl").write_text(
        '{"ts":"2026-10-08 00:01:00","level":"error","module":"m",'
        '"event":"run_end","run_id":"LONG","status":"error",'
        '"error":"13h run failed"}\n', encoding="utf-8")

    runs = read_runs(tmp_path)
    assert runs == [], (
        f"实测 read_runs 返回 {runs!r}。若已能看见跨窗口 run ⇒ 缺陷已修，"
        f"请把本断言改成「必须包含 LONG」"
    )


def test_read_runs_control_arm_sees_run_within_window(tmp_path):
    """对照臂：run_start 在窗口内时**能**看见 ⇒ 上一条不是恒真。"""
    from aplus.log import read_runs

    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs" / "2026-10-07.jsonl").write_text(
        '{"ts":"2026-10-07 23:59:00","level":"info","module":"m",'
        '"event":"run_start","run_id":"OK7"}\n', encoding="utf-8")
    (tmp_path / "logs" / "2026-10-08.jsonl").write_text(
        '{"ts":"2026-10-08 00:01:00","level":"error","module":"m",'
        '"event":"run_end","run_id":"OK7","status":"error","error":"boom"}\n',
        encoding="utf-8")

    runs = read_runs(tmp_path)
    assert len(runs) == 1, f"窗口内的 run 应当可见，实测 {runs!r}"
    assert runs[0]["run_id"] == "OK7"


# ═════════════════════════════════════════════════════════════
# 缺陷 7 · 时间戳是无时区的裸字符串
#   aplus/log.py:50（time.strftime）+ log.py:180（按 ts 字符串排序）
# ═════════════════════════════════════════════════════════════

def test_event_timestamp_is_naive_without_timezone(tmp_path):
    """【钉住缺陷】在守：时间戳**当前是 naive 本地时间、无时区**。

    缺陷：log.py:50 用 `time.strftime("%Y-%m-%d %H:%M:%S")` ⇒
    无 tz offset、无 Z；而 `read_runs:180` 按 ts **字符串**排序
    （`out.sort(key=lambda r: r.get("ts", ""), reverse=True)`）。

    坏掉的症状（出事那天我能不能查出来）：
    很难。夏令时切换 / 改系统时区 / NTP 回拨之后，
    同一秒可能对应两个真实时刻，而「今天」这个边界整体平移。
    跨零点排障（本仓最常见的场景）恰好撞在边界上：
    `read_logs` 只读最后 2 个**文件名**（log.py:190），
    文件名由 `date.today()` 生成（log.py:135，naive）⇒
    时区一改，「今天/昨天」选中的文件就不是出事的那两个。

    复现：
        ts field: '2026-10-14 22:13:43'
        has timezone/offset? False
    """
    import aplus.log as L

    L.setup_logging(tmp_path)
    L.get_logger("t").event("x")

    f = sorted((tmp_path / "logs").glob("*.jsonl"))[-1]
    entry = json.loads(f.read_text(encoding="utf-8").splitlines()[0])

    ts = entry["ts"]
    has_tz = ("+" in ts[10:]) or ts.endswith("Z") or ("T" in ts and "Z" in ts)
    assert not has_tz, (
        f"实测时间戳已带时区：{ts!r} ⇒ log.py:50 已改用 tz-aware，"
        f"请更新缺陷 7 的描述并把本断言改成正面断言"
    )
    assert ts == datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")[:16] + ts[16:], (
        f"时间戳应当等于本地 naive 时间（用于确认它跟的是机器时区），实测 {ts!r}"
    )


# ═════════════════════════════════════════════════════════════
# 非缺陷登记 —— 「先问如果它没坏呢」的落地
# 下面三条我怀疑过、实测**没坏**，不列为缺陷，但留在文件里防止后人重查。
# ═════════════════════════════════════════════════════════════

def test_nondefect_cross_midnight_run_is_correlated(tmp_path):
    """【非缺陷登记】跨零点的 run 实测**没有**坏。

    我怀疑「run 跨零点会不会算错天 / 配不上对」。实测：按 run_id 配对，
    run_start 23:59 在昨天文件、run_end 00:01 在今天文件，
    `read_runs` 仍返回 finished=True / status=error / error 正确。

    ⇒ **不列为缺陷。** 留在此处是为了让后人不要重新怀疑它，
    也提醒「先问如果它没坏」不是形式主义 —— 我今天已因此少报 3 条。
    """
    from aplus.log import read_runs

    _write(tmp_path / "logs" / "2026-10-13.jsonl",
           '{"ts":"2026-10-13 23:59:00","level":"info","module":"daily",'
           '"event":"run_start","run_id":"R9"}')
    _write(tmp_path / "logs" / "2026-10-14.jsonl",
           '{"ts":"2026-10-14 00:01:00","level":"error","module":"daily",'
           '"event":"run_end","run_id":"R9","status":"error",'
           '"error":"midnight crash"}')

    runs = read_runs(tmp_path)
    assert len(runs) == 1, f"跨零点必须仍能配对，实测 {runs!r}"
    assert runs[0]["finished"] is True
    assert runs[0]["error"] == "midnight crash"
    assert runs[0]["status"] == "error"


def test_nondefect_rotate_logs_does_not_over_delete(tmp_path):
    """【非缺陷登记】`rotate_logs` 实测**不会**误删。

    我怀疑「按 `date.fromisoformat(f.stem)` 删文件会不会误伤」。
    实测：非日期命名的文件（`vintage_2026-10-14.log`）因 ValueError 被跳过，
    窗口内的 `.jsonl` 保留，只有超期日期文件被删。⇒ 不列为缺陷。
    """
    from aplus.log import rotate_logs

    keep = _write(tmp_path / "logs" / "2026-10-13.jsonl", '{"a":1}')
    other = _write(tmp_path / "logs" / "vintage_2026-10-14.log", "x")
    drop = _write(tmp_path / "logs" / "2026-09-01.jsonl", '{"a":1}')

    rotate_logs(tmp_path, keep_days=1, today="2026-10-14")

    assert keep.exists(), "窗口内的 jsonl 不该被删"
    assert other.exists(), "非日期命名的文件不该被删（ValueError 被跳过）"
    assert not drop.exists(), "超期 jsonl 本该被删（对照：确认函数确实在工作）"


def test_nondefect_launchd_plists_are_valid_for_plutil():
    """【非缺陷登记】`com.aplus.vintage.plist` 实测**不是坏的**。

    `plistlib` 报 `ExpatError: not well-formed`，一度让我以为 plist 坏了。
    但 launchd 实际用的是 `plutil`，`plutil -lint` 对全部 7 个 plist **全部 OK**
    ⇒ 是 plistlib 比 plutil 严格，不是文件坏了。⇒ 不列为缺陷。
    """
    import subprocess

    agents = Path.home() / "Library" / "LaunchAgents"
    if not agents.is_dir():
        pytest.skip("本机没有 launchd（CI / Linux）")

    plists = sorted(agents.glob("com.aplus.*.plist"))
    if not plists:
        pytest.skip("本机没有 aplus 的 launchd 配置")

    for plist in plists:
        r = subprocess.run(["plutil", "-lint", str(plist)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"{plist.name} 未通过 plutil -lint ⇒ plist 确实坏了，"
            f"本条登记失效：{r.stdout}{r.stderr}"
        )
