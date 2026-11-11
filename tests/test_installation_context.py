"""Installer context tests use real files/processes and a named protocol fixture.

The tiny companion fixture is not OpenJules execution evidence. The unchanged
installed CI runs the actual pinned OpenJules helper and legacy CLI.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch
import venv

from tests import test_deployment
from zpm import deployment, installation_context as context
from zpm.models import ConfigurationError
from zpm.store import Store


PROBE_FIXTURE = r'''
import hashlib,json,os,sys
from pathlib import Path
assert sys.argv[1:] == ["--probe-managed-installation"]
raw = (Path(sys.prefix) / "openjules-managed-installation.json").read_bytes()
record = {"prefix":sys.prefix,"executable":sys.executable,"cwd":str(Path.cwd()),
          "argv":sys.argv[1:],"environment":dict(os.environ)}
path = Path(sys.prefix) / "fixture-probe.json"
path.write_text(json.dumps(record))
path.chmod(0o600)
report = {"schema_version":1,"selector_sha256":hashlib.sha256(raw).hexdigest(),
          "legacy_execution_allowed":False}
print(json.dumps(report))
'''

_PUBLISHER = r'''
from contextlib import contextmanager
from pathlib import Path
import os,sys
from zpm import installation_context as context
from zpm.models import ConfigurationError
root, fault = Path(sys.argv[1]), sys.argv[2]
def pause():
    print("boundary", flush=True)
    if sys.stdin.readline() != "continue\n":
        raise RuntimeError("boundary was not released")
if fault == "during_write":
    original_fdopen = context.os.fdopen
    @contextmanager
    def interrupted(fd, mode, *args, **kwargs):
        with original_fdopen(fd, mode, *args, **kwargs) as stream:
            if "w" not in mode:
                yield stream
                return
            class Partial:
                def __getattr__(self, name):
                    return getattr(stream, name)
                def write(self, value):
                    halfway = len(value) // 2
                    stream.write(value[:halfway])
                    stream.flush()
                    os.fsync(stream.fileno())
                    pause()
                    return stream.write(value[halfway:])
            yield Partial()
    context.os.fdopen = interrupted
else:
    original_link = context.os.link
    def link(*args, **kwargs):
        if fault == "before_publish":
            pause()
        result = original_link(*args, **kwargs)
        if fault == "after_publish":
            pause()
        return result
    context.os.link = link
try:
    context.publish_selector(root)
except ConfigurationError:
    print("rejected", flush=True)
    raise SystemExit(2)
print("published", flush=True)
'''


@unittest.skipUnless(sys.platform == "linux", "managed installation supports Linux")
class InstallationContextTests(unittest.TestCase):
    tearDown = test_deployment.DeploymentTests.tearDown
    read = test_deployment.DeploymentTests.read

    def setUp(self):
        test_deployment.DeploymentTests.setUp(self)
        self.checkout = Path(self.sources["openjules"]["checkout"])
        self.helper = self.checkout / "src/worker/execution_context.py"
        self.helper.write_text(PROBE_FIXTURE)
        (self.checkout / ".gitignore").write_text("__pycache__/\n")
        subprocess.run(["git", "-C", str(self.checkout), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.checkout), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "probe fixture"],
                       check=True, capture_output=True)
        revision = subprocess.check_output(["git", "-C", str(self.checkout), "rev-parse", "HEAD"], text=True).strip()
        self.config["sources"]["openjules"]["revision"] = revision
        self.installs = []

    def prepare(self):
        deployment.prepare(self.config, self.bundle)
        self.data = self.read("manifest.json")["configuration"]

    def create_environment(self):
        venv.EnvBuilder(with_pip=False).create(self.bundle / "environments/openjules")

    def complete(self):
        context.publish_selector(self.bundle)
        marker = {"revision": self.data["sources"]["openjules"]["revision"], "python": sys.version}
        context.publish_private(self.bundle / "environments/openjules/bootstrap-complete.json",
                                (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode())

    def fake_installer(self, command, **kwargs):
        self.installs.append(command)
        if command[1:3] == ["-m", "venv"]:
            venv.EnvBuilder(with_pip=False).create(command[-1])
            return subprocess.CompletedProcess(command, 0)
        if command[1:4] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(command, 0)
        if command[1:] == ["-m", "pip", "freeze"]:
            return subprocess.CompletedProcess(command, 0, "fixture-package==1\n", "")
        raise AssertionError("unexpected dependency fixture command")

    def bootstrap(self):
        with patch("zpm.deployment.subprocess.run", side_effect=self.fake_installer):
            return deployment._bootstrap(self.bundle, self.data)

    def probe(self):
        return context.probe(self.bundle, self.checkout,
                             deployment._environment(self.bundle, "bootstrap", self.data))

    def private_snapshot(self):
        return {str(path.relative_to(self.bundle)): path.read_bytes()
                for folder in ("config", "secrets") for path in (self.bundle / folder).iterdir()}

    @contextmanager
    def publisher(self, fault):
        process = subprocess.Popen(
            [sys.executable, "-c", _PUBLISHER, str(self.bundle), fault],
            cwd=Path(__file__).resolve().parents[1],
            env=deployment._environment(self.bundle, "bootstrap", self.data),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def boundary(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "publisher did not reach its boundary")
        self.assertEqual(process.stdout.readline().strip(), "boundary")

    def test_unbootstrapped_verification_remains_offline_and_does_not_create_context(self):
        self.prepare()
        with patch.object(context, "probe", side_effect=AssertionError("offline verify ran installed probe")):
            self.assertTrue(deployment.verify(self.bundle)["valid"])
        self.assertFalse(context.selector_path(self.bundle).exists())
        self.assertFalse(context.completed(self.bundle, self.data["sources"]["openjules"]["revision"]))

    def test_bootstrap_probes_the_installed_interpreter_before_marking_complete(self):
        self.prepare()
        original = context.probe
        observations = []
        def observed(*args, **kwargs):
            observations.append(context.completed(self.bundle, self.data["sources"]["openjules"]["revision"]))
            return original(*args, **kwargs)
        with patch.dict(os.environ, {"UNRELATED_SECRET": "must-not-cross", "PYTHONPATH": "/not-approved",
                                     "POLYPHONY_RUNTIME_TOKEN": "must-not-cross",
                                     "ZPM_MODEL_PROVIDER_TOKEN": "must-not-cross"}), \
                patch.object(context, "probe", side_effect=observed):
            self.assertTrue(self.bootstrap()["installed"])
        self.assertEqual(observations, [False])
        self.assertTrue(context.completed(self.bundle, self.data["sources"]["openjules"]["revision"]))
        raw = context.selector_path(self.bundle).read_bytes()
        selector = json.loads(raw)
        self.assertEqual(set(selector), {"schema_version", "managed_config_path", "managed_config_sha256"})
        self.assertEqual(selector["managed_config_path"], str(self.bundle / "config/openjules.json"))
        self.assertEqual(selector["managed_config_sha256"],
                         hashlib.sha256((self.bundle / "config/openjules.json").read_bytes()).hexdigest())
        self.assertEqual(context.selector_path(self.bundle).stat().st_mode & 0o777, 0o600)
        observed = self.read("environments/openjules/fixture-probe.json")
        self.assertEqual(observed["prefix"], str(self.bundle / "environments/openjules"))
        self.assertEqual(observed["executable"], str(self.bundle / "environments/openjules/bin/python"))
        self.assertEqual(observed["cwd"], str(self.checkout))
        self.assertEqual(observed["argv"], ["--probe-managed-installation"])
        self.assertTrue({"UNRELATED_SECRET", "PYTHONPATH", "POLYPHONY_RUNTIME_TOKEN",
                         "ZPM_MODEL_PROVIDER_TOKEN"}.isdisjoint(observed["environment"]))

    def test_repeated_bootstrap_keeps_context_credentials_and_pause_or_removed_state(self):
        self.prepare()
        self.bootstrap()
        selector = context.selector_path(self.bundle)
        original = (selector.read_bytes(), selector.stat().st_ino, selector.stat().st_mtime_ns)
        completions = {name: (self.bundle / "environments" / name / "bootstrap-complete.json").read_bytes()
                       for name in deployment.SOURCE_FILES}
        sealed = self.private_snapshot()
        for action in ("pause", "remove"):
            with Store(self.bundle / "state/zpm.sqlite3") as store:
                store.change("owner/project", action)
                expected = store.bindings()
            with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("reinstalled completed environment")):
                deployment._bootstrap(self.bundle, self.data)
            self.assertEqual(original, (selector.read_bytes(), selector.stat().st_ino, selector.stat().st_mtime_ns))
            self.assertEqual(completions, {name: (self.bundle / "environments" / name / "bootstrap-complete.json").read_bytes()
                                          for name in completions})
            self.assertEqual(sealed, self.private_snapshot())
            with Store(self.bundle / "state/zpm.sqlite3") as store:
                self.assertEqual(expected, store.bindings())

    def test_complete_environment_never_repairs_missing_foreign_or_unsafe_selector(self):
        self.prepare()
        self.bootstrap()
        path = context.selector_path(self.bundle)
        expected = path.read_bytes()
        external = self.root / "external-selector"
        external.write_bytes(expected)
        external.chmod(0o600)
        for fault in ("missing", "changed", "version", "duplicate", "public", "symlink", "fifo", "directory"):
            with self.subTest(fault=fault):
                path.unlink()
                if fault == "changed":
                    path.write_bytes(b"foreign-selector\n")
                    path.chmod(0o600)
                elif fault in {"version", "duplicate"}:
                    value = expected.replace(b'"schema_version":1', b'"schema_version":true' if fault == "version"
                                             else b'"schema_version":1,"schema_version":1')
                    path.write_bytes(value)
                    path.chmod(0o600)
                elif fault == "public":
                    path.write_bytes(expected)
                    path.chmod(0o644)
                elif fault == "symlink":
                    path.symlink_to(external)
                elif fault == "fifo":
                    os.mkfifo(path, 0o600)
                elif fault == "directory":
                    path.mkdir(mode=0o700)
                def identity():
                    if not os.path.lexists(path):
                        return None
                    info = os.lstat(path)
                    return (info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                before = identity()
                with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("mutated dependencies")), \
                        self.assertRaises(ConfigurationError):
                    deployment._bootstrap(self.bundle, self.data)
                after = identity()
                self.assertEqual(before, after)
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                elif os.path.lexists(path):
                    path.unlink()
                path.write_bytes(expected)
                path.chmod(0o600)
        self.assertEqual(external.read_bytes(), expected)

    def test_incomplete_environment_rejects_foreign_selector_before_dependency_mutation(self):
        self.prepare()
        self.create_environment()
        path = context.selector_path(self.bundle)
        path.write_bytes(b"not-owned\n")
        path.chmod(0o600)
        with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("mutated dependencies")), \
                self.assertRaises(ConfigurationError):
            deployment._bootstrap(self.bundle, self.data)
        self.assertEqual(path.read_bytes(), b"not-owned\n")
        self.assertFalse((path.parent / "bootstrap-complete.json").exists())
        path.unlink()
        external = self.root / "foreign-environment"
        path.parent.rename(external)
        path.parent.symlink_to(external, target_is_directory=True)
        with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("followed a foreign environment")), \
                self.assertRaises(ConfigurationError):
            deployment._bootstrap(self.bundle, self.data)
        self.assertTrue(path.parent.is_symlink())
        self.assertFalse((external / context.SELECTOR_NAME).exists())

    def test_unreadable_presence_is_never_treated_as_absence_or_repaired(self):
        self.prepare()
        self.bootstrap()
        selector = context.selector_path(self.bundle)
        completion = selector.parent / "bootstrap-complete.json"
        retained = {path: path.read_bytes() for path in (selector, completion)}
        actual_lstat, actual_stat = os.lstat, os.stat

        @contextmanager
        def deny_inspection(inaccessible):
            def selected(path):
                return path == inaccessible or path == str(inaccessible)
            def denied_lstat(path, *args, **kwargs):
                if selected(path):
                    raise PermissionError("private-fixture-path-must-not-escape")
                return actual_lstat(path, *args, **kwargs)
            def denied_stat(path, *args, **kwargs):
                if selected(path) and kwargs.get("follow_symlinks") is False:
                    raise PermissionError("private-fixture-path-must-not-escape")
                return actual_stat(path, *args, **kwargs)
            # CPython lexists calls os.lstat, while pathlib.lstat delegates to
            # os.stat(follow_symlinks=False) on both supported Python versions.
            with patch("os.lstat", side_effect=denied_lstat), \
                    patch("os.stat", side_effect=denied_stat):
                self.assertFalse(os.path.lexists(inaccessible))
                with self.assertRaises(PermissionError):
                    inaccessible.lstat()
                yield

        for inaccessible in (selector, completion):
            with self.subTest(path=inaccessible.name):
                with deny_inspection(inaccessible), \
                        patch("zpm.deployment.subprocess.run", side_effect=AssertionError("mutated dependencies")), \
                        self.assertRaises(ConfigurationError) as error:
                    deployment._bootstrap(self.bundle, self.data)
                self.assertNotIn("private-fixture-path-must-not-escape", str(error.exception))
                self.assertEqual(retained, {path: path.read_bytes() for path in retained})
        # The missing completion exposes the optional-selector branch; the
        # writer must also refuse before creating even a private temporary file.
        completion.unlink()
        for operation in (
            lambda: deployment._bootstrap(self.bundle, self.data),
            lambda: context.publish_selector(self.bundle),
        ):
            with deny_inspection(selector), \
                    patch("zpm.deployment.subprocess.run", side_effect=AssertionError("mutated dependencies")), \
                    patch.object(context.tempfile, "mkstemp", side_effect=AssertionError("began an unknown-destination write")), \
                    self.assertRaises(ConfigurationError):
                operation()
            self.assertEqual(selector.read_bytes(), retained[selector])
            self.assertFalse(completion.exists())

    def test_completed_marker_is_bound_to_original_revision_and_never_adopted(self):
        self.prepare()
        self.bootstrap()
        marker = self.bundle / "environments/openjules/bootstrap-complete.json"
        original = marker.read_bytes()
        for raw in (b"", b"{}", json.dumps({"revision": "f" * 40, "python": sys.version}).encode(),
                    b'{"revision":"x","revision":"y","python":"fixture"}'):
            with self.subTest(raw=raw):
                marker.write_bytes(raw)
                with patch("zpm.deployment.subprocess.run", side_effect=AssertionError("repaired completed environment")), \
                        self.assertRaises(ConfigurationError):
                    deployment._bootstrap(self.bundle, self.data)
                self.assertEqual(marker.read_bytes(), raw)
        marker.write_bytes(original)

    def test_an_old_companion_without_the_helper_cannot_complete_bootstrap(self):
        self.helper.unlink()
        subprocess.run(["git", "-C", str(self.checkout), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.checkout), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "old companion fixture"],
                       check=True, capture_output=True)
        self.config["sources"]["openjules"]["revision"] = subprocess.check_output(
            ["git", "-C", str(self.checkout), "rev-parse", "HEAD"], text=True).strip()
        self.prepare()
        sealed = self.private_snapshot()
        with self.assertRaisesRegex(ConfigurationError, "does not confirm"):
            self.bootstrap()
        self.assertTrue(context.selector_path(self.bundle).is_file())
        self.assertFalse((context.selector_path(self.bundle).parent / "bootstrap-complete.json").exists())
        self.assertEqual(sealed, self.private_snapshot())
        # An old runtime can also have a completion file. Even with a matching
        # local marker, its absent helper cannot make start or doctor ready.
        self.complete()
        for operation in (deployment._start, deployment._doctor):
            with self.subTest(operation=operation.__name__), \
                    patch("subprocess.run", side_effect=AssertionError("contacted a provider")), \
                    self.assertRaisesRegex(ConfigurationError, "does not confirm"):
                operation(self.bundle, self.data)

    def test_probe_failure_retains_new_binding_for_exact_retry_without_completion(self):
        self.prepare()
        sealed = self.private_snapshot()
        with patch.object(context, "probe", side_effect=ConfigurationError("injected local probe failure")):
            with self.assertRaisesRegex(ConfigurationError, "injected local"):
                self.bootstrap()
        raw = context.selector_path(self.bundle).read_bytes()
        self.assertFalse(context.completed(self.bundle, self.data["sources"]["openjules"]["revision"]))
        self.assertTrue(self.bootstrap()["installed"])
        self.assertEqual(context.selector_path(self.bundle).read_bytes(), raw)
        self.assertEqual(self.private_snapshot(), sealed)

    def test_start_and_doctor_refuse_before_any_service_or_provider_effect(self):
        self.prepare()
        for operation in (deployment._start, deployment._doctor):
            with self.subTest(operation=operation.__name__), \
                    patch("subprocess.Popen", side_effect=AssertionError("started a service or probe")), \
                    patch("subprocess.run", side_effect=AssertionError("contacted a provider")), \
                    self.assertRaisesRegex(ConfigurationError, "bootstrap is incomplete"):
                operation(self.bundle, self.data)
        self.create_environment()
        self.complete()
        context.selector_path(self.bundle).unlink()
        for operation in (deployment._start, deployment._doctor):
            with self.subTest(operation=operation.__name__), \
                    patch("subprocess.Popen", side_effect=AssertionError("started a service or probe")), \
                    patch("subprocess.run", side_effect=AssertionError("contacted a provider")), \
                    self.assertRaises(ConfigurationError):
                operation(self.bundle, self.data)

    def test_doctor_runs_the_real_local_probe_before_its_provider_check(self):
        self.prepare()
        self.create_environment()
        self.complete()
        report = {"deployment_id": self.data["deployment_id"], "repositories": [{"validation": {"ready": True}}]}
        def provider(command, **kwargs):
            self.assertTrue((self.bundle / "environments/openjules/fixture-probe.json").is_file())
            self.assertIn("check", command)
            return subprocess.CompletedProcess(command, 0, json.dumps(report), "")
        with patch("zpm.deployment.subprocess.run", side_effect=provider):
            self.assertTrue(deployment._doctor(self.bundle, self.data)["ready"])

    def test_probe_rejects_malformed_inexact_or_unsupported_contract(self):
        self.prepare()
        self.create_environment()
        context.publish_selector(self.bundle)
        self.probe()
        variants = [
            'print(json.dumps(report | {"schema_version": True}))',
            'print(json.dumps(report | {"schema_version": 1.0}))',
            'print(json.dumps(report | {"legacy_execution_allowed": 0}))',
            'print(json.dumps(report | {"legacy_execution_allowed": True}))',
            'print(json.dumps(report | {"selector_sha256": "a" * 64}))',
            'print(json.dumps(report | {"unexpected": False}))',
            'print(json.dumps(report).replace(\'"schema_version": 1\', \'"schema_version": 1, "schema_version": 1\'))',
            'print(json.dumps([report]))',
            'print("not-json")',
        ]
        for output in variants:
            with self.subTest(output=output):
                self.helper.write_text(PROBE_FIXTURE.replace("print(json.dumps(report))", output))
                for cached in self.helper.parent.glob("__pycache__/execution_context.*.pyc"):
                    cached.unlink()
                with self.assertRaisesRegex(ConfigurationError, "does not confirm"):
                    self.probe()

    def test_probe_bounds_output_and_time_reaps_owned_process_and_redacts_errors(self):
        self.prepare()
        self.create_environment()
        context.publish_selector(self.bundle)
        actual_popen = subprocess.Popen
        for program in (
            PROBE_FIXTURE + '\nprint("private-fixture-secret", file=sys.stderr)\nraise SystemExit(7)\n',
            PROBE_FIXTURE.replace("print(json.dumps(report))", 'print(json.dumps(report) + " " * 4096)'),
            'import time; time.sleep(30)',
        ):
            with self.subTest(program=program):
                self.helper.write_text(program)
                children = []
                def start(*args, **kwargs):
                    process = actual_popen(*args, **kwargs)
                    children.append(process)
                    return process
                with patch.object(context, "PROBE_SECONDS", 0.2), \
                        patch("subprocess.Popen", side_effect=start), \
                        self.assertRaises(ConfigurationError) as error:
                    self.probe()
                self.assertNotIn("private-fixture-secret", str(error.exception))
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdout.closed)


    def test_exited_probe_leader_cannot_leave_an_owned_stdout_holder_running(self):
        self.prepare()
        self.create_environment()
        context.publish_selector(self.bundle)
        child_pid = self.root / "owned-descendant.pid"
        self.helper.write_text(
            "import os,time\nfrom pathlib import Path\n"
            "child=os.fork()\n"
            "if child == 0:\n"
            f"    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            "else:\n"
            "    os._exit(0)\n"
        )
        # The separate monitor owns adopted descendants, so this test proves
        # reaping as well as termination without changing the test runner.
        monitor = r'''
import ctypes,json,os,signal,sys,time
from pathlib import Path
from zpm import deployment,installation_context as context
from zpm.models import ConfigurationError
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(36,1,0,0,0) != 0:
    raise RuntimeError("cannot establish test descendant ownership")
root,pid_file=Path(sys.argv[1]),Path(sys.argv[2])
data=json.loads((root/"manifest.json").read_text())["configuration"]
context.PROBE_SECONDS=1
child=None
try:
    try:
        context.probe(root,Path(data["sources"]["openjules"]["checkout"]),
                      deployment._environment(root,"bootstrap",data))
    except ConfigurationError:
        pass
    else:
        raise AssertionError("a live stdout holder passed the probe")
    child=int(pid_file.read_text())
    deadline=time.monotonic()+2
    status=None
    while time.monotonic()<deadline:
        waited,value=os.waitpid(child,os.WNOHANG)
        if waited:
            status=value
            child=None
            break
        time.sleep(0.01)
    if status is None or not os.WIFSIGNALED(status) or os.WTERMSIG(status)!=signal.SIGKILL:
        raise AssertionError("owned descendant survived probe cleanup")
    print("owned-descendant-killed-and-reaped")
finally:
    if child is not None:
        try:
            os.kill(child,signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(child,0)
'''
        result = subprocess.run(
            [sys.executable, "-c", monitor, str(self.bundle), str(child_pid)],
            cwd=Path(__file__).resolve().parents[1],
            env=deployment._environment(self.bundle, "bootstrap", self.data),
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "owned-descendant-killed-and-reaped")

    def test_changed_sealed_configuration_cannot_receive_a_new_selector(self):
        self.prepare()
        self.create_environment()
        config = self.bundle / "config/openjules.json"
        config.write_text("{}\n")
        with self.assertRaises(ConfigurationError):
            context.publish_selector(self.bundle)
        self.assertFalse(context.selector_path(self.bundle).exists())
        self.assertEqual(config.read_text(), "{}\n")

    def test_selector_derivation_refuses_a_nonregular_or_unsealed_manifest(self):
        self.prepare()
        self.create_environment()
        path = self.bundle / "manifest.json"
        original = path.read_bytes()
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(ConfigurationError):
            context.publish_selector(self.bundle)
        self.assertFalse(context.selector_path(self.bundle).exists())
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o600)
        malformed = json.loads(original)
        malformed["files"]["config/openjules.json"] = "f" * 64
        path.write_text(json.dumps(malformed))
        with self.assertRaises(ConfigurationError):
            context.publish_selector(self.bundle)
        self.assertFalse(context.selector_path(self.bundle).exists())

    def test_real_crash_before_and_after_publication_recovers_only_the_exact_binding(self):
        for fault in ("during_write", "before_publish", "after_publish"):
            with self.subTest(fault=fault):
                self.bundle = self.root / ("installation-" + fault)
                self.prepare()
                self.create_environment()
                sealed = self.private_snapshot()
                with self.publisher(fault) as process:
                    self.boundary(process)
                    abandoned = {path: path.read_bytes() for path in
                                 (self.bundle / "environments/openjules").glob(".managed-context-*")}
                    self.assertEqual(len(abandoned), 1)
                    process.kill()
                    process.communicate(timeout=5)
                    self.assertEqual(process.returncode, -signal.SIGKILL)
                if fault != "after_publish":
                    self.assertFalse(context.selector_path(self.bundle).exists())
                else:
                    self.assertEqual(context.selector_path(self.bundle).read_bytes(),
                                     context.expected_selector(self.bundle))
                self.assertTrue(self.bootstrap()["installed"])
                self.assertEqual(sealed, self.private_snapshot())
                self.assertEqual(abandoned, {path: path.read_bytes() for path in abandoned})
                self.assertEqual(context.selector_path(self.bundle).read_bytes(),
                                 context.expected_selector(self.bundle))

    def test_real_destination_race_preserves_foreign_file_and_refuses_publication(self):
        self.prepare()
        self.create_environment()
        with self.publisher("before_publish") as process:
            self.boundary(process)
            path = context.selector_path(self.bundle)
            path.write_bytes(b"concurrent-foreign-context\n")
            path.chmod(0o600)
            process.stdin.write("continue\n")
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 2)
            self.assertEqual(path.read_bytes(), b"concurrent-foreign-context\n")
            self.assertFalse((path.parent / "bootstrap-complete.json").exists())


if __name__ == "__main__":
    unittest.main()
