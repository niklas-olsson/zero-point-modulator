"""Frozen public execution-contract syntax, not native policy qualification.

This is the restricted encoding adopted in OpenJules #47, comment 6848006932.
It neither reconstructs a private runtime policy nor attests its implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .models import ConfigurationError, identifier, repository
from .registry import destination_binding

MAX_APPROVAL_BYTES = 131_072
MAX_MANIFEST_BYTES = 65_536
MAX_INTEGER = 2**53 - 1
CONTRACT_VERSION = 1
BASE_PROTOCOLS = frozenset({
    "worker-lifecycle-v2", "destination-identity-v1", "execution-contract-v1",
    "local-admission-v1", "never-admitted-v1",
})


def _object(value: Any, fields: str, label: str) -> dict:
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ConfigurationError(f"{label} has missing or unsupported fields")
    return value


def _string(value: Any, label: str) -> str:
    if type(value) is not str or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ConfigurationError(f"{label} must be a Unicode scalar string")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigurationError(f"{label} must be an exact integer in {minimum}..{maximum}")
    return value


def _fixed(value: Any, expected: Any, label: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise ConfigurationError(f"{label} has an unsupported value")


def _strings(value: Any, label: str, *, declared_set: bool = False) -> list[str]:
    if type(value) is not list:
        raise ConfigurationError(f"{label} must be an array of strings")
    for item in value:
        _string(item, label)
    if declared_set and value != sorted(set(value)):
        raise ConfigurationError(f"{label} must already be a sorted unique declared set")
    return value


def _canonical_repository(value: Any) -> str:
    # Registry configuration accepts aliases. An approved document never does.
    if repository(value) != value:
        raise ConfigurationError("approval repository must already be canonical")
    return value


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ConfigurationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def manifest_bytes(value: Any) -> bytes:
    """Validate the closed public shape and encode without rewriting its values.

    Native resource, image, executable, model and authority qualification remain
    producer responsibilities. Only explicit public syntax/encoding constraints
    are checked here; successful encoding does not mean a policy is runnable.
    """
    item = _object(value, "manifest_version source image execution resources confinement model children required_protocols", "manifest")
    _fixed(item["manifest_version"], 1, "manifest_version")
    source = _object(item["source"], "repository base_commit", "source")
    _canonical_repository(source["repository"])
    if type(source["base_commit"]) is not str or not re.fullmatch(r"[0-9a-f]{40}", source["base_commit"]):
        raise ConfigurationError("source base_commit must be an exact lowercase commit")
    image = _object(item["image"], "config_digest os architecture variant", "image")
    _digest(image["config_digest"], "image config_digest")
    for name in ("os", "architecture"):
        if not _string(image[name], f"image {name}"):
            raise ConfigurationError(f"image {name} must be nonempty")
    if image["variant"] is not None:
        _string(image["variant"], "image variant")
    execution = _object(item["execution"], "contract_id root_argv root_uid guest_environment_refs", "execution")
    _fixed(execution["contract_id"], "openjules-managed-docker-v1", "execution contract_id")
    _strings(execution["root_argv"], "root_argv")
    _integer(execution["root_uid"], "root_uid")
    _fixed(execution["guest_environment_refs"], "none", "guest_environment_refs")
    resources = _object(item["resources"], "memory_bytes swap_limit_bytes cpu_period_us cpu_quota_us pids_limit root_timeout_ms max_archive_bytes", "resources")
    for name, number in resources.items():
        _integer(number, f"resources {name}")
    duration = _integer(resources["root_timeout_ms"], "root_timeout_ms", minimum=1000, maximum=86_400_000)
    if duration % 1000:
        raise ConfigurationError("root_timeout_ms must be divisible by 1000")
    confinement = _object(item["confinement"], "network_mode read_only_rootfs privileged cap_drop no_new_privileges source_mount work_mount broker_mount tmpfs_contract watchdog_uid watchdog_contract restart_policy", "confinement")
    for name, expected in {
        "network_mode": "none", "read_only_rootfs": True, "privileged": False,
        "no_new_privileges": True, "source_mount": "approved-snapshot-ro",
        "work_mount": "owned-volume-rw", "tmpfs_contract": "openjules-tmpfs-v1",
        "watchdog_uid": 65534, "restart_policy": "no",
    }.items():
        _fixed(confinement[name], expected, f"confinement {name}")
    if _strings(confinement["cap_drop"], "cap_drop", declared_set=True) != ["ALL"]:
        raise ConfigurationError("cap_drop must be exactly ALL")
    for name, allowed in (("broker_mount", {"absent", "owned-broker-ro"}),
                          ("watchdog_contract", {"root-reaping-v1", "children-reaping-v1"})):
        if _string(confinement[name], name) not in allowed:
            raise ConfigurationError(f"confinement {name} has an unsupported value")
    model = item["model"]
    if model is not None:
        _object(model, "service_policy_id model transport_contract token_parameter max_calls max_output_tokens max_total_output_tokens request_timeout_ms max_request_bytes max_response_bytes", "model")
        identifier(model["service_policy_id"], "service_policy_id")
        _string(model["model"], "model name")
        _fixed(model["transport_contract"], "host-broker-chat-completions-v1", "model transport_contract")
        if _string(model["token_parameter"], "token_parameter") not in {"max_tokens", "max_completion_tokens"}:
            raise ConfigurationError("model token_parameter has an unsupported value")
        for name in ("max_calls", "max_output_tokens", "max_total_output_tokens", "max_request_bytes", "max_response_bytes"):
            _integer(model[name], f"model {name}")
        _integer(model["request_timeout_ms"], "request_timeout_ms", minimum=1, maximum=120_000)
    protocols = set(BASE_PROTOCOLS)
    children = item["children"]
    if children is not None:
        _object(children, "protocol max_children max_child_ms max_child_turns capabilities uid_start child_argv verify_argv max_result_bytes outcome_protocol session_protocol public_discussion", "children")
        _fixed(children["protocol"], "bounded-children-v1", "children protocol")
        protocols.add(children["protocol"])
        for name in ("max_children", "max_child_turns", "uid_start", "max_result_bytes"):
            _integer(children[name], f"children {name}")
        _integer(children["max_child_ms"], "max_child_ms", minimum=1, maximum=duration)
        _strings(children["capabilities"], "child capabilities", declared_set=True)
        for name in ("child_argv", "verify_argv"):
            _strings(children[name], name)
        for name, supported in (("outcome_protocol", "initial-v1"), ("session_protocol", "cold-clarification-v1")):
            if children[name] is not None:
                _fixed(children[name], supported, name)
                protocols.add(children[name])
        _fixed(children["public_discussion"], None, "children public_discussion")
    required = _strings(item["required_protocols"], "required_protocols", declared_set=True)
    if set(required) != protocols:
        raise ConfigurationError("required_protocols must exactly match the declared public protocol set")
    # Fixed ASCII keys make sort_keys equivalent to the adopted restricted
    # ordering. Scalar values are not normalized; argv order is significant.
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ConfigurationError("canonical manifest exceeds 65536 bytes")
    return encoded


@dataclass(frozen=True)
class Approval:
    envelope: dict[str, Any]
    manifest: bytes
    raw: bytes


def parse_approval(raw: bytes) -> Approval:
    """Pure bounded public-artifact parser. No journal, credential or network I/O."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_APPROVAL_BYTES:
        raise ConfigurationError("approval must contain 1..131072 UTF-8 bytes")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ConfigurationError("approval JSON contains duplicate fields")
            result[key] = value
        return result

    def not_number(_):
        raise ConfigurationError("approval JSON forbids floating point and nonfinite numbers")

    try:
        item = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=not_number, parse_constant=not_number)
    except (UnicodeError, ValueError, RecursionError):
        raise ConfigurationError("approval JSON is invalid or contains unsupported values") from None
    _object(item, "approval_version deployment_id repository profile destination_binding manifest execution_manifest_sha256", "approval")
    _fixed(item["approval_version"], 1, "approval_version")
    identifier(item["deployment_id"], "deployment_id")
    _canonical_repository(item["repository"])
    identifier(item["profile"], "profile")
    destination_binding(item["destination_binding"])
    encoded = manifest_bytes(item["manifest"])
    if item["manifest"]["source"]["repository"] != item["repository"]:
        raise ConfigurationError("manifest source repository does not match the approval scope")
    if _digest(item["execution_manifest_sha256"], "execution_manifest_sha256") != sha256(encoded):
        raise ConfigurationError("execution_manifest_sha256 does not match the exact canonical manifest")
    return Approval(item, encoded, raw)
