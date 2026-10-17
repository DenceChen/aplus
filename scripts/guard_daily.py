"""每日守卫：19:30 检查当天 run_daily 是否成功，失败/缺失则自动补跑。

launchd: com.aplus.guard
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.reports import read_run_status
from aplus.trading_calendar import (
    RUN_DAY_WEEKEND, classify_run_day, trading_days,
)


def main():
    status = read_run_status(ROOT / "data" / "reports")
    today = date.today().isoformat()

    # 🚨 这里**只拦周末**，不能拦「工作日不在仓里」。
    #   `WORKDAY_NOT_IN_STORE` 恰恰是本守卫要去修的缺陷（采集失败）⇒ 必须补跑；
    #   `WEEKEND` 才是「本就不该跑」的那一种。
    #   ⇒ 判据方向与 `run_daily.run_day_gate` 看似相反，实则各管各的一半。
    #
    # 2026-10-10/11 实测：出单作业每天 18:30 跑（含周六周日），
    # 三天名单全部映射到同一个 (entry, exit) ⇒ 前瞻底稿被重复名单污染。
    # 闸装在 `run_daily` 只能挡住 18:30 那一次；**本守卫 19:30 会把它再触发一遍**
    # （判据是 `status.date != today`，而周末那次根本不写 run_status）。
    verdict = classify_run_day(date.today(), trading_days())
    if verdict == RUN_DAY_WEEKEND:
        print(f"今天 {today} 是周末（非交易日）⇒ 不补跑")
        return

    if status and status.get("date") == today and status.get("status") == "ok":
        print(f"今天 {today} 已成功执行，无需补跑")
        return
    reason = "无执行记录" if not status else f"状态 {status.get('status')}（{status.get('date')}）"
    print(f"今天 {today} 未成功执行：{reason} → 自动补跑")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_daily.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=7200,
    )
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-2000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
