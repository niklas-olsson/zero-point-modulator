"""The installed runtime refuses an older authority missing required outcomes.

Both services are real installed processes. Only the existing model responses
are fixtures. The old source is a separate immutable checkout; no service or
capability response is substituted, and no populated-state downgrade is tested.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
from unittest.mock import patch

from check_authority_admission import (
    EMPTY_TABLES, SCHEMAS, _argument,
    canonical, cleanup_phase, completed_reconcile, docker_inventory, expected_error,
    journal_paths, read_tables, require_controller_plan, require_empty_snapshot,
    require_idle_resources, require_runtime_capabilities, stop_owned,
)
from check_stack import Client, eventually, require


RUNTIME_REASON = "Canonical managed authority is unsupported: child_initial_outcome=1"
CONTROLLER_REASON = RUNTIME_REASON
HISTORICAL_REVISION = "473df5a0406accfe5cb550877e7b28a602cb116c"
HISTORICAL_TREE = "389ec80cb6a99edf2993d59e4540f6bdeaaa5f18"
CURRENT_REVISION = "653c0a71a921c0d23b8e0171277524125e929e66"
CURRENT_TREE = "3e7565d387414e5166d29948fe4f13c2c57e61b1"
MODULES = (
    "src.liminal_bridge.coordinator_service",
    "src.liminal_bridge.managed_service",
    "src.core.collaboration.authority",
    "src.core.collaboration.managed_runtime",
    "src.core.collaboration.child_grants",
)
OUTCOME_MODULE = "src.core.collaboration.child_outcomes"
CHILD_SCHEMA = {
    "workers": ("worker_id", "spec", "policy_digest", "context", "closed", "close_reason",
                "close_delivered", "retire"),
    "intents": ("worker_id", "request_id", "payload", "delivery_key", "child_id", "status"),
    "children": ("child_id", "worker_id", "ordinal", "uid", "grant_json", "deadline", "boot_id",
                 "phase", "exec_id", "exec_dispatched", "armed_deadline", "exit_code", "revoked",
                 "provisional", "capture", "artifacts", "outcome_report"),
}
IMPORT_PROBE = """import hashlib,importlib.util,json,os,pathlib,sys
modules={}
for name in json.loads(sys.argv[1]):
    spec=importlib.util.find_spec(name)
    if spec is None:
        modules[name]=None
    else:
        path=pathlib.Path(spec.origin)
        modules[name]={'origin':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
print(json.dumps({'cwd':os.getcwd(),'executable':sys.executable,'modules':modules},sort_keys=True))
"""


def _git(checkout, *arguments):
    try:
        result = subprocess.run(["git", "-C", str(checkout), *arguments], capture_output=True,
                                text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        raise AssertionError("historical authority source could not be verified") from None
    require(len(result.stdout.encode()) <= 65536, "historical source evidence exceeded its bound")
    return result.stdout.strip()


def _pinned_modules(checkout, revision, names):
    """Bind imported source bytes to Git objects, even when index flags hide edits."""
    result = {}
    for name in names:
        relative = "/".join(name.split(".")) + ".py"
        expected = Path(checkout) / relative
        try:
            regular = stat.S_ISREG(expected.lstat().st_mode)
            working = expected.read_bytes() if regular else None
        except OSError:
            raise AssertionError("historical authority pinned module is unavailable") from None
        require(regular, "historical authority module is not a regular tracked source file")
        try:
            response = subprocess.run(
                ["git", "-C", str(checkout), "cat-file", "blob", revision + ":" + relative],
                capture_output=True, timeout=15, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            raise AssertionError("historical authority pinned module is unavailable") from None
        require(0 < len(response.stdout) <= 262144 and working == response.stdout,
                "historical authority module bytes differ from the pinned Git blob")
        result[name] = response.stdout
    return result


def verify_source(checkout, revision, tree):
    checkout = Path(checkout)
    require(checkout.is_absolute() and checkout.resolve(strict=True) == checkout,
            "historical authority checkout is not an exact absolute directory")
    require(_git(checkout, "rev-parse", "--show-toplevel") == str(checkout)
            and _git(checkout, "rev-parse", "HEAD") == revision
            and _git(checkout, "rev-parse", "HEAD^{tree}") == tree,
            "historical authority checkout changed its approved Git identity")
    require(_git(checkout, "status", "--porcelain", "--untracked-files=all") == "",
            "historical authority checkout has unapproved source changes")
    return {"revision": revision, "tree": tree}


def distinct_sources(current, historical, bundle):
    paths = [Path(value).resolve(strict=True) for value in (current, historical, bundle)]
    require(all(left != right and left not in right.parents and right not in left.parents
                for index, left in enumerate(paths) for right in paths[index + 1:]),
            "historical authority source aliases an approved source or installation")


def source_provenance(checkout, interpreter, environment, revision, tree, *, outcomes):
    identity = verify_source(checkout, revision, tree)
    require("PYTHONPATH" not in environment and "PYTHONHOME" not in environment
            and environment.get("PYTHONNOUSERSITE") == "1",
            "historical authority environment changes Python source selection")
    modules = (*MODULES, OUTCOME_MODULE)
    pinned = _pinned_modules(checkout, revision, modules if outcomes else MODULES)
    try:
        result = subprocess.run(
            [str(interpreter), "-c", IMPORT_PROBE, json.dumps(modules)],
            cwd=checkout, env=environment, capture_output=True, text=True, timeout=15,
        )
        require(result.returncode == 0 and 0 < len(result.stdout.encode()) <= 32768,
                "historical authority import probe did not complete")
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError):
        raise AssertionError("historical authority import provenance is unavailable") from None
    require(isinstance(value, dict) and set(value) == {"cwd", "executable", "modules"}
            and value["cwd"] == str(checkout)
            and Path(value["executable"]).absolute() == Path(interpreter).absolute()
            and isinstance(value["modules"], dict) and set(value["modules"]) == set(modules),
            "historical authority import probe changed its interpreter or source")
    digests = {}
    for name in modules:
        entry = value["modules"][name]
        if name == OUTCOME_MODULE and not outcomes:
            require(entry is None, "historical authority unexpectedly imports the newer outcome implementation")
            continue
        expected = Path(checkout).joinpath(*name.split(".")).with_suffix(".py")
        require(isinstance(entry, dict) and set(entry) == {"origin", "sha256"}
                and entry["origin"] == str(expected)
                and stat.S_ISREG(expected.lstat().st_mode),
                "historical authority imported a module outside its verified checkout")
        digest = hashlib.sha256(pinned[name]).hexdigest()
        require(expected.read_bytes() == pinned[name] and entry["sha256"] == digest,
                "historical authority imported different pinned source bytes")
        digests[name] = digest
    verify_source(checkout, revision, tree)
    return identity | {"module_sha256": digests, "initial_outcome_module_present": outcomes}


def require_current_capabilities(value, scope, profile, canonical_profile):
    required = {
        "schema_version", "deployment_id", "repository", "service", "protocol", "profile",
        "base", "profile_digest", "attempt_seconds", "capabilities", "managed_dispatch",
        "external_root", "children", "child_grants", "child_initial_outcome",
        "child_policy", "profile_supported", "reason",
    }
    require(isinstance(value, dict) and set(value) == required
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and all(value[key] == item for key, item in scope.items())
            and value["service"] == "keystone-polyphony" and value["protocol"] == "managed-runtime-v1"
            and value["profile"] == profile and value["reason"] == ""
            and value["profile_supported"] is True and value["managed_dispatch"] is True
            and value["external_root"] is True and value["child_grants"] is True
            and value["children"] is False
            and type(value["child_initial_outcome"]) is int and value["child_initial_outcome"] == 1,
            "historical admission lacks a complete current-authority positive control")
    selected = {key: value[key] for key in ("base", "attempt_seconds", "capabilities")}
    expected = {key: canonical_profile[key] for key in selected}
    child = {key: canonical_profile[key] for key in
             ("max_children", "max_child_seconds", "max_child_turns", "child_capabilities")}
    require(canonical(selected) == canonical(expected) and canonical(value["child_policy"]) == canonical(child)
            and value["profile_digest"] == hashlib.sha256(canonical(canonical_profile).encode()).hexdigest(),
            "historical admission authority differs from its sealed approved policy")
    return value


def require_historical_capabilities(value, current):
    require(isinstance(value, dict) and "child_initial_outcome" not in value
            and canonical(value) == canonical({key: item for key, item in current.items()
                                               if key != "child_initial_outcome"}),
            "historical authority changed more than the required missing capability")


def require_child_runtime(value, scope, profile, ready):
    require_runtime_capabilities(value, scope, profile, ready, unavailable_reason=RUNTIME_REASON)
    require(value.get("children") is ready,
            "historical admission changed child readiness for a different reason")


def historical_histories(bundle, configuration, scope, profile, expected=None):
    paths = journal_paths(bundle, configuration)
    config = json.loads((bundle / "config/openjules.json").read_text())
    state = Path(config["state_directory"])
    children = state / "children/children.sqlite3"
    require(children.is_absolute() and bundle.resolve() in children.resolve().parents
            and children not in paths.values(), "historical child journal leaves or aliases the bundle")
    require(len(config.get("children", [])) == 1
            and config["children"][0]["deployment_id"] == scope["deployment_id"]
            and config["children"][0]["repository"] == scope["repository"]
            and config["children"][0]["profile"] == profile
            and config["children"][0]["policy"].get("outcome_protocol") == "initial-v1",
            "historical admission requires the sealed initial-outcome profile")
    result = {name: read_tables(paths[name], schema) for name, schema in SCHEMAS.items()}
    result["children"] = read_tables(children, CHILD_SCHEMA)
    for name, tables in EMPTY_TABLES.items():
        require(all(result[name][table] == [] for table in tables),
                "historical admission created protected or execution history")
    require(all(rows == [] for rows in result["children"].values()),
            "historical admission created child history")
    require(result["controller"]["metadata"] == [["deployment_id", scope["deployment_id"]]],
            "historical admission changed controller identity")
    key = [scope["deployment_id"], scope["repository"], profile]
    for table in ("profiles", "child_profiles"):
        rows = result["runtime"][table]
        require(len(rows) == 1 and rows[0][:3] == key and isinstance(rows[0][3], str)
                and len(rows[0][3]) == 64 and all(char in "0123456789abcdef" for char in rows[0][3]),
                "historical admission lacks the original enabled runtime or child profile")
    policies = result["model"]["policies"]
    require(len(policies) == 1 and policies[0][0] == canonical(key)
            and isinstance(policies[0][1], str) and len(policies[0][1]) == 64
            and all(char in "0123456789abcdef" for char in policies[0][1]),
            "historical admission changed the model policy")
    archive = state / "runtime/captures"
    require(stat.S_ISDIR(archive.lstat().st_mode) and not any(archive.iterdir()),
            "historical admission created or lost its empty runtime capture directory")
    if expected is not None:
        require(canonical(result) == canonical(expected), "historical admission changed retained policies or identities")
    return result


def require_same_runtime(processes, process, pid):
    require(processes.running.get("admission-openjules") is process
            and process.pid == pid and process.poll() is None,
            "historical admission restarted or lost its original runtime process")


def qualify_historical_authority(
    bundle, paths, historical_checkout, scope, profile, model, processes, source_environment,
):
    """Current -> genuinely older -> current authority, with one unchanged OJ."""
    from zpm.deployment import _command, _environment, verify
    from zpm.store import Store

    verify(bundle)
    configuration = json.loads((bundle / "manifest.json").read_text())["configuration"]
    require(configuration["schema_version"] == 1 and configuration["max_workers"] == 1
            and configuration["sources"]["polyphony"]["revision"] == CURRENT_REVISION,
            "historical admission requires the reviewed singleton component set")
    require(not processes.running, "historical admission requires no pre-existing owned service")
    distinct_sources(paths["polyphony"], historical_checkout, bundle)
    commands = {name: _command(bundle, name, configuration) for name in paths}
    require(commands["polyphony"][1:3] == ["-m", "src.liminal_bridge.coordinator_service"]
            and "--execution-config" not in commands["polyphony"],
            "historical admission requires the generated non-executing coordinator")
    with patch.dict(os.environ, source_environment, clear=True):
        environments = {name: _environment(bundle, name, configuration) for name in paths}
    tokens = json.loads((bundle / "secrets/environment.json").read_text())
    authority = Client("http://127.0.0.1:" + str(configuration["ports"]["polyphony"]),
                       tokens["POLYPHONY_RUNTIME_TOKEN"])
    runtime = Client("http://127.0.0.1:" + str(configuration["ports"]["openjules"]),
                     tokens["OPENJULES_WORKER_TOKEN"])
    canonical_profile = json.loads((bundle / "config/polyphony.json").read_text())["repositories"][scope["repository"]]["profiles"][profile]
    names = ("manifest.json", "config/openjules.json", "config/polyphony.json", "config/provider.json",
             "secrets/polyphony.json", "secrets/environment.json", "secrets/operator.json")
    sealed = {name: (bundle / name).read_bytes() for name in names}
    state = Path(_argument(commands["zpm"], "--state"))
    query = scope | {"profile": profile}
    inventory = docker_inventory()
    source_evidence = {}

    def target_zero():
        require(stat.S_ISREG(state.lstat().st_mode), "historical admission cannot restore a missing journal")
        with Store(state, scope["deployment_id"]) as store:
            store.change(scope["repository"], "target", 0)

    def provenance(checkout, revision, tree, outcomes):
        return source_provenance(checkout, commands["polyphony"][0], environments["polyphony"],
                                 revision, tree, outcomes=outcomes)

    def start_authority(checkout):
        return processes.start("admission-authority", commands["polyphony"], checkout, environments["polyphony"])

    def stop_authority(process):
        require(processes.running.get("admission-authority") is process,
                "historical admission lost ownership of the authority process")
        stop_owned(processes, "admission-authority")
        require(process.poll() is not None and "admission-authority" not in processes.running,
                "historical admission authority was not reaped")

    def current_capabilities():
        value = authority.request("GET", "/managed/capabilities", query=query)
        return require_current_capabilities(value, scope, profile, canonical_profile)

    def ready_runtime():
        value = runtime.request("GET", "/api/workers/capabilities", query=query)
        return value if value.get("managed_dispatch") is True else None

    with cleanup_phase(processes, target_zero):
        source_evidence["current_before"] = provenance(paths["polyphony"], CURRENT_REVISION, CURRENT_TREE, True)
        current_process = start_authority(paths["polyphony"])
        current = eventually(current_capabilities, processes, "historical phase current authority", 15)
        runtime_process = processes.start("admission-openjules", commands["openjules"],
                                          paths["openjules"], environments["openjules"])
        runtime_pid = runtime_process.pid
        require_child_runtime(eventually(ready_runtime, processes, "historical phase real child preflight", 30),
                              scope, profile, True)
        before = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                     "historical-positive-before")
        require_controller_plan(before, scope, profile, 0, True)
        require_empty_snapshot(authority.request("GET", "/managed/snapshot", query=scope), scope)
        baseline = historical_histories(bundle, configuration, scope, profile)
        require_idle_resources(model, inventory)

        def unchanged():
            processes.check()
            require_same_runtime(processes, runtime_process, runtime_pid)
            historical_histories(bundle, configuration, scope, profile, baseline)
            require_idle_resources(model, inventory)
            require_empty_snapshot(authority.request("GET", "/managed/snapshot", query=scope), scope)
            require(runtime.request("GET", "/api/workers", query=scope) == {"schema_version": 1, "workers": []},
                    "historical admission exposed an unexpected runtime worker")

        unchanged()
        stop_authority(current_process)
        source_evidence["historical"] = provenance(historical_checkout, HISTORICAL_REVISION, HISTORICAL_TREE, False)
        older_process = start_authority(historical_checkout)

        def older_capabilities():
            value = authority.request("GET", "/managed/capabilities", query=query)
            require_historical_capabilities(value, current)
            return value

        eventually(older_capabilities, processes, "historical responsive authority without outcome capability", 15)
        require_child_runtime(runtime.request("GET", "/api/workers/capabilities", query=query), scope, profile, False)
        unchanged()
        with Store(state, scope["deployment_id"]) as store:
            store.change(scope["repository"], "target", 1)
        blocked = []
        for number in (1, 2):
            value = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                        "historical-blocked-" + str(number))
            row = require_controller_plan(value, scope, profile, 1, False, unavailable_reason=CONTROLLER_REASON)
            require(older_process.poll() is None, "historical authority exited during the denied phase")
            older_capabilities()
            require_child_runtime(runtime.request("GET", "/api/workers/capabilities", query=query),
                                  scope, profile, False)
            unchanged()
            blocked.append({key: row[key] for key in ("desired_workers", "effective_target", "healthy", "reserved", "shortfall")})
        spec = {"schema_version": 1, **scope, "slot_id": "historical-admission-probe", "generation": 1,
                "launch_key": "historical-admission-" + secrets.token_hex(16), "profile": profile}
        for _ in range(2):
            require(expected_error(runtime, "POST", "/api/workers/ensure", data=spec)
                    == {"detail": "Managed runtime preflight is not ready"},
                    "historical authority incompatibility did not reject ensure")
            older_capabilities()
            require_child_runtime(runtime.request("GET", "/api/workers/capabilities", query=query),
                                  scope, profile, False)
            unchanged()

        target_zero()
        stop_authority(older_process)
        verify_source(historical_checkout, HISTORICAL_REVISION, HISTORICAL_TREE)
        source_evidence["current_after"] = provenance(paths["polyphony"], CURRENT_REVISION, CURRENT_TREE, True)
        require(source_evidence["current_after"] == source_evidence["current_before"],
                "historical phase changed the restored authority source")
        restored_process = start_authority(paths["polyphony"])
        restored_capabilities = eventually(current_capabilities, processes, "historical current authority restored", 15)
        require(canonical(restored_capabilities) == canonical(current), "restored authority capability changed")
        require_child_runtime(eventually(ready_runtime, processes, "historical runtime readiness restored", 30),
                              scope, profile, True)
        after = completed_reconcile(processes, commands["zpm"], paths["zpm"], environments["zpm"],
                                    "historical-positive-after")
        require_controller_plan(after, scope, profile, 0, True)
        require(restored_process.poll() is None, "restored historical-phase authority exited")
        unchanged()
        result = {
            "scenario": "actual prior authority revision missing required initial child outcome capability",
            "historical_revision": HISTORICAL_REVISION, "historical_tree": HISTORICAL_TREE,
            "current_authority_revision": CURRENT_REVISION, "current_authority_tree": CURRENT_TREE,
            "managed_capabilities_status": 200, "missing_capability": "child_initial_outcome",
            "same_scope_base_and_child_policy": True, "same_runtime_process": True,
            "source_provenance": source_evidence,
            "runtime_reason": RUNTIME_REASON, "controller_reason": CONTROLLER_REASON,
            "blocked_controller_passes": blocked, "ensure_rejections": 2, "model_calls": 0,
            "protected_rows": 0, "runtime_archives": 0,
            "protected_history_sha256": hashlib.sha256(canonical(baseline).encode()).hexdigest(),
            "docker_inventory_counts": {key: len(value) for key, value in inventory.items()},
            "current_authority_recovered": True, "restored_target": 0,
            "not_proven": ["populated-state downgrade compatibility", "missing core protocol",
                           "literal health endpoint compatibility", "live provider behavior"],
        }
    require(sealed == {name: (bundle / name).read_bytes() for name in names},
            "historical admission changed the sealed installation or credentials")
    verify(bundle)
    return result
