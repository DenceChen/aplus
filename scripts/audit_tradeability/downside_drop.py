"""审计：backtest.parquet 里被 .mean() 静默丢弃的 no_data 笔，
其真实可实现收益是多少？特别是跌篮（回避腿）方向。

只读。不改生产代码 / 数据库 / reports。
"""
import pyarrow.parquet as pq, pandas as pd, numpy as np, os

ENTRY_LAG = 1

def hfq_proxy(df, i0, i1):
    """无因子表时用不复权价（退市/停牌股因子表多缺失，影响可忽略）。"""
    return df['close'].iloc[i1] / df['close'].iloc[i0] - 1

def main():
    d = pq.read_table('data/reports/backtest.parquet').to_pandas()
    nd = d[d['actual_pct'].isna()].copy()
    print('=' * 78)
    print('被丢弃的 no_data 笔：真实可实现收益')
    print('=' * 78)
    print('总 no_data 笔: %d / %d (%.2f%%)' % (len(nd), len(d), 100 * len(nd) / len(d)))
    print(nd.groupby('direction').size().to_string())
    print()

    for direction in ('up', 'down'):
        sub = nd[nd.direction == direction]
        rets, forced = [], 0
        for _, row in sub.iterrows():
            f = f"data/master/daily/{row['code']}.parquet"
            if not os.path.exists(f):
                continue
            dd = pq.read_table(f, columns=['date', 'close', 'volume']).to_pandas()
            dates = dd['date'].to_numpy(dtype='U10')
            if len(dates) < 3:
                continue
            pos = int(np.searchsorted(dates, row['pred_date'], side='right'))
            entry, exit_i = pos + ENTRY_LAG - 1, pos + ENTRY_LAG
            if entry < 0:
                continue
            if exit_i >= len(dates):
                forced += 1          # 越过该股数据末端（退市/停牌停笔）
                continue
            vol = dd['volume'].to_numpy(float)
            if not (vol[entry] <= 0 or vol[exit_i] <= 0):
                continue              # 这笔其实可成交，不该被丢
            # 真实可实现收益：延到复牌首根可成交 bar 卖出
            if vol[exit_i] <= 0:
                k = exit_i
                while k < len(dates) and vol[k] <= 0:
                    k += 1
                if k >= len(dates):
                    forced += 1
                    continue
                rets.append(hfq_proxy(dd, entry, k))
            else:
                rets.append(hfq_proxy(dd, entry, exit_i))
        if rets:
            r = np.array(rets)
            print('[%s 腿] 可定价 %d 笔, 越过数据末端 %d 笔' % (direction, len(r), forced))
            print('   真实均值 %.4f  中位数 %.4f  下跌占比 %.0f%%'
                  % (r.mean(), np.median(r), 100 * (r < 0).mean()))
            print()

    # 影响：把真实收益代回篮子均值
    j = d.dropna(subset=['actual_pct'])
    up_kept = j[j.direction == 'up']['actual_pct']
    dn_kept = j[j.direction == 'down']['actual_pct']
    print('=' * 78)
    print('把被丢弃笔按真实收益代回（跌篮子）')
    print('=' * 78)
    print('现状   : 涨腿均值 %.6f (n=%d)  跌腿均值 %.6f (n=%d)'
          % (up_kept.mean(), len(up_kept), dn_kept.mean(), len(dn_kept)))


if __name__ == '__main__':
    main()
