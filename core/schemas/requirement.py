"""V2 需求层 Schema - RequirementDoc → RequirementVersion → RequirementItem（IR）。

设计见 docs/v2/step1-data-model.md §3.2。
核心：需求版本化（P0-1）+ 字段级 IR（FieldSpec 驱动策略引擎）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas.common import (
    ConfidenceLevel,
    DataType,
    EntityBase,
    EntityStatus,
    ExpressionType,
    Priority,
    Provenance,
    RequirementItemType,
    SourceType,
    Ulid,
    confidence_to_level,
)


class SourceRef(BaseModel):
    """需求项在原文/图片中的定位，用于追溯到 raw_text"""

    locator: str  # paragraph / line / bbox
    value: str
    asset_id: Ulid | None = None

    model_config = ConfigDict(extra="forbid")


class FieldSpec(BaseModel):
    """字段规格 - 策略引擎做边界值/等价类的确定性输入。

    required 与 nullable 语义不同：
      required=True, nullable=False → 必填且不可为 null（如年龄）
      required=True, nullable=True  → 字段必须出现但值可为 null（如备注）
    """

    name: str
    label: str
    data_type: DataType
    required: bool = False
    nullable: bool = False
    min_length: int | None = None
    max_length: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None
    enum_values: list[str] = Field(default_factory=list)
    precision: int | None = None
    unique: bool = False
    default: str | None = None
    unit: str | None = None
    format: str | None = None  # 增强项：如 "email" / "yyyy-MM-dd"
    example: str | None = None  # 增强项

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_ranges(self) -> FieldSpec:
        """约束一致性校验（代码化，不靠 LLM）"""
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise ValueError(f"字段 {self.name}: min_length({self.min_length}) > max_length({self.max_length})")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"字段 {self.name}: min_value({self.min_value}) > max_value({self.max_value})")
        return self


class BusinessRule(BaseModel):
    """业务规则 - expression_type 决定策略引擎能否代码求值"""

    name: str
    expression_type: ExpressionType = ExpressionType.NATURAL_LANGUAGE
    expression: str
    inputs: list[str] = Field(default_factory=list)
    expected: str | None = None

    model_config = ConfigDict(extra="forbid")

    @property
    def is_computable(self) -> bool:
        """是否可由代码求值（非自然语言即可尝试）"""
        return self.expression_type != ExpressionType.NATURAL_LANGUAGE


class PermissionRule(BaseModel):
    """权限矩阵原子：角色 × 资源 × 操作 → 允许/拒绝"""

    role: str
    resource: str
    action: str
    allowed: bool
    condition: str | None = None

    model_config = ConfigDict(extra="forbid")


class RequirementDoc(EntityBase):
    """需求身份（稳定，跨版本不变）"""

    user_id: Ulid
    title: str
    source_type: SourceType
    asset_ids: list[Ulid] = Field(default_factory=list)
    material_ids: list[Ulid] = Field(default_factory=list)
    latest_version_id: Ulid | None = None  # 冗余指针，快速取当前版本
    status: EntityStatus = EntityStatus.DRAFT


class RequirementVersion(EntityBase):
    """需求版本快照（P0-1）- 变更影响分析的基石。

    需求变更 = 新增一个 Version（v2），而非修改 v1；
    v1 下的历史测试资产仍指向 v1，依据不丢失。
    """

    doc_id: Ulid
    version_no: int = Field(ge=1)
    raw_text: str | None = None
    change_summary: str | None = None
    source_ref: SourceRef | None = None
    provenance: Provenance = Provenance.HUMAN
    status: EntityStatus = EntityStatus.DRAFT

    model_config = ConfigDict(extra="forbid")


class RequirementItem(EntityBase):
    """原子需求项（IR 核心）- 隶属某个 Version，携带字段/规则/权限供策略引擎消费"""

    version_id: Ulid
    seq: int = Field(ge=1)
    type: RequirementItemType
    module: str
    statement: str
    fields: list[FieldSpec] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    permissions: list[PermissionRule] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_ref: SourceRef | None = None
    priority_hint: Priority | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH
    provenance: Provenance = Provenance.LLM
    status: EntityStatus = EntityStatus.DRAFT

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _derive_confidence_level(cls, data: object) -> object:
        """未显式提供 confidence_level 时，按 confidence 数值自动映射为等级"""
        if isinstance(data, dict) and "confidence_level" not in data and "confidence" in data:
            data = dict(data)
            data["confidence_level"] = confidence_to_level(float(data["confidence"])).value
        return data
