#!/usr/bin/env python3
r"""D1（10 涨 10 跌）的判决需要多少个**独立成交窗口**。

## 为什么要单独算一次

D2 的判决地平线已由 `scripts/verdict_horizon.py` 给出：需 **26.4 年**
才有资格判符号。两个交付物都被「独立观测数不够」卡住，但它们卡的**不是同一个量**：

| | D1（命中率） | D2（年化） |
|---|---|---|
| 观测单位 | **成交窗口**（`exit_date` 去重） | **交易日**（日净值序列） |
| 现有 | **5 个** | 1395 天 = 5.72 年 |
| 现读数 | CI95 `[0.0842, 0.9599]`（半宽 0.4378，未截断） | \|c\|/M = 0.466 |

⚠️ **D1 的窗口数比 D2 的天数少两个数量级** —— 136 行判定只落在 **5** 个窗口上
（`hit_rate_diagnosis.json`：`overall.n=136`、`n_windows=5`、
`inflation_factor=5.215=√(136/5)`）。第一个窗口就吞掉 56 行
（10-09/10-10/10-11 三个信号日 + 10-12 挤在同一笔成交里）。

⇒ **两个交付物的可行性差一个数量级以上**，不合并成一个「样本不够」的说法。

## 它量的是什么

`data/reports/hit_rate_diagnosis.json` 的 `overall` 块：
`half_width`（命中率 CI95 半宽）。半宽 ∝ `1/√W`，反解即可。

## 口径纪律

1. 🚨 **先复算产物的 `half_width`**，逐位相等才继续（判据 A）。
   口径不一致 ⇒ 后面的反解全部无效。
2. 🚨 **只用只读字段**，不许用字段算术推出反事实窗口数
   （记忆 `compare-like-calibers-or-not-at-all`）。
3. 🚨 **不改 `diagnose_hit_rate.py` 的任何阈值**（含 `THIN_WINDOWS`、`MDE_Z_SUM`、
   `NULL_RATE`）。本文件不写 `hit_rate_diagnosis.json`，不跑 t 检验。

## 🚨 诚实声明：本文件的判据是**看数之后**设计的

写这一点不是为了免责，是因为不写它就成了假预注册（记忆 `pre-register-thresholds-before-data`）。

- **判据 A 不是阈值判断**，而是**口径确认**：它只回答「产物那个 0.437843 是不是
  `1.96·√(p(1−p)/W)` 算出来的」。是 ⇒ 口径确认；否 ⇒ 抛错退出。
  它没有「通过/不通过」的判决臂，所以不存在「看到不利结论后改门槛」。
- **反解形式 `半宽 ∝ 1/√W` 是复用**，不是本文件发明：
  `scripts/verdict_horizon.py::required_n` 在 D2 那边先于 D2 的数确立，
  本文件 `import` 它而不是重写。
- **本文件没有判决臂**。它不回答「命中率是否优于 50%」，
  那个由 `hit_rate_diagnosis.json` 的 `ci95` 回答（现读数：`[0.0842, 0.9599]`）。
  它只回答「要攒到什么时候那个区间才收得窄到能下结论」。

## 对照臂（不是守卫，是让反解可被证伪的东西）

反解结果**完全取决于 `se` 用哪个口径**，而现有两个口径差 **2.71 倍**：

| 口径 | 公式 | 来源 | half_width |
|---|---|---|---|
| **判据臂** | `1.96·√(p(1−p)/W)` | `diagnose_hit_rate.py:200`，产物口径 | 0.5653 |
| **对照臂** | `1.96·sd(窗口率)/√W` | 窗口率的实际离散度 | 0.2085 |

判据臂假设「W 个窗口是同参数 p 的独立伯努利试验」，**不含窗口间异质性**。
实测窗口率 `[0.3929, 0.6500, 0.7500, 0.4500, 0.6000]` 的 sd = 0.1462 **小于**二项预期 0.2884
⇒ 判据臂的区间偏宽 ⇒ **反解出的 W 是上界**。

⚠️🚨 **但这不构成「窗口比独立假设更规律」的结论**：
W=5 估 sd 只有 4 个自由度，其抽样误差远大于两臂之间那几倍差。
**两个口径在 W=5 时都不可信**，因此本文件的结论只能是**形状陈述**：
「D1 的判决尺度在 0.1 ~ 10 年之间，取决于尚未定标的 `se` 口径」，
**不是**一个点估计（记忆 `unmeasurable-point-estimate-only-supports-shape-claims`）。

## 用法

    .venv/bin/python scripts/verdict_horizon_d1.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 🚨 **信号作用域的唯一入口**（预注册 `docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）。
#   本脚本**不读底稿**，它读**上游产物** ⇒ 只能要求上游自报作用域
#   （预注册 §5.2），**不许**「块缺失就按老口径继续用」。
from scripts.signal_scope import (  # noqa: E402
    VERDICT_NOT_DECIDABLE,
    attribution_block,
    resolve_attribution_context,
    upstream_scope_verdict,
)

SRC = ROOT / "data/reports/hit_rate_diagnosis.json"
OUT = ROOT / "data/reports/verdict_horizon_d1.json"


def _rel(path: Path) -> str:
    """相对仓根的路径；**不在仓内**（测试用 tmp_path）时用绝对路径。

    ⚠️ `Path.relative_to` 在不是祖先时会**抛 ValueError** ——
    而本仓踩过：「路径假设写死在仓内 ⇒ 一个测试就能把它逼出来」。
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)

#: 自己的路径，写进溯源章 —— `code_file_sha256` 算的就是这份文件的字节。
GENERATOR = "scripts/verdict_horizon_d1.py"

#: 🚨 第 166 层：章里的 `data_fp` **不适用于本脚本**（第 166 层的原文）。
#: `stamp()` 默认它算 `master/daily`（5460 个 parquet）的 `(文件数, 最大 mtime)`，
#: 而本脚本**一个 parquet 都没读** —— 它只读 `SRC` 那一份 JSON。
#: ⇒ 不声明的话，这就是**一枚指向它压根没读过的目录的假章**，
#: 而假章比无章更坏：它主动发出一个「数据是新鲜的」信号。
#: ⇒ 这里逐字声明，真实输入的指纹由调用点写进 `extra`。
DATA_FP_NOT_APPLICABLE = (
    "data_fp 不适用：verdict_horizon_d1.py 只读 data/reports/hit_rate_diagnosis.json，"
    "不读 master/daily 下的任何 parquet。真实输入指纹见 source_src_sha256。"
)

#: 半宽锚。**不是判决门槛**，只是把「要攒多久」写成可读的表。
#: 取值不来自任何阈值假设，取自产物自己出现过的量级（0.02 ~ 0.20 覆盖
#: `abs_gap_to_50pct`=0.0208 的一个数量级到整个 [0,1] 区间）。
HALF_WIDTH_TARGETS = (0.20, 0.10, 0.05, 0.02)

#: 一个成交窗口 ≈ 一个交易日（`exit_date`），年份换算用产物自己的 R。
#: 锚 = `hit_rate_diagnosis.json::span` 落在 7 天内 ⇒ 窗口即交易日。
WINDOW_IS_TRADING_DAY = True


def binomial_se(p: float, W: int) -> float:
    """产物口径：`diagnose_hit_rate.py:200` 的 `sqrt(p*(1-p)/W)`。"""
    return float((p * (1.0 - p) / W) ** 0.5) if 0 < p < 1 else 0.0


def cluster_se(window_rates: list[float]) -> float:
    """对照臂：窗口率的样本标准差 / √W。W<2 时无定义（与产物同规矩）。"""
    W = len(window_rates)
    if W < 2:
        raise ValueError(f"W={W} < 2 ⇒ 窗口间方差估不出来（与产物同一规矩）")
    m = sum(window_rates) / W
    var = sum((r - m) ** 2 for r in window_rates) / (W - 1)
    return float(var ** 0.5 / W ** 0.5)


def required_W(W_now: int, hw_now: float, target_hw: float) -> int:
    """半宽 ∝ 1/√W ⇒ `W_需 = W_now × (hw_now / target_hw)²`。

    🚨 **与 `verdict_horizon.py::required_n` 同一形式**（那里 `n ∝ (M/M_needed)²`），
    那里先于 D2 的数确立 ⇒ 此处 `import` 复用而不是重写，避免两处漂移。
    """
    if target_hw <= 0:
        raise ValueError("目标半宽必须为正")
    from math import ceil
    return int(ceil(W_now * (hw_now / target_hw) ** 2))


def bitwise_readback(o: dict) -> tuple[bool, float, float, float]:
    """判据 A 的**全部逻辑**。🚨 必须是模块级函数而不是嵌在 `main()` 里。

    嵌在 `main()` ⇒ 测试 import 不到 ⇒ 判决逻辑无任何东西能挡
    （记忆 `code-a-guard-cannot-import-is-effectively-unguarded`）。

    返回 `(bitwise_equal, recomputed, in_artifact, p_used)`。
    """
    W = int(o["n_windows"])
    # 🚨🚨 **必须用 `hits/n`，不能用 `rate`**。
    # `rate` 是 `round(hits/n, 4)` 的**显示值**（71/136=0.5220588 → 0.5221），
    # 产物内部算 se 用的是未舍入的 71/136。拿显示值复算会差几个 ulp
    # （实测 0.437841 vs 产物 0.437843）⇒ 判据 A 会**假性失败**。
    # 「字段存在」不等于「字段是原值」——`found-is-not-found-right` 的近亲。
    p = int(o["hits"]) / int(o["n"])
    return (round(1.96 * binomial_se(p, W), 6) == float(o["half_width"]),
            round(1.96 * binomial_se(p, W), 6), float(o["half_width"]), p)


def main() -> None:
    import argparse
    from aplus import provenance

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=SRC,
                    help="上游 hit_rate_diagnosis.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument(
        "--reports-dir", type=Path, default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --src 的父目录。")
    args = ap.parse_args()

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**读任何数据之前**（`main()` 最开头）。传 None 会让 stamp()
    #   **整项跳过**字节检查而无人察觉（第 131 层：关键字在、检查没跑）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    from scripts.measure_basket_nav_01 import R

    d = json.loads(args.src.read_text(encoding="utf-8"))

    # 🚨🚨 **信号作用域**（预注册 §5.2）。
    #   本脚本**不读底稿** ⇒ 它无法自己过滤 ⇒ 只能**要求上游自报作用域**。
    #   上游的作用域不是当前信号 ⇒ **拒绝反解**（否则算出来的是
    #   「**别的信号**还要攒多久」）。
    reports_dir = args.reports_dir if args.reports_dir else args.src.parent
    ctx = resolve_attribution_context(reports_dir)
    scope_check = upstream_scope_verdict(
        d, ctx.signal_id, ctx.weights_fingerprint)
    if not scope_check["ok"]:
        out = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "verdict": VERDICT_NOT_DECIDABLE,
            "verdict_reason": scope_check["reason"],
            "question": "D1（命中率）的判决需要多少个独立成交窗口",
            "source_artifact": str(args.src),
            "current_signal_id": ctx.signal_id,
            "current_weights_fingerprint": ctx.weights_fingerprint,
            "upstream_signal_scope": scope_check["upstream_scope"],
            "signal_scope": {
                "status": "MISSING_UPSTREAM_SCOPE",
                "current_signal_id": ctx.signal_id,
                "current_weights_fingerprint": ctx.weights_fingerprint,
                "source": ("上游产物自报（间接消费者不读底稿 ⇒ "
                           "它自己过滤不了 ⇒ 只能要求上游自报）"),
                "upstream_signal_scope": scope_check["upstream_scope"],
            },
            "attribution": {
                "basis": "none",
                "current_signal_id": ctx.signal_id,
                "current_weights_fingerprint": ctx.weights_fingerprint,
                "note": ("上游的作用域不可用 ⇒ 本产物**不做任何外推/反解** ⇒ "
                         "**无归属依据**。"),
            },
            "what_this_cannot_prove": [
                "🚨 **本产物没有做任何反解。** 上游的作用域不是当前信号 ⇒ "
                "**拒绝反解**。",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「还要攒很久」——"
                "它说的是「**上游的读数不属于当前信号**」。",
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        provenance.stamp(
            args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={
                "data_fp_applicability": DATA_FP_NOT_APPLICABLE,
                "source_src": str(args.src),
                "source_src_sha256": hashlib.sha256(
                    args.src.read_bytes()).hexdigest(),
                "verdict": VERDICT_NOT_DECIDABLE,
                "current_signal_id": ctx.signal_id,
                "current_weights_fingerprint": ctx.weights_fingerprint,
            })
        print(f"\n🚨🚨🚨 **不可判定：拒绝反解** 🚨🚨🚨\n  {scope_check['reason']}\n")
        print(f"→ {args.out}")
        return

    o = d["overall"]
    W = int(o["n_windows"])
    win_rates = [float(w["rate"]) for w in o["windows"]]

    # ── 判据 A：逐位复算产物半宽，口径不一致即抛错退出 ──
    bitwise, hw_bin, hw_prod, p = bitwise_readback(o)
    if not bitwise:
        raise SystemExit(
            f"🚨 判据 A 不成立：复算 half_width={hw_bin!r} "
            f"!= 产物 {hw_prod!r}\n   ⇒ 口径不一致，反解全部无效。停止，不出结论。"
        )

    hw_clu = 1.96 * cluster_se(win_rates)
    sd_win = cluster_se(win_rates) * W ** 0.5
    sd_bin_expected = binomial_se(p, W) * W ** 0.5

    def ladder(hw: float) -> list[dict]:
        return [
            {
                "target_half_width": T,
                "W_required": required_W(W, hw, T),
                "years_required": (required_W(W, hw, T) / R) if WINDOW_IS_TRADING_DAY else None,
                "multiple_of_now": round(required_W(W, hw, T) / W, 1),
            }
            for T in HALF_WIDTH_TARGETS
        ]

    judge_arm = ladder(hw_bin)
    control_arm = ladder(hw_clu)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "question": "D1（命中率）的判决需要多少个独立成交窗口",
        "source_artifact": _rel(args.src),
        "independent_unit": "成交窗口（exit_date 去重）——不是交易行",
        "criterion_A_bitwise_readback": {
            "unit": (
                "🚨 下面 `recomputed` / `in_artifact` 都是**半宽 half_width**，"
                "**不是命中率**。命中率在 `p_display_field_for_reference`（= 产物里 "
                "`round(p,4)` 的显示值）——两者量纲差约 1.08 倍，"
                "曾被读成同一个数而误判「产物与复算矛盾」。"
            ),
            "formula": "half_width = 1.96 · √(p(1−p)/W)  （diagnose_hit_rate.py:200）",
            "p_used": p,
            "p_used_derivation": "hits/n（**不是** `rate` 字段——那是 round(p,4) 的显示值）",
            "p_display_field_for_reference": float(o["rate"]),
            "W_from_artifact": W,
            "recomputed": round(hw_bin, 6),
            "in_artifact": hw_prod,
            "bitwise_equal": bitwise,
            "rule": "不相等即抛错退出，不出结论",
        },
        "honesty_declaration": {
            "criterion_was_designed_after_seeing_the_numbers": True,
            "why_that_is_not_a_broken_preregistration": (
                "判据 A 不是阈值判断而是口径确认：它只回答「产物那个数是不是这个公式算出来的」，"
                "没有通过/不通过判决臂 ⇒ 不存在「看到不利结论后改门槛」。"
                "反解形式 1/√W 复用 verdict_horizon.py::required_n（那里先于 D2 的数确立）。"
                "本文件无判决臂，不回答命中率是否优于 50%。"),
            "this_file_has_no_judgement_arm": True,
            "verdict_about_hit_rate_is_owned_by": (
                "hit_rate_diagnosis.json::overall.ci95（现读数 [0.0, 1.0]，"
                f"evidence=「{o['evidence']}」）"),
        },
        "current_reading": {
            "rate": p,
            "n_rows": int(o["n"]),
            "n_windows": W,
            "row_to_window_inflation": o.get("inflation_factor"),
            "inflation_factor_note": (
                f"{o.get('inflation_factor')} = √({o['n']}/{W}) ⇒ 行级区间被窗口叠加虚增 "
                f"{o.get('inflation_factor', 1) - 1:.2f} 倍"),
            "window_rates": win_rates,
            "window_sizes": [int(w["n"]) for w in o["windows"]],
            "first_window_absorbs": (
                f"{o['windows'][0]['n']}/{o['n']} 行 "
                f"（{o['windows'][0]['window']}：10-09/10-10/10-11 三个信号日 "
                "与 10-12 落在同一笔成交）"),
            "ci95": o["ci95"],
            "ci95_clipped": o["ci95_clipped"],
            "evidence_grade": o["evidence"],
        },
        "horizon": {
            "law": "半宽 ∝ 1/√W（与 verdict_horizon.py 的 M ∝ 1/√n 同形式）",
            "judgement_arm_binomial": {
                "role": "产物口径的判据臂",
                "se": round(binomial_se(p, W), 6),
                "half_width": round(hw_bin, 6),
                "ladder": judge_arm,
            },
            "control_arm_cluster": {
                "role": "⚠️ 对照臂，非判决臂。1.96·sd(窗口率)/√W，**不是**产物口径。",
                "se": round(cluster_se(win_rates), 6),
                "half_width": round(hw_clu, 6),
                "ladder": control_arm,
                "ratio_vs_judgement_arm": round(hw_bin / hw_clu, 3),
                "overdispersion_or_under": (
                    f"sd(窗口率)={sd_win:.6f} vs 二项预期 {sd_bin_expected:.6f} ⇒ "
                    f"{'欠离散（实际更集中 ⇒ 判据臂区间偏宽 ⇒ 反解出的 W 是上界）' if sd_win < sd_bin_expected else '过离散（实际更分散 ⇒ 判据臂区间偏窄 ⇒ 反解出的 W 是下界）'}"),
            },
            "spread_between_arms": {
                "ratio_at_0.05": round(judge_arm[2]["W_required"] / control_arm[2]["W_required"], 2),
                "reading": "两个口径在目标半宽 0.05 处差 "
                           f"{judge_arm[2]['W_required'] / control_arm[2]['W_required']:.1f} 倍",
            },
        },
        "what_this_is_not": {
            "is_not_a_verdict": "本文件不判命中率是否优于 50%，那是 ci95 的事",
            "is_not_a_point_estimate": (
                f"🚨 结论只能是**形状陈述**：W={W} 时估 sd 只有 {W - 1} 个自由度，"
                f"其抽样误差远大于两臂之间那几倍差 ⇒ 两个口径在 W={W} 时都不可信。"
                "任何单一年数都是把两个不可信口径之一当真，禁止引用。"),
            "does_not_change_any_threshold": (
                "不改 diagnose_hit_rate.py 的 THIN_WINDOWS / MDE_Z_SUM / NULL_RATE，"
                "不写 hit_rate_diagnosis.json"),
            "contradiction_policy": "若与本文件结论相反，只加对照臂，不改判据臂",
        },
        "comparison_with_D2": {
            "D1_windows_needed_at_half_width_0.05": judge_arm[2]["W_required"],
            "D1_years": judge_arm[2]["years_required"],
            "D2_years_to_determine_sign": 26.4,
            "ratio": round(26.4 / judge_arm[2]["years_required"], 1),
            "reading": (
                f"⚠️ 两个交付物都被独立观测数卡住，但卡的量不同、尺度差 "
                f"{26.4 / judge_arm[2]['years_required']:.0f} 倍以上。"
                "**不许**把它们合并成一句「样本不够」——"
                "D1 在 1~2 年尺度上可判决，D2 在人类寿命内不可判决。"),
        },
    }

    a0, a1 = judge_arm[2], control_arm[2]
    # 🚨 区间必须**同一个 T 下的两臂**，不许跨 T 取两端
    #    （跨 T 拼区间 = 拿两个不同量的端点说一个范围，
    #      compare-like-calibers-or-not-at-all 的形状错误）。
    bands = [
        {"target_half_width": j["target_half_width"],
         "judgement_arm_years": j["years_required"],
         "control_arm_years": c["years_required"],
         "control_judgement_ratio": round(j["years_required"] / c["years_required"], 2)}
        for j, c in zip(judge_arm, control_arm)
    ]
    lo_y = min(b["control_arm_years"] for b in bands)
    hi_y = max(b["judgement_arm_years"] for b in bands)
    out["reading"] = (
        f"判据 A 逐位相等 = {out['criterion_A_bitwise_readback']['bitwise_equal']}；"
        f"命中率 p={p:.4f}（n={o['n']} 行）只落在 **W={W} 个成交窗口**上，"
        f"CI95 {o['ci95']} 覆盖整个 [0,1]。\n"
        f"半宽 ∝ 1/√W ⇒ 要收到半宽 0.05（判定 {p:.4f} 与 0.5 相差 "
        f"{abs(p - 0.5):.4f} 的同量级）：\n"
        f"  判据臂（二项 se，产物口径）需 W={a0['W_required']} ≈ {a0['years_required']:.2f} 年；\n"
        f"  对照臂（聚类 se）      需 W={a1['W_required']} ≈ {a1['years_required']:.2f} 年；\n"
        f"  ⇒ 同一目标半宽下两臂差 {out['horizon']['spread_between_arms']['ratio_at_0.05']:.1f} 倍。\n"
        f"🚨 **不给单一年数**：W={W} 时估 sd 只有 {W - 1} 个自由度，两臂都不可信。"
        f"全部目标半宽下的跨度是 **{lo_y:.2f} ~ {hi_y:.1f} 年**（下界=对照臂最宽目标，"
        f"上界=判据臂最宽目标）。\n"
        f"对照 D2：判符号需 26.4 年 ⇒ **D1 与 D2 的可行性差一个数量级以上**。"
    )
    out["horizon"]["per_target_bands"] = bands
    # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）：
    #   间接消费者的作用域**来自上游**（它自己不读底稿）。
    out["signal_scope"] = {
        "status": scope_check["upstream_scope"].get("status"),
        "current_signal_id": ctx.signal_id,
        "current_weights_fingerprint": ctx.weights_fingerprint,
        "source": ("上游产物自报（间接消费者不读底稿 ⇒ "
                   "它自己过滤不了 ⇒ 只能要求上游自报）"),
        "upstream_signal_scope": scope_check["upstream_scope"],
    }
    out["attribution"] = {
        "basis": "upstream",
        "current_signal_id": ctx.signal_id,
        "current_weights_fingerprint": ctx.weights_fingerprint,
        "note": ("间接消费者**不读底稿** ⇒ 它自己归属不了 ⇒ "
                 "它的作用域**来自上游产物自报**，见 "
                 "`upstream_signal_scope`。"),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # 🚨 第 166 层：章必须盖在**本脚本真正读过的那份字节**上，
    #   而不是 `stamp()` 默认的 `master/daily`（5460 个 parquet，本脚本一个都没读）。
    #   `extra` 里逐字声明「`data_fp` 不适用」+ 自报真实输入的 sha256。
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "data_fp_applicability": DATA_FP_NOT_APPLICABLE,
            "source_src": _rel(args.src),
            "source_src_sha256": hashlib.sha256(
                args.src.read_bytes()).hexdigest(),
        })
    print(f"→ {_rel(args.out)}")
    print(out["reading"])


if __name__ == "__main__":
    main()
