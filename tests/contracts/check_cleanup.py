"""Owned cleanup phase of the existing installed child trial.

The real installed OpenJules CLI performs deletion. This harness only observes
the original resources, read-only journals and retained archive, and temporarily
reopens the same installed canonical authority while the bundle is stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

from check_authority_admission import docker_inventory, stop_owned
from check_isolation_stack import read_journal
from check_stack import eventually, require


SPEC_FIELDS = {"schema_version", "deployment_id", "repository", "slot_id", "generation", "launch_key", "profile"}
VOLUME_FIELDS = ("Name", "CreatedAt", "Driver", "Scope", "Labels", "Options")
MAX_ARCHIVE_BYTES = 16777216  # The installed controlled profile's unchanged bound.
MAX_PROGRESS_BYTES = 65536  # One root and two child lineages in this controlled scene.


def journal_paths(bundle, config):
    root = Path(config["state_directory"])
    paths = {"controller": bundle / "state/zpm.sqlite3", "lifecycle": Path(config["lifecycle_database"]),
             "runtime": root / "runtime/journal.sqlite3", "dispatcher": root / "dispatcher/dispatch.sqlite3",
             "children": root / "children/children.sqlite3", "models": root / "models/inference.sqlite3"}
    require(len(set(paths.values())) == len(paths) and all(
        path.is_absolute() and bundle.resolve() in path.resolve().parents for path in paths.values()),
        "cleanup journal paths leave or alias the retained installation")
    return paths


def retained_journals(paths, worker):
    """Digest bounded committed logical rows; never export private row values.

    Only this worker's runtime cleanup progress may differ. All other fields,
    rows and tables, including the non-target worker's history, remain covered.
    """
    result = {}
    for name, path in paths.items():
        with read_journal(path) as database:
            tables = [row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            require(0 < len(tables) <= 32, "cleanup journal table inventory changed")
            contents, selected = {}, 0
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                cursor = database.execute("SELECT * FROM " + quoted + " ORDER BY rowid")
                columns = [item[0] for item in cursor.description]
                rows = [list(row) for row in cursor.fetchmany(129)]
                require(len(rows) <= 128, "cleanup journal exceeds the controlled trial bound")
                if name == "runtime" and table == "workers":
                    require({"worker_id", "cleanup"} <= set(columns), "cleanup runtime journal lacks its qualified schema")
                    for row in rows:
                        if row[columns.index("worker_id")] == worker:
                            selected += 1
                            row[columns.index("cleanup")] = None
                contents[table] = [columns, rows]
            require(name != "runtime" or selected == 1, "cleanup original worker history is missing or duplicated")
            encoded = json.dumps(contents, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            require(len(encoded) <= 8_000_000, "cleanup journal content exceeds the controlled trial bound")
            result[name] = hashlib.sha256(encoded).hexdigest()
    return result


def original_capture(paths, worker, scope, artifacts):
    with read_journal(paths["lifecycle"]) as database:
        rows = database.execute("SELECT spec FROM managed_workers WHERE worker_id=?", (worker,)).fetchall()
    require(len(rows) == 1, "cleanup lacks the original lifecycle identity")
    spec = json.loads(rows[0]["spec"])
    require(set(spec) == SPEC_FIELDS and type(spec["schema_version"]) is int and spec["schema_version"] == 1
            and all(spec[key] == value for key, value in scope.items()), "cleanup original ensure payload changed scope or schema")
    with read_journal(paths["runtime"]) as database:
        rows = database.execute("SELECT * FROM workers WHERE worker_id=?", (worker,)).fetchall()
    require(len(rows) == 1 and rows[0]["spec"] == json.dumps(spec, sort_keys=True, separators=(",", ":"))
            and rows[0]["retired"] == 1 and rows[0]["cleanup"] is None,
            "cleanup original runtime identity is absent, active or already cleaned")
    row = dict(rows[0])
    receipt = json.loads(row["receipt"])
    manifest = json.loads(artifacts["manifest"])
    require(all(receipt.get(key) is True for key in ("termination_confirmed", "artifacts_captured", "launch_fenced"))
            and {key: receipt.get(key) for key in ("patch", "log", "manifest")} == artifacts
            and manifest.get("worker_id") == worker and manifest.get("container_id") == row["container_id"],
            "cleanup local execution receipt differs from canonical capture")
    archive = paths["runtime"].parent / "captures" / (hashlib.sha256(worker.encode()).hexdigest() + ".tar")
    require(receipt.get("capture_path") == str(archive) and receipt.get("capture_sha256") == manifest.get("archive_sha256"),
            "cleanup archive is not the exact retained worker capture")
    expected = {"sha256": manifest.get("archive_sha256"), "bytes": manifest.get("archive_bytes")}
    require(type(expected["bytes"]) is int and archive_identity(archive) == expected,
            "cleanup retained archive differs from its canonical manifest")
    return {"spec": spec, "archive": archive, "archive_identity": expected, "container": row["container_id"],
            "volume_identity": json.loads(row["volume_identity"])}


def archive_identity(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_ARCHIVE_BYTES,
                "cleanup archive is not a bounded regular file")
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: source.read(65536), b""):
            size += len(chunk)
            require(size <= MAX_ARCHIVE_BYTES, "cleanup archive grew beyond its bound")
            digest.update(chunk)
        require(size == info.st_size, "cleanup archive changed during observation")
        return {"sha256": digest.hexdigest(), "bytes": size}


def owned_resources(worker):
    identities = subprocess.check_output(
        ["docker", "ps", "-aq", "--no-trunc", "--filter", "label=openjules.worker=" + worker], text=True, timeout=10).split()
    require(len(identities) == 1, "cleanup retained worker lacks exactly one original container")
    container = json.loads(subprocess.check_output(["docker", "inspect", identities[0]], text=True, timeout=10))[0]
    labels = container["Config"]["Labels"]
    mounts = [item for item in container["Mounts"] if item["Destination"] == "/workspace"]
    require(container["Id"] == identities[0] and container["State"]["Running"] is False
            and labels.get("openjules.worker") == worker and labels.get("openjules.managed") == "1"
            and len(mounts) == 1 and mounts[0].get("Type") == "volume", "cleanup resource ownership or termination changed")
    name = mounts[0]["Name"]
    volume = json.loads(subprocess.check_output(["docker", "volume", "inspect", name], text=True, timeout=10))[0]
    require(volume.get("Name") == name and volume.get("Labels") == labels,
            "cleanup workspace volume is not owned by its original worker")
    return {"container": container["Id"], "volume": name, "labels": labels,
            "volume_identity": {field: volume.get(field) for field in VOLUME_FIELDS}}


def require_retained(paths, worker, witness):
    # History normalization permits only the selected progress column to change.
    # Independently bind that durable value to the proof frozen before deletion.
    with read_journal(paths["runtime"]) as database:
        rows = database.execute("SELECT cleanup FROM workers WHERE worker_id=?", (worker,)).fetchmany(2)
    require(len(rows) == 1, "cleanup original durable progress is missing or duplicated")
    progress = rows[0]["cleanup"]
    require(isinstance(progress, str) and 0 < len(progress.encode()) <= MAX_PROGRESS_BYTES
            and progress == witness["cleanup_progress"],
            "cleanup did not retain the exact completed original proof")
    require(retained_journals(paths, worker) == witness["journals"], "cleanup changed retained journal history")
    require(archive_identity(witness["archive"]) == witness["archive_identity"], "cleanup changed the retained archive")
    require(docker_inventory() == witness["remaining_inventory"], "cleanup removed another resource or recreated a target")
    require(owned_resources(witness["idle_worker"]) == witness["idle_resources"], "cleanup changed the non-target worker resources")


def qualify_owned_cleanup(bundle, source_paths, scope, worker, idle_worker, source_environment,
                          processes, artifacts, verify_canonical, *, canonical_proof):
    from zpm.deployment import _command, _environment, verify

    require("installation" not in processes.running, "cleanup requires the owned installation manager to be reaped")
    verify(bundle)
    configuration = json.loads((bundle / "manifest.json").read_text())["configuration"]
    config = json.loads((bundle / "config/openjules.json").read_text())
    paths = journal_paths(bundle, config)
    witness = original_capture(paths, worker, scope, artifacts)
    # Freeze the independently verified canonical attempt, never a post-cleanup
    # response or the mutable progress row. Canonical JSON also rejects bool/int
    # aliases, extra fields and incomplete or foreign completed receipts.
    witness["cleanup_progress"] = json.dumps({
        "schema_version": 1, "worker_id": worker,
        "spec_sha256": hashlib.sha256(json.dumps(
            witness["spec"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "container_id": witness["container"], "volume_identity": witness["volume_identity"],
        "archive_sha256": witness["archive_identity"]["sha256"], "canonical_proof": canonical_proof,
        "container_removed": True, "volume_removed": True, "completed": True,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False)
    original, idle = owned_resources(worker), owned_resources(idle_worker)
    require(worker != idle_worker and original["container"] == witness["container"]
            and original["volume_identity"] == witness["volume_identity"]
            and original["container"] != idle["container"] and original["volume"] != idle["volume"],
            "cleanup resources do not identify the original execution and distinct idle replacement")
    inventory = docker_inventory()
    require(all(identity in inventory[kind] for kind, identity in (
        ("containers", original["container"]), ("containers", idle["container"]),
        ("volumes", original["volume"]), ("volumes", idle["volume"]))), "cleanup resource inventory is incomplete")
    witness.update(journals=retained_journals(paths, worker), idle_worker=idle_worker, idle_resources=idle,
                   remaining_inventory={"containers": [value for value in inventory["containers"] if value != original["container"]],
                                        "volumes": [value for value in inventory["volumes"] if value != original["volume"]]})
    commands = {name: _command(bundle, name, configuration) for name in ("polyphony", "openjules")}
    with patch.dict(os.environ, source_environment, clear=True):
        environments = {name: _environment(bundle, name, configuration) for name in commands}
    cleanup = commands["openjules"] + ["--cleanup-worker", worker, "--deployment-id", scope["deployment_id"],
                                      "--repository", scope["repository"]]
    expected = {"schema_version": 1, **scope, "worker_id": worker, "state": "cleaned", "reason": "",
                "container_removed": True, "volume_removed": True, "archives_retained": True}
    failure = None
    try:
        processes.start("cleanup-authority", commands["polyphony"], source_paths["polyphony"], environments["polyphony"])
        eventually(verify_canonical, processes, "retained canonical authority before cleanup", 60)
        for _ in range(2):
            result = subprocess.run(cleanup, cwd=source_paths["openjules"], env=environments["openjules"],
                                    capture_output=True, text=True, timeout=60)
            require(result.returncode == 0, "installed cleanup CLI failed; inspect retained private state")
            require(len(result.stdout) <= 4096, "installed cleanup CLI returned oversized evidence")
            value = json.loads(result.stdout)
            require(value == expected and type(value.get("schema_version")) is int
                    and all(value.get(key) is True for key in ("container_removed", "volume_removed", "archives_retained")),
                    "installed cleanup CLI did not return the exact scoped completed receipt")
            require_retained(paths, worker, witness)
            require(verify_canonical() is True, "cleanup changed canonical root or child capture")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            stop_owned(processes, "cleanup-authority")
        except BaseException:
            if failure is not None:
                failure.add_note("cleanup could not reap its temporary owned authority")
            else:
                raise AssertionError("cleanup could not reap its temporary owned authority") from None
    verify(bundle)
    witness["report"] = {"cleanup_cli": expected, "replay_identical": True,
                         "retained_archive": witness["archive_identity"], "retained_idle_workers": 1,
                         "retained_local_journals": sorted(paths), "canonical_artifacts_unchanged": True}
    return witness
