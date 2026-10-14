"""每日增量更新：对主库中每只股票，从库内最后日期增量拉到今天。

建议收盘后 18:30 运行（baostock 复权因子 18:00 更新完毕）。
用法：
    .venv/bin/python scripts/daily_update.py

## 单票隔离（2026-10-11 上午修的生产事故）

2026-10-10 18:30 的日志：

```
[update] 步骤失败：TypeError("'>=' not supported between instances of 'float' and 'str'")
[predict] 池子 5032 只 → 涨 10 / 跌 10
    601607.SH 上海医药 mom20=903.72%
[done] 2026-10-10 全流程完成（partial）
```

原循环：

```python
df = pd.read_parquet(path)
last = df["date"].max()      # ← 空表 / 全 NaN 列 → nan（float）
if last >= end:             # ← float >= str，TypeError
    ...
try:                        # ← try 在这两行**之后**
```

**一只票的空表或坏列，让 5460 只票一只都没更新**；
上层记成 `partial` 后继续出单，于是名单建立在陈旧数据上，
而名单本身没有任何地方说明它是陈旧的。

现在：读表、算 `last`、同步、复权因子**全在 `try` 里**，
单票坏掉只计 `unreadable` 并继续；坏票数量必须出现在统计里，
**静默跳过会让「数据全好」和「一半读不了」看起来一样**。

## 为什么起点是「库内最后日期」而不是「次日」

`sync_daily(code, last, end)` 的 `last` 是**含**的。
这正是末根缺列能被修好的原因：2026-10-09 那根是被不含 `turn` 的备源
写进去的（末根 `turn` 只有 35.7% 有值，历史覆盖率却有 99.5%），
只要从 10-09 起重拉一次，缺失的列就会补上。
"""
import argparse
import time
from collections import Counter
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.pipeline import SyncPipeline
from aplus.sources import default_sources
from aplus.store import LocalStore
from aplus.write_lock import WriteLock


def _last_date(df) -> str | None:
    """库内最后日期。**读不出合法日期就返回 `None`**（= 这只票读坏了）。

    空表与全 NaN 列的 `.max()` 都返回 `nan`（float），
    拿它去和 `'2026-10-12'` 比就是 `TypeError`——而且原代码里
    这一行在 `try` 之外，会杀掉整个循环。
    """
    if df is None or "date" not in getattr(df, "columns", []):
        return None
    last = df["date"].max()
    if not isinstance(last, str) or not last.strip():
        return None
    return last


def update_all(data_root, end: str, sleep: float = 0.5,
               pipeline=None, sources=None, store=None) -> Counter:
    """对每只股票增量更新。返回统计（`unreadable` 是读坏的票数）。

    全程持有 `bootstrap.lock`：本脚本原先一点锁都没有，与回填撞上时两个进程
    同时 concat + `to_parquet` 同一批文件，后写覆盖先写，丢的列和行没人知道。
    锁被占时抛 `LockBusy` 而不是静默继续——静默跳过的后果是「用陈旧数据出单」。
    """
    root = Path(data_root)
    lock = WriteLock(root / "bootstrap.lock")
    lock.acquire()
    try:
        return _update_locked(root, end, sleep, pipeline, sources, store)
    finally:
        lock.release()


def _update_locked(root: Path, end: str, sleep: float,
                   pipeline, sources, store) -> Counter:
    store = store if store is not None else LocalStore(root)
    sources = sources if sources is not None else default_sources()
    pipeline = pipeline if pipeline is not None else SyncPipeline(sources, store)
    primary = sources[0]

    files = sorted((root / "master" / "daily").glob("*.parquet"))
    print(f"主库 {len(files)} 只股票，增量更新至 {end}")

    stats = Counter()
    for i, path in enumerate(files, 1):
        code = path.stem
        # ── 整段隔离：一只票的任何异常都不许中断全市场 ──
        try:
            last = _last_date(pd.read_parquet(path))
            if last is None:
                stats["unreadable"] += 1
                if stats["unreadable"] <= 5:
                    print(f"[{i}/{len(files)}] {code}: 读不出合法日期，跳过")
                continue
            if last >= end:
                stats["up_to_date"] += 1
                continue
            result = pipeline.sync_daily(code, last, end)
            stats[result.status] += 1
            if result.status != "ok":
                print(f"[{i}/{len(files)}] {code}: {result.status} "
                      f"{result.reason[:120]}")
        except RuntimeError as exc:
            stats["all_failed"] += 1
            print(f"[{i}/{len(files)}] {code}: ALL FAILED {exc}")
        except Exception as exc:  # noqa: BLE001
            stats["unreadable"] += 1
            if stats["unreadable"] <= 5:
                print(f"[{i}/{len(files)}] {code}: {exc!r}")
            continue
        try:
            factor = primary.fetch_adjust_factor(code, last, end)
            if factor is not None and not factor.empty:
                store.upsert_factor(code, factor)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(sleep)
        if i % 200 == 0:
            print(f"... 进度 {i}/{len(files)}，当前统计 {dict(stats)}")
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()
    stats = update_all(args.data_root, date.today().isoformat(), sleep=args.sleep)
    print(f"\n增量完成：{dict(stats)}")
    if stats.get("unreadable"):
        print(f"⚠️ {stats['unreadable']} 只股票读不出合法日期，**未更新**——"
              f"它们的末根仍是旧值，下游因子会在这些票上退化")


if __name__ == "__main__":
    main()