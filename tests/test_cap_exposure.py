"""市值暴露判决脚本的守卫（预注册 `docs/TODO_cap_exposure_2026-10-15.md`）。

★ 本文件的主轴是**判决分支的可达性**，不是产物的数值。

第 141 层的教训反向也成立：预注册里写着「四态判决」，
而**四条里只要有一条在真实数据上永远走不到，它就等于没注册** ——
判据是从产物反读出来的，而产物里只有走到的那个标签。
⇒ 这里用**合成夹具**逐条钉「这四个标签都存在、且互斥穷尽」。

⚠️ 与「四态」配套的第二条是**补记 3 的量词**：
`POWERLESS` 要求 **全部 5 档**都「零分布 95 分位 ≥ 实测 |β|」，
而 `NO_MEASURED` 只需要**一档**分得出来。方向一旦搞反，
`POWERLESS` 会掩盖真实存在的暴露（第 122 层三态语义里最贵的误读）。
⇒ `test_one_arm_that_can_resolve_blocks_the_powerless_label` 单独钉这一条。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "cap_exposure.py"

# ⚠️ 按路径加载而非 `import cap_exposure`：后者依赖 `sys.path` 的偶然状态，
#   在别的 cwd 下会拿到**另一个**文件（第 22 条目记录的正是这个坑）。
_spec = importlib.util.spec_from_file_location("_cap_exposure_under_test", SRC)
M = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT))
_spec.loader.exec_module(M)


def _arm(k: int, beta: float, passes: bool, q95):
    return {"k": k, "beta": beta, "passes_bonferroni": passes,
            "beta_star_abs_q95": q95}


def _five(beta_sign: float, passes: bool, q95_ratio: float):
    """5 档同号、同判、零分布宽度同比例的合成臂。

    `q95_ratio = q95 / |β|` ⇒ `>1` 表示「分不出」，`<1` 表示「分得出」。
    """
    arms = []
    for k in M.K_ARMS:
        beta = beta_sign * 0.01
        arms.append(_arm(k, beta, passes,
                         abs(beta) * q95_ratio if q95_ratio is not None else None))
    return arms


# ── ① 四态判决：每一条都必须可达，且给出的就是那个标签 ──────────────

@pytest.mark.parametrize("arms,expected", [
    # 有档位过线 + 全同号 ⇒ CONFIRMED
    (_five(-1.0, True, 0.5), "SIZE_EXPOSURE_CONFIRMED"),
    (_five(+1.0, True, 0.5), "SIZE_EXPOSURE_CONFIRMED"),
    # 有档位过线但符号随 K 变 ⇒ OVERFIT
    ([_arm(5, -0.01, True, 0.005), _arm(10, -0.01, False, 0.005),
      _arm(21, +0.01, False, 0.005), _arm(42, +0.01, False, 0.005),
      _arm(63, +0.01, False, 0.005)], "SIZE_EXPOSURE_OVERFIT"),
    # 0/5 过线 + 每档都分不出 ⇒ POWERLESS
    (_five(-1.0, False, 2.0), "SIZE_EXPOSURE_POWERLESS"),
    # 0/5 过线 + 分得出而没分出来 ⇒ NO_MEASURED
    (_five(-1.0, False, 0.5), "NO_SIZE_EXPOSURE_MEASURED"),
])
def test_every_preregistered_verdict_is_reachable(arms, expected):
    verdict, why = M.decide(arms)
    assert verdict == expected, f"得到 {verdict}，预注册要求 {expected}；理由={why}"
    assert why.strip(), "判决必须自带理由 —— 空理由的判决没法被人复核"


def test_the_four_verdict_labels_are_four_distinct_strings():
    """「四态」这个数字本身要立得住。"""
    labels = {M.decide(a)[0] for a in (
        _five(-1.0, True, 0.5),
        [_arm(5, -0.01, True, 0.005), _arm(10, +0.01, False, 0.005),
         _arm(21, +0.01, False, 0.005), _arm(42, +0.01, False, 0.005),
         _arm(63, +0.01, False, 0.005)],
        _five(-1.0, False, 2.0),
        _five(-1.0, False, 0.5),
    )}
    assert labels == {"SIZE_EXPOSURE_CONFIRMED", "SIZE_EXPOSURE_OVERFIT",
                      "SIZE_EXPOSURE_POWERLESS",
                      "NO_SIZE_EXPOSURE_MEASURED"}, labels


# ── ② 补记 3 的量词：一档分得出就不许说 POWERLESS ─────────────────────

def test_one_arm_that_can_resolve_blocks_the_powerless_label():
    """四档「分不出」+ **一档分得出** ⇒ 必须 NO_MEASURED。

    这是补记 3 的全部内容。方向搞反的代价：
    `POWERLESS` 会把「我们没测出来」说成「它不存在」。
    """
    arms = _five(-1.0, False, 2.0)
    arms[2]["beta_star_abs_q95"] = abs(arms[2]["beta"]) * 0.5  # K=21 分得出
    verdict, why = M.decide(arms)
    assert verdict == "NO_SIZE_EXPOSURE_MEASURED", (
        f"得到 {verdict} —— 一档分得出就足以否定 POWERLESS（补记 3）；理由={why}")
    assert "21" in why, f"理由必须点名是哪一档分得出来：{why}"


def test_the_powerless_label_also_needs_the_flag_on_every_arm():
    """逐档判据必须**逐档落进产物**，不许只留一个合并后的布尔。

    合并会丢掉「哪一档分得出来」这个最该被看见的事实（补记 3 末段）。
    """
    arms = _five(-1.0, False, 2.0)
    arms[3]["beta_star_abs_q95"] = abs(arms[3]["beta"]) * 0.5
    M.decide(arms)
    flags = [a["powerless_on_this_arm"] for a in arms]
    assert flags == [True, True, True, False, True], flags


def test_a_missing_zero_distribution_is_not_treated_as_unresolvable():
    """`q95 = None`（仪器没交出它）**不等于**「分不出」，也**不等于**「分得出」。

    🚨 2026-10-15 改判（这条守卫自己钉的是错的行为，故一并改写）：
       旧实现把它按「分得出」算 ⇒ 落 `NO_MEASURED`，而那个标签的文案是
       「分得出来而没分出来」——**一句关于事实的断言，
       触发它的却是「根本没量到」**（第 122 层）。

       ⇒ 旧方向的两个假判决，**贵贱不分**：按「分不出」算会写出 `POWERLESS`
         （拿缺档当判据，第 89 层）；按「分得出」算会写出 `NO_MEASURED`
         （凭空断言一个量得出来的量级）。**它们是同一个错误的两个方向**，
         所以「取保守方向」这条理由在此处**不成立** ——
         两个方向都是断言，只是一个听起来较弱。

    ⇒ 处置：响亮抛错，**不是**补一个第五态。
      预注册 §3 的四个状态是对**已测量的臂**定义的；
      没测出来的输入在那个定义域之外 ——
      事后往里加状态就是「补写出来的登记冒充预注册」（形状 22）。
    """
    arms = _five(-1.0, False, None)   # 五档全都没有零分布
    with pytest.raises(ValueError) as ei:
        M.decide(arms)
    msg = str(ei.value)
    assert "beta_star_abs_q95" in msg, msg
    assert "K=5" in msg, f"必须点名是哪几档没量到：{msg}"


# ── ③ 判决是幂等的：decide 会往 arm 上写逐档标志，两次判必须同解 ─────

def test_deciding_twice_gives_the_same_answer_and_the_same_arms():
    """`decide` **就地**改写 `arms`（写 `powerless_on_this_arm`）。

    ⇒ 必须证明它改的只是自己写的那一个字段，且不改变判决输入
    （`passes_bonferroni` / `beta` / `q95` 一律不许动）。
    否则「判决依赖自己上一次判决留下的痕迹」，产物一读一写就可能翻面。
    """
    arms = _five(-1.0, False, 2.0)
    keys = ("k", "beta", "passes_bonferroni", "beta_star_abs_q95")
    before = [{k: a[k] for k in keys} for a in arms]
    v1 = M.decide(arms)
    v2 = M.decide(arms)
    assert v1 == v2, f"第一次 {v1}，第二次 {v2}"
    after = [{k: a[k] for k in keys} for a in arms]
    assert before == after, "decide 改动了判决自己的输入字段"


# ── ④ 判决的输入集合是封闭的：越界输入必须响亮地坏掉 ────────────────

def test_an_unknown_verdict_is_impossible_because_the_return_is_a_literal():
    """判决只能来自四个**字面量**，不许由字符串拼接生成。

    拼接出来的标签没有 `test_every_preregistered_verdict_is_reachable` 能认。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "decide")
    ret_labels = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            first = node.value.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                "decide 的判决标签必须是字符串**字面量**，"
                f"得到的是 {ast.dump(first)[:120]}")
            ret_labels.add(first.value)
    assert ret_labels == {"SIZE_EXPOSURE_CONFIRMED", "SIZE_EXPOSURE_OVERFIT",
                          "SIZE_EXPOSURE_POWERLESS",
                          "NO_SIZE_EXPOSURE_MEASURED"}, ret_labels


# ── ⑤ 缺档必须报错，绝不回落（第 89 层：崩溃与空目录不可区分） ────────

def test_a_missing_arm_raises_and_names_the_exact_rerun_command(tmp_path):
    """少一档 ⇒ 抛异常 + **给出可复制的重跑命令**。

    静默 `fillna(0)` 会把「没量到市值」说成「市值为零」（第 9 层）。
    """
    with pytest.raises(FileNotFoundError) as ei:
        M._load_arm(21, tmp_path, M.MAIN_COL)
    msg = str(ei.value)
    assert "wf_weights_backtest_k21" in msg, msg
    assert "--rebalance-every 21" in msg, f"必须给出可复制的重跑命令：{msg}"


def test_an_arm_missing_the_cap_column_raises_instead_of_falling_back(tmp_path):
    """列缺失 ⇒ `KeyError` + 明确要求**整链**重跑五档。

    只补一档会让「5 档全同号」变成 4 档全同号，判据失去意义。
    """
    import pandas as pd
    p = tmp_path / "reports" / "wf_weights_backtest_k21.daily.parquet"
    p.parent.mkdir(parents=True)
    cols = {M.FA.Y_COL: [0.0, 1.0], **{c: [0.0, 1.0] for c in M.FACTOR8}}
    pd.DataFrame(cols).to_parquet(p)          # 刻意不带任何 cap 列
    with pytest.raises(KeyError) as ei:
        M._load_arm(21, tmp_path, M.MAIN_COL)
    msg = str(ei.value)
    assert M.MAIN_COL in msg, msg
    assert "重跑" in msg, f"必须说清处置：{msg}"


# ── ⑥ 测量仪器只有一台（第 22 条目：两份会漂移且零告警） ─────────────

def test_this_script_does_not_reimplement_the_measuring_instrument():
    """不许自带 `ols` / `wild_bootstrap` / `sign_consistency` / `robust_z`。

    锚在 **`def` 的名字**上而不是子串 —— 子串会被我自己写的注释满足（第 13 条）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    banned = {"ols", "wild_bootstrap", "sign_consistency", "robust_z",
              "_block_means", "analyze", "power_floor"}
    assert not (defined & banned), (
        f"cap_exposure.py 自己实现了 {defined & banned} —— "
        "测量仪器只能有一台（factor_attribution.py）")


def test_the_instrument_is_loaded_from_factor_attribution_by_path():
    """必须是**按路径加载**那个脚本，且模块里确实带着它的因子声明。

    `import factor_attribution` 依赖 `sys.path` 的偶然状态（第 22 条目）。

    🚨 第三版改判（这条守卫自己写了两次失败史，这里是第三次空/红的教训）：
       前两版把 `targets == [...]` 写成**恰好一处**的等值断言，
       于是「这个文件按路径加载模块的次数」被当成了不变量 ——
       而真正的不变量是**「不许靠 `sys.path` 的偶然状态」**。
       加一次合规的按路径加载（2026-10-15：为了从生产者推导列名）
       就会红 ⇒ **守卫红而代码是对的**，且修法方向很危险：
       它会诱导人去「把多出来的加载挪走」而不是「放宽断言」。

       ⇒ 放宽**只能往更强走**：① 仪器的加载必须仍在；② 任何按路径加载
         的模块都必须在**白名单**里（不许悄悄多引一个模块）；
         ③ 源码里**不许出现裸 `import`** 这两个模块名（新加的更强一侧）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    # ⚠️ 谓词锚在 **`spec_from_file_location` 的实参**上，不是「文件里出现过
    #   factor_attribution 这个词」—— 后者会被 import 行、甚至注释满足。
    #   （第一版写的是找 `.load(` 调用，而真实路径是
    #   `spec_from_file_location` → `module_from_spec` → `loader.exec_module`，
    #   压根没有 `.load` ⇒ **守卫红而代码是对的**，第 131 条目。）
    targets = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "spec_from_file_location":
            # ⚠️ 实参是 `_SCRIPTS / "factor_attribution.py"` —— 一个 **BinOp**，
            #   不是裸 Constant。第二版只收裸 Constant 又空了一次 ⇒
            #   收**实参内部**的所有字符串叶子（路径拼接的右操作数就在里面）。
            for arg in n.args:
                targets += [c.value for c in ast.walk(arg)
                            if isinstance(c, ast.Constant)
                            and isinstance(c.value, str)]

    allowed = {"_fa_cap_exposure", "factor_attribution.py",
               "_bwfw_cap_exposure", "backtest_walk_forward_weights.py"}
    assert targets, "一次按路径加载都没有 ⇒ 仪器必来自 sys.path 的偶然状态"
    assert set(targets) <= allowed, (
        f"按路径加载了白名单外的模块：{sorted(set(targets) - allowed)}")
    assert {"_fa_cap_exposure", "factor_attribution.py"} <= set(targets), (
        f"仪器必须按路径加载，实参却给了 {targets}")

    # 🚨 更强的一侧：裸 `import` 一个都不许（第 22 条目的病根）。
    bare = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            bare |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            bare.add(n.module)
    smuggled = {m for m in bare
                if m.split(".")[0] in {"factor_attribution",
                                        "backtest_walk_forward_weights"}}
    assert not smuggled, (
        f"这些脚本必须按路径加载，不许裸 import：{sorted(smuggled)}")

    assert M.FACTOR8 == tuple(M.FA.FACTOR_COLS), (
        "8 个既有因子必须取自仪器自己的声明，不许抄第二份")
    assert M.FA is not None and hasattr(M.FA, "analyze")


# ── ⑦ 对照臂只报不判 ────────────────────────────────────────────────

def test_the_control_arm_column_never_reaches_the_verdict():
    """`decide` 与 `_analyze_arm` 的函数体里**不许**出现 `CTRL_COL`。

    对照臂存在的唯一目的是让「缩尾与否会不会改变结论」成为可读事实。
    它一旦进了判决分支，就变成「挑好看的那个」（预注册补记 1）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef)}
    for name in ("decide", "_analyze_arm"):
        names = {n.id for n in ast.walk(fns[name]) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(fns[name])
                  if isinstance(n, ast.Attribute)}
        assert "CTRL_COL" not in names, (
            f"{name}() 读了 CTRL_COL —— 对照臂不得进判决")


def test_the_two_columns_are_different_calibers_not_two_names_for_one():
    """主列与臂列必须是**两个不同的列名**，且都不是被硬打死的产物列。

    同名同义的两列会让对照臂恒等于主臂，而它**照样出数**、零告警。
    """
    assert M.MAIN_COL != M.CTRL_COL
    assert M.MAIN_COL != M.FA.Y_COL and M.CTRL_COL != M.FA.Y_COL


# ── ⑧ 预注册的「不能证明什么」必须落在产物里，不许只印在终端 ────────

def test_what_this_cannot_prove_is_non_empty_and_reaches_the_payload():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    payload_keys = set()
    for node in ast.walk(main):
        if isinstance(node, ast.Dict):
            payload_keys |= {k.value for k in node.keys
                             if isinstance(k, ast.Constant)}
    assert "what_this_cannot_prove" in payload_keys, (
        f"产物键里没有它；现有：{payload_keys}")
    assert len(M.WHAT_THIS_CANNOT_PROVE) >= 5
    assert all(isinstance(s, str) and s.strip()
               for s in M.WHAT_THIS_CANNOT_PROVE)


# ── ⑨ 门槛不许写字面量：0.05/9 必须由候选数推导（第 144 条目） ──────

def test_no_hardcoded_bonferroni_literal_in_the_code():
    """AST 扫**所有** `0.05` 参与的算术 —— 注释不参与，所以「注释里有」无害。

    真门槛在仪器里按候选数算；这里手打一个 `0.05/9` 就是第二份真相源。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and \
                        isinstance(sub.value, float) and \
                        abs(sub.value - 0.05) < 1e-12:
                    hits.append(ast.dump(node)[:120])
    assert not hits, f"手打的 0.05：{hits}"


def test_the_preregistration_document_actually_exists_and_declares_four_states():
    """预注册**必须真的在盘上**，且标题里的状态数与代码里的四态一致。

    「判据已预注册」是一句可以被文档满足的话（形状 22）。
    """
    p = ROOT / M.PREREG
    assert p.exists(), f"预注册不在：{p}"
    text = p.read_text(encoding="utf-8")
    assert "四态" in text, "预注册的 §3 标题必须是「四态」（补记 2）"
    for label in ("SIZE_EXPOSURE_CONFIRMED", "SIZE_EXPOSURE_OVERFIT",
                  "SIZE_EXPOSURE_POWERLESS", "NO_SIZE_EXPOSURE_MEASURED"):
        assert label in text, f"预注册里没有 {label}"


# ── ⑩ K 是 rebalance_every，从产物自己的字段读出来的，不是猜的 ──────

@pytest.mark.parametrize("k", [5, 10, 21, 42, 63])
def test_each_arm_name_names_its_own_rebalance_frequency(k):
    """档名必须**自解释**：文件名里的 K 就是那一档的 `rebalance_every`。

    否则「5 档」与「5 个不同的调仓周期」的对应关系只存在于某个人的记忆里，
    而判决的整个前提就是跨 5 档一致。
    """
    assert M.arm_path(k, ROOT / "data").name == \
        f"wf_weights_backtest_k{k}.daily.parquet"
    assert k in M.K_ARMS


# ── ⑪ 生产端真的跑起来：列名/键名漂移对结构守卫**全隐形** ──────────
# 🚨 2026-10-15 审计实录（三条按严重度）：
#   ① `CTRL_COL` 被手打成 `expo_logcap_rawz`，生产者从无此列 ⇒ 对照臂从未运行；
#   ② `decide()` 把 `None`/`NaN` 一起算成「分得出」⇒ 输出事实断言；
#   ③ AST 取证：`tests/` 里 `_analyze_arm`/`measure`/`main` **调用次数为 0**。
#   ①② 都是**因为**③才活下来的 —— 下面这几条守的正是③。


def test_the_two_cap_columns_are_columns_the_producer_actually_emits():
    """主列与对照列必须**取自生产者自己的导出清单**，不许手打。

    当天实测：`CTRL_COL = "expo_logcap_rawz"`，而生产者
    （`EXPO_COLS` 末两位 `logcap` / `logcap_raw`，配 `f"expo_{f}"`）
    **从无此列** ⇒ `main()` 每次跑到对照臂就崩，产物一个字都写不出来，
    而当时 12 条守卫**全绿**。

    根因不是「手滑打错一个字」：命名约定 `f"expo_{f}"` 的唯一主人在生产者，
    手打一个后缀就是**第二个主人**（第 148 层同族 ——
    那层是「下标偏移量有第二个主人」，这层是「列名有第二个主人」）。
    """
    emitted = {f"expo_{c}" for c in M.PRODUCER.EXPO_COLS}
    assert M.MAIN_COL in emitted, (
        f"主列 {M.MAIN_COL!r} 不在生产者的导出清单里：{sorted(emitted)}")
    assert M.CTRL_COL in emitted, (
        f"对照列 {M.CTRL_COL!r} 不在生产者的导出清单里：{sorted(emitted)}")


def _producer_frame(n: int = 300, seed: int = 20261015):
    """造一份**生产者会写出的形状**的日活。

    列清单**直接取自它自己的 `EXPO_COLS`** ⇒ 这份夹具不可能与生产漂移。
    当天旧的夹具全是手搓的 `expo_f0..f7`（`factor_attribution.py:99` 记过
    「这两条守卫与判决用的真列**无交集** ⇒ 两条都是恒真」）——
    夹具的**形状**对不上真实产物，守卫就只在夹具的世界里成立。
    """
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    cols = {M.FA.Y_COL: rng.normal(size=n)}
    for c in M.PRODUCER.EXPO_COLS:        # 注意存的是**不带前缀**的列名
        cols[f"expo_{c}"] = rng.normal(size=n)
    return pd.DataFrame(cols)


def _write_one_arm(tmp_path, k=None):
    import pandas as pd
    k = M.K_ARMS[0] if k is None else k
    p = tmp_path / "reports" / f"wf_weights_backtest_k{k}.daily.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    _producer_frame().to_parquet(p)
    return k


def test_the_producer_side_really_runs_on_a_frame_the_producer_would_write(tmp_path):
    """`_analyze_arm` 从 parquet 一路跑到判决输入，**必须真的被调用过**。

    🚨 当天 AST 取证：`tests/` 全文件的 `ast.Call` 里被调目标只有
       `decide` / `_load_arm` / `arm_path`；`_analyze_arm` / `measure` / `main`
       的调用次数**为 0**（它们只被 `ast.walk` 当节点遍历）。
       ⇒ 从 parquet 到判决的**全部接线**——列名、`_beta_star_abs_q95` 这个键、
         per-arm 字段名、`n_factors` 是不是 9——**没有一条被守卫看着**。
       ⇒ 后果不是「覆盖不足」，是**上面那个打错的列名对 12 条守卫全隐形**。
    """
    import numpy as np
    k = _write_one_arm(tmp_path)
    arm = M._analyze_arm(k, tmp_path, M.MAIN_COL)
    for f in ("beta", "t", "se", "boot_p", "beta_star_abs_q95",
              "joint_p", "joint_F", "p_bonferroni"):
        v = arm[f]
        assert v is not None and np.isfinite(v), f"{f} 没有量出来：{arm}"
    assert arm["n_factors"] == len(M.FACTOR8) + 1, (
        f"Bonferroni 分母必须是「8 因子 + 市值列」=9，拿到 {arm['n_factors']}"
        f" —— 分母错 ⇒ 门槛错 ⇒ 判决错，且产物里看不出来")


def test_the_control_arm_runs_on_the_very_same_frame(tmp_path):
    """**对照臂也必须跑得起来** —— 它跑不起来时主判据照样出数。

    这是当天那条缺陷能长期隐身的最后一道：`main()` 里
    `measure(..., MAIN_COL)` 先成功、`measure(..., CTRL_COL)` 再崩，
    ⇒ 主判据的数已经在内存里了，只是**没人看得见**，
    而崩之前没有任何东西会说话。
    """
    import numpy as np
    k = _write_one_arm(tmp_path)
    arm = M._analyze_arm(k, tmp_path, M.CTRL_COL)
    assert np.isfinite(arm["beta"]), f"对照臂没量出 β：{arm}"
    assert arm["n_factors"] == len(M.FACTOR8) + 1


@pytest.mark.parametrize("bad", [None, float("nan")],
                         ids=["missing", "nan"])
def test_a_zero_distribution_that_is_not_a_measurement_blocks_the_verdict(bad):
    """`None` 与 `NaN` 是**两种不同的非观测**，却坍缩成同一句事实断言。

    当天实测：`decide()` 对 `q95=None` 与 `q95=NaN` 返回**逐字相同**的元组，
    文案是「分得出来而没分出来 ⇒ 是真的没有」；
    而 `_analyze_arm` 用的 `.get(cap_col)` 让「键不存在」也返回 `None`
    ⇒ **缺键 / 未算 / 数值崩溃**三种语义完全同形（第 122 层）。

    ⇒ 非观测一律**拒绝出判决**，不许换个方向照样断言（第 122 层）。
    """
    arms = _five(-1.0, False, 2.0)      # 五档都「分不出」⇒ 不拦就会判 POWERLESS
    arms[2]["beta_star_abs_q95"] = bad
    with pytest.raises(ValueError) as ei:
        M.decide(arms)
    assert "beta_star_abs_q95" in str(ei.value), str(ei.value)


def test_zero_arms_is_refused_rather_than_judged():
    """`all([])` 在 Python 里恒为真 ⇒ **零观测**会被判成「这份样本分不出」。

    当天实测：`decide([])` 返回 `('SIZE_EXPOSURE_POWERLESS', '0/0 档过线…')`。
    0 档能说的话只有一句：**不出判决** —— 这是第 89 层
    「崩溃与空目录不可区分」的镜像。
    """
    with pytest.raises(ValueError) as ei:
        M.decide([])
    assert "0 档" in str(ei.value), str(ei.value)


def test_the_missing_column_message_names_what_the_parquet_actually_has(tmp_path):
    """报错文案**不许把矛头指向错误的成因**。

    当天实测：文案写「大概率是那一档**在接线 `expo_logcap` 之前**跑的（旧产物）」，
    而真实成因是消费者问了一个生产者从无此列的名字
    ⇒ 照文案办事会**白跑 5×7 分钟的链**，什么也修不好。
    这与台账里那条「断言对、成因错」同族：抛异常是对的，推断是错的。

    ⇒ 文案必须**同时**摆出「要的列」与「parquet 里实际有哪些 `expo_*`」，
      两个成因并列，不替读者下结论。
    """
    import pandas as pd
    p = tmp_path / "reports" / "wf_weights_backtest_k21.daily.parquet"
    p.parent.mkdir(parents=True)
    cols = {M.FA.Y_COL: [0.0, 1.0], **{c: [0.0, 1.0] for c in M.FACTOR8}}
    pd.DataFrame(cols).to_parquet(p)
    with pytest.raises(KeyError) as ei:
        M._load_arm(21, tmp_path, "expo_logcap_nonexistent")
    msg = str(ei.value)
    assert "expo_logcap_nonexistent" in msg, msg
    assert "expo_ivol" in msg, f"必须报出 parquet 里实际存在的 expo_* 列：{msg}"
    assert "EXPO_COLS" in msg, (
        f"文案必须把**权威列名清单的出处**指给读者 —— "
        f"只说「重跑」会让人白跑 5×7 分钟：{msg}")


@pytest.mark.parametrize("how", ["whole_key", "just_this_column"],
                         ids=["key_absent", "column_absent"])
def test_a_missing_zero_distribution_key_stops_at_the_producer_side(
        tmp_path, monkeypatch, how):
    """仪器没交出 `_beta_star_abs_q95` 时，**不许**用 `.get()` 静默变成 `None`。

    当天实测：`_analyze_arm` 写的是 `res["_beta_star_abs_q95"].get(cap_col)`
    ⇒ 「键不存在」与「值为 `None`」与「值为 `NaN`」三者完全同形，
    而下游把后两者判成「分得出」⇒ **仪器故障一路走成了
    「分得出来而没分出来」这句事实断言**，且全程零告警（第 122 层）。

    🚨 **必须两条路都测**：只测「整键不存在」的话，
       `if not isinstance(q95_by_col, dict)` 一个人就把测试挡住了，
       而「字典在、唯独缺这一列」那条路一次都没被执行过 ——
       而那正是 `res["factors"][cap_col]` 与 `res["_beta_star_abs_q95"][cap_col]`
       两个投影**各自漂移**时走的路（第 148 层：并列字段必须分别取证）。
    """
    k = _write_one_arm(tmp_path)
    real = M.FA.analyze

    def _broken(df, factor_cols, label):
        res = dict(real(df, factor_cols, label))
        if how == "whole_key":
            res.pop("_beta_star_abs_q95", None)
        else:
            res["_beta_star_abs_q95"] = {
                c: v for c, v in res["_beta_star_abs_q95"].items()
                if c != M.MAIN_COL}
        return res

    monkeypatch.setattr(M.FA, "analyze", _broken)
    with pytest.raises(KeyError) as ei:
        M._analyze_arm(k, tmp_path, M.MAIN_COL)
    assert "_beta_star_abs_q95" in str(ei.value), str(ei.value)


# ── ⑫ 对照臂那一句必须是**两臂之间**的比较（2026-10-15 首次真跑即假）──
# 🚨 实录：曝光脚本第一次跑到对照臂，产物里写的是
#    「⚠️ **两臂方向不一致**（robust_z 符号 [-1, -1, 1, -1, 1]
#      vs 裸 z [-1, -1, 1, -1, 1]）」——
#    **两组符号逐位相同**，而它说「不一致」。
#    根因：`same_dir` 里把「**各臂跨 K 是否同号**」当成了「**两臂是否给相反方向**」
#    ——后者才是预注册补记 1 的原话。这是**两个不同的问题**，
#    且答错的那一路会让 `consistent=False`（臂内跨 K 有正有负）
#    冒充成臂与臂之间的分歧。
# ⇒ 与台账「断言对、成因错」同族，且更贵：它是**产物里的一句事实断言**，
#   而且**紧挨着把它否掉的那组数**。


def _side(signs, consistent):
    return {"sign_consistency": {"signs": list(signs),
                                 "consistent": consistent,
                                 "sign": signs[0]}}


def test_identical_sign_vectors_are_reported_as_identical_not_as_disagreement():
    signs = [-1, -1, 1, -1, 1]          # 两臂都跨 K 有正有负
    note = M.control_arm_note(_side(signs, False), _side(signs, False))
    assert "两臂方向不一致" not in note, (
        f"两组符号逐位相同，却被写成「两臂方向不一致」：{note}")
    assert "逐位相同" in note, note
    assert "没有单一方向" in note or "无法评估" in note, (
        f"臂内跨 K 不同号 ⇒「偏大盘还是偏小盘」**无方向可答**，"
        f"必须说清「无法评估」而不是「相反」：{note}")


def test_genuinely_opposite_sign_vectors_are_reported_as_disagreement():
    note = M.control_arm_note(_side([-1] * 5, True), _side([+1] * 5, True))
    assert "两臂方向不一致" in note, note


def test_one_consistent_one_not_is_still_a_comparison_between_the_two_arms():
    """一臂同号、另一臂不同号 ⇒ 两臂**确实不一致**（符号向量不同），
    但理由必须写成「两臂的符号向量不同」，不许借臂内一致性说事。"""
    note = M.control_arm_note(_side([-1] * 5, True), _side([-1, 1, -1, 1, -1], False))
    assert "两臂方向不一致" in note, note
    assert "逐位相同" not in note, note


def _main_dict_values(main, key):
    out = []
    for n in ast.walk(main):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    out.append(v)
    return out


def _main_fn():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")


def test_the_control_arm_note_reaches_what_this_cannot_prove():
    """预注册补记 1 的原话是「**记进 `what_this_cannot_prove`**」。

    只在产物顶层放一个 `control_arm_note` 键**不算数** ——
    那个列表才是「读产物的人」会去看的地方，而它当时照抄常量，
    实测结论一个字都没进去。

    🚨 2026-10-15 第一版写的是 `assert not isinstance(v, ast.Name)`
    —— 它只排除「照抄常量」**一种写法**，于是变异把它换成
    `list(WHAT_THIS_CANNOT_PROVE)`（`ast.Call`）就整条通过（CE10 存活）。
    ⇒ 那是**代理**当判据：真实要守的是「实测结果**在不在里面**」，
      不是「它长得像不像那个常量」（第 144 层：同一个变量才是及格线）。
    ⇒ 现在钉的是**必须引用 `control_note`**。
    """
    vals = _main_dict_values(_main_fn(), "what_this_cannot_prove")
    assert vals, "产物里没有 what_this_cannot_prove 键"
    for v in vals:
        names = {x.id for x in ast.walk(v) if isinstance(x, ast.Name)}
        assert "control_note" in names, (
            f"what_this_cannot_prove 的值引用了 {sorted(names)}，"
            f"没有引用 `control_note` ⇒ 对照臂的实测结果一个字都没进去 —— "
            f"预注册补记 1 说的就是「记进这个列表」")


def test_main_actually_computes_the_note_instead_of_writing_one():
    """`control_arm_note` 的实算结果必须流进产物。

    🚨 上一条守卫看不见这一半：把 `control_note = control_arm_note(...)`
    换成一句手打字符串，字典里仍然是 `[*常量, control_note]` ⇒ 上一条照样绿。
    ⇒ 接线单独一条守卫：`control_note` 这个名字的赋值右值
      **必须是对 `control_arm_note` 的一次调用**（CE9）。
    """
    main = _main_fn()
    calls = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "control_arm_note"]
    assert len(calls) == 1, (
        f"main() 里对 control_arm_note 的调用必须恰好 1 次，实测 {len(calls)} 次")

    assigns = [n for n in ast.walk(main)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "control_note"
                       for t in n.targets)]
    assert assigns, "main() 里没有给 control_note 赋值"
    for a in assigns:
        assert isinstance(a.value, ast.Call), (
            f"control_note 的右值是 {type(a.value).__name__} 而不是调用 ⇒ "
            f"产物里那句话是手打的，不是实算出来的")
        assert isinstance(a.value.func, ast.Name) \
            and a.value.func.id == "control_arm_note", (
            f"control_note 调的是 {ast.dump(a.value.func)[:60]}，"
            f"不是 control_arm_note")
