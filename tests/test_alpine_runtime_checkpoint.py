"""Pure source/admission checks, not native candidate execution evidence."""
import contextlib
import io
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.contracts import alpine_sealed_guest as guest


class RuntimeCheckpointTests(unittest.TestCase):
    def test_published_active_plan_is_only_data_without_an_explicit_run(self):
        reviewed = hashlib.sha256(runtime.ACTIVE_PLAN.read_bytes()).hexdigest()
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("write")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runtime.main(["--check-plan", "--reviewed-plan-sha256", reviewed])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["native_execution"], "NOT_RUN")

    def test_missing_review_hash_refuses_before_effects(self):
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("write")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = runtime.main(["--run", "--reviewed-plan-sha256", "invalid"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["failure"], "REVIEWED_PLAN_REQUIRED")

    def test_run_reference_cannot_override_an_explicit_review_hash(self):
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("write")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runtime.main(["--run", "--reviewed-plan-sha256", "a" * 64,
                                 "--run-ref", runtime.RUN_PREFIX + "b" * 64])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["failure"], "REVIEW_REFERENCE_DISAGREEMENT")
        self.assertEqual(runtime.reviewed_identity("", runtime.RUN_PREFIX + "a" * 64), "a" * 64)
        for suffix in ("*", "a" * 64 + " ", "A" * 64, "a" * 64 + "/extra"):
            with self.subTest(suffix=suffix), self.assertRaises(candidate.Refused):
                runtime.reviewed_identity("a" * 64, runtime.RUN_PREFIX + suffix)

    def test_workflow_retry_refuses_before_plan_reads_or_effects(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), \
                patch.object(runtime, "admit_plan", side_effect=AssertionError("must refuse first")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runtime.main(["--run", "--reviewed-plan-sha256", "a" * 64])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["failure"], "AUTOMATIC_RERUN_REFUSED")

    def test_immutable_caps_and_producer_inputs_are_reused(self):
        self.assertIs(runtime.CAPS, candidate.CAPS)
        summary = candidate.load_bundle(candidate.ROOT, runtime.ORIGINAL_PLAN)
        self.assertEqual(summary["binary_artifacts"], 173)
        self.assertEqual(summary["expected_installed_apk_records"], 64)

    def test_reparented_observed_child_remains_in_cohort(self):
        rows = {10: {"start": 100, "ppid": 1, "rss": 5},
                20: {"start": 200, "ppid": 1, "rss": 7},
                30: {"start": 300, "ppid": 20, "rss": 11}}
        retained, rss = runtime.reconcile_processes(rows, {10: 100, 20: 200}, (10, 100))
        self.assertEqual(retained, {10: 100, 20: 200, 30: 300})
        self.assertEqual(rss, 23)

    def test_reused_pid_is_not_treated_as_departed(self):
        rows = {10: {"start": 100, "ppid": 1, "rss": 5},
                20: {"start": 201, "ppid": 1, "rss": 7}}
        with self.assertRaisesRegex(candidate.Refused, "OWNED_PID_REUSED"):
            runtime.reconcile_processes(rows, {20: 200}, (10, 100))

    def test_missing_child_is_absent_but_missing_runner_refuses(self):
        rows = {10: {"start": 100, "ppid": 1, "rss": 5}}
        self.assertEqual(runtime.reconcile_processes(rows, {20: 200}, (10, 100))[0], {10: 100})
        with self.assertRaisesRegex(candidate.Refused, "RUNNER_IDENTITY"):
            runtime.reconcile_processes({}, {}, (10, 100))

    def test_only_fixed_credential_free_oci_redirect_origin(self):
        original = runtime.REGISTRY + "sha256:" + "a" * 64
        good = "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/aa/data?opaque=private"
        self.assertEqual(runtime.redirect_url(good, original), good)
        for url in (good.replace("https:", "http:"), good.replace("production.cloudfront.docker.com", "other.example"),
                    good.replace("https://", "https://credential@"), good + "#fragment"):
            with self.assertRaises(candidate.Refused):
                runtime.redirect_url(url, original)

    def test_live_missing_container_stats_refuse(self):
        with self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_MISSING"):
            runtime.assert_stats({"id": "a" * 64}, "a" * 64, True)

    def test_empty_live_stats_are_not_zero_usage(self):
        with self.assertRaises(candidate.Refused):
            runtime.assert_stats({}, "a" * 64, True)

    def test_native_inventory_retains_virtual_package(self):
        recipe = json.loads((candidate.ROOT / "inputs-v1.json").read_bytes())
        expected = guest.expected_apks(recipe)
        self.assertEqual(len(expected), 64)
        self.assertEqual(expected[".python-rundeps"]["version"], "20261105.193119")
        self.assertEqual(len(expected[".python-rundeps"]["dependencies"]), 16)

    def test_guest_plan_requires_actual_complete_interpreter_identity(self):
        recipe = json.loads((candidate.ROOT / "inputs-v1.json").read_bytes())
        with self.assertRaises(guest.GuestRefused):
            guest.dependency_plan(recipe, {"implementation": "cpython", "version": "3.12.16"})
        identity = {"implementation": "cpython", "version": "3.12.16", "cache_tag": "synthetic",
                    "abi": "synthetic", "platform": "linux-synthetic"}
        plan = guest.dependency_plan(recipe, identity)
        self.assertEqual(plan["interpreter"], identity)
        self.assertEqual({name: len(row["wheels"]) for name, row in plan["environments"].items()},
                         {"zpm": 3, "openjules": 83, "polyphony": 70})

    def test_binding_stays_zero_capacity_without_execution(self):
        recipe = json.loads((candidate.ROOT / "inputs-v1.json").read_bytes())
        config = guest.fixture_configuration(recipe, {"synthetic": True}, "sha256:" + "a" * 64)
        self.assertEqual(config["binding"]["desired_workers"], 0)
        self.assertEqual(config["command"], ["/bin/false"])
        self.assertEqual(config["policy"]["capabilities"], [])


if __name__ == "__main__":
    unittest.main()
