"""
Session Manager
処理履歴・生成diff・テスト結果をJSONLで保存する。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SessionConfig


@dataclass
class SessionEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    iteration: int = 1
    instruction: str = ""
    diff: str = ""
    test_success: bool = False
    test_output: str = ""
    applied: bool = False
    notes: str = ""


class SessionManager:
    def __init__(self, repo_root: str | Path, config: SessionConfig):
        self.storage = Path(repo_root) / config.storage_dir
        self.storage.mkdir(parents=True, exist_ok=True)
        self.session_id: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.storage / f"{self.session_id}.jsonl"
        self.max_history = config.max_history
        self._entries: list[SessionEntry] = []

    def record(self, entry: SessionEntry) -> None:
        self._entries.append(entry)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        self._prune_old_sessions()

    def last_entry(self) -> SessionEntry | None:
        return self._entries[-1] if self._entries else None

    def summary(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._entries]

    def save_diff(self, diff_text: str, label: str = "") -> Path:
        fname = f"{self.session_id}_{label or 'diff'}.patch"
        p = self.storage / fname
        p.write_text(diff_text, encoding="utf-8")
        return p

    def _prune_old_sessions(self) -> None:
        logs = sorted(self.storage.glob("*.jsonl"))
        while len(logs) > self.max_history:
            logs.pop(0).unlink(missing_ok=True)
