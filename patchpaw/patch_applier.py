"""
Patch Applier
SEARCH/REPLACEブロックをパースし、ファイルに直接適用する。

サポートする2形式:
  - SEARCH / REPLACE: 一意一致を要求 (count == 1)。曖昧なら停止
  - SEARCH_ALL / REPLACE_ALL: 同一リテラル文字列を全箇所置換 (count >= 1)。
    同一ファイル内の大量箇所変更で SEARCH/REPLACE が破綻するケース用。
    正規表現ではなくリテラル一致。

フロー:
  1. LLM出力からFILE/SEARCH/REPLACEブロックをパース (両モード混在可)
  2. dry_run: 各SEARCHが対象ファイル内で適切に存在するか確認
             SEARCH (空) は新規ファイル作成として扱う
             SEARCH_ALL は 0 箇所一致でエラー、1+ 箇所で OK
  3. apply: 置換を実行（失敗時は元に戻す）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# FILE: path\n<<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE
# path 部分は改行を含まない (DOTALL の下で `.+?` が改行を吸わないように
# `[^\n]+?` を使う)。
BLOCK_UNIQUE_RE = re.compile(
    r"^FILE:\s*(?P<path>[^\n]+?)\s*\n"
    r"<<<<<<< SEARCH\n"
    r"(?P<search>.*?)"
    r"=======\n"
    r"(?P<replace>.*?)"
    r">>>>>>> REPLACE\b",
    re.DOTALL | re.MULTILINE,
)

# FILE: path\n<<<<<<< SEARCH_ALL\n...\n=======\n...\n>>>>>>> REPLACE_ALL
BLOCK_ALL_RE = re.compile(
    r"^FILE:\s*(?P<path>[^\n]+?)\s*\n"
    r"<<<<<<< SEARCH_ALL\n"
    r"(?P<search>.*?)"
    r"=======\n"
    r"(?P<replace>.*?)"
    r">>>>>>> REPLACE_ALL",
    re.DOTALL | re.MULTILINE,
)


@dataclass
class EditBlock:
    file_path: str
    search: str
    replace: str
    # "unique" = SEARCH/REPLACE (count == 1 を要求、行ブロック単位の規約)
    # "all"    = SEARCH_ALL/REPLACE_ALL (リテラル部分文字列の全箇所置換)
    mode: str = "unique"


def _strip_trailing_newline(s: str) -> str:
    """ブロック区切りの末尾改行を 1 個だけ削る (SEARCH_ALL 用)。

    SEARCH_ALL では「リテラル部分文字列」を全置換したい。LLM が
    `old_name` と書くとパーサは区切り改行込みで `"old_name\\n"` を拾う
    ため、識別子マッチ (e.g. `use(old_name)`) で 0 件になってしまう。
    末尾 `\\n` を 1 個削れば「リテラル文字列」として期待通り動く。

    複数行の SEARCH_ALL も改行 1 個削るだけなので影響なし
    (`"print(x)\\nprint(y)"` のような複数行リテラルは保たれる)。
    """
    return s[:-1] if s.endswith("\n") else s


def parse_blocks(text: str) -> list[EditBlock]:
    """LLM出力からEditBlockのリストを抽出する。

    SEARCH/REPLACE と SEARCH_ALL/REPLACE_ALL の両モードを拾い、
    出力テキスト内の出現位置順に並べる (apply 順を保つため)。
    SEARCH_ALL の search/replace は末尾改行を 1 個 trim する。
    """
    matches: list[tuple[int, EditBlock]] = []
    for m in BLOCK_UNIQUE_RE.finditer(text):
        matches.append((m.start(), EditBlock(
            file_path=m.group("path").strip(),
            search=m.group("search"),
            replace=m.group("replace"),
            mode="unique",
        )))
    for m in BLOCK_ALL_RE.finditer(text):
        matches.append((m.start(), EditBlock(
            file_path=m.group("path").strip(),
            search=_strip_trailing_newline(m.group("search")),
            replace=_strip_trailing_newline(m.group("replace")),
            mode="all",
        )))
    matches.sort(key=lambda t: t[0])
    return [b for _, b in matches]


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
                "変更前のコード (新規作成の場合は空欄)\n"
                "=======\n"
                "変更後のコード\n"
                ">>>>>>> REPLACE"
            )

        errors = []
        for block in blocks:
            file_path = self.root / block.file_path

            # ───── SEARCH_ALL モード (リテラル全箇所置換) ─────
            if block.mode == "all":
                if block.search == "":
                    errors.append(
                        f"SEARCH_ALL の中身が空です: {block.file_path}\n"
                        f"  → 新規ファイル作成は SEARCH (空 SEARCH ブロック) を使ってください。"
                    )
                    continue
                if not file_path.exists():
                    errors.append(f"ファイルが存在しません: {block.file_path}")
                    continue
                content = file_path.read_text(encoding="utf-8")
                count = content.count(block.search)
                if count == 0:
                    preview = block.search[:80].replace("\n", "\\n")
                    errors.append(
                        f"SEARCH_ALL ブロックがファイルに見つかりません: {block.file_path}\n"
                        f"  SEARCH_ALL 先頭: {preview!r}\n"
                        f"  → SEARCH_ALL は実在するリテラル文字列を指定してください。"
                    )
                # count >= 1 は OK (1 箇所だけマッチでも通す)
                continue

            # ───── SEARCH モード (一意一致を要求) ─────
            # SEARCHが空 = 新規ファイル作成
            if block.search == "":
                if file_path.exists():
                    errors.append(
                        f"新規作成しようとしましたが既に存在します: {block.file_path}\n"
                        f"  → 既存ファイルを変更する場合は SEARCH に変更前コードを書いてください。"
                    )
                continue

            # 既存ファイルへの変更
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
                    f"  → 前後の行を含めてSEARCHブロックをより広く取るか、"
                    f"全箇所変更が意図ならSEARCH_ALL/REPLACE_ALLを使ってください。"
                )

        if errors:
            return False, "\n".join(errors)
        return True, ""

    def apply(self, llm_output: str) -> tuple[bool, str]:
        """SEARCHをREPLACEで置換してファイルに書き込む。失敗時は元に戻す。"""
        blocks = parse_blocks(llm_output)
        if not blocks:
            return False, "SEARCH/REPLACEブロックが見つかりません。"

        # ロールバック用に元の状態を保存
        # str   → 既存ファイル（ロールバック時に内容を戻す）
        # None  → 新規作成（ロールバック時にファイルを削除）
        originals: dict[Path, str | None] = {}
        for block in blocks:
            p = self.root / block.file_path
            if p not in originals:
                originals[p] = p.read_text(encoding="utf-8") if p.exists() else None

        try:
            for block in blocks:
                file_path = self.root / block.file_path

                # ───── SEARCH_ALL モード (リテラル全箇所置換) ─────
                if block.mode == "all":
                    if block.search == "":
                        raise ValueError(
                            f"SEARCH_ALL の中身が空です: {block.file_path}"
                        )
                    if not file_path.exists():
                        raise ValueError(
                            f"ファイルが存在しません: {block.file_path}"
                        )
                    content = file_path.read_text(encoding="utf-8")
                    count = content.count(block.search)
                    if count == 0:
                        raise ValueError(
                            f"適用エラー: {block.file_path} "
                            f"(SEARCH_ALL が 0 箇所一致)"
                        )
                    # 全置換 (count 引数省略でデフォルト全置換)
                    new_content = content.replace(block.search, block.replace)
                    file_path.write_text(new_content, encoding="utf-8")
                    continue

                # ───── SEARCH モード (一意一致を要求) ─────
                if block.search == "":
                    # 新規ファイル作成
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(block.replace, encoding="utf-8")
                    continue

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
                if original is None:
                    # 新規作成したファイルを削除
                    if path.exists():
                        path.unlink()
                else:
                    path.write_text(original, encoding="utf-8")
            return False, str(e)
