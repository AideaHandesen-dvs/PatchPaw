"""
Prompt Builder
LLMへ送るプロンプトを組み立てる。
LLMへの出力は「SEARCH/REPLACEブロック」に制約する。
行番号の計算はLLMに任せない。

常時文脈:
  project_context が渡されると、毎回のプロンプトに自動挿入される。
  プロジェクトルートの .patchpaw/context.md に設計書・コーディング規約・
  アーキテクチャ等を書いておけば、Claude Projects のナレッジベースに
  相当する機能を簡易的に実現できる。
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a code editing assistant. Output ONLY search/replace blocks.

For each change, use this exact format:

FILE: path/to/file.py
<<<<<<< SEARCH
exact original code to find
=======
replacement code
>>>>>>> REPLACE

Rules:
- FILE: must be the relative path from the repository root (e.g. src/main.py)
- SEARCH must be an exact copy of the existing code, including all whitespace and indentation
- SEARCH must be unique enough to match exactly one location in the file
  (include the surrounding function signature or context lines if needed)
- REPLACE is the new code to substitute in place of SEARCH
- To create a new file, use an empty SEARCH block (nothing between SEARCH and =======)
- Multiple blocks are allowed for multiple files or multiple changes to the same file
- Output NOTHING else — no explanations, no markdown fences, no prose
- If no change is needed, output nothing
"""


class PromptBuilder:
    def build(
        self,
        instruction: str,
        file_contents: dict[str, str],
        test_result: str | None = None,
        previous_output: str | None = None,
        iteration: int = 1,
        project_context: str | None = None,
    ) -> list[dict]:
        """
        Chat messages リストを返す。
        """
        user_parts: list[str] = []

        # 常時文脈（設計書・規約等）を最初に挿入
        if project_context:
            user_parts.append(f"## Project Context\n{project_context}")

        user_parts.append(f"## User Instruction\n{instruction}")

        if file_contents:
            user_parts.append("## Source Files")
            for path, content in file_contents.items():
                user_parts.append(f"### {path}\n```\n{content}\n```")

        if test_result:
            label = "Test Result (previous attempt)" if iteration > 1 else "Test Result"
            user_parts.append(f"## {label}\n```\n{test_result}\n```")

        if previous_output and iteration > 1:
            user_parts.append(
                f"## Previous Output (attempt {iteration - 1})\n"
                f"The following blocks were attempted but failed. Fix them.\n"
                f"```\n{previous_output}\n```"
            )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
