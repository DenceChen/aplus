"""基本面因子面板（BM / ROE）—— **本项目第一个独立于价量的信息源**。

## 为什么必须有它

现役 8 个因子（mom20/rev5/vol20/vsur/turn20/ivol/turnratio/rsi）
**全部**由 `master/daily` 的 OHLCV 派生。它们是同一条序列的 8 种重新表述，
不携带价格之外的任何信息。在这样的结构里搜权重，天花板是数学确定的。

石川《因子投资方法与实践》里 BM / ROE 属于独立一族，需要财报——
而 `aplus/financial.py` 的诊断结论是：当前源（akshare 东财）**每报告期只存一行、
无版本维度**，服务的必然是**后修值**，所以 §1.5.1 判定「一律不实现」。

## 那本诊断已经否决了出路 A

`scripts/diag_financial_lookahead.py` 实测：白名单率与**报告年龄**强相关
（2025 年报 97.4%、2024 年 0.4%）。最新一份年报几乎必然「未被修订」
——**因为它还没等到下一次被刷新**。所以 `UPDATE_DATE == NOTICE_DATE`
筛出的不是「可信的财报」而是「最新的财报」，连续两年样本塌缩到 2 个。

**出路 B（每日落盘攒 vintage）要数年才有结果。** 在那之前，
本模块提供的是一个**明确标注的诊断通道**，不是可上生产的因子。

## 本模块做的事，以及刻意不做的事

做：按 `NOTICE_DATE <= as_of` 对齐财报，取最新一份，产出 BM / ROE 两列。

不做：**不声称 PIT 已建立**。对齐用的是诚实的披露时间戳，
贴在一个**事后可能被修订**的数值上——`financial.py` 把这句话写得很清楚。
所以本模块的产出**只能进诊断脚本**，不得进 `weights.json`、
不得进 `run_daily` 的实盘路径。这条边界由测试钉住。

## 数据完整性闸（都不是可选项）

1. `NOTICE_DATE >= REPORT_DATE + 30天` —— 披露日不可能早于报告期末，
   否则是脏记录（实测有 1 条 `NOTICE_DATE = 1900-01-01`）。
2. `REPORT_TYPE == 年报` —— ROE(TTM)/BM/F-Score 都是年报口径，
   混进季报会把 `NOTICE_DATE` 分布整体前移，通过率算出一个偏乐观的假数。
3. **不按 `UPDATE_DATE` 过滤** —— 出路 A 已被实测否决，见文件头。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

#: 披露日至少要晚于报告期末这么多天。低于它的记录是脏数据。
MIN_LAG_DAYS = 30

#: 早于这一天的披露日一律丢弃（实测有 `1900-01-01` 的坏值）
MIN_NOTICE = "1980-01-01"


def load_annual(cache: Path) -> pd.DataFrame:
    """读年报缓存，跑完整性闸，归一到 `code / notice_date / bm_inputs`。"""
    raw = pd.read_parquet(cache)
    a = raw.loc[raw["REPORT_TYPE"].astype(str).str.strip() == "年报"].copy()
    a["code"] = a["SECURITY_CODE"].astype(str).str.zfill(6)
    a["notice_date"] = pd.to_datetime(a["NOTICE_DATE"], errors="coerce")
    a["report_date"] = pd.to_datetime(a["REPORT_DATE"], errors="coerce")

    bad = a["notice_date"].isna() | (a["notice_date"] < MIN_NOTICE)
    # 披露早于报告期末 = 脏记录。30 天是最低申报门槛的宽容量，
    # 真实年报几乎都在 4 月 30 日前披露，留足余量。
    bad |= (a["notice_date"] - a["report_date"]) < pd.Timedelta(days=MIN_LAG_DAYS)
    a = a.loc[~bad].copy()

    a["bm_inputs"] = a["BPS"]
    a["roe"] = pd.to_numeric(a["ROEJQ"], errors="coerce")
    a["report_year"] = a["report_date"].dt.year
    a = a[["code", "report_date", "notice_date", "report_year",
           "bm_inputs", "roe"]]
    # 同一只票同一报告期去重（源可能给多行）
    a = a.sort_values("notice_date").drop_duplicates(
        subset=["code", "report_date"], keep="last")
    return a.reset_index(drop=True)


def attach_to_cross_sections(xs: dict, annual: pd.DataFrame,
                             start: str, end: str) -> dict:
    """把 BM / ROE 两列**就地挂进** `xs[day]` 的横截面。

    每个 `day` 取该股 `NOTICE_DATE <= day` 的**最新**一份年报。

    ## 为什么用 backward asof 而不是 `groupby.max`
    `merge_asof(direction="backward")` 一次给出每个日期的「当时可见的最新一份」，
    而 `max(notice_date <= day)` 要按日期循环，慢一个量级。
    更重要的是它**不会**误取未来披露的记录——那正是财报因子的头号前视偏差。
    """
    days = [d for d in sorted(xs.get("_dates", [])) if start <= d <= end]
    if not days:
        return xs

    codes = sorted(annual["code"].unique())
    # 必须显式叉乘：`grid["code"] = codes` 在 days × codes 上**不广播**，
    # pandas 会报 "Length of values (N) does not match length of index (M)"。
    grid = pd.DataFrame([(d, c) for d in days for c in codes],
                        columns=["day", "code"])

    # `merge_asof` 的键必须是**数值** dtype（pandas 3.x 直接拒绝字符串：
    # "Incompatible merge dtype ... both sides must have numeric dtype"）。
    # 这里用 `YYYYMMDD` 整数，与 `backtest._build_bar_cache` 同一套约定，
    # 不另发明一种键格式——两种并存迟早有人用错一边。
    ann = annual.copy()
    ann["notice_key"] = ann["notice_date"].dt.strftime("%Y%m%d")
    ann = ann.dropna(subset=["notice_key"])
    ann["notice_key"] = ann["notice_key"].astype(np.int64)
    ann = ann.sort_values("notice_key")

    grid = grid.copy()
    grid["day_key"] = grid["day"].str.replace("-", "", regex=False).astype(np.int64)
    grid = grid.sort_values("day_key")

    merged = []
    for code, rep in ann.groupby("code", sort=False):
        rep = rep.sort_values("notice_key")
        g = grid[grid["code"] == code].drop(columns="code").sort_values("day_key")
        # ← 关键：backward 只会取 notice_key <= day_key 的记录，
        #    即**当时尚未披露的财报拿不到**。
        out = pd.merge_asof(
            g, rep[["notice_key", "bm_inputs", "roe", "report_year"]],
            left_on="day_key", right_on="notice_key", direction="backward")
        out["code"] = code
        merged.append(out)

    if not merged:
        return xs
    wide = pd.concat(merged, ignore_index=True)

    # 价格从 `_daily_map` 现取，与横截面里的其它价格口径同源
    px = _closes_on(xs, wide["day"].unique())
    wide = wide.merge(px, on=["day", "code"], how="left")

    # **BM = BPS / 收盘价**。BPS 是每股账面净资产（东财口径，元/股），
    # 与不复权收盘价同量纲 → 比值即「每元市值买到多少元账面净资产」。
    # 不复权是**对的**：除权会同时改价和 BPS 分子，用后复权价会让
    # 一次送股凭空改变 BM（10 送 10 时 BPS 减半、价格减半，比值本应不变，
    # 后复权价却不变 → BM 直接腰斩）。
    with np.errstate(divide="ignore", invalid="ignore"):
        wide["bm"] = wide["bm_inputs"] / wide["close"]
    # BM = log(B/P)，**越大越便宜**。这里刻意**不取负**：
    # 价值因子要的就是「买最便宜的」，取负会让 top-10 变成买最贵的。
    # 方向由 `test_bm_higher_means_cheaper` 钉住——符号翻反过一次，
    # 而翻反后数字照样正常，只是选出了完全相反的一批票。
    with np.errstate(divide="ignore", invalid="ignore"):
        wide["bm"] = np.log(wide["bm"].where(wide["bm"] > 0))
    wide = wide.loc[np.isfinite(wide["bm"]),
                    ["day", "code", "bm", "roe", "report_year"]]

    # `report_year` 一并带出来：**它是这份财报的「账龄」**——
    # 同一个信号日上，一部分股票的账面值已经被改过很多轮，另一部分几乎没被动过。
    # 没有这一列就无法把「宏观风格」和「PIT 污染」分开，见 `bm_ic_attribution`。
    by_day = {d: g.set_index("code")[["bm", "roe", "report_year"]]
              for d, g in wide.groupby("day")}
    for d in days:
        add = by_day.get(d)
        if add is None or add.empty:
            continue
        uni = xs.get(d)
        if uni is None or uni.empty:
            continue
        uni = uni.join(add, on="code")
        xs[d] = uni
    return xs


def _closes_on(xs: dict, days) -> pd.DataFrame:
    """给定若干日期，取每只票那天的**不复权**收盘价。"""
    daily_map = xs.get("_daily_map") or {}
    rows = []
    for code, df in daily_map.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        d = df["date"].astype(str).to_numpy()
        c = pd.to_numeric(df["close"], errors="coerce").to_numpy()
        lo = int(np.searchsorted(d, min(days), side="left"))
        hi = int(np.searchsorted(d, max(days), side="right"))
        if hi <= lo:
            continue
        sub = pd.DataFrame({"day": d[lo:hi], "code": code, "close": c[lo:hi]})
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=["day", "code", "close"])
    out = pd.concat(rows, ignore_index=True)
    return out.loc[out["day"].isin(set(days))]


def coverage(xs: dict, start: str, end: str) -> dict:
    """因子覆盖率——**先看这个再决定要不要跑 IC**。"""
    days = [d for d in sorted(xs.get("_dates", [])) if start <= d <= end]
    n = []
    for d in days:
        uni = xs.get(d)
        if uni is None or uni.empty or "bm" not in uni.columns:
            n.append(0)
            continue
        n.append(int(uni["bm"].notna().sum()))
    s = pd.Series(n, index=days)
    return {
        "n_days": len(days), "days_with_any": int((s > 0).sum()),
        "mean_n": float(s.mean()), "median_n": float(s.median()),
        "min_n": int(s.min()), "max_n": int(s.max()),
        "last_n": int(s.iloc[-1]) if len(s) else 0,
    }