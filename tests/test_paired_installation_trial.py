"""Installed trial assertions remain closed and cannot authorize services."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests import test_deployment

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_paired_installation_preflight as trial
finally:
    sys.path.pop(0)


def safe_report():
    return {
        "schema_version": 1, "receipt_contract": trial.CONTRACT,
        "receipt_id": "0323b08b-2ce1-4ff7-9d85-3068bd5ae1aa",
        "phase": "native-config-validated", "compatible": True,
        "source_identity_verified": True, "native_validation_performed": True,
        "live_capabilities_checked": False, "execution_qualified": False,
        "services_started": False, "components": {
            "openjules": {"status": "valid"}, "keystone-polyphony": {"status": "valid"},
        }, "errors": [],
    }


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


class PairedTrialTests(unittest.TestCase):
    def test_success_report_has_exact_safe_fields_and_nonclaims(self):
        value = safe_report()
        self.assertEqual(trial.accepted_report(encode(value)), value)
        for field, changed in (("schema_version", True), ("compatible", 1),
                               ("services_started", True), ("execution_qualified", True),
                               ("live_capabilities_checked", True), ("receipt_id", "/private/path"),
                               ("errors", [{"owner": "private-canary", "code": "secret"}]),
                               ("native_validation_performed", False), ("private-extra", "canary")):
            mutated = deepcopy(value)
            mutated[field] = changed
            with self.subTest(field=field), self.assertRaises(AssertionError):
                trial.accepted_report(encode(mutated))

    def test_report_parser_rejects_duplicate_nonfinite_and_oversized_json(self):
        for raw in (encode(safe_report())[:-1] + b',"compatible":true}',
                    b'{"receipt_id":NaN}', b'\xff', b'x' * 16385):
            with self.subTest(raw=raw[:30]), self.assertRaises(AssertionError):
                trial.accepted_report(raw)

    def test_unsupported_report_distinguishes_valid_native_receipts_from_compatibility(self):
        value = safe_report()
        value.update(phase="native-config-refused", compatible=None, source_identity_verified=False,
                     errors=[{"owner": "zpm", "code": "required_peer_unvalidated"}])
        self.assertEqual(trial.unsupported_report(b"bounded package output\n" + encode(value) + b"\n"), value)
        for field, changed in (("compatible", False), ("native_validation_performed", False),
                               ("components", {"openjules": {"status": "valid"}}),
                               ("private-extra", "private-canary")):
            mutated = deepcopy(value)
            mutated[field] = changed
            with self.subTest(field=field), self.assertRaises(AssertionError):
                trial.unsupported_report(encode(mutated))

    def test_profile_and_base_refusal_reports_require_both_actual_native_statuses(self):
        for code in ("scope_mismatch", "base_mismatch"):
            value = safe_report()
            value.update(phase="native-config-refused", compatible=None, source_identity_verified=False,
                         errors=[{"owner": "zpm", "code": code}])
            self.assertEqual(trial.refused_report(encode(value), code), value)
            value["components"]["keystone-polyphony"]["status"] = "unvalidated"
            with self.subTest(code=code), self.assertRaises(AssertionError):
                trial.refused_report(encode(value), code)

    def test_repository_refusal_requires_native_invalid_and_unperformed_peer(self):
        value = safe_report()
        value.update(phase="native-config-refused", compatible=None, source_identity_verified=False,
                     components={"openjules": {"status": "invalid"}, "keystone-polyphony": {"status": "unvalidated"}},
                     errors=[{"owner": "openjules", "code": "producer_invalid"}])
        self.assertEqual(trial.refused_report(encode(value), "producer_invalid"), value)
        for service in ("openjules", "keystone-polyphony"):
            changed = deepcopy(value)
            changed["components"][service]["status"] = "valid"
            with self.subTest(service=service), self.assertRaises(AssertionError):
                trial.refused_report(encode(changed), "producer_invalid")

    def test_prebootstrap_snapshot_needs_no_selector_and_detects_journal_or_configuration_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            for name in ("config", "secrets", "state"):
                (bundle / name).mkdir(mode=0o700)
            for name in ("manifest.json", "manage.py", "config/runtime.json", "state/zpm.sqlite3"):
                (bundle / name).write_bytes(b"original fixture bytes")
            original = trial.configuration_state_snapshot(bundle)
            (bundle / "state/installation-admission-status.json").write_bytes(b"safe status fixture")
            (bundle / "state/stack.lock").touch()
            self.assertEqual(trial.configuration_state_snapshot(bundle), original)
            (bundle / "state/zpm.sqlite3-wal").write_bytes(b"new journal")
            self.assertNotEqual(trial.configuration_state_snapshot(bundle), original)
            (bundle / "state/zpm.sqlite3-wal").unlink()
            (bundle / "config/runtime.json").write_bytes(b"changed fixture bytes")
            self.assertNotEqual(trial.configuration_state_snapshot(bundle), original)

    def test_start_audit_guards_block_actual_service_process_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "manager.py"
            marker = root / "unexpected-service"
            script.write_text("import subprocess,sys\nsubprocess.run([sys.executable,'-c'," +
                              repr("from pathlib import Path;Path(" + repr(str(marker)) + ").touch()") + "])\n")
            result = subprocess.run([sys.executable, "-I", "-B", "-c", trial.START_GUARD, str(script)],
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"paired_trial_unexpected_service_start", result.stdout)
            self.assertFalse(marker.exists())
            script.write_text("import subprocess,sys\nsubprocess.run([sys.executable,'-m','src.worker.managed_service'])\n")
            result = subprocess.run([sys.executable, "-I", "-B", "-c", trial.NATIVE_START_GUARD,
                                     json.dumps({"context": [], "commands": {}}), str(script)],
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"paired_trial_unexpected_service_start", result.stdout)

    def test_native_wrapper_cannot_substitute_another_module_or_cli_operation(self):
        from zpm import installation_preflight

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script, marker = root / "manager.py", root / "unexpected-service"
            (root / "wrong_module.py").write_text("from pathlib import Path;Path(" + repr(str(marker)) + ").touch()\n")
            permitted = {"context": [], "probe": installation_preflight._PROBE,
                         "native": installation_preflight._NATIVE, "commands": {
                             sys.executable: {"checkout": str(root), "package": "fixture",
                                              "module": "approved_cli", "command": "approved_cli",
                                              "verb": "validate-installation"}}}
            for module, verb in (("wrong_module", "validate-installation"), ("approved_cli", "start")):
                command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(root / "cache"),
                           "-c", installation_preflight._NATIVE, str(root), module, verb]
                script.write_text("import subprocess\nsubprocess.run(" + repr(command) + ")\n")
                result = subprocess.run([sys.executable, "-I", "-B", "-c", trial.NATIVE_START_GUARD,
                                         json.dumps(permitted), str(script)], capture_output=True, timeout=10)
                with self.subTest(module=module, verb=verb):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"paired_trial_unexpected_service_start", result.stdout)
                    self.assertFalse(marker.exists())
            (root / "approved_cli.py").write_text("print('validation-only-fixture')\n")
            command[-2:] = ["approved_cli", "validate-installation"]
            script.write_text("import subprocess\nsubprocess.run(" + repr(command) + ",check=True)\n")
            result = subprocess.run([sys.executable, "-I", "-B", "-c", trial.NATIVE_START_GUARD,
                                     json.dumps(permitted), str(script)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), b"validation-only-fixture")


class FreshMismatchPreparationTests(unittest.TestCase):
    setUp = test_deployment.DeploymentTests.setUp
    tearDown = test_deployment.DeploymentTests.tearDown

    def test_initial_generation_fault_is_sealed_once_without_changing_recipe_or_prior_bundle(self):
        from zpm import deployment, installation_admission

        recipe = deepcopy(self.config) | {"installation_admission": trial.CONTRACT}
        original = deepcopy(recipe)
        ids = []
        for case in ("repository", "profile", "base"):
            destination = self.root / ("mismatch-" + case)
            trial.prepare_mismatched_pair(recipe, destination, case)
            manifest = json.loads((destination / "manifest.json").read_bytes())
            intent = json.loads((destination / "config/installation-admission.json").read_bytes())
            runtime = json.loads((destination / "config/openjules.json").read_bytes())
            native = runtime["profiles"][0]
            ids.append(intent["receipt_id"])
            self.assertEqual(recipe, original)
            self.assertTrue(deployment.verify(destination)["valid"])
            self.assertEqual(intent["profiles"][0]["profile"], original["binding"]["profile"])
            self.assertEqual(intent["profiles"][0]["base"], original["source"]["revision"])
            self.assertEqual(manifest["configuration"]["source"]["revision"], original["source"]["revision"])
            self.assertEqual(intent["components"]["openjules"]["input_digests"]["configuration"],
                             installation_admission._hash((destination / "config/openjules.json").read_bytes()))
            if case == "repository":
                self.assertNotEqual(native["repository"], intent["profiles"][0]["repository"])
                self.assertEqual(native["name"], intent["profiles"][0]["profile"])
                self.assertEqual(native["base_commit"], intent["profiles"][0]["base"])
                declared = json.loads((destination / "config/installation-scopes.json").read_bytes())
                self.assertEqual(declared["scopes"][0]["repository"], intent["profiles"][0]["repository"])
            elif case == "profile":
                self.assertNotEqual(native["name"], intent["profiles"][0]["profile"])
                self.assertEqual(native["base_commit"], intent["profiles"][0]["base"])
            else:
                self.assertEqual(native["name"], intent["profiles"][0]["profile"])
                self.assertNotEqual(native["base_commit"], intent["profiles"][0]["base"])
            self.assertEqual(list((destination / "environments").iterdir()), [])
            self.assertFalse((destination / "state/installation-admission.json").exists())
            selected = (destination / "config/installation-admission.json").read_bytes()
            with self.assertRaisesRegex(AssertionError, "fresh_mismatch_required"):
                trial.prepare_mismatched_pair(recipe, destination, case)
            self.assertEqual((destination / "config/installation-admission.json").read_bytes(), selected)
        self.assertEqual(len(set(ids)), 3)


if __name__ == "__main__":
    unittest.main()
