"""每日编排：增量数据 → 验证昨日预测 → 今日预测（10 涨 + 10 跌）→ 资产配置方案。

用法：
    .venv/bin/python scripts/run_daily.py                # 全流程（18:30 定时跑）
    .venv/bin/python scripts/run_daily.py --skip-update  # 跳过数据增量（回填进行中/调试用）
"""
import argparse
import json
import random
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.alloc import build_allocation
from aplus.pipeline import AllSourcesFailed, SyncPipeline
from aplus.predict import build_predictions, filter_universe
from aplus.reports import (
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_UPSTREAM_UNAVAILABLE,
    append_verification,
    build_run_status,
    current_signal_id,
    latest_prediction_date,
    load_predictions,
    load_verification_history,
    outcome_for_run_day,
    pending_prediction_dates,
    save_allocation,
    save_predictions,
)
from aplus.backtest import STOCK_COST_DEFAULT
from aplus.snapshot import build_cross_section
from aplus.sources import default_sources
from aplus.store import LocalStore
from aplus.trading_calendar import (
    RUN_DAY_LABELS, RUN_DAY_TRADING, RUN_DAY_WEEKEND,
    classify_run_day, coverage_detail, trading_days,
)
from aplus.verify import (
    UNJUDGEABLE_REASON,
    last_two_bars,
    mark_unjudgeable,
    rolling_stats,
    verify_predictions,
)
from aplus.write_lock import LockBusy, WriteLock

MARKET_SAMPLE = 300  # 市场平均收益的抽样规模（全量读盘太重，抽样足够稳定）
MARKET_MAX_DATE_SAMPLE = 300  # 市场级末日期的抽样规模（与上者同量级，单列避免被误改）

#: 仓根（`provenance.code_file_sha256(root, ...)` 要它把路径算成**仓相对**）。
ROOT = Path(__file__).resolve().parents[1]



def _load_names(data_root: Path) -> dict:
    """股票代码 → 中文名（来自 baostock 股票列表，本地缓存）。"""
    cache = Path(data_root) / "names.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        stock_list = default_sources()[0].fetch_stock_list()
        names = dict(zip(stock_list["code"], stock_list["name"]))
        cache.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        return names
    except Exception as exc:  # noqa: BLE001 — 名字缺失不阻塞预测
        print(f"[names] 股票列表获取失败（用代码代替名称）：{exc!r}")
        return {}


def _safe_last_date(df) -> str | None:
    """库里最后日期；读不出合法日期返回 `None`（= 这只票读坏了）。

    空表与全 NaN 列的 `.max()` 返回 `nan`（**float**），
    拿它去和 `'2026-10-12'` 比就是 `TypeError`。
    `scripts/daily_update.py::_last_date` 是同一判据的第二份实现——
    改一处记得改另一处，或干脆把两边都换成这一个。
    """
    if df is None or "date" not in getattr(df, "columns", []):
        return None
    last = df["date"].max()
    if not isinstance(last, str) or not last.strip():
        return None
    return last


# ── 步骤状态的三态分类（P0，2026-10-17）────────────────────────────────────
#
# 事故形状：`step_predict_and_allocate` 遇 `bootstrap.lock` 裸 `return`
# ⇒ `run_step` 记 `"ok"` ⇒ `_pipeline` 归约 `status="ok"`
# ⇒ `guard_daily.py:37` 打「已成功执行，无需补跑」，
# 而 D1 当日名单**一个字节都没落盘**。链条四环全部在位。
#
# ⚠️ 诚实标注：这是「地雷已埋」，不是「事故已发生」——
# `data/guard.log` 里 `跳过预测` 出现 2 次（10-13 / 10-14），
# 但 `pred_2026-10-09..10-16` 全部存在 ⇒ D1 迄今没有真的丢单。
# 那两天守卫之所以补跑，是因为 `status.date` 陈旧，不是被这个 ok 骗过。
#
# 🚨 为什么**不能**写成「`fn()` 有返回值就当状态」：
# `step_factor_audit` 返回真值三元组 `(len(bad), len(usable), bad)`
# ⇒ `steps["factor_audit"]` 会变成字符串 `"(0, 0, [])"`。
# 那是**静默故障**：状态字段变成元组字符串，`all(v == "ok")` 仍判 False，
# 看不出哪里错。⇒ 必须用**类型化异常**，返回值约定作废。

class StepSkipped(RuntimeError):
    """这一步**没交出它欠的交付物**，而原因不是崩溃。

    与「合法空转」的区别只有一条，且这条就是全部：
    `verify` 无待判定日 / `bond-paper` 等首个交易日 ——
    那两步把它们该做的事做完了（确认过确实没东西可做），`ok` 是**真的**；
    锁被占 / 库为空这类，交付物确实没落盘，`ok` 是**假的**。
    ⇒ 判据是「交付物有没有落」，不是「函数有没有正常返回」。
    """


def classify_step(exc: BaseException | None) -> str:
    """步骤状态字段的三态映射。**必须是可 import 的模块级纯函数**——
    `run_step` 是 `_pipeline` 内的闭包，判决逻辑若 import 不到，
    就没有任何东西能挡它（tests/test_run_daily_step_status.py）。
    """
    if exc is None:
        return "ok"
    if isinstance(exc, StepSkipped):
        return f"skipped: {exc}"
    return f"failed: {exc!r}"


def reduce_run_status(steps: dict[str, str]) -> str:
    """`steps` → 落进 `run_status.json` 的 `status`。**同样必须是可 import 的纯函数**。

    🚨 为什么要提出来：`all(v == "ok" ...)` 这一行原先**内联在 `_pipeline` 里**，
    而测试为了能问它，在**测试文件里重写了一遍**——
    ⇒ 那条守卫钉的是一份**副本**：生产那一份改成 `all(v.startswith("ok"))`
    （`"skipped: …".startswith("ok")` 为假，所以这一改还是绿的；
    改成 `all(v in ("ok", "skipped"))` 之类才会漏）时它照样全绿。
    ⇒ 「测试里重写生产逻辑」是第 96/137/192 层的同族：
    **证明的是「这个表达式在这个文件里成立」，不是「生产用的那个还对」。**

    ⚠️ 归约**只认精确的 `"ok"`**：`"skipped: …"` 与 `"failed: …"` 都让整轮变
    `partial`，而 `guard_daily.py` 的唯一成功判据就是 `status == "ok"`
    ⇒ 少交一样交付物 ⇒ 19:30 自动补跑。这条缝是本修复的接收面。
    """
    return "ok" if all(v == "ok" for v in steps.values()) else "partial"


# ── 采集源可用性（2026-10-21）──────────────────────────────────────
# 🚨 2026-10-20 实测**断供一整天**：000001.SZ 的拉取超时（akshare 打印
# 「服务器连接失败 / timed out / 接收数据异常」）⇒ 探针分支把它读成
# 「无新数据（非交易日或数据未就绪）」⇒ **全市场跳过更新** ⇒ 闸门判
# WORKDAY_NOT_IN_STORE ⇒ rc=2 ⇒ **当天没出单**（而当晚仓里其实已经有
# 5207 只的当日数据，只是没人再去拉一次）。
#
# ⇒ 三处一起改（**缺一处还是断供**）：
#   ① 探针**多票**（不许单票定全池）—— 形状 18/23 的采集版
#   ② 探针失败**重试**（网络抖动是瞬时的，实测 3 次里通常第 2 次就好）
#   ③ 「源抛异常」与「源返回空帧」**结构化**分开：
#      前者 = **数据源不可用** ⇒ rc=3（可重试，且要有人来看）
#      后者 = 今天没有这一天的数据 ⇒ 交给交易日闸去判「是不是交易日」
UPDATE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
UPDATE_NO_NEW_DATA = "no_new_data"
UPDATE_UP_TO_DATE = "up_to_date"
UPDATE_DONE = "updated"

#: 探针票的**候选数**。🚨 一只票的网络超时不该决定另外 5459 只的命运。
PROBE_CANDIDATES = 5
#: 探针重试次数与间隔（秒）。
PROBE_RETRIES = 3
PROBE_RETRY_SLEEP = 20.0


def _spread_probe_files(files, n=PROBE_CANDIDATES):
    """从池子里**分散**取 n 只探针票（保序、去重）。

    🚨 不取前 n 只：它们可能同属一个板块、同批上市、同一状态 ⇒
    一只挂了就整批挂 ⇒ 「分散」是这里唯一有意义的取法。
    """
    files = list(files)
    if len(files) <= n:
        return files
    idx = sorted({round(i * (len(files) - 1) / (n - 1)) for i in range(n)})
    return [files[i] for i in idx]


def _probe_market(pipeline, probes, today):
    """探针：**多票** + **重试** ⇒ `(status, detail, advanced_codes)`。

    `advanced_codes` = 探针里**真的把数据拉到今天**的那些票（`set`）。
    ⇒ 上层据此知道哪些票已经更新过，不必再拉一遍（也不必把它们算成已完成
    而其实没更新 —— 那正是「一只票定全池」的另一种形态）。

    🚨 **判据是「数据有没有前进」，不是「调用有没有抛异常」**：
    非交易日拉取会返回含旧日期的**非空**帧（≠ 有新数据）⇒ 必须实读推进后的末根。
    """
    advanced: set[str] = set()
    upstream_errors: list[str] = []
    for attempt in range(1, PROBE_RETRIES + 1):
        advanced = set()
        upstream_errors = []
        for path in probes:
            code = path.stem
            last = _safe_last_date(pd.read_parquet(path))
            if last is None:
                continue
            if last >= today:
                advanced.add(code)
                return UPDATE_UP_TO_DATE, f"{code} 已是最新（{last}）", advanced
            try:
                pipeline.sync_daily(code, last, today)
            except AllSourcesFailed as exc:
                if not exc.all_empty:
                    # 🚨 **结构化**区分：源抛了异常 ⇒ 数据源不可用 ⇒ 该重试
                    upstream_errors.append(f"{code}: {exc.errors}")
                continue
            except RuntimeError as exc:  # noqa: BLE001 — 其它形态的源失败
                upstream_errors.append(f"{code}: {exc!r}")
                continue
            new_last = _safe_last_date(pd.read_parquet(path))
            if new_last and new_last > last:
                advanced.add(code)
        if advanced:
            return UPDATE_DONE, f"{len(advanced)} 只探针票已前进", advanced
        if upstream_errors:
            if attempt < PROBE_RETRIES:
                print(f"[update] 探针数据源不可用（第 {attempt}/{PROBE_RETRIES} 次），"
                      f"{PROBE_RETRY_SLEEP:.0f}s 后重试：{upstream_errors[0][:140]}")
                time.sleep(PROBE_RETRY_SLEEP)
                continue
            return (UPDATE_UPSTREAM_UNAVAILABLE,
                    "；".join(upstream_errors)[:400], advanced)
        # 所有探针都返回空帧 ⇒ 今天没有这一天的数据（非交易日 / 上游未就绪）
        return (UPDATE_NO_NEW_DATA,
                f"{len(probes)} 只探针票全部无新数据", advanced)
    return UPDATE_UPSTREAM_UNAVAILABLE, "；".join(upstream_errors)[:400], advanced


def step_incremental_update(store, sources, today, data_root):
    # **真正持锁**，不是 exists() 预检查。这个函数自己要写 5460 个 parquet，
    # 而检查与写入之间隔着整个网络往返——回填在那个窗口里拿到锁并开始
    # 改写，两边就会同时 concat + to_parquet 同一批文件，后写覆盖先写，
    # 丢的列和行没有任何人会知道。而这条路径是 launchd 每天 18:30
    # 无人在看地跑的（tests/test_run_daily_write_lock.py）。
    lock = WriteLock(Path(data_root) / "bootstrap.lock")
    try:
        lock.acquire()
    except LockBusy as exc:
        msg = f"全市场回填进行中（{exc}），跳过增量以免并发打 baostock——明日自动恢复"
        print(f"[update] {msg}")
        # 🚨 不是裸 return：明天的增量没做，而裸 return 会被 run_step 记成
        # "ok" ⇒ status="ok" ⇒ 守卫打「已成功执行，无需补跑」。
        raise StepSkipped(msg)
    try:
        return _incremental_update_locked(store, sources, today)
    finally:
        lock.release()


def _incremental_update_locked(store, sources, today):
    """增量更新。返回**状态字符串**（`UPDATE_*` 之一）。

    🚨 **返回值不许省**：调用方要靠它区分「今天没数据」（交给交易日闸）
    与「**数据源不可用**」（该重试、且必须有人来看）——
    2026-10-20 断供就是这两者被压成一个 `RuntimeError` 造成的。
    """
    pipeline = SyncPipeline(sources, store)  # 短窗口严格覆盖
    daily_dir = store.root / "master" / "daily"
    files = sorted(daily_dir.glob("*.parquet"))
    if not files:
        print("[update] 主库为空，跳过")
        return UPDATE_NO_NEW_DATA

    # 探针：先更新**几只**参考股票——全都没新数据（非交易日/未到更新时间）
    # 则整体跳过，避免对 5546 只股票做无效拉取（2026-10-10 周六实测教训）。
    # 🚨 **多票**：2026-10-20 实测，一只票的网络超时把整个市场跳过了。
    probes = _spread_probe_files(files)
    status, detail, advanced = _probe_market(pipeline, probes, today)
    if status == UPDATE_UP_TO_DATE:
        print(f"[update] 参考股票已是最新（{detail}），跳过")
        return UPDATE_UP_TO_DATE
    if status == UPDATE_UPSTREAM_UNAVAILABLE:
        print(f"[update] 🚨 **数据源不可用**（不是「今天没这一天的数据」）：{detail}")
        return UPDATE_UPSTREAM_UNAVAILABLE
    if status == UPDATE_NO_NEW_DATA:
        print(f"[update] {detail}（非交易日或数据未就绪），整体跳过")
        return UPDATE_NO_NEW_DATA
    print(f"[update] 探针：{detail}")

    done = len(advanced)
    unreadable = 0
    for path in files:
        code = path.stem
        if code in advanced:
            continue  # 探针已经拉到今天，不必再拉
        # ── 单票隔离（2026-10-11 上午）──────────────────────────────
        # 2026-10-10 的生产日志：`[update] 步骤失败：TypeError("'>=' not
        # supported between instances of 'float' and 'str'")`，之后
        # **5460 只票一只都没更新**，而上层记成 `partial` 后继续出单，
        # 于是名单建立在陈旧数据上、且没有任何地方说明它是陈旧的。
        #
        # 读表与取 `last` 必须与同步一样在 `try` 里：一只票的空表或坏列
        # 不该让另外 5459 只原地不动。坏票要**计数上报**——
        # 静默跳过会让「数据全好」和「一半读不了」看起来一样。
        try:
            last = _safe_last_date(pd.read_parquet(path))
        except Exception as exc:  # noqa: BLE001
            last = None
            unreadable += 1
            if unreadable <= 5:
                print(f"[update] {code}: 读不出合法日期（{exc!r}），跳过")
        if last is None:
            continue
        if last >= today:
            continue
        try:
            pipeline.sync_daily(code, last, today)
            done += 1
        except RuntimeError:
            pass  # 全源失败：明日重试，不阻塞预测流程
    print(f"[update] 增量完成 {done}/{len(files)}")
    if unreadable:
        print(f"[update] ⚠️ {unreadable} 只股票读不出合法日期，**未更新**——"
              f"它们的末根仍是旧值，下游因子会在这些票上退化")
    return UPDATE_DONE


def _last_two_bars(store, code, after_date, factors_map=None):
    """委托给 aplus.verify.last_two_bars —— 后复权 + **锚定信号日**，口径集中一处。

    2026-10-11 之前这里直接取 `iloc[-1].close / iloc[-2].close`，除权日会记下
    一笔从未发生的亏损（实测 `002594.SZ` 2025-07-29：−66.94% vs 交易所 +0.37%）。

    2026-10-14 再修一层：连「最后两根」都是错的——必须锚定到 `after_date`，
    否则实盘量的是 D+1/D，而回测（`entry_lag=1`）量的是 D+2/D+1，**差一根 K 线**。
    实测同一个数字因此能从 20.0% 变成 70.6%。
    """
    return last_two_bars(store, code, factors_map=factors_map, after_date=after_date)


def _market_window(store, after_date, sample_size=MARKET_SAMPLE):
    """市场基准的**测量窗口 + 采样结果**。`_market_pct` 是它的薄封装。

    ★ 为什么必须把窗口**一起返回**（2026-10-14）

    原来的实现只返回一个数，于是「测的是哪两根 K 线」在下游完全不可见。
    实测后果：`verification.parquet` 里 2026-10-09 / 10-10 / 10-11 三天的
    `market_pct` **精确相同**（0.001196，`nunique()==1`）——
    三个不同的市场报出了同一个基准。

    根因不是「值碰巧一样」，是**退化**：
      - `random.Random(42)` + `sorted(codes)` ⇒ 每天抽中**同一批**股票；
      - `last_two_bars(..., after_date)` 取「信号日之后第一根」，
        早判时次日数据尚未落库 ⇒ 退化到当时能拿到的同一批 bar。
      实测 60 只样本中 **58 只**在三天里拿到逐位相同的 K 线。

    ⇒ 一旦 `nunique()==1`，`up_excess = actual_pct − market_pct`
    只是把收益平移了一个常量，**没有任何信息**。
    而 `market_pct` 被 11 个模块消费，其中包括
    **自进化主循环 `evolve_race.py:251`**——假基准污染的是判据本身。

    ★ 种子改为**由日期派生**：`Random(f"aplus-mkt-{after_date}")`。
    保留「同一日期重跑必得同值」的可复现性，
    同时让不同日期抽到不同样本 —— 这两件事**不矛盾**，
    原来的写法把它们当成了取舍，实际只是没写对。
    """
    import random as _random

    codes = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not codes:
        return None

    # ★ 种子含日期：同日期可复现，跨日期换样本
    rng = _random.Random(f"aplus-mkt-{after_date}")
    sample = [p.stem for p in rng.sample(codes, min(sample_size, len(codes)))]

    pcts, windows, n_unadjusted = [], set(), 0
    for code in sample:
        bar = _last_two_bars(store, code, after_date)
        if bar and bar["prev_close"] > 0:
            pcts.append(bar["close"] / bar["prev_close"] - 1)
            windows.add((bar.get("entry_date"), bar.get("exit_date")))
            if not bar.get("adjusted", False):
                n_unadjusted += 1
    if n_unadjusted:
        print(f"[verify] 市场基准 {n_unadjusted}/{len(pcts)} 只缺因子表，已降级为不复权口径")
    if not pcts:
        # ★ 缺档必须**响亮**，不能静默回落成 0.0（第 12 条同族）：
        #   0.0 在下游与「市场平盘」不可区分，会把超额凭空归零。
        print(f"[verify] ⚠️ 市场基准缺档：{after_date} 抽中 {len(sample)} 只全部无次日 bar。"
              f"**超额本轮不可计算**，禁止按 0.0 回落")
        return None

    return {
        "pct": float(sum(pcts) / len(pcts)),
        "n_used": len(pcts),
        "n_sampled": len(sample),
        "windows": sorted(w for w in windows if w[0] is not None),
    }


def _market_pct(store, after_date, sample_size=MARKET_SAMPLE):
    """市场基准（薄封装）。缺档返回 `None`，**不回落 0.0**。

    保留本函数名是因为 11 个消费方都在用它；语义变化（0.0 → None）
    本身就是修复的一部分，缺档必须能被下游看见。
    """
    w = _market_window(store, after_date, sample_size)
    return None if w is None else w["pct"]


def _store_max_date(store, sample_size=MARKET_MAX_DATE_SAMPLE) -> str | None:
    """库的市场级末日期（**抽样取最大**，不取均值、不看 mtime）。

    ## 为什么必须是「市场级」而不能取单只

    实测 600825.SH 的 daily 末日期停在 2026-10-12，而同日两只对照票是
    2026-10-15，抽样 300 只里绝大多数是 2026-10-15 ⇒ **库是当前的，
    是这只票停牌**。若用 `run_daily` 原本那条「取第 0 行探针」的路，
    一只停牌票就会被读成「整个库没更新」—— 两种形状的后果正好相反。

    ## 为什么取最大而不是均值

    判据问的是「库里有没有越过某一天」。均值会被少数刚停牌/刚退市的票
    拉回来，把「已经有数据了」误报成「还没到」。

    🚨 锚是 parquet 里的**日期**，不是文件 mtime（预注册 §3 的 C7）：
    mtime 会被拷贝、备份、重写但内容不变改掉，用它当锚是过严守卫的头号成因。

    ⚠️ 抽样而非全量：全量 5460 个 parquet 太重。300 只的**最大**值
    足以回答「库里有没有越过某一天」—— 只要有一只更新到就返回它。
    """
    codes = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not codes:
        return None
    # 种子固定：这是一个**不随日期变化**的事实（同一天重跑必得同值）。
    # 与 `_market_window` 的日期派生种子不同——那一条要的是跨日换样本，
    # 这一条要的是「同一天看的是同一批票」，否则「库里更新了没有」
    # 会在抽样噪声上抖动，而它是要写进产物当判据的。
    rng = random.Random("aplus-store-max")
    best = None
    for code in [p.stem for p in rng.sample(codes, min(sample_size, len(codes)))]:
        df = store.read_daily(code)
        if df is None or getattr(df, "empty", True) or "date" not in df.columns:
            continue
        d = str(df["date"].astype(str).max())
        if best is None or d > best:
            best = d
    return best


def _judge_one_day(store, reports_dir, pred_date, today):
    """判定单个预测日；数据未就绪返回 False（留给下一轮补判）。"""
    loaded = load_predictions(reports_dir, pred_date)
    if loaded is None:
        return False
    up, down = loaded
    preds = pd.concat([up, down], ignore_index=True)
    # 复权因子按需单只读（last_two_bars 内部懒加载），不预载全市场 5556 张表
    probe = _last_two_bars(store, preds["code"].iloc[0], pred_date)
    data_date = store.read_daily(probe["code"])["date"].max() if probe else None
    if data_date is None or data_date <= pred_date:
        print(f"[verify] 预测日 {pred_date} 的次日数据未就绪，留待下次补判")
        return False
    bars = pd.DataFrame(
        [b for b in (_last_two_bars(store, c, pred_date) for c in preds["code"]) if b])
    if bars.empty:
        print(f"[verify] 预测日 {pred_date} 无一只有可成交窗口，跳过")
        return False
    # 成本显式建模：pred 表没有 cost 列时用回测同一口径（0.003），
    # 并在报告里同时给毛值 `gross_pct`，不静默当 0。
    #
    # ★ `signal_id`（2026-10-21）：**带进**验证行。
    #   🚨 **既有 160 行不许回填**（追加式纪律）⇒ 只有**新行**带这个标签。
    #   旧行没有标签 ⇒ `aplus/signal_scope.py` 判「不可归属」⇒ 不可判定。
    results = verify_predictions(preds, bars, require_cost=True,
                                 default_cost=STOCK_COST_DEFAULT,
                                 signal_id=current_signal_id(reports_dir))
    results["pred_date"] = pred_date
    results["verified_on"] = today
    # ★ 基准的**测量窗口**一并落库（2026-10-14）。
    #   只有 `market_pct` 一个标量时，"三个不同市场报出同一个基准"
    #   （实测 10-09/10-10/10-11 精确等于 0.001196）在下游完全不可见。
    #   把 entry/exit 写进产物 ⇒ 退化当场可见，且可被守卫读出来。
    mkt = _market_window(store, pred_date)
    if mkt is None:
        print(f"[verify] {pred_date} 的市场基准缺档 ⇒ 该日**不写** market_pct，"
              f"避免 0.0 被读成『市场平盘』")
        results["market_pct"] = pd.NA
        results["market_n_used"] = 0
    else:
        results["market_pct"] = mkt["pct"]
        results["market_n_used"] = mkt["n_used"]
        win = f"{mkt['windows'][0][0]}→{mkt['windows'][-1][1]}" if mkt["windows"] else "?"
        print(f"[verify] 市场基准 {mkt['pct']:+.3%} "
              f"（{mkt['n_used']}/{mkt['n_sampled']} 只，窗口 {win}）")
    # 成交窗口必须落库：`pred_date` 不等于观测日（周末出名单时，
    # 周五/六/日三次出单落到同一个 10-12→10-13 窗口）。
    # 没有这两列，`daily_portfolio_returns` 会把同一天连乘三次，
    # 实测把 +0.67% 的真实日收益报成 −78.06% 的年化。
    results["entry_date"] = bars.set_index("code")["entry_date"].reindex(results["code"]).to_numpy()
    results["exit_date"] = bars.set_index("code")["exit_date"].reindex(results["code"]).to_numpy()

    # 🚨 永久不可判定的行必须**落一个标记**，否则停牌日会被无限重判
    #   （预注册 `PREREG_NO_DATA_REJUDGE_2026-10-16.md` §3 的 C4/C5）。
    #   `required_entry/required_exit` 取**本日实际用到的**成交窗口上界：
    #   它们是这一天的口径常量，不是某一只票的属性。
    required_entry = str(bars["entry_date"].max())
    required_exit = str(bars["exit_date"].max())
    store_max = _store_max_date(store)
    # 只对 `no_data` 行读库（命中/未命中行不需要），IO 与命中数无关。
    stock_max = {}
    for code in results.loc[results["result"] == "no_data", "code"]:
        df = store.read_daily(code)
        if df is not None and not getattr(df, "empty", True) and "date" in df.columns:
            stock_max[code] = str(df["date"].astype(str).max())
    results[UNJUDGEABLE_REASON] = mark_unjudgeable(
        results, stock_max, store_max, required_entry, required_exit)
    n_halt = int(results[UNJUDGEABLE_REASON].notna().sum())
    if n_halt:
        print(f"[verify] {pred_date} 有 {n_halt} 条**永久**不可判定（库已到 "
              f"{store_max} 而这些票停在窗口之前，按停牌处理、不再阻塞该日补判）")

    append_verification(reports_dir, results)
    hit = (results["result"] == "hit").sum()
    gross = results["actual_pct"].mean()
    # 净 = 毛 − 成本，只在 `rolling_stats` 里扣一次（契约见 verify.py）
    net = float((results["actual_pct"] - results["cost"]).mean())
    win = sorted(set(results["entry_date"].dropna()) | set(results["exit_date"].dropna()))
    win_txt = f"｜窗口 {win[0]}→{win[-1]}" if len(win) >= 2 else ""
    print(f"[verify] {pred_date} 的 {len(results)} 条预测判定完成："
          f"命中 {hit} 条｜均收益 毛 {gross:+.3%} / 净 {net:+.3%}"
          f"（已扣成本 {STOCK_COST_DEFAULT:.3%}）{win_txt}")
    return True


def step_verify_yesterday(store, reports_dir, today):
    """判定**所有**尚未判定的预测日（2026-10-14 修复，见 `pending_prediction_dates`）。

    原来只判「最新」那一次：5 个预测日 100 条推荐只入库 20 条，
    80% 的证据被静默丢弃，而且丢掉的恰好是命中率高的那天
    （2026-10-09 诚实口径 70.6% vs 留着的 10-12 只有 20.0%）。
    样本量因此从 n 天塌缩到 1 天。
    """
    pending = pending_prediction_dates(reports_dir)
    if not pending:
        print("[verify] 无待判定的预测日")
        # 🚨 这里**故意保留裸 return**，且 `ok` 在这一处是真的：
        # verify 的交付物是「把每一个待判定的预测日都判掉」，
        # 而它刚刚**确认过待判定集合为空** ⇒ 交付物已交（空集），
        # 不是「没交」。判据是「交付物有没有落」，不是「有没有 return」。
        # ⇒ 与 step_predict_and_allocate 的锁路径**语义相反**，
        #   那里是交付物真的没落，所以那里 raise。这里 raise 就是假话。
        return
    print(f"[verify] 待判定 {len(pending)} 个预测日：{pending[0]} ~ {pending[-1]}")
    done = 0
    for pred_date in pending:            # 从最早的补起——越早的证据越该先补
        if _judge_one_day(store, reports_dir, pred_date, today):
            done += 1
    print(f"[verify] 本轮补判完成 {done}/{len(pending)} 天")


def predict_block_reason(store_root) -> str | None:
    """出单前的阻断判据。**纯函数、模块级、可 import** ⇒ 可问、可变异。

    返回阻断原因字符串，或 `None` 表示「不阻断」。
    判别力**只**来自构造成员不同：锁在场 vs 不在场。
    空池 / 数据不全那些格子**不在这里判** —— 它们各有各的语义
    （例如空池意味着「池子建不起来」，与「此刻数据不可信」不是一件事），
    混进同一个判据会让对照臂分不清是哪一条在起作用。
    """
    # 预测只读不写，所以这里用「锁在不在」的预检查即可：读到一个正在被
    # 改写的文件会得到不可信的横截面，宁可跳过。但判定走同一份 WriteLock，
    # 免得「锁」这个词在两个地方指两件事。
    if WriteLock(Path(store_root) / "bootstrap.lock").path.exists():
        return ("全市场回填进行中（bootstrap.lock），跳过预测——"
                "数据文件正在改写，此刻的横截面不可信")
    return None


def signal_day_closes(store, codes, as_of: str) -> dict:
    """读出这些票在**信号日当天或之前最后一根 bar**的**不复权 close**。

    ## 为什么取价口径是「信号日 close」而不是「入场价」

    用户是**出单时刻**按名单下单 ⇒ 那一刻**看得见**的价是信号日收盘价。
    用入场价（>D 的第一根）判「买不买得起」= **用未来的信息做今天的决定**
    ⇒ 🚨 生产**必须**用信号日 close。

    ⚠️ **口径差必须写清**：`data/reports/deployed_d2_verdict.json` 的 T3 臂
    用的是**入场价** ⇒ **T3 的读数与生产口径不是同一个量**
    ⇒ 要得到生产口径的读数，**需要用信号日 close 重跑那一臂**。

    ## 缺价 ⇒ **没有这个键**（**不是 0**）

    编 0 会被读成「免费」⇒ 可买率虚高。缺价的票在
    `aplus/alloc.py::_classify_affordability` 里判成 `PRICE_MISSING`。

    ## 复用已有的取价实现

    `aplus/webapp.py::close_as_of`（主库 `master/daily/{code}.parquet` 的
    `date <= as_of` 最后一根 bar 的 `close`）⇒ **不重写第三份**
    （第 97 层：两份副本之间没有守卫）。
    """
    from aplus.webapp import close_as_of

    out = {}
    for code in codes:
        close, _bar_date = close_as_of(store, str(code), as_of)
        if close is not None:
            out[str(code)] = float(close)
    return out


def current_positions_from_state(prev) -> list | None:
    """从持仓状态取「**今天之前**手上那一份」，供调仓差异去比。

    ## 🚨 它读的是**钱包**，不是候选池（2026-10-21 改）

    `state["up"]` 是**候选池**（那份 10 只涨腿名单）——
    `aplus/holdings_entry.py` 的模块 docstring 逐字写着这一点，
    它靠 `up` 做「集合唯一匹配」找建仓日 ⇒ **`up` 的语义不能改**。
    ⇒ 钱包另有一列：`state["held_codes"]`（`aplus/live_rebalance.py::portfolio_codes`）。

    **旧实现拿候选池当钱包，实测代价**：2026-10-20 的 top10 里
    **6 只**因「1 手 > 每份金额 ¥5,000」被配置层剔除 ⇒
    调仓差异会算出「**卖 6 只**」——卖的是**从未持有过**的票。

    ## 返回 `None` 的含义（两件事必须分开）

    | 情形 | 返回 | 后果 |
    |---|---|---|
    | 状态读不出来 | `None` | 没有当前持仓 ⇒ 差异不可信 |
    | 状态在但**没有** `held_codes`（旧文件） | `None` | **同样不可信** |

    ⚠️ 旧文件那一格**不许回落到候选池**：回落看着「总有值」，
    但那是 10 只候选 ⇒ 报告会叫用户卖掉没买过的票。
    ⇒ **宁可说不知道**（`rebalance_source="none"`），
    也不能给一个**具体的错答案**。

    ## 名字从候选池借，但**只借名字**

    `up` 与 `up_names` 是两个平行列表。长度不符时**不配对**（只给代码）——
    错配的名字比没有名字更糟：它会让用户以为自己在看另一只票。
    """
    from aplus.live_rebalance import portfolio_codes

    codes = portfolio_codes(prev)
    if codes is None:
        return None
    pool = list((prev or {}).get("up") or [])
    names = list((prev or {}).get("up_names") or [])
    name_of = dict(zip(pool, names)) if len(pool) == len(names) else {}
    return [{"code": c, "name": name_of.get(c, "")} for c in codes]


def holdings_layer_view(due: bool, positions, held_codes) -> dict:
    """持仓层要的**钱包**（不是候选池）。

    ## 为什么提出来成模块级函数

    判决逻辑藏在 `step_predict_and_allocate` 里 ⇒ **没有任何东西能挡它**
    （第 43 层：import 不到的代码等于零守卫）。
    ⇒ 提成模块级 ⇒ 可 import、可测、可变异。

    ## 入参

    | 参数 | 含义 |
    |---|---|
    | `due` | 今天是不是调仓日 |
    | `positions` | **配置方案真正拿到钱**的那些（`plan.positions`） |
    | `held_codes` | 手上那批（钱包）；`None` = **读不出来** |

    ## 返回什么

    | 键 | 含义 |
    |---|---|
    | `buy` | 调仓日 = 今天**真正拿到钱**的那些（非调仓日为空） |
    | `held_codes` | 要落进 `state` 的那一份（`None` = 读不出来 ⇒ **不写**） |
    | `reliable` | 手上那批是否可读 |

    ## 🚨 `buy` 为什么不是「今天那 10 只」

    实测 2026-10-20 的 top10：**6 只**因「1 手 > 每份金额 ¥5,000」
    被配置层剔除 ⇒ 指令说「买入这 10 只」而配置方案只配得起 4 只
    ⇒ 用户照指令买，钱包就和平台测量的那个账户**完全不是一回事**。
    ⇒ `buy` 必须取自**配置方案真正拿到钱的那些**（`positions`），
    也就是**先算配置、再出指令**。
    """
    funded = [str(p.get("code")) for p in (positions or [])
              if isinstance(p, dict) and p.get("code")]
    if due:
        return {"buy": funded, "held_codes": funded, "reliable": True}
    return {"buy": [], "held_codes": held_codes,
            "reliable": held_codes is not None}


def step_predict_and_allocate(store, reports_dir, today, capital, names_map):
    """出当日预测与配置方案。

    **回填持锁时一律不出名单**：`LocalStore._upsert` 是 `to_parquet` 直写，
    不是原子替换——并发读可能读到写了一半的文件。与其让预测崩在一个
    半截 parquet 上，不如明确不发布，并在日志里说明原因。

    🚨 阻断走 `raise StepSkipped` 而不是裸 `return`：D1 当日名单没落盘，
    而裸 return 会被 `run_step` 记成 `"ok"` ⇒ `status="ok"`
    ⇒ `guard_daily.py:37` 打「已成功执行，无需补跑」。
    """
    reason = predict_block_reason(store.root)
    if reason:
        print(f"[predict] {reason}")
        raise StepSkipped(reason)

    from aplus.evolve import live_execution_status, load_weights
    from aplus.factor import composite_score
    from aplus.live_rebalance import (HELD_CODES_KEY, advance, instruction, is_due,
                                      load_state, portfolio_codes, save_state)

    cfg = load_weights(reports_dir)
    weights = cfg["weights"]                      # 自进化晋级结果自动生效
    execution = cfg.get("execution") or {}
    every = int(execution.get("rebalance_every", 1) or 1)
    buf = execution.get("buffer_multiple")

    prev = load_state(reports_dir)
    due = is_due(prev, every, buf)

    # 这份名单来自哪组权重？那组权重**被证伪了吗**？
    # 判决板（web）已经显示「现任已被证伪」，但每日名单是另一个出口——
    # 用户看的是名单，不一定会去看判决板。两处用**同一份判定**
    # （aplus/champion_health），不各写一套。
    try:
        from aplus.champion_health import champion_health
        health = champion_health(reports_dir, weights)
    except Exception as exc:  # noqa: BLE001
        health = {"champion_health": "unknown", "champion_falsified": False}
        print(f"[predict] 现任健康度读取失败（不拦名单）：{exc!r}")
    if health.get("champion_falsified"):
        print("\n" + "!" * 78)
        print("!!  这份名单来自一组**已被证伪**的权重")
        print(f"!!  {health.get('champion_note', '')}")
        print("!!  名单照发（每天 10 涨 10 跌是你的要求），但它不代表这套策略能赚钱。")
        print("!" * 78 + "\n")
    elif health.get("champion_health") not in ("unknown", None):
        print(f"[predict] 现任健康度：{health.get('champion_health')}"
              f"（{health.get('champion_windows', 0)} 段独立窗口）")

    # 「已被证伪」说的是**结果**（历史上亏了），这里说的是**原因**
    # （权重方向和实测预测力相反）。两者都要有：只有结果时用户
    # 不知道为什么，只有原因时又缺了「这事有多确定」的证据。
    #
    # 方向核对不出来时**什么都不打**——`unknown` 不是「无冲突」，
    # 打成「符号全部一致」就是一个从未做过的结论。
    try:
        from aplus.factor_ic import weight_sign_conflicts
        sign = weight_sign_conflicts(reports_dir, weights)
    except Exception as exc:  # noqa: BLE001
        sign = {"unknown": True, "note": f"符号核对不可用：{exc!r}"}
    if sign.get("conflicts"):
        print("\n" + "!" * 78)
        print(f"!!  这组权重的 {len(sign['conflicts'])} 个因子方向与实测预测力**相反**")
        print(f"!!  {sign.get('note', '')}")
        if sign.get("ic_window"):
            print(f"!!  IC 测量窗口：{sign['ic_window'][0]} ~ {sign['ic_window'][1]}"
                  f"（来源 {sign.get('report')}）")
        print("!" * 78 + "\n")

    # 实盘在执行什么配置？权重文件声明的频率/缓冲区与实盘能力对不上时，
    # **必须在用户看到的那份名单里说明**，而不是打一行日志了事。
    # 独立验证（2019-2023，纯反转）：日频净 −168.9%、月频净 +77.6%，
    # 两者的年成本差约 10 倍——测的是月频、做的是日频，方向都反了。
    exec_status = live_execution_status(reports_dir)
    if not exec_status["honored"]:
        print("\n" + "!" * 78)
        print("!!  执行方式不匹配 —— 这份名单不是权重文件声明的那个配置")
        print(f"!!  {exec_status['reason']}")
        print("!" * 78 + "\n")
        print(f"[predict] ⚠️ {exec_status['reason']}\n")
    else:
        print(f"[predict] 执行方式：{exec_status['reason']}")

    cross = build_cross_section(store, names_map=names_map)
    # ST 状态未知的股票：放行是对的（要求 isST==0 会砍掉 88% 的正常股票），
    # 但**有多少只处于这个状态必须出现在出单里**——缺数据不等于「不是 ST」，
    # 而降级写入会把 isST 削空且不留痕迹（2026-10-12 缺陷 24）。
    unknown_st: dict = {}
    # 陈旧透传与买不进的剔除计数必须**接进实盘**（2026-10-14 接线缺口）。
    #
    # `filter_universe` 的 `stale` / `unfillable` 是可选出参：不给就静默剔除。
    # 回测路径接了，实盘这条路径当时没接——于是实盘每天都在剔股票，
    # 而出单里看不出剔了什么。实测那 78 条陈旧记录 **100% 落在跌腿**，
    # 也就是它在凭空造看空信号，而这一点在实盘日志里完全不可见。
    stale_hits: dict = {}
    unfillable_hits: dict = {}
    universe = filter_universe(cross, unknown_st=unknown_st,
                               stale=stale_hits, unfillable=unfillable_hits)
    if stale_hits:
        print(f"[predict] ⚠️ 陈旧剔除 {len(stale_hits)} 只（末根 K 线早于全池最新"
              f"交易日，滞后再按 max_stale_days 复核）——时点不可比，名次无意义："
              f"{' '.join(list(stale_hits)[:6])}{' …' if len(stale_hits) > 6 else ''}")
    if unfillable_hits:
        print(f"[predict] ⚠️ 买不进剔除 {len(unfillable_hits)} 只（一字板 / 涨停收盘 /"
              f"停牌无成交）——推荐一只买不进的股票是无效输出，"
              f"塞进跌篮子等于把「买不到」当成「会跌」："
              f"{' '.join(list(unfillable_hits)[:6])}"
              f"{' …' if len(unfillable_hits) > 6 else ''}")
    if unknown_st:
        print(f"[predict] ⚠️ ST 状态未知 {len(unknown_st)} 只（末根 isST 缺失，"
              f"已按「不是 ST」放行）——数据补齐后此项应归零："
              f"{' '.join(list(unknown_st)[:6])}{' …' if len(unknown_st) > 6 else ''}")

    # ── 数据截止日必须出现在出单里（2026-10-11 上午）────────────────
    # 2026-10-10 的日志：update 步骤因 TypeError 整个失败（一只票的空表
    # 杀掉全市场 5460 只的循环），上层记成 `partial` 后**继续出单**，
    # 于是名单建立在陈旧数据上，而名单本身没有任何地方说明它是陈旧的。
    # `601607.SH mom20=903.72%` 就是那份陈旧数据里还带着复权因子污染的证据。
    #
    # 修数据之前，先让陈旧**看得见**：用户拿到一份名单，必须知道它的数据基准。
    cutoff = ""
    try:
        probe = store.read_daily("000001.SZ")
        last_bar = str(pd.to_datetime(probe["date"]).max().date())
        cutoff = last_bar
        stale_days = (pd.Timestamp(today) - pd.Timestamp(last_bar)).days
        if stale_days > 4:
            print("\n" + "!" * 78)
            print(f"!!  数据截至 {last_bar}，距预测日 {today} 已 {stale_days} 天")
            print("!!  这份名单建立在**陈旧数据**上——增量更新可能失败了。")
            print("!!  看 `data/run_daily.log` 里的 `[update]` 段。")
            print("!" * 78 + "\n")
        print(f"[predict] 数据截至 {last_bar}"
              + ("（陈旧，见上方警告）" if stale_days > 4 else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"[predict] 数据截止日读取失败（不影响出单）：{exc!r}")

    up, down = build_predictions(universe, top_n=10, bottom_n=10, weights=weights)

    # 新闻情绪覆盖层：仅对预测涨 Top 做消息面验证（任何故障不得阻塞主流程）
    try:
        from aplus.llm import default_llm_chain, load_secrets
        from aplus.news import MiniMaxSearch, news_overlay, search_api_key

        secrets = load_secrets(Path.home() / ".aplus" / "llm.json")
        search_key = search_api_key(secrets)
        if search_key:
            overlay = news_overlay(up.head(10), MiniMaxSearch(search_key),
                                   default_llm_chain())
            overlay = overlay.sort_values("adjusted_score", ascending=False).reset_index(drop=True)
            up = overlay.head(10)
            print("[news] 情绪覆盖完成：")
            for _, r in up.iterrows():
                sent = f"{r['news_sentiment']:+.1f}" if pd.notna(r["news_sentiment"]) else "  —"
                print(f"    {r['name']} 情绪 {sent} {r.get('news_reason') or ''}")
    except Exception as exc:  # noqa: BLE001
        print(f"[news] 覆盖层跳过：{exc!r}")
    # `weights` 必须传下去：预测文件要记下「自己是用哪组权重排的」。
    # 不传的话概览页拿到的是一份出处不可知的榜单——权重一改，
    # 屏幕上那张榜静默地变成过期榜单，而页面上一处说明都没有。
    #
    # ★ `signal_id`（2026-10-21）：记下「这份名单是用哪套**排名配置**排出来的」。
    #   ⚠️ 它**只含名单生成配置**（weights / top_n / bottom_n）⇒
    #   敞口 5→10 只与可买性过滤（配置层）**一个字都没改到它** ⇒
    #   指纹**逐位不变** ⇒ 前瞻底稿里的既有行**仍然属于当前信号**。
    _signal_id = current_signal_id(reports_dir)
    save_predictions(reports_dir, today, up, down, weights=weights,
                    signal_id=_signal_id)
    print(f"[predict] 信号指纹 {_signal_id}（名单生成配置：权重 + top_n/bottom_n）")

    # ── 持仓指令（2026-10-11 21:45）────────────────────────────────
    # **推荐照旧每天出**——用户要的是「每天 10 涨 10 跌」，那是排序产品。
    # 调仓频率管的是**交易**，两者不冲突。这里只补一段「今天该不该动手」。
    #
    # 缓冲区只在权重文件**显式声明**时才生效。默认不开启：
    # 缓冲把日频年成本从 16.91% 压到 0.57%，默认开启会静默改写所有历史结论。
    # 🚨 **钱包，不是候选池**（2026-10-21）：`prev["up"]` 是那份 10 只涨腿名单，
    # 而**真正拿到钱的**只有配置层留下来的那些（实测 10 只里 6 只被可买性剔掉）。
    # ⇒ 缓冲区判据与持仓指令都必须对着**钱包**算，否则「留着旧持仓」留的是候选池。
    # ⚠️ `None` = **读不出来**（与「空仓」是两件事，见 `instruction()`），
    #    **不许**回落到候选池（那等于凭空发明一批持仓）。
    held_up = portfolio_codes(prev)
    held_down = list((prev or {}).get("down") or [])
    trade_up, trade_down = list(up["code"]), list(down["code"])
    if due and buf and held_up is None:
        # 🚨 **缓冲区没生效，而且必须说出来**（2026-10-21 晚，复核点出）。
        # 缓冲区判据要拿「你手上那几只」去比今天的排名 ⇒ 钱包读不出来时
        # **无从比较** ⇒ 缓冲区不生效 ⇒ **换手会比平时大**（从「部分换」变「全换」）。
        # ⇒ 静默跳过会让人以为「今天也是按缓冲区换的」⇒ **这是一次隐形的换手变化**。
        # ⚠️ 当前生产**不触发**（`weights.json` 不存在 ⇒ `buffer_multiple=None`），
        #    但**一旦冠军带缓冲区上线就会咬人** ⇒ 提前说清。
        print(f"[predict] ⚠️ **缓冲区（{buf}×）没有生效**：手上那批**读不出来**"
              "（状态里没有「哪几只真的拿到了钱」这一列）⇒ 无从比较"
              "⇒ 今天的换手会比平时大。")
    if due and buf and held_up:
        # 沿用回测的缓冲区判据，不在这里另写一套（两套实现迟早漂移）
        from aplus.rebalance import buffer_hold, fill_to_top_n

        scored = universe.assign(_s=composite_score(universe, weights))
        scored = scored[scored["_s"].notna()].sort_values("_s", ascending=False)
        ranked_up = list(scored["code"])
        ranked_dn = list(reversed(ranked_up))
        trade_up = fill_to_top_n(buffer_hold(held_up, ranked_up, 10, buf), ranked_up, 10)
        trade_down = fill_to_top_n(buffer_hold(held_down, ranked_dn, 10, buf), ranked_dn, 10)
        kept = len(set(trade_up) & set(held_up))
        print(f"[predict] 缓冲区 {buf}×：{len(held_up)} 只旧持仓留下 {kept} 只，"
              f"换手从 {len(held_up) - kept} 只降下来")

    # 🚨 **持仓指令改到配置方案之后出**（2026-10-21）。
    # 以前指令在这里出，用的是 `trade_up`（今天那 10 只）⇒
    # 而配置层会把「1 手 > 每份金额 ¥5,000」的票剔掉（实测 6/10）⇒
    # 指令叫用户「买入这 10 只」，配置方案只配得起 4 只
    # ⇒ 钱包 ≠ 平台测量的账户。⇒ 顺序必须是「先算配置、再出指令」。

    # 计数只在状态机里算一份。`due` 为真时归零（含首次建仓与配置变更），
    # 否则由 `advance` 跨一个交易日 +1。
    #
    # 注意**不要**在调 advance 之前自己改 `days_since_rebalance`——
    # `advance` 是从旧值 +1 的，先覆盖再 advance 等于加了两次（2026-10-11 21:50）。
    by_code = universe.drop_duplicates("code").set_index("code")

    # ★★ 持仓**只在换仓日更新**（2026-10-14 修）。
    #
    # 原写法无条件写 `"up": trade_up`（当日新名单），于是非换仓日
    # 也把「我持有什么」改成了「今天该看什么」。后果不是持仓漂移，
    # 而是**缓冲区判据永远对着最新名单算**：
    #   `held_up = prev.get("up")` 拿到的始终是上一天的新名单，
    #   `buffer_hold(held_up, ranked_up, ...)` 于是等价于
    #   「拿今天的名单和今天的排名比」——缓冲区完全失效，
    #   而代码路径**不报任何错**。
    #
    # ⇒ 「每天出名单、N 天换一次仓」这个产品形态此前**没有被真正执行**：
    #   名单在发，缓冲在算，但持仓记忆是假的（第 6 条：开关没焊到事实上）。
    state = dict(prev or {})
    state["as_of"] = today
    state["every"] = every
    state["buffer_multiple"] = buf
    if due or not (prev or {}).get("up"):
        # 换仓日（含首次建仓）：更新持仓 + 名字
        state["up"] = trade_up
        state["down"] = trade_down
        state["up_names"] = [str(n) for n in by_code.reindex(trade_up)["name"].fillna("")]
        state["down_names"] = [str(n) for n in by_code.reindex(trade_down)["name"].fillna("")]
        state["held_since"] = today          # ★ 持仓是哪天定的 —— 审计与缓冲区校验都要它
        # ⚠️ **旧的钱包必须先作废**（2026-10-21）：换仓日结束后手上那批就换了，
        # 而「钱包」要等 `plan` 算完才有 ⇒ 这中间若崩在磁盘上，
        # 留着**旧**钱包比留「读不出来」更坏（后者会明说不知道）。
        # ⇒ 先置 `None`（`save_state` 遇 `None` **不写这一列** ⇒ 读出来是「不知道」）。
        state[HELD_CODES_KEY] = None
        # ⚠️ 这里**曾经**还有一段 `up_scores`，现已删除（2026-10-15）。
        # 它调 `composite_score(universe, weights)`，而 `universe` 是否带因子列
        # 取决于 `filter_universe` —— 测试里那个最小夹具只有 `code`/`name`，
        # 于是**这条分支第一次真被执行时当场 KeyError**。
        #
        # 删它不是因为它会崩，是因为**全仓没有任何东西读 `up_scores`**：
        # `buffer_hold(held, ranked, ...)` 比的是名次不是分数。
        # 为一个没人消费、还会让真实路径崩的字段付出代价，是纯负债。
        # ⇒ 记下它需要的东西（`held_since`）就够，别顺手多存。
    else:
        # 非换仓日：持仓**原封不动**。名单照发（每天 10 涨 10 跌是要求），
        # 但名次变化 ≠ 该换仓。
        #
        # ⚠️ `held_since` 现在**真的落盘**了（`save_state` 写它），
        #    所以这里打的是**建仓日**而不是 `?`。
        #    仍打 `?` ⇒ 说明这个状态是**旧格式**（没有这个键）⇒
        #    「建仓日未知」这件事必须打出来，不能用一个问号糊过去。
        held_since = state.get("held_since") or "未知"
        # 🚨 打的是**钱包**的只数，不是候选池的只数（2026-10-21）。
        # 候选池恒是 10 只 ⇒ 打它等于什么都没说；钱包才是「你手上几只」。
        _wallet = portfolio_codes(prev)
        _wallet_txt = (f"{len(_wallet)} 只" if _wallet is not None
                       else "**读不出来**（状态里没有「哪几只真的拿到了钱」这一列）")
        print(f"[predict] 距上次换仓 {state.get('days_since_rebalance', '?')}/"
              f"{every} 个交易日 —— 持仓沿用 {held_since} 的名单"
              f"（{_wallet_txt}），今日名单只作参考")

    if due:
        state["days_since_rebalance"] = 0
    else:
        state = advance(state, today)
    # ⚠️🚨 **这里不落盘**（2026-10-21 晚，改自「先落一次、算完再落第二次」）。
    #
    # **为什么改**：两次落盘之间有一个**窗口**——那窗口里盘上的状态是
    # 「已换仓（every/days/held_since 都更新了）**但钱包那一列还没有**」。
    # ⇒ 那个状态在**非调仓日**会让 `build_allocation` 的非调仓日分支
    #    把「钱包读不出来」塌成「**空仓**」⇒ 产物写「**0 只 · 100% 现金**」
    #    ⇒ **正是这条修复自己在注释里判为「错的方向是让人卖光」的那一侧**
    #    （而它会持续 62 个交易日 —— 下一次调仓日才恢复）。
    #
    # ⇒ **单次落盘（在配置方案算完之后）**：崩了就崩在「盘上什么都没变」上
    #    ⇒ 盘上仍是**昨天的钱包 + 昨天的计数** ⇒ 次日 `is_due` 仍为真
    #    ⇒ **自愈重算**（可能重复调仓一次，代价远小于「62 天说你该清仓」）。
    #
    # ⚠️ 守卫：`tests/test_holdings_layer_reads_the_portfolio.py`
    #    ＋ `tests/test_alloc_rebalance_diff.py`（非调仓日 + 钱包未知 ⇒
    #    **不许**塌成「0 只 / 100% 现金」）。

    print(f"[predict] 池子 {len(universe)} 只 → 涨 {len(up)} / 跌 {len(down)}")
    print("  预测涨 Top10:")
    for _, r in up.iterrows():
        print(f"    {r['code']} {r['name']} score={r['score']:.2f} mom20={r['mom20']:.2%}")
    print("  预测跌 Bottom10:")
    for _, r in down.iterrows():
        print(f"    {r['code']} {r['name']} score={r['score']:.2f} mom20={r['mom20']:.2%}")

    # ── 配置方案：**调仓日与非调仓日是两回事**（2026-10-21）──────────
    # 以前这里写的是 `build_allocation(up, capital=capital)` —— 没传
    # `current_positions` ⇒ `aplus/alloc.py` 里 `current_codes` 为空 ⇒
    # keep/remove **结构上不可能出现** ⇒ 产物里只有 5 条 add，
    # 界面于是只列「要买的 5 只」，**看不出该留哪只、该卖哪只**。
    #
    # ⚠️ 传的是 `prev`（**今天之前**手上那份），不是 `state["up"]`——
    #    后者在调仓日已经被写成今天的名单了，拿它比 ⇒ 永远全 add。
    #    `prev` 是 None（状态读不出来）⇒ 传 None ⇒ `rebalance_source="none"`
    #    ⇒ 差异**明说不可信**，而不是装成一份齐全的调仓表。
    #
    # 🚨 **`is_rebalance_day=due`**（2026-10-21）：非调仓日**不该出订单**
    # （实测：21 天里有 20 天，用户看到一张根本不会执行的卖买清单）
    # ⇒ 非调仓日 `positions` = **沿用的持仓**，`rebalance` = **空**。
    #
    # 🚨 **可买性过滤在配置层**（不是名单层）：`closes` 传进去 ⇒
    #    `aplus/alloc.py` 剔掉「1 手 > 每份金额」的票 ⇒ **不足就少给几只**
    #    ⇒ 发布的 10 涨 10 跌**仍然是全市场真正的前 10 / 后 10**
    #    ⇒ 前瞻底稿的 6 个窗口**不被作废**。
    _closes = signal_day_closes(store, list(up["code"]), today)
    plan = build_allocation(
        up, capital=capital,
        current_positions=current_positions_from_state(prev),
        is_rebalance_day=due,
        closes=_closes,
        held_since=(state.get("held_since") or None),
        days_since_rebalance=state.get("days_since_rebalance"),
        rebalance_every=every,
    )
    # ── 持仓层：**钱包**（2026-10-21）────────────────────────────────
    # `plan.positions` = 今天**真正拿到钱**的那些（可买性过滤之后）。
    # ⇒ ① 指令里的「买入这 N 只」= `plan.positions`（不是今天那 10 只候选）；
    #    ② `state["held_codes"]` = 钱包（**候选池另存一列**，`state["up"]` 不动）；
    #    ③ 调仓差异的「当前持仓」= 钱包（读不出来就明说不可信）。
    view = holdings_layer_view(due, plan.positions, held_up)
    if view["held_codes"] is not None:
        state[HELD_CODES_KEY] = view["held_codes"]
    save_state(reports_dir, state)

    instr = instruction(due, view["buy"], trade_down, every, buf,
                        held_up=held_up, held_down=held_down)
    if instr:
        print(instr)
    # 🚨 **闲置现金必须让用户看见**（2026-10-21）。
    # 当天有多少是现金，取决于当天候选里有几只配得起每份 ¥5,000 ——
    # **历史平均部署率 ~78.7%**（`deployed_d2_verdict.json` 的
    # `GRID['(n_positions x per_slot_budget)']`），
    # 但**单日可以低到 40%**（2026-10-20 那天 10 只里 6 只是科创板高价股）。
    # ⇒ 下面打的是**当天**的数，不打任何固定比例。
    # ⇒ 为什么必须打：指令只说「买入这 N 只」⇒ 用户可能以为满仓了，
    # 而闲置现金按 **0% 计息** ⇒ 它直接扣在账户年化上。
    if due and plan.cash_weight > 0:
        print(f"[alloc] ⚠️ **{plan.cash_weight:.0%} 的本金是现金**（配得起的只有 "
              f"{len(plan.positions)} 只）⇒ 闲置现金按 **0% 计息**，"
              f"它直接扣在账户年化上。")

    save_allocation(reports_dir, today, plan.to_dict())
    equity = sum(p["weight"] for p in plan.positions)
    # ⚠️🚨 **手上那批读不出来 ⇒ 不许打「0 只 · 100% 现金」**（2026-10-21 晚）。
    #   「读不出来」与「空仓」是两件事 ⇒ 打「0 只」会**叫人卖光**。
    if plan.holdings_known:
        print(f"[alloc] 配置：{len(plan.positions)} 只 × 10% = 权益 {equity:.0%}，现金 {plan.cash_weight:.0%}")
    else:
        print("[alloc] 配置：**手上那批读不出来**（状态里没有「哪几只真的拿到了钱」"
              "这一列）⇒ 本方案**不描述**你的持仓。"
              "⚠️ **不许**读成「空仓 / 该 100% 现金」⇒ 请以**券商账户**为准。")
    for p in plan.positions:
        print(f"    {p['code']} {p['name']} {p['amount']:.0f} 元")
    # 🚨 **剔除必须可见**（2026-10-21）：「平台每天在悄悄剔股票」不可见。
    _aff = plan.affordability
    if _aff:
        print(f"[alloc] 可买性过滤：{_aff['n_candidates']} 只候选 → 留 {_aff['n_kept']} 只"
              f"（B 类分配不够 {_aff['n_B_allocation']} · "
              f"A 类物理买不进 {_aff['n_A_physical']} · 缺价 {_aff['n_price_missing']}）")
        for _k, _lab in (("removed_B_allocation", "B 类（1 手 > 每份金额）"),
                         ("removed_A_physical", "A 类（1 手 > 全部本金）"),
                         ("removed_price_missing", "缺价（判不出）")):
            if _aff[_k]:
                print(f"    剔除 {_lab}：{' '.join(_aff[_k][:8])}"
                      f"{' …' if len(_aff[_k]) > 8 else ''}")
    if not due:
        # ⚠️ 同样分三态：钱包读不出来时**不许**说「沿用 X 的名单，0 只」
        _held_desc = (f"沿用 {plan.held_since or '建仓日未知'} 的名单，{len(plan.held)} 只"
                      if plan.holdings_known else
                      "**手上那批读不出来**（不是「0 只」⇒ 请以**券商账户**为准）")
        print(f"[alloc] ⚠️ **今天不是调仓日**（距上次换仓 "
              f"{plan.days_since_rebalance}/{every} 个交易日）⇒ "
              f"本方案**没有订单**，描述的是**当前持仓**（{_held_desc}）")
    _counts = {"keep": 0, "add": 0, "remove": 0}
    for _r in plan.rebalance:
        _counts[_r["action"]] = _counts.get(_r["action"], 0) + 1
    if plan.rebalance_source == "current_positions":
        print(f"[alloc] 调仓差异（相对当前持仓）：留 {_counts['keep']} · "
              f"买 {_counts['add']} · 卖 {_counts['remove']}")
        for _r in plan.rebalance:
            if _r["action"] == "remove":
                print(f"    卖 {_r['code']} {_r['name']}（不在新名单里）")
    else:
        print("[alloc] ⚠️ 调仓差异**不可信**：当前持仓读不出来 ⇒ "
              "只知道要买哪些，不知道该留哪只、该卖哪只")

    # ── 前瞻验证：每个交易日收盘后自动累积一次证据 ──────────────────
    # 为什么必须自动跑：前瞻窗口 2026-10-13 起算，K=63 需 189 个交易日
    # （约 9 个月）才够判决力。**不自动跑，窗口永远是 0 天**，
    # 而那是目前唯一干净的晋级路径——它需要时间，更需要每天都被记一笔。
    #
    # 失败不阻断名单：名单已经发出去才有意义。但必须**打出来**——
    # 静默失败会让「证据在攒」变成一句没人验证过的假设。
    try:
        from aplus.forward import forward_status
        fstatus = forward_status(reports_dir, today)
        ready = [c for c in fstatus.get("registrations_detail", [])[0].get("candidates", [])
                 if c.get("measurable")] if fstatus.get("registrations_detail") else []
        print(f"[forward] 前瞻台账 {fstatus.get('registrations', 0)} 份注册 · "
              f"{fstatus.get('n_candidates', 0)} 组候选 · "
              f"可判决 {len(ready)}/{fstatus.get('n_candidates', 0)} 组"
              f"（窗口需严格晚于注册日，K 日调仓需 3 个周期）")
    except Exception as exc:  # noqa: BLE001
        print(f"[forward] 前瞻状态读取失败（不影响今日名单）：{exc!r}")

    # 成本：每天出单没问题，**每天换手**是另一回事。
    # 5 万本金 / 10 只持仓时，日频换手的成本占毛超额中位数是 91%（97 组实测）。
    # 这个数必须每天可见——它决定「照单交易」是不是在给券商打工。
    # 测不出来时不打（`known=False`），因为 0 会被读成「成本不花什么钱」。
    try:
        from aplus.champion_health import live_cost_share
        cs = live_cost_share(reports_dir, every)
        if cs.get("known"):
            print(f"[cost] {cs['note']}")
    except Exception as exc:  # noqa: BLE001
        print(f"[cost] 成本占比读取失败（不影响今日名单）：{exc!r}")

    # 跌腿：产品承诺是「10 涨 10 跌」，这半个产品每天都推出去，
    # 却从来没被检查过——实测现任权重 avg_down_return=+0.126%/日（在涨）。
    # 测不出来时不打（known=False），因为 None 会被读成「跌腿不跌不涨」。
    try:
        from aplus.champion_health import live_down_leg
        dl = live_down_leg(reports_dir, every, weights, buf)
        if dl.get("known"):
            flag = " ⚠️ 承诺未兑现" if dl["avg_down_return"] >= 0 else ""
            print(f"[down] {dl['note']}{flag}")
    except Exception as exc:  # noqa: BLE001
        print(f"[down] 跌腿表现读取失败（不影响今日名单）：{exc!r}")

    # ── 交给下一步（持仓级降级预警）的上下文 ────────────────────────
    # ★ 为什么把**全池**交出去而不是让下一步自己再算一遍：
    #   `universe` 就是**生产刚刚发出去的那一份名单**所用的横截面
    #   （同一个 `build_cross_section` ⇒ 同一个 `aplus/panel.py::_factor_series`
    #   ⇒ 与生产逐位一致）⇒ 下一步**零额外面板成本**。
    #   ⚠️ 慢路径 `aplus/factor.py::compute_factors` **不是**生产实现
    #   （实测 vol20 差 0.0468 ⇒ 合成分差 0.57 ⇒ 连名次都变）⇒ 不许用。
    return {
        "universe": universe,
        "cross_section_size": int(len(cross)),
        "weights": dict(weights),
        "filtered": {
            "stale": len(stale_hits),
            "unfillable": len(unfillable_hits),
            "unknown_st": len(unknown_st),
        },
        "weights_meta": {
            "source": cfg.get("source"),
            "caveat": cfg.get("caveat"),
            "caveat_kind": cfg.get("caveat_kind"),
        },
    }


# ── 持仓级降级预警（2026-10-21 接线）────────────────────────────────
#: 产物 `holdings_alert_<信号日>.json` 的**生成器**登记值。
#:
#: ⚠️ `aplus/provenance.py::PATTERN_GENERATORS` 把这个文件名登记给
#:    **测量台** `scripts/measure_holdings_alert.py`（那是第一个产出者）。
#:    而**日频**的产出者是本文件 ⇒ 章里必须写**真正跑了的那份字节**
#:    （第 125 层：章说的必须是「这份产物是这些字节造的」，不是
#:    「写盘那一刻盘上恰好是这些字节」）。
#: ⇒ `provenance.freshness()` 因此改成「**比对对象取章自报的生成器**」
#:    （登记表仍然管「有没有人管这份产物」：表外 ⇒ `unknown`）。
HOLDINGS_ALERT_GENERATOR = "scripts/run_daily.py"

#: 产物里的「判据阶梯」与 `aplus/holdings_entry.py::RULE_ORDER` 同源，
#: 这里只放**给人看的出处**，字面量**不在这里复制**。
HOLDINGS_ALERT_RULE_ORDER_SOURCE = "aplus/holdings_entry.py::RULE_ORDER"


def step_holdings_alert(reports_dir, today, ctx):
    """持仓级降级预警：**用当天的全池**给持仓子集定级（每天自动出）。

    ## 它回答什么

    「我手上这几只里，哪几只已经掉到最差那一档？」——
    **只回答横截面位次**，不预测涨跌、不算盈亏、不改权重、不改 K。

    ## 三条口径纪律（预注册 `docs/PREREG_HOLDINGS_ALERT_2026-10-21.md`）

    ① **建仓分从建仓日那份预测文件读**（`pred_<建仓日>.parquet::score`）
       ⇒ **不重算建仓日的全池**（那要再付一次 GB 级代价）。
       持仓股票若**不在**建仓日那份名单里 ⇒ **没有建仓分**
       ⇒ 走第 4 态 `UNDECIDED`（「测不出 ≠ 没变差」），**不许**用 0 或当前分顶替。
    ② **持仓 = 建仓日那份配置里真正拿到钱的那些**（`alloc_<建仓日>::positions`）。
    ③ 缺建仓分 / 不在当日全池 ⇒ `UNDECIDED`。

    ## 成本：零额外面板

    全池就是 `predict` 步骤**已经在内存里**的那一份 ⇒ 本步骤只排序、定级。
    ⚠️ **不许**在这里再调一次 `build_cross_section`（GB 级 / 10 分钟量级），
    也不许用慢路径因子实现。

    ## 失败不许拖垮流水线，但不许静默成功

    抛异常 ⇒ `run_step` 记成 `failed: …` 并**继续**跑后面的步骤；
    ⇒ 但产物**不落盘**（半截 JSON 会被当成有效读数）。
    """
    from aplus import provenance
    from aplus.holdings_entry import (
        build_holdings_alert_artifact,
        production_parity,
        scored_universe,
    )
    from aplus.predict import build_predictions
    from aplus.reports import weights_fingerprint

    universe = ctx.get("universe")
    if universe is None or len(universe) == 0:
        raise StepSkipped("predict 步骤没有留下全池 ⇒ 本步骤无输入")
    weights = dict(ctx.get("weights") or {})
    w_hash = weights_fingerprint(weights)

    # ★ 第 125 层：跑的和盖章的必须是**同一份字节** ⇒ 开跑时抓一次。
    expect_bytes = provenance.code_file_sha256(ROOT, HOLDINGS_ALERT_GENERATOR)

    ranked = scored_universe(universe, weights)
    if len(ranked) == 0:
        raise StepSkipped("全池没有一只算出合成分 ⇒ 本步骤无输入")

    # ★ 生产对拍（外部锚）：本块要用的两腿必须与**生产落盘**的那份逐位相同。
    #   `build_predictions` 是生产自己的那一份（**两腿的取法也在它那里**）
    #   ⇒ 与落盘文件对拍 = 验「落盘那份没被换过、没被 `score_transform` 改过」。
    up_leg, down_leg = build_predictions(universe, top_n=10, bottom_n=10,
                                         weights=weights)
    parity = production_parity(reports_dir, today, up_leg, down_leg)
    if parity.get("verdict") != "MATCHES_PRODUCTION":
        raise RuntimeError(
            "🚨 持仓预警的两腿与生产落盘的名单对不上 ⇒ **拒绝出产物**"
            "（自己算自己不是证据，生产产物才是）：" + str(parity))

    artifact = build_holdings_alert_artifact(
        reports_dir=reports_dir,
        as_of=today,
        ranked=ranked,
        weights=weights,
        weights_hash=w_hash,
        generated_by=HOLDINGS_ALERT_GENERATOR,
        legs=(up_leg, down_leg),
        pool_method=(
            "当日全池**直接取自 run_daily 的 predict 步骤**（同一份 "
            "`build_cross_section` ⇒ 同一套 `aplus/panel.py::_factor_series` "
            "因子实现 ⇒ 与生产逐位一致）；**不建 GB 级面板、不重算全池**"),
        pool_method_note=(
            "⚠️ 慢路径 `aplus/factor.py::compute_factors` **不是**生产实现"
            "（测量台实测：vol20 差 0.0468 ⇒ 合成分差 0.57 ⇒ 涨腿 10 只里 "
            "3 只名次不同）⇒ 本路径**不许**用它"),
        pool_extra={
            "cross_section_size": ctx.get("cross_section_size"),
            "filtered": ctx.get("filtered"),
            "weights_source": (ctx.get("weights_meta") or {}).get("source"),
            "weights_caveat": (ctx.get("weights_meta") or {}).get("caveat"),
            "weights_caveat_kind": (ctx.get("weights_meta") or {}).get("caveat_kind"),
            "production_parity": parity,
        },
        extra={
            "daily_wiring": {
                "producer": HOLDINGS_ALERT_GENERATOR,
                "rule_order_source": HOLDINGS_ALERT_RULE_ORDER_SOURCE,
                "entry_scores_source": (
                    "建仓日那份预测文件的涨腿落盘分（**不重算建仓日的全池**）"),
                "cost": ("**零额外面板**：全池复用 predict 步骤已在内存的那一份；"
                         "本步骤只排序、定级、写一份 json"),
                "what_it_does_not_do": (
                    "**不产生收益**：不改权重、不改 K、不生成买卖单、"
                    "不预测涨跌、不算盈亏"),
            },
        },
    )

    out_path = Path(reports_dir) / ("holdings_alert_%s.json" % today)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        out_path, HOLDINGS_ALERT_GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        data_root=Path(reports_dir).parent,
        extra={
            "as_of": today,
            "weights_hash": w_hash,
            "pool_size": artifact["pool"]["size"],
            "entry_day": artifact["entry"]["entry_day"],
            "held_codes": artifact["population"]["codes"],
            "summary": artifact["summary"],
            "production_parity_verdict": parity.get("verdict"),            # ★ 章只覆盖**生成器自己**的字节 ⇒ 把**算判决那个模块**的字节
            #   也写进去（`aplus/holdings_entry.py` 与 `aplus/holdings_alert.py`），
            #   否则「改了判据逻辑但没重跑」这件事在章里**看不见**（第 125 层）。
            "code_file_sha256_holdings_entry":
                provenance.code_file_sha256(ROOT, "aplus/holdings_entry.py"),
            "code_file_sha256_holdings_alert":
                provenance.code_file_sha256(ROOT, "aplus/holdings_alert.py"),
        })

    summary = artifact["summary"]
    print("[holdings-alert] 信号日 " + str(today) + "｜全池 "
          + str(artifact["pool"]["size"]) + " 只｜持仓 "
          + str(artifact["population"]["n_heldings"]) + " 只"
          + "｜建仓日 " + str(artifact["entry"]["entry_day"])
          + "｜REDUCE " + str(summary.get("REDUCE"))
          + " / WATCH " + str(summary.get("WATCH"))
          + " / HOLD " + str(summary.get("HOLD"))
          + " / UNDECIDED " + str(summary.get("UNDECIDED")), flush=True)
    for item in artifact["items"]:
        print("  " + str(item["code"]) + " " + str(item["name"])
              + "｜" + str(item["action"]) + "｜" + str(item["action_criterion"]),
              flush=True)
    print("[holdings-alert] 产物 " + str(out_path), flush=True)


CORRUPT_GATE_RATIO = 0.01    # 损坏率分级阈值：超过即需要全量修复
# 单只损坏文件的中和额度。超过这个数就当作**系统性**故障停机。
#
# ## 为什么不是「1 只就停」
#
# 2026-10-11 上午闸门按「1 只即停」上线，立刻被 `001872.SZ`/`600018.SH`
# 两只借壳股顶住（首事件因子本就 >1、但基准覆盖了全部日线，属误报），
# 结果天天出不了名单。**一个天天误报的闸门等于没有闸门**——只会被绕过或忽略。
#
# 现在敢放宽，是因为损坏已在**两层数据闸**上被中和：
# 1. `adjust.attach_factor`：基准未知的时期作废（UNKNOWN_SCALE_MIN_FACTOR）
# 2. `MAX_PLAUSIBLE_RETURN`：±40% 之外的收益作废 → mom20 变 NaN → 该股退出横截面
#
# 实测 `302132.SZ`（+462.51% 虚高收益）正是这样被中和的：它退出榜单，
# 而不是霸占榜首。5 只以内不改变横截面性质；超过 5 只说明取数链路整体故障，
# 没有任何单点闸门能兜住。
CORRUPT_GATE_MAX_BAD = 5


def _run_audit(data_root, run_step, steps: dict) -> dict:
    """跑因子体检并把结果收回成字典。

    体检**自身失败**也必须当「挡」处理：`factor_gate_blocks` 收到的是
    「拿不到体检结论」，此时若放行，等于把「不知道数据好不好」当成「数据好」。
    """
    box: dict = {"bad": 0, "usable": 0, "error": None}

    def _go():
        bad, usable, _codes = step_factor_audit(data_root)
        box["bad"], box["usable"] = int(bad), int(usable)

    run_step("factor_audit", _go)
    if steps.get("factor_audit", "ok") != "ok":
        box["error"] = steps["factor_audit"]
        box["bad"] = max(box["bad"], 1)      # 体检失败按「至少有一只坏」处理
    return box


def factor_gate_blocks(bad_count: int, total_usable: int) -> bool:
    """因子表损坏达到**系统性**量级时，是否禁止发布推荐。

    ## 为什么按数量而不是比例

    比例在分母接近 0 时会失真（`0/0` 若按「无损坏」处理，等于把
    「数据层整个空了」读成「一切正常」）。数量是绝对值，不受分母影响。

    ## 为什么不是「1 只就停」

    见 `CORRUPT_GATE_MAX_BAD`：单只损坏已被两层数据闸中和，为它封掉整个平台
    会让闸门失去信誉。当天的教训是反方向的——`mom20=+903.72%` 进入用户建仓清单
    **确实发生过**，所以中和层必须先到位（tests/test_implausible_return.py、
    tests/test_unknown_scale.py），闸门才可以放宽。

    `CORRUPT_GATE_RATIO` 仍然有用，但它是**日志分级**用的
    （是否需要触发全量修复），不参与放行判断——两个概念不能混成一个。
    """
    if total_usable <= 0:
        return True
    return int(bad_count) > CORRUPT_GATE_MAX_BAD


def step_factor_audit(data_root):
    """复权因子表体检——补上交叉校验层的盲区。

    OHLCV 有双源交叉校验，复权因子**没有第二源**且会被向前填充，是整个数据层
    最容易出事、却最无人看管的字段。2026-10-11 生产事故（因子污染 → mom20
    +903.72% → 该股进入用户当日配置）证明它必须有独立的守门人。

    返回 `(bad_count, usable_count, bad_codes)`，供流水线决定**要不要发预测**。
    详见 `factor_gate_blocks` 的理由：宁可不出名单，也不出一份基于坏数据的名单。
    """
    from aplus.adjust import (classify_factor_files, is_factor_corrupt,
                              is_scale_neutralized)
    from aplus.log import get_logger

    usable, orphans = classify_factor_files(data_root)
    if not usable:
        print("[factor-audit] 无可用因子表，闸门关闭")
        log = get_logger("daily")
        log.event("step_failed", level="error",
                  detail="因子表目录为空——数据层未初始化，拒绝发布推荐")
        return 0, 0, []
    # 日线首根日期：让「借壳上市（首事件因子本就 >1）」与「早期事件丢失」
    # 能被区分开。缺了它，那 2 只借壳股会被误判成损坏，
    # 而闸门不按比例放行 → 每天都出不了名单（详见 tests/test_factor_gate.py）。
    first_dates = _daily_first_dates(data_root, usable)
    bad = []          # 中和不了 → 闸门拦截
    neutralized = []  # 已被 attach_factor 作废中和 → 报备但不拦
    for code in usable:
        try:
            fdf = store_factor(data_root, code)
            first = first_dates.get(code)
            if not is_factor_corrupt(fdf, first_daily_date=first):
                continue
            if is_scale_neutralized(fdf, first_daily_date=first):
                neutralized.append(code)
            else:
                bad.append(code)
        except Exception:  # noqa: BLE001 — 坏文件也算损坏
            bad.append(code)
    ratio = len(bad) / len(usable)
    log = get_logger("daily")
    # 孤儿因子表单列：它们没有日线、从不参与横截面，坏不坏都不影响预测。
    # 把它们算进损坏率会让一个不影响结果的问题反复触发全量修复（1835 秒）。
    if orphans:
        log.event("orphan_factor_files", level="info",
                  count=len(orphans), sample=" ".join(orphans[:5]),
                  detail=(f"{len(orphans)} 只因子表没有对应日线（孤儿），"
                          f"不参与横截面，未计入损坏率"))
    # 级别按**是否达闸门阈值**分档，而不是「有没有坏文件」。
    # 每天为一只已中和的表打 error，会让 healthcheck 的「最近的 error」永远
    # 非空，真正的错误反而被淹没——告警一旦变成背景噪音就不再是告警。
    systemic = factor_gate_blocks(len(bad), len(usable))
    log.event("factor_audit",
              level="error" if (systemic and bad) else ("warn" if bad else "info"),
              bad=len(bad), usable=len(usable), ratio=round(ratio, 6),
              neutralized=len(neutralized), blocks=systemic)
    if neutralized:
        # 这些表确实不完整，但 `attach_factor` 已把「基准未知」那段作废，
        # 它们不会再编造收益，也不会进榜。报备但不拦——为 0.04% 已经被
        # 数据层处理干净的表封掉整个平台，只会让闸门失去信誉。
        print(f"[factor-audit] 另有 {len(neutralized)} 只早期基准未知"
              f"（已自动作废该段，不影响榜单）：{' '.join(neutralized[:5])}")
    if bad:
        sample = " ".join(bad[:5])
        if ratio > CORRUPT_GATE_RATIO:
            print(f"[factor-audit] ⚠️ 损坏 {len(bad)}/{len(usable)}（{ratio:.1%}）"
                  f" 样例 {sample} → 需跑 repair_factors.py --all")
        else:
            print(f"[factor-audit] 损坏 {len(bad)}/{len(usable)}（{ratio:.2%}）"
                  f" 样例 {sample} → 未达系统性阈值，不拦推荐"
                  f"（这些股票已被 attach_factor 作废 + MAX_PLAUSIBLE_RETURN 双重中和）")
    else:
        print(f"[factor-audit] {len(usable)} 只可用因子表全部健康"
              + (f"（另有 {len(orphans)} 只孤儿未计入）" if orphans else ""))
    return len(bad), len(usable), bad


def _daily_first_dates(data_root, codes: list) -> dict:
    """{code: 日线首根日期}。

    只读 parquet 的 `date` 一列（列投影），不加载整张表——体检要跑全部 5447 只，
    整表读进来是分钟级的浪费。读不到的代码不进字典，
    `is_factor_corrupt` 会退回更保守的旧判据（宁可错杀）。
    """
    import pandas as _pd

    daily_dir = Path(data_root) / "master" / "daily"
    out: dict = {}
    for code in codes:
        path = daily_dir / f"{code}.parquet"
        if not path.exists():
            continue
        try:
            dates = _pd.read_parquet(path, columns=["date"])["date"]
            if len(dates):
                out[code] = str(dates.min())[:10]
        except Exception:  # noqa: BLE001 — 读不到就当没有，交给保守分支
            continue
    return out


def store_factor(data_root, code):
    from aplus.store import LocalStore
    return LocalStore(data_root).read_factor(code)


def step_stats(reports_dir, today):
    """统计**当前信号**的判定结果。

    ## 🚨 W（独立成交窗口数）必须**按信号分桶**（2026-10-21）

    前端解锁命中率报告用的判据是 `stats.json::n_independent_windows`，
    而底稿 `verification.parquet` 是**追加式**的 ⇒ 同一本底稿里会装着
    **两个信号**产生的行（可买性过滤改了名单生成配置之后）。

    ⇒ 若 `stats.json` 报的是**整本底稿**的 W，而 D1 闸门报的是
    **当前信号**的 W ⇒ **两者打架**：前端解锁了，闸门说不可判定。

    ⇒ **两者必须同时从「不可判定」起步** ⇒ `stats.json` 的 W 也走
    **同一个**作用域入口（`aplus/signal_scope.py::scope_ledger`），
    **不许**在 `aplus/` 侧再写一遍过滤（第 97 层：两份副本之间没有守卫）。

    🚨 **不许**为了让 W 看起来正常而**回落成整本底稿**（第 6 种形状：
    假装能调）⇒ `status != OK` 时 W **必为 0** 且带上「不可判定」标签。
    """
    history = load_verification_history(reports_dir)

    # 作用域解析：**唯一入口**是 `aplus/signal_scope.py`（不重写过滤）。
    # ⚠️ 用 `resolve_attribution_context`（两个基准都算）而不是单算一个
    # ⇒ 底稿只带得动另一个基准时（老底稿没有 `signal_id` 列）
    # **换基准**而不是回落。
    from aplus.signal_scope import resolve_attribution_context, scope_block, scope_ledger

    ctx = resolve_attribution_context(reports_dir)
    scoped = scope_ledger(history, ctx.signal_id, ctx.weights_fingerprint)

    # 🚨 归属不对 ⇒ **空帧** ⇒ 统计数全 0（W = 0）⇒
    #    前端**解不开**命中率报告 ⇒ 与闸门「不可判定」**同时起步**。
    stats = rolling_stats(scoped.frame)
    stats["as_of"] = today
    stats["signal_scope"] = scope_block(scoped)
    stats["n_independent_windows_of_ledger"] = int(
        len(history.drop_duplicates(subset=["entry_date", "exit_date"]))
    ) if {"entry_date", "exit_date"} <= set(history.columns) else None
    if ctx.signal_id_error:
        stats["signal_scope"]["signal_id_error"] = ctx.signal_id_error
    if ctx.weights_fingerprint_error:
        stats["signal_scope"]["weights_fingerprint_error"] = ctx.weights_fingerprint_error

    path = Path(reports_dir) / "stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    if not scoped.usable:
        print(f"[stats] 🚨 **不可判定（{scoped.status}）**：{scoped.reason}")
        print("[stats] ⇒ 本文件的 W = 0（**不回落成整本底稿**），"
              "前端与 D1 闸门**同时**从「不可判定」起步。")
        return
    if len(scoped.frame):
        # ★ 两个数都要报：条目数是**信息量**，独立窗口数才是**样本量**。
        #   周末出单会让多个信号日落进同一个成交窗口
        #   （实测 3 个信号日 → 1 笔），只报条目数等于把一次交易数三遍。
        win = stats.get("n_independent_windows")
        win_txt = f"{win} 次独立成交" if win else "独立成交次数未知（缺 entry/exit）"
        print(f"[stats] 当前信号 {scoped.current_signal_id or '—'}"
              f"（归属基准 {scoped.attribution_basis}）："
              f"{len(scoped.frame)} 条判定 / {win_txt}："
              f"总命中 {stats['overall_hit_rate']:.1%}，"
              f"涨命中 {stats['hit_rate_up']:.1%}，超额 {stats['up_excess_vs_market']:+.2%}")
    else:
        print("[stats] 暂无验证历史（首个预测日）")


# ---------- 可转债纸面账户（路线 A 第四阶段） ----------

PAPER_STATE_NOTE = (
    "start 即调仓网格的相位锚点，一经创建永不修改。第三阶段回测已披露：相位偏移 "
    "0~4 的净年化区间 +6.8%~+17.1%，锚点 offset=0 恰是最幸运相位——**任何在看到"
    "样本外结果之后再调整 start 的行为都是自欺**。调参请另开账户，不要改这里。"
)


def step_bond_update(data_root):
    """转债日线增量（探针判定非交易日，失败不阻塞主流程）。"""
    from aplus.bond_sync import sync_bond_daily

    try:
        res = sync_bond_daily(data_root)
    except Exception as exc:  # noqa: BLE001 — 转债侧故障不得拖垮股票侧主流程
        msg = f"转债日线增量异常：{exc!r}"
        print(f"[bond-update] {msg}")
        # 「不拖垮主流程」由 run_step 的单步隔离保证（下一步照跑），
        # 不靠在这里裸 return —— 裸 return 会让 status 变成 "ok"，
        # 而这一步其实一步都没做。
        raise StepSkipped(msg)
    status = res.get("status")
    if status == "ok":
        print(f"[bond-update] {res['latest']} → {res['new_latest']}：更新 {res['saved']} 只"
              f"（失败 {res['failed']}，摘牌/无数据 {res['empty']}，共 {res['total']} 只；"
              f"探针 {res.get('probe_fetched')}/{len(res.get('probe_codes') or [])} "
              f"问出结果、{res.get('probe_advanced')} 只前进）")
    elif status == "no_new_session":
        # 🚨 2026-10-15：这一行原本只打印结论、不打印任何证据，
        #   而结论连续 4 个交易日是**错的**——探针选中一只 10-09 后停止交易的债，
        #   `max()` 又让它成为确定性的死点，整个 1087 只的库被永久冻住。
        #   「市场没开」是**否定**断言，必须自带可独立重算的材料
        #   （第「守卫不许给自己发信号」层：告警不许只凭自己存在就成立）。
        _d = res.get("probe_source_dates") or {}
        print(f"[bond-update] 无新交易日（最新仍为 {res['latest']}），跳过。"
              f"⚠️ 该结论由 {res.get('probe_fetched')} 只探针支撑："
              f"{', '.join(f'{c}→{d}' for c, d in sorted(_d.items())) or '(无)'}"
              + (f"；探针失败 {len(res.get('probe_failed') or [])} 只"
                 if res.get("probe_failed") else ""))
    else:
        print(f"[bond-update] 状态 {status}：{res.get('reason') or res.get('error', '')}")


def step_bond_paper(data_root, reports_dir, today):
    """转债纸面账户：重放 → 落盘 → 摘要。

    start 在首次运行时固定为当天并写入 state，之后**只读不改**——它是调仓网格的
    相位锚点。run() 是纯重放（幂等），18:30 与 19:30 守卫连跑不会重复记账。
    """
    from aplus.bond_horizon import build_horizon_cache
    from aplus.bond_paper import (
        DEFAULT_COST,
        DEFAULT_HOLD_DAYS,
        DEFAULT_RANDOM_SEEDS,
        DEFAULT_TOP_N,
        PAPER_PHASES,
        BondPaperAccount,
        summarize_paper,
    )
    from aplus.bond_sync import load_bond_map
    from aplus.reports import (
        read_bond_paper_state,
        save_bond_paper,
        write_bond_paper_state,
        write_bond_paper_summary,
    )

    state = read_bond_paper_state(reports_dir)
    if state is None:
        state = {
            "created": today, "start": today,
            "hold_days": DEFAULT_HOLD_DAYS, "top_n": DEFAULT_TOP_N,
            "cost": DEFAULT_COST,
            "phases": list(PAPER_PHASES),
            "random_seeds": list(DEFAULT_RANDOM_SEEDS),
            "note": PAPER_STATE_NOTE,
        }
        write_bond_paper_state(reports_dir, state)
        print(f"[bond-paper] 账户创建：锚点 start={today}（相位在此刻定死，此后不再修改）")

    bond_map = load_bond_map(data_root)
    if not bond_map:
        msg = "转债库为空（先跑 scripts/bond_bootstrap.py）"
        print(f"[bond-paper] {msg}")
        raise StepSkipped(msg)
    # 日历来源钉进 state：参考债一旦停牌日历就会少一天，调仓网格整体偏移，
    # 相位锚点当场失效。钉死并复用，摘牌时才回退并打 error 提醒重新钉定。
    cache = build_horizon_cache(bond_map, ref_code=state.get("calendar_ref"))
    cal = cache.get("calendar") or []
    if cache.get("ref_fallback"):
        reason = cache.get("ref_fallback_reason")
        print(f"[bond-paper] ⚠️ 日历锚 {state.get('calendar_ref')} 失效（{reason}）"
              f"→ 已回退到 {cache['calendar_ref']}；**网格可能位移，建议重开账户或接受回退**")
    state["calendar_ref"] = cache["calendar_ref"]
    write_bond_paper_state(reports_dir, state)
    if not cal:
        msg = f"无交易日历（锚点 {state.get('calendar_ref')} 与回退候选都没问出日历）"
        print(f"[bond-paper] {msg}")
        raise StepSkipped(msg)

    acct = BondPaperAccount(
        hold_days=state["hold_days"], top_n=state["top_n"], cost=state["cost"],
        phases=state["phases"], random_seeds=state["random_seeds"])
    rows = acct.run(cache, state["start"], "9999-12-31")
    save_bond_paper(reports_dir, rows)

    summary = summarize_paper(rows, state)
    summary["calendar_last"] = cal[-1]
    summary["calendar_first"] = cal[0]
    write_bond_paper_summary(reports_dir, summary)

    if rows.empty:
        print(f"[bond-paper] 数据最新 {cal[-1]}，尚无 ≥{state['start']} 的交易日——"
              f"非交易日或等待首个交易日，账户保持待命")
        # 🚨 这里**故意保留裸 return**，且 `ok` 在这一处是真的：
        # 上面的 `write_bond_paper_summary(reports_dir, summary)` 已经落了盘，
        # 这一步的交付物（账户状态 + 摘要）**已经交了**，只是净值序列为空。
        # ⇒ 与上面两格（库为空 / 无日历）语义相反：那里**一个字节都没写**。
        return
    head = summary["headline"]
    print(f"[bond-paper] 数据截至 {summary['as_of']}｜{summary['n_days']} 个交易日")
    print(f"    主账户 cheap_p0：净值 {head['last_nav']:.4f}｜回撤 {head['max_drawdown']:.2%}｜"
          f"调仓 {head['n_signals']} 次｜累计费用 {head['total_cost']:.2%}")
    if summary["phase_spread"]:
        band = " ".join(f"p{k[-1]}={v:.3f}" for k, v in summary["phase_spread"].items())
        print(f"    相位带：{band}")
    if summary.get("random_mean_nav") is not None:
        print(f"    随机对照均值 {summary['random_mean_nav']:.4f}")
    if summary["verdict"]:
        v = summary["verdict"]
        print(f"    判决：{v['n_signals']} 期后 超额 {v['excess_vs_random']:+.4f}"
              f"（相位最好 {v['phase_best']:.3f} / 最差 {v['phase_worst']:.3f}）")
    else:
        print(f"    判决：样本累积中（调仓期不足 3 次，不给年化结论）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--skip-update", action="store_true")
    parser.add_argument("--skip-bonds", action="store_true",
                        help="跳过转债增量与纸面账户（调试用）")
    args = parser.parse_args()

    from aplus.log import get_logger, rotate_logs, setup_logging
    from aplus.reports import write_run_status

    setup_logging(args.data_root)
    rotate_logs(args.data_root)

    # 薄壳：给整条流水线一个 run_id 与 run_end，healthcheck 才能一眼看到
    # 「今天跑了没、成没成、耗时多久」。无缓冲与噪音隔离由 _bootstrap 负责。
    from _bootstrap import RUN
    with RUN("daily", capital=args.capital,
             skip_update=args.skip_update, skip_bonds=args.skip_bonds) as run:
        _pipeline(args, run)


def _classify_with_coverage(today: str, daily_dir=None):
    """交易日判定 —— **必须带覆盖率**。

    ## 为什么不接覆盖率，第四态就是死的

    `classify_run_day(day, known)` 不给 `coverage` 时走**旧的三态**
    （`TRADING_DAY` / `WEEKEND` / `WORKDAY_NOT_IN_STORE`）。
    `coverage=None` ⇒ `RUN_DAY_TRADING_DAY_PARTIAL` 在生产上**永不可达**
    ⇒ 「仓里只采到一半股票」与「仓里完全没今天」在产物里**长得一样**
    ⇒ 两者都可能被读成「不是工作日」而**继续出一张残缺榜**。

    一张残缺榜比没有榜更坏：缺掉的那些票拿不到今天的因子 ⇒ 它们不在
    横截面里 ⇒ 横截面被悄悄缩小 ⇒ 名次变了而**没有任何东西会报**
    （本仓静默失效清单里「一只票定全池」那族的远亲）。

    ## 为什么成本可以忽略

    `coverage_detail` 与 `trading_days` **读同一份缓存快照**
    （`aplus/trading_calendar.py::_snapshot`）⇒ 多这一次调用不额外读盘。
    ⇒ 分子分母也一起报（`hit/total`）：只报一个比例，读者无法判断
    「0.5」是「1/2 只」还是「95/200 只」。
    """
    day = date.fromisoformat(today)
    # 🚨 2026-10-21 修：`daily_dir` **必须**传进来。
    #   旧写法不传 ⇒ 读的是**仓库里那一份**（`aplus/trading_calendar.py::DAILY_DIR`）
    #   ⇒ ⇒ `run_day_gate(today, data_root, …)` 的 `data_root` 对**分类**是**假的**
    #   ⇒ ⇒ 守卫的判定**取决于测试跑的那一刻生产仓里有什么**
    #   ⇒ ⇒ 2026-10-21 20:16 采进 10-21 之后，
    #   「仓里只到 10-20 ⇒ 必然 WORKDAY_NOT_IN_STORE」这条**前提自动失效**
    #   ⇒ ⇒ 守卫红了，而**代码没坏**（第 147 层：守卫前提过期）
    #   ⇒ ⇒ 修法不是改夹具应付今天，而是让分类**真的走那个 data_root**
    #   ⇒ ⇒ 守卫从此**与运行时无关**。
    if daily_dir is not None:
        cov = coverage_detail(day, daily_dir=Path(daily_dir))
    else:
        cov = coverage_detail(day)
    verdict = classify_run_day(day, trading_days(), coverage=cov.coverage)
    return verdict, cov


def run_day_gate(today, data_root, run):
    """**交易日闸**：非交易日一个字都不发布。

    返回 `None` ⇒ 照常发布；返回 `(rc, verdict)` ⇒ 已写好 `run_status`，该退出。

    ## 🚨 调用点必须排在 `step_incremental_update` **之后**
    更新正是让「今天」成为仓里交易日的那一步。若闸在更新**之前**，
    每个真正的交易日（例：周一，库最新停在上周五）都会得到
    「工作日不在库里」⇒ **合法交易日反而被 rc=2 挡住，一天都不出单**。

    ## 为什么是模块级而不是 `_pipeline` 里的一段
    判据埋在一条 20 分钟的流水线后面就等于没有守卫（CLAUDE.md 第 124 层）。
    抽出来之后，守卫能 monkeypatch `trading_days` 与 `write_run_status`，
    在 1 毫秒内断言「周末 ⇒ rc=0 且不碰 predictions」——
    **绝不跑 `run_daily.py::main()` 来验证**。

    ## 退出码为什么是 2 而不是 1
    `WORKDAY_NOT_IN_STORE` 是「仓里没有这一天，而仓里**没有任何东西**能
    区分法定节假日与采集失败」⇒ 必须有人来看。rc=2 与「留空 `--end`
    拒绝运行」那几处一致（同一个意思：要求人来裁决）。
    """
    from aplus.reports import write_run_status

    verdict, cov = _classify_with_coverage(
        today, daily_dir=Path(data_root) / "master" / "daily")
    cov_pct = f"{cov.coverage * 100:.1f}%（{cov.hit}/{cov.total} 只已到当天）"
    if verdict == RUN_DAY_TRADING:
        print(f"[gate] {today} 是交易日，正常发布（覆盖率 {cov_pct}）")
        return None

    reports_dir = Path(data_root) / "reports"
    # ★ 2026-10-20：状态里必须说清**这次尝试的结局**，而不只是「跑在哪天、跑没跑成」。
    # 旧写法只写 `status:"skipped"` + 一个门名 ⇒ 界面上是「部分完成 · run_day_gate」，
    # 用户既不知道今天有没有出单，也不知道该修 cron 还是修采集。
    # ⇒ `outcome` 把「不是工作日」与「工作日但仓里没数据（采集失败）」**分开**。
    outcome, outcome_reason = outcome_for_run_day(verdict, coverage=cov.coverage)
    write_run_status(reports_dir, build_run_status(
        attempted_date=today, outcome=outcome, outcome_reason=outcome_reason,
        status="skipped", steps={"run_day_gate": verdict},
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    ))
    reason = RUN_DAY_LABELS[verdict]
    print(f"[gate] {today} 判定为 {verdict} ⇒ 不发布任何名单。{reason}")
    print(f"[gate] 本次尝试的结局：{outcome} —— {outcome_reason}")
    run.log(f"交易日闸拦下发布：{verdict}", level="info", date=today,
            verdict=verdict, reason=reason, outcome=outcome)
    # 周末是**正常**的一天（不是失败），所以 rc=0；
    # 「工作日不在仓里」是**必须有人来看**，rc=2。
    rc = 0 if verdict == RUN_DAY_WEEKEND else 2
    return rc, verdict


def _pipeline(args, run):
    from aplus.log import get_logger
    from aplus.reports import write_run_status
    log = get_logger("daily")
    today = date.today().isoformat()
    store = LocalStore(Path(args.data_root))
    reports_dir = Path(args.data_root) / "reports"
    steps = {}

    def run_step(name, fn):
        """单步失败不拖垮全流程（照常出预测），但必须留下完整证据。

        🚨 `StepSkipped` 必须在 `Exception` **之前**分支，否则它会被
        记成 `failed:` —— 而锁占用是**条件不满足**（良性、每日可见），
        记成 error 级失败是把一个正常事件报成告警，那是错分类不是风格问题。
        三态映射本身在模块级 `classify_step`（可 import ⇒ 有东西能挡）。
        """
        import time as _t
        t0 = _t.time()
        log.event("step_start", step=name)
        try:
            fn()
        except StepSkipped as exc:
            steps[name] = classify_step(exc)
            log.event("step_skipped", level="warn", step=name,
                      elapsed=round(_t.time() - t0, 1), detail=str(exc))
            run.log(f"步骤跳过：{name}", level="warn", step=name, detail=str(exc))
            print(f"[{name}] 步骤跳过：{exc}")
        except Exception as exc:  # noqa: BLE001
            steps[name] = classify_step(exc)
            log.event("step_failed", level="error", step=name,
                      elapsed=round(_t.time() - t0, 1), detail=repr(exc))
            run.log(f"步骤失败：{name}", level="error", step=name, detail=repr(exc))
            print(f"[{name}] 步骤失败：{exc!r}")
        else:
            steps[name] = classify_step(None)
            log.event("step_done", step=name, elapsed=round(_t.time() - t0, 1))

    update_status = None
    if not args.skip_update:
        holder: dict = {}

        def _do_update():
            holder["status"] = step_incremental_update(
                store, default_sources(), today, args.data_root)

        run_step("update", _do_update)
        update_status = holder.get("status")

    # 🚨 **数据源不可用 ⇒ 立刻中止，退出码 3**（不是 2）。
    #    2 = 「工作日但仓里没这一天」⇒ 要人裁决今天该不该出单；
    #    3 = 「数据源不可用」⇒ **晚些时候重试就能补上**，而且要有人来看。
    #    ⇒ 绝不能让它落到交易日闸上：闸会把它误判成「今天不是交易日 /
    #    仓里没数据」（2026-10-20 那次断供正是这个误判造成的 ——
    #    诊断错了 ⇒ 没人知道该重试 ⇒ 整天没出单）。
    if update_status == UPDATE_UPSTREAM_UNAVAILABLE:
        msg = ("🚨 **数据源不可用**（网络/服务器），今天没采到任何新数据 ⇒ "
               "**不出单**。这不是「今天不是交易日」，是**该重试**："
               "查 data/run_daily.err.log 的上游报错，或等晚间补跑班次。")
        print(f"[update] {msg}")
        run.log(msg, level="error", step="update",
                outcome=OUTCOME_UPSTREAM_UNAVAILABLE)
        write_run_status(reports_dir, build_run_status(
            attempted_date=today,
            outcome=OUTCOME_UPSTREAM_UNAVAILABLE,
            outcome_reason=msg,
            status="failed", steps=steps,
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
        raise SystemExit(3)

    # 🚨 闸必须在 update **之后**、verify **之前**（理由见 run_day_gate 的 docstring）
    _gated = run_day_gate(today, args.data_root, run)
    if _gated is not None:
        raise SystemExit(_gated[0])

    if not args.skip_bonds:
        run_step("bond_update", lambda: step_bond_update(args.data_root))
    run_step("verify", lambda: step_verify_yesterday(store, reports_dir, today))

    # 因子完整性闸门**必须排在预测之前**。2026-10-11 事故的结构正是
    # 「先发名单、事后体检」：体检在 09:07 抓到了坏因子，而 08:53 的
    # 预测已经用坏数据把用户名单写出来了（mom20=+903.72% 的三只股票
    # 直接霸占涨榜）。后发现的体检挡不住已发出的名单。
    audit = _run_audit(args.data_root, run_step, steps)
    # ★ `predict` 的产物（**全池** + 权重）要交给下一步 ⇒ 用 holder 收回来。
    #   `run_step` 只看异常，不看返回值 ⇒ 返回值**不能**当状态。
    #   holder 声明在**闸门之外** ⇒ 闸门拦下时下面那步读得到它（空 ⇒ 跳过）。
    predict_ctx: dict = {}
    if audit.get("error") or factor_gate_blocks(audit["bad"], audit["usable"]):
        msg = (f"因子表损坏 {audit['bad']}/{audit['usable']}"
               f"{'（体检本身失败）' if audit.get('error') else ''} → "
               f"**拒绝发布推荐**。坏因子会让动量冲到 +900% 量级，"
               f"2026-10-11 就因此把三只错误标的送进了用户建仓清单。"
               f"修复命令：.venv/bin/python scripts/repair_factors.py --all")
        run.log(msg, level="error", step="predict")
        print(f"[predict] {msg}")
        steps["predict"] = f"blocked: 因子表损坏 {audit['bad']} 只"
    else:

        def _do_predict():
            predict_ctx.update(step_predict_and_allocate(
                store, reports_dir, today, args.capital,
                _load_names(Path(args.data_root))))

        run_step("predict", _do_predict)
    # ── 持仓级降级预警（2026-10-21 接线）────────────────────────────
    # 排在 **predict/allocate 之后**：它要用**当天的全池**与**当天的配置产物**。
    # ⚠️ `predict` 没过 ⇒ 没有全池 ⇒ 这一步**跳过**（不是失败）：
    #   缺输入与「跑失败」是两件事（`classify_step` 的三态就是这么分的）。
    # ⚠️ 这一步失败**不许**拖垮整条流水线（`run_step` 记成 `failed:` 并继续），
    #   但**不许**静默成功：产物要么落盘（带章），要么什么都没有。
    if predict_ctx.get("universe") is not None:
        run_step("holdings_alert", lambda: step_holdings_alert(
            reports_dir, today, predict_ctx))
    else:
        steps["holdings_alert"] = "skipped: predict 步骤没有产出全池"
    if not args.skip_bonds:
        run_step("bond_paper", lambda: step_bond_paper(args.data_root, reports_dir, today))
    run_step("stats", lambda: step_stats(reports_dir, today))

    status = reduce_run_status(steps)
    # ★ 2026-10-20：把「这次尝试的结局」也落进状态文件。
    # `status` 说「跑成没跑成」，`outcome` 说「**出了单没出单**、为什么」。
    # 两个是**不同的量** ⇒ 并排写，不合并成一个字段。
    _not_ok = {k: v for k, v in steps.items() if v != "ok"}
    if status == "ok":
        outcome, outcome_reason = OUTCOME_OK, "全部步骤跑完，名单已发布。"
    else:
        outcome = OUTCOME_FAILED
        outcome_reason = (
            f"这次跑完了，但有 {len(_not_ok)} 步没过（{'/'.join(_not_ok) or '?'}）"
            f"⇒ **今天的名单可能没出全**。细节看 data/run_daily.log。")
    write_run_status(reports_dir, build_run_status(
        attempted_date=today, outcome=outcome, outcome_reason=outcome_reason,
        status=status, steps=steps,
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S")))
    run.log("流水线完成", level="info" if status == "ok" else "error",
            date=today, status=status, outcome=outcome,
            failed=[k for k, v in steps.items() if v != "ok"])
    print(f"[done] {today} 全流程完成（{status}）· 本次尝试的结局：{outcome}")


if __name__ == "__main__":
    main()
