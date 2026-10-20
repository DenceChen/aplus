"""交易日历的唯一真相源，以及「今天该不该出单」的判据。

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
而全市场是 7783 天（1994-09-28 → 2026-10-20）。
只看一只，上市前的日子会被误判成「没采集」。

并集里的**每一天都至少有一只票真的有数据** —— 这正是它能当锚的原因。

## 🚨 取样必须**无偏**且**可复现**（2026-10-20 修）

旧实现是 `sorted(glob(...))[:40]` —— **按代码排序取前 40 只** ⇒
样本只覆盖一段代码区间：实测那 40 只**全是 `000xxx`**（深市主板），
`002/300/600/688` **一只都不在里面**。
而**判词正是由这个样本的新鲜度决定的**：
若这 40 只恰好都没更新到今天 ⇒ 判「今天不在库里」；
若其中**有一只**更新了 ⇒ 判「今天在库里，正常发布」。

⇒ **判词取决于「哪 40 只排在前面」，而不是市场真相。**
实测（2026-10-20，仓内 5460 只，**每个数都能复现**）：
- 按代码前 **40** 只：**32/40** 已到 2026-10-20（80%）——
  而**按代码前 200 只全是 `000xxx`**（`000` 前缀有 458 只）⇒
  样本在**代码空间**上只覆盖 **13 个前缀里的 1 个**（仓里 `000` 前缀就有 458 只）；
- 全仓真实比例 = **5207/5460 = 95.37%**；
- 均匀取样（`random.sample`，4 个种子实测 37~39/40）≈ **95%**。

⇒ 也就是说：旧样本的「健康覆盖率」读数是 **80%**、均匀样本是 **95%**，
差 **15pp**（= 40 只里多 6~7 只永久停牌票，因为那 8 只全落在
`000001~000049` 这一段代码里）⇒ **同一个市场，两个不同的读数。**

⚠️ **本仓流传过一句「随机抽 40 只有 7 只到 2026-10-20」——本轮复现不出来**
（4 个种子实测 37/40、39/40、39/40、38/40，期望 38.15）
⇒ 那句话**不许**被抄进任何注释/文档（数字被照抄，而它在这里是错的）。

修法两条**同时**成立，缺一不可：

- **无偏**：按文件名的**稳定哈希**（md5）排序取前 N ⇒ 样本均匀铺在
  整个代码区间上（实测 n=200 覆盖 **10** 个代码前缀：
  `000/001/002/300/301/600/601/603/605/688`；仓里共 13 个前缀，
  漏掉的三个最小的 `003`(42 只)/`302`(1)/`689`(1)）。
- **可复现**：哈希是确定性的 ⇒ **每次运行取到的是同一批文件**。
  `random.sample` 做不到：同一天重跑两次可能给出两个不同的判词。

⚠️ **「5 只就够」那句旧结论已经被推翻了**（它是一条**化石**）：
并集的天数不再只由「宽度的 N」决定，还由**深度**决定 —— 见下一节。

## 🚨 日历的**历史深度**不许被取样悄悄削掉（2026-10-20 修）

仓里 5460 只里**只有 2 只**带 2019 年之前的历史（实测 2026-10-20）：
`000043.SZ` 起于 **1994-09-28**、`300114.SZ` 起于 **2010-08-27**，
其余 5458 只都是 2019-01-02 起。

⇒ 旧的「按代码前 40 只」**恰好**含 `000043.SZ`（它在第 36 位）
  ⇒ 日历曾有 **7783 天**（1994→2026）；
⇒ **纯哈希样本一只都不含** ⇒ 日历只剩 **1889 天**（2019→2026）。

⇒ 那是**静默退化**：没有任何东西会红，而「日历覆盖到哪一年」是
`guard_vintage.is_gap_day` 的**范围判据**（超出范围 ⇒ 假定为交易日 ⇒ 报缺口）。
⇒ 所以取样 = **宽度部分**（哈希，N 只）**∪ 深度部分**
（比样本更早历史的那些文件，由 parquet **元数据统计量**找出）。

⚠️ 代价是**实测**的，不是推的：深度部分要全仓扫一遍 parquet footer，
实测 **1.8~2.5 s / 5460 只**（只读统计量，不读数据页；逐只
`pd.read_parquet` 是 143 s）。⇒ `trading_days()` 冷启动实测 **3.39 s**，
此后按 `(目录, 样本量)` 缓存命中即 **0.5 ms**。

## 🚨 二值判据不够：要有**覆盖率**（2026-10-20 修）

`known` 是**并集** ⇒ **只要样本里有 1 只更新了，「今天在库里」就成立**
⇒ 面板只覆盖 1% 也会放行 ⇒ 出一张**残缺榜**，而**页面上一个字都不说**。

⇒ 新增 `store_coverage()`（无偏样本里 `max(date) >= day` 的股票占比）
  与第四态 `RUN_DAY_TRADING_DAY_PARTIAL`（工作日、仓里**部分**有今天的数据
  ⇒ **拒绝发布，但把覆盖率写出来**）。

⇒ `classify_run_day(day, known, coverage=None)`：
  `coverage=None` 时**行为与旧版逐位相同**（既有三态的语义一个字不改）；
  给了 `coverage` 就走**四态**判据（覆盖率取代「在不在库里」这个二值问题）。

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

## 🚨 缓存按 **(目录, 样本量)** 分键

首版只有一个全局槽位，于是 `trading_days(daily_dir=X)`
会返回由 `Y` 建出来的日历 —— 参数形同虚设，
而测试替身与真实目录会互相污染。
⚠️ `sample` 也必须进键：旧键只有目录 ⇒ `trading_days(d, sample=40)` 之后
再问 `trading_days(d, sample=200)` 会拿到**前者的缓存**（静默的错样本）。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

import pandas as pd

#: 仓内日线目录。日历从它的 parquet 里反推。
DAILY_DIR = Path(__file__).resolve().parent.parent / "data" / "master" / "daily"

#: 取样大小（**宽度**部分）。
#:
#: 实测代价（2026-10-20，本机，仓内 5460 只，取 3 次的最小值）：
#: | N | 读 `date` 一列 | 覆盖的代码前缀数 |
#: |---|---|---|
#: | 40（旧，`sorted` 取前 40） | **0.31 s** | **1**（全是 `000`） |
#: | 40（哈希） | 0.20 s | 9 |
#: | 200（本，哈希） | **1.03 s** | **10** |
#:
#: ⇒ 宽度从 40 提到 200 的**增量代价 ≈ 0.72 s**（约 3.3 倍读盘时间），
#: 换来的是样本从「一个板块」变成「十一个代码区间」。
#: ⚠️ 另有一次性的**深度**扫描（全仓 parquet footer，实测 **1.8~2.5 s**
#: （页缓存冷热），每进程一次、有缓存）—— 见「历史深度」那一节。
#: ⇒ 端到端：`trading_days()` 冷启动 **3.39 s**、命中缓存 **0.5 ms**。
#: ⚠️ **深度扫描是 O(全仓文件数)**（不是 O(样本)）⇒ 仓长到 5 万只时
#: 它会涨到 ~16 s。样本量只影响宽度那半。
CALENDAR_SAMPLE = 200

#: 实测的「**永久停牌/退市**」比例（2026-10-20，仓内 5460 只）：
#: 全仓 **253/5460 = 0.0463**（那些票的 `max(date)` 停在那之前，
#: 它们**永远不会**再更新）；本仓这个样本里是 **12/202 = 0.0594**
#: （含 2 只深度载体 `000043.SZ` / `300114.SZ`，它们也早已停止交易）。
#:
#: ⚠️ **这是覆盖率唯一合法的扣减项** —— 其余任何缺口都是缺陷。
CALIBRATED_STALE_FRACTION = 0.0594

#: 覆盖率阈值 = **1 − 2 × 上面那个比例** = **0.8812**。
#:
#: ## 为什么是「2 倍」（**事前定**在这里，不是看完数据再挑的）
#:
#: ① **健康日的实测覆盖率** = **190/202 = 0.9406**
#:    （2026-10-20 实测：样本 202 只里 12 只永久停牌 ⇒ 190 只有今天的数据）。
#:    全仓真实天花板 = (5460 − 253)/5460 = **0.9537**。
#: ② 阈值必须**低于**健康值。**唯一**能让健康日误报的漂移是
#:    「永久停牌票变多」⇒ 于是把阈值钉在**那条漂移线上**：
#:    `阈值 = 1 − 2 × 永久停牌比例` ⇒ 永久停牌比例**翻倍**（5.94% → 11.88%）
#:    之前，健康日**不会**误报。
#:    ⇒ 余量 = 0.9406 − 0.8812 = **5.9pp = 12 只**
#:    （对应全仓永久停牌票从 253 只涨到约 580 只，按实测退市节奏是**数年**，
#:    不是几周 ⇒ 不会变成「天天误报 ⇒ 告警疲劳 ⇒ 平台慢性停发」）。
#: ③ 阈值必须**远高于**真实失效形态：
#:    「只更新了 1 只票」⇒ 覆盖率 **0.005**（比阈值低 **176 倍**）；
#:    「只更新了一半市场」⇒ **0.50**；「只更新了三分之一」⇒ 0.33。
#: ④ **2 倍不是随手挑的**：它是**仍能挡住全部五个大板块整体失败的最大倍数**
#:    （实测样本逐前缀只数 002:40 / 600:36 / 300:35 / 688:28 / 603:26）：
#:    - 2× ⇒ 阈值 0.8812，这五个板块整体失败的覆盖率分别是
#:      0.802 / 0.822 / 0.827 / 0.861 / 0.871 ⇒ **全部 < 阈值 ⇒ 全部被挡**；
#:    - 3× ⇒ 阈值 0.8218，`600` 板块整体失败（0.822）就**挡不住**了。
#:    ⇒ 2× 是这个方向上的**边界**，不是拍脑袋。
#: ⑤ **过严的阈值比恒真的更危险**（CLAUDE.md 第 112 层）：定到 0.95 就会在
#:    健康日误报（今天实测 0.9406，余量只剩 0.9pp ≈ 2 只），
#:    而误报会把人推向「改本来正确的代码」。
#:
#: ## 阈值变了会怎样
#: - **调高** ⇒ 更容易判 `TRADING_DAY_PARTIAL` ⇒ 更容易**拒发**（更保守），
#:   但可能在健康日误报（挡住一个本该出的交付日；连续误报 = 平台慢性停发）。
#: - **调低** ⇒ 更容易判 `TRADING_DAY` ⇒ 更容易**放行**（更激进），
#:   ⇒ 残缺榜更容易进**追加式**前瞻底稿（写进去就洗不掉 ⇒ 这一侧更贵）。
#: ⇒ 所以它只能**往严的一侧**调，且**必须先重测健康日覆盖率**再改。
#: ⇒ ⚠️ **标定会过期**：本常量按 2026-10-20 的实测标定。
#:   当全仓永久停牌比例超过约 **9.3%**（≈ 2×）时，健康日会开始误报
#:   ⇒ 那时必须**重新标定**，而重新标定**必须先看数据**（不许凭感觉调）。
#:
#: ## 已知边界（**不假装覆盖**）
#: 覆盖率是一个**标量** ⇒ 它**看不见「缺失集中在一整个板块」**。
#: 上面的 ④ 只保证**五个大板块**整体失败被挡住；
#: `301`(15 只 ⇒ 0.926) 与 `000`(11 只 ⇒ 0.946) 整体失败**挡不住**。
#: ⇒ 要堵这个方向需要**逐前缀**判据（本轮未做：任务书要的是标量，
#:   而逐前缀需要另一个阈值族 ⇒ 那是**换判据臂**，得单独预注册）。
COVERAGE_MIN = 1.0 - 2.0 * CALIBRATED_STALE_FRACTION

_CAL: dict[tuple[str, int], "_Snapshot"] = {}


class _Snapshot(NamedTuple):
    """一次取样的结果。**日历与覆盖率必须来自同一次取样** ——
    否则「日历说在库里、覆盖率说不在」这种矛盾会从两个口径的缝隙里长出来。"""

    days: set[str]        # 日期并集
    last: dict[str, str]  # 文件 stem → 该文件里的最大日期（ISO 字符串）


class Coverage(NamedTuple):
    """覆盖率的三个量 —— **分子分母必须能一起报出来**。

    `coverage` 是结论；`hit`/`total` 是它的**口径**。
    只报 `coverage` 的话，读者无法判断「0.5」是「1/2 只」还是「95/200 只」。
    """

    coverage: float   # hit / total
    hit: int          # 分子：已经到 `day` 的股票数
    total: int        # 分母：样本里**成功读出 date 列**的股票数


# ── 取样 ──────────────────────────────────────────────────────────

def _hash_key(p: Path) -> tuple[str, str]:
    """稳定哈希键：文件名（去扩展名）的 md5。

    - **确定性**：同一份仓、同一个文件名 ⇒ 同一个键 ⇒ 样本每次运行完全相同。
    - **无偏**：md5 与代码前缀无关 ⇒ 样本均匀铺在整个代码区间上。

    ⚠️ 哈希的是 **stem**（`000043.SZ`）而不是整个文件名（含 `.parquet`），
    这样扩展名改了样本不变 —— 样本的身份是**那只票**，不是那个文件。
    """
    return (hashlib.md5(p.stem.encode("utf-8")).hexdigest(), p.stem)


def _breadth_sample(files: list[Path], sample: int) -> list[Path]:
    """**宽度**部分：按稳定哈希取前 `sample` 只。

    🚨 这里曾经是 `sorted(files)[:sample]` —— 按**代码**排序取前 N ⇒
    样本只覆盖一段代码区间（实测前 200 只**全是 `000xxx`**，
    因为仓里 `000` 前缀就有 458 只）⇒ 判词取决于「哪几只排在前面」。
    ⇒ 改成哈希排序：同样的 N，铺在**整个**代码区间上。
    """
    if sample >= len(files):
        return list(files)
    return sorted(files, key=_hash_key)[:sample]


def sample_paths(daily_dir: Path = DAILY_DIR,
                 sample: int = CALENDAR_SAMPLE) -> list[Path]:
    """**取样（宽度部分）** —— 无偏且可复现。

    公开出来是为了让「样本长什么样」这件事**可问**（CLAUDE.md 第 124 层：
    判据不可问就等于没有守卫）：守卫能在一毫秒内问它「你的样本跨了几个
    代码前缀」，也能对它打变异，而不必真的读一遍 parquet。

    ⚠️ 这**只是宽度部分**。真正被读进日历的还有「深度部分」
    （`_history_carriers`）—— 两者合起来才是 `_snapshot` 读的文件集。
    """
    return _breadth_sample(sorted(Path(daily_dir).glob("*.parquet")), sample)


def _file_min_date(p: Path) -> str | None:
    """parquet **元数据统计量**里的最早日期（ISO 字符串）；拿不到返回 `None`。

    只读 footer，不读数据页 ⇒ 全仓 5460 只实测 **2.52 s**
    （逐只 `pd.read_parquet` 是 143 s）。
    ⚠️ 元数据统计量与 `pd.read_parquet(columns=["date"])` 的 `min` 逐位一致
    （这条对拍已由 `tests/test_panel_end_date_single_point.py::_pool_lo` 做过）。
    """
    try:
        import pyarrow.parquet as pq
        md = pq.read_metadata(p)
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        if "date" not in names:
            return None
        i = names.index("date")
        best: str | None = None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(i).statistics
            if st is None or not st.has_min_max:
                continue
            v = st.min
            if isinstance(v, bytes):
                v = v.decode()
            v = str(v)[:10]
            if best is None or v < best:
                best = v
        return best
    except Exception:            # noqa: BLE001 —— 坏文件不该让整本日历消失
        return None


def _history_carriers(files: list[Path], floor: str) -> list[Path]:
    """**深度**部分：比 `floor` 更早的历史载体（把并集往前延的那些文件）。

    🚨 为什么必须有这一步：仓里 5460 只里**只有 2 只**带 2019 年之前的历史
    （`000043.SZ` 起于 1994-09-28、`300114.SZ` 起于 2010-08-27）。
    ⇒ 旧实现「按代码前 40 只」**恰好**含 `000043.SZ` ⇒ 日历曾有 **7783 天**；
    ⇒ **纯哈希样本一只都不含** ⇒ 日历只剩 **1889 天**。
    ⇒ 那是**静默退化**（没有任何东西会红），而日历覆盖到哪一年是
    `guard_vintage.is_gap_day` 的范围判据。
    ⇒ 所以显式把它们补回来 ⇒ **深度不再是取样的偶然**。
    """
    out: list[Path] = []
    for p in files:
        mn = _file_min_date(p)
        if mn is not None and mn < floor:
            out.append(p)
    return out


def _read_dates(files: list[Path]) -> tuple[set[str], dict[str, str]]:
    """读这批文件的 `date` 列 ⇒ (日期并集, 每只票的最大日期)。"""
    days: set[str] = set()
    last: dict[str, str] = {}
    for p in files:
        try:
            d = pd.read_parquet(p, columns=["date"])["date"].astype(str)
        except Exception:            # noqa: BLE001 —— 坏文件不该让整本日历消失
            continue
        if len(d) == 0:
            continue
        days |= set(d)
        last[p.stem] = d.max()
    return days, last


def _snapshot(daily_dir: Path, sample: int, refresh: bool = False) -> _Snapshot:
    key = (str(Path(daily_dir).resolve()), int(sample))
    if key in _CAL and not refresh:
        return _CAL[key]
    files = sorted(Path(daily_dir).glob("*.parquet"))
    breadth = _breadth_sample(files, sample)
    days, last = _read_dates(breadth)
    floor = min(days) if days else ""
    if floor:
        carriers = _history_carriers(files, floor)
        if carriers:
            d2, l2 = _read_dates(carriers)
            days |= d2
            last.update(l2)
    snap = _Snapshot(days=days, last=last)
    _CAL[key] = snap
    return snap


def trading_days(daily_dir: Path = DAILY_DIR,
                 sample: int = CALENDAR_SAMPLE,
                 refresh: bool = False) -> set[str]:
    """交易日历 = 日线日期的**并集**（不是单只股票的日期集）。

    `known` 是**集合**，没有顺序、没有区间端点 —— 想知道覆盖到哪天用
    `max(trading_days())`。调用方需要区间语义时自己问，不要在这里加。

    ⚠️ 返回的是**副本**：缓存里的那个集合不许被调用方改掉
    （改了就等于把后面所有人的日历改掉了，而那没有任何东西会报）。
    """
    return set(_snapshot(daily_dir, sample, refresh).days)


# ── 覆盖率：二值判据的替代品 ───────────────────────────────────────

def _iso_day(day: date | datetime | str) -> str:
    """`date` / `datetime` / 字符串 ⇒ `YYYY-MM-DD`。"""
    if isinstance(day, datetime):        # 🚨 datetime 是 date 的子类，先判它
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    return str(day)[:10]


def coverage_detail(day: date | datetime | str,
                    daily_dir: Path = DAILY_DIR,
                    sample: int = CALENDAR_SAMPLE,
                    refresh: bool = False) -> Coverage:
    """**无偏样本**里「已经有这一天数据」的股票占比。

    - **分子** `hit` = 样本里 `max(date) >= day` 的股票数
      （一只票只要有这一天及以后的任何一根 bar，就算它「有这一天的数据」——
      停牌在那天之前的票**不**算）。
    - **分母** `total` = 样本里**成功读出 `date` 列**的股票数
      （读坏了的文件**不进分母**：它们既不是「有数据」也不是「没数据」，
      而是「我们不知道」；而分母缩小会让覆盖率**变大** ⇒ 方向不安全）。
    - 样本为空（目录里一个 parquet 都读不出来）⇒ 覆盖率 **0.0**
      ⇒ 判「仓里完全没有今天的数据」⇒ **fail-closed**。

    ⚠️ 它**读盘**，所以它不是纯函数；纯的是 `classify_run_day`
    （覆盖率作为**参数**传进去，见下）。
    """
    day_s = _iso_day(day)
    snap = _snapshot(daily_dir, sample, refresh)
    total = len(snap.last)
    if total == 0:
        return Coverage(0.0, 0, 0)
    hit = sum(1 for v in snap.last.values() if v >= day_s)
    return Coverage(hit / total, hit, total)


def store_coverage(day: date | datetime | str,
                   daily_dir: Path = DAILY_DIR,
                   sample: int = CALENDAR_SAMPLE,
                   refresh: bool = False) -> float:
    """覆盖率（标量）。见 `coverage_detail` 的分子分母定义。"""
    return coverage_detail(day, daily_dir=daily_dir, sample=sample,
                            refresh=refresh).coverage


# ── 「今天该不该出单」的判据 ───────────────────────────────────────

RUN_DAY_TRADING = "TRADING_DAY"
RUN_DAY_WEEKEND = "WEEKEND"
RUN_DAY_WORKDAY_NOT_IN_STORE = "WORKDAY_NOT_IN_STORE"
#: 🚨 **新增的第四态**（2026-10-20）。既有三态的常量与语义**一个字没改**
#: （别的代码与测试在读它们）—— 这一态是**加**的。
RUN_DAY_TRADING_DAY_PARTIAL = "TRADING_DAY_PARTIAL"

RUN_DAY_LABELS = {
    RUN_DAY_TRADING: "交易日，正常发布",
    RUN_DAY_WEEKEND: "周末，非交易日，不发布",
    RUN_DAY_WORKDAY_NOT_IN_STORE: (
        "工作日但仓里没有这一天的数据 ⇒ 拒绝发布。"
        "🚨 仓里**没有任何东西**能区分「法定节假日」与「采集失败」——"
        "在「今天不在库里」这一件事上两者逐位同形。"
        "两种误判的代价不对称："
        "节假日误报只是挡住一次本不该有的交付（约一年 11 天，方向保守）；"
        "而采集失败若静默放过，就是**一个交付日凭空消失且零告警**。"
        "⇒ 按后者的代价取严：rc=2，必须有人来看。"
    ),
    RUN_DAY_TRADING_DAY_PARTIAL: (
        "工作日，但仓里**只有一部分**股票有这一天的数据（覆盖率低于阈值）"
        "⇒ **拒绝发布**。"
        "🚨 这一档是 2026-10-20 新增的：旧判据只问「今天在不在库里」，"
        "而那个集合是**并集** ⇒ **只要有一只**票更新了就算「在库里」"
        "⇒ 面板只覆盖 1% 也会放行，出一张**残缺榜**而页面上一个字都不说。"
        "⇒ 现在**拒绝发布，并且把覆盖率一起报出来** —— "
        "**覆盖率必须随判决一起出现**，否则这一档与「工作日不在库里」"
        "在页面上长得一模一样（而它们的成因完全不同）。"
        "两种误判的代价不对称：宁可少发一天（可重跑补上），"
        "也不要把一张残缺榜写进**追加式**前瞻底稿（写进去就洗不掉了）。"
    ),
}


def classify_run_day(day: date, known: set[str],
                     coverage: float | None = None) -> str:
    """`day` 是 `datetime.date`；`known` 是 `trading_days()` 的返回值。

    **纯函数** —— 不读盘、不取缓存、不看当前时间 ⇒ 守卫能在 1 毫秒内问它、
    能对它打变异（判据埋在一条 20 分钟的流水线后面就等于没有守卫）。
    `known` **不许有默认值**：给了默认值，某次调用就会静悄悄地用上
    「另一个日历」而没有任何东西会喊。

    ## `coverage` 是什么、为什么它是**参数**

    `coverage` = 无偏样本里「已经有这一天数据」的股票占比
    （`store_coverage()` 的返回值）。它是**参数**而不是函数内部现算的 ——
    现算就等于把判据埋进一次读盘里，纯函数性当场消失，
    而「纯函数能在 1 毫秒内被问、被变异」正是它唯一的存在理由。

    - `coverage=None` ⇒ **行为与旧版逐位相同**（既有三态一个字没改）。
    - 给了 `coverage` ⇒ 走**四态**判据，`known` **不参与判决**
      （覆盖率是更强的信号：它区分「今天在库里」与「今天**基本**在库里」）。

    ## 四态判据（给了 `coverage` 时）

    | 条件 | 判决 | 动作 |
    |---|---|---|
    | 周末 | `WEEKEND` | 不发布（本来就不出单） |
    | 覆盖率 ≥ `COVERAGE_MIN` | `TRADING_DAY` | 正常发布 |
    | 0 < 覆盖率 < `COVERAGE_MIN` | **`TRADING_DAY_PARTIAL`** | **拒绝发布 + 报出覆盖率** |
    | 覆盖率 = 0 | `WORKDAY_NOT_IN_STORE` | 拒绝发布（采集失败） |

    ⚠️ **两条路径只在两个格子上不同，而这两个格子必须钉在明处**：

    ① 「**周末却出现在仓里**」：旧路径（先问「在不在库里」）⇒ `TRADING_DAY`；
       新路径（先问「是不是周末」）⇒ `WEEKEND`。
       仓里真实交易日历**实测零个周末日期**（2026-10-20 实测 7783 天里
       周末日期 0 个）⇒ 那个格子在真实数据上是空的；
       新路径判成 `WEEKEND` 是**更安全的一侧**
       （2026-10-10/11 真的在周末出过单，那是本仓真实犯过的错）。
    ② 「`known` 说不在库里、但覆盖率说满格」：旧路径 ⇒ `WORKDAY_NOT_IN_STORE`；
       新路径 ⇒ `TRADING_DAY`。这是**故意的语义切换**（覆盖率取代二值问题）；
       在生产里**不可达** —— `trading_days` 与 `store_coverage` 走同一个
       `_snapshot` 缓存 ⇒ 两个口径永远同源、永远一致。

    （`tests/test_trading_calendar.py::test_the_two_paths_differ_on_exactly_the_two_documented_corners`
    把这两个格子钉住，其余格子两条路径必须**逐格相同**。）
    """
    if coverage is None:
        # ── 既有三态：一个字节都不改（别的代码与测试在读它们）──
        if day.isoformat() in known:
            return RUN_DAY_TRADING
        if day.weekday() >= 5:
            return RUN_DAY_WEEKEND
        return RUN_DAY_WORKDAY_NOT_IN_STORE
    # ── 带覆盖率的新判据 ──
    if day.weekday() >= 5:
        return RUN_DAY_WEEKEND
    if coverage >= COVERAGE_MIN:
        return RUN_DAY_TRADING
    if coverage > 0:
        return RUN_DAY_TRADING_DAY_PARTIAL
    return RUN_DAY_WORKDAY_NOT_IN_STORE


__all__ = [
    "DAILY_DIR", "CALENDAR_SAMPLE", "COVERAGE_MIN", "CALIBRATED_STALE_FRACTION",
    "trading_days", "sample_paths", "store_coverage", "coverage_detail", "Coverage",
    "RUN_DAY_TRADING", "RUN_DAY_WEEKEND", "RUN_DAY_WORKDAY_NOT_IN_STORE",
    "RUN_DAY_TRADING_DAY_PARTIAL",
    "RUN_DAY_LABELS", "classify_run_day",
]
