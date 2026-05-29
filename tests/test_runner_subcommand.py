"""
patchpaw run サブコマンドのテスト

カバー範囲:
  - parse_tasks_file: コメント/空行の無視、欠落ファイル
  - TaskRunner: dry_run モードで Controller を呼ばずに進むこと、
                タスクが空の場合の挙動
  - cli ヘルパ: _env_bool / _env_int の挙動
"""

from __future__ import annotations

import json
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
# TaskRunner (--continue-from-task)
# ────────────────────────────────────────────

class TestContinueFromTask:
    """--continue-from-task N で N 番目以降から開始する挙動を検証する。"""

    def test_skips_earlier_tasks(self, tmp_path, capsys, monkeypatch):
        """start_from=2 のとき 1 番目はスキップされ 2 番目以降だけ表示される。"""
        def _boom(*args, **kwargs):
            raise AssertionError("Controller should not be instantiated in dry_run")
        monkeypatch.setattr("patchpaw.runner.Controller", _boom)

        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=2,
        )
        ok = runner.run_tasks(["task one", "task two", "task three"])

        assert ok is True
        out = capsys.readouterr().out
        # task one はスキップされている = 表示されない
        assert "task one" not in out
        # task two と task three は実行 (dry-run でも表示) される
        assert "[2/3] task two" in out
        assert "[3/3] task three" in out
        # 開始位置が告知されている
        assert "開始タスク: 2" in out

    def test_start_from_1_is_default_behavior(self, tmp_path, capsys, monkeypatch):
        """start_from=1 (デフォルト) は従来通り 1 番目から開始。"""
        def _noop(*args, **kwargs):
            raise AssertionError("Controller should not be instantiated in dry_run")
        monkeypatch.setattr("patchpaw.runner.Controller", _noop)

        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=1,
        )
        ok = runner.run_tasks(["a", "b"])

        assert ok is True
        out = capsys.readouterr().out
        assert "[1/2] a" in out
        assert "[2/2] b" in out
        # start_from=1 のときは「開始タスク」表示は出ない
        assert "開始タスク" not in out

    def test_out_of_range_too_high(self, tmp_path, capsys):
        """start_from がタスク数を超えるとエラーになる。"""
        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=99,
        )
        ok = runner.run_tasks(["a", "b", "c"])

        assert ok is False
        out = capsys.readouterr().out
        assert "範囲外" in out
        assert "99" in out

    def test_out_of_range_zero(self, tmp_path, capsys):
        """start_from=0 もエラー (1-indexed なので)。"""
        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=0,
        )
        ok = runner.run_tasks(["a", "b"])

        assert ok is False
        assert "範囲外" in capsys.readouterr().out

    def test_out_of_range_negative(self, tmp_path, capsys):
        """負の値もエラー。"""
        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=-1,
        )
        ok = runner.run_tasks(["a"])

        assert ok is False
        assert "範囲外" in capsys.readouterr().out

    def test_start_from_last_task(self, tmp_path, capsys, monkeypatch):
        """start_from がちょうど最後のタスク番号なら 1 タスクだけ実行される。"""
        monkeypatch.setattr("patchpaw.runner.Controller",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                AssertionError("dry_run なのに Controller が呼ばれた")
                            ))

        runner = TaskRunner(
            repo_root=tmp_path,
            config=Config(),
            dry_run=True,
            commit_per_task=False,
            start_from=3,
        )
        ok = runner.run_tasks(["a", "b", "c"])

        assert ok is True
        out = capsys.readouterr().out
        assert "a" not in out.split("━━━")[1] if "━━━" in out else True
        assert "[3/3] c" in out


# ────────────────────────────────────────────
# TaskRunner サマリ JSON 出力 (v2.3)
# ────────────────────────────────────────────

class _StubRunResult:
    """Controller.run の戻り値を模した最小スタブ。"""
    def __init__(self, success=True, iterations=1, message="完了"):
        self.success = success
        self.iterations = iterations
        self.message = message
        self.final_output = ""
        self.final_test_output = ""


class _StubController:
    """Controller を差し替える最小スタブ。タスク内容に応じて結果を変える。

    'fail:' で始まるタスクは失敗扱い、それ以外は成功扱い。
    """
    def __init__(self, repo_root, config, *, max_iterations, approval_callback):
        pass

    def run(self, instruction, file_hints, test_command):
        if instruction.startswith("fail:"):
            return _StubRunResult(success=False, iterations=3, message="テスト失敗")
        return _StubRunResult(success=True, iterations=1, message="完了")


class TestSummaryJson:
    """run_tasks が sessions/<run_id>_summary.json を書くことを検証する。"""

    def _runner(self, tmp_path, **kwargs):
        """sessions/ を tmp_path 配下に向けた TaskRunner を返す。"""
        config = Config()
        config.session.storage_dir = "sessions/"
        kwargs.setdefault("commit_per_task", False)
        kwargs.setdefault("run_id", "run_TEST")
        return TaskRunner(
            repo_root=tmp_path,
            config=config,
            **kwargs,
        )

    def _read_summary(self, tmp_path, run_id="run_TEST"):
        p = tmp_path / "sessions" / f"{run_id}_summary.json"
        assert p.exists(), f"summary not written at {p}"
        return json.loads(p.read_text(encoding="utf-8"))

    def test_summary_written_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path)
        ok = runner.run_tasks(["task A", "task B"])

        assert ok is True
        s = self._read_summary(tmp_path)
        assert s["run_id"] == "run_TEST"
        assert s["tasks_file_total"] == 2
        assert s["executed"] == 2
        assert s["succeeded"] == 2
        assert s["failed"] == 0
        assert s["start_from"] == 1
        assert len(s["tasks"]) == 2
        assert s["tasks"][0]["task"] == "task A"
        assert s["tasks"][0]["success"] is True
        assert s["tasks"][1]["task"] == "task B"

    def test_summary_includes_failures(self, tmp_path, monkeypatch):
        """失敗タスクの情報が summary に残る。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path, stop_on_fail=False)
        ok = runner.run_tasks(["task A", "fail: task B", "task C"])

        assert ok is False
        s = self._read_summary(tmp_path)
        assert s["executed"] == 3
        assert s["succeeded"] == 2
        assert s["failed"] == 1
        # 失敗タスクの message が記録されている
        failed_entries = [t for t in s["tasks"] if not t["success"]]
        assert len(failed_entries) == 1
        assert "テスト失敗" in failed_entries[0]["message"]

    def test_summary_respects_stop_on_fail(self, tmp_path, monkeypatch):
        """stop_on_fail=True で途中終了したら executed は途中まで。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path, stop_on_fail=True)
        ok = runner.run_tasks(["task A", "fail: task B", "task C"])

        assert ok is False
        s = self._read_summary(tmp_path)
        # task C は実行されない
        assert s["executed"] == 2
        assert s["succeeded"] == 1
        assert s["failed"] == 1
        assert all(t["task"] != "task C" for t in s["tasks"])

    def test_summary_records_start_from(self, tmp_path, monkeypatch):
        """--continue-from-task の値が summary に反映される。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path, start_from=2)
        ok = runner.run_tasks(["task A", "task B", "task C"])

        assert ok is True
        s = self._read_summary(tmp_path)
        assert s["start_from"] == 2
        assert s["tasks_file_total"] == 3
        assert s["executed"] == 2
        # スキップされた task A は tasks に含まれない
        assert all(t["task"] != "task A" for t in s["tasks"])

    def test_dry_run_does_not_write_summary(self, tmp_path, monkeypatch):
        """dry_run=True のときはサマリを書かない。"""
        def _boom(*a, **kw):
            raise AssertionError("Controller should not be invoked in dry_run")
        monkeypatch.setattr("patchpaw.runner.Controller", _boom)

        runner = self._runner(tmp_path, dry_run=True)
        runner.run_tasks(["a", "b"])

        summary_path = tmp_path / "sessions" / "run_TEST_summary.json"
        assert not summary_path.exists()

    def test_summary_path_in_output(self, tmp_path, monkeypatch, capsys):
        """サマリ保存先のパスが標準出力に表示される。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["a"])

        out = capsys.readouterr().out
        assert "📄 サマリ保存" in out
        assert "run_TEST_summary.json" in out

    def test_run_id_auto_generated(self, tmp_path, monkeypatch):
        """run_id を渡さなければ datetime ベースで自動生成される。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        config = Config()
        config.session.storage_dir = "sessions/"
        runner = TaskRunner(
            repo_root=tmp_path,
            config=config,
            commit_per_task=False,
            # run_id を渡さない
        )
        assert runner.run_id.startswith("run_")
        # 形式: run_YYYYMMDD_HHMMSS
        assert len(runner.run_id) == len("run_YYYYMMDD_HHMMSS")


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
