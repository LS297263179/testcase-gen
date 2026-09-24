"""V2 Step 11 S6 Benchmark Runner 纯函数层（被 scripts/v2_benchmark.py 消费）。

定位：Runtime 外部观测层的"组装 + 归类 + 序列化"层。本模块 **不 import core.v2.runtime**，
对 PipelineResult 一律鸭子类型读取（与 S3 RunObservation 同一解耦手法），确保零 Runtime 行为影响。

职责（任务书 §十~§十五、§二十三）：
  1. load_eval_artifacts    —— 从 benchmark DB 装配 EvalArtifacts（ARCHIVED TestCase 不入评价集合）
  2. classify_run_status    —— 五态启发式分类器（日志前缀 / PipelineResult / 产物计数三源 + 冻结优先级）
  3. hard_to_payload / from —— HardMetricsReport 的稳定 JSON 契约（不修改 metrics_hard.py）
  4. build_*_fingerprint    —— 环境指纹与 case 指纹（白名单，凭证/base_url 结构上不可能进入）
  5. write_json_atomic      —— dumps → assert_no_sensitive → 临时文件 → os.replace
  6. 生产库护栏             —— benchmark DB 解析为 data/data_v2.db 时拒绝运行

冻结边界：不新增 DB 表、不改 DDL/schema_version、不改 S3/S5、不写 benchmark/{cases,gold,manifests}。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from core.schemas import GenerationConfig, TargetType, Technique, TestCase, TestCaseStatus
from core.v2 import repository as repo
from core.v2.eval.matching import (
    ItemMatchState,
    ObligationOutcome,
    ObligationState,
    ScenarioOutcome,
    ScenarioState,
    StrategyOutcome,
)
from core.v2.eval.metrics_hard import (
    CostLatency,
    CountsObservation,
    EvalArtifacts,
    HardMetricsReport,
    InvalidFinding,
    MetricCell,
    PendingReviewItem,
    ReviewerBasedSnapshot,
    RunObservation,
    StepTiming,
)
from core.v2.eval.rails import RailMatch
from core.v2.eval.schema import BenchmarkRunStatus, MetricProvenance
from core.v2.eval.semantic_prompts import SEMANTIC_EVAL_PROMPT_VERSION
from core.v2.prompts import PARSER_PROMPT_VERSION
from core.v2.review_prompts import REVIEWER_PROMPT_VERSION
from core.v2.tc_prompts import PATTERN_DATA_PROMPT_VERSION, SYNTHESIZER_PROMPT_VERSION
from core.v2.tp_prompts import COMPLETER_PROMPT_VERSION, GENERATOR_PROMPT_VERSION

# ============================================================
# 冻结常量
# ============================================================

RUNNER_VERSION = "benchmark-runner-v1"
S6_SCHEMA_VERSION = "benchmark-runset-v1"
S6_CASE_SCHEMA_VERSION = "benchmark-runset-case-v1"

# 6 个业务 Prompt 版本（与 scripts/v2_real_run.PROMPT_VERSIONS 同口径，只读常量不触发任何初始化）
BUSINESS_PROMPT_VERSIONS: dict[str, str] = {
    "parser": PARSER_PROMPT_VERSION,
    "tp_generator": GENERATOR_PROMPT_VERSION,
    "tp_completer": COMPLETER_PROMPT_VERSION,
    "tc_synthesizer": SYNTHESIZER_PROMPT_VERSION,
    "tc_pattern_data": PATTERN_DATA_PROMPT_VERSION,
    "reviewer": REVIEWER_PROMPT_VERSION,
}
S5_PROMPT_VERSION = SEMANTIC_EVAL_PROMPT_VERSION

# 源 1：已核实的**最终降级**日志前缀（logger.exception 的 record.msg 前缀，稳定）。
# ★ 刻意不含"LLM 调用失败（第 N 次）…后重试"与"模型不支持图片输入"（任务书 §十三：非最终降级）。
LLM_DEGRADE_LOG_PREFIXES: tuple[str, ...] = (
    "需求解析 LLM 调用失败",  # core/v2/parser.py
    "Phase A LLM 调用失败",  # core/v2/tp_generator.py
    "Phase B LLM 调用失败",  # core/v2/tp_generator.py
    "LLM 用例合成调用失败",  # core/v2/tc_generator.py
    "LLM 软维度评审调用失败",  # core/v2/review_soft.py
)

# 源 2：PipelineResult.error_message 中的 LLM 特征
LLM_RETRY_EXHAUSTED_MARKER = "LLM 调用失败（已重试"  # core/llm_client.py 重试耗尽
# LLM SDK 异常类名（认证/限流/超时/连接/坏请求）——Runtime _fail 把异常类名写进 error_message
_SDK_LLM_ERROR_CLASSES: tuple[str, ...] = (
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
    "APIError",
    "APIStatusError",
    "BadRequestError",
    "InternalServerError",
    "OpenAIError",
    "AnthropicError",
)
_SDK_LLM_ERROR_RE = re.compile(r"(openai|anthropic|azure|httpx)\.[A-Za-z_.]*Error")

# 敏感关键字（复用 v2_real_run 白名单 + 任务书 §十四 追加 base_url / credential）
SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "secret_key",
    "access_token",
    "auth_token",
    "bearer",
    "base_url",
    "credential",
)

# 安全前缀长度（原始错误文本只保留此前缀 + hash，任务书 §十四）
SAFE_PREFIX_CHARS = 160

logger = logging.getLogger("v2.eval.runner_lib")


# ============================================================
# 异常
# ============================================================


class SensitiveDataError(RuntimeError):
    """落盘前敏感自检失败：拒绝写出 runset 文件（CLI 退出码 5）。"""


class ProductionDbGuardError(RuntimeError):
    """benchmark DB 解析为生产库 data/data_v2.db（CLI 退出码 3）。"""


# ============================================================
# 通用小工具
# ============================================================


def _en(value: object) -> Any:
    """枚举/对象 → JSON 安全值（枚举取 .value，其余原样交给 json(default=str)）。"""
    return value.value if isinstance(value, Enum) else value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_error_summary(text: str | None) -> dict[str, Any]:
    """原始错误 → {safe_prefix, hash}：可核对同源，不回显完整异常文本（可能含 base_url/凭证）。"""
    if not text:
        return {"safe_prefix": "", "hash": ""}
    return {"safe_prefix": text[:SAFE_PREFIX_CHARS], "hash": sha256_text(text)[:16]}


def error_text(exc: BaseException) -> str:
    """异常 → 分类器可消费的稳定摘要串（与 Runtime _fail 同口径：类名: 消息）。"""
    return f"{type(exc).__name__}: {exc}"


def assert_no_sensitive(text: str) -> None:
    """写盘前自检：小写比对敏感关键字，命中即拒绝落盘（双保险；白名单是第一保险）。"""
    lowered = text.lower()
    hits = [k for k in SENSITIVE_KEYWORDS if k in lowered]
    if hits:
        raise SensitiveDataError(f"runset 内容命中敏感关键字 {hits}，拒绝写盘")


def write_json_atomic(path: Path, payload: dict) -> Path:
    """稳定 JSON → 敏感自检 → 临时文件 → os.replace（原子写，不留半成品）。"""
    text = json_dumps(payload)
    assert_no_sensitive(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def json_dumps(payload: Any) -> str:
    """确定性序列化：键排序、枚举转 value、集合排序、datetime ISO。"""

    def _default(obj: Any):
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        if isinstance(obj, (set, frozenset)):
            return sorted(_en(x) for x in obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_default)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# 生产库护栏
# ============================================================


def production_db_candidates(project_root: Path) -> tuple[Path, ...]:
    """生产 V2 库及其 WAL 伴随文件（任一命中即拒绝运行）。"""
    base = (project_root / "data" / "data_v2.db").resolve()
    return (base, Path(str(base) + "-wal"), Path(str(base) + "-shm"), Path(str(base) + "-journal"))


def assert_not_production_db(candidate: str | Path, project_root: Path | str) -> Path:
    """benchmark DB 绝不允许等于生产库：命中则抛 ProductionDbGuardError（退出码 3）。"""
    root = Path(project_root)
    resolved = Path(candidate).resolve() if Path(candidate).is_absolute() else (root / candidate).resolve()
    forbidden = {p.resolve() for p in production_db_candidates(root)}
    if resolved in forbidden:
        raise ProductionDbGuardError(f"benchmark DB 解析为生产库，拒绝运行: {resolved}")
    return resolved


# ============================================================
# Git 探测（best-effort：失败不阻断 benchmark）
# ============================================================


def probe_git(project_root: Path | str) -> dict[str, Any]:
    """采集 git_commit / git_branch / git_dirty；任何失败 → git_probe='unknown' 且不抛。"""
    root = str(Path(project_root))

    def _run(args: list[str]) -> str:
        proc = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 退出码 {proc.returncode}")
        return proc.stdout

    try:
        commit = _run(["rev-parse", "HEAD"]).strip()
        branch = _run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        dirty = bool(_run(["status", "--porcelain", "--untracked-files=no"]).strip())
    except Exception:  # noqa: BLE001 - git 不可用时只降级指纹完整度，不阻断 benchmark
        logger.warning("git 探测失败，环境指纹 git_probe=unknown", exc_info=True)
        return {"git_commit": None, "git_branch": None, "git_dirty": None, "git_probe": "unknown"}
    return {"git_commit": commit, "git_branch": branch, "git_dirty": dirty, "git_probe": "ok"}


# ============================================================
# 日志捕获（源 1）
# ============================================================


@dataclass
class LogCapture:
    """进程内 logging handler 的收集桶：只保留分类信号，绝不整段落盘。

    用法（跨单元隔离）：每个 (case, repeat) 单元开始前 `mark()`，结束后 `since(m)` 取本单元增量，
    因此降级日志一定计入**产生它的那个单元**，不会串到别的单元。
    """

    degrade_lines: list[str] = field(default_factory=list)
    ignored_lines: list[str] = field(default_factory=list)  # 中间重试/图片降级等非最终降级信号
    total_records: int = 0

    def mark(self) -> tuple[int, int, int]:
        return (len(self.degrade_lines), len(self.ignored_lines), self.total_records)

    def since(self, mark: tuple[int, int, int]) -> LogCapture:
        d0, i0, t0 = mark
        return LogCapture(
            degrade_lines=list(self.degrade_lines[d0:]),
            ignored_lines=list(self.ignored_lines[i0:]),
            total_records=self.total_records - t0,
        )


class LogSignalHandler(logging.Handler):
    def __init__(self, sink: LogCapture):
        super().__init__(level=logging.WARNING)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - handler 绝不因格式化失败而抛穿业务栈
            return
        self._sink.total_records += 1
        if msg.startswith(LLM_DEGRADE_LOG_PREFIXES):
            self._sink.degrade_lines.append(msg)
        elif "LLM 调用失败" in msg or "已自动忽略图片" in msg:
            self._sink.ignored_lines.append(msg)


def attach_log_capture(level: int = logging.WARNING) -> tuple[LogCapture, LogSignalHandler]:
    """把信号 handler 挂到根 logger（CLI 在进程入口调用一次）。"""
    sink = LogCapture()
    handler = LogSignalHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(min(root.level or level, level))
    return sink, handler


def detach_log_capture(handler: LogSignalHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


# ============================================================
# 五态启发式分类器（任务书 §三 / §十三）
# ============================================================


@dataclass
class ClassificationResult:
    """分类产物：状态 + 命中规则 + 信号明细 + 可靠性注记（透传进 RunObservation.reliability_note）。"""

    status: BenchmarkRunStatus
    rules_hit: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    reliability_note: str = ""


def _llm_evidence(capture: LogCapture | None, pipeline_result: Any) -> tuple[list[str], list[str], int]:
    """抽取三源中的源 1（最终降级日志）与源 2（PipelineResult 错误文本）。"""
    degrade = [m[:SAFE_PREFIX_CHARS] for m in (capture.degrade_lines if capture else [])]
    ignored = len(capture.ignored_lines) if capture else 0
    error_lines: list[str] = []
    text = str(getattr(pipeline_result, "error_message", "") or "")
    if text:
        error_lines.append(text[:SAFE_PREFIX_CHARS])
    for step in getattr(pipeline_result, "steps", []) or []:
        step_err = str(getattr(step, "error", "") or "")
        if step_err:
            error_lines.append(step_err[:SAFE_PREFIX_CHARS])
    return degrade, error_lines, ignored


def _hits_llm_error(lines: list[str]) -> list[str]:
    hits: list[str] = []
    for line in lines:
        if LLM_RETRY_EXHAUSTED_MARKER in line or _SDK_LLM_ERROR_RE.search(line):
            hits.append(line)
            continue
        if any(cls in line for cls in _SDK_LLM_ERROR_CLASSES):
            hits.append(line)
    return hits


def classify_run_status(
    *,
    pipeline_result: Any | None,
    capture: LogCapture | None = None,
    counts: dict[str, Any] | None = None,
    input_invalid: bool = False,
    evaluation_error: str | None = None,
    extra_error_lines: list[str] | None = None,
) -> ClassificationResult:
    """五态分类（冻结优先级 EVALUATION_FAILURE > INPUT_INVALID > LLM_FAILURE > RUNTIME_FAILURE > COMPLETED）。

    counts 需含 items / test_cases / review_present（缺省按 0/False 处理）。
    extra_error_lines：Runtime **抛穿** 时（PipelineResult 不存在）由调用方补的错误文本。

    源 3（产物计数）按"该阶段是否真的跑到"门控，否则 Runtime 中途失败的 case 天然没有下游产物，
    照抄计数会把 RUNTIME_FAILURE 误判成 LLM_FAILURE：
      - items==0：只要 IR 阶段有记录就算（含"IR 未产出 items"失败点，正是认证类 LLM 故障的次生症状）；
      - test_cases==0：仅当 testcases 阶段确实跑成功却没产用例才算；
      - review 缺失：仅当 review 阶段有记录 + 已有 TestCase + 报告确实缺失才算。
    """
    c = dict(counts or {})
    items = int(c.get("items", 0) or 0)
    test_cases = int(c.get("test_cases", 0) or 0)
    steps = list(getattr(pipeline_result, "steps", []) or [])
    step_names = {getattr(s, "step", "") for s in steps}
    tc_step_passed = any(getattr(s, "step", "") == "testcases" and bool(getattr(s, "success", False)) for s in steps)
    review_ran = "review" in step_names
    items_zero = "ir" in step_names and items == 0
    cases_zero = tc_step_passed and test_cases == 0
    review_missing = review_ran and test_cases > 0 and not bool(c.get("review_present", False))

    degrade, error_lines, ignored = _llm_evidence(capture, pipeline_result)
    error_lines += [str(x)[:SAFE_PREFIX_CHARS] for x in (extra_error_lines or [])]
    llm_log_hits = [m for m in degrade if m.startswith(LLM_DEGRADE_LOG_PREFIXES)]
    llm_err_hits = _hits_llm_error(error_lines)
    success = bool(getattr(pipeline_result, "success", False)) if pipeline_result is not None else False
    failed_step = getattr(pipeline_result, "failed_step", None) if pipeline_result is not None else None

    def _res(status: BenchmarkRunStatus, rules: list[str], note: str) -> ClassificationResult:
        # 每条规则的证据计数（裁决 A7）：runset 内即可自证"为什么判成这一态"，无需回日志文件。
        ev_counts = {
            "llm_degrade_log": len(llm_log_hits),
            "llm_error_signature": len(llm_err_hits),
            "count_items_zero": int(items_zero),
            "count_test_cases_zero": int(cases_zero),
            "review_result_missing": int(review_missing),
        }
        unattributed = [
            r for r in rules if r.removeprefix("rule=") in ev_counts and not ev_counts[r.removeprefix("rule=")]
        ]
        signals = {
            "llm_degrade_logs": [safe_error_summary(m) for m in llm_log_hits],
            "llm_error_hits": [safe_error_summary(m) for m in llm_err_hits],
            "evidence_counts": ev_counts,
            "rules_without_evidence": unattributed,
            "ignored_intermediate_signals": ignored,
            "counts": {"items": items, "test_cases": test_cases, "review_present": bool(c.get("review_present"))},
            "review_ran": review_ran,
        }
        return ClassificationResult(status, rules, signals, note)

    # 1) 评价层自身异常（最高优先：观测层不可信时不猜任何结论）
    if evaluation_error:
        return _res(
            BenchmarkRunStatus.EVALUATION_FAILURE,
            ["rule=evaluation_error"],
            f"EVALUATION_FAILURE：评价/装配层异常 {safe_error_summary(evaluation_error)['hash']}",
        )
    # 2) 输入预检失败（INPUT_INVALID 的 case 不会调用 Runtime，见任务书 §九）
    if input_invalid:
        return _res(
            BenchmarkRunStatus.INPUT_INVALID,
            ["rule=input_precheck"],
            "INPUT_INVALID：需求原文缺失/为空/不可读，未调用 Runtime",
        )
    # 3) 三源降级信号统一收集（源 1 日志 / 源 2 错误特征 / 门控后的源 3 计数）
    rules: list[str] = []
    if llm_log_hits:
        rules.append("rule=llm_degrade_log")
    if llm_err_hits:
        rules.append("rule=llm_error_signature")
    if items_zero:
        rules.append("rule=count_items_zero")
    if cases_zero:
        rules.append("rule=count_test_cases_zero")
    if review_missing:
        rules.append("rule=review_result_missing")
    if rules:
        # 任务书 §三特别规定：Runtime success=True 但命中降级信号，同样 LLM_FAILURE（核心产物已降级）
        where = "Runtime success=True" if success else f"failed_step={failed_step or 'n/a'}"
        return _res(
            BenchmarkRunStatus.LLM_FAILURE,
            rules,
            f"LLM_FAILURE：{where} 且命中降级信号 {'、'.join(rules)}",
        )
    # 4) 失败但三源均无 LLM 特征 → 纯运行故障
    if not success:
        return _res(
            BenchmarkRunStatus.RUNTIME_FAILURE,
            ["rule=failed_pipeline_no_llm_signal"],
            f"RUNTIME_FAILURE：failed_step={failed_step or 'n/a'}，未见 LLM 降级特征",
        )
    return _res(BenchmarkRunStatus.COMPLETED, ["rule=completed"], "COMPLETED：三源均无降级信号")


# ============================================================
# Artifacts 装配（任务书 §五 / §十）
# ============================================================


@dataclass
class ArtifactsBundle:
    """EvalArtifacts + S6 观察字段（ARCHIVED 用例与 Review 是否产出）。"""

    artifacts: EvalArtifacts = field(default_factory=EvalArtifacts)
    archived_test_case_ids: list[str] = field(default_factory=list)
    review_present: bool = False

    @property
    def archived_count(self) -> int:
        return len(self.archived_test_case_ids)

    def summary(self) -> dict[str, int]:
        a = self.artifacts
        return {
            "items": len(a.items),
            "test_points": len(a.test_points),
            "test_cases": len(a.test_cases),  # 已排除 ARCHIVED（质量评价集合口径）
            "obligations": len(a.obligations),
            "archived_test_cases": self.archived_count,
            "strategy_points": sum(1 for tp in a.test_points if str(_en(tp.provenance)) == "strategy"),
        }


def load_eval_artifacts(run_id: str | None, version_id: str | None) -> ArtifactsBundle:
    """从当前 V2 DB 路径（S6 进程内为 benchmark DB）只读装配评价产物。

    ARCHIVED（Step 8 已归档）TestCase 被剔除出评价集合，但 id 保留为运行/优化观察。
    obligation_covered_points 以 repo.coverage_targets(..., TESTPOINT) 为权威关系源。
    """
    if not run_id:
        return ArtifactsBundle()

    artifacts = EvalArtifacts(
        items=repo.list_items(version_id) if version_id else [],
        test_points=repo.list_test_points_by_run(run_id),
        obligations=repo.list_obligations(run_id),
    )
    all_cases: list[TestCase] = repo.list_test_cases(run_id)
    active = [tc for tc in all_cases if tc.status != TestCaseStatus.ARCHIVED]
    artifacts.test_cases = active
    artifacts.obligation_covered_points = {
        ob.id: list(repo.coverage_targets(ob.id, TargetType.TESTPOINT)) for ob in artifacts.obligations
    }
    report = repo.get_latest_review_report(run_id)
    artifacts.review_report = report
    return ArtifactsBundle(
        artifacts=artifacts,
        archived_test_case_ids=[tc.id for tc in all_cases if tc.status == TestCaseStatus.ARCHIVED],
        review_present=report is not None,
    )


def load_generation_config(run_id: str | None) -> GenerationConfig | None:
    """Run → GenerationConfig（环境指纹与 S5 model_meta 的来源；run 未出生则 None）。"""
    if not run_id:
        return None
    run = repo.get_run(run_id)
    if run is None:
        return None
    return repo.get_generation_config(run.generation_config_id)


def build_run_observation(
    *,
    pipeline_result: Any | None,
    case_id: str,
    gold,
    status: BenchmarkRunStatus,
    bundle: ArtifactsBundle,
    generation_config: GenerationConfig | None,
    reliability_note: str,
) -> RunObservation:
    """统一观测输入（不扩展 RunObservation；pipeline_result 为 None 时鸭子类型读空对象）。"""
    pr = pipeline_result if pipeline_result is not None else _EmptyPipelineResult()
    return RunObservation.from_pipeline_result(
        pr,
        case_id=case_id,
        gold=gold,
        status=status,
        artifacts=bundle.artifacts,
        generation_config=generation_config,
        reliability_note=reliability_note,
    )


@dataclass
class _EmptyPipelineResult:
    """INPUT_INVALID（未调用 Runtime）时的空 PipelineResult 替身（鸭子类型契约一致）。"""

    run_id: str | None = None
    success: bool = False
    failed_step: str | None = None
    error_message: str | None = None
    steps: list[Any] = field(default_factory=list)
    total_duration_ms: int = 0
    total_llm_calls: int = 0


# ============================================================
# HardMetricsReport 序列化（任务书 §二十三：S6 自有文件，不改 S3）
# ============================================================

_HARD_CELLS = (
    "requirement_coverage",
    "critical_scenario_coverage",
    "obligation_coverage",
    "structural_validity",
    "duplication_score",
    "requirement_precision",
    "test_point_precision",
    "identity_match_rate",  # S7 三轨：Identity Rail 诊断单元（裁决 D1，与 requirement_coverage 并列）
)


def _cell_to(c: MetricCell) -> dict:
    return {
        "value": c.value,
        "numerator": c.numerator,
        "denominator": c.denominator,
        "provenance": _en(c.provenance),
        "detail": c.detail,
    }


def _cell_from(d: dict) -> MetricCell:
    return MetricCell(
        numerator=d["numerator"],
        denominator=d["denominator"],
        value=d["value"],
        provenance=MetricProvenance(d["provenance"]),
        detail=d["detail"],
    )


def hard_to_payload(report: HardMetricsReport) -> dict:
    """S3 报告 → 稳定 JSON（S7 消费契约 s6_schema 的一部分）。"""
    return {
        "s6_schema": S6_SCHEMA_VERSION,
        "case_id": report.case_id,
        "run_id": report.run_id,
        "run_status": _en(report.run_status),
        "quality_evaluated": report.quality_evaluated,
        "metrics": {name: _cell_to(getattr(report, name)) for name in _HARD_CELLS},
        "via_counts": dict(report.via_counts),
        "identity_diagnostics": dict(report.identity_diagnostics),
        "anchor_scope": report.anchor_scope,
        "requirement_matches": [
            {
                "gold_id": m.gold_id,
                "state": _en(m.state),
                "item_id": m.item_id,
                "item_ids": list(m.item_ids),
                "similarity": m.similarity,
                "basis": m.basis,
                "via": getattr(m, "via", ""),
            }
            for m in report.requirement_matches
        ],
        "scenario_outcomes": [_plain(o) for o in report.scenario_outcomes],
        "obligation_outcomes": [_plain(o) for o in report.obligation_outcomes],
        "strategy_outcomes": [_plain(o) for o in report.strategy_outcomes],
        "missing_risk": dict(report.missing_risk),
        "invalid": [_plain(f) for f in report.invalid],
        "pending_review": [_plain(p) for p in report.pending_review],
        "duplication_exact": list(report.duplication_exact),
        "duplication_semantic": list(report.duplication_semantic),
        "cost_latency": {
            "total_duration_ms": report.cost_latency.total_duration_ms,
            "total_llm_calls": report.cost_latency.total_llm_calls,
            "per_step": [_plain(s) for s in report.cost_latency.per_step],
            "duration_ms_per_case": report.cost_latency.duration_ms_per_case,
            "llm_calls_per_case": report.cost_latency.llm_calls_per_case,
            "llm_failure_steps": list(report.cost_latency.llm_failure_steps),
        },
        "counts": asdict(report.counts),
        "reviewer_based": _reviewer_to(report.reviewer_based),
    }


def hard_from_payload(payload: dict) -> HardMetricsReport:
    """hard_to_payload 的逆（S7 / 测试 round-trip 用）。"""
    report = HardMetricsReport(
        case_id=payload["case_id"],
        run_status=BenchmarkRunStatus(payload["run_status"]),
        quality_evaluated=payload["quality_evaluated"],
        run_id=payload["run_id"],
    )
    for name, cell in payload["metrics"].items():
        setattr(report, name, _cell_from(cell))
    report.requirement_matches = [
        RailMatch(
            gold_id=m["gold_id"],
            state=ItemMatchState(m["state"]),
            item_id=m["item_id"],
            item_ids=list(m["item_ids"]),
            similarity=m["similarity"],
            basis=m["basis"],
            via=m.get("via", ""),  # 旧 payload（S6 及以前）无 via → 空串，向后兼容
        )
        for m in payload["requirement_matches"]
    ]
    report.via_counts = dict(payload.get("via_counts", {}))
    report.identity_diagnostics = dict(payload.get("identity_diagnostics", {}))
    report.anchor_scope = payload.get("anchor_scope", "")
    report.scenario_outcomes = [
        ScenarioOutcome(
            scenario_id=o["scenario_id"],
            state=ScenarioState(o["state"]),
            anchors=dict(o["anchors"]),
            detail=o["detail"],
        )
        for o in payload["scenario_outcomes"]
    ]
    report.obligation_outcomes = [
        ObligationOutcome(
            target=o["target"],
            technique=Technique(o["technique"]),
            state=ObligationState(o["state"]),
            covered_points=o["covered_points"],
            required_points=o["required_points"],
            obligation_id=o["obligation_id"],
            detail=o["detail"],
        )
        for o in payload["obligation_outcomes"]
    ]
    report.strategy_outcomes = [
        StrategyOutcome(
            feature=o["feature"],
            technique=Technique(o["technique"]),
            requirement_gold_id=o["requirement_gold_id"],
            met=o["met"],
            detail=o["detail"],
        )
        for o in payload["strategy_outcomes"]
    ]
    report.missing_risk = dict(payload["missing_risk"])
    report.invalid = [InvalidFinding(**f) for f in payload["invalid"]]
    report.pending_review = [PendingReviewItem(**p) for p in payload["pending_review"]]
    report.duplication_exact = list(payload["duplication_exact"])
    report.duplication_semantic = list(payload["duplication_semantic"])
    cl = payload["cost_latency"]
    report.cost_latency = CostLatency(
        total_duration_ms=cl["total_duration_ms"],
        total_llm_calls=cl["total_llm_calls"],
        per_step=[StepTiming(**s) for s in cl["per_step"]],
        duration_ms_per_case=cl["duration_ms_per_case"],
        llm_calls_per_case=cl["llm_calls_per_case"],
        llm_failure_steps=list(cl["llm_failure_steps"]),
    )
    report.counts = CountsObservation(**payload["counts"])
    report.reviewer_based = _reviewer_from(payload["reviewer_based"])
    return report


def _plain(obj: Any) -> dict:
    """dataclass → dict（枚举转 value；不做排序，保持 S3 计算顺序）。"""
    data = asdict(obj)
    return {k: (_en(v) if not isinstance(v, (list, dict)) else _plain_list(v)) for k, v in data.items()}


def _plain_list(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain_list(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain_list(v) for k, v in value.items()}
    return _en(value)


def _reviewer_to(r: ReviewerBasedSnapshot | None) -> dict | None:
    if r is None:
        return None
    return {
        "report_id": r.report_id,
        "overall_score": r.overall_score,
        "scores": dict(r.scores),
        "obligation_coverage": r.obligation_coverage,
        "finding_count": r.finding_count,
        "findings_by_provenance": dict(r.findings_by_provenance),
        "provenance": _en(r.provenance),
    }


def _reviewer_from(d: dict | None) -> ReviewerBasedSnapshot | None:
    if d is None:
        return None
    return ReviewerBasedSnapshot(
        report_id=d["report_id"],
        overall_score=d["overall_score"],
        scores=d["scores"],
        obligation_coverage=d["obligation_coverage"],
        finding_count=d["finding_count"],
        findings_by_provenance=d["findings_by_provenance"],
        provenance=MetricProvenance(d["provenance"]),
    )


# ============================================================
# 指纹（任务书 §十五 / §十六）
# ============================================================


def build_digest_map(root: Path | str, subdirs: tuple[str, ...] = ("cases", "gold", "manifests")) -> dict[str, str]:
    """Benchmark 数据文件字节摘要（键 = '<subdir>/<文件名>'，只读）。"""
    root = Path(root)
    digests: dict[str, str] = {}
    for sub in subdirs:
        d = root / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*")):
            if path.is_file():
                digests[f"{sub}/{path.name}"] = sha256_file(path)
    return digests


def case_fingerprint(case, gold, digest_map: dict[str, str]) -> dict[str, Any]:
    """单 case 的数据版本指纹：case/gold 文件字节摘要 + requirement 原文摘要。"""
    req_key = f"cases/{case.requirement_file}"
    return {
        "case_id": case.case_id,
        "benchmark_version": case.benchmark_version,
        "gold_version": gold.gold_version,
        "case_file_digest": digest_map.get(f"cases/{case.case_id}.json", ""),
        "gold_file_digest": digest_map.get(f"gold/{case.gold_ref}", ""),
        "requirement_file_digest": digest_map.get(req_key, ""),
    }


def case_set_digest(
    manifest,
    digest_map: dict[str, str],
    cases: dict[str, Any] | None = None,
    golds: dict[str, Any] | None = None,
) -> str:
    """manifest 所列 case（含配对 Gold 与需求原文）的字节摘要聚合哈希（按 case_id 排序，确定性）。"""
    parts: list[str] = []
    for cid in sorted(manifest.cases):
        files = {f"cases/{cid}.json"}
        case = (cases or {}).get(cid)
        if case is not None:
            files.add(f"cases/{case.requirement_file}")
            files.add(f"gold/{case.gold_ref}")
        else:  # 拿不到模型时按命名约定兜底（<case_id>.json / <case_id>*.md / <case_id>.gold.json）
            files |= {k for k in digest_map if Path(k).name.startswith(cid)}
        parts += [f"{k}:{digest_map[k]}" for k in sorted(files) if k in digest_map]
    return sha256_text("|".join(parts))[:32]


def build_environment_fingerprint(
    *,
    manifest,
    cases,
    golds,
    digest_map: dict[str, str],
    git: dict[str, Any],
    generation_config: GenerationConfig | None,
    resolved_benchmark_db: str,
) -> dict[str, Any]:
    """runset 级环境指纹：代码版本 × Prompt × 模型 × 配置 × Benchmark 数据版本。"""
    cfg = generation_config
    model = {
        "model_provider": cfg.model_provider if cfg else None,
        "model_name": cfg.model_name if cfg else None,
        "temperature": cfg.temperature if cfg else None,
        "enable_thinking": cfg.enable_thinking if cfg else None,
        "max_tokens": cfg.max_tokens if cfg else None,
        "generator_version": cfg.generator_version if cfg else None,
        "reviewer_version": cfg.reviewer_version if cfg else None,
        "generation_config_id": cfg.id if cfg else None,
    }
    return {
        "benchmark_version": manifest.benchmark_version,
        "manifest_id": manifest.manifest_id,
        "manifest_version": manifest.benchmark_version,
        "manifest_case_ids": list(manifest.cases),
        "case_set_digest": case_set_digest(manifest, digest_map, cases, golds),
        "gold_versions": {cid: golds[cid].gold_version for cid in manifest.cases if cid in golds},
        "case_versions": {cid: cases[cid].benchmark_version for cid in manifest.cases if cid in cases},
        **git,
        **model,
        "business_prompt_versions": dict(BUSINESS_PROMPT_VERSIONS),
        "s5_prompt_version": S5_PROMPT_VERSION,
        "runner_version": RUNNER_VERSION,
        "resolved_benchmark_db": resolved_benchmark_db,
    }


# ============================================================
# A2 断点续跑支持（S6 落盘契约零变更：以下全部只读回既有产物 / 只做纯判定）
# ============================================================

PASS_HEADER_FILE = "INCOMPLETE"
STALE_INFIX = ".stale-"

# 复用门禁里参与归因的 GenerationConfig 字段（不含 id / 时间戳等每次必变的值）
CONFIG_SIGNATURE_FIELDS = (
    "model_provider",
    "model_name",
    "temperature",
    "enable_thinking",
    "max_tokens",
    "generator_version",
    "reviewer_version",
    "prompt_version",
)


def unit_key(case_id: str, repeat_index: int) -> str:
    return f"{case_id}__r{repeat_index}"


def unit_rel_path(case_id: str, repeat_index: int) -> str:
    """单元落盘相对路径（与 S6 既有形态一致，不改契约）。"""
    return f"cases/{case_id}__r{repeat_index:02d}.json"


def read_unit_payload(runset_dir: Path | str, rel: str) -> tuple[dict | None, str | None]:
    """读回一个单元文件 → (payload | None, 不可用原因 | None)。"""
    p = Path(runset_dir) / rel
    if not p.is_file():
        return None, "missing_file"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "unparseable"
    if not isinstance(data, dict):
        return None, "unparseable"
    return data, None


def check_unit_reusability(
    payload: dict | None,
    *,
    case,
    gold,
    digest_map: dict[str, str],
    repeat_index: int,
) -> tuple[bool, str]:
    """文件侧复用判据（DB 侧与 pass header 侧判据由 CLI 分别执行）。

    任一不满足即不可复用，原因串用于日志与审计：
    missing_file / unparseable / schema_mismatch / not_completed / unit_mismatch / fingerprint_drift。
    """
    if payload is None:
        return False, "missing_file"
    if payload.get("s6_schema") != S6_CASE_SCHEMA_VERSION:
        return False, "schema_mismatch"
    if payload.get("status") != BenchmarkRunStatus.COMPLETED.value:
        return False, "not_completed"
    if payload.get("case_id") != case.case_id or int(payload.get("repeat_index") or -1) != int(repeat_index):
        return False, "unit_mismatch"
    if dict(payload.get("case_fingerprint") or {}) != case_fingerprint(case, gold, digest_map):
        return False, "fingerprint_drift"
    return True, "reusable"


def config_signature(cfg: GenerationConfig | None) -> dict[str, Any]:
    """GenerationConfig → 归因签名；cfg 缺失（DB 不连续）→ 空 dict，调用方必须 fail-closed。"""
    if cfg is None:
        return {}
    return {f: _en(getattr(cfg, f, None)) for f in CONFIG_SIGNATURE_FIELDS}


def pick_uniform_generation_config(
    cfgs: dict[str, GenerationConfig | None],
) -> tuple[GenerationConfig | None, list[str]]:
    """所有被索引单元的 GenerationConfig 必须完全一致，否则返回问题清单（fail-closed 依据）。

    取代旧 `first_cfg = 本轮第一个单元` 的做法 —— 混合来源时宁可不出 runset，
    也不给整轮贴一个只对某个单元成立的指纹。
    """
    problems: list[str] = []
    named = {k: v for k, v in cfgs.items() if v is not None}
    missing = sorted(set(cfgs) - set(named))
    if missing:
        problems.append(f"以下单元无法解析 GenerationConfig（DB 不连续或 run 未落库）: {missing}")
    if not named:
        return None, problems or ["没有任何单元带 GenerationConfig"]
    sigs = {k: config_signature(v) for k, v in named.items()}
    distinct = {json.dumps(s, sort_keys=True, ensure_ascii=False) for s in sigs.values()}
    if len(distinct) > 1:
        by_sig: dict[str, list[str]] = {}
        for k, s in sigs.items():
            by_sig.setdefault(json.dumps(s, sort_keys=True, ensure_ascii=False), []).append(k)
        problems.append(f"单元间配置不一致，拒绝归并为单一环境指纹: {sorted(by_sig.values(), key=len, reverse=True)}")
        return None, problems
    return named[sorted(named)[0]], problems


def build_case_index_entry(payload: dict, rel: str) -> dict[str, Any]:
    """case payload → runset.json 的索引条目（与 S6 既有字段完全一致）。"""
    status = payload.get("status")
    return {
        "case_id": payload.get("case_id"),
        "repeat_index": payload.get("repeat_index"),
        "status": status,
        "run_id": payload.get("run_id"),
        "quality_evaluated": status == BenchmarkRunStatus.COMPLETED.value,
        "file": rel,
        "llm_calls": int(((payload.get("llm_calls") or {}).get("total")) or 0),
    }


def write_pass_header(runset_dir: Path | str, header: dict[str, Any]) -> Path:
    """把本轮 pass 的来源信息写进 INCOMPLETE（resume 的核对依据）。"""
    return write_json_atomic(Path(runset_dir) / PASS_HEADER_FILE, header)


def read_pass_header(runset_dir: Path | str) -> tuple[dict | None, str]:
    """读回 pass header → (header, 状态)。

    状态：ok / none（无文件）/ legacy（旧版只写 runset_id 纯文本，无法核对 git_commit）/ bad。
    """
    p = Path(runset_dir) / PASS_HEADER_FILE
    if not p.is_file():
        return None, "none"
    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "bad"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "legacy"
    if not isinstance(data, dict):
        return None, "legacy"
    return data, "ok"


def load_runset_record(runset_dir: Path | str) -> dict | None:
    """读回已收尾 runset 的索引（用于判断"已完成，无需 resume"）。"""
    p = Path(runset_dir) / "runset.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stale_path(runset_dir: Path | str, rel: str) -> Path:
    """重跑前保留旧文件的落点：cases/.<name>.stale-<UTC 时间戳>.json（不删除失败现场）。"""
    p = Path(runset_dir) / rel
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return p.parent / f".{p.name}{STALE_INFIX}{stamp}"


# resume 时必须与本轮逐项相等的 pass header 字段（来源一致性门禁）
RESUME_HEADER_FIELDS = (
    "manifest_id",
    "benchmark_version",
    "case_set_digest",
    "gold_versions",
    "case_versions",
    "runner_version",
    "s6_schema",
    "business_prompt_versions",
    "s5_prompt_version",
    "resolved_benchmark_db",
)


def diff_pass_header(prev: dict, cur: dict) -> list[str]:
    """比较两次 pass 的来源声明，返回不一致项（含 git_commit / git_dirty）。"""
    problems: list[str] = []
    for f in ("git_commit", "git_branch", "git_dirty", *RESUME_HEADER_FIELDS):
        pv = (prev.get("git") or {}).get(f) if f in ("git_commit", "git_branch", "git_dirty") else prev.get(f)
        cv = (cur.get("git") or {}).get(f) if f in ("git_commit", "git_branch", "git_dirty") else cur.get(f)
        if pv != cv:
            problems.append(f"{f}: 既有 runset={pv!r} 本轮={cv!r}")
    return problems
