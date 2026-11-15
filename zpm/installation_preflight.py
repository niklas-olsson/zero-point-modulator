"""Explicit, private native installation preflight for single-host recipes.

Preparation is data only. Native validation uses the installed interpreters and
independently verified producer checkouts. The pure fixture API remains a pure
API: its closed parsers and compatibility reducer are reused only after this
module has established provenance. No result here grants live task authority.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid

from . import installation_admission as admission, installation_context
from .models import ConfigurationError


CONTRACT = admission.CONTRACT
SELECTION = "config/installation-admission.json"
SCOPES = "config/installation-scopes.json"
RECEIPT = "state/installation-admission.json"
STATUS = "state/installation-admission-status.json"
KP_CONTRACT_DIGEST = "9a5a5d649f9dbf0357f200954381ee3ac669d89e5efcf3ed8a558c2a2ed32c7e"
OPERATION_SECONDS = 90
PROCESS_SECONDS = 10
REAP_SECONDS = 2
_SPECS = {
    "openjules": {
        "source": "openjules", "package": "src.worker",
        "module": "src.worker.installation_validation",
        "command": "src.worker.installation_validation",
        "artifact": "src/worker/contracts/openjules-installation-v1.json",
        "digest": admission.OPENJULES_CONTRACT_DIGEST, "invalid_exit": 2,
        "inputs": {"configuration": "config/openjules.json", "scope_declarations": SCOPES},
    },
    "keystone-polyphony": {
        "source": "polyphony", "package": "src.liminal_bridge",
        "module": "src.liminal_bridge.managed_cli",
        "command": "src.liminal_bridge.managed_cli",
        "artifact": "contracts/managed-installation-v1.json",
        "digest": KP_CONTRACT_DIGEST, "invalid_exit": 1,
        "inputs": {"managed_policy": "config/polyphony.json", "credentials": "secrets/polyphony.json"},
    },
}
_CODES = {
    "not_selected", "unsupported_schema", "selection_mismatch", "input_unavailable", "input_mismatch",
    "bootstrap_required", "source_unavailable", "source_identity", "source_bytes", "source_inventory",
    "static_contract_mismatch", "import_origin", "interpreter_identity", "process_timeout",
    "process_output_bound", "process_signal", "process_failed", "process_cleanup", "operation_timeout",
    "producer_invalid", "producer_disabled", "producer_result_malformed", "exit_receipt_mismatch",
    "source_mismatch", "contract_mismatch", "unsupported_contract", "result_malformed",
    "deployment_mismatch", "scope_mismatch", "base_mismatch", "profile_digest_mismatch",
    "capabilities_mismatch", "required_peer_unvalidated", "required_capability_mismatch",
    "retained_receipt_mismatch", "receipt_unavailable", "installation_changed", "diagnostic_unavailable",
    "operation_busy",
}
_PROBE = """import importlib,json,pathlib,sys
sys.path.append(sys.argv[1])
p=importlib.import_module('src')
s=importlib.import_module(sys.argv[2])
v=importlib.import_module(sys.argv[3])
print(json.dumps({'package':str(pathlib.Path(p.__file__).resolve()) if p.__file__ else None,
 'package_paths':[str(pathlib.Path(x).resolve()) for x in p.__path__],
 'service_package':str(pathlib.Path(s.__file__).resolve()) if s.__file__ else None,
 'service_paths':[str(pathlib.Path(x).resolve()) for x in s.__path__],
 'module':str(pathlib.Path(v.__file__).resolve()),
 'executable':sys.executable,'prefix':sys.prefix},sort_keys=True))
"""
_NATIVE = """import runpy,sys
sys.path.append(sys.argv.pop(1))
module=sys.argv.pop(1)
runpy.run_module(module,run_name='__main__',alter_sys=True)
"""


def enabled(data):
    return data.get("installation_admission") == CONTRACT


def _report(receipt_id=None, *, success=False, owner=None, code=None, components=None):
    components = components or {service: {"status": "valid" if success else "unvalidated"}
                                for service in admission.SERVICES}
    return {
        "schema_version": 1, "receipt_contract": CONTRACT, "receipt_id": receipt_id,
        "phase": "native-config-validated" if success else "native-config-refused",
        "compatible": True if success else None,
        "source_identity_verified": success,
        "native_validation_performed": any(item["status"] in {"valid", "invalid", "disabled"}
                                           for item in components.values()),
        "live_capabilities_checked": False, "execution_qualified": False, "services_started": False,
        "components": components,
        "errors": [] if success else [{"owner": owner, "code": code}],
    }


class PreflightError(ConfigurationError):
    """Only fixed codes and an opaque operation identity may cross this boundary."""

    def __init__(self, owner, code):
        if owner not in {*admission.SERVICES, "zpm"} or code not in _CODES:
            owner, code = "zpm", "producer_result_malformed"
        super().__init__("installation preflight refused: " + owner + "/" + code)
        self.owner, self.code = owner, code
        self.component_status = None
        self.report = _report(owner=owner, code=code)


def _require(condition, owner, code):
    if not condition:
        raise PreflightError(owner, code)


@contextmanager
def interruptible():
    """Let bounded process cleanup run when the manager receives SIGTERM."""
    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class _Budget:
    # Subprocess deadlines interrupt waits. Local bounded reads and inventory
    # loops check this shared budget between operations; a filesystem syscall
    # is not independently interruptible by this elapsed-time check.
    def __init__(self):
        self.deadline = time.monotonic() + OPERATION_SECONDS

    def remaining(self, owner="zpm"):
        remaining = self.deadline - time.monotonic()
        _require(remaining > 0, owner, "operation_timeout")
        return remaining


@contextmanager
def _coordination(root, *, exclusive, budget=None):
    """Coordinate observers on the original sealed selection, never task state.

    An ordinary doctor may already be writing its capability observations.
    Admission waits within its original operation budget; a later observer
    reports busy without opening the WAL-aware journal. No file is created.
    """
    descriptor = None
    try:
        try:
            root = Path(root)
            path = root / SELECTION
            _require(root.is_absolute() and root.resolve(strict=True) == root
                     and not root.stat().st_mode & 0o077 and path.resolve(strict=True) == path,
                     "zpm", "input_unavailable")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            original = os.fstat(descriptor)
            _require(stat.S_ISREG(original.st_mode) and original.st_nlink == 1
                     and original.st_uid == os.geteuid() and not original.st_mode & 0o077
                     and 0 < original.st_size <= admission.MAX_SELECTION_BYTES,
                     "zpm", "input_unavailable")
            raw = os.read(descriptor, admission.MAX_SELECTION_BYTES + 1)
            _require(len(raw) == original.st_size, "zpm", "input_unavailable")
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            def custody():
                try:
                    _require(path.resolve(strict=True) == path
                             and identity(os.fstat(descriptor)) == identity(original)
                             and identity(path.lstat()) == identity(original), "zpm", "selection_mismatch")
                    _require(os.pread(descriptor, admission.MAX_SELECTION_BYTES + 1, 0) == raw,
                             "zpm", "selection_mismatch")
                except PreflightError:
                    raise
                except (OSError, ValueError, RuntimeError):
                    raise PreflightError("zpm", "selection_mismatch") from None
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            while True:
                if budget is not None:
                    budget.remaining()
                try:
                    fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _require(exclusive and budget is not None, "zpm", "operation_busy")
                    time.sleep(min(0.05, budget.remaining()))
            custody()
        except PreflightError:
            raise
        except (OSError, ValueError, RuntimeError):
            raise PreflightError("zpm", "input_unavailable") from None
        yield custody
        custody()
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def observe(root: Path, data: dict):
    """A short observer span; unrelated to the manager's lifetime lock."""
    if not enabled(data):
        yield
        return
    with _coordination(root, exclusive=False):
        yield


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit, owner, *, private=False):
    try:
        _require(path.resolve(strict=True) == path, owner, "input_unavailable")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit
                     and (not private or not info.st_mode & 0o077), owner, "input_unavailable")
            raw = stream.read(limit + 1)
            _require(len(raw) <= limit, owner, "input_unavailable")
        return raw
    except (OSError, RuntimeError, ValueError):
        raise PreflightError(owner, "input_unavailable") from None


def _decode(raw, limit, owner, code):
    try:
        return admission._decode(raw, limit)
    except (ValueError, TypeError, RecursionError):
        raise PreflightError(owner, code) from None


def _write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _entries(data):
    return data["repositories"] if data["schema_version"] == 2 else [data]


def _scopes(data):
    return admission.encode({"schema_version": 1, "scopes": [
        {"deployment_id": data["deployment_id"], "repository": repository}
        for repository in sorted({entry["binding"]["repository"] for entry in _entries(data)})]})


def _selection(data, inputs, receipt_id):
    _require(type(receipt_id) is str and admission._RECEIPT_ID.fullmatch(receipt_id) is not None,
             "zpm", "selection_mismatch")
    components = {}
    for service, spec in _SPECS.items():
        repository, contract = admission.COMPONENTS[service]
        revision = data["sources"][spec["source"]]["revision"]
        _require(type(revision) is str and admission._REVISION.fullmatch(revision) is not None,
                 service, "source_identity")
        components[service] = {
            "repository": repository, "source_revision": revision,
            "validation_contract": contract, "contract_digest": spec["digest"],
            "input_digests": {name: _hash(raw) for name, raw in inputs[service].items()},
        }
    profiles = sorted([{
        "repository": entry["binding"]["repository"], "profile": entry["binding"]["profile"],
        "base": entry["source"]["revision"], "capabilities": entry["policy"]["capabilities"],
    } for entry in _entries(data)], key=lambda item: (item["repository"], item["profile"]))
    return {"schema_version": 1, "receipt_contract": CONTRACT, "receipt_id": receipt_id,
            "deployment_id": data["deployment_id"], "installation_plan_digest": _hash(admission.encode(data)),
            "components": components, "profiles": profiles}


def _inputs(root):
    return {service: {name: _read(root / relative, admission.INPUTS[service][name], service, private=True)
                      for name, relative in spec["inputs"].items()} for service, spec in _SPECS.items()}


def prepare(root: Path, data: dict) -> None:
    """Seal intent and opaque identity without importing either producer."""
    if not enabled(data):
        return
    _require(data["schema_version"] in {1, 2}, "zpm", "unsupported_schema")
    scopes = _scopes(data)
    _require(len(scopes) <= admission.INPUTS["openjules"]["scope_declarations"], "zpm", "selection_mismatch")
    _write_new(root / SCOPES, scopes)
    selected = _selection(data, _inputs(root), str(uuid.uuid4()))
    raw = admission.encode(selected)
    _require(len(raw) <= admission.MAX_SELECTION_BYTES, "zpm", "selection_mismatch")
    _write_new(root / SELECTION, raw)


def _prepared(root, data):
    _require(enabled(data), "zpm", "not_selected")
    _require(type(data.get("schema_version")) is int and data["schema_version"] in {1, 2},
             "zpm", "unsupported_schema")
    raw_manifest = _read(root / "manifest.json", admission.MAX_RESULT_BYTES, "zpm", private=True)
    manifest = _decode(raw_manifest, admission.MAX_RESULT_BYTES, "zpm", "selection_mismatch")
    _require(type(manifest) is dict and admission.encode(manifest.get("configuration")) == admission.encode(data)
             and manifest.get("directory") == str(root) and type(manifest.get("schema_version")) is int
             and manifest["schema_version"] == 1
             and type(manifest.get("files")) is dict, "zpm", "selection_mismatch")
    raw = _read(root / SELECTION, admission.MAX_SELECTION_BYTES, "zpm", private=True)
    selected = _decode(raw, admission.MAX_SELECTION_BYTES, "zpm", "selection_mismatch")
    _require(type(selected) is dict, "zpm", "selection_mismatch")
    inputs = _inputs(root)
    _require(inputs["openjules"]["scope_declarations"] == _scopes(data), "openjules", "input_mismatch")
    expected = _selection(data, inputs, selected.get("receipt_id"))
    _require(raw == admission.encode(expected), "zpm", "selection_mismatch")
    sealed = {SELECTION: _hash(raw)}
    for service, spec in _SPECS.items():
        for name, relative in spec["inputs"].items():
            sealed[relative] = _hash(inputs[service][name])
            _require(manifest["files"].get(relative) == sealed[relative], service, "input_mismatch")
    _require(manifest["files"].get(SELECTION) == sealed[SELECTION], "zpm", "selection_mismatch")
    return selected, raw, inputs, sealed, raw_manifest


def verify_prepared(root: Path, data: dict) -> dict:
    """Bound the first reads of opted-in private inputs during offline verify."""
    return _prepared(root, data)[3]


def environment():
    # Native commands receive no caller credential, proxy, import or Git setting.
    return {name: value for name, value in os.environ.items()
            if name in {"LANG", "LC_ALL", "LC_CTYPE"}} | {
        "PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
    }


def _run(command, cwd, owner, budget, *, limit=admission.MAX_RESULT_BYTES, seconds=PROCESS_SECONDS):
    """Bound output while reading; keep the leader owned through group cleanup."""
    process, failure, answer = None, None, None
    try:
        deadline = time.monotonic() + min(seconds, budget.remaining(owner))
        process = subprocess.Popen(command, cwd=cwd, env=environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        output = bytearray()
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as watcher:
            watcher.register(process.stdout, selectors.EVENT_READ)
            ended = False
            while True:
                remaining = deadline - time.monotonic()
                _require(remaining > 0, owner, "process_timeout")
                for key, _ in watcher.select(min(0.1, remaining)):
                    chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                    if not chunk:
                        watcher.unregister(key.fileobj)
                        ended = True
                    else:
                        output.extend(chunk)
                        _require(len(output) <= limit, owner, "process_output_bound")
                exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if ended and exited is not None:
                    _require(exited.si_code == os.CLD_EXITED, owner, "process_signal")
                    answer = exited.si_status, bytes(output)
                    break
    except (OSError, subprocess.SubprocessError):
        failure = PreflightError(owner, "process_failed")
    except BaseException as error:
        failure = error
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                if failure is None:
                    failure = PreflightError(owner, "process_cleanup")
            try:
                process.wait(timeout=REAP_SECONDS)
            except (OSError, subprocess.SubprocessError):
                if failure is None:
                    failure = PreflightError(owner, "process_cleanup")
            if process.stdout is not None:
                process.stdout.close()
    if failure is not None:
        raise failure
    budget.remaining(owner)
    return answer


def _git(checkout, owner, budget, *arguments):
    executable = shutil.which("git", path=os.defpath)
    _require(executable is not None, owner, "source_unavailable")
    code, raw = _run([executable, "--no-replace-objects", "--literal-pathspecs",
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "core.pager=cat", "-C", str(checkout), *arguments], checkout, owner, budget,
        limit=2_000_000, seconds=15)
    _require(code == 0, owner, "source_unavailable")
    return raw


def _source(checkout, revision, service, budget):
    """Read immutable Git objects and compare working bytes without filters."""
    spec = _SPECS[service]
    _require(checkout.is_absolute() and checkout.resolve(strict=True) == checkout,
             service, "source_identity")
    identity = _git(checkout, service, budget, "rev-parse", "--show-toplevel", "HEAD", revision + "^{tree}")
    lines = identity.decode("utf-8").splitlines()
    _require(len(lines) == 3 and lines[:2] == [str(checkout), revision], service, "source_identity")
    tree = lines[2]
    rows = _git(checkout, service, budget, "ls-tree", "-rz", revision).split(b"\0")
    _require(1 < len(rows) <= 4097, service, "source_inventory")
    source_paths, tracked_roots, total = set(), set(), 0
    for row in rows:
        budget.remaining(service)
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        relative = raw_path.decode("utf-8")
        _require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                 service, "source_inventory")
        tracked_roots.add(Path(relative).parts[0])
        if mode == b"160000" and kind == b"commit":
            # KP's unrelated browser toolchains are recorded Git links. They
            # are not imported or initialized by either native validator.
            _require(not relative.startswith("src/") and relative != spec["artifact"]
                     and not (checkout / relative).is_symlink(), service, "source_inventory")
            continue
        if mode == b"120000" and kind == b"blob":
            _require(not relative.startswith("src/") and relative != spec["artifact"]
                     and (checkout / relative).is_symlink(), service, "source_inventory")
            raw = os.readlink(checkout / relative).encode("utf-8")
        else:
            _require(mode in {b"100644", b"100755"} and kind == b"blob", service, "source_inventory")
            raw = _read(checkout / relative, 2_000_000, service)
        total += len(raw)
        _require(total <= 16_000_000, service, "source_inventory")
        algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
        actual = algorithm(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        _require(actual.encode("ascii") == oid, service, "source_bytes")
        if relative.startswith("src/"):
            source_paths.add(relative)
    observed = set()
    count = 0
    for directory, directories, files in os.walk(checkout / "src", followlinks=False):
        budget.remaining(service)
        for name in [*directories, *files]:
            count += 1
            path = Path(directory) / name
            _require(count <= 4096 and not path.is_symlink(), service, "source_inventory")
        directories[:] = [name for name in directories if name != "__pycache__"]
        observed.update(str((Path(directory) / name).relative_to(checkout)) for name in files)
    _require(observed == source_paths and spec["module"].replace(".", "/") + ".py" in source_paths,
             service, "source_inventory")
    # The existing context probe and service commands use their checkout cwd.
    # Reject ignored root import shadows before those commands run too. The
    # isolated validator alone would not protect a later ordinary -m launch.
    for path in checkout.iterdir():
        budget.remaining(service)
        if path.name in tracked_roots or path.name in {".git", "__pycache__"}:
            continue
        import_candidate = (path.suffix in {".py", ".pyc", ".pyo", ".so"}
                            or (path.name.isidentifier() and path.is_dir()))
        _require(not import_candidate, service, "source_inventory")
    artifact = _read(checkout / spec["artifact"], 65_536, service)
    if service == "openjules":
        digest = _hash(artifact)
    else:
        document = _decode(artifact, 65_536, service, "static_contract_mismatch")
        # KP publishes canonical JSON without a newline. This is the static
        # contract digest only; authority profile digest semantics stay in KP.
        digest = _hash(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False).encode("utf-8"))
    _require(digest == spec["digest"], service, "static_contract_mismatch")
    return {"revision": revision, "tree": tree, "static_artifact_sha256": _hash(artifact)}


def guard(root: Path, data: dict) -> None:
    """Check selected inputs and source before any existing sibling import."""
    with interruptible():
        _guard(root, data)


def _guard(root, data):
    selected, selected_raw, _, _, _ = _prepared(root, data)
    prior = _retained(root, selected, selected_raw)
    budget = _Budget()
    for service, spec in _SPECS.items():
        source = _source(Path(data["sources"][spec["source"]]["checkout"]),
                         selected["components"][service]["source_revision"], service, budget)
        if prior is not None:
            _require(prior["components"][service]["source_evidence"] == source
                     and prior["components"][service]["interpreter"] == _interpreter(root, spec["source"], service),
                     service, "retained_receipt_mismatch")
    if prior is not None:
        _require(prior["bootstrap_evidence"] == _bootstrap_evidence(root, data),
                 "zpm", "retained_receipt_mismatch")


def _retained(root, selected, selected_raw):
    """Read the original private identity before running any sibling process."""
    path = root / RECEIPT
    if not os.path.lexists(path):
        status = root / STATUS
        if os.path.lexists(status):
            previous = _decode(_read(status, 4096, "zpm", private=True), 4096,
                               "zpm", "retained_receipt_mismatch")
            _require(type(previous) is dict and previous.get("receipt_contract") == CONTRACT
                     and previous.get("receipt_id") == selected["receipt_id"]
                     and previous.get("receipt_established") is not True
                     and previous.get("phase") != "native-config-validated",
                     "zpm", "retained_receipt_mismatch")
        return None
    raw = _read(path, admission.MAX_RESULT_BYTES, "zpm", private=True)
    value = _decode(raw, admission.MAX_RESULT_BYTES, "zpm", "retained_receipt_mismatch")
    try:
        _require(type(value) is dict and value["schema_version"] == 1
                 and value["receipt_contract"] == CONTRACT and value["receipt_id"] == selected["receipt_id"]
                 and value["prepared_selection_sha256"] == _hash(selected_raw)
                 and value["installation_plan_digest"] == selected["installation_plan_digest"]
                 and type(value["bootstrap_evidence"]) is dict
                 and set(value["components"]) == set(admission.SERVICES), "zpm", "retained_receipt_mismatch")
        for service in admission.SERVICES:
            component = value["components"][service]
            _require(type(component) is dict and all(component[key] == expected for key, expected
                     in selected["components"][service].items())
                     and all(type(component[key]) is dict for key in ("source_evidence", "interpreter", "import_probe")),
                     service, "retained_receipt_mismatch")
    except (KeyError, TypeError):
        raise PreflightError("zpm", "retained_receipt_mismatch") from None
    return value


def _interpreter(root, source, owner):
    path = root / "environments" / source / "bin/python"
    try:
        resolved = path.resolve(strict=True)
        raw = _read(resolved, 64_000_000, owner)
        configuration = _read(path.parent.parent / "pyvenv.cfg", 65_536, owner)
        _require(os.access(path, os.X_OK), owner, "interpreter_identity")
        return {"path": str(path), "resolved": str(resolved), "sha256": _hash(raw),
                "configuration_sha256": _hash(configuration)}
    except (OSError, RuntimeError):
        raise PreflightError(owner, "interpreter_identity") from None


def _command(interpreter, checkout, work, service, *, probe=False, arguments=()):
    spec = _SPECS[service]
    suffix = [spec["package"], spec["module"]] if probe else [spec["command"], *arguments]
    return [str(interpreter), "-I", "-B", "-X", "pycache_prefix=" + str(work / "unused-bytecode"),
            "-c", _PROBE if probe else _NATIVE, str(checkout), *suffix]


def _probe(root, checkout, work, service, budget):
    spec = _SPECS[service]
    interpreter = root / "environments" / spec["source"] / "bin/python"
    code, raw = _run(_command(interpreter, checkout, work, service, probe=True), checkout,
                     service, budget, limit=4096)
    _require(code == 0, service, "import_origin")
    package = checkout / spec["package"].replace(".", "/")
    root_init, service_init = checkout / "src/__init__.py", package / "__init__.py"
    expected = {"package": str(root_init) if root_init.is_file() else None,
        "package_paths": [str(checkout / "src")],
        "service_package": str(service_init) if service_init.is_file() else None, "service_paths": [str(package)],
        "module": str(checkout / (spec["module"].replace(".", "/") + ".py")),
        "executable": str(interpreter),
        "prefix": str(interpreter.parent.parent)}
    _require(_decode(raw, 4096, service, "import_origin") == expected, service, "import_origin")
    return expected


def _observe(raw, service, component, returncode):
    """Closed parsing follows actual invocation, never a caller's assertion."""
    result = _decode(raw, admission.MAX_RESULT_BYTES, service, "producer_result_malformed")
    _require(type(result) is dict and type(result.get("valid")) is bool
             and returncode == (0 if result["valid"] else _SPECS[service]["invalid_exit"]),
             service, "exit_receipt_mismatch")
    envelope = {key: copy.deepcopy(component[key]) for key in ("source_revision", "contract_digest", "input_digests")}
    envelope["result"] = result
    status, descriptor = admission._observe(admission.encode(envelope), service, component)
    _require(status["status"] != "unvalidated", service, status["code"])
    if status["status"] in {"invalid", "disabled"}:
        error = PreflightError(service, "producer_" + status["status"])
        error.component_status = status["status"]
        raise error
    _require(status["status"] == "valid" and descriptor is not None, service, "producer_result_malformed")
    return result, descriptor


def _run_producer(root, checkout, work, service, component, inputs, budget):
    spec = _SPECS[service]
    selected = {}
    for name, raw in inputs.items():
        path = work / (spec["source"] + "-" + name + ".json")
        _write_new(path, raw)
        selected[name] = str(path)
    if service == "openjules":
        arguments = ["--config", selected["configuration"], "--scope-declarations", selected["scope_declarations"],
                     "--expected-contract", component["validation_contract"],
                     "--expected-contract-digest", component["contract_digest"]]
    else:
        arguments = ["validate-installation", "--credentials", selected["credentials"],
                     "--managed-config", selected["managed_policy"],
                     "--validation-contract", component["validation_contract"], "--json"]
    interpreter = root / "environments" / spec["source"] / "bin/python"
    code, raw = _run(_command(interpreter, checkout, work, service, arguments=arguments),
                     checkout, service, budget)
    for name, path in selected.items():
        _require(_read(Path(path), admission.INPUTS[service][name], service, private=True) == inputs[name],
                 service, "input_mismatch")
    return _observe(raw, service, component, code)


def _bootstrap_evidence(root, data):
    evidence = {}
    for service in ("openjules", "polyphony", "zpm"):
        owner = "keystone-polyphony" if service == "polyphony" else service
        relative = "environments/" + service + "/bootstrap-complete.json"
        raw = _read(root / relative, 65_536, owner, private=True)
        marker = _decode(raw, 65_536, owner, "bootstrap_required")
        _require(type(marker) is dict and set(marker) == {"revision", "python"}
                 and marker["revision"] == data["sources"][service]["revision"]
                 and type(marker["python"]) is str and bool(marker["python"]),
                 owner, "bootstrap_required")
        evidence[relative] = _hash(raw)
    try:
        selector = installation_context.validate_selector(root)
    except ConfigurationError:
        raise PreflightError("openjules", "bootstrap_required") from None
    evidence["selector"] = _hash(selector)
    return evidence


def _state_snapshot(root, budget, *, owned_work=None):
    # Metadata detects newly created journals or changed retained state without
    # reading arbitrary task content. Only this module's own receipt/status and
    # bounded temporary inputs may be written by preflight.
    result, count = {}, 0
    state = root / "state"
    excluded = {root / RECEIPT, root / STATUS, state / "stack.lock"}
    if owned_work is not None:
        excluded.add(owned_work)
    paths = [state]
    def unreadable(error):
        raise error
    for directory, directories, files in os.walk(state, followlinks=False, onerror=unreadable):
        budget.remaining()
        directories[:] = [name for name in directories if Path(directory) / name not in excluded]
        observed = [Path(directory) / name for name in [*directories, *files]
                    if Path(directory) / name not in excluded]
        count += len(observed)
        _require(count <= 4096, "zpm", "installation_changed")
        paths.extend(observed)
    for path in paths:
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode), "zpm", "installation_changed")
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
        # Our exact temporary directory changes only the top directory's entry
        # timestamps/size. Every other entry, including WAL/SHM and new journals,
        # remains in the comparison; no name-prefix exclusion is used.
        result[str(path.relative_to(root))] = (identity if path == state else
            (*identity, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return result


def _publish(path, raw, *, replace=False, receipt_id=None):
    _require(path.parent.resolve(strict=True) == path.parent and not path.parent.stat().st_mode & 0o077,
             "zpm", "receipt_unavailable")
    if os.path.lexists(path):
        prior = _read(path, 4096 if replace else admission.MAX_RESULT_BYTES, "zpm", private=True)
        if not replace:
            _require(prior == raw, "zpm", "retained_receipt_mismatch")
            return
        value = _decode(prior, 4096, "zpm", "diagnostic_unavailable")
        _require(type(value) is dict and value.get("receipt_contract") == CONTRACT
                 and value.get("receipt_id") == receipt_id, "zpm", "diagnostic_unavailable")
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".installation-receipt-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                _require(_read(path, admission.MAX_RESULT_BYTES, "zpm", private=True) == raw,
                         "zpm", "retained_receipt_mismatch")
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        _require(_read(path, admission.MAX_RESULT_BYTES, "zpm", private=True) == raw,
                 "zpm", "receipt_unavailable")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _status(root, report):
    established = os.path.lexists(root / RECEIPT)
    if os.path.lexists(root / STATUS):
        previous = _decode(_read(root / STATUS, 4096, "zpm", private=True), 4096,
                           "zpm", "diagnostic_unavailable")
        _require(type(previous) is dict, "zpm", "diagnostic_unavailable")
        established = (established or previous.get("receipt_established") is True
                       or previous.get("phase") == "native-config-validated")
    # This negative recovery fact is not positive admission evidence. It only
    # prevents loss of an original receipt being mistaken for a first attempt.
    _publish(root / STATUS, admission.encode(report | {"receipt_established": established}),
             replace=True, receipt_id=report["receipt_id"])


def preflight(root: Path, data: dict) -> dict:
    with interruptible():
        _require(enabled(data), "zpm", "not_selected")
        budget = _Budget()
        with _coordination(root, exclusive=True, budget=budget) as custody:
            return _preflight(root, data, budget=budget, custody=custody)


def _preflight(root: Path, data: dict, *, budget=None, custody=None) -> dict:
    """Fresh paired validation; callers hold the existing deployment lock.

    The first genuine KP descriptor supplies its producer-owned profile digest.
    Every later operation repeats both native commands and compares the whole
    newly established receipt with the original, without trusting stored flags.
    """
    budget, receipt_id, states = budget or _Budget(), None, {}
    try:
        root = Path(root)
        _require(root.is_absolute() and root.resolve(strict=True) == root
                 and not root.stat().st_mode & 0o077, "zpm", "input_unavailable")
        selected, selected_raw, inputs, _, manifest_raw = _prepared(root, data)
        if custody is not None:
            custody()
        receipt_id = selected["receipt_id"]
        prior = _retained(root, selected, selected_raw)
        bootstrap = _bootstrap_evidence(root, data)
        _require(prior is None or bootstrap == prior["bootstrap_evidence"], "zpm", "retained_receipt_mismatch")
        state = _state_snapshot(root, budget)
        sources, interpreters, probes, results, descriptors = {}, {}, {}, {}, {}
        with tempfile.TemporaryDirectory(prefix=".installation-validation-", dir=root / "state") as temporary:
            work = Path(temporary)
            for service, spec in _SPECS.items():
                checkout = Path(data["sources"][spec["source"]]["checkout"])
                sources[service] = _source(checkout, selected["components"][service]["source_revision"], service, budget)
                interpreters[service] = _interpreter(root, spec["source"], service)
                if prior is not None:
                    _require(prior["components"][service]["source_evidence"] == sources[service]
                             and prior["components"][service]["interpreter"] == interpreters[service],
                             service, "retained_receipt_mismatch")
            # All selected producer identities are checked before the first
            # producer process. A later component's drift cannot run an earlier
            # component under the old receipt as a side effect of refusal.
            for service, spec in _SPECS.items():
                checkout = Path(data["sources"][spec["source"]]["checkout"])
                probes[service] = _probe(root, checkout, work, service, budget)
                _require(prior is None or prior["components"][service]["import_probe"] == probes[service],
                         service, "retained_receipt_mismatch")
                results[service], descriptors[service] = _run_producer(root, checkout, work, service,
                    selected["components"][service], inputs[service], budget)
                states[service] = {"status": "valid"}
            # Native KP validation is now established independently. Use only
            # its descriptor to obtain its digest, while matching declared facts.
            authority = descriptors["keystone-polyphony"]
            projection = [{key: item[key] for key in ("repository", "profile", "base", "capabilities")}
                          for item in authority["profiles"]]
            _require(projection == selected["profiles"], "keystone-polyphony", "scope_mismatch")
            profiles = [{**expected, "profile_digest": actual["profile_digest"]}
                        for expected, actual in zip(selected["profiles"], authority["profiles"])]
            pure_selection = {key: selected[key] for key in ("schema_version", "deployment_id", "installation_plan_digest")}
            pure_selection["components"] = {service: {key: value for key, value in component.items()
                                                      if key != "input_digests"}
                                             for service, component in selected["components"].items()}
            pure_selection["profiles"] = profiles
            receipt = admission.prepare_receipt(admission.encode(pure_selection), inputs, receipt_id=receipt_id)
            compatible, code = admission._compatible(receipt, descriptors["openjules"], authority)
            _require(compatible is True, "zpm", code)
            for service, spec in _SPECS.items():
                checkout = Path(data["sources"][spec["source"]]["checkout"])
                _require(_source(checkout, selected["components"][service]["source_revision"], service, budget)
                         == sources[service], service, "source_identity")
                _require(_interpreter(root, spec["source"], service) == interpreters[service],
                         service, "interpreter_identity")
            for service, spec in _SPECS.items():
                checkout = Path(data["sources"][spec["source"]]["checkout"])
                _require(_probe(root, checkout, work, service, budget) == probes[service], service, "import_origin")
            for service, spec in _SPECS.items():
                checkout = Path(data["sources"][spec["source"]]["checkout"])
                _require(_source(checkout, selected["components"][service]["source_revision"], service, budget)
                         == sources[service], service, "source_identity")
                _require(_interpreter(root, spec["source"], service) == interpreters[service],
                         service, "interpreter_identity")
            current = _prepared(root, data)
            _require(current[1] == selected_raw and current[2] == inputs and current[4] == manifest_raw
                     and _bootstrap_evidence(root, data) == bootstrap
                     and _state_snapshot(root, budget, owned_work=work) == state, "zpm", "installation_changed")
            receipt.update(phase="native-config-validated", compatible=True,
                           source_identity_verified=True, native_validation_performed=True,
                           prepared_selection_sha256=_hash(selected_raw), bootstrap_evidence=bootstrap)
            for service in admission.SERVICES:
                receipt["components"][service].update(valid=True, native_result=results[service],
                    source_evidence=sources[service], interpreter=interpreters[service], import_probe=probes[service])
            raw_receipt = admission.encode(receipt)
            budget.remaining()
            if custody is not None:
                custody()
            _publish(root / RECEIPT, raw_receipt)
        report = _report(receipt_id, success=True)
        if custody is not None:
            custody()
        _status(root, report)
        budget.remaining()
        return report
    except PreflightError as error:
        failure = error
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ConfigurationError):
        failure = PreflightError("zpm", "producer_result_malformed")
    if failure.component_status is not None:
        states[failure.owner] = {"status": failure.component_status}
    failure.report = _report(receipt_id, owner=failure.owner, code=failure.code,
        components={service: states.get(service, {"status": "unvalidated"}) for service in admission.SERVICES})
    if receipt_id is not None:
        try:
            _status(root, failure.report)
        except (OSError, ValueError, ConfigurationError):
            pass  # The same bounded refusal remains available on the CLI error channel.
    raise failure
