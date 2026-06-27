"""Standalone indexer — pull from Drive, extract, write the cache, stamp a manifest.

Run this to (re)build the shared cache *without touching the running bot*. The bot
watches the cache manifest and hot-reloads its index when this finishes, so
extraction/data changes go live without a restart:

    python -m lorehound.index            # incremental: changed files + version bumps
    python -m lorehound.index --force     # full re-extract (ignore the cache)

This is the indexing half of the bot/indexer split. The bot still builds its index
from the cache at startup (and `/reindex` still works in-process), but the *expensive*
re-download + re-extraction can run here, on its own schedule, while the bot keeps
serving. Note: bot-*code* changes (new commands, chargen/render logic) still need a
bot restart — only data/extraction changes hot-reload.

Don't run this concurrently with an in-bot `/reindex` of the same cache: the
re-entry lock in RulesService is per-process and won't coordinate across the two.
"""

from __future__ import annotations

import sys
import time

from .config import Config
from .drive_client import DriveClient


def main(argv: list[str]) -> int:
    force = "--force" in argv
    config = Config.load()
    if not config.drive_configured:
        print(
            "[index] Google Drive is not configured — set DRIVE_FOLDER_ID and "
            "credentials in .env (see README).",
            file=sys.stderr,
        )
        return 2

    drive = DriveClient(
        folder_id=config.drive_folder_id,  # type: ignore[arg-type]
        credentials_file=config.google_credentials_file,
        credentials_json=config.google_credentials_json,
    )
    mode = "full re-extract (cache bypassed)" if force else "incremental (changed only)"
    print(f"[index] starting {mode}…", flush=True)
    t0 = time.time()
    docs = drive.fetch_all(force=force)
    path = drive.write_manifest(docs)
    print(
        f"[index] extracted {len(docs)} doc(s) in {time.time() - t0:.0f}s; "
        f"manifest → {path}",
        flush=True,
    )
    print("[index] the running bot will hot-reload its index shortly.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
