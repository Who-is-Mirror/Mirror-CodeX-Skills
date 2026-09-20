#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import runtime_paths


class RuntimePathsTest(unittest.TestCase):
    def test_default_uses_dynamic_port_and_session_isolated_profile(self) -> None:
        environment = {
            "EDGE_CDP_SESSION": "android-daily-sheet-roundtrip",
            "EDGE_CDP_WINDOWS_PROFILE": r"C:\Users\Tester",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = runtime_paths.resolve_runtime_paths()
        self.assertEqual(config.cdp_port, 0)
        self.assertFalse(config.cdp_port_is_override)
        self.assertEqual(
            config.edge_profile,
            r"C:\Users\Tester\AppData\Local\Codex\EdgeSessions\android-daily-sheet-roundtrip",
        )

    def test_explicit_profile_and_port_are_controlled_overrides(self) -> None:
        environment = {
            "EDGE_CDP_SESSION": "performance-task-entry",
            "EDGE_CDP_WINDOWS_PROFILE": r"C:\Users\Tester",
            "EDGE_CDP_PROFILE": r"C:\Users\Tester\AppData\Local\Codex\EdgeBackgroundProfile",
            "EDGE_CDP_PORT": "43123",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = runtime_paths.resolve_runtime_paths()
        self.assertEqual(config.cdp_port, 43123)
        self.assertTrue(config.cdp_port_is_override)
        self.assertTrue(config.edge_profile.endswith(r"\EdgeBackgroundProfile"))

    def test_invalid_session_is_rejected(self) -> None:
        environment = {
            "EDGE_CDP_SESSION": "../shared",
            "EDGE_CDP_WINDOWS_PROFILE": r"C:\Users\Tester",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "EDGE_CDP_SESSION"):
                runtime_paths.resolve_runtime_paths()


if __name__ == "__main__":
    unittest.main()
