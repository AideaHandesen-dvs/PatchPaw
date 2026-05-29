# PatchPaw TODO

優先度順に並んでいる。上から着手すること。

DESIGN.md §17 "Future Enhancements" は汎用すぎて優先度の根拠にならないので、
実際の着手対象はここで管理する。

---

## P1: `patchpaw-run.sh` を Python 化 (`patchpaw run` サブコマンド)

**選んだ理由 (筆頭にした根拠)**: 他 2 案の基盤になる。タスク間で文脈引き継ぎが
できれば、sed 風ブロックも repo-map も「タスクのまとまり」を単位として
紐付けられる。bash の連鎖だけだと各 `patchpaw fix` 呼び出しが完全に独立し、
前タスクの結果を次タスクに渡せない。

### v1 (移植のみ — まずここまで) [x]
- `patchpaw/runner.py` を新設、`Controller` を順に呼ぶ
- `cli.py` に `run` サブコマンド追加: `patchpaw run tasks.txt --repo .`
- 環境変数 (`STOP_ON_FAIL`, `MAX_ITER`, `COMMIT_PER_TASK`, `DRY_RUN`,
  `PATCHPAW_TEST_CMD`) を CLI フラグに移植
- git tag/commit は `subprocess.run(["git", ...])` で継続
- `patchpaw-run.sh` は当面残す (後方互換)。v2 完了後に deprecate を判断

### v2 (機能追加) [ ]
- タスク間で「直前タスクで変更したファイル」を次タスクのプロンプトに自動注入
- セッションサマリ JSON (累計トークン推定、所要時間、適用パッチ数) を
  `sessions/<session_id>_summary.json` に出力
- タスク失敗時の自己診断 (どのタスクをスキップして次に進むかを LLM に判断させる、
  または `--continue-from-task N` で再開できる)

### 受け入れ条件 [ ]
- `patchpaw run tasks.txt --yes` で現行 `./patchpaw-run.sh tasks.txt` と同等動作
- `tests/test_runner_subcommand.py` でタスクファイルパースとフラグ変換を検証
- README.md にサブコマンド使用例を追加
- 既存テストを壊さない

---

## P2: sed 風一括置換ブロック (`DELETE_PATTERN`)

**目的**: 同一ファイル内の大量箇所変更で SEARCH/REPLACE が破綻する問題の解消。

### 現状の課題
同一ファイル中の同じパターンを 10 箇所書き換えたい場合、LLM は SEARCH/REPLACE
ブロックを 10 個生成しないといけない。周辺コンテキストを変えながら 10 個書く
必要があってトークン浪費 + 失敗率上昇。

### 設計案
新フォーマット (たたき台):

```
FILE: path/to/file.py
<<<<<<< DELETE_PATTERN
^\s*print\(.*\)\s*$
>>>>>>> DELETE
```

または `REPLACE_PATTERN`:

```
FILE: path/to/file.py
<<<<<<< REPLACE_PATTERN
print\((.*)\)
=======
logger.debug(\1)
>>>>>>> REPLACE_PATTERN
```

- 正規表現 vs リテラル文字列はどちらか先に固める。Python `re.MULTILINE` 前提なら
  正規表現で統一が楽。
- ロールバックは現行と同じ機構を流用 (originals dict)。

### 影響範囲
- `patch_applier.py`: `parse_blocks` を拡張、`apply` で正規表現置換を実装
- `diff_validator.py`: 新ブロック認識。`DANGEROUS_PATTERNS` は現在 REPLACE 側に
  しか掛かっていない点に注意 (REPLACE_PATTERN の replacement にも掛ける必要あり)
- `prompt_builder.py`: SYSTEM_PROMPT に新フォーマット説明追加 + 使い分け指示
- `tests/test_patchpaw.py`: 新ブロックのテスト追加

### 受け入れ条件
- 既存 SEARCH/REPLACE フローを壊さない
- ロールバックが効く
- LLM 出力で DELETE_PATTERN 単独・SEARCH/REPLACE と混在のどちらでも動く
- 危険パターン検査が REPLACE_PATTERN の replacement にも適用される

---

## P3: repo-map (ファイル自動選択)

**目的**: `--files` 未指定時、`allowed_paths` 全体ではなく関連ファイルだけを
LLM に渡したい。

### 現状の課題
`--files` を明示しないと `allowed_paths` 全体を LLM に渡す。中規模リポジトリで
急速にコンテキストを食う (DeepSeek V4 Flash の 1M トークンでも長期セッションで
効いてくる)。

### 第一案 (軽量)
ユーザー指示文と各ファイルの単純なキーワードマッチでスコアリングして
上位 N ファイルだけ渡す。

- aider 方式 (tree-sitter でシンボル抽出 → PageRank) は重い。まず簡易版で十分か
  検証する
- フラグ: `--auto-files`、`--files` と排他
- スコア説明を `progress_callback` でログ出力 (なぜそのファイルを選んだか)

### 未設計のまま
P1, P2 を終えてから着手。設計案を出す段階で要相談 (実装着手前にチャットで議論)。

---

## 完了済み (履歴)

- 新規ファイル作成 (SEARCH 空ブロック対応) — 2026-05-29
- `--version` フラグ追加 — 2026-05-29
- LLM 応答時間ログ — 2026-05-29
- `PATCHPAW_QUIET` でバナー抑制 — 2026-05-29
- `.patchpaw/context.md` による常時文脈注入 — 2026-05-29
- `patchpaw-run.sh` (bash 版) — 2026-05-29

完了タスクは `tasks.done.txt` に退避済み (ドッグフーディングの実証記録として残す)。
