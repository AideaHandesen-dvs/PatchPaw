"""
Patch Applier
SEARCH/REPLACEブロックをパースし、ファイルに直接適用する。

フロー:
  1. LLM出力からFILE/SEARCH/REPLACEブロックをパース
  2. dry_run: 各SEARCHが対象ファイル内で一意に存在するか確認
  3. apply: 置換を実行（失敗時は元に戻す）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# FILE: path\n<<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE
BLOCK_RE = re.compile(
    r"^FILE:\s*(?P<path>.+?)\s*\n"
    r"<<<<<<< SEARCH\n"
    r"(?P<search>.*?)"
    r"=======\n"
    r"(?P<replace>.*?)"
    r">>>>>>> REPLACE",
    re.DOTALL | re.MULTILINE,
)


@dataclass
class EditBlock:
    file_path: str
    search: str
    replace: str


def parse_blocks(text: str) -> list[EditBlock]:
    """LLM出力からEditBlockのリストを抽出する。"""
    return [
        EditBlock(
            file_path=m.group("path").strip(),
            search=m.group("search"),
            replace=m.group("replace"),
        )
        for m in BLOCK_RE.finditer(text)
    ]


class PatchApplier:
    def __init__(self, repo_root: str | Path):
        self.root = Path(repo_root).resolve()

    def dry_run(self, llm_output: str) -> tuple[bool, str]:
        """実際には変更せず、適用できるかだけ確認する。"""
        blocks = parse_blocks(llm_output)
        if not blocks:
            return False, (
                "SEARCH/REPLACEブロックが見つかりません。\n"
                "次の形式で出力してください:\n"
                "FILE: path/to/file.py\n"
                "<<<<<<< SEARCH\n"
                "変更前のコード\n"
                "=======\n"
                "変更後のコード\n"
                ">>>>>>> REPLACE"
            )

        errors = []
        for block in blocks:
            file_path = self.root / block.file_path
            if not file_path.exists():
                errors.append(f"ファイルが存在しません: {block.file_path}")
                continue
            content = file_path.read_text(encoding="utf-8")
            count = content.count(block.search)
            if count == 0:
                preview = block.search[:80].replace("\n", "\\n")
                errors.append(
                    f"SEARCHブロックがファイルに見つかりません: {block.file_path}\n"
                    f"  SEARCH先頭: {preview!r}\n"
                    f"  → SEARCHブロックを元ファイルの内容に正確に合わせてください。"
                )
            elif count > 1:
                errors.append(
                    f"SEARCHブロックが{count}箇所に一致しました（曖昧）: {block.file_path}\n"
                    f"  → 前後の行を含めてSEARCHブロックをより広く取ってください。"
                )

        if errors:
            return False, "\n".join(errors)
        return True, ""

    def apply(self, llm_output: str) -> tuple[bool, str]:
        """SEARCHをREPLACEで置換してファイルに書き込む。失敗時は元に戻す。"""
        blocks = parse_blocks(llm_output)
        if not blocks:
            return False, "SEARCH/REPLACEブロックが見つかりません。"

        # ロールバック用に元の内容を保存
        originals: dict[Path, str] = {}
        for block in blocks:
            p = self.root / block.file_path
            if p not in originals and p.exists():
                originals[p] = p.read_text(encoding="utf-8")

        try:
            for block in blocks:
                file_path = self.root / block.file_path
                content = file_path.read_text(encoding="utf-8")
                count = content.count(block.search)
                if count != 1:
                    raise ValueError(
                        f"適用エラー: {block.file_path} "
                        f"(SEARCHが{count}箇所一致)"
                    )
                new_content = content.replace(block.search, block.replace, 1)
                file_path.write_text(new_content, encoding="utf-8")
            return True, ""
        except Exception as e:
            # ロールバック
            for path, original in originals.items():
                path.write_text(original, encoding="utf-8")
            return False, str(e)
