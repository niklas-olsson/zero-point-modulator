"""Real Docker and three-process managed contract trial, without model calls.

The explicit work directory is retained on success and failure. It contains
private test credentials and runtime recovery state; do not upload it wholesale.
No production repository, credential or task is used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def require(value, message):
    if not value:
        raise AssertionError(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class Client:
    def __init__(self, endpoint, token):
        self.endpoint, self.token = endpoint, token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, *, query=None, data=None, expected=200, text=False):
        url = self.endpoint + path + ("?" + urlencode(query) if query else "")
        request = Request(url, method=method, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=3) as response:
                status, payload = response.status, response.read(5_000_001)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            require(status == expected, f"unexpected HTTP {status} from {path}")
            return None
        require(status == expected and len(payload) <= 5_000_000, f"unexpected response from {path}")
        return payload.decode("utf-8") if text else json.loads(payload)


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)


def clean_environment(extra=None):
    environment = {name: value for name, value in os.environ.items() if name in {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    return environment | {"PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1"} | (extra or {})


def bootstrap_command(bundle, trial_root, label, *, deny_network):
    command = [sys.executable, str(bundle / "manage.py"), "bootstrap"]
    if not deny_network:
        return command
    require(os.getuid() > 0 and os.getgid() > 0, "sealed network trial requires an ordinary runner user")
    for executable in ("/usr/bin/sudo", "/usr/bin/unshare", "/usr/bin/setpriv"):
        require(Path(executable).is_file(), "sealed network trial requires sudo, unshare and setpriv")
    helper = Path(__file__).with_name("denied_bootstrap.py").resolve()
    # Only trusted system tools execute before the UID, groups and all process
    # capabilities have been dropped. No candidate Python code runs as root.
    return ["/usr/bin/sudo", "-n", "--", "/usr/bin/unshare", "--net", "--pid", "--fork", "--kill-child=SIGKILL",
            "--mount-proc=/proc", "--propagation=private", "--",
            "/usr/bin/setpriv", "--reuid=" + str(os.getuid()), "--regid=" + str(os.getgid()),
            "--clear-groups", "--no-new-privs", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
            "--", sys.executable, "-I", str(helper), "--manager", str(bundle / "manage.py"),
            "--bundle", str(bundle), "--evidence", str(trial_root / (label + "-network.json")),
            "--parent-network-namespace", os.readlink("/proc/self/ns/net"),
            "--parent-pid-namespace", os.readlink("/proc/self/ns/pid"),
            "--uid", str(os.getuid()), "--gid", str(os.getgid())]


def sealed_report(bundle, reference, trial_root, *, deny_network):
    if reference is None:
        return None
    receipts = {}
    for service in ("zpm", "openjules", "polyphony"):
        receipt = json.loads((bundle / "environments" / service / "sealed-complete.json").read_text())
        require(receipt["plan_sha256"] == reference["sha256"], "sealed completion changed the approved plan")
        installed = receipt["installed"]
        receipts[service] = {"source_revision": receipt["source_revision"],
                             "interpreter": receipt["interpreter"], "distribution_count": len(installed),
                             "installed_set_sha256": hashlib.sha256(
                                 json.dumps(installed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    network = []
    if deny_network:
        for label in ("bootstrap", "bootstrap-repeat", "bootstrap-retained"):
            record = json.loads((trial_root / (label + "-network.json")).read_text())
            require(record["bootstrap_returncode"] == 0 and record["ambient_target_unused"],
                    "sealed bootstrap network evidence is incomplete")
            network.append(record)
    return {"plan_sha256": reference["sha256"], "environments": receipts,
            "bootstrap_ip_network_denied": deny_network, "network_observations": network,
            "later_service_network": "explicit loopback in the ordinary runner namespace"}


def diagnostics(root, error, checks, revisions, sensitive):
    """Bound exported logs and redact secrets before cutting the final tail."""
    def redact(content):
        for token in sensitive:
            content = content.replace(token, "[redacted]")
        return content

    diagnostic = {"passed": False, "error": error, "completed_checks": checks,
                  "component_revisions": revisions, "logs": {}}
    allowance = max((len(token) for token in sensitive), default=0)
    for name in ("bootstrap", "bootstrap-repeat", "bootstrap-retained", "installation",
                 "polyphony", "openjules", "zpm", "remove", "reenroll", "legacy-refusal"):
        path = root / (name + ".log")
        if path.is_file():
            with path.open("rb") as stream:
                size = path.stat().st_size
                start = max(0, size - 20000 - allowance)
                stream.seek(start)
                content = stream.read(size - start)
            boundary = max(0, size - 20000) - start
            end = boundary
            for token in sensitive:
                encoded = token.encode()
                position = content.find(encoded)
                while position >= 0 and position < boundary:
                    if position + len(encoded) > boundary:
                        end = max(end, position + len(encoded))
                    position = content.find(encoded, position + 1)
            tail = ("[redacted]" if end > boundary else "") + content[end:].decode("utf-8", errors="replace")
            diagnostic["logs"][name] = redact(tail)[-20000:]
    diagnostic = json.loads(redact(json.dumps(diagnostic, sort_keys=True)))
    private_json(root / "diagnostics.json", diagnostic)
    return diagnostic


class Processes:
    def __init__(self, root):
        self.root, self.running, self.handles = root, {}, []

    def start(self, name, command, cwd, environment):
        require(name not in self.running, "trial process already running")
        log = (self.root / (name + ".log")).open("ab")
        self.handles.append(log)
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.running[name] = process
        return process

    def stop(self, name, kill=False):
        process = self.running.pop(name, None)
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL if kill else signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=55 if name == "installation" else 10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def run_once(self, name, command, cwd, environment, timeout):
        process = self.start(name, command, cwd, environment)
        try:
            require(process.wait(timeout=timeout) == 0, name + " failed; inspect retained diagnostics")
        finally:
            self.stop(name)

    def check(self):
        require(all(process.poll() is None for process in self.running.values()), "a trial service exited; inspect retained service logs")

    def close(self):
        for name in list(reversed(self.running)):
            self.stop(name)
        for log in self.handles:
            log.close()


def eventually(predicate, processes, description, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        processes.check()
        try:
            result = predicate()
            if result:
                return result
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise AssertionError("deadline waiting for " + description)


def wait_for_capacity(read_plan, processes, count, *, retired_worker_ids=()):
    retired = frozenset(retired_worker_ids)

    def converged():
        report = read_plan()
        row = report["repositories"][0]
        live_ids = {worker["worker_id"] for worker in row["workers"]}
        # Counts can still match in ZPM's cached view after a task finishes.
        # Retirement must be observed before an equal-size replacement counts
        # as convergence; the controller's next tick supplies that evidence.
        if row["healthy"] == count and row["reserved"] == count and retired.isdisjoint(live_ids):
            return report
        return None

    return eventually(converged, processes, f"capacity {count} with completed workers retired")



def installed_legacy_refusal(bundle, checkout, processes, data):
    """Exercise a real legacy CLI before any service or protected work starts."""
    from zpm.deployment import _environment
    from zpm.installation_context import selector_path, validate_selector

    def inventory():
        containers = subprocess.run(["docker", "ps", "--all", "--quiet", "--no-trunc"],
                                    check=True, capture_output=True, text=True, timeout=15).stdout.splitlines()
        volumes = subprocess.run(["docker", "volume", "ls", "--quiet"],
                                 check=True, capture_output=True, text=True, timeout=15).stdout.splitlines()
        return {"containers": sorted(containers), "volumes": sorted(volumes)}

    def state_files():
        result = {}
        for path in sorted((bundle / "state").rglob("*")):
            require(not path.is_symlink(), "installed refusal state contains a symlink")
            if path.is_dir():
                continue
            require(path.is_file() and path.stat().st_size <= 1_000_000,
                    "installed refusal state is not a bounded regular file")
            result[str(path.relative_to(bundle))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    selector = validate_selector(bundle)
    histories, resources = state_files(), inventory()
    command = [str(bundle / "environments/openjules/bin/python"), "-m", "src.cli", "run",
               "managed-installation-refusal-check", "--repo", "openjules/installation-refusal-check"]
    process = processes.start("legacy-refusal", command, checkout, _environment(bundle, "bootstrap", data))
    try:
        returncode = process.wait(timeout=10)
    finally:
        processes.stop("legacy-refusal")
    path = processes.root / "legacy-refusal.log"
    require(path.stat().st_size <= 4096, "installed legacy refusal output exceeded its bound")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    reason = "managed_installation_requires_canonical_ingress"
    require(returncode == 2 and bool(lines) and lines[-1].endswith(": error: " + reason),
            "installed legacy CLI did not return the explicit managed-context refusal")
    require(histories == state_files() and resources == inventory(),
            "installed legacy refusal changed retained state or Docker resources")
    require(selector == validate_selector(bundle), "installed legacy refusal changed the selector")
    return {"entrypoint": "src.cli run", "returncode": returncode, "reason": reason,
            "selector_sha256": hashlib.sha256(selector_path(bundle).read_bytes()).hexdigest(),
            "state_files_unchanged": len(histories),
            "docker_inventory_counts": {name: len(rows) for name, rows in resources.items()},
            "retained_state_and_resources_unchanged": True}

def run(args):
    zpm = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(zpm))
    from zpm.controller import Controller
    from zpm.deployment import _command, _environment, prepare, verify
    from zpm.models import released, snapshot as worker_snapshot
    from zpm.store import Store

    dependency_reference = None
    reference_path = getattr(args, "dependency_plan", None)
    deny_network = getattr(args, "deny_bootstrap_network", False)
    require(not deny_network or reference_path is not None,
            "network-denied bootstrap requires an explicit sealed dependency plan")
    if reference_path is not None:
        require(reference_path.stat().st_size <= 16384, "dependency plan reference is oversized")
        dependency_reference = json.loads(reference_path.read_text(encoding="utf-8"))
    root = args.work_dir.absolute()
    require(not root.exists(), "trial work directory must be new")
    root.mkdir(mode=0o700)
    (root / "source").mkdir(mode=0o755)
    image = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
                           capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    require(image == args.image and image.startswith("sha256:") and len(image) == 71, "trial requires an already-present immutable Docker image ID")
    source = root / "source"
    (source / "answer.py").write_text("def answer():\n    return 0\n")
    (source / ".gitignore").write_text(".env\n")
    for command in (["init", "-q"], ["add", "answer.py", ".gitignore"], ["-c", "user.name=Managed Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled base"]):
        subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True, timeout=15)
    # Ignored local data is deliberately present, but is not approved task input.
    (source / ".env").write_text("TRIAL_HOST_ONLY=must-not-enter-the-worker\n")
    subprocess.run(["git", "-C", str(source), "config", "trial.hostOnly", "must-not-enter-the-worker"], check=True, timeout=15)
    base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    deployment = "managed-trial-" + secrets.token_hex(8)
    repository = "github:github.com/managed-trial/repository"
    profile = "controlled-code-change"
    scope = {"deployment_id": deployment, "repository": repository}
    authority_port, runtime_port = port(), port()
    while authority_port == runtime_port:
        runtime_port = port()
    authority_endpoint = f"http://127.0.0.1:{authority_port}"
    runtime_endpoint = f"http://127.0.0.1:{runtime_port}"
    command = ["python3", "-c", "from pathlib import Path; import runpy,sys,time; task=sys.stdin.read(); assert not Path('/source/.env').exists(); assert not Path('/source/.git').exists(); Path('answer.py').write_text('def answer():\\n    return 42\\n'); assert runpy.run_path('answer.py')['answer']() == 42; print('controlled-code-change-passed',flush=True); print('deadline-trial-running' if 'DEADLINE_TRIAL' in task else 'normal-trial',flush=True); time.sleep(120 if 'DEADLINE_TRIAL' in task else 0)"]
    source_paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
    revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=15).strip()
                 for name, path in source_paths.items()}
    bundle = root / "installation"
    configuration = {
        "schema_version": 1, "deployment_id": deployment,
        "binding": {"schema_version": 1, "repository": repository, "profile": profile, "desired_workers": 0},
        "max_workers": 3,
        "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in source_paths.items()},
        "source": {"checkout": str(source), "revision": base}, "image": image, "command": command,
        "ports": {"polyphony": authority_port, "openjules": runtime_port},
        "runtime": {"max_seconds": 90, "pids_limit": 64},
        "policy": {"checks": ["controlled-test"], "reviewers": ["operator"], "max_attempts": 1,
                   "budget_seconds": 120, "attempt_seconds": 12, "capabilities": ["python"]},
    }
    if dependency_reference is not None:
        configuration["dependency_plan"] = dependency_reference
    prepare(configuration, bundle)
    verify(bundle)
    data = json.loads((bundle / "manifest.json").read_text())["configuration"]
    environment_tokens = json.loads((bundle / "secrets/environment.json").read_text())
    operator_tokens = json.loads((bundle / "secrets/operator.json").read_text())
    tokens = {"runtime": environment_tokens["POLYPHONY_RUNTIME_TOKEN"],
              "zpm": environment_tokens["OPENJULES_WORKER_TOKEN"], "ingress": operator_tokens["ingress"]}
    state = bundle / "state/zpm.sqlite3"
    processes = Processes(root)
    authority = Client(authority_endpoint, tokens["runtime"])
    ingress = Client(authority_endpoint, tokens["ingress"])
    runtime = Client(runtime_endpoint, tokens["zpm"])
    checks = []
    commands = {name: _command(bundle, name, data) for name in source_paths}
    environments = {name: _environment(bundle, name, data) for name in source_paths}

    def start_runtime():
        processes.start("openjules", commands["openjules"], args.openjules_checkout, environments["openjules"])

    def start_zpm():
        processes.start("zpm", commands["zpm"], zpm, environments["zpm"])

    def plan():
        with Store(state, deployment) as store:
            return Controller(store).plan()

    def target(count, *, retired_worker_ids=()):
        with Store(state, deployment) as store:
            store.change(repository, "target", count)
        return wait_for_capacity(plan, processes, count, retired_worker_ids=retired_worker_ids)

    def snapshot():
        return authority.request("GET", "/managed/snapshot", query=scope)

    try:
        sealed_before = {name: (bundle / name).read_bytes() for name in
                         ("manifest.json", "secrets/environment.json", "secrets/operator.json")}
        require(prepare(data, bundle)["reused"] is True, "identical preparation did not reuse the installation")
        require(sealed_before == {name: (bundle / name).read_bytes() for name in sealed_before}, "repeat preparation changed credentials or configuration")
        processes.run_once("bootstrap", bootstrap_command(bundle, root, "bootstrap", deny_network=deny_network),
                           bundle, clean_environment(), 900)
        bootstrapped = {name: (bundle / "environments" / name / "bootstrap-complete.json").read_bytes() for name in source_paths}
        from zpm.installation_context import selector_path
        original_context = selector_path(bundle).read_bytes()
        processes.run_once("bootstrap-repeat", bootstrap_command(bundle, root, "bootstrap-repeat", deny_network=deny_network),
                           bundle, clean_environment(), 60)
        require(bootstrapped == {name: (bundle / "environments" / name / "bootstrap-complete.json").read_bytes() for name in source_paths}, "repeated bootstrap replaced completed environments")
        require(selector_path(bundle).read_bytes() == original_context, "repeated bootstrap changed the managed installation context")
        checks.append("repeat preparation and bootstrap reuse the verified private bundle, credentials and completed environments")
        legacy_refusal = installed_legacy_refusal(bundle, args.openjules_checkout, processes, data)
        checks.append("the installed OpenJules legacy CLI explicitly refuses before services start, preserving retained state, its selector and the complete Docker inventory")
        # Exercise the documented user-facing manager before the destructive
        # crash scenarios, which then reuse these exact configs and interpreters.
        processes.start("installation", [sys.executable, str(bundle / "manage.py"), "start"], bundle, clean_environment())
        def installed_ready():
            result = subprocess.run([sys.executable, str(bundle / "manage.py"), "doctor"], cwd=bundle,
                                    env=clean_environment(), capture_output=True, text=True, timeout=50)
            require(result.returncode in {0, 3}, "generated doctor could not inspect the installation")
            return json.loads(result.stdout).get("ready") is True
        eventually(installed_ready, processes, "generated installation doctor readiness", 60)
        require(plan()["reserved"] == 0, "installation startup allocated workers despite its zero target")
        processes.stop("installation")
        verify(bundle)
        checks.append("generated private bundle bootstraps three isolated environments and starts real services whose scoped doctor check passes")

        processes.start("polyphony", commands["polyphony"], args.polyphony_checkout, environments["polyphony"])
        eventually(lambda: authority.request("GET", "/managed/capabilities", query=scope | {"profile": profile}), processes, "authority start", 15)
        start_runtime()
        eventually(lambda: runtime.request("GET", "/api/workers/capabilities", query=scope | {"profile": profile}).get("managed_dispatch"), processes, "managed runtime readiness", 30)
        start_zpm()
        require(plan()["reserved"] == 0, "zero target allocated worker capacity")
        initial = target(2)
        initial_ids = {worker["worker_id"] for worker in initial["repositories"][0]["workers"]}
        require(len(initial_ids) == 2, "capacity two did not create two distinct incarnations")
        checks.append("three separate authenticated service processes converge from zero to two real Docker workers")

        # A service outage is uncertainty, not authority to create replacement
        # workers. The same journals are then used by a fresh service process.
        processes.stop("openjules", kill=True)
        def uncertainty():
            report = plan()
            row = report["repositories"][0]
            return report if row["healthy"] == 0 and row["reserved"] == 2 else None
        unknown = eventually(uncertainty, processes, "unknown capacity during runtime outage", 15)
        require({worker["worker_id"] for worker in unknown["repositories"][0]["workers"]} == initial_ids, "unknown runtime created replacement identities")
        processes.stop("zpm", kill=True)
        start_runtime()
        start_zpm()
        recovered = target(2)
        require({worker["worker_id"] for worker in recovered["repositories"][0]["workers"]} == initial_ids, "service restart duplicated idle workers")
        checks.append("runtime outage retains reservations and process restart recovers the original worker incarnations")

        request = {"schema_version": 1, **scope, "source": "github:issue:1", "request_key": "explicit-run-1",
                   "title": "Controlled managed runtime trial", "requirements": "Change answer() to return 42 and run the controlled assertion."}
        first = ingress.request("POST", "/managed/enroll", data=request)
        repeated = ingress.request("POST", "/managed/enroll", data=request)
        require(first["task"]["id"] == repeated["task"]["id"], "source replay created a second canonical task")
        ingress.request("POST", "/managed/enroll", data=request | {"requirements": "changed retry"}, expected=409)
        ingress.request("POST", "/managed/enroll", data=request | {"repository": "github:github.com/foreign/repository"}, expected=403)
        runtime.request("GET", "/api/workers", query=scope | {"deployment_id": "foreign-deployment"}, expected=403)
        checks.append("idempotent source delivery preserves one task and conflicting or foreign-scope requests are denied")

        def captured():
            report = snapshot()
            attempts = [attempt for attempt in report["attempt"] if attempt["task"] == first["task"]["id"]]
            candidates = [candidate for candidate in report["candidate"] if candidate["task"] == first["task"]["id"]]
            return (report, attempts[0], candidates[0]) if len(attempts) == 1 and len(candidates) == 1 and attempts[0].get("termination_confirmed") else None
        evidence, attempt, candidate = eventually(captured, processes, "real code execution and canonical artifact capture")
        require(len(evidence["task"]) == 1 and len(evidence["attempt"]) == 1, "runtime duplicated canonical task execution")
        require(attempt.get("launch_fenced") is True, "terminal execution lacks a durable launch fence")
        require(set(attempt.get("captured_artifacts", {})) == {"patch", "log", "manifest"}, "canonical capture lacks required artifacts")
        artifacts = {}
        for kind, digest in attempt["captured_artifacts"].items():
            content = authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True)
            require(hashlib.sha256(content.encode()).hexdigest() == digest, "canonical artifact content hash mismatch")
            artifacts[kind] = content
        require("answer.py" in artifacts["patch"] and "+    return 42" in artifacts["patch"], "captured patch lacks the real code change")
        require("controlled-code-change-passed" in artifacts["log"], "captured log lacks the successful controlled assertion")
        require(candidate["patch"] == attempt["captured_artifacts"]["patch"] and candidate["base"] == base, "candidate is not bound to the exact captured patch and base")
        require(evidence["task"][0]["status"] == "awaiting_review" and not evidence.get("admission"), "runtime self-authorized task acceptance")
        require((source / "answer.py").read_text() == "def answer():\n    return 0\n", "task modified the read-only approved source")
        checks.append("real isolated code change, assertion, termination and three content-hashed artifacts form a review-pending canonical candidate")

        verifier = root / "artifact-verification"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(verifier)], check=True, timeout=15)
        subprocess.run(["git", "-C", str(verifier), "apply", "--check", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
        subprocess.run(["git", "-C", str(verifier), "apply", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
        subprocess.run([sys.executable, "-c", "import runpy; assert runpy.run_path('answer.py')['answer']() == 42"], cwd=verifier, check=True, timeout=10)
        checks.append("captured patch applies to the approved base and independently passes the controlled assertion")

        completed_worker = attempt["runtime_binding"]["worker_id"]
        replacement = target(2, retired_worker_ids={completed_worker})
        live_ids = {worker["worker_id"] for worker in replacement["repositories"][0]["workers"]}
        require(completed_worker not in live_ids and len(live_ids) == 2, "completed worker incarnation was reused for a second root")
        retired = runtime.request("GET", "/api/workers/" + completed_worker, query=scope)
        require(retired["worker_id"] == completed_worker and released(worker_snapshot(retired)),
                "completed worker retirement lacks termination, capture or closed admission")
        checks.append("ZPM observes completed-worker retirement and restores capacity with a new incarnation while OpenJules retains terminal evidence")

        deadline_request = request | {"source": "github:issue:2", "request_key": "explicit-deadline-run-1",
                                      "title": "Controlled deadline recovery", "requirements": "DEADLINE_TRIAL: preserve the changed file when the service disappears."}
        deadline_task = ingress.request("POST", "/managed/enroll", data=deadline_request)["task"]["id"]
        def deadline_attempt():
            attempts = [item for item in snapshot()["attempt"] if item["task"] == deadline_task]
            return attempts[0] if len(attempts) == 1 and attempts[0].get("runtime_binding") else None
        running_attempt = eventually(deadline_attempt, processes, "deadline task claim", 15)
        worker = running_attempt["runtime_binding"]["worker_id"]
        def running_command():
            listed = subprocess.run(["docker", "ps", "-aq", "--filter", "label=openjules.worker=" + worker], capture_output=True, text=True, check=True, timeout=10)
            ids = listed.stdout.split()
            require(len(ids) <= 1, "runtime created duplicate containers for one worker")
            if not ids:
                return None
            copied = subprocess.run(["docker", "cp", ids[0] + ":/workspace/run/output.log", "-"], capture_output=True, timeout=10)
            if copied.returncode:
                return None
            with tarfile.open(fileobj=io.BytesIO(copied.stdout)) as archive:
                for member in archive:
                    if member.isfile() and member.name.endswith("output.log"):
                        content = archive.extractfile(member).read()
                        if b"deadline-trial-running" in content:
                            return ids[0]
            return None
        container_id = eventually(running_command, processes, "controlled sleeping process", 8)
        processes.stop("openjules", kill=True)
        def watchdog_stopped():
            result = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", container_id], capture_output=True, text=True, check=True, timeout=10)
            return result.stdout.strip() == "false"
        eventually(watchdog_stopped, processes, "independent container watchdog deadline", 20)
        require(plan()["reserved"] == 2, "runtime outage released capacity before capture")
        start_runtime()
        def recovered_capture():
            attempts = [item for item in snapshot()["attempt"] if item["task"] == deadline_task]
            require(len(attempts) == 1, "deadline recovery allocated a second canonical attempt")
            return attempts[0] if attempts[0].get("termination_confirmed") and attempts[0].get("captured_artifacts") else None
        salvaged = eventually(recovered_capture, processes, "deadline recovery and durable salvage", 30)
        require(salvaged["id"] == running_attempt["id"] and salvaged.get("launch_fenced") is True, "deadline recovery changed the attempt or lost its fence")
        capture_manifest = authority.request("GET", "/managed/artifacts/" + salvaged["captured_artifacts"]["manifest"], query=scope, text=True)
        capture_manifest = json.loads(capture_manifest)
        archives = [path for path in (bundle / "state").rglob("*.tar") if hashlib.sha256(path.read_bytes()).hexdigest() == capture_manifest["archive_sha256"]]
        require(len(archives) == 1, "recovered workspace archive does not match canonical manifest")
        with tarfile.open(archives[0]) as archive:
            member = archive.getmember("workspace/run/repo/answer.py")
            require(member.isfile() and archive.extractfile(member).read() == b"def answer():\n    return 42\n", "deadline capture lost the interrupted task's modified file")
        after_deadline = snapshot()
        require(len(after_deadline["candidate"]) == 1 and not after_deadline.get("admission"), "timed-out execution created an accepted or successful candidate")
        checks.append("container watchdog terminates the process after API death and restart captures the interrupted workspace under the original attempt")

        target(3)
        target(1)
        target(0)
        processes.stop("polyphony", kill=True)
        processes.start("polyphony", commands["polyphony"], args.polyphony_checkout, environments["polyphony"])
        durable = eventually(snapshot, processes, "authority restart", 15)
        require(durable["candidate"][0]["id"] == candidate["id"] and len(durable["attempt"]) == 2, "authority restart lost or duplicated captured work")
        checks.append("completed workers retire, capacity reaches three then one then zero, and authority artifacts survive service restart")

        # Exercise the documented removal and explicit re-enrollment through
        # the installed CLI while the actual controller and services run.
        before_removal = target(2)
        removal_ids = {worker["worker_id"] for worker in before_removal["repositories"][0]["workers"]}
        installed_cli = [str(bundle / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(state)]
        processes.run_once("remove", installed_cli + ["remove", repository], zpm, clean_environment(), 30)
        wait_for_capacity(plan, processes, 0)
        with Store(state, deployment) as store:
            removed = store.binding(repository)
            require(removed["enabled"] is False and removed["desired_workers"] == 2, "removal lost the selected target or did not disable enrollment")
        require(prepare(data, bundle)["reused"] is True, "retained installation cannot be reused after removal")
        with Store(state, deployment) as store:
            require(store.binding(repository) == removed, "repeat preparation silently re-enabled removed enrollment")
        processes.stop("zpm")
        processes.stop("openjules")
        processes.stop("polyphony")
        processes.run_once("bootstrap-retained", bootstrap_command(bundle, root, "bootstrap-retained", deny_network=deny_network),
                           bundle, clean_environment(), 60)
        processes.start("polyphony", commands["polyphony"], args.polyphony_checkout, environments["polyphony"])
        eventually(snapshot, processes, "retained authority restart", 15)
        start_runtime()
        eventually(lambda: runtime.request("GET", "/api/workers/capabilities", query=scope | {"profile": profile}).get("managed_dispatch"), processes, "retained runtime readiness", 30)
        start_zpm()
        processes.run_once("reenroll", installed_cli + ["enroll", str(bundle / "config/repository.json")], zpm, clean_environment(), 30)
        resumed = target(1, retired_worker_ids=removal_ids)
        require(resumed["reserved"] == 1, "retained installation did not resume bounded capacity")
        retained_snapshot = snapshot()
        require(len(retained_snapshot["task"]) == 2 and len(retained_snapshot["attempt"]) == 2,
                "removal and resume lost or repeated canonical work")
        require(retained_snapshot["candidate"][0]["id"] == candidate["id"], "removal and resume lost the candidate")
        for kind, digest in attempt["captured_artifacts"].items():
            require(authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True) == artifacts[kind],
                    "removal and resume changed a retained canonical artifact")
        require(sealed_before == {name: (bundle / name).read_bytes() for name in sealed_before}, "removal and resume rotated credentials or configuration")
        target(0)
        checks.append("installed removal drains owned workers, retains authority and artifacts, and explicit re-enrollment resumes with the same identity and credentials")

        final_reserved = plan()["reserved"]
        processes.close()
        # Import only after definitions and the generated installation have
        # completed; the contract helper reuses this module's HTTP/process tools.
        from check_two_runtime_stack import run_contract
        contention = run_contract(root, bundle, data, source_paths, revisions, image)
        checks.append("two independent managed runtimes contend through real canonical claims without duplicating protected execution")

        report = {"passed": True, "schema_version": 1, "deployment_id": deployment, "image": image,
                  "component_revisions": revisions, "installation_bootstrap_and_doctor": "passed",
                  "managed_installation_legacy_refusal": legacy_refusal,
                  "checks": checks, "canonical_task": first["task"]["id"], "candidate": candidate["id"],
                  "captured_artifacts": attempt["captured_artifacts"], "final_reserved": final_reserved,
                  "two_runtime_contention": contention,
                  "sealed_dependencies": sealed_report(bundle, dependency_reference, root, deny_network=deny_network),
                  "proof": "real Docker, real HTTP, separate services, controlled code change",
                  "not_proven": ["live model inference", "child delegation", "independent semantic review", "forge publication", "merge authorization",
                                 "interrupted preparation recovery", "version upgrade", "destructive removal"]}
        private_json(root / "report.json", report)
        return report
    except Exception as exc:
        # Publish only bounded diagnostics with every generated token removed.
        # Journals, archives, configuration and complete raw logs stay private.
        sensitive = set(environment_tokens.values()) | set(operator_tokens.values())
        diagnostic = diagnostics(root, str(exc) if isinstance(exc, AssertionError) else type(exc).__name__,
                                 checks, revisions, sensitive)
        if hasattr(exc, "contract_diagnostic"):
            diagnostic["two_runtime_contention"] = exc.contract_diagnostic
            # The nested helper has already redacted its distinct credentials.
            (root / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2, sort_keys=True))
        print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
        raise
    finally:
        processes.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument(f"--{name}-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-present full sha256 image ID")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private directory, retained for recovery")
    parser.add_argument("--dependency-plan", type=Path, help="explicit local sealed dependency reference JSON")
    parser.add_argument("--deny-bootstrap-network", action="store_true",
                        help="qualify bootstrap in a new IP network namespace, then run services on explicit loopback")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, AssertionError) else "trial failed; inspect retained private service logs"
        print(json.dumps({"passed": False, "error": message, "work_dir": str(args.work_dir.absolute())}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
