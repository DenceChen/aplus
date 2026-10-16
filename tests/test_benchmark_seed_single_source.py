"""基准抽样种子的**单一真相源**守卫（2026-10-16）。

## 它守的是什么

`aplus/backtest.py::BENCH_SEED` 是基准抽样种子的**唯一定义**。
在此之前同一个 `42` 有三份拷贝，而它们**查找方式互不相同**：

| # | 位置 | 形态 |
|---|---|---|
| ① | `aplus/backtest.py::_market_by_day` | 内联字面量 `Random(42)` |
| ② | `scripts/market_benchmark_audit.py::MARKET_SEED` | 模块常量（喂一份**重写的**抽样实现） |
| ③ | `scripts/market_nav_caliber.py` 产物 `sample.seed` | **自报字段** |

⇒ 改一边，其余两边**静默错开**。② 的后果最重：审计会拿**另一批票**
去和生产的票配对，而 `raw vs filtered` 从「配对」退化成
「两次独立抽样的比较」——噪声不减反增，**而报告会照常打印出来**。

## 🚨 这份文件的形状是被两件事逼出来的，不是设计出来的

### ① 「全仓不许出现 `Random(42)`」是**过严守卫**（第 112 层）

第一版守卫就是这么写的。实测全仓另有 **5 处与基准种子无关**的
`Random(42)`（`scripts/audit_tradeability/st_gap_correct.py`、
若干测试夹具）与 **2 处我自己写的注释 / help 文本**
（`tests/test_market_pct_is_not_a_constant.py`、`scripts/run_daily.py`）
⇒ 那条守卫会**永远红**，并把人推向「去改本来无关的夹具」这个错误修法。

⇒ 正解：**AST 只解析 `_market_by_day` 的函数体**，不扫全仓。
一个函数是判据的**宿主**，全仓不是。

### ② 子串锚会被**我自己写的注释**满足（第 13 层）

`assert f"Random({MARKET_SEED})" in inspect.getsource(_market_by_day)`
在 `_market_by_day` 增加 `seed` 参数后**合法地**不再成立
⇒ 守卫对着一次正确改动变红 ⇒ 后人把它改松成「只要有 `Random(` 就算过」，
而那恒真。⇒ 本文件全部用 **AST / 行为**，不用子串。

## 诚实的边界（登记为欠账，不假装覆盖）

- **`seed` 不等于「可复现」**：`rng.sample` 作用在 `list(all_codes)` 上，
  而顺序由**调用方怎么建 `daily_map`** 决定
  ⇒ 同一个 seed 配一个键顺序不同的 `daily_map`，抽到的**不是同一批票**。
  ⇒ 本文件能钉「同一 seed + 同一顺序 ⇒ 逐位相同」，
    **钉不了**「seed 42 在任何地方都给出同一批票」（那是谎话）。
- 「全仓每一个基准种子消费者都用了 `BENCH_SEED`」**由本文件的存在间接保证**
  （`benchmark_cohort` 是唯一样本选择实现），但**没有一条守卫逐个点名消费者**
  ⇒ 新增第五个调用点会静默通过。
  🚨 这正是第 137 层「人维护的清单永远差一个，扫出来的才自动包含新来者」的
  同一个病 ⇒ 债，见 `docs/MISTAKE_LOG_2026-10-14.md` 第 184 层。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import aplus.backtest
import pytest

from aplus.backtest import BENCH_SEED, MARKET_SAMPLE, _market_by_day, benchmark_cohort

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data" / "reports" / "market_nav_caliber.json"
CALIBER = ROOT / "scripts" / "market_nav_caliber.py"


def _codes(n: int = 400) -> list:
    """足够长的代码列表，使抽样真的在抽（`min(150, n)` 起效）。"""
    return [f"{i:06d}.SZ" for i in range(n)]


def _dm(n: int = 400) -> dict:
    """抽样只用到键的顺序与数量，值不参与。"""
    return {c: None for c in _codes(n)}


# ── 一、`BENCH_SEED` 是唯一定义：AST 锚在函数体，不扫全仓 ──────────

def test_the_sampler_builds_its_rng_from_the_constant_not_a_literal():
    """**RNG 构造的实参不许是整数字面量**，必须是常量名。

    🚨 **第一版把判据锚错了宿主，写成了「`_market_by_day` 函数体不许有
    任何整数字面量」—— 当场红，抓到的是 `1`。**
    那一个 `1` 来自 `ret = close / prev_close - 1`，是**收益公式的分子**，
    与种子毫无关系 ⇒ 这是一条**永远红的过严守卫**（第 112 层），
    而它的失效方向最贵：**把人推向「去改本来正确的收益公式」**。
    ⇒ 根因是宿主选错：RNG 构造长在 `benchmark_cohort` 里，
      不在 `_market_by_day` 里 ⇒ 判据跟着**它要守的那个动作**走。

    ⚠️ 另一层：**「不许有 int 字面量」本身就太宽**，连 `sample_size=7`
      这种正当实参都会被抓。⇒ 只钉**「RNG 的种子位」这一个语法位置**。

    ★ 断言的是 **AST 的实参位置**，不是源码子串 ⇒ 注释与 docstring 里
      出现 `42` 无害（第 13 层）。
    """
    tree = ast.parse(inspect.getsource(benchmark_cohort))
    seed_literals = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = (call.func.id if isinstance(call.func, ast.Name)
                else getattr(call.func, "attr", None))
        if name != "Random":
            continue
        for arg in call.args:
            seed_literals += [n.value for n in ast.walk(arg)
                              if isinstance(n, ast.Constant)
                              and isinstance(n.value, int)
                              and not isinstance(n.value, bool)]
    assert seed_literals == [], (
        f"benchmark_cohort 的 Random 实参里有整数字面量 {seed_literals}；"
        f"种子只能是常量 BENCH_SEED（现在 = {BENCH_SEED}）")


def test_the_probes_above_can_actually_see_an_integer_literal():
    """**探测器的正控**——否则上面那条可能是恒真的。

    判据形状的由来：全仓另有 5 处**与基准无关**的 `Random(42)` 与 2 处
    我自己写的注释 ⇒「全仓不许出现 `Random(42)`」是**永远红的过严守卫**。
    ⇒ 正解是 AST 锚在**具体函数**上。
    ★ 但「锚窄了」和「锚对了」是两件事 —— 后者必须由这条证明：
      造一段带 `Random(42)` 的源码喂给**同一个探测器**，它必须抓到。
    """
    import textwrap

    bad = textwrap.dedent("""
        def _fake(pool, seed):
            rng = random.Random(42)
            return rng.sample(pool, 3)
    """)
    found = []
    for call in ast.walk(ast.parse(bad)):
        if isinstance(call, ast.Call):
            name = (call.func.id if isinstance(call.func, ast.Name)
                    else getattr(call.func, "attr", None))
            if name == "Random":
                found += [n.value for n in ast.walk(call.args[0])
                          if isinstance(n, ast.Constant)
                          and isinstance(n.value, int)]
    assert found == [42], (
        f"探测器连 `Random(42)` 都看不见 ⇒ 它对真实现也看不见"
        f"（实际抓到 {found}）")


def test_market_by_day_delegates_to_the_single_sampler():
    """`_market_by_day` 必须调 `benchmark_cohort`，不得自己抽。

    这条与上一条**不可互换**：上一条说「没有字面量」，
    本条说「**抽样是那个共享实现**」——
    后者能抓住「抽了另一批票但没写字面量」（例如换了 `rng` 的构造方式）。
    """
    tree = ast.parse(inspect.getsource(_market_by_day))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "benchmark_cohort" in called, (
        "_market_by_day 没有调 benchmark_cohort ⇒ 抽样被重写了，"
        "而两台测量仪之间的差永远查不出来（第 22 层）")

    # 反向正控：探测器看得见别的调用名（否则上面那句近乎恒真）
    assert "sample" not in called and "benchmark_cohort" in called


def test_bench_seed_is_a_plain_int_and_the_one_production_uses():
    """`_market_by_day` 的默认路径必须用 `BENCH_SEED`——行为判据。

    锚的是**「不传 seed 时抽到哪一批票」**，不是「源码里写着哪个名字」：
    改名 `BENCH_SEED`→`SEED` 时这条**照样绿**（正确），
    而把默认值改成别的数时它**红**（真正该抓的）。
    """
    dm = _dm()
    assert benchmark_cohort(dm) == benchmark_cohort(dm, MARKET_SAMPLE, BENCH_SEED)


# ── 二、`seed` 注入真的生效，且默认路径逐位未变 ────────────────────

def test_default_seed_matches_explicit_seed_bitwise():
    """`seed=None` 与 `seed=BENCH_SEED` 必须**逐位相同**。

    🚨 这是「加参数不改既有结论」的**唯一**证据。
      `seed` 参数是 2026-10-16 加的；若它悄悄改变了默认路径，
      则既有产物、既有截图、既有守卫文本**全部与新代码不同源**，
      而没有任何东西会红（第 96 层：快照的守卫对当前代码零覆盖）。
    """
    dm = _dm()
    assert benchmark_cohort(dm) == benchmark_cohort(dm, seed=None)
    assert benchmark_cohort(dm) == benchmark_cohort(dm, seed=BENCH_SEED)


def test_different_seeds_produce_different_cohorts():
    """反向正控：换种子必须换票群。

    没有这条，上面那条可以在 `seed` **被完全忽略**的实现上永远绿
    ⇒ 它守的是「两个调用返回同一个东西」，不是「默认路径没被改」。
    ⇒ 第 120 层：产出判决的函数，第一条测试必须是能力/反控守卫。
    """
    dm = _dm()
    assert benchmark_cohort(dm, seed=BENCH_SEED) \
        != benchmark_cohort(dm, seed=BENCH_SEED + 1)


def test_sample_size_is_clamped_to_the_pool():
    """池比抽样规模小时抽全池——不许 `IndexError`、不许静默少抽。"""
    small = {f"{i:06d}.SZ": None for i in range(11)}
    assert len(benchmark_cohort(small)) == 11
    assert len(benchmark_cohort(small, sample_size=MARKET_SAMPLE)) == 11


# ── 三、产物自报字段不许与代码脱钩（假 provenance）─────────────────

def test_the_artifact_self_reports_the_same_seed_the_code_uses():
    """产物 `sample.seed` 必须 **== `BENCH_SEED`**，不许手打。

    🚨 这是**假 provenance** 的守卫（第 182 层同族）：
      「产物说自己用了哪个种子」与「代码真的用了哪个种子」之间，
      在此之前**没有任何守卫** ⇒ 改一边，那边的自报字段就成了谎话，
      而报告**照常被引用**。
    """
    if not ARTIFACT.exists():
        pytest.skip("产物未生成")
    art = json.loads(ARTIFACT.read_text())
    assert art["sample"]["seed"] == BENCH_SEED, (
        f"产物自报 seed={art['sample']['seed']}，"
        f"而代码用的是 BENCH_SEED={BENCH_SEED}")


def test_the_artifact_sample_size_matches_the_code():
    """同一条链的另一半：规模也不许与代码脱钩。"""
    if not ARTIFACT.exists():
        pytest.skip("产物未生成")
    art = json.loads(ARTIFACT.read_text())
    assert art["sample"]["size"] == MARKET_SAMPLE


def test_the_artifact_does_not_hand_type_the_seed_anywhere_else():
    """产物里**除 `sample.seed` 外**不许再出现第二个种子自报字段。

    ⚠️ 这条**刻意窄**：`cross_check_rel_diff` 等字段里出现数字 `42` 是正常的。
      宽到「全文不许出现 42」会变成第 112 层的过严守卫。
    ⇒ 只钉**键名**：自报种子的键必须唯一，且它必须叫 `seed`。
    """
    if not ARTIFACT.exists():
        pytest.skip("产物未生成")
    art = json.loads(ARTIFACT.read_text())

    def _seed_keys(node, path=""):
        # ⚠️ 顶层不许带前导 `.`：否则唯一的路径读成 `.sample.seed`，
        #   而断言在比 `"sample.seed"` ⇒ 一条**永远红**的守卫（第 112 层）。
        if isinstance(node, dict):
            for k, v in node.items():
                here = f"{path}.{k}" if path else str(k)
                if "seed" in str(k).lower():
                    yield here, v
                yield from _seed_keys(v, here)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from _seed_keys(v, f"{path}[{i}]")

    keys = list(_seed_keys(art))
    assert [k for k, _ in keys] == ["sample.seed"], (
        f"产物里的种子自报字段不止一个：{[k for k, _ in keys]}")


# ── 四、生成器不许手打自报（判据写在**源码**上 ⇒ 变异能打中）─────
#
# 🚨 为什么要这一节：上面那条 `test_the_artifact_does_not_hand_type_the_seed_anywhere_else`
#   读的是**落盘 JSON** ⇒ 改生成器它纹丝不动（第 96 层 / 「读快照的守卫等于没守」），
#   而 `tests/mutation_specs/test_benchmark_seed_single_source.mut` 实测**只含两条**
#   且**都打在 `aplus/backtest.py`** ⇒ 那条产物守卫**至今没有一条变异证明它在守东西**。
#   ⇒ 本节的守卫断言在**源码 AST** 上 ⇒ 变异打得到 ⇒ 才谈得上「它在守」。
#
# ⚠️ 本节记的缺陷是真的：`scripts/market_nav_caliber.py` 曾写出
#   `"seed_is_the_single_source": True`、`"seed_source": "aplus.backtest::BENCH_SEED"`
#   两行**手打自报**，以及 `"sampler": f"random.Random(BENCH_SEED).sample"` ——
#   🚨 **一个带 `f` 前缀却没有任何插值的假模板**，读起来像派生常量，实际是文本。

def _sample_dict_nodes(src: str) -> list:
    """找出源码里**同时**含 `"seed"` 与 `"sampler"` 键的 dict 字面量。"""
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if {"seed", "sampler"} <= keys:
            found.append(node)
    return found


def _the_sample_dict(src: str) -> ast.Dict:
    """恰好一个。**多于一个本身就是缺陷** ⇒ 不许悄悄取第一个。"""
    found = _sample_dict_nodes(src)
    assert len(found) == 1, (
        f"源码里有 {len(found)} 个同时含 seed/sampler 的 dict 字面量，"
        f"判据锚在哪个上就成了一声明 ⇒ {len(found)} 个必须报错")
    return found[0]


def _value_of(d: ast.Dict, key: str):
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    raise AssertionError(f"dict 里没有 {key!r}")


def _the_out_dict(src: str) -> ast.Dict:
    """找出 `out = {...}` 那个字面量（产物顶层）。

    🚨 它和 `_the_sample_dict` **不可互相替代**：
      `sample` 的右花括号 `},` 之后就是**顶层** ⇒ 一行插在 `sampler` 之后的
      新键会静悄悄地落进**顶层**，而 `sample` 的守卫**正确地一声不响**
      （这正是 S8 第一版存活的原因，见 `.mut` 里 S8 的锚点为什么改过）。
    ⇒ 两个宿主都要钉，漏一个就有一条无声通路。
    """
    found = [n.value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "out" for t in n.targets)
             and isinstance(n.value, ast.Dict)]
    assert len(found) == 1, f"源码里有 {len(found)} 个 `out = {{...}}`，判据锚不稳"
    return found[0]


def _dict_keys(d: ast.Dict) -> set:
    return {k.value for k in d.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _imported_from_backtest(src: str) -> set:
    """本模块从 `aplus.backtest` 导入进来的符号名集合。"""
    return {a.asname or a.name
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.ImportFrom) and n.module == "aplus.backtest"
            for a in n.names}


def test_the_producers_seed_field_is_a_reference_not_a_literal():
    """生成器不许把 `sample.seed` 写成**字面量**，必须是那个符号的引用。

    🚨 这是第 182 层「假 provenance」的**源头**：`HEAD` 里的生成器写的是
      `"seed": 42, "sampler": "random.Random(42).sample"` —— 产物自报的种子
      与代码用的常量**没有任何连接**，改一边另一边照常出报告。
    ⇒ 钉的性质是「**这是引用不是字面量**」，不是「它写着哪个名字」⇒ 改名照样绿。
    """
    node = _value_of(_the_sample_dict(CALIBER.read_text(encoding="utf-8")), "seed")
    assert isinstance(node, ast.Name), (
        f"生成器把 sample.seed 写成了 {type(node).__name__} "
        f"（{ast.dump(node)}）而不是对常量的引用 ⇒ 产物自报与代码真用会脱钩")
    # ⚠️ 名字不手打：从「它导入了什么」+「那个名字在 backtest 里是不是同一个对象」推。
    imported = _imported_from_backtest(CALIBER.read_text(encoding="utf-8"))
    assert node.id in imported, (
        f"sample.seed 引用了 {node.id!r}，而本模块从 aplus.backtest 导入的是 "
        f"{sorted(imported)} ⇒ 它引的不是那个单一真相源")
    assert getattr(aplus.backtest, node.id) is BENCH_SEED, (
        f"{node.id} 在 aplus.backtest 里已经不再指向 BENCH_SEED")


def test_the_producers_sampler_field_actually_interpolates():
    """`sample.sampler` 不许是**带 `f` 前缀却没有插值**的假模板。

    🚨 实测过的真实形态：`f"random.Random(BENCH_SEED).sample"` ——
      `BENCH_SEED` 后面没有花括号 ⇒ 产出的文本里那个符号名是**死的**，
      而读代码的人会以为它跟着常量走。
    ⇒ 这条比「手写 42」更隐蔽：它**看起来**已经是派生的了。
    """
    node = _value_of(_the_sample_dict(CALIBER.read_text(encoding="utf-8")), "sampler")
    assert isinstance(node, ast.JoinedStr), (
        f"sample.sampler 是 {type(node).__name__} 而不是 f-string")
    fvs = [n for n in ast.walk(node) if isinstance(n, ast.FormattedValue)]
    assert fvs, (
        "sampler 是个**没有插值的 f-string** ⇒ 它的值与常量无关，"
        "却长得像由常量派生")
    imported = _imported_from_backtest(CALIBER.read_text(encoding="utf-8"))
    for fv in fvs:
        assert isinstance(fv.value, ast.Name), (
            f"插值是 {type(fv.value).__name__}，不是符号引用：{ast.dump(fv.value)}")
        assert fv.value.id in imported, (
            f"插值引用了 {fv.value.id!r}，而本模块没从 aplus.backtest 导入它")
        assert getattr(aplus.backtest, fv.value.id) is BENCH_SEED, (
            f"{fv.value.id} 在 aplus.backtest 里已经不再指向 BENCH_SEED")


def test_the_probes_above_actually_flag_a_hand_typed_seed():
    """**正控**：上面两条的探测器必须看得见真实的缺陷形态，否则它们可能恒真。

    喂两段假源码：**手写 42** 与 **无插值的 f-string** ⇒ 必须分别被上面两条判掉。
    ⚠️ 没有这条，上面两条可以在一个「什么源码都判它合格」的实现上永远绿
    ⇒ 那是第 120 层：产出判决的守卫，第一条测试必须是反控。
    """
    src = ('out = {"sample": {"seed": 42, "sampler": "random.Random(42).sample"}}')
    node = _value_of(_the_sample_dict(src), "seed")
    assert not isinstance(node, ast.Name), "探测器看不见字面量 seed"
    node = _value_of(_the_sample_dict(src), "sampler")
    assert not isinstance(node, ast.JoinedStr), "探测器把普通字符串当成了 f-string"

    src = ('from aplus.backtest import BENCH_SEED\n'
           'out = {"sample": {"seed": BENCH_SEED,'
           ' "sampler": f"random.Random(BENCH_SEED).sample"}}')
    node = _value_of(_the_sample_dict(src), "sampler")
    assert isinstance(node, ast.JoinedStr), "探测器看不见 f-string"
    assert not [n for n in ast.walk(node) if isinstance(n, ast.FormattedValue)], (
        "探测器看不见「无插值的 f-string」⇒ 这正是实测踩过的那个坑")

    # 反向：真的插值必须被认出来（否则上面那条恒红）
    src = ('from aplus.backtest import BENCH_SEED\n'
           'out = {"sample": {"seed": BENCH_SEED,'
           ' "sampler": f"random.Random({BENCH_SEED}).sample"}}')
    node = _value_of(_the_sample_dict(src), "sampler")
    assert [n for n in ast.walk(node) if isinstance(n, ast.FormattedValue)], (
        "探测器把**真有插值**的 f-string 也判掉了 ⇒ 过严守卫（第 112 层）")


def test_the_producers_sample_keys_are_exactly_the_four_registered_ones():
    """**源码层**的孪生守卫：生成器里那个 dict 的键集合**恰好**是登记的那 4 个。

    🚨🚨 **这条是 S8 能被杀的原因**：
      产物层那条读落盘 JSON，而变异**不会重跑生成器** ⇒ 它对 S8 **零反应**（第 96 层）
      ⇒ 而 S8 改的**正是源码** ⇒ 只有把断言打在源码上，它才动得了。
    ⇒ 本节与产物层的分工：**源码层钉「生成器不许产出什么」，产物层钉「落盘的确实是它」**
      —— 后者防前者漏（少写一个键），前者防后者看不见（多写一个键）。
    """
    keys = _dict_keys(_the_sample_dict(CALIBER.read_text(encoding="utf-8")))
    assert keys == {"size", "pool", "seed", "sampler"}, (
        f"生成器里 sample 的键集合实得 {sorted(keys)}；"
        f"多出来的每一个都是**要漂移的表面**（第 182 层）——"
        f"而它在产物里与正确值**长得一模一样**，没有任何东西会红")


def test_the_producers_top_level_keys_are_exactly_the_thirteen_registered_ones():
    """**顶层**的键集合也要钉住 —— 这是 S9 存在的原因，也是 S8 第一版逃逸的位置。

    🚨🚨 **这一条是被一次「存活」逼出来的，不是设计出来的**：
      S8 第一版的锚点落在 `"sampler": …"},` 上。那个 `}` 已经**关掉了 `sample`**，
      而 `load_specs` 的 `dedent` 又把新增行的缩进拽到第 0 列
      ⇒ Python 不在乎缩进 ⇒ 变异**语法完全合法、语义完全无声**地
      把 `"seed_source"` 插进了**顶层** ⇒ 而 `sample` 的守卫**正确地没有红**。
    ⇒ 一个守卫的「存活」在这里不是它没用，是**它守的不是那一处**。
    """
    keys = _dict_keys(_the_out_dict(CALIBER.read_text(encoding="utf-8")))
    assert keys == {
        "prereg", "window_arg", "window_actual", "n_days", "sample",
        "entry_lag", "factors_adjusted", "equal_weight", "cap_weight",
        "gates", "cross_check_rel_diff", "full_pool_daily_mean_pct",
        "what_this_cannot_prove",
    }, (
        f"产物顶层键集合实得 {sorted(keys)}；"
        f"多出来的每一个都会**原样落进 JSON**，而没有任何东西会因为它漂移而红")


def test_the_artifact_sample_keys_are_exactly_the_four_registered_ones():
    """产物的 `sample` 段键集合**恰好**是登记的那 4 个。

    🚨 这条比「不许有第二个 seed 键」**更宽一格**：它连**不带 `seed` 子句**的
      装饰字段也拦（实测被撤掉的那两个正属此类）⇒ 装饰性自报没有合法藏身处。
    ⚠️ 它**不**校验值 —— 值由 `test_the_artifact_self_reports_the_same_seed_the_code_uses`
      逐条负责。分工：这条钉**形状**，那条钉**值**。
    🚨 而它**不可变异**（改生成器不会重跑它）⇒ 它的孪生兄弟在上一条。
    """
    if not ARTIFACT.exists():
        pytest.skip("产物未生成")
    art = json.loads(ARTIFACT.read_text())
    assert set(art["sample"]) == {"size", "pool", "seed", "sampler"}, (
        f"sample 段的键集合实得 {sorted(art['sample'])}；"
        f"多出来的多半是**手打自报**（第 182 层）——它们的漂移方向是说谎，不是过期")
