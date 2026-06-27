"""Lorehound entry point: build the bot, attach services, load cogs, run."""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands

from lorehound.config import Config, ConfigError
from lorehound.drive_client import DriveClient
from lorehound.rules import ReindexInProgress, RulesService

# How often the bot checks the cache manifest for a standalone-indexer update.
# A reindex changes data rarely, so 30s latency to pick it up is plenty.
CACHE_POLL_SECONDS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("lorehound")

EXTENSIONS = [
    "lorehound.cogs.dice_cog",
    "lorehound.cogs.rules_cog",
    "lorehound.cogs.chargen_cog",
    "lorehound.cogs.meta_cog",
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

    def _apply_install_contexts(self) -> None:
        """Make every command usable when installed to a guild *or* to a user, and
        in servers, DMs, and private group chats.

        This is what lets a user-installed copy of Lorehound work inside a private
        group DM. Requires "User Install" to be enabled for this application in the
        Discord Developer Portal (Installation → Installation Contexts).
        """
        installs = discord.app_commands.AppInstallationType(guild=True, user=True)
        contexts = discord.app_commands.AppCommandContext(
            guild=True, dm_channel=True, private_channel=True
        )
        for cmd_type in (
            discord.AppCommandType.chat_input,  # slash commands
            discord.AppCommandType.user,        # user context menus
            discord.AppCommandType.message,     # message context menus
        ):
            for cmd in self.tree.walk_commands(type=cmd_type):
                cmd.allowed_installs = installs
                cmd.allowed_contexts = contexts

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)

        # Sync slash commands. Guild-scoped sync is instant (great for dev);
        # global sync can take up to ~1 hour to propagate.
        try:
            if self.config.user_install:
                # User-installable apps must use GLOBAL commands; tag them so they
                # work in guilds, DMs, and group DMs.
                self._apply_install_contexts()
                synced = await self.tree.sync()
                log.info(
                    "Synced %d global commands with user-install enabled "
                    "(usable in DMs/group DMs; global sync can take ~1h to appear)",
                    len(synced),
                )
                # Still push to the dev guild for instant iteration, if configured.
                if self.config.guild_id:
                    guild = discord.Object(id=self.config.guild_id)
                    self.tree.copy_global_to(guild=guild)
                    g = await self.tree.sync(guild=guild)
                    log.info(
                        "Also synced %d commands to dev guild %s (instant)",
                        len(g),
                        self.config.guild_id,
                    )
            elif self.config.guild_id:
                guild = discord.Object(id=self.config.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Synced %d commands to guild %s", len(synced), self.config.guild_id
                )
            else:
                synced = await self.tree.sync()
                log.info(
                    "Synced %d global commands (may take ~1h to appear)", len(synced)
                )
        except discord.Forbidden:
            log.error(
                "Command sync failed (403 Forbidden): the bot isn't in guild %s. "
                "Invite it with your OAuth2 URL (scopes: bot + applications.commands), "
                "then restart.",
                self.config.guild_id,
            )
        except discord.HTTPException as exc:
            log.error(
                "Command sync failed: %s. If LOREHOUND_USER_INSTALL is set, confirm "
                "'User Install' is enabled for this app in the Discord Developer "
                "Portal (Installation → Installation Contexts), then restart.",
                exc,
            )

        # Warm the rules index in the background if Drive is ready, then keep
        # watching the cache so a standalone indexer run hot-reloads us — no restart.
        if self.rules_service.drive is not None:
            asyncio.create_task(self._warm_rules())
            asyncio.create_task(self._watch_cache())

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

    async def _watch_cache(self) -> None:
        """Hot-reload the index when the standalone indexer refreshes the cache.

        Polls the cache manifest's mtime (written by ``python -m lorehound.index`` when
        a (re)extraction finishes) and, when it advances, rebuilds + atomically swaps
        the index via ``RulesService.refresh`` — so data/extraction changes go live
        without a bot restart. The prior index stays queryable until the swap. Only
        the indexer writes the manifest, so a bot-side warm/``/reindex`` never
        re-triggers this loop."""
        drive = self.rules_service.drive
        manifest = drive.manifest_path if drive else None
        if manifest is None:
            return

        def mtime() -> float | None:
            try:
                return manifest.stat().st_mtime
            except OSError:
                return None

        last = mtime()  # baseline: don't reload for a manifest that predates startup
        while not self.is_closed():
            await asyncio.sleep(CACHE_POLL_SECONDS)
            now = mtime()
            # No manifest yet, unchanged, or a refresh is already running → wait.
            if now is None or now == last or self.rules_service.indexing:
                continue
            last = now
            log.info("Cache manifest changed — hot-reloading the rules index…")
            try:
                summary = await asyncio.to_thread(self.rules_service.refresh)
                log.info(
                    "Hot-reloaded index: %d docs, %d chunks",
                    summary["documents"],
                    summary["chunks"],
                )
            except ReindexInProgress:
                last = None  # a refresh slipped in; re-check (and reload) next tick
            except Exception as exc:  # noqa: BLE001
                log.warning("Cache hot-reload failed: %s", exc)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id: %s)", self.user, self.user and self.user.id)
        # Diagnostic: set LOREHOUND_SMOKE_TEST=1 to verify login + command sync
        # then exit cleanly — confirms config without leaving the bot running.
        if os.environ.get("LOREHOUND_SMOKE_TEST"):
            log.info("Smoke test OK — ready and commands synced. Shutting down.")
            await self.close()


def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    bot = Lorehound(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
