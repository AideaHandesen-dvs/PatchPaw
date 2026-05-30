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
from .utils import normalize_relative_path


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
            raw_path = block.file_path
            # `..` や絶対パス等を normalize_relative_path で解決して
            # repo 外なら即拒否。"src/../etc/passwd" のような ホワイトリスト
            # 回避を防ぐため、以降のチェックは正規化済みパスで行う
            # (生 raw_path を fnmatch / startswith に掛ける旧実装は脆弱)。
            norm_path = normalize_relative_path(raw_path)
            if norm_path is None:
                errors.append(
                    f"無効なパス (repo 外を指す、または絶対パス): {raw_path}"
                )
                continue

            # affected_files も正規化後のパスで一意化
            # (LLM が "./src/main.py" と "src/main.py" を別エントリで重複登録
            #  しないように)
            if norm_path not in affected:
                affected.append(norm_path)

            # スコープチェック
            if not self._is_allowed(norm_path):
                errors.append(f"ホワイトリスト外のファイルへの変更: {raw_path}")

            # 危険パターン検査（REPLACE / REPLACE_ALL ブロック内）
            for pattern in DANGEROUS_PATTERNS:
                if re.search(pattern, block.replace):
                    errors.append(
                        f"危険なパターンを検出 ({raw_path}): {pattern}"
                    )

        return ValidationResult(
            ok=len(errors) == 0,
            errors=errors,
            affected_files=affected,
        )

    def _is_allowed(self, path: str) -> bool:
        """path は normalize_relative_path 済みであることを呼び出し側が保証する。

        normalize 済みの前提で、生文字列のすり抜けは考えない。
        """
        for allowed in self.allowed_paths:
            norm = allowed.rstrip("/")
            if path == norm or path.startswith(norm + "/"):
                return True
        return False
