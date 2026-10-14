#!/usr/bin/env python
"""一键健康检查：昨晚/今天到底跑没跑、成没成、哪一步炸的、耗时多久。

**排查的第一条命令，不该是 `grep`。** 凌晨出事时，你要的答案通常只有四个：
跑了吗 / 成没成 / 哪一步 / 多久。下面一条命令全给你。

用法：
    .venv/bin/python scripts/healthcheck.py            # 今天 + 昨天
    .venv/bin/python scripts/healthcheck.py --days 7
    .venv/bin/python scripts/healthcheck.py --json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.log import read_logs, read_runs, rotate_logs

ROOT = Path(__file__).resolve().parent.parent
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""


def _color_status(status: str) -> str:
    if status == "ok":
        return f"{GREEN}ok{RESET}"
    if status == "error":
        return f"{RED}error{RESET}"
    if status == "running":
        return f"{YELLOW}running{RESET}"
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="回看几天")
    ap.add_argument("--json", action="store_true", help="输出机器可读")
    ap.add_argument("--errors", type=int, default=15, help="最多显示几条 error 事件")
    args = ap.parse_args()

    data_root = ROOT / "data"
    runs = read_runs(data_root, limit=200)
    rows = [r for r in runs][: args.days * 6]

    errs = [e for e in read_logs(data_root, tail=2000)
            if e.get("level") == "error"][-args.errors:]

    if args.json:
        print(json.dumps({"runs": rows, "errors": errs},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"\n{'═'*78}")
    print(f"  aplus 健康检查   {DIM}最近 {len(rows)} 次运行{RESET}")
    print(f"{'═'*78}\n")

    if not rows:
        print(f"{YELLOW}没有任何运行记录 —— 定时任务可能从未成功跑过{RESET}")
        print(f"检查：launchctl list | grep aplus\n")
        return 1

    print(f"{'时间':<20}{'模块':<16}{'状态':<10}{'耗时':>8}  备注")
    print(f"{'─'*78}")
    bad = 0
    for r in rows:
        note = r.get("error") or ""
        if not note:
            extra = {k: v for k, v in r.items()
                     if k not in ("ts", "module", "status", "duration_s", "finished",
                                  "run_id", "error", "level")}
            note = " ".join(f"{k}={v}" for k, v in list(extra.items())[:3])
        if r["status"] != "ok":
            bad += 1
        dur = f"{r['duration_s']:.0f}s" if isinstance(r.get("duration_s"), (int, float)) else "—"
        print(f"{r.get('ts',''):<20}{r.get('module',''):<16}"
              f"{_color_status(r['status']):<18}{dur:>8}  {note[:40]}")

    print(f"\n{'─'*78}")
    if bad:
        print(f"{RED}{bad} 次运行未成功{RESET}")
    else:
        print(f"{GREEN}全部成功{RESET}")

    if errs:
        print(f"\n{'─'*78}\n最近的 error 事件（{len(errs)} 条）")
        for e in errs[-args.errors:]:
            print(f"  {e.get('ts')}  [{e.get('module')}] {e.get('event')}")
            if e.get("error"):
                print(f"      {e['error'][:150]}")
            if e.get("step"):
                print(f"      步骤: {e['step']}")

    rotate_logs(data_root)
    print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())