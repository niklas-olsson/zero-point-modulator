"""Negative oracle checks for the shared-service trial; these do not run Docker."""

from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from zpm.controller import Controller
from zpm.models import Binding
from zpm.store import Store

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_shared_isolation_stack import repository_plan, require_private_workspaces, require_retained_histories
finally:
    sys.path.pop(0)


class SharedPlanEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.deployment = "shared-deployment"
        self.names = ["github:github.com/trial/" + label for label in ("a", "b")]
        self.profiles = {name: "profile-" + label for name, label in zip(self.names, ("a", "b"))}
        self.plan = {"deployment_id": self.deployment, "max_workers": 3, "reserved": 2, "repositories": []}
        for name, label in zip(self.names, ("a", "b")):
            worker = {"deployment_id": self.deployment, "repository": name, "profile": self.profiles[name],
                      "worker_id": "worker-" + label, "slot_id": "slot-1", "generation": 1,
                      "launch_key": "launch-" + label, "pending_operation": None}
            self.plan["repositories"].append({"repository": name, "profile": self.profiles[name], "reserved": 1, "workers": [worker]})

    def project(self, plan):
        return repository_plan(plan, self.deployment, self.profiles, self.names[1])

    def test_projection_preserves_input_and_only_narrows_after_validation(self):
        original = deepcopy(self.plan)
        narrowed = self.project(self.plan)
        self.assertEqual(self.plan, original)
        self.assertEqual(narrowed["reserved"], 1)
        self.assertEqual(narrowed["repositories"], [original["repositories"][1]])
        self.assertEqual(narrowed["deployment_id"], self.deployment)
        self.assertEqual(narrowed["max_workers"], 3)

    def test_b_projection_rejects_hidden_changes_in_a_or_shared_totals(self):
        cases = [
            ("deployment", lambda plan: plan.update(deployment_id="foreign")),
            ("ceiling", lambda plan: plan.update(max_workers=4)),
            ("missing scope", lambda plan: plan["repositories"].pop(0)),
            ("duplicated scope", lambda plan: plan["repositories"].__setitem__(0, deepcopy(plan["repositories"][1]))),
            ("foreign scope", lambda plan: plan["repositories"][0].update(repository="github:github.com/trial/foreign")),
            ("extra scope", lambda plan: plan["repositories"].append(deepcopy(plan["repositories"][0]))),
            ("changed A profile", lambda plan: plan["repositories"][0].update(profile="foreign")),
            ("changed A worker scope", lambda plan: plan["repositories"][0]["workers"][0].update(repository=self.names[1])),
            ("changed A worker deployment", lambda plan: plan["repositories"][0]["workers"][0].update(deployment_id="foreign")),
            ("changed A worker profile", lambda plan: plan["repositories"][0]["workers"][0].update(profile="foreign")),
            ("shared worker identity", lambda plan: plan["repositories"][0]["workers"][0].update(worker_id="worker-b")),
            ("shared launch key", lambda plan: plan["repositories"][0]["workers"][0].update(launch_key="launch-b")),
            ("lost A reservation", lambda plan: plan["repositories"][0].update(reserved=0)),
            ("malformed A reservation", lambda plan: plan["repositories"][0].update(reserved=True)),
            ("lost shared reservation", lambda plan: plan.update(reserved=1)),
            ("malformed shared reservation", lambda plan: plan.update(reserved=True)),
            ("unbound A", lambda plan: plan["repositories"][0]["workers"][0].update(worker_id=None)),
            ("empty A identity", lambda plan: plan["repositories"][0]["workers"][0].update(worker_id="")),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                changed = deepcopy(self.plan)
                mutate(changed)
                with self.assertRaises(AssertionError):
                    self.project(changed)

    def test_real_pending_store_plan_preserves_both_unbound_reservations(self):
        with TemporaryDirectory() as directory, Store(Path(directory) / "state.sqlite3", self.deployment) as store:
            reservations = []
            for name in self.names:
                binding = Binding.parse({"schema_version": 1, "repository": name, "profile": self.profiles[name], "desired_workers": 1})
                stored = store.enroll(binding)
                store.validation(name, True, "", stored["revision"])
                reservations.append(store.reserve(binding, stored["revision"]))
            controller = Controller(store, max_workers=3)
            reserved = controller.plan()
            self.assertEqual(reserved["reserved"], 2)
            self.assertTrue(all(row["workers"][0]["worker_id"] == "" for row in reserved["repositories"]))
            self.assertEqual(self.project(reserved)["reserved"], 1)
            for reservation in reservations:
                store.dispatch(reservation["launch_key"], 3)
            dispatched = controller.plan()
            self.assertTrue(all(row["workers"][0]["pending_operation"] == "ensure" for row in dispatched["repositories"]))
            self.assertEqual(self.project(dispatched)["reserved"], 1)
            store.uncertainty(reservations[0]["launch_key"], "controlled response loss")
            uncertain = controller.plan()
            self.assertEqual(uncertain["repositories"][0]["workers"][0]["state"], "unknown")
            self.assertEqual(self.project(uncertain)["reserved"], 1)
            for changes in ({"worker_id": None}, {"state": "ready"}, {"admission_open": True},
                            {"termination_confirmed": True}, {"artifacts_captured": True},
                            {"active_executions": ["unbound-execution"]}):
                with self.subTest(changes=changes):
                    changed = deepcopy(uncertain)
                    changed["repositories"][0]["workers"][0].update(changes)
                    with self.assertRaises(AssertionError):
                        self.project(changed)
            changed = deepcopy(uncertain)
            changed["reserved"] = 1
            with self.assertRaises(AssertionError):
                self.project(changed)
            # A cleared action cannot turn the actual empty identity marker
            # into a settled worker merely because both scope counts match.
            store.save_observation(reservations[0]["launch_key"], store.worker(reservations[0]["launch_key"])["snapshot"])
            with self.assertRaises(AssertionError):
                self.project(controller.plan())

    def test_duplicate_incarnation_or_over_ceiling_cannot_hide_in_other_scope(self):
        changed = deepcopy(self.plan)
        first = changed["repositories"][0]
        duplicate = first["workers"][0] | {"worker_id": "new-worker", "launch_key": "new-launch"}
        first["workers"].append(duplicate)
        first["reserved"] = 2
        changed["reserved"] = 3
        with self.assertRaises(AssertionError):
            self.project(changed)
        duplicate["slot_id"] = "slot-2"
        self.assertEqual(self.project(changed)["reserved"], 1)
        first["workers"].append(duplicate | {"worker_id": "third-worker", "launch_key": "third-launch", "slot_id": "slot-3"})
        first["reserved"] = 3
        changed["reserved"] = 4
        with self.assertRaises(AssertionError):
            self.project(changed)

    def test_retained_history_must_include_observed_workers_and_original_peer(self):
        observed = {"a-original", "a-scaled", "a-reenrolled"}
        known = {"a": observed | {"a-post-capture-replacement"}, "b": {"b-original"}}
        require_retained_histories(known, observed, "b-original")
        for missing in observed:
            with self.subTest(missing=missing), self.assertRaises(AssertionError):
                require_retained_histories(known | {"a": known["a"] - {missing}}, observed, "b-original")
        for peer in (set(), {"b-replacement"}, {"b-original", "b-extra"}):
            with self.subTest(peer=peer), self.assertRaises(AssertionError):
                require_retained_histories(known | {"b": peer}, observed, "b-original")
        with self.assertRaises(AssertionError):
            require_retained_histories({"a": set(), "b": set()}, observed, "b-original")
        with self.assertRaises(AssertionError):
            require_retained_histories(known | {"a": known["a"] | known["b"]}, observed, "b-original")


class SharedMountEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.containers = {label: {"Id": "container-" + label, "State": {"Running": True}, "Mounts": [
            {"Destination": "/source", "Type": "bind", "Source": "/private/snapshot-" + label, "RW": False},
            {"Destination": "/workspace", "Type": "volume", "Name": "workspace-" + label, "RW": True},
        ]} for label in ("a", "b")}

    def test_actual_mount_evidence_requires_distinct_readonly_sources_and_private_volumes(self):
        require_private_workspaces(self.containers)
        # Docker versions may also report the two configured nonpersistent mounts.
        for label in self.containers:
            self.containers[label]["Mounts"].extend({"Destination": path, "Type": "tmpfs"} for path in ("/tmp", "/control"))
        require_private_workspaces(self.containers)

    def test_foreign_or_shared_mounts_cannot_pass_with_distinct_container_ids(self):
        cases = [
            ("same container", lambda rows: rows["a"].update(Id="container-b")),
            ("stopped peer", lambda rows: rows["b"]["State"].update(Running=False)),
            ("shared source", lambda rows: rows["a"]["Mounts"][0].update(Source="/private/snapshot-b")),
            ("writable source", lambda rows: rows["a"]["Mounts"][0].update(RW=True)),
            ("shared workspace", lambda rows: rows["a"]["Mounts"][1].update(Name="workspace-b")),
            ("host workspace", lambda rows: rows["a"]["Mounts"][1].update(Type="bind")),
            ("foreign extra volume", lambda rows: rows["a"]["Mounts"].append({"Destination": "/foreign", "Type": "volume", "Name": "workspace-b", "RW": True})),
            ("foreign extra source", lambda rows: rows["a"]["Mounts"].append({"Destination": "/foreign", "Type": "bind", "Source": "/private/snapshot-b", "RW": False})),
            ("extra tmpfs", lambda rows: rows["a"]["Mounts"].append({"Destination": "/foreign", "Type": "tmpfs"})),
            ("missing workspace", lambda rows: rows["a"]["Mounts"].pop()),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                changed = deepcopy(self.containers)
                mutate(changed)
                with self.assertRaises(AssertionError):
                    require_private_workspaces(changed)


if __name__ == "__main__":
    unittest.main()
