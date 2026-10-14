"""持仓状态机：`due=False` 的日子**不得改写持仓**。

## 发现的缺陷（2026-10-14）

`scripts/run_daily.py` 的 `step_predict_and_allocate` 里：

```python
state.update({
    "as_of": today, "every": every, "buffer_multiple": buf,
    "up": trade_up, "down": trade_down,     # ← 无条件写当日名单
    ...
})
if due:
    state["days_since_rebalance"] = 0
else:
    state = advance(state, today)
```

`trade_up` 是**当日新名单**，不是持仓。非换仓日（`due=False`）照样写进
`state["up"]`，于是：

1. 状态文件里的「我持有什么」被每天的「今天该看什么」覆盖；
2. 下一轮 `held_up = prev.get("up")` 拿到的仍是最新名单，
   **不是上次的真实持仓**；
3. `buffer_hold(held_up, ranked_up, ...)` 的缓冲区判据
   于是永远对着最新名单算 —— **从不对真实持仓算**。

⇒ 「每天出名单、N 天换一次仓」这个产品形态**没有被真正执行**：
名单在发，缓冲在算，但持仓记忆是假的。

## 与 CLAUDE.md 第 6 条同族

第 6 条「看起来像开关，实际焊死」：改 `rebalance_every` 只会让
`is_due()` 少返回几次 True，而**持仓状态本身**是假的 ——
开关接到了定时器上，没接到事实上。

## 判据

**外部锚**：一个状态机跑过多天后，`state["up"]` 必须等于**最后一次
换仓日**的名单，而不是最新一天的名单。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RD = _ROOT / "scripts" / "run_daily.py"


def _step_fn() -> str:
    src = _RD.read_text(encoding="utf-8")
    m = re.search(r"^def step_predict_and_allocate\(.*?(?=^def |\Z)", src, re.S | re.M)
    assert m, "step_predict_and_allocate 不见了 —— 守卫比产品活得久"
    return m.group(0)


def test_holdings_must_not_be_overwritten_on_non_rebalance_days():
    """★ 核心守卫：非换仓日必须**沿用旧持仓**。

    ## 断言锚在哪里，为什么换过两次

    ① 第一版锚 `state.update(...)` 的实参 —— 产品把 update 改成逐行
       赋值后，守卫报「结构变了」。这**不是**产品有错，是锚点太脆。
    ② 锚到**真正的控制流**：给 `state` 写 `up`/`down` 的赋值语句，
       必须全部落在 `if due …` 分支里。

    不变量本身没变：**非换仓日不得写持仓**。
    换的是观察方式，不是标准。
    """
    body = _step_fn()
    tree = ast.parse(body)

    writes = []   # (所在 if 条件文本, 赋值目标)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        cond = ast.unparse(node.test)
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = [ast.unparse(t) for t in sub.targets]
            elif isinstance(sub, ast.AugAssign):
                targets = [ast.unparse(sub.target)]
            for tgt in targets:
                if re.match(r"^state\[?['\"](up|down)['\"]", tgt):
                    writes.append((cond, tgt))

    assert writes, (
        "找不到任何给 state['up']/state['down'] 的赋值 —— "
        "要么持仓根本不持久化（更糟），要么结构变了需要重新评估这条守卫"
    )
    unguarded = [(c, t) for c, t in writes if "due" not in c]
    assert not unguarded, (
        f"持仓赋值出现在非 due 条件下：{unguarded} ⇒ "
        "非换仓日也在改写持仓，缓冲区判据永远对着最新名单算（第 6 条：开关没焊到事实上）"
    )


def test_instruction_must_receive_the_real_holdings():
    """`instruction()` 拿到的 held 必须是真持仓，不是当日新名单。

    ⚠️ 第一版用正则抓 `instruction(...)` 的实参，跨行调用在第一个换行处
    就被截断，于是**正确的产品被判红**。
    ⇒ 改用 AST 读实参。解析器不会骗人，正则会。
    """
    body = _step_fn()
    tree = ast.parse(body)

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "instruction"
    ]
    assert calls, "找不到 instruction(...) 调用 —— 守卫需要重新评估"

    kwargs = {kw.arg: ast.unparse(kw.value) for call in calls for kw in call.keywords
              if kw.arg}
    assert kwargs.get("held_up") == "held_up", (
        f"instruction() 的 held_up 实参是 {kwargs.get('held_up')!r}，"
        "必须是 held_up（上一轮的真实持仓）"
    )
    assert kwargs.get("held_down") == "held_down", (
        f"instruction() 的 held_down 实参是 {kwargs.get('held_down')!r}，"
        "必须是 held_down"
    )
    # 持仓为空的首次建仓是合法的，但必须由 prev 决定，不能凭空来
    assert "held_up" in body and "prev" in body, (
        "held_up 必须来自持久化状态 prev，而不是当日现算"
    )


def test_buffer_hold_operates_on_the_real_holdings():
    """★ 缓冲区判据必须以**真实持仓**为输入。

    `buffer_hold(held, ranked, ...)` 里的 `held` 是整个缓冲区逻辑的地基。
    若它是当日新名单（因为上一轮把持仓覆盖了），
    「只换掉跌出缓冲区的那几只」就退化成「全换」——
    且**完全不报错**，因为代码路径完全正常。
    """
    body = _step_fn()
    for leg, held in [("trade_up", "held_up"), ("trade_down", "held_down")]:
        m = re.search(rf"{leg}\s*=\s*fill_to_top_n\(\s*buffer_hold\(\s*(\w+)", body)
        if m is None:
            continue  # 该腿没走缓冲区分支，跳过
        assert m.group(1) == held, (
            f"{leg} 的 buffer_hold 输入是 {m.group(1)!r}，"
            f"必须是 {held!r}（上一轮的持仓）而不是当日新名单"
        )


def test_state_file_records_which_day_the_holdings_were_set():
    """状态必须记下**最后一次换仓日**。

    ## 第二次被第 13 条咬到

    第一版写 `assert "held_since" in body`。变异验证时删掉**赋值那一行**，
    守卫**照样全绿** —— 因为同名的 `state.get('held_since', '?')`
    还在 `print` 里，子串断言被自己代码的另一处满足了。

    ⇒ 断言必须锚在**赋值语句**上（AST），
    不是「这个名字在文件里出现过」。
    """
    body = _step_fn()
    tree = ast.parse(body)

    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                m = re.match(r"^state\[[\"']([\w]+)[\"']\]$", ast.unparse(tgt))
                if m:
                    assigned.add(m.group(1))

    assert {"held_since"} <= assigned, (
        f"state 里没有『持仓是哪天定的』字段，实际赋值的有：{sorted(assigned)}。"
        "持仓被覆盖后无法审计，缓冲区逻辑也无从校验"
    )
