"""
Controller
全体の状態管理と処理フロー制御。

フロー:
  1. ユーザー指示受け取り
  2. 関連ファイル収集
  3. プロンプト生成（常時文脈があれば自動注入）
  4. LLM で SEARCH/REPLACE ブロック生成
  5. ブロック検証（スコープ・危険パターン）
  6. dry_run（SEARCHが一意に存在するか確認）
  7. ユーザー承認
  8. 置換適用
  9. テスト実行
  10. 成功 or 再試行（最大 max_iterations）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import time

from .config import Config
from .diff_validator import DiffValidator
from .llm_adapter import build_adapter
from .patch_applier import PatchApplier
from .prompt_builder import PromptBuilder
from .repository_reader import RepositoryReader
from .session_manager import SessionEntry, SessionManager
from .test_runner import TestRunner


@dataclass
class RunResult:
    success: bool
    iterations: int
    final_output: str
    final_test_output: str
    message: str
    affected_files: list[str] = field(default_factory=list)


class Controller:
    def __init__(
        self,
        repo_root: str | Path,
        config: Config,
        *,
        max_iterations: int = 5,
        approval_callback: Callable[[str], bool] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        self.root = Path(repo_root)
        self.config = config
        self.max_iterations = max_iterations
        self.approval_cb = approval_callback or (lambda _: True)
        self.progress = progress_callback or print

        self.reader = RepositoryReader(self.root, config)
        self.prompt_builder = PromptBuilder()
        self.llm = build_adapter(config.llm)
        self.validator = DiffValidator(config.repository.allowed_paths)
        self.applier = PatchApplier(self.root)
        self.test_runner = TestRunner(self.root, config.sandbox)
        self.session = SessionManager(self.root, config.session)

        # 常時文脈の読み込み (.patchpaw/context.md)
        context_path = self.root / ".patchpaw" / "context.md"
        self.project_context: str | None = None
        if context_path.exists():
            self.project_context = context_path.read_text(encoding="utf-8")
            self.progress(f"📋 常時文脈を読み込みました: {context_path}")

    def run(
        self,
        instruction: str,
        file_hints: list[str] | None = None,
        test_command: str = "python -m pytest tests/ -v --tb=short",
        previous_task_changes: list[str] | None = None,
    ) -> RunResult:
        self.progress(f"📂 ファイル収集中 ({'指定ファイル' if file_hints else 'ホワイトリスト全体'})...")
        files = self.reader.collect_files(file_hints)
        if not files:
            return RunResult(
                success=False,
                iterations=0,
                final_output="",
                final_test_output="",
                message="読み取れるファイルが見つかりませんでした。config.yaml の allowed_paths を確認してください。",
            )
        self.progress(f"   {len(files)} ファイル読み込み完了")

        previous_output: str | None = None
        test_output: str | None = None

        for iteration in range(1, self.max_iterations + 1):
            self.progress(f"\n🤖 LLM に変更案を生成依頼 (試行 {iteration}/{self.max_iterations})...")
            messages = self.prompt_builder.build(
                instruction=instruction,
                file_contents=files,
                test_result=test_output,
                previous_output=previous_output,
                iteration=iteration,
                project_context=self.project_context,
                previous_task_changes=previous_task_changes,
            )

            try:
                t_start = time.time()
                llm_output = self.llm.generate(messages)
                elapsed = time.time() - t_start
                self.progress(f"⏱ LLM 応答時間: {elapsed:.2f}s")
            except Exception as e:
                return RunResult(
                    success=False,
                    iterations=iteration,
                    final_output="",
                    final_test_output=test_output or "",
                    message=f"LLM エラー: {e}",
                )

            if not llm_output.strip():
                return RunResult(
                    success=True,
                    iterations=iteration,
                    final_output="",
                    final_test_output="",
                    message="LLM: 変更不要と判断しました。",
                )

            # ---- 検証 ----
            self.progress("🔍 出力を検証中...")
            validation = self.validator.validate(llm_output)
            if not validation.ok:
                self.progress("❌ 検証エラー:")
                for err in validation.errors:
                    self.progress(f"   • {err}")
                test_output = "検証エラー:\n" + "\n".join(validation.errors)
                previous_output = llm_output
                continue

            self.progress(f"   対象ファイル: {', '.join(validation.affected_files)}")

            # ---- dry-run ----
            ok, dry_msg = self.applier.dry_run(llm_output)
            if not ok:
                self.progress(f"❌ 適用チェック失敗:\n{dry_msg}")
                test_output = f"適用チェック失敗:\n{dry_msg}"
                previous_output = llm_output
                continue

            # ---- ユーザー承認 ----
            self.progress("\n" + "─" * 60)
            self.progress(llm_output)
            self.progress("─" * 60)
            if not self.approval_cb(llm_output):
                return RunResult(
                    success=False,
                    iterations=iteration,
                    final_output=llm_output,
                    final_test_output="",
                    message="ユーザーが変更を拒否しました。",
                )

            # ---- 適用 ----
            self.progress("📝 変更を適用中...")
            applied, apply_msg = self.applier.apply(llm_output)
            if not applied:
                self.progress(f"❌ 適用失敗: {apply_msg}")
                test_output = f"適用失敗:\n{apply_msg}"
                previous_output = llm_output
                continue

            patch_path = self.session.save_diff(llm_output, label=f"iter{iteration}")
            self.progress(f"   保存: {patch_path}")

            # ---- テスト実行 ----
            self.progress("🧪 テスト実行中...")
            result = self.test_runner.run(test_command)
            test_output = result.output
            self.progress(result.output[:2000])

            entry = SessionEntry(
                iteration=iteration,
                instruction=instruction,
                diff=llm_output,
                test_success=result.success,
                test_output=result.output,
                applied=True,
            )
            self.session.record(entry)

            if result.success:
                self.progress("\n✅ テスト成功！変更を確定しました。")
                return RunResult(
                    success=True,
                    iterations=iteration,
                    final_output=llm_output,
                    final_test_output=result.output,
                    message="完了",
                    affected_files=validation.affected_files,
                )

            self.progress(f"⚠️  テスト失敗 (試行 {iteration})。再試行します...")
            previous_output = llm_output
            files = self.reader.collect_files(file_hints)  # パッチ適用後のファイルを再読み込み

        return RunResult(
            success=False,
            iterations=self.max_iterations,
            final_output=previous_output or "",
            final_test_output=test_output or "",
            message=f"{self.max_iterations} 回試行しましたが成功しませんでした。",
        )
