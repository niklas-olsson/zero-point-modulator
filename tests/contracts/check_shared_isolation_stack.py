"""Qualify two repositories in one generated schema-2 installation with Docker.

One controller and one OpenJules/Polyphony service pair share their failure
domain. Controlled commands establish execution and scope evidence, not live
inference, independent semantic review or forge publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import time

from check_stack import Client, Processes, clean_environment, diagnostics, eventually, port, private_json, require, wait_for_capacity
from check_isolation_stack import (
    TASK_PROGRAM, checkpoint_progress, container_for, elapsed, live_ids,
    progress, release, require_active_runtime, require_controller_drain,
    require_live_container, require_peer_runtime, require_peer_unchanged,
    require_pending_drain_journals, require_pending_drain_runtime,
    require_removed_plan, require_uncontrolled_journals, supplied_environment,
    wait_for_capture, worker_binding, worker_journals,
)


TOPOLOGY = "one generated schema-2 deployment, two repositories, one controller and service pair, one Docker Engine"
LIMITS = [
    "mutually hostile host services with Docker authority",
    "repository-local isolation from a shared service, authority or Docker outage",
    "wall-clock scheduling fairness or a 100-repository workload",
    "multiple deployments behind one OpenJules API",
    "OpenJules GitHub event delivery or webhook ingress",
    "simultaneous ingress-process delivery",
    "live model inference", "independent semantic review", "forge publication",
]


def repository_plan(plan, deployment, profiles, repository, maximum=3):
    """Validate the entire shared plan before narrowing existing witnesses."""
    require(plan.get("deployment_id") == deployment and plan.get("max_workers") == maximum,
            "shared deployment or capacity ceiling changed")
    rows = plan.get("repositories")
    require(isinstance(rows, list) and len(rows) == 2 and len(profiles) == 2
            and {row.get("repository") for row in rows} == set(profiles),
            "shared plan lost, duplicated or added a repository")
    worker_ids, launch_keys, incarnations = set(), set(), set()
    reserved = 0
    for row in rows:
        name = row["repository"]
        require(row.get("profile") == profiles[name], "shared plan changed an approved profile")
        workers = row.get("workers")
        require(isinstance(workers, list) and type(row.get("reserved")) is int
                and row["reserved"] == len(workers), "repository reservation count does not match its workers")
        reserved += row["reserved"]
        for worker in workers:
            require(worker.get("deployment_id") == deployment and worker.get("repository") == name
                    and worker.get("profile") == profiles[name], "shared plan contains a foreign worker binding")
            key = worker.get("launch_key")
            incarnation = (name, worker.get("slot_id"), worker.get("generation"))
            require(isinstance(key, str) and key and key not in launch_keys and incarnation not in incarnations,
                    "shared plan reused a launch key or worker incarnation")
            launch_keys.add(key)
            incarnations.add(incarnation)
            identity = worker.get("worker_id")
            if identity == "":
                require(worker.get("pending_operation") in {"reserve", "ensure"}
                        and worker.get("state") in {"starting", "unknown"}
                        and worker.get("termination_confirmed") is False and worker.get("artifacts_captured") is False
                        and worker.get("admission_open") is False and worker.get("active_executions") == [],
                        "unbound shared reservation lacks a pending launch")
            else:
                require(isinstance(identity, str) and identity and identity not in worker_ids,
                        "shared plan reused a worker across reservations")
                worker_ids.add(identity)
    require(type(plan.get("reserved")) is int and plan["reserved"] == reserved <= maximum,
            "shared reservation total is inconsistent or exceeds the deployment ceiling")
    selected = next(row for row in rows if row["repository"] == repository)
    return plan | {"reserved": selected["reserved"], "repositories": [selected]}


def require_private_workspaces(containers):
    require(len(containers) == 2 and len({row["Id"] for row in containers.values()}) == 2
            and all(row["State"]["Running"] for row in containers.values()),
            "shared service pair did not start two distinct real containers")
    volumes, sources = {}, {}
    for label, container in containers.items():
        require(all(row["Type"] != "tmpfs" or row["Destination"] in {"/tmp", "/control"} for row in container["Mounts"]),
                "command worker mounted an unexpected temporary path")
        mounts = [row for row in container["Mounts"] if row["Type"] != "tmpfs"]
        require(len(mounts) == 2 and {row["Destination"] for row in mounts} == {"/source", "/workspace"},
                "command worker mounted an unexpected host or sibling path")
        workspace = next(row for row in mounts if row["Destination"] == "/workspace")
        source = next(row for row in mounts if row["Destination"] == "/source")
        require(workspace["Type"] == "volume" and workspace["RW"] is True
                and source["Type"] == "bind" and source["RW"] is False,
                "worker lacks a private workspace or read-only source mount")
        volumes[label], sources[label] = workspace["Name"], source["Source"]
    require(len(set(volumes.values())) == 2 and len(set(sources.values())) == 2,
            "repository workers share a workspace volume or source snapshot")


def require_retained_histories(known, left_workers, right_worker):
    require(bool(left_workers) and left_workers <= known["a"] and known["b"] == {right_worker},
            "shared restart lost an observed worker or replaced B's retained history")
    require(known["a"].isdisjoint(known["b"]), "shared runtime adopted a worker across scopes")


class Repository:
    def __init__(self, installation, label, canary, other_canary):
        self.installation, self.label = installation, label
        self.deployment, self.bundle = installation.deployment, installation.bundle
        self.repository = "github:github.com/managed-shared-isolation/repository-" + label
        self.profile = "controlled-shared-isolation-" + label
        self.scope = {"deployment_id": self.deployment, "repository": self.repository}
        self.source = installation.root / ("source-" + label)
        self.source.mkdir(mode=0o755)
        for name, content in {
            "identity.txt": label + "\n", label + "-only.txt": "source " + label + "\n",
            "answer.txt": "initial " + label + "\n", "run_trial.py": TASK_PROGRAM,
            ".gitignore": ".env\n.isolation-*\n",
        }.items():
            (self.source / name).write_text(content)
        for command in (["init", "-q"], ["add", "."],
                        ["-c", "user.name=Shared Isolation Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled repository " + label]):
            subprocess.run(["git", "-C", str(self.source), *command], check=True, capture_output=True, timeout=15)
        self.base = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
        self.host_canary = secrets.token_urlsafe(32)
        (self.source / ".env").write_text("HOST_ONLY_ISOLATION_CANARY=" + self.host_canary + "\n")
        self.secret_reference = "ZPM_ISOLATION_" + label.upper() + "_" + secrets.token_hex(8).upper()
        self.configuration = {
            "binding": {"schema_version": 1, "repository": self.repository, "profile": self.profile, "desired_workers": 0},
            "source": {"checkout": str(self.source), "revision": self.base}, "image": installation.image,
            "command": ["python3", "-I", "-S", "run_trial.py", label,
                        hashlib.sha256(canary.encode()).hexdigest(), hashlib.sha256(other_canary.encode()).hexdigest(),
                        str(self.bundle), str(installation.root / "source-a"), str(installation.root / "source-b")],
            "runtime": {"max_seconds": 300, "pids_limit": 64},
            "environment_refs": {"OPENAI_API_KEY": "env:" + self.secret_reference},
            "policy": {"checks": ["controlled-isolation"], "reviewers": ["operator"], "max_attempts": 1,
                       "budget_seconds": 400, "attempt_seconds": 270, "capabilities": ["python"]},
        }

    def plan(self):
        return repository_plan(self.installation.plan(), self.deployment, self.installation.profiles, self.repository)

    def change(self, operation, count=None):
        command = [str(self.bundle / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(self.installation.state),
                   "--deployment-id", self.deployment, operation, self.repository]
        if count is not None:
            command.append(str(count))
        self.installation.processes.run_once(operation, command, self.installation.source_paths["zpm"], clean_environment(), 30)

    def reenroll(self):
        command = [str(self.bundle / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(self.installation.state),
                   "--deployment-id", self.deployment, "enroll", str(self.bundle / self.binding_file)]
        self.installation.processes.run_once("reenroll", command, self.installation.source_paths["zpm"], clean_environment(), 30)

    def snapshot(self):
        return self.installation.authority.request("GET", "/managed/snapshot", query=self.scope)

    def enroll(self, number, hold=True):
        """Deliver the same source request from two separate installed CLI processes."""
        requirements = self.installation.root / ("requirements-" + self.label + "-" + str(number) + ".txt")
        requirements.write_text("Verify this repository and change answer.txt. "
                                + ("HOLD_FOR_ISOLATION_TRIAL" if hold else "Complete normally."))
        requirements.chmod(0o600)
        command = [str(self.bundle / "environments/polyphony/bin/python"), "-m", "src.liminal_bridge.managed_cli", "enroll",
                   "--url", self.installation.authority_endpoint, "--deployment-id", self.deployment,
                   "--repository", self.repository, "--source", "github:issue:" + str(number),
                   "--request-key", "controlled-shared-" + str(number), "--title", "Repository isolation " + self.label,
                   "--requirements-file", str(requirements)]
        receipts = []
        for delivery in ("first", "replay"):
            name = "ingress-" + self.label + "-" + str(number) + "-" + delivery
            self.installation.processes.run_once(name, command, self.installation.source_paths["polyphony"],
                                                clean_environment({"POLYPHONY_INGRESS_TOKEN": self.tokens["ingress"]}), 30)
            receipt = json.loads((self.installation.root / (name + ".log")).read_text())
            require(all(receipt.get(key) == value for key, value in self.scope.items()), "ingress receipt changed repository scope")
            receipts.append(receipt["task"]["id"])
        require(receipts[0] == receipts[1], "cross-process source replay created a duplicate task")
        return receipts[0]

    def attempt(self, task_id):
        attempts = [row for row in self.snapshot()["attempt"] if row["task"] == task_id]
        require(len(attempts) <= 1, "canonical task has duplicate attempts")
        return attempts[0] if attempts and attempts[0].get("runtime_binding") else None

    def captured(self, task_id, worker_id):
        attempt, candidate = wait_for_capture(self.snapshot, self.scope, task_id, worker_id, self.base,
                                              self.installation.processes, self.label)
        contents = {}
        for kind, digest in attempt["captured_artifacts"].items():
            content = self.installation.authority.request("GET", "/managed/artifacts/" + digest, query=self.scope, text=True)
            require(hashlib.sha256(content.encode()).hexdigest() == digest, "canonical artifact hash mismatch")
            require(not any(token in content for token in self.installation.sensitive), "canonical artifact exposed a test credential")
            contents[kind] = content
        require("+completed " + self.label in contents["patch"], "captured patch lacks this repository's change")
        require("ISOLATION_BOUNDARY_VERIFIED_" + self.label in contents["log"]
                and "ISOLATION_COMPLETED_" + self.label in contents["log"], "controlled boundary or completion assertion failed")
        require((self.source / "answer.txt").read_text() == "initial " + self.label + "\n", "task modified its approved source")
        return {"task": task_id, "attempt": attempt["id"], "worker": worker_id,
                "candidate": candidate["id"], "artifacts": attempt["captured_artifacts"]}


class Installation:
    def __init__(self, root, source_paths, revisions, image, ports):
        from zpm.deployment import _command, _environment, prepare, verify

        self.root, self.source_paths, self.image = root, source_paths, image
        self.bundle = root / "installation"
        self.deployment = "shared-isolation-" + secrets.token_hex(8)
        canaries = ["shared-isolation-" + secrets.token_urlsafe(32) for _ in range(2)]
        self.repositories = [Repository(self, label, canaries[index], canaries[1 - index]) for index, label in enumerate(("a", "b"))]
        self.profiles = {item.repository: item.profile for item in self.repositories}
        self.data = {
            "schema_version": 2, "deployment_id": self.deployment, "max_workers": 3,
            "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in source_paths.items()},
            "ports": ports, "repositories": [item.configuration for item in self.repositories],
        }
        started = time.monotonic()
        prepared = prepare(self.data, self.bundle)
        verify(self.bundle)
        self.observed_seconds = {"prepare_and_verify": elapsed(started)}
        self.data = json.loads((self.bundle / "manifest.json").read_text())["configuration"]
        self.state = self.bundle / "state/zpm.sqlite3"
        environment = json.loads((self.bundle / "secrets/environment.json").read_text())
        operator = json.loads((self.bundle / "secrets/operator.json").read_text())
        require(operator["schema_version"] == 2 and set(operator["repositories"]) == set(self.profiles), "generated credential map changed scope")
        credentials = [*environment.values(), operator["owner"]]
        for item in self.repositories:
            item.tokens = operator["repositories"][item.repository]
            credentials.extend(item.tokens[role] for role in ("ingress", "reviewer"))
            item.binding_file = next(row["binding_file"] for row in prepared["repositories"] if row["repository"] == item.repository)
        require(len(credentials) == len(set(credentials)) == 7, "generated roles or repositories share credentials")
        self.sensitive = set(credentials) | set(canaries) | {item.host_canary for item in self.repositories}
        self.sealed = {str(path.relative_to(self.bundle)): path.read_bytes() for path in
                       [self.bundle / "manifest.json", *sorted((self.bundle / "secrets").iterdir()), *sorted((self.bundle / "config").iterdir())]}
        self.authority_endpoint = f"http://127.0.0.1:{ports['polyphony']}"
        self.runtime_endpoint = f"http://127.0.0.1:{ports['openjules']}"
        self.authority = Client(self.authority_endpoint, environment["POLYPHONY_RUNTIME_TOKEN"])
        self.runtime = Client(self.runtime_endpoint, environment["OPENJULES_WORKER_TOKEN"])
        supplied = {item.secret_reference: canaries[index] for index, item in enumerate(self.repositories)}
        self.manager_environment = clean_environment(supplied)
        self.commands = {name: _command(self.bundle, name, self.data) for name in source_paths}
        with supplied_environment(supplied):
            self.environments = {name: _environment(self.bundle, name, self.data) for name in source_paths}
        self.processes = Processes(root)

    def plan(self):
        from zpm.controller import Controller
        from zpm.store import Store
        with Store(self.state, self.deployment) as store:
            plan = Controller(store, max_workers=self.data["max_workers"]).plan()
        repository_plan(plan, self.deployment, self.profiles, self.repositories[0].repository)
        return plan

    def bootstrap(self):
        started = time.monotonic()
        self.processes.run_once("bootstrap", [sys.executable, str(self.bundle / "manage.py"), "bootstrap"], self.bundle, clean_environment(), 900)
        self.observed_seconds["bootstrap"] = elapsed(started)
        self.python_versions = {name: subprocess.check_output([str(self.bundle / "environments" / name / "bin/python"), "--version"],
                                                             env=clean_environment(), text=True, timeout=15).strip() for name in self.source_paths}

    def doctor(self):
        result = subprocess.run([sys.executable, str(self.bundle / "manage.py"), "doctor"], cwd=self.bundle,
                                env=clean_environment(), capture_output=True, text=True, timeout=50)
        require(result.returncode in {0, 3}, "generated doctor could not inspect the shared installation")
        return json.loads(result.stdout).get("ready") is True

    def start_services(self):
        for name in ("polyphony", "openjules", "zpm"):
            self.processes.start(name, self.commands[name], self.source_paths[name], self.environments[name])
            if name == "zpm":
                continue
            for item in self.repositories:
                client, path = ((self.authority, "/managed/capabilities") if name == "polyphony"
                                else (self.runtime, "/api/workers/capabilities"))
                eventually(lambda: client.request("GET", path, query=item.scope | {"profile": item.profile}).get("managed_dispatch"),
                           self.processes, name + " capability " + item.label, 30)

    def require_sealed(self):
        require(self.sealed == {name: (self.bundle / name).read_bytes() for name in self.sealed},
                "shared installation changed sealed configuration or credentials")


def denied_cross_access(installation, workers):
    denials = []
    left, right = installation.repositories
    for current, other in ((left, right), (right, left)):
        for role in ("ingress", "reviewer"):
            client = Client(installation.authority_endpoint, current.tokens[role])
            snapshot = client.request("GET", "/managed/snapshot", query=current.scope)
            require(all(snapshot.get(key) == value for key, value in current.scope.items()), "scoped principal received a foreign snapshot")
            client.request("GET", "/managed/snapshot", query=other.scope, expected=403)
            denials.append({"from": current.label, "to": other.label, "case": role + " foreign repository", "status": 403})
        Client(installation.authority_endpoint, current.tokens["ingress"]).request(
            "POST", "/managed/enroll", expected=403, data={"schema_version": 1, **other.scope, "source": "github:issue:denied",
            "request_key": "denied-shared-ingress", "title": "Denied scope trial", "requirements": "Must not create a task."})
        foreign = workers[other.label]
        installation.runtime.request("GET", "/api/workers/" + foreign, query=current.scope, expected=404)
        for action in ("drain", "stop"):
            installation.runtime.request("POST", "/api/workers/" + foreign + "/" + action, expected=404,
                                         data={"schema_version": 1, **current.scope, "request_key": "deny-foreign-" + action})
        found = installation.runtime.request("GET", "/api/workers", query=current.scope)["workers"]
        require({row["worker_id"] for row in found} == {workers[current.label]}
                and all(all(row.get(key) == value for key, value in current.scope.items()) for row in found),
                "shared runtime discovery exposed another scope")
        denials.append({"from": current.label, "to": other.label, "case": "foreign worker read, drain and stop", "status": 404})
    unknown = left.scope | {"repository": "github:github.com/managed-shared-isolation/unconfigured"}
    installation.runtime.request("GET", "/api/workers", query=unknown, expected=403)
    installation.authority.request("GET", "/managed/snapshot", query=unknown, expected=403)
    denials.append({"case": "unconfigured repository with shared host credentials", "status": 403})
    return denials


def run(args):
    from zpm.deployment import prepare, verify

    trial_started = time.monotonic()
    root = args.work_dir.absolute()
    require(not root.exists(), "trial work directory must be new")
    root.mkdir(mode=0o700)
    source_paths = {"zpm": Path(__file__).resolve().parents[2], "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
    revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=15).strip()
                 for name, path in source_paths.items()}
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True, timeout=30).strip()
    require(image == args.image and image.startswith("sha256:") and len(image) == 71, "trial requires an already-present immutable Docker image ID")
    engine = subprocess.check_output(["docker", "info", "--format", "{{.ID}}"], text=True, timeout=30).strip()
    require(bool(engine), "Docker Engine identity is unavailable")
    versions = {"harness_python": sys.version.split()[0], "host_platform": platform.platform(),
                "docker_engine": subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True, timeout=30).strip(),
                "git": subprocess.check_output(["git", "--version"], text=True, timeout=15).strip()}
    ports = set()
    while len(ports) < 2:
        ports.add(port())
    numbers = sorted(ports)
    installation, checks, evidence, observations = None, [], {}, []
    sensitive, timings = set(), {}
    try:
        installation = Installation(root, source_paths, revisions, image, {"polyphony": numbers[0], "openjules": numbers[1]})
        sensitive = installation.sensitive
        processes = installation.processes
        left, right = installation.repositories
        installation.bootstrap()
        started = time.monotonic()
        processes.start("installation", [sys.executable, str(installation.bundle / "manage.py"), "start"],
                        installation.bundle, installation.manager_environment)
        eventually(installation.doctor, processes, "generated shared doctor", 60)
        require(installation.plan()["reserved"] == 0, "generated manager allocated capacity at zero targets")
        timings["manager_start_to_observed_doctor_ready"] = elapsed(started)
        processes.stop("installation")
        verify(installation.bundle)
        installation.start_services()
        for item in installation.repositories:
            item.change("target", 1)
        initial = {item.label: wait_for_capacity(item.plan, processes, 1) for item in installation.repositories}
        workers = {label: next(iter(live_ids(plan))) for label, plan in initial.items()}
        containers = {label: container_for(worker) for label, worker in workers.items()}
        require_private_workspaces(containers)
        require(installation.plan()["reserved"] == 2, "shared controller did not retain both initial reservations")
        denials = denied_cross_access(installation, workers)
        checks.append("one schema-2 bundle bootstraps and starts both scoped profiles with distinct credentials, workers and workspace volumes")
        checks.append("shared host tokens authorize both configured scopes; repository credentials and foreign-object requests enforce their scope")

        right_task = right.enroll(1)
        right_attempt = eventually(lambda: right.attempt(right_task), processes, "original B attempt", 20)
        right_worker, right_container = workers["b"], containers["b"]["Id"]
        eventually(lambda: progress(right_container), processes, "B private progress barrier", 20)
        right_binding = worker_binding(initial["b"]["repositories"][0]["workers"][0], containers["b"], right_attempt["id"])
        right_binding["execution"] = require_uncontrolled_journals(worker_journals(installation.bundle, right_worker), right_binding)

        def fresh_peer():
            shared = installation.plan()
            plan = repository_plan(shared, installation.deployment, installation.profiles, right.repository)
            require_peer_unchanged(plan, right.snapshot(), right.scope, right_worker, right_task, right_attempt["id"])
            worker = installation.runtime.request("GET", "/api/workers/" + right_worker, query=right.scope)
            classification = require_peer_runtime(worker, right.scope, right_worker, right_attempt["id"])
            require_active_runtime(worker, right_binding)
            require_uncontrolled_journals(worker_journals(installation.bundle, right_worker), right_binding)
            require_live_container(container_for(right_worker), right_binding)
            return {"task_id": right_task, "worker_id": right_worker, "attempt_id": right_attempt["id"],
                    "container_id": right_container, "exec_id": right_binding["execution"]["exec_id"],
                    "shared_reserved": shared["reserved"], "b_reserved": plan["reserved"], "classification": classification,
                    "controller_pending_operation": None, "durable_draining": False, "drain_or_stop_operations": 0,
                    "container_running": True, "runtime_retired": False}

        def peer_progress(phase):
            observations.append({"held_a_phase": phase, **checkpoint_progress(lambda: progress(right_container), fresh_peer, processes)})

        peer_progress("initial idle A")
        started = time.monotonic()
        left.change("target", 2)
        scaled = wait_for_capacity(left.plan, processes, 2)
        require(workers["a"] in live_ids(scaled) and installation.plan()["reserved"] == 3, "A scaling replaced its original worker or lost a shared reservation")
        peer_progress("idle target two reached")
        left.change("target", 1)
        reduced = wait_for_capacity(left.plan, processes, 1)
        require(live_ids(reduced) <= live_ids(scaled) and installation.plan()["reserved"] == 2, "A scale-down changed worker ownership or shared reservations")
        left_worker = next(iter(live_ids(reduced)))
        peer_progress("idle target one reached")
        timings["a_idle_scale_one_to_two_to_one"] = elapsed(started)
        left_task = left.enroll(1)
        require(left_task != right_task, "identical source item and request key collided across repositories")
        left_attempt = eventually(lambda: left.attempt(left_task), processes, "original A attempt", 20)
        left_container_data = container_for(left_worker)
        left_container = left_container_data["Id"]
        eventually(lambda: progress(left_container), processes, "A private progress barrier", 20)
        left_binding = worker_binding(reduced["repositories"][0]["workers"][0], left_container_data, left_attempt["id"])
        left_binding["execution"] = require_uncontrolled_journals(worker_journals(installation.bundle, left_worker), left_binding)
        peer_progress("both original tasks active")

        started = time.monotonic()
        left.change("remove")

        def removed_and_draining():
            require_removed_plan(left.plan(), left_binding)
            records = worker_journals(installation.bundle, left_worker)
            if not records["controls"]:
                return None
            require(len(records["controls"]) == 1, "A received more than its one owned drain intent")
            key = records["controls"][0]["request_key"]
            require_pending_drain_journals(records, left_binding, key)
            require_controller_drain(installation.state, left_binding, key)
            require_pending_drain_runtime(installation.runtime.request("GET", "/api/workers/" + left_worker, query=left.scope), left_binding)
            require(installation.plan()["reserved"] == 2, "active removal released or added a shared reservation")
            return key

        drain_key = eventually(removed_and_draining, processes, "A owned pending drain", 25)
        timings["a_remove_to_observed_pending_active_drain"] = elapsed(started)

        def fresh_owned_drain():
            require(removed_and_draining() == drain_key, "A replaced its original drain key")
            require_live_container(container_for(left_worker), left_binding)
            return {"worker_id": left_worker, "attempt_id": left_attempt["id"], "container_id": left_container,
                    "exec_id": left_binding["execution"]["exec_id"], "durable_draining": True,
                    "drain_completed": False, "container_running": True, "shared_reserved": 2}

        owned_drain = checkpoint_progress(lambda: progress(left_container), fresh_owned_drain, processes)
        peer_progress("removed A with owned active pending drain")
        started = time.monotonic()
        release(left_container)
        evidence["a_first"] = left.captured(left_task, left_worker)
        wait_for_capacity(left.plan, processes, 0)
        require(installation.plan()["reserved"] == 1, "A capture released B or retained finished A capacity")
        peer_progress("removed A captured with only B reserved")
        require(prepare(installation.data, installation.bundle)["reused"] is True, "repeat preparation did not reuse the shared installation")
        installation.require_sealed()
        require(not left.plan()["repositories"][0]["enabled"], "repeat preparation silently reenrolled A")
        peer_progress("retained bundle reused without reenrollment")
        timings["a_release_to_capture_and_retained_preparation"] = elapsed(started)
        started = time.monotonic()
        left.reenroll()
        left.change("target", 1)
        resumed = wait_for_capacity(left.plan, processes, 1)
        resumed_worker = next(iter(live_ids(resumed)))
        require(resumed_worker not in live_ids(scaled) | {right_worker}, "reenrollment reused a retired worker or adopted B")
        peer_progress("A explicitly reenrolled with a new worker")
        second_task = left.enroll(2, hold=False)
        evidence["a_reenrolled"] = left.captured(second_task, resumed_worker)
        left.change("target", 0)
        wait_for_capacity(left.plan, processes, 0)
        peer_progress("reenrolled A captured and target zero")
        timings["a_reenroll_to_capture_and_zero_reservations"] = elapsed(started)
        checks.append("B retains its original task, attempt, worker, profile, Docker container and exec with fresh progress across A target, removal, capture and reenrollment phases")
        checks.append("removed A retains its exact pending drain and shared reservation until actual terminal capture; reenrollment creates a new worker")
        checks.append("separate installed ingress CLI processes deduplicate the same source request within each scope; identical source and key across repositories remain distinct tasks")

        for current, other, task, attempt in ((left, right, right_task, right_attempt["id"]), (right, left, left_task, left_attempt["id"])):
            installation.authority.request("POST", "/managed/permission", expected=409,
                                           data={"schema_version": 1, **current.scope, "attempt_id": attempt})
            installation.authority.request("POST", "/managed/claim", expected=409, data={"schema_version": 1, **current.scope,
                                           "profile": current.profile, "worker_id": "shared-isolation-denied-worker", "task_id": task,
                                           "request_key": "deny-foreign-task", "base": current.base})
            denials.append({"from": current.label, "to": other.label, "case": "foreign task and attempt", "status": 409})
        for digest in evidence["a_first"]["artifacts"].values():
            installation.authority.request("GET", "/managed/artifacts/" + digest, query=right.scope, expected=409)
        peer_progress("foreign canonical operations denied")
        right.change("target", 0)
        release(right_container)
        evidence["b"] = right.captured(right_task, right_worker)
        wait_for_capacity(right.plan, processes, 0)
        for digest in evidence["b"]["artifacts"].values():
            installation.authority.request("GET", "/managed/artifacts/" + digest, query=left.scope, expected=409)
        denials.append({"case": "foreign canonical artifacts in both directions", "status": 409})

        snapshots = {item.label: item.snapshot() for item in installation.repositories}
        for item, tasks in ((left, {left_task, second_task}), (right, {right_task})):
            require({row["id"] for row in snapshots[item.label]["task"]} == tasks
                    and len(snapshots[item.label]["attempt"]) == len(tasks), "canonical scope contains unexpected tasks or attempts")
        before_restart = installation.plan()
        require(before_restart["reserved"] == 0, "final drain left unresolved shared capacity")
        started = time.monotonic()
        for name in ("zpm", "openjules", "polyphony"):
            processes.stop(name)
        verify(installation.bundle)
        installation.require_sealed()
        installation.start_services()
        for item in installation.repositories:
            require(item.snapshot() == snapshots[item.label], "shared service restart changed canonical tasks or capture")
            for record in evidence.values():
                if record["task"] in {row["id"] for row in snapshots[item.label]["task"]}:
                    for digest in record["artifacts"].values():
                        content = installation.authority.request("GET", "/managed/artifacts/" + digest, query=item.scope, text=True)
                        require(hashlib.sha256(content.encode()).hexdigest() == digest, "shared restart changed canonical artifact bytes")
        after_restart = installation.plan()
        enrollment_fields = ("repository", "profile", "desired_workers", "effective_target", "enabled", "paused")
        require(after_restart["reserved"] == 0
                and [{key: row[key] for key in enrollment_fields} for row in before_restart["repositories"]]
                == [{key: row[key] for key in enrollment_fields} for row in after_restart["repositories"]],
                "shared restart recreated drained capacity or changed retained enrollment")
        timings["shared_restart_after_capture_and_retained_evidence"] = elapsed(started)
        known = {}
        for item in installation.repositories:
            found = installation.runtime.request("GET", "/api/workers", query=item.scope)["workers"]
            require(all(all(row.get(key) == value for key, value in item.scope.items()) for row in found), "retained discovery contains a foreign scope")
            known[item.label] = {row["worker_id"] for row in found}
            require(len(known[item.label]) == len(found), "retained discovery duplicated a worker")
            for worker in found:
                container_for(worker["worker_id"])
        require_retained_histories(known, live_ids(scaled) | {resumed_worker}, right_worker)
        require(subprocess.check_output(["docker", "info", "--format", "{{.ID}}"], text=True, timeout=30).strip() == engine,
                "Docker Engine changed during the shared isolation trial")
        checks.append("both scopes retain exact canonical captures after a shared restart with zero unresolved reservations and disjoint worker histories")
        processes.close()
        timings["total_including_bootstrap_and_service_cleanup"] = elapsed(trial_started)
        report = {"schema_version": 1, "passed": True, "topology": TOPOLOGY, "component_revisions": revisions,
                  "deployment_id": installation.deployment, "image": image, "docker_engine_id": engine,
                  "runtime_versions": versions, "service_python_versions": installation.python_versions,
                  "observed_seconds": installation.observed_seconds | timings, "checks": checks, "denials": denials,
                  "repositories": {item.label: {**item.scope, "profile": item.profile, "source_revision": item.base,
                                               "binding_file": item.binding_file, "worker_history": sorted(known[item.label])} for item in installation.repositories},
                  "canonical_evidence": evidence, "peer_progress_checkpoints": observations,
                  "ingress": {"implementation": "src.liminal_bridge.managed_cli", "separate_processes": 6,
                              "deliveries_per_request": 2, "delivery_order": "sequential separate processes",
                              "cross_repository_source": "github:issue:1",
                              "cross_repository_request_key": "controlled-shared-1", "github_event_ingress_exercised": False},
                  "owned_pending_drain": {"request_key": drain_key, "backend_delivery_confirmed": False, **owned_drain},
                  "final_reserved": 0, "max_workers": 3,
                  "scene_order": "B active throughout A idle 1-to-2-to-1 resize, active removal/capture and explicit reenrollment; shared restart only after both capture",
                  "proof": "real generated schema-2 installation, real HTTP and Docker, controlled commands and private fixture barriers",
                  "timing_scope": "monotonic elapsed time to observed predicates; no performance threshold or model latency inference",
                  "not_proven": LIMITS}
        require(not any(token in json.dumps(report) for token in sensitive), "aggregate report contains a test credential")
        private_json(root / "report.json", report)
        return report
    except Exception as exc:
        error = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        print(json.dumps(diagnostics(root, error, checks, revisions, sensitive), sort_keys=True), file=sys.stderr)
        raise
    finally:
        if installation is not None:
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"passed": False, "error": "shared isolation trial failed; inspect retained redacted diagnostics"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
