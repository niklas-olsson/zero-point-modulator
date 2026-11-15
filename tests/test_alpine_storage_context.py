"""Storage refusal context only: local files and controlled faults, no native run."""
import copy
import errno
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_alpine_sealed_runtime as runtime


PRIVATE = "PRIVATE-STORAGE-CONTENT-DO-NOT-EXPORT"


class HostileOSError(OSError):
    @property
    def errno(self):
        raise AssertionError("diagnostic accessed overridden errno")

    @property
    def filename(self):
        raise AssertionError("diagnostic accessed private filename")

    def __str__(self):
        raise AssertionError("diagnostic formatted the original error")

    def __repr__(self):
        raise AssertionError("diagnostic represented the original error")


class HostileString(str):
    def __hash__(self):
        raise AssertionError("diagnostic hashed an unexpected string")

    def __eq__(self, other):
        raise AssertionError("diagnostic compared an unexpected string")

    def __len__(self):
        raise AssertionError("diagnostic measured an unexpected string")


class StorageContextTests(unittest.TestCase):
    def setUp(self):
        parent = runtime.REPO / ".cache/alpine-storage-context"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def refusal(self, path, original, *, root=None, sources=None):
        root = root or self.root
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained")
        real_lstat = runtime.os.lstat

        def failed(value, *args, **kwargs):
            if Path(value) == path:
                raise original
            return real_lstat(value, *args, **kwargs)

        with patch.object(runtime.os, "lstat", side_effect=failed), self.assertRaises(runtime.Refused) as caught:
            runtime.logical_files([root], temporary_root=self.root, source_roots=sources)
        self.assertEqual(caught.exception.args, ("STORAGE_OBSERVATION",))
        return caught.exception

    def trial(self):
        trial = object.__new__(runtime.Trial)
        trial.record = {
            "schema": "zpm-native-alpine-native-report/v1", "attempt": "native-alpine-13",
            "plan_sha256": "a" * 64, "harness_source_revision": "b" * 40,
            "production_sources": dict(candidate.SOURCES), "recipe_sha256": "c" * 64,
            "native_checks": dict.fromkeys(candidate.NATIVE_CHECKS, "NOT_RUN"),
            "status": "RUNNING", "first_failure": None, "cleanup_failures": [],
            "native_execution_qualified": False, "truncated": False,
        }
        return trial

    def test_real_walk_and_atomic_preparation_publication_keep_original_refusal(self):
        work = self.root / "work"
        stage = work / (".zpm-prepare-" + PRIVATE)
        stage.mkdir(parents=True, mode=0o700)
        payload = b"bounded retained preparation fixture"
        (stage / "manifest.json").write_bytes(payload)
        before = runtime.logical_files([self.root], temporary_root=self.root)
        real_walk = runtime.os.walk
        published = []

        def publishing_walk(root, **options):
            for directory, dirs, files in real_walk(root, **options):
                if Path(directory) == work and stage.name in dirs and not published:
                    stage.rename(work / "installation")
                    published.append(True)
                yield directory, dirs, files

        with patch.object(runtime.os, "walk", side_effect=publishing_walk), self.assertRaises(runtime.Refused) as caught:
            runtime.logical_files([self.root], temporary_root=self.root)
        self.assertEqual(caught.exception.args, ("STORAGE_OBSERVATION",))
        self.assertEqual(runtime._storage_observation(caught.exception), {
            "operation": "ENTRY_LSTAT_MISSING", "root_role": "TRIAL",
            "subtree": "PREPARATION_STAGE", "errno_class": "ENOENT",
        })
        self.assertEqual((work / "installation/manifest.json").read_bytes(), payload)
        self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root), before)
        self.assertEqual(published, [True])

    def test_walk_failure_retains_only_known_root_and_builtin_errno(self):
        original = HostileOSError(errno.EACCES, PRIVATE, PRIVATE)
        calls = []

        def unavailable(root, **options):
            calls.append(root)
            options["onerror"](original)
            yield str(root), [], []

        with patch.object(runtime.os, "walk", side_effect=unavailable), self.assertRaises(runtime.Refused) as caught:
            runtime.logical_files([self.root], temporary_root=self.root)
        self.assertEqual(calls, [self.root])
        self.assertEqual(runtime._storage_observation(caught.exception), {
            "operation": "WALK_ENUMERATION", "root_role": "TRIAL",
            "subtree": "UNAVAILABLE", "errno_class": "EACCES",
        })

    def test_other_lstat_failure_uses_closed_errno_without_extra_io(self):
        path = self.root / "work/installation/environments" / PRIVATE
        path.parent.mkdir(parents=True)
        path.write_bytes(b"retained")
        original = HostileOSError(errno.EPERM, PRIVATE, PRIVATE)
        real_lstat, real_stat, real_walk = runtime.os.lstat, runtime.os.stat, runtime.os.walk
        calls, counts_at_failure = [], []

        def lstat(value, *args, **kwargs):
            calls.append(("lstat", value))
            if Path(value) == path:
                counts_at_failure.append(len(calls))
                raise original
            return real_lstat(value, *args, **kwargs)

        def observed_stat(value, *args, **kwargs):
            calls.append(("stat", value))
            return real_stat(value, *args, **kwargs)

        with patch.object(runtime.os, "lstat", side_effect=lstat), \
                patch.object(runtime.os, "stat", side_effect=observed_stat), \
                patch.object(runtime.os, "walk", wraps=real_walk) as walk, \
                patch.object(runtime.os, "open", side_effect=AssertionError("extra open")), \
                patch.object(Path, "resolve", side_effect=AssertionError("extra resolution")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("extra read")), \
                self.assertRaises(runtime.Refused) as caught:
            runtime.logical_files([self.root], temporary_root=self.root)
        walk.assert_called_once()
        self.assertEqual(counts_at_failure, [len(calls)])
        self.assertEqual(runtime._storage_observation(caught.exception), {
            "operation": "ENTRY_LSTAT_ERROR", "root_role": "TRIAL",
            "subtree": "INSTALLATION_ENVIRONMENTS", "errno_class": "EPERM",
        })

    def test_known_source_roles_are_bound_to_selected_path_objects(self):
        for service, role in (("zpm", "ZPM_SOURCE"), ("openjules", "OPENJULES_SOURCE"),
                              ("polyphony", "POLYPHONY_SOURCE")):
            root = self.root / service
            failure = self.refusal(root / ".git" / PRIVATE, OSError(errno.EIO, PRIVATE),
                                   root=root, sources={service: root})
            with self.subTest(service=service):
                self.assertEqual(runtime._storage_observation(failure), {
                    "operation": "ENTRY_LSTAT_ERROR", "root_role": role,
                    "subtree": "SOURCE_GIT", "errno_class": "EIO",
                })
        root = self.root / "harness"
        with patch.object(runtime, "REPO", root):
            failure = self.refusal(root / "file", OSError(errno.ENOTDIR, PRIVATE), root=root)
        self.assertEqual(runtime._storage_observation(failure)["root_role"], "HARNESS")
        self.assertEqual(runtime._storage_observation(failure)["subtree"], "SOURCE_FILES")
        root = self.root / "unselected"
        failure = self.refusal(root / "file", OSError(errno.EIO, PRIVATE), root=root)
        self.assertEqual(runtime._storage_observation(failure)["root_role"], "UNAVAILABLE")
        self.assertEqual(runtime._storage_observation(failure)["subtree"], "UNAVAILABLE")

    def test_unknown_errno_is_not_formatted_or_exported(self):
        for number, expected in ((None, "UNAVAILABLE"), (123456789, "OTHER"), (False, "UNAVAILABLE")):
            original = OSError(number, PRIVATE, PRIVATE)
            failure = self.refusal(self.root / "entry", original)
            with self.subTest(number=number):
                self.assertEqual(runtime._storage_observation(failure)["errno_class"], expected)

    def test_reserved_missing_slot_remains_charged_without_a_failure_or_diagnostic(self):
        path = self.root / "acquisition.json.new"
        path.write_bytes(b"fixture")
        real_lstat = runtime.os.lstat

        def vanished(value, *args, **kwargs):
            if Path(value) == path:
                raise FileNotFoundError(errno.ENOENT, PRIVATE, PRIVATE)
            return real_lstat(value, *args, **kwargs)

        with patch.object(runtime.os, "lstat", side_effect=vanished), \
                patch.object(runtime, "_remember_storage_observation", side_effect=AssertionError("new refusal")):
            self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root),
                             sum(runtime.TRANSIENT_FILES.values()))

    def test_stable_file_hardlink_and_symlink_accounting_is_unchanged(self):
        path = self.root / "ordinary"
        path.write_bytes(b"12345")
        os.link(path, self.root / "hardlink")
        (self.root / "symlink").symlink_to(path)
        self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root),
                         sum(runtime.TRANSIENT_FILES.values()) + 5)

    def test_control_failures_from_original_walk_and_lstat_propagate_by_identity(self):
        (self.root / "control-entry").write_bytes(b"retained")
        for control in (runtime.Refused("HOST_INTERRUPTED"), runtime.Refused("HOST_TOTAL_DEADLINE"),
                        KeyboardInterrupt(PRIVATE), SystemExit(PRIVATE)):
            for surface in ("walk", "lstat"):
                with self.subTest(control=type(control).__name__, surface=surface):
                    with patch.object(runtime.os, surface, side_effect=control), \
                            patch.object(runtime, "_remember_storage_observation", side_effect=AssertionError("new diagnostic")), \
                            self.assertRaises(type(control)) as caught:
                        runtime.logical_files([self.root], temporary_root=self.root)
                    self.assertIs(caught.exception, control)

    def test_real_control_signal_at_original_lstat_is_not_swallowed(self):
        path = self.root / "file"
        path.write_bytes(b"retained")
        original = runtime.Refused("HOST_INTERRUPTED")
        real_lstat = runtime.os.lstat

        def interrupted(signum, frame):
            raise original

        def lstat(value, *args, **kwargs):
            if Path(value) == path:
                signal.raise_signal(signal.SIGTERM)
            return real_lstat(value, *args, **kwargs)

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with patch.object(runtime.os, "lstat", side_effect=lstat), self.assertRaises(runtime.Refused) as caught:
                runtime.logical_files([self.root], temporary_root=self.root)
            self.assertIs(caught.exception, original)
            self.assertIsNone(runtime._storage_observation(original))
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_optional_metadata_faults_cannot_replace_the_original_refusal(self):
        for later in (RuntimeError(PRIVATE), runtime.Refused("HOST_TOTAL_DEADLINE"), KeyboardInterrupt(PRIVATE)):
            with self.subTest(later=type(later).__name__), \
                    patch.object(runtime, "_storage_location", side_effect=later):
                failure = self.refusal(self.root / "file", OSError(errno.EIO, PRIVATE))
            self.assertEqual(failure.args, ("STORAGE_OBSERVATION",))
            self.assertEqual(runtime._storage_observation(failure), dict.fromkeys(runtime._STORAGE_FIELDS, "UNAVAILABLE"))

    def test_refusal_custody_survives_whole_recorder_failure(self):
        with patch.object(runtime, "_remember_storage_observation", side_effect=KeyboardInterrupt(PRIVATE)):
            failure = self.refusal(self.root / "file", FileNotFoundError(errno.ENOENT, PRIVATE))
        self.assertEqual(failure.args, ("STORAGE_OBSERVATION",))
        self.assertIsNone(runtime._storage_observation(failure))

    def test_first_failure_context_is_immutable_and_later_cleanup_cannot_replace_it(self):
        first = self.refusal(self.root / "work/tmp/first", OSError(errno.EACCES, PRIVATE))
        expected = runtime._storage_observation(first)
        trial = self.trial()
        trial.fail("STORAGE_OBSERVATION", failure=first)
        first.storage_observation = {"raw_path": PRIVATE}
        changed_copy = runtime._storage_observation(first)
        changed_copy["subtree"] = "TRIAL_OTHER"
        second = self.refusal(self.root / "inputs/second", OSError(errno.EIO, PRIVATE))
        trial.fail("STORAGE_OBSERVATION", failure=second)
        trial.fail("CLEANUP_UNCONFIRMED", failure=second)
        self.assertEqual(trial.record["storage_observation"], expected)
        self.assertEqual(trial.record["first_failure"], "STORAGE_OBSERVATION")
        self.assertEqual(runtime._storage_observation(first), expected)
        earlier = self.trial()
        earlier.fail("COMMAND_EXIT")
        earlier.fail("STORAGE_OBSERVATION", failure=first)
        self.assertNotIn("storage_observation", earlier.record)

    def test_arbitrary_exception_attributes_are_never_adopted(self):
        failure = runtime.Refused("STORAGE_OBSERVATION")
        failure.storage_observation = {"operation": PRIVATE}
        trial = self.trial()
        trial.fail("STORAGE_OBSERVATION", failure=failure)
        self.assertNotIn("storage_observation", trial.record)

    def test_closed_projection_rejects_hostile_values_and_inconsistent_context(self):
        failure = self.refusal(self.root / "work/tmp/file", OSError(errno.EIO, PRIVATE))
        valid = runtime._storage_observation(failure)
        unavailable = dict.fromkeys(runtime._STORAGE_FIELDS, "UNAVAILABLE")
        malformed = [None, [], PRIVATE, {"path": PRIVATE}, {**valid, "extra": PRIVATE},
                     {**valid, "root_role": "UNAVAILABLE"}, {**valid, "operation": "WALK_ENUMERATION"},
                     {**valid, "root_role": "HARNESS"}]
        for field in runtime._STORAGE_FIELDS:
            malformed.extend({**valid, field: value} for value in ([], {}, True, PRIVATE, PRIVATE * 100, HostileString(PRIVATE)))
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(runtime._safe_storage_observation(value), unavailable)
        trial = self.trial()
        trial.fail("STORAGE_OBSERVATION", failure=failure)
        for value in malformed:
            trial.record["storage_observation"] = value
            public = runtime.public_summary(trial.record)
            self.assertEqual(public["storage_observation"], unavailable)
            self.assertNotIn(PRIVATE, json.dumps(public))

    def test_public_and_overflow_reports_keep_only_the_original_closed_context(self):
        failure = self.refusal(self.root / "work/tmp/file", HostileOSError(errno.EACCES, PRIVATE, PRIVATE))
        trial = self.trial()
        trial.fail("STORAGE_OBSERVATION", failure=failure)
        expected = copy.deepcopy(trial.record["storage_observation"])
        public = runtime.public_summary(trial.record)
        self.assertEqual(public["storage_observation"], expected)
        self.assertNotIn(PRIVATE, json.dumps(public))
        trial.record["private_padding"] = PRIVATE + "x" * runtime.CAPS["report_bytes"]
        value = json.loads(runtime.report_bytes(trial.record))
        self.assertEqual(value["first_failure"], "STORAGE_OBSERVATION")
        self.assertEqual(value["storage_observation"], expected)
        self.assertTrue(value["truncated"])
        self.assertFalse(value["native_execution_qualified"])
        self.assertNotIn(PRIVATE, json.dumps(value))
        trial.record["storage_observation"] = {"raw_path": PRIVATE}
        self.assertEqual(json.loads(runtime.report_bytes(trial.record))["storage_observation"],
                         dict.fromkeys(runtime._STORAGE_FIELDS, "UNAVAILABLE"))

    def test_projector_failure_is_inert_after_original_storage_failure(self):
        failure = self.refusal(self.root / "file", OSError(errno.EIO, PRIVATE))
        trial = self.trial()
        trial.fail("STORAGE_OBSERVATION", failure=failure)
        with patch.object(runtime, "_safe_storage_observation", side_effect=KeyboardInterrupt(PRIVATE)):
            self.assertEqual(runtime.public_summary(trial.record)["storage_observation"],
                             dict.fromkeys(runtime._STORAGE_FIELDS, "UNAVAILABLE"))
            trial.record["private_padding"] = PRIVATE + "x" * runtime.CAPS["report_bytes"]
            value = json.loads(runtime.report_bytes(trial.record))
        self.assertEqual(value["first_failure"], "STORAGE_OBSERVATION")
        self.assertEqual(value["storage_observation"], dict.fromkeys(runtime._STORAGE_FIELDS, "UNAVAILABLE"))


if __name__ == "__main__":
    unittest.main()
