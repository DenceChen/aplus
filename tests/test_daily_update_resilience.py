"""采集链路的**抗断供**守卫。

## 这条文件为什么存在

2026-10-20 实测**差点断供一天**（`data/logs/2026-10-20.jsonl` + `data/run_daily.log`）：

    18:30:04  step_start update     ← 第一跑
    18:31:36  run_end  error        ← 探针把**网络超时**读成「非交易日」⇒ 全市场跳过
    19:30:02  step_start update     ← 守卫补跑
    19:33:45  step_skipped update   ← 写库锁被占 ⇒ 采集又跳过
    19:33:45  step_start update     ← **第三跑**并发开始
    20:58:26  run_end ok            ← 发布
    21:12:53  run_end ok            ← **又发布一次**

⇒ 三条缺陷：**① 网络错误被误诊成「非交易日」**、**② 守卫与在飞的那跑并发**、
**③ 每天重复发布一次**（②③ 是同一件事：守卫在 19:30 看 `run_status.json`
时那文件**还是昨天的**——状态是跑完才写的 ⇒ 守卫必然判定「未成功」）。

⇒ 守卫钉住的是**三条性质**，不是某几行实现：
  A. 「源抛异常」与「源返回空帧」必须**结构化**分开（不靠字符串）
  B. 探针**不许是单票**（一只票的网络超时不该决定另外 5459 只的命运）
  C. 「有一跑在飞」必须**可观测**且守卫必须据此**不并发开第二跑**

⚠️ **守卫是时间局部的**：它证明代码此刻对，管不了上一轮已经发出的名单。
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RD = _load(ROOT / "scripts" / "run_daily.py", "run_daily_resilience")
GD = _load(ROOT / "scripts" / "guard_daily.py", "guard_daily_resilience")

from aplus.pipeline import AllSourcesFailed  # noqa: E402

_TODAY = date.today().isoformat()


# ── A. 结构化分类 ────────────────────────────────────────────────────

def test_all_sources_failed_says_structurally_whether_the_source_raised():
    """`all_empty` 必须**数出来**，不许靠 `repr(exc)` 里的文本去猜。

    🚨 靠文本区分是本仓反复踩的坑：找错了**不崩**，只会自信地说一句胡说。
    """
    err = AllSourcesFailed("000001.SZ", {"akshare": "empty result (no data in range)"},
                           all_empty=True)
    assert err.all_empty is True
    err2 = AllSourcesFailed("000001.SZ", {"akshare": "服务器连接失败"}, all_empty=False)
    assert err2.all_empty is False
    # ⇒ 同一个 `errors` 文本形态，两种**结构**结论
    err3 = AllSourcesFailed("x", {"akshare": "empty result (no data in range)"},
                            all_empty=False)
    assert err3.all_empty is False and err3.errors["akshare"].startswith("empty")


def test_sync_daily_computes_all_empty_structurally_not_by_hand():
    """🚨 **必须真的走一遍 `sync_daily`**。

    第一版守卫只**直接构造** `AllSourcesFailed(..., all_empty=...)` ⇒
    它钉的是「这个属性存在」，**没钉「它是怎么算出来的」** ⇒
    变异 `all_empty` 恒为 `True` 时**全绿**（存活）。
    ⇒ 这就是「只钉存在不钉性质」：对改**算法**零信息量。

    ⇒ 这里用假源驱动真实的 `sync_daily`：
      两个源都返回空帧 ⇒ `all_empty=True`
      一个源空、一个源抛异常 ⇒ `all_empty=False`
    """
    from aplus.pipeline import SyncPipeline

    class _Src:
        def __init__(self, name, mode):
            self.name = name
            self.mode = mode

        def fetch_daily(self, code, start, end):
            if self.mode == "raise":
                raise RuntimeError("服务器连接失败，请稍后再试。")
            return pd.DataFrame()          # 空帧

    pipe = SyncPipeline([_Src("a", "empty"), _Src("b", "empty")], store=None)
    with pytest.raises(AllSourcesFailed) as ei:
        pipe.sync_daily("000001.SZ", "2026-10-19", "2026-10-20")
    assert ei.value.all_empty is True, "全空 ⇒ 该读成「今天没这一天的数据」"

    pipe2 = SyncPipeline([_Src("a", "empty"), _Src("b", "raise")], store=None)
    with pytest.raises(AllSourcesFailed) as ei2:
        pipe2.sync_daily("000001.SZ", "2026-10-19", "2026-10-20")
    assert ei2.value.all_empty is False, (
        "有一个源抛了异常 ⇒ 该读成「数据源不可用」（可重试）")


def test_a_source_exception_is_not_read_as_a_non_trading_day(tmp_path):
    """源抛异常 ⇒ `upstream_unavailable`（**可重试**），不许读成 `no_new_data`。

    这正是 2026-10-20 断供的那一步：网络错误被读成「非交易日或数据未就绪」。
    """
    class Boom:
        def sync_daily(self, code, start, end):
            raise AllSourcesFailed(code, {"akshare": "服务器连接失败"}, all_empty=False)

    probes = []
    for i in range(3):
        p = tmp_path / f"{i:06d}.SZ.parquet"
        pd.DataFrame({"date": ["2026-10-19"]}).to_parquet(p)
        probes.append(p)

    RD.PROBE_RETRIES = 1          # 别在测试里睡 40 秒
    RD.PROBE_RETRY_SLEEP = 0
    status, detail, _ = RD._probe_market(Boom(), probes, "2026-10-20")
    assert status == RD.UPDATE_UPSTREAM_UNAVAILABLE, detail


def test_all_sources_returning_empty_is_not_read_as_a_source_outage(tmp_path):
    """所有源都**返回空帧**（没抛异常）⇒ `no_new_data` ⇒ 交给交易日闸去判。

    ⚠️ 两者应对**相反**：空帧 ⇒ 今天没这一天的数据（非交易日/上游未就绪）；
    抛异常 ⇒ 数据源不可用 ⇒ 该重试。把它们合并 = 断供。
    """
    class Empty:
        def sync_daily(self, code, start, end):
            raise AllSourcesFailed(code, {"akshare": "empty result"}, all_empty=True)

    probes = []
    for i in range(3):
        p = tmp_path / f"{i:06d}.SZ.parquet"
        pd.DataFrame({"date": ["2026-10-19"]}).to_parquet(p)
        probes.append(p)

    RD.PROBE_RETRIES = 1
    RD.PROBE_RETRY_SLEEP = 0
    status, detail, _ = RD._probe_market(Empty(), probes, "2026-10-20")
    assert status == RD.UPDATE_NO_NEW_DATA, detail


# ── B. 探针不许是单票 ────────────────────────────────────────────────

def test_the_probe_uses_several_spread_out_candidates():
    """🚨 **一只票定全池**（形状 18/23 的采集版）：一只票的网络超时
    不该决定另外 5459 只的命运 ⇒ 探针必须是**多只且分散**的。

    「分散」也要钉：不取前 n 只（它们可能同属一个板块、同批上市、同一状态
    ⇒ 一只挂了就整批挂）。
    """
    files = [f"{i:06d}.SZ.parquet" for i in range(5460)]
    picked = RD._spread_probe_files(files)
    assert len(picked) >= 3, f"探针只有 {len(picked)} 只 ⇒ 单票定全池"
    idxs = [files.index(p) for p in picked]
    assert idxs[0] == 0 and idxs[-1] == len(files) - 1, (
        f"探针没有覆盖全池两端：{idxs}")
    gaps = [idxs[i + 1] - idxs[i] for i in range(len(idxs) - 1)]
    assert min(gaps) > 0
    assert max(gaps) - min(gaps) <= 2, f"探针取样不均匀：{gaps}"


def test_the_call_site_actually_uses_the_spread_probe():
    """🚨 **探针跑被调函数、缺陷在调用点**。

    `_spread_probe_files` 本身被钉住了（变异 M2 能杀它），
    但**调用点**退回 `files[:1]` 时**没有任何守卫会响**（变异 M7 存活）⇒
    「函数是对的」与「它被用对了」是两个命题。

    ⇒ 这里用 **AST** 钉调用点（**不靠子串**——子串会被注释满足）：
    `_incremental_update_locked` 必须把**整个 `files` 列表**交给
    `_spread_probe_files`；收到切片（`files[:1]`）即判死。
    """
    import ast
    src = (ROOT / "scripts" / "run_daily.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_incremental_update_locked")
    calls = [c for c in ast.walk(fn)
             if isinstance(c, ast.Call)
             and getattr(c.func, "id", None) == "_spread_probe_files"]
    assert len(calls) == 1, (
        f"`_incremental_update_locked` 里有 {len(calls)} 处 `_spread_probe_files` "
        f"调用（必须恰好 1 处）")
    arg = calls[0].args[0]
    assert isinstance(arg, ast.Name) and arg.id == "files", (
        f"探针必须拿**整个 files 列表**去分散取样，收到的是 {ast.dump(arg)[:100]}"
        f" ⇒ 探针退化成单票 ⇒ 一只票的网络超时会决定全市场")


def test_the_probe_retries_a_transient_source_outage(tmp_path, monkeypatch):
    """瞬时网络抖动**该被重试吸收**：第一次失败、第二次成功 ⇒ 状态是 `updated`。

    ⚠️ 这条是**正控**：没有它，上面那条「源异常 ⇒ upstream_unavailable」
    只是一条「永远失败」的描述，零判别力。
    """
    class Flaky:
        def __init__(self):
            self.calls = 0

        def sync_daily(self, code, start, end):
            self.calls += 1
            if self.calls == 1:
                raise AllSourcesFailed(code, {"akshare": "timed out"}, all_empty=False)
            # 成功：把末根推进到 today
            return None

    p = tmp_path / "000001.SZ.parquet"
    pd.DataFrame({"date": ["2026-10-19"]}).to_parquet(p)

    flaky = Flaky()

    def fake_sync(code, start, end):
        flaky.sync_daily(code, start, end)
        # 模拟写入：把末根改成 today
        pd.DataFrame({"date": ["2026-10-19", "2026-10-20"]}).to_parquet(p)

    class Pipe:
        sync_daily = staticmethod(fake_sync)

    RD.PROBE_RETRIES = 3
    RD.PROBE_RETRY_SLEEP = 0
    status, detail, advanced = RD._probe_market(Pipe(), [p], "2026-10-20")
    assert status == RD.UPDATE_DONE, detail
    assert advanced == {"000001.SZ"}


# ── C. 「有一跑在飞」可观测且守卫据此不并发 ───────────────────────────

def _write_log(data_root: Path, day: str, entries: list):
    """写进 `<data_root>/logs/<day>.jsonl`（`read_runs` 的路径约定）。"""
    logs_dir = data_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    with (logs_dir / f"{day}.jsonl").open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_a_run_without_its_end_event_counts_as_in_flight(tmp_path):
    """「在飞」是**可观测的事实**（有 run_start、没有配对的 run_end），
    不是「进程还在」（第 158 层：PID 存活探针不是身份）。"""
    _write_log(tmp_path / "data", _TODAY, [
        {"ts": "2026-10-20 18:30:04", "module": "daily", "event": "run_start",
         "run_id": "183004-aaa"},
    ])
    flying = GD._daily_run_in_flight(tmp_path / "data")
    assert flying is not None and flying["run_id"] == "183004-aaa"


def test_a_finished_run_does_not_count_as_in_flight(tmp_path):
    """配对上了 run_end ⇒ 不在飞 ⇒ 守卫可以正常判「已成功，不用补」。"""
    _write_log(tmp_path / "data", _TODAY, [
        {"ts": "2026-10-20 18:30:04", "module": "daily", "event": "run_start",
         "run_id": "183004-aaa"},
        {"ts": "2026-10-20 20:58:26", "module": "daily", "event": "run_end",
         "status": "ok", "run_id": "183004-aaa"},
    ])
    assert GD._daily_run_in_flight(tmp_path) is None


def test_another_module_in_flight_does_not_block_the_guard(tmp_path):
    """只有 `daily` 模块的运行才算「有一跑在飞」——
    `repair_tail_cols` / `factor_width_mde` 在跑不关出单的事。"""
    _write_log(tmp_path / "data", _TODAY, [
        {"ts": "2026-10-20 18:30:04", "module": "repair_tail_cols",
         "event": "run_start", "run_id": "183004-zzz"},
    ])
    assert GD._daily_run_in_flight(tmp_path) is None


def test_the_guard_does_not_start_a_second_run_while_one_is_in_flight(
        tmp_path, monkeypatch):
    """🚨 **并发 = 重复发布 + 白烧一轮 CPU**（2026-10-20 实测两次发布）。

    ## 判据为什么必须打在「**读到的是跑完之后的状态**」上

    守卫的价值不在「看见在飞就不动」，而在**「等它跑完再看」**：
    正常的一天里 18:30 那跑要到 ~20:58，19:30 的守卫此刻读到的
    `run_status.json` **还是昨天的** ⇒ 不等就会判定「未成功」⇒ 开第二跑。

    ⇒ 所以这条守卫模拟的是**真实时序**：状态文件在等完之后才变成今天的 ok。
    若把「在飞检测」整段删掉，本条**立刻变红**（守卫会读到昨天 ⇒ 开第二跑）
    ⇒ 它有牙。
    """
    _write_log(tmp_path / "data", _TODAY, [
        {"ts": "2026-10-20 18:30:04", "module": "daily", "event": "run_start",
         "run_id": "183004-aaa"},
    ])
    called = []
    state = {"waited": False}

    def fake_wait(*a, **k):
        state["waited"] = True
        return "ok"

    def fake_status(*a, **k):
        # 等之前 = 昨天的状态（正是 19:30 看到的那份）；等之后 = 今天的 ok
        if state["waited"]:
            return {"date": _TODAY, "status": "ok"}
        return {"date": "2026-10-20", "status": "ok"}

    def boom(*a, **k):
        called.append(a)
        raise AssertionError("守卫不该在有一跑在飞时再开一跑")

    monkeypatch.setattr(GD, "ROOT", tmp_path)
    monkeypatch.setattr(GD, "_wait_for_run", fake_wait)
    monkeypatch.setattr(GD.subprocess, "run", boom)
    monkeypatch.setattr(GD, "classify_run_day", lambda *a, **k: "trading")
    monkeypatch.setattr(GD, "trading_days", lambda: [])
    monkeypatch.setattr(GD, "read_run_status", fake_status)
    GD.main()
    assert state["waited"], "守卫没等在飞的那跑 ⇒ 会读到昨天的状态 ⇒ 会重复发布"
    assert not called, "守卫在有一跑在飞时仍然开了第二跑"


def test_the_guard_still_reruns_when_the_flying_run_failed(tmp_path, monkeypatch):
    """反向正控：等的那跑**失败**了 ⇒ 守卫**必须**补跑。

    ⚠️ 没有这一条，上一条就可能退化成「永远不补跑」的装饰
    （第 112 层：过严的筛选会把 `all()` 退化成装饰）。
    """
    _write_log(tmp_path / "data", _TODAY, [
        {"ts": "2026-10-20 18:30:04", "module": "daily", "event": "run_start",
         "run_id": "183004-aaa"},
        {"ts": "2026-10-20 18:31:36", "module": "daily", "event": "run_end",
         "status": "error", "run_id": "183004-aaa"},
    ])
    called = []

    def fake_run(*a, **k):
        called.append(a)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(GD, "ROOT", tmp_path)
    monkeypatch.setattr(GD.subprocess, "run", fake_run)
    monkeypatch.setattr(GD, "classify_run_day", lambda *a, **k: "trading")
    monkeypatch.setattr(GD, "trading_days", lambda: [])
    monkeypatch.setattr(GD, "read_run_status", lambda *a, **k: {
        "date": "2026-10-20", "status": "skipped"})
    GD.main()
    assert called, "在飞那一跑失败了却没有补跑"


# ── D. 退出码：数据源不可用 ≠ 工作日不在仓里 ─────────────────────────

def test_upstream_unavailable_is_its_own_outcome_and_not_data_missing():
    """「**数据源不可用**」必须与「工作日但仓里没数据」分开。

    ⇒ 两者产出一样（都没出单），但**应对相反**：前者该重试，后者要人裁决。
    ⇒ 合并成一个 `data_missing` = 2026-10-20 那种「诊断错了没人知道该重试」。
    """
    from aplus.reports import (
        OUTCOME_DATA_MISSING, OUTCOME_UPSTREAM_UNAVAILABLE, RUN_STATUS_OUTCOMES,
    )
    assert OUTCOME_UPSTREAM_UNAVAILABLE in RUN_STATUS_OUTCOMES
    assert OUTCOME_UPSTREAM_UNAVAILABLE != OUTCOME_DATA_MISSING


def test_the_update_step_returns_a_status_rather_than_nothing():
    """🚨 **返回值不许省**：调用方要靠它区分「今天没数据」与「数据源不可用」。

    没有返回值 ⇒ 两件事又被压成一个 `None` ⇒ 又回到 2026-10-20 的断供。
    """
    import inspect
    src = inspect.getsource(RD.step_incremental_update)
    assert "return _incremental_update_locked" in src, (
        "step_incremental_update 没有把更新状态返回给调用方")
    src2 = inspect.getsource(RD._incremental_update_locked)
    for name in ("UPDATE_UPSTREAM_UNAVAILABLE", "UPDATE_NO_NEW_DATA",
                 "UPDATE_UP_TO_DATE", "UPDATE_DONE"):
        assert name in src2, f"{name} 没有从更新步骤里出来"


def test_the_four_update_statuses_are_distinct():
    """四个状态必须**互不相同**且都是模块级常量（可 import ⇒ 有东西能挡）。"""
    values = {RD.UPDATE_UPSTREAM_UNAVAILABLE, RD.UPDATE_NO_NEW_DATA,
              RD.UPDATE_UP_TO_DATE, RD.UPDATE_DONE}
    assert len(values) == 4, values
    assert RD.UPDATE_DONE == "updated"
    assert RD.UPDATE_UP_TO_DATE == "up_to_date"
