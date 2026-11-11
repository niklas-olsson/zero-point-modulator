"""Deterministic checks for shared-installation evidence, without Docker."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zpm.deployment import _prepare_files
from zpm.models import Binding, OPERATIONS, readiness
from zpm.store import Store

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_multi_repository_stack import (
        SOURCE_READINESS, SOURCE_UNAVAILABLE, canonical, cleanup_services,
        displace_source, repository_view, require_quota,
        require_same_history, require_scope_capability, require_shared_plan,
        require_source_block, retained_history,
    )
finally:
    sys.path.pop(0)


class SharedPlanEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.deployment = "shared-deployment"
        self.a, self.b = ("github:github.com/shared-trial/" + label for label in ("a", "b"))
        self.repositories = (self.a, self.b)
        rows = []
        for repository in self.repositories:
            rows.append({
                "repository": repository, "profile": "approved", "desired_workers": 1,
                "effective_target": 1, "permitted_target": 1, "enabled": True, "paused": False,
                "revision": 1, "healthy": 1, "reserved": 1, "shortfall": 0, "launch_deficit": 0,
                "validation": {"ready": True, "reason": ""}, "reasons": [],
                "workers": [{"schema_version": 1, "deployment_id": self.deployment, "repository": repository,
                             "worker_id": "worker-" + repository[-1], "state": "ready", "admission_open": True,
                             "active_executions": [], "pending_operation": None}],
            })
        self.plan = {"schema_version": 1, "deployment_id": self.deployment, "max_workers": 2,
                     "reserved": 2, "over_capacity": 0, "repositories": rows}

    def test_full_topology_is_checked_before_selecting_a_repository_view(self):
        before = deepcopy(self.plan)
        view = repository_view(self.plan, self.deployment, self.repositories, self.b)
        self.assertEqual(view["reserved"], 1)
        self.assertEqual([row["repository"] for row in view["repositories"]], [self.b])
        self.assertEqual(self.plan, before)
        changes = [
            ("extra row", lambda value: value["repositories"].append(deepcopy(value["repositories"][0]))),
            ("missing row", lambda value: value["repositories"].pop(0)),
            ("foreign unselected row", lambda value: value["repositories"][0].update(repository="github:github.com/foreign/repo")),
            ("duplicate rows", lambda value: value["repositories"][0].update(repository=self.b)),
            ("foreign deployment", lambda value: value.update(deployment_id="foreign")),
            ("foreign unselected worker", lambda value: value["repositories"][0]["workers"][0].update(repository=self.b)),
            ("reused worker", lambda value: value["repositories"][0]["workers"][0].update(worker_id="worker-b")),
            ("missing maximum", lambda value: value.pop("max_workers")),
            ("offline unknown maximum", lambda value: value.update(max_workers=None)),
            ("incorrect total", lambda value: value.update(reserved=1)),
            ("over capacity", lambda value: value.update(reserved=3, over_capacity=1)),
            ("hidden reservation", lambda value: value["repositories"][0].update(reserved=2)),
            ("missing worker", lambda value: value["repositories"][0]["workers"].clear()),
            ("boolean count", lambda value: value["repositories"][0].update(reserved=True)),
            ("boolean schema", lambda value: value.update(schema_version=True)),
        ]
        for label, change in changes:
            with self.subTest(label=label):
                value = deepcopy(self.plan)
                change(value)
                with self.assertRaises(AssertionError):
                    repository_view(value, self.deployment, self.repositories, self.b)

    def source_plan(self):
        value = deepcopy(self.plan)
        value["reserved"] = 1
        row = value["repositories"][0]
        ready, reason = readiness({
            "schema_version": 1, "operations": sorted(OPERATIONS), "profile_supported": True,
            "repository_access": False, "managed_dispatch": False, "reason": SOURCE_UNAVAILABLE,
        })
        self.assertEqual((ready, reason), (False, SOURCE_READINESS))
        row.update(healthy=0, reserved=0, shortfall=1, launch_deficit=1, workers=[],
                   validation={"ready": ready, "reason": reason}, reasons=[reason])
        return value

    def test_source_failure_requires_free_global_capacity_and_the_requested_target(self):
        value = self.source_plan()
        require_source_block(value, self.deployment, self.a, self.b)
        changes = [
            ("no requested work", lambda plan: plan["repositories"][0].update(desired_workers=0, effective_target=0, permitted_target=0)),
            ("no launch deficit", lambda plan: plan["repositories"][0].update(launch_deficit=0)),
            ("unrelated unavailable authority", lambda plan: plan["repositories"][0]["validation"].update(reason="authority unavailable")),
            ("stale positive validation", lambda plan: plan["repositories"][0]["validation"].update(ready=True)),
            ("global ceiling", lambda plan: plan["repositories"][0]["reasons"].append("deployment capacity ceiling")),
            ("repository ceiling", lambda plan: plan["repositories"][0]["reasons"].append("repository capacity ceiling")),
        ]
        for label, change in changes:
            with self.subTest(label=label):
                plan = deepcopy(value)
                change(plan)
                with self.assertRaises(AssertionError):
                    require_source_block(plan, self.deployment, self.a, self.b)
        masked = deepcopy(self.plan)
        masked["repositories"][0]["validation"] = {"ready": False, "reason": SOURCE_READINESS}
        masked["repositories"][0]["reasons"] = [SOURCE_READINESS]
        with self.assertRaises(AssertionError):
            require_source_block(masked, self.deployment, self.a, self.b)

    def quota_plan(self):
        value = deepcopy(self.plan)
        value["repositories"][0].update(desired_workers=2, effective_target=2, permitted_target=2,
                                         shortfall=1, launch_deficit=1, reasons=["deployment capacity ceiling"])
        return value

    def test_global_ceiling_requires_an_idle_healthy_worker_and_explicit_shortfall(self):
        value = self.quota_plan()
        require_quota(value, self.deployment, self.a, self.b)
        changes = [
            ("no unmet target", lambda plan: plan["repositories"][0].update(desired_workers=1)),
            ("repository instead of global ceiling", lambda plan: plan["repositories"][0].update(permitted_target=1)),
            ("shortfall suppressed", lambda plan: plan["repositories"][0].update(shortfall=0)),
            ("source not ready", lambda plan: plan["repositories"][0]["validation"].update(ready=False)),
            ("missing ceiling reason", lambda plan: plan["repositories"][0].update(reasons=[])),
            ("ambiguous repository ceiling", lambda plan: plan["repositories"][0]["reasons"].append("repository capacity ceiling")),
            ("active A root", lambda plan: plan["repositories"][0]["workers"][0].update(state="busy", active_executions=["attempt"])),
            ("closed A admission", lambda plan: plan["repositories"][0]["workers"][0].update(admission_open=False)),
            ("pending A drain", lambda plan: plan["repositories"][0]["workers"][0].update(pending_operation="drain")),
            ("missing A pending evidence", lambda plan: plan["repositories"][0]["workers"][0].pop("pending_operation")),
        ]
        for label, change in changes:
            with self.subTest(label=label):
                plan = deepcopy(value)
                change(plan)
                with self.assertRaises(AssertionError):
                    require_quota(plan, self.deployment, self.a, self.b)

    def test_readiness_checks_use_the_actual_runtime_and_authority_contracts(self):
        scope = {"deployment_id": self.deployment, "repository": self.a}
        runtime = {"schema_version": 1, **scope, "profile": "approved", "profile_supported": True,
                   "managed_dispatch": True, "repository_access": True, "reason": ""}
        require_scope_capability(runtime, scope, "approved")
        unavailable = runtime | {"managed_dispatch": False, "repository_access": False, "reason": SOURCE_UNAVAILABLE}
        require_scope_capability(unavailable, scope, "approved", source_available=False)
        authority = {"schema_version": 1, **scope, "profile": "approved", "profile_supported": True,
                     "managed_dispatch": True, "service": "keystone-polyphony",
                     "protocol": "managed-runtime-v1", "external_root": True}
        require_scope_capability(authority, scope, "approved", authority=True)
        for value, options in [
            (authority, {}),
            (runtime | {"repository": self.b}, {}),
            (runtime | {"profile": "other"}, {}),
            (runtime | {"managed_dispatch": 1}, {}),
            (unavailable | {"reason": "some failure"}, {"source_available": False}),
            (unavailable | {"profile_supported": False}, {"source_available": False}),
            (authority | {"external_root": False}, {"authority": True}),
            (authority | {"protocol": "legacy"}, {"authority": True}),
        ]:
            with self.subTest(value=value, options=options):
                with self.assertRaises(AssertionError):
                    require_scope_capability(value, scope, "approved", **options)

    def test_history_comparison_cannot_hide_a_transient_or_retired_allocation(self):
        before = {"zpm": [{"launch_key": "old", "generation": 1}],
                  "lifecycle": [{"worker_id": "old"}], "runtime": [{"worker_id": "old"}]}
        require_same_history(before, deepcopy(before))
        for kind in before:
            with self.subTest(kind=kind):
                after = deepcopy(before)
                after[kind].append({"worker_id": "created-then-retired", "launch_key": "new"})
                with self.assertRaises(AssertionError):
                    require_same_history(before, after)
        after = deepcopy(before)
        after["zpm"][0]["generation"] = True
        with self.assertRaises(AssertionError):
            require_same_history(before, after)


class SharedRetainedHistoryTests(unittest.TestCase):
    """Actual ZPM schema plus synthetic pinned-OJ rows; no runtime execution."""

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "installation"
        self.bundle.mkdir()
        # Synthetic private stage marker required by the real bundle writer.
        (self.bundle / "preparation.json").write_text("{}\n")
        (self.bundle / "preparation.json").chmod(0o600)
        self.deployment = "shared-history"
        self.repositories = tuple("github:github.com/shared-history/" + label for label in ("a", "b"))
        entries = []
        for repository in self.repositories:
            entries.append({
                "binding": {"schema_version": 1, "repository": repository, "profile": "approved",
                            "desired_workers": 1, "max_workers": 2},
                "source": {"checkout": str(self.root / repository[-1]), "revision": "1" * 40},
                "image": "sha256:" + "2" * 64, "command": ["python3", "-c", "pass"],
                "runtime": {}, "environment_refs": {},
                "policy": {"checks": ["tests"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 60, "attempt_seconds": 60, "capabilities": ["python"]},
            })
        self.entries = entries
        data = {"schema_version": 2, "deployment_id": self.deployment, "max_workers": 2,
                "sources": {name: {"checkout": str(self.root / name), "revision": "3" * 40}
                            for name in ("zpm", "openjules", "polyphony")},
                "ports": {"polyphony": 8766, "openjules": 8000}, "repositories": entries}
        # The production writer creates the real ownership journal and paths.
        # These fixtures intentionally do not parse Git sources or run services.
        _prepare_files(data, self.bundle, self.bundle)
        config = json.loads((self.bundle / "config/openjules.json").read_text())
        self.lifecycle_path = Path(config["lifecycle_database"])
        runtime_directory = Path(config["state_directory"]) / "runtime"
        runtime_directory.mkdir(parents=True)
        self.runtime_path = runtime_directory / "journal.sqlite3"
        self.assertEqual(self.runtime_path, self.bundle / "state/openjules/runtime/runtime/journal.sqlite3")
        self.lifecycle = sqlite3.connect(self.lifecycle_path)
        self.runtime = sqlite3.connect(self.runtime_path)
        for connection in (self.lifecycle, self.runtime):
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
        """)
        self.runtime.executescript("""
            CREATE TABLE workers (
                worker_id TEXT PRIMARY KEY, spec TEXT NOT NULL, profile_fingerprint TEXT NOT NULL,
                container_id TEXT, create_dispatched INTEGER NOT NULL DEFAULT 0,
                start_dispatched INTEGER NOT NULL DEFAULT 0, start_confirmed INTEGER NOT NULL DEFAULT 0,
                retired INTEGER NOT NULL DEFAULT 0, receipt TEXT);
        """)
        self.add_worker(0, "retired-a", retired=True)
        self.add_worker(0, "current-a")
        self.add_worker(1, "current-b")

    def add_worker(self, index, identity, retired=False):
        binding = Binding.parse(self.entries[index]["binding"])
        with Store(self.bundle / "state/zpm.sqlite3", self.deployment) as store:
            reserved = store.reserve(binding, store.binding(binding.repository)["revision"])
            snapshot = reserved["snapshot"] | {
                "worker_id": identity, "state": "stopped" if retired else "ready",
                "termination_confirmed": retired, "artifacts_captured": retired, "admission_open": not retired,
            }
            store.save_observation(reserved["launch_key"], snapshot)
        spec = {key: snapshot[key] for key in ("schema_version", "deployment_id", "repository",
                                               "slot_id", "generation", "launch_key", "profile")}
        container = "container-" + identity
        self.lifecycle.execute("INSERT INTO managed_workers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, self.deployment, binding.repository, spec["slot_id"], spec["generation"], canonical(spec),
            "docker:" + container, snapshot["state"], "", int(retired), int(retired), int(retired)))
        self.runtime.execute("INSERT INTO workers VALUES (?,?,?,?,?,?,?,?,?)", (
            identity, canonical(spec), "f" * 64, container, 1, 1, 1, int(retired), "{}" if retired else None))
        self.lifecycle.commit()
        self.runtime.commit()

    def history(self, index=0):
        return retained_history(self.bundle, self.deployment, self.repositories, self.repositories[index])

    def test_reader_retains_released_generations_and_committed_wal_rows(self):
        before = self.history()
        self.assertEqual(len(before["zpm"]), 2)
        self.assertEqual({row["worker_id"] for row in before["zpm"]}, {"retired-a", "current-a"})
        self.assertEqual({row["worker_id"] for row in before["runtime"]}, {"retired-a", "current-a"})
        self.assertEqual(len(self.history(1)["zpm"]), 1)
        self.add_worker(0, "brief-allocation", retired=True)
        after = self.history()
        self.assertEqual(len(after["zpm"]), 3)
        with self.assertRaises(AssertionError):
            require_same_history(before, after)

    def test_unselected_foreign_rows_and_conflicting_scope_columns_fail_closed(self):
        self.lifecycle.execute("UPDATE managed_workers SET repository=? WHERE worker_id=?",
                               ("github:github.com/foreign/repository", "current-b"))
        self.lifecycle.commit()
        with self.assertRaises(AssertionError):
            self.history()
        self.lifecycle.execute("UPDATE managed_workers SET repository=? WHERE worker_id=?",
                               (self.repositories[1], "current-b"))
        self.lifecycle.commit()
        row = self.runtime.execute("SELECT spec FROM workers WHERE worker_id=?", ("current-b",)).fetchone()
        spec = json.loads(row[0])
        spec["deployment_id"] = "foreign"
        self.runtime.execute("UPDATE workers SET spec=? WHERE worker_id=?", (canonical(spec), "current-b"))
        self.runtime.commit()
        with self.assertRaises(AssertionError):
            self.history()

    def test_controller_binding_mismatch_and_missing_runtime_journal_cannot_look_empty(self):
        with sqlite3.connect(self.bundle / "state/zpm.sqlite3") as connection:
            row = connection.execute("SELECT launch_key,snapshot FROM workers WHERE repository=? LIMIT 1",
                                     (self.repositories[1],)).fetchone()
            snapshot = json.loads(row[1])
            snapshot["repository"] = self.repositories[0]
            connection.execute("UPDATE workers SET snapshot=? WHERE launch_key=?", (canonical(snapshot), row[0]))
        with self.assertRaises(AssertionError):
            self.history()
        with sqlite3.connect(self.bundle / "state/zpm.sqlite3") as connection:
            snapshot["repository"] = self.repositories[1]
            connection.execute("UPDATE workers SET snapshot=? WHERE launch_key=?", (canonical(snapshot), row[0]))
        self.runtime.close()
        self.runtime_path.unlink()
        with self.assertRaises(AssertionError):
            self.history()
        self.assertFalse(self.runtime_path.exists())


class SharedCleanupTests(unittest.TestCase):
    def test_restore_failure_still_closes_services(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source-a"
            source.mkdir()
            displaced = displace_source(source)
            close = Mock()
            shared = SimpleNamespace(members=[SimpleNamespace(source=source)],
                                     processes=SimpleNamespace(close=close))
            with patch.object(Path, "rename", side_effect=OSError("restore failed")):
                with self.assertRaisesRegex(OSError, "restore failed"):
                    cleanup_services(shared, displaced)
            close.assert_called_once_with()
            self.assertTrue(displaced.is_dir())
            self.assertFalse(source.exists())

    def test_failed_displacement_never_attempts_restoration(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source-a"
            source.mkdir()
            close = Mock()
            shared = SimpleNamespace(members=[SimpleNamespace(source=source)],
                                     processes=SimpleNamespace(close=close))
            displaced = None
            with patch.object(Path, "rename", side_effect=OSError("initial rename failed")):
                with self.assertRaisesRegex(OSError, "initial rename failed"):
                    displaced = displace_source(source)
            with patch("check_multi_repository_stack.restore_source") as restore:
                cleanup_services(shared, displaced)
            restore.assert_not_called()
            close.assert_called_once_with()
            self.assertTrue(source.is_dir())

    def test_existing_outage_path_is_never_adopted_as_a_displaced_source(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source-a"
            source.mkdir()
            foreign = source.with_name(source.name + "-unavailable")
            foreign.mkdir()
            marker = foreign / "retained"
            marker.write_text("keep")
            with patch.object(Path, "rename") as rename:
                with self.assertRaises(AssertionError):
                    displace_source(source)
            rename.assert_not_called()
            self.assertTrue(source.is_dir())
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
