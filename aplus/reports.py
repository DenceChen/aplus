"""报告持久化：预测/验证/配置的落盘与历史读取。

目录结构（reports_dir，默认 aplus/data/reports）：
    predictions/pred_{date}.parquet   单文件含 up+down 两段（direction 列区分）
    verification.parquet              全部验证判定（追加式，唯一调参依据）
    allocation/alloc_{date}.json      每日资产配置方案
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from .verify import UNJUDGEABLE_REASON

# 预测文件里那一列的名字：它记的是「本文件是用哪组权重排出来的」。
WEIGHTS_HASH_COLUMN = "weights_hash"


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


def save_predictions(reports_dir, pred_date: str, up: pd.DataFrame, down: pd.DataFrame,
                    weights: dict | None = None) -> None:
    """落盘预测榜单，并**记下自己用了哪组权重**。

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
    """
    both = pd.concat([up, down], ignore_index=True)
    both["pred_date"] = pred_date
    fp = weights_fingerprint(weights)
    if fp is not None:
        both[WEIGHTS_HASH_COLUMN] = fp
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
