"""用户可见的「逐年符号一致 N/N」这句话，必须从数据里**算出来**，不许手写。

## 起因：一句话说错了，而且错在最要紧的地方

`DEFAULT_WEIGHTS` 的符号翻转是本平台今天做的最大一处改动，它的证据句
「8 因子 × 8 年逐年符号 **32/32** 一致」被印在四处：

- `aplus/factor.py` 的权重注释
- `aplus/evolve.py` 的 caveat —— **这条直接显示在概览页给用户看**
- `scripts/signcorrect_freq_sweep.py` 的实验说明
- 测试的 docstring

实测 `data/reports/factor_ic_7y.json`：

    因子      有逐年记录年数  逐年符号          缺哪年
    mom20     8               --------          —
    rev5      8               ++++++++          —
    vol20     8               --------          —
    vsur      8               --------          —
    turn20    8               --------          —
    turnratio 7               -------            ['2019']
    ivol      8               --------          —
    rsi       8               --------          —

    合计 63 个 (因子×年) 观测，同号 63 个

三处错：

1. **32/32 算术上不自洽**——8 因子 × 8 年应为 64，不是 32。
2. **「8 年 × 8 因子」不成立**——`turnratio` 只有 7 年。
3. 真实数字是 **63/63**，比原先声称的更强。

`turnratio` 缺 2019 是有原因的：它需要累积成交量历史，2019 年初没有前值
（它的 `n_days=1630`，比其余因子的 1821 少 191 天，正好对上）。所以正确
说法不是「8 因子 × 8 年」，而是「**8 个因子里 7 个满 8 年，合计 63 个
(因子×年) 观测**」。

## 为什么必须改成算出来的

这条证据句**是给用户看的**，是符号翻转的全部说服力所在。手写的数字
必然会和数据漂移——它已经漂了一次。所以本文件把散文里的 N/N 反向钉到
IC 报告上：数据一变，测试就红，而不是等某个人想起来去改文案。

## 三条测试

1. 函数算出的 N/N 必须与报告里的逐年记录一致（自洽）。
2. 每个有全段 IC 的因子，其逐年符号必须与全段同号（这条才是证据本身）。
3. **caveat 里的 N/N 必须等于函数算出来的 N/N**（散文 ↔ 数据的反向引用）。
"""
import json
import re
from pathlib import Path

import pytest

from aplus.evolve import load_weights
from aplus.factor_ic import yearly_sign_consistency

IC_REPORT = Path("data/reports") / "factor_ic_7y.json"


@pytest.fixture(scope="module")
def ic_payload():
    if not IC_REPORT.exists():
        pytest.skip(f"没有 {IC_REPORT}")
    return json.loads(IC_REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def consistency():
    got = yearly_sign_consistency(Path("data/reports"))
    if got["unknown"]:
        pytest.skip(f"逐年一致性不可得：{got['note']}")
    return got


# ---------- 1. 自洽：算出来的 N/N 与报告一致 ----------

def test_counts_agree_with_the_raw_report(ic_payload, consistency):
    """函数报的 (agree, total) 必须等于报告里逐年记录的条数。

    守的是「别只数符号相同的，把缺记录的年份悄悄排除」。
    `turnratio` 缺 2019 是**事实**，必须计入分母被看见，而不是从表里抹掉。
    """
    years = {r["year"] for r in ic_payload["yearly"]}
    expected_total = len(ic_payload["factors"]) * len(years)

    missing = {f: sorted(years - {r["year"] for r in ic_payload["yearly"]
                                  if r["factor"] == f})
               for f in ic_payload["factors"]}
    real_total = expected_total - sum(len(v) for v in missing.values())

    assert consistency["total"] == real_total, (
        f"total 应为 {real_total}（{expected_total} 减缺记录的 "
        f"{sum(len(v) for v in missing.values())}），实得 {consistency['total']}；"
        f"缺记录的因子：{ {k: v for k, v in missing.items() if v} }")
    assert consistency["n_years"] == len(years)
    assert consistency["n_factors"] == len(ic_payload["factors"])


# ---------- 2. 证据本身 ----------

def test_every_factor_keeps_its_sign_in_every_year_it_was_measured(consistency):
    """没有任何一个 (因子×年) 与该因子的全段 IC 反号。"""
    assert consistency["agree"] == consistency["total"], (
        f"有因子在某些年反号了：{consistency['flips']}")
    assert consistency["agree"] > 0, "一个都没测到等于没有证据"


def test_a_factor_with_no_full_period_ic_cannot_claim_yearly_consistency(
        consistency):
    """逐年记录必须能追溯到一个**全段 IC**，否则它证明不了方向。

    允许因子缺某几年（数据不够，如 turnratio 的 2019），
    但不允许「逐年有数、全段没数」——那叫方向不明。
    """
    orphans = [f for f, d in consistency["per_factor"].items()
               if d["n_years"] > 0 and d["full_ic"] is None]
    assert not orphans, f"这些因子有逐年记录却没有全段 IC：{orphans}"


# ---------- 3. 散文必须反查数据 ----------

def test_caveat_number_must_be_computed_not_handwritten(consistency):
    """概览页 caveat 里的 N/N 必须等于实测 N/N。

    这是本文件的核心：那句话**显示给用户**，是符号翻转的全部说服力。
    手写的数字已经错过一次（写成 32/32，而实为 63/63）。
    """
    loaded = load_weights(Path("data/reports"))
    caveat = loaded.get("caveat") or ""
    truth = f"{consistency['agree']}/{consistency['total']}"

    found = re.findall(r"(\d+)\s*/\s*(\d+)\s*个（因子×年）观测", caveat)
    assert found, f"caveat 里找不到「N/N 个（因子×年）观测」的证据句：{caveat[:160]}"

    for a, b in found:
        # 81/87 是**旧配置**的赛马名次，与符号一致性无关，须排除。
        if (a, b) == ("81", "87"):
            continue
        assert f"{a}/{b}" == truth, (
            f"caveat 说 {a}/{b}，实测是 {truth}。"
            f"证据句必须从 yearly_sign_consistency() 取，不能手写。"
            f"实得：{caveat[:200]}")


def test_caveat_must_not_claim_a_factor_year_grid_it_did_not_measure(consistency):
    """caveat 不得声称「8 因子 × 8 年」这类满格说法。

    `turnratio` 只有 7 年。写「8 因子 × 8 年」等于把一个没测过的格子
    算进证据里——正是「用没做过的测量支持结论」。
    """
    loaded = load_weights(Path("data/reports"))
    caveat = loaded.get("caveat") or ""
    grid = re.search(r"(\d+)\s*因子\s*[×x]\s*(\d+)\s*年", caveat)
    if not grid:
        return          # 不写满格说法即可，不必报错
    f, y = int(grid.group(1)), int(grid.group(2))
    assert (f, y) == (consistency["n_factors"], consistency["n_years"]) and \
        f * y == consistency["total"], (
            f"caveat 声称「{f} 因子 × {y} 年」= {f*y} 个观测，"
            f"实测 {consistency['n_factors']}×{consistency['n_years']} 满格、"
            f"实际有记录 {consistency['total']} 个（"
            f"缺记录的因子：{ {k: v['missing'] for k, v in consistency['per_factor'].items() if v['missing']} }）")


def test_unknown_report_must_not_be_reported_as_consistency(tmp_path):
    """没有 IC 报告时必须 `unknown=True`，且 agree/total 为 None。

    返回 `agree == total == 0` 会被读成「查过了，零反号，一致」——
    而实际是「根本没查」。与 `weight_sign_conflicts` 的同一纪律。
    """
    got = yearly_sign_consistency(tmp_path)
    assert got["unknown"] is True
    assert got["agree"] is None and got["total"] is None
    assert "不等于" in got["note"], "note 必须写明这不等于「无反号」"