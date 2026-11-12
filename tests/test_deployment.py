"""Deployment configuration is a boundary, not evidence of runtime readiness."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
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
from zpm import deployment, installation_context
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

    def public_configuration(self):
        configuration = self.child_configuration()
        self.public_scope = self.root / "public-scope.key"
        self.public_scope_bytes = "private-scope-canary-\u2603\r\nretain trailing newline\n".encode("utf-8")
        self.public_scope.write_bytes(self.public_scope_bytes)
        self.public_scope.chmod(0o600)
        configuration["command"] = [
            "python3", "-I", "-S", "/opt/openjules/managed_root_runner.py",
            "--public-socket", "/run/openjules-model/public.sock",
            "--max-turns", "12", "--control-timeout-seconds", "1.5",
            "--socket", "/run/openjules-model/model.sock",
            "--verify-json", '["python3","-m","unittest"]',
            "--control-socket", "/run/openjules-model/control.sock",
        ]
        configuration["child_policy"].update({
            "outcome_protocol": "initial-v1",
            "public_discussion": {
                "protocol": "managed-public-pull-v1", "public_approved": True,
                "topic": "  approved public topic  ",
                "server": {"scope_key_file": str(self.public_scope),
                           "bootstrap": {"host": "127.0.0.1", "port": 41001}},
                "personas": {"reviewer": "  Preserve independent public reasoning.  "},
                "instructions": {"question": "Discuss only this approved public question."},
                "invitations": {"peer": {"target": "0123456789abcdef",
                                         "content": "An optional approved public invitation."}},
            },
        })
        return configuration

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
        self.assertNotIn("session_protocol", data["child_policy"])
        self.assertNotIn("outcome_protocol", canonical)
        self.assertNotIn("session_protocol", canonical)
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

    def test_cold_child_sessions_propagate_only_to_the_runtime_policy(self):
        configuration = self.child_configuration()
        configuration["child_policy"].update({
            "outcome_protocol": "initial-v1", "session_protocol": "cold-clarification-v1",
        })
        prepare(configuration, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        normalized = self.read("manifest.json")["configuration"]
        policy = self.read("config/openjules.json")["children"][0]["policy"]
        self.assertEqual(policy, normalized["child_policy"])
        self.assertEqual(policy["session_protocol"], "cold-clarification-v1")
        self.assertEqual(policy["outcome_protocol"], "initial-v1")
        self.assertEqual(policy["command"], ["python3", "-I", "-S", "/opt/openjules/managed_runner.py"])
        canonical = self.read("config/polyphony.json")["repositories"]["github:github.com/owner/project"]["profiles"]["approved"]
        self.assertEqual(canonical, {
            "base": configuration["source"]["revision"], "attempt_seconds": 60, "capabilities": ["python"],
            "max_children": 2, "max_child_seconds": 30, "max_child_turns": 6, "child_capabilities": ["python"],
        })
        self.assertEqual(self.read("config/provider.json")["max_workers"], configuration["max_workers"])

    def test_cold_child_sessions_reject_invalid_opt_in_before_writing(self):
        configuration = self.child_configuration()
        variants = [
            {"outcome_protocol": "initial-v1", "session_protocol": value}
            for value in (None, False, True, 1, 1.0, "", "future-v2", "cold-clarification-v1 ", [], {})
        ]
        variants.extend((
            {"session_protocol": "cold-clarification-v1"},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": None},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": "initial-v1", "max_children": 1},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": "initial-v1",
             "command": ["python3", "custom.py"]},
        ))
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ConfigurationError):
                prepare(configuration | {"child_policy": configuration["child_policy"] | variant}, self.bundle)
            self.assertFalse(self.bundle.exists())
        no_model = deepcopy(configuration)
        no_model.pop("model_policy")
        no_model["child_policy"].update({
            "outcome_protocol": "initial-v1", "session_protocol": "cold-clarification-v1",
        })
        with self.assertRaisesRegex(ConfigurationError, "requires an explicit model_policy"):
            prepare(no_model, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_cold_child_session_opt_in_cannot_change_an_existing_bundle(self):
        configuration = self.child_configuration()
        configuration["child_policy"]["outcome_protocol"] = "initial-v1"
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.bundle = self.root / ("sessions-" + str(enabled))
                selected = deepcopy(configuration)
                if enabled:
                    selected["child_policy"]["session_protocol"] = "cold-clarification-v1"
                prepare(selected, self.bundle)
                manifest = self.read("manifest.json")
                names = ("manifest.json", *manifest["files"])
                sealed = {name: (self.bundle / name).read_bytes() for name in names}
                if not enabled:
                    expected = {
                        "max_children": 2, "max_child_seconds": 30, "max_child_turns": 6,
                        "capabilities": ["python"], "uid_start": 60000,
                        "command": ["python3", "-I", "-S", "/opt/openjules/managed_runner.py"],
                        "verify_argv": ["python3", "-m", "unittest", "discover"],
                        "max_result_bytes": 4_000_000, "outcome_protocol": "initial-v1",
                    }
                    self.assertEqual(manifest["configuration"]["child_policy"], expected)
                    self.assertNotIn(b'"session_protocol"', sealed["config/openjules.json"])
                with Store(self.bundle / "state/zpm.sqlite3") as store:
                    store.change("owner/project", "target", 1)
                    store.change("owner/project", "remove")
                    retained_rows = store.bindings()
                artifact = self.bundle / "state/openjules/retained-session-evidence"
                artifact.write_bytes(b"retained capture")
                changed = deepcopy(selected)
                if enabled:
                    changed["child_policy"].pop("session_protocol")
                else:
                    changed["child_policy"]["session_protocol"] = "cold-clarification-v1"
                with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("credential rotation")), \
                        patch("zpm.deployment.Store", side_effect=AssertionError("state mutation")):
                    self.assertTrue(prepare(selected, self.bundle)["reused"])
                    with self.assertRaisesRegex(ConfigurationError, "different configuration"):
                        prepare(changed, self.bundle)
                self.assertEqual(sealed, {name: (self.bundle / name).read_bytes() for name in names})
                self.assertEqual(artifact.read_bytes(), b"retained capture")
                with Store(self.bundle / "state/zpm.sqlite3") as store:
                    self.assertEqual(store.bindings(), retained_rows)

    def test_public_discussion_seals_exact_bytes_without_expanding_canonical_policy(self):
        configuration = self.public_configuration()
        original = deepcopy(configuration)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("offline network")):
            result = prepare(configuration, self.bundle)
            checked = verify(self.bundle)
        self.assertEqual(configuration, original)
        self.assertFalse(result["services_started"])
        self.assertFalse(checked["remote_access_checked"])
        manifest = self.read("manifest.json")
        normalized = manifest["configuration"]
        supplied = normalized["child_policy"]["public_discussion"]
        expected_hash = hashlib.sha256(self.public_scope_bytes).hexdigest()
        self.assertEqual(supplied["server"], {
            "scope_key_file": str(self.public_scope), "scope_key_sha256": expected_hash,
            "bootstrap": {"host": "127.0.0.1", "port": 41001},
        })
        runtime = self.read("config/openjules.json")
        generated = runtime["children"][0]["policy"]["public_discussion"]
        self.assertEqual(generated | {"server": supplied["server"]}, supplied)
        server = generated["server"]
        self.assertEqual(server["python"], str(self.bundle / "environments/polyphony/bin/python"))
        self.assertFalse(Path(server["python"]).exists(), "preparation must not bootstrap an interpreter")
        self.assertEqual(server["cwd"], configuration["sources"]["polyphony"]["checkout"])
        self.assertEqual(server["scope_key_sha256"], expected_hash)
        self.assertEqual(server["bootstrap"], supplied["server"]["bootstrap"])
        copy = Path(server["scope_key_file"])
        self.assertEqual(copy.parent, self.bundle / "secrets")
        self.assertRegex(copy.name, r"^public-scope-[0-9a-f]{64}\.key$")
        self.assertNotEqual(copy, self.public_scope)
        self.assertEqual(copy.read_bytes(), self.public_scope_bytes)
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
        self.assertEqual(copy.stat().st_nlink, 1)
        relative = str(copy.relative_to(self.bundle))
        self.assertEqual(manifest["files"][relative], expected_hash)
        self.assertEqual(runtime["profiles"][0]["command"], configuration["command"])
        self.assertEqual(runtime["models"][0]["policy"]["max_calls"], configuration["model_policy"]["max_calls"])
        canonical = self.read("config/polyphony.json")["repositories"]["github:github.com/owner/project"]["profiles"]["approved"]
        self.assertEqual(canonical, {
            "base": configuration["source"]["revision"], "attempt_seconds": 60, "capabilities": ["python"],
            "max_children": 2, "max_child_seconds": 30, "max_child_turns": 6, "child_capabilities": ["python"],
        })
        self.assertEqual(self.read("config/provider.json")["max_workers"], configuration["max_workers"])
        for name in ("manifest.json", *manifest["files"]):
            if name != relative:
                self.assertNotIn(b"private-scope-canary-", (self.bundle / name).read_bytes(), name)
        self.assertNotIn("private-scope-canary-", json.dumps(result) + json.dumps(checked))
        self.assertIn("original external scope file", (self.bundle / "README.txt").read_text())

    def test_public_discussion_rejects_unapproved_shapes_before_preparation(self):
        configuration = self.public_configuration()
        public = configuration["child_policy"]["public_discussion"]
        variants = [
            None, {}, public | {"extra": True}, public | {"protocol": "future-v2"},
            public | {"public_approved": 1}, public | {"public_approved": False},
            public | {"topic": ""}, public | {"topic": "x\nhidden"},
            public | {"topic": "\u754c" * 86}, public | {"topic": "\ud800"},
            public | {"personas": {}}, public | {"personas": {str(i): "x" for i in range(4)}},
            public | {"personas": {"bad key": "x"}}, public | {"personas": {"x" * 65: "x"}},
            public | {"personas": {"one": " \t "}},
            public | {"instructions": {}}, public | {"instructions": {str(i): "x" for i in range(9)}},
            public | {"instructions": {"one": "x" * 4097}},
            public | {"instructions": {"one": "\u754c" * 1366}},
            public | {"instructions": {"one": "x\x00y"}},
            public | {"invitations": {"one": {"target": "0123456789abcdef", "content": " "}}},
            public | {"invitations": {"one": {"target": "ABCDEF0123456789", "content": "x"}}},
            public | {"invitations": {"one": {"target": "0123456789abcdef", "content": "x", "extra": 1}}},
            public | {"invitations": {"a": {"target": "0123456789abcdef", "content": "x"},
                                      "b": {"target": "0123456789abcdef", "content": "y"}}},
            public | {"server": public["server"] | {"python": sys.executable}},
            public | {"server": public["server"] | {"cwd": str(self.root)}},
            public | {"server": public["server"] | {"scope_key_sha256": None}},
            public | {"server": public["server"] | {"scope_key_sha256": "A" * 64}},
            public | {"server": public["server"] | {"scope_key_sha256": "0" * 64}},
            public | {"server": public["server"] | {"bootstrap": None}},
            public | {"server": public["server"] | {"bootstrap": {"host": "localhost", "port": 41001}}},
            public | {"server": public["server"] | {"bootstrap": {"host": "127.0.0.1", "port": True}}},
        ]
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("invalid input wrote credentials")), \
                patch("zpm.deployment.Store", side_effect=AssertionError("invalid input wrote state")):
            for index, value in enumerate(variants):
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    prepare(configuration | {"child_policy": configuration["child_policy"] | {
                        "public_discussion": value,
                    }}, self.bundle)
                self.assertFalse(self.bundle.exists())
        # Topic whitespace is preserved; unlike public text values it is not trimmed.
        allowed = deepcopy(configuration)
        allowed["child_policy"]["public_discussion"]["topic"] = " "
        allowed["child_policy"]["public_discussion"]["invitations"] = {}
        allowed["child_policy"]["public_discussion"]["server"].pop("bootstrap")
        prepare(allowed, self.bundle)
        self.assertEqual(self.read("config/openjules.json")["children"][0]["policy"]["public_discussion"]["topic"], " ")

    def test_public_discussion_requires_existing_model_outcome_and_exact_root_family(self):
        configuration = self.public_configuration()
        variants = []
        without_model = deepcopy(configuration)
        without_model.pop("model_policy")
        variants.append(without_model)
        for changes in (
            {"outcome_protocol": None}, {"max_children": 1},
            {"command": ["python3", "custom-child.py"]},
            {"session_protocol": "cold-clarification-v1"},
        ):
            variants.append(configuration | {"child_policy": configuration["child_policy"] | changes})
        without_outcome = deepcopy(configuration)
        without_outcome["child_policy"].pop("outcome_protocol")
        variants.append(without_outcome)
        prefix = ["python3", "-I", "-S", "/opt/openjules/managed_root_runner.py"]
        sockets = [
            "--socket", "/run/openjules-model/model.sock",
            "--control-socket", "/run/openjules-model/control.sock",
            "--public-socket", "/run/openjules-model/public.sock",
        ]
        commands = [
            ["python3", "custom-root.py"], prefix + sockets[:-2],
            prefix + sockets + ["--public-socket", "/run/openjules-model/public.sock"],
            prefix + sockets + ["--unknown", "1"], prefix + sockets + ["positional"],
            prefix + ["--socket", "/tmp/other.sock", *sockets[2:]],
        ]
        commands.extend(prefix + sockets + [name, value] for name, value in (
            ("--max-turns", "0"), ("--max-turns", "1001"), ("--max-turns", "+2"),
            ("--control-timeout-seconds", "0"), ("--control-timeout-seconds", "121"),
            ("--control-timeout-seconds", "nan"), ("--control-timeout-seconds", "inf"),
            ("--verify-json", "{}"), ("--verify-json", "[null]"),
            ("--verify-json", '[""]'), ("--verify-json", '["a\\u0000b"]'),
            ("--verify-json", json.dumps(["x"] * 129)),
        ))
        variants.extend(configuration | {"command": command} for command in commands)
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("invalid command wrote credentials")):
            for index, variant in enumerate(variants):
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    prepare(variant, self.bundle)
                self.assertFalse(self.bundle.exists())
        prepare(configuration, self.bundle)
        self.assertEqual(self.read("config/openjules.json")["profiles"][0]["command"], configuration["command"])

    def test_public_discussion_aggregate_bound_uses_escaped_canonical_bytes(self):
        configuration = self.public_configuration()
        public = configuration["child_policy"]["public_discussion"]
        oversized = deepcopy(configuration)
        value = oversized["child_policy"]["public_discussion"]
        value["personas"] = {str(index): "\u754c" * 1365 for index in range(3)}
        value["instructions"] = {str(index): "\u754c" * 1365 for index in range(8)}
        self.assertTrue(all(len(item.encode()) == 4095 for item in value["personas"].values()))
        self.assertLess(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()), 65536)
        self.assertGreater(len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()), 65536)
        with self.assertRaisesRegex(ConfigurationError, "65536-byte canonical bound"):
            prepare(oversized, self.bundle)
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.public_scope.read_bytes(), self.public_scope_bytes)
        prepare(configuration, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        self.assertEqual(self.read("manifest.json")["configuration"]["child_policy"]["public_discussion"]["personas"],
                         public["personas"])

    def test_public_scope_change_between_parse_and_generation_refuses_publication(self):
        configuration = self.public_configuration()
        original = deployment._prepare_files
        changed = b"replacement-private-scope"
        def change_before_generation(data, stage, final):
            self.public_scope.write_bytes(changed)
            return original(data, stage, final)
        with patch("zpm.deployment._prepare_files", side_effect=change_before_generation):
            with self.assertRaisesRegex(ConfigurationError, "scope hash does not match"):
                prepare(configuration, self.bundle)
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.public_scope.read_bytes(), changed)

    def test_public_scope_copy_uses_the_hash_checked_descriptor_bytes(self):
        configuration = self.public_configuration()
        original = deployment._public_scope_file
        changed = b"later-private-scope-must-not-be-copied"
        returned = []
        def replace_after_checked_read(path, expected=None):
            result = original(path, expected)
            if path == str(self.public_scope) and expected is not None and not returned:
                returned.append(result[1])
                self.public_scope.write_bytes(changed)
            return result
        with patch("zpm.deployment._public_scope_file", side_effect=replace_after_checked_read):
            prepare(configuration, self.bundle)
        self.assertEqual(returned, [self.public_scope_bytes])
        generated = self.read("config/openjules.json")["children"][0]["policy"]["public_discussion"]
        copy = Path(generated["server"]["scope_key_file"])
        self.assertEqual(copy.read_bytes(), self.public_scope_bytes)
        self.assertNotEqual(copy.read_bytes(), changed)
        with self.assertRaisesRegex(ConfigurationError, "scope hash does not match"):
            verify(self.bundle)
        self.public_scope.write_bytes(self.public_scope_bytes)
        self.assertTrue(verify(self.bundle)["valid"])

    def test_public_original_scope_remains_required_before_every_manage_operation(self):
        configuration = self.public_configuration()
        prepare(configuration, self.bundle)
        manifest = self.read("manifest.json")
        sealed = {name: (self.bundle / name).read_bytes() for name in ("manifest.json", *manifest["files"])}
        for failure in ("missing", "changed", "permissions"):
            with self.subTest(failure=failure):
                if failure == "missing":
                    self.public_scope.unlink()
                elif failure == "changed":
                    self.public_scope.write_bytes(b"unapproved-private-scope")
                else:
                    self.public_scope.chmod(0o644)
                try:
                    with self.assertRaises(ConfigurationError):
                        verify(self.bundle)
                    with self.assertRaises(ConfigurationError):
                        prepare(configuration, self.bundle)
                    with patch("zpm.deployment._start", side_effect=AssertionError("started invalid binding")), \
                            patch("zpm.deployment._doctor", side_effect=AssertionError("contacted invalid binding")), \
                            patch("zpm.deployment._bootstrap", side_effect=AssertionError("bootstrapped invalid binding")):
                        for operation in ("verify", "bootstrap", "start", "doctor"):
                            output = io.StringIO()
                            with redirect_stderr(output), redirect_stdout(io.StringIO()):
                                self.assertEqual(deployment.manage(self.bundle, [operation]), 2)
                            self.assertNotIn("private-scope-canary-", output.getvalue())
                    self.assertEqual(sealed, {name: (self.bundle / name).read_bytes() for name in sealed})
                finally:
                    self.public_scope.write_bytes(self.public_scope_bytes)
                    self.public_scope.chmod(0o600)
                self.assertTrue(verify(self.bundle)["valid"])

    def test_public_sealed_scope_requires_original_digest_and_private_descriptor(self):
        configuration = self.public_configuration()
        prepare(configuration, self.bundle)
        manifest_path = self.bundle / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        manifest = self.read("manifest.json")
        copy = Path(self.read("config/openjules.json")["children"][0]["policy"]["public_discussion"]["server"]["scope_key_file"])
        relative = str(copy.relative_to(self.bundle))
        for failure in ("missing-record", "coherent-copy-rewrite", "executable-mode", "hard-link"):
            with self.subTest(failure=failure):
                changed = deepcopy(manifest)
                alias = self.root / "scope-alias"
                if failure == "missing-record":
                    changed["files"].pop(relative)
                elif failure == "coherent-copy-rewrite":
                    copy.write_bytes(b"coherently-rehashed-unapproved-copy")
                    changed["files"][relative] = hashlib.sha256(copy.read_bytes()).hexdigest()
                elif failure == "executable-mode":
                    copy.chmod(0o700)
                else:
                    os.link(copy, alias)
                manifest_path.write_text(json.dumps(changed))
                try:
                    with self.assertRaises(ConfigurationError):
                        verify(self.bundle)
                finally:
                    if alias.exists():
                        alias.unlink()
                    copy.write_bytes(self.public_scope_bytes)
                    copy.chmod(0o600)
                    manifest_path.write_bytes(original_manifest)
                self.assertTrue(verify(self.bundle)["valid"])

    def test_public_opt_in_and_reference_cannot_adopt_or_reset_retained_installation(self):
        configuration = self.public_configuration()
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.bundle = self.root / ("public-" + str(enabled))
                selected = deepcopy(configuration)
                if not enabled:
                    selected["child_policy"].pop("public_discussion")
                    selected["command"] = self.config["command"]
                prepare(selected, self.bundle)
                manifest = self.read("manifest.json")
                names = ("manifest.json", *manifest["files"])
                sealed = {name: (self.bundle / name).read_bytes() for name in names}
                if not enabled:
                    self.assertNotIn("public_discussion", manifest["configuration"]["child_policy"])
                    self.assertNotIn(b'"public_discussion"', sealed["config/openjules.json"])
                    self.assertFalse(list((self.bundle / "secrets").glob("public-scope-*.key")))
                with Store(self.bundle / "state/zpm.sqlite3") as store:
                    store.change("owner/project", "target", 1)
                    store.change("owner/project", "remove")
                    retained = store.bindings()
                capture = self.bundle / "state/openjules/retained-public-evidence"
                capture.write_bytes(b"retained evidence")
                variants = []
                toggled = deepcopy(selected)
                if enabled:
                    toggled["child_policy"].pop("public_discussion")
                else:
                    toggled["child_policy"]["public_discussion"] = deepcopy(configuration["child_policy"]["public_discussion"])
                    toggled["command"] = configuration["command"]
                variants.append(toggled)
                if enabled:
                    changed_topic = deepcopy(selected)
                    changed_topic["child_policy"]["public_discussion"]["topic"] += " changed"
                    variants.append(changed_topic)
                    alias = self.root / "equal-scope-new-reference.key"
                    alias.write_bytes(self.public_scope_bytes)
                    alias.chmod(0o600)
                    changed_reference = deepcopy(selected)
                    changed_reference["child_policy"]["public_discussion"]["server"]["scope_key_file"] = str(alias)
                    variants.append(changed_reference)
                with patch("zpm.deployment.Store", side_effect=AssertionError("reinitialized state")), \
                        patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("rotated credentials")):
                    self.assertTrue(prepare(selected, self.bundle)["reused"])
                    for variant in variants:
                        with self.assertRaisesRegex(ConfigurationError, "different configuration"):
                            prepare(variant, self.bundle)
                self.assertEqual(sealed, {name: (self.bundle / name).read_bytes() for name in names})
                self.assertEqual(capture.read_bytes(), b"retained evidence")
                with Store(self.bundle / "state/zpm.sqlite3") as store:
                    self.assertEqual(store.bindings(), retained)

    def test_raw_deployment_cli_refuses_duplicate_public_approvals_and_preserves_omission(self):
        configuration = self.public_configuration()
        public = configuration["child_policy"]["public_discussion"]
        raw = json.dumps(configuration)
        child_raw = json.dumps(configuration["child_policy"])
        omitted_child = deepcopy(configuration["child_policy"])
        omitted_child.pop("public_discussion")
        key = json.dumps("scope_key_file") + ": " + json.dumps(str(self.public_scope))
        persona = '"reviewer": ' + json.dumps(public["personas"]["reviewer"])
        cases = [
            raw.replace('"public_approved": true', '"public_approved": false, "public_approved": true', 1),
            raw.replace(persona, '"reviewer": "unapproved", ' + persona, 1),
            raw.replace(key, '"scope_key_file": "/unapproved", ' + key, 1),
            raw.replace('"child_policy": ' + child_raw,
                        '"child_policy": ' + child_raw + ', "child_policy": ' + json.dumps(omitted_child), 1),
            raw.replace('"public_discussion": ' + json.dumps(public),
                        '"public_discussion": null, "public_discussion": ' + json.dumps(public), 1),
            raw.replace('"max_workers": 8', '"max_workers": NaN, "max_workers": 8', 1),
        ]
        input_path = self.root / "raw-deployment.json"
        for index, ambiguous in enumerate(cases):
            with self.subTest(index=index):
                self.assertNotEqual(ambiguous, raw)
                # These really are syntactically valid last-key-wins documents.
                self.assertIsInstance(json.loads(ambiguous), dict)
                input_path.write_text(ambiguous)
                result = subprocess.run(
                    [sys.executable, "-m", "zpm", "deployment", "prepare", str(input_path),
                     "--directory", str(self.bundle)],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("private-scope-canary-", result.stderr)
                self.assertFalse(self.bundle.exists())
        # Only the opted-in raw configuration changes. An omitted legacy profile
        # retains its established last-key-wins loader and exact generated policy.
        omitted = deepcopy(configuration)
        omitted["child_policy"].pop("public_discussion")
        omitted["command"] = self.config["command"]
        legacy_raw = json.dumps(omitted).replace('"max_workers": 8', '"max_workers": 9, "max_workers": 8', 1)
        input_path.write_text(legacy_raw)
        legacy = self.root / "legacy-raw"
        result = subprocess.run(
            [sys.executable, "-m", "zpm", "deployment", "prepare", str(input_path),
             "--directory", str(legacy)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((legacy / "manifest.json").read_text())["configuration"]["max_workers"], 8)
        self.assertNotIn(b'"public_discussion"', (legacy / "config/openjules.json").read_bytes())
        input_path.write_text(raw)
        accepted = subprocess.run(
            [sys.executable, "-m", "zpm", "deployment", "prepare", str(input_path),
             "--directory", str(self.bundle)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(verify(self.bundle)["valid"])

    def test_retained_public_manifest_rejects_duplicate_keys_before_reuse_or_start(self):
        configuration = self.public_configuration()
        prepare(configuration, self.bundle)
        path = self.bundle / "manifest.json"
        original = path.read_text()
        variants = [
            original.replace('"public_approved": true', '"public_approved": false, "public_approved": true', 1),
            original.replace('"reviewer": "  Preserve independent public reasoning.  "',
                             '"reviewer": "unapproved", "reviewer": "  Preserve independent public reasoning.  "', 1),
        ]
        for index, raw in enumerate(variants):
            with self.subTest(index=index):
                self.assertNotEqual(raw, original)
                self.assertEqual(json.loads(raw), json.loads(original))
                path.write_text(raw)
                try:
                    with self.assertRaises(ConfigurationError):
                        verify(self.bundle)
                    with self.assertRaises(ConfigurationError):
                        prepare(configuration, self.bundle)
                    with patch("zpm.deployment._start", side_effect=AssertionError("started ambiguous manifest")), \
                            patch("zpm.deployment._bootstrap", side_effect=AssertionError("bootstrapped ambiguous manifest")), \
                            patch("zpm.deployment._doctor", side_effect=AssertionError("contacted ambiguous manifest")):
                        for operation in ("start", "bootstrap", "doctor"):
                            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                                self.assertEqual(deployment.manage(self.bundle, [operation]), 2)
                finally:
                    path.write_text(original)
        self.assertTrue(verify(self.bundle)["valid"])

    def test_public_copied_scope_first_read_is_bounded_through_a_real_fifo_swap(self):
        configuration = self.public_configuration()
        prepare(configuration, self.bundle)
        generated = self.read("config/openjules.json")["children"][0]["policy"]["public_discussion"]
        copy = Path(generated["server"]["scope_key_file"])
        program = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "from zpm.deployment import verify\n"
            "from zpm.models import ConfigurationError\n"
            "root, copy = map(Path, sys.argv[1:])\n"
            "real_stat = os.stat\n"
            "swapped = []\n"
            "def stat_then_replace(path, *args, **kwargs):\n"
            "    observed = real_stat(path, *args, **kwargs)\n"
            "    if str(path) in {str(copy), copy.name} and not swapped:\n"
            "        swapped.append(True)\n"
            "        copy.unlink()\n"
            "        os.mkfifo(copy, 0o600)\n"
            "    return observed\n"
            "with patch('zpm.deployment.os.stat', side_effect=stat_then_replace):\n"
            "    try:\n"
            "        verify(root)\n"
            "    except ConfigurationError:\n"
            "        if swapped != [True]:\n"
            "            raise SystemExit(8)\n"
            "        print('copied-leaf-swapped-and-refused')\n"
            "        raise SystemExit(0)\n"
            "raise SystemExit(9)\n"
        )
        try:
            result = subprocess.run([sys.executable, "-c", program, str(self.bundle), str(copy)],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=3, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "copied-leaf-swapped-and-refused\n")
            self.assertEqual(result.stderr, "")
        finally:
            copy.unlink()
            copy.write_bytes(self.public_scope_bytes)
            copy.chmod(0o600)
        self.assertTrue(verify(self.bundle)["valid"])
        # The generic integrity reader must never read this derived secret,
        # even through an equivalent relative spelling in an integrity record.
        manifest_path = self.bundle / "manifest.json"
        original = manifest_path.read_bytes()
        manifest = self.read("manifest.json")
        relative = str(copy.relative_to(self.bundle))
        manifest["files"]["secrets/./" + copy.name] = manifest["files"][relative]
        manifest_path.write_text(json.dumps(manifest))
        real_read = Path.read_bytes
        def forbid_generic_read(path):
            if path == copy:
                raise AssertionError("copied scope reached unbounded generic read_bytes")
            return real_read(path)
        try:
            with patch.object(Path, "read_bytes", forbid_generic_read):
                self.assertTrue(verify(self.bundle)["valid"])
        finally:
            manifest_path.write_bytes(original)

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


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "O_NOFOLLOW"), "Linux private descriptor contract")
class PublicScopeFileTests(unittest.TestCase):
    """Use actual file descriptors; no provider, service or secret disclosure."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "original"
        self.directory.mkdir()
        self.scope = self.directory / "scope.key"
        self.raw = b"private-reader-canary\n"
        self.scope.write_bytes(self.raw)
        self.scope.chmod(0o600)

    def test_exact_private_bytes_and_digest_survive_the_inclusive_bound(self):
        raw = b"x" * 4093 + "\u2603".encode("utf-8")
        self.scope.write_bytes(raw)
        for mode in (0o400, 0o600):
            with self.subTest(mode=mode):
                self.scope.chmod(mode)
                path, observed = deployment._public_scope_file(str(self.scope), hashlib.sha256(raw).hexdigest())
                self.assertEqual(path, self.scope)
                self.assertEqual(observed, raw)
                self.assertEqual(len(observed), 4096)
        with self.assertRaisesRegex(ConfigurationError, "scope hash does not match"):
            deployment._public_scope_file(str(self.scope), "0" * 64)

    def test_private_file_type_mode_owner_link_and_byte_failures_are_fixed_errors(self):
        for raw in (b"", b"x" * 4097, b"with\x00nul", b"\xff"):
            with self.subTest(raw_length=len(raw)):
                self.scope.write_bytes(raw)
                with self.assertRaisesRegex(ConfigurationError, "unchanged private owned UTF-8 file"):
                    deployment._public_scope_file(str(self.scope))
        self.scope.write_bytes(self.raw)
        for mode in (0o000, 0o644, 0o700):
            with self.subTest(mode=mode):
                self.scope.chmod(mode)
                with self.assertRaises(ConfigurationError):
                    deployment._public_scope_file(str(self.scope))
        self.scope.chmod(0o600)
        with patch("zpm.deployment.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ConfigurationError):
                deployment._public_scope_file(str(self.scope))
        alias = self.root / "hard-link"
        os.link(self.scope, alias)
        with self.assertRaises(ConfigurationError):
            deployment._public_scope_file(str(self.scope))
        alias.unlink()
        leaf = self.root / "leaf-link"
        leaf.symlink_to(self.scope)
        parent = self.root / "parent-link"
        parent.symlink_to(self.directory, target_is_directory=True)
        for path in (leaf, parent / self.scope.name, self.directory):
            with self.subTest(path=path.name), self.assertRaises(ConfigurationError):
                deployment._public_scope_file(str(path))
        for value in (None, 1, "", "relative", str(self.directory) + "/../original/scope.key",
                      "//" + str(self.scope).lstrip("/"), str(self.scope) + "\x00", "\ud800"):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ConfigurationError):
                deployment._public_scope_file(value)
        self.assertEqual(deployment._public_scope_file(str(self.scope))[1], self.raw)

    def test_nonregular_fifo_refusal_is_bounded_in_a_real_process(self):
        fifo = self.root / "scope-fifo"
        os.mkfifo(fifo, 0o600)
        program = (
            "import sys\n"
            "from zpm.deployment import _public_scope_file\n"
            "from zpm.models import ConfigurationError\n"
            "try:\n"
            "    _public_scope_file(sys.argv[1])\n"
            "except ConfigurationError:\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(9)\n"
        )
        result = subprocess.run([sys.executable, "-c", program, str(fifo)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=3, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_unknown_inspection_state_refuses_before_opening_the_leaf(self):
        real_stat, real_lstat, real_open = os.stat, os.lstat, os.open
        opened = []
        def selected(path):
            return str(path) in {self.scope.name, str(self.scope)}
        def denied_stat(path, *args, **kwargs):
            if selected(path):
                raise PermissionError("private-reader-canary")
            return real_stat(path, *args, **kwargs)
        def denied_lstat(path, *args, **kwargs):
            if selected(path):
                raise PermissionError("private-reader-canary")
            return real_lstat(path, *args, **kwargs)
        def recorded_open(path, *args, **kwargs):
            opened.append(str(path))
            if selected(path):
                raise AssertionError("an unreadable leaf was opened")
            return real_open(path, *args, **kwargs)
        with patch("zpm.deployment.os.stat", side_effect=denied_stat), \
                patch("zpm.deployment.os.lstat", side_effect=denied_lstat), \
                patch("zpm.deployment.os.open", side_effect=recorded_open):
            with self.assertRaises(ConfigurationError) as caught:
                deployment._public_scope_file(str(self.scope))
        self.assertNotIn("private-reader-canary", str(caught.exception))
        self.assertTrue(opened)
        self.assertNotIn(self.scope.name, opened)
        self.assertEqual(self.scope.read_bytes(), self.raw)

    def test_actual_mutation_after_read_does_not_return_unstable_bytes(self):
        real_read = os.read
        changed = []
        def mutate_after_read(descriptor, count):
            result = real_read(descriptor, count)
            if result and not changed:
                changed.append(True)
                self.scope.write_bytes(self.raw + b"changed")
            return result
        with patch("zpm.deployment.os.read", side_effect=mutate_after_read):
            with self.assertRaisesRegex(ConfigurationError, "unchanged private owned UTF-8 file"):
                deployment._public_scope_file(str(self.scope))
        self.assertEqual(changed, [True])
        self.assertEqual(self.scope.read_bytes(), self.raw + b"changed")
        self.scope.write_bytes(self.raw)
        self.assertEqual(deployment._public_scope_file(str(self.scope))[1], self.raw)

    def test_actual_parent_replacement_cannot_hide_behind_the_same_leaf_inode(self):
        real_open = os.open
        for kind in ("symlink", "new-directory"):
            with self.subTest(kind=kind):
                original_inode = self.scope.stat().st_ino
                moved = self.root / "retained-original"
                swapped = []
                def swap_before_leaf(path, *args, **kwargs):
                    if str(path) in {self.scope.name, str(self.scope)} and not swapped:
                        swapped.append(True)
                        self.directory.rename(moved)
                        if kind == "symlink":
                            self.directory.symlink_to(moved, target_is_directory=True)
                        else:
                            self.directory.mkdir()
                            self.scope.write_bytes(self.raw)
                            self.scope.chmod(0o600)
                    return real_open(path, *args, **kwargs)
                try:
                    with patch("zpm.deployment.os.open", side_effect=swap_before_leaf):
                        with self.assertRaisesRegex(ConfigurationError, "unchanged private owned UTF-8 file"):
                            deployment._public_scope_file(str(self.scope))
                    self.assertEqual(swapped, [True])
                    self.assertEqual(self.scope.read_bytes(), self.raw)
                    if kind == "symlink":
                        # A leaf-only lstat/open check would still see the same
                        # regular inode through this replaced parent.
                        self.assertTrue(self.directory.is_symlink())
                        self.assertFalse(self.scope.is_symlink())
                        self.assertEqual(self.scope.stat().st_ino, original_inode)
                    else:
                        self.assertNotEqual(self.scope.stat().st_ino, original_inode)
                finally:
                    if swapped:
                        if self.directory.is_symlink():
                            self.directory.unlink()
                        else:
                            self.scope.unlink()
                            self.directory.rmdir()
                        moved.rename(self.directory)
                self.assertEqual(deployment._public_scope_file(str(self.scope))[1], self.raw)


if __name__ == "__main__":
    unittest.main()
