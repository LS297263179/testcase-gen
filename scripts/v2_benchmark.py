"""V2 Step 11 S6 Benchmark Runner —— 独立 CLI 进程（任务书《S6 正式开发》）。

一句话：把 bench-v0.1 正式 Benchmark Case 依次灌进 V2 原有 Runtime（零修改），
再用 S3 硬指标 + S5 语义指标评价，产出可供 S7 消费的 runset 快照。

★ 三条硬纪律（设计文档 §12 + 任务书 §一）：
  1. **只能以独立进程运行**：进程入口第一件事就是 core.v2.db.set_v2_db_path()，
     把全部读写钉在 benchmark/data/benchmark_v2.db 上；严禁在 Flask/长驻进程内调用。
  2. **生产库零污染**：解析后的 DB 路径若等于 data/data_v2.db → 立即 exit 3。
  3. **Benchmark 数据只读**：只读 benchmark/{cases,gold,manifests}，只写 benchmark/runsets/。

★ Runtime 调用原则（任务书 §六）：client=None 传入 run_v2_pipeline，
  让 Runtime 继续用自己的 client_factory 构建 generate/review 双客户端，行为与产品路径完全一致；
  S5 语义评价另建一个客户端（复用现有 purpose，不新增、不改 client_factory）。

★ 诚实降级（任务书 §三/§四/§十八）：非 COMPLETED 的 case 只跑 S3 观测（其质量格按 S3 契约
  自动为 None），绝不调用 S5 真实 LLM，也绝不伪造 semantic score。

用法：
  python scripts/v2_benchmark.py --dry-run                      # 只出执行计划（零 DB / 零 LLM / 零写盘）
  python scripts/v2_benchmark.py                                # 正式 runset（fresh benchmark DB）
  python scripts/v2_benchmark.py --case bc_01_login --repeat 2  # 排障子集
  python scripts/v2_benchmark.py --reuse-db                     # 调试用：保留历史 benchmark DB
  python scripts/v2_benchmark.py --keep-db                      # A2：不清库，但要求库文件已存在
  python scripts/v2_benchmark.py --resume <runset_id>           # A2 断点续跑（隐含 --keep-db）

★ A2 断点续跑语义（MVP-A，零契约变更）：
  - 复用既有 runset 目录与 id，不再生成新时间戳 runset；
  - 单元状态以**磁盘 case 文件为唯一事实源**：reusable（跳过，零 LLM）/ rerun（旧文件转存
    `.stale-*` 后重跑）/ missing（正常跑）；
  - 复用门禁六条件（任一不满足即降级 rerun，绝不"用本轮配置冒充历史单元"）：
    ① 文件存在且可解析 ② case s6_schema 匹配 ③ status==completed ④ case_fingerprint 与当前
    case/gold/requirement 字节摘要一致 ⑤ 该单元 run_id 在目标 DB 仍可解析出 GenerationConfig
    且关键配置一致 ⑥ 本轮 pass header 与既有 pass header 的 git_commit / 数据集 / prompt / runner 版本逐项相等；
  - 收尾聚合一律**从磁盘读回全部单元**重建，且要求所有单元的 GenerationConfig 完全一致，
    否则 fail-closed 不出 runset（取代旧的"取本轮第一个单元"）；
  - `_COMPLETE.json` 只在 `planned == written == 磁盘可解析单元数` 且索引引用文件全部存在时写。

退出码：0=全 COMPLETED / 1=存在非 COMPLETED / 2=参数错误（含 resume 目标不存在、已完整、
与 --case/--repeat/--fresh-db/--reuse-db 冲突、跨 commit 或来源漂移被拒）/
3=DB 与生产库保护（含 resume 时库文件不存在）/ 4=数据集校验失败 / 5=落盘或敏感自检失败
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 允许 `python scripts/v2_benchmark.py` 直接运行：把项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.schemas import SourceType  # noqa: E402
from core.v2 import repository as repo  # noqa: E402
from core.v2.bootstrap import V2BootstrapError, ensure_v2_ready  # noqa: E402
from core.v2.client_factory import build_llm_client  # noqa: E402
from core.v2.db import get_v2_db_path, set_v2_db_path, v2_read_conn  # noqa: E402
from core.v2.eval import runner_lib as rl  # noqa: E402
from core.v2.eval.metrics_hard import evaluate_hard_metrics  # noqa: E402
from core.v2.eval.metrics_soft import evaluate_soft_metrics, to_payload  # noqa: E402
from core.v2.eval.schema import (  # noqa: E402
    BenchmarkCase,
    BenchmarkGold,
    BenchmarkManifest,
    BenchmarkRunStatus,
    BenchmarkValidationError,
    load_benchmark_suite,
)
from core.v2.ingestion import normalize_text  # noqa: E402  （与 Runtime 同一归一化口径）
from core.v2.runtime import run_v2_pipeline  # noqa: E402

logger = logging.getLogger("v2.benchmark")

# ============================================================
# 默认值与退出码（冻结）
# ============================================================

BENCHMARK_ROOT = _PROJECT_ROOT / "benchmark"
DEFAULT_DB = "benchmark/data/benchmark_v2.db"
DEFAULT_RUNSETS = "benchmark/runsets"
DEFAULT_MANIFEST = "bm-bench-v0-1"
DEFAULT_USERNAME = "cli_benchmark"

EXIT_OK = 0
EXIT_NOT_ALL_COMPLETED = 1
EXIT_USAGE = 2
EXIT_DB_GUARD = 3
EXIT_DATASET = 4
EXIT_WRITE_FAILURE = 5

COMPLETE_MARKER = "_COMPLETE.json"


class BenchmarkDbUnavailable(RuntimeError):
    """A2：`--keep-db` / `--resume` 要求既有 benchmark DB，但库文件不存在（退出码同 3）。"""


# ============================================================
# 数据结构
# ============================================================


@dataclass
class RunPlan:
    """一个 (case, repeat) 执行单元；顺序 = manifest.cases 原顺序 × repeat 升序。"""

    case: BenchmarkCase
    gold: BenchmarkGold
    repeat_index: int


@dataclass
class CaseContext:
    """跑单个 case 所需的不变输入（避免 run_case 参数爆炸）。"""

    manifest: BenchmarkManifest
    dataset_root: Path
    output_root: Path
    digests: dict[str, str]
    username: str
    user_id: str = ""
    s5_enabled: bool = True


# ============================================================
# CLI
# ============================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="v2_benchmark",
        description="V2 Step 11 S6 Benchmark Runner（独立进程 + 独立 benchmark DB + runset 快照）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0=全部 COMPLETED；1=存在非 COMPLETED；2=参数错误；"
            "3=DB/bootstrap/生产库保护；4=Benchmark 数据集校验失败；5=runset 写入或敏感自检失败"
        ),
    )
    p.add_argument("--manifest", default=DEFAULT_MANIFEST, help=f"manifest_id（默认 {DEFAULT_MANIFEST}）")
    p.add_argument("--dataset-root", default=str(BENCHMARK_ROOT), help="Benchmark 数据根目录（只读）")
    p.add_argument("--output-dir", default=DEFAULT_RUNSETS, help="runset 输出根目录")
    p.add_argument("--db", default=DEFAULT_DB, help="benchmark DB 路径（禁止等于生产库）")
    db_life = p.add_mutually_exclusive_group()
    db_life.add_argument("--fresh-db", dest="db_mode", action="store_const", const="fresh", help="干净初始化（默认）")
    db_life.add_argument("--reuse-db", dest="db_mode", action="store_const", const="reuse", help="调试：沿用现有库")
    db_life.add_argument(
        "--keep-db", dest="db_mode", action="store_const", const="keep", help="A2：不清库且要求库已存在"
    )
    db_life.set_defaults(db_mode=None)
    p.add_argument("--case", action="append", default=None, help="只跑指定 case（可重复给出；必须是 manifest 成员）")
    p.add_argument("--repeat", type=int, default=None, help="覆盖 manifest.repeat（仅运行参数，不改数据集）")
    p.add_argument("--resume", default=None, metavar="RUNSET_ID", help="A2 断点续跑：复用既有 runset 目录，只补缺口")
    p.add_argument("--dry-run", action="store_true", help="只出执行计划：不建 DB / 不调 Runtime / 不调 LLM / 不写盘")
    p.add_argument("--username", default=DEFAULT_USERNAME, help="benchmark DB 内的 V2 user（查不到则自动创建）")
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    return p


def resolve_db_mode(args: argparse.Namespace) -> str:
    """DB 生命周期模式：显式指定优先；未指定时 resume → keep，否则 → fresh。"""
    if args.db_mode:
        return args.db_mode
    return "keep" if args.resume else "fresh"


def validate_args(args: argparse.Namespace) -> str | None:
    """argparse 之后的组合校验；返回错误串（None=通过）。"""
    if args.repeat is not None and args.repeat < 1:
        return f"--repeat 必须 ≥1（当前 {args.repeat}）"
    if args.resume:
        if args.case:
            return "--resume 不接受 --case（续跑计划由既有 runset 与 manifest 决定，改子集会破坏完整性）"
        if args.repeat is not None:
            return "--resume 不接受 --repeat（同上）"
        if args.db_mode in ("fresh", "reuse"):
            return f"--resume 必须使用 --keep-db 语义，不能与 --{args.db_mode}-db 同用"
    args.db_mode = resolve_db_mode(args)
    return None


# ============================================================
# 执行计划
# ============================================================


def resolve_repeat(manifest: BenchmarkManifest, override: int | None) -> int:
    return override if override is not None else max(1, int(manifest.repeat or 1))


def build_plan(
    suite, manifest_id: str, case_filter: list[str] | None, repeat_override: int | None
) -> tuple[RunPlan, ...]:
    """按 manifest.cases 原顺序展开执行计划；case_filter 越界 → ValueError（CLI 退出码 2）。"""
    manifest = suite.manifests.get(manifest_id)
    if manifest is None:
        raise ValueError(f"manifest 不存在: {manifest_id}（已知: {sorted(suite.manifests)}）")
    keep = list(case_filter or [])
    unknown = [cid for cid in keep if cid not in manifest.cases]
    if unknown:
        raise ValueError(f"--case 不属于 manifest {manifest_id}: {unknown}")
    wanted = keep if keep else list(manifest.cases)
    repeat = resolve_repeat(manifest, repeat_override)
    plan: list[RunPlan] = []
    for cid in wanted:
        case = suite.cases[cid]
        gold = suite.golds[cid]
        for r in range(1, repeat + 1):
            plan.append(RunPlan(case=case, gold=gold, repeat_index=r))
    return tuple(plan)


def precheck_requirement(case: BenchmarkCase, dataset_root: Path) -> tuple[str, str | None]:
    """输入预检（任务书 §九）：返回 (归一化正文, 失败原因)。失败时不得调用 Runtime。

    归一化复用 core.v2.ingestion.normalize_text（与 Runtime 完全同口径），
    因此"预检判空"与"Runtime 实际拿到空文本"不会出现分歧。
    """
    path = dataset_root / "cases" / case.requirement_file
    if not path.is_file():
        return "", f"requirement_file 不存在: cases/{case.requirement_file}"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"requirement_file 读取失败: {type(exc).__name__}"
    text = normalize_text(raw)
    if not text:
        return "", f"requirement_file 归一化后为空: cases/{case.requirement_file}"
    return text, None


# ============================================================
# benchmark DB 生命周期 + user
# ============================================================


def prepare_benchmark_db(db_path: Path, mode: str) -> None:
    """fresh：删除 benchmark 库文件及其 WAL 伴随文件（仅限 benchmark 目录，生产库已被护栏挡住）；
    reuse：只保证父目录；keep（A2）：不清库，但要求库文件已存在（resume 的 GenerationConfig 归因依赖它）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "fresh":
        for candidate in (db_path, *[Path(str(db_path) + sfx) for sfx in ("-wal", "-shm", "-journal")]):
            candidate.unlink(missing_ok=True)
        logger.info("benchmark DB 已按 fresh 模式清理: %s", db_path)
    elif mode == "keep":
        if not db_path.exists():
            raise BenchmarkDbUnavailable(f"--resume/--keep-db 要求既有 benchmark DB 存在: {db_path}")
        logger.info("benchmark DB 按 keep 模式沿用（不清库）: %s", db_path)


def _pass_header(
    *, runset_id: str, pass_no: int, manifest, suite, digests: dict[str, str], git: dict, db_target: Path
) -> dict:
    """写进 INCOMPLETE 的"本轮来源声明"——resume 的唯一核对依据。"""
    return {
        "runset_id": runset_id,
        "pass_no": pass_no,
        "pass_started_at": rl.utc_now_iso(),
        "git": git,
        "manifest_id": manifest.manifest_id,
        "benchmark_version": manifest.benchmark_version,
        "case_set_digest": rl.case_set_digest(manifest, digests, suite.cases, suite.golds),
        "gold_versions": {cid: suite.golds[cid].gold_version for cid in manifest.cases if cid in suite.golds},
        "case_versions": {cid: suite.cases[cid].benchmark_version for cid in manifest.cases if cid in suite.cases},
        "runner_version": rl.RUNNER_VERSION,
        "s6_schema": rl.S6_SCHEMA_VERSION,
        "business_prompt_versions": dict(rl.BUSINESS_PROMPT_VERSIONS),
        "s5_prompt_version": rl.S5_PROMPT_VERSION,
        "resolved_benchmark_db": str(db_target),
    }


def _classify_units(plan: tuple[RunPlan, ...], runset_dir: Path, ctx: CaseContext):
    """以磁盘 case 文件为事实源做复用门禁。

    返回 (待跑单元, reused, rerun, missing, reused_cfgs)；`reused` 单元**绝不进 run_case**。
    跨单元的配置一致性不在这里"猜本轮会用什么配置"，而是：① 要求复用单元的 GenerationConfig
    在目标 DB 中可解析且彼此一致；② 收尾时再对**全部**单元做一致性检查，不一致即 fail-closed。
    """
    to_run: list[RunPlan] = []
    reused: list[str] = []
    rerun: list[str] = []
    missing: list[str] = []
    reused_cfgs: dict[str, Any] = {}
    for unit in plan:
        key = rl.unit_key(unit.case.case_id, unit.repeat_index)
        rel = rl.unit_rel_path(unit.case.case_id, unit.repeat_index)
        payload, _why = rl.read_unit_payload(runset_dir, rel)
        ok, reason = rl.check_unit_reusability(
            payload, case=unit.case, gold=unit.gold, digest_map=ctx.digests, repeat_index=unit.repeat_index
        )
        cfg = None
        if ok:
            cfg = rl.load_generation_config(payload.get("run_id"))
            if not rl.config_signature(cfg):
                ok, reason = False, "config_unresolvable_in_db"
        if ok:
            common = rl.pick_uniform_generation_config({**reused_cfgs, key: cfg})
            if common[0] is None:
                ok, reason = False, "config_drift_among_reused"
        if ok:
            reused.append(key)
            reused_cfgs[key] = cfg
            logger.info("resume: 单元 %s 复用（零 LLM 调用）", key)
            continue
        if payload is None:
            missing.append(key)
            reason = reason or "missing_file"
        else:
            rerun.append(key)
            stale = rl.stale_path(runset_dir, rel)
            (runset_dir / rel).replace(stale)
            logger.warning("resume: 单元 %s 不可复用(%s)，旧文件转存 %s 后重跑", key, reason, stale.name)
        to_run.append(unit)
    return to_run, reused, rerun, missing, reused_cfgs


def resolve_benchmark_user(username: str) -> str:
    """benchmark DB 内的 V2 user（首跑自动创建；password_hash 留空，CLI 不做认证）。"""
    from core.schemas import new_ulid

    with v2_read_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is not None:
        logger.info("复用 benchmark DB 内已存在 user: %s", username)
        return row["id"]
    uid = new_ulid()
    repo.save_user(uid, username, password_hash="")
    logger.info("已在 benchmark DB 内开通 user: username=%s id=%s", username, uid)
    return uid


# ============================================================
# 单 case 执行（case/repeat 级隔离：任何失败都归到本单元，不影响其他单元）
# ============================================================


def run_case(plan: RunPlan, ctx: CaseContext, capture: rl.LogCapture | None = None) -> dict[str, Any]:
    """跑一个 (case, repeat) 单元并返回可直接落盘的 case 文件 payload。

    隔离原则（任务书 §二十二）：本函数内任何失败都只归到当前单元，绝不向上抛给主循环；
    Runtime 未捕获异常 → RUNTIME/LLM_FAILURE，装配/评价异常 → EVALUATION_FAILURE（冻结最高优先）。
    """
    case, gold = plan.case, plan.gold
    started_at = rl.utc_now_iso()
    # 单元起点打标：降级日志按"本单元增量窗口"取回（跨 case / 跨 repeat 不串信号）
    mark = capture.mark() if capture else None
    unit = {"signals": rl.LogCapture()}

    def _classify(
        pr_result: Any,
        *,
        counts: dict[str, Any] | None = None,
        input_invalid: bool = False,
        error: str | None = None,
        extra_error: str | None = None,
        prior_rules: list[str] | None = None,
    ) -> rl.ClassificationResult:
        unit["signals"] = capture.since(mark) if capture else rl.LogCapture()
        res = rl.classify_run_status(
            pipeline_result=pr_result,
            capture=unit["signals"],
            counts=counts,
            input_invalid=input_invalid,
            evaluation_error=error,
            extra_error_lines=[extra_error] if extra_error else None,
        )
        if prior_rules:
            res.rules_hit = list(dict.fromkeys([*prior_rules, *res.rules_hit]))
        return res

    pr = None
    bundle = rl.ArtifactsBundle()
    cfg = None
    counts: dict[str, Any] = {}
    evaluation_error: str | None = None
    hard_payload: dict | None = None
    soft_payload: dict | None = None
    soft_note = ""

    def _finish(cls, *, hard=None, soft=None, note="", error=None):
        return _case_payload(
            plan=plan,
            ctx=ctx,
            cls=cls,
            pr=pr,
            bundle=bundle,
            cfg=cfg,
            hard=hard,
            soft=soft,
            soft_note=note,
            evaluation_error=error,
            signals=unit["signals"],
            started_at=started_at,
        )

    # 1. 输入预检（失败即 INPUT_INVALID，且绝不调用 Runtime）
    text, precheck_error = precheck_requirement(case, ctx.dataset_root)
    if precheck_error:
        pr = None
        return _finish(_classify(None, input_invalid=True), note=f"not_evaluated: {precheck_error}")

    # 2. Runtime（client=None → 保持 Runtime 自身 generate/review 客户端行为，任务书 §六）
    try:
        pr = run_v2_pipeline(
            user_id=ctx.user_id,
            title=f"[bench] {case.case_id} r{plan.repeat_index:02d} {case.title}",
            text=text,
            source_type=SourceType.TEXT,
            client=None,
        )
    except Exception as exc:  # noqa: BLE001 - Runtime 抛穿：本单元失败，主循环继续后续 case
        logger.exception("case=%s repeat=%d Runtime 抛出未捕获异常", case.case_id, plan.repeat_index)
        pr = None
        return _finish(
            _classify(None, extra_error=rl.error_text(exc)),
            note="not_evaluated: Runtime 未捕获异常，无产物可评",
        )

    # 3. 装配 + S3 硬指标（评价层异常 = EVALUATION_FAILURE；非 COMPLETED 也仍跑 S3 以保留运行观测）
    def _counts() -> dict[str, Any]:
        return {**bundle.summary(), "review_present": bundle.review_present}

    try:
        bundle = rl.load_eval_artifacts(pr.run_id, pr.version_id)
        cfg = rl.load_generation_config(pr.run_id)
        counts = _counts()
        cls = _classify(pr, counts=counts)
        obs = rl.build_run_observation(
            pipeline_result=pr,
            case_id=case.case_id,
            gold=gold,
            status=cls.status,
            bundle=bundle,
            generation_config=cfg,
            reliability_note=cls.reliability_note,
        )
        hard = evaluate_hard_metrics(obs)
        hard_payload = rl.hard_to_payload(hard)
    except Exception as exc:  # noqa: BLE001
        logger.exception("case=%s repeat=%d 装配/硬指标评价异常", case.case_id, plan.repeat_index)
        evaluation_error = rl.error_text(exc)
        cls = _classify(pr, counts=_counts(), error=evaluation_error)
        return _finish(cls, note="not_evaluated: EVALUATION_FAILURE（S6 不伪造任何指标）", error=evaluation_error)

    # 4. S5 语义评价：仅 COMPLETED 消耗真实 LLM 预算（任务书 §四）
    if cls.status == BenchmarkRunStatus.COMPLETED:
        try:
            # 复用现有 purpose（不新增、不改 client_factory）；review 未启用时工厂自带回退到 generate 配置
            soft_payload = to_payload(evaluate_soft_metrics(obs, hard, build_llm_client("review")))
        except Exception as exc:  # noqa: BLE001
            logger.exception("case=%s repeat=%d S5 语义评价异常", case.case_id, plan.repeat_index)
            evaluation_error = rl.error_text(exc)
            cls = _classify(pr, counts=counts, error=evaluation_error, prior_rules=cls.rules_hit)
            soft_note = "not_evaluated: S5 评价异常（case 文件仍保留 S3 观测）"
    else:
        soft_note = "not_evaluated: 非 COMPLETED 不调用 S5 真实 LLM（任务书 §四）"

    return _finish(cls, hard=hard_payload, soft=soft_payload, note=soft_note, error=evaluation_error)


def _pipeline_summary(pr) -> dict[str, Any]:
    if pr is None:
        return {"invoked": False}
    return {
        "invoked": True,
        "success": bool(pr.success),
        "failed_step": pr.failed_step,
        "error": rl.safe_error_summary(pr.error_message),
        "total_duration_ms": pr.total_duration_ms,
        "total_llm_calls": pr.total_llm_calls,
        "doc_id": pr.doc_id,
        "version_id": pr.version_id,
        "review_report_id": pr.review_report_id,
        "steps": [
            {
                "step": s.step,
                "success": bool(s.success),
                "duration_ms": s.duration_ms,
                "llm_calls": s.llm_calls,
                "counts": dict(s.counts or {}),
                "error": rl.safe_error_summary(s.error),
            }
            for s in pr.steps
        ],
    }


def _case_payload(
    *,
    plan: RunPlan,
    ctx: CaseContext,
    cls: rl.ClassificationResult,
    pr,
    bundle: rl.ArtifactsBundle,
    cfg,
    hard: dict | None,
    soft: dict | None,
    soft_note: str,
    evaluation_error: str | None,
    signals: rl.LogCapture,
    started_at: str,
) -> dict[str, Any]:
    case, gold = plan.case, plan.gold
    soft_calls = int((soft or {}).get("llm_calls") or 0)
    fp = rl.case_fingerprint(case, gold, ctx.digests)
    return {
        "s6_schema": rl.S6_CASE_SCHEMA_VERSION,
        "case_id": case.case_id,
        "repeat_index": plan.repeat_index,
        "benchmark_version": case.benchmark_version,
        "gold_version": gold.gold_version,
        "run_id": getattr(pr, "run_id", None),
        "status": cls.status.value,
        "reliability_note": cls.reliability_note,
        "classification": {
            "rules_hit": cls.rules_hit,
            "signals": cls.signals,
            "captured_records": signals.total_records,
            "evaluation_error": rl.safe_error_summary(evaluation_error),
        },
        "pipeline": _pipeline_summary(pr),
        "artifacts_summary": {**bundle.summary(), "review_present": bundle.review_present},
        "archived_test_case_ids": sorted(bundle.archived_test_case_ids),
        "hard": hard,
        "soft": soft,
        "soft_note": soft_note or ("not_evaluated: 未产出语义评价" if soft is None else ""),
        "llm_calls": {
            "pipeline": int(getattr(pr, "total_llm_calls", 0) or 0),
            "s5_soft": soft_calls,
            "total": int(getattr(pr, "total_llm_calls", 0) or 0) + soft_calls,
        },
        "generation_config_id": cfg.id if cfg else None,
        "case_fingerprint": fp,
        "started_at": started_at,
        "finished_at": rl.utc_now_iso(),
    }


# ============================================================
# runset 落盘
# ============================================================


def resolve_output_dir(output_dir: str | Path) -> Path:
    """runset 根目录（相对路径一律相对项目根，避免依赖调用时的 cwd）。"""
    p = Path(output_dir)
    return p if p.is_absolute() else (_PROJECT_ROOT / p)


def new_runset_id(manifest_id: str) -> str:
    return f"{manifest_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _rates(counts: dict[str, int], total: int) -> dict[str, float | None]:
    def _r(n: int) -> float | None:
        return round(n / total, 4) if total else None

    return {
        "completion_rate": _r(counts.get(BenchmarkRunStatus.COMPLETED.value, 0)),
        "runtime_failure_rate": _r(counts.get(BenchmarkRunStatus.RUNTIME_FAILURE.value, 0)),
        "llm_failure_rate": _r(counts.get(BenchmarkRunStatus.LLM_FAILURE.value, 0)),
        "evaluation_failure_rate": _r(counts.get(BenchmarkRunStatus.EVALUATION_FAILURE.value, 0)),
        "input_invalid_rate": _r(counts.get(BenchmarkRunStatus.INPUT_INVALID.value, 0)),
    }


def build_runset_index(runset_id: str, fingerprint: dict, case_entries: list[dict], planned: int, repeat: int) -> dict:
    counts: dict[str, int] = {}
    for e in case_entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    total = len(case_entries)
    completed = counts.get(BenchmarkRunStatus.COMPLETED.value, 0)
    return {
        "s6_schema": rl.S6_SCHEMA_VERSION,
        "runset_id": runset_id,
        "runner_version": rl.RUNNER_VERSION,
        "created_at": rl.utc_now_iso(),
        "manifest": {
            "manifest_id": fingerprint["manifest_id"],
            "repeat": repeat,
            "cases": fingerprint["manifest_case_ids"],
        },
        "environment_fingerprint": fingerprint,
        "planned_units": planned,
        "written_units": total,
        "status_counts": counts,
        "evaluated_cases": f"{completed}/{total}",
        "completed_cases": completed,
        "non_completed_cases": total - completed,
        **_rates(counts, total),
        "cases": case_entries,
        "note": "质量指标仅在 evaluated_cases 口径内计分；非 COMPLETED 不折算质量 0 分（设计文档 §9.1）",
    }


# ============================================================
# 主流程
# ============================================================


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # openai/httpx 的 DEBUG 会打印 header；一律压到 WARNING 防泄漏
    for noisy in ("openai", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def do_dry_run(args, suite, plan, repeat, dataset_root: Path, db_target: Path) -> int:
    """轻量：读 manifest + 校验文件 + 执行计划 + 指纹基础信息；不建 DB / 不调 Runtime / 不调 LLM / 不写盘。"""
    print("\n" + "=" * 72 + "\nDRY-RUN（不建 DB / 不调 Runtime / 不调 LLM / 不写 runset）\n" + "=" * 72)
    print(f"manifest      : {args.manifest} (benchmark_version={suite.manifests[args.manifest].benchmark_version})")
    print(f"dataset_root  : {dataset_root} (只读)")
    print(f"resolved db   : {db_target}")
    print(f"output dir    : {args.output_dir}")
    print(f"repeat        : {repeat}")
    print(f"planned units : {len(plan)}")
    for idx, p in enumerate(plan, 1):
        text, err = precheck_requirement(p.case, dataset_root)
        mark = "[ERR]" if err else "[OK]"
        print(f"  {idx:>2}. {p.case.case_id:<26} r{p.repeat_index:02d} {mark} chars={len(text)}")
        if err:
            print(f"      ! {err}")
    print("=" * 72)
    return EXIT_OK


def _finalize_runset(
    *,
    runset_dir: Path,
    runset_id: str,
    plan: tuple[RunPlan, ...],
    manifest,
    suite,
    digests: dict[str, str],
    db_target: Path,
    repeat: int,
    resume_info: dict | None = None,
    pass_no: int = 1,
) -> int:
    """收尾：从磁盘读回**全部**计划单元 → 配置一致性校验 → 重建 runset.json → 完整才写 `_COMPLETE.json`。

    正常跑与 resume 共用这一条路径，因此"3+7 两次"与"10 一次"产出的索引结构等价。
    任一单元文件缺失/不可解析、或单元间 GenerationConfig 不一致 ⇒ fail-closed：
    不写 `_COMPLETE`、保留 `INCOMPLETE`，S8 侧天然拒绝该 runset。
    """
    entries: list[dict] = []
    cfgs: dict[str, Any] = {}
    for unit in plan:
        rel = rl.unit_rel_path(unit.case.case_id, unit.repeat_index)
        payload, why = rl.read_unit_payload(runset_dir, rel)
        if payload is None:
            logger.error("收尾失败：单元文件不可用 %s (%s) —— 不写 _COMPLETE", rel, why)
            return EXIT_WRITE_FAILURE
        entries.append(rl.build_case_index_entry(payload, rel))
        cfgs[rl.unit_key(unit.case.case_id, unit.repeat_index)] = rl.load_generation_config(payload.get("run_id"))

    cfg, problems = rl.pick_uniform_generation_config(cfgs)
    if cfg is None:
        for p in problems:
            logger.error("收尾失败：环境指纹一致性检查不通过 —— %s", p)
        logger.error("拒绝把单一指纹贴到来源不一的单元集合上（不写 _COMPLETE）")
        return EXIT_WRITE_FAILURE

    try:
        fingerprint = rl.build_environment_fingerprint(
            manifest=manifest,
            cases=suite.cases,
            golds=suite.golds,
            digest_map=digests,
            git=rl.probe_git(_PROJECT_ROOT),
            generation_config=cfg,
            resolved_benchmark_db=str(db_target),
        )
        index = build_runset_index(runset_id, fingerprint, entries, len(plan), repeat)
        if resume_info:
            index["resume"] = resume_info
        rl.write_json_atomic(runset_dir / "manifest.resolved.json", {"manifest": manifest.model_dump(mode="json")})
        rl.write_json_atomic(runset_dir / "runset.json", index)
    except rl.SensitiveDataError as exc:
        logger.error("runset 敏感自检失败: %s", exc)
        return EXIT_WRITE_FAILURE
    except OSError as exc:
        logger.error("runset 落盘失败: %s", exc)
        return EXIT_WRITE_FAILURE

    if index["planned_units"] != index["written_units"]:
        logger.error(
            "收尾失败：planned=%s != written=%s —— 不写 _COMPLETE", index["planned_units"], index["written_units"]
        )
        return EXIT_WRITE_FAILURE

    marker: dict[str, Any] = {
        "runset_id": runset_id,
        "units": index["written_units"],
        "planned_units": index["planned_units"],
        "written_units": index["written_units"],
        "pass_no": pass_no,
    }
    if resume_info:
        marker.update(
            {
                "resumed_from": resume_info["resumed_from"],
                "reused_units": len(resume_info["reused_units"]),
                "rerun_units": len(resume_info["rerun_units"]),
                "missing_units": len(resume_info["missing_units"]),
            }
        )
    (runset_dir / COMPLETE_MARKER).write_text(rl.json_dumps(marker), encoding="utf-8")
    (runset_dir / rl.PASS_HEADER_FILE).unlink(missing_ok=True)  # 只有完整落盘后才摘掉未完成标记

    non_completed = index["non_completed_cases"]
    print(f"\nrunset: {runset_dir}")
    print(f"units : {index['written_units']}（非 COMPLETED {non_completed}）evaluated={index['evaluated_cases']}")
    print(f"状态分布: {index['status_counts']}")
    if resume_info:
        print(
            f"resume: pass={pass_no} reused={len(resume_info['reused_units'])} "
            f"rerun={len(resume_info['rerun_units'])} missing={len(resume_info['missing_units'])}"
        )
    return EXIT_NOT_ALL_COMPLETED if non_completed else EXIT_OK


def run_benchmark(args: argparse.Namespace) -> int:
    """真实 runset 主流程（返回退出码）。"""
    dataset_root = Path(args.dataset_root).resolve()

    # 1. 生产库护栏 + 进程内 DB 切换（必须先于任何 DB 访问）
    try:
        db_target = rl.assert_not_production_db(args.db, _PROJECT_ROOT)
    except rl.ProductionDbGuardError as exc:
        logger.error("%s", exc)
        return EXIT_DB_GUARD
    set_v2_db_path(str(db_target))

    # 2. 数据集加载与校验（fail-fast）
    try:
        suite = load_benchmark_suite(dataset_root)
    except BenchmarkValidationError as exc:
        logger.error("Benchmark 数据集校验失败: %s", exc)
        return EXIT_DATASET

    # 3. 执行计划
    try:
        plan = build_plan(suite, args.manifest, args.case, args.repeat)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE
    manifest = suite.manifests[args.manifest]
    repeat = resolve_repeat(manifest, args.repeat)

    # 4. dry-run：到此为止（不碰 DB / 不调 LLM）
    if args.dry_run:
        return do_dry_run(args, suite, plan, repeat, dataset_root, db_target)

    # 5. benchmark DB 生命周期 + bootstrap
    try:
        prepare_benchmark_db(db_target, args.db_mode)
        ensure_v2_ready()
    except (V2BootstrapError, OSError, BenchmarkDbUnavailable) as exc:
        logger.error("benchmark DB bootstrap 失败: %s", exc)
        return EXIT_DB_GUARD
    if Path(get_v2_db_path()).resolve() != db_target:
        logger.error("DB 路径在 bootstrap 后发生漂移，拒绝继续: %s", get_v2_db_path())
        return EXIT_DB_GUARD

    # 6. runset 目录 + pass header（INCOMPLETE = 本轮来源声明，resume 靠它核对 git/数据集/prompt 来源）
    digests = rl.build_digest_map(dataset_root)
    git = rl.probe_git(_PROJECT_ROOT)
    out_root = resolve_output_dir(args.output_dir)
    if args.resume:
        runset_id = args.resume
        runset_dir = out_root / runset_id
        if not runset_dir.is_dir():
            logger.error("--resume 指定的 runset 不存在: %s", runset_dir)
            return EXIT_USAGE
        if (runset_dir / COMPLETE_MARKER).is_file():
            logger.error("--resume 指定的 runset 已完整收尾（%s 存在），无需续跑", COMPLETE_MARKER)
            return EXIT_USAGE
        prev, hstatus = rl.read_pass_header(runset_dir)
        if hstatus != "ok":
            logger.error(
                "--resume 拒绝执行：既有 runset 的 pass header 状态=%s，无法核对 git_commit / 数据集 / prompt 来源"
                "（宁可不续跑，也不给半成品贴指纹）",
                hstatus,
            )
            return EXIT_USAGE
        pass_no = int(prev.get("pass_no") or 1) + 1
        header = _pass_header(
            runset_id=runset_id,
            pass_no=pass_no,
            manifest=manifest,
            suite=suite,
            digests=digests,
            git=git,
            db_target=db_target,
        )
        problems = rl.diff_pass_header(prev, header)
        if problems:
            for p in problems:
                logger.error("--resume 来源不一致: %s", p)
            logger.error("跨 commit / 数据集漂移 / prompt 或 runner 版本变化一律拒绝续跑（MVP-A 纪律）")
            return EXIT_USAGE
    else:
        pass_no = 1
        runset_id = new_runset_id(args.manifest)
        runset_dir = out_root / runset_id
        runset_dir.mkdir(parents=True, exist_ok=True)
        header = _pass_header(
            runset_id=runset_id,
            pass_no=pass_no,
            manifest=manifest,
            suite=suite,
            digests=digests,
            git=git,
            db_target=db_target,
        )
    rl.write_pass_header(runset_dir, header)

    ctx = CaseContext(
        manifest=manifest,
        dataset_root=dataset_root,
        output_root=runset_dir,
        digests=digests,
        username=args.username,
        user_id=resolve_benchmark_user(args.username),
    )

    # 7. 单元分类（磁盘 case 文件为唯一事实源）→ 只跑缺口；reused 单元绝不进 run_case（零 LLM）
    to_run, reused, rerun, missing, _cfgs = _classify_units(plan, runset_dir, ctx)
    if args.resume:
        logger.info(
            "resume: reused=%d rerun=%d missing=%d 本轮待跑=%d", len(reused), len(rerun), len(missing), len(to_run)
        )

    capture, handler = rl.attach_log_capture()
    try:
        for unit in to_run:
            payload = run_case(unit, ctx, capture)
            rel_case = rl.unit_rel_path(unit.case.case_id, unit.repeat_index)
            try:
                rl.write_json_atomic(runset_dir / rel_case, payload)
            except rl.SensitiveDataError as exc:  # 单元级泄漏：立即中止（不落"看似完整"的索引）
                logger.error("case 文件敏感自检失败: %s", exc)
                return EXIT_WRITE_FAILURE
            logger.info("case=%s r%d → %s", unit.case.case_id, unit.repeat_index, payload["status"])
    finally:
        rl.detach_log_capture(handler)

    # 8. 收尾：从磁盘重建全量聚合（正常跑与 resume 共用同一条路径）
    resume_info = None
    if args.resume:
        resume_info = {
            "resumed_from": runset_id,
            "pass_no": pass_no,
            "reused_units": reused,
            "rerun_units": rerun,
            "missing_units": missing,
        }
    return _finalize_runset(
        runset_dir=runset_dir,
        runset_id=runset_id,
        plan=plan,
        manifest=manifest,
        suite=suite,
        digests=digests,
        db_target=db_target,
        repeat=repeat,
        resume_info=resume_info,
        pass_no=pass_no,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    err = validate_args(args)
    if err:
        print(f"[ERR] {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return run_benchmark(args)
    except KeyboardInterrupt:  # pragma: no cover - 人工中断
        logger.warning("已被用户中断")
        return EXIT_WRITE_FAILURE


if __name__ == "__main__":
    sys.exit(main())
