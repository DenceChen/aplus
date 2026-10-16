"""现任权重的多窗口健康度：把它从 web 层抽出来，供判决板与实盘共用。

## 为什么要抽

这段逻辑原本是 `aplus/webapp.py` 里的一个嵌套函数 `_champion_health`，
80 行，负责把历次赛马报告里**属于现任权重**的行汇总成多窗口证据。

想让它同时被 `scripts/run_daily.py` 用上（让每日名单自己带上
「现任已被证伪」的提示）时，摆在面前只有两条路：

- 复制一份 → **两份实现迟早漂移**，然后你就再也不知道
  「线上那份和判决板那份」是不是在说同一件事
- 抽成模块 → 两处引用同一份

**历史上这个项目已经吃过一次「同一件事两个实现」的亏**：
`_weights_path` 复制过、`_annual_cost` 凭印象编过一次、
`indicator` 换手率口径与书里的比值口径混过一次。

## 三个必须照搬的坑（都是踩过的）

1. **`kind` 判断必须在 `isinstance(dict)` 之前**。
   `independent_validation_*.json` 也是 dict，写成 `elif` 时被第一个分支吃掉，
   `table` 取空 → 那一段被**静默丢弃** → 证伪判定永远只看到 1 段。
2. **`evolution_log.json` 是数组**，形状与报告族完全不同
   （`[{window, evaluations:[...]}]`），`.get()` 直接 500。
3. **两个报告族用不同键名表示同一个量**：
   `race_*.json` → `up_excess_vs_market` / `excess_days`；
   `independent_validation_*.json` → `excess` / `days`。
   只认前者，独立验证那一段就被判成「excess=0、t=−3.55」
   这种自相矛盾的组合而悄悄丢掉。
"""
from __future__ import annotations

import json
from pathlib import Path

from .provenance import report_jsons


def collect_windows(reports_dir, running_weights) -> list[dict]:
    """历次报告里属于现任权重的窗口证据。缺数据返回空列表（= unknown）。"""
    if not running_weights:
        return []
    want = tuple(sorted((k, float(v)) for k, v in running_weights.items() if v))
    reports_dir = Path(reports_dir)
    windows: list[dict] = []
    # ★ 走 report_jsons()：它排掉 `.provenance.json` 边车。
    #   边车名是主报告名的延长，字典序更大 ⇒ 自己 glob 必然把边车当报告。
    paths = report_jsons(reports_dir, "race_*.json",
                         "evolution_race*.json",
                         "independent_validation_*.json")
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data.get("kind") == "independent_validation":
            wnd = data.get("window") or []
            rows = [{"weights": r.get("weights"),
                     "stats": r.get("test") or {}, "_window": wnd}
                    for r in data.get("rows") or []]
        elif isinstance(data, dict):
            wnd = data.get("window") or []
            rows = [{"weights": r.get("weights"),
                     "stats": ((r.get("test") or {}).get("stats")) or {}}
                    for r in (data.get("table") or [])]
        else:
            rows = []
            for entry in (data or []):
                if not isinstance(entry, dict):
                    continue
                wnd = entry.get("window") or []
                for ev in entry.get("evaluations") or []:
                    rows.append({"weights": ev.get("weights"),
                                 "stats": ev.get("stats") or {},
                                 "_window": wnd})
            if not rows:
                continue
        for row in rows:
            got = tuple(sorted((k, float(v))
                               for k, v in (row.get("weights") or {}).items() if v))
            if got != want:
                continue
            st = row.get("stats") or {}
            wnd = row.get("_window") or (data.get("window")
                                        if isinstance(data, dict) else None)
            if st.get("excess_t") is None or not wnd:
                continue
            exc = st.get("up_excess_vs_market", st.get("excess"))
            days = st.get("excess_days", st.get("days"))
            windows.append({"window": list(wnd),
                            "n_days": int(days or 0),
                            "excess": float(exc if exc is not None else 0.0),
                            "t": float(st["excess_t"])})
    return windows


def champion_health(reports_dir, running_weights) -> dict:
    """现任权重在多个**独立窗口**上的累计健康度。

    必须按权重匹配而不是「取最新一份的表」：历次报告的候选池不同，
    现任未必每轮都在。缺数据就说 `unknown`——
    **不拿别的组的成绩给线上这组背书。**
    """
    from .evolve_race import evaluate_champion_health

    if not running_weights:
        return {"champion_falsified": False, "champion_health": "unknown",
                "champion_windows": 0}
    windows = collect_windows(reports_dir, running_weights)
    if not windows:
        return {"champion_falsified": False, "champion_health": "unknown",
                "champion_windows": 0}
    h = evaluate_champion_health(windows)
    return {"champion_falsified": bool(h["falsified"]),
            "champion_health": h["verdict"],
            "champion_windows": len(h["windows"]),
            "champion_note": h["note"]}

def live_cost_share(reports_dir, rebalance_every: int) -> dict:
    """线上这个**调仓频率**下，成本吃掉毛超额的最坏比例。

    ## 为什么问的是频率而不是「现任那组权重」

    报告里未必有线上那组权重的行（`weights.json` 从未生成过）。
    而真正决定「能不能活」的是**执行频率**：同一批权重在 K=21 上
    成本占比 16%、在 K=1 上 91%，差 5 倍多。
    频率是线上配置里**确实存在**的那个字段，所以问它。

    ## 取最差而不是最好

    报告里同一频率下有多组候选，取最好的一组是粉饰。
    这里问的是「我线上这套执行方式最坏能有多糟」。

    ## 没有数据就说没有

    旧报告没有 `cost_share` 字段、该频率没有候选、报告本身不存在——
    三种情况一律返回 `known=False` / `cost_share=None`。
    返回 0 会被读成「成本不花什么钱」，而实际是**没测过**。
    """
    # 按**修改时间**从新到旧试：按文件名排序取「最新」会拿到旧报告
    #（`race_composite_*` 在字典序上排在 `ic_guided_race_*` 后面），
    # 而旧报告里根本没有 cost_share —— 表现为「永远测不出来」。
    #
    # 逐份回退而不是只看最新一份：新增一个不含该指标的报告，
    # 不应该把一个已知结论降级成未知。
    files = [p for pat in ("race_composite*.json", "ic_guided_race*.json")
             for p in Path(reports_dir).glob(pat)]
    if not files:
        return {"known": False, "cost_share": None, "rebalance_every": rebalance_every,
                "note": "没有赛马报告，成本占比未测过（不等于成本为零）"}
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    def _miss(note):
        return {"known": False, "cost_share": None,
                "rebalance_every": rebalance_every, "note": note}

    for path in files:
        try:
            table = json.loads(path.read_text(encoding="utf-8")).get("table") or []
        except (OSError, ValueError, AttributeError):
            continue
        vals = [r["test_stats"].get("cost_share") for r in table
                if isinstance(r, dict)
                and r.get("rebalance_every") == rebalance_every
                and isinstance(r.get("test_stats"), dict)
                and isinstance(r["test_stats"].get("cost_share"), (int, float))]
        if not vals:
            continue
        worst = float(max(vals))
        note = (f"K={rebalance_every} 下成本吃掉毛超额的最坏比例 {worst:.0%}"
                f"（{len(vals)} 组候选，取最差；来源 {path.name}）")
        if worst > 0.5:
            note += "；超过 50% 意味着毛超额的一半以上还没到手就付给券商了"
        return {"known": True, "cost_share": worst, "report": path.name,
                "rebalance_every": rebalance_every, "n": len(vals), "note": note}
    return _miss(f"现有赛马报告里都没有 K={rebalance_every} 的成本占比（未测过）")


def _norm_weights(w):
    """权重正规化。🚨 **必须与 `collect_windows` 逐字同一条**（第 117 层）。

    两处各写一份 ⇒ 两处会漂 ⇒ 「现任是谁」在两处得到两个答案，
    而两处都不报错。
    """
    if not isinstance(w, dict):
        return None
    try:
        return tuple(sorted((k, float(v)) for k, v in w.items() if v))
    except (TypeError, ValueError):
        return None


def _norm_buffer(v):
    """`buffer_multiple` 正规化：缺键与 `None` 同义（都读作「没有缓冲」）。

    老报告整个键都没有 ⇒ `.get` 给出 `None`，而 `load_weights` 的回落路径
    写的也正是 `buffer_multiple: None` ⇒ 在生产侧它们是**同一个身份**。
    数字统一成 `float`，免得 `3` 与 `3.0` 因为 JSON 写法不同被当成两套配置。
    """
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def live_down_leg(reports_dir, rebalance_every: int, champion_weights: dict,
                  buffer_multiple) -> dict:
    """线上这套**执行配置**下，跌腿（产品承诺的「10 跌」那一半）到底跌不跌。

    ## 报告的是**现任那一行**，不是候选池的极值

    产品承诺是「每天 10 涨 10 跌」，而用户今天拿到的名单由
    `run_daily.py` 的 `load_weights()["weights"]` 出单、按
    `execution["rebalance_every"]` 调仓、用 `execution["buffer_multiple"]`
    决定哪些旧持仓留在带内 ⇒ 「兑现了吗」问的是**那一行**。

    🚨 **现任是三元组（权重, K, 缓冲），不是二元组。** 这不是我发明的：
    `aplus/live_rebalance.py::is_due` 的 docstring 第 4 条逐字写着
    「配置变了（频率或**缓冲**不同）——旧持仓是按旧配置挑的，
    继续拿着就是**拿一套策略的仓位去跑另一套策略**」。
    ⇒ 生产自己早就把缓冲算进身份；只按 (权重, K) 匹配，
    会把**另一套策略的跌腿**报成「你今天拿到的这 10 跌」。
    先量后判的探针也独立得到同一结论：incumbent 的权重在 K=1 上命中 **2 行**
    且数值不同，差异字段就是 `buffer_multiple`（`3.0` vs `None`）。
    ⚠️ 若忽略这一维而坚持「非唯一 ⇒ `known=False`」，
    **任何跑日频的人会静默失去这条检查**，而「静默」与「今天没警报」
    在判决板上逐位同形（第 119 层）。

    🚨 三个实参**都不许有默认值**：有默认值则「现任是谁」
    会被某次调用悄悄抹掉（第 119 层），而症状与「一切正常」逐位同形。

    旧版取 `max(avg_down_return)`（最坏候选），理由抄自 `live_cost_share`
    的「报告里未必有线上那组权重的行」—— 🚨 **该前提今天已不成立**
    （`load_weights` 有回落路径）。换臂预注册：
    `docs/PREREG_LIVE_DOWN_LEG_INCUMBENT_2026-10-16.md` §2。
    ⚠️ `live_cost_share` 不改：它问「最坏能有多糟」，与本函数不是同一个问题
    （同一个 `max` 手法套在两个问题上，一个对一个错）。

    ## 判据用绝对收益，不用「跑输大盘」

    5 万本金做不了空，跌腿对用户的价值是「这 10 只别买」，
    所以要证的是「这篮子真的会跌」。实测 97 组里
    `down_excess_vs_market > 0` 只有 45/97 ≈ 随机——
    按那个设标准，等于拿一个约等于随机的量当合格线。

    没有数据就说没有，返回 `known=False` —— 返回 0 会被读成
    「跌腿不跌不涨」，而实际是**没测过**。
    """
    if not isinstance(champion_weights, dict):
        raise TypeError("champion_weights 必须是那组现行权重的 dict")
    want = _norm_weights(champion_weights)
    want_buf = _norm_buffer(buffer_multiple)
    files = [p for pat in ("race_composite*.json", "ic_guided_race*.json")
             for p in Path(reports_dir).glob(pat)]
    if not files:
        return {"known": False, "avg_down_return": None,
                "hit_rate_down": None, "rebalance_every": rebalance_every,
                "note": "没有赛马报告，跌腿表现未测过（不等于不跌）"}
    # 按修改时间从新到旧：与 live_cost_share 同一策略（逐份回退而不是只看最新一份）。
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    def _miss(note):
        return {"known": False, "avg_down_return": None, "hit_rate_down": None,
                "rebalance_every": rebalance_every, "note": note}

    ident = (f"权重 {dict(champion_weights)}，K={rebalance_every}，"
             f"缓冲 {buffer_multiple if buffer_multiple is not None else '无'}")
    for path in files:
        try:
            table = json.loads(path.read_text(encoding="utf-8")).get("table") or []
        except (OSError, ValueError, AttributeError):
            continue
        hits = [r for r in table
                if isinstance(r, dict)
                and r.get("rebalance_every") == rebalance_every
                and _norm_buffer(r.get("buffer_multiple")) == want_buf
                and _norm_weights(r.get("weights")) == want
                and isinstance(r.get("test_stats"), dict)
                and isinstance(r["test_stats"].get("avg_down_return"), (int, float))]
        if not hits:
            continue
        if len(hits) > 1:
            # ⚠️ 这里**故意不给数**，理由要写明，否则日后会被读成「守卫过严」：
            # 同一份报告里同一个身份出现多行且数值不同 ⇒ 报告内部就有歧义，
            # 挑任何一行都是**编一个数出来**。保守方向是宁可沉默不可给错数。
            return {"known": False, "avg_down_return": None, "hit_rate_down": None,
                    "rebalance_every": rebalance_every, "n": len(hits),
                    "report": path.name,
                    "note": (f"现任（{ident}）在 {path.name} 里有 **{len(hits)} 行**且数值不同"
                             " ⇒ 身份不唯一，不给数（宁可沉默不可给错数）")}
        st = hits[0]["test_stats"]
        avg = float(st["avg_down_return"])
        rate = st.get("hit_rate_down")
        rate = float(rate) if isinstance(rate, (int, float)) else None
        hit_txt = f"，命中率 {rate:.1%}" if rate is not None else ""
        note = (f"现任（{ident}）的「10 跌」篮子日均收益 {avg:+.2%}{hit_txt}"
                f"（来源 {path.name}）")
        if avg >= 0:
            note += "——现任这一行里，「10 跌」那篮子平均每天**在涨**，产品承诺未兑现"
        return {"known": True, "avg_down_return": avg,
                "hit_rate_down": rate, "report": path.name,
                "rebalance_every": rebalance_every, "n": 1, "note": note}
    return _miss(f"现有赛马报告里没有现任（{ident}）那一行的跌腿数据（未测过）")

