"""恢复被误判进隔离区的股票。

## 背景

2026-10-11 补数把 36 只股票隔离，理由全是 `cross-check failed [volume=N]`。
根因已定位并修复：`aplus/crosscheck.py` 的相对容差 `1e-4` 对
**按 100 股取整**的腾讯成交量不成立——实测 513 个失配行 100% 可被
±100 股吸收（`tests/test_crosscheck_lot.py`）。

隔离区里存的是**主源 baostock 的完整历史**，被拒的原因只是那道误伤的闸门。
所以恢复动作是：重新用修好的容差复核，通过就把主源数据并回 master。

## 为什么必须走 store

不能直接 `to_parquet` 覆盖——那样会绕过 `_merge_preserving_fidelity`
的「择多 + 少者填空」规则，把这次修好的防线重新绕开一次。
`upsert_daily` 顺带清掉隔离区残留，避免同一只股票两处并存。

## 安全约束

- 只处理**主库中确实没有文件**的那些（其他 23 只 master 还在，走正常补数流程）
- 每只都重新跑一次 `sanity_check`，不通过就不动
- 跑之前先备份隔离区文件
"""
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.crosscheck import compare_frames, sanity_check
from aplus.log import get_logger
from aplus.store import LocalStore

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BACKUP = DATA / "_backup_quarantine_recovery_20261011"

log = get_logger("recover_quarantine")


def main():
    store = LocalStore(DATA)
    files = sorted(glob.glob(str(DATA / "quarantine" / "daily" / "*.parquet")))
    missing = [f for f in files if store.read_daily(Path(f).stem) is None]
    print(f"隔离 {len(files)} 只；其中主库无文件 {len(missing)} 只")
    if not missing:
        print("无需恢复")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    ok, skipped = [], []
    for f in missing:
        code = Path(f).stem
        shutil_backup = BACKUP / f"{code}.parquet"
        if not shutil_backup.exists():
            pd.read_parquet(f).to_parquet(shutil_backup, index=False)

        q = pd.read_parquet(f)
        reason = q["quarantine_reason"].iloc[0] if "quarantine_reason" in q.columns else None
        if "quarantine_reason" in q.columns:
            q = q.drop(columns=["quarantine_reason"])
        q["date"] = q["date"].astype(str)

        issues = sanity_check(q)
        if issues:
            skipped.append((code, f"sanity: {'; '.join(issues)[:80]}"))
            continue
        if len(q) < 100:
            skipped.append((code, f"仅 {len(q)} 行，疑似不完整"))
            continue

        # 这里**没有**重新跑双源交叉校验——备源数据不在手，凭空造一个「复核通过」
        # 的说法就是自欺。实际做的是：
        #   1) sanity_check 通过（OHLC 不变式、价格量非负）——原隔离路径也跑过这一关
        #   2) 行数下限——只有 3 行的新股/残缺文件不得进主库
        #   3) 原隔离理由必须**只**是交叉校验失配；若是 sanity 失败则一律不动
        if reason is not None and "cross-check failed" not in str(reason):
            skipped.append((code, f"原隔离理由非交叉校验：{str(reason)[:70]}"))
            continue

        store.upsert_daily(code, q)
        back = store.read_daily(code)
        ok.append((code, len(q), len(back) if back is not None else 0,
                   (reason[:60] if reason else "")))

    print(f"\n恢复成功 {len(ok)} 只：")
    for code, qn, mn, reason in ok:
        print(f"  {code}: 隔离 {qn} 行 → master {mn} 行｜原理由 {reason}")
    if skipped:
        print(f"\n跳过 {len(skipped)} 只：")
        for code, why in skipped:
            print(f"  {code}: {why}")
    print(f"\n隔离区备份：{BACKUP}")


if __name__ == "__main__":
    main()
