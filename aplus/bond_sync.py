"""可转债日线增量同步（路线 A 第四阶段）。

与股票侧 `step_incremental_update` 同构的探针哲学：**先探一只，验证数据真的
前进了，再决定要不要全量拉**。非交易日（周末/节假日）腾讯仍会返回含旧日期的
非空帧，空帧判定在这里同样无效——必须用「数据是否前进」判定。

为什么单独一个模块：转债的失效模式与股票不同
- 腾讯对**摘牌债**返回生涯末段的过期 K 线（不是空）→ 增量必须靠 is_active
  与末根日期判断，而不是靠「拉到了没有」
- 债券宇宙是流动的（新发/摘牌），名单每轮都可能变；本模块只同步**已在库**的
  代码，新增/摘牌的名单维护交给 bond_bootstrap（全量，约 7 分钟）按需跑
"""
import time
from pathlib import Path

import pandas as pd

from .sources.tencent_bond_source import TencentBondSource, _EmptyBondResult

DEFAULT_SLEEP = 0.25
CONSEC_FAIL_PAUSE = 5
PAUSE_SECONDS = 20.0


def load_bond_map(data_root) -> dict:
    """载入转债日线库（{code: DataFrame}）。

    从 scripts/bond_race.py 迁入包内：每日流水线（run_daily）要用它做纸面账户
    重放，而包内模块不应依赖 scripts/ 目录。
    """
    daily_dir = Path(data_root) / "bonds" / "master" / "daily"
    out = {}
    for p in sorted(daily_dir.glob("*.parquet")):
        df = pd.read_parquet(p)
        if df is not None and not df.empty:
            out[p.stem] = df
    return out


def _last_date(path: Path) -> str:
    return str(pd.read_parquet(path)["date"].max())


def sync_bond_daily(data_root, sleep: float = DEFAULT_SLEEP, limit: int = 0,
                    source=None) -> dict:
    """增量推进转债日线库。返回统计字典（不抛异常——失败不得阻塞每日流程）。

    流程：扫末根日期 → 探针一只 → 数据未前进则整体跳过（非交易日）→ 否则重取
    所有落后于最新日期的代码。摘牌债会被反复尝试但拿不到新数据，计入 skipped。
    """
    daily_dir = Path(data_root) / "bonds" / "master" / "daily"
    paths = sorted(daily_dir.glob("*.parquet"))
    if not paths:
        return {"status": "empty", "reason": "转债库为空，先跑 scripts/bond_bootstrap.py"}

    lasts = {}
    for p in paths:
        try:
            lasts[p.stem] = _last_date(p)
        except Exception:  # noqa: BLE001 — 坏文件不该让整轮同步失败
            lasts[p.stem] = ""
    latest = max(lasts.values()) if lasts else ""

    src = source or TencentBondSource()

    # --- 探针：拿最新的一只试拉，验证市场真的开了 ---
    probe = max(lasts, key=lambda c: lasts[c])
    probe_path = daily_dir / f"{probe}.parquet"
    try:
        probe_df = src.fetch_daily(probe, "", "")
    except Exception as exc:  # noqa: BLE001
        return {"status": "probe_failed", "probe": probe, "error": repr(exc)}
    if probe_df is None or probe_df.empty:
        return {"status": "probe_empty", "probe": probe, "latest": latest}
    new_latest = str(probe_df["date"].max())
    if new_latest <= latest:
        return {"status": "no_new_session", "latest": latest,
                "reason": f"探针 {probe} 未前进（{latest} → {new_latest}）"}

    # --- 落后于最新交易日的代码重取 ---
    stale = [c for c, d in lasts.items() if d < new_latest]
    if limit:
        stale = stale[:limit]
    saved = failed = empty = 0
    consec_fail = 0
    for i, code in enumerate(stale, 1):
        try:
            df = src.fetch_daily(code, "", "")
        except _EmptyBondResult:
            empty += 1          # 摘牌/从未上市：稳定为空，不重试
            continue
        except Exception:  # noqa: BLE001 — 瞬时失败（源内已重试一次）
            failed += 1
            consec_fail += 1
            if consec_fail >= CONSEC_FAIL_PAUSE:
                time.sleep(PAUSE_SECONDS)
                consec_fail = 0
            continue
        consec_fail = 0
        if df is None or df.empty:
            empty += 1
            continue
        df.to_parquet(probe_path.with_name(f"{code}.parquet"))
        saved += 1
        if sleep:
            time.sleep(sleep)
    return {"status": "ok", "latest": latest, "new_latest": new_latest,
            "total": len(paths), "stale": len(stale), "saved": saved,
            "failed": failed, "empty": empty}
