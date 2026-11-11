"""Negative checks for isolation evidence; these do not run Docker."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_isolation_stack import (
        Installation, LOCK_CONTENTION, PENDING_DRAIN, aggregate_diagnostics, canonical,
        checkpoint_progress, controller_operation, live_ids, read_journal,
        require_active_runtime, require_controller_drain, require_live_container,
        require_peer_runtime, require_peer_unchanged, require_pending_drain_journals,
        require_pending_drain_runtime, require_removed_plan, require_uncontrolled_journals,
        wait_for_capture, worker_binding, worker_journals,
    )
finally:
    sys.path.pop(0)


class IsolationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.scope = {"deployment_id": "deployment-b", "repository": "github:github.com/trial/b"}
        self.plan = {
            "deployment_id": self.scope["deployment_id"],
            "repositories": [{"repository": self.scope["repository"], "desired_workers": 1,
                              "effective_target": 1, "enabled": True, "paused": False, "reserved": 1,
                              "workers": [{**self.scope, "worker_id": "worker-b", "admission_open": True, "state": "busy",
                                           "pending_operation": None}]}],
        }
        self.snapshot = {**self.scope, "task": [{"id": "task-b"}],
                         "attempt": [{"id": "attempt-b", "task": "task-b", "termination_confirmed": False,
                                      "runtime_binding": {"worker_id": "worker-b"}}]}

    def test_equal_capacity_cannot_hide_peer_identity_or_authority_changes(self):
        require_peer_unchanged(self.plan, self.snapshot, self.scope, "worker-b", "task-b", "attempt-b")
        cases = [
            ("worker replacement", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(worker_id="replacement")),
            ("wrong worker scope", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(repository="github:github.com/trial/a")),
            ("wrong authority scope", lambda plan, snapshot: snapshot.update(deployment_id="deployment-a")),
            ("changed desired target", lambda plan, snapshot: plan["repositories"][0].update(desired_workers=2)),
            ("closed admission", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(admission_open=False)),
            ("pending drain", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(pending_operation="drain")),
            ("pending stop", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(pending_operation="stop")),
            ("missing pending evidence", lambda plan, snapshot: plan["repositories"][0]["workers"][0].pop("pending_operation")),
            ("replaced attempt", lambda plan, snapshot: snapshot["attempt"][0].update(id="replacement")),
            ("foreign task", lambda plan, snapshot: snapshot["task"].append({"id": "task-a"})),
            ("already terminated", lambda plan, snapshot: snapshot["attempt"][0].update(termination_confirmed=True)),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                plan, snapshot = deepcopy(self.plan), deepcopy(self.snapshot)
                mutate(plan, snapshot)
                with self.assertRaises(AssertionError):
                    require_peer_unchanged(plan, snapshot, self.scope, "worker-b", "task-b", "attempt-b")

    def test_duplicate_or_unbound_worker_ids_cannot_establish_distinct_capacity(self):
        duplicate = deepcopy(self.plan)
        duplicate["repositories"][0]["workers"] *= 2
        with self.assertRaises(AssertionError):
            live_ids(duplicate)
        for identity in (None, ""):
            with self.subTest(identity=identity):
                unbound = deepcopy(self.plan)
                unbound["repositories"][0]["workers"][0]["worker_id"] = identity
                with self.assertRaises(AssertionError):
                    live_ids(unbound)

    def test_each_held_phase_requires_progress_after_its_fresh_baseline(self):
        processes = SimpleNamespace(check=lambda: None)
        checked = []

        def bounded_observation(predicate, *_):
            for _ in range(3):
                value = predicate()
                if value:
                    return value
            raise AssertionError("counter did not advance in the held phase")

        with patch("check_isolation_stack.eventually", side_effect=bounded_observation):
            # It may be greater than any prior phase's counter, but a fixed
            # counter after entering this phase is not fresh progress.
            with self.assertRaises(AssertionError):
                checkpoint_progress(lambda: 100, lambda: checked.append("fresh"), processes)
            self.assertEqual(checked, ["fresh"])
            checked.clear()
            values = iter([100, 100, 101])
            self.assertEqual(checkpoint_progress(lambda: next(values), lambda: checked.append("fresh"), processes),
                             {"before": 100, "after": 101, "witness_before": None, "witness_after": None})
            self.assertEqual(checked, ["fresh", "fresh"])

    def test_fresh_runtime_rejects_generic_uncertainty_and_replaced_attempts(self):
        original = {**self.scope, "worker_id": "worker-b", "state": "busy", "admission_open": True,
                    "active_executions": ["attempt-b"], "termination_confirmed": False, "artifacts_captured": False}
        require_peer_runtime(original, self.scope, "worker-b", "attempt-b")
        locked = original | {"state": "unknown", "admission_open": False, "reason": LOCK_CONTENTION}
        self.assertEqual(require_peer_runtime(locked, self.scope, "worker-b", "attempt-b"),
                         "unknown: execution lock held; admission closed")
        for changed in ({"reason": "some other outage"}, {"reason": PENDING_DRAIN + "; " + LOCK_CONTENTION},
                        {"admission_open": True}, {"state": "draining"}):
            with self.subTest(unknown_change=changed), self.assertRaises(AssertionError):
                require_peer_runtime(locked | changed, self.scope, "worker-b", "attempt-b")
        for changed in ({"worker_id": "replacement"}, {"repository": "github:github.com/trial/a"},
                        {"admission_open": False}, {"active_executions": []},
                        {"active_executions": ["other-attempt"]}, {"termination_confirmed": True}):
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                require_peer_runtime(original | changed, self.scope, "worker-b", "attempt-b")

    def test_aggregate_diagnostics_redact_both_installations_and_cut_tokens(self):
        sensitive = {"credential-a-" + "A" * 48, "credential-b-" + "B" * 48, "synthetic-secret-" + "S" * 48}
        credentials = sorted(sensitive)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            installations = []
            for label in ("a", "b"):
                private = root / label
                private.mkdir()
                installations.append(SimpleNamespace(label=label, root=private))
                # The first token crosses the log-tail cutoff. Both other
                # credentials occur later, including the sibling's credential.
                tail = ("tail " + credentials[1] + " " + credentials[2]).encode()
                data = b"prefix " + credentials[0].encode() + b"x" * (20000 - len(tail) - 20) + tail
                (private / "openjules.log").write_bytes(data)
            report = aggregate_diagnostics(root, installations, "failure " + credentials[0], [], {"zpm": "revision"}, sensitive)
            encoded = json.dumps(report)
            for credential in credentials:
                self.assertNotIn(credential, encoded)
            self.assertIn("[redacted]", encoded)
            self.assertEqual(set(report["installations"]), {"a", "b"})
            for installation in report["installations"].values():
                self.assertLessEqual(len(installation["logs"]["openjules"]), 20000)
                self.assertFalse(installation["logs"]["openjules"].startswith("A"))
            self.assertEqual(json.loads((root / "diagnostics.json").read_text()), report)
            self.assertEqual((root / "diagnostics.json").stat().st_mode & 0o777, 0o600)


class DurableWitnessTests(unittest.TestCase):
    """Use actual live SQLite files; journal reads must observe committed WAL."""

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "installation"
        self.bundle.mkdir(mode=0o700)
        self.spec = {"schema_version": 1, "deployment_id": "deployment-b", "repository": "github:github.com/trial/b",
                     "slot_id": "slot-b", "generation": 1, "launch_key": "launch-b", "profile": "profile-b"}
        # Generate the actual installer layout and controller schema. Companion
        # journals below are synthetic; no bootstrap or runtime readiness is claimed.
        from zpm.deployment import _prepare_files
        from zpm.models import Binding
        data = {
            "schema_version": 1, "deployment_id": self.spec["deployment_id"], "max_workers": 2,
            "binding": Binding.parse({"schema_version": 1, "repository": self.spec["repository"],
                                      "profile": self.spec["profile"], "desired_workers": 0}).to_dict(),
            "sources": {name: {"checkout": str(self.root / name), "revision": "1" * 40}
                        for name in ("zpm", "openjules", "polyphony")},
            "source": {"checkout": str(self.root / "source"), "revision": "a" * 40},
            "image": "sha256:" + "b" * 64, "command": ["python3", "approved.py"],
            "ports": {"polyphony": 8766, "openjules": 8000}, "environment_refs": {},
            "runtime": {"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000,
                        "pids_limit": 128, "max_seconds": 300, "max_archive_bytes": 16777216},
            "policy": {"checks": ["tests"], "reviewers": ["operator"], "max_attempts": 1,
                       "budget_seconds": 400, "attempt_seconds": 270, "capabilities": ["python"]},
        }
        (self.bundle / "preparation.json").write_text("{}")
        (self.bundle / "preparation.json").chmod(0o600)
        _prepare_files(data, self.bundle, self.bundle)
        generated = json.loads((self.bundle / "config/openjules.json").read_text())
        self.lifecycle_path = Path(generated["lifecycle_database"])
        runtime_directory = Path(generated["state_directory"]) / "runtime"
        runtime_directory.mkdir(parents=True)
        self.runtime_path = runtime_directory / "journal.sqlite3"
        self.assertEqual(self.runtime_path, self.bundle / "state/openjules/runtime/runtime/journal.sqlite3")
        self.assertEqual(self.lifecycle_path, self.bundle / "state/openjules/lifecycle.sqlite3")
        self.controller_path = self.bundle / "state/zpm.sqlite3"
        self.lifecycle = sqlite3.connect(self.lifecycle_path)
        self.runtime = sqlite3.connect(self.runtime_path)
        self.controller = sqlite3.connect(self.controller_path)
        for connection in (self.lifecycle, self.runtime, self.controller):
            self.addCleanup(connection.close)
            connection.execute("PRAGMA journal_mode=WAL")
        self.lifecycle.executescript("""
            CREATE TABLE managed_workers (
                worker_id TEXT PRIMARY KEY, deployment_id TEXT NOT NULL, repository TEXT NOT NULL,
                slot_id TEXT NOT NULL, generation INTEGER NOT NULL, spec TEXT NOT NULL,
                runtime_id TEXT, state TEXT NOT NULL, reason TEXT NOT NULL,
                draining INTEGER NOT NULL DEFAULT 0, termination_confirmed INTEGER NOT NULL DEFAULT 0,
                artifacts_captured INTEGER NOT NULL DEFAULT 0,
                UNIQUE(deployment_id,repository,slot_id,generation));
            CREATE TABLE worker_operations (
                deployment_id TEXT NOT NULL, repository TEXT NOT NULL, request_key TEXT NOT NULL,
                action TEXT NOT NULL, worker_id TEXT NOT NULL, payload TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(deployment_id,repository,request_key));
            CREATE TABLE worker_executions (
                worker_id TEXT NOT NULL, execution_id TEXT NOT NULL, parent_id TEXT,
                termination_confirmed INTEGER NOT NULL DEFAULT 0, artifacts_captured INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(worker_id,execution_id));
        """)
        self.runtime.executescript("""
            CREATE TABLE workers (
                worker_id TEXT PRIMARY KEY, spec TEXT NOT NULL, profile_fingerprint TEXT NOT NULL,
                container_id TEXT, create_dispatched INTEGER NOT NULL DEFAULT 0,
                start_dispatched INTEGER NOT NULL DEFAULT 0, start_confirmed INTEGER NOT NULL DEFAULT 0,
                retired INTEGER NOT NULL DEFAULT 0, receipt TEXT);
            CREATE TABLE attempts (
                worker_id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL, input_sha256 TEXT NOT NULL,
                seconds REAL NOT NULL, deadline REAL NOT NULL, exec_id TEXT,
                exec_dispatched INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0, exit_code INTEGER);
        """)
        self.worker = {**self.spec, "worker_id": "worker-b", "state": "unknown", "reason": LOCK_CONTENTION,
                       "admission_open": False, "active_executions": ["attempt-b"],
                       "termination_confirmed": False, "artifacts_captured": False}
        self.container = {"Id": "c" * 64, "State": {"Running": True}, "Config": {"Labels": {
            "openjules.managed": "1", "openjules.worker": "worker-b", "openjules.profile": "f" * 64,
            "openjules.spec": hashlib.sha256(canonical(self.spec).encode()).hexdigest(),
        }}}
        self.binding = worker_binding(self.worker, self.container, "attempt-b")
        self.lifecycle.execute(
            "INSERT INTO managed_workers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("worker-b", self.spec["deployment_id"], self.spec["repository"], "slot-b", 1, canonical(self.spec),
             "docker:" + self.container["Id"], "unknown", LOCK_CONTENTION, 0, 0, 0))
        self.lifecycle.execute("INSERT INTO worker_executions VALUES (?,?,?,?,?)", ("worker-b", "attempt-b", None, 0, 0))
        self.runtime.execute("INSERT INTO workers VALUES (?,?,?,?,?,?,?,?,?)",
                             ("worker-b", canonical(self.spec), "f" * 64, self.container["Id"], 1, 1, 1, 0, None))
        self.runtime.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)",
                             ("worker-b", "attempt-b", "d" * 64, 270, 1000000, "exec-b", 1, 0, None))
        self.controller.execute("INSERT INTO workers VALUES (?,?,?,?,?,?,?,?)",
                                ("launch-b", self.spec["repository"], "slot-b", 1, "profile-b", canonical(self.worker), None, None))
        for connection in (self.lifecycle, self.runtime, self.controller):
            connection.commit()
        self.binding["execution"] = require_uncontrolled_journals(self.records(), self.binding)

    def records(self):
        return worker_journals(self.bundle, "worker-b")

    def control(self, **changes):
        return {"deployment_id": self.spec["deployment_id"], "repository": self.spec["repository"],
                "request_key": "original-drain-key", "action": "drain", "worker_id": "worker-b",
                "payload": canonical({"worker_id": "worker-b", "action": "drain"}), "completed": 0} | changes

    def insert_control(self, control):
        fields = ("deployment_id", "repository", "request_key", "action", "worker_id", "payload", "completed")
        self.lifecycle.execute("INSERT INTO worker_operations VALUES (?,?,?,?,?,?,?)", tuple(control[key] for key in fields))
        self.lifecycle.commit()

    def test_readers_observe_live_wal_without_writes_or_implicit_creation(self):
        require_uncontrolled_journals(self.records(), self.binding)
        with read_journal(self.lifecycle_path) as database:
            with self.assertRaises(sqlite3.OperationalError):
                database.execute("UPDATE managed_workers SET draining=1")
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(AssertionError), read_journal(missing):
            pass
        self.assertFalse(missing.exists())
        linked = self.root / "linked.sqlite3"
        linked.symlink_to(self.lifecycle_path)
        with self.assertRaises(AssertionError), read_journal(linked):
            pass
        # This completed foreign-scoped control is in the original worker's
        # history. Filtering reads by expected scope or incomplete status misses it.
        foreign = self.control(deployment_id="deployment-a", repository="github:github.com/trial/a", completed=1)
        self.insert_control(foreign)
        observed = self.records()
        self.assertEqual(observed["controls"], [foreign])
        with self.assertRaises(AssertionError):
            require_uncontrolled_journals(observed, self.binding)

    def test_peer_witness_rejects_every_durable_fence_and_binding_change(self):
        records = self.records()
        mutations = [
            ("lifecycle", "worker_id", "replacement"), ("lifecycle", "deployment_id", "deployment-a"),
            ("lifecycle", "repository", "github:github.com/trial/a"), ("lifecycle", "slot_id", "another-slot"),
            ("lifecycle", "generation", 2), ("lifecycle", "spec", canonical(self.spec | {"profile": "other"})),
            ("lifecycle", "runtime_id", "docker:other"), ("lifecycle", "draining", 1),
            ("lifecycle", "termination_confirmed", 1), ("lifecycle", "artifacts_captured", 1),
            ("runtime", "worker_id", "replacement"), ("runtime", "spec", canonical(self.spec | {"deployment_id": "deployment-a"})),
            ("runtime", "container_id", "other"), ("runtime", "profile_fingerprint", "e" * 64),
            ("runtime", "start_confirmed", 0), ("runtime", "retired", 1), ("runtime", "receipt", "{}"),
            ("attempt", "worker_id", "replacement"), ("attempt", "attempt_id", "other-attempt"),
            ("attempt", "exec_id", "replacement-exec"), ("attempt", "exec_dispatched", 0),
            ("attempt", "cancelled", 1), ("attempt", "exit_code", 0), ("attempt", "input_sha256", "e" * 64),
            ("attempt", "seconds", 999), ("attempt", "deadline", 1000001),
        ]
        for table, key, value in mutations:
            with self.subTest(table=table, key=key):
                changed = deepcopy(records)
                changed[table][key] = value
                with self.assertRaises(AssertionError):
                    require_uncontrolled_journals(changed, self.binding)
        for key, value in (("parent_id", "other-root"), ("execution_id", "replacement"),
                           ("worker_id", "replacement"), ("termination_confirmed", 1), ("artifacts_captured", 1)):
            with self.subTest(execution_field=key):
                changed = deepcopy(records)
                changed["executions"][0][key] = value
                with self.assertRaises(AssertionError):
                    require_uncontrolled_journals(changed, self.binding)
        for table in ("lifecycle", "runtime", "attempt"):
            with self.subTest(missing=table), self.assertRaises(AssertionError):
                require_uncontrolled_journals(records | {table: None}, self.binding)
        for action in ("drain", "stop"):
            for completed in (0, 1):
                with self.subTest(action=action, completed=completed), self.assertRaises(AssertionError):
                    require_uncontrolled_journals(records | {"controls": [self.control(action=action, completed=completed)]}, self.binding)

    def test_pending_owned_drain_requires_exact_intent_key_scope_and_uncertainty(self):
        pending = self.worker | {"reason": PENDING_DRAIN + "; " + LOCK_CONTENTION}
        require_pending_drain_runtime(pending, self.binding)
        with self.assertRaises(AssertionError):
            require_pending_drain_journals(self.records(), self.binding, "original-drain-key")
        self.lifecycle.execute("UPDATE managed_workers SET draining=1")
        self.insert_control(self.control())
        records = self.records()
        require_pending_drain_journals(records, self.binding, "original-drain-key")
        for key, value in (("deployment_id", "deployment-a"), ("repository", "github:github.com/trial/a"),
                           ("request_key", "replacement-key"), ("worker_id", "foreign-worker"),
                           ("payload", canonical({"worker_id": "foreign-worker", "action": "drain"})),
                           ("completed", 1), ("action", "stop")):
            with self.subTest(control_field=key):
                changed = deepcopy(records)
                changed["controls"][0][key] = value
                with self.assertRaises(AssertionError):
                    require_pending_drain_journals(changed, self.binding, "original-drain-key")
        for changed in ({"reason": LOCK_CONTENTION}, {"reason": "unrelated outage"}, {"reason": PENDING_DRAIN},
                        {"state": "draining", "reason": "new execution is fenced"}, {"admission_open": True},
                        {"deployment_id": "deployment-a"}, {"active_executions": []},
                        {"termination_confirmed": True}, {"artifacts_captured": True}):
            with self.subTest(http_change=changed), self.assertRaises(AssertionError):
                require_pending_drain_runtime(pending | changed, self.binding)
        for table, key, value in (("runtime", "retired", 1), ("runtime", "receipt", "{}"),
                                  ("attempt", "exit_code", 0), ("lifecycle", "draining", 0)):
            with self.subTest(false_completion=key):
                changed = deepcopy(records)
                changed[table][key] = value
                with self.assertRaises(AssertionError):
                    require_pending_drain_journals(changed, self.binding, "original-drain-key")
        with self.assertRaises(AssertionError):
            require_pending_drain_journals(records | {"controls": records["controls"] * 2}, self.binding, "original-drain-key")

    def test_controller_acknowledgment_cannot_hide_replacement_key_or_scope(self):
        self.assertEqual(controller_operation(self.controller_path, self.binding), (None, None))
        require_controller_drain(self.controller_path, self.binding, "original-drain-key")
        self.controller.execute("UPDATE workers SET pending_action='drain',action_key='original-drain-key'")
        self.controller.commit()
        require_controller_drain(self.controller_path, self.binding, "original-drain-key")
        mutations = [
            ("pending_action", "stop", "drain"), ("action_key", "replacement-key", "original-drain-key"),
            ("repository", "github:github.com/trial/a", self.spec["repository"]),
            ("slot_id", "other-slot", "slot-b"), ("generation", 2, 1), ("profile", "other", "profile-b"),
            ("snapshot", canonical(self.worker | {"worker_id": "replacement"}), canonical(self.worker)),
            ("snapshot", canonical(self.worker | {"deployment_id": "deployment-a"}), canonical(self.worker)),
        ]
        for field, changed, original in mutations:
            with self.subTest(field=field):
                self.controller.execute("UPDATE workers SET " + field + "=?", (changed,))
                self.controller.commit()
                with self.assertRaises(AssertionError):
                    require_controller_drain(self.controller_path, self.binding, "original-drain-key")
                self.controller.execute("UPDATE workers SET " + field + "=?", (original,))
                self.controller.commit()
        self.controller.execute("UPDATE metadata SET value='deployment-a'")
        self.controller.commit()
        with self.assertRaises(AssertionError):
            require_controller_drain(self.controller_path, self.binding, "original-drain-key")

    def test_container_and_removed_reservation_must_keep_original_ownership(self):
        require_live_container(self.container, self.binding)
        for mutate in (
            lambda item: item.update(Id="replacement"),
            lambda item: item["State"].update(Running=False),
            lambda item: item["Config"].update(Labels={}),
            lambda item: item["Config"]["Labels"].update({"openjules.worker": "foreign-worker"}),
            lambda item: item["Config"]["Labels"].update({"openjules.spec": "d" * 64}),
            lambda item: item["Config"]["Labels"].update({"openjules.profile": "e" * 64}),
        ):
            changed = deepcopy(self.container)
            mutate(changed)
            with self.assertRaises(AssertionError):
                require_live_container(changed, self.binding)
        require_active_runtime(self.worker, self.binding)
        with self.assertRaises(AssertionError):
            require_active_runtime(self.worker | {"schema_version": True}, self.binding)
        plan = {"deployment_id": self.spec["deployment_id"], "repositories": [{
            "repository": self.spec["repository"], "profile": self.spec["profile"], "enabled": False,
            "desired_workers": 1, "effective_target": 0, "reserved": 1, "workers": [self.worker],
        }]}
        require_removed_plan(plan, self.binding)
        for changed in ({"enabled": True}, {"desired_workers": 0}, {"effective_target": 1},
                        {"reserved": 0}, {"repository": "github:github.com/trial/a"}, {"profile": "other"}):
            with self.subTest(plan_change=changed):
                mutated = deepcopy(plan)
                mutated["repositories"][0].update(changed)
                with self.assertRaises(AssertionError):
                    require_removed_plan(mutated, self.binding)



class CapturePublicationTests(unittest.TestCase):
    def setUp(self):
        self.scope = {"deployment_id": "deployment-a", "repository": "github:github.com/trial/a"}
        self.base = "a" * 40
        self.complete = {
            **self.scope,
            "task": [{"id": "task-a", "status": "awaiting_review", "current_candidate": "candidate-a"}],
            "attempt": [{
                "id": "attempt-a", "task": "task-a", "worker": "runtime-author",
                "state": "exited", "termination_confirmed": True, "launch_fenced": True,
                "runtime_binding": {**self.scope, "worker_id": "worker-a", "attempt_id": "attempt-a",
                                    "base": self.base, "profile": "profile-a"},
                "captured_artifacts": {"patch": "b" * 64, "log": "c" * 64, "manifest": "d" * 64},
            }],
            "candidate": [{"id": "candidate-a", "task": "task-a", "attempt": "attempt-a",
                           "base": self.base, "patch": "b" * 64}],
        }
        self.pending = deepcopy(self.complete)
        self.pending["candidate"] = []
        self.pending["task"][0].update(status="in_progress")
        self.pending["task"][0].pop("current_candidate")
        self.running = deepcopy(self.pending)
        self.running["attempt"][0].update(state="running", termination_confirmed=False)
        self.running["attempt"][0].pop("launch_fenced")
        self.running["attempt"][0].pop("captured_artifacts")

    def observe(self, snapshots, clock=None):
        self.read = Mock(side_effect=snapshots)
        self.processes = SimpleNamespace(check=Mock())
        times = list(range(len(snapshots) + 1)) if clock is None else clock
        with patch("check_stack.time.monotonic", side_effect=times), patch("check_stack.time.sleep") as self.sleep:
            return wait_for_capture(self.read, self.scope, "task-a", "worker-a", self.base, self.processes, "a")

    def test_separate_terminal_and_candidate_publications_complete_within_one_barrier(self):
        unclaimed = deepcopy(self.running)
        unclaimed["attempt"] = []
        snapshots = [unclaimed, deepcopy(self.running), deepcopy(self.pending), deepcopy(self.complete)]
        # A prior task's valid candidate is ordinary retained history.
        for snapshot in snapshots:
            snapshot["candidate"].append({"id": "prior-candidate", "task": "prior-task", "attempt": "prior-attempt",
                                          "base": self.base, "patch": "e" * 64})
        self.assertEqual(self.observe(snapshots), (self.complete["attempt"][0], self.complete["candidate"][0]))
        self.assertEqual(self.read.call_count, 4)
        self.assertEqual(self.processes.check.call_count, 4)
        self.assertEqual(self.sleep.call_count, 3)

    def test_one_unchanged_deadline_distinguishes_terminal_and_candidate_waits(self):
        for snapshot, phase in ((self.running, "terminal capture"), (self.pending, "candidate publication")):
            with self.subTest(phase=phase), self.assertRaisesRegex(AssertionError, "deadline waiting for " + phase + " a"):
                self.observe([deepcopy(snapshot)], clock=[0, 1, 46])
            self.assertEqual(self.read.call_count, 1)
            self.assertEqual(self.sleep.call_count, 1)

    def test_existing_invalid_candidates_fail_on_first_observation(self):
        cases = [
            ("malformed collection", lambda item: item.update(candidate={})),
            ("malformed row", lambda item: item.update(candidate=[None])),
            ("missing identity", lambda item: item["candidate"][0].pop("id")),
            ("missing task", lambda item: item["candidate"][0].pop("task")),
            ("missing attempt", lambda item: item["candidate"][0].pop("attempt")),
            ("missing base", lambda item: item["candidate"][0].pop("base")),
            ("missing patch", lambda item: item["candidate"][0].pop("patch")),
            ("duplicate", lambda item: item["candidate"].append(deepcopy(item["candidate"][0]))),
            ("wrong task", lambda item: item["candidate"][0].update(task="foreign-task")),
            ("wrong attempt", lambda item: item["candidate"][0].update(attempt="foreign-attempt")),
            ("wrong base", lambda item: item["candidate"][0].update(base="f" * 40)),
            ("wrong patch", lambda item: item["candidate"][0].update(patch="f" * 64)),
            ("wrong current candidate", lambda item: item["task"][0].update(current_candidate="foreign-candidate")),
            ("missing current candidate", lambda item: item["task"][0].pop("current_candidate")),
            ("not awaiting review", lambda item: item["task"][0].update(status="in_progress")),
            ("self acceptance", lambda item: item["task"][0].update(status="accepted")),
            ("self admission", lambda item: item.update(admission=[{"id": "unauthorized"}])),
            ("premature candidate", lambda item: item["attempt"][0].update(termination_confirmed=False)),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                changed = deepcopy(self.complete)
                mutate(changed)
                with self.assertRaises(AssertionError):
                    self.observe([changed, deepcopy(self.complete)])
                self.assertEqual(self.read.call_count, 1)
                self.assertEqual(self.sleep.call_count, 0)

    def test_pending_publication_cannot_hide_changed_capture_or_scope(self):
        def replace_attempt(item):
            item["attempt"][0]["id"] = "replacement"
            item["attempt"][0]["runtime_binding"]["attempt_id"] = "replacement"
            item["candidate"][0]["attempt"] = "replacement"

        def replace_patch(item):
            item["attempt"][0]["captured_artifacts"]["patch"] = "f" * 64
            item["candidate"][0]["patch"] = "f" * 64

        cases = [
            ("scope", lambda item: item.update(repository="github:github.com/trial/b")),
            ("missing task", lambda item: item.update(task=[])),
            ("missing attempt", lambda item: item.update(attempt=[])),
            ("duplicate attempt", lambda item: item["attempt"].append(deepcopy(item["attempt"][0]))),
            ("replacement attempt", replace_attempt),
            ("changed capture", replace_patch),
            ("changed profile", lambda item: item["attempt"][0]["runtime_binding"].update(profile="replacement")),
            ("foreign worker", lambda item: item["attempt"][0]["runtime_binding"].update(worker_id="foreign-worker")),
            ("lost fence", lambda item: item["attempt"][0].update(launch_fenced=False)),
            ("false terminal", lambda item: item["attempt"][0].update(termination_confirmed=False)),
            ("nonboolean terminal", lambda item: item["attempt"][0].update(termination_confirmed=1)),
            ("wrong terminal state", lambda item: item["attempt"][0].update(state="running")),
            ("missing capture", lambda item: item["attempt"][0].pop("captured_artifacts")),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                changed = deepcopy(self.complete)
                mutate(changed)
                with self.assertRaises(AssertionError):
                    self.observe([deepcopy(self.pending), changed, deepcopy(self.complete)])
                self.assertEqual(self.read.call_count, 2)
                self.assertEqual(self.sleep.call_count, 1)

    def test_installation_still_verifies_the_exact_captured_bytes_after_publication(self):
        contents = {"patch": "+completed a\n", "log": "ISOLATION_BOUNDARY_VERIFIED_a\nISOLATION_COMPLETED_a\n",
                    "manifest": "{}"}
        artifacts = {kind: hashlib.sha256(content.encode()).hexdigest() for kind, content in contents.items()}
        pending, complete = deepcopy(self.pending), deepcopy(self.complete)
        for snapshot in (pending, complete):
            snapshot["attempt"][0]["captured_artifacts"] = artifacts
        complete["candidate"][0]["patch"] = artifacts["patch"]
        read = Mock(side_effect=[pending, complete])
        by_path = {"/managed/artifacts/" + artifacts[kind]: content for kind, content in contents.items()}
        authority = SimpleNamespace(request=Mock(side_effect=lambda method, path, **kwargs: by_path[path]))
        with TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "answer.txt").write_text("initial a\n")
            installation = SimpleNamespace(snapshot=read, scope=self.scope, base=self.base, label="a",
                                           source=source, sensitive={"private-token"}, authority=authority)
            with patch("check_stack.time.monotonic", side_effect=[0, 1, 2]), patch("check_stack.time.sleep"):
                result = Installation.captured(installation, "task-a", "worker-a", SimpleNamespace(check=lambda: None))
        self.assertEqual(result, {"task": "task-a", "attempt": "attempt-a", "worker": "worker-a",
                                  "candidate": "candidate-a", "artifacts": artifacts})
        self.assertEqual(read.call_count, 2)
        self.assertEqual(authority.request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
