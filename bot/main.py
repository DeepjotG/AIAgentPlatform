"""Entrypoint. Run with `python -m bot`."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from .cogs.admin import AdminCog
from .cogs.tickets import TicketsCog
from .presets import apply_preset, load_presets
from .services.intake import IntakeService
from .storage.base import Repository
from .storage.memory import InMemoryRepository
from .ui import StartIntakeView

log = logging.getLogger(__name__)


class IntakeBot(commands.Bot):
    def __init__(self, repo: Repository) -> None:
        intents = discord.Intents.default()
        # Required: channel.overwrites can only resolve a Member target if that
        # member is cached, and identifying the ticket opener depends on it.
        # Enable "Server Members Intent" in the Discord developer portal too.
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.repo = repo
        self.service = IntakeService(repo)

    async def setup_hook(self) -> None:
        await self.repo.setup()
        await self.service.hydrate()

        tickets = TicketsCog(self, self.repo, self.service)
        await self.add_cog(tickets)
        await self.add_cog(AdminCog(self, self.repo, self.service))

        # Re-registers the intake button so it keeps working in tickets that
        # were already open when the bot restarted.
        self.add_view(StartIntakeView(tickets.on_intake_clicked))

        await self._seed_dev_guild()

        # Global command sync can take up to an hour to propagate. Set
        # DEV_GUILD_ID while developing for an instant, guild-scoped sync.
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to dev guild %s", dev_guild)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

    async def _seed_dev_guild(self) -> None:
        """Apply a preset on boot so a restart doesn't mean re-running /intake.

        Only runs when DEV_GUILD_ID and DEV_PRESET are both set; storage is
        in-memory today, so config would otherwise be wiped on every restart.
        """
        guild_id = os.getenv("DEV_GUILD_ID")
        preset_key = os.getenv("DEV_PRESET")
        if not guild_id or not preset_key:
            return

        presets = load_presets()
        preset = presets.get(preset_key)
        if preset is None:
            log.warning(
                "DEV_PRESET=%r is not a known preset. Available: %s",
                preset_key,
                ", ".join(sorted(presets)),
            )
            return

        config = await self.repo.get_or_create_guild_config(int(guild_id))
        apply_preset(config, preset)

        category_id = os.getenv("DEV_TICKET_CATEGORY_ID")
        if category_id and int(category_id) not in config.ticket_category_ids:
            config.ticket_category_ids.append(int(category_id))

        await self.repo.save_guild_config(config)
        log.info(
            "Seeded guild %s with preset %r (ready=%s)",
            guild_id,
            preset_key,
            config.is_ready,
        )

    async def close(self) -> None:
        await self.repo.teardown()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set (check your .env)")

    # Swap for PostgresRepository once the database is up; nothing else changes.
    bot = IntakeBot(InMemoryRepository())

    try:
        bot.run(token, log_handler=None)
    except KeyboardInterrupt:
        pass
