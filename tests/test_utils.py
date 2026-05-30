"""
Utilities — テスト
"""

import pytest
from pathlib import Path

from patchpaw.utils import (
    canonicalize_repo_relative,
    format_duration,
    normalize_relative_path,
)


def test_format_duration_under_1s() -> None:
    """0.5秒 → "0.5s" になる"""
    assert format_duration(0.5) == "0.5s"


def test_format_duration_59_9s() -> None:
    """59.9秒 → "59.9s" になる（60秒未満）"""
    assert format_duration(59.9) == "59.9s"


def test_format_duration_125_3s() -> None:
    """125.3秒 → "2m 5s" になる（60秒以上）"""
    assert format_duration(125.3) == "2m 5s"


def test_format_duration_zero() -> None:
    """0.0秒 → "0.0s" になる（最小境界値）"""
    assert format_duration(0.0) == "0.0s"


def test_format_duration_zero_int() -> None:
    """整数0 → "0.0s" になる"""
    assert format_duration(0) == "0.0s"


# ────────────────────────────────────────────
# canonicalize_repo_relative — FS レベル正規化
# (3 箇所で共通化漏れだったセキュリティチェックの中核)
# ────────────────────────────────────────────

class TestCanonicalizeRepoRelative:
    def test_plain_relative(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        abs_p, rel = canonicalize_repo_relative(tmp_path, "src/main.py")
        assert rel == "src/main.py"
        assert abs_p == (tmp_path / "src" / "main.py").resolve()

    def test_dotdot_resolves_to_canonical(self, tmp_path):
        """`src/../etc/passwd` → canonical は `etc/passwd` になる。
        呼び出し側はこの canonical を allowed_paths 検査に使うので
        ホワイトリスト回避が防げる。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / "passwd").write_text("x")
        _, rel = canonicalize_repo_relative(tmp_path, "src/../etc/passwd")
        assert rel == "etc/passwd"

    def test_dot_slash_resolved(self, tmp_path):
        """`./src/main.py` → `src/main.py`"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        _, rel = canonicalize_repo_relative(tmp_path, "./src/main.py")
        assert rel == "src/main.py"

    def test_dotdot_outside_repo_raises(self, tmp_path):
        with pytest.raises(ValueError):
            canonicalize_repo_relative(tmp_path, "../outside.txt")

    def test_absolute_path_raises(self, tmp_path):
        with pytest.raises(ValueError):
            canonicalize_repo_relative(tmp_path, "/etc/passwd")


# ────────────────────────────────────────────
# normalize_relative_path — 文字列レベル正規化 (FS 不要)
# ────────────────────────────────────────────

class TestNormalizeRelativePath:
    def test_plain_relative(self):
        assert normalize_relative_path("src/main.py") == "src/main.py"

    def test_dot_slash_resolved(self):
        assert normalize_relative_path("./src/main.py") == "src/main.py"

    def test_dotdot_inside_repo(self):
        """repo 内に留まるなら `..` を解決して通す"""
        assert normalize_relative_path("src/../etc/passwd") == "etc/passwd"

    def test_dotdot_escapes_to_none(self):
        """先頭 `..` で repo 外を指したら None"""
        assert normalize_relative_path("../outside") is None

    def test_dotdot_alone_to_none(self):
        assert normalize_relative_path("..") is None

    def test_absolute_to_none(self):
        assert normalize_relative_path("/etc/passwd") is None

    def test_duplicate_slashes_resolved(self):
        assert normalize_relative_path("src//main.py") == "src/main.py"
