"""可转债数据层冒烟（路线 A 第一阶段）：动态名单 + 3 只活跃转债近 10 日 K 线。

用法：
    .venv/bin/python scripts/bond_smoke.py
    .venv/bin/python scripts/bond_smoke.py --days 10

验收点：
- fetch_bond_list() 返回全市场名单（沪 SSE 文件 + 深腾讯扫描，去重合成）
- TencentBondSource.fetch_daily 拉近 N 日日 K（volume 单位=张）
- bond_convert.is_active 摘牌检测：末根 K 线过旧即标 inactive（实证：浦发转债/九洲转2）
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.sources.bond_convert import is_active
from aplus.sources.bond_list import fetch_bond_list
from aplus.sources.tencent_bond_source import TencentBondSource

# 活跃种子（冒烟提速；种子摘牌时自动从动态名单新代码段顺位补位）
SEED_ACTIVE = ["113056.SH", "127045.SZ", "127102.SZ"]
# 摘牌负例（展示 K 线新鲜度检测有效性）
SEED_DELISTED = ["110059.SH", "123089.SZ"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--hunt-limit", type=int, default=30, help="种子不足时最多试探的名单代码数")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=int(args.days * 2.5))  # 粗放窗口容节假日
    start_s, end_s = start.isoformat(), end.isoformat()

    print("[1/3] 拉取可转债动态名单（沪 SSE 文件 + 深腾讯扫描）……")
    bonds = fetch_bond_list()
    by_exchange = bonds["exchange"].value_counts().to_dict()
    print(f"    名单总数: {len(bonds)} 只  分市场: {by_exchange}")
    print(f"    样例: {bonds.head(2).to_dict('records')} … {bonds.tail(2).to_dict('records')}")

    src = TencentBondSource()
    as_of = date.today()

    print(f"[2/3] 拉取活跃转债近 {args.days} 日 K 线……")
    active_hits, misses = [], []
    # 候选池：种子在前，其后取名单尾部（代码大＝新券，命中活跃概率高）
    pool = SEED_ACTIVE + bonds["code"].tolist()[::-1]
    for code in pool:
        if len(active_hits) >= 3:
            break
        if code in {c for c, _ in active_hits} or code in misses:
            continue
        if len(misses) >= args.hunt_limit:
            break  # 试探预算用尽（长假/种子失效日）——有几分收获算几分
        try:
            df = src.fetch_daily(code, start_s, end_s)
        except Exception:  # noqa: BLE001 — 无数据/摘牌/窗口空都是稳定状态，顺位下一个
            misses.append(code)
            continue
        if df.empty or not is_active(df.iloc[-1]["date"], as_of=as_of):
            misses.append(code)
            continue
        active_hits.append((code, df.tail(args.days).reset_index(drop=True)))
    for code, recent in active_hits:
        first, last = recent.iloc[0], recent.iloc[-1]
        span = (float(last["close"]) / float(first["open"]) - 1) * 100
        print(
            f"    {code} 近{len(recent)}根 | {first['date']}→{last['date']} | "
            f"末收 {last['close']:.3f} 末量 {last['volume']:.0f} 张 | 区间 {span:+.2f}%"
        )
    if misses:
        print(f"    （顺位跳过 {len(misses)} 只无数据/摘牌候选，如 {misses[:3]}）")

    print("[3/3] 摘牌检测负例（K 线过旧应判 inactive）……")
    for code in SEED_DELISTED:
        try:
            df = src.fetch_daily(code, "2000-01-01", end_s)
            last_date = df.iloc[-1]["date"]
            print(f"    {code} K线末根 {last_date} → active={is_active(last_date, as_of=as_of)}")
        except Exception as exc:  # noqa: BLE001
            print(f"    {code}: {exc}")

    ok = len(active_hits) == 3
    print(f"\n冒烟结论: {'通过' if ok else '失败'} —— 名单 {len(bonds)} 只，活跃样本 {len(active_hits)}/3")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
