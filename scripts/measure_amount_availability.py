"""`amount` 字段可用性实测——因为注释里的数字会变成化石。

## 为什么需要这个脚本（而不在注释里写死一个数）

`aplus/backtest.py` 的 `_bars_tradable` 有一条
「`amount` 已知且 `<= 0` 就判停牌」的 clause。它当初的实证依据是
「59.4% 的末根 K 线 amount 是 NaN、8.8% 整列缺失」。

那个数字后来**变成了假的**：合并层 `keep="last"` 的 bug 修好之后，
`store._merge_preserving_fidelity` 拦住了降级写入，2026-10-12 的补数
又把 5458 只日线重写了一遍——但注释没有跟着变。

于是它开始为一串**不再可能发生**的条件辩护，而措辞让人以为那是经验事实。

**机器会自我证伪，散文不会。** 所以这个数字不该住在散文里，
该住在一条能重跑的命令里。

## 测什么：三件事，缺一不可

1. **NaN 率** —— 列在不在是 `coverage.missing_columns` 的事；
   这里问的是「列在、值有没有」。
2. **占位常量** —— 退化也可能写成 0 或某个固定值而不是 NaN，
   只查 NaN 是瞎的。
3. **`amount == 0` 与 `volume == 0` 是否双向等价** —— 这一条才是决定
   `_bars_tradable` 那半个 clause 还有没有用的判据：
   若二者严格等价，`volume <= 0` 已经把停牌全拦下了；
   若不等价（有钱成交却成交额为 0），那批票会被静默剔除出可交易池。

## 不做什么：重复健康检查

`aplus/coverage.py` 已经在查「文件健不健康」（缺列、尾部陈旧、头部截断、
内部缺口）。本脚本**不重复**它——只回答那套体检答不了的那一问：
**这个字段的值，真的被填上了吗。**

## 但「缺列」必须响亮地报出来，不能 `continue` 跳过

首版写了 `except Exception: continue`，于是：

    2 只文件根本没有 `amount` 列  →  被静默跳过  →  报告说「NaN 零」

**一个用来检测退化的诊断，自己复制了它本该堵掉的盲区。**
「我扫了 N 只，零 NaN」这句话在有文件被跳过时是**假的**——
分母悄悄变小，而被跳过的正是最可疑的那些。

所以缺列单独成类：进报告、进分母说明、进退出码。
**跳过的数量必须可见，否则这个数字就不能被引用。**

## 用法

    python scripts/measure_amount_availability.py            # 抽样 600 只
    python scripts/measure_amount_availability.py --sample 0  # 全库
    python scripts/measure_amount_availability.py --daily-dir DIR --sample 600
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DAILY = ROOT / "data" / "master" / "daily"

#: 抽样数。字段可用性是**整体性质**，几百只足够暴露任何系统性缺失；
#: 且抽样用固定种子，同一份代码两次跑出来的样本完全一致——
#: 数字要能被复查，不能每次都不一样。
DEFAULT_SAMPLE = 600
SEED = 20261014


def pick_files(daily_dir: Path, sample: int | None, seed: int = SEED):
    """按固定种子抽样。

    `None`（`--all`）时返回全部。排序先行，抽样才有意义——
    `Path.glob` 的顺序不保证稳定，直接抽会抽到不同的票。
    """
    files = sorted(Path(daily_dir).glob("*.parquet"))
    if sample is None or sample >= len(files):
        return files
    idx = np.random.default_rng(seed).choice(len(files), sample, replace=False)
    return [files[i] for i in idx]


def _col(path: Path, name: str) -> np.ndarray:
    """读一列并强制成 float64。

    全空 object 列会被 parquet 存成 null 类型、读回来推断成 float64，
    这是**读回假象**不是存储问题——见账本「读回假象」那条。
    """
    a = pq.read_table(path, columns=[name]).column(name).to_numpy(zero_copy_only=False)
    return a.astype("float64")


def measure(files, verbose: bool = True) -> dict:
    """跑完全部文件，返回一份可序列化的账。

    返回 dict 而不是打印，是为了让测试能断言**数字**而不是字符串——
    「测出来是 0」这件事本身就该被钉住。
    """
    nan_rows = zero_rows = total_rows = 0
    amt0_vol_pos = vol0_amt_pos = both_zero = 0
    files_with_nan: list[str] = []
    files_without_col: list[str] = []
    all_pos: list[np.ndarray] = []
    n_measured = 0

    for p in files:
        # **先看列在不在**——`read_table` 缺列会抛异常，而首版就把它
        # `continue` 掉了，于是「2 只没有 amount 列」被静默跳过，
        # 报告照样说「零 NaN」。**跳过的必须报出来，否则数字是假的。**
        cols = set(pq.read_schema(p).names)
        if "amount" not in cols or "volume" not in cols:
            files_without_col.append(p.stem)
            continue
        try:
            a = _col(p, "amount")
            v = _col(p, "volume")
        except Exception:
            continue                      # 读坏了是 coverage 的事，不在这里判死
        if len(a) == 0:
            continue
        n_measured += 1
        total_rows += len(a)
        nan = np.isnan(a)
        zero = a == 0
        vzero = v == 0

        n_nan = int(nan.sum())
        nan_rows += n_nan
        if n_nan:
            files_with_nan.append(p.stem)

        zero_rows += int(zero.sum())
        both_zero += int((zero & vzero).sum())
        amt0_vol_pos += int((zero & ~vzero).sum())
        vol0_amt_pos += int((~zero & vzero).sum())
        pos = a[a > 0]
        if len(pos):
            all_pos.append(pos)

    out = {
        "n_files": len(files),
        # 「实际测到的分母」与「声称要测的分母」必须都能看见
        "n_files_measured": n_measured,
        "n_files_without_column": len(files_without_col),
        "files_without_column": files_without_col[:20],
        "total_rows": total_rows,
        "nan_rows": nan_rows,
        "zero_rows": zero_rows,
        "amount0_and_volume0": both_zero,
        "amount0_but_volume_gt0": amt0_vol_pos,
        "volume0_but_amount_gt0": vol0_amt_pos,
        "files_with_nan": files_with_nan[:20],
        "n_files_with_nan": len(files_with_nan),
    }
    if all_pos:
        x = np.concatenate(all_pos)
        n_uniq = len(np.unique(x[:200_000]))
        out.update({
            "n_positive": int(x.size),
            "min": float(x.min()),
            "p50": float(np.median(x)),
            "p99": float(np.percentile(x, 99)),
            "max": float(x.max()),
            # 占位常量检测：退化也可能写 0 或固定值而不是 NaN。
            # 唯一值几乎等于样本数 → 真实测量值；远小于 → 常量填充。
            "uniq_of_200k": int(n_uniq),
        })
    if verbose:
        report(out)
    return out


def report(m: dict) -> None:
    print(f"声称要测 {m['n_files']} 只 / **实际测到 {m['n_files_measured']} 只** "
          f"/ {m.get('total_rows', 0):,} 行")
    # 跳过的先说，不然后面的数字会被当成覆盖全库
    if m["n_files_without_column"]:
        print(f"  ⚠️ **{m['n_files_without_column']} 只没有 amount/volume 列，被排除在"
              f"统计之外**: {m['files_without_column'][:10]}")
        print("     下面所有「零」都只对**测到的那部分**成立——"
              "有文件被跳过时，这句话不是全库结论。")
    print(f"  amount 的 NaN 行数                 : {m['nan_rows']:,}"
          f"  ({m['nan_rows'] / max(m['total_rows'], 1) * 100:.4f}%)")
    print(f"  含 NaN 的文件                       : {m['n_files_with_nan']}")
    print(f"  amount == 0 的行                    : {m['zero_rows']:,}")
    print(f"    其中 volume 也 == 0（自洽·停牌）  : {m['amount0_and_volume0']:,}")
    print(f"    **其中 volume > 0（矛盾）**       : {m['amount0_but_volume_gt0']:,}")
    print(f"  volume == 0 但 amount > 0（反向矛盾）: {m['volume0_but_amount_gt0']:,}")
    if "uniq_of_200k" in m:
        print(f"  正值分位数(元): min {m['min']:,.0f} | 中位 {m['p50']:,.0f} "
              f"| p99 {m['p99']:,.0f} | max {m['max']:,.0f}")
        print(f"  唯一值 {m['uniq_of_200k']:,}/200,000 ← 远小于则说明有占位常量")
    n_bad = m["amount0_but_volume_gt0"] + m["volume0_but_amount_gt0"]
    if n_bad == 0:
        print("\n  判据: `amount==0` 与 `volume==0` **严格等价**")
    else:
        print(f"\n  判据: **两者不等价**（{n_bad} 行）——`amount` 会影响可交易判定，"
              "见 tests 里的 KNOWN_BAD 白名单")

    # **三条结论必须同时成立才能说「完全可用」。**
    # 首版这里只查了 NaN 和缺列，漏了矛盾项，于是报告自己打自己的脸：
    # 上一行刚说「两者不等价」，下一行就说「完全可用」。
    if m["nan_rows"] == 0 and m["n_files_without_column"] == 0 and n_bad == 0:
        print("  → `amount` 在此库**完全可用**；`_bars_tradable` 那个 amount clause "
              "是冗余的防御，不是经验必需。")
    elif n_bad:
        print("  → 存在「有成交却成交额为 0」的行：`_bars_tradable` 会把**成交过的** "
              "bar 判成停牌，可交易的票被静默剔除，且不报任何错。")
    elif m["nan_rows"] == 0:
        print("  → 测到的部分 `amount` 完全可用；但有文件缺列，"
              "**不能说这是全库结论**。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="amount 字段可用性实测")
    ap.add_argument("--daily-dir", type=Path, default=DEFAULT_DAILY)
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help=f"抽样只数（默认 {DEFAULT_SAMPLE}）；传 0 表示全库")
    a = ap.parse_args(argv)
    files = pick_files(a.daily_dir, None if a.sample == 0 else a.sample)
    if not files:
        print(f"没有 parquet 文件：{a.daily_dir}")
        return 1
    m = measure(files)
    # 退出码：矛盾最严重（非零），缺列次之（也非零）——
    # **跳过文件不能让退出码变绿**，否则 CI 里它就是一句安慰。
    if m["amount0_but_volume_gt0"] or m["volume0_but_amount_gt0"]:
        return 2
    if m["n_files_without_column"] or m["nan_rows"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
