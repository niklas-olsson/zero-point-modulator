"""Destination mode of the existing pull-based capacity reconciler.

One controller lock, one worker journal and one bounded I/O budget. The runtime
still pulls canonical tasks; placement here reserves capacity, never tasks.
"""

from __future__ import annotations

import json
import time

from . import destination_contract as contract, destination_state as state
from .controller import Controller
from .models import Binding, ConfigurationError, ProviderConfig, released
from .provider import DestinationProvider, ProviderError


class _Budget:
    def __init__(self, maximum):
        if type(maximum) is not int or maximum < 1:
            raise ConfigurationError("max_actions must be positive")
        self.remaining = maximum
        self.used = 0

    def call(self, operation, *args, **kwargs):
        if self.remaining <= 0:
            raise ConfigurationError("destination tick I/O budget is exhausted")
        self.remaining -= 1
        self.used += 1
        return operation(*args, **kwargs)


class DestinationController(Controller):
    def __init__(self, store, provider_factory=DestinationProvider):
        store.require_active_destinations()
        self.store = store
        self.provider_factory = provider_factory
        self._providers = {}
        self._refused_destinations = set()

    def plan(self):
        return state.plan(self.store)

    def _provider(self, destination_id, original_binding=None):
        _, policy, destinations, _ = state.current(self.store)
        destination = destinations.get(destination_id)
        if destination is None or (original_binding is not None
                and destination["journal_incarnation"] != original_binding["journal_incarnation"]):
            raise ConfigurationError("the original execution destination is unavailable")
        config = ProviderConfig.parse({"schema_version": 1, "endpoint": destination["endpoint"],
            "token_ref": destination["token_ref"], "max_workers": destination["max_workers"],
            "timeout_seconds": policy["timeout_seconds"]})
        key = (destination_id, config.endpoint, config.token_ref, config.max_workers, config.timeout_seconds)
        if key not in self._providers:
            self._providers[key] = self.provider_factory(config)
        return self._providers[key]

    @staticmethod
    def _binding(row):
        return Binding.parse({field: row[field] for field in Binding.__dataclass_fields__})

    def _retire(self):
        """Select original reservations to retire without releasing uncertainty."""
        _, policy, destinations, profiles = state.current(self.store)
        live = [row for row in self.store.workers() if not released(row["snapshot"])]
        retire = set()
        bindings = {row["repository"]: self._binding(row) for row in self.store.bindings()}
        selected_profiles = {(p["destination_id"], p["repository"], p["profile"]): p for p in profiles}
        for row in live:
            selected = bindings[row["repository"]]
            original = state.binding(row)
            destination = destinations[original["destination_id"]]
            current_profile = selected_profiles.get((original["destination_id"], row["repository"], row["profile"]))
            if (row["profile"] != selected.profile or not destination["enabled"] or current_profile is None
                    or current_profile["policy_binding_id"] != original["policy_binding_id"]):
                retire.add(row["launch_key"])
        order = {"ready": 0, "starting": 1, "busy": 2, "failed": 3, "unknown": 4}

        def reduce(rows, maximum):
            chosen = sorted(rows, key=lambda row: (
                row["launch_key"] not in retire and row["pending_action"] not in {"drain", "stop"}
                and row["snapshot"]["state"] != "draining",
                order.get(row["snapshot"]["state"], 5), row["repository"], row["slot_id"], row["generation"]))
            for row in chosen[:max(0, len(rows) - maximum)]:
                retire.add(row["launch_key"])

        for repository, selected in bindings.items():
            reduce([row for row in live if row["repository"] == repository], selected.permitted_target)
        for destination_id, destination in destinations.items():
            reduce([row for row in live if state.binding(row)["destination_id"] == destination_id], destination["max_workers"])
        reduce(live, policy["max_workers"])
        return retire

    def _accept(self, row, response, observing=False):
        expected = {"deployment_id": self.store.deployment_id,
                    **{field: row[field] for field in ("repository", "slot_id", "generation", "launch_key", "profile")}}
        binding, original = state.binding(row), state.original(row)
        data = contract.worker_snapshot(response, expected=expected, binding=binding, original=original)
        with self.store.transaction():
            row = self.store.worker(row["launch_key"])
            if row["snapshot"]["worker_id"] and row["snapshot"]["worker_id"] != data["worker_id"]:
                raise ConfigurationError("destination changed its original worker incarnation")
            if data["disposition"] is not None and row["destination_admitted"]:
                raise ConfigurationError("an admitted operation cannot become never admitted")
            for other in self.store.workers():
                if (other["launch_key"] != row["launch_key"] and other["protocol_version"] == 2
                        and state.binding(other)["destination_id"] == binding["destination_id"]
                        and state.binding(other)["journal_incarnation"] == binding["journal_incarnation"]
                        and other["snapshot"]["worker_id"] == data["worker_id"]):
                    raise ConfigurationError("destination reused a worker identity across original keys")
            clear = (row["pending_action"] is None or released(data)
                     or (row["pending_action"] in {"reserve", "ensure", "ensure-retry"}
                         and data["state"] in {"starting", "ready", "busy", "draining"})
                     or (row["pending_action"] == "drain" and data["state"] == "draining" and not data["admission_open"]))
            self.store.save_observation(row["launch_key"], data, clear_action=clear)
            if data["disposition"] is None:
                self.store.connection.execute("UPDATE workers SET destination_admitted=1 WHERE launch_key=?", (row["launch_key"],))
        if data["disposition"] is not None:
            self._refused_destinations.add(binding["destination_id"])
        return self.store.worker(row["launch_key"])

    def _ensure(self, row, budget):
        with self.store.transaction():
            self.store.require_active_destinations()
            row = self.store.worker(row["launch_key"])
            if row["pending_action"] not in {"reserve", "ensure-retry"}:
                return
            if row["launch_key"] in self._retire():
                if row["pending_action"] == "reserve":
                    data = {**row["snapshot"], "state": "failed", "reason": "cancelled before dispatch",
                            "termination_confirmed": True, "artifacts_captured": True}
                    self.store.save_observation(row["launch_key"], data)
                return
            self.store.connection.execute("UPDATE workers SET pending_action='ensure' WHERE launch_key=?", (row["launch_key"],))
        binding = state.binding(row)
        provider = self._provider(binding["destination_id"], binding)
        spec = {"schema_version": 2, "deployment_id": self.store.deployment_id,
                **{field: row[field] for field in ("repository", "slot_id", "generation", "launch_key", "profile")},
                "destination_binding": binding, **state.original(row)}
        self._accept(row, budget.call(provider.ensure, spec))

    def _worker(self, key, budget):
        row = self.store.worker(key)
        if released(row["snapshot"]):
            return
        binding = state.binding(row)
        try:
            provider = self._provider(binding["destination_id"], binding)
            pending = row["pending_action"]
            retired_retry = pending == "ensure-retry" and key in self._retire()
            if pending in {"reserve", "ensure-retry"} and not retired_retry:
                self._ensure(row, budget)
                return
            if pending == "ensure" or retired_retry:
                found = budget.call(provider.discover, self.store.deployment_id, row["repository"], row["profile"], binding,
                                    launch_key=key, original=state.original(row))
                if len(found) > 1 or any(item.get("launch_key") != key for item in found):
                    raise ConfigurationError("destination discovery returned conflicting original operations")
                if found:
                    row = self._accept(row, found[0])
                if (not released(row["snapshot"]) and row["snapshot"]["worker_id"]
                        and key in self._retire()):
                    self.store.action(key, "drain")
                elif row["pending_action"] == "ensure":
                    # An empty qualified response proves no terminal outcome.
                    # Retain B/H/A and charge capacity. The next fair turn may
                    # resume the same operation, without a fresh readiness gate.
                    self.store.connection.execute("UPDATE workers SET pending_action='ensure-retry' WHERE launch_key=?", (key,))
                return
            if pending in {"drain", "stop"}:
                response = budget.call(getattr(provider, pending), self.store.deployment_id, row["repository"],
                                       row["snapshot"]["worker_id"], row["action_key"], binding)
                row = self._accept(row, response)
            else:
                row = self._accept(row, budget.call(provider.observe, self.store.deployment_id, row["repository"],
                                                   row["snapshot"]["worker_id"], binding), observing=True)
            if released(row["snapshot"]):
                return
            if (row["snapshot"]["state"] == "draining" and not row["snapshot"]["active_executions"]
                  and not row["snapshot"]["admission_open"] and row["pending_action"] is None):
                self.store.action(key, "stop")
            elif key in self._retire() and row["pending_action"] is None:
                self.store.action(key, "drain")
        except (ProviderError, ConfigurationError) as error:
            self.store.uncertainty(key, str(error))

    def _readiness(self, selected, budget):
        binding = selected["binding"]
        scope = {"deployment_id": self.store.deployment_id, "repository": selected["profile"]["repository"],
                 "profile": selected["profile"]["profile"], "binding": binding}
        try:
            provider = self._provider(binding["destination_id"], binding)
            raw = budget.call(provider.capabilities, **scope)
            ready, reason = contract.capabilities(raw, **scope)
            host = None
            if ready:
                raw = budget.call(provider.execution_contract, **scope)
                host = contract.contract_response(raw, **scope, approved=selected["approval"])
                ready = host["new_effects_open"] and host["unreleased_workers"] < host["worker_ceiling"]
                reason = "ready" if ready else "destination admission is closed or at its ceiling"
            state.save_validation(self.store, selected, ready, reason, host)
            return host if ready else None
        except (ProviderError, ConfigurationError) as error:
            state.save_validation(self.store, selected, False, str(error))
            return None

    def _eligible(self, destination_id, repository, profile):
        selected = state.selection(self.store, destination_id, repository, profile)
        local = self.store.binding(repository)
        binding = self._binding(local)
        rows = [row for row in self.store.workers() if not released(row["snapshot"])]
        if (not selected["destination"]["enabled"] or binding.profile != profile or binding.permitted_target == 0
                or len(rows) >= selected["policy"]["max_workers"]
                or sum(row["repository"] == repository for row in rows) >= binding.permitted_target
                or sum(row["protocol_version"] == 2 and state.binding(row)["destination_id"] == destination_id for row in rows)
                >= selected["destination"]["max_workers"]):
            return None
        return selected, local, binding

    def _units(self, *, check=False):
        _, _, _, profiles = state.current(self.store)
        units = [("destination", p["repository"], p["profile"], p["destination_id"]) for p in profiles]
        if not check:
            selected = {(row["repository"], row["profile"]) for row in self.store.bindings()}
            units = [unit for unit in units if (unit[1], unit[2]) in selected]
            units += [("worker", row["launch_key"]) for row in self.store.workers()
                      if not released(row["snapshot"])]
        pairs = sorted((json.dumps(unit, separators=(",", ":")), unit) for unit in units)
        previous = state.cursor(self.store)
        return [pair for pair in pairs if pair[0] > previous] + [pair for pair in pairs if pair[0] <= previous]

    def _tick(self, maximum, *, check=False):
        self.store.require_active_destinations()
        self._providers = {}
        self._refused_destinations = set()
        budget = _Budget(maximum)
        for cursor, unit in self._units(check=check):
            if budget.remaining == 0:
                break
            if unit[0] == "worker":
                self._worker(unit[1], budget)
            else:
                _, repository, profile, destination_id = unit
                if destination_id in self._refused_destinations:
                    state.cursor(self.store, cursor)
                    continue
                try:
                    eligible = None if check else self._eligible(destination_id, repository, profile)
                    if not check and eligible is None:
                        state.cursor(self.store, cursor)
                        continue
                    selected = state.selection(self.store, destination_id, repository, profile) if check else eligible[0]
                    # Reserve the pair of readiness reads and, for new work,
                    # one ensure. Do not advance past an affordable next-tick
                    # unit merely because the preceding host spent this budget.
                    required = 2 if check else 3
                    if budget.remaining < required:
                        if budget.used == 0:
                            # A deliberately tiny total budget can still
                            # recover original operations. It never performs
                            # half of a new-placement readiness sequence.
                            state.cursor(self.store, cursor)
                            continue
                        break
                    observed = self._readiness(selected, budget)
                    if observed is not None and not check:
                        row = state.reserve(self.store, eligible[2], eligible[1]["revision"], selected, observed)
                        try:
                            self._ensure(row, budget)
                        except (ProviderError, ConfigurationError) as error:
                            self.store.uncertainty(row["launch_key"], str(error))
                except ConfigurationError:
                    # Missing approval/enrollment remains a local unavailable
                    # result, without reading a credential or native endpoint.
                    pass
            state.cursor(self.store, cursor)
        return self.plan() | {"tick_io": {"limit": maximum, "used": budget.used, "remaining": budget.remaining,
            "minimum_new_placement": 3, "minimum_destination_check": 2,
            "new_placement_budget_available": maximum >= 3,
            "destination_check_budget_available": maximum >= 2}}

    def check(self, max_actions=16):
        with self.store.controller_lock():
            return self._tick(max_actions, check=True)

    def tick(self, max_actions=16):
        with self.store.controller_lock():
            return self._tick(max_actions)

    def run(self, interval=5.0, max_actions=16):
        if interval < 0.1:
            raise ConfigurationError("recovery interval must be at least 0.1 seconds")
        with self.store.controller_lock():
            while True:
                self._tick(max_actions)
                time.sleep(interval)
