# OpenPaw Code Design Document

Version: 0.1.0
Status: Draft

---

# 1. Overview

OpenPaw Code is a secure local AI coding assistant inspired by OpenClaw.

Unlike agent systems that are granted direct control over the host environment, OpenPaw Code treats the Large Language Model (LLM) as a pure patch generator.

The LLM receives:

- User instructions
- Relevant source files
- Test results

And returns:

- Unified diff patches

The LLM never receives:

- SSH keys
- Root privileges
- Arbitrary shell access
- Direct file system control

This design minimizes security risks while preserving most of the practical value of AI-assisted software development.

---

# 2. Design Principles

## 2.1 Patch-Only Architecture

The LLM is restricted to generating unified diff patches.

## 2.2 Human-in-the-Loop

All generated patches require explicit user approval before being applied.

## 2.3 Least Privilege

The system grants only the minimum permissions necessary.

## 2.4 Local-First

All code and prompts can be processed locally using models such as Ollama.

## 2.5 Reproducibility

Every action is recorded and can be replayed.

## 2.6 Auditability

All changes are represented as Git-compatible diffs.

---

# 3. High-Level Architecture

```text
User
  ↓
CLI / UI
  ↓
Controller
  ├─ Repository Reader
  ├─ Context Builder
  ├─ LLM Adapter
  ├─ Diff Validator
  ├─ Patch Applier
  ├─ Test Runner
  └─ Session Manager
```

---

# 4. Core Concept

The LLM is treated as a deterministic transformation:

```text
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
- Enforce output format requirements

## 5.3 Patch Generation

- Request unified diff output only

## 5.4 Patch Validation

- Validate diff syntax
- Restrict modified paths
- Enforce file size limits

## 5.5 Patch Application

- Apply patches using `git apply`

## 5.6 Test Execution

- Run tests in isolated environments

## 5.7 Iterative Repair

- Retry based on test failures

## 5.8 Session Persistence

- Store prompts, diffs, logs, and metadata

---

# 6. Non-Functional Requirements

## Security
- No direct shell access for the LLM
- No root privileges
- No SSH keys
- Restricted file access
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

Extracts relevant project files.

## 7.3 Context Builder

Constructs LLM prompts.

## 7.4 LLM Adapter

Communicates with providers.

Supported backends:
- Ollama
- OpenAI-compatible APIs
- Anthropic-compatible APIs

## 7.5 Diff Validator

Checks generated patches.

## 7.6 Patch Applier

Applies validated diffs.

## 7.7 Test Runner

Executes tests in sandboxed environments.

## 7.8 Session Manager

Stores artifacts and metadata.

---

# 8. Workflow

```text
1. User submits request
2. Repository Reader selects files
3. Context Builder constructs prompt
4. LLM Adapter requests patch
5. Diff Validator checks output
6. User reviews patch
7. Patch Applier applies patch
8. Test Runner executes tests
9. If tests fail, feed results back to LLM
10. Repeat until success or retry limit reached
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

```text
Host OS
 └─ Virtual Machine (optional)
     └─ Docker Container
         └─ OpenPaw Code
```

---

# 11. Directory Structure

```text
openpaw-code/
├── openpaw_code/
│   ├── controller.py
│   ├── repository_reader.py
│   ├── context_builder.py
│   ├── llm_adapter.py
│   ├── diff_validator.py
│   ├── patch_applier.py
│   ├── test_runner.py
│   ├── session_manager.py
│   └── config.py
├── tests/
├── docs/
│   └── DESIGN.md
├── assets/
│   └── logo.png
├── pyproject.toml
├── README.md
└── LICENSE
```

---

# 12. Configuration

```yaml
llm:
  provider: ollama
  model: qwen3:8b

repository:
  allowed_paths:
    - src/
    - tests/
    - README.md

patch:
  max_files: 20
  max_patch_size_kb: 512

sandbox:
  enabled: true
  docker_image: python:3.12
  network_disabled: true
  timeout_seconds: 300
  memory_limit_mb: 4096

agent:
  max_iterations: 5
  require_user_approval: true
```

---

# 13. CLI Design

```bash
openpaw "Fix failing tests"
openpaw "Add unit tests for parser.py"
openpaw --auto "Refactor alert_handler.py"
openpaw resume session-20260517-001
```

---

# 14. Session Storage

```text
.sessions/
└── 20260517-001/
    ├── config.yaml
    ├── prompt.txt
    ├── response.txt
    ├── patch.diff
    ├── test.log
    └── metadata.json
```

---

# 15. Error Handling

- Invalid diff → regenerate
- Patch apply failure → abort
- Test timeout → abort
- Retry limit reached → stop and report

---

# 16. Minimum Viable Product (MVP)

Features:
- CLI interface
- Single repository support
- Unified diff generation
- User approval
- `git apply`
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

---

# 18. Success Criteria

The system can:
1. Accept a coding request
2. Generate a valid patch
3. Apply the patch safely
4. Run tests in isolation
5. Iterate until success
6. Preserve a complete audit trail

---

# 19. License

MIT License

---

# 20. Summary

OpenPaw Code is a secure AI coding assistant based on a simple principle:

> The LLM may suggest patches, but it never controls the system directly.

This architecture combines:
- Strong security
- Full auditability
- Local execution
- Practical coding assistance

