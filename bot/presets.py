"""Ready-made intake configurations, loaded from presets.json.

Lets a server (or a dev restarting the bot) get a working questionnaire without
running ``/intake add-question`` five times.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

from .storage.models import MAX_QUESTIONS, GuildConfig, Question, QuestionStyle

PRESETS_PATH = Path(__file__).with_name("presets.json")


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    name: str
    intake_title: str
    intake_description: str
    prompt_template: str
    questions: tuple[Question, ...]


@cache
def load_presets() -> dict[str, Preset]:
    """Parse and validate presets.json.

    Raises ``ValueError`` on a malformed preset so a typo surfaces at startup
    rather than when a user clicks the button and the modal fails to build.
    """
    raw = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    presets: dict[str, Preset] = {}

    for key, body in raw.items():
        questions = tuple(
            Question(
                key=q["key"],
                label=q["label"],
                style=QuestionStyle(q.get("style", "short")),
                placeholder=q.get("placeholder"),
                required=q.get("required", True),
                choices=list(q.get("choices", [])),
            )
            for q in body["questions"]
        )

        problems = [f"{q.key}: {error}" for q in questions for error in q.validate()]
        if len(questions) > MAX_QUESTIONS:
            problems.append(f"{len(questions)} questions, max is {MAX_QUESTIONS}")
        if len({q.key for q in questions}) != len(questions):
            problems.append("duplicate question keys")
        if problems:
            raise ValueError(f"Preset {key!r} is invalid: {'; '.join(problems)}")

        presets[key] = Preset(
            key=key,
            name=body["name"],
            intake_title=body["intake_title"],
            intake_description=body["intake_description"],
            prompt_template=body["prompt_template"],
            questions=questions,
        )

    return presets


def apply_preset(config: GuildConfig, preset: Preset) -> None:
    """Replace the questionnaire and panel copy in place.

    Watched categories are left alone -- those are server-specific and should
    survive switching presets.
    """
    config.intake_title = preset.intake_title
    config.intake_description = preset.intake_description
    config.prompt_template = preset.prompt_template
    # Copy: presets are cached and shared between guilds, and GuildConfig is
    # mutated in place by the /intake commands.
    config.questions = [replace(q, choices=list(q.choices)) for q in preset.questions]
