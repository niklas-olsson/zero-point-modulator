"""Explicit full-destination extension of the existing capacity journal."""

from __future__ import annotations

import json
import time
import uuid

from . import approvals, registry
from .destination_contract import admission, qualification, worker_snapshot
from .models import Binding, ConfigurationError, released
from .portable_manifest import parse_approval


FEATURE = "full_destination_schema"
COLUMNS = {"destination_observations": ("destination_id", "repository", "profile", "observation")}
WORKER_COLUMNS = ("execution_qualification", "destination_admitted")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def enabled(connection):
    row = connection.execute("SELECT value_v2 FROM metadata WHERE key=?", (FEATURE,)).fetchone()
    if row is None:
        return False
    if row[0] != "1":
        raise ConfigurationError("unsupported full destination journal extension")
    return True


def activate(store, *, offline):
    if not offline:
        raise ConfigurationError("activation requires stopped older writers and explicit --offline")
    store.require_registry()
    with store.controller_lock(), store.transaction():
        store.require_registry()
        if enabled(store.connection):
            return
        if any(not released(row["snapshot"]) or row["pending_action"] is not None
               or row["action_key"] is not None for row in store.workers()):
            raise ConfigurationError("full destination activation requires all legacy history released")
        if any(row["desired_workers"] != 0 for row in store.bindings()):
            raise ConfigurationError("full destination activation requires zero repository targets")
        store.connection.execute("ALTER TABLE workers ADD COLUMN execution_qualification TEXT")
        store.connection.execute("ALTER TABLE workers ADD COLUMN destination_admitted INTEGER NOT NULL DEFAULT 0")
        store.connection.execute("""CREATE TABLE destination_observations (
            destination_id TEXT NOT NULL, repository TEXT NOT NULL, profile TEXT NOT NULL,
            observation TEXT NOT NULL, PRIMARY KEY(destination_id, repository, profile))""")
        store.connection.execute("INSERT INTO metadata VALUES (?, '1')", (FEATURE,))
    store.require_active_destinations()


def validate_records(connection, deployment_id):
    for row in connection.execute("SELECT * FROM workers"):
        if type(row["destination_admitted"]) is not int or row["destination_admitted"] not in (0, 1):
            raise ConfigurationError("invalid retained destination admission fact")
        if row["protocol_version"] == 1:
            legacy = registry.decode_json(row["snapshot"])
            if (row["destination_binding"] is not None or row["execution_qualification"] is not None
                    or row["destination_admitted"] != 0 or type(legacy) is not dict
                    or not released(legacy) or row["pending_action"] is not None or row["action_key"] is not None):
                raise ConfigurationError("legacy history no longer satisfies the offline activation fence")
            continue
        if row["protocol_version"] != 2:
            raise ConfigurationError("unsupported destination worker protocol")
        binding = registry.destination_binding(registry.decode_json(row["destination_binding"]))
        original = qualification(registry.decode_json(row["execution_qualification"]))
        data = registry.decode_json(row["snapshot"])
        if type(data) is not dict:
            raise ConfigurationError("invalid retained destination snapshot")
        expected = {"deployment_id": deployment_id, **{name: row[name] for name in (
            "repository", "slot_id", "generation", "launch_key", "profile")}}
        if type(data.get("worker_id")) is not str:
            raise ConfigurationError("invalid retained destination worker identity")
        if not data["worker_id"]:
            # A locally reserved or uncertain unsent/unacknowledged operation
            # cannot contain terminal evidence supplied by nobody.
            data = {**data, "worker_id": "pending-validation-only"}
            if row["destination_admitted"] or row["pending_action"] in {"drain", "stop"}:
                raise ConfigurationError("known destination operation lost its worker identity")
            if released(data) or data.get("disposition") is not None:
                if not (row["pending_action"] is None and data["reason"] == "cancelled before dispatch"
                        and data["state"] == "failed" and data["termination_confirmed"] is True
                        and data["artifacts_captured"] is True and data["admission_open"] is False
                        and not data["active_executions"] and data.get("disposition") is None):
                    raise ConfigurationError("unacknowledged destination intent has invalid terminal evidence")
            elif row["pending_action"] not in {"reserve", "ensure", "ensure-retry"}:
                raise ConfigurationError("unacknowledged destination intent lost its recovery phase")
        accepted = worker_snapshot(data, expected=expected, binding=binding, original=original)
        if accepted["disposition"] is not None and row["destination_admitted"]:
            raise ConfigurationError("an admitted destination operation became never admitted")
        if row["pending_action"] not in {None, "reserve", "ensure", "ensure-retry", "drain", "stop"}:
            raise ConfigurationError("invalid retained destination operation")
    for row in connection.execute("SELECT * FROM destination_observations"):
        value = registry.decode_json(row["observation"])
        if type(value) is not dict or type(value.get("ready")) is not bool:
            raise ConfigurationError("invalid destination observation")


def current(store):
    store.require_active_destinations()
    db = store.connection
    revision = db.execute("SELECT * FROM registry_revisions ORDER BY revision DESC LIMIT 1").fetchone()
    destinations = {row["destination_id"]: registry.decode_json(row["specification"]) | {"revision": row["revision"]}
                    for row in registry._latest(db, "destinations", ("destination_id",))}
    profiles = [registry.decode_json(row["specification"]) | {"revision": row["revision"]}
                for row in registry._latest(db, "destination_profiles", ("destination_id", "repository", "profile"))]
    return revision["revision"], registry.decode_json(revision["policy"]), destinations, profiles


def selection(store, destination_id, repository, profile):
    revision, policy, destinations, profiles = current(store)
    destination = destinations.get(destination_id)
    selected = next((p for p in profiles if (p["destination_id"], p["repository"], p["profile"])
                     == (destination_id, repository, profile)), None)
    if destination is None or selected is None:
        raise ConfigurationError("destination profile is not enrolled")
    binding = {"destination_id": destination_id, "journal_incarnation": destination["journal_incarnation"],
               "policy_binding_id": selected["policy_binding_id"], "policy_binding_version": selected["policy_binding_version"]}
    if not approvals.enabled(store.connection):
        raise ConfigurationError("destination execution contract is not approved")
    row = store.connection.execute("SELECT envelope FROM destination_approvals WHERE destination_id=? AND policy_binding_id=?",
                                   (destination_id, selected["policy_binding_id"])).fetchone()
    if row is None:
        raise ConfigurationError("destination execution contract is not approved")
    approved = parse_approval(row[0]).envelope
    if (approved["destination_binding"] != binding or approved["deployment_id"] != store.deployment_id
            or approved["repository"] != repository or approved["profile"] != profile):
        raise ConfigurationError("destination approval does not match current enrollment")
    return {"registry_revision": revision, "policy": policy, "destination": destination,
            "profile": selected, "binding": binding, "approval": approved}


def save_validation(store, selected, ready, reason, observation=None):
    key = (selected["binding"]["destination_id"], selected["profile"]["repository"], selected["profile"]["profile"])
    value = {"ready": ready, "reason": reason, "checked_at": time.time(),
             "registry_revision": selected["registry_revision"], "profile_revision": selected["profile"]["revision"],
             "destination_binding": selected["binding"],
             "execution_manifest_sha256": selected["approval"]["execution_manifest_sha256"],
             "admission": observation}
    store.connection.execute("INSERT INTO destination_observations VALUES (?,?,?,?) ON CONFLICT(destination_id,repository,profile) DO UPDATE SET observation=excluded.observation",
                             (*key, encode(value)))


def observation(store, selected):
    row = store.connection.execute("SELECT observation FROM destination_observations WHERE destination_id=? AND repository=? AND profile=?",
        (selected["binding"]["destination_id"], selected["profile"]["repository"], selected["profile"]["profile"])).fetchone()
    if row is None:
        return {"ready": False, "reason": "not checked"}
    value = registry.decode_json(row[0])
    if (value.get("registry_revision") != selected["registry_revision"]
            or value.get("profile_revision") != selected["profile"]["revision"]
            or value.get("destination_binding") != selected["binding"]
            or value.get("execution_manifest_sha256") != selected["approval"]["execution_manifest_sha256"]):
        return {**value, "ready": False, "reason": "enrollment changed after observation"}
    return value


def original(row):
    return qualification(registry.decode_json(row["execution_qualification"]))


def binding(row):
    return registry.destination_binding(registry.decode_json(row["destination_binding"]))


def reserve(store, repository_binding, repository_revision, selected, host_admission):
    """Commit original B/H/A and charge every ceiling before any ensure I/O."""
    store.require_active_destinations()
    with store.transaction():
        latest = selection(store, selected["binding"]["destination_id"], repository_binding.repository, repository_binding.profile)
        current_binding = store.binding(repository_binding.repository)
        destination = latest["destination"]
        if (latest != selected or current_binding["revision"] != repository_revision
                or not destination["enabled"] or not current_binding["enabled"] or current_binding["paused"]):
            raise ConfigurationError("enrollment changed before destination reservation")
        active = [row for row in store.workers() if not released(row["snapshot"])]
        own = [row for row in active if row["repository"] == repository_binding.repository]
        at_destination = [row for row in active if row["protocol_version"] == 2
                          and binding(row)["destination_id"] == destination["destination_id"]]
        if (len(active) >= latest["policy"]["max_workers"] or len(own) >= repository_binding.permitted_target
                or len(at_destination) >= destination["max_workers"]):
            raise ConfigurationError("destination reservation exceeds a current capacity ceiling")
        admitted = admission(host_admission)
        if not admitted["new_effects_open"] or admitted["unreleased_workers"] >= admitted["worker_ceiling"]:
            raise ConfigurationError("destination currently refuses new capacity")
        slot, generation = store.next_slot(repository_binding.repository)
        key = str(uuid.uuid4())
        selected_original = qualification({"contract_version": 1,
            "execution_manifest_sha256": selected["approval"]["execution_manifest_sha256"],
            "admission_revision": admitted["revision"]})
        data = {"schema_version": 2, "deployment_id": store.deployment_id,
            "repository": repository_binding.repository, "slot_id": slot, "generation": generation,
            "launch_key": key, "profile": repository_binding.profile, "destination_binding": selected["binding"],
            "worker_id": "", "state": "starting", "reason": "launch reserved", "termination_confirmed": False,
            "artifacts_captured": False, "admission_open": False, "active_executions": [],
            **selected_original, "disposition": None}
        store.connection.execute("""INSERT INTO workers
            (launch_key,repository,slot_id,generation,profile,snapshot,pending_action,action_key,
             protocol_version,destination_binding,execution_qualification)
            VALUES (?,?,?,?,?,?,'reserve',?,2,?,?)""", (key, repository_binding.repository, slot, generation,
                repository_binding.profile, encode(data), key, encode(selected["binding"]), encode(selected_original)))
    return store.worker(key)


def cursor(store, key=None):
    if key is None:
        row = store.connection.execute("SELECT value_v2 FROM metadata WHERE key='destination_cursor'").fetchone()
        return row[0] if row else ""
    store.connection.execute("INSERT INTO metadata VALUES ('destination_cursor',?) ON CONFLICT(key) DO UPDATE SET value_v2=excluded.value_v2", (key,))


def plan(store):
    revision, policy, destinations, profiles = current(store)
    rows = store.workers()
    live = [row for row in rows if not released(row["snapshot"])]
    output = []
    for destination_id, destination in sorted(destinations.items()):
        owned = [row for row in live if row["protocol_version"] == 2 and binding(row)["destination_id"] == destination_id]
        views = []
        for profile in profiles:
            if profile["destination_id"] != destination_id:
                continue
            try:
                selected = selection(store, destination_id, profile["repository"], profile["profile"])
                view = observation(store, selected)
                approved = True
            except ConfigurationError as error:
                view, approved = {"ready": False, "reason": str(error)}, False
            views.append({"repository": profile["repository"], "profile": profile["profile"],
                          "approved": approved, "validation": view})
        output.append({"destination_id": destination_id, "journal_incarnation": destination["journal_incarnation"],
                       "enabled": destination["enabled"], "max_workers": destination["max_workers"],
                       "reserved": len(owned), "uncertain": sum(row["snapshot"]["state"] == "unknown" for row in owned),
                       "profiles": views})
    repositories = []
    for row in store.bindings():
        spec = Binding.parse({field: row[field] for field in Binding.__dataclass_fields__})
        owned = [worker for worker in live if worker["repository"] == spec.repository]
        healthy = sum(worker["snapshot"]["state"] in {"ready", "busy"} and worker["profile"] == spec.profile for worker in owned)
        repositories.append({"repository": spec.repository, "profile": spec.profile, "desired_workers": spec.desired_workers,
            "permitted_target": spec.permitted_target, "healthy": healthy, "reserved": len(owned),
            "shortfall": max(0, spec.target - healthy), "enabled": spec.enabled, "paused": spec.paused,
            "workers": [worker["snapshot"] | {"pending_operation": worker["pending_action"]} for worker in owned]})
    return {"schema_version": 2, "deployment_id": store.deployment_id, "execution_enabled": True,
            "registry_revision": revision, "max_workers": policy["max_workers"], "reserved": len(live),
            "destinations": output, "repositories": repositories,
            "observation_scope": "retained; status does not refresh or grant execution"}
