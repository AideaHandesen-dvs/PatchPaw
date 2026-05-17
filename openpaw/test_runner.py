"""
Test Runner
Docker コンテナ内でテストを実行する。
- ネットワーク遮断
- root権限なし (--user 1000:1000)
- タイムアウト設定
- メモリ制限
"""

from __future__ import annotations

import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import SandboxConfig


@dataclass
class TestResult:
    success: bool
    output: str
    exit_code: int


class TestRunner:
    def __init__(self, repo_root: str | Path, config: SandboxConfig):
        self.root = Path(repo_root).resolve()
        self.config = config

    def run(self, command: str = "python -m pytest tests/ -v --tb=short") -> TestResult:
        """
        Docker が使用可能な場合は Docker 内で、なければローカルで実行する。
        """
        if self._docker_available():
            return self._run_in_docker(command)
        else:
            return self._run_local(command)

    def _run_in_docker(self, command: str) -> TestResult:
        net_flag = "--network=none" if self.config.network_disabled else ""
        cmd = [
            "docker", "run",
            "--rm",
            f"--memory={self.config.memory_limit}",
            "--cpus=1",
            "--user=1000:1000",          # root権限なし
            "--read-only",               # コンテナFS読み取り専用
            "--tmpfs=/tmp:size=64m",     # /tmp だけ書き込み可
        ]
        if net_flag:
            cmd.append(net_flag)
        cmd += [
            "--volume", f"{self.root}:/workspace:ro",  # ソースは読み取り専用マウント
            "--workdir=/workspace",
            self.config.docker_image,
            "sh", "-c", command,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            output = (result.stdout + result.stderr).strip()
            return TestResult(
                success=result.returncode == 0,
                output=output,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                success=False,
                output=f"タイムアウト ({self.config.timeout_seconds}秒)",
                exit_code=124,
            )

    def _run_local(self, command: str) -> TestResult:
        """Docker がない環境向けのフォールバック（開発用）。"""
        import os, shlex

        env = {k: v for k, v in os.environ.items()}
        # .env 系を除外
        for key in list(env.keys()):
            if "SECRET" in key or "PASSWORD" in key or "TOKEN" in key:
                del env[key]

        try:
            result = subprocess.run(
                shlex.split(command),
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                env=env,
            )
            output = (result.stdout + result.stderr).strip()
            return TestResult(
                success=result.returncode == 0,
                output=output,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                success=False,
                output=f"タイムアウト ({self.config.timeout_seconds}秒)",
                exit_code=124,
            )
        except FileNotFoundError as e:
            return TestResult(success=False, output=str(e), exit_code=1)

    @staticmethod
    def _docker_available() -> bool:
        return shutil.which("docker") is not None and _docker_running()


def _docker_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
