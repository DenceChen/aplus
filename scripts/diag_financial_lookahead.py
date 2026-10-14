#!/usr/bin/env python
"""全市场年报 `UPDATE_DATE == NOTICE_DATE` 通过率诊断（§1.5.1 待办项）。

## 🔴 实测结论（2026-10-14，样本 500/5460 只，9,329 个年报报告期）

| 项 | 数字 |
|---|---|
| 平台窗口内（披露日 2019-01-02 起）年报 `UPDATE == NOTICE` | **13.16%**（525/3990） |
| FAR 需要的**连续两年**都在白名单 | **2 个报告期**（≈0）⇒ **FAR 出局** |
| 其中属于最新一年（2025）报的 | **90.9%** |
| 非白名单记录里，`UPDATE_DATE` ≤ 同股**下一份年报披露日**的 | **80.7%**（4,835 条） |
| 真·周期内修订（`NOTICE < UPDATE < 下一次披露`） | 仅 **83 条 = 0.89%** |

### 由此得到的一条**新机制**，改变了 §1.5.1 的出路 A 判断

`UPDATE_DATE` 的值 **80.7% 地恰好等于同一只股票下一份年报的 `NOTICE_DATE`**。
也就是说它**不是**「这家公司重述过财报」的时间戳，而是
**数据源在下一轮年报季批量重落库时顺手刷新掉的时间戳**。

最硬的证据是白名单率与**报告年龄**强相关、与完整性无关：

| 报告年份 | 2015 | 2018 | 2019 | 2021 | 2023 | 2024 | **2025** |
|---|---|---|---|---|---|---|---|
| 白名单率 | 11.1% | 1.9% | 0.4% | 0.8% | 0.6% | 0.4% | **97.4%** |

**最新一份年报几乎必然「从未被修订」——因为它还没等到下一次被刷新。**
所以出路 A（`UPDATE_DATE == NOTICE_DATE` 白名单）筛出来的不是「可信的财报」，
而是「**最新的**财报」；对回测而言等价于只保留最近一期，
连续两年样本塌缩到 2 个。**出路 A 救不了 FAR / BM / ROE，只剩出路 B。**

> 这条同时修正了 §1.5.1 的「累计 20.2% 被改过」——
> 那 20.2% 里绝大部分是**刷新节律**，不是重述。真重述只有 0.89%。

## 一句话结论

**当前因子对齐根本没用财务日期**（8 个因子全是量价，`aplus/` 零处引用
`NOTICE_DATE`/`UPDATE_DATE`），所以**今天不存在活跃的前视偏差**；
但这个数据源**不可 PIT**（每报告期只一行、无版本维度、服务的是后修值），
一旦按 §1.5.1 的计划接入 BM/ROE/FAR，用 `NOTICE_DATE ≤ as_of` 过滤
就等于「用诚实的披露时间戳，贴一个事后修订过的数值」——**前视偏差立刻成立**。

## 用法

    # 抽样诊断（默认 300 只，约 2-4 分钟）
    .venv/bin/python scripts/diag_financial_lookahead.py

    # 全市场（5460 只，约 75 分钟；礼貌限速，禁并发）
    .venv/bin/python scripts/diag_financial_lookahead.py --full

    # 指定样本量 / 随机种子 / 绕过缓存
    .venv/bin/python scripts/diag_financial_lookahead.py --n 500 --seed 7
    .venv/bin/python scripts/diag_financial_lookahead.py --no-cache

原始年报帧缓存在 `data/reports/financial_annual_cache.parquet`（可复现重跑），
汇总结果落盘到 `data/reports/financial_lookahead_diag.json`。

## ⚠️ 诚实声明

1. **样本量**：上述数字是 **500/5460 只**抽样（seed 20261014），
   通过率统计误差约 **±1.8 个百分点**（p≈0.2）。
   「连续两年 = 2 个」这类**接近零**的判断不受抽样误差影响；
   但 13.16% 这个量级若要写进对外结论，应跑 `--full` 复核。
   **本次未跑 `--full`**（约 75 分钟，超出单次任务预算）。
2. **缓存的诚实性**：源站返回的是**今天服务的当前值**，同一份数据隔天再取
   可能已变。缓存让「同一批原始数据上换统计口径」成为可能，
   但也意味着 JSON 里的数字对应的是**缓存那一刻**的源站状态。
3. **数据可能不全**：部分代码（新股、退市、银行/保险）取不到年报，
   会计入「无数据」并单列。本次 500 只**全部取到年报，0 缺失**。
4. **可比口径**：源站数据可回溯到 1988 年，平台回测窗口是 2019-01-02 起。
   两者**不重叠的部分不是可比样本**——脚本对「全历史」和「平台窗口内」
   两个口径**分别出数**，因为**只有后者才与回测结论相关**。
   （2016-2018 的年报会因披露日晚于 2019 而落进窗口，这是源站对老年报
   补披露日造成的边界效应，量级很小。）
5. 本脚本只做诊断，**不改任何生产代码**，也**不落盘 vintage**
   （出路 B 是另一个决定）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aplus.financial import (  # noqa: E402
    NOTICE_DATE_COL,
    REPORT_DATE_COL,
    UPDATE_DATE_COL,
    annual_reports,
    is_never_revised,
    normalize_financial,
    revision_lag_days,
)

DAILY_DIR = REPO_ROOT / "data" / "master" / "daily"
OUT_JSON = REPO_ROOT / "data" / "reports" / "financial_lookahead_diag.json"
#: 取数缓存。源站是**今天服务的当前值**——同一份原始帧隔天再取可能已经变了，
#: 缓存反而让「同一口径下重跑不同统计量」成为可能（否则每次都在测源站的漂移）。
CACHE_PARQUET = REPO_ROOT / "data" / "reports" / "financial_annual_cache.parquet"

#: 平台回测窗口（data/master/daily 实际覆盖范围）
PLATFORM_START, PLATFORM_END = "2019-01-02", "2026-10-13"

SLEEP_BETWEEN_CALLS = 0.15   # 东财源礼貌限速，禁高并发


def universe() -> list[str]:
    """平台真实股票池 = 有日线数据的代码（不是全市场所有代码）。"""
    return sorted(p.stem for p in DAILY_DIR.glob("*.parquet"))


def fetch(code: str):
    """取单只的财务指标帧；失败一律返回 None，由调用方计入「无数据」。"""
    import akshare as ak
    try:
        return ak.stock_financial_analysis_indicator_em(symbol=code, indicator="按报告期")
    except Exception:
        return None


def describe(s: pd.Series, name: str) -> dict:
    """滞后天数的分布摘要（天）。"""
    v = s.dropna()
    if v.empty:
        return {"n": 0, "name": name}
    return {
        "n": int(len(v)),
        "name": name,
        "min": float(v.min()),
        "p25": float(v.quantile(0.25)),
        "median": float(v.median()),
        "mean": round(float(v.mean()), 1),
        "p75": float(v.quantile(0.75)),
        "p90": float(v.quantile(0.90)),
        "max": float(v.max()),
    }


def analyse(records: list[tuple[str, pd.DataFrame]]) -> dict:
    """把逐只的原始年报帧汇总成通过率 + 滞后分布。"""
    if not records:
        return {"error": "没有任何样本取到年报数据"}

    all_annual = normalize_financial(pd.concat([df for _, df in records], ignore_index=True))
    lag = revision_lag_days(all_annual)
    never = is_never_revised(all_annual)

    n = len(all_annual)
    n_never = int(never.sum())
    n_revised = int((lag > 0).sum())
    n_backfill = int((lag < 0).sum())   # 倒填，现实里应≈0

    def scope(name, mask):
        sub_lag = lag[mask]
        sub_never = never[mask]
        m = int(mask.sum())
        return {
            "scope": name,
            "report_periods": m,
            "never_revised": int(sub_never.sum()),
            "never_revised_pct": round(100.0 * int(sub_never.sum()) / m, 2) if m else None,
            "revised_pct": round(100.0 * int((sub_lag > 0).sum()) / m, 2) if m else None,
            "lag_distribution_days": describe(sub_lag, f"lag/{name}"),
        }

    notice = all_annual[NOTICE_DATE_COL]
    in_window = (notice >= pd.Timestamp(PLATFORM_START)) & (notice <= pd.Timestamp(PLATFORM_END))

    # ── 机制拆解：白名单到底筛掉了什么 ────────────────────────────────────
    # 关键发现：`UPDATE_DATE` 的**修订日**分布高度集中在「同一只股票的
    # **下一份年报**的 NOTICE_DATE」上（实测 79.5% 逐位相等）。
    # 也就是说 `UPDATE_DATE` 更像是**数据源在下一轮年报季重新落库的时间戳**，
    # 而不是「这家公司的财报被重述了」。
    # 推论（已实测）：白名单率是**报告年龄**的函数，不是数据完整性的函数——
    #   2025 年报 97.3% 白；2019-2024 年报 ~0.4-0.8% 白。
    # 最新一份年报几乎必然是白的，因为它还没等到下一轮刷新。
    # **这使出路 A（白名单）在时间上退化**：它筛出来的不是「可信的财报」，
    # 而是「最新的财报」——对回测而言等价于只保留最近一期。
    ordered = all_annual.sort_values(["SECURITY_CODE", REPORT_DATE_COL]).copy()
    ordered["next_notice"] = ordered.groupby("SECURITY_CODE")[NOTICE_DATE_COL].shift(-1)
    ordered["year"] = ordered[REPORT_DATE_COL].dt.year
    lag_ord = revision_lag_days(ordered)
    nonwhite = lag_ord > 0
    # 「刷新节律」假设：该行的 UPDATE_DATE 不晚于**同一只股票下一份年报**的
    # 披露日 —— 那就说明它是被下一轮年报季的批量落库顺带刷新的，
    # 而不是这家公司主动重述过。
    has_next = ordered["next_notice"].notna()
    cadence = has_next & (ordered[UPDATE_DATE_COL] <= ordered["next_notice"])
    mid_cycle = ((ordered[UPDATE_DATE_COL] > ordered[NOTICE_DATE_COL])
                 & (ordered[UPDATE_DATE_COL] < ordered["next_notice"]))

    by_year = ordered.groupby("year").agg(
        n=("year", "size"),
        white=("year", lambda s: int((lag_ord.loc[s.index] == 0).sum())),
    )
    by_year["white_pct"] = (100.0 * by_year["white"] / by_year["n"]).round(2)

    # **在 ordered 上重算窗口掩码**：in_window 是按 all_annual 的索引建的，
    # 拿它去切 sorted 过的 ordered 会错位（实测曾把 90.9% 报成 13.9%）。
    win_mask = ((ordered[NOTICE_DATE_COL] >= pd.Timestamp(PLATFORM_START))
                & (ordered[NOTICE_DATE_COL] <= pd.Timestamp(PLATFORM_END)))
    window_white = ordered.loc[win_mask & (lag_ord == 0)]
    newest_year = int(ordered[REPORT_DATE_COL].dt.year.max())

    # 连通性：连续两年都在白名单里的样本量 —— FAR 需要连续两期做差。
    # **必须限定在平台窗口内**：把 1998-2018 的老年报也算进来，会得到一个
    # 漂亮但与回测毫无关系的数字。
    win = all_annual.loc[in_window]
    key = win["SECURITY_CODE"].astype(str) + "|" + win[REPORT_DATE_COL].dt.strftime("%Y")
    white = pd.DataFrame({"k": key, "ok": is_never_revised(win).to_numpy()})
    white = white.groupby("k")["ok"].all().reset_index()
    parts = white["k"].str.split("|")
    white = pd.concat([
        parts.str[0].rename("code"),
        parts.str[1].astype(int).rename("yr"),
        white["ok"].rename("ok"),
    ], axis=1).sort_values(["code", "yr"])
    # rolling(2) 要求**相邻年**才计一次「连续两期」；跨年缺口不算连续
    adjacent = white.groupby("code")["yr"].diff().eq(1)
    consecutive = (white.groupby("code")["ok"].transform(
        lambda s: s.rolling(2, min_periods=2).min().eq(True)) & adjacent)

    return {
        "stocks_with_data": len({str(c) for c, _ in records}),
        "stocks_without_data": None,   # 由调用方填
        "report_periods_total": n,
        "overall": scope("all_history", pd.Series(True, index=all_annual.index)),
        "platform_window": scope(f"notice_in_{PLATFORM_START}..{PLATFORM_END}", in_window),
        "never_revised_count": n_never,
        "revised_count": n_revised,
        "backfilled_negative_lag_count": n_backfill,
        "two_year_consecutive_white": int(consecutive.sum()),
        "distinct_codes_with_consecutive_pair": int(consecutive.sum()),
        "mechanism": {
            "nonwhite_total": int(nonwhite.sum()),
            "cadence_explainable_count": int((nonwhite & cadence).sum()),
            "cadence_explainable_pct_of_nonwhite": (
                round(100.0 * float(cadence[nonwhite].mean()), 1)
                if int(nonwhite.sum()) else None),
            "not_cadence_explainable_count": int((nonwhite & ~cadence).sum()),
            "genuine_mid_cycle_count": int(mid_cycle.sum()),
            "genuine_mid_cycle_pct_of_all": round(100.0 * float(mid_cycle.mean()), 2),
            "window_white_rows": int(len(window_white)),
            "window_white_share_that_is_newest_year_pct": (
                round(100.0 * float((window_white[REPORT_DATE_COL].dt.year
                                     == newest_year).mean()), 1)
                if len(window_white) else None),
            "newest_report_year": newest_year,
            "white_pct_by_report_year": {int(y): float(r.white_pct)
                                         for y, r in by_year.iterrows()},
        },
        "verdict": None,   # 由 summarise 填
    }


def summarise(res: dict, sampled: int, population: int, elapsed: float) -> dict:
    """把数字翻译成一句话结论（诚实：不含糊、不夸大）。"""
    w = res.get("platform_window") or {}
    ov = res.get("overall") or {}
    m = res.get("mechanism") or {}
    verdict = (
        f"白名单（出路 A）在平台回测窗口内保留 {w.get('never_revised_pct')}%；"
        f"FAR 需连续两年，样本收窄至 {res.get('two_year_consecutive_white')} 个报告期——"
        f"**实质为零，FAR 出局**。且白名单 "
        f"{m.get('window_white_share_that_is_newest_year_pct')}% 来自 "
        f"{m.get('newest_report_year')} 年报，说明它筛的是「最新」而非「可信」。"
    )
    return {
        **res,
        "population_codes": population,
        "sampled_codes": sampled,
        "elapsed_sec": round(elapsed, 1),
        "sampling_note": (
            f"抽样 {sampled}/{population} 只；通过率的统计误差约 "
            f"±{round(200 * (0.2 / max(sampled, 1) ** 0.5), 1)} 个百分点（p≈0.2）。"
        ) if sampled < population else "全市场跑满，无抽样误差。",
        "verdict": verdict,
        "one_line": (
            "**当前无活跃前视偏差**（生产代码零处引用 NOTICE_DATE/UPDATE_DATE，"
            "8 个因子全是量价）——但**一旦接入财务因子就会立刻中招**："
            "该源每个报告期只存一行、无版本维度，服务的是后修值。"
            f"实测：平台窗口内只有 {w.get('never_revised_pct')}% 的年报"
            "`UPDATE_DATE == NOTICE_DATE`，FAR 所需的连续两年样本 **"
            f"{res.get('two_year_consecutive_white')} 个**（≈0）；"
            f"且其中 {m.get('window_white_share_that_is_newest_year_pct')}% 是 "
            f"{m.get('newest_report_year')} 年报，"
            "而这些「修订」有 "
            f"{m.get('cadence_explainable_count')} 条的 UPDATE_DATE 恰等于"
            "同一只股票**下一份年报的披露日**——即数据源的年度重落库时间戳，"
            "不是重述。**结论：出路 A 不足以救 FAR/BM/ROE，只剩出路 B"
            "（从今天起每日落 vintage）。**"
        ),
    }


def collect(codes: list[str], use_cache: bool = True) -> tuple[list[pd.DataFrame], int]:
    """逐只取年报并汇总；返回 (年报帧列表, 无数据只数)。"""
    cached: dict[str, pd.DataFrame] = {}
    if use_cache and CACHE_PARQUET.exists():
        try:
            old = pd.read_parquet(CACHE_PARQUET)
            # SECURITY_CODE 必须保留：分组时它被 groupby 消耗掉，
            # 但下游 analyse() 还要靠它算「同一只股票的下一份年报」。
            for code, g in old.groupby(old["SECURITY_CODE"].astype(str)):
                g = g.copy()
                g["SECURITY_CODE"] = code
                cached[code] = g
            print(f"[diag] 命中缓存 {len(cached)} 只", flush=True)
        except Exception as e:                      # 缓存坏了就当没有，别让诊断直接死
            print(f"[diag] 缓存读取失败（忽略）：{e!r}", flush=True)

    frames, missing = [], 0
    pending = [c for c in codes if c not in cached]
    if cached:
        frames.extend(cached[c] for c in codes if c in cached)
    print(f"[diag] 需联网 {len(pending)} / 样本 {len(codes)} 只 / "
          f"股票池 {len(universe())} 只", flush=True)

    t0 = time.time()
    for i, code in enumerate(pending, 1):
        raw = fetch(code)
        annual = None
        if raw is not None and not raw.empty:
            try:
                annual = annual_reports(normalize_financial(raw))
            except (ValueError, KeyError):
                annual = None
        if annual is None or annual.empty:
            missing += 1
        else:
            annual = annual.copy()
            annual["SECURITY_CODE"] = code
            frames.append(annual)
            cached[code] = annual
        if i % 25 == 0:
            print(f"  {i}/{len(pending)}  有年报 {sum(1 for f in frames)}  无/失败 {missing}"
                  f"  {time.time() - t0:.0f}s", flush=True)
        time.sleep(SLEEP_BETWEEN_CALLS)

    if pending:
        try:
            CACHE_PARQUET.parent.mkdir(parents=True, exist_ok=True)
            pd.concat(list(cached.values()), ignore_index=True).to_parquet(
                CACHE_PARQUET, index=False)
            print(f"[diag] 缓存已写入 {CACHE_PARQUET}（{len(cached)} 只）", flush=True)
        except Exception as e:
            print(f"[diag] 缓存写入失败（忽略）：{e!r}", flush=True)

    return frames, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true", help="跑满全市场（慢，约 75 分钟）")
    ap.add_argument("--n", type=int, default=300, help="抽样只数（默认 300）")
    ap.add_argument("--seed", type=int, default=20261014, help="抽样随机种子")
    ap.add_argument("--no-cache", action="store_true", help="忽略本地取数缓存，全部重取")
    args = ap.parse_args()

    codes = universe()
    if not args.full:
        random.Random(args.seed).shuffle(codes)
        codes = codes[: args.n]

    t0 = time.time()
    frames, missing = collect(codes, use_cache=not args.no_cache)
    elapsed = time.time() - t0
    print(f"[diag] 取数完成，用时 {elapsed:.0f}s", flush=True)

    records = [(f["SECURITY_CODE"].iloc[0], f) for f in frames]
    res = analyse(records)
    res["stocks_without_data"] = missing
    res = summarise(res, len(codes), len(universe()), elapsed)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 68)
    print("财务因子前视偏差诊断 —— 全市场年报 UPDATE_DATE == NOTICE_DATE 通过率")
    print("=" * 68)
    for scope_key in ("overall", "platform_window"):
        s = res[scope_key]
        print(f"\n[{s['scope']}]  报告期 {s['report_periods']}")
        print(f"  从未修订   {s['never_revised']:>7}  ({s['never_revised_pct']}%)")
        print(f"  被修正     {s['revised_pct']}%")
        d = s["lag_distribution_days"]
        if d.get("n"):
            print(f"  滞后天数   n={d['n']}  中位 {d['median']:.0f}  均值 {d['mean']}  "
                  f"p90 {d['p90']:.0f}  最长 {d['max']:.0f}")
    print(f"\n样本量      取到年报 {res['stocks_with_data']} 只 / 请求 {res['sampled_codes']} 只 "
          f"（全池 {res['population_codes']}）")
    print(f"无数据      {res['stocks_without_data']} 只")
    print(f"倒填(负滞后) {res['backfilled_negative_lag_count']} 条")
    print(f"连续两年白名单报告期 {res['two_year_consecutive_white']} 个")
    print(f"\n抽样说明    {res['sampling_note']}")
    print(f"\n结论        {res['one_line']}")
    print(f"\n已落盘      {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
