"""
設定ファイル (config.yaml) を読み込み、型付きオブジェクトで提供する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen3:8b"
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    max_tokens: int = 4096
    temperature: float = 0.2


@dataclass
class SandboxConfig:
    docker_image: str = "python:3.12-slim"
    network_disabled: bool = True
    timeout_seconds: int = 300
    memory_limit: str = "512m"


@dataclass
class RepositoryConfig:
    allowed_paths: list[str] = field(
        default_factory=lambda: ["src/", "tests/", "README.md"]
    )
    denied_patterns: list[str] = field(
        default_factory=lambda: [
            "*.env", ".env*", "**/.git/**",
            "**/node_modules/**", "**/__pycache__/**",
            "*.key", "*.pem",
        ]
    )


@dataclass
class SessionConfig:
    storage_dir: str = "sessions/"
    max_history: int = 20


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    repository: RepositoryConfig = field(default_factory=RepositoryConfig)
    session: SessionConfig = field(default_factory=SessionConfig)

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        p = Path(path)
        if not p.exists():
            return cls()
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

        llm_raw = raw.get("llm", {})
        # 環境変数で api_key を上書き
        env_var = llm_raw.pop("api_key_env", "OPENAI_API_KEY")
        llm_raw.setdefault("api_key", os.environ.get(env_var, ""))

        return cls(
            llm=LLMConfig(**{k: v for k, v in llm_raw.items() if k in LLMConfig.__dataclass_fields__}),
            sandbox=SandboxConfig(**{k: v for k, v in raw.get("sandbox", {}).items() if k in SandboxConfig.__dataclass_fields__}),
            repository=RepositoryConfig(**{k: v for k, v in raw.get("repository", {}).items() if k in RepositoryConfig.__dataclass_fields__}),
            session=SessionConfig(**{k: v for k, v in raw.get("session", {}).items() if k in SessionConfig.__dataclass_fields__}),
        )
