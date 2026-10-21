# `holdings_box_wallet` 的变异规格（2026-10-21 新增）

规格本体在 `test_holdings_box_wallet.mut`；
被测 node：`tests/test_holdings_box_shows_the_wallet.py`（5 条，node 里**真跑** `renderHeld`）。

## 变异台结果（实测）

```
基线绿：5 passed
✅ P1  rc=1 / 3 failed   （钱包改回候选池 ⇒ 缺陷原样）
✅ P2  rc=1 / 1 failed   （三态塌成两态 ⇒ 「读不出来」被当成「真空仓」）
2/2 条符合预期
```

## 为什么 P1 一次红 3 条

P1 把钱包换回候选池 ⇒ ① 只数错、② 候选池没与持仓分开标注、③ 三态的分岔没了
⇒ 三条守卫各红 ⇒ 它们的判别力**互不相同**（不是同一条的复本，第 112 层）。

## 前端变异的闸门

目标文件是 `.html` ⇒ 台子用 `node --check` 验**抽出来的内联脚本块**
（`tests/test_chat_wiring.py::test_every_inline_script_block_actually_parses` 同一套量具）。
⇒ 所以前端变异必须是**语法合法但语义错**的
（`const heldCodes = live.up || [];` 合法、但把钱包换回了候选池）。
