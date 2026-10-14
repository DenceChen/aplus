"""可转债纸面账户（路线 A 第四阶段）——20% 候选的样本外审判台。

背景（第三阶段收盘，PLATFORM.md §7.5）：低价因子周频轮动样本内净年化 +17.1%
（h=5）/ +21.0%（h=20），但自陈三处边缘：t 统计随持有期衰减（h=5 t=1.57）、
网格相位敏感（净年化五相 +6.8%~+17.1%，回测锚点恰是最幸运相位）、2026 段
低价溢价收敛转负。**判定成立但证据强度为边缘**——纸面账户的职责就是用样本外
数据把它裁决掉：继续跑 = 有效，被打掉 = 这两天的工作被正确地否掉。

口径（与 aplus/bond_horizon.py 写死的一致，保真是本模块存在的理由）：
- 信号日 D 收盘后按 cheap 深度选目标 → **D+1 开盘买入** → 持有 h 根 K 线 →
  卖出日 = 下一个信号日（= D+h 收盘卖出）
- 等权槽位记账：每槽 1/n 资金买入 units，nav = Σ units×close。期收益恰好等于
  mean(close/open)，与回测器的 mean(horizon_return) 严格同构
- 费用：cost = 一次完整换手的双边费率，按回测同一换手比例收
  turnover_ratio = (新进 + 退出) / (2 × max(top_n, |当期|, |上期|))
  首期建仓自动退化为 cost/2（只付买侧）
- 候选池漏斗**直接复用 bond_horizon._pool_of**（不自写副本）：漏斗一旦分叉，
  样本外与样本内就不可比，保真优先于命名洁癖

三处对回测器的有意偏离（全部在此声明）：
1. **费用按净值乘性扣除**，回测器用加性近似（net = gross - cost_drag）。
   二者差 gross×cost_drag（二阶小量，约 1e-5），乘性才是正确记账。
2. **费用归属期**：回测器把第 i 期的建仓成本记在第 i 期内（net_i = gross_i -
   cost_i）；本模块在信号日算出 cost_i、到**卖出日**才扣，保证「买进→卖出」
   这段完整持有期的费用落在同一期。记到下一期会让净值与回测口径错位一期。
3. **摘牌/强赎代理**：持仓末根 K 线早于卖出日即按末根收盘卖出并标记
   n_liquidated——回测器同样如此，此处把它变成纸面账户的显式可见计数。

调仓网格、候选漏斗、费用换手比例公式一律与回测器**逐行同构**，不做任何
「实盘看起来更合理」的改写：那种改写正是让样本外数字失去意义的元凶。

**相位诚实性**：回测五相净年化 +6.8%~+17.1%，锚点选择权是最危险的动作。本模块
默认并行跑 phase 0~4 五条臂：phase 0 的锚点 = 账户创建后的首个交易日（看到任何
结果前固定，永不调整），phase 1~4 用于**在线测量**相位敏感性——把已知弱点变成
可测量量，而不是假设它不存在。若实盘相位恰为不利端，预期应按 +6.8% 而非
+17.1% 计。

**随机对照必须有**：判定线是「对随机净超额 > 0」。没有随机臂的纸面账户只能裁决
净值、不能裁决超额——等于白建。故每相位默认带 5 条随机臂（按 (seed, 信号日)
确定性采样，保证补跑/重跑完全可复现）。
"""
import random

import numpy as np
import pandas as pd

from .bond_horizon import COST_WAN15, _pool_of

#: 指向债券侧唯一真相源，不写裸字面量（理由同 bond_excess.DEFAULT_COST：
#: 数值与股票侧 0.003 相同但含义不同，grep 批量修会出事）。
DEFAULT_COST = COST_WAN15   # 万 15/边 × 双边——第三阶段主判定的保守口径
DEFAULT_HOLD_DAYS = 5
DEFAULT_TOP_N = 10
PAPER_PHASES = (0, 1, 2, 3, 4)
DEFAULT_RANDOM_SEEDS = (1, 2, 3, 4, 5)

MIN_PERIODS_FOR_VERDICT = 3   # 少于 3 个完整调仓期不给年化判决（防止刚开张就"达标"）


def _arm_metrics(sub: pd.DataFrame) -> dict:
    """单臂：终值净值 / 最大回撤 / 调仓与费用统计。"""
    nav = sub["nav"].astype(float)
    peak, mdd = 1.0, 0.0
    for v in nav:
        peak = max(peak, float(v))
        mdd = max(mdd, float((peak - v) / peak))
    last = sub.iloc[-1]
    picks = sub[sub["target"] != ""]
    buys = sub[sub["is_buy_day"]]
    sells = sub[sub["is_sell_day"]]
    return {
        "last_nav": float(last["nav"]),
        "total_return": float(last["nav"] - 1.0),
        "max_drawdown": mdd,
        "n_days": int(len(sub)),
        "n_signals": int(len(picks)),
        "n_sells": int(len(sells)),
        "n_liquidated": int(sub["n_liquidated"].sum()),
        "total_cost": float(sub["cost_paid"].sum()),
        "avg_turnover": float(buys["n_bought"].mean()) if len(buys) else 0.0,
        "last_date": str(last["date"]),
        "holdings": [c for c in str(last["holdings"]).split(",") if c],
        "last_target": [c for c in str(last["target"]).split(",") if c],
        "last_add": [c for c in str(last["add"]).split(",") if c],
        "last_remove": [c for c in str(last["remove"]).split(",") if c],
        "last_is_signal": bool(last["is_signal_day"]),
        "last_is_sell": bool(last["is_sell_day"]),
    }


def summarize_paper(rows: pd.DataFrame, state: dict | None = None) -> dict:
    """纸面账户日报摘要（Web 与日志共用）。

    诚实性护栏：调仓期少于 MIN_PERIODS_FOR_VERDICT 时**不给年化/超额判决**，
    只如实报「样本累积中」。刚开张两天就算出「年化 +40%」是自欺，且会让人
    误以为回测的 +17.1% 已经拿到样本外确认。
    """
    state = dict(state or {})
    if rows is None or rows.empty:
        return {"status": "empty", "as_of": None, "state": state, "verdict": None,
                "reason": "尚无交易日记录（首日为信号日，次日才建仓）"}
    arms = {name: _arm_metrics(sub) for name, sub in rows.groupby("arm")}
    cheap = {k: v for k, v in arms.items() if k.startswith("cheap")}
    rand = {k: v for k, v in arms.items() if k.startswith("rand")}
    dates = sorted(rows["date"].unique())
    head = cheap.get("cheap_p0", {})
    rand_navs = [v["last_nav"] for v in rand.values()]
    n_signals = head.get("n_signals", 0)
    enough = n_signals >= MIN_PERIODS_FOR_VERDICT

    out = {
        "status": "accumulating" if not enough else "judging",
        "as_of": dates[-1],
        "first_date": dates[0],
        "n_days": len(dates),
        "state": state,
        "headline": head,
        "arms": arms,
        # 相位带：五个相位终值一起摆出来，而不是只报最好的那个
        "phase_spread": {k: v["last_nav"] for k, v in sorted(cheap.items())},
        "random_mean_nav": (sum(rand_navs) / len(rand_navs)) if rand_navs else None,
        "verdict": None,
    }
    if enough and rand_navs:
        out["verdict"] = {
            "n_signals": n_signals,
            "head_nav": head["last_nav"],
            "random_nav": out["random_mean_nav"],
            "excess_vs_random": head["last_nav"] - out["random_mean_nav"],
            "phase_best": max((v["last_nav"] for v in cheap.values()), default=None),
            "phase_worst": min((v["last_nav"] for v in cheap.values()), default=None),
            "caveat": ("样本外期数仍少，且相位带宽度直接反映回测披露的相位敏感性"
                       "——单看 headline 的 phase0 会高估"),
        }
    return out


ROW_COLUMNS = [
    "date", "arm", "mode", "phase", "seed", "hold_days", "top_n", "cost_rate",
    "is_signal_day", "is_buy_day", "is_sell_day",
    "holdings", "target", "n_positions", "n_bought", "n_sold", "n_liquidated",
    "add", "remove", "hold", "turnover_names", "cost_paid",
    "mean_entry_open", "nav", "daily_return",
]


# ---------- 信号日选券 ----------

def select_targets(cache: dict, day: str, top_n: int = DEFAULT_TOP_N,
                   mode: str = "cheap", seed: int | None = None) -> list:
    """信号日 day 的目标持仓（as-of 语义：只看 ≤day 的 K 线，且要求 D+1 可成交）。

    mode="cheap"：cheap 深度 TopN（并列按代码序，确定性）。
    mode="random"：同池随机——随机对照臂，与打分臂走**完全相同**的漏斗，
      只换最后一步选券。按 (seed, day) 构造 RNG，保证任意补跑路径下可复现。
    """
    pool = _pool_of(cache, day)
    if not pool:
        return []
    if mode == "random":
        codes = sorted(r[0] for r in pool)
        chosen = set(random.Random(f"bond-paper|{seed}|{day}").sample(
            codes, min(top_n, len(codes))))
        rows = sorted((r for r in pool if r[0] in chosen), key=lambda r: r[0])
    else:
        rows = sorted(pool, key=lambda r: (-r[1], r[0]))[:top_n]
    return [{"code": r[0], "cheap": float(r[1]), "pos": int(r[2])} for r in rows]


def bond_rebalance_signal(positions_prev, positions_now) -> dict:
    """调仓差异——每日可执行输出（页面与日志直接消费）。

    positions_* 接受 code 列表或逗号分隔字符串（历史行反序列化友好）。
    """
    prev = _as_codes(positions_prev)
    now = _as_codes(positions_now)
    ps, ns = set(prev), set(now)
    return {"add": sorted(ns - ps), "remove": sorted(ps - ns), "hold": len(ps & ns)}


def _as_codes(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [c for c in value.split(",") if c]
    return [str(c) for c in value]


# ---------- 单条臂的状态机 ----------

class PaperArm:
    """一条纸面账户臂（cheap×5 相位 + random×5 种子）。

    生命周期：信号日 D 收盘选券 → D+1 开盘建仓 → 每日收盘盯市 →
    D+h 收盘卖出并计费 → 同时成为下一个信号日。
    空池时保持空仓且次日重试。
    """

    def __init__(self, name, mode, phase, seed, hold_days, top_n, cost):
        self.name = name
        self.mode = mode
        self.phase = phase
        self.seed = seed
        self.hold_days = hold_days
        self.top_n = top_n
        self.cost = cost
        self.next_signal_idx = phase      # 相位 = 信号日在网格上的偏移
        self.pending = None               # 待建仓 code 列表
        self.pending_buy_idx = None
        self.pending_cost = 0.0           # 本期建仓成本：信号日算出，卖出日才扣
        self.held_cost = 0.0              # 已建仓但尚未扣除的那笔成本
        self.slots = {}                   # code -> (units, entry_open, entry_date)
        self.prev_picks = []
        self.nav = 1.0

    def reset(self) -> None:
        """清空全部可变状态，回到「尚未开始」的起点。

        run() 是**纯重放**（给定 cache/start/end 结果恒定），不依赖任何上次调用
        留下的状态——否则补跑会带着上一段的历史，18:30 与 19:30 守卫连跑就会
        双重记账。相位锚点同步重置。
        """
        self.next_signal_idx = self.phase
        self.pending = None
        self.pending_buy_idx = None
        self.pending_cost = 0.0
        self.held_cost = 0.0
        self.slots = {}
        self.prev_picks = []
        self.nav = 1.0

    # --- K 线取数（按日历日 as-of 切片，绝不看未来） ---

    @staticmethod
    def _close_of(cache, code, day):
        """(末根 ≤ day 的收盘价, 当日是否有成交棒)。"""
        dates, _o, c, _v, _ch = cache["frames"][code]
        pos = int(np.searchsorted(dates, day, side="right")) - 1
        if pos < 0:
            return None, False
        return float(c[pos]), str(dates[pos]) == day

    def _buy_at_open(self, cache, code, day, nav_per_slot):
        """D+1 **当日那根** K 线开盘买入；返回 (units, entry_open) 或 None。

        插入点语义必须与 _close_of 一致（side="right" - 1 = 当日或之前最后一根）：
        用 side="right" 会取到**次日**的 bar，建仓价与当日收盘估值错配一根 K 线
        ——那种错法不会崩，只会让净值悄悄偏离回测口径（tests 保真用例钉死）。
        """
        dates, o, c, v, _ch = cache["frames"][code]
        pos = int(np.searchsorted(dates, day, side="right")) - 1
        if pos < 0 or pos >= len(dates):
            return None
        if not (float(o[pos]) > 0 and float(c[pos]) > 0 and float(v[pos]) > 0):
            return None
        entry_open = float(o[pos])
        return nav_per_slot / entry_open, entry_open

    # --- 每日推进 ---

    def step(self, cache, day, i, n_cal) -> dict:
        prev_nav = self.nav
        bought, cost_paid, n_liquidated = [], 0.0, 0
        is_buy = self.pending is not None and self.pending_buy_idx == i
        is_signal = i == self.next_signal_idx
        target_now, add, remove, hold_n, turnover_names = [], [], [], 0, 0
        n_sold = 0

        # 1) 开盘建仓（等权槽位，每槽 nav/n）
        if is_buy:
            picks = list(self.pending)
            slot_value = prev_nav / max(len(picks), 1)
            for code in picks:
                got = self._buy_at_open(cache, code, day, slot_value)
                if got is None:
                    continue  # 池子保证了可成交，此处兜底防御
                self.slots[code] = (got[0], got[1], day)
                bought.append(code)
            self.held_cost = self.pending_cost
            self.pending, self.pending_buy_idx = None, None

        # 2) 收盘盯市：nav = Σ units × 末根收盘（无仓则沿用上日净值）
        if self.slots:
            total = sum(u * (self._close_of(cache, c, day)[0] or 0.0)
                        for c, (u, _eo, _ed) in self.slots.items())
            self.nav = total
        else:
            self.nav = prev_nav

        # 3) 信号日：收盘卖出（已在盯市值内）→ 扣本期费用 → 选新目标
        if is_signal:
            if self.slots:
                n_sold = len(self.slots)
                for code in self.slots:
                    _px, traded_today = self._close_of(cache, code, day)
                    if not traded_today:
                        n_liquidated += 1  # 强赎/摘牌代理：按末根收盘卖出
                self.slots = {}
                # 费用记在**本期**——与回测器同侧（cost_i 属于第 i 期的建仓）。
                # 记到下一期会让纸面净值与回测口径系统性错位一期（保真测试钉死）。
                self.nav *= (1.0 - self.held_cost)
                cost_paid = self.held_cost
                self.held_cost = 0.0
            sel = (select_targets(cache, day, self.top_n, self.mode, self.seed)
                   if i + 1 < n_cal else [])
            target_now = [t["code"] for t in sel]
            if target_now:
                sig = bond_rebalance_signal(self.prev_picks, target_now)
                add, remove, hold_n = sig["add"], sig["remove"], sig["hold"]
                denom = max(self.top_n, len(target_now), len(self.prev_picks))
                turnover_names = len(remove)
                self.pending_cost = (len(add) + len(remove)) / (2.0 * denom) * self.cost
                self.prev_picks = list(target_now)
                self.pending, self.pending_buy_idx = list(target_now), i + 1
                self.next_signal_idx = i + self.hold_days
            else:
                # 空池 → 本期空仓，网格仍按 hold_days 推进（与回测器一致）。
                # 注意：绝不能改成「次日重试」——那会让所有相位在预热期逐日对齐，
                # 把相位网格彻底抹平（5 条臂退化成 1 条，相位敏感性就再也测不到了）。
                self.pending_cost = 0.0
                self.next_signal_idx = i + self.hold_days

        entry = [eo for _u, eo, _ed in self.slots.values()]
        return {
            "date": day, "arm": self.name, "mode": self.mode, "phase": self.phase,
            "seed": self.seed if self.seed is not None else -1,
            "hold_days": self.hold_days, "top_n": self.top_n, "cost_rate": self.cost,
            "is_signal_day": is_signal, "is_buy_day": is_buy,
            "is_sell_day": bool(is_signal and n_sold > 0),
            "holdings": ",".join(sorted(self.slots)),
            "target": ",".join(target_now),
            "n_positions": len(self.slots), "n_bought": len(bought),
            "n_sold": n_sold, "n_liquidated": n_liquidated,
            "add": ",".join(add), "remove": ",".join(remove), "hold": hold_n,
            "turnover_names": turnover_names, "cost_paid": cost_paid,
            "mean_entry_open": float(sum(entry) / len(entry)) if entry else float("nan"),
            "nav": self.nav,
            "daily_return": (self.nav / prev_nav - 1.0) if prev_nav else 0.0,
        }


# ---------- 账户（多臂并行） ----------

class BondPaperAccount:
    """可转债纸面账户：cheap×相位 + random×种子 并行追踪。

    run() 是**无状态重算**：每次从 start 重放到 end，只输出「尚未见过」的那些
    日期行。幂等性由构造保证（不依赖任何落盘状态），补跑/重跑/18:30 与 19:30
    守卫连跑都不会重复记账或漏记。
    """

    def __init__(self, hold_days=DEFAULT_HOLD_DAYS, top_n=DEFAULT_TOP_N,
                 cost=DEFAULT_COST, phases=PAPER_PHASES,
                 random_seeds=DEFAULT_RANDOM_SEEDS):
        if hold_days < 1:
            raise ValueError(f"hold_days 必须 ≥1，收到 {hold_days}")
        if top_n < 1:
            raise ValueError(f"top_n 必须 ≥1，收到 {top_n}")
        if cost < 0:
            raise ValueError(f"cost 不得为负，收到 {cost}")
        self.hold_days, self.top_n, self.cost = hold_days, top_n, cost
        self.arms = []
        for ph in phases:
            self.arms.append(PaperArm(f"cheap_p{ph}", "cheap", ph, None,
                                      hold_days, top_n, cost))
        for ph in phases:
            for sd in random_seeds:
                self.arms.append(PaperArm(f"rand_p{ph}_s{sd}", "random", ph, sd,
                                          hold_days, top_n, cost))

    def run(self, cache: dict, start: str, end: str) -> pd.DataFrame:
        """重放 [start, end) 区间，返回逐日逐臂的记账表。"""
        cal = [d for d in cache.get("calendar", []) if start <= d < end]
        rows = []
        for arm in self.arms:
            arm.reset()
            for i, day in enumerate(cal):
                rows.append(arm.step(cache, day, i, len(cal)))
        return pd.DataFrame(rows, columns=ROW_COLUMNS)
