#!/usr/bin/env python3
"""Resolve portable, non-secret runtime paths for performance-task-entry."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


SKILL_DIR = Path(__file__).resolve().parent.parent
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WSL_CODEX_HOME = re.compile(r"^/mnt/([A-Za-z])/Users/([^/]+)/\.codex(?:/|$)")
WSL_WINDOWS_HOME = re.compile(r"^/mnt/([A-Za-z])/Users/([^/]+)(?:/|$)")


def _override(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _path_override(name: str, default: Path) -> Path:
    value = _override(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def _windows_profile_from_path(value: Path) -> str | None:
    match = WSL_CODEX_HOME.match(value.as_posix())
    if match:
        return f"{match.group(1).upper()}:\\Users\\{match.group(2)}"
    match = WSL_WINDOWS_HOME.match(value.as_posix())
    if match:
        return f"{match.group(1).upper()}:\\Users\\{match.group(2)}"
    return None


def _normalise_windows_path(value: str) -> str:
    value = value.strip().replace("/", "\\")
    value = re.sub(r"\\+", r"\\", value)
    if not WINDOWS_PATH.match(value):
        raise ValueError("Windows path overrides must use a drive-qualified path")
    return value.rstrip("\\")


def _existing_windows_path(value: str) -> bool:
    """Check a Windows path when this runs under WSL; false is safe elsewhere."""
    match = re.match(r"^([A-Za-z]):\\(.*)$", value)
    if not match:
        return False
    return Path("/mnt", match.group(1).lower(), *match.group(2).split("\\")).exists()


def _edge_executable(windows_profile: str) -> str:
    explicit = _override("PERFORMANCE_EDGE_EXECUTABLE")
    if explicit:
        return _normalise_windows_path(explicit)
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        windows_profile + r"\AppData\Local\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((candidate for candidate in candidates if _existing_windows_path(candidate)), candidates[0])


def _system_origin() -> str:
    value = (_override("PERFORMANCE_SYSTEM_ORIGIN") or "http://192.168.100.235:8080").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
        raise ValueError("PERFORMANCE_SYSTEM_ORIGIN must be an http(s) origin without a path")
    return value


def _cdp_port() -> tuple[int, bool]:
    """Return the requested debugging port and whether it was explicitly fixed.

    Edge chooses a collision-free port when no override is supplied.  A zero port
    is therefore an internal automatic-allocation sentinel, not a valid override.
    """
    value = _override("PERFORMANCE_CDP_PORT")
    if value is None:
        return 0, False
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("PERFORMANCE_CDP_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("PERFORMANCE_CDP_PORT must be between 1 and 65535")
    return port, True


@dataclass(frozen=True)
class RuntimePaths:
    skill_dir: Path
    codex_home: Path
    source_root: Path
    legacy_source_root: Path
    draft_archive_root: Path
    credential_vault: Path
    windows_profile: str
    edge_executable: str
    edge_profile: str
    system_origin: str
    cdp_port: int
    cdp_port_is_override: bool

    @property
    def cdp_endpoint(self) -> str | None:
        """Configured endpoint, if a controlled fixed-port override is in use.

        Automatic allocation has no endpoint until the dedicated Edge writes its
        `DevToolsActivePort` file.  Callers that need CDP must use
        ensure_background_edge.py rather than reconstructing an endpoint.
        """
        if not self.cdp_port_is_override:
            return None
        return f"http://127.0.0.1:{self.cdp_port}/json/version"

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in (
            "skill_dir",
            "codex_home",
            "source_root",
            "legacy_source_root",
            "draft_archive_root",
            "credential_vault",
        ):
            result[key] = str(result[key])
        result["cdp_endpoint"] = self.cdp_endpoint
        return result


def resolve_runtime_paths() -> RuntimePaths:
    codex_home = _path_override("PERFORMANCE_CODEX_HOME", SKILL_DIR.parent.parent)
    source_root = _path_override(
        "PERFORMANCE_SOURCE_ROOT", codex_home / "artifacts" / "akbs-member-ops"
    )
    legacy_source_root = _path_override(
        "PERFORMANCE_LEGACY_SOURCE_ROOT",
        codex_home / "artifacts" / "android-knowledge-intake",
    )
    draft_archive_root = _path_override(
        "PERFORMANCE_DRAFT_ARCHIVE_ROOT", codex_home / "artifacts" / "performance-drafts"
    )
    credential_vault = _path_override(
        "PERFORMANCE_CREDENTIAL_VAULT", Path.home() / ".codex" / "secrets" / "performance-task-entry"
    )
    profile_override = _override("PERFORMANCE_WINDOWS_PROFILE")
    if profile_override:
        windows_profile = _normalise_windows_path(profile_override)
    else:
        windows_profile = _windows_profile_from_path(codex_home)
        if not windows_profile:
            userprofile = _override("USERPROFILE")
            if userprofile and WINDOWS_PATH.match(userprofile):
                windows_profile = _normalise_windows_path(userprofile)
        if not windows_profile:
            raise ValueError(
                "cannot infer Windows profile; set PERFORMANCE_WINDOWS_PROFILE to C:\\Users\\<name>"
            )
    edge_profile = _override("PERFORMANCE_EDGE_PROFILE")
    if edge_profile:
        edge_profile = _normalise_windows_path(edge_profile)
    else:
        edge_profile = windows_profile + r"\AppData\Local\Codex\EdgeBackgroundProfile"
    cdp_port, cdp_port_is_override = _cdp_port()
    return RuntimePaths(
        skill_dir=SKILL_DIR,
        codex_home=codex_home,
        source_root=source_root,
        legacy_source_root=legacy_source_root,
        draft_archive_root=draft_archive_root,
        credential_vault=credential_vault,
        windows_profile=windows_profile,
        edge_executable=_edge_executable(windows_profile),
        edge_profile=edge_profile,
        system_origin=_system_origin(),
        cdp_port=cdp_port,
        cdp_port_is_override=cdp_port_is_override,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print resolved non-secret configuration")
    args = parser.parse_args()
    try:
        config = resolve_runtime_paths().public_dict()
    except ValueError as error:
        print(json.dumps({"ready": False, "error": str(error)}))
        return 2
    if args.json:
        print(json.dumps(config, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(config, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
