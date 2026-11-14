"""Source-only #63 fixtures. No candidate artifact acquisition or execution."""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.contracts import check_alpine_sealed_candidate as trial


def tar_bytes(names):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content in names:
            member = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
            member.size = len(content) if member.isfile() else 0
            archive.addfile(member, io.BytesIO(content) if member.isfile() else None)
    return raw.getvalue()


def layer(raw, members=1):
    compressed = gzip.compress(raw, mtime=0)
    return compressed, {"compressed_bytes": len(compressed), "digest": "sha256:" + trial.digest(compressed),
                        "decoded_bytes": len(raw), "diff_id": "sha256:" + trial.digest(raw), "archive_members": members}


class AlpineSourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan_raw = (trial.ROOT / "plan-v1.json").read_bytes()
        self.plan_hash = trial.digest(self.plan_raw)
        self.plan = json.loads(self.plan_raw)
        self.documents = {name: json.loads((trial.ROOT / name).read_bytes()) for name in trial.METADATA}

    def test_exact_public_recipe_oci_closure_and_original_tuple(self):
        summary = trial.load_bundle(trial.ROOT, self.plan_hash)
        self.assertEqual(summary["binary_artifacts"], 173)
        self.assertEqual(summary["binary_bytes"], 209_309_575)
        self.assertEqual(summary["expected_installed_apk_records"], 64)
        self.assertEqual(summary["environment_wheels"], {"zpm": 3, "openjules": 83, "polyphony": 70})
        self.assertEqual(summary["native_shared_objects"], 53)
        self.assertLess(summary["transfer_reservation_bytes"], 512 * 1024**2)
        self.assertEqual(self.plan["source_exceptions"]["symlinks"][0]["target"], "polyphony")
        self.assertEqual(len(self.plan["source_exceptions"]["uninitialized_gitlinks"]), 2)
        self.assertIsNone(self.documents["inputs-v1.json"]["interpreter"]["observed_interpreter_identity"])

    def test_plan_hash_admission_precedes_any_document_read(self):
        with patch.object(trial, "read_bounded", side_effect=AssertionError("input read")):
            for value in (None, "", "A" * 64, "a" * 63):
                with self.subTest(value=value), self.assertRaises(trial.Refused):
                    trial.load_bundle(trial.ROOT, value)
        with self.assertRaisesRegex(trial.Refused, "INPUT_HASH"):
            trial.load_bundle(trial.ROOT, "0" * 64)

    def test_changed_caps_authority_sources_layout_and_execution_claims_refuse(self):
        changes = [
            lambda p: p.update(extra=True), lambda p: p.update(active_trial_implemented=True),
            lambda p: p.update(mode="native-run"), lambda p: p["caps"].update(wall_seconds=1801),
            lambda p: p["caps"].update(running_containers=True),
            lambda p: p["authority"]["qualification"].update(uid=0),
            lambda p: p["authority"]["assembly"]["cap_add"].append("SYS_ADMIN"),
            lambda p: p["production_sources"]["openjules"].update(revision="f" * 40),
            lambda p: p["source_exceptions"]["symlinks"][0].update(target="/etc/passwd"),
            lambda p: p["native_file_pins"][0].update(sha256="0" * 64),
            lambda p: p["external_prerequisites"].remove("reviewed-active-runner-and-workflow"),
            lambda p: p["probe_ids"].append("arbitrary-command"),
        ]
        for change in changes:
            value = copy.deepcopy(self.plan)
            change(value)
            with self.subTest(change=change), self.assertRaises(trial.Refused):
                trial.validate_documents(value, self.documents)

    def test_original_public_artifact_and_interpreter_inputs_cannot_be_silently_retargeted(self):
        changes = [
            lambda d: d["inputs-v1.json"]["source_revisions"].update(zpm="0" * 40),
            lambda d: d["inputs-v1.json"]["interpreter"].update(observed_interpreter_identity={"abi": "guessed"}),
            lambda d: d["inputs-v1.json"]["interpreter"].update(path="/usr/bin/python3"),
            lambda d: d["inputs-v1.json"].update(runtime_execution=0),
            lambda d: d["inputs-v1.json"]["apk_packages"][0].update(url="https://example.com/unreviewed.apk"),
            lambda d: d["inputs-v1.json"]["apk_packages"][0].update(signature_key_sha256="0" * 64),
            lambda d: d["inputs-v1.json"]["wheels"][0].update(filename="../escape.whl"),
            lambda d: d["inputs-v1.json"]["wheels"][0].update(size=True),
            lambda d: d["inputs-v1.json"]["environments"]["zpm"]["wheels"].pop(),
            lambda d: d["inputs-v1.json"]["oci"]["base_installed_packages"][0].update(version="replacement"),
            lambda d: d["oci-config.json"]["config"].update(Entrypoint=["unreviewed-program"]),
            lambda d: d["native-extensions-v1.json"]["members"][0].update(import_module="not;python"),
        ]
        for change in changes:
            value = copy.deepcopy(self.documents)
            change(value)
            with self.subTest(change=change), self.assertRaises(trial.Refused):
                trial.validate_documents(self.plan, value)

    def test_exact_file_bytes_symlinks_fifo_size_and_duplicate_json_are_refused(self):
        root = self.root / "inputs"
        shutil.copytree(trial.ROOT, root)
        target = root / "oci-config.json"
        target.write_bytes(target.read_bytes() + b' ')
        with self.assertRaises(trial.Refused):
            trial.load_bundle(root, self.plan_hash)
        target.unlink()
        target.symlink_to(trial.ROOT / "oci-config.json")
        with self.assertRaises(trial.Refused):
            trial.load_bundle(root, self.plan_hash)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(trial.Refused):
            trial.read_bounded(fifo, 100)
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1.0}', b'\xff', b'[' * 2000 + b'0' + b']' * 2000):
            with self.subTest(raw=raw[:20]), self.assertRaises(trial.Refused):
                trial.decode(raw)

    def test_private_report_creation_is_durable_and_never_overwrites_evidence(self):
        target = self.root / "report.json"
        raw = trial.report_bytes(trial.new_report(self.plan_hash))
        with patch.object(trial.os, "fsync", wraps=os.fsync) as fsync:
            trial.write_report(target, raw)
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(trial.Refused):
            trial.write_report(target, b'changed')
        self.assertEqual(target.read_bytes(), raw)
        link = self.root / "linked-report.json"
        link.symlink_to(target)
        with self.assertRaises(trial.Refused):
            trial.write_report(link, b'changed')
        self.assertEqual(target.read_bytes(), raw)

    def test_failure_and_oversized_report_keep_first_failure_and_unexecuted_verdict(self):
        report = trial.new_report(self.plan_hash, "a" * 40)
        trial.fail(report, "FIRST_FAILURE")
        trial.fail(report, "SECOND_FAILURE")
        report["input_summary"] = "x" * (trial.CAPS["report_bytes"] + 1)
        raw = trial.report_bytes(report)
        self.assertLessEqual(len(raw), 256 * 1024)
        retained = json.loads(raw)
        self.assertEqual(retained["first_failure"], "FIRST_FAILURE")
        self.assertEqual(retained["truncation_reason"], "REPORT_BOUND")
        self.assertEqual(retained["plan_sha256"], self.plan_hash)
        self.assertEqual(retained["harness_source_revision"], "a" * 40)
        self.assertEqual(set(retained["native_checks"].values()), {"NOT_RUN"})
        self.assertFalse(retained["native_execution_qualified"])

    def test_cold_cli_is_source_only_before_and_after_import(self):
        report = self.root / "cold-report.json"
        script = '''
import sys
def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
        raise AssertionError("effect before native review: " + event)
    if event == "import" and args[0].split(".")[0] in {"src", "aiohttp", "cryptography", "litellm", "tokenizers"}:
        raise AssertionError("candidate package import")
sys.addaudithook(audit)
from tests.contracts.check_alpine_sealed_candidate import main
raise SystemExit(main(sys.argv[1:]))
'''
        result = subprocess.run([sys.executable, "-B", "-c", script, "--plan-sha256", self.plan_hash,
                                 "--report", str(report)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        retained = json.loads(report.read_bytes())
        self.assertEqual(retained["status"], "PRETRIAL_SOURCE_PASS")
        self.assertEqual(retained["candidate_processes_started"], 0)
        self.assertEqual(retained["artifact_body_bytes_received"], 0)
        self.assertEqual(set(retained["native_checks"].values()), {"NOT_RUN"})
        self.assertFalse(retained["active_trial_implemented"])
        result = subprocess.run([sys.executable, "-B", "-c", script, "--plan-sha256", self.plan_hash,
                                 "--report", str(report), "--run"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --run", result.stderr)
        self.assertEqual(json.loads(report.read_bytes()), retained)


class AlpineArchiveGuardTests(unittest.TestCase):
    def test_small_synthetic_layer_is_checked_without_extraction(self):
        raw, row = layer(tar_bytes([("usr/local/example.txt", b"synthetic only")]))
        self.assertEqual(trial.verify_layer_bytes(raw, row)["members"], 1)
        for change in ({"digest": "sha256:" + "0" * 64}, {"diff_id": "sha256:" + "0" * 64},
                       {"decoded_bytes": 1}, {"archive_members": 2}):
            with self.subTest(change=change), self.assertRaises(trial.Refused):
                trial.verify_layer_bytes(raw, row | change)
        trailing, changed = layer(tar_bytes([("file", b"data")]))
        trailing += gzip.compress(b'additional stream')
        changed.update(compressed_bytes=len(trailing), digest="sha256:" + trial.digest(trailing))
        with self.assertRaises(trial.Refused):
            trial.verify_layer_bytes(trailing, changed)

    def test_archive_escape_duplicate_device_and_link_escape_refuse(self):
        link = tarfile.TarInfo("unsafe-link")
        link.type, link.linkname = tarfile.SYMTYPE, "../outside"
        device = tarfile.TarInfo("device")
        device.type = tarfile.CHRTYPE
        for entries in [([("../escape", b'x')]), ([('/absolute', b'x')]),
                        ([('duplicate', b'x'), ('duplicate', b'y')]), ([(link, b'')]), ([(device, b'')])]:
            raw, row = layer(tar_bytes(entries), len(entries))
            with self.subTest(entries=entries), self.assertRaises(trial.Refused):
                trial.verify_layer_bytes(raw, row)

    def _wheel(self, extra=None):
        metadata = b'Metadata-Version: 2.1\nName: fixture-package\nVersion: 1.0\n'
        contents = {"fixture_package/__init__.py": b'raise AssertionError("must never execute")\n',
                    "fixture_package-1.0.dist-info/METADATA": metadata}
        if extra:
            contents.update(extra)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, raw in contents.items():
                archive.writestr(name, raw)
        raw = output.getvalue()
        row = {"filename": "fixture_package-1.0-py3-none-any.whl", "size": len(raw), "sha256": trial.digest(raw),
               "name": "fixture-package", "version": "1.0", "core_metadata_sha256": trial.digest(metadata),
               "uncompressed_member_bytes": sum(map(len, contents.values())), "native_library_count": 0}
        return raw, row

    def test_synthetic_wheel_checks_reuse_production_identity_without_import(self):
        raw, row = self._wheel()
        self.assertEqual(trial.verify_wheel_bytes(raw, row, [])["name"], "fixture-package")
        for change in ({"name": "replacement"}, {"version": "2.0"}, {"sha256": "0" * 64},
                       {"core_metadata_sha256": "0" * 64}, {"uncompressed_member_bytes": 1}):
            with self.subTest(change=change), self.assertRaises(trial.Refused):
                trial.verify_wheel_bytes(raw, row | change, [])
        raw, row = self._wheel({"../escape.py": b'not executed'})
        with self.assertRaises(trial.Refused):
            trial.verify_wheel_bytes(raw, row, [])

    def test_native_member_hash_and_declared_inventory_are_checked_as_data(self):
        member = "fixture_package/_native.abi3.so"
        payload = b'\x7fELF synthetic non-executable fixture'
        raw, row = self._wheel({member: payload})
        row["native_library_count"] = 1
        native = [{"member": member, "size": len(payload), "sha256": trial.digest(payload)}]
        self.assertEqual(trial.verify_wheel_bytes(raw, row, native)["version"], "1.0")
        native[0]["sha256"] = "0" * 64
        with self.assertRaises(trial.Refused):
            trial.verify_wheel_bytes(raw, row, native)
        with self.assertRaises(trial.Refused):
            trial.verify_wheel_bytes(raw, row, [])


if __name__ == "__main__":
    unittest.main()
