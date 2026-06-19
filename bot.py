"""Lorehound entry point: build the bot, attach services, load cogs, run."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from lorehound.config import Config, ConfigError
from lorehound.drive_client import DriveClient
from lorehound.rules import RulesService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("lorehound")

EXTENSIONS = [
    "lorehound.cogs.dice_cog",
    "lorehound.cogs.rules_cog",
]


class Lorehound(commands.Bot):
    def __init__(self, config: Config) -> None:
        # Slash commands don't need message-content intent; keep it minimal so
        # the bot needs no privileged intents to start.
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.config = config

        drive = None
        if config.drive_configured:
            drive = DriveClient(
                folder_id=config.drive_folder_id,  # type: ignore[arg-type]
                credentials_file=config.google_credentials_file,
                credentials_json=config.google_credentials_json,
            )
        else:
            log.info("Google Drive not configured — rules commands will prompt setup.")
        # Cogs read this during load.
        self.rules_service = RulesService(drive)

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)

        # Sync slash commands. Guild-scoped sync is instant (great for dev);
        # global sync can take up to ~1 hour to propagate.
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), self.config.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take ~1h to appear)", len(synced))

        # Warm the rules index in the background if Drive is ready.
        if self.rules_service.drive is not None:
            asyncio.create_task(self._warm_rules())

    async def _warm_rules(self) -> None:
        try:
            summary = await asyncio.to_thread(self.rules_service.refresh)
            log.info(
                "Rules indexed: %d docs, %d chunks",
                summary["documents"],
                summary["chunks"],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not warm rules index: %s", exc)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id: %s)", self.user, self.user and self.user.id)


def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}")

    bot = Lorehound(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
