"""LLM 调用客户端 - 支持 Anthropic 和 OpenAI 两种 API 格式，支持多模态"""

import time
from pathlib import Path

import yaml

# 项目根目录（此文件所在目录）
_PROJECT_ROOT = Path(__file__).parent


def load_config(path: str | None = None) -> dict:
    """加载配置文件（默认使用项目根目录下的 config.yaml）"""
    if path is None:
        path = str(_PROJECT_ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 api_type: str = "anthropic",
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
        """调用 LLM，带指数退避重试
        images: [{"data": "base64...", "media_type": "image/png"}]
        max_tokens: 可选，覆盖默认的 max_tokens
        """
        self._current_max_tokens = max_tokens or self.max_tokens
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self._call(system_prompt, user_prompt, images)
            except Exception as e:
                last_error = e
                # 图片不支持时，去掉图片重试
                if images and "image" in str(e).lower():
                    print("[WARN] 模型不支持图片输入，已自动忽略图片，仅使用文本生成")
                    images = None
                    continue
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    time.sleep(wait)

        raise RuntimeError(f"LLM 调用失败（已重试 {self.max_retries} 次）: {last_error}")

    def _call(self, system_prompt: str, user_prompt: str,
              images: list[dict] | None = None) -> str:
        if self.api_type == "anthropic":
            return self._call_anthropic(system_prompt, user_prompt, images)
        else:
            return self._call_openai(system_prompt, user_prompt, images)

    def _call_anthropic(self, system_prompt: str, user_prompt: str,
                        images: list[dict] | None = None) -> str:
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
            "max_tokens": self._current_max_tokens,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        if self.enable_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        response = self.client.messages.create(**kwargs)
        # 思考模式下，跳过 thinking block，取 text block
        for block in response.content:
            if block.type == "text":
                return block.text
        return response.content[0].text

    def _call_openai(self, system_prompt: str, user_prompt: str,
                     images: list[dict] | None = None) -> str:
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
            "max_tokens": self._current_max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if self.enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        # 思考模式下，thinking 内容可能在 reasoning_content 字段
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
