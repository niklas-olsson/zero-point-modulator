"""Public fixture diagnostics preserve complete execution and private evidence."""

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_confined_suite as trial
finally:
    sys.path.pop(0)


class ConfinedSuiteTests(unittest.TestCase):
    def test_private_exception_data_never_enters_projection(self):
        root = Path(__file__).resolve().parents[1]
        relative = str(Path(__file__).resolve().relative_to(root))
        try:
            raise subprocess.CalledProcessError(7, ["PRIVATE_ARG"], output="PRIVATE_OUTPUT", stderr="PRIVATE_STDERR")
        except subprocess.CalledProcessError as error:
            result = trial.failure(error, error.__traceback__, root, {relative: 10000})
        self.assertEqual((result["kind"], result["returncode"], result["errno"]), ("process", 7, None))
        self.assertTrue(result["frames"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertTrue(all(frame["file"] == relative for frame in result["frames"]))
        self.assertEqual(trial.failure(OSError(11, "PRIVATE_PATH"), None, root, {})["errno"], 11)

    def test_unknown_process_stats_remain_unknown_and_zero_counters_remain_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cgroup, proc = root / "cgroup", root / "proc"
            cgroup.mkdir()
            proc.mkdir()
            (cgroup / "pids.current").write_text("0\n")
            (cgroup / "pids.max").write_text("128\n")
            (cgroup / "pids.events").write_text("max 2\nPRIVATE 55\n")
            (proc / "1").mkdir()
            first = trial.resources(cgroup, proc)
            self.assertEqual((first["pids.current:value"], first["pids.max:value"], first["pids.events:max"]), (0, 128, 2))
            self.assertEqual(first["processes"], 1)
            self.assertEqual(first["process_stats_unavailable"], 1)
            self.assertIsNone(first["zombies"])
            (proc / "1/stat").write_text("1 (PRIVATE_PROCESS) Z 0 0 0\n")
            second = trial.resources(cgroup, proc)
            self.assertEqual((second["zombies"], second["process_stats_unavailable"]), (1, 0))
            self.assertIsNone(second["memory.current:value"])
            self.assertNotIn("PRIVATE", json.dumps(second))

    def test_full_suite_keeps_failure_subtest_skip_and_complete_inventory(self):
        class Cases(unittest.TestCase):
            def test_pass(self):
                self.assertTrue(True)

            def test_failure(self):
                with self.subTest(secret="PRIVATE_SUBTEST"):
                    self.assertEqual(1, 2, "PRIVATE_ASSERTION")

            @unittest.skip("PRIVATE_SKIP")
            def test_skip(self):
                self.fail("unreachable")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Cases)
        approved = frozenset(trial.flatten(suite))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "report.json"
            with patch.object(trial, "source_inventory", return_value=(approved, {})), \
                    patch.object(trial.unittest.TestLoader, "discover", return_value=suite), \
                    patch.object(trial, "resources", return_value={}):
                code = trial.run_suite("a" * 40, destination)
            value = json.loads(destination.read_text())
            selected = trial.safe_suite(value, approved, {})
            self.assertEqual(code, 1)
            self.assertTrue(selected["complete"])
            self.assertEqual((selected["expected"], selected["executed"], selected["skipped"], selected["failure_count"]), (3, 3, 1, 1))
            self.assertEqual(selected["inventory_sha256"], selected["executed_inventory_sha256"])
            self.assertEqual(selected["progress"]["started"], 3)
            self.assertEqual(selected["progress"]["completed"], 3)
            self.assertIsNone(selected["progress"]["active_test_id"])
            self.assertEqual(len(selected["slowest_completed"]), 3)
            self.assertFalse(selected["passed"])
            self.assertNotIn("PRIVATE", json.dumps(selected))
            self.assertIn("PRIVATE_ASSERTION", (destination.parent / "confined-unittest.log").read_text())
            changed = copy.deepcopy(value)
            changed["failures"][0]["test_id"] = "PRIVATE_TEST_ID"
            with self.assertRaises(AssertionError):
                trial.safe_suite(changed, approved, {})
            changed = copy.deepcopy(value)
            changed["failures"][0]["frames"] = [{"file": "PRIVATE_PATH", "line": 1}]
            with self.assertRaises(AssertionError):
                trial.safe_suite(changed, approved, {})
            changed = copy.deepcopy(value)
            changed["executed_inventory_sha256"] = "b" * 64
            with self.assertRaises(AssertionError):
                trial.safe_suite(changed, approved, {})

    def test_bytecode_cache_observation_exports_only_fixed_boolean_presence(self):
        with tempfile.TemporaryDirectory() as temporary:
            present = Path(temporary) / "PRIVATE_PRESENT.pyc"
            present.write_bytes(b"PRIVATE_CONTENT")
            modules = {name: SimpleNamespace(__cached__=str(present)) for name in trial.CACHE_MODULES}
            modules["subprocess"].__cached__ = str(Path(temporary) / "PRIVATE_ABSENT.pyc")
            modules["tempfile"] = SimpleNamespace()
            with patch.object(trial, "CACHE_MODULES", modules), patch.object(trial.sys, "dont_write_bytecode", True):
                value = trial.bytecode_cache()
            self.assertEqual(value, {"dont_write_bytecode": True, "json": True,
                                     "subprocess": False, "tempfile": False, "unittest": True})
            self.assertNotIn("PRIVATE", json.dumps(value))

    def test_progress_checkpoint_identifies_active_test_without_claiming_completion(self):
        checkpoints = []

        class Cases(unittest.TestCase):
            pass

        def case(test):
            checkpoints.append(json.loads(destination.read_text()))

        for index in range(20):
            setattr(Cases, f"test_{index:02}", case)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Cases)
        approved = frozenset(trial.flatten(suite))
        final_checkpoint = []
        original_save = trial.save

        def retain_checkpoint(path, value):
            original_save(path, value)
            if value["progress"]["completed"] == len(approved) and not value["complete"]:
                final_checkpoint.append(json.loads(path.read_text()))

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "report.json"
            with patch.object(trial, "source_inventory", return_value=(approved, {})), \
                    patch.object(trial.unittest.TestLoader, "discover", return_value=suite), \
                    patch.object(trial, "resources", return_value={}) as resource_scan, \
                    patch.object(trial, "save", side_effect=retain_checkpoint):
                self.assertEqual(trial.run_suite("a" * 40, destination), 0)
            self.assertEqual(resource_scan.call_count, 2)
            for index, checkpoint in enumerate(checkpoints):
                selected = trial.safe_suite(checkpoint, approved, {})
                self.assertEqual((selected["progress"]["started"], selected["progress"]["completed"]),
                                 (index + 1, index))
                self.assertEqual(selected["progress"]["active_test_id"], sorted(approved)[index])
                self.assertEqual(len(selected["slowest_completed"]), min(trial.MAX_TIMINGS, index))
                self.assertNotIn(selected["progress"]["active_test_id"],
                                 [row["test_id"] for row in selected["slowest_completed"]])
                self.assertFalse(selected["complete"] or selected["passed"])
            self.assertEqual(len(final_checkpoint), 1)
            selected = trial.safe_suite(final_checkpoint[0], approved, {})
            self.assertEqual(selected["progress"]["completed"], len(approved))
            self.assertIsNone(selected["progress"]["active_test_id"])
            self.assertFalse(selected["complete"] or selected["passed"])
            final = trial.safe_suite(json.loads(destination.read_text()), approved, {})
            self.assertTrue(final["complete"] and final["passed"])
            self.assertEqual(len(final["slowest_completed"]), trial.MAX_TIMINGS)

        original = checkpoints[1]
        for change in (
            {"active_test_id": "PRIVATE_TEST_ID"},
            {"active_test_id": None},
            {"started": 0}, {"completed": 2}, {"started": len(approved) + 1},
            {"elapsed_seconds": -1}, {"elapsed_seconds": float("inf")},
            {"elapsed_seconds": float("nan")}, {"elapsed_seconds": True},
        ):
            with self.subTest(progress=tuple(change)):
                value = copy.deepcopy(original)
                value["progress"].update(change)
                with self.assertRaises(AssertionError):
                    trial.safe_suite(value, approved, {})
        for change in (
            {"test_id": "PRIVATE_TEST_ID"},
            {"test_id": original["progress"]["active_test_id"]},
            {"seconds": -1}, {"seconds": float("inf")},
            {"seconds": float("nan")}, {"seconds": True},
        ):
            with self.subTest(timing=tuple(change)):
                value = copy.deepcopy(original)
                value["slowest_completed"][0].update(change)
                with self.assertRaises(AssertionError):
                    trial.safe_suite(value, approved, {})
        value = copy.deepcopy(checkpoints[2])
        value["slowest_completed"][1] = value["slowest_completed"][0]
        with self.assertRaises(AssertionError):
            trial.safe_suite(value, approved, {})
        value = copy.deepcopy(final)
        value["slowest_completed"].append(value["slowest_completed"][0])
        with self.assertRaises(AssertionError):
            trial.safe_suite(value, approved, {})
        value = copy.deepcopy(original)
        value["complete"] = True
        value["executed_inventory_sha256"] = value["inventory_sha256"]
        with self.assertRaises(AssertionError):
            trial.safe_suite(value, approved, {})
        value = copy.deepcopy(final)
        value["executed"] = 0
        with self.assertRaises(AssertionError):
            trial.safe_suite(value, approved, {})
        for cache in ({}, final["bytecode_cache"] | {"PRIVATE_MODULE": True},
                      final["bytecode_cache"] | {"json": "PRIVATE_PATH"},
                      final["bytecode_cache"] | {"dont_write_bytecode": 1}):
            value = copy.deepcopy(final)
            value["bytecode_cache"] = cache
            with self.assertRaises(AssertionError):
                trial.safe_suite(value, approved, {})

    def test_missing_or_repeated_test_inventory_refuses_before_running(self):
        class Case(unittest.TestCase):
            def test_one(self):
                raise AssertionError("must not execute")

        case = Case("test_one")
        for suite in (unittest.TestSuite(), unittest.TestSuite([case, case])):
            with self.subTest(count=suite.countTestCases()), tempfile.TemporaryDirectory() as temporary:
                with patch.object(trial, "source_inventory", return_value=(frozenset({case.id()}), {})), \
                        patch.object(trial.unittest.TestLoader, "discover", return_value=suite):
                    with self.assertRaises(AssertionError):
                        trial.run_suite("a" * 40, Path(temporary) / "report.json")

    def test_raw_log_is_bounded_without_altering_execution_result(self):
        output = io.BytesIO()
        log = trial.BoundedLog(output)
        log.write("x" * 1_999_999)
        log.write("\N{SNOWMAN}" * 1000)
        log.write("PRIVATE_AFTER_LIMIT")
        self.assertEqual(len(output.getvalue()), 2_000_000)
        self.assertTrue(log.limited)
        self.assertNotIn(b"PRIVATE_AFTER_LIMIT", output.getvalue())

    def test_outer_deadline_escapes_ordinary_producer_exception_handler(self):
        with self.assertRaises(trial.Deadline):
            try:
                raise trial.Deadline()
            except Exception:
                self.fail("producer swallowed the final deadline")
        self.assertEqual(trial.exception_kind(trial.Deadline()), "timeout")

    def test_fixture_cleanup_uses_acknowledged_identity_and_native_guards(self):
        class Missing(Exception):
            pass

        identity = {"Name": "original-volume", "CreatedAt": "original", "Driver": "local"}
        row = {"container_id": "original-container", "volume_identity": json.dumps(identity)}
        container = SimpleNamespace(id="original-container", attrs={"State": {"Running": False}}, remove=Mock())
        volume = SimpleNamespace(remove=Mock())
        runtime = SimpleNamespace(_row=Mock(return_value=row), _container=Mock(return_value=container),
                                  _cleanup_container=Mock(return_value=container), _cleanup_volume=Mock(return_value=volume),
                                  client=SimpleNamespace(containers=SimpleNamespace(get=Mock(side_effect=Missing)),
                                                         volumes=SimpleNamespace(get=Mock(side_effect=Missing))))
        original = {"container": "original-container", "volume": identity}
        result = trial.cleanup_fixture(runtime, "fixture", "spec", original, Missing)
        self.assertTrue(result["complete"])
        runtime._cleanup_container.assert_called_once_with("fixture", "spec", "original-container", "original-volume")
        runtime._cleanup_volume.assert_called_once_with("fixture", "spec", identity)
        container.remove.assert_called_once_with(force=False)
        volume.remove.assert_called_once_with(force=False)
        container.remove.reset_mock()
        volume.remove.reset_mock()
        for changed in ({"container": "foreign", "volume": identity},
                        {"container": "original-container", "volume": identity | {"CreatedAt": "replacement"}}):
            with self.assertRaises(AssertionError):
                trial.cleanup_fixture(runtime, "fixture", "spec", changed, Missing)
        container.remove.assert_not_called()
        volume.remove.assert_not_called()
        container.attrs["State"]["Running"] = True
        container.stop = Mock()
        container.reload = Mock()
        with self.assertRaises(AssertionError):
            trial.cleanup_fixture(runtime, "fixture", "spec", None, Missing)
        container.stop.assert_called_once_with(timeout=2)
        container.remove.assert_not_called()
        container.attrs["State"]["Running"] = False
        runtime.client.containers.get.side_effect = None
        with self.assertRaises(AssertionError):
            trial.cleanup_fixture(runtime, "fixture", "spec", original, Missing)


if __name__ == "__main__":
    unittest.main()
