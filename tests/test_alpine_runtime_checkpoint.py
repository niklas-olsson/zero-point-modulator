"""Pure source/admission checks, not native candidate execution evidence."""
import contextlib
import io
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.contracts import alpine_sealed_guest as guest


class RuntimeCheckpointTests(unittest.TestCase):
    def test_historical_plans_keep_their_bytes_and_refuse_before_source_or_effects(self):
        historical = {
            1: "8917e1b695e7154c9ecd60f40ab2defcd691ca715da81ae8b23b5dc60ebcb953",
            2: "bc4e5168483bdf372af334a14f6e3e0bc1b58850bd026262cce834274028bced",
            3: "6da9d7727ae89c35ac9d26c6437a5a3c549efb07961d43309cf619903520ea72",
            4: "929ffd1c1b6b8bcd471673e0a8fdc1b78b3a6809aa96618411a1008399c074c0",
            5: "017c6ffb7c0a15b8efbc3046da53e3978d45f9b3bb8bda1d264d579f638575cf",
            6: "11907aa13d2300c092d69ff02cf7e1ce8c17aa38c414ec71bdd9b6ca284c55d6",
            7: "d87dba65bad619979dd704a4e1035acfb5c7383ec6fe0c795cd34d9fc55a3973",
            8: "07788eb17025b3eb73fffc6da808b6512949325359ea1ba4a4218ddeed8644d2",
            9: "767dd8998d2e65448ce7ec9f63689c7665c280142da511da29059032127b6b8d",
            10: "8ce3ef40b09add5f2371c026229d95f80c51d06e13e86bdfcc7d872650053440",
            11: "92c4d05be1388e058054d7bb45f90d9dcd4f272bb448db853a6f8819ee908645",
        }
        for number, reviewed in historical.items():
            path = candidate.ROOT / f"active-plan-{number}.json"
            with self.subTest(attempt=number):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), reviewed)
                with patch.object(runtime, "source_blob", side_effect=AssertionError("code read")), \
                        patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                        patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")), \
                        patch.object(Path, "mkdir", side_effect=AssertionError("write")), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    code = runtime.main(["--check-plan", "--plan", str(path),
                                         "--reviewed-plan-sha256", reviewed])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output.getvalue())["failure"], "ACTIVE_PLAN_IDENTITY")

    def test_prospective_binding_changes_only_response_custody_and_workflow(self):
        old = json.loads((candidate.ROOT / "active-plan-11.json").read_bytes())
        current = json.loads(runtime.ACTIVE_PLAN.read_bytes())
        self.assertEqual(runtime.ACTIVE_PLAN.name, "active-plan-12.json")
        self.assertEqual(current["attempt"], "native-alpine-12")
        self.assertEqual(set(current), set(old))
        for field in set(old) - {"attempt", "code"}:
            with self.subTest(field=field):
                self.assertEqual(current[field], old[field])
        self.assertEqual(set(current["code"]), set(old["code"]))
        self.assertEqual(len(current["code"]), 11)
        changed = {path for path in old["code"] if old["code"][path] != current["code"][path]}
        self.assertEqual(changed, {"tests/contracts/check_alpine_sealed_runtime.py",
                                   ".github/workflows/native-alpine-candidate.yml"})

    def test_tenth_attempt_retains_original_failure_without_later_context(self):
        root = runtime.REPO / "docs/native-alpine-evidence/attempt-10"
        raw = (root / "summary.json").read_bytes()
        self.assertEqual(len(raw), 2219)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "985a8538ced06dc827e9dd3505d55a67af0b9cf401c266b12b02385584183a4a")
        original = json.loads(raw)
        self.assertEqual(original["attempt"], "native-alpine-10")
        self.assertEqual(original["first_failure"], "DOCKER_DEADLINE")
        self.assertEqual(original["docker_observation"], {"endpoint": "CONTAINER_SIZE", "phase": "RESPONSE_HEADERS"})
        self.assertNotIn("docker_sample_context", original)
        self.assertNotIn("oci_redirect_hostname_class", original)
        self.assertFalse(original["native_execution_qualified"])
        self.assertEqual(original["acquisition"]["artifacts_complete"], 173)
        self.assertEqual(original["cleanup_failures"], [])
        custody = json.loads((root / "run.json").read_bytes())
        self.assertEqual(custody["source"]["commit"], original["harness_source_revision"])
        self.assertEqual(custody["source"]["plan_sha256"], original["plan_sha256"])
        self.assertEqual(custody["outcome"]["particular_container_and_stage"], "not-identified")

    def test_ninth_attempt_evidence_keeps_original_bytes_and_unknown_target(self):
        root = runtime.REPO / "docs/native-alpine-evidence/attempt-9"
        raw = (root / "summary.json").read_bytes()
        self.assertEqual(len(raw), 2069)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "96a8b0551a36d042dcc1854a368742b885ad68f7795d82ee07b0cf2ae3cef418")
        original = json.loads(raw)
        self.assertEqual(original["attempt"], "native-alpine-9")
        self.assertEqual(original["first_failure"], "OCI_REDIRECT_REFUSED")
        self.assertEqual(original["oci_redirect_predicate"], "HOSTNAME")
        self.assertNotIn("oci_redirect_hostname_class", original)
        self.assertFalse(original["native_execution_qualified"])
        custody = json.loads((root / "run.json").read_bytes())
        self.assertEqual(custody["source"]["commit"], original["harness_source_revision"])
        self.assertEqual(custody["source"]["plan_sha256"], original["plan_sha256"])
        self.assertEqual(custody["outcome"]["actual_redirect_hostname"], "not-retained")
        self.assertEqual(custody["outcome"]["later_redirect_predicates"], "not-evaluated")

    def test_plan_workflow_work_artifact_and_actual_report_select_the_same_attempt(self):
        plan = json.loads(runtime.ACTIVE_PLAN.read_bytes())
        attempt = plan["attempt"]
        workflow = (runtime.REPO / ".github/workflows/native-alpine-candidate.yml").read_text()
        self.assertIn("sha256sum tests/contracts/native-alpine/" + runtime.ACTIVE_PLAN.name, workflow)
        self.assertIn('--work "$RUNNER_TEMP/' + attempt + '"', workflow)
        self.assertIn("name: " + attempt + "-summary-${{ github.run_id }}-${{ github.run_attempt }}", workflow)
        self.assertIn("path: ${{ runner.temp }}/" + attempt + "/summary.json", workflow)
        self.assertNotIn("native-alpine-10", workflow)
        documents = {name: json.loads((candidate.ROOT / name).read_bytes()) for name in candidate.METADATA}
        reviewed = hashlib.sha256(runtime.ACTIVE_PLAN.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")):
            root = Path(temporary)
            trial = runtime.Trial(root, {name: root / name for name in candidate.SOURCES},
                                  reviewed, "b" * 40, documents)
            retained = json.loads((root / "private-report.json").read_bytes())
            self.assertEqual(trial.record["attempt"], attempt)
            self.assertEqual(retained["attempt"], attempt)
            self.assertEqual(retained["plan_sha256"], reviewed)
            self.assertFalse(retained["native_execution_qualified"])
            self.assertEqual(set(retained["native_checks"].values()), {"NOT_RUN"})

    def test_cold_host_import_closure_is_bound_without_effects(self):
        script = r'''
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
def no_effects(event, args):
    if event in {"socket.__new__", "socket.connect", "socket.bind", "subprocess.Popen",
                 "os.system", "os.posix_spawn", "os.fork", "os.mkdir"}:
        raise AssertionError("effect during cold host import: " + event)
    if event == "open" and len(args) > 2 and isinstance(args[2], int):
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("write during cold host import")
sys.addaudithook(no_effects)
sys.path.insert(0, str(root))
from tests.contracts import check_alpine_sealed_runtime
paths = set()
for value in tuple(sys.modules.values()):
    name = getattr(value, "__file__", None)
    if name:
        path = Path(name).resolve()
        if path.is_relative_to(root):
            paths.add(path.relative_to(root).as_posix())
print(json.dumps(sorted(paths)))
'''
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(runtime.REPO)],
                                capture_output=True, text=True, timeout=10, check=True)
        observed = set(json.loads(result.stdout))
        self.assertIn("zpm/installation_roles.py", observed)
        self.assertLessEqual(observed, set(runtime.CODE_PATHS))
        plan = json.loads(runtime.ACTIVE_PLAN.read_bytes())
        self.assertLessEqual(observed, set(plan["code"]))

    def test_changed_imported_role_selector_refuses_exact_source_admission(self):
        reviewed = hashlib.sha256(runtime.ACTIVE_PLAN.read_bytes()).hexdigest()
        path = runtime.REPO / "zpm/installation_roles.py"
        original = path.read_bytes()
        altered = original[:-1] + (b" " if original[-1:] != b" " else b"\n")
        real = runtime.source_blob
        def changed(source, **kwargs):
            return altered if source == path else real(source, **kwargs)
        with patch.object(runtime, "source_blob", side_effect=changed), \
                patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("child")), \
                patch.object(runtime.socket, "socket", side_effect=AssertionError("socket")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("write")), \
                self.assertRaisesRegex(candidate.Refused, "ACTIVE_CODE_IDENTITY"):
            runtime.admit_plan(runtime.ACTIVE_PLAN, reviewed)

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
