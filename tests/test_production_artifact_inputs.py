"""★ 第 132 层：**测试把自己的输出写到了生产产物路径**，而新加的章替它洗成了合法。

## 发生了什么（2026-10-15 实测，不是推理）

`scripts/verdict_composite.py:89` 的 `--out` 默认值是**相对路径**
`data/reports/verdict_composite.json`，相对 **cwd** 解析。
`tests/test_verdict_composite.py` 里两条测试用
`_synthetic_table()`（**随机数造的 parquet**）跑这个脚本而**不传 `--out`**；
pytest 的 cwd 就是仓库根 ⇒ 默认值精确落在生产产物路径上。

比特级时间线：

```
11:33:6x  门禁 pytest 开跑（370.64s）
11:40:02  data/reports/verdict_composite.json        ← 被测试写掉
11:40:02  data/reports/verdict_composite.json.provenance.json  ← 章也重盖了
11:40:12  门禁结束（.testtmp/gate131.log 的 mtime）
```

产物里的 `config.matrices` 字面写着

```
/private/var/folders/…/pytest-of-airboom/pytest-2330/
test_the_control_arm_is_restri0/mat.parquet
```

——`pytest-2330` 是那次门禁的临时目录编号，
`test_the_control_arm_is_restri0` 就是**被点名的那条测试**。

## 最刺眼的一层：**章替它洗成了合法**

`provenance.stamp()` 照章不误地盖了章，章里写

```
data_fp = 5460:1792002326.672246      ← 真数据根 data/master/daily
data_dir = data/master/daily
```

而这一跑读的是 `/tmp/.../mat.parquet`。**章的 `data_fp` 取自固定的
数据目录，与 `--matrices` 这次实际喂进来的东西毫无关系。**
⇒ `freshness()` 判 **`fresh` + 字节证据**，因为它能查的三样全是真的：
生成器字节没变、有 `data_fp`、有章。

⇒ **失效方向是最坏的那一种：假 fresh。** 它不会自己暴露，
只会让后来每一次复核都「确认」一份合成数据。

⇒ 污染版本**已经进了 git**（`8e4b327` 把改动一起扫进去了）。

## 结论没被破坏，被破坏的是证据

用真抽取表重跑，第 127 层写在 CLAUDE.md 里那组数字**逐位复现**：

| 量 | 第 127 层 | 真跑 |
|---|---|---|
| 判决年 | 6 | 6 |
| 朴素翻号过线 | 6/6 | `sign_arm_failures = 0` |
| 最优定权年均优势 | +0.005011 | `0.005011665358819271` |
| 占门槛 | 36.1% | `0.3608082740994693` |
| 占优年数 | 5/6（2024 略输） | 正 5 年，2024 = `−0.0013019` |

⇒ 所以这不是「结论错了」，是**正确结论的证据被静默换成了假的**。
后果比重算一次结论严重：它保证任何后续复核都确认错的那一份。

## 通则

1. **治「结果只印不落」的 `--out` 可以造出「落错地方」**：
   修复动作本身成了污染的帮凶，因为它盖的章**不含输入路径**。
2. **章不是万能的**：它能查「谁写的、多旧、数据根在哪」，
   查不了「这一跑读了什么输入」——**这半边必须由产物自己声明**。
3. **判据不许挂在人的记忆上**：修法不是「在调用点记得传 `--out`」
   （下一个新测试就会漏），注入点必须上移到调用**发生**的地方。

## 两条守卫

- **A（事后可查）**：已登记产物声明的**文件系统输入**必须落在仓库内。
  按键名窄名单查，**不做全文扫描**——`evolution_ledger.json` 的
  `evidence: "/tmp/aplus_snap_final/…"` 是**历史证据出处**（自由文本），
  全文扫会误伤。
- **B（当场可抓）**：把写者重跑一遍，看已登记产物的字节有没有变。
  A 是「事后审计」，B 是「当场抓现行」，两个半边都覆盖。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

from aplus.provenance import DEFAULT_GENERATORS

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REPORTS = _ROOT / "data" / "reports"

# ⚠️ 这个名单必须**窄**。放宽到 `source` / `*_source` 就会去标
# `caliber.t_crit_source = "scripts/factor_attribution.T_CRIT_5PCT"`——
# 那是**常数出处**（方法引用），不是文件系统输入。
# 第 112 层：过严的守卫和恒真的一样有害，它会一直红，人就把它关掉。
# ⇒ 这里只收「这一跑的输入是一个目录/文件」这个语义。
_INPUT_KEYS = frozenset({
    "matrices", "data_root", "data_dir", "inputs", "input",
    "panel_root", "db", "calendar",
})


def _declared_inputs(obj, trail=""):
    """产出 (键路径, 值) —— 只收白名单里的键，且值是绝对路径样子的字符串。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _declared_inputs(v, f"{trail}.{k}" if trail else k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _declared_inputs(v, f"{trail}[]")
    elif isinstance(obj, str):
        leaf = trail.split(".")[-1].replace("[]", "")
        if leaf in _INPUT_KEYS and obj.startswith("/"):
            yield trail, obj


def _outside_repo(value: str) -> bool:
    """输入路径必须**在仓库内**。相对路径（`data/master/daily`）不算输入声明。"""
    try:
        resolved = pathlib.Path(value).resolve()
    except (OSError, ValueError):
        return True          # 解析不了 ⇒ 判不可引用（保守方向）
    try:
        resolved.relative_to(_ROOT)
    except ValueError:
        return True
    return False


# ── A：已登记产物声明的输入不许落在仓库外 ──────────────────────────

def test_no_registered_artifact_declares_an_input_outside_the_repo():
    """★ 事后可查的那一半。

    2026-10-15 实测抓到两份：`verdict_composite.json` 的
    `config.matrices`（pytest 临时目录）与 `spread_tilted_arm.json` 的
    `data_root`（`/tmp/aplus_snap_1015`——macOS 会清 /tmp，
    那份产物**不可复现**，第 127 层的规矩下即不可引用）。
    """
    bad = []
    for name in sorted(DEFAULT_GENERATORS):
        p = _REPORTS / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        for trail, value in _declared_inputs(data):
            if _outside_repo(value):
                bad.append(f"{name} 的 {trail} = {value}")
    assert not bad, (
        "🚨 这些**已登记产物**声明的输入落在仓库外 —— 不可复现即不可引用：\n  "
        + "\n  ".join(bad)
        + "\n\n"
        "这正是 `verdict_composite.json` 被 pytest 的合成数据顶掉时的状态：\n"
        "  产物是 `fresh`（章的 data_fp 指向真数据根），而 `config.matrices`\n"
        "  字面写着 pytest 的临时目录。\n"
        "⇒ 重跑真数据；测试不许写生产路径（见 B）。"
    )


def test_the_input_scanner_actually_detects_a_temp_path_and_spares_a_real_one():
    """反控：探测器自己要被守卫（第 131 层「正控要验判决不只验探测器」）。

    没有这一条，一个恒真的探测器会让上面那条**永远绿**，
    而绿得毫无意义——症状与「守对了」逐位同形。
    """
    tmp = "/private/var/folders/x/pytest-of-me/pytest-99/test_foo0/mat.parquet"
    hit = [v for _, v in _declared_inputs({"config": {"matrices": tmp}})]
    assert hit == [tmp], f"探测器认不出临时路径：{hit}"

    inside = str(_REPORTS / "ic_matrices.parquet")
    # ⚠️ 判决在 `_outside_repo()`，不在探测器上（第 131 层「正控要验判决
    # 不只验探测器」）。第一版把控制钉在探测器上，当场抓到了我自己：
    # `_declared_inputs` 的职责只是**认出这是个输入声明**，
    # 「它在不在仓库外」是另一件事。
    hit = [v for _, v in _declared_inputs({"config": {"matrices": inside}})]
    assert hit == [inside], f"探测器认不出仓库内的输入声明：{hit}"
    assert not _outside_repo(inside), f"判决把仓库内路径判成仓库外（过严）：{inside}"
    assert _outside_repo(tmp), f"判决认不出仓库外的输入：{tmp}"

    # 🚨 自由文本里的临时路径**不许**算输入声明：
    # `evolution_ledger.json` 的 `evidence` 记的是历史证据的出处。
    hit = [v for _, v in _declared_inputs({"evidence": tmp})]
    assert not hit, f"把证据出处当成了输入声明（过严）：{hit}"


# ── B：当场抓现行 ────────────────────────────────────────────────

@pytest.mark.parametrize("rel", ["tests/test_verdict_composite.py"])
def test_running_the_synthetic_arm_tests_leaves_every_registered_artifact_untouched(rel):
    """★ 当场能抓的那一半。

    上面 A 是事后审计；这条是**重跑写者、看字节变没变**——
    污染发生的那一刻它就红，不必等下一次人工翻产物。

    2026-10-15 的死法：`test_the_control_arm_is_restricted_to_the_same_
    years_as_the_main_arm` 不传 `--out`，于是合成结果落到了
    `data/reports/verdict_composite.json`，章也跟着重盖，
    `freshness()` 判 fresh。B 在这里是红的。
    """
    targets = [n for n in sorted(DEFAULT_GENERATORS) if (_REPORTS / n).exists()]
    before = {n: hashlib.sha256((_REPORTS / n).read_bytes()).hexdigest() for n in targets}

    try:
        r = subprocess.run([sys.executable, "-m", "pytest", rel, "-q"],
                           cwd=_ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        # ⚠️ 超时**不许** skip（第 130 层：异常路径必须和正常路径一样有守卫）。
        # 「没法验证」与「没问题」在输出里长得一模一样。
        pytest.fail(
            f"跑 {rel} 超时 600s ⇒ 本条守卫**未得出判决**。\n"
            "「验不出来」和「验过了没问题」必须能区分开："
            "这里取保守方向，判红。"
        )

    assert r.returncode == 0, (
        f"{rel} 自己没跑绿（rc={r.returncode}），"
        "先修它再谈它有没有改写产物：\n" + r.stdout[-2000:]
    )

    changed = [n for n in targets
               if hashlib.sha256((_REPORTS / n).read_bytes()).hexdigest() != before[n]]
    assert not changed, (
        f"🚨 跑 {rel} 之后这些已登记产物的字节变了：{changed}\n"
        "⇒ **测试把自己的输出写进了生产产物路径**。\n"
        "根因：脚本的 `--out` 默认值是相对 cwd 的生产路径，"
        "而 pytest 的 cwd 就是仓库根。\n"
        "修法：把 `--out` 的注入点放在**调用发生的地方**"
        "（`tests/test_verdict_composite.py::_run`），"
        "不许靠「每个调用点记得传」（下一个新测试就会漏）。"
    )
