"""守卫：`signal_id` 的**来源**不许被伪造（2026-10-21 修复）。

## 这份文件在守什么

`aplus/verify.py::verify_predictions` 曾经有一个 `signal_id` **入参**：
在「整批没有该列」时用它补上；而调用点
（`scripts/run_daily.py::_judge_one_day`）传的是
`current_signal_id(reports_dir)` —— **当前**权重的 id。

⇒ 于是：早于 `signal_id` 列落地时写下的**旧名单**
（那份预测文件里**根本没有**这一列）被盖上了**重判当天**的 id
⇒ 「这份名单由该信号产生」这个**证据**变成了**编造**
⇒ D1 的**独立成交窗口数（W）**被高估一倍（屏上 2/10，真值 1/10）。

预注册：`docs/PREREG_FIX_RESIGNAL_ID_2026-10-21.md` §4（G-A…G-E）+ §3。

## 五条守卫

| 守卫 | 层 | 钉住什么 |
|---|---|---|
| **G-A** | 接线（AST） | 重判路径**不许**出现 `signal_id=` 实参；**且** `verify_predictions` 的**签名**里不许有这个参数（不留旁路） |
| **G-B** | 行为 | 预测文件**自带**标签 ⇒ 重判后**仍是它自己的**那一列 |
| **G-C** | 行为 | 预测文件**没有**标签（= 2026-10-10 的真实情形）⇒ 重判后那一批**不许**有标签 ⇒ **不可归属** |
| **G-D** | 对账（只读**真实底稿**） | ① 生成器写出的每一行**必须**与**它自己的预测文件**逐行对得上；② 生产底稿的每一行**必须**与它的预测文件对得上 —— **除非**那一行在**待重判**的日期里（下一轮日跑会重写它） |
| **G-E** | 口径 | `aplus/signal_scope.py` 的**四态**与**判定顺序**不许被这次修改动过 |

## 判据从哪读（**不许手打**，预注册 §2）

| 量 | 唯一真相源 | 本文件怎么读 |
|---|---|---|
| 归属**四态**与判定顺序 | `aplus/signal_scope.py::scope_ledger` + `STATUS_*` / `BASIS_*` | **只调用**（引用模块自己的常量，不在测试里重写一份四态） |
| **当前**信号 id / 当前权重指纹 | `aplus/signal_scope.py::resolve_attribution_context` | **只调用** |
| 某一行**自己**的标签 | **该 `pred_date` 的预测文件自带的 `signal_id` 列** | 逐行对拍（G-D） |
| 标签列的**列名** | `aplus/reports.py::SIGNAL_ID_COLUMN` ≡ `aplus/verify.py::SIGNAL_ID_COLUMN` | 只调用 |

## ⚠️ G-D 的「有据」三档（**证据强度递减**）

对底稿/重判输出的每一行，取**它自己的**预测文件：

1. 文件**自带** `signal_id` 列 ⇒ 该行的标签**必须逐位等于**文件里那一行
   （标签**不许丢**、也**不许被换成别的值**）；
2. 否则，文件自带 `weights_hash` 列且它 == **当前**权重指纹
   ⇒ 这份名单是**当前权重**排出来的 ⇒ 该行的标签（若存在）**必须等于当前信号 id**；
3. 否则（既没有标签列、也没有当前权重的证据）⇒ 该行**必须没有标签**
   ⇒ 不可归属（`aplus/signal_scope.py` 判「不可归属」，**不是**「没有排序力」）。

⇒ 第 ③ 档正是 2026-10-10 那份名单的情形：**没有任何正确来源**
⇒ 它**必须**没有标签 ⇒ 预注册 R3 明确禁止手工贴 `da18a166`（那是**编造标签**）。
"""
from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"
PRED_DIR = REPORTS / "predictions"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import signal_scope
from aplus.reports import (SIGNAL_ID_COLUMN, append_verification,
                            load_predictions, save_predictions)
from aplus.signal_scope import (resolve_attribution_context, scope_ledger)
from aplus.verify import verify_predictions

#: 沙盒重判时写在 `verified_on` 上的日期（**只作落章**，不参与任何判据）。
REJUDGE_ON = "2026-10-22"

#: 沙盒重判**最多**做几天（每个待判日要读约 300 只票的日线 ⇒ 不便宜）。
MAX_SANDBOX_DAYS = 2


# ────────────────────────── 量具 ──────────────────────────

def _run_daily():
    """按路径 import 生产入口（**不**照抄它的 import 写法，第 24 层的形状）。"""
    spec = importlib.util.spec_from_file_location(
        "_run_daily_signal_id_guard", ROOT / "scripts" / "run_daily.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verify_predictions_calls(path: Path) -> list[ast.Call]:
    """找出 `verify_predictions(...)` 的**调用点**。

    ⚠️ **锚在语法位置**（`ast.Call` 的被调用名），**不**用子串
    （子串会被注释满足 ⇒ 第 13 层）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "verify_predictions":
            calls.append(node)
    return calls


def _label_of(value) -> str | None:
    """把一格标签归一成字符串；缺失/空 ⇒ None。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _prediction_frame(pred_date: str) -> pd.DataFrame | None:
    path = PRED_DIR / f"pred_{pred_date}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _file_evidence(pred_date: str) -> dict:
    """一份预测文件给它的每一行提供的**标签证据**。"""
    frame = _prediction_frame(pred_date)
    if frame is None:
        return {}
    out = {}
    for _, row in frame.iterrows():
        code = str(row["code"])
        out[code] = {
            "signal_id": (_label_of(row[SIGNAL_ID_COLUMN])
                          if SIGNAL_ID_COLUMN in frame.columns else None),
            "weights_hash": (_label_of(row["weights_hash"])
                             if "weights_hash" in frame.columns else None),
        }
    return out


def _reconcile(rows: pd.DataFrame, ctx) -> list[dict]:
    """逐行对账：返回**没有据**的那些行（空列表 = 全部对得上）。"""
    bad = []
    evidence_cache: dict[str, dict] = {}
    for _, row in rows.iterrows():
        pred_date = str(row["pred_date"])
        code = str(row["code"])
        row_label = _label_of(row[SIGNAL_ID_COLUMN]) if SIGNAL_ID_COLUMN in rows.columns else None
        if pred_date not in evidence_cache:
            evidence_cache[pred_date] = _file_evidence(pred_date)
        ev = evidence_cache[pred_date].get(code)
        if ev is None:
            # 该 pred_date 的预测文件里没有这一只 ⇒ 底稿多出来的一行 ⇒ 记下来
            bad.append({"pred_date": pred_date, "code": code, "why": "预测文件里没有这一只",
                        "row_label": row_label, "file_label": None})
            continue
        if ev["signal_id"] is not None:
            if row_label != ev["signal_id"]:
                bad.append({"pred_date": pred_date, "code": code,
                            "why": "标签与预测文件自带的标签**逐位不相等**",
                            "row_label": row_label, "file_label": ev["signal_id"]})
        elif (ev["weights_hash"] is not None
              and ctx.weights_fingerprint is not None
              and ev["weights_hash"] == ctx.weights_fingerprint):
            # 这份名单是**当前权重**排出来的 ⇒ 当前信号 id 是有据的
            if row_label is not None and row_label != ctx.signal_id:
                bad.append({"pred_date": pred_date, "code": code,
                            "why": "文件是当前权重排的，标签却不是当前信号 id",
                            "row_label": row_label, "file_label": ctx.signal_id})
        else:
            # 既没有标签列、也没有当前权重的证据 ⇒ 这一行**必须没有标签**
            if row_label is not None:
                bad.append({"pred_date": pred_date, "code": code,
                            "why": "预测文件**没有任何**能支撑这个标签的证据",
                            "row_label": row_label, "file_label": None})
    return bad


def _rejudge_in_sandbox(store, tmp_dir: Path, pred_date: str):
    """在**临时目录**里走**生产**重判路径（`run_daily._judge_one_day`）。

    ⚠️ **只读生产**：预测文件**复制**进临时目录，产物**只写临时目录**
    ⇒ 生产底稿一个字节都不动（预注册 §5-3「不许动今晚那批产物」）。
    """
    RD = _run_daily()
    src = PRED_DIR / f"pred_{pred_date}.parquet"
    if not src.exists():
        return None
    (tmp_dir / "predictions").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp_dir / "predictions" / f"pred_{pred_date}.parquet")
    ok = RD._judge_one_day(store, tmp_dir, pred_date, REJUDGE_ON)
    out_path = tmp_dir / "verification.parquet"
    if not ok or not out_path.exists():
        return None
    return pd.read_parquet(out_path)


# ────────────────────────── G-A（接线层） ──────────────────────────

def test_the_rejudge_call_site_never_passes_a_signal_id():
    """🚨 **重判路径不许再喂「当前信号 id」**（预注册 R1）。

    判据落在**语法位置**上：`ast.Call` 的被调用名 == `verify_predictions`
    ⇒ 它的 `keywords` 里**不许**有 `signal_id`
    ⇒ 改了变量名骗不过（那是「只钉存在不钉性质」）。

    ⚠️ **防空真**：必须真的找到调用点，否则本守卫会**恒真**。
    """
    calls = _verify_predictions_calls(ROOT / "scripts" / "run_daily.py")
    assert calls, (
        "探测失效：`scripts/run_daily.py` 里一个 `verify_predictions` 调用点都没找到"
        " ⇒ 本守卫恒真（守卫失效的样子和「守住了」一模一样）")
    for call in calls:
        kwargs = sorted(kw.arg for kw in call.keywords if kw.arg)
        assert "signal_id" not in kwargs, (
            f"重判路径不许把「当前信号 id」喂进 `verify_predictions`"
            f"（实参名 {kwargs}）⇒ 那会给**旧名单**盖上**重判当天**的标签"
            f" ⇒ 独立成交窗口数被高估一倍")


def test_verify_predictions_has_no_signal_id_parameter_at_all():
    """🚨 **入参必须不存在**（预注册 R2 的「不留旁路」那一支）。

    只删掉调用点上的实参（预注册 R1）**不够**：
    只要入参还在，下一个调用点（或将来的人）还会传 ⇒ 缺陷**原样复发**。

    ⇒ 「只校验不补」也**不合格**：那一支在「整批没有该列」时**恒真**
    ⇒ 它在**恰恰出缺陷的那一格**没有判别力。
    """
    tree = ast.parse((ROOT / "aplus" / "verify.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "verify_predictions")
    names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    assert "signal_id" not in names, (
        f"`verify_predictions` 的签名里还有 `signal_id`（{names}）"
        f" ⇒ 留了一条「调用方声明一个标签」的通道 ⇒ 缺陷会原样复发")


# ────────────────────────── G-B（行为层） ──────────────────────────

def test_a_prediction_file_that_carries_a_signal_id_keeps_it_through_the_rejudge(tmp_path):
    """🚨 **自带标签的文件 ⇒ 重判后**仍是它自己的**标签**（预注册 G-B）。

    这条守卫**今天就是绿的**（旧实现的「已有该列就保留」已经满足）
    ⇒ 它防的是**别的实现**把这条路径改成「无条件覆盖」
    ⇒ 必须配变异（M2/M3）才有牙。
    """
    up = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"],
                       "direction": ["up"], "score": [2.0]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"],
                         "direction": ["down"], "score": [-2.0]})
    save_predictions(tmp_path, "2026-10-21", up, down, signal_id="OLD12345")
    loaded_up, loaded_down = load_predictions(tmp_path, "2026-10-21")
    preds = pd.concat([loaded_up, loaded_down], ignore_index=True)
    bars = pd.DataFrame({"code": ["600000.SH", "600001.SH"],
                         "close": [11.0, 9.0], "prev_close": [10.0, 10.0]})

    out = verify_predictions(preds, bars, default_cost=0.003)
    assert SIGNAL_ID_COLUMN in out.columns
    assert set(out[SIGNAL_ID_COLUMN]) == {"OLD12345"}

    # 走完整的重判路径（写进底稿）也必须保住
    append_verification(tmp_path, out)
    ledger = pd.read_parquet(tmp_path / "verification.parquet")
    assert SIGNAL_ID_COLUMN in ledger.columns
    assert set(ledger[SIGNAL_ID_COLUMN]) == {"OLD12345"}


# ────────────────────────── G-C（行为层） ──────────────────────────

def test_the_real_legacy_prediction_file_produces_unattributable_rows(rejudged_1010):
    """🚨 **没有标签列的预测文件 ⇒ 重判后那一批不许有标签**（预注册 G-C）。

    🚨 **这条才是抓今天这个缺陷的那条**：旧实现会盖上 `0df5828c` ⇒ 红。

    夹具用的是**真实**的 `pred_2026-10-10.parquet`（那份文件里
    **确实没有** `signal_id` 列）走**生产**重判路径
    ⇒ 不是「用夹具注入的字段」测出来的。
    """
    rows = rejudged_1010
    assert rows is not None and len(rows) > 0, "真实底稿重判没产出行 ⇒ 夹具失效"
    if SIGNAL_ID_COLUMN in rows.columns:
        assert rows[SIGNAL_ID_COLUMN].isna().all() or (
            rows[SIGNAL_ID_COLUMN].astype("string").str.strip() == "").all(), (
            "2026-10-10 那份名单**没有任何**能支撑标签的来源"
            " ⇒ 重判后**不许**出现标签（盖上去就是编造）")
    # ⇒ 不可归属（`aplus/signal_scope.py` 判「不可判定」，**不是**「没有排序力」）
    ctx = resolve_attribution_context(REPORTS)
    scoped = scope_ledger(rows, ctx.signal_id, ctx.weights_fingerprint)
    assert scoped.rows_matched == 0
    assert len(scoped.frame) == 0


# ────────────────────────── G-D（对账层，只读真实底稿） ──────────────────────────

def test_the_generator_writes_only_labels_backed_by_the_prediction_file(sandbox_rejudge):
    """🚨 **生成器写出的每一行**必须与**它自己的预测文件**逐行对得上。

    走**生产**重判路径（`run_daily._judge_one_day`），在**临时目录**里
    ⇒ 生产底稿一个字节都不动。

    ⚠️ 这一条是 M1（还原调用点的实参）能杀掉的**对账层**证据：
    接线层的守卫（G-A）看得见**实参表**，但看得不见**产物**。
    """
    rows, judged_dates = sandbox_rejudge
    assert judged_dates, (
        "一个预测日都没能重判 ⇒ 本守卫**此刻测不到任何东西**（恒真）"
        " ⇒ 查两件事：① 库的数据末端（`scripts/run_daily.py::_store_max_date`）"
        " ② `pending_prediction_dates` 是不是空集"
        "（空数据不会让测试变红，它会让测试恒真）")
    ctx = resolve_attribution_context(REPORTS)
    bad = _reconcile(rows, ctx)
    assert not bad, (
        f"生成器写出的行与**它自己的预测文件**对不上（{len(bad)} 行）："
        f"{bad[:5]} ⇒ 「这份名单由该信号产生」的证据是**编造**的")


def test_every_ledger_row_reconciles_with_its_prediction_file(pending_dates):
    """🚨 **生产底稿**的每一行必须与**它的预测文件**对得上。

    ⚠️ **例外只有一个**：那一行在**待重判**的日期里
    ⇒ 下一轮日跑会**重写**它 ⇒ 它是**待修产物的债务**，不是新缺陷。

    ⇒ 判据是「**除待重判的行之外，全部对得上**」：
    - 今天：2026-10-10 那 20 行仍带着旧标签（它**在待判集合里**）⇒ 绿；
    - 下一轮日跑重判它之后：它没有标签了 ⇒ 仍然绿；
    - 🚨 若某天出现一个**不会被重写**的日期带着没据的标签 ⇒ **红**
      （那说明缺陷在**已经结清**的日期上留下了永久的假证据）。

    ⚠️ **防空真**：末尾用一次**注入**证明对账器真的会喊
    （空数据不会让测试变红，它会让测试恒真）。
    """
    ledger = pd.read_parquet(REPORTS / "verification.parquet")
    ctx = resolve_attribution_context(REPORTS)
    bad = _reconcile(ledger, ctx)
    hard = [b for b in bad if b["pred_date"] not in pending_dates]
    assert not hard, (
        f"生产底稿里有 {len(hard)} 行的标签**没有据**，且这些日期"
        f"**不在待判集合里** ⇒ 下一轮日跑**不会**修好它们"
        f" ⇒ 那是永久的假证据：{hard[:5]}"
        f"（另有 {len(bad) - len(hard)} 行在待判日期里 ⇒ 下一轮日跑会重写它们）")

    # ── 探针自证：对账器**确实会喊**（防空真）────────────────────────
    settled = ledger[~ledger["pred_date"].astype(str).isin(pending_dates)]
    assert len(settled), "底稿里一行已结清的日期都没有 ⇒ 探针无处落脚"
    probe = ledger.copy()
    target = int(settled.index[0])
    if SIGNAL_ID_COLUMN not in probe.columns:
        probe[SIGNAL_ID_COLUMN] = pd.NA
    probe.loc[target, SIGNAL_ID_COLUMN] = "DEADBEEF-FAKE"
    flagged = _reconcile(probe, ctx)
    assert any(f["code"] == str(probe.loc[target, "code"])
               and f["pred_date"] == str(probe.loc[target, "pred_date"])
               for f in flagged), (
        "注入一个没据的标签，对账器**没喊** ⇒ 本守卫恒真（失效的样子和「守住了」一样）")


# ────────────────────────── G-E（口径层） ──────────────────────────

def test_the_four_attribution_states_are_reachable_exclusive_and_ordered():
    """🚨 `scope_ledger` 的**四态**不许被这次修改动过（预注册 G-E）。

    判据用**行为**而不是「diff」：
    - 四个状态**各自可达**（四个构造出来的帧）；
    - 四态**互斥**（四个状态两两不同 ⇒ 没有塌成两态/三态）；
    - **判定顺序**被钉住：`signal_id` 优先于 `weights_hash`
      ⇒ 底稿**有** `signal_id` 列但**没有一行**匹配时，
      判的是 `NO_MATCHING_SIGNAL`（**不是**回落去 `weights_hash` 那一支）。

    ⚠️ **只调用模块自己的常量**（`signal_scope.STATUS_*`），
    **不在测试里重写一份四态**（预注册 §2）。
    """
    sig = "abc12345"
    wh = "c2178335be6d"

    # ① `signal_id` 列 + 有一行匹配 ⇒ OK
    ok_frame = pd.DataFrame({"code": ["a"], SIGNAL_ID_COLUMN: [sig],
                              "weights_hash": [wh]})
    # ② 没有 `signal_id` 列，但 `weights_hash` 逐行匹配 ⇒ ATTRIBUTED_BY_WEIGHTS_HASH
    wh_frame = pd.DataFrame({"code": ["a"], "weights_hash": [wh]})
    # ③ 有 `signal_id` 列但**没有一行**匹配（`weights_hash` 反而匹配）
    #    ⇒ 判 `NO_MATCHING_SIGNAL` ⇒ **不是**回落去 `weights_hash` 那一支（顺序）
    order_frame = pd.DataFrame({"code": ["a"], SIGNAL_ID_COLUMN: ["other"],
                                 "weights_hash": [wh]})
    # ④ 两列都没有 ⇒ 无任何归属证据
    none_frame = pd.DataFrame({"code": ["a"]})

    got = {
        scope_ledger(ok_frame, sig, wh).status: "①",
        scope_ledger(wh_frame, sig, wh).status: "②",
        scope_ledger(order_frame, sig, wh).status: "③",
        scope_ledger(none_frame, sig, wh).status: "④",
    }
    assert got[signal_scope.STATUS_OK] == "①", "① `signal_id` 逐行匹配 ⇒ 必须是 OK"
    assert got[signal_scope.STATUS_ATTRIBUTED_BY_WEIGHTS_HASH] == "②", (
        "② 没有 `signal_id` 列 + `weights_hash` 匹配 ⇒ 必须是 ATTRIBUTED_BY_WEIGHTS_HASH")
    assert got[signal_scope.STATUS_NO_MATCHING_SIGNAL] == "③", (
        "③ 有 `signal_id` 列但没有一行匹配 ⇒ 必须是 NO_MATCHING_SIGNAL"
        "（**不许**回落去 `weights_hash` 那一支 ⇒ 判定顺序被钉住）")
    assert got[signal_scope.STATUS_MISSING_SIGNAL_ID_COLUMN] == "④", (
        "④ 两列都没有 ⇒ 必须是 MISSING_SIGNAL_ID_COLUMN")
    assert len(got) == 4, f"四态塌成了 {len(got)} 态（{got}）"


# ────────────────────────── 夹具 ──────────────────────────

@pytest.fixture(scope="module")
def store():
    from aplus.store import LocalStore
    return LocalStore(ROOT / "data")


@pytest.fixture(scope="module")
def pending_dates() -> set[str]:
    from aplus.reports import pending_prediction_dates
    return set(pending_prediction_dates(REPORTS))


@pytest.fixture(scope="module")
def rejudged_1010(tmp_path_factory, store):
    """用**当前代码**在临时目录里重判 2026-10-10（真实文件 + 真实库）。"""
    tmp = tmp_path_factory.mktemp("rejudge_1010")
    rows = _rejudge_in_sandbox(store, tmp, "2026-10-10")
    assert rows is not None, (
        "2026-10-10 重判没产出行 ⇒ 夹具失效（是数据没就绪，还是代码坏了？）")
    return rows


@pytest.fixture(scope="module")
def sandbox_rejudge(tmp_path_factory, store, pending_dates):
    """把**待判**的预测日在临时目录里重判一遍（生产路径，生产底稿不动）。

    ⚠️ **候选集不止「待判日」**：待判日可能因为**数据还没就绪**而判不了
    （实测 2026-10-20 / 10-21 就是如此：库里还没有它们的次日 bar）
    ⇒ 那时本守卫会**测不到任何东西**（空数据 ⇒ 恒真）
    ⇒ 候选集往后接「底稿里已结清的日期」与「盘上的预测文件」
    ⇒ 只要有**任何一天**能重判，对账就有真东西可查。
    """
    tmp = tmp_path_factory.mktemp("sandbox_rejudge")
    ledger_dates = []
    ledger_path = REPORTS / "verification.parquet"
    if ledger_path.exists():
        ledger_dates = sorted(set(pd.read_parquet(
            ledger_path, columns=["pred_date"])["pred_date"].astype(str)))
    file_dates = sorted((p.stem.removeprefix("pred_")
                         for p in PRED_DIR.glob("pred_*.parquet")), reverse=True)
    candidates = (sorted(pending_dates)
                  + [d for d in reversed(ledger_dates) if d not in pending_dates]
                  + [d for d in file_dates if d not in pending_dates])

    frames = []
    judged = []
    for pred_date in candidates:
        if len(judged) >= MAX_SANDBOX_DAYS:
            break
        rows = _rejudge_in_sandbox(store, tmp, pred_date)
        if rows is None or not len(rows):
            continue
        frames.append(rows)
        judged.append(pred_date)
    if not frames:
        return pd.DataFrame(), judged
    return pd.concat(frames, ignore_index=True), judged
