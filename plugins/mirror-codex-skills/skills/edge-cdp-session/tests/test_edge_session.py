#!/usr/bin/env python3
"""Regression tests for starting Windows Edge from WSL."""

from __future__ import annotations

import errno
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

import edge_session


class RunPowerShellTest(unittest.TestCase):
    def test_direct_windows_execution_remains_the_primary_path(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")

        with patch.object(edge_session.subprocess, "run", return_value=completed) as run:
            result = edge_session.run_powershell("/mnt/c/powershell.exe", "command")

        self.assertIs(result, completed)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "/mnt/c/powershell.exe")

    def test_exec_format_error_retries_through_wsl_init(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        direct = [
            "/mnt/c/powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "command",
        ]

        with (
            patch.object(edge_session.os, "access", return_value=True),
            patch.object(
                edge_session.subprocess,
                "run",
                side_effect=[OSError(errno.ENOEXEC, "Exec format error"), completed],
            ) as run,
        ):
            result = edge_session.run_powershell(direct[0], "command")

        self.assertIs(result, completed)
        self.assertEqual(run.call_args_list[0].args[0], direct)
        self.assertEqual(run.call_args_list[1].args[0], ["/init", *direct])

    def test_unrelated_os_error_is_not_hidden(self) -> None:
        with patch.object(
            edge_session.subprocess,
            "run",
            side_effect=OSError(errno.ENOENT, "missing"),
        ):
            with self.assertRaises(OSError):
                edge_session.run_powershell("/mnt/c/powershell.exe", "command")


class DevToolsActivePortTest(unittest.TestCase):
    PROFILE = r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile"
    PAYLOAD = "43123\n/devtools/browser/fresh-id\n"

    def test_direct_wsl_state_is_preferred_without_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)
            (profile / "DevToolsActivePort").write_text(self.PAYLOAD, encoding="utf-8")
            with (
                patch.object(edge_session, "profile_state_path", return_value=profile),
                patch.object(edge_session, "run_powershell") as powershell,
            ):
                state = edge_session.read_devtools_active_port(
                    self.PROFILE, "powershell.exe"
                )

        self.assertEqual(state, (43123, "/devtools/browser/fresh-id"))
        powershell.assert_not_called()

    def test_powershell_reads_the_windows_view_when_wsl_path_is_missing(self) -> None:
        completed = subprocess.CompletedProcess([], 0, self.PAYLOAD, "")
        with (
            patch.object(
                edge_session,
                "profile_state_path",
                return_value=Path("/definitely/missing/profile"),
            ),
            patch.object(edge_session, "run_powershell", return_value=completed) as run,
        ):
            state = edge_session.read_devtools_active_port(
                self.PROFILE, "powershell.exe"
            )

        self.assertEqual(state, (43123, "/devtools/browser/fresh-id"))
        self.assertIn("[IO.File]::ReadAllText", run.call_args.args[1])
        self.assertIn("DevToolsActivePort", run.call_args.args[1])

    def test_malformed_or_failed_powershell_state_is_rejected(self) -> None:
        cases = [
            subprocess.CompletedProcess([], 0, "43123\nwrong-path\n", ""),
            subprocess.CompletedProcess([], 0, "43123\n/devtools/browser/id\nextra\n", ""),
            subprocess.CompletedProcess([], 3, "", "missing"),
        ]
        for completed in cases:
            with (
                self.subTest(completed=completed),
                patch.object(
                    edge_session,
                    "profile_state_path",
                    return_value=Path("/definitely/missing/profile"),
                ),
                patch.object(edge_session, "run_powershell", return_value=completed),
            ):
                self.assertIsNone(
                    edge_session.read_devtools_active_port(
                        self.PROFILE, "powershell.exe"
                    )
                )

    def test_stale_state_is_cleared_from_local_and_windows_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)
            state_file = profile / "DevToolsActivePort"
            state_file.write_text(self.PAYLOAD, encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, "1", "")
            with (
                patch.object(edge_session, "profile_state_path", return_value=profile),
                patch.object(edge_session, "run_powershell", return_value=completed) as run,
            ):
                removed = edge_session.clear_devtools_active_port(
                    self.PROFILE, "powershell.exe"
                )

            self.assertFalse(state_file.exists())
        self.assertTrue(removed)
        self.assertIn("Remove-Item -LiteralPath", run.call_args.args[1])


class EndpointOwnershipTest(unittest.TestCase):
    @staticmethod
    def process(pid: int, command_line: str, parent_pid: int = 10) -> edge_session.EdgeProcess:
        return edge_session.EdgeProcess(pid, parent_pid, command_line)

    def test_devtools_state_must_match_the_live_browser_endpoint(self) -> None:
        data = {"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/fresh-id"}
        self.assertTrue(
            edge_session.endpoint_matches_state(data, 43123, "/devtools/browser/fresh-id")
        )
        self.assertFalse(
            edge_session.endpoint_matches_state(data, 43123, "/devtools/browser/stale-id")
        )
        self.assertFalse(
            edge_session.endpoint_matches_state(
                {"webSocketDebuggerUrl": "ws://127.0.0.1:not-a-port/devtools/browser/fresh-id"},
                43123,
                "/devtools/browser/fresh-id",
            )
        )

    def test_process_and_listener_metadata_preserve_and_bind_pids(self) -> None:
        process_metadata = json.dumps({
            "ProcessId": 100,
            "ParentProcessId": 10,
            "CommandLine": "msedge.exe --remote-debugging-port=0",
        })
        listener_metadata = json.dumps([
            {"OwningProcess": 100, "LocalAddress": "127.0.0.1", "LocalPort": 43123},
            {"OwningProcess": 100, "LocalAddress": "::1", "LocalPort": 43123},
        ])
        with patch.object(
            edge_session,
            "run_powershell",
            side_effect=[
                subprocess.CompletedProcess([], 0, process_metadata, ""),
                subprocess.CompletedProcess([], 0, listener_metadata, ""),
            ],
        ) as run:
            processes = edge_session.edge_processes("powershell.exe")
            listener = edge_session.loopback_listener_owner("powershell.exe", 43123)
        self.assertEqual(processes, [self.process(100, "msedge.exe --remote-debugging-port=0")])
        self.assertEqual(listener, ("single", 100))
        self.assertIn("ProcessId,ParentProcessId,CommandLine", run.call_args_list[0].args[1])
        self.assertIn("Get-NetTCPConnection", run.call_args_list[1].args[1])

    def test_only_the_root_dedicated_edge_command_line_is_owned(self) -> None:
        root = (
            '"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe" '
            '--remote-debugging-port=0 --user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        )
        renderer = (
            '"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe" --type=renderer '
            '--remote-debugging-port=0 --user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        )
        utility = (
            '"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe" --type utility '
            '--remote-debugging-port=0 --user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        )
        self.assertTrue(
            edge_session.dedicated_root_pid(
                [self.process(100, root), self.process(101, renderer, 100), self.process(102, utility, 100)],
                r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
                0,
            ) == 100
        )
        self.assertIsNone(
            edge_session.dedicated_root_pid(
                [self.process(100, root), self.process(200, root)],
                r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
                0,
            )
        )

    def test_stale_or_unowned_endpoint_is_not_resolved(self) -> None:
        config = SimpleNamespace(
            session="performance-task-entry",
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        with (
            patch.object(edge_session, "read_devtools_active_port", return_value=(43123, "/devtools/browser/id")),
            patch.object(
                edge_session,
                "probe",
                return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"},
            ),
            patch.object(edge_session, "loopback_listener_owner", return_value=("single", 999)),
        ):
            self.assertIsNone(edge_session.resolve_auto_endpoint("powershell.exe", config, []))

    def test_owned_fixed_override_reuses_live_endpoint_without_state_file(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            cdp_port=43123,
            cdp_port_is_override=True,
        )
        root = (
            'msedge.exe --remote-debugging-port=43123 '
            '--user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        )
        data = {"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"}
        with (
            patch.object(edge_session, "read_devtools_active_port", return_value=None) as state,
            patch.object(edge_session, "probe", return_value=data),
            patch.object(edge_session, "loopback_listener_owner", return_value=("single", 100)),
        ):
            self.assertEqual(
                edge_session.resolve_fixed_endpoint(
                    "powershell.exe", config, [self.process(100, root)]
                ),
                (data, "http://127.0.0.1:43123"),
            )
        state.assert_not_called()

    def test_stale_auto_state_with_unrelated_listener_is_not_resolved(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        root = 'msedge.exe --remote-debugging-port=0 --user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        with (
            patch.object(edge_session, "read_devtools_active_port", return_value=(43123, "/devtools/browser/id")),
            patch.object(
                edge_session,
                "probe",
                return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"},
            ) as probe,
            patch.object(edge_session, "loopback_listener_owner", return_value=("single", 777)),
        ):
            self.assertIsNone(
                edge_session.resolve_auto_endpoint("powershell.exe", config, [self.process(100, root)])
            )
        probe.assert_not_called()

    def test_auto_endpoint_requires_listener_owned_by_the_exact_root_pid(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        root = 'msedge.exe --remote-debugging-port=0 --user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"'
        data = {"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"}
        with (
            patch.object(edge_session, "read_devtools_active_port", return_value=(43123, "/devtools/browser/id")),
            patch.object(edge_session, "probe", return_value=data),
            patch.object(edge_session, "loopback_listener_owner", return_value=("single", 100)),
        ):
            self.assertEqual(
                edge_session.resolve_auto_endpoint("powershell.exe", config, [self.process(100, root)]),
                (data, "http://127.0.0.1:43123"),
            )


class StartupWaitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=0,
            cdp_port_is_override=False,
        )

    def test_auto_mode_waits_for_state_before_querying_windows_metadata(self) -> None:
        clock = {"now": 0.0}

        def sleep(seconds: float) -> None:
            clock["now"] += seconds

        root = edge_session.EdgeProcess(
            100,
            10,
            'msedge.exe --remote-debugging-port=0 '
            '--user-data-dir="C:\\Users\\Mirror\\AppData\\Local\\Codex\\EdgeBackgroundProfile"',
        )
        resolved = (
            {"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"},
            "http://127.0.0.1:43123",
        )
        with (
            patch.object(
                edge_session,
                "read_devtools_active_port",
                side_effect=[None, None, (43123, "/devtools/browser/id")],
            ) as state,
            patch.object(edge_session, "edge_processes", return_value=[root]) as processes,
            patch.object(edge_session, "resolve_auto_endpoint", return_value=resolved) as resolve,
            patch.object(edge_session.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(edge_session.time, "sleep", side_effect=sleep),
        ):
            current, phase, elapsed = edge_session.wait_for_started_endpoint(
                "powershell.exe", self.config, timeout_seconds=2.0, poll_interval_seconds=0.25
            )

        self.assertEqual(current, resolved)
        self.assertEqual(phase, "ready")
        self.assertEqual(elapsed, 0.5)
        self.assertEqual(state.call_count, 3)
        processes.assert_called_once_with("powershell.exe")
        resolve.assert_called_once_with("powershell.exe", self.config, [root])

    def test_monotonic_deadline_includes_one_final_state_attempt(self) -> None:
        clock = {"now": 0.0}

        def sleep(seconds: float) -> None:
            clock["now"] += seconds

        with (
            patch.object(edge_session, "read_devtools_active_port", return_value=None) as state,
            patch.object(edge_session, "edge_processes") as processes,
            patch.object(edge_session.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(edge_session.time, "sleep", side_effect=sleep),
        ):
            current, phase, elapsed = edge_session.wait_for_started_endpoint(
                "powershell.exe", self.config, timeout_seconds=1.0, poll_interval_seconds=0.25
            )

        self.assertIsNone(current)
        self.assertEqual(phase, "waiting_for_devtools_active_port")
        self.assertEqual(elapsed, 1.0)
        self.assertEqual(state.call_count, 5)
        processes.assert_not_called()

    def test_default_startup_timeout_is_thirty_seconds(self) -> None:
        self.assertEqual(edge_session.STARTUP_TIMEOUT_SECONDS, 30.0)


class AutoPortMainTest(unittest.TestCase):
    def test_default_launch_requests_automatic_port_and_returns_resolved_endpoint(self) -> None:
        config = SimpleNamespace(
            session="performance-task-entry",
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        resolved = (
            {"webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"},
            "http://127.0.0.1:43123",
        )
        started = subprocess.CompletedProcess([], 0, "", "")
        output = io.StringIO()
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", side_effect=["powershell.exe", None]),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[]),
            patch.object(edge_session, "resolve_auto_endpoint", return_value=None),
            patch.object(edge_session, "clear_devtools_active_port"),
            patch.object(edge_session, "run_powershell", return_value=started) as run,
            patch.object(
                edge_session,
                "wait_for_started_endpoint",
                return_value=(resolved, "ready", 0.75),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(edge_session.main(), 0)

        self.assertIn("--remote-debugging-port=0", run.call_args.args[1])
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["cdp_http_endpoint"], "http://127.0.0.1:43123")
        self.assertEqual(payload["cdp_port"], 43123)
        self.assertTrue(payload["started"])

    def test_startup_timeout_reports_phase_and_measured_elapsed_time(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        output = io.StringIO()
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", return_value="powershell.exe"),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[]),
            patch.object(edge_session, "resolve_auto_endpoint", return_value=None),
            patch.object(edge_session, "clear_devtools_active_port"),
            patch.object(
                edge_session,
                "run_powershell",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            patch.object(
                edge_session,
                "wait_for_started_endpoint",
                return_value=(None, "waiting_for_devtools_active_port", 31.2478),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(edge_session.main(), 1)

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["phase"], "waiting_for_devtools_active_port")
        self.assertEqual(payload["elapsed_seconds"], 31.248)
        self.assertEqual(payload["timeout_seconds"], 30.0)
        self.assertNotIn("10 seconds", payload["error"])

    def test_fixed_override_fails_closed_when_port_is_already_occupied(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=9223,
            cdp_port_is_override=True,
        )
        output = io.StringIO()
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", return_value="powershell.exe"),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[]),
            patch.object(edge_session, "resolve_fixed_endpoint", return_value=None),
            patch.object(
                edge_session, "tcp_listeners",
                return_value=[edge_session.TcpListener(777, "127.0.0.1", 9223)],
            ),
            patch.object(edge_session, "run_powershell") as run,
            redirect_stdout(output),
        ):
            self.assertEqual(edge_session.main(), 1)

        self.assertEqual(run.call_count, 0)
        self.assertIn("non-owned endpoint", output.getvalue())

    def test_auto_mode_removes_only_stale_state_when_no_owned_root_exists(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", return_value="powershell.exe"),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[]),
            patch.object(edge_session, "resolve_auto_endpoint", return_value=None),
            patch.object(edge_session, "clear_devtools_active_port") as clear,
            patch.object(edge_session, "run_powershell", return_value=subprocess.CompletedProcess([], 1, "", "")),
        ):
            self.assertEqual(edge_session.main(), 1)
        clear.assert_called_once_with(config.edge_profile, "powershell.exe")

    def test_auto_mode_does_not_launch_when_an_owned_root_is_unhealthy(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=0,
            cdp_port_is_override=False,
        )
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", return_value="powershell.exe"),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[EndpointOwnershipTest.process(100, "root")]),
            patch.object(edge_session, "resolve_auto_endpoint", return_value=None),
            patch.object(edge_session, "run_powershell") as run,
        ):
            self.assertEqual(edge_session.main(), 1)
        self.assertEqual(run.call_count, 0)

    def test_fixed_mode_does_not_launch_when_owned_root_endpoint_is_unhealthy_even_if_port_is_closed(self) -> None:
        config = SimpleNamespace(
            edge_profile=r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile",
            edge_executable=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            cdp_port=43123,
            cdp_port_is_override=True,
        )
        with (
            patch.object(edge_session, "resolve_runtime_paths", return_value=config),
            patch.object(edge_session.shutil, "which", return_value="powershell.exe"),
            patch.object(edge_session, "edge_processes", return_value=[]),
            patch.object(edge_session, "matching_dedicated_roots", return_value=[EndpointOwnershipTest.process(100, "root")]),
            patch.object(edge_session, "resolve_fixed_endpoint", return_value=None),
            patch.object(edge_session, "loopback_listener_owner", return_value=("none", None)),
            patch.object(edge_session, "run_powershell") as run,
        ):
            self.assertEqual(edge_session.main(), 1)
        self.assertEqual(run.call_count, 0)


if __name__ == "__main__":
    unittest.main()
