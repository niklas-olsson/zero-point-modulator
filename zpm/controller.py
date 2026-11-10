"""Single-host reconciliation with durable intent and conservative recovery."""

from __future__ import annotations

import time
from typing import Any

from .models import Binding, ConfigurationError, count, readiness, released, snapshot
from .provider import ProviderError, WorkerProvider
from .store import Store


def _binding(row: dict[str, Any]) -> Binding:
    return Binding.parse({key: row[key] for key in Binding.__dataclass_fields__})


class Controller:
    def __init__(self, store: Store, provider: WorkerProvider | None = None, max_workers: int | None = None):
        self.store = store
        self.provider = provider
        self.max_workers = None if max_workers is None else count(max_workers, "max_workers")

    def plan(self) -> dict[str, Any]:
        """Read local observations only. Never contact a runtime or resolve tokens."""
        all_workers = self.store.workers()
        occupied = sum(not released(row["snapshot"]) for row in all_workers)
        over_capacity = None if self.max_workers is None else max(0, occupied - self.max_workers)
        results = []
        for stored in self.store.bindings():
            binding = _binding(stored)
            live = [row for row in all_workers if row["repository"] == binding.repository and not released(row["snapshot"])]
            healthy = sum(row["snapshot"]["state"] in {"ready", "busy"} and row["profile"] == binding.profile for row in live)
            reserved = len(live)
            reasons = sorted({row["snapshot"]["reason"] for row in live if row["snapshot"]["state"] == "unknown"})
            if not stored["validation"]["ready"] and binding.target:
                reasons.append(stored["validation"]["reason"])
            if binding.target > binding.permitted_target:
                reasons.append("repository capacity ceiling")
            if self.max_workers is not None and occupied >= self.max_workers and reserved < binding.permitted_target:
                reasons.append("deployment capacity ceiling")
            if over_capacity:
                reasons.append("deployment reservations exceed current admission ceiling")
            if reserved > healthy and binding.target > healthy:
                reasons.append("starting, draining or uncertain capacity retains reservations")
            results.append({
                "repository": binding.repository, "profile": binding.profile,
                "desired_workers": binding.desired_workers, "effective_target": binding.target,
                "permitted_target": binding.permitted_target,
                "enabled": binding.enabled, "paused": binding.paused,
                "revision": stored["revision"], "validation": stored["validation"],
                "healthy": healthy, "reserved": reserved,
                "shortfall": max(0, binding.target - healthy),
                "launch_deficit": max(0, binding.permitted_target - reserved),
                "excess_reservations": max(0, reserved - binding.permitted_target),
                "reasons": reasons,
                "workers": [row["snapshot"] | {"pending_operation": row["pending_action"]} for row in live],
            })
        return {"schema_version": 1, "deployment_id": self.store.deployment_id, "max_workers": self.max_workers, "reserved": occupied, "over_capacity": over_capacity, "repositories": results}

    def check(self) -> dict[str, Any]:
        if self.provider is None:
            raise ConfigurationError("provider is required for remote validation")
        for row in self.store.bindings():
            try:
                response = self.provider.capabilities(self.store.deployment_id, row["repository"], row["profile"])
                ready, reason = readiness(response)
                expected = {"deployment_id": self.store.deployment_id, "repository": row["repository"], "profile": row["profile"]}
                if ready and any(response.get(key) != value for key, value in expected.items()):
                    ready, reason = False, "provider capability response does not match the requested scope"
            except (ProviderError, ConfigurationError) as exc:
                ready, reason = False, str(exc)
            self.store.validation(row["repository"], ready, reason, row["revision"])
        return self.plan()

    def _accept(self, row: dict[str, Any], response: Any, observing: bool = False) -> dict[str, Any]:
        data = snapshot(response)
        expected = {"deployment_id": self.store.deployment_id, **{key: row[key] for key in ("repository", "slot_id", "generation", "launch_key", "profile")}}
        if any(data[key] != value for key, value in expected.items()):
            raise ConfigurationError("runtime response changed immutable ownership or execution specification")
        previous_id = row["snapshot"]["worker_id"]
        if previous_id and previous_id != data["worker_id"]:
            raise ConfigurationError("runtime changed the immutable worker incarnation")
        if any(other["launch_key"] != row["launch_key"] and other["snapshot"]["worker_id"] == data["worker_id"] for other in self.store.workers()):
            raise ConfigurationError("runtime reused a worker incarnation across reservations")
        operation = row["pending_action"]
        clear = (
            operation in {None, "reserve"}
            or (operation == "ensure" and data["state"] in {"starting", "ready", "busy", "draining"})
            or released(data)
            or (operation == "drain" and data["state"] == "draining" and not data["admission_open"])
        )
        self.store.save_observation(row["launch_key"], data, clear_action=clear)
        return self.store.worker(row["launch_key"])

    def _launch(self, row: dict[str, Any], allowed: bool, cancel_unstarted: bool = False) -> bool:
        """Resolve a lost response first. The original key is never replaced."""
        assert self.provider is not None
        key = row["launch_key"]
        try:
            if row["pending_action"] == "ensure":
                found = self.provider.discover(self.store.deployment_id, row["repository"], key)
                matches = [item for item in found if isinstance(item, dict) and item.get("launch_key") == key]
                if len(matches) > 1:
                    raise ConfigurationError("multiple runtime incarnations match one launch key")
                if matches:
                    row = self._accept(row, matches[0])
                    if row["pending_action"] != "ensure":
                        return False
                    # Discovery can find a journalled incarnation whose launch
                    # never reached the backend. Observation is read-only; keep
                    # its intent and resume the same idempotent ensure below.
            if not allowed:
                if row["pending_action"] == "reserve" and cancel_unstarted:
                    data = dict(row["snapshot"])
                    data.update(state="failed", reason="cancelled before dispatch", termination_confirmed=True, artifacts_captured=True)
                    self.store.save_observation(key, data)
                elif row["pending_action"] == "ensure":
                    self.store.uncertainty(key, "launch unresolved; no replacement or new launch is authorized")
                return False
            assert self.max_workers is not None
            row = self.store.dispatch(key, self.max_workers)
            if row["pending_action"] != "ensure":
                return False
            spec = {"schema_version": 1, "deployment_id": self.store.deployment_id, **{field: row[field] for field in ("repository", "slot_id", "generation", "launch_key", "profile")}}
            self._accept(row, self.provider.ensure(spec))
            return True
        except (ProviderError, ConfigurationError) as exc:
            self.store.uncertainty(key, str(exc))
            return True

    def _mutate(self, row: dict[str, Any], operation: str) -> None:
        assert self.provider is not None
        key = row["launch_key"]
        try:
            row = self.store.action(key, operation)
            call = getattr(self.provider, operation)
            result = call(self.store.deployment_id, row["repository"], row["snapshot"]["worker_id"], row["action_key"])
            self._accept(row, result)
        except (ProviderError, ConfigurationError) as exc:
            self.store.uncertainty(key, str(exc))

    def tick(self, max_actions: int = 16) -> dict[str, Any]:
        with self.store.controller_lock():
            return self._tick(max_actions)

    def _tick(self, max_actions: int) -> dict[str, Any]:
        if self.provider is None or self.max_workers is None:
            raise ConfigurationError("reconciliation requires a provider and an explicit deployment capacity ceiling")
        if type(max_actions) is not int or max_actions < 1:
            raise ConfigurationError("max_actions must be positive")
        self.check()
        remaining = max_actions
        budgets = self.store.action_budgets(max_actions)
        # Observe owned, journalled incarnations. Never adopt unrecorded discovery data.
        for row in self.store.workers():
            if released(row["snapshot"]) or not row["snapshot"]["worker_id"]:
                continue
            try:
                data = self.provider.observe(self.store.deployment_id, row["repository"], row["snapshot"]["worker_id"])
                self._accept(row, data, observing=True)
            except (ProviderError, ConfigurationError) as exc:
                self.store.uncertainty(row["launch_key"], str(exc))

        # Reconcile prior durable operations before making new policy decisions.
        for row in self.store.workers():
            if released(row["snapshot"]):
                continue
            stored = self.store.binding(row["repository"])
            binding = _binding(stored)
            if row["pending_action"] in {"reserve", "ensure"}:
                if remaining <= 0 or budgets.get(row["repository"], 0) <= 0:
                    continue
                live = sum(not released(x["snapshot"]) for x in self.store.workers(row["repository"]))
                policy_allowed = binding.profile == row["profile"] and live <= binding.permitted_target and binding.permitted_target > 0
                allowed = policy_allowed and stored["validation"]["ready"]
                if self._launch(row, allowed, cancel_unstarted=not policy_allowed):
                    remaining -= 1
                    budgets[row["repository"]] -= 1
            elif row["pending_action"] in {"drain", "stop"} and row["snapshot"]["worker_id"] and remaining > 0 and budgets.get(row["repository"], 0) > 0:
                self._mutate(row, row["pending_action"])
                remaining -= 1
                budgets[row["repository"]] -= 1

        # Retire outdated profiles and excess capacity, choosing idle workers first.
        for stored in self.store.bindings():
            binding = _binding(stored)
            rows = [row for row in self.store.workers(binding.repository) if not released(row["snapshot"])]
            draining = [row for row in rows if row["snapshot"]["state"] == "draining" or row["pending_action"] in {"drain", "stop"}]
            candidates = [row for row in rows if row not in draining and row["snapshot"]["worker_id"] and row["pending_action"] in {None, "ensure"}]
            order = {"ready": 0, "starting": 1, "busy": 2, "failed": 3, "unknown": 4}
            candidates.sort(key=lambda row: (row["profile"] == binding.profile, order.get(row["snapshot"]["state"], 5), row["slot_id"]))
            required = max(0, len(rows) - binding.permitted_target - len(draining))
            for row in candidates:
                if remaining <= 0 or budgets.get(binding.repository, 0) <= 0:
                    break
                if row["profile"] != binding.profile or required > 0:
                    self._mutate(row, "drain")
                    remaining -= 1
                    budgets[binding.repository] -= 1
                    required = max(0, required - 1)
            for row in self.store.workers(binding.repository):
                data = row["snapshot"]
                if remaining > 0 and budgets.get(binding.repository, 0) > 0 and data["state"] == "draining" and not data["active_executions"] and not data["admission_open"] and row["pending_action"] is None:
                    self._mutate(row, "stop")
                    remaining -= 1
                    budgets[binding.repository] -= 1

        # Fair rounds, with all nonterminal reservations counted against limits.
        progress = True
        failed_repositories: set[str] = set()
        while remaining > 0 and progress:
            progress = False
            for stored in self.store.bindings():
                if remaining <= 0:
                    break
                binding = _binding(stored)
                rows = [row for row in self.store.workers() if not released(row["snapshot"])]
                own_count = sum(row["repository"] == binding.repository for row in rows)
                if budgets.get(binding.repository, 0) <= 0 or binding.repository in failed_repositories or not stored["validation"]["ready"] or own_count >= binding.permitted_target or len(rows) >= self.max_workers:
                    continue
                try:
                    row = self.store.reserve(binding, stored["revision"])
                except ConfigurationError:
                    continue  # A concurrent operator edit wins before launch.
                self._launch(row, allowed=True)
                if self.store.worker(row["launch_key"])["snapshot"]["state"] in {"failed", "unknown"}:
                    failed_repositories.add(binding.repository)
                remaining -= 1
                budgets[binding.repository] -= 1
                progress = True
        return self.plan()

    def run(self, interval: float = 5.0, max_actions: int = 16) -> None:
        if interval < 0.1:
            raise ConfigurationError("recovery interval must be at least 0.1 seconds")
        with self.store.controller_lock():
            while True:
                self._tick(max_actions)
                time.sleep(interval)
