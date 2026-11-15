#!/usr/bin/env python3
"""Fixed native commands for the reviewed Alpine fixture, never a service.

The host validates and mounts this file read-only. Native modes run only inside
the declared container. Importing it for pure fixtures performs no operation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import sysconfig
import time

ROOT = Path("/work")
METADATA = Path("/harness/native-alpine")
RECIPE_SHA = "e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f"
CATALOG_SHA = "6e8d0bc3e1257625b803a55c6f12282b5275d1beb96ee6d7667ada18779baef0"
SERVICES = ("zpm", "openjules", "polyphony")
PROBE_IDS = ("zpm-native-imports", "openjules-native-imports", "polyphony-native-imports",
             "zpm-cli-help", "openjules-managed-service-help", "polyphony-coordinator-help",
             "zpm-binding-validation", "openjules-managed-context")
GUEST_CHECKS = ("apk_transaction", "installed_apk_inventory", "interpreter_identity",
                "production_prepare_verify", "sealed_bootstrap", "native_imports_and_interfaces", "repeat_bytes")
PHASES = {"assembly": (("assembly", 300),),
          "qualification": (("initial_bootstrap", 900), ("probes", 120), ("repeat", 60))}
OUTPUT_LIMIT = 16 * 1024**2


class GuestRefused(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise GuestRefused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def read(path, maximum=1048576, sha=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum, "GUEST_FILE_BOUND")
        value = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    require(len(value) == info.st_size and after.st_size == info.st_size and after.st_mtime_ns == info.st_mtime_ns,
            "GUEST_FILE_CHANGED")
    require(sha is None or digest(value) == sha, "GUEST_FILE_IDENTITY")
    return value


def write(path, value, owner=None):
    raw = encoded(value)
    require(len(raw) <= 262144, "GUEST_REPORT_BOUND")
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        if owner is not None and os.geteuid() == 0:
            os.fchown(stream.fileno(), *owner)
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def output_counter(path, deadline, owner_uid):
    """One fixed host/guest ledger, never replaced while writers exist."""
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_size == 8 and info.st_nlink == 1
                and info.st_uid == owner_uid and stat.S_IMODE(info.st_mode) == 0o600,
                "OUTPUT_COUNTER_IDENTITY")
        while True:
            require(time.monotonic() < deadline, "OUTPUT_COUNTER_DEADLINE")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.005, max(0, deadline - time.monotonic())))
        raw = os.pread(fd, 8, 0)
        require(len(raw) == 8, "OUTPUT_COUNTER_CHANGED")
        yield fd, int.from_bytes(raw, "big")
    finally:
        os.close(fd)


def charge_output(path, count, deadline, owner_uid):
    require(type(count) is int and 0 <= count <= 65536, "OUTPUT_CHUNK_BOUND")
    with output_counter(path, deadline, owner_uid) as (fd, previous):
        require(previous <= OUTPUT_LIMIT + 65536, "OUTPUT_COUNTER_CHANGED")
        current = previous + count
        require(os.pwrite(fd, current.to_bytes(8, "big"), 0) == 8, "OUTPUT_COUNTER_WRITE")
        # Account the observed offending chunk before refusing; never append
        # that chunk to a retained log or claim it fits the original envelope.
        require(current <= OUTPUT_LIMIT, "AGGREGATE_OUTPUT_BOUND")
        return current


def output_total(path, deadline, owner_uid):
    with output_counter(path, deadline, owner_uid) as (_, total):
        return total


def phase_record(control, stage):
    name, seconds = PHASES[stage][0]
    start = control["stage_started_ns"]
    return {"schema": "zpm-native-alpine-phase/v1", "attempt": control["attempt"], "stage": stage,
            "phases": [{"name": name, "started_ns": start,
                        "deadline_ns": min(start + seconds * 1_000_000_000, control["work_deadline_ns"]),
                        "finished_ns": None}]}


def validate_phases(value, control, stage, *, now_ns, running, previous=None):
    """A fixed monotone phase history also lets the host bound blocked C calls."""
    require(type(value) is dict and set(value) == {"schema", "attempt", "stage", "phases"}
            and value["schema"] == "zpm-native-alpine-phase/v1"
            and value["attempt"] == control["attempt"] and value["stage"] == stage,
            "PHASE_IDENTITY")
    rows, expected = value["phases"], PHASES[stage]
    require(type(rows) is list and 1 <= len(rows) <= len(expected), "PHASE_COUNT")
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) == {"name", "started_ns", "deadline_ns", "finished_ns"}, "PHASE_FIELDS")
        name, seconds = expected[index]
        require(row["name"] == name and type(row["started_ns"]) is int and type(row["deadline_ns"]) is int
                and row["started_ns"] <= now_ns and row["deadline_ns"] == min(
                    row["started_ns"] + seconds * 1_000_000_000, control["work_deadline_ns"]), "PHASE_BOUND")
        require(row["started_ns"] == control["stage_started_ns"] if index == 0 else
                rows[index - 1]["finished_ns"] is not None and row["started_ns"] >= rows[index - 1]["finished_ns"], "PHASE_ORDER")
        finish = row["finished_ns"]
        require(finish is None or type(finish) is int and row["started_ns"] <= finish < row["deadline_ns"]
                and finish <= now_ns, "PHASE_FINISH")
        require(index == len(rows) - 1 or finish is not None, "PHASE_UNFINISHED")
    if previous is not None:
        require(len(rows) >= len(previous["phases"]), "PHASE_REWIND")
        for old, new in zip(previous["phases"], rows):
            require(all(new[key] == old[key] for key in ("name", "started_ns", "deadline_ns"))
                    and (old["finished_ns"] is None or new["finished_ns"] == old["finished_ns"]), "PHASE_REWIND")
    if running:
        require(now_ns < rows[-1]["deadline_ns"], "GUEST_PHASE_DEADLINE")
    return value


def read_virtual(path, maximum):
    """procfs reports zero inode size; bound the bytes actually returned."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "GUEST_VIRTUAL_FILE")
        raw = stream.read(maximum + 1)
    require(0 < len(raw) <= maximum, "GUEST_VIRTUAL_BOUND")
    return raw


def installed_apks(raw):
    require(len(raw) <= 8 * 1024**2, "APK_DATABASE_BOUND")
    result = {}
    for stanza in raw.decode("utf-8").strip().split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line.startswith(("P:", "V:", "C:", "D:")):
                key, value = line.split(":", 1)
                require(key not in fields, "APK_DATABASE_DUPLICATE")
                fields[key] = value
        require(set(fields) in ({"P", "V", "C"}, {"P", "V", "C", "D"}) and fields["P"] not in result, "APK_DATABASE_FIELDS")
        result[fields["P"]] = {"version": fields["V"], "control_checksum": fields["C"],
                                "dependencies": fields.get("D", "").split()}
    return result


def expected_apks(recipe):
    rows = {row["name"]: row for row in recipe["oci"]["base_installed_packages"]}
    rows.update({row["name"]: row for row in recipe["apk_packages"]})
    return {name: {key: row[key] for key in ("version", "control_checksum", "dependencies")}
            for name, row in rows.items()}


def dependency_plan(recipe, identity):
    require(set(identity) == {"implementation", "version", "cache_tag", "abi", "platform"}, "NATIVE_INTERPRETER_FIELDS")
    require(identity["implementation"] == "cpython" and identity["version"] == "3.12.16", "NATIVE_INTERPRETER_IDENTITY")
    require(all(type(value) is str and 0 < len(value) <= 128 for value in identity.values()), "NATIVE_INTERPRETER_VALUES")
    return {"schema_version": 1, "interpreter": dict(identity),
            "seed": {"method": "sealed-pip-wheel", "filename": recipe["seed_filename"]},
            "artifacts": [{key: row[key] for key in ("filename", "size", "sha256", "name", "version")} for row in recipe["wheels"]],
            "environments": {name: {"source_revision": row["source_revision"],
                                    "dependency_files": {row["dependency_files"]["path"]: row["dependency_files"]["sha256"]},
                                    "wheels": row["wheels"]} for name, row in recipe["environments"].items()}}


def fixture_configuration(recipe, reference, image):
    return {"schema_version": 1, "deployment_id": "native-alpine-qualification",
            "binding": {"schema_version": 1, "repository": "fixture/native-alpine", "profile": "offline",
                        "desired_workers": 0}, "max_workers": 1,
            "sources": {name: {"checkout": "/sources/" + name, "revision": recipe["source_revisions"][name]} for name in SERVICES},
            "source": {"checkout": "/sources/zpm", "revision": recipe["source_revisions"]["zpm"]},
            "image": image, "command": ["/bin/false"], "dependency_plan": reference,
            "policy": {"checks": ["unqualified-fixture-only"], "reviewers": ["operator"], "max_attempts": 1,
                        "budget_seconds": 15, "attempt_seconds": 15, "capabilities": []}}


def guest_authority(control, qualification):
    status = dict(line.split(":", 1) for line in read_virtual(Path("/proc/self/status"), 65536).decode().splitlines() if ":" in line)
    require(status.get("NoNewPrivs", "").strip() == "1" and status.get("Seccomp", "").strip() == "2", "GUEST_SANDBOX")
    if qualification:
        require(os.getuid() == control["uid"] > 0 and os.getgid() == control["gid"] > 0, "GUEST_UID_GID")
        require(set(os.getgroups()) <= {control["gid"]}, "GUEST_SUPPLEMENTARY_GROUPS")
        for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            require(name in status and int(status[name].strip(), 16) == 0, "GUEST_CAPABILITIES")
    else:
        require(os.getuid() == 0 and os.getgid() == 0, "ASSEMBLY_UID_GID")
        for name, expected in (("CapPrm", 0xb), ("CapEff", 0xb), ("CapBnd", 0xb), ("CapInh", 0), ("CapAmb", 0)):
            require(name in status and int(status[name].strip(), 16) == expected, "ASSEMBLY_CAPABILITIES")
    namespaces = {name: os.readlink("/proc/self/ns/" + name) for name in ("net", "pid")}
    require(all(namespaces[name] != control["host_namespaces"][name] for name in namespaces), "GUEST_NAMESPACE")
    interfaces = {line.split(":", 1)[0].strip() for line in read_virtual(Path("/proc/net/dev"), 65536).decode().splitlines()[2:]}
    require(interfaces == {"lo"}, "GUEST_NETWORK_INTERFACES")
    mounts = {}
    for line in read_virtual(Path("/proc/self/mountinfo"), 262144).decode().splitlines():
        fields = line.split()
        require(len(fields) >= 10 and " - " in line, "GUEST_MOUNTINFO")
        mounts[fields[4]] = set(fields[5].split(","))
    readonly_paths = ["/inputs", "/harness"] + (["/sources/" + name for name in SERVICES] if qualification else [])
    for path in readonly_paths:
        require("ro" in mounts.get(path, set()), "GUEST_READONLY_MOUNT")
    require("rw" in mounts.get("/work", set()) and (not qualification or "ro" in mounts.get("/", set())), "GUEST_WORK_ROOT")
    return {"uid": os.getuid(), "gid": os.getgid(), "no_new_privileges": True,
            "network_interfaces": ["lo"], "separate_network_and_pid_namespaces": True,
            "read_only_root": qualification, "capabilities_empty": qualification}


class Guest:
    def __init__(self, control, recipe, stage):
        self.control, self.recipe, self.stage = control, recipe, stage
        self.deadline = control["work_deadline_ns"] / 1_000_000_000
        self.phase_end = self.deadline
        self.phases = None
        self.owner = (control["uid"], control["gid"])
        self.budget = ROOT / "output-budget.bin"
        self.output_bytes = 0
        self.command_count = 0
        self.result = {"schema": "zpm-native-alpine-guest/v1", "attempt": control["attempt"], "stage": stage,
                       "status": "RUNNING", "first_failure": None,
                       "checks": {name: "NOT_RUN" for name in GUEST_CHECKS},
                       "probe_ids_passed": [], "interpreter": None, "authority": None,
                       "installed_distribution_counts": {}, "elapsed_ms": {}, "phases": None}
        self.file = ROOT / (stage + "-result.json")
        self.save()

    def save(self):
        self.result["phases"] = self.phases
        write(self.file, self.result, self.owner)

    def check(self):
        require(time.monotonic() < min(self.deadline, self.phase_end), "GUEST_PHASE_DEADLINE")

    def begin_phase(self, name):
        if self.phases is None:
            self.phases = phase_record(self.control, self.stage)
            require(self.phases["phases"][0]["name"] == name, "PHASE_ORDER")
        else:
            self.finish_phase()
            index = len(self.phases["phases"])
            require(index < len(PHASES[self.stage]) and PHASES[self.stage][index][0] == name, "PHASE_ORDER")
            start = time.monotonic_ns()
            self.phases["phases"].append({"name": name, "started_ns": start,
                "deadline_ns": min(start + PHASES[self.stage][index][1] * 1_000_000_000,
                                   self.control["work_deadline_ns"]), "finished_ns": None})
        self.phase_end = self.phases["phases"][-1]["deadline_ns"] / 1_000_000_000
        self.check()
        write(ROOT / (self.stage + "-phase.json"), self.phases, self.owner)
        def expired(signum, frame):
            raise GuestRefused("GUEST_PHASE_DEADLINE")
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, max(.000001, self.phase_end - time.monotonic()))

    def finish_phase(self):
        self.check()
        self.phases["phases"][-1]["finished_ns"] = time.monotonic_ns()
        write(ROOT / (self.stage + "-phase.json"), self.phases, self.owner)

    def owner_call(self, operation, *args):
        self.check()
        value = operation(*args)
        self.check()
        return value

    def passed(self, check):
        self.result["checks"][check] = "PASS"
        self.save()

    def run(self, command, seconds, cwd=ROOT):
        end = min(time.monotonic() + seconds, self.deadline, self.phase_end)
        require(time.monotonic() < end, "GUEST_TOTAL_DEADLINE")
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/work/home", "TMPDIR": "/work/tmp",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
                       "PIP_CONFIG_FILE": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        self.command_count += 1
        output = bytearray()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            path = ROOT / (self.stage + "-command-%02d.log" % self.command_count)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            if os.geteuid() == 0:
                os.fchown(fd, *self.owner)
            with os.fdopen(fd, "wb") as log:
                while selector.get_map():
                    require(time.monotonic() < end, "GUEST_COMMAND_DEADLINE")
                    for key, _ in selector.select(min(.1, max(0, end - time.monotonic()))):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        self.output_bytes += len(block)
                        charge_output(self.budget, len(block), end, self.control["uid"])
                        require(len(output) + len(block) <= 1048576, "GUEST_OUTPUT_BOUND")
                        output.extend(block)
                        log.write(block)
                process.wait(timeout=max(.01, end - time.monotonic()))
                require(time.monotonic() < end, "GUEST_COMMAND_DEADLINE")
                require(process.returncode == 0, "GUEST_COMMAND_EXIT")
                self.check()
                return bytes(output)
        except subprocess.TimeoutExpired:
            raise GuestRefused("GUEST_COMMAND_DEADLINE") from None
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining = self.deadline - time.monotonic()
                if remaining > 0:
                    try:
                        process.wait(timeout=min(1, remaining))
                    except subprocess.TimeoutExpired:
                        raise GuestRefused("GUEST_CHILD_UNREAPED") from None

    def assembly(self):
        self.begin_phase("assembly")
        self.result["authority"] = guest_authority(self.control, False)
        packages = ["/inputs/apks/" + row["filename"] for row in self.recipe["apk_packages"]]
        self.run(["/sbin/apk", "add", "--no-network", "--no-cache", "--repositories-file", "/dev/null", *packages], 300)
        self.passed("apk_transaction")
        actual = installed_apks(read(Path("/lib/apk/db/installed"), 8 * 1024**2))
        expected = expected_apks(self.recipe)
        require(actual == expected and len(actual) == 64, "NATIVE_APK_INVENTORY")
        self.passed("installed_apk_inventory")
        self.finish_phase()

    def qualification(self):
        self.begin_phase("initial_bootstrap")
        self.result["authority"] = guest_authority(self.control, True)
        require(read(Path("/etc/alpine-release"), 64).strip() == b"3.24.4", "NATIVE_ALPINE_RELEASE")
        original = json.loads(read(METADATA / "plan-v1.json", 32768,
                                   "c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76"))
        for row in original["native_file_pins"]:
            require(len(read(Path("/") / row["path"], row["size"], row["sha256"])) == row["size"], "NATIVE_FILE_SIZE")
        require(sys.executable == "/usr/local/bin/python3.12", "NATIVE_INTERPRETER_PATH")
        sys.path.insert(0, "/sources/zpm")
        from zpm import dependencies, deployment, installation_context
        identity = self.owner_call(dependencies.interpreter_identity)
        plan = dependency_plan(self.recipe, identity)
        self.result["interpreter"] = identity
        self.passed("interpreter_identity")
        plan_file = ROOT / "dependency-plan.json"
        write(plan_file, plan)
        reference = {"manifest": str(plan_file), "sha256": digest(read(plan_file)), "artifacts": "/inputs/wheels"}
        configuration = fixture_configuration(self.recipe, reference, self.control["fixture_image"])
        installation = ROOT / "installation"
        before = time.monotonic()
        self.owner_call(deployment.prepare, configuration, installation)
        self.owner_call(deployment.verify, installation)
        self.passed("production_prepare_verify")
        private_before = {path.relative_to(installation).as_posix(): read(path) for directory in ("config", "secrets")
                          for path in sorted((installation / directory).iterdir()) if path.is_file()}
        private_before["manifest.json"] = read(installation / "manifest.json")
        self.run([sys.executable, "-I", "-B", str(installation / "manage.py"), "bootstrap"], 900, installation)
        data = json.loads(read(installation / "manifest.json"))["configuration"]
        self.owner_call(dependencies.check_completed, installation, data, deployment._environment(installation, "bootstrap", data))
        completed = {service: read(installation / "environments" / service / "sealed-complete.json") for service in SERVICES}
        for service, raw in completed.items():
            receipt = json.loads(raw)
            require(receipt["plan_sha256"] == reference["sha256"] and receipt["interpreter"] == identity
                    and receipt["source_revision"] == self.recipe["source_revisions"][service], "SEALED_COMPLETION_IDENTITY")
            self.result["installed_distribution_counts"][service] = len(receipt["installed"])
        self.result["elapsed_ms"]["initial_bootstrap"] = round((time.monotonic() - before) * 1000)
        self.passed("sealed_bootstrap")
        selector_before = self.owner_call(installation_context.validate_selector, installation)
        bootstrap_before = {service: read(installation / "environments" / service / "bootstrap-complete.json") for service in SERVICES}
        self.probes(installation, configuration, selector_before)
        self.passed("native_imports_and_interfaces")
        self.begin_phase("repeat")
        repeat_end = self.phase_end
        require(self.owner_call(deployment.prepare, configuration, installation)["reused"] is True, "REPEAT_PREPARE_REUSE")
        self.owner_call(deployment.verify, installation)
        require(time.monotonic() < repeat_end, "REPEAT_DEADLINE")
        self.run([sys.executable, "-I", "-B", str(installation / "manage.py"), "bootstrap"], repeat_end - time.monotonic(), installation)
        self.owner_call(dependencies.check_completed, installation, data, deployment._environment(installation, "bootstrap", data))
        require(time.monotonic() < repeat_end, "REPEAT_DEADLINE")
        require(all(read(installation / name) == raw for name, raw in private_before.items()), "REPEAT_PRIVATE_BYTES")
        require(self.owner_call(installation_context.validate_selector, installation) == selector_before, "REPEAT_SELECTOR_BYTES")
        require(all(read(installation / "environments" / service / "sealed-complete.json") == completed[service] and
                    read(installation / "environments" / service / "bootstrap-complete.json") == bootstrap_before[service]
                    for service in SERVICES), "REPEAT_COMPLETION_BYTES")
        self.passed("repeat_bytes")
        self.finish_phase()

    def probes(self, installation, configuration, selector):
        self.begin_phase("probes")
        end = self.phase_end
        binding = ROOT / "binding.json"
        write(binding, configuration["binding"])
        helper = "/harness/alpine_sealed_guest.py"
        for probe in PROBE_IDS:
            service = probe.split("-", 1)[0]
            python = str(installation / "environments" / service / "bin/python")
            command = [python, "-I", "-B", helper, "probe", "--probe-id", probe]
            started = time.monotonic()
            result = self.run(command, min(15, end - started), ROOT)
            if probe == "openjules-managed-context":
                # The native probe returns a private selector hash. Verify it
                # here, and never copy it into the exportable result.
                record = json.loads(result)
                require(record == {"schema_version": 1, "selector_sha256": digest(selector),
                                   "legacy_execution_allowed": False}, "MANAGED_CONTEXT_RESULT")
            self.result["elapsed_ms"][probe] = round((time.monotonic() - started) * 1000)
            self.result["probe_ids_passed"].append(probe)
            self.save()


def native_imports(service, recipe, catalog):
    selected = set(recipe["environments"][service]["wheels"])
    rows = [row for row in catalog["members"] if row["filename"] in selected]
    site = Path(sysconfig.get_paths()["platlib"])
    require(site.is_relative_to(Path(sys.prefix)) and Path(sys.prefix).is_relative_to(Path("/work/installation/environments")), "NATIVE_IMPORT_ENVIRONMENT")
    loaded = 0
    for row in rows:
        path = site / row["member"]
        require(path.resolve() == path and len(read(path, row["size"], row["sha256"])) == row["size"], "NATIVE_INSTALLED_BYTES")
        if row["import_module"] is not None:
            module = importlib.import_module(row["import_module"])
            require(Path(module.__file__).resolve() == path, "NATIVE_LOADED_PATH")
            loaded += 1
    print(json.dumps({"service": service, "imported_extensions": loaded,
                      "verified_support_libraries": len(rows) - loaded}, sort_keys=True))


def fixed_probe(name, recipe):
    import runpy
    require(name in PROBE_IDS, "PROBE_ID")
    service = name.split("-", 1)[0]
    require(Path(sys.prefix) == Path("/work/installation/environments") / service, "PROBE_ENVIRONMENT")
    if name.endswith("-native-imports"):
        catalog = json.loads(read(METADATA / "native-extensions-v1.json", 32768, CATALOG_SHA))
        return native_imports(service, recipe, catalog)
    if name == "zpm-cli-help":
        sys.argv = ["zpm", "--help"]
        runpy.run_module("zpm", run_name="__main__")
        return 0
    if name == "zpm-binding-validation":
        sys.argv = ["zpm", "validate", "/work/binding.json"]
        runpy.run_module("zpm", run_name="__main__")
        return 0
    sys.path.insert(0, "/sources/" + service)
    if name == "openjules-managed-context":
        sys.argv = ["execution_context", "--probe-managed-installation"]
        runpy.run_module("src.worker.execution_context", run_name="__main__")
        return 0
    module = "src.worker.managed_service" if service == "openjules" else "src.liminal_bridge.coordinator_service"
    sys.argv = [module, "--help"]
    runpy.run_module(module, run_name="__main__")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("assembly", "qualification", "probe"))
    parser.add_argument("--probe-id", choices=PROBE_IDS)
    args = parser.parse_args(argv)
    recipe = json.loads(read(METADATA / "inputs-v1.json", 245074, RECIPE_SHA))
    if args.mode == "probe":
        return fixed_probe(args.probe_id, recipe) or 0
    control = json.loads(read(ROOT / "control.json", 65536))
    require(type(control) is dict and set(control) == {"attempt", "uid", "gid", "work_deadline_ns", "fixture_image",
            "host_namespaces", "stage", "stage_started_ns"} and control["stage"] == args.mode
            and type(control["uid"]) is int and control["uid"] > 0
            and type(control["gid"]) is int and control["gid"] > 0
            and type(control["stage_started_ns"]) is int and control["stage_started_ns"] > 0, "GUEST_CONTROL")
    require(type(control.get("work_deadline_ns")) is int and time.monotonic_ns() < control["work_deadline_ns"], "GUEST_ADMISSION_DEADLINE")
    guest = Guest(control, recipe, args.mode)
    try:
        if args.mode == "assembly":
            guest.assembly()
        else:
            guest.qualification()
        guest.result["status"] = "PASS"
        guest.check()
        return 0
    except BaseException as error:
        code = str(error) if isinstance(error, GuestRefused) and re.fullmatch(r"[A-Z0-9_]{1,64}", str(error)) else "GUEST_NATIVE_FAILURE"
        guest.result.update(status="FAIL", first_failure=code)
        pending = [name for name in GUEST_CHECKS if guest.result["checks"][name] == "NOT_RUN" and
                   (name in ("apk_transaction", "installed_apk_inventory")) == (args.mode == "assembly")]
        if pending:
            guest.result["checks"][pending[0]] = "FAIL"
        return 1
    finally:
        try:
            guest.save()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    raise SystemExit(main())
