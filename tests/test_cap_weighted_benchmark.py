"""市值加权基准实验（预注册 `docs/TODO_cap_weighted_benchmark_2026-10-15.md`）的守卫。

## 这里守卫的是什么

不是「市值加权算得对」——那由单测覆盖。这里守卫的是三件**别的事**：

1. **两套测量仪**：基准相减发生在共享路径深处（`_market_by_day` /
   `_daily_series`），本实验只能**给它加参数**。自己再写一遍逐日收益，
   两台之间的差永远查不出来（第 22 层静默失效）。
2. **判据被偷偷改过**：三档分支是预注册里写死的字面量。
   任何一个字被改动，判决就换了对象，而报告看上去一模一样。
3. **口径漂移**：市值定义、权重归一化、成本链、MIN_BASKET、K=21、窗口起点。
   这些改了会让 A 臂不再复现已发布的 `factor_width_mde.json` 数字。

## 变异测试义务

每条守卫都必须在**真实变异**下变红才算数。
`tests/test_cap_weighted_benchmark.py` 的每条都在最终报告里贴了变异前后。
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import math
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from aplus import capweight                                    # noqa: E402
from aplus.backtest import ENTRY_LAG, _market_by_day            # noqa: E402
from aplus.evolve_race import _daily_series, _tstat             # noqa: E402
from aplus.capweight import CapContext, CapTable                 # noqa: E402


# ── 市值定义：必须与已验证的审计逐位相同 ─────────────────────────────

def test_cap_definition_is_bitwise_the_verified_audit_formula():
    """`aplus.capweight` 的 `a_shares` 必须与 `turn_integrity_audit` **逐位相同**。

    预注册的市值口径是 `close × a_shares`，而 `a_shares = volume×100/turn`
    这条公式是在 `scripts/turn_integrity_audit.py` 里用 21/21 地面真值验过的。

    ⚠️ 这里重新实现一遍**不是**「更干净」，是**第二份真相**：
      审计那份将来改了（换分母、加护栏），本模块那份不会跟着改，
      于是产物里印着「21/21 验过」而实际用的是另一个公式。
      所以本模块**从审计模块 import**，并用这条守卫钉住「真的是同一个对象」。
    """
    from scripts.turn_integrity_audit import implied_a_shares as audited

    assert capweight.implied_a_shares is audited, (
        "aplus.capweight.implied_a_shares 不是 turn_integrity_audit 的那一个 —— "
        "两份实现之间的差永远查不出来")

    d = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "close": [10.0, 11.0, 12.0],
        "volume": [1000.0, 2000.0, 0.0],
        "turn": [0.5, 1.0, 0.0],
    })
    got = capweight.implied_cap(d)
    # 第三行 volume=0 / turn=0 ⇒ 无意义 ⇒ NaN（不得 clip 到 0，见 turn_integrity_audit）
    assert np.isnan(got.iloc[2]), f"无效日没有变成 NaN，而是 {got.iloc[2]}"
    assert got.iloc[0] == pytest.approx(10.0 * 1000.0 * 100 / 0.5)
    assert got.iloc[1] == pytest.approx(11.0 * 2000.0 * 100 / 1.0)


def test_cap_hits_ground_truth_on_the_real_shares_files():
    """真实数据上：`cap/close` 必须命中 `data/master/shares/` 的 `a_shares` 地面真值。

    审计脚本的结论是「21/21 命中、中位比值 1.0000」。本条在**本模块自己的
    查表路径**上重跑一遍同样的比对 —— 因为审计验的是「每日反推值」，
    而本模块实际用的是「按日检索 + 停牌日沿用上一根」，
    **那是另一段代码**（`CapTable._series` 的 searchsorted）。
    审计绿不覆盖它。
    """
    shares_dir = ROOT / "data" / "master" / "shares"
    daily_dir = ROOT / "data" / "master" / "daily"
    checked, ratios = 0, []
    for sf in sorted(shares_dir.glob("*.parquet")):
        dp = daily_dir / f"{sf.stem}.parquet"
        if not dp.exists():
            continue
        sh = pd.read_parquet(sf).sort_values("date")
        d = pd.read_parquet(dp)
        if "turn" not in d.columns or "a_shares" not in sh.columns:
            continue
        w = d[(d["date"] >= "2021-01-01")]
        w = w[w["turn"].notna() & (w["turn"] > 1e-9) & w["volume"].notna() & (w["volume"] > 0)]
        if len(w) < 100:
            continue
        truth, ok = _truth_asof(sh, w["date"].to_numpy())
        if ok.sum() < 100:
            continue
        implied = capweight.implied_cap(w).to_numpy()
        ratio = implied[ok] / (w["close"].to_numpy(float)[ok] * truth[ok])
        ratios.append(float(np.median(ratio)))
        checked += 1
    assert checked >= 20, f"只检到 {checked} 只票，样本不足以判口径"
    med = float(np.median(ratios))
    assert 0.95 <= med <= 1.05, f"cap/close 对 a_shares 真值的中位比值 {med:.4f} 越界"
    assert sum(0.95 <= r <= 1.05 for r in ratios) / len(ratios) >= 0.95, (
        f"命中比例 {sum(0.95 <= r <= 1.05 for r in ratios)}/{len(ratios)} 低于 95%")


def _truth_asof(sh: pd.DataFrame, dates: np.ndarray, col: str = "a_shares"):
    """逐日取股本真值（分段常数右连）。早于首条记录的日期必须剔除。"""
    raw = np.searchsorted(sh["date"].to_numpy(), dates, side="right") - 1
    valid = raw >= 0
    idx = np.clip(raw, 0, len(sh) - 1)
    values = sh[col].to_numpy(float)[idx]
    valid &= values > 0
    return values, valid


# ── 最强的一条不变量：cap 通路关掉时必须**逐位**不变 ────────────────

def _toy_map():
    """一只可构造的小 daily_map：4 只票 × 8 天，含一根停牌（turn=NaN）。"""
    out = {}
    for k, code in enumerate(["A", "B", "C", "D"]):
        out[code] = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=8).strftime("%Y-%m-%d"),
            "code": code,
            "open": np.linspace(10, 17, 8) + k,
            "high": np.linspace(10, 17, 8) + k + 1,
            "low": np.linspace(10, 17, 8) + k - 1,
            "close": np.linspace(10, 17, 8) + k,
            "volume": [1000.0 + 500 * k] * 8,
            "amount": [1e4] * 8,
            "tradestatus": [1] * 8,
            "isST": [0] * 8,
            "turn": [1.0 + 0.5 * k] * 8,
            "pctChg": [0.0] * 8,
        })
    # C 在第 4 天停牌：turn 为 NaN ⇒ 该日无市值 ⇒ 权重必须沿用上一根，
    # 而不是变成 0 或把整个篮子打回等权。
    out["C"].loc[3, "turn"] = np.nan
    return out


def test_market_by_day_with_cap_table_off_is_bitwise_unchanged():
    """`cap_table=None` 时 `_market_by_day` 的输出必须与老实现**逐位相同**。

    预注册的 A 臂 = 今天那 6.95%/年。若这一条不成立，
    「加了个可选参数」就已经改掉了历史结论的数值 —— 而报告上写的是同一个数。
    """
    dm = _toy_map()
    dates = list(dm["A"]["date"])

    got = _market_by_day(dm, dates, entry_lag=1)

    import random as _random
    from aplus.backtest import _next_bars
    rng = _random.Random(42)
    codes = rng.sample(list(dm), min(150, len(dm)))
    want = {}
    for day in dates:
        m = _next_bars(dm, codes, day, entry_lag=1)
        want[day] = float((m["close"] / m["prev_close"] - 1).mean()) if len(m) else 0.0

    assert list(got) == list(want)
    for day in want:
        assert got[day] == want[day], f"{day}: {got[day]!r} != {want[day]!r}"


def test_daily_series_without_weights_col_is_bitwise_unchanged():
    """组合端加权重列后，`weights_col=None` 必须与老实现**逐位相同**。

    ★ 这是本实验最强的一条守卫。三臂共用同一份 `_daily_series`，
      B 臂与 C 臂的**唯一**差别就是组合端要不要按市值加权。
      若「不传权重列」这条路径被改动过，那么 B 与 C 的差就不只来自市值加权，
      而报告会把那个差一并算进「因子贡献」——**正是预注册 §1 警告的那件事**。
    """
    res = _toy_results()

    got = _daily_series(res, "up")
    leg = res[res["direction"] == "up"]
    net = leg["actual_pct"] - leg["cost"]      # `_daily_series` 扣成本，手算必须同扣
    want = (net.groupby(leg["pred_date"]).mean()
            - leg.groupby(leg["pred_date"])["market_pct"].mean())
    count = net.groupby(leg["pred_date"]).count()
    from aplus.verify import MIN_BASKET
    want = want.where(count >= MIN_BASKET).dropna()

    pd.testing.assert_series_equal(got, want, check_exact=True)


def _toy_results():
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    rows = []
    for i, day in enumerate(days):
        for j in range(6):
            rows.append({
                "code": f"{day}-{j}", "direction": "up", "pred_date": day,
                "actual_pct": 0.01 * (j - 2) + 0.002 * i,
                "cost": 0.0005, "market_pct": 0.003 + 0.0001 * i,
                "score": float(6 - j),
            })
    return pd.DataFrame(rows)


def _with_missing_bars_on_surviving_days(n_nan_per_day: int = 2,
                                         cap: float = 1.0) -> pd.DataFrame:
    """`_toy_results()` + **每天**若干行缺 bar，并带上 `cap` 列。

    ## 🚨 为什么不能按行号前置 NaN（2026-10-15 审查抓到，这是真恒真守卫）

    第一版写的是 `res.loc[:n_bars-1, "actual_pct"] = np.nan`，看起来「造出了 NaN」。
    但 `_toy_results` 是**按天连续排列**的（每天 6 行），而 `n_bars = MIN_BASKET+2 = 5`
    ⇒ 那 5 行**全部落在第一天** ⇒ 第一天存活 1 行 < `MIN_BASKET = 3`
    ⇒ `.where(count >= MIN_BASKET).dropna()` 把**整天**丢掉
    ⇒ **参与计算的三天零 NaN**，而分母上的 `w.where(ok)` 与 `w` 在零 NaN 处**逐位相等**。

    实测：把 `_daily_series` 的分母从 `w.where(ok).groupby(...).sum()`
    改成 `w.groupby(...).sum()`（两条守卫 docstring 里**逐字点名**的那个缺陷），
    两条守卫**全部照绿**。

    ⇒ 这是 CLAUDE.md 第 5 条通则的一个新变体：**防空真探针核了「有 NaN」，
      没核「NaN 落在参与计算的行上」** —— 探针自己给出了假保证。

    ## 参数为什么是「每天 2 个」而不是别的

    实测三档（变异后 `max|diff|`）：

    | 每天 NaN | 存活日 | 每天存活行 | 变异可见? |
    |---|---|---|---|
    | 5 | 0 | — | ❌ 无 |
    | 3 | 4 | 3 | ✅ 1.28e-02（**贴 MIN_BASKET 边界**） |
    | **2** | **4** | **4** | ✅ **6.83e-03** |
    | 1 | 4 | 5 | ✅ 2.58e-03 |

    选 2 的两条理由：**①** 存活行数 4 > `MIN_BASKET` = 3 ⇒ **不贴边界**
    ——贴边界的话 `MIN_BASKET` 一次 off-by-one 就会静默改掉哪些天参与计算，
    而守卫今天照样全绿；**②** 它是**最小充分**扰动：不改夹具规模，
    只让「分母按权重和算」这个缺陷在**每一天**上都可观测。
    """
    res = _toy_results().assign(cap=cap)
    res["actual_pct"] = res["actual_pct"].astype(float)
    for day in res["pred_date"].unique():
        idx = res.index[res["pred_date"] == day][:n_nan_per_day]
        res.loc[idx, "actual_pct"] = np.nan
    return res


def _assert_nan_lands_on_rows_that_reach_the_denominator(
        res: pd.DataFrame, min_basket: int, direction: str = "up") -> None:
    """防空真探针：**必须**有 NaN 收益行落在**参与了加权计算**的那一天。

    只断言「有 NaN」是 CLAUDE.md 记录过的假保证：
    夹具可以把 NaN 全部堆在被 `MIN_BASKET` 整天丢弃的那一天，
    于是守卫恒真而探针照绿。
    """
    assert res["actual_pct"].isna().any(), (
        "夹具坏了：没有缺 bar 的行 ⇒ 加权/等权的差异恒为零，守卫会恒真")
    leg = res[res["direction"] == direction]
    net = leg["actual_pct"] - leg["cost"]
    w = leg["cap"]
    ok = w.notna() & (w > 0) & net.notna()
    cnt = ok.groupby(leg["pred_date"]).sum()
    surviving = [d for d, c in cnt.items() if c >= min_basket]
    assert surviving, f"夹具坏了：没有任何一天活过 MIN_BASKET={min_basket}"
    nan_on_survivors = leg[leg["pred_date"].isin(surviving)]["actual_pct"].isna().sum()
    assert nan_on_survivors > 0, (
        f"🚨 夹具坏了：{nan_on_survivors} 行 NaN 落在参与计算的存活日上。"
        f"存活日 {surviving} 上一个 NaN 都没有 ⇒ `w.where(ok)` 与 `w` 在那里逐位相等 ⇒ "
        f"「分母按权重和算」这个缺陷在存活日上**不可观测** ⇒ 守卫恒真。"
        f"（NaN 实际落在 {sorted(set(leg['pred_date'][leg['actual_pct'].isna()]) - set(surviving))}，"
        f"那些天被 MIN_BASKET 整天丢弃了。）")
    # 分母在存活日上必须真的依赖 ok 掩码，否则整条守卫问的是空命题
    s = leg[leg["pred_date"].isin(surviving)]
    assert not (s["cap"].where(s["actual_pct"].notna())
                == s["cap"]).all(), (
        "分母掩码在存活日上不起作用 —— `w.where(ok)` 与 `w` 逐位相等，"
        "加权与等权无法区分")


# ── 权重归一化 ──────────────────────────────────────────────────────

def test_every_days_weights_sum_to_exactly_one():
    """预注册的「每日重新归一化」—— 任何一天的权重和恒为 1。

    ⚠️ 停牌日必须**沿用上一根的市值**而不是变成 0 或 NaN：
      变成 0 会让停牌的大盘股静默退出基准（等于偷偷改了抽样口径），
      变成 NaN 会让整天的权重全丢。
    """
    dm = _toy_map()
    ctx = CapContext(CapTable(dm))
    codes = list(dm)
    for day in dm["A"]["date"]:
        w = ctx.normalize(day, codes)
        assert sum(w.values()) == 1.0, f"{day}: 权重和 {sum(w.values())!r} != 1.0"
        assert all(v > 0 for v in w.values()), f"{day}: 出现非正权重 {w}"

    # C 在第 4 天停牌（turn=NaN）⇒ **该日**的市值必须等于停牌前那一根，
    # 而不是 0、不是 NaN。注意方向：沿用的是 01-04 自己上一根（01-03），
    # 01-05 有新收盘价，市值本就该变。
    c_before = ctx.table.cap_on("C", "2024-01-03")
    c_susp = ctx.table.cap_on("C", "2024-01-04")
    assert c_susp == c_before, "停牌日没有沿用上一根的市值"
    assert np.isfinite(c_susp) and c_susp > 0, "停牌日市值退化成 0/NaN"
    w_susp = ctx.normalize("2024-01-04", codes)
    assert w_susp["C"] > 0, "停牌的大盘股被静默踢出了篮子"


def test_normalize_falls_back_to_equal_weight_and_says_so_when_no_caps_exist():
    """市值全不可得的那一天 ⇒ 回退等权，**并且必须被计数**。

    静默回退是第 5 层同族的失效：数字照常出，报告看上去与正常跑一模一样，
    而实际上有几天的分母换了口径。回退次数必须可读。
    """
    dm = _toy_map()
    for df in dm.values():
        df["turn"] = np.nan          # 所有票都停牌 ⇒ 一个市值都拿不到
    ctx = CapContext(CapTable(dm))
    w = ctx.normalize("2024-01-02", list(dm))
    assert w == {}, "没有任何市值时必须返回空（交由调用方回退等权），不能编一个数"
    assert ctx.stats["days_total"] == 1
    assert ctx.stats["days_fallback_equal"] == 1, "回退没有被计数"
    assert ctx.stats["rows_requested"] == 4 and ctx.stats["rows_missing"] == 4


# ── 基准端：cap 加权必须真的改变数字，且与手算一致 ──────────────────

def test_market_by_day_cap_weighted_matches_the_hand_computed_weighted_mean():
    dm = _toy_map()
    dates = list(dm["A"]["date"])
    ctx = CapContext(CapTable(dm))

    import random as _random
    from aplus.backtest import _next_bars
    rng = _random.Random(42)
    codes = rng.sample(list(dm), min(150, len(dm)))
    cap = {}

    got = _market_by_day(dm, dates, entry_lag=1, cap_ctx=ctx, cap_sink=cap)

    assert cap, "cap_sink 没被写入 —— 基准端根本没做市值加权"
    for day in dates:
        m = _next_bars(dm, codes, day, entry_lag=1)
        if not len(m):
            # 末端几天没有 D+1/D+2 的 bar ⇒ 抽样池为空。
            # 两臂必须给同一个值（都是 0.0），否则 A/B 的差里混进了「空池」。
            assert cap[day] == got[day] == 0.0, f"{day}: 空池日两臂不一致"
            continue
        w = ctx.normalize(day, list(m["code"]))
        r = m["close"] / m["prev_close"] - 1
        want = float(sum(w[c] * x for c, x in zip(m["code"], r)))
        assert cap[day] == pytest.approx(want, abs=1e-15), f"{day}: {cap[day]!r} != {want!r}"
        # 与等权臂不同（除非权重恰好均匀）—— 否则这条守卫是恒真的
    assert cap != got, "市值加权基准与等权基准逐位相同 —— 加权没起作用"


def test_market_by_day_uniform_caps_reproduce_the_equal_weight_series():
    """权重全相等 ⇒ 市值加权基准必须与等权基准**相同**。

    这是「加权」这件事的退化校验：一组构造出来的全 1 权重若算不出等权，
    那说明归一化/加权那一段本身是坏的，而真实数据上的差异会掩盖它。
    """
    dm = _toy_map()
    dates = list(dm["A"]["date"])
    equal = _market_by_day(dm, dates, entry_lag=1)
    flat = CapContext(CapTable(dm, weight_override=1.0))
    cap = {}
    _market_by_day(dm, dates, entry_lag=1, cap_ctx=flat, cap_sink=cap)
    for day in dates:
        assert cap[day] == pytest.approx(equal[day], abs=1e-15), day


# ── 组合端：市值加权的篮子超额必须与手算一致 ───────────────────────

def test_daily_series_cap_weighted_matches_hand_computation():
    res = _toy_results()
    up = res[res["direction"] == "up"]
    # 给一只票压上 3 倍市值 ⇒ 它的贡献变成 3 倍
    caps = {c: (3.0 if j == 0 else 1.0)
            for j, c in enumerate(up["code"].unique())}
    res = res.assign(cap=res["code"].map(caps).astype(float))

    got = _daily_series(res, "up", weights_col="cap")

    leg = res[res["direction"] == "up"]
    net = leg["actual_pct"] - leg["cost"]
    w = leg["cap"]
    basket = (net * w).groupby(leg["pred_date"]).sum() / w.groupby(leg["pred_date"]).sum()
    want = basket - leg.groupby(leg["pred_date"])["market_pct"].mean()
    from aplus.verify import MIN_BASKET
    n_capped = w.where(w > 0).groupby(leg["pred_date"]).count()
    want = want.where(n_capped >= MIN_BASKET).dropna()

    pd.testing.assert_series_equal(got, want, check_names=False, check_exact=False,
                                   rtol=1e-12, atol=0)


def test_daily_series_cap_weighted_counts_only_capped_constituents():
    """⚠️ 分成分下限在 cap 口径下必须数**有市值的成分**，不是数行数。

    若仍按行数计，一天里 200 只持仓只有 5 只有市值时，
    「篮子超额」实际上是 5 只票的收益 —— 而 MIN_BASKET 当初就是为了
    拦住这件事（见 `_daily_series` 的 docstring）。
    """
    from aplus.verify import MIN_BASKET
    res = _toy_results()
    up = res[res["direction"] == "up"]
    # 每天 6 行，只留 **MIN_BASKET-1** 行有市值 —— **按天**做，不是按全表前 N 行。
    # （按全表前 N 行只会砍掉第一天的两行，而那天仍有 4 >= MIN_BASKET ⇒ 恒真。）
    rank = up.groupby("pred_date").cumcount()
    res = res.assign(cap=np.where(rank < int(MIN_BASKET) - 1, 1.0, 0.0))
    got = _daily_series(res, "up", weights_col="cap")
    assert got is None or len(got) == 0, (
        f"只有 {MIN_BASKET - 1} 只有市值的日子仍然出数了（{len(got) if got is not None else 0} 天）")

    # 正控：留到 **正好** MIN_BASKET 只时必须出数 —— 上面那条不许是「全灭都绿」
    res2 = res.assign(cap=np.where(rank < int(MIN_BASKET), 1.0, 0.0))
    got2 = _daily_series(res2, "up", weights_col="cap")
    assert got2 is not None and len(got2) == 4, (
        f"正好 {MIN_BASKET} 只有市值时必须出数（实得 {len(got2) if got2 is not None else 0} 天）")


# ── 一台测量仪，不许有第二台 ────────────────────────────────────────

def test_experiment_reuses_run_year_and_never_recomputes_daily_returns():
    """实验脚本必须 `import run_year`，且**不得**自己算逐日超额/分块 t。

    自查方式用 AST 而不是子串：`grep "run_year"` 会被注释和文档字符串骗过，
    而一份「看起来复用了、其实另写了一遍」的脚本正是这一层要拦的。
    """
    src = (ROOT / "scripts" / "cap_weighted_benchmark.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.update(a.name for a in node.names)
    assert "run_year" in imported, (
        "实验脚本没有从共享脚本 import run_year —— 它自己搭了一套回测")
    assert "_daily_series" not in imported, (
        "实验脚本直接 import 了 _daily_series：共享入口是 run_year，"
        "绕过它就等于多了一台测量仪")
    assert "_tstat" not in imported, "分块 t 必须由 run_year 内部那一份出"

    # 不许自己实现分块（口径必须只有 evolve_race 一处），也不许自己抽样基准
    assert "_block_means" not in src, "实验脚本自己实现了分块 —— 分块口径必须只有一处"
    assert "DEGENERATE_CV" not in src, "退化判据被复制了一份"
    assert "random.Random" not in src and "random.seed" not in src, (
        "实验脚本自己抽样了基准 —— 基准抽样只有 _market_by_day 一处")


def test_evaluate_weights_still_reads_cost_from_the_single_source():
    """`evaluate_weights` 里的成本必须**仍然**来自 `STOCK_COST_DEFAULT` 单一来源。

    预注册 §4：本实验不改变任何成本假设。若实验顺手在
    `evaluate_weights` 里塞了一个 `cost=` 参数，本判决作废。

    🚨 第一版是 `assert "cost = STOCK_COST_DEFAULT" in src` —— **只核赋值左侧**
    （2026-10-15 审查实测，四种改法全部照样命中）：

    | 改法 | 第一版守卫 |
    |---|---|
    | `cost = STOCK_COST_DEFAULT * 2`（成本翻倍） | ✅ 绿 |
    | `cost = STOCK_COST_DEFAULT + 0.0006`（约 2.1 倍） | ✅ 绿 |
    | `cost = 0.00055  # cost = STOCK_COST_DEFAULT` | ✅ 绿（**注释即可满足**） |
    | `cost = STOCK_COST_DEFAULT`（唯一真代码行） | ✅ 绿 |

    ⇒ **子串能告诉你「有这个串」，不能告诉你「它是被用的那个值」** —— 形状 13。
    ⇒ 改 AST：核每一处 `cost = ...` 的**右值恰好**是 `Name('STOCK_COST_DEFAULT')`，
      且**不允许**任何一处是别的表达式（含事后追加的 `cost = cost * 2`）。
    """
    import aplus.backtest as _bt
    fn = _bt.evaluate_weights
    src = inspect.getsource(fn)
    tree = ast.parse(textwrap.dedent(src))
    assigned = [ast.unparse(n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name) and t.id == "cost"]
    assert assigned, (
        "`evaluate_weights` 里根本没有 `cost = ...` 赋值 —— "
        "成本可能改从别处进来了，单一真相源守卫必须红，不能静默通过")
    bad = [v for v in assigned
           if not (v.strip() == "STOCK_COST_DEFAULT")]
    assert not bad, (
        f"`cost` 被赋成了 {bad}，不再是 `STOCK_COST_DEFAULT` 本身 —— "
        f"预注册 §4「不改变任何成本假设」被破坏（实测第一版对 "
        f"`STOCK_COST_DEFAULT * 2` 与注释里的同名字样**全绿**）")
    # ⚠️ 子串断言会被同一函数 docstring 里那句**记录该开关已被删掉**的注释满足
    #   （形状 13）⇒ 这里锚在**语法位置**上：`cross_sections.get(...)` 的实参。
    keys = set()
    for node in ast.walk(ast.parse(textwrap.dedent(src))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "cross_sections"
                and node.args and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    assert "_cost" not in keys, f"成本又有了第二条来源：cross_sections 读了 {sorted(keys)}"
    assert not any(p in inspect.signature(fn).parameters
                   for p in ("cost", "cost_model")), (
        "evaluate_weights 不许长出成本入参 —— 成本口径只有一条链")


def test_the_cap_diagnostic_rides_in_the_shared_path_not_a_second_pass():
    """预注册 §3.1 的「组合 vs 基准流通市值中位数比」只能从共享路径里取。

    ⚠️ 在实验脚本里重算一遍基准的中位市值 = **第二次抽样**：
      两份抽样哪怕种子相同，中位数也会因停牌/上市沿用的处理不同而不同，
      而这个差**永远查不出来**。所以分子分母都必须由
      `_market_by_day` / `evaluate_weights` 在各自那一层交出来。
    """
    dm = _toy_map()
    dates = list(dm["A"]["date"])
    ctx = CapContext(CapTable(dm))
    sink = {}
    _market_by_day(dm, dates, entry_lag=1, cap_ctx=ctx, cap_sink=sink)
    assert set(ctx.diag) == {"benchmark"}, f"基准端没交出诊断：{sorted(ctx.diag)}"
    # 覆盖的天数必须与**基准真正出数**的天数一致（末端没有 D+1/D+2 的日子不出数）
    from aplus.backtest import _next_bars
    traded = [d for d in dates if len(_next_bars(dm, list(dm), d, entry_lag=1))]
    assert set(ctx.diag["benchmark"]) == set(traded)
    assert len(traded) >= 5, "夹具坏了：几乎没有可成交日，中位数比验不到东西"
    # 组合端由 evaluate_weights 交出 —— 这里只验通道存在且记的是中位数
    ctx.normalize(dates[0], list(dm), channel="portfolio")
    assert dates[0] in ctx.diag["portfolio"]
    v = ctx.diag["portfolio"][dates[0]]
    caps = [ctx.table.cap_on(c, dates[0]) for c in dm]
    assert v == pytest.approx(float(np.median(caps)), rel=1e-12)
    # 未传 channel 时不留痕 —— 加参数不得改变既有行为
    ctx2 = CapContext(CapTable(dm))
    ctx2.normalize(dates[0], list(dm))
    assert ctx2.diag == {}, "channel 默认 None 仍留下了诊断 —— 参数不再是 no-op"


def test_benchmark_cap_never_silently_falls_back_to_equal_weight():
    """`benchmark_cap=True` 而 `_market_cap` 缺失 ⇒ **当场报错**，不许回落。

    静默回落的后果特别隐蔽：`market.get(day, 0.0)` 在空字典上恒为 0.0，
    于是「超额」变成**组合绝对收益**——数字照样出得来、照样三条臂各不相同，
    只是整体抬高了一截，而报告看上去完全正常。
    """
    from aplus.backtest import evaluate_weights
    xs = {"_dates": ["2024-01-02"], "_daily_map": {}, "_market": {"2024-01-02": 0.01}}
    with pytest.raises(ValueError, match="_market_cap"):
        evaluate_weights(xs, weights=lambda u: u, top_n=1, bottom_n=1,
                         dates=["2024-01-02"], rebalance_every=1,
                         benchmark_cap=True)


def test_a_shares_on_is_index_aligned_with_the_date_array():
    """⚠️ 缓存里的三个数组必须**逐位等长**。

    `turn_integrity_audit.implied_a_shares` 用 `daily.loc[ok, ...]` 构造，
    返回的是**被筛掉无效行之后**的短序列；直接 `.to_numpy()` 当成与日期
    等长的数组，下标就会整体前移 —— 短的那天越界、长的那天**读错票**。

    ★ 这个洞单元夹具抓不到：toy 夹具里只有 1 行 turn=NaN，
      `a_shares_on` 那条路径（诊断 §3.3 才会走到）从没在短序列上被调用过。
      ⇒ 必须用**真的含有停牌行的票**来钉。
    """
    dm = _toy_map()
    ctx = CapContext(CapTable(dm))
    # toy 里 C 有 1 行 turn=NaN ⇒ 短序列长度 7，日期长度 8
    dates, caps, shares = ctx.table._series("C")
    assert len(dates) == len(caps) == len(shares), (
        f"C 的三个数组不等长：dates={len(dates)} caps={len(caps)} shares={len(shares)}")
    # 停牌那天市值沿用上一根，而股本**不沿用**（沿用就是编一个没测过的数）
    assert ctx.table.cap_on("C", "2024-01-04") == ctx.table.cap_on("C", "2024-01-03")
    assert not np.isfinite(ctx.table.a_shares_on("C", "2024-01-04")), (
        "停牌日的股本被沿用了 —— 那一天的股本根本没被测过")

    # 真数据：全池里随便抽一只含停牌行的票，验同样的等长性质
    import glob
    real = [p for p in sorted(glob.glob(str(ROOT / "data" / "master" / "daily" / "*.parquet")))
            if pd.read_parquet(p, columns=["turn"])["turn"].isna().any()][:3]
    if not real:
        pytest.skip("库里没有含 turn=NaN 的票")
    from aplus.store import LocalStore
    store = LocalStore(ROOT / "data")
    for p in real:
        code = Path(p).stem
        d = store.read_daily(code)
        dates, caps, shares = CapTable({code: d})._series(code)
        assert len(dates) == len(caps) == len(shares) == len(d), (
            f"{code}: {len(dates)}/{len(caps)}/{len(shares)} vs 日线 {len(d)} 行")


def test_uniform_cap_weights_reproduce_equal_weight_even_with_missing_bars():
    """全等市值 ⇒ 加权篮子必须与等权篮子**相等**，**包括有行缺 bar 的那天**。

    🚨 这是 V 对照臂在真数据上抓到的那个洞：`verify_predictions` 用
      `merge(how="left")`，没有下一根 bar 的行 `actual_pct` 是 NaN。
      分子跳过这些行，而权重在它们上面不是 NaN ⇒ 分母若按权重和算，
      「加权」就会比「等权」**系统性偏低**（实测 2021 单年差 2.4%）。

    ⇒ 夹具见 `_with_missing_bars_on_surviving_days`：**每天**都置空，
      且防空真探针核的是「NaN 落在存活日上」而不只是「有 NaN」——
      第一版按行号前置，那 5 行全落在被整天丢弃的第一天，**这条守卫恒真**
      （分母变异实测照绿，见该函数 docstring 的三档实测表）。
    """
    from aplus.verify import MIN_BASKET
    res = _with_missing_bars_on_surviving_days(cap=1.0)   # 全等市值 ⇒ 退化回等权
    _assert_nan_lands_on_rows_that_reach_the_denominator(res, MIN_BASKET)

    got = _daily_series(res, "up", weights_col="cap")
    want = _daily_series(res, "up")                 # 不传权重列 ⇒ 老口径
    assert got is not None and want is not None
    assert list(got.index) == list(want.index)
    np.testing.assert_allclose(got.to_numpy(float), want.to_numpy(float),
                               rtol=1e-12, atol=0)


def test_the_weighted_basket_divides_only_by_contributing_rows():
    """分母必须等于**真正贡献了分子**的那些行的权重和。

    恒等式对拍：把缺失行补上收益之后，加权篮子应当等于「用全等权重重算」。

    🚨 夹具的 NaN **必须每天都有**（`_with_missing_bars_on_surviving_days`）。
      第一版按行号前置 5 行，全部落在被 `MIN_BASKET` 整天丢弃的第一天，
      存活日零 NaN ⇒ `w.where(ok)` 与 `w` 逐位相等 ⇒ 把分母改成
      `w.groupby(...)`（本守卫 docstring 点名的那个缺陷）时**它照样全绿**。
      这是 CLAUDE.md 第 5 条通则的变体：**防空真探针核了「有 NaN」，
      没核「NaN 落在参与计算的行上」**。
    """
    from aplus.verify import MIN_BASKET
    res = _with_missing_bars_on_surviving_days(cap=2.0)
    _assert_nan_lands_on_rows_that_reach_the_denominator(res, MIN_BASKET)
    got = _daily_series(res, "up", weights_col="cap")

    leg = res[res["direction"] == "up"]
    net = leg["actual_pct"] - leg["cost"]
    w = leg["cap"]
    ok = w.notna() & (w > 0) & net.notna()
    want = ((net * w).groupby(leg["pred_date"]).sum()
            / w.where(ok).groupby(leg["pred_date"]).sum()
            - leg.groupby(leg["pred_date"])["market_pct"].mean())
    want = want.where(ok.groupby(leg["pred_date"]).sum() >= MIN_BASKET).dropna()
    pd.testing.assert_series_equal(got, want, check_names=False, check_exact=True)


def test_run_year_signature_carries_the_cap_switches_only():
    """`run_year` 只许多出 cap 相关与基准口径的参数。

    它是 P0/安慰剂/榜宽三处的共享入口；在这里长出别的开关，
    三条臂之间的差就分不清来自信号还是这段代码。
    """
    from backtest_walk_forward_weights import run_year
    params = set(inspect.signature(run_year).parameters)
    assert params == {"xs", "weights", "dates", "top_n", "bottom_n",
                      "rebalance_every", "daily_out", "score_transform",
                      "benchmark_cap", "portfolio_cap", "cap_ctx"}, params


# ── 判决三档：照抄预注册表格，一个字都不许改 ─────────────────────────

def test_verdict_branches_are_the_preregistered_three():
    """预注册 §2 的分支表，逐字照抄。

    | 分支 | 条件 |
    |---|---|
    | `CAP_FACTOR_WORKS`      | C 臂毛超额 > 0 且分块 t ≥ t_crit |
    | `CAP_POSITIVE_NOT_SIG`  | C 臂毛超额 > 0 但 t < t_crit |
    | `CAP_ZERO_OR_NEGATIVE`  | C 臂毛超额 ≤ 0 |
    """
    from cap_weighted_benchmark import T_CRIT_5PCT, decide

    t_c = T_CRIT_5PCT
    assert t_c == pytest.approx(1.959963984540054, abs=1e-12), "t_crit 不是 1.96 的正态分位数"

    # > 0 且 t ≥ t_crit
    assert decide(0.01, 5.0)[0] == "CAP_FACTOR_WORKS"
    assert decide(0.01, t_c)[0] == "CAP_FACTOR_WORKS"          # 边界含等号
    assert decide(0.01, 1.999)[0] == "CAP_FACTOR_WORKS"
    # > 0 但 t < t_crit
    assert decide(0.01, 1.5)[0] == "CAP_POSITIVE_NOT_SIG"
    assert decide(0.01, t_c - 1e-9)[0] == "CAP_POSITIVE_NOT_SIG"
    assert decide(0.01, -9.0)[0] == "CAP_POSITIVE_NOT_SIG"     # 负 t 仍落在这一档
    assert decide(0.01, None)[0] == "CAP_POSITIVE_NOT_SIG", "t 测不出时不得当成显著"
    # ≤ 0，无论 t 多大
    assert decide(0.0, 9.0)[0] == "CAP_ZERO_OR_NEGATIVE"
    assert decide(0.0, None)[0] == "CAP_ZERO_OR_NEGATIVE"
    assert decide(-0.001, 9.0)[0] == "CAP_ZERO_OR_NEGATIVE"
    assert decide(-5.0, -9.0)[0] == "CAP_ZERO_OR_NEGATIVE"

    # 三个标签拼写也钉死：报告里引用的是这三个字符串
    for tag in ("CAP_FACTOR_WORKS", "CAP_POSITIVE_NOT_SIG", "CAP_ZERO_OR_NEGATIVE"):
        assert tag in {decide(a, b)[0] for a in (-1, 0, 1) for b in (None, 0, 9)}


def test_verdict_module_never_hardcodes_the_critical_value():
    """判决模块里不许出现 1.96 字面量 —— 临界值必须从 `factor_attribution` 来。

    用 AST 核浮点常量，不是核子串：文档字符串里写一句「≈1.96」不构成违规，
    而 `if t >= 1.96:` 在源码里就是一个 `Constant` 节点。
    """
    from factor_attribution import T_CRIT_5PCT as REF
    from cap_weighted_benchmark import T_CRIT_5PCT as GOT
    assert GOT == REF, "t_crit 不是从 factor_attribution 拿的那一个"

    for mod in ("cap_weighted_benchmark.py",):
        src = (ROOT / "scripts" / mod).read_text(encoding="utf-8")
        tree = ast.parse(src)
        hard = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if abs(node.value - 1.96) < 0.01:
                    hard.append((mod, node.lineno, node.value))
        assert not hard, f"{mod} 里硬写了近似 1.96 的浮点字面量：{hard}"


def test_cost_assumptions_are_not_redefined_anywhere_in_the_experiment():
    """预注册 §4：成本假设、MIN_BASKET 规则、K、窗口起点——一个都不许在本脚本里重定义。"""
    src = (ROOT / "scripts" / "cap_weighted_benchmark.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
    for banned in ("FEE_SCHEDULE", "COST_REAL_50K", "MIN_BASKET",
                   "COST_REAL_CAPITAL", "EQUITY_WEIGHT"):
        assert banned not in assigned, (
            f"实验脚本自己定义了 {banned} —— 成本/成分下限必须只有一处真相")
    assert "WINDOW_START = " not in src, "窗口起点被在实验脚本里重新定义"
    assert 'default="2021-01-01"' in src, "窗口起点必须是预注册的 2021-01-01"


# ── 产物：A 臂必须复现已发布数字 ────────────────────────────────────

def test_arm_a_reproduces_the_published_factor_width_number():
    """A 臂必须复现 `factor_width_mde.json` 的 N=200 数字（1e-9 内）。

    这是整条链最强的端到端证据：窗口、K、MIN_BASKET 比例化规则、
    逐年权重、成本链、逐日聚合、分块 —— 全部与那次测量**同一个量**。
    对不上 ⇒ 本实验的 A/B/C 三臂至少有一臂不是同一个量。
    """
    ref = json.loads((ROOT / "data" / "reports" / "factor_width_mde.json")
                     .read_text(encoding="utf-8"))
    out_path = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    if not out_path.is_file():
        pytest.skip("实验尚未运行")
    got = json.loads(out_path.read_text(encoding="utf-8"))

    want = ref["widths"]["200"]["gross_annual_pct"]
    have = got["arms"]["A"]["gross_annual_pct"]
    assert got["arms"]["A"]["n_days"] == ref["widths"]["200"]["n_days"], (
        f"A 臂天数 {got['arms']['A']['n_days']} != 已发布 {ref['widths']['200']['n_days']}"
        f" —— 窗口终点不同，两臂不是同一个量")
    assert have == pytest.approx(want, abs=1e-9), f"A 臂 {have!r} != 已发布 {want!r}"


def test_artifact_reports_the_mandatory_diagnostics_and_the_decomposition():
    """产物必须带齐预注册 §3 的诊断 + `C − B`，且三臂各自的 `n_days` 与实际终点。"""
    out_path = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    if not out_path.is_file():
        pytest.skip("实验尚未运行")
    out = json.loads(out_path.read_text(encoding="utf-8"))

    for arm in ("A", "B", "C"):
        assert out["arms"][arm]["n_days"] > 0, f"{arm} 臂没打印 n_days"
        assert out["arms"][arm]["window_end"], f"{arm} 臂没打印实际窗口终点"
        assert out["arms"][arm]["gross_annual_pct"] is not None
        assert out["arms"][arm]["t_blocked"] is not None

    d = out["diagnostics"]
    for key in ("cap_exposure_ratio", "c_minus_b", "a_shares_t_blocked"):
        assert key in d, f"缺诊断 {key}"
    assert d["c_minus_b"] == pytest.approx(
        out["arms"]["C"]["gross_annual_pct"] - out["arms"]["B"]["gross_annual_pct"],
        abs=1e-12), "C − B 不是真的减出来的"
    assert "verdict" in out and out["verdict"] in (
        "CAP_FACTOR_WORKS", "CAP_POSITIVE_NOT_SIG", "CAP_ZERO_OR_NEGATIVE")


def test_the_artifacts_verdict_is_the_one_decide_gives_for_arm_c():
    """🚨 产物里的 `verdict` 必须**就是** `decide(C 臂)` 算出来的那一个（2026-10-15 审查补）。

    ## 为什么「只做成员检查」不够 —— 这是能把负面结论翻成正面结论的那种洞

    旁边那条只断言 `out["verdict"] in ("CAP_FACTOR_WORKS", "CAP_POSITIVE_NOT_SIG",
    "CAP_ZERO_OR_NEGATIVE")`。它核的是**字符串落在三个字面量集合内**，
    而**不是「它是 C 臂算出来的那个」**。全仓没有任何守卫断言
    `out["verdict"] == decide(C.gross, C.t)`。

    ⇒ 具体反例（审查 agent 实测过）：把 `scripts/cap_weighted_benchmark.py:422`
      的 `decide(arms["C"][...])` 改成 `decide(arms["B"][...])`。
      B 臂 gross = **+14.99%**、t = **2.2914 ≥ 1.96**
      ⇒ 判决变成 **`CAP_FACTOR_WORKS`「因子有效」**，三处相关守卫**全绿**。
    ⇒ 真实结论是「C 臂 t = 1.638，未显著」，报告却会把「因子有效」绿灯出去，
      **且没有任何东西会喊**。

    ⇒ 这是 CLAUDE.md 形状 6「看起来像开关，实际焊死」的**判定侧**版本：
      输出合法、格式正确，但**与它声称依据的那个量脱钩**。

    ★ 所以这条守卫的正控方向是「判决读到 B 臂 ⇒ 必须红」，
      而**不是**「三个分支都被覆盖」（那个已由
      `test_verdict_branches_are_the_preregistered_three` 在进程内穷举负责）。
      两件事：**分支表对不对** vs **产物用的是不是这张表的输出**。
    """
    from cap_weighted_benchmark import T_CRIT_5PCT, decide
    out_path = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    if not out_path.is_file():
        pytest.skip("实验尚未运行")
    out = json.loads(out_path.read_text(encoding="utf-8"))

    c = out["arms"]["C"]
    want_verdict, want_branch = decide(c["gross_annual_pct"], c["t_blocked"])
    assert out["verdict"] == want_verdict, (
        f"产物判决 {out['verdict']!r} ≠ decide(C 臂 gross={c['gross_annual_pct']!r}, "
        f"t={c['t_blocked']!r}) = {want_verdict!r}。"
        f"🚨 判决与 C 臂脱钩 —— 最坏的一步是它读了 B 臂："
        f"B 的 t={out['arms']['B']['t_blocked']:.4f} ≥ t_crit ⇒ 会印「因子有效」，"
        f"而真实结论是「C 臂 t={c['t_blocked']:.4f} 未显著」。")
    assert out["verdict_branch"] == want_branch, "判决理由与判决不同源"

    # 防呆：`t_crit` 必须是同一个（判决与守卫不许各持一份）
    assert out["t_crit"] == pytest.approx(T_CRIT_5PCT, abs=1e-12), (
        f"产物用的 t_crit={out['t_crit']!r}，模块现值 {T_CRIT_5PCT!r}")


def test_the_artifact_was_produced_by_the_bytes_now_on_disk():
    """★ **0 号守卫**：产物必须由**当前磁盘上那份脚本的字节**跑出来。

    ## 它是被同一次审查的发现 5 逼出来的

    本文件有**四条**产物层守卫只读一次落盘 JSON，
    而 `mutation_check.py` 那 24 条 `cap_weighted` 变异**全部改 Python 源码、不改产物**
    ⇒ 那些变异在这四条守卫上**结构性 GREEN**。
    ⇒ 它们守的是「上次那份东西对不对」，不是「这段代码会不会产出对的东西」
    （CLAUDE.md：产物层守卫对当前代码零覆盖）。

    ⚠️ 四条产物守卫在产物缺失时是 `pytest.skip` ⇒ 干净 CI 上产物层零覆盖**且不报错**。

    ## 判据取**内容**而不是 mtime —— 因为本仓有实测的反例

    本产物 mtime 只比脚本新 **4.73 秒**，而 CLAUDE.md 形状 21 记的那个事故是 **0.31 秒**
    （产物比脚本新，却出自盖章**之前**的字节，因为 Python 在 import 那一刻就绑定了源码）。
    ⇒ 「产物 ≥ 脚本」这种纯 mtime 守卫**分不开**这两种情形。

    而本产物**有**字节证据：sidecar 的 `code_file_sha256` 与脚本当前字节哈希**逐位相等**。

    🚨 **双向**：字节相同 ⇒ `fresh`（免掉「改完提交掉」造成的假阳性重跑）；
      字节不同 ⇒ `stale`（堵住「改了没提交」造成的假 fresh，第 25 种形状的后一种盲区）。
    🚨 **降级必须说出口**：无该字段（旧章）时退回 mtime，
      并在守卫输出里标注这是**降级判据** —— 两种强度差一个量级的绿不许长得一样。
    """
    art = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    src = ROOT / "scripts" / "cap_weighted_benchmark.py"
    if not art.is_file():
        pytest.skip("实验尚未运行（产物层守卫此时零覆盖且不报错）")
    side = art.parent / f"{art.name}.provenance.json"
    assert side.is_file(), (
        f"缺 sidecar {side.name} ⇒ 这份产物按 aplus-measurement-integrity 的规矩是 "
        f"`unknown`，**不许引用**；而下面四条产物层守卫此刻正拿它当证据。")
    stamp = json.loads(side.read_text(encoding="utf-8"))
    assert stamp.get("generator") == "scripts/cap_weighted_benchmark.py", (
        f"sidecar 的 generator 是 {stamp.get('generator')!r}")

    now = hashlib.sha256(src.read_bytes()).hexdigest()
    stamped = stamp.get("code_file_sha256")
    if stamped is None:
        gap = art.stat().st_mtime - src.stat().st_mtime
        assert gap >= 0, (
            f"⚠️ **降级判据**（sidecar 无 `code_file_sha256`，只能比 mtime）："
            f"产物比脚本旧 {abs(gap):.0f} 秒 ⇒ 脚本改过而没重跑。\n"
            f"**修完 P0 一律重跑，重跑前绝对收益数字不可引用。**")
        return

    assert stamped == now, (
        f"产物不是当前脚本的字节跑出来的：\n"
        f"  sidecar 盖章时 {stamped}\n"
        f"  磁盘现在   {now}\n"
        f"⇒ 下面四条产物层守卫正拿一份**陈旧产物**当证据，而它们自己全绿。"
        f"重跑：.venv/bin/python scripts/cap_weighted_benchmark.py")


def test_the_tolerances_in_this_file_are_not_silently_widened():
    """★ **容差必须被单独测一条**（CLAUDE.md 明写，2026-10-15 审查发现本文件从未落地）。

    ## 为什么容差本身要守

    本文件里散布着 `abs=1e-9` / `1e-12` / `1e-15` 若干处。把某一个悄悄放宽到 `1e-6`，
    **没有任何东西会发现** —— 守卫照绿，而它守的精度已经没了。
    CLAUDE.md 把「过严的守卫」列为头号成因，而「容差被放宽」正是过严的常见入口。

    ## 顺带清掉一条化石

    `:25` 的 `import math` **全文没有任何 `math.` 引用** —— 它是「这条规则从未落地」的化石。
    """
    self_src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(self_src)
    found: list[tuple[str, float]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "approx"):
            continue
        for kw in node.keywords:
            if kw.arg not in ("abs", "rel") or not isinstance(kw.value, ast.Constant):
                continue
            if isinstance(kw.value.value, (int, float)):
                found.append((kw.arg, float(kw.value.value)))
    assert found, "本文件一条 `approx(...)` 都没匹配到 ⇒ 解析器坏了，不是「容差不存在」"
    loose = [f"{k}={v:g}" for k, v in found if v > 1e-9]
    assert not loose, (
        f"容差被放宽到 {loose}（上界 1e-9）⇒ 精度门槛没了而守卫全绿。"
        f"实测全部容差：{sorted(set(f'{k}={v:g}' for k, v in found))}")

    # ★ 探针自证：构造出来的偏差**确实 ≠ 0**，否则连假测试都拦不住
    a = 1.0
    nudged = math.nextafter(a, 2.0) - a            # 只差最后一个 ulp
    assert nudged != 0.0 and abs(nudged) > 0.0, "nextafter 探针自己坏了"
    assert abs(nudged) < 1e-15, (
        f"ulp 级偏差 {nudged:.3e} 已经大过 1e-15 ⇒ "
        f"用 1e-15 容差的守卫会误杀一个完全正常的浮点结果")
    """表外的报告会被报成 unknown ⇒ 不许引用。登记与 sidecar 必须都在。"""
    from aplus.provenance import DEFAULT_GENERATORS, resolve_generator
    name = "cap_weighted_benchmark.json"
    assert name in DEFAULT_GENERATORS, f"{name} 未登记在 DEFAULT_GENERATORS"
    assert resolve_generator(name) == "scripts/cap_weighted_benchmark.py"
    out_path = ROOT / "data" / "reports" / name
    if out_path.is_file():
        # ⚠️ 侧卡的命名是 `<报告名>.provenance.json`（`provenance.stamp` 的规矩，
        #   全仓 12 份 sidecar 都是这个形状），不是 `<stem>.provenance.json`。
        #   写成 stem 会得到一条永远红的守卫 —— 而永远红的守卫和恒真的一样有害。
        assert (out_path.parent / f"{out_path.name}.provenance.json").is_file(), (
            f"报告存在但没有 sidecar（{out_path.name}.provenance.json）—— "
            "登记不等于真盖章了")


# ── 档位必须真的不同（否则三臂是同一个数在演三次） ────────────────────

def test_the_three_arms_are_actually_three_different_measurements():
    """B 与 C 的组合端口径不同 ⇒ 数字必须不同（除非市值权重恰好均匀）。

    三臂逐位相同说明「cap 参数」根本没接上，
    而那正是本实验唯一想改的东西。
    """
    out_path = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    if not out_path.is_file():
        pytest.skip("实验尚未运行")
    out = json.loads(out_path.read_text(encoding="utf-8"))
    a, b, c = (out["arms"][x]["gross_annual_pct"] for x in "ABC")
    assert a != b, "A 与 B 相同 —— 基准端的市值加权没生效"
    assert b != c, "B 与 C 相同 —— 组合端的市值加权没生效"
    assert out["arms"]["B"]["benchmark_weighting"] == "cap"
    assert out["arms"]["C"]["benchmark_weighting"] == "cap"
    assert out["arms"]["C"]["portfolio_weighting"] == "cap"
    assert out["arms"]["A"]["portfolio_weighting"] == "equal"


def test_the_negative_control_still_lands_on_arm_b():
    """🚨 产物里的 `V_uniform_control` 此前**全仓无守卫**（2026-10-15 审查补上）。

    ## 它为什么不可省

    `test_market_by_day_uniform_caps_reproduce_the_equal_weight_series` 那条
    守的是**构造帧**：喂全相等假市值 ⇒ 加权必须退化成等权。
    它能抓住「归一化/加权那一段写坏了」，但它**守不到**
    「真实数据上 `weight_override` 到底有没有被接上」。

    ⇒ 这两件事是独立的：
      ① 加权算法对不对   —— 构造帧守
      ② 加权在真跑里生效没有 —— **只有产物层的 V 臂能证**

    真实数据上若 `weight_override` 根本没接进主路径（例如某次重构把它
    改成只在单元测试里可用的参数），B 与 C 的差就会变成别的东西，
    而 `equals_B: false` **不会有任何东西喊**。

    ## 为什么这是「唯一能证明 B→C 归因正确」的那条

    判决链是：`C − B = −2.50%/年` 解释为「组合端市值加权的贡献」。
    这个解释成立的**前提**是 B 与 C 的差**只**来自组合端。
    V 臂喂全等市值 ⇒ 组合端加权退化为等权 ⇒ 它必须精确落回 B。
    V 不落回 B ⇒ B 与 C 的差里混了别的东西 ⇒ 那句解释作废。
    """
    out_path = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    if not out_path.is_file():
        pytest.skip("实验尚未运行")
    out = json.loads(out_path.read_text(encoding="utf-8"))

    # ── 防空真：断言探针**真的读到了** controls 块 ───────────────────
    #   键名一改，`ctrl = out.get("controls", {})` 会得到空字典，
    #   后面那句 `ctrl["V_uniform_control"]` 抛 KeyError ⇒ 红。
    #   但若有人把它写成 `.get(..., {})` 再取子键，就会**静默跳过全部断言**
    #   ⇒ 那条守卫会永远绿，而它正是恒真守卫最常见的死法。
    #   所以这里显式断言「探针扫到了东西」。
    ctrl = out.get("controls")
    assert isinstance(ctrl, dict) and ctrl, (
        "产物里没有非空的 `controls` 块 —— 负控臂没落盘。"
        "没有它就无法证明 B→C 的差只来自组合端")
    assert "V_uniform_control" in ctrl, (
        f"controls 的键是 {sorted(ctrl)}，缺 V_uniform_control")

    v = ctrl["V_uniform_control"]
    assert v.get("equals_B") is True, (
        f"V 对照臂没有落回 B 臂（equals_B={v.get('equals_B')!r}）"
        " ⇒ B 与 C 的差里混了别的东西，`C − B` 不能解释成"
        "「组合端市值加权的贡献」")

    # 逐位：B 臂的毛超额必须与 V 臂相同（同一次抽样下的同一段代码）
    b_gross = out["arms"]["B"]["gross_annual_pct"]
    assert v["gross_annual_pct"] == pytest.approx(b_gross, abs=1e-12), (
        f"V 臂 {v['gross_annual_pct']!r} != B 臂 {b_gross!r}")
    assert v.get("relative_diff_vs_B") == pytest.approx(0.0, abs=1e-12), (
        f"相对差 {v.get('relative_diff_vs_B')!r} ≠ 0")

    # ── `controls.V` 与 `arms.V` 必须是**同一次计算的两个视图** ──────────
    #   脚本把同一臂存两处：`:376` 进 `arms`（完整 stats）、
    #   `:453` 进 `controls`（摘要 + 对拍结论）。
    #   上面三条全部拿 `controls` 去对 `arms.B`，所以一旦有人让
    #   `controls` 那份**另跑一次**或从别处拼出来，
    #   整条守卫就在拿一个来源不明的数对 B —— 而 `arms.V` 无人过问。
    #   ⇒ 这里把两个视图钉成同一个数。
    v_arm = out["arms"]["V_uniform_control"]
    assert v_arm["gross_annual_pct"] == pytest.approx(
        v["gross_annual_pct"], abs=1e-15), (
        f"controls.V({v['gross_annual_pct']!r}) 与 arms.V"
        f"({v_arm['gross_annual_pct']!r}) 不是同一个数 ⇒ "
        f"对拍拿的可能是另一次计算的结果")

    # 标签只作**可读性**检查，且锚在 `arms.V`（唯一带 `portfolio_weighting` 的地方）：
    #   它的值是 `cap(uniform)` —— 说的是「走的是市值加权那一段代码，
    #   喂的是全等假市值」，**不是** `equal`。
    #   ⚠️ 这里第一版断言成 `"equal" in ...portfolio_weighting`，
    #   而那条守卫当时是**红的** —— 红的原因是**我假设了一个不存在的字段**，
    #   不是实现有缺陷。别拿「变绿」当理由把实现改成满足断言。
    assert v_arm.get("portfolio_weighting") == "cap(uniform)", (
        f"V 臂的组合端口径标签是 {v_arm.get('portfolio_weighting')!r}，"
        f"应当是 `cap(uniform)` —— 它说的是「走市值加权那段代码 + 全等假市值」")


def test_a_non_finite_input_is_refused_rather_than_falling_into_a_preregistered_branch():
    """🚨 NaN **不属于任何一支预注册判决**，不许滑进 `CAP_POSITIVE_NOT_SIG`。

    ★ 实测原写法（2026-10-15）：`NaN <= 0` 与 `NaN >= t_crit` **都是 False**
      ⇒ 两支都被跳过 ⇒ 落进第三支，而第三支的文案把它**印成一次测量**：

      | 入参 | 原判决 | 原文案 |
      |---|---|---|
      | `gross=NaN, t=None` | `CAP_POSITIVE_NOT_SIG` | 「C 臂毛超额 **+nan%/年 > 0**」 |
      | `gross=.05, t=NaN`   | `CAP_POSITIVE_NOT_SIG` | 「… **t = nan < 1.9600**」 |

      ⇒ 那不是「缺档被读成结论」，是**凭空造出一个方向性结论**：
        `t = nan < 1.96` 读起来就是「测得偏小」。

    ⇒ 与 `factor_attribution` 的 `isfinite` vs `!= 0` **同型**：
      NaN 与所有值比较都是 False，于是「不满足任何条件」=
      「不满足任何**可观测**条件」。
    """
    import math
    from cap_weighted_benchmark import decide

    nan = float("nan")
    for gross, t, tag in ((nan, None, "毛超额 NaN"),
                          (nan, nan, "两个都 NaN"),
                          (0.05, nan, "只有 t 是 NaN")):
        v, why = decide(gross, t)
        assert v == "CAP_UNMEASURABLE", \
            f"{tag} 应当拒绝判定，却落进了 {v}"
        # ★ 文案本身也不许把 NaN 印成一次测量或一个方向
        assert "nan <" not in why, f"{tag}：文案把 NaN 印成了「测得偏小」：{why}"
        assert "方向为正" not in why, f"{tag}：文案凭空断言了方向：{why}"
    # 正控：有限的 0 / ±inf 之外的值不受影响
    assert decide(0.05, 5.0)[0] == "CAP_FACTOR_WORKS"
    assert decide(0.05, 1.5)[0] == "CAP_POSITIVE_NOT_SIG"
    assert decide(0.0, 9.0)[0] == "CAP_ZERO_OR_NEGATIVE"
    # ±inf 同样不是有限值
    assert decide(math.inf, 5.0)[0] == "CAP_UNMEASURABLE"
    assert decide(0.05, -math.inf)[0] == "CAP_UNMEASURABLE"
