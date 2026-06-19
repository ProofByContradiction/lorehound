"""Runtime configuration, loaded from environment variables (.env locally)."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()  # populate os.environ from a local .env if present
except ImportError:  # python-dotenv is optional in production
    pass


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    discord_token: str
    # Optional: sync slash commands to this guild for instant updates while
    # developing. Leave unset to register commands globally (can take ~1 hour).
    guild_id: int | None
    # Google Drive (optional until you set up credentials).
    drive_folder_id: str | None
    google_credentials_file: str | None
    google_credentials_json: str | None

    @property
    def drive_configured(self) -> bool:
        return bool(
            self.drive_folder_id
            and (self.google_credentials_file or self.google_credentials_json)
        )

    @classmethod
    def load(cls) -> "Config":
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it "
                "in (see README)."
            )

        guild_raw = os.environ.get("DISCORD_GUILD_ID", "").strip()
        guild_id = int(guild_raw) if guild_raw.isdigit() else None

        return cls(
            discord_token=token,
            guild_id=guild_id,
            drive_folder_id=os.environ.get("DRIVE_FOLDER_ID", "").strip() or None,
            google_credentials_file=os.environ.get(
                "GOOGLE_CREDENTIALS_FILE", ""
            ).strip()
            or None,
            google_credentials_json=os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
            or None,
        )
