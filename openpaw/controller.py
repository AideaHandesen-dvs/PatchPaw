"""
Controller
全体の状態管理と処理フロー制御。

フロー:
  1. ユーザー指示受け取り
  2. 関連ファイル収集
  3. プロンプト生成
  4. LLM で diff 生成
  5. diff 検証
  6. ユーザー承認
  7. パッチ適用
  8. テスト実行
  9. 成功 or 再試行 (最大 max_iterations)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
    final_diff: str
    final_test_output: str
    message: str


class Controller:
    def __init__(
        self,
        repo_root: str | Path,
        config: Config,
        *,
        max_iterations: int = 5,
        # コールバック: diff テキストを受け取り True=承認 / False=拒否
        approval_callback: Callable[[str], bool] | None = None,
        # 進捗表示コールバック
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

    def run(
        self,
        instruction: str,
        file_hints: list[str] | None = None,
        test_command: str = "python -m pytest tests/ -v --tb=short",
    ) -> RunResult:
        self.progress(f"📂 ファイル収集中 ({'指定ファイル' if file_hints else 'ホワイトリスト全体'})...")
        files = self.reader.collect_files(file_hints)
        if not files:
            return RunResult(
                success=False,
                iterations=0,
                final_diff="",
                final_test_output="",
                message="読み取れるファイルが見つかりませんでした。config.yaml の allowed_paths を確認してください。",
            )
        self.progress(f"   {len(files)} ファイル読み込み完了")

        previous_diff: str | None = None
        test_output: str | None = None

        for iteration in range(1, self.max_iterations + 1):
            self.progress(f"\n🤖 LLM に diff 生成依頼 (試行 {iteration}/{self.max_iterations})...")
            messages = self.prompt_builder.build(
                instruction=instruction,
                file_contents=files,
                test_result=test_output,
                previous_diff=previous_diff,
                iteration=iteration,
            )

            try:
                diff_text = self.llm.generate(messages)
            except Exception as e:
                return RunResult(
                    success=False,
                    iterations=iteration,
                    final_diff="",
                    final_test_output=test_output or "",
                    message=f"LLM エラー: {e}",
                )

            if not diff_text.strip():
                return RunResult(
                    success=True,
                    iterations=iteration,
                    final_diff="",
                    final_test_output="",
                    message="LLM: 変更不要と判断しました。",
                )

            # ---- diff 検証 ----
            self.progress("🔍 diff を検証中...")
            validation = self.validator.validate(diff_text)
            if not validation.ok:
                self.progress("❌ diff 検証エラー:")
                for err in validation.errors:
                    self.progress(f"   • {err}")
                test_output = "diff validation failed:\n" + "\n".join(validation.errors)
                previous_diff = diff_text
                continue

            self.progress(f"   対象ファイル: {', '.join(validation.affected_files)}")

            # ---- dry-run ----
            ok, dry_msg = self.applier.dry_run(diff_text)
            if not ok:
                self.progress(f"❌ git apply --check 失敗: {dry_msg}")
                test_output = f"git apply --check failed:\n{dry_msg}"
                previous_diff = diff_text
                continue

            # ---- ユーザー承認 ----
            self.progress("\n" + "─" * 60)
            self.progress(diff_text)
            self.progress("─" * 60)
            if not self.approval_cb(diff_text):
                return RunResult(
                    success=False,
                    iterations=iteration,
                    final_diff=diff_text,
                    final_test_output="",
                    message="ユーザーが変更を拒否しました。",
                )

            # ---- パッチ適用 ----
            self.progress("📝 パッチを適用中...")
            applied, apply_msg = self.applier.apply(diff_text)
            if not applied:
                self.progress(f"❌ パッチ適用失敗: {apply_msg}")
                test_output = f"patch apply failed:\n{apply_msg}"
                previous_diff = diff_text
                continue

            patch_path = self.session.save_diff(diff_text, label=f"iter{iteration}")
            self.progress(f"   パッチ保存: {patch_path}")

            # ---- テスト実行 ----
            self.progress("🧪 テスト実行中...")
            result = self.test_runner.run(test_command)
            test_output = result.output
            self.progress(result.output[:2000])

            entry = SessionEntry(
                iteration=iteration,
                instruction=instruction,
                diff=diff_text,
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
                    final_diff=diff_text,
                    final_test_output=result.output,
                    message="完了",
                )

            self.progress(f"⚠️  テスト失敗 (試行 {iteration})。再試行します...")
            previous_diff = diff_text

        return RunResult(
            success=False,
            iterations=self.max_iterations,
            final_diff=previous_diff or "",
            final_test_output=test_output or "",
            message=f"{self.max_iterations} 回試行しましたが成功しませんでした。",
        )
