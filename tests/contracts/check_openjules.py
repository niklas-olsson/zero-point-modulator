"""Exercise the real HTTP contract with OpenJules' explicit subprocess fixture.

This optional integration check requires an OpenJules checkout containing its
worker lifecycle API and tests. It does not run coding agents, Docker or Polyphony.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import patch


def require(condition: bool, message: str) -> None:
    # Remain an actual check when Python is invoked with optimization enabled.
    if not condition:
        raise AssertionError(message)


def load_fixture(checkout: Path):
    path = checkout / "tests/unit/test_worker_lifecycle.py"
    spec = importlib.util.spec_from_file_location("openjules_contract_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the OpenJules subprocess test fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SubprocessFixture


@contextmanager
def temporary_credential():
    """Create a fresh test-only reference without replacing existing variables."""
    while True:
        name = "ZPM_CONTRACT_" + secrets.token_hex(16).upper()
        if name not in os.environ:
            break
    token = secrets.token_urlsafe(32)
    os.environ[name] = token
    try:
        yield name, token
    finally:
        if os.environ.get(name) == token:
            del os.environ[name]


@contextmanager
def local_server(app):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="error", access_log=False, timeout_graceful_shutdown=2,
        ))
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True,
            name="openjules-contract-http",
        )
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            require(server.started, "the local HTTP fixture did not start within five seconds")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=2)
            require(not thread.is_alive(), "the local HTTP fixture did not stop")


def run_trial(checkout: Path) -> dict:
    zpm_checkout = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(zpm_checkout), str(checkout)]
    from src.api.worker_lifecycle import create_worker_app
    from src.worker.lifecycle import LifecycleConflict, UnavailableRuntime, WorkerLifecycle, WorkerScope
    from zpm.controller import Controller
    from zpm.models import ProviderConfig
    from zpm.provider import OpenJulesProvider, ProviderError
    from zpm.store import Store

    class DropFirstLaunchReply(OpenJulesProvider):
        """Lose one successful reply after using the actual HTTP transport."""

        def __init__(self, config):
            super().__init__(config)
            self.lost = False

        def ensure(self, spec):
            result = super().ensure(spec)
            if not self.lost:
                self.lost = True
                raise ProviderError("injected lost reply after successful HTTP launch")
            return result

    # The helper inherits normal proxy behavior. Keep this loopback-only trial's
    # credential away from any external proxy configured by the surrounding shell.
    with (TemporaryDirectory(prefix="zpm-openjules-contract-") as temporary,
          temporary_credential() as credential,
          patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})):
        root = Path(temporary)
        runtime = load_fixture(checkout)(root)
        deployment = "contract-trial"
        repo_a = "github:github.com/contract/repo-a"
        repo_b = "github:github.com/contract/repo-b"
        scope_a, scope_b = WorkerScope(deployment, repo_a), WorkerScope(deployment, repo_b)
        lifecycle = WorkerLifecycle(root / "openjules.sqlite", runtime)
        reference, token = credential
        app = create_worker_app(lifecycle, {token: [scope_a, scope_b]})
        store = None
        checks = []
        try:
            with local_server(app) as endpoint:
                provider = DropFirstLaunchReply(ProviderConfig.parse({
                    "schema_version": 1, "endpoint": endpoint,
                    "token_ref": "env:" + reference, "max_workers": 4,
                    "timeout_seconds": 2,
                }))
                store = Store(root / "zpm.sqlite", deployment)
                for repository in (repo_a, repo_b):
                    store.enroll({
                        "schema_version": 1, "repository": repository,
                        "profile": "fixture", "desired_workers": 0,
                    })
                controller = Controller(store, provider, max_workers=4)
                require(controller.tick()["reserved"] == 0, "zero target launched a worker")
                store.change(repo_a, "target", 2)
                store.change(repo_b, "target", 1)
                first = controller.tick()
                require(first["reserved"] == 2 and runtime.launches == 2,
                        "lost reply did not retain exactly one uncertain reservation")
                require(any(row["snapshot"]["state"] == "unknown" for row in store.workers(repo_a)),
                        "lost launch reply did not expose uncertainty")
                store.close()
                store = Store(root / "zpm.sqlite")
                controller = Controller(store, provider, max_workers=4)
                recovered = controller.tick()
                require(recovered["reserved"] == 3 and runtime.launches == 3,
                        "controller restart duplicated or failed to recover the launch")
                require(all(row["healthy"] == row["desired_workers"] for row in recovered["repositories"]),
                        "repositories did not reach their independent targets")
                require(controller.tick()["reserved"] == 3 and runtime.launches == 3,
                        "stable reconciliation launched an extra worker")
                checks.append("lost successful HTTP launch reply and ZPM reopen recover the same incarnation")

                try:
                    provider.discover("foreign-deployment", repo_a)
                    raise AssertionError("foreign deployment was authorized")
                except ProviderError as error:
                    require("403" in str(error), "foreign scope did not produce authorization denial")
                checks.append("authenticated HTTP operations reject a foreign deployment")

                worker_id = store.workers(repo_a)[0]["snapshot"]["worker_id"]
                lifecycle.begin_execution(scope_a, worker_id, "root-attempt")
                lifecycle.begin_execution(scope_a, worker_id, "child-attempt", "root-attempt")
                require(controller.tick()["reserved"] == 3 and runtime.launches == 3,
                        "registered child occupancy added a replica")
                store.change(repo_a, "target", 3)
                scaled = controller.tick()
                require(scaled["reserved"] == 4 and runtime.launches == 4, "target three did not converge")
                store.change(repo_a, "target", 1)
                scaled = controller.tick()
                require(scaled["reserved"] == 2 and runtime.stops == 2, "target one did not retire idle workers")
                require(lifecycle.observe_worker(scope_a, worker_id)["state"] == "busy",
                        "scale-down retired the busy worker ahead of idle workers")
                checks.append("0 to 2 to 3 to 1 capacity prefers idle retirement and children add no replicas")

                store.change(repo_a, "target", 0)
                held = controller.tick()
                by_repo = {item["repository"]: item for item in held["repositories"]}
                require(by_repo[repo_a]["reserved"] == 1 and by_repo[repo_b]["healthy"] == 1,
                        "drain lost child occupancy or changed the second repository")
                require(by_repo[repo_b]["desired_workers"] == 1, "another repository's target changed")
                require(lifecycle.observe_worker(scope_a, worker_id)["state"] == "draining",
                        "zero target did not close worker admission")
                try:
                    lifecycle.begin_execution(scope_a, worker_id, "late-child", "root-attempt")
                    raise AssertionError("draining worker admitted a child")
                except LifecycleConflict:
                    pass
                lifecycle.record_execution_end(scope_a, worker_id, "root-attempt",
                                               termination_confirmed=True, artifacts_captured=True)
                require(controller.tick()["reserved"] == 2, "root completion bypassed unresolved child capture")
                lifecycle.record_execution_end(scope_a, worker_id, "child-attempt",
                                               termination_confirmed=True, artifacts_captured=True)
                require(controller.tick()["reserved"] == 1, "confirmed execution did not permit retirement")
                store.change(repo_b, "target", 0)
                require(controller.tick()["reserved"] == 0, "scale to zero retained a completed reservation")
                checks.append("drain holds registered child occupancy until trusted termination and capture facts")

                def defer_one_dispatch():
                    """Journal an incarnation over HTTP without dispatching it.

                    This injects the crash boundary deterministically; it does
                    not claim that the HTTP server process was actually killed.
                    """
                    original = lifecycle._resume_ensure
                    called = False

                    def defer(spec, incarnation):
                        nonlocal called
                        if not called:
                            called = True
                            return lifecycle._current(spec.scope, incarnation)
                        return original(spec, incarnation)

                    lifecycle._resume_ensure = defer
                    try:
                        controller.tick()
                    finally:
                        lifecycle._resume_ensure = original
                    require(called, "pre-dispatch fault was not reached")
                    rows = [row for row in store.workers(repo_a) if row["snapshot"]["state"] == "unknown"]
                    require(len(rows) == 1, "journal-only launch did not remain a single unknown reservation")
                    return rows[0]

                store.change(repo_a, "target", 1)
                pending = defer_one_dispatch()
                require(runtime.launches == 4, "deferred launch unexpectedly started a subprocess")
                store.close()
                store = Store(root / "zpm.sqlite")
                controller = Controller(store, provider, max_workers=4)
                controller.tick()
                resumed = store.worker(pending["launch_key"])
                require(resumed["snapshot"]["worker_id"] == pending["snapshot"]["worker_id"],
                        "resume changed the journalled worker incarnation")
                require(resumed["snapshot"]["state"] == "ready" and runtime.launches == 5,
                        "discovery of an unknown journal row stranded or duplicated the original ensure")
                checks.append("pre-dispatch journal recovery resumes the original launch key and incarnation")
                store.change(repo_a, "target", 0)
                require(controller.tick()["reserved"] == 0, "recovered worker did not retire")

                store.change(repo_a, "target", 1)
                cancelled = defer_one_dispatch()
                store.change(repo_a, "target", 0)
                for _ in range(3):
                    if controller.tick()["reserved"] == 0:
                        break
                require(controller.plan()["reserved"] == 0 and runtime.launches == 5,
                        "cancellation of journal-only launch remained stranded or dispatched new work")
                original_spec = {key: cancelled[key] for key in
                                 ("repository", "slot_id", "generation", "launch_key", "profile")}
                original_spec.update(schema_version=1, deployment_id=deployment)
                require(provider.ensure(original_spec)["state"] == "stopped" and runtime.launches == 5,
                        "cancelled incarnation was recreated by a delayed ensure")
                checks.append("zero target cancels a journal-only launch and later ensure cannot recreate it")

                artifacts = list(root.glob("*.artifact"))
                require(runtime.stops == 5 and len(artifacts) == 5,
                        "real subprocess termination or artifact count did not match launches")
                require(all(path.read_text() == "fixture-work\n" for path in artifacts),
                        "captured subprocess output did not match expected work")
                require(all(process.poll() is not None for process in runtime.processes.values()),
                        "a subprocess remained active after scale to zero")
                checks.append("all five real subprocesses terminate and their expected output is captured")

                lifecycle.runtime = UnavailableRuntime()
                store.change(repo_a, "target", 1)
                blocked = controller.tick()
                require(blocked["reserved"] == 0 and runtime.launches == 5,
                        "unavailable runtime authorized managed provisioning")
                require(not next(item for item in blocked["repositories"] if item["repository"] == repo_a)["validation"]["ready"],
                        "unavailable runtime was reported ready")
                checks.append("default unavailable runtime blocks provisioning through the actual HTTP contract")
                return {
                    "result": "passed", "checks": checks, "real_subprocesses": 5,
                    "scope": "HTTP lifecycle contract with subprocess fixture; no production agent, Docker or Polyphony proof",
                }
        finally:
            if store is not None:
                store.close()
            runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openjules-checkout", type=Path, required=True,
                        help="checkout containing the proposed OpenJules lifecycle API and fixture")
    args = parser.parse_args()
    checkout = args.openjules_checkout.resolve()
    required = ("src/worker/lifecycle.py", "src/api/worker_lifecycle.py", "tests/unit/test_worker_lifecycle.py")
    if not all((checkout / path).is_file() for path in required):
        parser.error("OpenJules checkout must contain its worker lifecycle implementation and subprocess fixture")
    print(json.dumps(run_trial(checkout), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
