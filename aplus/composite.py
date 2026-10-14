"""因子合成的**纯函数**层：组合 IC 恒等式与权重求解。

## 为什么能只存相关矩阵

对同一天的横截面，把 k 个因子排成秩 `F`（n×k）、前瞻收益排成秩 `r`（n），
记

    R  = corr(F)          k×k，因子之间的秩相关
    Σ_f = corr(F, r)      k  ，每个因子与收益的秩相关

那么**任意权重** `w` 下组合分的 IC 是

    ρ(w) = (wᵀ Σ_f) / sqrt(wᵀ R w)

**这是恒等式，不是近似**：它就是 Pearson 相关的线性性，
分子是 `cov(wᵀF, r) = wᵀ cov(F, r)`，分母是 `sqrt(var(wᵀF)) = sqrt(wᵀ Rw)`。

于是每天只要存 **k²+k 个数**（8 因子 = 72），就足以算出**任意权重的组合 IC**——
不必再读面板、不必重跑 10 分钟。`scripts/extract_ic_matrices.py` 抽的就是它。

## 两条容易踩的坑

1. **口径**：所有相关必须算在**同一批行**上（各因子都有限的那些）。
   而 `aplus/factor_ic.py` 的单因子 IC 是**各因子用自己的有效子集**算的
   （`daily_ic_ranked` 里 `ok = np.isfinite(f)` 逐因子独立）。
   两者**不完全可比**——所以单因子与合成的对照，
   必须用抽取文件里**同一份** Σ_f，不许拿报告里的数来比。
2. **零方差**：某因子当天全并列（常见于缩尾后的分位）时相关系数无定义，
   `corrcoef` 给 NaN 而**不报错**。NaN 混进 R⁻¹ 会让权重整片变 NaN，
   而且看起来"算出来了"。这里显式守卫并报出是哪几个。
"""
from __future__ import annotations

import re

import numpy as np

#: 单因子方差低于此值视为当日无信息——秩相关对它无定义。
#: 不是「小」而是「零」：秩是整数，全并列时方差恰为 0。
ZERO_VAR_EPS = 1e-12


class DegenerateFactor(Exception):
    """当天有因子零方差，组合 IC 无定义。"""


def zscore(F: np.ndarray) -> np.ndarray:
    """按列 z 标准化（`ddof=1`）。

    **必须先标准化再加权**，否则 `composite_ic` 给的是另一个数——
    ���且是**系统性偏移**、不会崩的那种（见 `composite_ic` 的说明）。
    """
    F = np.asarray(F, dtype=float)
    sd = F.std(axis=0, ddof=1)
    if np.any(sd <= ZERO_VAR_EPS):
        raise DegenerateFactor("有列零方差，无法标准化")
    return (F - F.mean(axis=0)) / sd


def rank_zscore(F: np.ndarray) -> np.ndarray:
    """横截面**先排名、再 z 标准化**。

    ## 为什么必须排

    策略是**按分数排序取分位**的，所以描述它的统计量是**秩相关**
    （Spearman），不是 Pearson。`aplus/factor_ic.py` 的单因子 IC
    两端都用 `_rankdata` 排过名——单因子那边排了，这边不排，
    就是在拿两种东西做除法。

    ## 为什么复用 `_rankdata` 而不是自己写一个

    并列平均怎么处理，两处只要有一处不同，两侧的数就**再也比不了**，
    而差异小到不会有人发现。**单一真相源**在这里不是洁癖，是可比性的前提。

    > 这个 import **故意延迟到函数体内**：
    > `aplus/composite.py` 是纯函数层，不该因为要一个数值小helper
    > 就把 `factor_ic` → `backtest` 整条流水线拖进来。
    > 排名是本模块唯一的外部依赖，延迟导入把它挡在模块加载之外。

    秩化**会制造新的零方差**：原始值各不相同但秩全同（量化过的因子天天如此），
    所以 `zscore` 那道守卫在秩口径下**更容易**触发，仍然抛。
    """
    from aplus.factor_ic import _rankdata

    F = np.asarray(F, dtype=float)
    if F.ndim != 2:
        raise ValueError(f"要 (n, k) 的矩阵，收到形状 {F.shape}")
    Rk = np.column_stack([_rankdata(F[:, j]) for j in range(F.shape[1])])
    return zscore(Rk)


def daily_stats(F: np.ndarray, r: np.ndarray,
                names: tuple[str, ...] = ()) -> tuple[np.ndarray, np.ndarray]:
    """同一天的 (R, Σ_f)。

    `F` (n,k) 与 `r` (n,) 必须**已经排过秩**且长度相同、无 NaN。
    秩这件事由调用方保证——本模块只做线性代数。

    零方差因子抛 `DegenerateFactor` 并在消息里点名：
    **静默填 0 会让那个因子凭空「没有观点」**，而它其实是有观点的、
    只是当天所有股票都并列，没法比较。
    """
    F = np.asarray(F, dtype=float)
    r = np.asarray(r, dtype=float)
    if F.ndim != 2 or F.shape[0] != len(r):
        raise ValueError(f"F 形状 {F.shape} 与 r 长度 {len(r)} 不符")

    sd = F.std(axis=0, ddof=1)
    sr = r.std(ddof=1)
    if sr <= ZERO_VAR_EPS:
        raise DegenerateFactor("前瞻收益当天零方差")
    dead = [names[i] if i < len(names) else f"f{i}"
            for i in range(F.shape[1]) if sd[i] <= ZERO_VAR_EPS]
    if dead:
        raise DegenerateFactor(f"当天零方差的因子：{dead}")

    M = np.corrcoef(np.column_stack([F, r]), rowvar=False)
    k = F.shape[1]
    return M[:k, :k].copy(), M[:k, k].copy()


def composite_ic(w: np.ndarray, R: np.ndarray, sf: np.ndarray) -> float:
    """`ρ(w) = (wᵀΣ_f)/sqrt(wᵀRw)`，即组合分的秩相关。

    ## ⚠️ `w` 作用于 **z 标准化后**的因子

    `R` 与 `Σ_f` 都是相关系数，**无量纲**——它们隐含假设每个因子
    已被当日横截面的 z 标准化。于是 `wᵀRw` 是 `var(wᵀz(F))` 而**不是**
    `var(wᵀF)`；要拿原始因子直接加权，等价于把权重换成了 `w∘s`
    （`s` = 各因子的当日标准差）。

    忘了标准化会得到一个**看起来合理、系统性偏移**的数：
    本项目实测偏差 1.5e-5（`tests/test_composite.py` 钉住）。
    它不崩、不报异常，只是答案不是你要的那个——
    所以 `zscore()` 是**必须显式调用**的一步，不是可选优化。

    分母非正时抛异常而不是返回 0：**返回 0 会被读成「组合没有预测力」**，
    而实际含义是「这个权重组合不出信号」——两件事不该混。
    """
    w = np.asarray(w, dtype=float)
    R = np.asarray(R, dtype=float)
    sf = np.asarray(sf, dtype=float)
    denom2 = float(w @ R @ w)
    if not np.isfinite(denom2) or denom2 <= ZERO_VAR_EPS:
        raise DegenerateFactor("wᵀRw ≤ 0，组合分方差为零")
    return float(w @ sf) / np.sqrt(denom2)


def optimal_weights(R: np.ndarray, sf: np.ndarray) -> np.ndarray:
    """最大化 `ρ(w)` 的权重：`w ∝ R⁻¹ Σ_f`，再归一到 `wᵀRw = 1`。

    归一不可省：`ρ(w)` 对**正倍数**不变，权重的绝对大小毫无意义，
    留着会让下游「权重之和应为 1」之类的检查误报。

    R 奇异（因子共线）时用 `lstsq` 求最小范数解而不是 `inv`——
    `inv` 会直接抛 `LinAlgError`，而共线在因子数据里是**常态**
    （`mom20` 与 `mom60`、`turn20` 与 `turnratio` 高度相关）。
    """
    R = np.asarray(R, dtype=float)
    sf = np.asarray(sf, dtype=float)
    try:
        w = np.linalg.solve(R, sf)
    except np.linalg.LinAlgError:
        w = np.linalg.lstsq(R, sf, rcond=None)[0]
    n2 = float(w @ R @ w)
    if not np.isfinite(n2) or n2 <= ZERO_VAR_EPS:
        raise DegenerateFactor("R 奇异到求不出非零权重")
    return w / np.sqrt(n2)


# ── 滚动扩展窗：从**历史日**求权重，用在**未来日** ────────────────

def r_column(fa: str, fb: str) -> str:
    """R 的列名 —— **带因子名**，因为 R 的格子必须**自解释**。

    ## 为什么不是 `R__{a}{b}`

    2026-10-14 的真实事故：抽取表按 `FACTOR_NAMES` 的**位置**存
    `R__02`（= mom20 × vol20），而 `unpack` 按**调用方给的顺序**读。
    传 `--factors mom20,rev5,vsur` 时，`vsur` 被当成第 2 位，
    于是整块 R **平移错标**，且**一个错都不报**——
    `R__00`…`R__22` 一列都不缺，缺列检查只证明了「列在」。

    更要命的是**结构不变量抓不到它**：R 无论怎么错标都仍然
    对称、对角线仍然全 1。所以「对称 + 单位对角」这类校验在这里
    **恒真**——是安慰剂，不是守卫。

    > **能防住错标的只有标签本身。** 把因子名写进列名，
    > 文件就自解释，谁打乱顺序都伤不到它。
    """
    return f"R__{fa}__{fb}"


def unpack(df, factors):
    """把长表拆成 `(日期数组, R 数组 n×k×k, Σ_f 数组 n×k)`。

    列名约定见 `r_column`：R 按**因子名**存，Σ_f 按因子名存。
    因此本函数对**因子顺序**天然不敏感——
    顺序只是书写，不是语义。
    """
    factors = list(factors)
    k = len(factors)

    # 旧格式（按位置存）必须**响亮地拒绝**。
    # 若照旧读，缺列检查会发现列都在，于是放行一份**错标**的 R。
    legacy = [c for c in df.columns if re.fullmatch(r"R__\d\d", str(c))]
    if legacy:
        raise ValueError(
            f"抽取表是**旧的位置格式**（{len(legacy)} 个 `R__ab` 列，"
            f"如 {legacy[:3]}）。本格式的 a、b 是**抽取时**的因子位置，"
            f"与调用方顺序无关——照旧读会**静默错标**，不报错。\n"
            f"必须重跑抽取："
            f"`python scripts/extract_ic_matrices.py --out <新路径>`"
        )

    Rcols = [r_column(a, b) for a in factors for b in factors]
    missing = [c for c in Rcols + [f"sf__{f}" for f in factors]
               if c not in df.columns]
    if missing:
        raise KeyError(f"抽取表缺列：{missing[:5]}"
                       f"（共 {len(missing)} 个）——"
                       f"多半是重跑时因子清单变了")
    Rs = df[Rcols].to_numpy(float).reshape(len(df), k, k)
    sf = df[[f"sf__{f}" for f in factors]].to_numpy(float)
    return df["date"].to_numpy(), Rs, sf


def train_weights(hist: dict, factors) -> np.ndarray:
    """**只用历史日**求权重：先按天平均 `R` 与 `Σ_f`，再求最优解。

    为什么先平均再解，而不是解了再平均：
    `Σ_f` 的符号正是因子「当前是否有效」的信息——
    先平均等于让**长期稳定的那部分**决定权重，
    逐日求解则会让权重跟着单日噪声乱跳。

    ## 前视防护

    调用方**必须**只传历史日；本函数无法自己判断。
    `walk_forward` 是唯一该用的入口——它按年份切片，
    `test_walk_forward_never_lets_a_year_see_its_own_future` 钉住这一点。
    """
    _, Rs, sf = hist["date"], hist["R"], hist["sf"]
    if len(Rs) == 0:
        raise DegenerateFactor("历史为空，无法定权")
    return optimal_weights(Rs.mean(axis=0), sf.mean(axis=0))


def daily_ic_series(Rs: np.ndarray, sf: np.ndarray, w: np.ndarray) -> np.ndarray:
    """把固定的 `w` 作用在每一天的 `(R_t, Σ_f,t)` 上，返回逐日组合 IC。

    哪天退化（零方差等）就**跳过那天**，不许填 0——
    填 0 会被当成「那天信号完全失效」，而实际含义是「那天没测出来」。
    跳过的日子由调用方按 `np.isfinite` 自行排除。
    """
    out = np.full(len(Rs), np.nan)
    for t in range(len(Rs)):
        try:
            out[t] = composite_ic(w, Rs[t], sf[t])
        except (DegenerateFactor, FloatingPointError):
            continue
    return out


def sign_weights(sf_hist: np.ndarray) -> np.ndarray:
    """**朴素翻号**：只按历史平均 IC 的**符号**定向，一个系数都不学。

    这是石川 7.4 那句「等权往往就是很好的选择」的**最强版本**——
    原话谈的是「偏离等权」，而这条臂连等权都不肯给，
    它只肯给**方向**。它因此是这条路线的真正及格线：
    若连它都够不到门槛，学系数再精也是白费。

    > 它比 `optimal_weights` 弱**是定义决定的**：符号臂只学方向，
    > 最优臂还学幅度，所以**在训练期上**前者必然不占优。
    > 钉住这一点，是为了防止有人把「符号臂样本外更差」
    > 讲成「符号臂哪里有毛病」——它差是应该的，**差多少**才是信息。

    历史全 0 的因子没有方向可言，取 +1（中性），并让 `composite_ic`
    的分母守卫去发现「这个方向其实是零方差」。
    """
    mu = np.asarray(sf_hist, dtype=float).mean(axis=0)
    w = np.sign(mu)
    w[w == 0.0] = 1.0
    # 归一到单位欧氏长度，让它和 `optimal_weights` 的权重**可比**。
    # IC 本身对整体缩放不敏感（分子分母各一次方），所以这纯粹为了好看。
    return w / np.sqrt(float(w @ w))


def walk_forward(df, factors, need_ic: float, judged_from: int = 2021,
                 equal_weight_years: tuple[int, ...] = (2019, 2020),
                 all_years_equal: bool = False,
                 weight_rule: str = "optimal"):
    """滚动扩展窗逐年判决。**判决规则在 `docs/` 里预注册。**

    | 年 | 权重来源 |
    |---|---|
    | `equal_weight_years`（或 `all_years_equal`） | **等权**（真实起点没有历史可用） |
    | 其余 | **只用该年之前**的日定权 |

    `judged_from` 之前的年**只报不判**——
    用它们判决等于承认「你事先就知道它们的 IC」。

    ## `all_years_equal` 是**对照臂**，不是主判决

    石川《因子投资方法与实践》7.4（行 14500）原话：

        理论上，变量的权重应和它们预测未来收益率的能力成正比。
        **然而在实践中，等权重配置所有变量往往就是很好的选择。**

    7.5 更直接：「金融领域数据信噪比极低，时序上可利用的相关性非常有限」。

    而 `R⁻¹Σ_f` **正是偏离等权**（按预测能力加权）。
    这个警告与本项目的主张**方向相反**，所以必须并列跑一遍，
    让数据回答「书里这条在 A 股成不成立」——
    而不是**因为读到了这句话就改主判决**（那是第 63 条）。

    返回逐年记录。每条含 `judged`（是否计入判决）与 `pass_`。
    """
    dates, Rs, sf = unpack(df, factors)
    yrs = np.array([int(str(d)[:4]) for d in dates])
    k = len(factors)

    out = []
    for y in sorted(set(yrs.tolist())):
        mask = yrs == y
        before = yrs < y
        no_hist = {"year": y, "n_days": int(mask.sum()),
                   "ic_mean": None, "reason": "无历史"}

        if all_years_equal or weight_rule == "equal" or y in equal_weight_years:
            w = np.full(k, 1.0 / np.sqrt(k))
            src = "equal"
        else:
            if weight_rule not in ("optimal", "sign", "equal"):
                raise ValueError(
                    f"未知权重规则 {weight_rule!r}：只认 'optimal' / 'sign'"
                    "（等权另由 equal_weight_years / all_years_equal 指定，"
                    "写成 weight_rule='equal' 亦可）。"
                    "**不许回落**——回落就是换个名字的静默失效。")
            if not before.any():
                out.append(no_hist)
                continue
            hist_sf = sf[before]
            if weight_rule == "sign":
                # 符号臂也只用当年之前——**符号同样可以前视**。
                # 它「看起来不像会前视」恰恰是最容易写错的地方。
                w = sign_weights(hist_sf)
                src = "sign"
            else:
                try:
                    w = train_weights({"date": dates[before], "R": Rs[before],
                                       "sf": hist_sf}, factors)
                except DegenerateFactor as e:
                    out.append({"year": y, "n_days": int(mask.sum()),
                                "ic_mean": None, "reason": str(e)})
                    continue
                src = "walk_forward"

        ic = daily_ic_series(Rs[mask], sf[mask], w)
        ic = ic[np.isfinite(ic)]
        out.append({
            "year": y, "n_days": int(mask.sum()), "n_used": int(len(ic)),
            "weights_source": src,
            "ic_mean": float(ic.mean()) if len(ic) else None,
            "t": float(ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic)))
            if len(ic) > 2 and ic.std(ddof=1) > 0 else None,
            "judged": bool(y >= judged_from and len(ic)),
            "pass_": bool(y >= judged_from and len(ic)
                          and ic.mean() >= need_ic),
            "weights": {f: float(x) for f, x in zip(factors, w)},
        })
    return out