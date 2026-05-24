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

from .config import LLMConfig


class LLMError(Exception):
    pass


class BaseLLMAdapter(ABC):
    @abstractmethod
    def generate(self, messages: list[dict]) -> str:
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

    def generate(self, messages: list[dict]) -> str:
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
                return self._clean_output(body["message"]["content"])
        except Exception as e:
            raise LLMError(f"Ollama エラー: {e}") from e


class OpenAIAdapter(BaseLLMAdapter):
    def __init__(self, config: LLMConfig):
        self.base_url = (config.base_url or "https://api.openai.com").rstrip("/")
        self.model = config.model
        self.api_key = config.api_key
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    def generate(self, messages: list[dict]) -> str:
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
                return self._clean_output(body["choices"][0]["message"]["content"])
        except Exception as e:
            raise LLMError(f"LLM エラー: {e}") from e


def build_adapter(config: LLMConfig) -> BaseLLMAdapter:
    if config.provider == "openai":
        return OpenAIAdapter(config)
    return OllamaAdapter(config)
