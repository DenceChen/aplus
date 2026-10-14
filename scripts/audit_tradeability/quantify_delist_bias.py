"""量化：跌篮子（回避腿）里被静默丢弃的退市股，值多少超额。

关键机制：策略把这些票判为「看跌」，它们随后停牌退市、数据到 2026-10-13
为止仍然在涨跌幅里算不出来（actual_pct=NaN），于是 verify 的 .mean()
把它们丢掉了 —— 回避成功的那些笔，一次都没被记功。

只读。
"""
import pyarrow.parquet as pq, pandas as pd, numpy as np, os, csv

def load_outdate():
    m = {}
    with open('/tmp/bs_stock_basic.csv') as fh:
        for r in csv.DictReader(fh):
            if r['type'] == '1':
                m['%s.%s' % (r['code'].split('.')[1],
                             r['code'].split('.')[0].upper())] = r['outDate']
    return m

def main():
    outdate = load_outdate()
    d = pq.read_table('data/reports/backtest.parquet').to_pandas()
    nd = d[d['actual_pct'].isna()].copy()
    dn = nd[nd.direction == 'down']

    rets, nodelist = [], []
    for _, row in dn.iterrows():
        f = f"data/master/daily/{row['code']}.parquet"
        if not os.path.exists(f):
            continue
        dd = pq.read_table(f, columns=['date', 'close', 'volume']).to_pandas()
        dates = dd['date'].to_numpy(dtype='U10')
        vol = dd['volume'].to_numpy(float)
        pos = int(np.searchsorted(dates, row['pred_date'], side='right'))
        entry = pos + 0            # entry_lag=1 -> pos
        exit_i = pos + 1
        if entry < 0 or entry >= len(dates):
            continue
        # 真实可实现收益：从入场持有到「能卖的第一根」；退市则持有到末根收盘
        if exit_i >= len(dates):
            # 越界 = 该股在窗口内停笔。按末根收盘价强制平仓计真实损益。
            rets.append(dd['close'].iloc[-1] / dd['close'].iloc[entry] - 1)
            nodelist.append(row['code'])
        elif vol[exit_i] <= 0:
            k = exit_i
            while k < len(dates) and vol[k] <= 0:
                k += 1
            if k >= len(dates):
                rets.append(dd['close'].iloc[-1] / dd['close'].iloc[entry] - 1)
                nodelist.append(row['code'])
            else:
                rets.append(dd['close'].iloc[k] / dd['close'].iloc[entry] - 1)
        else:
            continue
    r = np.array(rets)
    n_del = len(nodelist)
    print('=' * 78)
    print('跌篮子被丢弃笔的真实损益（持有到可成交/末根强平）')
    print('=' * 78)
    print('可还原 %d 笔，其中 %d 笔属于「数据到头（退市/停笔）」' % (len(r), n_del))
    print('   真实均值 %+.4f   中位数 %+.4f   亏损占比 %.0f%%'
          % (r.mean(), np.median(r), 100 * (r < 0).mean()))
    print()

    j = d.dropna(subset=['actual_pct'])
    up = j[j.direction == 'up']['actual_pct']
    dn_kept = j[j.direction == 'down']['actual_pct']
    market = j['market_pct'].mean()

    dn_now = dn_kept.mean()
    dn_fix = (dn_kept.sum() + r.sum()) / (len(dn_kept) + len(r))
    print('涨腿均值 %+.6f (n=%d)   市场 %+.6f' % (up.mean(), len(up), market))
    print()
    print('跌篮子均值  现状 %+.6f (n=%d)' % (dn_now, len(dn_kept)))
    print('         代回后 %+.6f (n=%d)' % (dn_fix, len(dn_kept) + len(r)))
    print()
    print('回避超额 down_excess = market - avg_down')
    print('         现状 %+.6f' % (market - dn_now))
    print('      代回后 %+.6f' % (market - dn_fix))
    print('         差值 %+.6f /笔' % ((market - dn_fix) - (market - dn_now)))
    print()
    print('(注：跌腿是「回避清单」，不扣成本；此处只统计被丢弃笔的相对贡献)')


if __name__ == '__main__':
    main()
