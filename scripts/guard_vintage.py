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

采集器自带 `--ensure`：今天**已登记**就直接退出。
守卫「今天已登记就跳过」的判据也基于同一句话。

🚨 **但这两句合起来是一句假话，2026-10-15 实测证伪**：
「已登记」只在采集器**跑完并写完 manifest 之后**才为真，
而跑完要 **68.3 分钟**（`data/vintage.log` 自报）。
⇒ **`--ensure` 只能挡住「上一次已经跑完」的重复触发，挡不住 68 分钟内的重复。**
⇒ 而本机恰好有**两个调度器在 30 分钟内各起一次**
（`com.aplus.guardvintage` 9:30、`com.aplus.vintage` 10:00，指向同一个采集器）——
`--ensure` 的「查了再动」窗口长达 68 分钟，**不是原子的**，两次都读到「未登记」。

> 这一条的形状值得单独记：**文档与代码完全一致，两者一起错在世界上。**
> 旧文原话是「让一天被触发两次也只付一次网络往返」——
> 听起来像在描述一个已经成立的机制，而它描述的是一个**尚不存在**的机制。
> （与形状 128 同族：help 说的是快照，实现取的是一只票。
> 这里文档说的是「已登记」，现实里差着 68 分钟。）
>
> ⇒ 缺陷登记：`docs/TODO_vintage_double_collection_2026-10-15.md`。
> ⚠️ 本守卫里两处**照抄了这句假话**的注释/断言（「45 分钟」那个数也早已过期，
> 实测 68.3）—— 它们钉住的是「实参里传了 `--ensure`」这个**形状**，
> 而 `--ensure` 能不能挡住并发是**另一个量**（第 119 层同族）。

## 两个调度器

| launchd 项 | 时刻 | 做什么 |
|---|---|---|
| `com.aplus.guardvintage` | 9:30 | 本脚本 → **自己再起一次**采集 |
| `com.aplus.vintage` | 10:00 | 直接跑采集器 |

⇒ 守卫在 9:30 就跑完了它「判断该不该补跑」的职责，
10:00 的那个调度器面对的是一个**尚未落盘**的日子 ⇒ 于是重复。
**守卫永远抓不到「定时器漏跑」，因为它总是先跑。**
修法应当是让守卫**晚于**采集器并只在其失败时兜底，
但那要改 launchd 调度，属于独立一轮工作，见上面那份 TODO。

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

# ── 采集作业的实测耗时与由此推出的超时 ───────────────────────────────
#: **实测单跑耗时 68.3 分钟**，出处：`data/vintage.log` 2026-10-15 那次跑
#: 自己的收尾行 ——「2026-10-15: 65391 行 / 5460 只 / 141 列 / 耗时 68.3 分钟，
#: 失败 0 只」。⚠️ 这是**产物自己报的数**，不是估算。
COLLECT_MEASURED_S = 68.3 * 60
#
#: 🚨 旧字面量 `timeout=5400`（90 分钟）**只比实测多 22 分钟**——
#:   那不是安全边际，是一枚硬币。而机器上只要还有**第二个采集实例**
#:   在跑（launchd `com.aplus.guardvintage` 9:30 与 `com.aplus.vintage` 10:00
#:   指向同一个采集器，见 §两个调度器），这一个就被拖过线。
#:   **2026-10-15 实测**：守卫 09:30 起跑、11:00 抛 `TimeoutExpired`；
#:   同一时段另一次采集 11:08 正常收尾。
#:
#: ⇒ 超时取 **3× 实测**：机器上确有第二个实例（争抢 ⇒ 各自约 2× 慢），
#:   3× 覆盖「双实例 + 常规争抢」。它是**兜底**，不是判据——
#:   双实例本身是缺陷，见 `docs/TODO_vintage_double_collection_2026-10-15.md`。
COLLECT_TIMEOUT_S = 3 * COLLECT_MEASURED_S

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
        timed_out = False
        try:
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" /
                                     "collect_financial_vintage.py"), "--ensure"],
                cwd=ROOT, capture_output=True, text=True,
                timeout=COLLECT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # 🚨 超时**既不是成功也不是失败**：子进程可能已经写完 manifest
            #   只是没退出，也可能真的卡住。两种在终端上长得一模一样
            #   （第 8 种形状：退出码/进程状态都不是事实）。
            #   ⇒ 判决必须**回头再看一眼产物**，不许拿超时当结论。
            #
            # ⚠️ 这里**不 raise**：旧写法让 TimeoutExpired 直接冒出去，
            #   于是 `span(root)` / `days_missed(root)` **一行都没跑** ——
            #   守卫在超时那天**整个变成空操作**，而唯一的证据是一段
            #   没人读的 traceback。「守卫存在」与「守卫今天跑了」不是一回事。
            timed_out = True
            print(f"⚠️ 采集超过 {COLLECT_TIMEOUT_S / 60:.0f} 分钟仍未退出"
                  f"（实测单跑 {COLLECT_MEASURED_S / 60:.1f} 分钟，"
                  f"留 {COLLECT_TIMEOUT_S / COLLECT_MEASURED_S:.1f}× 余量）")
            print("   ⚠️ 超时**不是结论**。下面按**产物**重判今日是否已登记。")
        else:
            print(r.stdout[-2000:])
            if r.returncode != 0:
                print(r.stderr[-2000:])
                raise SystemExit(r.returncode)

        if timed_out:
            if today in registered_days(root):
                print(f"   ⇒ 产物显示 {today} **已登记** —— 采集其实完成了，"
                      f"只是子进程没退出")
            else:
                print(f"   ⇒ 产物显示 {today} **仍未登记** —— "
                      f"今天的快照可能没落盘（源站过了今天就取不回这一天的值）")

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