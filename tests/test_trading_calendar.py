"""交易日历的**取样**与**覆盖率判据**的守卫（2026-10-20）。

## 这一轮修的两个真实缺陷（都是**静默**的：不崩、不告警、判词照发）

**A. 样本有偏** —— `sorted(glob(...))[:40]` 是**按代码排序取前 40 只**：
实测那 40 只**全是 `000xxx`**（深市主板），`002/300/600/688` **一只都不在**。
而**判词正是由这个样本的新鲜度决定的** ⇒
「今天在不在库里」取决于**哪 40 只排在前面**，而不是市场真相。
实测（2026-10-20，仓内 5460 只）：按代码前 **40** 只里 **32 只**已到
2026-10-20（80%），而全仓真实比例 = **5207/5460 = 95.37%**；
按代码前 **200** 只**全是 `000xxx`**（`000` 前缀有 458 只）⇒
样本在代码空间上只覆盖 **13 个前缀里的 1 个**（仓里 `000` 前缀有 458 只）。
⇒ 旧样本的健康覆盖率读数 80%、均匀样本 ~95% ⇒ 同一个市场，两个读数。

**B. 前提是二值的** —— `known` 是**并集** ⇒ **只要有 1 只**票更新了就算
「今天在库里」⇒ 面板只覆盖 1% 也会放行 ⇒ 出一张**残缺榜**，
而**页面上一个字都不说**。

## 四条主守卫（各打在**真实失效形态**上）

1. **样本无偏**（**性质**守卫，不是「存在」守卫）：样本必须跨代码前缀
   （同时出现 `000/002/300/600/688`）⇒ 旧的 `sorted(...)[:40]` **必然红**。
2. **取样可复现**：连跑两次取到的样本**逐只相同**（`random.sample` 做不到）。
3. **覆盖率的三个分支**：覆盖 **0** / **低于阈值** / **不低于阈值**，
   三个分支各一个断言。
4. **纯函数性**：`classify_run_day` **不许读盘**（monkeypatch 住读盘入口
   + 一条 AST 静态检查，双保险）。

## 🚨 本文件所有涉及写盘的测试一律用 `tmp_path`
写探针时传了生产根就会覆盖真的产物（CLAUDE.md 第 132 层）⇒
**判据不许挂在人的记忆上**。

## 🚨 变异必读
判「这组守卫是不是安慰剂」**只能靠变异**，且**只有 `rc == 1` 才算被杀**
（rc∈{2,3,4,5} = 收集/用法/内部错误 ⇒ 不可验证）。
规格：`tests/mutation_specs/test_trading_calendar.mut`。
"""
from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aplus.trading_calendar as tc  # noqa: E402
from aplus.trading_calendar import (  # noqa: E402
    CALENDAR_SAMPLE, COVERAGE_MIN, RUN_DAY_LABELS, RUN_DAY_TRADING,
    RUN_DAY_TRADING_DAY_PARTIAL, RUN_DAY_WEEKEND,
    RUN_DAY_WORKDAY_NOT_IN_STORE, classify_run_day, coverage_detail,
    sample_paths, store_coverage, trading_days,
)

# 合成宇宙用的代码前缀。**故意超过 5 个** ——
# 少了的话，「按代码排序取前 N」与「哈希取前 N」可能落在同一批前缀上，
# 守卫 1 就分不出它们（那是一条**恒真**的守卫）。
PREFIXES = ("000", "001", "002", "003", "300", "301", "600", "688")
#: 任务书点名的五个前缀（守卫 1 的断言口径）。
REQUIRED_PREFIXES = ("000", "002", "300", "600", "688")

ORDINARY_DATES = ("2019-01-02", "2019-01-03", "2019-01-04")


def _write_stock(directory: Path, name: str, dates) -> None:
    pd.DataFrame({"date": list(dates)}).to_parquet(directory / f"{name}.parquet",
                                                      index=False)


def _synthetic_universe(directory: Path, per_prefix: int = 30) -> None:
    """建一个**跨 8 个代码前缀**的合成宇宙。"""
    directory.mkdir(parents=True, exist_ok=True)
    for pre in PREFIXES:
        for i in range(1, per_prefix + 1):
            _write_stock(directory, f"{pre}{i:03d}.SZ", ORDINARY_DATES)


#: 🚨 合成宇宙**只建一次**（模块级）—— 240 次 parquet 写盘约 3.6 s，
#: 逐个用例重建会把这条守卫拖成慢测试，而慢守卫会被跳过，
#: 被跳过的守卫等于没有（CLAUDE.md：穷举守卫必须把昂贵的那半与被测的那半分开）。
@pytest.fixture(scope="module")
def universe(tmp_path_factory):
    d = tmp_path_factory.mktemp("daily")
    _synthetic_universe(d, per_prefix=30)
    return d


# ── 1. 样本无偏（性质守卫）─────────────────────────────────────────

def test_the_calendar_sample_spans_the_code_prefixes():
    """🚨 **这条现在必然是绿的，而旧的 `sorted(...)[:40]` 必然红。**

    旧实现按**代码**排序取前 N ⇒ 样本只覆盖一段代码区间
    （实测生产仓里前 200 只**全是 `000xxx`**，因为 `000` 前缀有 458 只）。
    ⇒ 样本的新鲜度 = 「哪几只排在前面」，而**判词**正是由它决定的。

    ⚠️ 断言的是**性质**（样本跨了哪几个前缀），不是「某个文件存在」
    ——「存在」守卫会被「多放一个文件就绿」骗过去。
    """
    picked = [p.stem[:3] for p in
              tc._breadth_sample([Path(f"{pre}{i:03d}.SZ.parquet")
                                  for pre in PREFIXES for i in range(1, 41)],
                                 200)]
    got = set(picked)
    missing = set(REQUIRED_PREFIXES) - got
    assert not missing, (
        f"样本没有覆盖这些代码前缀：{sorted(missing)}。"
        f"取到的分布={ {p: picked.count(p) for p in sorted(set(picked))} }"
        "⇒ 样本只覆盖了一段代码区间 ⇒ 判词取决于「哪几只排在前面」。")


def test_the_calendar_sample_is_not_dominated_by_one_code_prefix():
    """**无偏**的第二个口径：样本不许被一个前缀吃掉。

    只钉「出现了哪些前缀」是不够的：前缀**个数**可以不变而**权重**变偏
    （比如 60% 都是 `600`）。⇒ 追加一条「单前缀占比」的性质约束。
    """
    picked = [p.stem[:3] for p in
              tc._breadth_sample([Path(f"{pre}{i:03d}.SZ.parquet")
                                  for pre in PREFIXES for i in range(1, 41)],
                                 200)]
    worst = max(picked.count(pre) for pre in set(picked)) / len(picked)
    assert worst <= 0.40, (
        f"单个代码前缀占样本的 {worst:.1%} ⇒ 样本被一段代码区间主导"
        "（均匀时 8 个前缀各 12.5%）。")


def test_the_sample_taken_from_a_real_directory_also_spans_the_prefixes(universe):
    """同一性质，但走**真实目录**那条路（`glob` → 哈希 → 取样）。

    只测纯函数会漏掉「目录里其实只有一个前缀」这种真实形态
    （CLAUDE.md 第 118 层：问错了对象，问的还是旧的东西）。
    """
    got = {p.stem[:3] for p in sample_paths(universe, 200)}
    missing = set(REQUIRED_PREFIXES) - got
    assert not missing, f"真实目录上的取样缺前缀：{sorted(missing)}"


# ── 2. 取样可复现 ─────────────────────────────────────────────────

def test_the_sample_is_identical_on_every_call(universe):
    """🚨 `random.sample` 做不到这件事：同一天重跑两次可能给出两个不同的判词。

    ⇒ 判词必须是**仓的事实**，不是**那一次运气的产物**。
    """
    a = [p.name for p in sample_paths(universe, 200)]
    b = [p.name for p in sample_paths(universe, 200)]
    assert a == b, "同一次运行里取样变了 ⇒ 判词会飘"
    assert len(a) == 200


def test_the_sample_does_not_depend_on_the_filesystem_order(tmp_path):
    """同一批文件，**按相反顺序创建** ⇒ 取到的样本必须逐只相同。

    ⚠️ 这条防的是「样本依赖 `glob` 的返回顺序」——那在 `sorted()` 之后
    看起来是稳定的，但只要有人在中间插一次「按 mtime 排 newest first」，
    它就会在**某天**悄悄换掉样本 ⇒ 判词跟着漂。

    ⚠️ 另一件事**不是**这条守卫的职责：仓**长大**时样本会换血
    （哈希取前 N，文件集一变入选的那 N 只就变）—— 那是**取 N 个**这件事
    的固有性质，不是缺陷。真正要守住的是「**同一份仓** ⇒ **同一个样本**」。
    """
    names = [f"{pre}{i:03d}.SZ" for pre in PREFIXES for i in range(1, 11)]
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for name in names:
        _write_stock(a, name, ORDINARY_DATES)
    for name in reversed(names):
        _write_stock(b, name, ORDINARY_DATES)
    assert ([p.name for p in sample_paths(a, 40)]
            == [p.name for p in sample_paths(b, 40)]), (
        "样本依赖了目录列举顺序 ⇒ 判词会随文件系统而漂")


# ── 3. 覆盖率的三个分支 ───────────────────────────────────────────

def test_zero_coverage_is_workday_not_in_store():
    """覆盖 **0** ⇒ 仓里**完全**没有今天的数据 ⇒ 采集失败。

    ⚠️ 这是**新判据与旧判词的分界**：旧判据问「在不在并集里」，
    答不出「一只都没有」与「只有 2%」的区别。
    """
    assert (classify_run_day(date(2026, 10, 20), set(), coverage=0.0)
            == RUN_DAY_WORKDAY_NOT_IN_STORE)


def test_partial_coverage_is_the_partial_state():
    """0 < 覆盖 < 阈值 ⇒ **第四态**：工作日、仓里**部分**有今天的数据
    ⇒ **拒绝发布**，但要把覆盖率写出来。"""
    verdict = classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=0.5)
    assert verdict == RUN_DAY_TRADING_DAY_PARTIAL
    assert verdict != RUN_DAY_TRADING, (
        "覆盖 50% 却被判成「正常发布」⇒ 残缺榜会进前瞻底稿")


def test_full_coverage_is_a_trading_day():
    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=1.0)
            == RUN_DAY_TRADING)


def test_the_threshold_itself_is_load_bearing():
    """🚨 阈值必须**真的参与判决** —— 否则它就是一句装饰。

    判据落在**阈值的两侧**各取一个点：低的那个必须判 PARTIAL，
    高的那个必须判 TRADING。⇒ 把阈值改成 0 或 1 都会红。
    """
    just_below = COVERAGE_MIN - 0.01
    just_above = COVERAGE_MIN + 0.01
    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"},
                             coverage=just_below) == RUN_DAY_TRADING_DAY_PARTIAL)
    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"},
                             coverage=just_above) == RUN_DAY_TRADING)


def test_a_store_with_one_percent_coverage_is_refused(tmp_path):
    """「只更新了 1 只票」⇒ 残缺榜 ⇒ **必须拒发**。

    🚨 这是本轮要堵的**真实失效形态**（2026-10-20 的判词方向）：
    旧判据只问「今天在不在并集里」，而并集里**只要有一只**更新了就算
    「在库里」⇒ 面板只覆盖 1% 也会放行 ⇒ 出一张残缺榜而屏幕上零个字。

    ⚠️ 这条同时给阈值**从下面**兜了底：阈值若被改成 0（或 0.01），
    它立刻红 ⇒ 阈值不许被「调到不拦任何东西」而不报。
    """
    d = tmp_path / "daily"
    d.mkdir()
    for i in range(39):
        _write_stock(d, f"6000{i:03d}.SH", ("2026-10-16",))
    _write_stock(d, "600999.SH", ("2026-10-16", "2026-10-20"))
    cov = coverage_detail(date(2026, 10, 20), daily_dir=d, sample=40, refresh=True)
    assert cov.coverage == 0.025
    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"},
                             coverage=cov.coverage)
            == RUN_DAY_TRADING_DAY_PARTIAL), (
        f"覆盖率 {cov.coverage:.1%} 却被判成「正常发布」⇒ 残缺榜会进前瞻底稿")


def test_the_threshold_sits_below_the_healthy_day_coverage():
    """🚨 阈值必须**低于健康日的覆盖率** —— 从**上面**兜底。

    健康日的覆盖率 = `1 − 永久停牌比例`（实测 2026-10-20：
    样本 202 只里 12 只永久停牌 ⇒ 190/202 = **0.9406**）。
    ⇒ 阈值若被改成一个**比健康值还高**的数（手打 0.95、或把标定倍数
    从 2× 改成 0.5×），健康日会**天天**判成 PARTIAL ⇒
    平台**慢性停发**（而症状是「不出单」，与「周末不出单」长得一样）。

    ⚠️ **过严的守卫和恒真的守卫一样有害**（CLAUDE.md 第 112 层）——
    所以这条要防的正是「把判据改严来让某个变异看起来被抓住」。
    """
    healthy = 1.0 - tc.CALIBRATED_STALE_FRACTION
    assert healthy > tc.COVERAGE_MIN, (
        f"阈值 {tc.COVERAGE_MIN} 不低于健康日覆盖率 {healthy} ⇒ "
        "健康日会被判成 PARTIAL ⇒ 平台慢性停发。")


def test_a_weekend_is_never_a_publish_day_even_with_full_coverage():
    """周末 ⇒ 不发布，**不管覆盖率多高**。

    🚨 2026-10-10/11 本仓真的在周末出过完整名单（三份重复名单进了
    `verification.parquet`）⇒ 这是本仓**真实犯过**的错。
    """
    for day in (date(2026, 10, 10), date(2026, 10, 11)):
        assert (classify_run_day(day, set(), coverage=1.0)
                == RUN_DAY_WEEKEND)


def test_the_four_states_are_mutually_exclusive_and_all_reachable():
    """防「四态塌成三态」（第 120 层：缺状态被当成「测过了、没问题」）。"""
    reached = {
        classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=1.0),
        classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=0.5),
        classify_run_day(date(2026, 10, 20), set(), coverage=0.0),
        classify_run_day(date(2026, 10, 10), set(), coverage=1.0),
    }
    assert reached == {RUN_DAY_TRADING, RUN_DAY_TRADING_DAY_PARTIAL,
                       RUN_DAY_WORKDAY_NOT_IN_STORE, RUN_DAY_WEEKEND}


# ── 4. 纯函数性 ───────────────────────────────────────────────────

class _BoomPd:
    """任何读盘调用都当场炸。"""

    @staticmethod
    def read_parquet(*a, **k):        # noqa: ANN001 — 探针，不该有签名
        raise AssertionError("读了盘")


def test_classify_run_day_never_touches_the_disk(monkeypatch):
    """🚨 `classify_run_day` 必须是**纯函数**。

    理由不是洁癖：判据埋在一条 20 分钟的流水线后面就等于没有守卫
    （CLAUDE.md 第 124 层）⇒ 判据必须能在 1 毫秒内被问、被变异。
    ⇒ 一旦它在函数内部读盘，它就变成了「跑一次要 3 秒、还要看仓里
    当时有什么」的判据 ⇒ 守卫只能对它做**端到端**测试，
    而端到端测试的失败原因分不清是判据错还是数据变。

    ⚠️ 这里把**模块里所有读盘入口**都换成炸弹，而不只是 `pd.read_parquet`
    —— 只换 `read_parquet` 的话，「换成 `pyarrow` 直读」就绕过去了。
    """
    def boom(*a, **k):                # noqa: ANN001 — 探针，不该有签名
        raise AssertionError("classify_run_day 读了盘")

    monkeypatch.setattr(tc, "pd", _BoomPd)
    for name in ("trading_days", "store_coverage", "coverage_detail",
                 "_snapshot", "_read_dates", "sample_paths"):
        monkeypatch.setattr(tc, name, boom)

    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=1.0)
            == RUN_DAY_TRADING)
    assert (classify_run_day(date(2026, 10, 20), {"2026-10-20"}, coverage=0.5)
            == RUN_DAY_TRADING_DAY_PARTIAL)


def test_classify_run_day_does_not_even_name_a_disk_reader():
    """纯函数性的**静态**版本：把上一条从「运行时炸」变成「源码里不许有」。

    运行时那条只能挡住**已知的**入口名；这条把判据的**依赖集**钉死：
    `classify_run_day` 的函数体里不许出现任何读盘相关的名字。
    ⚠️ 钉的是**名字**（不是行为）⇒ 一次良性的重命名会红 ⇒
    所以名单是「读盘相关」这一类，不是「某个具体函数」。
    """
    forbidden = {"pd", "Path", "open", "read_parquet", "glob", "read_text",
                 "trading_days", "store_coverage", "coverage_detail",
                 "_snapshot", "_read_dates", "sample_paths", "_breadth_sample",
                 "_history_carriers", "_file_min_date", "DAILY_DIR"}
    tree = ast.parse((ROOT / "aplus" / "trading_calendar.py").read_text(
        encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "classify_run_day")
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    bad = sorted(used & forbidden)
    assert not bad, (
        f"classify_run_day 里出现了 {bad} ⇒ 它不是纯函数"
        "（要么读盘、要么依赖当前时间/当前仓的状态）")


# ── 5. 向后兼容：既有三态一个字没改 ────────────────────────────────

def test_without_coverage_the_three_old_states_behave_exactly_as_before():
    """🚨 `coverage=None` 时**行为与旧版逐位相同**。

    别的代码与测试在读这三个常量（`RUN_DAY_TRADING` /
    `RUN_DAY_WEEKEND` / `RUN_DAY_WORKDAY_NOT_IN_STORE`）⇒ 它们的语义一个字不许改。
    """
    assert (classify_run_day(date(2026, 10, 16), {"2026-10-16"})
            == RUN_DAY_TRADING)
    assert (classify_run_day(date(2026, 10, 10), {"2026-10-16"})
            == RUN_DAY_WEEKEND)
    assert (classify_run_day(date(2026, 10, 19), {"2026-10-16"})
            == RUN_DAY_WORKDAY_NOT_IN_STORE)


def test_the_three_old_constant_values_are_unchanged():
    """常量**值**被别的代码按字面量读 ⇒ 钉死它们。"""
    assert RUN_DAY_TRADING == "TRADING_DAY"
    assert RUN_DAY_WEEKEND == "WEEKEND"
    assert RUN_DAY_WORKDAY_NOT_IN_STORE == "WORKDAY_NOT_IN_STORE"
    assert RUN_DAY_TRADING_DAY_PARTIAL == "TRADING_DAY_PARTIAL"


def test_the_two_paths_differ_on_exactly_the_two_documented_corners():
    """🚨 两条路径（`coverage=None` vs 给了覆盖率）只在**两个格子**上不同，
    而那两个格子必须**钉在明处** —— 否则它们会变成没人知道的口化石。

    **格子 ①「周末却出现在仓里」**：
    - 旧路径（先问「在不在库里」）⇒ `TRADING_DAY`；
    - 新路径（先问「是不是周末」）⇒ `WEEKEND`。
    🚨 仓里真实交易日历**实测零个周末日期**（2026-10-20 实测 7783 天里
    周末日期 0 个）⇒ 那个格子在真实数据上是空的；
    ⇒ 新路径判成 `WEEKEND` 是**更安全的一侧**
    （2026-10-11 本仓真的在周末出过完整名单）。

    **格子 ②「`known` 说不在库里、但覆盖率说满格」**：
    - 旧路径 ⇒ `WORKDAY_NOT_IN_STORE`；
    - 新路径 ⇒ `TRADING_DAY`。
    ⇒ 这是**故意的语义切换**：给了覆盖率之后，**覆盖率取代**那个二值问题
    （覆盖率是更强的信号：它区分「今天在库里」与「今天**基本**在库里」）。
    ⇒ 在生产里这两个口径**同源**（`trading_days` 与 `store_coverage`
    走同一个 `_snapshot` 缓存 ⇒ 永远一致）⇒ 格子 ②在真实调用里不可达。

    其余格子两条路径必须**完全一致** —— 少一条一致就说明
    「`coverage=None` 保持旧行为」这句话是假的。
    """
    # ── 必须一致的格子 ──
    for day, known, cov in [
        (date(2026, 10, 16), {"2026-10-16"}, 1.0),   # 工作日，在库里，覆盖率满
        (date(2026, 10, 19), {"2026-10-16"}, 0.0),   # 工作日，不在库里，覆盖率 0
        (date(2026, 10, 10), {"2026-10-16"}, 0.0),   # 周末，不在库里，覆盖率 0
    ]:
        assert (classify_run_day(day, known)
                == classify_run_day(day, known, coverage=cov)), (
            f"{day}/覆盖 {cov} 两条路径不一致 ⇒ 「coverage=None 保持旧行为」是假的")
    # ── 格子 ①：周末却出现在仓里 ──
    saturday = date(2026, 10, 10)
    in_store = {saturday.isoformat()}
    assert classify_run_day(saturday, in_store) == RUN_DAY_TRADING
    assert classify_run_day(saturday, in_store, coverage=1.0) == RUN_DAY_WEEKEND
    # ── 格子 ②：known 说不在、覆盖率说满格 ──
    workday = date(2026, 10, 19)
    assert classify_run_day(workday, {"2026-10-16"}) == RUN_DAY_WORKDAY_NOT_IN_STORE
    assert classify_run_day(workday, {"2026-10-16"}, coverage=1.0) == RUN_DAY_TRADING


def test_every_state_has_a_label_and_the_new_one_names_the_coverage():
    """🚨 第四态的文案必须**把覆盖率写出来**这件事写在脸上。

    这是「今天缺的那条信息」：没有它，这一档与「工作日不在库里」
    在页面上长得一模一样，而它们的成因完全不同。
    """
    assert set(RUN_DAY_LABELS) == {
        RUN_DAY_TRADING, RUN_DAY_WEEKEND, RUN_DAY_WORKDAY_NOT_IN_STORE,
        RUN_DAY_TRADING_DAY_PARTIAL}
    label = RUN_DAY_LABELS[RUN_DAY_TRADING_DAY_PARTIAL]
    assert "覆盖率" in label, label
    assert "拒绝发布" in label, label
    # 旧三态的文案不许被这一轮顺手改掉（别的界面在引用它们）
    assert "节假日" in RUN_DAY_LABELS[RUN_DAY_WORKDAY_NOT_IN_STORE]
    assert "采集失败" in RUN_DAY_LABELS[RUN_DAY_WORKDAY_NOT_IN_STORE]
    assert "周末" in RUN_DAY_LABELS[RUN_DAY_WEEKEND]
    assert "不发布" in RUN_DAY_LABELS[RUN_DAY_WEEKEND]
    assert "正常发布" in RUN_DAY_LABELS[RUN_DAY_TRADING]


# ── 6. 覆盖率这个量本身：分子分母各是什么 ─────────────────────────

def test_store_coverage_counts_the_stocks_that_reached_the_day(tmp_path):
    """**分子** = `max(date) >= day` 的股票数；**分母** = 读得出 `date` 列的股票数。"""
    d = tmp_path / "daily"
    d.mkdir()
    for i in range(3):
        _write_stock(d, f"60000{i}.SH", ("2026-10-16", "2026-10-20"))
    _write_stock(d, "600009.SH", ("2026-10-16",))          # 停在 10-16
    cov = coverage_detail(date(2026, 10, 20), daily_dir=d, sample=10, refresh=True)
    assert (cov.hit, cov.total) == (3, 4)
    assert cov.coverage == 0.75
    assert store_coverage(date(2026, 10, 20), daily_dir=d, sample=10,
                          refresh=True) == 0.75


def test_an_unreadable_file_is_not_counted_as_covered(tmp_path):
    """读坏的文件**不进分母** —— 它是「我们不知道」，不是「有数据」。

    🚨 方向很重要：分母变小会让覆盖率**变大** ⇒ 把「不知道」算成
    「有」是**不安全**的一侧。所以分母只数**读得出**的。
    """
    d = tmp_path / "daily"
    d.mkdir()
    _write_stock(d, "600000.SH", ("2026-10-20",))
    (d / "600001.SH.parquet").write_bytes(b"not a parquet file at all")
    cov = coverage_detail(date(2026, 10, 20), daily_dir=d, sample=10, refresh=True)
    assert (cov.hit, cov.total) == (1, 1)


def test_an_empty_store_reads_as_zero_coverage(tmp_path):
    """目录里一只票都读不出来 ⇒ 覆盖率 **0.0** ⇒ 判「完全没有」⇒ fail-closed。"""
    d = tmp_path / "daily"
    d.mkdir()
    cov = coverage_detail(date(2026, 10, 20), daily_dir=d, sample=10, refresh=True)
    assert cov.coverage == 0.0 and cov.total == 0


# ── 7. 日历的历史深度不许被取样悄悄削掉 ───────────────────────────

def test_the_calendar_keeps_the_history_the_sample_would_lose(tmp_path):
    """🚨 **静默退化**的那一半：仓里 5460 只里**只有 2 只**带 2019 年之前的历史
    （`000043.SZ` 起于 1994-09-28、`300114.SZ` 起于 2010-08-27）。

    ⇒ 旧的「按代码前 40 只」**恰好**含 `000043.SZ`（它在第 36 位）
      ⇒ 日历曾有 **7783 天**（1994→2026）；
    ⇒ **纯哈希样本一只都不含** ⇒ 日历只剩 **1889 天**（2019→2026）。

    ⇒ 那是**静默退化**：没有任何东西会红，而「日历覆盖到哪一年」是
    `guard_vintage.is_gap_day` 的**范围判据**（超出范围 ⇒ 假定为交易日
    ⇒ 报缺口）⇒ 日历的下界缩了，缺口判定的范围就跟着缩了。

    ⇒ 所以取样必须显式带上「深度部分」。
    """
    d = tmp_path / "daily"
    d.mkdir()
    for i in range(5):
        _write_stock(d, f"00000{i + 1}.SZ", ORDINARY_DATES)
    # 这一只带早期历史，**代码排在最后** ⇒ 按代码取前 N 一定漏掉它
    _write_stock(d, "600999.SH", ("1994-09-28",) + ORDINARY_DATES)
    cal = trading_days(daily_dir=d, sample=5, refresh=True)
    assert "1994-09-28" in cal, (
        f"日历的历史下界被取样削掉了：{sorted(cal)}。"
        "⇒ 「日历覆盖到哪一年」不再是仓的事实，而是取样的偶然。")


def test_the_calendar_sample_size_is_at_least_the_documented_one():
    """⚠️ 「40 只够」是**旧实现**的结论（它靠「按代码前 40 只恰好含
    `000043.SZ`」这个偶然成立）⇒ 样本量必须钉住，否则有人改回 40 时
    上面那条深度守卫还在、而**无偏**已经没了。"""
    assert CALENDAR_SAMPLE >= 200
