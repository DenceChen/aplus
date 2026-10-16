"""基准抽样种子的多 seed 扫描（预注册 `docs/PREREG_BENCH_SEED_SWEEP_2026-10-16.md`）。

## 它量的是什么

不是「种子会不会翻转判决」，而是**两族数各自的种子敏感性**：

- **A 族（达标判决本身）**：`verdict_reading` / 三档 `cagr` / `verdict_cases_fired`
  ⇒ 按生成器 caliber 原文，判决档 = `market_return + net_excess_bt003`，
  而 `net_excess := 篮子净 − market` 是**定义式** ⇒ 判决档的**读数与它自己的 MDE
  都不吃种子**。⚠️ 本脚本只做**逐位差取证**，不做语义判定
  （恒真的恒等式定不了语义，第 176 层）。
- **B 族（「离目标多远」）**：`c_mkt` / `required_excess` / 实测超所需
  ⇒ 按构造吃种子 ⇒ **扫描的价值全在这一侧**。

## 三条硬纪律（写在这里，不只写在预注册里）

1. 🚨 **只换输入，不重写公式**。年化调**生产的** `market_nav_caliber._annualized_compound`；
   换算调**生产的** `measure_basket_nav_01.caliber_benchmark_fraction` /
   `required_excess`。**不许**在本文件里出现第二份 CAGR 公式
   ——那是第二台测量仪，两台之间的差永远查不出来（第 22 层）。
   ⚠️ **一处诚实的偏离**：逐日连乘 `∏(1+r)` 由本脚本做（`market_nav_caliber.py::_summ`
   是 **`main()` 里的嵌套函数**，测试与外部都够不着它，第 96 层）
   ⇒ 登记为债；年化那一步用的是生产的那一个。
2. 🚨 **`daily_map` 全程只建一次**。`benchmark_cohort` 的 docstring 逐字警告：
   `rng.sample` 作用在 `list(all_codes)` 上，**键顺序由调用方怎么建 `daily_map` 决定**
   ⇒ 每个种子各建一次 `daily_map` ⇒ 换掉的不是种子，是「顺序 × 种子」两个量。
   ⇒ 键顺序指纹必须落盘（否则两个扫描产物长得一样）。
3. 🚨 **判决只在判决臂 `equal_weight` 上**，敏感性臂 `cap_weight` **只作对照并显式标 role**
   （第 183 层：两臂差 7.94×，而换臂时判决可能一字不变）。

## 一条被诚实写进产物的边界

🚨 **`M_mkt` 本身是种子 42 的量**（`tiers[0].mde_as_annual_pct` 由种子 42 的逐日
market 序列算出）⇒ 拿它判别的种子是**用 A 配置标定的仪器去量 B**（第 17 形状 / 第 94 层）
⇒ 因此 `sign_determinable` 只出**形状陈述**，且分母在产物里显式标 `role`。
重算 `M_mkt(k)` 需要逐日序列重跑 `backtest_walk_forward_weights`（GB 级 × N）⇒ 债。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus import provenance  # noqa: E402
from aplus.backtest import BENCH_SEED, ENTRY_LAG, _market_by_day  # noqa: E402
# 🚨 两条 import 分家、逐字照抄 `market_nav_caliber.py:64-65`：
#   `market_date_axis` 在 **panel**，`load_factors_map` 在 **snapshot**。
#   我第一版把两个名字合写成 `from aplus.panel import load_factors_map, market_date_axis`
#   ⇒ 全仓 25 处调用点里 24 处都从 snapshot 导它，而那条合写**恰好也**命中 24 处
#   —— 能让 25/25 的探针全绿、只让运行炸（第 168 层：凭记忆写名字）。
from aplus.panel import market_date_axis  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
# ★ 年化函数用**生产的**那一个（模块级，可 import）。见 docstring「硬纪律 1」。
from scripts.market_nav_caliber import (  # noqa: E402
    WINDOW_END,
    WINDOW_START,
    _annualized_compound,
)
from scripts.measure_basket_nav_01 import (  # noqa: E402
    VERDICT_BENCH_ARM,
    caliber_benchmark_fraction,
    required_excess,
)

ROOT = Path(__file__).resolve().parents[1]
# 🚨 `generator` 是**相对仓库根的路径串**，不是 `__file__`（`measure_portfolio_nav.py:57`
#   的范式）。传 `__file__` 会让 `ROOT / __file__` 变成不可解析的路径
#   ⇒ `code_file_sha256` 返 `None` ⇒ 而 `stamp()` 收到 `None` 会**整项跳过**
#   字节检查（第 125 层），且**无人察觉**。
GENERATOR = "scripts/bench_seed_sweep.py"

# 🚨 窗口端点**共用生产脚本的常量**，不许在本文件再写一份字面量
#   （第 124 层的形态：「一份票判据写在两个地方，中间没有锁」）。
CALIBER = ROOT / "data" / "reports" / "market_nav_caliber.json"
BASKET = ROOT / "data" / "reports" / "basket_nav_01_2026-10-16.json"

# N 是**资源约束下的测量规模，不是判据** ⇒ 不受「门槛须由外部锚推导」约束，
# 但事前写死、不许事后调整（预注册 §4 / §8.2）。
N_SEEDS = 21


# ── 口径：逐日序列 → 复利终值 → 几何年化（**调生产函数**）──────────────

def geometric_annual(series: dict, dates: list) -> tuple[float, float, int]:
    """返回 `(compounded_final_multiple, geometric_annual_fraction, n_days)`。

    🚨 **一个交易日都不许缺**（判决窗口 G1）：缺了就 `raise`，
    **不许**静默用现存的日子算 —— 那会让窗口长度随抽样变（第 13 种形状：
    「缺档必须报错，绝不回落」）。
    """
    missing = [d for d in dates if d not in series]
    if missing:
        raise KeyError(f"基准序列缺 {len(missing)} 个交易日，首个 = {missing[0]!r}；"
                       f"不许用现存日子凑合算 —— 窗口长度一变就是另一个量")
    mult = 1.0
    for d in dates:
        mult *= (1.0 + series[d])
    n = len(dates)
    g = _annualized_compound(mult, n)
    if g is None:
        raise ValueError(f"年化函数拒绝入参：mult={mult!r} n={n}")
    return float(mult), float(g), n


def order_fingerprint(daily_map: dict) -> str:
    """`daily_map` **键顺序**的指纹 —— 换顺序后两个扫描产物必须长得不一样。

    这是 `benchmark_cohort` docstring 那条警告的唯一落地点：
    `seed` 不等于「可复现」，而复现的另一半条件就是**键顺序**。
    """
    h = hashlib.sha256()
    for k in daily_map:                       # 按**插入序**，不是排序序
        h.update(str(k).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ── D1 / D2 / D3 / D4：判据必须是模块级函数且**可被直接调用** ─────────

def d1_required_excess_is_positive_for_every_seed(c_mkts: dict, target: float,
                                                  yrs: float) -> dict:
    """D1（唯一判决型）：`max_k c_mkt(k) < T` ⇒ `required_excess(k) 恒 > 0`。

    门槛 `T` 由调用方从**产物自报的 `target`** 传入，不许本模块写字面量。
    `c_mkt ≤ −1` ⇒ `(1+c_mkt)^(1/yrs)` 无定义 ⇒ **拒绝**，不许静默给复数。

    🚨 `required_excess_by_k` 由 **生产函数 `required_excess()` 真调算出**
    （预注册 §7 约束 1：只换输入、不重写公式）⇒ 布尔判决由**那些返回值**生成，
    **不是 `holds` 的复述** ⇒ 字段名与语义对齐（第 177 层）。
    ⚠️ 旧实现把 `required_excess_all_positive` 直接写成 `bool(holds)`：名字承诺
    「`required_excess()` 全部为正」，实际从未调用过它 ⇒ 预注册 §7 约束 1
    只兑现了 `caliber_benchmark_fraction` 那一半。
    """
    rejected = sorted(k for k, c in c_mkts.items() if 1.0 + c <= 0.0)
    if rejected:
        raise ValueError(f"这些种子的 c_mkt ≤ −1，年化比值无定义：{rejected}")
    max_k = max(c_mkts, key=lambda k: c_mkts[k])
    holds = c_mkts[max_k] < target
    req = {k: required_excess(target, c_mkts[k], yrs) for k in sorted(c_mkts)}
    all_pos = all(v > 0.0 for v in req.values())
    return {
        "rule": "max_k c_mkt(k) < target  ⇒  required_excess(k) > 0 对全部 k 成立",
        "target": target,
        "target_source": "basket_nav_01_2026-10-16.json::target（产物自报，非手打）",
        "argmax_seed_offset_k": max_k,
        "c_mkt_at_argmax": c_mkts[max_k],
        "holds": bool(holds),
        "required_excess_by_k": req,
        "required_excess_all_positive": bool(all_pos),
        "required_excess_all_positive_equals_holds": bool(all_pos) == bool(holds),
        "caveat": ("按**代数必然**，本条布尔恒等于 `holds`（(1+c)<(1+T) 且指数 "
                   "1/yrs>0 ⇒ 比值>1 ⇒ 减 1 > 0）⇒ 它的信息量全在 holds 那一支。"
                   "🚨 因此 `required_excess_all_positive_equals_holds` **不是恒真装饰**："
                   "它把这条代数恒等式变成**可失败的检查** —— 生产函数哪天被改坏、"
                   "或某格 `c_mkt ≤ −1` 绕过拒绝，它才变 False（第 22 层：两台测量仪"
                   "之间的差必须落盘，否则永远查不出来）。⚠️ `required_excess_by_k` 的"
                   "每个值都**继承 `c_mkt` 的不可测性**（`|c_mkt| < M_mkt`）⇒ "
                   "**形状陈述，不是点估计**（`required_excess` docstring 逐字）"),
    }


def d2_sign_determinable_against_the_seed42_mde(c_mkts: dict, m_mkt: float,
                                                m_mkt_source: str) -> dict:
    """D2：`|c_mkt(k)| / M_mkt(42)` 的分布。

    🚨🚨 **分母是种子 42 的 MDE，不是 `M_mkt(k)`** —— 而 `M_mkt` 自己吃种子
    （它由种子 42 的逐日序列算出）⇒ 这是**用 A 配置标定的仪器去量 B**（第 17 形状）。
    ⇒ 因此本条**只准出形状陈述**：分母的角色必须在产物里显式标出来。
    """
    ratios = {k: abs(c) / m_mkt for k, c in c_mkts.items()}
    ge1 = sorted(k for k, r in ratios.items() if r >= 1.0)
    return {
        "rule": "|c_mkt(k)| / M_mkt >= 1 ⇒ 该格的符号可定（1 来自 MDE 的定义，非手打）",
        "m_mkt": m_mkt,
        "m_mkt_source": m_mkt_source,
        "m_mkt_role": ("🚨 **分母是种子 42 的 MDE，不随 k 更新** —— "
                       "本条只能出形状陈述；「任何种子下都不可定符号」这句话"
                       "需要先重算 M_mkt(k) 才成立（债）"),
        "ratios": ratios,
        "seeds_with_ratio_ge_1": ge1,
        "max_ratio": max(ratios.values()),
        "max_ratio_at_k": max(ratios, key=lambda k: ratios[k]),
        "verdict_direction": (
            "全部远低于 1 ⇒ 就**种子 42 的** MDE 而言无一种子改变『连符号都定不了』"
            if not ge1 else
            "🚨 有种子越过 1 ⇒ 「连符号都定不了」这一格**必须撤回**，"
            "并需重算 M_mkt(k)（债）"),
    }


def d3_distribution_only(c_mkts: dict) -> dict:
    """D3：**纯描述，零判据**。

    🚨 `spread = max − min` 是**观测极差**，**不是总体极差**
    ⇒ 不许写成「基准年化的可能范围是 [min, max]」（第 178 层）。
    """
    xs = sorted(c_mkts.values())
    n = len(xs)

    def _q(p: float) -> float:            # 线性插值分位，仅用于描述
        if n == 1:
            return xs[0]
        i = p * (n - 1)
        lo, hi = math.floor(i), math.ceil(i)
        return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)

    return {
        "kind": "DESCRIPTIVE_ONLY",
        "n_seeds": n,
        "min": xs[0], "p5": _q(0.05), "p25": _q(0.25),
        "median": _q(0.5), "p75": _q(0.75), "p95": _q(0.95), "max": xs[-1],
        "mean": float(sum(xs) / n),
        "observed_spread": xs[-1] - xs[0],
        "spread_is": "**观测极差**（N 个观测的 max−min）",
        "spread_is_not": ("🚨 **不是总体极差/可能范围** —— "
                          "本分布是 `seeds = BENCH_SEED + k, k∈[0,20]` 这 21 个点上取的，"
                          "区间之外的种子未测（第 178 层）"),
    }


def d4_bitwise_reconciliation(c_mkt_by_k: dict, artifact_as_fraction: float) -> dict:
    """D4：逐位取证（**不是判据**）。

    🚨 `artifact_as_fraction` 由调用方用**生产的** `caliber_benchmark_fraction`
    换算好传进来 —— 本函数**自己不做任何量纲换算**（第 138/182 层：
    两个产物的 `*_pct` 后缀**量纲相反**，caliber 是百分数而 `mde_as_annual_pct`
    是小数；而量纲守卫钉住了生产者，对消费者零覆盖）。
    ⚠️ 我第一版在这里写 `artifact_geom_pct / 100.0` —— 数字**恰好**等于
    `caliber_benchmark_fraction` 的返回值（同一个 100.0），所以它**不会报错**，
    而它让换算在这份文件里**发生了第二次** ⇒ 有人改了 `CALIBER_PCT_SCALE`
    就会静默地只换一半。这是第 17 种形状里「两份副本之间没有锁」。

    ① `c_mkt(0)` 必须**逐位**等于产物 caliber 读数；
    ② 跨 `k` 的逐位差必须**非零**（若全零 ⇒ 种子被忽略 ⇒ 扫描没生效）。
    """
    base = c_mkt_by_k.get(0)
    if base is None:
        raise KeyError("扫描里没有 k=0，而 k=0 是基线复核格（预注册 §4）")
    deltas = {k: c - base for k, c in c_mkt_by_k.items()}
    distinct = sorted({repr(v) for v in deltas.values()})
    return {
        "caliber_conversion": ("caliber 的 geometric_annual_pct 是**百分数**，"
                               "已由生产的 caliber_benchmark_fraction 换算为分数；"
                               "本函数不做第二次换算"),
        "k0_matches_artifact_bitwise": bool(base == artifact_as_fraction),
        "k0_scan": base,
        "k0_artifact_as_fraction": artifact_as_fraction,
        "k0_bitwise_diff": base - artifact_as_fraction,
        "distinct_deltas_across_k": len(distinct),
        "all_identical_across_k": len(distinct) == 1,
        "reading": ("🚨 逐位全同 ⇒ 种子被忽略，扫描没生效 ⇒ 停止，别把结果当结论"
                    if len(distinct) == 1 else
                    "跨种子的 c_mkt 逐位不同 ⇒ 种子真的换了票群"),
    }


def _run(args) -> dict:
    # 🚨 第 140/125 层：`expect_bytes` 必须落在**第一个昂贵步骤之前**，
    #   且实参是 `(ROOT, GENERATOR)` 而不是 `(ROOT, __file__)`。
    #   ⚠️ 我第一版写 `code_file_sha256(__file__)` —— 第一个参数是 `root`，
    #   于是 `Path(abs_path) / abs_path` 在 POSIX 上被吞掉、算出**仓库内一个
    #   恰好存在的相对路径**的概率不为零；更常见的是返回 `None`，
    #   而 `stamp()` 收到 `None` 会**整项跳过**字节检查（第 125 层，无人察觉）。
    bytes_at_start = provenance.code_file_sha256(ROOT, GENERATOR)
    if bytes_at_start is None:
        # 🚨 缺档必须**报错绝不回落**（CLAUDE.md）。这里回落 = 静默地不检查字节，
        #   而 `stamp()` 那一侧还会在 sidecar 里写 `code_file_sha256: null`
        #   —— 一枚「看起来盖了章、其实没验」的章（第 125 层的最坏形态）。
        raise SystemExit(
            f"🚨 取不到 {GENERATOR} 的字节哈希（code_file_sha256 返 None）—— "
            f"拒绝盖章：那会让 sidecar 写出一枚没验过字节的章，而无人察觉"
        )

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        raise SystemExit("🚨 主库为空")
    t0 = time.time()
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    order_fp = order_fingerprint(daily_map)
    all_dates = market_date_axis(daily_map)
    dates = [d for d in all_dates if args.start <= str(d) < args.end]
    if len(dates) < 2:
        raise SystemExit(f"🚨 窗口内交易日不足：{len(dates)}")
    print(f"池 {len(daily_map)} 只 / 交易日 {len(dates)} / {time.time() - t0:.0f}s", flush=True)

    adj_cache: dict = {}          # ★ 全程同一个 ⇒ 缓存不跨种子串味
    rows = []
    for k in range(N_SEEDS):
        seed = BENCH_SEED + k
        tk = time.time()
        series = _market_by_day(daily_map, dates, entry_lag=ENTRY_LAG,
                                factors_map=factors_map, adj_cache=adj_cache,
                                seed=seed)
        mult, g, n = geometric_annual(series, dates)
        rows.append({"k": k, "seed": seed, "n_days": n,
                     "compounded_final_multiple": mult,
                     "geometric_annual_fraction": g,
                     "geometric_annual_pct": g * 100.0})
        print(f"  k={k:2d} seed={seed}  c_mkt={g * 100.0:+.6f}%  ({time.time() - tk:.0f}s)",
              flush=True)

    c_by_k = {r["k"]: r["geometric_annual_fraction"] for r in rows}

    # ── 上游产物（只**读字段**，不许用公式重算 —— 第 109 层）────────────
    cal = json.loads(CALIBER.read_text())
    bk = json.loads(BASKET.read_text())
    verdict_arm = VERDICT_BENCH_ARM                     # 「equal_weight」
    c_mkt_verdict = caliber_benchmark_fraction(cal, verdict_arm)   # arm 显式传，无默认值
    target = float(bk["target"])
    yrs = float(bk["tiers"][0]["years"])
    m_mkt = float(bk["tiers"][0]["mde_as_annual_pct"])
    # 🚨🚨 **两处必须写进产物的诚实标注**（否则本产物看起来比它的输入更可信）：
    #
    # ① `basket_nav_01_2026-10-16.json` **没有 sidecar**（`measure_basket_nav_01.py`
    #    全文无 `stamp()`）⇒ 按本仓规矩 `unknown` = **不可引用**，而 D1 的 `T` 与
    #    D2 的 `M_mkt` **正从它读**。本产物不因此作废（它盖了自己的章），
    #    但**它的两个输入是化石** —— 方向是**更不可信**，属保守方向。
    # ② `aplus/backtest.py::_market_by_day` 的 docstring 逐字自称「**随机分层抽样**」，
    #    而实现是 `rng.sample` **普通随机** ⇒ 本扫描**继承这条债**：
    #    换种子换的是**普通随机样本**，不是分层样本。
    basket_sidecar = BASKET.with_suffix(".provenance.json")
    caliber_sidecar = CALIBER.with_suffix(".provenance.json")

    return {
        "kind": "bench_seed_sweep",
        "prereg": "docs/PREREG_BENCH_SEED_SWEEP_2026-10-16.md",
        # 🚨 跨函数带出来给 `main()` 盖章用。第一版 `stamp(… expect_bytes=bytes_at_start)`
        #   写在 `main()` 里而 `bytes_at_start` 是 `_run()` 的局部名 ⇒ **NameError**,
        #   且崩在**产物写完之后**（第 131 层：三个「跑到底才炸」的错长同一个样）。
        "_expect_bytes": bytes_at_start,
        "window": {"start": args.start, "end": args.end,
                   "n_days": len(dates),
                   "endpoints_are": "开区间 [start, end)；端点共用 market_nav_caliber 的常量"},
        "n_seeds": N_SEEDS,
        "seeds": f"{BENCH_SEED} + k, k=0..{N_SEEDS - 1}",
        "sampler": {"function": "aplus.backtest._market_by_day(..., seed=…)",
                    "cohort_impl": "aplus.backtest.benchmark_cohort（唯一样本选择实现）",
                    "sample_size": int(cal["sample"]["size"]),
                    "seed_formula": "seeds = BENCH_SEED + k（🚨 不许 hash(day) 派生）",
                    "daily_map_built_once": True,
                    "daily_map_order_sha256": order_fp,
                    "daily_map_pool": len(daily_map)},
        "by_seed": rows,
        "verdict_arm": {
            "arm": verdict_arm,
            "role": "判决臂（唯一下判决的臂；第 183 层不许因为读数难看换臂）",
            "c_mkt_from_artifact": c_mkt_verdict,
            "c_mkt_source": f"market_nav_caliber.json::{verdict_arm}.geometric_annual_pct "
                            f"经 caliber_benchmark_fraction 换算",
        },
        "input_caveats": {
            "basket_nav_01_has_no_sidecar": {
                "sidecar_exists": basket_sidecar.exists(),
                "meaning": "🚨 无 sidecar ⇒ 按本仓规矩**不可引用**"
                           "（measure_basket_nav_01.py 全文无 stamp()）。"
                           "D1 的 T 与 D2 的 M_mkt 正从它读 ⇒ "
                           "本产物的这两个输入是化石。"
                           "方向是更不可信（保守方向），但必须写在脸上。",
                "affects": ["D1.target", "D2.m_mkt", "yrs"],
            },
            "caliber_has_sidecar": caliber_sidecar.exists(),
            "sampler_docstring_is_wrong": (
                "🚨 `aplus/backtest.py::_market_by_day` 的 docstring 逐字自称"
                "「**随机分层抽样**」，而实现是 `rng.sample` **普通随机**"
                "（benchmark_cohort 是唯一样本选择实现）。"
                "⇒ 本扫描继承这条债：换种子换的是普通随机样本，**不是分层样本**。"
                "本轮只登记不修。"),
        },
        "verdicts": {
            "D1": d1_required_excess_is_positive_for_every_seed(c_by_k, target, yrs),
            "D2": d2_sign_determinable_against_the_seed42_mde(
                c_by_k, m_mkt, "basket_nav_01_2026-10-16.json::tiers[0].mde_as_annual_pct"),
            "D3": d3_distribution_only(c_by_k),
            "D4": d4_bitwise_reconciliation(c_by_k, c_mkt_verdict),
        },
        "family_a_untouched": {
            "claim": "判决档（verdict_reading / 三档 cagr / verdict_cases_fired）"
                     "按生成器 caliber 原文不吃基准种子",
            "basis": "caliber.verdict_tier_is = \"market_return + net_excess_bt003\" "
                     "且 net_excess := 篮子净 − market（定义式）",
            "status": "🚨 **结构推理，不是实测** —— 本轮未重跑整条回测，"
                      "故未逐位实测（预注册 §3.2 / §6；第 167 层）",
            "float_caveat": "逐日序列 = market + (basket − market)，浮点上 a+(b−a) ≠ b 精确 "
                            "⇒ 「逐位不变」不保证；真要证必须重跑并直接比产物（本轮不做）",
        },
        "not_measured_this_round": [
            "per_combo.*.measured_excess_annual（需逐日 market ⇒ GB 级重跑 × N）",
            "excess_legs_own_mde.*（同一个差序列的几何年化）",
            "M_mkt(k) 本身（D2 的分母没跟着 k 更新 ⇒ D2 只能出形状陈述）",
            "daily_map 键顺序敏感性（本次固定了顺序，未测换顺序的后果）",
        ],
        "what_this_cannot_prove": [
            "「基准年化在任何种子下都不可定符号」—— D2 的分母是种子 42 的 MDE",
            f"seeds ∈ [{BENCH_SEED}, {BENCH_SEED + N_SEEDS - 1}] 之外的邻域"
            "（N=21 是资源约束，不是覆盖声明）",
            "A 族判决不受种子影响（本轮只是结构推理，未实测）",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="基准抽样种子扫描（预注册 §4–§5）")
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "bench_seed_sweep.json"))
    args = ap.parse_args(argv)

    out = _run(args)
    # 🚨 `_expect_bytes` 是**盖章用的一次性载荷**，不是产物字段。
    #   `provenance.py::stamp` 的 docstring 逐字写着「写在报告**旁边**而不是报告内部：
    #   报告的 schema 是下游在读的，**往里塞字段会污染它**」⇒ 摘掉再写盘，
    #   不留一个 `_` 前缀的私钥在已发表的 JSON 里。
    expect_bytes = out.pop("_expect_bytes")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    v = out["verdicts"]
    print(f"\n{'=' * 88}\n基准种子扫描（N={out['n_seeds']}）\n{'=' * 88}")
    print(f"  D1 全种正号   {v['D1']['holds']}")
    print(f"  D2 最大比值   {v['D2']['max_ratio']:.4f} @ k={v['D2']['max_ratio_at_k']}"
          f"（分母是种子 42 的 MDE）")
    print(f"  D3 观测极差   {v['D3']['observed_spread'] * 100:+.4f}pp"
          f"（**观测极差，不是总体范围**）")
    print(f"  D4 k=0 逐位   {v['D4']['k0_bitwise_diff']:+.3e}"
          f" / 跨 k 逐位全同 = {v['D4']['all_identical_across_k']}")

    provenance.stamp(args.out, GENERATOR, root=ROOT, data_root=args.data_root,
                     expect_bytes=expect_bytes,
                     extra={"n_seeds": N_SEEDS, "prereg": out["prereg"]})
    print(f"  写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
