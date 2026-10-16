#!/usr/bin/env python
"""前瞻 D2 的第一个实测判决 —— 判据预注册在 docs/PREREG_FORWARD_D2_2026-10-16.md。

⚠️ 本文件实现的是那份预注册的 R1–R5。**任何口径改动都必须先改预注册**，
   不许在这里「顺手调一下」（第 91 层：跑过数据后改判据 = 撤回一次判决）。

口径要点（每一条都是实测出来的，不是假设的）：

R1 **独立观测单位 = `entry_date`，不是 `pred_date`**
    实测 6 个 `pred_date` 只对应 **4** 个 `entry_date`：10-09/10-10/10-11
    三天出的单**全部 entry 到 2026-10-12**，持有的是**同一批 bar**。
    ⇒ 按 `pred_date` 计数会把 1 个观测数成 3 个，而它们共享同一次市场冲击。
    ⚠️ 反面：10-10 是周六、10-11 是周日，系统照样出单 ⇒ 那两天的出单
       在独立证据上**零新增**。这不是 bug，是「每天各 10 只」这条交付契约
       与「按 bar 窗口计独立观测」这两件事的**必然交叠**。

R3 判决只用**逐行算术**超额，**不许**用复合差。
    复合差对 n=4 是非线性的，且与 t 的分母不同源 ⇒ 只作并排展示。

R4 四档判决，**`POWERLESS` 必须最先**（第 141 层：分支顺序本身是判据）。
    🚨 **`NOT_MEASURABLE_AT_THIS_N` 在连续采样下是死分支**（第 141 层
       「不可能满足的分支」）：R2 的 `MDE = 1.96·se` 与本规则的
       `CI95 = 点估计 ± MDE` 联立给出 `CI 跨 0 ⟺ |点估计| < MDE`
       ⇒ 这个条件已被第一档整条吃掉。程序取证：20 万次采样中该分支命中 **0** 次。
       ⚠️ **「死」不等于「代数上不存在能命中它的入参」**：`|c|/MDE > 1` 时
          `lo > 0` 或 `hi < 0` 必有一真，所以第二档唯一的入口是
          **`|c|/MDE == 1` 恰好相等**那一个测度零点（此时 `lo` 或 `hi` 恰好为 0，
          而 `0 > 0` 与 `0 < 0` 都为假）。这正是 `abs(point) < mde` 里的
          **严格小于**承重的地方 —— 放宽成 `<=` 会把它吞进 `POWERLESS`，
          守卫 `test_g1_is_strictly_less_than_so_the_exact_boundary_falls_to_`
          `the_second_bucket` 专治这一条。
       **处置：不删、不改判据**，而是登记成 `DEAD_BRANCHES` 并让守卫钉住
       （把它「改成可达」需要换 CI 的临界值 = 换判据臂，第 183 层明禁）。
R5 **只回答「能不能定出符号」**，不回答「年化够不够 20%」——
    外推年化在本样本量下是第 167 层的无源之水，故产物里**没有**任何外推字段。

🚨 一条**未注册**的敏感性臂：n=4 时正态近似的 1.96 **低估**真实临界值
   （t(3) = 3.182）⇒ 用 1.96 会把 CI 画窄 ⇒ **失效方向是乐观的**
   （更容易宣布显著）。故产物**并排**给出 t 临界值版本并标 role=sensitivity，
   而**判决仍用预注册写的 1.96**（第 183 层：加对照臂，不换判据臂）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from aplus import provenance  # noqa: E402

GENERATOR = "scripts/forward_d2_verdict.py"

# ---- 判决参数：门槛来自外部锚，不是从本次数据推的 ----
Z_95 = 1.96                 # 正态近似双侧 95%（预注册 R2）
MIN_BLOCKS_FOR_ANY_SIGN = 6  # 低于此 n，判决一律 POWERLESS（预注册 R4）
# t 分布双侧 95% 临界值表（仅用于**敏感性臂**，不参与判决）。
# 来源：标准统计表，df = n_blocks - 1。
_T_CRIT_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
              7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
              13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
              18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}

# ---- 上游契约：缺列必须 KeyError，不许 .get(默认值) 回落 ----
REQUIRED_COLUMNS = ("pred_date", "entry_date", "exit_date", "direction",
                    "gross_pct", "market_pct")
UP = "up"

VERDICT_POWERLESS = "POWERLESS"
VERDICT_NOT_MEASURABLE = "NOT_MEASURABLE_AT_THIS_N"
VERDICT_NEGATIVE = "NEGATIVE"
VERDICT_POSITIVE = "POSITIVE"
VERDICT_NO_DATA = "NO_DATA"

# 🚨 判决键里不许内建价值判断（「negative」与「不利」是两件事，
#    两条单独读都对、合起来矛盾 —— 第 179 层）。
BRANCH_ORDER = (VERDICT_POWERLESS, VERDICT_NEGATIVE, VERDICT_POSITIVE,
                VERDICT_NOT_MEASURABLE)

# 🚨 登记**结构性不可达**的分支（第 141 层）。
#   不删、不改判据：把它改成可达需要让 CI 用比 MDE 更宽的临界值
#   （即 t 临界值），而那是**换判据臂**（第 183 层明禁）。
#   登记它是为了让「R4 写了四档、实际只有三档」这件事在代码里可见，
#   而不是让读者数分支个数时以为四档都在用。
#   守卫 `tests/test_forward_d2_verdict.py` 钉住：代数上确实不可达，
#   且本清单恰好等于「不可达分支集合」。
DEAD_BRANCHES = (
    VERDICT_NOT_MEASURABLE,
)


def leg_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """取涨腿，并剔掉不可判定的行。

    ⚠️ 剔除条件是 `gross_pct` 为 NaN，**不是** `result` 的某个取值：
       实测 `result` 只有 hit/miss/flat/no_data，没有 `verified`，
       而 `no_data` 那行（600825.SH）的 `gross_pct` 就是 NaN。
       ⇒ 按 `gross_pct` 判是「量真的在不在」，按 `result` 判是「名字承诺了什么」。
    """
    for col in REQUIRED_COLUMNS:
        if col not in frame.columns:
            raise KeyError(f"verification 底稿缺必需列 {col!r}")
    up = frame[frame["direction"] == UP]
    return up[up["gross_pct"].notna() & up["entry_date"].notna()]


def blocks(leg: pd.DataFrame) -> pd.DataFrame:
    """按 `entry_date` 分块求超额均值（R1）。

    🚨 **减法顺序在本函数里不承重 —— 这一点是实测的，不要凭直觉改它。**
       三次取证（同一份 `verification.parquet`，逐字段 `==` 比，不是容差比较）：

       | 变异 | 变几个字段 |
       |---|---|
       | 减法挪到合并**之后**（先合 `market_pct` 再相减） | **0** |
       | 减法挪到合并**之前**（现状） | 基准 |
       | `market_pct := 0`（**干脆不减**） | **9（全变）** |

       ⇒ **承重的是「减不减」，不是「什么时候减」。**
       均值那半是**恒等式**（线性映射，第 176 层）；
       而「方差那半承重」这个说法**已被撤回**：同一 `entry_date` 内三个信号日的
       `market_pct` 确实互不相同（2026-10-12 那块是 `0.004265/0.002688/0.005592`），
       行级 `sd` 确实差 **4.68e-04** —— 但 `measure()` 的 sd 输入是
       **`blocks()["excess_mean"]`**（块均值的 sd），**行级 sd 在本仓零消费者**
       ⇒ 那个差谁也不读（[[guards-that-pin-existence-not-property]] 的读法：
       要问「它凭什么被允许」，更要问「**它是什么**」）。

    ⇒ 改这段顺序**改不出任何判决差异**；要改判决只能动「减不减」，
       而那属于口径变更，必须先改预注册（第 91 层）。
    """
    if leg.empty:
        return pd.DataFrame(columns=["entry_date", "excess_mean", "n_legs"])
    work = leg.assign(excess=leg["gross_pct"] - leg["market_pct"])
    out = (work.groupby("entry_date", as_index=False)
                .agg(excess_mean=("excess", "mean"), n_legs=("excess", "size"),
                     n_pred_dates=("pred_date", "nunique"))
                .sort_values("entry_date")
                .reset_index(drop=True))
    return out


def verdict_of(n: int, point: float, mde: float,
               lo: float, hi: float) -> str:
    """R4 的四档判决，顺序即判据。

    ⚠️ `POWERLESS` 必须最先判：`|c|/MDE < 1` 时点估计连**符号**都定不了，
       若先判「CI 跨 0」会把「测不出」与「符号相反」并成一档，
       而两者对下一步的动作**相反**（前者要求加数据，后者要求改结构）。
    """
    if n < MIN_BLOCKS_FOR_ANY_SIGN or abs(point) < mde:
        return VERDICT_POWERLESS
    if hi < 0:
        return VERDICT_NEGATIVE
    if lo > 0:
        return VERDICT_POSITIVE
    return VERDICT_NOT_MEASURABLE


def measure(leg: pd.DataFrame) -> dict:
    """执行 R1–R5，产出可核对、可盖章的判决块。"""
    blk = blocks(leg)
    n = len(blk)
    if n == 0:
        return {
            "verdict": VERDICT_NO_DATA,
            "n_blocks": 0,
            "n_pred_dates": 0,
            "blocks": [],
        }

    series = blk["excess_mean"].tolist()
    point = sum(series) / n
    if n >= 2:
        var = sum((v - point) ** 2 for v in series) / (n - 1)
        sd = math.sqrt(var)
        se = sd / math.sqrt(n)
        t_stat = point / se if se > 0 else float("nan")
    else:
        sd = se = t_stat = float("nan")

    mde = Z_95 * se if se == se else float("nan")   # NaN-safe，不用 .get
    ratio = abs(point) / mde if mde == mde and mde > 0 else float("nan")
    lo = point - mde if mde == mde else float("nan")
    hi = point + mde if mde == mde else float("nan")
    v = verdict_of(n, point, mde, lo, hi) if mde == mde else VERDICT_POWERLESS

    # 🚨 敏感性臂：n 小时正态近似低估临界值 ⇒ CI 被画窄（失效方向：乐观）。
    #   它**不参与**判决，只与判决并排，让读的人看见这条偏差。
    t_crit = _T_CRIT_95.get(n - 1)
    if t_crit is None:
        sens_lo = sens_hi = None
    else:
        sens_lo, sens_hi = point - t_crit * se, point + t_crit * se

    return {
        "verdict": v,
        "n_blocks": n,
        "n_pred_dates": int(leg["pred_date"].nunique()),
        "mean_excess_pct_per_block": point,
        "sd_pct": sd,
        "se_pct": se,
        "t_stat": t_stat,
        "mde_pct": mde,
        "abs_mean_over_mde": ratio,
        "ci95_low_pct": lo,
        "ci95_high_pct": hi,
        "ci_method": f"normal_approx_z={Z_95}",
        "sensitivity_student_t": {
            "role": "sensitivity",
            "role_note": "n 小时 1.96 低估真实临界值 ⇒ CI 偏窄；判决仍用 1.96",
            "t_crit_95": t_crit,
            "ci95_low_pct": sens_lo,
            "ci95_high_pct": sens_hi,
        },
        "blocks": [
            {"entry_date": str(row.entry_date),
             "excess_mean_pct": row.excess_mean,
             "n_legs": int(row.n_legs),
             "n_pred_dates": int(row.n_pred_dates)}
            for row in blk.itertuples()
        ],
    }


def sha256_of(path: Path) -> str:
    """产物输入的**字节**指纹。

    ⚠️ 读文件本身，不用 mtime —— mtime 是代理不是证据：
    本仓变异台会「改源码 → 跑测试 → 写回」，内容回到逐位相同而 mtime 被顶到现在。

    🚨 登记的债：这是**第三份**副本（`score_ranking_power.py` /
    `diagnose_hit_rate.py` 各有一份私函数，`provenance.py` 里**没有**共用实现）。
    本轮不抽共用实现 —— 那要动另外两个正在被门禁看守的脚本，
    而 CLAUDE.md 形状 17 的修法（抽单一真相源）与本轮判决无直接关系。
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def caliber() -> dict:
    """口径随数字一起离开脚本（第 159 层：字段名不是语义）。"""
    return {
        "observation_unit": "entry_date（R1：持有哪些 bar，而不是哪天下单）",
        "observation_unit_why": (
            "实测 6 个 pred_date 只对应 4 个 entry_date；10-09/10-10/10-11 "
            "三天的单全部 entry 到 2026-10-12 ⇒ 持有同一批 bar ⇒ 不是 3 个观测。"
            "10-10/10-11 是周末，出单对独立证据零新增。"),
        "leg": "direction == 'up' 且 gross_pct 非 NaN 的行，10 只等权",
        "excess_is": "逐行 (gross_pct − market_pct) 的块均值（毛口径）",
        "market_pct_is": (
            "逐信号日抽样的市场腿；同一 entry_date 内实测有 3 个不同值"
            "（2026-10-12 那块是 0.004265/0.002688/0.005592）"
            "⇒ 块均值两种减法顺序逐位相同（线性映射）"),
        "market_pct_merge_order": (
            "🚨 实测**不承重**：减法挪到合并之后，判决层逐字段 0 处变化；"
            "而 market_pct:=0（干脆不减）⇒ 9 个字段全变。"
            "⇒ 承重的是「减不减」不是「什么时候减」。"
            "⚠️ 「行级 sd 差 4.68e-04」是真的，但那个量在本仓**零消费者**"
            "（sd 的输入是块均值的 sd）⇒ 别拿它当承重墙"),
        "dead_branches": list(DEAD_BRANCHES),
        "market_pct_is_not": "不是被选中的那 10 只自身收益（那是恒等式，定不了语义）",
        "market_sample_caveat": (
            "MARKET_SAMPLE=150 是抽样池，实测 market_n_used 约 285；"
            "单种子 seed=42 在回测侧实测偏高约 0.95pp/年 ⇒ 用它当分母会压低超额，"
            "本判决的失效方向不利于平台目标"),
        "cost_caliber": "毛口径；净口径需另一次年化，混用即口径错配",
        "ci_method_is": "正态近似（1.96）；n 小时偏窄，t 临界值版本作敏感性臂并排",
        "verdict_branch_order": list(BRANCH_ORDER),
        "this_answers": "「前瞻超额腿能不能定出符号」",
        "this_does_not_answer": (
            "「年化够不够 20%」——本样本量下任何外推年化都是无源之水；"
            "产物里刻意没有任何外推年化字段（R5）"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "data/reports/forward_d2_verdict.json"))
    args = ap.parse_args(argv)

    # 🚨 第 125 层：**盖章那一刻盘上的字节 ≠ 这次执行真正跑的字节**。
    #   两侧不一致时 sidecar 不是弱证据、是反向的错误证据 ⇒ 拒绝盖章。
    #   抓点必须在**开跑前**（不是盖章前 —— 那会让检查恒真）。
    expect = provenance.code_file_sha256(ROOT, GENERATOR)

    src = Path(args.data_root) / "reports" / "verification.parquet"
    if not src.exists():
        print(f"🚨 找不到前瞻底稿：{src}", file=sys.stderr)
        return 2
    frame = pd.read_parquet(src)

    out = measure(leg_rows(frame))
    out["caliber"] = caliber()
    out["source_parquet"] = str(src)
    # 🚨 第 166 层：盖章只盖代码不盖输入 ⇒ 追加式底稿在判决落盘后合法变长时
    #   `code_file_sha256` 逐位不变 ⇒ 判 `fresh` 而它该抓的东西在视野之外。
    #   ⇒ 自报读到的字节哈希（第 156 层）。
    out["source_parquet_sha256"] = sha256_of(src)

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(outp, GENERATOR, root=ROOT, expect_bytes=expect,
                     data_root=args.data_root,
                     extra={"source_parquet_sha256": out["source_parquet_sha256"],
                            "observation_unit": "entry_date"})

    v = out["verdict"]
    print(f"判决 → {v}")
    if v != VERDICT_NO_DATA:
        print(f"  独立观测（entry_date 块） = {out['n_blocks']}"
              f"  （信号日 {out['n_pred_dates']} 个）")
        print(f"  逐块超额均值 = {out['mean_excess_pct_per_block']:+.4f}%")
        print(f"  CI95 = [{out['ci95_low_pct']:+.4f}%, {out['ci95_high_pct']:+.4f}%]"
              f"   |c|/MDE = {out['abs_mean_over_mde']:.4f}")
        s = out["sensitivity_student_t"]
        if s["t_crit_95"]:
            print(f"  敏感性臂（t={s['t_crit_95']}）CI95 = "
                  f"[{s['ci95_low_pct']:+.4f}%, {s['ci95_high_pct']:+.4f}%]")
    if v == VERDICT_POWERLESS:
        print("  ⇒ 连符号都定不了；这不等于「没有超额」，只等于「测不到」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
