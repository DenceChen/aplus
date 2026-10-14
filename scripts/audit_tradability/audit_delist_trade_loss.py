"""审计：持仓股退市/停牌导致「买不到或卖不出」的成交是否被静默丢弃。

只读。不改生产代码、不改数据库、不改 reports。
复现 aplus/backtest.py::_next_bars 的窗口定位逻辑，对全库逐股检查
「信号日存在但成交窗口越过该股数据末端」的情形。
"""
import glob, os, sys
import numpy as np
import pyarrow.parquet as pq

ENTRY_LAG = 1
DAILY = "data/master/daily"


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.parquet")))
    codes = [os.path.basename(f)[:-8] for f in files]
    basic = {}
    import csv
    with open("/tmp/bs_stock_basic.csv") as fh:
        for r in csv.DictReader(fh):
            if r["type"] != "1":
                continue
            sym = "%s.%s" % (r["code"].split(".")[1], r["code"].split(".")[0].upper())
            basic[sym] = r["outDate"]

    n_tail_truncated = 0     # 成交窗口越过该股数据末端 -> continue，trade 消失
    n_suspended_bar = 0      # 成交窗口内某根停牌 -> continue
    n_ok = 0
    details = []
    tail_by_year = {}

    for f, code in zip(files, codes):
        d = pq.read_table(f, columns=["date", "volume"]).to_pandas()
        dates = d["date"].to_numpy(dtype="U10")
        vol = d["volume"].to_numpy(float)
        n = len(dates)
        if n < 3:
            continue
        zero = vol <= 0
        # 每个信号日 D -> 成交窗口 = pos+ENTRY_LAG-1, pos+ENTRY_LAG
        pos = 1
        while pos < n:
            entry = pos + ENTRY_LAG - 1
            exit_i = entry + 1
            if entry >= n or exit_i >= n:
                n_tail_truncated += 1
                yr = dates[n - 1][:4]
                tail_by_year[yr] = tail_by_year.get(yr, 0) + 1
                if len(details) < 25:
                    details.append((code, "TAIL_TRUNCATED", dates[pos - 1],
                                    dates[n - 1], basic.get(code, "")))
            elif zero[entry] or zero[exit_i]:
                n_suspended_bar += 1
            else:
                n_ok += 1
            pos += 1

    print("=" * 72)
    print("审计：退市/停牌导致的成交窗口截断（全库 5460 只，date+volume 列，谓词下推无需全量读 OHLC）")
    print("=" * 72)
    print("可正常成交 (volume>0)          : %8d" % n_ok)
    print("因停牌被跳过 (volume==0)       : %8d  (%.2f%% of skipped)" % (
        n_suspended_bar, 100 * n_suspended_bar / max(1, n_suspended_bar + n_tail_truncated)))
    print("因越过数据末端被丢弃 (退市尾段) : %8d  (%.2f%% of skipped)" % (
        n_tail_truncated, 100 * n_tail_truncated / max(1, n_suspended_bar + n_tail_truncated)))
    print()
    print("被丢弃的成交按「该股末根年份」分布（= 退市/长期停牌年份）：")
    for y in sorted(tail_by_year):
        print("   %s : %d" % (y, tail_by_year[y]))
    print()
    print("样例 (code, 原因, 信号日, 该股末根, baostock outDate):")
    for r in details[:12]:
        print("   ", r)


if __name__ == "__main__":
    main()
