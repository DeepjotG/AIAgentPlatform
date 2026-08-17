"""Storage interface.

Everything above this layer talks to ``Repository`` only, so swapping the
in-memory implementation for Postgres is a one-line change in ``main.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import GuildConfig, IntakeSubmission, PendingIntake


class Repository(ABC):
    async def setup(self) -> None:
        """Open connections / create tables. No-op for in-memory."""

    async def teardown(self) -> None:
        """Close connections. No-op for in-memory."""

    # --- guild configuration -------------------------------------------------

    @abstractmethod
    async def get_guild_config(self, guild_id: int) -> GuildConfig | None: ...

    @abstractmethod
    async def save_guild_config(self, config: GuildConfig) -> None: ...

    async def get_or_create_guild_config(self, guild_id: int) -> GuildConfig:
        config = await self.get_guild_config(guild_id)
        if config is None:
            config = GuildConfig(guild_id=guild_id)
            await self.save_guild_config(config)
        return config

    # --- in-flight intakes ---------------------------------------------------

    @abstractmethod
    async def get_pending_intake(self, channel_id: int) -> PendingIntake | None: ...

    @abstractmethod
    async def save_pending_intake(self, intake: PendingIntake) -> None: ...

    @abstractmethod
    async def delete_pending_intake(self, channel_id: int) -> None: ...

    @abstractmethod
    async def list_pending_channel_ids(self) -> list[int]:
        """Every channel currently gated. Read once at startup to populate the
        service's in-memory index; see ``IntakeService.hydrate``."""

    # --- completed intakes ---------------------------------------------------

    @abstractmethod
    async def save_submission(self, submission: IntakeSubmission) -> None: ...
