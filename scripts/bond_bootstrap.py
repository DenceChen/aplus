"""可转债历史数据采集（路线 A 第二阶段，PLATFORM.md §7.5）。

用法：
    .venv/bin/python scripts/bond_bootstrap.py                # 全量（约 5-10 分钟）
    .venv/bin/python scripts/bond_bootstrap.py --limit 20     # 调试小批量
    .venv/bin/python scripts/bond_bootstrap.py --sleep 0.3    # 更礼貌的间隔

流程：fetch_bond_list（沪 SSE 文件 + 深腾讯扫描，含摘牌历史债——防幸存者偏差）
→ 逐只拉腾讯 kline（param={code},day,,,640,，近 ~2.7 年）
→ 存 data/bonds/master/daily/{code}.parquet。

- 串行 + 礼貌间隔（默认 0.25s/只）
- 幂等续跑：已有 parquet 跳过
- 摘牌债腾讯返回过期 K 线（能拿多少存多少，历史债对回测有用）；
  彻底无数据的代码计 empty 不落盘（重跑可再试）
- 限流保护：瞬时失败 TencentBondSource 内部重试一次；连续 3 只失败暂停 30s
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.sources.bond_list import fetch_bond_list
from aplus.sources.tencent_bond_source import TencentBondSource, _EmptyBondResult

CONSEC_FAIL_PAUSE = 3  # 连续失败 N 只 → 暂停 30s（限流保护）
PAUSE_SECONDS = 30.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--sleep", type=float, default=0.25, help="每只之间的间隔秒数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试）")
    args = parser.parse_args()

    out_dir = Path(args.data_root) / "bonds" / "master" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[1/2] 拉取可转债动态名单（沪 SSE + 深腾讯扫描，含摘牌）……", flush=True)
    bonds = fetch_bond_list()
    codes = bonds["code"].tolist()
    if args.limit:
        codes = codes[: args.limit]
    print(f"    名单 {len(bonds)} 只，本次目标 {len(codes)} 只 → {out_dir}", flush=True)

    src = TencentBondSource()
    stats = Counter()
    consec_fail = 0
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        path = out_dir / f"{code}.parquet"
        if path.exists():
            stats["skipped"] += 1
            continue
        try:
            df = src.fetch_daily(code, "", "")  # param={code},day,,,640,
        except _EmptyBondResult:
            stats["empty"] += 1  # 腾讯无此 K 线：从未上市/过老代码，正常
            continue
        except Exception as exc:  # noqa: BLE001 — 网络/限流瞬时失败（源内已重试一次）
            stats["failed"] += 1
            consec_fail += 1
            print(f"[{i}/{len(codes)}] {code}: FAILED {exc!r}", flush=True)
            if consec_fail >= CONSEC_FAIL_PAUSE:
                print(f"    连续 {consec_fail} 只失败——疑似限流，暂停 {PAUSE_SECONDS:.0f}s", flush=True)
                time.sleep(PAUSE_SECONDS)
                consec_fail = 0
            continue
        consec_fail = 0
        if df.empty:
            stats["empty"] += 1
            continue
        df.to_parquet(path)
        stats["saved"] += 1
        time.sleep(args.sleep)
        if i % 50 == 0:
            el = time.time() - t0
            print(f"... 进度 {i}/{len(codes)} | {dict(stats)} | {el:.0f}s", flush=True)

    print(f"\n[2/2] 采集完成：{dict(stats)}（总耗时 {time.time() - t0:.0f}s）", flush=True)
    saved = sorted(p.stem for p in out_dir.glob("*.parquet"))
    print(f"    落盘 {len(saved)} 只（历史累计含既往运行），如 {saved[:3]} …", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
