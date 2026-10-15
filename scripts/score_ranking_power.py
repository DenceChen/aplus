#!/usr/bin/env python
"""S3 / G4：`score` 四分位命中率**是否单调递增**。

## 这个脚本答的是「能不能发布」，不是「赚不赚钱」

`docs/TODO_reverse_scoring_2026-10-15.md` §3 的主判据 G4 是**上线闸门**：

| 编号 | 判据 | 阈值 | 来源 |
|---|---|---|---|
| **G4** | `score` 四分位命中率 | **单调递增** | S3 |

原文：**「过不了这一关就不许发布」，与收益高低无关。**
⇒ 所以本脚本的判决是 `PASS` / `NOT_PASSED` 两态，
**不是**「有排序力」/「没有排序力」两态 —— 见下面「三态」一节。

## 为什么必须把**独立事件数**和**条目数**分开报（第 80 层）

`verification.parquet` 有 80 行，但它是

    4 个预测日 × 2 个臂 × 每日 10 只

⇒ **独立事件是 4 个交易日，不是 80 条观测。**
把 80 当 n 会把检出力虚报 20 倍。

## 分位怎么切：组内切，不全局切

每天每臂就是 10 只，分数**只在当天那 10 只里被用来排序**（交付物就是当日 10 只），
所以四分位必须在 `(pred_date, direction)` **组内**切。
全局切会把「哪一天」的效应混进「分数高」的效应里（第 22 层）。

## 三态：为什么不能只报「单调 / 不单调」

G4 是**必要条件**。不满足它有三种完全不同的原因，而它们的行动含义相反：

| 情形 | 含义 | 该做什么 |
|---|---|---|
| `PASS` | 四分位单调递增 | 允许进入发布评估（还要过 G1/G2/G3/G5/G6） |
| `NOT_PASSED_POWERLESS` | 不单调，且**检出力不足以分辨任何效应** | **不许发布**；先把前瞻期攒够 |
| `NOT_PASSED_MEASURED` | 不单调，且检出力**足够**分辨 | 分数本身没排序力 ⇒ 要换结构（S1） |

⇒ 「不单调」在 n=4 个独立日上**几乎必然**出现，它**不是**「证伪」。
把 `NOT_PASSED_POWERLESS` 写成「打分没有排序力」就是我上一轮差点犯的错
（§0 里那句「`adjusted_score` 四分位无单调性」正是这么来的，
而且 `adjusted_score` 只有 20/80 非 NaN）。本脚本两个口径都报。

## 置换检验：零假设是「分数与命中无关，但保留日期结构」

置换在 `(pred_date, direction)` **组内**做 ⇒ 日期效应与方向效应都被保留，
被打乱的只有「分数 ↔ 命中」的配对关系。
统计量用**点二列相关**（score 与 hit 指示变量），它比四分位表更稳
（四分位在 n=35 上只有 4 个点，Spearman 的自由度低到没有意义）。

p 值用 Phipson–Smyth 的 `(b+1)/(B+1)`，不是 `b/B` ——
后者在「零假设下无一次超界」时会报 **p = 0**，而 p=0 不是概率。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402

ART_NAME = "score_ranking_power.json"
#: 默认的判据字段 —— G4 原文写的是 `score`，**不是** `adjusted_score`。
DEFAULT_SCORE_COL = "score"
#: G4 的阈值形状：**单调递增**（`q_{i+1} >= q_i` 全成立），**不写死效应量**。
MONOTONE_NONDECREASING = True
#: 判据里「可判定」的定义：`hit`/`miss` 是判决，`no_data` 是**未知**、
#: `flat` 是**无波动** —— 两者都不是「未命中」，混进分母会凭空造出命中率。
JUDGEABLE = ("hit", "miss")
N_PERM = 20_000
SEED = 42


def _within_group_quartile(g: pd.Series) -> pd.Series:
    """组内四分位（1 = 分数最高）。n<4 时退化成按名次均分，绝不返回 NaN。"""
    n = len(g)
    ranks = g.rank(ascending=False, method="first")
    return ((ranks - 1) * 4 // n + 1).astype("int64")


def _point_biserial(x: np.ndarray, y: np.ndarray) -> float:
    """score 与 0/1 命中的点二列相关。任一侧常量 ⇒ 0.0（无关联可测，不是崩）。"""
    if len(x) < 3 or np.all(y == y[0]) or np.all(x == x[0]):
        return 0.0
    xs = (x - x.mean()) / (x.std() or 1.0)
    ys = (y - y.mean()) / (y.std() or 1.0)
    return float(np.mean(xs * ys))


def _permutation_p(values: np.ndarray, hits: np.ndarray,
                   groups: list[np.ndarray], stat, n_perm: int,
                   seed: int) -> tuple[float, float]:
    """组内置换 p 值 + 零分布的 95 分位（= **检出力下限**）。

    返回 `(p_two_sided, rho_null_abs_q95)`。
    `rho_null_abs_q95` 的含义：真实 |相关| 小于它 ⇒ **这份样本分不出它**。
    ⚠️ 这就是「NOT_PASSED_POWERLESS」的判据来源，不是拍脑袋的「n 太小」。
    """
    rng = np.random.default_rng(seed)
    obs = abs(stat(values, hits))
    null = np.empty(n_perm)
    for i in range(n_perm):
        perm = values.copy()
        for idx in groups:
            perm[idx] = rng.permutation(values[idx])
        null[i] = abs(stat(perm, hits))
    # Phipson & Smyth：p = (b+1)/(B+1)，**不是 b/B**
    p = float((int((null >= obs).sum()) + 1) / (n_perm + 1))
    return p, float(np.quantile(null, 0.95))


def measure(df: pd.DataFrame, score_col: str, n_perm: int = N_PERM,
            seed: int = SEED) -> dict:
    """一个 score 列上的完整测量。**两个口径一起报**（`score` 与 `adjusted_score`）。

    `n_perm` / `seed` 是参数而非常量，因为**对账守卫需要用同一组参数重算一遍**
    （第 118 层：读落盘 json 等于没问）。写死的话守卫只能对着快照做自洽检查。
    """
    usable = df[df["result"].isin(JUDGEABLE)].copy()
    # ⚠️ 缺分数的行**不许静默跳过**（第 112 层）：跳过后「6/8 可测」会变成
    #   「8/8 全部一致」，那是**另一个量**。这里把剔除量显式记进产物。
    n_missing_score = int(usable[score_col].isna().sum())
    scored = usable[usable[score_col].notna()].copy()
    out: dict = {
        "score_col": score_col,
        "n_rows_total": int(len(df)),
        "n_rows_judgeable": int(len(usable)),
        "n_rows_dropped_undecidable": int(len(df) - len(usable)),
        "dropped_breakdown": {k: int(v) for k, v in
                              df.loc[~df["result"].isin(JUDGEABLE), "result"]
                              .value_counts().items()},
        "n_rows_score_missing": n_missing_score,
        "n_rows_scored": int(len(scored)),
        "n_independent_pred_dates": int(df["pred_date"].nunique()),
        "independent_unit": "pred_date（一天一个独立事件；条目数不是独立观测数）",
        "quartile_rule": "组内（pred_date × direction）按 score 名次切 4 段",
        # ⚠️ 「四分位」这个词会让人默认「等分四份」——**实测不是**。
        # n=10（生产组大小）切出 **3/2/3/2**；只有 n 能被 4 整除才均衡
        # （n=4/8/16/20 才是 1/1/1/1、2/2/2/2、4/4/4/4、5/5/5/5）。
        # ⇒ 各段 `n` 差一倍，命中率是**小样本比率**；引用任何一段都必须带它的 `n`。
        # （守卫 `tests/test_score_ranking_power.py` 第一版断言「均分」，
        #   当场红——那是我**预测**的性质，不是量出来的。这是第 98 层。）
        "quartile_sizes_at_n10": {"Q1": 3, "Q2": 2, "Q3": 3, "Q4": 2},
        "quartile_sizes_note": (
            "四分位按名次取整分段，**段大小不均**（n=10 → 3/2/3/2）。"
            "这是「组内排名分段的必然」而不是缺陷：只有 n % 4 == 0 才均衡。"
            "⇒ 各段命中率的样本量不同，**跨段比较必须读 n**；"
            "G4 判的是**单调性**而非段间差值，故不受段大小影响。"),
    }
    if n_missing_score:
        out["score_missing_note"] = (
            f"⚠️ `{score_col}` 有 {n_missing_score}/{len(usable)} 条可判定记录**缺分数**"
            f" ⇒ 本口径只用 {len(scored)} 条。引用本口径的任何结论都只能带这个分母。")
    arms = {}
    for arm, a in scored.groupby("direction"):
        q = a.groupby("pred_date", group_keys=False)[score_col].apply(
            _within_group_quartile)
        a = a.assign(q=q)
        sub = {}
        for k in sorted(a["q"].unique()):
            g = a[a["q"] == k]
            sub[f"Q{int(k)}"] = {
                "n": int(len(g)),
                "hit_rate": float((g["result"] == "hit").mean()),
            }
        seq = [sub[k]["hit_rate"] for k in sorted(kk for kk in sub if kk.startswith("Q"))]
        monotone = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
        x = a[score_col].to_numpy(dtype=float)
        y = (a["result"] == "hit").to_numpy(dtype=float)
        groups = [np.asarray(idx) for idx in a.groupby("pred_date").indices.values()]
        rho = _point_biserial(x, y)
        p, q95 = _permutation_p(x, y, groups,
                                lambda u, v: _point_biserial(u, v), n_perm, seed)
        sub["_stats"] = {
            "hit_rate_sequence_q1_to_q4": seq,
            "monotone_nondecreasing": bool(monotone),
            "point_biserial_rho": rho,
            "permutation_p_two_sided": p,
            "null_abs_rho_q95_power_floor": q95,
            "detectable_at_alpha05": bool(abs(rho) >= q95),
        }
        arms[str(arm)] = sub
    out["arms"] = arms
    out["criterion"] = {
        "id": "G4",
        "source": "docs/TODO_reverse_scoring_2026-10-15.md §3（S3）",
        "rule": "score 四分位命中率单调递增（分段单调即可）",
        "alpha": 0.05,
        "n_perm": n_perm,
        "seed": seed,
        "p_formula": "(b+1)/(B+1)  —— 不用 b/B，否则零假设下会报 p=0",
    }
    return out


#: S2 的止血决定（`docs/TODO_reverse_scoring_2026-10-15.md` §2.1）：
#: `down` 臂在逆大盘时命中率 27.3%（p=0.0034）⇒ **已停用**。
#: ⇒ **发布闸门只看 `up` 臂**。把这个写死，是因为「按字典序取第一个失败的臂」
#: 会让判决理由落在一个**已经停用**的臂上 —— 症状一样，含义完全不同。
GATE_ARM = "up"


def verdict(res: dict) -> tuple[str, str]:
    """三态判决。**NOT_PASSED 两支都意味着不许发布**，区别只在下一步做什么。

    ⚠️ 判决按**闸门臂**（`GATE_ARM`）判，其余臂**并列报告但不当主判决**：
      早期版本按字典序取第一个失败臂就返回，于是理由文字落在已停用的
      `down` 臂上 —— 而「down 臂不单调」与「闸门没通过」的行动含义完全不同。
    """
    gate = res["arms"].get(GATE_ARM)
    if gate is None:
        return "NOT_PASSED_MEASURED", (
            f"闸门臂 {GATE_ARM!r} 在产物里不存在 ⇒ 判据无从判定，"
            "**不许**回落成「通过」")
    st = gate["_stats"]
    others = []
    for arm, sub in sorted(res["arms"].items()):
        if arm == GATE_ARM:
            continue
        s = sub["_stats"]
        others.append(
            f"{arm} 臂（**并列报告，非闸门**：S2 已因逆大盘 27.3% 停用）"
            f"单调={s['monotone_nondecreasing']} ρ={s['point_biserial_rho']:+.4f} "
            f"检出力下限={s['null_abs_rho_q95_power_floor']:.4f}")
    if st["monotone_nondecreasing"]:
        return "PASS", ("闸门臂四分位单调递增；仍须过 G1/G2/G3/G5/G6 才谈发布"
                        + ("；" + "；".join(others) if others else ""))
    kind = ("NOT_PASSED_POWERLESS" if not st["detectable_at_alpha05"]
            else "NOT_PASSED_MEASURED")
    tail = ("**这份样本连这个量级的关联都分不出来** ⇒ 不许发布，先攒前瞻期"
            if not st["detectable_at_alpha05"]
            else "**检出力足够却仍不单调** ⇒ 分数没有排序力，要换结构（S1）")
    return kind, (
        f"闸门臂 {GATE_ARM}：四分位不单调；点二列 ρ={st['point_biserial_rho']:+.4f}，"
        f"置换 p={st['permutation_p_two_sided']:.4f}，"
        f"零分布 95 分位（检出力下限）= "
        f"{st['null_abs_rho_q95_power_floor']:.4f} ⇒ {tail}"
        + ("；" + "；".join(others) if others else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S3/G4：score 四分位是否单调递增")
    ap.add_argument("--verification",
                    default="data/reports/verification.parquet")
    ap.add_argument("--out", default=f"data/reports/{ART_NAME}")
    args = ap.parse_args(argv)

    src = Path(args.verification)
    if not src.exists():
        print(f"🚨 {src} 不存在 —— G4 无法测量，**不许回落**", file=sys.stderr)
        return 2
    df = pd.read_parquet(src)

    # 两个口径一起测：`score` 是 G4 原文的字段；`adjusted_score` 是 §0 引用的字段
    # （它只有 20/80 非 NaN ⇒ 引用它的那句结论样本量只有四分之一）。
    results = {c: measure(df, c) for c in ("score", "adjusted_score")
               if c in df.columns}
    primary = results[DEFAULT_SCORE_COL]
    kind, why = verdict(primary)

    payload = {
        "prereg": "docs/TODO_reverse_scoring_2026-10-15.md",
        "gate": "G4 / S3 —— 上线闸门，与收益高低无关",
        "verification_source": str(src),
        "primary_score_col": DEFAULT_SCORE_COL,
        "by_score_col": results,
        "verdict": kind,
        "verdict_reason": why,
        "publish_allowed": kind == "PASS",
        "what_this_cannot_prove": [
            "**不能**把 NOT_PASSED_POWERLESS 读成「打分没有排序力」——"
            "n=4 个独立预测日，任何形状都极易出现，包括真的单调的那种；"
            "「没过关」与「被证伪」是两件事（§0 那句话就是这么错的）",
            "**不能**用条目数 80 当独立观测数 —— 它是 4 日 × 2 臂 × 10 只",
            "本脚本只测 G4 一个判据；G1/G2/G3/G5/G6 未测 ⇒ "
            "即使 G4 通过也**不许**据此发布",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    provenance.stamp(out, "scripts/score_ranking_power.py", root=ROOT,
                     extra={"verdict": kind,
                            "publish_allowed": payload["publish_allowed"],
                            "source_parquet": str(src),
                            "source_parquet_sha256": _sha256_of(src)})

    for col, res in results.items():
        print(f"\n=== 口径 {col}（{res['n_rows_judgeable']}/{res['n_rows_total']} 条可判定，"
              f"{res['n_independent_pred_dates']} 个独立预测日）===")
        for arm, sub in res["arms"].items():
            st = sub["_stats"]
            seq = " | ".join(
                f"{k} {sub[k]['hit_rate']:.1%}(n={sub[k]['n']})"
                for k in sorted(k for k in sub if k.startswith("Q")))
            print(f"  {arm:>4} 臂: {seq}")
            print(f"        单调={st['monotone_nondecreasing']} "
                  f"ρ={st['point_biserial_rho']:+.4f} "
                  f"p={st['permutation_p_two_sided']:.4f} "
                  f"检出力下限|ρ|={st['null_abs_rho_q95_power_floor']:.4f} "
                  f"⇒ 分得出? {st['detectable_at_alpha05']}")
    print(f"\n判决（G4 / {DEFAULT_SCORE_COL} 口径）: {kind}")
    print(f"  {why}")
    print(f"  允许发布: {payload['publish_allowed']}")
    print(f"→ {out}")
    return 0


def _sha256_of(path: Path) -> str:
    """产物字节指纹。**读文件本身**，不用 mtime —— mtime 是代理不是证据。"""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
