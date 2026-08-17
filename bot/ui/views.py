"""Persistent view holding the button that opens the intake modal.

The custom_id is static and the view carries no per-channel state, so a single
registered instance in ``setup_hook`` keeps buttons alive in every open ticket
across restarts.
"""

from __future__ import annotations

from typing import Protocol

import discord

INTAKE_BUTTON_ID = "intake:start"


class IntakeClickHandler(Protocol):
    async def on_intake_clicked(self, interaction: discord.Interaction) -> None: ...


class StartIntakeView(discord.ui.View):
    def __init__(self, handler: IntakeClickHandler) -> None:
        super().__init__(timeout=None)
        self.handler = handler

    @discord.ui.button(
        label="Answer questions",
        style=discord.ButtonStyle.primary,
        emoji="\N{MEMO}",
        custom_id=INTAKE_BUTTON_ID,
    )
    async def start_intake(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.handler.on_intake_clicked(interaction)


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
