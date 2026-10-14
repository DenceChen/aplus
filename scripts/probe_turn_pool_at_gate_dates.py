"""在**闸门真正看的那 5 天**上量 `turnratio` 的股票池 —— 缩短长窗口能不能救闸。

## 这个脚本要回答什么

7 年赛马（`data/reports/ic_guided_race_7y.json`）里，现任权重
`{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}` 在测试段 `n_ranked=4893`，
而**所有**含 `turnratio` 的候选都是 `4567`，窄 6.76%，被第六道闸
（股票池可比，门槛 2%，`aplus/evolve_race.py:594`）全部拦下。
问的是：**把 `TURN_RATIO_LONG` 从 252 缩短到多短，缺口才压进 2%？**

## 上一版探针错在哪（`scripts/probe_turn_window_vs_pool_gate.py`）

它量的是**文件最后一根 bar**（2026-10 前后）的池子，得到 L=42 → 5435 只、
L=252 → 5344 只，**全都比 4893 还大 9~11%**，看上去"缩短窗口毫无用处"。

但闸门读的不是最后一天。`backtest.py:755` 走的是
`sampled_ranked_count(cross_sections, use_dates, weights)`，而
`use_dates` 是**测试段**那 753 个交易日（`scripts/ic_guided_race.py:208`
的 `split_window`），函数内部再等距抽 5 天取**中位数**
（`backtest.py:791-800`）。测试段跨 2023-08 ~ 2026-10，而**次新股是随
时间累积进来的**：2023 年那批池子里的次新股远少于 2026 年。

同一个 L（252）在 2026-10-09 能收 5344 只，在 2023-08-25 收不到这个数。
闸门看的是 5 天的**中位数**，第一根抽样日恰恰落在测试段最开头、次新股最少
的位置上——那才是缺口最致命的一天。**在今天量、却拿去回答闸门的题，
量的是一个闸门从来没看过的数。** 结论因此完全反向。

## 本脚本怎么量

`use_dates` 的算法照抄 `backtest.py:767-801`：`panel.dates` = 最长那只票的
全部交易日 ∩ `[start, end)`（`panel.py:687`），`start/end` 取报告的
`window`（`end` 是**开区间**），再 `split_window(TRAIN_RATIO=0.6,
EMBARGO_DAYS=5)`。报告的 `test_dates` 字段只是 `[首, 尾]` 两个端点，
不是完整列表，所以 5 个抽样日是自己重算的——算出来首尾是
`2023-08-25 / 2026-10-09`，与报告 `test_window` 逐位相同，可作交叉验证。

`turnratio` 的可算性用 `panel.py:429-440` 的 `rolling` 语义复刻：
`turn.rolling(L, min_periods=ceil(L*0.9)).mean()`，两侧窗口都要过门槛，
再过 `_safe_ratio` 的分母非 0。**只读 `data/master/daily/*.parquet` 的
`date` / `turn` 两列**，不调 `attach_factor`（它要 `factor_events` 参数）。

现任那 4 个因子**不重算**（重算要跑两趟 `build_panel`），直接拿「进池数」当现任的
可打分池——它们回看都 ≤60 日，远在 `min_history=41` 之上，池内基本恒非 NaN
（报告自己印证：`n_ranked` 中位 4893 ≈ `n_universe` 均值 4886）。

## 复刻赛马时踩到的两处口径漂移（不查清就量不对）

第一次跑出来"所有 L 全部过闸"，与报告里"15 组全被拦"直接矛盾。查下去是两个
**赛马之后才改的**口径，不是数据问题：

1. **`MIN_TURN_COVERAGE=0.9` 是赛马之后才有的。** `git log -S` 显示它由提交
   `521bced`（"turnratio 双窗口统一覆盖率门槛"）引入，而报告落盘于
   **2026-10-12 23:26**。赛马当时长窗口是 `rolling(252)` 的 pandas 默认
   `min_periods=252`——**一根不缺**。实测该档中位数 **4,567**，与报告
   `n_ranked` 逐位相同；现行 `ceil(252*0.9)=227` 档则是 4,807。
   这一条就值 240 只。
2. **简称 ST 过滤在赛马里根本没生效。** `predict.py:141` 的
   `df["name"].map(is_st_name)` 依赖 `names_map`，而 `scripts/ic_guided_race.py`
   调 `build_cross_sections(store, ..., names_map=未传)` → 面板里 `name` 退化成
   代码本身 → `is_st_name` 恒 False。实测开关差 **162 只**
   （池子中位 4743 vs 4905），而报告的现任 4893 站在 4905 那一侧。

所以本脚本两档都算，并把"现任池中位数 vs 报告 4893"的偏差直接打出来当校验
（实测 4904，偏差 −0.22%）。

用法：
    .venv/bin/python scripts/probe_turn_pool_at_gate_dates.py --data-root data
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from aplus.evolve_race import EMBARGO_DAYS, TRAIN_RATIO, split_window
from aplus.panel import (MIN_TURN_COVERAGE, TURN_RATIO_LONG, TURN_RATIO_SHORT,
                         can_buy)
from aplus.predict import is_st_name

LONG_WINDOWS = (42, 63, 90, 120, 180, 252)
# 现任权重（mom20/rev5/vol20/vsur）在测试段的实测值，取自 ic_guided_race_7y.json
CHAMP_N_RANKED = 4893
CHAMP_N_UNIVERSE = 4886
N_RANKED_SAMPLES = 5            # 与 backtest.N_RANKED_SAMPLES 同源
POOL_GATE = 0.02                # 与 evolve_race.max_universe_gap 默认同源
MIN_HISTORY = 41                # 与 panel.cross_section(min_history=41) 同源


def gate_days(report_path: Path, data_root: Path) -> tuple[list, list]:
    """重算 `use_dates` 并抽出闸门真正看的那 5 天。

    返回 `(test_dates, picks)`。
    """
    rep = json.loads(report_path.read_text())
    start, end = rep["window"]              # end 是开区间，[start, end)
    n_ranked_samples = rep.get("n_ranked_samples", N_RANKED_SAMPLES)

    # panel.py:686-687 —— 以「行数最多的那只票」为交易日历参照
    files = sorted((data_root / "master" / "daily").glob("*.parquet"))
    ref, best = None, -1
    for f in files:
        n = len(pd.read_parquet(f, columns=["date"]))
        if n > best:
            ref, best = f, n
    cal = sorted(pd.read_parquet(ref, columns=["date"])["date"].astype(str).unique())
    dates = [d for d in cal if start <= d < end]

    train_dates, test_dates = split_window(dates, TRAIN_RATIO, EMBARGO_DAYS)
    # 与报告的 test_window 逐位对齐才算复现成功，否则说明口径已经漂了
    assert [test_dates[0], test_dates[-1]] == rep["test_window"], (
        f"重算的测试段 {[test_dates[0], test_dates[-1]]} 与报告 "
        f"{rep['test_window']} 不一致——build_panel/split_window 口径变了")

    # backtest.py:791-798 的取样算法逐字复刻
    if len(test_dates) <= n_ranked_samples:
        picks = list(test_dates)
    else:
        step = (len(test_dates) - 1) / (n_ranked_samples - 1)
        picks = [test_dates[int(round(i * step))] for i in range(n_ranked_samples)]
    return test_dates, picks


def scan_pool(data_root: Path, picks: list, windows: list, stale: bool,
              use_names: bool) -> dict:
    """全池扫一遍，返回 `{(档位, L): [5 天的计数, ...]}`。

    ## 必须复刻 filter_universe，否则量的不是闸门看到的池子

    第一版只数「date ≤ D 有行」的股票，得到 5460 只，比现任的 4893 **还大**，
    于是每个 L 的 gap 都是负数（"池子更宽，全部通过"）——**纯废数**。
    闸门读的 `scoreable_count` 是作用在 `filter_universe` 之后的横截面上的，
    那一步剔掉 ST / 上市 ≤60 日 / 停牌 / 买不进，共约 420 只。
    本函数把 `aplus/predict.py:50` 的过滤逐条复刻在同一份 parquet 上：

    | 判据 | 出处 |
    |---|---|
    | `listed_days >= 41` | `panel.py:603` 的 `min_history` |
    | `last_date == D`（`stale=True` 时） | `predict.py:130` 陈旧透传 |
    | `listed_days > 60` | `predict.py:147` 次新剔除 |
    | `volume > 0` 且 `can_buy` | `predict.py:147` / `panel.py:261` |
    | `isST != 1` | `predict.py:147` |
    | 简称无 ST 前缀（**仅当 `use_names`**） | `predict.py:141` |

    `can_buy` 直接调生产函数，不复写——涨停/一字板的容差与板块限幅
    （`PRICE_TOL` / `price_limit`）复刻一遍必然出错。

    ## 两个必须复刻的口径漂移（否则对不上报告）

    **一、简称 ST 过滤在赛马里根本没生效。** `predict.py:141` 的
    `df["name"].map(is_st_name)` 依赖 `names_map`，而 `scripts/ic_guided_race.py`
    调 `build_cross_sections(store, ..., names_map=未传)` → 面板里 `name` 退化成
    代码本身 → `is_st_name("600000.SH")` 恒 False。实测开关差 **162 只**
    （池子中位 4743 vs 4905），而报告的现任 n_ranked=4893 站在 4905 那一侧。
    故默认 `use_names=False`。

    **二、`MIN_TURN_COVERAGE=0.9` 是赛马之后才加的。** `git log -S` 显示它由
    提交 `521bced`（"turnratio 双窗口统一覆盖率门槛"）引入，而报告落盘于
    2026-10-12 23:26。赛马当时长窗口是 `rolling(252)` 的 pandas 默认
    `min_periods=252`，即**一根不缺**。实测该档的中位数 **4,567**，与报告
    `n_ranked` 逐位相同；现行 `ceil(252*0.9)=227` 档则是 4,807。
    两个档位都算，因为问的是"改 L 能不能过闸"，而闸的读数取决于跑的时候是哪个档。

    每只股票只读 9 列，对每个抽样日 D 做一次二分定位，然后直接切出长/短
    窗口数非 NaN 个数——与 pandas `rolling` 的语义等价，但不需要构造滚动对象。
    """
    names = json.loads((data_root / "names.json").read_text()) \
        if (data_root / "names.json").exists() else {}
    # 两档覆盖率：赛马当时的"一根不缺"，与现行 ceil(L*0.9)
    regimes = {
        "严格(赛马当时, minp=L)": {L: L for L in windows},
        f"现行(minp=ceil({MIN_TURN_COVERAGE}·L))": {
            L: math.ceil(L * MIN_TURN_COVERAGE) for L in windows},
    }
    # 严格档下短窗口也是 pandas 默认 21 根（MIN_TURN_COVERAGE 一并还没有）
    minp_short = {"严格(赛马当时, minp=L)": TURN_RATIO_SHORT,
                  f"现行(minp=ceil({MIN_TURN_COVERAGE}·L))":
                      math.ceil(TURN_RATIO_SHORT * MIN_TURN_COVERAGE)}

    keys = ("in_universe", "no_bar", "young", "stale", "st",
            "ok", "too_short", "partial_ok", "no_turn")
    acc = {(r, L): [{k: 0 for k in keys} for _ in picks]
           for r in regimes for L in windows}

    files = sorted((data_root / "master" / "daily").glob("*.parquet"))
    for f in files:
        code = f.stem
        name = names.get(code, code)
        st_by_name = is_st_name(name)
        # 约 31% 的 parquet **根本没有 turn 列**（panel.py:429 的
        # `if "turn" in merged.columns` 对它们直接跳过 → turnratio 恒 NaN）。
        # 这里把缺列折成全 NaN，让它落进"真缺数据"那一桶，而不是崩掉。
        cols = pq.read_schema(f).names
        want = ["date", "open", "high", "low", "close", "volume", "amount",
                "isST", "turn"]
        avail = [c for c in want if c in cols]
        df = pd.read_parquet(f, columns=avail)
        n_rows = len(df)
        d = df["date"].astype(str).to_numpy()
        col = lambda c: (df[c].to_numpy(dtype=float) if c in avail  # noqa: E731
                         else np.full(n_rows, np.nan))
        turn = col("turn")
        valid = ~np.isnan(turn)
        prev_close = np.concatenate(([np.nan], col("close")[:-1]))   # close.shift(1)
        isst = col("isST")

        for k, D in enumerate(picks):
            n = int(np.searchsorted(d, D, side="right"))   # date <= D 的行数
            for key in acc:
                acc[key][k]["no_bar"] += (n == 0)
            if n == 0:
                continue
            i = n - 1
            # ── panel.cross_section(min_history=41) ──
            if n < MIN_HISTORY:
                for key in acc:
                    acc[key][k]["young"] += 1
                continue
            # ── filter_universe：陈旧透传（该股在 D 没有自己的 K 线）──
            if stale and d[i] != D:
                for key in acc:
                    acc[key][k]["stale"] += 1
                continue
            # ── filter_universe：次新 / ST / 买不进 ──
            if n <= 60 or (not np.isnan(isst[i]) and isst[i] == 1) \
                    or (use_names and st_by_name) \
                    or not (col("volume")[i] > 0) \
                    or not can_buy(code, col("open")[i], col("high")[i], col("low")[i],
                                   col("close")[i], col("volume")[i],
                                   col("amount")[i], prev_close[i]):
                for key in acc:
                    acc[key][k]["st"] += 1
                continue

            cnt_short = int(valid[max(0, n - TURN_RATIO_SHORT):n].sum())
            for reg, minp_long in regimes.items():
                short_ok = cnt_short >= minp_short[reg]
                for L in windows:
                    lo = max(0, n - L)
                    long_ok = int(valid[lo:n].sum()) >= minp_long[L]
                    # _safe_ratio：长窗口均值为 0 则整列 NaN（停牌股零成交）
                    ok = bool(long_ok and short_ok and t_mean(turn, lo, n) > 0)
                    c = acc[(reg, L)][k]
                    c["in_universe"] += 1
                    if ok:
                        c["ok"] += 1
                        if n < L:
                            # pandas 的 rolling 窗口在序列开头会被截短，
                            # 够门槛根数就算得出来——如实计入单列
                            c["partial_ok"] += 1
                    elif n < L:
                        c["too_short"] += 1      # 上市不足 L 日：结构性
                    else:
                        c["no_turn"] += 1         # 够了但 turn 覆盖不够：真缺数据
    return acc


def t_mean(turn: np.ndarray, lo: int, n: int) -> float:
    """窗口内非 NaN `turn` 的均值（全 NaN → NaN，与 pandas 一致）。"""
    seg = turn[lo:n]
    seg = seg[~np.isnan(seg)]
    return float(seg.mean()) if seg.size else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--report", type=Path,
                    default=Path("data/reports/ic_guided_race_7y.json"))
    ap.add_argument("--no-stale", dest="stale", action="store_false",
                    help="不启用 predict.py:130 的陈旧透传（赛马报告产于 2026-10-12，"
                         "该修复标注 2026-10-14，赛马当时可能还没有这条）")
    ap.add_argument("--with-names", action="store_true",
                    help="启用简称 ST 过滤。默认关：ic_guided_race.py 没传 names_map，"
                         "赛马当时这条根本没生效（关掉才复现出报告的 4567）")
    args = ap.parse_args()

    print("=" * 100)
    print("turnratio 股票池 —— 在第六道闸真正看的那 5 个交易日上量")
    print("=" * 100)

    test_dates, picks = gate_days(args.report, args.data_root)
    print(f"\nuse_dates 来源：报告 `window` {args.report.name} + build_panel 的 [start,end) "
          f"+ split_window({TRAIN_RATIO}, embargo={EMBARGO_DAYS})")
    print(f"  报告的 test_dates 字段只是 [首, 尾] 两个端点，不是完整列表 → "
          f"{len(test_dates)} 个交易日由日历重算得到")
    print(f"  use_dates = 测试段 {test_dates[0]} ~ {test_dates[-1]}")
    print(f"  闸门实际抽的 5 天（backtest.py:791-798 等距取样，取中位数）：")
    for i, p in enumerate(picks):
        print(f"    [{i}] {p}")
    print(f"\n报告基准：现任 n_ranked={CHAMP_N_RANKED} / n_universe={CHAMP_N_UNIVERSE}；"
          f"含 turnratio 的 15 组候选 n_ranked=4567")
    print(f"闸门：候选比现任窄 > {POOL_GATE:.0%} 即否决（evolve_race.py:594，只拦变窄）")
    print(f"简称 ST 过滤：{'开' if args.with_names else '关（复现赛马口径）'}")

    print("扫全池（5460 只 × 5 天 × 6 个 L × 2 个覆盖率档，含 filter_universe 复刻）……",
          flush=True)
    acc = scan_pool(args.data_root, picks, list(LONG_WINDOWS), args.stale,
                    args.with_names)

    # 现任 4 因子回看 ≤60 日，远在 min_history=41 之上，池内基本恒非 NaN
    # （报告自己印证：n_ranked 中位 4893 ≈ n_universe 均值 4886），
    # 所以「进池」即现任的可打分池，不重算现任因子。
    strict = "严格(赛马当时, minp=L)"
    cur = f"现行(minp=ceil({MIN_TURN_COVERAGE}·L))"
    base = acc[(strict, LONG_WINDOWS[0])]
    champ_med = int(sorted(c["in_universe"] for c in base)[len(base) // 2])
    print(f"\n复刻校验：本脚本口径下现任池中位数 = {champ_med:,}"
          f"（报告 {CHAMP_N_RANKED}，偏差 "
          f"{(CHAMP_N_RANKED - champ_med) / champ_med:+.2%}）")

    for reg in (strict, cur):
        print("\n" + "=" * 100)
        print(f"档位：长窗口覆盖率 = {reg}")
        print("=" * 100)
        print(f"{'L':>5} {'minp':>5} │" +
              "".join(f"{p:>13}" for p in picks) +
              f" │{'中位数':>9}{'现任':>9}{'窄':>9}{'闸门':>7}")
        print("─" * 100)
        passing = []
        for L in LONG_WINDOWS:
            rows = acc[(reg, L)]
            oks = [c["ok"] for c in rows]
            med = int(sorted(oks)[len(oks) // 2])
            gap = (champ_med - med) / champ_med
            ok_gate = gap <= POOL_GATE
            passing += [L] if ok_gate else []
            mp = L if reg is strict else math.ceil(L * MIN_TURN_COVERAGE)
            print(f"{L:>5} {mp:>5} │" + "".join(f"{v:>13,}" for v in oks) +
                  f" │{med:>9,}{champ_med:>9,}{gap:>8.2%}"
                  f"{'通过' if ok_gate else '拦下':>7}")

        print(f"\n　缺口分解（在中位数所在的那一天上拆；括号内为该抽样日）：")
        print(f"　{'L':>5} │{'进池(现任)':>12}{'可算':>9}{'缺口':>7} │"
              f"{'上市不足L(结构)':>18}{'turn覆盖不足(缺数)':>22}{'滚动截短(边缘)':>18}")
        for L in LONG_WINDOWS:
            rows = acc[(reg, L)]
            i = [j for j, v in enumerate(rows)
                 if v["ok"] == int(sorted(r["ok"] for r in rows)[len(rows) // 2])][0]
            c = rows[i]
            print(f"　{L:>5} │{c['in_universe']:>12,}{c['ok']:>9,}"
                  f"{c['in_universe'] - c['ok']:>7,} │"
                  f"{c['too_short']:>13,} ({picks[i][2:]})"
                  f"{c['no_turn']:>17,} ({picks[i][2:]})"
                  f"{c['partial_ok']:>13,} ({picks[i][2:]})")
        print(f"\n　本档能过闸的 L：**{', '.join(map(str, passing)) or '无'}**")

    # ── 过滤器漏斗，用于校验复刻是否与生产一致 ──────────────────────
    print("\n" + "=" * 100)
    print("表 3　过滤器漏斗（5 个抽样日；校验 filter_universe 复刻得对不对）")
    print("=" * 100)
    print(f"{'抽样日':>13} │{'全池 parquet':>12}{'当天无K线':>11}{'listed<41':>10}"
          f"{'陈旧透传':>10}{'ST/次新/买不进':>14}{'进池':>8}")
    print("─" * 100)
    for k, p in enumerate(picks):
        c = base[k]
        total = (c["in_universe"] + c["no_bar"] + c["young"]
                 + c["stale"] + c["st"])
        print(f"{p:>13} │{total:>12,}{c['no_bar']:>11,}{c['young']:>10,}"
              f"{c['stale']:>10,}{c['st']:>14,}{c['in_universe']:>8,}")

    # ── 结论 ────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("结论")
    print("=" * 100)
    for reg in (strict, cur):
        oks = [c["ok"] for c in acc[(reg, TURN_RATIO_LONG)]]
        med = int(sorted(oks)[len(oks) // 2])
        gap = (champ_med - med) / champ_med
        print(f"\nL={TURN_RATIO_LONG}（生产值，{reg}）：{med:,} 只，窄 {gap:.2%} → "
              f"{'拦下' if gap > POOL_GATE else '通过'}")
    print(f"""
两档的差**不是**"缩短长窗口"造成的，而是 MIN_TURN_COVERAGE={MIN_TURN_COVERAGE}
这道覆盖率豁免在赛马**之后**才加进来（提交 521bced，报告落盘 2026-10-12 23:26）。
赛马当时长窗口是 rolling(252) 的 pandas 默认 min_periods=252，一根不缺。

余量口径：闸门只拦**变窄**（gap=(champ-cand)/champ，evolve_race.py:592-594），
池子变宽是设计使然不否决。本脚本不重算现任那 4 个因子；若它们在池内偶有 NaN，
真实现任 n_ranked 会略低于「进池」数，闸门只会**更宽松**。""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())