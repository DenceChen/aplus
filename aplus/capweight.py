"""流通市值权重 —— 预注册 `docs/TODO_cap_weighted_benchmark_2026-10-15.md`。

## 这个模块只做一件事：把「一只票在某一天值多少钱」变成一个可检索的数

**它不碰回测、不碰成本、不碰判决。** 它是尺子，不是测量仪 ——
测量仪仍然是 `evaluate_weights` / `_daily_series` / `_tstat` 那一条共享路径。

## 🚨 口径（预注册 §1 写死，一个字都不许改）

    市值 = close × a_shares        a_shares = volume × 100 / turn

**不是 `float_shares`** —— 后者含 B 股/H 股，会系统性高估。
依据 `scripts/turn_integrity_audit.py`：对 `data/master/shares/` 全部 21 个文件
做逐日同口径比对，`a_shares` **21/21 命中 [0.95,1.05]**，中位比值 1.0000，
而 `float_shares` 只有 10/21、`total_shares` 只有 8/21。

⇒ 本模块**从审计脚本 import 那一个函数**，不在这里重写一遍。
重写就是第二份真相：审计那份将来换了公式，本模块不会跟着换，
而产物里仍印着「21/21 验过」。

## 停牌日：沿用上一根的市值，**不得**变成 0 或 NaN

预注册 §1 要求「**每日重新归一化**」。停牌日没有 `turn` ⇒ 没有市值：

- 记 0 ⇒ 停牌的大盘股静默退出基准，等于偷偷改了抽样口径；
- 记 NaN ⇒ 整天的权重全丢，样本凭空少一天。

两条都错。正确做法是**沿用该票最后一根有效 bar 的市值**：
停牌不改变它的股本，也不改变它停牌前最后成交的那个价。

## 市值全不可得的那一天 ⇒ 回退等权，**并且必须被计数**

`CapContext.stats` 是这件事唯一的出口。静默回退是第 5 层同族的失效：
数字照常出，报告看上去与正常跑一模一样，而实际上有几天的分母换了口径。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ★ 口径的单一真相源在审计脚本里（21/21 地面真值验过的那一份）。
#   这里只做转手，不做实现 —— 见模块 docstring。
from scripts.turn_integrity_audit import implied_a_shares  # noqa: F401


def implied_a_shares_full(daily: pd.DataFrame) -> pd.Series:
    """与 `daily.index` **等长**的 A 股股本序列。

    ⚠️ 为什么要这一层：`turn_integrity_audit.implied_a_shares` 用
      `daily.loc[ok, ...]` 构造，返回的是**被筛掉无效行之后**的短序列。
      而 `CapTable` 的缓存里市值与股本必须与**日期数组逐位对齐**
      —— 短的那个会让 `shares[i]` 越界（第一次真跑就炸在这里，
      单元守卫全绿：toy 夹具里每一行都有 turn，走不到那条分支）。
    """
    a = implied_a_shares(daily)
    out = pd.Series(np.nan, index=daily.index, dtype=float)
    out.loc[a.index] = a.to_numpy(float)
    return out


def implied_cap(daily: pd.DataFrame) -> pd.Series:
    """`close × a_shares`。只在 `a_shares` 有意义的日子有值。

    无效日（`turn <= 0`、`volume <= 0`、`close` 缺失）返回 **NaN**，
    **不得 clip 到 0** —— clip 会把「不知道」变成「一文不值」，
    而下游分不清这两个（`turn_integrity_audit.truth_asof` 的注释同源）。
    """
    a = implied_a_shares_full(daily)
    close = daily["close"].astype(float)
    ok = a.notna() & close.notna() & (close > 0)
    out = pd.Series(np.nan, index=daily.index, dtype=float)
    out.loc[ok] = close.loc[ok] * a.loc[ok]
    return out


class CapTable:
    """按 (code, day) 检索流通市值，带**停牌沿用**与一次构建的缓存。

    ## 为什么不是「现算现查」

    基准侧每天要 150 只票的市值 × 约 1400 天 ≈ 21 万次；
    组合侧每天要 top-N（200）× 约 1400 天 ≈ 28 万次。
    每次都从 `daily` 重算 `volume*100/turn` 意味着几十万次全表扫描 ——
    这是本项目 OOM/超时的同族成因。构建一次、按日检索是 O(1)。

    ## `weight_override`

    只给守卫用：让所有票返回同一个假市值，于是「加权」这一档退化成等权，
    用来验证归一化那一段本身没坏。**生产路径不得使用。**
    """

    def __init__(self, daily_map: dict, weight_override: float | None = None):
        self._daily_map = daily_map
        self._override = weight_override
        self._idx: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def _series(self, code: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """该票的 (日期数组, 市值数组, 股本数组)，按日期升序。

        股本与市值**共用同一个日期数组**：日期数组是这层缓存里最大的开销
        （每只 ~1400 根 × 40 字节 ≈ 56 KB，全池 ~300 MB），
        存两份就是凭空多 300 MB。诊断 §3.3 要的 `a_shares` 从这里顺带取。
        """
        got = self._idx.get(code)
        if got is not None:
            return got
        d = self._daily_map.get(code)
        if d is None or d.empty:
            got = (np.empty(0, dtype="U10"), np.empty(0, dtype=float),
                   np.empty(0, dtype=float))
        elif self._override is not None:
            dates = d["date"].to_numpy(dtype="U10")
            got = (dates, np.full(len(dates), float(self._override)),
                   np.full(len(dates), float(self._override)))
        else:
            dates = d["date"].to_numpy(dtype="U10")
            shares = implied_a_shares_full(d).to_numpy(float)
            cap = implied_cap(d).to_numpy(float)
            # 停牌/无效 ⇒ 沿用上一根有效市值（ffill），**不是** 0、不是 NaN。
            cap = pd.Series(cap).ffill().to_numpy(float)
            got = (dates, cap, shares)
        self._idx[code] = got
        return got

    def _at(self, code: str, day: str, which: int) -> float:
        dates, caps, shares = self._series(code)
        if not len(dates):
            return float("nan")
        i = int(np.searchsorted(dates, day, side="right")) - 1
        if i < 0:
            return float("nan")
        return float(caps[i] if which == 0 else shares[i])

    def cap_on(self, code: str, day: str) -> float:
        """该票在 `day` 的流通市值。日历上取**不晚于 `day` 的最后一根**。

        `day` 不在序列里（停牌 / 尚未上市）时向前取 —— 与
        `turn_integrity_audit.truth_asof` 的分段常数右连同一口径。
        早于首根 ⇒ NaN（**不得** clip 到 0）。
        """
        return self._at(code, day, 0)

    def a_shares_on(self, code: str, day: str) -> float:
        """该票在 `day` 的 A 股股本（**不**做停牌沿用 —— 见 docstring）。

        ⚠️ 与 `cap_on` 的差别是刻意的：停牌那天没有成交，
          `volume×100/turn` 无从谈起，而股本**根本没有变**。
          市值需要沿用是因为价格 stale 之后 `close` 也不可信；
          股本沿用则是把一个「当天根本没测过」的数冒充成测量值。
          ⇒ 诊断（单因子排序）宁可丢一天，也不要编一天。
        """
        return self._at(code, day, 1)


class CapContext:
    """一天的权重 = 市值归一化，并**统计**回退与缺失。

    ## 为什么是 context 而不是裸函数

    因为「这一天市值全不可得，回退等权了」这件事**必须可读**。
    做成裸函数就没有地方放这个计数，做成 context 就有 ——
    调用方在 `attrs` 里读得到，报告里印得出。

    ## `channel`（诊断出口，默认 None ⇒ 不留痕）

    预注册 §3.1 要「组合 vs 基准的流通市值中位数比」。
    那个比值的分子分母**只在共享路径里存在**（组合端在
    `evaluate_weights`，基准端在 `_market_by_day`），
    事后在外面重算一遍就等于**第二次抽样**。

    ⇒ 所以诊断走 `channel` 参数：调用点报出「我这一段是基准还是组合」，
    这里把当天的市值中位数记进 `self.diag[channel][day]`。
    默认 `None` ⇒ 一个字符都不留，行为与加这个参数之前逐位相同。
    """

    def __init__(self, table: CapTable):
        self.table = table
        self.stats = {"days_total": 0, "days_fallback_equal": 0,
                      "rows_requested": 0, "rows_missing": 0}
        self.diag: dict[str, dict[str, float]] = {}

    def normalize(self, day: str, codes, channel: str | None = None) -> dict[str, float]:
        """这些票在这一天的权重（和恒为 1.0）。无市值 ⇒ 该票**不在字典里**。

        返回空字典 = 这一天的市值一个都拿不到 ⇒ 调用方回退等权。
        """
        codes = list(codes)
        caps: dict[str, float] = {}
        for c in codes:
            v = self.table.cap_on(c, day)
            if np.isfinite(v) and v > 0:
                caps[c] = v
        self.stats["days_total"] += 1
        self.stats["rows_requested"] += len(codes)
        self.stats["rows_missing"] += len(codes) - len(caps)
        if channel is not None:
            self.diag.setdefault(channel, {})[day] = (
                float(np.median(list(caps.values()))) if caps else float("nan"))
        if not caps:
            self.stats["days_fallback_equal"] += 1
            return {}
        total = float(sum(caps.values()))
        # 先除后补：让和**恰好**为 1.0。浮点上 w/total 的和可能是
        # 1.0000000000000002，而守卫钉的是 `== 1.0`。
        w = {c: v / total for c, v in caps.items()}
        drift = 1.0 - sum(w.values())
        if drift:
            # 补在权重最大的那只上（相对误差最小）
            k = max(w, key=w.get)
            w[k] += drift
        return w
