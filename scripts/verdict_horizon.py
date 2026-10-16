#!/usr/bin/env python3
r"""判决所需样本量的反解 —— D2 究竟是「还没攒够」还是「结构上不可能」。

## 它量的是什么

`data/reports/basket_nav_01_2026-10-16.json` 的判决档读数
`c = +10.5563%` / `M = +22.6738%/年`，判决规则 §16 说
`|c| < M` ⇒ **连符号都定不了**。

那句话有两个读法，而它们导向完全不同的行动：

- **读法甲**：「样本不够，攒够就能判决」⇒ 于是去攒数据。
- **读法乙**：「`M ∝ 1/√n`，而 `σ_年化` 是市场波动降不下来
  ⇒ 需要的时间尺度超出人类寿命」⇒ 于是**停止用这个框架找答案**。

本脚本把它们分开算清楚。

## 口径纪律（照抄仓库既有铁律，不是本文件发明的）

1. 🚨 **净值序列用生产的 `account_nav()` 组装**，不许把 `net_excess_*`
   直接当净值（那是**差**，`measure_basket_nav_01.py:100` 明写，实测差 10.01pp）。
2. 🚨 **MDE 用生产公式** `1.96·sd(log1p)·√R/√n`，年化乘 `R` **不乘 `√R`**
   （推导与模拟验证见 `measure_basket_nav_01.py:60-85` 的 docstring）。
   本脚本的「读数校验」就是它算出的 MDE 与产物**逐位**比对。
3. 🚨 **不许用字段算术推出反事实**。本文件不重算任何已落盘字段来「验证」它；
   它只用生产函数**从 daily.parquet 重算 MDE**，并要求与产物逐位相同才算数。

## 判据（预注册于看数之前）

- **A**：`读回 MDE == 产物 MDE`（逐位）⇒ 口径一致，后面的反解才有效。
  不一致 ⇒ 抛错退出，不出结论。
- **B**：`n_eff/newey 折损 < 1%` ⇒ iid 假设成立，MDE 未被高估。
  若折损显著 ⇒ **MDE 可能被高估**，此时读法甲有部分成立，判据不适用。
- **C**：把所需 `n` 换算成年，与**任何人类可用的数据跨度**比较。
  ⚠️ 门槛不取手打字面量，取**产物自己声明的窗口**（`years`）作分母：
  「所需年数 / 现有年数」这个**比值**才是承重数字，比绝对年数稳。
- **D**：判决若与本脚本相反 ⇒ **只加对照臂，不改判据臂**。

## 它不做什么

🚨 **不改 `judge()`，不改 `TARGET`，不改任何判据。**
本脚本只回答「在当前公式下要多少样本」，不回答「该不该换个公式」——
后者是判据臂的重写，须另起预注册。

用法
----
    .venv/bin/python scripts/verdict_horizon.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BASKET = ROOT / "data/reports/basket_nav_01_2026-10-16.json"
DAILY = ROOT / "data/reports/wf_weights_backtest_k21.daily.parquet"
OUT = ROOT / "data/reports/verdict_horizon.json"

#: 自己的路径，写进溯源章 —— `code_file_sha256` 算的就是这份文件的字节。
GENERATOR = "scripts/verdict_horizon.py"

#: 🚨 第 166 层：章里的 `data_fp` **不适用于本脚本**。
#: `stamp()` 默认它算 `master/daily`（5460 个 parquet）的 `(文件数, 最大 mtime)`，
#: 而本脚本读的是 `BASKET`（一份 JSON）与 `DAILY`（一份 parquet）——**在 `master/daily` 里一个都没读**。
#: ⇒ 不声明的话，那是一枚**指向它压根没读过的目录的假章**，
#: 而假章比无章更坏：它主动发出一个「数据是新鲜的」信号。
#: ⇒ 两份真实输入的指纹由调用点写进 `extra`（路径 + sha256 成对：路径答「从哪来」，
#:   指纹答「是哪些字节」——`DAILY` 是**可重写的副产物**，只给路径会给出恒真的新鲜度）。
DATA_FP_NOT_APPLICABLE = (
    "data_fp 不适用：verdict_horizon.py 读 data/reports/basket_nav_01_2026-10-16.json "
    "与 data/reports/wf_weights_backtest_k21.daily.parquet，"
    "不读 master/daily 下的任何 parquet。真实输入指纹见 source_basket_sha256 / source_daily_sha256。"
)

#: Newey-West 的最大滞后。与记忆 `diagnose-with-the-quantity-production-actually-uses`
#: 无关；5 是「日频收益的一周效应刚好覆盖」这个独立锚，不是为了拟合结果而挑的。
NW_LAG = 5

#: 判据 B 的门槛：iid 假设是否成立的折损上限。
#: 外部锚 = 「一周 5 个交易日」的**完整一周** —— 折损若小到覆盖不满一周，
#: 那就没有任何日频聚集可言。
NW_TOLERANCE = 1.0 / NW_LAG / 100.0  # = 1/500 = 0.2%


def production_mde(r: np.ndarray, R: int) -> float:
    """MDE = 1.96·sd(log1p)·R/√n —— 与 `measure_basket_nav_01._leg` 同一公式。

    年化乘 `R` 而非 `√R`：那是「由 n 天估出的年化率」的标准误，
    与「持有一年那一个观测」的 sd 差 `√R = 15.62` 倍（见被引 docstring 的模拟验证）。
    """
    n = len(r)
    if n < 2:
        return float("nan")
    return float(1.96 * np.log1p(r).std(ddof=1) / np.sqrt(n) * R)


def required_n(c: float, M: float, n_now: int, M_needed: float | None = None) -> int:
    """M ∝ 1/√n ⇒ 要把 M 降到目标值所需的天数。

    - `M_needed is None`：要 `M ≤ |c|`（**符号可判**）
    - 否则：要 `M ≤ M_needed`（例如 `U = c + M < TARGET` ⇒ **可判「不可达」**）
    """
    target_M = abs(c) if M_needed is None else M_needed
    if target_M <= 0:
        raise ValueError("目标 M ≤ 0，无有限 n 可达")
    return int(round(n_now * (M / target_M) ** 2))


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    from aplus import provenance

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**读任何输入之前**。传 None 会让 stamp() **整项跳过**字节检查
    #   而无人察觉（第 131 层：关键字在、检查没跑）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    from scripts.measure_basket_nav_01 import R, account_nav

    basket = json.loads(BASKET.read_text(encoding="utf-8"))
    df = pd.read_parquet(DAILY)
    target = float(basket["target"])

    tiers = {}
    for t in basket["tiers"]:
        tiers[t["tier"]] = t
    verdict = next(t for k, t in tiers.items() if k.startswith("①"))
    c = float(verdict["cagr"])
    M_prod = float(verdict["mde_as_annual_pct"])
    n_now = int(verdict["n_days"])
    yrs_now = float(verdict["years"])

    # ── 判据 A：从 daily.parquet 重算 MDE，必须与产物逐位相同 ──
    r = account_nav(df, "net_excess_bt003")
    M_recomputed = production_mde(r, R)
    bitwise = (M_recomputed == M_prod)
    if not bitwise:
        raise SystemExit(
            f"🚨 判据 A 不成立：重算 MDE={M_recomputed!r} != 产物 {M_prod!r}\n"
            f"   ⇒ 口径不一致，后面的反解全部无效。停止，不出结论。"
        )

    # ── 判据 B：自相关会不会让 MDE 被高估 ──
    log = np.log1p(r)
    acf = [float(np.corrcoef(log[:-k], log[k:])[0, 1]) for k in range(1, NW_LAG + 1)]
    n_eff = n_now / (1 + 2 * sum((1 - k / (NW_LAG + 1)) * acf[k - 1]
                                 for k in range(1, NW_LAG + 1)))
    shrink = 1 - n_eff / n_now
    M_newey = float(1.96 * log.std(ddof=1) / np.sqrt(n_eff) * R)

    # ── 判据 C：反解 ──
    n_sign = required_n(c, M_prod, n_now)
    n_unreach = required_n(c, M_prod, n_now, M_needed=target - c)
    n_sign_nw = required_n(c, M_newey, n_now)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "question": "判决 |c|<M 是「还没攒够数据」还是「结构上不可能」",
        "source_artifact": str(BASKET.relative_to(ROOT)),
        "source_daily": str(DAILY.relative_to(ROOT)),
        "leg": "net_excess_bt003（① 判决档）",
        "target_annual": target,
        "R_days_per_year": R,
        "criterion_A_bitwise_readback": {
            "MDE_from_daily": M_recomputed,
            "MDE_in_artifact": M_prod,
            "bitwise_equal": bitwise,
            "rule": "不相等即抛错退出，不出结论",
        },
        "criterion_B_iid_holds": {
            "preregistered_wording_verbatim":
                "若折损显著 ⇒ **MDE 可能被高估**，此时读法甲有部分成立，判据不适用。",
            "preregistered_wording_is_wrong_about_direction": (
                "🚨 预注册这句把方向写反了，2026-10-16 实测发现。"
                "自相关修正把有效样本量**调小**（n_eff < n）⇒ MDE **调大**，"
                "不是调小 ⇒ 方向是「MDE 被**低估**」。"
                "🚨 保留原措辞不删：它是判据臂的一部分，"
                "删掉就变成「看到不利结论后改判据」。"
                "本条只登记实测方向，**不删不改**预注册原文。"
            ),
            "acf_lag1_to_%d" % NW_LAG: [round(a, 6) for a in acf],
            "acf_se_approx": round(float(1 / np.sqrt(n_now)), 6),
            "acf_all_insignificant": all(abs(a) < 2 / np.sqrt(n_now) for a in acf),
            "n_eff_newey_west": round(n_eff, 1),
            "shrink": round(shrink, 6),
            "tolerance": NW_TOLERANCE,
            "holds": bool(shrink < NW_TOLERANCE),
            "MDE_newey_west": round(M_newey, 6),
            "direction_actually_observed": (
                "MDE_newey_west > MDE_iid ⇒ 自相关让 MDE **更大**"
                if M_newey > M_prod else
                "MDE_newey_west <= MDE_iid ⇒ 自相关让 MDE 更小或持平"
            ),
            "control_arm_role": (
                "⚠️ 对照臂，非判决臂。判决仍用产物原口径 iid。"
                "本臂只用于回答「自相关会不会把结论救回来」，"
                "而实测方向是**加重**不是减轻。"
            ),
        },
        "criterion_C_horizon": {
            "c": c,
            "MDE": M_prod,
            "abs_c_over_M": abs(c) / M_prod,
            "n_now": n_now,
            "years_now": yrs_now,
            "to_determine_sign": {
                "n_required": n_sign,
                "years_required": n_sign / R,
                "multiple_of_now": n_sign / n_now,
            },
            "to_determine_unreachable": {
                "M_needed": target - c,
                "n_required": n_unreach,
                "years_required": n_unreach / R,
                "multiple_of_now": n_unreach / n_now,
            },
            "control_arm_newey_west_horizon": {
                "role": "⚠️ 对照臂（非判决）",
                "MDE": round(M_newey, 6),
                "n_required": n_sign_nw,
                "years_required": n_sign_nw / R,
                "multiple_of_now": n_sign_nw / n_now,
                "reading": "自相关口径**加重**结论，不是减轻",
            },
        },
        "what_this_is_not": {
            "does_not_change_judge": True,
            "does_not_change_target": True,
            "does_not_suggest_a_different_formula":
                "本文件只算「当前公式要多少样本」。该不该换公式是判据臂重写，"
                "须另起预注册；此处提出即视为越权。",
            "contradiction_policy": "与本文件结论相反时，只加对照臂，不改判据臂",
        },
    }

    sign = out["criterion_C_horizon"]["to_determine_sign"]
    unreach = out["criterion_C_horizon"]["to_determine_unreachable"]
    nw = out["criterion_C_horizon"]["control_arm_newey_west_horizon"]
    b = out["criterion_B_iid_holds"]
    out["reading"] = (
        f"c={c:+.4%}，M={M_prod:+.4%}/年，|c|/M={abs(c) / M_prod:.3f}"
        f"（<1 ⇒ 连符号都定不了，这与产物判决一致）。\n"
        f"要翻转成「符号可判」需 n×{sign['multiple_of_now']:.1f} "
        f"= {sign['years_required']:.1f} 年数据，而现有 {yrs_now:.2f} 年。\n"
        f"要判成「不可达」需 {unreach['years_required']:.1f} 年。\n"
        f"对照臂（Newey-West，非判决）：自相关让 MDE "
        f"{'变大' if M_newey > M_prod else '变小'} → "
        f"M={M_newey:+.4%}/年，需 {nw['years_required']:.1f} 年，"
        f"即**{'加重' if M_newey > M_prod else '减轻'}**结论。\n"
        f"⚠️ 预注册 B 的后果陈述把方向写反了（原文保留未删，见产物 "
        f"preregistered_wording_verbatim）。"
    )

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 🚨 第 166 层：章盖在本脚本真正读过的那两份字节上（见 DATA_FP_NOT_APPLICABLE）。
    provenance.stamp(
        OUT, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "data_fp_applicability": DATA_FP_NOT_APPLICABLE,
            "source_basket": str(BASKET.relative_to(ROOT)),
            "source_basket_sha256": hashlib.sha256(BASKET.read_bytes()).hexdigest(),
            "source_daily": str(DAILY.relative_to(ROOT)),
            "source_daily_sha256": hashlib.sha256(DAILY.read_bytes()).hexdigest(),
        })
    print(f"→ {OUT.relative_to(ROOT)}")
    print(out["reading"])
    print(f"\n写盘前最后一次确认：读数校验逐位相等 = {bitwise}")


if __name__ == "__main__":
    main()
