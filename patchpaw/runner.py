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

import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
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
    ):
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.max_iter = max_iter
        self.stop_on_fail = stop_on_fail
        self.commit_per_task = commit_per_task
        self.dry_run = dry_run
        self.test_cmd = test_cmd or self.DEFAULT_TEST_CMD
        self.start_from = start_from  # 1-indexed。タスクファイルの N 番目から開始
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

        self._print_summary(total)
        return all_ok

    # -------------------------------------------------- #
    # internal
    # -------------------------------------------------- #

    def _run_one(self, index: int, task: str, *, has_git: bool) -> bool:
        """1 タスクを実行し、成功なら True を返す。"""
        t_start = time.time()
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
            )
            dur = time.time() - t_start
            self.results.append(TaskResult(
                task=task,
                success=result.success,
                duration_s=dur,
                iterations=result.iterations,
                message=result.message,
            ))

            if result.success:
                print(f"✓ 完了 ({dur:.1f}s): {task}")
                if self.commit_per_task and has_git:
                    self._git_commit_and_tag(index, task)
                return True
            else:
                print(f"✗ 失敗 ({dur:.1f}s): {task}")
                print(f"   {result.message}")
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
