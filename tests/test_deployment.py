"""Deployment configuration is a boundary, not evidence of runtime readiness."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from zpm.deployment import MANAGED_ENTRYPOINTS, SOURCE_FILES, _bootstrap, _command, _doctor, _environment, prepare, verify
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

    def test_doctor_requires_ready_validation_for_every_repository(self):
        prepare(self.config, self.bundle)
        data = self.read("manifest.json")["configuration"]
        report = {"deployment_id": data["deployment_id"], "repositories": [{"validation": {"ready": True}}]}
        successful_check = subprocess.CompletedProcess([], 0, json.dumps(report), "")
        with patch("zpm.deployment.subprocess.run", return_value=successful_check):
            self.assertTrue(_doctor(self.bundle, data)["ready"])
        report["repositories"].append({"validation": {"ready": False}})
        incomplete_check = subprocess.CompletedProcess([], 0, json.dumps(report), "")
        with patch("zpm.deployment.subprocess.run", return_value=incomplete_check):
            self.assertFalse(_doctor(self.bundle, data)["ready"])

    def test_doctor_cannot_turn_deployment_identifier_into_a_mutating_command(self):
        configuration = self.config | {"deployment_id": "reconcile"}
        prepare(configuration, self.bundle)
        data = self.read("manifest.json")["configuration"]
        report = {"deployment_id": "reconcile", "repositories": [{"validation": {"ready": True}}]}
        with patch("zpm.deployment.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(report), "")) as invoke:
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
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "service fixture"], check=True, capture_output=True)
            self.config["sources"][service]["revision"] = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        prepare(self.config, self.bundle)
        for service in SOURCE_FILES:
            interpreter = self.bundle / "environments" / service / "bin/python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
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
