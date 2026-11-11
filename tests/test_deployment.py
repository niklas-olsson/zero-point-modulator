"""Deployment configuration is a boundary, not evidence of runtime readiness."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import venv
from unittest.mock import Mock, patch

from zpm.deployment import CHILD_ENTRYPOINTS, MANAGED_ENTRYPOINTS, MODEL_ENTRYPOINTS, MODEL_TOKEN_ENV, SOURCE_FILES, _bootstrap, _command, _doctor, _environment, prepare, verify
from zpm import installation_context
from zpm.models import ConfigurationError
from zpm.store import Store


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = {}
        for name, entrypoint in {**SOURCE_FILES, "source": "README.md"}.items():
            checkout = self.root / name
            checkout.mkdir()
            for filename in MANAGED_ENTRYPOINTS.get(name, (entrypoint,)):
                path = checkout / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture source\n")
            for command in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", "-C", str(checkout), *command], check=True, capture_output=True)
            revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            self.sources[name] = {"checkout": str(checkout), "revision": revision}
        self.config = {
            "schema_version": 1, "deployment_id": "installation-test",
            "binding": {"schema_version": 1, "repository": "Owner/Project", "profile": "approved", "desired_workers": 0},
            "max_workers": 8, "sources": {name: item for name, item in self.sources.items() if name != "source"},
            "source": self.sources["source"], "image": "sha256:" + "a" * 64,
            "command": ["python", "-c", "print('controlled trial')"],
            "policy": {"checks": ["tests"], "reviewers": ["operator"], "max_attempts": 3,
                       "budget_seconds": 600, "attempt_seconds": 60, "capabilities": ["python"]},
        }
        self.bundle = self.root / "installation"

    def tearDown(self):
        self.temporary.cleanup()

    def read(self, name):
        return json.loads((self.bundle / name).read_text())

    def model_configuration(self, token_ref="env:TEST_PROVIDER_KEY"):
        checkout = Path(self.sources["openjules"]["checkout"])
        for entrypoint in MODEL_ENTRYPOINTS:
            path = checkout / entrypoint
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture model entry point\n")
        subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "model fixture"], check=True, capture_output=True)
        self.config["sources"]["openjules"]["revision"] = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        return deepcopy(self.config) | {"model_policy": {
            "endpoint": "https://provider.example/v1/chat/completions", "model": "operator-approved-model",
            "token_ref": token_ref, "max_calls": 4, "max_output_tokens": 1024, "max_total_output_tokens": 4096,
            "request_timeout_seconds": 5, "token_parameter": "max_completion_tokens",
        }}

    def child_configuration(self):
        configuration = self.model_configuration()
        for name, entrypoints in CHILD_ENTRYPOINTS.items():
            checkout = Path(configuration["sources"][name]["checkout"])
            for entrypoint in entrypoints:
                path = checkout / entrypoint
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture child entry point\n")
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "child fixture"], check=True, capture_output=True)
            configuration["sources"][name]["revision"] = subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        return configuration | {"child_policy": {
            "max_children": 2, "max_child_seconds": 30, "max_child_turns": 6,
            "capabilities": ["python"], "verify_argv": ["python3", "-m", "unittest", "discover"],
        }}

    def test_prepare_is_private_offline_and_scopes_all_three_services(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("offline operation contacted network")):
            report = prepare(self.config, self.bundle)
            verification = verify(self.bundle)
        self.assertFalse(report["services_started"])
        self.assertFalse(verification["remote_access_checked"])
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o700)
        for path in (self.bundle / "secrets").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        authority = self.read("config/polyphony.json")
        runtime = self.read("config/openjules.json")
        binding = self.read("config/repository.json")
        canonical = "github:github.com/owner/project"
        self.assertEqual(binding["repository"], canonical)
        self.assertEqual(authority["repositories"][canonical]["profiles"]["approved"]["base"], runtime["profiles"][0]["base_commit"])
        self.assertEqual(runtime["host"], "127.0.0.1")
        self.assertEqual(runtime["profiles"][0]["repository"], canonical)
        credentials = self.read("secrets/polyphony.json")
        self.assertEqual({row["role"] for row in credentials.values()}, {"owner", "reviewer", "runtime", "ingress"})
        for row in credentials.values():
            if row["role"] in {"runtime", "ingress", "reviewer"}:
                self.assertEqual(row["repositories"], [canonical])
                self.assertEqual(row["deployment_id"], "installation-test")
        tokens = [*credentials, *self.read("secrets/openjules.json")]
        self.assertEqual(len(set(tokens)), 5)
        public_text = "".join(path.read_text() for path in (self.bundle / "config").iterdir()) + json.dumps(report)
        public_text += (self.bundle / "manifest.json").read_text()
        self.assertTrue(all(token not in public_text for token in tokens))
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.deployment_id, "installation-test")
            self.assertEqual(store.bindings()[0]["desired_workers"], 0)

    def test_prepare_never_overwrites_existing_data_or_uses_source_directory(self):
        self.bundle.mkdir()
        marker = self.bundle / "do-not-touch"
        marker.write_text("retained")
        with self.assertRaises(ConfigurationError):
            prepare(self.config, self.bundle)
        self.assertEqual(marker.read_text(), "retained")
        with self.assertRaises(ConfigurationError):
            prepare(self.config, Path(self.sources["source"]["checkout"]) / "installation")

    def test_repeated_prepare_reuses_credentials_without_reenrolling_removed_state(self):
        self.assertFalse(prepare(self.config, self.bundle)["reused"])
        retained = {name: (self.bundle / name).read_bytes() for name in
                    ("manifest.json", "secrets/environment.json", "secrets/operator.json", "secrets/polyphony.json", "secrets/openjules.json")}
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            store.change("owner/project", "target", 2)
            store.change("owner/project", "remove")
            expected = store.bindings()
        artifact = self.bundle / "state/openjules/retained-capture"
        artifact.write_bytes(b"retained task evidence")
        normalized = self.config | {"binding": self.config["binding"] | {"repository": "github:github.com/owner/project"}}
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("credential rotation")), \
                patch("zpm.deployment.Store", side_effect=AssertionError("state mutation")):
            repeated = prepare(normalized, self.bundle)
        self.assertTrue(repeated["reused"])
        self.assertFalse(repeated["services_started"])
        self.assertFalse(repeated["remote_access_checked"])
        self.assertEqual(retained, {name: (self.bundle / name).read_bytes() for name in retained})
        self.assertEqual(artifact.read_bytes(), b"retained task evidence")
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.bindings(), expected)
            self.assertFalse(store.bindings()[0]["enabled"])

    def test_repeat_rejects_different_configuration_incomplete_bundle_and_symlink(self):
        prepare(self.config, self.bundle)
        manifest = (self.bundle / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ConfigurationError, "different configuration"):
            prepare(self.config | {"max_workers": 7}, self.bundle)
        self.assertEqual((self.bundle / "manifest.json").read_bytes(), manifest)
        linked = self.root / "linked-installation"
        linked.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaisesRegex(ConfigurationError, "symlink"):
            prepare(self.config, linked)
        (self.bundle / "manifest.json").unlink()
        retained = (self.bundle / "secrets/environment.json").read_bytes()
        with self.assertRaisesRegex(ConfigurationError, "not a complete deployment"):
            prepare(self.config, self.bundle)
        self.assertEqual((self.bundle / "secrets/environment.json").read_bytes(), retained)
        self.assertFalse((self.bundle / "manifest.json").exists())

    def test_repeat_verifies_sealed_files_before_reusing(self):
        prepare(self.config, self.bundle)
        path = self.bundle / "config/provider.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ConfigurationError, "integrity"):
            prepare(self.config, self.bundle)
        self.assertEqual(path.read_text(), "{}")

    def test_repeat_and_verify_never_recreate_a_missing_or_replaced_journal(self):
        prepare(self.config, self.bundle)
        journal = self.bundle / "state/zpm.sqlite3"
        retained = self.root / "retained.sqlite3"
        journal.rename(retained)
        for operation in (lambda: prepare(self.config, self.bundle), lambda: verify(self.bundle)):
            with self.assertRaisesRegex(ConfigurationError, "journal must remain"):
                operation()
            self.assertFalse(journal.exists())
        journal.symlink_to(retained)
        with self.assertRaisesRegex(ConfigurationError, "journal must remain"):
            prepare(self.config, self.bundle)
        journal.unlink()
        journal.write_text("not a database")
        journal.chmod(0o600)
        with self.assertRaisesRegex(ConfigurationError, "cannot read the retained"):
            prepare(self.config, self.bundle)
        self.assertEqual(journal.read_text(), "not a database")
        journal.unlink()
        retained.rename(journal)
        with sqlite3.connect(journal) as database:
            database.execute("UPDATE metadata SET value='other-deployment' WHERE key='deployment_id'")
        with self.assertRaisesRegex(ConfigurationError, "journal identity"):
            prepare(self.config, self.bundle)

    def test_changed_pins_dirty_checkouts_and_unsafe_inputs_fail_before_writing(self):
        variants = [
            {"schema_version": True}, {"image": "python:latest"}, {"command": "shell injection"},
            {"ports": {"openjules": 8766}}, {"runtime": {"uid": 0}}, {"runtime": {"uid": 65534}},
            {"runtime": {"max_seconds": 1}}, {"environment_refs": {"DOCKER_HOST": "env:CONTROL_SOCKET"}},
            {"environment_refs": {"OPENAI_API_KEY": "env:POLYPHONY_RUNTIME_TOKEN"}},
            {"source": {**self.sources["source"], "revision": "a" * 40}},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ConfigurationError):
                prepare({**self.config, **variant}, self.bundle)
            self.assertFalse(self.bundle.exists())
        (Path(self.sources["source"]["checkout"]) / "README.md").write_text("dirty")
        with self.assertRaises(ConfigurationError):
            prepare(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_verify_rejects_changed_configuration_permissions_and_symlinks(self):
        prepare(self.config, self.bundle)
        config = self.bundle / "config/openjules.json"
        original = config.read_bytes()
        config.write_text("{}")
        with self.assertRaisesRegex(ConfigurationError, "integrity"):
            verify(self.bundle)
        config.write_bytes(original)
        config.chmod(0o644)
        with self.assertRaisesRegex(ConfigurationError, "private"):
            verify(self.bundle)
        config.chmod(0o600)
        target = self.root / "outside"
        target.write_bytes(original)
        target.chmod(0o600)
        config.unlink()
        config.symlink_to(target)
        with self.assertRaisesRegex(ConfigurationError, "regular"):
            verify(self.bundle)

    def test_service_environments_and_interpreters_do_not_cross_credentials(self):
        config = deepcopy(self.config)
        config["environment_refs"] = {"OPENAI_API_KEY": "env:TEST_MODEL_KEY", "ANTHROPIC_API_KEY": "env:PYTHONPATH"}
        prepare(config, self.bundle)
        data = self.read("manifest.json")["configuration"]
        with patch.dict(os.environ, {"UNRELATED_SECRET": "do-not-inherit", "PYTHONPATH": "/untrusted", "TEST_MODEL_KEY": "model-secret"}):
            environments = {name: _environment(self.bundle, name, data) for name in SOURCE_FILES}
        self.assertIn("OPENJULES_WORKER_TOKEN", environments["zpm"])
        self.assertNotIn("POLYPHONY_RUNTIME_TOKEN", environments["zpm"])
        self.assertIn("POLYPHONY_RUNTIME_TOKEN", environments["openjules"])
        self.assertNotIn("OPENJULES_WORKER_TOKEN", environments["openjules"])
        self.assertEqual(environments["openjules"]["ZPM_MODEL_OPENAI_API_KEY"], "model-secret")
        self.assertEqual(environments["openjules"]["ZPM_MODEL_ANTHROPIC_API_KEY"], "/untrusted")
        self.assertNotIn("TEST_MODEL_KEY", environments["openjules"])
        self.assertEqual(self.read("config/openjules.json")["profiles"][0]["environment_refs"], {
            "OPENAI_API_KEY": "env:ZPM_MODEL_OPENAI_API_KEY", "ANTHROPIC_API_KEY": "env:ZPM_MODEL_ANTHROPIC_API_KEY",
        })
        self.assertNotIn("TEST_MODEL_KEY", environments["polyphony"])
        self.assertTrue(all("UNRELATED_SECRET" not in env and "PYTHONPATH" not in env for env in environments.values()))
        commands = [_command(self.bundle, name, data) for name in SOURCE_FILES]
        self.assertEqual(len({command[0] for command in commands}), 3)
        self.assertTrue(all("model-secret" not in " ".join(command) for command in commands))

    def test_model_policy_is_scoped_and_resolves_only_in_the_openjules_environment(self):
        configuration = self.model_configuration(token_ref="env:PYTHONPATH")
        synthetic_secret = "synthetic-provider-secret-only-for-this-test"
        with patch.dict(os.environ, {"PYTHONPATH": synthetic_secret, "UNRELATED_SECRET": "do-not-inherit"}):
            with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("preparation contacted a provider")):
                prepare(configuration, self.bundle)
                verify(self.bundle)
            data = self.read("manifest.json")["configuration"]
            environments = {name: _environment(self.bundle, name, data) for name in (*SOURCE_FILES, "bootstrap")}
        runtime = self.read("config/openjules.json")
        self.assertEqual(runtime["profiles"][0]["environment_refs"], {})
        self.assertEqual(runtime["models"], [{
            "deployment_id": "installation-test", "repository": "github:github.com/owner/project", "profile": "approved",
            "policy": data["model_policy"] | {"token_ref": "env:" + MODEL_TOKEN_ENV},
        }])
        self.assertEqual(data["model_policy"]["token_ref"], "env:PYTHONPATH")
        self.assertEqual(environments["openjules"][MODEL_TOKEN_ENV], synthetic_secret)
        self.assertTrue(all("PYTHONPATH" not in env and "UNRELATED_SECRET" not in env for env in environments.values()))
        for service in ("zpm", "polyphony", "bootstrap"):
            self.assertNotIn(MODEL_TOKEN_ENV, environments[service])
            self.assertNotIn(synthetic_secret, environments[service].values())
        for path in self.bundle.rglob("*"):
            if path.is_file():
                self.assertNotIn(synthetic_secret.encode(), path.read_bytes())

    def test_model_preparation_is_offline_but_start_requires_the_explicit_provider_reference(self):
        configuration = self.model_configuration()
        with patch.dict(os.environ, {}, clear=True):
            # Retain PATH only for local Git validation, without provider secrets.
            with patch.dict(os.environ, {"PATH": os.defpath}):
                prepare(configuration, self.bundle)
                data = self.read("manifest.json")["configuration"]
                verify(self.bundle)
                for service in ("zpm", "polyphony", "bootstrap"):
                    _environment(self.bundle, service, data)
                with self.assertRaisesRegex(ConfigurationError, "provider reference is unavailable"):
                    _environment(self.bundle, "openjules", data)
                with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "invalid whitespace credential"}):
                    with self.assertRaisesRegex(ConfigurationError, "provider reference is unavailable"):
                        _environment(self.bundle, "openjules", data)

    def test_model_policy_rejects_invalid_or_guest_credential_configuration_before_writing(self):
        configuration = self.model_configuration()
        variants = [
            {"endpoint": "http://provider.example/v1/chat/completions"},
            {"endpoint": "https://user:password@provider.example/v1/chat/completions"},
            {"endpoint": "https://provider.example/v1/chat/completions?token=secret"},
            {"endpoint": "https://provider.example/v1/chat/completions#secret"},
            {"endpoint": "https://provider.example:invalid/v1/chat/completions"},
            {"endpoint": "https://provider.example/v1/models"}, {"endpoint": None},
            {"model": ""}, {"model": "unapproved model"}, {"token_ref": "inline-secret"},
            {"token_ref": "env:OPENJULES_WORKER_TOKEN"}, {"token_ref": "env:POLYPHONY_RUNTIME_TOKEN"},
            {"max_calls": True}, {"max_calls": 0}, {"max_calls": 1001},
            {"max_output_tokens": 32769}, {"max_total_output_tokens": 10_000_001},
            {"request_timeout_seconds": 0}, {"request_timeout_seconds": float("nan")},
            {"request_timeout_seconds": float("inf")}, {"max_request_bytes": 1023},
            {"max_response_bytes": 2097153}, {"token_parameter": "arbitrary"}, {"token_parameter": []},
            {"fallback_model": "unapproved"},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ConfigurationError):
                prepare(configuration | {"model_policy": configuration["model_policy"] | variant}, self.bundle)
            self.assertFalse(self.bundle.exists())
        for invalid in (None, {}, []):
            with self.subTest(model_policy=invalid), self.assertRaises(ConfigurationError):
                prepare(configuration | {"model_policy": invalid}, self.bundle)
            self.assertFalse(self.bundle.exists())
        with self.assertRaisesRegex(ConfigurationError, "cannot inject guest"):
            prepare(configuration | {"environment_refs": {"OPENAI_API_KEY": "env:TEST_PROVIDER_KEY"}}, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_model_entrypoints_are_required_only_when_the_operator_opts_in(self):
        configuration = deepcopy(self.config) | {"model_policy": {
            "endpoint": "http://127.0.0.1:12345/v1/chat/completions", "model": "local-fixture",
            "token_ref": "env:TEST_PROVIDER_KEY",
        }}
        with self.assertRaisesRegex(ConfigurationError, "model entry points"):
            prepare(configuration, self.bundle)
        self.assertFalse(self.bundle.exists())
        prepare(self.config, self.bundle)
        self.assertNotIn("model_policy", self.read("manifest.json")["configuration"])
        self.assertNotIn("models", self.read("config/openjules.json"))
        self.assertTrue(verify(self.bundle)["valid"])

    def test_children_share_the_approved_profile_without_changing_top_level_capacity(self):
        configuration = self.child_configuration()
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("preparation contacted a service")):
            prepare(configuration, self.bundle)
            self.assertTrue(verify(self.bundle)["valid"])
        data = self.read("manifest.json")["configuration"]
        runtime = self.read("config/openjules.json")
        scope = {"deployment_id": data["deployment_id"], "repository": data["binding"]["repository"],
                 "profile": data["binding"]["profile"]}
        self.assertEqual(runtime["children"], [{**scope, "policy": data["child_policy"]}])
        canonical = self.read("config/polyphony.json")["repositories"][scope["repository"]]["profiles"][scope["profile"]]
        self.assertEqual({name: canonical[name] for name in ("max_children", "max_child_seconds", "max_child_turns")},
                         {name: data["child_policy"][name] for name in ("max_children", "max_child_seconds", "max_child_turns")})
        self.assertEqual(canonical["child_capabilities"], data["child_policy"]["capabilities"])
        self.assertNotIn("uid_start", canonical)
        self.assertNotIn("command", canonical)
        self.assertNotIn("outcome_protocol", data["child_policy"])
        self.assertNotIn("outcome_protocol", canonical)
        self.assertEqual(runtime["profiles"][0]["environment_refs"], {})
        self.assertEqual(self.read("config/provider.json")["max_workers"], configuration["max_workers"])
        with Store(self.bundle / "state/zpm.sqlite3", data["deployment_id"]) as store:
            self.assertEqual(len(store.bindings()), 1)
            self.assertEqual(store.bindings()[0]["desired_workers"], 0)

    def test_initial_child_outcomes_are_explicit_and_do_not_change_canonical_limits(self):
        configuration = self.child_configuration()
        configured = configuration | {"child_policy": configuration["child_policy"] | {"outcome_protocol": "initial-v1"}}
        prepare(configured, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        runtime = self.read("config/openjules.json")
        policy = runtime["children"][0]["policy"]
        self.assertEqual(policy["outcome_protocol"], "initial-v1")
        self.assertEqual(policy["command"], ["python3", "-I", "-S", "/opt/openjules/managed_runner.py"])
        canonical = self.read("config/polyphony.json")["repositories"]["github:github.com/owner/project"]["profiles"]["approved"]
        self.assertNotIn("outcome_protocol", canonical)
        self.assertEqual(canonical["max_children"], 2)
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.bindings()[0]["desired_workers"], 0)

    def test_initial_child_outcomes_reject_unsupported_protocol_or_custom_runner(self):
        configuration = self.child_configuration()
        for variant in ({"outcome_protocol": value} for value in (None, False, 1, "", "future-v2", {})):
            with self.subTest(variant=variant), self.assertRaisesRegex(ConfigurationError, "outcome_protocol"):
                prepare(configuration | {"child_policy": configuration["child_policy"] | variant}, self.bundle)
            self.assertFalse(self.bundle.exists())
        with self.assertRaisesRegex(ConfigurationError, "supported managed runner"):
            prepare(configuration | {"child_policy": configuration["child_policy"] | {
                "outcome_protocol": "initial-v1", "command": ["python3", "custom.py"],
            }}, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_children_reject_invalid_limits_identity_overlap_and_expanded_capabilities_before_writing(self):
        configuration = self.child_configuration()
        variants = [
            {"max_children": 0}, {"max_children": 65}, {"max_children": True},
            {"max_child_seconds": 0}, {"max_child_seconds": 61}, {"max_child_seconds": float("nan")},
            {"max_child_seconds": float("inf")}, {"max_child_seconds": True},
            {"max_child_turns": 0}, {"max_child_turns": 1001}, {"max_child_turns": 1.5},
            {"capabilities": ["owner"]}, {"capabilities": ["python", "python"]}, {"capabilities": [None]},
            {"uid_start": 0}, {"uid_start": True}, {"uid_start": 65535},
            {"uid_start": 65531}, {"uid_start": 65533},
            {"command": []}, {"command": ["python3", ""]}, {"command": "python3"},
            {"verify_argv": ["python3", "\x00"]}, {"verify_argv": None},
            {"max_result_bytes": 1023}, {"max_result_bytes": 4_000_001},
            {"token_ref": "env:TEST_PROVIDER_KEY"}, {"depth": 2},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ConfigurationError):
                prepare(configuration | {"child_policy": configuration["child_policy"] | variant}, self.bundle)
            self.assertFalse(self.bundle.exists())
        for invalid in (None, {}, []):
            with self.subTest(child_policy=invalid), self.assertRaises(ConfigurationError):
                prepare(configuration | {"child_policy": invalid}, self.bundle)
            self.assertFalse(self.bundle.exists())
        with self.assertRaisesRegex(ConfigurationError, "protected root"):
            prepare(configuration | {"runtime": {"uid": 60000}}, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_children_require_the_host_model_policy_and_current_component_entrypoints(self):
        configuration = self.model_configuration() | {"child_policy": {
            "max_children": 2, "max_child_seconds": 30, "max_child_turns": 6, "capabilities": ["python"],
        }}
        no_model = deepcopy(configuration)
        no_model.pop("model_policy")
        with self.assertRaisesRegex(ConfigurationError, "requires an explicit model_policy"):
            prepare(no_model, self.bundle)
        self.assertFalse(self.bundle.exists())
        with self.assertRaisesRegex(ConfigurationError, "managed child entry points"):
            prepare(configuration, self.bundle)
        self.assertFalse(self.bundle.exists())
        configuration.pop("child_policy")
        prepare(configuration, self.bundle)
        self.assertNotIn("child_policy", self.read("manifest.json")["configuration"])
        self.assertNotIn("children", self.read("config/openjules.json"))
        profile = self.read("config/polyphony.json")["repositories"]["github:github.com/owner/project"]["profiles"]["approved"]
        self.assertEqual(set(profile), {"base", "attempt_seconds", "capabilities"})

    def test_doctor_requires_ready_validation_for_every_repository(self):
        # Real local-probe ordering and refusals are covered in test_installation_context.
        prepare(self.config, self.bundle)
        data = self.read("manifest.json")["configuration"]
        report = {"deployment_id": data["deployment_id"], "repositories": [{"validation": {"ready": True}}]}
        successful_check = subprocess.CompletedProcess([], 0, json.dumps(report), "")
        with patch("zpm.deployment._installation_context"), \
                patch("zpm.deployment.subprocess.run", return_value=successful_check):
            self.assertTrue(_doctor(self.bundle, data)["ready"])
        report["repositories"].append({"validation": {"ready": False}})
        incomplete_check = subprocess.CompletedProcess([], 0, json.dumps(report), "")
        with patch("zpm.deployment._installation_context"), \
                patch("zpm.deployment.subprocess.run", return_value=incomplete_check):
            self.assertFalse(_doctor(self.bundle, data)["ready"])

    def test_doctor_cannot_turn_deployment_identifier_into_a_mutating_command(self):
        configuration = self.config | {"deployment_id": "reconcile"}
        prepare(configuration, self.bundle)
        data = self.read("manifest.json")["configuration"]
        report = {"deployment_id": "reconcile", "repositories": [{"validation": {"ready": True}}]}
        with patch("zpm.deployment._installation_context"), \
                patch("zpm.deployment.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(report), "")) as invoke:
            self.assertTrue(_doctor(self.bundle, data)["ready"])
        argv = invoke.call_args.args[0]
        identity_index = argv.index("--deployment-id") + 1
        self.assertEqual(argv[identity_index], "reconcile")
        self.assertEqual(argv[identity_index + 1], "check")
        self.assertNotIn("--watch", argv)

    def test_trial_diagnostics_redact_tokens_even_at_the_log_tail_boundary(self):
        path = Path(__file__).parent / "contracts/check_stack.py"
        specification = importlib.util.spec_from_file_location("managed_stack_trial", path)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        token = "private-test-credential-" * 3
        (self.root / "openjules.log").write_text("earlier-output" + token + "x" * 19970 + token)
        result = module.diagnostics(self.root, "failed " + token, [], {"zpm": "a" * 40}, {token})
        exported = (self.root / "diagnostics.json").read_text()
        self.assertNotIn(token, exported)
        self.assertNotIn("credential", result["logs"]["openjules"])
        self.assertIn("[redacted]", result["error"])
        self.assertLessEqual(len(result["logs"]["openjules"]), 20000)
        self.assertEqual((self.root / "diagnostics.json").stat().st_mode & 0o777, 0o600)

    def test_trial_waits_for_retirement_when_cached_capacity_still_matches(self):
        path = Path(__file__).parent / "contracts/check_stack.py"
        specification = importlib.util.spec_from_file_location("managed_stack_trial", path)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        def plan(healthy, reserved, *identities):
            return {"repositories": [{"healthy": healthy, "reserved": reserved,
                                       "workers": [{"worker_id": identity} for identity in identities]}]}

        stale = plan(2, 2, "completed", "idle")
        starting_replacement = plan(1, 2, "replacement", "idle")
        converged = plan(2, 2, "replacement", "idle")
        read_plan = Mock(side_effect=[stale, starting_replacement, converged])
        processes = Mock()
        with patch.object(module.time, "sleep"):
            result = module.wait_for_capacity(read_plan, processes, 2, retired_worker_ids={"completed"})
        self.assertIs(result, converged)
        self.assertEqual(read_plan.call_count, 3)
        self.assertEqual(processes.check.call_count, 3)

    def test_manager_sigterm_stops_all_real_service_processes(self):
        pid_paths = []
        for service in SOURCE_FILES:
            checkout = Path(self.sources[service]["checkout"])
            entrypoint = "zpm/__main__.py" if service == "zpm" else SOURCE_FILES[service]
            path = checkout / entrypoint
            path.parent.mkdir(parents=True, exist_ok=True)
            pid_path = self.root / (service + ".pid")
            pid_paths.append(pid_path)
            path.write_text("from pathlib import Path\nimport os,time\n" +
                            f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n" +
                            "while True: time.sleep(1)\n")
            (checkout / entrypoint.split("/")[0] / "__init__.py").write_text("")
            if service == "openjules":
                from tests.test_installation_context import PROBE_FIXTURE
                (checkout / "src/worker/execution_context.py").write_text(PROBE_FIXTURE)
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "service fixture"], check=True, capture_output=True)
            self.config["sources"][service]["revision"] = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        prepare(self.config, self.bundle)
        for service in SOURCE_FILES:
            interpreter = self.bundle / "environments" / service / "bin/python"
            if service == "openjules":
                venv.EnvBuilder(with_pip=False).create(interpreter.parent.parent)
            else:
                interpreter.parent.mkdir(parents=True)
                interpreter.symlink_to(sys.executable)
        installation_context.publish_selector(self.bundle)
        completion = {"revision": self.config["sources"]["openjules"]["revision"], "python": sys.version}
        installation_context.publish_private(
            self.bundle / "environments/openjules/bootstrap-complete.json",
            (json.dumps(completion, sort_keys=True, indent=2) + "\n").encode(),
        )
        program = "import json; from pathlib import Path; from zpm.deployment import _start; " + \
                  f"root=Path({str(self.bundle)!r}); " + \
                  "raise SystemExit(_start(root,json.loads((root/'manifest.json').read_text())['configuration']))"
        process = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not all(path.exists() for path in pid_paths) and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.02)
            self.assertTrue(all(path.exists() for path in pid_paths), "service fixtures did not all start")
            with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("bootstrap mutated an active installation")):
                with self.assertRaisesRegex(ConfigurationError, "already running"):
                    _bootstrap(self.bundle, self.read("manifest.json")["configuration"])
            process.terminate()
            self.assertEqual(process.wait(timeout=8), 130)
            for path in pid_paths:
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(path.read_text()), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
