"""`/intake` slash commands for server owners.

Thin wrappers over the repository plus ``Question.validate``; the web dashboard
will drive the same objects, so keep business rules out of this file.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..presets import apply_preset, load_presets
from ..services.intake import IntakeService
from ..services.prompt import render_prompt, unknown_tokens
from ..storage.base import Repository
from ..storage.models import (
    MAX_QUESTIONS,
    MAX_TEMPLATE_LEN,
    GuildConfig,
    Question,
    QuestionStyle,
    parse_choices,
)
from ..ui import IntakeModal


class TemplateModal(discord.ui.Modal, title="Prompt template"):
    """Multi-line text is unpleasant as a slash-command argument, so the template
    and panel copy are edited in modals instead."""

    template: discord.ui.TextInput = discord.ui.TextInput(
        label="Template",
        style=discord.TextStyle.paragraph,
        placeholder="Use {answers} for every answer, or {question_key} for one.",
        max_length=MAX_TEMPLATE_LEN,
    )

    def __init__(self, repo: Repository, config: GuildConfig) -> None:
        super().__init__()
        self.repo = repo
        self.config = config
        self.template.default = config.prompt_template

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.config.prompt_template = self.template.value
        await self.repo.save_guild_config(self.config)

        stray = unknown_tokens(self.template.value, self.config.questions)
        note = ""
        if stray:
            listed = ", ".join(f"`{{{token}}}`" for token in sorted(stray))
            note = f"\n\nHeads up -- these match no question: {listed}"
        await interaction.response.send_message(
            f"Prompt template saved.{note}", ephemeral=True
        )


class PanelModal(discord.ui.Modal, title="Intake panel"):
    heading: discord.ui.TextInput = discord.ui.TextInput(label="Title", max_length=45)
    body: discord.ui.TextInput = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, max_length=2000
    )

    def __init__(self, repo: Repository, config: GuildConfig) -> None:
        super().__init__()
        self.repo = repo
        self.config = config
        self.heading.default = config.intake_title
        self.body.default = config.intake_description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.config.intake_title = self.heading.value
        self.config.intake_description = self.body.value
        await self.repo.save_guild_config(self.config)
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
        assert interaction.guild_id is not None  # every command is guild_only
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

    @intake.command(name="remove-category", description="Stop watching a category")
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

        if len(config.questions) >= MAX_QUESTIONS:
            await interaction.response.send_message(
                f"Discord allows at most {MAX_QUESTIONS} inputs per modal, so that's "
                "the limit. Remove one first, or merge two into a paragraph field.",
                ephemeral=True,
            )
            return

        if config.question(key) is not None:
            await interaction.response.send_message(
                f"A question with key `{key}` already exists.", ephemeral=True
            )
            return

        question = Question(
            key=key,
            label=label,
            style=QuestionStyle(style.value) if style else QuestionStyle.SHORT,
            placeholder=placeholder,
            required=required,
            choices=parse_choices(choices) if choices else [],
        )
        errors = question.validate()
        if errors:
            await interaction.response.send_message(
                "\n".join(f"- {error}" for error in errors), ephemeral=True
            )
            return

        config.questions.append(question)
        await self.repo.save_guild_config(config)

        detail = f" Options: {', '.join(question.choices)}." if question.is_choice else ""
        await interaction.response.send_message(
            f"Added question `{key}`. Reference it as `{{{key}}}`.{detail}",
            ephemeral=True,
        )

    @intake.command(name="remove-question", description="Delete an intake question")
    @app_commands.describe(key="The question's key")
    async def remove_question(self, interaction: discord.Interaction, key: str) -> None:
        config = await self._config(interaction)
        if config.question(key) is None:
            await interaction.response.send_message(
                f"No question with key `{key}`.", ephemeral=True
            )
            return

        config.questions = [q for q in config.questions if q.key != key]
        await self.repo.save_guild_config(config)
        await interaction.response.send_message(
            f"Removed question `{key}`.", ephemeral=True
        )

    @remove_question.autocomplete("key")
    async def _question_keys(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        assert interaction.guild_id is not None
        # Read-only: autocomplete fires on every keystroke and must not create rows.
        config = await self.repo.get_guild_config(interaction.guild_id)
        if config is None:
            return []
        return [
            app_commands.Choice(name=f"{q.key} — {q.label}"[:100], value=q.key)
            for q in config.questions
            if current.lower() in q.key.lower()
        ]

    @intake.command(
        name="preset", description="Replace the questions with a ready-made set"
    )
    @app_commands.describe(name="Which preset to apply")
    async def preset(self, interaction: discord.Interaction, name: str) -> None:
        presets = load_presets()
        chosen = presets.get(name)
        if chosen is None:
            await interaction.response.send_message(
                f"Unknown preset. Available: {', '.join(sorted(presets))}",
                ephemeral=True,
            )
            return

        config = await self._config(interaction)
        apply_preset(config, chosen)
        await self.repo.save_guild_config(config)

        hint = (
            "Run `/intake preview` to try it."
            if config.ticket_category_ids
            else "Now run `/intake add-category` to point it at your ticket category."
        )
        await interaction.response.send_message(
            f"Applied **{chosen.name}** ({len(chosen.questions)} questions). {hint}",
            ephemeral=True,
        )

    @preset.autocomplete("name")
    async def _preset_names(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        typed = current.lower()
        return [
            app_commands.Choice(
                name=f"{p.name} — {len(p.questions)} questions"[:100], value=p.key
            )
            for p in load_presets().values()
            if typed in p.key.lower() or typed in p.name.lower()
        ]

    # --- copy ----------------------------------------------------------------

    @intake.command(name="template", description="Edit the agent prompt template")
    async def template(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        await interaction.response.send_modal(TemplateModal(self.repo, config))

    @intake.command(name="panel", description="Edit the intake panel title and text")
    async def panel(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        await interaction.response.send_modal(PanelModal(self.repo, config))

    # --- inspection ----------------------------------------------------------

    @intake.command(name="status", description="Show the current intake setup")
    async def status(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        embed = discord.Embed(
            title="Intake configuration",
            colour=discord.Colour.blurple()
            if config.is_ready
            else discord.Colour.orange(),
        )
        embed.add_field(name="Enabled", value="Yes" if config.enabled else "No")
        embed.add_field(
            name="Ready",
            value="Yes" if config.is_ready else "No — needs a category and a question",
        )
        embed.add_field(
            name="Watched categories",
            value="\n".join(f"<#{cid}>" for cid in config.ticket_category_ids)
            or "_none_",
            inline=False,
        )
        embed.add_field(
            name="Questions", value=self._describe_questions(config), inline=False
        )
        embed.add_field(
            name="Prompt template",
            value=f"```\n{config.prompt_template[:900]}\n```",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    def _describe_questions(config: GuildConfig) -> str:
        if not config.questions:
            return "_none_"
        rows = []
        for index, question in enumerate(config.questions, start=1):
            row = f"{index}. `{question.key}` — {question.label}"
            if not question.required:
                row += " _(optional)_"
            if question.is_choice:
                row += f"\n    dropdown: {', '.join(question.choices)}"
            rows.append(row)
        return "\n".join(rows)

    @intake.command(
        name="preview", description="Open the intake modal as a user would see it"
    )
    async def preview(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction)
        if not config.questions:
            await interaction.response.send_message(
                "Add a question first with `/intake add-question`.", ephemeral=True
            )
            return

        async def show_prompt(
            modal_interaction: discord.Interaction, answers: dict[str, str]
        ) -> None:
            rendered = render_prompt(config.prompt_template, config.questions, answers)
            await modal_interaction.response.send_message(
                f"This is what the agent would receive:\n```\n{rendered[:1800]}\n```",
                ephemeral=True,
            )

        await interaction.response.send_modal(IntakeModal(config, show_prompt))

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
        name="unlock", description="Release this ticket's gate without a submission"
    )
    async def unlock(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Run this inside the ticket channel.", ephemeral=True
            )
            return

        if not self.service.is_gated(channel.id):
            await interaction.response.send_message(
                "No intake is pending here.", ephemeral=True
            )
            return

        await self.service.abandon(channel)
        await interaction.response.send_message(
            "Intake gate released — the opener can post now.", ephemeral=True
        )
