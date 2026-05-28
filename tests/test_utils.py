"""
Utilities — テスト
"""

from patchpaw.utils import format_duration


def test_format_duration_under_1s() -> None:
    """0.5秒 → "0.5s" になる"""
    assert format_duration(0.5) == "0.5s"


def test_format_duration_59_9s() -> None:
    """59.9秒 → "59.9s" になる（60秒未満）"""
    assert format_duration(59.9) == "59.9s"


def test_format_duration_125_3s() -> None:
    """125.3秒 → "2m 5s" になる（60秒以上）"""
    assert format_duration(125.3) == "2m 5s"
