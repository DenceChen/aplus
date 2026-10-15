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
from aplus.pipeline import SyncPipeline
from aplus.predict import build_predictions, filter_universe
from aplus.reports import (
    append_verification,
    latest_prediction_date,
    load_predictions,
    load_verification_history,
    pending_prediction_dates,
    save_allocation,
    save_predictions,
)
from aplus.backtest import STOCK_COST_DEFAULT
from aplus.snapshot import build_cross_section
from aplus.sources import default_sources
from aplus.store import LocalStore
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
        print(f"[update] 全市场回填进行中（{exc}），"
              f"跳过增量以免并发打 baostock——明日自动恢复")
        return
    try:
        _incremental_update_locked(store, sources, today)
    finally:
        lock.release()


def _incremental_update_locked(store, sources, today):
    pipeline = SyncPipeline(sources, store)  # 短窗口严格覆盖
    daily_dir = store.root / "master" / "daily"
    files = sorted(daily_dir.glob("*.parquet"))
    if not files:
        print("[update] 主库为空，跳过")
        return

    # 探针：先更新参考股票——无新数据（非交易日/未到更新时间）则整体跳过，
    # 避免对 5546 只股票做无效拉取（2026-10-10 周六实测教训）
    probe_code = files[0].stem
    probe_last = _safe_last_date(pd.read_parquet(files[0]))
    if probe_last is None:
        # 探针票读坏了 → **换一只**，而不是整体放弃更新。
        # `files[0]` 是 000001.SZ，而它正是 INCIDENTS 里记的
        # 「只剩 17 行」截断缺陷的受害者之一——拿一只可能坏掉的票
        # 当全市场的判据，等于让一只票决定 5459 只的命运。
        for alt in files[1:]:
            cand = _safe_last_date(pd.read_parquet(alt))
            if cand is not None:
                probe_code, probe_last = alt.stem, cand
                break
        else:
            print("[update] 所有股票的日期列都读不出来，整体跳过——"
                  "主库已损坏，需要 bootstrap 重灌")
            return
    if probe_last >= today:
        print(f"[update] 参考股票 {probe_code} 已是最新（{probe_last}），跳过")
        return
    try:
        pipeline.sync_daily(probe_code, probe_last, today)
        # 探针必须验证数据真的前进：周末拉取会返回含旧日期的非空帧（≠有新数据）
        new_last = str(pd.read_parquet(files[0])["date"].max())
        if new_last <= probe_last:
            print(f"[update] 参考股票 {probe_code} 数据未前进（{probe_last} → {new_last}），整体跳过")
            return
    except RuntimeError:
        print(f"[update] 参考股票 {probe_code} 无新数据（非交易日或数据未就绪），整体跳过")
        return

    done = 1
    unreadable = 0
    for path in files[1:]:
        code = path.stem
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
    results = verify_predictions(preds, bars, require_cost=True,
                                 default_cost=STOCK_COST_DEFAULT)
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
        return
    print(f"[verify] 待判定 {len(pending)} 个预测日：{pending[0]} ~ {pending[-1]}")
    done = 0
    for pred_date in pending:            # 从最早的补起——越早的证据越该先补
        if _judge_one_day(store, reports_dir, pred_date, today):
            done += 1
    print(f"[verify] 本轮补判完成 {done}/{len(pending)} 天")


def step_predict_and_allocate(store, reports_dir, today, capital, names_map):
    """出当日预测与配置方案。

    **回填持锁时一律不出名单**：`LocalStore._upsert` 是 `to_parquet` 直写，
    不是原子替换——并发读可能读到写了一半的文件。与其让预测崩在一个
    半截 parquet 上，不如明确不发布，并在日志里说明原因。
    """
    # 预测只读不写，所以这里用「锁在不在」的预检查即可：读到一个正在被
    # 改写的文件会得到不可信的横截面，宁可跳过。但判定走同一份 WriteLock，
    # 免得「锁」这个词在两个地方指两件事。
    if WriteLock(Path(store.root) / "bootstrap.lock").path.exists():
        print(f"[predict] 全市场回填进行中（bootstrap.lock），跳过预测——"
              f"数据文件正在改写，此刻的横截面不可信")
        return

    from aplus.evolve import live_execution_status, load_weights
    from aplus.factor import composite_score
    from aplus.live_rebalance import advance, instruction, is_due, load_state, save_state

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
    save_predictions(reports_dir, today, up, down, weights=weights)

    # ── 持仓指令（2026-10-11 21:45）────────────────────────────────
    # **推荐照旧每天出**——用户要的是「每天 10 涨 10 跌」，那是排序产品。
    # 调仓频率管的是**交易**，两者不冲突。这里只补一段「今天该不该动手」。
    #
    # 缓冲区只在权重文件**显式声明**时才生效。默认不开启：
    # 缓冲把日频年成本从 16.91% 压到 0.57%，默认开启会静默改写所有历史结论。
    held_up = list((prev or {}).get("up") or [])
    held_down = list((prev or {}).get("down") or [])
    trade_up, trade_down = list(up["code"]), list(down["code"])
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

    instr = instruction(due, trade_up, trade_down, every, buf,
                        held_up=held_up, held_down=held_down)
    if instr:
        print(instr)

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
        print(f"[predict] 距上次换仓 {state.get('days_since_rebalance', '?')}/"
              f"{every} 个交易日 —— 持仓沿用 {state.get('held_since', '?')} 的名单"
              f"（{len(state.get('up') or [])} 只），今日名单只作参考")

    if due:
        state["days_since_rebalance"] = 0
    else:
        state = advance(state, today)
    save_state(reports_dir, state)

    print(f"[predict] 池子 {len(universe)} 只 → 涨 {len(up)} / 跌 {len(down)}")
    print("  预测涨 Top10:")
    for _, r in up.iterrows():
        print(f"    {r['code']} {r['name']} score={r['score']:.2f} mom20={r['mom20']:.2%}")
    print("  预测跌 Bottom10:")
    for _, r in down.iterrows():
        print(f"    {r['code']} {r['name']} score={r['score']:.2f} mom20={r['mom20']:.2%}")

    plan = build_allocation(up, capital=capital)
    save_allocation(reports_dir, today, plan.to_dict())
    equity = sum(p["weight"] for p in plan.positions)
    print(f"[alloc] 配置：{len(plan.positions)} 只 × 10% = 权益 {equity:.0%}，现金 {plan.cash_weight:.0%}")
    for p in plan.positions:
        print(f"    {p['code']} {p['name']} {p['amount']:.0f} 元")

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
        dl = live_down_leg(reports_dir, every)
        if dl.get("known"):
            flag = " ⚠️ 承诺未兑现" if dl["avg_down_return"] >= 0 else ""
            print(f"[down] {dl['note']}{flag}")
    except Exception as exc:  # noqa: BLE001
        print(f"[down] 跌腿表现读取失败（不影响今日名单）：{exc!r}")


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
    history = load_verification_history(reports_dir)
    stats = rolling_stats(history)
    stats["as_of"] = today
    path = Path(reports_dir) / "stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(history):
        # ★ 两个数都要报：条目数是**信息量**，独立窗口数才是**样本量**。
        #   周末出单会让多个信号日落进同一个成交窗口
        #   （实测 3 个信号日 → 1 笔），只报条目数等于把一次交易数三遍。
        win = stats.get("n_independent_windows")
        win_txt = f"{win} 次独立成交" if win else "独立成交次数未知（缺 entry/exit）"
        print(f"[stats] 累计 {len(history)} 条判定 / {win_txt}："
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
        print(f"[bond-update] 异常跳过：{exc!r}")
        return
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
        print("[bond-paper] 转债库为空，跳过（先跑 scripts/bond_bootstrap.py）")
        return
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
        print("[bond-paper] 无交易日历，跳过")
        return

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


def _pipeline(args, run):
    from aplus.log import get_logger
    from aplus.reports import write_run_status
    log = get_logger("daily")
    today = date.today().isoformat()
    store = LocalStore(Path(args.data_root))
    reports_dir = Path(args.data_root) / "reports"
    steps = {}

    def run_step(name, fn):
        """单步失败不拖垮全流程（照常出预测），但必须留下完整证据。"""
        import time as _t
        t0 = _t.time()
        log.event("step_start", step=name)
        try:
            fn()
            steps[name] = "ok"
            log.event("step_done", step=name, elapsed=round(_t.time() - t0, 1))
        except Exception as exc:  # noqa: BLE001
            steps[name] = f"failed: {exc!r}"
            log.event("step_failed", level="error", step=name,
                      elapsed=round(_t.time() - t0, 1), detail=repr(exc))
            run.log(f"步骤失败：{name}", level="error", step=name, detail=repr(exc))
            print(f"[{name}] 步骤失败：{exc!r}")

    if not args.skip_update:
        run_step("update", lambda: step_incremental_update(store, default_sources(), today, args.data_root))
    if not args.skip_bonds:
        run_step("bond_update", lambda: step_bond_update(args.data_root))
    run_step("verify", lambda: step_verify_yesterday(store, reports_dir, today))

    # 因子完整性闸门**必须排在预测之前**。2026-10-11 事故的结构正是
    # 「先发名单、事后体检」：体检在 09:07 抓到了坏因子，而 08:53 的
    # 预测已经用坏数据把用户名单写出来了（mom20=+903.72% 的三只股票
    # 直接霸占涨榜）。后发现的体检挡不住已发出的名单。
    audit = _run_audit(args.data_root, run_step, steps)
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
        run_step("predict", lambda: step_predict_and_allocate(
            store, reports_dir, today, args.capital, _load_names(Path(args.data_root))))
    if not args.skip_bonds:
        run_step("bond_paper", lambda: step_bond_paper(args.data_root, reports_dir, today))
    run_step("stats", lambda: step_stats(reports_dir, today))

    status = "ok" if all(v == "ok" for v in steps.values()) else "partial"
    write_run_status(reports_dir, {"date": today, "status": status, "steps": steps,
                                   "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    run.log("流水线完成", level="info" if status == "ok" else "error",
            date=today, status=status,
            failed=[k for k, v in steps.items() if v != "ok"])
    print(f"[done] {today} 全流程完成（{status}）")


if __name__ == "__main__":
    main()
