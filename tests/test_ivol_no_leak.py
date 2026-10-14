"""IVOL 不得前视：面板每个日期的 ivol 必须是**该日期自己**的截面。

## 事故（2026-10-11，性质最严重的一次）

`aplus/panel.py: _factor_series` 末尾原写着：

```python
out["ivol"] = ivol_series(r, m).iloc[-1] if len(m) else np.nan
```

`r` / `m` 是**全历史**序列，`ivol_series` 返回**全历史**序列，
`.iloc[-1]` 取的是**最后一根 K 线（数据末端）**的 ivol，
再赋给 `out["ivol"]` —— pandas 按索引对齐，标量广播到**窗口内每一天**。

后果：**回测里任何一个历史日的 ivol，值都等于数据末端的 ivol。**
这不是「略有偏差」，这是**整条时间轴上的前视泄露**：

- 2024-10-01 那天的 ivol，用的是 2026-10-09 的数据算出来的值
- 任何「ivol 有预测力」的结论，都可能只是「**未来某段行情的信息**泄漏到了过去」
- 2 年终裁第一名 `{ivol: -1.0}`、频率赛马 t=2.32 的 `{ivol: -0.3}`
  **两个结论都已被污染**，必须作废重跑

`iv` 出现在 `out` 里是唯一被 `.iloc[-1]` 处理的因子，其余 7 个因子都是
逐点序列——所以只查 ivol，其余因子通过泄露测试并不构成「全平台无泄露」的证据。

## 为什么这种错误能活下来

它不崩、不报错、不产生 inf/nan，横截面每一列都有数、都有区分度、
看起来完全正常。唯一能抓住它的测试形状是
`test_backtest.py::test_panel_respects_as_of_no_future_leak`：
**把 D 之后的数据改成天荒地老，看 D 的因子变不变**。
本文件把这个形状扩展到 ivol 上，并加上「ivol 必须逐日变化」这条更直接的哨兵。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import ivol_series
from aplus.panel import build_panel
from aplus.store import LocalStore

from tests.test_backtest import _noisy_stock


# 必须用**带噪声**的股票，不能用 `test_backtest._populate`：
# 后者是确定性单调序列（每日恰好 +1.2%），波动率恒等于 0，
# vol20/ivol 全体都是 ~1e-16 的浮点尘埃，
# 于是「改了未来值没变」和「变了」在数值上都是 1e-17 量级——
# **泄露测试跑在退化数据上，等于没跑**。
# 这是本轮发现的第三个「测试本身失效」事故。
@pytest.fixture()
def store(tmp_path):
    s = LocalStore(tmp_path)
    for i, drift in enumerate([0.012, 0.011, 0.010, -0.010, -0.011, -0.012, 0.008]):
        s.upsert_daily(f"60000{i+1}.SH", _noisy_stock(f"60000{i+1}.SH", drift=drift, seed=i))
    return s


def _fixture_is_non_degenerate(store):
    """哨兵前置条件：这条 fixture 必须真的产生有区分度的波动率。"""
    dates = store.read_daily("600001.SH")["date"].tolist()
    panel = build_panel(store, dates[60], dates[72])
    v = panel.cross_section(len(panel.dates) - 1)["ivol"].astype(float).to_numpy()
    assert np.nanmax(v) > 1e-6, f"fixture 退化（ivol 全是 {v}），泄露测不出东西"
    assert len(np.unique(np.round(v, 12))) > 1, "fixture 退化：横截面无区分度"


def _poisons_after(store, cut, cols=("close", "open", "volume")):
    for p in sorted((store.root / "master" / "daily").glob("*.parquet")):
        df = store.read_daily(p.stem).copy()
        df.loc[df["date"] > cut, list(cols)] = 9999.0
        store.upsert_daily(p.stem, df)


def test_ivol_changes_with_date(store):
    """哨兵：面板里 ivol 必须**逐日不同**。

    若实现退化成「全历史最后一个值广播」，整列会被压成常数，
    横截面排序对 ivol 就彻底失效——但表面毫无异常。
    """
    _fixture_is_non_degenerate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    panel = build_panel(store, dates[60], dates[72])
    rows = [panel.cross_section(i)["ivol"].to_numpy(float) for i in range(len(panel.dates))]
    mat = np.vstack(rows)
    assert mat.shape[0] >= 5, "样本太少，测不出来"
    # 至少有一行的取值与第一行不同
    assert not np.allclose(mat, mat[0]), "ivol 沿时间轴是常数 → 前视广播"


def test_ivol_no_future_leak(store):
    """把 cut 之后的价格/成交量改成 9999，cut 当天的 ivol 必须逐位不变。"""
    _fixture_is_non_degenerate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end, cut = dates[60], dates[72], dates[65]

    panel = build_panel(store, start, end)
    i = panel.dates.index(cut)
    before = panel.cross_section(i).set_index("code")["ivol"].astype(float)

    _poisons_after(store, cut)
    after = build_panel(store, start, end).cross_section(i).set_index("code")["ivol"].astype(float)

    common = before.index.intersection(after.index)
    assert len(common) > 0
    diff = (before[common] - after[common]).abs()
    both_nan = before[common].isna() & after[common].isna()
    bad = (~(diff <= 1e-12)) & (~both_nan)
    assert not bad.any(), (
        f"ivol 用了 {cut} 之后的数据："
        + str(before[common][bad].head(5).to_dict())
    )


def test_ivol_matches_local_recomputation(store):
    """面板 ivol 在每个日期上必须等于「只用该日之前数据」算出的值。

    这是泄露的**正向**检验：不只是「改了未来没变」，
    而是「未来根本没进来算」——用一个数据截断的独立实现对照。
    """
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[72]
    panel = build_panel(store, start, end)

    from aplus.panel import market_return_series

    code = panel.codes[0]
    daily = store.read_daily(code).sort_values("date", kind="mergesort")
    r_all = daily["close"].astype(float).pct_change()
    mkt = market_return_series({
        p.stem: store.read_daily(p.stem).sort_values("date", kind="mergesort")["close"]
        .astype(float).pct_change().rename(store.read_daily(p.stem).sort_values("date")["date"])
        for p in sorted((store.root / "master" / "daily").glob("*.parquet"))
    })

    for d in panel.dates:
        k = int((daily["date"].to_numpy() <= d).sum())
        truncated = ivol_series(r_all.iloc[:k].reset_index(drop=True),
                               pd.Series(mkt.reindex(daily["date"].to_numpy()[:k]).to_numpy()))
        expect = truncated.iloc[-1] if len(truncated) else np.nan
        got = panel.cross_section(panel.dates.index(d)).set_index("code")["ivol"].astype(float).get(code, np.nan)
        if np.isnan(expect) and np.isnan(got):
            continue
        assert np.isclose(got, expect, rtol=1e-6, atol=1e-12), (
            f"{d}: 面板 ivol={got} ≠ 截断重算={expect}"
        )
