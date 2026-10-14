"""所有长跑脚本的统一前奏——import 它，别重复抄。

## 解决三件具体的排查痛点

1. **重定向后日志不更新**：`python x.py > x.log` 时 stdout 是块缓冲，
   日志能空上几分钟，看着像卡死。`line_buffering=True` 让每一行立即落盘。
   launchd 启动的进程同样受益（不依赖 plist 里加 `-u`，改 plist 要重载）。
2. **pandas 数值噪音刷屏**：一次赛马能刷几百行 `invalid value encountered in
   subtract`，把真异常埋掉。只压已知的良性几条，其余警告照常可见。
3. **sys.path**：脚本从 `scripts/` 直接跑时找不到 `aplus` 包。

用法：脚本顶部
    import sys; sys.path.insert(0, __file__[:__file__.rfind('/scripts/')])
    from _bootstrap import RUN   # noqa: E402
    ...
    with RUN("evolve_weekly") as run:
        with run.step("横截面预计算"):
            ...
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.log import begin_run, configure_noisy_stderr  # noqa: E402

configure_noisy_stderr()

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)   # 3.7+
    except (AttributeError, ValueError):
        pass                                        # 非 tty 场景下忽略


def RUN(module: str, **ctx):
    """带 run_id / 步骤计时 / 总耗时的运行日志。失败留 traceback 且照常抛出。"""
    return begin_run(module, **ctx)


__all__ = ["RUN", "begin_run", "configure_noisy_stderr"]