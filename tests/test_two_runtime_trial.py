"""Negative oracles for the real two-runtime contract scene.

HTTP servers here are explicit transport fixtures. They do not qualify Docker
execution or Polyphony arbitration; the installed CI attachment supplies that.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
import check_two_runtime_stack as trial


SCOPE = {"deployment_id": "two-runtime-test", "repository": "github:github.com/test/repository"}
SPEC = {"schema_version": 1, **SCOPE, "slot_id": "a", "generation": 1,
        "launch_key": "original-worker", "profile": "controlled"}
WORKER = "a" * 32
ATTEMPT = "b" * 32
CONTAINER = "c" * 64
FINGERPRINT = "d" * 64


def claim(principal="first"):
    return {"schema_version": 1, **SCOPE, "profile": "controlled", "worker_id": WORKER if principal == "first" else "e" * 32,
            "task_id": "task-one", "request_key": "request-" + principal, "base": "f" * 40}


def response(principal="first"):
    request = claim(principal)
    binding = {key: request[key] for key in ("deployment_id", "repository", "profile", "worker_id", "base")}
    binding.update(runtime_principal=principal, attempt_id=ATTEMPT, launch_key="polyphony:" + ATTEMPT,
                   deadline=2000.0)
    return {"schema_version": 1, **SCOPE, "binding": binding, "attempt": {"id": ATTEMPT},
            "requirements": trial.REQUIREMENTS, "launch_allowed": True}


def idle_history():
    return {
        "lifecycle": {
            "managed_workers": [{"worker_id": WORKER, **SCOPE, "slot_id": "a", "generation": 1,
                                 "spec": trial.canonical(SPEC), "destination_binding": None,
                                 "runtime_id": "docker:" + CONTAINER,
                                 "state": "ready", "reason": "", "draining": 0,
                                 "termination_confirmed": 0, "artifacts_captured": 0}],
            "worker_operations": [{**SCOPE, "request_key": SPEC["launch_key"], "action": "ensure", "worker_id": WORKER,
                                   "payload": trial.canonical(SPEC), "completed": 1}],
            "worker_executions": [],
        },
        "runtime": {
            "profiles": [{**SCOPE, "name": "controlled", "fingerprint": FINGERPRINT}],
            "child_profiles": [{**SCOPE, "name": "controlled", "fingerprint": None}],
            "workers": [{"worker_id": WORKER, "spec": trial.canonical(SPEC), "profile_fingerprint": FINGERPRINT,
                         "container_id": CONTAINER, "start_confirmed": 1, "retired": 0, "receipt": None}],
            "attempts": [],
        },
        "dispatcher": {"deliveries": []},
    }


def claiming_history():
    history = idle_history()
    history["dispatcher"]["deliveries"] = [{
        "worker_id": WORKER, "spec": trial.canonical(SPEC), "task_id": "task-one",
        "request_key": "request-first", "phase": "claiming", "binding": None, "artifacts": "{}", "reason": "",
    }]
    return history


def running_history():
    history = claiming_history()
    history["lifecycle"]["worker_executions"] = [{
        "worker_id": WORKER, "execution_id": ATTEMPT, "parent_id": None,
        "termination_confirmed": 0, "artifacts_captured": 0,
    }]
    history["runtime"]["attempts"] = [{
        "worker_id": WORKER, "attempt_id": ATTEMPT, "input_sha256": hashlib.sha256(trial.REQUIREMENTS.encode()).hexdigest(),
        "seconds": 59.5, "deadline": 1000.0, "exec_id": "one-original-exec",
        "exec_dispatched": 1, "cancelled": 0, "exit_code": None,
    }]
    history["dispatcher"]["deliveries"][0].update(phase="dispatching", binding=trial.canonical(response()))
    return history


class ClaimBarrierTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.wait_snapshot = threading.Event()
        self.disconnect_snapshot = False
        self.wait_snapshot.set()
        owner = self

        class AuthorityHTTPFixture(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.wait_snapshot.wait(2)
                owner.requests.append((self.command, self.path, self.headers["Authorization"], b""))
                if owner.disconnect_snapshot:
                    self.close_connection = True
                    self.connection.shutdown(trial.socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send(200, b'{"fixture_snapshot": true}')

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                request = json.loads(raw)
                principal = "first" if self.headers["Authorization"] == "Bearer token-first" else "second"
                owner.requests.append((self.command, self.path, self.headers["Authorization"], raw))
                status = 200 if principal == "first" else 409
                value = response(principal) if status == 200 else trial.REJECTION
                self.send(status, json.dumps(value, indent=1).encode() + b"\n")

            def send(self, status, content):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), AuthorityHTTPFixture)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, kwargs={"poll_interval": 0.01})
        self.upstream_thread.start()
        self.barrier = trial.ClaimBarrier("http://127.0.0.1:" + str(self.upstream.server_port),
                                         {"Bearer token-first": "first", "Bearer token-second": "second"},
                                         SCOPE, "controlled", "f" * 40)

    def tearDown(self):
        self.wait_snapshot.set()
        self.barrier.close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(2)
        self.assertFalse(self.upstream_thread.is_alive())

    def send(self, principal, *, operation="claim", data=None):
        method = "GET" if operation == "snapshot" else "POST"
        body = json.dumps(data if data is not None else claim(principal), indent=2).encode() if method == "POST" else None
        request = Request(self.barrier.endpoint + "/managed/" + operation, method=method, data=body,
                          headers={"Authorization": "Bearer token-" + principal, "Content-Type": "application/json"})
        try:
            result = urlopen(request, timeout=3)
        except HTTPError as error:
            result = error
        try:
            return result.code, result.read()
        finally:
            result.close()

    def test_two_real_http_requests_are_held_and_forwarded_byte_exactly_once(self):
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.send, "first")
            second = pool.submit(self.send, "second")
            with self.barrier.condition:
                self.assertTrue(self.barrier.condition.wait_for(lambda: len(self.barrier.claims) == 2, 2))
            self.assertIsNotNone(self.barrier.pair())
            self.assertEqual(self.requests, [])
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.barrier.release()
            for principal, future, status in (("first", first, 200), ("second", second, 409)):
                code, raw = future.result(3)
                expected = response(principal) if status == 200 else trial.REJECTION
                self.assertEqual((code, raw), (status, json.dumps(expected, indent=1).encode() + b"\n"))
        self.assertEqual(len(self.requests), 2)
        for method, route, token, raw in self.requests:
            principal = "first" if token == "Bearer token-first" else "second"
            self.assertEqual((method, route), ("POST", "/managed/claim"))
            self.assertEqual(raw, json.dumps(claim(principal), indent=2).encode())
        self.assertEqual(self.barrier.outcome()[:2], ("first", "second"))
        with self.barrier.condition:
            self.assertTrue(self.barrier.condition.wait_for(
                lambda: self.barrier.idle("first") and self.barrier.count("first", "claim") == 1, 2))

    def test_barrier_rejects_one_claim_duplicate_principal_and_nonindependent_pair(self):
        self.barrier.arrive("first", claim())
        self.assertIsNone(self.barrier.pair())
        with self.assertRaises(AssertionError):
            self.barrier.release()
        with self.assertRaises(AssertionError):
            self.barrier.arrive("first", claim())
        second = claim("second")
        second["worker_id"] = WORKER
        self.barrier.arrive("second", second)
        with self.assertRaises(AssertionError):
            self.barrier.pair()
        self.assertEqual(self.requests, [])

    def test_barrier_refuses_changed_scope_base_shape_or_schema(self):
        for changes in ({"repository": "github:github.com/foreign/repo"}, {"base": "0" * 40},
                        {"schema_version": True}, {"extra": "value"}, {"worker_id": ""}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                self.barrier.arrive("first", claim() | changes)
        self.assertEqual(self.barrier.claims, {})

    def test_actual_result_oracle_rejects_second_allocation_generic_conflict_and_foreign_binding(self):
        self.barrier.arrive("first", claim())
        self.barrier.arrive("second", claim("second"))
        for status, rejection in ((200, response("second")), (409, {"error": "conflict"}),
                                  (409, trial.REJECTION | {"allocated": 0})):
            self.barrier.results = {}
            self.barrier.record_result("first", 200, response())
            self.barrier.record_result("second", status, rejection)
            with self.subTest(status=status, rejection=rejection), self.assertRaises(AssertionError):
                self.barrier.outcome()
        wrong = response()
        wrong["binding"]["runtime_principal"] = "second"
        self.barrier.results = {}
        self.barrier.record_result("first", 200, wrong)
        self.barrier.record_result("second", 409, trial.REJECTION)
        with self.assertRaises(AssertionError):
            self.barrier.outcome()

    def test_restart_fence_owns_not_yet_started_handlers_and_excludes_old_completions(self):
        accepted, allow_start, pause_entered, reap_entered = (threading.Event() for _ in range(4))
        original_start = threading.Thread.start
        old_server = self.barrier.server
        old_endpoint = self.barrier.endpoint
        self.wait_snapshot.clear()

        def held_start(thread):
            if thread.name.startswith("contract-proxy-request-") and not accepted.is_set():
                accepted.set()
                self.assertTrue(allow_start.wait(2))
            return original_start(thread)

        def pause():
            pause_entered.set()
            self.barrier.pause_for_restart("second")

        def reopen():
            reap_entered.set()
            return self.barrier.reopen_after_reap("second", SimpleNamespace(poll=lambda: -9))

        try:
            with patch.object(threading.Thread, "start", held_start), ThreadPoolExecutor(3) as pool:
                request = pool.submit(self.send, "second", operation="snapshot")
                self.assertTrue(accepted.wait(2))
                # The handler has not started or parsed/authenticated headers.
                # Nevertheless the accepted socket/thread is already owned.
                with self.barrier.condition:
                    self.assertEqual(self.barrier.open_requests, 1)
                    self.assertEqual(self.barrier.inflight["second"], 0)
                    self.assertEqual(len(old_server.sockets), 1)
                    self.assertEqual(len(old_server.handlers), 1)
                paused = pool.submit(pause)
                self.assertTrue(pause_entered.wait(2))
                self.assertFalse(paused.done())
                self.assertEqual(self.barrier.epoch, 1)
                allow_start.set()
                paused.result(3)
                self.assertEqual(old_server.socket.fileno(), -1)
                with self.barrier.condition:
                    self.assertTrue(self.barrier.condition.wait_for(
                        lambda: self.barrier.inflight["second"] == 1, 2))
                with self.assertRaises(AssertionError):
                    self.barrier.reopen_after_reap("second", SimpleNamespace(poll=lambda: None))
                reopened = pool.submit(reopen)
                self.assertTrue(reap_entered.wait(2))
                self.assertFalse(reopened.done())
                self.assertEqual(self.barrier.epoch, 1)
                self.wait_snapshot.set()
                self.assertEqual(request.result(3)[0], 200)
                fence = reopened.result(3)
            self.assertEqual(fence["old_epoch"], 1)
            self.assertEqual(fence["new_epoch"], 2)
            self.assertEqual(fence["accepted_handlers_reaped"], 1)
            self.assertTrue(fence["old_listener_closed"])
            self.assertEqual(self.barrier.endpoint, old_endpoint)
            self.assertEqual(self.barrier.open_requests, 0)
            self.assertTrue(all(not thread.is_alive() for thread in old_server.handlers))
            self.assertEqual(self.barrier.count("second", "snapshot", epoch=1), 1)
            self.assertEqual(self.barrier.count("second", "snapshot"), 0)
            self.assertEqual(self.send("second", operation="snapshot")[0], 200)
            with self.barrier.condition:
                self.assertTrue(self.barrier.condition.wait_for(
                    lambda: self.barrier.idle("second") and self.barrier.count("second", "snapshot") == 1, 2))
            # An exhausted whole-fence deadline never permits a listener reopen,
            # even if its old handlers happen to have finished by this check.
            self.barrier.pause_for_restart("second")
            self.barrier.stop_state["deadline"] = trial.time.monotonic() - 1
            with self.assertRaises(AssertionError):
                self.barrier.reopen_after_reap("second", SimpleNamespace(poll=lambda: -9))
            self.assertEqual(self.barrier.epoch, 2)
            self.assertEqual(self.barrier.server.socket.fileno(), -1)
        finally:
            allow_start.set()
            self.wait_snapshot.set()

    def test_generation_fence_retains_real_forwarder_errors_without_counting_success(self):
        status, _ = self.send("unknown", operation="snapshot")
        self.assertEqual(status, 502)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.barrier.count("second", "snapshot"), 0)
        with self.assertRaises(AssertionError):
            self.barrier.check()
        self.barrier.pause_for_restart("second")
        self.barrier.reopen_after_reap("second", SimpleNamespace(poll=lambda: -9))
        self.assertEqual(self.send("second", operation="snapshot")[0], 200)
        with self.barrier.condition:
            self.assertTrue(self.barrier.condition.wait_for(
                lambda: self.barrier.idle("second") and self.barrier.count("second", "snapshot") == 1, 2))
        # A later valid response in a new generation cannot erase the original
        # authentication/forwarding failure or turn it into an expected close.
        self.assertEqual(self.barrier.closed_responses, {})
        with self.assertRaises(AssertionError):
            self.barrier.check()

    def test_upstream_disconnect_during_fence_remains_a_real_error(self):
        self.disconnect_snapshot = True
        self.wait_snapshot.clear()
        try:
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(self.send, "second", operation="snapshot")
                with self.barrier.condition:
                    self.assertTrue(self.barrier.condition.wait_for(
                        lambda: self.barrier.inflight["second"] == 1, 2))
                self.barrier.pause_for_restart("second")
                self.wait_snapshot.set()
                self.assertEqual(pending.result(3)[0], 502)
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.barrier.count("second", "snapshot", epoch=1), 0)
            self.assertEqual(self.barrier.closed_responses, {})
            self.assertTrue(self.barrier.errors)
            self.barrier.reopen_after_reap("second", SimpleNamespace(poll=lambda: -9))
            self.assertEqual(self.barrier.count("second", "snapshot"), 0)
            with self.assertRaises(AssertionError):
                self.barrier.check()
        finally:
            self.wait_snapshot.set()

    def test_close_does_not_forward_a_claim_without_its_second_contender(self):
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.send, "first")
            with self.barrier.condition:
                self.assertTrue(self.barrier.condition.wait_for(lambda: len(self.barrier.claims) == 1, 2))
            self.barrier.close()
            self.assertEqual(pending.result(3)[0], 502)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.barrier.thread.is_alive())
        self.assertEqual(self.barrier.open_requests, 0)


class JournalAndIdentityTests(unittest.TestCase):
    def test_readonly_wal_reader_exposes_foreign_rows_and_rejects_missing_or_changed_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).absolute() / "journal.sqlite3"
            db = sqlite3.connect(path)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE samples (identity TEXT, value INTEGER)")
            db.execute("INSERT INTO samples VALUES ('owned', 1)")
            db.commit()
            schema = {"samples": ("identity", "value")}
            self.assertEqual(trial.journal(path, schema)["samples"], [{"identity": "owned", "value": 1}])
            db.execute("INSERT INTO samples VALUES ('foreign', 2)")
            db.commit()
            self.assertEqual(len(trial.journal(path, schema)["samples"]), 2)
            with self.assertRaises(AssertionError):
                trial.journal(path, schema, maximum=1)
            with self.assertRaises(AssertionError):
                trial.journal(path, {"samples": ("identity",)})
            link = path.with_name("link")
            link.symlink_to(path)
            with self.assertRaises(AssertionError):
                trial.journal(link, schema)
            with self.assertRaises(FileNotFoundError):
                trial.journal(path.with_name("missing"), schema)
            db.close()

    def test_unbound_claim_and_idle_loser_witnesses_reject_hidden_execution_or_foreign_scope(self):
        self.assertEqual(trial.require_idle(idle_history(), SPEC, WORKER), CONTAINER)
        trial.require_claiming(claiming_history(), SPEC, WORKER, claim())
        variants = []
        changed = idle_history()
        changed["runtime"]["attempts"] = [{"attempt_id": ATTEMPT}]
        variants.append(changed)
        changed = idle_history()
        changed["lifecycle"]["worker_operations"][0]["repository"] = "github:github.com/foreign/repo"
        variants.append(changed)
        changed = idle_history()
        changed["runtime"]["profiles"][0]["deployment_id"] = "foreign"
        variants.append(changed)
        changed = idle_history()
        changed["runtime"]["workers"][0]["retired"] = 1
        variants.append(changed)
        changed = idle_history()
        changed["lifecycle"]["managed_workers"][0]["destination_binding"] = "{}"
        variants.append(changed)
        changed = idle_history()
        changed["dispatcher"]["deliveries"] = claiming_history()["dispatcher"]["deliveries"]
        variants.append(changed)
        for history in variants:
            with self.subTest(history=history), self.assertRaises(AssertionError):
                trial.require_idle(history, SPEC, WORKER)
        bound = claiming_history()
        bound["dispatcher"]["deliveries"][0]["binding"] = trial.canonical(response())
        with self.assertRaises(AssertionError):
            trial.require_claiming(bound, SPEC, WORKER, claim())

    def test_frozen_winner_refuses_changed_exec_input_deadline_capture_or_second_attempt(self):
        baseline = running_history()
        frozen = trial.require_running(baseline, SPEC, WORKER, claim(), response())
        trial.require_running(deepcopy(baseline), SPEC, WORKER, claim(), response(), frozen)
        for field, value in (("exec_id", "replacement-exec"), ("seconds", 60.0),
                             ("deadline", 1001.0), ("cancelled", 1), ("exit_code", 0),
                             ("input_sha256", "0" * 64), ("attempt_id", "replacement-attempt")):
            changed = deepcopy(baseline)
            changed["runtime"]["attempts"][0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                trial.require_running(changed, SPEC, WORKER, claim(), response(), frozen)
        changed = deepcopy(baseline)
        changed["runtime"]["attempts"].append(deepcopy(changed["runtime"]["attempts"][0]))
        with self.assertRaises(AssertionError):
            trial.require_running(changed, SPEC, WORKER, claim(), response(), frozen)

    def test_canonical_all_scope_records_and_unspent_budget_cannot_hide_additional_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).absolute() / "authority.sqlite3"
            db = sqlite3.connect(path)
            db.executescript("PRAGMA journal_mode=WAL; CREATE TABLE records(kind TEXT,id TEXT,body TEXT);"
                             "CREATE TABLE artifacts(id TEXT,content BLOB);"
                             "CREATE TABLE events(seq INTEGER,time REAL,kind TEXT,record_id TEXT);")
            task = {"id": "task-one", "managed_scope": SCOPE, "status": "in_progress", "attempts": 1,
                    "allocated_seconds": 60, "max_attempts": 2, "budget_seconds": 120}
            attempt = {"id": ATTEMPT, "task": "task-one", "number": 1, "state": "unknown",
                       "termination_confirmed": False, "execution_mode": "external",
                       "runtime_binding": response()["binding"]}
            records = {"task": task, "attempt": attempt, "start": {"attempt": ATTEMPT}, "managed_source": {"task": "task-one"}}
            db.executemany("INSERT INTO records VALUES (?,?,?)",
                           [(kind, kind, trial.canonical(body)) for kind, body in records.items()])
            db.commit()
            snapshot = {kind: [] for kind in trial.RECORD_KINDS} | {
                "schema_version": 1, **SCOPE, "cursor": 3, "task": [task],
                "attempt": [attempt | {"permission_current": True}], "candidate": []}
            frozen = trial.canonical_state(path, SCOPE, "task-one", response(), snapshot)
            incomplete = dict(snapshot)
            incomplete.pop("work")
            with self.assertRaises(AssertionError):
                trial.canonical_state(path, SCOPE, "task-one", response(), incomplete, frozen)
            for field, value in (("attempts", True), ("max_attempts", 1), ("budget_seconds", 60),
                                 ("allocated_seconds", 120), ("status", "awaiting_review")):
                changed = task | {field: value}
                db.execute("UPDATE records SET body=? WHERE kind='task'", (trial.canonical(changed),))
                db.commit()
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    trial.canonical_state(path, SCOPE, "task-one", response(), snapshot, frozen)
                db.execute("UPDATE records SET body=? WHERE kind='task'", (trial.canonical(task),))
                db.commit()
            db.execute("INSERT INTO records VALUES ('attempt','foreign','{}')")
            db.commit()
            with self.assertRaises(AssertionError):
                trial.canonical_state(path, SCOPE, "task-one", response(), snapshot, frozen)
            db.execute("DELETE FROM records WHERE id='foreign'")
            db.commit()
            stale = deepcopy(snapshot)
            stale["attempt"][0]["permission_current"] = False
            with self.assertRaises(AssertionError):
                trial.canonical_state(path, SCOPE, "task-one", response(), stale, frozen)
            db.close()


    def test_terminal_controls_require_both_exact_owned_keys_and_complete_capture(self):
        history = idle_history()
        keys = {"drain": "original-drain", "stop": "original-stop"}
        history["lifecycle"]["managed_workers"][0].update(
            draining=1, termination_confirmed=1, artifacts_captured=1, state="stopped")
        history["runtime"]["workers"][0].update(
            retired=1, receipt=trial.canonical({
                "termination_confirmed": True, "artifacts_captured": True, "launch_fenced": True}))
        for action in ("drain", "stop"):
            history["lifecycle"]["worker_operations"].append({
                **SCOPE, "request_key": keys[action], "action": action, "worker_id": WORKER,
                "payload": trial.canonical({"worker_id": WORKER, "action": action}), "completed": 1})
        trial.require_terminal_controls(history, SPEC, WORKER, keys, CONTAINER)
        for field, value in (("request_key", "another-key"), ("repository", "github:github.com/foreign/repo"),
                             ("worker_id", "another-worker"), ("completed", 0),
                             ("payload", trial.canonical({"worker_id": WORKER, "action": "stop"}))):
            changed = deepcopy(history)
            changed["lifecycle"]["worker_operations"][1][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                trial.require_terminal_controls(changed, SPEC, WORKER, keys, CONTAINER)
        pending = deepcopy(history)
        pending["lifecycle"]["worker_operations"][1]["completed"] = 0
        self.assertFalse(trial.require_terminal_controls(pending, SPEC, WORKER, keys, CONTAINER, allow_pending=True))
        with self.assertRaises(AssertionError):
            trial.require_terminal_controls(pending, SPEC, WORKER, keys, CONTAINER)
        incomplete = deepcopy(history)
        incomplete["lifecycle"]["worker_operations"].pop()
        with self.assertRaises(AssertionError):
            trial.require_terminal_controls(incomplete, SPEC, WORKER, keys, CONTAINER)
        incomplete = deepcopy(history)
        incomplete["runtime"]["workers"][0]["receipt"] = trial.canonical({
            "termination_confirmed": True, "artifacts_captured": False, "launch_fenced": True})
        with self.assertRaises(AssertionError):
            trial.require_terminal_controls(incomplete, SPEC, WORKER, keys, CONTAINER)

    def test_cleanup_attempts_every_owned_control_and_process_without_masking_original(self):
        members = {
            label: {"label": label, "client": object(), "worker_id": label,
                    "controls": {"drain": label + "-drain", "stop": label + "-stop"}}
            for label in ("a", "b")
        }
        processes = Mock()
        processes.running = {"authority": object(), "a": object(), "b": object()}
        processes.handles = [Mock()]
        barrier = Mock()
        original = trial.ContractFailure("original assertion", {"logs": {}})
        with patch.object(trial, "control", side_effect=[OSError(), None, None, None]) as controls, \
                patch.object(trial, "stop_owned", side_effect=[OSError(), None, None]) as stops:
            trial.cleanup(processes, barrier, members, SCOPE, original)
        self.assertEqual(controls.call_count, 4)
        self.assertEqual(stops.call_count, 3)
        barrier.close.assert_called_once()
        self.assertEqual(original.contract_diagnostic["cleanup_failures"], ["a-drain", "owned-service-reap-b"])
        self.assertEqual(str(original), "two-runtime contract failed during original assertion")
        with patch.object(trial, "control"), patch.object(trial, "stop_owned"):
            barrier.close.side_effect = OSError()
            with self.assertRaises(trial.ContractFailure):
                trial.cleanup(processes, barrier, members, SCOPE)

    def test_failure_diagnostics_redact_before_truncating_and_bound_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "private-token-" + "x" * 64
            (root / "openjules-a.log").write_text("prefix" * 4000 + token + "\n" + "漢" * 3000)
            report = trial.failure_diagnostic(root, "test phase", RuntimeError(token), [], {token})
            self.assertNotIn(token, json.dumps(report))
            self.assertNotIn("RuntimeError(", json.dumps(report))
            self.assertLessEqual(len(report["logs"]["openjules-a.log"].encode()), 4000)


if __name__ == "__main__":
    unittest.main()
