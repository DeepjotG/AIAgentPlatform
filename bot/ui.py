"""Discord UI components: the intake button and the modal it opens."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord

from .storage.models import MAX_TITLE_LEN, GuildConfig, Question, QuestionStyle

log = logging.getLogger(__name__)

INTAKE_BUTTON_ID = "intake:start"

ClickHandler = Callable[[discord.Interaction], Awaitable[None]]
SubmitHandler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]


class StartIntakeView(discord.ui.View):
    """The button posted into a new ticket.

    Persistent: a fixed custom_id and no per-channel state mean one registered
    instance keeps the button alive in every open ticket across restarts.
    """

    def __init__(self, on_click: ClickHandler) -> None:
        super().__init__(timeout=None)
        self.on_click = on_click

    @discord.ui.button(
        label="Answer questions",
        style=discord.ButtonStyle.primary,
        emoji="\N{MEMO}",
        custom_id=INTAKE_BUTTON_ID,
    )
    async def start_intake(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.on_click(interaction)


def completed_view() -> discord.ui.View:
    """A disabled stand-in swapped onto the prompt message after submission."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Submitted",
            style=discord.ButtonStyle.secondary,
            emoji="\N{WHITE HEAVY CHECK MARK}",
            disabled=True,
            custom_id="intake:done",
        )
    )
    return view


def _build_field(question: Question) -> discord.ui.Item:
    """The modal component for one question.

    Choice questions return a Select wrapped in a Label: a Select carries only a
    placeholder, so the Label supplies the question text.
    """
    if not question.is_choice:
        return discord.ui.TextInput(
            label=question.label,
            style=(
                discord.TextStyle.paragraph
                if question.style is QuestionStyle.PARAGRAPH
                else discord.TextStyle.short
            ),
            placeholder=question.placeholder,
            required=question.required,
            custom_id=f"q:{question.key}",
        )

    select = discord.ui.Select(
        custom_id=f"q:{question.key}",
        placeholder=question.placeholder or "Select all that apply",
        min_values=1 if question.required else 0,
        max_values=len(question.choices),
        required=question.required,
        options=[discord.SelectOption(label=c, value=c) for c in question.choices],
    )
    return discord.ui.Label(text=question.label, component=select)


class IntakeModal(discord.ui.Modal):
    """The questionnaire, built at click time from the guild's current questions."""

    def __init__(self, config: GuildConfig, on_submit: SubmitHandler) -> None:
        super().__init__(title=config.intake_title[:MAX_TITLE_LEN], timeout=None)
        self._on_submit = on_submit
        self._fields: dict[str, discord.ui.TextInput | discord.ui.Select] = {}

        for question in config.questions:
            item = _build_field(question)
            self.add_item(item)
            # A choice answer lives on the Select inside the Label, not the Label.
            self._fields[question.key] = (
                item.component if isinstance(item, discord.ui.Label) else item
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = {
            key: ", ".join(field.values)
            if isinstance(field, discord.ui.Select)
            else field.value
            for key, field in self._fields.items()
        }
        await self._on_submit(interaction, answers)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("Intake modal failed", exc_info=error)
        message = "Something went wrong saving your answers. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
