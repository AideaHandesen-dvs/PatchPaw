"""
Patch Applier
検証済みの unified diff を git apply で適用する。
root権限・SSH鍵なし。適用失敗時は自動ロールバック。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class PatchError(Exception):
    pass


class PatchApplier:
    def __init__(self, repo_root: str | Path):
        self.root = Path(repo_root).resolve()

    def dry_run(self, diff_text: str) -> tuple[bool, str]:
        """実際には適用せず、成功するか確認する。"""
        return self._run_git_apply(diff_text, check_only=True)

    def apply(self, diff_text: str) -> tuple[bool, str]:
        """diff を適用する。失敗時は git apply --reverse で戻す。"""
        ok, msg = self._run_git_apply(diff_text, check_only=False)
        if not ok:
            # ロールバック試行
            self._run_git_apply(diff_text, check_only=False, reverse=True)
        return ok, msg

    def _run_git_apply(
        self,
        diff_text: str,
        *,
        check_only: bool,
        reverse: bool = False,
    ) -> tuple[bool, str]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False, encoding="utf-8"
        ) as f:
            f.write(diff_text)
            patch_path = f.name
            import shutil; shutil.copy(patch_path, '/tmp/debug.patch')

        cmd = ["git", "apply"]
        if check_only:
            cmd.append("--check")
        if reverse:
            cmd.append("--reverse")
        cmd.append(patch_path)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "git apply タイムアウト"
        except FileNotFoundError:
            return False, "git コマンドが見つかりません"
        finally:
            Path(patch_path).unlink(missing_ok=True)
