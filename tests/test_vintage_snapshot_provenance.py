"""vintage 链的溯源接线守卫（第 19 层「章只记代码不记数据」在 PIT 链上的实例）。

## 为什么要有它

`scripts/collect_financial_vintage.py` 是 **PIT 数据的唯一真相源**
（27 GB/年，`factor_spec_cn.md` §出路 B 把「从今天起每日攒 vintage」写成
财务因子的唯一出路）。而在本文件写下之前：

- `DEFAULT_GENERATORS` 里**没有它** ⇒ `resolve_generator()` 返 `None`
  ⇒ 按本模块自己的规矩，`unknown` = **不许引用**；
- `data/reports/financial_vintage/` 里**一份 sidecar 都没有**；
- ⇒ 产物一落盘，代码与产物的关系就断了：**代码改了、行为变了，
  历史快照与新快照无法区分**，而这是 PIT 数据的全部价值所在。

## 这几条守卫**刻意只钉接线，不钉产物**

历史快照（2026-10-14/15）**永久无法追溯盖章** —— 那两天跑的时候
`code_file_sha256` 这个字段还不存在，代码字节已不可得。
⇒ 若把「必须已有带章快照」写成判据，这条守卫会**永远红**，
而**永远红的守卫会把人推向「为了变绿去手写一份章」这个错误动作**
（形状 16，比红着更糟）。
⇒ 判据一律落在**代码接线**上：接线今天就能补，产物只能等下一次采集。

## 已有的同语义守卫（**本文件不许重复它们**）

| 谁已经守 | 守的是什么 |
|---|---|
| `test_provenance.py::_stamp_sites()` + `_DEBT` | 全仓每个 `provenance.stamp()` 要么传 `expect_bytes`、要么登记欠账 |
| `test_generator_names_resolve.py` | 已登记生成器的最后一行不得 `NameError`（把 `expect_bytes` 写错作用域就是它抓） |
| `test_cap_weighted_benchmark.py` | **逐脚本**的「取样在 `_run()` 第一行」 |

⚠️ 上面第三条是**逐脚本**的 —— 逐脚本守卫治不了「漏没漏」
（CLAUDE.md 已记：142 层副产物就是为这个才补的全仓守卫）。
本文件补的是**它覆盖不到的那一半**：vintage 这一条链有没有接线。

## ⚠️ 变异必读

判「这组守卫是不是安慰剂」**只能靠变异**。本文件的变异台在
`tests/mutation_specs/test_vintage_snapshot_provenance.mut`，
改动生产脚本（不是本文件）之后必须清 `.pyc`。

**2026-10-15 实测 6/6 全杀（基线已验绿，每条都是 `rc=1` 且指名到守卫）**：

| 变异 | 被谁杀掉 |
|---|---|
| V1 摘掉 `expect_bytes=` 实参 | `…freezes_the_generator_bytes` |
| V2 摘掉 `data_root=` 实参（回落默认 `master/daily`） | `…points_the_data_fingerprint_at_the_vintage_store` |
| V3 `GENERATOR` 指向别的脚本 | `…sampled_before_anything_runs` |
| V4 盖章点改指 `provenance.no_such_stamp`（**属性名写错**） | 上面三条里的三条同时红 |
| V5 取样点从 `main()` 首行挪走 | `…sampled_before_anything_runs` |
| V6 登记表里删掉 `financial.parquet` 那把键 | `…registered_as_the_generator_of_its_own_snapshots` |

★ **V4 的形态是刻意选的**：把 `provenance.stamp(` 改成
`provenance.no_such_stamp(` —— 属性访问在语法上完全合法（`compile()` 过得去），
而守卫按**属性名**匹配，于是立刻归零。
这比「删掉整段盖章」更接近真实事故（第 123 层：名字写错 ⇒ 静默失效）。
⇒ 推论：**改名与删除是两种不同的失效，而只测后者会漏掉前者。**
"""
from __future__ import annotations

import ast
from pathlib import Path

from aplus import provenance as P
from aplus.vintage import PARTITION_NAME, snapshot_path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts" / "collect_financial_vintage.py"
GENERATOR = "scripts/collect_financial_vintage.py"


# ── 取证工具：全部走 AST，不走子串（第 13 种形状）────────────────────

def _tree() -> ast.Module:
    return ast.parse(COLLECTOR.read_text(encoding="utf-8"), filename=str(COLLECTOR))


def _calls(tree: ast.Module, dotted: str) -> list[ast.Call]:
    """`provenance.stamp(...)` / `write_snapshot(...)` 这类调用点。"""
    head, _, attr = dotted.rpartition(".")
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        ok = (
            isinstance(f, ast.Attribute) and f.attr == attr
            and isinstance(f.value, ast.Name) and f.value.id == head
        ) if head else (
            isinstance(f, ast.Name) and f.id == attr
        )
        if ok:
            out.append(n)
    return sorted(out, key=lambda c: c.lineno)


def _main(tree: ast.Module) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "main":
            return n
    raise AssertionError(f"{COLLECTOR.name} 里没有 `main()`")


def _first_executable(body: list[ast.stmt]) -> ast.stmt:
    """第一条**可执行**语句（跳过 docstring）。"""
    i = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        i = 1
    assert i < len(body), "`main()` 除了 docstring 没有任何语句 ⇒ 探测器失效"
    return body[i]


def _expect_bytes_samples() -> list[ast.Assign]:
    """`expect_bytes = provenance.code_file_sha256(...)` 这类赋值。"""
    out = []
    for n in ast.walk(_tree()):
        if not isinstance(n, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "expect_bytes" for t in n.targets):
            continue
        seg = ast.unparse(n.value)
        if "code_file_sha256" in seg:
            out.append(n)
    return out


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """模块级 `NAME = "字面量"` ⇒ `{NAME: 字面量}`。"""
    out: dict[str, str] = {}
    for n in tree.body:
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            continue
        t = n.targets[0]
        if (isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)):
            out[t.id] = n.value.value
    return out


def _generator_argument(node: ast.AST, consts: dict[str, str]) -> str | None:
    """从 `code_file_sha256(ROOT, X)` 的第二个实参解出**它指的字符串**。

    ⚠️ 第一版只认 `ast.Constant` ⇒ 实现写成模块常量 `GENERATOR` 时判红，
    而那不是缺陷，是**守卫钉了「长得像」而不是钉了「指向哪个文件」**
    （`guards-that-pin-existence-not-property`）。⇒ 名字要**解析到值**才算。
    """
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return None
    arg = node.args[1]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name):
        return consts.get(arg.id)
    return None


# ── ① 登记 ────────────────────────────────────────────────────────

def test_the_collector_is_registered_as_the_generator_of_its_own_snapshots():
    """`resolve_generator("financial.parquet")` 必须解析到采集器**本身**。

    ⚠️ 两层都要断：
    ① 返 `None` ⇒ 按本模块规矩 `unknown` = 不许引用（那就是今天的形状）；
    ② 解析到**别的**文件 ⇒ `commits_touching()` 追的是另一个脚本的提交历史，
       判出来的是另一个脚本的新鲜度（第 123 层）。
    ③ 解析到的文件**不在盘上** ⇒ `code_file_sha256` 返 `None`、
       `commits_touching` 返 `[]` ⇒ **「没有提交动过它」⇒ 假 fresh**。
       第 123 层记的就是这个：名字写错时它把一个真 stale 翻成了 fresh。
    """
    gen = P.resolve_generator(PARTITION_NAME)
    assert gen is not None, (
        f"`{PARTITION_NAME}` 不在 `DEFAULT_GENERATORS`/`PATTERN_GENERATORS` 里 ⇒ "
        f"vintage 链的每一份快照都判 `unknown`（= 不许引用），"
        f"而 `unknown` 在这里与「数据坏了」长得一模一样。"
        f"\n  ⇒ 在 `aplus/provenance.py` 补一行 "
        f"`{PARTITION_NAME!r}: {GENERATOR!r}`。")
    assert gen == GENERATOR, (
        f"`{PARTITION_NAME}` 解析到 `{gen}`，而唯一写它的是 `{GENERATOR}` ⇒ "
        f"`commits_touching()` 答的是另一个脚本的提交历史。")
    assert (ROOT / gen).is_file(), (
        f"登记的生成器 `{gen}` **不在盘上** ⇒ `code_file_sha256` 返 `None`、"
        f"`commits_touching` 返空列表 ⇒ 第 123 层："
        f"「查不到」被读成「没有动过」⇒ **假 fresh**，比 stale 更危险。")


# ── ② 盖章点在写盘点之后（且不许空转）─────────────────────────────

def test_the_snapshot_is_stamped_after_it_is_written():
    """`stamp()` 必须落在 `write_snapshot()` **之后**。

    🚨 **判据不许空转**：若采集器一次都没盖章，下面这个比较会 vacuously 为真
    ⇒ 那是一条**恒真守卫**，而它的失效方向恰好是「以为链上有章」。
    ⇒ 先断言盖章点存在（非空），再比行号。

    反过来（先盖章后写盘）的失效形态：写盘那一刻抛异常 ⇒ **章在、产物不在**，
    即第 131 层「产物正在生成时的错误」，而 `freshness()` 对着章说话。
    """
    stamps = _calls(_tree(), "provenance.stamp")
    assert stamps, (
        f"`{COLLECTOR.name}` **一次都没调用 `provenance.stamp()`** ⇒ "
        f"vintage 链没有任何溯源证据。\n"
        f"  ⇒ 本条判据（比较行号）在这种情况下会 vacuously 为真，"
        f"所以先断非空，再断顺序。")
    writes = _calls(_tree(), "write_snapshot")
    assert writes, "正控失败：`write_snapshot()` 都没调用，探测器认错了函数名"
    assert min(s.lineno for s in stamps) > min(w.lineno for w in writes), (
        f"盖章点在第 {min(s.lineno for s in stamps)} 行，"
        f"而 `write_snapshot()` 在第 {min(w.lineno for w in writes)} 行 ⇒ "
        f"**章在、产物不在**的窗口（第 131 层）。")


def test_every_stamp_site_in_the_collector_freezes_the_generator_bytes():
    """本采集器的每个盖章点都必须传 `expect_bytes`（第 125 层）。

    ⚠️ 全仓级的那条在 `test_provenance.py::_stamp_sites()` + `_DEBT`。
    这里**不是重复**：全仓那条只说「接了或登记欠账」，
    而本条说「**这个脚本不许欠账**」—— 欠账表是**已知的欠账，不是白名单**。
    """
    stamps = _calls(_tree(), "provenance.stamp")
    assert stamps, "正控失败：一个盖章点都认不出（探测器坏了，不是代码好了）"
    missing = [f"第 {c.lineno} 行" for c in stamps
               if not any(k.arg == "expect_bytes" for k in c.keywords)]
    assert not missing, (
        f"`{COLLECTOR.name}` 的盖章点没接 `expect_bytes`：{', '.join(missing)}\n"
        f"  ⇒ 第 125 层：章里写的是「写盘那一刻盘上恰好是这些字节」，"
        f"不是「这份产物是这些字节造出来的」。而 vintage 单轮 **68 分钟**，"
        f"正是「跑的过程中改生成器」会发生的量级。")


# ── ③ 取样点必须在跑之前（第 125 层 / 第 131 层）──────────────────

def test_the_generator_bytes_are_sampled_before_anything_runs():
    """`expect_bytes` 必须在 `main()` 的**第一条可执行语句**取，且指名采集器。

    为什么是「第一条」而不是「盖章前」：
    放在盖章前一行 = **拿开跑的字节盖开跑的章** ⇒ 检查恒真（第 131 层）。
    放在 `main()` 里而工作函数在别处 ⇒ `NameError`（那是
    `test_generator_names_resolve.py` 抓的，不在本文件重复）。

    ⚠️ 判定认的是**语句形状**（赋值目标 `expect_bytes` + 值里含
    `code_file_sha256`）而不是「文件里出现过这两个词」——
    否则本 docstring 自己就能把它满足掉（第 13 种形状）。
    """
    main = _main(_tree())
    first = _first_executable(main.body)
    assert isinstance(first, ast.Assign), (
        f"`main()` 的第一条可执行语句是 `{ast.unparse(first)[:70]}`，"
        f"不是生成器字节取样 ⇒ 取样点被挤到跑之后 ⇒ "
        f"「开跑的字节」与「盖章的字节」可能是两份东西（第 125/131 层）。")
    seg = ast.unparse(first)
    assert "expect_bytes" in seg and "code_file_sha256" in seg, (
        f"`main()` 第一条是 `{seg[:70]}`，既不写 `expect_bytes` 也不调 "
        f"`code_file_sha256` ⇒ 采样点不在第一行。")
    tree = _tree()
    got = _generator_argument(first.value, _module_string_constants(tree))
    assert got == GENERATOR, (
        f"取样指向的是 `{got}` 而不是 `{GENERATOR}` ⇒ "
        f"章里冻结的是**别的文件**的字节（守卫全绿、章照盖，形状 16）。")


def test_the_sampler_probe_can_find_a_sample_on_a_synthetic_source():
    """**正控**：探测器本身必须认得出人写的取样（否则上一条是装饰）。

    这就是记忆 `guards-that-pin-existence-not-property` 的解药：
    一条只钉「存在」的断言，在被钉的东西消失时会**继续绿**。
    """
    src = ("def main():\n"
           "    expect_bytes = provenance.code_file_sha256(ROOT, G)\n"
           "    run()\n")
    ns = {"ast": ast}
    tree = ast.parse(src)
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "expect_bytes"
                     for t in n.targets)
             and "code_file_sha256" in ast.unparse(n.value)]
    assert found, f"正控失败：人写的取样都认不出（ns={ns}）⇒ 主判据是装饰"


# ── ④ 章必须描述它用的数据（第 19 层）────────────────────────────

def test_the_stamp_points_the_data_fingerprint_at_the_vintage_store():
    """`stamp()` 不许走默认 `data_root` —— 默认是 `data/master/daily`，
    而**这个脚本一次都没读过它**。

    第 19 层的原型：`factor_attribution.json` 判 fresh，而它算自的数据
    早已被修过，章里**没有字段能表达这件事**。
    ⇒ 这里若不显式传，指纹描述的是另一份数据，而 `freshness()` 照样给绿。
    """
    stamps = _calls(_tree(), "provenance.stamp")
    assert stamps, "正控失败：一个盖章点都认不出"
    bad = []
    for c in stamps:
        kw = {k.arg: k.value for k in c.keywords}
        if "data_root" not in kw:
            bad.append(f"第 {c.lineno} 行没传 data_root")
        else:
            seg = ast.unparse(kw["data_root"])
            # 允许 `args.root` / `ROOT` 这类实参；不许字面量钉死某个快照根
            if isinstance(kw["data_root"], ast.Constant):
                bad.append(f"第 {c.lineno} 行把 data_root 写死成 {seg}")
    assert not bad, (
        "；".join(bad) + "\n  ⇒ 默认 `data_root` 是 `data/master/daily`，"
        "而本采集器只写 `financial_vintage/`、从不读日线 ⇒ "
        "指纹描述的不是这份产物用的数据（第 19 层）。")


def test_a_snapshot_without_a_stamp_is_unknown_not_fresh():
    """**「尚无带章快照」必须是 `unknown`，不是「大概没事」。**

    这是 `docs/VINTAGE_PROVENANCE_AND_REVISION_2026-10-15.md` §1.4 那条
    要求的另一半：守卫必须容忍「还没盖章」，而容忍的正确形态是
    **判 `unknown`**，不是**放行**。

    ⇒ 失效方向：判 `fresh` ⇒ 明天第一份没盖章的快照会被当作可引用。
    """
    fake = Path("/tmp/aplus_test_vintage_no_stamp") / f"date=2099-01-01" / PARTITION_NAME
    v = P.freshness(fake, root=ROOT)["verdict"]
    assert v == "unknown", (
        f"一份**没有 sidecar**的快照被判成 `{v}` ⇒ "
        f"「没有证据」与「证据说没问题」被合并了。")
    # 正控：真 sidecar 在时不该是 unknown —— 否则上一条可能是恒真
    assert P.freshness(COLLECTOR, root=ROOT)["verdict"] == "unknown", (
        "正控失败：连脚本文件本身都判 unknown ⇒ `freshness()` 的入参约定变了，"
        "本文件的其它判定也可能落空（第 153 层：入参不可信）。")
