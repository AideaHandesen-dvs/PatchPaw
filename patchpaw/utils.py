"""
Utilities — 小さな汎用関数
"""

from __future__ import annotations

import os
import posixpath
from pathlib import Path


def format_duration(seconds: float) -> str:
    """秒数を見やすい形式に変換する。

    60秒未満: "{x:.1f}s" (例: "3.5s")
    60秒以上: "{m}m {s:.0f}s" (例: "1m 30s")
    60秒ちょうどは "1m 0s" になる（境界値）
    (v2.2 連鎖タスク1)
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m {s:.0f}s"


def canonicalize_repo_relative(root: Path, rel_path: str) -> tuple[Path, str]:
    """rel_path を repo root に対して正規化する。

    戻り値: (絶対パス, root からの正規化済み相対パス)。
    `..`、`./`、絶対パス、重複スラッシュ等を解決した後、root の中に
    留まっていることを検証する。root 外に出る場合は ValueError。

    呼び出し側は戻り値の canonical_rel を allowed_paths / denied_patterns
    チェック等に使うことで、`src/../etc/passwd` のようなホワイトリスト
    回避を防げる。生 rel_path を直接 fnmatch / startswith に掛ける実装は
    脆弱なので必ずこれを経由する。

    なぜ独自例外でなく ValueError か:
      utils → 他モジュールの循環依存を避けるため、呼び出し側で catch
      して各モジュール固有の例外 (SecurityError 等) に変換する設計。

    Examples:
      canonicalize_repo_relative(Path("/repo"), "src/main.py")
        → (Path("/repo/src/main.py"), "src/main.py")
      canonicalize_repo_relative(Path("/repo"), "src/../etc/passwd")
        → (Path("/repo/etc/passwd"), "etc/passwd")   # ".." を解決済み
      canonicalize_repo_relative(Path("/repo"), "../outside")
        → ValueError                                  # repo 外
      canonicalize_repo_relative(Path("/repo"), "/etc/passwd")
        → ValueError                                  # 絶対パスで repo 外
    """
    abs_path = (root / rel_path).resolve()
    root_resolved = root.resolve()
    try:
        canonical_rel = str(abs_path.relative_to(root_resolved))
    except ValueError:
        # Python 3.11 までは relative_to が root の prefix 外で ValueError。
        # メッセージを統一して投げ直す。
        raise ValueError(f"repo 外へのパス: {rel_path}")
    return abs_path, canonical_rel


def normalize_relative_path(rel_path: str) -> str | None:
    """rel_path を文字列レベルで正規化する (`..`、`./`、重複スラッシュ等)。

    canonicalize_repo_relative はファイルシステムにアクセスして resolve()
    するが、こちらは文字列処理のみ。symlink は解決できない代わりに、
    FS なしで使える (= DiffValidator のようにファイル存在を仮定できない
    レイヤで使う)。

    戻り値:
      - 正規化された相対パス文字列 (repo 内に留まる場合)
      - None (絶対パス、または `..` で repo 外を指す場合)

    posixpath.normpath を使うので OS 非依存 (`/` セパレータ前提)。

    Examples:
      normalize_relative_path("src/main.py")          → "src/main.py"
      normalize_relative_path("./src/main.py")        → "src/main.py"
      normalize_relative_path("src/../etc/passwd")    → "etc/passwd"
      normalize_relative_path("../outside")           → None
      normalize_relative_path("/etc/passwd")          → None
    """
    # 絶対パスは弾く (POSIX/Windows どちらの絶対形式も)
    if os.path.isabs(rel_path) or rel_path.startswith("/"):
        return None
    norm = posixpath.normpath(rel_path)
    # 先頭 `..` は repo 外を指す
    if norm == ".." or norm.startswith("../"):
        return None
    return norm
