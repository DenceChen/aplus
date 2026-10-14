"""覆盖度体检：分清「新股本来就没那么多数据」与「老股历史被截断」。

## 为什么需要这个模块

`scripts/bootstrap_history.py` 用 `SyncPipeline(default_sources(), store,
coverage="overlap")`，主源 baostock 失败时走 `len(frames) == 1` 分支把**腾讯备源**
（仅约 640 交易日、仅 OHLCV）写进主库并返回 `status="degraded"`。主源一挂，
历史就被截断，而且 `--resume` 的跳过条件是「有文件就跳过」，缺口永不自愈。

体检要回答的问题只有一个：**这只股票的数据是短，还是被砍了头？**
答错两个方向都致命——把老股当新股放过，洞会一直静默存在。

## 判据（这是本模块的核心，不是随手写的阈值）

单看文件本身**无法**区分二者。实测三个铁证：

    000001.SZ  平安银行，1991-04-03 上市，文件首行 2026-09-09，仅 17 行  -> 截断
    600519.SH  贵州茅台，2001-08-27 上市，文件首行 2026-09-09，仅 17 行  -> 截断
    301699.SZ  2026-09-09 上市，       文件首行 2026-09-09，17 行        -> 真新股

三只股票**首行同一天、行数同为 17**，其中两只是被砍的老股。
任何只吃 parquet 的启发式（首行日期聚集、行数、成交额跳变）都会把它们判成同一类。

所以判据分成两层，且**方向刻意不对称**：

1. **有上市/退市登记（权威）**：`listing_date` 说明首行正好是上市日 -> 真新股，
   首行之前本来就没有数据，不算缺口；`delist_date` 早于 `target_end` -> 已退市，
   尾端早于 `target_end` 同理不算缺口。`backfill_gaps.py` 用一次
   `fetch_stock_list()`（5561 行，只读、不写库）注入它，判定变**精确**。

2. **没有登记（降级启发式）**：只认「**正面证据**」才豁免首行缺口——

   - `first - target_start <= head_grace_days`：本来就在窗口起点附近上市；
   - 开板**连续涨停** `>= new_listing_streak` 根：A 股新股上市头几天的独有形态
     （实测：2020-2023 真新股 52% 命中，而 2024-02-19 截断簇 0%、2019 正常股 1%）。

   判不出来就**当缺口报**。理由是代价不对称：把真新股误报成缺口，只是多一次
   幂等的网络请求（源站本来就只返回它上市后的数据，`upsert_daily` 是合并不是覆盖，
   补完结果与不补完全一致）；反过来漏报一个截断，就是一个五年起步的静默数据洞，
   而且没人会来查。**宁可吵，不可静默。**

## 纯度

判定逻辑全是纯函数（吃 DataFrame / dict，不碰 IO），单测无需联网；
`classify_file` / `audit_directory` 只是薄薄一层读盘壳子。

## 用法

    from aplus.coverage import audit_directory
    rep = audit_directory("data/master/daily", "2019-01-02", "2026-10-11")
    rep["reason_counts"]            # {'missing_columns': 1693, 'head_truncated': 1598, ...}
    rep["primary_reason_counts"]    # 互斥分组，各组之和 == 文件总数
    rep["needs_backfill"]           # 建议补数的代码清单
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# 所有 parquet 都有的基线列。缺这几列属于另一种（更严重的）事故，不是本次要查的。
BASELINE_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume")

# 腾讯备源拿不到、baostock 才有的列——缺列就是降级写入留下的指纹。
REQUIRED_EXTRA_COLUMNS = ("amount", "tradestatus", "isST", "turn", "pctChg")

REQUIRED_COLUMNS = BASELINE_COLUMNS + REQUIRED_EXTRA_COLUMNS

# 缺口原因（互斥分组的优先级：一个文件只归到第一个命中的原因）
REASON_PRIORITY = ("empty", "missing_columns", "tail_nan_cols", "head_truncated",
                   "interior_gap", "tail_stale")
HEALTHY_REASON = "healthy"


@dataclass(frozen=True)
class Thresholds:
    """全部可调阈值集中一处，便于测试与调参。

    数值依据：
    - `max_calendar_gap_days=20`：A 股最长休市是春节，约 14 个自然日（首尾都跨周末），
      留 6 天余量。超过 20 天才算中间断层。
    - `head_grace_days=30` / `tail_grace_days=30`：容忍新股上市首日与登记日之间
      的几个交易日，以及长假的自然日折算。
    - `new_listing_streak=4`：连续涨停根数门槛。取 4 而非 3 是因为豁免错误的代价
      是静默漏洞；实测截断簇里 3 连涨仍有 3% 命中，4 连涨降到 1% 以下。
    - `halt_run=5`：尾端连续停牌/零成交 5 根以上，视为退市/长期停牌的自然停笔。
    """

    head_grace_days: int = 30
    tail_grace_days: int = 30
    max_calendar_gap_days: int = 20
    new_listing_streak: int = 4
    streak_return: float = 0.045
    halt_run: int = 5


THRESHOLDS = Thresholds()


# --------------------------------------------------------------------------- 纯函数


def missing_columns(df) -> list[str]:
    """返回 df 缺少的必需列（保持 REQUIRED_COLUMNS 的固定顺序，便于断言）。"""
    have = set(df.columns)
    return [c for c in REQUIRED_COLUMNS if c not in have]


def _to_date(value) -> date | None:
    """把 '2024-02-19' / '20240219' / Timestamp / date 统一成 date。无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in ("nat", "nan", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def _dates(df) -> list[date]:
    """取出升序去重后的交易日。脏值直接丢弃，不让一条坏日期毁掉整个判定。"""
    if df is None or "date" not in df.columns or len(df) == 0:
        return []
    parsed = pd.to_datetime(df["date"], errors="coerce")
    out = {t.date() for t in pd.Series(parsed.dropna().unique())}
    return sorted(out)


def _limit_up_streak(df, th: Thresholds) -> int:
    """开头连续涨停的根数（新股上市独有形态）。数据不足或 close 异常返回 0。"""
    if df is None or "close" not in df.columns or len(df) < 3:
        return 0
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype="float64")
    window = close[: th.new_listing_streak + 2]
    if len(window) < 2 or not window[: th.new_listing_streak + 1].all():
        return 0
    streak = 0
    for i in range(1, len(window)):
        if window[i - 1] <= 0:
            return streak
        if window[i] / window[i - 1] - 1.0 >= th.streak_return:
            streak += 1
        else:
            break
    return streak


def _trailing_halt(df, th: Thresholds) -> bool:
    """尾部连续 halt_run 根停牌（tradestatus=0）或零成交 -> 像是退市后停笔。"""
    if df is None or len(df) < th.halt_run:
        return False
    tail = df.tail(th.halt_run)
    if "tradestatus" in tail.columns:
        status = pd.to_numeric(tail["tradestatus"], errors="coerce")
        if status.notna().all() and (status == 0).all():
            return True
    if "volume" in tail.columns:
        vol = pd.to_numeric(tail["volume"], errors="coerce")
        if vol.notna().all() and (vol <= 0).all():
            return True
    return False


def tail_nan_columns(df, th: Thresholds = THRESHOLDS) -> dict[str, int]:
    """尾部窗口里「当天有成交、但某列却是空」的列 -> 空值根数。

    ## 为什么要有这条判据

    `missing_columns` 只答「列在不在」。但**列存在不等于列有数据**：
    2026-10-09 那批 bar 是用缺 `turn` 的降级源写的，合并时旧帧没有同一日期
    可填，于是 3342 只股票（61.2%）的 `turn` 列在最后一根上是空的，体检一路
    判健康——这个盲区让缺口静默存在了三周。

    ## 停牌必须排除，否则误报比盲区更吵

    停牌日 `volume=0`，`turn`/`amount`/`pctChg` 为空是**正常**的（当天没成交，
    本就没有换手率可言）。若不排除停牌，长期停牌股会被整片误报。
    所以「有成交」是**前置条件**——而判据只用 `volume > 0` 一条：

    - `volume > 0` 就是真的成交了，`turn` 就该有值；没有值就是缺口；
    - `volume == 0` 就是没成交，空值正常，不报。

    这里**不**再看 `tradestatus`：它与 volume 冲突时（`tradestatus=0` 但
    `volume>0`）信息量更足的是 volume（真金白银的成交额），且此时 turn 为空
    确实是缺口。早先的版本两个条件都查，结果自相矛盾——自己写的「宁可吵」
    原则自己没有遵守。少一个分支，也少一个能把错误变成别的形状的分支。

    ## 只查附加列

    基线列（OHLCV）不在此判据范围：它们整列缺失归 `missing_columns` 管，
    尾部缺值意味着别的更严重的问题，不该在这里重复报。
    """
    if df is None or len(df) == 0:
        return {}
    window = df.tail(th.halt_run)
    vol = pd.to_numeric(window["volume"], errors="coerce") if "volume" in window.columns else None
    if vol is None:
        return {}
    traded = vol.fillna(0.0) > 0
    if not traded.any():
        return {}
    out: dict[str, int] = {}
    for col in REQUIRED_EXTRA_COLUMNS:
        if col not in window.columns:
            continue
        values = pd.to_numeric(window[col], errors="coerce")
        n = int((traded & values.isna()).sum())
        if n:
            out[col] = n
    return out


def coverage_gaps(df, target_start, target_end, *, listing_date=None,
                  delist_date=None, thresholds: Thresholds = THRESHOLDS) -> dict:
    """算一只股票的覆盖缺口。纯函数，不碰 IO。

    参数
    ----
    df            日线帧（只需要 date / close / volume / tradestatus 列）
    target_start  目标窗口起点，'20190101' 或 '2019-01-02' 皆可
    target_end    目标窗口终点
    listing_date  上市登记日（有就精确判新股；没有就走启发式）
    delist_date   退市登记日（有就精确判退市）

    返回
    ----
    dict，含 missing_head / missing_tail / rows / gaps 四个必备键，外加判定依据
    （head_is_gap / tail_is_gap / is_new_listing / is_delisted / missing_cols）。
    `gaps` 是中间断层的 (上一交易日, 下一交易日) ISO 字符串对。

    注意 `missing_head` / `missing_tail` 的单位是**自然日**，不是交易日——
    用来排序和肉眼判断量级够了，真要补数据由脚本按交易日重新拉取。
    """
    th = thresholds
    start, end = _to_date(target_start), _to_date(target_end)
    dates = _dates(df)
    result = {
        "rows": len(dates),
        "first": dates[0].isoformat() if dates else None,
        "last": dates[-1].isoformat() if dates else None,
        "missing_head": 0,
        "missing_tail": 0,
        "gaps": [],
        "head_is_gap": False,
        "tail_is_gap": False,
        "is_new_listing": False,
        "is_delisted": False,
        "missing_cols": missing_columns(df) if df is not None else list(REQUIRED_COLUMNS),
        # 列在、但尾部「有成交却空」的列（如 turn）。与 missing_cols 正交：
        # 一个列都不缺，一个列有值却缺尾。
        "tail_nan_cols": tail_nan_columns(df, th) if df is not None else {},
    }
    if not dates:
        return result

    first, last = dates[0], dates[-1]

    # --- 中间断层 -------------------------------------------------------
    gaps = []
    for prev, nxt in zip(dates, dates[1:]):
        if (nxt - prev).days > th.max_calendar_gap_days:
            gaps.append((prev.isoformat(), nxt.isoformat()))
    result["gaps"] = gaps

    # --- 首行：真新股 vs 历史被截断 ---------------------------------------
    listing = _to_date(listing_date)
    if listing is not None:
        # 权威判据：首行落在上市日（或其后几个交易日内）=> 之前本来就没有数据
        is_new = 0 <= (first - listing).days <= th.head_grace_days
    else:
        head_days = (first - start).days if start else 0
        is_new = head_days <= th.head_grace_days or _limit_up_streak(df, th) >= th.new_listing_streak
    result["is_new_listing"] = is_new
    head_days = (first - start).days if start else 0
    result["head_is_gap"] = not is_new and head_days > th.head_grace_days
    result["missing_head"] = max(0, head_days) if result["head_is_gap"] else 0

    # --- 尾端：退市 vs 停更 -----------------------------------------------
    delisted = _to_date(delist_date)
    if delisted is not None:
        is_del = end is None or delisted < end
    else:
        is_del = end is not None and last < end - timedelta(days=th.tail_grace_days) and _trailing_halt(df, th)
    result["is_delisted"] = is_del
    tail_days = (end - last).days if end else 0
    result["tail_is_gap"] = not is_del and tail_days > th.tail_grace_days
    result["missing_tail"] = max(0, tail_days) if result["tail_is_gap"] else 0
    return result


def classify_frame(code: str, df, target_start, target_end, **kw) -> dict:
    """在 coverage_gaps 之上给出「健不健康」与原因标签。纯函数。"""
    info = coverage_gaps(df, target_start, target_end, **kw)
    reasons = []
    if info["rows"] == 0:
        reasons.append("empty")
    if info["missing_cols"]:
        reasons.append("missing_columns")
    if info["tail_nan_cols"]:
        reasons.append("tail_nan_cols")
    if info["head_is_gap"]:
        reasons.append("head_truncated")
    if info["gaps"]:
        reasons.append("interior_gap")
    if info["tail_is_gap"]:
        reasons.append("tail_stale")

    primary = HEALTHY_REASON
    for reason in REASON_PRIORITY:
        if reason in reasons:
            primary = reason
            break

    return {
        "code": code,
        "rows": info["rows"],
        "first": info["first"],
        "last": info["last"],
        "missing_cols": info["missing_cols"],
        "tail_nan_cols": info["tail_nan_cols"],
        "gaps": info["gaps"],
        "missing_head": info["missing_head"],
        "missing_tail": info["missing_tail"],
        "is_new_listing": info["is_new_listing"],
        "is_delisted": info["is_delisted"],
        "reasons": reasons,
        "primary_reason": primary,
        "is_healthy": not reasons,
    }


# --------------------------------------------------------------------------- IO 薄壳


def classify_file(path, target_start, target_end, *, listing_date=None,
                  delist_date=None, thresholds: Thresholds = THRESHOLDS,
                  code: str | None = None) -> dict:
    """读一个 parquet 并分类。IO 只发生在这一层。"""
    path = Path(path)
    df = pd.read_parquet(path)
    return classify_frame(
        code or path.stem, df, target_start, target_end,
        listing_date=listing_date, delist_date=delist_date, thresholds=thresholds,
    )


def audit_directory(daily_dir, target_start, target_end=None, *,
                    listing_dates: dict | None = None,
                    delist_dates: dict | None = None,
                    thresholds: Thresholds = THRESHOLDS,
                    progress=None) -> dict:
    """体检整个日线目录，返回汇总 + 逐只明细 + 按原因分组的计数。

    `listing_dates` / `delist_dates` 是 {code: 'YYYY-MM-DD'}，由调用方从股票列表
    注入；为 None 时退化为启发式（判定会偏保守，见模块 docstring）。

    返回里两套计数：
    - `reason_counts`      重叠计数，一只可以同时缺列又被截断，各项之和 > 文件数；
    - `primary_reason_counts` 互斥计数，按 REASON_PRIORITY 取第一个命中的原因，
      **各项之和恰好等于文件总数**。
    """
    listing_dates = listing_dates or {}
    delist_dates = delist_dates or {}
    daily_dir = Path(daily_dir)
    if target_end is None:
        target_end = date.today()
    end_iso = _to_date(target_end).isoformat()

    files: dict = {}
    reason_counts: dict = {}
    primary_counts: dict = {}
    needs_backfill: list = []
    n_new_listing = n_delisted = n_healthy = 0

    paths = sorted(daily_dir.glob("*.parquet"))
    for i, path in enumerate(paths, 1):
        code = path.stem
        try:
            info = classify_file(
                path, target_start, end_iso,
                listing_date=listing_dates.get(code),
                delist_date=delist_dates.get(code),
                thresholds=thresholds,
            )
        except Exception as exc:  # noqa: BLE001 — 单只读坏不该毁掉整场体检
            info = {"code": code, "rows": 0, "first": None, "last": None,
                    "missing_cols": list(REQUIRED_COLUMNS), "gaps": [],
                    "missing_head": 0, "missing_tail": 0, "is_new_listing": False,
                    "is_delisted": False, "reasons": ["unreadable"],
                    "primary_reason": "unreadable", "is_healthy": False,
                    "error": f"{type(exc).__name__}: {exc}"}
        files[code] = info
        for reason in info["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        primary_counts[info["primary_reason"]] = primary_counts.get(info["primary_reason"], 0) + 1
        n_new_listing += bool(info["is_new_listing"])
        n_delisted += bool(info["is_delisted"])
        if info["is_healthy"]:
            n_healthy += 1
        else:
            needs_backfill.append(code)
        if progress and i % progress == 0:
            print(f"... 体检 {i}/{len(paths)}｜不健康 {len(needs_backfill)}", flush=True)

    return {
        "daily_dir": str(daily_dir),
        "target_start": _to_date(target_start).isoformat(),
        "target_end": end_iso,
        "n_files": len(paths),
        "n_healthy": n_healthy,
        "n_unhealthy": len(needs_backfill),
        "n_new_listing": n_new_listing,
        "n_delisted": n_delisted,
        "reason_counts": reason_counts,
        "primary_reason_counts": primary_counts,
        "needs_backfill": sorted(needs_backfill),
        "files": files,
    }


__all__ = [
    "BASELINE_COLUMNS", "REQUIRED_EXTRA_COLUMNS", "REQUIRED_COLUMNS",
    "REASON_PRIORITY", "HEALTHY_REASON", "Thresholds", "THRESHOLDS",
    "missing_columns", "coverage_gaps", "classify_frame", "classify_file",
    "audit_directory",
]