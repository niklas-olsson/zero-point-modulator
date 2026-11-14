"""One controller, two full destination services, real Docker and authority.

Called only from the existing installed-stack trial at its exact component pins.
The seed remains an ordinary single-host installation. These explicit additional
service configurations qualify the consumer contract, not a two-host installer,
public distribution, live inference or browser execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import selectors
import signal
import subprocess
import time
from unittest.mock import patch

from check_stack import Client, Processes, eventually, port, private_json, require
from check_authority_admission import stop_owned


class DestinationTrialFailure(AssertionError):
    def __init__(self, phase, checks, error):
        super().__init__("full destination trial failed during " + phase)
        self.destination_diagnostic = {"phase": phase, "completed_checks": checks,
                                       "error_type": type(error).__name__}


def run_contract(parent_root, installed_bundle, original_data, source_paths, revisions, image):
    from zpm.deployment import _command, _environment, prepare, verify
    from zpm.destination_controller import DestinationController
    from zpm import destination_state
    from zpm.models import ProviderConfig, released
    from zpm.provider import DestinationProvider, ProviderError
    from zpm.store import Store

    root = parent_root / "full-destination-contract"
    require(not root.exists(), "full destination trial directory must be fresh")
    root.mkdir(mode=0o700)
    source = root / "source"
    source.mkdir(mode=0o755)
    (source / "answer.py").write_text("answer = 0\n")
    for arguments in (["init", "-q"], ["add", "."], ["-c", "user.name=Destination Trial",
            "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled base"]):
        subprocess.run(["git", "-C", str(source), *arguments], check=True, capture_output=True, timeout=15)
    base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    deployment = "destinations-" + secrets.token_hex(8)
    repository, profile = "github:github.com/destination-trial/repository", "controlled-destination"
    scope = {"deployment_id": deployment, "repository": repository}
    ports = set()
    while len(ports) < 3:
        ports.add(port())
    authority_port, port_a, port_b = sorted(ports)
    seed = root / "seed"
    prepare({"schema_version": 1, "deployment_id": deployment,
        "binding": {"schema_version": 1, "repository": repository, "profile": profile, "desired_workers": 0},
        "max_workers": 2, "sources": original_data["sources"],
        "source": {"checkout": str(source), "revision": base}, "image": image,
        "command": ["python3", "-I", "-c", "from pathlib import Path; import os,sys; assert os.getuid()==65532; assert sys.stdin.read()=='Set the controlled answer to42.'; Path('answer.py').write_text('answer = 42\\n'); print('DESTINATION_CAPTURED',flush=True)"],
        "ports": {"polyphony": authority_port, "openjules": port_a},
        "runtime": {"max_seconds": 60, "pids_limit": 64},
        "policy": {"checks": ["controlled-test"], "reviewers": ["operator"], "max_attempts": 1,
                   "budget_seconds": 30, "attempt_seconds": 20, "capabilities": ["python"]}}, seed)
    verify(seed)
    data = json.loads((seed / "manifest.json").read_text())["configuration"]
    sealed = {path.relative_to(seed).as_posix(): path.read_bytes()
              for folder in ("config", "secrets") for path in (seed / folder).iterdir()}
    environment = json.loads((seed / "secrets/environment.json").read_text())
    operators = json.loads((seed / "secrets/operator.json").read_text())
    credentials = json.loads((seed / "secrets/polyphony.json").read_text())
    token_a = environment["POLYPHONY_RUNTIME_TOKEN"]
    token_b, controller_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    credentials[token_b] = credentials[token_a] | {"identity": "second-destination-runtime"}
    require(credentials[token_a]["identity"] != credentials[token_b]["identity"], "runtime principals alias")
    private_json(root / "authority-credentials.json", credentials)
    private_json(root / "second-controller.json", {controller_b: [scope]})
    processes = Processes(root)
    members = {}
    store = None
    checks = []
    phase = "full destination initialization and explicit public approval"
    failure = None
    injected = {"lost": False, "refuse": False}

    def command_result(command, cwd, environment):
        # Fixed total time and pipe byte limits apply while reading. Do not
        # collect unbounded native diagnostics and truncate them afterward.
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                                   start_new_session=True)
        deadline = time.monotonic() + 45
        output, errors = bytearray(), bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                for stream, buffer, limit in ((process.stdout, output, 131072), (process.stderr, errors, 32768)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, (buffer, limit))
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, "supported command exceeded its finite deadline")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        buffer, limit = key.data
                        raw = os.read(key.fileobj.fileno(), min(65536, limit - len(buffer) + 1))
                        if not raw:
                            selector.unregister(key.fileobj)
                        else:
                            buffer.extend(raw)
                            require(len(buffer) <= limit, "supported command exceeded its output bound")
                require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0,
                        "supported host or controller command failed")
            return json.loads(output)
        finally:
            try:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=5)
            finally:
                process.stdout.close()
                process.stderr.close()

    def admin(label, *arguments):
        member = members[label]
        return command_result(member["command"] + list(arguments), source_paths["openjules"], member["environment"])

    def start(label):
        member = members[label]
        processes.start("openjules-" + label, member["command"], source_paths["openjules"], member["environment"])

    class ObservedProvider(DestinationProvider):
        def ensure(self, spec):
            if spec["destination_binding"]["destination_id"] == "host-a":
                if injected["refuse"]:
                    injected["refuse"] = False
                    admin("a", "--close-admission")
                result = super().ensure(spec)
                if not injected["lost"]:
                    injected["lost"] = True
                    raise ProviderError("trial loses the reply after the actual original ensure")
                return result
            return super().ensure(spec)

    controller = None

    def tick(limit=3):
        return controller.tick(limit)

    def settled(count):
        report = tick(6)
        return report if report["reserved"] == count and report["repositories"][0]["healthy"] == count else None

    def identity(row):
        return {key: row[key] for key in ("launch_key", "slot_id", "generation", "profile",
                                         "destination_binding", "execution_qualification")}

    try:
        for label, runtime_port, runtime_token, control_token in (
            ("a", port_a, token_a, environment["OPENJULES_WORKER_TOKEN"]),
            ("b", port_b, token_b, controller_b),
        ):
            config = json.loads((seed / "config/openjules.json").read_text())
            config.update(schema_version=2, port=runtime_port, state_directory=str(root / ("runtime-" + label)),
                          lifecycle_database=str(root / ("lifecycle-" + label + ".sqlite3")),
                          execution_destination={"destination_id": "host-" + label})
            config["profiles"][0]["root_timeout_ms"] = config["profiles"][0].pop("max_seconds") * 1000
            config["execution_contract"] = {"version": 1, "mode": "full", "initial_worker_ceiling": 1,
                "public_profiles": [scope | {"profile": profile, "model_service_policy_id": None}]}
            if label == "b":
                config["credentials_file"] = str(root / "second-controller.json")
            require(not config.get("models") and not config.get("children"), "trial enabled inference or child policies")
            path = root / ("openjules-" + label + ".json")
            private_json(path, config)
            endpoint = "http://127.0.0.1:" + str(runtime_port)
            members[label] = {"config": config, "endpoint": endpoint, "token": control_token,
                "command": [str(installed_bundle / "environments/openjules/bin/python"), "-m", "src.worker.managed_service", "--config", str(path)],
                "environment": _environment(seed, "openjules", data) | {"POLYPHONY_RUNTIME_TOKEN": runtime_token},
                "client": Client(endpoint, control_token)}
            admin(label, "--initialize-destination")
            approval = admin(label, "--preview-execution-contract", "--deployment-id", deployment,
                             "--repository", repository, "--profile", profile)
            approval_path = root / ("approval-" + label + ".json")
            private_json(approval_path, approval)
            admin(label, "--approve-execution-contract", str(approval_path))
            require(admin(label, "--inspect-admission")["new_effects_open"] is False, "fresh admission was open")
            admin(label, "--open-admission")
            members[label].update(approval=approval, approval_path=approval_path)
        require(members["a"]["approval"]["execution_manifest_sha256"] == members["b"]["approval"]["execution_manifest_sha256"],
                "same portable profile produced different approved execution digests")
        checks.append("both fresh host journals independently inspect and approve one exact real source/image contract")

        authority_command = _command(seed, "polyphony", data)
        authority_command[0] = str(installed_bundle / "environments/polyphony/bin/python")
        authority_command[authority_command.index("--credentials") + 1] = str(root / "authority-credentials.json")
        processes.start("polyphony", authority_command, source_paths["polyphony"], _environment(seed, "polyphony", data))
        authority_endpoint = "http://127.0.0.1:" + str(authority_port)
        authority = Client(authority_endpoint, token_a)
        ingress = Client(authority_endpoint, operators["ingress"])
        snapshot = lambda: authority.request("GET", "/managed/snapshot", query=scope)
        eventually(snapshot, processes, "destination authority readiness", 15)
        for label in members:
            start(label)
        registry = {"schema_version": 2, "max_workers": 2, "timeout_seconds": 5,
            "destinations": [], "profiles": []}
        for label, member in members.items():
            bound = member["approval"]["destination_binding"]
            registry["destinations"].append({"destination_id": bound["destination_id"],
                "journal_incarnation": bound["journal_incarnation"], "endpoint": member["endpoint"],
                "token_ref": "env:ZPM_DESTINATION_TRIAL_" + label.upper(), "max_workers": 1, "enabled": False})
            registry["profiles"].append({"destination_id": bound["destination_id"], "repository": repository,
                "profile": profile, "policy_binding_id": bound["policy_binding_id"], "policy_binding_version": 1})
        state_path = root / "zpm.sqlite3"
        store = Store.initialize_registry(state_path, registry, deployment)
        for member in members.values():
            store.import_approval(member["approval_path"])
        store.activate_destinations(offline=True)
        for destination in registry["destinations"]:
            destination["enabled"] = True
        store.import_registry(registry)
        store.enroll({"schema_version": 1, "repository": repository, "profile": profile, "desired_workers": 2})
        controller = DestinationController(store, ObservedProvider)
        with patch.dict(os.environ, {"ZPM_DESTINATION_TRIAL_A": members["a"]["token"],
                "ZPM_DESTINATION_TRIAL_B": members["b"]["token"], "no_proxy": "*", "NO_PROXY": "*"}):
            phase = "canonical readiness and lost original ensure reply"
            eventually(lambda: all(item["profiles"][0]["validation"]["ready"]
                                  for item in controller.check(4)["destinations"]),
                       processes, "both destination readiness checks", 30)
            # check uses the same persisted cursor. Reset only this empty trial
            # fixture's cursor so the declared lost-reply scene selects host a.
            destination_state.cursor(store, "")
            first = tick(3)
            require(first["reserved"] == 1 and store.workers()[0]["snapshot"]["state"] == "unknown",
                    "lost reply did not retain the original uncertain reservation")
            original_a = store.workers()[0]
            frozen = identity(original_a)
            tick(3)
            require(len(store.workers()) == 2, "healthy second destination did not progress")
            processes.stop("openjules-a", kill=True)
            for _ in range(3):
                require(tick(3)["reserved"] == 2, "unavailable original destination released capacity")
            require(identity(store.worker(original_a["launch_key"])) == frozen, "outage changed the original operation")
            store.close()
            store = Store(state_path, deployment, create=False)
            controller = DestinationController(store, ObservedProvider)
            admin("a", "--close-admission")
            start("a")
            eventually(lambda: settled(2), processes, "original destination recovery while closed", 30)
            require(identity(store.worker(original_a["launch_key"])) == frozen and len(store.workers()) == 2,
                    "restart recreated or rebound original capacity")
            checks.append("lost HTTP reply, service death and controller reopen retain original B/H/A and recover while host admission is closed")

            phase = "exact permanent refusal at the final native admission boundary"
            store.change(repository, "target", 0)
            eventually(lambda: tick(6)["reserved"] == 0, processes, "both original workers drain and capture", 30)
            require(all(released(row["snapshot"]) for row in store.workers()), "original termination/capture is incomplete")
            admin("a", "--open-admission")
            registry["destinations"][1]["enabled"] = False
            store.import_registry(registry)
            store.change(repository, "target", 1)
            injected["refuse"] = True
            eventually(lambda: any(row["snapshot"].get("disposition") is not None for row in store.workers())
                       or (tick(3) and False), processes, "durable stale-revision refusal", 15)
            refused = next(row for row in store.workers() if row["snapshot"].get("disposition") is not None)
            require(released(refused["snapshot"]) and refused["snapshot"]["disposition"]["reason"] == "stale_admission_revision",
                    "native host did not return the exact permanent refusal")
            store.change(repository, "target", 0)
            admin("a", "--open-admission")
            bound = destination_state.binding(refused)
            original = destination_state.original(refused)
            client = DestinationProvider(ProviderConfig.parse({"schema_version": 1,
                "endpoint": members["a"]["endpoint"], "token_ref": "env:ZPM_DESTINATION_TRIAL_A",
                "max_workers": 1, "timeout_seconds": 5}))
            require(client.discover(deployment, repository, profile, bound, launch_key=refused["launch_key"], original=original)
                    == [refused["snapshot"]], "reopening changed original refusal discovery")
            require(client.observe(deployment, repository, refused["snapshot"]["worker_id"], bound) == refused["snapshot"],
                    "original worker observation changed refusal mode")
            mixed = client.discover(deployment, repository, profile, bound)
            require({row["launch_key"] for row in mixed} == {original_a["launch_key"], refused["launch_key"]},
                    "mixed discovery omitted original full history")
            checks.append("native admission race retains and replays the original terminal never-admitted row across reopening and mixed inventory")

            phase = "actual canonical task execution and retained captured result"
            registry["destinations"][0]["enabled"] = False
            registry["destinations"][1]["enabled"] = True
            store.import_registry(registry)
            installed_cli = [str(installed_bundle / "environments/zpm/bin/zpm"), "--state", str(state_path),
                             "--deployment-id", deployment]
            cli_environment = _environment(seed, "zpm", data) | {
                "ZPM_DESTINATION_TRIAL_A": members["a"]["token"],
                "ZPM_DESTINATION_TRIAL_B": members["b"]["token"], "no_proxy": "*", "NO_PROXY": "*"}
            def cli(*arguments):
                return command_result(installed_cli + list(arguments), source_paths["zpm"], cli_environment)
            def cli_settled(count):
                report = cli("reconcile", "--registry", "--max-actions", "6")
                return report if report["reserved"] == count and report["repositories"][0]["healthy"] == count else None
            checked = cli("check", "--registry", "--max-actions", "4")
            require(checked["tick_io"]["used"] <= 4 and checked["destinations"][1]["profiles"][0]["validation"]["ready"],
                    "installed full-mode check did not qualify the selected destination")
            cli("target", repository, "1")
            eventually(lambda: cli_settled(1), processes, "installed CLI placement after proven release", 30)
            task = ingress.request("POST", "/managed/enroll", data={"schema_version": 1, **scope,
                "source": "github:issue:1", "request_key": "destination-code-change", "title": "Controlled destination work",
                "requirements": "Set the controlled answer to42."})
            def captured():
                value = snapshot()
                attempts = [row for row in value["attempt"] if row["task"] == task["task"]["id"]]
                candidates = [row for row in value["candidate"] if row["task"] == task["task"]["id"]]
                return (value, attempts[0], candidates[0]) if len(attempts) == len(candidates) == 1 and attempts[0].get("termination_confirmed") else None
            canonical, attempt, candidate = eventually(captured, processes, "destination canonical capture", 60)
            require(attempt.get("launch_fenced") is True and set(attempt["captured_artifacts"]) == {"patch", "log", "manifest"},
                    "canonical result lacks the original capture fence")
            artifacts = {}
            for kind, digest in attempt["captured_artifacts"].items():
                content = authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True)
                require(hashlib.sha256(content.encode()).hexdigest() == digest, "captured artifact digest changed")
                require(not any(token in content for token in (*environment.values(), *operators.values(), token_b, controller_b)),
                        "captured output crossed the credential boundary")
                artifacts[kind] = content
            require("+answer = 42" in artifacts["patch"] and "DESTINATION_CAPTURED" in artifacts["log"], "controlled change was not captured")
            require(canonical["task"][0]["status"] == "awaiting_review" and not canonical.get("admission"), "capacity allocated task acceptance")
            require((source / "answer.py").read_text() == "answer = 0\n", "execution changed its source")
            cli("target", repository, "0")
            eventually(lambda: cli_settled(0), processes, "installed CLI final capture and drain", 30)
            store.close()
            store = Store(state_path, deployment, create=False)
            require(store.worker(refused["launch_key"])["snapshot"] == refused["snapshot"], "controller reopen lost permanent refusal")
            for kind, digest in attempt["captured_artifacts"].items():
                require(authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True) == artifacts[kind],
                        "reopen changed retained canonical result")
            # Exercise the installed CLI against the same retained active format.
            output = cli("status")
            require(output["reserved"] == 0 and output["execution_enabled"] is True, "installed CLI cannot read full destination state")
            checks.append("installed check/reconcile CLI places and retires the second real Docker runtime; one canonical task is captured and retained results survive controller reopen")
        require(sealed == {name: (seed / name).read_bytes() for name in sealed}, "trial changed sealed seed configuration")
        verify(seed)
        verify(installed_bundle)
        return {"schema_version": 1, "passed": True, "component_revisions": revisions,
            "topology": "one local controller, two full managed service processes and independent journals, one real canonical authority and Docker Engine",
            "image": image, "portable_execution_manifest_sha256": members["a"]["approval"]["execution_manifest_sha256"],
            "checks": checks, "canonical_task": task["task"]["id"], "candidate": candidate["id"],
            "captured_artifacts": attempt["captured_artifacts"], "final_reserved": 0,
            "consumer_paths": {"fault_injection": "source controller using actual HTTP",
                               "steady_state": "installed zpm check/reconcile --registry",
                               "retained_status": "installed zpm status"},
            "model_calls": 0, "child_delegations": 0,
            "never_admitted": {"launch_key": refused["launch_key"], "disposition": refused["snapshot"]["disposition"]},
            "not_proven": ["two independent hosts", "generated role-aware installation", "remote TLS deployment",
                           "live model inference", "browser execution", "independent semantic acceptance", "public distribution"]}
    except BaseException as error:
        failure = DestinationTrialFailure(phase, list(checks), error)
        raise failure from error
    finally:
        cleanup_failures = []
        if store is not None:
            try:
                store.change(repository, "target", 0)
            except Exception:
                cleanup_failures.append("retained-zero-target")
            try:
                store.close()
            except Exception:
                cleanup_failures.append("controller-close")
        for name in reversed(list(processes.running)):
            try:
                stop_owned(processes, name)
            except BaseException:
                cleanup_failures.append("owned-service-reap")
        for handle in processes.handles:
            handle.close()
        if cleanup_failures:
            if failure is None:
                raise DestinationTrialFailure("owned cleanup", checks, RuntimeError("cleanup incomplete"))
            failure.destination_diagnostic["cleanup_failures"] = cleanup_failures
