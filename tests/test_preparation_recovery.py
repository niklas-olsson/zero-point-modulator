"""Real preparer crashes and foreign destination races on supported Linux."""

from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import select
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_deployment
from zpm import deployment
from zpm.models import ConfigurationError
from zpm.store import Store


_REAL_CDLL = ctypes.CDLL


def without_renameat2(*args, **kwargs):
    """Mask only the wrapper; syscall remains the real host libc entry point."""
    library = _REAL_CDLL(*args, **kwargs)

    class LibraryWithoutWrapper:
        def __getattr__(self, name):
            if name == "renameat2":
                raise AttributeError(name)
            return getattr(library, name)

    return LibraryWithoutWrapper()


_SYSCALL_ABI = (sys.platform == "linux" and platform.machine() == "x86_64"
                and ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(ctypes.c_long) == 8)


_PREPARER = r'''
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from zpm import deployment

configuration = json.loads(Path(sys.argv[1]).read_text())
destination, fault = Path(sys.argv[2]), sys.argv[3]
if sys.argv[4] == "syscall":
    from tests.test_preparation_recovery import without_renameat2
    deployment.ctypes.CDLL = without_renameat2

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


_PUBLISHER = r'''
import json
import os
import sys
from zpm import deployment
from zpm.models import ConfigurationError

if sys.argv[3] == "syscall":
    from tests.test_preparation_recovery import without_renameat2
    deployment.ctypes.CDLL = without_renameat2
parent = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    print("ready", flush=True)
    if sys.stdin.readline() != "publish\n":
        raise SystemExit("publication was not released")
    try:
        deployment._publish_stage(parent, sys.argv[2], "contended-destination")
    except ConfigurationError as error:
        print(json.dumps({"stage": sys.argv[2], "outcome": "refused", "reason": str(error)}), flush=True)
    else:
        print(json.dumps({"stage": sys.argv[2], "outcome": "published"}), flush=True)
finally:
    os.close(parent)
'''


@unittest.skipUnless(sys.platform == "linux", "atomic publication supports Linux only")
class PreparationRecoveryTests(unittest.TestCase):
    setUp = test_deployment.DeploymentTests.setUp
    tearDown = test_deployment.DeploymentTests.tearDown
    force_syscall = False

    @contextmanager
    def preparer(self, fault, destination=None):
        configuration = self.root / "preparation-input.json"
        configuration.write_text(json.dumps(self.config))
        process = subprocess.Popen(
            [sys.executable, "-c", _PREPARER, str(configuration), str(destination or self.bundle), fault,
             "syscall" if self.force_syscall else "native"],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    @contextmanager
    def publisher(self, stage):
        process = subprocess.Popen(
            [sys.executable, "-c", _PUBLISHER, str(self.root), stage,
             "syscall" if self.force_syscall else "native"],
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

    def test_unlocked_publication_race_has_one_winner_and_retains_the_loser(self):
        stages = {}
        for name in ("publisher-a", "publisher-b"):
            stage = self.root / name
            stage.mkdir(mode=0o700)
            (stage / "marker").write_text(name)
            stages[name] = (stage.stat().st_ino, self.snapshot(stage))
        with self.publisher("publisher-a") as first, self.publisher("publisher-b") as second:
            self.wait_line(first, "ready")
            self.wait_line(second, "ready")
            for process in (first, second):
                process.stdin.write("publish\n")
                process.stdin.flush()
            results = []
            for process in (first, second):
                output, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, errors)
                results.append(json.loads(output))
        winner, = [row for row in results if row["outcome"] == "published"]
        loser, = [row for row in results if row["outcome"] == "refused"]
        self.assertIn("not replaced", loser["reason"])
        destination = self.root / "contended-destination"
        self.assertEqual(destination.stat().st_ino, stages[winner["stage"]][0])
        self.assertEqual(self.snapshot(destination), stages[winner["stage"]][1])
        self.assertFalse((self.root / winner["stage"]).exists())
        retained = self.root / loser["stage"]
        self.assertEqual(retained.stat().st_ino, stages[loser["stage"]][0])
        self.assertEqual(self.snapshot(retained), stages[loser["stage"]][1])

    def test_stage_directory_fsync_failure_retains_unpublished_stage(self):
        original = deployment._fsync_directory

        def fail_complete_stage(path):
            if path.name.startswith(".zpm-prepare-") and (path / "manifest.json").is_file():
                raise OSError(errno.EIO, "injected stage directory fsync failure")
            return original(path)

        with patch("zpm.deployment._fsync_directory", side_effect=fail_complete_stage):
            with self.assertRaisesRegex(ConfigurationError, "durable publication failed"):
                deployment.prepare(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())
        stage, = self.stages()
        retained = self.snapshot(stage)
        inode = stage.stat().st_ino
        repeated = deployment.prepare(self.config, self.bundle)
        self.assertFalse(repeated["reused"])
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        self.assertEqual(stage.stat().st_ino, inode)
        self.assertEqual(self.snapshot(stage), retained)

    def test_parent_fsync_failure_after_publication_reuses_the_published_identity(self):
        original = os.fsync
        parent = self.root.stat()

        def fail_published_parent(descriptor):
            info = os.fstat(descriptor)
            if self.bundle.exists() and (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                raise OSError(errno.EIO, "injected post-publication directory fsync failure")
            return original(descriptor)

        with patch("zpm.deployment.os.fsync", side_effect=fail_published_parent):
            with self.assertRaisesRegex(ConfigurationError, "durable publication failed"):
                deployment.prepare(self.config, self.bundle)
        self.assertEqual(self.stages(), [])
        retained = self.snapshot(self.bundle)
        inode = self.bundle.stat().st_ino
        self.assertTrue(deployment.verify(self.bundle)["valid"])
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("published credentials rotated")):
            repeated = deployment.prepare(self.config, self.bundle)
        self.assertTrue(repeated["reused"])
        self.assertEqual(self.bundle.stat().st_ino, inode)
        # Read-only SQLite verification may create auxiliary WAL/SHM files.
        self.assert_retained(self.bundle, retained)

    def test_unsupported_atomic_primitive_fails_closed_without_fallback(self):
        with patch("zpm.deployment.sys.platform", "darwin"):
            with self.assertRaisesRegex(ConfigurationError, "requires Linux"):
                deployment.prepare(self.config, self.bundle)
        self.assertEqual(self.stages(), [])
        self.assertFalse(self.bundle.exists())

        with patch("zpm.deployment.ctypes.CDLL", side_effect=without_renameat2), \
                patch("zpm.deployment.platform.machine", return_value="aarch64"):
            with self.assertRaisesRegex(ConfigurationError, "requires Linux x86_64 LP64"):
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


@unittest.skipUnless(_SYSCALL_ABI, "real syscall fallback requires Linux x86_64 LP64")
class SyscallPreparationRecoveryTests(PreparationRecoveryTests):
    """Run the real process, race and fsync cases through libc.syscall on CI."""
    force_syscall = True

    def setUp(self):
        super().setUp()
        mask = patch("zpm.deployment.ctypes.CDLL", side_effect=without_renameat2)
        mask.start()
        self.addCleanup(mask.stop)

    # Keep each case explicit for the pinned-source, non-executing test inventory
    # used by the confined full-suite verifier. Reuse the base class assertions.
    def test_killed_preparer_retries_without_touching_abandoned_stages(self):
        super().test_killed_preparer_retries_without_touching_abandoned_stages()

    def test_kill_after_publication_reuses_published_identity_and_removed_state(self):
        super().test_kill_after_publication_reuses_published_identity_and_removed_state()

    def test_complete_stage_uses_final_paths_but_generated_manager_cannot_start_it(self):
        super().test_complete_stage_uses_final_paths_but_generated_manager_cannot_start_it()

    def test_atomic_publication_never_replaces_a_foreign_destination_race(self):
        super().test_atomic_publication_never_replaces_a_foreign_destination_race()

    def test_concurrent_preparers_publish_once_then_reuse_the_same_bundle(self):
        super().test_concurrent_preparers_publish_once_then_reuse_the_same_bundle()

    def test_unlocked_publication_race_has_one_winner_and_retains_the_loser(self):
        super().test_unlocked_publication_race_has_one_winner_and_retains_the_loser()

    def test_stage_directory_fsync_failure_retains_unpublished_stage(self):
        super().test_stage_directory_fsync_failure_retains_unpublished_stage()

    def test_parent_fsync_failure_after_publication_reuses_the_published_identity(self):
        super().test_parent_fsync_failure_after_publication_reuses_the_published_identity()

    def test_unsupported_atomic_primitive_fails_closed_without_fallback(self):
        super().test_unsupported_atomic_primitive_fails_closed_without_fallback()


class AtomicRenameBindingTests(unittest.TestCase):
    def test_wrapper_is_preferred_without_inspecting_the_syscall_abi(self):
        wrapper = Mock(return_value=0)
        library = SimpleNamespace(renameat2=wrapper)
        with patch("zpm.deployment.sys.platform", "linux"), \
                patch("zpm.deployment.ctypes.CDLL", return_value=library), \
                patch("zpm.deployment.platform.machine", side_effect=AssertionError("wrapper needs no syscall mapping")):
            function = deployment._atomic_rename()
            self.assertIs(function, wrapper)
            self.assertEqual(function(7, b"stage", 7, b"destination", 1), 0)
        self.assertEqual(wrapper.argtypes, (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint))
        self.assertIs(wrapper.restype, ctypes.c_int)
        wrapper.assert_called_once_with(7, b"stage", 7, b"destination", 1)

    def test_missing_wrapper_binds_exact_x86_64_lp64_syscall_arguments(self):
        syscall = Mock(return_value=0)
        with patch("zpm.deployment.sys.platform", "linux"), \
                patch("zpm.deployment.platform.machine", return_value="x86_64"), \
                patch("zpm.deployment.ctypes.sizeof", return_value=8), \
                patch("zpm.deployment.ctypes.CDLL", return_value=SimpleNamespace(syscall=syscall)) as loader:
            function = deployment._atomic_rename()
            self.assertEqual(function(7, b"stage", 9, b"destination", 1), 0)
        loader.assert_called_once_with(None, use_errno=True)
        self.assertEqual(syscall.argtypes, (ctypes.c_long, ctypes.c_long, ctypes.c_char_p,
                                           ctypes.c_long, ctypes.c_char_p, ctypes.c_ulong))
        self.assertIs(syscall.restype, ctypes.c_long)
        syscall.assert_called_once_with(316, 7, b"stage", 9, b"destination", 1)

    def test_unsupported_machine_or_widths_refuse_without_a_syscall(self):
        for machine, pointer_bytes, long_bytes in (("aarch64", 8, 8), ("i686", 4, 4),
                                                   ("x86_64", 4, 4), ("x86_64", 4, 8), ("x86_64", 8, 4)):
            with self.subTest(machine=machine, pointer_bytes=pointer_bytes, long_bytes=long_bytes):
                syscall = Mock(side_effect=AssertionError("unsupported ABI executed"))
                sizes = {ctypes.c_void_p: pointer_bytes, ctypes.c_long: long_bytes}
                with patch("zpm.deployment.sys.platform", "linux"), \
                        patch("zpm.deployment.platform.machine", return_value=machine), \
                        patch("zpm.deployment.ctypes.sizeof", side_effect=sizes.__getitem__), \
                        patch("zpm.deployment.ctypes.CDLL", return_value=SimpleNamespace(syscall=syscall)):
                    with self.assertRaisesRegex(ConfigurationError, "requires Linux x86_64 LP64"):
                        deployment._atomic_rename()
                syscall.assert_not_called()

    def test_loader_failure_and_missing_syscall_fail_closed(self):
        with patch("zpm.deployment.sys.platform", "linux"), \
                patch("zpm.deployment.ctypes.CDLL", side_effect=OSError("loader failure")) as loader:
            with self.assertRaisesRegex(ConfigurationError, "libc is unavailable"):
                deployment._atomic_rename()
        loader.assert_called_once_with(None, use_errno=True)
        with patch("zpm.deployment.sys.platform", "linux"), \
                patch("zpm.deployment.platform.machine", return_value="x86_64"), \
                patch("zpm.deployment.ctypes.sizeof", return_value=8), \
                patch("zpm.deployment.ctypes.CDLL", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ConfigurationError, "must provide syscall"):
                deployment._atomic_rename()

    def test_errno_is_preserved_and_a_failing_wrapper_never_selects_syscall(self):
        for backend in ("wrapper", "syscall"):
            for error, message in ((errno.EEXIST, "not replaced"), (errno.ENOSYS, "unavailable on this Linux filesystem"),
                                   (errno.EINVAL, "unavailable on this Linux filesystem"),
                                   (errno.ENOTSUP, "unavailable on this Linux filesystem"),
                                   (errno.EACCES, f"failed \\(errno {errno.EACCES}\\)"),
                                   (errno.EXDEV, f"failed \\(errno {errno.EXDEV}\\)")):
                with self.subTest(backend=backend, errno=error):
                    def fail(*args):
                        ctypes.set_errno(error)
                        return -1
                    function = Mock(side_effect=fail)
                    unused = Mock(side_effect=AssertionError("failing wrapper selected syscall"))
                    library = (SimpleNamespace(renameat2=function, syscall=unused) if backend == "wrapper"
                               else SimpleNamespace(syscall=function))
                    with patch("zpm.deployment.sys.platform", "linux"), \
                            patch("zpm.deployment.platform.machine", return_value="x86_64"), \
                            patch("zpm.deployment.ctypes.sizeof", return_value=8), \
                            patch("zpm.deployment.ctypes.CDLL", return_value=library):
                        with self.assertRaisesRegex(ConfigurationError, message):
                            deployment._publish_stage(7, "stage", "destination")
                    self.assertEqual(function.call_count, 1)
                    self.assertEqual(ctypes.get_errno(), error)
                    unused.assert_not_called()


if __name__ == "__main__":
    unittest.main()
