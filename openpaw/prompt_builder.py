"""
Prompt Builder
LLMへ送るプロンプトを組み立てる。
LLMへの出力は「unified diff のみ」に制約する。
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a code editing assistant. Your ONLY output must be a valid unified diff.

Rules:
- Output ONLY a unified diff in standard format (--- a/... +++ b/...).
- Do NOT include any explanation, markdown fences, or prose.
- Do NOT add, remove, or rename files unless explicitly requested.
- Do NOT read, write, or execute any system commands.
- Keep changes minimal and focused on the user's request.
- If no change is needed, output an empty response.

Unified diff format reminder:
--- a/path/to/file
+++ b/path/to/file
@@ -L,S +L,S @@
 context line
-removed line
+added line
 context line
"""


class PromptBuilder:
    def build(
        self,
        instruction: str,
        file_contents: dict[str, str],
        test_result: str | None = None,
        previous_diff: str | None = None,
        iteration: int = 1,
    ) -> list[dict]:
        """
        Chat messages リストを返す。
        """
        user_parts: list[str] = []

        user_parts.append(f"## User Instruction\n{instruction}")

        if file_contents:
            user_parts.append("## Source Files")
            for path, content in file_contents.items():
                user_parts.append(f"### {path}\n```\n{content}\n```")

        if test_result:
            label = "Test Result (previous attempt)" if iteration > 1 else "Test Result"
            user_parts.append(f"## {label}\n```\n{test_result}\n```")

        if previous_diff and iteration > 1:
            user_parts.append(
                f"## Previous Diff (attempt {iteration - 1})\n"
                f"The following diff was applied but tests failed. Fix it.\n"
                f"```diff\n{previous_diff}\n```"
            )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
