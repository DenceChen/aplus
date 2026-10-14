"""修复「整份文件只有 7 列」的 schema 洞（不是尾根缺列，是整列不存在）。

## 与 `repair_tail_cols.py` 的分工（两者不可互相替代）

2026-10-14 主源网络事故留下了**三种**形态：

| 形态 | 样本 | 谁修 |
|---|---|---|
| 尾根缺 5 列（部分行） | 53 只 × 2026-10-14 | `repair_tail_cols.py`（已修完） |
| **整份文件只有 7 列** | `000043.SZ` / `300114.SZ` | **本脚本** |
| 正常 | 其余 5405 只 | — |

🚨 `repair_tail_cols.py` **修不了后一种**：`find_broken` 用
`tail_nan_columns(df)`，而该函数对「列根本不存在」返回 `[]`
（这是刻意语义：缺列归 `missing_columns` 判，不重复计入尾根）。
所以这两只对它完全隐形。

## 为什么会变成「整份 7 列」——根因不是降级写入本身

链路（2026-10-15 实测定位）：

1. baostock 对**停牌日**返回 `open/high/low/close/...` 全为空串、`tradestatus=0`
2. `convert.py:baostock_frame_to_unified` 用裸 `astype(float)` 解析 OHLC
   → `ValueError: could not convert string to float: ''`
3. 流水线把这个异常当成「主源坏了」→ **降级**到腾讯（只有 7 列 OHLCV）
4. 整份文件只剩 7 列；**下一次重试会撞同一个异常**，所以永远补不回来

⇒ 洞是**自我维持**的。已修 `convert.py`（停牌日结转上一收盘价，
与库里既有 1.5 万条停牌行同构），本脚本负责把已经写坏的两份补回来。

## 三条安全线（与 repair_tail_cols 一致，不另发明）

1. **写前必备份**：先复制原 parquet，再动库
2. **不信 status**：每只写完**重读文件**复验，没填上就报 `unresolved`
3. **互斥锁**：复用 `data/bootstrap.lock`，baostock 禁并发

## 用法

    .venv/bin/python scripts/repair_schema_holes.py --dry-run
    .venv/bin/python scripts/repair_schema_holes.py
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow.parquet as pq  # noqa: E402

from aplus.coverage import REQUIRED_COLUMNS, missing_columns  # noqa: E402
from aplus.pipeline import SyncPipeline  # noqa: E402
from aplus.sources import default_sources  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
from aplus.write_lock import WriteLock  # noqa: E402

# 全历史起点：远早于任何 A 股上市日，多拉的部分源站自然不返回。
FULL_HISTORY_START = "1990-01-01"


def find_schema_holes(daily_dir: Path) -> dict[str, list[str]]:
    """扫目录找**整列不存在**的文件。只读 parquet 元数据，不碰数据页。"""
    out: dict[str, list[str]] = {}
    for path in sorted(daily_dir.glob("*.parquet")):
        have = set(pq.ParquetFile(path).schema.names)
        miss = [c for c in REQUIRED_COLUMNS if c not in have]
        if miss:
            out[path.stem] = miss
    return out


def _verify(path: Path) -> tuple[bool, str]:
    """**重读文件**复验，不用 pipeline 的返回值当证据。

    ⚠️ 两层检查，第二层别省：列**存在但整列为空**，和列**根本不存在**是两种失败，
    后者会让 `_merge_preserving_fidelity` 静默跳过 —— 只查 schema 会漏掉它。
    """
    import pandas as pd

    have = set(pq.ParquetFile(path).schema.names)
    miss = [c for c in REQUIRED_COLUMNS if c not in have]
    if miss:
        return False, f"仍缺列 {miss}"

    df = pd.read_parquet(path)
    bad = [c for c in REQUIRED_COLUMNS if df[c].isna().all()]
    if bad:
        return False, f"列已存在但**整列为空**: {bad}"
    return True, f"{len(df)} 行，列齐备"


def main() -> int:  # noqa: C901 — 单文件脚本，扁平流程比拆函数更易读
    ap = argparse.ArgumentParser(description="修复整份文件只有 7 列的 schema 洞")
    ap.add_argument("--dry-run", action="store_true", help="只体检不写")
    ap.add_argument("--codes", default="", help="逗号分隔；缺省=体检出来的全部")
    ap.add_argument("--data-root", default="data")
    args = ap.parse_args()

    root = Path(args.data_root)
    daily_dir = root / "master" / "daily"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / f"_backup_schemaholes_{stamp}"

    holes = find_schema_holes(daily_dir)
    if args.codes:
        wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
        holes = {k: v for k, v in holes.items() if k in wanted}

    print(f"体检：{len(holes)} 个文件整列缺失")
    for code, miss in holes.items():
        print(f"  {code}: 缺 {miss}")

    if args.dry_run or not holes:
        return 0

    # ── 安全线 1：写前必备份 ──────────────────────────────────
    backup_dir.mkdir(parents=True, exist_ok=True)
    for code in holes:
        shutil.copy2(daily_dir / f"{code}.parquet", backup_dir / f"{code}.parquet")
    print(f"已备份 {len(holes)} 份 → {backup_dir}")

    stats: Counter = Counter()
    store = LocalStore(root)
    # ★ `coverage="overlap"` 不是**放宽判据**，是用在该用的地方。
    #   默认 `strict` 的 docstring 写明适用场景是「每日增量，窗口短、双方都应全覆盖」；
    #   而 `overlap` 的 docstring 写的正是本脚本这一档：
    #     「长窗口回填场景——备源如腾讯仅有近 640 交易日，
    #       窗口外的缺失是**能力边界而非数据错误**」
    #
    #   实测证据（000043.SZ）：
    #     全历史 baostock=6127 / 腾讯=640，missing_a=**0**，mismatch_ratio=**0.0000**
    #   ⇒ 主源严格包含备源的日期，且 640 个共同日价格逐位一致，
    #     写入更全的历史是**严格改进**，不是拿可疑数据换覆盖。
    pipeline = SyncPipeline(default_sources(), store, coverage="overlap")

    # ── 安全线 3：互斥锁，baostock 禁并发 ─────────────────────
    with WriteLock(root / "bootstrap.lock"):
        for code in holes:
            try:
                res = pipeline.sync_daily(code, FULL_HISTORY_START, datetime.now().date().isoformat())
                stats["synced"] += 1
            except Exception as exc:  # noqa: BLE001 — 单只失败不该毁掉整场
                stats["error"] += 1
                print(f"  ✗ {code}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                continue

            # ── 安全线 2：不看 status，重读文件复验 ────────────
            ok, why = _verify(daily_dir / f"{code}.parquet")
            if ok:
                stats["fixed"] += 1
                print(f"  ✓ {code}: {why}")
            else:
                stats["unresolved"] += 1
                print(f"  ✗ {code} **未解决**: {why}  ← 报出来，不藏")

    print(f"\n同步 {stats['synced']}｜修好 {stats['fixed']}｜"
          f"未解决 {stats['unresolved']}｜异常 {stats['error']}")
    return 1 if (stats["unresolved"] or stats["error"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
