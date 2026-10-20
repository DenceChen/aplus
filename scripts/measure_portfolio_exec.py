"""组合**资金可执行性**：¥50,000 能不能真的开出来（`portfolio_exec.json`）。

## 为什么必须有这个产物

`portfolio_nav.json` 测的是组合的**净值**，它的 `测量假设` 第一条写着
「**每天的名单全部执行**」，而**没有**任何一条提到**钱够不够**。

⇒ 那个组合在 3 个成交窗口上的名义成分数是 29 / 6 / 10。29 份等分
¥50,000 之后每份 ¥1,724 —— 而 A 股 1 手 = 100 股，**只要股价 > ¥17.24 就买不起**。

`lot_affordability.json` 早就警告过这件事，但它的**宽度档是 10/30/100/200/546**
—— 恰好**没有 29**；而它自己的 `what_this_cannot_prove` 第 3 条逐字说
「它不知道策略实际会选哪 N 只 ⇒ 真实可行性取决于选股结果，**不等于池子比例**」。

⇒ 所以池子比例也不能拿来回答这个问题。**只能用策略实际选出的那批票，逐只查。**

## 这个脚本**不算任何收益**

收益归 `portfolio_nav.json`。一个量只由一处算（第 109 层）——
两个产物各写一份 CAGR 公式，就是当年 `compounded_stats()` 存在的理由。

本脚本只回答三件事：**投得进去多少 / 闲置多少 / 开不开得出来**。

## 两种处置口径，**都算、都落盘**（预注册 §3）

买不到 1 手之后钱怎么办？两种都说得通，且给出**不同的组合**：

- **A `hold_cash`**：每份金额固定 = `CAPITAL / 行数`；买不到的份额**变成现金**。
- **B `reallocate`**：剔除连 1 手都买不起的 → 剩余**重新等分** → 再检查 → 迭代到**不动点**。

⚠️ **A 与 B 不可比较** —— 它们是不同的组合，不是同一组合的两种估计。
⚠️ 因此本产物**不做二选一**（预注册 §0：判据是在看到两个读数之后写的，
   冒充预注册会让自己有「挑一个」的机会，而挑选本身是错的方向）。

## 「行数」与「去重 code 数」是**两个量**

同一只票被两个 `pred_date` 选中时（实测 `300019.SZ` 两天各中一次，
两行 `actual_pct` 逐位相同 ⇒ 同日同价成交），在**行**口径下它拿两份金额。
这与 `daily_portfolio_returns` 的 `by_day.mean()` **同一个分母**
（`aplus/verify.py:390-393`）—— 换了分母就是换了量（第 7 层）。

⇒ 两个数都报，不许只报一个。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402
# 🚨 **信号作用域的唯一入口**（预注册 `docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）。
#   本脚本**不自己实现**过滤逻辑（第 97 层），也**不自己实现**指纹（第二份真相源）。
from aplus.signal_scope import (  # noqa: E402
    VERDICT_NOT_DECIDABLE,
    not_decidable_note,
    resolve_attribution_context,
    attribution_block,
    scope_block,
    scope_ledger,
)

GENERATOR = "scripts/measure_portfolio_exec.py"
DEFAULT_OUT = ROOT / "data" / "reports" / "portfolio_exec.json"
DEFAULT_DATA_ROOT = ROOT / "data"          # 与 lot_affordability.py 同一处默认值
VERIFY = ROOT / "data" / "reports" / "verification.parquet"
NAV = ROOT / "data" / "reports" / "portfolio_nav.json"

#: 预注册 E1/E2 —— 与 `lot_affordability.py:43-44` **同一个字面量**。
CAPITAL = 50_000.0
LOT = 100

#: 预注册 X3 —— 闲置现金超过这个比例就不算「开得出来」。
#: ⚠️ 这条阈值是**拍**的，不是推出来的；预注册 §4 X3 已显式承认它可被推翻。
IDLE_CASH_LIMIT = 0.20


def _sha256_of(path: Path) -> str:
    """底稿的**字节**指纹。读文件本身，不用 mtime——mtime 是代理不是证据。

    ⚠️ `provenance` 里**没有** `sha256_file`，只有 `code_sha` /
    `code_file_sha256`（那两个是给**代码**算的，不是给底稿算的）。
    兄弟脚本 `measure_portfolio_nav.py:264` 用的是同一个本地实现。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def price_on(data_root: Path, code: str, day: str) -> float | None:
    """该票在 `day` 那天的**不复权 close**；没有就 `None`。

    ⚠️ 口径与 `lot_affordability.py` **不完全相同**，这是有意的：
    它取「判决窗口最后一天」的价格（`w["close"].iloc[-1]`），
    本脚本取**该票自己的 `entry_date` 当天**——同一窗口里不同票的
    `entry_date` 可能不同，用窗口末日会量错票。两个是**两个量**（第 7 层）。
    """
    f = data_root / "master" / "daily" / f"{code}.parquet"
    if not f.is_file():
        return None
    try:
        d = pd.read_parquet(f, columns=["date", "close"])
    except Exception:                       # 单只读不出来不该让整份报告没有
        return None
    d = d[d["date"].astype(str).str[:10] == day]
    if d.empty:
        return None
    px = float(d["close"].iloc[0])
    return px if px > 0 else None            # 与 lot_affordability 的 `px > 0` 一致


def _spend(per_leg: float, one_lot_cost: float, need: int) -> int:
    """每份 `per_leg` 元最多能买几手，封顶 `need`。买不到返回 0。"""
    if one_lot_cost <= 0:
        return 0
    return max(0, min(need, int(per_leg // one_lot_cost)))


def _classify(legs: list[dict], per: float, lot: int) -> dict:
    """把票分成**三个互不相同的类**，并**分别落盘**。

    🚨 这里是被自己的错逼出来的（2026-10-16）：我先用「买的手数 < 需要的份数」
    一个判据同时回答「买不起」和「买不满」，于是 `300019.SZ`（need=2、每份只够 1 手）
    被并进「买不起」⇒ 报出「买得起 16 / 买不起 12」，两个数**都是错的**
    （真值 17 与 11）。

    ⇒ **「连 1 手都买不起」与「买不满所需份数」是两个量**（第 7 层）。
      混成一个判据，读者会重蹈覆辙 —— 所以产物必须分列。

      ① 连 1 手都买不起：`_spend(...) == 0`
      ② 买得起 ≥1 手：`_spend(...) >= 1`
      ③ 买满所需份数：`_spend(...) >= need`
      ④ 买得起但买不满：②减③（**不算买不起**）
    """
    n_zero = n_one = n_full = 0
    partial: list[dict] = []
    for lg in legs:
        if lg["price"] is None:
            continue                            # E6：缺失不进这三个计数
        k = _spend(per, lg["price"] * lot, lg["need"])
        if k == 0:
            n_zero += 1
            continue
        n_one += 1
        if k >= lg["need"]:
            n_full += 1
        else:
            partial.append({"code": lg["code"], "需要份数": lg["need"], "买到份数": k})
    return {
        "连1手都买不起_票数": n_zero,
        "买得起1手_票数": n_one,
        "买满所需份数_票数": n_full,
        "买得起但买不满_票数": len(partial),
        "买得起但买不满_明细": partial,
    }


def _hold_cash(legs: list[dict], capital: float, lot: int) -> dict:
    """口径 A：每份金额固定 = `capital / 行数`，买不到的**留现金**。"""
    n_rows = len(legs)
    per = capital / n_rows if n_rows else 0.0
    invested = bought = missing = 0
    for lg in legs:
        if lg["price"] is None:             # E6：缺失计入分母、不计入买得起
            missing += 1
            continue
        k = _spend(per, lg["price"] * lot, lg["need"])
        bought += k
        invested += k * lg["price"] * lot
    return {
        "口径": "A_hold_cash",
        "每份金额": per,
        "实际投入": invested,
        "闲置现金": capital - invested,
        "投入率": invested / capital if capital else None,
        "买入份数": bought,
        "price_missing": missing,
        "不动点轮数": 1,
        **_classify(legs, per, lot),
    }


def _reallocate(legs: list[dict], capital: float, lot: int) -> dict:
    """口径 B：剔除连 1 手都买不起的 → 剩余**重新等分** → 再检查 → 迭代到不动点。

    每轮剔除都让剩余行数**单调减少** ⇒ 至多 `len(legs)` 轮必然收敛。
    轮数落盘，读者才能判断它是否真的迭代过（预注册 §3）。
    """
    alive = list(legs)
    rounds = 0
    per = capital / len(alive) if alive else 0.0
    while alive and rounds < len(legs) + 2:
        rounds += 1
        per = capital / len(alive)
        keep = [lg for lg in alive
                if lg["price"] is not None and _spend(per, lg["price"] * lot, 1) >= 1]
        if len(keep) == len(alive):
            break                            # 不动点：再删就没人能买 1 手了
        alive = keep
    invested = bought = missing = 0
    for lg in legs:
        if lg["price"] is None:
            missing += 1
    for lg in alive:
        k = _spend(per, lg["price"] * lot, lg["need"])
        bought += k
        invested += k * lg["price"] * lot
    return {
        "口径": "B_reallocate",
        "每份金额": per,
        "实际投入": invested,
        "闲置现金": capital - invested,
        "投入率": invested / capital if capital else None,
        "买入份数": bought,
        "price_missing": missing,
        "不动点轮数": rounds,
        "存活的票数": len(alive),
        "存活的份数": sum(lg["need"] for lg in alive),
        "被剔除_票数": len(legs) - len(alive),
        # ⚠️ 分类必须在 **alive** 上做，不是 legs：per 提高后被剔除的那些票
        #    在 legs 上会被算成「买得起」，而 B 根本不给它们买——
        #    那是分类与实际购买集合**不是同一个量**（第 7 层）。
        **_classify(alive, per, lot),
    }


def measure(history: pd.DataFrame, data_root: Path) -> dict:
    """把逐笔判定摊成**逐成交窗口的资金可执行性**。"""
    if history is None or history.empty:
        return {"逐窗口": {}}
    need_cols = {"direction", "exit_date", "code"}
    if not need_cols.issubset(history.columns):
        raise ValueError(f"底稿缺列 {sorted(need_cols - set(history.columns))}")

    up = history[history["direction"] == "up"].copy()   # C1 同源：只持涨腿
    if up.empty:
        return {"逐窗口": {}}
    up["_day"] = up["exit_date"].astype(str).str[:10]    # 观测日 = exit_date
    up["_entry"] = up["entry_date"].astype(str).str[:10]

    windows: dict[str, dict] = {}
    for day, g in up.groupby("_day"):
        legs: list[dict] = []
        for code, gc in g.groupby("code"):
            entries = [e for e in gc["_entry"].unique() if e and e != "nan"]
            entry = entries[0] if entries else None
            legs.append({
                "code": code,
                "need": len(gc),                            # 行数：该票被选几次
                "entry": entry,
                "price": price_on(data_root, code, entry) if entry else None,
            })
        dup = {c: int(k) for c, k in g.groupby("code").size().items() if k > 1}
        windows[day] = {
            "名义行数": int(len(g)),              # 行数含重复：与 by_day.mean() 同分母
            "去重code数": int(len(legs)),
            "被多次选中的票": dup,
            "A_hold_cash": _hold_cash(legs, CAPITAL, LOT),
            "B_reallocate": _reallocate(legs, CAPITAL, LOT),
        }
    return {"逐窗口": windows}


def _verdict(windows: dict) -> str:
    """X3：**取逐窗口最差**（只要有一个窗口开不出来，这个策略就有问题）。

    聚合规则逐字写进产物，因为「逐窗口最差」与「加权平均」是两个答案。
    """
    ok = [w for w in windows.values()
          if w["A_hold_cash"]["投入率"] is not None]
    if not ok:
        return "判不出（无行情）"
    worst = min(w["A_hold_cash"]["投入率"] for w in ok)
    idle = 1.0 - worst
    tag = "可开" if idle < IDLE_CASH_LIMIT else "部分开不出"
    return f"{tag}（最差窗口闲置 {idle:.1%}）"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="组合资金可执行性：¥5 万开不写得出来")
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--verification", type=Path, default=None,
                   help="前瞻底稿路径（默认 = 模块常量 VERIFY）。")
    p.add_argument(
        "--reports-dir", type=Path, default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --verification 的父目录。")
    args = p.parse_args(argv)

    # 🚨 盖章要的是「这次跑真正跑的那些字节」，所以**先抓**再跑（第 125 层）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    # ⚠️ `--verification` 缺省 ⇒ 用模块常量 `VERIFY`（老口径）。
    #    两条分支**都可达**（不是恒真假开关）。
    VERIFY_PATH = args.verification if args.verification is not None else VERIFY

    # 🚨🚨 **信号作用域**（预注册 §2 / §3）。
    #   底稿是**追加式**的 ⇒ 逐窗口投入率**只由当前信号**的行算出来。
    reports_dir = args.reports_dir if args.reports_dir else VERIFY_PATH.parent
    ctx = resolve_attribution_context(reports_dir)
    # 🚨 **读口留在调用点**（既有守卫钉住：expect_bytes 必须抓在它之前）。
    frame = pd.read_parquet(VERIFY_PATH)
    # 🚨 **过滤只走共享入口**（第 97 层）。
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)

    if not scoped.has_evidence:
        # 🚨 作用域不对 ⇒ **不许**报逐窗口投入率（那报的是另一个信号）。
        out = {
            "generated_by": GENERATOR,
            "上游产物": "data/reports/portfolio_nav.json",
            "本产物算什么": "只算「投得进去多少 / 闲置多少 / 开不开得出来」；"
                       "**不算任何收益**——收益归 portfolio_nav.json（第 109 层）",
            "verdict_about_exec": (
                f"{VERDICT_NOT_DECIDABLE}：{scoped.reason}"),
            "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
            "逐窗口": {},
            "what_this_cannot_prove": [
                "🚨 **本产物没有报任何投入率**。作用域不对 ⇒ "
                "**没有资格**被算出来。",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「买不进」或"
                "「闲置现金很高」——它说的是「**这份证据不属于当前信号**」。",
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        provenance.stamp(
            args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={"source_parquet_sha256": _sha256_of(VERIFY_PATH),
                   "verdict": VERDICT_NOT_DECIDABLE,
                   "signal_scope_status": scoped.status,
                   "signal_id": scoped.current_signal_id,
                   "n_days_measured": 0})
        print(not_decidable_note(scoped))
        print(f"→ {args.out}")
        return 0

    history = scoped.frame
    res = measure(history, args.data_root)
    windows = res["逐窗口"]

    if not windows:
        print("🚨 没有成交窗口，产物不落盘", file=sys.stderr)
        return 2

    idle_worst = 1.0 - min(w["A_hold_cash"]["投入率"] for w in windows.values())
    out = {
        "generated_by": GENERATOR,
        "上游产物": "data/reports/portfolio_nav.json",
        "本产物算什么": "只算「投得进去多少 / 闲置多少 / 开不开得出来」；"
                   "**不算任何收益**——收益归 portfolio_nav.json（第 109 层）",
        "capital": CAPITAL,
        "lot_shares": LOT,
        "idle_cash_limit": IDLE_CASH_LIMIT,
        "价格口径": "不复权 close，取该票**自己的 entry_date 当天**"
                "（lot_affordability.json 取窗口末日 ⇒ 两个量）",
        "成分口径": "行数（与 daily_portfolio_returns 的 by_day.mean() 同分母）",
        "verdict_aggregation": "取逐窗口**最差**（任一窗口开不出来即算开不出来）",
        "为什么两个口径给出同一个投入额": (
            "⚠️ 实测三个窗口 A 与 B 的投入额**逐元相同**。原因是绝大多数票 "
            "`need=1`，而 `_spend` 用 `min(need, ...)` **封顶** ⇒ 每份金额从 "
            "¥1,724 提到 ¥2,941 也买不到第 2 手，多出来的钱**无处可去**。"
            "⇒ **闲置不是因为「钱被均分后不够」，而是因为整手粒度凑不出一手。**"
            "⇒ 想提高投入率只能**减少组合宽度**（每份更大），"
            "「重新分配」这条路在 need≈1 时是死的。"
        ),
        "逐窗口": windows,
        "最差窗口闲置现金": idle_worst,
        "verdict_about_exec": _verdict(windows),
        "这个产物不能回答": [
            "**涨不涨**——收益是 portfolio_nav.json 的量，本脚本一道收益公式都没写。",
            "**闲置现金拖累多少收益**——那需要按可执行权重重算收益，"
            "而那会把一个量拆成两处算（第 109 层），须另行预注册。",
            "**将来能不能开出来**——3 个成交窗口、且 entry 价格只在这几天量过。",
        ],
        "预注册": "docs/PREREG_PORTFOLIO_EXEC_2026-10-16.md",
        # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）。
        "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT,
        data_root=args.data_root, expect_bytes=expect_bytes,
        extra={"source_parquet_sha256": _sha256_of(VERIFY_PATH),
               "n_days_measured": len(windows),
               "signal_scope_status": scoped.status,
               "signal_id": scoped.current_signal_id},
    )
    print(f"写入 {args.out}")
    for d, w in windows.items():
        a = w["A_hold_cash"]
        print(f"  {d}  行数={w['名义行数']:>3}  去重={w['去重code数']:>3}  "
              f"A投入率={a['投入率']:.2%}  闲置={a['闲置现金']:>10,.2f}  "
              f"缺价={a['price_missing']}")
    print(f"verdict_about_exec = {out['verdict_about_exec']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
