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


# ────────────────────────────────────────────
# Patch Applier — SEARCH/REPLACE と SEARCH_ALL/REPLACE_ALL
# (P2: sed 風一括置換ブロック)
# ────────────────────────────────────────────

class TestParseBlocks:
    """parse_blocks のモード判別と並び順を検証。"""

    def test_unique_block_parsed(self):
        from patchpaw.patch_applier import parse_blocks
        blocks = parse_blocks(VALID_DIFF)
        assert len(blocks) == 1
        assert blocks[0].mode == "unique"
        assert blocks[0].file_path == "src/main.py"

    def test_search_all_block_parsed(self):
        from patchpaw.patch_applier import parse_blocks
        text = textwrap.dedent("""\
            FILE: src/main.py
            <<<<<<< SEARCH_ALL
            old_name
            =======
            new_name
            >>>>>>> REPLACE_ALL
        """)
        blocks = parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].mode == "all"
        # SEARCH_ALL は末尾改行を trim する (リテラル部分文字列扱い)
        assert blocks[0].search == "old_name"
        assert blocks[0].replace == "new_name"

    def test_search_all_multiline_block_keeps_inner_newlines(self):
        """SEARCH_ALL の複数行ブロックは内部の改行を保ち、末尾改行のみ trim。"""
        from patchpaw.patch_applier import parse_blocks
        text = textwrap.dedent("""\
            FILE: src/main.py
            <<<<<<< SEARCH_ALL
            print(x)
            print(y)
            =======
            log(x)
            log(y)
            >>>>>>> REPLACE_ALL
        """)
        blocks = parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].search == "print(x)\nprint(y)"
        assert blocks[0].replace == "log(x)\nlog(y)"

    def test_mixed_blocks_parsed_in_order(self):
        """SEARCH と SEARCH_ALL を混在させた場合、出現順に並ぶ。"""
        from patchpaw.patch_applier import parse_blocks
        text = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH
            x = 1
            =======
            x = 2
            >>>>>>> REPLACE

            FILE: b.py
            <<<<<<< SEARCH_ALL
            old
            =======
            new
            >>>>>>> REPLACE_ALL

            FILE: c.py
            <<<<<<< SEARCH
            y = 1
            =======
            y = 2
            >>>>>>> REPLACE
        """)
        blocks = parse_blocks(text)
        assert [b.file_path for b in blocks] == ["a.py", "b.py", "c.py"]
        assert [b.mode for b in blocks] == ["unique", "all", "unique"]

    def test_search_does_not_match_search_all(self):
        """BLOCK_UNIQUE_RE が SEARCH_ALL ブロックを誤って拾わない。"""
        from patchpaw.patch_applier import parse_blocks
        text = textwrap.dedent("""\
            FILE: x.py
            <<<<<<< SEARCH_ALL
            foo
            =======
            bar
            >>>>>>> REPLACE_ALL
        """)
        blocks = parse_blocks(text)
        # SEARCH_ALL ブロック 1 個だけが拾われる (SEARCH モードの誤検出なし)
        assert len(blocks) == 1
        assert blocks[0].mode == "all"

    def test_default_mode_is_unique(self):
        """EditBlock のデフォルト mode が 'unique' で、既存コードを壊さない。"""
        from patchpaw.patch_applier import EditBlock
        b = EditBlock(file_path="x.py", search="a", replace="b")
        assert b.mode == "unique"


class TestPatchApplierUnique:
    """SEARCH/REPLACE モードの回帰テスト (P2 で壊してないことを保証)。"""

    def test_apply_unique_success(self, tmp_path):
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH
            x = 1
            =======
            x = 99
            >>>>>>> REPLACE
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert ok, msg
        assert (tmp_path / "a.py").read_text() == "x = 99\ny = 2\n"

    def test_apply_unique_ambiguous_rolls_back(self, tmp_path):
        """SEARCH が複数箇所に一致したら apply は失敗してロールバック。"""
        from patchpaw.patch_applier import PatchApplier
        original = "x = 1\nx = 1\n"
        (tmp_path / "a.py").write_text(original)
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH
            x = 1
            =======
            x = 2
            >>>>>>> REPLACE
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert not ok
        assert (tmp_path / "a.py").read_text() == original

    def test_create_new_file_via_empty_search(self, tmp_path):
        from patchpaw.patch_applier import PatchApplier
        diff = textwrap.dedent("""\
            FILE: new.py
            <<<<<<< SEARCH
            =======
            print("hello")
            >>>>>>> REPLACE
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert ok, msg
        assert (tmp_path / "new.py").read_text() == 'print("hello")\n'


class TestPatchApplierSearchAll:
    """SEARCH_ALL/REPLACE_ALL モードのテスト。"""

    def test_apply_replaces_all_occurrences(self, tmp_path):
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text(
            "old_name = 1\nuse(old_name)\nreturn old_name\n"
        )
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            old_name
            =======
            new_name
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert ok, msg
        assert (tmp_path / "a.py").read_text() == (
            "new_name = 1\nuse(new_name)\nreturn new_name\n"
        )

    def test_apply_single_match_succeeds(self, tmp_path):
        """SEARCH_ALL でも 1 箇所だけ一致は成功 (論点 4)。"""
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text("only_once = 1\n")
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            only_once
            =======
            renamed
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert ok, msg
        assert (tmp_path / "a.py").read_text() == "renamed = 1\n"

    def test_apply_zero_match_rolls_back(self, tmp_path):
        """SEARCH_ALL で 0 箇所一致はエラー、ファイル不変。"""
        from patchpaw.patch_applier import PatchApplier
        original = "x = 1\n"
        (tmp_path / "a.py").write_text(original)
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            nonexistent
            =======
            anything
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert not ok
        assert (tmp_path / "a.py").read_text() == original

    def test_apply_empty_search_all_errors(self, tmp_path):
        """SEARCH_ALL の中身が空はエラー (新規作成は SEARCH を使え)。"""
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text("x = 1\n")
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            =======
            print("hi")
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert not ok
        assert "空" in msg or "empty" in msg.lower() or "SEARCH_ALL" in msg

    def test_dry_run_zero_match_reports_error(self, tmp_path):
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text("x = 1\n")
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            nonexistent
            =======
            anything
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).dry_run(diff)
        assert not ok
        assert "SEARCH_ALL" in msg


class TestPatchApplierMixed:
    """SEARCH/REPLACE と SEARCH_ALL/REPLACE_ALL の混在 (論点 8)。"""

    def test_mixed_apply_success(self, tmp_path):
        from patchpaw.patch_applier import PatchApplier
        (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
        (tmp_path / "b.py").write_text("foo()\nfoo()\nfoo()\n")
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH
            x = 1
            =======
            x = 99
            >>>>>>> REPLACE

            FILE: b.py
            <<<<<<< SEARCH_ALL
            foo
            =======
            bar
            >>>>>>> REPLACE_ALL
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert ok, msg
        assert (tmp_path / "a.py").read_text() == "x = 99\ny = 2\n"
        assert (tmp_path / "b.py").read_text() == "bar()\nbar()\nbar()\n"

    def test_mixed_rollback_when_second_block_fails(self, tmp_path):
        """先に SEARCH_ALL で複数箇所変えた後、後続 SEARCH が失敗 → 全部ロールバック。"""
        from patchpaw.patch_applier import PatchApplier
        a_original = "foo()\nfoo()\nfoo()\n"
        b_original = "x = 1\n"
        (tmp_path / "a.py").write_text(a_original)
        (tmp_path / "b.py").write_text(b_original)
        diff = textwrap.dedent("""\
            FILE: a.py
            <<<<<<< SEARCH_ALL
            foo
            =======
            bar
            >>>>>>> REPLACE_ALL

            FILE: b.py
            <<<<<<< SEARCH
            nonexistent_line
            =======
            replacement
            >>>>>>> REPLACE
        """)
        ok, msg = PatchApplier(tmp_path).apply(diff)
        assert not ok
        # 両方とも元に戻ってる
        assert (tmp_path / "a.py").read_text() == a_original
        assert (tmp_path / "b.py").read_text() == b_original


class TestDiffValidatorWithSearchAll:
    """DiffValidator が SEARCH_ALL/REPLACE_ALL を認識し、
    DANGEROUS_PATTERNS が REPLACE_ALL の中身にも自動適用されることを確認 (論点 6)。"""

    def test_search_all_affected_files_tracked(self):
        from patchpaw.diff_validator import DiffValidator
        diff = textwrap.dedent("""\
            FILE: src/main.py
            <<<<<<< SEARCH_ALL
            old_name
            =======
            new_name
            >>>>>>> REPLACE_ALL
        """)
        v = DiffValidator(allowed_paths=["src/"])
        r = v.validate(diff)
        assert r.ok
        assert "src/main.py" in r.affected_files

    def test_dangerous_pattern_caught_in_replace_all(self):
        from patchpaw.diff_validator import DiffValidator
        diff = textwrap.dedent("""\
            FILE: src/main.py
            <<<<<<< SEARCH_ALL
            x = 1
            =======
            import subprocess
            >>>>>>> REPLACE_ALL
        """)
        v = DiffValidator(allowed_paths=["src/"])
        r = v.validate(diff)
        assert not r.ok
        assert any("危険" in e for e in r.errors)
