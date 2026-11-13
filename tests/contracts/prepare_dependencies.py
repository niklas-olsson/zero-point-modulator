"""Acquire the finite wheel inputs for the sealed-bootstrap contract trial.

This is an explicitly online test fixture, separate from installer preparation
and bootstrap. It does not install packages or select deployment policy. The
result is a new local plan for the exact three checked-out source revisions.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import time
import zipfile


TOOL_REQUIREMENTS = ("pip==25.3", "setuptools==80.9.0", "wheel==0.45.1")
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARTIFACTS = 512
MAX_ACQUISITION_STORAGE = 4 * MAX_TOTAL_BYTES
MAX_COMMAND_OUTPUT = 1024 * 1024


def acquisition_environment(home, temporary):
    # Acquisition is explicitly online. Keep the host's approved transport
    # routing, without copying pip configuration or exposing proxy values in
    # reports. The installer's separate offline environment remains unchanged.
    allowed = {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(HOME=str(home), TMPDIR=str(temporary), PIP_CONFIG_FILE=os.devnull,
                       PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")
    return environment


def observed_storage(directory):
    total, count = 0, 0
    for current, subdirectories, files in os.walk(directory, followlinks=False):
        for name in (*subdirectories, *files):
            try:
                info = (Path(current) / name).lstat()
            except FileNotFoundError:
                continue  # pip can remove its own temporary files during a sample.
            count += 1
            require(count <= 200000, "acquisition storage inventory exceeds its entry bound")
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def bounded_acquisition(command, environment, output_path, directory):
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    output_bytes, peak_storage = 0, 0
    started = time.monotonic()
    deadline = started + 600
    try:
        with output_path.open("xb") as output, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                require(time.monotonic() < deadline, "wheel acquisition exceeded its time bound")
                peak_storage = max(peak_storage, observed_storage(directory))
                require(peak_storage <= MAX_ACQUISITION_STORAGE,
                        "wheel acquisition exceeded its storage bound")
                for key, _ in selector.select(0.25):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    output_bytes += len(block)
                    require(output_bytes <= MAX_COMMAND_OUTPUT, "wheel acquisition exceeded its output bound")
                    output.write(block)
            require(process.wait(timeout=5) == 0, "wheel acquisition failed; inspect the private bounded log")
        return {"elapsed_ns": int((time.monotonic() - started) * 1_000_000_000),
                "output_bytes": output_bytes, "peak_observed_regular_file_bytes": peak_storage,
                "storage_sample_interval_seconds": 0.25}
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=5)
        process.stdout.close()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_record(path):
    require(path.is_file() and not path.is_symlink() and path.suffix == ".whl",
            "acquisition returned a non-wheel or linked artifact")
    size = path.stat().st_size
    require(0 < size <= MAX_ARTIFACT_BYTES, "wheel exceeds the trial artifact bound")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl", path.name),
            "wheel filename is not a safe local basename")
    with zipfile.ZipFile(path) as archive:
        metadata = [entry for entry in archive.infolist()
                    if entry.filename.endswith(".dist-info/METADATA") and entry.filename.count("/") == 1]
        require(len(metadata) == 1 and metadata[0].file_size <= 1024 * 1024,
                "wheel must contain one bounded distribution metadata file")
        message = BytesParser().parsebytes(archive.read(metadata[0]))
    names, versions = message.get_all("Name", []), message.get_all("Version", [])
    require(len(names) == len(versions) == 1, "wheel identity is missing or ambiguous")
    name = re.sub(r"[-_.]+", "-", names[0]).lower()
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) and versions[0].strip() == versions[0],
            "wheel metadata identity is invalid")
    return {"filename": path.name, "size": size, "sha256": sha256(path),
            "name": name, "version": versions[0]}


def acquire(checkouts, directory):
    require(not directory.exists(), "acquisition directory must be new")
    directory.mkdir(mode=0o700)
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o700)
    home = directory / "home"
    home.mkdir(mode=0o700)
    temporary = directory / "temporary"
    temporary.mkdir(mode=0o700)
    environment = acquisition_environment(home, temporary)
    revisions = {}
    dependencies = {}
    for name, checkout in checkouts.items():
        require(not subprocess.check_output(
            ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"],
            text=True, timeout=15), "trial source checkout is not clean")
        revisions[name] = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True, timeout=15).strip()
        filename = "pyproject.toml" if name == "zpm" else "requirements.txt"
        dependencies[name] = {filename: sha256(checkout / filename)}

    acquisition_runs = []

    def download(destination, inputs):
        destination.mkdir(mode=0o700)
        command = [sys.executable, "-I", "-m", "pip", "download", "--disable-pip-version-check",
                   "--no-cache-dir", "--only-binary=:all:", "--dest", str(destination), *inputs]
        observation = bounded_acquisition(command, environment, destination / "acquisition.log", directory)
        acquisition_runs.append({"stage": destination.name, **observation})
        paths = sorted(destination.glob("*.whl"))
        require(bool(paths) and len(paths) <= MAX_ARTIFACTS, "acquisition wheel count is invalid")
        require(sum(path.stat().st_size for path in paths) <= MAX_TOTAL_BYTES,
                "acquisition exceeds the artifact byte bound")
        return paths

    tool_paths = download(directory / "tools", list(TOOL_REQUIREMENTS))
    tool_records = [wheel_record(path) for path in tool_paths]
    seeds = [item["filename"] for item in tool_records if item["name"] == "pip"]
    require(len(seeds) == 1, "acquisition did not identify one pip seed")
    records = {}
    environments = {}
    acquired_bytes = sum(path.stat().st_size for path in tool_paths)
    for name, checkout in checkouts.items():
        inputs = [str(path) for path in tool_paths]
        if name != "zpm":
            inputs += ["-r", str(checkout / "requirements.txt")]
        paths = download(directory / name, inputs)
        acquired_bytes += sum(path.stat().st_size for path in paths)
        names = set()
        for path in paths:
            record = wheel_record(path)
            require(record["name"] not in names, "one environment resolved conflicting distributions")
            names.add(record["name"])
            prior = records.get(path.name)
            require(prior is None or prior == record, "same wheel filename resolved to different bytes")
            if prior is None:
                require(len(records) < MAX_ARTIFACTS and
                        sum(item["size"] for item in records.values()) + record["size"] <= MAX_TOTAL_BYTES,
                        "combined wheel plan exceeds its artifact bounds")
                with path.open("rb") as source, (artifacts / path.name).open("xb") as target:
                    shutil.copyfileobj(source, target)
                os.chmod(artifacts / path.name, 0o600)
                records[path.name] = record
        environments[name] = {"source_revision": revisions[name], "dependency_files": dependencies[name],
                              "wheels": [path.name for path in paths]}

    # Reuse the installer's interpreter identity rather than inventing a second
    # definition of supported interpreters in the test acquisition helper.
    sys.path.insert(0, str(checkouts["zpm"]))
    from zpm.dependencies import interpreter_identity

    plan = {"schema_version": 1, "interpreter": interpreter_identity(),
            "seed": {"method": "sealed-pip-wheel", "filename": seeds[0]},
            "artifacts": [records[name] for name in sorted(records)], "environments": environments}
    manifest = directory / "dependencies.json"
    manifest.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)
    reference = {"manifest": str(manifest), "sha256": sha256(manifest), "artifacts": str(artifacts)}
    (directory / "reference.json").write_text(json.dumps(reference, sort_keys=True) + "\n", encoding="utf-8")
    report = {"passed": True, "schema_version": 1, "source_revisions": revisions, "interpreter": plan["interpreter"],
              "manifest_sha256": reference["sha256"], "seed": plan["seed"],
              "artifact_count": len(records), "artifact_bytes": sum(item["size"] for item in records.values()),
              "acquired_wheel_bytes_including_repeated_local_copies": acquired_bytes,
              "byte_scope": "wheel files only; excludes acquisition metadata and later installed storage",
              "acquisition_runs": acquisition_runs,
              "acquisition_limits": {"seconds_per_command": 600, "output_bytes_per_command": MAX_COMMAND_OUTPUT,
                                     "observed_storage_bytes": MAX_ACQUISITION_STORAGE,
                                     "artifact_bytes": MAX_TOTAL_BYTES, "artifact_count": MAX_ARTIFACTS},
              "mode": "explicit online acquisition; no installation or runtime qualification"}
    (directory / "acquisition-report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openjules-checkout", type=Path, required=True)
    parser.add_argument("--polyphony-checkout", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    checkouts = {"zpm": Path(__file__).resolve().parents[2],
                 "openjules": args.openjules_checkout.resolve(strict=True),
                 "polyphony": args.polyphony_checkout.resolve(strict=True)}
    directory = args.work_dir.absolute()
    try:
        print(json.dumps(acquire(checkouts, directory), sort_keys=True, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        report = {"passed": False, "schema_version": 1, "error_type": type(exc).__name__,
                  "error": "explicit wheel acquisition failed; inspect its bounded private logs",
                  "mode": "online acquisition only; no installation or runtime qualification"}
        if directory.is_dir():
            (directory / "acquisition-report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
