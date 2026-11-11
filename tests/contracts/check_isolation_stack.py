"""Qualify two generated installations sharing one real Docker Engine.

Each deployment has its own controller, OpenJules API and Polyphony authority.
The deterministic task commands use private fixture barriers, not model calls.
Retain the work directory privately; export only the aggregate report or its
bounded, credential-redacted diagnostics.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import secrets
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
    require(worker["admission_open"] and worker["state"] in {"ready", "busy"}
            and worker["deployment_id"] == scope["deployment_id"]
            and worker["repository"] == scope["repository"], "peer admission or runtime scope changed")
    require(all(snapshot.get(key) == value for key, value in scope.items()), "peer authority scope changed")
    require({task["id"] for task in snapshot["task"]} == {task_id}, "peer canonical task changed")
    attempts = snapshot["attempt"]
    require(len(attempts) == 1 and attempts[0]["id"] == attempt_id
            and attempts[0]["task"] == task_id
            and attempts[0]["runtime_binding"]["worker_id"] == worker_id
            and not attempts[0].get("termination_confirmed"), "peer execution was replaced or terminated")


def require_peer_runtime(worker, scope, worker_id, attempt_id):
    require(worker.get("worker_id") == worker_id and all(worker.get(key) == value for key, value in scope.items()),
            "fresh peer runtime observation changed identity or scope")
    require(worker.get("admission_open") is True and worker.get("state") in {"ready", "busy"}
            and worker.get("active_executions") == [attempt_id] and not worker.get("termination_confirmed"),
            "fresh peer runtime observation lost admission or the original active attempt")


def checkpoint_progress(read_counter, check_peer, processes):
    """Take the baseline only after entering the caller's held A phase."""
    check_peer()
    before = read_counter()
    require(type(before) is int and before > 0, "peer has no current progress baseline")

    def advanced():
        value = read_counter()
        return value if type(value) is int and value > before else None

    after = eventually(advanced, processes, "peer advances inside the current held phase", 15)
    check_peer()
    return {"before": before, "after": after}


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
        peer_observations = []

        def peer_progress(phase):
            def fresh_peer():
                require_peer_unchanged(right.plan(), right.snapshot(), right.scope, right_worker, right_task, right_attempt["id"])
                worker = right.runtime.request("GET", "/api/workers/" + right_worker, query=right.scope)
                require_peer_runtime(worker, right.scope, right_worker, right_attempt["id"])
            observed = checkpoint_progress(lambda: progress(right_container), fresh_peer, processes)
            peer_observations.append({"held_a_phase": phase, **observed})
            return observed["after"]

        # The peer is already executing when A loses its runtime service.
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
        peer_progress("original worker reconnected")
        checks.append("A runtime outage preserves its reservation and reconnects the same worker while B's original command progresses")

        left_task = left.enroll(1)
        left_attempt = eventually(lambda: left.attempt(left_task), processes, "A canonical attempt", 20)
        left_container = containers[0]["Id"]
        eventually(lambda: progress(left_container), processes, "A private progress barrier", 20)
        require(left_attempt["runtime_binding"]["worker_id"] == left_worker, "A task adopted a different worker")
        started = time.monotonic()
        left.change("target", 2)
        scaled = wait_for_capacity(left.plan, processes, 2)
        observed_seconds["a_scale_one_to_two"] = elapsed(started)
        require(left_worker in live_ids(scaled) and right_worker not in live_ids(scaled), "A scaling replaced its active worker or adopted B")
        peer_progress("target two reached")
        started = time.monotonic()
        left.change("target", 1)
        reduced = wait_for_capacity(left.plan, processes, 1)
        observed_seconds["a_scale_two_to_one"] = elapsed(started)
        require(live_ids(reduced) == {left_worker}, "A scale-down did not preserve its active worker")
        peer_progress("target one reached")
        started = time.monotonic()
        left.cli("remove", left.repository)
        def removed_and_draining():
            plan = left.plan()
            row = plan["repositories"][0]
            workers = row["workers"]
            return plan if not row["enabled"] and row["effective_target"] == 0 and row["reserved"] == 1 and len(workers) == 1 \
                and workers[0]["state"] == "draining" and not workers[0]["admission_open"] \
                and left_attempt["id"] in workers[0]["active_executions"] else None
        draining = eventually(removed_and_draining, processes, "removed A retains its draining active execution", 25)
        observed_seconds["a_remove_to_observed_active_drain"] = elapsed(started)
        require(live_ids(draining) == {left_worker}, "removal changed A's unresolved worker identity")
        peer_progress("removed with active drain")
        started = time.monotonic()
        release(left_container)
        evidence["a_first"] = left.captured(left_task, left_worker, processes)
        wait_for_capacity(left.plan, processes, 0)
        observed_seconds["a_release_to_capture_and_zero_reservations"] = elapsed(started)
        peer_progress("removed execution captured")
        checks.append("A scales independently, removes enrollment, closes admission and retains its active root until real capture while B progresses unchanged")

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
                  "canonical_evidence": evidence, "peer_progress_checkpoints": peer_observations,
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
