"""Plain data models shared by the storage, service and UI layers.

Deliberately free of any discord.py types so the same objects can be served
by a future web dashboard API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

# Discord's hard limits on modal components. Enforced at config time so an
# owner finds out when they add the question, not when a user clicks the button.
MAX_QUESTIONS_PER_PAGE = 5
MAX_LABEL_LEN = 45
MAX_PLACEHOLDER_LEN = 100
MAX_VALUE_LEN = 4000
MAX_TITLE_LEN = 45
MAX_CHOICE_OPTIONS = 25
"""Discord's cap on options in a single select menu."""
MAX_OPTION_LABEL_LEN = 100


class QuestionStyle(str, Enum):
    SHORT = "short"
    PARAGRAPH = "paragraph"
    CHOICE = "choice"
    """Renders as a multi-select dropdown instead of a text box."""


@dataclass(slots=True)
class Question:
    """One text input inside the intake modal."""

    key: str
    """Identifier used in the prompt template as ``{key}``."""

    label: str
    style: QuestionStyle = QuestionStyle.SHORT
    placeholder: str | None = None
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None
    position: int = 0
    page: int = 0
    """Modal number this question appears on. Always 0 today; the chaining
    work lands by allowing page > 0 without touching anything else."""

    choices: list[str] = field(default_factory=list)
    """Dropdown options. Only meaningful when style is CHOICE."""

    @property
    def is_choice(self) -> bool:
        return self.style is QuestionStyle.CHOICE

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.key.isidentifier():
            errors.append(
                f"Question key {self.key!r} must be letters, digits and "
                "underscores only, and cannot start with a digit."
            )
        if not self.label.strip():
            errors.append("Question label cannot be empty.")
        if len(self.label) > MAX_LABEL_LEN:
            errors.append(f"Label must be {MAX_LABEL_LEN} characters or fewer.")
        if self.placeholder and len(self.placeholder) > MAX_PLACEHOLDER_LEN:
            errors.append(
                f"Placeholder must be {MAX_PLACEHOLDER_LEN} characters or fewer."
            )
        if self.max_length is not None and not (1 <= self.max_length <= MAX_VALUE_LEN):
            errors.append(f"Max length must be between 1 and {MAX_VALUE_LEN}.")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            errors.append("Min length cannot exceed max length.")

        if self.is_choice:
            if len(self.choices) < 2:
                errors.append("A choice question needs at least two options.")
            if len(self.choices) > MAX_CHOICE_OPTIONS:
                errors.append(
                    f"Discord allows at most {MAX_CHOICE_OPTIONS} dropdown options."
                )
            if len(set(self.choices)) != len(self.choices):
                errors.append("Dropdown options must be unique.")
            for option in self.choices:
                if len(option) > MAX_OPTION_LABEL_LEN:
                    errors.append(
                        f"Option {option!r} exceeds {MAX_OPTION_LABEL_LEN} characters."
                    )
        elif self.choices:
            errors.append("Only choice questions can have dropdown options.")
        return errors


def parse_choices(raw: str) -> list[str]:
    """Split an admin's comma-separated option list.

    Commas are the separator, so an option containing one is not expressible;
    that is an accepted tradeoff for keeping this a single slash-command field.
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
    """Everything one server has customised about its intake flow."""

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

    def questions_for_page(self, page: int) -> list[Question]:
        return sorted(
            (q for q in self.questions if q.page == page),
            key=lambda q: q.position,
        )

    def question_by_key(self, key: str) -> Question | None:
        return next((q for q in self.questions if q.key == key), None)

    def with_question(self, question: Question) -> GuildConfig:
        """Return a copy with ``question`` appended, renumbering positions."""
        questions = [*self.questions, question]
        for index, existing in enumerate(questions):
            questions[index] = replace(existing, position=index)
        return replace(self, questions=questions)

    def without_question(self, key: str) -> GuildConfig:
        questions = [q for q in self.questions if q.key != key]
        for index, existing in enumerate(questions):
            questions[index] = replace(existing, position=index)
        return replace(self, questions=questions)


@dataclass(slots=True)
class PendingIntake:
    """A ticket channel that is locked and waiting on its opener to submit."""

    guild_id: int
    channel_id: int
    user_id: int
    original_send_messages: bool | None = None
    """The opener's ``send_messages`` overwrite before we locked them, so it can
    be restored exactly. ``None`` means "inherit from category/role"."""

    page: int = 0
    partial_answers: dict[str, str] = field(default_factory=dict)
    """Answers banked from earlier modal pages. Unused until chaining ships."""

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
