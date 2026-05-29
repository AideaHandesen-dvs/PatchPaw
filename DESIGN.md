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
    def __init__(self, success=True, iterations=1, message="完了", affected_files=None):
        self.success = success
        self.iterations = iterations
        self.message = message
        self.final_output = ""
        self.final_test_output = ""
        self.affected_files = affected_files or []


class _StubController:
    """Controller を差し替える最小スタブ。タスク内容に応じて結果を変える。

    'fail:' で始まるタスクは失敗扱い、それ以外は成功扱い。
    """
    def __init__(self, repo_root, config, *, max_iterations, approval_callback):
        pass

    def run(self, instruction, file_hints, test_command, previous_task_changes=None):
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

    # ─── トークン使用量 (v2.3.x) ───

    def test_summary_tokens_default_zero_when_stub_omits_them(
        self, tmp_path, monkeypatch
    ):
        """tokens フィールドを持たないスタブでも summary は壊れず 0 で出る。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A"])

        s = self._read_summary(tmp_path)
        assert s["tokens_total"] == {"prompt": 0, "completion": 0, "total": 0}
        assert s["tasks"][0]["tokens"] == {
            "prompt": 0, "completion": 0, "total": 0,
        }

    def test_summary_aggregates_tokens(self, tmp_path, monkeypatch):
        """RunResult が tokens を返せば、per-task と tokens_total に集計される。"""
        from patchpaw.controller import RunResult

        class _TokenController:
            def __init__(self, repo_root, config, *, max_iterations, approval_callback):
                pass
            def run(self, instruction, file_hints, test_command,
                    previous_task_changes=None):
                # タスクごとに異なるトークン数を返して集計を検証可能にする
                if instruction == "task A":
                    return RunResult(
                        success=True, iterations=1,
                        final_output="", final_test_output="", message="完了",
                        prompt_tokens=10, completion_tokens=20, total_tokens=30,
                    )
                return RunResult(
                    success=True, iterations=2,
                    final_output="", final_test_output="", message="完了",
                    prompt_tokens=5, completion_tokens=7, total_tokens=12,
                )

        monkeypatch.setattr("patchpaw.runner.Controller", _TokenController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A", "task B"])

        s = self._read_summary(tmp_path)
        assert s["tasks"][0]["tokens"] == {
            "prompt": 10, "completion": 20, "total": 30,
        }
        assert s["tasks"][1]["tokens"] == {
            "prompt": 5, "completion": 7, "total": 12,
        }
        # 全タスク合算
        assert s["tokens_total"] == {
            "prompt": 15, "completion": 27, "total": 42,
        }

    # ─── LLM 呼び出し時間 (v2.3.x) ───

    def test_summary_llm_elapsed_default_zero_when_stub_omits(
        self, tmp_path, monkeypatch
    ):
        """llm_elapsed_s を持たないスタブでも summary は壊れず 0.0 で出る。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A"])

        s = self._read_summary(tmp_path)
        assert s["llm_elapsed_total_s"] == 0.0
        assert s["tasks"][0]["llm_elapsed_s"] == 0.0

    def test_summary_aggregates_llm_elapsed(self, tmp_path, monkeypatch):
        """RunResult が llm_elapsed_s を返せば per-task と total に集計される。"""
        from patchpaw.controller import RunResult

        class _ElapsedController:
            def __init__(self, repo_root, config, *, max_iterations, approval_callback):
                pass
            def run(self, instruction, file_hints, test_command,
                    previous_task_changes=None):
                if instruction == "task A":
                    return RunResult(
                        success=True, iterations=1,
                        final_output="", final_test_output="", message="完了",
                        llm_elapsed_s=1.23,
                    )
                return RunResult(
                    success=True, iterations=3,
                    final_output="", final_test_output="", message="完了",
                    llm_elapsed_s=4.56,
                )

        monkeypatch.setattr("patchpaw.runner.Controller", _ElapsedController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A", "task B"])

        s = self._read_summary(tmp_path)
        # round(_, 2) されてるので == 比較で OK
        assert s["tasks"][0]["llm_elapsed_s"] == 1.23
        assert s["tasks"][1]["llm_elapsed_s"] == 4.56
        # 合計も round(_, 2) されてるが、1.23 + 4.56 = 5.79 ちょうど
        assert s["llm_elapsed_total_s"] == 5.79

    # ─── 適用 patch ファイルパス (v2.3.x) ───

    def test_summary_patch_files_default_empty(self, tmp_path, monkeypatch):
        """patch_files を持たないスタブでも summary は壊れず [] / 0 で出る。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _StubController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A"])

        s = self._read_summary(tmp_path)
        assert s["patches_total"] == 0
        assert s["tasks"][0]["patch_files"] == []

    def test_summary_aggregates_patch_files(self, tmp_path, monkeypatch):
        """RunResult.patch_files が per-task に出て、patches_total に集計される。"""
        from patchpaw.controller import RunResult

        class _PatchController:
            def __init__(self, repo_root, config, *, max_iterations, approval_callback):
                pass
            def run(self, instruction, file_hints, test_command,
                    previous_task_changes=None):
                if instruction == "task A":
                    return RunResult(
                        success=True, iterations=2,
                        final_output="", final_test_output="", message="完了",
                        patch_files=[
                            "sessions/20260529_120000_iter1.patch",
                            "sessions/20260529_120000_iter2.patch",
                        ],
                    )
                return RunResult(
                    success=True, iterations=1,
                    final_output="", final_test_output="", message="完了",
                    patch_files=["sessions/20260529_120100_iter1.patch"],
                )

        monkeypatch.setattr("patchpaw.runner.Controller", _PatchController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A", "task B"])

        s = self._read_summary(tmp_path)
        assert s["tasks"][0]["patch_files"] == [
            "sessions/20260529_120000_iter1.patch",
            "sessions/20260529_120000_iter2.patch",
        ]
        assert s["tasks"][1]["patch_files"] == [
            "sessions/20260529_120100_iter1.patch",
        ]
        # patches_total = 2 + 1
        assert s["patches_total"] == 3


# ────────────────────────────────────────────
# TaskRunner carry_context (v2.2)
# ────────────────────────────────────────────

class _RecordingController:
    """previous_task_changes が何で呼ばれたかを記録するスタブ。"""
    # クラス変数で記録 (各タスクで新しいインスタンスが作られるため)
    call_log: list = []

    def __init__(self, repo_root, config, *, max_iterations, approval_callback):
        pass

    def run(self, instruction, file_hints, test_command, previous_task_changes=None):
        # 呼び出し時の previous_task_changes を記録
        _RecordingController.call_log.append({
            "instruction": instruction,
            "previous_task_changes": (
                list(previous_task_changes) if previous_task_changes else None
            ),
        })
        # タスク文字列に応じた affected_files を返す
        # 'changes:A,B' なら affected_files=['A', 'B']
        # それ以外なら ['default.py']
        affected = ["default.py"]
        if instruction.startswith("changes:"):
            affected = instruction.split(":", 1)[1].split(",")
        from patchpaw.controller import RunResult
        return RunResult(
            success=True,
            iterations=1,
            final_output="",
            final_test_output="",
            message="完了",
            affected_files=affected,
        )


class TestCarryContext:
    """直前タスクの affected_files が次タスクに渡ることを検証する。"""

    def setup_method(self):
        _RecordingController.call_log = []

    def _runner(self, tmp_path, **kwargs):
        config = Config()
        config.session.storage_dir = "sessions/"
        kwargs.setdefault("commit_per_task", False)
        kwargs.setdefault("run_id", "run_TEST")
        return TaskRunner(
            repo_root=tmp_path,
            config=config,
            **kwargs,
        )

    def test_first_task_has_no_previous_changes(self, tmp_path, monkeypatch):
        """最初のタスクには previous_task_changes は渡されない (None)。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _RecordingController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["changes:foo.py"])

        assert len(_RecordingController.call_log) == 1
        assert _RecordingController.call_log[0]["previous_task_changes"] is None

    def test_second_task_receives_first_task_changes(self, tmp_path, monkeypatch):
        """2 タスク目には 1 タスク目の affected_files が渡る。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _RecordingController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["changes:foo.py,bar.py", "task two"])

        assert len(_RecordingController.call_log) == 2
        # 1 タスク目には何も渡らない
        assert _RecordingController.call_log[0]["previous_task_changes"] is None
        # 2 タスク目には 1 タスク目の変更が渡る
        assert _RecordingController.call_log[1]["previous_task_changes"] == [
            "foo.py", "bar.py"
        ]

    def test_three_task_chain_only_carries_immediate_previous(self, tmp_path, monkeypatch):
        """3 タスク連鎖で、3 タスク目には 2 タスク目の変更だけ (累積しない)。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _RecordingController)
        runner = self._runner(tmp_path)
        runner.run_tasks([
            "changes:A.py",
            "changes:B.py",
            "task three",
        ])

        assert _RecordingController.call_log[0]["previous_task_changes"] is None
        assert _RecordingController.call_log[1]["previous_task_changes"] == ["A.py"]
        # A.py は含まれず B.py だけ (= 直前 1 タスクのみ、累積しない)
        assert _RecordingController.call_log[2]["previous_task_changes"] == ["B.py"]

    def test_carry_context_disabled(self, tmp_path, monkeypatch):
        """carry_context=False ならどのタスクにも previous_task_changes は渡らない。"""
        monkeypatch.setattr("patchpaw.runner.Controller", _RecordingController)
        runner = self._runner(tmp_path, carry_context=False)
        runner.run_tasks(["changes:foo.py", "task two", "task three"])

        for entry in _RecordingController.call_log:
            assert entry["previous_task_changes"] is None

    def test_empty_affected_files_propagates(self, tmp_path, monkeypatch):
        """直前タスクの affected_files が空なら次タスクには None として渡る
        (PromptBuilder は空リストでもセクションを出さないので実害なし)。"""

        # 空の affected_files を返す Controller スタブ
        class _EmptyAffectedController:
            def __init__(self, **kwargs):
                pass
            def run(self, instruction, file_hints, test_command, previous_task_changes=None):
                _RecordingController.call_log.append({
                    "instruction": instruction,
                    "previous_task_changes": (
                        list(previous_task_changes) if previous_task_changes else None
                    ),
                })
                from patchpaw.controller import RunResult
                return RunResult(
                    success=True, iterations=1,
                    final_output="", final_test_output="",
                    message="変更不要", affected_files=[],
                )

        monkeypatch.setattr("patchpaw.runner.Controller", _EmptyAffectedController)
        runner = self._runner(tmp_path)
        runner.run_tasks(["task A", "task B"])

        # 2 タスク目には空リスト or None が渡る (どちらでもセクションは出ない)
        prev = _RecordingController.call_log[1]["previous_task_changes"]
        assert prev is None or prev == []


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
