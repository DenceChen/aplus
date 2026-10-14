"""单因子权重必须在实盘路径上跑通——反转族 `{mom20:-1.0}` 只有**一个**因子。

## 为什么单独钉

`composite_score` 用 `min_count=len(weights)`：**任一因子 NaN → 合成分 NaN**。
从 4 个因子减到 1 个，几何上会让股票池从 ~4897 张到 ~5441，
也让下游每一处「假定有多个因子」的代码路径第一次真正跑到单因子分支。

晋级候选第一次只带一个因子时，如果实盘路径在这里崩了，
表现会是「赛马赢了、当天出不了名单」——**最坏的一种失败**：
裁决已经落盘、权重已经写进 `weights.json`，而平台交不出货。

所以在等赛马的这几分钟里先把这条路径走通。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import composite_score
from aplus.predict import build_predictions, filter_universe
from aplus.snapshot import COLUMNS


def _universe(n=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rows.append({
            "code": f"{600000+i:06d}.SH",
            "name": f"股票{i}",
            "mom20": float(rng.normal(0, 0.3)),
            "rev5": float(rng.normal(0, 0.05)),
            "vol20": float(abs(rng.normal(0.02, 0.01))),
            "vsur": float(abs(rng.normal(1.0, 0.3))),
            "turn20": float(abs(rng.normal(2.0, 0.5))),
            "turnratio": float(rng.normal(0, 0.1)),
            "ivol": float(abs(rng.normal(0.02, 0.005))),
            "rsi": float(rng.uniform(10, 90)),
            "isST": int(rng.random() < 0.05),
            "volume": float(rng.uniform(1e5, 1e7)),
            "listed_days": int(rng.integers(60, 900)),
            "last_date": "2026-10-09",
            "buyable": bool(rng.random() > 0.05),
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def test_single_factor_weights_score_every_row():
    """单因子权重的合成分不能是 NaN —— `min_count=1` 意味着只看 mom20。"""
    u = _universe()
    s = composite_score(u, {"mom20": -1.0})
    assert s.notna().all(), f"单因子权重却有 {int(s.isna().sum())} 行为 NaN"


def test_single_factor_ranking_inverts_momentum():
    """mom20 取负权重 → mom20 越高，得分越低。

    注意方向：第一版把这条断言写反了（断言 `idxmin()` 对应 mom20=−1.0），
    于是测试红、而实现其实是对的。`idxmin()` 是**得分最低**的那行，
    负权重下它对应 mom20 **最高** = +1.0。
    同文件里端到端那条（涨榜落在超跌一端）当时就是通过的，两条互相印证。
    """
    u = _universe(50)
    u = u.assign(mom20=np.linspace(-1.0, 1.0, 50))   # 明确排序
    s = composite_score(u, {"mom20": -1.0})
    lowest_score = u.loc[s.idxmin(), "mom20"]
    highest_score = u.loc[s.idxmax(), "mom20"]
    assert lowest_score == pytest.approx(1.0), "得分最低的行应是 mom20 最高的（超涨）"
    assert highest_score == pytest.approx(-1.0), "得分最高的行应是 mom20 最低的（超跌）"


def test_pipeline_runs_end_to_end_with_single_factor_weights():
    """filter_universe → build_predictions 在单因子权重下必须出行，且方向正确。"""
    u = _universe(200)
    u = u.assign(mom20=np.linspace(-1.0, 1.0, 200))
    kept = filter_universe(u, "2026-10-09")
    w = {"mom20": -1.0}
    up, down = build_predictions(kept, top_n=5, bottom_n=5, weights=w)
    assert len(up) == 5 and len(down) == 5
    # 涨榜应挑 mom20 最低的（-1.0 那一端）
    worst = u.nsmallest(10, "mom20")["code"].tolist()
    assert set(up["code"]) <= set(worst), "涨榜没有落在超跌一端"
    # 跌榜应挑 mom20 最高的
    best = u.nlargest(10, "mom20")["code"].tolist()
    assert set(down["code"]) <= set(best), "跌榜没有落在超涨一端"


def test_single_factor_pool_is_wider_than_multi_factor_pool():
    """记录一条事实：因子越少，股票池越大。

    这不是缺陷，是 `min_count=len(weights)` 的直接后果。
    但它曾与「股票池可比闸」冲突——闸门原按绝对差一刀切，
    会把「4 因子换 1 因子」这种正常候选误判为不可比。
    该闸已改为**单向**（只拦变窄）。本测试把这个事实钉住，
    免得将来有人为了「股票池整齐」去改 `min_count` 语义。
    """
    u = _universe(200)
    u.loc[:20, "turn20"] = np.nan          # 只有换手率缺失
    multi = composite_score(u, {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "turn20": -1.0})
    single = composite_score(u, {"mom20": -1.0})
    assert int(single.notna().sum()) > int(multi.notna().sum())


def test_scores_are_not_all_tied():
    """防 `robust_z` 那类退化：单因子下也必须分得开。"""
    u = _universe(200)
    s = composite_score(u, {"mom20": -1.0}).dropna()
    assert len(s.unique()) > len(s) * 0.9, "单因子合成分几乎全是并列 —— 排名失效"
