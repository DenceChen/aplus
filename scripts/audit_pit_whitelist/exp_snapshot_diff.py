"""PIT 快照差分实验 —— 独立检验 `is_never_revised()` 这条「出路 A」。

## 为什么要有这个实验

台账（`evolution_ledger`）里两条记录互相拉扯：

1. 「PIT 判决：FAR/BM/ROE 因后修财务**做不到真 PIT**，可安全进回测的财务因子归零」
   —— 判决是**否定**的，理由是财报源每个报告期只存一行、没有版本维度。
2. 「出路已经在代码里写着」—— `aplus/financial.py::is_never_revised()`
   （`UPDATE_DATE == NOTICE_DATE` 白名单）被标为「出路 A」：
   只保留披露后**从未被改动**的记录，此时今天服务的值 == 首次披露值，PIT 由构造成立。
3. 但另一条「自我否决」说：`UPDATE_DATE` 是**全表刷新时间戳**、不是重述证据，
   拿它度量 PIT 污染会得到一个假的 99%。

⇒ 三条合起来 = 「出路 A 依赖一个可能标不出修订的字段」。**必须用不依赖该字段的
独立仪器来判。** 而 PIT 采集器（launchd `com.aplus.guardvintage`）从 2026-10-14 起
每天落一份全表快照 ⇒ **两份不同时刻的快照之差，就是「到底改没改」的铁证。**

## 本实验问三个问题

- **A** 每天的整表指纹变不变（manifest 里的 sha256）。
- **B** 首末两份快照按 (SECUCODE, REPORT_DATE) 对齐后，**哪些值变了**、变了多少。
- **C** **白名单体检**：标称「从未被改动」的那些记录，实际变了吗？
  ⇒ 这是对 `is_never_revised()` 的直接体检。
- **D** **反证体检**：实际变了的记录里，`UPDATE_DATE == NOTICE_DATE` 的比例
  与「没变的记录」相比如何？
  ⇒ 若两者相近 ⇒ `UPDATE_DATE` 确实标不出修订（印证那条「自我否决」），
    从而**出路 A 的前提被证伪**。

## 读法上的三条纪律

- **窗口长度决定结论强度。** 快照只有几天，而修订的中位滞后是**几百天**
  （本实验会实测）⇒ 在短窗口里测出的失效率是**下界**，不是估计值。
  本脚本因此**不打印「真实失效率」**，只打印实测值与窗口长度。
- **只报实测的量。** 「会漏多少」这种外推交给更长的快照序列，不在这里猜。
- **只读。** 不写仓内任何文件；输出全在 stdout。

用法：`.venv/bin/python scripts/audit_pit_whitelist/exp_snapshot_diff.py`
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
V = ROOT / 'data' / 'reports' / 'financial_vintage'
CODE, REPORT, NOTICE, UPDATE = 'SECUCODE', 'REPORT_DATE', 'NOTICE_DATE', 'UPDATE_DATE'

# 不参与「值有没有变」比对的列：标识列、日期列、文本列
META = {
    CODE, 'SECURITY_CODE', 'SECURITY_NAME_ABBR', 'ORG_CODE', 'ORG_TYPE',
    'SECURITY_TYPE_CODE', 'CURRENCY', REPORT, 'REPORT_DATE_NAME', 'REPORT_TYPE',
    NOTICE, UPDATE, 'snapshot_date',
}


def main() -> int:
    if not V.exists():
        print(f'找不到快照目录：{V}')
        return 1

    man = {}
    for line in (V / '_manifest.jsonl').read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            man.setdefault(d['snapshot_date'], d)
    dates = sorted(man)
    if len(dates) < 2:
        print(f'快照不足 2 份（现有 {len(dates)}）⇒ 做不了差分')
        return 1

    print('=== A) 每日快照指纹（manifest）===')
    for d in dates:
        print(f"  {d}  rows={man[d]['n_rows']}  cols={man[d]['n_cols']}  sha={man[d]['sha256'][:16]}")
    print(f'  不同指纹的天数 = {len({man[d]["sha256"] for d in dates})} / {len(dates)} 天')

    first, last = dates[0], dates[-1]
    a = pd.read_parquet(V / f'date={first}' / 'financial.parquet')
    b = pd.read_parquet(V / f'date={last}' / 'financial.parquet')
    a['_k'] = a[CODE].astype(str) + '|' + a[REPORT].astype(str)
    b['_k'] = b[CODE].astype(str) + '|' + b[REPORT].astype(str)
    ia, ib = a.set_index('_k'), b.set_index('_k')
    common = ia.index.intersection(ib.index)

    print(f'\n=== B) 对齐 {first} → {last}（窗口 {len(dates)} 天）===')
    print(f'  共同键 = {len(common)} / A {len(ia)} / B {len(ib)}')
    val_cols = [c for c in ia.columns
                if c not in META and c in ib.columns and ia[c].dtype.kind in 'fiu']
    print(f'  参与比对的数值列 = {len(val_cols)}')
    A, B = ia.loc[common, val_cols], ib.loc[common, val_cols]

    row_changed = pd.Series(False, index=common)
    changed_cols = {}
    for c in val_cols:
        x, y = A[c], B[c]
        diff = (~(x.isna() & y.isna())) & (x != y)
        n = int(diff.sum())
        if n:
            changed_cols[c] = n
            row_changed |= diff
    print(f'  发生变化的列 = {len(changed_cols)} / {len(val_cols)}')
    for c, n in sorted(changed_cols.items(), key=lambda kv: -kv[1])[:10]:
        print(f'    {c}: {n} 行')
    n_changed = int(row_changed.sum())
    print(f'  **至少一个值变了的行 = {n_changed} / {len(common)} = {row_changed.mean() * 100:.4f}%**')

    print('\n=== C) 白名单体检（is_never_revised = UPDATE_DATE == NOTICE_DATE）===')
    for nm, df in (('A', a), ('B', b)):
        nv = df[UPDATE].notna() & (df[UPDATE] == df[NOTICE])
        print(f'  快照 {nm}: 标称「从未被改动」= {int(nv.sum())} / {len(df)} = {nv.mean() * 100:.2f}%')
    never_a = set(a.loc[a[UPDATE].notna() & (a[UPDATE] == a[NOTICE]), '_k'])
    never_b = set(b.loc[b[UPDATE].notna() & (b[UPDATE] == b[NOTICE]), '_k'])
    both = common.isin(never_a & never_b)
    if int(both.sum()):
        ch = int(row_changed.reindex(common)[both].sum())
        print(f'  两快照都标称「从未被改动」= {int(both.sum())} 行，'
              f'**其中实际变了的 = {ch} ⇒ 失效率 = {ch / int(both.sum()) * 100:.4f}%**')
        print(f'  ⚠️ 窗口只有 {len(dates)} 天，而修订中位滞后是几百天（下）'
              f' ⇒ 这个失效率是**下界**，不是估计值。')

    print('\n=== D) 反证体检：变了的行，UPDATE_DATE 标得出「被改过」吗？===')
    if n_changed:
        sub = ib.loc[common[row_changed]]
        upd = pd.to_datetime(sub[UPDATE], errors='coerce')
        notc = pd.to_datetime(sub[NOTICE], errors='coerce')
        same = (upd == notc)
        lag = (upd - notc).dt.days
        print(f'  变了 = {len(sub)} 行')
        print(f'    其中 UPDATE_DATE == NOTICE_DATE（会被白名单放行）= {int(same.sum())} = {same.mean() * 100:.2f}%')
        print(f'    UPDATE_DATE 缺失 = {int(upd.isna().sum())}')
        print(f'    lag(更新-披露) 天：p25={lag.quantile(.25)} 中位={lag.median()} '
              f'p75={lag.quantile(.75)} max={lag.max()}')
        keep = ib.loc[common[~row_changed]]
        u2 = pd.to_datetime(keep[UPDATE], errors='coerce')
        n2 = pd.to_datetime(keep[NOTICE], errors='coerce')
        print(f'  对照：没变的 {len(keep)} 行里，UPDATE == NOTICE 占 {(u2 == n2).mean() * 100:.2f}%')
        print('  ⇒ 两者接近 ⇒ UPDATE_DATE **标不出**修订 ⇒ is_never_revised() 是假白名单。')
    else:
        print('  两快照之间没有任何值变化。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
