"""从真回测报告里抽 K 扫描表，**门槛一律重算，不读报告里的值**。

## 为什么必须重算

报告里那个 `hurdle_gross` 是**本仓实现算出来的**，
用它去检验本仓实现的结论 = 自证（详见
`docs/TODO_measure_sigma_r.md`「重算就是造第二台测量仪」）。

这里的门槛只依赖三个**外部锚**：

```
TARGET_NET      = 0.20      用户的收益目标
COST_REAL_50K   = 0.0055    5 万本金单笔 5000 的真实现况往返成本
DAYS_PER_YEAR   = 244       A 股年交易日
```

三者都不来自本仓任何实现。算完之后再与报告里存的值**对一遍**：
吻合是检查（不是依据），不吻合必须报警。

## 自查（每条都必须过，否则退出码非 0）

1. `annual_gross_pct == daily_excess × 244`（逐个年份）
2. 重算门槛 == 报告存的 `hurdle_gross`
3. 成本缩放是线性的（`cost_rescale_linear_check.linear`）

任何一条不过就 `SystemExit` —— **宁可不出表，也不能出一张口径没对过的表**。
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np
from pathlib import Path

# ── 三个外部锚。**不 import 本仓任何计算模块**，就是为了保持独立。 ──────────
# ⚠️ 2026-10-15 例外说明：下面那句「不 import 本仓任何模块」的**本意**是
#   「不借用本仓算出来的数」（门槛/成本/年化乘数都自己从外部锚推）。
#   `aplus.provenance` 是**纯 sidecar 写入器**，不含任何计算，
#   引入它**不破坏独立性**，而它换来的是这份产物从「不可引用」变成可引用。
TARGET_NET = 0.20
COST_REAL_50K = 0.0055
DAYS_PER_YEAR = 244

import sys  # noqa: E402
# 🚫 不要把它别名成 `_sys`：仓库里没有命名冲突，而别名会让
#   `tests/test_script_entrypoint_imports_are_satisfiable.py` 的 AST 扫描
#   （按 `sys.path.insert` 的字面形状找调用点）**整条看不见这行**
#   ⇒ 脚本从命令行跑就会 ModuleNotFoundError，而守卫报「没有 insert」。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aplus import provenance  # noqa: E402


def hurdle_gross(k: int) -> float:
    """跨过门槛所需的**毛**超额率（年化，小数）。

    年成本 = 单次成本 × 换手次数 = COST_REAL_50K × 244/K。
    要净到 TARGET_NET，毛就得比它多出这笔成本。
    """
    return TARGET_NET + COST_REAL_50K * (DAYS_PER_YEAR / k)


def _finite_mean(vals) -> tuple[float, int]:
    """有限值的均值 + 参与计算的个数。

    ★ 为什么不能直接 `sum(vals)/len(vals)`：**`t_blocked` 在调仓块不足 3 个
    的年份是 `None`**（K=63 时 2026 只有 1~2 块）。
    `None` 当 0 会把「没算出来」读成「t=0」，而 0 恰好是最容易被当成
    「无效应」的数 —— 一个缺失被静默翻译成一个结论。
    ⇒ 缺失就是缺失：**剔掉、计数、并把计数一起报出来。**
    """
    ok = [v for v in vals if v is not None and math.isfinite(float(v))]
    return (float(np.mean(ok)), len(ok)) if ok else (float("nan"), 0)


def summarize(doc: dict) -> dict:
    """一份报告 → 一行摘要，附带三条自查的结果。"""
    k = int(doc["rebalance_every"])
    years = doc["years"]
    n = len(years)

    gross = [y["gross"]["annual_pct"] for y in years]
    net_real = [y["real"]["annual_net_pct"] for y in years]
    t_real = [y["real"]["t_blocked"] for y in years]
    cost_real = [y["real"]["annual_cost_pct"] for y in years]
    t_mean, t_n = _finite_mean(t_real)

    # 自查 1：年化必须是日频 × 244。差一个常数差，就是口径串了。
    drift = max(
        abs(y["bt003"]["annual_gross_pct"] - y["bt003"]["daily_excess"] * DAYS_PER_YEAR)
        for y in years
    )

    h = hurdle_gross(k)
    avg_gross = sum(gross) / n
    return {
        "k": k,
        "rounds_per_year": DAYS_PER_YEAR / k,
        "hurdle_recomputed": h,
        "hurdle_in_report": doc.get("hurdle_gross"),
        "hurdle_gap": None if doc.get("hurdle_gross") is None else abs(h - doc["hurdle_gross"]),
        "avg_gross": avg_gross,
        "ratio": avg_gross / h if h else float("nan"),
        "avg_net_real": sum(net_real) / n,
        "avg_cost_real": sum(cost_real) / n,
        "avg_t_real": t_mean,
        "n_t_obs": t_n,
        "hit_years": sum(1 for y in years if y["real"]["hit_threshold"]),
        "years": n,
        "_selfcheck_annualization_drift": drift,
        "_selfcheck_linear": bool(doc["cost_rescale_linear_check"]["linear"]),
        "per_year_gross": {int(y["year"]): y["gross"]["annual_pct"] for y in years},
        "per_year_net_real": {int(y["year"]): y["real"]["annual_net_pct"] for y in years},
        "per_year_t_real": {int(y["year"]): y["real"]["t_blocked"] for y in years},
    }


def selfcheck(row: dict, drift_tol: float = 0.0, hurdle_tol: float = 0.0) -> list[str]:
    """返回**空列表** = 全过。返回非空 = 每一条都是一条独立的口径不符。

    ## 为什么容差是 **0**，不是 1e-12

    被比的两个数是**同一次运算、同一个进程**里算出来的
    （`annual_gross_pct = daily_excess × 244`），JSON 用 `repr` 往返
    **不丢 bit**。实测六份报告的漂移**恰好是 0.000e+00**。

    ⇒ 任何**非零**差都只有一个来源：**报告是另一条代码路径写的**
    （改了公式、换了天数、或者根本不是这个脚本产出的）。
    那正是最该拦下的情况，所以容差必须是 0。
    留一丝容差，等于给「口径已经换了但数值差不多」留后门。
    """
    bad = []
    if row["_selfcheck_annualization_drift"] > drift_tol:
        bad.append(f"K={row['k']}: annual_gross_pct != daily_excess×244，"
                   f"最大偏差 {row['_selfcheck_annualization_drift']:.3e}")
    g = row["hurdle_gap"]
    if g is None or g > hurdle_tol:
        bad.append(f"K={row['k']}: 重算门槛 {row['hurdle_recomputed']:.8f} "
                   f"与报告存的 {row['hurdle_in_report']} 不吻合")
    if not row["_selfcheck_linear"]:
        bad.append(f"K={row['k']}: 成本缩放非线性，0.55% 档的读数不能由 0.3% 档等比换算")
    return bad


def _t_cell(r: dict) -> str:
    """t 均那一格。

    缺失的年份**不参与平均**，但**必须显示出来**。
    K=63 时 2026 只有 1~2 个调仓块，`t_blocked` 是 `None`；
    把它当 0 会把「没算出来」读成「无效应」。
    """
    if r["n_t_obs"] == 0:
        return "—"
    cell = f"{r['avg_t_real']:.2f}"
    if r["n_t_obs"] != r["years"]:
        cell += f"（{r['n_t_obs']}/{r['years']} 年有值）"
    return cell


def fmt_table(rows: list[dict]) -> str:
    head = ("| K | 轮/年 | 门槛毛超额 | 实测毛超额 | 门槛倍数 | 净@0.55% | t 均 | 够线年数 |\n"
            "|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {r['k']} | {r['rounds_per_year']:.1f} | {r['hurdle_recomputed'] * 100:.2f}% | "
        f"{r['avg_gross'] * 100:.2f}% | {r['ratio']:.2f}× | "
        f"**{r['avg_net_real'] * 100:.2f}%** | {_t_cell(r)} | "
        f"{r['hit_years']}/{r['years']} |\n"
        for r in sorted(rows, key=lambda r: r["k"]))
    return head + body


def _fmt_t(v) -> str:
    """单年 t 的单元格。`None` → `—`，绝不 `.2f` 格式化 `None`。

    这是 `_finite_mean` 那个洞的**第二个出口**：聚合处挡住了，
    逐年表里再漏一次的话，崩溃会发生在「已经算完之后」，
    把已经算好的结果一起带走。
    """
    if v is None or not math.isfinite(float(v)):
        return "—"
    return f"{float(v):.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    rows, bad = [], []
    for p in args.reports:
        if not p.is_file():
            print(f"⏭  跳过不存在的报告：{p}")
            continue
        row = summarize(json.loads(p.read_text(encoding="utf-8")))
        rows.append(row)
        bad += selfcheck(row)

    if not rows:
        raise SystemExit("没有可读的报告 —— 不出表（宁可空着也不能出一张没对过口径的表）")
    if bad:
        for b in bad:
            print(f"🚨 {b}", flush=True)
        raise SystemExit("自查未过 —— 不出表")

    print(fmt_table(rows))
    for r in sorted(rows, key=lambda r: r["k"]):
        print(f"\n### K={r['k']} 逐年")
        print("| 年 | 毛超额 | 净@0.55% | t |")
        print("|---|---|---|---|")
        for y in sorted(r["per_year_gross"]):
            print(f"| {y} | {r['per_year_gross'][y] * 100:.2f}% | "
                  f"{r['per_year_net_real'][y] * 100:.2f}% | "
                  f"{_fmt_t(r['per_year_t_real'][y])} |")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"targets": {"TARGET_NET": TARGET_NET,
                               "COST_REAL_50K": COST_REAL_50K,
                               "DAYS_PER_YEAR": DAYS_PER_YEAR},
                   "rows": rows}
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        # ⚠️ 盖章必须在写完之后，且**只能靠真跑**拿 ——
        #   手写 sidecar 是形状 16（捏造一个与真章无法区分的文件），比红着更糟。
        provenance.stamp(args.json_out, "scripts/k_sweep_extract.py",
                         root=Path(__file__).resolve().parents[1],
                         extra={"n_rows": len(rows),
                                "DAYS_PER_YEAR": DAYS_PER_YEAR,
                                "COST_REAL_50K": COST_REAL_50K,
                                "TARGET_NET": TARGET_NET})
        print(f"\n已写 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())