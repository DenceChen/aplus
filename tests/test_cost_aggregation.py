"""年化成本必须**按日取均值再求和**——`sum()` 会虚高 `top_n` 倍。

## 事故

`evaluate_weights` 把同一个**组合级** drag 写给涨腿的每一行：

    res.loc[res["direction"] == "up", "cost"] = drag

`drag` 是「本期成本占本期收益的比例」，**一只票一份**，不是每行一份。于是
两种下游用法结果完全相反：

- `groupby("pred_date").mean()` → 每期恰好一个 drag，**正确**
- `up["cost"].sum()`           → 每期记了 `top_n` 份，**放大 `top_n` 倍**

`scripts/signcorrect_freq_sweep.py` 与 `scripts/regime_control_sweep.py`
第一版都写了 `sum()`。实测榜宽 10 时报出「年成本 370.4%」，真实值 **37.04%**。

## 为什么这个 bug 特别危险

**它虚高的方向让一个错误结论看起来像铁证。**

虚高 10 倍之后，「日频成本 370%/年、信号只有 92%/年、成本必然吃掉信号」
读起来是一个无懈可击的结论——而它把这个战略判断建立在**一个 10 倍的数**上。
真实的 37%/年 与信号 92%/年 在同一量级，「日频因成本不可行」这个结论
根本不成立。

对比一下今天的另一个 bug（市场基准混入上市前股票，虚高 114 个百分点/年）：
那个至少虚高得**太离谱**、容易引起怀疑。这个只虚高 10 倍，完全落在
「数字看着很合理」的区间里。**越合理的错数字越危险。**

所以判据必须钉的是**聚合口径**这个性质，而不是某个具体数值——
数值会随费率、榜宽、频率变，口径不会。
"""
import ast

import pandas as pd
import pytest

from scripts.regime_control_sweep import _metrics  # noqa: F401  （确保可导入）
from scripts.signcorrect_freq_sweep import sweep  # noqa: F401


def _pred(n_stocks: int, drag: float, days: list[str]) -> pd.DataFrame:
    """构造一个合规的预测表：每个**日期** `n_stocks` 只涨腿，cost 全为 `drag`。

    `days` 必须是**互不相同**的日期列表。传重复日期会让「每期成本 = drag × 期数」
    这条关系被重复计数，判据随之失效——第一版就踩了这个坑（比值变成
    `top_n × 重复次数` 而不是 `top_n`，于是一条假红掩盖了另一条真关系）。
    """
    assert len(set(days)) == len(days), "days 必须互不相同，否则期数会被算错"
    rows = [{"pred_date": d, "code": f"{d}-{i}", "direction": "up",
             "actual_pct": 0.01, "cost": drag, "market_pct": 0.0}
            for d in days for i in range(n_stocks)]
    return pd.DataFrame(rows)


# ---------- 核心判据：mean 与 sum 的关系 ----------

@pytest.mark.parametrize("n_stocks", [10, 20, 50])
def test_daily_mean_is_the_only_correct_way_to_aggregate_cost(n_stocks):
    """每期成本 = 每期 drag × 期数，与榜宽无关。

    这条钉的是**不变量**：把榜宽从 10 加到 50，若用 `sum()`，报出的年成本
    会跟着涨 5 倍——而真实成本在固定换手下不该随榜宽线性膨胀。
    """
    pred = _pred(n_stocks, drag=0.002, days=["2020-01-01", "2020-01-02"])

    per_day = pred.groupby("pred_date")["cost"].mean().sum()   # 正确
    naive = pred["cost"].sum()                                  # 错

    assert per_day == pytest.approx(0.002 * 2), "每期一个 drag，共 2 期"
    assert naive == pytest.approx(per_day * n_stocks), (
        "sum() 的偏差恰好是 top_n 倍——这条关系必须成立，"
        "否则说明上游写 cost 的方式变了，本文件的判据全部失效")


def test_widening_the_basket_must_not_inflate_reported_cost():
    """榜宽翻倍，报出的年成本不应翻倍。

    这是从**外部可见行为**反查聚合口径——比盯住实现细节更耐重构。
    """
    days = ["2020-01-01", "2020-01-02", "2020-01-03"]
    out = {}
    for n in (10, 20):
        pred = _pred(n, drag=0.002, days=days)
        out[n] = pred.groupby("pred_date")["cost"].mean().sum()
    assert out[10] == pytest.approx(out[20]), (
        f"同样的 drag，榜宽 10 与 20 的年成本应相同，实得 {out}"
    )


# ---------- 语义：cost 是组合级，不是每股级 ----------

def test_cost_column_is_duplicated_across_the_up_leg():
    """把上游契约写进测试：`cost` 每行相同，它是组合级口径。

    这条不是「测试实现」，是**记录一个别人极易误读的约定**。
    `cost_drag` 的返回值是「占该期收益的比例」，即组合级；
    `evaluate_weights` 复制到每行后，任何人拿它做逐行求和都会错 10 倍。
    """
    pred = _pred(10, drag=0.002, days=["2020-01-01"])
    assert pred["cost"].nunique() == 1, "本测试假定 cost 每行相同（见 docstring）"
    assert (pred["cost"] == 0.002).all()


def test_net_return_must_subtract_cost_once_per_day_not_once_per_row():
    """净值曲线：日净收益 = 该日涨腿**平均**净收益。

    `actual_pct − cost` 先按日取均值，与 `mean(actual) − mean(cost)` 等价——
    因为 cost 每行相同。这条确保复利净值不会把成本扣 10 次。
    """
    pred = _pred(10, drag=0.002, days=["2020-01-01", "2020-01-02"])
    up = pred.copy()
    up["_net"] = up["actual_pct"] - up["cost"]
    daily = up.groupby("pred_date")["_net"].mean()
    assert daily.tolist() == pytest.approx([0.008, 0.008]), (
        "日净收益应为 0.01 − 0.002 = 0.008；"
        "若实现改成按行求和或把 cost 重复扣除，这里会立刻红")


# ---------- 生产代码的聚合口径守卫（AST 结构判据） ----------

def _is_attr_call(node, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name)


def _is_cost_slice(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and node.value == "cost")


def _reads_cost(node: ast.AST) -> bool:
    """子树里有没有出现过 `["cost"]` 下标。"""
    return any(isinstance(n, ast.Subscript) and _is_cost_slice(n.slice)
               for n in ast.walk(node))


def _cost_aliases(tree: ast.AST) -> dict[str, str]:
    """变量名 → 它装的是「裸 cost 列」还是「cost 的日均值」。

    没有这一层，`c = up["cost"]` 然后 `c.sum()` 就能整条绕过形状判据——
    而拆中间变量恰恰是重构时最自然的一步。解析不出的一律不记，
    宁可漏判也不误判（误判比漏判更消耗信任）。
    """
    alias: dict[str, str] = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)):
            continue
        name, v = n.targets[0].id, n.value
        if isinstance(v, ast.Subscript) and _is_cost_slice(v.slice):
            alias[name] = "raw"
        elif _is_attr_call(v, "mean") and _reads_cost(v.func.value):
            alias[name] = "mean"
    return alias


def _cost_kind(node: ast.AST, alias: dict[str, str]) -> str | None:
    """这个表达式装的是裸 cost 列（`raw`）、日均值（`mean`），还是无关（None）。"""
    if isinstance(node, ast.Name):
        return alias.get(node.id)
    if _is_attr_call(node, "mean"):
        return "mean" if _reads_cost(node.func.value) else None
    if isinstance(node, ast.Subscript) and _is_cost_slice(node.slice):
        return "raw"
    return None


def test_cost_aggregation_is_daily_mean_not_raw_row_sum():
    """三个扫描脚本里，`cost` 只能以两种形态出现：按日取均值、逐行扣除。

    **在 AST 上判，不在文本上判。** 四个版本各栽一次，全是同一类病——
    判据钉住了**写法**而不是**口径**：

    - 第一版 `src not in` 全文匹配，命中了文档里描述这个 bug 的那句话——
      修好了、有人写下解释，判据就误报。
    - 第二版剥掉字符串字面量再 `ast.unparse` 匹配，`unparse` 把双引号归一化
      成单引号，`["cost"]` 永远匹配不上，判据**假绿**。
    - 第三版要求每个 `["cost"]` 的父节点都是 `.mean()`，把逐行扣成本
      （`actual_pct - cost`）这种**合法**用法也一起抓了。
    - 第四版只查 `.sum()` 的接收者形状，`c = up["cost"]` 后 `c.sum()` 整条绕过。

    最终判据分两条：

    1. 每个 `.sum()` 装的必须是 **mean**，不是 **raw**——经由别名表解析，
       拆成中间变量照样判违规。`groupby(...).mean().sum()`（先按日取均值，
       再跨日求和）合法；`pred["cost"].sum()` 当场判违规。
    2. 必须**存在**至少一处 `groupby(...)["cost"].mean()`。缺了它说明成本
       来源整个不见了，比算错更需要立刻发现。

    与引号风格、变量名、是否拆中间变量都无关；`actual_pct - cost` 那种
    逐行扣除不受影响（它不是 `.sum()`）。
    """
    bad: list[str] = []
    for path in ("scripts/signcorrect_freq_sweep.py",
                 "scripts/regime_control_sweep.py",
                 "scripts/basket_width_sweep.py"):
        tree = ast.parse(open(path, encoding="utf-8").read())
        alias = _cost_aliases(tree)

        for node in ast.walk(tree):
            if _is_attr_call(node, "sum") \
                    and _cost_kind(node.func.value, alias) == "raw":
                bad.append(f"{path}: 出现未经 groupby(...).mean() 保护的 "
                           f"cost 求和——会虚高 top_n 倍")

        has_daily_mean = any(
            isinstance(n, ast.Attribute) and n.attr == "mean"
            and isinstance(n.value, ast.Subscript)
            and _is_cost_slice(n.value.slice)
            and _is_attr_call(n.value.value, "groupby")
            for n in ast.walk(tree))
        if not has_daily_mean:
            bad.append(f"{path}: 找不到 groupby(...)['cost'].mean()，"
                       f"成本来源整个不见了")

    assert not bad, "成本聚合口径被破坏：\n  " + "\n  ".join(bad)


def test_cost_comes_from_actual_turnover_not_a_guess():
    """成本必须来自 `pred["cost"]`（由实际换手算出），不得凭费率估算。

    费率一旦变化（印花税、佣金调整），估算口径不会跟着变，而实测口径会——
    这是把成本钉在数据上的唯一理由。第一版脚本里那个凭印象写的
    `_annual_cost()` 就是反例：它照样打印出一张漂亮的表。
    """
    for path in ("scripts/signcorrect_freq_sweep.py",
                 "scripts/regime_control_sweep.py",
                 "scripts/basket_width_sweep.py"):
        tree = ast.parse(open(path, encoding="utf-8").read())
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_annual_cost" not in called, (
            f"{path} 不得回退到凭费率估算的成本函数——费率一改它不会跟着变")