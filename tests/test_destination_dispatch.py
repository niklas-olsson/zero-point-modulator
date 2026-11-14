"""Full destination protocol fixtures, never installed runtime evidence."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zpm import destination_contract as contract, destination_state as state
from zpm.destination_controller import DestinationController
from zpm.models import ConfigurationError, OPERATIONS, released
from zpm.provider import DestinationProvider, ProviderError
from zpm.store import Store

from .test_approvals import encode, envelope
from .test_registry import binding, config


REPOSITORY = "github:github.com/owner/project"


def configuration(hosts=2):
    result = config()
    result["max_workers"] = hosts
    result["destinations"], result["profiles"] = [], []
    for index in range(hosts):
        name = "host-" + chr(ord("a") + index)
        part = config(name)
        part["destinations"][0].update(endpoint=f"https://{name}.example", max_workers=1)
        result["destinations"] += part["destinations"]
        result["profiles"] += part["profiles"]
    return result


class FullFixture:
    """Two independent dictionaries intentionally reuse native worker IDs."""
    def __init__(self, approval, calls):
        self.approval = approval
        self.calls = calls
        self.records = {}
        self.specs = {}
        self.open = True
        self.revision = 1
        self.ceiling = 1
        self.available = True
        self.capable = True
        self.lose = None
        self.mutate = None
        self.before_contract = None
        self.effects = 0

    def call(self, method, *arguments):
        self.calls.append((self.approval["destination_binding"]["destination_id"], method, copy.deepcopy(arguments)))
        if not self.available:
            raise ProviderError("fixture host unavailable")

    def capabilities(self, **scope):
        self.call("capabilities", scope)
        return {"schema_version": 2, "deployment_id": scope["deployment_id"], "repository": scope["repository"],
                "profile": scope["profile"], "destination_binding": scope["binding"],
                "identity_binding": 1, "portable_manifest": 1, "local_admission": 1,
                "managed_dispatch": self.capable, "repository_access": self.capable,
                "profile_supported": True, "operations": sorted(OPERATIONS), "reason": "fixture"}

    def execution_contract(self, **scope):
        self.call("execution_contract", scope)
        if self.before_contract:
            self.before_contract()
        return {"schema_version": 2, "contract_version": 1, "deployment_id": scope["deployment_id"],
                "repository": scope["repository"], "profile": scope["profile"], "destination_binding": scope["binding"],
                "manifest": self.approval["manifest"], "execution_manifest_sha256": self.approval["execution_manifest_sha256"],
                "admission": self.admission()}

    def admission(self):
        return {"version": 1, "revision": self.revision, "new_effects_open": self.open,
                "worker_ceiling": self.ceiling, "unreleased_workers": sum(not released(r) for r in self.records.values())}

    def reply(self, data):
        data = copy.deepcopy(data)
        if self.mutate:
            self.mutate(data)
        return data

    def ensure(self, spec):
        self.call("ensure", spec)
        key = spec["launch_key"]
        if self.lose == "before":
            self.lose = None
            raise ProviderError("fixture lost before admission")
        if key in self.specs:
            if self.specs[key] != spec:
                raise ProviderError("changed original specification")
        else:
            self.specs[key] = copy.deepcopy(spec)
            refusal = ("stale_admission_revision" if spec["admission_revision"] != self.revision else
                       "closed" if not self.open else "ceiling" if self.admission()["unreleased_workers"] >= self.ceiling else None)
            data = spec | {"worker_id": f"worker-{len(self.records) + 1}", "state": "ready", "reason": "fixture ready",
                "termination_confirmed": False, "artifacts_captured": True, "admission_open": True,
                "active_executions": [], "disposition": None}
            if refusal:
                data.update(state="stopped", reason="never_admitted", termination_confirmed=True, admission_open=False,
                            disposition={"version": 1, "kind": "never_admitted", "reason": refusal,
                                         "observed_admission_revision": self.revision})
            else:
                self.effects += 1
            self.records[key] = data
        if self.lose == "after":
            self.lose = None
            raise ProviderError("fixture lost after admission")
        return self.reply(self.records[key])

    def discover(self, deployment_id, repository, profile, binding, *, launch_key=None, original=None):
        self.call("discover", deployment_id, repository, profile, binding, launch_key, original)
        return [self.reply(data) for key, data in self.records.items() if key == launch_key or launch_key is None]

    def find(self, worker_id):
        found = next((data for data in self.records.values() if data["worker_id"] == worker_id), None)
        if found is None:
            raise ProviderError("fixture original worker missing")
        return found

    def observe(self, deployment_id, repository, worker_id, binding):
        self.call("observe", deployment_id, repository, worker_id, binding)
        return self.reply(self.find(worker_id))

    def drain(self, deployment_id, repository, worker_id, request_key, binding):
        self.call("drain", deployment_id, repository, worker_id, request_key, binding)
        data = self.find(worker_id)
        if data["disposition"] is None:
            data.update(state="draining", admission_open=False, reason="fixture draining")
        if self.lose == "drain":
            self.lose = None
            raise ProviderError("fixture lost drain reply")
        return self.reply(data)

    def stop(self, deployment_id, repository, worker_id, request_key, binding):
        self.call("stop", deployment_id, repository, worker_id, request_key, binding)
        data = self.find(worker_id)
        if data["admission_open"] or data["active_executions"]:
            raise ProviderError("active execution")
        if data["disposition"] is None:
            data.update(state="stopped", termination_confirmed=True, artifacts_captured=True, reason="fixture stopped")
        return self.reply(data)


class DestinationDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "state.sqlite3"
        self.calls = []
        self.hosts = {}
        self.store = None
        self.addCleanup(lambda: self.store.close() if self.store else None)

    def prepare(self, hosts=2, target=None, active=True):
        self.configuration = configuration(hosts)
        self.store = Store.initialize_registry(self.path, self.configuration, "deployment-a")
        for item in self.configuration["destinations"]:
            name = item["destination_id"]
            approval = envelope()
            approval["destination_binding"]["destination_id"] = name
            path = self.root / (name + ".json")
            path.write_bytes(encode(approval))
            path.chmod(0o600)
            self.store.import_approval(path)
            self.hosts[name] = FullFixture(approval, self.calls)
        if active:
            self.store.activate_destinations(offline=True)
            for item in self.configuration["destinations"]:
                item["enabled"] = True
            self.store.import_registry(self.configuration)
            self.store.enroll(binding(hosts if target is None else target))
            self.controller = DestinationController(self.store, self.factory)
        return self.store

    def factory(self, config):
        return self.hosts[config.endpoint.split("//")[1].split(".")[0]]

    def reopen(self):
        self.store.close()
        self.store = Store(self.path, "deployment-a", create=False)
        self.controller = DestinationController(self.store, self.factory)

    def tick(self, limit=3):
        before = len(self.calls)
        result = self.controller.tick(limit)
        self.assertEqual(result["tick_io"]["used"], len(self.calls) - before)
        self.assertLessEqual(len(self.calls) - before, limit)
        return result

    def test_activation_is_explicit_offline_and_fences_older_open_writers(self):
        self.prepare(active=False)
        frozen = Path(__file__).parent / "fixtures" / "legacy_store_c5fb355.py"
        spec = importlib.util.spec_from_file_location("zpm._pre_full_store", frozen)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The predecessor predates approval records too. Exercise the old open
        # writer with the actual v2 layout it understands before either fence.
        with tempfile.TemporaryDirectory() as old_directory:
            path = Path(old_directory) / "state.sqlite3"
            new = Store.initialize_registry(path, configuration(), "deployment-a")
            old = module.Store(path, "deployment-a", create=False)
            self.addCleanup(old.close)
            self.addCleanup(new.close)
            with self.assertRaises(ConfigurationError):
                new.activate_destinations(offline=False)
            self.assertFalse(new.registry_status()["execution_enabled"])
            new.activate_destinations(offline=True)
            self.assertTrue(new.registry_status()["execution_enabled"])
            self.assertTrue(all(not d["enabled"] for d in new.registry_status()["destinations"]))
            with self.assertRaises(ConfigurationError):
                old.import_registry(configuration())
            with self.assertRaises(ConfigurationError):
                module.Store(path, "deployment-a", create=False)
        with self.assertRaises(ConfigurationError):
            DestinationController(self.store, self.factory)
        self.assertEqual(self.calls, [])

    def test_two_hosts_use_shared_ceilings_and_scoped_native_ids(self):
        self.prepare()
        first = self.tick(3)
        self.assertEqual(first["reserved"], 1)
        self.assertEqual(self.tick(3)["reserved"], 2)
        self.tick(16)
        self.assertEqual([h.effects for h in self.hosts.values()], [1, 1])
        rows = self.store.workers()
        self.assertEqual({r["snapshot"]["worker_id"] for r in rows}, {"worker-1"})
        self.assertEqual(len({r["slot_id"] for r in rows}), 2)
        self.assertTrue(all(r["destination_admitted"] == 1 for r in rows))
        self.reopen()
        self.assertEqual(self.controller.plan()["reserved"], 2)

    def test_total_budget_and_persisted_rotation_make_healthy_progress(self):
        self.prepare()
        self.hosts["host-a"].available = False
        too_small = self.tick(2)
        self.assertEqual(too_small["reserved"], 0)
        self.assertFalse(too_small["tick_io"]["new_placement_budget_available"])
        self.assertEqual(too_small["tick_io"]["minimum_new_placement"], 3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.tick(3)["reserved"], 0)
        self.assertEqual([c[1] for c in self.calls], ["capabilities"])
        self.reopen()
        self.assertEqual(self.tick(3)["reserved"], 1)
        self.assertEqual(self.hosts["host-b"].effects, 1)
        for _ in range(6):
            self.tick(3)
        self.assertTrue(any(c[0] == "host-b" and c[1] == "observe" for c in self.calls))
        self.assertEqual(self.hosts["host-a"].effects, 0)
        self.assertEqual(self.controller.plan()["repositories"][0]["shortfall"], 1)

    def test_missing_native_state_never_releases_or_relocates_original_capacity(self):
        self.prepare()
        self.tick()
        self.tick()
        original = self.store.workers()[0]
        name = state.binding(original)["destination_id"]
        self.hosts[name].records.clear()
        for _ in range(4):
            self.assertEqual(self.tick(3)["reserved"], 2)
        self.reopen()
        self.assertEqual(self.store.worker(original["launch_key"])["snapshot"]["state"], "unknown")
        self.assertEqual([h.effects for h in self.hosts.values()], [1, 1])
        self.assertEqual(len(self.store.workers()), 2)

    def test_deployment_ceiling_shrink_waits_for_original_drain_and_capture(self):
        self.prepare()
        self.tick()
        self.tick()
        self.configuration["max_workers"] = 1
        self.store.import_registry(self.configuration)
        for _ in range(8):
            self.tick(3)
        self.assertEqual(self.controller.plan()["reserved"], 1)
        rows = self.store.workers()
        self.assertEqual(sum(released(row["snapshot"]) for row in rows), 1)
        self.assertEqual([h.effects for h in self.hosts.values()], [1, 1])

    def test_corrupt_pending_worker_identity_is_refused_on_reopen(self):
        self.prepare(hosts=1)
        self.hosts["host-a"].lose = "before"
        self.tick()
        row = self.store.workers()[0]
        for invalid in (None, False, 0, []):
            with self.subTest(invalid=invalid):
                self.store.connection.execute("UPDATE workers SET snapshot=?", (json.dumps(row["snapshot"] | {"worker_id": invalid}),))
                with self.assertRaises(ConfigurationError):
                    Store(self.path, "deployment-a", create=False)
        self.store.connection.execute("UPDATE workers SET snapshot=?", (json.dumps(row["snapshot"]),))
        self.reopen()
        self.assertEqual(self.controller.plan()["reserved"], 1)

    def test_released_legacy_history_remains_readable_but_cannot_be_reactivated(self):
        from zpm.controller import Controller
        from .support import FixtureProvider
        self.store = Store(self.path, "deployment-a")
        legacy = Controller(self.store, FixtureProvider(), 1)
        self.store.enroll(binding(1))
        legacy.tick()
        self.store.change(REPOSITORY, "target", 0)
        legacy.tick()
        original = self.store.workers()[0]
        self.assertTrue(released(original["snapshot"]))
        self.store.migrate_registry(configuration(1), self.root / "legacy-backup.sqlite3", offline=True)
        self.store.activate_destinations(offline=True)
        self.assertEqual(self.store.workers()[0]["snapshot"], original["snapshot"])
        for sql, value in (("destination_admitted", 1), ("pending_action", "ensure"),
                           ("snapshot", json.dumps(original["snapshot"] | {"state": "unknown", "termination_confirmed": False}))):
            with self.subTest(field=sql):
                self.store.connection.execute(f"UPDATE workers SET {sql}=?", (value,))
                with self.assertRaises(ConfigurationError):
                    Store(self.path, "deployment-a", create=False)
                restore = 0 if sql == "destination_admitted" else None if sql == "pending_action" else json.dumps(original["snapshot"])
                self.store.connection.execute(f"UPDATE workers SET {sql}=?", (restore,))
        self.reopen()
        self.assertEqual(self.controller.plan()["reserved"], 0)

    def test_lost_reply_recovers_only_original_key_without_current_readiness(self):
        self.prepare(hosts=1)
        host = self.hosts["host-a"]
        host.lose = "after"
        self.assertEqual(self.tick()["reserved"], 1)
        row = self.store.workers()[0]
        original = (row["launch_key"], row["destination_binding"], row["execution_qualification"])
        self.assertEqual(row["snapshot"]["state"], "unknown")
        host.capable, host.open, host.revision = False, False, 2
        self.reopen()
        self.calls.clear()
        self.assertEqual(self.tick(1)["reserved"], 1)
        self.assertEqual([c[1] for c in self.calls], ["discover"])
        recovered = self.store.workers()[0]
        self.assertEqual((recovered["launch_key"], recovered["destination_binding"], recovered["execution_qualification"]), original)
        self.assertEqual(recovered["snapshot"]["state"], "ready")
        self.assertEqual(host.effects, 1)

    def test_tiny_budget_recovers_original_despite_an_unfilled_other_destination(self):
        self.prepare()
        self.hosts["host-a"].lose = "after"
        self.tick(3)
        self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "unknown")
        self.reopen()
        self.calls.clear()
        self.tick(1)
        self.assertEqual([c[1] for c in self.calls], ["discover"])
        self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "ready")
        self.assertEqual(self.hosts["host-b"].effects, 0)
        self.reopen()
        self.tick(3)
        self.assertEqual(self.hosts["host-b"].effects, 1)

    def test_empty_discovery_stays_charged_until_permanent_original_refusal(self):
        self.prepare(hosts=1)
        host = self.hosts["host-a"]
        host.lose = "before"
        self.tick()
        row = self.store.workers()[0]
        original = state.original(row)
        self.reopen()
        self.assertEqual(self.tick(1)["reserved"], 1)
        self.assertEqual(self.store.workers()[0]["pending_action"], "ensure-retry")
        host.open, host.revision = False, 2
        self.reopen()
        self.assertEqual(self.tick(1)["reserved"], 0)
        retained = self.store.workers()[0]
        self.assertEqual(retained["launch_key"], row["launch_key"])
        self.assertEqual(state.original(retained), original)
        self.assertEqual(retained["snapshot"]["disposition"], {"version": 1, "kind": "never_admitted",
            "reason": "stale_admission_revision", "observed_admission_revision": 2})
        self.assertEqual(host.effects, 0)
        self.reopen()
        host.open = True
        self.tick(3)
        self.assertEqual(len(self.store.workers()), 2)
        self.assertEqual(self.store.worker(row["launch_key"])["snapshot"], retained["snapshot"])
        self.assertEqual(host.effects, 1)

    def test_wrong_original_binding_or_manifest_keeps_uncertainty_charged(self):
        for field in ("destination_binding", "execution_manifest_sha256", "admission_revision", "launch_key"):
            with self.subTest(field=field):
                if self.store:
                    self.store.close()
                    self.store = None
                    for path in self.root.glob("state.sqlite3*"):
                        path.unlink()
                self.prepare(hosts=1)
                host = self.hosts["host-a"]
                def mutate(data):
                    if field == "destination_binding":
                        data[field]["journal_incarnation"] = "9" * 32
                    elif field == "execution_manifest_sha256":
                        data[field] = "sha256:" + "0" * 64
                    elif field == "admission_revision":
                        data[field] += 1
                    else:
                        data[field] = "another-operation"
                host.mutate = mutate
                self.assertEqual(self.tick()["reserved"], 1)
                self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "unknown")
                self.reopen()
                self.assertEqual(self.tick(1)["reserved"], 1)
                self.assertEqual(len(self.store.workers()), 1)
                host.mutate = None
                self.tick(1)
                self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "ready")
                self.assertEqual(host.effects, 1)

    def test_admitted_worker_cannot_later_be_reclassified_never_admitted(self):
        self.prepare(hosts=1)
        self.tick()
        host = self.hosts["host-a"]
        def fabricate(data):
            data.update(state="stopped", reason="never_admitted", termination_confirmed=True, artifacts_captured=True,
                        admission_open=False, active_executions=[], disposition={"version": 1, "kind": "never_admitted",
                        "reason": "closed", "observed_admission_revision": 1})
        host.mutate = fabricate
        self.assertEqual(self.tick(1)["reserved"], 1)
        self.assertEqual(self.store.workers()[0]["snapshot"]["state"], "unknown")
        self.reopen()
        self.assertEqual(self.controller.plan()["reserved"], 1)

    def test_disabled_destination_and_closed_host_still_drain_and_stop_original(self):
        self.prepare(hosts=1)
        self.tick()
        original = self.store.workers()[0]
        self.configuration["destinations"][0]["enabled"] = False
        self.configuration["destinations"][0]["token_ref"] = "env:ROTATED_PRIVATE_REF"
        self.store.import_registry(self.configuration)
        host = self.hosts["host-a"]
        host.capable, host.open, host.revision = False, False, 2
        self.calls.clear()
        self.tick(1)  # observe then commit original drain key
        drain = self.store.workers()[0]["action_key"]
        host.lose = "drain"
        self.tick(1)
        self.assertEqual(self.store.workers()[0]["action_key"], drain)
        self.reopen()
        self.tick(1)  # replay original drain; then commit stop key
        self.assertEqual(self.store.workers()[0]["pending_action"], "stop")
        self.assertEqual(self.tick(1)["reserved"], 0)
        self.assertEqual([c[1] for c in self.calls], ["observe", "drain", "drain", "stop"])
        self.assertTrue(all(c[2][-1] == state.binding(original) for c in self.calls))
        self.assertEqual(self.store.workers()[0]["execution_qualification"], original["execution_qualification"])
        self.assertEqual(host.effects, 1)

    def test_concurrent_target_edit_after_readiness_prevents_launch(self):
        self.prepare(hosts=1)
        second = Store(self.path, "deployment-a", create=False)
        self.addCleanup(second.close)
        self.hosts["host-a"].before_contract = lambda: second.change(REPOSITORY, "target", 0)
        self.assertEqual(self.tick()["reserved"], 0)
        self.assertEqual([c[1] for c in self.calls], ["capabilities", "execution_contract"])
        self.assertEqual(self.hosts["host-a"].effects, 0)

    def test_retired_retry_keeps_discovering_a_late_original_without_new_ensure(self):
        self.prepare(hosts=1)
        host = self.hosts["host-a"]
        host.lose = "after"
        self.tick()
        retained = host.records.pop(next(iter(host.records)))
        self.tick(1)
        self.assertEqual(self.store.workers()[0]["pending_action"], "ensure-retry")
        self.store.change(REPOSITORY, "target", 0)
        self.reopen()
        self.calls.clear()
        self.assertEqual(self.tick(1)["reserved"], 1)
        host.records[retained["launch_key"]] = retained
        self.tick(1)
        self.assertEqual(self.store.workers()[0]["pending_action"], "drain")
        self.tick(1)
        self.assertEqual(self.tick(1)["reserved"], 0)
        self.assertEqual([c[1] for c in self.calls], ["discover", "discover", "drain", "stop"])
        self.assertEqual(host.effects, 1)
        self.assertEqual(len(self.store.workers()), 1)

    def test_refusal_suppresses_fresh_keys_for_the_destination_in_same_tick(self):
        self.prepare(hosts=1)
        host = self.hosts["host-a"]
        host.lose = "before"
        self.tick()
        self.tick(1)
        host.open, host.revision = False, 2
        self.calls.clear()
        self.tick(16)
        self.assertEqual([c[1] for c in self.calls], ["ensure"])
        self.assertEqual(len(self.store.workers()), 1)


class FullContractTests(unittest.TestCase):
    def setUp(self):
        self.approved = envelope()
        self.binding = self.approved["destination_binding"]
        self.original = {"contract_version": 1, "execution_manifest_sha256": self.approved["execution_manifest_sha256"], "admission_revision": 7}
        self.receipt = {"schema_version": 2, "deployment_id": "deployment-a", "repository": REPOSITORY,
            "slot_id": "slot-0", "generation": 1, "launch_key": "original-key", "profile": "fixture",
            "destination_binding": self.binding, **self.original, "worker_id": "logical-refusal-id", "state": "stopped",
            "reason": "never_admitted", "termination_confirmed": True, "artifacts_captured": True,
            "admission_open": False, "active_executions": [], "disposition": {"version": 1, "kind": "never_admitted",
            "reason": "closed", "observed_admission_revision": 7}}

    def test_receipt_requires_every_terminal_fact_and_exact_original(self):
        contract.worker_snapshot(self.receipt, binding=self.binding, original=self.original)
        changes = {"schema_version": True, "contract_version": True, "admission_revision": 8,
            "state": "failed", "reason": "closed", "termination_confirmed": False,
            "artifacts_captured": False, "admission_open": True, "active_executions": ["original-execution"],
            "worker_id": "", "disposition": None}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                contract.worker_snapshot(self.receipt | {field: value}, binding=self.binding, original=self.original)
        for field, value in {"version": True, "kind": "gone", "reason": [], "observed_admission_revision": 8}.items():
            data = copy.deepcopy(self.receipt)
            data["disposition"][field] = value
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                contract.worker_snapshot(data, binding=self.binding, original=self.original)
        for field in self.receipt:
            data = copy.deepcopy(self.receipt)
            del data[field]
            with self.subTest(missing=field), self.assertRaises(ConfigurationError):
                contract.worker_snapshot(data, binding=self.binding, original=self.original)

    def test_empty_keyed_discovery_echoes_all_original_fields_and_mixed_modes_stay_visible(self):
        response = {"schema_version": 2, "destination_binding": self.binding, **self.original, "workers": []}
        self.assertEqual(contract.discovery(response, binding=self.binding, original=self.original), [])
        for field in self.original:
            wrong = response | {field: True if field != "execution_manifest_sha256" else "sha256:" + "f" * 64}
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                contract.discovery(wrong, binding=self.binding, original=self.original)
        identity = {k: v for k, v in self.receipt.items() if k not in contract.QUALIFICATION | {"disposition"}}
        identity["reason"] = "identity stopped"
        mixed = {"schema_version": 2, "contract_version": 1, "destination_binding": self.binding,
                 "workers": [identity, self.receipt]}
        found = contract.discovery(mixed, binding=self.binding)
        self.assertEqual(len(found), 2)
        self.assertNotIn("contract_version", found[0])
        self.assertEqual(found[1]["disposition"]["kind"], "never_admitted")
        with self.assertRaises(ConfigurationError):
            contract.discovery(mixed, binding=self.binding, original=self.original)

    def test_transport_uses_exact_route_and_original_control_shape(self):
        from zpm.models import ProviderConfig
        client = DestinationProvider(ProviderConfig.parse({"schema_version": 1, "endpoint": "https://host.example/prefix",
            "token_ref": "env:PRIVATE_REF", "max_workers": 1}))
        with patch.object(client, "_send", return_value=self.receipt) as send:
            client.stop("deployment-a", REPOSITORY, "worker/a", "original-stop-key", self.binding)
            self.assertEqual(send.call_args.args[:2], ("POST", "/worker%2Fa/stop"))
            self.assertEqual(send.call_args.args[2], {"schema_version": 2, "deployment_id": "deployment-a", "repository": REPOSITORY,
                "request_key": "original-stop-key", "destination_binding": self.binding})
            self.assertEqual(send.call_args.kwargs, {"base_path": "/api/v2/workers", "strict": True})
        response = {"schema_version": 2, "destination_binding": self.binding, **self.original, "workers": []}
        with patch.object(client, "_send", return_value=response) as send:
            self.assertEqual(client.discover("deployment-a", REPOSITORY, "fixture", self.binding,
                launch_key="original-key", original=self.original), [])
            self.assertEqual(send.call_args.args[3], {"schema_version": 2, "deployment_id": "deployment-a", "repository": REPOSITORY,
                "profile": "fixture", **self.binding, **self.original, "launch_key": "original-key"})
        for raw in (b'{"schema_version":2,"schema_version":2}', b'{"x":NaN}', b'[' * 1500):
            with self.assertRaises(ConfigurationError):
                contract.decode_response(raw)

    def test_discovery_client_refuses_foreign_scope_in_mixed_and_keyed_rows(self):
        from zpm.models import ProviderConfig
        client = DestinationProvider(ProviderConfig.parse({"schema_version": 1, "endpoint": "https://host.example",
            "token_ref": "env:PRIVATE_REF", "max_workers": 1}))
        for keyed in (False, True):
            for field in ("deployment_id", "repository", "profile", *(('launch_key',) if keyed else ())):
                data = self.receipt | {field: "foreign"}
                response = {"schema_version": 2, "contract_version": 1, "destination_binding": self.binding, "workers": [data]}
                if keyed:
                    response.update(self.original)
                with self.subTest(keyed=keyed, field=field), patch.object(client, "_send", return_value=response), self.assertRaises(ConfigurationError):
                    client.discover("deployment-a", REPOSITORY, "fixture", self.binding,
                        **({"launch_key": "original-key", "original": self.original} if keyed else {}))


if __name__ == "__main__":
    unittest.main()
