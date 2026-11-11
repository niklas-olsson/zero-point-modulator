"""Negative checks for isolation evidence; these do not run Docker."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_isolation_stack import (
        aggregate_diagnostics, checkpoint_progress, live_ids,
        require_peer_runtime, require_peer_unchanged,
    )
finally:
    sys.path.pop(0)


class IsolationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.scope = {"deployment_id": "deployment-b", "repository": "github:github.com/trial/b"}
        self.plan = {
            "deployment_id": self.scope["deployment_id"],
            "repositories": [{"repository": self.scope["repository"], "desired_workers": 1,
                              "effective_target": 1, "enabled": True, "paused": False, "reserved": 1,
                              "workers": [{**self.scope, "worker_id": "worker-b", "admission_open": True, "state": "busy"}]}],
        }
        self.snapshot = {**self.scope, "task": [{"id": "task-b"}],
                         "attempt": [{"id": "attempt-b", "task": "task-b", "termination_confirmed": False,
                                      "runtime_binding": {"worker_id": "worker-b"}}]}

    def test_equal_capacity_cannot_hide_peer_identity_or_authority_changes(self):
        require_peer_unchanged(self.plan, self.snapshot, self.scope, "worker-b", "task-b", "attempt-b")
        cases = [
            ("worker replacement", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(worker_id="replacement")),
            ("wrong worker scope", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(repository="github:github.com/trial/a")),
            ("wrong authority scope", lambda plan, snapshot: snapshot.update(deployment_id="deployment-a")),
            ("changed desired target", lambda plan, snapshot: plan["repositories"][0].update(desired_workers=2)),
            ("closed admission", lambda plan, snapshot: plan["repositories"][0]["workers"][0].update(admission_open=False)),
            ("replaced attempt", lambda plan, snapshot: snapshot["attempt"][0].update(id="replacement")),
            ("foreign task", lambda plan, snapshot: snapshot["task"].append({"id": "task-a"})),
            ("already terminated", lambda plan, snapshot: snapshot["attempt"][0].update(termination_confirmed=True)),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                plan, snapshot = deepcopy(self.plan), deepcopy(self.snapshot)
                mutate(plan, snapshot)
                with self.assertRaises(AssertionError):
                    require_peer_unchanged(plan, snapshot, self.scope, "worker-b", "task-b", "attempt-b")

    def test_duplicate_or_unbound_worker_ids_cannot_establish_distinct_capacity(self):
        duplicate = deepcopy(self.plan)
        duplicate["repositories"][0]["workers"] *= 2
        with self.assertRaises(AssertionError):
            live_ids(duplicate)
        for identity in (None, ""):
            with self.subTest(identity=identity):
                unbound = deepcopy(self.plan)
                unbound["repositories"][0]["workers"][0]["worker_id"] = identity
                with self.assertRaises(AssertionError):
                    live_ids(unbound)

    def test_each_held_phase_requires_progress_after_its_fresh_baseline(self):
        processes = SimpleNamespace(check=lambda: None)
        checked = []

        def bounded_observation(predicate, *_):
            for _ in range(3):
                value = predicate()
                if value:
                    return value
            raise AssertionError("counter did not advance in the held phase")

        with patch("check_isolation_stack.eventually", side_effect=bounded_observation):
            # It may be greater than any prior phase's counter, but a fixed
            # counter after entering this phase is not fresh progress.
            with self.assertRaises(AssertionError):
                checkpoint_progress(lambda: 100, lambda: checked.append("fresh"), processes)
            self.assertEqual(checked, ["fresh"])
            checked.clear()
            values = iter([100, 100, 101])
            self.assertEqual(checkpoint_progress(lambda: next(values), lambda: checked.append("fresh"), processes),
                             {"before": 100, "after": 101})
            self.assertEqual(checked, ["fresh", "fresh"])

    def test_fresh_runtime_admission_and_attempt_cannot_hide_behind_cached_plan(self):
        original = {**self.scope, "worker_id": "worker-b", "state": "busy", "admission_open": True,
                    "active_executions": ["attempt-b"], "termination_confirmed": False}
        require_peer_runtime(original, self.scope, "worker-b", "attempt-b")
        for changed in ({"worker_id": "replacement"}, {"repository": "github:github.com/trial/a"},
                        {"admission_open": False}, {"active_executions": []},
                        {"active_executions": ["other-attempt"]}, {"termination_confirmed": True}):
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                require_peer_runtime(original | changed, self.scope, "worker-b", "attempt-b")

    def test_aggregate_diagnostics_redact_both_installations_and_cut_tokens(self):
        sensitive = {"credential-a-" + "A" * 48, "credential-b-" + "B" * 48, "synthetic-secret-" + "S" * 48}
        credentials = sorted(sensitive)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            installations = []
            for label in ("a", "b"):
                private = root / label
                private.mkdir()
                installations.append(SimpleNamespace(label=label, root=private))
                # The first token crosses the log-tail cutoff. Both other
                # credentials occur later, including the sibling's credential.
                tail = ("tail " + credentials[1] + " " + credentials[2]).encode()
                data = b"prefix " + credentials[0].encode() + b"x" * (20000 - len(tail) - 20) + tail
                (private / "openjules.log").write_bytes(data)
            report = aggregate_diagnostics(root, installations, "failure " + credentials[0], [], {"zpm": "revision"}, sensitive)
            encoded = json.dumps(report)
            for credential in credentials:
                self.assertNotIn(credential, encoded)
            self.assertIn("[redacted]", encoded)
            self.assertEqual(set(report["installations"]), {"a", "b"})
            for installation in report["installations"].values():
                self.assertLessEqual(len(installation["logs"]["openjules"]), 20000)
                self.assertFalse(installation["logs"]["openjules"].startswith("A"))
            self.assertEqual(json.loads((root / "diagnostics.json").read_text()), report)
            self.assertEqual((root / "diagnostics.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
