# PatchPaw プロジェクト文脈

## 概要
PatchPaw はセキュアなローカル AI コーディングアシスタント。
LLM を「パッチジェネレーター」として扱い、SEARCH/REPLACE ブロックのみを出力させる。
LLM にシェルアクセス・ファイル操作権限・ネットワーク権限は一切与えない。

## アーキテクチャ
```
User → CLI (cli.py)
         → Controller (controller.py)
              ├─ RepositoryReader   : ファイル収集 (allowed_paths/denied_patterns)
              ├─ PromptBuilder      : LLM プロンプト構築 + 常時文脈注入
              ├─ LLM Adapter        : Ollama / OpenAI 互換 API 通信
              ├─ DiffValidator      : SEARCH/REPLACE ブロック検証
              ├─ PatchApplier       : パッチ適用 + ロールバック + 新規ファイル作成
              ├─ TestRunner         : Docker or ローカルテスト実行
              └─ SessionManager     : セッションログ (.jsonl) + パッチ保存 (.patch)
```

## ディレクトリ構成
```
patchpaw/
├── patchpaw/
│   ├── __init__.py
│   ├── cli.py              # CLI エントリーポイント, config 自動検索
│   ├── config.py            # config.yaml パーサー
│   ├── controller.py        # メインループ, 常時文脈読み込み
│   ├── diff_validator.py    # SEARCH/REPLACE 検証
│   ├── llm_adapter.py       # Ollama / OpenAI アダプター
│   ├── patch_applier.py     # パッチ適用, 新規ファイル作成, ロールバック
│   ├── prompt_builder.py    # プロンプト構築, project_context 注入
│   ├── repository_reader.py # ファイル収集
│   ├── session_manager.py   # セッション永続化
│   └── test_runner.py       # テスト実行
├── tests/
│   └── test_patchpaw.py     # ユニットテスト
├── scripts/
│   └── patchpaw-run.sh      # タスク連鎖ランナー
├── config.yaml              # デフォルト設定
├── pyproject.toml
├── DESIGN.md
└── README.md
```

## コーディング規約
- Python 3.11+, 型ヒント必須 (from __future__ import annotations)
- dataclass を積極的に使う
- 外部依存は最小限 (標準ライブラリ + pyyaml のみ)
- urllib.request を使う (requests は依存に入れない)
- テストは tests/test_patchpaw.py に追加, pytest で実行
- エラーメッセージは日本語
- docstring はモジュール先頭に書く

## セキュリティ原則
- LLM はテキスト入出力のみ (信頼されないコンポーネント)
- 全ての副作用は Controller が検証してから実行
- ファイルアクセスは allowed_paths で制限
- パッチ適用は失敗時に自動ロールバック

## 次の改善テーマ (優先度順)

1. sed風の一括置換ブロック (DELETE_PATTERN 等) を追加
   - 動機: 同一ファイル多数箇所の機械的変更が SEARCH/REPLACE では出力上限に当たる
   - PawAgent の test_safety.py 改造で実証された限界

2. patchpaw-run.sh を Python 化して `patchpaw run tasks.txt` サブコマンド化
   - 動機: タスク間の文脈引き継ぎ (前タスクで変更したファイルを次タスクに自動で渡す)

3. repo-map (関連ファイル自動選択)
   - 動機: 大きいプロジェクトで allowed_paths 全体を読み込むとコンテキストが膨らむ
