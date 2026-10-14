"""逐因子信息系数（IC）诊断。

## 为什么要有这个模块

赛马量的是**组合层面的超额**：某组权重排出的 top-10 相对市场赚了多少。
它有两个致命的低效：

1. **信噪比极差**。84 组候选里挑一组，本质是在噪声里排序；
   单组结果同时被「权重选错」「频率选错」「运气」三件事污染。
2. **归因为零**。超额为负时无法回答「是哪个因子在反向拖累」，
   于是每轮迭代都变成再猜一组权重——而猜的就是刚才被证伪的那批。

IC 直接量的是**因子值与前瞻收益的相关性**，不经过组合构造、
不扣成本、不受调仓频率影响。它把「因子有没有预测力」和
「权重搭得对不对」两个问题拆开，而后者才是赛马该回答的。

## 三条不可协商的口径（与 `backtest._next_bars` 逐位对齐）

1. **因子只用 D 日收盘前可得的数据** —— 直接取 `build_cross_sections`
   产出的横截面，与策略看到的是同一份数值，不另算一遍。
2. **前瞻收益 = D+1 收盘买入 → D+2 收盘卖出**（`ENTRY_LAG = 1`）。
   `FWD_WINDOWS["lag1_real"]` 就是这个窗口，与回测同一口径。
3. **只在可成交的股票上算 IC** —— 停牌 / 价格不可信 / 超出
   `MAX_PLAUSIBLE_RETURN` 的样本剔除，理由与 `_next_bars` 逐条相同。

`tests/test_factor_ic.py::test_forward_returns_matches_next_bars` 把
本模块的快路径与 `backtest._next_bars` 钉成逐位相等 —— 口径一旦分叉，
IC 就在量另一件事，而数字看上去照样正常。

## 泄露探针：`lag0_leaky` 是故意保留的坏窗口

`lag0_leaky` 量的正是 `entry_lag=0` 那条前视路径：D 收盘出信号，
却拿 D 收盘当买价 —— 收益窗口起点落在信息集之内。
平台早已把默认口径改成 `ENTRY_LAG=1`，但**改成多少、值多少，必须有数**。
保留这个坏窗口不是为了用，是为了量化「曾经虚高多少」。

## 尾部才是策略真正交易的地方

策略在 ~4900 只里只买 top-10（0.2%）。横截面整体 IC 再高，
也可能整个集中在中段、尾部为零 —— 那样它对选股毫无用处。
`ic_table` 因此同时给出**尾部分段 IC**：只在最高 20% 和最低 20%
内部算相关性。若 `all` 显著而 `tail_high`/`tail_low` 归零，
结论是「这个因子解释了收益，但不解释极端收益」。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from aplus.adjust import hfq_close
from aplus.backtest import (MAX_PLAUSIBLE_RETURN, _bars_tradable,
                            _next_bars, _raw_bars)
# 边车后缀用**单一真相源**，不在这里另抄一遍字面量 ——
# 抄一遍就是 `docs/MISTAKE_LOG` 第 73 层那个「复制到多处的事实 = 下次漂移的种子」。
from aplus.provenance import SIDECAR_SUFFIX

# 前瞻收益窗口：(相对「D 之后第一根」的进场偏移, 出场偏移)
# lag0_leaky = (-1, 0) → D 收盘买、D+1 收盘卖（**前视，仅供对照**）
# lag1_real  = ( 0, 1) → D+1 收盘买、D+2 收盘卖（平台实际口径）
FWD_WINDOWS: dict[str, tuple[int, int]] = {
    "lag0_leaky": (-1, 0),
    "lag1_real": (0, 1),
    "hold4": (0, 4),
    "hold21": (0, 21),
}

# 尾部分段：策略在 ~4900 只里只买 top-10，最高位/最低位内部的相关性
# 才是它真正依赖的量。全截面 IC 高而尾部分段为 0 是常见情形。
TAIL_Q = 0.20
MIN_PAIRS = 20          # 少于这个数不给 IC —— 相关性在 n 小时是纯噪声
MIN_OBS = 30            # 少于这么多交易日不给 t —— 同理


def window_block(window: str) -> int:
    """该窗口的**独立观测单位**（交易日数）。

    持有 H 天时，相邻 H 天的前瞻收益窗口重叠 H−1 天，
    逐日 IC 高度自相关 —— 观测单位必须是持有期，不是交易日。
    用 1 会让 t 虚高 H 倍左右，正好撞上显著性闸，且不会报任何错。
    """
    enter, exit_off = FWD_WINDOWS[window]
    return max(1, exit_off - enter)


def build_price_cache(daily_map: dict, factors_map: dict | None = None) -> dict:
    """每股 → `(dates, hfq_closes, raw_bars)`，一次建好反复取用。

    复权口径与 `_next_bars` 完全一致：有因子表走 `hfq_close`，
    没有就降级为不复权并**如实标记**（`adjusted=False`），
    不假装所有样本都复权过。
    """
    factors_map = factors_map or {}
    cache: dict = {}
    for code, df in daily_map.items():
        if df is None or len(df) == 0:
            continue
        s = df.sort_values("date", kind="stable")
        events = factors_map.get(code)
        has_adj = events is not None and len(events) > 0
        closes = (hfq_close(s, events).to_numpy(float) if has_adj
                  else s["close"].to_numpy(float))
        cache[code] = (s["date"].to_numpy(dtype="U10"), closes,
                       _raw_bars(s), has_adj)
    return cache


def forward_returns(cache: dict, codes, after_date: str,
                    enter_off: int, exit_off: int,
                    max_abs_return: float = MAX_PLAUSIBLE_RETURN) -> pd.Series:
    """`after_date` 之后第 `enter_off` 根收盘买入、第 `exit_off` 根收盘卖出的收益。

    偏移相对「`after_date` 之后的第一根」，即 `enter_off=0` 就是 D+1。
    不可成交的样本（停牌 / 非正价格 / 收益超界）**直接剔除而非填 0** ——
    填 0 会把它们变成「预测正确且赚 0」，凭空稀释 IC。
    """
    out: dict[str, float] = {}
    for code in codes:
        hit = cache.get(code)
        if hit is None:
            continue
        dates, closes, raw, _has_adj = hit
        pos = int(np.searchsorted(dates, after_date, side="right"))
        entry, exit_i = pos + enter_off, pos + exit_off
        if entry < 0 or exit_i >= len(dates) or entry >= exit_i:
            continue
        if not _bars_tradable(raw, entry, exit_i):
            continue
        entry_px, exit_px = float(closes[entry]), float(closes[exit_i])
        if entry_px <= 0 or exit_px <= 0:
            continue
        ret = exit_px / entry_px - 1.0
        if abs(ret) > max_abs_return:
            continue
        out[code] = ret
    return pd.Series(out, dtype=float)


def _pcorr(a: np.ndarray, b: np.ndarray) -> float | None:
    """两组数各自的 Pearson。分母为 0 → None（无秩信息），不返回 0。

    中心化在**传入的那一组之内**做：尾部分段是子样本，
    拿全样本的均值去中心化算出来的不是子样本的相关性。
    """
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom <= 0:
        return None
    return float((a * b).sum() / denom)


def _rankdata(a: np.ndarray) -> np.ndarray:
    """平均秩（并列取均值），返回 float64 排名。**全向量化**。

    并列组用「值发生变化」的分界 + `cumsum` 定位，不走 Python 循环：
    本模块每天要对 ~4900 只 × 8 因子 × 4 窗口做排序，
    循环版会在这个量级上变成主要耗时（实测慢两个数量级）。
    """
    n = len(a)
    if n == 0:
        return np.empty(0, dtype=float)
    order = np.argsort(a, kind="stable")
    sorted_a = a[order]
    is_start = np.empty(n, dtype=bool)
    is_start[0] = True
    np.not_equal(sorted_a[1:], sorted_a[:-1], out=is_start[1:])
    group_id = np.cumsum(is_start) - 1
    counts = np.bincount(group_id)
    sums = np.zeros(len(counts), dtype=float)
    np.add.at(sums, group_id, np.arange(n, dtype=float))
    ranks = np.empty(n, dtype=float)
    ranks[order] = (sums / counts)[group_id]
    return ranks


class RankedForward:
    """某日某窗口的前瞻收益秩（中心化 + 归一），算 N 个因子只排一次序。

    收益序列的秩与因子无关，每窗口排一次就够；
    为每个因子重排一遍会让排序开销乘以因子数（实测 8 倍）。
    """

    __slots__ = ("codes", "r")

    def __init__(self, codes: list, r: np.ndarray):
        self.codes = codes
        self.r = r                       # 原始秩，未中心化


def make_ranked_forward(u_idx: pd.DataFrame, fwd: pd.Series
                        ) -> RankedForward | None:
    """把前瞻收益与横截面对齐并排秩。样本不足 / 全并列 → None。"""
    j = fwd.reindex(u_idx.index).dropna()
    if len(j) < MIN_PAIRS:
        return None
    r = _rankdata(j.to_numpy(float))
    if float(np.std(r)) <= 0:
        return None                      # 收益全并列 → 无相关可言
    return RankedForward(list(j.index), r)


def daily_ic_ranked(u_idx: pd.DataFrame, rf: RankedForward | None,
                    factor: str, tail_q: float = TAIL_Q
                    ) -> dict[str, float | None]:
    """用已排好秩的收益序列算分层 IC —— 批量扫描走这条路径。"""
    blank = {"all": None, "tail_high": None, "tail_low": None, "n": 0}
    if rf is None:
        return blank
    f = u_idx.reindex(rf.codes)[factor].to_numpy(float)
    ok = np.isfinite(f)
    if int(ok.sum()) < MIN_PAIRS:
        return {**blank, "n": int(ok.sum())}
    fr, rr = _rankdata(f[ok]), rf.r[ok]
    n = len(fr)
    k = min(n, max(MIN_PAIRS, int(n * tail_q)))
    order = np.argsort(fr, kind="stable")
    return {
        "all": _pcorr(fr, rr),
        "tail_high": _pcorr(fr[order[n - k:]], rr[order[n - k:]]),
        "tail_low": _pcorr(fr[order[:k]], rr[order[:k]]),
        "n": n,
    }


def rank_corr(x: np.ndarray, y: np.ndarray, min_pairs: int = MIN_PAIRS
              ) -> float | None:
    """Spearman 秩相关 = 秩上的 Pearson。

    秩用 `argsort` 双遍处理并列：因子值在横截面上并列极多
    （缩尾后的分位、`turn20` 这种整数值），直接 `np.corrcoef` 会把
    并列当顺序，IC 被系统性污染。

    任一侧 NaN 的对**成对剔除**，不是各自分别 dropna ——
    后者会让两个序列长度不同还照样算相关。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.shape != y.shape:
        raise ValueError(f"长度不一致：{x.shape} vs {y.shape}")
    ok = np.isfinite(x) & np.isfinite(y)
    if int(ok.sum()) < min_pairs:
        return None
    return _pcorr(_rankdata(x[ok]), _rankdata(y[ok]))


def daily_ic(universe: pd.DataFrame, fwd: pd.Series, factor: str,
             tail_q: float = TAIL_Q) -> dict[str, float | None]:
    """单个交易日的分层 IC（单因子便捷入口，内部走 `daily_ic_ranked`）。

    返回三个口径：
    - `all`       整个可成交横截面
    - `tail_high` 因子最高 `tail_q` 那部分内部的 IC
    - `tail_low`  因子最低 `tail_q` 那部分内部的 IC
    """
    u_idx = universe.set_index("code")
    rf = make_ranked_forward(u_idx, fwd)
    got = daily_ic_ranked(u_idx, rf, factor, tail_q=tail_q)
    if got["n"] == 0 and rf is None:
        got["n"] = int(fwd.reindex(u_idx.index).notna().sum())
    return got


def ic_summary(rows: list[dict], factor: str, window: str) -> dict:
    """把逐日 IC 汇总成一行：均值、块 t、正 IC 占比。"""
    sel = [r for r in rows if r.get("factor") == factor
           and r.get("window") == window and r.get("all") is not None]
    if not sel:
        return {"factor": factor, "window": window, "n_days": 0}
    def _stat(field):
        v = pd.Series([r[field] for r in sel if r.get(field) is not None], dtype=float)
        return v
    out = {"factor": factor, "window": window, "n_days": len(sel)}
    for field in ("all", "tail_high", "tail_low"):
        v = _stat(field)
        if len(v) < MIN_OBS:
            out[f"{field}_mean"] = None
            out[f"{field}_t"] = None
            out[f"{field}_hit"] = None
            continue
        out[f"{field}_mean"] = round(float(v.mean()), 5)
        sd = float(v.std(ddof=1))
        out[f"{field}_t"] = (round(float(v.mean() / (sd / np.sqrt(len(v)))), 3)
                             if sd > 0 else None)
        out[f"{field}_hit"] = round(float((v > 0).mean()), 4)
    out["avg_n"] = round(float(np.mean([r["n"] for r in sel])), 1)
    return out


def _find_ic_report(reports_dir) -> "Path | None":
    """最新的 `factor_ic*.json`。找不到返回 None——**不回落**到别的文件。

    ⚠️ 必须**先排掉 provenance 边车**（`SIDECAR_SUFFIX`）。
    边车的文件名是 `主报告名 + ".provenance.json"`，也就是主文件名的
    **超长前缀**，字典序必然排在主文件后面 ⇒ `sorted(...)[-1]` 返回的是边车。

    真报告有 `kind/window/factors/windows/fwd_windows/tail_q/bonferroni_crit`，
    边车只有 `code_sha/code_dirty/generator/stamped_at` 四个 key ⇒
    下游取因子时 KeyError 被 `except` 吞掉 ⇒ 静默退化成 `unknown=True`。

    **真实后果**：概览页「符号已按实测 IC 校正」这句话当时没有数据支撑，
    而守它的 5 条测试全部 skip（`consistency()` 一见 unknown 就 skip）——
    skip 不是通过。由 `tests/test_report_sidecar_selection.py` 钉住。
    """
    try:
        files = sorted(p for p in Path(reports_dir).glob("factor_ic*.json")
                       if not p.name.endswith(SIDECAR_SUFFIX))
    except (TypeError, OSError):
        return None
    return files[-1] if files else None


def weight_sign_conflicts(reports_dir, weights: dict) -> dict:
    """现任权重的符号，有没有和实测 IC 的方向相反。

    返回形如::

        {"unknown": False, "conflicts": ["mom20", "vol20"], "agrees": ["rev5"],
         "unmeasurable": ["turn20"], "ic_window": [...], "note": "..."}

    ## 证据缺失时**必须沉默**

    没有 IC 报告 / 报告坏掉 / 没有一个因子的 IC 够样本量，
    一律返回 `unknown=True` 且 `conflicts=None`。

    返回 `conflicts=[]` 会被读成「查过了，一处冲突都没有」——
    而实际是「根本没查」。判决板和每日名单都靠这个字段决定
    要不要报警，报成「无冲突」比不报更坏。
    """
    scope = sorted(f for f, w in (weights or {}).items() if abs(float(w or 0)) > 0)
    blank = {"unknown": True, "conflicts": None, "agrees": None,
             "unmeasurable": None, "checked": [], "ic_window": None,
             "ic_values": {}, "report": None,
             "note": "没有可用的因子 IC 报告，本次未做符号核对（不等于「无冲突」）"}

    if not scope:
        return {**blank, "note": "现任权重为空，无法核对符号（不等于「无冲突」）"}

    path = _find_ic_report(reports_dir)
    if path is None:
        return blank
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["table"]
    except (OSError, ValueError, KeyError, TypeError):
        return {**blank, "report": path.name}

    ic: dict[str, float] = {}
    weak: set[str] = set()
    for row in rows:
        f = row.get("factor")
        if f not in scope or row.get("window") != "lag1_real":
            continue
        val = row.get("all")
        n_days = row.get("n_days") or 0
        if val is None or n_days < MIN_OBS or float(val) == 0.0:
            weak.add(f)
            continue
        ic[f] = float(val)

    conflicts, agrees, unmeasurable = [], [], []
    for f in scope:
        if f in ic:
            (conflicts if (float(weights[f]) > 0) != (ic[f] > 0)
             else agrees).append(f)
        else:
            unmeasurable.append(f)

    if not ic:
        # 结论必须沉默，但**查过哪些**要留下——否则排查时无从下手，
        # 而「一个都测不出来」和「符号都对」在报告里会长得一模一样。
        return {**blank, "checked": scope, "unmeasurable": unmeasurable,
                "report": path.name,
                "note": ("IC 报告里没有够样本量的真实窗口行（查过 "
                         + "、".join(unmeasurable) + "），本次未做符号核对")}

    parts = []
    if conflicts:
        parts.append(f"{len(conflicts)} 个因子的权重方向与实测 IC 相反："
                     + "、".join(f"{f}(IC {ic[f]:+.4f}, 权重 {float(weights[f]):+.2f})"
                                 for f in conflicts))
    if agrees:
        parts.append(f"{len(agrees)} 个方向一致：" + "、".join(agrees))
    if unmeasurable:
        parts.append(f"{len(unmeasurable)} 个因子没有够样本量的 IC，未核对："
                     + "、".join(unmeasurable))
    parts.append("（方向相反≠反过来就能赚；要反过来必须重跑赛马并过全部闸门）")
    return {"unknown": False, "conflicts": conflicts, "agrees": agrees,
            "unmeasurable": unmeasurable, "checked": scope,
            "ic_window": payload.get("window"),
            "ic_values": {k: round(v, 5) for k, v in ic.items()},
            "report": path.name, "note": "；".join(parts)}


def yearly_sign_consistency(reports_dir) -> dict:
    """逐年 IC 符号与全段 IC 的一致率——**从报告算，不许手写**。

    返回形如::

        {"unknown": False, "agree": 63, "total": 63, "n_factors": 8,
         "n_years": 8, "flips": [], "per_factor": {...}, "note": "..."}

    ## 为什么这个函数存在

    「逐年符号 N/N 一致」这句话是 `DEFAULT_WEIGHTS` 符号翻转的**全部说服力**，
    而且它被直接印在概览页给用户看（`evolve.load_weights` 的 caveat）。
    它此前是**手写的**，写成「8 因子 × 8 年逐年符号 32/32 一致」——
    三处错：32/32 算术上不自洽（8×8 应为 64）、`turnratio` 实际只有 7 年、
    真实数字是 **63/63**。信号本身没错（63/63 比 32/32 更强），但报出来的
    是编的。

    手写的证据数字必然与数据漂移。所以这里让它可算，散文反查它。

    ## 缺记录的年份计入分母，不被抹掉

    `turnratio` 缺 2019（需要累积成交量历史，年初无前值；其 `n_days=1630`
    比其余因子的 1821 少 191 天，正好对上）。那是**事实**，必须留在分母里被
    看见，而不是从表里删掉好凑一个整数。`missing` 字段把缺口显式报出。

    ## 未知时必须沉默

    与 `weight_sign_conflicts` 同一纪律：没有报告 / 报告坏掉 / 一个都没测到，
    一律 `unknown=True` 且 `agree=total=None`。返回 `0/0` 会被读成
    「查过了，零反号」——而实际是「根本没查」。
    """
    blank = {"unknown": True, "agree": None, "total": None,
             "n_factors": None, "n_years": None, "flips": None,
             "per_factor": None, "report": None, "note": ""}

    path = _find_ic_report(reports_dir)
    if path is None:
        return {**blank,
                "note": "没有可用的因子 IC 报告，本次未做逐年符号核对"
                        "（不等于「逐年一致」）"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        yearly = payload["yearly"]
        factors = list(payload["factors"])
    except (OSError, ValueError, KeyError, TypeError):
        return {**blank, "report": path.name,
                "note": "因子 IC 报告结构异常，未做逐年符号核对"
                        "（不等于「逐年一致」）"}

    # 全段 IC 只认 `lag1_real`（非泄漏窗口）——`lag0_leaky` 是诊断口径，
    # 用它定符号就是拿泄漏结果当证据。
    full: dict[str, float] = {}
    for row in payload.get("table", []):
        if row.get("window") == "lag1_real" and row.get("factor") in factors:
            val = row.get("all")
            if val is not None and float(val) != 0.0:
                full[row["factor"]] = float(val)

    years = sorted({r["year"] for r in yearly})
    if not years or not factors:
        return {**blank, "report": path.name,
                "note": "IC 报告里没有逐年记录，未做核对（不等于「逐年一致」）"}

    per_factor: dict[str, dict] = {}
    flips, agree, total = [], 0, 0
    for f in factors:
        by_year = {r["year"]: r.get("ic_mean") for r in yearly
                   if r.get("factor") == f and r.get("ic_mean") is not None}
        have = [y for y in years if y in by_year]
        ref = full.get(f)
        bad = [y for y in have
               if ref is not None and (by_year[y] > 0) != (ref > 0)]
        agree += len(have) - len(bad)
        total += len(have)
        flips.extend({"factor": f, "year": y} for y in bad)
        per_factor[f] = {"full_ic": ref, "n_years": len(have),
                         "missing": [y for y in years if y not in by_year],
                         "flips": bad,
                         "signs": "".join("+" if by_year[y] > 0 else "-"
                                          for y in have)}

    if total == 0:
        return {**blank, "report": path.name,
                "note": "逐年记录为空，未做核对（不等于「逐年一致」）"}

    gaps = {f: d["missing"] for f, d in per_factor.items() if d["missing"]}
    parts = [f"{agree}/{total} 个（因子×年）观测与该因子的全段 IC 同号"]
    if gaps:
        parts.append("缺记录的年份已计入分母：" + "、".join(
            f"{f} 缺 {','.join(y)}" for f, y in gaps.items()))
    return {"unknown": False, "agree": agree, "total": total,
            "n_factors": len(factors), "n_years": len(years),
            "flips": flips, "per_factor": per_factor,
            "report": path.name, "note": "；".join(parts)}


__all__ = ["FWD_WINDOWS", "TAIL_Q", "MIN_PAIRS", "MIN_OBS", "build_price_cache",
           "forward_returns", "rank_corr", "daily_ic", "daily_ic_ranked",
           "make_ranked_forward", "RankedForward", "ic_summary", "_rankdata",
           "window_block", "weight_sign_conflicts", "yearly_sign_consistency"]