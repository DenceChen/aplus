"""vintage 守卫：检查当天快照是否已登记，缺则自动补跑。

launchd: com.aplus.guardvintage

## 为什么必须是独立作业，不能挂在 `guard_daily` 里

`guard_daily` 负责给 `run_daily` 补跑。若把 vintage 也塞进去，
就建立了「run_daily 失败 → vintage 断」这条依赖——而采集断一天是
**永久损失**，代价与 `run_daily` 失败完全不对等。

所以：**两个守卫各自独立，谁也拖不垮谁。**

## 为什么需要它，而不是「22:30 跑一次就完了」

launchd 的日历触发**不补跑**：机器在触发时刻睡着或关机，那天就永久错过。
而「今天不落盘，源站那天服务过的原始值永久丢失」——
**没有任何补救手段能把它找回来**，这是本项目唯一一个不可逆的任务。

采集器自带 `--ensure`（重复触发是空操作），
所以守卫「今天已登记就跳过」的判据让一天被触发两次也只付一次网络往返。

## 它**不会**去补历史缺口

源站只服务当前值。今天去抓，拿到的永远是今天的数；
把它标成 10-13 的快照写进 manifest，就是一条**伪造的证据**——
「10-13 源站服务的是这个」，而它明明来自 10-14。

**缺口是已知的损失，伪造是未知的污染。** 后者更坏：
它会让将来的 PIT 判决建立在一个从不存在的时间切片上，
而且**表面上完全正常**。所以历史缺口只如实报，不假装补上。
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.vintage import VINTAGE_ROOT, span  # noqa: E402

#: 交易日历从日线数据的日期并集反推。
DAILY_DIR = ROOT / "data" / "master" / "daily"

#: 取前 N 只取并集。实测 5 只就收敛到 1884 天、400 只仍是 1884，
#: 40 只是安全余量，代价 0.26 秒。
CALENDAR_SAMPLE = 40

_CAL: dict[str, set[str]] = {}


def trading_days(daily_dir: Path = DAILY_DIR,
                 sample: int = CALENDAR_SAMPLE,
                 refresh: bool = False) -> set[str]:
    """交易日历 = 日线日期的**并集**（不是单只股票的日期集）。

    ## 为什么必须取并集

    单只股票有停牌和上市时间。实测 000005.SZ 只有 1290 天，
    而全市场是 1884 天。只看一只，上市前的日子会被误判成「没采集」。

    ## 为什么样本 40 只就够

    实测并集从 5 只起就不再增长（5/40/400 只全是 1884 天）——
    因为**只要有一只票在交易，那天就是交易日**。
    40 只是余量，不是精度需求。

    ## 缓存按**路径**分键

    首版只有一个全局槽位，于是 `trading_days(daily_dir=X)`
    会返回由 `Y` 建出来的日历——参数形同虚设，
    而测试替身与真实目录会互相污染。
    """
    key = str(Path(daily_dir).resolve())
    if key in _CAL and not refresh:
        return _CAL[key]
    out: set[str] = set()
    for p in sorted(Path(daily_dir).glob("*.parquet"))[:sample]:
        try:
            d = pd.read_parquet(p, columns=["date"])["date"].astype(str)
        except Exception:
            continue
        out |= set(d)
    _CAL[key] = out
    return out


def is_gap_day(day: str, cal: set[str] | None = None) -> bool:
    """这一天该不该报缺口。

    ## 「不在日历里」有两种，判法相反

    | 情况 | 判法 | 理由 |
    |---|---|---|
    | 在日历覆盖**范围内**但不在日历里 | 非交易日 | 日历说了算，那就是周末/节假日 |
    | **超出**日历覆盖范围（通常是今天） | 假定为交易日 | 见下 |

    首版把两者混成一句 `day not in cal`，于是
    **每个周末都会被报成「永久丢失」**——而告警疲劳会让一个
    防永久损失的探测器在几周内失效。真缺口被日常噪声淹没，比没告警更糟。

    ## 为什么范围外的未知要当作交易日

    日历来自日线数据，而日线由 `run_daily`（18:30）推进。
    采集在 22:30 跑，日历通常**还停在昨天**，
    「今天是不是交易日」当时根本无从得知。

    两种误判的代价完全不对称：
    - 误判为交易日 → 多抓一次，多花 36 MB
    - 误判为非交易日 → **那一天永久丢失**

    所以判据必须朝「宁可多采」偏。
    """
    if cal is None:
        cal = trading_days()
    if not cal:
        return True                     # 完全没有日历 → 全部按交易日
    lo, hi = min(cal), max(cal)
    if day < lo or day > hi:
        return True                     # 超出覆盖范围 → 未知，假定是交易日
    # 日历里**有**的才是交易日。范围内但不在日历里 → 周末/节假日 → 不报。
    # （首版这里写成 `day not in cal`，方向反了，于是每个周末都报「永久丢失」。
    #   而告警疲劳会让防永久损失的探测器在几周内失效。）
    return day in cal


def registered_days(root: Path = VINTAGE_ROOT) -> set[str]:
    """**已经在 manifest 里登记过的日期**。

    注意判据用 manifest 而不是「分区文件在不在」：
    文件存在只说明写过，manifest 才有条目才说明**整轮抓完了**。
    半份快照冒充完整快照，比没有更坏（见采集器 `main()` 的闸）。

    ## 坏行必须跳过而不是抛异常

    manifest 是逐行追加的 jsonl，可能因为断电/并发写而留下半行。
    若这里抛异常，**守卫就等于没有**——而它抛异常的方式
    （launchd 记一条 stderr、退出码非 0）极容易被忽略，
    然后采集就静默地一天天漏下去。
    """
    p = Path(root) / "_manifest.jsonl"
    if not p.is_file():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            day = json.loads(line).get("snapshot_date")
        except Exception:
            continue                      # 半行/坏行：跳过，不崩
        if day:
            out.add(day)
    return out


def days_missed(root: Path = VINTAGE_ROOT, today: str | None = None,
                cal: set[str] | None = None) -> list[str]:
    """从第一天快照到今天之间，**缺登记的交易日**。

    只查 `[first, today]`，不查未来。

    **周末与节假日不算缺**——它们由 `is_gap_day` 排除。
    不排除的话，每个周末都会报一次「永久丢失」，
    而**告警疲劳会让一个防永久损失的探测器在几周内失效**。
    真缺口被日常噪声淹没，比没有告警更糟。
    """
    days = registered_days(root)
    days.discard(None)
    if not days:
        return []
    today = today or date.today().isoformat()
    if cal is None:
        cal = trading_days()
    first = min(days)
    out, cur = [], _next(first)
    while cur <= today:
        if cur not in days and is_gap_day(cur, cal=cal):
            out.append(cur)
        cur = _next(cur)
    return out


def _next(day: str) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime(
        "%Y-%m-%d")


def main() -> int:
    # **根目录显式传下去**：`def f(root=VINTAGE_ROOT)` 的默认值在定义时绑定，
    # 改模块级路径对已定义好的默认参数**不起作用**——
    # 运维热改和测试替身都会踩到，且行为与预期不符。
    root = VINTAGE_ROOT
    today = date.today().isoformat()
    if today in registered_days(root):
        print(f"vintage {today} 已登记，无需补跑")
    else:
        print(f"vintage {today} 未登记 → 立即采集（只抓今天）")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" /
                                 "collect_financial_vintage.py"), "--ensure"],
            cwd=ROOT, capture_output=True, text=True, timeout=5400)
        print(r.stdout[-2000:])
        if r.returncode != 0:
            print(r.stderr[-2000:])
            raise SystemExit(r.returncode)

    s = span(root)
    missed = days_missed(root)
    print(f"  覆盖 {s['first']} ~ {s['last']}，{s['n_days']}/{s['calendar_days']} 天")
    if missed:
        print(f"  ⚠️ {len(missed)} 天无登记：{missed[:10]}"
              f"{' …' if len(missed) > 10 else ''}")
        print("     **这些天永久丢失，补不回来**：源站只服务当前值，"
              "今天抓到的永远是今天的数。")
        print("     所以守卫**不会**去补历史——把今天的值标成过去的日期写进 "
              "manifest，那是伪造证据，比一个已知的缺口坏得多。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())