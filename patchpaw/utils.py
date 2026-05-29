"""
Utilities — 小さな汎用関数
"""

from __future__ import annotations


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
