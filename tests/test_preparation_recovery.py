"""Real preparer crashes and foreign destination races on supported Linux."""

from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_deployment
from zpm import deployment
from zpm.models import ConfigurationError
from zpm.store import Store


_PREPARER = r'''
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from zpm import deployment

configuration = json.loads(Path(sys.argv[1]).read_text())
destination, fault = Path(sys.argv[2]), sys.argv[3]

def pause():
    print("boundary", flush=True)
    if sys.stdin.readline() != "continue\n":
        raise SystemExit("preparation boundary was not released")

if fault == "credentials":
    original = deployment._write
    original_fdopen = deployment.os.fdopen
    @contextmanager
    def interrupted_stream(*args, **kwargs):
        with original_fdopen(*args, **kwargs) as stream:
            class PartialWrite:
                def __getattr__(self, name):
                    return getattr(stream, name)
                def write(self, content):
                    halfway = len(content) // 2
                    stream.write(content[:halfway])
                    stream.flush()
                    os.fsync(stream.fileno())
                    pause()
                    stream.write(content[halfway:])
            yield PartialWrite()
    def write(path, *args, **kwargs):
        if path.name != "environment.json":
            return original(path, *args, **kwargs)
        deployment.os.fdopen = interrupted_stream
        try:
            return original(path, *args, **kwargs)
        finally:
            deployment.os.fdopen = original_fdopen
    deployment._write = write
elif fault in {"before_publish", "after_publish"}:
    original = deployment._publish_stage
    def publish(*args):
        if fault == "before_publish":
            pause()
        original(*args)
        if fault == "after_publish":
            pause()
    deployment._publish_stage = publish
elif fault == "concurrent":
    original = deployment.fcntl.flock
    def lock(*args):
        print("locking", flush=True)
        original(*args)
    deployment.fcntl.flock = lock

print(json.dumps(deployment.prepare(configuration, destination)), flush=True)
'''


@unittest.skipUnless(sys.platform == "linux", "atomic publication supports Linux only")
class PreparationRecoveryTests(unittest.TestCase):
    setUp = test_deployment.DeploymentTests.setUp
    tearDown = test_deployment.DeploymentTests.tearDown

    @contextmanager
    def preparer(self, fault, destination=None):
        configuration = self.root / "preparation-input.json"
        configuration.write_text(json.dumps(self.config))
        process = subprocess.Popen(
            [sys.executable, "-c", _PREPARER, str(configuration), str(destination or self.bundle), fault],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def wait_line(self, process, expected):
        readable, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(readable, "preparer did not reach the requested boundary")
        self.assertEqual(process.stdout.readline().strip(), expected)

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def assert_retained(self, root, snapshot):
        self.assertEqual(snapshot, {name: (root / name).read_bytes() for name in snapshot})

    def stages(self):
        return sorted(self.root.glob(".zpm-prepare-*"))

    def kill_preparer(self, process):
        process.kill()
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, -signal.SIGKILL)

    def test_killed_preparer_retries_without_touching_abandoned_stages(self):
        for fault in ("credentials", "before_publish"):
            with self.subTest(boundary=fault):
                destination = self.root / fault
                previous = set(self.stages())
                with self.preparer(fault, destination) as process:
                    self.wait_line(process, "boundary")
                    stage, = set(self.stages()) - previous
                    original = self.snapshot(stage)
                    if fault == "credentials":
                        with self.assertRaises(json.JSONDecodeError):
                            json.loads(original["secrets/environment.json"])
                    inode = stage.stat().st_ino
                    self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
                    marker = json.loads((stage / "preparation.json").read_text())
                    self.assertEqual(marker["directory"], str(destination))
                    self.assertEqual(marker["deployment_id"], self.config["deployment_id"])
                    self.assertFalse(destination.exists())
                    self.kill_preparer(process)
                report = deployment.prepare(self.config, destination)
                self.assertFalse(report["reused"])
                self.assertTrue(deployment.verify(destination)["valid"])
                self.assertEqual(stage.stat().st_ino, inode)
                self.assertEqual(self.snapshot(stage), original)
                self.assertNotEqual((destination / "secrets/environment.json").read_bytes(),
                                    original["secrets/environment.json"])
                self.assertNotEqual(json.loads((destination / "preparation.json").read_text())["preparation_id"],
                                    marker["preparation_id"])
                self.assertFalse(report["services_started"])

    def test_kill_after_publication_reuses_published_identity_and_removed_state(self):
        with self.preparer("after_publish") as process:
            self.wait_line(process, "boundary")
            self.assertTrue(self.bundle.is_dir())
            self.assertEqual(self.stages(), [])
            retained = self.snapshot(self.bundle)
            inode = self.bundle.stat().st_ino
            self.kill_preparer(process)
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            store.change("owner/project", "remove")
            removed = store.bindings()
        retained.pop("state/zpm.sqlite3")
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("published credentials rotated")):
            repeated = deployment.prepare(self.config, self.bundle)
        self.assertTrue(repeated["reused"])
        self.assertEqual(self.bundle.stat().st_ino, inode)
        self.assert_retained(self.bundle, retained)
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual(store.bindings(), removed)
            self.assertFalse(store.bindings()[0]["enabled"])

    def test_complete_stage_uses_final_paths_but_generated_manager_cannot_start_it(self):
        with self.preparer("before_publish") as process:
            self.wait_line(process, "boundary")
            stage, = self.stages()
            retained = self.snapshot(stage)
            config = json.loads((stage / "config/openjules.json").read_text())
            for name in ("state_directory", "lifecycle_database", "credentials_file"):
                self.assertTrue(config[name].startswith(str(self.bundle) + "/"))
                self.assertNotIn(str(stage), config[name])
            with self.assertRaisesRegex(ConfigurationError, "moved deployment"):
                deployment.verify(stage)
            environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            result = subprocess.run(
                [sys.executable, str(stage / "manage.py"), "start"], cwd=stage, env=environment,
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("moved deployment", result.stderr)
            self.assertEqual(list((stage / "environments").iterdir()), [])
            self.assertEqual(self.snapshot(stage), retained)
            self.assertFalse(self.bundle.exists())

    def test_atomic_publication_never_replaces_a_foreign_destination_race(self):
        for kind in ("empty", "nonempty", "file", "symlink"):
            with self.subTest(destination=kind):
                destination = self.root / kind
                previous = set(self.stages())
                with self.preparer("before_publish", destination) as process:
                    self.wait_line(process, "boundary")
                    stage, = set(self.stages()) - previous
                    retained = self.snapshot(stage)
                    if kind == "file":
                        destination.write_text("foreign file")
                    elif kind == "symlink":
                        destination.symlink_to(self.root / "absent-foreign-target")
                    else:
                        destination.mkdir()
                        if kind == "nonempty":
                            (destination / "foreign").write_text("retained")
                    inode = destination.lstat().st_ino
                    stdout, stderr = process.communicate("continue\n", timeout=10)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn("not replaced", stderr)
                    self.assertEqual(destination.lstat().st_ino, inode)
                    self.assertEqual(self.snapshot(stage), retained)
                    self.assertEqual(stdout, "")
                    if kind == "file":
                        self.assertEqual(destination.read_text(), "foreign file")
                    elif kind == "symlink":
                        self.assertEqual(destination.readlink(), self.root / "absent-foreign-target")
                        self.assertFalse(destination.exists())
                    elif kind == "nonempty":
                        self.assertEqual(list(destination.iterdir()), [destination / "foreign"])
                        self.assertEqual((destination / "foreign").read_text(), "retained")
                    else:
                        self.assertEqual(list(destination.iterdir()), [])

    def test_concurrent_preparers_publish_once_then_reuse_the_same_bundle(self):
        with self.preparer("before_publish") as first:
            self.wait_line(first, "boundary")
            stage, = self.stages()
            retained = self.snapshot(stage)
            inode = stage.stat().st_ino
            with self.preparer("concurrent") as second:
                self.wait_line(second, "locking")
                self.assertIsNone(second.poll())
                self.assertEqual(self.stages(), [stage])
                first_output, first_error = first.communicate("continue\n", timeout=10)
                second_output, second_error = second.communicate(timeout=10)
                self.assertEqual((first.returncode, second.returncode), (0, 0), first_error + second_error)
                self.assertFalse(json.loads(first_output)["reused"])
                self.assertTrue(json.loads(second_output)["reused"])
        self.assertEqual(self.stages(), [])
        self.assertEqual(self.bundle.stat().st_ino, inode)
        self.assert_retained(self.bundle, retained)
        self.assertTrue(deployment.verify(self.bundle)["valid"])

    def test_unsupported_atomic_primitive_fails_closed_without_fallback(self):
        with patch("zpm.deployment.sys.platform", "darwin"):
            with self.assertRaisesRegex(ConfigurationError, "requires Linux"):
                deployment.prepare(self.config, self.bundle)
        self.assertEqual(self.stages(), [])
        self.assertFalse(self.bundle.exists())

        def unsupported(*args):
            ctypes.set_errno(errno.ENOTSUP)
            return -1

        with patch("zpm.deployment._atomic_rename", return_value=unsupported):
            with self.assertRaisesRegex(ConfigurationError, "unavailable on this Linux filesystem"):
                deployment.prepare(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())
        stage, = self.stages()
        with self.assertRaisesRegex(ConfigurationError, "moved deployment"):
            deployment.verify(stage)


if __name__ == "__main__":
    unittest.main()
