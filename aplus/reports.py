"""报告持久化：预测/验证/配置的落盘与历史读取。

目录结构（reports_dir，默认 aplus/data/reports）：
    predictions/pred_{date}.parquet   单文件含 up+down 两段（direction 列区分）
    verification.parquet              全部验证判定（追加式，唯一调参依据）
    allocation/alloc_{date}.json      每日资产配置方案

## `signal_id`：前瞻证据的**分桶键**（2026-10-21）

前瞻底稿 `verification.parquet` 是**追加式**的 ⇒ 同一本底稿里会**同时**装着
**不同信号**产生的行 ⇒ 「这个打分器有没有排序力」必须**只问当前信号的行**。

⚠️ `weights_hash` **证明力弱于**信号指纹：它只记**权重**，不记 `top_n`/`bottom_n`
⇒ 同一组权重下不同宽度的名单会被混在一起
⇒ `aplus/signal_scope.py` 的归属基准**优先 `signal_id`**，`weights_hash` 只是退路。

`signal_fingerprint(config)` 与 `current_signal_id(reports_dir)` 是
**唯一真相源**；`aplus/signal_scope.py` 只**调用**它们，不自己实现
（第 97 层：两份副本之间没有守卫）。
"""
import hashlib
import inspect
import json
from pathlib import Path

import pandas as pd

from .verify import UNJUDGEABLE_REASON

# 预测文件里那一列的名字：它记的是「本文件是用哪组权重排出来的」。
WEIGHTS_HASH_COLUMN = "weights_hash"

# 预测/验证文件里那一列的名字：它记的是「这一行是用哪套**信号**产生的」。
#
# 🚨 与 `WEIGHTS_HASH_COLUMN` **不是一回事**：权重不变而池子变了 ⇒
# 权重指纹**逐位相同** ⇒ 它**不能**当信号身份。
SIGNAL_ID_COLUMN = "signal_id"


def _pred_path(reports_dir, pred_date: str) -> Path:
    d = Path(reports_dir) / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"pred_{pred_date}.parquet"


def weights_fingerprint(weights: dict | None) -> str | None:
    """一组权重的**稳定指纹**（12 位十六进制），无权重时返回 None。

    ## 为什么指纹必须与 dict 插入顺序无关

    它的用途是**跨进程、跨天**回答「是不是同一组权重」。它一旦对顺序敏感，
    `load_weights` 在保存时给出的顺序与 `/api/weights` 读回的顺序稍有不同
    就会算出两个指纹，于是每次都报 `mismatch` —— 一个永远在响的假警报。

    假警报的杀伤力不比漏报小：用户学会忽略它之后，连真的那条一起忽略。

    排序发生在 **hash 之前**（`json.dumps(..., sort_keys=True)`），
    不在 dict 上排序后取 items —— 后者同样可行但更啰嗦，而且
    `ensure_ascii=False` 那一层就得多解释一句「为什么排序不改变编码」。

    ## 为什么是 12 位

    够短才能直接印在页面警示条上（用户要能自己核对两个指纹）；
    48 位熵对于「同一台机器上两组配置」这个比较场景，撞车概率可以忽略。

    ## 空权重返回 None 而不是某个字符串

    空 dict 的 `json.dumps` 是合法的 `"{}"`，会算出一个**看起来合法**的指纹。
    拿它去和真权重的指纹比必然 mismatch，而真相是「压根没有权重可谈」——
    那属于 unknown，不属于 mismatch。
    """
    if not weights:
        return None
    payload = json.dumps(weights, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ── 信号指纹：前瞻证据的分桶键（2026-10-21）────────────────────────

def signal_fingerprint(config: dict) -> str:
    """信号配置的 8 位指纹。**配置变 ⇒ 指纹变** ⇒ 前瞻证据可按它分桶。

    ## 契约（**逐字固定**，下游判据/测量脚本按它 import）

    ```python
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    ```

    `config` 的键**必须固定为这三个**（多一个少一个都会让指纹不可比）：

    | 键 | 内容 |
    |---|---|
    | `weights` | 名单用的那组权重（dict） |
    | `top_n` | 名单宽度（涨腿） |
    | `bottom_n` | 名单宽度（跌腿） |

    ## 🚨 为什么**只有**这三个（**配置层的参数一律不进**）

    `signal_id` 回答的是「**这份名单是用哪套排名配置排出来的**」
    ⇒ 它只该包含**影响排名**的东西。

    - ⛔ **不含 `pool_filter`**：可买性过滤在**配置层**（`aplus/alloc.py`），
      它决定「**买哪几只**」，**不决定「排第几」**
      ⇒ 名单（10 涨 10 跌）**一个字都没变** ⇒ 它不是信号身份的一部分。
    - ⛔ **不含 `n_positions`**：那是**账户层**（持仓几只），同理。

    ⇒ 这带来一个**必须保住的后果**：敞口 5→10 只 + 加上可买性过滤，
    **名单生成配置一个字没变** ⇒ **`signal_id` 逐位不变**
    ⇒ **底稿里那 160 行仍然属于当前信号** ⇒ **D1 的 6 个窗口与验收时间线全部保住**。

    ## 为什么用 `sort_keys=True`

    指纹要在**跨进程、跨天**比较 ⇒ 必须与 dict 插入顺序无关
    （与 `weights_fingerprint` 同一条纪律）。

    ## 为什么 `default=str`

    配置里可能出现 numpy 标量（`np.float64`）⇒ 不加 `default=str`
    会在 `json.dumps` 处抛 `TypeError`。**宁可把它写成字符串**
    （`"0.5"` 与 `"0.5"` 仍然稳定可比）也不要让整个指纹计算崩掉。

    ## 8 位够吗

    够用：它的用途是「**这一行属不属于当前信号**」的**布尔判定**，
    不是密码学意义上的抗碰撞。32 位熵在单机的几千个信号里撞车概率可忽略。
    """
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def current_signal_id(reports_dir, *, weights: dict | None = None,
                      top_n: int | None = None,
                      bottom_n: int | None = None) -> str:
    """从**生产的真实配置**算当前信号 id。

    ## 每个默认值的来源（**一律不许手打**）

    | 参数 | 默认来源 |
    |---|---|
    | `weights` | `aplus.evolve.load_weights(reports_dir)`（实盘真正在用的那组） |
    | `top_n` | `aplus.predict.build_predictions` 签名默认值 |
    | `bottom_n` | `aplus.predict.build_predictions` 签名默认值 |

    🚨 **配置层的东西一律不在这里**（`pool_filter` / `n_positions`）
    ⇒ 见 `signal_fingerprint` 的「为什么只有这三个」。

    ## 显式传参的用途

    允许显式覆盖是为了让**测试**能钉住性质，以及让**另一个信号**的
    指纹可算（对照臂）。**生产路径不要传** ⇒ 传了就等于「当前信号」
    变成了「我刚传进去的那个」。
    """
    from .predict import build_predictions

    if weights is None:
        from .evolve import load_weights
        weights = load_weights(reports_dir)["weights"]

    pred_params = inspect.signature(build_predictions).parameters
    if top_n is None:
        top_n = int(pred_params["top_n"].default)
    if bottom_n is None:
        bottom_n = int(pred_params["bottom_n"].default)

    config = {
        "weights": dict(weights or {}),
        "top_n": int(top_n),
        "bottom_n": int(bottom_n),
    }
    return signal_fingerprint(config)


def save_predictions(reports_dir, pred_date: str, up: pd.DataFrame, down: pd.DataFrame,
                    weights: dict | None = None,
                    signal_id: str | None = None) -> None:
    """落盘预测榜单，并**记下自己用了哪组权重**（以及**哪套信号**）。

    ## 为什么必须记

    2026-10-14 实测：概览页显示的榜单来自 `pred_2026-10-13.parquet`，
    那是 `DEFAULT_WEIGHTS` 符号**整体翻转之前**生成的
    （`{mom20:+1.0, ...}` → `{mom20:-1.0, ...}`）。那份榜单的看涨前 6 名
    20 日动量**全是正的**（+164.5% / +122.8% / +71.7% / +105.7% / +98.0% / +79.2%），
    而实测 IC（`data/reports/factor_ic_7y.json`）说 `mom20` 是负的
    （全段 IC −0.0357；8 因子 × 8 年逐年符号 63/63 一致——`turnratio` 缺 2019
    只有 7 条逐年记录，缺口不补也不外推，分母是有记录的那 63 条）。
    符号修正后，看涨榜**应该全是低动量股**。

    也就是说：用户要的是「明天会涨的 10 只」，而屏幕上这张榜是用一把
    实测会挑反的尺子排出来的，且**没有任何一处说明它早于当前权重**。

    本函数此前只写 `pred_date`，不记权重，于是任何权重变更都会让概览页
    静默显示过期榜单：HTTP 200、字段类型正确、前端不报错。

    ## 没传 weights 就不写这一列

    留一个全空串或全 NaN 的僵尸列，看起来「有字段」，读出来还是不可知，
    还得每次额外判空。不如干脆没有——缺列就是缺列，判定见
    `prediction_weights_verdict` 的 `unknown` 档。

    ## `signal_id`：**信号身份**（2026-10-21）

    与 `weights_hash` **不是一回事**：过滤只改池、不改权重 ⇒ 权重指纹逐位相同
    ⇒ 它**区分不了**信号变了。

    🚨 **没传就不写这一列**（与 `weights_hash` 同一条纪律）：
    缺列 ⇒ `aplus/signal_scope.py` 判 `MISSING_SIGNAL_ID_COLUMN`
    ⇒ **不可判定**（而不是「就当全是当前信号」）。
    """
    both = pd.concat([up, down], ignore_index=True)
    both["pred_date"] = pred_date
    fp = weights_fingerprint(weights)
    if fp is not None:
        both[WEIGHTS_HASH_COLUMN] = fp
    if signal_id is not None and str(signal_id).strip():
        both[SIGNAL_ID_COLUMN] = str(signal_id).strip()
    both.to_parquet(_pred_path(reports_dir, pred_date), index=False)


def load_prediction_weights_hash(reports_dir, pred_date: str) -> str | None:
    """读回某份预测文件记下的权重指纹；**读不出就返回 None**。

    三种「读不出」，一律折成 None，因为它们对使用者的含义是同一句
    「出处不可知」：

    - 文件不存在
    - 文件没有 `weights_hash` 列（2026-10-14 之前生成的全部文件）
    - 文件损坏（抛异常）——**出处读不出来本来就是一个合法结论**，不是故障。
      让它抛出去会把 `/api/predictions/latest` 整个打成 500，
      而页面上只剩一片空白：把「结论」和「故障」混在一起，最该说话时反而不说话。
    - 列存在但一行里出现了两个不同的指纹 —— 矛盾等于不可知。
      取第一个或取最后一个都是替这份文件做主。
    """
    # 不用 `_pred_path`：它会 mkdir，而这是一条**读**路径——
    # 查一个不存在的预测日不该顺手把 predictions 目录建出来。
    path = Path(reports_dir) / "predictions" / f"pred_{pred_date}.parquet"
    if not path.exists():
        return None
    try:
        raw = pd.read_parquet(path, columns=[WEIGHTS_HASH_COLUMN])[WEIGHTS_HASH_COLUMN]
    except Exception:  # noqa: BLE001 — 缺列 / 文件损坏 / 格式不对，都是「不可知」
        return None
    vals = {str(v).strip() for v in raw.dropna().tolist() if str(v).strip()}
    return vals.pop() if len(vals) == 1 else None


def prediction_weights_verdict(reports_dir, pred_date: str, current_weights: dict | None) -> dict:
    """榜单用的权重，与当前权重，是否一致——三态。

        match     —— 榜单记下的指纹 == 当前权重的指纹
        mismatch  —— 两边指纹都读得出，且不相等
        unknown   —— **任一边读不出**（老文件没有这一列；或当前权重为空）

    ## `unknown` 为什么必须报警，而不能当成 `match`

    **无法证明一致 ≠ 一致。**

    一个缺失的列若被静默当成「一致」，这个字段就退化成了摆设：
    所有权重变更仍然静默通过，只是多了一个从不报警的列。

    ## 「太吵」不成立

    本函数每次只看**被点名的那一份**文件。历史归档走
    `/api/predictions/history`，那个接口不带这个状态。
    所以永远只有概览页正在显示的那一张被判——不是「所有历史文件一起报警」。
    """
    board_hash = load_prediction_weights_hash(reports_dir, pred_date)
    current_hash = weights_fingerprint(current_weights)
    if board_hash is None or current_hash is None:
        status = "unknown"
    elif board_hash == current_hash:
        status = "match"
    else:
        status = "mismatch"
    return {
        "status": status,
        "board_hash": board_hash,
        "current_hash": current_hash,
        "current_weights": current_weights or None,
    }


def load_predictions(reports_dir, pred_date: str):
    path = _pred_path(reports_dir, pred_date)
    if not path.exists():
        return None
    both = pd.read_parquet(path)
    return both[both["direction"] == "up"].reset_index(drop=True), both[both["direction"] == "down"].reset_index(drop=True)


def latest_prediction_date(reports_dir):
    d = Path(reports_dir) / "predictions"
    if not d.exists():
        return None
    dates = sorted(p.stem.removeprefix("pred_") for p in d.glob("pred_*.parquet"))
    return dates[-1] if dates else None


def pending_prediction_dates(reports_dir):
    """所有**尚未判定**的预测日，按时间升序。

    ## 为什么不能只取最新一天（2026-10-14 修复）

    `latest_prediction_date` 返回目录里**最新**的文件名。
    `run_daily.step_verify_yesterday` 拿它选判定对象，于是每次只判一天，
    前一天**永久跳过**——没有任何补判循环。

    实测 2026-10-09 ~ 10-13 共 5 个预测日、100 条推荐，
    `verification.parquet` 只入库 20 条：**80% 的证据被静默丢弃**。
    而被丢弃的 2026-10-09 那天，诚实口径下命中率是 70.6%（12/17），
    偏偏是留着的 10-12 那天得到 20.0%。

    换句话说：**证据被丢了，而且丢掉的正好是好的那批**。
    样本量因此从 n 天塌缩到 1 天——`pred_date` 唯一值只有一个。

    命中率是平台调参的唯一依据（「所有判定结果永久留存」是
    `verify.py` 的模块承诺）。这条函数就是那个承诺的兑现处。

    ## 为什么缺 `exit_date` 的判定不算「已判定」（2026-10-14）

    实测线上那 20 行 2026-10-12 的判定是 `closes[-1]/closes[-2]` 产的，
    即 `entry_lag=0`——**与锚定口径差一根 K 线**。同一个 bug 让
    「信号日之后第一根买」变成「库里最后两根买」，而后者在跑批当天
    恰好等于「次日收盘 / 今日收盘」，看起来完全正常。

    而本函数原本只看 `pred_date` 在不在表里，于是那一天**永远不会被重判**，
    两种口径的行会永久混在同一张表里，`rolling_stats` 分不出哪条是哪条。

    判据：`exit_date` 非空 = 锚定口径判过 = 算数。
    旧行缺这一列 → 视为未判定 → 重开。`append_verification` 按
    `(pred_date, code)` 去重保留最后一条，重判会**覆盖**旧行而不是追加。
    """
    d = Path(reports_dir) / "predictions"
    if not d.exists():
        return []
    dates = sorted(p.stem.removeprefix("pred_") for p in d.glob("pred_*.parquet"))
    if not dates:
        return []
    vpath = _ver_path(reports_dir)
    judged = set()
    if vpath.exists():
        try:
            ver = pd.read_parquet(vpath)
            # 锚定口径判定的日子：有 exit_date 且非空
            if "exit_date" in ver.columns:
                anchored = ver["exit_date"].notna() & (ver["exit_date"].astype(str) != "")
                # 🚨 行级结清（2026-10-16）：一天**全部**行都结清才算判过。
                #   旧实现用「至少一行」，而 `no_data` 天是**部分覆盖**——
                #   实测 2026-10-12 共 20 行、13 行有 exit_date、7 行 no_data，
                #   于是那 7 行**永远不会被重判**。
                #   「或永久不可判定」这一支是为停牌票准备的：它们永远补不上，
                #   若不结清那一天会被**每天重判一次、永不停歇**。
                if UNJUDGEABLE_REASON in ver.columns:
                    reason = ver[UNJUDGEABLE_REASON]
                    settled = reason.notna() & (reason.astype(str) != "")
                else:
                    settled = pd.Series(False, index=ver.index)
                done = ver.assign(_settled=anchored | settled)
                per_day = done.groupby(done["pred_date"].astype(str))["_settled"].all()
                judged = set(per_day[per_day].index.astype(str))
            # 无 exit_date 列 → 整档都是旧口径，一律重判
        except Exception:  # noqa: BLE001 — 判定档损坏时宁可全部补判，不可跳过
            judged = set()
    return [x for x in dates if x not in judged]


def _ver_path(reports_dir) -> Path:
    return Path(reports_dir) / "verification.parquet"


def append_verification(reports_dir, results: pd.DataFrame) -> None:
    path = _ver_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        results = pd.concat([old, results], ignore_index=True)
        # 同一预测日同一票去重（重跑幂等）
        results = results.drop_duplicates(subset=["pred_date", "code"], keep="last")
    results.to_parquet(path, index=False)


def load_verification_history(reports_dir) -> pd.DataFrame:
    path = _ver_path(reports_dir)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _alloc_path(reports_dir, date: str) -> Path:
    d = Path(reports_dir) / "allocation"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"alloc_{date}.json"


def save_allocation(reports_dir, date: str, plan_dict: dict) -> None:
    """写每日资产配置方案。

    ★ `date` **必须同时进内容**，不能只拿去拼文件名。
    曾经只拼文件名（`alloc_<date>.json`）而内容里一个日期都没有，
    于是**产物没有身份**：任何消费者问「这份方案是哪天的」只能去猜文件名，
    而「猜文件名」和「写文件名」是两个可以各自漂移的通道。
    ⇒ 内容里的 `date` 与文件名由**同一个入参**派生，两者**构造上不可能不一致**。
    """
    payload = dict(plan_dict)
    # 不覆盖调用方显式给的日期（那才是权威）；只在缺省时补上
    payload.setdefault("date", date)
    _alloc_path(reports_dir, date).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_allocation(reports_dir, date: str):
    path = _alloc_path(reports_dir, date)
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    # 🚨 **不静默回填**：内容里缺 date 就是缺，不能拿文件名日期假装它有。
    #    旧文件靠调用侧自己判（`obj.get("date")`），这里保持 None 而不是编一个。
    if isinstance(obj, dict) and not obj.get("date"):
        obj["date"] = None
    return obj


def _run_status_path(reports_dir) -> Path:
    return Path(reports_dir) / "run_status.json"


def write_run_status(reports_dir, status: dict) -> None:
    path = _run_status_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 「今天那次尝试的结局」：与「那一次跑成没跑成」是**两个不同的量** ──
#
# 🚨 **为什么必须分开**（本仓反复吃过的亏：两个各自正确的数被当成同一个量）：
#
# | 字段 | 它说的是 |
# |---|---|
# | `date` / `status` / `steps` | **那一次尝试**跑在哪天、跑成没跑成、哪一步没过 |
# | `attempted_date` / `outcome` / `outcome_reason` | **那一次尝试的结局**：出了单没出单、为什么没出 |
#
# 实测事故（2026-10-20 18:30）：采集失败 ⇒ fail-closed **一份名单都没出**，
# 落盘的是 `status:"skipped"` + `steps.run_day_gate:"WORKDAY_NOT_IN_STORE"`。
# ⇒ 界面上只有「部分完成 · run_day_gate」——**一个门名**：
# 它没告诉用户「所以今天没出单」，更没说「不是 cron 没跑，是采集没拿到数据」。
# 而用户真正要修的是**采集**。
#
# ⇒ `status:"skipped"` 本身也是**歧义**的：同一个值同时表示
# 「今天不是工作日」（本来就不出单）与「今天工作日但采集失败」（本该出单却没出）。
# ⇒ 这两件事必须用**两个不同的 outcome 值**分开，否则界面无从说真话。
OUTCOME_OK = "ok"                        # 正常发布（出了单）
OUTCOME_NOT_A_WORKDAY = "not_a_workday"  # 今天不是工作日 ⇒ 本来就不出单（不是故障）
OUTCOME_DATA_MISSING = "data_missing"    # 今天是工作日但仓里**完全**没今天的数据 ⇒ 没出单（要查采集）
OUTCOME_FAILED = "failed"                # 跑了，但某一步炸了 ⇒ 没出单（要查那一步）
# 2026-10-20 新增：**部分覆盖** —— 今天是工作日，仓里**有一部分**股票有今天的数据。
# 🚨 它必须与 `data_missing` 分开：后者是「完全没数据」（采集全挂），
# 前者是「有些有、有些没有」（采集挂了一半 / 还在路上）。
# 合在一起的话，界面会说「仓里没有这一天的数据」—— 而事实上**有一部分有** ⇒ **事实错误**，
# 而且会把排查方向指错（用户去查「完全没采到」，而真因是「采了一半」）。
OUTCOME_PARTIAL_COVERAGE = "partial_coverage"

#: 🚨 **2026-10-21 新增**：「**数据源不可用**」必须与「工作日但仓里没数据」分开。
#:
#: 两者在**产出上**一样（都没出单），在**应对上**相反：
#: 「仓里没数据」是**结果**（可能是法定节假日，本来就不该有单）；
#: 「数据源不可用」是**原因**（网络/服务器挂了 ⇒ 晚些时候重试就能补上）。
#: ⇒ 2026-10-20 实测的断供就栽在这里：`000001.SZ` 的一次网络超时被读成
#: 「非交易日或数据未就绪」⇒ 全市场跳过更新 ⇒ rc=2 ⇒ **整天没出单**，
#: 而当晚仓里其实已有 5207 只的当日数据。
#: ⇒ 这一态让「该重试」这件事**有一个可读的字段**，而不是混进 `data_missing`。
OUTCOME_UPSTREAM_UNAVAILABLE = "upstream_unavailable"

RUN_STATUS_OUTCOMES = (OUTCOME_OK, OUTCOME_NOT_A_WORKDAY,
                        OUTCOME_DATA_MISSING, OUTCOME_FAILED,
                        OUTCOME_PARTIAL_COVERAGE,
                        OUTCOME_UPSTREAM_UNAVAILABLE)


def outcome_for_run_day(verdict: str, coverage: float | None = None) -> tuple[str, str]:
    """`classify_run_day` 的判决 → `(outcome, 一行话)`。

    **纯函数、模块级、可 import** ⇒ 守卫能在 1 毫秒里问它、
    能对它打变异（判据埋在一条 20 分钟的流水线后面就等于没有守卫）。
    ⚠️ `coverage` 是**参数**（不是在这里读盘）：纯函数纪律不因为多一个输入而破。

    ## 为什么「工作日但仓里没数据」必须**单独**一个值

    周末/节假日 ⇒ 本来就不出单（不是故障，用户不该被吓到）；
    工作日但仓里没这一天 ⇒ **本该出单却没出**（采集挂了，用户该去查采集）。
    ⇒ 两者合成一个 `skipped` 时，界面只能说「部分完成」，
    用户既不知道今天有没有单，也不知道该修 cron 还是修采集。

    ## 为什么「部分覆盖」又必须**单独**一个值

    仓里**有一部分**股票有今天的数据 ⇒ 与「完全没数据」是**两件事**：
    后者去查「采集全挂」，前者去查「采集挂了一半 / 还在路上」。
    ⇒ 合成一句「仓里没有这一天的数据」是**事实错误**，且把排查方向指错。
    """
    from .trading_calendar import (  # 局部 import：避免模块级循环依赖
        RUN_DAY_TRADING, RUN_DAY_WEEKEND, RUN_DAY_TRADING_DAY_PARTIAL,
    )

    if verdict == RUN_DAY_TRADING:
        return OUTCOME_OK, "交易日，正常发布"
    if verdict == RUN_DAY_WEEKEND:
        return (OUTCOME_NOT_A_WORKDAY,
                "今天是周末 ⇒ 交易所不开市，**本来就不出单**（不是故障）。"
                "下一个交易日照常出。")
    if verdict == RUN_DAY_TRADING_DAY_PARTIAL:
        pct = "未知" if coverage is None else f"{coverage * 100:.1f}%"
        return (OUTCOME_PARTIAL_COVERAGE,
                f"今天是工作日，但仓里只有 **{pct}** 的股票有今天的数据 ⇒ **今天没出单**。"
                "部分覆盖出的榜是**残缺**的（缺的那些票拿不到今天的因子 ⇒ 它们不在横截面里，"
                "横截面被悄悄缩小）⇒ 宁可不发。"
                "**要查的是采集**：看 data/run_daily.log 的 [update] 段"
                "（常见原因：主源超时 / 还没采完）。")
    return (OUTCOME_DATA_MISSING,
            "今天是工作日，但仓里**完全**没有这一天的数据 ⇒ **今天没出单**。"
            "🚨 仓里没有交易所日历 ⇒「法定节假日」与「采集失败」在这件事上**同形**；"
            "若今天恰是法定节假日，本条会读成采集失败（方向保守：挡住一次本不该有的交付）。"
            "**要查的是采集**：看 data/run_daily.log 的 [update] 段"
            "（常见原因：主源超时 / 数据未就绪）。")


def build_run_status(*, attempted_date: str, outcome: str, outcome_reason: str,
                     status: str, steps: dict, updated_at: str) -> dict:
    """`run_status.json` 的内容。**两个量并排，不合并。**

    `date` / `status` / `steps` **保持原义**（那一次尝试跑在哪天、跑成没跑成、
    哪一步没过）—— 改了会连带改坏 `guard_daily.py` 的补跑判据
    （它的唯一判据是 `status == "ok"`）与前端的既有渲染。

    新增三个字段是**加**，不是**改**：
    - `attempted_date`：这次尝试是**为哪一天**做的（`'YYYY-MM-DD'`）。
      今天与 `date` 同值；分开命名是为了让前端有一个**没有歧义**的字段可读。
    - `outcome`：这次尝试的**结局**（`ok` / `not_a_workday` / `data_missing` / `failed`）。
    - `outcome_reason`：一句人能懂的话（写清「出了没出单、为什么、该查什么」）。
    """
    if outcome not in RUN_STATUS_OUTCOMES:
        raise ValueError(
            f"outcome 必须是 {RUN_STATUS_OUTCOMES} 之一，收到 {outcome!r}")
    return {
        "date": attempted_date,
        "status": status,
        "steps": steps,
        "updated_at": updated_at,
        "attempted_date": attempted_date,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
    }


def read_run_status(reports_dir):
    path = _run_status_path(reports_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- 可转债纸面账户（路线 A 第四阶段） ----------
#
# state 是**配置与锚点**，一经创建不得再改：start 就是调仓网格的相位锚点，
# 必须在看到任何样本外结果之前定死（回测已证明挑相位能轻松把 +6.8% 变成 +17.1%）。
# rows 是纯重放的产物，每次全量覆写（幂等，不追加）。

def _bond_paper_state_path(reports_dir) -> Path:
    return Path(reports_dir) / "bond_paper_state.json"


def write_bond_paper_state(reports_dir, state: dict) -> None:
    path = _bond_paper_state_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def read_bond_paper_state(reports_dir):
    path = _bond_paper_state_path(reports_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _bond_paper_path(reports_dir) -> Path:
    return Path(reports_dir) / "bond_paper.parquet"


def save_bond_paper(reports_dir, rows: pd.DataFrame) -> None:
    path = _bond_paper_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(path, index=False)


def load_bond_paper(reports_dir) -> pd.DataFrame:
    path = _bond_paper_path(reports_dir)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def write_bond_paper_summary(reports_dir, summary: dict) -> None:
    path = Path(reports_dir) / "bond_paper.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def read_bond_paper_summary(reports_dir):
    path = Path(reports_dir) / "bond_paper.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── D2 缺口台账（`d2_signal_requirement.json`）的 API 读面 ──────────────────
#
# 🚨 **量纲：本函数只允许把「真百分数族」端出去。**
# 产物里 `headline.*` 与 `account_annual_ceiling_by_width[*].*_pct` 是**百分数**
# （`8.3441`），而 `rows[*].measured_gross_annual` 是**分数**（`0.0481`）——
# 同一个 artifact、同一个量、**差 100 倍**（第 182 层：生产者侧一条换算都没有，
# 守卫全在产物字段上，而「这字段是分数还是百分数」在**消费那一刻**无人负责）。
# ⇒ 下面每一行都逐个点名取数，**没有一个数是算出来的**（第 109 层）。
#
# 🚨 **`by_width` 一行必须 JOIN 两族，按 `n` 键，不按位置**：
#   · `account_annual_ceiling_by_width[*]` 有 `abs_gross_over_mde`，**没有** `detectable`
#   · `rows[*]` 有 `detectable`，**没有** `abs_gross_over_mde`
# 两族恰好同序，但「恰好同序」是巧合不是契约（第 128 层）。join 不完整时
# `status` 变成 `malformed`，**绝不**把「查不到」塌成 `False`（第 123 层：
# 查不到 ≠ 不存在）。

_D2_GAP_NUMERIC_KEYS = (
    "account_annual_at_50k_pct",
    "gap_to_target_pp",
    "account_annual_at_infinite_capital_pct",
    "gap_to_target_at_infinite_capital_pp",
    "required_gross_at_50k_pct",
    "multiple_needed_at_50k",
)


def _d2_verdict_input(head, rows):
    """投影 `aplus/web/verdict.js::d2LedgerVerdict` **真正读的那几个键**。

    ## 为什么必须多这一层（形状失配，不是命名偏好）

    `d2LedgerVerdict` 吃的是**产物原形**：`headline.account_annual_ceiling_pct.*` /
    `headline.required_gross_at_50k_deliverable` /
    `headline.measured_gross_best?.at_deliverable_width` /
    `headline.multiple_needed_at_50k_deliverable` / `rows[*].measured_gross_over_mde_abs`。

    而本模块对外一律**改名 + 拍平 + 只出百分数**
    （`account_annual_at_50k_pct` / `gap_to_target_pp` / …）⇒ **两个面形状对不上**。

    ⇒ 直接把 API 载荷喂给判决函数，它走 `head?.account_annual_ceiling_pct`
      读到 `undefined` ⇒ `return null` ⇒ **换一个原因仍然是死闸**
      （上一轮的死因是「传了 predictions 没有 headline」；照搬 API 载荷是
      「传了 API 载荷也没有 headline」——症状一样，根因没动）。
    ⇒ 改判决函数或改封存测试**都不许**（那是为让实现好过而改判据，第 91 层）
      ⇒ 只能在这边补一层**窄口投影**。

    ## 三条硬约束

    ① `account_annual_ceiling_pct` **按引用传**（`ceil` 是同一个对象，不是副本）
       ⇒ 结构上不可能出现「API 那个数与判决读的那个数不是同一个」，
         也就不可能值漂移（第 109 层：核对只读字段，不许重算）。
    ② `rows` **收窄成 `{n, measured_gross_over_mde_abs}`**
       ⇒ **不重新暴露分数组**：`rows[*].measured_gross_annual` = 0.0481（分数），
         与 `headline` 族的 4.8107（百分数）**差 100 倍**，
         消费侧混读会把「基准年化 +494%」印出来（第 97/182 层）。
    ③ **只投影判决真的读的键** ⇒ 这是窄口，不是第二份台账
       （第二份台账必然漂移，而两份之间没有任何守卫）。

    缺 `headline` / `rows` ⇒ 返回 `None`，调用方按「没有台账」处理，
    **绝不**返回一个空壳让判决以为有台账（空壳会让 `null` 与「缺口非正」混同）。
    """
    if not isinstance(head, dict):
        return None
    ceil = head.get("account_annual_ceiling_pct")
    if not isinstance(ceil, dict):
        return None

    measured_best = head.get("measured_gross_best")
    return {
        "headline": {
            # 🚨 按引用传，不是 `dict(ceil)`（见约束①）
            "account_annual_ceiling_pct": ceil,
            "required_gross_at_50k_deliverable":
                head.get("required_gross_at_50k_deliverable"),
            "measured_gross_best": {
                # 只留判决读的那一个键；`at_infinite_capital` 不进载荷
                "at_deliverable_width":
                    (measured_best or {}).get("at_deliverable_width"),
            },
            "multiple_needed_at_50k_deliverable":
                head.get("multiple_needed_at_50k_deliverable"),
        },
        # 🚨 收窄成两个键（见约束②）：分数数组不许借这一步回到 API 面
        "rows": [{"n": r.get("n"),
                  "measured_gross_over_mde_abs": r.get("measured_gross_over_mde_abs")}
                 for r in rows if isinstance(r, dict)],
    }


def read_d2_gap(reports_dir):
    """读 D2 缺口台账，**只读字段、不重算**。

    缺档 / 读不出必需字段 ⇒ `status != "ok"` 且每个数值键为 `None`。
    ⚠️ **绝不回落成 0**：0 会被读成「缺口 0，已达标」，而「没有档」与
    「缺口是 0」是相反的结论（第 89 层：拒绝出判决 ≠ 判决为空）。
    """
    blank = {k: None for k in _D2_GAP_NUMERIC_KEYS}
    blank["by_width"] = []
    # 🚨 四条失败路径也必须带这个键（缺键 vs 显式 None 在 JS 里同形，
    # 但在守卫里不同形：`.get("verdict_input")` 会把两者都读成 undefined ⇒
    # 一条「缺档时闸不许假装有台账」的守卫会分不出自己要看的那一格）。
    # 显式 `None` ⇒ 前端 `Object.assign({}, predictions, null)` 静默忽略 ⇒
    # 袋子里没有 `headline` ⇒ `d2LedgerVerdict` 返 `null` ⇒ **闸闭嘴**。
    blank["verdict_input"] = None

    path = Path(reports_dir) / "d2_signal_requirement.json"
    if not path.exists():
        return {"status": "missing", "reason": f"没有 {path.name}", **blank}

    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"status": "unreadable", "reason": f"{type(exc).__name__}: {exc}", **blank}

    head = ledger.get("headline")
    ceil = (head or {}).get("account_annual_ceiling_pct")
    if not isinstance(ceil, dict) or not isinstance(head, dict):
        return {"status": "malformed", "reason": "headline / account_annual_ceiling_pct 缺失", **blank}

    # 六个顶层数：全部**直读**。🚨 `gap_to_target_pp` 读的是
    # `account_annual_ceiling_pct.gap_to_target_at_deliverable_width_and_50k_pp`
    # （`…353`）而**不是** `by_width[0].gap_to_target_at_50k_pp`（`…355`）——
    # 两处差 **2 ulp**，而封存测试用 `==` 逐位钉住后者。
    # ⇒ 读错来源**当场红**，且这是**唯一**能钉住这个 2 ulp 的东西。
    picked = {
        "account_annual_at_50k_pct": ceil.get("at_deliverable_width_and_50k"),
        "gap_to_target_pp": ceil.get("gap_to_target_at_deliverable_width_and_50k_pp"),
        "account_annual_at_infinite_capital_pct": ceil.get("at_infinite_capital"),
        "gap_to_target_at_infinite_capital_pp": ceil.get("gap_to_target_at_infinite_capital_pp"),
        "required_gross_at_50k_pct": head.get("required_gross_at_50k_deliverable"),
        "multiple_needed_at_50k": head.get("multiple_needed_at_50k_deliverable"),
    }
    missing = [k for k, v in picked.items() if not isinstance(v, (int, float))]
    if missing:
        return {"status": "malformed", "reason": f"顶层缺字段: {', '.join(missing)}", **blank}

    # ── by_width：JOIN 两族 ──
    widths = head.get("account_annual_ceiling_by_width")
    rows = ledger.get("rows")
    if not isinstance(widths, list) or not isinstance(rows, list):
        return {"status": "malformed", "reason": "by_width 或 rows 不是列表", **blank}

    detectable_by_n = {}
    for r in rows:
        if isinstance(r, dict) and "n" in r:
            detectable_by_n[r["n"]] = r.get("detectable")

    joined, gaps = [], []
    for w in widths:
        if not isinstance(w, dict) or "n" not in w:
            gaps.append("by_width 里有行缺 n")
            continue
        flag = detectable_by_n.get(w["n"])
        if not isinstance(flag, bool):
            # 🚨 查不到就报 malformed，**不许** `.get(..., False)`
            gaps.append(f"n={w['n']} 在 rows 里没有 bool 型 detectable")
            continue
        joined.append({
            "n": w["n"],
            "is_the_deliverable_width": bool(w.get("is_the_deliverable_width")),
            "account_annual_at_50k_pct": w.get("account_annual_at_50k_pct"),
            "gap_to_target_at_50k_pp": w.get("gap_to_target_at_50k_pp"),
            "account_annual_at_infinite_capital_pct": w.get("account_annual_at_infinite_capital_pct"),
            "gap_to_target_at_infinite_capital_pp": w.get("gap_to_target_at_infinite_capital_pp"),
            "measured_gross_annual_pct": w.get("measured_gross_annual_pct"),
            "measured_net_at_50k_pct": w.get("measured_net_at_50k_pct"),
            "friction_at_50k_pct": w.get("friction_at_50k_pct"),
            "abs_gross_over_mde": w.get("abs_gross_over_mde"),
            # 🚨 改名：`rows[*].detectable` → `is_detectable`。
            # 同 artifact 内它与 `abs_gross_over_mde` / `measured_gross_over_mde_abs`
            # 三份键名指向同一个数 ⇒ 命名债已登记，本轮**不改产物**（改了会改产物本身）。
            "is_detectable": flag,
        })

    if gaps:
        return {"status": "malformed", "reason": "；".join(gaps), **blank}
    if not joined:
        return {"status": "malformed", "reason": "join 后没有任何一行", **blank}

    # 判据必须自报口径：没跟数字一起离开脚本的口径，下一个人就会拿错宽度去比。
    caliber = ledger.get("caliber") or {}
    return {
        "status": "ok",
        **picked,
        "by_width": joined,
        "verdict_input": _d2_verdict_input(head, rows),
        "deliverable_width": ceil.get("deliverable_width"),
        "supremum_width": ceil.get("width"),
        "equity_weight_is_full_position_judgment_caliber": caliber.get("equity_weight"),
        "account_leg_arm": (ledger.get("account_leg") or {}).get("arm"),
        "branch": ledger.get("branch"),
    }
