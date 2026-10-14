"""股本数据源：总股本 / 流通股本 / A 股股本（时间序列）。

## 为什么单独一层

平台现有 8 个因子全是量价（`snapshot.FACTOR_COLS`）。基本面因子的第一块砖是股本——
`docs/factor_spec_cn.md` §Q4：规模因子 = 收盘价 × 总股本、BM = 权益 / 总市值、
ROE(TTM) 与 F-Score 也都拿市值当分母。没有股本，这一整串都算不出来。

## 源是怎么选的（实测，不是推测）

akshare 1.19.1，环境可联网。候选只有两个，结论如下：

- **`stock_share_change_cninfo`（巨潮）**：返回**时间序列**，`变动日期 / 总股本 /
  已流通股份 / 人民币普通股 / 流通受限股份 / 变动原因`。**单位是万股**。
  实测与 `stock_zh_a_gbjg_em`（单位为股）同一只票比值恰好 `10000.0`，
  两者互为单位的交叉校验。选定此源。
- **`stock_zh_a_gbjg_em`（东财）**：字段更干净，但是**截面快照**（只有最近几期），
  且更新更慢（603392 上最新期 2025-12-17，而巨潮已到 2026-06-30）。作为备源。
- **baostock `query_stock_basic` 不是备源**：本环境 baostock 0.9.4 的该接口
  `fields` 实测为 `code/code_name/ipoDate/outDate/type/status`，**没有**
  `totalShare`/`liqaShare`。任务简报里的假设不成立，如实记录。

## 三条不能破的规矩

1. **缺失就是缺失**。股本字段缺失/非正一律转 NaN，**不填 0、不前向填充**。
   填 0 会让下游算出「总市值 = 0」并静默退出排名；前向填充会把今年的股本变动
   写进去年的行，那是未来函数。
2. **时点必须可见**。股本是会变的（增发、回购、解禁），所以返回的**每一行**都带
   该股本的生效日 `date`，`latest_snapshot()` 另外给出 `as_of`。拿不到时点的股本
   绝不能被下游当常量用。
3. **单位在边界归一**。源是「万股」，本模块对外一律「股」。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .convert import _infer_exchange

# 对外统一 schema。`total_shares` / `float_shares` / `a_shares` 一律单位为**股**。
SHARE_COLUMNS = ["date", "code", "total_shares", "float_shares", "a_shares", "reason"]

#: 巨潮的股本列单位为**万股**，归一为股要乘这个系数（实测 = 10000.0）
WAN = 10_000.0

_EXCHANGES = ("SH", "SZ", "BJ")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")


# ── 代码归一 ────────────────────────────────────────────────────────────────

def normalize_code(code: str) -> str:
    """三种写法收敛到统一 code：`000001.SZ` / `sz.000001` / 裸 `000001`。

    `aplus/sources/convert.py` 里只有单向的 `to_akshare_code`，没有反向归一；
    股本源（巨潮用裸 6 位）与主库（`000001.SZ`）都要汇到同一处，故在此补齐。

    非法输入**报错而不是猜**——猜出来的「看起来对」的代码会静默污染主库。
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"非法股票代码：{code!r}")
    raw = code.strip()
    symbol = exchange = None

    if "." in raw:
        left, _, right = raw.partition(".")
        if right.upper() in _EXCHANGES:          # 000001.SZ
            symbol, exchange = left, right.upper()
        elif left.upper() in _EXCHANGES:        # sz.000001
            symbol, exchange = right, left.upper()
    elif len(raw) == 8 and raw[:2].upper() in _EXCHANGES:   # sz000001
        symbol, exchange = raw[2:], raw[:2].upper()
    else:                                      # 裸 6 位
        symbol = raw

    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        raise ValueError(f"非法股票代码：{code!r}")
    return f"{symbol}.{exchange or _infer_exchange(symbol)}"


def _cninfo_symbol(code: str) -> str:
    """巨潮接口只吃裸 6 位代码。"""
    return normalize_code(code).split(".")[0]


# ── 单位与缺失值归一 ─────────────────────────────────────────────────────────

def _to_shares(series) -> pd.Series:
    """巨潮的万股 → 股；缺失/非正一律 NaN。

    **不做前向填充**：股本会变，填充等于把后来的事实倒灌进历史行。
    """
    values = pd.to_numeric(series, errors="coerce") * WAN
    return values.where(values > 0)


def _norm_date(value) -> str | None:
    """日期归一为 `YYYY-MM-DD`；认不出（或日期不存在）就返回 None，不编一个日期。"""
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    if _DATE_RE.match(text):
        fmt = "%Y-%m-%d"
    elif _COMPACT_DATE_RE.match(text):
        fmt = "%Y%m%d"
    else:
        return None
    # 交给 pandas 兜底校验「形状对但不存在」的日期（2026-13-45 / 20261345）
    parsed = pd.to_datetime(text, format=fmt, errors="coerce")
    return None if parsed is None or parsed is pd.NaT else parsed.strftime("%Y-%m-%d")


def _empty_shares() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") if c not in ("date", "code", "reason")
                         else pd.Series(dtype="object") for c in SHARE_COLUMNS})


def cninfo_frame_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """巨潮 `stock_share_change_cninfo` 原始帧 → `SHARE_COLUMNS`。

    保留**全部**股本变动期次（时间序列），不折叠成当前值——股本会变。
    """
    if df is None or len(df) == 0 or "总股本" not in getattr(df, "columns", []):
        return _empty_shares()

    out = pd.DataFrame({
        "date": df["变动日期"].map(_norm_date) if "变动日期" in df.columns else None,
        "code": [normalize_code(f"{s}") for s in df["证券代码"].astype(str)] if "证券代码" in df.columns else None,
        "total_shares": _to_shares(df["总股本"]),
        "float_shares": _to_shares(df["已流通股份"]) if "已流通股份" in df.columns else np.nan,
        "a_shares": _to_shares(df["人民币普通股"]) if "人民币普通股" in df.columns else np.nan,
        "reason": df["变动原因"].astype(str) if "变动原因" in df.columns else "",
    })
    for col in ("total_shares", "float_shares", "a_shares"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # 日期认不出的行直接丢掉：时点不明的股本行等于没有时点
    out = out[out["date"].notna()].dropna(subset=["code"])
    return out[SHARE_COLUMNS].sort_values(["code", "date"]).reset_index(drop=True)


# ── 抓取 ────────────────────────────────────────────────────────────────────

#: 巨潮返回 0 条时 akshare 会硬取这些列（空帧里根本没有），KeyError 的 key 只可能是它们
_CNINFO_EMPTY_KEYS = frozenset({"公告日期", "变动日期", "证券代码"})


def _cninfo_call(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """薄封装一层，便于测试打桩（测试全程不打网络）。"""
    import akshare as ak
    try:
        return ak.stock_share_change_cninfo(symbol=symbol, start_date=start_date, end_date=end_date)
    except KeyError as exc:
        # 巨潮对没有股本变动史的票返回 `records=0`（实测 000004/000005/000018），
        # 而 akshare 无条件执行 `temp_df["公告日期"]` → 空帧上抛 KeyError。
        # 这是**源无数据**，不是抓取失败：如实归成空帧，别把它算成错误。
        # 只认这三个 key：别的 KeyError 一律照抛，不猜。
        if exc.args and str(exc.args[0]) in _CNINFO_EMPTY_KEYS:
            return pd.DataFrame()
        raise


def fetch_share_history(code: str, start: str = "1990-01-01", end: str = "2099-12-31") -> pd.DataFrame:
    """拉一只票的股本变动**历史**（不是当前值）。

    返回的每一行都带 `date`（该期股本的生效日），因此下游可以按时点回溯，
    也可以用 `latest_snapshot()` 取 `as_of`。源返回空时给空帧，不编造。
    """
    symbol = _cninfo_symbol(code)
    raw = _cninfo_call(symbol, start.replace("-", ""), end.replace("-", ""))
    return cninfo_frame_to_shares(raw)


# ── 时点 ────────────────────────────────────────────────────────────────────

def has_usable_as_of(value) -> bool:
    """`as_of` 是否是一个**真实存在的**日期字符串。

    空串、None、非字符串（`20260630` 这种 int）、`not-a-date`、`2026-13-45` 一律 False。
    下游拿它当闸门，所以宁可严——放过一个坏时点就等于放行一次未来函数。
    """
    if not isinstance(value, str):
        return False
    return _norm_date(value) is not None


def shares_as_of(history: pd.DataFrame) -> str | None:
    """帧内最后一条股本变动的生效日。空帧/无合法时点 → None。"""
    if history is None or history.empty or "date" not in history.columns:
        return None
    dates = [d for d in history["date"].map(_norm_date) if d]
    return max(dates) if dates else None


def latest_snapshot(history: pd.DataFrame) -> dict | None:
    """当前股本快照，**必带 `as_of`**。

    没有 `as_of` 的股本快照对下游是危险的：它长得像一个常量事实，
    实际只反映某个时点。空帧返回 None，不返回一堆 NaN 装作有数据。
    """
    as_of = shares_as_of(history)
    if as_of is None:
        return None
    row = history[history["date"].map(_norm_date) == as_of].iloc[-1]
    return {
        "code": row["code"],
        "as_of": as_of,
        "total_shares": float(row["total_shares"]) if pd.notna(row["total_shares"]) else float("nan"),
        "float_shares": float(row["float_shares"]) if pd.notna(row["float_shares"]) else float("nan"),
        "a_shares": float(row["a_shares"]) if pd.notna(row["a_shares"]) else float("nan"),
        "reason": row.get("reason", ""),
    }


def point_in_time_shares(history: pd.DataFrame, date: str) -> dict:
    """取 `date` 当日**或之前**最近一期的股本（backward as-of）。

    股本会变，所以「用最新值回填历史」是未来函数。查不到就是 NaN，
    不用第一期倒填。
    """
    blank = {"code": None, "as_of": None, "total_shares": float("nan"),
             "float_shares": float("nan"), "a_shares": float("nan"), "reason": ""}
    if history is None or history.empty:
        return blank
    target = _norm_date(date)
    if target is None:
        return blank
    hist = history.assign(_d=history["date"].map(_norm_date))
    hist = hist[hist["_d"].notna() & (hist["_d"] <= target)]
    if hist.empty:
        return blank
    row = hist.sort_values("_d").iloc[-1]
    return {
        "code": row["code"],
        "as_of": row["_d"],
        "total_shares": float(row["total_shares"]) if pd.notna(row["total_shares"]) else float("nan"),
        "float_shares": float(row["float_shares"]) if pd.notna(row["float_shares"]) else float("nan"),
        "a_shares": float(row["a_shares"]) if pd.notna(row["a_shares"]) else float("nan"),
        "reason": row.get("reason", ""),
    }


# ── Q2：`turn` 的分母是总股本还是流通股本 ────────────────────────────────────

def implied_shares_from_turn(volume, turn) -> np.ndarray:
    """由 `turn` 反推分母：`turn = volume / shares × 100` → `shares = volume × 100 / turn`。

    `turn` 是**百分数**。停牌日 turn=0 时不得造出 inf——返回 NaN。
    """
    vol = pd.to_numeric(pd.Series(volume, dtype="float64"), errors="coerce").to_numpy()
    rate = pd.to_numeric(pd.Series(turn, dtype="float64"), errors="coerce").to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        implied = vol * 100.0 / rate
    implied = np.where(np.isfinite(implied) & (implied > 0), implied, np.nan)
    return implied


_LEGS = ("total_shares", "float_shares", "a_shares")

#: 判定结论用短名（下游要写进日志/报告，列名太长）
_LEG_VERDICT = {"total_shares": "total", "float_shares": "float", "a_shares": "a_shares"}


def classify_turn_denominator(daily: pd.DataFrame, history: pd.DataFrame, *, tol: float = 0.005) -> dict:
    """判定 `turn` 的分母落在哪条腿上（Q2）。

    做法：对每个交易日反推 `shares = volume × 100 / turn`，再按时点 backward join 股本，
    算 `implied / leg` 的**中位数**。落在 1±tol 内的腿即为分母。

    三条腿都可能：带 H 股的公司（中国银行/中国石化）分母既不是总股本，
    也不是含 H 股的「已流通股份」，而是 A 股（人民币普通股）。

    ## `tol` 为什么取 0.005 而不是 2%

    `turn` 在源站只有 4 位小数（实测样本 `0.5127` / `1.000086`），量化到
    ±0.00005 个百分点；相对误差 = 0.00005 / turn，换手率 ≥0.5% 时约 0.01%，
    极低换手（0.05%）时约 0.1%。再取多日中位数会更稳。0.5% 有两个量级的余量。

    反过来，容差**不能太松**：海康威视的总股本只比流通股本多 1.31%，
    容差 2% 时两条腿会同时命中，判定直接退化成「分不出来」。

    腿之间分不出来时返回 `ambiguous`，没数据或反推不出来时返回 `unknown`——
    **宁可说分不出来，也不硬报一个结论**。
    """
    out = {"verdict": "unknown", "n": 0, "ratio_to_total": float("nan"),
           "ratio_to_float": float("nan"), "ratio_to_a": float("nan")}
    if (daily is None or daily.empty or history is None or history.empty
            or not {"volume", "turn", "date"} <= set(daily.columns)):
        return out

    work = daily.copy()
    work["_implied"] = implied_shares_from_turn(work["volume"], work["turn"])
    work["_d"] = work["date"].map(_norm_date)
    work = work[work["_d"].notna() & work["_implied"].notna()]
    if work.empty:
        return out

    hist = history.assign(_d=history["date"].map(_norm_date))
    hist = hist[hist["_d"].notna()].sort_values("_d")
    # backward as-of：取「当日或之前」最近一期。用 searchsorted 而非 merge_asof——
    # pandas 3.0 的 merge_asof 不接受字符串键，而这里的日期是 ISO 字符串。
    anchors = hist["_d"].tolist()
    idx = np.searchsorted(anchors, work["_d"].tolist(), side="right") - 1
    matched = work.assign(_i=idx)
    matched = matched[matched["_i"] >= 0]
    if matched.empty:
        return out
    legs = hist.iloc[matched["_i"].to_numpy()][list(_LEGS)].reset_index(drop=True)
    legs.index = matched.index
    matched = pd.concat([matched.drop(columns=["_i"]), legs], axis=1)
    matched = matched.dropna(subset=["total_shares"])
    if matched.empty:
        return out

    ratios = {}
    for leg in _LEGS:
        col = matched[leg]
        ratios[leg] = float(np.nanmedian(matched["_implied"] / col)) if col.notna().any() else float("nan")

    hits = [leg for leg in _LEGS if ratios[leg] == ratios[leg] and abs(ratios[leg] - 1.0) <= tol]
    detail = dict(n=int(len(matched)), ratio_to_total=ratios["total_shares"],
                  ratio_to_float=ratios["float_shares"], ratio_to_a=ratios["a_shares"])
    if not hits:
        out.update(detail)
        return out

    # 多条腿同时命中 = 这只票压根没有受限股，三条腿数值相同，分不出口径
    verdict = _LEG_VERDICT[hits[0]] if len(hits) == 1 else "ambiguous"
    out.update(verdict=verdict, **detail)
    return out