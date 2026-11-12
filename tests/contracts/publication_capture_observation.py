"""Failure-only observations of the original publication capture, never recovery."""

from __future__ import annotations

import ast
from contextlib import closing, contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import tarfile

from publication_fixture import ROOT_VERIFY

MAX_JSON = 131072
MAX_LOG = 262144
MAX_SUITE_LOG = 2_000_000
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_TEST_FILE = 256 * 1024
MAX_TEST_TOTAL = 2 * 1024 * 1024
MAX_TEST_IDS = 1024
MAX_FAILURES = 16
DIGEST = re.compile(r"[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")

CAPTURE_REASONS = {
    "canonical capture scope changed": "scope",
    "canonical task collection is malformed": "task-collection",
    "canonical attempt collection is malformed": "attempt-collection",
    "canonical candidate collection is malformed": "candidate-collection",
    "canonical capture task is missing or duplicated": "task-cardinality",
    "canonical task has duplicate attempts": "attempt-cardinality",
    "runtime self-authorized acceptance": "self-acceptance",
    "canonical task has duplicate candidates": "candidate-cardinality",
    "canonical capture disappeared or candidate preceded its attempt": "capture-order",
    "captured attempt changed its runtime binding": "runtime-binding",
    "canonical termination flag is malformed": "termination-flag",
    "terminal capture regressed or candidate preceded final capture": "terminal-order",
    "captured attempt lacks a terminal state or launch fence": "terminal-fence",
    "canonical capture is incomplete or malformed": "artifacts",
    "canonical capture changed while awaiting candidate publication": "changed-capture",
    "candidate changed its task or attempt": "candidate-identity",
    "candidate changed its source base or capture": "candidate-source",
    "candidate is not the task's canonical review request": "candidate-review-state",
    "deadline waiting for terminal capture publication final candidate": "terminal-capture-deadline",
    "deadline waiting for candidate publication publication final candidate": "candidate-publication-deadline",
    "a trial service exited; inspect retained service logs": "owned-service-exit",
}


class _Unavailable(Exception):
    pass


def _need(condition, reason):
    if not condition:
        raise _Unavailable(reason)


def _integer(value, maximum=MAX_TEST_IDS, minimum=0):
    return value if type(value) is int and minimum <= value <= maximum else None


def _boolean(value):
    return value if type(value) is bool else None


def _state(value, allowed):
    return value if type(value) is str and value in allowed else "other"


def _exit(value, *, present=True):
    code = _integer(value, 255, -255)
    return {"kind": "missing" if not present else "null" if value is None else
            "integer" if code is not None else "other", "value": code}


def _json(raw):
    _need(type(raw) is str and len(raw.encode("utf-8")) <= MAX_JSON, "json-limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "json-shape")
            result[key] = value
        return result

    def reject(_value):
        raise _Unavailable("json-shape")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def capture_reason(error):
    if type(error) is AssertionError and len(error.args) == 1 and type(error.args[0]) is str:
        return CAPTURE_REASONS.get(error.args[0], "other")
    return "other"


def canonical_capture_observation(value, scope, task, worker, attempt, base):
    """Return a safe latest-read summary and a private optional manifest digest."""
    if type(value) is not dict:
        return {"available": True, "valid": False, "scope_matches": False}, None
    result = {"available": True, "valid": True,
              "scope_matches": all(value.get(key) == wanted for key, wanted in scope.items()), "counts": {}}
    collections = {}
    for kind in ("task", "attempt", "candidate"):
        rows = value.get(kind)
        fields = {"task": ("id",), "attempt": ("id", "task", "worker"),
                  "candidate": ("id", "task", "attempt", "base", "patch")}[kind]
        valid = (type(rows) is list and len(rows) <= 2 and all(type(row) is dict and all(
            type(row.get(key)) is str and bool(row[key]) for key in fields) for row in rows))
        result["counts"][kind] = {"value": len(rows) if type(rows) is list and len(rows) <= 2 else None,
                                  "over_limit": type(rows) is list and len(rows) > 2, "valid": valid}
        result["valid"] = result["valid"] and valid
        collections[kind] = rows if valid else []
    tasks = [row for row in collections["task"] if row.get("id") == task]
    attempts = [row for row in collections["attempt"] if row.get("id") == attempt and row.get("task") == task]
    result.update(original_task_present=len(tasks) == 1 if result["counts"]["task"]["valid"] else None,
                  original_attempt_present=len(attempts) == 1 if result["counts"]["attempt"]["valid"] else None)
    if len(tasks) == 1:
        result["task_state"] = _state(tasks[0].get("status"), {
            "open", "running", "awaiting_review", "accepted", "failed", "cancelled", "timed_out",
        })
    manifest_digest = None
    if len(attempts) == 1:
        row = attempts[0]
        binding = row.get("runtime_binding")
        bound = (type(binding) is dict and all(binding.get(key) == wanted for key, wanted in
                 (scope | {"worker_id": worker, "attempt_id": attempt, "base": base}).items()))
        artifacts = row.get("captured_artifacts")
        complete = (type(artifacts) is dict and set(artifacts) == {"patch", "log", "manifest"}
                    and all(type(item) is str and DIGEST.fullmatch(item) for item in artifacts.values()))
        candidates = [item for item in collections["candidate"] if item.get("task") == task
                      or item.get("attempt") == attempt]
        result.update(runtime_binding_matches=bound,
                      attempt_state=_state(row.get("state"), {"running", "exited", "timed_out"}),
                      termination_confirmed=_boolean(row.get("termination_confirmed")),
                      launch_fenced=_boolean(row.get("launch_fenced")), artifacts_complete=bool(complete),
                      candidate_present=bool(candidates) if result["counts"]["candidate"]["valid"] else None,
                      current_candidate_matches=(len(tasks) == len(candidates) == 1
                                                 and type(candidates[0].get("id")) is str and bool(candidates[0]["id"])
                                                 and tasks[0].get("current_candidate") == candidates[0].get("id"))
                      if result["counts"]["task"]["valid"] and result["counts"]["candidate"]["valid"] else None)
        if result["scope_matches"] and bound and complete:
            manifest_digest = artifacts["manifest"]
    return result, manifest_digest


@contextmanager
def _regular(path, maximum, minimum=0):
    """Refuse symlinks in every path component and nonregular final files."""
    path = Path(path)
    _need(path.is_absolute() and len(path.parts) <= 64 and ".." not in path.parts, "unsafe-path")
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(descriptor)
        _need(stat.S_ISREG(info.st_mode) and minimum <= info.st_size <= maximum, "file-limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            yield stream, info
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _read(path, maximum, minimum=0):
    with _regular(path, maximum, minimum) as (stream, info):
        raw = stream.read(maximum + 1)
        _need(len(raw) == info.st_size, "file-changed")
    return raw


def approved_test_ids(source, base):
    """Read only pinned Git metadata and blob-matching host ASTs, never imports."""
    try:
        _need(type(base) is str and SHA.fullmatch(base), "source-base")
        source = Path(source)
        _need(source.is_absolute() and stat.S_ISDIR(os.stat(source / ".git", follow_symlinks=False).st_mode),
              "source-git-directory")
        listing = subprocess.run(
            ["git", "-C", str(source), "ls-tree", "-z", base + ":tests"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
            env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                 "GIT_TERMINAL_PROMPT": "0"},
        )
        _need(listing.returncode == 0 and len(listing.stdout) <= 65536, "source-tree")
        records = listing.stdout.split(b"\0")
        _need(len(records) <= 129, "source-tree-limit")
        identities, total, files = set(), 0, 0
        for record in records:
            if not record:
                continue
            metadata, name = record.decode("utf-8").split("\t", 1)
            if not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", name):
                continue
            mode, kind, blob = metadata.split()
            _need(mode in {"100644", "100755"} and kind == "blob" and SHA.fullmatch(blob), "source-entry")
            files += 1
            _need(files <= 64, "source-file-count")
            raw = _read(source / "tests" / name, MAX_TEST_FILE)
            total += len(raw)
            _need(total <= MAX_TEST_TOTAL, "source-total-limit")
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            _need(actual == blob, "source-blob-mismatch")
            parsed = ast.parse(raw.decode("utf-8"))
            for node in parsed.body:
                if not isinstance(node, ast.ClassDef) or not IDENTIFIER.fullmatch(node.name):
                    continue
                for method in node.body:
                    if (isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
                            and IDENTIFIER.fullmatch(method.name)):
                        identities.add("tests." + name[:-3] + "." + node.name + "." + method.name)
                        _need(len(identities) <= MAX_TEST_IDS, "source-id-count")
        _need(files > 0 and identities, "source-empty")
        return frozenset(identities), {"available": True, "files": files, "ids": len(identities)}
    except _Unavailable as error:
        return None, {"available": False, "reason": error.args[0]}
    except BaseException:
        return None, {"available": False, "reason": "source-unavailable"}


def _combined(output):
    frames, script_lines = [], []
    malformed = 0
    lines = output.splitlines()
    for line in lines[:1024]:
        frame = re.fullmatch(r'\s*File "<string>", line ([0-9]{1,3}), in <module>', line)
        if frame and 1 <= int(frame[1]) <= len(ROOT_VERIFY[3].splitlines()) and len(script_lines) < 4:
            script_lines.append(int(frame[1]))
        if not line.startswith("PUBLICATION_COMBINED_RESULT "):
            continue
        try:
            value = _json(line.removeprefix("PUBLICATION_COMBINED_RESULT "))
            elapsed = value.get("elapsed_seconds") if type(value) is dict else None
            _need(type(value) is dict and set(value) == {"tests", "expected_tests", "elapsed_seconds", "exit_code"}
                  and _integer(value["tests"]) is not None and _integer(value["expected_tests"]) is not None
                  and type(elapsed) in {int, float} and math.isfinite(elapsed) and 0 <= elapsed <= 120
                  and _integer(value["exit_code"], 255, -255) is not None, "combined-shape")
            if len(frames) < 4:
                frames.append(value)
            else:
                malformed += 1
        except (ValueError, TypeError, RecursionError, _Unavailable):
            malformed += 1
    return {"combined_frames": frames, "unrecognized_or_extra_frames": min(malformed, 1024),
            "output_scan_limited": len(lines) > 1024,
            "observed_in_range_string_frame_lines": script_lines}


def verification_log_observation(output_bytes, suite_bytes, approved_ids):
    """Describe observed bounded frames; never copy arbitrary log strings."""
    result = {"output_log": {"available": True, "present": output_bytes is not None},
              "suite_log": {"available": True, "present": suite_bytes is not None}}
    if output_bytes is not None:
        _need(type(output_bytes) is bytes and len(output_bytes) <= MAX_LOG, "output-log-limit")
        lines = output_bytes.decode("utf-8", errors="replace").splitlines()
        details, count, unparsed = [], 0, 0
        for line in lines[:1024]:
            try:
                event = _json(line)
            except (ValueError, UnicodeError, RecursionError, _Unavailable):
                unparsed += 1
                continue
            if type(event) is not dict or event.get("event") != "configured_verification":
                continue
            count += 1
            if len(details) >= 4:
                continue
            output = event.get("output")
            detail = {"state": _state(event.get("state"), {"exited", "timed_out", "output_limit", "unknown"}),
                      "exit_code": _exit(event.get("exit_code"), present="exit_code" in event)}
            if type(output) is str and len(output.encode("utf-8")) <= 65536:
                detail.update(_combined(output))
            else:
                detail["output_available"] = False
            details.append(detail)
        result["output_log"].update(available=True, bytes=len(output_bytes), scan_limited=len(lines) > 1024,
                                    configured_events_observed=count, details=details,
                                    details_limited=count > len(details), unparsed_lines_observed=unparsed)
    if suite_bytes is not None:
        _need(type(suite_bytes) is bytes and len(suite_bytes) <= MAX_SUITE_LOG, "suite-log-limit")
        lines = suite_bytes.decode("utf-8", errors="replace").splitlines()
        suite = result["suite_log"]
        suite.update(available=True, bytes=len(suite_bytes), scan_limited=len(lines) > 20000,
                     source_ids_available=approved_ids is not None, ran_summary=None, result=None,
                     observed_progress_count=0 if approved_ids is not None else None,
                     observed_completed_progress_count=0 if approved_ids is not None else None,
                     progress_count_limited=False, last_progress_id=None, last_progress_status=None,
                     known_failures=[], unknown_failure_records=0, unknown_failure_records_limited=False,
                     failure_details_limited=False, ran_summary_observations=0, result_summary_observations=0,
                     unrecognized_summary_records=0, summary_count_limited=False)
        known, seen = approved_ids or frozenset(), set()
        ran_position, result_position = None, None

        def summary_count(field):
            if suite[field] == MAX_TEST_IDS:
                suite["summary_count_limited"] = True
            suite[field] = min(suite[field] + 1, MAX_TEST_IDS)

        def failure(identity, category):
            if identity not in known:
                if suite["unknown_failure_records"] == MAX_TEST_IDS:
                    suite["unknown_failure_records_limited"] = True
                suite["unknown_failure_records"] = min(suite["unknown_failure_records"] + 1, MAX_TEST_IDS)
            elif (identity, category) not in seen:
                seen.add((identity, category))
                if len(suite["known_failures"]) < MAX_FAILURES:
                    suite["known_failures"].append({"test_id": identity, "result": category})
                else:
                    suite["failure_details_limited"] = True

        for position, line in enumerate(lines[:20000]):
            progress = re.fullmatch(r"(test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\) \.\.\.(.*)", line)
            if progress and progress[2] in known and progress[2].rsplit(".", 1)[-1] == progress[1]:
                tail = progress[3].strip()
                status = tail if tail in {"ok", "FAIL", "ERROR", "expected failure", "unexpected success"} else "incomplete"
                if tail.startswith("skipped "):
                    status = "skipped"
                if suite["observed_progress_count"] == MAX_TEST_IDS:
                    suite["progress_count_limited"] = True
                suite.update(observed_progress_count=min(suite["observed_progress_count"] + 1, MAX_TEST_IDS),
                             last_progress_id=progress[2], last_progress_status=status)
                if status != "incomplete":
                    suite["observed_completed_progress_count"] = min(suite["observed_completed_progress_count"] + 1, MAX_TEST_IDS)
                if status in {"FAIL", "ERROR"}:
                    failure(progress[2], status)
            # Only the approved identity prefix is retained. Subtest arguments
            # after it are private even when they caused the failure.
            header = re.match(r"^(FAIL|ERROR): (test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)(?:$| \()", line)
            if header:
                failure(header[3] if header[3].rsplit(".", 1)[-1] == header[2] else None, header[1])
            elif line.startswith(("FAIL: ", "ERROR: ")):
                failure(None, "other")
            ran = re.fullmatch(r"Ran ([0-9]{1,4}) tests? in ([0-9]+(?:\.[0-9]+)?)s", line)
            if ran and int(ran[1]) <= MAX_TEST_IDS and 0 <= float(ran[2]) <= 120:
                summary_count("ran_summary_observations")
                ran_position = position
                suite["ran_summary"] = {"tests": int(ran[1]), "seconds": float(ran[2])}
            elif line.startswith("Ran "):
                summary_count("unrecognized_summary_records")
            summary = re.fullmatch(r"(OK|FAILED)(?: \(([^()]*)\))?", line)
            if summary:
                counts = {}
                valid = True
                for item in summary[2].split(", ") if summary[2] else []:
                    match = re.fullmatch(r"(failures|errors|skipped|expected failures|unexpected successes)=([0-9]{1,4})", item)
                    if not match or int(match[2]) > MAX_TEST_IDS or match[1] in counts:
                        valid = False
                        break
                    counts[match[1]] = int(match[2])
                if valid:
                    summary_count("result_summary_observations")
                    result_position = position
                    suite["result"] = {"state": "ok" if summary[1] == "OK" else "failed", "counts": counts}
                else:
                    summary_count("unrecognized_summary_records")
        suite["summary_ambiguous"] = (suite["ran_summary_observations"] > 1 or suite["result_summary_observations"] > 1
                                      or suite["unrecognized_summary_records"] > 0
                                      or (ran_position is not None and result_position is not None
                                          and result_position < ran_position))
        suite["complete_summary_observed"] = (suite["ran_summary_observations"] == suite["result_summary_observations"] == 1
                                              and not suite["summary_ambiguous"] and not suite["scan_limited"])
        if suite["summary_ambiguous"] or suite["scan_limited"]:
            suite["ran_summary"] = suite["result"] = None
    return result


def _archive_logs(raw):
    selected, seen = {}, set()
    targets = {"workspace/run/output.log": MAX_LOG, "workspace/run/publication-unittest.log": MAX_SUITE_LOG}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for index, member in enumerate(archive):
            name = member.name
            path = PurePosixPath(name)
            _need(index < 8192 and type(name) is str and len(name) <= 512 and "\\" not in name
                  and not path.is_absolute() and ".." not in path.parts and "\0" not in name
                  and (name == "workspace" or name.startswith("workspace/")), "archive-member")
            key = name.rstrip("/")
            _need(key not in seen, "archive-duplicate")
            seen.add(key)
            if name in targets:
                _need(member.isfile() and 0 <= member.size <= targets[name], "archive-log-member")
                with archive.extractfile(member) as stream:
                    content = stream.read(targets[name] + 1)
                _need(len(content) == member.size, "archive-log-changed")
                selected[name] = content
    return selected.get("workspace/run/output.log"), selected.get("workspace/run/publication-unittest.log")


def runtime_capture_observation(context, canonical_manifest_digest):
    """One bounded read of original host state. No runtime construction or polling."""
    result = {"available": False}
    try:
        directory = context["directory"]
        database_path = directory / "journal.sqlite3"
        with _regular(database_path, MAX_ARCHIVE, 1) as (_stream, before):
            with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True, timeout=0)) as database:
                database.set_progress_handler(lambda: 1, 10000)
                workers = database.execute(
                    "SELECT worker_id,CASE WHEN length(CAST(spec AS BLOB))<=? THEN spec END,"
                    "profile_fingerprint,container_id,CASE WHEN length(CAST(receipt AS BLOB))<=? THEN receipt END,"
                    "length(CAST(receipt AS BLOB)) FROM workers WHERE worker_id=? LIMIT 2",
                    (MAX_JSON, MAX_JSON, context["worker"]),
                ).fetchall()
                attempts = database.execute(
                    "SELECT worker_id,attempt_id,exit_code FROM attempts WHERE worker_id=? AND attempt_id=? LIMIT 2",
                    (context["worker"], context["attempt"]),
                ).fetchall()
            after = os.stat(database_path, follow_symlinks=False)
            _need(stat.S_ISREG(after.st_mode) and (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
                  "journal-changed")
        _need(len(workers) == len(attempts) == 1 and workers[0][0] == context["worker"]
              and attempts[0][:2] == (context["worker"], context["attempt"]), "original-rows-unavailable")
        worker, spec_raw, fingerprint, container, receipt_raw, receipt_size = workers[0]
        spec = _json(spec_raw)
        _need(type(spec) is dict and all(spec.get(key) == value for key, value in
              (context["scope"] | {"profile": context["profile"]}).items()), "runtime-binding")
        _need(type(fingerprint) is str and DIGEST.fullmatch(fingerprint)
              and fingerprint == context["profile_fingerprint"], "profile-fingerprint")
        result = {"available": True, "identity_matches": True, "attempt_exit_code": _exit(attempts[0][2]),
                  "receipt_present": receipt_size is not None,
                  "archive": {"available": False, "reason": "receipt-absent"}}
        if receipt_size is None:
            return result
        _need(type(receipt_size) is int and 0 < receipt_size <= MAX_JSON, "receipt-limit")
        receipt = _json(receipt_raw)
        _need(type(receipt) is dict, "receipt-shape")
        result.update(receipt_state=_state(receipt.get("state"), {
            "ready", "running", "starting", "completed", "cancelled", "failed", "unknown", "timed_out",
        }), receipt_exit_code=_exit(receipt.get("exit_code"), present="exit_code" in receipt),
            termination_confirmed=_boolean(receipt.get("termination_confirmed")),
            launch_fenced=_boolean(receipt.get("launch_fenced")),
            artifacts_captured=_boolean(receipt.get("artifacts_captured")))
        manifest_raw = receipt.get("manifest")
        manifest = _json(manifest_raw)
        _need(type(manifest) is dict and all(manifest.get(key) == value for key, value in {
            "worker_id": worker, "attempt_id": context["attempt"], "base_commit": context["base"],
            "profile": context["profile"], "profile_fingerprint": fingerprint, "container_id": container,
        }.items()), "manifest-binding")
        _need(canonical_manifest_digest is None or
              hashlib.sha256(manifest_raw.encode()).hexdigest() == canonical_manifest_digest, "canonical-manifest")
        _need(all(receipt.get(key) is True for key in ("termination_confirmed", "launch_fenced", "artifacts_captured"))
              and manifest.get("termination_confirmed") is True and manifest.get("launch_fenced") is True,
              "capture-not-final")
        archive_path = directory / "captures" / (hashlib.sha256(worker.encode()).hexdigest() + ".tar")
        digest = receipt.get("capture_sha256")
        _need(receipt.get("capture_path") == str(archive_path) and type(digest) is str and DIGEST.fullmatch(digest)
              and manifest.get("archive_sha256") == digest, "archive-binding")
        raw = _read(archive_path, context["archive_limit"], 1)
        _need(type(manifest.get("archive_bytes")) is int and manifest["archive_bytes"] == len(raw)
              and hashlib.sha256(raw).hexdigest() == digest, "archive-hash")
        output, suite = _archive_logs(raw)
        _need(type(manifest.get("log_present")) is bool and manifest["log_present"] == (output is not None),
              "manifest-log-presence")
        if output is not None:
            _need(type(receipt.get("log")) is str and output.decode("utf-8", errors="replace") == receipt["log"],
                  "receipt-log")
        ids, inventory = approved_test_ids(context["source"], context["base"]) if suite is not None else (
            None, {"available": False, "reason": "suite-absent"})
        result["archive"] = {"available": True, "identity_and_hash_verified": True,
                             "canonical_manifest_bound": canonical_manifest_digest is not None,
                             "source_inventory": inventory, **verification_log_observation(output, suite, ids)}
    except _Unavailable as error:
        result["archive" if result.get("available") else "observation"] = {"available": False, "reason": error.args[0]}
    except BaseException:
        result["archive" if result.get("available") else "observation"] = {"available": False, "reason": "unavailable"}
    return result


def _profile_fingerprint(profile):
    """Pinned c925 DockerProfile.asdict normalization, without importing it."""
    fields = ("name", "deployment_id", "repository", "source_path", "image", "base_commit")
    _need(all(type(profile.get(key)) is str and 0 < len(profile[key]) <= 4096 for key in fields), "profile-shape")
    value = {key: profile[key] for key in fields}
    command = profile.get("command")
    _need(type(command) is list and 0 < len(command) <= 128
          and all(type(item) is str and 0 < len(item) <= 4096 for item in command), "profile-command")
    value["command"] = list(command)
    defaults = {"uid": (65532, 1, 65535), "memory_bytes": (536870912, 16777216, 1 << 40),
                "cpu_quota": (100000, 1000, 1 << 31), "pids_limit": (128, 8, 65536),
                "max_seconds": (300, 1, 86400), "max_archive_bytes": (16777216, 1024, 1 << 30)}
    for key, (default, minimum, maximum) in defaults.items():
        item = profile.get(key, default)
        _need(_integer(item, maximum, minimum) is not None, "profile-bound")
        value[key] = item
    refs = profile.get("environment_refs", {})
    _need(type(refs) is dict and len(refs) <= 64 and all(type(key) is str and type(item) is str
          and len(key) <= 128 and len(item) <= 1024 for key, item in refs.items()), "profile-environment")
    value["environment_refs"] = sorted([key, item] for key, item in refs.items())
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _need(len(raw) <= MAX_JSON, "profile-limit")
    return hashlib.sha256(raw).hexdigest()


class RootCaptureObservation:
    """Passive binding and last-read cache; local inspection only on failure."""

    def __init__(self):
        self._context = None
        self._snapshot = {"available": False, "reason": "not-observed"}
        self._manifest_digest = None

    def bind(self, *, config, scope, task, worker, attempt, base, source):
        try:
            _need(type(config) is dict and type(config.get("profiles")) is list and len(config["profiles"]) == 1,
                  "context")
            profile = config["profiles"][0]
            _need(type(profile) is dict and type(scope) is dict and set(scope) == {"deployment_id", "repository"}
                  and all(type(value) is str and 0 < len(value) <= 1024 for value in [task, worker, attempt, *scope.values()])
                  and type(base) is str and SHA.fullmatch(base), "context")
            _need(profile.get("base_commit") == base and profile.get("source_path") == str(source)
                  and all(profile.get(key) == value for key, value in scope.items())
                  and type(profile.get("name")) is str and 0 < len(profile["name"]) <= 128
                  and type(profile.get("max_archive_bytes")) is int and profile["max_archive_bytes"] > 0,
                  "context")
            directory = Path(config["state_directory"]) / "runtime"
            _need(directory.is_absolute() and Path(source).is_absolute(), "context")
            self._context = {"directory": directory, "scope": dict(scope), "task": task, "worker": worker,
                             "attempt": attempt, "base": base, "profile": profile["name"], "source": Path(source),
                             "profile_fingerprint": _profile_fingerprint(profile),
                             "archive_limit": min(profile["max_archive_bytes"], MAX_ARCHIVE)}
        except BaseException:
            self._context = None

    def observe_snapshot(self, value):
        try:
            _need(self._context is not None, "context")
            context = self._context
            self._snapshot, self._manifest_digest = canonical_capture_observation(
                value, context["scope"], context["task"], context["worker"], context["attempt"], context["base"],
            )
        except BaseException:
            self._snapshot, self._manifest_digest = {"available": False, "reason": "unavailable"}, None

    def failure(self, error):
        try:
            return {"reason": capture_reason(error), "non_atomic_observation": True, "canonical": self._snapshot,
                    "runtime": runtime_capture_observation(self._context, self._manifest_digest)
                    if self._context is not None else {"available": False, "reason": "context-unavailable"}}
        except BaseException:
            return {"reason": "other", "available": False}
