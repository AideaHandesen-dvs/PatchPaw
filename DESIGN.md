# PatchPaw Design Document

Version: 0.1.0
Status: Draft

---

# 1. Overview

PatchPaw is a secure local AI coding assistant inspired by OpenClaw.

Unlike agent systems that are granted direct control over the host environment, PatchPaw treats the Large Language Model (LLM) as a pure patch generator.

The LLM receives:

- User instructions
- Relevant source files
- Test results

And returns:

- SEARCH/REPLACE blocks

The LLM never receives:

- SSH keys
- Root privileges
- Arbitrary shell access
- Direct file system control

This design minimizes security risks while preserving most of the practical value of AI-assisted software development.

---

# 2. Design Principles

## 2.1 Patch-Only Architecture

The LLM is restricted to generating SEARCH/REPLACE blocks.

## 2.2 Human-in-the-Loop

All generated patches require explicit user approval before being applied.

## 2.3 Least Privilege

The system grants only the minimum permissions necessary.

## 2.4 Local-First

All code and prompts can be processed locally using models such as Ollama.

## 2.5 Reproducibility

Every action is recorded and can be replayed.

## 2.6 Auditability

All changes are stored as SEARCH/REPLACE patch files alongside JSONL session logs.

---

# 3. High-Level Architecture

```text
User
  ↓
CLI / UI
  ↓
Controller
  ├─ Repository Reader
  ├─ Prompt Builder
  ├─ LLM Adapter
  ├─ Diff Validator
  ├─ Patch Applier
  ├─ Test Runner
  └─ Session Manager
```

---

# 4. Core Concept

The LLM is treated as a deterministic transformation:

```
Patch = LLM(
    user_request,
    relevant_files,
    test_results,
    project_metadata
)
```

The LLM produces text only.

All execution is performed by trusted controller components.

---

# 5. Functional Requirements

## 5.1 Repository Analysis

- Read project files
- Select relevant files
- Respect allowed path restrictions

## 5.2 Prompt Construction

- Build prompts with instructions and context
- Enforce SEARCH/REPLACE output format

## 5.3 Patch Generation

- Request SEARCH/REPLACE block output only

## 5.4 Patch Validation

- Validate block syntax
- Restrict modified paths to whitelist
- Detect dangerous patterns in REPLACE blocks

## 5.5 Patch Application

- Apply patches via string replacement with automatic rollback on failure

## 5.6 Test Execution

- Run tests in isolated environments

## 5.7 Iterative Repair

- Retry based on test failures

## 5.8 Session Persistence

- Store prompts, patches, logs, and metadata

---

# 6. Non-Functional Requirements

## Security

- No direct shell access for the LLM
- No root privileges
- No SSH keys
- Restricted file access via whitelist
- Optional network isolation

## Reliability

- Rollback on failure
- Timeout enforcement
- Resource limits

## Portability

- Linux first
- Compatible with Docker

## Extensibility

- Pluggable LLM backends
- Configurable test runners

---

# 7. Module Design

## 7.1 Controller

Coordinates the complete workflow.

## 7.2 Repository Reader

Extracts relevant project files. Enforces whitelist (`allowed_paths`) and denylist (`denied_patterns`). Blocks path traversal.

## 7.3 Prompt Builder

Constructs LLM prompts. Enforces SEARCH/REPLACE output format via system prompt.

## 7.4 LLM Adapter

Communicates with providers.

Supported backends:

- Ollama
- OpenAI-compatible APIs (including DeepSeek)

## 7.5 Diff Validator

Checks generated SEARCH/REPLACE blocks for format errors, dangerous patterns, and out-of-scope file paths.

## 7.6 Patch Applier

Applies validated patches via string replacement. Saves originals for rollback on failure.

## 7.7 Test Runner

Executes tests in Docker sandboxes. Falls back to local execution when Docker is unavailable.

## 7.8 Session Manager

Stores `.patch` and `.jsonl` artifacts per session.

---

# 8. Workflow

```
1.  User submits request
2.  Repository Reader selects files
3.  Prompt Builder constructs prompt
4.  LLM Adapter requests SEARCH/REPLACE blocks
5.  Diff Validator checks output
6.  Patch Applier performs dry-run
7.  User reviews patch
8.  Patch Applier applies patch
9.  Test Runner executes tests
10. If tests fail, feed results back to LLM
11. Repeat until success or retry limit reached
```

---

# 9. Security Model

## Trusted Components

- Controller
- Patch Applier
- Test Runner
- Session Manager

## Untrusted Component

- LLM output

## Security Boundary

The LLM can only emit text.

The controller validates and constrains all side effects.

---

# 10. Sandbox Strategy

Recommended hierarchy:

```
Host OS
 └─ Docker Container
     └─ PatchPaw (--network=none, --user=1000:1000, --read-only)
```

---

# 11. Directory Structure

```
patchpaw/
├── patchpaw/
│   ├── __init__.py
│   ├── cli.py
│   ├── controller.py
│   ├── repository_reader.py
│   ├── prompt_builder.py
│   ├── llm_adapter.py
│   ├── diff_validator.py
│   ├── patch_applier.py
│   ├── test_runner.py
│   ├── session_manager.py
│   ├── runner.py            # P1: タスクファイルを順次実行する TaskRunner
│   └── config.py
├── tests/
│   ├── test_patchpaw.py
│   ├── test_runner_subcommand.py
│   └── test_utils.py
├── config.yaml
├── config-selftest.yaml     # ドッグフーディング時の allowed_paths 絞り込み用
├── pyproject.toml
├── README.md
├── DESIGN.md
├── HANDOFF.md               # セッション間引き継ぎ運用ガイド
├── TODO.md
├── logo.png
└── .env.example
```

---

# 12. Configuration

```yaml
llm:
  provider: ollama          # "ollama" or "openai"
  model: qwen3:8b
  base_url: http://localhost:11434
  api_key_env: OPENAI_API_KEY
  max_tokens: 4096
  temperature: 0.2

sandbox:
  docker_image: python:3.12-slim
  network_disabled: true
  timeout_seconds: 300
  memory_limit: 512m

repository:
  allowed_paths:
    - src/
    - tests/
    - README.md
  denied_patterns:
    - "*.env"
    - ".env*"
    - "**/.git/**"
    - "*.key"
    - "*.pem"

session:
  storage_dir: sessions/
  max_history: 20
```

---

# 13. CLI Design

```bash
# バグ修正
patchpaw fix "src/calculator.py の除算でゼロ除算エラーが出る。修正して" --repo ./myproject

# テスト追加
patchpaw fix "src/parser.py の単体テストを tests/ に追加して" \
  --repo ./myproject \
  --files src/parser.py

# 許可ファイル一覧の確認
patchpaw list-files --repo ./myproject

# 承認をスキップ（CI用）
patchpaw fix "..." --repo ./myproject --yes

# タスクファイルから複数タスクを順次実行 (P1 v1〜v2.2)
patchpaw run tasks.txt --repo ./myproject

# 途中から再開 (P1 v2.1)
patchpaw run tasks.txt --repo ./myproject --continue-from-task 3

# タスク間の文脈引き継ぎを無効化 (P1 v2.2)
patchpaw run tasks.txt --repo ./myproject --no-carry-context
```

## 13.1 `patchpaw run` の挙動

タスクファイル (1 行 1 タスク、`#` でコメント、空行は無視) を読み、各
タスクごとに `Controller` を新規生成して順次実行する。タスクごとに
`session_id` が分離される (bash 版 `patchpaw-run.sh` と同じ挙動)。

主な機能:
- `--max-iter` (env: `MAX_ITER`) LLM 最大試行回数
- `--stop-on-fail` / `--no-stop-on-fail` (env: `STOP_ON_FAIL`)
- `--commit-per-task` / `--no-commit-per-task` (env: `COMMIT_PER_TASK`)
- `--continue-from-task N` (env: `CONTINUE_FROM_TASK`) 1-indexed で再開
- `--carry-context` / `--no-carry-context` (env: `CARRY_CONTEXT`)
  直前タスクで変更されたファイル一覧を次タスクのプロンプトに自動注入
- `--dry-run` (env: `DRY_RUN`)
- `--test-cmd` (env: `PATCHPAW_TEST_CMD`、未指定時は `.patchpaw/test-cmd`
  → デフォルトの順で解決)

CLI フラグ未指定時はすべて対応する環境変数を見て、それも未指定なら
デフォルト値を使う。

実行終了時に `sessions/<run_id>_summary.json` が書かれる (P1 v2.3)。
含まれるフィールド:
- `run_id`, `started_at`, `finished_at`, `total_duration_s`
- 設定値: `test_cmd`, `max_iter`, `stop_on_fail`, `commit_per_task`,
  `start_from`
- 集計: `tasks_file_total`, `executed`, `succeeded`, `failed`
- `tokens_total`: run 全体の LLM トークン使用量集計
  (`prompt` / `completion` / `total`)。プロバイダが usage を返さない場合は 0
- `tasks[]`: 各タスクの `task`, `success`, `duration_s`,
  `iterations`, `message`, `tokens` (`prompt` / `completion` / `total`)

---

# 14. Session Storage

```
sessions/
├── 20260517_120000.jsonl     # セッションログ（JSONL形式）
├── 20260517_120000_iter1.patch  # 試行1のパッチ
└── 20260517_120000_iter2.patch  # 試行2のパッチ
```

---

# 15. Error Handling

- Invalid SEARCH/REPLACE block → regenerate
- SEARCH not found in file → regenerate
- Patch apply failure → rollback and abort
- Test timeout → abort
- Retry limit reached → stop and report

---

# 16. Minimum Viable Product (MVP)

Features:

- CLI interface
- Single repository support
- SEARCH/REPLACE block generation
- User approval
- String replacement with rollback
- Docker-based test execution
- Iterative repair loop

Estimated implementation effort:

- 1 to 3 days

---

# 17. Future Enhancements

- Semantic code search
- AST-based relevance selection
- Web UI
- Parallel patch exploration
- Multi-agent planning
- IDE integration
- `patchpaw history` command for session browsing

---

# 18. Success Criteria

The system can:

- Accept a coding request
- Generate valid SEARCH/REPLACE blocks
- Apply the patch safely with rollback on failure
- Run tests in isolation
- Iterate until success
- Preserve a complete audit trail

---

# 19. License

MIT License

---

# 20. Summary

PatchPaw is a secure AI coding assistant based on a simple principle:

> **The LLM may suggest patches, but it never controls the system directly.**

This architecture combines:

- Strong security
- Full auditability
- Local execution
- Practical coding assistance
