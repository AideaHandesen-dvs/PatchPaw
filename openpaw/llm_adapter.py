"""
LLM Adapter
Ollama / OpenAI API と通信し、unified diff 文字列を返す。
LLM にはシステム操作権限を与えない（テキスト入出力のみ）。
"""

from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod

from .config import LLMConfig


class LLMError(Exception):
    pass


class BaseLLMAdapter(ABC):
    @abstractmethod
    def generate(self, messages: list[dict]) -> str:
        ...

    def _clean_diff(self, text: str) -> str:
        import re
        print(f"[RAW]\n{text}\n[/RAW]")  # ← 追加
        # markdownフェンスを除去
        text = re.sub(r"```(?:diff)?\n?", "", text)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()
        # hunkヘッダーの行数を再計算して修正
        return self._fix_hunk_headers(text)
    
    def _fix_hunk_headers(self, diff: str) -> str:
        import re
        lines = diff.splitlines(keepends=True)
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r'^(@@ -)(\d+)(?:,\d+)?( \+)(\d+)(?:,\d+)?( @@.*)\n?', line)
            if m:
                old_start = int(m.group(2))
                new_start = int(m.group(4))
                suffix = m.group(5)
                # hunkの中身を集める
                i += 1
                hunk = []
                while i < len(lines) and not lines[i].startswith('@@') \
                        and not lines[i].startswith('---') \
                        and not lines[i].startswith('+++'):
                    hunk.append(lines[i])
                    i += 1
                old_count = sum(1 for l in hunk if not l.startswith('+'))
                new_count = sum(1 for l in hunk if not l.startswith('-'))
                result.append(f'@@ -{old_start},{old_count} +{new_start},{new_count}{suffix}\n')
                result.extend(hunk)
            else:
                result.append(line)
                i += 1

        fixed = ''.join(result)
        for i, line in enumerate(fixed.splitlines(), 1):
            print(f"{i:3}: {repr(line)}")
        return fixed

        return ''.join(result)

class OllamaAdapter(BaseLLMAdapter):
    def __init__(self, config: LLMConfig):
        self.base_url = config.base_url.rstrip("/")
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature

    def generate(self, messages: list[dict]) -> str:
        # Ollama /api/chat エンドポイント
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
                return self._clean_diff(body["message"]["content"])
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
            raise LLMError("OPENAI_API_KEY が未設定です。")
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
                return self._clean_diff(body["choices"][0]["message"]["content"])
        except Exception as e:
            raise LLMError(f"OpenAI エラー: {e}") from e


def build_adapter(config: LLMConfig) -> BaseLLMAdapter:
    if config.provider == "openai":
        return OpenAIAdapter(config)
    return OllamaAdapter(config)
