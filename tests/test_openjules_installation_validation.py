"""Test the qualification harness; fixture receipts are not installed evidence."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_openjules_installation_validation as trial
finally:
    sys.path.pop(0)


FIXTURES = Path(__file__).parent / "fixtures/installation-admission"
VECTORS = {value["name"]: value for value in json.loads(
    (FIXTURES / "openjules-vectors.json").read_text())["vectors"]}


def receipt(name="root"):
    native = deepcopy(VECTORS[name]["expected"])
    component = {"source_revision": trial.REVISION, "validation_contract": trial.CONTRACT,
                 "contract_digest": trial.CONTRACT_DIGEST,
                 "input_digests": {"configuration": native["configuration_sha256"],
                                  "scope_declarations": native["scope_declarations_sha256"]}}
    return native, component


class ReceiptBindingTests(unittest.TestCase):
    def test_published_valid_and_invalid_vectors_use_closed_consumer_parser(self):
        for name in ("root", "model", "children", "boolean_port"):
            with self.subTest(vector=name):
                native, component = receipt(name)
                original = deepcopy((native, component))
                parsed, descriptor, envelope = trial.observe(
                    trial.encode(native), component, 0 if native["valid"] else 2)
                self.assertEqual(parsed, native)
                self.assertEqual(descriptor, native["descriptor"])
                self.assertEqual(envelope["input_digests"], component["input_digests"])
                self.assertEqual((native, component), original)

    def test_native_exit_and_receipt_must_agree(self):
        for name, code in (("root", 2), ("root", 1), ("boolean_port", 0), ("boolean_port", -9)):
            native, component = receipt(name)
            with self.subTest(vector=name, exit=code), self.assertRaisesRegex(
                    AssertionError, "native_validation_exit_receipt_mismatch"):
                trial.observe(trial.encode(native), component, code)

    def test_changed_inputs_host_checks_and_partial_descriptors_are_refused(self):
        for kind in ("configuration", "scope_declarations", "host_checks", "unknown_field", "partial_descriptor"):
            native, component = receipt("boolean_port" if kind == "partial_descriptor" else "root")
            if kind in {"configuration", "scope_declarations"}:
                component["input_digests"][kind] = "0" * 64
            elif kind == "host_checks":
                native["host_checks_required"].pop()
            elif kind == "unknown_field":
                native["private-extra"] = "never-a-report-value"
            else:
                native["descriptor"] = receipt()[0]["descriptor"]
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    AssertionError, "native_validation_receipt_unvalidated"):
                trial.observe(trial.encode(native), component, 0 if native["valid"] else 2)

    def test_native_input_hash_must_agree_with_external_binding(self):
        native, component = receipt()
        native["configuration_sha256"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "native_validation_receipt_unvalidated"):
            trial.observe(trial.encode(native), component, 0)


@unittest.skipUnless(sys.platform == "linux", "installed qualification supports Linux")
class BoundedProcessTests(unittest.TestCase):
    def run_python(self, code, **limits):
        return trial.run_bounded([sys.executable, "-B", "-c", code], Path.cwd(), **limits)

    def test_fixed_environment_excludes_credentials_import_paths_and_proxies(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin", "PYTHONPATH": "/private-import",
                                     "OPENAI_API_KEY": "private-key", "HTTPS_PROXY": "private-proxy"}, clear=True):
            self.assertEqual(trial.environment(), {
                "PATH": "/usr/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})

    def test_bounded_output_and_exit_status_are_retained_without_stderr(self):
        code, raw = self.run_python("import sys;sys.stderr.write('private-value');sys.stdout.write('ok');sys.exit(2)", limit=2)
        self.assertEqual((code, raw), (2, b"ok"))

    def test_overflow_refused_while_process_is_still_running(self):
        start = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "native_validation_output_bound"):
            self.run_python("import os,time;os.write(1,b'x'*4097);time.sleep(30)", limit=4096)
        self.assertLess(time.monotonic() - start, 5)

    def test_timeout_is_bounded_even_if_stdout_is_closed(self):
        start = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "native_validation_process_timeout"):
            self.run_python("import os,time;os.close(1);time.sleep(30)", seconds=0.3)
        self.assertLess(time.monotonic() - start, 5)

    def test_signal_exit_is_not_a_native_refusal(self):
        with self.assertRaisesRegex(AssertionError, "native_validation_process_signal"):
            self.run_python("import os,signal;os.kill(os.getpid(),signal.SIGTERM)")

    def test_parent_exit_with_inherited_stdout_kills_owned_descendant(self):
        with TemporaryDirectory() as directory:
            pid_path = Path(directory) / "descendant.pid"
            program = ("import os,pathlib,time\n"
                       "if os.fork() == 0:\n"
                       " pathlib.Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid()))\n"
                       " time.sleep(30)\n"
                       "else:\n"
                       " os._exit(0)\n")
            with self.assertRaisesRegex(AssertionError, "native_validation_process_timeout"):
                self.run_python(program, seconds=0.5)
            pid = int(pid_path.read_text())
            # An adopted zombie is no longer executing; its reaper owns removal.
            path = Path("/proc") / str(pid) / "stat"
            deadline = time.monotonic() + 2
            while path.exists() and path.read_text().split()[2] != "Z":
                if time.monotonic() >= deadline:
                    self.fail("owned descendant remained running")
                time.sleep(0.01)


@unittest.skipUnless(sys.platform == "linux", "installed qualification supports Linux")
class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.module = self.root / "src/worker/installation_validation.py"
        self.module.parent.mkdir(parents=True)
        for directory in (self.root / "src", self.module.parent):
            (directory / "__init__.py").write_text("")
        self.module.write_text(
            "# Explicit launcher fixture, not OpenJules.\n"
            "import argparse,json,sys\nfrom pathlib import Path\n"
            "CONTRACT_PATH=Path(__file__).parent/'contracts/openjules-installation-v1.json'\n"
            "if __name__=='__main__': print(json.dumps({'arguments':sys.argv[1:]}))\n")
        artifact = self.root / trial.ARTIFACT
        artifact.parent.mkdir()
        artifact.write_bytes((FIXTURES / "openjules-installation-v1.json").read_bytes())
        (self.root / ".gitignore").write_text("__pycache__/\n*.so\nignored.py\njson.py\nargparse.py\n")
        for args in (("init", "--quiet"), ("add", "."),
                     ("-c", "user.name=Qualification fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "--quiet", "-m", "Source binding fixture")):
            self.git(*args)
        self.revision = self.git("rev-parse", "HEAD").decode().strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").decode().strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def verify(self):
        trial.verify_source(self.root, self.revision, self.tree)

    def test_clean_exact_blobs_and_static_contract_are_accepted(self):
        self.verify()

    def test_index_hidden_source_edit_is_refused(self):
        self.git("update-index", "--assume-unchanged", "src/worker/installation_validation.py")
        self.module.write_text("# Index-hidden source substitution.\n")
        self.assertEqual(self.git("status", "--porcelain"), b"")
        with self.assertRaisesRegex(AssertionError, "native_validation_source_bytes"):
            self.verify()

    def test_ignored_python_or_extension_cannot_shadow_native_imports(self):
        for name in ("ignored.py", "installation_validation.so"):
            path = self.module.parent / name
            path.write_bytes(b"untracked import candidate")
            self.assertEqual(self.git("status", "--porcelain"), b"")
            with self.subTest(name=name), self.assertRaisesRegex(
                    AssertionError, "native_validation_source_inventory"):
                self.verify()
            path.unlink()

    def test_old_bytecode_is_allowed_but_symlink_source_is_refused(self):
        cache = self.module.parent / "__pycache__"
        cache.mkdir()
        (cache / "old.pyc").write_bytes(b"ignored by fresh cache prefix")
        self.verify()
        (self.module.parent / "ignored.py").symlink_to(self.module)
        with self.assertRaisesRegex(AssertionError, "native_validation_source_entry"):
            self.verify()

    def test_wrong_head_or_tree_is_refused(self):
        for revision, tree in (("0" * 40, self.tree), (self.revision, "0" * 40)):
            with self.subTest(revision=revision, tree=tree), self.assertRaisesRegex(
                    AssertionError, "native_validation_source_identity"):
                trial.verify_source(self.root, revision, tree)

    def test_isolated_probe_and_entrypoint_ignore_root_shadow_modules(self):
        marker = self.root / "shadow-executed"
        for name in ("json.py", "argparse.py"):
            (self.root / name).write_text(
                "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n"
                "raise SystemExit(1)\n")
        self.verify()
        cache = self.root / "__pycache__/unused"
        probe_code, raw = trial.run_bounded(trial.native_command(
            sys.executable, self.root, cache, probe=True), self.root)
        self.assertEqual(probe_code, 0)
        self.assertEqual(json.loads(raw)["module"], str(self.module))
        arguments = ["--config", "private path.json", "--expected-contract", trial.CONTRACT]
        code, raw = trial.run_bounded(trial.native_command(
            sys.executable, self.root, cache, arguments), self.root)
        self.assertEqual((code, json.loads(raw)), (0, {"arguments": arguments}))
        self.assertFalse(marker.exists())
        self.assertFalse(cache.exists())


class ProtectedStateTests(unittest.TestCase):
    def test_snapshot_detects_new_state_changed_secrets_and_directory_mode(self):
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            for name in ("config", "secrets", "state", "environments/openjules"):
                (bundle / name).mkdir(parents=True)
            for name in ("manifest.json", "manage.py", "config/openjules.json", "secrets/fixture.json",
                         "environments/openjules/bootstrap-complete.json",
                         "environments/openjules/openjules-managed-installation.json"):
                trial.private_file(bundle / name, b"{}\n")
            before = trial.protected_snapshot(bundle)
            secret = bundle / "secrets/fixture.json"
            secret.write_bytes(b'{"changed":true}\n')
            self.assertNotEqual(before, trial.protected_snapshot(bundle))
            secret.write_bytes(b"{}\n")
            trial.private_file(bundle / "state/runtime-journal", b"created")
            self.assertNotEqual(before, trial.protected_snapshot(bundle))
            (bundle / "state/runtime-journal").unlink()
            (bundle / "state").chmod(0o700 if (bundle / "state").stat().st_mode & 0o777 != 0o700 else 0o750)
            self.assertNotEqual(before, trial.protected_snapshot(bundle))


if __name__ == "__main__":
    unittest.main()
