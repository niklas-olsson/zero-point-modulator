"""Installed admission evidence for a responsive coordinator without managed policy.

This is a current-service configuration test, not an old-release or /health
fixture. The original model trial supplies the subsequent positive execution.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import stat
import subprocess
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from check_stack import Client, eventually, require


AUTHORITY_REASON = "managed runtime policy is not configured"
RUNTIME_REASON = "Canonical managed authority is unavailable"
CONTROLLER_REASON = "repository_access unavailable"
RECORD_KINDS = (
    "task", "attempt", "resource", "problem", "advice", "candidate",
    "evidence", "work", "response", "destination",
)
SCHEMAS = {
    "controller": {
        "metadata": ("key", "value"),
        "workers": ("launch_key", "repository", "slot_id", "generation", "profile",
                    "snapshot", "pending_action", "action_key"),
    },
    "authority": {
        "records": ("kind", "id", "body"),
        "artifacts": ("id", "content"),
        "events": ("seq", "time", "kind", "record_id"),
    },
    "lifecycle": {
        "managed_workers": ("worker_id", "deployment_id", "repository", "slot_id", "generation",
                            "spec", "runtime_id", "state", "reason", "draining",
                            "termination_confirmed", "artifacts_captured"),
        "worker_operations": ("deployment_id", "repository", "request_key", "action",
                              "worker_id", "payload", "completed"),
        "worker_executions": ("worker_id", "execution_id", "parent_id",
                              "termination_confirmed", "artifacts_captured"),
    },
    "runtime": {
        "profiles": ("deployment_id", "repository", "name", "fingerprint"),
        "child_profiles": ("deployment_id", "repository", "name", "fingerprint"),
        "workers": ("worker_id", "spec", "profile_fingerprint", "container_id", "create_dispatched",
                    "start_dispatched", "start_confirmed", "retired", "receipt"),
        "attempts": ("worker_id", "attempt_id", "input_sha256", "seconds", "deadline",
                     "exec_id", "exec_dispatched", "cancelled", "exit_code"),
    },
    "dispatcher": {
        "deliveries": ("worker_id", "spec", "task_id", "request_key", "phase",
                       "binding", "artifacts", "reason"),
    },
    "model": {
        "policies": ("scope", "fingerprint"),
        "workers": ("worker_id", "spec", "policy_digest", "attempt_id", "deadline", "boot_id", "revoked"),
        "calls": ("worker_id", "attempt_id", "request_id", "payload_hash",
                  "reserved_tokens", "status", "response"),
    },
}
EMPTY_TABLES = {
    "controller": ("workers",),
    "authority": ("records", "artifacts", "events"),
    "lifecycle": ("managed_workers", "worker_operations", "worker_executions"),
    "runtime": ("workers", "attempts"),
    "dispatcher": ("deliveries",),
    "model": ("workers", "calls"),
}
SERVICE_NAMES = ("admission-authority", "admission-openjules")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _argument(command, flag):
    require(command.count(flag) == 1, "generated command lacks one required option")
    index = command.index(flag)
    require(index + 1 < len(command), "generated command has an incomplete option")
    return command[index + 1]


def journal_paths(bundle, configuration):
    """Derive paths from generated service config/commands, not guessed layout."""
    from zpm.deployment import _command

    config = json.loads((bundle / "config/openjules.json").read_text())
    state = Path(config["state_directory"])
    paths = {
        "controller": Path(_argument(_command(bundle, "zpm", configuration), "--state")),
        "authority": Path(_argument(_command(bundle, "polyphony", configuration), "--db")),
        "lifecycle": Path(config["lifecycle_database"]),
        "runtime": state / "runtime/journal.sqlite3",
        "dispatcher": state / "dispatcher/dispatch.sqlite3",
        "model": state / "models/inference.sqlite3",
    }
    root = bundle.resolve()
    for path in paths.values():
        require(path.is_absolute() and root in path.resolve().parents,
                "admission journal path leaves the generated installation")
    require(len(set(paths.values())) == len(paths), "admission journals alias each other")
    return paths


def read_tables(path, schema):
    """Read committed WAL state without creating, migrating or checkpointing it."""
    try:
        require(stat.S_ISREG(path.lstat().st_mode), "admission journal is not a regular existing file")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.execute("BEGIN")
            result = {}
            for table, columns in schema.items():
                names = tuple(row[1] for row in db.execute('PRAGMA table_info("' + table + '")'))
                require(names == columns, "admission journal schema does not match its component")
                # Empty tables must have no historical row in any scope. Policy
                # tables must contain exactly one row. Two rows suffice to reject
                # either violation without exporting or loading arbitrary content.
                fields = ",".join('"' + column + '"' for column in columns)
                result[table] = [list(row) for row in db.execute(
                    'SELECT ' + fields + ' FROM "' + table + '" LIMIT 2'
                )]
            return result
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        raise AssertionError("admission journal could not be read without mutation") from None


def _digest_text(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def read_histories(bundle, configuration, scope, profile, expected=None):
    paths = journal_paths(bundle, configuration)
    result = {name: read_tables(paths[name], schema) for name, schema in SCHEMAS.items()}
    for name, tables in EMPTY_TABLES.items():
        for table in tables:
            require(result[name][table] == [], "admission created protected or execution history")
    require(result["controller"]["metadata"] == [["deployment_id", scope["deployment_id"]]],
            "admission controller journal changed deployment identity")
    key = [scope["deployment_id"], scope["repository"], profile]
    profiles = result["runtime"]["profiles"]
    require(len(profiles) == 1 and profiles[0][:3] == key and _digest_text(profiles[0][3]),
            "admission runtime profile is missing or outside the selected scope")
    require(result["runtime"]["child_profiles"] == [key + [None]],
            "admission unexpectedly enabled or changed a child profile")
    policies = result["model"]["policies"]
    require(len(policies) == 1 and policies[0][0] == canonical(key) and _digest_text(policies[0][1]),
            "admission model policy is missing or outside the selected scope")
    if expected is not None:
        require(canonical(result) == canonical(expected), "admission changed retained policy or identity")
    return result


def require_empty_snapshot(value, scope=None):
    fields = set(RECORD_KINDS) | {"cursor"}
    if scope is not None:
        fields |= {"schema_version", "deployment_id", "repository"}
    require(isinstance(value, dict) and set(value) == fields,
            "admission authority snapshot lacks complete evidence")
    require(type(value["cursor"]) is int and value["cursor"] == 0
            and all(value[kind] == [] for kind in RECORD_KINDS),
            "admission authority contains a protected effect")
    if scope is not None:
        require(type(value["schema_version"]) is int and value["schema_version"] == 1
                and all(value[key] == item for key, item in scope.items()),
                "admission authority snapshot changed scope")


def require_runtime_capabilities(value, scope, profile, ready):
    from zpm.models import OPERATIONS

    require(isinstance(value, dict)
            and type(value.get("schema_version")) is int and value["schema_version"] == 1
            and all(value.get(key) == item for key, item in scope.items())
            and value.get("profile") == profile,
            "admission runtime capabilities changed scope")
    operations = value.get("operations")
    require(isinstance(operations, list) and all(isinstance(item, str) for item in operations)
            and set(operations) == OPERATIONS and len(operations) == len(OPERATIONS),
            "admission runtime lost its real lifecycle contract")
    require(value.get("profile_supported") is True
            and value.get("repository_access") is ready and value.get("managed_dispatch") is ready,
            "admission runtime did not report the expected current capability")
    if not ready:
        require(value.get("reason") == RUNTIME_REASON, "admission was blocked by a different failure")


def require_controller_plan(value, scope, profile, target, ready):
    require(isinstance(value, dict)
            and type(value.get("schema_version")) is int and value["schema_version"] == 1
            and value.get("deployment_id") == scope["deployment_id"],
            "admission controller report changed deployment")
    for key, expected in (("max_workers", 1), ("reserved", 0), ("over_capacity", 0)):
        require(type(value.get(key)) is int and value[key] == expected,
                "admission controller report has invalid global capacity")
    rows = value.get("repositories")
    require(isinstance(rows, list) and len(rows) == 1, "admission controller report hid another repository")
    row = rows[0]
    require(isinstance(row, dict) and row.get("repository") == scope["repository"]
            and row.get("profile") == profile and row.get("enabled") is True and row.get("paused") is False,
            "admission controller report changed enrollment")
    for key, expected in (
        ("desired_workers", target), ("effective_target", target), ("permitted_target", target),
        ("healthy", 0), ("reserved", 0), ("shortfall", target),
        ("launch_deficit", target), ("excess_reservations", 0),
    ):
        require(type(row.get(key)) is int and row[key] == expected,
                "admission controller report changed selected or observed capacity")
    reason = "ready" if ready else CONTROLLER_REASON
    require(row.get("workers") == [] and isinstance(row.get("validation"), dict)
            and row["validation"].get("ready") is ready and row["validation"].get("reason") == reason,
            "admission controller report lacks the expected readiness decision")
    require(row.get("reasons") == ([] if ready or target == 0 else [CONTROLLER_REASON]),
            "admission controller report contains an unrelated shortfall")
    return row


def completed_reconcile(processes, command, cwd, environment, name):
    require(command[-1:] == ["--watch"], "admission requires the generated watch command")
    log = processes.root / (name + ".log")
    require(not log.exists() and not log.is_symlink(), "admission controller log is stale")
    processes.run_once(name, command[:-1], cwd, environment, 50)
    try:
        require(stat.S_ISREG(log.lstat().st_mode) and 0 < log.stat().st_size <= 1_000_000,
                "admission controller log is missing or unbounded")
        value = json.loads(log.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AssertionError("admission controller did not emit one complete JSON report") from None
    require(isinstance(value, dict), "admission controller did not emit an object report")
    return value


def docker_inventory():
    result = {}
    for kind, command in (
        ("containers", ["docker", "ps", "-aq", "--no-trunc"]),
        ("volumes", ["docker", "volume", "ls", "-q"]),
    ):
        try:
            output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            raise AssertionError("admission Docker inventory could not be read") from None
        identities = output.split()
        require(len(identities) == len(set(identities)), "admission Docker inventory contains duplicate identities")
        result[kind] = sorted(identities)
    return result


def require_idle_resources(model, expected_inventory):
    with model.lock:
        require(not model.requests and not model.errors, "admission attempted model inference")
    require(docker_inventory() == expected_inventory, "admission changed Docker containers or volumes")


def expected_error(client, method, path, *, query=None, data=None, text=False):
    """Read the small rejection body, which the shared Client discards.

    Reuse its explicit no-proxy/no-redirect opener and three-second I/O timeout.
    This helper accepts only the expected 503 and never emits response content.
    """
    url = client.endpoint + path + ("?" + urlencode(query) if query else "")
    request = Request(url, method=method, data=canonical(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + client.token, "Content-Type": "application/json"})
    try:
        response = client.opener.open(request, timeout=3)
    except HTTPError as error:
        with error:
            require(error.code == 503, "admission endpoint returned a different rejection status")
            payload = error.read(4097)
    else:
        response.close()
        raise AssertionError("admission endpoint unexpectedly accepted the request")
    require(len(payload) <= 4096, "admission rejection body exceeded its bound")
    try:
        decoded = payload.decode("utf-8")
        return decoded if text else json.loads(decoded)
    except (UnicodeError, json.JSONDecodeError):
        raise AssertionError("admission rejection body is malformed") from None


def owner_probe_token(bundle, operator, sensitive):
    """Use the existing generated owner for the legacy read endpoint.

    Runtime/ingress principals cannot read /snapshot. The installer already
    provisions an unscoped owner; no additional principal or credential copy is
    necessary. This credential never enters a service environment or guest.
    """
    path = bundle / "secrets/polyphony.json"
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "admission authority credentials are not private")
    principals = json.loads(path.read_text())
    token = operator.get("owner")
    require(isinstance(token, str) and len(token) >= 32
            and principals.get(token) == {"identity": "operator", "role": "owner"},
            "admission lacks the existing unscoped generated owner")
    sensitive.add(token)
    return token


def no_policy_command(original):
    require(original[1:3] == ["-m", "src.liminal_bridge.coordinator_service"]
            and "--execution-config" not in original,
            "admission requires the generated non-executing coordinator")
    _argument(original, "--db")
    _argument(original, "--port")
    _argument(original, "--credentials")
    _argument(original, "--managed-config")
    command = list(original)
    index = command.index("--managed-config")
    del command[index:index + 2]
    return command


def stop_owned(processes, name):
    process = processes.running.get(name)
    try:
        processes.stop(name)
    finally:
        # Keep ownership of the exact child if normal shutdown raises. This
        # never addresses an externally supplied PID or another service group.
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        if process is not None and process.poll() is not None:
            processes.running.pop(name, None)


@contextmanager
def cleanup_phase(processes, reset_target):
    failure = None
    try:
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        failed = []
        try:
            reset_target()
        except Exception:
            failed.append("target reset")
        for name in SERVICE_NAMES:
            try:
                stop_owned(processes, name)
            except Exception:
                failed.append(name)
        if failed:
            note = "admission cleanup failed: " + ", ".join(failed)
            if failure is not None:
                failure.add_note(note)
            else:
                raise AssertionError(note)


def qualify_unmanaged_authority(
    bundle, paths, scope, profile, model, processes, source_environment, sensitive, initial_inventory,
):
    """Keep one real OJ process through positive/negative/positive admission."""
    from zpm.deployment import _command, _environment, verify
    from zpm.store import Store

    configuration = json.loads((bundle / "manifest.json").read_text())["configuration"]
    require(configuration["schema_version"] == 1 and configuration["max_workers"] == 1,
            "admission phase requires the existing singleton model trial")
    require("installation" not in processes.running, "admission phase requires the manager to be reaped")
    commands = {name: _command(bundle, name, configuration) for name in paths}
    with patch.dict(os.environ, source_environment, clear=True):
        environments = {name: _environment(bundle, name, configuration) for name in paths}
    tokens = json.loads((bundle / "secrets/environment.json").read_text())
    operator = json.loads((bundle / "secrets/operator.json").read_text())
    endpoint = "http://127.0.0.1:" + str(configuration["ports"]["polyphony"])
    authority = Client(endpoint, tokens["POLYPHONY_RUNTIME_TOKEN"])
    ingress = Client(endpoint, operator["ingress"])
    runtime = Client("http://127.0.0.1:" + str(configuration["ports"]["openjules"]),
                     tokens["OPENJULES_WORKER_TOKEN"])
    probe_token = owner_probe_token(bundle, operator, sensitive)
    probe = Client(endpoint, probe_token)
    unsupported_command = no_policy_command(commands["polyphony"])
    require(all(probe_token not in canonical(value) for value in (commands, environments)),
            "admission probe credential entered a service command or environment")
    sealed_names = (
        "manifest.json", "config/openjules.json", "config/polyphony.json", "config/provider.json",
        "secrets/polyphony.json", "secrets/environment.json", "secrets/operator.json",
    )
    sealed = {name: (bundle / name).read_bytes() for name in sealed_names}
    state = Path(_argument(commands["zpm"], "--state"))

    def target_zero():
        require(stat.S_ISREG(state.lstat().st_mode), "admission cannot restore a missing controller journal")
        with Store(state, scope["deployment_id"]) as store:
            store.change(scope["repository"], "target", 0)

    def start_authority(command):
        return processes.start("admission-authority", command, paths["polyphony"], environments["polyphony"])

    query = scope | {"profile": profile}
    with cleanup_phase(processes, target_zero):
        start_authority(commands["polyphony"])
        eventually(lambda: authority.request("GET", "/managed/capabilities", query=query),
                   processes, "admission original authority readiness", 15)
        runtime_process = processes.start("admission-openjules", commands["openjules"],
                                          paths["openjules"], environments["openjules"])
        runtime_pid = runtime_process.pid

        def ready_capabilities():
            value = runtime.request("GET", "/api/workers/capabilities", query=query)
            return value if value.get("managed_dispatch") is True else None

        initial_capabilities = eventually(ready_capabilities, processes, "admission real Docker/model readiness", 30)
        require_runtime_capabilities(initial_capabilities, scope, profile, True)
        require_empty_snapshot(authority.request("GET", "/managed/snapshot", query=scope), scope)
        initial_plan = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                           "admission-positive-before")
        require_controller_plan(initial_plan, scope, profile, 0, True)
        baseline = read_histories(bundle, configuration, scope, profile)
        inventory = initial_inventory
        require_idle_resources(model, inventory)

        def unchanged():
            processes.check()
            require(processes.running.get("admission-openjules") is runtime_process
                    and runtime_process.pid == runtime_pid and runtime_process.poll() is None,
                    "admission runtime restarted across the authority transition")
            read_histories(bundle, configuration, scope, profile, baseline)
            require_idle_resources(model, inventory)
            require(runtime.request("GET", "/api/workers", query=scope)
                    == {"schema_version": 1, "workers": []},
                    "admission runtime exposed an unexpected worker")

        unchanged()
        stop_owned(processes, "admission-authority")
        negative_process = start_authority(unsupported_command)
        live_snapshot = eventually(lambda: probe.request("GET", "/snapshot"),
                                   processes, "admission responsive no-policy authority", 15)
        require_empty_snapshot(live_snapshot)
        require(expected_error(authority, "GET", "/managed/capabilities", query=query, text=True)
                == AUTHORITY_REASON, "admission authority failed for a different reason")
        negative_capabilities = runtime.request("GET", "/api/workers/capabilities", query=query)
        require_runtime_capabilities(negative_capabilities, scope, profile, False)
        unchanged()
        with Store(state, scope["deployment_id"]) as store:
            store.change(scope["repository"], "target", 1)
        blocked = []
        for number in (1, 2):
            value = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                        "admission-blocked-" + str(number))
            row = require_controller_plan(value, scope, profile, 1, False)
            require(negative_process.poll() is None, "admission no-policy authority exited")
            require_empty_snapshot(probe.request("GET", "/snapshot"))
            require_runtime_capabilities(runtime.request("GET", "/api/workers/capabilities", query=query),
                                         scope, profile, False)
            unchanged()
            blocked.append({key: row[key] for key in ("desired_workers", "effective_target",
                                                       "healthy", "reserved", "shortfall")})
        spec = {"schema_version": 1, **scope, "slot_id": "admission-probe", "generation": 1,
                "launch_key": "admission-probe-" + secrets.token_hex(16), "profile": profile}
        for _ in range(2):
            require(expected_error(runtime, "POST", "/api/workers/ensure", data=spec)
                    == {"detail": "Managed runtime preflight is not ready"},
                    "admission ensure did not reject current authority incompatibility")
            unchanged()
        enrollment = {
            "schema_version": 1, **scope, "source": "github:issue:admission-probe",
            "request_key": "admission-probe", "title": "Admission must remain closed",
            "requirements": "No protected execution is authorized by this unavailable policy.",
        }
        require(expected_error(ingress, "POST", "/managed/enroll", data=enrollment, text=True)
                == AUTHORITY_REASON, "admission enrollment did not reject missing policy")
        require_empty_snapshot(probe.request("GET", "/snapshot"))
        unchanged()

        target_zero()
        stop_owned(processes, "admission-authority")
        start_authority(commands["polyphony"])
        eventually(lambda: authority.request("GET", "/managed/capabilities", query=query),
                   processes, "admission restored authority readiness", 15)
        require_runtime_capabilities(eventually(ready_capabilities, processes, "admission restored runtime readiness", 30),
                                     scope, profile, True)
        restored = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                       "admission-positive-after")
        require_controller_plan(restored, scope, profile, 0, True)
        require_empty_snapshot(authority.request("GET", "/managed/snapshot", query=scope), scope)
        unchanged()
        result = {
            "scenario": "responsive current coordinator without managed policy",
            "legacy_snapshot_status": 200, "managed_capabilities_status": 503,
            "authority_reason": AUTHORITY_REASON, "runtime_reason": RUNTIME_REASON,
            "controller_reason": CONTROLLER_REASON, "blocked_controller_passes": blocked,
            "ensure_rejections": 2, "enrollment_rejected": True, "same_runtime_process": True,
            "protected_history_sha256": hashlib.sha256(canonical(baseline).encode()).hexdigest(),
            "protected_rows": 0, "model_calls": 0,
            "docker_inventory_counts": {key: len(value) for key, value in inventory.items()},
            "original_authority_recovered": True,
            "owner_probe": "existing generated owner used only for the host-side legacy snapshot",
        }
    require(sealed == {name: (bundle / name).read_bytes() for name in sealed_names},
            "admission phase changed the sealed installation or credentials")
    verify(bundle)
    return result
