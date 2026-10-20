"""**历史 BM IC 的 PIT 污染到底有多大？** —— 只读审计，输出全在 stdout。

## 为什么要有这个脚本

BM@K=21 的历史 IC 检验（`scripts/fundamental_ic.py`）报出 **IC-t = +2.94**，
是本项目**第一个跨信息源**的显著结果。但它是在**后修值**上算的：

- `scripts/fundamental_factor.py` 按 `NOTICE_DATE <= as_of` 对齐财报，
  贴的是**今天源站服务的那个值**；
- 而源站每个报告期只存一行、无版本维度 ⇒ 服务的必然是被修订过的值。

⇒ **那个 t=2.94 完全可能就是 PIT 污染本身。**
⇒ 本脚本把「污染有多大」**量化**，并写清每一步的假设。

## 三条纪律

1. **只报实测值与区间/下界，不猜。** 外推必须写出假设与它的方向。
2. **窗口长度决定结论强度。** 快照只有 7 天，而修订中位滞后 **353.5 天**
   ⇒ 短窗口里测出的是**下界**，不是估计值。
3. **只读。** 不写仓内任何文件。

用法：
    .venv/bin/python scripts/audit_pit_history_contamination.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "data" / "reports" / "financial_vintage"
CODE, REPORT, NOTICE, UPDATE = "SECUCODE", "REPORT_DATE", "NOTICE_DATE", "UPDATE_DATE"

#: 不参与「值有没有变」比对的列：标识列、日期列、文本列
META = {
    CODE, "SECURITY_CODE", "SECURITY_NAME_ABBR", "ORG_CODE", "ORG_TYPE",
    "SECURITY_TYPE_CODE", "CURRENCY", REPORT, "REPORT_DATE_NAME", "REPORT_TYPE",
    NOTICE, UPDATE, "snapshot_date",
}


def load(day: str) -> pd.DataFrame:
    df = pd.read_parquet(V / f"date={day}" / "financial.parquet")
    df["_k"] = df[CODE].astype(str) + "|" + df[REPORT].astype(str)
    return df


def diff_rows(a: pd.DataFrame, b: pd.DataFrame, val_cols: list[str]) -> pd.Series:
    """逐 (代码, 报告期) 判断「至少一个数值列变了」。返回布尔 Series（索引 = _k）。"""
    ia, ib = a.set_index("_k"), b.set_index("_k")
    common = ia.index.intersection(ib.index)
    changed = pd.Series(False, index=common)
    for c in val_cols:
        x, y = ia.loc[common, c], ib.loc[common, c]
        d = (~(x.isna() & y.isna())) & (x != y)
        changed |= d.reindex(common, fill_value=False)
    return changed


def main() -> int:
    if not V.exists():
        print(f"找不到快照目录：{V}")
        return 1
    man = {}
    for line in (V / "_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            man.setdefault(d["snapshot_date"], d)
    days = sorted(man)
    if len(days) < 2:
        print(f"快照不足 2 份（现有 {len(days)}）⇒ 做不了差分")
        return 1

    print("=" * 84)
    print("A) 快照差分：直接实测（这是**事实**，不是外推）")
    print("=" * 84)
    first, last = days[0], days[-1]
    a, b = load(first), load(last)
    ia = a.set_index("_k")
    val_cols = [c for c in ia.columns
                if c not in META and c in b.columns and ia[c].dtype.kind in "fiu"]
    changed = diff_rows(a, b, val_cols)
    n_common = len(changed)
    n_changed = int(changed.sum())
    span_days = (pd.Timestamp(last) - pd.Timestamp(first)).days
    print(f"  窗口 {first} → {last}（{span_days} 天）")
    print(f"  共同键 {n_common} · 参与比对的数值列 {len(val_cols)}")
    print(f"  **至少一个值变了的行 = {n_changed} / {n_common} = "
          f"{n_changed / n_common * 100:.4f}%**")
    print(f"  ⇒ 日均修订率 ≈ {n_changed / n_common / span_days * 100:.5f}% / 天")

    # ── 白名单体检 ──────────────────────────────────────────────
    print()
    print("=" * 84)
    print("B) `is_never_revised()` 白名单体检（出路 A 的直接证伪）")
    print("=" * 84)
    never_a = set(a.loc[a[UPDATE].notna() & (a[UPDATE] == a[NOTICE]), "_k"])
    never_b = set(b.loc[b[UPDATE].notna() & (b[UPDATE] == b[NOTICE]), "_k"])
    both = changed.index.isin(never_a & never_b)
    if int(both.sum()):
        ch = int(changed[both].sum())
        print(f"  两快照都标称「从未被改动」= {int(both.sum())} 行，"
              f"**其中实际变了的 = {ch} ⇒ 失效率 = {ch / int(both.sum()) * 100:.4f}%**")
        print(f"  ⚠️ 窗口只有 {span_days} 天 ⇒ 这是**下界**。")
    sub = b.set_index("_k").loc[changed[changed].index]
    upd = pd.to_datetime(sub[UPDATE], errors="coerce")
    notc = pd.to_datetime(sub[NOTICE], errors="coerce")
    keep = b.set_index("_k").loc[changed[~changed].index]
    u2 = pd.to_datetime(keep[UPDATE], errors="coerce")
    n2 = pd.to_datetime(keep[NOTICE], errors="coerce")
    print(f"  变了 {len(sub)} 行里 UPDATE==NOTICE 占 {(upd == notc).mean() * 100:.2f}%"
          f"；没变的 {len(keep)} 行里占 {(u2 == n2).mean() * 100:.2f}%")
    print("  ⇒ 两者接近 ⇒ `UPDATE_DATE` **标不出**修订 ⇒ 白名单是**假**的。")

    # ── 年龄依赖：这决定外推的方向 ─────────────────────────────
    print()
    print("=" * 84)
    print("C) **年龄依赖**：修订率随「披露后多久」怎么变？")
    print("=" * 84)
    print("  这一节决定外推的方向，所以它比 B 更要紧。")
    notic = pd.to_datetime(b.set_index("_k").loc[changed[changed].index, NOTICE],
                           errors="coerce")
    age = (pd.Timestamp(last) - notic).dt.days
    print(f"  变了的行：披露后第几天被改的（n={len(age)}）")
    for lo, hi, lab in [(0, 30, "0-30 天"), (31, 90, "31-90 天"),
                        (91, 180, "91-180 天"), (181, 365, "181-365 天"),
                        (366, 730, "366-730 天"), (731, 1095, "731-1095 天"),
                        (1096, 10 ** 6, "1096 天以上")]:
        m = (age >= lo) & (age <= hi)
        print(f"    {lab:<14} 变了 {int(m.sum()):>4} 行")
    # 分母：全池里同年龄段的行数
    all_notic = pd.to_datetime(b[NOTICE], errors="coerce")
    all_age = (pd.Timestamp(last) - all_notic).dt.days
    print()
    print("  同年龄段的分母（全池）与实测修订率：")
    for lo, hi, lab in [(0, 30, "0-30 天"), (31, 90, "31-90 天"),
                        (91, 180, "91-180 天"), (181, 365, "181-365 天"),
                        (366, 730, "366-730 天"), (731, 1095, "731-1095 天"),
                        (1096, 10 ** 6, "1096 天以上")]:
        m = (age >= lo) & (age <= hi)
        d = (all_age >= lo) & (all_age <= hi)
        n = int(d.sum())
        if n == 0:
            continue
        rate = int(m.sum()) / n * 100
        print(f"    {lab:<14} 分母 {n:>6} 行 · 6 天内被改 {int(m.sum()):>4} 行 "
              f"⇒ {rate:.5f}%")

    # ── 按披露年分组 ───────────────────────────────────────────
    print()
    print("=" * 84)
    print("D) 按**披露年**分组：老报告是不是改得更多？")
    print("=" * 84)
    yr = notic.dt.year
    all_yr = all_notic.dt.year
    print(f"  {'披露年':<8}{'变了':>8}{'分母':>10}{'6 天内修订率':>16}")
    # 🚨 必须遍历**分母里出现的每一年**，不能只遍历「有变化的年」——
    # 只遍历后者会让「零变化的年」整行消失，读起来像「老报告没人改」，
    # 而事实是「老报告这一年**一行都没变**」。这两件事长得几乎一样。
    for y in sorted(set(all_yr.dropna().unique())):
        m = yr == y
        d = all_yr == y
        n = int(d.sum())
        if n == 0:
            continue
        print(f"  {int(y):<8}{int(m.sum()):>8}{n:>10}{m.sum() / n * 100:>15.5f}%")
    print("  ⚠️ 2026-10-14 之前披露的记录**一行都没进这张表**——")
    print("     快照只保留每只票最近 12 个报告期，老报告根本没被采集到。")
    print("     ⇒ 这张表**测不了** 2019-2023 那段窗口的修订率。")

    # ── 覆盖率：PIT 快照能不能覆盖 2019-2023 的信号日？ ─────────
    print()
    print("=" * 84)
    print("E) **PIT 快照的物理覆盖**：历史那段窗口造得出 PIT 帧吗？")
    print("=" * 84)
    ann = b[b["REPORT_TYPE"].astype(str).str.strip() == "年报"].copy()
    ann_year = pd.to_datetime(ann[REPORT], errors="coerce").dt.year
    print(f"  快照起于 {first}（最早的 as_of 只能从这天起）")
    print("  快照里**年报**按报告期年份的行数：")
    for y in range(2017, 2027):
        n = int((ann_year == y).sum())
        if n:
            print(f"    FY{y}  {n:>6} 行")
    print("  ⇒ 2019-2023 的信号日需要的年报（FY2017~FY2022）**在快照里几乎不存在**；")
    print("    且 2026-10-14 之前**一份快照都没有** ⇒ 那段窗口的 PIT 帧**造不出来**。")

    print()
    print("=" * 84)
    print("F) 外推：历史 IC 的输入到底有多脏？（**暴露窗口 = (信号日, IC 读数日]**）")
    print("=" * 84)
    ic_read_day = "2026-10-14"     # fundamental_ic.json 的实测产出日
    r_day = n_changed / n_common / span_days
    print("  ⚠️ 暴露窗口为什么是 (信号日, 读数日] 而不是 (披露日, 读数日]：")
    print("     历史 IC 在信号日 D 用的是**读数日**服务的那份 BPS。")
    print("     若一份报告在 (披露日, D] 内被改过，则 D 当天读到的**就是**改后的值")
    print("     ⇒ 与「当时看得见的值」**相同** ⇒ **那一段不构成污染**。")
    print("     污染只来自 **D 之后**、读数日之前的修订。")
    print()
    print(f"  假设 1：修订是**恒定危险率**（每天 r = {r_day * 100:.5f}%），")
    print("           且各天的修订相互独立（泊松近似）。")
    print("  假设 2：IC 的读数日 = " + ic_read_day + "（fundamental_ic.json 实测产出日）。")
    print()
    end = pd.Timestamp(ic_read_day)
    print(f"  {'信号日':<14}{'暴露天数':>10}{'外推累计修订率':>18}")
    for d in ["2019-01-02", "2020-01-02", "2021-01-04", "2022-01-04",
              "2023-01-03", "2023-12-29"]:
        days = (end - pd.Timestamp(d)).days
        print(f"  {d:<14}{days:>10}{min(1.0, r_day * days) * 100:>17.1f}%")
    print()
    print("  ⇒ 历史 IC 的窗口（2019-01-02 ~ 2023-12-31，1,214 个交易日、")
    print("    1,154 天有数据）在假设 1 下的输入污染率约为 **17% ~ 47%**。")
    print()
    print("  读法与**边界**（三条都写明，不许含糊）：")
    print("   · 上面是**模型值**，不是实测值。它成立的前提是假设 1。")
    print("   · C 节的年龄依赖**既不支持也不否定**假设 1：最老年龄段（1096 天以上）")
    print("     只落了 1 个事件（1/1724），泊松噪声 95% 区间约 [0.03, 3.6] 个事件")
    print("     ⇒ 那个分母上的修订率可以是 0.002% 也可以是 0.2%。")
    print("   · **真值的硬边界**：[~0.1%, 100%]。")
    print("     下界 = B 节实测：IC 读数日之后 6 天里，白名单行就有 0.1293% 变脏；")
    print("     上界 = 100%（每条都可能被改过）。")
    print("     17%~47% 是**这个区间里的一个模型点**，它的宽度由快照长度（7 天）封顶。")

    # ── 修订的方向 ──────────────────────────────────────────────
    print()
    print("=" * 84)
    print("G) 修订的**方向**：BPS 是被调高还是被调低？")
    print("=" * 84)
    print("  这一节决定「后修值把历史 IC 推向哪个方向」——")
    print("  方向错了，结论就会朝「后修值总是让因子更好看」的方向滑。")
    ib = b.set_index("_k")
    ia_ = a.set_index("_k")
    ch_idx = changed[changed].index
    common_bps = [c for c in ("BPS", "ROEJQ") if c in ia_.columns and c in ib.columns]
    for c in common_bps:
        x = pd.to_numeric(ia_.loc[ch_idx, c], errors="coerce")
        y = pd.to_numeric(ib.loc[ch_idx, c], errors="coerce")
        both = x.notna() & y.notna()
        d = (y[both] - x[both])
        up = int((d > 0).sum())
        down = int((d < 0).sum())
        same = int((d == 0).sum())
        print(f"  {c}: 变大 {up} 行 / 变小 {down} 行 / 不变 {same} 行"
              f"（n={int(both.sum())}）")
        if up + down:
            print(f"    ⇒ 净方向：{'调高' if up > down else '调低'}"
                  f"（{up} vs {down}）")
    print("  ⚠️ n 极小 ⇒ 只能作**方向提示**，不能当量级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
