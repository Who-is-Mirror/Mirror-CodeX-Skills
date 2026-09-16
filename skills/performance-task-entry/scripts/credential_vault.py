#!/usr/bin/env python3
"""Store performance-system credentials without persisting plaintext."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from runtime_paths import resolve_runtime_paths


DEFAULT_VAULT_DIR = resolve_runtime_paths().credential_vault
AAD = b"performance-task-entry-credential-v1"


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def paths(vault_dir: Path) -> tuple[Path, Path]:
    return vault_dir / "master.key", vault_dir / "credential.enc.json"


def prepare(vault_dir: Path) -> None:
    vault_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    vault_dir.chmod(0o700)


def load_and_validate(vault_dir: Path) -> dict:
    key_path, credential_path = paths(vault_dir)
    key = key_path.read_bytes()
    if len(key) != 32:
        raise ValueError("invalid master key length")
    envelope = json.loads(credential_path.read_text(encoding="utf-8"))
    nonce = base64.b64decode(envelope["nonce"], validate=True)
    ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    payload = json.loads(AESGCM(key).decrypt(nonce, ciphertext, AAD))
    if payload.get("schema") != "performance-task-entry-credential-v1":
        raise ValueError("unexpected credential schema")
    if not isinstance(payload.get("username"), str) or not payload["username"]:
        raise ValueError("missing username")
    if not isinstance(payload.get("password"), str) or not payload["password"]:
        raise ValueError("missing password")
    return {"updated_at": payload.get("updated_at")}


def store(vault_dir: Path, interactive: bool) -> int:
    if interactive:
        username = getpass.getpass("Account: ").strip()
        password = getpass.getpass("Password: ")
    else:
        request = json.load(sys.stdin)
        username = str(request.get("username", "")).strip()
        password = str(request.get("password", ""))
    if not username or not password:
        raise ValueError("account and password are required")

    prepare(vault_dir)
    key_path, credential_path = paths(vault_dir)
    if key_path.exists():
        key = key_path.read_bytes()
        if len(key) != 32:
            raise ValueError("existing master key is invalid")
    else:
        key = os.urandom(32)
        atomic_write(key_path, key)

    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = json.dumps(
        {
            "schema": "performance-task-entry-credential-v1",
            "username": username,
            "password": password,
            "updated_at": updated_at,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, payload, AAD)
    envelope = json.dumps(
        {
            "schema": "performance-task-entry-vault-v1",
            "cipher": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "updated_at": updated_at,
        },
        indent=2,
    ).encode("utf-8") + b"\n"
    atomic_write(credential_path, envelope)
    load_and_validate(vault_dir)
    print(json.dumps({"status": "stored", "ready": True, "updated_at": updated_at}))
    return 0


def status(vault_dir: Path) -> int:
    key_path, credential_path = paths(vault_dir)
    result = {
        "ready": False,
        "vault_dir": str(vault_dir),
        "key_exists": key_path.exists(),
        "credential_exists": credential_path.exists(),
    }
    try:
        metadata = load_and_validate(vault_dir)
        result.update({"ready": True, **metadata})
    except Exception as error:
        result["error"] = type(error).__name__
    print(json.dumps(result))
    return 0 if result["ready"] else 1


def delete(vault_dir: Path) -> int:
    if vault_dir.exists():
        shutil.rmtree(vault_dir)
    print(json.dumps({"status": "deleted", "ready": False}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("store", "status", "delete"))
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    args = parser.parse_args()
    if args.action == "store":
        return store(args.vault_dir, args.interactive)
    if args.action == "status":
        return status(args.vault_dir)
    return delete(args.vault_dir)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
