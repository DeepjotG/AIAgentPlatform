"""`/intake` slash commands for server owners.

Every command is a thin wrapper over the repository plus validation in
``storage.models``; the web dashboard will drive the same objects, so keep
business rules out of this file.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..services.intake import IntakeService
from ..services.prompt import render_prompt, unknown_tokens
from ..storage.base import Repository
from ..storage.models import (
    MAX_QUESTIONS_PER_PAGE,
    MAX_VALUE_LEN,
    GuildConfig,
    Question,
    QuestionStyle,
    parse_choices,
)
from ..ui.modals import IntakeModal

log = logging.getLogger(__name__)


class TemplateModal(discord.ui.Modal, title="Prompt template"):
    """Multi-line editing is unpleasant as a slash command argument, so the
    template and panel copy are edited in modals instead."""

    template: discord.ui.TextInput = discord.ui.TextInput(
        label="Template",
        style=discord.TextStyle.paragraph,
        placeholder="Use {answers} for every answer, or {question_key} for one.",
        max_length=MAX_VALUE_LEN,
    )

    def __init__(self, cog: "AdminCog", config: GuildConfig) -> None:
        super().__init__()
        self.cog = cog
        self.config = config
        self.template.default = config.prompt_template

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.config.prompt_template = self.template.value
        await self.cog.repo.save_guild_config(self.config)

        missing = unknown_tokens(self.template.value, self.config.questions)
        note = ""
        if missing:
            listed = ", ".join(f"`{{{token}}}`" for token in sorted(missing))
            note = f"\n\nHeads up -- these don't match any question: {listed}"
        await interaction.response.send_message(
            f"Prompt template saved.{note}", ephemeral=True
        )


class PanelModal(discord.ui.Modal, title="Intake panel"):
    heading: discord.ui.TextInput = discord.ui.TextInput(
        label="Title", max_length=45
    )
    body: discord.ui.TextInput = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, max_length=2000
    )

    def __init__(self, cog: "AdminCog", config: GuildConfig) -> None:
        super().__init__()
        self.cog = cog
        self.config = config
        self.heading.default = config.intake_title
        self.body.default = config.intake_description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.config.intake_title = self.heading.value
        self.config.intake_description = self.body.value
        await self.cog.repo.save_guild_config(self.config)
        await interaction.response.send_message("Panel updated.", ephemeral=True)


class AdminCog(commands.Cog):
    intake = app_commands.Group(
        name="intake",
        description="Configure the ticket intake questionnaire",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(
        self, bot: commands.Bot, repo: Repository, service: IntakeService
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.service = service

    async def _config(self, interaction: discord.Interaction) -> GuildConfig:
        assert interaction.guild_id is not None
        return await self.repo.get_or_create_guild_config(interaction.guild_id)

    # --- ticket categories ---------------------------------------------------

    @intake.command(
        name="add-category",
        description="Treat new channels in this category as Ticket Tool tickets",
    )
    @app_commands.describe(category="The category Ticket Tool creates tickets in")
    async def add_category(
        self, interaction: discord.Interaction, category: discord.CategoryChannel
    ) -> None:
        config = await self._config(interaction)
        if category.id in config.ticket_category_ids:
            await interaction.response.send_message(
                f"**{category.name}** is already watched.", ephemeral=True
            )
            return
        config.ticket_category_ids.append(category.id)
        await self.repo.save_guild_config(config)
        await interaction.response.send_message(
            f"Now watching **{category.name}** for new tickets.", ephemeral=True
        )

    @intake.command(
        name="remove-category", description="Stop watching a ticket category"
    )
    async def remove_category(
        self, interaction: discord.Interaction, category: discord.CategoryChannel
    ) -> None:
        config = await self._config(interaction)
        if category.id not in config.ticket_category_ids:
            await interaction.response.send_message(
                f"**{category.name}** wasn't being watched.", ephemeral=True
            )
            return
        config.ticket_category_ids.remove(category.id)
        await self.repo.save_guild_config(config)
        await interaction.response.send_message(
            f"Stopped watching **{category.name}**.", ephemeral=True
        )

    # --- questions -----------------------------------------------------------

    @intake.command(name="add-question", description="Add a question to the intake")
    @app_commands.describe(
        key="Identifier used in the prompt template as {key}",
        label="The question shown to the user (45 characters max)",
        style="Text box, or a dropdown the user picks from",
        choices="Dropdown options, comma-separated. Only for the Choice style.",
        placeholder="Optional greyed-out hint inside the input",
        required="Whether the user must answer (default: yes)",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Short answer", value="short"),
            app_commands.Choice(name="Paragraph", value="paragraph"),
            app_commands.Choice(name="Choice (dropdown)", value="choice"),
        ]
    )
    async def add_question(
        self,
        interaction: discord.Interaction,
        key: str,
        label: str,
        style: app_commands.Choice[str] | None = None,
        choices: str | None = None,
        placeholder: str | None = None,
        required: bool = True,
    ) -> None:
        config = await self._config(interaction)

        if len(config.questions_for_page(0)) >= MAX_QUESTIONS_PER_PAGE:
            await interaction.response.send_message(
                f"Discord allows at most {MAX_QUESTIONS_PER_PAGE} inputs per modal, "
                "so that's the current limit. Remove one first, or trim two "
                "questions into a single paragraph field.",
                ephemeral=True,
            )
            return

        if config.question_by_key(key) is not None:
            await interaction.response.send_message(
                f"A question with key `{key}` already exists.", ephemeral=True
            )
            return

        resolved_style = QuestionStyle(style.value) if style else QuestionStyle.SHORT
        parsed = parse_choices(choices) if choices else []

        if resolved_style is not QuestionStyle.CHOICE and parsed:
            await interaction.response.send_message(
                "`choices` only applies to the **Choice (dropdown)** style. "
                "Set the style, or drop the options.",
                ephemeral=True,
            )
            return

        question = Question(
            key=key,
            label=label,
            style=resolved_style,
            placeholder=placeholder,
            required=required,
            choices=parsed,
        )
        errors = question.validate()
        if errors:
            await interaction.response.send_message(
                "\n".join(f"- {error}" for error in errors), ephemeral=True
            )
            return

        await self.repo.save_guild_config(config.with_question(question))
        detail = (
            f" Options: {', '.join(parsed)}." if question.is_choice else ""
        )
        await interaction.response.send_message(
            f"Added question `{key}`. Reference it in your template as "
            f"`{{{key}}}`.{detail}",
            ephemeral=True,
        )

    @intake.command(name="remove-question", description="Delete an intake question")
    @app_commands.describe(key="The question's key")
    async def remove_question(
        self, interaction: discord.Interaction, key: str
    ) -> None:
        config = await self._config(interaction)
        if config.question_by_key(key) is None:
            await interaction.response.send_message(
                f"No question with key `{key}`.", ephemeral=True
            )
            return
        await self.repo.save_guild_config(config.without_question(key))
        await interaction.response.send_message(
            f"Removed question `{key}`.", ephemeral=True
        )

    @remove_question.autocomplete("key")
    async def _question_keys(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        config = await self._config(interaction)
        return [
            app_commands.Choice(name=f"{q.key} — {q.label}"[:100], value=q.key)
            for q in config.questions
            if current.lower() in q.key.lower()
        ][:25]

    # --- copy ----------------------------------------------------------------

    @intake.command(name="template", description="Edit the agent prompt template")
    async def template(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        await interaction.response.send_modal(TemplateModal(self, config))

    @intake.command(name="panel", description="Edit the intake panel title and text")
    async def panel(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        await interaction.response.send_modal(PanelModal(self, config))

    # --- inspection ----------------------------------------------------------

    @intake.command(name="status", description="Show the current intake setup")
    async def status(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        embed = discord.Embed(
            title="Intake configuration",
            colour=discord.Colour.blurple() if config.is_ready else discord.Colour.orange(),
        )
        embed.add_field(
            name="Enabled", value="Yes" if config.enabled else "No", inline=True
        )
        embed.add_field(
            name="Ready",
            value="Yes" if config.is_ready else "No — needs a category and a question",
            inline=True,
        )
        categories = (
            "\n".join(f"<#{cid}>" for cid in config.ticket_category_ids) or "_none_"
        )
        embed.add_field(name="Watched categories", value=categories, inline=False)

        if config.questions:
            rows = []
            for i, q in enumerate(config.questions_for_page(0), start=1):
                suffix = "" if q.required else " _(optional)_"
                if q.is_choice:
                    suffix += f"\n    dropdown: {', '.join(q.choices)}"
                rows.append(f"{i}. `{q.key}` — {q.label}{suffix}")
            listing = "\n".join(rows)
        else:
            listing = "_none_"
        embed.add_field(name="Questions", value=listing, inline=False)
        embed.add_field(
            name="Prompt template",
            value=f"```\n{config.prompt_template[:900]}\n```",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @intake.command(
        name="preview", description="Open the intake modal as a user would see it"
    )
    async def preview(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        if not config.questions_for_page(0):
            await interaction.response.send_message(
                "Add a question first with `/intake add-question`.", ephemeral=True
            )
            return

        async def on_preview_submit(
            modal_interaction: discord.Interaction,
            answers: dict[str, str],
            page: int,
        ) -> None:
            rendered = render_prompt(
                config.prompt_template, config.questions, answers
            )
            await modal_interaction.response.send_message(
                f"This is what the agent would receive:\n```\n{rendered[:1800]}\n```",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            IntakeModal(config, 0, on_preview_submit)
        )

    # --- toggles and overrides -----------------------------------------------

    @intake.command(name="toggle", description="Turn the intake gate on or off")
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        config = await self._config(interaction)
        config.enabled = enabled
        await self.repo.save_guild_config(config)
        await interaction.response.send_message(
            f"Intake {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @intake.command(
        name="unlock",
        description="Release this ticket's intake gate without a submission",
    )
    async def unlock(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Run this inside the ticket channel.", ephemeral=True
            )
            return
        intake = await self.repo.get_pending_intake(channel.id)
        if intake is None:
            await interaction.response.send_message(
                "No intake is pending here.", ephemeral=True
            )
            return
        await self.service.abandon(channel)
        await interaction.response.send_message(
            "Intake gate released — the opener can post now.", ephemeral=True
        )
