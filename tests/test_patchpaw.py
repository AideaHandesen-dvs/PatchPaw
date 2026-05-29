"""
PatchPaw — ユニットテスト
"""

import textwrap
import pytest
from pathlib import Path
import tempfile
import os


# ────────────────────────────────────────────
# Config
# ────────────────────────────────────────────
class TestConfig:
    def test_defaults(self):
        from patchpaw.config import Config
        c = Config()
        assert c.llm.provider == "ollama"
        assert c.sandbox.network_disabled is True
        assert "src/" in c.repository.allowed_paths

    def test_load_yaml(self, tmp_path):
        from patchpaw.config import Config
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "llm:\n  provider: openai\n  model: gpt-4o\n",
            encoding="utf-8",
        )
        c = Config.load(cfg)
        assert c.llm.provider == "openai"
        assert c.llm.model == "gpt-4o"

    def test_missing_file_returns_defaults(self, tmp_path):
        from patchpaw.config import Config
        c = Config.load(tmp_path / "nonexistent.yaml")
        assert c.llm.provider == "ollama"


# ────────────────────────────────────────────
# Repository Reader
# ────────────────────────────────────────────
class TestRepositoryReader:
    @pytest.fixture()
    def repo(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_ok(): pass")
        (tmp_path / ".env").write_text("SECRET=abc")
        (tmp_path / "README.md").write_text("# readme")
        return tmp_path

    def _reader(self, repo):
        from patchpaw.config import Config, RepositoryConfig
        from patchpaw.repository_reader import RepositoryReader
        c = Config()
        c.repository = RepositoryConfig(
            allowed_paths=["src/", "tests/", "README.md"],
            denied_patterns=["*.env", ".env*"],
        )
        return RepositoryReader(repo, c)

    def test_read_allowed_file(self, repo):
        r = self._reader(repo)
        content = r.read_file("src/main.py")
        assert "hello" in content

    def test_denied_env_file(self, repo):
        from patchpaw.repository_reader import SecurityError
        r = self._reader(repo)
        with pytest.raises(SecurityError):
            r.read_file(".env")

    def test_path_traversal_blocked(self, repo):
        from patchpaw.repository_reader import SecurityError
        r = self._reader(repo)
        with pytest.raises(SecurityError):
            r.read_file("../../../etc/passwd")

    def test_collect_files(self, repo):
        r = self._reader(repo)
        files = r.collect_files()
        assert "src/main.py" in files
        assert ".env" not in files

    def test_list_allowed(self, repo):
        r = self._reader(repo)
        lst = r.list_allowed()
        assert any("main.py" in f for f in lst)


# ────────────────────────────────────────────
# Diff Validator
# ────────────────────────────────────────────
VALID_DIFF = textwrap.dedent("""\
    FILE: src/main.py
    <<<<<<< SEARCH
    def foo():
        return 1
    =======
    def foo():
        return 2
    >>>>>>> REPLACE
""")

DANGEROUS_DIFF = textwrap.dedent("""\
    FILE: src/main.py
    <<<<<<< SEARCH
    x = 1
    =======
    x = 1
    import subprocess
    >>>>>>> REPLACE
""")

OUTSIDE_DIFF = textwrap.dedent("""\
    FILE: secret.key
    <<<<<<< SEARCH
    old
    =======
    new
    >>>>>>> REPLACE
""")


class TestDiffValidator:
    @pytest.fixture()
    def validator(self):
        from patchpaw.diff_validator import DiffValidator
        return DiffValidator(allowed_paths=["src/", "tests/", "README.md"])

    def test_valid_diff(self, validator):
        r = validator.validate(VALID_DIFF)
        assert r.ok
        assert "src/main.py" in r.affected_files

    def test_empty_diff(self, validator):
        r = validator.validate("")
        assert not r.ok

    def test_dangerous_diff(self, validator):
        r = validator.validate(DANGEROUS_DIFF)
        assert not r.ok
        assert any("危険" in e for e in r.errors)

    def test_outside_diff(self, validator):
        r = validator.validate(OUTSIDE_DIFF)
        assert not r.ok
        assert any("ホワイトリスト外" in e for e in r.errors)


# ────────────────────────────────────────────
# Prompt Builder
# ────────────────────────────────────────────
class TestPromptBuilder:
    def test_basic(self):
        from patchpaw.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs = pb.build(
            instruction="バグを直して",
            file_contents={"src/main.py": "x = 1\n"},
        )
        assert msgs[0]["role"] == "system"
        assert "search/replace" in msgs[0]["content"].lower()
        assert "バグを直して" in msgs[1]["content"]
        assert "src/main.py" in msgs[1]["content"]

    def test_retry_includes_previous_diff(self):
        from patchpaw.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs = pb.build(
            instruction="fix",
            file_contents={"src/a.py": "pass"},
            test_result="FAILED",
            previous_output=VALID_DIFF,
            iteration=2,
        )
        combined = msgs[1]["content"]
        assert "Previous Output" in combined
        assert "FAILED" in combined

    def test_previous_task_changes_injected(self):
        """previous_task_changes が渡されたら user_parts にセクションが入る (v2.2)。"""
        from patchpaw.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs = pb.build(
            instruction="次のタスク",
            file_contents={"src/a.py": "pass"},
            previous_task_changes=["src/foo.py", "tests/test_foo.py"],
        )
        combined = msgs[1]["content"]
        assert "Previous Task's Changes" in combined
        assert "src/foo.py" in combined
        assert "tests/test_foo.py" in combined

    def test_previous_task_changes_none_omitted(self):
        """previous_task_changes=None なら該当セクションは出ない。"""
        from patchpaw.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs = pb.build(
            instruction="foo",
            file_contents={"src/a.py": "pass"},
            previous_task_changes=None,
        )
        assert "Previous Task's Changes" not in msgs[1]["content"]

    def test_previous_task_changes_empty_list_omitted(self):
        """previous_task_changes=[] でも該当セクションは出ない。"""
        from patchpaw.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs = pb.build(
            instruction="foo",
            file_contents={"src/a.py": "pass"},
            previous_task_changes=[],
        )
        assert "Previous Task's Changes" not in msgs[1]["content"]


# ────────────────────────────────────────────
# Session Manager
# ────────────────────────────────────────────
class TestSessionManager:
    def test_record_and_read(self, tmp_path):
        from patchpaw.config import SessionConfig
        from patchpaw.session_manager import SessionEntry, SessionManager
        sm = SessionManager(tmp_path, SessionConfig(storage_dir="sessions/"))
        e = SessionEntry(instruction="test", diff=VALID_DIFF, test_success=True)
        sm.record(e)
        assert sm.last_entry().instruction == "test"

    def test_save_diff(self, tmp_path):
        from patchpaw.config import SessionConfig
        from patchpaw.session_manager import SessionManager
        sm = SessionManager(tmp_path, SessionConfig(storage_dir="sessions/"))
        p = sm.save_diff(VALID_DIFF, label="iter1")
        assert p.exists()
        assert VALID_DIFF in p.read_text()


# ────────────────────────────────────────────
# LLM Adapter — token usage extraction (v2.3.x)
# ────────────────────────────────────────────

class _FakeResp:
    """urllib.request.urlopen の戻り値を模す最小 context manager。"""
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


class TestLLMAdapterUsage:
    """OpenAI/Ollama アダプタが usage を GenerateResult に詰めることを検証。"""

    def test_openai_extracts_usage(self, monkeypatch):
        import json
        from patchpaw.config import LLMConfig
        from patchpaw.llm_adapter import OpenAIAdapter, GenerateResult

        body = json.dumps({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 34,
                "total_tokens": 46,
            },
        }).encode()
        monkeypatch.setattr(
            "patchpaw.llm_adapter.urllib.request.urlopen",
            lambda req, timeout=600: _FakeResp(body),
        )
        cfg = LLMConfig(provider="openai", model="x", api_key="dummy")
        adapter = OpenAIAdapter(cfg)
        r = adapter.generate([{"role": "user", "content": "hi"}])
        assert isinstance(r, GenerateResult)
        assert r.text == "hello"
        assert r.prompt_tokens == 12
        assert r.completion_tokens == 34
        assert r.total_tokens == 46

    def test_openai_missing_usage_returns_none(self, monkeypatch):
        """usage フィールドが欠けてる互換実装でも壊れず None で返ること。"""
        import json
        from patchpaw.config import LLMConfig
        from patchpaw.llm_adapter import OpenAIAdapter

        body = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
        }).encode()
        monkeypatch.setattr(
            "patchpaw.llm_adapter.urllib.request.urlopen",
            lambda req, timeout=600: _FakeResp(body),
        )
        cfg = LLMConfig(provider="openai", model="x", api_key="dummy")
        adapter = OpenAIAdapter(cfg)
        r = adapter.generate([{"role": "user", "content": "hi"}])
        assert r.text == "ok"
        assert r.prompt_tokens is None
        assert r.completion_tokens is None
        assert r.total_tokens is None

    def test_ollama_extracts_counts(self, monkeypatch):
        import json
        from patchpaw.config import LLMConfig
        from patchpaw.llm_adapter import OllamaAdapter

        body = json.dumps({
            "message": {"content": "hello"},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }).encode()
        monkeypatch.setattr(
            "patchpaw.llm_adapter.urllib.request.urlopen",
            lambda req, timeout=600: _FakeResp(body),
        )
        cfg = LLMConfig(provider="ollama", model="x")
        adapter = OllamaAdapter(cfg)
        r = adapter.generate([{"role": "user", "content": "hi"}])
        assert r.text == "hello"
        assert r.prompt_tokens == 10
        assert r.completion_tokens == 5
        # total は prompt + completion の合算
        assert r.total_tokens == 15

    def test_ollama_partial_counts_total_none(self, monkeypatch):
        """prompt_eval_count がキャッシュヒットで欠けた場合、total も None。"""
        import json
        from patchpaw.config import LLMConfig
        from patchpaw.llm_adapter import OllamaAdapter

        body = json.dumps({
            "message": {"content": "ok"},
            "eval_count": 5,
            # prompt_eval_count なし
        }).encode()
        monkeypatch.setattr(
            "patchpaw.llm_adapter.urllib.request.urlopen",
            lambda req, timeout=600: _FakeResp(body),
        )
        cfg = LLMConfig(provider="ollama", model="x")
        adapter = OllamaAdapter(cfg)
        r = adapter.generate([{"role": "user", "content": "hi"}])
        assert r.text == "ok"
        assert r.prompt_tokens is None
        assert r.completion_tokens == 5
        assert r.total_tokens is None
