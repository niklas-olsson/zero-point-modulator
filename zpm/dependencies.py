"""Finite, operator-approved Python artifacts for the existing installer.

Preparation reads data and Git objects only. Package code runs solely during
explicit bootstrap. The operator and the installation UID are trusted; approved
wheels and build hooks are not sandboxed. Other users cannot replace pip inputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import email.parser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
import zipfile

from . import installation_context
from .installation_roles import role_services, services as installation_services
from .models import ConfigurationError

SERVICES = ("zpm", "openjules", "polyphony")
PLAN_LIMIT = 1_000_000
ARTIFACT_LIMIT = 128 * 1024 * 1024
TOTAL_LIMIT = 1024 * 1024 * 1024
SOURCE_LIMIT = 32 * 1024 * 1024
OUTPUT_LIMIT = 1_000_000
HASH = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
WHEEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+!-]{0,240}\.whl")
ERROR = "sealed dependency inputs are invalid or changed"
_MANAGER_FD = ContextVar("sealed_bootstrap_manager_fd", default=None)


@contextmanager
def command_guard(lock_fd):
    token = _MANAGER_FD.set(lock_fd)
    try:
        yield
    finally:
        _MANAGER_FD.reset(token)


def _fail(message=ERROR):
    raise ConfigurationError(message)


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError, RecursionError):
        _fail()


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    if not isinstance(value, str) or HASH.fullmatch(value) is None:
        _fail()
    return value


def _name(value):
    if not isinstance(value, str) or NAME.fullmatch(value) is None or len(value) > 128:
        _fail()
    return value


@contextmanager
def _directory(path, *, private=False):
    """Open every path component without following links; pin the final dir."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        _fail()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            _fail()
        yield descriptor
    except OSError:
        _fail()
    finally:
        os.close(descriptor)


def _read(path, limit, *, expected=None, size=None, private=False):
    path = Path(path)
    try:
        with _directory(path.parent, private=private) as parent:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_size > limit or before.st_mode & 0o022
                        or (private and (before.st_uid != os.geteuid() or before.st_mode & 0o077))
                        or (size is not None and before.st_size != size)):
                    _fail()
                raw = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if (len(raw) > limit or len(raw) != before.st_size
                        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or (expected is not None and _digest(raw) != expected)):
                    _fail()
                return raw
    except OSError:
        _fail()


def _write(path, raw, *, readonly=False):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        if readonly:
            os.fchmod(stream.fileno(), 0o400)


def _publish(path, value):
    """Private exact-value publication, never repair or replace retained evidence."""
    raw = _encoded(value)
    if len(raw) > PLAN_LIMIT:
        _fail()
    if path.exists() or path.is_symlink():
        if _read(path, PLAN_LIMIT, private=True) != raw:
            _fail("sealed bootstrap evidence differs; retain the original installation")
        return
    temporary = path.parent / (".sealed-" + os.urandom(12).hex())
    _write(temporary, raw)
    try:
        os.link(temporary, path, follow_symlinks=False)
    except FileExistsError:
        if _read(path, PLAN_LIMIT, private=True) != raw:
            _fail()
    finally:
        temporary.unlink()
    with _directory(path.parent, private=True) as descriptor:
        os.fsync(descriptor)


def interpreter_identity():
    """Only acquisition/bootstrap calls this; offline validation never does."""
    return {"implementation": sys.implementation.name,
            "version": ".".join(map(str, sys.version_info[:3])),
            "cache_tag": sys.implementation.cache_tag,
            "abi": sysconfig.get_config_var("SOABI"),
            "platform": sysconfig.get_platform()}


def wheel_identity(raw):
    """Read bounded metadata without extracting or importing package code."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
            members = wheel.infolist()
            if len(members) > 20000 or len({x.filename for x in members}) != len(members):
                _fail()
            metadata = [x for x in members if x.filename.count("/") == 1
                        and x.filename.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or metadata[0].file_size > PLAN_LIMIT:
                _fail()
            for member in members:
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    _fail()
                if stat.S_ISLNK(member.external_attr >> 16):
                    _fail()
            fields = email.parser.BytesParser().parsebytes(wheel.read(metadata[0]), headersonly=True)
            if len(fields.get_all("Name", [])) != 1 or len(fields.get_all("Version", [])) != 1:
                _fail()
            name = re.sub(r"[-_.]+", "-", fields["Name"]).lower()
            version = fields["Version"]
            if not version or len(version) > 128 or any(c.isspace() for c in version):
                _fail()
            return {"name": _name(name), "version": version}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError):
        _fail()


def _roots(service, path, expected):
    raw = _read(path, PLAN_LIMIT, expected=expected)
    try:
        if service == "zpm":
            project = tomllib.loads(raw.decode())
            build = project["build-system"]
            if (not isinstance(build, dict) or not isinstance(project.get("project"), dict)
                    or build.get("build-backend") != "setuptools.build_meta" or "backend-path" in build
                    or not isinstance(build.get("requires"), list)):
                _fail("sealed ZPM builds require the declared setuptools backend")
            roots = build["requires"] + project["project"].get("dependencies", []) + ["pip", "wheel"]
        else:
            roots = [line.split("#", 1)[0].strip() for line in raw.decode().splitlines()]
            roots = [line for line in roots if line]
        if not roots or len(roots) > 512 or any(
                not isinstance(line, str) or not re.match(r"^[A-Za-z0-9]", line)
                or any(c in line for c in "@:/\\\n\r") or len(line) > 1024 for line in roots):
            _fail("sealed dependencies require simple package requirements without URLs or options")
        return roots
    except (KeyError, TypeError, ValueError, UnicodeError):
        _fail()


def _load(reference, sources, *, artifacts=True, role=None):
    _object(reference, {"manifest", "sha256", "artifacts"})
    for name in ("manifest", "artifacts"):
        if not isinstance(reference[name], str) or not Path(reference[name]).is_absolute():
            _fail()
    raw = _read(Path(reference["manifest"]), PLAN_LIMIT, expected=_hash(reference["sha256"]))
    plan = _json(raw)
    _object(plan, {"schema_version", "interpreter", "seed", "artifacts", "environments"})
    selected = SERVICES if role is None else role_services(role)
    if set(sources) != set(selected):
        _fail("sealed source set differs from the declared installer role")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != (1 if role is None else 2):
        _fail()
    identity = _object(plan["interpreter"], {"implementation", "version", "cache_tag", "abi", "platform"})
    if (any(not isinstance(x, str) or not x or len(x) > 128 for x in identity.values())
            or identity["implementation"] != "cpython" or not re.fullmatch(r"3\.(11|12)\.\d+", identity["version"])
            or not identity["platform"].startswith("linux-")):
        _fail("sealed dependencies support explicit CPython 3.11 or 3.12 Linux tuples")
    seed = _object(plan["seed"], {"method", "filename"})
    if seed["method"] != "sealed-pip-wheel" or not isinstance(seed["filename"], str):
        _fail()
    rows = plan["artifacts"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 512:
        _fail()
    if any(not isinstance(row, dict) or type(row.get("size")) is not int
           or not 0 < row["size"] <= ARTIFACT_LIMIT for row in rows):
        _fail()
    if sum(row["size"] for row in rows) > TOTAL_LIMIT:
        _fail()
    inventory = {}
    total = 0
    for row in rows:
        _object(row, {"filename", "size", "sha256", "name", "version"})
        filename = row["filename"]
        if (not isinstance(filename, str) or WHEEL.fullmatch(filename) is None or filename in inventory
                or type(row["size"]) is not int or not 0 < row["size"] <= ARTIFACT_LIMIT):
            _fail()
        _hash(row["sha256"])
        _name(row["name"])
        if not isinstance(row["version"], str) or not row["version"] or len(row["version"]) > 128:
            _fail()
        total += row["size"]
        inventory[filename] = row
        if artifacts:
            content = _read(Path(reference["artifacts"]) / filename, ARTIFACT_LIMIT,
                            expected=row["sha256"], size=row["size"])
            if wheel_identity(content) != {key: row[key] for key in ("name", "version")}:
                _fail()
    if total > TOTAL_LIMIT or seed["filename"] not in inventory or inventory[seed["filename"]]["name"] != "pip":
        _fail()
    _object(plan["environments"], selected)
    used = set()
    for service, environment in plan["environments"].items():
        _object(environment, {"source_revision", "dependency_files", "wheels"})
        if environment["source_revision"] != sources[service]["revision"]:
            _fail("sealed producer revision differs from the installation")
        filename = "pyproject.toml" if service == "zpm" else "requirements.txt"
        _object(environment["dependency_files"], {filename})
        _read(Path(sources[service]["checkout"]) / filename, PLAN_LIMIT,
              expected=_hash(environment["dependency_files"][filename]))
        _roots(service, Path(sources[service]["checkout"]) / filename, environment["dependency_files"][filename])
        wheels = environment["wheels"]
        if (not isinstance(wheels, list) or not wheels or len(wheels) > 512
                or any(not isinstance(x, str) or x not in inventory for x in wheels)
                or len(set(wheels)) != len(wheels) or seed["filename"] not in wheels):
            _fail()
        names = [inventory[x]["name"] for x in wheels]
        if len(set(names)) != len(names) or "zero-point-modulator" in names:
            _fail()
        if service == "zpm" and not {"pip", "wheel", "setuptools"} <= set(names):
            _fail("sealed ZPM builds require pip, setuptools and wheel artifacts")
        used.update(wheels)
    if used != set(inventory):
        _fail("sealed plans must not contain unused artifacts")
    return plan, raw, inventory


def normalize_reference(value, sources, *, role=None):
    _load(value, sources, role=role)
    return dict(value)


def mode(data):
    return "sealed-local-wheels-v2" if data.get("schema_version") == 3 else "sealed-local-wheels-v1"


def _source_files(source):
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-C", source["checkout"], "ls-tree", "-rz", source["revision"]]
    listing = subprocess.run(command, check=True, capture_output=True, timeout=15).stdout
    if len(listing) > PLAN_LIMIT:
        _fail()
    records = listing.split(b"\0")[:-1]
    if not records or len(records) > 2048:
        _fail()
    total = 0
    for record in records:
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = raw_path.decode()
        if (mode not in {"100644", "100755"} or kind != "blob" or Path(path).is_absolute()
                or ".." in Path(path).parts or "\\" in path):
            _fail("sealed ZPM sources require regular tracked files")
        content = _read(Path(source["checkout"]) / path, SOURCE_LIMIT)
        total += len(content)
        if total > SOURCE_LIMIT:
            _fail()
        algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
        if algorithm(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() != oid:
            _fail("ZPM source changed while it was being sealed")
        yield path, content


def seal(reference, sources, root, *, role=None):
    plan, raw, inventory = _load(reference, sources, artifacts=False, role=role)
    selected = SERVICES if role is None else role_services(role)
    directory = root / "dependencies"
    directory.mkdir(mode=0o700)
    wheelhouse = directory / "wheels"
    wheelhouse.mkdir(mode=0o700)
    _write(directory / "plan.json", raw, readonly=True)
    records = {"dependencies/plan.json": _digest(raw)}
    for filename, row in inventory.items():
        content = _read(Path(reference["artifacts"]) / filename, ARTIFACT_LIMIT,
                        expected=row["sha256"], size=row["size"])
        if wheel_identity(content) != {key: row[key] for key in ("name", "version")}:
            _fail()
        _write(wheelhouse / filename, content, readonly=True)
        records["dependencies/wheels/" + filename] = row["sha256"]
    source = directory / "source"
    source.mkdir(mode=0o700)
    for path, content in _source_files(sources["zpm"]):
        target = source / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(target, content, readonly=True)
        records["dependencies/source/" + path] = _digest(content)
    for service in selected:
        filename = "pyproject.toml" if service == "zpm" else "requirements.txt"
        roots = _roots(service, Path(sources[service]["checkout"]) / filename,
                       plan["environments"][service]["dependency_files"][filename])
        content = _encoded(roots)
        _write(directory / (service + "-roots.json"), content, readonly=True)
        records["dependencies/" + service + "-roots.json"] = _digest(content)
    _write(directory / "files.json", _encoded(records), readonly=True)
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o500)
    directory.chmod(0o500)


def verify(reference, sources, root, *, role=None):
    plan, raw, inventory = _load(reference, sources, role=role)
    selected = SERVICES if role is None else role_services(role)
    directory = root / "dependencies"
    if _read(directory / "plan.json", PLAN_LIMIT, private=True) != raw:
        _fail()
    records = _json(_read(directory / "files.json", PLAN_LIMIT, private=True))
    if not isinstance(records, dict) or not records:
        _fail()
    expected = {"dependencies/plan.json", *("dependencies/wheels/" + x for x in inventory),
                *("dependencies/" + x + "-roots.json" for x in selected)}
    expected.update("dependencies/source/" + path for path, _ in _source_files(sources["zpm"]))
    if set(records) != expected:
        _fail()
    for path, digest in records.items():
        _read(root / path, ARTIFACT_LIMIT, private=True, expected=_hash(digest))
    for filename, row in inventory.items():
        if records["dependencies/wheels/" + filename] != row["sha256"]:
            _fail()
    for path, content in _source_files(sources["zpm"]):
        if records["dependencies/source/" + path] != _digest(content):
            _fail()
    return plan, inventory


def _run(command, cwd, environment, seconds=300, *, allowed_returncodes=(0,), failure_directory=None, label="sealed bootstrap"):
    """Bound time and output, kill/reap the owned process group on failure."""
    control, parent = os.pipe()
    lock = _MANAGER_FD.get()
    guardian = [sys.executable, "-I", str(Path(__file__).with_name("_bootstrap_process.py")),
                str(control), str(-1 if lock is None else lock), *command]
    try:
        process = subprocess.Popen(guardian, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   pass_fds=(control,) if lock is None else (control, lock))
    except BaseException:
        os.close(parent)
        raise
    finally:
        os.close(control)
    output = bytearray()
    deadline = time.monotonic() + seconds
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail(label + " command exceeded its time bound")
                for key, _ in selector.select(min(remaining, 0.25)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    output.extend(chunk)
                    if len(output) > OUTPUT_LIMIT:
                        _fail(label + " command exceeded its output bound")
            result = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if result not in allowed_returncodes:
            _fail(f"{label} command failed with exit {result}; bounded output retained privately")
        return bytes(output)
    finally:
        os.close(parent)  # EOF cancels the group even if the manager was killed.
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _fail(label + " cleanup is unconfirmed; the inherited manager lock remains held")
        process.stdout.close()
        if sys.exc_info()[0] is not None:
            _write(Path(cwd if failure_directory is None else failure_directory) / (".sealed-command-" + os.urandom(12).hex() + ".log"), bytes(output[:OUTPUT_LIMIT]))


_INSTALLED = r'''
import base64, hashlib, importlib.metadata as m, json, pathlib, re, sys, sysconfig
prefix = pathlib.Path(sys.prefix).resolve()
assert prefix == pathlib.Path(sys.argv[1]).resolve()
assert {"implementation": sys.implementation.name, "version": ".".join(map(str, sys.version_info[:3])),
        "cache_tag": sys.implementation.cache_tag, "abi": sysconfig.get_config_var("SOABI"),
        "platform": sysconfig.get_platform()} == json.loads(sys.argv[2])
result = {}
for dist in m.distributions():
    name = re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower()
    assert name not in result
    digest = hashlib.sha256()
    assert dist.files is not None
    for entry in sorted(dist.files, key=str):
        path = pathlib.Path(dist.locate_file(entry))
        assert not path.is_symlink()
        resolved = path.resolve(strict=True)
        assert resolved.is_relative_to(prefix)
        if str(entry).endswith(".pyc"):
            continue
        content = resolved.read_bytes()
        if entry.hash:
            actual = base64.urlsafe_b64encode(hashlib.new(entry.hash.mode, content).digest()).rstrip(b"=").decode()
            assert actual == entry.hash.value
        else:
            assert str(entry).endswith(".dist-info/RECORD")
        digest.update(str(entry).encode() + b"\0" + hashlib.sha256(content).digest())
    result[name] = {"version": dist.version, "files_sha256": digest.hexdigest()}
print(json.dumps(result, sort_keys=True))
'''

_CHECK_ROOTS = r'''
import importlib.metadata as m, json, re, sys
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.markers import default_environment
installed = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d for d in m.distributions()}
queue = [(line, "") for line in json.load(open(sys.argv[1]))]
seen = set()
while queue:
    line, extra = queue.pop()
    req = Requirement(line)
    assert req.url is None
    if req.marker and not req.marker.evaluate(default_environment() | {"extra": extra}):
        continue
    name = re.sub(r"[-_.]+", "-", req.name).lower()
    assert name in installed and req.specifier.contains(installed[name].version, prereleases=True)
    provided = {re.sub(r"[-_.]+", "-", x).lower() for x in installed[name].metadata.get_all("Provides-Extra", [])}
    assert {re.sub(r"[-_.]+", "-", x).lower() for x in req.extras} <= provided
    for scope in ["", *sorted(req.extras)]:
        if (name, scope) not in seen:
            seen.add((name, scope))
            queue.extend((child, scope) for child in (installed[name].requires or []))
print("requirements-satisfied")
'''

_PIP_SEED = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('pip',run_name='__main__')"


def _installed(python, cwd, environment):
    value = _json(_run([str(python), "-I", "-c", _INSTALLED, str(Path(python).parent.parent),
                       json.dumps(interpreter_identity())], cwd, environment, 120))
    if not isinstance(value, dict):
        _fail()
    return value


def _build_expected(root, data, plan, inventory):
    records = _json(_read(root / "dependencies/files.json", PLAN_LIMIT, private=True))
    return {"schema_version": 1, "source_revision": data["sources"]["zpm"]["revision"],
            "source_files_sha256": _digest(_encoded({k: v for k, v in records.items()
                                                       if k.startswith("dependencies/source/")})),
            "plan_sha256": data["dependency_plan"]["sha256"], "interpreter": plan["interpreter"],
            "build_distributions": {inventory[x]["name"]: inventory[x]["version"]
                                    for x in plan["environments"]["zpm"]["wheels"]}}


def _built(root, data, plan, inventory):
    directory = root / "environments/zpm/sealed-build"
    record_bytes = _read(directory / "record.json", PLAN_LIMIT, private=True)
    record = _json(record_bytes)
    expected = _build_expected(root, data, plan, inventory)
    if (not isinstance(record, dict) or set(record) != {*expected, "wheel", "wheel_sha256"}
            or any(record[k] != v for k, v in expected.items())
            or not isinstance(record["wheel"], str) or WHEEL.fullmatch(record["wheel"]) is None):
        _fail("retained ZPM build differs from the admitted source and toolset")
    wheel = directory / record["wheel"]
    content = _read(wheel, ARTIFACT_LIMIT, private=True, expected=_hash(record["wheel_sha256"]))
    project = tomllib.loads(_read(root / "dependencies/source/pyproject.toml", PLAN_LIMIT, private=True).decode())
    if wheel_identity(content) != {"name": "zero-point-modulator", "version": project["project"]["version"]}:
        _fail()
    return wheel, record["wheel_sha256"], _digest(record_bytes)


def _build(root, data, plan, inventory, scratch, python, environment, common):
    stable = root / "environments/zpm/sealed-build"
    if stable.exists() or stable.is_symlink():
        return _built(root, data, plan, inventory)
    source = scratch / "source"
    source.mkdir(mode=0o700)
    records = _json(_read(root / "dependencies/files.json", PLAN_LIMIT, private=True))
    for name, digest in records.items():
        if name.startswith("dependencies/source/"):
            target = source / name.removeprefix("dependencies/source/")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write(target, _read(root / name, SOURCE_LIMIT, expected=digest, private=True))
    output = scratch / "built"
    output.mkdir(mode=0o700)
    _run([str(python), "-I", "-m", "pip", *common, "wheel", "--no-index", "--no-deps",
          "--no-build-isolation", "--wheel-dir", str(output), str(source)], scratch, environment, 120)
    built = list(output.iterdir())
    if len(built) != 1 or WHEEL.fullmatch(built[0].name) is None:
        _fail()
    content = _read(built[0], ARTIFACT_LIMIT)
    record = _build_expected(root, data, plan, inventory) | {"wheel": built[0].name, "wheel_sha256": _digest(content)}
    stage = scratch / "build-evidence"
    stage.mkdir(mode=0o700)
    _write(stage / built[0].name, content, readonly=True)
    _write(stage / "record.json", _encoded(record), readonly=True)
    with _directory(stage, private=True) as descriptor:
        os.fsync(descriptor)
    # The manager lock serializes trusted writers; the private destination is
    # absent. No completed build or existing environment is overwritten.
    if stable.exists() or stable.is_symlink():
        _fail()
    os.rename(stage, stable)
    stable.chmod(0o500)
    with _directory(stable.parent, private=True) as descriptor:
        os.fsync(descriptor)
    return _built(root, data, plan, inventory)


def _discard_scratch(path, identity):
    info = path.lstat()
    if path.is_symlink() or (info.st_dev, info.st_ino) != identity:
        _fail("owned bootstrap scratch changed; retain it for inspection")
    for directory, _, _ in os.walk(path, followlinks=False):
        with _directory(Path(directory)) as descriptor:
            if os.fstat(descriptor).st_uid != os.geteuid():
                _fail("bootstrap scratch contains foreign-owned files; retain it for inspection")
            os.fchmod(descriptor, 0o700)
    shutil.rmtree(path)


def _completion(root, data, service, plan, inventory, environment, *, require=False):
    directory = root / "environments" / service
    receipt = directory / "sealed-complete.json"
    if not receipt.exists() and not receipt.is_symlink():
        if require or (directory / "bootstrap-complete.json").exists():
            _fail("sealed bootstrap is incomplete or has foreign completion evidence")
        return False
    value = _json(_read(receipt, PLAN_LIMIT, private=True))
    expected = {"schema_version": 1, "plan_sha256": data["dependency_plan"]["sha256"],
                "source_revision": data["sources"][service]["revision"],
                "interpreter": plan["interpreter"],
                "build_record_sha256": _built(root, data, plan, inventory)[2] if service == "zpm" else None}
    if not isinstance(value, dict) or set(value) != {*expected, "installed"} or any(value[k] != v for k, v in expected.items()):
        _fail("sealed bootstrap completion differs from the admitted plan")
    current = _installed(directory / "bin/python", directory, environment)
    if current != value["installed"]:
        _fail("sealed installed distribution content changed; retain the original installation")
    _expected_set(current, service, root, plan, inventory)
    return True


def _expected_set(installed, service, root, plan, inventory):
    expected = {inventory[x]["name"]: inventory[x]["version"] for x in plan["environments"][service]["wheels"]}
    if service == "zpm":
        project = tomllib.loads(_read(root / "dependencies/source/pyproject.toml", PLAN_LIMIT, private=True).decode())
        expected["zero-point-modulator"] = project["project"]["version"]
    if {name: item["version"] for name, item in installed.items()} != expected:
        _fail("sealed installed distribution set differs from the admitted plan")


def check_completed(root, data, environment):
    plan, inventory = verify(data["dependency_plan"], data["sources"], root,
                             role=data["role"] if data["schema_version"] == 3 else None)
    if interpreter_identity() != plan["interpreter"]:
        _fail("sealed bootstrap interpreter does not match the admitted tuple")
    for service in installation_services(data):
        _completion(root, data, service, plan, inventory, environment, require=True)


def bootstrap(root, data, environment):
    plan, inventory = verify(data["dependency_plan"], data["sources"], root,
                             role=data["role"] if data["schema_version"] == 3 else None)
    if interpreter_identity() != plan["interpreter"]:
        _fail("sealed bootstrap interpreter does not match the admitted tuple")
    identity = {"schema_version": 1, "plan_sha256": data["dependency_plan"]["sha256"],
                "interpreter": plan["interpreter"],
                "sources": {key: value["revision"] for key, value in data["sources"].items()}}
    _publish(root / "environments/sealed-intent.json", identity)
    if "openjules" in installation_services(data):
        if installation_context.completed(root, data["sources"]["openjules"]["revision"]):
            installation_context.probe(root, Path(data["sources"]["openjules"]["checkout"]), environment,
                                       require_complete=True, revision=data["sources"]["openjules"]["revision"])
        else:
            installation_context.validate_selector(root, required=False)
    for service in installation_services(data):
        directory = root / "environments" / service
        intent = root / "environments" / (service + "-sealed-intent.json")
        if directory.exists() and not intent.exists():
            _fail("sealed bootstrap refuses an environment without its original intent")
        _publish(intent, identity | {"service": service})
        if directory.is_symlink():
            _fail()
        directory.mkdir(mode=0o700, exist_ok=True)
        with _directory(directory, private=True):
            pass
        if _completion(root, data, service, plan, inventory, environment):
            _publish(directory / "bootstrap-complete.json", {"revision": data["sources"][service]["revision"], "python": sys.version})
            continue
        # A fresh private scratch directory holds only bytes read from our sealed
        # bundle. It is inaccessible to other UIDs; pip never opens caller paths.
        prefix = ".sealed-bootstrap-" + service + "-"
        if len(list((root / "environments").glob(prefix + "*"))) >= 3:
            _fail("three interrupted sealed bootstrap stages remain; inspect and explicitly retire owned scratch before retry")
        scratch = Path(tempfile.mkdtemp(prefix=prefix, dir=root / "environments"))
        scratch_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
        (scratch / "home").mkdir(mode=0o700)
        (scratch / "tmp").mkdir(mode=0o700)
        clean = {key: value for key, value in environment.items() if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE"}}
        clean.update(HOME=str(scratch / "home"), TMPDIR=str(scratch / "tmp"),
                     PIP_CONFIG_FILE=os.devnull, PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1")
        wheels = scratch / "wheels"
        wheels.mkdir(mode=0o700)
        selected = plan["environments"][service]["wheels"]
        requirements = []
        for filename in selected:
            row = inventory[filename]
            content = _read(root / "dependencies/wheels" / filename, ARTIFACT_LIMIT,
                            expected=row["sha256"], size=row["size"], private=True)
            _write(wheels / filename, content, readonly=True)
            requirements.append((wheels / filename).as_uri() + " --hash=sha256:" + row["sha256"])
        wheels.chmod(0o500)
        _write(scratch / "requirements.txt", ("\n".join(requirements) + "\n").encode(), readonly=True)
        _run([sys.executable, "-I", "-m", "venv", "--without-pip", str(directory)], scratch, clean, 60)
        python = directory / "bin/python"
        # Reinstall the same finite set after interruption. Unknown packages are
        # not removed or blessed; they require operator investigation.
        current = _installed(python, scratch, clean)
        allowed = {inventory[x]["name"]: inventory[x]["version"] for x in selected}
        if service == "zpm":
            allowed["zero-point-modulator"] = tomllib.loads(_read(root / "dependencies/source/pyproject.toml", PLAN_LIMIT, private=True).decode())["project"]["version"]
        if any(name not in allowed or row["version"] != allowed[name] for name, row in current.items()):
            _fail("partial sealed environment contains unapproved distributions")
        common = ["--disable-pip-version-check", "--no-input", "--no-cache-dir"]
        _run([str(python), "-I", "-c", _PIP_SEED, str(wheels / plan["seed"]["filename"]),
              *common, "install", "--no-index", "--no-deps", "--require-hashes", "--only-binary=:all:",
              "--force-reinstall", "-r", str(scratch / "requirements.txt")], scratch, clean)
        if service == "zpm":
            built, built_hash, _ = _build(root, data, plan, inventory, scratch, python, clean, common)
            _write(scratch / "built-requirements.txt", (built.as_uri() + " --hash=sha256:" + built_hash + "\n").encode(), readonly=True)
            _run([str(python), "-I", "-m", "pip", *common, "install", "--no-index", "--no-deps",
                  "--require-hashes", "--only-binary=:all:", "--force-reinstall", "-r",
                  str(scratch / "built-requirements.txt")], scratch, clean)
        _run([str(python), "-I", "-m", "pip", *common, "check"], scratch, clean, 60)
        _run([str(python), "-I", "-c", _CHECK_ROOTS, str(root / "dependencies" / (service + "-roots.json"))], scratch, clean, 60)
        # Explicitly import only native entrypoints after approved packages have
        # installed. The source path is supplied, not inherited from PYTHONPATH.
        modules = {"zpm": "zpm.cli", "openjules": "src.worker.managed_service", "polyphony": "src.liminal_bridge.coordinator_service"}
        probe = "import importlib,sys;sys.path.insert(0,sys.argv[1]) if sys.argv[1] else None;importlib.import_module(sys.argv[2]);print('service-imported')"
        _run([str(python), "-I", "-c", probe, "" if service == "zpm" else data["sources"][service]["checkout"], modules[service]], scratch, clean, 60)
        installed = _installed(python, scratch, clean)
        _expected_set(installed, service, root, plan, inventory)
        if service == "openjules":
            installation_context.publish_selector(root)
            installation_context.probe(root, Path(data["sources"][service]["checkout"]), clean, require_complete=False)
        _publish(directory / "sealed-complete.json", {"schema_version": 1,
                 "plan_sha256": data["dependency_plan"]["sha256"], "source_revision": data["sources"][service]["revision"],
                 "interpreter": plan["interpreter"], "installed": installed,
                 "build_record_sha256": _built(root, data, plan, inventory)[2] if service == "zpm" else None})
        _publish(directory / "bootstrap-complete.json", {"revision": data["sources"][service]["revision"], "python": sys.version})
        _discard_scratch(scratch, scratch_identity)
    return {"installed": True, "dependency_mode": mode(data), "plan_sha256": data["dependency_plan"]["sha256"],
            "services_started": False, "remote_access_checked": False}
