"""特质性波动率 IVOL + 量价数据处理纪律（TDD，2026-10-11）。

## 因子来源

《因子投资：方法与实践》（石川/刘洋溢/连祥斌）第 5.3 节「特质性波动率」。
实证口径原文：**用过去 21 个交易日个股超额收益率对因子收益率做时序回归，
取残差收益率的波动率作为 IVOL**；组合构建是**做多最低 IVOL、做空最高 IVOL**。

书里给的 A 股结果：等权重下对 FF3 的月均超额 **+1.27%，t = 8.64**；
市值加权 +0.82%，t = 3.64。

**为什么值得试**：这是我们缺的因子，且是**纯价量可算**——不需要财报、市值、
分钟线。而现有 5 个因子（动量/反转/波动/量能/换手）样本外全线失效。

**但书里也给了两条降温**，所以必须严格样本外：
- 换用 Liu–Shi–Lian 四因子定价后，月均超额只剩 0.36%、t=1.62，**不再显著**
- Anderson et al. (2015) 指出该异象**对极端点极度敏感**，去掉 1987-10 那个点后
  负相关就消失了

结论只能由样本外来给，不能由这里的构造正确性来给。

## 数据处理纪律（同书 3.1.2 节）

1. **最少交易日**：窗口内有效样本须 ≥ 窗口的 2/3 才计算，否则作缺失。
   21 日窗口 → 至少 14 个有效样本。滥用停牌的公司会用极小样本算出偏差很大的波动率。
2. **停牌日不得填 0**：算波动率时填 0 等于「这天没波动」，会**系统性低估**波动率
   （动量类可以填——用上一个非停牌日的价格作基期，逻辑上通顺；波动率不行）
3. **复牌首日异常值压缩**：长期停牌复牌不设涨跌停，实测出现过单日 +593%。
   书里的处理是把日收益率 >10% 压到 10%、< -10% 压到 -10%。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.panel import _factor_series, market_return_series


def _frame(closes, volumes=None, dates=None):
    """构造单股帧。dates 必须与传入的 mkt 序列**同一套标签**——否则 reindex 静默对不上，
    这正是最初写测试时踩的坑。"""
    n = len(closes)
    dates = dates if dates is not None else [f"2026-01-{i + 1:02d}" for i in range(n)]
    return pd.DataFrame({
        "date": dates, "close": closes, "open": closes,
        "volume": volumes if volumes is not None else [1e6] * n,
        "adjust_factor": 1.0,
    })


# ── 市场收益序列 ──

def test_market_return_is_cross_sectional_mean():
    """等权市场收益 = 各股日收益的横截面均值。"""
    a = pd.Series([0.01, -0.02, 0.03], index=["d1", "d2", "d3"])
    b = pd.Series([0.03, 0.00, 0.01], index=["d1", "d2", "d3"])
    m = market_return_series({"A": a, "B": b})
    assert m["d1"] == pytest.approx(0.02)
    assert m["d2"] == pytest.approx(-0.01)


def test_market_return_ignores_empty_frames():
    m = market_return_series({"A": pd.Series([0.01, 0.02], index=["d1", "d2"]),
                              "B": pd.Series(dtype=float)})
    assert m["d1"] == pytest.approx(0.01)


def test_market_return_empty_input_is_empty_series():
    assert len(market_return_series({})) == 0


# ── IVOL 构造 ──

def test_stock_tracking_market_has_near_zero_ivol():
    """完全跟随市场的股票，残差波动应接近 0。"""
    idx = [f"d{i:02d}" for i in range(60)]
    mkt = pd.Series([0.01 if i % 2 else -0.008 for i in range(60)], index=idx)
    close = np.cumprod(1 + mkt.to_numpy()) * 100
    out = _factor_series(_frame(close, dates=idx), mkt=mkt)
    tail = out["ivol"].dropna()
    assert len(tail) > 0
    assert float(tail.iloc[-1]) < 1e-9, "无特质波动的股票 IVOL 应为 0"


def test_ivol_matches_true_ols_residual_std():
    """IVOL 必须精确等于**同窗口内 OLS 回归的残差标准差**。

    断言对象是 numpy.linalg.lstsq 的真值，不是 `idio.std(ddof=0)`——后者是错的：
    21 个样本估出的 beta 是 1.219 而非真实构造的 1.1（采样噪声），且 OLS 拟了两个
    参数，残差方差会按自由度收缩。两者的差（约 0.2%）恰恰是「用公式近似」与
    「真回归」的差，这条测试就是要把公式钉死在真值上。
    """
    idx = [f"d{i:02d}" for i in range(80)]
    rng = np.random.default_rng(7)
    # 幅度刻意压小：IVOL 用的是**钳制后**的收益率（书 3.1.2 节，复牌无涨跌停可单日 +593%），
    # 样本必须全部落在 ±10% 内，否则钳制会改变期望值，恒等式就不再精确成立。
    mkt = pd.Series(rng.normal(0, 0.004, 80), index=idx)
    idio = pd.Series(rng.normal(0, 0.008, 80), index=idx)
    ret = 1.1 * mkt + idio                       # beta=1.1，残差=idio
    assert ret.abs().max() < 0.10, "样本须全部落在钳制阈值内"
    close = np.cumprod(1 + ret.to_numpy()) * 100

    out = _factor_series(_frame(close, dates=idx), mkt=mkt)
    got = float(out["ivol"].dropna().iloc[-1])

    w = 21
    R = pd.Series(close, index=idx).pct_change().iloc[-w:].to_numpy()
    M = mkt.iloc[-w:].to_numpy()
    X = np.column_stack([np.ones(w), M])
    coef, *_ = np.linalg.lstsq(X, R, rcond=None)
    truth = float((R - X @ coef).std(ddof=0))

    assert got == pytest.approx(truth, rel=1e-10), "IVOL 必须等于真 OLS 残差标准差"


def test_ivol_is_nan_without_market_series():
    """没给市场序列时 IVOL 应为 NaN，而不是悄悄给个错数。"""
    out = _factor_series(_frame([100.0 + i for i in range(60)]))
    assert "ivol" in out.columns
    assert out["ivol"].isna().all()


def test_ivol_nan_when_market_has_zero_variance():
    """市场收益恒定 → beta 无定义 → IVOL 必须 NaN，不能 inf。"""
    idx = [f"d{i:02d}" for i in range(60)]
    mkt = pd.Series([0.01] * 60, index=idx)
    close = np.cumprod(1 + np.array([0.01, 0.02, 0.005, -0.01] * 15)) * 100
    out = _factor_series(_frame(close, dates=idx), mkt=mkt)
    v = out["ivol"].dropna()
    assert len(v) == 0, "市场零方差时 IVOL 应全为缺失"


def test_ivol_never_infinite():
    idx = [f"d{i:02d}" for i in range(60)]
    mkt = pd.Series([0.0] * 30 + [0.02] * 30, index=idx)
    close = np.cumprod(1 + np.array([0.05, -0.05] * 30)) * 100
    out = _factor_series(_frame(close, dates=idx), mkt=mkt)
    assert not np.isinf(out["ivol"].to_numpy(dtype=float)).any()


# ── 数据处理纪律 ──

def test_vol20_requires_two_thirds_valid_samples():
    """21 日窗口里只有 10 天有数据 → vol20 应作缺失，而不是用 10 个点硬算。"""
    n = 60
    closes = np.full(n, 100.0)
    # 让前 40 天里有 30 天收益为 0（模拟停牌/无波动），只有 11 天有真实变动
    closes[30:41] = 101.0
    out = _factor_series(_frame(closes))
    v = out["vol20"].to_numpy(dtype=float)
    # 窗口内有效样本不足的日期必须为 NaN
    assert np.isnan(v).any(), "有效样本不足时必须作缺失"


def test_resumption_day_return_is_clamped():
    """长期停牌复牌的单日大涨被压到 ±10%（书 3.1.2 节）。

    **与 `MAX_PLAUSIBLE_RETURN` 的分工**：|收益| > 40% 视为**数据不可信**，
    直接作废（见 test_implausible_return.py）；落在 (10%, 40%] 区间的
    「复牌无涨跌停可多涨」才走钳制。实测那个 +593.72% 的样本现已归入作废档
    ——那条数据本身就是源站把缩股记成跳空造成的，不是真实的复牌行情。
    """
    n = 40
    closes = np.full(n, 100.0)
    closes[20] = 100.0 * (1 + 0.25)            # +25%：复牌无涨跌停，合法但需压缩
    out = _factor_series(_frame(closes))
    r = out["ret_clamped"]
    assert not np.isinf(r.to_numpy(dtype=float)).any()
    assert float(r.iloc[20]) == pytest.approx(0.10), "+25% 应被压到 +10%"


def test_resumption_day_beyond_plausible_band_is_voided_not_clamped():
    """+593.72% 那种量级**不再**被钳制，而是直接作废——它不是行情，是数据缺陷。

    钳到 ±10% 会把一个「源站把缩股记成跳空」的数据缺陷伪装成一次温和的复牌，
    让它继续参与波动率与 IVOL 的估计；作废则让它退出统计，缺陷可被追溯。
    """
    n = 40
    closes = np.full(n, 100.0)
    closes[20] = 100.0 * (1 + 5.9372)
    out = _factor_series(_frame(closes))
    assert pd.isna(out["ret_clamped"].iloc[20]), (
        "不可信的价格跳变被钳成了 +10% —— 数据缺陷被伪装成了正常行情"
    )
    assert not out["vol20"].isna().all(), "作废不应让整列因子都消失"


# ── 停牌日不得进入任何波动率估计（2026-10-11 补充的红线） ──────────────────

def test_suspended_days_are_excluded_from_ret_clamped():
    """`ret_clamped` 必须**同时**剔停牌与压极值，不能只压极值。

    原实现 `ret_raw.clip(±10%)` 在停牌日给出 0（close 不变），于是停牌股一路
    把 0 灌进 IVOL 的方差与回归里——**系统性低估波动率**。实测触发面不小：
    停牌在 A 股是常态（重组、增发、重大事项停牌可达数月）。
    """
    n = 40
    closes = np.full(n, 100.0)
    volumes = [1e6] * n
    volumes[15:20] = [0.0] * 5                  # 连续 5 天停牌，close 维持不变
    out = _factor_series(_frame(closes, volumes=volumes))
    r = out["ret_clamped"].to_numpy(dtype=float)
    assert np.isnan(r[15:20]).all(), (
        f"停牌日仍出现在 ret_clamped 里：{r[15:20]}——"
        f"填 0 等于宣称这天没波动，会系统性低估 IVOL"
    )


def test_ivol_of_heavily_suspended_stock_is_higher_not_lower():
    """同一只股票上直接对比两种处理：停牌日**填 0** vs **剔除**。

    旧实现把停牌日的 0 当作「这天波动率为零」灌进方差与回归，得到的 IVOL
    **低于**真实水平；剔除后才是「真正发生过的交易」的波动。

    刻意在同一只股票、同一段价格上做 A/B——换一只股票去比是比不出这个问题的，
    因为停牌改变了样本构成，两只股票的波动本来就不可比。
    """
    w, n = 21, 60
    rng = np.random.default_rng(11)
    idx = [f"d{i:02d}" for i in range(n)]
    mkt = pd.Series(rng.normal(0, 0.004, n), index=idx)

    closes, volumes = [100.0], [1e6]
    shocks = rng.normal(0, 0.01, n - 1)
    for i in range(1, n):
        if i % 4 == 0:                       # 每 4 天停牌一次：close 不变、volume=0
            closes.append(closes[-1])
            volumes.append(0.0)
        else:
            closes.append(closes[-1] * (1 + shocks[i - 1]))
            volumes.append(1e6)
    assert volumes[-w:].count(0.0) >= 5, "前提：这 21 天里必须有足够多的停牌日"

    got = float(_factor_series(_frame(closes, volumes=volumes, dates=idx),
                               mkt=mkt)["ivol"].dropna().iloc[-1])

    # 反事实：把停牌日按 0 收益喂进去（旧实现的口径）
    adj = pd.Series(closes, index=idx)
    ret_fill = adj.pct_change().fillna(0.0).to_numpy()[-w:]
    m_win = mkt.to_numpy()[-w:]
    X = np.column_stack([np.ones(w), m_win])
    coef, *_ = np.linalg.lstsq(X, ret_fill, rcond=None)
    filled = float((ret_fill - X @ coef).std(ddof=0))

    assert got > filled, (
        f"剔除停牌日后 IVOL = {got:.6f}，填充 0 的旧口径 = {filled:.6f} —— "
        f"把停牌当「零波动」会系统性低估波动率"
    )


def test_vol20_also_clips_resumption_extremes():
    """`vol20` 的输入也必须是钳制后的序列。

    原实现用未钳制的 `ret_real`，而 `panel.py` 的 docstring 却写着
    「日收益率钳到 ±10% 再进入波动率与 IVOL」——文档说得比代码做得更多。
    单日 +593% 进入 std 会把整段波动率抬高一个量级。
    """
    n = 40
    closes = np.full(n, 100.0)
    closes[20] = 100.0 * (1 + 5.9372)
    base = _factor_series(_frame([100.0 + (i % 7) * 0.01 for i in range(n)]))
    spiked = _factor_series(_frame(closes))
    # 与无复牌尖峰的基线对比：尖峰不得把 vol20 抬到与日常波动量级脱节
    v_spike = float(spiked["vol20"].iloc[-1])
    v_base = float(base["vol20"].iloc[-1])
    assert np.isfinite(v_spike)
    assert v_spike <= 0.10 + 1e-9, (
        f"单日 +593% 未被压制，vol20 = {v_spike:.4f}（未复牌基线 {v_base:.4f}）"
    )


def test_min_samples_uses_ceiling_not_floor():
    """「有效样本 ≥ 窗口 2/3」必须向上取整，且**真的作用在 vol20 上**。

    取整方向反了很难发现：21 日窗口 × 2/3 = 14 恰好整除，只有 20 日窗口
    （2/3 = 13.33）才暴露。原实现用 `int(13.33)` = 13，比判据松。
    """
    n = 25
    # 构造最后 20 根 K 线里恰好 13 / 14 个有效样本
    def vol20_with(n_valid: int) -> float:
        closes = np.full(n, 100.0)
        volumes = [1e6] * n
        # 最后 20 根里，只让前 n_valid 根有效（有波动），其余停牌
        for i in range(n - 20, n - 20 + (20 - n_valid)):
            volumes[i] = 0.0
        closes[n - 1] = 101.0                    # 制造一个真实的非零收益
        out = _factor_series(_frame(closes, volumes=volumes))
        return float(out["vol20"].iloc[-1])

    v13 = vol20_with(13)
    v14 = vol20_with(14)
    assert not np.isfinite(v13), (
        f"20 日窗口只有 13 个有效样本（< 2/3 = 13.33）却算出了 {v13}"
    )
    assert np.isfinite(v14), "20 日窗口有 14 个有效样本（≥ 2/3）却作缺失"
