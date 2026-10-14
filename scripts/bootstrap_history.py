"""全市场历史回填：串行 + 礼貌间隔（baostock 禁并发、日限 5 万次）。

用法：
    .venv/bin/python scripts/bootstrap_history.py --start 20190101
    .venv/bin/python scripts/bootstrap_history.py --start 20190101 --resume   # 断点续跑
    .venv/bin/python scripts/bootstrap_history.py --start 20190101 --limit 20  # 调试小批量
"""
import argparse
import os
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.pipeline import SyncPipeline
from aplus.sources import default_sources
from aplus.store import LocalStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--sleep", type=float, default=0.5, help="每只股票之间的间隔秒数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试）")
    parser.add_argument("--resume", action="store_true", help="跳过主库已有数据的代码")
    args = parser.parse_args()

    start_s = args.start if "-" in args.start else f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}"

    # baostock 禁并发：锁文件防止回填与每日增量同时打主源
    lock = Path(args.data_root) / "bootstrap.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        print(f"已有回填在跑（{lock} 存在），退出。如确认无进程请手动删除锁文件。")
        return
    lock.write_text(str(os.getpid()))

    sources = default_sources()
    primary = sources[0]
    store = LocalStore(Path(args.data_root))
    # overlap 模式：备源（腾讯）仅有近 640 交易日，只对交集严格比对，避免误隔离
    pipeline = SyncPipeline(sources, store, coverage="overlap")

    stats = Counter()
    try:
        stock_list = primary.fetch_stock_list()
        codes = stock_list["code"].tolist()
        print(f"股票列表 {len(codes)} 只（含退市，缓解幸存者偏差）", flush=True)
        if args.limit:
            codes = codes[: args.limit]

        for i, code in enumerate(codes, 1):
            if args.resume and store.read_daily(code) is not None:
                stats["skipped"] += 1
                continue
            try:
                result = pipeline.sync_daily(code, start_s, args.end)
                stats[result.status] += 1
                if result.status != "ok":
                    print(f"[{i}/{len(codes)}] {code}: {result.status} {result.reason[:150]}", flush=True)
            except RuntimeError as exc:
                stats["all_failed"] += 1
                print(f"[{i}/{len(codes)}] {code}: ALL FAILED {exc}", flush=True)
            try:
                factor = primary.fetch_adjust_factor(code, start_s, args.end)
                if factor is not None and not factor.empty:
                    store.upsert_factor(code, factor)
            except Exception as exc:  # noqa: BLE001
                print(f"[{i}/{len(codes)}] {code}: factor failed {exc!r}", flush=True)
            time.sleep(args.sleep)
            if i % 100 == 0:
                print(f"... 进度 {i}/{len(codes)}，当前统计 {dict(stats)}", flush=True)

        print(f"\n回填完成：{dict(stats)}", flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
