"""α / β 收益分解 —— 石川《因子投资方法与实践》行 905 第 4 点。

> Bender et al. (2014)：管理人超额收益中 **80% 可由因子暴露（β）解释**，
> 剩下 20% 才取决于获得 **α** 的能力。

## 为什么做这个

月频多头口径毛超额 11.10%/年，但**逐年区间 −16.39% ~ +32.61%** ——
均值不大而方差巨大。这种形状通常意味着它**不是稳定的 α，
而是某个持续的因子暴露**：组合一直在承担一组固定的倾斜，
涨的那几年正好倾斜兑现。

**在分清它是什么之前，择时/止损都没有意义** ——
止损只能重新分配一个**可能根本不存在**的 α。

## 方法

对每一年的**毛超额**逐日序列回归：

    r_t = α + Σ_f β_f · x_{f,t} + ε_t

`x_{f,t}` 是**涨腿组合**（账户真实建仓的那一侧）对因子 f 的暴露：
持仓票的 `robust_z` 在**全横截面**上的均值。

## 三条纪律

1. **逐日毛超额与暴露都从 `backtest_walk_forward_weights.py` 的落盘文件读**，
   绝不自己重算 —— 重算就是造第二台测量仪，两台之间的差永远查不出来。
   暴露在那一侧产生时 `xs`（GB 级面板）还在手上；事后重算就得再加载一次，
   而并发面板作业就是本项目 OOM 的唯一来源。
2. **不许拿 Bender 的 80/20 当门槛。** 那是别人样本上的经验值，
   拿来当预注册门槛就是「先有结论后凑判据」。
   **本脚本只判「α 是否 > 0」，β 占比只报不判。**
3. **只用毛超额**，不用净值 —— 成本与「排序的信息量」无关，
   混进来会让分解同时回答两个问题。

用法：
    .venv/bin/python scripts/decompose_alpha_beta.py \
        --daily data/reports/wf_weights_backtest.daily.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.factor import FACTOR_NAMES
from aplus.hurdle import DAYS_PER_YEAR

#: 与判决同一口径：月频。
K = 21

#: 每个因子的暴露列名前缀。**必须**与产出侧一致，否则列全变 NaN，
#: 而回归会安静地把 NaN 行丢掉 —— 你只会看到「样本变少了」。
EXPO_PREFIX = "expo_"


def t_blocked(x, block: int = K) -> float:
    """分块 t —— **委派**给 `evolve_race._tstat`，不重写。

    K 日调仓时「选了谁」在 K 天内不变，相邻 K−1 天不是独立观测；
    直接求 t 会把 t 撑大约 √K 倍。

    为什么委派而不是写一份：本项目第一版是照着它重写了一遍，
    结果**放宽了两处**——块数下限 3→2，退化判据 `sd<=0` → `sd==0`。
    而 `sd<=0` 恰恰是项目里专门修过的坑（`pd.Series.std` 对常数序列
    返回 3.5e-18 而不是 0，于是 `t` 变成 3.1e16 的凭空巨值）。
    自己重写等于把已修好的坑重新挖开。
    """
    from aplus.evolve_race import _tstat
    s = pd.Series(np.asarray(x, float))
    s = s[np.isfinite(s)]
    t, n = _tstat(s, block=block)
    if t is None:
        return float("nan")
    return float(t)


def ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """带截距的最小二乘系数。`numpy.linalg.lstsq` 足够，少一个依赖
    就少一处「库版本变了结果跟着变」的不可控面。"""
    beta, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(X)), X]), y, rcond=None)
    return beta


def decompose_one(year: int, frame: pd.DataFrame, factors: list[str],
                  min_obs: int) -> dict | None:
    """分解单年。`frame` 已按年切片，列为 `gross_excess` + `expo_*`。

    `α` 是**截距**，`β 部分`是拟合值的均值减去截距。
    两者相加**恒等于**原均值 —— 这是恒等式，不是巧合；
    `tests/test_decompose_alpha_beta.py::test_alpha_plus_beta_equals_total`
    把这条恒等式钉死，防止「分解」悄悄漏掉一块。
    """
    y = frame["gross_excess"].to_numpy(float)
    X = frame[[f"{EXPO_PREFIX}{f}" for f in factors]].to_numpy(float)

    # 重复日期必须在这里挡掉，不能指望调用方记得去重。
    # 重复行会让同一天被数两遍：系数本身仍然算得出来（只是偏了），
    # 而报告里 `n` 与真实交易日数对不上 —— 没有任何异常抛出。
    dup = frame.index.duplicated(keep="first")
    if dup.any():
        y, X = y[~dup], X[~dup]

    keep = np.isfinite(y) & np.isfinite(X).all(axis=1)
    y, X = y[keep], X[keep]
    if len(y) < max(min_obs, len(factors) + 2):
        return None

    coef = ols(y, X)
    alpha_d = float(coef[0])
    fitted = np.column_stack([np.ones(len(X)), X]) @ coef
    resid = y - fitted
    beta_part = float((fitted - alpha_d).mean())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = float(1.0 - (resid ** 2).sum() / ss_tot) if ss_tot > 0 else float("nan")

    return {
        "year": year,
        "n": int(len(y)),
        "total_annual_pct": float(y.mean()) * DAYS_PER_YEAR,
        "alpha_annual_pct": alpha_d * DAYS_PER_YEAR,
        "beta_part_annual_pct": beta_part * DAYS_PER_YEAR,
        # ★ 水平占比**在 R² 很低时不可识别**，不报、不存、不打印。
        #   实测每年 β/|α| ≈ 1.0 —— α 与 β 互为镜像，谁都不是那个数。
        #   报出来会被读成「β 占了 3000%」，而它只是两个不可分的数在抵消。
        #   （与 `108.34%` 同一族：数字是真的，口径撑不住。）
        #   需要「β 占多少」时唯一可辩护的量是下面的 `r2`。
        "alpha_beta_cancellation": (abs(beta_part / alpha_d)
                                    if alpha_d else float("nan")),
        # 唯一被识别、且信息量最大的读数：涨腿**本身**的因子倾斜。
        # 它不需要回归就能测，也不依赖 R²。
        "mean_exposures": {f: float(X[:, i].mean()) for i, f in enumerate(factors)},
        # ★ R² 是**变异**份额，它**不是** Bender 那句「80% 由因子暴露解释」
        #   对应的量 —— 我把两者搞反过一次（第二次栽在「第 67 层：失败方向
        #   判断反了」上）。Bender 说的是**水平/收益**的份额，因为复利吃的是
        #   水平；而 R² 吃的是方差。**两个不同的统计量，不可互换。**
        #   ⇒ 既然目标是水平，本该报水平份额 —— 但水平份额在本数据上
        #     **不可识别**（见 `alpha_beta_cancellation` ≈ 1：α 与 β 互为镜像）。
        #     所以：**Bender 的问题在这份数据上答不了**，报 R² 只能当作
        #     「因子解释了 3.5% 的变异」这一独立事实陈述，不能拿它去谈 80%。
        "r2": r2,
        "alpha_t_blocked": t_blocked(resid + alpha_d),
        "resid_daily_sd": float(resid.std(ddof=1)) if len(resid) > 1 else float("nan"),
        "betas": {f: float(b) for f, b in zip(factors, coef[1:])},
    }


def main(argv=None) -> int:
    # argv 可注入 ⇒ CLI 本身可被测试直接跑，不用起子进程。
    started = time.time()          # 给产物自检当「这一轮」的下界
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", type=Path,
                    default=Path("data/reports/wf_weights_backtest.daily.parquet"))
    ap.add_argument("--factors", default="", help="逗号分隔，默认全部 8 个")
    ap.add_argument("--from-year", type=int, default=2021)
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/alpha_beta_decomp.json"))
    args = ap.parse_args(argv)

    if not args.daily.is_file():
        raise SystemExit(
            f"逐日产物不存在：{args.daily}\n"
            f"先跑 scripts/backtest_walk_forward_weights.py（它落盘 .daily.parquet，"
            f"内含毛超额 + 逐日因子暴露）")

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)

    df = pd.read_parquet(args.daily)
    df.index = df.index.astype(str)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    missing = [c for c in ["gross_excess"] + [f"{EXPO_PREFIX}{f}" for f in factors]
               if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{args.daily} 缺列：{missing}\n"
            f"这份产物多半是**旧格式**（只有 gross_excess，没有 expo_*）——"
            f"重跑产出侧即可，不要在这里补列。")

    # NaN 全列检查：全 NaN 的列会让「样本量」看起来正常而回归实际上
    # 什么也没测到（见 aplus/report_ready.py:is_nan_column）。
    from aplus.report_ready import is_nan_column
    dead = [c for c in df.columns if is_nan_column(df, c)]
    if dead:
        raise SystemExit(f"{args.daily} 里有全 NaN 列：{dead} —— "
                         f"分解会「跑成功」而什么也没测到。")

    years = sorted({int(str(d)[:4]) for d in df.index if int(str(d)[:4]) >= args.from_year})
    print(f"逐日毛超额 {len(df)} 天  {df.index.min()} ~ {df.index.max()}")
    print(f"因子 {len(factors)} 个 · K={K} · 判决年 {years}\n")

    rows = []
    for y in years:
        sub = df[[str(d).startswith(str(y)) for d in df.index]]
        r = decompose_one(y, sub, factors, min_obs=len(factors) + 12)
        if r is None:
            print(f"{y}  跳过：有效样本不足")
            continue
        rows.append(r)

    if not rows:
        raise SystemExit("没有可分解的年份")

    hdr = (f"{'年':<6}{'n':>5}{'合计/年':>10}{'α部分/年':>11}{'β部分/年':>11}"
           f"{'β/|α|':>8}{'R²':>8}{'α t':>8}")
    print(hdr)
    print("─" * 76)
    for r in rows:
        print(f"{r['year']:<6}{r['n']:>5}{r['total_annual_pct']:>10.2%}"
              f"{r['alpha_annual_pct']:>11.2%}{r['beta_part_annual_pct']:>11.2%}"
              f"{r['alpha_beta_cancellation']:>8.2f}{r['r2']:>8.3f}"
              f"{r['alpha_t_blocked']:>8.2f}")

    mean = lambda k: float(np.mean([r[k] for r in rows]))          # noqa: E731
    print(f"{'年均':<6}{'':>5}{mean('total_annual_pct'):>10.2%}"
          f"{mean('alpha_annual_pct'):>11.2%}{mean('beta_part_annual_pct'):>11.2%}"
          f"{mean('alpha_beta_cancellation'):>8.2f}{mean('r2'):>8.3f}"
          f"{mean('alpha_t_blocked'):>8.2f}")
    print("  「β/|α|」≈ 1 ⇒ α 与 β 互为镜像，**水平占比不可识别**，因此不报。")
    print("  ⇒ Bender 问的正是**水平**份额，所以在这份数据上**答不了**。")
    print("  「R²」= 因子暴露解释掉的**变异**份额（另一个量，不是他那个），")
    print("  只能当独立事实陈述，且是别人样本上的经验值，本项目**只报不判**。")

    n_pos = sum(1 for r in rows if r["alpha_annual_pct"] > 0)
    print(f"\n预注册判据：α > 0 的年份 = {n_pos}/{len(rows)}")
    if n_pos < len(rows):
        print("→ **α 不是每年都正** ⇒ 这份超额不能当成稳定异象反复收割。")
        print("  ⚠️ 但**不要**把这件事读成「主要是 β」：见上面 β/|α| 与 R² ——"
              "在这两个数这么小/这么低时，")
        print("     α 与 β 之分**本身没有被识别**。被识别的结论只有两条："
              "①暴露倾斜存在；")
        print("     ②这 8 个因子解释掉的变异极少。")
    print("（R² **只报不判** —— Bender 的 80/20 是别人样本上的经验值，"
          "当门槛就是先有结论后凑判据。）")

    # 涨腿真正在赌什么：逐年平均暴露，**不需要回归就能测**，也不依赖 R²。
    tilt = sorted(((f, float(np.mean([r["mean_exposures"][f] for r in rows])))
                   for f in factors), key=lambda kv: kv[1])
    print(f"\n涨腿的年均因子倾斜（robust_z 单位，{len(tilt)} 个，由负到正）")
    print("  " + "  ".join(f"{f} {v:+.2f}" for f, v in tilt))

    # 最大的几个 β 值得单独点名：它们是「组合其实一直在赌什么」的直接读数。
    all_b = {}
    for r in rows:
        for f, b in r["betas"].items():
            all_b.setdefault(f, []).append(b)
    ranked = sorted(((f, float(np.mean(v))) for f, v in all_b.items()),
                    key=lambda kv: -abs(kv[1]))
    print(f"\n年均 β（{len(ranked)} 个，按 |β| 排序）")
    print("  " + "  ".join(f"{f} {b:+.3f}" for f, b in ranked[:5]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"kind": "alpha_beta_decomp", "k": K, "factors": factors,
         "source_daily": str(args.daily),
         "caliber": ("多头腿（涨腿）毛超额，β=持仓在 robust_z 尺度上的全横截面均值；"
                     "空头腿不可执行故不参与"),
         "verdict": {"alpha_positive_years": n_pos, "n_years": len(rows)},
         "note": ("只用毛超额；水平占比不报（α/β 互为镜像，不可识别）；"
                  "R² 只报不判；判据「α>0」预注册于 docs/TODO_measure_sigma_r.md"),
         "mean_exposure_tilt": {f: v for f, v in tilt},
         "mean_betas": {f: b for f, b in ranked}, "years": rows},
        ensure_ascii=False, indent=2, default=float), encoding="utf-8")

    from aplus.report_ready import require_keys
    # ★ `min_mtime` 不能省：`require_keys` 的存在意义就是区分
    #   「这一轮写的」与「上一轮留下的旧文件」，而本项目读错报告
    #   正是这么发生的。不传它，这条守卫在**最该生效的场景里失效**。
    ok = require_keys(args.out, ["years.0.alpha_annual_pct",
                                 "years.0.mean_exposures",
                                 "mean_exposure_tilt",
                                 "verdict.alpha_positive_years"],
                      min_mtime=started)
    print(f"\n产物自检：{ok.reason}")
    print(f"→ {args.out}")
    return 0 if ok.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
