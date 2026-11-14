#!/usr/bin/env python3
"""Explicit, reviewed-plan native Alpine qualification. Importing is inert.

This fixture owns no installation authority. It observes the frozen producer
tuple with its existing preparation/bootstrap APIs in one offline container.
The separate candidate compiler remains a source-only command.
"""
from __future__ import annotations

import argparse
import base64
import configparser
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

# The workflow uses the isolated script entry point, not an ambient package.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import check_musl_preparation as musl
from tests.contracts import alpine_sealed_guest as guest

CAPS = candidate.CAPS
require, exact, digest, encoded = candidate.require, candidate.exact, candidate.digest, candidate.encoded
Refused = candidate.Refused
ORIGINAL_PLAN = "c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76"
ACTIVE_PLAN = candidate.ROOT / "active-plan-3.json"
REPO = Path(__file__).resolve().parents[2]
SOCKET = "/var/run/docker.sock"
API = "1.45"
LABEL = "org.zero-point-modulator.native-alpine-attempt"
TOKEN_URL = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"
REGISTRY = "https://registry-1.docker.io/v2/library/python/blobs/"
CODE_PATHS = (
    ".github/workflows/native-alpine-candidate.yml",
    "tests/__init__.py",
    "tests/contracts/check_alpine_sealed_runtime.py",
    "tests/contracts/alpine_sealed_guest.py",
    "tests/contracts/check_alpine_sealed_candidate.py",
    "tests/contracts/check_musl_preparation.py",
    "zpm/dependencies.py",
    "zpm/__init__.py",
    "zpm/installation_context.py",
    "zpm/models.py",
)
FIXED_POLICY = {
    "socket": "unix:///var/run/docker.sock", "engine_api": API,
    "artifact_concurrency": 1, "token_requests": 1, "oci_redirects_per_layer": 1,
    "artifact_retries": 0, "docker_log_driver": "none",
    "storage_observation": "sampled-logical-files-and-conservative-owned-image-sizes",
    "physical_storage_quota": False, "opaque_daemon_caches_observed": False,
    "setup_transport_in_artifact_budget": False,
    "container_network": "none-with-loopback", "native_activation": "explicit-reviewed-plan-only",
    "token_response_framing": "content-length-or-bounded-bare-http11-chunked",
    "artifact_response_framing": "exact-content-length-only",
    "response_metadata_scope": "headers-plus-token-chunk-framing",
}
COMMANDS = {
    "assembly": ["/usr/local/bin/python3.12", "-I", "-B", "/harness/alpine_sealed_guest.py", "assembly"],
    "qualification": ["/usr/local/bin/python3.12", "-I", "-B", "/harness/alpine_sealed_guest.py", "qualification"],
    "apk": ["/sbin/apk", "add", "--no-network", "--no-cache", "--repositories-file", "/dev/null"],
}
CONTAINER_ENV = {"HOME": "/work/home", "TMPDIR": "/work/tmp",
                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
# Prospective logical charges, not new disk quotas. A larger actual native
# size refuses; no unknown mutation is temporarily treated as zero storage.
MUTATION_RESERVATIONS = {"base_image_bytes": 51195904,
                         "container_writable_bytes": 512 * 1024**2,
                         "assembled_image_bytes": 51195904 + 512 * 1024**2}
FIXED_POLICY["mutation_storage_reservations"] = MUTATION_RESERVATIONS
FIXED_POLICY["container_environment_overrides"] = CONTAINER_ENV
FIXED_POLICY["final_report_reservation_bytes"] = 4 * CAPS["report_bytes"]
TRANSIENT_FILES = {name: CAPS["report_bytes"] for name in (
    "private-report.json.new", "ownership.json.new", "summary.json.new",
    "acquisition.json.new", "acquisition-child.json.new", "artifact-verification-child.json.new",
    "work/control.json.new", "work/assembly-phase.json.new", "work/assembly-result.json.new",
    "work/qualification-phase.json.new", "work/qualification-result.json.new",
    "work/dependency-plan.json.new", "work/binding.json.new",
)}
FIXED_POLICY["transient_file_reservations"] = TRANSIENT_FILES
FIXED_POLICY["root_created_temporary_files"] = ["work/assembly-phase.json.new", "work/assembly-result.json.new"]
RUN_PREFIX = "refs/heads/qualification/native-alpine/run/"


def fixed_code(error, fallback="UNEXPECTED_FAILURE"):
    value = str(error) if isinstance(error, (Refused, guest.GuestRefused)) else ""
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) else fallback


def reviewed_identity(explicit, run_ref):
    """A run ref and manual review hash may agree; neither may override drift."""
    require(type(explicit) is str and type(run_ref) is str, "REVIEWED_PLAN_REQUIRED")
    reference = run_ref.removeprefix(RUN_PREFIX) if run_ref.startswith(RUN_PREFIX) else ""
    if run_ref.startswith(RUN_PREFIX):
        require(candidate.HEX.fullmatch(reference), "RUN_REFERENCE_INVALID")
    if explicit:
        require(candidate.HEX.fullmatch(explicit), "REVIEWED_PLAN_REQUIRED")
    require(bool(explicit or reference), "REVIEWED_PLAN_REQUIRED")
    require(not explicit or not reference or explicit == reference, "REVIEW_REFERENCE_DISAGREEMENT")
    return explicit or reference


def admit_plan(path, reviewed_sha256, repo=REPO):
    """Read/hash only. Call before directories, subprocesses, socket or HTTP."""
    require(type(reviewed_sha256) is str and candidate.HEX.fullmatch(reviewed_sha256), "REVIEWED_PLAN_REQUIRED")
    raw = candidate.read_bounded(path, 65536, reviewed_sha256)
    plan = candidate.decode(raw)
    candidate.object_fields(plan, "schema attempt original_plan_sha256 recipe_sha256 caps authority policy commands code", "ACTIVE_PLAN_FIELDS")
    exact([plan["schema"], plan["attempt"]], ["zpm-native-alpine-active-plan/v1", "native-alpine-3"], "ACTIVE_PLAN_IDENTITY")
    exact(plan["original_plan_sha256"], ORIGINAL_PLAN, "ORIGINAL_PLAN")
    exact(plan["recipe_sha256"], candidate.METADATA["inputs-v1.json"][1], "RECIPE_HASH")
    exact(plan["caps"], CAPS, "ACTIVE_CAPS")
    exact(plan["authority"], candidate.AUTHORITY, "ACTIVE_AUTHORITY")
    exact(plan["policy"], FIXED_POLICY, "ACTIVE_POLICY")
    exact(plan["commands"], COMMANDS, "ACTIVE_COMMANDS")
    require(type(plan["code"]) is dict and set(plan["code"]) == set(CODE_PATHS), "ACTIVE_CODE_SET")
    for name, row in plan["code"].items():
        candidate.object_fields(row, "bytes sha256", "ACTIVE_CODE_FIELDS")
        require(candidate.integer(row["bytes"], 0, 262144)
                and type(row["sha256"]) is str and candidate.HEX.fullmatch(row["sha256"]), "ACTIVE_CODE_SIZE")
        content = source_blob(repo / name, maximum=row["bytes"])
        require(len(content) == row["bytes"] and digest(content) == row["sha256"], "ACTIVE_CODE_IDENTITY")
    candidate.load_bundle(candidate.ROOT, ORIGINAL_PLAN)
    documents = {name: candidate.decode(candidate.read_bounded(candidate.ROOT / name, size, sha))
                 for name, (size, sha) in candidate.METADATA.items()}
    return plan, documents


def atomic_private(path, raw):
    require(len(raw) <= CAPS["report_bytes"], "PRIVATE_RECORD_BOUND")
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def report_bytes(report):
    raw = encoded(report) + b"\n"
    if len(raw) > CAPS["report_bytes"]:
        report["status"], report["native_execution_qualified"], report["truncated"] = "FAILED", False, True
        if report.get("first_failure") is None:
            report["first_failure"] = "REPORT_BOUND"
        report = {key: report[key] for key in (
            "schema", "attempt", "plan_sha256", "harness_source_revision", "production_sources",
            "recipe_sha256", "native_checks", "first_failure", "cleanup_failures")}
        report.update(status="FAILED", native_execution_qualified=False, truncated=True,
                      truncation_reason="REPORT_BOUND")
        report["cleanup_failures"] = report["cleanup_failures"][:32]
        raw = encoded(report) + b"\n"
    require(len(raw) <= CAPS["report_bytes"], "MINIMAL_REPORT_BOUND")
    return raw


def public_summary(report):
    """Explicit safe projection. No private paths, logs, selectors or hashes."""
    names = ("schema", "attempt", "plan_sha256", "harness_source_revision", "production_sources",
             "recipe_sha256", "status", "first_failure", "native_checks", "native_execution_qualified",
             "cleanup_failures", "truncated", "elapsed_ms")
    result = {name: report[name] for name in names if name in report}
    groups = {"environment": ("python", "kernel", "machine", "runner_os", "runner_version", "uid", "gid",
                               "engine_api", "git", "openssl", "docker_client", "docker_server", "native_interpreter"),
              "acquisition": ("requests", "body_bytes", "metadata_reserved_bytes", "reserved_body_bytes",
                              "response_header_bytes", "response_framing_bytes", "artifacts_complete", "redirects", "origins"),
              "resources": ("samples", "maximum_sample_gap_ms", "peak_host_rss_bytes", "peak_logical_owned_bytes",
                            "peak_container_memory_bytes", "peak_container_pids", "last_container_cpu_ns",
                            "minimum_host_available_bytes", "storage_scope", "physical_storage_quota",
                            "opaque_daemon_caches_observed", "command_output_bytes", "pending_mutation_reservations")}
    for group, fields in groups.items():
        result[group] = {key: report.get(group, {})[key] for key in fields if key in report.get(group, {})}
    return result


def parse_stat(raw, pid):
    try:
        end = raw.rindex(")")
        require(raw[:raw.index(" (")] == str(pid), "PROCESS_IDENTITY")
        values = raw[end + 2:].split()
        require(len(values) >= 22, "PROCESS_STAT")
        state, ppid, session, start, pages = values[0], int(values[1]), int(values[3]), int(values[19]), int(values[21])
        require(start > 0 and pages >= 0, "PROCESS_STAT")
        return {"pid": pid, "start": start, "ppid": ppid, "session": session, "state": state,
                "rss": pages * os.sysconf("SC_PAGE_SIZE")}
    except (ValueError, IndexError):
        raise Refused("PROCESS_STAT") from None


def process_snapshot(proc=Path("/proc")):
    rows = {}
    try:
        for entry in os.scandir(proc):
            if not entry.name.isdecimal():
                continue
            pid = int(entry.name)
            try:
                raw = (proc / entry.name / "stat").read_text()
            except FileNotFoundError:
                continue
            except OSError:
                raise Refused("PROCESS_STAT_UNREADABLE") from None
            rows[pid] = parse_stat(raw, pid)
    except OSError:
        raise Refused("PROCESS_SURFACE_UNAVAILABLE") from None
    return rows


def reconcile_processes(snapshot, retained, root, sessions=()):
    """Retain observed reparented children; PID reuse is never absence."""
    require(root[0] in snapshot and snapshot[root[0]]["start"] == root[1], "RUNNER_IDENTITY")
    current = {pid: start for pid, start in retained.items() if pid in snapshot}
    for pid, start in current.items():
        require(snapshot[pid]["start"] == start, "OWNED_PID_REUSED")
    current[root[0]] = root[1]
    for pid, row in snapshot.items():
        if row.get("session") in sessions:
            current[pid] = row["start"]
    changed = True
    while changed:
        changed = False
        for pid, row in snapshot.items():
            if row["ppid"] in current and pid not in current:
                current[pid] = row["start"]
                changed = True
    return current, sum(snapshot[pid]["rss"] for pid in current)


def logical_files(roots, *, temporary_root=None):
    """Regular linked bytes only; deliberately not physical/daemon accounting."""
    reservations = ({temporary_root / name: size for name, size in TRANSIENT_FILES.items()}
                    if temporary_root is not None else {})
    seen, total = set(), sum(reservations.values())
    for root in roots:
        require(root.is_dir() and not root.is_symlink(), "STORAGE_ROOT")
        def unavailable(error):
            raise Refused("STORAGE_OBSERVATION") from None
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=unavailable):
            for name in dirs + files:
                path = Path(directory) / name
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    require(path in reservations, "STORAGE_OBSERVATION")
                    continue  # Its whole fixed reservation remains charged.
                except OSError:
                    raise Refused("STORAGE_OBSERVATION") from None
                if path in reservations:
                    permitted_owners = {os.getuid()}
                    if path.relative_to(temporary_root).as_posix() in FIXED_POLICY["root_created_temporary_files"]:
                        permitted_owners.add(0)  # Only until the guest's exact fchown.
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and info.st_uid in permitted_owners and stat.S_IMODE(info.st_mode) == 0o600
                            and info.st_size <= reservations[path], "TRANSIENT_FILE_IDENTITY")
                    continue
                if stat.S_ISREG(info.st_mode):
                    identity = (info.st_dev, info.st_ino)
                    if identity not in seen:
                        seen.add(identity)
                        total += info.st_size
                else:
                    require(stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode), "STORAGE_NONORDINARY")
    return total


def available_memory():
    try:
        rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        parts = rows["MemAvailable"].split()
        require(len(parts) == 2 and parts[1] == "kB" and parts[0].isdecimal(), "AVAILABLE_MEMORY")
        return int(parts[0]) * 1024
    except (OSError, ValueError, KeyError):
        raise Refused("AVAILABLE_MEMORY") from None


def source_tree_records(raw):
    require(raw.endswith(b"\0") and len(raw) <= 1048576, "SOURCE_TREE_BOUND")
    result = []
    for row in raw[:-1].split(b"\0"):
        try:
            metadata, name = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            name = name.decode("utf-8")
        except (ValueError, UnicodeError):
            raise Refused("SOURCE_TREE_RECORD") from None
        candidate.safe_member(name)
        require(mode in {"100644", "100755", "120000", "160000"} and candidate.COMMIT.fullmatch(oid), "SOURCE_TREE_RECORD")
        require(kind == ("commit" if mode == "160000" else "blob"), "SOURCE_TREE_KIND")
        result.append((name, mode, oid))
    require(len({row[0] for row in result}) == len(result), "SOURCE_TREE_DUPLICATE")
    return result


def verify_source_files(root, service, records):
    expected, total = set(), 0
    for name, mode, oid in records:
        path = root / name
        expected.add(name)
        if mode == "160000":
            require(any(row == {"service": service, "path": name, "revision": oid}
                        for row in candidate.SOURCE_EXCEPTIONS["uninitialized_gitlinks"]), "SOURCE_GITLINK")
            require(not path.exists() or (path.is_dir() and not path.is_symlink() and not any(path.iterdir())), "SOURCE_GITLINK_INITIALIZED")
            continue
        if mode == "120000":
            require(path.is_symlink(), "SOURCE_LINK")
            target = os.readlink(path)
            require(any(row == {"service": service, "path": name, "target": target, "blob": oid}
                        for row in candidate.SOURCE_EXCEPTIONS["symlinks"]), "SOURCE_LINK")
            raw = os.fsencode(target)
        else:
            raw = source_blob(path)
            require(bool(os.lstat(path).st_mode & 0o111) == (mode == "100755"), "SOURCE_MODE")
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid, "SOURCE_BLOB")
        total += len(raw)
    def unavailable(error):
        raise Refused("SOURCE_OBSERVATION") from None
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unavailable):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name != ".git"]
        for name in files + [name for name in dirs if (Path(directory) / name).is_symlink()]:
            require((Path(directory) / name).relative_to(root).as_posix() in expected, "SOURCE_UNTRACKED")
    if service in candidate.SOURCE_FACTS:
        require(len(records) == candidate.SOURCE_FACTS[service]["entries"] and total == candidate.SOURCE_FACTS[service]["tracked_bytes"], "SOURCE_TOTALS")
    else:
        require(service == "harness" and 0 < len(records) <= 10000 and total <= CAPS["source_tracked_bytes"], "HARNESS_SOURCE_TOTALS")
    return total


def source_blob(path, *, maximum=CAPS["source_tracked_bytes"]):
    """Tracked empty Git blobs are valid, unlike nonempty metadata documents."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 <= before.st_size <= maximum, "SOURCE_FILE_BOUND")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    finally:
        if fd is not None:
            os.close(fd)
    require(len(raw) == before.st_size and all(getattr(before, name) == getattr(after, name)
            for name in ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")), "SOURCE_CHANGED")
    return raw


def source_git_metadata(root, repository):
    """Admit local Git configuration before any command can read it."""
    metadata = root / ".git"
    require(metadata.is_dir() and not metadata.is_symlink(), "SOURCE_GIT_DIRECTORY")
    config = configparser.RawConfigParser(strict=True)
    try:
        config.read_string(candidate.read_bounded(metadata / "config", 65536).decode("utf-8"))
    except (UnicodeError, configparser.Error):
        raise Refused("SOURCE_GIT_CONFIG") from None
    allowed = {"core": {"repositoryformatversion", "filemode", "bare", "logallrefupdates"},
               'remote "origin"': {"url", "fetch"}}
    require(not config.defaults(), "SOURCE_GIT_CONFIG")
    for section in config.sections():
        if section == "gc":
            # actions/checkout disables automatic garbage collection locally.
            require(dict(config[section]) == {"auto": "0"}, "SOURCE_GIT_CONFIG")
        elif section.startswith('branch "') and section.endswith('"'):
            require(set(config[section]) <= {"remote", "merge"}
                    and config[section].get("remote") == "origin"
                    and re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", config[section].get("merge", "")), "SOURCE_GIT_CONFIG")
        else:
            require(section in allowed and set(config[section]) <= allowed[section], "SOURCE_GIT_CONFIG")
    require(config.has_section("core") and config["core"].get("repositoryformatversion") == "0"
            and config["core"].get("bare") == "false" and config["core"].get("filemode") == "true", "SOURCE_GIT_CONFIG")
    remote = config['remote "origin"'] if config.has_section('remote "origin"') else {}
    require(remote.get("url") in {"https://github.com/" + repository, "https://github.com/" + repository + ".git"}
            and remote.get("fetch") == "+refs/heads/*:refs/remotes/origin/*", "SOURCE_GIT_ORIGIN")
    for relative in ("objects/info/alternates", "info/grafts", "commondir"):
        require(not (metadata / relative).exists() and not (metadata / relative).is_symlink(), "SOURCE_GIT_INDIRECTION")
    # checkout's sparse-checkout disable writes these three false values, then
    # removes extensions.worktreeConfig. The closed main config above excludes
    # that extension, so this exact ordinary file is inactive, not indirection.
    worktree_path = metadata / "config.worktree"
    try:
        os.lstat(worktree_path)
    except FileNotFoundError:
        pass
    else:
        worktree = configparser.RawConfigParser(strict=True)
        try:
            worktree.read_string(source_blob(worktree_path, maximum=65536).decode("utf-8"))
        except (OSError, UnicodeError, configparser.Error, Refused):
            raise Refused("SOURCE_GIT_WORKTREE_CONFIG") from None
        require(not worktree.defaults() and set(worktree.sections()) == {"core", "index"}
                and dict(worktree["core"]) == {"sparsecheckout": "false", "sparsecheckoutcone": "false"}
                and dict(worktree["index"]) == {"sparse": "false"}, "SOURCE_GIT_WORKTREE_CONFIG")
    def unavailable(error):
        raise Refused("SOURCE_GIT_OBSERVATION") from None
    for directory, dirs, files in os.walk(metadata, followlinks=False, onerror=unavailable):
        for name in dirs + files:
            info = os.lstat(Path(directory) / name)
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), "SOURCE_GIT_NONORDINARY")
            if stat.S_ISREG(info.st_mode):
                require(info.st_nlink == 1, "SOURCE_GIT_HARDLINK")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HeaderBound(Refused):
    def __init__(self, observed):
        self.observed = observed
        super().__init__("HTTP_METADATA_BOUND")


class HeaderReader:
    def __init__(self, stream):
        self.stream, self.count, self.active = stream, 0, True

    def readline(self, maximum=-1):
        if not self.active:
            return self.stream.readline(maximum)
        allowance = CAPS["request_metadata_reserved_bytes"] - self.count + 1
        part = self.stream.readline(min(maximum, allowance) if maximum >= 0 else allowance)
        self.count += len(part)
        if self.count > CAPS["request_metadata_reserved_bytes"]:
            raise HeaderBound(self.count)
        return part

    def __getattr__(self, name):
        return getattr(self.stream, name)


class BoundedHTTPResponse(http.client.HTTPResponse):
    def _read_status(self):
        status = super()._read_status()
        self.wire_http_version = status[0]
        return status

    def begin(self):
        reader = HeaderReader(self.fp)
        self.fp = reader
        try:
            super().begin()
        finally:
            reader.active = False
            self.response_header_bytes = reader.count


class BoundedHTTPSConnection(http.client.HTTPSConnection):
    response_class = BoundedHTTPResponse


class BoundedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        self.context = ssl.create_default_context()
        super().__init__(context=self.context)

    def https_open(self, request):
        return self.do_open(BoundedHTTPSConnection, request, context=self.context)


def redirect_url(url, original):
    parts = urllib.parse.urlsplit(url)
    require(original.startswith(REGISTRY) and parts.scheme == "https" and
            parts.hostname == "production.cloudfront.docker.com" and parts.port in (None, 443) and
            not parts.username and not parts.password and not parts.fragment and
            parts.path.startswith("/registry-v2/docker/registry/v2/blobs/sha256/"), "OCI_REDIRECT_REFUSED")
    require(len(url) <= 16384 and "\\" not in url and all(33 <= ord(char) <= 126 for char in url), "OCI_REDIRECT_REFUSED")
    return url


class Acquisition:
    """Used only in an owned subprocess; parent bounds DNS and shutdown."""
    def __init__(self, directory, deadline, opener=None):
        self.directory, self.deadline = directory, deadline
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), BoundedHTTPSHandler())
        self.record = {"requests": 0, "body_bytes": 0, "metadata_reserved_bytes": 0,
                       "reserved_body_bytes": 0, "response_header_bytes": 0, "response_framing_bytes": 0,
                       "artifacts_complete": 0, "redirects": 0, "origins": []}

    def persist(self):
        atomic_private(self.directory.parent / "acquisition.json", encoded(self.record) + b"\n")

    def reserve(self, url, maximum):
        require(time.monotonic() < self.deadline, "ACQUISITION_DEADLINE")
        require(self.record["requests"] < CAPS["acquisition_requests"], "ACQUISITION_REQUESTS")
        require(type(maximum) is int and maximum > 0 and self.record["reserved_body_bytes"]
                + maximum + self.record["metadata_reserved_bytes"] + 65536 <= CAPS["acquisition_bytes"], "ACQUISITION_BYTES")
        self.record["requests"] += 1
        self.record["metadata_reserved_bytes"] += 65536
        self.record["reserved_body_bytes"] += maximum
        origin = urllib.parse.urlsplit(url).hostname
        if origin not in self.record["origins"]:
            self.record["origins"].append(origin)
        self.persist()

    def header_observation(self, response):
        count = getattr(response, "response_header_bytes", None)
        require(type(count) is int and 0 < count <= CAPS["request_metadata_reserved_bytes"], "HTTP_HEADER_OBSERVATION")
        self.record["response_header_bytes"] += count
        self.persist()

    def open(self, url, maximum, token=None, allow_redirect=False):
        self.reserve(url, maximum)
        headers = {"Accept-Encoding": "identity", "User-Agent": "zpm-native-alpine-qualification/1"}
        if token is not None:
            require(url.startswith(REGISTRY) and candidate.HEX.fullmatch(url.removeprefix(REGISTRY).removeprefix("sha256:")), "TOKEN_SCOPE")
            headers["Authorization"] = "Bearer " + token
        try:
            response = self.opener.open(urllib.request.Request(url, headers=headers), timeout=min(10, max(.01, self.deadline - time.monotonic())))
        except HeaderBound as error:
            self.record["response_header_bytes"] += error.observed
            self.persist()
            raise
        except urllib.error.HTTPError as error:
            redirected = error.code in (301, 302, 303, 307, 308) and allow_redirect
            try:
                self.header_observation(error.fp)
                if redirected:
                    locations = error.headers.get_all("Location", [])
                    require(len(locations) == 1, "OCI_REDIRECT_REFUSED")
                    location = redirect_url(locations[0], url)
            finally:
                error.close()
            if redirected:
                self.record["redirects"] += 1
                return self.open(location, maximum, token=None, allow_redirect=False)
            raise Refused("ARTIFACT_HTTP_REFUSED") from None
        try:
            self.header_observation(response)
            require(response.status == 200, "ARTIFACT_HTTP_STATUS")
            require(response.headers.get_all("Content-Encoding", []) in ([], ["identity"]), "HTTP_ENCODING")
            transfers = response.headers.get_all("Transfer-Encoding", [])
            response.zpm_token_chunked = False
            if transfers:
                # The exact anonymous token endpoint alone may have an unknown
                # content length. Artifact bytes retain their original exact
                # Content-Length requirement. Never combine framing authorities.
                require(url == TOKEN_URL and maximum == CAPS["metadata_response_bytes"]
                        and token is None and not allow_redirect, "HTTP_FRAMING")
                require(len(transfers) == 1 and transfers[0].lower() == "chunked"
                        and not response.headers.get_all("Content-Length", [])
                        and not response.headers.get_all("Trailer", []), "HTTP_TOKEN_FRAMING")
                require(isinstance(response, BoundedHTTPResponse) and response.wire_http_version == "HTTP/1.1"
                        and response.version == 11
                        and response.chunked is True and response.fp is not None, "HTTP_TOKEN_READER")
                response.zpm_token_chunked = True
                response.response_framing_bytes = 0
        except BaseException:
            response.close()
            raise
        return response

    def token_framing(self, response, *, line=False):
        """Charge raw framing before parsing, within the original header reserve.

        Read the bounded response's original stream, never HTTPResponse.read's
        permissive chunk-extension/trailer parser. No framing contents survive
        in the credential-free counters or errors.
        """
        require(time.monotonic() < self.deadline, "ACQUISITION_DEADLINE")
        remaining = (CAPS["request_metadata_reserved_bytes"] - response.response_header_bytes
                     - response.response_framing_bytes)
        require(remaining >= 0, "HTTP_METADATA_BOUND")
        part = response.fp.readline(remaining + 1) if line else response.fp.read(min(2, remaining + 1))
        response.response_framing_bytes += len(part)
        self.record["response_framing_bytes"] += len(part)
        self.persist()
        require(time.monotonic() < self.deadline, "ACQUISITION_DEADLINE")
        require(response.response_header_bytes + response.response_framing_bytes
                <= CAPS["request_metadata_reserved_bytes"], "HTTP_METADATA_BOUND")
        require(part.endswith(b"\r\n"), "HTTP_CHUNK_SYNTAX")
        return part

    def token_chunks(self, response, size):
        """One bounded token body: bare sizes, exact CRLF, no extensions/trailers."""
        total = 0
        while True:
            line = self.token_framing(response, line=True)
            require(re.fullmatch(rb"[0-9A-Fa-f]{1,16}\r\n", line), "HTTP_CHUNK_SYNTAX")
            length = int(line[:-2], 16)
            if length == 0:
                require(self.token_framing(response, line=True) == b"\r\n", "HTTP_CHUNK_TRAILER")
                require(total > 0, "HTTP_BODY_INCOMPLETE")
                return
            require(length <= size - total, "ACQUISITION_BYTES")
            while length:
                require(time.monotonic() < self.deadline, "ACQUISITION_DEADLINE")
                chunk = response.fp.read(min(65536, length))
                require(bool(chunk), "HTTP_BODY_INCOMPLETE")
                length -= len(chunk)
                total += len(chunk)
                yield chunk
            require(self.token_framing(response) == b"\r\n", "HTTP_CHUNK_SYNTAX")

    def receive(self, response, size, destination=None):
        count, hasher, chunks, stream = 0, hashlib.sha256(), [], None
        try:
            lengths = response.headers.get_all("Content-Length", [])
            chunked = getattr(response, "zpm_token_chunked", False) is True
            if chunked:
                require(destination is None and not lengths and size == CAPS["metadata_response_bytes"], "HTTP_TOKEN_FRAMING")
                pieces = self.token_chunks(response, size)
            else:
                require(len(lengths) == 1 and lengths[0].isdecimal() and 0 < int(lengths[0]) <= size, "HTTP_CONTENT_LENGTH")
                require(destination is None or int(lengths[0]) == size, "ARTIFACT_DECLARED_LENGTH")
            stream = destination.open("xb") if destination is not None else None
            while True:
                require(time.monotonic() < self.deadline, "ACQUISITION_DEADLINE")
                if chunked:
                    try:
                        chunk = next(pieces)
                    except StopIteration:
                        break
                else:
                    chunk = response.read(min(65536, size - count + 1))
                    if not chunk:
                        break
                count += len(chunk)
                self.record["body_bytes"] += len(chunk)
                self.persist()  # Retain even overflow/partial bodies before refusal.
                require(count <= size and self.record["body_bytes"] + self.record["metadata_reserved_bytes"] <= CAPS["acquisition_bytes"], "ACQUISITION_BYTES")
                hasher.update(chunk)
                if stream is not None:
                    stream.write(chunk)
                else:
                    chunks.append(chunk)
            if stream is not None:
                stream.flush()
                os.fsync(stream.fileno())
            require(chunked or count == int(lengths[0]), "HTTP_BODY_INCOMPLETE")
            return count, hasher.hexdigest(), b"".join(chunks)
        finally:
            response.close()
            if stream is not None:
                stream.close()

    def run(self, recipe):
        _, _, raw = self.receive(self.open(TOKEN_URL, CAPS["metadata_response_bytes"]), CAPS["metadata_response_bytes"])
        value = candidate.decode(raw)
        token = value.get("token") if isinstance(value, dict) else None
        require(type(token) is str and 1 <= len(token) <= 16384 and re.fullmatch(r"[A-Za-z0-9._~-]+", token), "REGISTRY_TOKEN")
        rows = [("layers/" + row["digest"][7:] + ".gz", REGISTRY + row["digest"], row["compressed_bytes"], row["digest"][7:], True)
                for row in recipe["oci"]["layers"]]
        rows += [("apks/" + row["filename"], row["url"], row["size"], row["sha256"], False) for row in recipe["apk_packages"]]
        rows += [("wheels/" + row["filename"], row["url"], row["size"], row["sha256"], False) for row in recipe["wheels"]]
        for relative, url, size, sha, registry in rows:
            received, actual, _ = self.receive(self.open(url, size, token if registry else None, registry), size, self.directory / relative)
            require(received == size and actual == sha, "ACQUIRED_ARTIFACT_IDENTITY")
            self.record["artifacts_complete"] += 1
            self.persist()
        require(self.record["artifacts_complete"] == 173, "ACQUISITION_COUNT")


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)


@contextmanager
def absolute_timeout(deadline, code):
    """Bound synchronous local protocol parsing as well as individual reads."""
    now = time.monotonic()
    require(deadline > now, code)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_end = now + previous_timer[0] if previous_timer[0] > 0 else None
    end = min(deadline, previous_end) if previous_end is not None else deadline
    def expired(signum, frame):
        raise Refused(code)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(.000001, end - now))
    try:
        yield
        require(time.monotonic() < end, code)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_end is not None and previous_end > time.monotonic():
            signal.setitimer(signal.ITIMER_REAL, previous_end - time.monotonic(), previous_timer[1])


def docker_json(path, *, absent=False, deadline=None):
    require(path.startswith("/") and "\r" not in path and "\n" not in path, "DOCKER_PATH")
    seconds = min(1.0, (deadline - time.monotonic()) if deadline is not None else 1.0)
    require(seconds > 0, "DOCKER_DEADLINE")
    connection = UnixHTTP("localhost", timeout=seconds)
    try:
        with absolute_timeout(time.monotonic() + seconds, "DOCKER_DEADLINE"):
            connection.request("GET", "/v" + API + path, headers={"Connection": "close"})
            response = connection.getresponse()
            raw = response.read(1048577)
            require(len(raw) <= 1048576, "DOCKER_RESPONSE_BOUND")
            if response.status == 404 and absent:
                return None
            require(response.status == 200, "DOCKER_RESPONSE_STATUS")
            return candidate.decode(raw)
    except (OSError, http.client.HTTPException):
        raise Refused("DOCKER_OBSERVATION") from None
    finally:
        connection.close()


def assert_stats(stats, identity, running):
    require(type(stats) is dict and stats.get("id") == identity, "CONTAINER_STATS_IDENTITY")
    if not running:
        return None
    try:
        memory = stats["memory_stats"]["usage"]
        pids = stats["pids_stats"]["current"]
        cpu = stats["cpu_stats"]["cpu_usage"]["total_usage"]
        limit = stats["memory_stats"]["limit"]
    except (KeyError, TypeError):
        raise Refused("CONTAINER_STATS_MISSING") from None
    require(all(type(x) is int and x >= 0 for x in (memory, pids, cpu, limit)), "CONTAINER_STATS_TYPE")
    require(limit == CAPS["container_memory_bytes"] and memory <= limit and pids <= CAPS["container_pids"], "CONTAINER_RESOURCE_LIMIT")
    return {"memory": memory, "pids": pids, "cpu": cpu}


def environment_map(values):
    require(type(values) is list and len(values) <= 32, "ENVIRONMENT_FIELDS")
    result = {}
    for item in values:
        require(type(item) is str and len(item) <= 4096 and "=" in item and "\0" not in item, "ENVIRONMENT_VALUE")
        key, value = item.split("=", 1)
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and key not in result, "ENVIRONMENT_DUPLICATE")
        result[key] = value
    return result


def expected_environment(documents):
    return {**environment_map(documents["oci-config.json"]["config"]["Env"]), **CONTAINER_ENV}


def assert_execution_config(config, command, user, environment, owner):
    require(type(config) is dict, "CONTAINER_CONFIG")
    exact(config.get("Cmd"), command, "CONTAINER_COMMAND")
    require(config.get("Entrypoint") in (None, []) and config.get("Volumes") in (None, {})
            and config.get("Healthcheck") in (None, {}) and config.get("OnBuild") in (None, [])
            and config.get("ExposedPorts") in (None, {}) and config.get("StopSignal") in (None, ""), "CONTAINER_CONFIG")
    exact(environment_map(config.get("Env")), environment, "CONTAINER_ENVIRONMENT")
    require(config.get("User") == user and config.get("WorkingDir") == "/work", "CONTAINER_USER_OR_WORKDIR")
    exact(config.get("Labels"), {LABEL: owner}, "CONTAINER_LABELS")
    require(config.get("OpenStdin") is False and config.get("StdinOnce") is False
            and config.get("Tty") is False, "CONTAINER_INTERACTIVE_CONFIG")


def assert_container(info, identity, name, image, stage, owner, mounts, uid, gid, environment):
    authority = candidate.AUTHORITY[stage]
    require(type(info) is dict and info.get("Id") == identity and info.get("Name") == "/" + name, "CONTAINER_IDENTITY")
    config, host = info.get("Config", {}), info.get("HostConfig", {})
    require(info.get("Image") == image and config.get("Labels", {}).get(LABEL) == owner, "CONTAINER_OWNER")
    assert_execution_config(config, COMMANDS[stage], "0:0" if stage == "assembly" else f"{uid}:{gid}", environment, owner)
    require(config.get("Hostname") == name and config.get("Domainname", "") == "", "CONTAINER_HOSTNAME")
    for key, value in {"NetworkMode": "none", "ReadonlyRootfs": authority["read_only_root"],
                       "Privileged": False, "Memory": CAPS["container_memory_bytes"],
                       "MemorySwap": CAPS["container_memory_and_swap_bytes"], "PidsLimit": CAPS["container_pids"],
                       "NanoCpus": CAPS["container_cpus"] * 1000000000}.items():
        exact(host.get(key), value, "CONTAINER_LIMIT_OR_AUTHORITY")
    exact(host.get("CapDrop"), ["ALL"], "CONTAINER_CAPABILITIES")
    exact(host.get("CapAdd") or [], authority["cap_add"], "CONTAINER_CAPABILITIES")
    require(host.get("SecurityOpt") in (["no-new-privileges"], ["no-new-privileges=true"]), "CONTAINER_SECURITY")
    require(host.get("LogConfig") == {"Type": "none", "Config": {}}, "CONTAINER_LOG_DRIVER")
    for key in ("Devices", "DeviceRequests", "DeviceCgroupRules", "VolumesFrom", "ExtraHosts", "Links",
                "PortBindings", "Tmpfs", "GroupAdd", "Sysctls", "Binds", "Ulimits"):
        require(host.get(key) in (None, [], {}), "CONTAINER_EXTRA_AUTHORITY")
    require(host.get("PidMode", "") == "" and host.get("IpcMode") == "private" and host.get("UTSMode", "") == "", "CONTAINER_NAMESPACE")
    require(host.get("UsernsMode", "") == "" and host.get("CgroupParent", "") == ""
            and host.get("Runtime") == "runc" and host.get("AutoRemove") is False
            and host.get("PublishAllPorts") is False
            and host.get("RestartPolicy") == {"Name": "no", "MaximumRetryCount": 0}, "CONTAINER_EXTRA_AUTHORITY")
    require(type(info.get("Mounts")) is list and len(info["Mounts"]) == len(mounts)
            and all(type(row.get("RW")) is bool for row in info["Mounts"]), "CONTAINER_MOUNTS")
    observed = {(row["Source"], row["Destination"], row["RW"]): row for row in info["Mounts"]}
    require(len(observed) == len(mounts) and set(observed) == set(mounts), "CONTAINER_MOUNTS")
    require(all(row.get("Type") == "bind" and row.get("Propagation") == "rprivate" for row in observed.values()), "CONTAINER_MOUNT_TYPE")


def assert_assembled(info, identity, trial):
    require(info.get("Id") == identity and info.get("Os") == "linux"
            and info.get("Architecture") == "amd64", "ASSEMBLED_IMAGE_IDENTITY")
    layers = info.get("RootFS", {})
    original = [row["diff_id"] for row in trial.documents["inputs-v1.json"]["oci"]["layers"]]
    require(layers.get("Type") == "layers" and type(layers.get("Layers")) is list
            and len(layers["Layers"]) == len(original) + 1 and layers["Layers"][:-1] == original
            and re.fullmatch(r"sha256:[0-9a-f]{64}", layers["Layers"][-1]), "ASSEMBLED_IMAGE_LAYERS")
    assert_execution_config(info.get("Config"), COMMANDS["assembly"], "0:0",
                            expected_environment(trial.documents), trial.owner)


class Trial:
    def __init__(self, root, sources, plan_sha, revision, documents):
        self.root, self.sources, self.documents = root, sources, documents
        self.started = time.monotonic()
        self.deadline = self.started + CAPS["wall_seconds"]
        self.work_deadline = self.deadline - CAPS["cleanup_reserve_seconds"]
        self.cleaning = False
        self.cleanup_deadline = self.deadline
        self.alarm_owned = False
        self.owner = "zpm-alpine-" + os.urandom(12).hex()
        self.environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                            "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
                            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                            "DOCKER_API_VERSION": API,
                            "PYTHONDONTWRITEBYTECODE": "1"}
        self.docker = ["/usr/bin/docker", "--config", str(root / "docker-config"), "--host", "unix://" + SOCKET]
        self.record = {
            "schema": "zpm-native-alpine-native-report/v1", "attempt": "native-alpine-3",
            "plan_sha256": plan_sha, "harness_source_revision": revision,
            "production_sources": dict(candidate.SOURCES), "recipe_sha256": candidate.METADATA["inputs-v1.json"][1],
            "status": "RUNNING", "first_failure": None, "native_execution_qualified": False,
            "native_checks": {name: "NOT_RUN" for name in candidate.NATIVE_CHECKS},
            "environment": {}, "acquisition": {"requests": 0, "body_bytes": 0, "artifacts_complete": 0},
            "resources": {"samples": 0, "maximum_sample_gap_ms": 0, "peak_host_rss_bytes": 0,
                          "peak_logical_owned_bytes": 0, "peak_container_memory_bytes": 0,
                          "peak_container_pids": 0, "last_container_cpu_ns": 0,
                          "minimum_host_available_bytes": None,
                          "command_output_bytes": 0, "pending_mutation_reservations": 0,
                          "storage_scope": FIXED_POLICY["storage_observation"],
                          "physical_storage_quota": False, "opaque_daemon_caches_observed": False},
            "cleanup_failures": [], "truncated": False,
        }
        self.journal = {"schema": "zpm-native-alpine-ownership/v1", "owner": self.owner,
                        "containers": [], "image_references": [], "base_preexisting": None}
        self.retained = {}
        self.pidfds = {}
        self.sessions = set()
        self.root_identity = (os.getpid(), process_snapshot()[os.getpid()]["start"])
        self.container = None
        self.last_sample = None
        self.aggregate_output = 0
        self.command_count = 0
        self.direct_children = []
        self.base_observation = None
        for name in ("home", "tmp", "docker-config", "logs", "inputs", "work", "keys"):
            (root / name).mkdir(mode=0o700)
        for name in ("layers", "apks", "wheels"):
            (root / "inputs" / name).mkdir(mode=0o700)
        self.output_budget = root / "work/output-budget.bin"
        with self.output_budget.open("xb") as counter:
            os.fchmod(counter.fileno(), 0o600)
            counter.write(bytes(8))
        self.control = None
        self.save_journal()
        self.save()

    def end(self, seconds):
        require(self.cleaning or self.record["first_failure"] is None, "WORK_STOPPED_AFTER_FAILURE")
        end = min(time.monotonic() + seconds, self.observation_deadline())
        require(end > time.monotonic(), "TOTAL_DEADLINE")
        return end

    def fail(self, code):
        if self.record["first_failure"] is None:
            self.record["first_failure"] = code
        self.record["status"] = "FAILED"
        self.record["native_execution_qualified"] = False

    def save(self):
        raw = report_bytes(self.record)
        if len(raw) > CAPS["report_bytes"]:
            raise Refused("REPORT_BOUND")
        atomic_private(self.root / "private-report.json", raw)

    def save_journal(self):
        atomic_private(self.root / "ownership.json", encoded(self.journal) + b"\n")

    def register_child(self, pid):
        """Retain the owned original before an unrelated full-host scan."""
        require(len(self.pidfds) < 512, "OWNED_PROCESS_IDENTITY_BOUND")
        row = parse_stat(Path(f"/proc/{pid}/stat").read_text(), pid)
        fd = os.pidfd_open(pid)
        try:
            require(parse_stat(Path(f"/proc/{pid}/stat").read_text(), pid)["start"] == row["start"], "OWNED_PID_REUSED")
        except BaseException:
            os.close(fd)
            raise
        self.pidfds[(pid, row["start"])] = fd
        self.retained[pid] = row["start"]
        self.sessions.add(pid)

    def observed_processes(self, rows):
        for pid, start in self.pidfds:
            require(pid not in rows or rows[pid]["start"] == start, "OWNED_PID_REUSED")
        active_sessions = {row.get("session") for row in rows.values()}
        for origin in tuple(self.sessions):
            if origin not in active_sessions and origin not in rows:
                self.sessions.remove(origin)
        self.retained, rss = reconcile_processes(rows, self.retained, self.root_identity, self.sessions)
        for pid, start in self.retained.items():
            if pid == self.root_identity[0] or (pid, start) in self.pidfds:
                continue
            require(len(self.pidfds) < 512, "OWNED_PROCESS_IDENTITY_BOUND")
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                require(parse_stat(Path(f"/proc/{pid}/stat").read_text(), pid)["start"] == start, "OWNED_PID_REUSED")
            except FileNotFoundError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            self.pidfds[(pid, start)] = fd
        return rss

    def observation_deadline(self):
        return self.cleanup_deadline if self.cleaning else self.work_deadline

    def observe_mutations(self):
        """Keep full intent charges until exact absence is actually confirmed."""
        charge = 0
        if self.base_observation is not None:
            info = docker_json("/images/" + self.base_observation + "/json", deadline=self.observation_deadline())
            assert_base(info, self.documents)
            require(type(info.get("Size")) is int and 0 <= info["Size"] <= MUTATION_RESERVATIONS["base_image_bytes"], "IMAGE_STORAGE_OBSERVATION")
            charge += MUTATION_RESERVATIONS["base_image_bytes"]
        for row in self.journal["image_references"]:
            if row["removed"]:
                continue
            charge += row["reserved_bytes"]
            info = docker_json("/images/" + row["tag"] + "/json", absent=True, deadline=self.observation_deadline())
            if info is None and row["id"] is not None:
                info = docker_json("/images/" + row["id"] + "/json", absent=True, deadline=self.observation_deadline())
            if info is None:
                require(row["state"] in ("creating", "removing"), "IMAGE_DISAPPEARED")
                continue
            identity = info.get("Id")
            require(type(identity) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", identity)
                    and (row["id"] is None or row["id"] == identity), "IMAGE_STORAGE_IDENTITY")
            require(row["kind"] == "base" or info.get("Config", {}).get("Labels", {}).get(LABEL) == self.owner,
                    "IMAGE_STORAGE_OWNER")
            if row["kind"] == "base":
                require(identity == "sha256:" + self.documents["inputs-v1.json"]["oci"]["config_sha256"], "IMAGE_STORAGE_IDENTITY")
            if row["id"] is None:
                row["id"] = identity
                self.save_journal()
            require(type(info.get("Size")) is int and 0 <= info["Size"] <= row["reserved_bytes"], "IMAGE_STORAGE_BOUND")
            row["last_observed_bytes"] = info["Size"]
        for row in self.journal["containers"]:
            if row["removed"]:
                continue
            charge += row["reserved_bytes"]
            info = docker_json("/containers/" + (row["id"] or row["name"]) + "/json?size=true",
                               absent=True, deadline=self.observation_deadline())
            if info is None:
                require(row["state"] in ("creating", "removing"), "CONTAINER_DISAPPEARED")
                continue
            identity = info.get("Id")
            require(type(identity) is str and candidate.HEX.fullmatch(identity)
                    and (row["id"] is None or identity == row["id"]), "CONTAINER_STORAGE_IDENTITY")
            assert_container(info, identity, row["name"], row["image"], row["stage"], self.owner,
                             row["mounts"], os.getuid(), os.getgid(), expected_environment(self.documents))
            if row["id"] is None:
                row["id"] = identity
                self.save_journal()
            require(type(info.get("SizeRw")) is int and 0 <= info["SizeRw"] <= row["reserved_bytes"], "CONTAINER_STORAGE_BOUND")
            row["last_observed_bytes"] = info["SizeRw"]
            if info.get("State", {}).get("Running") is True:
                self.observe_guest_phase(row, running=True)
                stats = docker_json("/containers/" + identity + "/stats?stream=false&one-shot=true", deadline=self.observation_deadline())
                try:
                    counters = assert_stats(stats, identity, True)
                except Refused:
                    after = docker_json("/containers/" + identity + "/json", deadline=self.observation_deadline())
                    require(after.get("Id") == identity and after.get("State", {}).get("Running") is False
                            and after["State"].get("Status") in ("exited", "dead"), "CONTAINER_STATS_MISSING")
                    counters = None
                if counters:
                    resource = self.record["resources"]
                    resource["peak_container_memory_bytes"] = max(resource["peak_container_memory_bytes"], counters["memory"])
                    resource["peak_container_pids"] = max(resource["peak_container_pids"], counters["pids"])
                    resource["last_container_cpu_ns"] = counters["cpu"]
        return charge

    def observe_guest_phase(self, row, *, running):
        path = self.root / "work" / (row["stage"] + "-phase.json")
        value = candidate.decode(candidate.read_bounded(path, 8192))
        checked = guest.validate_phases(value, row["control"], row["stage"], now_ns=time.monotonic_ns(),
                                        running=running and not self.cleaning, previous=row.get("phase_observation"))
        row["phase_observation"] = checked
        return checked

    def sample(self):
        now = time.monotonic()
        require(now < self.observation_deadline(), "TOTAL_DEADLINE")
        rows = process_snapshot()
        rss = self.observed_processes(rows)
        resource = self.record["resources"]
        resource["samples"] += 1
        if self.last_sample is not None:
            resource["maximum_sample_gap_ms"] = max(resource["maximum_sample_gap_ms"], round((now - self.last_sample) * 1000))
        self.last_sample = now
        resource["peak_host_rss_bytes"] = max(resource["peak_host_rss_bytes"], rss)
        available = available_memory()
        minimum = resource["minimum_host_available_bytes"]
        resource["minimum_host_available_bytes"] = available if minimum is None else min(minimum, available)
        resource["pending_mutation_reservations"] = sum(row["reserved_bytes"] for group in ("containers", "image_references")
                    for row in self.journal[group] if not row["removed"])
        logical = (logical_files([self.root, REPO, *self.sources.values()], temporary_root=self.root) + self.observe_mutations()
                   + FIXED_POLICY["final_report_reservation_bytes"])
        resource["command_output_bytes"] = guest.output_total(self.output_budget, self.observation_deadline(), os.getuid())
        require(resource["command_output_bytes"] <= CAPS["aggregate_log_bytes"], "AGGREGATE_OUTPUT_BOUND")
        resource["peak_logical_owned_bytes"] = max(resource["peak_logical_owned_bytes"], logical)
        require(rss <= CAPS["host_rss_bytes"], "HOST_RSS_BOUND")
        require(logical <= CAPS["owned_storage_bytes"], "LOGICAL_STORAGE_BOUND")
        self.refresh_acquisition()

    def refresh_acquisition(self):
        path = self.root / "acquisition.json"
        if path.exists():
            value = candidate.decode(candidate.read_bounded(path, 65536))
            fields = {"requests", "body_bytes", "metadata_reserved_bytes", "reserved_body_bytes", "response_header_bytes", "response_framing_bytes",
                      "artifacts_complete", "redirects", "origins"}
            require(type(value) is dict and set(value) == fields, "ACQUISITION_RECORD")
            require(all(type(value[name]) is int and value[name] >= 0 for name in fields - {"origins"}), "ACQUISITION_RECORD")
            require(type(value["origins"]) is list and len(value["origins"]) <= 5 and all(origin in {
                "auth.docker.io", "registry-1.docker.io", "production.cloudfront.docker.com",
                "dl-cdn.alpinelinux.org", "files.pythonhosted.org"} for origin in value["origins"]), "ACQUISITION_ORIGINS")
            previous = self.record["acquisition"]
            require(all(value[name] >= previous.get(name, 0) for name in fields - {"origins"})
                    and len(set(value["origins"])) == len(value["origins"])
                    and value["origins"][:len(previous.get("origins", []))] == previous.get("origins", []),
                    "ACQUISITION_RECORD_REWIND")
            # Preserve observed offending counters before refusing their cap.
            self.record["acquisition"] = value
            require(value["requests"] <= CAPS["acquisition_requests"]
                    and value["metadata_reserved_bytes"] == value["requests"] * CAPS["request_metadata_reserved_bytes"]
                    and value["reserved_body_bytes"] + value["metadata_reserved_bytes"] <= CAPS["acquisition_bytes"]
                    and value["body_bytes"] <= value["reserved_body_bytes"]
                    and value["response_header_bytes"] + value["response_framing_bytes"] <= value["metadata_reserved_bytes"]
                    and value["artifacts_complete"] <= 173 and value["redirects"] <= 4,
                    "ACQUISITION_OBSERVED_BOUND")

    def run(self, command, seconds=15, *, cwd=None, allowed=(0,)):
        end = self.end(seconds)
        self.command_count += 1
        path = self.root / "logs" / ("command-%03d.log" % self.command_count)
        process = subprocess.Popen(command, cwd=cwd or self.root, env=self.environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        self.direct_children.append(process.pid)
        output = bytearray()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            self.register_child(process.pid)
            with path.open("xb") as log:
                while selector.get_map():
                    require(time.monotonic() < end, "COMMAND_DEADLINE")
                    for key, _ in selector.select(min(.25, max(0, end - time.monotonic()))):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        self.aggregate_output = guest.charge_output(self.output_budget, len(block), end, os.getuid())
                        require(len(output) + len(block) <= CAPS["command_output_bytes"], "COMMAND_OUTPUT_BOUND")
                        output.extend(block)
                        log.write(block)
                    self.sample()
                process.wait(timeout=max(.01, end - time.monotonic()))
                require(time.monotonic() < end, "COMMAND_DEADLINE")
                require(process.returncode in allowed, "COMMAND_EXIT")
                log.flush()
            return bytes(output)
        except subprocess.TimeoutExpired:
            raise Refused("COMMAND_DEADLINE") from None
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining = self.observation_deadline() - time.monotonic()
                if remaining > 0:
                    try:
                        process.wait(timeout=min(1, remaining))
                    except subprocess.TimeoutExpired:
                        self.fail("HOST_CHILD_UNREAPED")

    def fork_stage(self, name, seconds, operation):
        """Bound even blocked resolver/archive/parser work with an owned PID."""
        end = self.end(seconds)
        result = self.root / (name + "-child.json")
        reader, writer = os.pipe2(os.O_CLOEXEC)
        pid = os.fork()
        if pid == 0:
            try:
                os.close(writer)
                os.setsid()
                require(os.read(reader, 1) == b"1", "CHILD_OBSERVER_NOT_READY")
                os.close(reader)
                # Children cannot accidentally inherit provider/proxy settings.
                os.environ.clear()
                os.environ.update(self.environment)
                operation(end)
                atomic_private(result, encoded({"status": "PASS", "failure": None}) + b"\n")
                os._exit(0)
            except BaseException as error:
                try:
                    atomic_private(result, encoded({"status": "FAIL", "failure": fixed_code(error, "CHILD_FAILURE")}) + b"\n")
                finally:
                    os._exit(1)
        os.close(reader)
        self.direct_children.append(pid)
        done = False
        try:
            self.register_child(pid)
            self.sample()
            require(time.monotonic() < end, "STAGE_DEADLINE")
            os.write(writer, b"1")
            os.close(writer)
            writer = None
            while True:
                found, status = os.waitpid(pid, os.WNOHANG)
                if found:
                    done = True
                    value = candidate.decode(candidate.read_bounded(result, 4096))
                    require(value.get("status") == "PASS" and os.waitstatus_to_exitcode(status) == 0,
                            value.get("failure") if re.fullmatch(r"[A-Z0-9_]{1,64}", str(value.get("failure"))) else "CHILD_FAILURE")
                    self.sample()
                    return
                require(time.monotonic() < end, "STAGE_DEADLINE")
                self.sample()
                time.sleep(min(.25, max(0, end - time.monotonic())))
        finally:
            if writer is not None:
                os.close(writer)
            if not done:
                fd = next((fd for (observed, _), fd in self.pidfds.items() if observed == pid), None)
                if fd is not None:
                    try:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)  # Still our unreaped original child.
                    except ProcessLookupError:
                        pass
            self.sample_if_possible()

    def sample_if_possible(self):
        try:
            self.sample()
        except Exception as error:
            self.fail(fixed_code(error, "RESOURCE_OBSERVATION"))

    def check_sources(self):
        total = 0
        for service, root in self.sources.items():
            require(root.resolve() == root and root.is_dir() and (root / ".git").is_dir() and not (root / ".git").is_symlink(), "SOURCE_DIRECTORY")
            source_git_metadata(root, candidate.SOURCE_FACTS[service]["repository"])
            git = ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(root)]
            facts = candidate.SOURCE_FACTS[service]
            require(self.run(git + ["rev-parse", "HEAD"]).decode().strip() == facts["revision"], "SOURCE_REVISION")
            require(self.run(git + ["rev-parse", "HEAD^{tree}"]).decode().strip() == facts["tree"], "SOURCE_TREE")
            require(self.run(git + ["status", "--porcelain=v1", "--untracked-files=all"]) == b"", "SOURCE_DIRTY")
            config = self.run(git + ["config", "--local", "--list", "-z"]).decode().lower()
            require(not any(word in config for word in ("extraheader", "credential.", "proxy", "includepath", "include.path", "includeif.")), "SOURCE_CREDENTIAL_CONFIGURATION")
            total += verify_source_files(root, service, source_tree_records(self.run(git + ["ls-tree", "-rz", "HEAD"])))
            self.sample()
        return total

    def prerequisites(self, revision):
        require(platform.system() == "Linux" and platform.machine() == "x86_64", "HOST_PLATFORM")
        require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12), "HOST_PYTHON_VERSION")
        require(os.getuid() > 0 and os.getgid() > 0, "HOST_NONROOT_REQUIRED")
        require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"), "HOST_PIDFD_REQUIRED")
        require(available_memory() >= CAPS["minimum_host_available_bytes"], "HOST_AVAILABLE_MEMORY")
        require(stat.S_ISSOCK(os.stat(SOCKET).st_mode), "LOCAL_DOCKER_SOCKET")
        self.record["environment"] = {"python": platform.python_version(), "kernel": platform.release(),
                                      "machine": platform.machine(), "runner_os": os.environ.get("ImageOS", "unreported"),
                                      "runner_version": os.environ.get("ImageVersion", "unreported"),
                                      "uid": os.getuid(), "gid": os.getgid(), "engine_api": API}
        require(all(type(value) in (str, int) and (type(value) is int or len(value) <= 256)
                    for value in self.record["environment"].values()), "HOST_IDENTITY_BOUND")
        source_git_metadata(REPO, "niklas-olsson/zero-point-modulator")
        harness_git = ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                       "-c", "core.hooksPath=/dev/null", "-C", str(REPO)]
        require(self.run(harness_git + ["rev-parse", "HEAD"]).decode().strip() == revision, "HARNESS_SOURCE_REVISION")
        require(self.run(harness_git + ["status", "--porcelain=v1", "--untracked-files=all"]) == b"", "HARNESS_SOURCE_DIRTY")
        harness_bytes = verify_source_files(REPO, "harness", source_tree_records(self.run(harness_git + ["ls-tree", "-rz", "HEAD"])))
        self.record["environment"]["git"] = self.run(["/usr/bin/git", "--version"]).decode().strip()
        self.record["environment"]["openssl"] = self.run(["/usr/bin/openssl", "version"]).decode().strip()
        versions = candidate.decode(self.run(self.docker + ["version", "--format", "{{json .}}"] ))
        def api_tuple(value):
            require(type(value) is str and re.fullmatch(r"1\.[0-9]{2}", value), "DOCKER_API_VERSION")
            return tuple(map(int, value.split(".")))
        server, client = versions["Server"], versions["Client"]
        require(api_tuple(server["MinAPIVersion"]) <= api_tuple(API) <= api_tuple(server["ApiVersion"]), "DOCKER_API_UNSUPPORTED")
        require(api_tuple(client["ApiVersion"]) >= api_tuple(API), "DOCKER_CLIENT_API")
        for name, row in (("docker_client", client), ("docker_server", server)):
            require(type(row["Version"]) is str and re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,63}", row["Version"]), "DOCKER_VERSION")
            self.record["environment"][name] = row["Version"]
        daemon = docker_json("/info", deadline=self.work_deadline)
        require(daemon.get("OSType") == "linux" and daemon.get("Architecture") in ("x86_64", "amd64"), "DOCKER_PLATFORM")
        require(daemon.get("MemoryLimit") is True and daemon.get("SwapLimit") is True and daemon.get("PidsLimit") is True, "DOCKER_LIMIT_SUPPORT")
        require(daemon.get("DefaultRuntime") == "runc", "DOCKER_RUNTIME")
        require(harness_bytes + self.check_sources() <= CAPS["source_tracked_bytes"], "COMBINED_SOURCE_BOUND")
        self.sample()

    def create(self, image, stage):
        require(self.container is None, "CONTAINER_CONCURRENCY")
        name = self.owner + "-" + stage
        require(docker_json("/containers/" + name + "/json", absent=True, deadline=self.work_deadline) is None, "CONTAINER_NAME_CONFLICT")
        mounts = [(str(self.root / "inputs"), "/inputs", False),
                  (str(REPO / "tests/contracts"), "/harness", False),
                  (str(self.root / "work"), "/work", True)]
        if stage == "qualification":
            mounts += [(str(path), "/sources/" + service, False) for service, path in sorted(self.sources.items())]
        control = {**self.control, "stage": stage, "stage_started_ns": time.monotonic_ns()}
        atomic_private(self.root / "work/control.json", encoded(control) + b"\n")
        atomic_private(self.root / "work" / (stage + "-phase.json"), encoded(guest.phase_record(control, stage)) + b"\n")
        row = {"name": name, "id": None, "stage": stage, "image": image, "removed": False,
               "state": "creating", "mutation_confirmed": False, "reserved_bytes": MUTATION_RESERVATIONS["container_writable_bytes"],
               "last_observed_bytes": None, "mounts": mounts, "control": control}
        self.journal["containers"].append(row)
        self.save_journal()  # Intent before the possibly partially successful create.
        authority = candidate.AUTHORITY[stage]
        self.sample()  # Full prospective writable-layer charge before create.
        command = self.docker + ["create", "--name", name, "--hostname", name, "--label", LABEL + "=" + self.owner,
                                "--network", "none", "--ipc", "private", "--security-opt", "no-new-privileges",
                                "--cap-drop", "ALL", "--memory", str(CAPS["container_memory_bytes"]),
                                "--memory-swap", str(CAPS["container_memory_and_swap_bytes"]),
                                "--pids-limit", str(CAPS["container_pids"]), "--cpus", "2", "--log-driver", "none",
                                "--user", "0:0" if stage == "assembly" else f"{os.getuid()}:{os.getgid()}",
                                "--workdir", "/work", "--pull", "never"]
        for key, value in CONTAINER_ENV.items():
            command += ["--env", key + "=" + value]
        if authority["read_only_root"]:
            command.append("--read-only")
        for capability in authority["cap_add"]:
            command += ["--cap-add", capability]
        for source, destination, writable in mounts:
            require("," not in source, "MOUNT_PATH")
            command += ["--mount", f"type=bind,source={source},target={destination},bind-propagation=rprivate" + ("" if writable else ",readonly")]
        identity = self.run(command + [image, *COMMANDS[stage]]).decode().strip()
        require(candidate.HEX.fullmatch(identity), "CONTAINER_CREATED_ID")
        require(row["id"] in (None, identity), "CONTAINER_CREATED_ID")
        row["id"] = identity
        row["state"], row["mutation_confirmed"] = "present", True
        self.container = identity
        self.save_journal()
        info = docker_json("/containers/" + identity + "/json", deadline=self.work_deadline)
        assert_container(info, identity, name, image, stage, self.owner, mounts, os.getuid(), os.getgid(), expected_environment(self.documents))
        require(info.get("State", {}).get("Status") == "created", "CONTAINER_PRESTART")
        self.sample()
        return identity

    def remove_container(self, row):
        info = docker_json("/containers/" + (row["id"] or row["name"]) + "/json", absent=True, deadline=self.observation_deadline())
        if info is None:
            if row["state"] == "removing" and row["id"] is not None:
                require(docker_json("/containers/" + row["name"] + "/json", absent=True,
                                    deadline=self.observation_deadline()) is None, "CLEANUP_CONTAINER_NAME_SURVIVED")
                row["removed"], row["state"] = True, "absent"
                if self.container == row["id"]:
                    self.container = None
                self.save_journal()
                require(row["mutation_confirmed"], "CONTAINER_MUTATION_OUTCOME_UNKNOWN")
            require(row["removed"], "CONTAINER_MUTATION_OUTCOME_UNKNOWN" if not row["mutation_confirmed"] else "CONTAINER_DISAPPEARED")
            return
        require(info.get("Name") == "/" + row["name"] and info.get("Config", {}).get("Labels", {}).get(LABEL) == self.owner
                and (row["id"] is None or info.get("Id") == row["id"]), "CLEANUP_CONTAINER_OWNER")
        row["id"] = info["Id"]
        row["state"] = "removing"
        self.save_journal()
        self.run(self.docker + ["rm", "--force", row["id"]], seconds=10)
        require(docker_json("/containers/" + row["id"] + "/json", absent=True, deadline=self.observation_deadline()) is None, "CLEANUP_CONTAINER_SURVIVED")
        require(docker_json("/containers/" + row["name"] + "/json", absent=True, deadline=self.observation_deadline()) is None, "CLEANUP_CONTAINER_NAME_SURVIVED")
        row["removed"] = True
        row["state"] = "absent"
        if self.container == row["id"]:
            self.container = None
        self.save_journal()
        require(row["mutation_confirmed"], "CONTAINER_MUTATION_OUTCOME_UNKNOWN")

    def cleanup(self):
        self.cleaning = True
        self.cleanup_deadline = min(self.deadline, time.monotonic() + CAPS["cleanup_reserve_seconds"])
        if self.alarm_owned and self.cleanup_deadline > time.monotonic():
            signal.setitimer(signal.ITIMER_REAL, self.cleanup_deadline - time.monotonic())
        for row in reversed(self.journal["containers"]):
            try:
                self.remove_container(row)
            except Exception as error:
                self.record["cleanup_failures"].append(fixed_code(error, "CLEANUP_CONTAINER_UNCONFIRMED"))
        for row in reversed(self.journal["image_references"]):
            try:
                info = docker_json("/images/" + row["tag"] + "/json", absent=True, deadline=self.observation_deadline())
                if info is None and row["id"] is not None:
                    info = docker_json("/images/" + row["id"] + "/json", absent=True, deadline=self.observation_deadline())
                if info is None:
                    if row["state"] == "removing" and row["id"] is not None:
                        row["removed"], row["state"] = True, "absent"
                        self.save_journal()
                        require(row["mutation_confirmed"], "IMAGE_MUTATION_OUTCOME_UNKNOWN")
                    require(row["removed"], "IMAGE_MUTATION_OUTCOME_UNKNOWN" if not row["mutation_confirmed"] else "IMAGE_DISAPPEARED")
                    continue
                expected = row["id"]
                require((expected is None or info.get("Id") == expected) and
                        (row["kind"] == "base" or info.get("Config", {}).get("Labels", {}).get(LABEL) == self.owner), "CLEANUP_IMAGE_OWNER")
                require(row["kind"] != "base" or info.get("Id") == "sha256:" + self.documents["inputs-v1.json"]["oci"]["config_sha256"], "CLEANUP_BASE_IDENTITY")
                identity = info["Id"]
                row["id"], row["state"] = identity, "removing"
                self.save_journal()
                tags = info.get("RepoTags") or []
                reference = row["tag"] if row["tag"] in tags else identity
                require(reference != identity or not tags and not info.get("RepoDigests"), "CLEANUP_IMAGE_FOREIGN_REFERENCE")
                self.run(self.docker + ["image", "rm", reference], seconds=10)
                require(docker_json("/images/" + row["tag"] + "/json", absent=True, deadline=self.observation_deadline()) is None, "CLEANUP_TAG_SURVIVED")
                # docker image rm of the last owned tag normally removes the
                # image. Never force or remove another actor's new reference.
                remaining = docker_json("/images/" + identity + "/json", absent=True, deadline=self.observation_deadline())
                if remaining is not None:
                    require(not remaining.get("RepoTags") and not remaining.get("RepoDigests"), "CLEANUP_IMAGE_FOREIGN_REFERENCE")
                    self.run(self.docker + ["image", "rm", identity], seconds=10)
                    require(docker_json("/images/" + identity + "/json", absent=True, deadline=self.observation_deadline()) is None, "CLEANUP_IMAGE_SURVIVED")
                row["removed"] = True
                row["state"] = "absent"
                self.save_journal()
                require(row["mutation_confirmed"], "IMAGE_MUTATION_OUTCOME_UNKNOWN")
            except Exception as error:
                self.record["cleanup_failures"].append(fixed_code(error, "CLEANUP_IMAGE_UNCONFIRMED"))
        try:
            self.cleanup_processes()
        except Exception as error:
            self.record["cleanup_failures"].append(fixed_code(error, "CLEANUP_PROCESS_UNCONFIRMED"))
        try:
            self.sample()
        except Exception as error:
            code = fixed_code(error, "FINAL_RESOURCE_OBSERVATION")
            self.fail(code)
            self.record["cleanup_failures"].append(code)
        self.record["native_checks"]["owned_cleanup"] = "FAIL" if self.record["cleanup_failures"] else "PASS"
        if self.record["cleanup_failures"]:
            self.fail(self.record["cleanup_failures"][0])

    def cleanup_processes(self):
        # Reconcile once more so known children remain owned after reparenting.
        observation_failure = None
        try:
            self.observed_processes(process_snapshot())
        except Exception as error:
            observation_failure = error
        def terminate_known():
            for fd in self.pidfds.values():
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        terminate_known()  # Even the first full-host scan may have refused.
        while time.monotonic() < self.observation_deadline():
            for pid in self.direct_children:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            rows = process_snapshot()
            self.observed_processes(rows)
            terminate_known()
            pending = []
            for pid, start in self.pidfds:
                if pid not in rows:
                    continue
                require(rows[pid]["start"] == start, "CLEANUP_PID_REUSED")
                pending.append(pid)
            if not pending:
                if observation_failure is not None:
                    raise observation_failure
                return
            time.sleep(min(.25, max(0, self.observation_deadline() - time.monotonic())))
        raise Refused("CLEANUP_PROCESS_SURVIVED")


def bounded_openssl(command, timeout, deadline, directory, budget):
    """Only the existing APK verifier's fixed OpenSSL argv is accepted."""
    require(command[:4] == ["openssl", "dgst", "-sha1", "-verify"] and len(command) == 8,
            "APK_VERIFY_COMMAND")
    end = min(time.monotonic() + timeout, deadline)
    require(time.monotonic() < end, "ARTIFACT_VERIFY_DEADLINE")
    process = subprocess.Popen(["/usr/bin/openssl", *command[1:]], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            require(time.monotonic() < end, "APK_VERIFY_DEADLINE")
            for key, _ in selector.select(min(.1, max(0, end - time.monotonic()))):
                block = os.read(key.fd, 4096)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                guest.charge_output(budget, len(block), end, os.getuid())
                require(len(output) + len(block) <= CAPS["command_output_bytes"], "COMMAND_OUTPUT_BOUND")
                output.extend(block)
        process.wait(timeout=max(.01, end - time.monotonic()))
        require(time.monotonic() < end, "APK_VERIFY_DEADLINE")
        require(process.returncode == 0, "APK_SIGNATURE")
        # A bounded private log participates in the parent's total log sample.
        destination = directory / ("openssl-" + command[-1].split("/")[-2] + ".log")
        destination.write_bytes(output)
        return output.decode("ascii")
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    process.wait(timeout=min(1, remaining))
                except subprocess.TimeoutExpired:
                    pass


def verify_artifacts(trial, deadline):
    recipe = trial.documents["inputs-v1.json"]
    catalog = trial.documents["native-extensions-v1.json"]["members"]
    found_keys = {}
    layer_files = []
    for index, row in enumerate(recipe["oci"]["layers"]):
        require(time.monotonic() < deadline, "ARTIFACT_VERIFY_DEADLINE")
        path = trial.root / "inputs/layers" / (row["digest"][7:] + ".gz")
        raw = candidate.read_bounded(path, row["compressed_bytes"], row["digest"][7:])
        candidate.verify_layer_bytes(raw, row)
        decoded = musl.gzip_streams(raw, 1, decoded_limit=row["decoded_bytes"])[0][1]
        members = musl.tar_entries(decoded)
        for filename, sha in recipe["oci"]["trusted_keys"].items():
            name = "etc/apk/keys/" + filename
            if name in members:
                member, content = members[name]
                require(member.isfile() and 0 < len(content) <= 8192 and digest(content) == sha, "APK_KEY_IDENTITY")
                require(filename not in found_keys or found_keys[filename] == content, "APK_KEY_CHANGED")
                found_keys[filename] = content
        layer_path = trial.root / "inputs/layers" / ("%02d.tar" % index)
        with layer_path.open("xb") as stream:
            stream.write(decoded)
        layer_files.append(layer_path)
    require(set(found_keys) == set(recipe["oci"]["trusted_keys"]), "APK_KEY_SET")
    for name, raw in found_keys.items():
        (trial.root / "keys" / name).write_bytes(raw)
    for row in recipe["apk_packages"]:
        require(time.monotonic() < deadline, "ARTIFACT_VERIFY_DEADLINE")
        musl.verify_package(trial.root / "inputs/apks" / row["filename"], row, trial.root / "keys",
                            lambda command, timeout: bounded_openssl(command, timeout, deadline, trial.root / "logs", trial.output_budget))
    for row in recipe["wheels"]:
        require(time.monotonic() < deadline, "ARTIFACT_VERIFY_DEADLINE")
        raw = candidate.read_bounded(trial.root / "inputs/wheels" / row["filename"], row["size"], row["sha256"])
        candidate.verify_wheel_bytes(raw, row, [entry for entry in catalog if entry["filename"] == row["filename"]])
    config = candidate.read_bounded(candidate.ROOT / "oci-config.json", *candidate.METADATA["oci-config.json"])
    configuration = candidate.decode(config)
    require(configuration["config"].get("Entrypoint") in (None, []) and configuration["config"].get("Volumes") in (None, {}), "OCI_CONFIG_AUTHORITY")
    manifest = [{"Config": recipe["oci"]["config_sha256"] + ".json", "RepoTags": [trial.owner + "/base:1"],
                 "Layers": ["%02d/layer.tar" % index for index in range(len(layer_files))]}]
    with tarfile.open(trial.root / "base-image.tar", "x:") as archive:
        for name, raw in (("manifest.json", encoded(manifest)), (manifest[0]["Config"], config)):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            archive.addfile(info, io.BytesIO(raw))
        for name, path in zip(manifest[0]["Layers"], layer_files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
            with path.open("rb") as stream:
                archive.addfile(info, stream)


def assert_base(info, documents):
    recipe = documents["inputs-v1.json"]
    require(info.get("Id") == "sha256:" + recipe["oci"]["config_sha256"] and info.get("Os") == "linux"
            and info.get("Architecture") == "amd64", "NATIVE_BASE_IMAGE_IDENTITY")
    exact(info.get("RootFS"), {"Type": "layers", "Layers": [row["diff_id"] for row in recipe["oci"]["layers"]]}, "NATIVE_BASE_DIFF_IDS")
    expected = documents["oci-config.json"]["config"]
    config = info.get("Config", {})
    for key in ("Env", "Cmd", "WorkingDir"):
        exact(config.get(key), expected[key], "NATIVE_BASE_CONFIG")
    require(config.get("Entrypoint") in (None, []) and config.get("Volumes") in (None, {}), "NATIVE_BASE_AUTHORITY")


def native_guest_result(trial, stage):
    from tests.contracts.alpine_sealed_guest import GUEST_CHECKS, PROBE_IDS
    path = trial.root / "work" / (stage + "-result.json")
    value = candidate.decode(candidate.read_bounded(path, CAPS["report_bytes"]))
    candidate.object_fields(value, "schema attempt stage status first_failure checks probe_ids_passed interpreter authority installed_distribution_counts elapsed_ms phases", "NATIVE_GUEST_FIELDS")
    require(value["schema"] == "zpm-native-alpine-guest/v1" and value["attempt"] == trial.owner and value["stage"] == stage,
            "NATIVE_GUEST_BINDING")
    require(type(value["checks"]) is dict and set(value["checks"]) == set(GUEST_CHECKS)
            and all(x in ("NOT_RUN", "PASS", "FAIL") for x in value["checks"].values()), "NATIVE_GUEST_CHECKS")
    permitted = {"apk_transaction", "installed_apk_inventory"} if stage == "assembly" else set(GUEST_CHECKS) - {"apk_transaction", "installed_apk_inventory"}
    require(all(value["checks"][name] == "NOT_RUN" for name in set(GUEST_CHECKS) - permitted), "NATIVE_GUEST_STAGE")
    passing = value["status"] == "PASS"
    require(value["status"] in ("PASS", "FAIL") and
            (value["first_failure"] is None if passing else type(value["first_failure"]) is str
             and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value["first_failure"])), "NATIVE_GUEST_STATUS")
    states = [value["checks"][name] for name in GUEST_CHECKS if name in permitted]
    unpassed = next((index for index, state in enumerate(states) if state != "PASS"), len(states))
    require(all(state == "NOT_RUN" for state in states[unpassed + 1:]), "NATIVE_GUEST_CHECK_ORDER")
    row = next(row for row in reversed(trial.journal["containers"]) if row["stage"] == stage)
    phases = value["phases"]
    if phases is not None:
        phases = guest.validate_phases(phases, row["control"], stage, now_ns=time.monotonic_ns(),
                                       running=passing, previous=row.get("phase_observation"))
    for path in [trial.root / "work" / (stage + "-result.json"), trial.root / "work" / (stage + "-phase.json"),
                 *sorted((trial.root / "work").glob(stage + "-command-*.log"))]:
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_gid == os.getgid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, "GUEST_RECEIPT_CUSTODY")
    qualification = stage == "qualification"
    expected_authority = {"uid": os.getuid() if qualification else 0,
                               "gid": os.getgid() if qualification else 0, "no_new_privileges": True,
                               "network_interfaces": ["lo"], "separate_network_and_pid_namespaces": True,
                               "read_only_root": qualification, "capabilities_empty": qualification}
    require(value["authority"] is None or value["authority"] == expected_authority, "NATIVE_GUEST_AUTHORITY")
    require(not any(state == "PASS" for state in states) or value["authority"] == expected_authority
            and phases is not None, "NATIVE_GUEST_EVIDENCE")
    probes = value["probe_ids_passed"]
    require(type(probes) is list and probes == list(PROBE_IDS[:len(probes)])
            and len(probes) <= len(PROBE_IDS) and (qualification or not probes), "NATIVE_PROBE_IDS")
    counts = value["installed_distribution_counts"]
    expected_counts = {name: len(trial.documents["inputs-v1.json"]["environments"][name]["wheels"])
                      + (1 if name == "zpm" else 0) for name in guest.SERVICES}
    require(type(counts) is dict and set(counts) <= (set(guest.SERVICES) if qualification else set())
            and all(type(count) is int and count == expected_counts[name] for name, count in counts.items()),
            "NATIVE_DISTRIBUTION_COUNTS")
    times = value["elapsed_ms"]
    require(type(times) is dict and set(times) <= ({"initial_bootstrap", *PROBE_IDS} if qualification else set())
            and all(type(elapsed) is int and 0 <= elapsed <= (900000 if name == "initial_bootstrap" else 15000)
                    for name, elapsed in times.items()), "NATIVE_GUEST_TIMINGS")
    require(qualification or value["interpreter"] is None, "NATIVE_INTERPRETER_REPORT")
    if value["interpreter"] is not None:
        identity = value["interpreter"]
        require(type(identity) is dict and set(identity) == {"implementation", "version", "cache_tag", "abi", "platform"}
                and all(type(text) is str and 0 < len(text) <= 128 for text in identity.values())
                and identity["implementation"] == "cpython" and identity["version"] == "3.12.16", "NATIVE_INTERPRETER_REPORT")
    if passing:
        require(all(state == "PASS" for state in states) and value["authority"] == expected_authority,
                "NATIVE_GUEST_INCOMPLETE")
        require(phases is not None and len(phases["phases"]) == len(guest.PHASES[stage])
                and all(phase["finished_ns"] is not None for phase in phases["phases"]), "NATIVE_PHASE_INCOMPLETE")
        if qualification:
            require(probes == list(PROBE_IDS) and counts == expected_counts and value["interpreter"] is not None
                    and set(times) == {"initial_bootstrap", *PROBE_IDS}, "NATIVE_GUEST_INCOMPLETE")
    if value["interpreter"] is not None and value["checks"]["interpreter_identity"] == "PASS":
        trial.record["environment"]["native_interpreter"] = value["interpreter"]
    for name in permitted:
        trial.record["native_checks"][name] = value["checks"][name]
    trial.save()
    if not passing:
        raise Refused(value["first_failure"])


def finish_trial(trial):
    """Keep workload failure, unknown cleanup and final output failures distinct."""
    try:
        trial.cleanup()
    except BaseException as error:
        code = fixed_code(error, "CLEANUP_UNCONFIRMED")
        trial.fail(code)
        trial.record["cleanup_failures"].append(code)
        trial.record["native_checks"]["owned_cleanup"] = "FAIL"
    try:
        trial.refresh_acquisition()
    except BaseException as error:
        trial.fail(fixed_code(error, "FINAL_ACQUISITION_OBSERVATION"))
    trial.record["elapsed_ms"] = round((time.monotonic() - trial.started) * 1000)
    if time.monotonic() >= trial.observation_deadline():
        trial.fail("FINAL_DEADLINE")
    eligible = (trial.record["first_failure"] is None and not trial.record["cleanup_failures"]
                and all(value == "PASS" for value in trial.record["native_checks"].values()))
    trial.record["status"] = "PASS" if eligible else "FAILED"
    trial.record["native_execution_qualified"] = eligible
    for exported in (False, True):
        try:
            trial.save()
            if exported:
                atomic_private(trial.root / "summary.json", report_bytes(public_summary(trial.record)))
            trial.sample()
        except BaseException as error:
            trial.fail(fixed_code(error, "FINAL_RESOURCE_OR_REPORT_FAILURE"))
            trial.record["native_execution_qualified"] = False
    trial.record["elapsed_ms"] = round((time.monotonic() - trial.started) * 1000)
    if time.monotonic() >= trial.observation_deadline():
        trial.fail("FINAL_DEADLINE")
    trial.record["native_execution_qualified"] = trial.record["status"] == "PASS"
    try:
        trial.save()
        atomic_private(trial.root / "summary.json", report_bytes(public_summary(trial.record)))
        if time.monotonic() >= trial.observation_deadline():
            trial.fail("FINAL_REPORT_DEADLINE")
            trial.record["native_execution_qualified"] = False
            trial.record["elapsed_ms"] = round((time.monotonic() - trial.started) * 1000)
            trial.save()
            atomic_private(trial.root / "summary.json", report_bytes(public_summary(trial.record)))
    except BaseException as error:
        trial.fail(fixed_code(error, "FINAL_REPORT_UNCONFIRMED"))
        trial.record["native_execution_qualified"] = False
        trial.record["elapsed_ms"] = round((time.monotonic() - trial.started) * 1000)
        # A retained file alone is never acceptance: the native workflow must
        # also succeed. Failure to publish a correction cannot return success.
    return 0 if trial.record["native_execution_qualified"] else 1


def qualify(trial):
    recipe = trial.documents["inputs-v1.json"]
    stage = "artifact_bytes"
    try:
        trial.prerequisites(trial.record["harness_source_revision"])
        trial.fork_stage("acquisition", CAPS["acquisition_seconds"], lambda end: Acquisition(trial.root / "inputs", end).run(recipe))
        trial.fork_stage("artifact-verification", trial.work_deadline - time.monotonic(), lambda end: verify_artifacts(trial, end))
        trial.record["native_checks"]["artifact_bytes"] = "PASS"
        trial.record["native_checks"]["apk_trust"] = "PASS"
        trial.save()
        stage = "base_image_identity"
        base = "sha256:" + recipe["oci"]["config_sha256"]
        prior = docker_json("/images/" + base + "/json", absent=True, deadline=trial.work_deadline)
        trial.journal["base_preexisting"] = prior is not None
        trial.save_journal()
        if prior is None:
            tag = trial.owner + "/base:1"
            require(docker_json("/images/" + tag + "/json", absent=True, deadline=trial.work_deadline) is None, "IMAGE_TAG_CONFLICT")
            row = {"tag": tag, "id": base, "kind": "base", "removed": False, "state": "creating",
                   "mutation_confirmed": False, "reserved_bytes": MUTATION_RESERVATIONS["base_image_bytes"],
                   "last_observed_bytes": None}
            trial.journal["image_references"].append(row)
            trial.save_journal()
            trial.sample()
            trial.run(trial.docker + ["load", "--input", str(trial.root / "base-image.tar")], seconds=60)
            row["state"], row["mutation_confirmed"] = "present", True
            trial.save_journal()
        else:
            trial.base_observation = base
        actual = docker_json("/images/" + base + "/json", deadline=trial.work_deadline)
        assert_base(actual, trial.documents)
        trial.record["native_checks"]["base_image_identity"] = "PASS"
        trial.sample()
        trial.save()
        trial.control = {"attempt": trial.owner, "uid": os.getuid(), "gid": os.getgid(),
                   "work_deadline_ns": int(trial.work_deadline * 1_000_000_000), "fixture_image": base,
                   "host_namespaces": {name: os.readlink("/proc/self/ns/" + name) for name in ("net", "pid")}}
        for name in ("home", "tmp"):
            (trial.root / "work" / name).mkdir(mode=0o700)
        stage = "apk_transaction"
        assembly = trial.create(base, "assembly")
        trial.run(trial.docker + ["start", "--attach", assembly], seconds=CAPS["apk_transaction_seconds"])
        info = docker_json("/containers/" + assembly + "/json", deadline=trial.work_deadline)
        require(info["State"]["Status"] == "exited" and info["State"]["ExitCode"] == 0 and not info["State"]["OOMKilled"], "ASSEMBLY_EXIT")
        native_guest_result(trial, "assembly")
        tag = trial.owner + "/assembled:1"
        require(docker_json("/images/" + tag + "/json", absent=True, deadline=trial.work_deadline) is None, "IMAGE_TAG_CONFLICT")
        row = {"tag": tag, "id": None, "kind": "assembled", "removed": False, "state": "creating",
               "mutation_confirmed": False, "reserved_bytes": MUTATION_RESERVATIONS["assembled_image_bytes"],
               "last_observed_bytes": None}
        trial.journal["image_references"].append(row)
        trial.save_journal()
        trial.sample()
        image = trial.run(trial.docker + ["commit", "--pause=true", "--change", "LABEL " + LABEL + "=" + trial.owner, assembly, tag], seconds=60).decode().strip()
        require(image.startswith("sha256:") and candidate.HEX.fullmatch(image[7:]), "ASSEMBLED_IMAGE_ID")
        require(row["id"] in (None, image), "ASSEMBLED_IMAGE_ID")
        row["id"] = image
        row["state"], row["mutation_confirmed"] = "present", True
        trial.save_journal()
        assert_assembled(docker_json("/images/" + image + "/json", deadline=trial.work_deadline), image, trial)
        trial.remove_container(trial.journal["containers"][-1])
        stage = "interpreter_identity"
        qualification = trial.create(image, "qualification")
        trial.run(trial.docker + ["start", "--attach", qualification], seconds=trial.work_deadline - time.monotonic())
        info = docker_json("/containers/" + qualification + "/json", deadline=trial.work_deadline)
        require(info["State"]["Status"] == "exited" and info["State"]["ExitCode"] == 0 and not info["State"]["OOMKilled"], "QUALIFICATION_EXIT")
        native_guest_result(trial, "qualification")
    except BaseException as error:
        trial.fail(fixed_code(error))
        if trial.record["native_checks"][stage] == "NOT_RUN":
            trial.record["native_checks"][stage] = "FAIL"
        # On a failed native command, retain any completed real stage checks,
        # but a malformed or missing guest record cannot produce native PASS.
        if trial.container is not None:
            current = trial.journal["containers"][-1]["stage"]
            try:
                native_guest_result(trial, current)
            except Exception:
                pass
    finally:
        finish_trial(trial)
    return 0 if trial.record["native_execution_qualified"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--plan", type=Path, default=ACTIVE_PLAN)
    parser.add_argument("--reviewed-plan-sha256", default="")
    parser.add_argument("--run-ref", default="")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--harness-source-revision")
    for service in candidate.SOURCES:
        parser.add_argument("--" + service + "-source", type=Path)
    args = parser.parse_args(argv)
    try:
        reviewed = reviewed_identity(args.reviewed_plan_sha256, args.run_ref)
        if args.run:
            require(os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1", "AUTOMATIC_RERUN_REFUSED")
        _, documents = admit_plan(args.plan, reviewed)
        if args.check_plan:
            print(json.dumps({"status": "SOURCE_PLAN_ADMITTED", "native_execution": "NOT_RUN",
                              "plan_sha256": reviewed}, sort_keys=True))
            return 0
        require(args.work is not None and type(args.harness_source_revision) is str and
                candidate.COMMIT.fullmatch(args.harness_source_revision), "ACTIVE_ARGUMENTS")
        sources = {service: getattr(args, service + "_source") for service in candidate.SOURCES}
        require(all(path is not None and path.is_absolute() and path.resolve() == path
                    for path in sources.values()), "SOURCE_PATHS")
        require(os.getuid() > 0 and os.getgid() > 0 and platform.system() == "Linux" and platform.machine() == "x86_64", "SUPPORTED_HOST_REQUIRED")
        root = args.work.absolute()
        require(root.parent.is_dir() and root.parent.resolve() == root.parent and not root.exists(), "FRESH_WORK_REQUIRED")
        all_sources = [REPO, *sources.values()]
        require(not any(root.is_relative_to(path) or path.is_relative_to(root) for path in all_sources), "WORK_SOURCE_OVERLAP")
        require(all(not left.is_relative_to(right) and not right.is_relative_to(left)
                    for index, left in enumerate(all_sources) for right in all_sources[index + 1:]), "SOURCE_OVERLAP")
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "HOST_TIMER_CONFLICT")
        os.umask(0o077)
        root.mkdir(mode=0o700)
        trial = Trial(root, sources, reviewed, args.harness_source_revision, documents)
        def interrupted(signum, frame):
            raise Refused("HOST_TOTAL_DEADLINE" if signum == signal.SIGALRM else "HOST_INTERRUPTED")
        previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
        trial.alarm_owned = True
        signal.setitimer(signal.ITIMER_REAL, max(.000001, trial.work_deadline - time.monotonic()))
        try:
            return qualify(trial)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for fd in trial.pidfds.values():
                os.close(fd)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    except Exception as error:
        # Pre-admission refusals create nothing and cannot disclose paths or
        # native output. Post-admission detailed evidence stays private.
        print(json.dumps({"status": "REFUSED", "failure": fixed_code(error)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
