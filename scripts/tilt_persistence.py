"""度量「风格倾斜的持续性」—— 石川行 2792 那一问。

## 为什么是这件事

`docs/TODO_measure_sigma_r.md` 的 α/β 分解已定案：预注册的「α > 0」
判据**不通过**（6 年里 2 年为正）。但分解同时识别出一条不依赖回归、
不依赖 R² 的事实 —— 组合长期压在**低波动、低换手**那一侧。

剩下的问题只有一个，且是二分的：

* 同一组倾斜**年年重复** ⇒ 稳定的风格，可据此显式构建/约束
  （石川行 15562 的「因子暴露约束」）；
* 倾斜只是这个样本期的**偶然** ⇒ 策略本质仍是运气。

## 判据出自书的哪一行

石川行 2792-2795：

    「使得个股的因子暴露取值在时序上并不稳定。一旦因子暴露在时序上不稳定，
      就会导致在不同期进行截面回归时，该因子的表现就像随机因子一样，
      因而难以获得显著的溢价。」

⇒ 判据不是「暴露有没有」（已经有了），而是「暴露**在时序上稳不稳**」。
本脚本把它拆成两个可机械执行的量：**符号一致率** 与 **跨块自相关**。

## 三条纪律

1. **只读不算。** 输入是回测脚本落盘的逐日 parquet，
   暴露由那一侧产生时横截面还在手上；事后重算要再加载 GB 级面板，
   而并发面板作业就是本项目 OOM 的唯一来源。**重算就是造第二台测量仪。**
2. **判据预注册在 `docs/TODO_tilt_persistence.md`，读数之前冻结。**
   本脚本里的阈值一律从那个文档搬过来，**不在这里改**。
3. **口径标签跟着数字离开脚本。** 见 `CALIBER`。

## 观测单元：调仓块，不是交易日

K=63 一个块内**持仓不变**，而暴露是逐日用当日横截面重算的
（`backtest_walk_forward_weights.py::_up_exposure`），
所以块内的逐日变动**只反映横截面自身漂移**，不是策略在改主意。
拿交易日当独立观测会把样本量虚增 K 倍。
项目里 `t_blocked` 就是为同一件事存在的。

块边界用**位置取模** `i % K == 0` 重建，与 `aplus/rebalance.py`
的 `is_rebalance_day`（按位置判定）同一约定。
本 parquet 未丢任何交易日（缺席的 110 天全部落在春节/国庆等假期窗口内），
所以位置取模与真实调仓轴**逐位对齐**。

用法：
    .venv/bin/python scripts/tilt_persistence.py \\
        --daily data/reports/wf_bt_k63.daily.parquet --k 63
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.factor import FACTOR_NAMES

#: 退化序列的变异系数阈值 —— **与 `aplus/evolve_race._tstat` 同一把尺子**。
#: 不用 `std == 0`：`np.full(30, 0.3).std()` 是 5.55e-17 而非 0，
#: 那个判据永远不成立，会把常量序列读成自相关 +1。
DEGENERATE_CV = 1e-12

EXPO_PREFIX = "expo_"

#: 口径标签 —— **必须跟着数字离开脚本**（项目第 7 条）。
#: 本项目曾因核对脚本乘错 100 倍，凭空造出一个假的「口径矛盾」。
CALIBER = {
    "exposure_unit": "robust_z（全横截面标准化；0 = 与市场中性，非 0 = 倾斜）",
    "leg": "只算涨腿（多头）；空头腿在 A 股不可执行，不参与",
    "blocks": "不重叠调仓块，块长 = K，与 aplus/rebalance.py::is_rebalance_day 同一位置约定",
    "yearly": "年度倾斜 = 该年全部交易日暴露的算术均值（等权，非块加权）",
    "days_per_year": 244,
    "annualization_note": "本脚本不产出年化收益；244 仅在此声明，与全仓判决同口径",
}

#: 判据阈值 —— **全部预注册于 docs/TODO_tilt_persistence.md §3，读数前冻结**。
#: 这里的数字不得为了迁就结果而修改。
PREREG = {
    "agree_stable": 4,        # ≥ 4/6 年与全样本方向同号
    "agree_drift": 2,         # ≤ 2/6 年同号（反向一致）
    "ac1_stable": 0.0,        # AC1 > 0 判稳定
    "ac1_drift": 0.0,         # AC1 < 0 判漂移
    "min_factors_for_verdict": 6,   # 8 个因子里要有几个同时满足两侧条件
    "flip_rate_discount": 1.0 / 3,  # 符号翻转率 > 1/3 ⇒ 该因子 agree 不作数
    "n_shuffle": 1000,
    "p_value_cap": 0.05,      # 经验 p 必须小于它才算「超过运气」
}


# ── 基础量 ────────────────────────────────────────────────────────────

def factor_columns(cols) -> list[str]:
    """列名 → 因子名（`expo_ivol` → `ivol`），保持 `FACTOR_NAMES` 的顺序。

    用 `FACTOR_NAMES` 过滤而不是「凡带前缀的都算」：
    多一列无关的 `expo_*` 就会被当成因子混进判决，
    而报告照样出数、没有任何异常。
    """
    want = {f"{EXPO_PREFIX}{f}" for f in FACTOR_NAMES}
    got = [c for c in cols if c in want]
    missing = want - set(got)
    if missing:
        # 缺列必须响亮拒绝。回落成「有的算有的不算」会让
        # 「8 个因子里 6 个稳定」变成「6 个稳定 0 个漂移」的假象。
        raise SystemExit(f"缺暴露列: {sorted(missing)}")
    return [c[len(EXPO_PREFIX):] for c in got]


def to_yearly(df: pd.DataFrame, factors: list[str]) -> dict:
    """逐日表 → `{年: {因子: 均值}}`。年度一律**交易日等权**。

    为什么不用块加权：K=63 下 2026 只有 1~2 个块
    （`k_sweep_extract.py` 的注释记着同一件事），
    块加权会让 2026 与 2021 **不可比**，而报告不会为此报警。
    """
    years = pd.to_datetime(df.index).year
    out: dict = {}
    for y in sorted(set(years)):
        sel = years == y
        out[int(y)] = {f: float(np.nanmean(df[f"{EXPO_PREFIX}{f}"].to_numpy(float)[sel]))
                       for f in factors}
    return out


def to_blocks(values: np.ndarray, k: int) -> np.ndarray:
    """不重叠块均值；**末块不足 K 天也保留**（真实调仓也是这么发生的）。

    删末块等于「假装最后一个调仓没发生」，而它恰恰是最新的那块 ——
    删掉等于用「未来不存在」来提高历史的一致性。
    """
    v = np.asarray(values, float)
    n_full = len(v) // k
    if n_full < 1:
        raise SystemExit(f"完整块数 {n_full} < 1，算不出 AC1；K={k} 相对样本太粗")
    # ⚠️ 不能写成 `v[:ceil(n/k)*k].reshape(...)`：末尾不足 K 天时，
    # 那个长度**大于** v，`reshape` 当场 ValueError。
    # 而若改成补零/补 NaN 凑整，末块均值就会被填充值污染 ——
    # 报错是这里唯一正确的行为，凑整才是静默失效。
    blocks = [b.mean() for b in v[: n_full * k].reshape(n_full, k)]
    tail = v[n_full * k:]
    if len(tail):
        blocks.append(float(np.nanmean(tail)))     # 末块不足 K 天，保留（见 docstring）
    out = np.asarray(blocks, float)
    if len(out) < 2:
        raise SystemExit(f"块数 {len(out)} < 2，算不出 AC1；K={k} 相对样本太粗")
    return out


def ac1(x: np.ndarray) -> float:
    """lag-1 自相关。**长度 < 3 或退化返回 NaN**。

    ## 退化判据为什么不能写 `std == 0`（本项目已栽过一次的同一个坑）

    `np.full(30, 0.3).std()` = **5.551115123125783e-17**，不是 0 ——
    浮点累加误差。于是 `std == 0` **永远不成立**，
    `corrcoef` 对近乎常量的序列照常返回 **+1.0**，
    即「完美持续」—— 而事实是**完全没有持续性可言**。

    这与 `aplus/evolve_race.py::_tstat` 修过的第 2 个真实缺陷同形：
    那里是 `t = 0.02/(3.5e-18/√30) = 3.1e16` 的凭空巨值，
    这里是凭空的自相关 `+1`。**失效方向一致：把噪声读成信号。**

    ⇒ 判据用**变异系数**（与项目同一把尺子，`DEGENERATE_CV`）：
    `sd` 相对数据自身尺度可忽略 ⇒ 归一化只剩浮点噪声 ⇒ 返回 NaN。
    """
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan")
    mu, sd = float(x.mean()), float(x.std())
    if not np.isfinite(sd) or sd <= 0 or sd <= abs(mu) * DEGENERATE_CV:
        return float("nan")
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def ac1_ci95(r: float, n: int) -> tuple[float, float]:
    """自相关的 Fisher z 95% 区间。

    B 只有 ~23 个块时，`AC1 > 0` **远不足以**说「显著为正」——
    抽样误差能把真值 0 读成 ±0.4。区间跨 0 就不能声称显著。
    """
    if not np.isfinite(r) or n < 4:
        return (float("nan"), float("nan"))
    z = np.arctanh(np.clip(r, -0.999999, 0.999999))
    se = 1.0 / np.sqrt(n - 3)
    return (float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se)))


def flip_rate(year_vals: list[float]) -> float:
    """相邻年份之间的**符号翻转率**（0 = 每年同号，1 = 每年都翻）。

    预注册 §3.5.1：翻转率 > 1/3 的因子，`agree` 不作数 ——
    量级淹没在噪声里时，符号是抛出来的。
    """
    s = np.sign(np.asarray(year_vals, float))
    s = s[s != 0]
    if len(s) < 2:
        return float("nan")
    return float(np.mean(s[1:] != s[:-1]))


# ── 对照臂：给出**不依赖数据的零分布** ────────────────────────────────

def shuffle_p_agree(year_vals: list[float], n_shuffle: int, seed: int) -> float:
    """C1：把每年的**符号**随机重掷，比观测到的 `agree` 更高/持平的比例。

    ⚠️ 打乱的是**符号**，不是年份顺序。
    打乱顺序对 `agree` **完全无效**（它是顺序无关的统计量），
    那样的「对照臂」会安静地返回一个恒等于观测值的 p ≡ 1.0 ——
    看起来在跑，实际什么都没测。
    """
    rng = np.random.default_rng(seed)
    s = np.sign(np.asarray(year_vals, float))
    obs = float(np.mean(s == np.sign(np.nanmean(year_vals))))
    flips = rng.integers(0, 2, size=(n_shuffle, len(s))) * 2 - 1
    null = np.mean(flips == np.sign(np.nanmean(year_vals)), axis=1)
    return float((1 + int(np.sum(null >= obs))) / (1 + n_shuffle))


def shuffle_p_ac1(block_vals: np.ndarray, n_shuffle: int, seed: int) -> float:
    """C2：块序列随机置换后 AC1 的经验分布。

    置换**保边际分布**，只打乱次序 ⇒ 零分布回答的是
    「同样的块值，随便排会不会也这么持久」。
    """
    rng = np.random.default_rng(seed)
    obs = ac1(block_vals)
    if not np.isfinite(obs):
        return float("nan")
    b = np.asarray(block_vals, float)
    null = np.empty(n_shuffle)
    for i in range(n_shuffle):
        null[i] = ac1(rng.permutation(b))
    return float((1 + int(np.sum(null >= obs))) / (1 + n_shuffle))


# ── 主计算 ────────────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, k: int, n_shuffle: int, seed: int) -> dict:
    """一张逐日表 → 一份倾斜持续性报告（单一真相源）。"""
    factors = factor_columns(df.columns)
    yearly = to_yearly(df, factors)
    years = sorted(yearly)
    n_years = len(years)

    rows = []
    for f in factors:
        yv = [yearly[y][f] for y in years]
        anchor = float(np.nanmean(yv))                 # 全样本方向锚（不看单年）
        s = np.sign(yv)
        agree = float(np.mean(s == np.sign(anchor)))   # nan 符号为 nan，比较即 False
        blocks = to_blocks(df[f"{EXPO_PREFIX}{f}"].to_numpy(float), k)
        a1 = ac1(blocks)
        lo, hi = ac1_ci95(a1, len(blocks))
        fr = flip_rate(yv)
        # ★ 对照臂 D1（**不参与判决**，事后加的，只报不判）：
        # 块级**符号**与全样本方向一致的比例。
        # 为什么需要它：AC1 测的是**水平**的持续性，不是**方向**的持续性。
        # 一个「围绕稳定均值来回摆」的序列可以符号 100% 一致而 AC1 为负
        # —— 稳定的是方向，震荡的是幅度。这两者是不同的主张，
        # 而预注册的 M2 只测了后者。**判决规则不动**，这里只把事实报出来。
        bsig = np.sign(blocks)
        block_sign_same = float(np.mean(bsig == np.sign(anchor)))
        rows.append({
            "factor": f,
            "anchor_sign": int(np.sign(anchor)),
            "anchor_mean": anchor,
            "yearly": {str(y): yearly[y][f] for y in years},
            "n_years": n_years,
            "agree": agree,                            # 0~1，与 K/n_years 无关
            "agree_n": int(round(agree * n_years)),
            "flip_rate": fr,
            "n_blocks": int(len(blocks)),
            "ac1": a1,
            "ac1_ci95": [lo, hi],
            # 对照臂，只报不判：
            "diag_block_sign_same": block_sign_same,
            "diag_n_blocks_same_sign": int(round(block_sign_same * len(blocks))),
            "diag_blocks": [float(x) for x in blocks],
            "p_agree_shuffled": shuffle_p_agree(yv, n_shuffle, seed),
            "p_ac1_shuffled": shuffle_p_ac1(blocks, n_shuffle, seed + 1),
        })

    # ── 判决（预注册 §3.3，机械执行，无 margin 字段）──
    need = PREREG["min_factors_for_verdict"]
    stable, drift, mixed, excluded = [], [], [], []
    for r in rows:
        # 预注册 §3.5.1：翻转率过高 ⇒ agree 不作数，直接出局（不算稳定也不算漂移）
        if np.isfinite(r["flip_rate"]) and r["flip_rate"] > PREREG["flip_rate_discount"]:
            r["side"] = "excluded_flip"
            r["excluded_reason"] = f"flip_rate={r['flip_rate']:.3f} > {PREREG['flip_rate_discount']:.3f}"
            excluded.append(r["factor"])
            continue
        ok_stable = (r["agree_n"] >= PREREG["agree_stable"] and r["ac1"] > PREREG["ac1_stable"])
        ok_drift = (r["agree_n"] <= PREREG["agree_drift"] and r["ac1"] < PREREG["ac1_drift"])
        if ok_stable and not ok_drift:
            r["side"] = "stable"
            stable.append(r["factor"])
        elif ok_drift and not ok_stable:
            r["side"] = "drift"
            drift.append(r["factor"])
        else:
            # 「一致但 AC1 反向」/「AC1 为正但一致率居中」——两边条件打架，不计入
            r["side"] = "mixed"
            mixed.append(r["factor"])

    n_stable, n_drift = len(stable), len(drift)
    if n_stable >= need:
        verdict = "stable"
    elif n_drift >= need:
        verdict = "drift"
    else:
        verdict = "mixed"
    # pass_ 只有 bool —— 不许有 margin 字段（CLAUDE.md 铁律）
    verdict_pass = (verdict == "stable")

    return {
        "kind": "tilt_persistence",
        "k": k,
        "factors": factors,
        "years": years,
        "n_years": n_years,
        "caliber": CALIBER,
        "prereg": PREREG,
        "prereg_doc": "docs/TODO_tilt_persistence.md",
        "verdict": {
            "verdict": verdict,
            "pass_": bool(verdict_pass),
            "n_stable": n_stable,
            "n_drift": n_drift,
            "n_mixed": len(mixed),
            "n_excluded_flip": len(excluded),
            "stable_factors": stable,
            "drift_factors": drift,
            "mixed_factors": mixed,
            "excluded_flip_factors": excluded,
            "need_for_verdict": need,
        },
        "per_factor": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="度量风格倾斜的持续性")
    ap.add_argument("--daily", default="data/reports/wf_bt_k63.daily.parquet")
    ap.add_argument("--out", default="data/reports/tilt_persistence.json")
    ap.add_argument("--k", type=int, default=63, help="调仓块长，须与输入文件的 rebalance_every 一致")
    ap.add_argument("--n-shuffle", type=int, default=PREREG["n_shuffle"])
    ap.add_argument("--seed", type=int, default=20261014)
    a = ap.parse_args(argv)

    df = pd.read_parquet(a.daily)
    if df.index.duplicated().any():
        # 重复日期必须响亮拒绝：重复行会让同一天被数两遍，
        # 而统计量仍算得出来（只是偏了），没有任何异常抛出。
        raise SystemExit("输入含重复日期")
    doc = analyze(df, a.k, a.n_shuffle, a.seed)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    v = doc["verdict"]
    print(f"→ {out}")
    print(f"口径: 暴露={CALIBER['exposure_unit']}；{CALIBER['leg']}；"
          f"块={CALIBER['blocks']}（K={a.k}, {doc['per_factor'][0]['n_blocks']} 块）")
    print(f"判据: agree≥{PREREG['agree_stable']}/{doc['n_years']} 且 AC1>{PREREG['ac1_stable']} "
          f"⇒ 稳定；需 ≥{PREREG['min_factors_for_verdict']}/8 因子")
    print(f"{'因子':<10}{'anchor':>8}{'同号年':>7}{'翻转率':>8}"
          f"{'AC1':>8}{'AC1 95%CI':>20}{'p_agree':>9}{'p_ac1':>8}"
          f"{'块同号(只报)':>14}  判定")
    for r in doc["per_factor"]:
        lo, hi = r["ac1_ci95"]
        ci = f"[{lo:+.3f},{hi:+.3f}]"
        print(f"{r['factor']:<10}{r['anchor_sign']:>8d}{r['agree_n']:>5d}/{doc['n_years']}"
              f"{r['flip_rate']:>8.2f}{r['ac1']:>8.3f}{ci:>20}"
              f"{r['p_agree_shuffled']:>9.3f}{r['p_ac1_shuffled']:>8.3f}"
              f"{r['diag_n_blocks_same_sign']:>9d}/{r['n_blocks']:<4}  {r['side']}")
    print(f"\n判决: {v['verdict'].upper()}  pass_={v['pass_']}  "
          f"(稳定 {v['n_stable']} / 漂移 {v['n_drift']} / 混合 {v['n_mixed']} / 出局 {v['n_excluded_flip']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())