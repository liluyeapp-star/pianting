#!/usr/bin/env python3
"""Behavior tests for the portable personal RSS builder."""

import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "skills" / "pianting" / "scripts" / "build_feed.py"


class FeedBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio_one = self.root / "one.mp3"
        self.audio_two = self.root / "two.mp3"
        self.audio_one.write_bytes(b"not-a-real-mp3-one")
        self.audio_two.write_bytes(b"not-a-real-mp3-two")
        self.library = self.root / "library"

    def tearDown(self):
        self.tmp.cleanup()

    def run_builder(self, audio=None, episode_id="episode-001", title="第一期"):
        return subprocess.run([
            sys.executable, str(BUILDER), "--library-dir", str(self.library),
            "--base-url", "https://example.pages.dev", "--audio", str(audio or self.audio_one),
            "--episode-id", episode_id, "--title", title,
            "--source-url", "https://example.com/original", "--duration", "123"],
            text=True, capture_output=True, check=False)

    def state(self):
        return json.loads((self.library / "state.json").read_text(encoding="utf-8"))

    def catalog(self):
        return json.loads((self.library / "catalog.json").read_text(encoding="utf-8"))

    def test_multiple_episodes_are_retained(self):
        self.assertEqual(self.run_builder().returncode, 0)
        self.assertEqual(self.run_builder(self.audio_two, "episode-002", "第二期").returncode, 0)
        catalog = self.catalog()
        self.assertEqual([item["episode_id"] for item in catalog["episodes"]], ["episode-001", "episode-002"])
        token = self.state()["token"]
        public = self.library / "public"
        self.assertTrue((public / token / "episode-001.mp3").is_file())
        self.assertTrue((public / token / "episode-002.mp3").is_file())
        self.assertTrue((public / "_worker.js").is_file())
        self.assertIn("episode-002.mp3", (public / "_worker.js").read_text(encoding="utf-8"))
        feed = ET.parse(str(public / token / "feed.xml"))
        self.assertEqual(len(feed.findall("./channel/item")), 2)

    def test_guid_and_private_path_stay_stable_on_update(self):
        self.assertEqual(self.run_builder().returncode, 0)
        old_state, old_guid = self.state(), self.catalog()["episodes"][0]["guid"]
        self.assertEqual(self.run_builder(self.audio_two, "episode-001", "更新标题").returncode, 0)
        self.assertEqual(self.state()["token"], old_state["token"])
        self.assertEqual(self.state()["show_guid"], old_state["show_guid"])
        self.assertEqual(self.catalog()["episodes"][0]["guid"], old_guid)

    def test_oversized_audio_does_not_create_or_change_library(self):
        giant = self.root / "giant.mp3"
        with giant.open("wb") as handle:
            handle.truncate(25 * 1024 * 1024 + 1)
        result = self.run_builder(giant, "episode-giant")
        self.assertEqual(result.returncode, 2)
        self.assertIn("25 MiB", result.stderr)
        self.assertFalse(self.library.exists())

    def test_path_injection_episode_id_is_rejected_without_writes(self):
        result = self.run_builder(episode_id="../escape")
        self.assertEqual(result.returncode, 2)
        self.assertIn("episode-id", result.stderr)
        self.assertFalse(self.library.exists())


if __name__ == "__main__":
    unittest.main()
