"""Core intake orchestration: identify the ticket opener, lock them out of the
channel until they submit, then restore exactly the permissions they had.

The cog is a thin adapter over this; a future dashboard API calls the same methods.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from ..storage.base import Repository
from ..storage.models import GuildConfig, IntakeSubmission, PendingIntake
from .prompt import render_prompt

log = logging.getLogger(__name__)


class IntakeService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self._gated_channels: set[int] = set()
        """Channels with an intake pending. ``on_message`` fires for every message
        the bot can see, so it checks this set before querying storage."""

    async def hydrate(self) -> None:
        """Rebuild the gate index from storage so tickets left open across a
        restart stay gated. Call once on startup."""
        self._gated_channels = set(await self.repo.list_pending_channel_ids())
        log.info("Restored %d pending intake(s)", len(self._gated_channels))

    def is_gated(self, channel_id: int) -> bool:
        return channel_id in self._gated_channels

    async def resolve_opener(
        self, channel: discord.TextChannel, *, attempts: int = 8, delay: float = 0.5
    ) -> discord.Member | None:
        """Find the member Ticket Tool granted access to when it made the channel.

        Ticket Tool may create the channel and add the opener's overwrite in two
        separate API calls, so poll briefly rather than reading overwrites once.
        The channel object is updated in place by the gateway, so re-reading it
        each attempt is enough.

        Members inherited from the parent category are excluded: a ticket channel
        synced to its category copies the category's staff overwrites, which would
        otherwise look like candidates.
        """
        for attempt in range(attempts):
            inherited = set(channel.category.overwrites) if channel.category else set()
            candidates = [
                target
                for target in channel.overwrites
                if isinstance(target, discord.Member)
                and not target.bot
                and target not in inherited
            ]

            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                log.warning(
                    "Ambiguous opener in #%s: %d candidates, skipping intake",
                    channel.name,
                    len(candidates),
                )
                return None
            if attempt < attempts - 1:
                await asyncio.sleep(delay)

        log.info("No ticket opener appeared in #%s, skipping intake", channel.name)
        return None

    async def begin(
        self, channel: discord.TextChannel, member: discord.Member
    ) -> PendingIntake:
        """Deny the opener ``send_messages`` and record the pending intake.

        Their previous value is stored so it can be restored verbatim -- ``None``
        (inherit) is meaningfully different from ``False`` (explicitly denied).
        """
        overwrite = channel.overwrites_for(member)
        original = overwrite.send_messages
        overwrite.send_messages = False
        await channel.set_permissions(
            member, overwrite=overwrite, reason="Ticket intake pending"
        )

        intake = PendingIntake(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            user_id=member.id,
            original_send_messages=original,
        )
        await self.repo.save_pending_intake(intake)
        self._gated_channels.add(channel.id)
        log.info("Intake started for %s in #%s", member, channel.name)
        return intake

    async def complete(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        config: GuildConfig,
        intake: PendingIntake,
        answers: dict[str, str],
    ) -> IntakeSubmission:
        """Store the answers, unlock the channel, and return the submission.

        Handing the rendered prompt to the agent happens at the call site.
        """
        submission = IntakeSubmission(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            user_id=member.id,
            answers=answers,
            rendered_prompt=render_prompt(
                config.prompt_template, config.questions, answers
            ),
        )
        await self.repo.save_submission(submission)
        await self._release(channel, member, intake)
        log.info("Intake completed by %s in #%s", member, channel.name)
        return submission

    async def abandon(self, channel: discord.TextChannel) -> None:
        """Unlock without a submission -- staff override, or a failed setup."""
        intake = await self.repo.get_pending_intake(channel.id)
        if intake is None:
            return
        member = channel.guild.get_member(intake.user_id)
        await self._release(channel, member, intake)

    async def forget(self, channel_id: int) -> None:
        """Drop pending state with no permission work -- the channel is gone."""
        if channel_id not in self._gated_channels:
            return
        await self.repo.delete_pending_intake(channel_id)
        self._gated_channels.discard(channel_id)

    async def _release(
        self,
        channel: discord.TextChannel,
        member: discord.Member | None,
        intake: PendingIntake,
    ) -> None:
        """Restore the opener's original permission and clear the gate."""
        if member is not None:
            overwrite = channel.overwrites_for(member)
            overwrite.send_messages = intake.original_send_messages
            # An overwrite saying nothing is noise; drop it rather than leave an
            # empty one behind on every ticket.
            await channel.set_permissions(
                member,
                overwrite=None if overwrite.is_empty() else overwrite,
                reason="Ticket intake resolved",
            )
        await self.repo.delete_pending_intake(channel.id)
        self._gated_channels.discard(channel.id)
