"""`ic_matrices` 抽取表的**合成市场**，供多个测试文件共用。

## 为什么要有这个共享件

`scripts/extract_ic_matrices.py` 曾长期靠 `/tmp` 里一张**手工产出的冒烟表**
被测试。那张表有三重问题：

1. **静默 skip** —— 文件不在就整条测试跳过。于是「测试通过」
   可能意味着「**什么都没测**」，而这两件事读起来一模一样。
2. **不受格式变更约束** —— 列名从 `R__ab` 改成 `R__<名>__<名>` 之后，
   它仍以旧格式躺在那里，于是测试仍在**测旧格式**，
   而生产的抽取脚本早就换了格式。
3. **跨会话状态** —— `/tmp` 里的东西由谁产生、何时产生、是否还兼容，
   测试文件自己不知道。

共享件让每个测试**当场造、当场用**：表必然与当前脚本同格式，
而「表在不在」不再是测试能否运行的前提。

## 为什么走真实 `extract()`

不是造一张长得像的表，而是**把真脚本跑一遍**。
否则 fixture 与被测对象会**共用同一个错误假设**，
两边同时绿，什么也证明不了——这正是 2026-10-14 那个
「R 按位置存、按顺序读」P0 溜过去的直接原因。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT = Path("scripts/extract_ic_matrices.py")

#: `FWD_WINDOWS['lag1_real']` 要 D+2 才卖得掉，最少也得有 4 个交易日
MIN_DAYS = 6


def load_extract_module():
    """按文件路径载入 `scripts/extract_ic_matrices.py`（它不是包内模块）。"""
    spec = importlib.util.spec_from_file_location("_extract_ic", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_market(factors=("f1", "f2", "f3"), n_stocks=220, n_days=14,
                 start="2021-06-01", seed=11):
    """造一份能真正跑通 `extract` 的迷你市场。

    收益用 `standard_t(df=4)` 而不是正态：**真实收益重尾**，
    而正态下「秩口径」与「原始值口径」的差会退化成 0，
    口径那一节的结论在合成数据上完全测不出来。

    > **每个因子的值只由它的名字决定，与 `factors` 的顺序无关。**
    > 第一版按 `factors[0]/[1]/[2]` 赋值，于是传 `("f3","f2","f1")`
    > 等于换了一个**完全不同的市场**——fixture 把「因子集合」
    > 和「因子顺序」混成了一件事，**那正是本次要治的病**，
    > 而且它出现在测试自己身上。
    """
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}.SZ" for i in range(n_stocks)]
    dates = pd.bdate_range(start, periods=n_days).strftime("%Y-%m-%d")

    cache: dict = {}
    ret = rng.standard_t(df=4, size=(n_stocks, n_days)) * 0.02
    px = 10.0 * np.cumprod(1.0 + ret, axis=1)
    for j, code in enumerate(codes):
        arr = pd.DataFrame({
            "date": dates,
            "close": px[j],
            "volume": np.full(n_days, 1e6),
            "amount": np.full(n_days, 1e7),
        })
        cache[code] = (arr["date"].to_numpy(dtype="U10"),
                       arr["close"].to_numpy(float),
                       np.vstack([arr["volume"].to_numpy(float),
                                  arr["amount"].to_numpy(float)]),
                       False)

    common = rng.normal(size=(n_stocks, n_days))
    xs: dict = {}
    for di, d in enumerate(dates[:-3]):      # 留够暖机与出场
        xs[d] = pd.DataFrame({
            "code": codes,
            # 三个因子：彼此相关，但**不互为仿射变换**
            "f1": common[:, di] + 0.8 * rng.normal(size=n_stocks),
            "f2": common[:, di] * 0.5 + rng.normal(size=n_stocks),
            "f3": np.abs(rng.standard_t(df=3, size=n_stocks)) * 0.03,
        })
    return cache, xs, list(factors)


def extract_table(factors=None, *, window="lag1_real", **kw) -> pd.DataFrame:
    """跑**真抽取脚本**，返回它写的那张表。

    `factors` 只改**抽取时的顺序**，不改市场本身——
    这样「同一组因子、不同书写」才是一个**可比**的对照。
    """
    mod = load_extract_module()
    order = list(factors) if factors else ["f1", "f2", "f3"]
    cache, xs, _ = build_market(**kw)
    rows, skipped = mod.extract(sorted(xs.keys()), xs, cache, order, window)
    assert rows, (f"一都没记下来（跳过 {len(skipped)} 天）"
                  f"——先看 MIN_N 与因子列是否真的存在")
    return pd.DataFrame(rows)