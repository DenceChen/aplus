"""交易日历的唯一真相源，以及「今天该不该出单」的三态判据。

## 判据是一个**绝对外部锚**，不是任何手打的表

「这一天是不是交易日」在本仓有且只有一个事实来源：
**仓里真实交易过的日期的并集**（`trading_days`）。
法定节假日**自动**不在里面 —— 那一天没有任何一只票成交，
所以它不可能出现在并集里。

⇒ **绝不手打节假日表。** 手打的表每年要人维护一次，
而「今年忘了加某个调休」只会在那一天静默出错一次：
不崩、不告警，只是发出了一份本不该发出的名单。

## 为什么必须取**并集**而不是单只股票的日期集

单只有停牌和上市时间。实测 `000005.SZ` 只有 1290 天，
而全市场是 7781 天（1994-09-28 → 2026-10-16）。
只看一只，上市前的日子会被误判成「没采集」。

并集里的**每一天都至少有一只票真的有数据** —— 这正是它能当锚的原因。

## 为什么样本 40 只就够

实测并集从 5 只起就不再增长（5 / 40 / 400 只全是同一天数）：
只要有一只票在交易，那天就是交易日。40 是余量，不是精度需求。
实测代价 **0.27 秒**（只读 `date` 一列 —— parquet 是列存且带行组统计量）。

## 缓存按**路径**分键

首版只有一个全局槽位，于是 `trading_days(daily_dir=X)`
会返回由 `Y` 建出来的日历 —— 参数形同虚设，
而测试替身与真实目录会互相污染。

## 🚨 「不在日历里」有两种，而本仓对它的两个判决**方向相反**

| | 误判成「交易日」 | 误判成「非交易日」 |
|---|---|---|
| `guard_vintage.is_gap_day`（采集 22:30） | 多抓一次，多花 36 MB | **那一天的数据永久丢失，不可恢复** |
| `classify_run_day`（出单 18:30） | 三份重复名单进 `verification.parquet` ⇒ 前瞻判决被污染 | 错过一个交付日 |

⇒ 所以本模块**只共享事实来源（`trading_days`）**，
两个**判决各自写死**（`classify_run_day` 与 `guard_vintage.is_gap_day`）。
把它们合成一个函数才是真正的「同一语义做两遍」——
它们是同一个**事实**、相反的**决策**。

## 🚨 调用点必须放在**增量更新之后**（2026-10-17 实测踩到）

`classify_run_day(day, trading_days())` 若在 `step_incremental_update` **之前**调用，
那么每个真正的交易日（例：周一 2026-10-19，库最新停在 10-16）都会得到
「工作日不在库里」⇒ **合法交易日反而被 rc=2 挡住，一天都不出单**。
更新正是让「今天」成为仓里交易日的那一步，判据必须在它之后。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

#: 仓内日线目录。日历从它的 parquet 里反推。
DAILY_DIR = Path(__file__).resolve().parent.parent / "data" / "master" / "daily"

#: 取前 N 只取并集。实测 5 只就收敛，40 只是安全余量，代价 0.27 秒。
CALENDAR_SAMPLE = 40

_CAL: dict[str, set[str]] = {}


def trading_days(daily_dir: Path = DAILY_DIR,
                 sample: int = CALENDAR_SAMPLE,
                 refresh: bool = False) -> set[str]:
    """交易日历 = 日线日期的**并集**（不是单只股票的日期集）。

    `known` 是**集合**，没有顺序、没有区间端点 —— 想知道覆盖到哪天用
    `max(trading_days())`。调用方需要区间语义时自己问，不要在这里加。
    """
    key = str(Path(daily_dir).resolve())
    if key in _CAL and not refresh:
        return _CAL[key]
    out: set[str] = set()
    for p in sorted(Path(daily_dir).glob("*.parquet"))[:sample]:
        try:
            d = pd.read_parquet(p, columns=["date"])["date"].astype(str)
        except Exception:            # noqa: BLE001 —— 坏文件不该让整本日历消失
            continue
        out |= set(d)
    _CAL[key] = out
    return out


# ── 「今天该不该出单」的三态判据 ─────────────────────────────────────

RUN_DAY_TRADING = "TRADING_DAY"
RUN_DAY_WEEKEND = "WEEKEND"
RUN_DAY_WORKDAY_NOT_IN_STORE = "WORKDAY_NOT_IN_STORE"

RUN_DAY_LABELS = {
    RUN_DAY_TRADING: "交易日，正常发布",
    RUN_DAY_WEEKEND: "周末，非交易日，不发布",
    RUN_DAY_WORKDAY_NOT_IN_STORE: (
        "工作日但仓里没有这一天的数据 ⇒ 拒绝发布。"
        "🚨 仓里**没有任何东西**能区分「法定节假日」与「采集失败」——"
        "在「今天不在库里」这一件事上两者逐位同形。"
        "两种误判的代价不对称："
        "节假日误报只是挡住一次本就不该有的交付（约一年 11 天，方向保守）；"
        "而采集失败若静默放过，就是**一个交付日凭空消失且零告警**。"
        "⇒ 按后者的代价取严：rc=2，必须有人来看。"
    ),
}


def classify_run_day(day: date, known: set[str]) -> str:
    """`day` 是 `datetime.date`；`known` 是 `trading_days()` 的返回值。

    **纯函数** —— 不读盘、不取缓存、不看当前时间 ⇒ 守卫能在 1 毫秒内问它、
    能对它打变异（判据埋在一条 20 分钟的流水线后面就等于没有守卫）。
    `known` **不许有默认值**：给了默认值，某次调用就会静悄悄地用上
    「另一个日历」而没有任何东西会喊。

    ## 判据为什么只能是这一句

    「不在 `known` 里」有两种，而它们**在本仓里同形**：
    法定节假日（仓里正确地没有这一天）与采集失败（仓里错误地没有这一天）。
    仓里没有交易所日历文件，也没有任何东西能把两者分开。
    ⇒ 唯一能做的是**按误判代价选方向**，并把「分不出来」这句话写在脸上。
    """
    if day.isoformat() in known:
        return RUN_DAY_TRADING
    if day.weekday() >= 5:
        return RUN_DAY_WEEKEND
    return RUN_DAY_WORKDAY_NOT_IN_STORE


__all__ = [
    "DAILY_DIR", "CALENDAR_SAMPLE", "trading_days",
    "RUN_DAY_TRADING", "RUN_DAY_WEEKEND", "RUN_DAY_WORKDAY_NOT_IN_STORE",
    "RUN_DAY_LABELS", "classify_run_day",
]
