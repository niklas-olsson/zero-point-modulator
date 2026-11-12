"""One synthetic confined full-suite fixture; no model or canonical task work."""

from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import http.client
import importlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile
import time
import textwrap
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError, URLError
from uuid import uuid4


OPENJULES_REVISION = "c925dee46765d2b22c9e38b55f614fc29f8068c1"
MAX_REPORT = 131072
MAX_FAILURES = 16
MAX_LINKS = 4
KNOWN_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.ENOENT, errno.ENOEXEC,
                        errno.ENOMEM, errno.ENOSPC, errno.EPERM, errno.EROFS,
                        errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT,
                        errno.EMFILE, errno.ENFILE, errno.EADDRINUSE, errno.EADDRNOTAVAIL,
                        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE})
CATEGORIES = {AssertionError: "assertion", OSError: "os", PermissionError: "permission",
              FileNotFoundError: "file-not-found", BlockingIOError: "would-block",
              ConnectionError: "connection", ConnectionRefusedError: "connection-refused",
              ConnectionResetError: "connection-reset", TimeoutError: "timeout",
              RuntimeError: "runtime", ValueError: "value", TypeError: "type", KeyError: "key",
              ImportError: "import", ModuleNotFoundError: "module-not-found",
              subprocess.TimeoutExpired: "process-timeout", subprocess.CalledProcessError: "process-exit",
              HTTPError: "http", URLError: "url", http.client.RemoteDisconnected: "remote-disconnected",
              http.client.BadStatusLine: "http-status-line", http.client.IncompleteRead: "http-incomplete-read",
              http.client.HTTPException: "http-protocol", socket.gaierror: "address-resolution"}
CATEGORY_VALUES = frozenset(CATEGORIES.values()) | {"provider", "configuration", "thread-start", "other"}
RUN = Path("/workspace/run")
CONFIG = RUN / "confined-suite-input.json"
REPORT = RUN / "confined-suite.json"
DRIVER = RUN / "confined-suite-driver.py"


def require(value):
    if not value:
        raise ValueError("confined suite contract refused")


def bounded_integer(value, maximum=1000000):
    return type(value) is int and 0 <= value <= maximum


def exception_chain(error):
    """Read built-in exception slots, never arbitrary messages or accessors."""
    rows, seen, relation = [], set(), "raised"
    while isinstance(error, BaseException) and len(rows) < MAX_LINKS and id(error) not in seen:
        seen.add(id(error))
        category = CATEGORIES.get(type(error), "other")
        if type(error) is RuntimeError:
            arguments = BaseException.args.__get__(error)
            if len(arguments) == 1 and type(arguments[0]) is str and arguments[0] == "can't start new thread":
                category = "thread-start"
        for module_name, class_name, selected in (
            ("zpm.provider", "ProviderError", "provider"),
            ("zpm.models", "ConfigurationError", "configuration"),
        ):
            module = sys.modules.get(module_name)
            if module is not None and type(error) is vars(module).get(class_name):
                category = selected
        number = None
        if type(error) in CATEGORIES and isinstance(error, OSError):
            value = OSError.errno.__get__(error)
            if type(value) is int and value in KNOWN_ERRNOS:
                number = value
        suppressed = BaseException.__suppress_context__.__get__(error)
        rows.append({"category": category, "errno": number, "relation": relation,
                     "context_suppressed": suppressed is True})
        cause = BaseException.__cause__.__get__(error)
        error = cause if cause is not None else BaseException.__context__.__get__(error)
        relation = "cause" if cause is not None else "context"
    return {"chain": rows, "cycle": error is not None and id(error) in seen,
            "limited": error is not None and id(error) not in seen}


def approved_identity(test, approved):
    try:
        identity = ".".join((type(test).__module__, type(test).__name__,
                             object.__getattribute__(test, "_testMethodName")))
        return identity if identity in approved else None
    except BaseException:
        return None


def small_read(path, maximum=4096):
    with Path(path).open("rb") as stream:
        value = stream.read(maximum + 1)
    require(len(value) <= maximum)
    return value


def resource_observation(watchdog_hash, scopes=None, proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup")):
    scopes = scopes or {}
    result = {"pid_namespace_matches": None, "pid1_watchdog_matches": None,
              "pid_scope_verified": False, "cgroup_scope_verified": False,
              "processes": {"available": False},
              "cgroup": {"current": None, "maximum": None, "events_max": None}}
    try:
        result["pid_namespace_matches"] = os.readlink(proc / "self/ns/pid") == os.readlink(proc / "1/ns/pid")
    except (OSError, ValueError):
        pass
    try:
        result["pid1_watchdog_matches"] = hashlib.sha256(small_read(proc / "1/cmdline", 65536)).hexdigest() == watchdog_hash
    except (OSError, ValueError):
        pass
    result["pid_scope_verified"] = scopes.get("private_pid_namespace") is True and result["pid_namespace_matches"] is not False
    if result["pid_scope_verified"]:
        try:
            counts = {"inspected": 0, "zombies": 0, "adopted_by_pid1": 0, "pid1_zombies": 0}
            for number, path in enumerate(proc.iterdir()):
                require(number < 1024)
                if not path.name.isdecimal():
                    continue
                require(counts["inspected"] < 512)
                try:
                    fields = small_read(path / "stat").rsplit(b")", 1)[1].split()
                except FileNotFoundError:
                    continue
                require(len(fields) >= 20 and fields[1].isdigit())
                zombie, adopted = fields[0] == b"Z", fields[1] == b"1"
                counts["inspected"] += 1
                counts["zombies"] += int(zombie)
                counts["adopted_by_pid1"] += int(adopted)
                counts["pid1_zombies"] += int(zombie and adopted)
            result["processes"] = {"available": True, **counts}
        except (OSError, ValueError, IndexError):
            pass
    try:
        require(scopes.get("private_cgroup_namespace") is True and small_read(proc / "self/cgroup", 256).strip() == b"0::/")
        mounts = [line.split() for line in small_read(proc / "self/mountinfo", 65536).splitlines()]
        selected = [fields for fields in mounts if len(fields) >= 10 and fields[4] == b"/sys/fs/cgroup"]
        require(len(selected) == 1 and selected[0][3] == b"/" and selected[0][selected[0].index(b"-") + 1] == b"cgroup2")
        result["cgroup_scope_verified"] = True
    except (OSError, ValueError, IndexError):
        pass
    if not result["cgroup_scope_verified"]:
        return result
    for name, file in (("current", "pids.current"), ("maximum", "pids.max"), ("events_max", "pids.events")):
        try:
            raw = small_read(cgroup / file, 256).decode("ascii").strip()
            if name == "events_max":
                require(re.fullmatch(r"max [0-9]+", raw) is not None)
                raw = raw.split()[1]
            require(raw.isdecimal() and int(raw) <= 1000000)
            result["cgroup"][name] = int(raw)
        except (OSError, ValueError, UnicodeError):
            pass
    return result


def atomic_report(path, value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    require(len(raw) <= MAX_REPORT)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ObservedResult(unittest.TextTestResult):
    def __init__(self, *args, approved, sample, publish, **kwargs):
        super().__init__(*args, **kwargs)
        self.approved, self.sample, self.publish = approved, sample, publish
        self.observations, self.unknown_ids = [], 0
        self.details_limited = self.diagnostics_unavailable = False

    def observe(self, test, err, kind):
        try:
            identity = approved_identity(test, self.approved)
            if identity is None:
                self.unknown_ids = min(self.unknown_ids + 1, 1024)
            elif len(self.observations) < MAX_FAILURES:
                self.observations.append({"test_id": identity, "kind": kind,
                                          **exception_chain(err[1]), "resources": self.sample()})
            else:
                self.details_limited = True
            self.publish(self, False)
        except BaseException:
            self.diagnostics_unavailable = True

    def addError(self, test, err):
        super().addError(test, err)
        self.observe(test, err, "error")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.observe(test, err, "failure")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.observe(test, err, "subtest")


def suite_main(config):
    approved = frozenset(config["approved_ids"])
    sample = lambda: resource_observation(config["watchdog_hash"], config["scopes"])
    before = sample()
    discovered, inventory_matches = None, False

    def publish(result, complete):
        atomic_report(REPORT, {
            "schema_version": 1, "source": config["source"], "expected_tests": len(approved),
            "discovered_tests": discovered, "inventory_matches": inventory_matches,
            "driver_sha256": config["driver_sha256"],
            "complete": complete, "successful": complete and result.wasSuccessful(),
            "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "observations": result.observations, "unknown_ids": result.unknown_ids,
            "details_limited": result.details_limited, "diagnostics_unavailable": result.diagnostics_unavailable,
            "before": before, "after": sample() if complete else None,
        })

    runner = unittest.TextTestRunner(verbosity=2, resultclass=lambda *args, **kwargs:
        ObservedResult(*args, approved=approved, sample=sample, publish=publish, **kwargs))
    publish(runner._makeResult(), False)
    expected = unittest.defaultTestLoader.discover(".")
    identities = []
    def visit(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                visit(test)
            else:
                identities.append(approved_identity(test, approved))
    visit(expected)
    discovered = expected.countTestCases()
    inventory_matches = (len(identities) == discovered == len(approved) and set(identities) == approved)
    result = runner.run(expected)
    publish(result, True)
    return 0 if result.wasSuccessful() else 1


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def guest_main(mode):
    config = json.loads(small_read(CONFIG, MAX_REPORT))
    require(os.getuid() == 65532 and os.getpid() != 1)
    require(hashlib.sha256(small_read(__file__, MAX_REPORT)).hexdigest() == config["driver_sha256"])
    if mode == "suite":
        return suite_main(config)
    require(mode == "verify")
    sys.path.insert(0, str(Path.cwd() / "tests/contracts"))
    fixture = importlib.import_module("publication_fixture")
    tools = load_file("_confined_image_runner", "/opt/openjules/managed_runner.py").LocalTools(Path.cwd(), command_seconds=120)
    for name, content in fixture.CONTENTS.items():
        tools.write_file(fixture.FILES[name], content)
    original = "['python3','-m','unittest','discover','-v']"
    require(fixture.ROOT_VERIFY[-1].count(original) == 1)
    command = list(fixture.ROOT_VERIFY)
    command[-1] = command[-1].replace(original, repr(["python3", str(DRIVER), "--guest", "suite"]))
    result = json.loads(tools.run_command(command))
    print(json.dumps({"event": "configured_verification", **result}), flush=True)
    return 0 if result.get("state") == "exited" and type(result.get("exit_code")) is int and result["exit_code"] == 0 else 1


def validate_resources(value):
    require(type(value) is dict and set(value) == {"pid_namespace_matches", "pid1_watchdog_matches", "pid_scope_verified", "cgroup_scope_verified", "processes", "cgroup"})
    require(type(value["pid_scope_verified"]) is bool and type(value["cgroup_scope_verified"]) is bool)
    require(all(value[key] is None or type(value[key]) is bool for key in ("pid_namespace_matches", "pid1_watchdog_matches")))
    processes = value["processes"]
    require(type(processes) is dict and type(processes.get("available")) is bool)
    keys = {"available", "inspected", "zombies", "adopted_by_pid1", "pid1_zombies"} if processes["available"] else {"available"}
    require(set(processes) == keys and all(bounded_integer(processes[key], 512) for key in keys - {"available"}))
    require(not processes["available"] or value["pid_scope_verified"])
    group = value["cgroup"]
    require(type(group) is dict and set(group) == {"current", "maximum", "events_max"})
    require(all(item is None or bounded_integer(item) for item in group.values()))
    require(value["cgroup_scope_verified"] or all(item is None for item in group.values()))


def validate_guest(value, source, approved, driver_hash):
    keys = {"schema_version", "source", "expected_tests", "complete", "successful", "tests_run", "failures", "errors",
            "skipped", "expected_failures", "unexpected_successes", "observations", "unknown_ids", "details_limited",
            "diagnostics_unavailable", "before", "after", "discovered_tests", "inventory_matches", "driver_sha256"}
    require(type(value) is dict and set(value) == keys and type(value["schema_version"]) is int and value["schema_version"] == 1)
    require(value["source"] == source and type(value["expected_tests"]) is int and value["expected_tests"] == len(approved))
    require(value["driver_sha256"] == driver_hash and (value["discovered_tests"] is None or bounded_integer(value["discovered_tests"], 1024)))
    for key in ("complete", "successful", "details_limited", "diagnostics_unavailable", "inventory_matches"):
        require(type(value[key]) is bool)
    for key in ("tests_run", "failures", "errors", "skipped", "expected_failures", "unexpected_successes", "unknown_ids"):
        require(bounded_integer(value[key], 1000000))
    validate_resources(value["before"])
    require(not value["complete"] or value["after"] is not None)
    if value["after"] is not None:
        validate_resources(value["after"])
    require(type(value["observations"]) is list and len(value["observations"]) <= MAX_FAILURES)
    for row in value["observations"]:
        require(type(row) is dict and set(row) == {"test_id", "kind", "chain", "cycle", "limited", "resources"})
        require(type(row["test_id"]) is str and row["test_id"] in approved and row["kind"] in {"error", "failure", "subtest"})
        require(type(row["cycle"]) is bool and type(row["limited"]) is bool and type(row["chain"]) is list and len(row["chain"]) <= MAX_LINKS)
        for link in row["chain"]:
            require(type(link) is dict and set(link) == {"category", "errno", "relation", "context_suppressed"})
            require(link["category"] in CATEGORY_VALUES and link["relation"] in {"raised", "cause", "context"})
            require(link["errno"] is None or (type(link["errno"]) is int and link["errno"] in KNOWN_ERRNOS))
            require(type(link["context_suppressed"]) is bool)
        validate_resources(row["resources"])
    require(not value["successful"] or (value["complete"] and value["tests_run"] == value["discovered_tests"]
            and value["failures"] == value["errors"] == value["unexpected_successes"] == 0))
    return value


def git(path, *arguments):
    result = subprocess.run(["git", "-C", str(path), *arguments], capture_output=True, timeout=30)
    require(result.returncode == 0 and len(result.stdout) <= MAX_REPORT)
    return result.stdout.decode().strip()


def container_options(profile, watchdog, source, volume, labels):
    require((profile.uid, profile.memory_bytes, profile.cpu_quota, profile.pids_limit, profile.max_seconds)
            == (65532, 536870912, 100000, 128, 240))
    return dict(entrypoint=["python3", "-I", "-S", "-c", watchdog], command=[], user="65534:65534",
                working_dir="/", detach=True, network_mode="none", read_only=True, cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"], privileged=False, mem_limit=profile.memory_bytes,
                memswap_limit=profile.memory_bytes, cpu_period=100000, cpu_quota=profile.cpu_quota,
                pids_limit=profile.pids_limit, volumes={str(source): {"bind": "/source", "mode": "ro"},
                volume: {"bind": "/workspace", "mode": "rw"}},
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                       "/control": "rw,noexec,nosuid,nodev,size=65536,uid=65534,gid=65534,mode=0700"},
                restart_policy={"Name": "no"}, environment={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}, labels=labels)


def source_boundary(create_source):
    """Refuse drift in the pinned production launch fields this fixture uses."""
    tree = ast.parse(textwrap.dedent(create_source))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "self.client.containers.create"]
    require(len(calls) == 1)
    keywords = {item.arg: item.value for item in calls[0].keywords}
    expected = {"entrypoint": "['python3','-I','-S','-c',watchdog]", "command": "[]",
                "user": "f'{_WATCHDOG_UID}:{_WATCHDOG_UID}'", "working_dir": "'/'",
                "network_mode": "'none'", "read_only": "True", "cap_drop": "['ALL']",
                "security_opt": "['no-new-privileges:true']", "privileged": "False",
                "mem_limit": "profile.memory_bytes", "memswap_limit": "profile.memory_bytes",
                "cpu_period": "100000", "cpu_quota": "profile.cpu_quota", "pids_limit": "profile.pids_limit",
                "restart_policy": "{'Name':'no'}", "environment": "{'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'}",
                "tmpfs": "{'/tmp':f'rw,noexec,nosuid,nodev,size=16777216,mode=1777',"
                         "'/control':f'rw,noexec,nosuid,nodev,size=65536,uid={_WATCHDOG_UID},gid={_WATCHDOG_UID},mode=0700'}"}
    require(all(name in keywords and ast.dump(keywords[name]) == ast.dump(ast.parse(value, mode="eval").body)
                for name, value in expected.items()))
    require(any(isinstance(node, ast.If) and ast.unparse(node.test) == "key in self.child_policies"
                and any(isinstance(item, ast.Assign) and ast.unparse(item) == "watchdog = CHILD_WATCHDOG"
                        for item in node.body) for node in ast.walk(tree)))


def inspected_boundary(container, image, source, volume, watchdog, image_environment):
    container.reload()
    data, config = container.attrs["HostConfig"], container.attrs["Config"]
    require(container.attrs["Image"] == image and config["User"] == "65534:65534"
            and config["Entrypoint"] == ["python3", "-I", "-S", "-c", watchdog]
            and config.get("Cmd") in ([], None) and config["WorkingDir"] == "/")
    environment = dict(item.split("=", 1) for item in config["Env"])
    require(len(environment) == len(config["Env"]) and environment == image_environment | {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
    require(data["NetworkMode"] == "none" and data["ReadonlyRootfs"] is True and data["Privileged"] is False
            and data["Memory"] == data["MemorySwap"] == 536870912 and data["CpuPeriod"] == data["CpuQuota"] == 100000
            and data["PidsLimit"] == 128 and not data.get("Init") and data.get("PidMode", "") == ""
            and data.get("CapAdd") in (None, []) and data["CapDrop"] == ["ALL"]
            and data["SecurityOpt"] == ["no-new-privileges:true"]
            and data["Tmpfs"] == {"/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                "/control": "rw,noexec,nosuid,nodev,size=65536,uid=65534,gid=65534,mode=0700"})
    mounts = {item["Destination"]: item for item in container.attrs["Mounts"]}
    require({"/source", "/workspace"} <= set(mounts) <= {"/source", "/workspace", "/tmp", "/control"}
            and len(mounts) == len(container.attrs["Mounts"]) and mounts["/source"]["Source"] == str(source)
            and mounts["/source"]["RW"] is False and mounts["/workspace"]["Name"] == volume and mounts["/workspace"]["RW"] is True)
    require(all(mounts[name]["Type"] == "tmpfs" for name in set(mounts) & {"/tmp", "/control"}))
    return {"private_pid_namespace": True, "private_cgroup_namespace": data.get("CgroupnsMode") == "private"}


def captured_report(container, source, approved, driver_hash):
    stream, metadata = container.get_archive(str(REPORT))
    require(bounded_integer(metadata.get("size"), MAX_REPORT))
    content = bytearray()
    for chunk in stream:
        content.extend(chunk)
        require(len(content) <= MAX_REPORT + 20480)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        members = archive.getmembers()
        require(len(members) == 1 and members[0].isfile() and members[0].name == REPORT.name and members[0].size <= MAX_REPORT)
        raw = archive.extractfile(members[0]).read(MAX_REPORT + 1)
    return validate_guest(json.loads(raw), source, approved, driver_hash)


def qualification_passed(exit_code, guest, expected):
    samples = [guest["before"], guest["after"], *(row["resources"] for row in guest["observations"])]
    consistent = all(sample is not None and sample["pid_namespace_matches"] is not False
                     and sample["pid1_watchdog_matches"] is not False
                     and (not sample["cgroup_scope_verified"] or sample["cgroup"]["maximum"] in (None, 128))
                     for sample in samples)
    return (type(exit_code) is int and exit_code == 0 and guest["complete"] and guest["successful"]
            and guest["inventory_matches"] and guest["tests_run"] == expected
            and guest["skipped"] == guest["expected_failures"] == 0
            and not guest["diagnostics_unavailable"] and guest["unknown_ids"] == 0 and consistent)


def cleanup_owned(client, runtime, missing, container, volume, container_id, volume_identity, labels, intents):
    if (intents["volume"] and volume is None) or (intents["container"] and container is None):
        return False  # A lost creation reply is not proof that nothing exists.
    try:
        if container is not None:
            require(runtime.ManagedDockerRuntime._cleanup_transport(SimpleNamespace(client=client)) == runtime._CLEANUP_TRANSPORT)
            container.reload()
            require(container.id == container_id and container.attrs["Id"] == container_id
                    and all(container.attrs["Config"]["Labels"].get(key) == value for key, value in labels.items()))
            if container.attrs["State"]["Running"]:
                container.kill()
            if container.attrs["State"].get("Status") != "created":
                stopped = container.wait(timeout=5)
                require(type(stopped) is dict and type(stopped.get("StatusCode")) is int)
            container.reload()
            state = container.attrs["State"]
            require(state["Running"] is False and not state.get("Paused") and not state.get("Restarting")
                    and state["Status"] in {"created", "exited", "dead"})
            container.remove(force=False, v=False)
            try:
                client.containers.get(container_id)
                return False
            except missing:
                pass
        if volume is not None:
            require(runtime.ManagedDockerRuntime._cleanup_transport(SimpleNamespace(client=client)) == runtime._CLEANUP_TRANSPORT)
            volume.reload()
            require(runtime.ManagedDockerRuntime._volume_identity(volume) == volume_identity and volume_identity["Labels"] == labels)
            volume.remove(force=False)
            try:
                client.volumes.get(volume.name)
                return False
            except missing:
                pass
        return True
    except BaseException:
        return False


def host_main(args):
    source = Path(__file__).resolve().parents[2]
    checkout = args.openjules_checkout.resolve()
    root = args.work_dir.absolute()
    require(root.resolve() == root and not root.exists() and source not in root.parents and checkout not in root.parents)
    root.mkdir(mode=0o700)
    report = {"schema_version": 1, "fixture": "confined-full-suite", "passed": False,
              "stage": "source", "guest": None, "cleanup_complete": False,
              "topology": "fixed-composition-and-synthetic-child-deadlines", "canonical_publication_qualified": False}
    client = container = volume = None
    volume_intent = container_intent = False
    volume_identity = container_id = None
    labels = {"zpm.confined-suite": uuid4().hex}
    try:
        revision = git(source, "rev-parse", "HEAD")
        require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None and git(source, "status", "--porcelain") == "")
        require(git(checkout, "rev-parse", "HEAD") == OPENJULES_REVISION and git(checkout, "status", "--porcelain") == "")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", args.image) is not None)
        from publication_capture_observation import approved_test_ids
        approved, inventory = approved_test_ids(source, revision)
        require(approved is not None and inventory["available"] is True)
        sys.path.insert(0, str(checkout))
        import docker
        from src.worker import docker_runtime as runtime, child_runtime as child
        require(Path(runtime.__file__).resolve() == checkout / "src/worker/docker_runtime.py"
                and Path(child.__file__).resolve() == checkout / "src/worker/child_runtime.py"
                and runtime._WATCHDOG_UID == child.WATCHDOG_UID == 65534)
        source_boundary(inspect.getsource(runtime.ManagedDockerRuntime._create))
        driver_bytes = small_read(__file__, MAX_REPORT)
        require(hashlib.sha1(b"blob " + str(len(driver_bytes)).encode() + b"\0" + driver_bytes).hexdigest()
                == git(source, "rev-parse", revision + ":tests/contracts/check_confined_suite.py"))
        driver_hash = hashlib.sha256(driver_bytes).hexdigest()
        profile = runtime.DockerProfile(name="confined-suite", deployment_id="confined-suite",
            repository="niklas-olsson/zero-point-modulator", source_path=str(source), image=args.image,
            command=("python3", "-I", "-S", str(DRIVER), "--guest", "verify"), base_commit=revision, max_seconds=240)
        report["stage"] = "source-export"
        snapshot = runtime.SourceSnapshots(root / "source").prepare(str(source), revision, profile.max_archive_bytes)
        report["stage"] = "transport-image"
        endpoint = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        require(endpoint.startswith("unix://"))
        client = docker.DockerClient(base_url=endpoint, timeout=5)
        client.api.max_redirects = 0
        transport = lambda: runtime.ManagedDockerRuntime._cleanup_transport(SimpleNamespace(client=client))
        require(transport() == runtime._CLEANUP_TRANSPORT)
        image = client.images.get(args.image)
        require(image.id == args.image)
        image_environment = dict(item.split("=", 1) for item in image.attrs["Config"]["Env"])
        report["stage"] = "volume-create"
        volume_intent = True
        volume = client.volumes.create(name="zpm-confined-suite-" + labels["zpm.confined-suite"], labels=labels)
        volume.reload()
        volume_identity = runtime.ManagedDockerRuntime._volume_identity(volume)
        require(volume_identity["Labels"] == labels and volume_identity["Driver"] == volume_identity["Scope"] == "local"
                and volume_identity["Options"] in (None, {}) and type(volume_identity["CreatedAt"]) is str and volume_identity["CreatedAt"])
        options = container_options(profile, child.CHILD_WATCHDOG, snapshot, volume.name, labels)
        options["log_config"] = docker.types.LogConfig(type="json-file", config={"max-size": "1m", "max-file": "1"})
        report["stage"] = "container-create"
        container_intent = True
        container = client.containers.create(args.image, **options)
        container_id = container.id
        require(type(container_id) is str and re.fullmatch(r"[0-9a-f]{64}", container_id))
        report["stage"] = "inspect"
        scopes = inspected_boundary(container, args.image, snapshot, volume.name, child.CHILD_WATCHDOG, image_environment)
        report.update(stage="execution", source=revision, openjules=OPENJULES_REVISION, image=args.image,
                      inventory=inventory, confinement_verified=True, driver_sha256=driver_hash,
                      budgets={"root": 180, "runtime": 240, "synthetic_child": 90, "command": 120, "suite": 110})
        watchdog_bytes = b"\0".join(part.encode() for part in options["entrypoint"]) + b"\0"
        config = {"source": revision, "approved_ids": sorted(approved), "watchdog_hash": hashlib.sha256(watchdog_bytes).hexdigest(),
                  "driver_sha256": driver_hash, "scopes": scopes}
        files = {"run": (None, 65532, 0o700), "children": (None, 65534, 0o711),
                 "run/" + CONFIG.name: (json.dumps(config).encode(), 65532, 0o400),
                 "run/" + DRIVER.name: (driver_bytes, 65532, 0o400),
                 "input.txt": (b"Synthetic confined suite; no task or model input.\n", 65532, 0o400)}
        report["stage"] = "archive-stage"
        require(container.put_archive("/workspace", runtime.ManagedDockerRuntime._tar(files)))
        report["stage"] = "start-arm"
        container.start()
        began = time.monotonic()
        for command in (["python3", "-I", "-S", "-c", runtime._SET_DEADLINE, str(began + 180)],
                        ["python3", "-I", "-S", "-c", child.ARM_CHILD, "synthetic-left", str(began + 90)],
                        ["python3", "-I", "-S", "-c", child.ARM_CHILD, "synthetic-right", str(began + 90)]):
            require(container.exec_run(command, user="65534:65534", workdir="/", stdout=False, stderr=False).exit_code == 0)
        report["stage"] = "exec-launch"
        execution = client.api.exec_create(container.id, ["python3", "-I", "-S", "-c", runtime._RUN,
            json.dumps({"base": revision, "command": profile.command})], user="65532:65532", workdir="/workspace/run",
            environment={"OPENJULES_INPUT_FILE": "/workspace/input.txt", "OPENJULES_ATTEMPT_ID": "synthetic-confined-suite"},
            stdin=False, stdout=False, stderr=False, privileged=False)["Id"]
        client.api.exec_start(execution, detach=True)
        report["stage"] = "exec-wait"
        while True:
            state = client.api.exec_inspect(execution)
            if not state.get("Running"):
                break
            require(time.monotonic() - began < 120)
            time.sleep(0.1)
        report["exit_code"] = state.get("ExitCode") if type(state.get("ExitCode")) is int else None
        report["stage"] = "capture"
        report["guest"] = captured_report(container, revision, approved, driver_hash)
        value = report["guest"]
        report["passed"] = qualification_passed(report["exit_code"], value, len(approved))
        report["stage"] = "complete" if report["passed"] else "suite-failed"
    except BaseException as error:
        report["passed"] = False
        report["failure"] = exception_chain(error)
    finally:
        clean = cleanup_owned(client, runtime, docker.errors.NotFound, container, volume, container_id, volume_identity,
                              labels, {"volume": volume_intent, "container": container_intent}) if client is not None else not volume_intent
        report["cleanup_complete"] = clean
        report["passed"] = report["passed"] and clean
        atomic_report(root / "report.json", report)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest", choices=("verify", "suite"), help=argparse.SUPPRESS)
    parser.add_argument("--openjules-checkout", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    if args.guest:
        return guest_main(args.guest)
    require(args.openjules_checkout is not None and args.image is not None and args.work_dir is not None)
    return host_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
