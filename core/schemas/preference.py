"""V2 偏好学习 Schema。设计见 docs/v2/step1-data-model.md §3.8。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.common import EntityBase, Ulid


class Preference(EntityBase):
    """用户偏好规则 - 从用户编辑中提炼，越用越贴合"""

    user_id: Ulid
    category: str
    pattern: str
    weight: float = Field(default=1.0, ge=0.0)
    active: bool = True
    source_diff: dict | None = None  # 未来由 TestCaseRevision 提供真实差异（🟡）

    model_config = ConfigDict(extra="forbid")


class PreferenceLink(BaseModel):
    """偏好规则与应用到的运行/用例的关联"""

    preference_id: Ulid
    run_id: Ulid | None = None
    test_case_id: Ulid | None = None

    model_config = ConfigDict(extra="forbid")
