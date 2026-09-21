#!/usr/bin/env python3
"""Check and update Mirror Codex Skills once at a coherent task boundary."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_plugin_update import (  # noqa: E402
    MARKETPLACE,
    TARGET_PLUGIN,
    compare_versions,
    fetch_manifest,
    run_codex,
    update_plugin,
    version_parts,
)


SCHEMA = "mirror-codex-task-start-v1"
MANIFEST_URL = (
    "https://raw.githubusercontent.com/Who-is-Mirror/Mirror-CodeX-Skills/"
    "main/plugins/mirror-codex-skills/.codex-plugin/plugin.json"
)
TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_LOCAL_JSON_BYTES = 1024 * 1024
REUSABLE_STATUSES = {
    "PASS",
    "CHECK_FAILED",
    "UPDATE_FAILED",
    "UPDATED_RESTART_REQUIRED",
}
OFFICIAL_REPOSITORY = "https://github.com/Who-is-Mirror/Mirror-CodeX-Skills"


class InstallError(RuntimeError):
    """The executing cache or active Codex inventory is inconsistent."""


def _strict_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} repeats key: {key}")
            value[key] = item
        return value

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number: {value}")

    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _strict_json_file(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be one regular file")
    raw = path.read_bytes()
    if len(raw) > MAX_LOCAL_JSON_BYTES:
        raise ValueError(f"{label} exceeds {MAX_LOCAL_JSON_BYTES} bytes")
    return _strict_json_bytes(raw, label=label)


def _result(status: str, message: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": status,
        "blocking": status != "PASS",
        "reused": False,
        "message": message,
        **fields,
    }


def _save(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".startup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def _nonblocking_lock(path: Path) -> Iterator[bool]:
    """Use the platform's advisory file lock without leaving a stale lock sentinel."""
    if path.is_symlink():
        raise ValueError("update lock must not be a symbolic link")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    stream = os.fdopen(descriptor, "a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _read_inventory(run_command: Callable[[list[str], int], Any]) -> dict[str, Any]:
    command = [
        "codex",
        "plugin",
        "list",
        "--marketplace",
        MARKETPLACE,
        "--available",
        "--json",
    ]
    completed = run_command(command, 60)
    if completed.returncode != 0:
        raise InstallError("codex plugin list failed")
    try:
        payload = _strict_json_bytes(completed.stdout.encode("utf-8"), label="plugin inventory")
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise InstallError(f"plugin inventory is invalid: {exc}") from exc
    if not isinstance(payload.get("installed"), list):
        raise InstallError("plugin inventory has no installed list")
    return payload


def _installed_target(inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    installed = inventory.get("installed")
    if not isinstance(installed, list):
        raise InstallError("plugin inventory has no installed list")
    targets = [
        row
        for row in installed
        if isinstance(row, Mapping)
        and row.get("name") == TARGET_PLUGIN
        and row.get("marketplaceName") == MARKETPLACE
        and row.get("installed") is True
        and row.get("enabled") is True
    ]
    if len(targets) != 1:
        raise InstallError("Mirror plugin is not uniquely installed and enabled")
    target = targets[0]
    source = target.get("marketplaceSource")
    if not isinstance(source, Mapping) or source.get("sourceType") != "git":
        raise InstallError("Mirror marketplace is not installed from Git")
    repository = str(source.get("source") or "").strip().rstrip("/")
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if repository.casefold() != OFFICIAL_REPOSITORY.casefold():
        raise InstallError("Mirror marketplace Git source is not the official repository")
    return target


def _cache_root(home: Path, version: str) -> Path:
    return home / "plugins" / "cache" / MARKETPLACE / TARGET_PLUGIN / version


def _manifest_binding(
    plugin_root: Path,
    home: Path,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    root = plugin_root.expanduser().resolve(strict=True)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = _strict_json_file(manifest_path, label="active plugin manifest")
    if manifest.get("name") != TARGET_PLUGIN:
        raise InstallError("active manifest names a different plugin")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise InstallError("active manifest has no version")
    version_parts(version)
    row = _installed_target(inventory)
    if row.get("version") != version:
        raise InstallError("active inventory version differs from executing manifest")
    expected = _cache_root(home, version).resolve(strict=True)
    if root != expected:
        raise InstallError("executing plugin root is not the active Codex cache")
    return {
        "plugin_name": TARGET_PLUGIN,
        "marketplace_name": MARKETPLACE,
        "execution_root": str(root),
        "local_version": version,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def _verify_updated_install(
    home: Path,
    expected_version: str,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    row = _installed_target(inventory)
    version = row.get("version")
    if not isinstance(version, str):
        raise InstallError("updated inventory has no version")
    version_parts(version)
    if compare_versions(version, expected_version) < 0:
        raise InstallError("updated inventory is older than the advertised release")
    root = _cache_root(home, version).resolve(strict=True)
    manifest = _strict_json_file(
        root / ".codex-plugin" / "plugin.json",
        label="updated plugin manifest",
    )
    if manifest.get("name") != TARGET_PLUGIN or manifest.get("version") != version:
        raise InstallError("updated cache manifest differs from active inventory")
    return {
        "installed_plugin_active": True,
        "installed_plugin_version": version,
        "installed_plugin_path": str(root),
    }


def ensure_task_started(
    plugin_root: Path,
    task_id: str,
    *,
    retry: bool = False,
    codex_home: Path | None = None,
    fetch_remote: Callable[[], Mapping[str, Any]] | None = None,
    run_command: Callable[[list[str], int], Any] = run_codex,
    inventory_reader: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check exactly once per task/root/version and update only at that boundary."""
    if not TASK_ID_RE.fullmatch(task_id):
        return _result(
            "INVALID_TASK_ID",
            "请为当前任务选择一个稳定的字母、数字、点、下划线或短横线标识。",
        )
    home = Path(
        codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    ).expanduser().resolve()
    read_inventory = inventory_reader or (lambda: _read_inventory(run_command))
    fetch_current = fetch_remote or (lambda: fetch_manifest(MANIFEST_URL))
    try:
        binding = {
            "task_id": task_id,
            **_manifest_binding(plugin_root, home, read_inventory()),
        }
        state_dir = home / "artifacts" / TARGET_PLUGIN / "startup"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_dir = state_dir.resolve(strict=True)
        state_path = state_dir / f"{task_id}.json"
    except (InstallError, OSError, ValueError) as exc:
        return _result(
            "INSTALL_FAMILY_INVALID",
            f"当前 Mirror 插件安装或执行缓存不一致：{exc}。如刚更新过，请重启 Codex。",
        )

    try:
        with _nonblocking_lock(state_dir / ".update.lock") as acquired:
            if not acquired:
                return _result(
                    "STARTUP_BUSY",
                    "另一个任务正在检查或更新 Mirror 插件；本次未开始业务操作。",
                    **binding,
                )
            # Inventory may have changed while this task waited for the lock.
            _manifest_binding(plugin_root, home, read_inventory())
            if state_path.exists() or state_path.is_symlink():
                previous = _strict_json_file(state_path, label="task startup record")
                if (
                    previous.get("schema") != SCHEMA
                    or previous.get("task_id") != task_id
                    or previous.get("status") not in REUSABLE_STATUSES
                    or previous.get("blocking")
                    is not (previous.get("status") != "PASS")
                ):
                    raise ValueError("task startup record identity or status is invalid")
                same_binding = all(
                    previous.get(key) == value for key, value in binding.items()
                )
                if same_binding and (
                    previous.get("status") == "PASS" or not retry
                ):
                    return {**previous, "reused": True}
                if not same_binding and previous.get("status") == "PASS":
                    return _result(
                        "TASK_VERSION_CHANGED",
                        "该任务已经绑定其他插件版本；先结束旧任务，不自动热切换。",
                        **binding,
                    )
                if (
                    not same_binding
                    and previous.get("status") != "UPDATED_RESTART_REQUIRED"
                    and not retry
                ):
                    return _result(
                        "TASK_VERSION_CHANGED",
                        "任务启动记录与当前插件缓存不同；确认安全后显式重试。",
                        **binding,
                    )

            try:
                remote = fetch_current()
                if not isinstance(remote, Mapping):
                    raise ValueError("remote manifest is not an object")
                if remote.get("name") != TARGET_PLUGIN:
                    raise ValueError("remote manifest names a different plugin")
                remote_version = remote.get("version")
                if not isinstance(remote_version, str):
                    raise ValueError("remote manifest has no version")
                version_parts(remote_version)
            except Exception as exc:
                result = _result(
                    "CHECK_FAILED",
                    f"无法确认 Mirror 插件最新版本（{type(exc).__name__}）；修复后用 --retry 重试。",
                    **binding,
                )
            else:
                if compare_versions(remote_version, binding["local_version"]) <= 0:
                    result = _result(
                        "PASS",
                        "Mirror 插件版本检查通过；本任务后续步骤复用该结果。",
                        remote_version=remote_version,
                        **binding,
                    )
                else:
                    updated = update_plugin(
                        run_command=run_command,
                        verify_install=lambda: _verify_updated_install(
                            home, remote_version, read_inventory()
                        ),
                        expected_version=remote_version,
                    )
                    if updated.get("status") == "PASS":
                        installed_version = updated["installed_plugin_version"]
                        result = _result(
                            "UPDATED_RESTART_REQUIRED",
                            f"Mirror 插件已更新至 {installed_version}。请退出并重启 Codex，再继续原任务。",
                            remote_version=remote_version,
                            installed_version=installed_version,
                            **binding,
                        )
                    else:
                        result = _result(
                            "UPDATE_FAILED",
                            "检测到新版本，但自动更新未确认成功；检查 Marketplace 后用 --retry 重试。",
                            remote_version=remote_version,
                            update_reason=updated.get("reason", "unknown"),
                            **binding,
                        )
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            _save(state_path, result)
            return result
    except (InstallError, OSError, ValueError) as exc:
        return _result(
            "STARTUP_STATE_INVALID",
            f"任务启动状态无法确认：{exc}。本次未开始业务操作。",
            **binding,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task-id",
        required=True,
        help="Stable ID for one coherent user task; reuse after restart and across skills.",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry a retained failed startup check.",
    )
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    result = ensure_task_started(args.plugin_root, args.task_id, retry=args.retry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["blocking"] else 78


if __name__ == "__main__":
    raise SystemExit(main())
