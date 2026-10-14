# aplus —— 个人 A 股量化平台

## 自进化的两个抓手（最重要，别丢）

**1. 回测** —— 历史数据 **与未来数据**双轨，前瞻验证的三条铁律永不放松：

- 注册即冻结
- 窗口必须晚于注册日
- 门槛随候选数收紧

> **降低 K 是提速，砍掉前瞻验证是作弊。**

**2. 书** —— 石川《因子投资方法与实践》与 Chan《Quantitative Trading》。

抽取好的纯文本在 `data/books/`，**带行号，可直接 grep**：

```bash
grep -n "<关键词>" data/books/cn_factor_lines.txt    # 石川，如「正交」「7.4」
grep -n "<关键词>" data/books/chan_lines.txt         # Chan，按页码引用
```

> ⚠️ **动手设计任何方法之前先 grep 书。**
> 2026-10-14 的反面教材：因子合成方案我先自己推出来，没先翻书；
> 而书里 7.4 / 2.6 讲的正是同一件事，还多给一条「等权往往就很好」的警告。
> **先推后读，与第 63 条「先有结论后凑公式」是同一个病。**

已挖过的章节见 `docs/factor_spec_cn.md` §0 定位表 —— 但那张表只覆盖**已挖**的部分，
未挖的章节要自己 grep。

**已落地的书稿**：`docs/factor_spec_cn.md`（石川·工程规格对照，行号级引用）、
`docs/methodology_chan.md`（Chan·20 条工程原则 + 5 条错误 + 凯利）、
`docs/chan_recipe_mining_2026-10-14.md`（Chan·策略配方 A–F，页码级引用）、
`docs/factor_book_recipes_2026-10-14.md`。

## 判据必须预注册

任何「够不够线」的判决，门槛要在**看到数据之前**写进 `docs/`，
并且**由外部锚推导**（σ_r 实测值、成本假设），不写成字面量。

- 判决必须是**机械执行**：`scripts/verdict_*.py`，没有可调口径
- 缺档必须**报错**，绝不回落（缺低尾档就用全截面顶上 = 口径不一致）
- 逐年判决只有 `pass_: bool` —— **不许有 margin 字段**，有它就有人讲「只差一点点」
- **零判决年 ≠ 零失败年**：没有可比年份时拒绝出判决
- 读到与主张**方向相反**的结论时：**加对照臂，不改判据**

## 环境

- Python 一律用 `.venv/bin/python`（**不要**用裸 `python`）
- 面板载入是 GB 级 / 10 分钟量级；跑之前先看内存，
  与赛马同跑会 OOM 并杀掉 RSS 最大的那个（往往是跑了几十分钟的那个）
- 报告在 `data/reports/`（`data/` 被 gitignore，报告不入库是本仓惯例）
- 报告的生成模块必须在 `aplus/provenance.py` 的 `DEFAULT_GENERATORS` 登记并盖章

## 当前待办入口

| 文档 | 内容 |
|---|---|
| `docs/TODO_measure_sigma_r.md` | σ_r 实测 + 月频判决（单因子已否，合成在跑）+ **两条预注册判据** |
| `docs/TODO_rerun_reversal_report.md` | 反转报告三个口径 bug 的修复与重跑 |
| `docs/TODO_cost_constant_alignment.md` | `STOCK_COST_DEFAULT` 0.003 → 0.0055 |
| `docs/MISTAKE_LOG_2026-10-14.md` | 今日失误 + 16 层守卫分类 |

> **铁律：修完 P0 一律重跑，重跑前绝对收益数字不可引用。**