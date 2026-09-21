#!/usr/bin/env python3
"""Regression tests for performance CDP runtime configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import runtime_paths


class CdpPortConfigurationTest(unittest.TestCase):
    def test_default_uses_edge_automatic_port_allocation(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            port, is_override = runtime_paths._cdp_port()
        self.assertEqual(port, 0)
        self.assertFalse(is_override)

    def test_explicit_port_remains_a_fixed_override(self) -> None:
        with patch.dict("os.environ", {"PERFORMANCE_CDP_PORT": "43123"}, clear=True):
            port, is_override = runtime_paths._cdp_port()
        self.assertEqual(port, 43123)
        self.assertTrue(is_override)

    def test_automatic_configuration_has_no_precomputed_endpoint(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = runtime_paths.resolve_runtime_paths()
        self.assertIsNone(config.cdp_endpoint)
        self.assertFalse(config.cdp_port_is_override)


if __name__ == "__main__":
    unittest.main()
