"""Sensitive current-schema portable backups shared by HTTP and offline CLI.

Restore requires exclusive runtime ownership (offline CLI) or an idle manager
with serialized API mutations. No player is activated by restore.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import __git_hash__, __version__
from .schema import COLLECTIONS, SCHEMA_VERSION, validate_current
from .storage import Storage

FORMAT = "huesync-config-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 4 * 1024 * 1024
_ENVELOPE_KEYS = {
    "format",
    "backup_version",
    "schema_version",
    "created_at",
    "huesync_version",
    "huesync_commit",
    "contains_secrets",
    "configuration",
}


class BackupError(ValueError):
    """Only fixed, non-sensitive diagnostics may cross API/CLI boundaries."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("Duplicate JSON fields are not allowed")
        result[key] = value
    return result


def _json(raw: bytes) -> dict:
    if len(raw) > MAX_BACKUP_BYTES:
        raise BackupError("Backup exceeds the 4 MiB limit")
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise BackupError("Invalid JSON backup") from None


def _configuration(data: dict, *, complete: bool) -> dict:
    try:
        validate_current(data, references=True)
        result = copy.deepcopy(data)
        for key, model in COLLECTIONS.items():
            normalized = [model.from_dict(row).to_dict() for row in data[key]]
            if complete and any(
                set(row) != set(full) for row, full in zip(data[key], normalized, strict=True)
            ):
                raise ValueError("Incomplete entity")
            result[key] = normalized
        validate_current(result, references=True)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        # Schema exceptions may contain IDs or user-supplied field values.
        raise BackupError(
            "Invalid configuration: check fields, types, IDs and references"
        ) from None


def export_configuration(storage: Storage) -> dict:
    try:
        data = _configuration(storage.read_configuration(), complete=False)
    except (OSError, ValueError, TypeError, KeyError):
        raise BackupError("Cannot export invalid or unreadable current configuration") from None
    data["active_coupling_id"] = None  # session status is not portable user configuration
    result = {
        "format": FORMAT,
        "backup_version": BACKUP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "huesync_version": __version__,
        "huesync_commit": __git_hash__,
        "contains_secrets": True,
        "configuration": data,
    }
    encode_backup(result)  # refuse an export that cannot later be imported
    return result


def validate_backup(backup: dict) -> dict:
    if not isinstance(backup, dict) or set(backup) != _ENVELOPE_KEYS:
        raise BackupError("Invalid or incomplete backup envelope")
    if backup["format"] != FORMAT:
        raise BackupError("Unsupported backup format")
    if type(backup["backup_version"]) is not int or backup["backup_version"] != BACKUP_VERSION:
        raise BackupError("Unsupported backup version")
    if type(backup["schema_version"]) is not int or backup["schema_version"] != SCHEMA_VERSION:
        raise BackupError("Unsupported configuration schema version")
    if backup["contains_secrets"] is not True:
        raise BackupError("Full backups must declare contains_secrets=true")
    for key in ("huesync_version", "huesync_commit"):
        if not isinstance(backup[key], str) or not 0 < len(backup[key]) <= 128:
            raise BackupError("Invalid application metadata")
    try:
        datetime.strptime(backup["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        raise BackupError("Invalid UTC creation timestamp") from None
    data = _configuration(backup["configuration"], complete=True)
    if data["active_coupling_id"] is not None:
        raise BackupError("Backup must not contain active session state")
    return data


def decode_backup(raw: bytes) -> dict:
    backup = _json(raw)
    validate_backup(backup)
    return backup


def encode_backup(backup: dict) -> bytes:
    validate_backup(backup)
    raw = (json.dumps(backup, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_BACKUP_BYTES:
        raise BackupError("Backup exceeds the 4 MiB limit")
    return raw


def export_file(storage: Storage, path: Path) -> None:
    raw = encode_backup(export_configuration(storage))
    # No overwrite, including symlinks. Secrets never enter an insecure temp file.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise BackupError("Cannot create backup file; destination must not already exist") from None
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        path.unlink(missing_ok=True)
        raise BackupError("Backup file write failed") from None


def restore_configuration(storage: Storage | Path, backup: dict) -> Path:
    """Commit a validated full configuration, after a durable exact-byte safety copy.

    No fallible runtime reload follows the commit: callers must already be idle
    or offline. Storage reads the file afresh; activation is a separate user action.
    Validation/preparation/rename failure leaves original bytes untouched.
    """
    data = validate_backup(backup)
    raw = (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    temporary = None
    try:
        with Storage.configuration_transaction():
            path = storage.path if isinstance(storage, Storage) else Path(storage)
            previous = path.read_bytes()
            metadata = path.stat()
            digest = hashlib.sha256(previous).hexdigest()
            # Unique copies avoid reusing a root-created 0600 file from the API
            # service account, and retain every failed-attempt safety copy.
            fd, safety_name = tempfile.mkstemp(
                prefix=f"{path.name}.pre-restore.{digest}.", suffix=".bak", dir=path.parent
            )
            safety = Path(safety_name)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
            fd, temporary = tempfile.mkstemp(prefix=".huesync-restore-", dir=path.parent)
            with os.fdopen(fd, "wb") as stream:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Make the safety copy durable before the commit point.
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if path.read_bytes() != previous:
                raise BackupError(
                    "Configuration changed during restore; retry after stopping writers"
                )
            os.replace(temporary, path)  # final fallible action / atomic commit point
            temporary = None
            return safety
    except OSError:
        raise BackupError(
            "Restore could not commit; current configuration was not replaced"
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


@contextmanager
def configuration_lease(path: Path):
    """Cross-process exclusion: runtime holds this lease; offline restore must acquire it.

    Never unlink a lock file: all participants must lock the same inode.
    """
    fd = os.open(
        path.with_name(path.name + ".runtime.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Configuration is in use; stop HueSync before CLI restore") from None
        # A root CLI must not leave a lock that the runtime config owner cannot open.
        metadata = path.stat()
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, 0o600)
        yield
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sensitive current-schema configuration backup")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("HUESYNC_CONFIG", "/etc/huesync/config.json")),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("export").add_argument("file", type=Path)
    restore = sub.add_parser("import")
    restore.add_argument("file", type=Path)
    restore.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "export":
            if not args.config.is_file():
                raise BackupError("Current configuration file does not exist")
            export_file(Storage(args.config), args.file)
            print("Sensitive configuration backup exported (0600). Keep it secure.")
        else:
            with args.file.open("rb") as stream:
                backup = decode_backup(stream.read(MAX_BACKUP_BYTES + 1))
            if args.check:
                print("Backup format, schema and references valid. Nothing changed.")
                return
            with configuration_lease(args.config):
                restore_configuration(args.config, backup)
            print("Restored; safety backup retained. Start HueSync, then activate a Coupling.")
    except BackupError as exc:
        parser.exit(1, f"Backup operation failed: {exc}\n")
    except (OSError, ValueError, TypeError):
        # No filenames, secret-bearing schema values or exception repr in diagnostics.
        parser.exit(
            1, "Backup operation failed: invalid input, inaccessible files, or runtime in use.\n"
        )


if __name__ == "__main__":
    main()
