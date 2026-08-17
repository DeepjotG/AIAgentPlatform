"""Plain data models shared by the storage, service and UI layers.

Deliberately free of discord.py types so the same objects can be served by a
future web dashboard API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

# Discord's hard limits. Enforced when a question is added so an owner finds out
# then, rather than when a user clicks the button and the modal fails to build.
MAX_QUESTIONS = 5
MAX_LABEL_LEN = 45
MAX_PLACEHOLDER_LEN = 100
MAX_TITLE_LEN = 45
MAX_TEMPLATE_LEN = 4000
MAX_CHOICES = 25
MAX_CHOICE_LEN = 100


class QuestionStyle(str, Enum):
    SHORT = "short"
    PARAGRAPH = "paragraph"
    CHOICE = "choice"
    """Renders as a multi-select dropdown instead of a text box."""


@dataclass(slots=True)
class Question:
    """One field in the intake modal."""

    key: str
    """Identifier used in the prompt template as ``{key}``."""

    label: str
    style: QuestionStyle = QuestionStyle.SHORT
    placeholder: str | None = None
    required: bool = True
    choices: list[str] = field(default_factory=list)
    """Dropdown options, ordered. Only meaningful when style is CHOICE."""

    @property
    def is_choice(self) -> bool:
        return self.style is QuestionStyle.CHOICE

    def validate(self) -> list[str]:
        """Every reason this question would be rejected; empty when it is valid."""
        errors: list[str] = []

        if not self.key.isidentifier():
            errors.append(
                f"Key {self.key!r} must be letters, digits and underscores only, "
                "and cannot start with a digit."
            )
        if not self.label.strip():
            errors.append("Label cannot be empty.")
        elif len(self.label) > MAX_LABEL_LEN:
            errors.append(f"Label must be {MAX_LABEL_LEN} characters or fewer.")
        if self.placeholder and len(self.placeholder) > MAX_PLACEHOLDER_LEN:
            errors.append(
                f"Placeholder must be {MAX_PLACEHOLDER_LEN} characters or fewer."
            )

        if not self.is_choice:
            if self.choices:
                errors.append("Only choice questions can have dropdown options.")
            return errors

        if len(self.choices) < 2:
            errors.append("A choice question needs at least two options.")
        if len(self.choices) > MAX_CHOICES:
            errors.append(f"Discord allows at most {MAX_CHOICES} dropdown options.")
        if len(set(self.choices)) != len(self.choices):
            errors.append("Dropdown options must be unique.")
        if any(len(choice) > MAX_CHOICE_LEN for choice in self.choices):
            errors.append(f"Each option must be {MAX_CHOICE_LEN} characters or fewer.")
        return errors


def parse_choices(raw: str) -> list[str]:
    """Split an admin's comma-separated option list.

    Commas are the separator, so an option containing one is not expressible.
    That is the accepted cost of keeping this a single slash-command field.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


DEFAULT_INTAKE_TITLE = "Before we begin"
DEFAULT_INTAKE_DESCRIPTION = (
    "Please answer a few quick questions so we can help you faster. "
    "You'll be able to talk in this channel once you submit."
)
DEFAULT_PROMPT_TEMPLATE = "A user opened a support ticket.\n\n{answers}"


@dataclass(slots=True)
class GuildConfig:
    """One server's intake setup.

    ``questions`` is ordered -- its order is the order fields appear in the modal.
    """

    guild_id: int
    enabled: bool = True
    ticket_category_ids: list[int] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    intake_title: str = DEFAULT_INTAKE_TITLE
    intake_description: str = DEFAULT_INTAKE_DESCRIPTION
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE

    @property
    def is_ready(self) -> bool:
        """Whether this guild has enough set up for the flow to run at all."""
        return self.enabled and bool(self.ticket_category_ids) and bool(self.questions)

    def question(self, key: str) -> Question | None:
        return next((q for q in self.questions if q.key == key), None)


@dataclass(slots=True)
class PendingIntake:
    """A ticket channel locked while waiting on its opener to submit."""

    guild_id: int
    channel_id: int
    user_id: int
    original_send_messages: bool | None = None
    """The opener's ``send_messages`` overwrite before we locked them, so it can be
    restored exactly. ``None`` (inherit) differs meaningfully from ``False``."""

    prompt_message_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class IntakeSubmission:
    """A completed intake, ready to hand to the agent."""

    guild_id: int
    channel_id: int
    user_id: int
    answers: dict[str, str]
    rendered_prompt: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
