"""候选必须是**复合**的：权重 + 调仓频率 + 缓冲区，三者一起进赛马。

## 事故（2026-10-11 20:35）

独立验证（2019-2023 干净数据，24 组）给出的结论：

| 纯反转 `{mom20:-1.0}` | 年成本 | 净 | t |
|---|---|---|---|
| 日频 K=1 | 246.3% | **−168.9%** | 6.32 |
| **月频 K=21** | 35.8% | **+77.6%** | 9.19 |
| **季频 K=63** | 12.2% | **+105.7%** | 11.17 |

**同一组权重、同样的信号，只改频率，净值从 −169% 变成 +106%。**

而 `candidate_weights` 只产出**权重**，`oos_decision` 的全部闸门
也只看得懂权重。若频率不进候选池：

- 赛马永远在日频下比，而日频下**任何**权重都是负的
- 「加权��还是调频率」这个真正的问题，根本不会被问出来
- 实盘 `run_daily.py` 也没有频率概念，每天全换

于是每一轮赛马都在一个**已知不可行的执行方式**下评判信号强弱，
得出的权重再送进那个不可行的执行方式。**测得再准也落不了地。**

## 复合候选的形状

```python
{"weights": {"mom20": -1.0}, "rebalance_every": 21, "buffer_multiple": None}
```

`weights` 仍是那本账，频率与缓冲是执行参数。
`oos_decision` 的六道闸全部作用在**扣成本后的净值**上——
`excess_tstat` 内部已经 `actual_pct - cost`，所以低频候选天然占优，
不需要、也不应该再为它单开一道「低频加分」的闸（那等于给结论配结论）。
"""
import pytest

from aplus.rebalance import BUFFER_MULTIPLE


def _cand(w, every=1, buf=None):
    return {"weights": w, "rebalance_every": every, "buffer_multiple": buf}


def test_candidate_carries_execution_params():
    c = _cand({"mom20": -1.0}, 21)
    assert c["weights"] == {"mom20": -1.0}
    assert c["rebalance_every"] == 21


def test_plain_weight_dict_is_still_accepted():
    """纯权重 dict 必须继续能用（回测脚本、历史数据都是这个形状）。"""
    from aplus.evolve import split_weights
    w, every, buf = split_weights({"mom20": -1.0})
    assert w == {"mom20": -1.0}
    assert every == 1 and buf is None


def test_split_reads_all_three_fields():
    from aplus.evolve import split_weights
    w, every, buf = split_weights(_cand({"mom20": -0.5}, 21, BUFFER_MULTIPLE))
    assert w == {"mom20": -0.5}
    assert every == 21
    assert buf == BUFFER_MULTIPLE


def test_default_buffer_is_not_silently_applied():
    """不写缓冲就是没有缓冲——默认开启会改写所有历史结论且无人察觉。"""
    from aplus.evolve import split_weights
    _, _, buf = split_weights(_cand({"mom20": -1.0}, 21))
    assert buf is None


def test_omitted_buffer_key_also_means_no_buffer():
    """**回归测试**（变异测试发现）：`buffer_multiple` 整个键被省略时，
    写 `candidate.get("buffer_multiple", 3.0)` 看起来像"给了默认值"，
    可上一条测试的 fixture 总是显式带这个键（值为 None），
    于是 `get(key, default)` 照样返回 None——**变异体文本变了、效果却是 no-op，
    测试照样绿**。

    真正能分辨的形状是「键根本不存在」。"""
    from aplus.evolve import split_weights
    _, _, buf = split_weights({"weights": {"mom20": -1.0}, "rebalance_every": 21})
    assert buf is None


def test_omitted_rebalance_key_means_daily():
    from aplus.evolve import split_weights
    _, every, buf = split_weights({"weights": {"mom20": -1.0}})
    assert every == 1 and buf is None


def test_buffer_below_one_is_rejected_loudly():
    """`buffer_multiple=0.3` 会让 `buffer_hold` 算出 `max(round(10*0.3),1)=3`，
    安静地变成「只留排名前 3」的另一个策略——几乎必然是把 3.0 写成了 0.3。"""
    from aplus.evolve import split_weights
    with pytest.raises(ValueError, match="buffer_multiple"):
        split_weights(_cand({"mom20": -1.0}, 1, 0.3))


def test_empty_weights_is_rejected():
    from aplus.evolve import split_weights
    with pytest.raises(ValueError, match="权重为空"):
        split_weights({"weights": {}, "rebalance_every": 21})


def test_frequency_and_buffer_candidates_are_deduplicated_by_all_three_fields():
    """去重必须看**三个**字段。`{mom20:-1} 日频` 与 `{mom20:-1} 月频`
    净值差 276 个百分点，算作同一组就把最关键的差异抹掉了。"""
    from aplus.evolve import dedupe_candidates
    xs = [_cand({"mom20": -1.0}, 1), _cand({"mom20": -1.0}, 21),
          _cand({"mom20": -1.0}, 21), _cand({"mom20": -1.0}, 21, 3.0)]
    out = dedupe_candidates(xs)
    assert len(out) == 3, f"期望 3 组（1日频/21月频/21月频+缓冲），实得 {len(out)}"


def test_objective_ranking_still_works_on_plain_dicts():
    from aplus.evolve import split_weights
    assert split_weights({"mom20": 1.0})[0] == {"mom20": 1.0}
