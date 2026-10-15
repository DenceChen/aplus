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

# 🚨 探针法定人数（2026-10-15 事故后新增）—— 见本模块 docstring。
#   取 5 的依据：实测「并列在库内最新日」的债里约 1/8 是死的（停止交易），
#   ⇒ 5 只里**一只都不前进**的概率 ≈ (1/8)^5 ≈ 3e-5，
#   而「确实非交易日」时是 5/5 都不前进 —— 两者不可能混淆。
PROBE_SIZE = 5


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


def _probe_codes(lasts: dict, k: int = PROBE_SIZE) -> list[str]:
    """选出探针债：**库内最晚日期并列**的前 `k` 只。

    🚨 为什么不是一只（2026-10-15 事故）
    ------------------------------------------------------------------
    原实现是 `max(lasts, key=lambda c: lasts[c])` —— 一只。
    并列时 `max` 返回**迭代顺序里的第一个**（`lasts` 由 `sorted(paths)` 构建
    ⇒ 字典序最小）。实测当天它恰好选中 `110075.SH`：一只在 2026-10-09 之后
    **停止交易**的债。

    而 `max()` 是**确定性的** ⇒ 这不是「偶尔踩雷」，是**永久冻结**：
    只要那只债还在库里，探针就永远是它，整个 1087 只的库永远停在 10-09，
    而日志天天打印「无新交易日（非交易日）」。

    修法不是「换一只探针」（换一只只是把死点挪个位置），
    是**法定人数** + 取**最大值**：任一只前进即判定市场前进。
    """
    if not lasts:
        return []
    latest = max(lasts.values())
    tied = sorted(c for c, d in lasts.items() if d == latest)
    return tied[:k]


def _quorum_probe(src, codes: list[str]) -> tuple[dict, list]:
    """问每一只探针，返回 (各债源端末根日期, 出错的债)。

    ⚠️ **一只探针抛异常不许否决全局**：异常是这只债的事（网络/源故障），
    不是市场的事。旧实现里它直接 `return probe_failed` 让全库停更 ——
    与「死债」是同一个错误后果、两个不同原因。
    """
    src_dates: dict = {}
    failed: list = []
    for c in codes:
        try:
            df = src.fetch_daily(c, "", "")
        except Exception:  # noqa: BLE001 — 单只探针的故障不否决全局
            failed.append(c)
            continue
        if df is None or df.empty:
            continue          # 空帧对「市场开没开」零信息（同本模块 docstring）
        try:
            src_dates[c] = str(df["date"].max())
        except Exception:  # noqa: BLE001
            failed.append(c)
    return src_dates, failed


def sync_bond_daily(data_root, sleep: float = DEFAULT_SLEEP, limit: int = 0,
                    source=None) -> dict:
    """增量推进转债日线库。返回统计字典（不抛异常——失败不得阻塞每日流程）。

    流程：扫末根日期 → **法定人数探针** → 数据未前进则整体跳过（非交易日）
    → 否则重取所有落后于最新日期的代码。摘牌债会被反复尝试但拿不到新数据，
    计入 skipped。

    🚨 两个返回分支都携带 `probe_codes` / `probe_fetched` /
    `probe_source_dates` / `probe_failed` ——
    「市场没开」是一句**否定**断言，而 2026-10-15 事故里它是被**一只债的沉默**
    撑起来的。没有这四个字段，这句话只能被相信，不能被独立重算
    （第「守卫不许给自己发信号」层：告警不许只凭自己存在就成立）。
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

    # --- 探针：问 PROBE_SIZE 只，任一只前进即判定市场前进 ---
    codes = _probe_codes(lasts)
    src_dates, probe_failed = _quorum_probe(src, codes)
    evidence = {"latest": latest,
                "probe_codes": list(codes),
                "probe_fetched": len(src_dates),
                "probe_source_dates": dict(src_dates),
                "probe_failed": list(probe_failed)}

    if not src_dates:
        # ⚠️ 一只都问不出结果时**不敢说「没开」** —— 那只说明我们根本没问到。
        return {"status": "probe_empty", **evidence,
                "reason": f"全部 {len(codes)} 只探针都没问出结果（"
                          f"其中 {len(probe_failed)} 只抛异常）⇒ 无法判断市场是否开市"}

    # new_latest 取**各探针源端日期的最大值**，不是任何单只的日期。
    new_latest = max(src_dates.values())
    advanced = sorted(c for c, d in src_dates.items() if d > latest)
    evidence["probe_advanced"] = len(advanced)
    evidence["new_latest"] = new_latest

    if new_latest <= latest:
        return {"status": "no_new_session", **evidence,
                "reason": f"{len(src_dates)} 只探针逐只查过，源端最晚仍为 {latest}"
                          f"（{', '.join(f'{c}→{d}' for c, d in sorted(src_dates.items()))}）"}

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
        df.to_parquet(daily_dir / f"{code}.parquet")
        saved += 1
        if sleep:
            time.sleep(sleep)
    return {"status": "ok", **evidence,
            "total": len(paths), "stale": len(stale), "saved": saved,
            "failed": failed, "empty": empty}
