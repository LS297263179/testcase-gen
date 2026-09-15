"""V2 运行会话 + 生成审计 Schema。

设计见 docs/v2/step1-data-model.md §3.7。
P0-2：Run 绑定具体 requirement_version_id。
P0-3：GenerationConfig 记录模型/Prompt/生成器/评审器版本（可复现、可评测的前提）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.common import EntityBase, RunStatus, Ulid, new_ulid


class GenerationConfig(BaseModel):
    """AI 生成审计配置（P0-3）- 回答"这条用例当时用哪个模型/Prompt 生成" """

    id: Ulid = Field(default_factory=new_ulid)
    model_provider: str  # openai / anthropic / …
    model_name: str
    temperature: float
    max_tokens: int | None = None
    enable_thinking: bool | None = None
    prompt_version: str  # 如 "case-generator-v3"
    generator_version: str  # 如 "2.1.0"
    reviewer_version: str | None = None  # 如 "1.4.0"

    model_config = ConfigDict(extra="forbid")


class RunCounts(BaseModel):
    """一次 Run 的产物计数（冗余统计，便于列表展示）"""

    items: int = 0
    points: int = 0
    cases: int = 0
    obligations: int = 0

    model_config = ConfigDict(extra="forbid")


class Run(EntityBase):
    """一次完整的生成会话（取代 V1 sessions）"""

    user_id: Ulid
    doc_id: Ulid
    requirement_version_id: Ulid  # P0-2：明确基于哪个需求版本生成
    generation_config_id: Ulid  # P0-3：1:1 关联 GenerationConfig
    strategy_profile: str | None = None
    status: RunStatus = RunStatus.INGESTING
    counts: RunCounts = Field(default_factory=RunCounts)
    legacy_session_id: int | None = None  # 迁移自 V1 时记录原 session.id

    model_config = ConfigDict(extra="forbid")
