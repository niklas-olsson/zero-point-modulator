from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from zpm.controller import Controller
from zpm.models import Binding, ConfigurationError, ProviderConfig, released, repository
from zpm.provider import ProviderError
from zpm.store import Store

from .support import FixtureProvider


def spec(name="owner/project", count=2):
    return {"schema_version": 1, "repository": name, "profile": "fixture", "desired_workers": count}


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.db"
        self.store = Store(self.path, "deployment-a")
        self.provider = FixtureProvider()
        self.controller = Controller(self.store, self.provider, max_workers=20)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def reopen(self):
        self.store.close()
        self.store = Store(self.path, "deployment-a")
        self.controller = Controller(self.store, self.provider, max_workers=20)

    def test_enrollment_is_idempotent_and_survives_restart(self):
        first = self.store.enroll(spec("Owner/Project", 3))
        again = self.store.enroll(spec("https://github.com/owner/project.git", 3))
        self.assertEqual(first, again)
        self.assertEqual(first["revision"], 1)
        self.reopen()
        self.assertEqual(self.store.deployment_id, "deployment-a")
        self.assertEqual(len(self.store.bindings()), 1)
        changed = self.store.enroll(spec(count=5))
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["desired_workers"], 5)

    def test_invalid_configuration_and_plaintext_token_fail(self):
        for count in (-1, True, 1.5, "2"):
            with self.assertRaises(ConfigurationError):
                Binding.parse(spec(count=count))
        for name in ("https://attacker.invalid/a/b", "owner/../escape", "owner/project?token=secret", "rad:abc"):
            with self.assertRaises(ConfigurationError):
                repository(name)
        for override in ({"token_ref": "actual-secret"}, {"endpoint": "https://user:secret@example.com"}, {"image": "arbitrary"}):
            config = {"schema_version": 1, "endpoint": "http://127.0.0.1:8000", "token_ref": "env:OJ_TOKEN", "max_workers": 5, **override}
            with self.assertRaises(ConfigurationError):
                ProviderConfig.parse(config)

    def test_local_plan_and_cli_validation_never_contact_provider(self):
        self.store.enroll(spec())
        self.controller.plan()
        self.assertEqual(self.provider.calls, [])
        config = Path(self.directory.name) / "binding.json"
        config.write_text(json.dumps(spec()))
        nonexistent = Path(self.directory.name) / "absent.db"
        result = subprocess.run([sys.executable, "-m", "zpm", "--state", str(nonexistent), "validate", str(config)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(nonexistent.exists())
        self.assertFalse(json.loads(result.stdout)["remote_access_checked"])

    def test_capability_failure_never_provisions(self):
        self.store.enroll(spec())
        self.provider.capable = False
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 0)
        self.assertEqual(len(self.provider.records), 0)
        self.assertFalse(report["repositories"][0]["validation"]["ready"])

    def test_wrong_scope_capability_fails_closed(self):
        original = self.provider.capabilities
        self.provider.capabilities = lambda *args: dict(original(*args), deployment_id="someone-else")
        self.store.enroll(spec())
        self.controller.tick()
        self.assertEqual(len(self.provider.records), 0)

    def test_reconcile_0_2_3_1_0_and_resume(self):
        self.store.enroll(spec(count=0))
        self.assertEqual(self.controller.tick()["reserved"], 0)
        for target in (2, 3, 1, 0):
            self.store.change("owner/project", "target", target)
            result = self.controller.tick()
            self.assertEqual(result["reserved"], target)
            self.assertEqual(result["repositories"][0]["healthy"], target)
        history = len(self.store.workers())
        self.reopen()
        self.store.change("owner/project", "target", 1)
        self.controller.tick()
        current = [x for x in self.store.workers() if not released(x["snapshot"])]
        self.assertEqual(len(current), 1)
        self.assertGreater(current[0]["generation"], 1)
        self.assertGreater(len(self.store.workers()), history)

    def test_lost_launch_reply_recovers_same_incarnation_after_restart(self):
        self.store.enroll(spec(count=1))
        self.provider.lose_launch_reply = True
        first = self.controller.tick()
        self.assertEqual(first["reserved"], 1)
        self.assertEqual(first["repositories"][0]["healthy"], 0)
        key = self.store.workers()[0]["launch_key"]
        self.reopen()
        second = self.controller.tick()
        self.assertEqual(second["repositories"][0]["healthy"], 1)
        self.assertEqual(len(self.provider.records), 1)
        self.assertEqual(len([x for x in self.provider.calls if x[0] == "ensure"]), 1)
        self.assertEqual(self.store.workers()[0]["launch_key"], key)

    def test_journalled_unknown_launch_resumes_same_ensure_after_restart(self):
        self.store.enroll(spec(count=1))
        ensure = self.provider.ensure
        calls = []

        def journal_before_dispatch(value):
            calls.append(dict(value))
            result = ensure(value)
            record = self.provider._find(value["deployment_id"], value["repository"], result["worker_id"])
            if len(calls) == 1:
                record.update(state="unknown", admission_open=False, reason="intent exists; dispatch interrupted")
                raise ProviderError("launch reply lost before backend dispatch")
            record.update(state="ready", admission_open=True, reason="same incarnation resumed")
            return dict(record)

        self.provider.ensure = journal_before_dispatch
        self.controller.tick()
        initial = self.store.workers()[0]
        self.assertEqual(initial["pending_action"], "ensure")
        self.reopen()
        report = self.controller.tick()
        self.assertEqual(report["repositories"][0]["healthy"], 1)
        self.assertEqual(len(self.provider.records), 1)
        self.assertEqual(calls, [calls[0], calls[0]])
        self.assertEqual(self.store.workers()[0]["launch_key"], initial["launch_key"])
        self.assertIsNone(self.store.workers()[0]["pending_action"])

    def test_unknown_launch_can_be_retired_without_resuming_ensure(self):
        self.store.enroll(spec(count=1))
        self.provider.mutate_response = lambda data: data.update(state="unknown", admission_open=False)
        self.controller.tick()
        initial = self.store.workers()[0]
        record = self.provider._find("deployment-a", initial["repository"], initial["snapshot"]["worker_id"])
        record.update(state="unknown", admission_open=False, reason="intent exists; dispatch interrupted")
        self.assertEqual(initial["pending_action"], "ensure")
        self.store.change("owner/project", "target", 0)
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 0)
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "ensure"]), 1)
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "drain"]), 1)
        self.assertEqual(self.store.workers()[0]["launch_key"], initial["launch_key"])

    def test_failed_receipt_with_open_admission_cannot_release_capacity(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        first = next(iter(self.provider.records.values()))
        first.update(state="failed", termination_confirmed=True, artifacts_captured=True, admission_open=True)
        self.assertFalse(released(first))
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 1)
        self.assertEqual(report["repositories"][0]["healthy"], 0)
        self.assertEqual(len(self.provider.records), 1)

    def test_malformed_state_retains_reservation_without_crashing_controller(self):
        self.store.enroll(spec(count=1))
        self.provider.mutate_response = lambda data: data.update(state=[])
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 1)
        self.assertEqual(report["repositories"][0]["healthy"], 0)
        self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "unknown")
        self.assertIn("worker response has unknown state", report["repositories"][0]["reasons"])

    def test_reserved_but_never_dispatched_can_cancel_without_launch(self):
        stored = self.store.enroll(spec(count=1))
        self.store.reserve(Binding.parse(spec(count=1)), stored["revision"])
        self.store.change("owner/project", "target", 0)
        result = self.controller.tick()
        self.assertEqual(result["reserved"], 0)
        self.assertEqual(len(self.provider.records), 0)
        self.assertEqual(self.store.workers()[0]["snapshot"]["reason"], "cancelled before dispatch")

    def test_pause_between_reservation_and_dispatch_wins_atomically(self):
        stored = self.store.enroll(spec(count=1))
        self.controller.check()
        row = self.store.reserve(Binding.parse(spec(count=1)), stored["revision"])
        self.store.change("owner/project", "pause")
        self.controller._launch(row, allowed=True)  # Deliberately stale controller decision.
        self.assertEqual(len(self.provider.records), 0)
        self.assertTrue(released(self.store.worker(row["launch_key"])["snapshot"]))

    def test_profile_edit_between_reservation_and_dispatch_prevents_old_launch(self):
        stored = self.store.enroll(spec(count=1))
        self.controller.check()
        row = self.store.reserve(Binding.parse(spec(count=1)), stored["revision"])
        self.store.enroll(dict(spec(count=1), profile="replacement-profile"))
        self.controller._launch(row, allowed=True)
        self.assertEqual(len(self.provider.records), 0)
        self.assertTrue(released(self.store.worker(row["launch_key"])["snapshot"]))

    def test_unknown_worker_is_not_replaced_for_a_healthy_deficit(self):
        self.store.enroll(spec(count=2))
        self.controller.tick()
        first = next(iter(self.provider.records.values()))
        self.provider.unreachable.add(first["worker_id"])
        for _ in range(3):
            result = self.controller.tick()
        self.assertEqual(result["reserved"], 2)
        self.assertEqual(result["repositories"][0]["healthy"], 1)
        self.assertEqual(len(self.provider.records), 2)

    def test_busy_workers_and_children_do_not_add_replica_demand(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        first = next(iter(self.provider.records.values()))
        first.update(state="busy", active_executions=["root", "child-a", "child-b"])
        result = self.controller.tick()
        self.assertEqual(result["repositories"][0]["healthy"], 1)
        self.assertEqual(len(self.provider.records), 1)

    def test_drain_waits_for_children_and_captures_before_release(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        first = next(iter(self.provider.records.values()))
        first.update(state="busy", active_executions=["root", "child"])
        self.store.change("owner/project", "target", 0)
        result = self.controller.tick()
        self.assertEqual(result["reserved"], 1)
        self.assertEqual(first["state"], "draining")
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "stop"]), 0)
        first["active_executions"] = []
        self.provider.capture_fails = True
        self.assertEqual(self.controller.tick()["reserved"], 1)
        pending_key = self.store.workers()[0]["action_key"]
        self.reopen()
        self.controller.tick()
        self.assertEqual(self.store.workers()[0]["action_key"], pending_key)
        self.provider.capture_fails = False
        self.assertEqual(self.controller.tick()["reserved"], 0)

    def test_idle_worker_is_drained_before_busy_worker(self):
        self.store.enroll(spec(count=2))
        self.controller.tick()
        values = list(self.provider.records.values())
        values[0].update(state="busy", active_executions=["root"])
        self.store.change("owner/project", "target", 1)
        self.controller.tick()
        self.assertEqual(values[0]["state"], "busy")
        self.assertEqual(values[1]["state"], "stopped")

    def test_lost_drain_reply_is_reconciled_without_new_drain(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        self.provider.lose_drain_reply = True
        self.store.change("owner/project", "target", 0)
        self.controller.tick()
        self.reopen()
        self.assertEqual(self.controller.tick()["reserved"], 0)
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "drain"]), 1)

    def test_repository_and_deployment_limits_include_uncertainty(self):
        self.store.enroll(dict(spec("owner/a", 5), max_workers=2))
        self.store.enroll(spec("owner/b", 5))
        self.controller.max_workers = 3
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 3)
        self.assertLessEqual(report["repositories"][0]["reserved"], 2)
        self.assertEqual({x["repository"] for x in self.store.workers()}, {repository("owner/a"), repository("owner/b")})

    def test_foreign_deployment_is_never_adopted_or_drained(self):
        other_path = Path(self.directory.name) / "other.db"
        with Store(other_path, "deployment-b") as other:
            other.enroll(spec(count=1))
            Controller(other, self.provider, 5).tick()
        foreign = next(iter(self.provider.records.values()))
        self.store.enroll(spec(count=1))
        self.controller.tick()
        self.store.change("owner/project", "target", 0)
        self.controller.tick()
        self.assertEqual(foreign["state"], "ready")
        self.assertTrue(all(call[1] == "deployment-a" for call in self.provider.calls if call[0] in {"drain", "stop"}))

    def test_reduced_deployment_ceiling_reports_excess_without_target_changes(self):
        self.store.enroll(spec("owner/a", 2))
        self.store.enroll(spec("owner/b", 2))
        self.controller.tick()
        self.controller.max_workers = 1
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 4)
        self.assertEqual(report["over_capacity"], 3)
        self.assertEqual(len(self.provider.records), 4)
        self.assertTrue(all(row["desired_workers"] == 2 for row in report["repositories"]))
        self.assertTrue(all("deployment reservations exceed current admission ceiling" in row["reasons"] for row in report["repositories"]))
        self.assertFalse(any(call[0] in {"drain", "stop"} for call in self.provider.calls))

    def test_small_mutation_budget_does_not_starve_healthy_repository(self):
        self.store.enroll(spec("owner/a", 1))
        self.store.enroll(spec("owner/b", 1))
        ensure = self.provider.ensure
        attempted = []

        def isolated_outage(value):
            attempted.append(value["repository"])
            if value["repository"] == repository("owner/a"):
                raise ProviderError("repository A runtime unavailable")
            return ensure(value)

        self.provider.ensure = isolated_outage
        first = self.controller.tick(max_actions=1)
        self.assertEqual(first["reserved"], 1)
        self.assertEqual(len(attempted), 1)
        self.reopen()
        second = self.controller.tick(max_actions=1)
        by_name = {row["repository"]: row for row in second["repositories"]}
        self.assertEqual(by_name[repository("owner/a")]["reserved"], 1)
        self.assertEqual(by_name[repository("owner/b")]["healthy"], 1)
        self.assertEqual(len(attempted), 2)
        self.assertEqual(attempted, [repository("owner/a"), repository("owner/b")])
        self.assertEqual(second["reserved"], 2)

    def test_idle_removed_enrollment_does_not_consume_mutation_credit(self):
        self.store.enroll(spec("owner/a", 1))
        self.store.change("owner/a", "remove")
        self.store.enroll(spec("owner/b", 1))
        report = self.controller.tick(max_actions=1)
        self.assertEqual(report["reserved"], 1)
        self.assertEqual(self.store.workers()[0]["repository"], repository("owner/b"))

    def test_mutated_launch_spec_is_not_adopted(self):
        self.store.enroll(spec(count=1))
        self.provider.mutate_response = lambda data: data.update(deployment_id="foreign")
        report = self.controller.tick()
        self.assertEqual(report["reserved"], 1)
        self.assertEqual(report["repositories"][0]["healthy"], 0)
        self.assertEqual(self.store.workers()[0]["snapshot"]["worker_id"], "")

    def test_runtime_worker_identity_cannot_be_reused_across_slots(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        old_id = self.store.workers()[0]["snapshot"]["worker_id"]
        self.provider.mutate_response = lambda data: data.update(worker_id=old_id)
        self.store.change("owner/project", "target", 2)
        report = self.controller.tick()
        self.assertEqual(report["repositories"][0]["healthy"], 1)
        self.assertEqual(report["reserved"], 2)

    def test_pause_remove_retain_binding_and_worker_history(self):
        self.store.enroll(spec(count=1))
        self.controller.tick()
        self.store.change("owner/project", "pause")
        self.assertEqual(self.controller.tick()["reserved"], 0)
        self.assertEqual(self.store.binding("owner/project")["desired_workers"], 1)
        self.store.change("owner/project", "resume", 2)
        self.assertEqual(self.controller.tick()["reserved"], 2)
        self.store.change("owner/project", "remove")
        self.assertEqual(self.controller.tick()["reserved"], 0)
        self.assertFalse(self.store.binding("owner/project")["enabled"])
        self.assertGreater(len(self.store.workers()), 1)

    def test_conflicting_controllers_are_rejected_across_processes(self):
        program = "from zpm.store import Store\nfrom zpm.models import ConfigurationError\nimport sys\ns=Store(sys.argv[1])\ntry:\n with s.controller_lock(): print('unsafe second controller')\nexcept ConfigurationError:\n sys.exit(7)\n"
        with self.store.controller_lock():
            result = subprocess.run([sys.executable, "-c", program, str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn("unsafe", result.stdout)


if __name__ == "__main__":
    unittest.main()
