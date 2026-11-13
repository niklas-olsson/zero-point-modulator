from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from zpm.cli import main
from zpm.controller import Controller
from zpm.models import Binding, ConfigurationError, ProviderConfig, released
from zpm.provider import OpenJulesProvider
from zpm.registry import configuration
from zpm.store import LEGACY_COLUMNS, Store

from .support import FixtureProvider


def config(name="host-a"):
    return {
        "schema_version": 2, "max_workers": 4, "timeout_seconds": 5,
        "destinations": [{
            "destination_id": name, "journal_incarnation": "1" * 32,
            "endpoint": "https://worker.example/approved/runtime", "token_ref": "env:OJ_PRIVATE_REF",
            "max_workers": 2, "enabled": False,
        }],
        "profiles": [{
            "destination_id": name, "repository": "Owner/Project", "profile": "fixture",
            "policy_binding_id": "2" * 32, "policy_binding_version": 1,
        }],
    }


def binding(workers=0):
    return {"schema_version": 1, "repository": "owner/project", "profile": "fixture", "desired_workers": workers}


def old_store_class():
    # Exact published Store source at a2a9f9b, not a rewritten imitation of the
    # old SELECT/initialization behavior. It imports the unchanged v1 models.
    path = Path(__file__).parent / "fixtures" / "legacy_store_a2a9f9b.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != "2e2e87bc0b43cd14f088b93999d29846f595ca61eb5c549dd7398ced95046376":
        raise AssertionError("the frozen pre-v2 Store fixture changed")
    spec = importlib.util.spec_from_file_location("zpm._legacy_store_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Store


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "state.sqlite3"
        self.backup = self.root / "before-v2.sqlite3"

    def open_legacy(self, history=False):
        store = Store(self.path, "deployment-a")
        self.addCleanup(store.close)
        if history:
            controller = Controller(store, FixtureProvider(), 4)
            for _ in range(2):
                store.enroll(binding(1))
                controller.tick()
                store.change("owner/project", "target", 0)
                controller.tick()
            self.assertTrue(all(released(row["snapshot"]) for row in store.workers()))
        else:
            store.enroll(binding())
        return store

    def open_registry(self, data=None):
        store = Store.initialize_registry(self.path, data or config(), "deployment-a")
        self.addCleanup(store.close)
        return store

    def logical_rows(self, store):
        return {
            table: [tuple(row) for row in store.connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY 1")]
            for table, columns in LEGACY_COLUMNS.items()
        }

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--state", str(self.path), *arguments])
        return code, json.loads(stdout.getvalue() or stderr.getvalue())

    def test_strict_configuration_and_shared_transport_validation(self):
        invalid = []
        for field, value in (("schema_version", True), ("schema_version", 1), ("max_workers", True),
                             ("timeout_seconds", float("nan")), ("timeout_seconds", float("inf")),
                             ("destinations", {}), ("profiles", {})):
            data = config()
            data[field] = value
            invalid.append(data)
        for field, value in (("enabled", True), ("enabled", 0), ("destination_id", "a:b"),
                             ("journal_incarnation", "A" * 32), ("token_ref", "actual-token"),
                             ("endpoint", "http://localhost:8000"),
                             ("endpoint", "https://worker.invalid%3A0"),
                             ("endpoint", "https://worker.invalid/\u202e")):
            data = config()
            data["destinations"][0][field] = value
            invalid.append(data)
        for field, value in (("policy_binding_version", True), ("policy_binding_version", 2),
                             ("policy_binding_id", "f" * 31), ("destination_id", "unenrolled")):
            data = config()
            data["profiles"][0][field] = value
            invalid.append(data)
        for kind in ("destinations", "profiles"):
            data = config()
            data[kind].append(copy.deepcopy(data[kind][0]))
            invalid.append(data)
        extra = config()
        extra["portable_manifest"] = {"unadopted": True}
        invalid.append(extra)
        with patch("zpm.provider.os.environ.get", side_effect=AssertionError("credential lookup")) as token:
            for data in invalid:
                for parse in (configuration, lambda value: Store.initialize_registry(self.path, value)):
                    with self.subTest(data=data, entry=parse), self.assertRaises(ConfigurationError):
                        parse(data)
            token.assert_not_called()
        self.assertFalse(self.path.exists())
        normalized = configuration(config())
        self.assertEqual(normalized["profiles"][0]["repository"], "github:github.com/owner/project")

    def test_fresh_registry_status_is_disabled_private_and_durable(self):
        with patch("zpm.store.os.fsync", wraps=os.fsync) as sync:
            store = self.open_registry()
        self.assertEqual(sync.call_count, 2)
        report = store.registry_status()
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["registry_revision"], 1)
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["ready"])
        self.assertFalse(report["remote_access_checked"])
        self.assertEqual(report["legacy_history_rows"], 0)
        self.assertNotIn("worker.example", json.dumps(report))
        self.assertNotIn("OJ_PRIVATE_REF", json.dumps(report))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with Store(self.path, "deployment-a", create=False) as reopened:
            self.assertEqual(reopened.registry_status(), report)

    def test_explicit_import_retains_omissions_and_all_changed_revisions(self):
        store = self.open_registry()
        self.assertEqual(store.import_registry(config())["registry_revision"], 1)
        second = config("host-b")
        report = store.import_registry(second)
        self.assertEqual([item["destination_id"] for item in report["destinations"]], ["host-a", "host-b"])
        changed = config()
        changed["destinations"][0].update(endpoint="https://new-worker.example/prefix", token_ref="env:ROTATED_REF")
        changed["profiles"][0]["policy_binding_id"] = "3" * 32
        report = store.import_registry(changed)
        self.assertEqual(report["registry_revision"], 3)
        self.assertTrue(all(item["enabled"] is False and item["ready"] is False for item in report["destinations"]))
        retained = [json.loads(row[0]) for row in store.connection.execute("SELECT specification FROM destinations WHERE destination_id='host-a' ORDER BY revision")]
        self.assertEqual([item["token_ref"] for item in retained], ["env:OJ_PRIVATE_REF", "env:ROTATED_REF"])
        profiles = [json.loads(row[0]) for row in store.connection.execute("SELECT specification FROM destination_profiles WHERE destination_id='host-a' ORDER BY revision")]
        self.assertEqual([item["policy_binding_id"] for item in profiles], ["2" * 32, "3" * 32])
        self.assertEqual(store.workers(), [])

    def test_import_identity_changes_roll_back_the_entire_batch(self):
        store = self.open_registry()
        before = list(store.connection.iterdump())
        invalid = []
        changed_incarnation = config()
        changed_incarnation["destinations"][0]["journal_incarnation"] = "4" * 32
        invalid.append(changed_incarnation)
        reused_policy = config()
        reused_policy["profiles"][0]["profile"] = "another-profile"
        invalid.append(reused_policy)
        same_batch = config("new-host")
        same_batch["profiles"].append(same_batch["profiles"][0] | {"profile": "conflicting-alias"})
        invalid.append(same_batch)
        enabled = config()
        enabled["destinations"][0]["enabled"] = True
        invalid.append(enabled)
        for data in invalid:
            data["max_workers"] = 9
            with self.subTest(data=data), self.assertRaises(ConfigurationError):
                store.import_registry(data)
            self.assertEqual(list(store.connection.iterdump()), before)

    def test_initialization_does_not_overwrite_existing_state_or_symlink(self):
        existing = self.root / "existing"
        existing.write_text("operator data")
        self.path.symlink_to(existing)
        with self.assertRaises(ConfigurationError):
            Store.initialize_registry(self.path, config())
        self.assertEqual(existing.read_text(), "operator data")
        self.path.unlink()
        store = self.open_legacy()
        before = list(store.connection.iterdump())
        with self.assertRaises(ConfigurationError):
            Store.initialize_registry(self.path, config())
        self.assertEqual(list(store.connection.iterdump()), before)

    def test_missing_partial_and_unknown_formats_are_not_initialized(self):
        with self.assertRaises(ConfigurationError):
            Store(self.path, create=False)
        self.assertFalse(self.path.exists())
        for version, statement in ((0, None), (2, "CREATE TABLE metadata (key TEXT, value_v2 TEXT)"), (9, None)):
            with self.subTest(version=version):
                with sqlite3.connect(self.path) as connection:
                    if statement:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version={version}")
                    before = list(connection.iterdump())
                with self.assertRaises(ConfigurationError):
                    Store(self.path)
                with sqlite3.connect(self.path) as connection:
                    self.assertEqual(list(connection.iterdump()), before)
                self.path.unlink()

    def test_missing_identity_and_malformed_registry_records_fail_without_repair(self):
        ambiguous = json.dumps(configuration(config())["destinations"][0]).replace(
            '"enabled": false', '"enabled": true, "enabled": false')
        mutations = [
            ("DELETE FROM metadata", ()),
            ("DELETE FROM registry_revisions", ()),
            ("DROP TABLE destination_profiles", ()),
            ("UPDATE destinations SET specification='{}'", ()),
            ("UPDATE destination_profiles SET destination_id='unregistered'", ()),
            ("UPDATE destinations SET specification=?", (ambiguous,)),
            ("UPDATE registry_revisions SET policy=?", ('{"schema_version":2,"max_workers":1,"timeout_seconds":NaN}',)),
        ]
        for index, (statement, parameters) in enumerate(mutations):
            with self.subTest(statement=statement):
                self.path = self.root / f"invalid-{index}.sqlite3"
                store = self.open_registry()
                store.connection.execute(statement, parameters)
                before = list(store.connection.iterdump())
                with self.assertRaises(ConfigurationError):
                    store.registry_status()
                with self.assertRaises(ConfigurationError):
                    Store(self.path, create=False)
                self.assertEqual(list(store.connection.iterdump()), before)

    def test_migration_preserves_released_history_and_verified_wal_backup(self):
        store = self.open_legacy(history=True)
        before = self.logical_rows(store)
        before_dump = list(store.connection.iterdump())
        self.assertGreater(Path(str(self.path) + "-wal").stat().st_size, 0)
        report = store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(report["deployment_id"], "deployment-a")
        self.assertEqual(report["legacy_history_rows"], 2)
        for table, rows in before.items():
            columns = LEGACY_COLUMNS[table]
            if table == "metadata":
                columns = ("key", "value_v2")
            actual = [tuple(row) for row in store.connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY 1")]
            self.assertEqual(actual, rows)
        self.assertTrue(all(row["protocol_version"] == 1 and row["destination_binding"] is None for row in store.workers()))
        with Store(self.backup, "deployment-a", create=False) as backup:
            self.assertEqual(backup.format_version, 1)
            self.assertEqual(list(backup.connection.iterdump()), before_dump)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_migration_refuses_targets_unreleased_work_and_pending_operations(self):
        store = self.open_legacy(history=True)
        original = self.logical_rows(store)
        with self.assertRaisesRegex(ConfigurationError, "offline shutdown"):
            store.migrate_registry(config(), self.backup)
        self.assertFalse(self.backup.exists())
        store.change("owner/project", "target", 1)
        store.change("owner/project", "pause")
        with self.assertRaisesRegex(ConfigurationError, "target"):
            store.migrate_registry(config(), self.backup, offline=True)
        store.change("owner/project", "target", 0)
        row = store.workers()[0]
        for update in ({"state": "unknown"}, {"artifacts_captured": False}, {"admission_open": True},
                       {"active_executions": ["unresolved"]}):
            with self.subTest(update=update):
                store.save_observation(row["launch_key"], row["snapshot"] | update)
                before = list(store.connection.iterdump())
                with self.assertRaisesRegex(ConfigurationError, "released history"):
                    store.migrate_registry(config(), self.backup, offline=True)
                self.assertEqual(list(store.connection.iterdump()), before)
                self.assertFalse(self.backup.exists())
        store.save_observation(row["launch_key"], row["snapshot"])
        store.action(row["launch_key"], "stop")
        with self.assertRaisesRegex(ConfigurationError, "unresolved operation"):
            store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(store.format_version, 1)
        self.assertEqual([row[0] for row in original["workers"]], [row["launch_key"] for row in sorted(store.workers(), key=lambda row: row["launch_key"])])

    def test_backup_overwrite_and_symlink_are_refused_before_conversion(self):
        store = self.open_legacy(history=True)
        before = list(store.connection.iterdump())
        self.backup.write_text("keep backup")
        with self.assertRaises(ConfigurationError):
            store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(self.backup.read_text(), "keep backup")
        self.backup.unlink()
        other = self.root / "other"
        other.write_text("keep target")
        self.backup.symlink_to(other)
        with self.assertRaises(ConfigurationError):
            store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(other.read_text(), "keep target")
        self.assertEqual(list(store.connection.iterdump()), before)

    def test_failed_conversion_rolls_back_schema_and_history(self):
        store = self.open_legacy(history=True)
        before = list(store.connection.iterdump())
        with patch("zpm.registry.import_configuration", side_effect=RuntimeError("fixture interruption")):
            with self.assertRaisesRegex(RuntimeError, "fixture interruption"):
                store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(store.format_version, 1)
        self.assertEqual(list(store.connection.iterdump()), before)
        with Store(self.backup, create=False) as backup:
            self.assertEqual(list(backup.connection.iterdump()), before)

    def test_backup_fsync_precedes_conversion_and_failure_keeps_legacy_state(self):
        store = self.open_legacy(history=True)
        before = list(store.connection.iterdump())
        original_sync = os.fsync
        observed = []

        def sync(descriptor, fail_directory=False):
            is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
            observed.append((is_directory, store.connection.execute("PRAGMA user_version").fetchone()[0]))
            if is_directory and fail_directory:
                raise OSError("fixture directory sync failure")
            return original_sync(descriptor)

        with patch("zpm.store.os.fsync", side_effect=lambda descriptor: sync(descriptor, True)):
            with self.assertRaisesRegex(OSError, "sync failure"):
                store.migrate_registry(config(), self.backup, offline=True)
        self.assertEqual(observed, [(False, 0), (True, 0)])
        self.assertEqual(list(store.connection.iterdump()), before)
        self.assertEqual(store.format_version, 1)
        observed.clear()
        with patch("zpm.store.os.fsync", side_effect=sync):
            store.migrate_registry(config(), self.root / "new-backup.sqlite3", offline=True)
        self.assertEqual(observed, [(False, 0), (True, 0)])
        self.assertEqual(store.format_version, 2)

    def test_actual_old_reader_refuses_converted_state_before_provider_creation(self):
        store = self.open_legacy(history=True)
        store.migrate_registry(config(), self.backup, offline=True)
        before = list(store.connection.iterdump())
        provider = Mock(side_effect=AssertionError("provider construction must be unreachable"))
        OldStore = old_store_class()
        old = OldStore.__new__(OldStore)
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such column: value"):
                old.__init__(self.path)
                provider(old)
        finally:
            old.close()
        provider.assert_not_called()
        self.assertEqual(list(store.connection.iterdump()), before)
        self.assertEqual(store.deployment_id, "deployment-a")

    def test_preexisting_old_writer_is_an_explicit_offline_migration_limit(self):
        store = self.open_legacy()
        OldStore = old_store_class()
        with OldStore(self.path) as old:
            store.migrate_registry(config(), self.backup, offline=True)
            # This deliberately violates --offline's operating precondition.
            # The old object never rereads metadata during configuration writes.
            old.enroll(binding(7))
            self.assertEqual(old.binding("owner/project")["desired_workers"], 7)
            with self.assertRaises(ConfigurationError):
                Controller(store, Mock(), 10)

    def test_new_code_objects_cannot_use_legacy_paths_after_migration(self):
        store = self.open_legacy(history=True)
        provider = Mock()
        controller = Controller(store, provider, 4)
        with Store(self.path, create=False) as migrator:
            migrator.migrate_registry(config(), self.backup, offline=True)
        before = list(store.connection.iterdump())
        operations = [
            lambda: Controller(store, provider, 4), controller.check, controller.plan, controller.tick,
            lambda: controller._tick(2), controller.run, lambda: controller._launch({}, True),
            lambda: controller._mutate({}, "stop"), lambda: controller._accept({}, {}),
            lambda: store.enroll(binding()), lambda: store.change("owner/project", "target", 1),
            lambda: store.validation("owner/project", True, "must not save", 1),
            lambda: store.action_budgets(2), lambda: store.reserve(Binding.parse(binding()), 1),
            lambda: store.dispatch("absent", 2), lambda: store.save_observation("absent", {}),
            lambda: store.uncertainty("absent", "must not save"), lambda: store.action("absent", "stop"),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(ConfigurationError, "not supported yet"):
                operation()
        self.assertEqual(provider.mock_calls, [])
        self.assertEqual(list(store.connection.iterdump()), before)

    def test_cli_offline_commands_and_remote_rejection_never_construct_provider(self):
        source = self.root / "registry.json"
        source.write_text(json.dumps(config()))
        with patch("zpm.cli.OpenJulesProvider", side_effect=AssertionError("unexpected provider")) as provider:
            code, report = self.cli("registry", "validate", str(source))
            self.assertEqual(code, 0)
            self.assertFalse(self.path.exists())
            code, report = self.cli("--deployment-id", "deployment-a", "registry", "init", str(source))
            self.assertEqual(code, 0)
            self.assertFalse(report["ready"])
            for arguments in (("registry", "import", str(source)), ("registry", "status"), ("plan",), ("status",)):
                with self.subTest(arguments=arguments):
                    code, report = self.cli(*arguments)
                    self.assertEqual(code, 0)
                    self.assertFalse(report["execution_enabled"])
            for operation in ("check", "reconcile"):
                code, report = self.cli(operation, "--provider", str(self.root / "deliberately-absent.json"))
                self.assertEqual(code, 2)
                self.assertIn("not supported yet", report["error"])
            code, report = self.cli("init")
            self.assertEqual(code, 2)
            self.assertIn("not supported yet", report["error"])
            provider.assert_not_called()

    def test_cli_migration_requires_offline_acknowledgment_and_keeps_identity(self):
        self.open_legacy(history=True)
        source = self.root / "registry.json"
        source.write_text(json.dumps(config()))
        arguments = ("registry", "migrate", str(source), "--backup", str(self.backup))
        code, report = self.cli(*arguments)
        self.assertEqual(code, 2)
        self.assertFalse(self.backup.exists())
        code, report = self.cli(*arguments, "--offline")
        self.assertEqual(code, 0)
        self.assertEqual(report["deployment_id"], "deployment-a")
        self.assertEqual(report["legacy_history_rows"], 2)

    def test_cli_rejects_ambiguous_json_without_state_or_provider_access(self):
        source = self.root / "ambiguous.json"
        text = json.dumps(config())
        variants = [text.replace('"schema_version": 2', '"schema_version": 1, "schema_version": 2'),
                    text.replace('"enabled": false', '"enabled": true, "enabled": false'),
                    text.replace('"timeout_seconds": 5', '"timeout_seconds": NaN')]
        for raw in variants:
            with self.subTest(raw=raw), patch("zpm.cli.OpenJulesProvider") as provider:
                source.write_text(raw)
                code, report = self.cli("registry", "init", str(source))
                self.assertEqual(code, 2)
                self.assertIn("registry JSON", report["error"])
                self.assertFalse(self.path.exists())
                provider.assert_not_called()

    def test_direct_legacy_provider_rejects_v2_before_credentials_or_transport(self):
        provider = OpenJulesProvider(ProviderConfig("https://worker.example", "env:OJ_TOKEN", 2))
        with patch("zpm.provider.os.environ.get", side_effect=AssertionError("credential lookup")) as token, \
                patch("zpm.provider.subprocess.Popen", side_effect=AssertionError("transport")) as transport:
            for version in (2, True, None):
                with self.subTest(version=version), self.assertRaises(ConfigurationError):
                    provider.ensure({"schema_version": version})
            for key in ("destination_binding", "destination_id", "journal_incarnation", "policy_binding_id",
                        "policy_binding_version", "protocol_version"):
                with self.subTest(key=key), self.assertRaises(ConfigurationError):
                    provider.ensure({"schema_version": 1, key: None})
            with self.assertRaises(ConfigurationError):
                provider._request("GET", "/capabilities", query={"schema_version": 2})
            token.assert_not_called()
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
