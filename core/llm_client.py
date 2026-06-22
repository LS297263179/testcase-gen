"""LLM 调用客户端 - 支持 Anthropic 和 OpenAI 两种 API 格式，支持多模态和流式输出"""

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class UsageStats:
    """Token 用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0

# 项目根目录（此文件所在目录）
_PROJECT_ROOT = Path(__file__).parent.parent


def load_config(path: str | None = None) -> dict:
    """加载配置文件（默认使用项目根目录下的 config.yaml）"""
    from core.config import load_yaml_config
    if path is not None:
        # 自定义路径时直接读取（兼容 CLI 传参场景）
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return load_yaml_config()


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 api_type: str = "openai",
                 temperature: float = 0.3, max_tokens: int = 4096,
                 max_retries: int = 3,
                 enable_thinking: bool = False):
        self.api_type = api_type
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.enable_thinking = enable_thinking

        if api_type == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(base_url=base_url, api_key=api_key)
        else:
            from openai import OpenAI
            self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(self, system_prompt: str, user_prompt: str,
             images: list[dict] | None = None,
             max_tokens: int | None = None) -> str:
        """调用 LLM，带智能重试
        images: [{"data": "base64...", "media_type": "image/png"}]
        max_tokens: 可选，覆盖默认的 max_tokens
        """
        effective_max_tokens = max_tokens or self.max_tokens
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self._call(system_prompt, user_prompt, images, effective_max_tokens)
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # 图片不支持时，去掉图片重试
                if images and "image" in err_str:
                    logger.warning("模型不支持图片输入，已自动忽略图片，仅使用文本生成")
                    images = None
                    continue
                # 不可重试的错误：认证失败、请求格式错误
                if any(kw in err_str for kw in ("401", "unauthorized", "invalid_api_key", "authentication")):
                    raise
                if any(kw in err_str for kw in ("400", "bad_request", "invalid_request")):
                    raise
                # 可重试的错误：速率限制、服务端错误、网络超时
                if attempt < self.max_retries - 1:
                    # 速率限制等更久
                    if any(kw in err_str for kw in ("429", "rate_limit", "too_many_requests")):
                        wait = min(2 ** attempt * 5, 60)
                    else:
                        wait = 2 ** attempt
                    logger.warning(f"LLM 调用失败（第 {attempt + 1} 次），{wait}s 后重试: {e}")
                    time.sleep(wait)

        raise RuntimeError(f"LLM 调用失败（已重试 {self.max_retries} 次）: {last_error}")

    def chat_stream(self, system_prompt: str, user_prompt: str,
                    images: list[dict] | None = None,
                    max_tokens: int | None = None) -> Generator[str, None, None]:
        """流式调用 LLM，yield 每个文本 chunk。

        用法:
            for chunk in client.chat_stream(sys, user):
                print(chunk, end="", flush=True)
        """
        effective_max_tokens = max_tokens or self.max_tokens
        if self.api_type == "anthropic":
            yield from self._stream_anthropic(system_prompt, user_prompt, images, effective_max_tokens)
        else:
            yield from self._stream_openai(system_prompt, user_prompt, images, effective_max_tokens)

    def _stream_openai(self, system_prompt: str, user_prompt: str,
                       images: list[dict] | None, max_tokens: int) -> Generator[str, None, None]:
        """OpenAI 兼容接口的流式调用"""
        content = []
        if images:
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"},
                })
        content.append({"type": "text", "text": user_prompt})

        kwargs = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "stream": True,
        }
        if self.enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}

        for chunk in self.client.chat.completions.create(**kwargs):
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _stream_anthropic(self, system_prompt: str, user_prompt: str,
                          images: list[dict] | None, max_tokens: int) -> Generator[str, None, None]:
        """Anthropic 接口的流式调用"""
        content = []
        if images:
            for img in images:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]},
                })
        content.append({"type": "text", "text": user_prompt})

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        if self.enable_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        with self.client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    def _call(self, system_prompt: str, user_prompt: str,
              images: list[dict] | None = None,
              max_tokens: int = 4096) -> str:
        if self.api_type == "anthropic":
            return self._call_anthropic(system_prompt, user_prompt, images, max_tokens)
        else:
            return self._call_openai(system_prompt, user_prompt, images, max_tokens)

    def _call_anthropic(self, system_prompt: str, user_prompt: str,
                        images: list[dict] | None = None,
                        max_tokens: int = 4096) -> str:
        content = []
        if images:
            for img in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                })
        content.append({"type": "text", "text": user_prompt})

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        if self.enable_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        response = self.client.messages.create(**kwargs)
        # 检查是否因 max_tokens 截断
        if hasattr(response, 'stop_reason') and response.stop_reason == 'max_tokens':
            logger.warning(f"LLM 响应因 max_tokens={max_tokens} 被截断，内容可能不完整")
        # 思考模式下，跳过 thinking block，取 text block
        for block in response.content:
            if block.type == "text":
                return block.text
        return response.content[0].text

    def _call_openai(self, system_prompt: str, user_prompt: str,
                     images: list[dict] | None = None,
                     max_tokens: int = 4096) -> str:
        content = []
        if images:
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['media_type']};base64,{img['data']}",
                    },
                })
        content.append({"type": "text", "text": user_prompt})

        kwargs = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if self.enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        # thinking 模式下 content 可能为 None
        if msg.content is None:
            logger.warning("LLM 返回 content 为 None，可能 thinking 模式下未输出文本")
            return ""
        return msg.content


def build_client(cfg: dict) -> LLMClient:
    """根据配置字典创建 LLMClient 实例"""
    return LLMClient(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        api_type=cfg.get("api_type", "openai"),
        temperature=cfg.get("temperature", 0.3),
        max_tokens=cfg.get("max_tokens", 4096),
        max_retries=cfg.get("max_retries", 3),
        enable_thinking=cfg.get("enable_thinking", False),
    )
