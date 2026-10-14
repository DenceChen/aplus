"""一次性因子补齐：为缺 factor 文件的股票拉取复权因子（回填期间网络错误遗留）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.sources.baostock_source import BaostockSource
from aplus.store import LocalStore


def main():
    store = LocalStore(Path(__file__).resolve().parent.parent / "data")
    daily_dir = store.root / "master" / "daily"
    factor_dir = store.root / "master" / "factor"
    have = {p.stem for p in factor_dir.glob("*.parquet")}
    missing = [p.stem for p in sorted(daily_dir.glob("*.parquet")) if p.stem not in have]
    print(f"缺因子的股票：{len(missing)} 只", flush=True)
    if not missing:
        return

    src = BaostockSource()
    done = failed = 0
    for i, code in enumerate(missing, 1):
        try:
            events = src.fetch_adjust_factor(code, "1990-01-01", "2099-12-31")
            if events is not None and not events.empty:
                store.upsert_factor(code, events)
                done += 1
            else:
                done += 1  # 无除权事件的股票本来就没有因子，不算缺
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if failed <= 10:
                print(f"[{i}] {code}: {exc!r}", flush=True)
        time.sleep(0.35)
        if i % 200 == 0:
            print(f"... {i}/{len(missing)}（成功 {done} 失败 {failed}）", flush=True)
    print(f"因子补齐完成：成功 {done}，失败 {failed}", flush=True)
    # 清理：成功入库的股票若在隔离区有残留文件，移除
    for q in (store.root / "quarantine" / "daily").glob("*.parquet"):
        if (daily_dir / q.name).exists():
            q.unlink()


if __name__ == "__main__":
    main()
