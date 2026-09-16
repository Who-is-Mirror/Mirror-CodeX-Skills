#!/usr/bin/env python3
"""Regression tests that consumers receive the owned, resolved CDP endpoints."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fill_draft
import probe_browser


class ProbeConsumerTest(unittest.TestCase):
    def test_probe_attaches_to_the_resolved_websocket_and_reports_its_port(self) -> None:
        resolved = {
            "ready": True,
            "cdp_port": 43123,
            "webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id",
        }
        commands: list[list[str]] = []

        def run(args: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            if args[1].endswith("ensure_background_edge.py"):
                return subprocess.CompletedProcess(args, 0, json.dumps(resolved), "")
            return subprocess.CompletedProcess(args, 0, "", "")

        output = io.StringIO()
        with (
            patch.object(probe_browser, "resolve_runtime_paths", return_value=SimpleNamespace()),
            patch.object(probe_browser, "run", side_effect=run),
            redirect_stdout(output),
        ):
            self.assertEqual(probe_browser.main(), 0)

        self.assertIn("--cdp=ws://127.0.0.1:43123/devtools/browser/id", commands[1])
        self.assertEqual(json.loads(output.getvalue())["cdp_port"], 43123)


class FillConsumerTest(unittest.TestCase):
    def test_fill_passes_the_resolved_http_endpoint_to_login(self) -> None:
        draft = {"month": "2026-08", "entries": []}
        encoded_result = base64.b64encode(
            json.dumps({"status": "complete", "month": "2026-08", "saved": [], "existing": [], "errors": []}).encode()
        ).decode()
        resolved = {
            "cdp_http_endpoint": "http://127.0.0.1:43123",
            "webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id",
        }
        commands: list[list[str]] = []

        def command(args: list[str], _cwd: Path, _timeout: int = 60) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            if args[0] == "python3":
                return subprocess.CompletedProcess(args, 0, json.dumps(resolved), "")
            if args[0] == "node":
                return subprocess.CompletedProcess(args, 0, "", "")
            if "run-code" in args:
                return subprocess.CompletedProcess(args, 0, fill_draft.RESULT_MARKER + encoded_result, "")
            return subprocess.CompletedProcess(args, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            draft_path = root / "draft.json"
            draft_bytes = json.dumps(draft).encode()
            draft_path.write_bytes(draft_bytes)
            output_dir = root / "outputs"
            archive_root = root / "archive"
            arguments = [
                "fill_draft.py", str(draft_path), "--expected-sha256", hashlib.sha256(draft_bytes).hexdigest(),
                "--output-dir", str(output_dir), "--archive-root", str(archive_root),
            ]
            with (
                patch.object(fill_draft, "command", side_effect=command),
                patch.object(fill_draft, "validate", return_value={"month": "2026-08"}),
                patch.object(sys, "argv", arguments),
            ):
                self.assertEqual(fill_draft.main(), 0)

        login = next(args for args in commands if args[0] == "node")
        self.assertEqual(login[-2:], ["--cdp-endpoint", "http://127.0.0.1:43123"])


if __name__ == "__main__":
    unittest.main()
