"""Bounded, append-only offline approval records in the disabled v2 registry."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

from .models import ConfigurationError
from .portable_manifest import Approval, CONTRACT_VERSION, MAX_APPROVAL_BYTES, parse_approval, sha256
from .registry import decode_json

FEATURE_KEY = "offline_approval_schema"
FEATURE_VERSION = "1"
MAX_APPROVALS = 256
MAX_RETAINED_BYTES = 16 * 1024 * 1024
APPROVAL_COLUMNS = {
    "destination_approvals": (
        "approval_id", "destination_id", "policy_binding_id", "registry_revision",
        "profile_revision", "envelope", "manifest", "provenance",
    ),
}


def enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT value_v2 FROM metadata WHERE key=?", (FEATURE_KEY,)).fetchone()
    if row is None:
        return False
    if row[0] != FEATURE_VERSION:
        raise ConfigurationError("unsupported offline approval journal extension")
    return True


def read_private_file(path: str | Path) -> Approval:
    """Read one owned regular 0600 file, without following its final symlink."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= MAX_APPROVAL_BYTES):
            raise ConfigurationError("approval requires an owned regular nonsymlink 0600 file of 1..131072 bytes")
        chunks = []
        remaining = MAX_APPROVAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16_384))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        fields = ("st_size", "st_mode", "st_uid", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in fields) or len(raw) != before.st_size:
            raise ConfigurationError("approval file changed during the bounded read")
    except OSError:
        raise ConfigurationError("cannot read an owned private nonsymlink approval file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return parse_approval(raw)


def _create(connection: sqlite3.Connection) -> None:
    # No open-time repair or implicit migration. Both objects are created only
    # in the first successful explicit import's existing BEGIN IMMEDIATE.
    connection.execute("""CREATE TABLE destination_approvals (
        approval_id INTEGER PRIMARY KEY,
        destination_id TEXT NOT NULL,
        policy_binding_id TEXT NOT NULL,
        registry_revision INTEGER NOT NULL,
        profile_revision INTEGER NOT NULL,
        envelope BLOB NOT NULL,
        manifest BLOB NOT NULL,
        provenance TEXT NOT NULL,
        UNIQUE(destination_id, policy_binding_id)
    )""")
    connection.execute("INSERT INTO metadata VALUES (?, ?)", (FEATURE_KEY, FEATURE_VERSION))


def _size(connection: sqlite3.Connection) -> tuple[int, int]:
    row = connection.execute("SELECT count(*), coalesce(sum(length(envelope)+length(manifest)),0) FROM destination_approvals").fetchone()
    return row[0], row[1]


def _scope_matches(item: dict, destination: dict, profile: dict, deployment_id: str) -> bool:
    return item["deployment_id"] == deployment_id and item["repository"] == profile["repository"] \
        and item["profile"] == profile["profile"] and item["destination_binding"] == {
            "destination_id": destination["destination_id"],
            "journal_incarnation": destination["journal_incarnation"],
            "policy_binding_id": profile["policy_binding_id"],
            "policy_binding_version": profile["policy_binding_version"],
        }


def validate_records(connection: sqlite3.Connection, deployment_id: str) -> None:
    """Fail closed on missing, malformed, changed or over-limit retained data."""
    if not enabled(connection):
        return
    count, byte_count = _size(connection)
    if not 1 <= count <= MAX_APPROVALS or byte_count > MAX_RETAINED_BYTES:
        raise ConfigurationError("offline approval history exceeds its count or byte bound")
    if connection.execute("""SELECT 1 FROM destination_approvals WHERE
            typeof(envelope) != 'blob' OR typeof(manifest) != 'blob'
            OR typeof(provenance) != 'text' OR length(CAST(provenance AS BLOB)) > 1024
            OR typeof(registry_revision) != 'integer' OR typeof(profile_revision) != 'integer'
            LIMIT 1""").fetchone():
        raise ConfigurationError("offline approval history has invalid stored types")
    for sequence, row in enumerate(connection.execute("SELECT * FROM destination_approvals ORDER BY approval_id"), start=1):
        approval = parse_approval(row["envelope"])
        item = approval.envelope
        binding = item["destination_binding"]
        if (row["approval_id"] != sequence or row["destination_id"] != binding["destination_id"]
                or row["policy_binding_id"] != binding["policy_binding_id"]
                or row["manifest"] != approval.manifest):
            raise ConfigurationError("offline approval history changed its immutable identity or manifest")
        provenance = decode_json(row["provenance"])
        if provenance != {
            "import_method": "owned-private-file/v1",
            "input_sha256": sha256(approval.raw), "input_bytes": len(approval.raw),
        } or type(provenance.get("input_bytes")) is not int:
            raise ConfigurationError("offline approval provenance does not match the retained input")
        revision = row["registry_revision"]
        profile_row = connection.execute("""SELECT specification FROM destination_profiles
            WHERE destination_id=? AND repository=? AND profile=? AND revision=?""", (
                binding["destination_id"], item["repository"], item["profile"], row["profile_revision"],
            )).fetchone()
        destination_row = connection.execute("""SELECT specification FROM destinations
            WHERE destination_id=? AND revision<=? ORDER BY revision DESC LIMIT 1""", (
                binding["destination_id"], revision,
            )).fetchone()
        if (not 1 <= row["profile_revision"] <= revision or profile_row is None or destination_row is None
                or connection.execute("SELECT 1 FROM registry_revisions WHERE revision=?", (revision,)).fetchone() is None
                or not _scope_matches(item, decode_json(destination_row[0]), decode_json(profile_row[0]), deployment_id)):
            raise ConfigurationError("offline approval is not bound to its original enrolled scope")


def import_record(connection: sqlite3.Connection, approval: Approval, deployment_id: str) -> None:
    """Called under the Store lock and transaction after format validation."""
    item = approval.envelope
    binding = item["destination_binding"]
    has_extension = enabled(connection)
    if has_extension:
        previous = connection.execute("SELECT envelope, manifest FROM destination_approvals WHERE destination_id=? AND policy_binding_id=?", (
            binding["destination_id"], binding["policy_binding_id"],
        )).fetchone()
        if previous is not None:
            if parse_approval(previous[0]).envelope != item or previous[1] != approval.manifest:
                raise ConfigurationError("changed reapproval cannot replace an immutable destination binding")
            return  # Keep the original exact artifact and provenance, with no write.
    destination = connection.execute("SELECT specification FROM destinations WHERE destination_id=? ORDER BY revision DESC LIMIT 1", (
        binding["destination_id"],
    )).fetchone()
    profile = connection.execute("SELECT revision, specification FROM destination_profiles WHERE destination_id=? AND repository=? AND profile=? ORDER BY revision DESC LIMIT 1", (
        binding["destination_id"], item["repository"], item["profile"],
    )).fetchone()
    if destination is None or profile is None or not _scope_matches(item, decode_json(destination[0]), decode_json(profile[1]), deployment_id):
        raise ConfigurationError("approval scope and binding must exactly match the current enrollment")
    count, byte_count = _size(connection) if has_extension else (0, 0)
    if count >= MAX_APPROVALS or byte_count + len(approval.raw) + len(approval.manifest) > MAX_RETAINED_BYTES:
        raise ConfigurationError("offline approval import would exceed the retained count or byte bound")
    revision = connection.execute("SELECT max(revision) FROM registry_revisions").fetchone()[0]
    if not has_extension:
        _create(connection)
    provenance = {"import_method": "owned-private-file/v1", "input_sha256": sha256(approval.raw), "input_bytes": len(approval.raw)}
    connection.execute("INSERT INTO destination_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
        count + 1, binding["destination_id"], binding["policy_binding_id"], revision, profile[0],
        approval.raw, approval.manifest, json.dumps(provenance, sort_keys=True, separators=(",", ":")),
    ))
    validate_records(connection, deployment_id)


def _summary(row: sqlite3.Row, item: dict) -> dict[str, Any]:
    return {
        "approval_id": row["approval_id"], "execution_contract_version": CONTRACT_VERSION,
        "repository": item["repository"], "profile": item["profile"],
        "destination_binding": item["destination_binding"],
        "execution_manifest_sha256": item["execution_manifest_sha256"],
        "registry_revision_at_import": row["registry_revision"], "profile_revision": row["profile_revision"],
        "provenance": decode_json(row["provenance"]),
    }


def annotate_status(connection: sqlite3.Connection, report: dict[str, Any]) -> dict[str, Any]:
    current = {}
    for destination in report["destinations"]:
        for profile in destination["profiles"]:
            profile["approval_status"] = "enrolled-only"
            profile["execution_manifest_sha256"] = None
            current[(destination["destination_id"], profile["policy_binding_id"])] = profile
    report["approval_schema_version"] = 1 if enabled(connection) else None
    report["native_policy_checked"] = False
    report["approvals"] = []
    report["approval_count"], report["approval_storage_bytes"] = (0, 0)
    if report["approval_schema_version"] is not None:
        report["approval_count"], report["approval_storage_bytes"] = _size(connection)
        for row in connection.execute("SELECT * FROM destination_approvals ORDER BY approval_id"):
            item = parse_approval(row["envelope"]).envelope
            summary = _summary(row, item)
            profile = current.get((row["destination_id"], row["policy_binding_id"]))
            summary["matches_current_enrollment"] = profile is not None
            report["approvals"].append(summary)
            if profile is not None:
                profile["approval_status"] = "approved-public-contract"
                profile["execution_manifest_sha256"] = item["execution_manifest_sha256"]
    return report


def inspect_record(connection: sqlite3.Connection, destination_id: str, policy_binding_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM destination_approvals WHERE destination_id=? AND policy_binding_id=?", (
        destination_id, policy_binding_id,
    )).fetchone() if enabled(connection) else None
    if row is None:
        raise ConfigurationError("no retained approval matches that destination binding")
    item = parse_approval(row["envelope"]).envelope
    return _summary(row, item) | {
        "approval": item, "execution_enabled": False, "ready": False,
        "remote_access_checked": False, "native_policy_checked": False,
    }
