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
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
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
                               "CapAdd": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER"] if stage == "assembly" else None,
                               "SecurityOpt": ["no-new-privileges"],
                               "LogConfig": {"Type": "none", "Config": {}}, "IpcMode": "private", "Runtime": "runc",
                               "AutoRemove": False, "PublishAllPorts": False,
                               "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
                "Mounts": [{"Source": source, "Destination": destination, "RW": writable,
                            "Type": "bind", "Propagation": "rprivate"} for source, destination, writable in row["mounts"]]}


class LiveRecordSnapshotTests(Fixture):
    """Real local inode replacement, with controlled observation boundaries."""

    def read(self, name="acquisition.json", deadline=None):
        return runtime.read_live_record(self.root, name, time.monotonic() + 10 if deadline is None else deadline)

    def publish(self, raw=b'{"version":1}\n', name="acquisition.json"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        runtime.atomic_private(path, raw)
        return path

    def test_real_atomic_replace_retires_open_inode_without_changing_its_snapshot(self):
        old, new = b'{"version":1}\n', b'{"version":22}\n'
        path = self.publish(old)
        real_read = os.read
        observations = []
        def replace_then_read(fd, limit):
            before = os.fstat(fd)
            time.sleep(.001)
            runtime.atomic_private(path, new)
            after = os.fstat(fd)
            observations.append((before, after, path.stat(), fd, limit))
            return real_read(fd, limit)
        with patch.object(runtime.os, "read", side_effect=replace_then_read) as reader:
            self.assertEqual(self.read(), old)
        reader.assert_called_once()
        before, after, successor, fd, limit = observations[0]
        self.assertEqual((before.st_nlink, after.st_nlink), (1, 0))
        self.assertNotEqual(before.st_ctime_ns, after.st_ctime_ns)
        self.assertEqual((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
        self.assertNotEqual(before.st_ino, successor.st_ino)
        self.assertEqual(limit, 65537)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(path.read_bytes(), new)
        self.assertEqual(self.read(), new)

    def test_immutable_reader_still_refuses_the_same_real_retirement(self):
        path = self.publish()
        real_stat = os.fstat
        calls = []
        def replace_after_stat(fd):
            info = real_stat(fd)
            if not calls:
                calls.append(fd)
                time.sleep(.001)
                runtime.atomic_private(path, b'{"version":2}\n')
            return info
        with patch.object(candidate.os, "fstat", side_effect=replace_after_stat), \
                self.assertRaisesRegex(candidate.Refused, "INPUT_CHANGED"):
            candidate.read_bounded(path, 65536)

    def test_replacement_after_open_before_first_stat_keeps_already_retired_inode(self):
        old = b'{"version":1}\n'
        path = self.publish(old)
        real_open = os.open
        retired = []
        def opened_before_replacement(name, flags, *args, **kwargs):
            fd = real_open(name, flags, *args, **kwargs)
            if name == path:
                runtime.atomic_private(path, b'{"version":2}\n')
                retired.append(os.fstat(fd))
            return fd
        real_read = os.read
        def read_retired(fd, limit):
            value = real_read(fd, limit)
            retired.append(os.fstat(fd))
            return value
        with patch.object(runtime.os, "open", side_effect=opened_before_replacement), \
                patch.object(runtime.os, "read", side_effect=read_retired):
            self.assertEqual(self.read(), old)
        self.assertEqual([info.st_nlink for info in retired], [0, 0])
        self.assertEqual(retired[0].st_ctime_ns, retired[1].st_ctime_ns)

    def test_closed_paths_and_exact_existing_bounds(self):
        with patch.object(runtime.os, "open", side_effect=AssertionError("no path admission")):
            for name in ("other.json", "work/qualification-result.json", "../acquisition.json", "/acquisition.json", True):
                with self.subTest(name=name), self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_PATH"):
                    self.read(name)
        for name, limit in runtime.LIVE_RECORD_LIMITS.items():
            with self.subTest(name=name):
                raw = b"x" * limit
                self.publish(raw, name)
                self.assertEqual(self.read(name), raw)
                self.publish(raw + b"x", name)
                with patch.object(runtime.os, "read", side_effect=AssertionError("no oversized read")), \
                        self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_BOUND"):
                    self.read(name)
                self.publish(b"", name)
                with self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_BOUND"):
                    self.read(name)

    def test_nonordinary_symlink_hardlink_and_public_mode_refuse_before_read(self):
        path = self.root / "acquisition.json"
        other = self.root / "other"
        other.write_bytes(b"private bytes")
        path.symlink_to(other)
        with self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_UNAVAILABLE"):
            self.read()
        path.unlink()
        for kind in ("directory", "fifo", "hardlink", "mode"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    path.mkdir(mode=0o600)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    self.publish()
                    if kind == "hardlink":
                        os.link(path, self.root / "extra-link")
                    else:
                        path.chmod(0o644)
                with patch.object(runtime.os, "read", side_effect=AssertionError("no unowned read")), \
                        self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_CUSTODY"):
                    self.read()
                if kind == "directory":
                    path.rmdir()
                else:
                    path.unlink()

    def test_in_place_mutation_and_metadata_changes_cannot_use_retirement_exception(self):
        real_read = os.read
        for change in ("same_size", "truncate", "restored_mtime", "mode", "temporary_hardlink"):
            path = self.publish(b'{"version":1}\n')
            original = path.stat()
            def mutate_then_read(fd, limit):
                time.sleep(.001)
                if change in ("same_size", "truncate", "restored_mtime"):
                    with path.open("r+b") as stream:
                        stream.write(b'{"version":2}\n')
                        if change == "truncate":
                            stream.truncate(5)
                    if change == "restored_mtime":
                        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
                elif change == "mode":
                    path.chmod(0o400)
                else:
                    extra = self.root / "temporary-link"
                    os.link(path, extra)
                    extra.unlink()
                return real_read(fd, limit)
            with self.subTest(change=change), patch.object(runtime.os, "read", side_effect=mutate_then_read), \
                    self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_CHANGED"):
                self.read()

    def test_owner_and_group_are_required_before_read(self):
        self.publish()
        real_stat = os.fstat
        for field in ("st_uid", "st_gid"):
            def wrong_owner(fd):
                info = real_stat(fd)
                value = SimpleNamespace(**{name: getattr(info, name) for name in dir(info) if name.startswith("st_")})
                setattr(value, field, getattr(value, field) + 1)
                return value
            with self.subTest(field=field), patch.object(runtime.os, "fstat", side_effect=wrong_owner), \
                    patch.object(runtime.os, "read", side_effect=AssertionError("no wrong-owner read")), \
                    self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_CUSTODY"):
                self.read()

    def test_all_retained_descriptor_identity_fields_must_survive_a_retirement(self):
        path = self.publish()
        real_stat, real_read = os.fstat, os.read
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode", "st_uid", "st_gid", "st_nlink", "st_ctime_ns"):
            self.publish()
            calls = []
            def changed_after(fd):
                info = real_stat(fd)
                calls.append(info)
                if len(calls) == 1:
                    return info
                value = SimpleNamespace(**{name: getattr(info, name) for name in dir(info) if name.startswith("st_")})
                setattr(value, field, calls[0].st_ctime_ns - 1 if field == "st_ctime_ns" else
                        2 if field == "st_nlink" else getattr(value, field) + 1)
                return value
            def replace_then_read(fd, limit):
                runtime.atomic_private(path, b'{"version":2}\n')
                return real_read(fd, limit)
            with self.subTest(field=field), patch.object(runtime.os, "fstat", side_effect=changed_after), \
                    patch.object(runtime.os, "read", side_effect=replace_then_read), \
                    self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_CHANGED"):
                self.read()

    def test_short_read_is_not_retried_and_descriptor_is_closed(self):
        self.publish()
        seen = []
        def short(fd, size):
            seen.append(fd)
            return b"{"
        with patch.object(runtime.os, "read", side_effect=short) as reader, \
                self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_CHANGED"):
            self.read()
        reader.assert_called_once()
        with self.assertRaises(OSError):
            os.fstat(seen[0])

    def test_original_deadline_bounds_open_and_late_read_without_retry(self):
        self.publish()
        with patch.object(runtime.time, "monotonic", return_value=10), \
                patch.object(runtime.os, "open", side_effect=AssertionError("expired before open")), \
                self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_DEADLINE"):
            self.read(deadline=10)
        with patch.object(runtime.time, "monotonic", side_effect=[0, 0, 2]), \
                patch.object(runtime.os, "read", wraps=os.read) as reader, \
                self.assertRaisesRegex(candidate.Refused, "LIVE_RECORD_DEADLINE"):
            self.read(deadline=1)
        reader.assert_called_once()

    def test_acquisition_caller_keeps_complete_old_snapshot_then_monotone_successor(self):
        trial = self.trial()
        publisher = runtime.Acquisition(trial.root / "inputs", trial.work_deadline, opener=object())
        publisher.record.update(requests=1, body_bytes=3, metadata_reserved_bytes=65536, reserved_body_bytes=10,
                                response_header_bytes=80, origins=["files.pythonhosted.org"])
        publisher.persist()
        real_read = os.read
        def replace_then_read(fd, limit):
            publisher.record["body_bytes"] = 5
            publisher.persist()
            return real_read(fd, limit)
        with patch.object(runtime.os, "read", side_effect=replace_then_read):
            trial.refresh_acquisition()
        self.assertEqual(trial.record["acquisition"]["body_bytes"], 3)
        trial.refresh_acquisition()
        self.assertEqual(trial.record["acquisition"]["body_bytes"], 5)
        publisher.record["body_bytes"] = 3
        publisher.persist()
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_RECORD_REWIND"):
            trial.refresh_acquisition()

    def test_both_guest_phase_publishers_preserve_owner_and_monotone_validation(self):
        trial = self.trial()
        real_read = os.read
        for stage in ("assembly", "qualification"):
            control = {**trial.control, "stage": stage, "stage_started_ns": time.monotonic_ns()}
            row = {"stage": stage, "control": control}
            phases = guest.phase_record(control, stage)
            path = trial.root / "work" / (stage + "-phase.json")
            runtime.atomic_private(path, guest.encoded(phases))
            def replace_then_read(fd, limit):
                phases["phases"][0]["finished_ns"] = time.monotonic_ns()
                guest.write(path, phases, (os.getuid(), os.getgid()))
                return real_read(fd, limit)
            with self.subTest(stage=stage), patch.object(runtime.os, "read", side_effect=replace_then_read):
                previous = trial.observe_guest_phase(row, running=True)
            self.assertIsNone(previous["phases"][0]["finished_ns"])
            self.assertIsNotNone(trial.observe_guest_phase(row, running=True)["phases"][0]["finished_ns"])
            info = path.stat()
            self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, info.st_nlink),
                             (0o600, os.getuid(), os.getgid(), 1))
            phases["phases"][0]["finished_ns"] = None
            guest.write(path, phases, (os.getuid(), os.getgid()))
            with self.assertRaisesRegex(guest.GuestRefused, "PHASE_REWIND"):
                trial.observe_guest_phase(row, running=True)


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

    def checkout_metadata(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        (metadata / "config").write_text(
            '[core]\n repositoryformatversion=0\n filemode=true\n bare=false\n'
            '[remote "origin"]\n url=https://github.com/fixture/source\n'
            ' fetch=+refs/heads/*:refs/remotes/origin/*\n[gc]\n auto=0\n')
        body = b'[core]\n\tsparseCheckout = false\n\tsparseCheckoutCone = false\n[index]\n\tsparse = false\n'
        (metadata / "config.worktree").write_bytes(body)
        return metadata, body

    def test_disabled_checkout_worktree_file_is_read_only_and_needs_no_git(self):
        metadata, body = self.checkout_metadata()
        main = (metadata / "config").read_bytes()
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")):
            runtime.source_git_metadata(self.root, "fixture/source")
        self.assertEqual((metadata / "config.worktree").read_bytes(), body)
        self.assertEqual((metadata / "config").read_bytes(), main)

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/bin/git").is_file(),
                         "The supported checkout reproduction requires native Linux Git")
    def test_real_checkout_sparse_disable_leaves_the_admitted_inactive_file(self):
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(self.root),
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_TERMINAL_PROMPT": "0"}
        for arguments in (["init", str(self.root)],
                          ["remote", "add", "origin", "https://github.com/fixture/source"],
                          ["config", "--local", "gc.auto", "0"],
                          ["sparse-checkout", "disable"],
                          ["config", "--local", "--unset-all", "extensions.worktreeConfig"]):
            subprocess.run(["/usr/bin/git", *arguments], cwd=self.root, env=environment,
                           check=True, timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        metadata = self.root / ".git"
        self.assertTrue((metadata / "config.worktree").is_file())
        self.assertNotIn("worktreeconfig", (metadata / "config").read_text().lower())
        with patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("Admission cannot run Git")):
            runtime.source_git_metadata(self.root, "fixture/source")

    def test_worktree_file_accepts_only_the_disabled_sparse_shape(self):
        metadata, body = self.checkout_metadata()
        changes = [body.replace(b"sparseCheckout = false", b"sparseCheckout = true"),
                   body.replace(b"sparseCheckoutCone = false", b"sparseCheckoutCone = true"),
                   body.replace(b"sparse = false", b"sparse = true"),
                   body.replace(b"false", b"0"), body.replace(b"false", b"False"),
                   body + b'[include]\n path=/not-read\n',
                   body + b'[DEFAULT]\n hidden=false\n',
                   body + b'[core]\n worktree=/not-used\n',
                   body.replace(b'[index]', b'[index "hidden"]'),
                   body.replace(b'\tsparse = false', b'\tsparse = false\n\tsparse = false'),
                   body.replace(b'\tsparseCheckoutCone = false\n', b''),
                   b'', b'\xff', b' ' * 65537]
        for value in changes:
            (metadata / "config.worktree").write_bytes(value)
            with self.subTest(value=value[:100]), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_WORKTREE_CONFIG"):
                runtime.source_git_metadata(self.root, "fixture/source")

    def test_inactive_worktree_file_cannot_be_a_link_fifo_or_directory(self):
        metadata, body = self.checkout_metadata()
        path = metadata / "config.worktree"
        target = self.root / "ordinary"
        target.write_bytes(body)
        opened = set()
        actual_open, actual_close = os.open, os.close
        def observe_open(value, *arguments, **keywords):
            descriptor = actual_open(value, *arguments, **keywords)
            if Path(value) == path:
                opened.add(descriptor)
            return descriptor
        def observe_close(descriptor):
            opened.discard(descriptor)
            return actual_close(descriptor)
        for kind in ("symlink", "dangling", "hardlink", "fifo", "directory"):
            path.unlink()
            if kind == "symlink":
                path.symlink_to(target)
            elif kind == "dangling":
                path.symlink_to(self.root / "absent")
            elif kind == "hardlink":
                os.link(target, path)
            elif kind == "fifo":
                os.mkfifo(path)
            else:
                path.mkdir()
            with self.subTest(kind=kind), patch.object(runtime.os, "open", side_effect=observe_open), \
                 patch.object(runtime.os, "close", side_effect=observe_close), \
                 patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_WORKTREE_CONFIG"):
                runtime.source_git_metadata(self.root, "fixture/source")
            self.assertEqual(opened, set(), "Refused metadata must leave no open descriptor")
            if kind == "directory":
                path.rmdir()
            else:
                path.unlink()
            path.write_bytes(body)

    def test_inactive_file_does_not_admit_extensions_or_other_git_indirection(self):
        metadata, body = self.checkout_metadata()
        main_path = metadata / "config"
        main = main_path.read_bytes()
        for value in (b"true", b"false"):
            main_path.write_bytes(main + b'[extensions]\n worktreeConfig=' + value + b'\n')
            with self.subTest(extension=value), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_CONFIG"):
                runtime.source_git_metadata(self.root, "fixture/source")
        main_path.write_bytes(main)
        for relative in ("objects/info/alternates", "info/grafts", "commondir"):
            path = metadata / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("/not-read")
            with self.subTest(relative=relative), patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("No Git")), \
                 self.assertRaisesRegex(candidate.Refused, "SOURCE_GIT_INDIRECTION"):
                runtime.source_git_metadata(self.root, "fixture/source")
            path.unlink()

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


class CapabilityInspectionTests(Fixture):
    """Controlled Docker 28 API data, never a native capability qualification."""

    def check(self, trial, row, info):
        runtime.assert_container(info, "d" * 64, row["name"], row["image"], row["stage"], trial.owner,
                                 row["mounts"], os.getuid(), os.getgid(), runtime.expected_environment(self.documents))

    def row(self, trial, stage="assembly"):
        return {"stage": stage, "name": trial.owner + "-" + stage, "image": "sha256:" + "c" * 64, "mounts": []}

    def test_canonical_inspect_names_preserve_the_exact_original_cli_authority(self):
        trial = self.trial()
        row = self.row(trial)
        info = self.container(trial, row)
        self.check(trial, row, info)
        self.assertEqual(candidate.AUTHORITY["assembly"]["cap_add"], ["CHOWN", "DAC_OVERRIDE", "FOWNER"])
        self.assertEqual(info["HostConfig"]["CapAdd"], ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER"])
        for value in (None, []):
            row = self.row(trial, "qualification")
            info = self.container(trial, row)
            info["HostConfig"]["CapAdd"] = value
            self.check(trial, row, info)

    def test_added_missing_duplicate_alias_or_reordered_capabilities_refuse(self):
        trial = self.trial()
        row = self.row(trial)
        expected = ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER"]
        for value in (None, [], False, 0, "", {}, (), candidate.AUTHORITY["assembly"]["cap_add"],
                      expected[:-1], expected + ["CAP_NET_ADMIN"], expected + [expected[0]],
                      list(reversed(expected)), ["cap_chown", *expected[1:]],
                      ["CAP_CHOWN ", *expected[1:]], [True, *expected[1:]]):
            info = self.container(trial, row)
            info["HostConfig"]["CapAdd"] = value
            with self.subTest(value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_CAPABILITIES"):
                self.check(trial, row, info)
        for value in (None, [], ["all"], ["CAP_ALL"], ["ALL", "ALL"], ["ALL", "CAP_NET_ADMIN"], False):
            info = self.container(trial, row)
            info["HostConfig"]["CapDrop"] = value
            with self.subTest(drop=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_CAPABILITIES"):
                self.check(trial, row, info)

    def test_empty_qualification_does_not_accept_falsey_aliases_or_missing_field(self):
        trial = self.trial()
        row = self.row(trial, "qualification")
        for value in (False, 0, "", {}, (), ["CAP_CHOWN"]):
            info = self.container(trial, row)
            info["HostConfig"]["CapAdd"] = value
            with self.subTest(value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_CAPABILITIES"):
                self.check(trial, row, info)
        for stage in ("assembly", "qualification"):
            row = self.row(trial, stage)
            info = self.container(trial, row)
            del info["HostConfig"]["CapAdd"]
            with self.subTest(stage=stage), self.assertRaisesRegex(candidate.Refused, "CONTAINER_CAPABILITIES"):
                self.check(trial, row, info)

    def test_created_canonical_container_is_observed_before_cli_reply_without_early_confirmation(self):
        trial = self.trial()
        state, commands = {}, []
        def observed(path, **kwargs):
            return copy.deepcopy(state.get("container"))
        def create(command, **kwargs):
            commands.append(command)
            row = trial.journal["containers"][-1]
            state["container"] = self.container(trial, row)
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            self.assertEqual(row["id"], "d" * 64)
            self.assertFalse(row["mutation_confirmed"])
            self.assertEqual(row["state"], "creating")
            return ("d" * 64 + "\n").encode()
        trial.run = create
        trial.sample = Mock(side_effect=trial.observe_mutations)
        with patch.object(runtime, "docker_json", side_effect=observed):
            self.assertEqual(trial.create("sha256:" + "c" * 64, "assembly"), "d" * 64)
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual([command[i+1] for i, item in enumerate(command) if item == "--cap-add"],
                         ["CHOWN", "DAC_OVERRIDE", "FOWNER"])
        self.assertEqual(command[command.index("--cap-drop")+1], "ALL")
        self.assertNotIn("start", command)
        self.assertTrue(trial.journal["containers"][0]["mutation_confirmed"])

    def test_lost_create_reply_retains_unknown_outcome_after_owned_absence(self):
        trial = self.trial()
        state = {"container": None}
        def observed(path, **kwargs):
            return copy.deepcopy(state["container"])
        def lost_reply(command, **kwargs):
            row = trial.journal["containers"][-1]
            state["container"] = self.container(trial, row)
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            raise candidate.Refused("CONTROLLED_LOST_CLI_REPLY")
        trial.run = lost_reply
        trial.sample = Mock(side_effect=trial.observe_mutations)
        with patch.object(runtime, "docker_json", side_effect=observed), \
                self.assertRaisesRegex(candidate.Refused, "CONTROLLED_LOST_CLI_REPLY"):
            trial.create("sha256:" + "c" * 64, "assembly")
        trial.fail("CONTROLLED_LOST_CLI_REPLY")
        row = trial.journal["containers"][0]
        self.assertFalse(row["mutation_confirmed"])
        def remove(command, **kwargs):
            self.assertEqual(command[-3:], ["rm", "--force", row["id"]])
            self.assertFalse(row["removed"])
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
            state["container"] = None
            return b"owned removal acknowledgement"
        trial.run = remove
        trial.cleanup_processes = Mock()
        with patch.object(runtime, "docker_json", side_effect=observed):
            trial.cleanup()
            self.assertEqual(trial.observe_mutations(), 0)
        self.assertTrue(row["removed"])
        self.assertFalse(row["mutation_confirmed"])
        self.assertEqual(trial.record["first_failure"], "CONTROLLED_LOST_CLI_REPLY")
        self.assertIn("CONTAINER_MUTATION_OUTCOME_UNKNOWN", trial.record["cleanup_failures"])
        self.assertEqual(trial.record["native_checks"]["owned_cleanup"], "FAIL")
        self.assertFalse(trial.record["native_execution_qualified"])


class ContainerStatsTests(Fixture):
    """Pinned one-shot response shapes and controlled full-ID request chronology."""

    def stats(self):
        return {"memory_stats": {"usage": 4096, "limit": candidate.CAPS["container_memory_bytes"]},
                "pids_stats": {"current": 2}, "cpu_stats": {"cpu_usage": {"total_usage": 123456}}}

    def running(self):
        trial = self.trial()
        row = {"stage": "assembly", "name": trial.owner + "-assembly", "image": "sha256:" + "c" * 64,
               "mounts": [], "id": "d" * 64, "removed": False, "state": "present", "mutation_confirmed": True,
               "reserved_bytes": runtime.MUTATION_RESERVATIONS["container_writable_bytes"]}
        trial.journal["containers"].append(row)
        trial.observe_guest_phase = Mock()
        info = self.container(trial, row)
        info["State"] = {"Running": True, "Status": "running"}
        return trial, row, info

    def test_successful_one_shot_omits_identity_without_synthesizing_it(self):
        stats = self.stats()
        original = copy.deepcopy(stats)
        self.assertEqual(runtime.assert_stats(stats, "d" * 64, True),
                         {"memory": 4096, "pids": 2, "cpu": 123456})
        self.assertEqual(stats, original)
        for addition in ({"id": "d" * 64}, {"name": "/owned"}, {"id": "d" * 64, "name": "/owned"}):
            with self.subTest(addition=addition):
                self.assertEqual(runtime.assert_stats({**stats, **addition}, "d" * 64, True, name="owned"),
                                 {"memory": 4096, "pids": 2, "cpu": 123456})

    def test_supplied_empty_null_wrong_or_unbound_identity_is_not_omission(self):
        for key, values in (("id", ("", None, False, "d" * 63, "e" * 64)),
                            ("name", ("", None, False, "owned", "/different"))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_IDENTITY"):
                    runtime.assert_stats({**self.stats(), key: value}, "d" * 64, True, name="owned")
        with self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_IDENTITY"):
            runtime.assert_stats({**self.stats(), "name": "/owned"}, "d" * 64, True)

    def test_every_required_counter_is_observed_without_zero_inference(self):
        paths = (("memory_stats", "usage"), ("memory_stats", "limit"), ("pids_stats", "current"),
                 ("cpu_stats", "cpu_usage", "total_usage"))
        for path in paths:
            stats = self.stats()
            parent = stats
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]
            with self.subTest(path=path), self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_MISSING"):
                runtime.assert_stats(stats, "d" * 64, True)
        with self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_MISSING"):
            runtime.assert_stats({}, "d" * 64, True)

    def test_counter_and_parent_types_refuse_boolean_null_and_negative_aliases(self):
        for key, value in (("memory_stats", None), ("memory_stats", []), ("pids_stats", False),
                           ("cpu_stats", {"cpu_usage": None})):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_TYPE"):
                runtime.assert_stats({**self.stats(), key: value}, "d" * 64, True)
        for value in (None, True, False, -1, 1.0, "1"):
            stats = self.stats()
            stats["cpu_stats"]["cpu_usage"]["total_usage"] = value
            with self.subTest(value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_TYPE"):
                runtime.assert_stats(stats, "d" * 64, True)

    def test_original_memory_limit_and_pid_caps_remain_enforced(self):
        for group, key, value in (("memory_stats", "usage", candidate.CAPS["container_memory_bytes"] + 1),
                                  ("memory_stats", "limit", candidate.CAPS["container_memory_bytes"] - 1),
                                  ("memory_stats", "limit", candidate.CAPS["container_memory_bytes"] + 1),
                                  ("pids_stats", "current", candidate.CAPS["container_pids"] + 1)):
            stats = self.stats()
            stats[group][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(candidate.Refused, "CONTAINER_RESOURCE_LIMIT"):
                runtime.assert_stats(stats, "d" * 64, True)

    def test_missing_counter_cannot_hide_a_present_invalid_or_over_limit_value(self):
        for value, code in ((True, "CONTAINER_STATS_TYPE"),
                            (candidate.CAPS["container_pids"] + 1, "CONTAINER_RESOURCE_LIMIT")):
            stats = self.stats()
            del stats["memory_stats"]["usage"]
            stats["pids_stats"]["current"] = value
            with self.subTest(value=value), self.assertRaisesRegex(candidate.Refused, code):
                runtime.assert_stats(stats, "d" * 64, True)

    def test_full_owned_inspection_precedes_one_fixed_id_request_and_updates_counters(self):
        trial, row, before = self.running()
        with patch.object(runtime, "docker_json", side_effect=[before, self.stats()]) as read:
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
        self.assertEqual([call.args[0] for call in read.call_args_list],
                         ["/containers/" + row["id"] + "/json?size=true",
                          "/containers/" + row["id"] + "/stats?stream=false&one-shot=true"])
        self.assertTrue(all(call.kwargs["deadline"] == trial.work_deadline for call in read.call_args_list))
        trial.observe_guest_phase.assert_called_once_with(row, running=True)
        resource = trial.record["resources"]
        self.assertEqual((resource["peak_container_memory_bytes"], resource["peak_container_pids"],
                          resource["last_container_cpu_ns"]), (4096, 2, 123456))

    def test_wrong_pre_inspect_owner_refuses_before_any_stats_request(self):
        trial, row, before = self.running()
        before["Config"]["Labels"][runtime.LABEL] = "foreign-owner"
        with patch.object(runtime, "docker_json", return_value=before) as read, self.assertRaises(candidate.Refused):
            trial.observe_mutations()
        read.assert_called_once()
        trial.observe_guest_phase.assert_not_called()

    def test_identity_type_and_limit_refusals_are_not_replaced_or_suppressed_after_exit(self):
        trial, row, before = self.running()
        for kind, code in (("identity", "CONTAINER_STATS_IDENTITY"), ("type", "CONTAINER_STATS_TYPE"),
                           ("limit", "CONTAINER_RESOURCE_LIMIT")):
            stats = self.stats()
            if kind == "identity":
                stats["id"] = "e" * 64
            elif kind == "type":
                stats["pids_stats"]["current"] = True
            else:
                stats["memory_stats"]["usage"] = candidate.CAPS["container_memory_bytes"] + 1
            # No third call is permitted: a later exit cannot erase this refusal.
            with self.subTest(kind=kind), patch.object(runtime, "docker_json", side_effect=[before, stats]) as read, \
                    self.assertRaisesRegex(candidate.Refused, code):
                trial.observe_mutations()
            self.assertEqual(read.call_count, 2)

    def test_only_missing_counters_can_join_exact_owned_exit_without_a_zero_sample(self):
        trial, row, before = self.running()
        after = self.container(trial, row)
        after["State"] = {"Running": False, "Status": "exited"}
        resource = trial.record["resources"]
        resource.update(peak_container_memory_bytes=9999, peak_container_pids=3, last_container_cpu_ns=999)
        with patch.object(runtime, "docker_json", side_effect=[before, {"id": row["id"], "name": "/" + row["name"]}, after]) as read:
            self.assertEqual(trial.observe_mutations(), row["reserved_bytes"])
        self.assertEqual(read.call_args.args, ("/containers/" + row["id"] + "/json",))
        self.assertEqual(read.call_args.kwargs, {"deadline": trial.work_deadline})
        self.assertEqual((resource["peak_container_memory_bytes"], resource["peak_container_pids"],
                          resource["last_container_cpu_ns"]), (9999, 3, 999))
        self.assertFalse(row["removed"])

    def test_missing_sample_still_refuses_live_wrong_or_unowned_after_inspection(self):
        trial, row, before = self.running()
        for kind in ("live", "created", "id", "name", "owner", "capability"):
            after = self.container(trial, row)
            after["State"] = {"Running": False, "Status": "exited"}
            if kind == "live":
                after["State"] = {"Running": True, "Status": "running"}
            elif kind == "created":
                after["State"]["Status"] = "created"
            elif kind == "id":
                after["Id"] = "e" * 64
            elif kind == "name":
                after["Name"] = "/foreign"
            elif kind == "owner":
                after["Config"]["Labels"][runtime.LABEL] = "foreign"
            else:
                after["HostConfig"]["CapAdd"].append("CAP_NET_ADMIN")
            with self.subTest(kind=kind), patch.object(runtime, "docker_json", side_effect=[before, {}, after]), \
                    self.assertRaises(candidate.Refused):
                trial.observe_mutations()

    def test_engine_version_is_checked_before_daemon_info_or_candidate_sources(self):
        trial = self.trial()
        def run(command, **kwargs):
            if command[-1] == "HEAD":
                return ("b" * 40 + "\n").encode()
            if "status" in command or "ls-tree" in command:
                return b""
            if "version" in command and "--format" in command:
                return json.dumps(versions).encode()
            return b"controlled tool identity\n"
        trial.run = run
        trial.check_sources, trial.sample = Mock(return_value=0), Mock()
        versions = {"Server": {"Version": "28.0.4", "MinAPIVersion": "1.24", "ApiVersion": "1.48"},
                    "Client": {"Version": "28.0.4", "ApiVersion": "1.48"}}
        daemon = {"OSType": "linux", "Architecture": "amd64", "MemoryLimit": True,
                  "SwapLimit": True, "PidsLimit": True, "DefaultRuntime": "runc"}
        with patch.object(runtime.platform, "system", return_value="Linux"), \
                patch.object(runtime.platform, "machine", return_value="x86_64"), \
                patch.object(runtime.sys, "version_info", (3, 12)), \
                patch.object(runtime.os, "getuid", return_value=1001), patch.object(runtime.os, "getgid", return_value=1001), \
                patch.object(runtime.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
                patch.object(runtime, "available_memory", return_value=candidate.CAPS["minimum_host_available_bytes"]), \
                patch.object(runtime, "source_git_metadata"), patch.object(runtime, "source_tree_records", return_value=[]), \
                patch.object(runtime, "verify_source_files", return_value=0), \
                patch.object(runtime, "docker_json", return_value=daemon) as read:
            trial.prerequisites("b" * 40)
            read.assert_called_once_with("/info", deadline=trial.work_deadline)
            trial.check_sources.assert_called_once()
            read.reset_mock()
            trial.check_sources.reset_mock()
            for version in ("28.0.5", "29.0.0", "28.0.4+unreviewed"):
                versions["Server"]["Version"] = version
                with self.subTest(version=version), self.assertRaisesRegex(candidate.Refused, "CONTAINER_STATS_ENGINE_VERSION"):
                    trial.prerequisites("b" * 40)
            read.assert_not_called()
            trial.check_sources.assert_not_called()


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
    def wire_response(body, headers=(("Transfer-Encoding", "chunked"),), version="HTTP/1.1"):
        class Connection:
            def __init__(self, raw):
                self.stream = io.BytesIO(raw)
            def makefile(self, mode):
                return self.stream
        raw = (version + " 200 OK\r\n" + "".join(key + ": " + value + "\r\n" for key, value in headers)
               + "\r\n").encode("ascii") + body
        response = runtime.BoundedHTTPResponse(Connection(raw))
        response.begin()
        return response

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

    def test_exact_token_endpoint_reads_bounded_original_chunks_and_counts_framing(self):
        wire = b"2\r\nab\r\n3\r\ncde\r\n0\r\n\r\n"
        response = self.wire_response(wire)
        header_bytes = response.response_header_bytes
        response.read = Mock(side_effect=AssertionError("permissive decoder must not run"))
        acquisition = self.acquisition(Mock(open=Mock(return_value=response)))
        opened = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        count, sha, body = acquisition.receive(opened, candidate.CAPS["metadata_response_bytes"])
        self.assertEqual((count, body), (5, b"abcde"))
        self.assertEqual(sha, hashlib.sha256(body).hexdigest())
        self.assertEqual(acquisition.record["response_header_bytes"], header_bytes)
        self.assertEqual(acquisition.record["response_framing_bytes"], len(wire) - len(body))
        self.assertEqual(acquisition.record["requests"], 1)
        self.assertEqual(acquisition.record["body_bytes"], 5)
        self.assertTrue(response.closed)
        self.assertNotIn("abcde", (self.root / "acquisition.json").read_text())

    def test_token_framing_has_no_mixed_duplicate_compressed_trailer_or_old_http_mode(self):
        acquisition = self.acquisition(object())
        variants = [
            ((("Transfer-Encoding", "chunked"), ("Content-Length", "5")), "HTTP/1.1"),
            ((("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "chunked")), "HTTP/1.1"),
            ((("Transfer-Encoding", "gzip"),), "HTTP/1.1"),
            ((("Transfer-Encoding", "chunked, gzip"),), "HTTP/1.1"),
            ((("Transfer-Encoding", "chunked"), ("Trailer", "X-Unselected")), "HTTP/1.1"),
            ((("Transfer-Encoding", "chunked"),), "HTTP/1.0"),
            ((("Transfer-Encoding", "chunked"),), "HTTP/1.7"),
        ]
        for headers, version in variants:
            with self.subTest(headers=headers, version=version):
                response = self.wire_response(b"5\r\nabcde\r\n0\r\n\r\n", headers, version)
                response.fp.read = Mock(side_effect=AssertionError("must refuse before body"))
                acquisition.opener = Mock(open=Mock(return_value=response))
                with self.assertRaises(candidate.Refused):
                    acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
                self.assertTrue(response.closed)
                self.assertEqual(acquisition.record["body_bytes"], 0)

    def test_chunked_token_is_not_an_artifact_or_a_different_endpoint(self):
        acquisition = self.acquisition(object())
        for url, maximum in ((runtime.TOKEN_URL + "&extra=1", candidate.CAPS["metadata_response_bytes"]),
                             (runtime.TOKEN_URL, 5), ("https://files.pythonhosted.org/fixture", 5)):
            response = self.wire_response(b"5\r\nabcde\r\n0\r\n\r\n")
            acquisition.opener = Mock(open=Mock(return_value=response))
            with self.subTest(url=url, maximum=maximum), self.assertRaisesRegex(candidate.Refused, "HTTP_FRAMING"):
                acquisition.open(url, maximum)
            self.assertTrue(response.closed)
        response = self.wire_response(b"5\r\nabcde\r\n0\r\n\r\n")
        acquisition.opener = Mock(open=Mock(return_value=response))
        response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        destination = acquisition.directory / "must-not-exist"
        with self.assertRaisesRegex(candidate.Refused, "HTTP_TOKEN_FRAMING"):
            acquisition.receive(response, candidate.CAPS["metadata_response_bytes"], destination)
        self.assertFalse(destination.exists())

    def test_token_chunks_refuse_extensions_bad_sizes_delimiters_trailers_and_truncation(self):
        acquisition = self.acquisition(object())
        variants = [(b"1;name=value\r\nx\r\n0\r\n\r\n", 0), (b"-1\r\n", 0),
                    (b"+1\r\n", 0), (b"00000000000000001\r\nx\r\n0\r\n\r\n", 0),
                    (b"1\nx\n0\n\n", 0), (b"3\r\nabcXX", 3), (b"5\r\nabc", 3),
                    (b"1\r\nx\r\n0\r\n", 1), (b"1\r\nx\r\n0\r\nX-Unknown: x\r\n\r\n", 1),
                    (b"0\r\n\r\n", 0)]
        for wire, observed in variants:
            with self.subTest(wire=wire):
                before = acquisition.record["body_bytes"]
                response = self.wire_response(wire)
                acquisition.opener = Mock(open=Mock(return_value=response))
                response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
                with self.assertRaises(candidate.Refused):
                    acquisition.receive(response, candidate.CAPS["metadata_response_bytes"])
                self.assertTrue(response.closed)
                self.assertEqual(acquisition.record["body_bytes"] - before, observed)

    def test_token_declared_chunk_overflow_refuses_before_payload_read(self):
        response = self.wire_response(b"100001\r\n")
        response.fp.read = Mock(side_effect=AssertionError("oversize chunk must not be read"))
        acquisition = self.acquisition(Mock(open=Mock(return_value=response)))
        response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_BYTES"):
            acquisition.receive(response, candidate.CAPS["metadata_response_bytes"])
        self.assertEqual(acquisition.record["body_bytes"], 0)
        self.assertEqual(acquisition.record["response_framing_bytes"], 8)
        self.assertTrue(response.closed)

    def test_token_headers_and_framing_share_the_existing_metadata_reservation(self):
        header_base = len(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-Pad: \r\n\r\n")
        target_header = candidate.CAPS["request_metadata_reserved_bytes"] - 6
        response = self.wire_response(b"1\r\nx\r\n0\r\n\r\n", (("Transfer-Encoding", "chunked"),
                                      ("X-Pad", "x" * (target_header - header_base))))
        self.assertEqual(response.response_header_bytes, target_header)
        acquisition = self.acquisition(Mock(open=Mock(return_value=response)))
        response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        with self.assertRaisesRegex(candidate.Refused, "HTTP_METADATA_BOUND"):
            acquisition.receive(response, candidate.CAPS["metadata_response_bytes"])
        self.assertEqual(acquisition.record["body_bytes"], 1)
        self.assertEqual(acquisition.record["response_framing_bytes"], 7)
        self.assertEqual(acquisition.record["metadata_reserved_bytes"], 65536)
        self.assertEqual(json.loads((self.root / "acquisition.json").read_bytes())["response_framing_bytes"], 7)

    def test_token_partial_payload_is_retained_before_the_original_deadline_stops_next_read(self):
        response = self.wire_response(b"3\r\nabc\r\n0\r\n\r\n")
        acquisition = self.acquisition(Mock(open=Mock(return_value=response)))
        response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        original_read = response.fp.read
        calls = []
        def delayed_read(size):
            calls.append(size)
            value = original_read(size)
            acquisition.deadline = time.monotonic() - 1
            return value
        response.fp.read = delayed_read
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_DEADLINE"):
            acquisition.receive(response, candidate.CAPS["metadata_response_bytes"])
        self.assertEqual(calls, [3])
        self.assertEqual(acquisition.record["body_bytes"], 3)
        self.assertEqual(acquisition.record["response_framing_bytes"], 3)
        self.assertTrue(response.closed)

    def test_token_final_delimiter_after_deadline_is_counted_but_never_completes(self):
        response = self.wire_response(b"1\r\nx\r\n0\r\n\r\n")
        acquisition = self.acquisition(Mock(open=Mock(return_value=response)))
        response = acquisition.open(runtime.TOKEN_URL, candidate.CAPS["metadata_response_bytes"])
        original_readline = response.fp.readline
        calls = []
        def delayed_final_line(size):
            value = original_readline(size)
            calls.append(value)
            if value == b"\r\n":
                acquisition.deadline = time.monotonic() - 1
            return value
        response.fp.readline = delayed_final_line
        with self.assertRaisesRegex(candidate.Refused, "ACQUISITION_DEADLINE"):
            acquisition.receive(response, candidate.CAPS["metadata_response_bytes"])
        self.assertEqual(calls, [b"1\r\n", b"0\r\n", b"\r\n"])
        self.assertEqual(acquisition.record["body_bytes"], 1)
        self.assertEqual(acquisition.record["response_framing_bytes"], 10)
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
               "response_header_bytes": 80, "response_framing_bytes": 0, "artifacts_complete": 0, "redirects": 0,
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


class OciRedirectDiagnosticTests(Fixture):
    """Original refusal custody, using in-memory transport and fixed metadata."""

    origin = runtime.REGISTRY + "sha256:" + "a" * 64
    target = "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/aa/data"
    response = staticmethod(TransportAndReportTests.response)

    def refused_target(self, target=None, original=None):
        with self.assertRaisesRegex(candidate.Refused, "^OCI_REDIRECT_REFUSED$") as caught:
            runtime.redirect_url(target or self.target, original or self.origin)
        return caught.exception

    def transport_refusal(self, locations):
        requests = []
        response = self.response(b"")
        response.read = Mock(side_effect=AssertionError("redirect body must not be read"))
        headers = Message()
        for location in locations:
            headers["Location"] = location
        class Opener:
            def open(inner, request, timeout):
                requests.append(request)
                if len(requests) != 1:
                    raise AssertionError("no redirected request is admitted")
                raise urllib.error.HTTPError(request.full_url, 307, "fixed fixture", headers, response)
        directory = self.root / ("inputs-" + str(len(list(self.root.iterdir()))))
        directory.mkdir()
        acquisition = runtime.Acquisition(directory, time.monotonic() + 3, opener=Opener())
        with patch.object(runtime.socket, "create_connection", side_effect=AssertionError("no network")), \
                self.assertRaisesRegex(candidate.Refused, "^OCI_REDIRECT_REFUSED$") as caught:
            acquisition.open(self.origin, 5, "sensitive-token", True)
        self.assertTrue(response.closed)
        response.read.assert_not_called()
        self.assertEqual(len(requests), 1)
        self.assertEqual(acquisition.record, {
            "requests": 1, "body_bytes": 0, "metadata_reserved_bytes": 65536,
            "reserved_body_bytes": 5, "response_header_bytes": 80,
            "response_framing_bytes": 0, "artifacts_complete": 0,
            "redirects": 0, "origins": ["registry-1.docker.io"],
        })
        return caught.exception

    def test_each_existing_target_predicate_has_one_closed_classification(self):
        cases = [
            ("ORIGINAL_REGISTRY", self.target, "https://unapproved.invalid/blobs/"),
            ("HTTPS_SCHEME", self.target.replace("https:", "http:"), self.origin),
            ("HOSTNAME", self.target.replace("production.cloudfront.docker.com", "unapproved.invalid"), self.origin),
            ("PORT", self.target.replace(".com/", ".com:444/"), self.origin),
            ("USERNAME", self.target.replace("https://", "https://sensitive-user@"), self.origin),
            ("PASSWORD", self.target.replace("https://", "https://:sensitive-password@"), self.origin),
            ("FRAGMENT", self.target + "#sensitive-fragment", self.origin),
            ("PATH_PREFIX", "https://production.cloudfront.docker.com/other-path", self.origin),
            ("URL_LENGTH", self.target + "?signed=" + "x" * 16384, self.origin),
            ("BACKSLASH", self.target + "?signed=sensitive\\value", self.origin),
            ("PRINTABLE_ASCII", self.target + "?signed=sensitive value", self.origin),
            ("PRINTABLE_ASCII", self.target + "?signed=sensitive-\u00e9", self.origin),
        ]
        for expected, target, original in cases:
            with self.subTest(predicate=expected):
                error = self.refused_target(target, original)
                self.assertEqual(runtime._oci_redirect_predicate(error), expected)
                raw = runtime.encoded(runtime._child_failure_result("acquisition", error))
                self.assertLess(len(raw), 4096)
                for forbidden in (b"sensitive", b"signed", b"https:", b"unapproved.invalid"):
                    self.assertNotIn(forbidden, raw)

    def test_location_cardinality_preserves_close_and_all_original_accounting(self):
        for locations in ([], [self.target, self.target + "?signed=sensitive"]):
            with self.subTest(count=len(locations)):
                error = self.transport_refusal(locations)
                self.assertEqual(runtime._oci_redirect_predicate(error), "LOCATION_CARDINALITY")

    def test_target_refusal_makes_no_follow_request_or_body_read(self):
        error = self.transport_refusal([self.target.replace(".com/", ".com:444/") + "?signed=sensitive"])
        self.assertEqual(runtime._oci_redirect_predicate(error), "PORT")
        self.assertNotIn("sensitive", json.dumps(runtime._child_failure_result("acquisition", error)))

    def test_allowed_redirect_and_credential_stripping_have_no_diagnostic(self):
        requests = []
        response = self.response()
        class Opener:
            def open(inner, request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    headers = Message()
                    headers["Location"] = self.target + "?signed=sensitive"
                    raise urllib.error.HTTPError(request.full_url, 307, "fixed fixture", headers, self.response(b""))
                return response
        directory = self.root / "inputs"
        directory.mkdir()
        acquisition = runtime.Acquisition(directory, time.monotonic() + 3, opener=Opener())
        opened = acquisition.open(self.origin, 5, "sensitive-token", True)
        self.assertEqual(acquisition.receive(opened, 5)[0], 5)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer sensitive-token")
        self.assertIsNone(requests[1].get_header("Authorization"))
        self.assertIsNone(requests[1].get_header("Cookie"))
        self.assertEqual(acquisition.record["redirects"], 1)
        self.assertEqual(acquisition.record["reserved_body_bytes"], 10)
        self.assertNotIn("oci_redirect_predicate", acquisition.record)
        self.assertNotIn("sensitive", json.dumps(acquisition.record))

    def test_exact_original_assertion_error_is_rethrown(self):
        original_require, errors = runtime.require, []
        def observe(condition, code):
            try:
                return original_require(condition, code)
            except candidate.Refused as error:
                errors.append(error)
                raise
        with patch.object(runtime, "require", side_effect=observe):
            error = self.refused_target(self.target + "#sensitive")
        self.assertIs(error, errors[0])
        self.assertEqual(runtime._oci_redirect_predicate(error), "FRAGMENT")

    def test_parsed_properties_are_read_once_in_original_short_circuit_order(self):
        reads = []
        class Parts:
            @property
            def scheme(inner):
                reads.append("scheme")
                return "https"
            @property
            def hostname(inner):
                reads.append("hostname")
                if reads.count("hostname") > 1:
                    raise AssertionError("second metadata read")
                return "unapproved.invalid"
            @property
            def port(inner):
                raise AssertionError("short-circuited metadata read")
        with patch.object(runtime.urllib.parse, "urlsplit", return_value=Parts()) as parse:
            error = self.refused_target()
        parse.assert_called_once_with(self.target)
        self.assertEqual(reads, ["scheme", "hostname"])
        self.assertEqual(runtime._oci_redirect_predicate(error), "HOSTNAME")

    def test_operand_and_parser_errors_are_not_marked_as_guard_failures(self):
        for boundary in ("parse", "property"):
            error = candidate.Refused("OCI_REDIRECT_REFUSED")
            class Parts:
                @property
                def scheme(inner):
                    raise error
            with self.subTest(boundary=boundary), \
                    patch.object(runtime.urllib.parse, "urlsplit", side_effect=error if boundary == "parse" else None,
                                 return_value=Parts()), self.assertRaises(candidate.Refused) as caught:
                runtime.redirect_url(self.target, self.origin)
            self.assertIs(caught.exception, error)
            self.assertIsNone(runtime._oci_redirect_predicate(error))
            self.assertNotIn("oci_redirect_predicate", runtime._child_failure_result("acquisition", error))

    def test_non_boolean_operand_keeps_original_conversion_and_error(self):
        error, conversions = candidate.Refused("OCI_REDIRECT_REFUSED"), []
        class Operand:
            def __bool__(inner):
                conversions.append(1)
                raise error
        with self.assertRaises(candidate.Refused) as caught:
            runtime._require_oci_redirect(Operand(), "HOSTNAME")
        self.assertIs(caught.exception, error)
        self.assertEqual(conversions, [1])
        self.assertIsNone(runtime._oci_redirect_predicate(error))

    def test_forged_exception_attributes_cannot_create_diagnostic_provenance(self):
        error = candidate.Refused("OCI_REDIRECT_REFUSED")
        error.oci_redirect_predicate = "HOSTNAME"
        error._oci_redirect_predicate = "PORT"
        self.assertEqual(runtime._child_failure_result("acquisition", error),
                         {"status": "FAIL", "failure": "OCI_REDIRECT_REFUSED"})
        trial = self.trial()
        trial.fail("OCI_REDIRECT_REFUSED", failure=error)
        self.assertNotIn("oci_redirect_predicate", trial.record)

    def test_projection_fault_keeps_original_error_and_unavailable_first_slot(self):
        original_require, errors = runtime.require, []
        def observe(condition, code):
            try:
                return original_require(condition, code)
            except candidate.Refused as error:
                errors.append(error)
                raise
        with patch.object(runtime, "require", side_effect=observe), \
                patch.object(runtime, "_safe_oci_redirect_predicate", side_effect=RuntimeError("sensitive projection fault")):
            error = self.refused_target(self.target + "#sensitive")
            child = runtime._child_failure_result("acquisition", error)
        self.assertIs(error, errors[0])
        self.assertEqual(child["oci_redirect_predicate"], "UNAVAILABLE")
        runtime._remember_oci_redirect_failure(error, "FRAGMENT")
        self.assertEqual(runtime._oci_redirect_predicate(error), "UNAVAILABLE")
        self.assertNotIn("sensitive", json.dumps(child))

    def test_serialized_child_custody_freezes_value_before_source_record_mutation(self):
        error = self.refused_target(self.target + "#sensitive")
        wire = runtime.encoded(runtime._child_failure_result("acquisition", error))
        value = candidate.decode(wire)
        with self.assertRaisesRegex(candidate.Refused, "^OCI_REDIRECT_REFUSED$") as caught:
            runtime._check_child_result("acquisition", value, 1 << 8)
        parent_error = caught.exception
        value["oci_redirect_predicate"] = "HOSTNAME"
        runtime._remember_oci_redirect_failure(parent_error, "PORT")
        trial = self.trial()
        trial.fail(runtime.fixed_code(parent_error), failure=parent_error)
        first = runtime.public_summary(trial.record)
        first["oci_redirect_predicate"] = "PASSWORD"
        self.assertEqual(runtime.public_summary(trial.record)["oci_redirect_predicate"], "FRAGMENT")
        self.assertLess(len(runtime.report_bytes(runtime.public_summary(trial.record))), candidate.CAPS["report_bytes"])

    def test_unrelated_stages_and_earlier_failures_do_not_acquire_this_diagnostic(self):
        error = self.refused_target(self.target + "#sensitive")
        self.assertNotIn("oci_redirect_predicate", runtime._child_failure_result("artifact-verification", error))
        for status, failure, stage in (("PASS", "OCI_REDIRECT_REFUSED", "acquisition"),
                                       ("FAIL", "OTHER_FAILURE", "acquisition"),
                                       ("FAIL", "OCI_REDIRECT_REFUSED", "artifact-verification")):
            value = {"status": status, "failure": failure, "oci_redirect_predicate": "HOSTNAME"}
            with self.subTest(status=status, failure=failure, stage=stage), self.assertRaises(candidate.Refused) as caught:
                runtime._check_child_result(stage, value, 1 << 8)
            self.assertIsNone(runtime._oci_redirect_predicate(caught.exception))
        trial = self.trial()
        trial.fail("EARLIER_FAILURE")
        trial.fail("OCI_REDIRECT_REFUSED", failure=error)
        self.assertEqual(trial.record["first_failure"], "EARLIER_FAILURE")
        self.assertNotIn("oci_redirect_predicate", runtime.public_summary(trial.record))

    def test_malformed_or_oversized_child_metadata_is_unavailable_without_coercion(self):
        class Hostile:
            def __str__(inner):
                raise AssertionError("diagnostic coercion")
            def __eq__(inner, other):
                raise AssertionError("diagnostic comparison")
        for predicate in (Hostile(), {"body": "sensitive"}, "sensitive" * 10000, None):
            value = {"status": "FAIL", "failure": "OCI_REDIRECT_REFUSED", "oci_redirect_predicate": predicate}
            with self.assertRaisesRegex(candidate.Refused, "^OCI_REDIRECT_REFUSED$") as caught:
                runtime._check_child_result("acquisition", value, 1 << 8)
            self.assertEqual(runtime._oci_redirect_predicate(caught.exception), "UNAVAILABLE")
            child = runtime._child_failure_result("acquisition", caught.exception)
            self.assertLess(len(runtime.encoded(child)), 4096)
            self.assertNotIn("sensitive", json.dumps(child))

    def test_final_summary_preserves_original_refusal_through_cleanup_failure(self):
        trial = self.trial()
        error = self.refused_target(self.target + "#sensitive")
        trial.fail("OCI_REDIRECT_REFUSED", failure=error)
        trial.cleanup = Mock(side_effect=candidate.Refused("CLEANUP_UNCONFIRMED"))
        trial.refresh_acquisition = Mock()
        trial.sample = Mock()
        self.assertEqual(runtime.finish_trial(trial), 1)
        summary = json.loads((trial.root / "summary.json").read_bytes())
        self.assertEqual(summary["first_failure"], "OCI_REDIRECT_REFUSED")
        self.assertEqual(summary["oci_redirect_predicate"], "FRAGMENT")
        self.assertIn("CLEANUP_UNCONFIRMED", summary["cleanup_failures"])
        self.assertFalse(summary["native_execution_qualified"])
        self.assertNotIn("sensitive", json.dumps(summary))

    def test_public_projection_and_report_overflow_keep_only_closed_bounded_value(self):
        trial = self.trial()
        error = self.refused_target(self.target + "#sensitive")
        trial.fail("OCI_REDIRECT_REFUSED", failure=error)
        trial.record["ignored_private_data"] = "sensitive" * candidate.CAPS["report_bytes"]
        result = json.loads(runtime.report_bytes(trial.record))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["first_failure"], "OCI_REDIRECT_REFUSED")
        self.assertEqual(result["oci_redirect_predicate"], "FRAGMENT")
        self.assertNotIn("sensitive", json.dumps(result))
        trial.record["oci_redirect_predicate"] = {"raw_url": "sensitive"}
        self.assertEqual(runtime.public_summary(trial.record)["oci_redirect_predicate"], "UNAVAILABLE")

    @unittest.skipUnless(hasattr(os, "pidfd_open") and hasattr(os, "fork"), "Linux owned-child fixture")
    def test_original_fork_result_delivers_the_closed_refusal_without_native_transport(self):
        trial = self.trial()
        trial.sample = Mock()
        try:
            with self.assertRaisesRegex(candidate.Refused, "^OCI_REDIRECT_REFUSED$") as caught:
                trial.fork_stage("acquisition", 2,
                                 lambda end: runtime.redirect_url(self.target + "#sensitive", self.origin))
            child = candidate.decode(candidate.read_bounded(trial.root / "acquisition-child.json", 4096))
            self.assertEqual(child, {"status": "FAIL", "failure": "OCI_REDIRECT_REFUSED",
                                     "oci_redirect_predicate": "FRAGMENT"})
            trial.fail(runtime.fixed_code(caught.exception), failure=caught.exception)
            self.assertEqual(runtime.public_summary(trial.record)["oci_redirect_predicate"], "FRAGMENT")
            self.assertFalse(Path(f"/proc/{trial.direct_children[0]}").exists())
        finally:
            for fd in trial.pidfds.values():
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
