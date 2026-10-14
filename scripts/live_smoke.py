"""实网冒烟：单只股票双源拉取 + 交叉校验 + 落库 + 复权现算演示。

用法：
    .venv/bin/python scripts/live_smoke.py --code 000001.SZ --days 30
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.adjust import attach_factor, to_hfq, to_qfq
from aplus.pipeline import SyncPipeline
from aplus.sources import default_sources
from aplus.store import LocalStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="000001.SZ")
    parser.add_argument("--days", type=int, default=30)  # 覆盖节假日窗口
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    start_s, end_s = start.isoformat(), end.isoformat()

    print(f"== aplus live smoke: {args.code} {start_s} ~ {end_s} ==")
    sources = default_sources()
    print(f"源链: {[s.name for s in sources]}")

    store = LocalStore(Path(args.data_root))
    pipeline = SyncPipeline(sources, store)
    result = pipeline.sync_daily(args.code, start_s, end_s)
    print(f"sync 结果: status={result.status} sources={result.sources_used} "
          f"degraded_from={result.degraded_from} cross_checked={result.cross_checked} "
          f"mismatch_ratio={result.mismatch_ratio:.4f}")
    if result.reason:
        print(f"  reason: {result.reason}")

    df = store.read_daily(args.code)
    if df is None or df.empty:
        print("!! 主库无数据（可能被隔离）——查看 data/quarantine/")
        quarantine = store.read_quarantine_daily(args.code)
        if quarantine is not None:
            print(quarantine.tail(3).to_string(index=False))
        return 1

    print(f"\n主库 {len(df)} 行，前 3 行：")
    print(df.head(3).to_string(index=False))
    print("后 3 行：")
    print(df.tail(3).to_string(index=False))

    # 复权演示：因子事件表 + 前向填充 + 现算
    factor = sources[0].fetch_adjust_factor(args.code, start_s, end_s)
    if factor is not None and not factor.empty:
        print(f"\n复权因子事件 {len(factor)} 条，示例：")
        print(factor.tail(3).to_string(index=False))
        merged = attach_factor(df, factor)
        qfq = to_qfq(merged)
        hfq = to_hfq(merged)
        show = pd.DataFrame({
            "date": merged["date"],
            "raw_close": merged["close"],
            "factor": merged["adjust_factor"],
            "qfq_close": qfq["close"].round(4),
            "hfq_close": hfq["close"].round(4),
        })
        print("\n复权现算对比（后 5 行）：")
        print(show.tail(5).to_string(index=False))
    else:
        print("\n窗口内无除权事件（因子恒为 1.0）")

    print("\n== live smoke 通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
