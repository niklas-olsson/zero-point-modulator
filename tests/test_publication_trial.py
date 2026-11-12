"""Deterministic negative oracles; no Docker, provider or forge qualification."""

from contextlib import chdir, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from zpm.controller import Controller
from zpm.models import Binding
from zpm.store import Store

CONTRACTS = Path(__file__).parent / "contracts"
sys.path.insert(0, str(CONTRACTS))
import check_publication_stack as trial
import publication_fixture as fixture


def environment():
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "niklas-olsson/zero-point-modulator",
        "GITHUB_REPOSITORY_ID": str(fixture.REPOSITORY_ID), "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
    }


def observed_fixture():
    return types.SimpleNamespace(
        root_turns=3, child_turns={"runbook": 2, "example": 2}, requests=[object()] * 7,
        errors=["publication tool result is missing, repeated or failed"],
        two_spawned=types.SimpleNamespace(is_set=lambda: True),
    )


def retained_capacity_plan(reservations=1, target=0):
    with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "state.sqlite3", "publication") as store:
        binding = Binding(fixture.REPOSITORY, "publication", max(reservations, 1))
        stored = store.enroll(binding)
        for _ in range(reservations):
            store.reserve(binding, stored["revision"])
        store.change(binding.repository, "target", target)
        return Controller(store).plan()


def handoff(root):
    identity = trial.workflow_identity(environment())
    artifacts = dict.fromkeys(("patch", "log", "manifest"), "c" * 64)
    children = ["child_" + value * 32 for value in ("1", "2")]
    return {
        "schema_version": 1, "phase": "captured_and_reaped", "identity": identity,
        "scope": {"deployment_id": "publication-123456", "repository": fixture.REPOSITORY},
        "revisions": {"zpm": identity["base"], "openjules": trial.OPENJULES_REVISION,
                      "polyphony": trial.POLYPHONY_REVISION},
        "image": "sha256:" + "b" * 64, "candidate": "d" * 64, "attempt": "attempt-original",
        "worker": "worker-original", "children": children, "canonical_sha256": "e" * 64,
        "root_artifacts": artifacts, "child_artifacts": {key: artifacts.copy() for key in children},
        "combined_verification": {"tests": 2, "expected_tests": 2, "elapsed_seconds": 0.1, "exit_code": 0},
        "verification_log": {"archive_sha256": "f" * 64, "log_sha256": "a" * 64, "log_bytes": 99},
        "inference": {"calls": 10, "child_calls": 4, "reserved_output_tokens": 10240},
        "bundle": str(root / "installation"), "source": str(root / "approved-source"),
    }


def publication(value):
    operation = trial.expected_operation(value)
    return {
        "schema_version": 1, "operation": operation, "status": "draft_published", "candidate_id": value["candidate"],
        "ready_for_review": False, "release_authorized": False, "manifest_sha256": "1" * 64,
        "tree_sha": "2" * 40, "commit_sha": "3" * 40,
        "proposal": {
            "number": 42, "url": "https://github.com/niklas-olsson/zero-point-modulator/pull/42",
            "state": "open", "draft": True, "head_sha": "3" * 40, "base_sha": value["identity"]["base"],
            "head_branch": "openjules/managed/qualification-123456/" + operation,
            "base_branch": "main", "repository_id": fixture.REPOSITORY_ID,
        },
    }


def patch_file(path, content):
    raw = content.encode()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    return ("diff --git a/" + path + " b/" + path + "\nnew file mode 100644\nindex "
            + "0" * 40 + ".." + sha + "\n--- /dev/null\n+++ b/" + path
            + "\n@@ -0,0 +1," + str(len(content.splitlines())) + " @@\n"
            + "".join("+" + line for line in content.splitlines(keepends=True)))


def fixed_patches():
    return {key: patch_file(fixture.FILES[key], content) for key, content in fixture.CONTENTS.items()}


def verification_log(record=None):
    record = record or {"tests": 2, "expected_tests": 2, "elapsed_seconds": 0.1, "exit_code": 0}
    return "\n".join(json.dumps(value) for value in [
        {"event": "publication_profile", "command_seconds": 120},
        {"event": "configured_verification", "state": "exited", "exit_code": 0,
         "output": "PUBLICATION_COMBINED_RESULT " + json.dumps(record) + "\n"},
    ])


class FakeForge:
    """Named response fixture, not an actual GitHub transport."""

    def __init__(self, value, result):
        self.calls = 0
        self.paths = []
        base, branch = value["identity"]["base"], result["proposal"]["head_branch"]
        original = [{"path": "README.md", "type": "blob", "mode": "100644", "sha": "4" * 40}]
        additions = [{"path": fixture.FILES[key], "type": "blob", "mode": "100644",
                      "sha": trial.git_blob_sha(content)} for key, content in fixture.CONTENTS.items()]
        marker = "<!-- openjules-publication:" + result["operation"] + ":" + result["manifest_sha256"] + " -->"
        self.values = {
            "": {"id": fixture.REPOSITORY_ID, "full_name": "niklas-olsson/zero-point-modulator", "default_branch": "main"},
            "/git/ref/heads/main": {"object": {"type": "commit", "sha": base}},
            "/git/ref/heads/" + trial.quote(branch, safe=""): {
                "ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": result["commit_sha"]}},
            "/git/commits/" + result["commit_sha"]: {
                "sha": result["commit_sha"], "tree": {"sha": result["tree_sha"]}, "parents": [{"sha": base}]},
            "/git/commits/" + base: {"sha": base, "tree": {"sha": "5" * 40}},
            "/git/trees/" + "5" * 40 + "?recursive=1": {"sha": "5" * 40, "truncated": False, "tree": original},
            "/git/trees/" + result["tree_sha"] + "?recursive=1": {
                "sha": result["tree_sha"], "truncated": False, "tree": original + additions},
            "/pulls/42": {
                "number": 42, "html_url": result["proposal"]["url"], "state": "open", "draft": True, "body": marker,
                "head": {"sha": result["commit_sha"], "ref": branch, "repo": {"id": fixture.REPOSITORY_ID}},
                "base": {"sha": base, "ref": "main", "repo": {"id": fixture.REPOSITORY_ID}},
            },
            "/pulls?state=all&head=" + trial.quote("niklas-olsson:" + branch, safe="") + "&per_page=100": [{"number": 42}],
        }

    def get(self, path):
        self.calls += 1
        self.paths.append(path)
        if path not in self.values:
            raise HTTPError("https://api.github.com/fixed-oracle", 404, "fixture missing", {}, None)
        return deepcopy(self.values[path])


class PublicationTrialTests(unittest.TestCase):
    def test_active_drain_uses_actual_controller_plan_and_canonical_closure(self):
        current = retained_capacity_plan()
        self.assertEqual(current["reserved"], 1)
        self.assertEqual(current["repositories"][0]["effective_target"], 0)
        self.assertNotIn("effective_workers", current["repositories"][0])
        read_children = Mock(return_value={"admission_closed": True})
        self.assertTrue(trial.active_drain_admission_closed(current, read_children))
        read_children.assert_called_once_with()

    def test_active_drain_skips_authority_until_capacity_matches(self):
        for reservations, target in ((0, 0), (2, 0), (1, 1)):
            with self.subTest(reservations=reservations, target=target):
                current = retained_capacity_plan(reservations, target)
                read_children = Mock(side_effect=AssertionError("premature authority read"))
                self.assertFalse(trial.active_drain_admission_closed(current, read_children))
                read_children.assert_not_called()

    def test_active_drain_requires_exact_canonical_closure(self):
        current = retained_capacity_plan()
        for response in ({}, {"admission_closed": False}, {"admission_closed": None},
                         {"admission_closed": 1}, {"admission_closed": "true"}):
            with self.subTest(response=response):
                read_children = Mock(return_value=response)
                self.assertFalse(trial.active_drain_admission_closed(current, read_children))
                read_children.assert_called_once_with()

    def test_active_drain_preserves_authority_failure(self):
        current = retained_capacity_plan()
        original = TimeoutError("authority unavailable")
        read_children = Mock(side_effect=original)
        with self.assertRaises(TimeoutError) as caught:
            trial.active_drain_admission_closed(current, read_children)
        self.assertIs(caught.exception, original)
        read_children.assert_called_once_with()

    def test_failure_observation_success_preserves_one_call_and_result(self):
        observation = trial.ExecutionFailureObservation()
        observation.operation = "active-worker"
        observation.model = observed_fixture()
        result, argument = object(), object()
        with patch.object(trial.Client, "request", return_value=result) as request:
            client = trial.Client("http://127.0.0.1:1", "private-token")
            with observation.record():
                actual = client.request("GET", "/private-worker", query=argument)
            self.assertIs(actual, result)
            request.assert_called_once_with("GET", "/private-worker", query=argument)
        self.assertIsNone(observation.report())

    def test_failure_observation_freezes_before_release_and_owned_cleanup(self):
        observation = trial.ExecutionFailureObservation()
        observation.operation = "active-worker"
        observation.model = model = observed_fixture()
        original = TimeoutError("private request and credential")
        processes = types.SimpleNamespace(running={"owned": object()}, handles=[])
        def failed_cleanup(*_):
            model.root_turns = 6
            model.errors[:] = ["private cleanup text"]
            raise OSError("private cleanup path")
        with patch.object(trial, "Processes", return_value=processes), \
             patch("check_authority_admission.stop_owned", side_effect=failed_cleanup) as cleanup:
            with self.assertRaises(TimeoutError) as caught:
                with trial.owned_processes(Path("/unused")), observation.record():
                    try:
                        with observation.record():
                            raise original
                    finally:
                        model.child_turns["runbook"] = 0
        self.assertIs(caught.exception, original)
        cleanup.assert_called_once_with(processes, "owned")
        expected = {
            "operation": "active-worker", "exception_kind": "timeout", "timing": "before-owned-cleanup",
            "fixture": {"available": True, "root_turns": 3, "child_turns": {"runbook": 2, "example": 2},
                        "accepted_requests": 7, "two_spawned": True, "error_observed": True,
                        "first_error": "tool-result"},
        }
        self.assertEqual(observation.report(), expected)
        observation.operation = "execution-cleanup"
        observation.capture(OSError("later private error"), before_cleanup=False)
        report = observation.report()
        report["fixture"]["child_turns"]["runbook"] = 99
        self.assertEqual(observation.report(), expected)

    def test_failure_observation_redacts_unknown_types_text_and_invalid_scalars(self):
        class PrivateExceptionName(Exception):
            def __str__(self):
                raise AssertionError("diagnostic must not stringify this private exception")
        class PrivateText(str):
            pass
        secret = "private-credential-path-payload"
        observation = trial.ExecutionFailureObservation()
        observation.operation = secret
        observation.model = types.SimpleNamespace(
            root_turns=True, child_turns={"runbook": 3, "example": secret, secret: 2},
            requests=[secret] * 11, errors=[PrivateText(secret), "publication fixture policy mismatch"],
            two_spawned=types.SimpleNamespace(is_set=lambda: secret),
        )
        original = PrivateExceptionName(secret)
        with self.assertRaises(PrivateExceptionName) as caught:
            with observation.record():
                raise original
        self.assertIs(caught.exception, original)
        report = observation.report()
        self.assertEqual((report["operation"], report["exception_kind"]), ("other", "other"))
        self.assertEqual(report["fixture"], {
            "available": True, "root_turns": None, "child_turns": {"runbook": None, "example": None},
            "accepted_requests": None, "two_spawned": None, "error_observed": True, "first_error": "other",
        })
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn("PrivateExceptionName", json.dumps(report))

    def test_failure_observation_uses_only_fixed_fixture_reasons_and_bounded_counts(self):
        observation = trial.ExecutionFailureObservation()
        model = observation.model = observed_fixture()
        for text, reason in observation.FIXTURE_ERRORS.items():
            model.errors = [text, "private second error"]
            with self.subTest(reason=reason):
                self.assertEqual(observation.fixture_observation()["first_error"], reason)
        for unknown in ("private first error", ValueError("private error"), None, 1):
            model.errors = [unknown]
            self.assertEqual(observation.fixture_observation()["first_error"], "other")
        model.errors = []
        self.assertEqual((observation.fixture_observation()["error_observed"],
                          observation.fixture_observation()["first_error"]), (False, None))
        for value in (-1, 7, True, 1.0, "private", None):
            model.root_turns = value
            with self.subTest(root=value):
                self.assertIsNone(observation.fixture_observation()["root_turns"])
        model.root_turns, model.child_turns, model.requests, model.errors = 6, [], "private", "private"
        report = observation.fixture_observation()
        self.assertEqual(report["root_turns"], 6)
        self.assertEqual(report["child_turns"], {"runbook": None, "example": None})
        self.assertIsNone(report["accepted_requests"])
        self.assertIsNone(report["error_observed"])
        self.assertEqual(report["first_error"], "other")

    def test_failure_observation_cannot_mask_original_when_snapshot_fails(self):
        original = ValueError("original private failure")
        observation = trial.ExecutionFailureObservation()
        observation.operation = "two-child-grants"
        observation.model = object()
        with self.assertRaises(ValueError) as caught:
            with observation.record():
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(observation.report()["fixture"], {"available": False})
        with patch.object(observation, "capture", side_effect=RuntimeError("broken diagnostic")):
            with self.assertRaises(ValueError) as caught:
                with observation.record():
                    raise original
        self.assertIs(caught.exception, original)

    def test_failure_observation_exception_categories_are_fixed(self):
        errors = [
            (AssertionError("private"), "assertion"), (TimeoutError("private"), "timeout"),
            (subprocess.TimeoutExpired(["private"], 3), "timeout"),
            (HTTPError("private", 500, "private", {}, None), "http"),
            (trial.URLError("private"), "url"), (ConnectionError("private"), "connection"),
            (OSError("private"), "os"), (json.JSONDecodeError("private", "private", 0), "json"),
            (subprocess.CalledProcessError(1, ["private"]), "process-exit"),
            (ValueError("private"), "value"), (KeyError("private"), "key"),
            (TypeError("private"), "type"), (KeyboardInterrupt("private"), "interrupted"),
            (RuntimeError("private"), "other"),
        ]
        for error, kind in errors:
            with self.subTest(kind=kind):
                self.assertEqual(trial.ExecutionFailureObservation.exception_kind(error), kind)

    def test_failure_report_cleanup_only_is_explicit_and_contains_no_fixture_values(self):
        def fail(args):
            args.stage = "root-and-children"
            args.execution_observation = observation = trial.ExecutionFailureObservation()
            observation.model = observed_fixture()
            observation.operation = "execution-cleanup"
            raise OSError("private cleanup path and token")
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(trial, "execute", side_effect=fail) as execute, \
             patch.object(trial.os, "umask"), redirect_stdout(io.StringIO()) as output:
            root = Path(temporary)
            result = trial.main(["execute", "--work-dir", temporary, "--openjules-checkout", temporary,
                                 "--polyphony-checkout", temporary, "--image", "fixed-test-image"])
            self.assertEqual(result, 1)
            execute.assert_called_once()
            report = json.loads(output.getvalue())
            self.assertEqual(json.loads((root / "failure-report.json").read_text()), report)
        self.assertEqual(report["failure_observation"], {
            "operation": "execution-cleanup", "exception_kind": "os", "timing": "after-cleanup-attempt", "fixture": None,
        })
        self.assertIs(report["automatic_retry_authorized"], False)
        self.assertNotIn("private cleanup", json.dumps(report))

    def test_dispatch_identity_refuses_rerun_or_other_repository_base(self):
        valid = environment()
        self.assertEqual(trial.workflow_identity(valid)["repository_id"], 1453104676)
        for name, value in {
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": "other/repository",
            "GITHUB_REPOSITORY_ID": "1", "GITHUB_REF": "refs/heads/feature",
            "GITHUB_SHA": "short", "GITHUB_RUN_ID": "../foreign", "GITHUB_RUN_ATTEMPT": "2",
        }.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                trial.workflow_identity(valid | {name: value})

    def test_private_handoff_is_create_once_bounded_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "handoff.json"
            trial.write_private_json(path, {"original": True})
            self.assertEqual(trial.read_private_json(path), {"original": True})
            with self.assertRaises(FileExistsError):
                trial.write_private_json(path, {"changed": True})
            path.chmod(0o644)
            with self.assertRaises(AssertionError):
                trial.read_private_json(path)
            path.chmod(0o600)
            with self.assertRaises(AssertionError):
                trial.read_private_json(path, 2)
            alias = root / "alias"
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                trial.read_private_json(alias)

    def test_handoff_rejects_changed_identity_scope_budget_or_children(self):
        root = Path("/private/publication-fixture")
        original = handoff(root)
        self.assertEqual(trial.validate_handoff(original, original["identity"], root), original)
        mutations = [
            ("phase", "executing"), ("schema_version", True), ("image", "mutable:tag"),
            ("candidate", "f" * 40), ("bundle", "/another/installation"),
            ("children", [original["children"][0]] * 2), ("child_artifacts", []),
            ("revisions", original["revisions"] | {"openjules": "0" * 40}),
            ("scope", original["scope"] | {"repository": "github:github.com/other/repository"}),
            ("inference", original["inference"] | {"calls": 10.0}),
            ("verification_log", original["verification_log"] | {"log_bytes": True}),
        ]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises((AssertionError, TypeError)):
                trial.validate_handoff(original | {key: value}, original["identity"], root)

    def test_real_git_new_file_patches_match_exact_union(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*arguments):
                return subprocess.check_output(
                    ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *arguments],
                    env=trial.clean_environment(), text=True, stderr=subprocess.STDOUT, timeout=10)
            git("init", "-q")
            (root / "README.md").write_text("approved base\n")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
            for key, content in fixture.CONTENTS.items():
                path = root / fixture.FILES[key]
                path.parent.mkdir(exist_ok=True)
                path.write_text(content)
            git("add", "--all")
            root_patch = git("diff", "--cached", "--binary", "HEAD")
            children = {key: git("diff", "--cached", "--binary", "HEAD", "--", path) for key, path in fixture.FILES.items()}
            result = trial.validate_task_patches(root_patch, children)
            self.assertEqual(set(result), set(fixture.FILES.values()))

    def test_example_cannot_select_host_endpoint_path_credential_or_destination(self):
        original = fixed_patches()
        mutations = [
            ("authority", "endpoint", "http://127.0.0.1:9999"),
            ("authority", "token_ref", "env:ATTACKER_TOKEN"),
            ("forge", "repository_id", 1), ("forge", "repository", "github:github.com/other/repository"),
            ("forge", "target_branch", "foreign"), ("forge", "token_ref", "env:OTHER_FORGE_TOKEN"),
            (None, "source_path", "/foreign/source"), (None, "state_directory", "/foreign/state"),
            (None, "base_commit", "0" * 40),
        ]
        for section, key, value in mutations:
            candidate = deepcopy(fixture.EXAMPLE)
            (candidate[section] if section else candidate)[key] = value
            changed = original | {"example": patch_file(fixture.FILES["example"], json.dumps(candidate, indent=2, sort_keys=True) + "\n")}
            with self.subTest(section=section, key=key), self.assertRaises(AssertionError):
                trial.validate_task_patches("".join(changed.values()), changed)
        root = Path("/private/publication-fixture")
        value = handoff(root)
        policy = trial.publisher_configuration(value, {"ports": {"polyphony": 8766}}, root)
        self.assertEqual(policy["source_path"], value["source"])
        self.assertEqual(policy["authority"], {"endpoint": "http://127.0.0.1:8766", "token_ref": "env:POLYPHONY_PUBLICATION_READ_TOKEN"})
        self.assertEqual(policy["forge"]["repository_id"], 1453104676)
        self.assertEqual(policy["forge"]["deployment_id"], value["scope"]["deployment_id"])
        self.assertEqual(policy["base_commit"], value["identity"]["base"])
        self.assertEqual(fixture.EXAMPLE["base_commit"], "REPLACE_WITH_FULL_APPROVED_BASE_SHA")

    def test_patch_union_rejects_overlap_extra_effects_and_modes(self):
        original = fixed_patches()
        valid = "".join(original.values())
        trial.validate_task_patches(valid, original)
        changes = [
            (valid, {"one": original["runbook"], "two": original["runbook"]}),
            (original["runbook"], original),
            ("preamble\n" + valid, original),
            (valid.replace("new file mode 100644", "new file mode 100755", 1), original),
            (valid + patch_file("unrequested.txt", "extra\n"), original),
            (valid.replace("+# Managed publication qualification", "+# Changed"), original),
        ]
        for root_patch, children in changes:
            with self.subTest(root=root_patch[:40]), self.assertRaises(AssertionError):
                trial.validate_task_patches(root_patch, children)

    def test_combined_verification_requires_full_count_success_and_complete_event(self):
        valid = verification_log()
        self.assertEqual(trial.completed_verification(valid)["tests"], 2)
        mutations = [
            valid + "\n" + valid, valid.replace('"command_seconds": 120', '"command_seconds": true'),
            valid.replace('"state": "exited"', '"state": "timed_out"'),
            valid.replace('"exit_code": 0', '"exit_code": 1'),
            valid.replace("PUBLICATION_COMBINED_RESULT", "TRUNCATED_RESULT"),
            verification_log({"tests": 1, "expected_tests": 2, "elapsed_seconds": 0.1, "exit_code": 0}),
            verification_log({"tests": True, "expected_tests": 1, "elapsed_seconds": 0.1, "exit_code": 0}),
            verification_log({"tests": 2, "expected_tests": 2, "elapsed_seconds": float("nan"), "exit_code": 0}),
        ]
        for value in mutations:
            with self.subTest(value=value[:70]), self.assertRaises(AssertionError):
                trial.completed_verification(value)

    def test_trusted_loader_delegates_main_and_selects_only_command_bound(self):
        observed = []
        class Local:
            def __init__(self, root, *, command_seconds=30, output_bytes=65536):
                observed.append((root, command_seconds, output_bytes))
        legacy, controls = types.SimpleNamespace(LocalTools=Local), object()
        module = types.ModuleType("_publication_root_image")
        def original(name):
            observed.append(name)
            return legacy if name == "managed_runner" else controls
        module._load_sibling = original
        def existing_main(arguments):
            self.assertEqual(arguments, ["--socket", "/approved/model.sock"])
            self.assertIs(module._load_sibling("child_tools"), controls)
            loaded = module._load_sibling("managed_runner")
            loaded.LocalTools("approved-root")
            return 73
        module.main = existing_main
        spec = types.SimpleNamespace(name=module.__name__, loader=types.SimpleNamespace(exec_module=lambda value: None))
        def select(name, path):
            self.assertEqual((name, path), ("_publication_root_image", "/opt/openjules/managed_root_runner.py"))
            return spec
        output = io.StringIO()
        with patch("importlib.util.spec_from_file_location", side_effect=select), \
             patch("importlib.util.module_from_spec", return_value=module), \
             patch.object(sys, "argv", ["fixed-loader", "--socket", "/approved/model.sock"]), \
             patch.dict(sys.modules), redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                exec(fixture.ROOT_LAUNCHER, {})
        self.assertEqual(raised.exception.code, 73)
        self.assertEqual(observed, ["child_tools", "managed_runner", ("approved-root", 120, 65536)])
        self.assertEqual(json.loads(output.getvalue()), {"event": "publication_profile", "command_seconds": 120})

    def test_whole_suite_command_runs_tests_once_and_keeps_bounded_summary(self):
        # UID/filesystem checks are named fixtures here. This runs real unittest
        # discovery and a real subprocess, not real managed confinement.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for key, content in fixture.CONTENTS.items():
                path = root / fixture.FILES[key]
                path.parent.mkdir(exist_ok=True)
                path.write_text(content)
            module_name = "test_publication_small_" + root.name.replace("-", "_")
            test_file = root / (module_name + ".py")
            test_file.write_text(
                "import unittest\nfrom pathlib import Path\nclass Actual(unittest.TestCase):\n"
                " def test_one(self):\n  Path('calls').open('a').write('one\\n')\n  print('\\u6f22'*3000)\n"
                " def test_two(self):\n  Path('calls').open('a').write('two\\n')\n")
            actual_open, actual_exists = open, Path.exists
            private_log = root / "private-suite.log"
            def controlled_open(path, *args, **kwargs):
                return actual_open(private_log if str(path) == "/workspace/run/publication-unittest.log" else path, *args, **kwargs)
            def boundary_exists(path):
                return False if str(path) in {"/var/run/docker.sock", "/source/.git"} else actual_exists(path)
            output = io.StringIO()
            before_path = list(sys.path)
            try:
                with chdir(root), patch("os.getuid", return_value=65532), patch("os.access", return_value=False), \
                     patch.object(Path, "exists", boundary_exists), patch("builtins.open", side_effect=controlled_open), \
                     patch.dict(os.environ, trial.clean_environment(), clear=True), redirect_stdout(output):
                    exec(fixture.ROOT_VERIFY[-1], {})
                self.assertEqual((root / "calls").read_text().splitlines(), ["one", "two"])
                self.assertGreater(private_log.stat().st_size, 6000)
                self.assertLess(len(output.getvalue().encode("utf-8")), 5000)
                record = json.loads(output.getvalue().split("PUBLICATION_COMBINED_RESULT ", 1)[1])
                self.assertEqual((record["expected_tests"], record["tests"], record["exit_code"]), (2, 2, 0))
                test_file.write_text("import unittest\nclass Actual(unittest.TestCase):\n def test_failed(self): self.fail('bounded fixture failure')\n")
                sys.modules.pop(module_name, None)
                with chdir(root), patch("os.getuid", return_value=65532), patch("os.access", return_value=False), \
                     patch.object(Path, "exists", boundary_exists), patch("builtins.open", side_effect=controlled_open), \
                     patch.dict(os.environ, trial.clean_environment(), clear=True), redirect_stdout(io.StringIO()):
                    with self.assertRaises(AssertionError):
                        exec(fixture.ROOT_VERIFY[-1], {})
                self.assertIn("bounded fixture failure", private_log.read_text())
            finally:
                sys.path[:] = before_path
                sys.modules.pop(module_name, None)

    def test_empty_bookkeeping_does_not_prove_manager_or_model_shutdown(self):
        processes = types.SimpleNamespace(running={})
        stopped_model = types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: False))
        for code in (-signal.SIGKILL, 0, 1, None):
            with self.subTest(code=code), self.assertRaises(AssertionError):
                trial.require_execution_reaped(processes, types.SimpleNamespace(poll=lambda: code), stopped_model)
        trial.require_execution_reaped(processes, types.SimpleNamespace(poll=lambda: 130), stopped_model)
        with self.assertRaises(AssertionError):
            trial.require_execution_reaped(processes, types.SimpleNamespace(poll=lambda: 130),
                                          types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True)))

    def test_bounded_child_json_reaps_actual_timeout_and_rejects_non_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with trial.owned_processes(root) as processes:
                result = trial.run_json_process(processes, "valid", [sys.executable, "-c", "print('{\"ok\":true}')"],
                                                root, trial.clean_environment(), 5)
                self.assertEqual(result, {"ok": True})
                with self.assertRaises(subprocess.TimeoutExpired):
                    trial.run_json_process(processes, "timeout", [sys.executable, "-c", "import time;time.sleep(60)"],
                                           root, trial.clean_environment(), 0.05)
                self.assertEqual(processes.running, {})
                with self.assertRaises(json.JSONDecodeError):
                    trial.run_json_process(processes, "invalid", [sys.executable, "-c", "print('not-json')"],
                                           root, trial.clean_environment(), 5)
                self.assertEqual(processes.running, {})

    def test_owned_cleanup_preserves_original_failure_when_reaping_fails(self):
        processes = types.SimpleNamespace(running={"owned": object()}, handles=[])
        with patch.object(trial, "Processes", return_value=processes), \
             patch("check_authority_admission.stop_owned", side_effect=OSError("private cleanup details")):
            with self.assertRaisesRegex(ValueError, "original") as raised:
                with trial.owned_processes(Path("/unused")):
                    raise ValueError("original")
        self.assertIn("could not reap", " ".join(raised.exception.__notes__))

    def test_runtime_history_and_complete_suite_archive_are_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "runtime"
            (directory / "captures").mkdir(parents=True)
            database = directory / "journal.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    "CREATE TABLE workers(worker_id TEXT); CREATE TABLE attempts(worker_id TEXT,attempt_id TEXT,exit_code INTEGER);"
                    "INSERT INTO workers VALUES('worker'); INSERT INTO attempts VALUES('worker','attempt',0);")
            log = b"complete private unittest log\n"
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                item = tarfile.TarInfo("workspace/run/publication-unittest.log")
                item.size = len(log)
                archive.addfile(item, io.BytesIO(log))
            content = output.getvalue()
            archive_path = directory / "captures" / (hashlib.sha256(b"worker").hexdigest() + ".tar")
            archive_path.write_bytes(content)
            configuration = {"state_directory": str(root), "profiles": [{"max_archive_bytes": 100000}]}
            manifest = {"archive_sha256": hashlib.sha256(content).hexdigest()}
            before = database.read_bytes()
            result = trial.single_runtime_execution(configuration, "worker", "attempt", manifest)
            self.assertEqual(result["log_sha256"], hashlib.sha256(log).hexdigest())
            self.assertEqual(database.read_bytes(), before)
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO workers VALUES('unexpected')")
            with self.assertRaises(AssertionError):
                trial.single_runtime_execution(configuration, "worker", "attempt", manifest)
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM workers WHERE worker_id='unexpected'")
            archive_path.write_bytes(content + b"changed")
            with self.assertRaises(AssertionError):
                trial.single_runtime_execution(configuration, "worker", "attempt", manifest)

    def test_publication_summary_refuses_ready_closed_ambiguous_or_foreign_results(self):
        value = handoff(Path("/private/fixture"))
        result = publication(value)
        trial.validate_publication(result, value)
        for key, changed in {"status": "existing_ready", "ready_for_review": 0, "release_authorized": 0,
                             "operation": "f" * 64, "schema_version": True}.items():
            with self.subTest(key=key), self.assertRaises(AssertionError):
                trial.validate_publication(result | {key: changed}, value)
        for key, changed in {"state": "closed", "draft": 1, "repository_id": 1,
                             "base_sha": "0" * 40, "head_sha": "0" * 40, "head_branch": "other",
                             "url": "https://example.invalid/42"}.items():
            with self.subTest(proposal=key), self.assertRaises(AssertionError):
                trial.validate_publication(result | {"proposal": result["proposal"] | {key: changed}}, value)

    def test_actual_remote_oracle_requires_feature_ref_exact_tree_and_draft(self):
        value = handoff(Path("/private/fixture"))
        result = publication(value)
        reader = FakeForge(value, result)
        report = trial.verify_remote(reader, value, result)
        self.assertEqual(reader.calls, 9)
        self.assertTrue(report["exact_child_union"])
        branch_path = "/git/ref/heads/" + trial.quote(result["proposal"]["head_branch"], safe="")
        for replacement in (
            None, {"ref": "refs/heads/foreign", "object": {"type": "commit", "sha": result["commit_sha"]}},
            {"ref": "refs/heads/" + result["proposal"]["head_branch"], "object": {"type": "commit", "sha": "9" * 40}},
        ):
            changed = FakeForge(value, result)
            if replacement is None:
                del changed.values[branch_path]
            else:
                changed.values[branch_path] = replacement
            with self.subTest(branch=replacement), self.assertRaises((AssertionError, HTTPError)):
                trial.verify_remote(changed, value, result)
        tree_path = "/git/trees/" + result["tree_sha"] + "?recursive=1"
        for mutation in ("extra", "content", "truncated", "mode", "base", "provenance", "duplicate", "ready"):
            changed = FakeForge(value, result)
            if mutation == "extra":
                changed.values[tree_path]["tree"].append({"path": "extra", "type": "blob", "mode": "100644", "sha": "9" * 40})
            elif mutation == "content":
                changed.values[tree_path]["tree"][-1]["sha"] = "9" * 40
            elif mutation == "truncated":
                changed.values[tree_path]["truncated"] = True
            elif mutation == "mode":
                changed.values[tree_path]["tree"][-1]["mode"] = "120000"
            elif mutation == "base":
                changed.values["/git/ref/heads/main"]["object"]["sha"] = "9" * 40
            elif mutation == "provenance":
                changed.values["/pulls/42"]["body"] = "unrelated"
            elif mutation == "ready":
                changed.values["/pulls/42"]["draft"] = False
            else:
                key = next(path for path in changed.values if path.startswith("/pulls?"))
                changed.values[key].append({"number": 43})
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                trial.verify_remote(changed, value, result)

    def test_forge_observer_is_read_only_fixed_host_and_byte_request_bounded(self):
        requests = []
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                requests.append((request.full_url, request.get_method(), timeout, request.headers))
                return Response(b'{"id":1453104676}')
        reader = trial.ForgeReader("test-only-token")
        reader.opener = Opener()
        self.assertEqual(reader.get("")["id"], 1453104676)
        self.assertEqual(requests[0][:3], ("https://api.github.com/repos/niklas-olsson/zero-point-modulator", "GET", 10))
        self.assertIsNone(trial.RejectRedirect().redirect_request(None))
        with patch.object(trial, "MAX_API", 2), self.assertRaises(AssertionError):
            reader.get("/bounded")
        reader.calls = 9
        count = len(requests)
        with self.assertRaises(AssertionError):
            reader.get("/tenth")
        self.assertEqual(len(requests), count)

    def test_exact_fixture_delegation_and_composition_use_ten_calls(self):
        import threading
        def transport_fixture(model):
            model.requests, model.errors, model.lock = [], [], threading.Lock()
        names = {"read_file", "write_file", "list_files", "run_command", "finish"}
        root_names = names | {"spawn_child", "child_status", "child_result", "cancel_child", "close_children"}
        def payload(instructions, results, tools):
            return {"model": fixture.MODEL, "max_tokens": 1024, "stream": False, "tool_choice": "auto",
                    "messages": [{"role": "system", "content": "fixture"},
                                 {"role": "user", "content": instructions}] + results,
                    "tools": [{"function": {"name": name}} for name in sorted(tools)]}
        def result(call, content):
            return {"role": "tool", "tool_call_id": call["id"], "content": content}
        def call(model, instructions, results, tools):
            return model.reply(payload(instructions, results, tools))["tool_calls"][0]
        with patch.dict(os.environ, trial.clean_environment(), clear=True), \
             patch("publication_fixture.ModelFixture.__init__", transport_fixture):
            model = fixture.PublicationFixture()
        model.release_root.set()
        patches = fixed_patches()
        roots = []
        identities = dict(zip(fixture.FILES, ("child_" + "1" * 32, "child_" + "2" * 32)))
        for key in fixture.FILES:
            spawned = call(model, fixture.REQUIREMENTS, roots, root_names)
            arguments = json.loads(spawned["function"]["arguments"])
            self.assertEqual(spawned["function"]["name"], "spawn_child")
            self.assertEqual((arguments["seconds"], arguments["max_turns"]), (90, 2))
            roots.append(result(spawned, json.dumps({"child_id": identities[key]})))
            children = []
            first = call(model, arguments["instructions"], children, names)
            self.assertEqual(json.loads(first["function"]["arguments"]),
                             {"path": fixture.FILES[key], "content": fixture.CONTENTS[key]})
            children.append(result(first, "written"))
            finished = call(model, arguments["instructions"], children, names)
            self.assertEqual(finished["function"]["name"], "finish")
        for key in fixture.FILES:
            requested = call(model, fixture.REQUIREMENTS, roots, root_names)
            self.assertEqual(requested["function"]["name"], "child_result")
            text = patches[key]
            sha = hashlib.sha256(text.encode()).hexdigest()
            roots.append(result(requested, json.dumps({
                "child_id": identities[key], "kind": "patch", "offset": 0,
                "total_bytes": len(text.encode()), "content": text,
                "content_sha256": sha, "artifact_sha256": sha})))
        composition = call(model, fixture.REQUIREMENTS, roots, root_names)
        arguments = json.loads(composition["function"]["arguments"])["argv"]
        self.assertEqual(composition["function"]["name"], "run_command")
        self.assertTrue(all(isinstance(arg, str) and len(arg) <= 8192 for arg in arguments))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, timeout=10)
            combined = subprocess.run(arguments, cwd=root, env=trial.clean_environment(),
                                      check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(combined.stdout.strip(), "PUBLICATION_CHILD_UNION_COMPOSED")
            self.assertEqual({path: (root / path).read_text() for path in fixture.FILES.values()},
                             {fixture.FILES[key]: value for key, value in fixture.CONTENTS.items()})
        roots.append(result(composition, json.dumps({"state": "exited", "exit_code": 0, "output": combined.stdout})))
        finish = call(model, fixture.REQUIREMENTS, roots, root_names)
        self.assertEqual(finish["function"]["name"], "finish")
        self.assertEqual((model.root_turns, model.child_turns, len(model.requests), model.errors),
                         (6, {"runbook": 2, "example": 2}, 10, []))

    def test_forge_env_cannot_construct_model_or_enter_execution_phase(self):
        with patch.dict(os.environ, {"GITHUB_PUBLICATION_TOKEN": "test-only-token"}), \
             patch("publication_fixture.ModelFixture.__init__", side_effect=AssertionError("server must not start")) as start:
            with self.assertRaisesRegex(AssertionError, "must not receive"):
                fixture.PublicationFixture()
            start.assert_not_called()
            with self.assertRaisesRegex(AssertionError, "must not receive"):
                trial.execute(types.SimpleNamespace())
