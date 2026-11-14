#!/usr/bin/env python3
"""Inert pretrial admission for ZPM #63. No acquisition or native execution.

The CLI reads only the frozen source documents and writes one private report.
Archive helpers are exercised with synthetic fixtures; they do not extract,
install, import or execute archive contents. An active runner is a separate
reviewed implementation prerequisite, not an option hidden behind this command.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import urlsplit
import zipfile

from tests.contracts import check_musl_preparation as musl
from zpm.dependencies import wheel_identity

ROOT = Path(__file__).resolve().parent / "native-alpine"
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+@!-]*\Z")
SOURCES = {
    "zpm": "c5fb355f35b73041925e99590d6a18e71038548a",
    "openjules": "0dd5bf4e42b3677c3f2f564655bc87a97c4bf973",
    "polyphony": "6480e36355bf0d5de4e25a560d40e665fd76038c",
}
METADATA = {
    "inputs-v1.json": (245074, "e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f"),
    "oci-index.json": (10301, "c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89"),
    "oci-manifest.json": (1735, "a61cbd76a882645de7f91423329fe43a67eed60d7da24f9e5331414bb3618b20"),
    "oci-config.json": (5432, "4076016aea5420e5c87eb52280edfc527ace1ce31dc1c9288b1cc4ef9a0e8905"),
    "native-extensions-v1.json": (16221, "6e8d0bc3e1257625b803a55c6f12282b5275d1beb96ee6d7667ada18779baef0"),
}
SOURCE_FACTS = {
    "zpm": {"repository": "niklas-olsson/zero-point-modulator", "revision": SOURCES["zpm"],
            "tree": "7326b297579ef0d58e88c688c81234da0ba2ab9e", "tracked_bytes": 2583291, "entries": 99},
    "openjules": {"repository": "niklas-olsson/OpenJules", "revision": SOURCES["openjules"],
                  "tree": "e92f616a633766341fab8ef896b5848d7e0fa22d", "tracked_bytes": 2827826, "entries": 239},
    "polyphony": {"repository": "niklas-olsson/keystone-polyphony", "revision": SOURCES["polyphony"],
                  "tree": "3e8841f995df388b5d9c108ead1b677355c8ca07", "tracked_bytes": 2533994, "entries": 288},
}
SOURCE_EXCEPTIONS = {
    "symlinks": [{"service": "polyphony", "path": "kp", "target": "polyphony", "blob": "cf280f1b00866e53feb5ffe364079418ec87bf2a"}],
    "uninitialized_gitlinks": [
        {"service": "polyphony", "path": "third_party/emsdk", "revision": "bafd64c26bdaf10bd829163d1575b50b759a72d8"},
        {"service": "polyphony", "path": "third_party/llama-cpp-wasm", "revision": "c1fa952e9d6be14bbc3dfe2668aaa72ac83531ba"},
    ],
}
NATIVE_PINS = [
    {"path": "lib/ld-musl-x86_64.so.1", "size": 670312, "sha256": "095b047c631447e4fa50401fa7ffce01f678587040d65ba18c574981dc630de5"},
    {"path": "usr/local/bin/python3.12", "size": 14000, "sha256": "8a225e021fcee4c6d493a243da73e8604feb2df88576e3910254c248ea38bc95"},
    {"path": "usr/local/lib/libpython3.12.so.1.0", "size": 7236608, "sha256": "0774989aecaf2f13b075e9785185b05c74bb69e258488c3fff5d721b7e9f31ee"},
]
PREREQUISITES = ["independent-source-and-input-review", "reviewed-active-runner-and-workflow",
                 "exact-plan-hash-admission", "supported-native-docker-runner"]
CAPS = {
    "wall_seconds": 1800, "cleanup_reserve_seconds": 90, "acquisition_seconds": 600,
    "acquisition_bytes": 512 * 1024**2, "acquisition_concurrency": 4,
    "request_metadata_reserved_bytes": 65536, "metadata_response_bytes": 1024**2,
    "acquisition_requests": 180, "artifact_archive_members": 100000,
    "single_archive_decoded_bytes": 512 * 1024**2,
    "host_rss_bytes": 1024**3, "minimum_host_available_bytes": 4 * 1024**3,
    "owned_storage_bytes": 4 * 1024**3, "source_tracked_bytes": 128 * 1024**2,
    "running_containers": 1, "container_memory_bytes": 2 * 1024**3,
    "container_memory_and_swap_bytes": 2 * 1024**3, "container_pids": 128,
    "container_cpus": 2, "apk_transaction_seconds": 300,
    "initial_bootstrap_seconds": 900, "repeat_bootstrap_seconds": 60,
    "probe_seconds": 15, "all_probes_seconds": 120,
    "command_output_bytes": 1024**2, "aggregate_log_bytes": 16 * 1024**2,
    "report_bytes": 256 * 1024, "sample_interval_ms": 250,
}
AUTHORITY = {
    "assembly": {"uid": 0, "network": "none", "cap_drop": ["ALL"],
                 "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
                 "no_new_privileges": True, "read_only_root": False},
    "qualification": {"uid": "invoking-nonzero-uid", "gid": "invoking-nonzero-gid",
                      "network": "none", "cap_drop": ["ALL"], "cap_add": [],
                      "no_new_privileges": True, "read_only_root": True},
    "mounts": {"inputs": "read-only", "sources": "read-only", "harness": "read-only", "owned_work": "read-write"},
    "host_control_socket_mounted": False, "host_namespace_shared": False,
    "host_devices_added": False, "services_started": False,
    "managed_task_started": False, "model_or_provider_calls": False,
    "loopback_remains_available": True,
}
PROBES = ["zpm-native-imports", "openjules-native-imports", "polyphony-native-imports",
          "zpm-cli-help", "openjules-managed-service-help", "polyphony-coordinator-help",
          "zpm-binding-validation", "openjules-managed-context"]
NATIVE_CHECKS = ["artifact_bytes", "apk_trust", "base_image_identity", "apk_transaction",
                 "installed_apk_inventory", "interpreter_identity", "production_prepare_verify",
                 "sealed_bootstrap", "native_imports_and_interfaces", "repeat_bytes", "owned_cleanup"]


class Refused(ValueError):
    """Fixed diagnostic codes only; never incorporate candidate output or paths."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def exact(value, expected, code):
    # Canonical JSON comparison keeps Boolean and integer identities distinct.
    require(encoded(value) == encoded(expected), code)


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def object_fields(value, fields, code):
    require(type(value) is dict and set(value) == set(fields.split()), code)


def read_bounded(path, limit, expected=None):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, "INPUT_FILE_BOUND")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size and all(getattr(before, name) == getattr(after, name)
                for name in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")), "INPUT_CHANGED")
        if expected is not None:
            require(type(expected) is str and HEX.fullmatch(expected) and digest(raw) == expected, "INPUT_HASH")
        return raw
    except OSError:
        raise Refused("INPUT_FILE_UNAVAILABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result
    def invalid(_):
        raise Refused("JSON_NUMBER")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=invalid, parse_constant=invalid)
        pending, visited = [(result, 0)], 0
        while pending:
            value, depth = pending.pop()
            visited += 1
            require(depth <= 32 and visited <= 100000, "JSON_STRUCTURE_BOUND")
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            pending.extend((child, depth + 1) for child in children)
        return result
    except (UnicodeError, ValueError, RecursionError):
        raise Refused("JSON_INVALID") from None


def safe_member(name):
    require(type(name) is str and bool(name) and "\\" not in name and "\0" not in name, "ARCHIVE_PATH")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == name, "ARCHIVE_PATH")
    return path


def verify_layer_bytes(raw, row):
    """Reuse the native musl fixture's bounded, non-extracting archive checks."""
    require(len(raw) == row["compressed_bytes"] and "sha256:" + digest(raw) == row["digest"], "LAYER_BYTES")
    require(integer(row["decoded_bytes"], 1, CAPS["single_archive_decoded_bytes"]), "LAYER_DECODE_BOUND")
    try:
        parts = musl.gzip_streams(raw, 1, decoded_limit=row["decoded_bytes"])
        require(len(parts[0][1]) == row["decoded_bytes"] and "sha256:" + digest(parts[0][1]) == row["diff_id"], "LAYER_DIFF_ID")
        members = musl.tar_entries(parts[0][1])
        require(len(members) == row["archive_members"], "LAYER_MEMBERS")
        # The shared APK parser admits rooted links lexically inside the guest.
        # No entry is ever extracted on the host by this pretrial helper.
        for name in members:
            safe_member(name)
        return {"compressed_bytes": len(raw), "decoded_bytes": len(parts[0][1]), "members": len(members)}
    except (musl.Refused, KeyError, TypeError, ValueError):
        raise Refused("LAYER_ARCHIVE_REFUSED") from None


def verify_wheel_bytes(raw, row, native_members):
    """Data-only wheel identity checks; production admission still owns install."""
    require(len(raw) == row["size"] and digest(raw) == row["sha256"], "WHEEL_BYTES")
    try:
        exact(wheel_identity(raw), {"name": row["name"], "version": row["version"]}, "WHEEL_IDENTITY")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = archive.infolist()
            total = sum(member.file_size for member in files)
            require(total == row["uncompressed_member_bytes"] and total <= CAPS["single_archive_decoded_bytes"], "WHEEL_DECODE_BOUND")
            for member in files:
                safe_member(member.filename.rstrip("/"))
            metadata = next(member for member in files if member.filename.count("/") == 1 and member.filename.endswith(".dist-info/METADATA"))
            require(digest(archive.read(metadata)) == row["core_metadata_sha256"], "WHEEL_METADATA")
            require(len(native_members) == row["native_library_count"], "WHEEL_NATIVE_INVENTORY")
            for member in native_members:
                info = archive.getinfo(member["member"])
                require(info.file_size == member["size"] and digest(archive.read(info)) == member["sha256"], "WHEEL_NATIVE_BYTES")
        return {"name": row["name"], "version": row["version"], "uncompressed_bytes": total}
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError, zipfile.BadZipFile):
        raise Refused("WHEEL_ARCHIVE_REFUSED") from None


def _artifact_url(value, host, prefix):
    require(type(value) is str, "ARTIFACT_ORIGIN")
    parts = urlsplit(value)
    require(parts.scheme == "https" and parts.netloc == host and parts.path.startswith(prefix)
            and not parts.query and not parts.fragment and not parts.username and not parts.password,
            "ARTIFACT_ORIGIN")
    require(".." not in PurePosixPath(parts.path).parts and "%" not in parts.path and "\\" not in parts.path, "ARTIFACT_ORIGIN")


def validate_documents(plan, documents):
    object_fields(plan, "schema mode candidate production_sources source_exceptions metadata native_file_pins caps authority active_trial_implemented expected_native_checks external_prerequisites probe_ids", "PLAN_FIELDS")
    exact([plan["schema"], plan["mode"], plan["candidate"], plan["active_trial_implemented"]],
          ["zpm-native-alpine-pretrial/v1", "source-only", "alpine3.24.4-official-cpython3.12.16-linux-amd64", False], "PLAN_MODE")
    exact(plan["caps"], CAPS, "PLAN_CAPS")
    exact(plan["authority"], AUTHORITY, "PLAN_AUTHORITY")
    exact(plan["probe_ids"], PROBES, "PLAN_PROBES")
    exact(plan["expected_native_checks"], NATIVE_CHECKS, "PLAN_CHECKS")
    exact(plan["production_sources"], SOURCE_FACTS, "SOURCE_FACTS")
    exact(plan["source_exceptions"], SOURCE_EXCEPTIONS, "SOURCE_LAYOUT")
    exact(plan["native_file_pins"], NATIVE_PINS, "NATIVE_FILE_PINS")
    exact(plan["external_prerequisites"], PREREQUISITES, "PLAN_PREREQUISITES")
    exact(plan["metadata"], {name: {"size": size, "sha256": sha} for name, (size, sha) in METADATA.items()}, "PLAN_METADATA")
    require(set(plan["production_sources"]) == set(SOURCES), "SOURCE_SET")
    for service, source in plan["production_sources"].items():
        object_fields(source, "repository revision tree tracked_bytes entries", "SOURCE_FIELDS")
        require(source["revision"] == SOURCES[service] and COMMIT.fullmatch(source["tree"])
                and integer(source["tracked_bytes"], 1, CAPS["source_tracked_bytes"])
                and integer(source["entries"], 1, 10000), "SOURCE_IDENTITY")
    require(sum(source["tracked_bytes"] for source in plan["production_sources"].values()) <= CAPS["source_tracked_bytes"], "SOURCE_SIZE")
    recipe = documents["inputs-v1.json"]
    require(recipe["schema"] == "zpm-native-sealed-candidate-inputs/v1" and recipe["source_revisions"] == SOURCES, "RECIPE_IDENTITY")
    exact([recipe["runtime_execution"], recipe["candidate_package_scripts_executed"], recipe["native_imports_executed"]], [False, False, False], "RECIPE_EVIDENCE_LEVEL")
    exact(recipe["interpreter"]["observed_interpreter_identity"], None, "INTERPRETER_NOT_OBSERVED")
    exact([recipe["interpreter"]["path"], recipe["interpreter"]["expected_implementation"], recipe["interpreter"]["expected_version"]],
          ["/usr/local/bin/python3.12", "cpython", "3.12.16"], "INTERPRETER_SELECTION")
    index, manifest, config = (documents[name] for name in ("oci-index.json", "oci-manifest.json", "oci-config.json"))
    exact(recipe["oci"]["index"], index, "OCI_INDEX")
    candidates = [row for row in index["manifests"] if row["platform"] == {"architecture": "amd64", "os": "linux"}]
    require(len(candidates) == 1 and candidates[0]["digest"] == "sha256:" + METADATA["oci-manifest.json"][1], "OCI_AMD64")
    require(manifest["config"]["digest"] == "sha256:" + METADATA["oci-config.json"][1]
            and manifest["config"]["size"] == METADATA["oci-config.json"][0], "OCI_CONFIG")
    require(config["os"] == "linux" and config["architecture"] == "amd64"
            and config["config"].get("Entrypoint") in (None, []), "OCI_PLATFORM_OR_ENTRYPOINT")
    layers = recipe["oci"]["layers"]
    require(len(layers) == len(manifest["layers"]) == 4, "OCI_LAYER_COUNT")
    exact([row["diff_id"] for row in layers], config["rootfs"]["diff_ids"], "OCI_DIFF_IDS")
    for row, original in zip(layers, manifest["layers"]):
        require(row["digest"] == original["digest"] and row["compressed_bytes"] == original["size"]
                and integer(row["decoded_bytes"], 1, CAPS["single_archive_decoded_bytes"])
                and integer(row["archive_members"], 1, CAPS["artifact_archive_members"]), "OCI_LAYER_IDENTITY")
    packages, wheels = recipe["apk_packages"], recipe["wheels"]
    require(len(packages) == 63 and len(wheels) == 106, "ARTIFACT_COUNT")
    require(len({row["name"] for row in packages}) == 63 and len({row["filename"] for row in wheels}) == 106, "ARTIFACT_DUPLICATE")
    for row in packages + wheels:
        require(NAME.fullmatch(row["filename"]) and integer(row["size"], 1, CAPS["acquisition_bytes"])
                and HEX.fullmatch(row["sha256"]), "ARTIFACT_IDENTITY")
    for row in packages:
        require(row["filename"] == f'{row["name"]}-{row["version"]}.apk'
                and row["architecture"] in {"x86_64", "noarch"}, "APK_IDENTITY")
        _artifact_url(row["url"], "dl-cdn.alpinelinux.org", "/alpine/v3.24/")
        require(row["url"].split("/")[-1] == row["filename"] and "/x86_64/" in row["url"]
                and recipe["oci"]["trusted_keys"].get(row["signature_key"]) == row["signature_key_sha256"], "APK_TRUST_BINDING")
    for row in wheels:
        _artifact_url(row["url"], "files.pythonhosted.org", "/packages/")
        require(row["url"].split("/")[-1] == row["filename"] and row["filename"].endswith(".whl"), "WHEEL_ORIGIN")
    binary_bytes = sum(row["compressed_bytes"] for row in layers) + sum(row["size"] for row in packages + wheels)
    exact([binary_bytes, recipe["finite_artifact_bytes"], recipe["finite_artifact_count"]], [209309575, 209309575, 173], "ARTIFACT_TOTAL")
    reservation = binary_bytes + CAPS["acquisition_requests"] * CAPS["request_metadata_reserved_bytes"] + CAPS["metadata_response_bytes"]
    require(reservation <= CAPS["acquisition_bytes"], "TRANSFER_BOUND")
    inventory = {row["filename"]: row for row in wheels}
    used = set()
    exact(sorted(recipe["environments"]), sorted(SOURCES), "ENVIRONMENT_SET")
    for service, environment in recipe["environments"].items():
        require(environment["source_revision"] == SOURCES[service]
                and len(environment["wheels"]) == len(set(environment["wheels"]))
                and set(environment["wheels"]) <= set(inventory)
                and recipe["seed_filename"] in environment["wheels"], "ENVIRONMENT_WHEELS")
        used.update(environment["wheels"])
    exact(used == set(inventory), True, "UNUSED_WHEEL")
    exact(recipe["environments"]["zpm"]["wheels"], ["pip-25.3-py3-none-any.whl", "setuptools-80.9.0-py3-none-any.whl", "wheel-0.45.1-py3-none-any.whl"], "SEALED_TOOLSET")
    base = {row["name"]: row for row in recipe["oci"]["base_installed_packages"]}
    require(len(base) == 38 and base[".python-rundeps"]["version"] == "20261105.193119"
            and base[".python-rundeps"]["control_checksum"] == "Q1p4YXIaFk/uirdRB47MhRqHJriv8=", "BASE_APK_INVENTORY")
    expected = base | {row["name"]: row for row in packages}
    require(len(expected) == 64, "EXPECTED_APK_INVENTORY")
    catalog = documents["native-extensions-v1.json"]
    object_fields(catalog, "schema scope members", "EXTENSION_CATALOG_FIELDS")
    require(catalog["schema"] == "zpm-native-alpine-extension-inputs/v1", "EXTENSION_CATALOG_VERSION")
    extensions = catalog["members"]
    require(len(extensions) == 53 and len({(row["filename"], row["member"]) for row in extensions}) == 53, "EXTENSION_INVENTORY")
    for row in extensions:
        safe_member(row["member"])
        require(row["filename"] in inventory and HEX.fullmatch(row["sha256"])
                and integer(row["size"], 1, CAPS["single_archive_decoded_bytes"]), "EXTENSION_IDENTITY")
        require(row["import_module"] is None or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", row["import_module"]), "EXTENSION_MODULE")
    for row in wheels:
        require(sum(member["filename"] == row["filename"] for member in extensions) == row["native_library_count"], "EXTENSION_CLOSURE")
    require(sum(row["import_module"] is not None for row in extensions) == 42, "EXTENSION_IMPORT_COUNT")
    return {"binary_artifacts": 173, "binary_bytes": binary_bytes, "transfer_reservation_bytes": reservation,
            "oci_layers": 4, "apk_files": 63, "expected_installed_apk_records": 64,
            "unique_wheels": 106, "native_shared_objects": 53, "fixed_extension_imports": 42,
            "environment_wheels": {name: len(row["wheels"]) for name, row in recipe["environments"].items()}}


def load_bundle(root, plan_sha256):
    require(type(plan_sha256) is str and HEX.fullmatch(plan_sha256), "EXACT_PLAN_HASH_REQUIRED")
    plan_raw = read_bounded(root / "plan-v1.json", 32 * 1024, plan_sha256)
    plan = decode(plan_raw)
    documents = {}
    for name, (size, sha) in METADATA.items():
        documents[name] = decode(read_bounded(root / name, size, sha))
    return validate_documents(plan, documents)


def new_report(plan_sha256, source_revision=None):
    return {"schema": "zpm-native-alpine-pretrial-report/v1", "mode": "source-only",
            "status": "PRETRIAL_REFUSED", "plan_sha256": plan_sha256 if type(plan_sha256) is str and HEX.fullmatch(plan_sha256) else None,
            "harness_source_revision": source_revision if type(source_revision) is str and COMMIT.fullmatch(source_revision) else None,
            "production_sources": dict(SOURCES), "recipe_sha256": METADATA["inputs-v1.json"][1],
            "native_checks": {name: "NOT_RUN" for name in NATIVE_CHECKS}, "input_summary": None,
            "first_failure": None, "artifact_body_bytes_received": 0, "candidate_processes_started": 0,
            "native_execution_qualified": False, "active_trial_implemented": False,
            "caps": dict(CAPS), "truncated": False, "truncation_reason": None}


def fail(report, code):
    if report["first_failure"] is None:
        report["first_failure"] = code
    report["status"] = "PRETRIAL_REFUSED"


def report_bytes(report):
    result = encoded(report) + b"\n"
    if len(result) > CAPS["report_bytes"]:
        # The minimal failure preserves original identities and the explicit
        # unexecuted verdict. Never drop native failures to retain a success.
        minimal = new_report(report.get("plan_sha256"), report.get("harness_source_revision"))
        failure = report.get("first_failure")
        minimal.update(first_failure=failure if type(failure) is str and re.fullmatch(r"[A-Z_]{1,64}", failure) else "REPORT_BOUND",
                       truncated=True, truncation_reason="REPORT_BOUND")
        result = encoded(minimal) + b"\n"
    require(len(result) <= CAPS["report_bytes"], "MINIMAL_REPORT_BOUND")
    return result


def write_report(path, raw):
    path = path.absolute()
    require(path.parent.resolve() == path.parent and path.parent.is_dir(), "REPORT_PARENT")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError:
        raise Refused("REPORT_CREATE_OR_WRITE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=ROOT)
    parser.add_argument("--plan-sha256", required=True, help="exact source-plan bytes to check; this does not assert independent approval")
    parser.add_argument("--harness-source-revision")
    parser.add_argument("--report", type=Path, required=True, help="new private report file; existing evidence is never overwritten")
    args = parser.parse_args(argv)
    report = new_report(args.plan_sha256, args.harness_source_revision)
    try:
        report["input_summary"] = load_bundle(args.inputs, args.plan_sha256)
        report["status"] = "PRETRIAL_SOURCE_PASS"
    except Refused as error:
        fail(report, str(error))
    except (OSError, KeyError, TypeError, ValueError):
        fail(report, "INPUT_VALIDATION_FAILED")
    except KeyboardInterrupt:
        fail(report, "SOURCE_CHECK_INTERRUPTED")
    try:
        raw = report_bytes(report)
        write_report(args.report, raw)
    except Refused:
        print("pretrial report could not be retained; original evidence was not replaced", file=sys.stderr)
        return 2
    status = decode(raw)["status"]
    print(status)
    return 0 if status == "PRETRIAL_SOURCE_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
