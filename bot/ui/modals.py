"""The intake modal, built at click time from the guild's configured questions."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord

from ..storage.models import (
    MAX_TITLE_LEN,
    GuildConfig,
    Question,
    QuestionStyle,
)

log = logging.getLogger(__name__)

SubmitCallback = Callable[
    [discord.Interaction, dict[str, str], int], Awaitable[None]
]
"""Called with (interaction, answers, page) once the user submits."""

CHOICE_SEPARATOR = ", "
"""How multi-select answers are joined into the single string an answer holds."""


def _text_input(question: Question) -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label=question.label,
        style=(
            discord.TextStyle.paragraph
            if question.style is QuestionStyle.PARAGRAPH
            else discord.TextStyle.short
        ),
        placeholder=question.placeholder,
        required=question.required,
        min_length=question.min_length,
        max_length=question.max_length,
        custom_id=f"q:{question.key}",
    )


def _choice_select(question: Question) -> discord.ui.Label:
    """A multi-select dropdown. Discord requires selects in a modal to be wrapped
    in a Label, which also supplies the question text -- a Select has only a
    placeholder of its own."""
    select = discord.ui.Select(
        custom_id=f"q:{question.key}",
        placeholder=question.placeholder or "Select all that apply",
        min_values=1 if question.required else 0,
        max_values=len(question.choices),
        required=question.required,
        options=[
            discord.SelectOption(label=choice, value=choice)
            for choice in question.choices
        ],
    )
    return discord.ui.Label(text=question.label, component=select)


def _build_field(question: Question) -> discord.ui.Item:
    return _choice_select(question) if question.is_choice else _text_input(question)


class IntakeModal(discord.ui.Modal):
    """One page of intake questions.

    Discord caps a modal at five inputs, so ``page`` exists to support chaining
    later. Today every configured question lives on page 0.
    """

    def __init__(
        self,
        config: GuildConfig,
        page: int,
        on_submit: SubmitCallback,
    ) -> None:
        super().__init__(title=config.intake_title[:MAX_TITLE_LEN], timeout=None)
        self.page = page
        self._on_submit = on_submit
        self._inputs: dict[str, discord.ui.TextInput | discord.ui.Select] = {}

        for question in config.questions_for_page(page):
            field = _build_field(question)
            self.add_item(field)
            # A choice question's answer lives on the Select inside the Label,
            # not on the Label itself.
            self._inputs[question.key] = (
                field.component if isinstance(field, discord.ui.Label) else field
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers: dict[str, str] = {}
        for key, field in self._inputs.items():
            if isinstance(field, discord.ui.Select):
                answers[key] = CHOICE_SEPARATOR.join(field.values)
            else:
                answers[key] = field.value
        await self._on_submit(interaction, answers, self.page)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("Intake modal failed", exc_info=error)
        message = "Something went wrong saving your answers. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
