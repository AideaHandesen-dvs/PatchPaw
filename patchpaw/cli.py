#!/usr/bin/env python3
"""
PatchPaw CLI
使い方:
  patchpaw fix "バグを修正してください" --repo ./myproject
  patchpaw fix "テストを追加してください" --files src/foo.py tests/test_foo.py
  patchpaw list-files --repo ./myproject
  patchpaw run tasks.txt --repo ./myproject
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .controller import Controller
from .repository_reader import RepositoryReader
from .runner import TaskRunner, parse_tasks_file


def _load_dotenv() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _find_config(specified: str) -> Path:
    """設定ファイルを探す。

    検索順:
      1. --config で明示指定されたパス（デフォルト値以外の場合）
      2. 環境変数 PATCHPAW_CONFIG
      3. カレントディレクトリの config.yaml
      4. ~/.patchpaw.yaml
      5. インストール元ディレクトリの config.yaml (~/patchpaw/config.yaml 等)
    """
    if specified != "config.yaml":
        return Path(specified)

    if env := os.environ.get("PATCHPAW_CONFIG"):
        return Path(env)

    if (p := Path("config.yaml")).exists():
        return p

    if (p := Path.home() / ".patchpaw.yaml").exists():
        return p

    if (p := Path(__file__).parent.parent / "config.yaml").exists():
        return p

    return Path("config.yaml")


# ────────────────────────────────────────────
# 環境変数フォールバックヘルパ
# ────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """環境変数を bool として解釈。'0'/'false'/'no'/'' は False、その他は True。"""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() not in ("0", "false", "no", "")


BANNER = r"""
██████   █████  ████████  ██████ ██   ██ ██████   █████  ██     ██
██   ██ ██   ██    ██    ██      ██   ██ ██   ██ ██   ██ ██     ██
██████  ███████    ██    ██      ███████ ██████  ███████ ██  █  ██
██      ██   ██    ██    ██      ██   ██ ██      ██   ██ ██ ███ ██
██      ██   ██    ██     ██████ ██   ██ ██      ██   ██  ███ ███

 PatchPaw — LLM は diff を作るだけ。実行権は Controller が持つ。
"""


# ────────────────────────────────────────────
# fix
# ────────────────────────────────────────────

def cmd_fix(args: argparse.Namespace, config: Config) -> int:
    repo = Path(args.repo).resolve()

    def approval(diff_text: str) -> bool:
        if args.yes:
            return True
        answer = input("\n👆 この diff を適用しますか？ [y/N]: ").strip().lower()
        return answer in ("y", "yes")

    controller = Controller(
        repo_root=repo,
        config=config,
        max_iterations=args.max_iter,
        approval_callback=approval,
    )

    file_hints = args.files if args.files else None
    result = controller.run(
        instruction=args.instruction,
        file_hints=file_hints,
        test_command=args.test_cmd,
    )

    print(f"\n{'✅' if result.success else '❌'} {result.message}")
    print(f"   試行回数: {result.iterations}")
    return 0 if result.success else 1


# ────────────────────────────────────────────
# list-files
# ────────────────────────────────────────────

def cmd_list_files(args: argparse.Namespace, config: Config) -> int:
    repo = Path(args.repo).resolve()
    reader = RepositoryReader(repo, config)
    files = reader.list_allowed()
    if not files:
        print("ホワイトリストに含まれるファイルが見つかりません。")
        return 1
    print(f"📁 {repo} の許可ファイル一覧:")
    for f in files:
        print(f"   {f}")
    return 0


# ────────────────────────────────────────────
# run (新規)
# ────────────────────────────────────────────

def cmd_run(args: argparse.Namespace, config: Config) -> int:
    """tasks.txt を読み、TaskRunner で順次実行する。"""
    repo = Path(args.repo).resolve()

    # CLI フラグ > 環境変数 > デフォルト
    max_iter        = args.max_iter        if args.max_iter        is not None else _env_int("MAX_ITER", 5)
    stop_on_fail    = args.stop_on_fail    if args.stop_on_fail    is not None else _env_bool("STOP_ON_FAIL", True)
    commit_per_task = args.commit_per_task if args.commit_per_task is not None else _env_bool("COMMIT_PER_TASK", True)
    dry_run         = args.dry_run         if args.dry_run                     else _env_bool("DRY_RUN", False)
    start_from      = (
        args.continue_from_task if args.continue_from_task is not None
        else _env_int("CONTINUE_FROM_TASK", 1)
    )

    # test_cmd 解決: CLI > env > .patchpaw/test-cmd > default
    if args.test_cmd:
        test_cmd = args.test_cmd
    elif env_test := os.environ.get("PATCHPAW_TEST_CMD"):
        test_cmd = env_test
    elif (tc_file := repo / ".patchpaw" / "test-cmd").exists():
        test_cmd = tc_file.read_text(encoding="utf-8").strip()
    else:
        test_cmd = TaskRunner.DEFAULT_TEST_CMD

    try:
        tasks = parse_tasks_file(args.tasks_file)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    if not tasks:
        print(f"❌ {args.tasks_file} に実行可能なタスクがありません")
        return 1

    runner = TaskRunner(
        repo_root=repo,
        config=config,
        max_iter=max_iter,
        stop_on_fail=stop_on_fail,
        commit_per_task=commit_per_task,
        dry_run=dry_run,
        test_cmd=test_cmd,
        start_from=start_from,
    )
    ok = runner.run_tasks(tasks)
    return 0 if ok else 1


# ────────────────────────────────────────────
# main
# ────────────────────────────────────────────

def main() -> None:
    _load_dotenv()
    if not os.environ.get("PATCHPAW_QUIET"):
        print(BANNER)

    parser = argparse.ArgumentParser(
        prog="patchpaw",
        description="PatchPaw — 安全なAIコーディングエージェント",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="設定ファイルのパス (default: 自動検索)"
    )
    parser.add_argument(
        "--version", action="version", version=f"PatchPaw {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    # fix サブコマンド
    fix_parser = subparsers.add_parser("fix", help="LLMで変更を生成・適用する")
    fix_parser.add_argument("instruction", help="LLMへの指示 (例: 'バグを修正して')")
    fix_parser.add_argument(
        "--files", nargs="*", help="読み込むファイルを明示指定 (省略時はホワイトリスト全体)"
    )
    fix_parser.add_argument(
        "--test-cmd",
        default="python -m pytest tests/ -v --tb=short",
        help="テストコマンド",
    )
    fix_parser.add_argument(
        "--max-iter", type=int, default=5, help="最大試行回数 (default: 5)"
    )
    fix_parser.add_argument(
        "--yes", "-y", action="store_true", help="承認プロンプトをスキップ"
    )
    fix_parser.add_argument(
        "--repo", default=".", help="対象リポジトリのパス (default: .)"
    )

    # list-files サブコマンド
    list_parser = subparsers.add_parser("list-files", help="許可ファイル一覧を表示")
    list_parser.add_argument(
        "--repo", default=".", help="対象リポジトリのパス (default: .)"
    )

    # run サブコマンド (新規)
    run_parser = subparsers.add_parser(
        "run", help="タスクファイルから複数タスクを順次実行 (patchpaw-run.sh の後継)"
    )
    run_parser.add_argument(
        "tasks_file", help="タスクファイル (1行1タスク、# でコメント、空行は無視)"
    )
    run_parser.add_argument(
        "--repo", default=".", help="対象リポジトリのパス (default: .)"
    )
    run_parser.add_argument(
        "--max-iter", type=int, default=None,
        help="LLM 最大試行回数 (env: MAX_ITER, default: 5)",
    )
    # --stop-on-fail / --no-stop-on-fail
    run_parser.add_argument(
        "--stop-on-fail", dest="stop_on_fail", action="store_true", default=None,
        help="失敗時に停止 (env: STOP_ON_FAIL, default: 有効)",
    )
    run_parser.add_argument(
        "--no-stop-on-fail", dest="stop_on_fail", action="store_false",
        help="失敗しても次のタスクへ継続",
    )
    # --commit-per-task / --no-commit-per-task
    run_parser.add_argument(
        "--commit-per-task", dest="commit_per_task", action="store_true", default=None,
        help="タスクごとに git add/commit/tag (env: COMMIT_PER_TASK, default: 有効)",
    )
    run_parser.add_argument(
        "--no-commit-per-task", dest="commit_per_task", action="store_false",
        help="git commit/tag を打たない",
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="実行せず、何が走るかだけ表示 (env: DRY_RUN)",
    )
    run_parser.add_argument(
        "--test-cmd", default=None,
        help="テストコマンド (env: PATCHPAW_TEST_CMD, "
             "未指定時は .patchpaw/test-cmd → デフォルトの順で解決)",
    )
    run_parser.add_argument(
        "--continue-from-task", dest="continue_from_task", type=int, default=None,
        metavar="N",
        help="タスクファイルの N 番目から開始 (1-indexed, env: CONTINUE_FROM_TASK)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    config_path = _find_config(args.config)
    config = Config.load(config_path)

    if args.command == "fix":
        sys.exit(cmd_fix(args, config))
    elif args.command == "list-files":
        sys.exit(cmd_list_files(args, config))
    elif args.command == "run":
        sys.exit(cmd_run(args, config))


if __name__ == "__main__":
    main()
