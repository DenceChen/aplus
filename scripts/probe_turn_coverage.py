"""股票池可比闸 6.7% 缺口的**真实分解**（2026-10-14）。

## 起因：一次归因错误

我曾断言「`turnratio` 缺 62%，单这一条就能解锁全部 4 组反转候选」。
实测推翻了它：

    短窗口阈值      救回      占合格池
    一根不缺          0        0.00%
    ceil(21×0.9)=19  0        0.00%    ← 我写的那版，等于没修
    ≥15 根          24        1.38%
    ≥10 根          26        1.50%    ← 最宽松的合理值

短窗口覆盖率是**双峰**的（要么完整，要么最近停牌缺一大截），
19/20 根这种形态几乎不存在。而闸门缺口是 6.7%——
最宽松的阈值也只够上 1.5%，**这条杠杆撑不起那个缺口**。

## 那缺口到底在哪

本脚本做**完整分解**：现任权重能排名的 4893 只里，
有多少是因为 `turnratio` 定义本身（252 日回看）而算不出来。

注意第一版探针里有一行 `if len(m) < TURN_RATIO_LONG: continue` ——
它把上市不足一年的股票**整个跳出了样本**，于是我量到的分母
天然小于现任的股票池。异常换手率**结构性地**排除掉所有次新股：
这不是数据缺陷，是因子定义的必然结果。

用法：
    .venv/bin/python scripts/probe_turn_coverage.py --data-root data --sample 0
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.panel import (MIN_TURN_COVERAGE, TURN_RATIO_LONG, TURN_RATIO_SHORT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--sample", type=int, default=0, help="0 = 全池")
    args = ap.parse_args()

    files = sorted((args.data_root / "master" / "daily").glob("*.parquet"))
    if not files:
        print(f"主库为空：{args.data_root / 'master' / 'daily'}")
        return 1
    if args.sample:
        step = max(1, len(files) // args.sample)
        files = files[::step]

    rows = []
    for p in files:
        try:
            m = pd.read_parquet(p)
        except Exception:
            continue
        if "turn" not in m.columns:
            continue
        n_bar = len(m)
        r = {"code": p.stem, "n_bar": n_bar}
        if n_bar < TURN_RATIO_LONG:
            # 上市不足一年 —— 异常换手率**定义上**就取不到 252 日均值。
            # 不是数据缺失，是因子回看长度与次新股年龄不兼容。
            r.update(long_cov=np.nan, short_cov=np.nan, n_short=0)
        else:
            t = m["turn"].astype(float).iloc[-TURN_RATIO_LONG:]
            short = m["turn"].astype(float).iloc[-TURN_RATIO_SHORT:]
            r.update(long_cov=len(t.dropna()) / TURN_RATIO_LONG,
                     short_cov=float(short.notna().sum()) / TURN_RATIO_SHORT,
                     n_short=int(short.notna().sum()))
        rows.append(r)

    df = pd.DataFrame(rows)
    if df.empty:
        print("没有可用的样本")
        return 1
    df.to_csv("/tmp/turn_coverage_probe.csv", index=False)

    # ── 缺口分解 ────────────────────────────────────────────────
    n_all = len(df)
    young = df["n_bar"] < TURN_RATIO_LONG
    mature = ~young
    print(f"样本 {n_all} 只（{'抽样' if args.sample else '全池'}）\n")
    print(f"{'上市不足 252 日（次新股）':<34}{int(young.sum()):>6} 只  "
          f"{young.mean():>7.2%}")
    print(f"{'上市 ≥252 日':<34}{int(mature.sum()):>6} 只  "
          f"{mature.mean():>7.2%}")
    dfm = df[mature]
    long_ok = dfm["long_cov"] >= MIN_TURN_COVERAGE
    short_ok = dfm["short_cov"] >= MIN_TURN_COVERAGE
    tr_ok = long_ok & short_ok
    print(f"{'  └ 其中长窗口覆盖 ≥0.9':<34}{int(long_ok.sum()):>6} 只  "
          f"{long_ok.mean():>7.2%}")
    print(f"{'  └ 且短窗口覆盖 ≥0.9':<34}{int(tr_ok.sum()):>6} 只  "
          f"{tr_ok.mean():>7.2%}")
    print(f"\nturnratio 可算率 = {tr_ok.sum()} / {n_all} = "
          f"{tr_ok.sum() / n_all:.2%}")
    print(f"换手率完全不可用的 = {(~tr_ok).sum()} 只 = {(~tr_ok).mean():.2%}")

    if len(dfm):
        print("\n" + "─" * 60)
        print(f"{'':28}{'252 日窗口':>12}{'21 日窗口':>12}")
        print("─" * 60)
        print(f"{'覆盖率中位数':<26}{dfm['long_cov'].median():>12.3f}"
              f"{dfm['short_cov'].median():>12.3f}")
        print(f"{'覆盖率 <0.9 的只数':<24}{(~long_ok).sum():>12}"
              f"{(~short_ok).sum():>12}")
        print(f"{'覆盖率 ==1.0 的只数':<24}{(dfm['long_cov'] >= 1).sum():>12}"
              f"{(dfm['short_cov'] >= 1).sum():>12}")

    print("\n【关键】长窗口已过门槛、但短窗口没过的：")
    both = dfm[long_ok & ~short_ok]
    nq = max(1, int(long_ok.sum()))
    print(f"    {len(both)} 只 / {nq} 只长窗口合格 = {len(both) / nq:.1%}")
    if len(both):
        print("    短窗口有效根数分布：")
        print(both["n_short"].value_counts().sort_index().to_string())
        print("\n    各候选阈值能救回多少只：")
        for thr, label in (
            (21, "pandas 默认：一根不缺"),
            (19, "0.9 覆盖率 = ceil(21×0.9)  ← 我写的那版"),
            (18, "int(21×0.9)"),
            (15, "窗口的一半以上"),
            (11, "0.5 覆盖率"),
            (10, "绝对下限 10 根"),
        ):
            n = int((both["n_short"] >= thr).sum())
            print(f"      ≥{thr:>2} 根  救回 {n:>3} 只 = {n / nq:>6.2%} 的合格池"
                  f"   {label}")

    rev = dfm[~long_ok & short_ok]
    print(f"\n【反方向】短窗口够、长窗口不够：{len(rev)} 只 "
          f"（{len(rev) / max(1, int(short_ok.sum())):.1%} of 短窗口合格）")

    print("\n→ /tmp/turn_coverage_probe.csv")
    return 0

    print("\n→ /tmp/turn_coverage_probe.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
