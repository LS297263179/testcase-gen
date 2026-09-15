"""V2 数据模型通用定义 - ULID / 实体基类 / 枚举 / 状态机 / 置信度等级。

本模块是 V2 Schema 的地基，被 core.schemas.* 全部依赖。
设计原则见 docs/v2/step1-data-model.md §3.1。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ============================================================
# ULID（内置实现，零依赖）— 全局稳定、时间有序主键
# ============================================================

# Crockford Base32 字母表（排除 I L O U，避免歧义）
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """生成 ULID：48-bit 毫秒时间戳 + 80-bit 随机数，Crockford Base32，26 字符。

    特性：时间有序（可按生成顺序排序/分页）、全局唯一、无需中心协调。
    """
    timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48-bit
    randomness = int.from_bytes(os.urandom(10), "big")  # 80-bit
    value = (timestamp_ms << 80) | randomness  # 128-bit

    chars = []
    for _ in range(26):  # 26 * 5 = 130 bit，覆盖 128 bit（最高位补零）
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


# ULID 类型：26 字符 Crockford Base32（不含 I L O U）
Ulid = Annotated[str, StringConstraints(min_length=26, max_length=26, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]


def _utcnow() -> datetime:
    """统一的当前时间（UTC，带时区）"""
    return datetime.now(UTC)


# ============================================================
# 实体基类
# ============================================================


class EntityBase(BaseModel):
    """所有持久化实体的基类：自动 ID + 时间戳 + schema 版本，严格模式。"""

    id: Ulid = Field(default_factory=new_ulid)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    schema_version: int = 2

    model_config = ConfigDict(extra="forbid")


# ============================================================
# 枚举定义
# ============================================================


class Priority(StrEnum):
    """用例/需求项优先级"""

    P0 = "P0"  # 阻塞 / 核心
    P1 = "P1"  # 严重 / 重要
    P2 = "P2"  # 一般
    P3 = "P3"  # 轻微 / 边缘


class Provenance(StrEnum):
    """实体来源，用于追溯"是谁产生的" """

    LLM = "llm"
    STRATEGY = "strategy"  # 策略引擎（确定性代码）
    HUMAN = "human"  # 人工编辑 / 确认
    VALIDATOR = "validator"  # 校验器产生（如 finding）
    OPTIMIZER = "optimizer"
    MIGRATED = "migrated"  # V1 迁移
    XMIND = "xmind"


class EntityStatus(StrEnum):
    """通用实体状态（Doc / Version / Item / Point 用）"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    """需求来源类型"""

    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    WORD = "word"
    IMAGE = "image"
    EXCEL = "excel"
    XMIND = "xmind"


class TestCaseType(StrEnum):
    """测试用例类型（P1-1：枚举化，取代 V1 的自由字符串）"""

    __test__ = False  # 名称以 Test 开头但非 pytest 测试类，禁止被收集

    FUNCTIONAL = "functional"
    BOUNDARY = "boundary"
    EQUIVALENCE = "equivalence"
    EXCEPTION = "exception"
    PERMISSION = "permission"
    COMPATIBILITY = "compatibility"
    PERFORMANCE = "performance"
    SECURITY = "security"
    STATE = "state"
    LINKAGE = "linkage"


class TestCaseStatus(StrEnum):
    """测试用例生命周期状态机（P0-6）"""

    __test__ = False  # 非 pytest 测试类，禁止被收集

    GENERATED = "generated"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    REVIEWED = "reviewed"
    EDITED = "edited"
    RE_REVIEW_REQUIRED = "re_review_required"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


# 状态机允许转移表（代码强制；非法转移抛错）
ALLOWED_TRANSITIONS: dict[TestCaseStatus, set[TestCaseStatus]] = {
    TestCaseStatus.GENERATED: {TestCaseStatus.VALIDATED, TestCaseStatus.VALIDATION_FAILED},
    TestCaseStatus.VALIDATION_FAILED: {TestCaseStatus.GENERATED},  # 修复后重生成
    TestCaseStatus.VALIDATED: {TestCaseStatus.REVIEWED},
    TestCaseStatus.REVIEWED: {TestCaseStatus.CONFIRMED, TestCaseStatus.EDITED},
    TestCaseStatus.EDITED: {TestCaseStatus.RE_REVIEW_REQUIRED},  # 人工改过必须重审
    TestCaseStatus.RE_REVIEW_REQUIRED: {TestCaseStatus.REVIEWED},
    TestCaseStatus.CONFIRMED: {TestCaseStatus.ARCHIVED, TestCaseStatus.EDITED},
    TestCaseStatus.ARCHIVED: set(),
}


def can_transition(current: TestCaseStatus, target: TestCaseStatus) -> bool:
    """判断状态转移是否合法"""
    return target in ALLOWED_TRANSITIONS.get(current, set())


class ConfidenceLevel(StrEnum):
    """数据可信等级（P1-4）：前端据此提示人工确认"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# 来源 → 参考权重（仅用于排序参考，非科学概率）
SOURCE_CONFIDENCE: dict[str, float] = {
    "human_confirmed": 1.0,
    "code_validated": 0.95,
    "llm_high_confidence": 0.8,
    "llm_low_confidence": 0.5,
    "migrated": 0.3,
}

# 数值置信度 → 等级 的默认阈值（可配）
CONFIDENCE_THRESHOLDS: tuple[float, float] = (0.75, 0.45)  # (high_min, medium_min)


def confidence_to_level(confidence: float) -> ConfidenceLevel:
    """将 [0,1] 置信度按阈值映射为等级"""
    high_min, medium_min = CONFIDENCE_THRESHOLDS
    if confidence >= high_min:
        return ConfidenceLevel.HIGH
    if confidence >= medium_min:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


class ExpressionType(StrEnum):
    """业务规则可计算性类型：决定策略引擎能否代码求值"""

    NATURAL_LANGUAGE = "natural_language"  # 交给 LLM
    FORMULA = "formula"  # total = price * quantity
    COMPARISON = "comparison"  # age >= 18
    RANGE = "range"  # 18 <= age <= 60
    ENUM = "enum"  # status in {active, disabled}


class TestDimension(StrEnum):
    """测试维度（测试点归类）"""

    __test__ = False  # 非 pytest 测试类，禁止被收集

    FUNCTIONAL = "functional"
    BOUNDARY = "boundary"
    EQUIVALENCE = "equivalence"
    EXCEPTION = "exception"
    INTERACTION = "interaction"
    PERMISSION = "permission"
    COMPATIBILITY = "compatibility"
    PERFORMANCE = "performance"
    SECURITY = "security"
    STATE = "state"
    LINKAGE = "linkage"
    DATA_VALIDATION = "data_validation"


class Technique(StrEnum):
    """测试设计方法（策略引擎用）"""

    BOUNDARY_VALUE = "boundary_value"
    EQUIVALENCE_CLASS = "equivalence_class"
    DECISION_TABLE = "decision_table"
    STATE_TRANSITION = "state_transition"
    ERROR_GUESSING = "error_guessing"
    SCENARIO = "scenario"
    PERMISSION_MATRIX = "permission_matrix"


class ReviewDimension(StrEnum):
    """6 维评审维度"""

    COVERAGE = "coverage"
    ACCURACY = "accuracy"
    EXECUTABILITY = "executability"
    CONSISTENCY = "consistency"
    MISSING_RISK = "missing_risk"
    DUPLICATION = "duplication"


class Severity(StrEnum):
    """问题严重级"""

    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class TargetType(StrEnum):
    """多态目标类型（P1-5）：无 DB 外键，由 Domain Validator 校验"""

    TESTCASE = "testcase"
    TESTPOINT = "testpoint"
    OBLIGATION = "obligation"
    REQUIREMENT_ITEM = "requirement_item"


class ObligationStatus(StrEnum):
    """覆盖义务状态"""

    PENDING = "pending"
    COVERED = "covered"
    WAIVED = "waived"


class RunStatus(StrEnum):
    """生成会话状态"""

    INGESTING = "ingesting"
    PARSING = "parsing"
    STRATEGIZING = "strategizing"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class ReviewTriggerType(StrEnum):
    """评审触发类型（P0-4：评审是多轮过程）"""

    INITIAL = "initial"
    AFTER_OPTIMIZER = "after_optimizer"
    AFTER_HUMAN_EDIT = "after_human_edit"
    MANUAL = "manual"


class RequirementItemType(StrEnum):
    """原子需求项类型"""

    FUNCTION = "function"
    DATA_FIELD = "data_field"
    BUSINESS_RULE = "business_rule"
    CONSTRAINT = "constraint"
    INTERACTION = "interaction"
    PERMISSION = "permission"
    INTERFACE = "interface"
    NON_FUNCTIONAL = "non_functional"


class DataType(StrEnum):
    """字段数据类型（策略引擎据此算边界值/等价类）"""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    ID_CARD = "id_card"
