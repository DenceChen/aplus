"""安慰剂重跑的**预注册预测**必须由机械判据执行，不靠人读表。

## 这条测试存在的理由

`docs/TODO_placebo_control_2026-10-15.md` §0.1 在**重跑之前**写下了三条预测。
本仓第 129 层那条教训是：预注册的价值全在「判据先于数据」，
而**判据如果不变成会红的代码，它就只是一段散文**——
下次有人改窗口、改种子、或者干脆忘了跑，这三条预测会被安静地跳过。

⇒ 本文件把三条预测变成会红的断言，并把判决量从「人读表格」换成
`scripts/audit_placebo_rerun.py` 的退出码。

## 三条纪律

1. **锚从 git HEAD 读，不从工作区读**：读工作区就是自己判自己。
2. **逐位相同用 `float.hex()` 比**：`(0.0, -0.0)` 在 `==` 下相同、
   `(nan, nan)` 在 `==` 下不同——两种假判决，`hex()` 都没有。
3. **脚本必须换个 cwd 也能跑**：形态 24 那个洞是「按路径导入」比
   「按脚本运行」多给了它一样东西；`git show` 不加 `-C` 是同一个洞。
"""
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "audit_placebo_rerun.py"
DOC = "docs/TODO_placebo_control_2026-10-15.md"
REPORT = _ROOT / "data" / "reports" / "placebo_control.json"


def _run(cwd):
    """按路径跑脚本，`cwd` 可换 —— 用来证明它不依赖当前目录。"""
    return subprocess.run([sys.executable, str(SCRIPT)],
                          capture_output=True, text=True, cwd=str(cwd))


def test_the_audit_script_runs_from_an_unrelated_working_directory(tmp_path):
    """形状 24：脚本存在不等于脚本跑得起来，而「换个地方跑」最容易漏。

    ⚠️ 今天真踩了：`git show HEAD:<doc>` 不带 `-C` 时，
    在仓库外的 cwd 里直接 `CalledProcessError` ⇒ 一换目录就炸。
    这条把它钉死。
    """
    r = _run(tmp_path)
    # 产物尚未按新窗口重跑时退出码是 1（判决红），**但必须是判决红**，
    # 不是崩溃：stderr 里出现 traceback 就说明脚本自己炸了（第 134 层）。
    assert "Traceback" not in r.stderr, (
        f"脚本在 {tmp_path} 下崩了 —— 那不是判决，那是脚本自己坏掉：\n{r.stderr}")
    assert r.returncode in (0, 1), (
        f"退出码 {r.returncode} 不在 0/1 里：0=全兑现，1=有预测未兑现，"
        f"2=产物缺失。其它值说明脚本自己出了问题。\n{r.stdout}\n{r.stderr}")


def test_the_external_anchor_has_not_moved_since_it_was_frozen():
    """★ 锚块必须与 **git HEAD** 逐字节一致 —— 跑完就不许挪。

    锚（`t_blocked=1.7272132346592737` 等）是三条预测的唯一数值来源。
    如果它可以随手改，那么「产物变了 ⇒ 改锚对上」就是一条**永远绿**的判决。

    ⚠️ 这条比「锚的值对不对」更重要：它判的是**锚有没有被事后移动**，
    判据本身与锚的内容无关 ⇒ 它不是恒真断言。
    """
    def block(text):
        i = text.find("<!-- ANCHOR:placebo_baseline")
        assert i >= 0, "锚块不见了 —— 三条预测失去唯一的数值来源"
        j = text.index("-->", i) + 3
        return text[i:j]

    head = subprocess.run(["git", "-C", str(_ROOT), "show", f"HEAD:{DOC}"],
                          capture_output=True, text=True, check=True).stdout
    work = ( _ROOT / DOC).read_text(encoding="utf-8")
    assert block(work) == block(head), (
        "🚨 `ANCHOR:placebo_baseline` 块与 git HEAD 不一致。\n"
        "  它是三条预测的唯一数值来源，写于重跑落盘**之前**。\n"
        "  若你是在看到结果之后改的，那判决就是自己判自己。\n"
        "  ⇒ 要改锚必须先说清：哪条预测没兑现、为什么机制解释错了。\n"
        "  🚨 不许只把锚改成产物里的数。")


def _mod():
    """按路径导入并**每次重新执行**。

    ⚠️ 走 `import` 会命中 `sys.modules` 缓存 ⇒ 改了脚本不重起进程就测不到
    （「读快照的守卫等于没守」的同族）。每次重新执行文件，守卫才跟着字节走。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_audit_placebo_rerun", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _executable_source(path: Path) -> str:
    """剥掉**注释与 docstring** 后的源码。

    ⚠️ 为什么必须剥（第 108 层：「不许出现 `shell=True`」的守卫，
    而这个词出现在我自己的注释里）：
    文本级扫描会命中**解释「为什么别用字面量」的那段说明**，
    于是守卫把**自己的理由**判成违规——它守的成了
    「文件里没有这个字符串」，而不是「代码里没用这个字面量」。

    ⇒ 用 `ast` 删 docstring（它会随 `ast.unparse` 一起消失），
    `ast.unparse` 本身就不保留注释。
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_audit_script_contains_no_hardcoded_verdict_window():
    """🚨 判据不许是**我写下的日期**——真值必须来自已提交的榜宽产物。

    「重跑就是为了让 placebo 与榜宽对齐」是这件事的**全部理由**；
    把对齐目标写成日期字面量，就是把我自己的记忆当锚：
    今天恰好相等，链一旦改窗口，代码不会跟着动，守卫照样绿。

    ⇒ 这条不是查「数字对不对」，是查**判据的来源**。
    ⚠️ 它必须能变异：把脚本里的派生换回字面量，本条立刻红。

    ⚠️ 只查**可执行代码**：注释里写着「别用这个字面量」并**不是**违规，
    那是这条守卫自己的理由（第 108 层）。
    """
    import re
    code = _executable_source(SCRIPT)
    for lit in ("2026-10-12", "1395"):
        hit = re.search(rf"(?<![\w.\-]){re.escape(lit)}(?![\w.])", code)
        assert hit is None, (
            f"脚本的**可执行代码**里出现了写死的判决窗口字面量 {lit}"
            f"（在 `…{code[max(0, hit.start()-40):hit.end()+40] if hit else ''}…`）。\n"
            "  ⇒ 请改用 `target_from_frozen_commit()`："
            "判决窗口的真值在**已提交的榜宽产物**里，那才是重跑要对齐的对象。")


def test_the_three_preregistered_predictions_hold():
    """§0.1 三条预测 + 机制 + 章。判决量是**位模式**，不是 `==`。

    ## 🚨 这条**现在应当是红的**，直到链把 `placebo_control.py` 用
    ## 榜宽产物的窗口（…2026-10-12）重跑完（2026-10-15 约 12:37）。
    磁盘上仍是旧窗口（…2026-10-14）的字节。
    ⇒ 红在这里是**正确**的：产物还没重跑，预测就还没兑现。
    ⇒ **不许为了让门禁变绿而放宽它**，也不许 skip（第 130 层）。
    """
    if not REPORT.is_file():
        pytest.fail(f"{REPORT} 不存在 —— 产物缺失，判决为**未测**，不是「通过」")

    mod = _mod()
    a = mod.anchor_from_git_head()
    base = mod.baseline_from_frozen_commit()
    tgt = mod.target_from_frozen_commit()

    import json
    art = json.loads(REPORT.read_text(encoding="utf-8"))
    run0 = art["arms"]["P0_observed"]["runs"][0]

    # ★ 机制先行，且**量的是 `_block_means` 真正用的那个长度**。
    #   `_block_means` 截断 `s = daily.dropna()`，而产物里的
    #   `n_days = len(series)` 是 dropna **之前**的长度（第 135 层）。
    #   拿 `n_days // block * block` 去比，是在比另一个量。
    #   唯一直接可观测截断结果的是 `n_independent_obs`。
    assert run0["n_independent_obs"] == base["n_blocks"], (
        f"块数变了：旧 {base['n_blocks']} → 新 {run0['n_independent_obs']}\n"
        "  ⇒ 截断后的序列长度不同 ⇒ 「t_blocked 必然逐位相同」这条推理**不成立**")

    assert tgt["widths_agree"], (
        f"榜宽产物各档位 n_days 不一致：{tgt['widths_seen']} ⇒ "
        "判决窗口本身就没有唯一定义，先修榜宽那份产物")
    assert art["window"][1] == tgt["window_end"], (
        f"窗口末端是 {art['window'][1]}，榜宽产物是 {tgt['window_end']} —— "
        "产物还没用判决窗口重跑")
    assert run0["n_days"] == tgt["n_days"], (
        f"n_days = {run0['n_days']}，榜宽产物是 {tgt['n_days']}")
    assert run0["t_blocked"].hex() == a["t_blocked"].hex(), (
        f"t_blocked 位模式变了：{a['t_blocked'].hex()} → {run0['t_blocked'].hex()}\n"
        "  ⇒ 我的机制理解错了（`_block_means` 截的是 `dropna()` 后的长度，"
        "或 P0 臂不走那条路径）\n"
        "  ⇒ **此时任何安慰剂数字都不可引用**，先查机制；"
        "🚨 不许改判据、不许重跑到对上为止（第 129 层）。")
    assert art["p0_t_blocked"].hex() == a["p0_t_blocked"].hex()


def test_the_report_is_citable_which_means_it_has_a_stamp():
    """产物必须**可引用**：`fresh` 且依据是**字节证据**。

    预注册 §0.1 写死：这份将成为**第一份带字节证据的 placebo 产物**
    ⇒ `freshness()` 的依据一栏应印「字节证据」而不是「⚠️ 仅 commit 走查」。
    守卫 `test_provenance.py::the_cli_prints_the_basis_of_every_fresh_verdict`
    钉的是那一栏不许消失；本条钉的是**这份产物真的有那一栏**。

    ⚠️ 传参必须是**完整路径**：传裸名会让 `freshness()` 去找
    `placebo_control.provenance.json`，而真名是
    `placebo_control**.json**.provenance.json`
    ⇒ 静默返回 `unknown`「没有 sidecar」（`TODO_generator_static_guards` §1）。
    """
    sys.path.insert(0, str(_ROOT))
    from aplus.provenance import freshness

    sidecar = _ROOT / "data" / "reports" / "placebo_control.json.provenance.json"
    assert sidecar.is_file(), (
        f"{sidecar.name} 不存在 ⇒ 该产物按本仓规矩是 unknown，**不可引用**。\n"
        "  `PLACEBO_NULL` 是承重判决，在它可引用之前，"
        "「因子有效」一个字都不能写。")

    f = freshness(REPORT)
    assert f["verdict"] == "fresh", f"判决是 {f['verdict']}：{f['reason']}"
    assert "字节" in f["reason"], f"依据不是字节证据：{f['reason']}"
