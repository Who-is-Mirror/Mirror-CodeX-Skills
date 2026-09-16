"""Exercise process/address identity through raw, mocked Windows metadata."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ensure_background_edge as edge


PROFILE = r"C:\Users\Mirror\AppData\Local\Codex\EdgeBackgroundProfile"
PORT = 43123
DATA = {"webSocketDebuggerUrl": f"ws://127.0.0.1:{PORT}/devtools/browser/stale-id"}


def config(fixed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        edge_profile=PROFILE, cdp_port=PORT if fixed else 0,
        cdp_port_is_override=fixed, edge_executable="msedge.exe",
    )


def process(pid: int = 100, port: int | None = 0, profile: str = PROFILE, extra: str = "") -> edge.EdgeProcess:
    mode = f" --remote-debugging-port={port}" if port is not None else ""
    return edge.EdgeProcess(pid, 10, f'msedge.exe{mode} --user-data-dir="{profile}"{extra}')


def listener(address: str = "127.0.0.1", owner: int = 100) -> dict:
    return {"OwningProcess": owner, "LocalAddress": address, "LocalPort": PORT}


class ListenerIdentityTests(unittest.TestCase):
    def assert_resolution(self, records, accepted: bool, fixed: bool) -> None:
        current = config(fixed)
        resolve = edge.resolve_fixed_endpoint if fixed else edge.resolve_auto_endpoint
        with (
            patch.object(edge, "run_powershell", return_value=subprocess.CompletedProcess([], 0, json.dumps(records), "")),
            patch.object(edge, "read_devtools_active_port", return_value=(PORT, "/devtools/browser/stale-id")) as state,
            patch.object(edge, "probe", return_value=DATA) as probe,
        ):
            actual = resolve("fake-powershell", current, [process(port=current.cdp_port)])
        if accepted:
            self.assertEqual(actual, (DATA, f"http://127.0.0.1:{PORT}"))
            probe.assert_called_once_with(f"http://127.0.0.1:{PORT}/json/version")
        else:
            self.assertIsNone(actual)
            probe.assert_not_called()
        if fixed:
            state.assert_not_called()
        else:
            state.assert_called_once_with(PROFILE, "fake-powershell")

    def test_round2_ipv6_only_owner_never_authorizes_ipv4_probe(self) -> None:
        # Exact Round 2 payload: a single owned ::1 row and a spoofed IPv4 CDP reply.
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                self.assert_resolution(listener("::1"), False, fixed)

    def test_round2_ipv6_owner_does_not_hide_unrelated_ipv4_wildcard(self) -> None:
        for fixed in (False, True):
            with self.subTest(fixed=fixed):
                self.assert_resolution([listener("::1"), listener("0.0.0.0", 777)], False, fixed)

    def test_all_ipv4_dial_owners_participate_before_probe(self) -> None:
        cases = [
            [listener(), listener("0.0.0.0", 777)],
            [listener(owner=777), listener("0.0.0.0")],
            [listener(), listener(owner=777)],
            [listener("0.0.0.0"), listener("0.0.0.0", 777)],
            [listener(owner=101)],  # A renderer PID cannot stand in for its root.
            [listener(owner=777)],  # Round 1 stale-state/unrelated-owner case.
        ]
        for fixed in (False, True):
            for records in cases:
                with self.subTest(fixed=fixed, records=records):
                    self.assert_resolution(records, False, fixed)

    def test_exact_ipv4_and_wildcard_owner_positive_cases(self) -> None:
        cases = [
            [listener()], [listener("0.0.0.0")],
            [listener(), listener("0.0.0.0")], [listener(), listener()],
            [listener(), listener("::1", 777)],
            [listener("0.0.0.0"), listener("192.0.2.1", 777)],
        ]
        for fixed in (False, True):
            for records in cases:
                with self.subTest(fixed=fixed, records=records):
                    self.assert_resolution(records, True, fixed)

    def test_unknown_or_missing_listener_metadata_never_reaches_probe(self) -> None:
        cases = [
            [], None, {}, [1],
            [listener("192.0.2.1")],
            [listener("::")], [listener(), listener("::", 777)],
            [listener("::ffff:127.0.0.1")],
            [listener(), listener("::ffff:0.0.0.0", 777)],
            [listener(), {"OwningProcess": 777}],
            [listener(), {**listener(), "LocalAddress": "not-an-address"}],
            [listener(), {**listener(), "LocalPort": PORT + 1}],
            [listener(), {**listener(), "LocalPort": str(PORT)}],
            [listener(), {**listener(), "LocalAddress": None}],
        ]
        for field in ("OwningProcess", "LocalAddress", "LocalPort"):
            record = listener()
            del record[field]
            cases.append([listener(), record])
        for owner in (0, -1, None, True, 100.5, "100"):
            cases.append([listener(), {**listener(), "OwningProcess": owner}])
        for fixed in (False, True):
            for records in cases:
                with self.subTest(fixed=fixed, records=records):
                    self.assert_resolution(records, False, fixed)

    def test_listener_collection_preserves_all_addresses_ports_and_owners(self) -> None:
        records = [listener("::1"), listener("0.0.0.0", 777), listener("192.0.2.1", 888)]
        with patch.object(edge, "run_powershell", return_value=subprocess.CompletedProcess([], 0, json.dumps(records), "")) as query:
            actual = edge.tcp_listeners("fake-powershell", PORT)
        self.assertEqual(actual, [
            edge.TcpListener(100, "::1", PORT), edge.TcpListener(777, "0.0.0.0", PORT),
            edge.TcpListener(888, "192.0.2.1", PORT),
        ])
        # The query may restrict State/LocalPort, but not discard address records.
        self.assertNotIn("$_.LocalAddress", query.call_args.args[1])

    def test_listener_query_errors_fail_closed(self) -> None:
        for code, output in ((1, "[]"), (0, "not-json")):
            with self.subTest(code=code, output=output), patch.object(edge, "run_powershell", return_value=subprocess.CompletedProcess([], code, output, "")):
                self.assertEqual(edge.loopback_listener_owner("fake-powershell", PORT), ("unavailable", None))
        for error in (OSError("mock error"), subprocess.TimeoutExpired("fake", 20)):
            with self.subTest(error=error), patch.object(edge, "run_powershell", side_effect=error):
                self.assertEqual(edge.loopback_listener_owner("fake-powershell", PORT), ("unavailable", None))


class ProfileRootIdentityTests(unittest.TestCase):
    def test_windows_quoting_preserves_profile_switch_and_root_identity(self) -> None:
        cases = [
            ["msedge.exe", "--remote-debugging-port=0", f"--user-data-dir={PROFILE}"],
            [r"C:\Program Files\Edge\msedge.exe", "--remote-debugging-port", "0", "--user-data-dir", PROFILE + "\\"],
            ["msedge.exe", "--remote-debugging-port=0", f"--user-data-dir={PROFILE}", "--extra=note --type=renderer"],
            ["msedge.exe", "--remote-debugging-port=0", f"--user-data-dir={PROFILE}", "--type="],
            ["msedge.exe", "--remote-debugging-port=0", f"--user-data-dir={PROFILE}", "--", "--type=renderer"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                command_line = subprocess.list2cmdline(arguments)
                self.assertEqual(edge._windows_arguments(command_line), arguments)
                root = edge.EdgeProcess(100, 10, command_line)
                self.assertEqual(edge.dedicated_root_pid([root], PROFILE, 0), 100)
        fully_quoted = edge.EdgeProcess(100, 10, f'msedge.exe "--user-data-dir={PROFILE}" "--remote-debugging-port=0"')
        self.assertEqual(edge.dedicated_root_pid([fully_quoted], PROFILE, 0), 100)
        quoted_child = edge.EdgeProcess(101, 100, fully_quoted.command_line + ' "--type=renderer"')
        self.assertEqual(edge.matching_dedicated_roots([fully_quoted, quoted_child], PROFILE), [fully_quoted])

    def test_all_exact_profile_root_modes_are_retained(self) -> None:
        roots = [process(), process(200, PORT), process(300, None), process(400, None, extra=" --remote-debugging-pipe")]
        children = [process(101, extra=" --type=renderer"), process(102, extra=" --type utility")]
        unrelated = process(500, profile=PROFILE + "-other")
        self.assertEqual(edge.matching_dedicated_roots(roots + children + [unrelated], PROFILE), roots)
        for port in (0, PORT):
            self.assertIsNone(edge.dedicated_root_pid(roots + children, PROFILE, port))

    def test_profile_normalization_is_exact_and_keeps_other_profiles_separate(self) -> None:
        equivalent = PROFILE.upper().replace("\\", "/") + "/child/../"
        root = process(profile=equivalent)
        other = process(200, profile=PROFILE + "2")
        self.assertEqual(edge.matching_dedicated_roots([root, other], PROFILE), [root])
        self.assertEqual(edge.dedicated_root_pid([root, other], PROFILE, 0), 100)

    def test_root_uniqueness_precedes_mode_state_and_listener_validation(self) -> None:
        for fixed in (False, True):
            current = config(fixed)
            root = process(port=current.cdp_port)
            cases = [
                None, [], [process(101, extra=" --type=renderer")],
                [process(port=0 if fixed else PORT)], [process(port=None)],
                [process(port=None, extra=" --remote-debugging-pipe")],
                [root, process(200, 0 if fixed else PORT)], [root, root],
                [root, process(200, None)],
                [process(port=current.cdp_port, extra=f" --remote-debugging-port={PORT}")],
                [process(port=current.cdp_port, extra=f' --user-data-dir="{PROFILE}2"')],
                [process(port=current.cdp_port, extra=" --remote-debugging-port")],
                [process(port=current.cdp_port, extra=' --extra="unterminated')],
                [process(port=current.cdp_port, extra=" --type")],
            ]
            for roots in cases:
                with (
                    self.subTest(fixed=fixed, roots=roots),
                    patch.object(edge, "read_devtools_active_port") as state,
                    patch.object(edge, "run_powershell") as listener_query,
                    patch.object(edge, "probe") as probe,
                ):
                    resolve = edge.resolve_fixed_endpoint if fixed else edge.resolve_auto_endpoint
                    self.assertIsNone(resolve("fake-powershell", current, roots))
                    state.assert_not_called()
                    listener_query.assert_not_called()
                    probe.assert_not_called()

    def test_round2_other_mode_and_all_existing_invalid_roots_block_before_clear_or_launch(self) -> None:
        for fixed in (False, True):
            current = config(fixed)
            cases = [
                [process(port=0 if fixed else PORT)],  # Both exact Round 2 mode transitions.
                [process(port=None)], [process(port=None, extra=" --remote-debugging-pipe")],
                [process(port=current.cdp_port), process(200, 0 if fixed else PORT)],
                [process(port=current.cdp_port), process(200, current.cdp_port)],
                [process(port=current.cdp_port), process(200, None)], None,
                [edge.EdgeProcess(100, 10, f'msedge.exe "--user-data-dir={PROFILE}" --remote-debugging-port={0 if fixed else PORT}')],
                [process(port=current.cdp_port, extra=' --extra="unterminated')],
            ]
            for roots in cases:
                with (
                    self.subTest(fixed=fixed, roots=roots),
                    patch.object(edge, "resolve_runtime_paths", return_value=current),
                    patch.object(edge.shutil, "which", return_value="fake-powershell"),
                    patch.object(edge, "edge_processes", return_value=roots),
                    patch.object(edge, "read_devtools_active_port") as state,
                    patch.object(edge, "run_powershell") as launch,
                    patch.object(edge, "clear_devtools_active_port") as clear,
                    patch.object(edge, "probe") as probe,
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(edge.main(), 1)
                    state.assert_not_called()
                    launch.assert_not_called()
                    clear.assert_not_called()
                    probe.assert_not_called()

    def test_same_mode_unhealthy_root_blocks_with_zero_launches_and_state_clears(self) -> None:
        for fixed in (False, True):
            for records in ([], [listener()], [listener(owner=777)]):
                with (
                    self.subTest(fixed=fixed, records=records),
                    patch.object(edge, "resolve_runtime_paths", return_value=config(fixed)),
                    patch.object(edge.shutil, "which", return_value="fake-powershell"),
                    patch.object(edge, "edge_processes", return_value=[process(port=PORT if fixed else 0)]),
                    patch.object(edge, "read_devtools_active_port", return_value=(PORT, "/devtools/browser/stale-id")),
                    patch.object(edge, "run_powershell", return_value=subprocess.CompletedProcess([], 0, json.dumps(records), "")) as query,
                    patch.object(edge, "clear_devtools_active_port") as clear,
                    patch.object(edge, "probe", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(edge.main(), 1)
                    clear.assert_not_called()
                    self.assertTrue(all("Start-Process" not in call.args[1] for call in query.call_args_list))


class FixedPortLaunchTests(unittest.TestCase):
    def test_no_root_requires_fixed_port_to_have_no_listener_on_any_address(self) -> None:
        cases = [None, [listener()], [listener("0.0.0.0")], [listener("::1")], [listener("::")], [listener("192.0.2.1")]]
        for records in cases:
            with (
                self.subTest(records=records),
                patch.object(edge, "resolve_runtime_paths", return_value=config(True)),
                patch.object(edge.shutil, "which", return_value="fake-powershell"),
                patch.object(edge, "edge_processes", return_value=[]),
                patch.object(edge, "tcp_listeners", return_value=records),
                patch.object(edge, "run_powershell") as launch,
                patch.object(edge, "clear_devtools_active_port") as clear,
                patch.object(edge, "probe") as probe,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(edge.main(), 1)
                launch.assert_not_called()
                clear.assert_not_called()
                probe.assert_not_called()

    def test_empty_listener_query_permits_one_fixed_launch_without_state_file(self) -> None:
        def run(_powershell: str, script: str) -> subprocess.CompletedProcess:
            if "Get-NetTCPConnection" in script:
                return subprocess.CompletedProcess([], 0, "[]", "")
            return subprocess.CompletedProcess([], 1, "", "mock stop after launch")

        with (
            patch.object(edge, "resolve_runtime_paths", return_value=config(True)),
            patch.object(edge.shutil, "which", return_value="fake-powershell"),
            patch.object(edge, "edge_processes", return_value=[]),
            patch.object(edge, "run_powershell", side_effect=run) as calls,
            patch.object(edge, "clear_devtools_active_port") as clear,
            patch.object(edge, "read_devtools_active_port") as state,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(edge.main(), 1)  # The mock intentionally stops after Start-Process.
        launches = [call for call in calls.call_args_list if "Start-Process" in call.args[1]]
        self.assertEqual(len(launches), 1)
        self.assertIn(f"--remote-debugging-port={PORT}", launches[0].args[1])
        clear.assert_not_called()
        state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
