"""Qualify two repositories in one generated installation on real Docker.

One controller, OpenJules API and Polyphony authority share a deployment.
Controlled commands use private fixture barriers and make no model calls.
Only the aggregate report and bounded redacted diagnostics may be exported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import time

from check_stack import (
    Client, Processes, clean_environment, diagnostics, eventually, port,
    private_json, require, wait_for_capacity,
)
from check_isolation_stack import (
    Installation, TASK_PROGRAM, canonical, checkpoint_progress, container_for,
    elapsed, live_ids, progress, read_journal, release, require_active_runtime,
    require_controller_drain, require_live_container, require_peer_runtime,
    require_peer_unchanged, require_pending_drain_journals,
    require_pending_drain_runtime, require_removed_plan,
    require_uncontrolled_journals, supplied_environment, worker_binding,
    worker_journals,
)


TOPOLOGY = "one schema-2 installation, two repositories, one controller and service pair, one Docker Engine"
SOURCE_UNAVAILABLE = "approved source/base or local Docker image is unavailable"
SOURCE_READINESS = "repository_access unavailable"
LIMITS = [
    "survival of a shared service, authority, Docker or host outage",
    "cold restart while any approved source is unavailable",
    "multiple deployments behind one OpenJules API",
    "mutually hostile host services with Docker authority",
    "live repository addition, schema migration or profile/pin upgrades",
    "wall-clock fairness or workload performance guarantees",
    "live model inference", "independent semantic review", "forge publication",
]
# Check the schema-2 host aliases before starting any task progress. The
# existing command also verifies its positive guest target, source boundaries,
# foreign canary, control-token names and private host paths.
SHARED_TASK_PROGRAM = (
    'import os\n'
    'assert not any(name.startswith("ZPM_REPOSITORY_") for name in os.environ)\n'
    + TASK_PROGRAM
)


def require_shared_plan(plan, deployment, repositories, maximum=2):
    """Validate the complete topology before selecting a repository view."""
    require(type(plan.get("schema_version")) is int and plan["schema_version"] == 1,
            "shared plan has an invalid schema")
    require(plan.get("deployment_id") == deployment, "shared deployment identity changed")
    require(type(plan.get("max_workers")) is int and plan["max_workers"] == maximum,
            "shared plan lacks the configured deployment ceiling")
    require(type(plan.get("reserved")) is int and 0 <= plan["reserved"] <= maximum,
            "shared plan exceeded or lost global reservation accounting")
    require(type(plan.get("over_capacity")) is int and plan["over_capacity"] == 0,
            "shared plan has invalid over-capacity evidence")
    rows = plan.get("repositories")
    require(isinstance(rows, list) and len(rows) == len(repositories),
            "shared plan has missing or extra repository rows")
    names = [row.get("repository") for row in rows]
    require(len(set(names)) == len(names) and set(names) == set(repositories),
            "shared plan contains a duplicate or foreign repository")
    known_workers = []
    for row in rows:
        for name in ("desired_workers", "effective_target", "permitted_target", "healthy",
                     "reserved", "shortfall", "launch_deficit"):
            require(type(row.get(name)) is int and row[name] >= 0,
                    "shared repository has invalid capacity counters")
        workers = row.get("workers")
        require(isinstance(workers, list) and len(workers) == row["reserved"],
                "shared repository reservation and worker counts differ")
        require(row["healthy"] <= row["reserved"], "healthy count exceeds reservations")
        for worker in workers:
            require(worker.get("deployment_id") == deployment
                    and worker.get("repository") == row["repository"],
                    "shared plan contains a foreign worker scope")
            identity = worker.get("worker_id")
            if identity:
                require(isinstance(identity, str), "shared worker identity is invalid")
                known_workers.append(identity)
    require(len(known_workers) == len(set(known_workers)), "repositories share a worker identity")
    require(sum(row["reserved"] for row in rows) == plan["reserved"],
            "shared plan global total does not equal repository reservations")
    return {row["repository"]: row for row in rows}


def repository_view(plan, deployment, repositories, repository):
    rows = require_shared_plan(plan, deployment, repositories)
    require(repository in rows, "requested repository is absent from the shared plan")
    # Internal adapter for the existing strict one-repository witnesses. The
    # original full plan was checked above; this view is never a public report.
    return {**plan, "repositories": [rows[repository]], "reserved": rows[repository]["reserved"]}


def require_scope_capability(value, scope, profile, *, source_available=True, authority=False):
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1
            and all(value.get(key) == expected for key, expected in scope.items())
            and value.get("profile") == profile, "capability response changed its scope or profile")
    require(value.get("profile_supported") is True, "approved profile is unsupported")
    if source_available:
        require(value.get("managed_dispatch") is True, "scoped managed dispatch is unavailable")
        if authority:
            require(value.get("service") == "keystone-polyphony"
                    and value.get("protocol") == "managed-runtime-v1" and value.get("external_root") is True,
                    "canonical authority capability contract is unavailable")
        else:
            require(value.get("repository_access") is True, "scoped repository access is unavailable")
    else:
        require(value.get("managed_dispatch") is False and value.get("repository_access") is False
                and value.get("reason") == SOURCE_UNAVAILABLE,
                "source fault lacks the exact scoped readiness failure")


def require_source_block(plan, deployment, left, right):
    rows = require_shared_plan(plan, deployment, (left, right))
    a, b = rows[left], rows[right]
    require(plan["reserved"] == 1 and a["reserved"] == 0 and b["reserved"] == 1,
            "source-failure window lacks genuine free global capacity")
    require(a["desired_workers"] == a["effective_target"] == a["permitted_target"] == 1
            and a["launch_deficit"] == a["shortfall"] == 1,
            "source-failure window does not retain A's unmet target")
    require(a["validation"].get("ready") is False
            and a["validation"].get("reason") == SOURCE_READINESS,
            "completed controller pass did not retain A's source failure")
    require("deployment capacity ceiling" not in a["reasons"]
            and "repository capacity ceiling" not in a["reasons"],
            "capacity masked the source failure")


def require_quota(plan, deployment, left, right):
    rows = require_shared_plan(plan, deployment, (left, right))
    a, b = rows[left], rows[right]
    require(plan["reserved"] == 2 and a["reserved"] == b["reserved"] == 1,
            "quota window does not contain exactly A1 and B1")
    require(a["desired_workers"] == a["effective_target"] == a["permitted_target"] == 2
            and a["healthy"] == a["shortfall"] == a["launch_deficit"] == 1,
            "quota window did not preserve A's explicit one-worker shortfall")
    require(a["validation"].get("ready") is True
            and "deployment capacity ceiling" in a["reasons"]
            and "repository capacity ceiling" not in a["reasons"],
            "global ceiling is not the demonstrated allocation blocker")
    worker = a["workers"][0]
    require(worker["state"] == "ready" and worker["admission_open"] is True
            and worker["active_executions"] == [] and "pending_operation" in worker
            and worker["pending_operation"] is None,
            "quota window requires a stable idle A worker")


def require_same_history(before, after):
    require(canonical(before) == canonical(after),
            "held no-allocation window changed retained launch or runtime history")


def retained_history(bundle, deployment, repositories, selected):
    """Read every retained row before selecting scope; never create a journal."""
    allowed = set(repositories)
    require(selected in allowed, "history selection is outside the installation")
    config = json.loads((bundle / "config/openjules.json").read_text())
    result = {"zpm": [], "lifecycle": [], "runtime": []}
    with read_journal(bundle / "state/zpm.sqlite3") as database:
        metadata = database.execute("SELECT value FROM metadata WHERE key='deployment_id'").fetchone()
        require(metadata is not None and metadata["value"] == deployment, "history deployment changed")
        rows = database.execute(
            "SELECT launch_key,repository,slot_id,generation,profile,snapshot FROM workers "
            "ORDER BY repository,slot_id,generation").fetchall()
        for row in rows:
            require(row["repository"] in allowed, "controller history contains a foreign repository")
            snapshot = json.loads(row["snapshot"])
            require(snapshot["deployment_id"] == deployment and snapshot["repository"] == row["repository"]
                    and all(snapshot.get(key) == row[key] for key in
                            ("launch_key", "slot_id", "generation", "profile")),
                    "controller history changed its retained worker binding")
            if row["repository"] == selected:
                result["zpm"].append({key: row[key] for key in
                                      ("launch_key", "repository", "slot_id", "generation", "profile")}
                                     | {"worker_id": snapshot["worker_id"]})
    locations = (
        ("lifecycle", Path(config["lifecycle_database"]), "managed_workers",
         ("deployment_id", "repository", "slot_id", "generation", "runtime_id")),
        ("runtime", Path(config["state_directory"]) / "runtime/journal.sqlite3",
         "workers", ("profile_fingerprint", "container_id")),
    )
    for kind, path, table, fields in locations:
        with read_journal(path) as database:
            # Table and field names are fixed literals above, not input.
            rows = database.execute("SELECT worker_id,spec," + ",".join(fields)
                                    + " FROM " + table + " ORDER BY worker_id").fetchall()
            for row in rows:
                spec = json.loads(row["spec"])
                require(spec.get("deployment_id") == deployment and spec.get("repository") in allowed
                        and type(spec.get("schema_version")) is int and spec["schema_version"] == 1
                        and type(spec.get("generation")) is int and spec["generation"] > 0,
                        "runtime history contains a foreign or malformed worker scope")
                if kind == "lifecycle":
                    require(all(row[key] == spec[key] for key in
                                ("deployment_id", "repository", "slot_id", "generation")),
                            "lifecycle history columns disagree with the retained specification")
                if spec["repository"] == selected:
                    result[kind].append({"worker_id": row["worker_id"], "spec": spec}
                                        | {key: row[key] for key in fields})
    return result


class Repository(Installation):
    """Repository view supplying the existing capture/read witness interface.

    Shared installation lifecycle is owned by SharedInstallation. This adapter
    uses only Installation.snapshot, attempt and its reviewed captured method.
    """

    def __init__(self, shared, fixture):
        self.shared = shared
        self.label, self.source, self.base = (fixture[key] for key in ("label", "source", "base"))
        self.repository, self.profile = fixture["repository"], fixture["profile"]
        self.deployment, self.bundle, self.state = shared.deployment, shared.bundle, shared.state
        self.scope = {"deployment_id": self.deployment, "repository": self.repository}
        self.authority, self.runtime = shared.authority, shared.runtime
        self.authority_endpoint, self.runtime_endpoint = shared.authority_endpoint, shared.runtime_endpoint
        self.tokens = shared.operator["repositories"][self.repository]
        self.ingress = Client(self.authority_endpoint, self.tokens["ingress"])
        self.reviewer = Client(self.authority_endpoint, self.tokens["reviewer"])
        self.sensitive = shared.sensitive

    def plan(self):
        return repository_view(self.shared.plan(), self.deployment, self.shared.repositories, self.repository)

    def enroll(self):
        request = {"schema_version": 1, **self.scope, "source": "github:issue:1",
                   "request_key": self.tokens["request_key_prefix"] + "controlled-task-1",
                   "title": "Shared repository " + self.label,
                   "requirements": "Verify this repository and change answer.txt. HOLD_FOR_ISOLATION_TRIAL"}
        result = self.ingress.request("POST", "/managed/enroll", data=request)
        require(self.ingress.request("POST", "/managed/enroll", data=request)["task"]["id"] == result["task"]["id"],
                "source replay created a duplicate task")
        return result["task"]["id"], request


class SharedInstallation:
    def __init__(self, root, source_paths, revisions, image, ports):
        from zpm.deployment import _command, _environment, prepare, verify

        self.root, self.source_paths = root, source_paths
        self.bundle = root / "installation"
        self.deployment = "multi-repository-" + secrets.token_hex(8)
        self.state = self.bundle / "state/zpm.sqlite3"
        self.processes = Processes(root)
        self.observed_seconds, self.command_number = {}, 0
        self.sensitive = set()
        canaries = ["multiple-" + secrets.token_urlsafe(32) for _ in range(2)]
        fixtures, entries, supplied = [], [], {}
        for index, label in enumerate(("a", "b")):
            source = root / ("source-" + label)
            source.mkdir(mode=0o755)
            for name, content in {
                "identity.txt": label + "\n", label + "-only.txt": "source " + label + "\n",
                "answer.txt": "initial " + label + "\n", "run_trial.py": SHARED_TASK_PROGRAM,
                ".gitignore": ".env\n.isolation-*\n",
            }.items():
                (source / name).write_text(content)
            for command in (["init", "-q"], ["add", "."],
                            ["-c", "user.name=Multiple Repository Trial", "-c", "user.email=trial@example.invalid",
                             "commit", "-qm", "controlled repository " + label]):
                subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True, timeout=15)
            host_canary = secrets.token_urlsafe(32)
            (source / ".env").write_text("HOST_ONLY_ISOLATION_CANARY=" + host_canary + "\n")
            base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
            repository = "github:github.com/managed-multiple/repository-" + label
            profile = "controlled-multiple-" + label
            reference = "ZPM_ISOLATION_" + label.upper() + "_" + secrets.token_hex(8).upper()
            supplied[reference] = canaries[index]
            self.sensitive.update((canaries[index], host_canary))
            fixtures.append({"label": label, "source": source, "base": base,
                             "repository": repository, "profile": profile})
            entries.append({
                "binding": {"schema_version": 1, "repository": repository, "profile": profile,
                            "desired_workers": 0, "max_workers": 2},
                "source": {"checkout": str(source), "revision": base}, "image": image,
                "command": ["python3", "-I", "-S", "run_trial.py", label,
                            hashlib.sha256(canaries[index].encode()).hexdigest(),
                            hashlib.sha256(canaries[1 - index].encode()).hexdigest(), str(self.bundle)],
                "runtime": {"max_seconds": 300, "pids_limit": 64},
                "environment_refs": {"OPENAI_API_KEY": "env:" + reference},
                "policy": {"checks": ["controlled-multiple"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 400, "attempt_seconds": 270, "capabilities": ["python"]},
            })
        self.input_data = {
            "schema_version": 2, "deployment_id": self.deployment, "max_workers": 2,
            "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in source_paths.items()},
            "ports": ports, "repositories": entries,
        }
        started = time.monotonic()
        prepare(self.input_data, self.bundle)
        self.observed_seconds["prepare"] = elapsed(started)
        verify(self.bundle)
        self.data = json.loads((self.bundle / "manifest.json").read_text())["configuration"]
        self.repositories = tuple(entry["binding"]["repository"] for entry in self.data["repositories"])
        environment_tokens = json.loads((self.bundle / "secrets/environment.json").read_text())
        self.operator = json.loads((self.bundle / "secrets/operator.json").read_text())
        self.tokens = {"runtime": environment_tokens["POLYPHONY_RUNTIME_TOKEN"],
                       "zpm": environment_tokens["OPENJULES_WORKER_TOKEN"]}
        self.sensitive.update(environment_tokens.values())
        self.sensitive.add(self.operator["owner"])
        for entry in self.operator["repositories"].values():
            self.sensitive.update((entry["ingress"], entry["reviewer"]))
        self.sealed = {str(path.relative_to(self.bundle)): path.read_bytes()
                       for folder in ("config", "secrets") for path in sorted((self.bundle / folder).iterdir())}
        self.sealed["manifest.json"] = (self.bundle / "manifest.json").read_bytes()
        self.authority_endpoint = f"http://127.0.0.1:{ports['polyphony']}"
        self.runtime_endpoint = f"http://127.0.0.1:{ports['openjules']}"
        self.authority = Client(self.authority_endpoint, self.tokens["runtime"])
        self.runtime = Client(self.runtime_endpoint, self.tokens["zpm"])
        self.commands = {name: _command(self.bundle, name, self.data) for name in source_paths}
        with supplied_environment(supplied):
            self.environments = {name: _environment(self.bundle, name, self.data) for name in source_paths}
        self.manager_environment = clean_environment(supplied)
        self.members = [Repository(self, fixture) for fixture in fixtures]
        self.maximum = json.loads((self.bundle / "config/provider.json").read_text())["max_workers"]
        require(type(self.maximum) is int and self.maximum == 2, "generated global ceiling is not two")
        self.verify_configured_scopes()

    def verify_configured_scopes(self):
        principals = json.loads((self.bundle / "secrets/polyphony.json").read_text())
        runtime = principals[self.tokens["runtime"]]
        require(runtime["role"] == "runtime" and runtime["deployment_id"] == self.deployment
                and runtime["repositories"] == sorted(self.repositories), "runtime credential lacks the exact shared scope")
        scopes = json.loads((self.bundle / "secrets/openjules.json").read_text())
        require(scopes == {self.tokens["zpm"]: [{"deployment_id": self.deployment, "repository": name}
                                              for name in sorted(self.repositories)]},
                "controller credential lacks the exact shared scopes")
        policy = json.loads((self.bundle / "config/polyphony.json").read_text())
        profiles = json.loads((self.bundle / "config/openjules.json").read_text())["profiles"]
        require(policy["deployment_id"] == self.deployment
                and set(policy["repositories"]) == set(self.repositories)
                and len(profiles) == 2, "generated service policy changed repository count or deployment")
        for member in self.members:
            for role in ("ingress", "reviewer"):
                principal = principals[member.tokens[role]]
                require(principal["role"] == role and principal["deployment_id"] == self.deployment
                        and principal["repositories"] == [member.repository],
                        "repository credential has a broader or wrong scope")
            approved = policy["repositories"][member.repository]["profiles"]
            require(set(approved) == {member.profile} and approved[member.profile]["base"] == member.base,
                    "canonical policy changed the approved repository profile")
            bound = [value for value in profiles if value["repository"] == member.repository]
            require(len(bound) == 1 and bound[0]["deployment_id"] == self.deployment
                    and bound[0]["name"] == member.profile and bound[0]["base_commit"] == member.base
                    and bound[0]["source_path"] == str(member.source),
                    "runtime profile changed its repository source binding")

    def bootstrap(self):
        started = time.monotonic()
        self.processes.run_once("bootstrap", [sys.executable, str(self.bundle / "manage.py"), "bootstrap"],
                                self.bundle, clean_environment(), 900)
        self.observed_seconds["bootstrap"] = elapsed(started)
        self.python_versions = {name: subprocess.check_output(
            [str(self.bundle / "environments" / name / "bin/python"), "--version"],
            env=clean_environment(), text=True, timeout=15).strip() for name in self.source_paths}

    def doctor(self):
        result = subprocess.run([sys.executable, str(self.bundle / "manage.py"), "doctor"], cwd=self.bundle,
                                env=clean_environment(), capture_output=True, text=True, timeout=50)
        require(result.returncode in {0, 3}, "generated doctor could not inspect the installation")
        value = json.loads(result.stdout)
        if value.get("ready") is True:
            require(value.get("remote_access_checked") is True, "doctor lacks authenticated capability evidence")
            require_shared_plan(value["capabilities"], self.deployment, self.repositories)
        return value

    def plan(self):
        from zpm.controller import Controller
        from zpm.store import Store
        require(self.state.is_file() and not self.state.is_symlink(), "shared ownership journal is missing")
        with Store(self.state, self.deployment) as store:
            value = Controller(store, max_workers=self.maximum).plan()
        require_shared_plan(value, self.deployment, self.repositories)
        return value

    def start(self, name):
        self.processes.start(name, self.commands[name], self.source_paths[name], self.environments[name])

    def start_services(self):
        self.start("polyphony")
        for member in self.members:
            eventually(lambda member=member: self.authority.request(
                "GET", "/managed/capabilities", query=member.scope | {"profile": member.profile}),
                self.processes, "shared authority " + member.label, 20)
        self.start("openjules")
        for member in self.members:
            eventually(lambda member=member: self.runtime.request(
                "GET", "/api/workers/capabilities", query=member.scope | {"profile": member.profile}).get("managed_dispatch"),
                self.processes, "shared runtime " + member.label, 30)
        self.start("zpm")

    def stop_watch(self):
        require("zpm" in self.processes.running and self.processes.running["zpm"].poll() is None,
                "expected controller watch was not running")
        original = self.processes.running["zpm"]
        self.processes.stop("zpm")
        require(original.poll() is not None and "zpm" not in self.processes.running,
                "controller watch was not reaped before one-shot reconciliation")

    def one_tick(self, label):
        require("zpm" not in self.processes.running, "a watch controller would overlap the one-shot")
        command = self.commands["zpm"]
        require(command[-1] == "--watch", "generated controller command changed")
        name = "reconcile-" + label
        log = self.root / (name + ".log")
        require(not log.exists(), "one-shot report log was already used")
        self.processes.run_once(name, command[:-1], self.source_paths["zpm"], self.environments["zpm"], 90)
        value = json.loads(log.read_text())
        require_shared_plan(value, self.deployment, self.repositories)
        return value

    def change(self, member, operation, count=None):
        self.command_number += 1
        command = [str(self.bundle / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(self.state),
                   "--deployment-id", self.deployment, operation, member.repository]
        if count is not None:
            command.append(str(count))
        self.processes.run_once("operator-" + str(self.command_number), command, self.source_paths["zpm"],
                                clean_environment(), 30)

    def history(self, member):
        return retained_history(self.bundle, self.deployment, self.repositories, member.repository)


def validate_scope_credentials(shared):
    denials = []
    left, right = shared.members
    for member, other in ((left, right), (right, left)):
        for role, client in (("ingress", member.ingress), ("reviewer", member.reviewer)):
            own = client.request("GET", "/managed/capabilities", query=member.scope | {"profile": member.profile})
            require_scope_capability(own, member.scope, member.profile, authority=True)
            own = client.request("GET", "/managed/snapshot", query=member.scope)
            require(all(own.get(key) == value for key, value in member.scope.items()), "scoped snapshot changed identity")
            client.request("GET", "/managed/capabilities", query=other.scope | {"profile": other.profile}, expected=403)
            client.request("GET", "/managed/snapshot", query=other.scope, expected=403)
            denials.append({"role": role, "from": member.label, "to": other.label,
                            "own_capabilities_and_snapshot": 200, "other_scope": 403})
        # These credentials are deliberately shared host authorities.
        own = shared.authority.request("GET", "/managed/capabilities", query=member.scope | {"profile": member.profile})
        require_scope_capability(own, member.scope, member.profile, authority=True)
        own = shared.runtime.request("GET", "/api/workers/capabilities", query=member.scope | {"profile": member.profile})
        require_scope_capability(own, member.scope, member.profile)
    return denials


def reject_other_enrollment(member, other, accepted_request):
    # The exact accepted body is changed only in repository scope.
    member.ingress.request("POST", "/managed/enroll",
                           data=accepted_request | {"repository": other.repository}, expected=403)


def reject_other_permission(member, other, attempt_id):
    request = {"schema_version": 1, **member.scope, "attempt_id": attempt_id}
    allowed = member.authority.request("POST", "/managed/permission", data=request)
    require(allowed.get("launch_allowed") is True and allowed["attempt"]["id"] == attempt_id
            and all(allowed.get(key) == value for key, value in member.scope.items()),
            "positive permission control did not authorize the active attempt")
    member.authority.request("POST", "/managed/permission",
                             data=request | {"repository": other.repository}, expected=409)


def reject_other_artifacts(member, other, evidence):
    for digest in evidence["artifacts"].values():
        content = member.authority.request("GET", "/managed/artifacts/" + digest, query=member.scope, text=True)
        require(hashlib.sha256(content.encode()).hexdigest() == digest, "positive artifact control changed bytes")
        member.authority.request("GET", "/managed/artifacts/" + digest, query=other.scope, expected=409)


def displace_source(source):
    displaced = source.with_name(source.name + "-unavailable")
    require(not displaced.exists(), "source outage destination already exists")
    source.rename(displaced)
    return displaced


def restore_source(source, displaced):
    require(not source.exists(), "source restoration would replace an unexpected path")
    displaced.rename(source)


def cleanup_services(shared, displaced):
    try:
        if displaced is not None:
            restore_source(shared.members[0].source, displaced)
    finally:
        if shared is not None:
            shared.processes.close()


def run(args):
    from zpm.deployment import prepare, verify

    started = time.monotonic()
    zpm = Path(__file__).resolve().parents[2]
    source_paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
    revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=15).strip()
                 for name, path in source_paths.items()}
    root = args.work_dir.absolute()
    require(not root.exists(), "trial work directory must be new")
    root.mkdir(mode=0o700)
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True, timeout=30).strip()
    require(image == args.image and image.startswith("sha256:") and len(image) == 71,
            "trial requires an already-present immutable Docker image ID")
    engine = subprocess.check_output(["docker", "info", "--format", "{{.ID}}"], text=True, timeout=30).strip()
    require(bool(engine), "Docker Engine identity is unavailable")
    ports = {port()}
    while len(ports) < 2:
        ports.add(port())
    numbers = sorted(ports)
    shared, checks, evidence, observations = None, [], {}, []
    timings, source_evidence, quota_evidence = {}, {}, {}
    missing_source = None
    try:
        shared = SharedInstallation(root, source_paths, revisions, image,
                                    {"polyphony": numbers[0], "openjules": numbers[1]})
        left, right = shared.members
        shared.bootstrap()
        shared.processes.start("installation", [sys.executable, str(shared.bundle / "manage.py"), "start"],
                               shared.bundle, shared.manager_environment)
        def doctor_ready():
            value = shared.doctor()
            return value if value.get("ready") is True else None
        initial_doctor = eventually(doctor_ready, shared.processes, "generated shared doctor", 60)
        require(initial_doctor["capabilities"]["reserved"] == 0, "zero targets allocated a worker")
        shared.processes.stop("installation")
        verify(shared.bundle)
        shared.start_services()
        for member in shared.members:
            shared.change(member, "target", 1)
        initial = [wait_for_capacity(member.plan, shared.processes, 1) for member in shared.members]
        left_worker, right_worker = (next(iter(live_ids(value))) for value in initial)
        containers = [container_for(identity) for identity in (left_worker, right_worker)]
        require(left_worker != right_worker and len({value["Id"] for value in containers}) == 2
                and all(value["State"]["Running"] for value in containers), "workers are not distinct running containers")
        volumes = []
        for value in containers:
            mounted = [item for item in value["Mounts"] if item["Destination"] == "/workspace"]
            require(len(mounted) == 1 and mounted[0]["Type"] == "volume", "worker lacks a private workspace volume")
            volumes.append(mounted[0]["Name"])
        require(len(set(volumes)) == 2, "repositories share a workspace volume")
        denials = validate_scope_credentials(shared)
        for member, other, worker in ((left, right, left_worker), (right, left, right_worker)):
            shared.runtime.request("GET", "/api/workers/" + worker, query=member.scope)
            shared.runtime.request("GET", "/api/workers/" + worker, query=other.scope, expected=404)
        checks.append("one generated bundle passes doctor and runs distinct scoped workers through one service pair")

        right_task, request = right.enroll()
        reject_other_enrollment(right, left, request)
        right_attempt = eventually(lambda: right.attempt(right_task), shared.processes, "B canonical attempt", 20)
        right_container = containers[1]["Id"]
        eventually(lambda: progress(right_container), shared.processes, "B private progress barrier", 20)
        right_binding = worker_binding(initial[1]["repositories"][0]["workers"][0], containers[1], right_attempt["id"])
        right_binding["execution"] = require_uncontrolled_journals(worker_journals(shared.bundle, right_worker), right_binding)
        reject_other_permission(right, left, right_attempt["id"])
        right_history = shared.history(right)

        def fresh_peer():
            require_peer_unchanged(right.plan(), right.snapshot(), right.scope, right_worker, right_task, right_attempt["id"])
            worker = shared.runtime.request("GET", "/api/workers/" + right_worker, query=right.scope)
            classification = require_peer_runtime(worker, right.scope, right_worker, right_attempt["id"])
            require_active_runtime(worker, right_binding)
            require_uncontrolled_journals(worker_journals(shared.bundle, right_worker), right_binding)
            require_live_container(container_for(right_worker), right_binding)
            require_same_history(right_history, shared.history(right))
            return {"worker_id": right_worker, "attempt_id": right_attempt["id"], "container_id": right_container,
                    "exec_id": right_binding["execution"]["exec_id"], "classification": classification,
                    "http_state": worker["state"], "admission_open": worker["admission_open"],
                    "controller_pending_operation": None, "drain_or_stop_operations": 0,
                    "durable_draining": False, "runtime_retired": False, "container_running": True}

        def peer_progress(phase):
            value = checkpoint_progress(lambda: progress(right_container), fresh_peer, shared.processes)
            observations.append({"held_a_phase": phase, **value})
            return value

        peer_progress("original B execution before A changes")
        shared.change(left, "target", 0)
        wait_for_capacity(left.plan, shared.processes, 0)
        peer_progress("idle A retired to zero")
        shared.stop_watch()
        before = shared.history(left)
        # Assignment happens only after the rename succeeds.
        missing_source = displace_source(left.source)
        shared.change(left, "target", 1)
        a_capability = shared.runtime.request("GET", "/api/workers/capabilities",
                                             query=left.scope | {"profile": left.profile})
        require_scope_capability(a_capability, left.scope, left.profile, source_available=False)
        require_scope_capability(shared.runtime.request("GET", "/api/workers/capabilities",
                                 query=right.scope | {"profile": right.profile}), right.scope, right.profile)
        first = shared.one_tick("source-first")
        require_source_block(first, shared.deployment, left.repository, right.repository)
        require_same_history(before, shared.history(left))
        source_progress = peer_progress("source unavailable with one free global reservation")
        second = shared.one_tick("source-second")
        require_source_block(second, shared.deployment, left.repository, right.repository)
        after = shared.history(left)
        require_same_history(before, after)
        source_evidence = {"completed_controller_passes": 2, "desired_a": 1, "reserved_a": 0,
                           "global_reserved": 1, "configured_max_workers": 2,
                           "runtime_reason": SOURCE_UNAVAILABLE, "controller_reason": SOURCE_READINESS,
                           "retained_history_sha256": hashlib.sha256(canonical(after).encode()).hexdigest(),
                           "fresh_b_progress": source_progress}
        restore_source(left.source, missing_source)
        missing_source = None
        require(subprocess.check_output(["git", "-C", str(left.source), "rev-parse", "HEAD"],
                                        text=True, timeout=15).strip() == left.base, "restored source pin changed")
        verify(shared.bundle)
        shared.start("zpm")
        restored = wait_for_capacity(left.plan, shared.processes, 1)
        left_worker = next(iter(live_ids(restored)))
        require(left_worker not in {initial[0]["repositories"][0]["workers"][0]["worker_id"], right_worker},
                "source restoration reused a retired or foreign worker")
        peer_progress("exact A source restored and fresh idle worker ready")
        checks.append("two completed controller passes preserve no-allocation history during A source failure with free global capacity")

        shared.stop_watch()
        before = shared.history(left)
        shared.change(left, "target", 2)
        first = shared.one_tick("quota-first")
        require_quota(first, shared.deployment, left.repository, right.repository)
        require_same_history(before, shared.history(left))
        quota_progress = peer_progress("global ceiling holds A desired two with A1 and B1")
        second = shared.one_tick("quota-second")
        require_quota(second, shared.deployment, left.repository, right.repository)
        after = shared.history(left)
        require_same_history(before, after)
        quota_doctor = shared.doctor()
        require(quota_doctor.get("ready") is True, "quota doctor did not validate actual scoped capabilities")
        require_quota(quota_doctor["capabilities"], shared.deployment, left.repository, right.repository)
        quota_evidence = {"completed_controller_passes": 2, "desired_a": 2, "reserved_a": 1,
                          "a_shortfall": 1, "global_reserved": 2, "configured_max_workers": 2,
                          "doctor_remote_access_checked": quota_doctor["remote_access_checked"],
                          "doctor_ready": quota_doctor["ready"], "doctor_is_target_convergence": False,
                          "retained_history_sha256": hashlib.sha256(canonical(after).encode()).hexdigest(),
                          "fresh_b_progress": quota_progress}
        shared.change(left, "target", 1)
        shared.start("zpm")
        shared.change(left, "pause")
        paused = wait_for_capacity(left.plan, shared.processes, 0)
        paused_row = paused["repositories"][0]
        require(paused_row["enabled"] is True and paused_row["paused"] is True
                and paused_row["desired_workers"] == 1 and paused_row["effective_target"] == 0,
                "A pause did not retain its enabled enrollment and selected target")
        peer_progress("A paused with target retained")
        shared.change(left, "resume", 1)
        resumed = wait_for_capacity(left.plan, shared.processes, 1)
        resumed_row = resumed["repositories"][0]
        require(resumed_row["enabled"] is True and resumed_row["paused"] is False
                and resumed_row["desired_workers"] == resumed_row["effective_target"] == 1,
                "A resume did not restore its explicit target and unpaused enrollment")
        resumed_worker = next(iter(live_ids(resumed)))
        require(resumed_worker not in {left_worker, right_worker}, "A resume reused a retired or foreign worker")
        left_worker = resumed_worker
        peer_progress("A resumed with a fresh idle incarnation")
        checks.append("two completed passes and authenticated doctor show the shared ceiling, then A pauses and resumes while B retains its execution")

        left_container_data = container_for(left_worker)
        left_container = left_container_data["Id"]
        left_task, request = left.enroll()
        reject_other_enrollment(left, right, request)
        left_attempt = eventually(lambda: left.attempt(left_task), shared.processes, "A canonical attempt", 20)
        eventually(lambda: progress(left_container), shared.processes, "A private progress barrier", 20)
        left_binding = worker_binding(resumed["repositories"][0]["workers"][0], left_container_data, left_attempt["id"])
        left_binding["execution"] = require_uncontrolled_journals(worker_journals(shared.bundle, left_worker), left_binding)
        reject_other_permission(left, right, left_attempt["id"])
        shared.change(left, "remove")

        def pending_drain():
            require_removed_plan(left.plan(), left_binding)
            records = worker_journals(shared.bundle, left_worker)
            if not records["controls"]:
                return None
            require(len(records["controls"]) == 1, "A has more than one owned drain intent")
            key = records["controls"][0]["request_key"]
            require_pending_drain_journals(records, left_binding, key)
            require_controller_drain(shared.state, left_binding, key)
            require_pending_drain_runtime(shared.runtime.request(
                "GET", "/api/workers/" + left_worker, query=left.scope), left_binding)
            return key
        drain_key = eventually(pending_drain, shared.processes, "removed A retains its pending active drain", 25)

        def fresh_drain():
            require_removed_plan(left.plan(), left_binding)
            worker = shared.runtime.request("GET", "/api/workers/" + left_worker, query=left.scope)
            classification = require_pending_drain_runtime(worker, left_binding)
            require_pending_drain_journals(worker_journals(shared.bundle, left_worker), left_binding, drain_key)
            require_controller_drain(shared.state, left_binding, drain_key)
            require_live_container(container_for(left_worker), left_binding)
            return {"worker_id": left_worker, "attempt_id": left_attempt["id"], "container_id": left_container,
                    "exec_id": left_binding["execution"]["exec_id"], "classification": classification,
                    "admission_open": False, "durable_draining": True, "drain_completed": False}
        drain_evidence = checkpoint_progress(lambda: progress(left_container), fresh_drain, shared.processes)
        peer_progress("A removed with its original active pending drain")
        release(left_container)
        evidence["a"] = left.captured(left_task, left_worker, shared.processes)
        wait_for_capacity(left.plan, shared.processes, 0)
        reject_other_artifacts(left, right, evidence["a"])
        peer_progress("A capture and foreign artifact denial completed")
        checks.append("removed A retains its own active drain until canonical capture; B never receives a control operation")

        # End the uncontrolled-peer interval before intentionally retiring B.
        # Its target is zero while the original root remains held, preventing
        # desired-one replacement after the root exits.
        shared.change(right, "target", 0)
        release(right_container)
        evidence["b"] = right.captured(right_task, right_worker, shared.processes)
        wait_for_capacity(right.plan, shared.processes, 0)
        reject_other_artifacts(right, left, evidence["b"])
        require(shared.plan()["reserved"] == 0, "final shared drain left unresolved capacity")
        snapshots = {member.label: member.snapshot() for member in shared.members}
        expected_tasks = {"a": left_task, "b": right_task}
        for member in shared.members:
            snapshot = snapshots[member.label]
            require({row["id"] for row in snapshot["task"]} == {expected_tasks[member.label]}
                    and len(snapshot["attempt"]) == 1, "scoped canonical history contains extra or foreign work")
        before_bindings = {row["repository"]: {key: row[key] for key in
                           ("desired_workers", "effective_target", "enabled", "paused", "revision")}
                           for row in shared.plan()["repositories"]}
        for name in ("zpm", "openjules", "polyphony"):
            shared.processes.stop(name)
        require(prepare(shared.input_data, shared.bundle)["reused"] is True, "restart did not reuse the shared installation")
        require(shared.sealed == {name: (shared.bundle / name).read_bytes() for name in shared.sealed},
                "restart changed sealed configuration or credentials")
        shared.start_services()
        eventually(doctor_ready, shared.processes, "restarted shared doctor", 60)
        after_plan = shared.plan()
        after_bindings = {row["repository"]: {key: row[key] for key in
                          ("desired_workers", "effective_target", "enabled", "paused", "revision")}
                          for row in after_plan["repositories"]}
        require(before_bindings == after_bindings and after_plan["reserved"] == 0,
                "restart changed retained enrollment or allocated capacity")
        require(after_bindings[left.repository]["enabled"] is False
                and after_bindings[right.repository]["enabled"] is True
                and after_bindings[right.repository]["desired_workers"] == 0,
                "restart did not retain removed A and zero-target B")
        fresh = Client(shared.authority_endpoint, json.loads(
            (shared.bundle / "secrets/environment.json").read_text())["POLYPHONY_RUNTIME_TOKEN"])
        for member in shared.members:
            require(fresh.request("GET", "/managed/snapshot", query=member.scope) == snapshots[member.label],
                    "restart changed scoped canonical records")
            for digest in evidence[member.label]["artifacts"].values():
                content = fresh.request("GET", "/managed/artifacts/" + digest, query=member.scope, text=True)
                require(hashlib.sha256(content.encode()).hexdigest() == digest, "restart changed captured artifact bytes")
        require(subprocess.check_output(["docker", "info", "--format", "{{.ID}}"],
                                        text=True, timeout=30).strip() == engine, "Docker Engine identity changed")
        checks.append("both captures survive full service restart with removed A, zero-target B and unchanged private credentials")
        final_history = {member.label: shared.history(member) for member in shared.members}
        shared.processes.close()
        timings["total_including_bootstrap_and_service_cleanup"] = elapsed(started)
        report = {
            "schema_version": 1, "passed": True, "topology": TOPOLOGY,
            "component_revisions": revisions, "image": image, "docker_engine_id": engine,
            "harness_python": sys.version.split()[0], "host_platform": platform.platform(),
            "service_python_versions": shared.python_versions,
            "deployment_id": shared.deployment, "configured_max_workers": 2, "final_reserved": 0,
            "repositories": {member.label: {**member.scope, "profile": member.profile, "source_revision": member.base,
                                          "retained_worker_count": len(final_history[member.label]["zpm"])}
                             for member in shared.members},
            "checks": checks, "credential_scope_checks": denials,
            "resource_scope_checks": {
                "worker_reads_both_directions": {"own_scope": 200, "other_allowed_scope": 404},
                "active_permission_both_directions": {"own_scope": 200, "other_allowed_scope": 409},
                "artifact_reads_both_directions": {"own_scope": 200, "other_allowed_scope": 409},
                "ingress_enrollment_both_directions": {"own_scope": 200, "other_scope": 403},
            },
            "canonical_evidence": evidence,
            "source_failure": source_evidence, "global_ceiling": quota_evidence,
            "peer_progress_checkpoints": observations,
            "peer_interval": "original B root through A operations; ends before B's explicit target-zero and release",
            "owned_pending_drain": {"request_key": drain_key, "backend_delivery_confirmed": False, **drain_evidence},
            "observed_seconds": shared.observed_seconds | timings,
            "proof": "real generated installation, real HTTP and Docker, controlled commands and private fixture barriers",
            "inference": "not configured; command-only execution",
            "timing_scope": "monotonic elapsed time to observed predicates; no performance or fairness bound",
            "not_proven": LIMITS,
        }
        require(not any(token in json.dumps(report) for token in shared.sensitive), "aggregate report contains a credential")
        private_json(root / "report.json", report)
        return report
    except Exception as exc:
        error = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        sensitive = shared.sensitive if shared else set()
        value = diagnostics(root, error, checks, revisions, sensitive)
        print(json.dumps(value, sort_keys=True), file=sys.stderr)
        raise
    finally:
        cleanup_services(shared, missing_source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument("--" + name + "-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-present full sha256 image ID")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private directory retained for recovery")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"passed": False, "error": "multiple-repository trial failed; inspect retained redacted diagnostics"}),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
