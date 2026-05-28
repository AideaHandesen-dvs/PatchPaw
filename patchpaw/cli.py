#!/usr/bin/env python3
"""
PatchPaw CLI
使い方:
  patchpaw fix "バグを修正してください" --repo ./myproject
  patchpaw fix "テストを追加してください" --files src/foo.py tests/test_foo.py
  patchpaw list-files --repo ./myproject
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import os

from . import __version__
from .config import Config
from .controller import Controller
from .repository_reader import RepositoryReader

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
    # 明示指定（--config に "config.yaml" 以外が渡された場合）
    if specified != "config.yaml":
        return Path(specified)

    # 環境変数
    if env := os.environ.get("PATCHPAW_CONFIG"):
        return Path(env)

    # カレントディレクトリ
    if (p := Path("config.yaml")).exists():
        return p

    # ホームディレクトリ (~/.patchpaw.yaml)
    if (p := Path.home() / ".patchpaw.yaml").exists():
        return p

    # インストール元ディレクトリ (cli.py の 2 階層上)
    # pip install -e . した場合: ~/patchpaw/patchpaw/cli.py → ~/patchpaw/config.yaml
    if (p := Path(__file__).parent.parent / "config.yaml").exists():
        return p

    # 見つからなければデフォルト（Config がデフォルト値を使う）
    return Path("config.yaml")


BANNER = r"""
██████   █████  ████████  ██████ ██   ██ ██████   █████  ██     ██
██   ██ ██   ██    ██    ██      ██   ██ ██   ██ ██   ██ ██     ██
██████  ███████    ██    ██      ███████ ██████  ███████ ██  █  ██
██      ██   ██    ██    ██      ██   ██ ██      ██   ██ ██ ███ ██
██      ██   ██    ██     ██████ ██   ██ ██      ██   ██  ███ ███

 PatchPaw — LLM は diff を作るだけ。実行権は Controller が持つ。
"""


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


if __name__ == "__main__":
    main()
