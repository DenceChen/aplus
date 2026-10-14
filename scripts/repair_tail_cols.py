"""修复日线尾部「有成交却缺列」的数据洞（turn / isST / amount / pctChg / tradestatus）。

## 这不是补 turn，是补一整批被降级源削掉的数据

2026-10-09 那批 bar 有相当一部分是用**腾讯备源**（仅 OHLCV）写进主库的。
新日期在旧帧里没有对应行可填，于是这五列在末根全是空的：

    601607.SH  末根 2026-10-09  amount/tradestatus/isST/turn/pctChg 全 NaN

影响不止于 turn：
- **`isST` 空 → ST 过滤对该股完全失明**。实测 2026-10-11 出的 20 只推荐里
  有 6 只（30%）末根 isST 为空，其中 5 只在跌篮子。推荐一只 ST 股是无效输出。
- `tradestatus` 空 → 停牌判定失灵
- `pctChg` / `amount` 空 → 依赖这两个因子的评分失真

## 检测器复用体检，不另写一份

「哪些文件有洞」完全交给 `aplus.coverage.tail_nan_columns`——就是体检用的那个
纯函数。同一件事两份实现迟早漂移：体检说健康、修复说坏了，然后没人知道信谁。

## 写完必须复验，不信 status

`sync_daily` 返回 `ok` 只说明「写下去了」，不说明「洞填上了」。源站若再次抖动，
同样会走降级写入，status 还是 `ok`，洞还在。所以每只写完立刻重读文件复验，
没填上的记 `unresolved` 并打印——**报出来，不藏**。

## 三条安全线

1. **写前必备份**：覆盖前把原 parquet 复制到 `data/_backup_tailcols_<时间戳>/daily/`。
   备份在 `sync_daily` **之前**——pipeline 在函数内部就写库了。
2. **单票全隔离**：异常只记这只，继续下一只，最后统一计数上报。
3. **互斥锁**：复用 `data/bootstrap.lock`，baostock 禁并发。

## 用法

    .venv/bin/python scripts/repair_tail_cols.py --dry-run           # 只体检
    .venv/bin/python scripts/repair_tail_cols.py --codes 601607.SH,688137.SH
    .venv/bin/python scripts/repair_tail_cols.py --resume            # 全量续跑
"""
import argparse
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _bootstrap import RUN  # noqa: E402

from aplus.coverage import tail_nan_columns  # noqa: E402
from aplus.pipeline import SyncPipeline  # noqa: E402
from aplus.sources import default_sources  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
from aplus.write_lock import WriteLock  # noqa: E402

# 往回多拉一段：不止末根，有些票尾部缺十几根。只补末根治不了它们。
BACKFILL_DAYS = 45

# 连续多少次 `degraded` 就判定「源站会话已死」，停下来。
#
# 实测（2026-10-12）：修到第 2140 只时 baostock 会话断开，之后 961 只全部
# 走降级路径返回 `degraded`——备源只有 OHLCV，**填不上要补的五列**，
# 于是每只都白跑一次网络往返还必然失败。不熔断就是白等 16 分钟。
DEGRADE_STREAK_LIMIT = 15


def find_broken(daily_dir: Path, wanted: set[str] | None = None) -> dict[str, list[str]]:
    """扫目录，返回 {代码: 尾部空值列列表}。检测逻辑一律走 coverage 的纯函数。"""
    out: dict[str, list[str]] = {}
    for path in sorted(daily_dir.glob("*.parquet")):
        code = path.stem
        if wanted is not None and code not in wanted:
            continue
        try:
            # 全列读入：`tail_nan_columns` 只认 REQUIRED_EXTRA_COLUMNS，
            # 但提前裁列等于把「哪些列算数」复制一份到这里，两处会漂移。
            df = pd.read_parquet(path)
        except Exception:  # noqa: BLE001 — 读不了的文件不算「尾部空值」，交给别的体检
            continue
        gaps = tail_nan_columns(df)
        if gaps:
            out[code] = sorted(gaps)
    return out


def _sync_window(df_last_date: str, today: str) -> tuple[str, str]:
    """回拉窗口：末根往前 45 自然日。存了根数比天数省事，取 45 覆盖约 30 个交易日。"""
    try:
        start = (datetime.strptime(df_last_date[:10], "%Y-%m-%d") - timedelta(days=BACKFILL_DAYS))
    except ValueError:
        start = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=BACKFILL_DAYS)
    return start.strftime("%Y-%m-%d"), today


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只体检不写（默认行为）")
    ap.add_argument("--codes", default="", help="逗号分隔指定代码")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只")
    ap.add_argument("--resume", action="store_true", help="跳过体检已无洞的代码")
    ap.add_argument("--sleep", type=float, default=0.2, help="每只之间的间隔秒")
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--degrade-limit", type=int, default=DEGRADE_STREAK_LIMIT,
                    help="连续多少次 degraded 就熔断（默认 %(default)s；调小是为了测试）")
    args = ap.parse_args()

    root = Path(args.data_root)
    daily_dir = root / "master" / "daily"
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / f"_backup_tailcols_{stamp}"

    with RUN("repair_tail_cols", dry_run=args.dry_run) as run:
        wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
        t0 = time.time()
        broken = find_broken(daily_dir, wanted or None)
        print(f"扫描 {len(list(daily_dir.glob('*.parquet')))} 只，用时 {time.time() - t0:.0f}s")

        col_counter = Counter(c for cols in broken.values() for c in cols)
        print(f"\n尾部有洞：{len(broken)} 只（占扫描数 "
              f"{len(broken) / max(len(list(daily_dir.glob('*.parquet'))), 1):.1%}）")
        print("按列统计：", dict(col_counter.most_common()))
        if not broken:
            print("\n[ok] 没有需要修的洞。")
            return 0

        codes = sorted(broken)
        if args.resume:
            codes = [c for c in codes if broken[c]]
        if args.limit:
            codes = codes[: args.limit]
            print(f"--limit {args.limit}：本次只处理 {len(codes)} 只")
        print(f"前 15 只：{' '.join(codes[:15])}")

        if args.dry_run:
            print("\n[dry-run] 未写入任何数据。去掉 --dry-run 才会真正修复。")
            return 0

        lock = WriteLock(root / "bootstrap.lock")
        lock.acquire()

        stats = Counter()
        unresolved: list[tuple[str, list[str]]] = []
        degrade_streak = 0
        aborted_at = None
        try:
            sources = default_sources()
            store = LocalStore(root)
            pipeline = SyncPipeline(sources, store, coverage="overlap")
            (backup_dir / "daily").mkdir(parents=True, exist_ok=True)
            print(f"\n备份目录：{backup_dir}", flush=True)

            for i, code in enumerate(codes, 1):
                try:
                    path = daily_dir / f"{code}.parquet"
                    last_date = str(pd.read_parquet(path, columns=["date"])["date"].iloc[-1])[:10]
                    shutil.copy2(path, backup_dir / "daily" / path.name)  # 必须在写之前

                    start, end = _sync_window(last_date, today)
                    result = pipeline.sync_daily(code, start, end)

                    # 复验：不信 status，重读文件看洞到底填没填
                    still = tail_nan_columns(pd.read_parquet(path))
                    if still:
                        stats["unresolved"] += 1
                        unresolved.append((code, sorted(still)))
                        degrade_streak = (degrade_streak + 1
                                          if result.status == "degraded" else 0)
                        if degrade_streak >= args.degrade_limit:
                            aborted_at = (i, code)
                            print(f"\n[熔断] 连续 {degrade_streak} 只返回 degraded"
                                  f"（{code}，第 {i} 只）——判定主源会话已死，"
                                  f"继续跑只会用备源白填 OHLCV。\n"
                                  f"        剩余 {len(codes) - i} 只已放弃，"
                                  f"待主源恢复后重跑本脚本即可（--resume 会只挑还没填上的）。",
                                  flush=True)
                            break
                        print(f"[{i}/{len(codes)}] {code}: 未填上 {sorted(still)}"
                              f"（status={result.status}）", flush=True)
                    else:
                        stats["fixed"] += 1
                        degrade_streak = 0
                        if i % 50 == 0:
                            print(f"... {i}/{len(codes)}｜已修 {stats['fixed']}"
                                  f"｜未填 {stats['unresolved']}", flush=True)
                except Exception as exc:  # noqa: BLE001 — 单票失败不拖垮整批
                    stats["failed"] += 1
                    print(f"[{i}/{len(codes)}] {code}: 异常 {exc!r}", flush=True)
                    run.log("stock_failed", level="error", code=code,
                            error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-2000:])
                time.sleep(args.sleep)
        finally:
            lock.release()
            for src in locals().get("sources", []) or []:
                if hasattr(src, "close"):
                    try:
                        src.close()
                    except Exception:  # noqa: BLE001
                        pass

        print(f"\n修复完成：填上 {stats['fixed']}｜未填 {stats['unresolved']}"
              f"｜异常 {stats['failed']}（扫描到 {len(broken)} 只有洞）")
        if aborted_at:
            print(f"⚠️ 因连续降级在第 {aborted_at[0]} 只熔断，"
                  f"还有 {len(codes) - aborted_at[0]} 只没处理。")
        if unresolved:
            print(f"\n未填上的前 30 只（需要人工看，可能是停牌/长期无数据）：")
            for code, cols in unresolved[:30]:
                print(f"  {code}  {cols}")
        print(f"备份目录：{backup_dir}")
        run.log("repair_done", **dict(stats), backup_dir=str(backup_dir))
        return 0


if __name__ == "__main__":
    sys.exit(main())
