"""V2 Requirement IR 构建编排 - Ingestion → Parser → Validator → 持久化。

对应蓝图 Requirement Parser → Requirement IR。支持：
  - 新需求：创建 RequirementDoc + Version 1
  - 需求变更：对已有 Doc 追加 Version N+1（Q4：可建 v1/v2，但**不**实现"变更对比→自动影响
    测试点/用例"的完整链路，那是 Step 6 追溯 + 变更影响分析）
严格遵循"LLM 抽取 → 代码校验 → 结构化持久化"，LLM 不单独控制流程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.schemas import (
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementVersion,
    SourceType,
)
from core.v2 import repository as repo
from core.v2.ingestion import ingest
from core.v2.parser import ParseResult, parse_requirement
from core.v2.validator import IRValidationResult, validate_ir

logger = logging.getLogger("v2.ir")


@dataclass
class IRBuildResult:
    """IR 构建结果：持久化后的 Doc/Version/Items + 解析与校验报告"""

    doc: RequirementDoc
    version: RequirementVersion
    items: list[RequirementItem] = field(default_factory=list)
    parse: ParseResult | None = None
    validation: IRValidationResult | None = None


def build_requirement_ir(
    client,
    *,
    user_id: str,
    title: str,
    raw_text: str,
    source_type: SourceType = SourceType.TEXT,
    images: list[dict] | None = None,
    doc: RequirementDoc | None = None,
    version_no: int | None = None,
    provenance: Provenance = Provenance.LLM,
) -> IRBuildResult:
    """构建（或追加版本到）Requirement IR 并持久化。

    - doc=None：新建 RequirementDoc + Version 1（title/source_type 生效）。
    - doc=已有：追加新版本，version_no 缺省 = 现有版本数 + 1（title 忽略，沿用原 doc）。
    流程：建 Doc/Version → LLM 抽取(超长自动分段) → 代码校验(去重/结构/置信度) → 持久化 Items
         → 更新 doc.latest_version_id。
    """
    if doc is None:
        doc = RequirementDoc(user_id=user_id, title=title, source_type=source_type)
        repo.save_doc(doc)
        next_version = 1
    else:
        next_version = len(repo.list_versions(doc.id)) + 1
    if version_no is None:
        version_no = next_version

    version = RequirementVersion(doc_id=doc.id, version_no=version_no, raw_text=raw_text, provenance=provenance)
    repo.save_version(version)

    parsed = parse_requirement(client, raw_text, version.id, images)
    # 解析失败原因此前只活在 parsed.issues 里、从不落日志：items=0 却查不到任何根因
    if parsed.issues:
        logger.warning("IR 解析问题: %s", parsed.issues)
    validation = validate_ir(parsed.items)

    for item in validation.items:
        repo.save_item(item)

    doc.latest_version_id = version.id
    repo.save_doc(doc)

    logger.info(
        "IR 构建完成: doc=%s version=%d items=%d (去重 %d, 低置信 %d, 分段 %d)",
        doc.id,
        version.version_no,
        len(validation.items),
        validation.duplicates_removed,
        len(validation.low_confidence_ids),
        parsed.segments,
    )
    return IRBuildResult(doc=doc, version=version, items=validation.items, parse=parsed, validation=validation)


def ingest_and_build_ir(
    client,
    *,
    user_id: str,
    title: str,
    text: str | None = None,
    paths: list[str] | None = None,
    source_type: SourceType = SourceType.TEXT,
    doc: RequirementDoc | None = None,
    version_no: int | None = None,
    provenance: Provenance = Provenance.LLM,
) -> IRBuildResult:
    """蓝图顶层入口：文本/文件/图片 → Ingestion 归一化 → Parser → Validator → 持久化 IR。

    一步完成 Ingestion Layer → Requirement IR 全链（Step 3+ 的上游入口）。
    """
    raw_text, images = ingest(text=text, paths=paths)
    return build_requirement_ir(
        client,
        user_id=user_id,
        title=title,
        raw_text=raw_text,
        source_type=source_type,
        images=images or None,
        doc=doc,
        version_no=version_no,
        provenance=provenance,
    )
