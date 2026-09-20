#!/usr/bin/env python3

from __future__ import annotations

import io
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

import ensure_background_edge


class EdgeCdpBridgeTest(unittest.TestCase):
    def test_delegates_existing_profile_to_shared_dynamic_session(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Tester\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            windows_profile=r"C:\Users\Tester",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        completed = subprocess.CompletedProcess([], 0, '{"ready": true}\n', '')
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "ensure_session.py"
            script.write_text("# test\n", encoding="utf-8")
            output = io.StringIO()
            with (
                patch.object(ensure_background_edge, "resolve_runtime_paths", return_value=config),
                patch.object(ensure_background_edge, "shared_ensure_script", return_value=script),
                patch.object(ensure_background_edge.subprocess, "run", return_value=completed) as run,
                redirect_stdout(output),
            ):
                self.assertEqual(ensure_background_edge.main(), 0)
        command = run.call_args.args[0]
        self.assertIn("performance-task-entry", command)
        self.assertIn(config.edge_profile, command)
        self.assertNotIn("--port", command)
        self.assertEqual(output.getvalue().strip(), '{"ready": true}')

    def test_forwards_explicit_troubleshooting_port(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Tester\Profile",
            edge_executable=r"C:\Edge\msedge.exe",
            windows_profile=r"C:\Users\Tester",
            cdp_port=43123,
            cdp_port_is_override=True,
        )
        with (
            patch.object(ensure_background_edge, "resolve_runtime_paths", return_value=config),
            patch.object(ensure_background_edge, "shared_ensure_script", return_value=Path("/tmp/ensure.py")),
            patch.object(
                ensure_background_edge.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, '{"ready": false}\n', ''),
            ) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(ensure_background_edge.main(), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--port") + 1], "43123")


if __name__ == "__main__":
    unittest.main()
