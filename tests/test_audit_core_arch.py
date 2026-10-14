"""核心代码与架构审计守卫（aplus）。

本文件只覆盖**审计 agent 亲自复现过**的缺陷。每条守卫生前写明
「守什么 / 坏掉的症状（用户视角）」，并在 fixture 前提守卫里钉死前提，
避免 parametrize 落在空集上假绿。

已复现的缺陷（详见各自 docstring 的「复现」段）：

  D1 webapp.py:85-95 / :275,:300,:339,:387,:419
     `_connect()` 返回裸 sqlite3.Connection，而所有调用点都用
     `with _connect(...) as conn:` —— sqlite3 的 `with` 只管**事务**
     （commit/rollback），**不关连接**。每来一次聊天请求泄漏一个连接 +
     一个 fd，只靠 CPython 的循环 GC 回收。

  D2 log.py:54-58
     `_EventLogger.event` 用 `except Exception: pass` 吞掉写入失败。
     日志目录不可写时，**包括 level="error" 在内的全部事件静默消失**，
     而调用方拿到的是「正常返回」。

  D3 panel_cache.py:101-107 / provenance.py:171-179（经 :209 消费）
     `is_dirty()` 在 `git status` 失败时返回 `False`，而 `False` 的语义是
     「工作区干净」。sha 能读到、status 读不到（index 损坏 / 超时 / 非 git 目录）
     时，溯源章被盖成「干净」，`freshness()` 据此给出 `trustworthy=True`。

三条都符合本仓 CLAUDE.md 记的「静默失效」形状：不崩、不报警、报告照样出数。
"""

from __future__ import annotations

import ast
import gc
import inspect
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aplus import log as aplog                      # noqa: E402
from aplus import panel_cache as apcache             # noqa: E402
from aplus import provenance as approv               # noqa: E402
from aplus import webapp as awebapp                  # noqa: E402

SRC = REPO / "aplus"


# ─────────────────────────────────────────────────────────────────────
# 夹具前提守卫
#
# 下面的守卫大多要「数文件里有几处实现」「某函数在某行」。
# 若前提被改坏（例如有人把 `_connect` 换掉、把日志改成不吞），
# parametrize 会落在空集上**假绿**。所以每条都先钉前提。
# ─────────────────────────────────────────────────────────────────────

def _connect_call_sites() -> list[int]:
    """AST 定位 `with _connect(...) as conn:` 的行号。

    必须按 **`with` 语句**找，不能用 `grep -c "_connect"`：后者会把
    `def _connect(...)` 的定义行也算进去，恒 ≥ 1 而毫无信息量；
    也不能用 `"_connect" in text`——那是子串断言（第 13 条教训）。
    这里直接数 ast.With 节点的 callitem.func 是 Name `_connect`。
    """
    tree = ast.parse((SRC / "webapp.py").read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "_connect"):
                    sites.append(node.lineno)
    return sorted(sites)


def test_premise_F1_webapp_has_several_with_connect_call_sites():
    """前提 D1：`webapp.py` 里至少 3 处 `with _connect(...)`。

    坏掉的症状：这条前提为 0 时，下面 parametrize 的 fd 守卫会落在空集上，
    **全绿而什么也没测**——正是第 14 条形状。本守卫在开发期真的抓到过一次：
    第一版按 `.attr == "_connect"` 找，得到 0 处，前提当场变红。
    """
    sites = _connect_call_sites()
    assert len(sites) >= 3, (
        f"前提不成立：`with _connect(...)` 只找到 {len(sites)} 处 {sites}；"
        "若泄漏点被重构掉了，请重新定位后再更新守卫"
    )


def test_premise_F1b_sqlite_with_block_does_not_close():
    """前提 D1：`with sqlite3.connect(...)` **不关连接**（只管事务）。

    这是 CPython 文档化的行为，不是本仓的 bug——但整条 D1 的论证依赖它，
    所以单独钉死。若哪天换掉 sqlite3 或改用真 contextmanager，此守卫提醒
    重新评估 D1。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        conn = sqlite3.connect(Path(d) / "t.db")
        with conn:
            conn.execute("SELECT 1")
        # `with` 块已正常退出；连接若已关闭，这里会抛 ProgrammingError。
        still_open = True
        try:
            conn.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            still_open = False
        assert still_open is True, "sqlite3 的 with 不再关闭连接 ⇒ D1 前提变了"


def test_premise_F2_log_event_has_a_broad_except_handler():
    """前提 D2：`log.py` 的 `event()` 里有 `except Exception`。

    用 AST 数 ExceptHandler 而不是 `grep -c except`：后者会把
    `step()`/`begin_run()` 里那两个**故意重新抛出**的 BaseException 也数进来，
    前提就成了恒真。
    """
    tree = ast.parse((SRC / "log.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.ClassDef) and n.name == "_EventLogger")
    ev = next(n for n in fn.body
              if isinstance(n, ast.FunctionDef) and n.name == "event")
    handlers = [h for node in ast.walk(ev) if isinstance(node, ast.Try)
                for h in node.handlers]
    broad = [h for h in handlers
             if h.type is None
             or (isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"))]
    assert len(broad) >= 1, "前提不成立：event() 里没有宽 except 了，D2 需重新评估"


def test_premise_F3_is_dirty_returns_bool_and_defaults_false_on_error():
    """前提 D3：`is_dirty()` 的 except 分支返回的是 `False`（= 干净）。

    这是 D3 的**语义核心**：错误分支与「工作区确实干净」不可区分。
    """
    tree = ast.parse((SRC / "panel_cache.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "is_dirty")
    handler = next(h for node in ast.walk(fn) if isinstance(node, ast.Try)
                   for h in node.handlers)
    ret = [n for stmt in handler.body for n in ast.walk(stmt)
           if isinstance(n, ast.Return)]
    assert ret, "前提不成立：except 分支里没有 return"
    values = [n.value for n in ret]
    assert all(isinstance(v, ast.Constant) for v in values), \
        "前提变了：except 分支不是常量返回"
    assert [v.value for v in values] == [False], \
        f"前提变了：except 返回 {[v.value for v in values]}，D3 结论需重估"


# ─────────────────────────────────────────────────────────────────────
# D1 —— sqlite 连接 / fd 泄漏
# ─────────────────────────────────────────────────────────────────────

class _CountingConn:
    """数「同时活着的 sqlite3.Connection」的辅助函数。"""

    @staticmethod
    def live() -> int:
        return len([o for o in gc.get_objects() if isinstance(o, sqlite3.Connection)])


@pytest.mark.parametrize("n_calls", [1, 5, 20])
def test_D1_with_connect_leaks_one_connection_per_call(tmp_path, n_calls):
    """守 D1：`with _connect(...)` 之后连接**仍然活着**。

    守的是：`_connect()` 返回的连接必须被真正关闭（`contextlib.closing`
    或显式 `close()`）。

    坏掉的症状（用户视角）：连续聊天/刷新页面时，进程持有的连接与 fd
    单调上涨，直到 `Too many open files`——服务端 500，且此时**日志里
    一行错误都没有**，因为异常发生在内核的 fd 层，不是 Python 层。

    复现：
        for _ in range(500): with _connect(p) as c: c.execute("SELECT 1")
        # 500 个 fd 全部留存（gc 关掉时），gc.collect() 后才回落。
    """
    db = tmp_path / "chat.db"
    gc.collect()
    before = _CountingConn.live()
    for _ in range(n_calls):
        with awebapp._connect(db) as conn:
            conn.execute("SELECT 1")
    after = _CountingConn.live()
    gc.collect()
    after_gc = _CountingConn.live()

    # 守护的核心：`with` 块退出后连接数没有回落 ⇒ 没有被关闭。
    assert after - before >= n_calls, (
        f"{n_calls} 次调用后连接数只增加 {after - before}，"
        f"预期至少 {n_calls}（说明连接已被关闭，D1 已修复——请更新本守卫）"
    )
    # 反向锚点：它们确实只是**被循环 GC 回收**，而不是被显式关闭。
    # 若哪天连接数不随 gc.collect() 回落，说明泄漏形态变了，本断言需重估。
    assert after_gc <= before + 1, (
        f"gc.collect() 后连接数仍为 {after_gc}（基线 {before}）——"
        "连接已不由循环 GC 回收，泄漏形态改变"
    )


def test_D1_connection_is_reusable_after_with_block_proves_no_close(tmp_path):
    """守 D1 的**语法位置**：连接在 `with` 之后仍可执行 SQL。

    单独钉这一条，因为它比数连接数更直接：不依赖 GC 时机，
    只依赖 `with sqlite3.connect()` 的语义。

    坏掉的症状：一旦有人把 `_connect` 换成 `contextlib.closing(...)`
    包裹或返回已关闭的连接，此守卫立刻变红，提示 D1 已被修复。
    """
    db = tmp_path / "chat.db"
    conn = awebapp._connect(db)
    with conn:
        conn.execute("INSERT INTO conversations (title) VALUES ('t')")
    # `with` 块外仍可用 ⇒ 连接未被关闭。
    conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
    conn.close()


@pytest.mark.skipif(os.name != "posix", reason="fd 计数依赖 /dev/fd")
def test_D1_open_fd_count_grows_with_chat_calls(tmp_path):
    """守 D1 的**用户可见后果**：fd 数随聊天次数线性上涨。

    这是 D1 唯一真正打到用户的那一层：连接数在进程内部看不见，
    fd 超限才会变成 `OSError: Too many open files`。

    这里刻意 `gc.disable()`：CPython 默认会跑循环 GC 把泄漏的连接收回去，
    从而**掩盖**这个缺陷。只有在 GC 还没跑到的那段窗口里——也就是真实的
    高并发突发——fd 才是线性增长的。禁用 GC 是为了把那个窗口固定下来，
    让守卫不恒绿。

    坏掉的症状：压测或连续使用时先撞上 fd 上限，接口开始 500。
    """
    db = tmp_path / "chat.db"
    _CountingConn.live()          # force the helper into the object graph
    gc.collect()

    def nfd() -> int:
        return len(os.listdir("/dev/fd"))

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        base = nfd()
        for _ in range(120):
            with awebapp._connect(db) as conn:
                conn.execute("SELECT 1")
        grown = nfd()
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()

    assert grown - base >= 100, (
        f"120 次调用后 fd 只增长 {grown - base}；"
        "连接已被及时关闭（D1 已修复）——请更新本守卫"
    )


# ─────────────────────────────────────────────────────────────────────
# D2 —— 结构化日志静默吞异常
# ─────────────────────────────────────────────────────────────────────

def test_D2_log_event_silently_drops_everything_when_sink_unwritable(tmp_path):
    """守 D2：日志写不进去时必须**发声**，不能静默返回。

    守的是：「日志失败」这条路径本身要可观测。

    坏掉的症状（用户视角）：磁盘满 / 目录权限变了 / 只读挂载之后，
    平台的运行日志**整份停写**，而任务照跑、报告照出、界面照显示绿色。
    用户问「昨晚那次为什么失败」，日志页是空的——因为记录失败这件事
    本身也被同一行 `pass` 吞掉了。**唯一能告诉你出事的东西，
    恰恰是第一个消失的东西。**

    复现：
        chmod 555 <dir>; _EventLogger("m", dir/"x.jsonl").event("run_start")
        # 不抛异常、返回正常，但文件根本没被创建。
    """
    if os.name != "posix":
        pytest.skip("依赖 POSIX 权限位")
    if os.geteuid() == 0:
        pytest.skip("root 无视权限位，构造不出不可写目录")

    blocked = tmp_path / "readonly"
    blocked.mkdir()
    sink = blocked / "events.jsonl"
    os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)   # 555 = 不可写
    try:
        logger = aplog._EventLogger("audit_demo", sink)
        # 关键：这里**不抛异常**。吞异常的实现会让它安静通过。
        logger.event("run_start", phase="backtest")
        logger.event("run_end", level="error", status="error")

        # 反向锚点：确认这个 sink 确实写不进去（否则本守卫恒真）。
        assert not sink.exists(), (
            "前提不成立：不可写目录里竟然写出了文件，本守卫恒真，请重估"
        )
    finally:
        os.chmod(blocked, stat.S_IRWXU)


def test_D2_writable_sink_actually_records_the_same_events(tmp_path):
    """守 D2 的**反向锚点**：能写的时候必须真的写到。

    没有这条，D2 守卫可能因为「logger 根本没工作」而假绿——
    空断言打转正是本仓反复踩的形状。

    坏掉的症状：这条一红，说明日志**连能写的正常路径都写不进去**，
    那比 D2 更严重：日志从来没在工作过。
    """
    sink = tmp_path / "events.jsonl"
    logger = aplog._EventLogger("audit_demo", sink)
    logger.event("run_start", phase="backtest")
    logger.event("run_end", level="error", status="error")

    assert sink.exists(), "可写目录下日志文件都没生成——日志链路整体坏了"
    lines = [ln for ln in sink.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2, f"应有 2 条事件，实际 {len(lines)}：{lines}"
    assert '"run_start"' in lines[0] and '"run_end"' in lines[1]
    # error 级别那条必须真的带上 error 级别（否则「错误降级成 info」这类
    # 接线错误也一并被放过）。
    assert '"level": "error"' in lines[1], f"error 级别丢失：{lines[1]}"


# ─────────────────────────────────────────────────────────────────────
# D3 —— is_dirty() 把「读不到」当成「干净」
# ─────────────────────────────────────────────────────────────────────

def _fake_git_bin(tmp_path: Path, fail_on: str) -> Path:
    """造一个 git 包装器：只有 `fail_on` 子命令失败，其余透传给真 git。

    模拟的是**真实存在**的分叉故障：`git rev-parse HEAD` 只读一个文件，
    `git status` 要扫整个工作区——index 损坏、`.git/index.lock` 残留、
    超时、或在非 git 目录下，两者失败与否**互相独立**。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = subprocess.run(["which", "git"], capture_output=True, text=True
                          ).stdout.strip() or "/usr/bin/git"
    script = bindir / "git"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        for a in "$@"; do
          if [ "$a" = "{fail_on}" ]; then
            echo "fatal: cannot read index" >&2
            exit 128
          fi
        done
        exec {real} "$@"
        """), encoding="utf-8")
    script.chmod(0o755)
    return bindir


@pytest.fixture
def dirty_repo(tmp_path):
    """一个**确实脏**的 git 仓库：有未提交改动（untracked 文件）。"""
    root = tmp_path / "repo"
    root.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, env=env, check=True)

    git("init", "-q", ".")
    (root / "tracked.txt").write_text("hi\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("-c", "user.email=a@b.c", "-c", "user.name=audit", "commit", "-qm", "init")
    (root / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")   # 制造 dirty
    return root


def test_D3_is_dirty_reports_clean_when_git_status_fails(dirty_repo, tmp_path, monkeypatch):
    """守 D3：`git status` 失败**不得**被当成「工作区干净」。

    守的是：溯源章里的 `code_dirty` 必须区分「干净」与「不知道」。

    坏掉的症状（用户视角）：报告的溯源章写着「代码未改动」，判决板把它
    标成 🟢 新鲜 / `trustworthy=True`。而实际上这份报告可能正是在**改了
    代码但没提交**的状态下生成的——于是「这个数字对应哪版代码」这个问题
    被回答错了，而且**没有任何提示**。溯源章的全部意义就是回答这一个问题。

    复现：PATH 里放一个 `git status` 返回 128 的包装器（rev-parse 正常）。
        ground truth (真 git): dirty = True
        is_dirty()          : False
    """
    # 先用真 git 钉死前提：这个仓库确实是脏的。
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    truth = subprocess.run(["git", "-C", str(dirty_repo), "status", "--porcelain"],
                           capture_output=True, text=True, env=env).stdout.strip()
    assert truth, "前提不成立：夹具仓库其实是干净的，脏状态没造出来"

    monkeypatch.setenv("PATH", f"{_fake_git_bin(tmp_path, fail_on='status')}:{os.environ['PATH']}")

    # 前置锚点：此时 `rev-parse` 仍然可用（D3 只在 status 失败时才露馅）。
    assert apcache.code_sha(dirty_repo), "前提不成立：连 sha 都读不到，构不成 D3 的分叉"

    assert apcache.is_dirty(dirty_repo) is False, (
        "is_dirty() 已能在 git status 失败时区分「不知道」——"
        "D3 若已修复，请更新本守卫（注意：只有在本测试的 "
        "「status 失败 / rev-parse 正常」场景下修复才算数）"
    )


def test_D3_agrees_with_real_git_when_git_works(dirty_repo):
    """守 D3 的**反向锚点**：git 正常时 `is_dirty` 必须与真 git 一致。

    没有这条，D3 守卫可能只是「恒返回 False」而全绿——那正是本仓
    记的第 6 条形状（看起来像开关，实际焊死）。

    坏掉的症状：这条一红，说明 `is_dirty` 连正常路径都判错，
    溯源章的 `code_dirty` 从来没对过。
    """
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def real_dirty() -> bool:
        return bool(subprocess.run(["git", "-C", str(dirty_repo), "status", "--porcelain"],
                                   capture_output=True, text=True, env=env).stdout.strip())

    assert apcache.is_dirty(dirty_repo) == real_dirty() is True, \
        "git 正常时 is_dirty 与真 git 不一致——它可能一直焊死在 False"

    # 清掉未提交改动后必须翻成 False，证明它真的会跟着状态变。
    (dirty_repo / "uncommitted.txt").unlink()
    assert apcache.is_dirty(dirty_repo) is False, \
        "工作区已干净，is_dirty 却仍报 True"
    assert real_dirty() is False, "前提不成立：真 git 也认为脏"


def test_D3_provenance_is_dirty_is_broken_by_a_different_mechanism(
        dirty_repo, tmp_path, monkeypatch):
    """守 D3 的**第二个消费点**：`provenance.is_dirty` 有同一处语义缺陷。

    两份实现的**坏法不一样**（实测，不能当成同一份代码处理）：

    - `panel_cache.is_dirty`：`except → return False`（字面 False）。
    - `provenance.is_dirty`：`_git()` 读不到时返回 `None`，
      再 `bool(None)` ⇒ **同样是 False**。「读不到」在两处都被压成了
      「干净」，只是路径不同。

    所以修 D3 必须**两处同修**：只改 `panel_cache` 的话，报告溯源章
    （`provenance.stamp`）仍然盖成干净，用户看到的判决板照样错。

    坏掉的症状：溯源章写着「代码未改动」，判决板标 🟢 新鲜 /
    `trustworthy=True`，而报告可能是在改了代码未提交的状态下生成的——
    「这个数字对应哪版代码」被回答错了，且无任何提示。
    """
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    truth = subprocess.run(["git", "-C", str(dirty_repo), "status", "--porcelain"],
                           capture_output=True, text=True, env=env).stdout.strip()
    assert truth, "前提不成立：夹具仓库其实是干净的"

    monkeypatch.setenv("PATH", f"{_fake_git_bin(tmp_path, fail_on='status')}:{os.environ['PATH']}")

    # 前置锚点：这条路径的坏法是 `_git()` 返回 None，被 bool() 压成 False。
    # 钉死它，才能说明「读不到」确实在 provenance 侧被吞成了 False。
    assert approv._git(dirty_repo, "status", "--porcelain") is None, \
        "前提变了：_git 不再把读不到压成 None，D3 的机制需重新评估"

    assert approv.is_dirty(dirty_repo) is False, (
        "provenance.is_dirty 已能区分「不知道」——D3 已修复，请更新本守卫"
    )


def test_D3_provenance_stamp_stamps_clean_when_git_status_fails(
        dirty_repo, tmp_path, monkeypatch):
    """守 D3 的**用户可见后果**：溯源章里真的写下 `code_dirty: False`。

    前两条钉的是 `is_dirty()` 这个函数；这条钉的是它**落进 sidecar 之后**
    的样子——也就是用户和判决板真正读到的那份 JSON。

    坏掉的症状：`bridge_error.json.provenance` 里写着
    `"code_dirty": false`，而工作区当时是脏的。`freshness()` 据此给出
    `trustworthy=True`，报告被当成可引用。
    """
    import json

    monkeypatch.setenv("PATH", f"{_fake_git_bin(tmp_path, fail_on='status')}:{os.environ['PATH']}")

    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "bridge_error.json"
    report.write_text("{}", encoding="utf-8")

    approv.stamp(report, generator="aplus/provenance.py", root=dirty_repo)
    stamped = approv.read_stamp(report)      # 已解析成 dict，不是 Path

    assert stamped["code_dirty"] is False, (
        f"溯源章不再写 code_dirty=False，D3 结论需重估：{stamped}"
    )
    # 同一枚章里 sha 是读得到的——正是「读不到≠不干净」被误用的那个分叉场景。
    assert stamped["code_sha"], "前提不成立：连 sha 都读不到，构不成 D3 的分叉故障"


# ─────────────────────────────────────────────────────────────────────
# 架构守卫（不是缺陷，是**前提**——把「它没坏」这件事也钉住）
#
# 这些不报缺陷，只保证上面三条缺陷的前提/上下文不被悄悄改掉。
# ─────────────────────────────────────────────────────────────────────

def test_arch_no_module_level_import_cycle():
    """守架构前提：**模块级**导入无环。

    本仓确实存在一条 `panel ⇄ snapshot` 的环（`panel.py:659` 在函数体内
    惰性 import `snapshot`，`snapshot.py:25` 在模块级 import `panel`）。
    这**不是缺陷**：惰性导入正是打断它的手段，模块级图无环即可正常 import。
    但它脆弱——若有人把 `panel.py:659` 那句提到模块级，import 立刻炸。
    这条守卫就是那个报警器。

    坏掉的症状：`import aplus.panel` 或 `import aplus.snapshot` 抛
    `ImportError: cannot import name ...`——服务起不来，且只有先导入了
    另一个模块才会触发（取决于谁先进来）。
    """
    import importlib

    tree_by_mod = {}
    for f in sorted(SRC.glob("*.py")):
        if f.name == "__init__.py":
            continue
        mod = f.stem
        deps = set()
        for node in ast.parse(f.read_text(encoding="utf-8")).body:   # 只看模块级
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    deps.add(node.module.split(".")[0])
                else:
                    deps.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.Import):
                deps.update(a.name.split(".")[1] for a in node.names
                            if a.name.startswith("aplus."))
        tree_by_mod[mod] = deps

    cycles, stack, done = [], [], set()

    def walk(node):
        if node in stack:
            cycles.append(stack[stack.index(node):] + [node])
            return
        if node in done:
            return
        stack.append(node)
        for dep in sorted(tree_by_mod.get(node, ())):
            if dep in tree_by_mod:
                walk(dep)
        stack.pop()
        done.add(node)

    for mod in sorted(tree_by_mod):
        walk(mod)

    assert not cycles, f"模块级导入成环：{sorted({tuple(c) for c in cycles})}"


def test_arch_lazy_import_that_breaks_the_cycle_is_still_inside_a_function():
    """守架构前提：`panel` 对 `snapshot` 的那条导入**必须留在函数体内。

    这是上面那条环的**具体解法**。把它钉死，免得有人「顺手整理 import」
    提到文件头，import 环就闭合了。

    坏掉的症状：`import aplus.snapshot` / `import aplus.panel` 直接抛
    `ImportError`，Web 服务起不来。
    """
    tree = ast.parse((SRC / "panel.py").read_text(encoding="utf-8"))
    module_level = {
        n.module.split(".")[0]
        for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "snapshot" not in module_level, \
        "panel.py 在模块级 import 了 snapshot ⇒ 模块级导入成环"

    # 且它确实还在（惰性地）import 着 snapshot——否则这条守卫是空转。
    lazy = [n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "snapshot"]
    assert lazy, "panel.py 已经不再 import snapshot ⇒ 环不存在了，本守卫需重估"


def test_arch_evolve_race_has_no_concurrency_primitives():
    """守并发前提：`evolve_race` 里**没有任何**线程/进程/锁原语。

    这条是**防误判**守卫。本仓把候选赛马叫 "race"，很容易让人以为
    里面有并发竞态——实测全仓 `aplus/*.py` 没有一处 threading /
    multiprocessing / Lock / asyncio。「赛马」是候选之间的**竞争**，
    不是线程竞争，`race()` 里的 `def run(...)` 是闭包不是线程。

    坏掉的症状：有人引入真并发后，这条会红，提醒补真正的竞态守卫
    （目前没有，因为目前没有竞态）。
    """
    text = (SRC / "evolve_race.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    banned = {"threading", "multiprocessing", "concurrent", "asyncio", "ThreadPoolExecutor"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"evolve_race 引入了并发原语：{imported & banned}"
    assert "Lock" not in text, "evolve_race 里出现了 Lock，请补真正的竞态守卫"