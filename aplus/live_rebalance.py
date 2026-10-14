"""实盘持仓状态机：让「每天推荐」与「多久换一次手」在出单层面也分开。

## 三件事，各管各的

| | 谁负责 | 频率 |
|---|---|---|
| **推荐** 10 涨 10 跌 | 排序 | **每天** |
| **交易** 该不该动手 | 本模块 | 每 K 个交易日 |
| **口径** 净值能不能兑现 | `live_execution_status` | — |

用户要的是每天的涨跌名单，那是**排序**产品；调仓频率管的是**交易**。
把两者混为一谈，两个方向都错：为了守月频而 20 天不出单，
或者为了每天出单而每天全换（测的是月频、做的是日频）。

## `every=1` 必须完全退化为恒等

日频冠军（本平台至今的配置）下，持仓指令返回空字符串、
持仓状态机每天都是「调仓日」。**出单与今天之前逐字相同**。
有测试钉死——加了持仓层却静默改写了全部历史行为，是最难发现的一种回归。

## 坏状态 = 没有状态

`holdings.json` 读不出来、缺字段、配置对不上，一律按「首次建仓」处理。
宁可多付一次成本，也不能拿着半截状态继续算换手：
那样算出来的换手率是错的，而报告上一切正常。

与 `aplus/rebalance.py` 的关系：那边管**回测**里的频率与缓冲区算法，
这边管**实盘**的跨日状态。缓冲区判据复用 `buffer_hold` / `fill_to_top_n`，
不重复实现。
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_NAME = "holdings.json"

REQUIRED_KEYS = ("as_of", "days_since_rebalance", "every", "up", "down")


def state_path(reports_dir) -> Path:
    return Path(reports_dir) / STATE_NAME


def load_state(reports_dir) -> dict | None:
    """读持仓状态。**任何异常都返回 `None`**（= 首次建仓）。

    不抛错的理由和台账一样：状态是辅助信息，
    让每日出单挂掉比多付一次成本贵得多。
    但**不能**静默当成「一切正常」——调用方拿到 `None` 会重建仓仓并留痕。
    """
    p = state_path(reports_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or any(k not in data for k in REQUIRED_KEYS):
        return None
    if not isinstance(data["days_since_rebalance"], int) \
            or isinstance(data["days_since_rebalance"], bool) \
            or data["days_since_rebalance"] < 0:
        return None
    if not isinstance(data["every"], int) or isinstance(data["every"], bool) \
            or data["every"] < 1:
        return None
    if not isinstance(data["up"], list) or not isinstance(data["down"], list):
        return None
    return data


def save_state(reports_dir, state: dict) -> None:
    """落盘持仓状态。参数非法**当场报错**——写进去之后每天都会读到它。

    ## 旧的一天不覆盖新的一天

    `scripts/guard_daily.py` 补跑历史日期时会再跑一遍出单，那一遍的 `as_of`
    比现有状态**早**。无脑覆盖会让持仓状态往回退，而用户看到的是今天的名单——
    下一次 `is_due` 拿三天前的计数去算今天该不该调仓。

    这不是理论：`guard` 的存在意义就是补跑。
    """
    every = int(state.get("every", 1))
    if every < 1:
        raise ValueError(f"every 至少为 1，收到 {state.get('every')!r}")
    d = state.get("days_since_rebalance")
    if isinstance(d, bool) or not isinstance(d, int) or d < 0:
        raise ValueError(f"days_since_rebalance 必须是非负整数，收到 {d!r}")

    new_on = str(state.get("as_of") or "")
    cur = load_state(reports_dir)
    if cur and new_on and str(cur.get("as_of") or "") > new_on:
        return          # 补跑历史日期，不倒退

    out = {
        "as_of": state.get("as_of"),
        "days_since_rebalance": d,
        "every": every,
        "buffer_multiple": state.get("buffer_multiple"),
        "up": list(state.get("up") or []),
        "down": list(state.get("down") or []),
        "up_names": list(state.get("up_names") or []),
        "down_names": list(state.get("down_names") or []),
    }
    p = state_path(reports_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再改名：状态是跨日文件，半截内容会让下一天算错换手。
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def is_due(state, every: int = 1, buffer_multiple: float | None = None) -> bool:
    """今天该不该重新选篮子。

    四种情况必须调仓：
    1. 没有历史状态（首次）
    2. `every <= 1`——日频恒为调仓日（恒等路径）
    3. 距上次调仓已满 `every` 个交易日
    4. **配置变了**（频率或缓冲不同）——旧持仓是按旧配置挑的，
       继续拿着就是拿一套策略的仓位去跑另一套策略
    """
    every = max(int(every or 1), 1)
    if every <= 1:
        return True
    if not state:
        return True
    if int(state.get("every", 1)) != every:
        return True
    if state.get("buffer_multiple") != buffer_multiple:
        return True
    return int(state.get("days_since_rebalance", 0)) + 1 >= every


def advance(state: dict, as_of: str) -> dict:
    """跨一个交易日后的新状态（今天恰好是调仓日时计数归零）。

    计数口径与 `aplus.rebalance.is_rebalance_day` 一致：
    调仓当天 `days_since_rebalance = 0`，其后每个交易日 +1。
    """
    every = max(int(state.get("every", 1) or 1), 1)
    nxt = int(state.get("days_since_rebalance", 0)) + 1
    out = dict(state)
    out["as_of"] = as_of
    if every <= 1 or nxt >= every:
        out["days_since_rebalance"] = 0
    else:
        out["days_since_rebalance"] = nxt
    return out


def instruction(due: bool, up: list, down: list, every: int = 1,
                 buffer_multiple: float | None = None,
                 held_up: list | None = None, held_down: list | None = None) -> str:
    """给用户看的**持仓指令**（不含推荐名单本身）。

    `every=1` 返回空字符串：日频下不需要任何额外说明，
    也不允许多出任何新东西——那会让「加了持仓层」变成一次静默的行为变更。
    """
    every = max(int(every or 1), 1)
    if every <= 1:
        return ""
    lines = []
    if due:
        n_up, n_dn = len(up or []), len(down or [])
        buf = f"，{buffer_multiple} 倍缓冲区" if buffer_multiple else ""
        lines.append(f"【持仓】今天是**调仓日**（每 {every} 个交易日一次{buf}）。")
        lines.append(f"  买入这 {n_up} 只：{' '.join(up or [])}")
        lines.append(f"  卖出/回避这 {n_dn} 只：{' '.join(down or [])}")
        lines.append(f"  名单里的**推荐**是按「下个交易日涨跌」排的，"
                     f"与调仓无关——非调仓日照样每天给你一份。")
    else:
        held_up = list(held_up or [])
        held_dn = list(held_down or [])
        lines.append(f"【持仓】今天**不是调仓日**（每 {every} 个交易日一次），"
                     f"**不动**。")
        lines.append(f"  继续持有：{' '.join(held_up) or '（空）'}")
        lines.append(f"  继续回避：{' '.join(held_dn) or '（空）'}")
        lines.append(f"  今天这份名单仍可参考——但按本配置不该据此换手，"
                     f"非调仓日换手只是在多交成本。")
    return "\n".join(lines)