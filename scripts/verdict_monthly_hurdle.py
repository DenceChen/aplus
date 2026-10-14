"""用 `factor_ic_*.json` 的**逐年低尾 IC** 对照月频门槛，出判决。

## 为什么单独一个脚本

`docs/TODO_measure_sigma_r.md` 里**预注册**过判据，在看到数据之前：

    月频过关所需的 rev5 低尾 IC（σ_r = 0.02656 实测）= 0.04167
    8 年里 0 年低于它  → 月频可行
    任何一年低于它    → 那个年不过线

预注册的意义就是**不许看完数据再定标准**。所以这段逻辑必须
**机械执行**：读报告、算年数、打印结论。没有可调的口径，
也就不存在「把标准调到刚好能过」这回事。

## 它回答什么

整条月频路线压在 `rev5` 低尾 IC = 0.04863 这**一个数**上。
全截面 IC 已经自证逐年能差 4 倍（2019 的 0.0274 → 2026 的 0.0103），
而低尾的逐年分解此前**报告里根本没有**。

于是这句话「月频可行」背后是一个**没有分布的单点估计**——
而 σ_r 实测后它已经够到门槛了（30.80% vs 26.4%），
于是「够得着」这件事**全部押在低尾能否逐年守住**上。

用法：
    .venv/bin/python scripts/verdict_monthly_hurdle.py \
        --report data/reports/factor_ic_7y_v2.json --factor rev5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.hurdle import DAYS_PER_YEAR, hurdle_rate, rounds_per_year
from aplus.ic_to_return import annualise, implied_spread, required_ic

#: σ_r **实测值**，出处 `data/reports/xsec_sigma_r.json`（带溯源章）。
#: 不写死在这里是为了让改动留下痕迹——**换值必须同时更新那个报告**。
SIGMA_R = 0.02656


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=Path(
        "data/reports/factor_ic_7y_v2.json"))
    ap.add_argument("--factor", default="rev5")
    ap.add_argument("--bucket", default="tail_low")
    args = ap.parse_args()

    if not args.report.is_file():
        raise SystemExit(f"报告不存在：{args.report}")

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    yearly = payload.get("yearly") or []
    if not yearly:
        raise SystemExit("报告里没有 yearly 段")

    # 该因子该档的逐年 IC——缺档就**报错**，不回落。
    # 回落会让读者以为看到的是低尾，实际看到的是全截面。
    rows = [r for r in yearly if r.get("factor") == args.factor]
    if not rows:
        raise SystemExit(f"yearly 里没有因子 {args.factor}")
    missing = [r for r in rows if r.get(args.bucket) is None]
    if missing:
        raise SystemExit(
            f"{len(missing)} 个年份缺 `{args.bucket}` 档——"
            "这是**口径不一致**（全段表出三档、逐年表只出一档）。"
            "请重跑 scripts/factor_ic_diagnostic.py。"
        )

    # 判决线按**当年自己的 σ_r** 之外的全段值算一次，保持可复算
    need = hurdle_rate(rounds_per_year=rounds_per_year(21))
    ic_need = required_ic(need / DAYS_PER_YEAR, sigma_r=SIGMA_R)

    print(f"月频门槛（K=21，年调仓 {rounds_per_year(21):.1f} 次）{need:.2%}")
    print(f"σ_r（实测 {SIGMA_R}）→ 过关所需 IC = {ic_need:.5f}\n")

    hdr = f"{'年':<6}{'n_days':>8}{'低尾IC':>10}{'年毛超额':>11}{'判决':>10}"
    print(hdr)
    print("─" * (6 + 8 + 10 + 11 + 10))

    n_fail = 0
    for r in sorted(rows, key=lambda x: x["year"]):
        ic = float(r[args.bucket])
        gross = annualise(implied_spread(ic, sigma_r=SIGMA_R))
        ok = gross >= need
        n_fail += (not ok)
        print(f"{r['year']:<6}{r.get('n_days', 0):>8}"
              f"{ic:>10.5f}{gross:>10.2%}"
              f"{('✅ 过' if ok else '❌ 差'):>10}"
              f"  {(gross - need) * 100:+.2f}pp")

    print()
    print(f"不及格年份：{n_fail} / {len(rows)}")
    if n_fail == 0:
        print("→ **每个单独年份都够得着**——这是「月频可行」成立的必要条件。")
        print("  但它**不是充分条件**：本脚本只做**信号侧**判决，")
        print("  不含成本假设（成本 0.55%/轮 待对齐）与实盘滑点。")
    else:
        print(f"→ **{n_fail} 个年份够不到**：那个年不过线，")
        print("  所以「月频可行」**不成立**——全段平均值高估了实际可用水平。")

    print(f"\n（报告：{args.report}  档：{args.bucket}  因子：{args.factor}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
