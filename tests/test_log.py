"""日志系统：让「出问题」这件事本身可排查（TDD）。

## 为什么专门测这个

排查 2026-10-11 那几个 bug 时，被日志本身绊了三次：

1. **stdout 缓冲**：`nohup python scripts/x.py > x.log` 后，日志文件整整 8 分钟
   是 **0 字节**，因为重定向时 stdout 是块缓冲。看不到进度，只能靠猜。
   launchd 启动的进程同样如此——`data/evolve.log` 没有实时性。
2. **噪音淹没信号**：`RuntimeWarning` 刷了几百行，把真正的异常埋在里面。
   `grep -v Warning` 能救一时，但那是人工的、临时的。
3. **没有 run 概念**：日志里没有「这次跑是哪次」「跑了多久」「哪一步失败」，
   只有散落的事件。凌晨出事时无法定位到具体步骤。

所以这里钉的是：**每次运行有 run_id、有步骤计时、失败必留证据、只压噪音不压错误**。
"""
import json
import warnings
from pathlib import Path

import pytest

from aplus.log import (
    begin_run, configure_noisy_stderr, read_logs, read_runs, setup_logging,
)


@pytest.fixture
def sink(tmp_path):
    setup_logging(tmp_path)
    return tmp_path / "logs"


def _events(sink):
    return [json.loads(l) for l in
            sorted(sink.glob("*.jsonl"))[-1].read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ── run 生命周期 ──

def test_run_has_id_and_duration(sink):
    with begin_run("evolve", window="2y") as run:
        with run.step("赛马"):
            pass
    evs = _events(sink)
    start = next(e for e in evs if e["event"] == "run_start")
    end = next(e for e in evs if e["event"] == "run_end")
    assert start["run_id"] == end["run_id"] and start["run_id"]
    assert start["module"] == "evolve" and start["window"] == "2y"
    assert end["status"] == "ok"
    assert isinstance(end["duration_s"], (int, float))


def test_step_records_duration_and_name(sink):
    with begin_run("evolve") as run:
        with run.step("横截面预计算"):
            pass
    evs = _events(sink)
    names = {e["event"] for e in evs}
    assert "step_start" in names and "step_done" in names
    done = next(e for e in evs if e["event"] == "step_done")
    assert done["step"] == "横截面预计算"
    assert isinstance(done["duration_s"], (int, float))
    assert done["status"] == "ok"


def test_failing_step_records_error_and_reraises(sink):
    """失败必须留证据（traceback + error 级别）**且**照常抛出——绝不能被日志吞掉。"""
    with pytest.raises(ValueError, match="炸了"):
        with begin_run("evolve") as run:
            with run.step("会炸的步骤"):
                raise ValueError("炸了")
    evs = _events(sink)
    err = next(e for e in evs if e["event"] == "step_done" and e["status"] == "error")
    assert err["level"] == "error"
    assert "ValueError" in err.get("traceback", "")
    errs = [e for e in evs if e.get("level") == "error"]
    assert errs, "error 级别事件必须落盘"


def test_failed_run_is_marked_error(sink):
    with pytest.raises(RuntimeError):
        with begin_run("daily") as run:
            run.log("中途发现数据源不可用", level="error", source="baostock")
            raise RuntimeError("boom")
    end = next(e for e in _events(sink) if e["event"] == "run_end")
    assert end["status"] == "error"


def test_read_runs_returns_latest_first(sink):
    for i in range(2):
        with begin_run("evolve", tag=str(i)) as run:
            run.log("做完一步")
    runs = read_runs(sink.parent, limit=5)
    assert len(runs) == 2
    assert runs[0]["tag"] in {"0", "1"}
    assert runs[0]["ts"] >= runs[1]["ts"], "最近的run必须排前面"


def test_read_runs_empty_is_not_an_error(tmp_path):
    setup_logging(tmp_path)
    assert read_runs(tmp_path, limit=5) == []


# ── 噪音隔离：只压已知的良性警告，其他一律放行 ──

def test_suppresses_only_the_known_benign_pandas_warning():
    """pandas 全 NaN 滚动窗口的 'invalid value encountered in subtract' 是良性的，
    会刷几百行淹没真错误——只压它，且压完真的不再出现。"""
    # 注意顺序：catch_warnings 里的 simplefilter("always") 会**重置**整个过滤器表，
    # 所以 suppression 必须在它之后装，否则测的是「过滤器被清空」而不是「规则生效」。
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_noisy_stderr()
        warnings.warn("invalid value encountered in subtract", RuntimeWarning)
    assert not caught, "良性警告应被过滤"


def test_other_runtime_warnings_still_surface():
    """**只**压那一条。别的 RuntimeWarning 必须照常抛出，否则就是在藏错误。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_noisy_stderr()
        warnings.warn("overflow encountered in multiply", RuntimeWarning)
    assert len(caught) == 1, "非良性警告必须继续可见"


def test_user_warnings_are_untouched():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_noisy_stderr()
        warnings.warn("这是我自己写的警告", UserWarning)
    assert len(caught) == 1


def test_configure_noisy_stderr_is_idempotent():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_noisy_stderr()
        configure_noisy_stderr()
        warnings.warn("invalid value encountered in subtract", RuntimeWarning)
    assert not caught


# ── 与既有读取接口的兼容 ──

def test_log_still_readable_by_existing_api(sink):
    """老接口 read_logs 不能被破坏——Web 的「日志」页在用。"""
    with begin_run("evolve") as run:
        run.log("候选赛马开始", candidates=10)
    rows = read_logs(sink.parent, tail=50)
    names = [r["event"] for r in rows]
    assert "run_start" in names and "候选赛马开始" in names and "run_end" in names
    row = next(r for r in rows if r["event"] == "候选赛马开始")
    assert row["candidates"] == 10