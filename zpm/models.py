"""Small versioned configuration and runtime contracts."""

from __future__ import annotations

import re
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
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigurationError("provider endpoint must be HTTP(S), without credentials, query or fragment")
        ref = data.get("token_ref")
        if not isinstance(ref, str) or not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", ref):
            raise ConfigurationError("token_ref must name an environment variable, not contain a token")
        timeout = data.get("timeout_seconds", 10.0)
        if type(timeout) not in {float, int} or not 0 < timeout <= 120:
            raise ConfigurationError("timeout_seconds must be greater than zero and at most 120")
        return cls(endpoint.rstrip("/"), ref, count(data.get("max_workers"), "max_workers"), float(timeout))


STATES = {"starting", "ready", "busy", "draining", "stopped", "failed", "unknown"}
OPERATIONS = {"ensure", "discover", "observe", "drain", "stop"}


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


def readiness(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        return False, "unsupported provider capability schema"
    operations = data.get("operations")
    if not isinstance(operations, list) or not OPERATIONS.issubset(set(x for x in operations if isinstance(x, str))):
        return False, "provider lacks required lifecycle operations"
    for key in ("profile_supported", "repository_access", "managed_dispatch"):
        if data.get(key) is not True:
            return False, f"{key} unavailable"
    return True, "ready"
