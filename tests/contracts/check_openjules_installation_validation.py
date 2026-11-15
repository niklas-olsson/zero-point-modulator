"""Test-only qualification of the installed OpenJules schema-1 validator.

Call after explicit bootstrap, before the enclosing trial starts services. Raw
inputs, native output and correlation hashes stay in the private trial directory.
This does not activate ZPM preflight or validate the missing Polyphony producer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time


REVISION = "7135d0988dff050df63acb89f75562dafccacf79"
TREE = "7f94829abf16ba0e1e258421454cff3f5ac1de71"
CONTRACT = "openjules-installation-v1"
CONTRACT_DIGEST = "1eaa70ae5ea2b28259c7f7f1884bb93335f7f51cd3dc1edbaf58710f9644573b"
MODULE = "src.worker.installation_validation"
ARTIFACT = "src/worker/contracts/openjules-installation-v1.json"
OUTPUT_LIMIT = 8_000_000
PROBE = """import json,pathlib,sys
sys.path.append(sys.argv[1])
import src,src.worker
from src.worker import installation_validation as v
print(json.dumps({'module':str(pathlib.Path(v.__file__).resolve()),
 'package':str(pathlib.Path(src.__file__).resolve()),
 'package_paths':[str(pathlib.Path(p).resolve()) for p in src.__path__],
 'worker_package':str(pathlib.Path(src.worker.__file__).resolve()),
 'worker_paths':[str(pathlib.Path(p).resolve()) for p in src.worker.__path__],
 'contract':str(v.CONTRACT_PATH.resolve()),'executable':sys.executable,
 'prefix':sys.prefix},sort_keys=True))
"""
NATIVE = """import runpy,sys
sys.path.append(sys.argv.pop(1))
runpy.run_module('src.worker.installation_validation',run_name='__main__',alter_sys=True)
"""


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def private_file(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def bounded_file(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
                "native_validation_input_unavailable")
        raw = stream.read(limit + 1)
        require(len(raw) <= limit, "native_validation_input_unavailable")
        return raw


def git(checkout, *arguments):
    code, output = run_bounded(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
         "-C", str(checkout), *arguments], checkout, limit=262144, seconds=15,
    )
    require(code == 0, "native_validation_source_unavailable")
    return output


def verify_source(checkout, revision=REVISION, tree=TREE):
    """Independently bind working source bytes, including index-hidden edits."""
    checkout = Path(checkout)
    require(checkout.is_absolute() and checkout.resolve(strict=True) == checkout,
            "native_validation_source_path")
    require(git(checkout, "rev-parse", "--show-toplevel").decode().strip() == str(checkout)
            and git(checkout, "rev-parse", "HEAD").decode().strip() == revision
            and git(checkout, "rev-parse", "HEAD^{tree}").decode().strip() == tree
            and not git(checkout, "status", "--porcelain", "--untracked-files=all"),
            "native_validation_source_identity")
    source_bytes, source_paths = 0, set()
    for row in git(checkout, "ls-tree", "-rz", revision, "src").split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        relative = raw_path.decode("utf-8")
        require(mode in {b"100644", b"100755"} and kind == b"blob"
                and relative.startswith("src/") and ".." not in Path(relative).parts,
                "native_validation_source_entry")
        path = checkout / relative
        require(path.resolve(strict=True) == path, "native_validation_source_entry")
        raw = bounded_file(path, 2_000_000)
        source_bytes += len(raw)
        require(source_bytes <= 8_000_000, "native_validation_source_bound")
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        require(actual.encode("ascii") == oid, "native_validation_source_bytes")
        source_paths.add(relative)
    observed = set()
    for path in (checkout / "src").rglob("*"):
        require(not path.is_symlink(), "native_validation_source_entry")
        if "__pycache__" in path.relative_to(checkout).parts:
            continue
        if not path.is_dir():
            observed.add(str(path.relative_to(checkout)))
    require(observed == source_paths and MODULE.replace(".", "/") + ".py" in source_paths,
            "native_validation_source_inventory")
    require(digest(bounded_file(checkout / ARTIFACT, 65536)) == CONTRACT_DIGEST,
            "native_validation_static_contract")


def environment():
    # Deliberately exclude PYTHONPATH, provider/runtime credentials and proxies.
    return {name: value for name, value in os.environ.items()
            if name in {"PATH", "LANG", "LC_ALL", "LC_CTYPE"}} | {
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }


def native_command(interpreter, checkout, cache_prefix, arguments=(), *, probe=False):
    # Bootstrap installs requirements, not the src package. Add only the verified
    # checkout after the isolated interpreter's standard and installed paths.
    return [str(interpreter), "-I", "-B", "-X", "pycache_prefix=" + str(cache_prefix),
            "-c", PROBE if probe else NATIVE, str(checkout), *arguments]


def run_bounded(command, cwd, *, limit=OUTPUT_LIMIT, seconds=10):
    """Bound bytes during reading and retain the leader until group cleanup."""
    process, failure, answer = None, None, None
    try:
        deadline = time.monotonic() + seconds
        process = subprocess.Popen(command, cwd=cwd, env=environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True)
        output = bytearray()
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as watcher:
            watcher.register(process.stdout, selectors.EVENT_READ)
            ended = False
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, "native_validation_process_timeout")
                for key, _ in watcher.select(min(0.1, remaining)):
                    chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                    if not chunk:
                        watcher.unregister(key.fileobj)
                        ended = True
                    else:
                        output.extend(chunk)
                        require(len(output) <= limit, "native_validation_output_bound")
                exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if ended and exited is not None:
                    require(exited.si_code == os.CLD_EXITED, "native_validation_process_signal")
                    answer = exited.si_status, bytes(output)
                    break
    except BaseException as error:
        failure = error
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError as error:
                if not isinstance(error, ProcessLookupError) and failure is None:
                    failure = AssertionError("native_validation_process_cleanup")
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if failure is None:
                    failure = AssertionError("native_validation_process_cleanup")
            process.stdout.close()
    if failure is not None:
        raise failure
    return answer


def observe(raw, component, returncode):
    """Reuse the consumer's closed shape and correlation rules, not policy rules."""
    from zpm import installation_admission as admission

    result = admission._decode(raw, OUTPUT_LIMIT)
    require(type(result) is dict and type(result.get("valid")) is bool
            and returncode == (0 if result["valid"] else 2),
            "native_validation_exit_receipt_mismatch")
    envelope = {name: copy.deepcopy(component[name])
                for name in ("source_revision", "contract_digest", "input_digests")}
    envelope["result"] = result
    status, descriptor = admission._observe(admission.encode(envelope), "openjules", component)
    require(status["status"] in {"valid", "invalid"}, "native_validation_receipt_unvalidated")
    return result, descriptor, envelope


def protected_snapshot(bundle):
    paths = [bundle / "manifest.json", bundle / "manage.py"]
    for directory in ("config", "secrets", "state"):
        paths.append(bundle / directory)
        paths.extend((bundle / directory).rglob("*"))
    paths.extend(bundle.glob("environments/*/bootstrap-complete.json"))
    paths.append(bundle / "environments/openjules/openjules-managed-installation.json")
    result, total = {}, 0
    for path in sorted(paths):
        info = path.lstat()
        relative = str(path.relative_to(bundle))
        require(not stat.S_ISLNK(info.st_mode), "native_validation_protected_path")
        if stat.S_ISDIR(info.st_mode):
            result[relative] = ["directory", stat.S_IMODE(info.st_mode)]
        else:
            raw = bounded_file(path, 8_000_000)
            total += len(raw)
            require(total <= 16_000_000, "native_validation_protected_bound")
            result[relative] = [digest(raw), stat.S_IMODE(info.st_mode), info.st_size]
    return result


def qualify(bundle, checkout, work, *, profile_case):
    """Return only safe qualification evidence; no installation flag is changed."""
    from zpm import installation_admission as admission, installation_context
    from zpm.deployment import verify

    require(profile_case in {"root", "model", "children"}, "native_validation_case")
    bundle, checkout, work = Path(bundle), Path(checkout), Path(work)
    verify(bundle)
    manifest = json.loads(bounded_file(bundle / "manifest.json", 2_000_000))
    data = manifest["configuration"]
    require(data["schema_version"] == 1 and data["sources"]["openjules"] == {
        "checkout": str(checkout), "revision": REVISION}, "native_validation_selected_source")
    verify_source(checkout)
    if "dependency_plan" in data:
        from zpm import dependencies
        dependencies.check_completed(bundle, data, environment())
    else:
        require(installation_context.completed(bundle, REVISION), "native_validation_bootstrap_required")
    installation_context.validate_selector(bundle)
    before = protected_snapshot(bundle)
    work.mkdir(mode=0o700)
    interpreter = bundle / "environments/openjules/bin/python"
    # -B prevents writes; a fresh cache prefix also avoids reusing old .pyc files.
    cache_prefix = work / "unused-bytecode"
    code, raw = run_bounded(native_command(interpreter, checkout, cache_prefix, probe=True),
                            checkout, limit=4096)
    require(code == 0 and json.loads(raw) == {
        "module": str(checkout / "src/worker/installation_validation.py"),
        "package": str(checkout / "src/__init__.py"), "package_paths": [str(checkout / "src")],
        "worker_package": str(checkout / "src/worker/__init__.py"), "worker_paths": [str(checkout / "src/worker")],
        "contract": str(checkout / ARTIFACT), "executable": str(interpreter),
        "prefix": str(interpreter.parent.parent)}, "native_validation_import_origin")
    config_path = bundle / "config/openjules.json"
    config = bounded_file(config_path, 1_000_000)
    require(digest(config) == manifest["files"]["config/openjules.json"],
            "native_validation_sealed_input")
    scopes = encode({"schema_version": 1, "scopes": [{
        "deployment_id": data["deployment_id"], "repository": data["binding"]["repository"]}]})
    private_file(work / "scopes.json", scopes)
    component = {"source_revision": REVISION, "validation_contract": CONTRACT,
                 "contract_digest": CONTRACT_DIGEST,
                 "input_digests": {"configuration": digest(config), "scope_declarations": digest(scopes)}}

    def invoke(name, input_path, scope_path, selected_component, *,
               expected_contract=CONTRACT, expected_digest=CONTRACT_DIGEST):
        arguments = ["--config", str(input_path),
            "--scope-declarations", str(scope_path), "--expected-contract", expected_contract,
            "--expected-contract-digest", expected_digest]
        code, output = run_bounded(native_command(interpreter, checkout, cache_prefix, arguments), checkout)
        private_file(work / (name + "-receipt.json"), output)
        return observe(output, selected_component, code)

    native, descriptor, envelope = invoke("original", config_path, work / "scopes.json", component)
    require(native["valid"] is True, "native_validation_generated_input_refused")
    require(descriptor["execution_destination_identity"] == int("execution_destination" in json.loads(config)),
            "native_validation_destination_projection")
    expected_features = {"model_broker": None if profile_case == "root" else 1,
        "children": "bounded-children-v1" if profile_case == "children" else None,
        "child_outcome": None, "child_session": None, "public_discussion": None}
    require(len(descriptor["profiles"]) == 1, "native_validation_profile_count")
    profile = descriptor["profiles"][0]
    require({key: profile[key] for key in ("deployment_id", "repository", "profile", "base_commit", "image", "features")} == {
        "deployment_id": data["deployment_id"], "repository": data["binding"]["repository"],
        "profile": data["binding"]["profile"], "base_commit": data["source"]["revision"],
        "image": data["image"], "features": expected_features}, "native_validation_generated_projection")
    repeated, _, _ = invoke("repeat", config_path, work / "scopes.json", component)
    require(repeated == native, "native_validation_repeat_changed")

    negative_codes = []
    for name, options, expected_error in (
        ("contract-id-refusal", {"expected_contract": "openjules-installation-v2"},
         {"code": "contract_id_mismatch", "path": "/expected_contract"}),
        ("contract-digest-refusal", {"expected_digest": "0" * 64},
         {"code": "contract_digest_mismatch", "path": "/expected_contract_digest"}),
    ):
        result, _, _ = invoke(name, config_path, work / "scopes.json", component, **options)
        require(result["valid"] is False and result["errors"] == [expected_error],
                "native_validation_contract_refusal_missing")
        negative_codes.append(expected_error["code"])
    for name, input_bytes, scope_bytes, expected_error in (
        ("schema-refusal", encode(json.loads(config) | {"schema_version": 2}), scopes,
         {"code": "configuration_schema_unsupported", "path": "/configuration/schema_version"}),
        ("scope-refusal", config, encode({"schema_version": 1, "scopes": [{
            "deployment_id": data["deployment_id"], "repository": "github:github.com/native-validation/foreign"}]}),
         {"code": "scope_declarations_missing_profile", "path": "/scope_declarations/scopes"}),
    ):
        input_copy, scope_copy = work / (name + "-input.json"), work / (name + "-scope.json")
        private_file(input_copy, input_bytes)
        private_file(scope_copy, scope_bytes)
        selected = component | {"input_digests": {"configuration": digest(input_bytes), "scope_declarations": digest(scope_bytes)}}
        result, _, _ = invoke(name, input_copy, scope_copy, selected)
        require(result["valid"] is False and result["errors"] == [expected_error],
                "native_validation_negative_refusal_missing")
        negative_codes.append(expected_error["code"])

    # A real producer result must still be refused if its retained correlation
    # envelope or selected input changes. No synthetic authority result is used.
    refusals = []
    for label, field in (("source", "source_revision"), ("contract", "contract_digest")):
        changed = copy.deepcopy(envelope)
        changed[field] = "0" * len(changed[field])
        state, _ = admission._observe(admission.encode(changed), "openjules", component)
        require(state["status"] == "unvalidated" and state["code"] == label + "_mismatch",
                "native_validation_consumer_binding_refusal_missing")
        refusals.append(label)
    for label in ("configuration", "scope_declarations"):
        changed = copy.deepcopy(component)
        original_bytes = config if label == "configuration" else scopes
        changed["input_digests"][label] = digest(original_bytes + b" ")
        state, _ = admission._observe(admission.encode(envelope), "openjules", changed)
        require(state["status"] == "unvalidated" and state["code"] == "input_mismatch",
                "native_validation_consumer_binding_refusal_missing")
        refusals.append(label)
    tampered = copy.deepcopy(envelope)
    tampered["result"]["configuration_sha256"] = "0" * 64
    state, _ = admission._observe(admission.encode(tampered), "openjules", component)
    require(state["status"] == "unvalidated" and state["code"] == "input_mismatch",
            "native_validation_native_binding_refusal_missing")
    refusals.append("native_receipt")
    private_file(work / "private-binding.json", encode(component | {
        "source_tree": TREE, "sealed_manifest_sha256": digest(bounded_file(bundle / "manifest.json", 2_000_000)),
        "original_result": native, "protected_snapshot": before}))
    require(before == protected_snapshot(bundle), "native_validation_changed_installation")
    require(not (work / "unused-bytecode").exists(), "native_validation_wrote_bytecode")
    verify_source(checkout)
    verify(bundle)
    installation_context.validate_selector(bundle)
    return {"schema_version": 1, "evidence": "actual-openjules-native-command",
        "producer_revision": REVISION, "producer_tree": TREE, "contract_digest": CONTRACT_DIGEST,
        "profile_case": profile_case, "valid_generated_configuration": True, "unchanged_repeat": True,
        "native_refusals": negative_codes, "consumer_binding_refusals": refusals,
        "sealed_configuration_and_state_unchanged": True,
        "polyphony_native_validation": "not_run", "paired_native_compatibility": None,
        "production_preflight_activated": False}
