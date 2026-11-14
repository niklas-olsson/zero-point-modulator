"""Local durable enrollment and runtime-operation journal. No task ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Binding, ConfigurationError, count, identifier, released, repository
from . import approvals, registry, destination_state


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sync_file_and_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


LEGACY_COLUMNS = {
    "metadata": ("key", "value"),
    "bindings": ("repository", "spec", "revision", "validation"),
    "workers": ("launch_key", "repository", "slot_id", "generation", "profile", "snapshot", "pending_action", "action_key"),
}


class Store:
    def __init__(self, path: str | Path, deployment_id: str | None = None, *, create: bool = True):
        self.path = Path(path).resolve()
        exists = self.path.exists()
        if not exists and not create:
            raise ConfigurationError("deployment state does not exist; explicit initialization is required")
        if not exists:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connect(create=not exists)
        try:
            if not exists:
                current = identifier(deployment_id, "deployment_id") if deployment_id else str(uuid.uuid4())
                with self.transaction():
                    self._create_base_tables()
                    self.connection.execute("INSERT INTO metadata VALUES ('deployment_id', ?)", (current,))
            version = self._format()
            column = "value" if version == 1 else "value_v2"
            row = self.connection.execute(f"SELECT {column} FROM metadata WHERE key='deployment_id'").fetchone()
            if row is None:
                raise ConfigurationError("deployment state is missing its original identity")
            self.deployment_id = identifier(row[0], "deployment_id")
            if deployment_id is not None and deployment_id != self.deployment_id:
                raise ConfigurationError("state belongs to a different deployment_id")
            os.chmod(self.path, 0o600)
        except BaseException:
            self.connection.close()
            raise

    def _connect(self, *, create: bool = False) -> None:
        try:
            self.connection = sqlite3.connect(
                self.path if create else self.path.as_uri() + "?mode=rw",
                uri=not create, timeout=5, isolation_level=None,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
        except sqlite3.Error:
            if hasattr(self, "connection"):
                self.connection.close()
            raise ConfigurationError("cannot open the existing deployment journal") from None

    def _create_base_tables(self, *, version: int = 1) -> None:
        column = "value" if version == 1 else "value_v2"
        self.connection.execute(f"CREATE TABLE metadata (key TEXT PRIMARY KEY, {column} TEXT NOT NULL)")
        self.connection.execute("""CREATE TABLE bindings (
                repository TEXT PRIMARY KEY, spec TEXT NOT NULL,
                revision INTEGER NOT NULL, validation TEXT NOT NULL
            )""")
        self.connection.execute("""CREATE TABLE workers (
                launch_key TEXT PRIMARY KEY, repository TEXT NOT NULL,
                slot_id TEXT NOT NULL, generation INTEGER NOT NULL,
                profile TEXT NOT NULL, snapshot TEXT NOT NULL,
                pending_action TEXT, action_key TEXT,
                UNIQUE(repository, slot_id, generation)
            )""")
        if version == 2:
            self._add_protocol_columns()

    def _add_protocol_columns(self) -> None:
        self.connection.execute("ALTER TABLE workers ADD COLUMN protocol_version INTEGER NOT NULL DEFAULT 1")
        self.connection.execute("ALTER TABLE workers ADD COLUMN destination_binding TEXT")

    def _format(self) -> int:
        try:
            stored_version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if stored_version not in {0, 2}:
                raise ConfigurationError("unsupported deployment journal format")
            expected = dict(LEGACY_COLUMNS)
            version = 1 if stored_version == 0 else 2
            if version == 2:
                expected["metadata"] = ("key", "value_v2")
                expected["workers"] += ("protocol_version", "destination_binding")
                expected.update(registry.REGISTRY_COLUMNS)
                if approvals.enabled(self.connection):
                    expected.update(approvals.APPROVAL_COLUMNS)
                if destination_state.enabled(self.connection):
                    expected["workers"] += destination_state.WORKER_COLUMNS
                    expected.update(destination_state.COLUMNS)
            tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if tables != set(expected):
                raise ConfigurationError("deployment journal has missing or unsupported tables")
            for table, columns in expected.items():
                actual = tuple(row[1] for row in self.connection.execute(f"PRAGMA table_info({table})"))
                if actual != columns:
                    raise ConfigurationError("deployment journal has missing or unsupported columns")
            identity_column = "value" if version == 1 else "value_v2"
            identity_row = self.connection.execute(f"SELECT {identity_column} FROM metadata WHERE key='deployment_id'").fetchone()
            if identity_row is None:
                raise ConfigurationError("deployment state is missing its original identity")
            identity = identifier(identity_row[0], "deployment_id")
            if hasattr(self, "deployment_id") and identity != self.deployment_id:
                raise ConfigurationError("deployment state changed its original identity")
            if version == 2:
                active = destination_state.enabled(self.connection)
                registry.validate_records(self.connection, active=active)
                approvals.validate_records(self.connection, identity)
                if active:
                    destination_state.validate_records(self.connection, identity)
                elif self.connection.execute("""SELECT 1 FROM workers WHERE protocol_version != 1
                        OR destination_binding IS NOT NULL LIMIT 1""").fetchone():
                    raise ConfigurationError("destination execution history is not supported by this offline version")
            return version
        except (sqlite3.Error, ValueError, TypeError):
            raise ConfigurationError("deployment journal format or registry records are invalid") from None

    @property
    def format_version(self) -> int:
        return self._format()

    def require_legacy(self) -> None:
        # Read the actual journal each time: another new-code Store object may
        # have performed the explicit offline transition since this one opened.
        if self._format() != 1:
            raise ConfigurationError(registry.OFFLINE_REASON)

    def require_registry(self) -> None:
        if self._format() != 2:
            raise ConfigurationError("destination registry requires explicit v2 initialization or migration")

    def require_active_destinations(self) -> None:
        self.require_registry()
        if not destination_state.enabled(self.connection):
            raise ConfigurationError(registry.OFFLINE_REASON)

    def require_capacity(self) -> None:
        if self._format() == 2:
            self.require_active_destinations()

    def activate_destinations(self, *, offline: bool) -> dict[str, Any]:
        destination_state.activate(self, offline=offline)
        _sync_file_and_directory(self.path)
        return destination_state.plan(self)

    @classmethod
    def initialize_registry(cls, path: str | Path, data: dict[str, Any], deployment_id: str | None = None) -> "Store":
        data = registry.configuration(data)
        current = identifier(deployment_id, "deployment_id") if deployment_id else str(uuid.uuid4())
        target = Path(path).absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise ConfigurationError("registry initialization requires a new state file") from None
        owned = os.fstat(descriptor)
        result = cls.__new__(cls)
        result.path = target.resolve()
        try:
            result._connect()
            with result.transaction():
                result._create_base_tables(version=2)
                result.connection.execute("INSERT INTO metadata VALUES ('deployment_id', ?)", (current,))
                registry.create_tables(result.connection)
                registry.import_configuration(result.connection, data)
                result.connection.execute("PRAGMA user_version=2")
            result.deployment_id = current
            result.require_registry()
            _sync_file_and_directory(result.path)
            return result
        except BaseException:
            if hasattr(result, "connection"):
                result.connection.close()
            if target.exists() and target.stat().st_ino == owned.st_ino and target.stat().st_dev == owned.st_dev:
                target.unlink()
            raise
        finally:
            os.close(descriptor)

    def registry_status(self) -> dict[str, Any]:
        self.require_registry()
        if destination_state.enabled(self.connection):
            return destination_state.plan(self)
        return approvals.annotate_status(self.connection, registry.status(self.connection, self.deployment_id))

    def import_approval(self, path: str | Path) -> dict[str, Any]:
        self.require_registry()
        approval = approvals.read_private_file(path)
        try:
            with self.controller_lock(), self.transaction():
                self.require_registry()
                approvals.import_record(self.connection, approval, self.deployment_id)
        except sqlite3.Error:
            raise ConfigurationError("offline approval import could not commit its complete record") from None
        return self.registry_status()

    def inspect_approval(self, destination_id: str, policy_binding_id: str) -> dict[str, Any]:
        self.require_registry()
        return approvals.inspect_record(self.connection, destination_id, policy_binding_id)

    def import_registry(self, data: dict[str, Any]) -> dict[str, Any]:
        self.require_registry()
        active = destination_state.enabled(self.connection)
        data = registry.configuration(data, active=active)
        with self.controller_lock(), self.transaction():
            self.require_registry()
            registry.import_configuration(self.connection, data, active=active)
        return self.registry_status()

    def _migration_ready(self) -> None:
        self.require_legacy()
        if any(row["desired_workers"] != 0 for row in self.bindings()):
            raise ConfigurationError("migration requires every repository target to be zero")
        if any(not released(row["snapshot"]) or row["pending_action"] is not None or row["action_key"] is not None
               for row in self.workers()):
            raise ConfigurationError("migration requires released history with no unresolved operation")

    def _backup_new(self, path: str | Path) -> None:
        target = Path(path).absolute()
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise ConfigurationError("migration backup must be a new nonsymlink file in an existing directory") from None
        os.close(descriptor)
        backup = sqlite3.connect(target)
        try:
            self.connection.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ConfigurationError("migration backup integrity check failed")
            def digest(connection: sqlite3.Connection) -> bytes:
                result = hashlib.sha256()
                for statement in connection.iterdump():
                    result.update(statement.encode("utf-8") + b"\n")
                return result.digest()
            if digest(backup) != digest(self.connection):
                raise ConfigurationError("migration backup does not match the original journal")
        except sqlite3.Error:
            raise ConfigurationError("cannot create and verify the migration backup") from None
        finally:
            backup.close()
        _sync_file_and_directory(target)

    def migrate_registry(self, data: dict[str, Any], backup: str | Path, *, offline: bool = False) -> dict[str, Any]:
        data = registry.configuration(data)
        if offline is not True:
            raise ConfigurationError("migration requires explicit offline shutdown of all old controllers and configuration writers")
        with self.controller_lock():
            self._migration_ready()
            self._backup_new(backup)
            with self.transaction():
                self._migration_ready()
                self.connection.execute("ALTER TABLE metadata RENAME COLUMN value TO value_v2")
                self._add_protocol_columns()
                registry.create_tables(self.connection)
                registry.import_configuration(self.connection, data)
                self.connection.execute("PRAGMA user_version=2")
        return self.registry_status()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

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
        self.require_capacity()
        binding = spec if isinstance(spec, Binding) else Binding.parse(spec)
        value = encode(binding.to_dict())
        with self.transaction():
            self.require_capacity()
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
        self.require_capacity()
        with self.transaction():
            self.require_capacity()
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

    def validation(self, name: str, ready: bool, reason: str, revision: int, diagnostic: dict[str, Any] | None = None) -> None:
        self.require_legacy()
        observation = {"ready": ready, "reason": reason}
        if diagnostic is not None:
            observation["diagnostic"] = diagnostic
        self.connection.execute("UPDATE bindings SET validation=? WHERE repository=? AND revision=?", (encode(observation), name, revision))

    def action_budgets(self, maximum: int) -> dict[str, int]:
        """Share one tick's mutation budget, rotating small batches durably.

        Pending retries and new launches use the same per-repository credits.
        A repository outage therefore cannot monopolize another's admission.
        Unused credits expire at the end of the tick.
        """
        self.require_legacy()
        with self.transaction():
            self.require_legacy()
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
        self.require_legacy()
        with self.transaction():
            self.require_legacy()
            current = self.binding(binding.repository)
            if current["revision"] != revision or not current["enabled"] or current["paused"]:
                raise ConfigurationError("enrollment changed before capacity reservation")
            slot, generation = self.next_slot(binding.repository)
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

    def next_slot(self, repository: str) -> tuple[str, int]:
        rows = self.workers(repository)
        occupied = {row["slot_id"] for row in rows if not released(row["snapshot"])}
        index = 0
        while f"slot-{index}" in occupied:
            index += 1
        slot = f"slot-{index}"
        return slot, 1 + max((row["generation"] for row in rows if row["slot_id"] == slot), default=0)

    def dispatch(self, key: str, max_workers: int) -> dict[str, Any]:
        """Linearize launch permission against the current enrollment policy."""
        self.require_legacy()
        with self.transaction():
            self.require_legacy()
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
        self.require_capacity()
        if clear_action:
            self.connection.execute("UPDATE workers SET snapshot=?, pending_action=NULL, action_key=NULL WHERE launch_key=?", (encode(data), key))
        else:
            self.connection.execute("UPDATE workers SET snapshot=? WHERE launch_key=?", (encode(data), key))

    def uncertainty(self, key: str, reason: str) -> None:
        self.require_capacity()
        row = self.worker(key)
        data = dict(row["snapshot"])
        data.update(state="unknown", reason=reason, termination_confirmed=False, artifacts_captured=False, admission_open=False)
        self.save_observation(key, data, clear_action=False)

    def action(self, key: str, operation: str) -> dict[str, Any]:
        self.require_capacity()
        if operation not in {"drain", "stop"}:
            raise ConfigurationError("unsupported runtime operation")
        with self.transaction():
            self.require_capacity()
            row = self.worker(key)
            cancel_known_launch = (
                row["pending_action"] in {"ensure", "ensure-retry"}
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
