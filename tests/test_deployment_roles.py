"""Role custody, exact producer inputs and conservative installation recovery."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest

from tests import test_approvals, test_deployment
from zpm import deployment, deployment_roles, role_manager
from zpm.models import ConfigurationError
from zpm.store import Store


class RoleDeploymentTests(unittest.TestCase):
    def setUp(self):
        test_deployment.DeploymentTests.setUp(self)
        self.binding = self.config["binding"] | {"repository": "github:github.com/owner/project"}
        self.control = {"schema_version": 3, "role": "controller-authority", "deployment_id": "role-test",
            "sources": {name: self.sources[name] for name in ("zpm", "polyphony")}, "binding": self.binding,
            "base_commit": self.sources["source"]["revision"], "policy": self.config["policy"], "max_workers": 2,
            "runtime_principals": [{"identity": "runtime-a", "token_ref": "env:ZPM_INSTALL_RUNTIME_A"},
                                   {"identity": "runtime-b", "token_ref": "env:ZPM_INSTALL_RUNTIME_B"}],
            "destinations": [{"destination_id": "host-a", "token_ref": "env:ZPM_INSTALL_LIFECYCLE_A"},
                             {"destination_id": "host-b", "token_ref": "env:ZPM_INSTALL_LIFECYCLE_B"}]}
        self.execution = {"schema_version": 3, "role": "execution", "deployment_id": "role-test",
            "sources": {name: self.sources[name] for name in ("zpm", "openjules")}, "binding": self.binding,
            "source": self.sources["source"], "image": self.config["image"], "command": self.config["command"],
            "runtime": {"root_timeout_ms": 60000}, "destination_id": "host-a", "worker_ceiling": 1,
            "authority_endpoint": "http://127.0.0.1:8766", "runtime_principal": "runtime-a",
            "runtime_token_ref": "env:ZPM_INSTALL_RUNTIME_A", "lifecycle_token_ref": "env:ZPM_INSTALL_LIFECYCLE_A"}
        self.tokens = {"ZPM_INSTALL_RUNTIME_A": "a" * 48, "ZPM_INSTALL_RUNTIME_B": "b" * 48,
                       "ZPM_INSTALL_LIFECYCLE_A": "c" * 48, "ZPM_INSTALL_LIFECYCLE_B": "d" * 48}

    def tearDown(self):
        test_deployment.DeploymentTests.tearDown(self)

    def prepare(self, configuration):
        with patch.dict(os.environ, self.tokens):
            report = deployment.prepare(configuration, self.bundle)
        return report, json.loads((self.bundle / "manifest.json").read_text())["configuration"]

    def read(self, name):
        return json.loads((self.bundle / name).read_text())

    def inspection(self, *, incarnation="a" * 32, ready=False):
        binding = {"destination_id": "host-a", "journal_incarnation": incarnation,
                   "policy_binding_id": "b" * 32, "policy_binding_version": 1}
        return {"schema_version": 2, "destination_id": "host-a", "journal_incarnation": incarnation,
                "identity_binding": 1, "portable_manifest": 1, "local_admission": 1,
                "journals_initialized": ready, "profiles": [{"deployment_id": "role-test",
                    "repository": self.binding["repository"], "profile": self.binding["profile"],
                    "destination_binding": binding, "current_policy_matches": True}]}

    def native_files(self):
        native = self.bundle / "state/openjules/native"
        native.mkdir(mode=0o700, exist_ok=True)
        for path in (native / "destination.json", native.parent / "lifecycle.sqlite3"):
            path.write_bytes(b"fixture retained producer state\n")
            path.chmod(0o600)

    def initialized(self):
        _, data = self.prepare(self.execution)
        def initialize(*args):
            self.assertTrue((self.bundle / role_manager.INIT_INTENT).is_file())
            self.native_files()
            return self.inspection()
        with patch.object(role_manager, "_native", side_effect=initialize):
            role_manager.initialize(self.bundle, data)
        return data

    def test_controller_prepares_only_authority_and_controller_custody_offline(self):
        with patch("subprocess.Popen", wraps=deployment.subprocess.Popen):
            report, data = self.prepare(self.control)
        self.assertEqual(report["services"], ["zpm", "polyphony"])
        self.assertEqual(report["next"], ["python3 manage.py bootstrap", "python3 manage.py start --authority-only"])
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        self.assertFalse((self.bundle / "state/openjules").exists())
        self.assertFalse((self.bundle / "config/openjules.json").exists())
        self.assertEqual(set(self.read("secrets/environment.json")), {"ZPM_INSTALL_LIFECYCLE_A", "ZPM_INSTALL_LIFECYCLE_B"})
        credentials = self.read("secrets/polyphony.json")
        self.assertEqual({row["identity"] for row in credentials.values() if row["role"] == "runtime"}, {"runtime-a", "runtime-b"})
        self.assertNotIn("ZPM_INSTALL_RUNTIME_A", deployment._environment(self.bundle, "zpm", data))
        self.assertNotIn("ZPM_INSTALL_LIFECYCLE_A", deployment._environment(self.bundle, "polyphony", data))
        self.assertEqual(self.read("config/registry.json")["destinations"], [])
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.format_version, 2)
            self.assertFalse(store.registry_status()["execution_enabled"])
            self.assertEqual(store.bindings()[0]["desired_workers"], 0)
            self.assertEqual(store.workers(), [])
        self.assertTrue((self.bundle / "state/prepared-v1.sqlite3").is_file())

    def test_execution_has_only_scoped_pairwise_secrets_and_no_authority_state(self):
        report, data = self.prepare(self.execution)
        self.assertEqual(report["services"], ["zpm", "openjules"])
        self.assertIn("python3 manage.py initialize-destination", report["next"])
        self.assertNotIn("python3 manage.py start", report["next"])
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        self.assertFalse((self.bundle / "state/polyphony").exists())
        self.assertFalse((self.bundle / "state/zpm.sqlite3").exists())
        self.assertEqual(set(path.name for path in (self.bundle / "secrets").iterdir()), {"environment.json", "openjules.json"})
        self.assertEqual(self.read("secrets/environment.json"), {"ZPM_INSTALL_RUNTIME_A": "a" * 48})
        native = self.read("config/openjules.json")
        self.assertEqual(native["schema_version"], 2)
        self.assertEqual(native["execution_contract"]["initial_worker_ceiling"], 1)
        self.assertEqual(set(native["authority"]), {"endpoint", "token_ref"})
        self.assertEqual(native["profiles"][0]["root_timeout_ms"], 60000)
        self.assertNotIn("max_seconds", native["profiles"][0])
        self.assertNotIn("models", native)
        self.assertNotIn("children", native)
        self.assertFalse((self.bundle / role_manager.INIT_INTENT).exists())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "foreign", "OWNER_TOKEN": "foreign"}):
            environment = deployment._environment(self.bundle, "openjules", data)
        self.assertNotIn("OWNER_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(environment["ZPM_INSTALL_RUNTIME_A"], "a" * 48)

    def test_native_authority_shape_and_integer_bounds_are_admitted_before_preparation(self):
        for values in ({"authority_endpoint": "https://authority.example.invalid/prefix"},
                       {"authority_endpoint": "http://127.0.0.2:8766"},
                       {"runtime": {"root_timeout_ms": 1001}}, {"worker_ceiling": 2**53},
                       {"binding": self.binding | {"repository": "github:github.com/owner/" + "x" * 500}}):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                deployment.parse_configuration(self.execution | values)
            self.assertFalse(self.bundle.exists())
        for endpoint in ("https://authority.example.invalid", "https://authority.example.invalid/", "http://[::1]:8766"):
            self.assertEqual(deployment.parse_configuration(self.execution | {"authority_endpoint": endpoint})["authority_endpoint"], endpoint.rstrip("/"))

    def test_roles_reject_extra_sources_policy_and_credential_aliases(self):
        variants = [self.execution | {"sources": self.config["sources"]},
                    self.execution | {"runtime_token_ref": "env:PATH"},
                    self.execution | {"runtime_token_ref": self.execution["lifecycle_token_ref"]},
                    self.execution | {"binding": self.binding | {"repository": "Owner/Project"}},
                    self.control | {"binding": self.binding | {"desired_workers": 1}},
                    self.control | {"model_policy": {}}]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                deployment.parse_configuration(value)
        with patch.dict(os.environ, self.tokens | {"ZPM_INSTALL_RUNTIME_A": "c" * 48}), self.assertRaises(ConfigurationError):
            deployment.prepare(self.execution, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_reprepare_preserves_private_tokens_and_current_capacity_without_resolving_environment(self):
        self.prepare(self.control)
        before = {path.name: path.read_bytes() for path in (self.bundle / "secrets").iterdir()}
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            store.activate_destinations(offline=True)
            store.change(self.binding["repository"], "target", 1)
        with patch.object(deployment_roles, "_resolve", side_effect=AssertionError("credential rotation")):
            repeated = deployment.prepare(self.control, self.bundle)
        self.assertTrue(repeated["reused"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.bundle / "secrets").iterdir()})
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.bindings()[0]["desired_workers"], 1)

    def test_foreign_role_secrets_and_state_refuse_verification(self):
        self.prepare(self.execution)
        path = self.bundle / "secrets/operator.json"
        path.write_text("{}")
        path.chmod(0o600)
        with self.assertRaises(ConfigurationError):
            deployment.verify(self.bundle)
        path.unlink()
        (self.bundle / "state/polyphony").mkdir(mode=0o700)
        with self.assertRaises(ConfigurationError):
            deployment.verify(self.bundle)

    def test_initialization_intent_precedes_native_call_and_original_identity_is_retained(self):
        data = self.initialized()
        receipt = (self.bundle / role_manager.BINDING_RECEIPT).read_bytes()
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        with patch.object(role_manager, "_native", return_value=self.inspection(ready=True)) as native:
            role_manager.initialize(self.bundle, data)
        self.assertEqual(native.call_args.args[2:], ("--inspect-destination",))
        self.assertEqual((self.bundle / role_manager.BINDING_RECEIPT).read_bytes(), receipt)
        self.assertTrue((self.bundle / role_manager.READY_RECEIPT).exists())
        with patch.object(role_manager, "_native", return_value=self.inspection()), self.assertRaisesRegex(ConfigurationError, "regressed"):
            role_manager._inspect_original(self.bundle, data)

    def test_lost_initialization_reply_recovers_only_by_inspecting_retained_producer_state(self):
        _, data = self.prepare(self.execution)
        def lost(*args):
            self.native_files()
            raise ConfigurationError("lost result after native initialization")
        with patch.object(role_manager, "_native", side_effect=lost), self.assertRaises(ConfigurationError):
            role_manager.initialize(self.bundle, data)
        self.assertTrue((self.bundle / role_manager.INIT_INTENT).exists())
        self.assertFalse((self.bundle / role_manager.BINDING_RECEIPT).exists())
        with patch.object(role_manager, "_native", return_value=self.inspection()) as native:
            role_manager.initialize(self.bundle, data)
        self.assertEqual(native.call_args.args[2:], ("--inspect-destination",))
        self.assertEqual(role_manager._binding(self.bundle, data)["journal_incarnation"], "a" * 32)

    def test_unknown_missing_initialization_never_reinvokes_fresh_initializer(self):
        _, data = self.prepare(self.execution)
        with patch.object(role_manager, "_native", side_effect=ConfigurationError("unavailable")) as native:
            for _ in range(2):
                with self.assertRaises(ConfigurationError):
                    role_manager.initialize(self.bundle, data)
        self.assertEqual([call.args[2:] for call in native.call_args_list], [("--initialize-destination",), ("--inspect-destination",)])
        self.assertFalse((self.bundle / role_manager.BINDING_RECEIPT).exists())

    def test_missing_or_replaced_initialized_state_refuses_without_reinitialization(self):
        data = self.initialized()
        with patch.object(role_manager, "_native", return_value=self.inspection(incarnation="c" * 32)) as native, self.assertRaises(ConfigurationError):
            role_manager.initialize(self.bundle, data)
        self.assertEqual(native.call_args.args[2:], ("--inspect-destination",))
        (self.bundle / "state/openjules/lifecycle.sqlite3").unlink()
        with patch.object(role_manager, "_native", side_effect=AssertionError("offline verification invoked native code")):
            with self.assertRaises(OSError):
                deployment.verify(self.bundle)

    def approval_file(self, name, argument):
        manifest = test_approvals.manifest()
        manifest["execution"]["root_argv"].append(argument)
        value = test_approvals.envelope(manifest, profile=self.binding["profile"], policy="b" * 32)
        value["deployment_id"] = "role-test"
        value["destination_binding"]["journal_incarnation"] = "a" * 32
        path = self.root / name
        path.write_bytes(test_approvals.encode(value))
        path.chmod(0o600)
        return path, value

    def test_rejected_approval_retains_attempt_without_poisoning_corrected_native_acceptance(self):
        data = self.initialized()
        rejected, rejected_value = self.approval_file("rejected.json", "not-the-native-candidate")
        correct, accepted_value = self.approval_file("accepted.json", "native-candidate")
        accepted = {"schema_version": 1, "approved": True,
                    "execution_manifest_sha256": accepted_value["execution_manifest_sha256"]}
        with patch.object(role_manager, "_inspect_original"), patch.object(role_manager, "_native", side_effect=ConfigurationError("candidate mismatch")) as native:
            with self.assertRaises(ConfigurationError):
                role_manager.approve(self.bundle, data, rejected)
            first_input = Path(native.call_args.args[-1])
        self.assertEqual(first_input.read_bytes(), rejected.read_bytes())
        self.assertFalse((self.bundle / role_manager.APPROVED_ENVELOPE).exists())
        with patch.object(role_manager, "_inspect_original"), patch.object(role_manager, "_native", return_value=accepted) as native:
            self.assertEqual(role_manager.approve(self.bundle, data, correct), accepted)
            second_input = Path(native.call_args.args[-1])
        self.assertNotEqual(first_input, second_input)
        self.assertEqual(second_input.read_bytes(), correct.read_bytes())
        self.assertEqual((self.bundle / role_manager.APPROVED_ENVELOPE).read_bytes(), correct.read_bytes())
        with patch.object(role_manager, "_inspect_original"), patch.object(role_manager, "_native", side_effect=AssertionError("changed accepted approval")), self.assertRaises(ConfigurationError):
            role_manager.approve(self.bundle, data, rejected)
        self.assertEqual(first_input.read_bytes(), rejected.read_bytes())

    def test_lost_approval_ack_replays_exact_retained_input_before_recording_accepted_document(self):
        data = self.initialized()
        path, value = self.approval_file("approval.json", "native-candidate")
        accepted = {"schema_version": 1, "approved": True,
                    "execution_manifest_sha256": value["execution_manifest_sha256"]}
        inputs = []
        def native(*args):
            retained = Path(args[-1])
            self.assertEqual(retained.read_bytes(), path.read_bytes())
            inputs.append(retained)
            if len(inputs) == 1:
                raise ConfigurationError("lost acknowledgement after native acceptance")
            return accepted
        with patch.object(role_manager, "_inspect_original"), patch.object(role_manager, "_native", side_effect=native):
            with self.assertRaises(ConfigurationError):
                role_manager.approve(self.bundle, data, path)
            self.assertFalse((self.bundle / role_manager.APPROVED_ENVELOPE).exists())
            self.assertEqual(role_manager.approve(self.bundle, data, path), accepted)
        self.assertEqual(inputs, [inputs[0], inputs[0]])
        self.assertEqual((self.bundle / role_manager.APPROVED_ENVELOPE).read_bytes(), path.read_bytes())
        reused = deployment.prepare(self.execution, self.bundle)
        self.assertTrue(reused["reused"])
        self.assertIn("python3 manage.py inspect-destination", reused["next"])
        self.assertIn("python3 manage.py start", reused["next"])
        self.assertNotIn("python3 manage.py initialize-destination", reused["next"])

    def test_offline_verification_refuses_malformed_or_foreign_accepted_envelope_without_native_calls(self):
        data = self.initialized()
        path, value = self.approval_file("approval.json", "native-candidate")
        retained = self.bundle / role_manager.APPROVED_ENVELOPE
        role_manager._publish(retained, path.read_bytes())
        with patch.object(role_manager, "_native", side_effect=AssertionError("offline verification called native code")):
            self.assertTrue(deployment.verify(self.bundle)["valid"])
            foreign = deepcopy(value)
            foreign["destination_binding"]["journal_incarnation"] = "c" * 32
            for invalid in ({}, foreign):
                retained.write_bytes(test_approvals.encode(invalid))
                with self.subTest(invalid=invalid), self.assertRaises(ConfigurationError):
                    deployment.verify(self.bundle)
                with self.assertRaises(ConfigurationError):
                    deployment.prepare(self.execution, self.bundle)

    def test_inspection_requires_exact_scope_supported_features_and_policy_match(self):
        _, data = self.prepare(self.execution)
        variants = [self.inspection() | {"identity_binding": True}, self.inspection() | {"extra": 1}]
        changed = self.inspection()
        changed["profiles"][0]["current_policy_matches"] = False
        variants.append(changed)
        changed = self.inspection()
        changed["profiles"][0]["repository"] = "github:github.com/foreign/repository"
        variants.append(changed)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                role_manager._inspection(value, data)

    def test_live_admission_uses_producer_authority_without_foreground_manager_lock(self):
        data = self.initialized()
        admitted = {"version": 1, "revision": 2, "new_effects_open": False, "worker_ceiling": 1, "unreleased_workers": 0}
        with deployment._manager_lock(self.bundle), patch.object(role_manager, "_inspect_original"), \
                patch.object(role_manager, "_native", return_value=admitted):
            self.assertEqual(role_manager.host_admission(self.bundle, data, "close-admission"), admitted)
            with self.assertRaises(ConfigurationError):
                role_manager.initialize(self.bundle, data)

    def test_catalog_binds_each_destination_to_its_exact_reference(self):
        _, data = self.prepare(self.control)
        destination = {"destination_id": "host-a", "journal_incarnation": "a" * 32,
                       "endpoint": "https://runtime.example.invalid", "token_ref": "env:ZPM_INSTALL_LIFECYCLE_A",
                       "max_workers": 1, "enabled": False}
        profile = {"destination_id": "host-a", "repository": self.binding["repository"], "profile": self.binding["profile"],
                   "policy_binding_id": "b" * 32, "policy_binding_version": 1}
        value = {"schema_version": 2, "max_workers": 2, "timeout_seconds": 2,
                 "destinations": [destination], "profiles": [profile]}
        source = self.root / "registry.json"
        source.write_text(json.dumps(value)); source.chmod(0o600)
        with patch.object(role_manager, "_controller_command", return_value={"imported": True}) as command:
            role_manager.registry_import(self.bundle, data, source)
        retained = Path(command.call_args.args[-1])
        self.assertNotEqual(retained, source)
        self.assertEqual(json.loads(retained.read_text()), value)
        value["destinations"][0]["token_ref"] = "env:ZPM_INSTALL_LIFECYCLE_B"
        source.write_text(json.dumps(value))
        with patch.object(role_manager, "_controller_command", side_effect=AssertionError("wrong credential imported")), self.assertRaises(ConfigurationError):
            role_manager.registry_import(self.bundle, data, source)

    def test_doctor_does_not_infer_capabilities_before_explicit_format_activation(self):
        _, data = self.prepare(self.control)
        with patch.object(role_manager, "_controller_command", side_effect=AssertionError("native check before format activation")):
            report = role_manager.doctor(self.bundle, data)
        self.assertFalse(report["ready"])
        self.assertFalse(report["remote_access_checked"])

    def test_missing_live_controller_journal_never_falls_back_to_preparation_backup(self):
        self.prepare(self.control)
        backup = (self.bundle / "state/prepared-v1.sqlite3").read_bytes()
        (self.bundle / "state/zpm.sqlite3").unlink()
        for operation in (lambda: deployment.verify(self.bundle), lambda: deployment.prepare(self.control, self.bundle)):
            with self.assertRaises(ConfigurationError):
                operation()
        self.assertFalse((self.bundle / "state/zpm.sqlite3").exists())
        self.assertEqual((self.bundle / "state/prepared-v1.sqlite3").read_bytes(), backup)

    def authority_file(self):
        path = self.bundle / "state/polyphony/authority.sqlite3"
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE fixture (value TEXT)")
        path.chmod(0o600)
        return path

    def test_authority_pending_first_start_refuses_adoption_or_reinitialization(self):
        _, data = self.prepare(self.control)
        with self.assertRaisesRegex(ConfigurationError, "authority-only"):
            role_manager._begin_authority(self.bundle, data, authority_only=False)
        role_manager._begin_authority(self.bundle, data, authority_only=True)
        self.authority_file()
        for operation in (lambda: role_manager._begin_authority(self.bundle, data, authority_only=True),
                          lambda: deployment.verify(self.bundle)):
            with self.assertRaisesRegex(ConfigurationError, "unknown"):
                operation()
        self.assertFalse((self.bundle / role_manager.AUTHORITY_RECEIPT).exists())

    def test_authority_original_file_guard_rejects_replacement_and_same_inode_truncation(self):
        _, data = self.prepare(self.control)
        role_manager._begin_authority(self.bundle, data, authority_only=True)
        path = self.authority_file()
        record = role_manager._authority_record(self.bundle, data) | {"file_identity": role_manager._authority_file(self.bundle)}
        role_manager._publish(self.bundle / role_manager.AUTHORITY_RECEIPT, role_manager._encoded(record))
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        retained = path.with_name("retained.sqlite3")
        path.rename(retained)
        self.authority_file()
        with self.assertRaisesRegex(ConfigurationError, "identity"):
            deployment.verify(self.bundle)
        path.unlink()
        retained.rename(path)
        original_inode = path.stat().st_ino
        with path.open("r+b") as stream:
            stream.truncate(16)
        self.assertEqual(path.stat().st_ino, original_inode)
        with self.assertRaises(ConfigurationError):
            deployment.verify(self.bundle)

    def test_authority_ready_probe_is_scoped_and_requires_owned_process_alive(self):
        from unittest.mock import Mock
        _, data = self.prepare(self.control)
        role_manager._begin_authority(self.bundle, data, authority_only=True)
        self.authority_file()
        result = {"schema_version": 1, "deployment_id": data["deployment_id"], "repository": self.binding["repository"],
                  "profile": self.binding["profile"], "base": data["base_commit"], "service": "keystone-polyphony",
                  "protocol": "managed-runtime-v1", "profile_supported": True, "managed_dispatch": True, "external_root": True}
        process = Mock()
        process.poll.side_effect = [None, 1]
        with patch.object(role_manager.OpenJulesProvider, "_send", return_value=result) as send, self.assertRaises(ConfigurationError):
            role_manager._confirm_authority(self.bundle, data, process)
        self.assertFalse((self.bundle / role_manager.AUTHORITY_RECEIPT).exists())
        self.assertEqual(send.call_args.kwargs["base_path"], "/managed")
        process.poll.side_effect = None
        process.poll.return_value = None
        with patch.object(role_manager.OpenJulesProvider, "_send", return_value=result):
            role_manager._confirm_authority(self.bundle, data, process)
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            store.activate_destinations(offline=True)
        repeated = deployment.prepare(self.control, self.bundle)
        self.assertEqual(repeated["next"], ["python3 manage.py bootstrap", "python3 manage.py start", "python3 manage.py status"])

    def test_authority_late_reply_clips_request_deadline_and_never_confirms_first_start(self):
        from unittest.mock import Mock
        _, data = self.prepare(self.control)
        role_manager._begin_authority(self.bundle, data, authority_only=True)
        self.authority_file()
        result = {"schema_version": 1, "deployment_id": data["deployment_id"], "repository": self.binding["repository"],
                  "profile": self.binding["profile"], "base": data["base_commit"], "service": "keystone-polyphony",
                  "protocol": "managed-runtime-v1", "profile_supported": True, "managed_dispatch": True, "external_root": True}
        process = Mock()
        process.poll.return_value = None
        timeouts = []
        def late_reply(provider, *args, **kwargs):
            timeouts.append(provider.config.timeout_seconds)
            return result
        with patch.object(role_manager.time, "monotonic", side_effect=[0, 14.75, 16]), \
                patch.object(role_manager.OpenJulesProvider, "_send", autospec=True, side_effect=late_reply), \
                self.assertRaisesRegex(ConfigurationError, "deadline"):
            role_manager._confirm_authority(self.bundle, data, process)
        self.assertEqual(timeouts, [0.25])
        self.assertTrue((self.bundle / role_manager.AUTHORITY_INTENT).exists())
        self.assertFalse((self.bundle / role_manager.AUTHORITY_RECEIPT).exists())


if __name__ == "__main__":
    unittest.main()
