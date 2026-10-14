"""复权计算：不复权 + 因子 → 前复权/后复权。

设计依据（调研报告 4.1）：前复权是动态重算的，基准随窗口右端漂移；
落库只存不复权 + 因子，复权在查询时现算，语义显式化。
"""
import numpy as np
import pandas as pd

PRICE_COLS = ("open", "high", "low", "close")

# 首事件因子超过此值、且首事件晚于日线首根时，**首事件之前那段日线的价格尺度
# 是未知的**，必须作废而不是按 1.0 填。
#
# 为什么 1.0 填不得：001914.SZ（招商积余）日线首根 2019-01-02、首事件
# 2019-06-06 因子 6.4111 → 填 1.0 会在首事件日造出 **+491.25%** 的假收益，
# 而该股复权因子单调、文件完整、所有旧判据都放行。`repair_factors.py`
# 重取后事件数不变（源站只给这些），早期基准无法恢复——**只能作废**。
#
# 阈值取 1.5：只有「尺度差了一个量级以上」才算未知。分红累积通常是百分之几，
# 定得再高就会把大量正常表打成 NaN，用「数据缺失」掩盖一切。
UNKNOWN_SCALE_MIN_FACTOR = 1.5


def attach_factor(daily_df: pd.DataFrame, factor_events: pd.DataFrame) -> pd.DataFrame:
    """把仅在除权日变化的因子事件表前向填充到日线上。

    用 **merge_asof(backward)** 而不是 merge + ffill + fillna(1.0)——后者只认
    「落在日线区间内的」事件：日线自 2019-01-02 起、因子事件自 2008 年起时，
    区间内只匹配得上最近那一件事，于是此前**七年多的因子全被填成 1.0**，
    事件当天跳到 10.146，凭空造出 +839% 的 20 日动量并进入用户当日配置
    （2026-10-11 事故）。merge_asof 会正确带上「区间之前」的最后一个事件。

    上市日之前（早于首个事件）无除权，保留 1.0——语义正确。
    输出保持 daily_df 的原始行序与行数。
    """
    left = daily_df.reset_index(drop=True).copy()
    if factor_events is None or len(factor_events) == 0:
        left["adjust_factor"] = 1.0
        return left
    right = factor_events[["date", "adjust_factor"]].copy()
    left["_row"] = left.index
    # merge_asof 只吃数值键 → 用 YYYYMMDD 数值键做 asof（等价且免去日期类型分支）
    left["_key"] = _date_key(left["date"])
    right["_key"] = _date_key(right["date"])
    left = left.sort_values("_key", kind="mergesort")
    right = right.sort_values("_key", kind="mergesort")
    merged = pd.merge_asof(left, right[["_key", "adjust_factor"]],
                           on="_key", direction="backward")
    factors = pd.to_numeric(merged["adjust_factor"], errors="coerce")
    unknown = None
    first_event, first_factor = _first_event(factor_events)
    if first_event is not None and float(first_factor) > UNKNOWN_SCALE_MIN_FACTOR:
        first_key = _date_key(pd.Series([first_event])).iloc[0]
        unknown = merged["_key"] < first_key
    factors = factors.ffill().fillna(1.0)
    if unknown is not None:
        # 首事件之前没有基准可贴。填 1.0 是**编造**——它会让首事件日凭空多出
        # (factor − 1) 倍的收益（001914.SZ 实测 +491%）。标 NaN：该股在这段
        # 时期退出横截面，等基准已知后再回来。
        #
        # 顺序很重要：必须**先** fillna(1.0) 把真正缺失的值补上、**再** mask，
        # 否则 fillna 会把刚打的 NaN 又填回 1.0，作废形同虚设。
        factors = factors.mask(unknown)
    merged["adjust_factor"] = factors
    return (merged.sort_values("_row", kind="mergesort")
            .drop(columns=["_row", "_key"]).reset_index(drop=True))


def _first_event(factor_events) -> tuple[str | None, float]:
    """最早那条除权事件的 (日期, 因子)。无可用事件返回 (None, 1.0)。"""
    if factor_events is None or len(factor_events) == 0:
        return None, 1.0
    try:
        first = factor_events.sort_values("date", kind="mergesort").iloc[0]
        return str(first["date"])[:10], float(pd.to_numeric(first["adjust_factor"],
                                                             errors="coerce"))
    except (KeyError, IndexError, TypeError, ValueError):
        return None, 1.0


def _date_key(dates: pd.Series) -> pd.Series:
    """日期 → YYYYMMDD 数值键（merge_asof 的 asof key）。

    保持与「字典序 == 时间序」一致，故 asof 的 backward 方向等价于按日期回溯。
    datetime 类型直接转 int64；字符串去掉分隔符。
    """
    if pd.api.types.is_datetime64_any_dtype(dates):
        return dates.astype("int64")
    return pd.to_numeric(dates.astype(str).str.replace("-", "", regex=False),
                         errors="coerce")


def _require_sorted(df: pd.DataFrame) -> None:
    dates = pd.to_datetime(df["date"])
    if not dates.is_monotonic_increasing:
        raise ValueError("date must be sorted ascending before adjusting")


def hfq_close(daily_df: pd.DataFrame, factor_events: pd.DataFrame | None) -> pd.Series:
    """**后复权**收盘价序列（与 daily_df 原始行序、长度一一对齐）。

    ## 用途：成交收益的唯一正确口径

    A 股除权除息当天，不复权收盘价会**直接跳空**（10 送 10 就是腰斩），但股东
    一股没少、还可能多拿了现金。用不复权价算持有收益，等于凭空记下一笔从未发生
    的亏损。2026-10-11 全库实测：2623 只股票存在不复权比值与交易所 `pctChg`
    相差 2% 以上的日对，最极端一根 K 线凭空造出 67 个百分点的亏损。

    后复权序列在除权点**连续**，跨除权的持有收益才是真实收益。

    无事件（或事件为空）时返回原值——此时后复权与不复权恒等，不改变正常样本。
    """
    if daily_df is None or len(daily_df) == 0:
        return pd.Series(dtype=float)
    if factor_events is None or len(factor_events) == 0:
        return daily_df["close"].astype(float)
    merged = attach_factor(daily_df, factor_events)
    # 注意**不得**在这里 `.fillna(1.0)`：`attach_factor` 打的 NaN 表示
    # 「该段复权基准未知」，填 1.0 就等于把编造值又请回来（001914.SZ 的 +491%）。
    factor = pd.to_numeric(merged["adjust_factor"], errors="coerce")
    return (merged["close"].astype(float) * factor).astype(float)


def to_hfq(df: pd.DataFrame, factor_col: str = "adjust_factor", price_cols=PRICE_COLS) -> pd.DataFrame:
    """后复权 = 不复权价 × 当日累计复权因子。"""
    _require_sorted(df)
    out = df.copy()
    for col in price_cols:
        if col in out.columns:
            out[col] = out[col] * out[factor_col]
    return out


def to_qfq(df: pd.DataFrame, factor_col: str = "adjust_factor", price_cols=PRICE_COLS) -> pd.DataFrame:
    """前复权 = 后复权 / 窗口最后一个交易日的因子（动态基准）。"""
    hfq = to_hfq(df, factor_col, price_cols)
    base = df[factor_col].iloc[-1]
    out = hfq.copy()
    for col in price_cols:
        if col in out.columns:
            out[col] = out[col] / base
    return out


# ---------- 因子表健康检查（校验层盲区的补丁） ----------

FACTOR_BASE_TOL = 1e-3  # baostock 在上市日返回的基准因子恒为 1.0


def is_factor_corrupt(factor_df, first_daily_date=None) -> bool:
    """复权因子表是否已损坏/残缺——**每次写入与每日体检都必须过这一关**。

    `first_daily_date` 是该股日线首根日期（可省）。给了它就能区分
    「基准事件因子本就 >1 的借壳股」与「早期事件丢失的残缺表」，
    见 `_base_suspect`。不给则退回旧判据（更保守）。

    背景（2026-10-11 生产事故，两轮）：
    - 第一轮**污染**：因子取错列（adjustFactor 而非 backAdjustFactor），
      偶发垃圾值低一个数量级 → 前向填充后污染两个月 → mom20 +903% 进入用户
      当日配置。5556 只中 615 只受影响。
    - 第二轮**残缺**：只补「损坏」项漏掉了「不完整」项。本地只有 1 条事件、
      源站实有 12 条。单行表完全「单调」，骗过损坏检测。首事件之前
      attach_factor 按 1.0 填充，于是除权日附近凭空造出 10 倍量级假跳空。

    损坏判据（任一命中即判损坏）：
    - 文件缺失 / 缺 adjust_factor 列
    - 有 NaN 或非正值
    - **非单调递增**：后复权因子只增不减，倒退即源站伪事件
    - **首行不是 1.0**：baostock 必在上市日返回一条 back=1.0 的基准事件，
      故完整因子表的首值**通常**为 1.0；首值 > 1 即**可疑**（可能历史事件丢失，
      见第二类损坏）

    已知误报：极少数股票的历史起点本身就是重组/借壳上市日（招商港口 2018-12-26
    借壳、招商积余 2019-06-06、上港集团 2006-10-26），其基准事件当日因子已累积到
    3.53/6.41/9.78。实测 5556 只中 **3 只（0.16%）**属此类——远低于
    `factor_audit` 的 1% 告警阈值，故宁可容忍这点误报，也不放弃这条能抓住
    大面积残缺的关键判据。判据有误报率，好过没有判据。

    「空表」是健康的——无除权事件的股票因子恒为 1.0，attach_factor 填 1.0 正确。

    **刻意不做**量级/跳变检测：实测 1200 只健康因子表的相邻跳变 P99 = 2.03、
    P99.9 = 3.51、最大 242.77——大比例送股本就产生百倍级合法跳变，任何阈值都会
    制造假阳性。一个会误伤正常数据的检测器，比没有检测器更危险。
    """
    if factor_df is None:
        return True                      # 文件缺失：状态未知
    if len(factor_df) == 0:
        return False                     # 无除权事件 → 恒为 1.0，健康
    if "adjust_factor" not in factor_df.columns:
        return True
    f = pd.to_numeric(factor_df["adjust_factor"], errors="coerce")
    if f.isna().any() or (f <= 0).any():
        return True
    if not bool(f.is_monotonic_increasing):
        return True
    return _base_suspect(factor_df, f, first_daily_date)


# 首事件允许比日线首根晚这么多自然日（源站基准事件在**上市日**，不是取数日，
# 两者本就不必对齐；留一点余量避免把无害的几天错位判成损坏）。
FACTOR_BASE_GRACE_DAYS = 10


def _base_suspect(factor_df, factors_numeric, first_daily_date) -> bool:
    """首事件基准是否可疑（早期事件可能已丢失）。

    ## 判据为什么是「日期」而不是「因子值」

    真正要抓的破坏是：`attach_factor` 在首事件**之前**按 1.0 填充，
    于是在第一个除权日造出十倍量级的假跳空（2026-10-11 第二类事故）。
    它成立的前提是 **首事件落在日线历史的起点之后**——那样早期那段才真的
    被填成了 1.0。

    借壳/重组上市的公司（招商港口 2018-12-26、招商积余 2019-06-06、
    上港集团 2006-10-26）首事件因子本来就 >1，但首事件在日线起点**之前**，
    全程被正确前向填充覆盖，根本没有 1.0 填充段——它们是健康的。

    原判据只看「因子值是否等于 1.0」，于是把这 3 只（0.16%）误报成损坏。
    体检只统计不阻断时，这点误报可以忍；但 2026-10-11 体检改成了**闸门**
    （见 tests/test_factor_gate.py），0.06% 的误报会让它**每天都阻断**。
    **一个天天误报的闸门等于没有闸门。**

    ## 拿不到日线首日时

    退回旧的「首行必须为 1.0」严格判据——方向是**保守**（更严）：
    不知道时宁可判损坏，也不放行未经验证的数据。
    """
    if first_daily_date is None:
        return abs(float(factors_numeric.iloc[0]) - 1.0) > FACTOR_BASE_TOL
    try:
        first_event = str(factor_df["date"].iloc[0])[:10]
        base = pd.Timestamp(str(first_daily_date)[:10])
    except (TypeError, ValueError):
        return abs(float(factors_numeric.iloc[0]) - 1.0) > FACTOR_BASE_TOL
    try:
        event_dt = pd.Timestamp(first_event)
    except ValueError:
        return abs(float(factors_numeric.iloc[0]) - 1.0) > FACTOR_BASE_TOL
    if event_dt <= base:
        return False                     # 首事件在日线起点之前/当天 → 无填充段
    return bool((event_dt - base).days > FACTOR_BASE_GRACE_DAYS)


def is_scale_neutralized(factor_df, first_daily_date=None) -> bool:
    """这份「损坏」的因子表是否已被 `attach_factor` 自动作废、中和掉？

    ## 为什么需要区分「损坏」与「已中和的损坏」

    `attach_factor` 对「首事件因子 > 1.5 且晚于日线首根」的情形会把首事件之前
    的因子标成 NaN（见 `UNKNOWN_SCALE_MIN_FACTOR`）。这类表**确实不完整**，
    但它不会再编造收益——那段时期的价格直接不可用，该股自然退出横截面。

    实测 `001914.SZ`：旧口径在 2019-06-06 造出 +491% 假收益；作废后那一段
    是 NaN，因子全部为 NaN，不会进榜。

    所以因子闸门（见 scripts/run_daily.py `factor_gate_blocks`）应当只对
    **中和不了**的损坏生效。为 2/5447（0.04%）已经被数据层处理干净的股票
    拦住整个平台，只会让闸门天天误报，最终被绕过、被忽略。

    ## 判据

    首事件因子 > `UNKNOWN_SCALE_MIN_FACTOR` 且首事件晚于日线首根 —— 与
    `attach_factor` 的作废条件**完全一致**。改那边必须同步改这里，
    否则闸门会对着一堆已经无害的表空转。
    """
    if factor_df is None or len(factor_df) == 0:
        return False
    if first_daily_date is None or "adjust_factor" not in factor_df.columns:
        return False
    first_event, first_factor = _first_event(factor_df)
    if first_event is None or float(first_factor) <= UNKNOWN_SCALE_MIN_FACTOR:
        return False
    try:
        return pd.Timestamp(first_event) > pd.Timestamp(str(first_daily_date)[:10])
    except (TypeError, ValueError):
        return False


def classify_factor_files(data_root) -> tuple[list, list]:
    """把因子表分成「有日线（会被用到）」与「孤儿（无日线，用不到）」。

    ## 为什么要分

    实测（2026-10-11）：因子表 5556 只，日线只有 5447 只，多出的 **109 只
    （1.96%）** 是孤儿——因子文件在，但该股没有日线行情。

    它们**从不参与横截面**（没有日线就 join 不进来），坏不坏都不影响任何预测。
    但旧审计把它们算进损坏率，而 `repair_factors.py` 只遍历日线代码，
    于是它们**永远修不了**，却一直把审计比例顶高、反复打 error。

    把「没在用」和「数据坏了」混为一谈，会让人为一个不影响结果的问题
    反复重跑全量修复（1835 秒），真正的问题反而被淹没。

    返回 (usable_codes, orphan_codes)，均按代码排序。
    """
    from pathlib import Path

    root = Path(data_root) / "master"
    factor_dir = root / "factor"
    if not factor_dir.exists():
        return [], []
    factor_codes = {p.stem for p in factor_dir.glob("*.parquet")}
    daily_dir = root / "daily"
    daily_codes = ({p.stem for p in daily_dir.glob("*.parquet")}
                   if daily_dir.exists() else set())
    orphans = factor_codes - daily_codes
    return sorted(factor_codes & daily_codes), sorted(orphans)
