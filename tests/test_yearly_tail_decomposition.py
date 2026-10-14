"""`yearly` 必须和全段表**同口径**——现在它只报全截面，把最强的一档漏掉了。

## 起因：整条月频路线压在唯一一个看不到分布的数上

`aplus/ic_to_return.py` 把 IC 换算成收益之后，最强信号 `rev5` 低尾
（IC 0.04863）换出年毛超额 **26.7%**，月频门槛 **26.4%**——
「刚好越过」。

但把**全截面** IC 按逐年重算：

    2019 15.0%  2020 16.5%  2021 15.1%  2022 14.0%
    2023 10.7%  2024 13.3%  2025 22.5%  2026  5.6%
    → 八年里 0 个年份越过月频门槛

**全截面这条路是关的。**「月频可行」整个压在低尾那一个数上。

而低尾的逐年分解——**报告里根本没有**：

    factor_ic_7y.json 的 yearly 每行只有
    factor / year / n_days / ic_mean / hit

`scripts/factor_ic_diagnostic.py:120` 写死了 `r["all"]`：

    vals = [r["all"] for r in sel_all if ...]

`tail_low` / `tail_high` 两档**从未被逐年聚合过**。
于是「26.7% 刚过线」这个结论，**背后是一个没有分布的单点估计**。

> 若低尾 IC 也像全截面那样有 4 倍年度波动
> （2019 的 0.0274 到 2026 的 0.0103），
> 那么「刚好越过」在任何单独一年都可能是 5.6% —— **也就是不过线**。

## 这不是「新加一个功能」，是**口径不一致**

全段表 `table` 出三档（`all` / `tail_low` / `tail_high`），
逐年表 `yearly` 只出一档。**同一份报告里两套口径**，
而两套之间没有一个字段说明差异——读者会以为 `ic_mean` 就是那个 IC。

这与本项目已记录的「同一项目两处各自算同一个 t，各自绿但不可比」
是同一类：不是算错，是**算的东西不一样却并排放着**。

## 三条纪律

1. **同口径**：`yearly` 的每一档必须与 `table` 的对应档同源同法。
2. **不许静默截断**：某一档在某年样本不足时，该档**缺**，
   不许悄悄回落到 `all`——那正是这次 bug 的形态。
3. **老报告向后兼容**：只有 `ic_mean` 的旧行必须仍能读，
   但**新报告必须三档齐全**，否则测试红。
"""
import json
from pathlib import Path

import pytest


# ── 一、诊断脚本的 yearly 必须三档齐全 ─────────────────────────────

def test_yearly_reports_every_tail_bucket_not_just_the_full_cross_section():
    """**本文件的核心断言。**

    旧实现写死 `r["all"]`，于是 `tail_low` / `tail_high` 逐年**完全缺失**。
    """
    import inspect

    from scripts import factor_ic_diagnostic as diag

    src = inspect.getsource(diag.yearly)
    for bucket in ("all", "tail_low", "tail_high"):
        assert f'"{bucket}"' in src, (
            f"yearly 没有聚合 `{bucket}` 这一档——"
            f"全段表出三档、逐年表只出一档，是**口径不一致**，"
            f"读者会以为 ic_mean 就是那个 IC"
        )


def test_the_three_buckets_come_from_the_same_rows():
    """三档必须**从同一批逐日记录**聚合出来。

    如果某一档用了不同的过滤条件（比如剔掉了 NaN 更多的日子），
    那三档就不可比——而 `yearly` 的读者一定会拿它们互相比较。"""
    import inspect

    from scripts import factor_ic_diagnostic as diag

    src = inspect.getsource(diag.yearly)
    # 同一个 sel_all 供三档使用，而不是每档各自筛一遍
    assert src.count("sel_all") >= 2, "三档必须共用同一个 sel_all"


# ── 二、合成数据上的行为 ──────────────────────────────────────────

def _rows():
    """两条逐日记录，故意让三档的符号不同。

    这样「只报 all」和「三档齐全」在断言上就**不可区分**，
    测试才有分辨力。
    """
    return [
        {"factor": "f", "window": "lag1_real", "date": f"2023-01-{d:02d}",
         "all": 0.05, "tail_low": 0.09, "tail_high": -0.04}
        for d in range(1, 41)
    ] + [
        {"factor": "f", "window": "lag1_real", "date": f"2024-01-{d:02d}",
         "all": 0.01, "tail_low": 0.02, "tail_high": -0.01}
        for d in range(1, 41)
    ]


def test_yearly_keeps_the_tail_buckets():
    from scripts.factor_ic_diagnostic import yearly

    out = yearly(_rows(), ["f"], "lag1_real")
    assert len(out) == 2, "2023 与 2024 各一行"
    r23 = next(r for r in out if r["year"] == "2023")
    assert r23["ic_mean"] == pytest.approx(0.05, abs=1e-4)
    assert r23["tail_low"] == pytest.approx(0.09, abs=1e-4)
    assert r23["tail_high"] == pytest.approx(-0.04, abs=1e-4)


def test_a_year_with_too_few_tail_observations_is_missing_not_filled():
    """**不许静默回落。**

    某年 `tail_low` 样本不足时，该档应**缺**（None / 不出该年），
    而不是悄悄用 `all` 的值填上——那正是这次 bug 的形态：
    读者以为看到的是低尾，实际看到的是全截面。
    """
    import inspect

    from scripts import factor_ic_diagnostic as diag

    src = inspect.getsource(diag.yearly)
    # 不许出现「tail 缺失就退回 all」的兜底
    for pattern in ("tail_low or ", 'tail_low"] or ', "if v else"):
        assert pattern not in src, (
            f"yearly 里有 `{pattern}` 这样的回落兜底——"
            "样本不足必须**如实缺失**，不能拿别的口径顶替"
        )


def test_a_sample_below_min_obs_does_not_produce_a_row():
    """样本不足的年份整行不出现（沿用既有约定）。"""
    from scripts.factor_ic_diagnostic import yearly, MIN_OBS

    rows = [{"factor": "f", "window": "lag1_real",
             "date": "2023-01-01", "all": 0.05, "tail_low": 0.09,
             "tail_high": -0.04}] * (MIN_OBS - 1)
    assert yearly(rows, ["f"], "lag1_real") == []


# ── 三、已发布报告：结构上必须能承载三档 ──────────────────────────

def test_the_existing_report_is_recognised_as_missing_the_tail_yearly():
    """**这条不要求旧报告已经改好**——它要求我们**知道**它缺。

    `data/reports/factor_ic_7y.json` 是 2026-10-12 生成的，
    改动前必然没有低尾逐年。测试的作用是：
    **一旦重跑生成了三档，这条必须转绿**——
    绿了就说明补齐了，红着就说明还没补。

    > 故意写成「现在红」：它是一条**待办的可执行形式**，
    > 比 TODO 文档里的一行字更难被忽略。
    """
    path = Path("data/reports/factor_ic_7y.json")
    if not path.is_file():
        pytest.skip("报告不在（data/ 被 gitignore）")
    payload = json.loads(path.read_text(encoding="utf-8"))
    yearly_rows = payload.get("yearly") or []
    if not yearly_rows:
        pytest.skip("报告没有 yearly 段")
    has_tail = "tail_low" in yearly_rows[0]
    assert has_tail, (
        "factor_ic_7y.json 的 yearly 仍缺 tail_low/tail_high——"
        "这是 docs/TODO_measure_sigma_r.md 的任务 B。"
        "重跑 scripts/factor_ic_diagnostic.py 后应转绿。"
    )
