"""V2 LLMClient 统一构建工厂（Step 10.2）。

Runtime / CLI / Web 通过本模块按用途构建 LLMClient，避免各处重复读取 config + 构造。
复用 core.config.get_model_config()（数据库优先、config.yaml 兜底、API Key 自动解密）
与 core.llm_client.build_client()（按配置字典构造 LLMClient）。

purpose:
  - "generate"：生成链路（Step 2 IR / Step 3 测试点 / Step 5 用例）用。
  - "review"：评审链路（Step 7）用；review.enabled 为假时回退到 generate 配置
    （第一版评审默认复用生成模型）。
"""

from __future__ import annotations

import logging

from core.config import get_model_config
from core.llm_client import LLMClient, build_client

logger = logging.getLogger("v2.client_factory")


def build_llm_client(purpose: str = "generate") -> LLMClient:
    """按用途构建 LLMClient。

    - purpose="generate"：用 config 的 generate 段。
    - purpose="review"：用 review 段；若 review.enabled 为假则回退 generate 段。
    """
    cfg = get_model_config() or {}
    section = cfg.get(purpose) or {}
    if purpose == "review" and not section.get("enabled", False):
        logger.info("review 未启用，评审客户端回退到 generate 配置")
        section = cfg.get("generate") or {}
    return build_client(section)
