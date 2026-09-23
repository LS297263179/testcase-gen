"""V2 Step 11 S8 Compare / Delta —— 只读比较层（设计文档 §11 + 附录 D）。

一句话：把「已提升的 baseline」与「任一 runset」两份**已落盘**指标做逐格 delta，
输出 improvement / regression / unchanged + 可比性声明。

★ 三条冻结纪律（§11 / §2 边界 17）：
  1. **零真实 LLM、零 DB、零 Runtime**：两侧操作数都只读 JSON，不 import 任何 LLM/DB 写路径，
     绝不重新评价任何 case。
  2. **不做门禁**：不定义固定阈值、不判 pass/fail、不因质量下降而抛错或返回非 0。
  3. **不允许静默混算**：可比性字段不一致时，所有格子降级为 ``not_comparable``，
     只保留原始读数与差值，不产出 improvement/regression 结论。

脱敏边界（§10.1 + 附录 D.2）：只白名单挑**数值 / 枚举 / 数据集 id**，
逐字丢弃 payload 里的 ``detail`` / ``reason`` / ``anchors`` / ``note`` / ``safe_prefix`` 等自由文本，
以及跨 run 不稳定且无比较价值的 ULID（run_id / report_id / tc_id / ref_id）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.v2.eval import runner_lib as rl

# ============================================================
# 常量（冻结）
# ============================================================

S8_BASELINE_SCHEMA_VERSION = "benchmark-baseline-v1"
S8_COMPARE_SCHEMA_VERSION = "benchmark-compare-v1"

DEFAULT_BASELINE_ID = "baseline-v0.1"
DEFAULT_BASELINE_PATH = "benchmark/baselines/baseline-v0.1.json"
DEFAULT_COMPARE_DIR = "benchmark/compares"

COMPLETE_MARKER = "_COMPLETE.json"

# hard 的 8 个 MetricCell（字段名与 metrics_hard.HardMetricsReport 一致）
HARD_CELLS = (
    "requirement_coverage",
    "requirement_precision",
    "obligation_coverage",
    "critical_scenario_coverage",
    "test_point_precision",
    "structural_validity",
    "duplication_score",
    "identity_match_rate",
)

# Step 7 评审六维（reviewer_based.scores，S3/S5 双轨同源透传）
SOFT_SCORE_DIMS = ("accuracy", "missing_risk", "executability", "consistency", "coverage", "duplication")

# 产物计数（quality_evaluated=False 时质量格为 None，计数仍作观测）
COUNT_KEYS = ("items", "obligations", "strategy_points", "test_points", "test_cases", "archived_test_cases")

VIA_KEYS = ("identity", "bridge", "anchor", "candidate", "ambiguous", "miss")

VERDICT_IMPROVEMENT = "improvement"
VERDICT_REGRESSION = "regression"
VERDICT_UNCHANGED = "unchanged"
VERDICT_NEUTRAL = "neutral"
VERDICT_DIAGNOSTIC = "diagnostic_only"
VERDICT_ONE_SIDE_NULL = "one_side_null"
VERDICT_BOTH_NULL = "unavailable_both"
VERDICT_NOT_COMPARABLE = "not_comparable"

ALL_VERDICTS = (
    VERDICT_IMPROVEMENT,
    VERDICT_REGRESSION,
    VERDICT_UNCHANGED,
    VERDICT_NEUTRAL,
    VERDICT_DIAGNOSTIC,
    VERDICT_ONE_SIDE_NULL,
    VERDICT_BOTH_NULL,
    VERDICT_NOT_COMPARABLE,
)

# 方向口径表 —— 声明「数值往哪个方向变化叫什么」，**不是质量阈值，不判 pass/fail**。
#   higher_better / lower_better → 可标 improvement / regression
#   neutral                      → 只报差值（体量、成本、计数类，无质量方向可言）
#   diagnostic_only              → 仅诊断，禁止当覆盖读数（identity 轨全项）
# 未列出的路径一律按 neutral 处理：宁可不判，不可误判。
METRIC_DIRECTIONS: dict[str, str] = {
    "hard.requirement_coverage": "higher_better",
    "hard.requirement_precision": "higher_better",
    "hard.obligation_coverage": "higher_better",
    "hard.critical_scenario_coverage": "higher_better",
    "hard.test_point_precision": "higher_better",
    "hard.structural_validity": "higher_better",
    "hard.duplication_score": "higher_better",
    "hard.identity_match_rate": "diagnostic_only",
    "hard.invalid_count": "lower_better",
    "hard.pending_review_count": "neutral",
    "hard.missing_risk.total_open": "lower_better",
    "hard.missing_risk.gold_miss_count": "lower_better",
    "hard.missing_risk.obligation_open_count": "lower_better",
    "hard.missing_risk.scenario_open_count": "lower_better",
    "hard.missing_risk.strategy_unmet_count": "lower_better",
    "hard.reviewer_based.overall_score": "higher_better",
    "soft.overall_score": "higher_better",
    "soft.illegal_evidence_refs_dropped": "lower_better",
    "soft.batch_failed_count": "lower_better",
    "soft.forbidden_suspects_count": "lower_better",
    "soft.verdicts.correct": "higher_better",
    "soft.verdicts.partial": "neutral",
    "soft.verdicts.incorrect": "lower_better",
    "soft.verdicts.pending_review": "neutral",
    "ir.parse_issues": "lower_better",
}
for _c in VIA_KEYS:
    METRIC_DIRECTIONS[f"hard.via_counts.{_c}"] = "diagnostic_only"
for _k in (
    "identity_match_rate",
    "auto_hit_identity_only",
    "best_similarity_mean",
    "identity_miss_gold_count",
):
    METRIC_DIRECTIONS[f"hard.identity_diagnostics.{_k}"] = "diagnostic_only"
for _d in SOFT_SCORE_DIMS:
    METRIC_DIRECTIONS[f"soft.scores.{_d}"] = "higher_better"
    METRIC_DIRECTIONS[f"hard.reviewer_based.scores.{_d}"] = "higher_better"

# 可比性判据：数据集版本 + 模型与采样 + 评价层版本。
# ★ 故意不含 git_commit / git_dirty —— 「代码变了」正是 S8 要测的对象，列进去等于自锁死。
# ★ 故意不含 generation_config_id / run_id —— 每次 Run 必然新生成，无比较价值。
COMPARABILITY_FIELDS = (
    "benchmark_version",
    "manifest_id",
    "case_set_digest",
    "gold_versions",
    "case_versions",
    "model_name",
    "model_provider",
    "temperature",
    "enable_thinking",
    "max_tokens",
    "generator_version",
    "reviewer_version",
    "business_prompt_versions",
    "s5_prompt_version",
    "runner_version",
)

MEAN_SEMANTICS = "n = 该格可评 case 数；value 为 None（如分母 0/0）的 case 不入分母，均值与 n 一起读"


class CompareOperandError(RuntimeError):
    """操作数缺失 / 不完整 / schema 不符（CLI 退出码 3）。"""


# ============================================================
# 摘要投影（baseline 与 candidate 共用，保证同口径）
# ============================================================


def _cell(cell: dict | None) -> dict[str, Any]:
    """MetricCell → 比较所需三元组（detail 自由文本丢弃）。"""
    c = cell or {}
    return {
        "value": c.get("value"),
        "numerator": c.get("numerator"),
        "denominator": c.get("denominator"),
        "provenance": c.get("provenance"),
    }


def _verdict_counts(judgments: list | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for j in judgments or []:
        v = str((j or {}).get("verdict") or "unknown")
        out[v] = out.get(v, 0) + 1
    return out


def digest_case_payload(payload: dict) -> dict[str, Any]:
    """单个 case JSON（S6 落盘形态）→ 比较摘要。"""
    hard = payload.get("hard") or {}
    soft = payload.get("soft") or {}
    fp = payload.get("case_fingerprint") or {}
    arts = payload.get("artifacts_summary") or {}
    cost = hard.get("cost_latency") or {}
    mr = hard.get("missing_risk") or {}
    idd = hard.get("identity_diagnostics") or {}
    rev = hard.get("reviewer_based") or {}
    soft_rev = soft.get("reviewer_based") or {}
    gold_based = soft.get("gold_based") or {}
    batches = soft.get("batches") or []
    per_step = cost.get("per_step") or []

    ir: dict[str, Any] = {"parse_issues": 0, "parse_issue_kinds": []}
    for st in per_step:
        if st.get("step") == "ir":
            cnt = st.get("counts") or {}
            ir = {
                "parse_issues": int(cnt.get("parse_issues") or 0),
                "parse_issue_kinds": sorted(str(k) for k in (cnt.get("parse_issue_kinds") or [])),
            }

    return {
        "case_id": payload.get("case_id"),
        "repeat_index": payload.get("repeat_index"),
        "status": payload.get("status"),
        "quality_evaluated": bool(hard.get("quality_evaluated")),
        "benchmark_version": payload.get("benchmark_version"),
        "gold_version": payload.get("gold_version"),
        "case_fingerprint": {
            "case_file_digest": fp.get("case_file_digest"),
            "gold_file_digest": fp.get("gold_file_digest"),
            "requirement_file_digest": fp.get("requirement_file_digest"),
        },
        "rules_hit": sorted(str(r) for r in ((payload.get("classification") or {}).get("rules_hit") or [])),
        "llm_calls": {
            "pipeline": int((payload.get("llm_calls") or {}).get("pipeline") or 0),
            "s5_soft": int((payload.get("llm_calls") or {}).get("s5_soft") or 0),
            "total": int((payload.get("llm_calls") or {}).get("total") or 0),
        },
        "hard": {
            "run_status": hard.get("run_status"),
            "metrics": {k: _cell((hard.get("metrics") or {}).get(k)) for k in HARD_CELLS},
            "via_counts": {k: int((hard.get("via_counts") or {}).get(k) or 0) for k in VIA_KEYS},
            "identity_diagnostics": {
                "identity_match_rate": idd.get("identity_match_rate"),
                "auto_hit_identity_only": idd.get("auto_hit_identity_only"),
                "best_similarity_mean": idd.get("best_similarity_mean"),
                "gold_total": idd.get("gold_total"),
                "module_consistency": idd.get("module_consistency"),
                "type_consistency": idd.get("type_consistency"),
                "statement_verbatim": idd.get("statement_verbatim"),
                "identity_miss_gold_count": len(idd.get("identity_miss_gold_ids") or []),
            },
            "anchor_scope": hard.get("anchor_scope"),
            "counts": {k: int(arts.get(k) or 0) for k in COUNT_KEYS},
            "cost_latency": {
                "total_duration_ms": cost.get("total_duration_ms"),
                "total_llm_calls": cost.get("total_llm_calls"),
                "llm_calls_per_case": cost.get("llm_calls_per_case"),
                "duration_ms_per_case": cost.get("duration_ms_per_case"),
                "per_step": [
                    {
                        "step": st.get("step"),
                        "llm_calls": st.get("llm_calls"),
                        "llm_attempts": st.get("llm_attempts"),
                        "llm_retries": st.get("llm_retries"),
                        "llm_failures": st.get("llm_failures"),
                        "duration_ms": st.get("duration_ms"),
                        "success": bool(st.get("success")),
                    }
                    for st in per_step
                ],
            },
            "duplication_exact_count": len(hard.get("duplication_exact") or []),
            "duplication_semantic_count": len(hard.get("duplication_semantic") or []),
            "invalid_count": len(hard.get("invalid") or []),
            "pending_review_count": len(hard.get("pending_review") or []),
            "missing_risk": {
                "total_open": int(mr.get("total_open") or 0),
                "gold_miss_count": len(mr.get("gold_miss_ids") or []),
                "obligation_open_count": len(mr.get("obligation_open_ids") or []),
                "scenario_open_count": len(mr.get("scenario_open_ids") or []),
                "strategy_unmet_count": len(mr.get("strategy_unmet_ids") or []),
            },
            "reviewer_based": {
                "overall_score": rev.get("overall_score"),
                "provenance": rev.get("provenance"),
                "finding_count": rev.get("finding_count"),
                "obligation_coverage": rev.get("obligation_coverage"),
                "scores": {k: (rev.get("scores") or {}).get(k) for k in SOFT_SCORE_DIMS},
            },
        },
        "ir": ir,
        "soft": {
            "present": bool(soft),
            "s5_schema": soft.get("s5_schema"),
            "prompt_version": soft.get("prompt_version"),
            "llm_calls": int(soft.get("llm_calls") or 0),
            "batch_count": len(batches),
            "batch_failed_count": sum(1 for b in batches if not (b or {}).get("ok", True)),
            "illegal_evidence_refs_dropped": len(soft.get("failures") or []),
            "verdicts": _verdict_counts(gold_based.get("judgments") or []),
            "additional_valid_candidates_count": len(gold_based.get("additional_valid_candidates") or []),
            "forbidden_suspects_count": len(gold_based.get("forbidden_suspects") or []),
            "overall_score": soft_rev.get("overall_score"),
            "scores": {k: (soft_rev.get("scores") or {}).get(k) for k in SOFT_SCORE_DIMS},
            "model_meta": {k: v for k, v in (soft.get("model_meta") or {}).items()},
        },
    }


def aggregate_of(cases: dict[str, dict]) -> dict[str, Any]:
    """逐 case 摘要 → 聚合层。均值分母 = 该格实际可评 case 数（None 不入分母）。"""
    if not cases:
        raise CompareOperandError("聚合前 case 集合为空")
    counts: dict[str, int] = {}
    for d in cases.values():
        counts[str(d.get("status"))] = counts.get(str(d.get("status")), 0) + 1
    n = len(cases)

    def _num(d: dict, *path: str) -> float | None:
        cur: Any = d
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        if isinstance(cur, bool) or not isinstance(cur, (int, float)):
            return None
        return float(cur)

    def _mean(path: tuple) -> dict[str, Any]:
        vals = [v for v in (_num(d, *path) for d in cases.values()) if v is not None]
        return {"mean": round(sum(vals) / len(vals), 6) if vals else None, "n": len(vals)}

    def _sum(*path: str) -> int:
        return int(sum(_num(d, *path) or 0 for d in cases.values()))

    retries = failures = 0
    for d in cases.values():
        for st in d["hard"]["cost_latency"]["per_step"]:
            retries += int(st.get("llm_retries") or 0)
            failures += int(st.get("llm_failures") or 0)

    verdicts: dict[str, int] = {}
    via: dict[str, int] = {}
    for d in cases.values():
        for k, v in d["soft"]["verdicts"].items():
            verdicts[k] = verdicts.get(k, 0) + int(v)
        for k, v in d["hard"]["via_counts"].items():
            via[k] = via.get(k, 0) + int(v)

    return {
        "cases_total": n,
        "status_counts": counts,
        "completion_rate": round(counts.get("completed", 0) / n, 4),
        "metric_means": {c: _mean(("hard", "metrics", c, "value")) for c in HARD_CELLS},
        "soft_score_means": {k: _mean(("soft", "scores", k)) for k in SOFT_SCORE_DIMS},
        "s5_verdict_totals": verdicts,
        "via_counts_totals": via,
        "totals": {
            "llm_calls_pipeline": _sum("llm_calls", "pipeline"),
            "llm_calls_s5": _sum("llm_calls", "s5_soft"),
            "llm_calls_total": _sum("llm_calls", "total"),
            "llm_retries": retries,
            "llm_failures": failures,
            "total_duration_ms": _sum("hard", "cost_latency", "total_duration_ms"),
            "items": _sum("hard", "counts", "items"),
            "test_points": _sum("hard", "counts", "test_points"),
            "test_cases_alive": _sum("hard", "counts", "test_cases"),
            "test_cases_archived": _sum("hard", "counts", "archived_test_cases"),
            "hard_pending_review": _sum("hard", "pending_review_count"),
            "invalid": _sum("hard", "invalid_count"),
            "missing_risk_open": _sum("hard", "missing_risk", "total_open"),
            "illegal_evidence_refs_dropped": _sum("soft", "illegal_evidence_refs_dropped"),
        },
        "mean_semantics": MEAN_SEMANTICS,
    }


# ============================================================
# 操作数装载（全部只读文件）
# ============================================================


def load_runset(path: str | Path) -> dict[str, Any]:
    """读一个 runset 目录 → 统一操作数结构。

    完整性判据（任一不满足 → :class:`CompareOperandError`，CLI 退出码 3）：
    ``_COMPLETE.json`` 存在、``runset.json`` 可解析、index 引用的 case 文件全部存在、
    ``planned_units == written_units == 实载数``、``_COMPLETE.json.units`` 与实载数一致。
    """
    root = Path(path)
    if not root.is_dir():
        raise CompareOperandError(f"runset 目录不存在: {root}")
    if not (root / COMPLETE_MARKER).is_file():
        raise CompareOperandError(f"runset 缺少 {COMPLETE_MARKER}（中途 aborted 的半成品不可比较）: {root}")
    if not (root / "runset.json").is_file():
        raise CompareOperandError(f"runset 缺少 runset.json: {root}")
    try:
        marker = json.loads((root / COMPLETE_MARKER).read_text(encoding="utf-8"))
        index = json.loads((root / "runset.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareOperandError(f"runset 元文件解析失败: {type(exc).__name__}") from exc

    cases: dict[str, dict] = {}
    for entry in index.get("cases") or []:
        rel = entry.get("file")
        f = root / rel if rel else None
        if f is None or not f.is_file():
            raise CompareOperandError(f"runset 索引引用的 case 文件缺失: {rel}")
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CompareOperandError(f"case 文件解析失败: {rel} ({type(exc).__name__})") from exc
        cid = str(payload.get("case_id") or entry.get("case_id"))
        cases[f"{cid}__r{payload.get('repeat_index')}"] = digest_case_payload(payload)

    planned, written = int(index.get("planned_units") or 0), int(index.get("written_units") or 0)
    if planned != written or written != len(cases):
        raise CompareOperandError(f"runset 单元数不自洽: planned={planned} written={written} loaded={len(cases)}")
    if int(marker.get("units") or 0) != len(cases):
        raise CompareOperandError(f"_COMPLETE.json units={marker.get('units')} 与实际 case 数 {len(cases)} 不符")

    return {
        "operand_kind": "runset",
        "operand_path": str(root),
        "runset_id": index.get("runset_id"),
        "s6_schema": index.get("s6_schema"),
        "runner_version": index.get("runner_version"),
        "created_at": index.get("created_at"),
        "manifest": dict(index.get("manifest") or {}),
        "fingerprint": dict(index.get("environment_fingerprint") or {}),
        "planned_units": planned,
        "written_units": written,
        "evaluated_cases": index.get("evaluated_cases"),
        "cases": cases,
        "aggregate": aggregate_of(cases),
    }


def load_baseline(path: str | Path) -> dict[str, Any]:
    """读回 baseline JSON（:func:`emit_baseline` 的逆）；schema 不符即拒绝，绝不猜。"""
    p = Path(path)
    if not p.is_file():
        raise CompareOperandError(f"baseline 文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareOperandError(f"baseline JSON 解析失败: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise CompareOperandError("baseline 顶层必须是 object")
    if data.get("s8_schema") != S8_BASELINE_SCHEMA_VERSION:
        raise CompareOperandError(
            f"baseline s8_schema 不符（期望 {S8_BASELINE_SCHEMA_VERSION}，实得 {data.get('s8_schema')}）"
        )
    if not data.get("cases"):
        raise CompareOperandError("baseline 不含任何 case 摘要")
    agg = data.get("aggregate") or aggregate_of(data["cases"])
    return {
        "operand_kind": "baseline",
        "operand_path": str(p),
        "runset_id": data.get("source_runset_id"),
        "s6_schema": data.get("s6_schema"),
        "runner_version": (data.get("fingerprint") or {}).get("runner_version"),
        "created_at": data.get("created_at"),
        "manifest": data.get("manifest") or {},
        "fingerprint": data.get("fingerprint") or {},
        "planned_units": data.get("planned_units"),
        "written_units": data.get("written_units"),
        "evaluated_cases": data.get("evaluated_cases"),
        "cases": data["cases"],
        "aggregate": agg,
        "baseline_id": data.get("baseline_id"),
    }


def resolve_operand(path: str | Path) -> dict[str, Any]:
    """目录 → runset；``.json`` → baseline。"""
    p = Path(path)
    if p.is_dir():
        return load_runset(p)
    if p.suffix.lower() == ".json":
        return load_baseline(p)
    raise CompareOperandError(f"操作数既不是 runset 目录也不是 baseline JSON: {p}")


def emit_baseline(
    runset_path: str | Path,
    out_path: str | Path,
    *,
    baseline_id: str = DEFAULT_BASELINE_ID,
    note: str = "",
) -> dict[str, Any]:
    """把正式 runset 提升为机器可读 baseline（附录 D.1）。

    只读 runset 落盘产物 —— **不重跑 Runtime、不调用 LLM、不碰任何 DB**。
    写出复用 ``rl.write_json_atomic``（内含 ``assert_no_sensitive`` 双保险）。
    """
    rec = load_runset(runset_path)
    payload = {
        "s8_schema": S8_BASELINE_SCHEMA_VERSION,
        "baseline_id": baseline_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_runset_id": rec["runset_id"],
        "source_operand_path": rec["operand_path"],
        "s6_schema": rec["s6_schema"],
        "runner_version": rec["runner_version"],
        "planned_units": rec["planned_units"],
        "written_units": rec["written_units"],
        "evaluated_cases": rec["evaluated_cases"],
        "manifest": rec["manifest"],
        "fingerprint": rec["fingerprint"],
        "aggregate": rec["aggregate"],
        "cases": rec["cases"],
        "note": note
        or (
            "baseline-v0.1 正式基线。数值为单次读数（repeat=1），只作 S8 delta 的回归参照，不作质量验收线；"
            "identity 轨仅诊断（diagnostic_only），不得当 requirement coverage；"
            "token 用量因 core/llm_client.py 未插桩 response.usage 而不可得，本文件不含估算值。"
        ),
    }
    rl.write_json_atomic(Path(out_path), payload)
    return payload


# ============================================================
# 可比性
# ============================================================


def check_comparability(baseline: dict, candidate: dict) -> dict[str, Any]:
    """逐项比对 :data:`COMPARABILITY_FIELDS`；任一不一致 → comparable=False（不静默混算）。"""
    fb, fc = baseline.get("fingerprint") or {}, candidate.get("fingerprint") or {}
    mismatches: list[dict[str, Any]] = []
    for f in COMPARABILITY_FIELDS:
        bv, cv = fb.get(f), fc.get(f)
        if bv != cv:
            mismatches.append({"field": f, "baseline": bv, "candidate": cv})

    if fb.get("git_commit") != fc.get("git_commit"):
        mismatches.append(
            {
                "field": "git_commit",
                "baseline": fb.get("git_commit"),
                "candidate": fc.get("git_commit"),
                "blocking": False,
                "meaning": "代码版本不同正是 S8 的比较对象，不构成不可比",
            }
        )
    bset, cset = set(baseline.get("cases") or {}), set(candidate.get("cases") or {})
    if bset != cset:
        mismatches.append(
            {
                "field": "case_units",
                "blocking": True,
                "missing_in_candidate": sorted(bset - cset),
                "extra_in_candidate": sorted(cset - bset),
            }
        )
    blocking = [m for m in mismatches if m.get("blocking", True)]
    return {
        "comparable": not blocking,
        "mismatches": mismatches,
        "blocking_mismatches": blocking,
        "fields_checked": list(COMPARABILITY_FIELDS),
        "note": "不可比时所有格子只保留原始读数与差值，不产出 improvement/regression 结论（附录 D.3）",
    }


# ============================================================
# delta
# ============================================================


def flatten(digest: Any, prefix: str = "") -> dict[str, Any]:
    """嵌套摘要 → {格子路径: 标量}。list 只保留数值字段（按元素 key 定位）与长度。"""
    out: dict[str, Any] = {}
    if isinstance(digest, dict):
        for k, v in digest.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(digest, list):
        out[f"{prefix}.len"] = len(digest)
        for item in digest:
            if isinstance(item, dict) and item.get("step"):
                for k, v in item.items():
                    if k != "step" and isinstance(v, (int, float, bool, type(None))):
                        out[f"{prefix}[{item['step']}].{k}"] = v
    elif digest is None or isinstance(digest, (bool, int, float, str)):
        out[prefix] = digest
    return out


def direction_of(cell_path: str) -> str:
    """格子路径 → 方向口径。

    三步归一：① 聚合层的均值格回映射到单 case 口径；② 剥掉 ``metrics.`` 容器段与
    ``.value`` 叶子；③ 表里没有的路径一律 neutral（宁可不判，不可误判）。
    """
    p = cell_path
    if p.startswith("totals."):
        return "neutral"  # 计数与成本类无质量方向，只报差值
    remap = {
        "metric_means.": "hard.{}",
        "soft_score_means.": "soft.scores.{}",
        "via_counts_totals.": "hard.via_counts.{}",
        "s5_verdict_totals.": "soft.verdicts.{}",
    }
    for pre, tmpl in remap.items():
        if p.startswith(pre):
            parts = p.split(".")
            if parts[-1] == "n":
                return "neutral"  # 可评 case 数是口径，不是质量
            p = tmpl.format(parts[1])
            break
    p = p.replace(".metrics.", ".").removesuffix(".value")
    if p in METRIC_DIRECTIONS:
        return METRIC_DIRECTIONS[p]
    return METRIC_DIRECTIONS.get(p.split("[")[0], "neutral")


def diff_cell(
    baseline_value: Any,
    candidate_value: Any,
    *,
    direction: str,
    comparable: bool = True,
) -> dict[str, Any]:
    """一格 delta。**None-safe、无阈值、不判 pass/fail、regression 不抛错。**"""
    cell: dict[str, Any] = {
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta": None,
        "direction": direction,
        "diagnostic_only": direction == "diagnostic_only",
        "verdict": VERDICT_NEUTRAL,
    }
    if baseline_value is None and candidate_value is None:
        cell["verdict"] = VERDICT_BOTH_NULL
        return cell
    if baseline_value is None or candidate_value is None:
        cell["verdict"] = VERDICT_ONE_SIDE_NULL
        return cell
    b_num = isinstance(baseline_value, (int, float)) and not isinstance(baseline_value, bool)
    c_num = isinstance(candidate_value, (int, float)) and not isinstance(candidate_value, bool)
    if not (b_num and c_num):
        cell["verdict"] = VERDICT_UNCHANGED if baseline_value == candidate_value else VERDICT_NEUTRAL
        return cell

    cell["delta"] = round(float(candidate_value) - float(baseline_value), 6)
    if not comparable:
        cell["verdict"] = VERDICT_NOT_COMPARABLE
        return cell
    if direction == "diagnostic_only":
        cell["verdict"] = VERDICT_DIAGNOSTIC
        return cell
    if direction == "neutral":
        cell["verdict"] = VERDICT_NEUTRAL
        return cell
    if cell["delta"] == 0:
        cell["verdict"] = VERDICT_UNCHANGED
    elif direction == "higher_better":
        cell["verdict"] = VERDICT_IMPROVEMENT if cell["delta"] > 0 else VERDICT_REGRESSION
    else:
        cell["verdict"] = VERDICT_REGRESSION if cell["delta"] > 0 else VERDICT_IMPROVEMENT
    return cell


def _status_verdict(bd: dict, cd: dict, comparable: bool) -> str:
    if not comparable:
        return VERDICT_NOT_COMPARABLE
    b_ok, c_ok = bd.get("status") == "completed", cd.get("status") == "completed"
    if b_ok == c_ok:
        return VERDICT_UNCHANGED
    return VERDICT_IMPROVEMENT if c_ok else VERDICT_REGRESSION


def compare_runsets(baseline: dict, candidate: dict) -> dict[str, Any]:
    """产出完整 CompareReport（纯 dict，可直接 JSON 落盘）。"""
    comp = check_comparability(baseline, candidate)
    ok = bool(comp["comparable"])
    bcase, ccase = baseline.get("cases") or {}, candidate.get("cases") or {}

    cases: dict[str, Any] = {}
    for key in sorted(set(bcase) & set(ccase)):
        bf, cf = flatten(bcase[key]), flatten(ccase[key])
        cells = {}
        for path in sorted(set(bf) | set(cf)):
            if path.endswith(".len") and path.replace(".len", "") in bf:
                continue  # 有 step 级明细时不再重复给长度
            cell = diff_cell(bf.get(path), cf.get(path), direction=direction_of(path), comparable=ok)
            cell["path"] = path
            cells[path] = cell
        cases[key] = {
            "status_baseline": bcase[key].get("status"),
            "status_candidate": ccase[key].get("status"),
            "status_verdict": _status_verdict(bcase[key], ccase[key], ok),
            "case_fingerprint_changed": bcase[key].get("case_fingerprint") != ccase[key].get("case_fingerprint"),
            "quality_evaluated_baseline": bcase[key].get("quality_evaluated"),
            "quality_evaluated_candidate": ccase[key].get("quality_evaluated"),
            "cells": cells,
        }

    bflat, cflat = flatten(baseline.get("aggregate") or {}), flatten(candidate.get("aggregate") or {})
    aggregate = {}
    for path in sorted(set(bflat) | set(cflat)):
        cell = diff_cell(bflat.get(path), cflat.get(path), direction=direction_of(path), comparable=ok)
        cell["path"] = path
        aggregate[path] = cell

    counts = {v: 0 for v in ALL_VERDICTS}
    for grp in (*cases.values(), {"cells": aggregate}):
        for cell in (grp.get("cells") or {}).values():
            counts[cell["verdict"]] = counts.get(cell["verdict"], 0) + 1

    def _side(rec: dict) -> dict[str, Any]:
        fp = rec.get("fingerprint") or {}
        return {
            "kind": rec.get("operand_kind"),
            "runset_id": rec.get("runset_id"),
            "baseline_id": rec.get("baseline_id"),
            "operand_path": rec.get("operand_path"),
            "model_name": fp.get("model_name"),
            "git_commit": fp.get("git_commit"),
            "case_set_digest": fp.get("case_set_digest"),
            "completed_cases": (rec.get("aggregate") or {}).get("status_counts", {}).get("completed"),
            "cases_total": (rec.get("aggregate") or {}).get("cases_total"),
        }

    return {
        "s8_schema": S8_COMPARE_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "baseline": _side(baseline),
        "candidate": _side(candidate),
        "comparability": comp,
        "verdict_totals": counts,
        "aggregate": aggregate,
        "cases": cases,
        "unmatched_cases": {
            "missing_in_candidate": sorted(set(bcase) - set(ccase)),
            "extra_in_candidate": sorted(set(ccase) - set(bcase)),
        },
        "caveats": [
            "direction 是「数值方向口径」，不是质量阈值；S8 不判 pass/fail、不设门禁（设计文档 §11.3）。",
            "identity_match_rate / via_counts / identity_diagnostics 全部 diagnostic_only=true："
            "identity 轨仅诊断，禁止当作 requirement coverage（设计文档附录 C.1、裁决 D1）。",
            "pending_review 与 additional_valid_candidates 只按 S3/S5 既有口径计数，不折算为 miss 或 incorrect。",
            "repeat=1 的 baseline 是单次读数，run-to-run 方差未被吸收，delta 只作回归参照（设计文档 §16）。",
            "token 用量不可得：core/llm_client.py 未插桩 response.usage，用量维度只有调用数（挂账⑩）。",
            "comparable=false 时全部格子 verdict=not_comparable，只保留原始读数与差值，不得据此下质量结论。",
        ],
    }


def report_from_payload(payload: dict) -> dict[str, Any]:
    """读回 compare 报告（只校验 schema 与必备段，不重算）。"""
    if not isinstance(payload, dict) or payload.get("s8_schema") != S8_COMPARE_SCHEMA_VERSION:
        raise CompareOperandError(f"compare 报告 s8_schema 不符（期望 {S8_COMPARE_SCHEMA_VERSION}）")
    for k in ("comparability", "aggregate", "cases", "caveats", "verdict_totals"):
        if k not in payload:
            raise CompareOperandError(f"compare 报告缺少必备段: {k}")
    return payload


def summarize_report(report: dict) -> list[str]:
    """人类可读摘要行（CLI 打印用）。"""
    lines: list[str] = []
    comp = report["comparability"]
    lines.append(f"comparable = {comp['comparable']}（阻塞项 {len(comp['blocking_mismatches'])}）")
    for m in comp["mismatches"]:
        lines.append(f"  ! {m['field']}: baseline={m.get('baseline')!r} candidate={m.get('candidate')!r}")
    vt = report["verdict_totals"]
    lines.append(
        "格子判定: improvement={improvement} regression={regression} unchanged={unchanged} "
        "neutral={neutral} diagnostic_only={diagnostic_only} one_side_null={one_side_null} "
        "unavailable_both={unavailable_both} not_comparable={not_comparable}".format(**vt)
    )
    lines.append("")
    lines.append(f"{'case':28}{'status b→c':22}" + "".join(f"{c[:13]:>14}" for c in HARD_CELLS))
    for key, rec in report["cases"].items():
        cells = rec["cells"]
        row = f"{key:28}{str(rec['status_baseline'])[:9] + '→' + str(rec['status_candidate'])[:9]:22}"
        for c in HARD_CELLS:
            cell = cells.get(f"hard.metrics.{c}.value") or {}
            d = cell.get("delta")
            row += f"{'-' if d is None else f'{d:+.3f}':>14}"
        lines.append(row)
    lines.append("")
    lines.append("聚合层（baseline → candidate）:")
    for path in sorted(report["aggregate"]):
        cell = report["aggregate"][path]
        if cell["verdict"] in (VERDICT_BOTH_NULL,):
            continue
        if not path.startswith(("metric_means.", "soft_score_means.", "totals.")):
            continue
        lines.append(
            f"  {path:56} {cell['baseline']!r:>14} → {cell['candidate']!r:>14}  Δ={cell['delta']} {cell['verdict']}"
        )
    return lines
