"""
Repository Reader
ホワイトリスト方式でリポジトリのファイルを安全に読み取る。
.env / SSH鍵 / .git 内部などは一切読まない。
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator

from .config import Config
from .utils import canonicalize_repo_relative


class SecurityError(Exception):
    """許可されていないファイルへのアクセス試行"""


class RepositoryReader:
    def __init__(self, repo_root: str | Path, config: Config):
        self.root = Path(repo_root).resolve()
        self.allowed_paths: list[str] = config.repository.allowed_paths
        self.denied_patterns: list[str] = config.repository.denied_patterns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        """ホワイトリスト検査後にファイルを返す。"""
        abs_path, canonical_rel = self._resolve(rel_path)
        # `..` や `./` を解決した正規化済み相対パスで検査する。
        # rel_path の生文字列を信用すると "src/../etc/passwd" のような
        # traversal が _check_allowed の startswith("src/") を素通りする。
        self._check_allowed(canonical_rel)
        self._check_denied(canonical_rel)
        return abs_path.read_text(encoding="utf-8", errors="replace")

    def collect_files(self, hints: list[str] | None = None) -> dict[str, str]:
        """
        hints が指定された場合はそのパスのみ、なければ allowed_paths 全体を走査して返す。
        戻り値: {相対パス: ファイル内容}
        """
        paths = hints if hints else self._walk_allowed()
        result: dict[str, str] = {}
        for p in paths:
            try:
                result[p] = self.read_file(p)
            except (SecurityError, FileNotFoundError, IsADirectoryError):
                pass
        return result

    def list_allowed(self) -> list[str]:
        """ホワイトリストに含まれる全ファイルの相対パスを返す。"""
        return list(self._walk_allowed())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, rel_path: str) -> tuple[Path, str]:
        """rel_path を絶対パスに変換しつつ traversal を防ぐ。

        戻り値: (絶対パス, repo_root からの正規化済み相対パス)
        canonical_rel は `..` や `./` を resolve 済みなので、
        以降の allowed/denied 検査で生文字列のすり抜けを防げる。

        実装は utils.canonicalize_repo_relative に集約 (diff_validator,
        patch_applier も同じヘルパーを使うことで 3 箇所での共通化漏れを
        防ぐ)。ValueError を SecurityError にラップして既存呼び出し側の
        例外処理 (collect_files の except) を壊さない。
        """
        try:
            return canonicalize_repo_relative(self.root, rel_path)
        except ValueError as e:
            raise SecurityError(f"パストラバーサル検知: {rel_path}") from e

    def _check_allowed(self, rel_path: str) -> None:
        for pattern in self.allowed_paths:
            if rel_path == pattern or rel_path.startswith(pattern.rstrip("/") + "/"):
                return
            if fnmatch.fnmatch(rel_path, pattern):
                return
        raise SecurityError(f"ホワイトリスト外のファイル: {rel_path}")

    def _check_denied(self, rel_path: str) -> None:
        for pattern in self.denied_patterns:
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                os.path.basename(rel_path), pattern
            ):
                raise SecurityError(f"拒否パターンに一致: {rel_path} ({pattern})")

    def _walk_allowed(self) -> Iterator[str]:
        for allowed in self.allowed_paths:
            target = self.root / allowed
            if target.is_file():
                rel = str(target.relative_to(self.root))
                if not self._is_denied(rel):
                    yield rel
            elif target.is_dir():
                for fpath in sorted(target.rglob("*")):
                    if fpath.is_file():
                        rel = str(fpath.relative_to(self.root))
                        if not self._is_denied(rel):
                            yield rel

    def _is_denied(self, rel_path: str) -> bool:
        for pattern in self.denied_patterns:
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                os.path.basename(rel_path), pattern
            ):
                return True
        return False
