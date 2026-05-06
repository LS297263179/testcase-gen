"""LLM 调用客户端 - 支持 Anthropic 和 OpenAI 两种 API 格式，支持多模态"""

import time

import yaml


def load_config(path: str = "config.yaml") -> dict:
    """加载配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 api_type: str = "anthropic",
                 temperature: float = 0.3, max_tokens: int = 4096,
                 max_retries: int = 3):
        self.api_type = api_type
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries

        if api_type == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(base_url=base_url, api_key=api_key)
        else:
            from openai import OpenAI
            self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(self, system_prompt: str, user_prompt: str,
             images: list[dict] | None = None) -> str:
        """调用 LLM，带指数退避重试
        images: [{"data": "base64...", "media_type": "image/png"}]
        """
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self._call(system_prompt, user_prompt, images)
            except Exception as e:
                last_error = e
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

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
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

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content
