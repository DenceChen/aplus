#!/usr/bin/env python
"""分母侧符号探针：基准抽样池的流通市值中位数相对全池，**能不能定出符号**。

判据预注册在 `docs/PREREG_CAP_EXPOSURE_SIGMA_2026-10-18.md`（§2 判据 V/W、
§3 的采样次数推导）。**本文件一个字都不许在看到实测数字之前改判据。**

## 它为什么承重

`data/reports/cap_weighted_benchmark.json::diagnostics.cap_exposure_ratio`
被 `scripts/cap_weighted_benchmark.py:285::_size_tilt_label` 读成一句方向标签。
该函数实读全文**只有四支**：

    None（成对日不足） / >1.0「组合偏大盘」 / <1.0「组合偏小盘」 / ==1.0「完全相同」

⇒ **没有「`|ratio − 1|` 小于噪声 ⇒ 测不出」这一支**
⇒ 任何非 1.0 的比值都会被**自信地**读成方向。
⇒ 而这条标签是「C 臂不是伪装成因子 alpha 的市值押注」这条结论的**唯一依据**。
🚨 **失效方向是乐观的**：用「测不出显著偏离」支撑「没有市值押注」
⇒ 会让人**不去查**那个唯一可能推翻 C 臂的暴露。

## 它量的是哪一个量（分子的缺席是刻意的）

生产的 `cap_exposure_ratio = 组合持仓逐日中位市值 ÷ 基准抽样池逐日中位市值`
（读自 `cap_weighted_benchmark.json::diagnostics`；产物自述
「分子分母都由共享路径交出（`CapContext.diag`），**不在这里第二次抽样**」
⇒ **分母口径正确**）。本探针**只算分母那一侧**：

    med150[d] ← 150 只抽样池在 d 日的流通市值中位数
    full[d]   ← 全池在 d 日的流通市值中位数
    R_s       ← median over d of ( med150[d] / full[d] )

⇒ 本探针**不能**支持任何关于「组合自身市值暴露大小」的陈述 ——
分子（`port_diag`）至今算完即弃、未落盘（预注册 §7 第一条待办）。

## 抽样在逐日循环【外】⇒ 本量的性质

`aplus/backtest.py:651` 的 `sample_codes = benchmark_cohort(...)` 在 `:653`
的 `for day in dates` **之前** ⇒ 那 **150 只在 1395 天里不变**
⇒ 偏移是**逐日共模（common-mode）的固定偏移**
⇒ **1395 个比值取中位数压不掉它一丁点** —— 中位数只压逐日抖动。

## 三条必须先说清的口径

1. **窗口从产物现读**，代码里没有任何日期字面量（预注册 §8.1：
   我上一段把 `2021-01-04` 手打成 `2021-01-01`，而产物里本来就有真值）。
2. **不声称复现生产抽到的那 150 只**：`benchmark_cohort` 的 docstring 逐字写着
   「同一 seed 配一个**键顺序不同**的 `daily_map`，抽到的**不是同一批票**」
   ⇒ 本探针只声称「**任何一次抽样的读数都落在这个分布里**」，
   而生产那一次是该分布的**一次抽样**。
3. **日期轴调 `panel.market_date_axis`（唯一实现）**，用惰性 mapping 逐票喂，
   避免在内存里摆下 GB 级面板。

## 判据是零阈值的

只问「符号定不定得下来」，而符号零点就是 1.0 —— 那是符号本身，不是量出来的门槛。
所以这里**没有任何手打阈值**，也就绕开了「1.73% 算不算大」那个问题。

## 🚨 本文件**不改** `_size_tilt_label`

第四支需要**分子分母配对**的噪声带，而分子只在 779s 的回测路径里产生
⇒ **拿半个判据去改判据 = 把测不准的判据装成判据**。两侧都只写产物。
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import random
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _bootstrap import begin_run, configure_noisy_stderr  # noqa: E402

configure_noisy_stderr()

# ⚠️ 用 `provenance.stamp(...)` 属性调用而不是 `from ... import stamp` 裸名：
#   守卫 `test_registered_is_not_actually_stamp` 按 **AST** 找 `*.stamp(...)`。
from aplus import provenance  # noqa: E402
from aplus.backtest import MARKET_SAMPLE  # noqa: E402
from aplus.capweight import CapTable, implied_cap  # noqa: E402
from aplus.panel import window_dates  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

GENERATOR = "scripts/cap_exposure_sigma_probe.py"

# 🚨 种子**显式写死**，不许 `hash(day)`（PYTHONHASHSEED ⇒ 不可复现的阴性等于没跑）。
# 公式可推导、无挑选、无偏好：seed_r = BASE_SEED + r，r ∈ [0, REPS)。
BASE_SEED = 20261018
REPS = 1000  # 预注册 §3，由那个推导给出，不是手打的

# 预注册 §3：p̂ 落在 2.5%/97.5% 分位时的蒙特卡洛相对误差。
#   SE(p̂)/p = sqrt((1−p)/(p·R)) / φ(z_p)，p=0.025、z=−1.96
_P = 0.025
_Z = 1.96


def axis_end_exclusive(window_end_inclusive: str) -> str:
    """把产物自报的**闭区间**末端翻成 `window_dates` 要的**半开**末端。

    🚨 这两个 `end` **约定相反，而字段名一模一样**：

    | 出处 | 写法 | 约定 |
    |---|---|---|
    | `cap_weighted_benchmark.py::arm_stats` | `"window_end": days[-1]` | **闭区间**（最后一个真量到的日子） |
    | `aplus/panel.py::window_dates` | `[start, end)` | **半开**（docstring 逐字写着） |

    ⇒ 把前者直接喂给后者会**安静地少一天**。
    实测首跑量到 **1394** 而产物自报 **1395**，差的就是末端那一天
    （三只票的原始 parquet 独立数出 1394，**含** 2026-10-09 才是 1395
    ⇒ 错在探针侧，生产自洽）。

    ⇒ 修法**不是**放宽下面那道 `n_days` 交叉核对（那是对的，是它抓到了本条），
    是**把约定显式翻译一次**并留成模块级函数 ——
    否则这道翻译埋在 `_run()` 里，守卫够不着（第 181 层 / 判据不可问就等于没守卫）。

    ⚠️ 加的是**一个日历日**而不是「下一个交易日」：
    `window_dates` 比的是日期串，多出来的非交易日不在并集轴上，天然被跳过。
    """
    d = datetime.date.fromisoformat(window_end_inclusive)
    return (d + datetime.timedelta(days=1)).isoformat()


def nan_agrees(mine: float, theirs: float) -> bool:
    """口径对拍的一致性判定。**抽到模块级是为了让守卫能 import 它** ——
    它原先埋在 `_run()` 的逐票读循环里，守卫够不着（第 181 层）。

    🚨 它守的到底是什么，写在脸上防「键名与内容互相打脸」：
    判的是「矩阵这一格与生产 `CapTable.cap_on` 这一格**说的是同一件事**」，
    **不是**「两个数在数值上接近」——
    IEEE-754 里 `nan != nan`，而双侧 NaN 在这里**是一致**。

    三种情形，全部实测过（第一次运行 rc=4 打的就是第三种的反面）：
    - **双侧 NaN ⇒ 一致**（`301181.SZ / 2021-01-04`，而该票首根观测是
      2022-02-21 ⇒ 两侧都在正确地说「这天这只票还没有市值」）；
    - **单侧 NaN 对有限值 ⇒ 不一致**（🚨 这一支不许放宽 ——
      放宽它等于说「探针在这一格什么都读不出来也算数」，
      而那正是首跑 rc=4 想排除的那类假红）；
    - **双侧有限 ⇒ 逐位相等**。
    """
    if math.isnan(mine) or math.isnan(theirs):
        return math.isnan(mine) and math.isnan(theirs)
    return mine == theirs


def width_relationship(span_v: float, span_w: float) -> tuple[bool, str]:
    """两臂的**宽度关系由数算出来**，不许从手打的方向词里挑一个。

    🚨 本函数存在的原因是一次**产物自相矛盾**（2026-10-18 实测）：
    臂 V 区间宽 **0.7067**、臂 W 区间宽 **0.0232** ⇒ **W 比 V 窄约 28 倍**，
    而同一份产物的 `arm_W.shape`、本文件 docstring、段注释、打印标签
    **四处**都写着「噪声上界 ⇒ 比 V 宽 ⇒ 保守方向」。

    ⇒ 机制：臂 V 抽**一次** 150 只、跨全部 1395 天**不变**
      ⇒ 组合成分偏移 `c(seed)` 是**跨天共模**项
      ⇒ 对 1395 天取中位数**压不掉它** ⇒ **V 的宽度就是成分偏移的宽度**；
      臂 W 每天重抽 ⇒ `c` 每天都是新抽样 ⇒ 被中位数平均掉
      ⇒ 只剩逐日抖动 ⇒ **W 窄**。
    ⇒ 预注册 §2 的方向预测因此**被证伪**，已按 append-only 补记
      （§2 的符号判据本身一个字未动）。

    ⚠️ 臂 W 是**有放回**抽样（有效样本 < 150 ⇒ 逐日方差**更大**）
      ⇒ 这个偏差只会让 W **更宽** ⇒ 量到的窄是**被低估的**
      ⇒ 它**不能**解释掉那 28 倍（方差膨胀 `(N+n−1)/(N−1) ≈ 1.0275` ⇒ sd 仅 +1.4%）。

    返回 `(w_is_narrower, 人话)`，方向**只从 `<` 这一个运算符来**。
    """
    w_is_narrower = span_w < span_v
    if w_is_narrower:
        return True, (f"W 比 V **窄** {span_v / span_w:.2f} 倍"
                      f"（实测 W 宽 {span_w:.4f} vs V 宽 {span_v:.4f}）")
    return False, (f"W 比 V **宽** {span_w / span_v:.2f} 倍"
                   f"（实测 W 宽 {span_w:.4f} vs V 宽 {span_v:.4f}）")


def _phi() -> float:
    """标准正态密度在 ±z_p 处的值。**单独抽出来是为了推导串与它共用同一个值。**"""
    return math.exp(-0.5 * _Z * _Z) / math.sqrt(2.0 * math.pi)


def mc_relative_error(reps: int) -> float:
    """预注册 §3 的推导。**不许打印成手打常量** —— 打印出来的只能是算出来的。"""
    return _relative_error_coefficient() / math.sqrt(reps)


def _relative_error_coefficient() -> float:
    """§3 里那个 `1/√R` 之外的系数：`sqrt((1−p)/p) / φ(z_p)`。

    🚨 这三行原先只有前一行，中间系数是**手打的** `0.0571`，
    而真值是 **106.8600**（差 1873×）⇒ 预注册 §3 印出的 `R=1000 ⇒ 1.81%`
    真值是 **337.92%**。预注册 §8.5 已按「只补记不改写」登记该更正。
    ⇒ 现在系数本身也是算出来的：**中间量与结果走同一条推导路径**，
    两者不可能再各说各话（第 178 层 / 第 181 层）。
    """
    phi = _phi()
    return math.sqrt((1.0 - _P) / _P) / phi


def binomial_se(count: int, reps: int) -> float:
    """判据 V 真正依赖的那个精度：`n_gt/R` 这个**比例**的蒙特卡洛标准误。

    ⚠️ 与 `mc_relative_error` **不是同一个量**（第 7 层）：
    分子数的是**同一批 rep** 里落在 1.0 某一侧的条数，分位数误差数的是
    分布某个**位置**的定位精度。判据 V 的分支只读 `n_gt`/`n_lt`，
    所以它的精度必须由这个给出 —— 印一个 337.92% 的分位误差
    会让人以为判决毫无功效，而判决的实际功效是「有没有跨过 1.0」。
    """
    if reps <= 0:
        return float("nan")
    p_hat = count / reps
    return math.sqrt(p_hat * (1.0 - p_hat) / reps)


def _median(a, axis=None):
    """`np.nanmedian` 但不喷 All-NaN 警告（全 NaN 切片在下面被显式判掉）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


class _DateOnlyMap:
    """喂给 `panel.market_date_axis` 的**惰性** mapping。

    `market_date_axis` 只用到 `.values()` 与 `df["date"]`，所以这里逐只产出
    **只含 date 列**的帧就够 —— 真函数被真调用，而内存里同时只有一只票的一列。

    🚨 造它不是为了省内存，是为了**不写第二份「并集」**。
    `set.update(...)` 那三行我自己也能写，而写完就是形状 17：
    同一个语义被处理两次，其中一份永远没人测。

    ⚠️ 只收窄 **I/O**（`columns=["date"]`），**不**重做并集语义 ——
    路径仍然取 `store._master_path`（单一真相源），不许自己拼。
    """

    def __init__(self, store: LocalStore, codes: list[str]):
        self._store, self._codes = store, codes

    def values(self):
        for code in self._codes:
            path = self._store._master_path("daily", code)
            yield pd.read_parquet(path, columns=["date"])

    def __len__(self) -> int:
        return len(self._codes)


def build_cap_matrix(store: LocalStore, files: list[Path], axis: list[str],
                     parity_codes: list[str]):
    """逐票读 → 立刻降维成一根 float 列 → 丢掉原帧。

    返回 `(M, codes, parity)`：`M[i, d] = cap_on(codes[i], axis[d])`，
    `parity` 是与生产 `CapTable.cap_on` 的逐位对拍结果（外部锚，不是恒等式）。
    """
    axis_arr = np.asarray(axis, dtype="U10")
    codes: list[str] = []
    rows: list[np.ndarray] = []

    # 对拍用的夹具：真建一个生产自己的 CapTable，对同一批 (code, day) 取值。
    # ⚠️ 它只吃被点名的那几只，不会变成 GB 级面板。
    parity_daily: dict = {}
    for code in parity_codes:
        df = store.read_daily(code)
        if df is not None and not df.empty:
            parity_daily[code] = df
    parity_table = CapTable(parity_daily) if parity_daily else None

    n_mismatch = 0
    max_rel_diff = 0.0
    n_parity_checked = 0
    mismatch_cells: list[dict] = []

    for t, path in enumerate(files):
        df = store.read_daily(path.stem)
        if df is None or len(df) == 0 or "date" not in df.columns:
            continue
        # 口径逐字抄 `capweight.CapTable._series`：implied_cap → ffill → 按 (code, day) 查
        cap = implied_cap(df).ffill()
        series = pd.Series(
            cap.to_numpy(float),
            index=df["date"].to_numpy("U10"),
        )
        # `cap_on(c, d)` = 不晚于 d 的最后一根 ⇒ 对 d 而言就是前向填充
        row = series.reindex(axis_arr, method="ffill").to_numpy(float)
        codes.append(path.stem)
        rows.append(row)

        if parity_table is not None and path.stem in parity_daily:
            for d in (0, len(axis) // 3, len(axis) // 2, len(axis) - 1):
                mine = float(row[d])
                theirs = float(parity_table.cap_on(path.stem, str(axis_arr[d])))
                n_parity_checked += 1
                both_finite = math.isfinite(mine) and math.isfinite(theirs)
                # 🚨 判据在模块级 `nan_agrees`（含「为什么双侧 NaN 是一致」
                #   的完整取证）。它曾内联在这里 ⇒ 守卫够不着 ⇒ 第 181 层。
                same = nan_agrees(mine, theirs)
                if both_finite and not same:
                    denom = abs(theirs) if theirs != 0.0 else 1.0
                    max_rel_diff = max(max_rel_diff, abs(mine - theirs) / denom)
                if not same:
                    n_mismatch += 1
                    if len(mismatch_cells) < 12:
                        mismatch_cells.append({"code": path.stem,
                                               "day": str(axis_arr[d]),
                                               "cap_table": theirs,
                                               "matrix": mine})
        del df, cap, series
        if (t + 1) % 500 == 0:
            print(f"  逐票读 {t + 1}/{len(files)}", flush=True)

    M = np.vstack(rows) if rows else np.empty((0, len(axis)), dtype=float)
    parity = {
        "codes_checked": sorted(parity_daily),
        "n_values_checked": n_parity_checked,
        "n_mismatch": n_mismatch,
        "max_rel_diff": max_rel_diff,
        "anchor": "aplus.capweight.CapTable.cap_on（本探针的外部锚，不是恒等式）",
        "mismatch_cells": mismatch_cells,
    }
    return M, codes, parity


def arm_v(M: np.ndarray, full: np.ndarray, reps: int) -> np.ndarray:
    """臂 V（**主判据**）：抽 150 个下标**一次**、跨全部交易日不变。

    与生产逐位同形：`benchmark_cohort` 的 `sample_codes` 在逐日循环**之外**，
    所以那 150 只在 1395 天里不变 ⇒ 这是**逐日共模的固定偏移**。

    🚨 这里**不声称**抽到了生产那一次的那批票（键顺序不可复现，见 docstring），
       只声称「任何一次抽样的读数都落在 §2 那个分布里」。
    """
    n_codes = M.shape[0]
    out = np.empty(reps, dtype=float)
    for r in range(reps):
        rng = random.Random(BASE_SEED + r)
        idx = rng.sample(range(n_codes), MARKET_SAMPLE)
        out[r] = float(_median(_median(M[idx], axis=0) / full))
        if (r + 1) % 200 == 0:
            print(f"  臂 V {r + 1}/{reps}", flush=True)
    return out


def arm_w(M: np.ndarray, full: np.ndarray, reps: int) -> np.ndarray:
    """臂 W（对照臂，显式标注它**不是**主判据）：每天**独立**重抽。

    🚨🚨 **本 docstring 曾写着「跨天共模项被拆掉 ⇒ 噪声上界 ⇒ 比 V 更宽
    ⇒ 保守方向」，而实测方向是反的：W 比 V 窄约 28 倍。**
    机制与取证在模块级 `width_relationship` 的 docstring 里，
    产物与打印层的方向词**全部由它算出**，四处手打方向词已全部删除。
    一句话版：V 的成分偏移是**跨天共模**，中位数**压不掉**它 ⇒ V 宽；
    W 每天重抽 ⇒ 偏移被平均掉 ⇒ 只剩逐日抖动 ⇒ W 窄。

    ⚠️ 向量化实现是**有放回**抽样（`Generator.integers` 的语义），
       而臂 V 是无放回 ⇒ 两者**不是同一个分布**。
       ⇒ 产物必须写 `with_replacement: true`，不许把它读成「臂 V 的逐日版」。
       ⇒ 有放回使有效样本 < 150 ⇒ 逐日方差**更大** ⇒ 只会让 W **更宽**
         ⇒ 量到的窄是**被低估的**，不能被它解释掉。
       ⇒ `parity_max_abs_diff` 对拍的是**向量化 vs 同一次抽样的逐日循环**
       （验的是向量化本身没算错，**不是**验两种抽样律相等）。
    """
    n_codes, n_days = M.shape
    # 🚨🚨 **`(n_days, 1)` 不是 `(n_days,)`** —— 第一次跑出来的 `IndexError` 就是这里。
    #   numpy 的**多维高级索引是逐元素配对再广播**，**不做外积**：
    #   `M[(1395,150), (1395,)]` 的第二个下标按**末尾维**对齐
    #   ⇒ `(1395,)` 对上 `(1395,150)` 的最后一维 **150** ⇒ 广播失败。
    #   要的是「**对每天 d，取那天的 150 个下标**」= **外积**：
    #   `(1395,150)` 配 `(1395,1)` ⇒ 结果 `[d, j] = M[idx[d, j], d]`。
    #   ⚠️ 本条**第一次真跑才暴露**（第 9 种形状：新写的分支在被真实执行前
    #   语法正确 ≠ 跑得通）—— 臂 V 1000/1000 跑完了才轮到它崩，
    #   而它是这个函数**唯一**的取数表达式。
    #   ⇒ 权威不是这一行，是 `arm_w_loop`：改完必须与它逐位相等。
    day_cols = np.arange(n_days)[:, None]
    out = np.empty(reps, dtype=float)
    for r in range(reps):
        rng = np.random.default_rng(BASE_SEED + r)
        idx = rng.integers(0, n_codes, size=(n_days, MARKET_SAMPLE))
        out[r] = float(_median(_median(M[idx, day_cols], axis=1) / full))
        if (r + 1) % 200 == 0:
            print(f"  臂 W {r + 1}/{reps}", flush=True)
    return out


def arm_w_loop(M: np.ndarray, full: np.ndarray, reps: int) -> np.ndarray:
    """臂 W 的**逐日循环**参考实现，与 `arm_w` 用**完全相同的抽样流**。

    `Generator.integers` 按 C 序填 ⇒ 按天逐次抽 `size=MARKET_SAMPLE`
    得到的索引序列与一次性抽 `(n_days, MARKET_SAMPLE)` 逐位相同
    ⇒ 差值若非 0 就是**向量化那一侧算错了**。
    （第 20 层：选探针时要问「缺陷在两侧是否对称」。这里两侧共用同一份抽样，
    所以这个差分**不**可能因「两种抽样律不同」而恒等于 0 —— 那是上一版的老毛病。）
    """
    n_codes, n_days = M.shape
    out = np.empty(reps, dtype=float)
    for r in range(reps):
        rng = np.random.default_rng(BASE_SEED + r)
        per_day = np.empty(n_days, dtype=float)
        for d in range(n_days):
            idx = rng.integers(0, n_codes, size=MARKET_SAMPLE)
            per_day[d] = _median(M[idx, d])
        out[r] = float(_median(per_day / full))
    return out


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="分母侧市值偏移的符号可判性探针")
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--window-from",
                    default="data/reports/cap_weighted_benchmark.json::"
                            "diagnostics.a_shares_arm",
                    help="窗口的唯一来源。🚨 代码里不许有日期字面量"
                         "（预注册 §8.1 自曝：我上一段手打过一次）")
    ap.add_argument("--reps", type=int, default=REPS)
    ap.add_argument("--out", default="data/reports/cap_exposure_sigma.json")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    with begin_run("cap_exposure_sigma_probe", reps=args.reps, out=args.out):
        return _run(args)


def _run(args) -> int:
    # 🚨 `expect_bytes` 必须在 `_run()` **第一行**（不是 `main()`、不是盖章前）：
    #   它抓的是「这一跑执行的那份字节」，盖章在整场计算之后。
    #   放 `main()` ⇒ 窗口离执行太远；放盖章前 ⇒ 检查恒真（抓取点 == 盖章点）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    t0 = time.time()

    src_path, sep, json_path = args.window_from.partition("::")
    if not sep or not json_path:
        print("🚨 --window-from 必须是 <路径>::<JSON 路径>，不许写死日期",
              file=sys.stderr)
        return 2
    src_path = (ROOT / src_path) if not Path(src_path).is_absolute() else Path(src_path)
    src = json.loads(src_path.read_text(encoding="utf-8"))
    node = src
    for key in json_path.split("."):
        node = node[key]
    w_start, w_end = node["window_start"], node["window_end"]
    art_days = int(node["n_days"])
    print(f"窗口（现读 {args.window_from}）= [{w_start}, {w_end}]"
          f"  产物自报 n_days={art_days}", flush=True)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    print(f"日线 {len(files)} 只", flush=True)

    axis = window_dates(_DateOnlyMap(store, [p.stem for p in files]),
                        w_start, axis_end_exclusive(w_end))
    print(f"日期轴 {len(axis)} 天（[{axis[0]}, {axis[-1]}]，并集→裁剪，"
          f"唯一实现在 aplus.panel）", flush=True)
    if len(axis) != art_days:
        print(f"🚨 量到的 {len(axis)} 天 ≠ 产物自报 {art_days} 天"
              " ⇒ 本探针与生产不同轴，**不许引用**", file=sys.stderr)
        return 3
    if axis[-1] != w_end:
        print(f"🚨 日期轴末端 {axis[-1]} ≠ 产物自报 window_end {w_end}"
              " ⇒ 闭/半开约定翻译错了，**不许引用**", file=sys.stderr)
        return 3

    # 对拍夹具：取 4 只确定存在的票（字母序首尾 + 中段），不是随机挑
    all_stems = [p.stem for p in files]
    step = max(1, len(all_stems) // 4)
    parity_codes = [all_stems[0], all_stems[-1],
                    all_stems[2 * step], all_stems[3 * step]]

    M, codes, parity = build_cap_matrix(store, files, axis, parity_codes)
    n_codes, n_days = M.shape
    print(f"市值矩阵 {n_codes} × {n_days}（{M.nbytes / 1e6:.0f} MB），"
          f"用时 {time.time() - t0:.0f}s", flush=True)
    if parity["n_mismatch"]:
        print(f"🚨 与生产 `CapTable.cap_on` 对拍 {parity['n_mismatch']} 处不一致"
              " ⇒ 口径已被换掉，**不许引用**", file=sys.stderr)
        for c in parity["mismatch_cells"]:
            print(f"   {c}", file=sys.stderr)
        return 4

    finite = np.isfinite(M)
    full_valid = finite.sum(axis=0)
    full = _median(M, axis=0)
    n_codes_measured = int(finite.any(axis=1).sum())
    print(f"全池每日有效票数 min={int(full_valid.min())} "
          f"median={float(np.median(full_valid))}；入表 {n_codes_measured} 只",
          flush=True)
    if not np.isfinite(full).all():
        print("🚨 存在整日 `full` 为 NaN ⇒ 判据无法计算，**不许引用**",
              file=sys.stderr)
        return 5

    # ---- 臂 V（主判据）：生产同形，一次抽、跨天不变 ----
    Rs_v = arm_v(M, full, args.reps)
    n_gt = int((Rs_v > 1.0).sum())
    n_lt = int((Rs_v < 1.0).sum())
    n_eq = args.reps - n_gt - n_lt
    verdict = ("SYMBOL_NOT_DETERMINABLE" if (n_gt > 0 and n_lt > 0)
               else "SIGN_DETERMINABLE")

    # ---- 臂 W（对照臂 role=sensitivity）：每天独立重抽 ⇒ 成分偏移被平均掉
    #      ⇒ 实测**比 V 窄**（方向由下面的 `width_relationship` 算出，不手打）
    Rs_w = arm_w(M, full, args.reps)
    n_parity = min(3, args.reps)
    Rs_w_loop = arm_w_loop(M, full, n_parity)
    parity_max_abs_diff = float(np.max(np.abs(Rs_w[:n_parity] - Rs_w_loop)))

    span_v = float(np.percentile(Rs_v, 97.5) - np.percentile(Rs_v, 2.5))
    span_w = float(np.percentile(Rs_w, 97.5) - np.percentile(Rs_w, 2.5))
    w_is_narrower, width_text = width_relationship(span_v, span_w)

    rel_err = mc_relative_error(args.reps)
    out = Path(args.out)
    out = out if out.is_absolute() else (ROOT / out)
    payload = {
        "question": "基准抽样池的流通市值中位数相对全池，符号定得下来吗",
        "quantity_measured": ("denominator side only —— 150 只抽样池的流通市值中位数 ÷ "
                              "全池同日的中位数。本探针没有测分子。"),
        "what_this_cannot_prove": [
            "🚨 分子（组合持仓的逐日流通市值中位数）本探针完全没有测 —— "
            "它只在 CapContext.diag 里、算完即弃、未落盘（预注册 §7 第一条待办）。",
            "🚨 本探针不声称知道生产那一次抽到了哪 150 只（benchmark_cohort 的 "
            "docstring 逐字写着「同一 seed 配一个键顺序不同的 daily_map，抽到的"
            "不是同一批票」；且产物顶层没有 `sample` 键可供引用）。它只声称"
            "「任何一次抽样的读数都落在这个分布里」，而生产那一次是该分布的一次抽样。",
            "🚨 判据 V 回答的是分母侧的符号，**不能**推出 cap_exposure_ratio=1.0173 "
            "的方向标签是对是错 —— 那个比值的分子分母配对噪声至今没测过。",
            "🚨 本文件**不改** `_size_tilt_label`：第四支需要分子分母配对的噪声带，"
            "而分子只在 779s 回测路径里产生 ⇒ 拿半个判据去改判据 = 把测不准的判据"
            "装成判据。",
            "⚠️ C 臂的判决不受本文件任何影响。本文件对 D1 / D2 零影响。",
        ],
        "prereg": "docs/PREREG_CAP_EXPOSURE_SIGMA_2026-10-18.md"
                  "（写于任何实测数字之前；§8 是补记不是改写）",
        "window": {
            "start": w_start, "end": w_end,
            "source": f"{args.window_from}::"
                      "{window_start,window_end,n_days}",
            "hand_typed_dates_in_code": 0,
        },
        "axis_selfcheck": {
            "n_days_axis": len(axis), "artifact_n_days": art_days,
            "match": len(axis) == art_days,
            "axis_first": axis[0], "axis_last": axis[-1],
            "axis_impl": "aplus.panel.market_date_axis（全体并集，唯一实现）"
                         "→ window_dates 裁 [start, end)",
        },
        "n_codes": n_codes,
        "full_pool_daily": {
            "valid_codes_min": int(full_valid.min()),
            "valid_codes_median": float(np.median(full_valid)),
            "codes_measured": n_codes_measured,
            "note": ("停牌沿用 ffill（capweight 同一口径），所以「全池当日中位数」与"
                     "「当日真在交易的票的中位数」不是同一个量（第 7 层）。"
                     "预注册 §5.2 要求把它印出来：若 min 远小于全池则本量不可比。"),
        },
        "cap_on_parity": parity,
        "mc_relative_error": {
            "formula": "sqrt((1-p)/(p*R)) / phi(z_p) = [sqrt((1-p)/p)/phi(z_p)] / sqrt(R), "
                       "p=0.025, z_p=-1.96",
            "reps": args.reps,
            "value": rel_err,
            "coefficient_per_sqrt_R": _relative_error_coefficient(),
            "derivation": (f"p={_P}、φ(−{_Z})={_phi():.6f} ⇒ "
                           f"sqrt((1−p)/p)/φ = {_relative_error_coefficient():.4f}；"
                           f"R={args.reps} ⇒ {_relative_error_coefficient():.4f}"
                           f"/sqrt({args.reps}) = {rel_err:.4f}"),
            "caveat": ("⚠️ 这是 **2.5%/97.5% 分位位置**的相对误差，"
                       f"而 R={args.reps} 时 2.5% 分位背后只有 {int(_P * args.reps)} 个观测 "
                       "⇒ 正态近似在那里本就失效，这个数说的是「尾部位置不可测」。"
                       "🚨 **判据 V 不读分位**，它读的是下面 `decision_precision` 那个二项计数"
                       "—— 两者不是同一个量，不许互相代替（第 7 层）。"),
        },
        "decision_precision": {
            "what_it_measures": ("判据 V 的分支只读 n_gt_1 / n_lt_1 两个**计数** ⇒ "
                                 "判决的蒙特卡洛精度是这两个比例的二项标准误，"
                                 "不是分位相对误差。"),
            "p_above_1": n_gt / args.reps,
            "se_above_1": binomial_se(n_gt, args.reps),
            "p_below_1": n_lt / args.reps,
            "se_below_1": binomial_se(n_lt, args.reps),
            "note": ("🚨 这个数**不能**把判决变成有功效的：判据 V 是**零阈值**的"
                     "（只看符号零点 1.0 在不在分布里），它不需要把 p̂ 估准。"
                     "它只说明「实测计数离 0 或 1 有多远」——"
                     "n_gt_1=0 时上限是 (1−1/REPS)^REPS ≈ 0.37/千，"
                     "即『一个都没落在 1.0 之上』这句话本身的把握程度。"),
        },
        "arm_V": {
            "role": "primary",
            "shape": "抽 150 个下标一次、跨全部交易日不变（与 benchmark_cohort 逐位同形）",
            "sampling": "random.Random(seed).sample(range(n_codes), 150)，无放回",
            "seed_formula": f"{BASE_SEED} + r, r in [0, {args.reps})",
            "n_gt_1": n_gt, "n_lt_1": n_lt, "n_eq_1": n_eq,
            "Rs_min": float(Rs_v.min()), "Rs_p2_5": float(np.percentile(Rs_v, 2.5)),
            "Rs_median": float(np.median(Rs_v)),
            "Rs_p97_5": float(np.percentile(Rs_v, 97.5)),
            "Rs_max": float(Rs_v.max()),
        },
        "arm_W": {
            "role": "sensitivity",
            "explicitly_not_the_primary_arm": True,
            "shape": ("每天独立重抽 150 个下标 ⇒ 成分偏移每天都是新抽样 ⇒ "
                      "被「对 1395 天取中位数」平均掉 ⇒ 只剩逐日抖动。" + width_text),
            "sampling": "有放回（numpy 向量化）；生产的 rng.sample 是无放回 ⇒ "
                        "两者**不是同一个分布**",
            "with_replacement": True,
            "n_gt_1": int((Rs_w > 1.0).sum()), "n_lt_1": int((Rs_w < 1.0).sum()),
            "Rs_min": float(Rs_w.min()), "Rs_p2_5": float(np.percentile(Rs_w, 2.5)),
            "Rs_median": float(np.median(Rs_w)),
            "Rs_p97_5": float(np.percentile(Rs_w, 97.5)),
            "Rs_max": float(Rs_w.max()),
            "vectorization_parity": {
                "reps_compared": n_parity,
                "max_abs_diff": parity_max_abs_diff,
                "what_it_checks": ("向量化 vs 同一次抽样的逐日循环 —— 两侧共用同一份抽样流"
                                   "⇒ 这个差分**不**可能因「抽样律不同」而恒等于 0"),
                "what_it_does_not_check": "🚨 它**不是**在验有放回 ≈ 无放回",
            },
        },
        "arm_width_comparison": {
            "what_is_compared": ("两臂各自 R_s 的 p97.5 − p2.5（比值变换不改变宽度的大小关系）"),
            "span_V": span_v,
            "span_W": span_w,
            "ratio_W_over_V": span_w / span_v,
            "W_is_narrower_than_V": w_is_narrower,
            "text": width_text,
            "why": ("臂 V 抽一次 150 只、跨全部 1395 天不变 ⇒ 成分偏移是**跨天共模**项 ⇒ "
                    "对天数取中位数**压不掉**它 ⇒ V 的宽度**就是**成分偏移的宽度；"
                    "臂 W 每天重抽 ⇒ 该偏移被平均掉 ⇒ 只剩逐日抖动 ⇒ W 更窄。"),
            "sampling_law_caveat": ("臂 W 是**有放回** ⇒ 有效样本 < 150 ⇒ 逐日方差**更大** "
                                    "⇒ 这个偏差只会让 W 更宽 ⇒ 量到的窄是**被低估的**。"
                                    "方差膨胀 (N+n−1)/(N−1) ≈ 1.0275 ⇒ sd 仅 +1.4%，"
                                    "远小于实测的 28 倍。"),
            "prereg_correction": ("预注册 §2 原预测「W 比 V 宽 ⇒ 保守方向」，"
                                  "**方向被证伪**，已按 append-only 补记；"
                                  "§2 的符号判据本身未改动一个字。"),
        },
        "criterion_V": {
            "verdict": verdict,
            "question": "该 150 只池相对全池的市值偏移，符号定得下来吗？",
            "zero_threshold": "符号零点 1.0 是符号本身，不是量出来的门槛 ⇒ 本判据不含任何手打阈值",
            "n_seeds_above": n_gt, "n_seeds_below": n_lt,
            "meaning": {
                "SYMBOL_NOT_DETERMINABLE":
                    "该 150 只的市值中位数相对全池的偏移**符号定不下来** ⇒ "
                    "`cap_exposure_ratio = 1.0173` 被 `_size_tilt_label` 读成"
                    "「组合偏大盘」时，那句方向标签**没有噪声依据**。"
                    "⚠️ 这**不**等于「组合真的没有市值押注」—— 只等于「分母侧测不出」。",
                "SIGN_DETERMINABLE":
                    "在所测种子上符号一致 ⇒ `_size_tilt_label` 缺的只是「测不出」那一档，"
                    "缺陷范围仅到那一档。⚠️ 仍然**不**等于已证明组合无市值暴露。",
            }[verdict],
        },
        "discipline": {
            "expect_bytes_captured_in": "_run() 第一行（不是 main()，不是盖章前）",
            "changes_to_size_tilt_label": "无 —— 本次两侧都只写产物",
            "seed_rule": "seed_r = 20261018 + r，显式写死；🚨 不许 hash(day)",
            "affects_d1": False,
            "affects_d2": False,
            "classification": "手段（把一条承重墙的判据从散文变成可问的）",
            "green_light_counts": 0,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                              sort_keys=True), encoding="utf-8")
    # ★ 盖章必须在**写完之后**，且只对**真跑**生效 —— 手写 sidecar 是形状 16。
    provenance.stamp(out, GENERATOR, root=ROOT, data_root=args.data_root,
                     expect_bytes=expect_bytes,
                     extra={"verdict_criterion_V": verdict, "reps": args.reps,
                            "n_gt_1": n_gt, "n_lt_1": n_lt,
                            "Rs_median_arm_v": float(np.median(Rs_v)),
                            "axis_match": len(axis) == art_days,
                            "cap_on_parity_mismatch": parity["n_mismatch"],
                            "arm_W_parity_max_abs_diff": parity_max_abs_diff})

    print()
    print("=== 判据 V（主）===")
    print(f"  R_s > 1.0 的种子数 = {n_gt} / {args.reps}")
    print(f"  R_s < 1.0 的种子数 = {n_lt} / {args.reps}")
    print(f"  R_s == 1.0 的种子数 = {n_eq}")
    print(f"  R_s 中位 = {float(np.median(Rs_v)):.6f}"
          f"  [{float(Rs_v.min()):.6f}, {float(Rs_v.max()):.6f}]")
    print(f"  判决 = {verdict}")
    print("=== 臂 W（敏感性 role=sensitivity，有放回）===")
    print(f"  R_s 中位 = {float(np.median(Rs_w)):.6f}"
          f"  [{float(Rs_w.min()):.6f}, {float(Rs_w.max()):.6f}]")
    print(f"  宽度 p97.5−p2.5：{width_text}")
    print(f"  向量化 vs 逐日循环 最大差 = {parity_max_abs_diff:.3e}"
          f"（{n_parity} reps，量出来的，不是假设的）")
    print("=== 对拍（外部锚 = 生产 CapTable.cap_on）===")
    print(f"  比了 {parity['n_values_checked']} 个值，"
          f"不一致 {parity['n_mismatch']} 个")
    print("=== 日期轴自检 ===")
    print(f"  轴 {len(axis)} 天 vs 产物自报 {art_days} 天 ⇒ "
          f"一致 = {len(axis) == art_days}")
    print(f"=== 产物 ===\n  {out}")
    print(f"总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
