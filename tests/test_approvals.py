from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zpm import approvals
from zpm.controller import Controller
from zpm.models import ConfigurationError
from zpm.portable_manifest import MAX_INTEGER, manifest_bytes, parse_approval
from zpm.store import Store

from .support import FixtureProvider
from .test_registry import binding, config, old_store_class


FIXTURES = Path(__file__).parent / "fixtures"
VECTORS = json.loads((FIXTURES / "portable-manifest-v1.json").read_text())["vectors"]


def manifest():
    return copy.deepcopy(VECTORS[0]["manifest"])


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def envelope(value=None, *, profile="fixture", policy="2" * 32):
    value = value if value is not None else manifest()
    return {
        "approval_version": 1, "deployment_id": "deployment-a",
        "repository": "github:github.com/owner/project", "profile": profile,
        "destination_binding": {
            "destination_id": "host-a", "journal_incarnation": "1" * 32,
            "policy_binding_id": policy, "policy_binding_version": 1,
        },
        "manifest": value, "execution_manifest_sha256": digest(encode(value)),
    }


class PublicManifestTests(unittest.TestCase):
    def test_independently_authored_canonical_literals_and_hashes(self):
        for vector in VECTORS:
            with self.subTest(vector=vector["name"]):
                expected = vector["canonical_utf8"].encode("utf-8")
                self.assertEqual(manifest_bytes(vector["manifest"]), expected)
                self.assertEqual(digest(expected), vector["sha256"])
                self.assertEqual(json.loads(expected), vector["manifest"])
                before = copy.deepcopy(vector["manifest"])
                parsed = parse_approval(encode(envelope(vector["manifest"])))
                self.assertEqual(parsed.manifest, expected)
                self.assertEqual(vector["manifest"], before)
        self.assertNotEqual(VECTORS[0]["sha256"], VECTORS[2]["sha256"])
        self.assertNotEqual(VECTORS[3]["sha256"], VECTORS[4]["sha256"])

    def test_every_object_is_closed_and_every_integer_rejects_boolean_aliases(self):
        full = VECTORS[1]["manifest"]

        def paths(item, path=()):
            if isinstance(item, dict):
                yield path
                for key, value in item.items():
                    yield from paths(value, path + (key,))

        for path in paths(full):
            for change in ("unknown", "missing"):
                item = copy.deepcopy(full)
                current = item
                for key in path:
                    current = current[key]
                if change == "unknown":
                    current["unadopted"] = None
                else:
                    current.pop(next(iter(current)))
                with self.subTest(path=path, change=change), self.assertRaises(ConfigurationError):
                    manifest_bytes(item)
            original = full
            for key in path:
                original = original[key]
            for key, value in original.items():
                if type(value) is not int:
                    continue
                for replacement in (True, False, -1, MAX_INTEGER + 1, 1.0):
                    item = copy.deepcopy(full)
                    current = item
                    for step in path:
                        current = current[step]
                    current[key] = replacement
                    with self.subTest(path=path + (key,), replacement=replacement), self.assertRaises(ConfigurationError):
                        manifest_bytes(item)

    def test_envelope_scope_binding_versions_and_digest_are_exact(self):
        changes = [
            (("approval_version",), True), (("approval_version",), 2),
            (("deployment_id",), " deployment-a"), (("profile",), "fixture "),
            (("repository",), "Owner/Project"), (("repository",), "github:github.com/Owner/Project"),
            (("destination_binding", "policy_binding_version"), True),
            (("destination_binding", "policy_binding_version"), 2),
            (("destination_binding", "destination_id"), "host:a"),
            (("destination_binding", "journal_incarnation"), "A" * 32),
            (("destination_binding", "policy_binding_id"), "0" * 31),
            (("execution_manifest_sha256",), "sha256:" + "0" * 64),
            (("execution_manifest_sha256",), "SHA256:" + "A" * 64),
        ]
        for path, value in changes:
            item = envelope()
            current = item
            for key in path[:-1]:
                current = current[key]
            current[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ConfigurationError):
                parse_approval(encode(item))
        for key in ("approval_version", "manifest", "destination_binding"):
            item = envelope()
            item.pop(key)
            with self.assertRaises(ConfigurationError):
                parse_approval(encode(item))
        item = envelope()
        item["private_endpoint"] = "must-not-enter-the-public-contract"
        with self.assertRaises(ConfigurationError):
            parse_approval(encode(item))
        item = envelope()
        item["destination_binding"]["new_version"] = 1
        with self.assertRaises(ConfigurationError):
            parse_approval(encode(item))

    def test_noncanonical_sets_unsupported_modes_and_scalar_values_are_refused(self):
        base = VECTORS[1]["manifest"]
        changes = [
            (("manifest_version",), 2), (("source", "repository"), "owner/project"),
            (("source", "base_commit"), "B" * 40), (("image", "config_digest"), "latest"),
            (("image", "variant"), "\ud800"), (("execution", "root_argv"), ["\udfff"]),
            (("execution", "guest_environment_refs"), ["env:SECRET"]),
            (("confinement", "privileged"), 0), (("confinement", "read_only_rootfs"), 1),
            (("confinement", "cap_drop"), ["ALL", "ALL"]),
            (("children", "capabilities"), ["😀", "\ue000"]),
            (("children", "capabilities"), ["a", "a"]),
            (("children", "protocol"), "bounded-children-v2"),
            (("children", "outcome_protocol"), "unknown-v1"),
            (("children", "public_discussion"), {}),
            (("children", "max_child_ms"), 60_001),
            (("model", "request_timeout_ms"), 120_001),
            (("model", "service_policy_id"), "https://private-provider.example"),
            (("model", "token_parameter"), "vendor_guess"),
            (("resources", "root_timeout_ms"), 1001),
            (("required_protocols",), list(reversed(base["required_protocols"]))),
            (("required_protocols",), sorted(base["required_protocols"] + ["unadopted-v1"])),
        ]
        for path, value in changes:
            item = copy.deepcopy(base)
            current = item
            for key in path[:-1]:
                current = current[key]
            current[path[-1]] = value
            before = copy.deepcopy(item)
            with self.subTest(path=path, value=value), self.assertRaises(ConfigurationError):
                manifest_bytes(item)
            self.assertEqual(item, before)

    def test_parser_refuses_ambiguous_non_json_and_nested_oversized_inputs(self):
        raw = encode(envelope())
        variants = [
            raw.replace(b'"approval_version":1', b'"approval_version":0,"approval_version":1'),
            raw.replace(b'"root_uid":1000', b'"root_uid":0,"root_uid":1000'),
            raw.replace(b'"root_uid":1000', b'"root_uid":NaN'),
            raw.replace(b'"root_uid":1000', b'"root_uid":1e3'),
            raw.replace(b'"root_uid":1000', b'"root_uid":Infinity'),
            raw.replace(b'"root_uid":1000', b'"root_uid":' + b'9' * 5000),
            raw.replace(b'"variant":null', b'"variant":"\\ud800"'),
            b'\xff', b'\xef\xbb\xbf' + raw, b'[' * 1500 + b'0' + b']' * 1500,
            b'{}' + b' ' * 131_071, b'', b'null',
        ]
        for candidate in variants:
            with self.subTest(length=len(candidate)), self.assertRaises(ConfigurationError):
                parse_approval(candidate)

    def test_exact_manifest_and_file_byte_bounds(self):
        item = manifest()
        item["execution"]["root_argv"] = ["x"]
        item["execution"]["root_argv"][0] += "x" * (65_536 - len(encode(item)))
        self.assertEqual(len(manifest_bytes(item)), 65_536)
        raw = encode(envelope(item))
        exact = raw + b' ' * (131_072 - len(raw))
        self.assertEqual(parse_approval(exact).raw, exact)
        with self.assertRaises(ConfigurationError):
            parse_approval(exact + b' ')
        item["execution"]["root_argv"][0] += "x"
        with self.assertRaises(ConfigurationError):
            manifest_bytes(item)


class OfflineApprovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "state.sqlite3"
        self.input = self.root / "approval.json"
        self.store = Store.initialize_registry(self.path, config(), "deployment-a")
        self.addCleanup(self.store.close)

    def write(self, value=None, *, raw=None):
        self.input.write_bytes(raw if raw is not None else encode(value if value is not None else envelope()))
        self.input.chmod(0o600)
        return self.input

    def dump(self):
        return list(self.store.connection.iterdump())

    def assert_unchanged_and_reopens(self, before):
        self.assertEqual(self.dump(), before)
        with Store(self.path, "deployment-a", create=False) as reopened:
            self.assertEqual(list(reopened.connection.iterdump()), before)
            self.assertFalse(reopened.registry_status()["ready"])

    def test_import_retains_exact_bytes_provenance_and_disabled_reopen(self):
        raw = (json.dumps(envelope(), indent=2) + "\n").encode()
        with patch("zpm.provider.OpenJulesProvider", side_effect=AssertionError("provider")), \
                patch("zpm.provider.os.environ.get", side_effect=AssertionError("credential lookup")):
            report = self.store.import_approval(self.write(raw=raw))
            self.assertEqual(report["registry_revision"], 1)
            self.assertEqual(report["approval_count"], 1)
            self.assertEqual(report["destinations"][0]["profiles"][0]["approval_status"], "approved-public-contract")
            self.assertFalse(report["execution_enabled"])
            self.assertFalse(report["ready"])
            self.assertFalse(report["remote_access_checked"])
            self.assertFalse(report["destinations"][0]["enabled"])
            self.assertNotIn("OJ_PRIVATE_REF", json.dumps(report))
            self.assertNotIn("worker.example", json.dumps(report))
            with Store(self.path, create=False) as reopened:
                self.assertEqual(reopened.registry_status(), report)
                result = reopened.inspect_approval("host-a", "2" * 32)
                self.assertEqual(result["approval"], envelope())
                self.assertEqual(result["provenance"]["input_sha256"], digest(raw))
                self.assertFalse(result["native_policy_checked"])
                row = reopened.connection.execute("SELECT envelope, manifest FROM destination_approvals").fetchone()
                self.assertEqual(tuple(row), (raw, VECTORS[0]["canonical_utf8"].encode()))

    def test_exact_reimport_has_no_sql_write_or_revision_and_keeps_first_provenance(self):
        self.store.import_approval(self.write())
        before = self.dump()
        changes = self.store.connection.total_changes
        wal = Path(str(self.path) + "-wal")
        wal_state = (wal.stat().st_size, wal.stat().st_mtime_ns)
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE}
        self.store.connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
        try:
            self.store.import_approval(self.write(raw=json.dumps(envelope(), indent=3).encode()))
        finally:
            self.store.connection.set_authorizer(None)
        self.assertEqual(self.store.connection.total_changes, changes)
        self.assertEqual((wal.stat().st_size, wal.stat().st_mtime_ns), wal_state)
        self.assert_unchanged_and_reopens(before)

    def test_scope_and_binding_mismatches_cannot_create_extension(self):
        before = self.dump()
        variants = []
        for name, value in (("deployment_id", "different"), ("profile", "different")):
            item = envelope()
            item[name] = value
            variants.append(item)
        item = envelope()
        item["repository"] = item["manifest"]["source"]["repository"] = "github:github.com/owner/different"
        item["execution_manifest_sha256"] = digest(encode(item["manifest"]))
        variants.append(item)
        for name, value in (("destination_id", "different"), ("journal_incarnation", "3" * 32), ("policy_binding_id", "3" * 32)):
            item = envelope()
            item["destination_binding"][name] = value
            variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ConfigurationError):
                self.store.import_approval(self.write(item))
            self.assert_unchanged_and_reopens(before)

    def test_changed_reapproval_conflicts_and_old_approvals_survive_new_bindings(self):
        self.store.import_approval(self.write())
        before = self.dump()
        changed = manifest()
        changed["image"]["config_digest"] = "sha256:" + "c" * 64
        with self.assertRaisesRegex(ConfigurationError, "changed reapproval"):
            self.store.import_approval(self.write(envelope(changed)))
        self.assert_unchanged_and_reopens(before)
        data = config()
        data["profiles"][0]["policy_binding_id"] = "3" * 32
        report = self.store.import_registry(data)
        self.assertFalse(report["approvals"][0]["matches_current_enrollment"])
        self.assertEqual(report["destinations"][0]["profiles"][0]["approval_status"], "enrolled-only")
        before = self.dump()
        self.store.import_approval(self.write())
        self.assert_unchanged_and_reopens(before)
        report = self.store.import_approval(self.write(envelope(policy="3" * 32)))
        self.assertEqual(report["approval_count"], 2)
        self.assertEqual([entry["matches_current_enrollment"] for entry in report["approvals"]], [False, True])
        self.assertEqual(self.store.inspect_approval("host-a", "2" * 32)["approval"], envelope())

    def test_a_new_approval_requires_the_current_binding_not_only_an_old_history_row(self):
        data = config()
        data["profiles"][0]["policy_binding_id"] = "3" * 32
        self.store.import_registry(data)
        before = self.dump()
        with self.assertRaisesRegex(ConfigurationError, "current enrollment"):
            self.store.import_approval(self.write())
        self.assert_unchanged_and_reopens(before)

    def test_first_import_sql_failures_roll_back_table_marker_and_record(self):
        before = self.dump()
        self.write()
        boundaries = ((sqlite3.SQLITE_CREATE_TABLE, "destination_approvals"),
                      (sqlite3.SQLITE_INSERT, "metadata"),
                      (sqlite3.SQLITE_INSERT, "destination_approvals"),
                      (sqlite3.SQLITE_TRANSACTION, "COMMIT"))
        for denied_action, denied_table in boundaries:
            with self.subTest(boundary=denied_table, action=denied_action):
                def authorizer(action, table, *_):
                    return sqlite3.SQLITE_DENY if (action, table) == (denied_action, denied_table) else sqlite3.SQLITE_OK
                self.store.connection.set_authorizer(authorizer)
                try:
                    with self.assertRaises(ConfigurationError):
                        self.store.import_approval(self.input)
                finally:
                    self.store.connection.set_authorizer(None)
                self.assert_unchanged_and_reopens(before)
                self.assertFalse(self.store.connection.in_transaction)

    def test_interruption_after_insert_rolls_back_initial_and_later_approvals(self):
        original = approvals.validate_records
        for expected_count in (1, 2):
            if expected_count == 2:
                self.store.import_approval(self.write())
                data = config()
                data["profiles"][0]["policy_binding_id"] = "3" * 32
                self.store.import_registry(data)
            before = self.dump()
            def interrupt(connection, deployment_id):
                if approvals.enabled(connection) and connection.execute("SELECT count(*) FROM destination_approvals").fetchone()[0] == expected_count:
                    raise KeyboardInterrupt("fixture interruption after insert")
                return original(connection, deployment_id)
            with patch("zpm.approvals.validate_records", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                self.store.import_approval(self.write(envelope(policy=("2" if expected_count == 1 else "3") * 32)))
            self.assert_unchanged_and_reopens(before)

    def test_file_type_mode_owner_size_and_stable_read_are_required(self):
        self.write()
        before = self.dump()
        for mode in (0o644, 0o400, 0o700):
            self.input.chmod(mode)
            with self.assertRaises(ConfigurationError):
                self.store.import_approval(self.input)
        self.input.chmod(0o600)
        with patch("zpm.approvals.os.geteuid", return_value=os.geteuid() + 1), self.assertRaises(ConfigurationError):
            self.store.import_approval(self.input)
        symlink = self.root / "symlink"
        symlink.symlink_to(self.input)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        for path in (symlink, fifo, self.root, self.root / "missing"):
            with self.subTest(path=path.name), self.assertRaises(ConfigurationError):
                self.store.import_approval(path)
        self.write(raw=b' ' * 131_073)
        with patch("zpm.approvals.os.read", side_effect=AssertionError("oversized file read")), self.assertRaises(ConfigurationError):
            self.store.import_approval(self.input)
        self.write()
        original_read = os.read
        def change(descriptor, size):
            result = original_read(descriptor, size)
            with self.input.open("ab") as stream:
                stream.write(b' ')
            return result
        with patch("zpm.approvals.os.read", side_effect=change), self.assertRaises(ConfigurationError):
            self.store.import_approval(self.input)
        self.assert_unchanged_and_reopens(before)

    def test_corrupt_feature_or_approval_history_is_never_repaired_on_open(self):
        self.store.import_approval(self.write())
        mutations = [
            ("DELETE FROM metadata WHERE key='offline_approval_schema'", ()),
            ("UPDATE metadata SET value_v2='2' WHERE key='offline_approval_schema'", ()),
            ("DROP TABLE destination_approvals", ()),
            ("ALTER TABLE destination_approvals ADD COLUMN unknown TEXT", ()),
            ("DELETE FROM destination_approvals", ()),
            ("UPDATE destination_approvals SET manifest=?", (b'{}',)),
            ("UPDATE destination_approvals SET envelope=?", (b'{}',)),
            ("UPDATE destination_approvals SET envelope=CAST(envelope AS TEXT)", ()),
            ("UPDATE destination_approvals SET provenance='{}'", ()),
            ("UPDATE destination_approvals SET profile_revision=999", ()),
            ("UPDATE destination_approvals SET registry_revision=999", ()),
            ("UPDATE destination_approvals SET destination_id='different'", ()),
            ("UPDATE destination_approvals SET approval_id=2", ()),
        ]
        for index, (statement, parameters) in enumerate(mutations):
            with self.subTest(statement=statement):
                target = self.root / f"corrupt-{index}.sqlite3"
                with sqlite3.connect(target) as connection:
                    self.store.connection.backup(connection)
                    connection.execute(statement, parameters)
                    connection.commit()
                    before = list(connection.iterdump())
                with self.assertRaises(ConfigurationError):
                    Store(target, create=False)
                with sqlite3.connect(target) as connection:
                    self.assertEqual(list(connection.iterdump()), before)

    def _seed_bounded_records(self, count, *, padded=False):
        data = config()
        data["profiles"] = [data["profiles"][0] | {"profile": f"p{index:03}", "policy_binding_id": f"{index + 10:032x}"}
                            for index in range(count + 1)]
        self.store.import_registry(data)
        items = [envelope(profile=profile["profile"], policy=profile["policy_binding_id"]) for profile in data["profiles"]]
        raws = [encode(item) for item in items]
        if padded:
            raws = [raw + b' ' * (131_072 - len(raw)) for raw in raws]
        self.store.import_approval(self.write(raw=raws[0]))
        canonical = VECTORS[0]["canonical_utf8"].encode()
        with self.store.transaction():
            for index in range(1, count):
                provenance = {"import_method": "owned-private-file/v1", "input_sha256": digest(raws[index]), "input_bytes": len(raws[index])}
                self.store.connection.execute("INSERT INTO destination_approvals VALUES (?, ?, ?, 2, 2, ?, ?, ?)", (
                    index + 1, "host-a", items[index]["destination_binding"]["policy_binding_id"],
                    raws[index], canonical, json.dumps(provenance),
                ))
        self.store.require_registry()  # Independently validate the complete persisted fixture.
        return raws

    def test_actual_256_record_bound_and_idempotence_at_capacity(self):
        self.assertEqual(approvals.MAX_APPROVALS, 256)
        raws = self._seed_bounded_records(256)
        before = self.dump()
        with self.assertRaisesRegex(ConfigurationError, "count or byte bound"):
            self.store.import_approval(self.write(raw=raws[-1]))
        self.store.import_approval(self.write(raw=raws[0]))
        self.assert_unchanged_and_reopens(before)

    def test_actual_16_mib_payload_bound_is_atomic(self):
        self.assertEqual(approvals.MAX_RETAINED_BYTES, 16 * 1024 * 1024)
        one_record = 131_072 + len(VECTORS[0]["canonical_utf8"].encode())
        count = approvals.MAX_RETAINED_BYTES // one_record
        self.assertLess(count, 256)
        raws = self._seed_bounded_records(count, padded=True)
        before = self.dump()
        with self.assertRaisesRegex(ConfigurationError, "count or byte bound"):
            self.store.import_approval(self.write(raw=raws[-1]))
        self.assert_unchanged_and_reopens(before)

    def test_actual_old_v2_reader_is_fenced_and_v1_reader_stays_fenced(self):
        path = FIXTURES / "legacy_store_c5fb355.py"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), "83e4ac89b2052cf6bbf7a1b1a88e7ee935f9fbd7244634d4a864064ff50900fe")
        spec = importlib.util.spec_from_file_location("zpm._legacy_approval_store_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with module.Store(self.path, create=False) as already_open:
            self.store.import_approval(self.write())
            before = self.dump()
            for operation in (already_open.registry_status, lambda: already_open.import_registry(config()),
                              lambda: module.Store(self.path, create=False)):
                with self.assertRaises(ConfigurationError):
                    operation()
        OldStore = old_store_class()
        old = OldStore.__new__(OldStore)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                old.__init__(self.path)
        finally:
            old.close()
        self.assert_unchanged_and_reopens(before)

    def test_approval_preserves_migrated_released_history_and_worker_keys(self):
        target = self.root / "legacy.sqlite3"
        with Store(target, "deployment-a") as store:
            controller = Controller(store, FixtureProvider(), 4)
            store.enroll(binding(1))
            controller.tick()
            store.change("owner/project", "target", 0)
            controller.tick()
            store.migrate_registry(config(), self.root / "legacy-backup.sqlite3", offline=True)
            before = {
                table: [tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}")]
                for table in ("bindings", "workers", "registry_revisions", "destinations", "destination_profiles")
            }
            original = store.workers()
            self.assertEqual(len(original), 1)
            store.import_approval(self.write())
            with Store(target, create=False) as reopened:
                for table, rows in before.items():
                    self.assertEqual([tuple(row) for row in reopened.connection.execute(f"SELECT * FROM {table}")], rows)
                self.assertEqual(reopened.workers(), original)
                self.assertEqual(reopened.registry_status()["legacy_history_rows"], 1)

    def test_cold_cli_import_status_and_execution_refusals_have_no_external_effects(self):
        self.write()
        script = '''
import os, sys
def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
        raise AssertionError("external effect: " + event)
    if event == "open" and "deliberately-absent-provider.json" in str(args[0]):
        raise AssertionError("provider input read")
sys.addaudithook(audit)
original_get = os.environ.get
def get(name, *default):
    if name == "OJ_PRIVATE_REF":
        raise AssertionError("credential reference resolution")
    return original_get(name, *default)
os.environ.get = get
from zpm.cli import main
raise SystemExit(main(sys.argv[1:]))
'''
        commands = [
            (["registry", "import-approval", str(self.input)], 0),
            (["registry", "approval", "host-a", "2" * 32], 0),
            (["registry", "status"], 0), (["status"], 0), (["plan"], 0),
            (["check", "--provider", "deliberately-absent-provider.json"], 2),
            (["reconcile", "--provider", "deliberately-absent-provider.json"], 2),
        ]
        for arguments, expected in commands:
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, "-B", "-c", script, "--state", str(self.path), *arguments],
                                        cwd=self.root, env=os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    report = json.loads(result.stdout)
                    self.assertFalse(report["ready"])
                    self.assertFalse(report["execution_enabled"])
                    self.assertFalse(report["remote_access_checked"])
                else:
                    self.assertIn("not supported yet", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
