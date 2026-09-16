"""V2 Step 8 Dedup Optimizer - 消费 Step 7 duplication findings，执行确定性去重归档。

对应 PROGRESS.md §9 蓝图 `Optimizer(自动优化/去重)`。职责：
  - 只消费 Step 7 已确认的 duplication finding（auto_fixable=True），不重新做重复判断
  - Duplicate pair canonicalize（防止 A↔B 两条 finding 导致两边都被归档）
  - Survivor 优先级：HUMAN > OPTIMIZER > LLM > STRATEGY > VALIDATOR > MIGRATED → P0>P1>P2>P3 → created_at 越早 → display_id 越小
  - 归档（ARCHIVED）不物理删除，保留审计痕迹
  - 只改变 TestCase 状态，不改变内容（Dedup Optimizer，不是全能 Optimizer）

★ Step 8 铁律（用户冻结）：
  - 不重新判重（消费 Step 7 finding）
  - 不覆盖人工意图（HUMAN provenance 最优先保留）
  - 不物理删除（ARCHIVED 保留审计）
  - 不修改 TestCase 内容（只改 status）
  - 幂等可重复执行（双边状态检查 + canonical pair 去重）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    Priority,
    Provenance,
    ReviewDimension,
    ReviewFinding,
    TestCase,
    TestCaseStatus,
)
from core.v2 import repository as repo

logger = logging.getLogger("v2.optimizer")

# ============================================================
# Survivor 优先级权重（确定性可解释，用户冻结）
# ============================================================

# provenance 权重：HUMAN 最优先保留（不覆盖人工意图），MIGRATED 最末
PROVENANCE_WEIGHT: dict[Provenance, int] = {
    Provenance.HUMAN: 0,
    Provenance.OPTIMIZER: 1,
    Provenance.LLM: 2,
    Provenance.STRATEGY: 3,
    Provenance.VALIDATOR: 4,
    Provenance.MIGRATED: 5,
    Provenance.XMIND: 6,
}

# priority 权重：P0 最优先保留
PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.P0: 0,
    Priority.P1: 1,
    Priority.P2: 2,
    Priority.P3: 3,
}


# ============================================================
# OptimizerAction / OptimizerResult（内存 dataclass，不持久化）
# ============================================================


@dataclass
class OptimizerAction:
    """一次去重动作的完整审计记录。

    对于 canonical pair (A, B)，同时记录两边 id + kept/archived，审计最清楚。
    finding_id 表示是哪条 ReviewFinding 触发了此动作。
    """

    finding_id: str  # 触发此动作的 ReviewFinding.id
    case_a_id: str  # canonical pair 的 A（min_id）
    case_b_id: str  # canonical pair 的 B（max_id）
    kept_case_id: str  # 保留的用例 id
    archived_case_id: str  # 归档的用例 id
    action: str = "archive"  # 动作类型（第一版只有 archive）
    reason: str = ""  # "exact_duplicate" | "semantic_duplicate"
    similarity: float | None = None  # SEMANTIC 时记录相似度（审计用）


@dataclass
class OptimizerResult:
    """Step 8 顶层产物（对齐 Step 3/4/5/6/7 的 *Result 约定）。

    第一版不持久化（内存 dataclass），ReviewFinding + OptimizerResult + TestCase status change 已足够审计。
    未来需要长期记录每次 optimizer 执行动作时再持久化成 optimizer_actions 表。
    """

    run_id: str
    processed_findings: int = 0  # 处理的 duplication finding 数
    archived_cases: int = 0  # 实际归档的用例数
    skipped_findings: int = 0  # 跳过的 finding 数（already_resolved / source_not_reviewed）
    actions: list[OptimizerAction] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)  # {reason: count}


# ============================================================
# Canonicalize pairs（P0 坑防护）
# ============================================================


def canonicalize_pairs(findings: list[ReviewFinding]) -> dict[tuple[str, str], ReviewFinding]:
    """A↔B 两条 finding → 唯一 pair (min_id, max_id)，保留第一个 finding 作为触发源。

    Step 7 review_hard.compute_duplication 对每对重复产 1 条 finding（target=后者，counterpart=前者）。
    但如果 ReviewReport 同时有 "A duplicate B" 和 "B duplicate A"（理论上不会，但防御性编程），
    canonicalize 后只保留一条 pair，防止两边都被归档。

    返回：{(min_id, max_id): finding}，finding 是触发此 pair 的第一个 ReviewFinding。
    """
    pairs: dict[tuple[str, str], ReviewFinding] = {}
    for f in findings:
        # 只处理 duplication 维度 + auto_fixable=True
        if f.dimension != ReviewDimension.DUPLICATION or not f.auto_fixable:
            continue
        # detail 必须含 counterpart_id
        if not f.detail:
            continue
        counterpart_id = f.detail.get("counterpart_id")
        if not counterpart_id:
            continue
        # canonical pair: (min_id, max_id)
        pair = (min(f.target_id, counterpart_id), max(f.target_id, counterpart_id))
        if pair not in pairs:
            pairs[pair] = f  # 保留第一个 finding
    return pairs


# ============================================================
# 双边状态检查（幂等核心）
# ============================================================


def check_pair_status(a: TestCase, b: TestCase) -> tuple[bool, str]:
    """检查一对用例是否应该处理。

    返回 (should_process, skip_reason)：
      - 双方都是 REVIEWED → (True, "")
      - 任一方非 REVIEWED：
        - 任一方 ARCHIVED → (False, "already_resolved")
        - 否则 → (False, "source_not_reviewed")

    幂等保证：已归档的不再动；多次运行结果一致。
    """
    if a.status != TestCaseStatus.REVIEWED or b.status != TestCaseStatus.REVIEWED:
        if a.status == TestCaseStatus.ARCHIVED or b.status == TestCaseStatus.ARCHIVED:
            return False, "already_resolved"
        return False, "source_not_reviewed"
    return True, ""


# ============================================================
# Survivor 排序（确定性可解释）
# ============================================================


def survivor_sort_key(tc: TestCase) -> tuple:
    """Survivor 排序键：权重小者优先保留。

    排序维度（用户冻结）：
      1. provenance: HUMAN(0) > OPTIMIZER(1) > LLM(2) > STRATEGY(3) > VALIDATOR(4) > MIGRATED(5)
      2. priority: P0(0) > P1(1) > P2(2) > P3(3)
      3. created_at: 越早优先（ISO 字符串可直接比较）
      4. display_id: 越小优先（最终 tie-break，TC_001 < TC_002）

    ★ 不覆盖人工意图：HUMAN provenance 的用例永远优先保留。
    ★ created_at 越早优先：生成序早的用例更稳定（文档明确，防止实现人员误解为"最新生成的优先"）。
    """
    return (
        PROVENANCE_WEIGHT.get(tc.provenance, 99),
        PRIORITY_WEIGHT.get(tc.priority, 99),
        tc.created_at.isoformat(),  # ISO 字符串可直接比较
        tc.display_id,
    )


def determine_survivor(a: TestCase, b: TestCase) -> tuple[TestCase, TestCase]:
    """确定一对用例中谁保留、谁归档。

    返回 (survivor, loser)：survivor 保留，loser 归档。
    """
    if survivor_sort_key(a) <= survivor_sort_key(b):
        return a, b
    return b, a


# ============================================================
# 去重主逻辑
# ============================================================


def deduplicate_by_findings(run_id: str, findings: list[ReviewFinding]) -> OptimizerResult:
    """消费 Step 7 duplication findings，执行去重归档。

    流程：
      1. Canonicalize duplicate pairs（A↔B → 唯一 pair）
      2. For each pair:
         - 拉 A, B（repo.get_test_case）
         - 双边状态检查（任一方非 REVIEWED → skip）
         - Determine survivor（HUMAN>LLM, P0>P1, created_at 早, display_id 小）
         - Archive loser（status→ARCHIVED + update_test_case_status）
         - Record OptimizerAction
      3. 返回 OptimizerResult

    ★ 只改变 TestCase 状态，不改变内容（title/steps/expected/priority 不变）。
    ★ 幂等：已归档的不再动；多次运行结果一致。
    """
    result = OptimizerResult(run_id=run_id)

    # 1. Canonicalize pairs
    pairs = canonicalize_pairs(findings)
    result.processed_findings = len(pairs)

    if not pairs:
        logger.info("Step 8 去重: run=%s 无 duplication finding 可处理", run_id)
        return result

    # 2. 处理每对
    for (a_id, b_id), finding in pairs.items():
        # 拉用例
        a = repo.get_test_case(a_id)
        b = repo.get_test_case(b_id)
        if a is None or b is None:
            result.skipped_findings += 1
            result.skip_reasons["case_not_found"] = result.skip_reasons.get("case_not_found", 0) + 1
            logger.warning("Step 8 去重: pair=(%s, %s) 用例不存在，skip", a_id, b_id)
            continue

        # 双边状态检查
        should_process, skip_reason = check_pair_status(a, b)
        if not should_process:
            result.skipped_findings += 1
            result.skip_reasons[skip_reason] = result.skip_reasons.get(skip_reason, 0) + 1
            logger.debug("Step 8 去重: pair=(%s, %s) skip=%s", a_id, b_id, skip_reason)
            continue

        # Determine survivor
        survivor, loser = determine_survivor(a, b)

        # Archive loser（只改 status，不改内容）
        loser.transition_to(TestCaseStatus.ARCHIVED)  # 状态机保证合法（REVIEWED→ARCHIVED 已放开）
        repo.update_test_case_status(loser.id, TestCaseStatus.ARCHIVED.value)
        result.archived_cases += 1

        # Record action
        duplicate_level = finding.detail.get("duplicate_level", "semantic") if finding.detail else "semantic"
        reason = "exact_duplicate" if duplicate_level == "exact" else "semantic_duplicate"
        similarity = finding.detail.get("similarity") if finding.detail else None

        action = OptimizerAction(
            finding_id=finding.id,
            case_a_id=a_id,
            case_b_id=b_id,
            kept_case_id=survivor.id,
            archived_case_id=loser.id,
            action="archive",
            reason=reason,
            similarity=similarity,
        )
        result.actions.append(action)

        logger.info(
            "Step 8 去重: pair=(%s, %s) kept=%s archived=%s reason=%s similarity=%s",
            a.display_id,
            b.display_id,
            survivor.display_id,
            loser.display_id,
            reason,
            similarity,
        )

    logger.info(
        "Step 8 去重完成: run=%s processed=%d archived=%d skipped=%d",
        run_id,
        result.processed_findings,
        result.archived_cases,
        result.skipped_findings,
    )
    return result
