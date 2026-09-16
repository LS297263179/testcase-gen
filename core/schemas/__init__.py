"""V2 数据模型（Pydantic v2）- 唯一真源。

设计基线：docs/v2/step1-data-model.md（rev.2）。
Step 1 冻结全部核心实体；🟡 标记的 reserved 实体只定义形状、不建表、不接管线。
"""

from core.schemas.common import (
    ALLOWED_TRANSITIONS,
    SOURCE_CONFIDENCE,
    AffectedReason,
    ChangeType,
    ConfidenceLevel,
    DataType,
    DuplicateLevel,
    EntityBase,
    EntityStatus,
    ExpressionType,
    GenerationMode,
    GenerationScope,
    ImpactLevel,
    ObligationStatus,
    Priority,
    Provenance,
    RequirementItemType,
    ReviewDimension,
    ReviewTriggerType,
    RunStatus,
    Severity,
    SourceType,
    TargetType,
    Technique,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    Ulid,
    can_transition,
    confidence_to_level,
    new_ulid,
)
from core.schemas.preference import Preference, PreferenceLink
from core.schemas.requirement import (
    BusinessRule,
    FieldSpec,
    PermissionRule,
    RequirementDoc,
    RequirementItem,
    RequirementVersion,
    SourceRef,
)
from core.schemas.reserved import LLMInvocation, TestCaseRevision, TestScenario
from core.schemas.review import CoverageDetail, ExecutabilityDetail, ReviewFinding, ReviewReport, ReviewScores
from core.schemas.run import GenerationConfig, Run, RunCounts
from core.schemas.strategy import CoverageObligation
from core.schemas.testcase import DataPlanItem, TestCase, TestStep
from core.schemas.testpoint import TestPoint

__all__ = [
    # common
    "ALLOWED_TRANSITIONS",
    "SOURCE_CONFIDENCE",
    "AffectedReason",
    "ChangeType",
    "ConfidenceLevel",
    "DataType",
    "DuplicateLevel",
    "EntityBase",
    "EntityStatus",
    "ExpressionType",
    "GenerationMode",
    "GenerationScope",
    "ImpactLevel",
    "ObligationStatus",
    "Priority",
    "Provenance",
    "RequirementItemType",
    "ReviewDimension",
    "ReviewTriggerType",
    "RunStatus",
    "Severity",
    "SourceType",
    "TargetType",
    "TestCaseStatus",
    "TestCaseType",
    "Technique",
    "TestDimension",
    "Ulid",
    "can_transition",
    "confidence_to_level",
    "new_ulid",
    # requirement
    "BusinessRule",
    "FieldSpec",
    "PermissionRule",
    "RequirementDoc",
    "RequirementItem",
    "RequirementVersion",
    "SourceRef",
    # test assets
    "TestPoint",
    "TestCase",
    "TestStep",
    "DataPlanItem",
    "CoverageObligation",
    # review
    "ReviewScores",
    "ReviewFinding",
    "ReviewReport",
    "CoverageDetail",
    "ExecutabilityDetail",
    # run
    "GenerationConfig",
    "RunCounts",
    "Run",
    # preference
    "Preference",
    "PreferenceLink",
    # reserved (🟡 design-only)
    "TestScenario",
    "TestCaseRevision",
    "LLMInvocation",
]
