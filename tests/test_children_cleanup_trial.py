"""Cleanup oracle fixtures, without Docker, authority or native cleanup claims."""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_cleanup as cleanup
    import check_managed_public_stack as public_trial
finally:
    sys.path.pop(0)


DEFAULT_PROGRESS = object()


class CleanupEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.bundle = Path(temporary.name) / "installation"
        (self.bundle / "config").mkdir(parents=True)
        self.config = {"state_directory": str(self.bundle / "state/openjules/runtime"),
                       "lifecycle_database": str(self.bundle / "state/openjules/lifecycle.sqlite3")}
        (self.bundle / "config/openjules.json").write_text(json.dumps(self.config))
        (self.bundle / "manifest.json").write_text(json.dumps({"configuration": {"fixture": True}}))
        self.paths = cleanup.journal_paths(self.bundle, self.config)
        self.worker, self.idle = "captured-worker", "idle-worker"
        self.scope = {"deployment_id": "cleanup-fixture", "repository": "github:github.com/fixture/cleanup"}
        self.spec = {"schema_version": 1, **self.scope, "slot_id": "slot", "generation": 1,
                     "launch_key": "original-launch", "profile": "approved"}
        self.original = {"container": "a" * 64, "volume": "original-volume", "labels": {"owned": "original"},
                         "volume_identity": {"Name": "original-volume", "CreatedAt": "original"}}
        self.non_target = {"container": "b" * 64, "volume": "idle-volume", "labels": {"owned": "idle"},
                           "volume_identity": {"Name": "idle-volume", "CreatedAt": "retained"}}
        self.inventory = {"containers": ["a" * 64, "b" * 64], "volumes": ["idle-volume", "original-volume"]}
        self.remaining = {"containers": ["b" * 64], "volumes": ["idle-volume"]}
        self.archive = self.paths["runtime"].parent / "captures" / (hashlib.sha256(self.worker.encode()).hexdigest() + ".tar")
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b"explicit archive-byte fixture")
        self.archive_id = {"sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(), "bytes": self.archive.stat().st_size}
        manifest = {"worker_id": self.worker, "container_id": self.original["container"],
                    "archive_sha256": self.archive_id["sha256"], "archive_bytes": self.archive_id["bytes"]}
        self.artifacts = {"patch": "private fixture patch", "log": "private fixture log", "manifest": json.dumps(manifest)}
        self.canonical_proof = {
            "binding": {"worker_id": self.worker, "attempt_id": "original-attempt"},
            "runtime_receipt": {"state": "exited", "termination_confirmed": True,
                                "launch_fenced": True, "artifacts": {"manifest": "original-root-hash"}},
            "child_lineage": [{"child_id": "original-child", "grant_digest": "original-grant",
                               "artifacts": {"manifest": "original-child-hash"}}],
        }
        # Independent literal witness, not manufactured by the oracle.
        self.progress = {
            "schema_version": 1, "worker_id": self.worker,
            "spec_sha256": hashlib.sha256(json.dumps(
                self.spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "container_id": self.original["container"], "volume_identity": self.original["volume_identity"],
            "archive_sha256": self.archive_id["sha256"], "canonical_proof": self.canonical_proof,
            "container_removed": True, "volume_removed": True, "completed": True,
        }
        self.progress_json = json.dumps(self.progress, sort_keys=True, separators=(",", ":"))
        self.receipt = {**self.artifacts, "termination_confirmed": True, "artifacts_captured": True,
                        "launch_fenced": True, "capture_path": str(self.archive), "capture_sha256": self.archive_id["sha256"]}
        for name, path in self.paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as database:
                if name == "runtime":
                    # Deliberately small oracle fixture, not a companion-schema witness.
                    database.execute("CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT,retired INTEGER,receipt TEXT,cleanup TEXT,container_id TEXT,volume_identity TEXT)")
                    database.execute("INSERT INTO workers VALUES(?,?,1,?,NULL,?,?)", (
                        self.worker, json.dumps(self.spec, sort_keys=True, separators=(",", ":")), json.dumps(self.receipt),
                        self.original["container"], json.dumps(self.original["volume_identity"])))
                    database.execute("INSERT INTO workers VALUES(?,?,1,?,NULL,?,?)", (
                        self.idle, "idle spec", "idle receipt", self.non_target["container"], json.dumps(self.non_target["volume_identity"])))
                elif name == "lifecycle":
                    database.execute("CREATE TABLE managed_workers(worker_id TEXT PRIMARY KEY,spec TEXT)")
                    database.execute("INSERT INTO managed_workers VALUES(?,?)", (self.worker, json.dumps(self.spec)))
                else:
                    database.execute("CREATE TABLE retained(name TEXT PRIMARY KEY,content TEXT)")
                    database.execute("INSERT INTO retained VALUES('original','private fixture journal value')")

    def update(self, name, sql, values=()):
        with sqlite3.connect(self.paths[name]) as database:
            database.execute(sql, values)

    def original_capture(self):
        return cleanup.original_capture(self.paths, self.worker, self.scope, self.artifacts)

    def test_exact_original_identity_and_capture_are_read_without_new_state(self):
        before = cleanup.retained_journals(self.paths, self.worker)
        original = self.original_capture()
        self.assertEqual(original["spec"], self.spec)
        self.assertEqual(original["archive_identity"], self.archive_id)
        self.assertEqual(original["volume_identity"], self.original["volume_identity"])
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)

    def test_changed_receipt_or_scope_cannot_qualify(self):
        for field, value in (("patch", "different"), ("capture_path", str(self.archive.parent / "other")),
                             ("termination_confirmed", False), ("capture_sha256", "f" * 64)):
            with self.subTest(field=field):
                receipt = self.receipt | {field: value}
                self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(receipt), self.worker))
                with self.assertRaises(AssertionError):
                    self.original_capture()
        self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(self.receipt), self.worker))
        self.update("lifecycle", "UPDATE managed_workers SET spec=?", (json.dumps(self.spec | {"deployment_id": "foreign"}),))
        with self.assertRaises(AssertionError):
            self.original_capture()

    def test_missing_corrupt_or_symlink_archive_cannot_qualify(self):
        saved = self.archive.with_suffix(".retained")
        self.archive.rename(saved)
        with self.assertRaises(OSError):
            self.original_capture()
        self.assertFalse(self.archive.exists())
        self.archive.symlink_to(saved)
        with self.assertRaises(OSError):
            self.original_capture()
        self.archive.unlink()
        self.archive.write_bytes(b"corrupt")
        with self.assertRaises(AssertionError):
            self.original_capture()
        self.assertEqual(saved.read_bytes(), b"explicit archive-byte fixture")

    def test_fifo_archive_refusal_is_bounded(self):
        self.archive.unlink()
        os.mkfifo(self.archive)
        errors = []

        def inspect():
            try:
                cleanup.archive_identity(self.archive)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=inspect, daemon=True)
        thread.start()
        thread.join(1)
        blocked = thread.is_alive()
        if blocked:
            # Release only this fixture FIFO if the nonblocking regression returns.
            fd = os.open(self.archive, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            thread.join(1)
        self.assertFalse(blocked, "archive open blocked before regular-file refusal")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AssertionError)

    def test_only_selected_cleanup_progress_may_change_in_journals(self):
        original = cleanup.retained_journals(self.paths, self.worker)
        self.update("runtime", "UPDATE workers SET cleanup='complete' WHERE worker_id=?", (self.worker,))
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), original)
        self.update("runtime", "UPDATE workers SET cleanup='changed' WHERE worker_id=?", (self.idle,))
        self.assertNotEqual(cleanup.retained_journals(self.paths, self.worker), original)
        self.update("runtime", "UPDATE workers SET cleanup=NULL WHERE worker_id=?", (self.idle,))
        self.update("runtime", "UPDATE workers SET receipt='changed' WHERE worker_id=?", (self.worker,))
        self.assertNotEqual(cleanup.retained_journals(self.paths, self.worker), original)
        self.update("runtime", "DELETE FROM workers WHERE worker_id=?", (self.worker,))
        with self.assertRaises(AssertionError):
            cleanup.retained_journals(self.paths, self.worker)

    def test_missing_journal_does_not_become_empty_success(self):
        self.paths["models"].unlink()
        with self.assertRaises(AssertionError):
            cleanup.retained_journals(self.paths, self.worker)
        self.assertFalse(self.paths["models"].exists())

    def test_target_absence_is_insufficient_when_idle_control_changed(self):
        witness = self.original_capture() | {"journals": cleanup.retained_journals(self.paths, self.worker),
            "remaining_inventory": self.remaining, "idle_worker": self.idle, "idle_resources": self.non_target,
            "cleanup_progress": self.progress_json}
        self.update("runtime", "UPDATE workers SET cleanup=? WHERE worker_id=?", (self.progress_json, self.worker))
        with patch.object(cleanup, "docker_inventory", return_value=self.remaining), patch.object(
                cleanup, "owned_resources", return_value=self.non_target | {"container": "c" * 64}):
            with self.assertRaises(AssertionError):
                cleanup.require_retained(self.paths, self.worker, witness)

    def test_resource_witness_requires_positive_owned_stopped_identity(self):
        labels = {"openjules.worker": self.worker, "openjules.managed": "1"}
        container = {"Id": "a" * 64, "Config": {"Labels": labels}, "State": {"Running": False},
                     "Mounts": [{"Destination": "/workspace", "Type": "volume", "Name": "original-volume"}]}
        volume = {"Name": "original-volume", "CreatedAt": "original", "Labels": labels}
        for identity_list, change in (("", None), ("a\nb\n", None), ("a" * 64, "running"), ("a" * 64, "foreign")):
            with self.subTest(change=change, inventory=identity_list):
                changed = deepcopy(container)
                if change == "running":
                    changed["State"]["Running"] = True
                if change == "foreign":
                    changed["Config"]["Labels"]["openjules.worker"] = "foreign"
                with patch.object(cleanup.subprocess, "check_output", side_effect=[identity_list, json.dumps([changed]), json.dumps([volume])]):
                    with self.assertRaises(AssertionError):
                        cleanup.owned_resources(self.worker)

    def exercise_phase(self, *, fail=None, cleanup_failure=False, stored_progress=DEFAULT_PROGRESS, manifest_evidence=None):
        events, requests, authority_reads = [], [], []
        processes = SimpleNamespace(running={})

        def start(name, command, cwd, environment):
            events.append(("start", name, command, cwd, environment))
            processes.running[name] = object()

        def stop(_processes, name):
            events.append(("stop", name))
            processes.running.pop(name, None)
            if cleanup_failure:
                raise RuntimeError("controlled reap failure")

        def canonical():
            authority_reads.append(True)
            if fail == "verification" and len(authority_reads) == 2:
                raise ValueError("controlled canonical failure")
            return True

        current_inventory = deepcopy(self.inventory)
        expected = {"schema_version": 1, **self.scope, "worker_id": self.worker, "state": "cleaned", "reason": "",
                    "container_removed": True, "volume_removed": True, "archives_retained": True}

        def run(command, **kwargs):
            requests.append((command, kwargs))
            if fail == "timeout":
                raise cleanup.subprocess.TimeoutExpired(command, 60)
            current_inventory.clear()
            current_inventory.update(deepcopy(self.remaining))
            progress = self.progress_json if stored_progress is DEFAULT_PROGRESS else stored_progress
            self.update("runtime", "UPDATE workers SET cleanup=? WHERE worker_id=?", (progress, self.worker))
            payload = expected if fail != "receipt" else expected | {"worker_id": "foreign"}
            if fail == "boolean":
                payload = expected | {"container_removed": 1}
            return SimpleNamespace(returncode=1 if fail == "cli" else 0, stdout=json.dumps(payload))

        processes.start = start
        with ExitStack() as stack:
            stack.enter_context(patch("zpm.deployment.verify", return_value={"valid": True}))
            stack.enter_context(patch("zpm.deployment._command", side_effect=lambda root, service, config: [service, "--original-config"]))
            stack.enter_context(patch("zpm.deployment._environment", side_effect=lambda root, service, config: {"PRIVATE_TOKEN": "fixture-only"}))
            stack.enter_context(patch.object(cleanup, "owned_resources", side_effect=lambda worker: self.original if worker == self.worker else self.non_target))
            stack.enter_context(patch.object(cleanup, "docker_inventory", side_effect=lambda: deepcopy(current_inventory)))
            stack.enter_context(patch.object(cleanup, "eventually", side_effect=lambda callback, *args: callback()))
            stack.enter_context(patch.object(cleanup, "stop_owned", side_effect=stop))
            stack.enter_context(patch.object(cleanup.subprocess, "run", side_effect=run))
            try:
                result = cleanup.qualify_owned_cleanup(self.bundle, {"polyphony": "original-polyphony", "openjules": "original-openjules"},
                    self.scope, self.worker, self.idle, {}, processes, self.artifacts, canonical,
                    canonical_proof=self.canonical_proof, **(manifest_evidence or {}))
            finally:
                self.assertEqual(events[-1], ("stop", "cleanup-authority"))
                self.assertEqual(processes.running, {})
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][0], requests[1][0])
        self.assertEqual(requests[0][0][-6:], ["--cleanup-worker", self.worker, "--deployment-id", self.scope["deployment_id"], "--repository", self.scope["repository"]])
        self.assertEqual(result["spec"], self.spec)
        report = json.dumps(result["report"])
        for private in ("fixture-only", "private fixture", str(self.bundle)):
            self.assertNotIn(private, report)
        return result

    def test_successful_cli_replay_keeps_exact_commands_and_exports_only_aggregate(self):
        self.exercise_phase()

    def test_cli_or_capture_verification_failure_still_stops_owned_authority(self):
        for failure in ("cli", "receipt", "boolean", "timeout", "verification"):
            self.update("runtime", "UPDATE workers SET cleanup=NULL WHERE worker_id=?", (self.worker,))
            with self.subTest(failure=failure), self.assertRaises((AssertionError, ValueError, cleanup.subprocess.TimeoutExpired)):
                self.exercise_phase(fail=failure)

    def test_successful_cli_cannot_hide_missing_or_changed_durable_progress(self):
        mutations = {
            "foreign worker": self.progress | {"worker_id": self.idle},
            "wrong spec": self.progress | {"spec_sha256": "f" * 64},
            "wrong resource": self.progress | {"container_id": self.non_target["container"]},
            "wrong archive": self.progress | {"archive_sha256": "f" * 64},
            "foreign canonical binding": self.progress | {
                "canonical_proof": self.canonical_proof | {"binding": {"worker_id": self.idle}}},
            "incomplete": self.progress | {"completed": False},
            "boolean alias": self.progress | {"container_removed": 1},
            "unexpected field": self.progress | {"other": True},
        }
        cases = {"absent": None, "malformed": "{", "oversized": "x" * (cleanup.MAX_PROGRESS_BYTES + 1)}
        cases.update({name: json.dumps(value, sort_keys=True, separators=(",", ":"))
                      for name, value in mutations.items()})
        for name, progress in cases.items():
            with self.subTest(name=name):
                self.update("runtime", "UPDATE workers SET cleanup=NULL WHERE worker_id=?", (self.worker,))
                with self.assertRaisesRegex(AssertionError, "exact completed original proof"):
                    self.exercise_phase(stored_progress=progress)

    def test_later_progress_change_cannot_hide_behind_normalized_history(self):
        witness = self.exercise_phase()
        with patch.object(cleanup, "docker_inventory", return_value=self.remaining), patch.object(
                cleanup, "owned_resources", return_value=self.non_target):
            cleanup.require_retained(self.paths, self.worker, witness)
            self.update("runtime", "UPDATE workers SET cleanup=NULL WHERE worker_id=?", (self.worker,))
            with self.assertRaisesRegex(AssertionError, "exact completed original proof"):
                cleanup.require_retained(self.paths, self.worker, witness)

    def test_reap_failure_does_not_mask_original_verification_failure(self):
        with self.assertRaisesRegex(ValueError, "controlled canonical failure") as caught:
            self.exercise_phase(fail="verification", cleanup_failure=True)
        self.assertEqual(caught.exception.__notes__, ["cleanup could not reap its temporary owned authority"])


    def public_capture(self):
        # Literal projection fixture. The installed public reader separately
        # derives and validates this shape from the actual frozen six tables.
        extension = {
            "schema_version": 1, "protocol": "managed-public-pull-v1",
            "topic": "cleanup-public-fixture", "snapshot_sha256": "c" * 64,
            "actors": [{"role": "root", "binding_digest": "d" * 64,
                        "status": "closed", "admission_closed": True}],
            "slots": [], "records": [], "counts": {"actors": 1, "slots": 0},
        }
        original = json.dumps(json.loads(self.receipt["manifest"]), sort_keys=True, separators=(",", ":"))
        self.receipt = self.receipt | {"manifest": original}
        # Keep an actual connection open so these committed rows remain in WAL.
        connection = sqlite3.connect(self.paths["runtime"])
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        connection.execute("UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(self.receipt), self.worker))
        connection.commit()
        self.assertGreater(Path(str(self.paths["runtime"]) + "-wal").stat().st_size, 0)
        effective = json.loads(original) | {
            "managed_public_v1": extension,
            "managed_public_original_manifest_sha256": hashlib.sha256(original.encode()).hexdigest(),
        }
        self.artifacts = self.artifacts | {
            "manifest": json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False),
        }
        return original, extension

    def test_public_extension_joins_retained_original_bytes_and_cleanup_replay(self):
        ordinary = self.original_capture()
        self.assertNotIn("runtime_manifest", ordinary)
        original, extension = self.public_capture()
        before = cleanup.retained_journals(self.paths, self.worker)
        # Omission deliberately retains the original strict equality path.
        with self.assertRaisesRegex(AssertionError, "local execution receipt differs"):
            self.original_capture()
        captured = cleanup.original_capture(
            self.paths, self.worker, self.scope, self.artifacts, public_extension=extension,
        )
        self.assertEqual(captured["runtime_manifest"], original)
        self.assertEqual({key: value for key, value in captured.items() if key != "runtime_manifest"}, ordinary)
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)
        witness = self.exercise_phase(manifest_evidence={
            "runtime_manifest": captured["runtime_manifest"], "public_extension": extension,
        })
        self.assertEqual(witness["runtime_manifest"], original)
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)
        with sqlite3.connect(self.paths["runtime"]) as database:
            receipt = json.loads(database.execute(
                "SELECT receipt FROM workers WHERE worker_id=?", (self.worker,),
            ).fetchone()[0])
        self.assertEqual(receipt, self.receipt)
        self.assertEqual(receipt["manifest"], original)
        self.assertNotEqual(receipt["manifest"], self.artifacts["manifest"])
        self.assertNotIn("runtime_manifest", witness["report"])
        self.assertEqual(witness["report"]["retained_archive"], self.archive_id)
        self.assertIs(witness["report"]["canonical_artifacts_unchanged"], True)

    def test_public_join_rejects_changed_original_hash_fields_and_extension(self):
        original, extension = self.public_capture()
        effective = json.loads(self.artifacts["manifest"])
        altered = {
            "wrong original byte hash": effective | {"managed_public_original_manifest_sha256": "f" * 64},
            "extra ordinary field": effective | {"caller_override": True},
            "changed ordinary field": effective | {"archive_bytes": self.archive_id["bytes"] + 1},
            "missing ordinary field": {key: value for key, value in effective.items() if key != "container_id"},
            "wrong frozen extension": effective | {"managed_public_v1": extension | {"snapshot_sha256": "e" * 64}},
            "boolean integer alias": effective | {"managed_public_v1": extension | {"schema_version": True}},
            "extra nested extension field": effective | {"managed_public_v1": extension | {"caller_override": True}},
        }
        before = cleanup.retained_journals(self.paths, self.worker)
        for name, value in altered.items():
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "exact original and frozen extension"):
                cleanup.original_capture(
                    self.paths, self.worker, self.scope,
                    self.artifacts | {"manifest": json.dumps(value, sort_keys=True, separators=(",", ":"))},
                    runtime_manifest=original, public_extension=extension,
                )
            self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)
        # A caller-supplied replacement cannot substitute for the actual row.
        changed = json.dumps(json.loads(original) | {"caller_override": True},
                             sort_keys=True, separators=(",", ":"))
        coherent = json.loads(changed) | {
            "managed_public_v1": extension,
            "managed_public_original_manifest_sha256": hashlib.sha256(changed.encode()).hexdigest(),
        }
        with self.assertRaisesRegex(AssertionError, "retained original manifest changed"):
            cleanup.original_capture(
                self.paths, self.worker, self.scope,
                self.artifacts | {"manifest": json.dumps(coherent, sort_keys=True, separators=(",", ":"))},
                runtime_manifest=changed, public_extension=extension,
            )
        # A later actual row rewrite cannot replace the original frozen bytes,
        # even when both that row and the proposed effective manifest agree.
        replacement = self.receipt | {"manifest": changed}
        self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(replacement), self.worker))
        rewritten = cleanup.retained_journals(self.paths, self.worker)
        with self.assertRaisesRegex(AssertionError, "retained original manifest changed"):
            cleanup.original_capture(
                self.paths, self.worker, self.scope,
                self.artifacts | {"manifest": json.dumps(coherent, sort_keys=True, separators=(",", ":"))},
                runtime_manifest=original, public_extension=extension,
            )
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), rewritten)
        self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(self.receipt), self.worker))
        self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)

    def test_public_join_requires_bounded_unambiguous_original_and_exact_effective_bytes(self):
        original, extension = self.public_capture()
        cases = {
            "duplicate original field": '{"worker_id":"foreign","worker_id":"captured-worker"}',
            "duplicate nested field": '{"nested":{"x":1,"x":2}}',
            "nonfinite original": '{"x":NaN}',
            "overflowed original": '{"x":1e999}',
            "raw surrogate": '{"x":"\ud800"}',
            "not an object": "[]",
            "oversized original": " " * (cleanup.MAX_PUBLIC_MANIFEST_BYTES + 1),
            "noncanonical original": original + "\n",
            "already decorated extension": json.dumps(json.loads(original) | {"managed_public_v1": None},
                                                      sort_keys=True, separators=(",", ":")),
            "already decorated hash": json.dumps(
                json.loads(original) | {"managed_public_original_manifest_sha256": "0" * 64},
                sort_keys=True, separators=(",", ":")),
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                replacement = self.receipt | {"manifest": value}
                self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(replacement), self.worker))
                before = cleanup.retained_journals(self.paths, self.worker)
                with self.assertRaises(AssertionError):
                    cleanup.original_capture(
                        self.paths, self.worker, self.scope, self.artifacts, public_extension=extension,
                    )
                self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)
        self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(self.receipt), self.worker))
        for value in (self.artifacts["manifest"] + "\n", json.dumps(json.loads(self.artifacts["manifest"]), indent=2)):
            with self.subTest(effective=value[:30]), self.assertRaisesRegex(AssertionError, "exact original and frozen extension"):
                cleanup.original_capture(
                    self.paths, self.worker, self.scope, self.artifacts | {"manifest": value},
                    runtime_manifest=original, public_extension=extension,
                )
        self.assertEqual(cleanup.original_capture(
            self.paths, self.worker, self.scope, self.artifacts,
            runtime_manifest=original, public_extension=extension,
        )["runtime_manifest"], original)

    def test_public_join_keeps_patch_log_archive_and_original_scope_checks(self):
        original, extension = self.public_capture()
        for key, value in (
            ("patch", "rewritten private patch"), ("log", "rewritten private log"),
            ("termination_confirmed", 1), ("artifacts_captured", False), ("launch_fenced", False),
            ("capture_path", str(self.archive.parent / "foreign.tar")), ("capture_sha256", "f" * 64),
        ):
            with self.subTest(key=key):
                self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?",
                            (json.dumps(self.receipt | {key: value}), self.worker))
                with self.assertRaises(AssertionError):
                    cleanup.original_capture(
                        self.paths, self.worker, self.scope, self.artifacts,
                        runtime_manifest=original, public_extension=extension,
                    )
        self.update("runtime", "UPDATE workers SET receipt=? WHERE worker_id=?", (json.dumps(self.receipt), self.worker))
        with self.assertRaisesRegex(AssertionError, "scope or schema"):
            cleanup.original_capture(
                self.paths, self.worker, self.scope | {"deployment_id": "foreign"}, self.artifacts,
                runtime_manifest=original, public_extension=extension,
            )
        saved = self.archive.read_bytes()
        self.archive.write_bytes(b"changed retained archive")
        with self.assertRaisesRegex(AssertionError, "archive differs"):
            cleanup.original_capture(
                self.paths, self.worker, self.scope, self.artifacts,
                runtime_manifest=original, public_extension=extension,
            )
        self.archive.write_bytes(saved)
        self.assertEqual(cleanup.original_capture(
            self.paths, self.worker, self.scope, self.artifacts,
            runtime_manifest=original, public_extension=extension,
        )["archive_identity"], self.archive_id)

    def test_public_cleanup_refuses_incomplete_or_changed_pair_before_owned_effects(self):
        original, extension = self.public_capture()
        cases = (
            {"runtime_manifest": original},
            {"public_extension": extension},
            {"runtime_manifest": original + "\n", "public_extension": extension},
            {"runtime_manifest": original, "public_extension": extension | {"snapshot_sha256": "e" * 64}},
        )
        for evidence in cases:
            with self.subTest(fields=sorted(evidence)), patch("zpm.deployment.verify", return_value={"valid": True}), patch.object(
                    cleanup, "owned_resources", side_effect=AssertionError("unexpected Docker observation")) as resources, patch.object(
                    cleanup.subprocess, "run", side_effect=AssertionError("unexpected installed CLI")) as execute, patch.object(
                    cleanup, "eventually", side_effect=AssertionError("unexpected canonical service")) as authority:
                processes = SimpleNamespace(running={})
                before = cleanup.retained_journals(self.paths, self.worker)
                with self.assertRaisesRegex(AssertionError, "cleanup (public|effective|retained)"):
                    cleanup.qualify_owned_cleanup(
                        self.bundle, {}, self.scope, self.worker, self.idle, {}, processes, self.artifacts,
                        lambda: self.fail("unexpected canonical callback"), canonical_proof=self.canonical_proof,
                        **evidence,
                    )
                resources.assert_not_called()
                execute.assert_not_called()
                authority.assert_not_called()
                self.assertEqual(cleanup.retained_journals(self.paths, self.worker), before)

    def test_public_archive_phase_reuses_frozen_original_bytes_before_commands(self):
        children = {"left": "child-left", "right": "child-right"}
        entries = {
            "workspace/run/repo/left.txt": (b"left implemented\n", 65532),
            "workspace/run/repo/right.txt": (b"right implemented\n", 65532),
        }
        for index, (actor, child_id) in enumerate(children.items()):
            prefix = "workspace/children/" + child_id + "/run/repo/"
            entries[prefix + ".git/HEAD"] = (b"original-head\n", 60000 + index)
            for name in ("left", "right"):
                entries[prefix + name + ".txt"] = (
                    (name + " implemented\n").encode() if actor == name else b"old\n", 60000 + index,
                )
        with tarfile.open(self.archive, "w") as archive:
            for name, (content, uid) in entries.items():
                item = tarfile.TarInfo(name)
                item.size, item.uid = len(content), uid
                archive.addfile(item, io.BytesIO(content))
        self.archive_id = {"sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                           "bytes": self.archive.stat().st_size}
        manifest = json.loads(self.receipt["manifest"]) | {
            "archive_sha256": self.archive_id["sha256"], "archive_bytes": self.archive_id["bytes"],
        }
        self.receipt = self.receipt | {"manifest": json.dumps(manifest), "capture_sha256": self.archive_id["sha256"]}
        original, extension = self.public_capture()
        root = self.bundle.parent / "public-archive-fixture"
        (root / "source").mkdir(parents=True)
        for name in ("left", "right"):
            (root / "source" / (name + ".txt")).write_bytes(b"old\n")
        context = {"root": root, "scope": self.scope, "retained_paths": self.paths,
                   "processes": SimpleNamespace(running={})}
        captured = {
            "artifacts": self.artifacts, "manifest": json.loads(self.artifacts["manifest"]),
            "original_runtime_manifest": original,
            "attempt": {"captured_artifacts": {"patch": hashlib.sha256(self.artifacts["patch"].encode()).hexdigest()}},
        }
        peer = SimpleNamespace(checkouts={"zpm": root})
        # Command observers deliberately do not claim Git, guest or Docker work.
        with patch.object(public_trial, "command", return_value=b"") as commands:
            result = public_trial.archive_and_patch_evidence(
                context, SimpleNamespace(child_ids=children), {"worker_id": self.worker}, captured, object(), peer,
            )
            self.assertEqual([call.args[1] for call in commands.call_args_list], [
                "managed-public-artifact-clone", "managed-public-artifact-check",
                "managed-public-artifact-apply", "managed-public-artifact-test",
            ])
            self.assertEqual(result["archive_sha256"], self.archive_id["sha256"])
            self.assertEqual((root / "captured-root.patch").read_text(), self.artifacts["patch"])
            commands.reset_mock()
            with self.assertRaisesRegex(AssertionError, "retained original manifest changed"):
                public_trial.archive_and_patch_evidence(
                    context, SimpleNamespace(child_ids=children), {"worker_id": self.worker},
                    captured | {"original_runtime_manifest": original + "\n"}, object(), peer,
                )
            commands.assert_not_called()
        self.assertEqual(cleanup.original_capture(
            self.paths, self.worker, self.scope, self.artifacts,
            runtime_manifest=original, public_extension=extension,
        )["archive_identity"], self.archive_id)


if __name__ == "__main__":
    unittest.main()
