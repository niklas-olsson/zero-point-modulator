"""Closed destination execution responses and existing extensible capabilities.

The adopted capability fields retain v1 extension handling; S/F snapshots,
execution contracts, discovery, admission and refusals have exact field sets.
No native policy or task authority is introduced here.
"""

from __future__ import annotations

import json
from typing import Any

from .models import ConfigurationError, capability_observation, snapshot
from .portable_manifest import MAX_INTEGER, manifest_bytes, sha256
from .registry import destination_binding


QUALIFICATION = {"contract_version", "execution_manifest_sha256", "admission_revision"}
SNAPSHOT = {"schema_version", "deployment_id", "repository", "slot_id", "generation",
            "launch_key", "profile", "destination_binding", "worker_id", "state", "reason",
            "termination_confirmed", "artifacts_captured", "admission_open", "active_executions"}


def exact_object(value: Any, fields: set[str]) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ConfigurationError("destination response has unsupported fields")
    return value


def integer(value: Any, minimum: int = 0, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigurationError("destination response has an invalid integer")
    return value


def qualification(value: Any) -> dict:
    import re

    exact_object(value, QUALIFICATION)
    integer(value["contract_version"], 1, 1)
    integer(value["admission_revision"], 1)
    if type(value["execution_manifest_sha256"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["execution_manifest_sha256"]):
        raise ConfigurationError("destination response has an invalid manifest digest")
    return dict(value)


def admission(value: Any) -> dict:
    exact_object(value, {"version", "revision", "new_effects_open", "worker_ceiling", "unreleased_workers"})
    integer(value["version"], 1, 1)
    integer(value["revision"], 1)
    integer(value["worker_ceiling"])
    integer(value["unreleased_workers"])
    if type(value["new_effects_open"]) is not bool:
        raise ConfigurationError("destination admission requires an exact boolean")
    return dict(value)


def worker_snapshot(data: Any, *, expected: dict | None = None,
                    binding: dict | None = None, original: dict | None = None,
                    allow_identity: bool = False) -> dict:
    if type(data) is not dict:
        raise ConfigurationError("destination worker response must be an object")
    full = bool(set(data) & (QUALIFICATION | {"disposition"}))
    exact_object(data, SNAPSHOT | (QUALIFICATION | {"disposition"} if full else set()))
    integer(data["schema_version"], 2, 2)
    retained = destination_binding(data["destination_binding"])
    if binding is not None and retained != binding:
        raise ConfigurationError("destination worker changed its original binding")
    if not full and (original is not None or not allow_identity):
        raise ConfigurationError("full destination worker qualification is missing")
    result = snapshot({**data, "schema_version": 1})
    result.update(schema_version=2, destination_binding=dict(retained))
    if expected is not None and any(data.get(key) != value for key, value in expected.items()):
        raise ConfigurationError("destination worker changed its original specification")
    if full:
        selected = qualification({key: data[key] for key in QUALIFICATION})
        if original is not None and selected != original:
            raise ConfigurationError("destination worker changed its original qualification")
        disposition = data["disposition"]
        if disposition is not None:
            exact_object(disposition, {"version", "kind", "reason", "observed_admission_revision"})
            integer(disposition["version"], 1, 1)
            integer(disposition["observed_admission_revision"], 1)
            if (disposition["kind"] != "never_admitted"
                    or type(disposition["reason"]) is not str
                    or disposition["reason"] not in {"closed", "ceiling", "stale_admission_revision"}
                    or (disposition["reason"] == "stale_admission_revision")
                    != (disposition["observed_admission_revision"] != selected["admission_revision"])
                    or data["state"] != "stopped" or data["reason"] != "never_admitted"
                    or data["termination_confirmed"] is not True or data["artifacts_captured"] is not True
                    or data["admission_open"] is not False or data["active_executions"] != []):
                raise ConfigurationError("destination never-admitted receipt is inconsistent")
        elif data["reason"] == "never_admitted":
            raise ConfigurationError("destination refusal lacks its permanent receipt")
        result.update(selected, disposition=None if disposition is None else dict(disposition))
    return result


def contract_response(data: Any, *, deployment_id: str, repository: str, profile: str,
                      binding: dict, approved: dict) -> dict:
    exact_object(data, {"schema_version", "contract_version", "deployment_id", "repository", "profile",
                        "destination_binding", "manifest", "execution_manifest_sha256", "admission"})
    integer(data["schema_version"], 2, 2)
    integer(data["contract_version"], 1, 1)
    if any(data[name] != value for name, value in {
        "deployment_id": deployment_id, "repository": repository, "profile": profile,
    }.items()) or destination_binding(data["destination_binding"]) != binding:
        raise ConfigurationError("execution contract changed its enrolled scope or binding")
    encoded = manifest_bytes(data["manifest"])
    expected = manifest_bytes(approved["manifest"])
    if (encoded != expected or data["execution_manifest_sha256"] != sha256(encoded)
            or data["execution_manifest_sha256"] != approved["execution_manifest_sha256"]):
        raise ConfigurationError("execution contract does not match the original public approval")
    return admission(data["admission"])


def capabilities(data: Any, *, deployment_id: str, repository: str, profile: str, binding: dict) -> tuple[bool, str]:
    if type(data) is not dict:
        raise ConfigurationError("destination capabilities must be an object")
    integer(data.get("schema_version"), 2, 2)
    if destination_binding(data.get("destination_binding")) != binding:
        raise ConfigurationError("destination capabilities changed the enrolled binding")
    for name in ("identity_binding", "portable_manifest", "local_admission"):
        integer(data.get(name), 1, 1)
    ready, reason, _ = capability_observation({**data, "schema_version": 1}, {
        "deployment_id": deployment_id, "repository": repository, "profile": profile,
    })
    return ready, reason


def discovery(data: Any, *, binding: dict, original: dict | None = None,
              expected: dict | None = None) -> list[dict]:
    fields = {"schema_version", "contract_version", "destination_binding", "workers"}
    exact_object(data, fields | (QUALIFICATION if original is not None else set()))
    integer(data["schema_version"], 2, 2)
    integer(data["contract_version"], 1, 1)
    if destination_binding(data["destination_binding"]) != binding:
        raise ConfigurationError("destination discovery changed the original binding")
    if original is not None and qualification({key: data[key] for key in QUALIFICATION}) != original:
        raise ConfigurationError("destination discovery changed the original qualification")
    if type(data["workers"]) is not list:
        raise ConfigurationError("destination discovery requires a worker list")
    return [worker_snapshot(item, binding=binding, original=original, expected=expected,
                            allow_identity=original is None) for item in data["workers"]]


def decode_response(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate response field")
            result[key] = value
        return result

    def invalid_number(_):
        raise ValueError("invalid response number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_number)
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigurationError("destination returned an invalid JSON response") from None
