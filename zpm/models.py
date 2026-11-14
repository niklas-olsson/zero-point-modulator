"""Small versioned configuration and runtime contracts."""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def count(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ConfigurationError(f"{name} must be a nonnegative 64-bit integer")
    return value


def repository(value: Any) -> str:
    """Canonicalize a GitHub binding; other forges fail explicitly in v1."""
    if not isinstance(value, str):
        raise ConfigurationError("repository must be a string")
    value = value.strip()
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):].rstrip("/")
        if value.endswith(".git"):
            value = value[:-4]
    elif value.startswith("github:github.com/"):
        value = value[len("github:github.com/"):]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", value):
        raise ConfigurationError("repository must identify a GitHub owner/repository")
    if value.split("/")[1] in {".", ".."}:
        raise ConfigurationError("invalid repository name")
    return "github:github.com/" + value.lower()


def identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise ConfigurationError(f"{name} must be a nonempty identifier")
    return value


@dataclass(frozen=True)
class Binding:
    repository: str
    profile: str
    desired_workers: int
    enabled: bool = True
    paused: bool = False
    max_workers: int | None = None
    schema_version: int = 1

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "Binding":
        if not isinstance(data, dict):
            raise ConfigurationError("binding must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ConfigurationError("binding contains unsupported fields")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ConfigurationError("binding schema_version must be 1")
        for name in ("enabled", "paused"):
            if name in data and type(data[name]) is not bool:
                raise ConfigurationError(f"{name} must be boolean")
        limit = data.get("max_workers")
        return cls(
            repository(data.get("repository")),
            identifier(data.get("profile"), "profile"),
            count(data.get("desired_workers"), "desired_workers"),
            data.get("enabled", True),
            data.get("paused", False),
            None if limit is None else count(limit, "max_workers"),
        )

    @property
    def target(self) -> int:
        return self.desired_workers if self.enabled and not self.paused else 0

    @property
    def permitted_target(self) -> int:
        return min(self.target, self.max_workers) if self.max_workers is not None else self.target

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    token_ref: str
    max_workers: int
    timeout_seconds: float = 10.0
    schema_version: int = 1

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "ProviderConfig":
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ConfigurationError("provider configuration contains unsupported fields")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ConfigurationError("provider schema_version must be 1")
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str):
            raise ConfigurationError("provider endpoint must be a URL")
        try:
            if any(character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                   for character in endpoint):
                raise ValueError
            parsed = urlsplit(endpoint)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or "?" in endpoint or "#" in endpoint or "%" in parsed.netloc
                    or parsed.netloc.endswith(":")):
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            if "[" in parsed.netloc or "]" in parsed.netloc:
                if not re.fullmatch(r"\[[^\[\]]+\](?::[0-9]+)?", parsed.netloc):
                    raise ValueError
                ipaddress.IPv6Address(parsed.hostname)
            if parsed.scheme == "http":
                address = ipaddress.ip_address(parsed.hostname)
                if ("%" in parsed.hostname or not address.is_loopback
                        or (address.version == 6 and int(address) != 1)):
                    raise ValueError
        except ValueError:
            raise ConfigurationError("provider endpoint requires HTTPS or literal loopback HTTP, without credentials, query or fragment") from None
        ref = data.get("token_ref")
        if not isinstance(ref, str) or not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", ref):
            raise ConfigurationError("token_ref must name an environment variable, not contain a token")
        timeout = data.get("timeout_seconds", 10.0)
        if type(timeout) not in {float, int} or not 0 < timeout <= 120:
            raise ConfigurationError("timeout_seconds must be greater than zero and at most 120")
        return cls(endpoint.rstrip("/"), ref, count(data.get("max_workers"), "max_workers"), float(timeout))


STATES = {"starting", "ready", "busy", "draining", "stopped", "failed", "unknown"}
OPERATIONS = {"ensure", "discover", "observe", "drain", "stop"}
UNSUPPORTED_AUTHORITY_REASON = "Canonical managed authority is unsupported: child_initial_outcome=1"


def snapshot(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ConfigurationError("unsupported worker response schema")
    for field in ("deployment_id", "repository", "slot_id", "launch_key", "profile", "worker_id"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ConfigurationError(f"worker response lacks {field}")
    if type(data.get("generation")) is not int or data["generation"] < 1:
        raise ConfigurationError("worker response has invalid generation")
    if not isinstance(data.get("state"), str) or data["state"] not in STATES:
        raise ConfigurationError("worker response has unknown state")
    for field in ("termination_confirmed", "artifacts_captured", "admission_open"):
        if type(data.get(field)) is not bool:
            raise ConfigurationError(f"worker response lacks boolean {field}")
    active = data.get("active_executions")
    if not isinstance(active, list) or any(not isinstance(x, str) for x in active):
        raise ConfigurationError("worker response lacks execution references")
    if not isinstance(data.get("reason"), str):
        raise ConfigurationError("worker response lacks reason")
    if data["state"] == "stopped" and (not data["termination_confirmed"] or not data["artifacts_captured"] or active):
        raise ConfigurationError("worker claims stopped before termination and capture")
    if data["state"] in {"ready", "busy"} and data["termination_confirmed"]:
        raise ConfigurationError("terminated worker cannot be healthy capacity")
    if data["state"] in {"draining", "stopped", "failed"} and data["admission_open"]:
        raise ConfigurationError("draining or failed worker cannot admit new execution")
    fields = {"schema_version", "deployment_id", "repository", "slot_id", "generation", "launch_key", "profile", "worker_id", "state", "reason", "termination_confirmed", "artifacts_captured", "admission_open", "active_executions"}
    return {field: data[field] for field in fields}


def released(data: dict[str, Any]) -> bool:
    return data.get("state") in {"stopped", "failed"} and data.get("termination_confirmed") is True and data.get("artifacts_captured") is True and data.get("admission_open") is False and not data.get("active_executions")


def _capability_diagnostic(data: Any) -> dict[str, Any]:
    """Accept only the bounded producer-owned observation adopted in OJ #51."""
    invalid = "unsupported provider capability diagnostic"
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ConfigurationError(invalid) from None
    if len(encoded) > 1024 or not isinstance(data, dict) or set(data) != {
        "schema_version", "producer", "dimension", "code", "requirement",
    }:
        raise ConfigurationError(invalid)
    requirement = data["requirement"]
    if (type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["producer"] != "openjules" or data["dimension"] != "canonical_authority"
            or data["code"] != "required_capability_unsupported"
            or not isinstance(requirement, dict) or set(requirement) != {"name", "version"}
            or requirement["name"] != "child_initial_outcome"
            or type(requirement["version"]) is not int or requirement["version"] != 1):
        raise ConfigurationError(invalid)
    return {**data, "requirement": dict(requirement)}


def capability_observation(data: Any, expected_scope: dict[str, str] | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate capability facts before retaining a typed, scope-bound refusal.

    The human reason is never interpreted or copied. Omission supports legacy
    peers; any present invalid extension blocks even an otherwise positive reply.
    """
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        return False, "unsupported provider capability schema", None
    operations = data.get("operations")
    if (not isinstance(operations, list) or not all(isinstance(item, str) for item in operations)
            or not OPERATIONS.issubset(operations)):
        return False, "provider lacks required lifecycle operations", None
    if expected_scope is not None and any(data.get(key) != value for key, value in expected_scope.items()):
        return False, "provider capability response does not match the requested scope", None
    if "diagnostic" in data:
        if any(not isinstance(data.get(key), str) or not data[key]
               for key in ("deployment_id", "repository", "profile")):
            return False, "provider capability response does not match the requested scope", None
        try:
            diagnostic = _capability_diagnostic(data["diagnostic"])
        except ConfigurationError as error:
            return False, str(error), None
        if (data.get("profile_supported") is not True or data.get("repository_access") is not False
                or data.get("managed_dispatch") is not False):
            return False, "provider capability diagnostic contradicts capability flags", None
        return False, UNSUPPORTED_AUTHORITY_REASON, diagnostic
    for key in ("profile_supported", "repository_access", "managed_dispatch"):
        if data.get(key) is not True:
            return False, f"{key} unavailable", None
    return True, "ready", None


def readiness(data: Any) -> tuple[bool, str]:
    """Return the existing readiness projection without a retained observation."""
    ready, reason, _ = capability_observation(data)
    return ready, reason
