"""结构化日志系统：JSONL 一行一事件，便于排查与 Web 展示。

## 排查友好是硬要求，不是锦上添花

2026-10-11 排查几个 bug 时被日志本身绊了三次，因此下面这些不是装饰：

- **run 概念**：`begin_run()` 给出 run_id + 总耗时 + 最终状态。凌晨出事时
  能一眼定位「哪次跑、跑了多久、哪一步炸的」，而不是在散落事件里翻。
- **步骤计时**：`run.step()` 是上下文管理器，成功记耗时，失败记 traceback
  且**照常抛出**——日志绝不能把异常吞掉。
- **噪音隔离**：`configure_noisy_stderr()` 只压一条已知的良性 pandas 警告，
  其余警告全部放行。压噪音和藏错误之间必须有明确边界。
- **无缓冲**：见 `scripts/_bootstrap.py`，重定向到文件后能实时看到进度。
"""
import contextlib
import json
import logging
import time
import traceback
import uuid
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

# `data/*.log` 超期轮转后保留的尾部字节数。留尾巴而不是删文件：
# 本仓铁律「不信退出码、要靠产物指纹」，日志被删等于当场抹掉排查线索。
LOG_TAIL_BYTES = 8192

# 唯一允许压掉的警告：pandas/numpy 在**全 NaN 滚动窗口**上做方差运算时会发
# 这条，结果就是 NaN，属良性。但它一次赛马能刷几百行，把真正的异常埋掉。
# 注意 message 是精确匹配的——换成别的表达式就不会命中，
# 详见 tests/test_log.py::test_other_runtime_warnings_still_surface。
BENIGN_NOISE = [
    (r".*invalid value encountered in subtract.*", RuntimeWarning),
    (r".*Degrees of freedom <= 0.*", RuntimeWarning),
    (r".*Mean of empty slice.*", RuntimeWarning),
    (r".*All-NaN (slice|axis) encountered.*", RuntimeWarning),
]


def configure_noisy_stderr() -> None:
    """只压良性数值噪音，其余一律放行。幂等。"""
    for pattern, cat in BENIGN_NOISE:
        warnings.filterwarnings("ignore", message=pattern, category=cat)


class _EventLogger:
    def __init__(self, module: str, sink: Path, run_id: str | None = None):
        self.module = module
        self._sink = sink
        self.run_id = run_id

    def event(self, event: str, level: str = "info", **fields):
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level,
                 "module": self.module, "event": event, **fields}
        if self.run_id:
            entry["run_id"] = self.run_id
        try:
            with self._sink.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001 — 日志失败绝不影响业务
            pass
        if level == "error":
            logging.getLogger(self.module).error(f"{event} {fields}")


class RunLog:
    """一次运行的结构化日志句柄——`run_id` 会自动带进每条事件。"""

    def __init__(self, module: str, sink: Path, run_id: str, ctx: dict):
        self._log = _EventLogger(module, sink, run_id=run_id)
        self.run_id = run_id
        self._t0 = time.time()

    def log(self, event: str, level: str = "info", **fields):
        self._log.event(event, level=level, **fields)

    @contextlib.contextmanager
    def step(self, name: str, **fields):
        """记录一个步骤的起止与耗时。失败时留 traceback 并照常抛出。"""
        self._log.event("step_start", step=name, **fields)
        t0 = time.time()
        try:
            yield self
        except BaseException as exc:              # noqa: BLE001 — 记录后必须原样抛出
            self._log.event("step_done", level="error", step=name, status="error",
                            duration_s=round(time.time() - t0, 3),
                            error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-4000:])
            raise
        else:
            self._log.event("step_done", step=name, status="ok",
                            duration_s=round(time.time() - t0, 3))


@contextlib.contextmanager
def begin_run(module: str, **ctx):
    """一次任务的生命周期日志：run_start → 各步骤 → run_end（含总耗时与状态）。

    异常时记录 run_end(status=error) 后**原样抛出**，绝不在这里吞掉业务异常。
    """
    sink = _sink_path()
    run_id = f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run = RunLog(module, sink, run_id, ctx)
    run.log("run_start", **ctx)
    t0 = time.time()
    try:
        yield run
    except BaseException as exc:                  # noqa: BLE001
        run.log("run_end", level="error", status="error",
                duration_s=round(time.time() - t0, 3),
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc()[-4000:])
        raise
    else:
        run.log("run_end", status="ok", duration_s=round(time.time() - t0, 3))


_ACTIVE_ROOT: Path | None = None


def setup_logging(data_root) -> Path:
    """初始化日志目录，返回当日日志文件路径。幂等。"""
    global _ACTIVE_ROOT
    root = Path(data_root)
    sink_dir = root / "logs"
    sink_dir.mkdir(parents=True, exist_ok=True)
    _ACTIVE_ROOT = root
    return sink_dir / f"{date.today().isoformat()}.jsonl"


def get_logger(module: str) -> _EventLogger:
    return _EventLogger(module, _sink_path())


def _sink_path() -> Path:
    root = _ACTIVE_ROOT or Path("data")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root / "logs" / f"{date.today().isoformat()}.jsonl"


def read_runs(data_root, limit: int = 10) -> list:
    """每次运行的汇总（run_start 配上同 run_id 的 run_end），最近的排前面。

    这是「昨晚到底跑没跑、成没成、哪一步炸的」的一行答案——
    不用去翻 JSONL，不用去猜哪个事件属于哪次运行。
    """
    sink_dir = Path(data_root) / "logs"
    if not sink_dir.exists():
        return []
    ends, starts, seen = {}, {}, []
    for f in sorted(sink_dir.glob("*.jsonl"))[-7:]:   # 跨周排障够用
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = e.get("run_id")
            if not rid:
                continue
            if e.get("event") == "run_start":
                if rid not in seen:
                    seen.append(rid)
                starts[rid] = e
            elif e.get("event") == "run_end":
                ends[rid] = e
    out = []
    for rid in seen:
        s, e = starts[rid], ends.get(rid)
        row = {k: v for k, v in s.items() if k not in ("event", "module")}
        row["module"] = s.get("module")
        row["finished"] = e is not None
        row["status"] = (e or {}).get("status", "running")
        row["duration_s"] = (e or {}).get("duration_s")
        if e and e.get("error"):
            row["error"] = e["error"]
        out.append(row)
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out[:limit]


def read_logs(data_root, level: str | None = None, tail: int = 200) -> list:
    """读取今日（及可选更早）日志，按级别过滤，取末尾 tail 条。"""
    sink_dir = Path(data_root) / "logs"
    entries = []
    if not sink_dir.exists():
        return []
    files = sorted(sink_dir.glob("*.jsonl"))[-2:]  # 今日 + 昨日（跨午夜排查）
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if level:
        entries = [e for e in entries if e.get("level") == level]
    return entries[-tail:]


def rotate_logs(data_root, keep_days: int = 14, today: str | None = None):
    """轮转日志：`logs/*.jsonl` 超期**删除**，`data/*.log` 超期**截断留尾**。

    两族走两套规矩，理由是它们根本不是一类文件：

    - **`logs/*.jsonl`**：文件名就是日期（`2026-10-14.jsonl`），同一天追加、
      每天一个文件，超期即无价值 ⇒ 直接删（既有行为，未改）。
    - **`<data_root>/*.log`**：launchd 的 StandardOutPath / StandardErrorPath
      落点（`run_daily.log`、`web_agent.err.log` …）。名字里**没有日期**，
      所以判龄只能用 mtime；且它们是**排查线索**——
      本仓铁律「不信退出码、要靠产物指纹」已经被假退出码坑过（2026-10-15）。
      ⇒ 超期**截断**、保留最后 `LOG_TAIL_BYTES` 字节并盖一条轮转标记，**不删文件**。
      实测这批文件最大才 ~20 KB，省下的磁盘远不值这条线索。

    幂等；单个文件坏掉（权限/已消失/是目录）不影响其余文件。
    """
    today = today or date.today().isoformat()
    cutoff_date = date.fromisoformat(today) - timedelta(days=keep_days)
    root = Path(data_root)

    sink_dir = root / "logs"
    if sink_dir.exists():
        for f in sink_dir.glob("*.jsonl"):
            try:
                if date.fromisoformat(f.stem) < cutoff_date:
                    f.unlink()
            except (ValueError, OSError):
                continue          # 文件名解析不了 / 删不掉：跳过，不拖垮整轮

    # 只扫 data_root **顶层**：logs/ 子目录归上面那条分支管，不重复扫。
    if root.is_dir():
        for f in root.glob("*.log"):
            try:
                if not f.is_file():
                    continue
                if f.stat().st_mtime >= _ts(cutoff_date):
                    continue
                _truncate_keep_tail(f, today)
            except OSError:
                continue          # 单个文件坏掉不得让整轮失败（与既有语义一致）


def _ts(d: date) -> float:
    """date → 当日 00:00 本地时间戳。判龄统一用 `<` 于当日零点。"""
    return datetime(d.year, d.month, d.day).timestamp()


def _truncate_keep_tail(path: Path, today: str) -> None:
    """清空超期内容，留下最后 `LOG_TAIL_BYTES` 字节 + 一条轮转标记。

    **不删文件**——文件还在这件事本身就是一条信息（「跑过，但已轮转」）。
    """
    size = path.stat().st_size
    if size <= LOG_TAIL_BYTES:
        return                    # 已经比尾巴还短：无可清，空转（幂等）
    with path.open("rb") as fh:
        fh.seek(size - LOG_TAIL_BYTES)
        tail = fh.read()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (f"=== [rotate_logs] {today} 已轮转：原 {size} 字节，"
              f"保留最后 {LOG_TAIL_BYTES} 字节（{stamp}）===\n").encode("utf-8")
    # 读全再整块写回：避免 seek/truncate 之间被追加写进来的新内容被抹掉。
    with path.open("wb") as fh:
        fh.write(header)
        fh.write(tail)
