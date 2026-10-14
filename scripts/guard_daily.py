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


def main():
    status = read_run_status(ROOT / "data" / "reports")
    today = date.today().isoformat()
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
