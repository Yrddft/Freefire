from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_APPROVAL_ROLE_ID = 1564884044205006849


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    database_url: str
    approval_role_id: int
    dev_guild_id: int | None

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv()

        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        database_url = (
            os.getenv("RENDER_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
        ).strip()
        if not token:
            raise RuntimeError("DISCORD_BOT_TOKEN is required.")
        if not database_url:
            raise RuntimeError("RENDER_DATABASE_URL or DATABASE_URL is required.")

        try:
            approval_role_id = int(
                os.getenv("APPROVAL_ROLE_ID", str(DEFAULT_APPROVAL_ROLE_ID))
            )
            dev_guild_value = os.getenv("DEV_GUILD_ID", "").strip()
            dev_guild_id = int(dev_guild_value) if dev_guild_value else None
        except ValueError as exc:
            raise RuntimeError(
                "APPROVAL_ROLE_ID and DEV_GUILD_ID must be numeric Discord IDs."
            ) from exc

        return cls(
            discord_bot_token=token,
            database_url=database_url,
            approval_role_id=approval_role_id,
            dev_guild_id=dev_guild_id,
        )

