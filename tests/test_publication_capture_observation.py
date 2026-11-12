"""Small private fixtures for passive diagnostics; no installed execution claim."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

CONTRACTS = Path(__file__).parent / "contracts"
sys.path.insert(0, str(CONTRACTS))
import check_publication_stack as trial
import publication_capture_observation as observation

SECRET = "PRIVATE_PARAMETER_TRACEBACK_AND_PATH"
METHODS = ("test_one", "test_two", "test_three", "test_four")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class CaptureFixture:
    def __init__(self, root, methods=METHODS):
        self.root = root
        self.source = root / "approved"
        (self.source / "tests").mkdir(parents=True)
        self.test_file = self.source / "tests/test_owned.py"
        self.test_file.write_text("raise AssertionError('this source must never be imported')\n"
                                  "class OwnedTests:\n" + "".join("    def " + name + "(self): pass\n" for name in methods))
        for arguments in (["init", "-q"], ["add", "tests/test_owned.py"],
                          ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(self.source), *arguments], check=True, capture_output=True, timeout=5)
        self.base = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        self.ids = ["tests.test_owned.OwnedTests." + name for name in methods]
        self.scope = {"deployment_id": "publication-fixture", "repository": "github:github.com/example/source"}
        self.worker, self.attempt, self.task = "1" * 32, "original-attempt", "original-task"
        self.state = root / "state"
        self.directory = self.state / "runtime"
        (self.directory / "captures").mkdir(parents=True)
        self.archive = self.directory / "captures" / (hashlib.sha256(self.worker.encode()).hexdigest() + ".tar")
        self.config = {"state_directory": str(self.state), "profiles": [{
            **self.scope, "name": "approved", "base_commit": self.base, "source_path": str(self.source),
            "image": "sha256:" + "a" * 64, "command": ["python3", "-c", "pass"], "uid": 65532,
            "memory_bytes": 536870912, "cpu_quota": 100000, "pids_limit": 128, "max_seconds": 240,
            "max_archive_bytes": 4_000_000, "environment_refs": {},
        }]}
        projected = self.config["profiles"][0] | {"environment_refs": []}
        self.fingerprint = hashlib.sha256(canonical(projected).encode()).hexdigest()
        self.spec = {**self.scope, "profile": "approved"}
        self.output = (json.dumps({"event": "configured_verification", "state": "exited", "exit_code": 1,
                                  "output": SECRET}) + "\n").encode()
        self.suite = ("test_one (" + self.ids[0] + ") ... ERROR\nERROR: test_one (" + self.ids[0] + ")\n"
                      + SECRET + "\nRan 4 tests in 0.010s\n\nFAILED (errors=1)\n").encode()
        self.exit_code = 3
        self.write_capture()

    def write_capture(self, extra=()):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, value in (("workspace/run/output.log", self.output),
                                ("workspace/run/publication-unittest.log", self.suite)):
                if value is not None:
                    member = tarfile.TarInfo(name)
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
            for member, content in extra:
                archive.addfile(member, io.BytesIO(content) if content is not None else None)
        raw = stream.getvalue()
        self.archive.write_bytes(raw)
        self.manifest = {"worker_id": self.worker, "attempt_id": self.attempt, "base_commit": self.base,
                         "profile": "approved", "profile_fingerprint": self.fingerprint, "container_id": "original-container",
                         "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_bytes": len(raw),
                         "termination_confirmed": True, "launch_fenced": True, "log_present": self.output is not None}
        self.receipt = {"state": "completed", "exit_code": self.exit_code, "termination_confirmed": True,
                        "launch_fenced": True, "artifacts_captured": True, "capture_path": str(self.archive),
                        "capture_sha256": self.manifest["archive_sha256"], "manifest": canonical(self.manifest),
                        "patch": SECRET, "log": (self.output or b"").decode("utf-8", errors="replace")}
        self.save_rows()
        self.snapshot = {**self.scope, "task": [{"id": self.task, "status": "running"}], "candidate": [],
                         "attempt": [{"id": self.attempt, "task": self.task, "worker": "canonical-worker",
                                      "runtime_binding": {**self.scope, "worker_id": self.worker,
                                                          "attempt_id": self.attempt, "base": self.base},
                                      "state": "exited", "termination_confirmed": True, "launch_fenced": True,
                                      "captured_artifacts": {"patch": "c" * 64, "log": "d" * 64,
                                                             "manifest": hashlib.sha256(self.receipt["manifest"].encode()).hexdigest()}}]}

    def save_rows(self):
        with sqlite3.connect(self.directory / "journal.sqlite3") as database:
            database.executescript("CREATE TABLE IF NOT EXISTS workers(worker_id TEXT PRIMARY KEY,spec TEXT,"
                                   "profile_fingerprint TEXT,container_id TEXT,receipt TEXT);"
                                   "CREATE TABLE IF NOT EXISTS attempts(worker_id TEXT PRIMARY KEY,attempt_id TEXT,exit_code INTEGER);")
            database.execute("INSERT OR REPLACE INTO workers VALUES(?,?,?,?,?)", (
                self.worker, canonical(self.spec), self.fingerprint, "original-container", canonical(self.receipt)))
            database.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?)", (self.worker, self.attempt, self.exit_code))

    def observer(self):
        result = observation.RootCaptureObservation()
        result.bind(config=self.config, scope=self.scope, task=self.task, worker=self.worker,
                    attempt=self.attempt, base=self.base, source=self.source)
        result.observe_snapshot(self.snapshot)
        return result


class PublicationCaptureObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = CaptureFixture(self.root)

    def test_fixed_reasons_never_stringify_unknown_or_subclass_errors(self):
        for text, category in observation.CAPTURE_REASONS.items():
            self.assertEqual(observation.capture_reason(AssertionError(text)), category)

        class PrivateAssertion(AssertionError):
            def __str__(self):
                raise RuntimeError(SECRET)

        for error in (PrivateAssertion(next(iter(observation.CAPTURE_REASONS))), AssertionError(SECRET),
                      AssertionError(SECRET, SECRET), RuntimeError(SECRET)):
            self.assertEqual(observation.capture_reason(error), "other")

    def test_invalid_canonical_collections_remain_unavailable_not_absent(self):
        for kind, field in (("task", "original_task_present"), ("attempt", "original_attempt_present"),
                            ("candidate", "candidate_present")):
            for invalid in (None, {}, [{}, {}, {}], [{}]):
                with self.subTest(kind=kind, invalid=invalid):
                    value = deepcopy(self.fixture.snapshot)
                    value[kind] = invalid
                    summary, _ = observation.canonical_capture_observation(
                        value, self.fixture.scope, self.fixture.task, self.fixture.worker, self.fixture.attempt, self.fixture.base)
                    self.assertFalse(summary["valid"])
                    self.assertIsNone(summary.get(field))
                    if kind in {"task", "candidate"}:
                        self.assertIsNone(summary.get("current_candidate_matches"))
        summary = self.fixture.observer()._snapshot
        self.assertIs(summary["candidate_present"], False)
        self.assertIs(summary["current_candidate_matches"], False)

    def test_latest_foreign_or_malformed_read_replaces_prior_cache(self):
        observer = self.fixture.observer()
        self.assertTrue(observer._snapshot["runtime_binding_matches"])
        foreign = deepcopy(self.fixture.snapshot)
        foreign["repository"] = SECRET
        observer.observe_snapshot(foreign)
        self.assertFalse(observer._snapshot["scope_matches"])
        self.assertIsNone(observer._manifest_digest)
        observer.observe_snapshot(SECRET)
        self.assertFalse(observer._snapshot["valid"])
        self.assertIsNone(observer._manifest_digest)
        self.assertNotIn(SECRET, json.dumps(observer._snapshot))

    def test_binding_and_cache_add_no_io_and_harness_preserves_result(self):
        observer = observation.RootCaptureObservation()
        with patch.object(observation.sqlite3, "connect", side_effect=AssertionError("unexpected I/O")), \
                patch.object(observation.subprocess, "run", side_effect=AssertionError("unexpected process")), \
                patch.object(observation.os, "open", side_effect=AssertionError("unexpected file")):
            observer.bind(config=self.fixture.config, scope=self.fixture.scope, task=self.fixture.task,
                          worker=self.fixture.worker, attempt=self.fixture.attempt, base=self.fixture.base,
                          source=self.fixture.source)
            observer.observe_snapshot(self.fixture.snapshot)
        wrapper = trial.ExecutionFailureObservation()
        wrapper.root_capture = Mock()
        value = {"original": object()}
        wrapper.operation = "root-termination"
        self.assertIs(wrapper.observe_capture_snapshot(value), value)
        wrapper.root_capture.observe_snapshot.assert_not_called()
        wrapper.operation = "root-capture"
        self.assertIs(wrapper.observe_capture_snapshot(value), value)
        wrapper.root_capture.observe_snapshot.assert_called_once_with(value)
        wrapper.root_capture.observe_snapshot.side_effect = RuntimeError(SECRET)
        self.assertIs(wrapper.observe_capture_snapshot(value), value)
        wrapper.root_capture.bind.side_effect = RuntimeError(SECRET)
        wrapper.bind_capture(config=object())

    def test_first_capture_failure_preserves_exception_and_freezes_before_cleanup(self):
        wrapper = trial.ExecutionFailureObservation()
        wrapper.operation = "root-capture"
        wrapper.root_capture = self.fixture.observer()
        original = AssertionError("deadline waiting for candidate publication publication final candidate")
        with patch.object(observation, "runtime_capture_observation", wraps=observation.runtime_capture_observation) as inspect:
            with self.assertRaises(AssertionError) as caught:
                with wrapper.record():
                    try:
                        with wrapper.record():
                            raise original
                    finally:
                        wrapper.capture(RuntimeError(SECRET), before_cleanup=False)
            self.assertIs(caught.exception, original)
            self.assertEqual(inspect.call_count, 1)
        first = wrapper.report()
        self.assertEqual(first["root_capture"]["reason"], "candidate-publication-deadline")
        first["root_capture"]["canonical"].clear()
        self.assertTrue(wrapper.report()["root_capture"]["canonical"])
        self.assertNotIn(SECRET, json.dumps(wrapper.report()))

    def test_inspection_failure_cannot_replace_original_exception(self):
        wrapper = trial.ExecutionFailureObservation()
        wrapper.operation = "root-capture"
        wrapper.root_capture.failure = Mock(side_effect=KeyboardInterrupt(SECRET))
        original = AssertionError("canonical capture scope changed")
        with self.assertRaises(AssertionError) as caught:
            with wrapper.record():
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(wrapper.report()["root_capture"], {"available": False})

    def test_original_bound_archive_identifies_failed_suite_without_private_text(self):
        before = (self.fixture.directory / "journal.sqlite3").read_bytes()
        result = self.fixture.observer().failure(AssertionError("canonical capture is incomplete or malformed"))
        runtime = result["runtime"]
        self.assertTrue(runtime["available"])
        self.assertEqual(runtime["receipt_exit_code"], {"kind": "integer", "value": 3})
        archive = runtime["archive"]
        self.assertTrue(archive["identity_and_hash_verified"])
        self.assertTrue(archive["canonical_manifest_bound"])
        self.assertEqual(archive["output_log"]["details"][0]["exit_code"]["value"], 1)
        self.assertEqual(archive["suite_log"]["result"], {"state": "failed", "counts": {"errors": 1}})
        self.assertEqual(archive["suite_log"]["known_failures"], [{"test_id": self.fixture.ids[0], "result": "ERROR"}])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(before, (self.fixture.directory / "journal.sqlite3").read_bytes())

    def test_partial_suite_reports_progress_without_completion_or_timeout_inference(self):
        fixture = self.fixture
        fixture.exit_code = None
        fixture.output = b'{"event":"agent_finished"}\n'
        fixture.suite = ("\n".join(name + " (" + identity + ") ... ok" for name, identity in
                                   zip(METHODS[:3], fixture.ids[:3])) + "\ntest_four (" + fixture.ids[3] + ") ... ").encode()
        fixture.write_capture()
        result = fixture.observer().failure(AssertionError("deadline waiting for candidate publication publication final candidate"))
        runtime = result["runtime"]
        self.assertEqual(runtime["receipt_state"], "completed")
        self.assertEqual(runtime["receipt_exit_code"], {"kind": "null", "value": None})
        suite = runtime["archive"]["suite_log"]
        self.assertEqual(suite["observed_progress_count"], 4)
        self.assertEqual(suite["observed_completed_progress_count"], 3)
        self.assertEqual(suite["last_progress_id"], fixture.ids[3])
        self.assertEqual(suite["last_progress_status"], "incomplete")
        self.assertFalse(suite["complete_summary_observed"])
        self.assertIsNone(suite["result"])
        self.assertNotIn("timed_out", json.dumps(result))

    def test_subtests_keep_only_approved_identity_and_unknown_ids_are_redacted(self):
        ids, _ = observation.approved_test_ids(self.fixture.source, self.fixture.base)
        suite = ("FAIL: test_one (" + self.fixture.ids[0] + ") (value='" + SECRET + "')\n"
                 "ERROR: test_private (tests." + SECRET + ".test_private)\n").encode()
        result = observation.verification_log_observation(None, suite, ids)["suite_log"]
        self.assertEqual(result["known_failures"], [{"test_id": self.fixture.ids[0], "result": "FAIL"}])
        self.assertEqual(result["unknown_failure_records"], 1)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_approved_inventory_is_static_and_rejects_changed_source_bytes(self):
        ids, status = observation.approved_test_ids(self.fixture.source, self.fixture.base)
        self.assertTrue(status["available"])
        self.assertEqual(ids, frozenset(self.fixture.ids))
        self.fixture.test_file.write_text("raise AssertionError('" + SECRET + "')\nclass Injected:\n    def test_private(self): pass\n")
        ids, status = observation.approved_test_ids(self.fixture.source, self.fixture.base)
        self.assertIsNone(ids)
        self.assertEqual(status, {"available": False, "reason": "source-blob-mismatch"})
        self.assertNotIn(SECRET, json.dumps(status))

    def test_untracked_source_and_ancestor_git_are_not_an_allowlist(self):
        (self.fixture.source / "tests/test_untracked.py").write_text("class Injected:\n    def test_private(self): pass\n")
        ids, status = observation.approved_test_ids(self.fixture.source, self.fixture.base)
        self.assertEqual(ids, frozenset(self.fixture.ids))
        exported = self.fixture.source / "exported"
        (exported / "tests").mkdir(parents=True)
        shutil.copyfile(self.fixture.test_file, exported / "tests/test_owned.py")
        with patch.object(observation.subprocess, "run", side_effect=AssertionError("ancestor Git lookup")):
            ids, status = observation.approved_test_ids(exported, self.fixture.base)
        self.assertIsNone(ids)
        self.assertFalse(status["available"])

    def test_original_identity_canonical_hash_and_archive_bytes_are_required(self):
        fixture = self.fixture
        original = deepcopy(fixture.receipt)
        for field in ("worker_id", "attempt_id", "base_commit", "profile", "profile_fingerprint", "container_id"):
            with self.subTest(field=field):
                changed = deepcopy(fixture.manifest)
                changed[field] = SECRET
                fixture.receipt = original | {"manifest": canonical(changed)}
                fixture.save_rows()
                self.assertFalse(fixture.observer().failure(AssertionError())["runtime"]["archive"]["available"])
        fixture.receipt = original
        fixture.save_rows()
        observer = fixture.observer()
        observer._manifest_digest = "e" * 64
        self.assertEqual(observer.failure(AssertionError())["runtime"]["archive"]["reason"], "canonical-manifest")
        fixture.archive.write_bytes(fixture.archive.read_bytes() + b"changed")
        self.assertEqual(fixture.observer().failure(AssertionError())["runtime"]["archive"]["reason"], "archive-hash")

    def test_unsafe_duplicate_and_link_archive_members_are_not_followed(self):
        for name, kind in (("workspace/../" + SECRET, tarfile.REGTYPE),
                           ("workspace/run/output.log", tarfile.REGTYPE),
                           ("workspace/run/publication-unittest.log", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, SECRET
                self.fixture.write_capture([(member, b"" if kind == tarfile.REGTYPE else None)])
                result = self.fixture.observer().failure(AssertionError())
                self.assertFalse(result["runtime"]["archive"]["available"])
                self.assertNotIn(SECRET, json.dumps(result))

    def test_symlink_archive_and_symlink_source_are_unavailable(self):
        target = self.root / "retained"
        self.fixture.archive.rename(target)
        self.fixture.archive.symlink_to(target)
        result = self.fixture.observer().failure(AssertionError())
        self.assertFalse(result["runtime"]["archive"]["available"])
        source_target = self.root / "source-copy"
        self.fixture.test_file.rename(source_target)
        self.fixture.test_file.symlink_to(source_target)
        ids, status = observation.approved_test_ids(self.fixture.source, self.fixture.base)
        self.assertIsNone(ids)
        self.assertFalse(status["available"])

    def test_receipt_is_bounded_before_json_parsing_and_busy_journal_is_unavailable(self):
        database_path = self.fixture.directory / "journal.sqlite3"
        with sqlite3.connect(database_path) as database:
            database.execute("UPDATE workers SET receipt=?", (SECRET * (observation.MAX_JSON // len(SECRET) + 1),))
        result = self.fixture.observer().failure(AssertionError())
        self.assertEqual(result["runtime"]["archive"]["reason"], "receipt-limit")
        self.assertNotIn(SECRET, json.dumps(result))
        self.fixture.save_rows()
        with sqlite3.connect(database_path) as database:
            database.execute("BEGIN EXCLUSIVE")
            result = self.fixture.observer().failure(AssertionError())
            self.assertFalse(result["runtime"]["available"])

    def test_absent_and_empty_logs_are_distinct_and_nullable_codes_are_not_coerced(self):
        absent = observation.verification_log_observation(None, None, None)
        empty = observation.verification_log_observation(b"", b"", None)
        self.assertEqual(absent["suite_log"], {"available": True, "present": False})
        self.assertTrue(empty["suite_log"]["present"])
        self.assertIsNone(empty["suite_log"]["observed_progress_count"])
        output = json.dumps({"event": "configured_verification", "state": SECRET, "exit_code": True,
                             "output": 'File "<string>", line 1, in <module>\n' + SECRET}).encode()
        result = observation.verification_log_observation(output, None, None)
        details = result["output_log"]["details"][0]
        self.assertEqual(details["state"], "other")
        self.assertEqual(details["exit_code"], {"kind": "other", "value": None})
        self.assertEqual(details["observed_in_range_string_frame_lines"], [1])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_failure_ids_are_bounded_to_sixteen_approved_methods(self):
        ids = frozenset("tests.test_owned.OwnedTests.test_" + str(index) for index in range(20))
        suite = "\n".join("ERROR: " + identity.rsplit(".", 1)[-1] + " (" + identity + ")" for identity in sorted(ids)).encode()
        result = observation.verification_log_observation(None, suite, ids)["suite_log"]
        self.assertEqual(len(result["known_failures"]), 16)
        self.assertTrue(result["failure_details_limited"])
        self.assertEqual(result["unknown_failure_records"], 0)

    def test_configured_profile_fingerprint_binds_all_execution_fields(self):
        fixture = self.fixture
        for field, value in (("image", "sha256:" + "f" * 64), ("command", ["python3", "-c", "changed"]),
                             ("uid", 60000), ("max_seconds", 200), ("environment_refs", {"MODEL": "env:PRIVATE"})):
            with self.subTest(field=field):
                original = fixture.config["profiles"][0][field]
                fixture.config["profiles"][0][field] = value
                result = fixture.observer().failure(AssertionError())
                self.assertFalse(result["runtime"]["available"])
                self.assertEqual(result["runtime"]["observation"]["reason"], "profile-fingerprint")
                fixture.config["profiles"][0][field] = original

    def test_duplicate_reversed_and_truncated_summaries_are_not_complete(self):
        cases = [b"Ran 4 tests in 0.1s\nFAILED (errors=1)\nRan 4 tests in 0.2s\nOK\n",
                 b"OK\nRan 4 tests in 0.1s\n", b"Ran 4 tests in 0.1s\nOK\n" + b"\n" * 20000]
        for content in cases:
            with self.subTest(length=len(content)):
                result = observation.verification_log_observation(None, content, frozenset())["suite_log"]
                self.assertFalse(result["complete_summary_observed"])
                self.assertIsNone(result["result"])
                self.assertTrue(result["summary_ambiguous"] or result["scan_limited"])
        output = json.dumps({"event": "configured_verification", "state": "exited", "exit_code": 1,
                             "output": "\n" * 1025}).encode()
        detail = observation.verification_log_observation(output, None, None)["output_log"]["details"][0]
        self.assertTrue(detail["output_scan_limited"])


if __name__ == "__main__":
    unittest.main()
