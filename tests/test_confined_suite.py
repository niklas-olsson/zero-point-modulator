"""Deterministic boundaries for the synthetic confined suite, without Docker."""

from copy import deepcopy
import errno
import hashlib
import io
import json
from pathlib import Path
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


PRIVATE = "PRIVATE_MESSAGE_URL_PATH_AND_SUBTEST"
SOURCE = "a" * 40
DRIVER_HASH = "b" * 64


def unavailable():
    return {"pid_namespace_matches": None, "pid1_watchdog_matches": None,
            "pid_scope_verified": False, "cgroup_scope_verified": False,
            "processes": {"available": False},
            "cgroup": {"current": None, "maximum": None, "events_max": None}}


def successful_guest():
    return {"schema_version": 1, "source": SOURCE, "expected_tests": 1,
            "discovered_tests": 1, "inventory_matches": True, "driver_sha256": DRIVER_HASH,
            "complete": True, "successful": True, "tests_run": 1, "failures": 0, "errors": 0,
            "skipped": 0, "expected_failures": 0, "unexpected_successes": 0,
            "observations": [], "unknown_ids": 0, "details_limited": False,
            "diagnostics_unavailable": False, "before": unavailable(), "after": unavailable()}


def identity(case):
    return ".".join((type(case).__module__, type(case).__name__, case._testMethodName))


class ConfinedSuiteTests(unittest.TestCase):
    def test_exception_projection_is_bounded_and_never_renders_private_objects(self):
        class Hostile(OSError):
            @property
            def errno(self):
                raise AssertionError("arbitrary accessor called")

            def __str__(self):
                raise AssertionError("arbitrary rendering called")

        hidden = Hostile(PRIVATE)
        root = RuntimeError(PRIVATE)
        root.__context__ = BlockingIOError(errno.EAGAIN, PRIVATE)
        root.__suppress_context__ = True
        root.__context__.__context__ = hidden
        hidden.__context__ = root
        value = trial.exception_chain(root)
        self.assertEqual([item["category"] for item in value["chain"]], ["runtime", "would-block", "other"])
        self.assertEqual(value["chain"][1]["errno"], errno.EAGAIN)
        self.assertTrue(value["chain"][0]["context_suppressed"])
        self.assertTrue(value["cycle"])
        self.assertNotIn(PRIVATE, json.dumps(value))
        nodes = [RuntimeError(PRIVATE) for _ in range(6)]
        for current, following in zip(nodes, nodes[1:]):
            current.__cause__ = following
        bounded = trial.exception_chain(nodes[0])
        self.assertEqual(len(bounded["chain"]), 4)
        self.assertTrue(bounded["limited"])

    def test_http_and_thread_categories_are_closed(self):
        for error, expected in ((trial.HTTPError(PRIVATE, 500, PRIVATE, {}, None), "http"),
                                (trial.URLError(PRIVATE), "url"),
                                (trial.http.client.RemoteDisconnected(PRIVATE), "remote-disconnected"),
                                (RuntimeError("can't start new thread"), "thread-start"),
                                (OSError(errno.EMFILE, PRIVATE), "os")):
            with self.subTest(expected=expected):
                value = trial.exception_chain(error)
                self.assertEqual(value["chain"][0]["category"], expected)
                self.assertNotIn(PRIVATE, json.dumps(value))

    def test_live_result_preserves_standard_outcomes_and_parent_subtest_identity(self):
        class Payload(unittest.TestCase):
            def test_pass(self):
                pass

            def test_failure(self):
                self.fail(PRIVATE)

            def test_error(self):
                raise ValueError(PRIVATE)

            def test_subtests(self):
                for number in range(2):
                    with self.subTest(secret=PRIVATE, number=number):
                        self.fail(PRIVATE)

            @unittest.skip(PRIVATE)
            def test_skip(self):
                pass

            @unittest.expectedFailure
            def test_expected(self):
                self.fail(PRIVATE)

            @unittest.expectedFailure
            def test_unexpected(self):
                pass

        loader = unittest.TestLoader()
        approved = {identity(case) for case in loader.loadTestsFromTestCase(Payload)}
        standard = unittest.TextTestRunner(stream=io.StringIO()).run(loader.loadTestsFromTestCase(Payload))
        private = io.StringIO()
        publish = Mock()
        observed = unittest.TextTestRunner(stream=private, resultclass=lambda *a, **kw:
            trial.ObservedResult(*a, approved=approved, sample=unavailable, publish=publish, **kw)
        ).run(loader.loadTestsFromTestCase(Payload))
        self.assertEqual(observed.testsRun, standard.testsRun)
        for attribute in ("failures", "errors", "skipped", "expectedFailures", "unexpectedSuccesses"):
            self.assertEqual(len(getattr(observed, attribute)), len(getattr(standard, attribute)))
        self.assertEqual(observed.wasSuccessful(), standard.wasSuccessful())
        self.assertEqual(len(observed.observations), 4)
        self.assertEqual(publish.call_count, 4)
        subtests = [row for row in observed.observations if row["kind"] == "subtest"]
        self.assertEqual(len(subtests), 2)
        self.assertTrue(all(row["test_id"].endswith("Payload.test_subtests") for row in subtests))
        self.assertNotIn(PRIVATE, json.dumps(observed.observations))
        self.assertIn(PRIVATE, private.getvalue())

    def test_diagnostic_faults_do_not_erase_standard_failure(self):
        class Payload(unittest.TestCase):
            def test_failure(self):
                self.fail(PRIVATE)

        case = Payload("test_failure")
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=lambda *a, **kw:
            trial.ObservedResult(*a, approved={identity(case)}, sample=unavailable,
                publish=Mock(side_effect=ValueError(PRIVATE)), **kw)).run(case)
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.wasSuccessful())
        self.assertTrue(result.diagnostics_unavailable)

    def test_test_identity_does_not_call_user_rendering_or_accept_error_holders(self):
        class Payload(unittest.TestCase):
            def test_one(self):
                pass

            def id(self):
                raise AssertionError("id called")

            def __str__(self):
                raise AssertionError("str called")

        case = Payload("test_one")
        selected = identity(case)
        self.assertEqual(trial.approved_identity(case, {selected}), selected)
        self.assertIsNone(trial.approved_identity(unittest.suite._ErrorHolder(PRIVATE), {selected}))

    def test_suite_writes_initial_report_and_rejects_same_count_duplicate_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"

            class Payload(unittest.TestCase):
                def test_one(self):
                    self.assertFalse(json.loads(report.read_text())["complete"])

                def test_two(self):
                    self.assertFalse(json.loads(report.read_text())["complete"])

            ids = {identity(Payload(name)) for name in ("test_one", "test_two")}
            config = {"source": SOURCE, "driver_sha256": DRIVER_HASH, "approved_ids": sorted(ids),
                      "watchdog_hash": DRIVER_HASH, "scopes": {}}
            for names, matches in ((("test_one", "test_two"), True), (("test_one", "test_one"), False)):
                suite = unittest.TestSuite(Payload(name) for name in names)
                with patch.object(trial, "REPORT", report), patch.object(trial, "resource_observation", side_effect=lambda *a: unavailable()), \
                     patch.object(trial.unittest.defaultTestLoader, "discover", return_value=suite), \
                     patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(trial.suite_main(config), 0)
                value = json.loads(report.read_text())
                trial.validate_guest(value, SOURCE, ids, DRIVER_HASH)
                self.assertEqual(value["tests_run"], 2)
                self.assertEqual(value["inventory_matches"], matches)
                self.assertEqual(trial.qualification_passed(0, value, 2), matches)

    def test_denied_namespace_probe_retains_host_scoped_process_and_cgroup_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup = root / "proc", root / "cgroup"
            (proc / "self").mkdir(parents=True)
            (proc / "1").mkdir()
            (proc / "2").mkdir()
            cgroup.mkdir()
            command = b"python3\0-I\0-S\0-c\0watchdog\0"
            (proc / "1/cmdline").write_bytes(command)
            for number, state, parent in ((1, "S", 0), (2, "Z", 1)):
                (proc / str(number) / "stat").write_text(f"{number} (private name) {state} {parent} " + "0 " * 20)
            (proc / "self/cgroup").write_text("0::/\n")
            (proc / "self/mountinfo").write_text("1 2 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw\n")
            for name, value in (("pids.current", "2"), ("pids.max", "128"), ("pids.events", "max 3")):
                (cgroup / name).write_text(value)
            scopes = {"private_pid_namespace": True, "private_cgroup_namespace": True}
            with patch.object(trial.os, "readlink", side_effect=PermissionError(errno.EACCES, PRIVATE)):
                value = trial.resource_observation(hashlib.sha256(command).hexdigest(), scopes, proc, cgroup)
            trial.validate_resources(value)
            self.assertIsNone(value["pid_namespace_matches"])
            self.assertTrue(value["pid1_watchdog_matches"])
            self.assertEqual(value["processes"], {"available": True, "inspected": 2, "zombies": 1,
                "adopted_by_pid1": 1, "pid1_zombies": 1})
            self.assertEqual(value["cgroup"], {"current": 2, "maximum": 128, "events_max": 3})
            unscoped = trial.resource_observation(DRIVER_HASH, {}, proc, cgroup)
            self.assertEqual(unscoped["processes"], {"available": False})
            self.assertEqual(unscoped["cgroup"], {"current": None, "maximum": None, "events_max": None})

    def test_process_traversal_bound_counts_unreadable_and_nonnumeric_entries(self):
        proc = Mock()
        proc.__truediv__ = Mock(return_value=Path("/does-not-exist"))
        proc.iterdir.return_value = (SimpleNamespace(name="entry") for _ in range(1025))
        value = trial.resource_observation(DRIVER_HASH, {"private_pid_namespace": True}, proc)
        self.assertEqual(value["processes"], {"available": False})

    def test_closed_validation_and_exit_qualification_reject_false_success(self):
        value = successful_guest()
        approved = {"tests.test_fixture.Owned.test_one"}
        self.assertIs(trial.validate_guest(value, SOURCE, approved, DRIVER_HASH), value)
        self.assertTrue(trial.qualification_passed(0, value, 1))
        for code in (None, False, 1, -9):
            self.assertFalse(trial.qualification_passed(code, value, 1))
        for field, changed in (("complete", False), ("inventory_matches", False), ("skipped", 1),
                               ("expected_failures", 1), ("unknown_ids", 1), ("diagnostics_unavailable", True)):
            modified = deepcopy(value)
            modified[field] = changed
            self.assertFalse(trial.qualification_passed(0, modified, 1))
        for field in ("pid_namespace_matches", "pid1_watchdog_matches"):
            for sample in ("before", "after"):
                modified = deepcopy(value)
                modified[sample][field] = False
                self.assertFalse(trial.qualification_passed(0, modified, 1))
        modified = deepcopy(value)
        modified["after"]["cgroup_scope_verified"] = True
        modified["after"]["cgroup"]["maximum"] = 256
        self.assertFalse(trial.qualification_passed(0, modified, 1))
        for key, changed in (("private", PRIVATE), ("tests_run", True), ("driver_sha256", "wrong"), ("after", None)):
            modified = deepcopy(value)
            modified[key] = changed
            with self.assertRaises(ValueError):
                trial.validate_guest(modified, SOURCE, approved, DRIVER_HASH)

    def test_inspected_profile_refuses_changes_to_actual_confinement(self):
        image, source, volume, watchdog = "sha256:" + "c" * 64, Path("/owned/source"), "owned", "watchdog"
        host = {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                "Memory": 536870912, "MemorySwap": 536870912, "CpuPeriod": 100000, "CpuQuota": 100000,
                "PidsLimit": 128, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                    "/control": "rw,noexec,nosuid,nodev,size=65536,uid=65534,gid=65534,mode=0700"}}
        attrs = {"Image": image, "HostConfig": host, "Config": {"User": "65534:65534",
            "Entrypoint": ["python3", "-I", "-S", "-c", watchdog], "Cmd": [], "WorkingDir": "/",
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"]},
            "Mounts": [{"Destination": "/source", "Source": str(source), "RW": False, "Type": "bind"},
                       {"Destination": "/workspace", "Name": volume, "RW": True, "Type": "volume"}]}
        container = SimpleNamespace(attrs=attrs, reload=lambda: None)
        self.assertTrue(trial.inspected_boundary(container, image, source, volume, watchdog, {})["private_pid_namespace"])
        for key, changed in (("CapAdd", ["SYS_PTRACE"]), ("Tmpfs", {}), ("Init", True), ("PidsLimit", 256)):
            container.attrs = deepcopy(attrs)
            container.attrs["HostConfig"][key] = changed
            with self.assertRaises(ValueError):
                trial.inspected_boundary(container, image, source, volume, watchdog, {})
        container.attrs = deepcopy(attrs)
        container.attrs["Config"]["Env"].append("PRIVATE=" + PRIVATE)
        with self.assertRaises(ValueError):
            trial.inspected_boundary(container, image, source, volume, watchdog, {})

    def test_cleanup_requires_exact_owned_resources_and_confirmed_disappearance(self):
        class Missing(Exception):
            pass

        labels = {"zpm.confined-suite": "owned"}
        container = Mock(id="c" * 64)
        container.attrs = {"Id": container.id, "Config": {"Labels": labels.copy()},
                           "State": {"Running": True, "Status": "running"}}
        container.kill.side_effect = lambda: container.attrs.update(State={"Running": False, "Status": "exited"})
        container.wait.return_value = {"StatusCode": 137}
        volume = Mock()
        volume.name = "owned-volume"
        volume.attrs = {"Name": volume.name, "Labels": labels.copy(), "CreatedAt": "fixed", "Driver": "local"}
        original = deepcopy(volume.attrs)
        client = SimpleNamespace(containers=Mock(), volumes=Mock())
        client.containers.get.side_effect = client.volumes.get.side_effect = Missing
        runtime = SimpleNamespace(_CLEANUP_TRANSPORT="qualified", ManagedDockerRuntime=SimpleNamespace(
            _cleanup_transport=lambda value: "qualified", _volume_identity=lambda value: deepcopy(value.attrs)))
        arguments = (client, runtime, Missing, container, volume, container.id, original, labels,
                     {"volume": True, "container": True})
        self.assertTrue(trial.cleanup_owned(*arguments))
        container.kill.assert_called_once_with()
        container.wait.assert_called_once_with(timeout=5)
        container.remove.assert_called_once_with(force=False, v=False)
        volume.remove.assert_called_once_with(force=False)
        client.containers.get.assert_called_once_with(container.id)
        client.volumes.get.assert_called_once_with(volume.name)
        container.reset_mock()
        volume.reset_mock()
        volume.attrs["CreatedAt"] = "replacement"
        self.assertFalse(trial.cleanup_owned(client, runtime, Missing, None, volume, None, original, labels,
                                           {"volume": True, "container": False}))
        volume.remove.assert_not_called()
        container.attrs["Config"]["Labels"] = {"zpm.confined-suite": "foreign"}
        self.assertFalse(trial.cleanup_owned(*arguments))
        container.remove.assert_not_called()

    def test_lost_create_reply_never_claims_cleanup_or_removes_other_resources(self):
        for intents in ({"volume": True, "container": False}, {"volume": False, "container": True}):
            client = Mock()
            self.assertFalse(trial.cleanup_owned(client, Mock(), Exception, None, None, None, None, {}, intents))
            self.assertEqual(client.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
