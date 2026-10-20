#!/usr/bin/env python3
"""候选池 & 预测名单的**板块暴露** + **整手可买性**分层测量（10 天，涨腿/跌腿分开报）。

产物：`data/reports/pool_board_exposure_2026-10-21.json`（带 `provenance` sidecar）。

## 这个产物回答什么

1. D1 每天推的 **10 涨**里，主板 / 创业板 / 科创板各**几只**、
   各**占多少金额**（两个金额口径：①「1 手金额」②「每份 ¥5,000」）。
2. 同样口径下的 **10 跌**分布 —— **只报，不据此过滤**。
   （依据 `docs/PREREG_AFFORDABILITY_2026-10-16.md` §6.3：跌腿是回避清单、不占资金。）
3. **整手可买性按板块分层**：各板块里「1 手 ≤ ¥5,000」的比例
   （⇒ 回答「科创板是不是把可买性拖垮了」）。
4. **假设性反事实**：若涨腿只从主板+创业板取 / 只从主板取，10 只里有几只可买。

## 这个产物**不**回答什么（🚨 硬边界）

- **不回答收益**：一个字的 CAGR / 超额 / 命中率都不在这里。
  反事实那节**只是把名单重排**，**不重算收益** ⇒ 那两个数是
  **执行性读数**，**不是收益读数**。
- **不回答「科创板会跌」**：本产物量的是「买不买得起」，
  与「涨不涨」是**两个完全不同的量**。
- **不回答权限**：「科创板需 50 万 + 24 个月」是**外部常识·本仓无权限模型**，
  本产物**一个字都不判定** ⇒ 见 `docs/PREREG_POOL_BOARD_FILTER_2026-10-21.md` §4。
- **不回答「重排后 top-10 是哪 10 只」**：全池排名**没有落盘**
  （`scripts/run_daily.py:680` 只落 20 行）⇒ 重建面板是 GB 级、
  本轮（轻活 agent）禁止 ⇒ 反事实只能用「板块自身可买率」估计，
  且产物里**逐字写明**它不是重排。

## 口径（每一条都指向仓内可查的出处）

| 口径 | 取法 | 出处 |
|---|---|---|
| 板块 | **只认代码前缀**，与价格无关 | `aplus/panel.py:170`（±20% 板认 300/301/688/689） |
| 一手股数 | `688/689` = **200 股**；其余 = **100 股** | `aplus/webapp.py::lot_shares_for`、`scripts/measure_deployed_d2.py::lot_shares` |
| 价格 | 主库 `master/daily/{code}.parquet` 的 `close`，**取 `date <= pred_date` 的最后一根 bar** | `aplus/webapp.py::close_as_of`；`docs/PREREG_AFFORDABILITY_2026-10-16.md` §4 |
| 复权 | **不复权**（判「买不买得起」用当日真实成交价） | `scripts/lot_affordability.py` 模块 docstring |
| 每份金额 | **¥5,000**（= 5 万 ÷ 10 涨） | `aplus/alloc.py` `per_stock_cap = 0.10` |
| 本金 | **¥50,000** | 用户原文（`PREREG_AFFORDABILITY` §2） |

## 🚨 缺价 = **显式缺失**，不是 0

`lot_cost` 在缺价时返回 `None`（不是 0、不是 inf）⇒ 缺价的那只
**不进金额分母**，但在 `n_price_missing` 里**点名**。
（"判不出"不许被读成 "免费" —— `scripts/measure_deployed_d2.py::lot_cost` 同款纪律。）
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402

GENERATOR = "scripts/measure_pool_board_exposure.py"

#: 本金与每份金额（**不许手打进判据**；但这两条是**输入参数**，不是推导出来的门槛）。
CAPITAL = 50_000.0
SLOT_BUDGET = 5_000.0          # = CAPITAL / 10（10 涨腿等权一份）

#: 板块 → 代码前缀。🚨 **只认前缀，与价格无关**
#: （`aplus/webapp.py:456-460` 逐字：「不许用『价格高就 200 股』这种推断」）。
#: ⚠️ `302`（池里 1 只）**不在** `aplus/panel.py:170` 的 ±20% 板名单里
#: ⇒ 本产物按**仓库自身规则**把它归 `OTHER` 并单列，不影响任何比例。
#:
#: ⚠️ 前缀**长度不一**（北交所是 2 段 `43/83/87/88`，`920` 是 3 段）⇒
#: 匹配必须用 `startswith`（取头 6 位数字），**不许**写成「取前 3 位的字典查表」
#: —— 那会让北交所**全部落到 OTHER**（仓里 0 只 ⇒ 这个错**静默**）。
BOARD_PREFIX_RULES: dict[str, tuple[str, ...]] = {
    "STAR": ("688", "689"),                       # 科创板（含 CDR）
    "CHINEXT": ("300", "301"),                    # 创业板
    "SH_MAIN": ("600", "601", "603", "605"),      # 沪主板
    "SZ_MAIN": ("000", "001", "002", "003"),      # 深主板
    "BSE": ("920", "43", "83", "87", "88"),       # 北交所（本仓 0 只）
}
BOARD_ORDER = ("SH_MAIN", "SZ_MAIN", "CHINEXT", "STAR", "BSE", "OTHER")

#: 一手股数。🚨 **科创板 200 股是交易所规则，不是本仓数据里的数** ——
#: 本仓只能证明「688/689 是科创板」，「起买 200 股」**没有仓内数据源**
#: ⇒ 写成显式常量并在产物 `caliber` 里**自报证据等级**。
#: （与 `scripts/measure_deployed_d2.py:82-86` 逐字同款理由。）
LOT_SHARES_STAR = 200
LOT_SHARES_DEFAULT = 100

#: 反事实的两个限制集合（**各是一个板集合，不是"成长板"**）。
#: 🚨 创业板与科创板**不许合并**：科创板权限门槛与创业板不同（外部常识·未核实）。
COUNTERFACTUAL_SETS: dict[str, tuple[str, ...]] = {
    "main_plus_chinext": ("SH_MAIN", "SZ_MAIN", "CHINEXT"),
    "main_only": ("SH_MAIN", "SZ_MAIN"),
}

#: 可买性三态（**A / B 是两个不同的量，不许合并成一个"买不起率"** ——
#: `docs/PREREG_AFFORDABILITY_2026-10-16.md` §2）。
AFFORD_OK = "OK"                 # 1 手 ≤ 每份金额（¥5,000）
AFFORD_B_SLOT = "B_SLOT"         # 每份不够，但 ≤ 全部本金 ⇒ 「得集中」，不是「买不了」
AFFORD_A_PHYSICAL = "A_PHYSICAL"  # 1 手 > 全部本金 ⇒ 物理不可能
AFFORD_PRICE_MISSING = "PRICE_MISSING"


# ── 纯函数（可 import、可测）──────────────────────────────────────────

def board_of(code: str | None) -> str:
    """该票属于哪块板。**只认代码前缀**，与价格无关。

    未知前缀 ⇒ `"OTHER"`（**不抛异常、不回落成某个默认板**）：
    一个认不出的票必须**显式落到 OTHER** 并被计数，而不是悄悄算进主板。

    ⚠️ 匹配用 **`startswith`**（前缀长度 2 或 3 都有：北交所 `43/83/87/88`、
    `920`）⇒ 一次「取前 3 位查表」会把北交所**全部**打成 OTHER
    （本仓北交所 0 只 ⇒ 这个错**静默**，只有参数化夹具能抓住）。
    """
    head = str(code or "").strip().split(".")[0]
    if len(head) < 3:
        return "OTHER"
    for board, prefixes in BOARD_PREFIX_RULES.items():
        for p in prefixes:
            if head.startswith(p):
                return board
    return "OTHER"


def lot_shares_for(code: str | None) -> int:
    """一手多少股：**688/689 = 200，其余 = 100**。"""
    return LOT_SHARES_STAR if board_of(code) == "STAR" else LOT_SHARES_DEFAULT


def lot_cost(close: float | None, code: str | None) -> float | None:
    """**1 手的金额** = 成交价 × 起买股数。

    🚨 **缺价 ⇒ `None`**（不是 0、不是 inf、不是 0.0）：
    「判不出」不许被读成「免费」，也不许被读成「买不起」
    —— 它是**第三种状态**，在产物里单列 `n_price_missing`。
    """
    if close is None:
        return None
    try:
        px = float(close)
    except (TypeError, ValueError):
        return None
    if px != px or px <= 0:          # NaN / 非正 ⇒ 缺价
        return None
    return px * lot_shares_for(code)


def classify_affordability(lc: float | None, slot_budget: float = SLOT_BUDGET,
                            capital: float = CAPITAL) -> str:
    """1 手金额落在哪一档。

    - `OK`      ：≤ 每份金额（¥5,000）⇒ 照等权买得起
    - `B_SLOT`  ：每份不够、但 ≤ 全部本金 ⇒ **分配不够**（得集中），**不是买不了**
    - `A_PHYSICAL`：> 全部本金 ⇒ **物理不可能**（与怎么分配无关）
    - `PRICE_MISSING`：缺价 ⇒ **判不出**，不许归到任何一档
    """
    if lc is None:
        return AFFORD_PRICE_MISSING
    if lc <= slot_budget:
        return AFFORD_OK
    if lc <= capital:
        return AFFORD_B_SLOT
    return AFFORD_A_PHYSICAL


def as_of_index(dates: list[str], as_of: str) -> int | None:
    """`dates`（**已升序**）里最后一个 `date <= as_of` 的下标；没有 ⇒ `None`。

    🚨 **不造价**：主库里没有 ≤ as_of 的 bar 就是**缺价**，
    不许回落到「最新一根」——那会把未来价当成当天价。
    """
    i = bisect.bisect_right(dates, as_of) - 1
    return i if i >= 0 else None


def board_rollup(rows: list[dict], slot_budget: float = SLOT_BUDGET) -> dict:
    """一条腿（up 或 down）按板块汇总：只数 + 两个金额口径。

    `rows` 每条至少有 `code` / `close` / `lot_cost`（可能 `None`）。

    ## 两个金额口径，**不是同一个量**

    - `lot_cost_sum`：若每只都买 1 手，各板**要花多少钱**（Σ 1 手金额）。
    - `budget_capped_spend`：每只**最多只掏一份预算 ¥5,000**
      （买得起就掏 1 手的价钱，买不起就那一份闲置）⇒ 各板**实际吸走多少现金**。
      总和 **≤ 5 万**，差额 = **闲置现金**（**不是收益**，是仓位规模）。

    ⚠️ `lot_cost_share` 与 `budget_capped_share` **不是同一个比值**
    ⇒ 不许只报一个，也不许把两个加起来当同一个 100%。
    """
    per: dict[str, dict] = {}
    for b in BOARD_ORDER:
        per[b] = {
            "n": 0, "n_priced": 0, "n_price_missing": 0,
            "lot_cost_sum": 0.0, "budget_capped_spend": 0.0,
            "n_afford_OK": 0, "n_afford_B_SLOT": 0, "n_afford_A_PHYSICAL": 0,
        }
    for r in rows:
        b = r["board"]
        cell = per.setdefault(b, dict(per["OTHER"]))
        cell["n"] += 1
        lc = r["lot_cost"]
        if lc is None:
            cell["n_price_missing"] += 1
            continue
        cell["n_priced"] += 1
        cell["lot_cost_sum"] += lc
        cell["budget_capped_spend"] += min(lc, slot_budget)
        st = classify_affordability(lc, slot_budget)
        if st == AFFORD_OK:
            cell["n_afford_OK"] += 1
        elif st == AFFORD_B_SLOT:
            cell["n_afford_B_SLOT"] += 1
        elif st == AFFORD_A_PHYSICAL:
            cell["n_afford_A_PHYSICAL"] += 1

    tot_lc = sum(c["lot_cost_sum"] for c in per.values())
    tot_sp = sum(c["budget_capped_spend"] for c in per.values())
    for c in per.values():
        c["lot_cost_share"] = (c["lot_cost_sum"] / tot_lc) if tot_lc > 0 else None
        c["budget_capped_share"] = (c["budget_capped_spend"] / tot_sp) if tot_sp > 0 else None
    n_priced = sum(c["n_priced"] for c in per.values())
    n_aff_ok = sum(c["n_afford_OK"] for c in per.values())
    return {
        "n": sum(c["n"] for c in per.values()),
        "n_priced": n_priced,
        "n_price_missing": sum(c["n_price_missing"] for c in per.values()),
        "by_board": {b: per[b] for b in per if per[b]["n"] > 0},
        "totals": {
            "lot_cost_sum": tot_lc,
            "budget_capped_spend": tot_sp,
            # 闲置 = 5 万 − 实际吸走的现金。**不是收益，是仓位规模。**
            "idle_cash": max(0.0, CAPITAL - tot_sp),
            "n_afford_OK": n_aff_ok,
            "n_afford_B_SLOT": sum(c["n_afford_B_SLOT"] for c in per.values()),
            "n_afford_A_PHYSICAL": sum(c["n_afford_A_PHYSICAL"] for c in per.values()),
            "n_afford_PRICE_MISSING": sum(c["n_price_missing"] for c in per.values()),
        },
    }


# ── 读盘 ────────────────────────────────────────────────────────────

def _read_dates_closes(path: Path) -> tuple[list[str], list[float | None]]:
    """读一只票的 `(date, close)`；`close` 非正/NaN ⇒ `None`（缺价，不是 0）。"""
    t = pq.read_table(path, columns=["date", "close"])
    dates = [str(x) for x in t.column("date").to_pylist()]
    raw = t.column("close").to_pylist()
    closes: list[float | None] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            closes.append(None)
            continue
        closes.append(f if (f == f and f > 0) else None)
    return dates, closes


def collect_pool(daily_dir: Path, pred_dates: list[str],
                 keep_codes: set[str] | None = None) -> tuple[dict, dict]:
    """扫全池，逐日算**每块板**的规模与整手可买性。

    返回 `(pool_by_date, leg_price_lookup, pool_size_by_board)`：
    - `pool_by_date[date][board] = {n, n_priced, n_price_missing, n_afford_OK, n_afford_B_SLOT, n_afford_A_PHYSICAL, lot_cost_sum}`
    - `leg_price_lookup[code] = {date: (close, bar_date)}`（**只留 `keep_codes` 里那些票**）
    - `pool_size_by_board[board]` = **该板的股票只数**（**不随天数放大**）

    ⚠️ **价格缺失的三种来源都进 `n_price_missing`**：文件读不出来、
    该日之前一根 bar 都没有、bar 的 close 是 NaN/非正。
    """
    counts: dict = {d: {b: {"n": 0, "n_priced": 0, "n_price_missing": 0,
                            "n_afford_OK": 0, "n_afford_B_SLOT": 0,
                            "n_afford_A_PHYSICAL": 0, "lot_cost_sum": 0.0}
                        for b in BOARD_ORDER}
                    for d in pred_dates}
    pool_size_by_board: dict[str, int] = {b: 0 for b in BOARD_ORDER}
    prices: dict = {}
    files = sorted(daily_dir.glob("*.parquet"))
    for f in files:
        code = f.stem
        b = board_of(code)
        pool_size_by_board[b] = pool_size_by_board.get(b, 0) + 1
        try:
            dates, closes = _read_dates_closes(f)
        except Exception:
            # 单只读不出来 ⇒ 该票在**每一天**都算缺价（不许编价）
            for d in pred_dates:
                counts[d][b]["n"] += 1
                counts[d][b]["n_price_missing"] += 1
            continue
        for d in pred_dates:
            cell = counts[d][b]
            cell["n"] += 1
            i = as_of_index(dates, d)
            close = closes[i] if i is not None else None
            if close is None:
                cell["n_price_missing"] += 1
                continue
            cell["n_priced"] += 1
            lc = lot_cost(close, code)
            cell["lot_cost_sum"] += lc
            st = classify_affordability(lc)
            if st == AFFORD_OK:
                cell["n_afford_OK"] += 1
            elif st == AFFORD_B_SLOT:
                cell["n_afford_B_SLOT"] += 1
            else:
                cell["n_afford_A_PHYSICAL"] += 1
        if keep_codes is not None and code not in keep_codes:
            continue
        per_code: dict[str, tuple] = {}
        for d in pred_dates:
            i = as_of_index(dates, d)
            per_code[d] = (closes[i] if i is not None else None,
                           dates[i] if i is not None else None)
        prices[code] = per_code
    return counts, prices, pool_size_by_board


def load_legs(pred_dir: Path) -> list[dict]:
    """读 `pred_*.parquet`（每个 20 行 = up 10 / down 10），按日期排序返回。"""
    out = []
    for p in sorted(pred_dir.glob("pred_*.parquet")):
        t = pq.read_table(p)
        cols = t.column_names
        data = {c: t.column(c).to_pylist() for c in cols}
        n = t.num_rows
        pred_date = str(data["pred_date"][0])
        for i in range(n):
            out.append({
                "pred_date": pred_date,
                "file": p.name,
                "code": str(data["code"][i]),
                "name": str(data["name"][i]) if "name" in data else "",
                "direction": str(data["direction"][i]),
                "score": float(data["score"][i]) if data["score"][i] is not None else None,
            })
    return out


# ── 反事实 ──────────────────────────────────────────────────────────

def counterfactual(leg_rows: list[dict], pool_cell: dict) -> dict:
    """若涨腿**只从某个板集合取**，10 只里有几只可买？

    ## 🚨 这**不是**「把 top-10 重排」—— 原因与替代做法

    全池排名**没有落盘**（`scripts/run_daily.py:680` 只落 20 行），
    重排 top-10 需要重建 GB 级面板 ⇒ 本轮（轻活 agent）**不做**。

    ⇒ 本函数给的是**三段**：
    1. **exact**：当前涨腿里**已经**属于该板集合的那几只 ⇒
       它们**必定**出现在重排后的名单里（它们是全池前 10），
       买得起几只 = **精确已知**。
    2. **best_case / worst_case**：剩下要补的名额，用该板集合**池子**里
       最贵/最便宜的来填 ⇒ **硬上下界**。
    3. **expected**：用该板集合**自身**的历史可买率填 ⇒ **估计**，
       **不是重排结果**。

    ⚠️ 这三个数**都不是收益读数**，也不许被当成「剔池后策略会变好/变坏」。
    """
    n_slots = len(leg_rows)
    out = {}
    for set_name, boards in COUNTERFACTUAL_SETS.items():
        bset = set(boards)
        known = [r for r in leg_rows if r["board"] in bset]
        n_known = len(known)
        n_known_ok = sum(1 for r in known if r["afford"] == AFFORD_OK)
        n_fill = max(0, n_slots - n_known)
        pool_n = sum(pool_cell.get(b, {}).get("n_priced", 0) for b in boards)
        pool_ok = sum(pool_cell.get(b, {}).get("n_afford_OK", 0) for b in boards)
        pool_bad = sum(pool_cell.get(b, {}).get("n_afford_B_SLOT", 0)
                       + pool_cell.get(b, {}).get("n_afford_A_PHYSICAL", 0) for b in boards)
        rate = (pool_ok / pool_n) if pool_n else None
        out[set_name] = {
            "boards": list(boards),
            "n_current_up_in_set": n_known,
            "n_current_up_affordable_OK": n_known_ok,
            "n_slots_to_fill": n_fill,
            "pool_n_priced": pool_n,
            "pool_n_affordable_OK": pool_ok,
            "pool_affordable_rate": rate,
            "expected_n_affordable": (None if rate is None else n_known_ok + n_fill * rate),
            "best_case_n_affordable": min(n_slots, n_known_ok + min(n_fill, pool_ok)),
            "worst_case_n_affordable": max(0, n_known_ok + max(0, n_fill - pool_bad)),
            "估计方法": ("expected = 已知部分（精确）+ 待补名额 × 该板集合池自身的可买率；"
                         "**不是** top-10 重排结果（全池排名未落盘）"),
            "已知部分为何精确": ("当前涨腿是**全池前 10** ⇒ 落在该板集合里的那几只**分数高于"
                                 "所有集合外的名字** ⇒ 重排后**必定**还在前 10 内。"),
            "估计的偏差方向": ("⚠️ **估计的偏差方向未知**：待补名额来自**全池前 10 之外**的名字，"
                              "而池可买率是**全池平均** ⇒ 若高分名字更偏贵，真实值低于 expected；"
                              "反之高于。**不许把 expected 当点估计引用**。"),
        }
    return out


# ── 主流程 ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # 🚨 第 125 层：盖章要的是「这次跑真正跑的那些字节」⇒ **第一条可执行语句**就抓。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    p = argparse.ArgumentParser(description="候选池/名单的板块暴露 + 整手可买性")
    p.add_argument("--data-root", type=Path, default=None)
    p.add_argument("--pred-dir", type=Path, default=None)
    p.add_argument("--out", type=Path,
                   default=ROOT / "data" / "reports" / "pool_board_exposure_2026-10-21.json")
    args = p.parse_args(argv)

    data_root = args.data_root or ROOT / "data"
    daily_dir = data_root / "master" / "daily"
    pred_dir = args.pred_dir or ROOT / "data" / "reports" / "predictions"
    if not daily_dir.is_dir():
        print(f"🚨 主库目录不存在：{daily_dir}", file=sys.stderr)
        return 2
    if not pred_dir.is_dir():
        print(f"🚨 预测产物目录不存在：{pred_dir}", file=sys.stderr)
        return 2

    t0 = time.time()
    legs = load_legs(pred_dir)
    if not legs:
        print("🚨 没有 pred_*.parquet", file=sys.stderr)
        return 2
    pred_dates = sorted({r["pred_date"] for r in legs})

    pool_by_date, pool_prices, pool_size_by_board = collect_pool(
        daily_dir, pred_dates, keep_codes={r["code"] for r in legs})

    days = []
    for d in pred_dates:
        day_rows = [r for r in legs if r["pred_date"] == d]
        for r in day_rows:
            close, bar_date = pool_prices.get(r["code"], {}).get(d, (None, None))
            r["close"] = close
            r["bar_date"] = bar_date
            r["board"] = board_of(r["code"])
            r["lot_shares"] = lot_shares_for(r["code"])
            r["lot_cost"] = lot_cost(close, r["code"])
            r["afford"] = classify_affordability(r["lot_cost"])
        up = [r for r in day_rows if r["direction"] == "up"]
        down = [r for r in day_rows if r["direction"] == "down"]
        days.append({
            "pred_date": d,
            "source_file": f"pred_{d}.parquet",
            "up": board_rollup(up),
            "down": board_rollup(down),
            "up_items": [{
                "code": r["code"], "name": r["name"], "board": r["board"],
                "close": r["close"], "bar_date": r["bar_date"],
                "lot_shares": r["lot_shares"], "lot_cost": r["lot_cost"],
                "affordability": r["afford"],
                "shortfall_at_5000": (None if r["lot_cost"] is None
                                      else max(0.0, r["lot_cost"] - SLOT_BUDGET)),
                "is_A_physical": r["afford"] == AFFORD_A_PHYSICAL,
            } for r in up],
            "counterfactual_up": counterfactual(up, pool_by_date[d]),
        })

    # ── 汇总 ──
    # ⚠️ `agg_boards` 是**观测数**（股票 × 天），**不是股票只数** ⇒
    #    「池子占多少只」必须用 `pool_size_by_board`（只数），两者不许混（第 7 层）。
    agg_boards: dict[str, dict] = {b: {"n_obs": 0, "n_obs_priced": 0, "n_obs_price_missing": 0,
                                        "n_afford_OK": 0, "n_afford_B_SLOT": 0,
                                        "n_afford_A_PHYSICAL": 0, "lot_cost_sum": 0.0}
                                    for b in BOARD_ORDER}
    for d in pred_dates:
        for b, cell in pool_by_date[d].items():
            agg_boards[b]["n_obs"] += cell["n"]
            agg_boards[b]["n_obs_priced"] += cell["n_priced"]
            agg_boards[b]["n_obs_price_missing"] += cell["n_price_missing"]
            agg_boards[b]["n_afford_OK"] += cell["n_afford_OK"]
            agg_boards[b]["n_afford_B_SLOT"] += cell["n_afford_B_SLOT"]
            agg_boards[b]["n_afford_A_PHYSICAL"] += cell["n_afford_A_PHYSICAL"]
            agg_boards[b]["lot_cost_sum"] += cell["lot_cost_sum"]
    pool_total = sum(pool_size_by_board.values())
    for b, cell in agg_boards.items():
        cell["afford_OK_ratio"] = (cell["n_afford_OK"] / cell["n_obs_priced"]) if cell["n_obs_priced"] else None
        cell["n_stocks"] = pool_size_by_board.get(b, 0)
        cell["share_of_pool_stocks"] = ((cell["n_stocks"] / pool_total) if pool_total else None)

    up_agg: dict[str, dict] = {b: {"n": 0, "n_priced": 0, "n_price_missing": 0,
                                    "n_afford_OK": 0, "n_afford_B_SLOT": 0,
                                    "n_afford_A_PHYSICAL": 0, "lot_cost_sum": 0.0,
                                    "budget_capped_spend": 0.0}
                                for b in BOARD_ORDER}
    down_agg = {b: dict(v) for b, v in up_agg.items()}
    for day in days:
        for leg, agg in (("up", up_agg), ("down", down_agg)):
            for b, c in day[leg]["by_board"].items():
                cell = agg.setdefault(b, dict(up_agg["OTHER"]))
                for k in ("n", "n_priced", "n_price_missing", "n_afford_OK",
                          "n_afford_B_SLOT", "n_afford_A_PHYSICAL",
                          "lot_cost_sum", "budget_capped_spend"):
                    cell[k] += c[k]
    for agg in (up_agg, down_agg):
        tot_lc = sum(c["lot_cost_sum"] for c in agg.values())
        tot_sp = sum(c["budget_capped_spend"] for c in agg.values())
        for c in agg.values():
            c["lot_cost_share"] = (c["lot_cost_sum"] / tot_lc) if tot_lc > 0 else None
            c["budget_capped_share"] = (c["budget_capped_spend"] / tot_sp) if tot_sp > 0 else None
            c["afford_OK_ratio"] = (c["n_afford_OK"] / c["n_priced"]) if c["n_priced"] else None

    def _agg_total(agg: dict) -> dict:
        n_priced = sum(c["n_priced"] for c in agg.values())
        return {
            "n_slots": sum(c["n"] for c in agg.values()),
            "n_priced": n_priced,
            "n_price_missing": sum(c["n_price_missing"] for c in agg.values()),
            "n_afford_OK": sum(c["n_afford_OK"] for c in agg.values()),
            "n_afford_B_SLOT": sum(c["n_afford_B_SLOT"] for c in agg.values()),
            "n_afford_A_PHYSICAL": sum(c["n_afford_A_PHYSICAL"] for c in agg.values()),
            "afford_OK_ratio": (sum(c["n_afford_OK"] for c in agg.values()) / n_priced) if n_priced else None,
            "lot_cost_sum": sum(c["lot_cost_sum"] for c in agg.values()),
            "budget_capped_spend": sum(c["budget_capped_spend"] for c in agg.values()),
        }

    out = {
        "caliber": {
            "title": "候选池与预测名单的板块暴露 + 整手可买性（**只量可执行性，不量收益**）",
            "generated_for": "docs/PREREG_POOL_BOARD_FILTER_2026-10-21.md",
            "capital": CAPITAL,
            "slot_budget_per_name": SLOT_BUDGET,
            "slot_budget_note": "= ¥50,000 ÷ 10（10 涨腿等权一份）＝ aplus/alloc.py per_stock_cap 0.10",
            "board_rule": {
                "by": "**只认代码前缀**，与价格无关（aplus/webapp.py:456-460 逐字禁止「价格高就 200 股」那种推断）",
                "map": {b: list(pfx) for b, pfx in BOARD_PREFIX_RULES.items()},
                "other_note": ("`302`（池里 1 只）**不在** `aplus/panel.py:170` 的 ±20% 板名单"
                               "（300/301/688/689）里 ⇒ 按仓库自身规则归 OTHER 并单列。"),
            },
            "lot_rule": {
                "rule": "688/689 = **200 股**一手；其余（主板/创业板/北交所）= **100 股**一手",
                "evidence_level": ("⚠️ **外部交易所规则 · 本仓无数据源**。本仓只能证明「688/689 是科创板」"
                                   "（aplus/panel.py::price_limit 认这两个前缀）；"
                                   "「起买 200 股」**没有仓内数据源** ⇒ 写成显式常量并在此自报证据等级。"),
                "same_rule_in_repo": ["aplus/webapp.py::lot_shares_for",
                                      "scripts/measure_deployed_d2.py::lot_shares"],
            },
            "price_rule": {
                "source": "主库 data/master/daily/{code}.parquet 的 close 列",
                "as_of": "**取 date <= pred_date 的最后一根 bar**（出单时刻真正看得见的价）",
                "adjusted": "**不复权**（判「买不买得起」用当日真实成交价；判「收益」才用后复权）",
                "missing": "**缺价 ⇒ 显式缺失**（`n_price_missing`），**不编 0**、不当成「免费」",
            },
            "down_leg_scope": ("**跌腿只报、不据此过滤**：跌腿是**回避清单**、不占资金"
                               "（docs/PREREG_AFFORDABILITY_2026-10-16.md §6 第 3 条；"
                               "A 股散户融不到券）。⇒ 板块/权限/整手约束**只作用于涨腿**。"),
            "permission": ("**本产物一个字都不判定权限**：「科创板需 50 万 + 24 个月、"
                           "创业板需 10 万 + 24 个月」是**外部常识·本仓无权限模型**"
                           "（`grep -rn '开通权限\\|交易权限' aplus/ scripts/` 命中 0）"
                           "⇒ 见 PREREG §4 的条件分支。"),
            "not_registered_in_default_generators": ("本脚本**未**登记进 `aplus/provenance.py::DEFAULT_GENERATORS`"
                                                    "（本轮不改 `aplus/` 下任何模块）"
                                                    "⇒ 引用本产物时须**显式传 generator**。"),
        },
        "inputs": {
            "prediction_dir": str(pred_dir),
            "n_prediction_files": len({r["file"] for r in legs}),
            "pred_dates": pred_dates,
            "daily_dir": str(daily_dir),
            "pool_size": pool_total,
            "n_rows_read": sum(1 for _ in daily_dir.glob("*.parquet")),
        },
        "pool_composition": {
            "note": ("**股票只数**（不随天数放大）。⚠️ 与 `pool_affordability_by_board.aggregate`"
                     "的**观测数**（股票 × 天）**不是同一个量**（第 7 层）⇒ 不许混引。"),
            "by_board": {b: {
                "n_stocks": pool_size_by_board.get(b, 0),
                "share_of_pool_stocks": ((pool_size_by_board.get(b, 0) / pool_total) if pool_total else None),
            } for b in BOARD_ORDER if pool_size_by_board.get(b, 0) > 0},
            "n_pool_total": pool_total,
            "prefix_to_board": {p: b for b, pfx in BOARD_PREFIX_RULES.items() for p in pfx},
        },
        "pool_affordability_by_board": {
            "note": ("各板在**每一天**的「1 手 ≤ ¥5,000」比例（分母 = 该板**当日有价**的只数）。"
                     "`aggregate` 的分母是**观测数**（股票 × 天），**不是股票只数**。"),
            "per_date": {d: {b: {
                "n": c["n"], "n_priced": c["n_priced"], "n_price_missing": c["n_price_missing"],
                "n_afford_OK": c["n_afford_OK"],
                "afford_OK_ratio": (c["n_afford_OK"] / c["n_priced"]) if c["n_priced"] else None,
                "n_afford_B_SLOT": c["n_afford_B_SLOT"],
                "n_afford_A_PHYSICAL": c["n_afford_A_PHYSICAL"],
            } for b, c in pool_by_date[d].items() if c["n"] > 0} for d in pred_dates},
            "aggregate": {b: {
                "n_obs": c["n_obs"], "n_obs_priced": c["n_obs_priced"],
                "n_obs_price_missing": c["n_obs_price_missing"],
                "n_stocks": c["n_stocks"],
                "n_afford_OK": c["n_afford_OK"],
                "afford_OK_ratio": c["afford_OK_ratio"],
                "n_afford_B_SLOT": c["n_afford_B_SLOT"],
                "n_afford_A_PHYSICAL": c["n_afford_A_PHYSICAL"],
            } for b, c in agg_boards.items() if c["n_obs"] > 0},
        },
        "up_leg_aggregate": {
            "by_board": {b: c for b, c in up_agg.items() if c["n"] > 0},
            "totals": _agg_total(up_agg),
        },
        "down_leg_aggregate": {
            "by_board": {b: c for b, c in down_agg.items() if c["n"] > 0},
            "totals": _agg_total(down_agg),
            "scope": "**只报，不据此过滤**（跌腿是回避清单、不占资金）",
        },
        "days": days,
        "what_this_cannot_prove": [
            "**不回答涨不涨**——本产物一个字的 CAGR / 超额 / 命中率都没有。"
            "「科创板买不进」与「科创板会跌」是**两个完全不同的量**。",
            "**反事实那节不是收益读数**——它只把名单重排（且因全池排名未落盘，"
            "连重排都只能给区间与估计），**不重算任何收益**。",
            "**不回答权限**——50 万/10 万门槛是外部常识、本仓无权限模型 ⇒ "
            "「用户有没有科创板权限」是**必须人工确认**的一项。",
            "**不回答剔池后策略会不会更好/更差**——那需要回测（回测会把候选池改掉，"
            "而 `verification.parquet` 是冻结的前瞻实验，改名单 = 污染它）。",
            "**「可买率低」不等于「会亏」**——它只说 5 万买不进这些名字。",
        ],
        "verdict_about_board_exposure": {
            "scope": "**只关于可执行性**（买不买得起），**不含任何收益判断**",
            "headline": None,   # 下面填
        },
        "runtime_seconds": None,
    }

    # ── 头条（算出来，不手打）─────────────────────────────────────────
    star = up_agg.get("STAR", {})
    mainb = {b: up_agg.get(b, {}) for b in ("SH_MAIN", "SZ_MAIN")}
    main_n = sum(c.get("n", 0) for c in mainb.values())
    main_ok = sum(c.get("n_afford_OK", 0) for c in mainb.values())
    main_priced = sum(c.get("n_priced", 0) for c in mainb.values())
    chex = up_agg.get("CHINEXT", {})
    tot = out["up_leg_aggregate"]["totals"]
    out["verdict_about_board_exposure"]["headline"] = {
        "up_slots_total": tot["n_slots"],
        "up_n_afford_OK": tot["n_afford_OK"],
        "up_afford_OK_ratio": tot["afford_OK_ratio"],
        "up_A_physical": tot["n_afford_A_PHYSICAL"],
        "up_B_slot": tot["n_afford_B_SLOT"],
        "star_n_in_up": star.get("n", 0),
        "star_share_of_up_slots": (star.get("n", 0) / tot["n_slots"]) if tot["n_slots"] else None,
        "star_share_of_up_lot_cost": star.get("lot_cost_share"),
        "star_share_of_up_budget_capped_spend": star.get("budget_capped_share"),
        "chinext_n_in_up": chex.get("n", 0),
        "main_board_n_in_up": main_n,
        "pool_star_afford_OK_ratio": agg_boards.get("STAR", {}).get("afford_OK_ratio"),
        "pool_main_board_afford_OK_ratio": (
            (sum(agg_boards[b]["n_afford_OK"] for b in ("SH_MAIN", "SZ_MAIN"))
             / sum(agg_boards[b]["n_obs_priced"] for b in ("SH_MAIN", "SZ_MAIN")))
            if sum(agg_boards[b]["n_obs_priced"] for b in ("SH_MAIN", "SZ_MAIN")) else None),
        "up_main_board_afford_OK_ratio": (main_ok / main_priced) if main_priced else None,
        "三档分开": ("**物理买不进（A）** = 1 手 > 全部本金（与怎么分配无关）；"
                    "**分配不够（B）** = 1 手 > 每份 ¥5,000 但 ≤ 本金（得集中，不是买不了）；"
                    "**权限未知** = 外部常识·本仓无模型 ⇒ 单列，不与前两者合并。"),
    }
    out["runtime_seconds"] = round(time.time() - t0, 2)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT,
        data_root=data_root, expect_bytes=expect_bytes,
        extra={
            "capital": CAPITAL,
            "slot_budget_per_name": SLOT_BUDGET,
            "n_prediction_files": out["inputs"]["n_prediction_files"],
            "pred_dates": pred_dates,
            "pool_size": pool_total,
            "lot_shares_STAR": LOT_SHARES_STAR,
            "lot_shares_default": LOT_SHARES_DEFAULT,
        },
    )
    print(f"写入 {args.out}（{out['runtime_seconds']}s）")
    h = out["verdict_about_board_exposure"]["headline"]
    print(f"  涨腿 {h['up_slots_total']} 只里：科创板 {h['star_n_in_up']} 只 "
          f"({h['star_share_of_up_slots']:.1%} of slots, "
          f"{h['star_share_of_up_lot_cost']:.1%} of 1手金额)")
    print(f"  涨腿买得起（1 手 ≤ ¥5,000）：{h['up_n_afford_OK']}/{tot['n_priced']} "
          f"= {h['up_afford_OK_ratio']:.1%}｜A 物理 {h['up_A_physical']}｜B 分配 {h['up_B_slot']}")
    print(f"  池子可买率：科创板 {h['pool_star_afford_OK_ratio']:.1%} vs "
          f"主板 {h['pool_main_board_afford_OK_ratio']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
