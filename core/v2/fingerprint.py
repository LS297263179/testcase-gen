"""V2 业务确定性指纹 - TestPoint 幂等身份计算。

Step 3 引入：TestPoint 的 ULID 是每次新生成的，无法用于跨轮次的幂等识别。
业务身份由 (version_id, generation_scope, sorted(item_ids), module, subcategory, normalize(title))
六元组唯一确定 → sha256 得到 fingerprint，DB 层加 UNIQUE 索引，Repository 层按 fingerprint upsert。

不放在 Schema 层的原因：Schema 保持纯声明，指纹算法属于持久化/领域逻辑。
"""

from __future__ import annotations

import hashlib
import re

# 归一化时压缩的空白字符（含全角空格）
_WS_RE = re.compile(r"[\s\u3000]+")


def normalize_text(text: str | None) -> str:
    """文本归一化：strip + 内部空白压缩为单空格 + 小写化。

    用于 fingerprint 计算与去重键，避免因大小写/空白差异导致同一业务身份产生多个指纹。
    不做标点归一（不同标点在测试点标题里可能表达不同语义，保守处理）。
    """
    if not text:
        return ""
    return _WS_RE.sub(" ", text.strip()).lower()


def compute_testpoint_fingerprint(
    *,
    version_id: str | None,
    generation_scope: str,
    item_ids: list[str],
    module: str,
    subcategory: str,
    title: str,
) -> str:
    """计算 TestPoint 的业务确定性指纹（32 位十六进制，前缀 tp_）。

    六元组构成业务身份：
      - version_id：不同 RequirementVersion 的 TestPoint 天然隔离
      - generation_scope：Phase A（item）与 Phase B（cross_item）分开算，避免同一 item 下
        Phase A 单点与 Phase B 跨点因 title 巧合而碰撞
      - sorted(item_ids)：M:N 关联集合，排序后拼接保证顺序无关
      - module / subcategory / normalize(title)：内容三元组

    返回格式：tp_<sha256 前 32 位十六进制>，共 35 字符。
    """
    payload = "|".join(
        [
            version_id or "",
            generation_scope or "",
            ",".join(sorted(item_ids or [])),
            normalize_text(module),
            normalize_text(subcategory),
            normalize_text(title),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tp_{digest}"
