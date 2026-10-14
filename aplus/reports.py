"""报告持久化：预测/验证/配置的落盘与历史读取。

目录结构（reports_dir，默认 aplus/data/reports）：
    predictions/pred_{date}.parquet   单文件含 up+down 两段（direction 列区分）
    verification.parquet              全部验证判定（追加式，唯一调参依据）
    allocation/alloc_{date}.json      每日资产配置方案
"""
import json
from pathlib import Path

import pandas as pd


def _pred_path(reports_dir, pred_date: str) -> Path:
    d = Path(reports_dir) / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"pred_{pred_date}.parquet"


def save_predictions(reports_dir, pred_date: str, up: pd.DataFrame, down: pd.DataFrame) -> None:
    both = pd.concat([up, down], ignore_index=True)
    both["pred_date"] = pred_date
    both.to_parquet(_pred_path(reports_dir, pred_date), index=False)


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
                anchored = ver[ver["exit_date"].notna() & (ver["exit_date"].astype(str) != "")]
                judged = set(anchored["pred_date"].dropna().astype(str))
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
    _alloc_path(reports_dir, date).write_text(
        json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_allocation(reports_dir, date: str):
    path = _alloc_path(reports_dir, date)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
