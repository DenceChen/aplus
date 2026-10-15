"""篮子宽度 × 测量下限 —— 「测不准」和「没有」分开。

**判据已预注册**：`docs/TODO_basket_width_2026-10-15.md`
模型、阈值、判决分支全在那边写死。**本脚本只负责执行。**

## 起因（一句话）

当前榜宽 10 只 ⇒ 逐日超额 sd = 0.8419%/日 ⇒ **年化 sd = 13.15%/年**
⇒ **MDE = 15.41%/年**，而实测毛超额只有 6.52%/年，**远在测量下限之下**。
⇒ 「没测出显著」目前**无法区分**「没有 alpha」和「有但测不出」。

🚨 这里曾印着「sd = 205%/年」—— 那是 `sd × days_per_year` 而不是
  `sd × √days_per_year` 算出来的，**大了 15.6 倍**（= √244）。
  它没污染任何判决（`decide()` 不读 sd；σ(N) 比值里 √244 约掉），
  受害的只有这些字。守卫见 `tests/test_annualization_is_a_sqrt_of_time.py`。

而 MDE ∝ sd，sd ∝ ~1/√N。⇒ **放宽榜宽是唯一能分开这两件事的杠杆。**

★ **放宽榜宽不是改产品，是改测量仪器。**
  宽榜取证（分母小），窄榜下注（期望高）。
  用户的 5 万本金对应合理区间是 20–30 只，不是 10 只。

## 三个刻意写死的约束

1. **窗口必须等于判决窗口**（2021-01-01 ~ 数据末端）。
   `basket_width_sweep.py` 里已有的是 2019–2023 —— **不是同一个量**，一律不得引用。
2. **`MIN_BASKET` 必须按榜宽比例化**。
   它现在是绝对值 3（`aplus/verify.py`），榜宽 200 时
   「200 只里只剩 5 只有 bar」照样计入 ⇒ 测出来的是噪声放大。
3. **`sd ∝ 1/√N` 是假设，不是结论。** 本脚本**实测** sd(N) 并与理论值对照，
   不拿假设去替代测量。

## 用法

```bash
.venv/bin/python scripts/factor_width_mde.py
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import begin_run, configure_noisy_stderr  # noqa: E402

configure_noisy_stderr()

import aplus.verify as verify_mod  # noqa: E402
from aplus.backtest import STOCK_COST_DEFAULT, build_cross_sections  # noqa: E402
from aplus.composite import unpack  # noqa: E402
from aplus.factor import FACTOR_NAMES  # noqa: E402
from aplus.costs import annual_friction  # noqa: E402
from aplus.hurdle import COST_REAL_50K, DAYS_PER_YEAR, hurdle_rate, rounds_per_year  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus import provenance  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

# ★ 权重也**从主回测 import**，不自己写一份。
#   `weights_by_year` 就定义在那个文件里（不在 aplus.composite —— 我第一次
#   按字面猜去 import 它，ImportError 才发现）。
#   自己重写一遍 = 造第二台测量仪，两台之间的差永远查不出来。
from backtest_walk_forward_weights import (ENTRY_LAG, at_cost, _daily,  # noqa: E402
                                           _rescale_check, _t, run_year,
                                           weights_by_year)
from factor_attribution import T_CRIT_5PCT, power_floor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# 预注册 §4.1
WIDTHS = [10, 30, 100, 200, 546]
PRIMARY_WIDTH = 200
K = 21
ALPHA = 0.05

# 预注册 §4.2.1：N ≥ 50 时 MIN_BASKET 按比例取
MIN_BASKET_BASE = 3          # aplus/verify.py 现值（绝对值）
MIN_BASKET_PROPORTIONAL_FROM = 50
MIN_BASKET_DIVISOR = 4       # max(10, N // 4)

# 预注册 §7.1（**在看到任何宽度数据之前**定死）：「接近 1/√N」的判定带。
# 第一版代码里直接写着 0.7/1.4，而文档 §4.3 只写了「接近」两个字 ——
# **先有码、后补文档**正是 CLAUDE.md 里记的「先推后读」那个病。
# ⇒ 数字现在由文档单点持有，代码只引用。
NEAR_THEORY_LO = 0.70
NEAR_THEORY_HI = 1.40

# ── 成本：宽榜必须按名数算，不能乘那个为 10 仓位标定的常数 ──
# ★ `COST_REAL_50K = 0.0055` 是 **N=10 的实测值**（`costs.FeeSchedule` 逐笔算得 6.41%/年，
#   常数 ×11.6 次 = 6.39%，1.0×）。它是**每笔比例**，与名数无关；
#   而 A 股**单边最低佣金 ¥5 是每笔固定额**，单笔越小占比越高。
#   ⇒ 第一版直接用它乘遍五个档，于是「净收益随榜宽单调上升 +0.49→+5.05%」——
#     **整列是 artifact**。真实值在 N=546 是 −121%/年（摩擦 131%/年）。
#   守卫：`tests/test_cost_is_calibrated_to_one_width_only.py`
CAPITAL_50K = 50_000.0
EQUITY_WEIGHT = 1.0          # 本策略满仓权益，无杠杆、无现金缓冲
TARGET_NET = 0.20            # 用户目标：年化 20% 净值（aplus.hurdle.TARGET_NET）
# ★ `COST_REAL_50K` 只在 N ≤ 这个值时与逐笔计算相符（实测 N=10 为 1.0×，
#   N=21 为 1.4×、N=30 为 1.7×）。写成一个**可读的门槛**而不是注释里的口头提醒。
VOID_ABOVE_N = 10


def width_aware_friction(n: int, k: int) -> float:
    """给定名数与调仓周期，返回**占权益**的年化摩擦。

    ★ 用 `costs.FeeSchedule` 而不是自己写一份费率 ——
      最低佣金是非线性的（逐笔 `max(额×万三, ¥5)`），
      先求和再乘费率会把成本凭空少掉数倍（本项目已踩过一次）。
    """
    return annual_friction(CAPITAL_50K, EQUITY_WEIGHT, n,
                           rounds_per_year(k)) / EQUITY_WEIGHT


def scaled_min_basket(n: int) -> int:
    """榜宽 → 有效成分下限。**N ≥ 50 时必须比例化**（预注册 §4.2.1）。

    绝对值 3 在宽榜上是危险的：一批 200 只里只剩 5 只都有 bar 的那天，
    `groupby().mean()` 照样算出一个「篮子超额」——
    而那个数是 5 只票的收益，**噪声比信号大得多**。
    """
    if n < MIN_BASKET_PROPORTIONAL_FROM:
        return MIN_BASKET_BASE
    return max(10, n // MIN_BASKET_DIVISOR)


def series_from_chunks(chunks: list) -> pd.Series:
    """把逐年 chunk 的毛超额拼成一条逐日序列（**跨年同一天只留一个**）。"""
    if not chunks:
        return pd.Series(dtype=float)
    s: dict[str, float] = {}
    for c in chunks:                      # 后写覆盖先写 = keep="last"
        for d, v in zip(c["dates"], c["excess"]):
            s[str(d)[:10]] = float(v)
    return pd.Series(s).sort_index()


def decide(primary: dict) -> tuple[str | None, str]:
    """预注册 §7.2 的判决分支（最终版，与本文逐条对齐）。

    **提成独立函数是为了让守卫能直接调它** ——
    测试里把同样的筛选逻辑再写一遍，验的只是概念，
    变异实现它抓不到（与「成本被扣两次」同源：判断量与被判断量必须同源）。

    ★ 这里**只有主档 N=200 的判决**。
      跨档观察（「放宽后超额反而下降 ⇒ 收益来自集中持股」）
      **故意不并进来**：`verdict` 是单档判决，被跨档结论占掉的话，
      **主档的显著性判决就整个消失了**。见 `cross_width_note()`。

    ★★ **这四条是「特例 → 一般」的嵌套链，不是四个独立条件**
    （2026-10-15 实测定性，纠正一处审计误判）：

        WIDE_MEASURABLE ⊂ SIG_BUT_CORRELATED ⊂ SIGNIFICANT_BUT_NEGATIVE ⊂ 其余

    布尔真值表 8/8 验证：`WIDE_MEASURABLE` 的条件恒蕴含 `SIG_BUT_CORRELATED` 的条件。
    ⇒ 「分支重叠」在这里**不是缺陷**，量的只是特例分支的触发频率。

    🚨 **风险方向与审计结论正相反**：若按「有重叠就重排」把它改成四条独立条件，
    或把 `SIG_BUT_CORRELATED` 提前，`WIDE_MEASURABLE` 会在 `(sig, pos, near)`
    这一个输入组合上变成**永不触发的死代码** ——「进前瞻」这条路整个消失，
    而剩下的判决看起来完全正常。

    ⇒ 且这是**预注册 §7.2** 的判据，**顺序本身就是判据，不得改动**。
      守卫：`tests/test_factor_width_mde.py::test_the_verdict_branches_form_a_nested_chain_not_four_conditions`。
    """
    t_blk = primary.get("t_blocked")
    gross = primary.get("gross_annual_pct", 0.0)
    ratio = primary.get("sd_ratio_obs_over_theory", float("nan"))
    sig = t_blk is not None and abs(t_blk) > T_CRIT_5PCT
    near_theory = NEAR_THEORY_LO <= ratio <= NEAR_THEORY_HI

    # ★ 嵌套链：每一条都是上一条的**放宽**。书写顺序 = 特例优先 = 唯一可行的顺序。
    if sig and gross > 0 and near_theory:                    # ⊂ 下一条
        return "WIDE_MEASURABLE", "N=200 超额显著且 σ 接近 1/√N ⇒ 取证成立，进前瞻"
    if sig and gross > 0:                                   # ⊂ 下一条
        return ("SIG_BUT_CORRELATED",
                f"显著但 σ 实测是 1/√N 理论的 {ratio:.2f} 倍 ⇒ 相关性高，"
                f"分散化收益有限；记录实测曲线，**不硬套假设**")
    if sig:                                                 # 最宽的一条
        return "SIGNIFICANT_BUT_NEGATIVE", f"N=200 显著但方向为负（{gross:+.2%}/年）"
    return ("NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH",
            "N=200 仍不显著 ⇒ 在**能测的量级上**没有可证明的超额。"
            " ⚠️ 这不等于「没有超额」—— 只等于「测不到」。")


def cross_width_note(rows: dict) -> str:
    """预注册 §7.2 的**跨档观察** —— 独立于 `verdict` 的第二通道。

    「放宽榜宽后毛超额反而下降」是一个**跨档**结论，
    而 `decide()` 只看主档 N=200。把它并进 `verdict` 会**顶掉主档判决**。

    ⚠️ 因此本函数**只产出一句话**，不产出判决。
      守卫 `test_cross_width_note_never_becomes_the_verdict` 钉住这一点。
    """
    ns = sorted((int(k) for k in rows), key=int)
    if len(ns) < 2:
        return "档位不足，不构成跨档观察"
    narrow, wide = rows[str(ns[0])], rows[str(ns[-1])]
    gn, gw = narrow["gross_annual_pct"], wide["gross_annual_pct"]
    if gw < gn:
        return (f"⚠️ 跨档观察：最宽档 N={ns[-1]} 的毛超额 {gw:+.2%}/年 "
                f"**低于**最窄档 N={ns[0]} 的 {gn:+.2%}/年 "
                f"⇒ **收益来自集中持股，不是因子** ⇒ 下一步是**逐票研究，不是因子研究**。")
    return (f"跨档观察：最宽档 N={ns[-1]} 毛超额 {gw:+.2%}/年 "
            f"≥ 最窄档 N={ns[0]} 的 {gn:+.2%}/年 ⇒ 放宽未损伤毛超额。")


def run_widths(xs, wmap, judged, by_year, widths, k) -> dict:
    """逐个榜宽跑一遍，返回 `{str(N): 那一档的数字}`。

    ★ 提成独立函数是为了让守卫**不碰面板**。
      第一版这段逻辑内联在 `main()` 里，于是
      `test_min_basket_is_restored_after_the_run` 只能去调 `main()` ——
      而 `main()` 会去读 5460 个日线 parquet（GB 级），
      单测因此挂住 120s，还和后台的 K 扫描抢内存。
      **守卫测试触发最贵的那一步，是守卫设计失败。**

    `aplus.verify.MIN_BASKET` 是全局量，这里改了必须在 `finally` 里还原 ——
    否则同进程后续的每日任务会静默按 N=546 的成分下限去跑 N=10。
    """
    rows, sd_theory_ref = {}, None
    base_n = widths[0]
    original_min_basket = verify_mod.MIN_BASKET
    try:
        for n in widths:
            mb = scaled_min_basket(n)
            verify_mod.MIN_BASKET = mb        # `_daily_series` 在调用时读这个模块属性
            chunks: list = []
            per_year = []
            for y in judged:
                r = run_year(xs, wmap[y]["weights"], by_year[y], n, n, k,
                             daily_out=chunks)
                if r.get("n_rows", 0):
                    per_year.append(r)
            series = series_from_chunks(chunks)
            if len(series) < 60:
                print(f"{n:>5}{mb:>8}{len(series):>7}  样本太少，不构成测量")
                continue

            pf = power_floor(series.to_numpy(float))
            t_blk, n_obs = _t(series, block=k)

            if sd_theory_ref is None:
                sd_theory_ref = pf["annual_sd_pct"]        # 基准榜宽
            # σ(N) ∝ 1/√N 的理论值（**假设**，用实测对照）
            sd_theory = sd_theory_ref / np.sqrt(n / base_n)
            ratio_obs = pf["annual_sd_pct"] / sd_theory if sd_theory else float("nan")

            rows[str(n)] = {
                "n": n, "min_basket": mb, "n_days": int(len(series)),
                "gross_annual_pct": pf["annual_mean_pct"],
                "annual_sd_pct": pf["annual_sd_pct"],
                "MDE_annual_pct": pf["MDE_annual_pct"],
                "t_naive": pf["t_naive"],
                "t_blocked": float(t_blk) if t_blk is not None else None,
                "n_independent_obs": int(n_obs) if n_obs is not None else None,
                "detectable": bool(abs(pf["annual_mean_pct"]) > pf["MDE_annual_pct"]),
                "sd_ratio_obs_over_theory": float(ratio_obs),
                "sd_theory_pct": float(sd_theory),
                "net_real_annual_pct": (float(np.mean([r["real"]["annual_net_pct"]
                                                        for r in per_year]))
                                        if per_year else None),
                # 🚨 上面那个是**乘 10 仓位标定常数**算出来的，只在 N=10 成立。
                #    下面两个才是宽榜该用的：摩擦按名数逐笔算。
                "net_width_aware_annual_pct": float(
                    pf["annual_mean_pct"] - width_aware_friction(n, k)),
                "friction_width_aware_annual_pct": float(width_aware_friction(n, k)),
                "hurdle_width_aware_annual_pct": float(
                    TARGET_NET + width_aware_friction(n, k)),
                "net_flat_constant_is_void_above_n": VOID_ABOVE_N,
                "hit_threshold_any_year": bool(any(r["real"]["hit_threshold"]
                                                   for r in per_year)),
                "min_basket_absolute_base": MIN_BASKET_BASE,
                "min_basket_was_scaled": mb != MIN_BASKET_BASE,
            }
            # ★ `_pct` 字段存的是**分数**（0.154 = 15.4%），
            #   第一版这里用 `:>8.2f}%` 直接把裸数字当百分数印 ⇒ MDE 印成「0.15%」，
            #   而紧挨着的判决行写「低于测量下限」—— 6.52% > 0.15% 看着自相矛盾。
            #   一律用 `%` 格式说明符（它自己乘 100），不许手拼 `%`。
            fr = width_aware_friction(n, k)
            net_wa = pf["annual_mean_pct"] - fr
            print(f"{n:>5}{mb:>8}{len(series):>7}{pf['annual_mean_pct']:>11.2%}"
                  f"{pf['annual_sd_pct']:>9.2%}{pf['MDE_annual_pct']:>9.2%}"
                  f"{(t_blk if t_blk is not None else float('nan')):>9.2f}"
                  f"{str(n_obs):>9}{ratio_obs:>12.2f}x{fr:>10.2%}{net_wa:>10.2%}")
            print(f"{'':>5}{'':>8}{'':>7}└─ "
                  f"{'[OK] 可测' if abs(pf['annual_mean_pct']) > pf['MDE_annual_pct'] else '[X] 低于测量下限'}"
                  f"（均值 {pf['annual_mean_pct']:.2%} vs MDE {pf['MDE_annual_pct']:.2%}）"
                  f"｜真实摩擦 {fr:.2%} → 真实净 {net_wa:.2%}"
                  f"｜门槛(真实摩擦口径) {TARGET_NET + fr:.2%}")
            sys.stdout.flush()
    finally:
        verify_mod.MIN_BASKET = original_min_basket      # ★ 无论如何都要还原
    return rows


# 测试用别名：语义上「这就是判决函数本身」，不是替身。
decide_for_test = decide


def main(argv=None) -> int:
    """薄包装：把真正的活放进 `with begin_run(...)`。

    ★ 第一版写的是 `run = begin_run(...)` 然后结尾 `run.done()` ——
      `begin_run` 是 `@contextmanager`，**不是**这样用的。后果不是「报错」而是
      **账本里只有 `run_start`、没有 `run_end`**：
      「跑了 557 秒正常收尾」与「写到最后一个字崩了」在日志里长得一模一样。
      崩点还恰好在 JSON 落盘之后 ⇒ **产物在，账本对产物只字未提**。

      这是第 89 层的表亲：**「开始」被记录、「结束」没有 ⇒ 二者不可区分。**
    """
    args = _parse_args(argv)
    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    with begin_run("factor_width_mde", widths=widths, start=args.start):
        return _run(args)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01",
                    help="★ 必须与判决窗口一致（2021-01-01），不得改成 2019")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--matrices", type=Path, default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--widths", default=",".join(str(w) for w in WIDTHS))
    ap.add_argument("--out", type=Path, default=Path("data/reports/factor_width_mde.json"))
    return ap.parse_args(argv)


def _run(args) -> int:
    # ★ 第 125 层：开跑时抓一次本文件的字节，盖章时比对。
    #   这一档要跑 ~14 分钟；期间若有人改了本文件，章会盖上**没跑过的字节**，
    #   而那种章不是弱证据、是**反向的错误证据** ⇒ 不一致就拒绝盖章。
    #
    # ⚠️ 必须抓在**这里**、`_run` 的第一行。两个位置都不对：
    #   · 放 `main()` 里 ⇒ `_run` 读不到（NameError 落在最后一行盖章处，
    #     而那已经**写完产物**了 ⇒ 产物在、章没有，第 131 层那一颗雷）
    #   · 放盖章**前**一行 ⇒ 「开跑的字节」变成「盖章的字节」⇒ 检查恒真，
    #     正是第 125 层要防的那种装饰性守卫
    expect_bytes = provenance.code_file_sha256(ROOT, "scripts/factor_width_mde.py")
    widths = [int(w) for w in args.widths.split(",") if w.strip()]

    # ── 窗口守卫：口径必须与判决一致 ──
    if not args.start.startswith("2021"):
        print(f"🚨 窗口 {args.start} 与判决窗口（2021 起）**不是同一个量**。"
              f" `basket_width_sweep.json` 里的 2019–2023 数字正是这样被误用的。",
              file=sys.stderr)
        return 2

    lin = _rescale_check()
    if not lin["linear"]:
        print(f"🚨 成本非线性，at_cost 的缩放不再精确：{lin}", file=sys.stderr)
        return 2

    if not args.matrices.is_file():
        print(f"🚨 抽取表不存在：{args.matrices}", file=sys.stderr)
        return 2

    factors = list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)
    unpack(df, factors)
    wmap = weights_by_year(df, factors)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）", flush=True)

    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        print("🚨 没有任何判决年份")
        return 1

    print(f"\n判决年 {judged}｜K={K}｜门槛(年毛超额)="
          f"{hurdle_rate(rounds_per_year=rounds_per_year(K)):.2%}"
          f"｜成本 实测链（P0 已修）")
    print(f"MIN_BASKET：N<50 取 {MIN_BASKET_BASE}（绝对值，N=10 够用）；"
          f"N≥50 取 max(10, N//{MIN_BASKET_DIVISOR})")
    print(f"🚨 成本：末两列用 `costs.FeeSchedule` **逐笔**算（含 ¥5 最低佣金），"
          f"按名数变化。\n   旧的固定常数 {COST_REAL_50K:.2%}/轮 是 **N=10 的标定值**，"
          f"N>{VOID_ABOVE_N} 起不可用（N=546 低估 20.5×）。")
    print("─" * 128)
    print(f"{'N':>5}{'MIN_BK':>8}{'天数':>7}{'毛超额/年':>11}{'sd/年':>9}"
          f"{'MDE/年':>9}{'t(分块)':>9}{'独立观测':>9}{'σ实测/σ理论':>13}"
          f"{'真实摩擦':>10}{'真实净':>10}")

    rows = run_widths(xs, wmap, judged, by_year, widths, K)

    print("─" * 104)

    # ── 判决（分支写死在预注册 §4.3）──
    primary = rows.get(str(PRIMARY_WIDTH))
    verdict, branch = (None, f"主档 N={PRIMARY_WIDTH} 未跑")
    if primary:
        verdict, branch = decide(primary)

    print(f"判决分支：{branch}")
    if verdict:
        print(f"判决：{verdict}")

    # ── 跨档观察：**独立通道**，不写进 verdict（预注册 §7.2）──
    note = cross_width_note(rows)
    print(note)

    out = {
        "generated": date.today().isoformat(),
        "prereg": "docs/TODO_basket_width_2026-10-15.md",
        "window": [args.start, end],
        "window_note": "★ 与判决窗口一致；`basket_width_sweep.json` 的 2019–2023 不是同一个量",
        "k_rebalance_every": K,
        "alpha_two_sided": ALPHA,
        "t_crit": T_CRIT_5PCT,
        "t_crit_source": "math.erf 二分反解（标准库），无查表、无手打",
        "hurdle_gross_annual": hurdle_rate(rounds_per_year=rounds_per_year(K)),
        # ★ 成本口径**必须随数字一起落盘**（第 7 层）
        "cost_model": {
            "wide_basket": "costs.FeeSchedule 逐笔（含 ¥5 最低佣金），按 n_positions=N",
            "capital": CAPITAL_50K,
            "equity_weight": EQUITY_WEIGHT,
            "rebalances_per_year": rounds_per_year(K),
            "flat_constant": COST_REAL_50K,
            "flat_constant_is_calibrated_for_n": 10,
            "flat_constant_void_above_n": VOID_ABOVE_N,
            "void_field": "widths[*].net_real_annual_pct —— 乘标定常数，N>10 时无意义",
            "use_instead": "widths[*].net_width_aware_annual_pct",
        },
        "min_basket_rule": (f"N<{MIN_BASKET_PROPORTIONAL_FROM} → {MIN_BASKET_BASE}（绝对值）；"
                            f"N≥{MIN_BASKET_PROPORTIONAL_FROM} → max(10, N//{MIN_BASKET_DIVISOR})"),
        "assumption_under_test": "σ ∝ 1/√N —— **假设**，由 sd_ratio_obs_over_theory 实测对照",
        "widths": rows,
        "verdict": verdict,
        "verdict_branch": branch,
        # ★ 跨档观察**单列**，绝不与 verdict 混在一格里（预注册 §7.2）
        "cross_width_note": note,
        "near_theory_band": [NEAR_THEORY_LO, NEAR_THEORY_HI],
        "what_this_cannot_prove": [
            "全样本内（权重用同一段历史拟合）⇒ 量级偏乐观，只有符号与排序有参考价值",
            "榜宽是产品设计参数，不是自由参数 ⇒ 由此挑出的 N 不可外推到别的资金量",
            "本实验不构成任何前瞻证据",
            "「N=200 不显著」只等于「测不到」，**不等于「没有超额」**",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # ★ 溯源章。`DEFAULT_GENERATORS` 里有这个名字**不等于**真有 sidecar ——
    #   两者之间没有自动通路（第 6 种形状：看起来接好了，实际焊死）。
    #   守卫 `test_registered_is_not_enough_the_writer_must_actually_stamp`
    #   用 AST 核这一行，不核子串。
    provenance.stamp(args.out, "scripts/factor_width_mde.py", root=ROOT,
                     data_root=args.data_root, expect_bytes=expect_bytes)
    print(f"\n→ {args.out}｜总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
