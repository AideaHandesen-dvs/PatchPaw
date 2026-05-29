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
│   └── config.py
├── tests/
│   └── test_patchpaw.py
├── config.yaml
├── pyproject.toml
├── README.md
├── DESIGN.md
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
```

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

# 20. Project Knowledge Layout (for Claude Projects)

PatchPaw 自身を Claude Projects で開発する場合、リポジトリ全体を zip にまとめて
Project knowledge にアップロードしてはならない。`git ls-files` で出る個別ファイルを
そのままアップロードする。

## Rationale

- Claude.ai のブラウザ UI でユーザーが各ファイルの中身を直接確認できる。
  zip では中身が見えるのは Claude (サンドボックス内で展開できる) だけで、
  ユーザーには見えない。これは「Project knowledge に何が入っているか」を
  ユーザーが自分の目で監査できないことを意味し、共有状態の信頼性を損なう。
- `git ls-files` は `.gitignore` を尊重するので、`sessions/`, `.env`, ビルド
  成果物などが自動的に除外される。
- 数ファイルだけ変えたとき、zip を作り直す必要がなく、変更ファイルだけ
  差し替えればよい。

## Upload procedure

```bash
cd ~/patchpaw
git ls-files
# 出てきたファイルをファイルマネージャで全選択し、
# Claude Projects の Project knowledge パネルにドラッグ&ドロップ
```

ディレクトリ構造を保ったまま別マシンに転送してから上げる場合:

```bash
rsync -R $(git ls-files) <remote>:<target>/
```

## Refresh routine

- **変更ごと**: 変更ファイルだけ Project knowledge で差し替える
- **フルリセット**: Project knowledge を全削除し、`git ls-files` の出力を再アップロード
- **セッション終了時**: コードを変更したなら、対応するファイルを Project knowledge にも
  反映してから閉じる。これを怠るとリポジトリの実態と Project knowledge が乖離し、
  次セッションの Claude が古い情報で動く

---

# 21. Summary

PatchPaw is a secure AI coding assistant based on a simple principle:

> **The LLM may suggest patches, but it never controls the system directly.**

This architecture combines:

- Strong security
- Full auditability
- Local execution
- Practical coding assistance
