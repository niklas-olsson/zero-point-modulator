"""Historical admission oracles; companion journal/process responses are fixtures.

Git source selection, Python import probes, SQLite/WAL, the installer writer and
controller are real. Docker and installed-service qualification remain in CI.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_historical_authority as trial
    import check_authority_admission as admission
    from check_authority_admission import RECORD_KINDS, canonical
finally:
    sys.path.pop(0)

from tests.test_authority_admission_trial import COMPANION_DDL
from zpm.controller import Controller
from zpm.deployment import _command, _prepare_files
from zpm.models import Binding, OPERATIONS
from zpm.store import Store


CHILD_DDL = """
    CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT NOT NULL,policy_digest TEXT NOT NULL,
        context TEXT,closed INTEGER NOT NULL DEFAULT 0,close_reason TEXT,
        close_delivered INTEGER NOT NULL DEFAULT 0,retire INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE intents(worker_id TEXT NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,
        delivery_key TEXT NOT NULL,child_id TEXT,status TEXT NOT NULL DEFAULT 'pending',
        PRIMARY KEY(worker_id,request_id));
    CREATE TABLE children(child_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
        uid INTEGER NOT NULL,grant_json TEXT NOT NULL,deadline REAL NOT NULL,boot_id TEXT NOT NULL,
        phase TEXT NOT NULL DEFAULT 'granted',exec_id TEXT,exec_dispatched INTEGER NOT NULL DEFAULT 0,
        armed_deadline REAL,exit_code INTEGER,revoked INTEGER NOT NULL DEFAULT 0,
        provisional TEXT NOT NULL DEFAULT '{}',capture TEXT,artifacts TEXT NOT NULL DEFAULT '{}',
        outcome_report TEXT,UNIQUE(worker_id,ordinal),UNIQUE(worker_id,uid));
"""


class HistoricalSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}}
        self.environment["PYTHONNOUSERSITE"] = "1"

    def source(self, name, *, outcomes=False, foreign_module=False):
        checkout = self.root / name
        checkout.mkdir()
        (checkout / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        names = (
            "src.liminal_bridge.coordinator_service", "src.liminal_bridge.managed_service",
            "src.core.collaboration.authority", "src.core.collaboration.managed_runtime",
            "src.core.collaboration.child_grants",
        ) + (("src.core.collaboration.child_outcomes",) if outcomes else ())
        for module in names:
            path = checkout.joinpath(*module.split(".")).with_suffix(".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("VALUE = " + repr(module) + "\n")
        if foreign_module:
            destination = self.root / "foreign.py"
            destination.write_text("VALUE = 'foreign'\n")
            selected = checkout / "src/liminal_bridge/managed_service.py"
            selected.unlink()
            selected.symlink_to(destination)
        subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "source"],
                       check=True, capture_output=True)
        revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD^{tree}"], text=True).strip()
        return checkout, revision, tree

    def probe(self, source, outcomes):
        checkout, revision, tree = source
        return trial.source_provenance(checkout, sys.executable, self.environment, revision, tree, outcomes=outcomes)

    def test_actual_namespace_imports_resolve_each_separate_clean_revision(self):
        old = self.source("older")
        current = self.source("current", outcomes=True)
        bundle = self.root / "bundle"
        bundle.mkdir()
        trial.distinct_sources(current[0], old[0], bundle)
        prior = self.probe(old, False)
        newer = self.probe(current, True)
        self.assertEqual(prior["revision"], old[1])
        self.assertFalse(prior["initial_outcome_module_present"])
        self.assertTrue(newer["initial_outcome_module_present"])
        self.assertEqual(set(newer["module_sha256"]), set(prior["module_sha256"]) | {trial.OUTCOME_MODULE})
        self.assertNotIn(str(self.root), json.dumps(prior | newer))
        self.assertEqual(self.probe(old, False), prior)

    def test_wrong_revision_tree_dirty_checkout_and_aliases_cannot_prove_old_code(self):
        source = self.source("older")
        checkout, revision, tree = source
        for expected_revision, expected_tree in (("0" * 40, tree), (revision, "0" * 40)):
            with self.subTest(revision=expected_revision, tree=expected_tree), self.assertRaises(AssertionError):
                trial.verify_source(checkout, expected_revision, expected_tree)
        (checkout / "src/liminal_bridge/managed_service.py").write_text("changed\n")
        with self.assertRaises(AssertionError):
            self.probe(source, False)
        subprocess.run(["git", "-C", str(checkout), "checkout", "--", "."], check=True, capture_output=True)
        (checkout / "unapproved.py").write_text("extra\n")
        with self.assertRaises(AssertionError):
            self.probe(source, False)
        (checkout / "unapproved.py").unlink()
        bundle = self.root / "bundle"
        bundle.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(checkout, target_is_directory=True)
        for other in (checkout, alias, checkout / "src"):
            with self.subTest(other=other), self.assertRaises(AssertionError):
                trial.distinct_sources(checkout, other, bundle)

    def test_clean_status_with_hidden_tracked_changes_cannot_prove_pinned_source(self):
        source = self.source("hidden-index-change")
        checkout, revision, tree = source
        relative = "src/liminal_bridge/managed_service.py"
        module = checkout / relative
        original = module.read_bytes()
        self.probe(source, False)
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                subprocess.run(["git", "-C", str(checkout), "update-index", "--" + flag, relative],
                               check=True, capture_output=True)
                module.write_text("VALUE = 'different tracked implementation'\n")
                self.assertEqual(subprocess.check_output(
                    ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"], text=True
                ), "")
                # The previous HEAD/tree/status guard genuinely accepts this
                # fixture. The new direct Git-object comparison must reject it.
                self.assertEqual(trial.verify_source(checkout, revision, tree),
                                 {"revision": revision, "tree": tree})
                with self.assertRaisesRegex(AssertionError, "pinned Git blob"):
                    self.probe(source, False)
                module.write_bytes(original)
                subprocess.run(["git", "-C", str(checkout), "update-index", "--no-" + flag, relative],
                               check=True, capture_output=True)
                self.probe(source, False)

    def test_foreign_source_or_unexpected_outcome_module_fails_import_provenance(self):
        with self.assertRaises(AssertionError):
            self.probe(self.source("foreign", foreign_module=True), False)
        with self.assertRaises(AssertionError):
            self.probe(self.source("unexpected", outcomes=True), False)
        with self.assertRaises(AssertionError):
            self.probe(self.source("missing"), True)
        old = self.source("environment")
        self.environment["PYTHONPATH"] = str(self.root)
        with self.assertRaises(AssertionError):
            self.probe(old, False)


class HistoricalStateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bundle = self.root / "installation"
        self.bundle.mkdir(mode=0o700)
        self.scope = {"deployment_id": "historical-deployment", "repository": "github:github.com/trial/historical"}
        self.profile = "approved-initial-outcome"
        self.configuration = {
            "schema_version": 1, "deployment_id": self.scope["deployment_id"], "max_workers": 1,
            "binding": Binding.parse({"schema_version": 1, "repository": self.scope["repository"],
                                      "profile": self.profile, "desired_workers": 0}).to_dict(),
            "sources": {name: {"checkout": str(self.root / name), "revision": "1" * 40}
                        for name in ("zpm", "openjules", "polyphony")},
            "source": {"checkout": str(self.root / "source"), "revision": "a" * 40},
            "image": "sha256:" + "b" * 64,
            "command": ["python3", "-I", "-S", "/opt/openjules/managed_root_runner.py"],
            "ports": {"polyphony": 8766, "openjules": 8000}, "environment_refs": {},
            "runtime": {"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000,
                        "pids_limit": 128, "max_seconds": 90, "max_archive_bytes": 16777216},
            "policy": {"checks": ["tests"], "reviewers": ["operator"], "max_attempts": 1,
                       "budget_seconds": 120, "attempt_seconds": 60, "capabilities": ["python"]},
            "model_policy": {"endpoint": "http://127.0.0.1:9000/v1/chat/completions",
                             "model": "fixture", "token_ref": "env:HISTORICAL_TEST_PROVIDER",
                             "max_calls": 12, "max_output_tokens": 1024, "max_total_output_tokens": 12288,
                             "request_timeout_seconds": 5},
            "child_policy": {"max_children": 2, "max_child_seconds": 30, "max_child_turns": 2,
                             "capabilities": ["python"], "uid_start": 60000,
                             "verify_argv": ["python3", "-I", "-c", "pass"], "outcome_protocol": "initial-v1"},
        }
        self.configuration["sources"]["polyphony"]["revision"] = trial.CURRENT_REVISION
        for entry in self.configuration["sources"].values():
            Path(entry["checkout"]).mkdir()
        self.historical = self.root / "historical"
        self.historical.mkdir()
        marker = self.bundle / "preparation.json"
        marker.write_text("{}\n")
        marker.chmod(0o600)
        _prepare_files(self.configuration, self.bundle, self.bundle)
        config = json.loads((self.bundle / "config/openjules.json").read_text())
        state = Path(config["state_directory"])
        self.paths = {
            "controller": self.bundle / "state/zpm.sqlite3",
            "authority": self.bundle / "state/polyphony/authority.sqlite3",
            "lifecycle": Path(config["lifecycle_database"]),
            "runtime": state / "runtime/journal.sqlite3",
            "dispatcher": state / "dispatcher/dispatch.sqlite3",
            "model": state / "models/inference.sqlite3",
            "children": state / "children/children.sqlite3",
        }
        self.archive = state / "runtime/captures"
        self.archive.mkdir(mode=0o700, parents=True)
        self.connections = {}
        for name, path in self.paths.items():
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            connection.execute("PRAGMA journal_mode=WAL")
            if name == "children":
                connection.executescript(CHILD_DDL)
            elif name != "controller":
                connection.executescript(COMPANION_DDL[name])
            self.connections[name] = connection
        key = (self.scope["deployment_id"], self.scope["repository"], self.profile)
        runtime = self.connections["runtime"]
        runtime.execute("INSERT INTO profiles VALUES(?,?,?,?)", (*key, "f" * 64))
        runtime.execute("INSERT INTO child_profiles VALUES(?,?,?,?)", (*key, "d" * 64))
        runtime.commit()
        model = self.connections["model"]
        model.execute("INSERT INTO policies VALUES(?,?)", (canonical(key), "e" * 64))
        model.commit()
        self.canonical_profile = json.loads((self.bundle / "config/polyphony.json").read_text())["repositories"][self.scope["repository"]]["profiles"][self.profile]

    def histories(self, expected=None):
        return trial.historical_histories(self.bundle, self.configuration, self.scope, self.profile, expected)

    def current_capability(self):
        profile = self.canonical_profile
        return {
            "schema_version": 1, **self.scope, "service": "keystone-polyphony", "protocol": "managed-runtime-v1",
            "profile": self.profile, "base": profile["base"],
            "profile_digest": hashlib.sha256(canonical(profile).encode()).hexdigest(),
            "attempt_seconds": profile["attempt_seconds"], "capabilities": profile["capabilities"],
            "managed_dispatch": True, "external_root": True, "children": False, "child_grants": True,
            "child_initial_outcome": 1, "profile_supported": True, "reason": "",
            "child_policy": {name: profile[name] for name in
                             ("max_children", "max_child_seconds", "max_child_turns", "child_capabilities")},
        }

    def runtime_capability(self, ready):
        return {"schema_version": 1, **self.scope, "profile": self.profile, "operations": sorted(OPERATIONS),
                "profile_supported": True, "repository_access": ready, "managed_dispatch": ready,
                "children": ready, "reason": "ready" if ready else trial.RUNTIME_REASON}

    def test_capability_requires_exact_positive_and_only_the_historical_omission(self):
        current = self.current_capability()
        trial.require_current_capabilities(current, self.scope, self.profile, self.canonical_profile)
        older = {key: value for key, value in current.items() if key != "child_initial_outcome"}
        trial.require_historical_capabilities(older, current)
        for key, value in (("child_initial_outcome", True), ("child_initial_outcome", 0),
                           ("schema_version", True), ("base", "b" * 40), ("repository", "github:github.com/foreign/repo"),
                           ("child_grants", False)):
            changed = current | {key: value}
            with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                trial.require_current_capabilities(changed, self.scope, self.profile, self.canonical_profile)
        mutations = [
            older | {"child_initial_outcome": None}, older | {"child_initial_outcome": 1},
            older | {"schema_version": True}, older | {"base": "b" * 40},
            older | {"child_grants": False}, older | {"managed_dispatch": False},
            older | {"reason": "unrelated failure"}, older | {"unknown": True},
            older | {"profile_digest": "0" * 64},
        ]
        changed_child = deepcopy(older)
        changed_child["child_policy"]["max_children"] = 1
        mutations.append(changed_child)
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                trial.require_historical_capabilities(changed, current)
        trial.require_child_runtime(self.runtime_capability(False), self.scope, self.profile, False)
        with self.assertRaises(AssertionError):
            trial.require_child_runtime(self.runtime_capability(False) | {"children": True}, self.scope, self.profile, False)

    def test_historical_diagnostic_does_not_change_current_no_policy_defaults(self):
        expected = "Canonical managed authority is unsupported: child_initial_outcome=1"
        value = self.runtime_capability(False)
        self.assertEqual(value["reason"], expected)
        trial.require_child_runtime(value, self.scope, self.profile, False)
        with self.assertRaises(AssertionError):
            admission.require_runtime_capabilities(value, self.scope, self.profile, False)

        with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
            store.change(self.scope["repository"], "target", 1)
            provider = SimpleNamespace(capabilities=lambda *_: value)
            controller = Controller(store, provider, max_workers=1)
            report = controller.tick()
            self.assertEqual(report["repositories"][0]["validation"], {"ready": False, "reason": expected})
            admission.require_controller_plan(report, self.scope, self.profile, 1, False,
                                              unavailable_reason=expected)
            with self.assertRaises(AssertionError):
                admission.require_controller_plan(report, self.scope, self.profile, 1, False)

            # The actual current-service/no-managed-policy scene still uses
            # the generic runtime reason and normalized controller default.
            value = value | {"reason": admission.RUNTIME_REASON}
            admission.require_runtime_capabilities(value, self.scope, self.profile, False)
            with self.assertRaises(AssertionError):
                trial.require_child_runtime(value, self.scope, self.profile, False)
            report = controller.tick()
            self.assertEqual(report["repositories"][0]["validation"],
                             {"ready": False, "reason": "repository_access unavailable"})
            admission.require_controller_plan(report, self.scope, self.profile, 1, False)
            with self.assertRaises(AssertionError):
                admission.require_controller_plan(report, self.scope, self.profile, 1, False,
                                                  unavailable_reason=expected)
            self.assertEqual(store.workers(), [])

            value = self.runtime_capability(True)
            store.change(self.scope["repository"], "target", 0)
            report = controller.tick()
            trial.require_child_runtime(value, self.scope, self.profile, True)
            admission.require_controller_plan(report, self.scope, self.profile, 0, True)
            self.assertEqual(store.workers(), [])

    def test_actual_generated_child_paths_and_wal_reject_every_child_history_table(self):
        original = self.histories()
        self.assertEqual(set(original), {"controller", "authority", "lifecycle", "runtime", "dispatcher", "model", "children"})
        self.assertEqual(self.paths["children"], self.bundle / "state/openjules/runtime/children/children.sqlite3")
        db = self.connections["children"]
        rows = (
            ("workers", "INSERT INTO workers(worker_id,spec,policy_digest) VALUES('foreign','{}','digest')"),
            ("intents", "INSERT INTO intents(worker_id,request_id,payload,delivery_key) VALUES('foreign','r','{}','key')"),
            ("children", "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id) "
                         "VALUES('foreign','foreign',1,60000,'{}',1,'boot')"),
        )
        for table, statement in rows:
            db.execute(statement)
            db.commit()
            with self.subTest(table=table), self.assertRaises(AssertionError):
                self.histories(original)
            db.execute("DELETE FROM " + table)
            db.commit()
            self.assertEqual(self.histories(original), original)

    def test_missing_child_schema_or_archive_cannot_be_empty_evidence(self):
        self.histories()
        db = self.connections["children"]
        db.execute("ALTER TABLE children ADD COLUMN unexpected TEXT")
        db.commit()
        with self.assertRaises(AssertionError):
            self.histories()
        db.close()
        original = self.paths["children"]
        saved = original.with_name("retained.sqlite3")
        original.rename(saved)
        with self.assertRaises(AssertionError):
            self.histories()
        self.assertFalse(original.exists())
        original.symlink_to(saved)
        with self.assertRaises(AssertionError):
            self.histories()

    def test_enabled_child_fingerprint_policy_and_empty_archive_are_retained(self):
        baseline = self.histories()
        db = self.connections["runtime"]
        db.execute("UPDATE child_profiles SET fingerprint=NULL")
        db.commit()
        with self.assertRaises(AssertionError):
            self.histories()
        db.execute("UPDATE child_profiles SET fingerprint=?", ("c" * 64,))
        db.commit()
        with self.assertRaises(AssertionError):
            self.histories(baseline)
        db.execute("UPDATE child_profiles SET fingerprint=?", ("d" * 64,))
        db.commit()
        self.histories(baseline)
        (self.archive / "unexpected.tar").write_bytes(b"archive fixture")
        with self.assertRaises(AssertionError):
            self.histories(baseline)
        (self.archive / "unexpected.tar").unlink()
        self.archive.rmdir()
        with self.assertRaises(OSError):
            self.histories(baseline)

    def test_runtime_process_identity_is_required_even_with_same_pid(self):
        process = SimpleNamespace(pid=123, poll=lambda: None)
        processes = SimpleNamespace(running={"admission-openjules": process})
        trial.require_same_runtime(processes, process, 123)
        replacement = SimpleNamespace(pid=123, poll=lambda: None)
        processes.running["admission-openjules"] = replacement
        with self.assertRaises(AssertionError):
            trial.require_same_runtime(processes, process, 123)
        processes.running["admission-openjules"] = process
        with self.assertRaises(AssertionError):
            trial.require_same_runtime(processes, process, 124)
        process.poll = lambda: 0
        with self.assertRaises(AssertionError):
            trial.require_same_runtime(processes, process, 123)

    def phase(self, *, fail_ensure=None, fail_restore=False):
        """Named orchestration fixture around real generated config/Store/journals."""
        owner = self
        events, commands, ensure_payloads = [], [], []
        source_paths = {name: Path(entry["checkout"]) for name, entry in self.configuration["sources"].items()}
        current = self.current_capability()
        phase = {"old": False, "starts": 0}

        class Processes:
            def __init__(self):
                self.running = {}
                self.root = owner.root

            def start(self, name, command, cwd, environment):
                events.append(("start", name, Path(cwd).name))
                if name == "admission-authority":
                    phase["starts"] += 1
                    if fail_restore and phase["starts"] == 3:
                        raise OSError("private restore detail")
                    phase["old"] = Path(cwd) == owner.historical
                    commands.append((list(command), dict(environment)))
                process = SimpleNamespace(pid=100 + len(events), returncode=None)
                process.poll = lambda: process.returncode
                self.running[name] = process
                return process

            def stop(self, name):
                events.append(("stop", name))
                process = self.running.pop(name, None)
                if process is not None:
                    process.returncode = 0

            def check(self):
                if any(process.poll() is not None for process in self.running.values()):
                    raise AssertionError("fixture service exited")

        class Client:
            def __init__(self, endpoint, token):
                self.endpoint, self.token = endpoint, token

            def request(self, method, path, **kwargs):
                if path == "/managed/capabilities":
                    return deepcopy({key: value for key, value in current.items()
                                     if not (phase["old"] and key == "child_initial_outcome")})
                if path == "/managed/snapshot":
                    return {"schema_version": 1, **owner.scope, "cursor": 0, **{key: [] for key in RECORD_KINDS}}
                if path == "/api/workers/capabilities":
                    return owner.runtime_capability(not phase["old"])
                if path == "/api/workers":
                    return {"schema_version": 1, "workers": []}
                raise AssertionError(path)

        def completed(processes, command, cwd, environment, name):
            self.assertEqual(command, _command(self.bundle, "zpm", self.configuration))
            capability = self.runtime_capability(not phase["old"])
            provider = SimpleNamespace(capabilities=lambda *_: capability)
            with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
                value = Controller(store, provider, max_workers=1).tick()
            events.append(("reconcile", name, value["repositories"][0]["desired_workers"]))
            return value

        def ensure(client, method, path, *, data):
            self.assertTrue(phase["old"])
            self.assertEqual((method, path), ("POST", "/api/workers/ensure"))
            ensure_payloads.append(deepcopy(data))
            if fail_ensure is not None:
                raise fail_ensure
            return {"detail": "Managed runtime preflight is not ready"}

        def provenance(checkout, interpreter, environment, revision, tree, *, outcomes):
            self.assertEqual(interpreter, _command(self.bundle, "polyphony", self.configuration)[0])
            self.assertNotIn("PYTHONPATH", environment)
            return {"revision": revision, "tree": tree, "module_sha256": {}, "initial_outcome_module_present": outcomes}

        processes = Processes()
        model = SimpleNamespace(lock=threading.Lock(), requests=[], errors=[])
        try:
            with patch("zpm.deployment.verify"), patch.object(trial, "source_provenance", provenance), \
                    patch.object(trial, "verify_source"), patch.object(trial, "Client", Client), \
                    patch.object(trial, "completed_reconcile", completed), \
                    patch.object(trial, "expected_error", ensure), \
                    patch.object(trial, "docker_inventory", return_value={"containers": [], "volumes": []}), \
                    patch("check_authority_admission.docker_inventory", return_value={"containers": [], "volumes": []}):
                result = trial.qualify_historical_authority(
                    self.bundle, source_paths, self.historical, self.scope, self.profile, model, processes,
                    {"PATH": os.environ.get("PATH", ""), "HISTORICAL_TEST_PROVIDER": "fixture-token"},
                )
            return result, events, commands, ensure_payloads, processes
        finally:
            self.last_phase = (events, commands, ensure_payloads, processes)

    def test_phase_uses_unchanged_generated_argv_environment_and_original_empty_state(self):
        baseline = self.histories()
        result, events, commands, payloads, processes = self.phase()
        self.assertEqual(result["managed_capabilities_status"], 200)
        expected_reason = "Canonical managed authority is unsupported: child_initial_outcome=1"
        self.assertEqual(result["runtime_reason"], expected_reason)
        self.assertEqual(result["controller_reason"], expected_reason)
        self.assertEqual(result["blocked_controller_passes"], [
            {"desired_workers": 1, "effective_target": 1, "healthy": 0, "reserved": 0, "shortfall": 1},
        ] * 2)
        self.assertEqual(result["ensure_rejections"], 2)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(commands[0], commands[1])
        self.assertEqual(commands[0], commands[2])
        self.assertEqual(commands[0][0], _command(self.bundle, "polyphony", self.configuration))
        self.assertIn("--managed-config", commands[0][0])
        self.assertEqual([event[2] for event in events if event[0] == "reconcile"], [0, 1, 1, 0])
        self.assertEqual([event[2] for event in events if event[:2] == ("start", "admission-authority")],
                         ["polyphony", "historical", "polyphony"])
        self.assertEqual(sum(event[:2] == ("start", "admission-openjules") for event in events), 1)
        self.assertEqual(processes.running, {})
        self.assertEqual(self.histories(baseline), baseline)
        with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
            self.assertEqual(Controller(store).plan()["repositories"][0]["desired_workers"], 0)

    def test_denied_phase_and_restore_failures_reset_target_and_reap_owned_services(self):
        original = AssertionError("original historical admission failure")
        with self.assertRaises(AssertionError) as caught:
            self.phase(fail_ensure=original)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.last_phase[3].running, {})
        self.assertEqual(self.last_phase[0][-2:], [("stop", "admission-authority"), ("stop", "admission-openjules")])
        with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
            self.assertEqual(Controller(store).plan()["repositories"][0]["desired_workers"], 0)
        with self.assertRaisesRegex(OSError, "private restore detail"):
            self.phase(fail_restore=True)
        self.assertEqual(self.last_phase[3].running, {})
        with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
            self.assertEqual(Controller(store).plan()["repositories"][0]["desired_workers"], 0)


if __name__ == "__main__":
    unittest.main()
