"""Installed role commands; producers retain their authority and native state."""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time

from . import approvals, dependencies, destination_contract, destination_state, installation_context, registry
from .deployment_roles import common, private_object
from .installation_roles import services
from .models import ConfigurationError, ProviderConfig
from .portable_manifest import MAX_APPROVAL_BYTES, parse_approval
from .provider import DestinationProvider, OpenJulesProvider, ProviderError


INIT_INTENT = "state/destination-initialization.json"
BINDING_RECEIPT = "state/destination-binding.json"
READY_RECEIPT = "state/destination-ready.json"
APPROVED_ENVELOPE = "state/execution-approval.json"
AUTHORITY_INTENT = "state/authority-start-intent.json"
AUTHORITY_RECEIPT = "state/authority-file-identity.json"
COMMAND_SECONDS = 45


def _present(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _same(value, expected):
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(_same(value[key], item) for key, item in expected.items())
    if type(expected) is list:
        return len(value) == len(expected) and all(_same(item, other) for item, other in zip(value, expected))
    return value == expected


def _publish(path, raw, *, maximum=1_000_000):
    """Publish exact private bytes once, preserving partial/foreign state."""
    if len(raw) > maximum:
        raise ConfigurationError("role operation evidence exceeds its size bound")
    parent = path.parent
    info = parent.lstat()
    if parent != parent.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ConfigurationError("role operation evidence requires an owned private directory")
    if _present(path):
        if _private_bytes(path, maximum) != raw:
            raise ConfigurationError("retained role operation evidence differs; it cannot be replaced")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".role-evidence-", dir=parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _private_bytes(path, maximum) != raw:
                raise ConfigurationError("role operation evidence appeared with different bytes") from None
        common()._fsync_directory(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _private_bytes(path, maximum=1_000_000):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= maximum):
            raise ConfigurationError("role operation requires an owned private regular file")
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
    if len(raw) != before.st_size or len(raw) > maximum or any(
            getattr(before, key) != getattr(after, key) for key in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid")):
        raise ConfigurationError("role operation input changed during its bounded read")
    return raw


def _base_record(root, data):
    manifest = private_object(root / "manifest.json")
    return {"schema_version": 1, "managed_config_sha256": manifest["files"]["config/openjules.json"],
            "deployment_id": data["deployment_id"], "repository": data["binding"]["repository"],
            "profile": data["binding"]["profile"], "destination_id": data["destination_id"]}


def _intent(root, data):
    expected = _base_record(root, data) | {"kind": "native-destination-initialization-intent"}
    if _present(root / INIT_INTENT) and not _same(private_object(root / INIT_INTENT), expected):
        raise ConfigurationError("retained destination initialization intent changed")
    return expected


def _binding(root, data, *, required=True):
    path = root / BINDING_RECEIPT
    if not _present(path):
        if required:
            raise ConfigurationError("original initialized destination binding is unavailable")
        return None
    value = private_object(path)
    expected = _base_record(root, data)
    if type(value) is not dict or set(value) != set(expected) | {"destination_binding"} or any(not _same(value.get(k), v) for k, v in expected.items()):
        raise ConfigurationError("retained destination binding receipt changed")
    binding = registry.destination_binding(value["destination_binding"])
    if binding["destination_id"] != data["destination_id"]:
        raise ConfigurationError("retained destination identity differs from its installation")
    return binding


def verify_retained_installation(root, data):
    """Offline structural checks. Native state validation is a later operation."""
    if data["role"] == "controller-authority":
        _verify_authority_guard(root, data)
        _current_catalog(root, data)
        return
    if _present(root / "state/zpm.sqlite3"):
        raise ConfigurationError("execution role cannot contain a controller journal")
    intent_present = _present(root / INIT_INTENT)
    _intent(root, data)
    binding = _binding(root, data, required=False)
    ready = _present(root / READY_RECEIPT)
    if (binding is not None or ready or _present(root / APPROVED_ENVELOPE)) and not intent_present:
        raise ConfigurationError("execution state has no original installation intent")
    native = root / "state/openjules"
    if not intent_present and any(native.iterdir()):
        raise ConfigurationError("execution state appeared without its original installation intent")
    if binding is not None:
        for path in (native / "native/destination.json", native / "lifecycle.sqlite3"):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ConfigurationError("retained initialized destination state is unavailable")
    if ready and (binding is None or not _same(private_object(root / READY_RECEIPT), _base_record(root, data) | {"destination_binding": binding})):
        raise ConfigurationError("retained destination readiness evidence changed")
    if _present(root / APPROVED_ENVELOPE):
        _approved(root, data, root / APPROVED_ENVELOPE)


def _authority_record(root, data):
    manifest = private_object(root / "manifest.json")
    return {"schema_version": 1, "kind": "same-installation-authority-file-guard",
            "configuration_sha256": manifest["files"]["config/polyphony.json"],
            "deployment_id": data["deployment_id"], "repository": data["binding"]["repository"],
            "profile": data["binding"]["profile"], "base_commit": data["base_commit"]}


def _authority_file(root):
    path = root / "state/polyphony/authority.sqlite3"
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        header = source.read(100)
        after = os.fstat(source.fileno())
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600
            or len(header) != 100 or header[:16] != b"SQLite format 3\x00"
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        raise ConfigurationError("original authority database file is missing, replaced, empty or invalid")
    page_size = int.from_bytes(header[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    if page_size not in {512, 1024, 2048, 4096, 8192, 16384, 32768, 65536} or after.st_size < page_size or after.st_size % page_size:
        raise ConfigurationError("original authority database file is truncated or invalid")
    return {"device": before.st_dev, "inode": before.st_ino}


def _verify_authority_guard(root, data):
    expected = _authority_record(root, data)
    intent, receipt = root / AUTHORITY_INTENT, root / AUTHORITY_RECEIPT
    database = root / "state/polyphony/authority.sqlite3"
    if not _present(intent):
        if _present(receipt) or _present(database):
            raise ConfigurationError("authority state appeared without its first-start installation intent")
        return False
    if not _same(private_object(intent), expected):
        raise ConfigurationError("authority first-start installation intent changed")
    if not _present(receipt):
        raise ConfigurationError("authority first-start outcome is unknown; retain state, do not initialize or adopt a replacement")
    value = private_object(receipt)
    if (type(value) is not dict or set(value) != set(expected) | {"file_identity"}
            or any(not _same(value.get(k), v) for k, v in expected.items())
            or type(value["file_identity"]) is not dict or set(value["file_identity"]) != {"device", "inode"}
            or any(type(v) is not int or v < 0 for v in value["file_identity"].values())
            or value["file_identity"] != _authority_file(root)):
        raise ConfigurationError("original authority database file identity is unavailable or changed")
    return True


def _begin_authority(root, data, *, authority_only):
    retained = _verify_authority_guard(root, data)
    if retained:
        return
    if not authority_only:
        raise ConfigurationError("first controller-authority startup requires explicit --authority-only")
    _publish(root / AUTHORITY_INTENT, _encoded(_authority_record(root, data)))


def _confirm_authority(root, data, process):
    """Current readiness of the owned process, separate from file identity."""
    credentials = private_object(root / "secrets/polyphony.json")
    identity = data["runtime_principals"][0]["identity"]
    candidates = [token for token, grant in credentials.items() if grant.get("role") == "runtime" and grant.get("identity") == identity]
    if len(candidates) != 1:
        raise ConfigurationError("configured authority readiness principal is unavailable")
    name = "ZPM_ROLE_AUTHORITY_READINESS_TOKEN"
    scope = {"deployment_id": data["deployment_id"], "repository": data["binding"]["repository"], "profile": data["binding"]["profile"]}
    deadline = time.monotonic() + 15
    expected = {"schema_version": 1, **scope, "service": "keystone-polyphony", "protocol": "managed-runtime-v1",
                "base": data["base_commit"], "profile_supported": True, "managed_dispatch": True, "external_root": True}
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 0.001:
            break
        if process.poll() is not None:
            raise ConfigurationError("owned canonical authority exited before startup was confirmed")
        provider = OpenJulesProvider(ProviderConfig.parse({"schema_version": 1, "endpoint": "http://127.0.0.1:" + str(data["port"]),
            "token_ref": "env:" + name, "max_workers": 0, "timeout_seconds": min(1.0, data["timeout_seconds"], remaining)}))
        try:
            with _request_credential(name, candidates[0]):
                value = provider._send("GET", "/capabilities", query=scope, base_path="/managed", strict=True)
            if time.monotonic() >= deadline:
                raise ConfigurationError("canonical authority startup reply exceeded its observation deadline")
            if type(value) is not dict or any(type(value.get(k)) is not type(v) or value[k] != v for k, v in expected.items()):
                raise ConfigurationError("canonical authority startup returned a different scope or contract")
            if process.poll() is not None:
                raise ConfigurationError("owned canonical authority exited during startup observation")
            file_identity = _authority_file(root)
            retained = root / AUTHORITY_RECEIPT
            result = _authority_record(root, data) | {"file_identity": file_identity}
            if time.monotonic() >= deadline:
                raise ConfigurationError("canonical authority startup observation exceeded its deadline")
            _publish(retained, _encoded(result))
            return
        except ProviderError:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    raise ConfigurationError("canonical authority startup is unconfirmed; retain its original first-start intent and state")


def _inspection(value, data):
    expected = {"schema_version", "destination_id", "journal_incarnation", "identity_binding",
                "portable_manifest", "local_admission", "journals_initialized", "profiles"}
    destination_contract.exact_object(value, expected)
    destination_contract.integer(value["schema_version"], 2, 2)
    for key in ("identity_binding", "portable_manifest", "local_admission"):
        destination_contract.integer(value[key], 1, 1)
    if type(value["journals_initialized"]) is not bool or type(value["profiles"]) is not list or len(value["profiles"]) != 1:
        raise ConfigurationError("native destination inspection has an unsupported readiness or profile shape")
    row = value["profiles"][0]
    destination_contract.exact_object(row, {"deployment_id", "repository", "profile", "destination_binding", "current_policy_matches"})
    if any(row.get(key) != expected for key, expected in {"deployment_id": data["deployment_id"],
            "repository": data["binding"]["repository"], "profile": data["binding"]["profile"]}.items()) or row["current_policy_matches"] is not True:
        raise ConfigurationError("native destination inspection does not match its original approved scope")
    binding = registry.destination_binding(row["destination_binding"])
    if (binding["destination_id"] != data["destination_id"] or value["destination_id"] != binding["destination_id"]
            or value["journal_incarnation"] != binding["journal_incarnation"]):
        raise ConfigurationError("native destination inspection changed its installation identity")
    return binding


def _invoke(root, command, cwd, environment, *, allowed=(0,)):
    directory = root / "state/command-logs"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (directory != directory.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ConfigurationError("role diagnostics require an owned private directory")
    raw = dependencies._run(command, cwd, environment, COMMAND_SECONDS,
                            allowed_returncodes=allowed, failure_directory=directory,
                            label="installed role")
    value = destination_contract.decode_response(raw)
    if type(value) is not dict:
        raise ConfigurationError("installed role command returned an unsupported result")
    return value


def _native(root, data, *arguments):
    return _invoke(root, common()._command(root, "openjules", data) + list(arguments),
                   data["sources"]["openjules"]["checkout"], common()._environment(root, "openjules", data))


def _inspect_original(root, data):
    original = _binding(root, data)
    value = _native(root, data, "--inspect-destination")
    if _inspection(value, data) != original:
        raise ConfigurationError("native destination differs from its retained original binding")
    if value["journals_initialized"]:
        _publish(root / READY_RECEIPT, _encoded(_base_record(root, data) | {"destination_binding": original}))
    elif _present(root / READY_RECEIPT):
        raise ConfigurationError("native destination readiness regressed after its original ready transition")
    return value


def initialize(root, data):
    with common()._manager_lock(root) as lock:
        with dependencies.command_guard(lock):
            if _binding(root, data, required=False) is not None:
                return _inspect_original(root, data)
            if _present(root / INIT_INTENT):
                # A repeat may recover a completed original initialization. It
                # never calls a fresh initializer after an uncertain attempt.
                value = _native(root, data, "--inspect-destination")
            else:
                if any((root / "state/openjules").iterdir()):
                    raise ConfigurationError("fresh initialization refuses existing execution state")
                _publish(root / INIT_INTENT, _encoded(_intent(root, data)))
                value = _native(root, data, "--initialize-destination")
            binding = _inspection(value, data)
            _publish(root / BINDING_RECEIPT, _encoded(_base_record(root, data) | {"destination_binding": binding}))
            if value["journals_initialized"]:
                _publish(root / READY_RECEIPT, _encoded(_base_record(root, data) | {"destination_binding": binding}))
            return value


def _approved(root, data, path):
    item = approvals.read_private_file(path)
    if (any(item.envelope[key] != expected for key, expected in {"deployment_id": data["deployment_id"],
            "repository": data["binding"]["repository"], "profile": data["binding"]["profile"]}.items())
            or item.envelope["destination_binding"] != _binding(root, data)):
        raise ConfigurationError("public execution approval differs from this original role destination")
    return item


def preview(root, data, output):
    output = Path(output)
    if not output.is_absolute() or _present(output):
        raise ConfigurationError("execution preview requires a new absolute private approval path")
    with common()._manager_lock(root) as lock:
        with dependencies.command_guard(lock):
            _inspect_original(root, data)
            value = _native(root, data, "--preview-execution-contract", "--deployment-id", data["deployment_id"],
                            "--repository", data["binding"]["repository"], "--profile", data["binding"]["profile"])
            item = parse_approval(_encoded(value))
            if item.envelope["destination_binding"] != _binding(root, data) or any(
                    item.envelope[key] != expected for key, expected in {"deployment_id": data["deployment_id"],
                        "repository": data["binding"]["repository"], "profile": data["binding"]["profile"]}.items()):
                raise ConfigurationError("native preview changed its original scope or destination")
            _publish(output, item.raw, maximum=MAX_APPROVAL_BYTES)
            return {"previewed": True, "approval_file": str(output), "destination_binding": item.envelope["destination_binding"],
                    "execution_manifest_sha256": item.envelope["execution_manifest_sha256"], "approved": False}


def approve(root, data, path):
    with common()._manager_lock(root) as lock:
        with dependencies.command_guard(lock):
            _inspect_original(root, data)
            item = _approved(root, data, path)
            retained = root / APPROVED_ENVELOPE
            accepted = _approved(root, data, retained) if _present(retained) else None
            if accepted is not None and accepted.envelope != item.envelope:
                raise ConfigurationError("execution approval differs from the retained producer-confirmed document")
            attempted = root / "state" / ("execution-approval-input-" + hashlib.sha256(item.raw).hexdigest() + ".json")
            _publish(attempted, item.raw, maximum=MAX_APPROVAL_BYTES)
            result = _native(root, data, "--approve-execution-contract", str(attempted))
            destination_contract.exact_object(result, {"schema_version", "approved", "execution_manifest_sha256"})
            if type(result["schema_version"]) is not int or result["schema_version"] != 1 or result["approved"] is not True or result["execution_manifest_sha256"] != item.envelope["execution_manifest_sha256"]:
                raise ConfigurationError("native approval did not confirm the retained exact manifest")
            _publish(retained, accepted.raw if accepted is not None else item.raw, maximum=MAX_APPROVAL_BYTES)
            return result


def host_admission(root, data, operation, value=None):
    # Deliberately no lifetime manager lock: these are producer owner-local
    # admission operations, valid while the foreground service runs.
    _inspect_original(root, data)
    arguments = ["--" + operation]
    if value is not None:
        destination_contract.integer(value)
        arguments.append(str(value))
    return destination_contract.admission(_native(root, data, *arguments))


def _controller_command(root, data, *arguments, allowed=(0,)):
    command = [str(root / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(root / "state/zpm.sqlite3"),
               "--deployment-id", data["deployment_id"], *arguments]
    return _invoke(root, command, data["sources"]["zpm"]["checkout"], common()._environment(root, "zpm", data), allowed=allowed)


def _catalog_matches(data, destinations, profiles):
    catalog = {row["destination_id"]: row["token_ref"] for row in data["destinations"]}
    for row in destinations:
        if row["destination_id"] not in catalog or row["token_ref"] != catalog[row["destination_id"]]:
            raise ConfigurationError("destination transport does not use its exact declared scoped credential")
    for row in profiles:
        if (row["destination_id"] not in catalog or row["repository"] != data["binding"]["repository"]
                or row["profile"] != data["binding"]["profile"]):
            raise ConfigurationError("destination profile differs from the role's declared canonical scope")


def _current_catalog(root, data):
    try:
        with closing(sqlite3.connect((root / "state/zpm.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            destinations = [registry.decode_json(row[0]) for row in connection.execute("SELECT specification FROM destinations")]
            profiles = [registry.decode_json(row[0]) for row in connection.execute("SELECT specification FROM destination_profiles")]
            active = destination_state.enabled(connection)
    except sqlite3.Error:
        raise ConfigurationError("original controller journal is unavailable; the preparation backup is not a replacement") from None
    _catalog_matches(data, destinations, profiles)
    return active


def registry_import(root, data, path):
    raw = _private_bytes(Path(path))
    value = registry.configuration(destination_contract.decode_response(raw), active=_current_catalog(root, data))
    _catalog_matches(data, value["destinations"], value["profiles"])
    # The installed CLI reads the exact validated private bytes, not a later
    # revision of the caller's mutable file.
    retained = root / "state" / ("registry-input-" + hashlib.sha256(raw).hexdigest() + ".json")
    _publish(retained, raw)
    return _controller_command(root, data, "registry", "import", str(retained))


def controller_approval(root, data, path):
    item = approvals.read_private_file(path)
    if any(item.envelope[key] != expected for key, expected in {"deployment_id": data["deployment_id"],
            "repository": data["binding"]["repository"], "profile": data["binding"]["profile"]}.items()):
        raise ConfigurationError("approval differs from the controller role's canonical scope")
    if item.envelope["destination_binding"]["destination_id"] not in {row["destination_id"] for row in data["destinations"]}:
        raise ConfigurationError("approval destination is absent from the controller credential catalog")
    _current_catalog(root, data)
    retained = root / "state" / ("approval-input-" + hashlib.sha256(item.raw).hexdigest() + ".json")
    _publish(retained, item.raw, maximum=MAX_APPROVAL_BYTES)
    return _controller_command(root, data, "registry", "import-approval", str(retained))


def _require_bootstrap(root, data):
    for service in services(data):
        value = private_object(root / "environments" / service / "bootstrap-complete.json")
        if (type(value) is not dict or set(value) != {"revision", "python"}
                or value["revision"] != data["sources"][service]["revision"]
                or type(value["python"]) is not str or not value["python"]):
            raise ConfigurationError("role bootstrap is incomplete or belongs to another source revision")
    common()._installation_context(root, data)


@contextmanager
def _request_credential(name, value):
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def doctor(root, data, maximum=16):
    if data["role"] == "controller-authority":
        if not _current_catalog(root, data):
            return {"ready": False, "qualification_complete": False, "reason": "explicit offline journal format activation is required",
                    "remote_access_checked": False, "admission_changed": False}
        started = time.time()
        report = _controller_command(root, data, "check", "--registry", "--max-actions", str(maximum), allowed=(0, 3))
        profiles = [row for destination in report.get("destinations", []) for row in destination.get("profiles", [])]
        qualified = bool(profiles) and all(row.get("approved") is True
            and type(row.get("validation", {}).get("checked_at")) in (int, float)
            and row["validation"]["checked_at"] >= started and type(row["validation"].get("admission")) is dict for row in profiles)
        return {"ready": qualified and all(row["validation"]["ready"] is True for row in profiles),
                "qualification_complete": qualified, "remote_access_checked": True, "admission_changed": False,
                "placement_enabled": any(row.get("enabled") is True for row in report.get("destinations", [])),
                "capabilities": report}
    inspected = _inspect_original(root, data)
    approval = _approved(root, data, root / APPROVED_ENVELOPE)
    binding = _binding(root, data)
    credentials = private_object(root / "secrets/openjules.json")
    if type(credentials) is not dict or len(credentials) != 1:
        raise ConfigurationError("execution role lifecycle credentials changed")
    name = "ZPM_ROLE_INSPECTION_TOKEN"
    provider = DestinationProvider(ProviderConfig.parse({"schema_version": 1, "endpoint": "http://127.0.0.1:" + str(data["port"]),
        "token_ref": "env:" + name, "max_workers": data["worker_ceiling"], "timeout_seconds": data["timeout_seconds"]}))
    scope = {"deployment_id": data["deployment_id"], "repository": data["binding"]["repository"],
             "profile": data["binding"]["profile"], "binding": binding}
    with _request_credential(name, next(iter(credentials))):
        canonical_ready, reason = destination_contract.capabilities(provider.capabilities(**scope), **scope)
        admission = destination_contract.contract_response(provider.execution_contract(**scope), **scope, approved=approval.envelope)
    return {"ready": canonical_ready and admission["new_effects_open"] and admission["unreleased_workers"] < admission["worker_ceiling"],
            "qualification_complete": canonical_ready, "canonical_ready": canonical_ready, "reason": reason,
            "destination_binding": binding, "journals_initialized": inspected["journals_initialized"],
            "execution_manifest_sha256": approval.envelope["execution_manifest_sha256"], "admission": admission,
            "remote_access_checked": True, "admission_changed": False}


def start(root, data, *, authority_only=False):
    if data["role"] == "execution":
        if authority_only:
            raise ConfigurationError("authority-only startup belongs to the controller-authority role")
        def before_start():
            _inspect_original(root, data)
            _approved(root, data, root / APPROVED_ENVELOPE)
        after_start = None
        selected = ("openjules",)
    else:
        active = _current_catalog(root, data)
        if not authority_only and not active:
            raise ConfigurationError("ordinary controller startup requires explicit journal format activation")
        selected = ("polyphony",) if authority_only else ("polyphony", "zpm")
        before_start = lambda: _begin_authority(root, data, authority_only=authority_only)
        after_start = lambda processes: _confirm_authority(root, data, processes["polyphony"])
    print(json.dumps({"role": data["role"], "starting_services": selected, "authority_only": authority_only,
                      "admission_changed": False, "target_changed": False}, sort_keys=True), flush=True)
    return common()._start_services(root, data, selected, before_start=before_start, after_start=after_start)


def manage(directory, argv=None):
    parser = argparse.ArgumentParser(description="Manage a private role-local ZPM installation")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("verify", "bootstrap", "initialize-destination", "inspect-destination", "inspect-admission",
                 "open-admission", "close-admission", "status"):
        commands.add_parser(name)
    commands.add_parser("start").add_argument("--authority-only", action="store_true")
    commands.add_parser("preview-execution-contract").add_argument("--output", required=True)
    for name in ("approve-execution-contract", "registry-import", "import-approval"):
        commands.add_parser(name).add_argument("path")
    for name in ("set-worker-ceiling", "target"):
        commands.add_parser(name).add_argument("value", type=int)
    commands.add_parser("activate").add_argument("--offline", action="store_true")
    for name in ("doctor", "reconcile"):
        commands.add_parser(name).add_argument("--max-actions", type=int, default=16)
    args = parser.parse_args(argv)
    try:
        verification = common().verify(directory)
        root = Path(verification["directory"])
        data = private_object(root / "manifest.json")["configuration"]
        operation = args.operation
        if operation == "verify":
            result = verification
        elif operation == "bootstrap":
            result = common()._bootstrap(root, data)
        else:
            _require_bootstrap(root, data)
            host_operations = {"initialize-destination", "inspect-destination", "preview-execution-contract",
                "approve-execution-contract", "inspect-admission", "open-admission", "close-admission", "set-worker-ceiling"}
            control_operations = {"registry-import", "import-approval", "activate", "target", "status", "reconcile"}
            if (operation in host_operations and data["role"] != "execution") or (operation in control_operations and data["role"] != "controller-authority"):
                raise ConfigurationError("operation is not owned by this installation role")
            if operation == "start":
                return start(root, data, authority_only=args.authority_only)
            if operation == "initialize-destination":
                result = initialize(root, data)
            elif operation == "inspect-destination":
                result = _inspect_original(root, data)
            elif operation == "preview-execution-contract":
                result = preview(root, data, args.output)
            elif operation == "approve-execution-contract":
                result = approve(root, data, args.path)
            elif operation in {"inspect-admission", "open-admission", "close-admission", "set-worker-ceiling"}:
                result = host_admission(root, data, operation, getattr(args, "value", None))
            elif operation == "registry-import":
                result = registry_import(root, data, args.path)
            elif operation == "import-approval":
                result = controller_approval(root, data, args.path)
            elif operation == "activate":
                if not args.offline:
                    raise ConfigurationError("journal format activation requires explicit --offline")
                _current_catalog(root, data)
                result = _controller_command(root, data, "registry", "activate", "--offline")
            elif operation == "target":
                destination_contract.integer(args.value)
                _current_catalog(root, data)
                result = _controller_command(root, data, "target", data["binding"]["repository"], str(args.value))
            elif operation == "status":
                _current_catalog(root, data)
                result = _controller_command(root, data, "status")
            elif operation == "doctor":
                result = doctor(root, data, args.max_actions)
            else:
                _current_catalog(root, data)
                result = _controller_command(root, data, "reconcile", "--registry", "--max-actions", str(args.max_actions))
        print(json.dumps(result, sort_keys=True, indent=2))
        return 3 if operation == "doctor" and result.get("ready") is not True else 0
    except (ConfigurationError, ProviderError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({"error": "role operation unavailable; retain original configuration, credentials and state"}), file=sys.stderr)
        return 2
