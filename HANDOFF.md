# PatchPaw HANDOFF

新セッション開始時に **最初に読むべきファイル**。
PatchPaw の設計と進行状況をセッション間で引き継ぐための運用ガイド。

---

## 1. このファイルの目的

セッション間で文脈を引き継ぐ。個別ファイル upload で発生したディレクトリ
階層誤認の事故 (pawagent プロジェクトで「ルート直下 vs `tools/`」の判別を
取り違えた件) を、構造的に予防する。

---

## 2. 次セッション開始の手順

### 2.1 zip で project knowledge にアップロード

```bash
git archive --format=zip --output=patchpaw.zip HEAD
```

`git archive` を使う利点:
- git 管理下のファイルのみ含まれる (`.env` 等の untracked は自動除外。
  API キー漏洩を仕組みで防げる)
- `.git/` 自体は含まれない
- `sessions/`, `__pycache__/`, `*.egg-info` 等が `.gitignore` で除外
  済みなら対象外

この zip を Claude Projects の Knowledge にアップロードする。
**個別ファイル upload は禁止**。

### 2.2 最初のメッセージのテンプレ

```
PatchPaw の続き。

現在のリポジトリ構造:
$ tree -I "__pycache__|sessions|*.egg-info|*.pyc"
[ここに tree の出力を貼る]

HANDOFF.md、DESIGN.md、TODO.md を全部読んでくれ。
次に何をやるかはこれらのファイルに書いてある。
```

zip だけだと Claude 側の展開挙動が実装依存なので、tree 出力で階層情報を
二重化する。

### 2.3 読むべきファイルの優先順

1. **HANDOFF.md** (このファイル) — 運用と罠と進行状況
2. **DESIGN.md** — 設計原則と全体構造
3. **TODO.md** — 次にやるべきこと
4. **tasks_done.txt** — ドッグフーディングの実証記録
5. (将来作る) **POSTMORTEM.md** — 失敗と学びの記録

---

## 3. 完了確認の規律

タスクを「完了」と宣言する前に **必ず** 以下を全部確認する。
前任セッションの「完了」が嘘になって後任が取りこぼし回収する事故
(pawagent POSTMORTEM v2 参照) を防ぐ。

- [ ] `python -m pytest tests/ -v` が緑
- [ ] `grep -rn <削除/変更対象キーワード> .` で残骸ゼロ
- [ ] DESIGN.md の該当 Section を更新
- [ ] TODO.md の完了マークを付ける (or 該当項目を `tasks_done.txt` へ退避)
- [ ] HANDOFF.md の「5. 進行中のタスク」を更新
- [ ] `git status` で意図しない modified が残ってない
- [ ] `git diff --cached --stat` で意図通りの行数
- [ ] commit message が実態を反映

「コード変更完了」を完了宣言の根拠にしない。
**ドキュメント反映と grep ゼロ確認まで含めて完了。**

---

## 4. PatchPaw 固有の罠

### 4.1 `.patchpaw/context.md` は「常時注入」される

`controller.py` が `<repo_root>/.patchpaw/context.md` を自動で読み込み、
毎回のプロンプトに含める。これは **PatchPaw を使う側 (ユーザーの
プロジェクト) 向け** の機能であって、PatchPaw 自身の開発作業時には
意味を持たない。

リポジトリ内に `context.md` という名前のファイルがあるが、これは別の
プロジェクト用のサンプルテキストである可能性が高い。混同しないこと。
PatchPaw 自身の文脈は **このファイル (HANDOFF.md)** に書く。

### 4.2 selftest config で `allowed_paths` を絞ること (必須)

PatchPaw を PatchPaw 自身に使う (ドッグフーディング) 時、デフォルトの
`config.yaml` は `patchpaw/`, `tests/`, `scripts/` を全部許可しているので
LLM が暴走すると `cli.py`, `controller.py` まで書き換える危険がある。

`config-selftest.yaml` を使い、`allowed_paths` を触っていいファイルだけに
絞る:

```yaml
repository:
  allowed_paths:
    - patchpaw/utils.py
    - tests/test_utils.py
```

これで `DiffValidator` がスコープ外の変更を物理的に弾く。
実行は `PATCHPAW_CONFIG=config-selftest.yaml patchpaw run ...`。

### 4.3 自分自身の脳を開腹手術するな

PatchPaw に `patchpaw/cli.py`, `patchpaw/controller.py`, `patchpaw/patch_applier.py`
等のコアコードを書き換えさせるのは危険。失敗すると次の `patchpaw` コマンド
そのものが死に、復旧手段が `git reset` だけになる。

**コアコード変更は Claude (チャット) が直接書く**こと。
ドッグフーディングは utils.py の docstring 追記みたいな低リスクタスクに
留めるか、変更前に必ず `git tag pre-XXX` を打つ。

### 4.4 LLM の「変更不要」判定で実コード変更が無いことがある

タスクを実行しても LLM が「変更不要と判断しました」を返す場合がある。
これは PatchPaw の冪等性 (`controller.py` の `if not llm_output.strip()` 分岐)
が正しく働いた結果で、`success=True` として記録される。

ただし `git show HEAD --stat` を見ると「0 files changed」になり、
コミットは `--allow-empty` で打たれる (タグも付く)。

動作確認時の判別方法:

```bash
# サマリ JSON の message フィールドを見る
cat sessions/run_*_summary.json | jq '.tasks[].message'

# git show で実変更があったか
git show HEAD --stat
```

「実行ログでは成功だが実コードは何も変わってない」現象に気づかないと、
次のタスクで「直前タスクの成果」が空のまま進む。

### 4.5 `.patchpaw/test-cmd` で test_cmd が暗黙に上書き

`.patchpaw/test-cmd` ファイルがあると、CLI フラグ・環境変数のいずれも
指定されてない場合のデフォルトが、その中身に置き換わる。

```bash
cat .patchpaw/test-cmd
```

意図しないテストコマンドが走る原因になりうる。動作確認時にチェック。

### 4.6 `_find_config` の検索順

`patchpaw/cli.py` の `_find_config` は config を以下の順で探す:

1. `--config` で明示指定されたパス
2. 環境変数 `PATCHPAW_CONFIG`
3. カレントディレクトリの `config.yaml`
4. `~/.patchpaw.yaml`
5. インストール元ディレクトリの `config.yaml`

`config-selftest.yaml` 等のカスタム名は **2 (環境変数) で渡す** か
`--config` 明示が必要。カレントに置いただけでは拾われない。

---

## 5. 進行中のタスクと次セッションへの引き継ぎ

### 5.1 P1: `patchpaw run` サブコマンド — **完了 (2026-05-29)**

- [x] **v1** 移植 (bash 版 `patchpaw-run.sh` の Python 化)
- [x] **v2.1** `--continue-from-task N` (タスク途中再開)
- [x] **v2.3** セッションサマリ JSON 出力 (MVP)
- [x] **v2.2** 直前タスクの変更ファイル引き継ぎ (`--no-carry-context` で opt-out)

### 5.2 次に着手するターゲット

優先度順:

1. **v2.3.x 追加候補**: サマリ JSON に LLM トークン使用量・応答時間・
   適用パッチパスを追加。それぞれコアコード変更 (Controller / llm_adapter /
   SessionManager) を伴うので、段階的に。詳細は TODO.md。
2. **P2 sed 風ブロック**: 同一ファイル内大量箇所変更で SEARCH/REPLACE が
   破綻する問題への対処。設計は TODO.md。
3. **P3 repo-map**: `--files` 未指定時の関連ファイル自動選択。

### 5.3 v2.2 設計 (実装済み、参考記録)

論点 1 (取得方法): **A** — `Controller.RunResult` に `affected_files: list[str]`
追加。Controller 側で `validation.affected_files` を埋める。

論点 2 (伝達方法): **D** — `PromptBuilder.build()` に
`previous_task_changes: list[str] | None = None` 引数追加。
`Controller.run` にも同名引数を追加して伝播。
LLM への明示テキストとして渡す ("## Previous Task's Changes" セクション)。

論点 3 (引き継ぎ範囲): **F** — 直前 1 タスクだけ (累積しない)。
シンプルさとコンテキスト肥大防止のため。

論点 4 (ON/OFF): **K** — デフォルト ON、`--no-carry-context` で opt-out。
環境変数 `CARRY_CONTEXT=0` でも OFF にできる。

---

## 6. このファイル自体の更新ルール

- 新しい罠を踏んだら **4. 罠リスト** に追記
- v2.2 が完了したら **5.1** のチェックを付け、**5.2** を削除
- セッション運用で改善があったら **2 / 3** を更新
- このファイル自体が嘘をつき始めたら、それは前任セッションの「完了宣言」が
  嘘だった証拠。`git log -- HANDOFF.md` で経緯を辿れ。
