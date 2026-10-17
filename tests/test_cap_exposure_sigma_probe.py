"""守卫：`scripts/cap_exposure_sigma_probe.py` 的**三样东西**。

🚨 本文件最初存在的原因是一次**真的 exit 3**：
   探针量到 **1394** 天而产物自报 **1395** 天 ⇒ 它自己拒绝出判决（rc=3）。
   根因是**两个约定相反、字段名一模一样的 `end`**：

   | 出处 | 写法 | 约定 |
   |---|---|---|
   | `cap_weighted_benchmark.py::arm_stats` | `"window_end": days[-1]` | **闭区间** |
   | `aplus/panel.py::window_dates` | `[start, end)` | **半开** |

   探针照 `window_dates` 的 docstring 契约调用、**没写错**，
   错在把闭区间字段直接喂进半开参数 ⇒ 安静地少一天。

## 覆盖三个模块级判据（**提取的理由各不相同，缺一都守不住**）

| 判据 | 它原先住在哪 | 为什么必须提到模块级 |
|---|---|---|
| `axis_end_exclusive` | 模块级 | 本来就在模块级；G3 用 AST 钉**调用点**（探针被调函数、缺陷在调用点，第 189 层） |
| `nan_agrees` | **`_run()` 的逐票读循环里** | 埋在循环里 ⇒ **结构上没有任何守卫够得着** ⇒ 放宽成 `else True` 对全部守卫隐形（第 181 层） |
| `width_relationship` | **不存在**（四处手打方向词） | 产物与打印层四处都写着「比 V 宽 ⇒ 保守方向」，而实测 **W 比 V 窄约 28 倍** |

⇒ 第三行是本文件最贵的一节：**四处手打的方向词与算出来的数同处一份产物**
  （`arm_W.shape`、docstring、段注释、打印标签），**没有任何东西会红**。
  修法不是改句子，是**让方向成为函数的输出**、让散文去插值那个输出。

## 五条纪律（本文件按这五条写，不是按「看起来严谨」写）

① **守卫必须 import 得判据**（`code-a-guard-cannot-import-is-effectively-unguarded`）
   —— 翻译若埋在 `_run()` 里就够不着，所以它是模块级函数 `axis_end_exclusive`。

② **钉语义，不钉自洽**：`assert axis_end_exclusive(d) == <手算值>` 是自己和自己比。
   外部锚是**生产真函数** `window_dates` 的裁剪行为 ——
   「翻译之后 END 回到轴上、不翻译就回不来」这件事**只有真函数能证**。

③ ⚠️ **必须有反控**：只断言「翻译后含 END」时，**恒等实现**（`return d`）
   照样全绿 ⇒ 必须同时断言「不翻译就**不含** END」⇒ 恒等体当场变红（M2）。
   这就是为什么 G1 是**两条断言**，不是一条。

④ 🚨 **`width_relationship` 的守卫必须成对**：同一函数、两组合成输入、
   **相反方向**。只钉「窄」那一支时，把 `<` 翻成 `>` 会让「窄」那一支**仍然红**
   （它会输出「宽」）—— **看起来守住了**，而真实数据那一支已彻底反号。
   ⇒ 少任何一条，`W_is_narrower` 就是一个**可以整体取反的量**。

⑤ 🚨 **断言里不许有「我算了一遍」的数**。本文件当场栽了两次，
   **两次红的方向还不一样**：
   - 第一版把 `0.7067 / 0.0232` 手打成 `30.44`（真值 `30.46`）
     ⇒ 第 109 层「核对时用公式重算」；
   - 第二版断言「对调输入后倍数该取倒数」⇒ **当场红，而生产是对的**
     （「W 比 V 窄 30.46 倍」与「W 比 V 宽 30.46 倍」描述同一件事）。
   ⇒ 修法：倍数由 `Fraction` **精确有理数**现算（与生产的 `float` 除法**不同源**），
     并把「对调时倍数不变、只有方向词变」**写成断言**，免得后人去改对的东西。

⚠️ **覆盖缺口（诚实边界）**：本文件覆盖三个模块级判据
   （`axis_end_exclusive` / `nan_agrees` / `width_relationship`）、
   `axis_end_exclusive` 的**调用点接线**（G3 用 AST）、
   以及 `width_text` 的**三处喂入点**（AST 数 `Name` 节点，≥3）。
   `_run()` 里**其余**的编排（哪一天算 `full[d]`、`n_gt`/`n_lt` 怎么数、
   `full[d]` 有效票数够不够）**不在覆盖范围内** ——
   断言它们只能去读产物，而读快照的守卫**等价于没守**
   （第 96 层：改了源码不重跑照样绿）。
   ⚠️ 其中「`full[d]` 每日有效票数的 min/median」是**产物自报**的，
   探针自己会喊「若 min 远小于 5460 则本量不可比」（预注册 §5 第 2 条），
   那是**运行期喊**，不是这里的守卫。
   🚨 **产物上的散文至今仍写着「比 V 宽」**（`data/reports/cap_exposure_sigma.json`
   是改动前跑的）⇒ 本文件对那份产物**零覆盖**，重跑才作废。
"""
import ast
import datetime
import importlib.util
import inspect
from fractions import Fraction
from pathlib import Path

import pandas as pd
import pytest

from aplus.panel import window_dates

_SCRIPT = (Path(__file__).resolve().parent.parent / "scripts"
           / "cap_exposure_sigma_probe.py")


def _mod():
    spec = importlib.util.spec_from_file_location("_cap_exp_sigma_probe", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()

# 🚨 末端刻意取**周五**，而它的「下一个日历日」周六**不在并集轴上**
#    ⇒ 这条夹具同时钉住「加一个日历日」与「不必是下一个交易日」两件事。
_END = "2026-10-09"
_NEXT_AXIS_DATE = "2026-11-02"   # 轴上 END 之后的**下一个**日子，隔了 3 周


def _fake_map() -> dict:
    """最小可用的 `daily_map`：`market_date_axis` 只读 `df["date"]`。"""
    days = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
            _END, _NEXT_AXIS_DATE]
    df = pd.DataFrame({"date": pd.to_datetime(days)})
    return {"000001.SZ": df, "600000.SH": df.copy()}


# ── ① 语义：由**生产真函数** `window_dates` 作外部锚 ──────────────

def test_translating_the_end_is_exactly_what_puts_the_last_day_back_on_the_axis():
    """翻译后轴的末端 == 产物自报的闭区间末端；**不翻译就回不来**。

    ★ 后半句是**反控**：没有它，`axis_end_exclusive` 退化成恒等函数
      （`return d`）时本守卫照样全绿 —— 那是一条**恒真守卫**，
      而它守的恰好是「少一天」这个已经真发生过的缺陷。
    """
    fake = _fake_map()
    # 🚨 `market_date_axis` 的元素是 **带时分秒的字符串**（`'2026-10-09 00:00:00'`），
    #   不是 `'2026-10-09'`。第一版直接拿元素和 `_END` 比，当场红；
    #   更贵的是第二句 —— `'2026-10-09' in [...]` **恒为假**（元素带后缀）
    #   ⇒ **那条反控在恒等实现下照样通过 ⇒ 它一条变异都杀不掉**。
    #   ⇒ 所有日期比较一律先 `[:10]` 归到「日」。
    def days(end_arg: str) -> list:
        return [d[:10] for d in window_dates(fake, "2026-01-01", end_arg)]

    translated = days(M.axis_end_exclusive(_END))
    raw = days(_END)

    assert translated[-1] == _END, "翻译后末端必须是产物自报的那一天"
    assert raw[-1] != _END, "生产 `window_dates` 是半开 ⇒ 不翻译必然丢掉末端"
    assert _END not in raw, "丢掉的是**那一天本身**，不是它前面某天"


def test_the_translation_adds_one_calendar_day_and_that_day_need_not_trade():
    """加的是**一个日历日**，不是「下一个交易日」。

    为什么这条值得单独钉：docstring 承诺的是「多出来的非交易日
    不在并集轴上，天然被跳过」—— 而**「天然被跳过」这件事本身要靠
    `window_dates` 的 `d < end` 真跑一遍才成立**，不是靠注释。
    """
    got = M.axis_end_exclusive(_END)
    want = (datetime.date.fromisoformat(_END) + datetime.timedelta(days=1)).isoformat()

    assert got == want
    assert got == "2026-10-10", "10-09 是周五 ⇒ 下一个日历日是周六 10-10"
    assert got not in _fake_map(), (
        f"{got} 刻意**不在**并集轴上（轴上 END 之后是 {_NEXT_AXIS_DATE}）"
        " ⇒ 若这条红了，说明夹具不再覆盖「非交易日那半天开区间」这个前提")


# ── ② 接线：AST 读**调用点的实参**，不读全文子串 ──────────────────

def _end_slot():
    """`end` 在**真函数签名**里的位置（`None` = 它是 keyword-only）。

    🚨 第一版把下标 **`2` 硬编码**在探测器里。它今天对，只因为
    `window_dates(daily_map, start, end)` 恰好是三参数 ——
    而「恰好」是运气不是契约：哪天签名变成四参数，探测器会去读
    第 4 个实参，于是**在错的槽上判**。
    ⇒ 下标从**函数本身**取，签名变则探测器跟着变。
    """
    params = list(inspect.signature(window_dates).parameters.values())
    end = params[[p.name for p in params].index("end")]
    if end.kind is inspect.Parameter.KEYWORD_ONLY:
        return None
    return params.index(end)


_END_POS = _end_slot()


def _window_dates_end_is_translated(src: str) -> bool:
    """探测器：**每一处** `window_dates(...)` 调用的 `end` 实参
    都必须包在 `axis_end_exclusive(...)` 里。

    ⚠️ 为什么不写 `assert "axis_end_exclusive(w_end)" in src`：
       本文件的模块 docstring、以及 `axis_end_exclusive` 自己的 docstring
       **都在讲这件事**，子串断言会被它们满足（第 13 层 / 第 79 层：
       只钉存在不钉性质、只钉文本不钉语法位置）。
    ⚠️ 用 `>= 1` 而不是「恰好一处」：将来若新增第二处调用，
       本探测器会要求**它也**翻译，而不是默默放过。
    """
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "window_dates"]
    if not calls:
        return False
    for call in calls:
        arg = None
        if _END_POS is not None and len(call.args) > _END_POS:
            arg = call.args[_END_POS]
        for kw in call.keywords:
            if kw.arg == "end":
                arg = kw.value
        if arg is None:
            return False
        if not (isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "axis_end_exclusive"):
            return False
    return True


def test_the_probe_call_site_translates_before_handing_the_end_to_window_dates():
    """🔌 接线守卫：**变异 M1 唯一的杀手**（把调用点的翻译摘掉）。

    ⚠️ 它守的是**接线**而不是函数行为：改 `axis_end_exclusive` 的函数体
      它不会红（那是 M2 的活），改调用点的实参它才红。
      两者必须都有独立证据，否则其中一条是装饰。
    """
    assert _window_dates_end_is_translated(_SCRIPT.read_text(encoding="utf-8"))


def test_that_wiring_detector_is_not_vacuous():
    """⚠️ **探测器的正控**：探测器自己失效时外观与「守住了」逐位相同。

    没有这一条，`_window_dates_end_is_translated` 改坏（正则写错 /
    名字拼错 / 提前 return True）会让 G3 **永远绿**，
    而全仓门禁只会印一行 `1 passed`（第 120 层 / 第 165 层）。
    """
    # 🚨 三个夹具**必须照真签名写三个位置实参**（`daily_map, start, end`）。
    #   第一版我写成四个（`store, files, w_start, w_end`）⇒ `args[2]` 落在
    #   `w_start` 上 ⇒ 正控被判否 ⇒ **我以为是探测器坏了，其实是构造错了**
    #   （第 141 层：「先怀疑构造，再怀疑被测物」）。
    bad = (
        "def f(w_start, w_end):\n"
        "    return window_dates(daily_map, w_start, w_end)\n"
    )
    assert not _window_dates_end_is_translated(bad), "无翻译 ⇒ 必须判否"

    gone = "def f(w_start, w_end):\n    return axis_end_exclusive(w_end)\n"
    assert not _window_dates_end_is_translated(gone), "调用点整个消失 ⇒ 必须判否"

    good = (
        "def f(w_start, w_end):\n"
        "    return window_dates(daily_map, w_start,\n"
        "                        axis_end_exclusive(w_end))\n"
    )
    assert _window_dates_end_is_translated(good), "有翻译 ⇒ 必须判是"

    # ★ 这条是**槽位**的反控，也是 `_END_POS` 存在的唯一理由：
    #   翻译出现在 `start` 槽上、`end` 槽是裸的 ⇒ 必须判否。
    #   若下标读错（例如退化成「文件里有没有 axis_end_exclusive」），
    #   这一条**当场变红** —— 否则 `good` 与 `shifted` 只差一个位置。
    shifted = (
        "def f(w_start, w_end):\n"
        "    return window_dates(daily_map, axis_end_exclusive(w_start), w_end)\n"
    )
    assert not _window_dates_end_is_translated(shifted), (
        "翻译在 `start` 槽 ⇒ `end` 槽没翻译 ⇒ 必须判否。"
        "判成是，说明下标读错了槽（探测器退化成了「文件里出现过就算」）")


# ── ③ 一条「不许把口径差说成口径对」的守卫 ──────────────────────────

def test_the_probe_does_not_itself_reject_its_own_bug_by_widening_the_check():
    """🚨 钉住**修复方向**：上一次那笔 bug 的正确修法是**翻译约定**，
    错误修法是**放宽 `n_days` 交叉核对**。

    为什么这条值钱：放宽交叉核对的那一版**也能让 rc=3 消失**，
    而症状与「修好了」逐位相同 —— 唯一区别是探针从此
    可以在**少一天的轴上**静默出一个判决（第 7 层）。
    ⇒ 本守卫让那种修法**当场变红**，把「它为什么错」写进断言里。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)

    checks = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare)
                and any(isinstance(c, ast.Name) and c.id == "art_days"
                        for c in node.comparators)):
            checks.append(node)

    assert checks, "找不到 `… != art_days` 的交叉核对 ⇒ 它被删了"
    for node in checks:
        # 🚨 不许出现任何非零容差：`!=` 换成 `abs(...) > tol` 形态的放宽
        assert not any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                       and c.value != 0
                       for c in ast.walk(node)), (
            "交叉核对里出现了非零字面量 ⇒ 有没有被放宽成容差判据？"
            "正确修法是翻译闭/半开约定，不是放宽天数核对")


@pytest.mark.parametrize("day", ["2021-01-04", "2026-10-09", "2026-12-31"])
def test_the_translation_is_defined_on_any_iso_day_not_just_the_one_it_hit(day):
    """合成臂：`fromisoformat` + 一个日历日，对任意合法 ISO 日都有定义。

    ⚠️ 存在的理由：**真实触发那次 bug 的只有 END 那一格**（2026-10-09），
      而一条只在那格里为真的守卫等于把观测冻结成契约（第 141 层）。
    """
    got = M.axis_end_exclusive(day)
    assert datetime.date.fromisoformat(got) == (
        datetime.date.fromisoformat(day) + datetime.timedelta(days=1))


# ── ④ NaN 一致性判据：模块级 `nan_agrees` ──────────────────────────
#
# 🚨 这条判据原先**内联在 `_run()` 的逐票读循环里** ⇒ 守卫够不着 ⇒
#    「放宽成 else True」对全部守卫不可见（第 181 层）。
#    提取到模块级是**为了让判据可问**，不是为了可读。

def test_two_nans_agree_even_though_nan_is_not_equal_to_itself():
    """双侧 NaN ⇒ **一致**（`nan != nan` 是 IEEE-754，不是判据）。

    这格就是首跑 rc=4 打中的那一格：`301181.SZ / 2021-01-04`，
    而该票首根观测是 2022-02-21 ⇒ 两侧都在正确地说「这天它还没有市值」。
    """
    assert not (float("nan") == float("nan")), (
        "夹具前提：若 NaN == NaN 恒真，本守卫测的东西已不存在")
    assert M.nan_agrees(float("nan"), float("nan")) is True


def test_one_sided_nan_against_a_finite_value_is_not_agreement():
    """🚨 **变异 M3 唯一的杀手**：把 `isnan(a) and isnan(b)` 放宽成 `True`。

    ⚠️ 方向性：这条**只**放宽单侧 NaN。放宽它等于宣称
      「探针在这一格什么都读不出来，也算口径一致」——
      而那正是首跑 rc=4 想排除的那类假红。
    """
    assert M.nan_agrees(float("nan"), 1.5) is False
    assert M.nan_agrees(1.5, float("nan")) is False
    assert M.nan_agrees(float("nan"), float("inf")) is False
    assert M.nan_agrees(float("-inf"), float("nan")) is False


def test_finite_values_must_be_bit_identical():
    """双侧有限 ⇒ 逐位相等（唯一收紧到 `==` 的一支）。

    ⚠️ 刻意**不**用 `approx`：这里判的是「是不是同一格同一个值」，
      而 `approx` 会把「差 1e-9」判成一致 —— 那正是口径被悄悄换掉的样子。
    """
    assert M.nan_agrees(1.5, 1.5) is True
    assert M.nan_agrees(0.0, 0.0) is True
    assert M.nan_agrees(1.5, 1.5000000000000002) is False
    assert M.nan_agrees(0.0, 1e-300) is False


# ── ⑤ 宽度方向**必须由数算出来**（这是产物自相矛盾的直接修法）─────
#
# 🚨 起因：产物 `arm_W.shape`、docstring、段注释、打印标签**四处**
#    都写着「比 V 宽 ⇒ 保守方向」，而实测 W 明显**更窄**。那是**手打的方向词**
#    放在了**算出来的数**旁边。
# ⚠️ **跨度口径必须带标签**（第 7 层，两个各自正确的数不是一个量）。这里有**三行**，
#    不是两行；三行各有各的证法，**不许互相顶替**：
#
#    | 口径                        | 臂 V          | 臂 W          | V/W    |
#    |-----------------------------|---------------|---------------|--------|
#    | **p97.5 − p2.5**（产物）     | 0.38988538…   | 0.01381944…   | 28.21  |
#    | **min − max**（产物真值）    | 0.70669026…   | 0.02319188…   | 30.47  |
#    | **min − max**（守卫四位小数）| 0.7067        | 0.0232        | 30.46  |
#
#    ⇒ 三行**方向完全一致**（W 更窄），**倍数各不相同** ⇒ **三个量**。
#    ⚠️ 第三行是**同一个 min−max 口径的四位小数写法**：守卫喂的数**不是**产物里的数
#      （0.0336% 相对差），它守的是 `width_relationship` **这个函数**（与跨度口径无关），
#      **不是**产物里那个 28.21，**也不是** 30.47。
# ⇒ 修法不是改句子，是**让方向成为 `width_relationship` 的输出**。

def test_the_width_direction_follows_the_numbers_in_both_directions():
    """同一个函数、两组合成输入、**相反**的方向。

    ⚠️ **必须成对**：只钉「窄」的那一支时，把 `<` 翻成 `>` 会让
      「窄」那一支**仍然红**（因为它会输出「宽」）——看起来守住了；
      而真实数据那一支已经彻底反号。
      ⇒ 两条断言缺任何一条，`W_is_narrower` 就是一个**可以整体取反的量**。
    """
    narrow, narrow_text = M.width_relationship(span_v=1.0, span_w=0.1)
    assert narrow is True, "W 明显更窄 ⇒ 必须判窄"
    assert "窄" in narrow_text and "10.00 倍" in narrow_text

    wide, wide_text = M.width_relationship(span_v=0.1, span_w=1.0)
    assert wide is False, "W 明显更宽 ⇒ 必须判宽"
    assert "宽" in wide_text and "10.00 倍" in wide_text

    equal, equal_text = M.width_relationship(span_v=0.5, span_w=0.5)
    assert equal is False, "等宽**不是**「更窄」⇒ `<` 必须严格小于"


def test_the_width_text_is_derived_not_hand_typed_():
    """方向词不许是**手打的** —— 文案里必须带着**算出来的倍数**。

    ⚠️ 这条守的是**为什么当初会反号**：方向词与数字同处一个字符串里，
      而方向词是人写的、数字是机器算的，两者可以各说各话
      （「键名/文案内建价值判断 ⇒ 与内容互相打脸」）。

    🚨 **本守卫的第一版把自己写死了一个手算的商 `30.44`，当场红**
      （真值 `30.46`）⇒ 而那正是它要防的形状的第 109 层版本：
      **把「算了一遍」的结果当常数钉进断言**。
      ⇒ 修法不是把 `30.44` 改成 `30.46`（那是把化石换个数字接着立），
        是**让断言自己算**，且**换一条推导路径**：
        生产用 `float.__truediv__`，本守卫用 `Fraction` 的**精确有理数**
        （不经过 IEEE-754 舍入）⇒ 两条路径不同源。
      ⇒ 这也意味着：**这里不存在任何手打的倍数常量可供腐烂。**
    """
    span_v, span_w = 0.7067, 0.0232
    _, text = M.width_relationship(span_v=span_v, span_w=span_w)

    # ① 两个实测宽度必须**逐字**出现在文案里 —— 手打的宽度立刻被抓
    assert "0.7067" in text and "0.0232" in text, (
        f"文案必须复述两个实测宽度，否则读者拿不到它凭什么这么说：{text}")

    # ② 倍数由**精确有理数**算（不同推导路径），不许手打
    exact = Fraction("0.7067") / Fraction("0.0232")
    assert f"{float(exact):.2f} 倍" in text, (
        f"文案里的倍数必须由两个宽度现算，精确有理数给的是 "
        f"{float(exact):.2f} 倍，而文案写的是：{text}")

    # ③ 反向：把两个宽度对调 ⇒ 方向词必须翻面
    # ⚠️ 方向词的判别**必须认加粗标记 `**窄**` / `**宽**`**：
    #   文案里还有一处不带加粗的「实测 **W 宽** …」⇒ 裸子串 `"宽" in text`
    #   在两个方向上**都为真** ⇒ 那是一条恒真的断言（第 112 层）。
    # 🚨 **倍数在对调后不该变**（第一版这里写错了，当场红）：
    #   「W 比 V 窄 30.46 倍」与「W 比 V 宽 30.46 倍」描述**同一件事**，
    #   只是站位不同（W 是 V 的 1/30.46 ⇔ W 是 V 的 30.46 倍）。
    #   ⇒ 对调输入时**只有方向词该变**，倍数必须保持不变；
    #   把它写成「倍数取倒数」会逼着后人去改一段本来正确的代码。
    is_narrow, _ = M.width_relationship(span_v=span_v, span_w=span_w)
    is_narrow_flipped, flipped = M.width_relationship(
        span_v=span_w, span_w=span_v)
    assert is_narrow is True and is_narrow_flipped is False
    assert "**窄**" in text and "**宽**" not in text, (
        f"方向词必须只在加粗处出现，否则裸子串判据恒真：{text}")
    assert "**宽**" in flipped and "**窄**" not in flipped, (
        f"对调输入后方向词必须翻面：{flipped}")
    assert f"{float(Fraction('0.7067') / Fraction('0.0232')):.2f} 倍" in flipped, (
        f"对调输入后倍数必须**保持不变**（同一个比值从对面读）：{flipped}")


def test_the_probe_source_carries_no_hand_typed_width_claim():
    """🚨 **AST 层**：源码里不许再出现**断言宽度方向**的字面量。

    ⚠️ 为什么不写 `assert "比 V 宽" not in src`：
       子串断言会被**本文件自己的注释与 docstring 的取证段落**满足
       （它们逐字引用了旧文案来记录它错在哪）⇒ 第 13 层。
    ⇒ 改判 AST：方向词只能出现在**字符串字面量**里，
      而本条要求源码里那些字符串**是被 `width_text` 拼进去的**——
      即产物/打印层的 `shape` 与打印必须走 `width_text` 这一个出口。
    """
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    fed_by_width_text = []
    for node in ast.walk(tree):
        # 产物里 `"shape": (... + width_text)` 与打印里的 `width_text`
        if isinstance(node, ast.Name) and node.id == "width_text":
            fed_by_width_text.append(node)
    assert fed_by_width_text, (
        "源码里找不到 `width_text` ⇒ 宽度方向又回到手打字符串了")
    assert len(fed_by_width_text) >= 3, (
        f"只有 {len(fed_by_width_text)} 处喂 `width_text`；"
        "预期至少三处（产物 arm_W.shape / 产物 arm_width_comparison.text / 打印层）")

