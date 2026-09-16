#!/usr/bin/env python3
"""Ensure the dedicated headless Edge CDP endpoint exists without touching normal Edge."""

from __future__ import annotations

import errno
import ipaddress
import json
import ntpath
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from runtime_paths import resolve_runtime_paths


STARTUP_TIMEOUT_SECONDS = 30.0
STARTUP_POLL_INTERVAL_SECONDS = 0.25


def run_powershell(powershell: str, script: str) -> subprocess.CompletedProcess[str]:
    """Run Windows PowerShell from WSL, including sessions without binfmt registration."""

    command = [powershell, "-NoProfile", "-NonInteractive", "-Command", script]
    options = {
        "capture_output": True,
        "text": True,
        "timeout": 20,
        "check": False,
    }
    try:
        return subprocess.run(command, **options)
    except OSError as error:
        if error.errno != errno.ENOEXEC or not os.access("/init", os.X_OK):
            raise
        return subprocess.run(["/init", *command], **options)

def probe(endpoint: str) -> dict | None:
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            data = json.load(response)
        return data if data.get("webSocketDebuggerUrl") else None
    except Exception:
        return None


def profile_state_path(edge_profile: str) -> Path:
    """Map the dedicated drive-qualified Windows profile to its WSL mount.

    The resolver only accepts drive-qualified Windows paths.  Keeping this
    conversion here makes the state-file read explicit and prevents a caller
    from substituting an arbitrary Linux path for the dedicated profile.
    """
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", edge_profile)
    if not match:
        raise ValueError("dedicated Edge profile is not a drive-qualified Windows path")
    return Path("/mnt", match.group(1).lower(), *filter(None, match.group(2).split("\\")))


def _parse_devtools_active_port(contents: str) -> tuple[int, str] | None:
    """Parse a complete Chromium DevToolsActivePort payload, or reject it."""
    lines = contents.splitlines()
    if len(lines) != 2:
        return None
    try:
        port = int(lines[0])
    except ValueError:
        return None
    websocket_path = lines[1]
    if not 1 <= port <= 65535 or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", websocket_path):
        return None
    return port, websocket_path


def _powershell_literal(value: str) -> str:
    """Quote one literal string for an embedded PowerShell command."""
    return "'" + value.replace("'", "''") + "'"


def _windows_state_path(edge_profile: str) -> str:
    # Validate the profile with the same strict drive-qualified conversion used
    # by the local WSL path before passing its logical Windows path onward.
    profile_state_path(edge_profile)
    return ntpath.join(edge_profile, "DevToolsActivePort")


def read_devtools_active_port(
    edge_profile: str, powershell: str | None = None
) -> tuple[int, str] | None:
    """Read dedicated CDP state through the WSL or redirected Windows view.

    Codex Desktop can virtualize ``AppData\\Local\\Codex`` for child Windows
    processes.  In that case Edge and PowerShell see the package LocalCache
    target while WSL's direct ``/mnt/<drive>`` mapping sees the physical path.
    Prefer the cheap local read, then ask PowerShell to read the same logical
    Windows path so Windows applies any package redirection.
    """
    state_file = profile_state_path(edge_profile) / "DevToolsActivePort"
    try:
        if state_file.is_file():
            local_state = _parse_devtools_active_port(state_file.read_text(encoding="utf-8"))
            if local_state:
                return local_state
    except OSError:
        pass
    if not powershell:
        return None
    windows_state = _windows_state_path(edge_profile)
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$path={_powershell_literal(windows_state)}; "
        "if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { exit 3 }; "
        "[Console]::Out.Write([IO.File]::ReadAllText($path))"
    )
    try:
        result = run_powershell(powershell, script)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return _parse_devtools_active_port(result.stdout)


def clear_devtools_active_port(edge_profile: str, powershell: str | None = None) -> bool:
    """Remove only the dedicated profile's stale CDP state in both views."""
    state_file = profile_state_path(edge_profile) / "DevToolsActivePort"
    removed = False
    try:
        if state_file.is_file():
            state_file.unlink()
            removed = True
    except OSError:
        pass
    if not powershell:
        return removed
    windows_state = _windows_state_path(edge_profile)
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$path={_powershell_literal(windows_state)}; "
        "if (Test-Path -LiteralPath $path -PathType Leaf) { "
        "Remove-Item -LiteralPath $path -Force; [Console]::Out.Write('1') "
        "} else { [Console]::Out.Write('0') }"
    )
    try:
        result = run_powershell(powershell, script)
    except (OSError, subprocess.TimeoutExpired):
        return removed
    return removed or (result.returncode == 0 and result.stdout.strip() == "1")


def endpoint_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/json/version"


def endpoint_matches_port(data: dict | None, port: int) -> bool:
    if not data:
        return False
    websocket = data.get("webSocketDebuggerUrl")
    if not isinstance(websocket, str):
        return False
    try:
        parsed = urlparse(websocket)
        return (
            parsed.scheme == "ws"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == port
            and bool(re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", parsed.path))
        )
    except ValueError:
        return False


def endpoint_matches_state(data: dict | None, port: int, websocket_path: str) -> bool:
    if not endpoint_matches_port(data, port):
        return False
    return urlparse(data["webSocketDebuggerUrl"]).path == websocket_path


@dataclass(frozen=True)
class EdgeProcess:
    pid: int
    parent_pid: int
    command_line: str


def edge_processes(powershell: str) -> list[EdgeProcess] | None:
    """Return Edge PID/parent/command-line metadata through Windows.

    This is intentionally process metadata rather than a process-name kill or
    reuse heuristic.  Missing metadata is an ownership failure, not permission
    to attach to an endpoint.
    """
    script = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'msedge.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = run_powershell(powershell, script)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        decoded = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    records = [] if decoded is None else decoded if isinstance(decoded, list) else [decoded]
    processes: list[EdgeProcess] = []
    for record in records:
        if not isinstance(record, dict):
            return None
        try:
            pid = record["ProcessId"]
            parent_pid = record["ParentProcessId"]
            command_line = record["CommandLine"]
        except KeyError:
            return None
        if (
            type(pid) is not int or pid <= 0
            or type(parent_pid) is not int or parent_pid < 0
            or not isinstance(command_line, str) or not command_line.strip()
        ):
            return None
        processes.append(EdgeProcess(pid, parent_pid, command_line))
    return processes


def _windows_arguments(command_line: str) -> list[str] | None:
    """Tokenize Windows double-quote/backslash syntax, rejecting open quotes.

    Inspect actual switch tokens, not text embedded inside another argument.
    This also preserves switches quoted as a whole, as list2cmdline emits for
    an executable/profile path containing spaces.
    """
    arguments: list[str] = []
    index = 0
    while index < len(command_line):
        if command_line[index] in " \t":
            index += 1
            continue
        value: list[str] = []
        quoted = False
        while index < len(command_line):
            character = command_line[index]
            if character in " \t" and not quoted:
                break
            slashes = 0
            while index < len(command_line) and command_line[index] == "\\":
                slashes += 1
                index += 1
            if index < len(command_line) and command_line[index] == '"':
                value.append("\\" * (slashes // 2))
                if slashes % 2:
                    value.append('"')
                elif quoted and command_line[index:index + 2] == '""':
                    value.append('"')
                    index += 1
                else:
                    quoted = not quoted
            else:
                value.append("\\" * slashes)
                if index == len(command_line) or (command_line[index] in " \t" and not quoted):
                    break
                value.append(command_line[index])
            index += 1
        if quoted:
            return None
        arguments.append("".join(value))
    return arguments


def _switch_values(arguments: list[str], name: str) -> list[str | None]:
    values: list[str | None] = []
    for index, argument in enumerate(arguments[1:], 1):
        if argument == "--":
            break
        key, equals, value = argument.partition("=")
        if key.lower() != name:
            continue
        if not equals:
            value = arguments[index + 1] if index + 1 < len(arguments) else None
            if value is not None and value.startswith("--"):
                value = None
        values.append(value)
    return values


def _command_argument(command_line: str, name: str) -> str | None:
    arguments = _windows_arguments(command_line)
    if arguments is None:
        return None
    values = _switch_values(arguments, name)
    return values[0] if len(values) == 1 else None


def _normalise_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.strip().replace("/", "\\")))


def is_root_browser(process: EdgeProcess) -> bool:
    arguments = _windows_arguments(process.command_line)
    if arguments is None:
        return False
    types = _switch_values(arguments, "--type")
    return not types or types == [""]


def matching_dedicated_roots(
    processes: list[EdgeProcess] | None, edge_profile: str
) -> list[EdgeProcess] | None:
    """Find every root using this profile, independently of its debugging mode."""
    if processes is None:
        return None
    expected_profile = _normalise_windows_path(edge_profile)
    matches: list[EdgeProcess] = []
    for process in processes:
        arguments = _windows_arguments(process.command_line)
        if not arguments:
            return None
        types = _switch_values(arguments, "--type")
        if len(types) > 1 or types == [None]:
            return None
        if types and types != [""]:
            continue
        profiles = _switch_values(arguments, "--user-data-dir")
        # An unreadable or repeated profile switch cannot establish that this
        # root is unrelated; never use it as permission to clear state/launch.
        if len(profiles) > 1 or profiles == [None]:
            return None
        if not profiles:
            continue
        profile = profiles[0]
        if _normalise_windows_path(profile) == expected_profile:
            matches.append(process)
    return matches


def dedicated_root_pid(
    processes: list[EdgeProcess] | None, edge_profile: str, port: int
) -> int | None:
    """Check the expected mode only after profile-wide root uniqueness."""
    roots = matching_dedicated_roots(processes, edge_profile)
    if roots is None or len(roots) != 1:
        return None
    root = roots[0]
    if _command_argument(root.command_line, "--remote-debugging-port") != str(port):
        return None
    return root.pid


@dataclass(frozen=True)
class TcpListener:
    owner_pid: int
    local_address: str
    local_port: int


def tcp_listeners(powershell: str, port: int) -> list[TcpListener] | None:
    """Keep every address/port/PID record for the requested Windows TCP port.

    Filter by port after enumeration so a free port produces an empty result,
    rather than Get-NetTCPConnection's no-match error for -LocalPort.  No
    address is removed before ownership and launch-conflict decisions.
    """
    script = (
        "$ErrorActionPreference='Stop'; "
        "Get-NetTCPConnection -ErrorAction Stop | "
        f"Where-Object {{ $_.State -eq 'Listen' -and $_.LocalPort -eq {port} }} | "
        "Select-Object OwningProcess,LocalAddress,LocalPort | ConvertTo-Json -Compress"
    )
    try:
        result = run_powershell(powershell, script)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        decoded = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    records = [] if decoded is None else decoded if isinstance(decoded, list) else [decoded]
    listeners: list[TcpListener] = []
    for record in records:
        if not isinstance(record, dict):
            return None
        try:
            owner = record["OwningProcess"]
            address = record["LocalAddress"]
            local_port = record["LocalPort"]
            if (
                type(owner) is not int or owner <= 0
                or type(local_port) is not int or local_port != port
                or not 1 <= local_port <= 65535 or not isinstance(address, str)
            ):
                return None
            ipaddress.ip_address(address)
        except (KeyError, TypeError, ValueError):
            return None
        listeners.append(TcpListener(owner, address, local_port))
    return listeners


def loopback_listener_owner(powershell: str, port: int) -> tuple[str, int | None]:
    """Decide ownership for the actual IPv4 dial target, 127.0.0.1.

    Both exact IPv4 and IPv4 wildcard records can receive it, so every such
    owner participates.  IPv6-only addresses never authorize IPv4.  An IPv6
    wildcard or mapped address lacks the socket's dual-stack metadata here;
    fail closed instead of assuming which connections it can accept.
    """
    listeners = tcp_listeners(powershell, port)
    if listeners is None:
        return "unavailable", None
    owners: set[int] = set()
    for listener in listeners:
        address = ipaddress.ip_address(listener.local_address)
        if isinstance(address, ipaddress.IPv6Address) and (address.is_unspecified or address.ipv4_mapped):
            return "unavailable", None
        if address in (ipaddress.IPv4Address("127.0.0.1"), ipaddress.IPv4Address("0.0.0.0")):
            owners.add(listener.owner_pid)
    if not owners:
        return "none", None
    if len(owners) != 1:
        return "ambiguous", None
    return "single", owners.pop()


def resolve_auto_endpoint(
    powershell: str, config, processes: list[EdgeProcess] | None
) -> tuple[dict, str] | None:
    """Resolve automatic CDP only from an owned root plus its state file."""
    root_pid = dedicated_root_pid(processes, config.edge_profile, 0)
    if root_pid is None:
        return None
    state = read_devtools_active_port(config.edge_profile, powershell)
    if not state:
        return None
    port, websocket_path = state
    listener_status, listener_pid = loopback_listener_owner(powershell, port)
    if listener_status != "single" or listener_pid != root_pid:
        return None
    data = probe(endpoint_for(port))
    if not endpoint_matches_state(data, port, websocket_path):
        return None
    return data, endpoint_for(port).removesuffix("/json/version")


def resolve_fixed_endpoint(
    powershell: str, config, processes: list[EdgeProcess] | None
) -> tuple[dict, str] | None:
    """Resolve an explicit override without relying on DevToolsActivePort."""
    root_pid = dedicated_root_pid(processes, config.edge_profile, config.cdp_port)
    if root_pid is None:
        return None
    listener_status, listener_pid = loopback_listener_owner(powershell, config.cdp_port)
    if listener_status != "single" or listener_pid != root_pid:
        return None
    data = probe(endpoint_for(config.cdp_port))
    if not endpoint_matches_port(data, config.cdp_port):
        return None
    return data, endpoint_for(config.cdp_port).removesuffix("/json/version")


def result(data: dict, cdp_http: str, started: bool) -> dict:
    parsed = urlparse(cdp_http)
    return {
        "ready": True,
        "started": started,
        "cdp_http_endpoint": cdp_http,
        "cdp_port": parsed.port,
        "webSocketDebuggerUrl": data["webSocketDebuggerUrl"],
    }


def resolve_started_endpoint(powershell: str, config) -> tuple[tuple[dict, str] | None, str]:
    """Try one post-launch resolution and report its current readiness phase.

    Automatic mode deliberately checks the profile-local state file before any
    Windows process or listener query.  A new Edge cannot have a usable dynamic
    endpoint until that complete file exists, so this avoids repeatedly paying
    for PowerShell queries during normal profile initialization.  The eventual
    resolution still runs every existing ownership and endpoint check.
    """
    if not config.cdp_port_is_override and not read_devtools_active_port(
        config.edge_profile, powershell
    ):
        return None, "waiting_for_devtools_active_port"

    processes = edge_processes(powershell)
    if processes is None:
        return None, "reading_edge_process_metadata"
    roots = matching_dedicated_roots(processes, config.edge_profile)
    if roots is None:
        return None, "validating_dedicated_edge_processes"
    if not roots:
        return None, "waiting_for_dedicated_edge_root"
    if len(roots) != 1:
        return None, "validating_unique_dedicated_edge_root"
    if _command_argument(roots[0].command_line, "--remote-debugging-port") != str(config.cdp_port):
        return None, "validating_debugging_mode"

    current = (
        resolve_fixed_endpoint(powershell, config, processes)
        if config.cdp_port_is_override
        else resolve_auto_endpoint(powershell, config, processes)
    )
    return current, "ready" if current else "validating_listener_and_cdp_endpoint"


def wait_for_started_endpoint(
    powershell: str,
    config,
    *,
    timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
    poll_interval_seconds: float = STARTUP_POLL_INTERVAL_SECONDS,
) -> tuple[tuple[dict, str] | None, str, float]:
    """Wait against a monotonic deadline and include one final attempt.

    The previous fixed iteration count measured only explicit sleeps while each
    iteration could also spend time in Windows metadata queries.  Checking the
    deadline after every attempt means an attempt that crosses it is already the
    final validation; sleeping exactly to the deadline leads to one last pass.
    """
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    phase = "starting_edge"
    while True:
        current, phase = resolve_started_endpoint(powershell, config)
        now = time.monotonic()
        elapsed = max(0.0, now - started_at)
        if current or now >= deadline:
            return current, phase, elapsed
        time.sleep(min(poll_interval_seconds, deadline - now))


def main() -> int:
    try:
        config = resolve_runtime_paths()
    except ValueError as error:
        print(json.dumps({"ready": False, "error": str(error)}))
        return 1
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        print(json.dumps({"ready": False, "error": "powershell.exe not found; cannot start Windows Edge"}))
        return 1
    processes = edge_processes(powershell)
    matching_roots_before = matching_dedicated_roots(processes, config.edge_profile)
    existing = (
        resolve_fixed_endpoint(powershell, config, processes)
        if config.cdp_port_is_override
        else resolve_auto_endpoint(powershell, config, processes)
    )
    if existing:
        data, cdp_http = existing
        print(json.dumps(result(data, cdp_http, False)))
        return 0

    # A fixed override is a troubleshooting escape hatch, never a way to adopt
    # an already-listening service.  The automatic path does not scan ports.
    if matching_roots_before is None:
        print(json.dumps({"ready": False, "error": "cannot verify dedicated Edge process ownership"}))
        return 1
    if matching_roots_before:
        print(json.dumps({"ready": False, "error": "owned dedicated Edge endpoint could not be validated"}))
        return 1
    if config.cdp_port_is_override:
        listeners = tcp_listeners(powershell, config.cdp_port)
        # A new Edge may bind more than IPv4 loopback.  Without a profile root
        # to validate, conservatively require the fixed port to have no TCP
        # listeners at any address before launch.
        if listeners is None or listeners:
            error = (
                "configured CDP port is occupied by a non-owned endpoint"
                if listeners
                else "configured CDP port listener ownership could not be validated"
            )
            print(json.dumps({"ready": False, "error": error}))
            return 1
    if not config.cdp_port_is_override:
        # A prior crashed automatic Edge can leave this file behind.  No owned
        # root exists, so removing this one exact profile state file avoids it
        # being mistaken for the new instance; it never touches normal Edge.
        clear_devtools_active_port(config.edge_profile, powershell)

    # No occupied override and no root using this profile remain.  Port zero
    # avoids a free-port scan race when creating the dedicated browser.
    arguments = [
        f"--remote-debugging-port={config.cdp_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={config.edge_profile}",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
    script = (
        f"Start-Process -FilePath '{config.edge_executable.replace(chr(39), chr(39) * 2)}' "
        f"-ArgumentList @({quoted}) -WindowStyle Hidden"
    )
    try:
        run = run_powershell(powershell, script)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ready": False, "error": "PowerShell timed out while starting Edge"}))
        return 1
    except OSError as error:
        print(
            json.dumps(
                {
                    "ready": False,
                    "error": "PowerShell could not be executed",
                    "detail": f"{type(error).__name__}: {error}",
                }
            )
        )
        return 1
    if run.returncode != 0:
        print(json.dumps({"ready": False, "error": "Edge start failed", "detail": run.stderr.strip()}))
        return 1
    current, phase, elapsed = wait_for_started_endpoint(powershell, config)
    if current:
        data, cdp_http = current
        print(json.dumps(result(data, cdp_http, True)))
        return 0
    print(
        json.dumps(
            {
                "ready": False,
                "error": "CDP endpoint did not become ready before the startup deadline",
                "phase": phase,
                "elapsed_seconds": round(elapsed, 3),
                "timeout_seconds": STARTUP_TIMEOUT_SECONDS,
            }
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
