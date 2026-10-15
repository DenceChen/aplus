"""守卫：**命中率诊断报告不许比它描述的判决更老**（2026-10-16）

## 这份报告是什么

`data/reports/hit_rate_diagnosis.json` 是**对外报命中率与超额的那份产物**。
用户看到的每一个「命中率 X%」都来自它。

## 它坏在哪（第 156 层的教科书案例，逐条实测）

| | 值 |
|---|---|
| 报告 mtime | `2026-10-15 07:57:17` |
| 数据源 `verification.parquet` mtime | `2026-10-15 19:12:51` |
| ⇒ 报告比它描述的数据源早 | **11 小时 15 分** |
| 报告里的判决数 | 69 条 |
| 数据源现在有 | **100 条**（80 行/4 日 → 100 行/5 日，**追加式**） |
| `freshness()` 的判决 | **`fresh` + `byte_evidence: true`** |

⇒ **最坏的一格：过期报告与新鲜报告在审计输出里长得一模一样。**

## 病根不是「忘了重跑」，是**章是手写的**

`scripts/diagnose_hit_rate.py` **全文没有 `provenance.stamp()` 调用**
（185 行，`main()` 只 `write_text` 然后 `print`）。
全仓也**没有任何代码**给这份报告盖章（`grep hit_rate_diagnosis` 只命中
`aplus/provenance.py:228` 的登记项）。

而 sidecar 存在、`code_file_sha256` 与当前生成器字节**逐位相等**
⇒ 「生成器没变」这一条是真的，**变的只有它的输入**。

`CLAUDE.md` 写着「2026-10-15 补登记 provenance + 盖章」——
那枚章是**手工补的**，补在旧产物上。

⇒ 这正是 `MISTAKE_LOG` 第 16 层预先写下的处置：
「修法里最危险的一步是『让它变绿』—— **手写一个 sidecar 是形状 16**
（从不可测的输入捏造一个与真章无法区分的文件），**比红着更糟**」
且「**这类红只能靠真跑修**」。

⇒ 所以本文件的守卫**不许被「补一枚章」满足**：
G3 钉的是**章里那个 sha 与底稿当前字节相等**，
手写章写不出这个值（除非连底稿一起重跑）。

## 为什么章里那个 `data_fp` 完全不相关

sidecar 里 `"data_fp": "5460:1792002326.67"` —— **5460 个文件**是
`data/master/daily`。而这个脚本读的是**单个文件**
`data/reports/verification.parquet`。

⇒ `stamp()` 默认把 `data_fp` 指向主库日线，于是**章盖着一个它没读过的数据根**，
而真正要抓的那次输入漂移**结构上落在视野之外**。

⇒ 修法不是改 `stamp()` 的默认（第 19 层的教训：改默认会让 20+ 份报告一次性翻红，
那是**引入新问题**），是**让这份报告自己声明它读的那个文件的内容哈希**。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus import provenance as provenance_mod  # noqa: E402

GEN_REL = "scripts/diagnose_hit_rate.py"
GEN = _ROOT / GEN_REL
ART = _ROOT / "data/reports/hit_rate_diagnosis.json"
SIDECAR = ART.with_name(ART.name + provenance_mod.SIDECAR_SUFFIX)
VERIFY = _ROOT / "data/reports/verification.parquet"


# ── 探针（正控与主守卫共用同一份实现，第 102/122 层）──────────────────

def _stamp_calls(tree: ast.AST) -> list[ast.Call]:
    """找出全部 `stamp(...)` 调用：`provenance.stamp` 与裸 `stamp` 两种形态。

    ⚠️ **不许用子串**：第 13 层——同一个文件里出现 `stamp` 这个词
    （注释、docstring、契约说明）会让子串断言恒绿，
    而这里要判的是「**这个调用点**有没有带某个关键字实参」。
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name == "stamp":
            out.append(node)
    return out


def _kwargs_of(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _dict_keys(node: ast.expr) -> set[str]:
    """dict 字面量的字符串键集合；不是字面量就返回空集（不猜）。"""
    if not isinstance(node, ast.Dict):
        return set()
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _module_string_const(tree: ast.AST) -> dict[str, str]:
    """模块顶层的 `NAME = "字面量"` 绑定。

    🚨 **为什么要这一层**（第 131 层的复发，2026-10-16 当场踩到）：
    第一版判据要求 `stamp()` 的第 2 个位置实参**本身**是 `ast.Constant`，
    于是把 `stamp(out, GENERATOR, ...)`（`GENERATOR` 是模块级常量，
    且**恰好解析回本脚本路径**）判成「没指名道姓」。
    ⇒ 我量的东西是「AST 实参是不是字面量」，
      而要守的性质是「章上的 `generator` 字段 == 本脚本路径」。
      中间隔着**一次常量解析**，而那一跳就是判据与性质之间的全部距离。

    ⇒ 修法不是把常量内联回去（那是**为了让判据变绿而改本来正确的代码**，
      且模块级常量是更好的写法，`score_ranking_power.py` 也是这么写的），
      是让判据**求值一次**：先收集模块级字符串常量，再解析实参。
    ⚠️ 解析不到的形态（从别处 import 的常量、函数内局部变量）
      返回「未解析」而**不是**「空」——两者对断言的含义不同。
    """
    out: dict[str, str] = {}
    for node in tree.body:                       # 只看模块顶层，不递归
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = node.value.value
    return out


def _resolve(node: ast.expr, consts: dict[str, str]) -> str | None:
    """把一个实参求值成字符串；求不出来返回 None（**不猜**）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


# ── G1 形态：生成器必须自己盖章，且指名自己 ──────────────────────────

def test_the_generator_stamps_the_report_and_names_itself():
    """`stamp()` 调用存在，且它的 generator 实参**求值**回本脚本路径。

    ⚠️ 判据刻意不要求「实参是字面量」——那会逼着实现把
    模块级常量内联回去，而常量是更好的写法（见 `_module_string_const`）。
    要守的是**解析结果**，不是**写法形状**。
    """
    tree = ast.parse(GEN.read_text(encoding="utf-8"))
    calls = _stamp_calls(tree)
    assert calls, (
        f"{GEN_REL} 全文没有 provenance.stamp() 调用 —— "
        "那份报告只能靠**手工**盖章，而手工章与真章无法区分（第 16 层）。"
        "修法是**真跑一次**，不是补一枚章。")

    consts = _module_string_const(tree)
    got = [(_resolve(c.args[1], consts) if len(c.args) > 1 else None)
           for c in calls]
    assert GEN_REL in got, (
        f"{GEN_REL} 里 stamp() 的 generator 实参求值不回来：{got!r}。\n"
        f"  模块级字符串常量有 {sorted(consts)!r}。"
        "章上的 generator 字段必须指名道姓，否则读者无从判断这份产物出自哪份代码。")


# ── G2 形态：章里必须声明**它真正读的那个文件**的内容哈希 ────────────

def test_the_stamp_declares_the_input_file_by_content_hash():
    """`extra=` 里必须有 `source_parquet_sha256`，且它的值**不是字面量**。

    ⚠️ **不许是字面量**：写死一个 sha256 字符串的话，
    它与底稿何时变过完全无关，守卫会永远绿——
    而那正是本文件要防的失效（第 156 层：章只盖代码不盖输入）。
    """
    calls = _stamp_calls(ast.parse(GEN.read_text(encoding="utf-8")))
    extras = [k["extra"] for c in calls if "extra" in (k := _kwargs_of(c))]
    assert extras, (
        f"{GEN_REL} 的 stamp() 调用没有 extra= 实参 ⇒ "
        "它无法声明自己读了哪个输入。")

    keys = _dict_keys(extras[0])
    assert "source_parquet_sha256" in keys, (
        f"extra= 的键是 {sorted(keys)!r}，缺 source_parquet_sha256 ⇒ "
        "章只盖了代码、没盖输入。底稿是**追加式**的"
        "（实测 80 行/4 日 → 100 行/5 日），"
        "而章里的 data_fp 指的是 master/daily（5460 个文件）——"
        "**它盖的是一个这个脚本没读过的数据根**。")

    val = next(v for k, v in zip(extras[0].keys, extras[0].values)
               if isinstance(k, ast.Constant) and k.value == "source_parquet_sha256")
    assert not isinstance(val, ast.Constant), (
        "source_parquet_sha256 被写成了字面量 —— "
        "那是一个与底稿实际字节无关的常量，"
        "底稿再变多少次它都不会红（形状 6：假装能调）。")


# ── G4 接线层：那个值必须真的**去读了字节**（产物层守卫对当前代码零覆盖）──

def test_the_stamped_hash_is_computed_from_the_files_bytes_not_its_path():
    """`source_parquet_sha256` 的值必须是对 `_sha256_of(...)` 的调用，
    而 `_sha256_of` 的函数体必须真的 `.read_bytes()`。

    🚨 **为什么这条不能省**（写它是被变异验证逼出来的，不是预防）：
    G3 是**产物层**守卫——它读的是**上一次跑落盘的 sidecar**。
    把代码改成 `"source_parquet_sha256": str(a.verification)`
    （盖章章的字段里放**路径字符串**而不是指纹），
    G1 绿（实参仍可解析）、G2 绿（`str(...)` 是 Call 不是字面量）、
    **G3 也绿——因为旧 sidecar 还在盘上，值仍然对得上**。
    ⇒ 三条守卫全部存活，而产物已经被污染成「章上写着路径却叫它 sha256」。

    ⇒ 这是第 96/118 层（「读落盘 json 的守卫与代码毫无关系」）在
    **本文件内部**的复发：产物层与代码之间没有任何通路。
    """
    tree = ast.parse(GEN.read_text(encoding="utf-8"))
    calls = _stamp_calls(tree)
    consts = _module_string_const(tree)
    extras = [k["extra"] for c in calls if "extra" in (k := _kwargs_of(c))]
    assert extras, "没有 extra= 实参"
    dict_node = extras[0]
    val = next(v for k, v in zip(dict_node.keys, dict_node.values)
               if isinstance(k, ast.Constant)
               and k.value == "source_parquet_sha256")
    assert isinstance(val, ast.Call), (
        f"source_parquet_sha256 的值是 {ast.unparse(val)!r}，不是一次调用 —— "
        "把**路径字符串**或**mtime**放进叫 sha256 的字段，"
        "读的人无从知道那不是指纹。")
    fname = val.func.id if isinstance(val.func, ast.Name) else getattr(
        val.func, "attr", None)
    assert fname == "_sha256_of", (
        f"source_parquet_sha256 调的是 {fname!r} 而不是 _sha256_of ⇒ "
        "指纹不是由「读文件字节」那个原语算出来的。")

    # `_sha256_of` 本体：必须读字节，且不许退回 mtime（代理不是证据，第 112 层）
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_sha256_of"), None)
    assert fn is not None, "找不到 _sha256_of 的定义"
    body = ast.unparse(fn)
    assert ".read_bytes()" in body, (
        f"_sha256_of 的实现是 {body!r}，它没有读文件字节。"
        "读 mtime 会被「改了又改回」骗过（本仓的变异台天天这么干）。")
    assert "st_mtime" not in body, (
        "_sha256_of 用了 mtime ⇒ mtime 是代理不是证据（第 112 层）")


# ── G6 接线顺序：expect_bytes 必须**早于**耗时步骤抓（第 125 层的形态）──

def test_the_generator_bytes_are_captured_before_the_expensive_work():
    """`expect_bytes` 的赋值行号必须**早于** `diagnose(...)` 的调用行号。

    🚨 **为什么这条现在近乎平凡、却仍然要钉**（变异 M7 被它杀掉）：
    第 125 层的失效是「抓在盖章前 ⇒ 开跑的字节变成盖章的字节 ⇒ **检查恒真**」。
    本脚本跑 0.1 秒，所以**今天**这个窗口约为零 —— 而
    「窗口为零」不是「形态不存在」：哪天有人在这行前面插一次 GB 级面板加载，
    顺序就变成承重的，而那时没有任何东西会喊。

    ⇒ 这是一条**接线顺序**守卫，1 毫秒可问、可变异，不碰数据。
    ⚠️ 它不判「这个脚本该不该接 `expect_bytes`」（那是 `test_provenance.py`
      的 `_DEBT` 契约管的）—— 本条只判**接了的话，抓取点对不对**。
    """
    tree = ast.parse(GEN.read_text(encoding="utf-8"))
    captures, expensive = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "expect_bytes":
                    captures.append((node.lineno, node.value))
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "diagnose":
                expensive.append(node.lineno)
    assert captures, "找不到 expect_bytes 的抓取点"
    assert expensive, "找不到耗时步骤 diagnose(...) 的调用点"

    # ⚠️ **必须钉值，不能只钉位置**：`expect_bytes = None` 让 `stamp()`
    #   **整项跳过**字节检查（docstring 逐字：「不传 ⇒ 不做这项检查」），
    #   而它**留在原来的行号上** ⇒ 一条只钉位置的守卫对它完全失明。
    #   ⚠️ 这与仓里 `test_provenance.py` 的全仓守卫同族：那条查的是
    #   「有没有 `expect_bytes=` 这个关键字」，而 `expect_bytes=expect_bytes`
    #   （值为 None）照样满足它 —— 守卫被一个空操作满足（第 122 层）。
    first = captures[0][1]
    fname = None
    if isinstance(first, ast.Call):
        fn = first.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
    assert fname == "code_file_sha256", (
        f"expect_bytes 被赋成 {ast.unparse(first)!r}，"
        "不是一次 `code_file_sha256(...)` 调用 ⇒ "
        "`stamp()` 会**整项跳过**字节检查（它的 docstring："
        "「不传 `expect_bytes` ⇒ **不做这项检查**」），"
        "而关键字 `expect_bytes=` 仍然在调用点上 ⇒ 只查关键字的守卫看不出区别。")

    lines = [ln for ln, _ in captures]
    assert min(lines) < min(expensive), (
        f"expect_bytes 抓在第 {min(lines)} 行，"
        f"耗时步骤在第 {min(expensive)} 行 ⇒ **抓取点在它之后** ⇒ "
        "章上比对的是「盖章那一刻的字节」而不是「这一跑执行的字节」，"
        "检查恒真（第 125 层）。"
        "correct 的位置是 `main()` 开头、任何耗时步骤之前。")


# ── G5 形态：--out 必须默认落到生产产物，否则「跑过了」可以完全为真 ──

def test_the_out_argument_defaults_to_the_registered_artifact():
    """`--out` 的默认值不许是 `None`。

    🚨 第 137 层实测过的形态：`if args.out:` 是**唯一**的写盘闸门，
    而默认值是 `None` ⇒ 裸跑 `python scripts/diagnose_hit_rate.py`
    打印一份漂亮的判决、**一个字节不落盘**。
    ⇒ 「我重跑过了」可以完全为真而产物逐位未变，
    而 `git diff 产物` 为空**无法区分**「没写」与「写了但内容相同」。
    """
    tree = ast.parse(GEN.read_text(encoding="utf-8"))
    out_arg = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "add_argument"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "--out":
            out_arg = node
    assert out_arg is not None, "找不到 --out 参数"
    d = _kwargs_of(out_arg).get("default")
    assert d is not None and not (isinstance(d, ast.Constant)
                                  and d.value is None), (
        "--out 默认是 None ⇒ 裸跑不落盘，"
        "而落盘闸门 `if args.out:` 会让这一跑**看起来成功**。"
        "默认必须指向已登记的生产产物路径。")


# ── G3 产物层：对账。这是唯一能抓住「输入漂移」的守卫 ────────────────

def test_the_report_carries_a_true_stamp_matching_the_current_verdicts():
    """章里的输入指纹必须等于 `verification.parquet` 的**当前字节**。

    ★ 本条在写出来的当天就是**红的**，而它必须保持红到
      `diagnose_hit_rate.py` 被**真跑一遍**为止。
      「让守卫变绿」的正确动作是重跑脚本，不是补章（第 16 层）。
    """
    assert ART.is_file(), f"{ART.name} 不存在"
    assert SIDECAR.is_file(), (
        f"{SIDECAR.name} 不存在 ⇒ 这份报告没有真章 ⇒ 按本仓规矩不可引用。"
        "**手写一枚章不算**（第 16 层）。")

    sc = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert sc.get("generator") == GEN_REL, (
        f"章上的 generator 是 {sc.get('generator')!r}，"
        f"而本文件守的是 {GEN_REL!r} ⇒ 这枚章不是这份报告的。")

    # ⚠️ 这里**刻意不断言** `code_dirty is False`（第一版写了，当场被自己否掉）：
    #   它的口径是**全仓** `git status --porcelain`，而重跑这个生成器的那一刻
    #   我正在改它 ⇒ 工作区必然脏 ⇒ `code_dirty=True` 是**结构上必然**的。
    #   一条永远红的守卫会训练人忽略红灯，并把人推向
    #   「去窄化 `is_dirty()` 口径」这个**错误修法**（第 112 层）。
    #   真判据是下面这条输入指纹——它与工作区状态无关，且正是本次失效的所在。

    want = hashlib.sha256(VERIFY.read_bytes()).hexdigest()
    got = sc.get("source_parquet_sha256")
    # 🚨 第 155 层：**hex 比较必须由程序做，打印出来的只能是布尔**。
    #   人眼比 64 位 hex 出的错，方向是「以为字节变了」⇒ 逼人重跑本来正确的东西
    #   （过严的读法与过严的判据等价）。失败消息只给「哪个不匹配 + 各自 sha256() 值」，
    #   **不给两行 hex 让读者去逐位对**。
    assert got is not None, (
        "章里没有 source_parquet_sha256 字段 ⇒ 这枚章没盖住输入。"
        "它声明的 data_fp 指的是 master/daily（5460 个文件），"
        "而本脚本读的是单个文件 verification.parquet。")
    if got != want:
        assert False, (
            "章里的输入指纹与底稿**当前字节**不相等"
            f"（章上的 sha256() = {hashlib.sha256(str(got).encode()).hexdigest()[:16]}…，"
            f"底稿的 = {want[:16]}…，长度 {len(str(got))} vs {len(want)}）\n"
            "⇒ 这份命中率判决**不是**对现在这份 verification.parquet 做的。\n"
            "  修法：`.venv/bin/python scripts/diagnose_hit_rate.py`（真跑）。\n"
            "  ⚠️ 上面的前缀由 sha256() 现算，**不要**拿它当指纹本身去比对。")


# ── 正控：探针自己有没有牙（第 112 层：过严与恒真同样有害）────────────

def test_the_stamp_probe_finds_a_call_and_reports_none_when_there_is_none():
    """探针的正控与反控。

    没有这一条，正文那三条在探针变宽时会**一路绿**，
    而「探针失效」和「一切都好」在门禁输出里长得一模一样（第 126 层）。
    """
    src_with = (
        "import provenance\n"
        "provenance.stamp(out, 'scripts/x.py', extra={'a': 1})\n"
    )
    src_without = "import provenance\nout.write_text(txt)\n"
    # 裸名形态也必须认（第 143 层踩过：只认属性调用会把真接线判成没接）
    src_bare = "from aplus.provenance import stamp\nstamp(out, 'scripts/x.py')\n"
    # 只有散文提到 stamp —— **不许**被算成调用点（第 13 层）
    src_prose = "# 这里本来应该 stamp(...)\nout.write_text(txt)\n"

    assert len(_stamp_calls(ast.parse(src_with))) == 1
    assert len(_stamp_calls(ast.parse(src_bare))) == 1
    assert _stamp_calls(ast.parse(src_without)) == []
    assert _stamp_calls(ast.parse(src_prose)) == [], (
        "探针把注释里的 stamp( 算成了调用点 —— "
        "第 13 层：子串能告诉你「有什么」，不能告诉你「在哪用」。")


def test_the_generator_name_resolves_through_a_constant_or_directly():
    """G1 探针的正控与反控（**新加** —— 换判据就必须验新探针）。

    第一版判据要求实参是字面量，于是把
    `stamp(out, GENERATOR, ...)`（`GENERATOR` 恰好解析回本脚本）判死。
    第二版改成**求值**，那就必须证明它两种形态都认、且**认错的不认**。
    """
    src = (
        'GENERATOR = "scripts/diagnose_hit_rate.py"\n'
        'import provenance\n'
        'provenance.stamp(out, GENERATOR)\n'
    )
    tree = ast.parse(src)
    consts = _module_string_const(tree)
    assert consts == {"GENERATOR": "scripts/diagnose_hit_rate.py"}, consts
    call = _stamp_calls(tree)[0]
    assert _resolve(call.args[1], consts) == "scripts/diagnose_hit_rate.py"

    # 反控一：常量指向**别的**文件时必须解析出那个别的文件（不许一律 True）
    other = ast.parse('G = "scripts/somebody_else.py"\n'
                      'import provenance\nprovenance.stamp(out, G)\n')
    oc = _module_string_const(other)
    assert _resolve(_stamp_calls(other)[0].args[1], oc) == "scripts/somebody_else.py"

    # 反控二：**函数内**的局部变量不算模块级常量
    inner = ast.parse('import provenance\n'
                      'def main():\n'
                      '    G = "scripts/x.py"\n'
                      '    provenance.stamp(out, G)\n')
    assert _module_string_const(inner) == {}, (
        "探针把函数体内的绑定当成了模块级常量 ⇒ "
        "它会解析出一个运行时不存在的名字")
    assert _resolve(_stamp_calls(inner)[0].args[1], {}) is None, (
        "解析不出来的实参必须返回 None（**未解析**），"
        "而 `None in got` 与 `GEN_REL in got` 同为假 ⇒ 方向安全：判红而非判绿")

    # 反控三：直接字面量也认（不因为「支持常量」就漏了直接写的情况）
    direct = ast.parse("import provenance\n"
                       "provenance.stamp(out, 'scripts/direct.py')\n")
    assert _resolve(_stamp_calls(direct)[0].args[1], {}) == "scripts/direct.py"
