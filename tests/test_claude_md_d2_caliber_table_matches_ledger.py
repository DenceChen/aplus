"""CLAUDE.md 的 **D2 口径台账**必须与现役台账产物**逐格相等**。

## 为什么这张表需要一条守卫（而「回本本金」那张表已经有一条）

CLAUDE.md 里那张「所需毛超额 / 门槛」的口径台账**抄**了台账产物的数字。
⇒ **抄进散文 ⇒ 会静默过期**（第 73 层）⇒ 而它**真的**过期过：

| 抄进去的（K=21 时代） | 现役产物（K=63） |
|---|---|
| 所需毛超额·∞本金·等权 **13.9304%** | **10.7391%**（字段名也从 `..._infinite_capital` 改成了 `..._infinite_capital_best`） |
| 所需毛超额·5万·等权 **15.5571%** | **11.2813%** |
| 缺口·5万·等权 **11.6559pp** | **7.3800pp** |

⇒ 根因：台账在 `2e6ab39` 从 K=21 换成 K=63，**引用它的地方没跟上**
（第 143 层：「先建行再改散落引用 —— 表格是唯一会被查的地方，散文不是」）。
⇒ 而 `tests/test_claude_md_capital_table_matches_artifact.py` 只管**回本本金**那张表
⇒ **这张表当时零覆盖**。

## 这条守卫钉住什么

1. 表里三行的数字**逐位等于** `d2_signal_requirement.json` 的对应字段（取 4 位小数）。
2. 每行的「载体」格必须**点名那个字段** ⇒ 字段改名 ⇒ 守卫会红
   （否则数字对了、路径错了，读者照样找不到）。
3. **判据要钉行头，不许钉数字** ⇒ 锚在**行标签**上（「找到」≠「找对了」）。

⚠️ **不许**用「全文里有没有这个数字」当判据（那会被注释/历史散文满足）。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
LEDGER = ROOT / "data" / "reports" / "d2_signal_requirement.json"

#: 表里每行的**行标签** ⇒ (产物 JSON 路径, 单位)
#: ⚠️ 行标签是**手打的**（表格的行头）⇒ 守卫靠它定位；产物路径也手打 ⇒
#: 字段改名时守卫会红 ⇒ 那正是要的效果。
ROWS = (
    ("所需毛超额·∞本金·等权",
     ("headline", "required_gross_at_infinite_capital_best"), "%"),
    ("所需毛超额·5万·等权",
     ("headline", "required_gross_at_50k_deliverable"), "%"),
    ("缺口·5万·等权",
     ("headline", "account_annual_ceiling_pct",
      "gap_to_target_at_deliverable_width_and_50k_pp"), "pp"),
)

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_rows(text: str) -> dict[str, list[str]]:
    """行标签 → 整行的格子。**只认以 `|` 开头且有足够格子的行。**"""
    out = {}
    for ln in text.splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = _cells(ln)
        if len(cells) < 3:
            continue
        label = cells[0].replace("**", "").strip()
        if label:
            out.setdefault(label, cells)
    return out


def _dig(obj, path):
    cur = obj
    for p in path:
        cur = cur[p]
    return cur


def _number_in(cell: str) -> float:
    m = _NUM.search(cell.replace("**", ""))
    assert m, f"这一格里没有数：{cell!r}"
    return float(m.group(0))


def test_the_ledger_artifact_is_readable_and_has_every_field_the_table_names():
    """先确认**产物侧**读得到（缺档 ⇒ 守卫会变成「什么都没测」）。"""
    assert LEDGER.exists(), f"台账产物不在盘上：{LEDGER}"
    art = json.loads(LEDGER.read_text(encoding="utf-8"))
    for label, path, _unit in ROWS:
        value = _dig(art, path)
        assert isinstance(value, (int, float)), (
            f"「{label}」的产物字段 {path} 读不出数（{value!r}）⇒ "
            f"表里的那个数已经**无处可对**")


def test_every_d2_caliber_row_in_claude_md_matches_the_ledger_cell_by_cell():
    """📋 表里那三行的数字**逐位等于**现役台账产物。"""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    art = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = _table_rows(text)

    missing = [label for label, _p, _u in ROWS if label not in rows]
    assert not missing, (
        f"CLAUDE.md 的口径台账里找不到这些行：{missing} ⇒ "
        f"要么行名改了，要么这张表被删了 —— 两种都要人来裁决")

    for label, path, unit in ROWS:
        cells = rows[label]
        got = _number_in(cells[1])
        want = float(_dig(art, path))
        assert abs(got - round(want, 4)) < 1e-12, (
            f"「{label}」：CLAUDE.md 写 {got}{unit}，产物是 {want}{unit} ⇒ "
            f"**散文里的数已过期**（第 73 层：抄进散文 ⇒ 静默过期）。\n"
            f"  修法：把这一格改成 {round(want, 4):.4f}{unit}（数值从产物读，"
            f"不许手抄）。")
        assert unit in cells[1], (
            f"「{label}」这一格缺单位（应带 {unit}）⇒ 读的人得自己猜量纲")


def test_every_d2_caliber_row_names_the_field_it_was_copied_from():
    """载体格必须**点名产物字段** ⇒ 字段改名时守卫会红。

    ⇒ 否则会出现「数字对了、路径错了」：读者按旧路径去读，读不到，
    而表里的数看起来完全正常。
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    rows = _table_rows(text)
    for label, path, _unit in ROWS:
        cells = rows[label]
        carrier = " ".join(cells[2:])
        assert path[-1] in carrier, (
            f"「{label}」的载体格没有点名产物字段 `{path[-1]}` ⇒ "
            f"字段改名后这张表会指向一个**不存在**的字段（而且没人会喊）。\n"
            f"  载体格现在写着：{carrier[:200]}")


def test_the_mismatch_detector_has_teeth():
    """⚠️ 正控：把表里的数改错 ⇒ 判据必须红。

    ⇒ 没有这一条，上面那条可能因为「匹配逻辑写错」而**恒绿**
    （第 112 层：过严的筛选会把 `all()` 退化成装饰）。
    """
    art = json.loads(LEDGER.read_text(encoding="utf-8"))
    label, path, unit = ROWS[1]
    want = float(_dig(art, path))

    good = f"| {label} | **{want:.4f}{unit}** | 说明 | `::headline.{path[-1]}` | 产物字段 |"
    bad = f"| {label} | **{want + 1.0:.4f}{unit}** | 说明 | `::headline.{path[-1]}` | 产物字段 |"

    assert abs(_number_in(_cells(good)[1]) - round(want, 4)) < 1e-12
    assert abs(_number_in(_cells(bad)[1]) - round(want, 4)) >= 1e-12
