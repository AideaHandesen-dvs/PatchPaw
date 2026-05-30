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

### v2.3.x (サマリ JSON 拡張) — **完了 (2026-05-29)**

サマリ JSON への 3 種の追加。コアコード変更を伴うので影響範囲を切り離して
段階的に進めた。

- [x] **LLM トークン使用量** — 2026-05-29
  - `llm_adapter.py` に `GenerateResult` dataclass 新設。
    `OpenAIAdapter` が response の `usage` を、`OllamaAdapter` が
    `prompt_eval_count` / `eval_count` を抽出
  - `Controller.RunResult` に `prompt_tokens` / `completion_tokens` /
    `total_tokens` 追加 (iteration 累積)
  - `TaskRunner` の `TaskResult` と `summary.json` に `tokens` /
    `tokens_total` セクション
- [x] **LLM 応答時間** — 2026-05-29
  - `Controller.RunResult` に `llm_elapsed_s: float` 追加 (iteration 累積)
  - `TaskResult` と `summary.json` に `llm_elapsed_s` / `llm_elapsed_total_s`
  - `duration_s` (タスク全体の wall-clock) との差がテスト実行+承認待ち時間
- [x] **適用 patch ファイルパス** — 2026-05-29
  - `Controller.RunResult` に `patch_files: list[str]` 追加。
    SessionManager の `save_diff()` の戻り値を `repo_root` 相対パスに
    変換して iteration 順に蓄積。apply 失敗 iteration は save_diff
    を呼ばないので入らない (=「実際に適用された patch」のみ)
  - `TaskResult` と `summary.json` に `patch_files` / `patches_total`

---

## P2: 一括置換ブロック (`SEARCH_ALL` / `REPLACE_ALL`) — **完了 (2026-05-29)**

**目的**: 同一ファイル内の大量箇所変更で SEARCH/REPLACE が破綻する問題の解消。

### 現状の課題 (解決済み)
同一ファイル中の同じ文字列を 10 箇所書き換えたい場合、LLM は SEARCH/REPLACE
ブロックを 10 個生成しないといけなかった。周辺コンテキストを変えながら
10 個書く必要があってトークン浪費 + 失敗率上昇。

### 実装した仕様
正規表現は導入せず、**リテラル部分文字列の全置換**ブロックを追加した。
正規表現案 (元のたたき台 `DELETE_PATTERN` / `REPLACE_PATTERN`) は却下:
- 大半のユースケース (リネーム、定数置換、import 整理) はリテラルで足りる
- 正規表現は `DANGEROUS_PATTERNS` の検査をバックリファレンスで回避される
  可能性、LLM の誤った正規表現出力リスク等で副作用が大きい
- 必要になったら段階的に後追いで足せる

フォーマット:

```
FILE: path/to/file.py
<<<<<<< SEARCH_ALL
old_name
=======
new_name
>>>>>>> REPLACE_ALL
```

セマンティクス:
- ブロック内の文字列をファイル中で**リテラル部分文字列マッチ**して全置換
- ブロック区切りの末尾改行はパーサが trim する (1 個だけ)。
  `use(old_name)` のような行内識別子もマッチする
- 複数行リテラルもサポート (内部の改行は保たれ、末尾改行のみ trim)
- 0 箇所マッチはエラー (タイポ防止)、1 箇所マッチは成功
- 空 `SEARCH_ALL` はエラー (新規作成は SEARCH を使う)

### 実装ファイル
- `patch_applier.py`: `BLOCK_UNIQUE_RE` + `BLOCK_ALL_RE` の 2 つに分け、
  `EditBlock.mode: str = "unique"` 追加。`parse_blocks` で両モード拾って
  位置順ソート。`dry_run` / `apply` で mode 分岐
- `prompt_builder.py`: SYSTEM_PROMPT に SEARCH_ALL 用フォーマット説明追加、
  使い分けルール明記
- `diff_validator.py`: **触っていない**。`DANGEROUS_PATTERNS` は
  `block.replace` に対して走り、REPLACE_ALL の中身も `block.replace` に
  入るので自動適用された
- `tests/test_patchpaw.py`: `TestParseBlocks`, `TestPatchApplierUnique`,
  `TestPatchApplierSearchAll`, `TestPatchApplierMixed`,
  `TestDiffValidatorWithSearchAll` の 5 クラスを新規追加 (計 18 テスト)

### 副次的に判明した既存バグ
旧 `BLOCK_RE` のパス部分 `.+?` が `re.DOTALL` 下で改行を吸い、複数ブロックを
跨いでマッチする問題があった (PatchApplier の直接テストが無かったため
気づかれていなかった)。`[^\n]+?` に修正。

### 受け入れ条件 (達成済み)
- [x] 既存 SEARCH/REPLACE フローを壊さない
- [x] ロールバックが効く (混在ブロックでも片方失敗で全ファイル復元)
- [x] LLM 出力で SEARCH_ALL 単独・SEARCH/REPLACE と混在のどちらでも動く
- [x] 危険パターン検査が REPLACE_ALL の中身にも適用される

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
- **P1 v2.3.x**: サマリ JSON に LLM トークン使用量を追加
  (`GenerateResult` 新設、`RunResult` / `TaskResult` 拡張) — 2026-05-29
- **P1 v2.3.x**: サマリ JSON に LLM 応答時間を追加
  (`RunResult.llm_elapsed_s`, `summary.llm_elapsed_total_s`) — 2026-05-29
- **P1 v2.3.x**: サマリ JSON に適用 patch ファイルパスを追加
  (`RunResult.patch_files`, `summary.patches_total`) — 2026-05-29
- **P2**: `SEARCH_ALL` / `REPLACE_ALL` ブロック (リテラル部分文字列の
  全箇所置換)。`patch_applier.py` の `BLOCK_RE` を 2 つに分割、
  `EditBlock.mode` 追加、`parse_blocks` で両モード位置順ソート、
  `dry_run`/`apply` で mode 分岐。SYSTEM_PROMPT 更新。
  PatchApplier 直接テスト 18 件を新規追加 (元 0 件)。
  副次的に旧 BLOCK_RE のパス改行吸い込みバグを修正 — 2026-05-29
- **セキュリティ修正**: `src/../etc/passwd` のような ホワイトリスト回避
  バグを `repository_reader` / `diff_validator` / `patch_applier` の
  3 箇所で同時修正。`utils.canonicalize_repo_relative` (FS レベル) と
  `utils.normalize_relative_path` (文字列レベル) の 2 ヘルパーに共通化、
  生 rel_path を直接 fnmatch / startswith に掛ける旧実装を排除。
  回帰テスト 16 件追加 — 2026-05-29
- **HANDOFF.md** 新設 (セッション間引き継ぎ運用ガイド) — 2026-05-29

完了タスクは `tasks.done.txt` に退避済み (ドッグフーディングの実証記録として残す)。
