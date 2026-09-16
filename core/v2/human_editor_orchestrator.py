"""V2 Step 9 Human Editor Orchestrator - 编排人工编辑 + 重评审。

对应 PROGRESS.md §9 蓝图 `Human Review(人工确认/编辑)`。职责：
  - edit_test_case：包装 human_editor.edit_test_case（单条编辑）
  - re_review_test_cases：批量重评审接口（第一版内部统一 Review，不引入 queue/parallel/retry）

★ 职责分离（用户冻结）：
  - Human Editor（human_editor.py）= 执行编辑动作（白名单 + Revision + Validator + 乐观锁）
  - Orchestrator（本文件）= 负责流程编排（编辑 → 重评审）
  - 不自动触发重评审（用户主动点击才 Review）

★ Step 9 完成后的系统变化：
  LLM 生成 → 代码验证 → AI Review → 自动优化 → 人工修改 → 再次验证 → 再次 Review
  这是 AI + 代码 + 人 三者协同的完整测试设计流程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import ReviewTriggerType
from core.v2.human_editor import EditResult, edit_test_case
from core.v2.review_orchestrator import ReviewResult, review_test_cases

logger = logging.getLogger("v2.human_editor_orchestrator")


@dataclass
class ReReviewResult:
    """批量重评审产物（对齐 Step 3/4/5/6/7/8 的 *Result 约定）。

    第一版：接口层支持 test_case_ids list，内部统一调用 review_test_cases（按 run_id 评审全部）。
    未来可扩展：batch queue / parallel review / partial failure / retry。
    """

    run_id: str
    review_result: ReviewResult | None = None
    requested_case_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def edit_case(
    test_case_id: str,
    updates: dict,
    *,
    expected_updated_at: str | None = None,
) -> EditResult:
    """人工编辑单条 TestCase（包装 human_editor.edit_test_case）。

    流程：白名单过滤 → 乐观锁 → Revision 快照 → 更新 → Validator → 状态机流转。
    ★ 不自动触发重评审（保存仅标记 RE_REVIEW_REQUIRED）。
    """
    return edit_test_case(test_case_id, updates, expected_updated_at=expected_updated_at)


def re_review_test_cases(
    client,
    *,
    run_id: str,
    test_case_ids: list[str] | None = None,
) -> ReReviewResult:
    """批量重评审接口（第一版内部统一 Review）。

    参数：
      - run_id：目标 Run
      - test_case_ids：可选，指定重评审的用例 id 列表（第一版仅记录，内部仍按 run_id 统一 Review）

    流程：
      1. 调用 review_test_cases(trigger_type=AFTER_HUMAN_EDIT)
      2. review_orchestrator 内部处理 VALIDATED / RE_REVIEW_REQUIRED 状态的用例
      3. 产出 ReviewReport（review_revision+1）
      4. 用例状态 → REVIEWED

    ★ 第一版不引入 batch queue / parallel review / partial failure / retry。
    ★ test_case_ids 参数仅用于审计/日志，内部统一按 run_id Review。
    """
    result = ReReviewResult(run_id=run_id, requested_case_ids=test_case_ids or [])

    if test_case_ids:
        logger.info(
            "Step 9 重评审: run=%s 请求 %d 条用例（第一版内部统一 Review）",
            run_id,
            len(test_case_ids),
        )

    # 调用 Step 7 review_test_cases（trigger=AFTER_HUMAN_EDIT）
    review_result = review_test_cases(
        client,
        run_id=run_id,
        trigger_type=ReviewTriggerType.AFTER_HUMAN_EDIT,
    )
    result.review_result = review_result
    result.issues = list(review_result.issues) if review_result.issues else []

    logger.info(
        "Step 9 重评审完成: run=%s reviewed=%d issues=%d",
        run_id,
        review_result.reviewed_count,
        len(result.issues),
    )
    return result
