# PatchPaw TODO

優先度順に並んでいる。上から着手すること。

DESIGN.md §17 "Future Enhancements" は汎用すぎて優先度の根拠にならないので、
実際の着手対象はここで管理する。

---

## P1: `patchpaw-run.sh` を Python 化 (`patchpaw run` サブコマンド) — **完了 (2026-05-29)**

bash 版 `patchpaw-run.sh` を Python サブコマンド `patchpaw run` に移植。

### 完了内訳
- [x] **v1**: 移植のみ (`patchpaw/runner.py` 新設、`cli.py` に `run`
  サブコマンド追加、環境変数フォールバック、git tag/commit 継続)
- [x] **v2.1**: `--continue-from-task N` でタスク途中再開
- [x] **v2.3**: セッションサマリ JSON 出力 (`sessions/<run_id>_summary.json`)
- [x] **v2.2**: 直前タスクの変更ファイルを次タスクへ引き継ぎ
  (`--no-carry-context` で opt-out 可能)

`patchpaw-run.sh` は後方互換のため残置中。次のクリーンアップで
deprecate を判断する。

### 未着手 (v2.3.x として段階的に追加候補)
サマリ JSON に以下を追加する案。それぞれコアコード変更を伴うので
影響範囲を切り離して段階的に進める。

- LLM トークン使用量
  - `llm_adapter.py` の `OpenAIAdapter` が response の `usage` を読み取り、
    生成テキストと併せて返す API 拡張が必要
  - DeepSeek V4 Flash は OpenAI 互換なので `usage.prompt_tokens` /
    `completion_tokens` を返すはず
- LLM 応答時間 (`Controller.RunResult` に `llm_elapsed_s` フィールド追加)
- 適用 patch ファイルパス (`Controller.RunResult` に
  `patch_files: list[str]` 追加、SessionManager の session_id を経由)

---

## P2: sed 風一括置換ブロック (`DELETE_PATTERN` / `REPLACE_PATTERN`)

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
- **P1 v1**: `patchpaw run` サブコマンド (Python 化) — 2026-05-29
- **P1 v2.1**: `--continue-from-task N` — 2026-05-29
- **P1 v2.3**: セッションサマリ JSON 出力 (MVP) — 2026-05-29
- **P1 v2.2**: 直前タスクの変更ファイル引き継ぎ — 2026-05-29
- **HANDOFF.md** 新設 (セッション間引き継ぎ運用ガイド) — 2026-05-29

完了タスクは `tasks.done.txt` に退避済み (ドッグフーディングの実証記録として残す)。
