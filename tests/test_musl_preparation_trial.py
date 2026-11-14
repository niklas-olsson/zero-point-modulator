"""Host-side refusal checks for the finite native musl qualification fixture."""
import base64
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.contracts import check_musl_preparation as trial


def archive(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.PAX_FORMAT) as stream:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    return result.getvalue()


class MuslInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs_path = trial.REPOSITORY / "tests/contracts/musl-preparation-inputs.json"
        self.inputs = trial.load_inputs(self.inputs_path)

    def modified_manifest(self, change):
        value = copy.deepcopy(self.inputs)
        change(value)
        path = self.root / "inputs.json"
        path.write_text(json.dumps(value))
        return path

    def test_committed_fixture_is_finite_main_only_and_has_an_exact_inventory(self):
        self.assertEqual(len(self.inputs["packages"]), 28)
        self.assertEqual(len(self.inputs["expected_installed"]), 44)
        self.assertEqual(sum(row["size"] for row in self.inputs["packages"]), 20711858)
        self.assertEqual(self.inputs["expected_installed"]["python3"]["version"], "3.12.16-r0")
        self.assertEqual(self.inputs["expected_installed"]["musl"]["version"], "1.2.5-r13")
        self.assertTrue(all("/main/" in row["url"] for row in self.inputs["packages"]))

    def test_unknown_fields_mutable_origins_and_inconsistent_inventory_refuse_before_acquisition(self):
        mutations = [lambda x: x.update(extra=True),
                     lambda x: x["caps"].update(transfer_bytes=10**12),
                     lambda x: x["packages"][0].update(url="https://example.com/fixture.apk"),
                     lambda x: x["packages"][0].update(filename="../fixture.apk"),
                     lambda x: x["packages"][0].update(size=True),
                     lambda x: x["packages"][0].update(size=64*1024**2),
                     lambda x: x["packages"][0].update(signature_key="unreviewed.rsa.pub"),
                     lambda x: x["expected_installed"]["python3"].update(version="3.14.0-r0")]
        for change in mutations:
            with self.subTest(change=change):
                with self.assertRaises(trial.Refused):
                    trial.load_inputs(self.modified_manifest(change))

    def test_duplicate_json_keys_are_not_last_write_wins(self):
        path = self.root / "duplicate.json"
        path.write_text('{"schema":"one","schema":"two"}')
        with self.assertRaisesRegex(trial.Refused, "duplicate JSON key"):
            trial.load_inputs(path)

    def test_gzip_stream_count_and_decoded_cap_refuse(self):
        valid = gzip.compress(b"content", mtime=0)
        self.assertEqual(trial.gzip_streams(valid, 1)[0][1], b"content")
        for data in (valid + valid, valid + b"unparsed", valid[:-1]):
            with self.subTest(data=data[-12:]), self.assertRaises(trial.Refused):
                trial.gzip_streams(data, 1)
        with self.assertRaisesRegex(trial.Refused, "oversized"):
            trial.gzip_streams(gzip.compress(b"x"*100000, mtime=0), 1, decoded_limit=1024)

    def test_tar_traversal_duplicates_sparse_and_trailing_data_refuse(self):
        for data in (archive([("../escape", b"x")]), archive([("same", b"a"), ("same", b"b")]),
                     archive([("one", b"a")]) + b"trailing"):
            with self.subTest(size=len(data)), self.assertRaises(trial.Refused):
                trial.tar_entries(data)
        sparse = io.BytesIO()
        with tarfile.open(fileobj=sparse, mode="w", format=tarfile.PAX_FORMAT) as stream:
            member = tarfile.TarInfo("sparse")
            member.size = 1
            member.pax_headers = {"GNU.sparse.map": "0,1", "GNU.sparse.size": str(1 << 40)}
            stream.addfile(member, io.BytesIO(b"x"))
        with patch.object(tarfile.TarFile, "extractfile", side_effect=AssertionError("sparse bytes read")):
            with self.assertRaisesRegex(trial.Refused, "unsafe APK tar member"):
                trial.tar_entries(sparse.getvalue())

    def test_installed_inventory_requires_unique_package_identities(self):
        path = self.root / "installed"
        path.write_text("P:fixture\nV:1-r0\nC:Q1fixture\n\nP:fixture\nV:2-r0\nC:Q1other\n")
        with self.assertRaisesRegex(trial.Refused, "invalid installed package"):
            trial.installed_inventory(path)


class ApkSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.private = cls.root / "fixture-private.pem"
        cls.key = cls.root / "fixture.rsa.pub"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(cls.private)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        subprocess.run(["openssl", "pkey", "-in", str(cls.private), "-pubout", "-out", str(cls.key)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_command(self, command, timeout):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if result.returncode:
            raise trial.Refused("signature command refused")
        return result.stdout

    def package(self):
        data = gzip.compress(archive([("usr/bin/fixture", b"fixture payload")]), mtime=0)
        info = f"pkgname = fixture\npkgver = 1-r0\narch = x86_64\ndatahash = {trial.digest(data)}\n".encode()
        control = gzip.compress(archive([(".PKGINFO", info)]), mtime=0)
        signature = subprocess.run(["openssl", "dgst", "-sha1", "-sign", str(self.private)],
                                   input=control, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10).stdout
        signed = gzip.compress(archive([(".SIGN.RSA.fixture.rsa.pub", signature)]), mtime=0)
        raw = signed + control + data
        row = {"name": "fixture", "version": "1-r0", "architecture": "x86_64", "signature_key": self.key.name,
               "size": len(raw), "sha256": trial.digest(raw), "data_sha256": trial.digest(data),
               "control_checksum": "Q1" + base64.b64encode(hashlib.sha1(control).digest()).decode()}
        path = self.root / "fixture.apk"
        path.write_bytes(raw)
        return path, row, (signed, control, data)

    def test_real_signature_and_index_control_identity_admit_exact_payload(self):
        path, row, parts = self.package()
        self.assertGreater(trial.verify_package(path, row, self.root, self.run_command), 0)
        row["control_checksum"] = "Q1" + base64.b64encode(b"x"*20).decode()
        with self.assertRaisesRegex(trial.Refused, "index control checksum"):
            trial.verify_package(path, row, self.root, self.run_command)

    def test_modified_control_cannot_be_admitted_by_rehashing_manifest_fields(self):
        path, row, parts = self.package()
        control = gzip.compress(archive([(".PKGINFO", b"pkgname = substituted\n")]), mtime=0)
        raw = parts[0] + control + parts[2]
        path.write_bytes(raw)
        row.update(size=len(raw), sha256=trial.digest(raw), control_checksum="Q1" + base64.b64encode(hashlib.sha1(control).digest()).decode())
        with self.assertRaisesRegex(trial.Refused, "signature command refused"):
            trial.verify_package(path, row, self.root, self.run_command)

    def test_modified_data_cannot_be_admitted_by_rehashing_the_whole_file(self):
        path, row, parts = self.package()
        raw = parts[0] + parts[1] + gzip.compress(archive([("usr/bin/fixture", b"substituted")]), mtime=0)
        path.write_bytes(raw)
        row.update(size=len(raw), sha256=trial.digest(raw))
        with self.assertRaisesRegex(trial.Refused, "authenticated APK data hash"):
            trial.verify_package(path, row, self.root, self.run_command)

    def test_signed_identity_and_archive_structure_are_still_checked(self):
        path, row, parts = self.package()
        row["name"] = "substituted"
        with self.assertRaisesRegex(trial.Refused, "authenticated APK identity"):
            trial.verify_package(path, row, self.root, self.run_command)
        raw = path.read_bytes() + gzip.compress(b"unadmitted", mtime=0)
        path.write_bytes(raw)
        row.update(size=len(raw), sha256=trial.digest(raw))
        with self.assertRaisesRegex(trial.Refused, "unexpected gzip stream"):
            trial.verify_package(path, row, self.root, self.run_command)


class MuslTrialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        inputs = trial.load_inputs(trial.REPOSITORY / "tests/contracts/musl-preparation-inputs.json")
        self.runner = trial.Trial(self.root, inputs)

    def test_ambient_docker_context_and_credentials_cannot_redirect_the_trial(self):
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "foreign", "DOCKER_HOST": "tcp://foreign:2376",
                                     "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/foreign", "HOME": "/foreign"}):
            root = self.root / "second"
            root.mkdir()
            runner = trial.Trial(root, self.runner.inputs)
        self.assertEqual(runner.env["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertNotIn("DOCKER_CONTEXT", runner.env)
        self.assertNotIn("DOCKER_TLS_VERIFY", runner.env)
        self.assertNotIn("DOCKER_CERT_PATH", runner.env)
        self.assertEqual(Path(runner.env["DOCKER_CONFIG"]).joinpath("config.json").read_text(), "{}\n")

    @unittest.skipUnless(sys.platform == "linux", "owned process groups use POSIX")
    def test_deadline_kills_owned_acquisition_and_leader_exit_kills_detached_descendants(self):
        started = time.monotonic()
        with self.assertRaisesRegex(trial.Refused, "deadline"):
            self.runner.run([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.1)
        self.assertLess(time.monotonic() - started, 2)
        marker = self.root / "escaped-marker"
        child = "import time,pathlib; time.sleep(1); pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        self.runner.run([sys.executable, "-c", parent])
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_loop_custody_is_reconciled_by_fresh_backing_file_after_lost_output(self):
        backing = self.root / "ext2.img"
        self.runner.loop_backings.append(backing)
        calls = []
        detached = False
        def command(args, **kwargs):
            nonlocal detached
            calls.append(args)
            if "--list" in args:
                rows = [{"name": "/dev/loop9", "back-file": "/unowned/file"}]
                if not detached:
                    rows.append({"name": "/dev/loop8", "back-file": str(backing)})
                return json.dumps({"loopdevices": rows})
            if "--detach" in args:
                detached = True
            return ""
        with patch.object(self.runner, "run", side_effect=command):
            result = self.runner.cleanup()
        self.assertTrue(result["confirmed"])
        self.assertIn(["sudo", "-n", "losetup", "--detach", "/dev/loop8"], calls)
        self.assertFalse(any("/dev/loop9" in command for command in calls))

    def test_uncertain_cleanup_never_reports_qualification_success(self):
        self.runner.images = ["zpm-musl-private:base"]
        with patch.object(self.runner, "docker", side_effect=trial.Refused("daemon unavailable")):
            result = self.runner.cleanup()
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["errors"], ["image:zpm-musl-private:base"])

    def test_missing_docker_measurements_refuse_instead_of_assuming_zero(self):
        self.runner.containers.append("owned-container")
        with patch.object(self.runner, "docker", return_value='[{"SizeRootFs": 1000}]'):
            with self.assertRaisesRegex(trial.Refused, "container measurement missing"):
                self.runner.measure_docker()

    def test_missing_or_wrong_filesystem_report_cannot_reuse_an_earlier_result(self):
        path = self.root / "native-result.json"
        with self.assertRaisesRegex(trial.Refused, "result is absent"):
            trial.read_native_result(path, "ext4", "current")
        for filesystem, revision in (("ext2", "current"), ("ext4", "stale")):
            path.write_text(json.dumps({"status": "passed", "filesystem": filesystem, "source_revision": revision}))
            with self.assertRaisesRegex(trial.Refused, "filesystem/source differs"):
                trial.read_native_result(path, "ext4", "current")
        path.write_text(json.dumps({"status": "passed", "filesystem": "ext4", "source_revision": "current"}))
        self.assertEqual(trial.read_native_result(path, "ext4", "current")["filesystem"], "ext4")

    def test_snapshot_uses_exact_git_blobs_despite_dirty_and_untracked_files(self):
        repository = self.root / "repository"
        repository.mkdir()
        (repository / "zpm").mkdir()
        (repository / "tests/contracts").mkdir(parents=True)
        (repository / "zpm/value.py").write_text("approved = True\n")
        (repository / "tests/test_preparation_recovery.py").write_text("# approved recovery fixture\n")
        (repository / "tests/contracts/check_musl_preparation.py").write_bytes(Path(trial.__file__).read_bytes())
        for arguments in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "snapshot"]):
            subprocess.run(["git", *arguments], cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (repository / "zpm/value.py").write_text("approved = False\n")
        (repository / "tests/evil.py").write_text("raise AssertionError('untracked')\n")
        with patch.object(trial, "REPOSITORY", repository):
            source, report = self.runner.snapshot()
        self.assertEqual((source / "zpm/value.py").read_text(), "approved = True\n")
        self.assertFalse((source / "tests/evil.py").exists())
        self.assertEqual(report["files"], 3)


if __name__ == "__main__":
    unittest.main()
