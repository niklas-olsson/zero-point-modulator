"""Schema-3 role-local configuration and effect-free private preparation.

Native installation commands live in role_manager. These functions perform only
the same explicit data/Git reads and private bundle writes as legacy preparation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from urllib.parse import urlsplit

from . import dependencies, registry
from .installation_roles import role_services
from .models import Binding, ConfigurationError, ProviderConfig, identifier, repository
from .store import Store


ROLE_COMMON = {"schema_version", "role", "deployment_id", "sources", "binding"}
CONTROL_FIELDS = {"base_commit", "policy", "max_workers", "runtime_principals", "destinations"}
EXECUTION_FIELDS = {"source", "image", "command", "runtime", "destination_id", "worker_ceiling",
                    "authority_endpoint", "runtime_principal", "runtime_token_ref", "lifecycle_token_ref"}
ROOT_LIMITS = {"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000,
               "pids_limit": 128, "root_timeout_ms": 300000, "max_archive_bytes": 16777216}
PRIVATE_LIMIT = 1_000_000
TOKEN_REF = re.compile(r"env:ZPM_INSTALL_[A-Z][A-Z0-9_]{0,95}\Z")


def common():
    # deployment imports this module only at schema-3 dispatch seams.
    from . import deployment
    return deployment


def credential_reference(value):
    if not isinstance(value, str) or not TOKEN_REF.fullmatch(value):
        raise ConfigurationError("role credentials require an explicit env:ZPM_INSTALL_ reference")
    return value


def _canonical_binding(value):
    binding = Binding.parse(value)
    if binding.repository != value.get("repository"):
        raise ConfigurationError("role installation requires the exact canonical repository identity")
    if len(binding.repository.encode("utf-8")) > 512:
        raise ConfigurationError("role canonical repository exceeds the native scope bound")
    if binding.desired_workers != 0:
        raise ConfigurationError("role preparation requires target zero; change retained capacity explicitly afterward")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", binding.profile):
        raise ConfigurationError("role profile contains unsupported native characters")
    return binding.to_dict()


def _control_policy(value):
    helper = common()
    helper._object(value, {"checks", "reviewers", "max_attempts", "budget_seconds",
                           "attempt_seconds", "capabilities"}, "role canonical policy")
    checks = helper._strings(value["checks"], "checks")
    reviewers = helper._strings(value["reviewers"], "reviewers")
    if reviewers != ["operator"]:
        raise ConfigurationError("role installation requires the operator reviewer principal")
    budget = helper._number(value["budget_seconds"], "budget_seconds", 1, 604800)
    return {"checks": checks, "reviewers": reviewers,
            "max_attempts": helper._number(value["max_attempts"], "max_attempts", 1, 1000, True),
            "budget_seconds": budget,
            "attempt_seconds": helper._number(value["attempt_seconds"], "attempt_seconds", 1, budget),
            "capabilities": helper._strings(value["capabilities"], "capabilities", False)}


def _catalog(rows, kind):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ConfigurationError("role credential catalog requires 1 to 100 entries")
    key = "identity" if kind == "runtime" else "destination_id"
    result = []
    for row in rows:
        common()._object(row, {key, "token_ref"}, "role credential entry")
        name = identifier(row[key], key) if kind == "runtime" else registry._destination_id(row[key])
        if kind == "runtime" and name in {"operator", "ingress"}:
            raise ConfigurationError("runtime principal must be distinct from controller operator principals")
        result.append({key: name, "token_ref": credential_reference(row["token_ref"])})
    if len({row[key] for row in result}) != len(result):
        raise ConfigurationError("role credential catalog contains duplicate identities")
    return sorted(result, key=lambda row: row[key])


def parse_configuration(value):
    helper = common()
    if type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 3:
        raise ConfigurationError("role installer schema_version must be 3")
    role = value.get("role")
    selected = role_services(role)
    required = ROLE_COMMON | (CONTROL_FIELDS if role == "controller-authority" else EXECUTION_FIELDS)
    helper._object(value, required | {"port", "timeout_seconds", "dependency_plan"}, "role installation", required)
    helper._object(value["sources"], set(selected), "role sources")
    sources = {name: helper._checkout(value["sources"][name], name) for name in selected}
    for name in selected:
        if any(not (Path(sources[name]["checkout"]) / path).is_file()
               for path in helper.MANAGED_ENTRYPOINTS[name]):
            raise ConfigurationError("role source lacks its required managed entry points")
    binding = _canonical_binding(value["binding"])
    timeout = helper._number(value.get("timeout_seconds", 10), "timeout_seconds", 0.001, 120)
    result = {"schema_version": 3, "role": role, "deployment_id": identifier(value["deployment_id"], "deployment_id"),
              "sources": sources, "binding": binding, "timeout_seconds": float(timeout),
              "port": helper._number(value.get("port", 8766 if role == "controller-authority" else 8000),
                                     "role service port", 1024, 65535, True)}
    if role == "controller-authority":
        base = value["base_commit"]
        if type(base) is not str or not re.fullmatch(r"[0-9a-f]{40}", base):
            raise ConfigurationError("canonical base requires an explicit full SHA-1 revision")
        result.update(base_commit=base, policy=_control_policy(value["policy"]),
                      max_workers=helper._number(value["max_workers"], "max_workers", 1, 2**63 - 1, True),
                      runtime_principals=_catalog(value["runtime_principals"], "runtime"),
                      destinations=_catalog(value["destinations"], "destination"))
        references = [row["token_ref"] for name in ("runtime_principals", "destinations") for row in result[name]]
    else:
        source = helper._checkout(value["source"], "repository source")
        if len(source["revision"]) != 40:
            raise ConfigurationError("managed execution requires a SHA-1 task source")
        image = value["image"]
        if type(image) is not str or not re.fullmatch(r"(?:sha256:|[^\s@]+@sha256:)[0-9a-f]{64}", image):
            raise ConfigurationError("role image requires an immutable SHA-256 identity")
        command = value["command"]
        if not isinstance(command, list) or not 1 <= len(command) <= 128 or any(
                type(part) is not str or not part or "\x00" in part or len(part) > 4096 for part in command):
            raise ConfigurationError("role root command requires explicit bounded argv")
        supplied = value["runtime"]
        if type(supplied) is not dict or set(supplied) - set(ROOT_LIMITS):
            raise ConfigurationError("role runtime contains unsupported fields")
        limits = ROOT_LIMITS | supplied
        bounds = {"uid": (1, 65535), "memory_bytes": (16777216, 1 << 40), "cpu_quota": (1000, 1 << 31),
                  "pids_limit": (8, 65536), "root_timeout_ms": (1000, 86400000), "max_archive_bytes": (1024, 1 << 30)}
        for name, (minimum, maximum) in bounds.items():
            helper._number(limits[name], name, minimum, maximum, True)
        if limits["root_timeout_ms"] % 1000:
            raise ConfigurationError("native root timeout requires integer whole seconds expressed in milliseconds")
        if limits["uid"] == 65534:
            raise ConfigurationError("uid 65534 is reserved for the native runtime watchdog")
        runtime_ref, lifecycle_ref = credential_reference(value["runtime_token_ref"]), credential_reference(value["lifecycle_token_ref"])
        authority = ProviderConfig.parse({"schema_version": 1, "endpoint": value["authority_endpoint"],
            "token_ref": runtime_ref, "max_workers": 0, "timeout_seconds": timeout}).endpoint
        parsed_authority = urlsplit(authority)
        if parsed_authority.path not in {"", "/"} or (parsed_authority.scheme == "http" and parsed_authority.hostname not in {"127.0.0.1", "::1"}):
            raise ConfigurationError("native authority requires a URL root and HTTPS or literal 127.0.0.1/::1 HTTP")
        principal = identifier(value["runtime_principal"], "runtime_principal")
        if principal in {"operator", "ingress"}:
            raise ConfigurationError("runtime principal aliases a controller operator principal")
        result.update(source=source, image=image, command=list(command), runtime=limits,
                      destination_id=registry._destination_id(value["destination_id"]),
                      worker_ceiling=helper._number(value["worker_ceiling"], "worker_ceiling", 0, 2**53 - 1, True),
                      authority_endpoint=authority, runtime_principal=principal,
                      runtime_token_ref=runtime_ref, lifecycle_token_ref=lifecycle_ref)
        references = [runtime_ref, lifecycle_ref]
    if len(set(references)) != len(references):
        raise ConfigurationError("role credential references must be distinct")
    if "dependency_plan" in value:
        result["dependency_plan"] = dependencies.normalize_reference(value["dependency_plan"], sources, role=role)
    return result


def private_object(path, limit=PRIVATE_LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ConfigurationError("role state requires an owned private regular file")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ConfigurationError("role state exceeds its supported size")
    try:
        return registry.decode_json(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigurationError("role state contains invalid JSON") from None


def _resolve(references):
    result = {}
    for reference in references:
        name = reference.removeprefix("env:")
        token = os.environ.get(name, "")
        if not 32 <= len(token) <= 4096 or any(character.isspace() for character in token) or "\x00" in token:
            raise ConfigurationError("a declared scoped role credential is unavailable or invalid")
        result[name] = token
    if len(set(result.values())) != len(result):
        raise ConfigurationError("role credentials must not alias another principal or destination")
    return result


def expected_files(data):
    shared = {"config/repository.json", "secrets/environment.json", "manage.py", "README.txt", "preparation.json"}
    if data["role"] == "controller-authority":
        return shared | {"config/polyphony.json", "config/registry.json", "secrets/polyphony.json", "secrets/operator.json"}
    return shared | {"config/openjules.json", "secrets/openjules.json"}


def directories(data):
    return ("config", "secrets", "state", "environments", "state/polyphony" if data["role"] == "controller-authority" else "state/openjules")


def prepare_files(data, root, final):
    helper = common()
    for name in directories(data):
        (root / name).mkdir(mode=0o700)
    binding = data["binding"]
    scope = {"deployment_id": data["deployment_id"], "repository": binding["repository"]}
    helper._write(root / "config/repository.json", binding)
    if data["role"] == "controller-authority":
        references = [row["token_ref"] for name in ("runtime_principals", "destinations") for row in data[name]]
        supplied = _resolve(references)
        tokens = {role: secrets.token_urlsafe(48) for role in ("owner", "reviewer", "ingress")}
        if len(set(tokens.values()) | set(supplied.values())) != len(tokens) + len(supplied):
            raise ConfigurationError("generated operator credentials alias scoped role credentials")
        grants = {tokens["owner"]: {"identity": "operator", "role": "owner"}}
        for role in ("reviewer", "ingress"):
            grants[tokens[role]] = {"identity": "operator" if role == "reviewer" else "ingress", "role": role,
                                   "deployment_id": data["deployment_id"], "repositories": [binding["repository"]]}
        for item in data["runtime_principals"]:
            grants[supplied[item["token_ref"][4:]]] = {"identity": item["identity"], "role": "runtime",
                "deployment_id": data["deployment_id"], "repositories": [binding["repository"]]}
        helper._write_managed_json(root / "secrets/polyphony.json", grants)
        helper._write(root / "secrets/operator.json", tokens)
        helper._write(root / "secrets/environment.json", {row["token_ref"][4:]: supplied[row["token_ref"][4:]]
                                                          for row in data["destinations"]})
        policy = data["policy"]
        helper._write(root / "config/polyphony.json", {"schema_version": 1, "deployment_id": data["deployment_id"],
            "repositories": {binding["repository"]: {name: policy[name] for name in
                ("checks", "reviewers", "max_attempts", "budget_seconds")} | {"profiles": {binding["profile"]: {
                    "base": data["base_commit"], "attempt_seconds": policy["attempt_seconds"],
                    "capabilities": policy["capabilities"]}}}}})
        configuration = {"schema_version": 2, "max_workers": data["max_workers"], "timeout_seconds": data["timeout_seconds"],
                         "destinations": [], "profiles": []}
        helper._write(root / "config/registry.json", configuration)
        # Reuse the supported offline transition: a fresh zero-work legacy
        # binding is retained in v2 without bypassing its activation fence.
        with Store(root / "state/zpm.sqlite3", data["deployment_id"]) as store:
            store.enroll(binding)
            store.migrate_registry(configuration, root / "state/prepared-v1.sqlite3", offline=True)
        (root / "state/zpm.sqlite3").chmod(0o600)
    else:
        supplied = _resolve([data["runtime_token_ref"], data["lifecycle_token_ref"]])
        helper._write_managed_json(root / "secrets/openjules.json", {supplied[data["lifecycle_token_ref"][4:]]: [scope]})
        helper._write(root / "secrets/environment.json", {data["runtime_token_ref"][4:]: supplied[data["runtime_token_ref"][4:]]})
        helper._write_managed_json(root / "config/openjules.json", {
            "schema_version": 2, "host": "127.0.0.1", "port": data["port"], "poll_interval_seconds": 1,
            "state_directory": str(final / "state/openjules/native"),
            "lifecycle_database": str(final / "state/openjules/lifecycle.sqlite3"),
            "credentials_file": str(final / "secrets/openjules.json"),
            "authority": {"endpoint": data["authority_endpoint"], "token_ref": data["runtime_token_ref"]},
            "execution_destination": {"destination_id": data["destination_id"]},
            "execution_contract": {"version": 1, "mode": "full", "initial_worker_ceiling": data["worker_ceiling"],
                "public_profiles": [scope | {"profile": binding["profile"], "model_service_policy_id": None}]},
            "profiles": [{"name": binding["profile"], **scope, "source_path": data["source"]["checkout"],
                "base_commit": data["source"]["revision"], "image": data["image"], "command": data["command"],
                **data["runtime"], "environment_refs": {}}],
        })
    helper._finish_bundle(data, root, final, role_readme(data))


def verify_files(root, data, manifest):
    expected = expected_files(data) | ({"dependencies/files.json"} if "dependency_plan" in data else set())
    if set(manifest["files"]) != expected:
        raise ConfigurationError("role bundle integrity records do not match its declared role")
    for name in ("config", "secrets"):
        names = {path.relative_to(root).as_posix() for path in (root / name).iterdir()}
        if names != {path for path in expected if path.startswith(name + "/")}:
            raise ConfigurationError("role bundle contains foreign configuration or secrets")
    selected = set(role_services(data["role"]))
    for service in {"openjules", "polyphony"} - selected:
        for path in (root / "environments" / service, root / "state" / service):
            if path.exists() or path.is_symlink():
                raise ConfigurationError("role bundle contains another role's environment or state")
    environment = private_object(root / "secrets/environment.json")
    expected_refs = ({item["token_ref"][4:] for item in data["destinations"]} if data["role"] == "controller-authority"
                     else {data["runtime_token_ref"][4:]})
    if type(environment) is not dict or set(environment) != expected_refs:
        raise ConfigurationError("role environment does not match its declared scoped references")
    from . import role_manager
    role_manager.verify_retained_installation(root, data)


def role_readme(data):
    role = data["role"]
    return f"""Private role-local ZPM installation: {role}.
This bundle is bound to its original path and exact source revisions. Do not copy
it to another host, replace initialized state, or merge another role into it.
Use python3 manage.py --help for explicit role operations and the source guide
docs/role-installation.md for the complete bootstrap and retained-recovery flow.
Preparation is offline; bootstrap installs dependencies. Neither initializes
execution destinations, grants host admission, enables placement or raises a
target. Private pairwise credentials must be supplied through the operator's
chosen secure channel. Repreparation preserves the retained values and state.
Execution and capture occur on the selected execution host. The controller and
canonical authority retain their own separate local journals. No model or child
facility is configured by this first deterministic role installer.
"""
