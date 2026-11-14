"""Synthetic/fault checks only. No Docker, candidate acquisition or bootstrap."""
import copy
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.contracts import alpine_sealed_guest as guest


class Fixture(unittest.TestCase):
    def setUp(self):
        parent = runtime.REPO / ".cache/alpine-runtime-fixtures"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.documents = {name: json.loads((candidate.ROOT / name).read_bytes()) for name in candidate.METADATA}

    def tearDown(self):
        self.temporary.cleanup()

    def trial(self):
        root = self.root / "attempt"
        root.mkdir(mode=0o700)
        sources = {name: self.root / name for name in candidate.SOURCES}
        for path in sources.values():
            path.mkdir()
        trial = runtime.Trial(root, sources, "a" * 64, "b" * 40, self.documents)
        trial.control = {"attempt": trial.owner, "uid": os.getuid(), "gid": os.getgid(),
                         "work_deadline_ns": int(trial.work_deadline * 1_000_000_000),
                         "fixture_image": "sha256:" + "c" * 64,
                         "host_namespaces": {"net": "net:[1]", "pid": "pid:[1]"}}
        return trial

    def container(self, trial, row, identity="d" * 64):
        stage = row["stage"]
        return {"Id": identity, "Name": "/" + row["name"], "Image": row["image"], "SizeRw": 100,
                "State": {"Status": "created", "Running": False},
                "Config": {"Labels": {runtime.LABEL: trial.owner}, "Cmd": runtime.COMMANDS[stage],
                           "Env": [key + "=" + value for key, value in runtime.expected_environment(self.documents).items()],
                           "User": "0:0" if stage == "assembly" else f"{os.getuid()}:{os.getgid()}",
                           "WorkingDir": "/work", "Hostname": row["name"], "Domainname": "",
                           "OpenStdin": False, "StdinOnce": False, "Tty": False},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": stage == "qualification", "Privileged": False,
                               "Memory": candidate.CAPS["container_memory_bytes"], "MemorySwap": candidate.CAPS["container_memory_and_swap_bytes"],
                               "PidsLimit": 128, "NanoCpus": 2_000_000_000, "CapDrop": ["ALL"],
                               "CapAdd": candidate.AUTHORITY[stage]["cap_add"], "SecurityOpt": ["no-new-privileges"],
                               "LogConfig": {"Type": "none", "Config": {}}, "IpcMode": "private", "Runtime": "runc",
                               "AutoRemove": False, "PublishAllPorts": False,
                               "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
                "Mounts": [{"Source": source, "Destination": destination, "RW": writable,
                            "Type": "bind", "Propagation": "rprivate"} for source, destination, writable in row["mounts"]]}


class SourceAndConfigurationTests(Fixture):
    def test_empty_tracked_blob_and_exact_executable_mode(self):
        root = self.root / "source"
        root.mkdir()
        (root / "empty").write_bytes(b"")
        (root / "command").write_bytes(b"fixed fixture\n")
        (root / "command").chmod(0o755)
        records = []
        for name, mode in (("empty", "100644"), ("command", "100755")):
            body = (root / name).read_bytes()
            records.append((name, mode, hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()))
        runtime.verify_source_files(root, "harness", records)
        (root / "command").chmod(0o644)
        with self.assertRaisesRegex(candidate.Refused, "SOURCE_MODE"):
            runtime.verify_source_files(root, "harness", records)

    def test_git_configuration_is_checked_before_any_git_command(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        base = '[core]\n repositoryformatversion = 0\n filemode = true\n bare = false\n[remote "origin"]\n url = https://github.com/fixture/source\n fetch = +refs/heads/*:refs/remotes/origin/*\n'
        path = metadata / "config"
        path.write_text(base)
        runtime.source_git_metadata(self.root, "fixture/source")
        for extra in ('[include]\n path = /not-read\n', '[filter "x"]\n clean = /not-run\n',
                      '[http "https://github.com/"]\n extraheader = private\n'):
            path.write_text(base + extra)
            with self.subTest(extra=extra), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaises(candidate.Refused):
                runtime.source_git_metadata(self.root, "fixture/source")

    def test_git_indirection_and_nonordinary_metadata_refuse(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        (metadata / "config").write_text('[core]\n repositoryformatversion=0\n filemode=true\n bare=false\n[remote "origin"]\n url=https://github.com/fixture/source\n fetch=+refs/heads/*:refs/remotes/origin/*\n')
        (metadata / "commondir").write_text("/not-read")
        with self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_INDIRECTION"):
            runtime.source_git_metadata(self.root, "fixture/source")
        (metadata / "commondir").unlink()
        (metadata / "unexpected").symlink_to("/not-read")
        with self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_NONORDINARY"):
            runtime.source_git_metadata(self.root, "fixture/source")

    def test_supported_checkout_gc_auto_zero_is_admitted_without_git(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        base = ('[core]\n repositoryformatversion=0\n filemode=true\n bare=false\n logallrefupdates=true\n'
                '[remote "origin"]\n url=https://github.com/fixture/source\n'
                ' fetch=+refs/heads/*:refs/remotes/origin/*\n[gc]\n auto=0\n')
        for branch in ('', '[branch "main"]\n remote=origin\n merge=refs/heads/main\n'):
            (metadata / "config").write_text(base + branch)
            with self.subTest(branch=bool(branch)), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")):
                runtime.source_git_metadata(self.root, "fixture/source")

    def test_checkout_gc_admission_rejects_other_values_keys_and_sections(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        base = ('[core]\n repositoryformatversion=0\n filemode=true\n bare=false\n'
                '[remote "origin"]\n url=https://github.com/fixture/source\n'
                ' fetch=+refs/heads/*:refs/remotes/origin/*\n')
        changes = ['[gc]\n auto=' + value + '\n' for value in ('1', '-1', '00', 'false', '0k', '"0"', '')]
        changes += ['[gc]\n', '[gc]\n auto=0\n autoDetach=false\n',
                    '[gc]\n auto=0\n pruneExpire=now\n', '[gc "fixture"]\n auto=0\n',
                    '[gc]\n auto=0\n[include]\n path=/not-read\n']
        for extra in changes:
            (metadata / "config").write_text(base + extra)
            with self.subTest(extra=extra), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_CONFIG"):
                runtime.source_git_metadata(self.root, "fixture/source")

    def test_container_environment_authority_and_mount_drift_refuse(self):
        trial = self.trial()
        row = {"stage": "qualification", "name": trial.owner + "-qualification", "image": "sha256:" + "c" * 64,
               "mounts": [("/fixed-input", "/inputs", False), ("/fixed-work", "/work", True)]}
        info = self.container(trial, row)
        def check(value):
            runtime.assert_container(value, "d" * 64, row["name"], row["image"], row["stage"], trial.owner,
                                     row["mounts"], os.getuid(), os.getgid(), runtime.expected_environment(self.documents))
        check(info)
        changes = [lambda x: x["Config"]["Env"].append("PRIVATE_PROVIDER_TOKEN=must-not-pass"),
                   lambda x: x["Config"]["Env"].append("HOME=/work/home"),
                   lambda x: x["Config"].update(WorkingDir="/"),
                   lambda x: x["Config"].update(Entrypoint=["/unapproved"]),
                   lambda x: x["HostConfig"].update(GroupAdd=["0"]),
                   lambda x: x["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
                   lambda x: x["HostConfig"].update(Runtime="unapproved"),
                   lambda x: x["Mounts"].append(x["Mounts"][0])]
        for change in changes:
            value = copy.deepcopy(info)
            change(value)
            with self.subTest(change=change), self.assertRaises(candidate.Refused):
                check(value)

    def test_storage_walk_error_is_never_silently_ignored(self):
        def unreadable(root, **options):
            options["onerror"](PermissionError("fixture"))
            yield str(root), [], []
        with patch.object(runtime.os, "walk", side_effect=unreadable), self.assertRaisesRegex(candidate.Refused, "STORAGE_OBSERVATION"):
            runtime.logical_files([self.root])

    def test_atomic_temporary_slot_keeps_full_charge_if_it_disappears(self):
        relative = "acquisition.json.new"
        path = self.root / relative
        path.write_bytes(b"fixture")
        path.chmod(0o600)
        reserve = sum(runtime.TRANSIENT_FILES.values())
        self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root), reserve)
        ordinary_lstat = runtime.os.lstat
        def disappear(value, *args, **kwargs):
            if Path(value) == path:
                raise FileNotFoundError("controlled atomic replacement")
            return ordinary_lstat(value, *args, **kwargs)
        with patch.object(runtime.os, "lstat", side_effect=disappear):
            self.assertEqual(runtime.logical_files([self.root], temporary_root=self.root), reserve)
        path.rename(self.root / "unrelated.new")
        missing = self.root / "unrelated.new"
        def unknown(value, *args, **kwargs):
            if Path(value) == missing:
                raise FileNotFoundError("unknown disappearance")
            return ordinary_lstat(value, *args, **kwargs)
        with patch.object(runtime.os, "lstat", side_effect=unknown), self.assertRaisesRegex(candidate.Refused, "STORAGE_OBSERVATION"):
            runtime.logical_files([self.root], temporary_root=self.root)

    def test_temporary_slot_identity_and_size_still_refuse(self):
        path = self.root / "private-report.json.new"
        path.write_bytes(b"fixture")
        path.chmod(0o644)
        with self.assertRaisesRegex(candidate.Refused, "TRANSIENT_FILE_IDENTITY"):
            runtime.logical_files([self.root], temporary_root=self.root)
        path.chmod(0o600)
        with path.open("r+b") as stream:
            stream.truncate(runtime.TRANSIENT_FILES[path.name] + 1)
        with self.assertRaisesRegex(candidate.Refused, "TRANSIENT_FILE_IDENTITY"):
            runtime.logical_files([self.root], temporary_root=self.root)

    def test_assembled_image_binds_original_layers_and_exact_root_environment(self):
        trial = self.trial()
        identity = "sha256:" + "e" * 64
        row = {"stage": "assembly", "name": trial.owner + "-assembly", "image": identity, "mounts": []}
        info = {"Id": identity, "Os": "linux", "Architecture": "amd64",
                "RootFS": {"Type": "layers", "Layers": [r["diff_id"] for r in self.documents["inputs-v1.json"]["oci"]["layers"]] + ["sha256:" + "f" * 64]},
                "Config": self.container(trial, row)["Config"]}
        runtime.assert_assembled(info, identity, trial)
        for mutate in (lambda x: x["RootFS"]["Layers"].reverse(),
                       lambda x: x["Config"].update(User="1001:1001"),
                       lambda x: x["Config"]["Env"].append("UNAPPROVED=value")):
            changed = copy.deepcopy(info)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(candidate.Refused):
                runtime.assert_assembled(changed, identity, trial)


class MutationTests(Fixture):
    def test_unknown_load_or_commit_keeps_full_intent_charge_when_no_image_is_visible(self):
        for kind, key in (("base", "base_image_bytes"), ("assembled", "assembled_image_bytes")):
            trial = self.trial()
            row = {"tag": trial.owner + "/" + kind + ":1", "id": None, "kind": kind, "removed": False,
                   "state": "creating", "mutation_confirmed": False, "reserved_bytes": runtime.MUTATION_RESERVATIONS[key]}
            trial.journal["image_references"].append(row)
            with patch.object(runtime, "docker_json", return_value=None):
                self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
                trial.cleanup_processes = Mock()
                trial.sample = Mock()
                trial.cleanup()
            self.assertIn("IMAGE_MUTATION_OUTCOME_UNKNOWN", trial.record["cleanup_failures"])
            self.assertFalse(row["removed"])
            self.assertFalse(trial.record["native_execution_qualified"])
            # Each subcase has a separately owned work root.
            self.temporary.cleanup()
            self.setUp()

    def test_create_success_before_cli_reply_is_observed_and_reserved(self):
        trial = self.trial()
        state = {}
        def observed(path, **kwargs):
            return copy.deepcopy(state.get("container")) if "/containers/" in path else None
        trial.sample = Mock(side_effect=trial.observe_mutations)
        def interrupted(command, **kwargs):
            row = trial.journal["containers"][-1]
            self.assertFalse(row["mutation_confirmed"])
            state["container"] = self.container(trial, row)
            self.assertEqual(trial.observe_mutations(), runtime.MUTATION_RESERVATIONS["container_writable_bytes"])
            raise candidate.Refused("CONTROLLED_LOST_CLI_REPLY")
        trial.run = interrupted
        with patch.object(runtime, "docker_json", side_effect=observed), self.assertRaisesRegex(candidate.Refused, "CONTROLLED_LOST_CLI_REPLY"):
            trial.create("sha256:" + "c" * 64, "assembly")
        row = trial.journal["containers"][0]
        self.assertEqual(row["id"], "d" * 64)
        self.assertFalse(row["mutation_confirmed"])
        retained = json.loads((trial.root / "ownership.json").read_bytes())
        self.assertEqual(retained["containers"][0]["id"], "d" * 64)

    def test_removal_keeps_charge_through_cli_success_until_both_absence_queries(self):
        trial = self.trial()
        row = {"name": trial.owner + "-assembly", "id": "d" * 64, "stage": "assembly", "image": "sha256:" + "c" * 64,
               "removed": False, "state": "present", "mutation_confirmed": True,
               "reserved_bytes": runtime.MUTATION_RESERVATIONS["container_writable_bytes"], "mounts": []}
        trial.journal["containers"].append(row)
        trial.container = row["id"]
        state = {"present": True}
        def observed(path, **kwargs):
            return self.container(trial, row) if state["present"] else None
        def remove(command, **kwargs):
            self.assertEqual(row["state"], "removing")
            self.assertFalse(row["removed"])
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            state["present"] = False
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            return b"fixed removed ID"
        trial.run = remove
        with patch.object(runtime, "docker_json", side_effect=observed):
            trial.remove_container(row)
            self.assertEqual(trial.observe_mutations(), 0)
        self.assertTrue(row["removed"])
        self.assertIsNone(trial.container)

    def test_oversized_native_mutation_refuses_without_losing_reservation(self):
        trial = self.trial()
        row = {"tag": trial.owner + "/assembled:1", "id": "sha256:" + "e" * 64, "kind": "assembled",
               "removed": False, "state": "present", "mutation_confirmed": True,
               "reserved_bytes": runtime.MUTATION_RESERVATIONS["assembled_image_bytes"]}
        trial.journal["image_references"].append(row)
        info = {"Id": row["id"], "Config": {"Labels": {runtime.LABEL: trial.owner}}, "Size": row["reserved_bytes"] + 1}
        with patch.object(runtime, "docker_json", return_value=info), self.assertRaisesRegex(candidate.Refused, "IMAGE_STORAGE_BOUND"):
            trial.observe_mutations()
        self.assertFalse(row["removed"])

    def test_interrupted_commit_visible_before_reply_retains_custody_and_uncertainty(self):
        trial = self.trial()
        row = {"tag": trial.owner + "/assembled:1", "id": None, "kind": "assembled", "removed": False,
               "state": "creating", "mutation_confirmed": False,
               "reserved_bytes": runtime.MUTATION_RESERVATIONS["assembled_image_bytes"]}
        trial.journal["image_references"].append(row)
        info = {"Id": "sha256:" + "f" * 64, "Config": {"Labels": {runtime.LABEL: trial.owner}},
                "Size": 200, "RepoTags": [row["tag"]], "RepoDigests": []}
        present = [True]
        def observe(path, **kwargs):
            return copy.deepcopy(info) if present[0] else None
        with patch.object(runtime, "docker_json", side_effect=observe):
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            self.assertEqual(row["id"], info["Id"])
            def remove(command, **kwargs):
                self.assertEqual(row["state"], "removing")
                self.assertFalse(row["removed"])
                present[0] = False
                self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
                return b"removed owned image"
            trial.run = remove
            trial.sample = Mock()
            trial.cleanup_processes = Mock()
            trial.cleanup()
        self.assertTrue(row["removed"])
        self.assertIn("IMAGE_MUTATION_OUTCOME_UNKNOWN", trial.record["cleanup_failures"])
        self.assertEqual(trial.record["native_checks"]["owned_cleanup"], "FAIL")

    def test_latched_observation_failure_prevents_next_effectful_command(self):
        trial = self.trial()
        trial.fail("OBSERVATION_FAILED")
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("must not spawn")), \
                self.assertRaisesRegex(candidate.Refused, "WORK_STOPPED_AFTER_FAILURE"):
            trial.run(["/not-executed"])
        self.assertEqual(trial.record["first_failure"], "OBSERVATION_FAILED")


class ProcessCustodyTests(Fixture):
    def test_historical_pid_reuse_never_adopts_a_foreign_session_member(self):
        trial = self.trial()
        trial.pidfds[(12345, 100)] = -1  # No signal or native handle is used.
        trial.sessions.add(12345)
        rows = {trial.root_identity[0]: {"start": trial.root_identity[1], "ppid": 1, "session": 1, "rss": 1},
                12345: {"start": 101, "ppid": 1, "session": 12345, "rss": 2}}
        trial.retained = {}  # The original has already disappeared in a prior sample.
        with patch.object(runtime.os, "pidfd_open", side_effect=AssertionError("must not adopt foreign process")), \
                self.assertRaisesRegex(candidate.Refused, "OWNED_PID_REUSED"):
            trial.observed_processes(rows)

    @unittest.skipUnless(hasattr(os, "pidfd_open") and hasattr(os, "fork"), "Linux owned-child fixture")
    def test_first_scan_refusal_does_not_admit_fork_work_and_reaps_original_child(self):
        trial = self.trial()
        marker = self.root / "operation-was-admitted"
        trial.sample = Mock(side_effect=candidate.Refused("CONTROLLED_FIRST_SCAN"))
        try:
            with self.assertRaisesRegex(candidate.Refused, "CONTROLLED_FIRST_SCAN"):
                trial.fork_stage("idle-fixture", 2, lambda end: marker.write_text("unexpected"))
            self.assertFalse(marker.exists())
            self.assertEqual(len(trial.direct_children), 1)
            self.assertTrue(trial.pidfds)
            trial.cleaning = True
            trial.cleanup_deadline = time.monotonic() + 2
            trial.cleanup_processes()
            self.assertFalse(Path(f"/proc/{trial.direct_children[0]}").exists())
            self.assertEqual(trial.record["first_failure"], "CONTROLLED_FIRST_SCAN")
        finally:
            for fd in trial.pidfds.values():
                os.close(fd)

    @unittest.skipUnless(hasattr(os, "pidfd_open"), "Linux owned-child fixture")
    def test_cleanup_first_scan_failure_still_terminates_owned_popen_child(self):
        trial = self.trial()
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        trial.direct_children.append(child.pid)
        try:
            trial.register_child(child.pid)
            actual = runtime.process_snapshot
            calls = [0]
            def first_failure():
                calls[0] += 1
                if calls[0] == 1:
                    raise candidate.Refused("CONTROLLED_FIRST_SCAN")
                return actual()
            trial.cleaning = True
            trial.cleanup_deadline = time.monotonic() + 2
            with patch.object(runtime, "process_snapshot", side_effect=first_failure), \
                    self.assertRaisesRegex(candidate.Refused, "CONTROLLED_FIRST_SCAN"):
                trial.cleanup_processes()
            self.assertFalse(Path(f"/proc/{child.pid}").exists())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            for fd in trial.pidfds.values():
                os.close(fd)


class GuestEvidenceTests(Fixture):
    def complete_qualification(self):
        trial = self.trial()
        now = time.monotonic_ns()
        control = {**trial.control, "stage": "qualification", "stage_started_ns": now - 3_000_000_000}
        phases = guest.phase_record(control, "qualification")
        phases["phases"][0]["finished_ns"] = now - 2_000_000_000
        for name, seconds, start, finish in (("probes", 120, now - 2_000_000_000, now - 1_000_000_000),
                                             ("repeat", 60, now - 1_000_000_000, now - 100_000_000)):
            phases["phases"].append({"name": name, "started_ns": start, "deadline_ns": start + seconds * 1_000_000_000,
                                     "finished_ns": finish})
        trial.journal["containers"].append({"stage": "qualification", "control": control})
        value = {"schema": "zpm-native-alpine-guest/v1", "attempt": trial.owner, "stage": "qualification",
                 "status": "PASS", "first_failure": None,
                 "checks": {name: "NOT_RUN" if name in ("apk_transaction", "installed_apk_inventory") else "PASS"
                            for name in guest.GUEST_CHECKS},
                 "probe_ids_passed": list(guest.PROBE_IDS),
                 "interpreter": {"implementation": "cpython", "version": "3.12.16", "cache_tag": "controlled",
                                 "abi": "controlled", "platform": "controlled"},
                 "authority": {"uid": os.getuid(), "gid": os.getgid(), "no_new_privileges": True,
                               "network_interfaces": ["lo"], "separate_network_and_pid_namespaces": True,
                               "read_only_root": True, "capabilities_empty": True},
                 "installed_distribution_counts": {"zpm": 4, "openjules": 83, "polyphony": 70},
                 "elapsed_ms": {"initial_bootstrap": 1, **{name: 1 for name in guest.PROBE_IDS}}, "phases": phases}
        runtime.atomic_private(trial.root / "work/qualification-phase.json", runtime.encoded(phases))
        runtime.atomic_private(trial.root / "work/qualification-result.json", runtime.encoded(value))
        return trial, value

    def test_complete_bound_native_record_is_required_before_positive_checks_copy(self):
        trial, value = self.complete_qualification()
        runtime.native_guest_result(trial, "qualification")
        self.assertEqual(trial.record["native_checks"]["repeat_bytes"], "PASS")
        self.assertFalse(trial.record["native_execution_qualified"])
        for key, changed in (("probe_ids_passed", list(guest.PROBE_IDS[:-1])),
                             ("installed_distribution_counts", {"zpm": 4, "openjules": 82, "polyphony": 70}),
                             ("first_failure", "INCONSISTENT_SUCCESS"),
                             ("authority", {**value["authority"], "read_only_root": False})):
            trial.record["native_checks"] = {name: "NOT_RUN" for name in candidate.NATIVE_CHECKS}
            altered = {**value, key: changed}
            runtime.atomic_private(trial.root / "work/qualification-result.json", runtime.encoded(altered))
            with self.subTest(field=key), self.assertRaises(candidate.Refused):
                runtime.native_guest_result(trial, "qualification")
            self.assertEqual(trial.record["native_checks"]["repeat_bytes"], "NOT_RUN")

    def test_failed_record_cannot_smuggle_positive_checks_without_authority(self):
        trial, value = self.complete_qualification()
        value.update(status="FAIL", first_failure="CONTROLLED_FAILURE", authority=None)
        runtime.atomic_private(trial.root / "work/qualification-result.json", runtime.encoded(value))
        with self.assertRaisesRegex(candidate.Refused, "NATIVE_GUEST_EVIDENCE"):
            runtime.native_guest_result(trial, "qualification")
        self.assertEqual(trial.record["native_checks"]["interpreter_identity"], "NOT_RUN")

    def test_guest_report_custody_and_late_phase_refuse(self):
        trial, value = self.complete_qualification()
        path = trial.root / "work/qualification-result.json"
        path.chmod(0o644)
        with self.assertRaisesRegex(candidate.Refused, "GUEST_RECEIPT_CUSTODY"):
            runtime.native_guest_result(trial, "qualification")
        path.chmod(0o600)
        with patch.object(runtime.time, "monotonic_ns", return_value=value["phases"]["phases"][-1]["deadline_ns"]), \
                self.assertRaisesRegex(guest.GuestRefused, "GUEST_PHASE_DEADLINE"):
            runtime.native_guest_result(trial, "qualification")
        self.assertEqual(trial.record["native_checks"]["repeat_bytes"], "NOT_RUN")


class DeadlineAndOutputTests(Fixture):
    def budget(self, value=0):
        path = self.root / "output-budget.bin"
        path.write_bytes(value.to_bytes(8, "big"))
        path.chmod(0o600)
        return path

    def test_all_writers_use_one_aggregate_and_partial_overflow_is_retained(self):
        path = self.budget(guest.OUTPUT_LIMIT - 5)
        deadline = time.monotonic() + 2
        guest.charge_output(path, 3, deadline, os.getuid())
        with self.assertRaisesRegex(guest.GuestRefused, "AGGREGATE_OUTPUT_BOUND"):
            runtime.guest.charge_output(path, 3, deadline, os.getuid())
        self.assertEqual(guest.output_total(path, deadline, os.getuid()), guest.OUTPUT_LIMIT + 1)

    def test_output_counter_custody_and_symlink_changes_refuse(self):
        path = self.budget()
        path.chmod(0o644)
        with self.assertRaisesRegex(guest.GuestRefused, "OUTPUT_COUNTER_IDENTITY"):
            guest.charge_output(path, 1, time.monotonic() + 1, os.getuid())
        path.unlink()
        path.symlink_to("unread-target")
        with self.assertRaises(OSError):
            guest.output_total(path, time.monotonic() + 1, os.getuid())

    def control(self):
        start = time.monotonic_ns() - 1_000_000
        return {"attempt": "fixed", "uid": os.getuid(), "gid": os.getgid(),
                "work_deadline_ns": start + 1000 * 1_000_000_000, "stage_started_ns": start}

    def test_host_phase_guard_refuses_late_reset_and_unbounded_repeat(self):
        control = self.control()
        value = guest.phase_record(control, "qualification")
        now = time.monotonic_ns()
        guest.validate_phases(value, control, "qualification", now_ns=now, running=True)
        changed = copy.deepcopy(value)
        changed["phases"][0]["deadline_ns"] += 1
        with self.assertRaisesRegex(guest.GuestRefused, "PHASE_BOUND"):
            guest.validate_phases(changed, control, "qualification", now_ns=now, running=True)
        with self.assertRaisesRegex(guest.GuestRefused, "GUEST_PHASE_DEADLINE"):
            guest.validate_phases(value, control, "qualification", now_ns=value["phases"][0]["deadline_ns"], running=True)
        changed = copy.deepcopy(value)
        changed["phases"][0]["started_ns"] += 1
        changed["phases"][0]["deadline_ns"] += 1
        with self.assertRaises(guest.GuestRefused):
            guest.validate_phases(changed, control, "qualification", now_ns=now, running=True, previous=value)

    def test_all_three_guest_phases_keep_their_original_deadlines(self):
        control = self.control()
        control["stage_started_ns"] = 10
        control["work_deadline_ns"] = 1100 * 1_000_000_000
        value = guest.phase_record(control, "qualification")
        first = value["phases"][0]
        first["finished_ns"] = 800 * 1_000_000_000
        value["phases"].append({"name": "probes", "started_ns": first["finished_ns"],
                                "deadline_ns": 920 * 1_000_000_000, "finished_ns": 900 * 1_000_000_000})
        value["phases"].append({"name": "repeat", "started_ns": 900 * 1_000_000_000,
                                "deadline_ns": 960 * 1_000_000_000, "finished_ns": None})
        guest.validate_phases(value, control, "qualification", now_ns=959 * 1_000_000_000, running=True)
        with self.assertRaisesRegex(guest.GuestRefused, "GUEST_PHASE_DEADLINE"):
            guest.validate_phases(value, control, "qualification", now_ns=960 * 1_000_000_000, running=True)
        value["phases"][-1]["deadline_ns"] = 1100 * 1_000_000_000
        with self.assertRaisesRegex(guest.GuestRefused, "PHASE_BOUND"):
            guest.validate_phases(value, control, "qualification", now_ns=959 * 1_000_000_000, running=True)

    @unittest.skipUnless(hasattr(signal, "setitimer"), "Unix deadline fixture")
    def test_blocking_synchronous_observation_has_an_absolute_deadline(self):
        before = time.monotonic()
        with self.assertRaisesRegex(candidate.Refused, "CONTROLLED_DEADLINE"):
            with runtime.absolute_timeout(before + .03, "CONTROLLED_DEADLINE"):
                time.sleep(1)
        self.assertLess(time.monotonic() - before, .5)

    def test_root_assembly_write_transfers_only_exact_file_to_invoking_owner(self):
        path = self.root / "assembly-result.json"
        calls = []
        with patch.object(guest.os, "geteuid", return_value=0), patch.object(guest.os, "fchown", side_effect=lambda *args: calls.append(args)):
            guest.write(path, {"fixture": True}, (1001, 1001))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], (1001, 1001))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_bytes()), {"fixture": True})


class TransportAndReportTests(Fixture):
    @staticmethod
    def response(body=b"abcde", **headers):
        class Response(io.BytesIO):
            status = 200
            response_header_bytes = 80
        result = Response(body)
        result.headers = Message()
        for key, value in {"Content-Length": str(len(body)), **headers}.items():
            result.headers[key] = value
        return result

    def acquisition(self, opener):
        directory = self.root / "inputs"
        directory.mkdir()
        return runtime.Acquisition(directory, time.monotonic() + 3, opener=opener)

    def test_partial_body_is_charged_and_persisted_before_identity_failure(self):
        directory = self.root / "inputs"
        directory.mkdir()
        acquisition = runtime.Acquisition(directory, time.monotonic() + 2, opener=object())
        class Response(io.BytesIO):
            headers = Message()
        Response.headers["Content-Length"] = "5"
        response = Response(b"abc")
        with self.assertRaisesRegex(candidate.Refused, "HTTP_BODY_INCOMPLETE"):
            acquisition.receive(response, 5, directory / "partial")
        self.assertEqual(acquisition.record["body_bytes"], 3)
        self.assertEqual(json.loads((self.root / "acquisition.json").read_bytes())["body_bytes"], 3)
        self.assertEqual((directory / "partial").read_bytes(), b"abc")

    def test_headers_are_bounded_before_accumulation_including_interim_responses(self):
        class Connection:
            def __init__(self, raw):
                self.stream = io.BytesIO(raw)
            def makefile(self, mode):
                return self.stream
        raw = b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"
        response = runtime.BoundedHTTPResponse(Connection(raw))
        response.begin()
        self.assertEqual(response.response_header_bytes, len(raw) - 3)
        self.assertEqual(response.read(), b"abc")
        response.close()
        excessive = b"HTTP/1.1 200 OK\r\n" + b"X-Fixture: " + b"x" * 1024 + b"\r\n"
        excessive += (b"X-Fixture: " + b"x" * 1024 + b"\r\n") * 90 + b"\r\n"
        response = runtime.BoundedHTTPResponse(Connection(excessive))
        with self.assertRaises(runtime.HeaderBound) as failure:
            response.begin()
        self.assertEqual(failure.exception.observed, candidate.CAPS["request_metadata_reserved_bytes"] + 1)
        response.close()

    def test_tls_construction_uses_the_public_explicit_verifying_context(self):
        handler = runtime.BoundedHTTPSHandler()
        self.assertEqual(handler.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handler.context.check_hostname)
        with patch.object(handler, "do_open", return_value="controlled") as opening:
            self.assertEqual(handler.https_open(object()), "controlled")
        self.assertIs(opening.call_args.args[0], runtime.BoundedHTTPSConnection)
        self.assertEqual(opening.call_args.kwargs, {"context": handler.context})

    def test_one_registry_redirect_strips_authority_and_reserves_both_exchanges(self):
        origin = runtime.REGISTRY + "sha256:" + "a" * 64
        destination = "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/aa/data?opaque=do-not-retain"
        requests = []
        class Opener:
            def open(inner, request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    headers = Message()
                    headers["Location"] = destination
                    raise urllib.error.HTTPError(origin, 307, "controlled redirect", headers, self.response(b""))
                return self.response()
        acquisition = self.acquisition(Opener())
        response = acquisition.open(origin, 5, "private-token", True)
        self.assertEqual(acquisition.receive(response, 5)[0], 5)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer private-token")
        self.assertIsNone(requests[1].get_header("Authorization"))
        self.assertIsNone(requests[1].get_header("Cookie"))
        self.assertEqual(acquisition.record["reserved_body_bytes"], 10)
        self.assertEqual(acquisition.record["metadata_reserved_bytes"], 2 * 65536)
        self.assertEqual(acquisition.record["redirects"], 1)
        self.assertNotIn("private-token", json.dumps(acquisition.record))
        self.assertNotIn("do-not-retain", json.dumps(acquisition.record))

    def test_second_redirect_refuses_without_a_third_request(self):
        origin = runtime.REGISTRY + "sha256:" + "a" * 64
        target = "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/aa/data"
        requests = []
        class Opener:
            def open(inner, request, timeout):
                requests.append(request)
                headers = Message()
                headers["Location"] = target
                raise urllib.error.HTTPError(request.full_url, 302, "controlled redirect", headers, self.response(b""))
        acquisition = self.acquisition(Opener())
        with self.assertRaisesRegex(candidate.Refused, "ARTIFACT_HTTP_REFUSED"):
            acquisition.open(origin, 5, "private-token", True)
        self.assertEqual(len(requests), 2)
        self.assertIsNone(requests[1].get_header("Authorization"))

    def test_unexpected_response_encoding_or_framing_refuses_before_body(self):
        acquisition = self.acquisition(object())
        for key, value in (("Content-Encoding", "gzip"), ("Transfer-Encoding", "chunked")):
            response = self.response(**{key: value})
            response.read = Mock(side_effect=AssertionError("must not read body"))
            acquisition.opener = Mock(open=Mock(return_value=response))
            with self.subTest(header=key), self.assertRaises(candidate.Refused):
                acquisition.open("https://files.pythonhosted.org/controlled", 5)
            self.assertTrue(response.closed)

    def test_repeated_content_length_and_changed_artifact_length_refuse(self):
        acquisition = self.acquisition(object())
        for duplicate in (False, True):
            response = self.response()
            if duplicate:
                response.headers["Content-Length"] = "5"
            response.read = Mock(side_effect=AssertionError("must not read body"))
            with self.subTest(duplicate=duplicate), self.assertRaises(candidate.Refused):
                acquisition.receive(response, 6, acquisition.directory / "never-created")
            self.assertFalse((acquisition.directory / "never-created").exists())

    def test_acquisition_observation_keeps_offending_counters_but_refuses_rewind(self):
        trial = self.trial()
        row = {"requests": 1, "body_bytes": 6, "metadata_reserved_bytes": 65536, "reserved_body_bytes": 5,
               "response_header_bytes": 80, "artifacts_complete": 0, "redirects": 0,
               "origins": ["files.pythonhosted.org"]}
        runtime.atomic_private(trial.root / "acquisition.json", runtime.encoded(row))
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_OBSERVED_BOUND"):
            trial.refresh_acquisition()
        self.assertEqual(trial.record["acquisition"]["body_bytes"], 6)
        row["body_bytes"] = 5
        runtime.atomic_private(trial.root / "acquisition.json", runtime.encoded(row))
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_RECORD_REWIND"):
            trial.refresh_acquisition()

    def test_report_fallback_changes_native_verdict_and_keeps_original_failure(self):
        trial = self.trial()
        trial.record["native_execution_qualified"] = True
        trial.record["status"] = "PASS"
        trial.record["first_failure"] = "ORIGINAL_FAILURE"
        trial.record["private_large_fixture"] = "x" * candidate.CAPS["report_bytes"]
        value = json.loads(runtime.report_bytes(trial.record))
        self.assertEqual(value["first_failure"], "ORIGINAL_FAILURE")
        self.assertFalse(value["native_execution_qualified"])
        self.assertFalse(trial.record["native_execution_qualified"])
        self.assertEqual(trial.record["status"], "FAILED")

    def test_public_projection_drops_private_fields_at_both_levels(self):
        trial = self.trial()
        trial.record["private_selector"] = "must-not-export"
        trial.record["environment"]["private_token"] = "must-not-export"
        trial.record["resources"]["private_path"] = "must-not-export"
        self.assertNotIn("must-not-export", json.dumps(runtime.public_summary(trial.record)))

    def test_final_resource_failure_cannot_leave_native_pass(self):
        trial = self.trial()
        trial.cleanup = Mock()
        trial.refresh_acquisition = Mock()
        trial.record["native_checks"] = {name: "PASS" for name in candidate.NATIVE_CHECKS}
        trial.sample = Mock(side_effect=candidate.Refused("FINAL_SAMPLE_MISSING"))
        self.assertEqual(runtime.finish_trial(trial), 1)
        summary = json.loads((trial.root / "summary.json").read_bytes())
        self.assertEqual(summary["first_failure"], "FINAL_SAMPLE_MISSING")
        self.assertFalse(summary["native_execution_qualified"])

    def test_cleanup_failure_preserves_first_workload_failure(self):
        trial = self.trial()
        trial.fail("ORIGINAL_WORKLOAD_FAILURE")
        trial.cleanup = Mock(side_effect=candidate.Refused("CLEANUP_UNCONFIRMED"))
        trial.refresh_acquisition = Mock()
        trial.sample = Mock()
        self.assertEqual(runtime.finish_trial(trial), 1)
        summary = json.loads((trial.root / "summary.json").read_bytes())
        self.assertEqual(summary["first_failure"], "ORIGINAL_WORKLOAD_FAILURE")
        self.assertIn("CLEANUP_UNCONFIRMED", summary["cleanup_failures"])
        self.assertEqual(summary["native_checks"]["owned_cleanup"], "FAIL")

    def test_final_summary_write_failure_cannot_return_success(self):
        trial = self.trial()
        trial.cleanup = Mock()
        trial.refresh_acquisition = Mock()
        trial.sample = Mock()
        trial.record["native_checks"] = {name: "PASS" for name in candidate.NATIVE_CHECKS}
        original = runtime.atomic_private
        def refuse_summary(path, raw):
            if path.name == "summary.json":
                raise PermissionError("controlled final publication refusal")
            original(path, raw)
        with patch.object(runtime, "atomic_private", side_effect=refuse_summary):
            self.assertEqual(runtime.finish_trial(trial), 1)
        self.assertFalse(trial.record["native_execution_qualified"])
        self.assertEqual(trial.record["status"], "FAILED")
        self.assertFalse((trial.root / "summary.json").exists())

    def test_final_report_overrun_is_failed_and_retains_the_observed_elapsed_time(self):
        trial = self.trial()
        trial.started = 100
        trial.observation_deadline = Mock(return_value=1900)
        trial.cleanup = Mock()
        trial.refresh_acquisition = Mock()
        trial.sample = Mock()
        trial.record["native_checks"] = {name: "PASS" for name in candidate.NATIVE_CHECKS}
        clock, summaries = [100], [0]
        original = runtime.atomic_private
        def slow_final_write(path, raw):
            original(path, raw)
            if path.name == "summary.json":
                summaries[0] += 1
                if summaries[0] == 2:
                    clock[0] = 1901
        with patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(runtime, "atomic_private", side_effect=slow_final_write):
            self.assertEqual(runtime.finish_trial(trial), 1)
        summary = json.loads((trial.root / "summary.json").read_bytes())
        self.assertEqual(summary["first_failure"], "FINAL_REPORT_DEADLINE")
        self.assertEqual(summary["elapsed_ms"], 1801000)
        self.assertFalse(summary["native_execution_qualified"])


if __name__ == "__main__":
    unittest.main()
