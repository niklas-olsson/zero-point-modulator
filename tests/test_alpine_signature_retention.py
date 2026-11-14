"""Controlled APK-helper and storage-observer chronology, with no native tools."""
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.contracts import check_musl_preparation as musl


def archive(name, body):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.USTAR_FORMAT) as stream:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        stream.addfile(info, io.BytesIO(body))
    return result.getvalue()


class SignatureRetentionTests(unittest.TestCase):
    def setUp(self):
        parent = runtime.REPO / ".cache/alpine-signature-fixtures"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def package(self, root=None):
        root = self.root if root is None else root
        keys = root / "keys"
        keys.mkdir()
        data = gzip.compress(archive("usr/bin/fixture", b"fixture payload"), mtime=0)
        fields = ("pkgname = fixture\npkgver = 1-r0\narch = x86_64\ndatahash = " + musl.digest(data) + "\n").encode()
        control = gzip.compress(archive(".PKGINFO", fields), mtime=0)
        signature = b"s" * 128
        raw = gzip.compress(archive(".SIGN.RSA.fixture.rsa.pub", signature), mtime=0) + control + data
        path = root / "fixture.apk"
        path.write_bytes(raw)
        row = {"name": "fixture", "version": "1-r0", "architecture": "x86_64", "signature_key": "fixture.rsa.pub",
               "filename": "fixture.apk", "size": len(raw), "sha256": musl.digest(raw), "data_sha256": musl.digest(data),
               "control_checksum": "Q1" + base64.b64encode(hashlib.sha1(control).digest()).decode()}
        return path, row, keys, signature, control

    def assert_inputs(self, directory, signature, control):
        info = directory.lstat()
        self.assertTrue(stat.S_ISDIR(info.st_mode))
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid), (0o700, os.getuid(), os.getgid()))
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["control.gz", "signature"])
        for name, expected in (("signature", signature), ("control.gz", control)):
            path = directory / name
            self.assertEqual(path.read_bytes(), expected)
            info = path.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, info.st_nlink),
                             (0o600, os.getuid(), os.getgid(), 1))

    def test_retained_success_survives_actual_helper_completion_between_walk_and_lstat(self):
        path, row, keys, signature, control = self.package()
        directory = self.root / ("signature-" + row["sha256"])
        ready, release, finished = threading.Event(), threading.Event(), threading.Event()
        errors, result = [], []

        def controlled_crypto(command, timeout):
            self.assertEqual(Path(command[-1]), directory / "control.gz")
            self.assertEqual(Path(command[-2]), directory / "signature")
            self.assertEqual(timeout, 10)
            ready.set()
            self.assertTrue(release.wait(3))
            return "Verified OK\n"  # Fixed result fixture, no OpenSSL execution.

        def verify():
            try:
                result.append(musl.verify_package(path, row, keys, controlled_crypto, retain_signature_inputs=True))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=verify)
        worker.start()
        original_walk = os.walk
        enumerated = []

        def enumerate_then_complete(top, **options):
            for current, dirs, files in original_walk(top, **options):
                if Path(current) == self.root and directory.name in dirs:
                    enumerated.append(True)
                    release.set()
                    self.assertTrue(finished.wait(3))
                yield current, dirs, files

        try:
            self.assertTrue(ready.wait(3))
            with patch.object(runtime.os, "walk", enumerate_then_complete):
                total = runtime.logical_files([self.root], temporary_root=self.root)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(enumerated, [True])
        self.assertGreater(result[0], 0)
        self.assert_inputs(directory, signature, control)
        expected = sum(runtime.TRANSIENT_FILES.values()) + len(path.read_bytes()) + len(signature) + len(control)
        self.assertEqual(total, expected)
        self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root), expected)

    def test_default_shared_verifier_keeps_its_original_temporary_cleanup(self):
        path, row, keys, _, _ = self.package()
        seen = []

        def crypto(command, timeout):
            directory = Path(command[-1]).parent
            seen.append(directory)
            self.assertTrue(directory.is_dir())
            return "Verified OK\n"

        musl.verify_package(path, row, keys, crypto)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())

    def test_failure_keeps_exact_inputs_and_original_error_until_owner_retirement(self):
        path, row, keys, signature, control = self.package()
        error = runtime.Refused("APK_VERIFY_DEADLINE")
        with self.assertRaises(runtime.Refused) as caught:
            musl.verify_package(path, row, keys, Mock(side_effect=error), retain_signature_inputs=True)
        self.assertIs(caught.exception, error)
        directory = self.root / ("signature-" + row["sha256"])
        self.assert_inputs(directory, signature, control)
        self.assertEqual(runtime.logical_files([self.root]), len(path.read_bytes()) + len(signature) + len(control))
        # Only the owner retires this complete private attempt, after its last
        # observation. The shared verifier never removes its retained inputs.
        self.temporary.cleanup()
        self.assertFalse(self.root.exists())

    def test_existing_workspace_directory_file_or_link_is_never_reused(self):
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                root = self.root / kind
                root.mkdir()
                path, row, keys, _, _ = self.package(root)
                workspace = root / ("signature-" + row["sha256"])
                target = root / "unrelated"
                target.write_bytes(b"preserve")
                if kind == "directory":
                    workspace.mkdir()
                elif kind == "file":
                    workspace.write_bytes(b"original")
                else:
                    workspace.symlink_to(target)
                crypto = Mock(side_effect=AssertionError("must not dispatch"))
                with self.assertRaises(FileExistsError):
                    musl.verify_package(path, row, keys, crypto, retain_signature_inputs=True)
                crypto.assert_not_called()
                self.assertEqual(target.read_bytes(), b"preserve")
                if kind == "file":
                    self.assertEqual(workspace.read_bytes(), b"original")

    def test_exclusive_input_creation_refuses_file_hardlink_symlink_and_fifo(self):
        target = self.root / "unrelated"
        target.write_bytes(b"preserve")
        for kind in ("file", "hardlink", "symlink", "fifo"):
            with self.subTest(kind=kind):
                path = self.root / kind
                if kind == "file":
                    path.write_bytes(b"original")
                elif kind == "hardlink":
                    os.link(target, path)
                elif kind == "symlink":
                    path.symlink_to(target)
                else:
                    os.mkfifo(path)
                with self.assertRaises(FileExistsError):
                    musl.write_signature_input(path, b"replacement", retain=True)
                self.assertEqual(target.read_bytes(), b"preserve")
                if kind == "file":
                    self.assertEqual(path.read_bytes(), b"original")

    def test_retained_directory_owner_and_mode_must_match(self):
        for field in ("uid", "gid", "mode"):
            with self.subTest(field=field):
                root = self.root / field
                root.mkdir()
                path, row, keys, _, _ = self.package(root)
                ordinary = Path.lstat

                def changed(value):
                    info = ordinary(value)
                    if value.name == "signature-" + row["sha256"]:
                        fields = list(info)
                        fields[{"uid": 4, "gid": 5, "mode": 0}[field]] = (
                            info.st_uid + 1 if field == "uid" else info.st_gid + 1 if field == "gid" else stat.S_IFDIR | 0o755)
                        return os.stat_result(fields)
                    return info

                with patch.object(Path, "lstat", changed), self.assertRaisesRegex(musl.Refused, "signature directory"):
                    musl.verify_package(path, row, keys, Mock(), retain_signature_inputs=True)

    def test_retained_file_owner_mode_and_link_count_refuse_before_write(self):
        original = os.fstat
        for field in ("uid", "gid", "mode", "links"):
            with self.subTest(field=field):
                path = self.root / field

                def changed(descriptor):
                    info = original(descriptor)
                    fields = list(info)
                    index = {"uid": 4, "gid": 5, "mode": 0, "links": 3}[field]
                    fields[index] = (info.st_uid + 1 if field == "uid" else info.st_gid + 1 if field == "gid"
                                     else stat.S_IFREG | 0o644 if field == "mode" else 2)
                    return os.stat_result(fields)

                with patch.object(musl.os, "fstat", changed), self.assertRaisesRegex(musl.Refused, "signature input"):
                    musl.write_signature_input(path, b"must not write", retain=True)
                self.assertEqual(path.read_bytes(), b"")

    def test_retention_flag_rejects_integer_and_string_aliases(self):
        path, row, keys, _, _ = self.package()
        for value in (0, 1, "true", None):
            with self.subTest(value=value), self.assertRaisesRegex(musl.Refused, "retention option"):
                musl.verify_package(path, row, keys, Mock(), retain_signature_inputs=value)
        self.assertEqual(list(self.root.glob("signature-*")), [])

    def test_runtime_verifier_retains_inputs_without_renewing_original_deadline(self):
        path, row, keys, signature, control = self.package()
        apks = self.root / "inputs/apks"
        apks.mkdir(parents=True)
        path.rename(apks / row["filename"])
        trial = SimpleNamespace(root=self.root, owner="controlled-fixture", output_budget=object(), documents={
            "inputs-v1.json": {"oci": {"layers": [], "trusted_keys": {}, "config_sha256": "a" * 64},
                               "apk_packages": [row], "wheels": []},
            "native-extensions-v1.json": {"members": []}})
        deadline = time.monotonic() + 10
        received = []

        def crypto(command, seconds, original, directory, budget):
            received.append((seconds, original, directory, budget))
            self.assert_inputs(self.root / ("signature-" + row["sha256"]), signature, control)
            return "Verified OK\n"

        with patch.object(runtime, "bounded_openssl", crypto):
            runtime.verify_artifacts(trial, deadline)
        self.assertEqual(received, [(10, deadline, self.root / "logs", trial.output_budget)])
        self.assert_inputs(self.root / ("signature-" + row["sha256"]), signature, control)

    def test_expired_original_runtime_deadline_refuses_before_workspace_or_crypto(self):
        _, row, _, _, _ = self.package()
        trial = SimpleNamespace(root=self.root, documents={
            "inputs-v1.json": {"oci": {"layers": [], "trusted_keys": {}}, "apk_packages": [row], "wheels": []},
            "native-extensions-v1.json": {"members": []}})
        with patch.object(runtime, "bounded_openssl") as crypto, self.assertRaisesRegex(runtime.Refused, "ARTIFACT_VERIFY_DEADLINE"):
            runtime.verify_artifacts(trial, time.monotonic() - 1)
        crypto.assert_not_called()
        self.assertEqual(list(self.root.glob("signature-*")), [])

    def test_closed_catalog_bounds_retained_bytes_inside_unchanged_storage_cap(self):
        recipe = json.loads((candidate.ROOT / "inputs-v1.json").read_bytes())
        prior = json.loads((candidate.ROOT / "active-plan-4.json").read_bytes())
        rows = recipe["apk_packages"]
        policy = runtime.FIXED_POLICY["apk_signature_inputs"]
        self.assertEqual(len(rows), policy["directories"])
        self.assertEqual(len(rows) * 2, policy["files"])
        self.assertEqual(sum(row["size"] for row in rows) + len(rows) * 1024, policy["maximum_combined_bytes"])
        self.assertEqual(policy["maximum_combined_bytes"], 78980231)
        self.assertEqual(runtime.CAPS, prior["caps"])
        self.assertEqual(runtime.TRANSIENT_FILES, prior["policy"]["transient_file_reservations"])


if __name__ == "__main__":
    unittest.main()
