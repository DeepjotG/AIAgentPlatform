"""Core intake orchestration: identify the ticket opener, lock them out of the
channel until they submit, then restore exactly the permissions they had.

The cog is a thin adapter over this; a future dashboard API calls the same
methods.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from ..storage.base import Repository
from ..storage.models import GuildConfig, IntakeSubmission, PendingIntake
from .prompt import render_prompt

log = logging.getLogger(__name__)

LOCK_REASON = "Ticket intake pending"
UNLOCK_REASON = "Ticket intake submitted"


class IntakeService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self._gated_channels: set[int] = set()
        """Channel ids with an intake pending. `on_message` fires for every
        message in every guild, so it consults this set before touching storage."""

    async def hydrate(self) -> None:
        """Rebuild the in-memory index from storage. Call once on startup so
        tickets left open across a restart stay gated."""
        self._gated_channels = set(await self.repo.list_pending_channel_ids())
        log.info("Restored %d pending intake(s)", len(self._gated_channels))

    def is_gated(self, channel_id: int) -> bool:
        return channel_id in self._gated_channels

    # --- identifying who opened the ticket -----------------------------------

    async def resolve_opener(
        self,
        channel: discord.TextChannel,
        *,
        attempts: int = 8,
        delay: float = 0.5,
    ) -> discord.Member | None:
        """Find the member Ticket Tool granted access to when it made the channel.

        Ticket Tool may create the channel and add the opener's permission
        overwrite in two separate API calls, so poll briefly rather than reading
        overwrites once and giving up.

        Members already present on the parent category are excluded: when a
        ticket channel is synced to its category, the category's staff overwrites
        are copied onto the channel and would otherwise look like candidates.
        """
        for attempt in range(attempts):
            current = channel.guild.get_channel(channel.id)
            if current is None:  # channel deleted mid-poll
                return None
            assert isinstance(current, discord.abc.GuildChannel)

            inherited = set(current.category.overwrites) if current.category else set()
            candidates = [
                target
                for target in current.overwrites
                if isinstance(target, discord.Member)
                and not target.bot
                and target not in inherited
            ]

            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                log.warning(
                    "Ambiguous ticket opener in #%s (%s): %d candidates, skipping intake",
                    current.name,
                    current.id,
                    len(candidates),
                )
                return None

            if attempt < attempts - 1:
                await asyncio.sleep(delay)

        log.info(
            "No ticket opener found for #%s (%s) after %d attempts",
            channel.name,
            channel.id,
            attempts,
        )
        return None

    # --- permission gating ---------------------------------------------------

    async def _lock(
        self, channel: discord.TextChannel, member: discord.Member
    ) -> bool | None:
        """Deny ``send_messages`` for ``member``. Returns their previous value so
        it can be restored verbatim -- ``None`` (inherit) is meaningfully
        different from ``False`` (explicitly denied) and must survive the round
        trip."""
        overwrite = channel.overwrites_for(member)
        original = overwrite.send_messages
        overwrite.send_messages = False
        await channel.set_permissions(member, overwrite=overwrite, reason=LOCK_REASON)
        return original

    async def _unlock(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        original: bool | None,
    ) -> None:
        overwrite = channel.overwrites_for(member)
        overwrite.send_messages = original
        if overwrite.is_empty():
            # Nothing left to say about this member; drop the overwrite entirely
            # rather than leaving an empty one behind.
            await channel.set_permissions(member, overwrite=None, reason=UNLOCK_REASON)
        else:
            await channel.set_permissions(
                member, overwrite=overwrite, reason=UNLOCK_REASON
            )

    # --- lifecycle -----------------------------------------------------------

    async def begin(
        self, channel: discord.TextChannel, member: discord.Member
    ) -> PendingIntake:
        """Lock the channel for ``member`` and record the pending intake."""
        original = await self._lock(channel, member)
        intake = PendingIntake(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            user_id=member.id,
            original_send_messages=original,
        )
        await self.repo.save_pending_intake(intake)
        self._gated_channels.add(channel.id)
        log.info(
            "Intake started for %s in #%s (%s)", member, channel.name, channel.id
        )
        return intake

    async def complete(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        config: GuildConfig,
        intake: PendingIntake,
        answers: dict[str, str],
    ) -> IntakeSubmission:
        """Persist the answers, unlock the channel, and return the submission.

        Handing the rendered prompt to the agent happens at the call site.
        """
        merged = {**intake.partial_answers, **answers}
        submission = IntakeSubmission(
            guild_id=channel.guild.id,
            channel_id=channel.id,
            user_id=member.id,
            answers=merged,
            rendered_prompt=render_prompt(
                config.prompt_template, config.questions, merged
            ),
        )
        await self.repo.save_submission(submission)
        await self._unlock(channel, member, intake.original_send_messages)
        await self.repo.delete_pending_intake(channel.id)
        self._gated_channels.discard(channel.id)
        log.info("Intake completed by %s in #%s", member, channel.name)
        return submission

    async def abandon(self, channel: discord.TextChannel) -> None:
        """Drop a pending intake and restore permissions without a submission.

        Used when the ticket is closed, or an admin overrides the gate.
        """
        intake = await self.repo.get_pending_intake(channel.id)
        if intake is None:
            return
        member = channel.guild.get_member(intake.user_id)
        if member is not None:
            await self._unlock(channel, member, intake.original_send_messages)
        await self.repo.delete_pending_intake(channel.id)
        self._gated_channels.discard(channel.id)

    async def forget(self, channel_id: int) -> None:
        """Drop pending state with no permission work -- the channel is gone."""
        if channel_id not in self._gated_channels:
            return
        await self.repo.delete_pending_intake(channel_id)
        self._gated_channels.discard(channel_id)
