"""Full public-source suite in the pinned Docker producer, without managed work.

Readiness is an explicit fixture. There is no authority, broker, child grant or
forge credential. Only report.json may be uploaded; native captures stay private.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from uuid import uuid4

MAX_REPORT = 131072
MAX_FAILURES = 64
MAX_TESTS = 1024
MAX_TIMINGS = 16
SUITE_SECONDS = 110
COMMAND_SECONDS = 120
SHA = re.compile(r"[0-9a-f]{40}")
KINDS = {"assertion", "os", "timeout", "process", "value", "runtime", "lookup", "other"}
COUNTERS = {
    "pids.current": (), "pids.max": (), "pids.events": ("max",),
    "memory.current": (), "memory.max": (),
    "memory.events": ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"),
    "cpu.stat": ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec"),
}
CACHE_MODULES = {"json": json, "subprocess": subprocess, "tempfile": tempfile, "unittest": unittest}


class Deadline(BaseException):
    """Escape producer Exception handlers so the host's total guard is final."""


def need(value):
    if not value:
        raise AssertionError("confined suite invariant failed")


def integer(value, maximum=(1 << 63) - 1, minimum=0):
    return value if type(value) is int and minimum <= value <= maximum else None


def read_bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode))
        data = stream.read(limit + 1)
        need(len(data) <= limit)
        return data


def save(path, value):
    encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    need(len(encoded) <= MAX_REPORT)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
    os.replace(temporary, path)


def git(source, *arguments):
    result = subprocess.run(
        ["git", "-C", str(source), *arguments], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=5,
        env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_TERMINAL_PROMPT": "0"},
    )
    need(result.returncode == 0 and len(result.stdout) <= 131072)
    return result.stdout


def source_inventory(root, base):
    from publication_capture_observation import approved_test_ids

    ids, observation = approved_test_ids(root, base)
    need(ids is not None and observation["available"])
    files, total = {}, 0
    for row in git(root, "ls-tree", "-r", "-z", base).split(b"\0"):
        if not row:
            continue
        metadata, name = row.decode().split("\t", 1)
        if not name.endswith(".py"):
            continue
        mode, kind, blob = metadata.split()
        need(mode in {"100644", "100755"} and kind == "blob" and SHA.fullmatch(blob)
             and re.fullmatch(r"(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py", name))
        data = read_bounded(root / name, 262144)
        total += len(data)
        need(total <= 4_000_000 and len(files) < 128)
        need(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == blob)
        files[name] = len(data.splitlines())
    return ids, files


def exception_kind(error):
    for kind, types in (
        ("assertion", (AssertionError,)), ("timeout", (Deadline, TimeoutError, subprocess.TimeoutExpired)),
        ("process", (subprocess.CalledProcessError,)), ("os", (OSError,)),
        ("value", (ValueError, TypeError)), ("runtime", (RuntimeError,)), ("lookup", (LookupError,)),
    ):
        if isinstance(error, types):
            return kind
    return "other"


def failure(error, traceback, root, files):
    """No messages, args, commands, locals or arbitrary traceback filenames."""
    frames, traversed = [], 0
    while traceback is not None and traversed < 64:
        traversed += 1
        try:
            name = str(Path(traceback.tb_frame.f_code.co_filename).resolve().relative_to(root))
        except (ValueError, OSError):
            name = ""
        line = traceback.tb_lineno
        if name in files and integer(line, files[name], 1) is not None:
            frames.append({"file": name, "line": line})
        traceback = traceback.tb_next
    return {"kind": exception_kind(error), "errno": integer(getattr(error, "errno", None), 4096),
            "returncode": integer(getattr(error, "returncode", None), 255, -255), "frames": frames[-6:]}


def resources(cgroup=Path("/sys/fs/cgroup"), proc=Path("/proc")):
    result = {}
    for name, keys in COUNTERS.items():
        try:
            raw = read_bounded(cgroup / name, 4096).decode("ascii")
            values = dict(line.split() for line in raw.splitlines()) if keys else {"value": raw.strip()}
            for key in keys or ("value",):
                text = values.get(key, "")
                result[name + ":" + key] = integer(int(text)) if re.fullmatch(r"[0-9]{1,19}", text) else None
        except (OSError, ValueError, AssertionError):
            for key in keys or ("value",):
                result[name + ":" + key] = None
    processes = zombies = unreadable = 0
    try:
        for index, entry in enumerate(proc.iterdir()):
            need(index < 2048)
            if not entry.name.isdigit():
                continue
            processes += 1
            try:
                fields = read_bounded(entry / "stat", 4096).rsplit(b")", 1)[1].split()
                zombies += fields[0] == b"Z"
            except (OSError, IndexError, AssertionError):
                unreadable += 1
                continue
        result.update(processes=processes, zombies=zombies if unreadable == 0 else None,
                      process_stats_unavailable=unreadable)
    except (OSError, AssertionError):
        result.update(processes=None, zombies=None, process_stats_unavailable=None)
    return result


def bytecode_cache():
    observation = {"dont_write_bytecode": bool(sys.dont_write_bytecode)}
    for name, module in CACHE_MODULES.items():
        cached = getattr(module, "__cached__", None)
        observation[name] = isinstance(cached, str) and Path(cached).is_file()
    return observation


class BoundedLog:
    """Keep raw private unittest output bounded without skipping any test."""

    def __init__(self, stream):
        self.stream, self.bytes, self.limited = stream, 0, False

    def write(self, value):
        encoded = value.encode("utf-8", errors="replace")
        remaining = max(0, 2_000_000 - self.bytes)
        self.stream.write(encoded[:remaining])
        self.bytes += min(len(encoded), remaining)
        self.limited |= len(encoded) > remaining

    def flush(self):
        self.stream.flush()


class ObservedResult(unittest.TextTestResult):
    def __init__(self, *args, report, destination, root, files, approved, suite_started, **kwargs):
        super().__init__(*args, **kwargs)
        self.report, self.destination, self.root, self.files, self.approved = report, destination, root, files, approved
        self.started = []
        self.suite_started, self.test_started = suite_started, None

    def startTest(self, test):
        need(test.id() in self.approved and len(self.started) < MAX_TESTS)
        need(self.report["progress"]["active_test_id"] is None)
        self.started.append(test.id())
        self.test_started = time.monotonic()
        self.report["progress"].update(started=len(self.started), active_test_id=test.id(),
                                       elapsed_seconds=round(self.test_started - self.suite_started, 3))
        save(self.destination, self.report)
        super().startTest(test)

    def stopTest(self, test):
        need(self.report["progress"]["active_test_id"] == test.id() and self.test_started is not None)
        now = time.monotonic()
        timings = self.report["slowest_completed"]
        timings.append({"test_id": test.id(), "seconds": round(now - self.test_started, 3)})
        timings.sort(key=lambda row: (-row["seconds"], row["test_id"]))
        del timings[MAX_TIMINGS:]
        self.report["progress"].update(completed=self.report["progress"]["completed"] + 1,
                                       active_test_id=None, elapsed_seconds=round(now - self.suite_started, 3))
        save(self.destination, self.report)
        self.test_started = None
        super().stopTest(test)

    def observe(self, test, error, outcome):
        need(test.id() in self.approved)
        self.report["failure_count"] += 1
        if len(self.report["failures"]) < MAX_FAILURES:
            self.report["failures"].append({"test_id": test.id(), "outcome": outcome,
                                            **failure(error[1], error[2], self.root, self.files),
                                            "resources": resources()})
        self.report["failures_limited"] = self.report["failure_count"] > MAX_FAILURES
        save(self.destination, self.report)

    def addError(self, test, error):
        self.observe(test, error, "error")
        super().addError(test, error)

    def addFailure(self, test, error):
        self.observe(test, error, "failure")
        super().addFailure(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.observe(test, error, "subtest")
        super().addSubTest(test, subtest, error)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item.id()


def run_suite(base, destination):
    root = Path.cwd().resolve()
    approved, files = source_inventory(root, base)
    # Discovery has the same start directory as the original publication verifier.
    suite = unittest.TestLoader().discover(".")
    identities = list(flatten(suite))
    need(len(identities) == len(approved) and set(identities) == approved)
    report = {"complete": False, "passed": False, "expected": len(approved), "executed": 0,
              "inventory_sha256": hashlib.sha256("\n".join(sorted(approved)).encode()).hexdigest(),
              "executed_inventory_sha256": None, "log_limited": False,
              "failure_count": 0, "failures": [], "failures_limited": False,
              "skipped": 0, "expected_failures": 0, "unexpected_successes": 0,
              "progress": {"started": 0, "completed": 0, "active_test_id": None, "elapsed_seconds": 0},
              "slowest_completed": [],
              "bytecode_cache": bytecode_cache(),
              "resources_before": resources(), "resources_after": None}
    save(destination, report)
    started = time.monotonic()
    with (destination.parent / "confined-unittest.log").open("wb") as output:
        bounded = BoundedLog(output)
        result = unittest.TextTestRunner(
            stream=bounded, verbosity=2,
            resultclass=lambda *a, **kw: ObservedResult(*a, report=report, destination=destination,
                                                       root=root, files=files, approved=approved,
                                                       suite_started=started, **kw),
        ).run(suite)
    complete = result.testsRun == len(approved) and sorted(result.started) == sorted(approved)
    report.update(complete=complete, executed=result.testsRun, skipped=len(result.skipped),
                  expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
                  resources_after=resources(), seconds=round(time.monotonic() - started, 3),
                  executed_inventory_sha256=hashlib.sha256("\n".join(sorted(result.started)).encode()).hexdigest(),
                  log_limited=bounded.limited)
    report["passed"] = complete and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not bounded.limited
    save(destination, report)
    return 0 if report["passed"] else 1


def guest(base):
    from publication_fixture import CONTENTS, FILES

    need(os.getuid() == os.getgid() == 65532 and Path.cwd() == Path("/workspace/run/repo"))
    need(not Path("/var/run/docker.sock").exists() and not Path("/source/.git").exists())
    run = Path("/workspace/run")
    info = run.lstat()
    need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())
    temporary = run / "publication-unittest-tmp"
    temporary.mkdir(mode=0o700)
    for name, relative in FILES.items():
        with open(relative, "x") as output:
            output.write(CONTENTS[name])
    environment = dict(os.environ, TMPDIR=str(temporary))
    # Whole-container termination fences descendants even if the suite process
    # prints a summary and then hangs in interpreter/thread shutdown.
    try:
        result = subprocess.run(["python3", __file__, "suite", base], env=environment,
                                timeout=SUITE_SECONDS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124


def safe_suite(value, approved, files):
    """Rebuild the guest projection before exposing any data to the host log."""
    need(type(value) is dict)
    output = {}
    for key in ("complete", "passed", "failures_limited", "log_limited"):
        need(type(value.get(key)) is bool)
        output[key] = value[key]
    for key in ("expected", "executed", "failure_count", "skipped", "expected_failures", "unexpected_successes"):
        output[key] = integer(value.get(key), 10000)
        need(output[key] is not None)
    expected_hash = hashlib.sha256("\n".join(sorted(approved)).encode()).hexdigest()
    need(value.get("inventory_sha256") == expected_hash and output["expected"] == len(approved))
    output["inventory_sha256"] = expected_hash
    output["executed_inventory_sha256"] = expected_hash if value.get("executed_inventory_sha256") == expected_hash else None
    need(not output["complete"] or output["executed_inventory_sha256"] == expected_hash)
    seconds = value.get("seconds")
    output["seconds"] = seconds if type(seconds) in (int, float) and 0 <= seconds <= SUITE_SECONDS else None
    cache = value.get("bytecode_cache")
    need(type(cache) is dict and set(cache) == {"dont_write_bytecode", *CACHE_MODULES}
         and all(type(present) is bool for present in cache.values()))
    output["bytecode_cache"] = dict(cache)
    progress = value.get("progress")
    need(type(progress) is dict)
    begun = integer(progress.get("started"), len(approved))
    completed = integer(progress.get("completed"), len(approved))
    active = progress.get("active_test_id")
    elapsed = progress.get("elapsed_seconds")
    need(begun is not None and completed is not None and completed <= begun
         and type(elapsed) in (int, float) and 0 <= elapsed <= SUITE_SECONDS)
    need((active is None and begun == completed)
         or (type(active) is str and active in approved and begun == completed + 1))
    need(not output["complete"] or (active is None and completed == output["executed"] == len(approved)))
    output["progress"] = {"started": begun, "completed": completed, "active_test_id": active,
                          "elapsed_seconds": elapsed}
    timings = value.get("slowest_completed")
    need(type(timings) is list and len(timings) == min(MAX_TIMINGS, completed))
    output["slowest_completed"] = []
    seen = set()
    for row in timings:
        need(type(row) is dict and type(row.get("test_id")) is str
             and row["test_id"] in approved and row["test_id"] != active and row["test_id"] not in seen
             and type(row.get("seconds")) in (int, float) and 0 <= row["seconds"] <= elapsed)
        seen.add(row["test_id"])
        output["slowest_completed"].append({"test_id": row["test_id"], "seconds": row["seconds"]})
    counter_keys = {name + ":" + key for name, keys in COUNTERS.items() for key in keys or ("value",)} | {"processes", "zombies", "process_stats_unavailable"}

    def counters(row):
        return {key: integer(row.get(key)) for key in sorted(counter_keys)} if type(row) is dict else None

    for key in ("resources_before", "resources_after"):
        output[key] = counters(value.get(key))
    rows = value.get("failures")
    need(type(rows) is list and len(rows) <= MAX_FAILURES)
    output["failures"] = []
    for row in rows:
        need(type(row) is dict and row.get("test_id") in approved and row.get("kind") in KINDS
             and row.get("outcome") in {"error", "failure", "subtest"})
        frames = row.get("frames")
        need(type(frames) is list and len(frames) <= 6)
        selected = []
        for frame in frames:
            need(type(frame) is dict and frame.get("file") in files
                 and integer(frame.get("line"), files[frame["file"]], 1) is not None)
            selected.append({"file": frame["file"], "line": frame["line"]})
        output["failures"].append({"test_id": row["test_id"], "kind": row["kind"], "outcome": row["outcome"],
                                   "errno": integer(row.get("errno"), 4096),
                                   "returncode": integer(row.get("returncode"), 255, -255),
                                   "frames": selected, "resources": counters(row.get("resources"))})
    need(not output["passed"] or (output["complete"] and not output["log_limited"] and output["executed"] == len(approved)
                                  and all(output[key] == 0 for key in ("failure_count", "skipped", "expected_failures", "unexpected_successes"))))
    return output


def captured_suite(container, approved, files):
    chunks, _metadata = container.get_archive("/workspace/run/confined-suite.json")
    raw = bytearray()
    try:
        for chunk in chunks:
            need(len(raw) + len(chunk) <= MAX_REPORT + 16384)
            raw.extend(chunk)
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        need(len(members) == 1 and members[0].name == "confined-suite.json"
             and members[0].isfile() and members[0].size <= MAX_REPORT)
        data = archive.extractfile(members[0]).read(MAX_REPORT + 1)
    return safe_suite(json.loads(data), approved, files)


def cleanup_fixture(runtime, worker, spec, original, not_found):
    """This fixture has no canonical state. Never apply this to managed work."""
    row = runtime._row(worker, spec)
    if row is None:
        return {"complete": True, "container_removed": False, "volume_removed": False}
    need(row["container_id"])
    if original is not None:
        need(row["container_id"] == original["container"])
    container = runtime._container(worker, spec, row)
    if container.attrs["State"].get("Running"):
        container.stop(timeout=2)
        container.reload()
    # Even incomplete setup must fence an acknowledged, identity-checked
    # diagnostic container. Missing volume/original evidence still blocks removal.
    need(original is not None and row["volume_identity"])
    need(json.loads(row["volume_identity"]) == original["volume"])
    container = runtime._cleanup_container(worker, spec, original["container"], original["volume"]["Name"])
    volume = runtime._cleanup_volume(worker, spec, original["volume"])
    container.remove(force=False)
    volume.remove(force=False)
    for getter, identity in ((runtime.client.containers.get, original["container"]),
                             (runtime.client.volumes.get, original["volume"]["Name"])):
        try:
            getter(identity)
        except not_found:
            continue
        raise AssertionError("confined fixture removal is not confirmed")
    return {"complete": True, "container_removed": True, "volume_removed": True}


def host(args):
    root = args.work_dir.absolute()
    root.mkdir(mode=0o700)
    source = Path(__file__).resolve().parents[2]
    openjules = args.openjules_checkout.resolve(strict=True)
    revisions = {name: git(path, "rev-parse", "HEAD").decode().strip()
                 for name, path in (("zpm", source), ("openjules", openjules))}
    need(all(SHA.fullmatch(value) for value in revisions.values()))
    approved, files = source_inventory(source, revisions["zpm"])
    sys.path.insert(0, str(openjules))
    from src.worker import docker_runtime
    from src.worker.lifecycle import RuntimeCapabilities, WorkerSpec

    need(Path(docker_runtime.__file__).resolve() == openjules / "src/worker/docker_runtime.py")
    need(re.fullmatch(r"sha256:[0-9a-f]{64}", args.image))
    profile = docker_runtime.DockerProfile(
        name="confined-suite-fixture", deployment_id="confined-suite-" + uuid4().hex,
        repository="github:github.com/niklas-olsson/zero-point-modulator", source_path=str(source),
        base_commit=revisions["zpm"], image=args.image, max_seconds=COMMAND_SECONDS,
        command=("python3", "-I", "tests/contracts/check_confined_suite.py", "guest", revisions["zpm"]),
    )
    need((profile.uid, profile.memory_bytes, profile.cpu_quota, profile.pids_limit, profile.environment_refs)
         == (65532, 536870912, 100000, 128, ()))
    # Synthetic readiness only for a fresh public-source fixture. No dispatcher
    # or canonical task is created, and this says nothing about installed readiness.
    runtime = docker_runtime.ManagedDockerRuntime(root / "runtime", [profile],
        lambda scope, name: RuntimeCapabilities(True, True, True, "explicit public-source fixture"))
    worker = "confined-suite-" + uuid4().hex
    spec = WorkerSpec(profile.deployment_id, profile.repository, "fixture", 1, worker, profile.name)
    report = {"schema_version": 1, "passed": False, "component_revisions": revisions, "image": args.image,
              "proof": "public-source fixture through pinned Docker producer; synthetic readiness; no authority/model/children/forge",
              "suite_seconds": SUITE_SECONDS, "command_seconds": COMMAND_SECONDS,
              "suite_source": "bounded fresh read of stopped diagnostic container, separate from native capture",
              "suite": None, "runtime": None, "cleanup": {"complete": False}, "failure_kind": None}
    original = None

    def expired(*_args):
        raise Deadline()

    old_handler = signal.signal(signal.SIGALRM, expired)
    signal.alarm(180)
    try:
        observation = runtime.ensure(worker, spec)
        need(observation.state == "ready")
        row = runtime._row(worker, spec)
        container = runtime._container(worker, spec, row)
        volume_identity = json.loads(row["volume_identity"])
        runtime._cleanup_volume(worker, spec, volume_identity)
        original = {"container": container.id, "volume": volume_identity}
        need(runtime.model_broker is None and not runtime.child_policies and runtime.child_supervisor is None)
        receipt = runtime.execute_attempt(worker, spec, "confined-suite", "", COMMAND_SECONDS)
        report["runtime"] = {"exit_code": integer(receipt.exit_code, 255, -255),
                             "termination_confirmed": receipt.termination_confirmed is True,
                             "artifacts_captured": receipt.artifacts_captured is True,
                             "launch_fenced": receipt.launch_fenced is True}
        container.reload()
        need(not container.attrs["State"].get("Running"))
        report["suite"] = captured_suite(container, approved, files)
        report["passed"] = report["suite"]["passed"] and receipt.exit_code == 0 and all(
            report["runtime"][key] for key in ("termination_confirmed", "artifacts_captured", "launch_fenced"))
    except (Exception, Deadline) as error:
        report["failure_kind"] = exception_kind(error)
    finally:
        signal.alarm(20)
        try:
            report["cleanup"] = cleanup_fixture(runtime, worker, spec, original, docker_runtime.NotFound)
        except (Exception, Deadline) as error:
            report["cleanup"] = {"complete": False, "failure_kind": exception_kind(error)}
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        report["passed"] = report["passed"] and report["cleanup"]["complete"]
        save(root / "report.json", report)
    return report


def main():
    if len(sys.argv) == 3 and sys.argv[1] in {"guest", "suite"}:
        need(SHA.fullmatch(sys.argv[2]))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        if sys.argv[1] == "guest":
            return guest(sys.argv[2])
        return run_suite(sys.argv[2], Path("/workspace/run/confined-suite.json"))
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openjules-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = host(args)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["passed"] else 1
    except Exception as error:
        print(json.dumps({"passed": False, "failure_kind": exception_kind(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
