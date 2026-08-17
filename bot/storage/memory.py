"""In-memory repository so the bot runs before Postgres is wired up.

State is lost on restart, which for pending intakes means an open ticket's
button stops working after a redeploy. That goes away with the Postgres
implementation; see ``schema.sql``.
"""

from __future__ import annotations

import copy

from .base import Repository
from .models import GuildConfig, IntakeSubmission, PendingIntake


class InMemoryRepository(Repository):
    def __init__(self) -> None:
        self._configs: dict[int, GuildConfig] = {}
        self._pending: dict[int, PendingIntake] = {}
        self._submissions: list[IntakeSubmission] = []

    async def get_guild_config(self, guild_id: int) -> GuildConfig | None:
        config = self._configs.get(guild_id)
        return copy.deepcopy(config) if config else None

    async def save_guild_config(self, config: GuildConfig) -> None:
        self._configs[config.guild_id] = copy.deepcopy(config)

    async def get_pending_intake(self, channel_id: int) -> PendingIntake | None:
        intake = self._pending.get(channel_id)
        return copy.deepcopy(intake) if intake else None

    async def save_pending_intake(self, intake: PendingIntake) -> None:
        self._pending[intake.channel_id] = copy.deepcopy(intake)

    async def delete_pending_intake(self, channel_id: int) -> None:
        self._pending.pop(channel_id, None)

    async def list_pending_channel_ids(self) -> list[int]:
        return list(self._pending)

    async def save_submission(self, submission: IntakeSubmission) -> None:
        self._submissions.append(copy.deepcopy(submission))
