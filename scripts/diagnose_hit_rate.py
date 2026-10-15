"""命中率诊断：把「推荐准不准」拆成**能区分病因**的几层，而不是只报一个总数。

## 为什么不能只报一个总数

2026-10-15 的实测（`data/reports/verification.parquet`，4 个交易日 / 69 条有效判定）：

    合计命中率 30/69 = 43.5%（95%CI 31.8%~55.2%，与 50% 无显著差异）

只看这个数会得到两个**都错**的结论：「样本不够，再攒」或「信号没用，放弃」。
必须再问一层：**这 43.5% 是被什么结构造成的？** 本脚本回答的正是这个问题。

## 三层分解

| 层 | 问题 | 判读 |
|---|---|---|
| ① 顺/逆大盘 | 信号是在选股，还是在押大盘方向？ | **顺大盘 58.3% / 逆大盘 27.3%** |
| ② beta 剔除后 | 抽掉大盘涨跌，还剩多少超额？ | 日均 **−0.304%**、t=−0.65 |
| ③ 打分排序力 | score 高的是否真的更准？ | up 臂四分位**无单调性** |

## 为什么这三层是这个项目的生死判据

用户的硬指标是「5 万元 / 年化 20%」。而：

    ① 顺大盘显著优于逆大盘，且逆大盘**显著低于随机**（p=0.0034）
       ⇒ 当前这两臂的「信号」主要在表达**大盘方向**，而其中反向表达的部分是
         **反向信息**——这不是噪声，是系统性的负贡献。
    ② 剔除 beta 后日均超额 −0.304%（年化 −76.6%）
       ⇒ 距离 +20% 的缺口是 **96.6 pp/年**，不是调参能填的量级。
    ③ score 无排序力
       ⇒ 「取 score 前 10 名」这个动作本身没有信息量，
         所以「把因子权重调好」不会改变命中率——**这是设计问题，不是标定问题**。

⇒ 结论方向：**先改结构（两臂的定义与排序），再谈标定。**
在结构改对之前跑更多同构的前瞻数据，只是把同一个 43.5% 攒得更精确。

## 口径纪律

- 只统计 `result ∈ {hit, miss}`；`no_data`（无次日 bar）**剔除并计数**，
  剔除量必须一起报出来，否则样本量会在无声中被削减。
- `flat`（恰好 0.00%）单列，按 `aplus/verify.py::judge` 的契约不计命中。
- 显著性用正态近似的双侧 p；n<30 时同时打印 95%CI 并标注「区间结论不稳」。
- **禁止把「顺大盘更好」直接写成「信号有效」**：两者之间的因果由 ② 检验，
  而 ② 的答案是否定的。分层是为了**防止**这种误读。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# 🚨 形状 24：`python <脚本>` 的 sys.path[0] 是 `scripts/`，**不含仓库根** ⇒
#    不插这一行，命令行直接跑必然 ModuleNotFoundError（而 tests/ 按路径导入
#    自带仓库根，会替它把缺陷补上——守卫全绿而脚本跑不起来）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402

GENERATOR = "scripts/diagnose_hit_rate.py"


def _two_sided_p(hits: int, n: int) -> tuple[float, float, float, float]:
    """返回 (命中率, 标准误, z, 双侧 p)。比例检验的正态近似。

    n 很小的时候这个近似很粗，所以调用方必须**同时**把 CI 一起报出去——
    这与本仓「证据强度不同就必须在措辞上分级」是同一条纪律。
    """
    if n == 0:
        return (float("nan"),) * 4
    p = hits / n
    se = sqrt(p * (1 - p) / n) if 0 < p < 1 else 0.0
    z = (p - 0.5) / se if se else float("nan")
    pv = erfc(abs(z) / sqrt(2)) if se == se else float("nan")
    return p, se, z, pv


def _block(hits: int, n: int) -> dict:
    p, se, z, pv = _two_sided_p(hits, n)
    return {
        "n": n,
        "hits": hits,
        "rate": None if p != p else round(p, 4),
        "ci95": None if p != p else [round(max(0.0, p - 1.96 * se), 4),
                                     round(min(1.0, p + 1.96 * se), 4)],
        "z": None if z != z else round(z, 3),
        "p_two_sided": None if pv != pv else round(pv, 4),
        # n<30 时 CI 宽到不能当结论用，必须在下游措辞里降级
        "evidence": "薄（n<30，CI 宽）" if n < 30 else "可作方向性证据",
    }


def diagnose_verdicts(df) -> dict:
    """从**已判定**的表算出分层诊断。

    单独抽出来是为了让守卫能用构造出来的表直接打，
    而不必去碰真产物（真产物每天才长几行，拿它当测试输入等于
    「拿现场当标准答案」——现场自己也在变）。
    """
    dropped = {"no_data": int((df["result"] == "no_data").sum()),
               "flat": int((df["result"] == "flat").sum())}
    m = df[df["result"].isin(["hit", "miss"])].copy()
    if m.empty:
        raise ValueError(
            f"没有可判定的记录（全部落在 no_data/flat：{dropped}）——"
            f"此时算命中率只会得到 0/0 或 0.0，那是**看起来有结论**的假象")
    m["hit"] = (m["result"] == "hit").astype(int)

    out = {
        "excluded": dropped,
        "verdicts": len(m),
        "trading_days": int(m["pred_date"].nunique()),
        "span": [str(m["pred_date"].min()), str(m["pred_date"].max())],
        # ── ① 总命中率 ──
        "overall": _block(int(m["hit"].sum()), len(m)),
        "by_direction": {d: _block(int(g["hit"].sum()), len(g))
                         for d, g in m.groupby("direction")},
    }

    # ── ② 顺/逆大盘：信号是在选股还是在押方向 ──
    if "market_pct" in m.columns:
        # ⚠️ 用**字符串**标签而不是布尔列：numpy 的 `bool_` 真值判断不可靠，
        #   而且 `groupby` 出来的键类型会随 pandas 版本变（`numpy.bool_` vs
        #   `bool`），靠 `if k` 分支迟早在某次升级后静默错位。
        m["顺大盘"] = np.where((m["direction"] == "up") == (m["market_pct"] > 0),
                              "顺大盘", "逆大盘")
        out["with_market"] = {str(k): _block(int(g["hit"].sum()), len(g))
                              for k, g in m.groupby("顺大盘")}
        # ── ③ 剔除 beta 后的超额 ──
        sign = np.where(m["direction"] == "up", 1, -1)
        m["超额"] = (m["actual_pct"] - m["market_pct"]) * sign
        ex = {}
        for key, g in [("合计", m)] + sorted(m.groupby("direction")):
            mu = float(g["超额"].mean())
            sd = float(g["超额"].std(ddof=1))
            t = mu / (sd / sqrt(len(g))) if sd and len(g) > 1 else float("nan")
            ex[key] = {"n": len(g), "日均超额pct": round(mu * 100, 4),
                       "年化pct": round(mu * 252 * 100, 2),
                       "t": None if t != t else round(t, 3),
                       "p_two_sided": None if t != t else round(erfc(abs(t) / sqrt(2)), 4)}
        out["beta_adjusted_excess"] = ex

    # ── ④ 打分排序力：score 高的是否真的更准 ──
    up = m[m["direction"] == "up"].dropna(subset=["adjusted_score"])
    if len(up) >= 8:
        try:
            q = pd.qcut(up["adjusted_score"], 4, labels=["Q1最低", "Q2", "Q3", "Q4最高"])
            r = up.groupby(q, observed=True)["hit"].agg(["size", "mean"])
            out["score_ranking_power"] = {
                "by_quartile": {str(i): {"n": int(row["size"]),
                                         "rate": round(float(row["mean"]), 4)}
                                for i, row in r.iterrows()},
                "monotonic_increasing": bool(r["mean"].is_monotonic_increasing),
                "missing_adjusted_score": {
                    d: int(g["adjusted_score"].isna().sum())
                    for d, g in m.groupby("direction")},
            }
        except ValueError as exc:
            out["score_ranking_power"] = {"error": f"分箱失败：{exc}"}

    out["读取的判据契约"] = (
        "『命中率低』不等于『样本不够』：先看 with_market 与 "
        "beta_adjusted_excess。若顺大盘显著优于逆大盘，则信号主要在表达大盘方向；"
        "此时**增加同构前瞻样本只会把同一个数攒得更精确**，不会改变方向。")
    return out


def diagnose(path: Path) -> dict:
    return diagnose_verdicts(pd.read_parquet(path))


def _sha256_of(path: Path) -> str:
    """产物输入的字节指纹。

    ⚠️ **读文件本身，不用 mtime** —— mtime 是代理不是证据：
    本仓的变异台会「改源码 → 跑测试 → 写回」，
    内容回到逐位相同而 mtime 被顶到现在（第 112 层那条实测）。

    这里**不复用** `scripts/score_ranking_power.py` 的同名私函数：
    跨脚本 import 一个下划线私有名是把两个脚本焊在一条 import 链上，
    而 sha256 本身就是标准库原语（第 163 层禁的是**重写**量具，
    不是**调用**标准库）。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verification", type=Path,
                    default=ROOT / "data/reports/verification.parquet")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/hit_rate_diagnosis.json")
    a = ap.parse_args()
    if not a.verification.is_file():
        print(f"✗ 缺档：{a.verification} —— 没有它就不能报命中率（缺档必须报错）")
        return 1

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**，比对在**盖章时**；不一致就拒绝盖章（反向的错误证据）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    res = diagnose(a.verification)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(txt, encoding="utf-8")

    # 🚨 第 156 层：`stamp()` 默认把 `data_fp` 指向 `master/daily`（5460 个文件），
    #   而本脚本读的是**单个文件** `verification.parquet` ⇒ 那样盖出来的章
    #   指的是一个本脚本没读过的数据根，而真正要抓的输入漂移落在视野之外。
    #   底稿是**追加式**的（实测 80 行/4 日 → 100 行/5 日），
    #   所以必须由本脚本**自己声明**它读的那份字节。
    provenance.stamp(
        a.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_parquet": str(a.verification),
            "source_parquet_sha256": _sha256_of(a.verification),
            "verdicts_measured": res["verdicts"],
            "trading_days_measured": res["trading_days"],
        })
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
