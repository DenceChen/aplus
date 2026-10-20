"""基本面族轮次 A 的**共享**读数与统计层（私有模块，非产物生成器）。

## 为什么是私有模块而不是三份拷贝

仓内铁律「形状 17」：同一语义被处理 N 次 ⇒ 必有第 N+1 处漏。
本轮三个产物（A1 污染集中度 / A2 覆盖池轴向偏差 / A3 因子族就绪）要共用三样东西：

1. **单票单行读法**（`read_bars`）—— 逐个 parquet 读**一根 bar**，
   **绝不建面板**（GB 级）——这是本轮的内存纪律。
2. **卡方尾概率**（`chi2_sf`）—— 本仓无 scipy（`aplus/dist.py` 已写明理由），
   用不完全 Gamma 的连分式展开（Numerical Recipes §6.2）。
3. **分档与占比偏差**（`decile_edges` / `max_abs_share_dev_pp`）——
   **同一分档边界**是 A1/A2 对拍的承重件，两处各写一遍必然漂移。

## 🚨 口径三则（读之前先看）

1. **价格 = 不复权 close**，取 `day` 当天那根 bar；当天没 bar ⇒ 取**之前最后一根**
   （与 `aplus/factor_fundamental.py::close_on` 的生产语义逐字一致）。
   复权价一律不用（除权会同时改价和 BPS 分子）。
2. **财务侧只从 `aplus.vintage.pit_frame(root, as_of)` 取**，
   年报口径由 `aplus/factor_fundamental.py::annual_from_frame` 决定——
   本模块**不自己发明**选择协议。
3. **「末行」≠「当日行」**：daily parquet 的末行是全仓最新交易日（2026-10-20），
   而 A1 要的是 2026-10-14 那根。两者都要能取，故 `read_bars` 按**指定日期**取，
   并把**陈旧价**（实际 bar 日 < 请求日）的只数报出来。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# ── 卡方尾概率（无 scipy）────────────────────────────────────────────────

_MAXIT, _EPS, _FPMIN = 300, 3e-16, 3e-300


def _gser(a: float, x: float) -> float:
    """不完全 Gamma 的**级数**展开（x < a+1 时收敛快）。"""
    ap = a
    s = 1.0 / a
    dl = s
    for _ in range(_MAXIT):
        ap += 1.0
        dl *= x / ap
        s += dl
        if abs(dl) < abs(s) * _EPS:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """不完全 Gamma 的**连分式**展开（x >= a+1 时收敛快）。"""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammq(a: float, x: float) -> float:
    """正则化**上**不完全 Gamma Q(a, x) = 1 − P(a, x)。"""
    if x < 0.0 or a <= 0.0:
        raise ValueError(f"gammq 的定义域是 a>0 且 x>=0，收到 a={a!r} x={x!r}")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def chi2_sf(x: float, df: int) -> float:
    """卡方分布的上尾概率 P(X > x)。`df` 必填、无默认。

    `df` 不给默认值是刻意的（`aplus/dist.py` 同款纪律）：
    给 df 默认值 = 把 bug 装进签名。`x <= 0` ⇒ 1.0。
    """
    if df <= 0:
        return float("nan")
    if x <= 0.0:
        return 1.0
    return _gammq(0.5 * df, 0.5 * x)


# ── 单票单行读价格（不建面板）───────────────────────────────────────────

#: daily parquet 的最小列集。要「末行」也要「指定日期那行」，
#: 一次读这几列就够 ⇒ 每票仍是 O(1) 行进内存。
_BAR_COLS = ["date", "close", "volume", "turn"]


def read_bars(root: Path | str, day: str) -> pd.DataFrame:
    """逐票取「`day` 当天那根 bar」（无则取之前最后一根）。

    返回 `DataFrame[code, bar_date, close, volume, turn]`，按 code 排序。

    ## 🚨 为什么不建面板

    本仓有 `aplus/panel.py::build_panel`（GB 级、10 分钟量级）。
    本轮**明令不许跑**（另一个 agent 正在占内存）。这里逐文件读**一根** bar，
    峰值内存 = 一只票的一行 ⇒ 全池 5,460 只读完也只留 5,460 行。

    ## 陈旧价的口径

    当天没 bar ⇒ 取之前最后一根（生产语义，`aplus/factor_fundamental.py::close_on`）。
    停牌/退市票因此会用**陈旧价**——这是生产口径，不是缺陷，
    但**必须报出来**（`bar_date < day` 的只数），否则读者会以为全是当日价。
    """
    from aplus.factor_fundamental import bare_code

    root = Path(root)
    files = sorted((root / "master" / "daily").glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{root}/master/daily 下没有 parquet")
    rows = []
    for p in files:
        t = pq.read_table(p, columns=_BAR_COLS).to_pandas()
        if t.empty:
            continue
        t = t.sort_values("date", kind="stable")
        sub = t[t["date"] <= day]
        if sub.empty:
            continue
        last = sub.iloc[-1]
        rows.append((p.stem, bare_code(p.stem), str(last["date"]),
                     float(last["close"]), float(last["volume"]),
                     float(last["turn"])))
    # 🚨 `code` 是**裸 6 位**（`aplus/factor_fundamental.py::bare_code` 的生产口径）——
    #    daily parquet 的文件名带 `.SZ/.SH` 后缀，而 `annual_from_frame` 返回裸码。
    #    两边不一致 ⇒ join **零行命中**，且 merge 静默返回空表（形状 17 的老坑）。
    #    保留 `code_full` 是为了报错时能看出是哪一只票对不上。
    out = pd.DataFrame(rows, columns=["code_full", "code", "bar_date", "close",
                                      "volume", "turn"])
    return out.sort_values("code").reset_index(drop=True)


def stale_price_count(bars: pd.DataFrame, day: str) -> int:
    """`bars` 里实际 bar 日 < 请求日的只数（陈旧价）。"""
    return int((bars["bar_date"] < day).sum())


# ── 分档与占比偏差 ──────────────────────────────────────────────────────

def decile_edges(values: np.ndarray, n_deciles: int = 10) -> np.ndarray:
    """按**值**的等频边界（长度 n_deciles−1）。

    ## 为什么必须按值而不是按位置

    要把**外来的一行**（变更行）放进「全池的某一档」，
    档位必须由**值域**决定 ⇒ 边界必须是值的分位点。
    按位置分箱（`scripts/bm_ic_attribution.py::decile_table` 的做法）
    只能给同一批数据自己分档，**装不进外来行**。
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.array([])
    qs = np.linspace(0.0, 1.0, n_deciles + 1)[1:-1]
    return np.unique(np.quantile(v, qs))


def assign_deciles(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """把每个值落到档位（0 起）。边界外 ⇒ 0 或末档（`searchsorted` 语义）。"""
    v = np.asarray(values, dtype=float)
    if edges.size == 0:
        return np.zeros(v.shape, dtype=int)
    idx = np.searchsorted(edges, v, side="right")
    return idx.astype(int)


def decile_counts(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """按给定边界统计各档只数（长度 = 档数）。"""
    n = int(edges.size) + 1
    a = assign_deciles(values, edges)
    return np.bincount(a, minlength=n)[:n]


def max_abs_share_dev_pp(obs_counts: np.ndarray, exp_props: np.ndarray) -> float:
    """读法**甲**：观测占比与期望占比的最大绝对偏差，单位**百分点**。

    `obs_counts` 是各档只数（分母 = 观测总数），`exp_props` 是各档期望占比（和为 1）。
    ⚠️ 两个分母不同：观测用「变更行总数」，期望用「全池占比」——
    这正是要检验的「变更行是否按全池的形状分布」。
    """
    obs = np.asarray(obs_counts, dtype=float)
    exp = np.asarray(exp_props, dtype=float)
    n = float(obs.sum())
    if n <= 0:
        return float("nan")
    return float(np.max(np.abs(obs / n - exp)) * 100.0)


def max_abs_rate_dev_pp(obs_counts: np.ndarray, pool_counts: np.ndarray) -> float:
    """读法**乙**：每档「污染率」相对**全池总污染率**的最大绝对偏差，单位百分点。

    污染率(d) = 变更行数(d) / 该档全池行数(d)；全池总污染率 = 总变更行 / 总池行。
    分母是**每档自己的池子**，不是变更行总数。
    """
    obs = np.asarray(obs_counts, dtype=float)
    pool = np.asarray(pool_counts, dtype=float)
    if obs.sum() <= 0 or pool.sum() <= 0:
        return float("nan")
    rate = np.divide(obs, pool, out=np.zeros_like(obs), where=pool > 0)
    return float(np.max(np.abs(rate - obs.sum() / pool.sum())) * 100.0)


def mc_null_max_dev(exp_props: np.ndarray, n_obs: int, draws: int = 20000,
                    seed: int = 20261020) -> np.ndarray:
    """零假设下「读法甲的最大绝对占比偏差」的**蒙特卡洛**分布。

    零假设 = 变更行在**全池的形状**下均匀落下（即与全池同分布）。
    用途：把「观测偏差」放到**噪声尺度**上比。
    🚨 没有它，「偏差 3pp」这句话**没有参照系**——
    n=64 行时单个档位的占比标准差就有 √(0.1×0.9/64) ≈ 3.75pp，
    而**十个档位的最大值**还要更大。这正是预注册 §6.1 那句
    「占全池 0.044pp ⇒ 3pp 门槛是『远超随机』」的**分母口径问题**：
    它把「行」换算成「占全池的份额」，而判据比的是**占比差**。
    本函数把两个口径都摆出来，让读数能自己判断。
    """
    p = np.asarray(exp_props, dtype=float)
    p = p / p.sum()
    rng = np.random.default_rng(seed)
    out = np.empty(draws, dtype=float)
    for i in range(draws):
        cnt = rng.multinomial(n_obs, p)
        out[i] = np.max(np.abs(cnt / n_obs - p)) * 100.0
    return out


def mc_null_max_rate_dev(pool_counts: np.ndarray, n_obs: int, draws: int = 20000,
                         seed: int = 20261020) -> np.ndarray:
    """零假设下「读法乙的最大绝对污染率偏差」的蒙特卡洛分布。

    零假设 = 变更行在全池形状下均匀落下（⇒ 每档污染率 = 全池总污染率）。
    分母与 `max_abs_rate_dev_pp` 一致 ⇒ **可与观测值同口径比较**。
    """
    pool = np.asarray(pool_counts, dtype=float)
    p = pool / pool.sum()
    rng = np.random.default_rng(seed)
    out = np.empty(draws, dtype=float)
    for i in range(draws):
        cnt = rng.multinomial(n_obs, p)
        rate = np.divide(cnt, pool, out=np.zeros_like(cnt, dtype=float), where=pool > 0)
        out[i] = np.max(np.abs(rate - n_obs / pool.sum())) * 100.0
    return out


def mc_null_max_dev_subsample(decile_of_item: np.ndarray, n_sub: int,
                              draws: int = 20000,
                              seed: int = 20261020) -> np.ndarray:
    """零假设下「子样本 vs 全池」的最大绝对分档占比偏差的蒙特卡洛分布。

    零假设 = 子样本是从全池**随机抽**的（无选择偏差）。
    适用场景（A2）：覆盖池 500 只是 akshare 返回的**非随机**子集，
    问的是「它的因子分布偏离全池的程度，比随机抽样能产生的偏离**更大**吗」。

    与 `mc_null_max_dev` 的区别：那个是「按形状多项抽样」（A1 用），
    这个是**有放回地从实际行里抽**（保留档内结构）⇒ 更贴近「抽 500 只票」这件事本身。
    """
    dec = np.asarray(decile_of_item)
    n_dec = int(dec.max()) + 1
    rng = np.random.default_rng(seed)
    n = len(dec)
    out = np.empty(draws, dtype=float)
    for i in range(draws):
        idx = rng.integers(0, n, size=n_sub)
        cnt = np.bincount(dec[idx], minlength=n_dec)[:n_dec]
        out[i] = np.max(np.abs(cnt / n_sub - np.bincount(dec, minlength=n_dec)[:n_dec] / n)) * 100.0
    return out


def mc_null_max_dev_categorical(cat_of_item: np.ndarray, n_sub: int,
                                 n_cat: int, draws: int = 20000,
                                 seed: int = 20261020) -> np.ndarray:
    """零假设下「随机子样本 vs 全池」的**分类**分布最大绝对占比偏差（百分点）。

    与 `mc_null_max_dev_subsample` 同源（随机抽 n_sub 行），
    但分的是**类别**（板块）而不是**等频档位**（上市年代是连续量，用直方图分箱）。
    """
    cat = np.asarray(cat_of_item)
    rng = np.random.default_rng(seed)
    n = len(cat)
    full = np.bincount(cat, minlength=n_cat)[:n_cat] / n
    out = np.empty(draws, dtype=float)
    for i in range(draws):
        idx = rng.integers(0, n, size=n_sub)
        cnt = np.bincount(cat[idx], minlength=n_cat)[:n_cat]
        out[i] = np.max(np.abs(cnt / n_sub - full)) * 100.0
    return out
