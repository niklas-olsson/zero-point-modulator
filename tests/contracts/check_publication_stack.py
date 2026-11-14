"""Two-phase real draft qualification; never upload its private work directory."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sqlite3
import subprocess
import sys
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener, HTTPRedirectHandler

from check_stack import Client, Processes, clean_environment, eventually, private_json, require, wait_for_capacity
from publication_capture_observation import RootCaptureObservation
from publication_fixture import (
    CHILD_VERIFY, CONTENTS, EXAMPLE, EXPECTED_HASHES, FILES, FORGE_NAMES, MODEL,
    REPOSITORY, REPOSITORY_ID, REQUIREMENTS, ROOT_LAUNCHER, ROOT_VERIFY, TARGET_BRANCH,
)

OPENJULES_REVISION = "0dd5bf4e42b3677c3f2f564655bc87a97c4bf973"
POLYPHONY_REVISION = "6480e36355bf0d5de4e25a560d40e665fd76038c"
MAX_HANDOFF = 131072
MAX_API = 8_000_000
PUBLISH_SECONDS = 120
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class ExecutionFailureObservation:
    """First-failure attribution only; never another runtime or model query."""

    OPERATIONS = frozenset({
        "two-child-grants", "active-root-lineage", "canonical-child-grants",
        "child-executions", "active-worker", "admission-close", "root-termination",
        "root-capture", "final-canonical-lineage", "root-artifacts", "combined-verification",
        "child-artifacts", "child-union", "runtime-history", "inference-accounting",
        "worker-retirement", "guest-isolation", "installation-integrity",
        "execution-cleanup", "handoff", "execution-report",
    })
    FIXTURE_ERRORS = {
        "publication fixture policy mismatch": "policy",
        "publication fixture lacks canonical instructions": "instructions",
        "publication fixture lacks original root requirements": "root-requirements",
        "publication fixture tool boundary changed": "tool-boundary",
        "publication tool result is missing, repeated or failed": "tool-result",
        "publication child exceeded its two-call budget": "child-call-budget",
        "publication root exceeded its six-call budget": "root-call-budget",
        "publication child identity changed": "child-identity",
        "publication child patch is incomplete or outside its assigned file": "child-patch",
        "publication root did not compose the exact children": "child-composition",
        "publication observation gate timed out": "observation-gate",
    }

    def __init__(self):
        self.operation = None
        self.model = None
        self._failure = None
        self.root_capture = RootCaptureObservation()

    def bind_capture(self, **context):
        try:
            self.root_capture.bind(**context)
        except BaseException:
            pass

    def observe_capture_snapshot(self, value):
        try:
            if self.operation == "root-capture":
                self.root_capture.observe_snapshot(value)
        except BaseException:
            pass
        return value

    @staticmethod
    def exception_kind(error):
        for category, kind in (
            (AssertionError, "assertion"), (subprocess.TimeoutExpired, "timeout"),
            (TimeoutError, "timeout"), (HTTPError, "http"), (URLError, "url"),
            (ConnectionError, "connection"), (OSError, "os"),
            (json.JSONDecodeError, "json"), (subprocess.CalledProcessError, "process-exit"),
            (ValueError, "value"), (KeyError, "key"), (TypeError, "type"),
            (KeyboardInterrupt, "interrupted"),
        ):
            if isinstance(error, category):
                return kind
        return "other"

    def fixture_observation(self):
        # These are non-atomic observations of local fixture fields. They are
        # not proof that any canonical grant, execution or capture completed.
        def count(value, maximum):
            return value if type(value) is int and 0 <= value <= maximum else None

        model = self.model
        requests, children, errors = model.requests, model.child_turns, model.errors
        spawned = model.two_spawned.is_set()
        first_error = (errors[0] if errors else None) if type(errors) is list else None
        return {
            "available": True,
            "root_turns": count(model.root_turns, 6),
            "child_turns": {name: count(children.get(name), 2) if type(children) is dict else None
                            for name in ("runbook", "example")},
            "accepted_requests": count(len(requests), 10) if type(requests) is list else None,
            "two_spawned": spawned if type(spawned) is bool else None,
            "error_observed": bool(errors) if type(errors) is list else None,
            "first_error": (self.FIXTURE_ERRORS.get(first_error, "other") if type(first_error) is str
                            else "other") if type(errors) is not list or errors else None,
        }

    def capture(self, error, *, before_cleanup):
        if self._failure is not None or self.operation is None:
            return
        operation = self.operation if type(self.operation) is str and self.operation in self.OPERATIONS else "other"
        self._failure = {
            "operation": operation, "exception_kind": self.exception_kind(error),
            "timing": "before-owned-cleanup" if before_cleanup else "after-cleanup-attempt",
            "fixture": None,
        }
        if before_cleanup:
            try:
                self._failure["fixture"] = self.fixture_observation()
            except BaseException:
                # A diagnostic must not replace the original failure.
                self._failure["fixture"] = {"available": False}
            if operation == "root-capture":
                try:
                    self._failure["root_capture"] = self.root_capture.failure(error)
                except BaseException:
                    self._failure["root_capture"] = {"available": False}

    @contextmanager
    def record(self):
        try:
            yield
        except BaseException as error:
            try:
                self.capture(error, before_cleanup=True)
            except BaseException:
                pass
            raise

    def report(self):
        return json.loads(json.dumps(self._failure))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_private_json(path, limit=MAX_HANDOFF):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit
                and info.st_mode & 0o077 == 0, "publication private handoff is not a bounded private regular file")
        content = source.read(limit + 1)
        require(len(content) == info.st_size, "publication private handoff changed during read")
    return json.loads(content)


def write_private_json(path, value):
    content = canonical(value).encode()
    require(len(content) <= MAX_HANDOFF, "publication private handoff exceeds its bound")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def workflow_identity(environment):
    require(environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("GITHUB_REPOSITORY") == REPOSITORY.removeprefix("github:github.com/")
            and environment.get("GITHUB_REPOSITORY_ID") == str(REPOSITORY_ID)
            and environment.get("GITHUB_REF") == "refs/heads/" + TARGET_BRANCH,
            "publication requires the approved repository and default-branch manual dispatch")
    base, run_id = environment.get("GITHUB_SHA", ""), environment.get("GITHUB_RUN_ID", "")
    require(SHA.fullmatch(base) is not None and re.fullmatch(r"[1-9][0-9]{0,19}", run_id) is not None
            and environment.get("GITHUB_RUN_ATTEMPT") == "1",
            "publication refuses invalid identity or a fresh-runner rerun; reconcile the original operation")
    return {"repository": REPOSITORY, "repository_id": REPOSITORY_ID,
            "base": base, "run_id": run_id, "run_attempt": 1}


def git(path, *arguments):
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(path), *arguments],
        text=True, timeout=15, env=clean_environment({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}),
    ).strip()


def source_paths(args, identity):
    paths = {"zpm": Path(__file__).resolve().parents[2],
             "openjules": args.openjules_checkout.resolve(strict=True),
             "polyphony": args.polyphony_checkout.resolve(strict=True)}
    revisions = {name: git(path, "rev-parse", "HEAD") for name, path in paths.items()}
    require(revisions == {"zpm": identity["base"], "openjules": OPENJULES_REVISION,
                          "polyphony": POLYPHONY_REVISION}, "publication source revisions changed")
    require((paths["zpm"] / ".git").is_dir(), "publication requires a complete approved checkout")
    require(not git(paths["zpm"], "diff", "--name-only", "HEAD"), "publication approved checkout has tracked changes")
    require(not git(paths["zpm"], "ls-tree", "-r", "--name-only", identity["base"], "--", *FILES.values()),
            "publication deliverable already exists; do not duplicate the task")
    return paths, revisions


def completed_verification(log):
    events = []
    for line in log.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except json.JSONDecodeError:
            pass
    profiles = [event for event in events if event.get("event") == "publication_profile"]
    require(len(profiles) == 1 and type(profiles[0].get("command_seconds")) is int
            and profiles[0]["command_seconds"] == 120, "publication root did not select its approved command bound")
    verifications = [event for event in events if event.get("event") == "configured_verification"]
    require(len(verifications) == 1, "publication root lacks exactly one configured whole-suite verification")
    result = verifications[0]
    require(result.get("state") == "exited" and type(result.get("exit_code")) is int and result["exit_code"] == 0,
            "publication combined verification failed or timed out")
    records = [line.removeprefix("PUBLICATION_COMBINED_RESULT ") for line in result.get("output", "").splitlines()
               if line.startswith("PUBLICATION_COMBINED_RESULT ")]
    require(len(records) == 1, "publication combined verification lacks its bounded result")
    record = json.loads(records[0])
    require(set(record) == {"tests", "expected_tests", "elapsed_seconds", "exit_code"}
            and type(record["tests"]) is int and record["tests"] > 0
            and type(record["expected_tests"]) is int and record["tests"] == record["expected_tests"]
            and type(record["exit_code"]) is int and record["exit_code"] == 0
            and type(record["elapsed_seconds"]) in {int, float} and math.isfinite(record["elapsed_seconds"])
            and 0 < record["elapsed_seconds"] < 120, "publication combined verification result is malformed")
    return record


def stable_evidence(snapshot, children):
    require(set(snapshot) >= {"task", "attempt", "candidate"} and len(snapshot["task"]) == len(snapshot["attempt"])
            == len(snapshot["candidate"]) == 1, "publication canonical root is absent or duplicated")
    require(children.get("admission_closed") is True and len(children.get("children", [])) == 2,
            "publication final child lineage is incomplete or open")
    records = [row["child"] for row in children["children"]]
    require(len({row["id"] for row in records}) == 2, "publication child identity is duplicated")
    return {**{kind: snapshot[kind] for kind in ("task", "attempt", "candidate")},
            "children": sorted(records, key=lambda row: row["id"]), "child_admission_closed": True}


def validate_handoff(value, identity, root):
    required = {"schema_version", "phase", "identity", "scope", "revisions", "image", "candidate", "attempt",
                "worker", "children", "canonical_sha256", "root_artifacts", "child_artifacts",
                "combined_verification", "verification_log", "inference", "bundle", "source"}
    require(isinstance(value, dict) and set(value) == required and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["phase"] == "captured_and_reaped"
            and canonical(value["identity"]) == canonical(identity), "publication handoff identity or phase changed")
    require(value["scope"] == {"deployment_id": "publication-" + identity["run_id"], "repository": REPOSITORY}
            and value["revisions"] == {"zpm": identity["base"], "openjules": OPENJULES_REVISION,
                                     "polyphony": POLYPHONY_REVISION}, "publication handoff scope or pins changed")
    require(value["bundle"] == str(root / "installation") and isinstance(value["source"], str)
            and Path(value["source"]).is_absolute()
            and isinstance(value["worker"], str) and value["worker"]
            and isinstance(value["candidate"], str) and DIGEST.fullmatch(value["candidate"]) is not None
            and isinstance(value["canonical_sha256"], str) and DIGEST.fullmatch(value["canonical_sha256"]) is not None
            and isinstance(value["attempt"], str) and value["attempt"], "publication handoff paths or identities changed")
    require(isinstance(value["children"], list) and len(value["children"]) == 2
            and all(isinstance(child, str) and re.fullmatch(r"child_[0-9a-f]{32}", child) for child in value["children"])
            and len(set(value["children"])) == 2,
            "publication handoff child identities changed")
    require(isinstance(value["child_artifacts"], dict), "publication child artifact map changed")
    for mapping in [value["root_artifacts"], *value["child_artifacts"].values()]:
        require(isinstance(mapping, dict) and set(mapping) == {"patch", "log", "manifest"}
                and all(isinstance(item, str) and DIGEST.fullmatch(item) for item in mapping.values()),
                "publication handoff artifact references changed")
    require(set(value["child_artifacts"]) == set(value["children"])
            and canonical(value["inference"]) == canonical({"calls": 10, "child_calls": 4, "reserved_output_tokens": 10240}),
            "publication handoff inference or lineage changed")
    log = value["verification_log"]
    require(isinstance(log, dict) and set(log) == {"archive_sha256", "log_sha256", "log_bytes"}
            and type(log["log_bytes"]) is int and 0 < log["log_bytes"] <= 2_000_000
            and all(isinstance(log[key], str) and DIGEST.fullmatch(log[key]) for key in ("archive_sha256", "log_sha256")),
            "publication private verification log identity changed")
    require(isinstance(value["image"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value["image"]),
            "publication image identity changed")
    return value


def publisher_configuration(handoff, configuration, root):
    # The task-produced example is never used to select privileged values.
    result = json.loads(json.dumps(EXAMPLE))
    result.update(state_directory=str(root / "publication"), source_path=handoff["source"],
                  base_commit=handoff["identity"]["base"], operation_seconds=PUBLISH_SECONDS)
    result["authority"] = {"endpoint": f"http://127.0.0.1:{configuration['ports']['polyphony']}",
                           "token_ref": "env:POLYPHONY_PUBLICATION_READ_TOKEN"}
    result["forge"].update(deployment_id=handoff["scope"]["deployment_id"],
                            branch_prefix="openjules/managed/qualification-" + handoff["identity"]["run_id"] + "/")
    return result


def expected_operation(handoff):
    return digest([handoff["scope"]["deployment_id"], REPOSITORY, handoff["candidate"]])


def validate_publication(value, handoff):
    fields = {"schema_version", "operation", "status", "candidate_id", "ready_for_review",
              "release_authorized", "manifest_sha256", "tree_sha", "commit_sha", "proposal"}
    require(isinstance(value, dict) and set(value) == fields and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["status"] == "draft_published"
            and value["operation"] == expected_operation(handoff) and value["candidate_id"] == handoff["candidate"]
            and value["ready_for_review"] is False and value["release_authorized"] is False,
            "publisher did not observe the original new draft; reconcile retained state")
    require(all(isinstance(value[key], str) and SHA.fullmatch(value[key]) for key in ("tree_sha", "commit_sha"))
            and isinstance(value["manifest_sha256"], str) and DIGEST.fullmatch(value["manifest_sha256"]),
            "publication Git or provenance identities are malformed")
    proposal = value["proposal"]
    require(isinstance(proposal, dict) and set(proposal) == {
        "number", "url", "state", "draft", "head_sha", "base_sha", "head_branch", "base_branch", "repository_id"},
        "publication proposal shape changed")
    require(type(proposal["number"]) is int and proposal["number"] > 0
            and type(proposal["repository_id"]) is int and proposal["repository_id"] == REPOSITORY_ID
            and proposal["state"] == "open" and proposal["draft"] is True
            and proposal["base_sha"] == handoff["identity"]["base"] and proposal["base_branch"] == TARGET_BRANCH
            and proposal["head_sha"] == value["commit_sha"]
            and proposal["head_branch"] == "openjules/managed/qualification-" + handoff["identity"]["run_id"] + "/" + value["operation"]
            and proposal["url"] == f"https://github.com/niklas-olsson/zero-point-modulator/pull/{proposal['number']}",
            "publication draft destination, base, head or state changed")
    return value


def git_blob_sha(content):
    encoded = content.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(encoded)).encode() + b"\0" + encoded).hexdigest()


def fixed_files_from_patch(patch):
    """Only this trial's two fixed new regular files can reach publication."""
    require(isinstance(patch, str) and 0 < len(patch.encode()) <= 32768,
            "publication patch is absent or oversized")
    blocks = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    require(blocks[0] == "", "publication patch has an unexpected preamble")
    result = {}
    by_path = {FILES[key]: content for key, content in CONTENTS.items()}
    for block in blocks[1:]:
        lines = block.splitlines(keepends=True)
        require(len(lines) >= 7, "publication patch is incomplete")
        header = re.fullmatch(r"diff --git a/(\S+) b/(\S+)\n", lines[0])
        require(header is not None and header[1] == header[2] and header[1] in by_path
                and header[1] not in result, "publication patch changes an unexpected or repeated path")
        path, content = header[1], by_path[header[1]]
        expected_lines = content.splitlines(keepends=True)
        index = re.fullmatch(r"index (0{7,40})\.\.([0-9a-f]{7,40})\n", lines[2])
        require(lines[1] == "new file mode 100644\n" and index is not None
                and git_blob_sha(content).startswith(index[2])
                and lines[3] == "--- /dev/null\n" and lines[4] == "+++ b/" + path + "\n"
                and lines[5] == "@@ -0,0 +1," + str(len(expected_lines)) + " @@\n"
                and lines[6:] == ["+" + line for line in expected_lines],
                "publication task file differs from the fixed reviewed content")
        result[path] = git_blob_sha(content)
    return result


def validate_task_patches(root_patch, child_patches):
    require(isinstance(child_patches, dict) and len(child_patches) == 2,
            "publication requires the original two child patches")
    combined = {}
    for patch in child_patches.values():
        files = fixed_files_from_patch(patch)
        require(len(files) == 1 and not set(files) & set(combined),
                "publication child work overlaps or contains extra files")
        combined.update(files)
    expected = {FILES[key]: git_blob_sha(content) for key, content in CONTENTS.items()}
    require(combined == expected and fixed_files_from_patch(root_patch) == expected,
            "publication root is not exactly the approved two-child union")
    return expected


def configured_result(log, marker):
    events = []
    for line in log.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event") == "configured_verification":
            events.append(value)
    require(len(events) == 1 and events[0].get("state") == "exited"
            and type(events[0].get("exit_code")) is int and events[0]["exit_code"] == 0
            and marker in events[0].get("output", ""), "publication configured child verification failed")
    return events[0]


def single_runtime_execution(config, worker, attempt, manifest):
    directory = Path(config["state_directory"]) / "runtime"
    with sqlite3.connect((directory / "journal.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=1) as database:
        workers = database.execute("SELECT worker_id FROM workers").fetchall()
        attempts = database.execute("SELECT worker_id,attempt_id,exit_code FROM attempts").fetchall()
    require(workers == [(worker,)] and attempts == [(worker, attempt, 0)],
            "publication created another worker/attempt or its root failed")
    archive_path = directory / "captures" / (hashlib.sha256(worker.encode()).hexdigest() + ".tar")
    limit = config["profiles"][0]["max_archive_bytes"]
    with os.fdopen(os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, "publication private archive is unsafe")
        archive_bytes = source.read(limit + 1)
    require(len(archive_bytes) == info.st_size
            and hashlib.sha256(archive_bytes).hexdigest() == manifest["archive_sha256"],
            "publication private archive changed")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        matches = [item for item in archive if item.name == "workspace/run/publication-unittest.log"]
        require(len(matches) == 1 and matches[0].isfile() and 0 < matches[0].size <= 2_000_000,
                "publication complete private verification log was not captured")
        log = archive.extractfile(matches[0]).read(2_000_001)
    require(len(log) == matches[0].size, "publication private verification log changed during read")
    return {"archive_sha256": manifest["archive_sha256"], "log_sha256": hashlib.sha256(log).hexdigest(),
            "log_bytes": len(log)}


@contextmanager
def owned_processes(root):
    from check_authority_admission import stop_owned
    processes, failure = Processes(root), None
    try:
        yield processes
    except BaseException as exc:
        failure = exc
        raise
    finally:
        errors = []
        for name in list(reversed(processes.running)):
            try:
                stop_owned(processes, name)
            except BaseException:
                errors.append(name)
        for handle in processes.handles:
            try:
                handle.close()
            except OSError:
                errors.append("log")
        if errors:
            if failure is not None:
                failure.add_note("publication could not reap all owned processes or close private logs")
            else:
                raise AssertionError("publication could not reap all owned processes or close private logs")


def require_execution_reaped(processes, manager, model):
    # Services use separate sessions. Only the manager's completed signal path
    # proves its own finally reaped them; an empty bookkeeping map is insufficient.
    require(not processes.running and manager.poll() == 130 and not model.thread.is_alive(),
            "publication execution shutdown was forced, incomplete or unconfirmed")


def active_drain_admission_closed(current, read_children):
    """Check local capacity before reading canonical child admission."""
    return (current["reserved"] == 1 and current["repositories"][0]["effective_target"] == 0
            and read_children().get("admission_closed") is True)


def execute(args):
    require(not any(name in os.environ for name in FORGE_NAMES),
            "runtime/model phase must not receive a forge credential")
    identity = workflow_identity(os.environ)
    paths, revisions = source_paths(args, identity)
    root = args.work_dir.absolute()
    require(not root.exists(), "publication execution requires a new private directory; do not replay a fresh candidate")
    root.mkdir(mode=0o700)
    from publication_fixture import PublicationFixture
    from check_children_stack import child_execs_started, inference_reservations, read_artifacts
    from check_isolation_stack import wait_for_capture
    from check_model_stack import TOKEN_SOURCE
    from check_stack import port
    from zpm.controller import Controller
    from zpm.deployment import MODEL_TOKEN_ENV, prepare, verify
    from zpm.store import Store

    args.stage = "runtime-preflight"
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
                                    text=True, timeout=30).strip()
    require(image == args.image and re.fullmatch(r"sha256:[0-9a-f]{64}", image),
            "publication requires the exact prebuilt managed-agent image")
    bundle = root / "installation"
    scope = {"deployment_id": "publication-" + identity["run_id"], "repository": REPOSITORY}
    profile = "approved-publication-agent-v1"
    authority_port, runtime_port = port(), port()
    while authority_port == runtime_port:
        runtime_port = port()
    handoff = None
    observation = ExecutionFailureObservation()
    args.execution_observation = observation
    with PublicationFixture() as model:
        observation.model = model
        with owned_processes(root) as processes, observation.record():
            prepare({
                "schema_version": 1, "deployment_id": scope["deployment_id"],
                "binding": {"schema_version": 1, "repository": REPOSITORY, "profile": profile, "desired_workers": 0},
                "max_workers": 1,
                "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in paths.items()},
                "source": {"checkout": str(paths["zpm"]), "revision": identity["base"]}, "image": image,
                "command": ["python3", "-I", "-S", "-c", ROOT_LAUNCHER,
                            "--socket", "/run/openjules-model/model.sock", "--control-socket", "/run/openjules-model/control.sock",
                            "--max-turns", "6", "--verify-json", json.dumps(ROOT_VERIFY)],
                "ports": {"polyphony": authority_port, "openjules": runtime_port},
                "runtime": {"max_seconds": 240},
                "policy": {"checks": ["repository-tests"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 360, "attempt_seconds": 180, "capabilities": ["python"]},
                "model_policy": {"endpoint": model.endpoint, "model": MODEL, "token_ref": "env:" + TOKEN_SOURCE,
                                 "max_calls": 10, "max_output_tokens": 1024, "max_total_output_tokens": 10240,
                                 "request_timeout_seconds": 30},
                "child_policy": {"max_children": 2, "max_child_seconds": 90, "max_child_turns": 2,
                                 "capabilities": ["python"], "uid_start": 60000, "verify_argv": CHILD_VERIFY},
            }, bundle)
            verify(bundle)
            environment_tokens = read_private_json(bundle / "secrets/environment.json")
            operators = read_private_json(bundle / "secrets/operator.json")
            sensitive = {model.token, *environment_tokens.values(), *operators.values()}
            runtime_config = read_private_json(bundle / "config/openjules.json", 1_000_000)
            require(runtime_config["models"][0]["policy"]["token_ref"] == "env:" + MODEL_TOKEN_ENV
                    and runtime_config["profiles"][0]["environment_refs"] == {},
                    "publication installation changed its model/guest credential boundary")
            authority = Client(f"http://127.0.0.1:{authority_port}", environment_tokens["POLYPHONY_RUNTIME_TOKEN"])
            ingress = Client(f"http://127.0.0.1:{authority_port}", operators["ingress"])
            runtime = Client(f"http://127.0.0.1:{runtime_port}", environment_tokens["OPENJULES_WORKER_TOKEN"])
            manager = [sys.executable, str(bundle / "manage.py")]
            manager_env = clean_environment({TOKEN_SOURCE: model.token})
            state = bundle / "state/zpm.sqlite3"

            def plan():
                with Store(state, scope["deployment_id"]) as store:
                    return Controller(store).plan()

            def target(count):
                with Store(state, scope["deployment_id"]) as store:
                    store.change(REPOSITORY, "target", count)

            def snapshot():
                return observation.observe_capture_snapshot(authority.request("GET", "/managed/snapshot", query=scope))

            def children(attempt_id):
                return authority.request("GET", "/managed/children", query=scope | {"attempt_id": attempt_id})

            def ready():
                result = subprocess.run(manager + ["doctor"], cwd=bundle, env=clean_environment(),
                                        capture_output=True, text=True, timeout=50)
                require(result.returncode in {0, 3}, "publication doctor failed")
                return json.loads(result.stdout).get("ready") is True

            args.stage = "installation"
            processes.run_once("bootstrap", manager + ["bootstrap"], bundle, clean_environment(), 900)
            installation_process = processes.start("installation", manager + ["start"], bundle, manager_env)
            eventually(ready, processes, "publication installation readiness", 60)
            require(plan()["reserved"] == 0 and not model.requests, "publication readiness performed unrequested work")
            target(1)
            wait_for_capacity(plan, processes, 1)
            request = {"schema_version": 1, **scope,
                       "source": "github:workflow:managed-publication:" + identity["run_id"],
                       "request_key": "publication-run-" + identity["run_id"],
                       "title": "Document the installed managed publication workflow",
                       "requirements": REQUIREMENTS}
            task = ingress.request("POST", "/managed/enroll", data=request)["task"]["id"]
            require(ingress.request("POST", "/managed/enroll", data=request)["task"]["id"] == task,
                    "publication original enrollment replay created another task")
            args.stage = "root-and-children"
            observation.operation = "two-child-grants"
            eventually(lambda: model.two_spawned.is_set() and not model.errors,
                       processes, "publication two real grants", 60)
            try:
                # Freeze a failure before the existing finally releases the root.
                with observation.record():
                    observation.operation = "active-root-lineage"
                    active = snapshot()
                    require(len(active["task"]) == len(active["attempt"]) == 1 and active["task"][0]["id"] == task,
                            "publication created another root task or attempt")
                    attempt = active["attempt"][0]
                    worker = attempt["runtime_binding"]["worker_id"]
                    observation.bind_capture(config=runtime_config, scope=scope, task=task, worker=worker,
                                             attempt=attempt["id"], base=identity["base"], source=paths["zpm"])
                    observation.operation = "canonical-child-grants"
                    grants = children(attempt["id"])
                    require(len(grants["children"]) == 2
                            and {item["child"]["id"] for item in grants["children"]} == set(model.child_ids.values()),
                            "publication root did not receive both real canonical child grants")
                    observation.operation = "child-executions"
                    eventually(lambda: child_execs_started(runtime_config, worker, model.child_ids.values()),
                               processes, "publication real child execs", 10)
                    observation.operation = "active-worker"
                    observed = runtime.request("GET", "/api/workers/" + worker, query=scope)
                    require(set(observed["active_executions"]) == {attempt["id"], *model.child_ids.values()}
                            and plan()["reserved"] == 1, "publication did not retain one worker for root and both children")
                    # Close new admission before releasing the root, avoiding an idle replacement.
                    observation.operation = "admission-close"
                    target(0)
                    def admission_closed():
                        return active_drain_admission_closed(plan(), lambda: children(attempt["id"]))
                    eventually(admission_closed, processes, "publication active drain admission closure", 10)
            finally:
                model.release_root.set()

            def terminal():
                require(not model.errors, "publication fixture rejected a tool/model request")
                records = snapshot()["attempt"]
                require(len(records) == 1 and records[0]["id"] == attempt["id"], "publication original attempt changed")
                return records[0].get("termination_confirmed") is True

            observation.operation = "root-termination"
            eventually(terminal, processes, "publication bounded whole-suite execution and capture", 190)
            observation.operation = "root-capture"
            attempt, candidate = wait_for_capture(snapshot, scope, task, worker, identity["base"],
                                                  processes, "publication final candidate")
            observation.operation = "final-canonical-lineage"
            evidence_snapshot = snapshot()
            final_children = children(attempt["id"])
            evidence = stable_evidence(evidence_snapshot, final_children)
            require(candidate["id"] == evidence_snapshot["candidate"][0]["id"],
                    "publication canonical candidate changed after capture")
            observation.operation = "root-artifacts"
            artifacts = read_artifacts(authority, scope, attempt["captured_artifacts"], sensitive)
            root_manifest = json.loads(artifacts["manifest"])
            require(root_manifest.get("attempt_id") == attempt["id"] and root_manifest.get("worker_id") == worker
                    and root_manifest.get("base_commit") == identity["base"]
                    and root_manifest.get("termination_confirmed") is True and root_manifest.get("launch_fenced") is True,
                    "publication root manifest changed its original execution or fence")
            observation.operation = "combined-verification"
            combined = completed_verification(artifacts["log"])
            observation.operation = "child-artifacts"
            child_artifacts, child_patches = {}, {}
            for row in final_children["children"]:
                child = row["child"]
                require(child["id"] in model.child_ids.values() and child["termination_confirmed"] is True
                        and child["launch_fenced"] is True, "publication final child is not the original fenced grant")
                values = read_artifacts(authority, scope, child["captured_artifacts"], sensitive,
                                        attempt_id=attempt["id"], child_id=child["id"])
                manifest = json.loads(values["manifest"])
                require(manifest.get("child_id") == child["id"] and manifest.get("root_attempt_id") == attempt["id"]
                        and manifest.get("worker_id") == worker and manifest.get("grant_digest") == child["binding"]["grant_digest"]
                        and manifest.get("archive_sha256") == root_manifest["archive_sha256"]
                        and manifest.get("root_manifest_sha256") == attempt["captured_artifacts"]["manifest"]
                        and manifest.get("exec_start_dispatched") is True and manifest.get("not_started") is False
                        and manifest.get("termination_confirmed") is True and manifest.get("launch_fenced") is True,
                        "publication child manifest changed its grant, execution or final capture")
                configured_result(values["log"], "PUBLICATION_CHILD_VERIFIED")
                child_patches[child["id"]] = values["patch"]
                child_artifacts[child["id"]] = child["captured_artifacts"]
            observation.operation = "child-union"
            validate_task_patches(artifacts["patch"], child_patches)
            observation.operation = "runtime-history"
            verification_log = single_runtime_execution(runtime_config, worker, attempt["id"], root_manifest)
            observation.operation = "inference-accounting"
            allowance = inference_reservations(runtime_config, worker, attempt["id"], 10)
            require(len(model.requests) == 10 and not model.errors, "publication consumed unexpected inference")
            observation.operation = "worker-retirement"
            wait_for_capacity(plan, processes, 0, retired_worker_ids={worker})
            require(plan()["repositories"][0]["desired_workers"] == 0, "publication target reopened")
            observation.operation = "guest-isolation"
            inspected = json.loads(subprocess.check_output(["docker", "inspect", root_manifest["container_id"]],
                                                           text=True, timeout=10))[0]
            require(inspected["State"]["Running"] is False and inspected["HostConfig"]["NetworkMode"] == "none"
                    and inspected["HostConfig"]["ReadonlyRootfs"] is True and inspected["HostConfig"]["CapDrop"] == ["ALL"]
                    and not any(name in json.dumps(inspected) for name in FORGE_NAMES)
                    and not any(token in json.dumps(inspected) for token in sensitive),
                    "publication guest violated its runtime or credential boundary")
            observation.operation = "installation-integrity"
            verify(bundle)
            handoff = {"schema_version": 1, "phase": "captured_and_reaped", "identity": identity,
                       "scope": scope, "revisions": revisions, "image": image, "candidate": candidate["id"],
                       "attempt": attempt["id"], "worker": worker, "children": sorted(child_artifacts),
                       "canonical_sha256": digest(evidence), "root_artifacts": attempt["captured_artifacts"],
                       "child_artifacts": child_artifacts, "combined_verification": combined, "verification_log": verification_log,
                       "inference": allowance,
                       "bundle": str(bundle), "source": str(paths["zpm"])}
            observation.operation = "execution-cleanup"
    require_execution_reaped(processes, installation_process, model)
    # This is the sole handoff, written only after every phase-one process stops.
    observation.operation = "handoff"
    write_private_json(root / "handoff.json", validate_handoff(handoff, identity, root))
    observation.operation = "execution-report"
    report = {"passed": True, "phase": "execution", "component_revisions": revisions,
              "repository_id": REPOSITORY_ID, "base": identity["base"], "run_id": identity["run_id"],
              "candidate": handoff["candidate"], "root_attempt": handoff["attempt"], "children": handoff["children"],
              "combined_verification": combined, "private_verification_log": verification_log,
              "inference": allowance, "all_phase_one_processes_reaped": True,
              "proof": "real generated installation and Docker root/children; deterministic provider replies",
              "not_proven": ["live provider behavior", "forge publication", "independent review", "cross-job recovery"]}
    private_json(root / "execution-report.json", report)
    return report


def run_json_process(processes, name, command, cwd, environment, timeout, limit=16384):
    """Keep output private and always reap the owned process group on failure."""
    from check_authority_admission import stop_owned
    path = processes.root / (name + ".log")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"):
        pass
    process = processes.start(name, command, cwd, environment)
    failure = None
    try:
        require(process.wait(timeout=timeout) == 0, "publication child process failed; reconcile original private state")
        return read_private_json(path, limit)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            stop_owned(processes, name)
        except BaseException:
            if failure is not None:
                failure.add_note("publication could not reap an owned bounded child")
            else:
                raise AssertionError("publication could not reap an owned bounded child") from None


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class ForgeReader:
    """Read-only fixed-host oracle, run in its own hard-bounded child process."""

    def __init__(self, token):
        self.token, self.calls = token, 0
        self.opener = build_opener(ProxyHandler({}), RejectRedirect())

    def get(self, suffix):
        self.calls += 1
        require(self.calls <= 9 and isinstance(suffix, str) and (suffix == "" or suffix.startswith("/")),
                "publication remote observation exceeded its fixed request budget")
        request = Request("https://api.github.com/repos/niklas-olsson/zero-point-modulator" + suffix,
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2026-03-10"}, method="GET")
        with self.opener.open(request, timeout=10) as response:
            require(response.status == 200 and response.headers.get("Content-Encoding", "identity") == "identity",
                    "publication remote observation is not an ordinary unencoded success")
            content = response.read(MAX_API + 1)
        require(0 < len(content) <= MAX_API, "publication remote observation exceeds its byte limit")
        return json.loads(content)


def tree_entries(value, expected_sha):
    require(isinstance(value, dict) and value.get("sha") == expected_sha and value.get("truncated") is False
            and isinstance(value.get("tree"), list) and len(value["tree"]) <= 10000,
            "publication remote Git tree is incomplete or oversized")
    entries = {}
    seen = set()
    for row in value["tree"]:
        require(isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"]
                and row["path"] not in seen and not row["path"].startswith("/")
                and all(part not in {"", ".", ".."} for part in row["path"].split("/"))
                and isinstance(row.get("sha"), str) and SHA.fullmatch(row["sha"]),
                "publication remote Git tree contains malformed or duplicate paths")
        seen.add(row["path"])
        if row.get("type") == "tree":
            require(row.get("mode") == "040000", "publication remote Git directory mode changed")
        else:
            require(row.get("type") == "blob" and row.get("mode") in {"100644", "100755"},
                    "publication remote Git tree contains an unsupported object")
            entries[row["path"]] = {"sha": row["sha"], "mode": row["mode"]}
    return entries


def verify_remote(reader, handoff, result):
    result = validate_publication(result, handoff)
    repository = reader.get("")
    require(repository.get("id") == REPOSITORY_ID
            and repository.get("full_name") == "niklas-olsson/zero-point-modulator"
            and repository.get("default_branch") == TARGET_BRANCH, "publication actual repository identity changed")
    base = handoff["identity"]["base"]
    reference = reader.get("/git/ref/heads/" + TARGET_BRANCH)
    require(reference.get("object", {}).get("type") == "commit"
            and reference["object"].get("sha") == base, "publication actual approved base moved")
    branch = result["proposal"]["head_branch"]
    actual_branch = reader.get("/git/ref/heads/" + quote(branch, safe=""))
    require(actual_branch.get("ref") == "refs/heads/" + branch
            and actual_branch.get("object", {}).get("type") == "commit"
            and actual_branch["object"].get("sha") == result["commit_sha"],
            "publication actual managed branch is missing, foreign or advanced")
    commit = reader.get("/git/commits/" + result["commit_sha"])
    require(commit.get("sha") == result["commit_sha"] and commit.get("tree", {}).get("sha") == result["tree_sha"]
            and isinstance(commit.get("parents"), list) and len(commit["parents"]) == 1
            and commit["parents"][0].get("sha") == base, "publication actual commit/tree/parent changed")
    base_commit = reader.get("/git/commits/" + base)
    base_tree = base_commit.get("tree", {}).get("sha")
    require(base_commit.get("sha") == base and isinstance(base_tree, str) and SHA.fullmatch(base_tree),
            "publication actual source commit is malformed")
    original = tree_entries(reader.get("/git/trees/" + base_tree + "?recursive=1"), base_tree)
    published = tree_entries(reader.get("/git/trees/" + result["tree_sha"] + "?recursive=1"), result["tree_sha"])
    expected = {FILES[key]: {"sha": git_blob_sha(content), "mode": "100644"} for key, content in CONTENTS.items()}
    require(not set(original) & set(expected) and published == original | expected,
            "publication actual remote tree is not the exact two-child contribution")
    proposal = reader.get("/pulls/" + str(result["proposal"]["number"]))
    marker = "<!-- openjules-publication:" + result["operation"] + ":" + result["manifest_sha256"] + " -->"
    require(proposal.get("number") == result["proposal"]["number"] and proposal.get("html_url") == result["proposal"]["url"]
            and proposal.get("state") == "open" and proposal.get("draft") is True
            and proposal.get("head", {}).get("sha") == result["commit_sha"]
            and proposal.get("head", {}).get("ref") == result["proposal"]["head_branch"]
            and proposal.get("base", {}).get("sha") == base
            and proposal.get("base", {}).get("ref") == TARGET_BRANCH
            and proposal.get("head", {}).get("repo", {}).get("id") == REPOSITORY_ID
            and proposal.get("base", {}).get("repo", {}).get("id") == REPOSITORY_ID
            and marker in proposal.get("body", ""), "publication actual draft or provenance marker changed")
    matches = reader.get("/pulls?state=all&head=" + quote("niklas-olsson:" + result["proposal"]["head_branch"], safe="")
                         + "&per_page=100")
    require(isinstance(matches, list) and len(matches) == 1 and matches[0].get("number") == proposal["number"],
            "publication branch does not have exactly one actual proposal")
    return {"repository_id": REPOSITORY_ID, "base": base, "commit_sha": result["commit_sha"],
            "tree_sha": result["tree_sha"], "proposal_number": proposal["number"], "proposal_url": proposal["html_url"],
            "state": "open", "draft": True, "manifest_sha256": result["manifest_sha256"],
            "exact_child_union": True, "new_paths": sorted(expected), "api_read_requests": reader.calls}


def observe(args):
    # Only called by the trusted publisher phase, under run_json_process's
    # 90-second total deadline. Socket timeouts alone are not a total deadline.
    identity = workflow_identity(os.environ)
    root = args.work_dir.absolute()
    handoff = validate_handoff(read_private_json(root / "handoff.json"), identity, root)
    result = read_private_json(root / "publisher-replay.log", 16384)
    token = os.environ.get("GITHUB_PUBLICATION_TOKEN", "")
    require(token and len(token) <= 8192 and not any(character.isspace() for character in token),
            "publication remote observer has no valid scoped credential")
    return verify_remote(ForgeReader(token), handoff, result)


def publish(args):
    identity = workflow_identity(os.environ)
    token = os.environ.get("GITHUB_PUBLICATION_TOKEN", "")
    require(token and len(token) <= 8192 and not any(character.isspace() for character in token)
            and not any(name in os.environ for name in ("GH_TOKEN", "GITHUB_TOKEN")),
            "publisher-only phase requires its one explicit forge credential reference")
    paths, revisions = source_paths(args, identity)
    root = args.work_dir.absolute()
    require(root.is_dir() and not root.is_symlink() and root.stat().st_mode & 0o077 == 0,
            "publication original private directory is unavailable")
    handoff = validate_handoff(read_private_json(root / "handoff.json"), identity, root)
    require(handoff["source"] == str(paths["zpm"]), "publication original source checkout changed")
    from check_children_stack import read_artifacts
    from zpm.deployment import _command, _environment, verify
    bundle = root / "installation"
    verify(bundle)
    configuration = read_private_json(bundle / "manifest.json", 1_000_000)["configuration"]
    require(configuration["deployment_id"] == handoff["scope"]["deployment_id"]
            and configuration["binding"]["repository"] == REPOSITORY
            and configuration["source"] == {"checkout": handoff["source"], "revision": identity["base"]}
            and configuration["image"] == handoff["image"], "publication original installation scope or source changed")
    environment_tokens = read_private_json(bundle / "secrets/environment.json")
    runtime_config = read_private_json(bundle / "config/openjules.json", 1_000_000)
    authority_token = environment_tokens["POLYPHONY_RUNTIME_TOKEN"]
    require(token != authority_token, "publication authority and forge credentials must be distinct")
    sensitive = {token, *environment_tokens.values(), *read_private_json(bundle / "secrets/operator.json").values()}
    authority_environment = _environment(bundle, "polyphony", configuration)
    require(not set(FORGE_NAMES) & set(authority_environment)
            and token not in authority_environment.values(), "publication authority received a forge credential")
    authority = Client(f"http://127.0.0.1:{configuration['ports']['polyphony']}", authority_token)
    scope = handoff["scope"]

    def retained():
        snapshot = authority.request("GET", "/managed/snapshot", query=scope)
        children = authority.request("GET", "/managed/children", query=scope | {"attempt_id": handoff["attempt"]})
        require(digest(stable_evidence(snapshot, children)) == handoff["canonical_sha256"],
                "publication original canonical root/children/candidate changed")
        require(snapshot["attempt"][0]["id"] == handoff["attempt"]
                and snapshot["candidate"][0]["id"] == handoff["candidate"]
                and snapshot["attempt"][0]["captured_artifacts"] == handoff["root_artifacts"],
                "publication original root artifact references changed")
        root_capture = read_artifacts(authority, scope, handoff["root_artifacts"], sensitive)
        require(canonical(completed_verification(root_capture["log"])) == canonical(handoff["combined_verification"]),
                "publication original combined verification changed")
        require(canonical(single_runtime_execution(runtime_config, handoff["worker"], handoff["attempt"],
                                                   json.loads(root_capture["manifest"])))
                == canonical(handoff["verification_log"]), "publication original private verification log changed")
        child_patches = {}
        for item in children["children"]:
            child = item["child"]
            require(child["captured_artifacts"] == handoff["child_artifacts"].get(child["id"]),
                    "publication original child artifact references changed")
            capture = read_artifacts(authority, scope, child["captured_artifacts"], sensitive,
                                     attempt_id=handoff["attempt"], child_id=child["id"])
            configured_result(capture["log"], "PUBLICATION_CHILD_VERIFIED")
            child_patches[child["id"]] = capture["patch"]
        validate_task_patches(root_capture["patch"], child_patches)
        return True

    args.stage = "publication-original-authority"
    with owned_processes(root) as processes:
        processes.start("publication-authority", _command(bundle, "polyphony", configuration),
                        paths["polyphony"], authority_environment)
        eventually(retained, processes, "publication original captured authority", 60)
        # Candidate bytes have now been checked. They never supply config fields.
        policy = publisher_configuration(handoff, configuration, root)
        config_path = root / "publication.json"
        write_private_json(config_path, policy)
        environment = clean_environment({"POLYPHONY_PUBLICATION_READ_TOKEN": authority_token,
                                         "GITHUB_PUBLICATION_TOKEN": token})
        command = [str(bundle / "environments/openjules/bin/python"), "-m", "src.worker.managed_publication",
                   "--config", str(config_path), "--candidate", handoff["candidate"]]
        for child_id in handoff["children"]:
            command.extend(["--child", child_id])
        args.stage = "publication-first-call"
        first = validate_publication(run_json_process(processes, "publisher-first", command, paths["openjules"],
                                                      environment, PUBLISH_SECONDS + 30), handoff)
        require(retained(), "publication changed original canonical evidence")
        args.stage = "publication-same-journal-replay"
        second = validate_publication(run_json_process(processes, "publisher-replay", command, paths["openjules"],
                                                       environment, PUBLISH_SECONDS + 30), handoff)
        require(canonical(first) == canonical(second), "publication fresh-process replay changed its actual proposal")
        require(retained(), "publication replay changed original canonical evidence")
        args.stage = "publication-remote-verification"
        observer_environment = clean_environment({key: os.environ[key] for key in (
            "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_REF",
            "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")})
        observer_environment["GITHUB_PUBLICATION_TOKEN"] = token
        remote = run_json_process(processes, "publication-remote", [
            sys.executable, str(Path(__file__).resolve()), "observe", "--work-dir", str(root),
        ], paths["zpm"], observer_environment, 90)
        require(remote.get("exact_child_union") is True and remote.get("draft") is True
                and remote.get("proposal_number") == first["proposal"]["number"]
                and remote.get("commit_sha") == first["commit_sha"] and remote.get("tree_sha") == first["tree_sha"],
                "publication independent remote observation changed")
        verify(bundle)
    require(not processes.running, "publication authority or publisher process remains active")
    report = {"passed": True, "phase": "publication", "component_revisions": revisions,
              "run_id": identity["run_id"], "candidate": handoff["candidate"], "operation": first["operation"],
              "observed_forge": remote, "fresh_process_replay_identical": True,
              "combined_verification": handoff["combined_verification"], "private_verification_log": handoff["verification_log"],
              "inference": handoff["inference"],
              "all_publication_processes_reaped": True, "ready_for_review": False, "release_authorized": False,
              "proof": "actual GitHub draft and same-job original-journal fresh-process replay",
              "not_proven": ["live provider behavior", "independent PR review", "PR CI approval or completion",
                             "merge authorization", "cross-job or lost-runner recovery", "combined-test-failure case"]}
    require(not any(value in canonical(report) for value in sensitive), "publication report contains a credential")
    private_json(root / "publication-report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("phase", choices=("execute", "publish", "observe"))
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--openjules-checkout", type=Path)
    parser.add_argument("--polyphony-checkout", type=Path)
    parser.add_argument("--image")
    args = parser.parse_args(argv)
    args.stage = "identity"
    os.umask(0o077)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        if args.phase != "observe":
            require(args.openjules_checkout is not None and args.polyphony_checkout is not None,
                    "publication requires both pinned component checkouts")
        if args.phase == "execute":
            require(isinstance(args.image, str), "publication execution requires its fixed image")
        result = {"execute": execute, "publish": publish, "observe": observe}[args.phase](args)
    except (Exception, KeyboardInterrupt) as error:
        # Never serialize exception messages, raw provider output or private logs.
        result = {"passed": False, "phase": args.phase, "stage": args.stage,
                  "error": "qualification_failed", "next_action": "reconcile original private state and actual forge outcome",
                  "automatic_retry_authorized": False}
        observation = getattr(args, "execution_observation", None)
        if observation is not None:
            try:
                observation.capture(error, before_cleanup=False)
                failure = observation.report()
                if failure is not None:
                    result["failure_observation"] = failure
            except BaseException:
                pass
        if args.phase != "observe" and args.work_dir.is_dir() and not args.work_dir.is_symlink():
            private_json(args.work_dir / "failure-report.json", result)
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
