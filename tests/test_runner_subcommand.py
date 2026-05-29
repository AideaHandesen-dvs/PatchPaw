"""
patchpaw run サブコマンドのテスト

カバー範囲:
  - parse_tasks_file: コメント/空行の無視、欠落ファイル
  - TaskRunner: dry_run モードで Controller を呼ばずに進むこと、
                タスクが空の場合の挙動
  - cli ヘルパ: _env_bool / _env_int の挙動
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from patchpaw.config import Config
from patchpaw.runner import TaskRunner, parse_tasks_file


# ────────────────────────────────────────────
# parse_tasks_file
# ────────────────────────────────────────────

class TestParseTasksFile:
    def test_basic_tasks(self, tmp_path):
        p = tmp_path / "tasks.txt"
        p.write_text("task one\ntask two\ntask three\n", encoding="utf-8")
        assert parse_tasks_file(p) == ["task one", "task two", "task three"]

    def test_ignores_comments(self, tmp_path):
        p = tmp_path / "tasks.txt"
        p.write_text(
            "# this is a comment\n"
            "real task\n"
            "  # indented comment\n"
            "another task\n",
            encoding="utf-8",
        )
        assert parse_tasks_file(p) == ["real task", "another task"]

    def test_ignores_blank_lines(self, tmp_path):
        p = tmp_path / "tasks.txt"
        p.write_text(
            "\n"
            "task one\n"
            "\n"
            "   \n"
            "task two\n"
            "\n",
            encoding="utf-8",
        )
        assert parse_tasks_file(p) == ["task one", "task two"]

    def test_strips_whitespace(self, tmp_path):
        p = tmp_path / "tasks.txt"
        p.write_text("  task with leading spaces  \n", encoding="utf-8")
        assert parse_tasks_file(p) == ["task with leading spaces"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_tasks_file(tmp_path / "nonexistent.txt")

    def test_only_comments_returns_empty(self, tmp_path):
        p = tmp_path / "tasks.txt"
        p.write_text("# only comments\n# nothing else\n\n", encoding="utf-8")
        assert parse_tasks_file(p) == []


# ────────────────────────────────────────────
# TaskRunner (dry_run)
# ────────────────────────────────────────────

class TestTaskRunnerDryRun:
    """dry_run=True なら Controller を一切生成しないことを確認するテスト。"""

    def test_dry_run_does_not_invoke_controller(self, tmp_path, capsys, monkeypatch):
        # Controller を呼んだら絶対に失敗する mock を仕込む
        def _boom(*args, **kwargs):
            raise AssertionError("Controller should not be instantiated in dry_run")

        monkeypatch.setattr("patchpaw.runner.Controller", _boom)

        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
        )
        ok = runner.run_tasks(["task one", "task two"])

        # dry_run なので results は空
        assert runner.results == []
        # 全タスク「成功扱い」ではなく、単にスキップなので all_ok は True のままで返る
        assert ok is True

        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "task one" in out
        assert "task two" in out

    def test_empty_tasks_returns_false(self, tmp_path, capsys):
        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
        )
        ok = runner.run_tasks([])
        assert ok is False
        assert "タスクが空" in capsys.readouterr().out


# ────────────────────────────────────────────
# cli ヘルパ
# ────────────────────────────────────────────

class TestEnvHelpers:
    """CLI フラグが未指定のとき環境変数を見るフォールバック挙動の検証。"""

    def test_env_bool_unset_returns_default(self, monkeypatch):
        from patchpaw.cli import _env_bool
        monkeypatch.delenv("FOO", raising=False)
        assert _env_bool("FOO", True) is True
        assert _env_bool("FOO", False) is False

    def test_env_bool_zero_is_false(self, monkeypatch):
        from patchpaw.cli import _env_bool
        monkeypatch.setenv("FOO", "0")
        assert _env_bool("FOO", True) is False

    def test_env_bool_false_is_false(self, monkeypatch):
        from patchpaw.cli import _env_bool
        monkeypatch.setenv("FOO", "false")
        assert _env_bool("FOO", True) is False
        monkeypatch.setenv("FOO", "False")
        assert _env_bool("FOO", True) is False

    def test_env_bool_one_is_true(self, monkeypatch):
        from patchpaw.cli import _env_bool
        monkeypatch.setenv("FOO", "1")
        assert _env_bool("FOO", False) is True

    def test_env_bool_empty_is_false(self, monkeypatch):
        from patchpaw.cli import _env_bool
        monkeypatch.setenv("FOO", "")
        assert _env_bool("FOO", True) is False

    def test_env_int_unset_returns_default(self, monkeypatch):
        from patchpaw.cli import _env_int
        monkeypatch.delenv("FOO", raising=False)
        assert _env_int("FOO", 5) == 5

    def test_env_int_valid(self, monkeypatch):
        from patchpaw.cli import _env_int
        monkeypatch.setenv("FOO", "3")
        assert _env_int("FOO", 5) == 3

    def test_env_int_invalid_returns_default(self, monkeypatch):
        from patchpaw.cli import _env_int
        monkeypatch.setenv("FOO", "not_a_number")
        assert _env_int("FOO", 5) == 5
