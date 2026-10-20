"""组合绝对净值年化：把「年化 20%」第一次**对着交付物本身**测量。

## 为什么需要这个产物（而不是又一把尺子）

尺子**早就在**：`aplus/verify.py::compounded_stats()` 的 docstring 逐字写着
「对标『年化 20%』的唯一尺子」，且 `aplus/webapp.py` 已经把它接进 `/api/status`。

缺的不是尺子，是**可核对的读数**：那个数只活在一次 HTTP 响应的内存里，
没有落盘、没有 provenance 章，事后无从核对「网页上那个 cagr 是哪天的、
从哪份底稿算出来的」。

⇒ 本脚本**调用**既有尺子并把读数落成产物。
🚨 **不重写 CAGR 公式**（第 163 层：禁的是重写量具）。
新增的只有尺子本来没有的那一项：**这个读数有多粗**（MDE）。

## 本脚本能回答 / 不能回答

| | |
|---|---|
| 能 | 「至今为止，交付出去的清单实际累计了多少」（`total_return`） |
| 能 | 「按这几天的日收益外推，年化是多少」（`cagr`，**带 MDE**） |
| 能 | 「20% 这个数，**当前样本能不能分辨**」（`verdict_about_20pct`） |
| **不能** | 「将来能不能到 20%」——前瞻问题 |
| **不能** | 「跌腿当空头腿净值会怎样」——融券费/可得性**未建模**，不猜 |
| **不能** | 「相对大盘的超额」——那是 `beta_adjusted_excess`，**另一个量**（第 7 层） |

## 口径（逐字抄自 `docs/PREREG_PORTFOLIO_NAV_2026-10-16.md` §1）

**C1（最重要）：组合 = 只持涨腿。** 跌腿是**回避清单**，我们从未持有过它。
A 股散户融不到券，把跌腿复利成一条曲线等于凭空做空。
⚠️ 这一条必须随数字一起出现，否则读的人会把「组合」理解成 20 只都持有——
那是两个量。

其余 C2~C7 全部由 `aplus/verify.py` 实现，本脚本不重复实现，只读取数。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# 🚨 形状 24：`python <脚本>` 的 sys.path[0] 是 `scripts/`，不含仓库根。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402
# 🚨 **信号作用域的唯一入口**（预注册 `docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）。
#   本脚本**不自己实现**过滤逻辑（第 97 层），也**不自己实现**指纹（第二份真相源）。
from aplus.signal_scope import (  # noqa: E402
    VERDICT_NOT_DECIDABLE,
    not_decidable_note,
    resolve_attribution_context,
    attribution_block,
    scope_block,
    scope_ledger,
    upstream_scope_verdict,
)
from aplus.verify import compounded_stats, daily_portfolio_returns  # noqa: E402

GENERATOR = "scripts/measure_portfolio_nav.py"

# 用户的常设目标。写死在这里，且**必须**与 verdict 判据用同一个常量——
# 两处各写一个字面量早晚会漂移（第 163 层）。
TARGET_ANNUAL = 0.20

# `aplus/verify.py::compounded_stats` 在低于此天数时把 cagr 置 None。
# 本脚本**导入**它而不是重抄数字，避免两份契约各自漂移。
from aplus.verify import (  # noqa: E402
    MIN_DAYS_FOR_ANNUALISATION, TRADING_DAYS_PER_YEAR,
)


def cagr_mde(daily: pd.Series) -> dict:
    """CAGR 的**可分辨下限**（预注册 §2 + §8 修正），公式可推导，不含调参。

        log(1+CAGR) = 252 · r̄          （年化把日均放大 252 倍）
        se          = 252 · sd_log / sqrt(n)
        MDE         = 1.96 × 252 × sd_log / sqrt(n)

    ## 🚨 那个 252 是补记进来的（预注册 §8），第一版漏了它

    第一版写成 `1.96 × sd_log / sqrt(n)` —— 那是**日均收益率**的 CI，
    被当成了**年化外推**的 CI。两者差 `252` 倍，而 n 越小放大越狠。

    实测（n=3、sd_log=0.005677）：
        漏 252 → MDE=0.64% ⇒ 判「低于 20%」
        带 252 → MDE=161.9% ⇒ 判「测不出符号」
    ⇒ 漏 252 的错误方向是**乐观**的：它把 3 天噪音说成了「稳输」，
    会让人以为策略已被判决、再不必攒样本。

    ## 为什么用 log1p

    `CAGR+1` 恰好就是连乘的底数 ⇒ 在 log 尺度上做标准误，
    尺度与被估量天然一致，不需要任何 delta 方法。

    ## 这个 MDE **不覆盖**什么

    它只覆盖**抽样误差**（r̄ 的估计精度）。
    它**不覆盖**「3 天的实现值代表一整年」这个**预测误差**——后者不可量化，
    只能用 `MIN_YEARS_FOR_DIRECTION_VERDICT` 这道**结构闸门**挡住。
    """
    r = daily.to_numpy(dtype=float)
    n = int(len(r))
    if n < 2:
        return {"n": n, "sd_log": None, "mde": None,
                "why": f"n={n} < 2：连日收益的样本标准差都估不出来"}
    if bool(np.any(1.0 + r <= 0.0)):
        # 本金亏光。那不是「波动大」，是**这条曲线已经终止**，
        # 对它谈分辨率没有意义（`compounded_stats` 同样返回 cagr=None）。
        return {"n": n, "sd_log": None, "mde": None,
                "why": "存在 1+r ≤ 0 的日子（本金亏光），CAGR 无定义"}
    sd_log = float(np.std(np.log1p(r), ddof=1))
    mde = 1.96 * TRADING_DAYS_PER_YEAR * sd_log / sqrt(n)
    return {"n": n, "sd_log": round(sd_log, 6), "mde": mde,
            "why": "MDE = 1.96 × 252 × sd_log / sqrt(n)（log 尺度；"
                   "252 是年化放大因子，预注册 §8 补记，漏它会低估 252 倍）"}


# 至少要有一整年观测，才允许对**一年期**目标给方向判断。
# 理由不是玄学：用户的目标本身就是一年的量，
# 用不到一年的样本判决一年期目标 = 把不同期的东西比大小（第 7 层）。
MIN_YEARS_FOR_DIRECTION_VERDICT = 1.0


def verdict_about_target(cagr, mde: float | None, years) -> str:
    """产物里关于「年化 20%」的那句话。

    🚨 判据写死在 `docs/PREREG_PORTFOLIO_NAV_2026-10-16.md`：
       §3 的 V3（|cagr−0.20| ≤ MDE ⇒ 逐字「测不出符号」）
       与 §8 补记的 **V7**（years < 1 ⇒ 一律不给方向，即使超出 MDE）。

    **顺序不能反**：V7 是**结构闸门**，必须先于 V3 判。
    先判 V3 的话，一个 years=0.012 的样本只要恰好远离 0.20
    就会说出「低于 20%」——那是把噪音当判决。
    """
    if cagr is None:
        return f"未年化（n_days < {MIN_DAYS_FOR_ANNUALISATION}，外推无意义）"
    if mde is None:
        return "无法判定（MDE 不可计算，见 mde.why）"
    if years is None or years < MIN_YEARS_FOR_DIRECTION_VERDICT:
        return (f"样本过短，不给方向（years={years} < "
                f"{MIN_YEARS_FOR_DIRECTION_VERDICT}）："
                "不到一年期的观测不能判决一年期目标")
    d = cagr - TARGET_ANNUAL
    if abs(d) <= mde:
        return "测不出符号"
    return ("高于 20%（超出 MDE）" if d > 0 else "低于 20%（超出 MDE）")


def window_sizes(history: pd.DataFrame) -> pd.Series:
    """每个**成交窗口**的成分数（涨腿、已扣成本、有 bar 的行）。

    ## 为什么必须单独算而不是照抄

    `daily_portfolio_returns` 只返回日收益，**不返回成分数**，
    而「组合」这个词在产物里必须有个数撑着：实测成分数是 **29 / 6 / 10**，
    而交付形态是「每天 10 只」——**两者根本不是同一个东西**。

    ⚠️ 本函数**重新实现**了一遍分组规则，所以它与尺子可能漂移。
      ⇒ `measure()` 里有一条断言把两边的**窗口集合**钉在一起：
      漂了就当场报错，而不是静默给出**另一组**看起来合理的数（第 7 层）。
    """
    up = history[history["direction"] == "up"]
    col = ("exit_date" if ("exit_date" in up.columns and up["exit_date"].notna().any())
           else "pred_date")
    return up[col].astype(str).value_counts().sort_index()


def measure(history: pd.DataFrame, diagnosis: dict | None) -> dict:
    """从底稿算读数。单独抽出来是为了让守卫能用构造表直接打。"""
    stats = compounded_stats(history)
    daily = daily_portfolio_returns(history)

    m = cagr_mde(daily)
    cagr = stats.get("cagr")
    mde = m.get("mde")
    d = None if cagr is None else cagr - TARGET_ANNUAL

    # ── V6'：同一份底稿的 sha256 保证；两个计数**只查不变式，不查相等** ──
    #   `n_days`（成交窗口桶数）≤ `trading_days`（出名单天数）是**不变式**；
    #   它们逐字不相等才是对的（10-09/10/11 三天共用一个成交窗口）。
    #   但剔除量 `excluded` 是**真同量**（同一个 df["result"] 的计数）⇒ 必须相等。
    excluded = {"no_data": int((history["result"] == "no_data").sum()),
                "flat": int((history["result"] == "flat").sum())}
    n_days, trading_days = stats.get("n_days", 0), (
        None if diagnosis is None else diagnosis.get("trading_days"))
    if trading_days is not None and not (0 < n_days <= trading_days):
        raise ValueError(
            f"不变式被打破：n_days={n_days}，trading_days={trading_days}。"
            "成交窗口桶数必须落在 (0, 出名单天数] 内——超出说明分组口径漂了")
    if diagnosis is not None and diagnosis.get("excluded") != excluded:
        raise ValueError(
            f"剔除量与 hit_rate_diagnosis.json 不一致：{excluded} vs "
            f"{diagnosis.get('excluded')}——同一个 df['result'] 的计数，"
            "不相等就是口径漂移（这一条是真同量，必须逐字相等）")

    # ── 逐窗口成分数：让「组合」在产物里有数撑着 ──
    sizes = window_sizes(history)
    used = set(map(str, daily.index))
    missing = used - set(map(str, sizes.index))
    if missing:
        raise ValueError(
            f"日收益里的窗口 {sorted(missing)} 在成分数表里不存在 ⇒ "
            "本脚本的分组规则与 daily_portfolio_returns 漂了，"
            "此时报出的成分数是**另一个量**（第 7 层）")
    dropped_windows = {str(k): int(v) for k, v in sizes.items()
                       if str(k) not in used}
    per_window = {str(k): int(sizes.loc[k]) for k in daily.index}

    return {
        "target_annual": TARGET_ANNUAL,
        "组合口径": (
            "C1 **只持涨腿**：跌腿是回避清单，我们从未持有过它，"
            "A 股散户融不到券，把它复利成一条曲线等于凭空做空。"
            "⚠️ C2 **不是「每天 10 只等权」**——实测每个成交窗口的成分数是 "
            f"{list(per_window.values())}，因为**重叠窗口会并集合并**、"
            "**没行情的标的不占仓**（见下方「测量假设」三条）。"
            "C3 成分不足 3 只的窗口整天丢弃不填 0；"
            "C4 观测日按 exit_date（收益实现那天）；C5 净=毛−成本且只扣一次；"
            "C6 年化=(1+total)^(252/n_days)−1。"),
        "测量假设": [
            "① **每天的名单全部执行**。若实际只做当天最新一份（只持 10 只），"
            "净值会不同——本产物不知道你是哪种，所以必须写明。",
            "② **重叠窗口按并集合并**：实测 10-09/10/10/10-11 三个信号日的涨腿"
            "两两交集 0/0/1 只、并集 29 只，而它们共用同一个成交窗口 "
            "⇒ 照单全收会同时持有 29 只。",
            "③ **没行情的标的不留现金**：实测 2026-10-12 信号日 10 只涨腿里 "
            "4 只 no_data，产物只对剩下 6 只求等权均值 ⇒ 等价于假设"
            "**仓位自动转给其余标的、满仓不留现金**。真实执行时那 4 只买不到，"
            "仓位应当是现金。",
        ],
        "逐窗口成分数": per_window,
        "被_MIN_BASKET_整天丢弃的窗口": dropped_windows,
        "读数": {
            "total_return": stats.get("total_return"),
            "total_return_是事实": (
                f"这 {stats.get('n_days')} 个成交窗口上，涨腿**等权**"
                f"（逐窗口成分数 {list(per_window.values())}）"
                f"**实际**累计的收益。不是外推。"),
            "cagr": cagr,
            "cagr_是外推": (
                f"把上面 {stats.get('n_days')} 天外推成一年的结果。"
                f"n 越小，外推越接近编造。"),
            "arith_daily": stats.get("arith_daily"),
            "arith_ann": stats.get("arith_ann"),
            "ann_vol": stats.get("ann_vol"),
            "vol_drag": stats.get("vol_drag"),
            "n_days": n_days,
            "years": stats.get("years"),
        },
        "mde": m,
        "gap_to_target": None if d is None else d,
        "verdict_about_20pct": verdict_about_target(
            cagr, mde, stats.get("years")),
        "excluded": excluded,
        "这个产物不能回答": [
            "将来能不能到 20%——前瞻问题，短样本不能回答。",
            "跌腿若当空头腿净值会怎样——融券费与可得性未建模，不猜。",
            "相对大盘的超额——那是 beta_adjusted_excess，另一个量。",
            "「如果只做当天最新一份会赚多少」——那是另一个组合，"
            "本产物测的是照单全收（假设①）。",
        ],
        "预注册": "docs/PREREG_PORTFOLIO_NAV_2026-10-16.md",
    }


def _sha256_of(path: Path) -> str:
    """底稿的**字节**指纹。读文件本身，不用 mtime——mtime 是代理不是证据。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verification", type=Path,
                    default=ROOT / "data/reports/verification.parquet")
    ap.add_argument("--diagnosis", type=Path,
                    default=ROOT / "data/reports/hit_rate_diagnosis.json")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/portfolio_nav.json")
    ap.add_argument(
        "--reports-dir", type=Path, default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --verification 的父目录。")
    a = ap.parse_args()
    for p, what in ((a.verification, "底稿"), (a.diagnosis, "命中率诊断")):
        if not p.is_file():
            print(f"✗ 缺档：{p}（{what}）—— 没有它就不能报组合净值")
            return 1

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**；传 None 会让 stamp() **整项跳过**字节检查而无人察觉。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    # 🚨🚨 **信号作用域**（预注册 §2 / §3）。
    #   底稿是**追加式**的 ⇒ 组合净值**只由当前信号**的行算出来。
    reports_dir = a.reports_dir if a.reports_dir else a.verification.parent
    ctx = resolve_attribution_context(reports_dir)
    # 🚨 **读口留在调用点**（既有守卫钉住：expect_bytes 必须抓在它之前）。
    frame = pd.read_parquet(a.verification)
    # 🚨 **过滤只走共享入口**（第 97 层）。
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)
    diagnosis = json.loads(a.diagnosis.read_text(encoding="utf-8"))

    # 🚨 **两个输入必须同一个作用域**：`measure()` 会拿底稿的 `excluded`
    #   与诊断产物的 `excluded` **逐字相等**做交叉核对 ⇒ 两者不同源时
    #   那条核对是**拿两个不同的量比**（第 7 层）。
    diag_scope = upstream_scope_verdict(
        diagnosis, ctx.signal_id, ctx.weights_fingerprint)
    not_decidable_reason = None
    if not scoped.has_evidence:
        not_decidable_reason = scoped.reason
    elif not diag_scope["ok"]:
        not_decidable_reason = (
            "🚨 命中率诊断产物的作用域与当前信号不一致 ⇒ "
            "**两个输入不同源** ⇒ 交叉核对（`excluded` 逐字相等）"
            "会拿两个不同的量比 ⇒ **不可判定**。\n   " + diag_scope["reason"])

    if not_decidable_reason is not None:
        res = {
            "verdict_about_20pct": (
                f"{VERDICT_NOT_DECIDABLE}：{not_decidable_reason}"),
            "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
            "what_this_cannot_prove": [
                "🚨 **本产物没有报任何年化读数**（`cagr` / `mde` 都没有）。"
                "作用域不对 ⇒ 判据**根本没有资格**跑。",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「达不到 20%」或"
                "「离 20% 还差多少」——它说的是"
                "「**这份证据不属于当前信号**」。",
                "🚨 **本产物不写 `cagr` 字段** ⇒ 任何按 `cagr` 取数的消费者"
                "会取不到值（**显式缺失**，不是 0.0）。",
            ],
        }
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        provenance.stamp(
            a.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={
                "source_parquet": str(a.verification),
                "source_parquet_sha256": _sha256_of(a.verification),
                "diagnosis": str(a.diagnosis),
                "diagnosis_sha256": _sha256_of(a.diagnosis),
                "verdict": VERDICT_NOT_DECIDABLE,
                "signal_scope_status": scoped.status,
                "signal_id": scoped.current_signal_id,
            })
        print(not_decidable_note(scoped))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    history = scoped.frame
    res = measure(history, diagnosis)
    # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）。
    res["signal_scope"] = scope_block(scoped)
    res["attribution"] = attribution_block(scoped)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    # 🚨 第 166 层：章必须盖在**本脚本真正读过的那份字节**上，
    #   而不是 stamp() 默认的 master/daily（5460 个文件，本脚本一个都没读）。
    provenance.stamp(
        a.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_parquet": str(a.verification),
            "source_parquet_sha256": _sha256_of(a.verification),
            "diagnosis": str(a.diagnosis),
            "diagnosis_sha256": _sha256_of(a.diagnosis),
            "n_days_measured": res["读数"]["n_days"],
            "signal_scope_status": scoped.status,
            "signal_id": scoped.current_signal_id,
        })
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
