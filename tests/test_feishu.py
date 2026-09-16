#!/usr/bin/env python3
"""Offline tests for the portable Feishu wrapper; no account or upload needed."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "skills/pianting/scripts/feishu_transcribe.py"
SPEC = importlib.util.spec_from_file_location("feishu_transcribe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def completed(arguments, payload, code=0):
    return subprocess.CompletedProcess(arguments, code, json.dumps(payload), "")


class FeishuTranscribeTests(unittest.TestCase):
    def test_drive_uploaded_state_continues_with_existing_file_token(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"audio")
            work.mkdir()
            size, sha256 = MODULE.media_fingerprint(media)
            MODULE.save_state(work, {"input_path": str(media), "input_bytes": size, "input_sha256": sha256, "title": "第 1 期", "file_token": "file_123", "stage": "drive_uploaded"})
            calls = []

            def create(arguments, **kwargs):
                calls.append(arguments)
                return completed(arguments, {"ok": True, "data": {"minute_token": "obcn123456789", "minute_url": "https://example.feishu.cn/minutes/obcn123456789"}})

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=create):
                state = MODULE.make_minute(media, "第 1 期", work)
            self.assertEqual("obcn123456789", state["minute_token"])
            self.assertEqual([["lark-cli", "minutes", "+upload", "--as", "user", "--file-token", "file_123", "--format", "json"]], calls)

    def test_saved_file_token_is_not_uploaded_again_after_interruption(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media = root / "episode.mp3"
            media.write_bytes(b"audio")
            work = root / "work"
            calls = []

            def first_run(arguments, **kwargs):
                calls.append(arguments)
                if arguments[1:3] == ["drive", "+upload"]:
                    return completed(arguments, {"ok": True, "data": {"file_token": "file_123"}})
                return completed(arguments, {"ok": False, "error": {"message": "network interrupted"}}, 1)

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=first_run):
                with self.assertRaises(MODULE.ToolError):
                    MODULE.make_minute(media, "第 1 期", work)
            state = MODULE.load_state(work)
            self.assertEqual("file_123", state["file_token"])
            self.assertEqual("minute_creation_unknown", state["stage"])

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run") as rerun:
                with self.assertRaises(MODULE.ToolError) as error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("无法安全重试", str(error.exception))
            rerun.assert_not_called()
            self.assertEqual(1, sum(command[1:3] == ["drive", "+upload"] for command in calls))

    def test_started_minute_creation_is_not_retried_after_process_death(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"audio")
            size, sha256 = MODULE.media_fingerprint(media)
            work.mkdir()
            MODULE.save_state(work, {"input_path": str(media), "input_bytes": size, "input_sha256": sha256, "file_token": "file_123", "stage": "minute_creation_started"})
            with patch.object(subprocess, "run") as run:
                with self.assertRaises(MODULE.ToolError) as error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("无法安全重试", str(error.exception))
            run.assert_not_called()

    def test_drive_upload_timeout_leaves_non_retryable_intent(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"audio")
            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired(["lark-cli"], 900)
            ):
                with self.assertRaises(MODULE.ToolError) as error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("服务器端是否已接收", str(error.exception))
            self.assertEqual("drive_upload_started", MODULE.load_state(work)["stage"])
            with patch.object(subprocess, "run") as rerun:
                with self.assertRaises(MODULE.ToolError) as retry_error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("不要盲目重传", str(retry_error.exception))
            rerun.assert_not_called()

    def test_changed_file_at_same_path_cannot_reuse_upload_state(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"old audio")
            size, sha256 = MODULE.media_fingerprint(media)
            work.mkdir()
            MODULE.save_state(work, {"input_path": str(media), "input_bytes": size, "input_sha256": sha256, "file_token": "file_123", "stage": "drive_uploaded"})
            media.write_bytes(b"new audio")
            with patch.object(subprocess, "run") as run:
                with self.assertRaises(MODULE.ToolError) as error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("内容已经变化", str(error.exception))
            run.assert_not_called()

    def test_resume_exports_existing_minute_without_upload_or_creation(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            work, output = root / "work", root / "out" / "transcript.txt"
            source = work / "transcript/artifact/transcript.txt"
            source.parent.mkdir(parents=True)
            source.write_text("Speaker 1 00:00:00.000\n你好\n", encoding="utf-8")
            calls = []

            def details(arguments, **kwargs):
                calls.append(arguments)
                return completed(arguments, {"ok": True, "data": {"minutes": [{"artifacts": {"transcript_file": "transcript/artifact/transcript.txt"}}]}})

            state = {"minute_token": "obcn123456789", "stage": "minute_created"}
            MODULE.save_state(work, state)
            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=details):
                result = MODULE.command_resume(type("Args", (), {"minute": "obcn123456789", "work_dir": work, "output": output, "timeout": 5, "interval": 1})())
            self.assertTrue(result["ok"])
            self.assertEqual(source.read_text(encoding="utf-8"), output.read_text(encoding="utf-8"))
            self.assertTrue(all(command[1:3] == ["minutes", "+detail"] for command in calls))

    def test_authentication_error_does_not_invoke_login(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"audio")

            def denied(arguments, **kwargs):
                return completed(arguments, {"ok": False, "error": {"message": "missing_scope: drive:drive"}}, 1)

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=denied) as run:
                with self.assertRaises(MODULE.ToolError) as error:
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertIn("不会自动登录", str(error.exception))
            self.assertEqual(["lark-cli", "drive", "+upload", "--as", "user", "--file", "./episode.mp3", "--name", "第 1 期.mp3", "--format", "json"], run.call_args.args[0])

    def test_auth_rejection_can_resume_same_work_dir_after_user_authorizes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            media, work = root / "episode.mp3", root / "work"
            media.write_bytes(b"audio")

            def drive_denied(arguments, **kwargs):
                return completed(arguments, {"ok": False, "error": {"type": "authorization", "subtype": "missing_scope", "message": "need drive scope"}}, 1)

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=drive_denied):
                with self.assertRaises(MODULE.ToolError):
                    MODULE.make_minute(media, "第 1 期", work)
            self.assertEqual("ready_to_upload", MODULE.load_state(work)["stage"])

            calls = []
            def permitted_then_minute_denied(arguments, **kwargs):
                calls.append(arguments)
                if arguments[1:3] == ["drive", "+upload"]:
                    return completed(arguments, {"ok": True, "data": {"file_token": "file_123"}})
                return completed(arguments, {"ok": False, "error": "permission_denied: minutes"}, 1)

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=permitted_then_minute_denied):
                with self.assertRaises(MODULE.ToolError):
                    MODULE.make_minute(media, "第 1 期", work)
            state = MODULE.load_state(work)
            self.assertEqual("drive_uploaded", state["stage"])
            self.assertEqual("file_123", state["file_token"])

            def minute_permitted(arguments, **kwargs):
                calls.append(arguments)
                return completed(arguments, {"ok": True, "data": {"minute_token": "obcn123456789"}})

            with patch.object(MODULE, "cli_program", return_value="lark-cli"), patch.object(subprocess, "run", side_effect=minute_permitted):
                state = MODULE.make_minute(media, "第 1 期", work)
            self.assertEqual("obcn123456789", state["minute_token"])
            self.assertEqual(1, sum(command[1:3] == ["drive", "+upload"] for command in calls))
            self.assertEqual(2, sum(command[1:3] == ["minutes", "+upload"] for command in calls))


if __name__ == "__main__":
    unittest.main()
