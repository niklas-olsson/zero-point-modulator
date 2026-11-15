"""Independent native11 faults. Fake Engine transport; no Docker or network.

Local Python children only exercise the existing command observer. These tests
do not qualify a native installation or establish any daemon-side failure cause.
"""
from contextlib import ExitStack, contextmanager
import json
import signal
import stat
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.test_alpine_sealed_runtime import Fixture


class DockerSamplingContextTests(Fixture):
    def trial(self):
        parent = self.root
        self._trial_number = getattr(self, "_trial_number", 0) + 1
        self.root = parent / ("trial-%d" % self._trial_number)
        self.root.mkdir()
        try:
            return super().trial()
        finally:
            self.root = parent

    def owned(self, *, stage="assembly"):
        trial = self.trial()
        row = {"stage": stage, "name": trial.owner + "-" + stage,
               "image": "sha256:" + "c" * 64, "mounts": [], "id": "d" * 64,
               "removed": False, "state": "present", "mutation_confirmed": True,
               "reserved_bytes": runtime.MUTATION_RESERVATIONS["container_writable_bytes"],
               "last_observed_bytes": None}
        trial.journal["containers"].append(row)
        trial.container = row["id"]
        return trial, row, self.container(trial, row)

    @contextmanager
    def control_signal(self, code):
        """Use the harness handler's original exception shape and real delivery."""
        number = signal.SIGTERM if code == "HOST_INTERRUPTED" else signal.SIGALRM
        previous = signal.getsignal(number)
        original = runtime.Refused(code)

        def interrupted(signum, frame):
            raise original

        signal.signal(number, interrupted)
        try:
            yield original, lambda: signal.raise_signal(number)
        finally:
            signal.signal(number, previous)

    @contextmanager
    def transport(self, respond, *, real_timer=False, fixed_clock=True):
        events = []

        class Response:
            def __init__(inner, value):
                inner.value = value

            def read(inner, bound):
                events.append(("READ", bound))
                return runtime.encoded(inner.value[1])

            @property
            def status(inner):
                events.append("STATUS")
                return inner.value[0]

        class Connection:
            def __init__(inner, host, timeout):
                events.append(("CONNECT", host, timeout))

            def request(inner, method, target, headers):
                inner.target = target
                events.append(("REQUEST", method, target, headers))

            def getresponse(inner):
                events.append("HEADERS")
                value = respond(inner.target)
                if isinstance(value, BaseException):
                    raise value
                return Response(value)

            def close(inner):
                events.append("CLOSE")

        @contextmanager
        def timer(deadline, code):
            events.append(("TIMER", deadline, code))
            yield

        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "UnixHTTP", Connection))
            if not real_timer:
                stack.enter_context(patch.object(runtime, "absolute_timeout", timer))
            if fixed_clock:
                stack.enter_context(patch.object(runtime.time, "monotonic", return_value=10))
            yield events

    @contextmanager
    def local_observer(self, trial):
        """Keep real sample/accounting flow; synthetic host process observations."""
        with patch.object(runtime, "process_snapshot", return_value={}), \
                patch.object(trial, "observed_processes", return_value=4096), \
                patch.object(runtime, "available_memory", return_value=8 * 1024**3), \
                patch.object(runtime, "logical_files", return_value=123), \
                patch.object(runtime.guest, "output_total", return_value=0), \
                patch.object(trial, "refresh_acquisition"):
            yield

    def sample_failure(self, trial, *, location="PRE_COMMIT", error=None):
        original = error if error is not None else runtime.Refused("DOCKER_DEADLINE")
        with self.local_observer(trial), self.transport(lambda _: original) as events:
            with self.assertRaises(runtime.Refused) as caught:
                trial.sample_at(location)
        self.assertIs(caught.exception, original)
        return original, events

    def test_size_request_original_deadline_counter_and_reservation_are_unchanged(self):
        trial, row, _ = self.owned()
        trial.work_deadline = 10.25
        trial.remember_prior_driver({"Driver": "overlay2"})
        error, events = self.sample_failure(trial)
        self.assertEqual(events, [
            ("CONNECT", "localhost", .25), ("TIMER", 10.25, "DOCKER_DEADLINE"),
            ("REQUEST", "GET", "/v1.45/containers/" + row["id"] + "/json?size=true",
             {"Connection": "close"}), "HEADERS", "CLOSE"])
        self.assertEqual(runtime._docker_observation(error),
                         {"endpoint": "CONTAINER_SIZE", "phase": "RESPONSE_HEADERS"})
        self.assertEqual(runtime._docker_sample_context(error),
                         {"location": "PRE_COMMIT", "owned_stage": "ASSEMBLY", "prior_driver": "OVERLAY2"})
        self.assertEqual(trial.record["resources"]["samples"], 1)
        self.assertEqual(trial.record["resources"]["pending_mutation_reservations"], row["reserved_bytes"])
        self.assertIsNone(row["last_observed_bytes"])
        self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_nested_and_failed_samples_restore_the_original_location(self):
        trial, _, _ = self.owned()
        real_sample = trial.sample
        first, second, third = [runtime.Refused("DOCKER_DEADLINE") for _ in range(3)]
        errors = iter((first, second, third))
        nested = False

        def sample():
            nonlocal nested
            if not nested:
                nested = True
                with self.assertRaises(runtime.Refused) as caught:
                    trial.sample_at("CLEANUP")
                self.assertIs(caught.exception, first)
            return real_sample()

        with self.local_observer(trial), self.transport(lambda _: next(errors)), \
                patch.object(trial, "sample", side_effect=sample):
            with self.assertRaises(runtime.Refused) as caught:
                trial.sample_at("PRE_COMMIT")
            self.assertIs(caught.exception, second)
            with self.assertRaises(runtime.Refused) as caught:
                trial.sample()
            self.assertIs(caught.exception, third)
        self.assertEqual([runtime._docker_sample_context(x)["location"] for x in (first, second, third)],
                         ["CLEANUP", "PRE_COMMIT", "UNAVAILABLE"])

    def test_optional_assignment_fault_after_store_restores_context(self):
        trial = self.trial()

        class InterruptedAssignment(type(trial)):
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if name == "_sample_location" and value == "PRE_COMMIT":
                    raise RuntimeError("fixture metadata fault after store")

        trial.__class__ = InterruptedAssignment
        result = object()
        with patch.object(trial, "sample", return_value=result) as sample:
            self.assertIs(trial.sample_at("PRE_COMMIT"), result)
        sample.assert_called_once_with()
        self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_original_control_signals_before_or_after_context_store_prevent_sampling(self):
        for code in ("HOST_INTERRUPTED", "HOST_TOTAL_DEADLINE"):
            for timing in ("before", "after"):
                with self.subTest(code=code, timing=timing):
                    trial = self.trial()
                    with self.control_signal(code) as (original, interrupt):
                        class InterruptedAssignment(type(trial)):
                            def __setattr__(self, name, value):
                                selected = name == "_sample_location" and value == "PRE_COMMIT"
                                if selected and timing == "before":
                                    interrupt()
                                super().__setattr__(name, value)
                                if selected and timing == "after":
                                    interrupt()

                        trial.__class__ = InterruptedAssignment
                        with patch.object(trial, "sample") as sample, self.assertRaises(runtime.Refused) as caught:
                            trial.sample_at("PRE_COMMIT")
                        self.assertIs(caught.exception, original)
                        sample.assert_not_called()
                        self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_other_base_controls_before_sampling_are_not_optional_diagnostics(self):
        for original in (KeyboardInterrupt("fixture"), SystemExit(3)):
            with self.subTest(kind=type(original).__name__):
                trial = self.trial()

                class InterruptedAssignment(type(trial)):
                    def __setattr__(self, name, value):
                        super().__setattr__(name, value)
                        if name == "_sample_location" and value == "PRE_COMMIT":
                            raise original

                trial.__class__ = InterruptedAssignment
                with patch.object(trial, "sample") as sample, self.assertRaises(type(original)) as caught:
                    trial.sample_at("PRE_COMMIT")
                self.assertIs(caught.exception, original)
                sample.assert_not_called()
                self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_control_during_restore_after_success_propagates_but_cannot_replace_an_original_failure(self):
        for body_failed in (False, True):
            for code in ("HOST_INTERRUPTED", "HOST_TOTAL_DEADLINE"):
                with self.subTest(body_failed=body_failed, code=code):
                    trial = self.trial()
                    body_error = runtime.Refused("CONTAINER_STORAGE_BOUND")
                    with self.control_signal(code) as (control, interrupt):
                        class InterruptedRestore(type(trial)):
                            def __setattr__(self, name, value):
                                super().__setattr__(name, value)
                                if name == "_sample_location" and value == "UNAVAILABLE":
                                    interrupt()

                        trial.__class__ = InterruptedRestore
                        with patch.object(trial, "sample", side_effect=body_error if body_failed else None) as sample:
                            with self.assertRaises(runtime.Refused) as caught:
                                trial.sample_at("PRE_COMMIT")
                        self.assertIs(caught.exception, body_error if body_failed else control)
                        sample.assert_called_once_with()
                        self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_actual_local_command_poll_preserves_context_and_output(self):
        trial, _, info = self.owned(stage="qualification")
        trial.remember_prior_driver({"Driver": "overlayfs"})
        seen = []

        def respond(target):
            seen.append(trial._sample_location)
            return 200, info

        with self.local_observer(trial), self.transport(respond, fixed_clock=False), \
                patch.object(trial, "register_child"):
            output = trial.run([sys.executable, "-c", "print('fixture-output')"],
                               seconds=3, sample_location="START_ATTACH_POLL")
        self.assertEqual(output, b"fixture-output\n")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"START_ATTACH_POLL"})
        self.assertEqual(trial._sample_location, "UNAVAILABLE")
        self.assertEqual(trial.aggregate_output, len(output))
        self.assertNotIn("docker_sample_context", trial.record)

    def test_real_outer_alarm_is_not_renewed_and_handler_is_restored(self):
        trial, _, _ = self.owned()
        before_handler = signal.getsignal(signal.SIGALRM)
        before_timer = signal.getitimer(signal.ITIMER_REAL)
        self.assertEqual(before_timer, (0.0, 0.0))

        def respond(_):
            time.sleep(1)
            self.fail("original alarm must interrupt the headers wait")

        before = time.monotonic()
        with self.local_observer(trial), self.transport(respond, real_timer=True, fixed_clock=False) as events:
            with self.assertRaisesRegex(runtime.Refused, "^DOCKER_DEADLINE$") as caught:
                with runtime.absolute_timeout(before + .04, "ORIGINAL_OUTER_DEADLINE"):
                    trial.sample_at("START_ATTACH_POLL")
        self.assertLess(time.monotonic() - before, .7)
        self.assertEqual([x for x in events if isinstance(x, tuple) and x[0] == "CONNECT"],
                         [("CONNECT", "localhost", 1.0)])
        self.assertEqual(events.count("CLOSE"), 1)
        self.assertEqual(runtime._docker_sample_context(caught.exception)["location"], "START_ATTACH_POLL")
        self.assertEqual(signal.getsignal(signal.SIGALRM), before_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), before_timer)
        self.assertEqual(trial._sample_location, "UNAVAILABLE")

    def test_actual_size_and_authority_refusals_still_stop_before_any_passing_sample(self):
        mutations = (
            (lambda info: info.update(SizeRw=runtime.MUTATION_RESERVATIONS["container_writable_bytes"] + 1),
             "CONTAINER_STORAGE_BOUND"),
            (lambda info: info.update(Id="e" * 64), "CONTAINER_STORAGE_IDENTITY"),
            (lambda info: info["HostConfig"].update(Privileged=True), "CONTAINER_LIMIT_OR_AUTHORITY"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                trial, row, info = self.owned()
                mutate(info)
                with self.local_observer(trial), self.transport(lambda _: (200, info)), \
                        self.assertRaises(runtime.Refused) as caught:
                    trial.sample_at("START_ATTACH_POLL")
                self.assertIn(expected, str(caught.exception))
                self.assertIsNone(runtime._docker_sample_context(caught.exception))
                self.assertIsNone(row["last_observed_bytes"])
                self.assertEqual(trial.record["resources"]["pending_mutation_reservations"], row["reserved_bytes"])

    def test_success_charges_full_reservation_and_does_not_emit_context(self):
        trial, row, info = self.owned()
        with self.local_observer(trial), self.transport(lambda _: (200, info)) as events:
            trial.sample_at("PRE_COMMIT")
        self.assertEqual(row["last_observed_bytes"], 100)
        self.assertEqual(trial.record["resources"]["peak_logical_owned_bytes"],
                         123 + row["reserved_bytes"] + runtime.FIXED_POLICY["final_report_reservation_bytes"])
        self.assertEqual(sum(isinstance(x, tuple) and x[0] == "REQUEST" for x in events), 1)
        self.assertEqual(events.count("CLOSE"), 1)
        self.assertNotIn("docker_sample_context", runtime.public_summary(trial.record))

    def test_unknown_data_and_diagnostic_faults_never_leak_or_replace_original_failure(self):
        trial, row, _ = self.owned()
        secret = "private-host:/private/socket?token=secret"
        trial.remember_prior_driver({"Driver": secret})
        original, _ = self.sample_failure(trial, location=secret)
        self.assertEqual(runtime._docker_sample_context(original),
                         {"location": "UNAVAILABLE", "owned_stage": "ASSEMBLY", "prior_driver": "OTHER"})
        raw = json.dumps(runtime._docker_sample_context(original))
        for forbidden in (secret, row["name"], row["id"], str(trial.root), "token"):
            self.assertNotIn(forbidden, raw)
        with patch.object(runtime, "_remember_docker_sample_context", side_effect=RuntimeError(secret)):
            other, _ = self.sample_failure(trial)
        self.assertIsNone(runtime._docker_sample_context(other))
        self.assertEqual(runtime._docker_observation(other)["endpoint"], "CONTAINER_SIZE")

    def test_exact_owned_row_stage_and_missing_values_are_closed(self):
        trial, row, _ = self.owned(stage="qualification")
        trial.journal["containers"].insert(0, {"removed": True, "stage": "assembly"})
        original, _ = self.sample_failure(trial)
        self.assertEqual(runtime._docker_sample_context(original)["owned_stage"], "QUALIFICATION")
        row["stage"] = "private-container-stage"
        other, _ = self.sample_failure(trial)
        self.assertEqual(runtime._docker_sample_context(other)["owned_stage"], "UNAVAILABLE")
        self.assertNotIn(row["stage"], json.dumps(runtime._docker_sample_context(other)))

    def test_failed_projection_freezes_unavailable_and_summary_rejects_mutable_private_values(self):
        trial, _, _ = self.owned()
        with patch.object(runtime, "_safe_docker_sample_context", side_effect=RuntimeError("private-path")):
            original, _ = self.sample_failure(trial)
        expected = dict.fromkeys(("location", "owned_stage", "prior_driver"), "UNAVAILABLE")
        self.assertEqual(runtime._docker_sample_context(original), expected)
        runtime._remember_docker_sample_context(original,
            {"location": "PRE_COMMIT", "owned_stage": "ASSEMBLY", "prior_driver": "OVERLAY2"})
        self.assertEqual(runtime._docker_sample_context(original), expected)
        trial.fail("DOCKER_DEADLINE", failure=original)
        for malicious in ({"location": "private-path"},
                          {"location": "PRE_COMMIT", "owned_stage": "ASSEMBLY", "prior_driver": "OVERLAY2", "path": "private-path"},
                          ["private-path"], "private-path" * 10000):
            trial.record["docker_sample_context"] = malicious
            summary = runtime.public_summary(trial.record)
            self.assertEqual(summary["docker_sample_context"], expected)
            self.assertNotIn("private-path", json.dumps(summary))

    def test_only_private_tagged_size_errors_can_acquire_context(self):
        value = {"location": "PRE_COMMIT", "owned_stage": "ASSEMBLY", "prior_driver": "OVERLAY2"}
        for path in ("/info", "/containers/" + "d" * 64 + "/json"):
            original = runtime.Refused("DOCKER_DEADLINE")
            with self.transport(lambda _: original), self.assertRaises(runtime.Refused):
                runtime.docker_json(path, deadline=11)
            runtime._remember_docker_sample_context(original, value)
            self.assertIsNone(runtime._docker_sample_context(original))
        unbound = runtime.Refused("DOCKER_DEADLINE")
        unbound.docker_observation = {"endpoint": "CONTAINER_SIZE", "phase": "RESPONSE_HEADERS"}
        unbound.docker_sample_context = value
        runtime._remember_docker_sample_context(unbound, value)
        self.assertIsNone(runtime._docker_sample_context(unbound))

    def test_first_failure_context_survives_cleanup_and_bounded_overflow(self):
        trial, _, _ = self.owned()
        trial.remember_prior_driver({"Driver": "overlay2"})
        first, _ = self.sample_failure(trial, location="START_ATTACH_POLL")
        trial.remember_prior_driver({"Driver": "overlayfs"})
        second, _ = self.sample_failure(trial, location="CLEANUP")
        original = runtime._docker_sample_context(first)
        self.assertEqual(original["prior_driver"], "OVERLAY2")
        self.assertEqual(runtime._docker_sample_context(second)["prior_driver"], "OVERLAYFS")
        trial.fail("DOCKER_DEADLINE", failure=first)
        with patch.object(trial, "remove_container", side_effect=second), \
                patch.object(trial, "cleanup_processes"), patch.object(trial, "sample"):
            trial.cleanup()
        self.assertEqual(trial.record["cleanup_failures"], ["DOCKER_DEADLINE"])
        self.assertEqual(runtime.public_summary(trial.record)["docker_sample_context"], original)
        trial.record["private_oversized_fixture"] = "sensitive" * runtime.CAPS["report_bytes"]
        report = json.loads(runtime.report_bytes(trial.record))
        self.assertTrue(report["truncated"])
        self.assertEqual(report["docker_sample_context"], original)
        self.assertNotIn("sensitive", json.dumps(report))
        returned = runtime._docker_sample_context(first)
        returned["location"] = "CLEANUP"
        runtime._remember_docker_sample_context(first, returned)
        self.assertEqual(runtime._docker_sample_context(first), original)

    def test_earlier_non_docker_failure_does_not_acquire_cleanup_context(self):
        trial, _, _ = self.owned()
        original, _ = self.sample_failure(trial)
        trial.fail("ORIGINAL_WORKLOAD_FAILURE")
        with patch.object(trial, "remove_container", side_effect=original), \
                patch.object(trial, "cleanup_processes"), patch.object(trial, "sample"):
            trial.cleanup()
        self.assertEqual(trial.record["first_failure"], "ORIGINAL_WORKLOAD_FAILURE")
        self.assertNotIn("docker_sample_context", runtime.public_summary(trial.record))

    def daemon_prerequisite(self, trial, daemon, *, check_sources=None):
        versions = {"Server": {"MinAPIVersion": "1.24", "ApiVersion": "1.48", "Version": "28.0.4"},
                    "Client": {"ApiVersion": "1.48", "Version": "28.0.4"}}

        def run(command, **kwargs):
            if command[-3:] == ["version", "--format", "{{json .}}"]:
                return runtime.encoded(versions)
            if command[-2:] == ["rev-parse", "HEAD"]:
                return ("b" * 40).encode()
            return b""

        with ExitStack() as stack:
            for target, name, value in (
                (runtime.platform, "system", "Linux"), (runtime.platform, "machine", "x86_64"),
                (runtime.os, "getuid", 1001), (runtime.os, "getgid", 1001),
                (runtime, "available_memory", 8 * 1024**3),
                (runtime.os, "stat", SimpleNamespace(st_mode=stat.S_IFSOCK)),
                (runtime, "source_git_metadata", None), (runtime, "source_tree_records", []),
                (runtime, "verify_source_files", 1), (trial, "sample", None),
            ):
                stack.enter_context(patch.object(target, name, return_value=value))
            stack.enter_context(patch.object(runtime.sys, "version_info", (3, 12, 0)))
            stack.enter_context(patch.object(trial, "run", side_effect=run))
            stack.enter_context(patch.object(trial, "check_sources", side_effect=check_sources or (lambda: 1)))
            with self.transport(lambda _: (200, daemon)) as events:
                trial.prerequisites("b" * 40)
        return events

    def test_control_during_driver_capture_prevents_source_checks_and_preserves_identity(self):
        daemon = {"OSType": "linux", "Architecture": "amd64", "MemoryLimit": True,
                  "SwapLimit": True, "PidsLimit": True, "DefaultRuntime": "runc", "Driver": "overlay2"}
        for code in ("HOST_INTERRUPTED", "HOST_TOTAL_DEADLINE"):
            for timing in ("before", "after"):
                with self.subTest(code=code, timing=timing):
                    trial = self.trial()
                    check_sources = Mock(return_value=1)
                    with self.control_signal(code) as (original, interrupt):
                        class InterruptedDriver(type(trial)):
                            def __setattr__(self, name, value):
                                selected = name == "_prior_docker_driver" and value == "OVERLAY2"
                                if selected and timing == "before":
                                    interrupt()
                                super().__setattr__(name, value)
                                if selected and timing == "after":
                                    interrupt()

                        trial.__class__ = InterruptedDriver
                        with self.assertRaises(runtime.Refused) as caught:
                            self.daemon_prerequisite(trial, daemon, check_sources=check_sources)
                        self.assertIs(caught.exception, original)
                        check_sources.assert_not_called()

    def test_prior_driver_comes_only_from_existing_validated_daemon_response(self):
        valid = {"OSType": "linux", "Architecture": "amd64", "MemoryLimit": True,
                 "SwapLimit": True, "PidsLimit": True, "DefaultRuntime": "runc", "Driver": "overlay2"}
        for change, failure in (({"OSType": "windows"}, "DOCKER_PLATFORM"),
                                ({"MemoryLimit": False}, "DOCKER_LIMIT_SUPPORT"),
                                ({"DefaultRuntime": "private-runtime"}, "DOCKER_RUNTIME")):
            with self.subTest(change=change):
                trial = self.trial()
                with self.assertRaisesRegex(runtime.Refused, failure):
                    self.daemon_prerequisite(trial, valid | change)
                self.assertEqual(trial._prior_docker_driver, "UNAVAILABLE")
        for driver, expected in (("overlay2", "OVERLAY2"), ("overlayfs", "OVERLAYFS"),
                                 ("private-backend", "OTHER"), (None, "UNAVAILABLE"),
                                 (False, "UNAVAILABLE"), ([], "UNAVAILABLE")):
            with self.subTest(driver=driver):
                trial = self.trial()
                events = self.daemon_prerequisite(trial, valid | {"Driver": driver})
                self.assertEqual(trial._prior_docker_driver, expected)
                self.assertEqual([x[2] for x in events if isinstance(x, tuple) and x[0] == "REQUEST"],
                                 ["/v1.45/info"])

    def test_actual_qualify_call_sites_separate_recovered_apk_results_from_sampling_location(self):
        # The three paths use real qualify/sample/HTTP/refusal flow. Acquisition,
        # daemon image metadata and guest execution are explicit fixtures.
        for location in ("START_ATTACH_POLL", "PRE_COMMIT", "COMMIT_POLL"):
            with self.subTest(location=location):
                trial = self.trial()
                trial.remember_prior_driver({"Driver": "overlay2"})
                base = "sha256:" + self.documents["inputs-v1.json"]["oci"]["config_sha256"]
                original = runtime.Refused("DOCKER_DEADLINE")
                containers = {}
                commands = []
                guest_reads = []

                def create(image, stage):
                    self.assertEqual(stage, "assembly")
                    row = {"stage": stage, "name": trial.owner + "-assembly", "image": image,
                           "mounts": [], "id": "d" * 64, "removed": False, "state": "present",
                           "mutation_confirmed": True,
                           "reserved_bytes": runtime.MUTATION_RESERVATIONS["container_writable_bytes"],
                           "last_observed_bytes": None}
                    trial.journal["containers"].append(row)
                    trial.container = row["id"]
                    info = self.container(trial, row)
                    info["State"] = {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False}
                    containers[row["id"]] = info
                    return row["id"]

                def run(command, *, seconds, sample_location):
                    commands.append(command[len(trial.docker)])
                    trial.sample_at(sample_location)
                    return b"" if commands[-1] == "start" else ("sha256:" + "e" * 64).encode()

                def guest_result(_, stage):
                    guest_reads.append(stage)
                    trial.record["native_checks"]["apk_transaction"] = "PASS"
                    trial.record["native_checks"]["installed_apk_inventory"] = "PASS"

                def respond(target):
                    if target == "/v1.45/images/" + base + "/json":
                        return 200, {"Id": base, "Size": runtime.MUTATION_RESERVATIONS["base_image_bytes"]}
                    if target.startswith("/v1.45/images/"):
                        return 404, {}
                    if target.endswith("/json?size=true") and trial._sample_location == location:
                        return original
                    if target.startswith("/v1.45/containers/"):
                        return 200, containers["d" * 64]
                    self.fail("unexpected fixture request")

                with self.local_observer(trial), self.transport(respond), \
                        patch.object(trial, "prerequisites"), patch.object(trial, "fork_stage"), \
                        patch.object(trial, "create", side_effect=create), patch.object(trial, "run", side_effect=run), \
                        patch.object(runtime, "assert_base"), patch.object(runtime, "native_guest_result", side_effect=guest_result), \
                        patch.object(runtime, "finish_trial"):
                    self.assertEqual(runtime.qualify(trial), 1)
                self.assertEqual(trial.record["first_failure"], "DOCKER_DEADLINE")
                self.assertEqual(trial.record["docker_sample_context"],
                                 {"location": location, "owned_stage": "ASSEMBLY", "prior_driver": "OVERLAY2"})
                self.assertEqual(trial.record["native_checks"]["apk_transaction"], "PASS")
                self.assertEqual(trial.record["native_checks"]["installed_apk_inventory"], "PASS")
                self.assertEqual(guest_reads, ["assembly"] if location == "START_ATTACH_POLL" else ["assembly", "assembly"])
                self.assertEqual(commands, ["start", "commit"] if location == "COMMIT_POLL" else ["start"])
                minimum = (runtime.MUTATION_RESERVATIONS["container_writable_bytes"]
                           + runtime.MUTATION_RESERVATIONS["assembled_image_bytes"])
                peak = trial.record["resources"]["peak_logical_owned_bytes"]
                if location == "COMMIT_POLL":
                    self.assertGreaterEqual(peak, minimum)
                else:
                    self.assertLess(peak, minimum)
