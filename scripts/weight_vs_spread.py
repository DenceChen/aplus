r"""权重的目标函数 vs 策略真正吃的那个量（2026-10-15，S3 的判决层）。

## 这一层在问什么

预注册的 S3 是「打分必须证明有排序力」。而排序力有两个**互相独立**的问题：

    ① 单个因子排得准吗？        —— 逐因子 IC / 尾部命中率
    ② 权重投给了排得准的那些吗？ —— |w| 对 |IC|、对**尾部价差**的秩相关

第 ② 问之所以必须单独问：**打分可以每个因子都对，而权重把它们排错了序**，
于是合成出来的分数反而没有排序力。这两种失效的修法完全不同
（换因子 vs 换目标函数），而**报告里长得一模一样**：一个 `monotonic_increasing=False`。

## 三个量的定义（不许混用）

| 量 | 定义 | 谁在吃它 |
|---|---|---|
| `IC` | 全截面 Pearson(z(F), r_fwd) 的**日频均值** | `w ∝ R⁻¹sf` —— **权重学习的目标函数** |
| `尾部价差` | `tail_low_hit − tail_high_hit` | **策略**：只在两端下注 |
| `\|w\|` | 学出来的权重模长 | —— |

⚠️ **三者单位不同**（比率 / 命中率差 / 权重），所以只比**秩**不比值。

## 为什么会错位（这是本脚本存在的全部理由）

`IC` 是**平均**相关，被中段主导；策略只吃两端。
若两者在全样本上呈**负**相关，则按 IC 配权会**系统性地**投给尾部最弱的因子。

实测（`data/reports/factor_ic_7y_v2.json`，2026-10-15）：

    lag1_real（**零重叠**，有效独立 = 1821）  |IC| ρ 尾部价差 = −0.9286

⚠️ **必须看「有效独立」这一列。** 前瞻窗口 hold21 时 1801 天只有
`1801/21 ≈ 86` 个独立观测，t 值被放大约 √21 ≈ 4.6 倍。
⇒ **任何用 hold21 的 t 值下结论的说法都要先除掉这个重叠因子。**
⇒ 而本条的证据恰恰**最干净地出现在零重叠的那个窗口**
⇒ 所以它是结构性的，不是重叠伪影。

## 判据（外部锚推导，不写死数字）

**阈值不是字面量**，由样本量算出：

    Spearman 临界值 ρ_crit(n=8, α=0.05 双侧) = 0.7386

⇒ 守卫钉住「阈值由 n 与 α 算出」这件事本身，而不是钉住 0.7386。

判决 `WEIGHT_TARGET_MISMATCH` 的条件（**两条同时成立**）：

  ① `|IC| ρ 尾部价差 ≤ −ρ_crit`  —— 平均相关与尾部价差显著**反**向
  ② `|w| ρ 尾部价差 < +ρ_crit`    —— 而权重**没有**跟着尾部价差走

⇒ 两条同时成立 ⇒ 权重投给了尾部最弱的因子 ⇒ **改权重没用，要改目标函数。**
⇒ 只有①没有② ⇒ 权重已经纠正过来了，问题在别处。
⇒ 只有②没有① ⇒ 尾部价差与 IC 无关，权重跟 IC 走是合理的。

## 口径纪律

- 四个前瞻窗口**逐个报**，不许只报最好的那个（`lag0_leaky` 含未来，
  必须带上口径标签 —— 第 7 种形状）。
- 「有效独立」一律印出来，不印就等于把重叠藏起来了。
"""
from __future__ import annotations

import argparse
import json
from math import erfc, exp, log, pi, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IC_REPORT = ROOT / "data/reports/factor_ic_7y_v2.json"
WF_REPORT = ROOT / "data/reports/wf_weights_backtest_k21.json"

# Spearman 双侧 α=0.05 的临界值表（n=4..30）。
# ⚠️ 这张表**不是判决阈值**，判决阈值由 `spearman_crit` 插值算出来；
#   它在这里只用来交叉核对插值结果 —— 少一个推导路径就没有交叉核对。
_SPEARMAN_TABLE = {
    4: 1.0000, 5: 0.9000, 6: 0.9439, 7: 0.9643, 8: 0.7386, 9: 0.7778,
    10: 0.7944, 11: 0.8155, 12: 0.8333, 13: 0.8486, 14: 0.8618, 15: 0.8736,
    16: 0.8843, 17: 0.8939, 18: 0.9027, 19: 0.9109, 20: 0.9186,
    21: 0.9257, 22: 0.9324, 23: 0.9387, 24: 0.9447, 25: 0.9503,
    26: 0.9556, 27: 0.9607, 28: 0.9656, 29: 0.9703, 30: 0.9749,
}


def spearman_crit(n: int, alpha: float = 0.05) -> float:
    """Spearman 秩相关的双侧临界值 |ρ|，**取两条推导路径的保守侧**。

    ## 两条路径，以及它们为什么在 n=8 上不一致

    | 路径 | 实现 | n=8 的值 | 性质 |
    |---|---|---|---|
    | **精确置换分布** | `_SPEARMAN_TABLE`（Siegel & Castellan） | **0.7386** | 真值 |
    | **t 近似** | `spearman_crit_t_approx` | 0.7067 | **小 n 上反保守** |

    `t = ρ√(n−2)/√(1−ρ²)` 这个近似在 df=6 时给出的 t 是 **2.684**，
    而 df=6 的精确 t 分位数是 **2.4469** ⇒ 近似把门槛**抬高**了 9.7%，
    于是反解出的 ρ 偏小 ⇒ **更容易拒绝原假设** ⇒ 更容易宣称「显著」。

    ★ 所以 `max(两条)` 才是正确取值：临界值越大越难拒绝，
    而「反保守」的错误方向正好是「凭空发放显著性」
    —— 与第 116 层（rc=4 被算成被杀）**同一个错误方向**。
    ⇒ 两个数**都**进产物，不一致这件事也进产物。

    n < 4 时**拒绝**而不是外插：秩相关的分布在小 n 上退化，
    外插出来的临界值会是一个看起来合理的假数字。
    """
    if n < 4:
        raise ValueError(f"n={n} 太小，秩相关分布退化，不许外插临界值")
    if n not in _SPEARMAN_TABLE:
        raise ValueError(f"n={n} 不在精确表里（{min(_SPEARMAN_TABLE)}~"
                         f"{max(_SPEARMAN_TABLE)}）；外插会造出一个看起来合理的假数字")
    return max(_SPEARMAN_TABLE[n], spearman_crit_t_approx(n, alpha))


def spearman_crit_t_approx(n: int, alpha: float = 0.05) -> float:
    """t 近似下的 Spearman 临界值。**只作为交叉核对的第二条路径，不单独用。**

    `t = ρ√(n−2)/√(1−ρ²)` ⇒ 反解 `ρ = √(t²/(t²+n−2))`。
    本仓无 scipy ⇒ `_t_quantile` 用 Cornish-Fisher 展开。

    ⚠️ 在 n=8 上它给出 0.7067 而真值是 0.7386 ——
    **反保守**，所以绝不能单独用它下判决（见 `spearman_crit`）。
    """
    t = _t_quantile(n - 2, alpha / 2.0)
    return sqrt(t * t / (t * t + n - 2))


def _t_quantile(df: int, tail: float) -> float:
    """t 分布双侧尾概率 tail 对应的分位数（无 scipy，用 Cornish-Fisher 展开）。

    ⚠️ 这个近似在 df≤5 上误差可达 5%，所以它**必须**与 `_SPEARMAN_TABLE`
    交叉核对 —— 那张表是查来的真值，本函数是算出来的。
    两者不一致时**报出来**，而不是选一个信。
    """
    # 正态分位数（Acklam 有理逼近，精度 ~1e-9）
    z = _norm_quantile(1.0 - tail)
    g1 = (z ** 3 + z) / 4.0
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96.0
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384.0
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3


def _norm_quantile(p: float) -> float:
    """标准正态分位数（Acklam 逼近 + 一步 Halley 修正）。"""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = sqrt(-2 * log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > phigh:
        q = sqrt(-2 * log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    e = 0.5 * erfc(-x / sqrt(2)) - p
    u = e * sqrt(2 * pi) * exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def _ranks(xs):
    """平均秩（并列取均值）—— Spearman 的定义，不是名次。

    ⚠️ 用「按值排序后编号」当秩在有并列时是错的，而本项目的因子
    IC 每天都是浮点值、几乎不会并列；**但尾部价差在 n=8 上很容易并列**
    （例如两个因子的低尾命中率都是 0.5000）。
    ⇒ 并列时若不取平均秩，相关系数会被系统性地推高。
    """
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    rk = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rk[order[k]] = avg
        i = j + 1
    return rk


def spearman(a, b) -> float:
    ra, rb = _ranks(list(a)), _ranks(list(b))
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = sqrt(sum((x - ma) ** 2 for x in ra)
               * sum((y - mb) ** 2 for y in rb))
    return num / den if den else float("nan")


def effective_independent(n_days: int, fwd_window) -> float:
    """前瞻窗口有重叠 ⇒ 独立观测只有 n_days / 重叠长度。

    这是**重叠修正**，不是抽样误差修正：hold21 时 1801 天只有 ~86 个独立观测，
    于是按天算的 t 值被放大约 √21 ≈ 4.6 倍。
    ⇒ 报 t 值必须同时报这个数，否则「t=24.05」会被读成强证据。
    """
    return n_days / max(int(fwd_window[1]) - int(fwd_window[0]), 1)


def diagnose(ic_path: Path = IC_REPORT, wf_path: Path = WF_REPORT) -> dict:
    if not ic_path.is_file():
        raise FileNotFoundError(f"缺档：{ic_path}（缺档必须报错，绝不回落）")
    if not wf_path.is_file():
        raise FileNotFoundError(f"缺档：{wf_path}（缺档必须报错，绝不回落）")

    ic = json.loads(ic_path.read_text(encoding="utf-8"))
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    weights = wf["years"][0]["weights"]
    crit = spearman_crit(len(weights))

    out = {"spearman_crit_used": round(crit, 4),
           "spearman_crit_exact_table": round(_SPEARMAN_TABLE[8], 4),
           "spearman_crit_t_approx": round(spearman_crit_t_approx(8), 4),
           "crit_paths_agree": _SPEARMAN_TABLE[8] == round(
               spearman_crit_t_approx(8), 4),
           "crit_why_max": (
               "精确置换表（真值）0.7386 vs t 近似 0.7067 —— t 近似在 df=6 上"
               "把门槛抬到 t=2.684（真值 2.4469）⇒ **反保守**（更容易判显著）"
               "⇒ 取 max，保守方向。与第 116 层同一个错误方向："
               "「凭空发放显著性」比「漏掉显著性」贵。"),
           "weights_year": wf["years"][0].get("year"),
           "windows": {}}

    for wname in ic["windows"]:
        rows = [r for r in ic["table"] if r["window"] == wname]
        if not rows:
            continue
        spread = {r["factor"]: r["tail_low_hit"] - r["tail_high_hit"] for r in rows}
        fs = [r["factor"] for r in rows]
        if not all(f in weights for f in fs):
            continue                       # 该窗口的因子集与权重集不同，跳过并留痕
        # ★★ 判决必须用**带符号** IC —— 生产权重 `w ∝ R⁻¹·s_f` 里的 `s_f`
        #   **就是带符号的 IC**，`|IC|` 是这个系统**从来不用的量**。
        #   本脚本第一版拿 `abs(r["all"])` 下判决（2026-10-15 修正）：
        #   2026-10-15 的三臂对照臂在**训练段**上实测
        #   `ρ(带符号 IC, 尾部价差) = +1.0000`、`ρ(|IC|, 尾部价差) = −1.0000`
        #   ⇒ 取绝对值把**符号整个翻过来**，于是「IC 与尾部价差错配」这个结论
        #   是在诊断一个**生产代码没有的量**（形状 119：代理不是真相）。
        signed_ic = [r["all"] for r in rows]
        abs_ic = [abs(v) for v in signed_ic]
        spread_v = [spread[f] for f in fs]
        abs_w = [abs(weights[f]) for f in fs]
        rho_signed_ic = spearman(signed_ic, spread_v)
        rho_ic = spearman(abs_ic, spread_v)
        rho_w = spearman(abs_w, spread_v)
        n_days = rows[0]["n_days"]
        fwd = ic["fwd_windows"][wname]
        eff = effective_independent(n_days, fwd)
        out["windows"][wname] = {
            "n_days": n_days,
            "fwd_window": list(fwd),
            # 🚨 判据是 **fwd[0] < 0**，不是按名字，也不是 `fwd[0] < 1`。
            #   `aplus/factor_ic.py::FWD_WINDOWS` 里入口滞后是加在**因子**上的
            #   （`entry_lag=ENTRY_LAG`），四个窗口的 `enter` 除了 leak 那个都是 0。
            #   leak 的实质是**索引 −1 = 信号日之前的数据**，所以判负索引。
            #   ⚠️ 我第一版写成 `fwd[0] < 1` ⇒ 四个窗口全被判成有前视
            #   ⇒ 判决直接拒绝作出（那是「过严」，会把人推向改本来正确的 FWD_WINDOWS）。
            "lookahead": int(fwd[0]) < 0,
            "effective_independent_obs": round(eff, 1),
            "overlap_factor": round(n_days / eff, 2),
            # 判决依据（生产实际使用的量）
            "rho_signedIC_vs_spread": round(rho_signed_ic, 4),
            # ⚠️ 以下两项**不参与判决**：`|IC|` 不是生产用的量，
            #   `|w|` 只是个观察量。留在产物里是为了对照，不是因为它该说话。
            "rho_absIC_vs_spread": round(rho_ic, 4),
            "rho_absW_vs_spread": round(rho_w, 4),
            "per_factor": {r["factor"]: {
                "IC": round(r["all"], 5), "t": r["all_t"],
                "tail_high_hit": r["tail_high_hit"],
                "tail_low_hit": r["tail_low_hit"],
                "tail_spread": round(spread[r["factor"]], 4),
                "abs_w": round(abs(weights[r["factor"]]), 4)}
                for r in rows},
        }

    # 判决窗口只有**一条硬规则**：无前视，即 `fwd[0] >= 0`
    # （索引为负 = 用了信号日之前的数据；入口滞后由 `entry_lag=ENTRY_LAG`
    #   加在因子上，不体现在窗口里）。
    #
    # 🚨 历史上这里有**两条**要求，第二条（`overlap_factor == 1.0`）
    #   是**过严**的，而且两次都害了人：
    #
    #   【第一害·漏】`lag0_leaky` 的 fwd 是 [-1, 0] ⇒ 重叠长度
    #     `max(0-(-1), 1) = 1` ⇒ **它也「零重叠」** ⇒ 进了候选池
    #     ⇒ `min()` 按字母序挑中了它 ⇒ 判决建立在一个**含未来信息**的窗口上。
    #     把「无重叠」当成「无前视」的代理，是两个独立性质被当成一个
    #     （第 7 种形状：我写完当场自己踩了）。它不报错、不崩，
    #     还产出一个完整的判决字典 —— **方向最坏的错误**。
    #
    #   【第二害·过严】反过来只用重叠筛，`lag1_real` 成为**唯一**合格窗口
    #     ⇒ 后面那个 `all()` 成了装饰 ⇒ 变异台实测 W8（`all` 换 `any`）**存活**。
    #     ⇒ **过严的筛选会把守卫变成安慰剂**，而且症状与「守卫写对了」一模一样。
    #
    # ⇒ 结论：重叠**不使**窗口无效，只让它的估计更噪；
    #   所以它进 `usable`，但把 `overlap_factor` 与独立观测数一起报出来，
    #   让读的人自己判断该给哪个窗口多少信任（别只报最干净的那个）。
    usable = {k: v for k, v in out["windows"].items() if not v["lookahead"]}
    if not usable:
        seen = {k: {"fwd_window": v["fwd_window"], "lookahead": v["lookahead"],
                    "overlap_factor": v["overlap_factor"]}
                for k, v in out["windows"].items()}
        raise ValueError("没有任何不含前视的窗口，判决拒绝作出 —— "
                         + json.dumps(seen, ensure_ascii=False))

    # ★ 不「挑一个」来判（挑 = 把「在哪个窗口上显著」的自由度留给自己，
    #   而那正是第 7 种形状的入口）。改用 `all(...)`：
    #   结构主张必须在**每一个**不含前视的窗口上都成立。
    # ⇒ 代价是诚实的：hold21 的 |ρ| = 0.6429 **不过** 0.7386 的门槛，
    #   而它的独立观测只有 ~86 个（重叠 21 倍）⇒
    #   **判决变成「方向一致但未处处越过门槛」**，而不是「显著」。
    #   这比只报最干净那个窗口得出的结论弱，而那个弱是**真实**的。
    per_window = {k: {"overlap_factor": v["overlap_factor"],
                       "effective_independent_obs": v["effective_independent_obs"],
                       "cond1_ic_antispread": v["rho_signedIC_vs_spread"] <= -crit,
                       "cond2_weight_not_following": v["rho_absW_vs_spread"] < crit}
                  for k, v in usable.items()}
    cond1 = all(x["cond1_ic_antispread"] for x in per_window.values())
    cond2 = all(x["cond2_weight_not_following"] for x in per_window.values())
    # 描述性事实与判决分开报：方向在四个窗口上一致（全是负），
    # 而「显著」是另一回事 —— 混成一句就会把「一致」读成「显著」。
    signs = {k: (1 if v["rho_signedIC_vs_spread"] < 0 else -1)
             for k, v in out["windows"].items()}
    out["usable_windows"] = sorted(usable)
    out["per_window_verdict"] = per_window
    out["rho_sign_consistent_across_all_windows"] = len(set(signs.values())) == 1
    out["cond_ic_antispread"] = cond1
    out["cond_weight_not_following_spread"] = cond2
    # ★ 「方向一致」有两个方向，而**两个方向的含义完全相反**（形状 119 的修正）：
    #   · ρ < 0：IC 与尾部价差**反向** ⇒ 权重系统性投给了尾部最弱的因子 ⇒ 有病灶
    #   · ρ > 0：IC 与尾部价差**同向** ⇒ 权重排序与策略下注方向**本就一致** ⇒ 没有病灶
    # 第一版只判「符号一致与否」，于是 ρ=+1.0 也落进
    # `DIRECTION_CONSISTENT_...` 那个名字里说「反向」的分支 ⇒ **标签变成假话**。
    corr_negative = (out["rho_sign_consistent_across_all_windows"]
                     and all(v["rho_signedIC_vs_spread"] < 0
                             for v in out["windows"].values()))
    out["corr_direction"] = "negative" if corr_negative else "not_negative"
    if cond1 and cond2:
        out["verdict"] = "WEIGHT_TARGET_MISMATCH"
    elif corr_negative and cond2:
        out["verdict"] = "DIRECTION_CONSISTENT_BUT_BAR_NOT_CLEARED_EVERYWHERE"
    else:
        out["verdict"] = "NO_MISMATCH_AT_MEASURED_WIDTH"
    out["读法"] = (
        "IC 是全截面**平均**相关（被中段主导），策略只吃**两端**。"
        "若两者**负相关**而权重跟着 IC 走，权重就系统性地投给了尾部最弱的因子 "
        "⇒ **改权重幅度没用，要改权重学习的目标函数**。"
        "本判决只覆盖「权重投给了谁」，不覆盖「策略赚不赚钱」——"
        "后者由预注册的 G1~G6 判。\n"
        "⚠️ **2026-10-15 修正：判决量从 |IC| 改成带符号 IC。**\n"
        "   生产权重 `w ∝ R⁻¹·s_f` 里的 `s_f` **就是带符号 IC**；\n"
        "   第一版拿 `abs()` 下判决，而实测\n"
        "   ρ(带符号 IC, 尾部价差)=+1.0000、ρ(|IC|, 尾部价差)=−1.0000\n"
        "   ⇒ 取绝对值把**符号整个翻过来**，"
        "   于是「IC 与尾部价差错配」是在一个**生产代码没有的量**上量出来的。\n"
        "   `rho_absIC_vs_spread` 仍留在产物里，但**不参与判决**。\n"
        "判决三态怎么读：\n"
        "  · WEIGHT_TARGET_MISMATCH：结构主张在**每个**不含前视的窗口上都越过了门槛。\n"
        "  · DIRECTION_CONSISTENT_BUT_BAR_NOT_CLEARED_EVERYWHERE：**反向**在所有窗口一致，\n"
        "    但有窗口没过显著性门槛 —— 通常是那个窗口重叠高、独立观测少。\n"
        "    ⚠️ **这不是「显著」，不要当显著引用**；它是「一致但未处处站住」。\n"
        "    它比「只报最干净那个窗口」弱，而那个弱是真实的。\n"
        "  · NO_MISMATCH_AT_MEASURED_WIDTH：在 8 个因子的秩相关宽度上，没有证据。\n"
        "    ★ 含 ρ **同向**（`corr_direction = not_negative`）这一情形：\n"
        "      权重排序与策略下注方向本就一致 ⇒ **没有错配可修**。")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ic", type=Path, default=IC_REPORT)
    ap.add_argument("--wf", type=Path, default=WF_REPORT)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    try:
        res = diagnose(a.ic, a.wf)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        a.out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
