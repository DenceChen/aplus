#!/usr/bin/env python
"""§8.2 的 ① 组合净值 —— **判决档只读产物字段**，口径见预注册 §15。

🚨 本脚本**不算** ② 账户净值，也**不许**把 ① 写成「能做到」。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

def _repo_root() -> Path:
    """向上找 `aplus/hurdle.py` 作为仓库根标记。

    ⚠️ 不写死 `parents[1]`：那在 `/tmp` 下是 `/`，
    `from aplus.hurdle` 会炸成一个与算法无关的 ImportError。
    """
    here = Path(__file__).resolve()
    for cand in (here.parents[1], *here.parents, Path.cwd(), *Path.cwd().parents):
        if (cand / "aplus" / "hurdle.py").exists():
            return cand
    raise SystemExit("找不到仓库根（缺 aplus/hurdle.py）")


ROOT = _repo_root()
sys.path.insert(0, str(ROOT))
from aplus.hurdle import DAYS_PER_YEAR as R          # noqa: E402  244，唯一真相源
from aplus import provenance                        # noqa: E402

#: 自己的路径，写进溯源章 —— `code_file_sha256` 算的就是这份文件的字节。
GENERATOR = "scripts/measure_basket_nav_01.py"

ART = ROOT / "data/reports/wf_weights_backtest_k21.daily.parquet"
CALIBER = ROOT / "data/reports/market_nav_caliber.json"
TARGET = 0.20
# 超额腿的键名前缀：`净超额@<组合>`。⚠️ 单一真相源 —— `main()` 建腿与
# `excess_contribution()` 取腿**都必须**用它，不许两处各硬编码一个字符串。
EXCESS_LEG_PREFIX = "净超额@"

#: 判决基准臂 —— **预注册选定的**，不许因为读数难看就换成另一支（第 91 层：
#: 看了数据再改判据）。守卫：
#: `tests/test_nav_caliber_judgment.py::test_the_verdict_benchmark_is_the_equal_weight_arm_and_not_the_cap_weighted_one`
VERDICT_BENCH_ARM = "equal_weight"
#: 敏感性臂 —— 只报「换一把尺子会怎样」，**不是**判决臂。
#: 🚨 实测两臂的「达标所需超额」差 **7.9 倍**（6.27% vs 49.78% 的实测/需要比值），
#: 而真毛值在 `gross.annual_pct`（2021 是 −0.0768 不是 −0.1106）
#: ⇒ 只报一支会让人以为「离目标多远」是有答案的问题。
SENSITIVITY_BENCH_ARMS = ("cap_weight",)
#: `market_nav_caliber.json` 的 `*_pct` 字段是**百分数**（`9.9471` = 9.9471%），
#: 本脚本其余量（`cagr` / `mde_as_annual_pct`）是**小数**。
#: 🚨 两者字段名同为 `*_pct` 而单位相反 —— 2026-10-16 我第一次接 caliber 时
#: 就把 `9.9471` 当小数用，算出「基准年化 +994.71%」。
#: ⇒ 全仓**第一处生产侧**换算（测试里的 `_as_fraction` 不算生产侧）在此，
#:    守卫见 `tests/test_basket_nav_01_verdict.py::test_the_caliber_percent_scale_is_converted_exactly_once`。
CALIBER_PCT_SCALE = 100.0


def tier(name: str, r: np.ndarray) -> dict:
    """**上界/点估计**一律复利；算术只作敏感性并排印出。"""
    log1p = np.log1p(r)
    n = len(r)
    total = float(np.prod(1.0 + r) - 1.0)
    years = n / R
    cagr = None if total <= -1.0 else float((1.0 + total) ** (1.0 / years) - 1.0)
    # 🚨🚨 年化**乘 R**，不乘 √R。这是本仓最容易改错的一处（2026-10-16 实测差点改错）。
    #
    # 推导（设 log(1+rᵢ) = μ + εᵢ，ε iid，sd = σ，years = n/R）：
    #     年化 = Σlog(1+rᵢ) × R/n = Rμ + (R/n)·Σεᵢ
    #     Var  = (R/n)² · n·σ² = R²σ²/n   ⇒   **sd = Rσ/√n**
    #
    # ⚠️ `σ√R`（年化**波动**）**不是**同一个量：它是「持有一年」那**一个**观测的 sd，
    #    而这里是「由 n 天估出的年化率」的标准误。两者差 `R/√R = √R = 15.62` 倍。
    # ⚠️ 用模拟验过：n=1395、sd=0.0155、4000 次重复时，经验 sd(年化点估计) ≈ 0.101
    #    （有限次抽样，故与推导值 0.1013 有 0.4% 的差），**等于 `σ×R/√n`**；
    #    若按 `σ√R/√n` 算只有 0.00648，**差 15.6 倍**。
    mde = float(1.96 * log1p.std(ddof=1) / np.sqrt(n)) if n > 1 else None
    # 外部锚（Chan p.136-137）：几何随机游走下平均复利率 g = m − s²/2。
    # ⚠️ 这是**与文献公式**对拍，不是「用公式重算自己的字段」（第 109 层）。
    vol = float(r.std(ddof=1)) * np.sqrt(R)
    chan_g = float(r.mean()) * R - vol ** 2 / 2.0 if n > 1 else None
    return {
        "tier": name,
        "chan_g_approx": chan_g,
        "chan_gap_pp": (None if (chan_g is None or cagr is None)
                        else (cagr - chan_g) * 100.0),
        "n_days": n,
        "years": round(years, 6),
        "compounded_total": total,
        "cagr": cagr,
        "arith_ann": float(r.mean()) * R,
        "vol_drag": (float(r.mean()) * R - cagr) if cagr is not None else None,
        "ann_vol": float(r.std(ddof=1)) * np.sqrt(R),
        "mde_log": mde,
        "mde_as_annual_pct": None if mde is None else mde * R,
    }


def account_nav(df: pd.DataFrame, leg: str) -> np.ndarray:
    """账户净值日收益 = `market_return` + 该腿的净超额**差序列**。

    🚨🚨 **不许**把 `net_excess_*` 直接当净值序列（§18.1 实测：差 10.01 个百分点）。
    它的定义就是「组合 − `market_return`」，是**差**，不是被估量。

    恒等式 `market + net_excess == 涨篮子净` 是**定义式**（`net_excess := 篮子净 − market`）
    ⇒ 它**恒真**，所以**定不了任何一方的语义**（§18.2）。
    """
    return df["market_return"].to_numpy(float) + df[leg].to_numpy(float)


def excess_contribution(cb: float, cm: float, yrs: float,
                        legs: dict, nm: str) -> dict:
    """超额贡献（**比值**口径）+ 它自己的 MDE（**必须取同源那条超额腿**）。

    🚨 复利年化**不可相减**：`c_bk − c_mkt` 是「分数减分数」得到的**差**，
    不是比值的年化（实测差 10 倍量级）。唯一口径是 `((1+c_bk)/(1+c_mkt))^(1/y) − 1`。

    ⚠️ `mde_of_the_underlying_excess_leg` 取的是**该组合自己那条净超额腿**的 MDE，
    不是账户 MDE、也不是市场腿 MDE —— 把 MDE 取成别的腿，`|x|/M` 就变成另一个量，
    而读数照样印得出来（实测 bt003 若误取毛腿 MDE，`|x|/M` 会从 0.0090 变成 0.0090±…）。
    所以**取腿的约定写在本函数里**（`EXCESS_LEG_PREFIX + nm`），守卫能直接测到。
    """
    key = EXCESS_LEG_PREFIX + nm
    if key not in legs:
        raise KeyError(f"没有 {key} 这条超额腿；现有腿 = {sorted(legs)}")
    Mx = legs[key]["mde_as_annual_pct"]
    val = ((1 + cb) / (1 + cm)) ** (1 / yrs) - 1
    return {"annual_pct": val, "mde_of_the_underlying_excess_leg": Mx,
            "excess_leg_key": key,
            "abs_over_mde": abs(val) / Mx,
            "sign_determinable": bool(abs(val) >= Mx)}


def caliber_benchmark_fraction(cal: dict, arm: str) -> float:
    """从 caliber 读某条基准臂的几何年化，返回**分数**。

    ⚠️ caliber 的 `*_pct` 是**百分数**（`9.9471` = 9.9471%），而本脚本其余量
       （`cagr` / `mde_as_annual_pct`）是**小数** ⇒ 两个字段名同为 `*_pct`
       而单位相反。2026-10-16 第一次接 caliber 时就把 `9.9471` 当小数用，
       算出「基准年化 +994.71%」。
    ⇒ 换算**只许在这里发生一次**（`CALIBER_PCT_SCALE` 是唯一真相源）。

    🚨 `arm` 必须显式传入、且必须真在 caliber 里 —— **不许有「默认那条」**：
    「判决臂 vs 敏感性臂」的区分本身就是要守的东西（第 119 层：换了被诊断的对象，
    而读数照样印得出来）。
    """
    if not isinstance(cal.get(arm), dict) or "geometric_annual_pct" not in cal.get(arm, {}):
        have = sorted(k for k, v in cal.items()
                      if isinstance(v, dict) and "geometric_annual_pct" in v)
        raise KeyError(f"caliber 没有 {arm!r} 这条基准臂；现有臂 = {have}")
    return float(cal[arm]["geometric_annual_pct"]) / CALIBER_PCT_SCALE


def required_excess(target: float, c_mkt: float, yrs: float) -> float:
    """达标所需的**超额贡献**（**年化**，比值口径，与 `excess_contribution()` 同口径）。

    🚨🚨 **必须年化**。未年化的 `(1+T)/(1+c_mkt) − 1` 是**每期**比值，
    把它标成「/年」就会与年化的实测混在一个比值里 ——
    2026-10-16 实测这个错让「实测/需要」从 **6.27%** 变成 **1.06%**（差 5.93×），
    而错的那个数被写进了预注册文档。
    ⇒ 判据（不是注释）：年化后必然满足 `(1 + 本函数) ** yrs == (1+target)/(1+c_mkt)`，
      这是几何均值的**定义**（与本函数实现路径无关 ⇒ 不是第 109 层的「用公式重算」）。

    ⚠️ 这个量**不是**判决量：达标判决用 `judge(c_account, M_account, …)`，
       账户净值本身**基准无关**（`market + net_excess ≡ 篮子净`）⇒ 不需要基准。
       本函数只回答「若市场腿是 X，账户还差多少超额」。
    ⚠️ 它继承 `c_mkt` 的不可测性（`|c_mkt| < M_mkt`）⇒ 形状陈述，不是点估计。
    """
    if yrs <= 0.0:
        raise ValueError(f"years 必须为正，实得 {yrs!r}")
    return ((1.0 + target) / (1.0 + c_mkt)) ** (1.0 / yrs) - 1.0


def judge(cc: float, MM: float, label: str) -> tuple[list[str], float]:
    """预注册 §16 的判决规则（**四条穷举、且第一条与其余三条并列**）。

    返回 ``(cases, U)``。⚠️ `cases` 可能**多于一条**：`|`c| < M` 与后面三条
    **不互斥**（第 160 层）⇒ 只印最宽松那条会把「连符号都定不了」吞掉。

    ⚠️ 文案里的目标数来自 `TARGET` 常量，**不许**再写字面量 `20%`
    （改 `TARGET` 而文案不动 = 第 73 层）。
    """
    pct0 = f"{TARGET:.0%}"
    cases: list[str] = []
    if abs(cc) < MM:
        cases.append("`|c| < M` ⇒ 🚨 **连符号都定不了**（第 160 层：不可测的点估计只支持形状陈述）")
    uu = cc + MM
    if uu < TARGET:
        cases.append(f"`U < T` ⇒ 「{label} 的 95% 单侧上界 {uu:+.2%} < {pct0} ⇒ "
                     f"**在 95% 置信下**账户净值年化 {pct0} **不可达**」")
    elif cc < TARGET <= uu:
        cases.append(f"`c < T ≤ U` ⇒ 🚨 **「测不出」**：点估计 {cc:+.2%} 低于目标，"
                     f"但 95% 上界 {uu:+.2%} 跨过 {pct0} ⇒ "
                     "**既不许说「达不到」，也不许说「接近」**")
    else:
        cases.append("`c ≥ T` ⇒ 不许写「能做到」⇒ 转 §8.2 的 ② 账户净值（Y4）")
    return cases, uu


def main() -> int:
    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**（`main()` 第一行）；传 None 会让 stamp() **整项跳过**
    #   字节检查而无人察觉（第 131 层的形态：关键字在、检查没跑）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    df = pd.read_parquet(ART)
    cols = list(df.columns)
    print(f"产物 {ART.name}")
    print(f"  sha256 = {hashlib.sha256(ART.read_bytes()).hexdigest()}")
    print(f"  行数 = {len(df)}   列 = {cols}")
    need = ["market_return", "net_excess_bt003", "net_excess_real"]
    missing = [c for c in need if c not in cols]
    if missing:
        print(f"\n🚨 缺列 {missing} ⇒ ① 的输入仍不存在，重跑没生效，不得引用任何数字。")
        return 2

    mkt = df["market_return"].to_numpy(float)
    rows = [
        tier("c=0（market_return 本身，抽样面板等权**毛**收益）", mkt),
        tier("① 判决档：market_return + net_excess_bt003",
             account_nav(df, "net_excess_bt003")),
        tier("参考档（非判决）：market_return + net_excess_real",
             account_nav(df, "net_excess_real")),
    ]
    # 外部锚：毛 − 净 ≡ 逐日已摊成本（会计恒等式，只靠减法）
    gross = account_nav(df, "gross_excess")
    print("\n=== 三档（复利为主口径，算术并排）===")
    for r in rows:
        pct = lambda k: "—" if r[k] is None else f"{r[k]:+.4%}"
        print(f"\n{r['tier']}")
        print(f"  n={r['n_days']}  years={r['years']:.4f}  累计={r['compounded_total']:+.4%}")
        print(f"  复利年化 = {pct('cagr')}      ← 主口径")
        print(f"  算术年化 = {pct('arith_ann')}      ⚠️ 敏感性栏，不许下判决")
        print(f"  波动拖累 = {pct('vol_drag')}      年化波动 = {r['ann_vol']:.4%}")
        print(f"  Chan 闭式 g=m−s²/2 = {pct('chan_g_approx')}"
              f"      差 {r['chan_gap_pp']:+.4f}pp   ← 外部锚，非自证")
        if r["mde_as_annual_pct"] is not None:
            print(f"  MDE(95%,对数) = ±{r['mde_as_annual_pct']:.4%}/年   ← 与点估计并排")

    v = rows[1]
    c, M = v["cagr"], v["mde_as_annual_pct"]
    U = c + M
    print(f"\n=== 判决（§16 修正后的穷举规则，由程序判定）===")
    print(f"  c = ① 复利年化      = {c:+.4%}")
    print(f"  M = ① 的 MDE(95%)   = ±{M:.4%}/年")
    print(f"  U = c + M (单侧上界) = {U:+.4%}")
    print(f"  T = 目标            = {TARGET:+.0%}")
    print(f"  外部锚 毛(bt003 档 + 已摊成本) − 毛 = "
          f"{float(np.mean(gross - account_nav(df, 'net_excess_bt003'))):+.6f}")

    # ---- §16 的规则，**互斥且穷举**；同一套规则，三档各跑一遍 ----
    # ⚠️ 判决档仍是 bt003（§15.2 预注册）；这里只是把同一条规则应用到另外两档，
    #    **判据文本一字未改**，不是改判据。规则本体在**模块级** `judge()`（可被守卫 import 测）。
    cases, U = judge(c, M, "①")
    print(f"\n  🚨 命中的规则（{len(cases)} 条并列印出，不许只印最宽松的那条）：")
    for i, t in enumerate(cases, 1):
        print(f"    {i}. {t}")
    verdict = cases[-1] if len(cases) == 1 else "‖".join(cases)
    print(f"\n  ⇒ 判决（唯一允许的措辞）= {verdict}")

    print("\n=== 同一套规则施加于另外两档（判决档不变，只登记）===")
    all_verdicts = {}
    for r in rows:
        if r["tier"] == v["tier"]:
            continue
        f, _ = judge(r["cagr"], r["mde_as_annual_pct"], r["tier"][:20])
        all_verdicts[r["tier"]] = f
        for s in f:
            print(f"  [{r['tier'][:20]}] {s}")

    print("\n=== 🚨 更硬的一栏：「选股到底有没有用」——超额腿自己的 MDE ===")
    print("   （账户净值那一栏分不清，是因为市场腿占了 24%/年 的波动；这一栏只看差）")
    excess_legs = {}
    for nm, col in (("毛超额", "gross_excess"),
                    (EXCESS_LEG_PREFIX + "bt003", "net_excess_bt003"),
                    (EXCESS_LEG_PREFIX + "real", "net_excess_real")):
        rr = df[col].to_numpy(float)
        t = tier(nm, rr)
        ratio = abs(t["cagr"]) / t["mde_as_annual_pct"]
        excess_legs[nm] = {**t, "abs_cagr_over_mde": ratio,
                           "sign_determinable": bool(abs(t["cagr"]) >= t["mde_as_annual_pct"])}
        print(f"  {nm:14s} 自身复利年化 = {t['cagr']:+.4%}/年   MDE = ±{t['mde_as_annual_pct']:.4%}/年"
              f"   |c|/M = {ratio:.4f}  ⇒ "
              f"{'符号可定' if excess_legs[nm]['sign_determinable'] else '🚨 连符号都定不了'}")

    print("\n=== 超额贡献（比值口径：复利年化**不可相减**）===")
    mc = rows[0]["cagr"]
    yrs = rows[0]["years"]
    xc = {}
    for nm, col in (("bt003", "net_excess_bt003"), ("real", "net_excess_real")):
        cb = rows[{"bt003": 1, "real": 2}[nm]]["cagr"]
        xc[nm] = excess_contribution(cb, mc, yrs, excess_legs, nm)
        val, Mx = xc[nm]["annual_pct"], xc[nm]["mde_of_the_underlying_excess_leg"]
        print(f"  {nm:6s} ((1+{cb:.6f})/(1+{mc:.6f}))^(1/{yrs:.4f}) − 1 = {val:+.4%}/年"
              f"   |x|/M = {abs(val) / Mx:.4f}  ⇒ "
              f"{'符号可定' if xc[nm]['sign_determinable'] else '🚨 连符号都定不了'}")
    print("   ⚠️ 分母 = 抽样面板等权（随机 150 只，单 seed），不是中证全指。")

    # ── 达标所需的超额贡献：**按基准分臂报**，不许只报一支 ──────────────
    print("\n=== 达标所需的超额贡献（年化，比值口径）—— 🚨 按基准分臂 ===")
    print("   ⚠️ 达标判决**不需要基准**（账户净值 = 篮子净，基准无关）。")
    print("   下面只回答「若市场腿是 X，账户还差多少超额」⇒ 它**整体**随基准变。")
    cal = json.loads(CALIBER.read_text(encoding="utf-8"))
    if int(cal["n_days"]) != len(df):
        raise SystemExit(f"🚨 基准口径窗口 {cal['n_days']} 天 ≠ 判决窗 {len(df)} 天 "
                         f"⇒ 两者不是同一个量，不许相除（口径不可比就不许比）")
    by_arm: dict[str, dict] = {}
    for arm in (VERDICT_BENCH_ARM,) + SENSITIVITY_BENCH_ARMS:
        c_mkt = caliber_benchmark_fraction(cal, arm)
        req = required_excess(TARGET, c_mkt, yrs)
        tag = "判决臂" if arm == VERDICT_BENCH_ARM else "⚠️ 敏感性臂（非判决）"
        row: dict = {"arm": arm, "role": tag,
                     "c_mkt_fraction": c_mkt,
                     "c_mkt_source": f"{CALIBER.name}:{arm}.geometric_annual_pct / {CALIBER_PCT_SCALE}",
                     "required_excess_annual": req,
                     "required_is_annualized": True,
                     "neither_side_is_a_point_estimate": True,
                     "per_combo": {}}
        if arm == VERDICT_BENCH_ARM and abs(c_mkt - mc) > 1e-12:
            raise SystemExit(f"🚨 判决臂 c_mkt 与逐日序列算出的 {mc!r} 不一致 "
                             f"（caliber {c_mkt!r}）⇒ 两个来源已分叉，不许继续报")
        for nm in ("bt003", "real"):
            cb = rows[{"bt003": 1, "real": 2}[nm]]["cagr"]
            xa = excess_contribution(cb, c_mkt, yrs, excess_legs, nm)
            row["per_combo"][nm] = {
                "measured_excess_annual": xa["annual_pct"],
                "measured_over_required": xa["annual_pct"] / req,
                "measured_abs_over_mde": xa["abs_over_mde"],
                "measured_sign_determinable": xa["sign_determinable"],
            }
            print(f"  [{arm:12s}|{tag}] c_mkt={c_mkt:+.4%}/年   所需={req:+.4%}/年   "
                  f"{nm:6s} 实测={xa['annual_pct']:+.4%}/年  实测/需要={xa['annual_pct']/req:+8.2%}")
        by_arm[arm] = row
    _v = by_arm[VERDICT_BENCH_ARM]["per_combo"]["bt003"]["measured_over_required"]
    _s = by_arm[SENSITIVITY_BENCH_ARMS[0]]["per_combo"]["bt003"]["measured_over_required"]
    print(f"   🚨 两臂「实测/需要」之比 = {_s / _v:.2f}×（判决臂 {_v:+.2%} vs 敏感性臂 {_s:+.2%}）"
          f" ⇒ 「离目标多远」**没有基准无关的答案**；基准无关的只有 "
          f"「账户 {rows[1]['cagr']:+.4%} vs 目标 {TARGET:.0%}，缺 {(TARGET - rows[1]['cagr']):+.2%}」。")

    out = {
        "artifact": str(ART.relative_to(ROOT)),
        "artifact_sha256": hashlib.sha256(ART.read_bytes()).hexdigest(),
        "R_days_per_year": R,
        "R_source": "aplus.hurdle.DAYS_PER_YEAR",
        "target": TARGET,
        "tiers": rows,
        "all_tiers_verdicts": all_verdicts,
        "excess_legs_own_mde": {k: {kk: vv for kk, vv in t.items() if kk != "tier"}
                                for k, t in excess_legs.items()},
        "excess_contribution_ratio": xc,
        "excess_contribution_note":
            "复利年化**不可相减** ⇒ 超额贡献必须走比值 ((1+c_bk)/(1+c_mkt))^(1/years) − 1。"
            "⚠️ 「相减」的 +0.61pp 是错的（分数减分数得到的是差不是比值的年化）。"
            "⚠️ 它的 MDE 取**同源净超额序列**的 MDE（delta method 未做，属已知近似）。",
        "gap_to_target_by_benchmark": by_arm,
        "gap_to_target_note":
            "⚠️⚠️ **本块的两个数都不是点估计**：分子的实测超额 |x|/M < 1（连符号都定不了），"
            "分母的所需超额继承 c_mkt 的 MDE（±20.01%/年，判决臂）"
            "⇒ 「实测/需要」是**两个不可定符号的量相除**，只作形状陈述，"
            "**不许**当「离目标还有多远」的量化结论引用。"
            "⚠️ 基准臂的选取是**预注册**的（见 VERDICT_BENCH_ARM），"
            "不许因为读数难看就换成敏感性臂（第 91 层）。",
        "verdict_reading": v["cagr"],
        "verdict_mde_annual": v["mde_as_annual_pct"],
        "verdict_upper_one_sided_95": U,
        "verdict_rule": "预注册 §16（对 §15.3 的修正）：U=c+M<T 才可说「不可达」；"
                        "c<T≤U 只能说「测不出」；|c|<M 连符号都定不了",
        "verdict_text": verdict,
        "verdict_cases_fired": cases,
        "caliber": {
            "R": f"{R}（aplus.hurdle.DAYS_PER_YEAR，A 股实况）",
            "R_is_not": "不是 aplus.verify.TRADING_DAYS_PER_YEAR=252（美股惯例）；"
                        "两者不是同一个量，本读数与 verify.compounded_stats 的 cagr 不可并排比较",
            "primary": "复利 CAGR = prod(1+r)**(R/n) − 1",
            "arith_role": "算术 mean×R **只作敏感性**，不许用它下判决",
            "verdict_tier_is": "market_return + net_excess_bt003",
            "verdict_tier_is_legit_because":
                "bt003 由 annual_friction(50_000, 10, rate=0.0003, min_comm=5) 一次算出，"
                "**不含 ¥5 固定佣金** ⇒ 比真实成本乐观 ⇒ 它是**上界**，"
                "「①<20% ⇒ 账户净值必然<20%」因此是单边结论",
            "verdict_tier_is_not":
                "**不是**因为它等于真值（0.003 ≠ 真值 0.005520）；照「理由」把它改成 real "
                "会亲手拆掉上界性（real 在 equity_weight=0.5 时真实 0.752% > 0.550%，"
                "翻成下界 ⇒ 失效在「做不到」误判成「做得到」那一侧）",
            "real_tier_role": "答「大概能拿到多少」，**不能**答「能不能到 20%」",
            "tier_sum_order": "三档都先**逐日相加**再复利，不是分别复利再合并",
            "mde": "MDE = 1.96 × sd(log1p r)/sqrt(n)，年化栏按 ×R 近似",
            "why_compounded_not_arith": (
                "石川《因子投资方法与实践》[[p136]]：「月均收益率是算术平均，累计收益曲线图中"
                "反映的是收益率的几何平均，而收益率的波动使得几何平均总是小于算术平均」——"
                "并给出反例：月均收益率为正而累计收益小于零 ⇒ 算术栏**能把符号翻过去**，"
                "不是保守的敏感性。Chan《Quantitative Trading》p.136-137："
                "g = m − s²/2，且「几何平均总是小于算术平均，除非两数完全相同」。"),
            "chan_external_anchor": (
                "每档并排印出 Chan 闭式 g = m − s²/2 与其实测差（pp）。"
                "⚠️ 这是**与文献公式**对拍，**不是**用公式重算自己的字段（第 109 层）。"),
            "this_is_not": "不是 ② 账户净值（不含整手 100 股、¥5 固定佣金、闲置现金）",
            "sample_size_anchor_chan_p85": {
                "source": "Chan《Quantitative Trading》p.85 引 Bailey et al. (2012) 最小回测长度",
                "table": [
                    ["目标：置信「真实 Sharpe ≥ 0」", "需要回测 Sharpe 1", "需要样本 681（2.71 年日频）"],
                    ["目标：置信「真实 Sharpe ≥ 0」", "需要回测 Sharpe 2", "需要样本 174（0.69 年日频）"],
                    ["目标：置信「真实 Sharpe ≥ 1」", "需要回测 Sharpe 1.5", "需要样本 2739（10.87 年日频）"],
                ],
                "n_days_actual": 1395,
                "can_support": "1395 ≥ 681 ⇒ 可以做「回测 Sharpe ≥ 1 ⇒ 真实 Sharpe ≥ 0」这一档",
                "cannot_support": "1395 < 2739 ⇒ **不能**做「真实 Sharpe ≥ 1」的置信声明",
                "why_it_matters": "这条外部表就是 §8.2「判决必须单边」的**外部依据**：本样本量"
                                  "支撑「低于目标」的符号级判决，支撑不住「达到目标」的强度级声明。"
                                  "判决方向由样本量决定，不由我选。",
            },
            "deflated_sharpe_caveat": (
                "Chan p.84-85 的 Bailey (2014) Deflated Sharpe Ratio：**调参次数越多，真实 Sharpe "
                "相对回测 Sharpe 被 deflation 越多**。本仓已迭代 5 档 K、多条权重路径、"
                "多轮成本标定与守卫 ⇒ 🚨 本读数的回测 Sharpe 应读作**上界**，"
                "未经 deflation 校正，**不许**当作「稳健可实现」的证据。"),
            "prereg": "docs/PREREG_7Y_NET_NAV_2026-10-16.md §15（事前预注册）"
                      " + §16（判据修正案） + §17（产物实读）",
            "bt003_optimism_has_TWO_sources": (
                "🚨 ①不含 ¥5 固定佣金；🚨 ②**实测每次调仓成本只有标称档的 0.9435×**"
                "（54 天满档 / 9 天 0.9× / 6 天 0.5×，部分篮子日摊薄，"
                "均值 0.002830 vs 标称 0.003）。⇒ 「0.003」是**标称值**，"
                "实际每次调仓 0.00283。预注册 §17.4。"),
            "basket_size_not_in_artifact": (
                "🚨 `.daily.parquet` **没有持仓数列** ⇒ **非调仓日的成分数测不到**。"
                "只有 15 个调仓日能从 `毛−净` 反推出是 5 或 9 只。"
                "⇒ 「1395 天每天都是 10 只篮子」这句话**产物支撑不了**，不许写。"),
            "c0_tier_label_caveat": (
                "⚠️ `c=0` 档名里的「毛」字指**市场腿本身未扣成本**（成本是涨篮子调仓才付的，"
                "市场腿不调仓）⇒ 它是**多余限定词**，且极易被读成「涨篮子的毛」。"
                "⚠️ tier 名**故意不改**：改了会波及所有下游复制点（第 73 层的扩散成本）。"
                "这一档真正的含义 = **账户 100% 只持抽样面板等权、完全不选股**的上界对照。"),
            "market_return_is_not_an_index": (
                "`market_return` 是**抽样面板等权日收益**（`aplus.backtest::_market_by_day`，"
                "`MARKET_SAMPLE=150`、种子 42 写死在源码）。"
                "🚨 **它不是被选中的那 10 只自身的等权收益** —— 更正 PREREG §12.4 那句"
                "「是那同一批被选中股票自身的等权收益」，那半句是错的（§18.2）。"
                "**不是**中证全指、也不是市值加权（那是 cap_weighted_benchmark.json 的口径）。"
                "✅ 仍然成立的是：`market + net_excess == 涨篮子等权的净收益` —— "
                "但那是**定义式恒等**（`net_excess := 涨篮子净 − market`），"
                "与 market 是什么无关 ⇒ **恒真的恒等式定不了 market 的语义**（§18.2）。"),
            "net_excess_is_a_DIFFERENCE_not_a_nav_series": (
                "🚨🚨 `net_excess_*` / `gross_excess` 三列**是差序列**（组合 − market），"
                "**不是**账户净值日收益序列。**直接对它算 CAGR 会得到一个无关的数** —— "
                "本脚本第一版就踩过（把 bt003 的 cagr 打成 +0.54%，真值是 +10.56%，"
                "差 10pp）。账户净值**必须**先逐日 `market_return + net_excess_*` 相加。"
                "⚠️ 证据：`market_return` 每日唯一（1395 个不同取值 / 1395 行）⇒ 广播列；"
                "恒等式自检印在 stdout（`毛 − 账户净值` 应等于实测成本档）。"),
            "market_single_seed_bias_direction": (
                "🚨 基准是**单个固定 seed 的 150 只随机抽样**，不是全池。"
                f"实测该抽样日均 {mkt.mean() * 100:.6f}% vs 全池 "
                f"{json.loads((ROOT / 'data/reports/market_nav_caliber.json').read_text())['full_pool_daily_mean_pct']:.6f}%"
                " ⇒ 该 seed 的基准**偏高（乐观）** ⇒ 用它当分母 ⇒ "
                "**超额贡献被压低** ⇒ 本读数落在**下界侧**。"
                "🚨🚨 失效方向**不利于本平台的目标**：它让「选股没用 / 达不到」的结论"
                "**更容易成立**，而不是更难。所以这一栏**不能**被当成保守的安全垫。"),
            "market_single_seed_bias_direction_is_NOT_a_whole_claim": (
                "⚠️ 这只是**这一个 seed** 的偏差方向，不是它的幅度保证。"
                "换 seed 基准会动 ⇒ **不许**声称「抽样基准等价于全池」，"
                "也**不许**用它去「修正」上表任何数字（第 109 层：只读字段，不许公式重算）。"
                "⚠️ 要真正消除这个自由度，需要的是**多 seed 稳健性扫描**，"
                "本仓**尚未做**，登记为欠账。"),
        },
    }
    op = ROOT / "data/reports/basket_nav_01_2026-10-16.json"
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # 🚨 第 166 层：章必须盖在**本脚本真正读过的那两份字节**上，
    #   而不是 stamp() 默认的 master/daily（5460 个文件，本脚本一个都没读）。
    #   ⚠️ 落盘路径**写死在源码里**（文件名带 2026-10-16）⇒ 它同时是
    #   `DEFAULT_GENERATORS` 里的一个键；换名要两处一起改，否则
    #   `freshness()` 判「不在登记表里」= 不可引用。
    provenance.stamp(
        op, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_daily_parquet": str(ART.relative_to(ROOT)),
            "source_daily_parquet_sha256": hashlib.sha256(ART.read_bytes()).hexdigest(),
            "source_caliber": str(CALIBER.relative_to(ROOT)),
            "source_caliber_sha256": hashlib.sha256(CALIBER.read_bytes()).hexdigest(),
        })
    print(f"\n→ {op.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
