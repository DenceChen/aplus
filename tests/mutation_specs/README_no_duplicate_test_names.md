# `test_no_duplicate_test_names.mut` —— 三条变异各自在打什么

被测对象：`tests/test_no_duplicate_test_names.py`（8 条守卫）。

| 变异 | 真实失效形态 | 预期被谁杀 |
|---|---|---|
| **N1** | 分组键从「按文件」改成**全树计数** | `test_live_cross_module_names_are_exempt` |
| **N2** | `startswith("test_")` 前缀过滤被摘掉 | `test_the_duplicate_probe_ignores_duplicated_helpers_that_are_not_tests` |
| **N3** | `tree.body` 换成 `ast.walk`（不限模块顶层） | `test_the_duplicate_probe_ignores_a_local_def_that_shadows_the_name` |

## 为什么 N1 是这批里最要紧的一条

**它是这次重构存在的唯一理由。**

重构前 `test_live_cross_module_names_are_exempt` **自己重算了一遍分组**
（`seen: dict[str, set[str]]`），所以它验的是**探测器**而不是**主判据的判决**
⇒ 变异 N1 只会让主判据假红，而反控**纹丝不动**
⇒ 那条反控是**装饰**（第 131 层：正控要验判决，不只验探测器）。

修法是把扫描收敛成 `_duplicates_across_suite()` 一个实现，反控改成
**直接读那份判决清单**：

```python
flagged = {entry.split("::")[1].split(" ")[0]
           for entry in _duplicates_across_suite() if "::" in entry}
wrongly = known & flagged
```

⇒ 不验则这条重构等于没做。

## N1 的变异体为什么长这样

它不是「把 `p` 换掉」那种玩具变异，而是**一个仍然合法的、按全树汇总的实现**：

```python
_merged = Counter()
for p in _all_test_files():
    _merged.update(_top_level_test_names(p))
for p, counts in ((_all_test_files()[0], _merged),):
```

第二段用单元素元组撑住原有的两层 `for` 缩进，
所以**它跑得动、报得出行号**——只是行号属于第一个文件。
真实失效形态是「有人为了少扫一遍文件，把计数提到循环外面」，
这在性能直觉上很有吸引力，因而是最可能真发生的那种错。

## 纪律提醒

- **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 收集/用法/内部错误 ⇒ 不可验证。
- **跑完先 `grep` 确认改动落进文件**再看 pytest 结果（第 14/148 层）。
- 变异台运行期间**不得并发跑别的 pytest**（台子会真改写被测源文件）。
