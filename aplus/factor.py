"""因子引擎：从日线数据计算横截面因子（纯函数）。

收益类因子一律用后复权价（不复权 close × 复权因子）计算——跨分红除权不失真，
这是 PLATFORM.md §2 的硬性要求（调研报告 4.1：不复权 + 因子是唯一正确口径）。
"""
import numpy as np
import pandas as pd

FACTOR_NAMES = ("mom20", "rev5", "vol20", "vsur", "turn20",
                 "turnratio", "ivol", "rsi")
# 权重 v1（回测调参的起点）：动量主导。等权版在趋势股上 mom20 与 rev5 互相抵消
# （涨股 5 日动量为正 → rev5 为负），排名退化为噪声——由回测合成数据用例暴露。
# turn20 不进默认权重：新因子只经周日赛马晋级，不走行政指定。
DEFAULT_WEIGHTS = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}


def momentum(close: pd.Series, window: int = 20) -> float:
    """窗口动量：末价/窗口前价 - 1。"""
    return float(close.iloc[-1] / close.iloc[-window - 1] - 1)


def turnover_mean(turn: pd.Series, window: int = 20) -> float:
    """窗口平均换手率（%）。低换手效应：高换手 → 投机过热 → 未来收益差（负权重候选）。"""
    return float(turn.tail(window).mean())


def reversal(close: pd.Series, window: int = 5) -> float:
    """短窗反转：取负的短动量（超跌反弹逻辑）。"""
    return -momentum(close, window)


def volatility(close: pd.Series, window: int = 20) -> float:
    """窗口日收益率总体标准差。"""
    pct = close.pct_change().dropna().tail(window)
    return float(pct.std(ddof=0))


def volume_surge(volume: pd.Series, window: int = 20) -> float:
    """量能突变：最新成交量 / 之前 window 期均量。脏数据（NaN/0 均量）返回 NaN。"""
    prior_mean = float(volume.iloc[-window - 1:-1].mean())
    if prior_mean != prior_mean or prior_mean == 0:  # NaN 或 0
        return float("nan")
    return float(volume.iloc[-1] / prior_mean)


# 方差低于此绝对阈值即视为「无信息列」：真实因子的横截面标准差量级约 1e-2，
# 1e-12 以下只可能是常数列。判据来自实测，不是拍脑袋。
_DEGENERATE_STD = 1e-12


def cross_section_z(scores: pd.Series) -> pd.Series:
    """横截面 z-score（总体标准差）。

    **方差近似零时必须整列归零**：只判 `std == 0` 是不够的——合成/停牌等
    退化场景下 std 会是 1e-16 量级，除下去得到的 z 值可达 1e16，直接把
    composite_score 彻底带偏（实测：全等差价格序列下 vol20 的 z 值把 score
    改了 2%）。与 `factor bug` 同类的地雷，宁可让整列不参与排名。
    """
    std = scores.std(ddof=0)
    if not np.isfinite(std) or std <= _DEGENERATE_STD:
        return scores * 0.0
    return (scores - scores.mean()) / std


def regime_weights(cross_section: pd.DataFrame, bull_weights: dict, bear_weights: dict,
                   threshold: float = -0.02) -> dict:
    """市场状态自适应：横截面中位 20 日动量 > threshold 用牛市权重，否则用调整市权重。

    依据 6 个月回测的 regime 发现：动量权重在上行结构（4-7 月）有效、
    调整市（8-9 月）失效——调整市应偏向反转与低波动。
    """
    market_mom = float(cross_section["mom20"].median())
    return bull_weights if market_mom > threshold else bear_weights


# 与 aplus/panel.py 同款口径；两处必须一致，否则回测与实盘又分裂。
TURN_RATIO_SHORT = 21
TURN_RATIO_LONG = 252
IVOL_WINDOW = 21
# ±10% 之外的收益不存在于 A 股（涨跌停上限）。此处只做**保护性**截断，
# 与 panel.RESUME_CLAMP 同思路；真实口径见 panel 的 MAX_PLAUSIBLE_RETURN。
_IVOL_CLAMP = 0.10


def _align_market(mkt, daily: pd.DataFrame) -> pd.Series:
    """把按日期索引的市场序列对齐到 `daily` 的**行位置**。

    不能 `reindex(daily.index)`：daily 的索引是 parquet 的原始行号（0..n-1 或
    任意子集），mkt 按日期字符串索引，按标签 reindex 会 100% 落空
    （实测 300 行全 NaN）。必须按日期**取值**对齐。
    """
    if mkt is None or len(mkt) == 0:
        return pd.Series(np.nan, index=daily.index, dtype=float)
    mk = pd.Series(np.asarray(mkt, dtype=float),
                   index=pd.Index(pd.Series(mkt.index).astype(str)))
    return pd.Series(mk.reindex(daily["date"].astype(str).to_numpy()).to_numpy(),
                     index=daily.index, dtype=float)


def ivol_series(r: pd.Series, m: pd.Series, window: int = IVOL_WINDOW) -> pd.Series:
    """IVOL：过去 `window` 日个股收益对市场收益回归后的**残差波动率**（书 5.3 节）。

    ## 为什么必须是**这一个**函数

    回测走 `aplus/panel.py` 的向量化横截面，实盘走 `aplus/factor.py: compute_factors`
    的标量路径。两者各自写一份时，窗口、ddof、裁剪阈值只要有一处不同，
    **同一个 `{"ivol": -1.0}` 权重在回测和实盘里就不是同一个因子**。

    实测过一次：两边算出的 ivol 相差约 4%（0.01910 vs 0.01843），
    由 `tests/test_backtest.py::test_fast_path_equals_original_slow_path` 抓到。
    4% 的偏移足以改变横截面排序。**抽成单一实现是唯一可靠的修法。**

    口径（与书中一致，保留原文注释的要点）：
      · `cov` 按 **ddof=0** 展开（`rolling().cov()` 默认 ddof=1，混用会破坏恒等式）
      · 同一窗口内做 OLS，残差方差 = `var(r) − cov²/var(m)`
        （不用「滚动 beta + 再滚动残差」——那等于把窗口滑到 2×window）
    """
    window = int(window)
    need = max(int(window * 0.7) // 1, 2) if window > 1 else 2
    need = min(need, window)
    var_r = r.rolling(window, min_periods=need).var(ddof=0)
    var_m = m.rolling(window, min_periods=need).var(ddof=0)
    mean_rm = (r * m).rolling(window, min_periods=need).mean()
    mean_r = r.rolling(window, min_periods=need).mean()
    mean_m = m.rolling(window, min_periods=need).mean()
    cov_rm = mean_rm - mean_r * mean_m
    with np.errstate(divide="ignore", invalid="ignore"):
        resid_var = np.where(var_m > 0, cov_rm ** 2 / var_m.where(var_m > 0, other=np.nan), np.nan)
    out = (var_r - resid_var).clip(lower=0.0)
    return np.sqrt(out).where(var_m > 0)


def compute_factors(daily: pd.DataFrame, mkt: pd.Series | None = None) -> pd.Series:
    """单只股票的日线 → 因子向量。

    daily 需按 date 升序；可选 adjust_factor 列（缺省按 1.0 处理）；
    可选 turn 列（无则 turn20/turnratio=NaN，该股被排名剔除——宁缺毋滥）；
    可��� mkt（等权市场日收益，index=date）——**ivol 需要它**，
    没有就返回 NaN，绝不用近似值顶替。

    ## 因子集合必须与 `aplus.panel.FACTOR_COLUMNS` 一致

    面板（回测路径）算得出、这里算不出的因子，会让「赛马晋级」变成空头支票：
    权重一旦用到它，实盘当天就 KeyError。2 年终裁的第一名 `{"ivol": -1.0}`
    正是这种情形（2026-10-11）。
    `tests/test_live_factor_parity.py` 用真实因子集合做断言，只改一边就会红。
    """
    adj_close = daily["close"] * daily.get("adjust_factor", pd.Series(1.0, index=daily.index))
    adj_close = adj_close.fillna(daily["close"])
    has_turn = "turn" in daily.columns
    turn20 = turnover_mean(daily["turn"]) if has_turn else float("nan")

    # 书 3.8 节：mean(turn, 21) / mean(turn, 252)
    turnratio = float("nan")
    if has_turn:
        t = pd.to_numeric(daily["turn"], errors="coerce")
        num = t.rolling(TURN_RATIO_SHORT).mean().iloc[-1]
        den = t.rolling(TURN_RATIO_LONG).mean().iloc[-1]
        turnratio = (float(num / den) - 1.0) if den and den == den and den != 0 else float("nan")

    # 书 5.3 节 IVOL：与面板**共用同一个实现**（见 ivol_series 的 docstring）
    r = adj_close.pct_change()
    mkt_aligned = _align_market(mkt, daily)
    ivol_series_ = ivol_series(r, mkt_aligned)
    ivol = float(ivol_series_.iloc[-1]) if len(ivol_series_) else float("nan")

    return pd.Series({
        "mom20": momentum(adj_close),
        "rev5": reversal(adj_close, window=5),
        "vol20": volatility(adj_close),
        "vsur": volume_surge(daily["volume"]),
        "turn20": turn20,
        "turnratio": turnratio,
        "ivol": ivol,
        "rsi": rsi(adj_close),
    })


MAX_DAILY_RET = _IVOL_CLAMP


WINSOR_Q = 0.01      # 横截面缩尾分位（《因子投资：方法与实践》3.1.4 节用 1%）


RSI_WINDOW = 14  # Wilder 原版窗口；在看任何结果之前固定，改它需重跑全部历史结论


def rsi(close, n: int = RSI_WINDOW):
    """Wilder 1978 的 RSI：`100 - 100/(1 + 平均涨幅/平均跌幅)`，窗口固定 n 天。

    ## 这不是自进化

    公式固定、窗口固定、同一段输入永远给同一个输出（实测逐位相同）。
    **参数不因数据而改变 = 没有学习任何东西。** 把它当「新东西」加进来之前
    应该先认清这一点——见 tests/test_rsi.py::test_deterministic_no_learning。

    ## 定义（SMA 播种 + 递推平滑，不是 ewm）

        首次：avg_gain = 前 n 日涨幅的**简单平均**
        之后：avg = (avg × (n-1) + 当日) / n

    `pandas.ewm(alpha=1/n, adjust=False)` 用**第一个观测**播种，与 Wilder 的
    SMA 播种不是一回事。这里照公式实现，并由 `test_matches_wilder_reference`
    对拍——不拿另一个库当参照，那只会变成「这个库和那个库差多少」。

    ## 口径选择

    **横截面用法**（本实现）：RSI 高 = 近期强，可直接作因子权重。
    **时间序列用法**（RSI<30 抄底）是**择时**，`composite_score` 表达不了——
    硬塞进来等于假装本平台支持择时。那是另一个策略，不是换个权重。

    真实横截面实测（2026-10-09，5196 只）RSI 与 `mom20` 的 Spearman 相关
    **+0.8648**：它是动量的**有界平滑版本**，预期增量有限，Top10 只重叠 3/10。
    既然相关这么高，它是否值得当独立因子，**只能靠赛马实测，不能靠推理**。

    价格必须用**后复权**：不复权价在除权日跳空，会造出假的涨跌污染分子分母
    （书 3.1.2：无论算收益还是回溯，一律用后复权）。
    """
    values = np.asarray(pd.Series(close).to_numpy(dtype=float), dtype=float)
    if values.size <= n:
        return float("nan")
    d = np.diff(values)
    if not np.all(np.isfinite(d[-n:])):
        return float("nan")
    gain = np.clip(d, 0.0, None)
    loss = np.clip(-d, 0.0, None)
    ag = float(gain[:n].mean())
    al = float(loss[:n].mean())
    for i in range(n, d.size):
        ag = (ag * (n - 1) + gain[i]) / n
        al = (al * (n - 1) + loss[i]) / n
    if al == 0.0:
        return 100.0 if ag > 0.0 else 50.0
    return float(100.0 - 100.0 / (1.0 + ag / al))


def rsi_series(close: pd.Series, n: int = RSI_WINDOW) -> pd.Series:
    """`rsi` 的向量版本：整条时间轴一次算完，与标量**逐位一致**。

    ## 为什么不能直接用 ewm

    `gain.ewm(alpha=1/n, adjust=False)` 从**第一个观测**播种，
    Wilder 用的是**前 n 个的简单平均**。两者虽然共用同一个递推
    `X[t] = (1−a)·X[t−1] + a·g[t]`（a = 1/n），但初值不同，差值按
    `(1−a)^k` 衰减——小样本上差得可观。

    播种修正（精确，非近似）：

        W[t] = E[t] + (W[n−1] − E[n−1]) · (1−a)^(t−n+1)

    拿这个循环跑 5460 只 × 1882 根要几十秒；面板每组候选都要重算一次，
    不可接受。`tests/test_rsi.py::test_rsi_series_matches_scalar_exactly`
    对拍 20 组随机序列，差值必须为 0。
    """
    values = pd.to_numeric(pd.Series(close), errors="coerce")
    d = values.diff()
    gain = d.clip(lower=0.0)
    loss = (-d).clip(lower=0.0)
    a = 1.0 / float(n)

    e_gain = gain.ewm(alpha=a, adjust=False).mean()
    e_loss = loss.ewm(alpha=a, adjust=False).mean()
    # Wilder 的 SMA 播种值（与 ewm 的递推起点同一个位置）
    w_gain_seed = gain.rolling(n).mean()
    w_loss_seed = loss.rolling(n).mean()

    # 修正量：从播种位置起按 (1−a) 逐期衰减
    decay = (1.0 - a) ** np.arange(len(values), dtype=float)
    idx = np.arange(len(values), dtype=float)
    valid_seed = ~w_gain_seed.isna().to_numpy()
    if not valid_seed.any():
        return pd.Series(np.nan, index=values.index, dtype=float)
    first = int(np.argmax(valid_seed))
    # 修正量只在播种位置之后生效：decay 从播种处重新归一
    corr = np.full(len(values), np.nan)
    corr[first:] = decay[first:] / decay[first]
    # 修正量只由**播种那一个常数差**决定，再让它按 (1−a) 逐期衰减。
    # 写成 (W_seed[t] − E[t]) × 衰减是错的：rolling(n).mean() 会一直滑，
    # 它在 t 时刻不是 Wilder 的递推值——实测第 15 期起就分道扬镳。
    dg_seed = float(w_gain_seed.iloc[first] - e_gain.iloc[first])
    dl_seed = float(w_loss_seed.iloc[first] - e_loss.iloc[first])
    dg = dg_seed * np.nan_to_num(corr, nan=0.0)
    dl = dl_seed * np.nan_to_num(corr, nan=0.0)

    ag = e_gain.to_numpy() + np.nan_to_num(dg, nan=0.0)
    al = e_loss.to_numpy() + np.nan_to_num(dl, nan=0.0)
    ok = ~np.isnan(ag) & ~np.isnan(al) & (idx >= first)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al[ok] == 0.0, np.inf, ag[ok] / al[ok])
        out = np.where(rs == np.inf, 100.0, 100.0 - 100.0 / (1.0 + rs))
    series = pd.Series(np.nan, index=values.index, dtype=float)
    series.iloc[np.where(ok)[0]] = out
    return series


def winsorize(scores: pd.Series, lo: float = WINSOR_Q, hi: float = 1 - WINSOR_Q) -> pd.Series:
    """横截面缩尾：把尾部极端值压到分位点。

    ## 为什么必须有（2026-10-11 实测）

    `composite_score` 原先直接 z-score。z-score 对极值是**线性放大**的：
    一只 `vol20` 异常高的股票 z 值可达 +50，它一个人把整列标准差撑起来，
    让其余 5400 只的 z 值全部塌向 0。

    后果落到目标函数上：`objective = 100 × 涨腿超额 + 总命中率`，14 组候选上
    两个分项的跨度分别是

    - `100 × 超额`：**27.774**（几乎全由两组极端值撑起，其余都在 ±1 内）
    - `命中率`：**0.076**

    也就是说**排序几乎完全由极端超额决定**。（此前我误判为「命中率主导」，
    方向是反的：真正的问题是超额项被少数被污染的样本垄断。）

    ## 为什么用分位数，不用 MAD / 3σ

    MAD 与 3σ 都依赖均值/标准差，**它们本身就被极值污染**——用被污染的统计量
    去判断污染，是循环论证。分位数不依赖任何被污染的量。

    ## 语义

    只压尾部，**不改变中部股票的相对次序**（有测试钉死）。若缩尾影响了中部，
    说明它已不再是缩尾，而是在系统性削弱信号。
    """
    values = pd.to_numeric(scores, errors="coerce")
    valid = values.dropna()
    if len(valid) < 3:
        return values
    lower, upper = valid.quantile(lo), valid.quantile(hi)
    if not (np.isfinite(lower) and np.isfinite(upper)) or upper <= lower:
        return values
    return values.clip(lower=lower, upper=upper)


def robust_z(scores: pd.Series, lo: float = WINSOR_Q, hi: float = 1 - WINSOR_Q) -> pd.Series:
    """**缩尾只用于估计尺度，排名一律用原值**——严格单调，不制造并列。

    ## 为什么不用 `winsorize` + z-score

    `winsorize` 是硬 `clip`：超过 99% 分位的值全部压成**同一个数**。
    2026-10-11 在真实横截面（2026-10-09，5215 只）上实测：

        现方案(硬 clip)：不同取值 4948/5215，最大并列组 53，并列股票 380
        Top10 内部并列：**9 / 10**

    也就是**「前 10 名」是 53 只分数完全相同的股票里按代码字母序挑的 10 个**。
    榜单头部因此不是「因子最强的 10 只」，而是「并列票的字母序前 10」。

    ## 也不���用「z 封顶」

    实测替代方案「稳健 z + |z| ≤ 4 封顶」更差：57 只被压到 4.0，
    最大并列组 **52**。**封顶 z 与 clip 值制造的是同一类并列。**

    ## 本函数的做法

        参考序列 = clip(原值, lo, hi)     # 只用来估均值/标准差
        均值/标准差 ← 参考序列             # 极值不撑大分母（缩尾的本来目的）
        z = (原值 − 均值) / 标准差         # 严格单调，排序与原始因子完全一致

    仿射变换保序，所以 Top-N 选取与直接按原始因子排序**逐位相同**。
    「异常值不许霸榜」由数据层接管：`MAX_PLAUSIBLE_RETURN = 0.40`
    在因子层把 |日收益| > 40% 的样本作废，真实横截面里不存在 30.0 这种 mom20。

    样本量不足或分位退化时退回普通 z-score——此时无尾部可压，尺度本来就稳。
    """
    values = pd.to_numeric(scores, errors="coerce")
    valid = values.dropna()
    if len(valid) < 3:
        return cross_section_z(values)
    lower, upper = valid.quantile(lo), valid.quantile(hi)
    if not (np.isfinite(lower) and np.isfinite(upper)) or upper <= lower:
        return cross_section_z(values)
    ref = values.clip(lower=lower, upper=upper)
    std = float(ref.std())
    if not np.isfinite(std) or std <= 0:
        return cross_section_z(values)
    return (values - float(ref.mean())) / std


def composite_score(factor_rows: pd.DataFrame, weights=None) -> pd.Series:
    """横截面**缩尾后**标准化，再按权重合成（v0 等权，权重是回测调参的抓手）。

    任一因子 NaN → 合成分 NaN（数据不完整的股票不得参与排名，
    宁缺毋滥——绝不能被 sum(skipna) 静默当 0 混入）。

    稳健标准化（`robust_z`）必须在**排名路径**上：缩尾只估尺度、排名用原值，
    见 `robust_z` docstring 里「53 只并列、Top10 里 9 只同分」的实测。
    逐列处理（而不是对合成分处理）：各因子量纲不同
    （`mom20`/`vol20` 是小数，`vsur`/`turn20` 是比值与百分比），
    合成分的分布无法跨列比较，缩尾阈值没有统一含义。

    **分母用权重绝对值之和**（`sum(|w|)`），不是 `sum(w)`。
    见 tests/test_score_sign.py：`sum(w)` 为负时 `(−z)/(−1) = +z`，
    会把负权候选的整组方向翻转——2026-10-11 的单因子探针赛马里，
    `{turnratio:-1}` 与 `{turnratio:+1}` 跑出了逐位相同的结果。
    对纯正权重候选两者相等，故历史结论不会因这次修复而失效。
    """
    weights = weights or DEFAULT_WEIGHTS
    norm = sum(abs(float(w)) for w in weights.values())
    if norm == 0:
        return pd.Series(np.nan, index=factor_rows.index)
    z = factor_rows[list(weights)].apply(robust_z, axis=0)
    return z.mul(pd.Series(weights)).sum(axis=1, min_count=len(weights)) / norm
