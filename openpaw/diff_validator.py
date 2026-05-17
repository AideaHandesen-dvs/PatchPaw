"""
Diff Validator
LLMが生成したSEARCH/REPLACEブロックを検査する。
- 変更範囲がホワイトリスト内か確認
- 危険なパターンの検出（REPLACEブロック内）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .patch_applier import parse_blocks


# REPLACEブロック内で検出すべき危険パターン（行番号プレフィックスなし）
DANGEROUS_PATTERNS = [
    r"`[^`]+`",                    # バッククォート実行
    r"\$\((?!\()",                 # $()実行
    r"open\s*\(['\"]\.env",        # .envアクセス
    r"load_dotenv",                # dotenv読み込み
    r"import\s+subprocess",        # subprocess
    r"os\.system\s*\(",            # os.system
    r"os\.popen\s*\(",             # os.popen
]


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    affected_files: list[str]


class DiffValidator:
    def __init__(self, allowed_paths: list[str]):
        self.allowed_paths = allowed_paths

    def validate(self, llm_output: str) -> ValidationResult:
        errors: list[str] = []
        affected: list[str] = []

        if not llm_output.strip():
            return ValidationResult(ok=False, errors=["出力が空です。"], affected_files=[])

        blocks = parse_blocks(llm_output)
        if not blocks:
            errors.append(
                "SEARCH/REPLACEブロックが見つかりません。"
                "FILE:/<<<<<<< SEARCH/=======/>>>>>>> REPLACE 形式で出力してください。"
            )
            return ValidationResult(ok=False, errors=errors, affected_files=[])

        for block in blocks:
            path = block.file_path
            if path not in affected:
                affected.append(path)

            # スコープチェック
            if not self._is_allowed(path):
                errors.append(f"ホワイトリスト外のファイルへの変更: {path}")

            # 危険パターン検査（REPLACEブロック内）
            for pattern in DANGEROUS_PATTERNS:
                if re.search(pattern, block.replace):
                    errors.append(
                        f"危険なパターンを検出 ({path}): {pattern}"
                    )

        return ValidationResult(
            ok=len(errors) == 0,
            errors=errors,
            affected_files=affected,
        )

    def _is_allowed(self, path: str) -> bool:
        for allowed in self.allowed_paths:
            norm = allowed.rstrip("/")
            if path == norm or path.startswith(norm + "/") or path == allowed:
                return True
        return False
