#!/usr/bin/env python3
"""Resolve one portable, non-secret dedicated Edge session configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WSL_WINDOWS_HOME = re.compile(r"^/mnt/([A-Za-z])/Users/([^/]+)(?:/|$)", re.IGNORECASE)
SESSION_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _override(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _normalise_windows_path(value: str) -> str:
    value = value.strip().replace("/", "\\")
    value = re.sub(r"\\+", r"\\", value)
    if not WINDOWS_PATH.match(value):
        raise ValueError("Windows path overrides must use a drive-qualified path")
    return value.rstrip("\\")


def _windows_profile_from_path(value: Path) -> str | None:
    match = WSL_WINDOWS_HOME.match(value.resolve().as_posix())
    if not match:
        return None
    return f"{match.group(1).upper()}:\\Users\\{match.group(2)}"


def _existing_windows_path(value: str) -> bool:
    match = re.match(r"^([A-Za-z]):\\(.*)$", value)
    if not match:
        return False
    return Path("/mnt", match.group(1).lower(), *match.group(2).split("\\")).exists()


def _windows_profile() -> str:
    explicit = _override("EDGE_CDP_WINDOWS_PROFILE")
    if explicit:
        return _normalise_windows_path(explicit)
    for candidate in (Path.cwd(), SKILL_DIR, Path.home()):
        inferred = _windows_profile_from_path(candidate)
        if inferred:
            return inferred
    userprofile = _override("USERPROFILE")
    if userprofile and WINDOWS_PATH.match(userprofile):
        return _normalise_windows_path(userprofile)
    raise ValueError(
        "cannot infer Windows profile; set EDGE_CDP_WINDOWS_PROFILE to C:\\Users\\<name>"
    )


def _edge_executable(windows_profile: str) -> str:
    explicit = _override("EDGE_CDP_EXECUTABLE")
    if explicit:
        return _normalise_windows_path(explicit)
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        windows_profile + r"\AppData\Local\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((item for item in candidates if _existing_windows_path(item)), candidates[0])


def _session_name() -> str:
    value = (_override("EDGE_CDP_SESSION") or "default").lower()
    if not SESSION_NAME.fullmatch(value):
        raise ValueError("EDGE_CDP_SESSION must match [a-z0-9][a-z0-9._-]{0,63}")
    return value


def _cdp_port() -> tuple[int, bool]:
    value = _override("EDGE_CDP_PORT")
    if value is None:
        return 0, False
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("EDGE_CDP_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("EDGE_CDP_PORT must be between 1 and 65535")
    return port, True


@dataclass(frozen=True)
class RuntimePaths:
    session: str
    windows_profile: str
    edge_executable: str
    edge_profile: str
    cdp_port: int
    cdp_port_is_override: bool


def resolve_runtime_paths() -> RuntimePaths:
    session = _session_name()
    windows_profile = _windows_profile()
    explicit_profile = _override("EDGE_CDP_PROFILE")
    edge_profile = (
        _normalise_windows_path(explicit_profile)
        if explicit_profile
        else windows_profile + rf"\AppData\Local\Codex\EdgeSessions\{session}"
    )
    cdp_port, cdp_port_is_override = _cdp_port()
    return RuntimePaths(
        session=session,
        windows_profile=windows_profile,
        edge_executable=_edge_executable(windows_profile),
        edge_profile=edge_profile,
        cdp_port=cdp_port,
        cdp_port_is_override=cdp_port_is_override,
    )
