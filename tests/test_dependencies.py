"""Admission, custody and recovery boundaries for finite installer inputs."""

from copy import deepcopy
import base64
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import zipfile

from zpm import dependencies, deployment
from zpm.models import ConfigurationError
from tests import test_deployment


class DependencyTests(unittest.TestCase):
    def setUp(self):
        test_deployment.DeploymentTests.setUp(self)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(mode=0o700)
        self.rows = []
        for name in ("pip", "setuptools", "wheel", "fixture-package"):
            filename = name.replace("-", "_") + "-1.0-py3-none-any.whl"
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr(name.replace("-", "_") + "-1.0.dist-info/METADATA",
                                 "Metadata-Version: 2.1\nName: " + name + "\nVersion: 1.0\n")
            content = raw.getvalue()
            (self.artifacts / filename).write_bytes(content)
            self.rows.append({"filename": filename, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                              "name": name, "version": "1.0"})
        environments = {}
        for service in dependencies.SERVICES:
            checkout = Path(self.sources[service]["checkout"])
            filename = "pyproject.toml" if service == "zpm" else "requirements.txt"
            content = ('[build-system]\nrequires = ["setuptools>=1"]\nbuild-backend = "setuptools.build_meta"\n'
                       '[project]\nname = "zero-point-modulator"\nversion = "0.1.0"\ndependencies = []\n'
                       if service == "zpm" else "fixture-package>=1\n")
            (checkout / filename).write_text(content)
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "dependency inputs"], check=True, capture_output=True)
            revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            self.sources[service]["revision"] = revision
            environments[service] = {"source_revision": revision,
                                     "dependency_files": {filename: hashlib.sha256(content.encode()).hexdigest()},
                                     "wheels": [row["filename"] for row in self.rows]}
        self.plan = {"schema_version": 1, "interpreter": dependencies.interpreter_identity(),
                     "seed": {"method": "sealed-pip-wheel", "filename": self.rows[0]["filename"]},
                     "artifacts": self.rows, "environments": environments}
        self.plan_path = self.root / "plan.json"
        self.save_plan()

    def tearDown(self):
        # Sealed input directories intentionally have no owner write permission.
        for directory, folders, _ in os.walk(self.root):
            Path(directory).chmod(0o700)
        test_deployment.DeploymentTests.tearDown(self)

    def save_plan(self, value=None, *, raw=None):
        content = (json.dumps(self.plan if value is None else value).encode() if raw is None else raw)
        self.plan_path.write_bytes(content)
        self.config["dependency_plan"] = {"manifest": str(self.plan_path), "artifacts": str(self.artifacts),
                                          "sha256": hashlib.sha256(content).hexdigest()}

    def test_prepare_seals_exact_wheel_and_git_source_bytes_without_execution(self):
        with patch.object(dependencies, "interpreter_identity", side_effect=AssertionError("interpreter probe during prepare")), \
                patch.object(dependencies, "_run", side_effect=AssertionError("package execution during prepare")):
            report = deployment.prepare(self.config, self.bundle)
            self.assertEqual(report["dependency_mode"], "sealed-local-wheels-v1")
            self.assertTrue(deployment.verify(self.bundle)["valid"])
        wheel = self.bundle / "dependencies/wheels" / self.rows[0]["filename"]
        self.assertEqual(wheel.read_bytes(), (self.artifacts / wheel.name).read_bytes())
        self.assertEqual(wheel.stat().st_mode & 0o777, 0o400)
        self.assertEqual(wheel.parent.stat().st_mode & 0o777, 0o500)
        self.assertFalse((self.bundle / "environments/sealed-intent.json").exists())
        self.assertEqual((self.bundle / "dependencies/source/pyproject.toml").read_bytes(),
                         (Path(self.sources["zpm"]["checkout"]) / "pyproject.toml").read_bytes())

    def test_closed_schema_and_inventory_refuse_ambiguous_inputs(self):
        mutations = [
            lambda p: p.update(schema_version=True),
            lambda p: p.update(unknown="value"),
            lambda p: p["interpreter"].update(version="3.13.1"),
            lambda p: p["seed"].update(method="ensurepip"),
            lambda p: p["seed"].update(filename=[]),
            lambda p: p["artifacts"][0].update(filename="../pip.whl"),
            lambda p: p["artifacts"][0].update(size=True),
            lambda p: p["artifacts"][0].update(name="PIP"),
            lambda p: p["artifacts"][0].update(version="2.0"),
            lambda p: p["artifacts"].append(p["artifacts"][0]),
            lambda p: p["environments"]["zpm"].update(source_revision="0" * 40),
            lambda p: p["environments"]["zpm"]["dependency_files"].update(unknown="0" * 64),
            lambda p: p["environments"]["zpm"]["wheels"].pop(0),
            lambda p: p["environments"]["zpm"]["wheels"].append(p["environments"]["zpm"]["wheels"][0]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                value = deepcopy(self.plan)
                mutation(value)
                self.save_plan(value)
                with self.assertRaises(ConfigurationError):
                    deployment.prepare(self.config, self.bundle)
                self.assertFalse(self.bundle.exists())

    def test_duplicate_json_and_nonfinite_values_refuse(self):
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"schema_version":NaN}'):
            self.save_plan(raw=raw)
            with self.assertRaises(ConfigurationError):
                deployment.prepare(self.config, self.bundle)

    def test_files_must_be_regular_unlinked_and_in_unlinked_directories(self):
        wheel = self.artifacts / self.rows[0]["filename"]
        content = wheel.read_bytes()
        other = self.root / "wheel-copy"
        other.write_bytes(content)
        cases = (lambda: wheel.symlink_to(other), lambda: os.link(other, wheel), lambda: os.mkfifo(wheel))
        for replace in cases:
            with self.subTest(kind=cases.index(replace)):
                wheel.unlink()
                replace()
                with self.assertRaises(ConfigurationError):
                    deployment.prepare(self.config, self.bundle)
                wheel.unlink()
                wheel.write_bytes(content)
        linked = self.root / "linked-artifacts"
        linked.symlink_to(self.artifacts, target_is_directory=True)
        self.config["dependency_plan"]["artifacts"] = str(linked)
        with self.assertRaises(ConfigurationError):
            deployment.prepare(self.config, self.bundle)

    def test_external_mutation_and_private_copy_mutation_are_not_adopted(self):
        deployment.prepare(self.config, self.bundle)
        external = self.artifacts / self.rows[0]["filename"]
        original = external.read_bytes()
        external.write_bytes(original + b"changed")
        with self.assertRaises(ConfigurationError):
            deployment.verify(self.bundle)
        external.write_bytes(original)
        sealed = self.bundle / "dependencies/wheels" / external.name
        sealed.chmod(0o600)
        sealed.write_bytes(original + b"changed")
        with self.assertRaises(ConfigurationError):
            deployment.verify(self.bundle)

    def test_change_between_admission_and_copy_refuses_publication(self):
        original = dependencies.seal
        def mutate(reference, sources, root):
            path = self.artifacts / self.rows[0]["filename"]
            path.write_bytes(path.read_bytes() + b"changed-after-admission")
            return original(reference, sources, root)
        with patch.object(dependencies, "seal", side_effect=mutate):
            with self.assertRaises(ConfigurationError):
                deployment.prepare(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())
        self.assertTrue(list(self.root.glob(".zpm-prepare-*")))

    def test_git_blob_mismatch_during_source_sealing_refuses(self):
        original = dependencies.seal
        def mutate(reference, sources, root):
            (Path(sources["zpm"]["checkout"]) / "zpm/deployment.py").write_text("replaced source\n")
            return original(reference, sources, root)
        with patch.object(dependencies, "seal", side_effect=mutate):
            with self.assertRaisesRegex(ConfigurationError, "source changed"):
                deployment.prepare(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_interpreter_mismatch_precedes_intent_and_venv(self):
        deployment.prepare(self.config, self.bundle)
        changed = self.plan["interpreter"] | {"version": "3.12.99"}
        with patch.object(dependencies, "interpreter_identity", return_value=changed), \
                patch.object(dependencies, "_run", side_effect=AssertionError("unexpected process")):
            with self.assertRaisesRegex(ConfigurationError, "interpreter"):
                deployment._bootstrap(self.bundle, self.config)
        self.assertEqual(list((self.bundle / "environments").iterdir()), [])

    def test_foreign_environment_or_changed_intent_is_not_repaired(self):
        deployment.prepare(self.config, self.bundle)
        directory = self.bundle / "environments/zpm"
        directory.mkdir(mode=0o700)
        retained = directory / "keep"
        retained.write_text("retained")
        with patch.object(dependencies, "_run", side_effect=AssertionError("unexpected process")):
            with self.assertRaisesRegex(ConfigurationError, "original intent"):
                deployment._bootstrap(self.bundle, self.config)
        self.assertEqual(retained.read_text(), "retained")
        intent = self.bundle / "environments/sealed-intent.json"
        intent.write_text("{}")
        with self.assertRaisesRegex(ConfigurationError, "evidence differs"):
            deployment._bootstrap(self.bundle, self.config)

    def test_incomplete_bootstrap_retains_original_intent_for_retry(self):
        deployment.prepare(self.config, self.bundle)
        with patch.object(dependencies, "_run", side_effect=ConfigurationError("simulated interruption")):
            with self.assertRaisesRegex(ConfigurationError, "simulated interruption"):
                deployment._bootstrap(self.bundle, self.config)
        intent = self.bundle / "environments/sealed-intent.json"
        original = intent.read_bytes()
        credentials = (self.bundle / "secrets/environment.json").read_bytes()
        with patch.object(dependencies, "_run", side_effect=ConfigurationError("same-plan retry")):
            with self.assertRaisesRegex(ConfigurationError, "same-plan retry"):
                deployment._bootstrap(self.bundle, self.config)
        self.assertEqual(original, intent.read_bytes())
        self.assertEqual(credentials, (self.bundle / "secrets/environment.json").read_bytes())
        self.assertFalse((self.bundle / "environments/zpm/bootstrap-complete.json").exists())

    def test_abandoned_stage_bound_refuses_without_changing_original_identity(self):
        deployment.prepare(self.config, self.bundle)
        with patch.object(dependencies, "_run", side_effect=ConfigurationError("interrupted")):
            for _ in range(3):
                with self.assertRaisesRegex(ConfigurationError, "interrupted"):
                    deployment._bootstrap(self.bundle, self.config)
        intent = (self.bundle / "environments/sealed-intent.json").read_bytes()
        with patch.object(dependencies, "_run", side_effect=AssertionError("unexpected fourth command")):
            with self.assertRaisesRegex(ConfigurationError, "three interrupted"):
                deployment._bootstrap(self.bundle, self.config)
        self.assertEqual(len(list((self.bundle / "environments").glob(".sealed-bootstrap-zpm-*"))), 3)
        self.assertEqual((self.bundle / "environments/sealed-intent.json").read_bytes(), intent)

    def test_bounded_processes_reject_output_and_deadline_without_echoing_output(self):
        before = time.monotonic()
        with self.assertRaisesRegex(ConfigurationError, "time bound"):
            dependencies._run([sys.executable, "-I", "-c", "import time;time.sleep(10)"], self.root, {}, 0.1)
        self.assertLess(time.monotonic() - before, 3)
        with self.assertRaisesRegex(ConfigurationError, "output bound"):
            dependencies._run([sys.executable, "-I", "-c", "print('private-canary' * 100000)"], self.root, {}, 5)
        logs = list(self.root.glob(".sealed-command-*.log"))
        self.assertTrue(logs)
        self.assertTrue(all(path.stat().st_size <= dependencies.OUTPUT_LIMIT for path in logs))

    def test_exited_leader_descendants_are_killed_and_reaped(self):
        late = self.root / "late-write"
        script = "import subprocess,sys;subprocess.Popen([sys.executable,'-c',sys.argv[1]])"
        child = "import time,pathlib;time.sleep(.4);pathlib.Path(" + repr(str(late)) + ").write_text('escaped')"
        dependencies._run([sys.executable, "-I", "-c", script, child], self.root, {}, 5)
        time.sleep(0.5)
        self.assertFalse(late.exists())

    def test_manager_death_keeps_lock_until_guardian_reaps_installer(self):
        lock_path = self.root / "manager.lock"
        ready = self.root / "command-ready"
        late = self.root / "late-write"
        code = """
import fcntl, pathlib, sys
from zpm import dependencies
lock = open(sys.argv[1], 'a+')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
with dependencies.command_guard(lock.fileno()):
    dependencies._run([sys.executable, '-I', '-c', sys.argv[2]], pathlib.Path(sys.argv[3]), {}, 10)
"""
        command = ("import pathlib,time;pathlib.Path(" + repr(str(ready)) + ").write_text('ready');"
                   "time.sleep(1);pathlib.Path(" + repr(str(late)) + ").write_text('escaped')")
        manager = subprocess.Popen([sys.executable, "-c", code, str(lock_path), command, str(self.root)],
                                   cwd=Path(deployment.__file__).parent.parent,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            with lock_path.open("a+") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                manager.kill()
                manager.wait(timeout=5)
                acquired = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except BlockingIOError:
                        time.sleep(0.01)
                self.assertTrue(acquired, "guardian must finish owned cleanup and release the original flock")
            time.sleep(1.1)
            self.assertFalse(late.exists())
        finally:
            if manager.poll() is None:
                manager.kill()
                manager.wait(timeout=5)

    def test_native_installed_probe_binds_actual_prefix_tuple_and_record_bytes(self):
        environment = self.root / "empty-venv"
        subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(environment)],
                       check=True, capture_output=True)
        python = environment / "bin/python"
        self.assertEqual(dependencies._installed(python, self.root, {}), {})
        fake = dependencies.interpreter_identity() | {"version": "3.12.99"}
        with patch.object(dependencies, "interpreter_identity", return_value=fake):
            with self.assertRaises(ConfigurationError):
                dependencies._installed(python, self.root, {})
        site = Path(subprocess.check_output([str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip())
        info = site / "fixture-1.0.dist-info"
        info.mkdir()
        metadata = info / "METADATA"
        metadata.write_text("Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n")
        encoded = base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).rstrip(b"=").decode()
        with (info / "RECORD").open("w", newline="") as stream:
            csv.writer(stream).writerows([["fixture-1.0.dist-info/METADATA", "sha256=" + encoded, metadata.stat().st_size],
                                         ["fixture-1.0.dist-info/RECORD", "", ""]])
        result = dependencies._installed(python, self.root, {})
        self.assertEqual(set(result), {"fixture"})
        self.assertEqual(result["fixture"]["version"], "1.0")
        metadata.write_text(metadata.read_text() + "Changed: true\n")
        with self.assertRaises(ConfigurationError):
            dependencies._installed(python, self.root, {})

    def test_wheel_metadata_rejects_duplicate_or_linked_members(self):
        for name in ("../escape", "a-1.dist-info/METADATA"):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as wheel:
                wheel.writestr("a-1.dist-info/METADATA", "Name: a\nVersion: 1\n")
                wheel.writestr(name, "unapproved")
            with self.assertRaises(ConfigurationError):
                dependencies.wheel_identity(raw.getvalue())
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as wheel:
            wheel.writestr("a-1.dist-info/METADATA", "Name: a\nVersion: 1\n")
            wheel.writestr("a/vendor/b-2.dist-info/METADATA", "Name: b\nVersion: 2\n")
        self.assertEqual(dependencies.wheel_identity(raw.getvalue()), {"name": "a", "version": "1"})


if __name__ == "__main__":
    unittest.main()
