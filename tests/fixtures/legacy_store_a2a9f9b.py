"""Local durable enrollment and runtime-operation journal. No task ledger."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Binding, ConfigurationError, count, identifier, released, repository


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, path: str | Path, deployment_id: str | None = None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bindings (
                repository TEXT PRIMARY KEY, spec TEXT NOT NULL,
                revision INTEGER NOT NULL, validation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers (
                launch_key TEXT PRIMARY KEY, repository TEXT NOT NULL,
                slot_id TEXT NOT NULL, generation INTEGER NOT NULL,
                profile TEXT NOT NULL, snapshot TEXT NOT NULL,
                pending_action TEXT, action_key TEXT,
                UNIQUE(repository, slot_id, generation)
            );
        """)
        os.chmod(self.path, 0o600)
        with self.transaction():
            row = self.connection.execute("SELECT value FROM metadata WHERE key='deployment_id'").fetchone()
            if row is None:
                current = identifier(deployment_id, "deployment_id") if deployment_id else str(uuid.uuid4())
                self.connection.execute("INSERT INTO metadata VALUES ('deployment_id', ?)", (current,))
            else:
                current = row["value"]
                if deployment_id is not None and deployment_id != current:
                    raise ConfigurationError("state belongs to a different deployment_id")
            self.deployment_id = current

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def controller_lock(self) -> Iterator[None]:
        """A host lock covers side effects, including slow/lost provider replies."""
        lock_path = self.path.with_name(self.path.name + ".controller.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigurationError("another controller owns this deployment state") from exc
            yield
        finally:
            os.close(descriptor)

    def enroll(self, spec: dict[str, Any] | Binding) -> dict[str, Any]:
        binding = spec if isinstance(spec, Binding) else Binding.parse(spec)
        value = encode(binding.to_dict())
        with self.transaction():
            row = self.connection.execute("SELECT * FROM bindings WHERE repository=?", (binding.repository,)).fetchone()
            if row is None:
                self.connection.execute("INSERT INTO bindings VALUES (?, ?, 1, ?)", (binding.repository, value, encode({"ready": False, "reason": "not checked"})))
            elif row["spec"] != value:
                self.connection.execute("UPDATE bindings SET spec=?, revision=revision+1, validation=? WHERE repository=?", (value, encode({"ready": False, "reason": "configuration changed"}), binding.repository))
        return self.binding(binding.repository)

    def binding(self, name: str) -> dict[str, Any]:
        name = repository(name)
        row = self.connection.execute("SELECT * FROM bindings WHERE repository=?", (name,)).fetchone()
        if row is None:
            raise ConfigurationError("repository is not enrolled")
        return {**json.loads(row["spec"]), "revision": row["revision"], "validation": json.loads(row["validation"])}

    def bindings(self) -> list[dict[str, Any]]:
        return [self.binding(row[0]) for row in self.connection.execute("SELECT repository FROM bindings ORDER BY repository")]

    def change(self, name: str, operation: str, workers: int | None = None) -> dict[str, Any]:
        with self.transaction():
            current = self.binding(name)
            spec = {key: current[key] for key in Binding.__dataclass_fields__}
            if operation == "target":
                spec["desired_workers"] = count(workers, "desired_workers")
            elif operation == "pause":
                spec["paused"] = True
            elif operation == "resume":
                if not spec["enabled"]:
                    raise ConfigurationError("removed enrollment must be enrolled again before resume")
                spec.update(paused=False, desired_workers=count(workers, "desired_workers"))
            elif operation == "remove":
                spec["enabled"] = False
            else:
                raise ConfigurationError("unknown enrollment operation")
            value = encode(Binding.parse(spec).to_dict())
            if value != encode({key: current[key] for key in Binding.__dataclass_fields__}):
                self.connection.execute("UPDATE bindings SET spec=?, revision=revision+1 WHERE repository=?", (value, current["repository"]))
        return self.binding(name)

    def validation(self, name: str, ready: bool, reason: str, revision: int) -> None:
        self.connection.execute("UPDATE bindings SET validation=? WHERE repository=? AND revision=?", (encode({"ready": ready, "reason": reason}), name, revision))

    def action_budgets(self, maximum: int) -> dict[str, int]:
        """Share one tick's mutation budget, rotating small batches durably.

        Pending retries and new launches use the same per-repository credits.
        A repository outage therefore cannot monopolize another's admission.
        Unused credits expire at the end of the tick.
        """
        with self.transaction():
            live = {row["repository"] for row in self.workers() if not released(row["snapshot"])}
            names = [
                row["repository"] for row in self.bindings()
                if row["repository"] in live or (
                    row["validation"]["ready"] and row["enabled"] and not row["paused"]
                    and row["desired_workers"] > 0 and row["max_workers"] != 0
                )
            ]
            if not names:
                return {}
            row = self.connection.execute("SELECT value FROM metadata WHERE key='action_cursor'").fetchone()
            offset = int(row[0]) % len(names) if row else 0
            ordered = names[offset:] + names[:offset]
            each, extra = divmod(maximum, len(names))
            budgets = {name: each + int(index < extra) for index, name in enumerate(ordered)}
            self.connection.execute(
                "INSERT INTO metadata (key,value) VALUES ('action_cursor', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str((offset + extra) % len(names)),),
            )
        return budgets

    def workers(self, name: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM workers"
        params: tuple[Any, ...] = ()
        if name is not None:
            query += " WHERE repository=?"
            params = (name,)
        query += " ORDER BY repository, slot_id, generation"
        return [{**dict(row), "snapshot": json.loads(row["snapshot"])} for row in self.connection.execute(query, params)]

    def worker(self, launch_key: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM workers WHERE launch_key=?", (launch_key,)).fetchone()
        if row is None:
            raise ConfigurationError("unknown launch reservation")
        return {**dict(row), "snapshot": json.loads(row["snapshot"])}

    def reserve(self, binding: Binding, revision: int) -> dict[str, Any]:
        """Persist a stable logical slot/generation and key before provider I/O."""
        with self.transaction():
            current = self.binding(binding.repository)
            if current["revision"] != revision or not current["enabled"] or current["paused"]:
                raise ConfigurationError("enrollment changed before capacity reservation")
            rows = self.workers(binding.repository)
            occupied = {row["slot_id"] for row in rows if not released(row["snapshot"])}
            index = 0
            while f"slot-{index}" in occupied:
                index += 1
            slot = f"slot-{index}"
            generation = 1 + max((row["generation"] for row in rows if row["slot_id"] == slot), default=0)
            key = str(uuid.uuid4())
            data = {
                "schema_version": 1, "deployment_id": self.deployment_id,
                "repository": binding.repository, "slot_id": slot,
                "generation": generation, "launch_key": key, "profile": binding.profile,
                "worker_id": "", "state": "starting", "reason": "launch reserved",
                "termination_confirmed": False, "artifacts_captured": False,
                "admission_open": False, "active_executions": [],
            }
            self.connection.execute("INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, 'reserve', ?)", (key, binding.repository, slot, generation, binding.profile, encode(data), key))
        return self.worker(key)

    def dispatch(self, key: str, max_workers: int) -> dict[str, Any]:
        """Linearize launch permission against the current enrollment policy."""
        with self.transaction():
            row = self.worker(key)
            if row["pending_action"] != "reserve":
                return row
            current = self.binding(row["repository"])
            binding = Binding.parse({field: current[field] for field in Binding.__dataclass_fields__})
            active = [item for item in self.workers() if not released(item["snapshot"])]
            own_count = sum(item["repository"] == row["repository"] for item in active)
            permitted = binding.profile == row["profile"] and binding.permitted_target > 0 and own_count <= binding.permitted_target and len(active) <= max_workers
            if not permitted:
                data = dict(row["snapshot"])
                data.update(state="failed", reason="cancelled before dispatch by current enrollment policy", termination_confirmed=True, artifacts_captured=True)
                self.save_observation(key, data)
            elif current["validation"]["ready"]:
                self.connection.execute("UPDATE workers SET pending_action='ensure' WHERE launch_key=?", (key,))
        return self.worker(key)

    def save_observation(self, key: str, data: dict[str, Any], clear_action: bool = True) -> None:
        if clear_action:
            self.connection.execute("UPDATE workers SET snapshot=?, pending_action=NULL, action_key=NULL WHERE launch_key=?", (encode(data), key))
        else:
            self.connection.execute("UPDATE workers SET snapshot=? WHERE launch_key=?", (encode(data), key))

    def uncertainty(self, key: str, reason: str) -> None:
        row = self.worker(key)
        data = dict(row["snapshot"])
        data.update(state="unknown", reason=reason, termination_confirmed=False, artifacts_captured=False, admission_open=False)
        self.save_observation(key, data, clear_action=False)

    def action(self, key: str, operation: str) -> dict[str, Any]:
        if operation not in {"drain", "stop"}:
            raise ConfigurationError("unsupported runtime operation")
        with self.transaction():
            row = self.worker(key)
            cancel_known_launch = (
                row["pending_action"] == "ensure"
                and operation == "drain"
                and bool(row["snapshot"]["worker_id"])
            )
            if row["pending_action"] is not None and row["pending_action"] != operation and not cancel_known_launch:
                raise ConfigurationError("another runtime operation remains unresolved")
            if row["pending_action"] is None or cancel_known_launch:
                # Retirement supersedes this incarnation's unresolved launch.
                # Keep its immutable launch_key and reservation; only a scoped
                # runtime cancellation/termination receipt may release it.
                request_key = str(uuid.uuid4())
                self.connection.execute("UPDATE workers SET pending_action=?, action_key=? WHERE launch_key=?", (operation, request_key, key))
        return self.worker(key)
