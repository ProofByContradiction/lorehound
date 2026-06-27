"""Tests for the bot/indexer split — the cache manifest the standalone indexer
(``python -m lorehound.index``) stamps and the bot's watcher keys on.

Pure/offline: no Drive, no network. They pin the manifest persistence + the
leading-dot exclusion the design relies on (the bot watches mtime; the cache glob
must not pick the marker up as a doc).
"""

import asyncio
import glob
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lorehound.config import Config
from lorehound.drive_client import REINDEX_MANIFEST, DriveClient, DriveDoc


def _doc(name: str) -> DriveDoc:
    return DriveDoc(file_id=name, name=name, mime_type="application/pdf", text="x")


class TestReindexManifest(unittest.TestCase):
    def _client(self, cache_dir) -> DriveClient:
        return DriveClient(folder_id="folder", cache_dir=cache_dir)

    def test_manifest_path_under_cache_dir(self):
        with tempfile.TemporaryDirectory() as d:
            dc = self._client(d)
            self.assertEqual(dc.manifest_path, Path(d) / REINDEX_MANIFEST)

    def test_manifest_path_none_without_cache(self):
        self.assertIsNone(self._client(None).manifest_path)
        self.assertIsNone(self._client(None).write_manifest([_doc("a")]))

    def test_write_manifest_records_version_and_files(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            dc = self._client(d)
            path = dc.write_manifest([_doc("T2K/Core.pdf"), _doc("T2K/A.pdf")])
            self.assertTrue(path.exists())
            body = json.loads(path.read_text())
            self.assertEqual(body["documents"], 2)
            self.assertEqual(body["files"], ["T2K/A.pdf", "T2K/Core.pdf"])  # sorted
            self.assertIn("version", body)

    def test_rewrite_advances_mtime(self):
        # The watcher keys on mtime; a rewrite must advance it even if unchanged.
        with tempfile.TemporaryDirectory() as d:
            dc = self._client(d)
            path = dc.write_manifest([_doc("a")])
            os.utime(path, (0, 0))  # backdate to the epoch
            old = path.stat().st_mtime
            dc.write_manifest([_doc("a")])
            self.assertGreater(path.stat().st_mtime, old)

    def test_manifest_excluded_from_cache_json_glob(self):
        # The leading dot keeps the marker out of the cache's ``*.json`` glob, so it's
        # never mistaken for an extracted-doc cache file.
        with tempfile.TemporaryDirectory() as d:
            dc = self._client(d)
            (Path(d) / "realdoc.json").write_text("{}")
            dc.write_manifest([_doc("a")])
            globbed = [os.path.basename(p) for p in glob.glob(os.path.join(d, "*.json"))]
            self.assertIn("realdoc.json", globbed)
            self.assertNotIn(REINDEX_MANIFEST, globbed)


class _FakeDrive:
    def __init__(self, manifest):
        self.manifest_path = manifest


class _FakeRules:
    """Stand-in for RulesService: records refresh() calls the watcher makes."""

    def __init__(self, manifest):
        self.drive = _FakeDrive(manifest)
        self.indexing = False
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1
        return {"documents": 1, "chunks": 2}


_UNCONFIGURED = Config(
    discord_token="x", guild_id=None, user_install=False,
    drive_folder_id=None, google_credentials_file=None, google_credentials_json=None,
)


class TestCacheWatcher(unittest.IsolatedAsyncioTestCase):
    """The bot's _watch_cache loop: a pre-existing manifest at startup must NOT
    trigger a reload, but a later manifest change must."""

    async def _wait_for(self, predicate, timeout=2.0):
        for _ in range(int(timeout / 0.02)):
            if predicate():
                return True
            await asyncio.sleep(0.02)
        return False

    async def test_reloads_only_on_manifest_change(self):
        import bot as botmod

        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / REINDEX_MANIFEST
            manifest.write_text("{}")        # already present at startup…
            os.utime(manifest, (1, 1))        # …with an old mtime (the baseline)

            b = botmod.Lorehound(_UNCONFIGURED)
            fake = _FakeRules(manifest)
            b.rules_service = fake
            with mock.patch.object(botmod, "CACHE_POLL_SECONDS", 0.02):
                task = asyncio.create_task(b._watch_cache())
                try:
                    # The pre-existing manifest is the baseline → no reload.
                    await asyncio.sleep(0.1)
                    self.assertEqual(fake.refreshed, 0)
                    # The indexer "finishes" → manifest mtime advances → reload.
                    manifest.write_text("{}")
                    self.assertTrue(
                        await self._wait_for(lambda: fake.refreshed >= 1),
                        "watcher did not hot-reload after the manifest changed",
                    )
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await b.close()


if __name__ == "__main__":
    unittest.main()
