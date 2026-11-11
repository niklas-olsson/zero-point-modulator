"""Bounded observations for the installed child trial, without control effects.

Linux process counters are self counters. JSON byte counts are logical request
sizes, not transport bytes or tokens. File stats never open a SQLite connection.
Only aggregates, stable identities and hashes enter the shareable report.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time


MAX_INTEGER = 2**63 - 1
MAX_JSON_BYTES = 5_000_000
MAX_CAPTURE_ENTRIES = 4096
JOURNALS = {"canonical", "controller", "lifecycle", "runtime", "dispatcher", "children", "models"}
STORAGE_PHASES = (
    "prepared", "ready_target_zero", "idle_before_enrollment",
    "capture_verified", "cleanup_stopped", "restart_replayed",
)
TIMINGS = (
    "prepare", "verify", "bootstrap", "initial_manager_ready",
    "target_one_converged", "restart_manager_ready",
)


def require(value, message):
    if not value:
        raise AssertionError("operational measurement: " + message)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def elapsed(start, end):
    require(integer(start) and integer(end) and end >= start, "invalid monotonic interval")
    return end - start


def json_bytes(value):
    try:
        content = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise AssertionError("operational measurement: invalid JSON value") from None
    require(len(content) <= MAX_JSON_BYTES, "logical request exceeds the measurement bound")
    return len(content)


class RequestMeasurements:
    """Called under the fixture lock after its request/authentication checks."""

    def __init__(self, root_limit):
        require(type(root_limit) is int and root_limit in {6, 8}, "unexpected fixture limit")
        self.limits = {"root": root_limit, "left": 2, "right": 2}
        self.rows = []
        self.first = {}
        self.spawns = {}
        self.enrollment = None

    def enrollment_started(self, timestamp):
        require(self.enrollment is None and integer(timestamp), "duplicate enrollment anchor")
        self.enrollment = timestamp

    def request(self, actor, ordinal, payload, timestamp):
        require(actor in self.limits and integer(timestamp), "unknown request identity")
        previous = sum(row["actor"] == actor for row in self.rows)
        require(type(ordinal) is int and ordinal == previous + 1
                and ordinal <= self.limits[actor], "request ordinal changed")
        require(isinstance(payload, dict) and isinstance(payload.get("messages"), list)
                and isinstance(payload.get("tools"), list), "request context is malformed")
        if actor != "root":
            require(actor in self.spawns, "child request precedes its spawn response")
            elapsed(self.spawns[actor]["prepared_ns"], timestamp)
        self.first.setdefault(actor, timestamp)
        self.rows.append({
            "actor": actor, "ordinal": ordinal, "message_count": len(payload["messages"]),
            "messages_bytes": json_bytes(payload["messages"]),
            "tools_bytes": json_bytes(payload["tools"]), "request_bytes": json_bytes(payload),
        })

    def spawn_prepared(self, actor, instructions, timestamp):
        require(actor in {"left", "right"} and actor not in self.spawns
                and isinstance(instructions, str) and instructions and integer(timestamp),
                "spawn response anchor changed")
        self.spawns[actor] = {
            "prepared_ns": timestamp,
            "instructions_digest": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        }

    def report(self, child_ids, canonical_children, *, worker_id, task_id, attempt_id):
        require(self.enrollment is not None and set(self.first) == set(self.limits)
                and all(sum(row["actor"] == actor for row in self.rows) == limit
                        for actor, limit in self.limits.items()), "request sequence is incomplete")
        require(isinstance(child_ids, dict) and set(child_ids) == {"left", "right"}
                and len(set(child_ids.values())) == 2
                and isinstance(canonical_children, list) and len(canonical_children) == 2,
                "child lineage is incomplete")
        retained = {}
        for permission in canonical_children:
            require(isinstance(permission, dict), "child permission is malformed")
            child = permission.get("child", {})
            require(isinstance(child, dict) and isinstance(child.get("id"), str)
                    and child["id"] not in retained, "child lineage is duplicated")
            retained[child["id"]] = child
        require(set(retained) == set(child_ids.values()), "child identity changed")
        children = []
        for actor in ("left", "right"):
            child_id = child_ids[actor]
            binding = retained[child_id].get("binding", {})
            require(isinstance(binding, dict), "child binding is malformed")
            anchor = self.spawns[actor]
            require(binding.get("worker_id") == worker_id and binding.get("task_id") == task_id
                    and binding.get("root_attempt_id") == binding.get("parent_attempt_id") == attempt_id
                    and binding.get("instructions_digest") == anchor["instructions_digest"]
                    and isinstance(binding.get("grant_digest"), str)
                    and len(binding["grant_digest"]) == 64
                    and all(char in "0123456789abcdef" for char in binding["grant_digest"]),
                    "request label does not match the retained canonical grant")
            children.append({
                "actor": actor, "child_id": child_id, "grant_digest": binding["grant_digest"],
                "instructions_digest": anchor["instructions_digest"],
                "spawn_response_prepared_to_first_request_ns": elapsed(
                    anchor["prepared_ns"], self.first[actor]),
            })
        return {
            "serialization": "UTF-8 JSON; sorted keys; compact separators; ensure_ascii=false",
            "byte_scope": "logical JSON, not HTTP bytes, provider tokens or billing",
            "enrollment_to_first_root_request_ns": elapsed(self.enrollment, self.first["root"]),
            "requests": list(self.rows), "request_count": len(self.rows),
            "fresh_children": children,
        }


def proc_stat(content):
    require(isinstance(content, str) and len(content) <= 16384, "invalid process stat")
    left, right = content.find("("), content.rfind(")")
    require(left > 0 and right > left, "process stat command boundary is missing")
    try:
        pid = int(content[:left].strip())
        fields = content[right + 1:].split()
        require(len(fields) >= 22 and fields[0] not in {"Z", "X", "x"},
                "process is missing or exited")
        values = {
            "pid": pid, "ppid": int(fields[1]), "user_ticks": int(fields[11]),
            "system_ticks": int(fields[12]), "starttime_ticks": int(fields[19]),
            "rss_pages": int(fields[21]),
        }
    except (ValueError, IndexError):
        raise AssertionError("operational measurement: malformed process stat") from None
    require(all(integer(value) for value in values.values())
            and values["pid"] > 0 and values["starttime_ticks"] > 0,
            "process stat counters are invalid")
    return values


def _bounded_bytes(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, "process metadata exceeds its bound")
    return value


def _read_process(proc, pid, command, cwd):
    directory = proc / str(pid)
    before = proc_stat(_bounded_bytes(directory / "stat", 16384).decode("utf-8"))
    raw = _bounded_bytes(directory / "cmdline", 131072)
    require(raw.endswith(b"\0"), "process command is incomplete")
    argv = [part.decode("utf-8") for part in raw[:-1].split(b"\0")]
    require(argv == list(command) and os.readlink(directory / "cwd") == str(cwd),
            "process command or working directory changed")
    after = proc_stat(_bounded_bytes(directory / "stat", 16384).decode("utf-8"))
    require(before["pid"] == after["pid"] == pid
            and before["starttime_ticks"] == after["starttime_ticks"]
            and before["ppid"] == after["ppid"], "process identity changed during observation")
    return after


def _children(proc, pid):
    raw = _bounded_bytes(proc / str(pid) / "task" / str(pid) / "children", 4096)
    try:
        children = [int(value) for value in raw.split()]
    except ValueError:
        raise AssertionError("operational measurement: invalid child process list") from None
    require(len(children) == len(set(children)) == 3
            and all(integer(value, 1) for value in children), "manager service cohort changed")
    return children


def idle_process_sample(manager, commands, working_directories, *, proc=Path("/proc"),
                        clock=time.monotonic_ns, sleep=time.sleep):
    """One fixed two-second window; no provider, Docker or controller invocation."""
    roles = {"manager", "polyphony", "openjules", "zpm"}
    require(set(commands) == set(working_directories) == roles and manager.poll() is None,
            "owned manager or expected service cohort is missing")
    ticks, pages = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    require(integer(ticks, 1) and integer(pages, 1), "Linux counter units are unavailable")
    start = clock()
    before = {"manager": _read_process(proc, manager.pid, commands["manager"], working_directories["manager"])}
    child_pids = _children(proc, manager.pid)
    for pid in child_pids:
        matches = []
        for role in ("polyphony", "openjules", "zpm"):
            raw = _bounded_bytes(proc / str(pid) / "cmdline", 131072)
            if raw == b"\0".join(part.encode("utf-8") for part in commands[role]) + b"\0":
                matches.append(role)
        require(len(matches) == 1 and matches[0] not in before, "service command is ambiguous")
        role = matches[0]
        before[role] = _read_process(proc, pid, commands[role], working_directories[role])
        require(before[role]["ppid"] == manager.pid, "service left its original manager")
    require(set(before) == roles, "service cohort is incomplete")
    sleep(2)
    require(manager.poll() is None and sorted(_children(proc, manager.pid)) == sorted(child_pids),
            "manager or direct service cohort changed during idle")
    after = {role: _read_process(proc, original["pid"], commands[role], working_directories[role])
             for role, original in before.items()}
    stop = clock()
    rows = {}
    for role in ("manager", "polyphony", "openjules", "zpm"):
        first, last = before[role], after[role]
        require(first["pid"] == last["pid"]
                and first["starttime_ticks"] == last["starttime_ticks"]
                and first["ppid"] == last["ppid"], "idle process identity changed")
        user = elapsed(first["user_ticks"], last["user_ticks"])
        system = elapsed(first["system_ticks"], last["system_ticks"])
        rss_before, rss_after = first["rss_pages"] * pages, last["rss_pages"] * pages
        require(integer(rss_before) and integer(rss_after), "RSS byte count is invalid")
        rows[role] = {
            "pid": first["pid"], "starttime_ticks": first["starttime_ticks"],
            "user_ticks_delta": user, "system_ticks_delta": system,
            "rss_bytes_before": rss_before, "rss_bytes_after": rss_after,
        }
    return {
        "nominal_sleep_seconds": 2, "observation_ns": elapsed(start, stop),
        "clock_ticks_per_second": ticks, "page_size_bytes": pages, "processes": rows,
        "scope": "self user/system CPU and approximate endpoint RSS; excludes descendants and unrelated host/kernel work",
        "completed_reconcile_tick_proven": False, "peak_rss_measured": False,
    }


def _safe_path(bundle, path):
    require(bundle.is_absolute() and path.is_absolute() and ".." not in path.parts,
            "storage path is not an absolute owned path")
    try:
        relative = path.relative_to(bundle)
    except ValueError:
        raise AssertionError("operational measurement: storage path leaves the bundle") from None
    require(relative.parts, "storage path aliases the bundle")
    current = bundle
    for component in relative.parts[:-1]:
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        require(stat.S_ISDIR(info.st_mode), "storage parent is not a real directory")
        current = current / component
    try:
        info = current.lstat()
    except FileNotFoundError:
        return False
    require(stat.S_ISDIR(info.st_mode), "storage parent is not a real directory")
    return True


def _file_stat(bundle, path):
    if not _safe_path(bundle, path):
        return {"present": False, "bytes": 0}
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"present": False, "bytes": 0}
    require(stat.S_ISREG(info.st_mode) and integer(info.st_size), "journal is not a regular file")
    return {"present": True, "bytes": info.st_size}


def _capture_stat(bundle, path):
    absent = {"present": False, "regular_files": 0, "bytes": 0}
    if not _safe_path(bundle, path):
        return absent
    try:
        info = path.lstat()
    except FileNotFoundError:
        return absent
    require(stat.S_ISDIR(info.st_mode), "capture root is not a real directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    total, files, entries = 0, 0, 0

    def visit(directory, depth=0):
        nonlocal total, files, entries
        require(depth <= 16, "capture inventory exceeds its depth bound")
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                require(entries <= MAX_CAPTURE_ENTRIES, "capture inventory exceeds its fixed bound")
                info = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    nested = os.open(child.name, flags, dir_fd=directory)
                    try:
                        visit(nested, depth + 1)
                    finally:
                        os.close(nested)
                else:
                    require(stat.S_ISREG(info.st_mode) and integer(info.st_size),
                            "capture inventory contains a nonregular entry")
                    files += 1
                    total += info.st_size
                    require(integer(total), "capture byte count is invalid")

    descriptor = os.open(path, flags)
    try:
        visit(descriptor)
    finally:
        os.close(descriptor)
    return {"present": True, "regular_files": files, "bytes": total}


def storage_snapshot(bundle, journals, captures):
    bundle = Path(bundle)
    require(set(journals) == JOURNALS and len(set(map(str, journals.values()))) == 7,
            "journal inventory changed or aliases a path")
    files = {}
    for name in sorted(journals):
        path = Path(journals[name])
        files[name] = {
            suffix: _file_stat(bundle, Path(str(path) + ending))
            for suffix, ending in (("main", ""), ("wal", "-wal"), ("shm", "-shm"))
        }
    return {"journals": files, "captures": _capture_stat(bundle, Path(captures))}


class Measurements:
    def __init__(self, *, clock=time.monotonic_ns):
        self.clock, self.started, self.timings, self.storage = clock, {}, {}, []

    def start(self, name):
        require(name in TIMINGS and name not in self.started and name not in self.timings,
                "timing phase was repeated")
        self.started[name] = self.clock()

    def stop(self, name):
        require(name in self.started, "timing phase was not started")
        self.timings[name] = elapsed(self.started.pop(name), self.clock())

    def storage_phase(self, label, bundle, journals, captures):
        require(len(self.storage) < len(STORAGE_PHASES)
                and label == STORAGE_PHASES[len(self.storage)], "storage phase order changed")
        self.storage.append({"phase": label, **storage_snapshot(bundle, journals, captures)})

    def report(self, idle, requests):
        require(not self.started and set(self.timings) == set(TIMINGS)
                and tuple(row["phase"] for row in self.storage) == STORAGE_PHASES,
                "required observations are incomplete")
        require(idle["observation_ns"] > 0 and requests["request_count"] in {10, 12},
                "required idle/request observations are invalid")
        deltas = []
        for before, after in zip(self.storage, self.storage[1:]):
            deltas.append({
                "from": before["phase"], "to": after["phase"],
                "journal_bytes": {
                    name: {part: after["journals"][name][part]["bytes"]
                           - before["journals"][name][part]["bytes"] for part in ("main", "wal", "shm")}
                    for name in sorted(JOURNALS)
                },
                "capture_bytes": after["captures"]["bytes"] - before["captures"]["bytes"],
                "capture_files": after["captures"]["regular_files"] - before["captures"]["regular_files"],
            })
        result = {
            "schema_version": 1,
            "evidence": "deterministic model fixture with actual installed services and Docker",
            "timing_unit": "monotonic nanoseconds", "observed_intervals_ns": dict(self.timings),
            "idle": idle, "context": requests, "storage": self.storage, "storage_deltas": deltas,
            "limits": [
                "fresh child delegation includes canonical grant, scheduling and broker work",
                "storage stats are separate observations, not an atomic snapshot or allocated disk blocks",
                "WAL and SHM can shrink or disappear without data loss",
                "no warm child session, second instruction round, public dialogue or live provider measurement",
                "no provider token usage, monetary cost or performance threshold is inferred",
            ],
        }
        require(json_bytes(result) <= 100_000, "aggregate report exceeds its bound")
        return result
