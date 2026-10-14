"""复权因子全量修复（2026-10-11 生产事故）。

事故：旧实现从 baostock 取 `adjustFactor` 列。该列偶发垃圾值且**低一个数量级**
——实测 sh.601607 的 2026-07-17 行 adjustFactor=1.020971 而
backAdjustFactor=10.037703。该值被 attach_factor 向前填充后污染 2026-07-17 ~
2026-09-17 近两个月，20 日动量被放大到 +903%，该股霸榜涨名单 Top1 并进入配置
方案，而原始行情完全正常（真实 mom20 = +1.46%）。

本脚本按修复后的取数逻辑（backAdjustFactor + cummax 单调性）重取因子：
- 幂等：重复运行结果一致
- **默认只补损坏项；残缺项请用 --all 全量重取**
  第二次修复才发现的第二类损坏：因子表**不完整**——本地只有 1 条事件，
  源站实有 12 条。首事件之前 attach_factor 按 1.0 填充是对的（baostock 在
  上市日本就返回 back=1.0），但缺了中间事件就会造出 10 倍量级的假跳空。
  单行表完全「单调」，骗过了损坏检测——这是**检测器本身的盲区**。
  故因子表一旦被怀疑有问题，一律 --all 重取，不做局部修补。
- 串行 + 礼貌间隔（baostock 禁并发，日限 5 万次）

用法：
    .venv/bin/python scripts/repair_factors.py            # 全量（5556 只，约 40 分钟）
    .venv/bin/python scripts/repair_factors.py --limit 50  # 试跑
    .venv/bin/python scripts/repair_factors.py --audit     # 只体检不写入
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.adjust import is_factor_corrupt
from aplus.sources.baostock_source import BaostockSource
from aplus.store import LocalStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--audit", action="store_true", help="只体检不写入")
    parser.add_argument("--all", action="store_true",
                        help="强制重取全部（不只补损坏项）——因子表**残缺**时必须用")
    parser.add_argument("--codes", default="",
                        help="逗号分隔的指定代码，只处理这些（体检后定点修复用，省 30 分钟）")
    args = parser.parse_args()

    store = LocalStore(Path(args.data_root))
    daily_dir = store.root / "master" / "daily"
    codes = [p.stem for p in sorted(daily_dir.glob("*.parquet"))]
    # 孤儿因子表（有因子、无日线）也纳入修复范围——旧代码只遍历日线代码，
    # 导致这批永远修不了，却一直被审计算成损坏。定点修复时尤其需要它们可达。
    from aplus.adjust import classify_factor_files
    _usable, _orphans = classify_factor_files(Path(args.data_root))
    codes = sorted(set(codes) | set(_orphans))
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
        missing = want - set(codes)
        if missing:
            print(f"⚠️ 主库里没有这些代码，已跳过：{' '.join(sorted(missing))}")
    if args.limit:
        codes = codes[: args.limit]

    src = BaostockSource()
    t0 = time.time()
    repaired = checked = failed = newly_added = 0
    still_bad = []
    for i, code in enumerate(codes, 1):
        old = store.read_factor(code)
        corrupt = is_factor_corrupt(old) or args.all
        if args.audit:
            checked += 1
            if corrupt:
                repaired += 1
                if len(still_bad) < 10:
                    still_bad.append(code)
            if i % 500 == 0:
                print(f"... 体检 {i}/{len(codes)} 异常 {repaired}（{time.time() - t0:.0f}s）", flush=True)
            continue
        if not corrupt:
            continue
        try:
            ev = src.fetch_adjust_factor(code, "1990-01-01", "2099-12-31")
        except Exception as exc:  # noqa: BLE001 — 单只失败不阻断全量修复
            failed += 1
            if failed <= 5:
                print(f"[{i}] {code}: {exc!r}", flush=True)
            continue
        if ev is None or ev.empty:
            # 无除权事件的股票本就没有因子事件：写空表清掉旧垃圾
            if old is not None and not old.empty:
                pd.DataFrame(columns=["date", "adjust_factor"]).to_parquet(
                    store.root / "master" / "factor" / f"{code}.parquet", index=False)
            newly_added += 1
            repaired += 1
        else:
            store.upsert_factor(code, ev)
            repaired += 1
        if repaired <= 10:
            print(f"[{i}] 修复 {code}（原 {0 if old is None else len(old)} 事件 → "
                  f"{0 if ev is None else len(ev)}）", flush=True)
        time.sleep(args.sleep)
        if i % 500 == 0:
            print(f"... 进度 {i}/{len(codes)}｜已修复 {repaired}｜新增 {newly_added}｜"
                  f"失败 {failed}｜{time.time() - t0:.0f}s", flush=True)
    src.close()

    if args.audit:
        print(f"\n体检完成：{checked} 只｜**损坏 {repaired} 只**（{repaired / max(checked,1):.1%}）")
        print("样例：", " ".join(still_bad))
    else:
        print(f"\n修复完成：修复 {repaired} 只（其中无事件清空 {newly_added}）｜失败 {failed}｜"
              f"耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
