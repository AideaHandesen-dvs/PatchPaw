"""
LLM Adapter
Ollama / OpenAI API と通信し、LLMの生出力テキストを返す。
LLMにはシステム操作権限を与えない（テキスト入出力のみ）。
"""

from __future__ import annotations

import json
import re
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .config import LLMConfig


class LLMError(Exception):
    pass


@dataclass
class GenerateResult:
    """LLM 生成結果。テキスト出力に加え、可能ならトークン使用量を含む。

    プロバイダ側が usage を返さない場合や、Ollama のキャッシュヒット等で
    一部が欠ける場合があるため、各フィールドは None になりうる。
    """
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class BaseLLMAdapter(ABC):
    @abstractmethod
    def generate(self, messages: list[dict]) -> GenerateResult:
        ...

    def _clean_output(self, text: str) -> str:
        """markdownコードフェンスのみ除去して返す。行番号操作は行わない。"""
        text = re.sub(r"```[\w]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        return text.strip()


class OllamaAdapter(BaseLLMAdapter):
    def __init__(self, config: LLMConfig):
        self.base_url = config.base_url.rstrip("/")
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    def generate(self, messages: list[dict]) -> GenerateResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read())
                text = self._clean_output(body["message"]["content"])
                # Ollama は prompt_eval_count / eval_count を返す。
                # キャッシュヒットや古いバージョンでは欠ける場合があるので None 安全。
                prompt_tokens = body.get("prompt_eval_count")
                completion_tokens = body.get("eval_count")
                total_tokens: int | None = None
                if prompt_tokens is not None and completion_tokens is not None:
                    total_tokens = prompt_tokens + completion_tokens
                return GenerateResult(
                    text=text,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
        except Exception as e:
            raise LLMError(f"Ollama エラー: {e}") from e


class OpenAIAdapter(BaseLLMAdapter):
    def __init__(self, config: LLMConfig):
        self.base_url = (config.base_url or "https://api.openai.com").rstrip("/")
        self.model = config.model
        self.api_key = config.api_key
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    def generate(self, messages: list[dict]) -> GenerateResult:
        if not self.api_key:
            raise LLMError("API キーが未設定です。")
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read())
                text = self._clean_output(body["choices"][0]["message"]["content"])
                # OpenAI 互換 API は usage オブジェクトを返す (DeepSeek V4 Flash も)。
                # ただし互換実装によっては欠ける可能性があるので None 安全。
                usage = body.get("usage") or {}
                return GenerateResult(
                    text=text,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
        except Exception as e:
            raise LLMError(f"LLM エラー: {e}") from e


def build_adapter(config: LLMConfig) -> BaseLLMAdapter:
    if config.provider == "openai":
        return OpenAIAdapter(config)
    return OllamaAdapter(config)
