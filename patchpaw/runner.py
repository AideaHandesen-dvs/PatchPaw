"""
Task Runner
tasks.txt から複数のタスクを読み、Controller に順次渡して実行する。

設計 (A 案):
- 各タスクごとに新しい Controller を生成する
  (= タスクごとに別 session_id。bash 版 patchpaw-run.sh と同じ挙動)
- 失敗時の継続/中断は stop_on_fail で制御
- commit_per_task=True なら成功タスクごとに git add -A && commit && tag
- dry_run=True なら Controller を呼ばず「何が実行されるか」だけ表示
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .controller import Controller


# ────────────────────────────────────────────
# タスクファイルパース
# ────────────────────────────────────────────

def parse_tasks_file(path: str | Path) -> list[str]:
    """
    タスクファイルを読み、タスク行のリストを返す。

    無視する行:
      - 空行
      - 先頭の空白を除いて # で始まる行 (コメント)

    bash 版の `grep -vE '^[[:space:]]*(#|$)'` と等価。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"タスクファイルが見つかりません: {path}")

    tasks: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tasks.append(stripped)
    return tasks


# ────────────────────────────────────────────
# 結果データクラス
# ────────────────────────────────────────────

@dataclass
class TaskResult:
    task: str
    success: bool
    duration_s: float
    iterations: int
    message: str
    # LLM トークン使用量 (タスク内の累積)。
    # プロバイダが usage を返さない場合は 0。
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # LLM 呼び出し時間の累積 (秒)。duration_s (タスク全体の wall-clock) との
    # 差が、テスト実行+承認待ち等の LLM 以外で使われた時間。
    llm_elapsed_s: float = 0.0
    # 適用された patch ファイル (repo_root 相対) を iteration 順に。
    patch_files: list[str] = field(default_factory=list)


# ────────────────────────────────────────────
# TaskRunner
# ────────────────────────────────────────────

class TaskRunner:
    """
    タスクのリストを順次 Controller に流すクラス。
    Controller はタスクごとに再生成される (session_id 分離)。
    """

    DEFAULT_TEST_CMD = "python -m pytest tests/ -v --tb=short"

    def __init__(
        self,
        repo_root: str | Path,
        config: Config,
        *,
        max_iter: int = 5,
        stop_on_fail: bool = True,
        commit_per_task: bool = True,
        dry_run: bool = False,
        test_cmd: str | None = None,
        start_from: int = 1,
        run_id: str | None = None,
        carry_context: bool = True,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.max_iter = max_iter
        self.stop_on_fail = stop_on_fail
        self.commit_per_task = commit_per_task
        self.dry_run = dry_run
        self.test_cmd = test_cmd or self.DEFAULT_TEST_CMD
        self.start_from = start_from  # 1-indexed。タスクファイルの N 番目から開始
        # run_id は run 全体を識別する文字列。SessionManager の session_id (タスクごと)
        # とは別物。summary JSON のファイル名にも使われる。
        self.run_id = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.started_at = datetime.now(timezone.utc).isoformat()
        # carry_context: 直前タスクで変更されたファイル一覧を次タスクの
        # プロンプトに自動注入する (v2.2 設計案 D + F)
        self.carry_context = carry_context
        self._last_affected_files: list[str] = []
        self.results: list[TaskResult] = []

    # -------------------------------------------------- #
    # public API
    # -------------------------------------------------- #

    def run_tasks(self, tasks: list[str]) -> bool:
        """タスクリストを順次実行。全成功なら True、それ以外 False。"""
        total = len(tasks)
        if total == 0:
            print("タスクが空です。")
            return False

        # start_from の範囲チェック
        if self.start_from < 1 or self.start_from > total:
            print(
                f"❌ --continue-from-task {self.start_from} は範囲外です "
                f"(有効範囲: 1〜{total})"
            )
            return False

        has_git = self._is_git_repo()
        if self.commit_per_task and not has_git and not self.dry_run:
            print("⚠️  git リポジトリ外なので commit/tag は無効化されます。")

        print(f"▶ タスク数: {total}")
        if self.start_from > 1:
            print(f"▶ 開始タスク: {self.start_from} (1〜{self.start_from - 1} はスキップ)")
        print(f"▶ テストコマンド: {self.test_cmd}")
        print(f"▶ 最大試行回数: {self.max_iter}")
        print(f"▶ リポジトリ: {self.repo_root}")
        if self.dry_run:
            print("⚠️  DRY-RUN モード: 実際には実行しません")
        print()

        all_ok = True
        for i, task in enumerate(tasks, 1):
            if i < self.start_from:
                continue
            print(f"━━━ [{i}/{total}] {task} ━━━")

            if self.dry_run:
                print(f"  (dry-run) instruction={task!r}")
                print()
                continue

            ok = self._run_one(i, task, has_git=has_git)
            if not ok:
                all_ok = False
                if self.stop_on_fail:
                    print("STOP_ON_FAIL=1 のため停止。sessions/ にログが残っています。")
                    break
                print("STOP_ON_FAIL=0 のため次のタスクへ継続。")
            print()

        if not self.dry_run:
            self._write_summary(total)
        self._print_summary(total)
        return all_ok

    # -------------------------------------------------- #
    # internal
    # -------------------------------------------------- #

    def _run_one(self, index: int, task: str, *, has_git: bool) -> bool:
        """1 タスクを実行し、成功なら True を返す。"""
        t_start = time.time()

        # 直前タスクの変更ファイルを引き継ぐ (carry_context=True のとき)
        prev_changes = self._last_affected_files if self.carry_context else None
        if prev_changes:
            print(f"   📎 引き継ぎ: 直前タスクで変更されたファイル {prev_changes}")

        try:
            controller = Controller(
                repo_root=self.repo_root,
                config=self.config,
                max_iterations=self.max_iter,
                approval_callback=lambda _diff: True,  # --yes 相当
            )
            result = controller.run(
                instruction=task,
                file_hints=None,
                test_command=self.test_cmd,
                previous_task_changes=prev_changes,
            )
            dur = time.time() - t_start
            self.results.append(TaskResult(
                task=task,
                success=result.success,
                duration_s=dur,
                iterations=result.iterations,
                message=result.message,
                prompt_tokens=getattr(result, "prompt_tokens", 0),
                completion_tokens=getattr(result, "completion_tokens", 0),
                total_tokens=getattr(result, "total_tokens", 0),
                llm_elapsed_s=getattr(result, "llm_elapsed_s", 0.0),
                patch_files=list(getattr(result, "patch_files", [])),
            ))

            if result.success:
                print(f"✓ 完了 ({dur:.1f}s): {task}")
                # 次タスクへの引き継ぎ用に affected_files を保持
                # ("変更不要" 判定で affected_files が空のときは前回の値を温存しない =
                # 「直前の実変更」をルールにする)
                self._last_affected_files = list(result.affected_files)
                if self.commit_per_task and has_git:
                    self._git_commit_and_tag(index, task)
                return True
            else:
                print(f"✗ 失敗 ({dur:.1f}s): {task}")
                print(f"   {result.message}")
                # 失敗時は affected_files をリセットしない
                # (前回成功時の文脈を次へ引き継ぐ余地を残す)
                return False

        except Exception as e:
            dur = time.time() - t_start
            self.results.append(TaskResult(
                task=task,
                success=False,
                duration_s=dur,
                iterations=0,
                message=f"例外: {e}",
            ))
            print(f"✗ 例外 ({dur:.1f}s): {task}")
            print(f"   {e}")
            return False

    def _is_git_repo(self) -> bool:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.repo_root),
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _git_commit_and_tag(self, index: int, task: str) -> None:
        """成功タスクの節目に git add -A && commit && tag を打つ。失敗は print のみ。"""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.repo_root), check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit",
                 "-m", f"patchpaw: task {index} - {task}",
                 "--allow-empty", "-q"],
                cwd=str(self.repo_root), check=True, capture_output=True,
            )
            tag = f"patchpaw-task-{index}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            r = subprocess.run(
                ["git", "tag", tag],
                cwd=str(self.repo_root), capture_output=True,
            )
            if r.returncode == 0:
                print(f"   節目タグ: {tag}  (git reset --hard {tag} で戻せる)")
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️ git 操作失敗 (スキップ): {e}")

    def _write_summary(self, total: int) -> None:
        """run 全体のサマリを JSON でファイルに書く。

        保存先: <repo_root>/<session.storage_dir>/<run_id>_summary.json

        含めるもの:
          - run_id, started_at, finished_at, total_duration_s
          - 設定値 (start_from, max_iter, test_cmd, dry_run 等)
          - 集計 (tasks_file_total, executed, succeeded, failed)
          - llm_elapsed_total_s: run 全体の LLM 呼び出し時間合計 (秒)
          - patches_total: run 全体で適用された patch ファイルの総数
          - tokens_total: run 全体の LLM トークン使用量集計
          - tasks: 各タスクの (task, success, duration_s, llm_elapsed_s,
            iterations, message, tokens, patch_files)
        """
        storage_dir = self.repo_root / self.config.session.storage_dir
        storage_dir.mkdir(parents=True, exist_ok=True)
        summary_path = storage_dir / f"{self.run_id}_summary.json"

        succeeded = sum(1 for r in self.results if r.success)
        failed_count = sum(1 for r in self.results if not r.success)
        total_duration = sum(r.duration_s for r in self.results)

        # トークン合計 (プロバイダが usage を返さない場合は 0 のまま)
        total_prompt = sum(r.prompt_tokens for r in self.results)
        total_completion = sum(r.completion_tokens for r in self.results)
        total_tokens = sum(r.total_tokens for r in self.results)
        # LLM 呼び出し時間の合計 (秒)
        llm_elapsed_total = sum(r.llm_elapsed_s for r in self.results)
        # 適用された patch ファイルの総数 (run 全体)
        patches_total = sum(len(r.patch_files) for r in self.results)

        summary = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(self.repo_root),
            "test_cmd": self.test_cmd,
            "max_iter": self.max_iter,
            "stop_on_fail": self.stop_on_fail,
            "commit_per_task": self.commit_per_task,
            "start_from": self.start_from,
            "tasks_file_total": total,
            "executed": len(self.results),
            "succeeded": succeeded,
            "failed": failed_count,
            "total_duration_s": round(total_duration, 2),
            "llm_elapsed_total_s": round(llm_elapsed_total, 2),
            "patches_total": patches_total,
            "tokens_total": {
                "prompt": total_prompt,
                "completion": total_completion,
                "total": total_tokens,
            },
            "tasks": [
                {
                    "task": r.task,
                    "success": r.success,
                    "duration_s": round(r.duration_s, 2),
                    "llm_elapsed_s": round(r.llm_elapsed_s, 2),
                    "iterations": r.iterations,
                    "message": r.message,
                    "tokens": {
                        "prompt": r.prompt_tokens,
                        "completion": r.completion_tokens,
                        "total": r.total_tokens,
                    },
                    "patch_files": list(r.patch_files),
                }
                for r in self.results
            ],
        }

        try:
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"📄 サマリ保存: {summary_path}")
        except OSError as e:
            print(f"⚠️ サマリ保存失敗: {e}")

    def _print_summary(self, total: int) -> None:
        succeeded = sum(1 for r in self.results if r.success)
        failed = [r for r in self.results if not r.success]
        executed = len(self.results)
        print()
        print("═══ サマリ ═══")
        if self.dry_run:
            print("DRY-RUN 完了。実行されたタスクはありません。")
            return
        if self.start_from > 1:
            print(
                f"✓ 成功: {succeeded} / {executed} "
                f"(タスク {self.start_from} から開始、総 {total} タスク)"
            )
        else:
            print(f"✓ 成功: {succeeded} / {total}")
        if failed:
            print(f"✗ 失敗: {len(failed)}")
            for f in failed:
                print(f"    {f.task}: {f.message}")
        else:
            print("🎉 全タスク完了")
