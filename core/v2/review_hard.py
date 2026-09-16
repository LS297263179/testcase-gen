"""V2 Step 7 Hard Review Engine - 代码算的确定性硬指标（不调 LLM）。

四个硬维度（用户冻结的混合分工里"代码算"的部分）：
  - coverage：双指标（strategy_obligation_coverage + requirement_item_coverage + uncovered_item_ids），
    不合成模糊单一分；未覆盖 item 产 COVERAGE finding。
  - duplication：两级（EXACT 按 content_hash 精确 / SEMANTIC 按相似度），只报告 + auto_fixable，绝不删（Step 8）。
  - consistency：display_id 格式 / display_id 唯一 / module 非空 等规范性。
  - executability 结构层：steps 非空 / 每步 action 非空 / expected 非空（形式完整性，语义层交 LLM）。

所有 finding 的 provenance=VALIDATOR（代码硬指标），与 LLM 软判断（review_soft.py）区分。
★ Step 7 只发现问题、只标记 auto_fixable，绝不修改/删除/重生成用例（Optimizer 是 Step 8）。
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

from core.schemas import (
    CoverageDetail,
    DuplicateLevel,
    Provenance,
    RequirementItem,
    ReviewDimension,
    ReviewFinding,
    Severity,
    TargetType,
    TestCase,
    TestPoint,
)
from core.v2.fingerprint import normalize_text
from core.v2.tp_validator import build_coverage_report

logger = logging.getLogger("v2.review_hard")

# 语义重复判定阈值（归一化 title+steps 的相似度）；低于精确重复但高于此值 → SEMANTIC_DUPLICATE
SEMANTIC_DUPLICATE_THRESHOLD = 0.85

# display_id 规范格式（TC_001）
_DISPLAY_ID_RE = re.compile(r"^TC_\d{3,}$")


@dataclass
class HardReviewResult:
    """硬指标评审产物：四个硬维度分数 + coverage 双指标明细 + validator findings + 各维度 reason。"""

    coverage_score: float = 0.0
    duplication_score: float = 100.0
    consistency_score: float = 100.0
    executability_structural_score: float = 100.0
    coverage_detail: CoverageDetail | None = None
    findings: list[ReviewFinding] = field(default_factory=list)
    dimension_reasons: dict[str, str] = field(default_factory=dict)


# ============================================================
# coverage：双指标（不合成模糊分）
# ============================================================


def compute_coverage(
    test_points: list[TestPoint], items: list[RequirementItem], strategy_coverage: float
) -> tuple[float, CoverageDetail, list[ReviewFinding], str]:
    """算 coverage 维度：返回 (score, CoverageDetail, findings, reason)。

    - score = requirement_item_coverage × 100（item 覆盖率更能反映"需求被测试覆盖程度"；
      strategy_obligation_coverage 是 Step 4 保证的结构性硬指标，放 detail 佐证，避免被其 =1.0 稀释）
    - detail 保留双指标 + uncovered_item_ids（用户核心要求：Reviewer 能定位"哪些 item 未覆盖"）
    - 每个未覆盖 item → COVERAGE finding（target=REQUIREMENT_ITEM, provenance=VALIDATOR）
    """
    report = build_coverage_report(test_points, items)
    detail = CoverageDetail(
        strategy_obligation_coverage=round(strategy_coverage, 4),
        requirement_item_coverage=round(report.coverage_ratio, 4),
        uncovered_item_ids=list(report.uncovered_item_ids),
    )
    findings: list[ReviewFinding] = []
    for item_id in report.uncovered_item_ids:
        findings.append(
            ReviewFinding(
                dimension=ReviewDimension.COVERAGE,
                severity=Severity.MAJOR,
                target_type=TargetType.REQUIREMENT_ITEM,
                target_id=item_id,
                issue="该需求项未被任何测试点覆盖（存在测试盲区）",
                suggestion="补充针对该需求项的测试点/用例",
                provenance=Provenance.VALIDATOR,
                auto_fixable=False,
            )
        )
    score = round(report.coverage_ratio * 100, 2)
    reason = (
        f"需求项覆盖率 {report.coverage_ratio:.0%}（{len(report.covered_item_ids)}/{report.total_items}），"
        f"策略义务覆盖率 {strategy_coverage:.0%}；未覆盖 {len(report.uncovered_item_ids)} 项"
    )
    return score, detail, findings, reason


# ============================================================
# duplication：两级（EXACT / SEMANTIC），只报告不删
# ============================================================


def _case_signature(tc: TestCase) -> str:
    """用例的语义签名（归一化 title + steps 文本），用于相似度比较。"""
    return normalize_text(f"{tc.title} {tc.render_steps_text()}")


def compute_duplication(
    test_cases: list[TestCase], threshold: float = SEMANTIC_DUPLICATE_THRESHOLD
) -> tuple[float, list[ReviewFinding], str]:
    """算 duplication 维度：返回 (score, findings, reason)。

    两级检测（用户要求，供 Step 8 区分处理）：
      - EXACT：content_hash 相同（内容完全一致）
      - SEMANTIC：content_hash 不同但归一化 title+steps 相似度 ≥ threshold
    每对重复产一条 DUPLICATION finding（target=后一条用例，counterpart=前一条），auto_fixable=True。
    ★ 只报告，绝不 merge/delete（Step 8 Optimizer 职责）。
    score = 100 - (涉及重复的用例数 / 总用例数) × 100。
    """
    findings: list[ReviewFinding] = []
    if not test_cases:
        return 100.0, findings, "无用例，无重复"

    flagged: set[str] = set()  # 涉及重复的用例 id（去重计数）
    exact_count = 0
    semantic_count = 0
    n = len(test_cases)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = test_cases[i], test_cases[j]
            # EXACT：content_hash 相同
            if a.content_hash and a.content_hash == b.content_hash:
                level = DuplicateLevel.EXACT
                similarity = 1.0
                exact_count += 1
            else:
                # SEMANTIC：签名相似度
                ratio = difflib.SequenceMatcher(None, _case_signature(a), _case_signature(b)).ratio()
                if ratio >= threshold:
                    level = DuplicateLevel.SEMANTIC
                    similarity = round(ratio, 4)
                    semantic_count += 1
                else:
                    continue
            flagged.add(a.id)
            flagged.add(b.id)
            findings.append(
                ReviewFinding(
                    dimension=ReviewDimension.DUPLICATION,
                    severity=Severity.MINOR,
                    target_type=TargetType.TESTCASE,
                    target_id=b.id,
                    issue=f"与用例 {a.display_id} {'完全重复' if level == DuplicateLevel.EXACT else '语义高度相似'}（相似度 {similarity}）",
                    suggestion=f"建议 Step 8 去重时保留其一（ counterpart={a.display_id} ）",
                    provenance=Provenance.VALIDATOR,
                    auto_fixable=True,
                    detail={
                        "duplicate_level": level.value,
                        "similarity": similarity,
                        "counterpart_id": a.id,
                        "counterpart_display_id": a.display_id,
                    },
                )
            )
    score = round(100 - (len(flagged) / n) * 100, 2)
    reason = f"检出精确重复 {exact_count} 对、语义重复 {semantic_count} 对，涉及 {len(flagged)}/{n} 条用例"
    return score, findings, reason


# ============================================================
# consistency：规范性（display_id 格式/唯一 + module 非空）
# ============================================================


def compute_consistency(test_cases: list[TestCase]) -> tuple[float, list[ReviewFinding], str]:
    """算 consistency 维度：返回 (score, findings, reason)。

    检查：display_id 格式（TC_\\d+）、display_id 在 run 内唯一、module 非空、title 非空。
    违规 → CONSISTENCY finding（target=TESTCASE, provenance=VALIDATOR, detail={violation}）。
    score = 100 - (违规用例数 / 总数) × 100。
    """
    findings: list[ReviewFinding] = []
    if not test_cases:
        return 100.0, findings, "无用例"
    seen_display: dict[str, str] = {}
    violating: set[str] = set()
    for tc in test_cases:
        violations: list[str] = []
        if not _DISPLAY_ID_RE.match(tc.display_id or ""):
            violations.append(f"display_id 格式不规范: {tc.display_id!r}（应形如 TC_001）")
        if not (tc.module or "").strip():
            violations.append("module 为空")
        if not (tc.title or "").strip():
            violations.append("title 为空")
        if tc.display_id in seen_display:
            violations.append(f"display_id 与 {seen_display[tc.display_id]} 重复")
        else:
            seen_display[tc.display_id] = tc.display_id
        if violations:
            violating.add(tc.id)
            findings.append(
                ReviewFinding(
                    dimension=ReviewDimension.CONSISTENCY,
                    severity=Severity.MINOR,
                    target_type=TargetType.TESTCASE,
                    target_id=tc.id,
                    issue="；".join(violations),
                    suggestion="规范化 display_id/module/title",
                    provenance=Provenance.VALIDATOR,
                    auto_fixable=True,
                    detail={"violation": violations},
                )
            )
    score = round(100 - (len(violating) / len(test_cases)) * 100, 2)
    reason = f"{len(violating)}/{len(test_cases)} 条用例存在规范性问题"
    return score, findings, reason


# ============================================================
# executability 结构层（形式完整性；语义层交 LLM）
# ============================================================


def compute_executability_structural(test_cases: list[TestCase]) -> tuple[float, list[ReviewFinding], str]:
    """算 executability 结构层子分：返回 (score, findings, reason)。

    检查形式完整性：steps 非空、每步 action 非空、expected 非空。
    ★ 结构合规只证明"形式上有步骤"，不证明"真能执行"——语义层由 LLM 评（review_soft）。
    缺陷 → EXECUTABILITY finding（provenance=VALIDATOR, detail={violation}）。
    score = 100 - (结构缺陷用例数 / 总数) × 100。
    """
    findings: list[ReviewFinding] = []
    if not test_cases:
        return 100.0, findings, "无用例"
    defective: set[str] = set()
    for tc in test_cases:
        violations: list[str] = []
        if not tc.steps:
            violations.append("steps 为空")
        if any(not (s.action or "").strip() for s in tc.steps):
            violations.append("存在 action 为空的步骤")
        if not (tc.expected or "").strip():
            violations.append("expected 为空")
        if violations:
            defective.add(tc.id)
            findings.append(
                ReviewFinding(
                    dimension=ReviewDimension.EXECUTABILITY,
                    severity=Severity.MAJOR,
                    target_type=TargetType.TESTCASE,
                    target_id=tc.id,
                    issue="结构缺陷：" + "；".join(violations),
                    suggestion="补全步骤/预期，使用例形式完整",
                    provenance=Provenance.VALIDATOR,
                    auto_fixable=False,
                    detail={"violation": violations, "layer": "structural"},
                )
            )
    score = round(100 - (len(defective) / len(test_cases)) * 100, 2)
    reason = f"{len(defective)}/{len(test_cases)} 条用例存在结构缺陷（steps/action/expected）"
    return score, findings, reason


# ============================================================
# 汇总入口
# ============================================================


def run_hard_review(
    test_cases: list[TestCase],
    test_points: list[TestPoint],
    items: list[RequirementItem],
    strategy_obligation_coverage: float,
) -> HardReviewResult:
    """跑全部硬指标维度，汇总分数 + findings + coverage_detail + 各维度 reason。

    纯代码、确定性、可复现（同一输入多次调用结果完全一致——门槛 12 稳定性）。
    """
    result = HardReviewResult()

    cov_score, cov_detail, cov_findings, cov_reason = compute_coverage(test_points, items, strategy_obligation_coverage)
    result.coverage_score = cov_score
    result.coverage_detail = cov_detail
    result.findings.extend(cov_findings)
    result.dimension_reasons["coverage"] = cov_reason

    dup_score, dup_findings, dup_reason = compute_duplication(test_cases)
    result.duplication_score = dup_score
    result.findings.extend(dup_findings)
    result.dimension_reasons["duplication"] = dup_reason

    con_score, con_findings, con_reason = compute_consistency(test_cases)
    result.consistency_score = con_score
    result.findings.extend(con_findings)
    result.dimension_reasons["consistency"] = con_reason

    exe_score, exe_findings, exe_reason = compute_executability_structural(test_cases)
    result.executability_structural_score = exe_score
    result.findings.extend(exe_findings)
    result.dimension_reasons["executability_structural"] = exe_reason

    logger.info(
        "硬指标评审: coverage=%.1f duplication=%.1f consistency=%.1f executability_structural=%.1f findings=%d",
        cov_score,
        dup_score,
        con_score,
        exe_score,
        len(result.findings),
    )
    return result
