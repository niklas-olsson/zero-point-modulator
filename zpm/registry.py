"""Disabled, local destination enrollment. This module performs no remote I/O."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .models import ConfigurationError, ProviderConfig, count, identifier, repository


OFFLINE_REASON = "Destination execution is not supported yet; destinations remain disabled."
REGISTRY_COLUMNS = {
    "registry_revisions": ("revision", "policy"),
    "destinations": ("destination_id", "revision", "specification"),
    "destination_profiles": ("destination_id", "repository", "profile", "revision", "specification"),
}


def decode_json(raw: str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ConfigurationError("registry JSON contains a duplicate field")
            value[key] = item
        return value

    def constant(value):
        raise ConfigurationError("registry JSON contains a nonfinite number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ConfigurationError(f"{label} must contain exactly the supported fields")
    return value


def _destination_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ConfigurationError("destination_id must be an approved destination identifier")
    return value


def _opaque_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ConfigurationError(f"{label} must contain 32 lowercase hexadecimal characters")
    return value


def destination_binding(value: Any) -> dict[str, Any]:
    """Validate the unchanged four-field public destination identity."""
    item = _object(value, {"destination_id", "journal_incarnation", "policy_binding_id", "policy_binding_version"}, "destination binding")
    _destination_id(item["destination_id"])
    _opaque_id(item["journal_incarnation"], "journal_incarnation")
    _opaque_id(item["policy_binding_id"], "policy_binding_id")
    if type(item["policy_binding_version"]) is not int or item["policy_binding_version"] != 1:
        raise ConfigurationError("policy_binding_version must be 1")
    return item


def _policy(value: Any) -> dict[str, Any]:
    value = _object(value, {"schema_version", "max_workers", "timeout_seconds"}, "registry policy")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ConfigurationError("registry schema_version must be 2")
    timeout = value["timeout_seconds"]
    if type(timeout) not in {int, float} or not 0 < timeout <= 120:
        raise ConfigurationError("timeout_seconds must be greater than zero and at most 120")
    return {"schema_version": 2, "max_workers": count(value["max_workers"], "max_workers"),
            "timeout_seconds": float(timeout)}


def _destination(value: Any, timeout: float, *, active: bool = False) -> dict[str, Any]:
    value = _object(value, {"destination_id", "journal_incarnation", "endpoint", "token_ref",
                            "max_workers", "enabled"}, "destination")
    if type(value["enabled"]) is not bool or (not active and value["enabled"] is not False):
        raise ConfigurationError("offline destinations must explicitly set enabled to false")
    # Reuse the single credential/transport validation boundary without resolving
    # the environment reference or constructing a provider.
    transport = ProviderConfig.parse({
        "schema_version": 1, "endpoint": value["endpoint"], "token_ref": value["token_ref"],
        "max_workers": value["max_workers"], "timeout_seconds": timeout,
    })
    return {
        "destination_id": _destination_id(value["destination_id"]),
        "journal_incarnation": _opaque_id(value["journal_incarnation"], "journal_incarnation"),
        "endpoint": transport.endpoint, "token_ref": transport.token_ref,
        "max_workers": transport.max_workers, "enabled": value["enabled"],
    }


def _profile(value: Any) -> dict[str, Any]:
    value = _object(value, {"destination_id", "repository", "profile", "policy_binding_id",
                            "policy_binding_version"}, "destination profile")
    if type(value["policy_binding_version"]) is not int or value["policy_binding_version"] != 1:
        raise ConfigurationError("policy_binding_version must be 1")
    return {
        "destination_id": _destination_id(value["destination_id"]),
        "repository": repository(value["repository"]),
        "profile": identifier(value["profile"], "profile"),
        "policy_binding_id": _opaque_id(value["policy_binding_id"], "policy_binding_id"),
        "policy_binding_version": 1,
    }


def configuration(value: Any, *, active: bool = False) -> dict[str, Any]:
    value = _object(value, {"schema_version", "max_workers", "timeout_seconds", "destinations", "profiles"},
                    "registry configuration")
    policy = _policy({key: value[key] for key in ("schema_version", "max_workers", "timeout_seconds")})
    if not isinstance(value["destinations"], list) or not isinstance(value["profiles"], list):
        raise ConfigurationError("destinations and profiles must be lists")
    destinations = [_destination(item, policy["timeout_seconds"], active=active) for item in value["destinations"]]
    profiles = [_profile(item) for item in value["profiles"]]
    ids = [item["destination_id"] for item in destinations]
    keys = [(item["destination_id"], item["repository"], item["profile"]) for item in profiles]
    policy_keys = [(item["destination_id"], item["policy_binding_id"]) for item in profiles]
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)) or len(policy_keys) != len(set(policy_keys)):
        raise ConfigurationError("registry configuration contains duplicate enrollment identities")
    if any(item["destination_id"] not in ids for item in profiles):
        raise ConfigurationError("each profile requires its destination in the same explicit import")
    return {**policy, "destinations": sorted(destinations, key=lambda item: item["destination_id"]),
            "profiles": sorted(profiles, key=lambda item: (item["destination_id"], item["repository"], item["profile"]))}


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def create_tables(connection: sqlite3.Connection) -> None:
    # Individual statements retain the caller's transaction; executescript
    # would implicitly commit the journal-format transition.
    connection.execute("CREATE TABLE registry_revisions (revision INTEGER PRIMARY KEY, policy TEXT NOT NULL)")
    connection.execute("""CREATE TABLE destinations (
        destination_id TEXT NOT NULL, revision INTEGER NOT NULL, specification TEXT NOT NULL,
        PRIMARY KEY(destination_id, revision))""")
    connection.execute("""CREATE TABLE destination_profiles (
        destination_id TEXT NOT NULL, repository TEXT NOT NULL, profile TEXT NOT NULL,
        revision INTEGER NOT NULL, specification TEXT NOT NULL,
        PRIMARY KEY(destination_id, repository, profile, revision))""")


def _latest(connection: sqlite3.Connection, table: str, fields: tuple[str, ...]) -> list[sqlite3.Row]:
    grouping = ", ".join(fields)
    matching = " AND ".join(f"current.{field}=latest.{field}" for field in fields)
    ordering = ", ".join(f"current.{field}" for field in fields)
    # Table and column names are internal constants, never configuration input.
    return list(connection.execute(f"""SELECT current.* FROM {table} current JOIN
        (SELECT {grouping}, MAX(revision) revision FROM {table} GROUP BY {grouping}) latest
        ON {matching} AND current.revision=latest.revision ORDER BY {ordering}"""))


def import_configuration(connection: sqlite3.Connection, data: dict[str, Any], *, active: bool = False) -> None:
    data = configuration(data, active=active)
    policy = {key: data[key] for key in ("schema_version", "max_workers", "timeout_seconds")}
    current_policy = connection.execute("SELECT * FROM registry_revisions ORDER BY revision DESC LIMIT 1").fetchone()
    destinations = {row["destination_id"]: decode_json(row["specification"])
                    for row in _latest(connection, "destinations", ("destination_id",))}
    profiles = {(row["destination_id"], row["repository"], row["profile"]): decode_json(row["specification"])
                for row in _latest(connection, "destination_profiles", ("destination_id", "repository", "profile"))}
    changed_destinations = []
    for item in data["destinations"]:
        previous = destinations.get(item["destination_id"])
        if previous and previous["journal_incarnation"] != item["journal_incarnation"]:
            raise ConfigurationError("an enrolled destination cannot change journal incarnation")
        if previous != item:
            changed_destinations.append(item)
    changed_profiles = []
    for item in data["profiles"]:
        key = (item["destination_id"], item["repository"], item["profile"])
        for row in connection.execute("SELECT specification FROM destination_profiles WHERE destination_id=?", (item["destination_id"],)):
            old = decode_json(row["specification"])
            if old["policy_binding_id"] == item["policy_binding_id"] and old != item:
                raise ConfigurationError("a retained policy binding cannot change its profile identity")
        if profiles.get(key) != item:
            changed_profiles.append(item)
    if current_policy and decode_json(current_policy["policy"]) == policy and not changed_destinations and not changed_profiles:
        return
    revision = 1 if current_policy is None else current_policy["revision"] + 1
    connection.execute("INSERT INTO registry_revisions VALUES (?, ?)", (revision, _encode(policy)))
    for item in changed_destinations:
        connection.execute("INSERT INTO destinations VALUES (?, ?, ?)", (item["destination_id"], revision, _encode(item)))
    for item in changed_profiles:
        connection.execute("INSERT INTO destination_profiles VALUES (?, ?, ?, ?, ?)",
                           (item["destination_id"], item["repository"], item["profile"], revision, _encode(item)))


def validate_records(connection: sqlite3.Connection, *, active: bool = False) -> None:
    policies = list(connection.execute("SELECT * FROM registry_revisions ORDER BY revision"))
    if not policies or any(type(row["revision"]) is not int or row["revision"] < 1 for row in policies):
        raise ConfigurationError("destination registry has missing or invalid policy history")
    revisions = {row["revision"] for row in policies}
    for row in policies:
        _policy(decode_json(row["policy"]))
    identities: dict[str, str] = {}
    for row in connection.execute("SELECT * FROM destinations ORDER BY revision"):
        item = _destination(decode_json(row["specification"]), 10.0, active=active)
        if row["revision"] not in revisions or item["destination_id"] != row["destination_id"]:
            raise ConfigurationError("destination registry has invalid destination history")
        previous = identities.setdefault(item["destination_id"], item["journal_incarnation"])
        if previous != item["journal_incarnation"]:
            raise ConfigurationError("destination registry changed an immutable incarnation")
    bindings: dict[tuple[str, str], dict[str, Any]] = {}
    for row in connection.execute("SELECT * FROM destination_profiles ORDER BY revision"):
        item = _profile(decode_json(row["specification"]))
        if (row["revision"] not in revisions or item["destination_id"] not in identities
                or any(item[field] != row[field] for field in ("destination_id", "repository", "profile"))):
            raise ConfigurationError("destination registry has invalid profile history")
        key = (item["destination_id"], item["policy_binding_id"])
        previous = bindings.setdefault(key, item)
        if previous != item:
            raise ConfigurationError("destination registry changed an immutable policy binding")


def status(connection: sqlite3.Connection, deployment_id: str, *, active: bool = False) -> dict[str, Any]:
    validate_records(connection, active=active)
    current = connection.execute("SELECT * FROM registry_revisions ORDER BY revision DESC LIMIT 1").fetchone()
    profiles = _latest(connection, "destination_profiles", ("destination_id", "repository", "profile"))
    destinations = []
    for row in _latest(connection, "destinations", ("destination_id",)):
        item = decode_json(row["specification"])
        destinations.append({
            **{field: item[field] for field in ("destination_id", "journal_incarnation", "max_workers", "enabled")},
            "revision": row["revision"], "ready": False,
            "profiles": [decode_json(profile["specification"]) | {"revision": profile["revision"]}
                         for profile in profiles if profile["destination_id"] == item["destination_id"]],
        })
    return {
        **decode_json(current["policy"]), "deployment_id": deployment_id,
        "registry_revision": current["revision"], "execution_enabled": active,
        "ready": False, "reason": "Readiness requires explicit current destination checks." if active else OFFLINE_REASON, "destinations": destinations,
        "legacy_history_rows": connection.execute("SELECT COUNT(*) FROM workers").fetchone()[0],
        "remote_access_checked": False,
    }
