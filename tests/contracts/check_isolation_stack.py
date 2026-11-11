"""Qualify two generated installations sharing one real Docker Engine.

Each deployment has its own controller, OpenJules API and Polyphony authority.
The deterministic task commands use private fixture barriers, not model calls.
Retain the work directory privately; export only the aggregate report or its
bounded, credential-redacted diagnostics.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import secrets
import sqlite3
import subprocess
import sys
import tarfile
import time

from check_stack import (
    Client, Processes, clean_environment, diagnostics, eventually, port,
    private_json, require, wait_for_capacity,
)


TOPOLOGY = "two generated deployments, separate service pairs and controllers, one Docker Engine"
LIMITS = [
    "mutually hostile host services with Docker authority",
    "two repositories in one generated installation",
    "one-controller multi-repository capacity fairness",
    "multiple deployments behind one OpenJules API",
    "live model inference", "independent semantic review", "forge publication",
]


SPEC_FIELDS = ("schema_version", "deployment_id", "repository", "slot_id", "generation", "launch_key", "profile")
LOCK_CONTENTION = "runtime operation is in progress"
PENDING_DRAIN = "Drain intent awaits runtime confirmation"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def elapsed(started):
    return round(time.monotonic() - started, 6)


TASK_PROGRAM = '''from pathlib import Path
import hashlib
import os
import sys
import time

label, own_digest, foreign_digest, *private_paths = sys.argv[1:]
requirements = sys.stdin.read()
assert os.getuid() == 65532
assert Path("identity.txt").read_text() == label + "\\n"
other = "b" if label == "a" else "a"
assert Path(label + "-only.txt").read_text() == "source " + label + "\\n"
assert not Path(other + "-only.txt").exists()
assert not Path("/source/" + other + "-only.txt").exists()
assert not Path("/source/.git").exists()
assert not Path("/source/.env").exists()
assert not Path("/var/run/docker.sock").exists()
assert not Path("/run/openjules-model").exists()
assert not any(Path(value).exists() for value in private_paths)
assert hashlib.sha256(os.environ["OPENAI_API_KEY"].encode()).hexdigest() == own_digest
assert not any(hashlib.sha256(value.encode()).hexdigest() == foreign_digest for value in os.environ.values())
assert not any(name in os.environ for name in (
    "POLYPHONY_RUNTIME_TOKEN", "OPENJULES_WORKER_TOKEN", "ZPM_MODEL_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY",
))
assert not any(name.startswith("ZPM_ISOLATION_") for name in os.environ)
Path("answer.txt").write_text("completed " + label + "\\n")
print("ISOLATION_BOUNDARY_VERIFIED_" + label, flush=True)
if "HOLD_FOR_ISOLATION_TRIAL" in requirements:
    cutoff = time.monotonic() + 240
    counter = 0
    while not Path(".isolation-release").exists():
        assert time.monotonic() < cutoff, "private fixture release deadline"
        counter += 1
        Path(".isolation-progress.tmp").write_text(str(counter))
        Path(".isolation-progress.tmp").replace(".isolation-progress")
        time.sleep(0.1)
print("ISOLATION_COMPLETED_" + label, flush=True)
'''


@contextmanager
def supplied_environment(values):
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class AllProcesses:
    def __init__(self, installations):
        self.installations = installations

    def check(self):
        for installation in self.installations:
            installation.processes.check()


def live_ids(plan):
    require(len(plan["repositories"]) == 1, "trial installation changed repository count")
    workers = plan["repositories"][0]["workers"]
    identities = {worker["worker_id"] for worker in workers}
    require(all(isinstance(value, str) and value for value in identities)
            and len(identities) == len(workers), "capacity has missing or duplicate worker identities")
    return identities


def require_peer_unchanged(plan, snapshot, scope, worker_id, task_id, attempt_id):
    """Do not accept equal capacity counts as evidence of preserved identity."""
    require(plan["deployment_id"] == scope["deployment_id"], "peer deployment identity changed")
    require(live_ids(plan) == {worker_id}, "peer worker was replaced or another worker was adopted")
    row = plan["repositories"][0]
    require(row["repository"] == scope["repository"] and row["desired_workers"] == 1
            and row["effective_target"] == 1 and row["enabled"] and not row["paused"]
            and row["reserved"] == 1, "peer enrollment or reservation changed")
    worker = row["workers"][0]
    require("pending_operation" in worker and worker["pending_operation"] is None,
            "peer controller has a pending or unobserved operation")
    require(all(worker.get(key) == value for key, value in scope.items()), "peer runtime scope changed")
    classify_active_root(worker)
    require(all(snapshot.get(key) == value for key, value in scope.items()), "peer authority scope changed")
    require({task["id"] for task in snapshot["task"]} == {task_id}, "peer canonical task changed")
    attempts = snapshot["attempt"]
    require(len(attempts) == 1 and attempts[0]["id"] == attempt_id
            and attempts[0]["task"] == task_id
            and attempts[0]["runtime_binding"]["worker_id"] == worker_id
            and not attempts[0].get("termination_confirmed"), "peer execution was replaced or terminated")


def classify_active_root(worker):
    # A command-only root holds the backend execution lock. Its conservative
    # observation is not evidence of open admission or healthy capacity.
    if worker.get("state") == "unknown":
        require(worker.get("reason") == LOCK_CONTENTION and worker.get("admission_open") is False,
                "active root has unrecognized uncertainty")
        return "unknown: execution lock held; admission closed"
    require(worker.get("state") in {"ready", "busy"} and worker.get("admission_open") is True,
            "active root lost admission without recognized execution-lock uncertainty")
    return "ready or busy: admission open"


def require_active_runtime(worker, binding):
    require(worker.get("worker_id") == binding["worker_id"]
            and canonical({key: worker.get(key) for key in SPEC_FIELDS}) == canonical(binding["spec"]),
            "fresh runtime observation changed its immutable worker binding")
    require(worker.get("active_executions") == [binding["attempt_id"]]
            and worker.get("termination_confirmed") is False and worker.get("artifacts_captured") is False,
            "fresh runtime observation lost the original active root")


def require_peer_runtime(worker, scope, worker_id, attempt_id):
    require(worker.get("worker_id") == worker_id and all(worker.get(key) == value for key, value in scope.items()),
            "fresh peer runtime observation changed identity or scope")
    require(worker.get("active_executions") == [attempt_id]
            and worker.get("termination_confirmed") is False and worker.get("artifacts_captured") is False,
            "fresh peer runtime observation lost the original active attempt")
    return classify_active_root(worker)


def require_pending_drain_runtime(worker, binding):
    require_active_runtime(worker, binding)
    require(worker.get("admission_open") is False and worker.get("state") == "unknown"
            and worker.get("reason") == PENDING_DRAIN + "; " + LOCK_CONTENTION,
            "owned drain lacks the exact pending execution-lock observation")
    return "unknown: owned drain pending; admission closed"


@contextmanager
def read_journal(path):
    """Inspect existing state without constructors, migrations or PRAGMA writes."""
    require(path.is_file() and not path.is_symlink(), "required runtime journal is absent or replaced")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as database:
        database.row_factory = sqlite3.Row
        database.execute("BEGIN")
        yield database


def worker_journals(bundle, worker_id):
    config = json.loads((bundle / "config/openjules.json").read_text())
    lifecycle_path = Path(config["lifecycle_database"])
    # The generated state_directory is the managed-service root. The pinned
    # service passes its runtime/ child to ManagedDockerRuntime.
    runtime_path = Path(config["state_directory"]) / "runtime/journal.sqlite3"
    with read_journal(lifecycle_path) as database:
        lifecycle = database.execute("SELECT * FROM managed_workers WHERE worker_id=?", (worker_id,)).fetchone()
        executions = database.execute(
            "SELECT worker_id,execution_id,parent_id,termination_confirmed,artifacts_captured "
            "FROM worker_executions WHERE worker_id=? ORDER BY execution_id", (worker_id,)).fetchall()
        # Filter only by original worker identity. Filtering by expected scope or
        # completed=0 would conceal foreign-scoped or already-completed controls.
        controls = database.execute(
            "SELECT deployment_id,repository,request_key,action,worker_id,payload,completed "
            "FROM worker_operations WHERE worker_id=? AND action IN ('drain','stop') ORDER BY rowid",
            (worker_id,)).fetchall()
    with read_journal(runtime_path) as database:
        runtime = database.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
        attempt = database.execute("SELECT * FROM attempts WHERE worker_id=?", (worker_id,)).fetchone()
    return {"lifecycle": dict(lifecycle) if lifecycle else None,
            "executions": [dict(row) for row in executions], "controls": [dict(row) for row in controls],
            "runtime": dict(runtime) if runtime else None, "attempt": dict(attempt) if attempt else None}


def worker_binding(worker, container, attempt_id):
    spec = {key: worker[key] for key in SPEC_FIELDS}
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1
            and type(spec["generation"]) is int and spec["generation"] > 0,
            "original worker has an invalid version or generation")
    fingerprint = container["Config"]["Labels"].get("openjules.profile")
    require(isinstance(fingerprint, str) and len(fingerprint) == 64
            and all(character in "0123456789abcdef" for character in fingerprint),
            "original container lacks its approved profile fingerprint")
    binding = {"worker_id": worker["worker_id"], "spec": spec, "container_id": container["Id"],
               "profile_fingerprint": fingerprint, "attempt_id": attempt_id}
    require_live_container(container, binding)
    return binding


def require_bound_active_journals(records, binding):
    lifecycle, runtime, attempt = (records[name] for name in ("lifecycle", "runtime", "attempt"))
    require(all(isinstance(row, dict) for row in (lifecycle, runtime, attempt)),
            "original active worker or attempt is missing from retained journals")
    spec = binding["spec"]
    payload = canonical(spec)
    require(lifecycle["worker_id"] == binding["worker_id"] and lifecycle["spec"] == payload
            and all(lifecycle[key] == spec[key] for key in ("deployment_id", "repository", "slot_id", "generation"))
            and lifecycle["runtime_id"] == "docker:" + binding["container_id"]
            and lifecycle["termination_confirmed"] == 0 and lifecycle["artifacts_captured"] == 0,
            "lifecycle journal changed or completed the original worker")
    require(runtime["worker_id"] == binding["worker_id"] and runtime["spec"] == payload
            and runtime["container_id"] == binding["container_id"]
            and runtime["profile_fingerprint"] == binding["profile_fingerprint"]
            and runtime["start_confirmed"] == 1 and runtime["retired"] == 0 and runtime["receipt"] is None,
            "runtime journal changed, retired or captured the original worker")
    require(records["executions"] == [{"worker_id": binding["worker_id"], "execution_id": binding["attempt_id"],
                                     "parent_id": None, "termination_confirmed": 0, "artifacts_captured": 0}],
            "lifecycle journal changed or completed the original root execution")
    require(attempt["worker_id"] == binding["worker_id"] and attempt["attempt_id"] == binding["attempt_id"]
            and attempt["exec_dispatched"] == 1 and isinstance(attempt["exec_id"], str) and bool(attempt["exec_id"])
            and attempt["cancelled"] == 0 and attempt["exit_code"] is None,
            "runtime journal changed, cancelled or completed the original attempt")
    execution = {key: attempt[key] for key in ("attempt_id", "input_sha256", "seconds", "deadline", "exec_id")}
    if "execution" in binding:
        require(execution == binding["execution"], "runtime changed the original execution input, budget or Docker exec")
    return execution


def require_uncontrolled_journals(records, binding):
    execution = require_bound_active_journals(records, binding)
    require(records["lifecycle"]["draining"] == 0 and records["controls"] == [],
            "peer received a durable drain or stop, including an already-completed control")
    return execution


def require_pending_drain_journals(records, binding, request_key):
    require_bound_active_journals(records, binding)
    require(isinstance(request_key, str) and bool(request_key), "owned drain lacks its original request key")
    expected = {key: binding["spec"][key] for key in ("deployment_id", "repository")}
    expected.update(request_key=request_key, action="drain", worker_id=binding["worker_id"],
                    payload=canonical({"worker_id": binding["worker_id"], "action": "drain"}), completed=0)
    require(records["lifecycle"]["draining"] == 1 and records["controls"] == [expected],
            "owned drain intent changed its scope, original key, payload or pending state")


def require_live_container(container, binding):
    labels = container.get("Config", {}).get("Labels") or {}
    require(container.get("Id") == binding["container_id"] and container.get("State", {}).get("Running") is True
            and labels.get("openjules.managed") == "1" and labels.get("openjules.worker") == binding["worker_id"]
            and labels.get("openjules.spec") == hashlib.sha256(canonical(binding["spec"]).encode()).hexdigest()
            and labels.get("openjules.profile") == binding["profile_fingerprint"],
            "actual Docker container changed identity, scope, approved profile or running state")


def controller_operation(state, binding):
    with read_journal(state) as database:
        identity = database.execute("SELECT value FROM metadata WHERE key='deployment_id'").fetchone()
        row = database.execute("SELECT * FROM workers WHERE launch_key=?", (binding["spec"]["launch_key"],)).fetchone()
    require(identity is not None and identity[0] == binding["spec"]["deployment_id"] and row is not None,
            "controller lost the original deployment or reservation")
    require(all(row[key] == binding["spec"][key] for key in ("repository", "slot_id", "generation", "profile")),
            "controller changed the original reservation binding")
    snapshot = json.loads(row["snapshot"])
    require(snapshot.get("worker_id") == binding["worker_id"]
            and canonical({key: snapshot.get(key) for key in SPEC_FIELDS}) == canonical(binding["spec"]),
            "controller changed the original worker incarnation")
    return row["pending_action"], row["action_key"]


def require_controller_drain(state, binding, request_key):
    # Acknowledged asynchronous intent may clear the local pending field.
    # Freeze the first durable lifecycle key; ZPM retains no operation history.
    require(controller_operation(state, binding) in {("drain", request_key), (None, None)},
            "controller replaced the original owned drain request")


def require_removed_plan(plan, binding):
    require(plan["deployment_id"] == binding["spec"]["deployment_id"]
            and live_ids(plan) == {binding["worker_id"]}, "removal changed the unresolved worker identity")
    row = plan["repositories"][0]
    require(row["repository"] == binding["spec"]["repository"] and row["profile"] == binding["spec"]["profile"]
            and row["enabled"] is False and row["desired_workers"] == 1
            and row["effective_target"] == 0 and row["reserved"] == 1,
            "removal lost its disabled enrollment, retained target or unresolved reservation")


def checkpoint_progress(read_counter, check_peer, processes):
    """Take the baseline only after entering the caller's held A phase."""
    witness_before = check_peer()
    before = read_counter()
    require(type(before) is int and before > 0, "peer has no current progress baseline")

    def advanced():
        value = read_counter()
        return value if type(value) is int and value > before else None

    after = eventually(advanced, processes, "peer advances inside the current held phase", 15)
    witness_after = check_peer()
    return {"before": before, "after": after, "witness_before": witness_before, "witness_after": witness_after}


def container_for(worker_id):
    result = subprocess.run(["docker", "ps", "-aq", "--filter", "label=openjules.worker=" + worker_id],
                            capture_output=True, text=True, check=True, timeout=10)
    ids = result.stdout.split()
    require(len(ids) == 1, "worker must identify exactly one retained Docker container")
    inspected = subprocess.run(["docker", "inspect", ids[0]], capture_output=True, text=True, check=True, timeout=10)
    data = json.loads(inspected.stdout)[0]
    require(data["Config"]["Labels"].get("openjules.worker") == worker_id, "Docker worker label changed")
    return data


def progress(container_id):
    result = subprocess.run(["docker", "cp", container_id + ":/workspace/run/repo/.isolation-progress", "-"],
                            capture_output=True, timeout=10)
    if result.returncode:
        return None
    require(len(result.stdout) <= 65536, "fixture progress archive is oversized")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        files = [member for member in archive if member.isfile()]
        require(len(files) == 1 and files[0].size <= 32, "invalid fixture progress record")
        value = archive.extractfile(files[0]).read().decode("ascii")
    require(value.isdecimal(), "invalid fixture progress counter")
    return int(value)


def release(container_id):
    # The trusted harness releases only this deterministic task's private
    # barrier. Lifecycle admission, runtime receipts and capture stay real.
    subprocess.run(["docker", "exec", "--user", "65532:65532", container_id,
                    "python3", "-I", "-S", "-c",
                    "from pathlib import Path; Path('/workspace/run/repo/.isolation-release').touch()"],
                   check=True, capture_output=True, timeout=10)


class Installation:
    def __init__(self, root, label, source_paths, revisions, image, ports, canary, other_canary):
        from zpm.deployment import _command, _environment, prepare, verify

        self.label = label
        self.root = root / label
        self.root.mkdir(mode=0o700)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o755)
        for name, content in {
            "identity.txt": label + "\n", label + "-only.txt": "source " + label + "\n",
            "answer.txt": "initial " + label + "\n", "run_trial.py": TASK_PROGRAM,
            ".gitignore": ".env\n.isolation-*\n",
        }.items():
            (self.source / name).write_text(content)
        for command in (["init", "-q"], ["add", "."],
                        ["-c", "user.name=Isolation Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled repository " + label]):
            subprocess.run(["git", "-C", str(self.source), *command], check=True, capture_output=True, timeout=15)
        self.host_canary = secrets.token_urlsafe(32)
        (self.source / ".env").write_text("HOST_ONLY_ISOLATION_CANARY=" + self.host_canary + "\n")
        self.base = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
        self.deployment = "isolation-" + label + "-" + secrets.token_hex(8)
        self.repository = "github:github.com/managed-isolation/repository-" + label
        self.profile = "controlled-isolation-" + label
        self.scope = {"deployment_id": self.deployment, "repository": self.repository}
        self.bundle = self.root / "installation"
        self.source_paths = source_paths
        self.secret_reference = "ZPM_ISOLATION_" + label.upper() + "_" + secrets.token_hex(8).upper()
        self.manager_environment = clean_environment({self.secret_reference: canary})
        private_paths = [str(root / name / "installation") for name in ("a", "b")]
        self.data = {
            "schema_version": 1, "deployment_id": self.deployment,
            "binding": {"schema_version": 1, "repository": self.repository, "profile": self.profile, "desired_workers": 0},
            "max_workers": 2,
            "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in source_paths.items()},
            "source": {"checkout": str(self.source), "revision": self.base}, "image": image,
            "command": ["python3", "-I", "-S", "run_trial.py", label,
                        hashlib.sha256(canary.encode()).hexdigest(), hashlib.sha256(other_canary.encode()).hexdigest(), *private_paths],
            "ports": ports, "runtime": {"max_seconds": 300, "pids_limit": 64},
            "environment_refs": {"OPENAI_API_KEY": "env:" + self.secret_reference},
            "policy": {"checks": ["controlled-isolation"], "reviewers": ["operator"], "max_attempts": 1,
                       "budget_seconds": 400, "attempt_seconds": 270, "capabilities": ["python"]},
        }
        self.observed_seconds = {}
        started = time.monotonic()
        prepare(self.data, self.bundle)
        self.observed_seconds["prepare"] = elapsed(started)
        started = time.monotonic()
        verify(self.bundle)
        self.observed_seconds["verify"] = elapsed(started)
        self.data = json.loads((self.bundle / "manifest.json").read_text())["configuration"]
        self.state = self.bundle / "state/zpm.sqlite3"
        environment_tokens = json.loads((self.bundle / "secrets/environment.json").read_text())
        operator_tokens = json.loads((self.bundle / "secrets/operator.json").read_text())
        self.tokens = {"runtime": environment_tokens["POLYPHONY_RUNTIME_TOKEN"],
                       "zpm": environment_tokens["OPENJULES_WORKER_TOKEN"], "ingress": operator_tokens["ingress"]}
        self.credential_values = set(environment_tokens.values()) | set(operator_tokens.values())
        self.sensitive = self.credential_values | {canary, other_canary, self.host_canary}
        self.sealed = {name: (self.bundle / name).read_bytes() for name in
                       ("manifest.json", "secrets/environment.json", "secrets/operator.json", "secrets/polyphony.json", "secrets/openjules.json")}
        self.authority_endpoint = f"http://127.0.0.1:{ports['polyphony']}"
        self.runtime_endpoint = f"http://127.0.0.1:{ports['openjules']}"
        self.authority = Client(self.authority_endpoint, self.tokens["runtime"])
        self.ingress = Client(self.authority_endpoint, self.tokens["ingress"])
        self.runtime = Client(self.runtime_endpoint, self.tokens["zpm"])
        self.commands = {name: _command(self.bundle, name, self.data) for name in source_paths}
        with supplied_environment({self.secret_reference: canary}):
            self.environments = {name: _environment(self.bundle, name, self.data) for name in source_paths}
        self.processes = Processes(self.root)

    def bootstrap(self):
        started = time.monotonic()
        self.processes.run_once("bootstrap", [sys.executable, str(self.bundle / "manage.py"), "bootstrap"],
                                self.bundle, clean_environment(), 900)
        self.observed_seconds["bootstrap"] = elapsed(started)
        self.python_versions = {
            name: subprocess.check_output([str(self.bundle / "environments" / name / "bin/python"), "--version"],
                                          env=clean_environment(), text=True, timeout=15).strip()
            for name in self.source_paths
        }

    def doctor(self):
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(self.bundle / "manage.py"), "doctor"], cwd=self.bundle,
                                env=clean_environment(), capture_output=True, text=True, timeout=50)
        self.observed_seconds.setdefault("doctor_calls", []).append(elapsed(started))
        require(result.returncode in {0, 3}, "generated doctor could not inspect the installation")
        return json.loads(result.stdout).get("ready") is True

    def start(self, name):
        self.processes.start(name, self.commands[name], self.source_paths[name], self.environments[name])

    def start_services(self, processes):
        self.start("polyphony")
        eventually(lambda: self.authority.request("GET", "/managed/capabilities", query=self.scope | {"profile": self.profile}),
                   processes, "authority " + self.label, 20)
        self.start("openjules")
        eventually(lambda: self.runtime.request("GET", "/api/workers/capabilities", query=self.scope | {"profile": self.profile}).get("managed_dispatch"),
                   processes, "runtime " + self.label, 30)
        self.start("zpm")

    def plan(self):
        from zpm.controller import Controller
        from zpm.store import Store
        with Store(self.state, self.deployment) as store:
            return Controller(store).plan()

    def change(self, operation, count=None):
        from zpm.store import Store
        with Store(self.state, self.deployment) as store:
            store.change(self.repository, operation, count)

    def cli(self, operation, value):
        command = [str(self.bundle / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(self.state),
                   "--deployment-id", self.deployment, operation, value]
        self.processes.run_once("reenroll" if operation == "enroll" else operation, command,
                                self.source_paths["zpm"], clean_environment(), 30)

    def snapshot(self):
        return self.authority.request("GET", "/managed/snapshot", query=self.scope)

    def enroll(self, number, hold=True):
        request = {"schema_version": 1, **self.scope, "source": "github:issue:" + str(number),
                   "request_key": "controlled-isolation-" + str(number), "title": "Repository isolation " + self.label,
                   "requirements": "Verify this repository and change answer.txt. " + ("HOLD_FOR_ISOLATION_TRIAL" if hold else "Complete normally.")}
        result = self.ingress.request("POST", "/managed/enroll", data=request)
        require(self.ingress.request("POST", "/managed/enroll", data=request)["task"]["id"] == result["task"]["id"],
                "source replay created a duplicate task")
        return result["task"]["id"]

    def attempt(self, task_id):
        attempts = [row for row in self.snapshot()["attempt"] if row["task"] == task_id]
        require(len(attempts) <= 1, "canonical task has duplicate attempts")
        return attempts[0] if attempts and attempts[0].get("runtime_binding") else None

    def captured(self, task_id, worker_id, processes):
        def complete():
            attempt = self.attempt(task_id)
            return attempt if attempt and attempt.get("termination_confirmed") and attempt.get("captured_artifacts") else None
        attempt = eventually(complete, processes, "canonical capture " + self.label, 45)
        require(attempt["runtime_binding"]["worker_id"] == worker_id and attempt.get("launch_fenced") is True,
                "captured attempt changed its worker or lacks a launch fence")
        require(set(attempt["captured_artifacts"]) == {"patch", "log", "manifest"}, "canonical capture is incomplete")
        contents = {}
        for kind, digest in attempt["captured_artifacts"].items():
            content = self.authority.request("GET", "/managed/artifacts/" + digest, query=self.scope, text=True)
            require(hashlib.sha256(content.encode()).hexdigest() == digest, "canonical artifact hash mismatch")
            require(not any(token in content for token in self.sensitive), "canonical artifact exposed a test credential")
            contents[kind] = content
        require("+completed " + self.label in contents["patch"], "captured patch lacks this repository's change")
        require("ISOLATION_BOUNDARY_VERIFIED_" + self.label in contents["log"]
                and "ISOLATION_COMPLETED_" + self.label in contents["log"], "controlled boundary or completion assertion failed")
        snapshot = self.snapshot()
        candidates = [row for row in snapshot["candidate"] if row["task"] == task_id]
        require(len(candidates) == 1 and candidates[0]["base"] == self.base
                and candidates[0]["patch"] == attempt["captured_artifacts"]["patch"], "candidate changed its source base or capture")
        require(next(row for row in snapshot["task"] if row["id"] == task_id)["status"] == "awaiting_review"
                and not snapshot.get("admission"), "runtime self-authorized acceptance")
        require((self.source / "answer.txt").read_text() == "initial " + self.label + "\n", "task modified its approved source")
        return {"task": task_id, "attempt": attempt["id"], "worker": worker_id,
                "candidate": candidates[0]["id"], "artifacts": attempt["captured_artifacts"]}


def denied_cross_access(left, right, left_worker, right_worker):
    denials = []
    for current, other, own_worker, foreign_worker in ((left, right, left_worker, right_worker), (right, left, right_worker, left_worker)):
        for endpoint, token, path in ((other.runtime_endpoint, current.tokens["zpm"], "/api/workers"),
                                      (other.authority_endpoint, current.tokens["runtime"], "/managed/snapshot")):
            Client(endpoint, token).request("GET", path, query=other.scope, expected=401)
            denials.append({"from": current.label, "to": other.label, "operation": path, "case": "foreign token", "status": 401})
        Client(other.runtime_endpoint, current.tokens["zpm"]).request(
            "POST", "/api/workers/" + foreign_worker + "/drain",
            data={"schema_version": 1, **other.scope, "request_key": "deny-foreign-token"}, expected=401)
        ingress_payload = {"schema_version": 1, **other.scope, "source": "github:issue:denied",
                           "request_key": "deny-foreign-ingress", "title": "Denied scope trial", "requirements": "Must not create a task."}
        Client(other.authority_endpoint, current.tokens["ingress"]).request(
            "POST", "/managed/enroll", data=ingress_payload, expected=401)
        current.ingress.request("POST", "/managed/enroll", data=ingress_payload, expected=403)
        for scope in (current.scope | {"deployment_id": other.deployment}, current.scope | {"repository": other.repository}):
            current.runtime.request("GET", "/api/workers", query=scope, expected=403)
            current.authority.request("GET", "/managed/snapshot", query=scope, expected=403)
        current.runtime.request("GET", "/api/workers/" + foreign_worker, query=current.scope, expected=404)
        for action in ("drain", "stop"):
            current.runtime.request("POST", "/api/workers/" + foreign_worker + "/" + action,
                                    data={"schema_version": 1, **current.scope, "request_key": "deny-foreign-" + action}, expected=404)
            current.runtime.request("POST", "/api/workers/" + own_worker + "/" + action,
                                    data={"schema_version": 1, **other.scope, "request_key": "deny-scope-" + action}, expected=403)
        denials.extend([{"from": current.label, "case": "foreign deployment or repository", "status": 403},
                        {"from": current.label, "case": "foreign worker read, drain and stop", "status": 404}])
        found = current.runtime.request("GET", "/api/workers", query=current.scope)["workers"]
        require({row["worker_id"] for row in found} == {own_worker}, "discovery exposed or adopted the sibling worker")
    return denials


def aggregate_diagnostics(root, installations, error, checks, revisions, sensitive):
    aggregate = {"passed": False, "topology": TOPOLOGY, "error": error, "checks": checks,
                 "component_revisions": revisions, "installations": {}}
    for installation in installations:
        aggregate["installations"][installation.label] = diagnostics(
            installation.root, error, checks, revisions, sensitive)
    # diagnostics() also redacts errors and labels; do not reintroduce raw text.
    encoded = json.dumps(aggregate, sort_keys=True)
    for token in sensitive:
        encoded = encoded.replace(token, "[redacted]")
    aggregate = json.loads(encoded)
    private_json(root / "diagnostics.json", aggregate)
    return aggregate


def run(args):
    trial_started = time.monotonic()
    zpm = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(zpm))
    from zpm.deployment import prepare, verify

    root = args.work_dir.absolute()
    require(not root.exists(), "trial work directory must be new")
    root.mkdir(mode=0o700)
    source_paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
    revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=15).strip()
                 for name, path in source_paths.items()}
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True, timeout=30).strip()
    require(image == args.image and image.startswith("sha256:") and len(image) == 71, "trial requires an already-present immutable Docker image ID")
    engine = subprocess.check_output(["docker", "info", "--format", "{{.ID}}"], text=True, timeout=30).strip()
    require(bool(engine), "Docker Engine identity is unavailable")
    runtime_versions = {
        "harness_python": sys.version.split()[0], "host_platform": platform.platform(),
        "docker_engine": subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True, timeout=30).strip(),
        "git": subprocess.check_output(["git", "--version"], text=True, timeout=15).strip(),
    }
    ports = set()
    while len(ports) < 4:
        ports.add(port())
    numbers = sorted(ports)
    canaries = ["isolation-" + secrets.token_urlsafe(32) for _ in range(2)]
    installations, checks, denials, evidence = [], [], [], {}
    observed_seconds = {}
    sensitive = set(canaries)
    processes = AllProcesses(installations)
    try:
        for index, label in enumerate(("a", "b")):
            installation = Installation(root, label, source_paths, revisions, image,
                                        {"polyphony": numbers[index * 2], "openjules": numbers[index * 2 + 1]},
                                        canaries[index], canaries[1 - index])
            installations.append(installation)
            sensitive.update(installation.sensitive)
        left, right = installations
        require(left.credential_values.isdisjoint(right.credential_values), "installations share a generated credential")
        for installation in installations:
            installation.sensitive = sensitive
            installation.bootstrap()
        manager_starts = {}
        for installation in installations:
            manager_starts[installation.label] = time.monotonic()
            installation.processes.start("installation", [sys.executable, str(installation.bundle / "manage.py"), "start"],
                                         installation.bundle, installation.manager_environment)
        for installation in installations:
            eventually(installation.doctor, processes, "generated doctor " + installation.label, 60)
            installation.observed_seconds["manager_start_to_observed_doctor_ready"] = elapsed(manager_starts[installation.label])
            require(installation.plan()["reserved"] == 0, "generated manager allocated capacity at a zero target")
        for installation in installations:
            installation.processes.stop("installation")
            verify(installation.bundle)
            installation.start_services(processes)
        target_starts = {}
        for installation in installations:
            target_starts[installation.label] = time.monotonic()
            installation.change("target", 1)
        initial = []
        for installation in installations:
            initial.append(wait_for_capacity(installation.plan, processes, 1))
            installation.observed_seconds["target_one_to_observed_ready"] = elapsed(target_starts[installation.label])
        left_worker, right_worker = [next(iter(live_ids(plan))) for plan in initial]
        require(left_worker != right_worker, "deployments share a worker identity")
        containers = [container_for(worker) for worker in (left_worker, right_worker)]
        require(len({item["Id"] for item in containers}) == 2 and all(item["State"]["Running"] for item in containers),
                "two actual workers are not simultaneously running on the shared engine")
        workspace_volumes = []
        for container in containers:
            mounts = [item for item in container["Mounts"] if item["Destination"] == "/workspace"]
            require(len(mounts) == 1 and mounts[0]["Type"] == "volume", "worker lacks its private workspace volume")
            workspace_volumes.append(mounts[0]["Name"])
        require(len(set(workspace_volumes)) == 2, "workers share a workspace volume")
        checks.append("two private generated bundles bootstrap, pass doctor and simultaneously own distinct real Docker workers")
        denials = denied_cross_access(left, right, left_worker, right_worker)
        checks.append("actual lifecycle and authority APIs reject sibling tokens, foreign scopes and foreign worker identities")

        right_task = right.enroll(1)
        right_attempt = eventually(lambda: right.attempt(right_task), processes, "peer canonical attempt", 20)
        right_container = containers[1]["Id"]
        eventually(lambda: progress(right_container), processes, "peer private progress barrier", 20)
        right_binding = worker_binding(initial[1]["repositories"][0]["workers"][0], containers[1], right_attempt["id"])
        right_binding["execution"] = require_uncontrolled_journals(worker_journals(right.bundle, right_worker), right_binding)
        peer_observations = []

        def fresh_peer():
            require_peer_unchanged(right.plan(), right.snapshot(), right.scope, right_worker, right_task, right_attempt["id"])
            worker = right.runtime.request("GET", "/api/workers/" + right_worker, query=right.scope)
            classification = require_peer_runtime(worker, right.scope, right_worker, right_attempt["id"])
            require_active_runtime(worker, right_binding)
            require_uncontrolled_journals(worker_journals(right.bundle, right_worker), right_binding)
            require_live_container(container_for(right_worker), right_binding)
            return {"worker_id": right_worker, "attempt_id": right_attempt["id"],
                    "container_id": right_container, "exec_id": right_binding["execution"]["exec_id"],
                    "http_state": worker["state"], "admission_open": worker["admission_open"],
                    "classification": classification, "controller_pending_operation": None, "durable_draining": False,
                    "runtime_retired": False, "drain_or_stop_operations": 0, "container_running": True}

        def peer_progress(phase):
            observed = checkpoint_progress(lambda: progress(right_container), fresh_peer, processes)
            peer_observations.append({"held_a_phase": phase, **observed})
            return observed["after"]

        # Observe B before disrupting A, so its conservative execution-lock
        # uncertainty cannot be attributed to the later neighboring outage.
        peer_before_outage = fresh_peer()
        started = time.monotonic()
        left.processes.stop("openjules", kill=True)
        def uncertain():
            plan = left.plan()
            row = plan["repositories"][0]
            return plan if row["healthy"] == 0 and row["reserved"] == 1 else None
        unknown = eventually(uncertain, processes, "A retains uncertain runtime reservation", 20)
        observed_seconds["a_runtime_outage_to_observed_uncertainty"] = elapsed(started)
        require(live_ids(unknown) == {left_worker}, "runtime outage replaced A's worker identity")
        peer_progress("runtime unavailable")
        started = time.monotonic()
        left.start("openjules")
        recovered = wait_for_capacity(left.plan, processes, 1)
        observed_seconds["a_runtime_restart_to_observed_ready"] = elapsed(started)
        require(live_ids(recovered) == {left_worker}, "runtime reconnect duplicated or adopted a worker")
        peer_progress("original idle worker reconnected")
        checks.append("A runtime outage preserves its reservation and reconnects the same idle worker while B's original command progresses")

        # Resize A while idle. Command-only active roots retain reservations but
        # can report unknown rather than healthy while their execution lock is held.
        started = time.monotonic()
        left.change("target", 2)
        scaled = wait_for_capacity(left.plan, processes, 2)
        observed_seconds["a_idle_scale_one_to_two"] = elapsed(started)
        scaled_ids = live_ids(scaled)
        require(left_worker in scaled_ids and right_worker not in scaled_ids,
                "A idle scaling replaced its original worker or adopted B")
        peer_progress("idle target two reached")
        started = time.monotonic()
        left.change("target", 1)
        reduced = wait_for_capacity(left.plan, processes, 1)
        observed_seconds["a_idle_scale_two_to_one"] = elapsed(started)
        reduced_ids = live_ids(reduced)
        require(len(reduced_ids) == 1 and reduced_ids <= scaled_ids and right_worker not in reduced_ids,
                "A idle scale-down did not retain exactly one of its verified workers")
        left_worker = next(iter(reduced_ids))
        peer_progress("idle target one reached")
        left_container_data = container_for(left_worker)
        left_container = left_container_data["Id"]
        require(left_container_data["State"]["Running"] and left_container != right_container,
                "A idle survivor is not its own running container")
        left_task = left.enroll(1)
        left_attempt = eventually(lambda: left.attempt(left_task), processes, "A canonical attempt", 20)
        eventually(lambda: progress(left_container), processes, "A private progress barrier", 20)
        require(left_attempt["runtime_binding"]["worker_id"] == left_worker, "A task adopted a different worker")
        left_binding = worker_binding(reduced["repositories"][0]["workers"][0], left_container_data, left_attempt["id"])
        left_binding["execution"] = require_uncontrolled_journals(worker_journals(left.bundle, left_worker), left_binding)
        require_active_runtime(left.runtime.request("GET", "/api/workers/" + left_worker, query=left.scope), left_binding)

        started = time.monotonic()
        left.cli("remove", left.repository)
        def removed_and_draining():
            plan = left.plan()
            require_removed_plan(plan, left_binding)
            records = worker_journals(left.bundle, left_worker)
            if not records["controls"]:
                return None
            require(len(records["controls"]) == 1, "A received more than its one owned drain intent")
            request_key = records["controls"][0]["request_key"]
            require_pending_drain_journals(records, left_binding, request_key)
            require_controller_drain(left.state, left_binding, request_key)
            worker = left.runtime.request("GET", "/api/workers/" + left_worker, query=left.scope)
            require_pending_drain_runtime(worker, left_binding)
            return request_key
        # Store clears acknowledged local action keys and has no operation
        # history. Freeze the first durable lifecycle key instead of racing a
        # transient controller field; every later observation must retain it.
        drain_key = eventually(removed_and_draining, processes, "removed A retains its owned pending drain", 25)
        observed_seconds["a_remove_to_observed_pending_active_drain"] = elapsed(started)

        def fresh_owned_drain():
            require_removed_plan(left.plan(), left_binding)
            worker = left.runtime.request("GET", "/api/workers/" + left_worker, query=left.scope)
            classification = require_pending_drain_runtime(worker, left_binding)
            require_pending_drain_journals(worker_journals(left.bundle, left_worker), left_binding, drain_key)
            require_controller_drain(left.state, left_binding, drain_key)
            require_live_container(container_for(left_worker), left_binding)
            return {"worker_id": left_worker, "attempt_id": left_attempt["id"],
                    "container_id": left_container, "exec_id": left_binding["execution"]["exec_id"],
                    "http_state": worker["state"], "admission_open": False, "classification": classification,
                    "durable_draining": True, "runtime_retired": False, "drain_completed": False,
                    "container_running": True}

        owned_drain_evidence = checkpoint_progress(lambda: progress(left_container), fresh_owned_drain, processes)
        peer_progress("removed with active pending drain")
        started = time.monotonic()
        release(left_container)
        evidence["a_first"] = left.captured(left_task, left_worker, processes)
        wait_for_capacity(left.plan, processes, 0)
        observed_seconds["a_release_to_capture_and_zero_reservations"] = elapsed(started)
        peer_progress("removed execution captured")
        checks.append("A resizes idle capacity, then removes enrollment and retains its active root under an owned pending drain until real capture while B progresses unchanged")

        canonical_before = left.snapshot()
        started = time.monotonic()
        for name in ("zpm", "openjules", "polyphony"):
            left.processes.stop(name)
        peer_progress("all services stopped")
        require(prepare(left.data, left.bundle)["reused"] is True, "A restart did not reuse its retained installation")
        require(left.sealed == {name: (left.bundle / name).read_bytes() for name in left.sealed}, "A restart replaced sealed configuration or credentials")
        left.start_services(processes)
        require(left.snapshot() == canonical_before, "A service restart changed canonical tasks or captured artifacts")
        require(not left.plan()["repositories"][0]["enabled"] and left.plan()["reserved"] == 0, "A restart silently reenrolled removed capacity")
        for digest in evidence["a_first"]["artifacts"].values():
            content = left.authority.request("GET", "/managed/artifacts/" + digest, query=left.scope, text=True)
            require(hashlib.sha256(content.encode()).hexdigest() == digest, "A restart changed captured artifact bytes")
        observed_seconds["a_full_service_restart_and_retained_evidence"] = elapsed(started)
        peer_progress("retained services restarted")
        started = time.monotonic()
        left.cli("enroll", str(left.bundle / "config/repository.json"))
        left.change("target", 1)
        resumed = wait_for_capacity(left.plan, processes, 1)
        resumed_worker = next(iter(live_ids(resumed)))
        require(resumed_worker not in {left_worker, right_worker}, "reenrollment reused a retired worker or adopted B")
        second_task = left.enroll(2, hold=False)
        evidence["a_reenrolled"] = left.captured(second_task, resumed_worker, processes)
        left.change("target", 0)
        wait_for_capacity(left.plan, processes, 0)
        observed_seconds["a_reenroll_to_capture_and_zero_reservations"] = elapsed(started)
        peer_progress("reenrolled execution captured")
        checks.append("A restarts with original credentials, removed state and canonical artifacts, then explicitly reenrolls and executes without disturbing B")

        # Real canonical foreign-attempt, task and artifact operations are
        # rejected even when the caller's local scope and token are valid.
        for current, other, task_id, attempt_id in (
            (left, right, right_task, right_attempt["id"]),
            (right, left, left_task, left_attempt["id"]),
        ):
            current.authority.request("POST", "/managed/permission",
                                      data={"schema_version": 1, **current.scope, "attempt_id": attempt_id}, expected=409)
            current.authority.request("POST", "/managed/claim", data={"schema_version": 1, **current.scope,
                                      "profile": current.profile, "worker_id": "isolation-denied-worker",
                                      "task_id": task_id, "request_key": "deny-foreign-task", "base": current.base}, expected=409)
            denials.append({"from": current.label, "to": other.label, "case": "foreign task and attempt", "status": 409})
        for digest in evidence["a_first"]["artifacts"].values():
            right.authority.request("GET", "/managed/artifacts/" + digest, query=right.scope, expected=409)
        peer_progress("foreign canonical operations denied")
        right.change("target", 0)
        release(right_container)
        evidence["b"] = right.captured(right_task, right_worker, processes)
        wait_for_capacity(right.plan, processes, 0)
        for digest in evidence["b"]["artifacts"].values():
            left.authority.request("GET", "/managed/artifacts/" + digest, query=left.scope, expected=409)
        denials.append({"case": "foreign canonical artifacts in both directions", "status": 409})
        checks.append("each real command verifies its source and synthetic secret boundary, and only its own authority exposes the captured task, attempt and artifacts")

        known = {}
        for installation in installations:
            canonical = installation.snapshot()
            expected_tasks = {left_task, second_task} if installation is left else {right_task}
            require({task["id"] for task in canonical["task"]} == expected_tasks
                    and len(canonical["attempt"]) == len(expected_tasks), "canonical scope contains unexpected tasks or attempts")
            discovered = installation.runtime.request("GET", "/api/workers", query=installation.scope)["workers"]
            require(all(all(worker.get(key) == value for key, value in installation.scope.items()) for worker in discovered),
                    "runtime discovery contains another installation's scope")
            keys = [(worker["slot_id"], worker["generation"], worker["launch_key"]) for worker in discovered]
            require(len(set(keys)) == len(keys), "runtime retained duplicate worker incarnations")
            known[installation.label] = {worker["worker_id"] for worker in discovered}
            for worker in discovered:
                container_for(worker["worker_id"])
            require(installation.plan()["reserved"] == 0, "final drain left unresolved capacity")
        require(known["a"].isdisjoint(known["b"]), "deployments adopted the same worker")
        require(subprocess.check_output(["docker", "info", "--format", "{{.ID}}"], text=True, timeout=30).strip() == engine,
                "Docker Engine changed during the isolation trial")
        checks.append("final discovery retains disjoint scoped histories with one container per worker and zero unresolved reservations")
        for installation in reversed(installations):
            installation.processes.close()
        observed_seconds["total_including_bootstrap_and_service_cleanup"] = elapsed(trial_started)
        report = {"schema_version": 1, "passed": True, "topology": TOPOLOGY, "component_revisions": revisions,
                  "docker_engine_id": engine, "image": image, "runtime_versions": runtime_versions,
                  "observed_seconds": observed_seconds, "checks": checks, "denials": denials,
                  "installations": {item.label: {**item.scope, "profile": item.profile, "source_revision": item.base,
                                               "worker_history": sorted(known[item.label]), "final_reserved": item.plan()["reserved"],
                                               "service_python_versions": item.python_versions, "observed_seconds": item.observed_seconds}
                                    for item in installations},
                  "canonical_evidence": evidence, "peer_before_a_outage": peer_before_outage,
                   "peer_progress_checkpoints": peer_observations,
                   "owned_pending_drain": {"request_key": drain_key, "key_origin": "first durable lifecycle intent; retained across later observations",
                                           "backend_delivery_confirmed": False, **owned_drain_evidence},
                   "scene_order": "B active throughout; A idle outage and reconnect, idle 1-to-2-to-1 resize, then active removal and capture",
                  "proof": "real generated installations, real HTTP and Docker, controlled commands and private fixture barriers",
                  "timing_scope": "monotonic elapsed time to observed predicates in this controlled trial; no performance threshold or child/model latency inference",
                  "not_proven": LIMITS}
        require(not any(token in json.dumps(report) for token in sensitive), "aggregate report contains a test credential")
        private_json(root / "report.json", report)
        return report
    except Exception as exc:
        error = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        diagnostic = aggregate_diagnostics(root, installations, error, checks, revisions, sensitive)
        print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
        raise
    finally:
        for installation in reversed(installations):
            installation.processes.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument(f"--{name}-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-present full sha256 image ID")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private directory retained for recovery")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"passed": False, "error": "isolation trial failed; inspect retained redacted diagnostics"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
