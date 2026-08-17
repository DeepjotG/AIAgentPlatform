"""Reacts to Ticket Tool creating a ticket channel, gates it behind the intake
modal, and reopens it once the opener submits.

Ticket Tool's own button click never reaches this bot -- Discord routes component
interactions only to the application that owns the component -- so the flow is
driven by our own button, posted into the channel after it appears.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..services.intake import IntakeService
from ..storage.base import Repository
from ..storage.models import GuildConfig, IntakeSubmission
from ..ui import IntakeModal, StartIntakeView, completed_view

log = logging.getLogger(__name__)

NUDGE_SECONDS = 8.0


class TicketsCog(commands.Cog):
    def __init__(
        self, bot: commands.Bot, repo: Repository, service: IntakeService
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.service = service

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel):
            return

        config = await self.repo.get_guild_config(channel.guild.id)
        if config is None or not config.is_ready:
            return
        if channel.category_id not in config.ticket_category_ids:
            return

        member = await self.service.resolve_opener(channel)
        if member is None:
            return

        try:
            intake = await self.service.begin(channel, member)
        except discord.Forbidden:
            log.warning("Missing Manage Permissions in #%s; cannot gate", channel.name)
            return

        try:
            message = await channel.send(
                content=member.mention,
                embed=self._prompt_embed(config),
                view=StartIntakeView(self.on_intake_clicked),
            )
        except discord.HTTPException:
            log.exception("Could not post intake prompt in #%s", channel.name)
            await self.service.abandon(channel)
            return

        intake.prompt_message_id = message.id
        await self.repo.save_pending_intake(intake)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.service.forget(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Backstop for the gap between Ticket Tool creating the channel and us
        locking it, and for openers whose roles bypass channel overwrites."""
        if message.guild is None or message.author.bot:
            return
        if not self.service.is_gated(message.channel.id):
            return

        intake = await self.repo.get_pending_intake(message.channel.id)
        if intake is None or message.author.id != intake.user_id:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            return

        await message.channel.send(
            f"{message.author.mention} please answer the questions above first "
            "-- the ticket opens up as soon as you submit.",
            delete_after=NUDGE_SECONDS,
        )

    async def on_intake_clicked(self, interaction: discord.Interaction) -> None:
        """Validate the clicker owns this ticket, then show them the questions."""
        if interaction.channel_id is None or interaction.guild_id is None:
            return

        intake = await self.repo.get_pending_intake(interaction.channel_id)
        if intake is None:
            await interaction.response.send_message(
                "This intake is no longer active.", ephemeral=True
            )
            return

        if interaction.user.id != intake.user_id:
            await interaction.response.send_message(
                "Only the person who opened this ticket can fill this in.",
                ephemeral=True,
            )
            return

        config = await self.repo.get_guild_config(interaction.guild_id)
        if config is None or not config.questions:
            await interaction.response.send_message(
                "This server has no intake questions configured. "
                "Ask an admin to run `/intake add-question`.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(IntakeModal(config, self._on_submitted))

    async def _on_submitted(
        self, interaction: discord.Interaction, answers: dict[str, str]
    ) -> None:
        # set_permissions can be rate limited; defer to stay inside Discord's
        # three second response window.
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        member = interaction.user
        if not isinstance(channel, discord.TextChannel) or not isinstance(
            member, discord.Member
        ):
            return

        intake = await self.repo.get_pending_intake(channel.id)
        config = await self.repo.get_guild_config(channel.guild.id)
        if intake is None or config is None:
            await interaction.followup.send(
                "This intake is no longer active.", ephemeral=True
            )
            return

        submission = await self.service.complete(
            channel, member, config, intake, answers
        )

        if intake.prompt_message_id is not None:
            try:
                prompt = await channel.fetch_message(intake.prompt_message_id)
                await prompt.edit(view=completed_view())
            except discord.HTTPException:
                pass

        await channel.send(embed=self._summary_embed(config, submission, member))
        await interaction.followup.send(
            "Thanks -- you can now send messages in this ticket.", ephemeral=True
        )

        # TODO: hand submission.rendered_prompt to the agent.
        log.info("Rendered prompt for #%s:\n%s", channel.name, submission.rendered_prompt)

    @staticmethod
    def _prompt_embed(config: GuildConfig) -> discord.Embed:
        return discord.Embed(
            title=config.intake_title,
            description=config.intake_description,
            colour=discord.Colour.blurple(),
        )

    @staticmethod
    def _summary_embed(
        config: GuildConfig, submission: IntakeSubmission, member: discord.Member
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Ticket details",
            colour=discord.Colour.green(),
            timestamp=submission.submitted_at,
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        for question in config.questions:
            value = submission.answers.get(question.key, "").strip()
            embed.add_field(
                name=question.label[:256],
                value=(value or "_(skipped)_")[:1024],
                inline=False,
            )
        return embed
