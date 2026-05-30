"""
Controller の単体テスト。

実依存 (RepositoryReader, DiffValidator, PatchApplier, SessionManager) は本物。
LLMAdapter と TestRunner は差し替え可能なスタブを使う。これは Controller の
オーケストレーション (iteration ループ、各種 retry 経路、トークン累積、
patch_files 蓄積、carry-context 伝達) を検証することが目的で、各依存単体の
挙動はそれぞれのテストクラスで担保されている。

HANDOFF.md 罠 4.8 (直接テストの欠落) の埋め合わせ。
"""

from __future__ import annotations

import pytest

from patchpaw.config import Config
from patchpaw.controller import Controller
from patchpaw.llm_adapter import GenerateResult
# pytest が `Test` で始まる名前を test class として収集しようとするので、
# 別名で import して collection warning を避ける。
from patchpaw.test_runner import TestResult as _TestResult


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

class _ScriptedLLM:
    """Controller.llm に差し込むスタブ。outputs を先頭から順に返す。

    list 要素が Exception なら raise する (LLM エラー経路の検証用)。
    呼び出された messages は self.calls に記録される (carry-context の検証用)。
    """

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[list[dict]] = []

    def generate(self, messages):
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError(
                f"_ScriptedLLM: 想定外の追加呼び出し ({len(self.calls)} 回目)"
            )
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _ScriptedTestRunner:
    """Controller.test_runner に差し込むスタブ。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[str] = []

    def run(self, command="python -m pytest tests/ -v --tb=short"):
        self.calls.append(command)
        if not self.results:
            raise AssertionError(
                f"_ScriptedTestRunner: 想定外の追加呼び出し ({len(self.calls)} 回目)"
            )
        return self.results.pop(0)


@pytest.fixture
def repo(tmp_path):
    """Controller が動く最小リポジトリ。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text(
        "from src.main import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def config():
    """allowed_paths を repo 配下に絞った Config。"""
    c = Config()
    c.repository.allowed_paths = ["src/", "tests/"]
    return c


def _make_controller(
    repo,
    config,
    llm_outputs,
    test_results,
    *,
    approval=True,
    max_iterations=5,
):
    """Controller を組み立て、LLM と TestRunner をスタブに差し替える。"""
    if callable(approval):
        approval_cb = approval
    else:
        approval_cb = lambda _: bool(approval)  # noqa: E731

    ctrl = Controller(
        repo_root=repo,
        config=config,
        max_iterations=max_iterations,
        approval_callback=approval_cb,
        progress_callback=lambda *_args, **_kwargs: None,  # 静かに
    )
    ctrl.llm = _ScriptedLLM(llm_outputs)
    ctrl.test_runner = _ScriptedTestRunner(test_results)
    return ctrl


# repo fixture の src/main.py を 1 行書き換える patch
PATCH_TOUCH = """\
FILE: src/main.py
<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a, b):
    return a + b  # touched
>>>>>>> REPLACE
"""

# PATCH_TOUCH 適用後の src/main.py をさらに書き換える patch (テスト失敗→retry 用)
PATCH_TOUCH_AGAIN = """\
FILE: src/main.py
<<<<<<< SEARCH
def add(a, b):
    return a + b  # touched
=======
def add(a, b):
    return a + b  # touched twice
>>>>>>> REPLACE
"""


# ────────────────────────────────────────────
# 1. Happy path
# ────────────────────────────────────────────
class TestControllerHappyPath:
    def test_single_iteration_success(self, repo, config):
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(
                    text=PATCH_TOUCH,
                    prompt_tokens=100, completion_tokens=20, total_tokens=120,
                )
            ],
            test_results=[_TestResult(success=True, output="1 passed", exit_code=0)],
        )
        result = ctrl.run("touch main", file_hints=["src/main.py"])

        assert result.success is True
        assert result.iterations == 1
        assert result.affected_files == ["src/main.py"]
        assert result.message == "完了"
        # トークン累積 (1 iter なのでそのまま反映)
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 20
        assert result.total_tokens == 120
        # llm_elapsed_s は実時間なので 0 以上であることだけ確認
        assert result.llm_elapsed_s >= 0.0
        # patch_files は 1 件、.patch で終わる
        assert len(result.patch_files) == 1
        assert result.patch_files[0].endswith(".patch")
        # 実ファイルが書き換わっていること
        assert "# touched" in (repo / "src" / "main.py").read_text()


# ────────────────────────────────────────────
# 2. LLM 空出力 → "変更不要"
# ────────────────────────────────────────────
class TestControllerNoChange:
    def test_empty_output_marks_success_as_no_change(self, repo, config):
        """LLM が空 (or 空白だけ) を返したら success=True で "変更不要" 扱い。
        罠 4.4 (HANDOFF.md) で言及されている冪等性挙動の回帰テスト。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(
                    text="   \n\n   ",  # 空白だけ
                    prompt_tokens=50, completion_tokens=0, total_tokens=50,
                )
            ],
            test_results=[],  # テストは走らないはず
        )
        result = ctrl.run("nothing to do")

        assert result.success is True
        assert "変更不要" in result.message
        assert result.iterations == 1
        # LLM 呼び出しは行われたのでトークンは累積される
        assert result.prompt_tokens == 50
        assert result.total_tokens == 50
        # apply してないので patch_files は空
        assert result.patch_files == []
        # 実ファイルは元のまま
        assert "# touched" not in (repo / "src" / "main.py").read_text()


# ────────────────────────────────────────────
# 3. LLM 例外 (1 iter 目)
# ────────────────────────────────────────────
class TestControllerLLMException:
    def test_first_iter_exception_returns_failure_with_zero_tokens(
        self, repo, config
    ):
        """iter 1 で LLM が例外を投げた場合、累積前なので全フィールド 0。
        controller.py の except 節は累積変数をそのまま渡すので、
        例外前に累積されていなければ 0 のまま。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[RuntimeError("network down")],
            test_results=[],
        )
        result = ctrl.run("anything")

        assert result.success is False
        assert "LLM エラー" in result.message
        assert "network down" in result.message
        assert result.iterations == 1
        # iter1 例外、累積されてない
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.total_tokens == 0
        assert result.llm_elapsed_s == 0.0
        assert result.patch_files == []


# ────────────────────────────────────────────
# 4. collect_files が空
# ────────────────────────────────────────────
class TestControllerEmptyRepo:
    def test_no_collectable_files_returns_failure(self, repo, config):
        """allowed_paths を空にすると 1 ファイルも読めない → iterations=0 で失敗。"""
        config.repository.allowed_paths = []
        ctrl = _make_controller(repo, config, llm_outputs=[], test_results=[])
        result = ctrl.run("nothing to read")

        assert result.success is False
        assert result.iterations == 0
        # message に config.yaml への言及があること (運用ヒント)
        assert "config.yaml" in result.message or "見つかりません" in result.message
        # LLM は呼ばれていないはず
        assert ctrl.llm.calls == []


# ────────────────────────────────────────────
# 5. 検証エラー → retry
# ────────────────────────────────────────────
class TestControllerValidationRetry:
    def test_validation_error_then_success(self, repo, config):
        """iter 1: ブロック構文不正で validator NG。iter 2: 正しい SEARCH/REPLACE。"""
        bad_output = "no valid SEARCH/REPLACE block here at all"
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(
                    text=bad_output,
                    prompt_tokens=50, completion_tokens=10, total_tokens=60,
                ),
                GenerateResult(
                    text=PATCH_TOUCH,
                    prompt_tokens=80, completion_tokens=20, total_tokens=100,
                ),
            ],
            test_results=[_TestResult(success=True, output="ok", exit_code=0)],
        )
        result = ctrl.run("retry me")

        assert result.success is True
        assert result.iterations == 2
        # 両 iter のトークンが累積されている
        assert result.prompt_tokens == 130
        assert result.completion_tokens == 30
        assert result.total_tokens == 160
        # iter 1 は apply に到達してないので patch_files は iter 2 の 1 件のみ
        assert len(result.patch_files) == 1


# ────────────────────────────────────────────
# 6. dry_run 失敗 → retry
# ────────────────────────────────────────────
class TestControllerDryRunRetry:
    def test_dry_run_failure_then_success(self, repo, config):
        """iter 1: SEARCH 文字列がファイル中に無く dry_run 失敗。iter 2: 成功。"""
        bad_dry = (
            "FILE: src/main.py\n"
            "<<<<<<< SEARCH\n"
            "this string is definitely not in the file\n"
            "=======\n"
            "replacement\n"
            ">>>>>>> REPLACE\n"
        )
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(
                    text=bad_dry,
                    prompt_tokens=50, completion_tokens=10, total_tokens=60,
                ),
                GenerateResult(
                    text=PATCH_TOUCH,
                    prompt_tokens=80, completion_tokens=20, total_tokens=100,
                ),
            ],
            test_results=[_TestResult(success=True, output="ok", exit_code=0)],
        )
        result = ctrl.run("dry run retry")

        assert result.success is True
        assert result.iterations == 2
        # dry_run 失敗 iter は apply 未到達 → patch_files は 1 件
        assert len(result.patch_files) == 1


# ────────────────────────────────────────────
# 7. テスト失敗 → retry → 両 iter の patch が記録される
# ────────────────────────────────────────────
class TestControllerTestFailureRetry:
    def test_test_failure_then_success_records_both_patches(self, repo, config):
        """apply 成功した iter は (たとえテストが失敗した iter であっても)
        全部 patch_files に蓄積される。これは DESIGN.md の仕様
        (ロールバック前のデバッグに使える)。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(text=PATCH_TOUCH, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
                GenerateResult(text=PATCH_TOUCH_AGAIN, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
            ],
            test_results=[
                _TestResult(success=False, output="1 failed", exit_code=1),
                _TestResult(success=True, output="1 passed", exit_code=0),
            ],
        )
        result = ctrl.run("test retry")

        assert result.success is True
        assert result.iterations == 2
        # 両 iter とも apply 成功しているので 2 件
        assert len(result.patch_files) == 2


# ────────────────────────────────────────────
# 8. max_iterations 達成 → 失敗
# ────────────────────────────────────────────
class TestControllerMaxIterations:
    def test_all_tests_failing_hits_max_iterations(self, repo, config):
        """max_iterations=2 で両 iter テスト失敗 → success=False、
        message に試行回数が含まれる、両 iter の patch は記録される。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(text=PATCH_TOUCH, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
                GenerateResult(text=PATCH_TOUCH_AGAIN, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
            ],
            test_results=[
                _TestResult(success=False, output="fail1", exit_code=1),
                _TestResult(success=False, output="fail2", exit_code=1),
            ],
            max_iterations=2,
        )
        result = ctrl.run("never succeeds")

        assert result.success is False
        assert result.iterations == 2
        assert "2" in result.message  # "2 回試行..." のような文言
        # 両 iter とも apply 成功 → 2 件
        assert len(result.patch_files) == 2
        # 累積も全部 2 iter ぶん
        assert result.total_tokens == 200


# ────────────────────────────────────────────
# 9. ユーザー拒否
# ────────────────────────────────────────────
class TestControllerUserRejection:
    def test_user_rejection_returns_failure_no_apply(self, repo, config):
        """approval_callback が False を返したら apply 前に中断。
        ファイルは書き換わらない、patch_files は空、ただし LLM は呼ばれたので
        トークンは累積される。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(text=PATCH_TOUCH, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
            ],
            test_results=[],
            approval=False,
        )
        result = ctrl.run("nope")

        assert result.success is False
        assert "拒否" in result.message
        assert result.iterations == 1
        # LLM は呼ばれたので累積
        assert result.prompt_tokens == 80
        assert result.total_tokens == 100
        # apply 前なので patch_files は空、ファイルも変更されてない
        assert result.patch_files == []
        assert "# touched" not in (repo / "src" / "main.py").read_text()


# ────────────────────────────────────────────
# 10. carry-context の伝達
# ────────────────────────────────────────────
class TestControllerCarryContext:
    def test_previous_task_changes_passed_to_prompt(self, repo, config):
        """run() の previous_task_changes 引数が PromptBuilder.build 経由で
        実際にプロンプトに乗ること。LLM への messages 文字列に該当パスが
        含まれていることで確認する。"""
        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(text=PATCH_TOUCH, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
            ],
            test_results=[_TestResult(success=True, output="ok", exit_code=0)],
        )
        result = ctrl.run(
            "follow-up task",
            previous_task_changes=["src/foo.py", "src/bar.py"],
        )

        assert result.success is True
        # LLM 呼び出しの messages を flatten してチェック
        joined = "\n".join(
            m.get("content", "")
            for msgs in ctrl.llm.calls
            for m in msgs
        )
        assert "src/foo.py" in joined
        assert "src/bar.py" in joined


# ────────────────────────────────────────────
# 11. .patchpaw/context.md の常時注入
# ────────────────────────────────────────────
class TestControllerProjectContext:
    def test_context_md_is_loaded_and_injected(self, repo, config):
        """.patchpaw/context.md があれば project_context として読まれ、
        毎回のプロンプトに含まれる。"""
        (repo / ".patchpaw").mkdir()
        marker = "PROJECT_CONTEXT_MARKER_XYZ"
        (repo / ".patchpaw" / "context.md").write_text(marker, encoding="utf-8")

        ctrl = _make_controller(
            repo, config,
            llm_outputs=[
                GenerateResult(text=PATCH_TOUCH, prompt_tokens=80,
                               completion_tokens=20, total_tokens=100),
            ],
            test_results=[_TestResult(success=True, output="ok", exit_code=0)],
        )

        # コンストラクタで読まれている
        assert ctrl.project_context is not None
        assert marker in ctrl.project_context

        ctrl.run("anything")
        joined = "\n".join(
            m.get("content", "")
            for msgs in ctrl.llm.calls
            for m in msgs
        )
        assert marker in joined

    def test_no_context_md_means_none(self, repo, config):
        """.patchpaw/context.md が無ければ project_context は None。"""
        ctrl = _make_controller(repo, config, llm_outputs=[], test_results=[])
        assert ctrl.project_context is None
