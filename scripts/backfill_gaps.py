"""补日线覆盖缺口：缺列 / 历史被截断 / 中间断层 / 尾端停更。

## 为什么要单独写这个脚本

`bootstrap_history.py` 补不回来，原因有两层：

1. **源层**：`SyncPipeline` 在主源失败、只有备源成功时走 `len(frames) == 1`
   分支，把**腾讯备源**（约 640 交易日、仅 OHLCV）直接 `upsert_daily` 进主库，
   返回 `status="degraded"`。主源一抖动，历史就被砍头。
2. **调度层**：`--resume` 的跳过条件是 `store.read_daily(code) is not None`
   ——「有文件就跳过」。缺口文件照样有文件，于是永远被跳过，永不自愈。

本脚本只处理「体检判定确有缺口」的代码，且默认**拒绝**降级写入。

## 三条不可让步的安全线

1. **写前必备份**：任何一次覆盖之前，先把将被覆盖的 parquet 复制到
   `data/_backup_gapfill_<YYYYMMDD-HHMMSS>/`。备份发生在**调用 sync_daily
   之前**——因为 pipeline 是在函数内部写库的，等它返回再备份已经晚了。
2. **默认不写降级数据**：`--allow-degraded` 默认关闭。腾讯备源只有 640 日历史且
   缺 5 列，写进去会把「缺 5 年」变成「有 5 年但 31% 股票缺列」——后者更难发现，
   因为表面上「有数据」。默认只记 skipped 并打印原因。
   为此这里用 `_StagingStore` 把 pipeline 的写操作截下来：pipeline 内部照常调
   `upsert_daily`，但只进内存暂存，等状态返回后再决定落不落主库。
3. **单只失败不拖垮整批**：每只股票独立 try，异常记 traceback 后继续下一只。

## 锁

复用 `bootstrap_history.py` 的同一个 `data/bootstrap.lock`：baostock 禁并发，
两个回填脚本必须互斥。

## 用法

    .venv/bin/python scripts/backfill_gaps.py --dry-run                    # 默认，只体检
    .venv/bin/python scripts/backfill_gaps.py --dry-run --no-remote        # 不联网，纯启发式
    .venv/bin/python scripts/backfill_gaps.py --codes 000001.SZ,600519.SH # 定点补
    .venv/bin/python scripts/backfill_gaps.py --limit 20                   # 小批量试跑
    .venv/bin/python scripts/backfill_gaps.py --resume --sleep 0.5         # 全量续跑
"""
import argparse
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _bootstrap import RUN  # noqa: E402

from aplus.coverage import audit_directory, classify_file  # noqa: E402
from aplus.pipeline import SyncPipeline  # noqa: E402
from aplus.sources import default_sources  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
from aplus.write_lock import WriteLock  # noqa: E402


class _StagingStore:
    """拦截 `upsert_daily` 的写库代理：先暂存，拿到 sync_daily 的状态再决定落不落。

    `SyncPipeline.sync_daily` 是在**函数内部**调 `store.upsert_daily` 的，返回
    `degraded` 时数据已经写下去了。想「降级不写」，只能在存储层截一道。

    `quarantine_daily` 直接透传——隔离区本来就是安全区，不碰主库。
    """

    def __init__(self, store):
        self._store = store
        self._pending: dict = {}

    @property
    def root(self):
        return self._store.root

    def upsert_daily(self, code: str, df) -> None:
        self._pending[code] = df          # 只进内存，不落盘

    def quarantine_daily(self, code: str, df, reason: str) -> None:
        self._store.quarantine_daily(code, df, reason)

    def commit(self, code: str) -> bool:
        df = self._pending.pop(code, None)
        if df is None:
            return False
        self._store.upsert_daily(code, df)
        return True

    def discard(self, code: str) -> None:
        self._pending.pop(code, None)


def _parse_date(text: str) -> str:
    return text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _backup(path: Path, backup_dir: Path, sub: str) -> None:
    """覆盖前把原文件复制进备份区。文件不存在（首次落库）就没什么可备的。"""
    if not path.exists():
        return
    dest = backup_dir / sub / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def _load_registry(enabled: bool):
    """一次只读调用拿全市场上市/退市登记日——判新股/退市唯一可靠的依据。

    实测铁证：`000001.SZ`（1991 上市）被砍成 17 行、`301699.SZ`（2026-09-09 上市）
    恰好也是 17 行且首行同为 2026-09-09。只看 parquet 永远分不开，必须有登记。
    拿不到就退化成启发式（偏保守，见 aplus.coverage 模块 docstring）。
    """
    if not enabled:
        return {}, {}, "已禁用（--no-remote）"
    try:
        from aplus.sources.baostock_source import BaostockSource
        src = BaostockSource()
        try:
            table = src.fetch_stock_list()
        finally:
            src.close()
        ipo = {r.code: r.ipo_date for r in table.itertuples()
               if r.ipo_date and str(r.ipo_date) not in ("nan", "")}
        out = {}
        for r in table.itertuples():
            v = str(r.delist_date) if r.delist_date is not None else ""
            if v and v not in ("nan", "", "None"):
                out[r.code] = v
        return ipo, out, f"baostock 股票列表 {len(table)} 只"
    except Exception as exc:  # noqa: BLE001 — 拿不到登记就退启发式，不能因此拒绝体检
        return {}, {}, f"⚠️ 拉取上市登记失败，退化为启发式判定：{type(exc).__name__}: {exc}"


def _print_report(report: dict, limit: int = 25) -> None:
    print(f"\n目录：{report['daily_dir']}｜文件 {report['n_files']}")
    print(f"窗口：{report['target_start']} ~ {report['target_end']}")
    print(f"健康 {report['n_healthy']}｜有问题 {report['n_unhealthy']}"
          f"（{report['n_unhealthy'] / max(report['n_files'], 1):.1%}）")
    print(f"判定为新股（首行=上市日，不算缺口）{report['n_new_listing']}"
          f"｜判定为已退市（尾端早于窗口，不算缺口）{report['n_delisted']}")
    print("\n按原因分组（互斥，各组之和 = 文件总数）：")
    for reason, n in sorted(report["primary_reason_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<20s} {n:>6d}")
    print("\n按原因计数（重叠，一只可命中多项）：")
    for reason, n in sorted(report["reason_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<20s} {n:>6d}")
    codes = report["needs_backfill"]
    print(f"\n需补数 {len(codes)} 只，前 {min(limit, len(codes))} 只：")
    print("  " + " ".join(codes[:limit]) + (" …" if len(codes) > limit else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="只体检、只打印，不写任何数据（默认行为）")
    parser.add_argument("--codes", default="", help="逗号分隔的指定代码，只处理这些")
    parser.add_argument("--start", default="20190101", help="回填起始日 YYYYMMDD")
    parser.add_argument("--end", default=date.today().isoformat(), help="回填结束日")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--sleep", type=float, default=0.5, help="每只之间的礼貌间隔秒数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试）")
    parser.add_argument("--resume", action="store_true",
                        help="跳过体检已判为健康的代码（注意：不是「有文件就跳过」）")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="允许把单源降级数据写进主库（默认关闭）")
    parser.add_argument("--no-remote", action="store_true",
                        help="不联网拉上市登记，用纯启发式判定新股/退市")
    args = parser.parse_args()

    start_s = _parse_date(args.start)
    end_s = _parse_date(args.end)
    daily_dir = Path(args.data_root) / "master" / "daily"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(args.data_root) / f"_backup_gapfill_{stamp}"

    with RUN("backfill_gaps", dry_run=args.dry_run, start=start_s, end=end_s) as run:
        ipo, delist, registry_note = _load_registry(not args.no_remote)
        print(registry_note, flush=True)

        want = {c.strip() for c in args.codes.split(",") if c.strip()}

        with run.step("覆盖度体检"):
            if want:
                # 定点模式只体检这几只——全量体检要读 5000+ 个 parquet，没意义
                files = {}
                for code in sorted(want):
                    path = daily_dir / f"{code}.parquet"
                    if not path.exists():
                        print(f"⚠️ 主库里没有 {code}，跳过")
                        continue
                    files[code] = classify_file(path, start_s, end_s,
                                                listing_date=ipo.get(code),
                                                delist_date=delist.get(code))
                unhealthy = sorted(c for c, f in files.items() if not f["is_healthy"])
                for code in sorted(want):
                    if code in files and files[code]["is_healthy"]:
                        print(f"ℹ️ {code} 体检健康，无需补数")
                report = {
                    "daily_dir": str(daily_dir),
                    "target_start": start_s, "target_end": end_s,
                    "n_files": len(files),
                    "n_healthy": len(files) - len(unhealthy),
                    "n_unhealthy": len(unhealthy),
                    "n_new_listing": sum(f["is_new_listing"] for f in files.values()),
                    "n_delisted": sum(f["is_delisted"] for f in files.values()),
                    "reason_counts": dict(Counter(r for f in files.values() for r in f["reasons"])),
                    "primary_reason_counts": dict(Counter(f["primary_reason"] for f in files.values())),
                    "needs_backfill": unhealthy,
                    "files": files,
                }
                codes = unhealthy
            else:
                report = audit_directory(daily_dir, start_s, end_s,
                                         listing_dates=ipo, delist_dates=delist)
                codes = list(report["needs_backfill"])
                if args.resume:
                    before = len(codes)
                    codes = [c for c in codes if not report["files"][c]["is_healthy"]]
                    print(f"--resume：跳过体检健康项 {before - len(codes)} 只")

            _print_report(report)
            if args.limit:
                codes = codes[: args.limit]
                print(f"\n--limit {args.limit}：本次只处理 {len(codes)} 只")

        if args.dry_run or not codes:
            if args.dry_run:
                print("\n[dry-run] 未写入任何数据。去掉 --dry-run 才会真正回填。")
            return 0

        # --- 真回填 --------------------------------------------------------
        lock = WriteLock(Path(args.data_root) / "bootstrap.lock")
        lock.acquire()

        stats = Counter()
        try:
            sources = default_sources()
            primary = sources[0]
            store = LocalStore(Path(args.data_root))
            guard = _StagingStore(store)
            pipeline = SyncPipeline(sources, store, coverage="overlap")
            backup_dir.mkdir(parents=True, exist_ok=True)
            print(f"\n备份目录：{backup_dir}", flush=True)

            for i, code in enumerate(codes, 1):
                t0 = time.time()
                try:
                    # 备份必须早于 sync_daily：pipeline 在函数内部就写库了
                    before_rows = report["files"].get(code, {}).get("rows", 0)
                    _backup(daily_dir / f"{code}.parquet", backup_dir, "daily")
                    _backup(Path(args.data_root) / "master" / "factor" / f"{code}.parquet",
                            backup_dir, "factor")

                    result = pipeline.sync_daily(code, start_s, end_s)
                    if result.status == "degraded" and not args.allow_degraded:
                        guard.discard(code)
                        stats["skipped_degraded"] += 1
                        reason = (result.reason or "").replace("\n", " ")[:160]
                        print(f"[{i}/{len(codes)}] {code}: 跳过（单源降级，"
                              f"备源只有约 640 日且缺列；--allow-degraded 可放行） {reason}",
                              flush=True)
                        run.log("degraded_skipped", code=code, reason=reason)
                        continue

                    guard.commit(code)
                    stats[result.status] += 1
                    if result.status != "ok":
                        print(f"[{i}/{len(codes)}] {code}: {result.status} "
                              f"{result.reason[:150]}", flush=True)
                    else:
                        after = classify_file(daily_dir / f"{code}.parquet", start_s, end_s,
                                              listing_date=ipo.get(code),
                                              delist_date=delist.get(code))
                        print(f"[{i}/{len(codes)}] {code}: ok  {before_rows} -> {after['rows']} 行"
                              f"｜仍缺列 {after['missing_cols'] or '无'}"
                              f"｜首行缺口 {after['missing_head']} 天"
                              f"｜{time.time() - t0:.1f}s", flush=True)

                    try:
                        factor = primary.fetch_adjust_factor(code, start_s, end_s)
                        if factor is not None and not factor.empty:
                            store.upsert_factor(code, factor)
                    except Exception as exc:  # noqa: BLE001 — 因子失败不该拖垮日线
                        stats["factor_failed"] += 1
                        run.log("factor_failed", level="error", code=code,
                                error=f"{type(exc).__name__}: {exc}")

                except RuntimeError as exc:
                    guard.discard(code)
                    stats["all_failed"] += 1
                    print(f"[{i}/{len(codes)}] {code}: 全部源失败 {exc}", flush=True)
                    run.log("all_failed", level="error", code=code, error=str(exc))
                except Exception as exc:  # noqa: BLE001 — 单只失败不拖垮整批
                    guard.discard(code)
                    stats["failed"] += 1
                    print(f"[{i}/{len(codes)}] {code}: 异常 {exc!r}", flush=True)
                    run.log("stock_failed", level="error", code=code,
                            error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-4000:])
                time.sleep(args.sleep)
                if i % 50 == 0:
                    print(f"... 进度 {i}/{len(codes)}｜{dict(stats)}", flush=True)
        finally:
            lock.release()
            for src in (locals().get("primary"),):
                if src is not None and hasattr(src, "close"):
                    try:
                        src.close()
                    except Exception:  # noqa: BLE001
                        pass

        ok = stats["ok"]
        print(f"\n回填完成：成功 {ok}｜跳过(降级) {stats['skipped_degraded']}"
              f"｜隔离 {stats['quarantined']}｜全源失败 {stats['all_failed']}"
              f"｜异常 {stats['failed']}｜因子失败 {stats['factor_failed']}")
        print(f"备份目录：{backup_dir}")
        run.log("backfill_done", **dict(stats), backup_dir=str(backup_dir))
        return 0


if __name__ == "__main__":
    sys.exit(main())