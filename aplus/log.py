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
from datetime import date, timedelta
from pathlib import Path

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
    today = today or date.today().isoformat()
    cutoff = date.fromisoformat(today) - timedelta(days=keep_days)
    sink_dir = Path(data_root) / "logs"
    if not sink_dir.exists():
        return
    for f in sink_dir.glob("*.jsonl"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except ValueError:
            continue
