"""Turning intake answers into the agent's prompt.

Pure functions with no discord.py dependency, so a future dashboard can render a
live preview through the same code path the bot uses.
"""

from __future__ import annotations

import re

from ..storage.models import Question

_TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ALL_ANSWERS = "answers"


def format_answers(questions: list[Question], answers: dict[str, str]) -> str:
    """Render answered questions as ``Label: value`` lines, in modal order.

    Unanswered optional questions are omitted rather than shown as blank.
    """
    lines = []
    for question in questions:
        value = answers.get(question.key, "").strip()
        if not value:
            continue
        if "\n" in value:
            indented = "\n".join(f"  {line}" for line in value.splitlines())
            lines.append(f"{question.label}:\n{indented}")
        else:
            lines.append(f"{question.label}: {value}")
    return "\n".join(lines)


def render_prompt(
    template: str, questions: list[Question], answers: dict[str, str]
) -> str:
    """Substitute ``{answers}`` and ``{question_key}`` tokens into ``template``.

    Unknown tokens render as empty strings rather than raising, so a template
    referencing a since-deleted question still produces usable output. Braces that
    aren't valid identifiers are left alone, so JSON in a template survives.
    """

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == _ALL_ANSWERS:
            return format_answers(questions, answers)
        return answers.get(key, "").strip()

    return _TOKEN.sub(substitute, template).strip()


def unknown_tokens(template: str, questions: list[Question]) -> set[str]:
    """Tokens in ``template`` matching neither a question key nor ``{answers}``."""
    known = {q.key for q in questions} | {_ALL_ANSWERS}
    return set(_TOKEN.findall(template)) - known
