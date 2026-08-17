"""Turning intake answers into the agent's prompt.

Pure functions with no discord.py dependency so the future dashboard can render
a live preview using exactly the same code path the bot uses.
"""

from __future__ import annotations

import re

from ..storage.models import Question

_TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

ANSWERS_TOKEN = "answers"
"""``{answers}`` expands to every question and answer as a labelled block."""


def format_answer_block(questions: list[Question], answers: dict[str, str]) -> str:
    """Render all answers as ``Label: value`` lines, in question order."""
    lines: list[str] = []
    for question in sorted(questions, key=lambda q: (q.page, q.position)):
        value = (answers.get(question.key) or "").strip()
        if not value:
            continue
        # Multi-line answers read better indented under their label.
        if "\n" in value:
            indented = "\n".join(f"  {line}" for line in value.splitlines())
            lines.append(f"{question.label}:\n{indented}")
        else:
            lines.append(f"{question.label}: {value}")
    return "\n".join(lines)


def render_prompt(
    template: str,
    questions: list[Question],
    answers: dict[str, str],
) -> str:
    """Substitute ``{question_key}`` and ``{answers}`` tokens into ``template``.

    Unknown tokens render as empty strings rather than raising, so a template
    that references a since-deleted question still produces usable output.
    """
    block = format_answer_block(questions, answers)

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == ANSWERS_TOKEN:
            return block
        return (answers.get(key) or "").strip()

    return _TOKEN.sub(substitute, template).strip()


def template_tokens(template: str) -> set[str]:
    """Every ``{token}`` referenced by a template. Used to warn owners about
    templates pointing at questions that no longer exist."""
    return set(_TOKEN.findall(template))


def unknown_tokens(template: str, questions: list[Question]) -> set[str]:
    known = {q.key for q in questions} | {ANSWERS_TOKEN}
    return template_tokens(template) - known
