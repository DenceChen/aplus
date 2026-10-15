"""尾部价差配权对照臂的守卫（2026-10-15）。

## 为什么这个模块的守卫特别要紧

它回答的是「**改目标函数**有没有用」——而项目已经在这条路上投了不少。
一个会让 C 臂在样本内赢、测试段输的判决，等于把全部人力引向一条死路，
而它自己一路绿灯（这与 `test_diagnose_hit_rate.py` 是同一个病）。

所以这里钉的不是「函数返回了字典」，而是**读这个字典的人会不会被骗**。

## 三条最容易骗人的地方（每条都有守卫）

1. **签名从全样本算** ⇒ 自证，且不报错。
2. **`None` 被当成 0** ⇒ 「测不出来」变成「实测为零」，
   而后者会去压低一个因子的权重。
3. **样本薄却报「C 更好」** ⇒ 预注册 A3 明确禁止。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import spread_tilted_arm as sta  # noqa: E402


FACTORS = ["mom20", "rev5", "vol20", "vsur", "turn20", "turnratio", "ivol", "rsi"]


def _stats(spreads, ics=None):
    """构造一份 train_spread_and_ic 的返回。"""
    ics = ics if ics is not None else {f: 1.0 for f in FACTORS}
    return {f: {"ic_mean": ics[f], "ic_n": 100,
                "tail_low_mean": 0.01, "tail_low_n": 100,
                "tail_high_mean": 0.00, "tail_high_n": 100,
                "spread": spreads[f], "spread_n": 100}
            for f in FACTORS}


# ══════════ 权重构造：三臂必须同构，唯一差别是排序依据 ══════════

def test_the_two_tilted_arms_are_isomorphic_and_differ_only_by_the_sort_key():
    """A 与 C 臂的**构造方式**必须同构，唯一差别是排序依据。

    ## 「同构」指什么（本条第一版写错过，记下来）

    我第一版断言「两个构造器喂同一份数据必须给出逐位相同的权重」——
    **那是错的断言**。`ic_tilted` 按 IC 降序、`spread_tilted` 按价差降序，
    两者的**语义本就不同**：
    - IC 高 = 因子本身信息强 ⇒ 给它**正**权重；
    - 尾部价差高 = **反转它**收益大 ⇒ 给它**负**权重才对。

    ⇒ 同一份递增数据喂进去，A 给小的正权重、C 给大的正权重
    （C 把最大的统计量排在**队尾**）。**符号相反是正确行为**，
    恰好也是 A/C 对照想看到的东西。

    ⇒ 真正的同构性是：**构造器只做「排序 → 线性倾斜 → 归一化」，
    不含任何额外信息**。可测的代理：把排序依据**换成同一个字段**
    （`spread_tilted_weights` 也读 `ic_mean` 的那份 stats），
    两者必须给出**完全相反**的权重 —— 因为 C 会把 ic_mean 最大的排最后。
    这就证明了 C 里没有掺进任何 A 没用的量。

    ⚠️ 若 A 用 `R⁻¹·s_f`（带协方差求逆）而 C 用简单倾斜，
    A1 就混进了「是否用协方差」，测不出「按哪个量排序」⇒ 对照失效。
    """
    sp = _stats({f: i / 10.0 for i, f in enumerate(FACTORS)})
    ic = _stats({f: 0.0 for f in FACTORS},
                ics={f: i / 10.0 for i, f in enumerate(FACTORS)})
    a = sta.ic_tilted_weights(ic, FACTORS)
    # C 臂读同一份 stats ⇒ 按 ic_mean 降序 ⇒ 权重应当是 A 的**镜像**
    c_mirror = sta.spread_tilted_weights(ic, FACTORS)
    assert set(a) == set(c_mirror), "两个构造器的因子集不同"
    for f in FACTORS:
        assert a[f] == pytest.approx(-c_mirror[f]), \
            (f"构造器不同构：{f} 上 A={a[f]} 而 C={c_mirror[f]}，"
             "两者不是互为相反数 ⇒ C 掺进了 A 没有的量")

    # 而真正给它们不同排序依据时，权重必须不同（否则对照是空转）
    assert sta.spread_tilted_weights(sp, FACTORS) != c_mirror, \
        "排序依据变了权重却没变 ⇒ 构造器没在用排序依据（恒真的对照）"


def test_a_tilted_weight_keeps_every_factor():
    """八个因子**一个都不能少**。

    `composite_score` 只取 `factor_rows[list(weights)]`
    ⇒ 少一个因子，它贡献**恒等于 0**，而报告上一切正常
    （`aplus-weights-are-v1-momentum-legacies` 记的老坑：4 个因子白算）。
    """
    sp = _stats({f: (i % 3) / 10.0 for i, f in enumerate(FACTORS)})
    w = sta.spread_tilted_weights(sp, FACTORS)
    assert set(w) == set(FACTORS), f"权重少了因子：{set(FACTORS) - set(w)}"
    assert all(v is not None for v in w.values())


def test_equal_weights_is_exactly_equal_and_unused_by_any_tuning_knob():
    """B 臂是等权，**没有任何自由参数**（预注册 §2 规则 3）。

    ⚠️ 一旦给 B 加任何可调的东西，它就退化成「我调的一个 C」，
    三条臂就不再是三个独立对照。
    """
    w = sta.equal_weights(FACTORS)
    assert len(set(w.values())) == 1, "等权臂出现了不等权"
    assert sta.equal_weights(FACTORS) == w, "等权臂不可复现（不许有任何随机/顺序依赖）"


def test_a_missing_spread_raises_instead_of_silently_dropping_the_factor():
    """训练段测不出价差的因子 ⇒ **报错**，不许静默排到队尾。

    ⚠️ 排到队尾 = 给它接近 0 的权重 = 它贡献恒等于 0，
    而报告上一切正常。⇒ 让它响亮。
    """
    sp = _stats({f: 0.05 for f in FACTORS})
    sp["ivol"] = dict(sp["ivol"], spread=None)
    with pytest.raises(ValueError, match="测不出尾部价差"):
        sta.spread_tilted_weights(sp, FACTORS)


# ══════════ 样本薄不许报「C 更好」（预注册 A3）═════════

def _fake_out(c_annual, a_annual, n_blocks, c_mono=None, weights="distinct"):
    """构造一份产物。

    ⚠️ **`weights` 默认给一组不同的权重**（2026-10-15 改）。
    第一版这个夹具**根本不带 `arm_weights`**，而那时 `A0` 是两态的
    （`.get()` 返回 None ⇒ `None is not None` 为假 ⇒ 判「不退化」）
    ⇒ 下面**每一条** A1 断言都是在「权重从未被读过」的状态下发的，
    守卫全绿而它守的东西压根没跑。

    A0 改成三态（`None` = 测不出）之后，这些夹具当场全部转红 ——
    **红灯数 = 此前被两态写法放过的那几条**。
    ⇒ 真实产物一定带 `arm_weights`，夹具也必须带；
    要测「缺档」就显式传 `weights=None`。
    """
    def arm(annual):
        return {"annual_net": annual, "mean_daily": None, "t": None,
                "n_days": 100, "n_blocks": n_blocks, "monotonic": c_mono,
                "evidence": "薄" if n_blocks < sta.MIN_DAILY_POINTS else "充分"}
    out = {"kind": "spread_tilted_arm",
           "test": {"arms": {"A_IC_TILTED": arm(a_annual),
                             "B_EQUAL": arm(None),
                             "C_SPREAD_TILTED": arm(c_annual)},
                    "common": arm(a_annual)}}
    if weights == "distinct":
        out["arm_weights"] = {
            "A_IC_TILTED": {f: round(0.1 * (i + 1), 4) for i, f in enumerate(FACTORS)},
            "C_SPREAD_TILTED": {f: round(0.1 * (8 - i), 4) for i, f in enumerate(FACTORS)}}
    elif weights == "identical":
        same = {f: round(0.1 + 0.01 * i, 4) for i, f in enumerate(FACTORS)}
        out["arm_weights"] = {"A_IC_TILTED": dict(same), "C_SPREAD_TILTED": dict(same)}
    elif weights is not None:
        raise ValueError(weights)
    return out


def test_a_thin_sample_must_not_produce_an_A1_verdict():
    """🚨 样本不足 681 点时 A1 必须为 **None**，且理由写明「不判定」。

    ⚠️ 这正是本仓反复出现的陷阱：看到 C 比 A 好，
    最省事的「修法」是把样本限制去掉或换个窗口重跑，直到显著为止。
    ⇒ 预注册 A3 把这条钉死：**薄就是薄，不得判定**。
    """
    out = sta.diagnose(_fake_out(0.30, 0.10, n_blocks=100), FACTORS)
    assert out["A1_C_not_worse_than_A"] is None, \
        "样本 100 个独立观测却给出了 A1 判决（C 赢得很明显也不行）"
    assert "不判定" in out["A1_note"]
    assert out["A3_thin_sample"] is True


def test_a_sufficient_sample_does_give_an_A1_verdict_and_records_a_loss():
    """样本充分时 A1 **必须**给出判决，且 C 更差时**如实记为 False**。

    ⚠️ 预注册 §3 的不可让步条款：判据方向不可事后改。
    若 C 显著更差，正确的动作是**记录这个事实并去查为什么**，
    不是回头把 A1 改成「C 更好」才算过。
    """
    out = sta.diagnose(_fake_out(0.10, 0.30, n_blocks=900), FACTORS)
    assert out["A1_C_not_worse_than_A"] is False, \
        "C 明显更差却判成 True ⇒ 判据被事后掰弯了"
    assert "证伪" in out["A1_note"], "C 更差时必须写明「改目标函数被证伪」"


def test_gates_are_not_all_passing_when_nothing_was_measured():
    """全 `None` 的产物必须**闸门全红**，不许出现「无法测 ⇒ 判过」。

    ⚠️ 这是第 23 层（假绿）的经典形态：把「测不出」当成「没问题」。
    """
    out = sta.diagnose(_fake_out(None, None, n_blocks=0), FACTORS)
    assert out["gates_passed"] == 0, (
        f"一条都没测出来却过了 {out['gates_passed']} 道闸 —— "
        "「无法证明」在晋级上必须与「不合格」同价")


# ══════════ 口径：`None` 不是 0 ══════════

def test_none_is_dropped_but_zero_is_kept_when_building_the_spread():
    """分层 IC 为 **0** 是有效观测；`None`（测不出）才丢。

    ⚠️ 用 `if v` 过滤会把 0 当成缺失 ⇒ 一个「实测为零」的因子
    被当成「没测出来」⇒ 它在价差里**消失**，而不是**贡献 0**。
    这两件事对 C 臂的影响完全不同。
    """
    vals = [0.01, None, 0.0, 0.03, None]
    assert sta._clean(vals) == [0.01, 0.0, 0.03], \
        "None 与 0 必须区别对待：0 是有效观测"


def test_the_preregistered_thresholds_are_not_hardcoded_literals():
    """G2/G3/G5 的阈值必须来自**具名常量**，改判据时只有一处要动。

    ⚠️ 判据散落在代码各处 = 迟早有一处没跟着改。
    而「改了一处没改另一处」会让判决看起来还是预注册的那套。
    """
    assert sta.MIN_T == 3.0, "石川 p.19-20 多重检验后门槛"
    assert sta.TARGET_ANNUAL == 0.20, "用户目标"
    assert sta.MIN_DAILY_POINTS == 681, "Chan p.84-85：SR=1 需 681 日频点"


# ══════════ 排序依据不许偷换变量 ══════════

def test_arm_a_sorts_by_the_signed_ic_not_by_its_magnitude():
    """A 臂按**带符号** IC 降序 ⇒ IC 最负的因子拿到**最小（负）权重**。

    ⚠️ 一旦改成 `abs(ic_mean)`，就等于偷偷多加了一个「强度」变量：
    A 臂会变成「最强的因子给正权重」，而 C 臂的排序依据是尾部价差
    （价差高 = **反转它**收益大）⇒ 两臂的**方向约定不再同源**，
    A1 测的就不再是「按哪个量排序」，而是「按哪个量排序 + 怎么定方向」。

    ⇒ 断言必须落在**符号**上：IC 最负者拿最小权重。
    只断言「权重互不相同」会被平局通过 —— 那正是这条缺陷的样子。
    """
    ics = {"mom20": 0.10, "rev5": 0.0, "vol20": -0.05, "vsur": 0.0,
           "turn20": 0.0, "turnratio": 0.0, "ivol": -0.90, "rsi": 0.0}
    w = sta.ic_tilted_weights(_stats({f: 0.0 for f in FACTORS}, ics=ics), FACTORS)
    assert w["ivol"] < 0, "IC 最负的因子必须拿**负**权重（带符号排序）"
    assert w["ivol"] == pytest.approx(min(w.values())), \
        "带符号降序时 IC 最小者名次最后；按 |IC| 排会把它顶到队首"
    assert w["mom20"] == pytest.approx(max(w.values())) and w["mom20"] > 0, \
        "IC 最大的因子必须拿最大权重（带符号降序）"


# ══════════ 年化乘数：244 必须是外部锚 ══════════

def test_annualization_uses_exactly_244_trading_days_not_252():
    """A 股年均 **244** 个交易日，**不是 252**。

    ⚠️ 写成 252 会把年化**虚高 3.28%** —— 而 3.28pp 在「差 20pp 才够线」
    的量级下**不会翻判决**，是一枚完美的静默偏差。
    ⇒ 断言钉的是**乘数本身**（244.0），不是「大概对得上」。
    """
    import pandas as pd
    s = pd.Series([0.001, 0.002, 0.0015, 0.0005])
    # 传的是**日净超额序列本身**，不是 DataFrame：
    # 第一版签名收 DataFrame 并去找 `excess_net`/`excess` 列，
    # 而 `RESULT_COLUMNS` 里**没有这两列** ⇒ 三个臂齐声报「无超额列」
    # 而脚本**退出码 0**（见 `_daily_excess` 的 docstring）。
    r = sta._annual_net(s)
    assert r["annual_net"] == pytest.approx(float(s.mean()) * 244.0), \
        f"年化乘数是 {r['annual_net'] / r['mean_daily']}，必须是 244"
    assert round(r["annual_net"] / r["mean_daily"], 6) == 244.0, \
        "244 写成了别的数（A 股年均交易日数，见 a-plus-friction-has-positive-floor）"


# ══════════ A/C 退化：同一条策略跑两遍时 A1 恒真 ══════════

def test_identical_arm_weights_must_be_reported_as_degenerate_not_as_a_pass():
    """🚨 A 臂与 C 臂权重**逐位相同** ⇒ 跑的是同一条策略 ⇒ A1 是 `X ≥ X`，**恒真**。

    ## 这不是假想（2026-10-15 实测）

    训练段上 ρ(带符号 IC, 尾部价差) = **+1.0000**、ρ(|IC|, 尾部价差) = **−1.0000**
    ⇒ 两个排序依据**给出完全相同的顺序**
    ⇒ `ic_tilted_weights` 与 `spread_tilted_weights` 返回**逐位相同的字典**
    ⇒ A1 变成 `C ≥ A` 即 `X ≥ X`，**永远成立**。

    ⚠️ 这在报告里长得**和「C 更好」一模一样**：一个绿绿的 True。
    而它实际上什么都没测——两条臂是同一条策略跑了两遍。

    ## 本条的判据

    权重逐位相同 ⇒ `A0_arms_degenerate` 为 **True**、A1 为 **None**、
    且理由里写明「同一条策略跑两遍 ⇒ 恒真 ⇒ 不判定」。
    """
    out = _fake_out(0.30, 0.30, n_blocks=900, weights="identical")
    r = sta.diagnose(out, FACTORS)
    assert r["A0_arms_degenerate"] is True, \
        "两臂权重逐位相同却没报退化 ⇒ A1 在结构上恒真"
    assert r["A1_C_not_worse_than_A"] is None, \
        "同一条策略跑两遍却给出了 A1 判决（那个判决恒真）"
    assert "恒真" in r["A1_note"], "理由必须写明它为什么不能判定"


def test_distinct_arm_weights_are_not_called_degenerate():
    """正控：权重**确实不同**时不得误报退化。

    ⚠️ 这是**过严守卫的解药**：把 A0 写成「总是判退化」会让它永远绿、
    且把每一次真实的 A1 判决都变成「无法判定」——
    那等于把主判据整个废掉（第 112 层）。
    """
    out = _fake_out(0.40, 0.30, n_blocks=900, weights="distinct")
    r = sta.diagnose(out, FACTORS)
    assert r["A0_arms_degenerate"] is False, "权重不同却报退化 ⇒ A0 过严"
    assert r["A1_C_not_worse_than_A"] is True, \
        "非退化且 C ≥ A 时必须给出判决（A0 把主判据废掉了）"


def test_an_artifact_without_arm_weights_is_not_called_degenerate():
    """产物里没有 `arm_weights` 时**无法判定**退化 ⇒ 不许当成「不退化」。

    ⚠️ 缺档必须报错、绝不回落：`.get()` 返回 None 而 `None is not None` 为假
    ⇒ 「没测」被当成「测了，没问题」—— 第 23 层（假绿）的经典形态。
    """
    r = sta.diagnose(_fake_out(0.40, 0.30, n_blocks=900, weights=None), FACTORS)
    assert r["A0_arms_degenerate"] is None, \
        "权重缺失时必须报「测不出」，不得当成「测了、不退化」"
    assert r["A1_C_not_worse_than_A"] is None, \
        "产物里没有 arm_weights 却给出了 A1 判决 ⇒ 「没测」被当成「测了」"
    assert "测不出" in r["A1_note"], "理由必须写明是「测不出」而不是「不劣化」"


# ══════════ 缺键必须报「无法测」，不许崩 ══════════

def test_an_artifact_without_the_common_block_is_reported_not_crashed():
    """产物缺 `test.common` 时必须**报「无法测」**，不许抛 KeyError。

    ⚠️ 崩溃与「测不出来」在下游**长得一样**（都没有判决），
    但崩溃会把**本来还能出的部分判决**（A2 / A3 / thin 标记）一起丢掉 ——
    读的人只看到「脚本炸了」，看不到「薄」这个仍然为真的事实。
    """
    def arm(annual):
        return {"annual_net": annual}
    out = {"kind": "spread_tilted_arm",
           "test": {"arms": {"A_IC_TILTED": arm(0.30),
                             "B_EQUAL": arm(0.20),
                             "C_SPREAD_TILTED": arm(0.40)}}}   # 故意没有 common
    r = sta.diagnose(out, FACTORS)          # 崩在这里就是缺陷
    assert r["gates_passed"] == 0, "一条都没测出来却过了闸门"
    assert r["A1_C_not_worse_than_A"] is None, "缺 common 却给出了 A1 判决"


# ══════════ 缺档必须报错 ══════════

def test_a_missing_data_root_errors_out_instead_of_producing_an_empty_verdict(tmp_path):
    import subprocess
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts/spread_tilted_arm.py"),
         "--data-root", str(tmp_path / "definitely_absent"),
         "--out", str(tmp_path / "out.json")],
        capture_output=True, text=True)
    assert p.returncode != 0, "缺档时退出码仍是 0"
    assert "缺档" in p.stdout
    assert not (tmp_path / "out.json").exists(), \
        "缺档时**不许**落一个空产物（会被当成当天的结论）"


# ══════════ A2/G4：合成分对齐守卫（2026-10-15 新增） ══════════
#
# ★ 这组守卫的由来：`monotonic` 此前是**夹具直接注入的字段**，
#   `_score_quartile_monotonic` 本体**零覆盖**。
#   而它的失败模式是返回 `None` —— 与「样本不足、真的测不出」
#   在产物里**长得一模一样**，exit code 还是 0。
#   三臂实跑第一次走到这一行就炸了：拿 `sc.index`（行号）去 merge
#   `code`（字符串），dtype 都对不上。
#
# ★★★ 更危险的是「只修 dtype 不修键」：`astype(str)` 之后 merge 匹配
#   **零行** ⇒ 样本不足 ⇒ `None`。**修得越像模像样，错得越深。**
#
# ⇒ 这组守卫的核心不是「函数返回了 bool」，而是
#   **「这个函数有能力返回 True」**——把结构上恒 None 与真测不出分开。


def _xs_and_results(n=80, *, hit_plan, weights=None, index_offset=0):
    """造一份**行号毫无意义**的横截面 + 对齐的结果表。

    ⚠️ `index_offset` 是关键：真实 `xs[day]` 的行号是 `panel.cross_section`
      过滤后的 **RangeIndex**，与股票代码没有任何关系。
      这里刻意把它推离 0，就是为了让「拿行号当代码」的实现立刻露馅。
    """
    import numpy as np
    import pandas as pd

    weights = weights or {"mom20": 1.0}
    df = pd.DataFrame({
        # ⚠️ 刻意**不带前导零**：带前导零时代码一进数值往返就永久丢失
        #   （"000012" → 12 → "12"），归一化再也接不回来 ⇒ 零匹配 ⇒ 静默 None。
        #   那是真实代码里更危险的一条路，由下面那条用例单独盯。
        "code": [str(100000 + i) for i in range(n)],
        "mom20": np.linspace(0.0, 1.0, n),              # 与分数严格同序
    })
    df.index = np.arange(index_offset, index_offset + n)   # 行号 ≠ 代码
    day = "2023-09-01"
    # ⚠️ `hit_plan` **必填且长度必须等于 n**：给它一个默认值，
    #   迟早会在某条用例里对不上 n，然后在 DataFrame 构造处炸得莫名其妙。
    plan = list(hit_plan)
    assert len(plan) == n, f"hit_plan 长度 {len(plan)} != n {n}"
    res = pd.DataFrame({
        "pred_date": day,
        "code": df["code"].to_numpy(),
        "direction": "up",
        "result": ["hit" if p else "miss" for p in plan],
    })
    return {day: df}, [day], res


def test_the_score_quartile_check_can_actually_reach_a_verdict_not_just_none():
    """**能力守卫**：数据齐、单调确实成立 ⇒ 必须给出 `True`。

    ⇒ 恒返回 `None` 的实现在这里必然红。
    ⇒ 这条是整组里唯一能挡住「静默测不出」的。
    """
    weights = {"mom20": 1.0}
    # 前 20 全 miss、次 20 里 7 中、下 20 里 13 中、末 20 全 hit
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is True, \
        f"单调确实成立却给不出判决（拿到 {out!r}）—— 量不出来不等于没问题"


def test_the_check_aligns_on_the_code_column_not_on_the_row_index():
    """键必须取 `universe["code"]` 这一列，**不是**行号。

    ⇒ 旧实现（`sc.index`）在这一条上 dtype 就对不上而崩；
      「只 astype(str) 糊过去」的版本会 merge 出零行 ⇒ 同样是 `None`。
    """
    weights = {"mom20": 1.0}
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan,
                                     index_offset=1000)
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is True, \
        "行号不是代码时量不出判决 —— 说明对齐用错了键（多半拿的是 index）"


def test_a_reversed_gradient_is_measured_as_false_rather_than_unmeasurable():
    """反控：**反向梯度必须判 `False`，不能滑成 `None`。**

    ⇒ `False`（测了、结论是反的）与 `None`（测不出）必须可区分：
      否则一个「越打分越不中」的策略，会被当成「样本不足、暂不下结论」
      而一路放行。
    """
    weights = {"mom20": 1.0}
    plan = ([1] * 20 + [1] * 13 + [0] * 7
            + [1] * 7 + [0] * 13 + [0] * 20)          # 与分数反向
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is False, \
        f"反向梯度应当判 False，拿到 {out!r} —— 不许滑成「测不出」"


def test_a_thin_sample_is_unmeasurable_and_that_is_different_from_false():
    """样本薄 ⇒ `None`（测不出），且 `None` ≠ `False`（测了、反的）。"""
    weights = {"mom20": 1.0}
    xs, dates, res = _xs_and_results(n=40, weights=weights,
                                     hit_plan=[0] * 20 + [1] * 20)
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is None, f"样本不足应当 None，拿到 {out!r}"
    assert out is not False, "None 与 False 混同 ⇒ 「没测」被读成「测了、反的」"


def test_the_join_normalizes_both_sides_instead_of_relying_on_chance():
    """对齐不许靠「两边碰巧都是 str」。

    ★ 变异台实测：把 `up["code"].astype(str)` 删掉，**20 条测试照样全绿**
      —— 现有夹具的结果表本来就是字符串，所以归一化从未被任何东西钉住。
      （第 112 层：守卫绿着，但它守的东西压根没跑。）

    ⇒ 这条用**整数 code 的结果表**把它钉死：归一化在，就对齐；
      归一化没了，merge 直接炸 —— 不再是「碰巧能用」。
    """
    import numpy as np
    weights = {"mom20": 1.0}
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)
    # 上游路径没归一：code 是 int64，而横截面那边是 str
    res = res.assign(code=res["code"].astype(np.int64))
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is True, \
        f"整数 code 的结果表应当照样对齐（拿到 {out!r}）—— 归一化被删掉了？"


def test_a_composite_score_of_the_wrong_length_is_refused_rather_than_paired_up(monkeypatch):
    """合成分与横截面**行数不一致 ⇒ 不许配对**，直接不判。

    ⚠️ 这条分支对真实的 `composite_score` 是**结构上不可达的**
      （它恒返回与 `factor_rows` 等长的 Series）。而「变异台存活」实测：
      把这个检查删掉，21 条测试**全绿** —— 也就是说它此前不是守卫，是注释。

    ⇒ 这里用**替换实现**把它变成可测的契约：换掉一个会改变返回长度的
      `composite_score`，函数必须拒绝配对，而不是按位置错位硬接。
      被钉住的是**接口契约**，不是某一次实现。
    """
    weights = {"mom20": 1.0}
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)

    real = sta.composite_score
    monkeypatch.setattr(sta, "composite_score",
                        lambda fr, w=None: real(fr, w).iloc[:-3])   # 短 3 行
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is None, \
        f"长度对不上时应当拒绝判定（拿到 {out!r}）—— 不许按错位配对"


# ── 🚨 第 120 层第三层：我信了一句**假注释**，并照着它写错了代码 ──
#
# 脚本第一版的 docstring 写着「`evaluate_weights` **不吐 score 列**」。
# **那是假的** —— `aplus/predict.py:167`：
#     PRED_COLUMNS = ["code", "name", "direction", "score"] + list(FACTOR_NAMES)
# 而 `evaluate_weights` 每行来自 `res = verify_predictions(preds, bars)`，
# `res` 继承 `preds` 的列 ⇒ 返回值**带着 `score`**。
#
# ⇒ 后果不是「多算了一份」，是 **merge 撞上同名列**：
#   pandas 把两侧都叫 `score` 的列改名成 `score_x`/`score_y`
#   ⇒ `merged["score"]` 抛 `KeyError` ⇒ 三臂实跑到这一步崩掉。
#
# ⇒ 重算的那份必须改名，且**那份同名 score 顺便成了免费的第三方真值**：


def _with_reported_score(xs, dates, res, weights):
    """给结果表补上**真实的** `score` 列（= 选股当时那份合成分）。"""
    import numpy as np
    import pandas as pd

    parts = []
    for day in dates:
        sc = sta.composite_score(xs[day], weights)
        codes = xs[day]["code"].to_numpy()
        parts.append(pd.DataFrame({"pred_date": day,
                                   "code": pd.Series(codes).astype(str).to_numpy(),
                                   "score": sc.to_numpy(dtype=float)}))
    ref = pd.concat(parts, ignore_index=True)
    return res.merge(ref, on=["pred_date", "code"], how="left")


def test_a_results_table_that_already_carries_score_must_not_break_the_join():
    """结果表**自带 `score`** 时不许崩、也不许分错列。

    ⇒ 旧写法（重算列也叫 `score`）在这里直接 `KeyError: 'score'`。
    """
    weights = {"mom20": 1.0}
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)
    res = _with_reported_score(xs, dates, res, weights)
    assert "score" in res.columns, "夹具没造出 score 列，测的不是那条路"
    out = sta._score_quartile_monotonic(xs, weights, dates, res)
    assert out is True, f"自带 score 列时应当照样判定，拿到 {out!r}"


def test_a_score_that_does_not_reconcile_with_the_reported_one_blocks_the_verdict():
    """对账闸门：重算的合成分与「选股当时那份」对不上 ⇒ **不判定**。

    ★ 这份 `score` 是**免费的第三方真值**：它是选股时用的分数。
      对不上 ⇒ **打分用的权重 ≠ 这里配权用的权重**
      ⇒ A2/G4 量的就是另一个东西，而四分位照样分得出来、看着还挺正常。
    """
    weights = {"mom20": 1.0}
    plan = ([0] * 20 + [1] * 7 + [0] * 13
            + [1] * 13 + [0] * 7 + [1] * 20)
    xs, dates, res = _xs_and_results(weights=weights, hit_plan=plan)
    res = _with_reported_score(xs, dates, res, weights)

    # 正控：逐位一致 ⇒ 能判定
    assert sta._score_quartile_monotonic(xs, weights, dates, res) is True

    # ⚠️ 篡改量刻意取 **0.01**（很小但远大于 1e-9）：
    #   这道闸门的全部意义就是「**任何非零**偏差都拦」——
    #   容差 1e-9 之下不存在「可以放过的非零偏差」。
    #   取大值（比如 0.5）反而会被一个被放宽的容差接住，守卫就废了。
    bad = res.assign(score=res["score"] + 0.01)
    assert sta._score_quartile_monotonic(xs, weights, dates, bad) is None, \
        "分数对不上还照判 —— 四分位分的是另一个量"


# ── 🚨 第 120 层第四例：`train_spread_and_ic` **零测试覆盖** ──────────────
#
# 它是整条对照臂的**防自证根基**：签名只在训练段上算，
# 测试段一根都不碰（预注册 §1）。而 `grep train_spread_and_ic tests/`
# 只有一条夹具的 docstring 提到它 —— **函数一次都没被调用过**。
#
# ⇒ 变异台 S1「把 `for day in train_dates` 换成遍历整个 `xs`」
#   （= 在全样本上定符号、再用同段验收，正是自证）**24 条测试一条都不红**。
#   而 S1 此前被记成「已杀」——那个 kill **来自语法错误**（IndentationError
#   ⇒ pytest rc=2 收集错误），不是来自任何守卫。


def _fake_forward_stack(monkeypatch, ic_by_day):
    """把前瞻栈换成可判定的假件：每个日期给一个**可区分**的 IC。

    ★ 关键：`ic_by_day` 让窗口内外的日期给出**截然不同**的值。
      若实现越界取数，均值必然被污染 ⇒ 守卫立刻红。
    """
    import pandas as pd

    monkeypatch.setattr(
        sta, "forward_returns",
        lambda cache, codes, day, enter, off: {"__day__": day})
    monkeypatch.setattr(
        sta, "make_ranked_forward",
        lambda u_idx, fwd: {"__day__": fwd["__day__"]})
    monkeypatch.setattr(
        sta, "daily_ic_ranked",
        lambda u_idx, rf, f: {"all": ic_by_day[rf["__day__"]],
                              "tail_low": ic_by_day[rf["__day__"]] * 0.5,
                              "tail_high": 0.0})

    def _xs(days):
        return {d: pd.DataFrame({"code": ["A", "B", "C"]}) for d in days}

    return _xs


def test_the_training_signature_must_ignore_days_that_are_not_in_the_training_window(monkeypatch):
    """🚨 签名**只**许看 `train_dates`；把窗口外的日期塞进 `xs` 必须毫无影响。

    ⇒ 这是「不在全样本上定符号」这条**防自证保证**唯一的落脚点。
      旧实现里它是一条 docstring，**不是守卫**。
    """
    train_days = [f"2023-01-{i:02d}" for i in range(1, 31)]     # 30 天，够 MIN_OBS
    out_days = ["2023-09-01", "2023-09-02"]                     # 测试段的两天
    ic = {d: 0.01 for d in train_days}
    ic.update({d: 999.0 for d in out_days})                     # 越界就爆表
    _xs = _fake_forward_stack(monkeypatch, ic)

    only_train = sta.train_spread_and_ic(train_days, _xs(train_days),
                                         {}, FACTORS)
    with_outsiders = sta.train_spread_and_ic(
        train_days, _xs(train_days + out_days), {}, FACTORS)

    assert only_train[FACTORS[0]]["ic_mean"] == pytest.approx(0.01, abs=1e-12), \
        "正控失败：窗口内 IC 不等于注入值"
    assert with_outsiders[FACTORS[0]]["ic_mean"] == pytest.approx(0.01, abs=1e-12), \
        (f"把测试段的两天塞进 `xs` 就改变了训练段签名 ⇒ "
         f"实现越界取数 = 全样本定符号 = 自证。"
         f"实测 ic_mean = {with_outsiders[FACTORS[0]]['ic_mean']}")
    assert with_outsiders[FACTORS[0]]["ic_n"] == len(train_days), \
        f"有效样本数被窗口外的日期撑大了：{with_outsiders[FACTORS[0]]['ic_n']}"


def test_a_thin_training_window_must_not_produce_a_spread_at_all(monkeypatch):
    """有效天数 < `MIN_OBS` ⇒ `spread` 必须是 `None`，**不是 0**。

    ⇒ 0 意味着「实测为零」，会把「没测出来」变成一个会压低权重的结论。
    """
    thin = [f"2023-01-{i:02d}" for i in range(1, 8)]             # 7 天 < MIN_OBS(30)
    ic = {d: 0.01 for d in thin}
    _xs = _fake_forward_stack(monkeypatch, ic)
    out = sta.train_spread_and_ic(thin, _xs(thin), {}, FACTORS)
    e = out[FACTORS[0]]
    assert e["spread"] is None, f"样本不足却给出了 spread = {e['spread']}"
    assert e["ic_n"] == len(thin), f"ic_n 报成了 {e['ic_n']}，应为 {len(thin)}"
