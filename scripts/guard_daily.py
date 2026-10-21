"""每日守卫：19:30 确保当天的 run_daily 出了名单，失败/缺失则自动补跑。

launchd: com.aplus.guard

## 🚨 为什么要先问「是不是已经在跑」

2026-10-20 实测（`data/logs/2026-10-20.jsonl`）：

    18:30:04  step_start update          ← 第一跑开始
    18:31:36  run_end  error             ← 采集被误诊成「非交易日」，rc=2
    19:30:02  step_start update          ← 守卫补跑开始
    19:33:45  step_skipped update        ← 写库锁被占（pid 21288）⇒ 采集又跳过
    19:33:45  step_start update          ← **第三跑**同时开始（并发！）
    20:58:26  run_end  ok                ← 第三跑发布
    21:12:53  run_end  ok                ← 守卫那一跑**又发布一次**

⇒ **两跑各发一次名单。** 而写库锁只挡「并发写 parquet」，
**挡不住第二个进程照样往下走、照样出单**。

⚠️ 更糟的是这不是偶发：**正常的一天**里 18:30 那跑要跑到 ~20:58，
19:30 的守卫去看 `run_status.json` 时**那文件还是昨天的**
（状态是跑完才写的）⇒ 守卫必然判定「未成功」⇒ **每天重跑一遍完整流水线、
每天重复发布一次**。⇒ 那不是「偶发的重复」，是**结构性的重复**。

⇒ 所以守卫的判据顺序必须是：
    ① 周末 ⇒ 本来就不该跑
    ② **有一跑在飞 ⇒ 等它跑完**（并发 = 重复发布 + 白烧一轮 CPU）
    ③ 跑完了且 status == "ok" ⇒ 不用补
    ④ 否则 ⇒ 补跑
"""
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.log import read_runs
from aplus.reports import read_run_status
from aplus.trading_calendar import (
    RUN_DAY_WEEKEND, classify_run_day, trading_days,
)

#: 等待在飞那一跑的预算（秒）。实测完整流水线 ~1.5 小时（5460 只逐只采集）。
#: ⚠️ 预算必须由**实测**推导：拿「策略臂耗时」外推是本仓反复踩的坑
#: （守卫的等待预算要按「机器最满的时候」定）。
WAIT_BUDGET_S = 7200
POLL_S = 30


def _daily_runs(data_root):
    """今天 `daily` 模块的运行汇总（`read_runs` 已是模块级纯函数 ⇒ 可 import）。"""
    try:
        return [r for r in read_runs(data_root, limit=50)
                if r.get("module") == "daily"]
    except Exception:  # noqa: BLE001 — 日志读不出来不该让守卫罢工
        return []


def _daily_run_in_flight(data_root):
    """有一跑 `run_daily` **还没结束**吗？⇒ 返回那条汇总（没有就 `None`）。

    判据是 `run_start` 有、**配对的 `run_end` 没有** ⇒ 「在飞」是
    **可观测的事实**，不是「进程还在」（第 158 层：PID 存活探针不是身份）。
    """
    for row in _daily_runs(data_root):
        if not row.get("finished"):
            return row
    return None


def _wait_for_run(run_id, data_root, budget_s=WAIT_BUDGET_S, poll_s=POLL_S):
    """等某一跑结束。返回它最终**成没成**（`None` = 超时，仍在飞）。

    ## 🚨🚨 2026-10-21 修一个**真 bug**（这一行的原实现把安全网弄断了）

    原实现是：

        if row.get("run_id") == run_id:
            return row.get("status") if row.get("finished") else None

    ⇒ **找到那条 run、发现它还没结束 ⇒ 立刻返回 `None`（放弃）**
    ⇒ ⇒ 「等了 7200s 那一跑仍未结束」那句话是**假的**（实际等了 **0 秒**）
    ⇒ ⇒ **安全网是坏的**：18:30 那班若**失败**，守卫会**立刻放弃而不补跑**
    ⇒ ⇒ 而**没有任何东西会红** ⇒ 因为那条守卫把 `_wait_for_run`
    **整个 monkeypatch 掉了** ⇒ 真函数**从没被跑过**。

    ⇒ 现在：**找到但未结束 ⇒ 继续等**（不是放弃）
    ⇒ 只有**真超时**（budget 用完）才返回 `None`。
    ⇒ 且**超时那一刻的等待时长是真的**（消息里那句「等了 Ns」从此不再撒谎）。
    """
    deadline = time.time() + budget_s
    while time.time() < deadline:
        for row in _daily_runs(data_root):
            if row.get("run_id") == run_id and row.get("finished"):
                return row.get("status")
        time.sleep(poll_s)
    return None


def main():
    data_root = ROOT / "data"
    reports_dir = data_root / "reports"
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

    # ── ② 有一跑在飞 ⇒ **等它跑完**，不要并发第二跑 ──────────────────
    flying = _daily_run_in_flight(data_root)
    if flying is not None:
        rid = flying.get("run_id")
        print(f"今天已有一跑 run_daily 在飞（run_id={rid}，始于 {flying.get('ts')}）"
              f"⇒ 等它跑完再看（并发 = 重复发布 + 白烧一轮 CPU）")
        final = _wait_for_run(rid, data_root)
        if final is None:
            print(f"⚠️ 等了 {WAIT_BUDGET_S}s 那一跑仍未结束 ⇒ 本守卫不补跑"
                  f"（再开一跑只会与它并发）")
            return
        print(f"在飞那一跑结束了：status={final}")

    # ── ③ 跑完了且成功 ⇒ 不用补 ────────────────────────────────────
    status = read_run_status(reports_dir)
    if status and status.get("date") == today and status.get("status") == "ok":
        print(f"今天 {today} 已成功执行，无需补跑")
        return

    # ── ④ 否则补跑 ──────────────────────────────────────────────────
    reason = "无执行记录" if not status else f"状态 {status.get('status')}（{status.get('date')}）"
    print(f"今天 {today} 未成功执行：{reason} → 自动补跑")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_daily.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=10800,
    )
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-2000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
