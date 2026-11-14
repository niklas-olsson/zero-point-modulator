"""Two independent managed runtimes contending for one canonical task.

This is an explicit runtime-contract fixture. It does not configure a replicated
ZPM installation. Only the authority allocates an attempt; the loopback proxy
holds two real claim requests and forwards their original bytes and responses.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import stat
import subprocess
import tarfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from check_stack import Client, NoRedirect, Processes, clean_environment, eventually, port, private_json, require
from check_authority_admission import SCHEMAS, RECORD_KINDS, canonical, require_empty_snapshot, require_runtime_capabilities, stop_owned
from check_isolation_stack import wait_for_capture, require_live_container, require_active_runtime, classify_active_root
from check_multi_repository_stack import concurrent_ingress


ATTEMPT_SECONDS = 60
TASK_BUDGET = 120
WATCHDOG_SECONDS = 90
CLAIM_WINDOW = 5
REJECTION = {"error": "canonical claim was not allocated", "code": "claim_not_allocated", "allocated": False}
SPEC_FIELDS = ("schema_version", "deployment_id", "repository", "slot_id", "generation", "launch_key", "profile")
TASK_PROGRAM = """from pathlib import Path
import os, sys, time
assert os.getuid() == 65532
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/openjules-model').exists()
assert not any(name in os.environ for name in ('POLYPHONY_RUNTIME_TOKEN', 'OPENJULES_WORKER_TOKEN', 'GITHUB_TOKEN'))
assert sys.stdin.read() == 'Make the controlled answer equal42 exactly once.'
assert Path('answer.py').read_text() == 'answer = 0\\n'
with Path('.contention-start').open('x') as started:
    started.write('one protected invocation\\n')
counter = 0
while not Path('.contention-release').exists():
    counter += 1
    Path('.contention-progress.tmp').write_text(str(counter))
    Path('.contention-progress.tmp').replace('.contention-progress')
    time.sleep(0.1)
Path('answer.py').write_text('answer = 42\\n')
assert Path('.contention-start').read_text() == 'one protected invocation\\n'
print('ONE_PROTECTED_EXECUTION_COMPLETED', flush=True)
"""
REQUIREMENTS = "Make the controlled answer equal42 exactly once."


class ContractFailure(AssertionError):
    def __init__(self, phase, diagnostic):
        super().__init__("two-runtime contract failed during " + phase)
        self.contract_diagnostic = diagnostic


class ClaimBarrier:
    """Bounded, authenticated pass-through; never manufactures claim results."""

    def __init__(self, upstream, tokens, scope, profile, base):
        require(urlsplit(upstream).hostname == "127.0.0.1", "claim upstream must be fixed loopback")
        require(len(tokens) == 2 and len(set(tokens.values())) == 2, "claim principals must be distinct")
        self.upstream, self.tokens = upstream, dict(tokens)
        self.scope, self.profile, self.base = dict(scope), profile, base
        self.condition = threading.Condition()
        self.release_event = threading.Event()
        self.claims, self.results, self.completed, self.inflight = {}, {}, Counter(), Counter()
        self.open_requests, self.errors, self.closed = 0, [], False
        self.epoch, self.stop_state, self.paused_principal = 1, None, None
        self.closed_responses = Counter()
        barrier = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def __init__(self, address, handler, epoch):
                super().__init__(address, handler)
                self.epoch, self.handlers, self.sockets = epoch, [], {}
                self.fence_principal = None

            def process_request(self, request, address):
                thread = threading.Thread(
                    target=self.process_request_thread, args=(request, address),
                    name="contract-proxy-request-" + str(self.epoch), daemon=True)
                # Register the accepted socket and actual thread object before
                # thread.start; an accepted-but-unscheduled handler is owned too.
                with barrier.condition:
                    barrier.open_requests += 1
                    self.handlers.append(thread)
                    self.sockets[thread] = request
                    barrier.condition.notify_all()
                try:
                    thread.start()
                except BaseException:
                    with barrier.condition:
                        self.handlers.remove(thread)
                        self.sockets.pop(thread)
                        barrier.open_requests -= 1
                        barrier.errors.append("HandlerStartFailure")
                        barrier.condition.notify_all()
                    self.shutdown_request(request)
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    with barrier.condition:
                        self.sockets.pop(threading.current_thread())
                        barrier.open_requests -= 1
                        barrier.condition.notify_all()

            def handle_error(self, *_):
                with barrier.condition:
                    barrier.errors.append("HandlerFailure")
                    barrier.condition.notify_all()

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_forward()

            def do_POST(self):
                self.handle_forward()

            def handle_forward(self):
                principal, operation, registered = None, None, False
                try:
                    bearer = self.headers.get("Authorization", "")
                    principal = barrier.tokens.get(bearer)
                    require(principal is not None, "forwarder received an unknown runtime credential")
                    operation = urlsplit(self.path).path
                    require(operation in {"/managed/" + value for value in
                            ("capabilities", "snapshot", "claim", "permission", "capture", "observe", "candidate")},
                            "forwarder received an unsupported operation")
                    require((self.command == "GET") == (operation in {"/managed/capabilities", "/managed/snapshot"}),
                            "forwarder received an unexpected method")
                    require(not self.headers.get("Transfer-Encoding"), "forwarder requires a bounded request")
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 <= size <= 1_000_000, "forwarder request exceeds its bound")
                    body = self.rfile.read(size)
                    require(len(body) == size, "forwarder request body is incomplete")
                    with barrier.condition:
                        barrier.inflight[principal] += 1
                        registered = True
                        barrier.condition.notify_all()
                    if operation == "/managed/claim":
                        payload = json.loads(body)
                        barrier.arrive(principal, payload)
                        require(barrier.release_event.wait(CLAIM_WINDOW), "both runtime claims did not reach the barrier")
                        with barrier.condition:
                            require(not barrier.closed, "claim barrier was closed before forwarding")
                    request = Request(barrier.upstream + self.path, method=self.command,
                                      data=body if self.command == "POST" else None,
                                      headers={"Authorization": bearer, "Content-Type": "application/json"})
                    opener = build_opener(ProxyHandler({}), NoRedirect())
                    try:
                        response = opener.open(request, timeout=3)
                    except HTTPError as error:
                        response = error
                    try:
                        status = response.code
                        content = response.read(1_000_001)
                        require(len(content) <= 1_000_000, "forwarder response exceeds its bound")
                        content_type = response.headers.get("Content-Type", "application/json")
                    finally:
                        response.close()
                    if operation == "/managed/claim":
                        barrier.record_result(principal, status, json.loads(content))
                    try:
                        self.send_response(status)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(content)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(content)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        # Only the downstream write can be an expected old
                        # client disconnect. Upstream open/read errors always
                        # reach the ordinary failure path below.
                        if (self.server.fence_principal != principal
                                or operation not in {"/managed/capabilities", "/managed/snapshot"}):
                            raise
                        with barrier.condition:
                            barrier.closed_responses[(self.server.epoch, principal)] += 1
                            barrier.condition.notify_all()
                        return
                    with barrier.condition:
                        barrier.completed[(self.server.epoch, principal, operation, status)] += 1
                        barrier.condition.notify_all()
                except Exception as error:
                    with barrier.condition:
                        barrier.errors.append(type(error).__name__)
                        barrier.condition.notify_all()
                    try:
                        self.send_error(502, "bounded contract forwarder failed")
                    except OSError:
                        pass
                finally:
                    if registered:
                        with barrier.condition:
                            barrier.inflight[principal] -= 1
                            barrier.condition.notify_all()

        self.server_type, self.handler_type = Server, Handler
        self._listen(("127.0.0.1", 0))
        self.address = self.server.server_address
        self.endpoint = "http://127.0.0.1:" + str(self.server.server_port)

    def _listen(self, address):
        self.server = self.server_type(address, self.handler_type, self.epoch)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            name="contract-proxy-listener-" + str(self.epoch), daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise

    def arrive(self, principal, payload):
        fields = {"schema_version", "deployment_id", "repository", "profile", "worker_id", "task_id", "request_key", "base"}
        require(isinstance(payload, dict) and set(payload) == fields
                and type(payload["schema_version"]) is int and payload["schema_version"] == 1
                and all(payload[key] == value for key, value in self.scope.items())
                and payload["profile"] == self.profile and payload["base"] == self.base
                and all(isinstance(payload[key], str) and payload[key] for key in ("worker_id", "task_id", "request_key")),
                "claim barrier received an invalid immutable binding")
        with self.condition:
            require(principal not in self.claims, "claim barrier received a retry instead of a second contender")
            self.claims[principal] = deepcopy(payload)
            self.condition.notify_all()

    def pair(self):
        with self.condition:
            if len(self.claims) != 2:
                return None
            values = list(self.claims.values())
            require(values[0]["task_id"] == values[1]["task_id"]
                    and values[0]["worker_id"] != values[1]["worker_id"]
                    and values[0]["request_key"] != values[1]["request_key"],
                    "claim barrier did not hold two independent claims for one task")
            return deepcopy(self.claims)

    def release(self):
        require(self.pair() is not None, "cannot release an incomplete claim pair")
        self.release_event.set()

    def record_result(self, principal, status, value):
        with self.condition:
            require(principal not in self.results, "claim response was duplicated")
            self.results[principal] = {"status": status, "value": deepcopy(value)}
            self.condition.notify_all()

    def outcome(self):
        with self.condition:
            if len(self.results) != 2:
                return None
            statuses = sorted(value["status"] for value in self.results.values())
            require(statuses == [200, 409], "canonical claims did not produce one allocation and one rejection")
            winner = next(key for key, value in self.results.items() if value["status"] == 200)
            loser = next(key for key, value in self.results.items() if value["status"] == 409)
            require(canonical(self.results[loser]["value"]) == canonical(REJECTION),
                    "canonical claim rejection is not explicit nonallocation")
            response = self.results[winner]["value"]
            claim = self.claims[winner]
            binding = response.get("binding", {})
            attempt = response.get("attempt", {})
            require(type(response.get("schema_version")) is int and response["schema_version"] == 1
                    and all(response.get(key) == value for key, value in self.scope.items())
                    and response.get("launch_allowed") is True
                    and response.get("requirements") == REQUIREMENTS
                    and all(binding.get(key) == claim[key] for key in ("deployment_id", "repository", "profile", "worker_id", "base"))
                    and binding.get("runtime_principal") == winner
                    and binding.get("attempt_id") == attempt.get("id")
                    and isinstance(attempt.get("id"), str) and attempt["id"]
                    and binding.get("launch_key") == "polyphony:" + attempt["id"],
                    "allocated claim changed the winning immutable binding")
            return winner, loser, deepcopy(response)

    def count(self, principal, operation, *, epoch=None):
        with self.condition:
            return self.completed[(self.epoch if epoch is None else epoch,
                                   principal, "/managed/" + operation, 200)]

    def idle(self, principal):
        with self.condition:
            return self.inflight[principal] == 0

    def check(self):
        with self.condition:
            require(not self.errors and self.thread.is_alive(), "contract forwarder failed")

    @staticmethod
    def _remaining(state):
        return max(0, state["deadline"] - time.monotonic())

    def _begin_stop(self, principal=None):
        require(self.stop_state is None, "proxy listener already has a stop operation")
        self.server.fence_principal = principal
        shutdown = threading.Thread(target=self.server.shutdown,
                                    name="contract-proxy-shutdown-" + str(self.epoch), daemon=True)
        state = {"server": self.server, "listener": self.thread, "shutdown": shutdown,
                 "epoch": self.epoch, "deadline": time.monotonic() + CLAIM_WINDOW}
        self.stop_state = state
        try:
            shutdown.start()
        finally:
            # block_on_close=False: close the listener/backlog without waiting
            # inside socketserver for its request threads.
            self.server.server_close()
        return state

    def _join_acceptor(self, state):
        for thread in (state["shutdown"], state["listener"]):
            if thread.ident is not None:
                thread.join(self._remaining(state))
        require(time.monotonic() <= state["deadline"]
                and state["shutdown"].ident is not None and not state["shutdown"].is_alive()
                and not state["listener"].is_alive()
                and state["server"].socket.fileno() == -1,
                "proxy acceptor did not stop within its original fence deadline")

    def _reap(self, state):
        self._join_acceptor(state)
        # No listener remains to append an accepted thread after this snapshot.
        threads = tuple(state["server"].handlers)
        for thread in threads:
            thread.join(self._remaining(state))
        with self.condition:
            complete = (time.monotonic() <= state["deadline"]
                        and all(not thread.is_alive() for thread in threads)
                        and not state["server"].sockets and self.open_requests == 0)
        if not complete:
            self._abort_sockets(state)
        require(complete, "proxy accepted handlers did not finish within the original fence deadline")
        return len(threads)

    def _abort_sockets(self, state):
        with self.condition:
            owned = tuple(state["server"].sockets.values())
        for request in owned:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            request.close()

    def pause_for_restart(self, principal):
        require(not self.closed and self.paused_principal is None
                and principal in self.tokens.values(), "proxy restart fence is not owned")
        self.paused_principal = principal
        state = self._begin_stop(principal)
        self._join_acceptor(state)

    def reopen_after_reap(self, principal, original_process):
        require(self.paused_principal == principal and self.stop_state is not None
                and original_process.poll() == -9,
                "proxy cannot reopen while the original losing process remains")
        state = self.stop_state
        count = self._reap(state)
        previous = self.count(principal, "snapshot", epoch=state["epoch"])
        self.epoch += 1
        self._listen(self.address)
        require(self.server.server_address == self.address, "proxy endpoint changed during its fence")
        self.stop_state, self.paused_principal = None, None
        return {"old_epoch": state["epoch"], "new_epoch": self.epoch,
                "accepted_handlers_reaped": count, "old_listener_closed": True,
                "same_endpoint": True, "old_snapshot_completions": previous,
                "old_generation_client_disconnects": self.closed_responses[(state["epoch"], principal)]}

    def close(self):
        self.closed = True
        self.release_event.set()
        state = self.stop_state or self._begin_stop()
        # A failed qualification fence never reopens. Cleanup gets its existing
        # bounded reap attempt and can interrupt only its owned client sockets.
        if self._remaining(state) == 0:
            self._abort_sockets(state)
            state["deadline"] = time.monotonic() + CLAIM_WINDOW
        try:
            self._reap(state)
        except BaseException:
            self._abort_sockets(state)
            raise


def journal(path, schema, maximum=8):
    """Read every row within a strict bound; never hide another scope by filtering."""
    require(stat.S_ISREG(path.lstat().st_mode), "contract journal is not a regular existing file")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        result = {}
        for table, columns in schema.items():
            actual = tuple(row[1] for row in db.execute('PRAGMA table_info("' + table + '")'))
            require(actual == columns, "contract journal schema changed")
            rows = db.execute('SELECT * FROM "' + table + '" ORDER BY rowid LIMIT ?', (maximum + 1,)).fetchall()
            require(len(rows) <= maximum, "contract journal contains unexpected history")
            result[table] = [dict(row) for row in rows]
        return result
    finally:
        db.close()


def histories(config):
    state = Path(config["state_directory"])
    return {
        "lifecycle": journal(Path(config["lifecycle_database"]), SCHEMAS["lifecycle"]),
        "runtime": journal(state / "runtime/journal.sqlite3", SCHEMAS["runtime"]),
        "dispatcher": journal(state / "dispatcher/dispatch.sqlite3", SCHEMAS["dispatcher"]),
    }


def only_worker(history, spec, worker_id):
    lifecycle, runtime = history["lifecycle"], history["runtime"]
    require(len(lifecycle["managed_workers"]) == len(runtime["workers"]) == 1, "runtime worker history changed")
    local, driver = lifecycle["managed_workers"][0], runtime["workers"][0]
    require(local["worker_id"] == driver["worker_id"] == worker_id
            and canonical(json.loads(local["spec"])) == canonical(spec)
            and local["destination_binding"] is None
            and canonical(json.loads(driver["spec"])) == canonical(spec)
            and local["runtime_id"] == "docker:" + driver["container_id"],
            "runtime incarnation binding changed")
    require(len(runtime["profiles"]) == len(runtime["child_profiles"]) == 1,
            "contract changed its profile cardinality")
    profile, child = runtime["profiles"][0], runtime["child_profiles"][0]
    key = {"deployment_id": spec["deployment_id"], "repository": spec["repository"], "name": spec["profile"]}
    fingerprint = profile["fingerprint"]
    require(all(profile[name] == value and child[name] == value for name, value in key.items())
            and isinstance(fingerprint, str) and len(fingerprint) == 64
            and all(value in "0123456789abcdef" for value in fingerprint)
            and driver["profile_fingerprint"] == fingerprint and child["fingerprint"] is None,
            "contract changed its command-only scoped profile")
    require(driver["start_confirmed"] == 1 and driver["container_id"], "runtime has no confirmed original container")
    return local, driver


def require_idle(history, spec, worker_id, container=None):
    local, driver = only_worker(history, spec, worker_id)
    require(not history["lifecycle"]["worker_executions"] and not history["runtime"]["attempts"]
            and not history["dispatcher"]["deliveries"], "losing runtime retained protected execution")
    require(local["draining"] == 0 and driver["retired"] == 0 and driver["receipt"] is None,
            "idle runtime was controlled or retired")
    require(len(history["lifecycle"]["worker_operations"]) == 1
            and history["lifecycle"]["worker_operations"][0]["action"] == "ensure"
            and history["lifecycle"]["worker_operations"][0]["request_key"] == spec["launch_key"]
            and history["lifecycle"]["worker_operations"][0]["completed"] == 1
            and history["lifecycle"]["worker_operations"][0]["worker_id"] == worker_id
            and all(history["lifecycle"]["worker_operations"][0][name] == spec[name]
                    for name in ("deployment_id", "repository"))
            and canonical(json.loads(history["lifecycle"]["worker_operations"][0]["payload"])) == canonical(spec),
            "idle runtime acquired a foreign control")
    require(container is None or driver["container_id"] == container, "loser restart changed its container")
    return driver["container_id"]


def require_claiming(history, spec, worker_id, claim):
    only_worker(history, spec, worker_id)
    require(not history["lifecycle"]["worker_executions"] and not history["runtime"]["attempts"],
            "protected execution preceded the canonical claim")
    rows = history["dispatcher"]["deliveries"]
    require(len(rows) == 1 and rows[0]["worker_id"] == worker_id
            and rows[0]["task_id"] == claim["task_id"] and rows[0]["request_key"] == claim["request_key"]
            and rows[0]["phase"] == "claiming" and rows[0]["binding"] is None
            and canonical(json.loads(rows[0]["spec"])) == canonical(spec),
            "dispatcher did not retain the independently held claim")


def require_running(history, spec, worker_id, claim, response, frozen=None):
    local, driver = only_worker(history, spec, worker_id)
    attempt_id = response["attempt"]["id"]
    require(local["draining"] == 0 and driver["retired"] == 0 and driver["receipt"] is None,
            "winner was controlled or prematurely captured")
    executions, attempts, deliveries = (history["lifecycle"]["worker_executions"],
                                        history["runtime"]["attempts"], history["dispatcher"]["deliveries"])
    require(len(executions) == len(attempts) == len(deliveries) == 1, "winner execution cardinality changed")
    execution, attempt, delivery = executions[0], attempts[0], deliveries[0]
    require(execution == {"worker_id": worker_id, "execution_id": attempt_id, "parent_id": None,
                          "termination_confirmed": 0, "artifacts_captured": 0}
            and attempt["worker_id"] == worker_id and attempt["attempt_id"] == attempt_id
            and attempt["exec_dispatched"] == 1 and isinstance(attempt["exec_id"], str) and attempt["exec_id"]
            and attempt["cancelled"] == 0 and attempt["exit_code"] is None
            and 0 < attempt["seconds"] <= ATTEMPT_SECONDS
            and attempt["input_sha256"] == hashlib.sha256(REQUIREMENTS.encode()).hexdigest()
            and delivery["worker_id"] == worker_id and delivery["task_id"] == claim["task_id"]
            and delivery["request_key"] == claim["request_key"] and delivery["phase"] == "dispatching"
            and canonical(json.loads(delivery["binding"])["binding"]) == canonical(response["binding"]),
            "winner changed its original execution, deadline or claim")
    immutable = {"container_id": driver["container_id"], "attempt": attempt,
                 "execution": execution, "delivery": delivery}
    require(frozen is None or canonical(immutable) == canonical(frozen), "winner execution was reset during peer recovery")
    return immutable


def canonical_state(path, scope, task_id, response, observed, frozen=None):
    require(isinstance(observed, dict)
            and set(observed) == set(RECORD_KINDS) | {"schema_version", "deployment_id", "repository", "cursor"}
            and type(observed.get("cursor")) is int and observed["cursor"] > 0
            and type(observed.get("schema_version")) is int and observed["schema_version"] == 1 and all(observed.get(key) == value for key, value in scope.items())
            and len(observed.get("task", [])) == len(observed.get("attempt", [])) == 1
            and observed["task"][0].get("id") == task_id
            and observed["attempt"][0].get("id") == response["attempt"]["id"]
            and observed["attempt"][0].get("permission_current") is True
            and not observed.get("candidate") and not observed.get("admission"),
            "canonical snapshot no longer permits the original unresolved attempt")
    data = journal(path, SCHEMAS["authority"], maximum=64)
    counts = Counter(row["kind"] for row in data["records"])
    require(counts == {"task": 1, "managed_source": 1, "start": 1, "attempt": 1}
            and not data["artifacts"], "canonical authority allocated additional or premature work")
    records = {row["kind"]: json.loads(row["body"]) for row in data["records"]}
    task, attempt, start = records["task"], records["attempt"], records["start"]
    require(all(type(task[key]) in (int, float) and math.isfinite(task[key])
                for key in ("allocated_seconds", "budget_seconds"))
            and all(type(task[key]) is int for key in ("attempts", "max_attempts"))
            and type(attempt["number"]) is int
            and task["id"] == task_id and task["managed_scope"] == scope
            and task["status"] == "in_progress" and task["attempts"] == 1
            and task["allocated_seconds"] == ATTEMPT_SECONDS
            and task["max_attempts"] == 2 and task["budget_seconds"] == TASK_BUDGET
            and attempt["id"] == response["attempt"]["id"] and attempt["task"] == task_id
            and attempt["number"] == 1 and attempt["state"] == "unknown"
            and attempt["termination_confirmed"] is False
            and attempt["execution_mode"] == "external"
            and canonical(attempt["runtime_binding"]) == canonical(response["binding"])
            and start["attempt"] == attempt["id"],
            "canonical attempt or unspent retry budget changed")
    require(frozen is None or canonical(data) == canonical(frozen), "peer recovery changed canonical protected history")
    return data


def container_running(container_id, worker_id, spec, fingerprint):
    result = subprocess.run(["docker", "inspect", container_id], check=True, capture_output=True, text=True, timeout=10)
    value = json.loads(result.stdout)
    require(len(value) == 1, "original worker container is missing or ambiguous")
    require_live_container(value[0], {"container_id": container_id, "worker_id": worker_id,
                                     "spec": spec, "profile_fingerprint": fingerprint})
    return value[0]


def progress(container_id):
    result = subprocess.run(["docker", "cp", container_id + ":/workspace/run/repo/.contention-progress", "-"],
                            capture_output=True, timeout=10)
    if result.returncode:
        return None
    require(len(result.stdout) <= 20480, "progress archive exceeds its bound")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:*") as archive:
        members = archive.getmembers()
        require(len(members) == 1 and members[0].isfile() and members[0].size <= 32,
                "progress archive has an unexpected member")
        value = archive.extractfile(members[0]).read(33).decode()
    require(value.isdecimal() and 0 < int(value) < 100000, "guest progress counter is invalid")
    return int(value)


def release(container_id):
    subprocess.run(["docker", "exec", "--user", "65532", container_id, "touch",
                    "/workspace/run/repo/.contention-release"], check=True, capture_output=True, timeout=10)


def terminal(value, spec, worker_id):
    require(value["worker_id"] == worker_id and all(value[key] == spec[key] for key in SPEC_FIELDS),
            "terminal worker changed identity")
    return (value.get("state") in {"stopped", "failed"} and value.get("termination_confirmed") is True
            and value.get("artifacts_captured") is True and not value.get("active_executions"))



def require_terminal_controls(history, spec, worker_id, keys, container, *, allow_pending=False):
    local, driver = only_worker(history, spec, worker_id)
    require(local["draining"] == 1 and local["termination_confirmed"] == 1
            and local["artifacts_captured"] == 1 and driver["retired"] == 1
            and driver["container_id"] == container,
            "owned terminal controls changed their original worker or capture")
    expected = [{
        "deployment_id": spec["deployment_id"], "repository": spec["repository"],
        "request_key": spec["launch_key"], "action": "ensure", "worker_id": worker_id,
        "payload": canonical(spec), "completed": 1,
    }]
    expected.extend({
        "deployment_id": spec["deployment_id"], "repository": spec["repository"],
        "request_key": keys[action], "action": action, "worker_id": worker_id,
        "payload": canonical({"worker_id": worker_id, "action": action}), "completed": 1,
    } for action in ("drain", "stop"))
    operations = history["lifecycle"]["worker_operations"]
    require(len(operations) == len(expected)
            and all(type(row["completed"]) is int and row["completed"] in (0, 1) for row in operations)
            and canonical([{key: value for key, value in row.items() if key != "completed"} for row in operations])
            == canonical([{key: value for key, value in row.items() if key != "completed"} for row in expected]),
            "terminal history lacks the exact original scoped control keys")
    receipt = json.loads(driver["receipt"])
    require(receipt["termination_confirmed"] is True and receipt["artifacts_captured"] is True
            and receipt["launch_fenced"] is True,
            "terminal controls lack a durable runtime capture and fence")
    complete = all(row["completed"] == 1 for row in operations)
    require(allow_pending or complete, "original terminal controls remain pending")
    return complete


def control(runtime, scope, worker_id, action, key):
    return runtime.request("POST", "/api/workers/" + worker_id + "/" + action,
                           data={"schema_version": 1, **scope, "request_key": key})


def cleanup(processes, barrier, members, scope, original=None):
    failures = []
    for member in members.values():
        if member.get("worker_id"):
            for action in ("drain", "stop"):
                try:
                    control(member["client"], scope, member["worker_id"], action, member["controls"][action])
                except Exception:
                    failures.append(member["label"] + "-" + action)
    for name in reversed(list(processes.running)):
        try:
            stop_owned(processes, name)
        except BaseException:
            failures.append("owned-service-reap-" + name)
    for handle in processes.handles:
        try:
            handle.close()
        except BaseException:
            failures.append("owned-log-close")
    try:
        barrier.close()
    except BaseException:
        failures.append("owned-forwarder-reap")
    if failures:
        if original is not None:
            original.add_note("contract cleanup incomplete: " + ",".join(failures))
            if hasattr(original, "contract_diagnostic"):
                original.contract_diagnostic["cleanup_failures"] = failures
        else:
            raise ContractFailure("owned cleanup", {"cleanup_failures": failures, "logs": {}})


def failure_diagnostic(root, phase, error, completed, sensitive):
    diagnostic = {"phase": phase, "error_type": type(error).__name__,
                  "completed_checks": list(completed), "logs": {}}
    for path in sorted(root.glob("*.log")):
        content = path.read_bytes()[-(20000 + max(map(len, sensitive), default=0)):].decode(errors="replace")
        for token in sensitive:
            content = content.replace(token, "[redacted]")
        diagnostic["logs"][path.name] = content.encode()[-4000:].decode(errors="ignore")
    return diagnostic


def run_contract(parent_root, installed_bundle, original_data, source_paths, revisions, image):
    """Attach after the generated installation's ordinary scene has stopped."""
    from zpm.deployment import _command, _environment, prepare, verify

    root = parent_root / "two-runtime-contract"
    require(not root.exists(), "two-runtime contract directory must be fresh")
    root.mkdir(mode=0o700)
    source = root / "source"
    source.mkdir(mode=0o755)
    (source / "answer.py").write_text("answer = 0\n")
    (source / ".gitignore").write_text(".contention-*\n")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=Runtime Contract", "-c", "user.email=contract@example.invalid", "commit", "-qm", "controlled base"]):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True, timeout=15)
    base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    deployment, repository, profile = "two-runtime-" + secrets.token_hex(8), "github:github.com/managed-runtime-contract/repository", "controlled-contention"
    scope = {"deployment_id": deployment, "repository": repository}
    ports = set()
    while len(ports) < 3:
        ports.add(port())
    authority_port, port_a, port_b = sorted(ports)
    bundle = root / "seed"
    prepare({"schema_version": 1, "deployment_id": deployment,
             "binding": {"schema_version": 1, "repository": repository, "profile": profile, "desired_workers": 0},
             "max_workers": 2, "sources": original_data["sources"],
             "source": {"checkout": str(source), "revision": base}, "image": image,
             "command": ["python3", "-c", TASK_PROGRAM], "ports": {"polyphony": authority_port, "openjules": port_a},
             "runtime": {"max_seconds": WATCHDOG_SECONDS, "pids_limit": 64},
             "policy": {"checks": ["controlled-test"], "reviewers": ["operator"], "max_attempts": 2,
                        "budget_seconds": TASK_BUDGET, "attempt_seconds": ATTEMPT_SECONDS, "capabilities": ["python"]}}, bundle)
    verify(bundle)
    data = json.loads((bundle / "manifest.json").read_text())["configuration"]
    sealed = {path.relative_to(bundle).as_posix(): path.read_bytes()
              for folder in ("config", "secrets") for path in (bundle / folder).iterdir()}
    environment = json.loads((bundle / "secrets/environment.json").read_text())
    operators = json.loads((bundle / "secrets/operator.json").read_text())
    credentials = json.loads((bundle / "secrets/polyphony.json").read_text())
    token_a = environment["POLYPHONY_RUNTIME_TOKEN"]
    principal_a = credentials[token_a]["identity"]
    token_b, controller_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    principal_b = "second-runtime"
    require(principal_b != principal_a, "contract runtime principals alias")
    credentials[token_b] = {**credentials[token_a], "identity": principal_b}
    private_json(root / "authority-credentials.json", credentials)
    private_json(root / "second-controller.json", {controller_b: [scope]})
    authority_endpoint = "http://127.0.0.1:" + str(authority_port)
    barrier = ClaimBarrier(authority_endpoint, {"Bearer " + token_a: principal_a, "Bearer " + token_b: principal_b}, scope, profile, base)
    configs, members = {}, {}
    processes = Processes(root)
    sensitive = set(environment.values()) | set(operators.values()) | {token_b, controller_b}
    phase, original = "configuration", None
    completed = []
    try:
        for label, principal, runtime_port, runtime_token, controller_token in (
                ("a", principal_a, port_a, token_a, environment["OPENJULES_WORKER_TOKEN"]),
                ("b", principal_b, port_b, token_b, controller_b)):
            config = json.loads((bundle / "config/openjules.json").read_text())
            config.update(port=runtime_port, state_directory=str(root / ("runtime-" + label)),
                          lifecycle_database=str(root / ("lifecycle-" + label + ".sqlite3")))
            config["authority"]["endpoint"] = barrier.endpoint
            if label == "b":
                config["credentials_file"] = str(root / "second-controller.json")
            require(not config.get("models") and not config.get("children"), "contract enabled inference or child execution")
            config_path = root / ("openjules-" + label + ".json")
            private_json(config_path, config)
            command = [str(installed_bundle / "environments/openjules/bin/python"), "-m", "src.worker.managed_service", "--config", str(config_path)]
            service_environment = _environment(bundle, "openjules", data) | {"POLYPHONY_RUNTIME_TOKEN": runtime_token}
            spec = {"schema_version": 1, **scope, "slot_id": label, "generation": 1,
                    "launch_key": "contention-worker-" + label, "profile": profile}
            configs[principal] = config
            members[principal] = {"label": label, "command": command, "environment": service_environment,
                                  "client": Client("http://127.0.0.1:" + str(runtime_port), controller_token), "spec": spec,
                                  "controls": {action: "contention-" + label + "-" + action for action in ("drain", "stop")}}
        authority_command = _command(bundle, "polyphony", data)
        authority_command[0] = str(installed_bundle / "environments/polyphony/bin/python")
        index = authority_command.index("--credentials")
        authority_command[index + 1] = str(root / "authority-credentials.json")
        authority_db = Path(authority_command[authority_command.index("--db") + 1])
        authority_process = processes.start("polyphony", authority_command, source_paths["polyphony"], _environment(bundle, "polyphony", data))
        authority = Client(authority_endpoint, token_a)
        snapshot = lambda: authority.request("GET", "/managed/snapshot", query=scope)
        eventually(snapshot, processes, "contract authority readiness", 15)
        require_empty_snapshot(snapshot(), scope)
        for principal, member in members.items():
            member["process"] = processes.start("openjules-" + member["label"], member["command"], source_paths["openjules"], member["environment"])
        def capabilities_ready():
            barrier.check()
            for member in members.values():
                value = member["client"].request("GET", "/api/workers/capabilities", query={**scope, "profile": profile})
                if value.get("managed_dispatch") is not True:
                    return False
                require_runtime_capabilities(value, scope, profile, True)
            return True
        eventually(capabilities_ready, processes, "both independent managed dispatchers ready", 30)
        phase = "explicit workers and legacy route refusal"
        for principal, member in members.items():
            observed = member["client"].request("POST", "/api/workers/ensure", data=member["spec"])
            member["worker_id"] = observed["worker_id"]
        require(len({member["worker_id"] for member in members.values()}) == 2, "two services produced one worker identity")
        def workers_ready():
            barrier.check()
            for member in members.values():
                value = member["client"].request("GET", "/api/workers/" + member["worker_id"], query=scope)
                if value["state"] != "ready" or value["admission_open"] is not True:
                    return False
            return True
        eventually(workers_ready, processes, "two real idle worker containers", 30)
        for principal, member in members.items():
            history = histories(configs[principal])
            member["container"] = require_idle(history, member["spec"], member["worker_id"])
            member["fingerprint"] = history["runtime"]["profiles"][0]["fingerprint"]
            container_running(member["container"], member["worker_id"], member["spec"], member["fingerprint"])
        require(members[principal_a]["container"] != members[principal_b]["container"], "two runtimes share a container")
        empty_histories = {principal: histories(config) for principal, config in configs.items()}
        refusals = []
        for principal, member in members.items():
            for event, action in (("issue_comment", "created"), ("pull_request_review", "submitted"), ("pull_request_review_comment", "created")):
                for _ in range(2):
                    request = Request(member["client"].endpoint + "/api/webhook", method="POST",
                                      data=json.dumps({"action": action, "repository": {"full_name": "managed-runtime-contract/repository"},
                                                       "issue": {"number": 1, "pull_request": {"url": "https://example.invalid/pull/1"}},
                                                       "pull_request": {"number": 1}, "comment": {"body": "/jules run"},
                                                       "review": {"body": "/jules run"}}).encode(),
                                      headers={"Content-Type": "application/json", "X-GitHub-Event": event})
                    try:
                        member["client"].opener.open(request, timeout=3).close()
                    except HTTPError as error:
                        status = error.code
                        error.close()
                    else:
                        status = 200
                    require(status == 404, "managed service unexpectedly accepted a legacy execution route")
                refusals.append({"runtime": member["label"], "event": event, "deliveries": 2, "status": 404})
        require_empty_snapshot(snapshot(), scope)
        require(canonical(empty_histories) == canonical({key: histories(config) for key, config in configs.items()}),
                "legacy route refusal changed protected history")
        completed.append("two real managed APIs explicitly refused duplicate legacy deliveries without execution")
        phase = "two actual claims held before canonical allocation"
        requirements_path = root / "requirements.txt"
        requirements_path.write_text(REQUIREMENTS)
        requirements_path.chmod(0o600)
        ingress_args = ["enroll", "--url", authority_endpoint, "--deployment-id", deployment,
                        "--repository", repository, "--source", "github:issue:1", "--request-key", "contention-delivery",
                        "--title", "One canonical controlled execution", "--requirements-file", str(requirements_path)]
        receipts, ingress_evidence = concurrent_ingress(
            installed_bundle / "environments/polyphony/bin/python", ingress_args, source_paths["polyphony"],
            clean_environment({"POLYPHONY_INGRESS_TOKEN": operators["ingress"]}), root, "ingress", processes)
        require(receipts[0] == receipts[1], "duplicate canonical deliveries did not return one task")
        task_id = receipts[0]["task"]["id"]
        pair = eventually(barrier.pair, processes, "both genuine runtime claims", CLAIM_WINDOW)
        for principal, claim in pair.items():
            member = members[principal]
            require(claim["task_id"] == task_id and claim["worker_id"] == member["worker_id"], "claim belongs to another task or worker")
            require_claiming(histories(configs[principal]), member["spec"], member["worker_id"], claim)
        before = snapshot()
        require(len(before["task"]) == 1 and not before["attempt"] and not before["candidate"],
                "barrier forwarded a claim before observing both contenders")
        barrier.release()
        winner, loser, response = eventually(barrier.outcome, processes, "actual canonical claim outcomes", 5)
        winning, losing = members[winner], members[loser]
        phase = "original protected execution and losing runtime recovery"
        frozen = None
        def running():
            nonlocal frozen
            barrier.check()
            history = histories(configs[winner])
            if not history["runtime"]["attempts"] or not history["runtime"]["attempts"][0]["exec_id"]:
                return False
            frozen = require_running(history, winning["spec"], winning["worker_id"], pair[winner], response)
            return progress(winning["container"])
        eventually(running, processes, "winner real protected command", 15)
        eventually(lambda: not histories(configs[loser])["dispatcher"]["deliveries"], processes, "explicitly rejected claim journal cleared", 5)
        require_idle(histories(configs[loser]), losing["spec"], losing["worker_id"], losing["container"])
        protected = canonical_state(authority_db, scope, task_id, response, snapshot())
        def winner_witness():
            barrier.check()
            require(processes.running["polyphony"] is authority_process and authority_process.poll() is None
                    and processes.running["openjules-" + winning["label"]] is winning["process"]
                    and winning["process"].poll() is None, "authority or winning service changed during peer recovery")
            observed = winning["client"].request("GET", "/api/workers/" + winning["worker_id"], query=scope)
            require_active_runtime(observed, {"worker_id": winning["worker_id"], "spec": winning["spec"],
                                             "attempt_id": response["attempt"]["id"]})
            classification = classify_active_root(observed)
            require_running(histories(configs[winner]), winning["spec"], winning["worker_id"], pair[winner], response, frozen)
            container_running(winning["container"], winning["worker_id"], winning["spec"], winning["fingerprint"])
            canonical_state(authority_db, scope, task_id, response, snapshot(), protected)
            return classification
        classification_before = winner_witness()
        before_progress = progress(winning["container"])
        require(before_progress is not None, "winner lacks its fresh pre-restart counter")
        old_process = processes.running["openjules-" + losing["label"]]
        barrier.pause_for_restart(loser)
        processes.stop("openjules-" + losing["label"], kill=True)
        require(old_process.poll() == -9, "loser process was not killed and reaped")
        transport_fence = barrier.reopen_after_reap(loser, old_process)
        prior_pulls = barrier.count(loser, "snapshot")
        require(prior_pulls == 0, "new transport generation inherited an old snapshot completion")
        replacement = processes.start("openjules-" + losing["label"], losing["command"], source_paths["openjules"], losing["environment"])
        require(replacement.pid != old_process.pid, "loser restart reused its process identity")
        eventually(capabilities_ready, processes, "same-journal loser readiness", 30)
        fresh_pulls = eventually(lambda: barrier.count(loser, "snapshot"), processes,
                                 "restarted loser performs a new-generation canonical pull", 5)
        after_progress = eventually(lambda: (value if (value := progress(winning["container"])) is not None and value > before_progress else None),
                                    processes, "winner progresses while restarted peer pulls", 5)
        classification_after = winner_witness()
        require_idle(histories(configs[loser]), losing["spec"], losing["worker_id"], losing["container"])
        container_running(losing["container"], losing["worker_id"], losing["spec"], losing["fingerprint"])
        completed.append("one actual canonical allocation survived same-journal loser restart with no second protected execution")
        phase = "quiesce losing capacity before winner terminal publication"
        for action in ("drain", "stop"):
            control(losing["client"], scope, losing["worker_id"], action, losing["controls"][action])
        eventually(lambda: terminal(losing["client"].request("GET", "/api/workers/" + losing["worker_id"], query=scope),
                                    losing["spec"], losing["worker_id"])
                   and require_terminal_controls(histories(configs[loser]), losing["spec"], losing["worker_id"],
                                                 losing["controls"], losing["container"], allow_pending=True),
                   processes, "loser own drain and stop capture", 15)
        losing_history = histories(configs[loser])
        require_terminal_controls(losing_history, losing["spec"], losing["worker_id"], losing["controls"], losing["container"])
        require(not losing_history["runtime"]["attempts"] and not losing_history["lifecycle"]["worker_executions"]
                and not losing_history["dispatcher"]["deliveries"], "loser executed protected work during retirement")
        require_running(histories(configs[winner]), winning["spec"], winning["worker_id"], pair[winner], response, frozen)
        canonical_state(authority_db, scope, task_id, response, snapshot(), protected)
        release(winning["container"])
        phase = "canonical capture and bounded terminal evidence"
        attempt, candidate = wait_for_capture(snapshot, scope, task_id, winning["worker_id"], base, processes, "two-runtime winner")
        require(attempt["id"] == response["attempt"]["id"] and canonical(attempt["runtime_binding"]) == canonical(response["binding"]),
                "winner capture changed its originally allocated attempt")
        captured = {}
        for kind, digest in attempt["captured_artifacts"].items():
            content = authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True)
            require(hashlib.sha256(content.encode()).hexdigest() == digest
                    and not any(token in content for token in sensitive), "contract capture hash or credential boundary failed")
            captured[kind] = content
        require(captured["log"].count("ONE_PROTECTED_EXECUTION_COMPLETED") == 1, "protected command did not complete exactly once")
        verifier = root / "verify"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(verifier)], check=True, capture_output=True, timeout=15)
        subprocess.run(["git", "-C", str(verifier), "apply", "--check", "-"], input=captured["patch"], text=True, check=True, capture_output=True, timeout=10)
        subprocess.run(["git", "-C", str(verifier), "apply", "-"], input=captured["patch"], text=True, check=True, capture_output=True, timeout=10)
        require(subprocess.check_output(["git", "-C", str(verifier), "diff", "--name-only"], text=True, timeout=10).splitlines() == ["answer.py"]
                and (source / "answer.py").read_text() == "answer = 0\n", "protected execution changed unapproved files or its source")
        subprocess.run([str(installed_bundle / "environments/zpm/bin/python"), "-c", "import runpy; assert runpy.run_path('answer.py')['answer'] == 42"],
                       cwd=verifier, env=clean_environment(), check=True, capture_output=True, timeout=10)
        final = snapshot()
        require(len(final["task"]) == len(final["attempt"]) == len(final["candidate"]) == 1
                and not final.get("admission") and final["task"][0]["status"] == "awaiting_review"
                and final["task"][0]["attempts"] == 1 and final["task"][0]["allocated_seconds"] == ATTEMPT_SECONDS,
                "terminal publication duplicated or accepted protected work")
        for action in ("drain", "stop"):
            control(winning["client"], scope, winning["worker_id"], action, winning["controls"][action])
        eventually(lambda: terminal(winning["client"].request("GET", "/api/workers/" + winning["worker_id"], query=scope),
                                    winning["spec"], winning["worker_id"])
                   and require_terminal_controls(histories(configs[winner]), winning["spec"], winning["worker_id"],
                                                 winning["controls"], winning["container"], allow_pending=True),
                   processes, "winner terminal worker", 15)
        barrier.check()
        final_history = histories(configs[winner])
        final_attempts = final_history["runtime"]["attempts"]
        expected_attempt = dict(frozen["attempt"], exit_code=0)
        require(final_attempts == [expected_attempt] and not histories(configs[loser])["runtime"]["attempts"],
                "final runtime histories changed the original execution or deadline")
        require_terminal_controls(final_history, winning["spec"], winning["worker_id"], winning["controls"], winning["container"])
        final_local, final_driver = only_worker(final_history, winning["spec"], winning["worker_id"])
        receipt = json.loads(final_driver["receipt"])
        require(receipt["state"] == "completed" and receipt["termination_confirmed"] is True
                and receipt["artifacts_captured"] is True and receipt["launch_fenced"] is True
                and final_history["dispatcher"]["deliveries"][0]["phase"] == "completed",
                "original runtime capture or canonical delivery is incomplete")
        canonical_final = journal(authority_db, SCHEMAS["authority"], maximum=64)
        require(Counter(row["kind"] for row in canonical_final["records"]) ==
                {"task": 1, "managed_source": 1, "start": 1, "attempt": 1, "work": 1, "runtime_capture": 3, "candidate": 1}
                and len(canonical_final["artifacts"]) == 3,
                "final canonical history contains hidden or duplicate protected work")
        require(sealed == {name: (bundle / name).read_bytes() for name in sealed}, "contract changed sealed seed configuration")
        verify(bundle)
        verify(installed_bundle)
        completed.append("the losing worker was independently retired before the winner's sole canonical capture and candidate")
        report = {"passed": True, "schema_version": 1, "component_revisions": revisions,
                  "topology": "two explicit managed runtime services with distinct journals and one canonical authority; no ZPM replica policy",
                  "image": image, "deployment_id": deployment, "repository": repository,
                  "canonical_profile": {"attempt_seconds": ATTEMPT_SECONDS, "max_attempts": 2,
                                        "budget_seconds": TASK_BUDGET, "runtime_watchdog_seconds": WATCHDOG_SECONDS},
                  "legacy_route_refusals": refusals, "canonical_duplicate_ingress": ingress_evidence,
                  "held_claims": [{"runtime": members[key]["label"], "worker_id": value["worker_id"],
                                   "task_id": value["task_id"], "request_key": value["request_key"],
                                   "status": barrier.results[key]["status"]} for key, value in sorted(pair.items())],
                  "winning_runtime": winning["label"], "attempt": attempt["id"], "candidate": candidate["id"],
                  "protected_executions": 1, "losing_protected_executions": 0,
                  "winning_container": winning["container"], "winning_exec": frozen["attempt"]["exec_id"],
                  "canonical_deadline": response["binding"]["deadline"],
                  "runtime_deadline": frozen["attempt"]["deadline"],
                  "loser_restart": {"old_pid": old_process.pid, "new_pid": replacement.pid,
                                    "same_worker": losing["worker_id"], "same_container": losing["container"],
                                    "fresh_canonical_pull": True, "snapshot_completions_before": prior_pulls,
                                    "snapshot_completions_after": fresh_pulls,
                                    "proxy_generation_fence": transport_fence,
                                    "authority_pid": authority_process.pid, "winning_service_pid": winning["process"].pid,
                                    "winner_counter_before": before_progress,
                                    "winner_counter_after": after_progress,
                                    "winner_observation_before": classification_before,
                                    "winner_observation_after": classification_after},
                  "artifacts": attempt["captured_artifacts"], "final_active_executions": 0,
                  "model_calls": 0, "checks": completed,
                  "not_proven": ["replicated generated installation support", "shared-journal service replicas",
                                 "winning claim response loss", "all enabled standalone legacy ingress",
                                 "public conversation", "live model inference", "independent review", "forge publication"]}
        return report
    except BaseException as error:
        original = error
        failure = ContractFailure(phase, failure_diagnostic(root, phase, error, completed, sensitive))
        original = failure
        raise failure from error
    finally:
        cleanup(processes, barrier, members, scope, original)
