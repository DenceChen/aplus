"""BM 的 IC 从哪来 —— 诊断 `fundamental_ic` 那个自相矛盾的结果。

## 待解释的现象

`fundamental_ic` 在同一张表里同时报出：

    bm @K=21   平均 IC +0.0703   IC-t +2.94（过闸）
    bm @K=21   组合 t   −0.96（买最便宜的 10 只，跑不赢同池随机 10 只）

**这两个数不可能同时描述同一个可用的价值效应。** 秩相关显著到 0.07，
取最上面 10 只却打不过随机——只有三种解释：

| 解释 | 判别特征 |
|---|---|
| A. 关系是**单调但极弱**的，效应集中在中间而非顶端 | 分组收益：底部最差、顶部不再更好 |
| B. 池子**本身**有偏（幸存者/覆盖偏差），IC 度量的是样本构成 | 分年 IC：集中在特定年份（牛熊结构差异） |
| C. **前视**：BPS 是后修值，账面里含信号日之后才知道的减值 | 截尾 IC：剔掉极端便宜（"便宜到出事"）后是否还在 |

三种机制的**后续动作完全不同**：A 是「效应存在但我们赚不到」，
B 是「数据有偏、必须扩池」，C 是「PIT 不成立，这条线彻底封死」。
所以先判机制，再谈结论。

## 事前登记

- 分组：**10 等分**，报每组的 K=21 平均收益与胜率，**全报不筛**。
- 分年：按信号日年份拆 IC，报每年的期数。
- 截尾：剔除 BM 最高 5%（最便宜的一档）与最低 5% 后重算 IC。
  若 IC 来自「便宜到出事」的尾部，截尾后应显著缩水。
- 覆盖池 vs 全池：报每年有年报的股票占全池比例——量化 B 的幅度。

本脚本**不产生任何采纳结论**，只解释上面那个矛盾。
"""
import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.evolve_race import bonferroni_t  # noqa: E402
from scripts.fundamental_factor import (  # noqa: E402
    attach_to_cross_sections, coverage, load_annual,
)
from scripts.horizon_sweep import forward_return  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = "scripts/bm_ic_attribution.py"

K = 21
FACTOR = "bm"
DECILES = 10
TRIM = 0.05          # 截掉两端各 5%
MIN_CROSS_SECTION = 60


def _pairs(uni, day, k, daily_map, factors_map, adj_cache, by_report_year=None):
    cols = ["code", FACTOR] + (["report_year"] if by_report_year else [])
    sub = uni[cols].dropna()
    if len(sub) < MIN_CROSS_SECTION:
        return None
    # 逐列 zip，不要在循环里 `sub[sub["code"] == code]`——那是 O(n²)，
    # 450 只 × 55 个网格日就是一千多万次扫描，只为取一个已经在手上的年份。
    xs_, ys_, ry = [], [], []
    for row in sub.itertuples(index=False):
        r = forward_return(daily_map, factors_map, adj_cache, row[0], day, k)
        if r is None:
            continue
        xs_.append(float(row[1]))
        ys_.append(r)
        if by_report_year:
            ry.append(int(row[2]))
    if len(xs_) < MIN_CROSS_SECTION:
        return None
    if by_report_year:
        return np.asarray(xs_), np.asarray(ys_), np.asarray(ry)
    return np.asarray(xs_), np.asarray(ys_)


def ic_by_calendar_and_report_year(xs, days, k, daily_map, factors_map,
                                   adj_cache):
    """**分开「宏观风格」与「PIT 污染」的那张表。**

    BM 的 IC 在 2021 年跳了一个数量级（2019/2020 约 +0.01，2021 起约 +0.10），
    这是结构断点而不是渐变。两种解释预测同样的时间形状：

    - **宏观**：2021 起成长退潮、价值占优 → 同一年内，各报告年份的 IC 应大致相同
      （同一个市场环境对所有账龄一视同仁）；
    - **PIT**：信号日看到的账面值来自不同账龄的报告，越旧的被改过越多轮 →
      同一年内，**报告年份越新 IC 应越高**。

    所以把 IC 按 `(日历年, 报告年份)` 交叉拆开。两种解释的预测在这里分岔。
    """
    from scripts.fundamental_ic import _spearman

    cells: dict[tuple[str, int], list] = {}
    for day in grid_days(days, k):
        uni = xs.get(day)
        if uni is None or "report_year" not in getattr(uni, "columns", []):
            continue
        got = _pairs(uni, day, k, daily_map, factors_map, adj_cache,
                     by_report_year=True)
        if got is None:
            continue
        v, r, ry = got
        for y in sorted(set(ry.tolist())):
            m = ry == y
            if int(m.sum()) < MIN_CROSS_SECTION:
                continue
            ic = _spearman(v[m], r[m])
            if ic is not None:
                cells.setdefault((day[:4], int(y)), []).append(ic)
    return {f"{cy}/{ry}": {"n": len(v), "mean_ic": round(float(np.mean(v)), 4)}
            for (cy, ry), v in sorted(cells.items())}


def revision_exposure(financial: Path) -> dict:
    """每个**报告年份**里，账面值此后被改动过的比例（`UPDATE_DATE != NOTICE_DATE`）。

    这是 PIT 污染的直接度量：不是「这条记录对不对」，而是
    **「信号日我们看到的那一行，到今天已经被改过几轮」**。

    注意与 `diag_financial_lookahead.py`（出路 A）的区别：那里用它**筛数据**
    （已否决），这里只用它**刻画污染程度**，不改变任何进入计算的样本。
    """
    raw = pd.read_parquet(financial)
    a = raw.loc[raw["REPORT_TYPE"].astype(str).str.strip() == "年报"].copy()
    a["ry"] = pd.to_datetime(a["REPORT_DATE"], errors="coerce").dt.year
    a["nd"] = pd.to_datetime(a["NOTICE_DATE"], errors="coerce")
    a["ud"] = pd.to_datetime(a["UPDATE_DATE"], errors="coerce")
    a = a.loc[a["nd"].notna() & a["ry"].notna()]
    out = {}
    for y, g in a.groupby("ry"):
        if len(g) < 200:
            continue
        touched = (g["ud"].notna() & (g["ud"] != g["nd"])).mean()
        out[str(int(y))] = {
            "n": int(len(g)), "revised_share": round(float(touched), 4),
            "median_days_revised": (
                int((g["ud"] - g["nd"]).dt.days.median())
                if touched > 0 else 0),
        }
    return out


def grid_days(days, k):
    return [d for i, d in enumerate(days) if k <= 1 or i % k == 0]


def decile_table(xs, days, k, daily_map, factors_map, adj_cache):
    """每个信号日把覆盖池按 BM 分 10 等份，返回 (分位, 收益) 长表。"""
    rows = []
    for day in grid_days(days, k):
        uni = xs.get(day)
        if uni is None or FACTOR not in getattr(uni, "columns", []):
            continue
        got = _pairs(uni, day, k, daily_map, factors_map, adj_cache)
        if got is None:
            continue
        v, r = got
        # `argsort` 后按位置分箱。**用位置而不是数值切点**——
        # BM 分布极度左偏（少数极便宜票拉长尾），按数值等分会有一档是空的。
        order = np.argsort(v, kind="stable")
        bins = np.array_split(order, DECILES)
        for b, idx in enumerate(bins):
            if len(idx):
                rows.append({"day": day, "q": b + 1, "ret": float(r[idx].mean())})
    return pd.DataFrame(rows)


def ic_by_year(xs, days, k, daily_map, factors_map, adj_cache):
    out = {}
    for day in grid_days(days, k):
        uni = xs.get(day)
        if uni is None or FACTOR not in getattr(uni, "columns", []):
            continue
        got = _pairs(uni, day, k, daily_map, factors_map, adj_cache)
        if got is None:
            continue
        from scripts.fundamental_ic import _spearman
        ic = _spearman(*got)
        if ic is not None:
            out.setdefault(day[:4], []).append(ic)
    return {y: {"n": len(v), "mean_ic": float(np.mean(v))} for y, v in out.items()}


def trimmed_ic(xs, days, k, daily_map, factors_map, adj_cache, trim=TRIM):
    """剔掉两端各 `trim` 后的 IC——检验「便宜到出事」这条尾部解释。"""
    out = {}
    for day in grid_days(days, k):
        uni = xs.get(day)
        if uni is None or FACTOR not in getattr(uni, "columns", []):
            continue
        got = _pairs(uni, day, k, daily_map, factors_map, adj_cache)
        if got is None:
            continue
        v, r = got
        lo, hi = np.quantile(v, [trim, 1 - trim])
        m = (v >= lo) & (v <= hi)
        from scripts.fundamental_ic import _spearman
        ic = _spearman(v[m], r[m])
        if ic is not None:
            out[day] = ic
    return pd.Series(out, dtype=float)


def pool_bias(xs, days, daily_map):
    """每年有年报的股票占**当时全池**的比例——量化 B。"""
    out = {}
    for y in sorted({d[:4] for d in days}):
        ds = [d for d in days if d[:4] == y]
        if len(ds) < 20:
            continue
        mid = ds[len(ds) // 2]
        uni = xs.get(mid)
        if uni is None or FACTOR not in getattr(uni, "columns", []):
            continue
        tot = len(uni)
        cov = int(uni[FACTOR].notna().sum())
        out[y] = {"day": mid, "pool_total": tot, "pool_covered": cov,
                  "share": round(cov / tot, 3) if tot else 0.0}
    return out


def tail_contrasts(xs, days, k, daily_map, factors_map, adj_cache):
    """三个逐日配对的尾部对照——**这才是价值因子该被检验的那一刀**。

    ## 为什么「对随机」不是正确的对照

    前面 `fundamental_ic` 的组合 t = −0.96 用的是「最便宜 10 只 vs 同池随机 10 只」。
    但分档已经显示：Q1（最贵）单独是 **−0.355%**，Q2~Q10 **全为正**。
    也就是说整个效应的来源是**最贵那一档在亏钱**，而不是「越便宜越好」。
    那么正确的零假设就不是随机，而是**对立的尾巴**：

        Q10 − Q1    因子是否真的单调？（真正的价值因子该过的闸）
        Q10 − 池均值  **长头寸**能不能拿到？（本产品只能做多）
        Q1  − 池均值  效应是不是全在**做不出来的那条腿**上？

    三个都报，因为它们的**决策含义完全不同**：前两个过闸才能做多，
    第三个过闸而第二个不过闸，就意味着效应真实存在但**本产品的结构吃不到**。
    """
    from scripts.head_bucket_probe import _paired_t

    q = decile_table(xs, days, k, daily_map, factors_map, adj_cache)
    if q.empty:
        return {}
    piv = q.pivot_table(index="day", columns="q", values="ret")
    pool = piv.mean(axis=1)
    legs = {
        "q10_minus_q1": piv.get(10) - piv.get(1),
        "q10_minus_pool": piv.get(10) - pool,
        "q1_minus_pool": piv.get(1) - pool,
    }
    crit = bonferroni_t(len(legs), df=max(len(pool) - 1, 2))
    out = {"t_crit": crit, "annualizer": 244 / k, "n_days": int(len(pool))}
    for name, s in legs.items():
        # **零基线必须共用索引**。初版写 `pd.Series(np.zeros(len(s)))`，
        # 索引是 0..n-1，而 `s` 的索引是日期 → pandas 按索引对齐后
        # `a - b` 变成 110 行全 NaN → `dropna()` 空集 → t 静默返回 None。
        # 而均值和年化照样打印得出来——**半个输出是坏的，且没有任何报错**。
        t = _paired_t(s, pd.Series(0.0, index=s.index))
        ann = float(s.mean() * 244 / k)
        out[name] = {
            "n": int(s.notna().sum()), "mean_per_k": float(s.mean()),
            "annualized": ann, "t": t,
            "significant": bool(t is not None and abs(t) >= crit),
        }

    # 最小可检出效应（MDE）。**没有它，「不显著」会被读成「没有效应」。**
    sd = float((piv.get(10) - piv.get(1)).std(ddof=1))
    mde_per_k = crit * sd / np.sqrt(max(len(pool), 1))
    out["mde"] = {
        "sd_per_k": sd, "crit": crit, "n_pairs": int(len(pool)),
        "min_detectable_per_k": float(mde_per_k),
        "min_detectable_annualized": float(mde_per_k * 244 / k),
        "note": ("低于这个年化幅度的真实效应，本设计**根本没有能力**发现。"
                 "「不显著」只能读作「没检出」，读作「不存在」就是自欺"),
    }
    return out


def _sha256_file(path) -> str | None:
    """输入文件的**字节**指纹。

    🚨 为什么章必须自己记输入（第 156 层：「盖章只验了身份证没验出生日期」）：
    `expect_bytes` 守的是**生成器**的字节，而本产物的判决还依赖**两个输入**：
    面板缓存（`--cache`）与财报缓存（`--financial`）。
    **代码一个字节没动、输入换了一份** ⇒ 产物照样判「新鲜」⇒
    只有把输入的**内容哈希**写进章，读者才能知道它算自哪一份字节。
    """
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main(argv=None) -> int:
    # 第 125 层：**盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节**。
    # ⇒ `main()` 的**第一条可执行语句**抓一次，盖章时比对，不一致就拒绝盖章。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--financial", type=Path,
                    default=Path("data/reports/financial_annual_cache.parquet"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/bm_ic_attribution.json"))
    args = ap.parse_args(argv)

    xs = pickle.loads(args.cache.read_bytes())
    days = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    annual = load_annual(args.financial)
    xs = attach_to_cross_sections(xs, annual, args.start, args.end)
    cov = coverage(xs, args.start, args.end)
    dm = xs.get("_daily_map") or {}
    fm = xs.get("_factors_map") or {}
    ac = xs.setdefault("_adj_cache", {})

    # ── A. 分位收益 ──────────────────────────────────────────────
    dt = decile_table(xs, days, K, dm, fm, ac)
    print(f"\n{'='*86}\nA. BM 分 {DECILES} 档的 K={K} 平均收益（Q1=最贵 … Q10=最便宜）"
          f"\n{'='*86}")
    dec = {}
    if not dt.empty:
        g = dt.groupby("q")["ret"]
        dec = {"mean": g.mean().round(5).to_dict(),
               "n_obs": g.size().to_dict(),
               "win_rate": (dt.assign(w=dt["ret"] > 0).groupby("q")["w"].mean()
                            .round(3).to_dict())}
        for q in range(1, DECILES + 1):
            print(f"  Q{q:<2} 平均 {g.mean().get(q, float('nan')):+.3%}   "
                  f"胜率 {dec['win_rate'].get(q, float('nan')):.1%}   "
                  f"期数 {g.size().get(q, 0)}")
        spread = g.mean().get(DECILES) - g.mean().get(1)
        print(f"\n  Q10 − Q1 = {spread:+.3%} / {K} 日 ≈ "
              f"{spread * (244 / K):+.2%} 年化")
        print(f"  **单调吗**：{'是' if g.mean().is_monotonic_decreasing else '否'}"
              f"（自 Q1 到 Q10 递减）")

    # ── B. 分年 ─────────────────────────────────────────────────
    byy = ic_by_year(xs, days, K, dm, fm, ac)
    print(f"\n{'='*86}\nB. 分年 IC\n{'='*86}")
    for y, v in sorted(byy.items()):
        print(f"  {y}   平均IC {v['mean_ic']:+.4f}   期数 {v['n']}")

    # ── C. 截尾 ─────────────────────────────────────────────────
    #
    # 🚨🚨 **`t_trimmed` 这个名字是承重的**（2026-10-21 修）：
    # 本节的 t 是**截尾 IC 序列自己的 t**（机理①的判据）。
    # 它原来也叫 `t`，而 G 节循环里又写了一次 `t = v.get("t")`
    # （每个尾部对照自己的 t）⇒ **G 节循环结束后，`t` 指向最后一个
    # 尾部对照（q1_minus_pool）的 t**，产物里 `trimmed_ic::t` 写的就是那个值。
    # 实测：旧产物 `trimmed_ic::t` = `tail_contrasts::q1_minus_pool::t`
    # = **-1.6119787074299516**，**逐位相同**；
    # 而 `trimmed_ic::mean` = **+0.067197**（正）⇒
    # **正均值序列的 t 不可能为负** ⇒ 那个数**不可能**是截尾 IC 的 t。
    # ⇒ 机理①（极值集中）在旧产物里**根本没被判定过**。
    #
    # ⇒ 两个不同的量必须有两个不同的名字。本节用 `t_trimmed`，
    #    G 节的循环变量用 `t_leg`。**不许再合并回 `t`。**
    tic = trimmed_ic(xs, days, K, dm, fm, ac)
    t_trimmed, n = (None, len(tic))
    if len(tic) >= 3:
        vv = tic.to_numpy()
        sd = vv.std(ddof=1)
        t_trimmed = float(vv.mean() / (sd / np.sqrt(len(vv)))) if sd > 0 else None
    crit = bonferroni_t(1, df=max(n - 1, 2))
    print(f"\n{'='*86}\nC. 截掉两端各 {TRIM:.0%} 后的 IC\n{'='*86}")
    print(f"  平均IC {tic.mean() if len(tic) else float('nan'):+.4f}   "
          f"t {t_trimmed if t_trimmed is not None else float('nan'):+.2f}   期数 {n}")

    # ── D. 池子偏差 ─────────────────────────────────────────────
    pb = pool_bias(xs, days, dm)
    print(f"\n{'='*86}\nD. 覆盖池占全池比例\n{'='*86}")
    for y, v in sorted(pb.items()):
        print(f"  {y}   {v['pool_covered']:>4} / {v['pool_total']:>4}  "
              f"= {v['share']:.1%}   （{v['day']}）")

    # ── E. 宏观 vs PIT 的判别式 ───────────────────────────────────
    cell = ic_by_calendar_and_report_year(xs, days, K, dm, fm, ac)
    print(f"\n{'='*86}\nE. IC 按 (信号日历年 / 财报报告年份) 交叉"
          f"\n{'='*86}")
    print("     报告年份越新 = 这份账面值被改动过的轮次越少")
    for key, v in cell.items():
        cy, ry = key.split("/")
        print(f"  信号 {cy}  报告 {ry}   平均IC {v['mean_ic']:+.4f}   "
              f"期数 {v['n']}")

    # ── F. 修订暴露 ───────────────────────────────────────────────
    rev = revision_exposure(args.financial)
    print(f"\n{'='*86}\nF. 各报告年份的修订比例（UPDATE_DATE ≠ NOTICE_DATE）"
          f"\n{'='*86}")
    for y, v in sorted(rev.items()):
        print(f"  报告 {y}   被改过 {v['revised_share']:.1%}   "
              f"条数 {v['n']:>6}   改动中位 {v['median_days_revised']} 天")

    # ── G. 尾部对照：价值因子该被检验的那一刀 ────────────────────
    tc = tail_contrasts(xs, days, K, dm, fm, ac)
    print(f"\n{'='*86}\nG. 尾部对照（{tc.get('n_days', 0)} 个配对日，"
          f"临界 {tc.get('t_crit', float('nan')):.2f}，族 3）\n{'='*86}")
    labels = {
        "q10_minus_q1": "Q10 − Q1   因子是否真单调",
        "q10_minus_pool": "Q10 − 池均  **长头寸能否拿到**",
        "q1_minus_pool": "Q1  − 池均  效应是否全在做不出的那条腿",
    }
    for key, lab in labels.items():
        v = tc.get(key, {})
        # 🚨 循环变量叫 `t_leg`，**不是 `t`**：它装的是「这一条尾部对照自己的 t」，
        #    与 C 节的「截尾 IC 序列自己的 t」是**两个不同的量**。
        #    2026-10-21 修：它原来也叫 `t` ⇒ G 节循环结束后 `t` 指向
        #    **最后一个**尾部对照（`q1_minus_pool`）的 t ⇒ 产物
        #    `trimmed_ic::t` 写出去的是那个值（实测 −1.6119787074299516，
        #    与 `tail_contrasts::q1_minus_pool::t` **逐位相同**），
        #    而 `trimmed_ic::mean` = **+0.067197**（正）
        #    ⇒ **正均值序列的 t 不可能为负** ⇒ 那个数不可能是截尾 IC 的 t。
        t_leg = v.get("t")
        mark = "**显著**" if v.get("significant") else "不显著"
        print(f"  {lab:<34} 年化 {v.get('annualized', float('nan')):+7.2%}   "
              f"t {t_leg if t_leg is not None else float('nan'):>+6.2f}   {mark}")
    mde = tc.get("mde", {})
    if mde:
        print(f"\n  ⚠️ 本设计最小可检出年化幅度 = "
              f"{mde['min_detectable_annualized']:+.2%}"
              f"（每期 sd {mde['sd_per_k']:.2%}，{mde['n_pairs']} 个配对日，"
              f"临界 {mde['crit']:.2f}）")
        print(f"     低于它的效应**检不出来**——「不显著」≠「不存在」")

    out = {
        "kind": "bm_ic_attribution", "k": K, "factor": FACTOR,
        "window": [args.start, args.end], "coverage": cov,
        "question": ("IC-t +2.94 过闸，但组合 t −0.96。三种机制："
                     "A 关系弱且集中在中部 / B 池子有偏 / C BPS 后修值带来前视"),
        "deciles": dec, "ic_by_year": byy,
        "trimmed_ic": {"mean": float(tic.mean()) if len(tic) else None,
                       "t": t_trimmed, "n": n, "trim": TRIM},
        "pool_bias": pb,
        "ic_by_calendar_and_report_year": cell,
        "revision_exposure": rev,
        "revision_caveat": (
            "F 表**不可当作污染证据**。报告年份 ≥2010 的改动滞后中位数恰好 ~365 天，"
            "整齐地锁在一年——逐笔重述不会这样。这是全表刷新时间戳，"
            "与 `diag_financial_lookahead.py` 否决出路 A 的发现一致："
            "`UPDATE_DATE` 反映的是「这一行最近一次被刷新」，不是「这个数被改过」"),
        "tail_contrasts": tc,
        "discriminator": (
            "E 表是分开「宏观风格」与「PIT 污染」的判别式：同一信号年内，"
            "报告年份越新 IC 若越高 → 污染解释；各年份 IC 相近 → 宏观解释"),
        "no_promotion": "本脚本只做归因，不产生采纳结论，更不写 weights.json",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     extra={
                         "k": K,
                         "factor": FACTOR,
                         "window": [args.start, args.end],
                         "cache": str(Path(args.cache).resolve()),
                         "cache_sha256": _sha256_file(args.cache),
                         "financial": str(Path(args.financial).resolve()),
                         "financial_sha256": _sha256_file(args.financial),
                         "fix_2026_10_21": (
                             "trimmed_ic::t 曾被 G 节尾部对照循环的 t 覆盖"
                             "（变量遮蔽）⇒ 本产物该字段首次是**截尾 IC 序列自己的 t**"),
                     })
    print(f"\n  ⚠️ {out['no_promotion']}")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
