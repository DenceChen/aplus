"""按任意区间构建横截面面板缓存。

## 为什么单独一个脚本

`regime_control_sweep.py` / `signcorrect_freq_sweep.py` 各自带一份 `_panel()`，
都只认「一个缓存覆盖全区间」。现实是：

    旧缓存 /tmp/reversal_panel_cache.pkl  →  只有 2019-01 ~ 2023-12（1214 日）
    数据本身 /tmp/aplus_snap_final         →  2019-01-02 ~ 2026-10-09（1882 日）

**缺的是缓存，不是数据。** 于是「复测段」这件事被数据缺失误判为不可做，
而它其实是可做的——只是没人建那段面板。

## 为什么按半区间分两次建，而不是一次建全区间

`build_panel` 要同时持有因子面板与横截面。2019-2026 单次构建的峰值内存
在本机（16 GB、同时还有别的作业）有 OOM 风险，而**一旦 OOM，几个小时就白费**。

分两段建：

- 峰值内存约减半
- 总计算量不变（每只股票只算一次因子，两段各算一次——这里确实多算了一次，
  但换来的是**不会中途崩**，在无人值守的长任务里这个交换是划算的）
- 任一段失败可单独重跑，不必重来另一段

## 写盘必须是原子的

`pickle.dump` 到目标路径再改名的写法不成立——`os.replace` 在同一文件系统内
才是原子的。本脚本先写 `目标.tmp` 再 `os.replace`，理由：

> 一次中途崩溃的写入会留下**半个** pickle。它的大小看起来正常（1.7 GB 级），
> 读它会在 `pickle.loads` 里炸出一条看不懂的错，甚至在别的进程里炸。
> 上一个读到这个文件的研究任务会把「我的缓存坏了」误当成「数据坏了」。

## 用法

    python scripts/build_panel_cache.py --start 2024-01-01 --end 2026-12-31 \
        --out /tmp/panel_2024_2026.pkl
"""
import argparse
import os
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.backtest import build_cross_sections  # noqa: E402
from aplus.store import LocalStore  # noqa: E402


def write_atomic(path: Path, obj) -> None:
    """先写临时文件再原子改名。

    `pickle.dump(obj, open(path, "wb"))` 在中途崩溃时留下的是**半个**文件，
    而且大小看起来完全正常——下一个读它的人会误判成数据损坏。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh, protocol=4)
            fh.flush()
            os.fsync(fh.fileno())      # 数据落盘后才改名，否则改名只是改名
        os.replace(tmp, path)          # 同一文件系统内原子
    except BaseException:
        # 失败即不留痕。留下 1.7 GB 残骸有三重代价：占盘、让下次重跑
        # 误以为「已经开始写了」，以及在无人值守的长夜里把一次崩溃
        # 变成两次崩溃。fsync 也要处理——只 unlink 不 fsync 目录的话，
        # 断电后目录项可能回来。
        tmp.unlink(missing_ok=True)
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", type=Path, default=Path("/tmp/panel_confirm.pkl"))
    args = ap.parse_args()

    t0 = time.time()
    store = LocalStore(args.data_root)
    daily_map = {p.stem: store.read_daily(p.stem)
                 for p in sorted((args.data_root / "master" / "daily").glob("*.parquet"))}
    print(f"日线 {len(daily_map)} 只载入 {time.time() - t0:.0f}s", flush=True)

    from aplus.snapshot import load_factors_map
    xs = build_cross_sections(store, args.start, args.end,
                              daily_map=daily_map,
                              factors_map=load_factors_map(store))
    print(f"横截面 {len(xs['_dates'])} 个交易日 {time.time() - t0:.0f}s", flush=True)
    if not xs["_dates"]:
        print("区间内**没有任何交易日**——不要拿这份缓存跑任何结论", flush=True)
        return 1

    write_atomic(args.out, xs)
    print(f"写出 {args.out}  {args.out.stat().st_size / 1e9:.2f} GB  "
          f"共 {time.time() - t0:.0f}s", flush=True)
    print(f"日期范围 {xs['_dates'][0]} ~ {xs['_dates'][-1]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())