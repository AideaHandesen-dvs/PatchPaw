"""
Diff Validator
LLMが生成したunified diffを検査する。
- フォーマット検証
- 変更範囲がホワイトリスト内か確認
- 危険なパターンの検出
"""

from __future__ import annotations

import re
from dataclasses import dataclass


DANGEROUS_PATTERNS = [
    # シェルコマンド埋め込み
    r"(?m)^\+.*`[^`]+`",
    r"(?m)^\+.*\$\((?!\()",
    # .env アクセス
    r"(?m)^\+.*open\s*\(['\"]\.env",
    r"(?m)^\+.*load_dotenv",
    # ネットワーク操作
    r"(?m)^\+.*import\s+subprocess",
    r"(?m)^\+.*os\.system\s*\(",
    r"(?m)^\+.*os\.popen\s*\(",
]

HEADER_RE = re.compile(
    r"^--- (?:a/)?(.+?)\n\+\+\+ (?:b/)?(.+?)\n", re.MULTILINE
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    affected_files: list[str]


class DiffValidator:
    def __init__(self, allowed_paths: list[str]):
        self.allowed_paths = allowed_paths

    def validate(self, diff_text: str) -> ValidationResult:
        errors: list[str] = []
        affected: list[str] = []

        if not diff_text.strip():
            return ValidationResult(ok=False, errors=["diff が空です。"], affected_files=[])

        # ---- ヘッダー解析 ----
        headers = HEADER_RE.findall(diff_text)
        if not headers:
            errors.append("unified diff ヘッダー (--- / +++) が見つかりません。")
        else:
            for src, dst in headers:
                path = dst.lstrip("/")
                affected.append(path)
                if not self._is_allowed(path):
                    errors.append(f"ホワイトリスト外のファイルへの変更: {path}")

        # ---- hunk ヘッダー存在確認 ----
        if headers and not HUNK_RE.search(diff_text):
            errors.append("@@ hunk ヘッダーが見つかりません。")

        # ---- 危険パターン検査 ----
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, diff_text):
                errors.append(f"危険なパターンを検出: {pattern}")

        # ---- 行形式の簡易チェック ----
        for i, line in enumerate(diff_text.splitlines(), 1):
            if line and line[0] not in ("+", "-", " ", "@", "\\", "#", "d"):
                # diff ヘッダー行か確認
                if not (line.startswith("---") or line.startswith("+++")):
                    errors.append(f"不正な行 {i}: {line[:60]!r}")
                    break

        return ValidationResult(ok=len(errors) == 0, errors=errors, affected_files=affected)

    def _is_allowed(self, path: str) -> bool:
        for allowed in self.allowed_paths:
            norm = allowed.rstrip("/")
            if path == norm or path.startswith(norm + "/") or path == allowed:
                return True
        return False
