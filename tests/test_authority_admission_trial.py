"""Admission evidence regressions; companion journals here are explicit fixtures."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_authority_admission import (
        AUTHORITY_REASON, CONTROLLER_REASON, RUNTIME_REASON, RECORD_KINDS,
        canonical, cleanup_phase, completed_reconcile, expected_error, journal_paths,
        no_policy_command, owner_probe_token, read_histories, require_controller_plan,
        require_empty_snapshot, require_idle_resources, require_runtime_capabilities, stop_owned,
    )
    from check_stack import Client
finally:
    sys.path.pop(0)

from zpm.controller import Controller
from zpm.deployment import _command, _environment, _prepare_files
from zpm.models import Binding, OPERATIONS
from zpm.store import Store


# These literal fixture schemas follow pinned component sources. The witness's
# schema map is intentionally not used to manufacture a matching database.
COMPANION_DDL = {
    "authority": """
        CREATE TABLE records(kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,id));
        CREATE TABLE artifacts(id TEXT PRIMARY KEY,content BLOB NOT NULL);
        CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,time REAL NOT NULL,kind TEXT NOT NULL,record_id TEXT NOT NULL);
    """,
    "lifecycle": """
        CREATE TABLE managed_workers(worker_id TEXT PRIMARY KEY,deployment_id TEXT NOT NULL,repository TEXT NOT NULL,
            slot_id TEXT NOT NULL,generation INTEGER NOT NULL,spec TEXT NOT NULL,runtime_id TEXT,state TEXT NOT NULL,
            reason TEXT NOT NULL,draining INTEGER NOT NULL DEFAULT 0,termination_confirmed INTEGER NOT NULL DEFAULT 0,
            artifacts_captured INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE worker_operations(deployment_id TEXT NOT NULL,repository TEXT NOT NULL,request_key TEXT NOT NULL,
            action TEXT NOT NULL,worker_id TEXT NOT NULL,payload TEXT NOT NULL,completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE worker_executions(worker_id TEXT NOT NULL,execution_id TEXT NOT NULL,parent_id TEXT,
            termination_confirmed INTEGER NOT NULL DEFAULT 0,artifacts_captured INTEGER NOT NULL DEFAULT 0);
    """,
    "runtime": """
        CREATE TABLE profiles(deployment_id TEXT NOT NULL,repository TEXT NOT NULL,name TEXT NOT NULL,fingerprint TEXT NOT NULL);
        CREATE TABLE child_profiles(deployment_id TEXT NOT NULL,repository TEXT NOT NULL,name TEXT NOT NULL,fingerprint TEXT);
        CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT NOT NULL,profile_fingerprint TEXT NOT NULL,container_id TEXT,
            create_dispatched INTEGER NOT NULL DEFAULT 0,start_dispatched INTEGER NOT NULL DEFAULT 0,
            start_confirmed INTEGER NOT NULL DEFAULT 0,retired INTEGER NOT NULL DEFAULT 0,receipt TEXT,
            creation_version INTEGER NOT NULL DEFAULT 0,volume_creates INTEGER NOT NULL DEFAULT 0,
            volume_create_acks INTEGER NOT NULL DEFAULT 0,volume_transport TEXT,volume_identity TEXT,
            container_creates INTEGER NOT NULL DEFAULT 0,container_create_acks INTEGER NOT NULL DEFAULT 0,
            container_transport TEXT,cleanup TEXT);
        CREATE TABLE attempts(worker_id TEXT PRIMARY KEY,attempt_id TEXT UNIQUE NOT NULL,input_sha256 TEXT NOT NULL,
            seconds REAL NOT NULL,deadline REAL NOT NULL,exec_id TEXT,exec_dispatched INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0,exit_code INTEGER);
    """,
    "dispatcher": """
        CREATE TABLE deliveries(worker_id TEXT PRIMARY KEY,spec TEXT NOT NULL,task_id TEXT NOT NULL,
            request_key TEXT NOT NULL,phase TEXT NOT NULL,binding TEXT,artifacts TEXT NOT NULL DEFAULT '{}',
            reason TEXT NOT NULL DEFAULT '');
    """,
    "model": """
        CREATE TABLE policies(scope TEXT PRIMARY KEY,fingerprint TEXT NOT NULL);
        CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT NOT NULL,policy_digest TEXT NOT NULL,
            attempt_id TEXT,deadline REAL,boot_id TEXT,revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE calls(worker_id TEXT NOT NULL,attempt_id TEXT NOT NULL,request_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,reserved_tokens INTEGER NOT NULL,status TEXT NOT NULL,response TEXT);
    """,
}


class GeneratedAdmissionFixture(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "installation"
        self.bundle.mkdir(mode=0o700)
        self.scope = {"deployment_id": "admission-deployment", "repository": "github:github.com/trial/admission"}
        self.profile = "approved-model-profile"
        self.configuration = {
            "schema_version": 1, "deployment_id": self.scope["deployment_id"], "max_workers": 1,
            "binding": Binding.parse({"schema_version": 1, "repository": self.scope["repository"],
                                      "profile": self.profile, "desired_workers": 0}).to_dict(),
            "sources": {name: {"checkout": str(self.root / name), "revision": "1" * 40}
                        for name in ("zpm", "openjules", "polyphony")},
            "source": {"checkout": str(self.root / "source"), "revision": "a" * 40},
            "image": "sha256:" + "b" * 64, "command": ["python3", "approved.py"],
            "ports": {"polyphony": 8766, "openjules": 8000}, "environment_refs": {},
            "runtime": {"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000,
                        "pids_limit": 128, "max_seconds": 90, "max_archive_bytes": 16777216},
            "policy": {"checks": ["tests"], "reviewers": ["operator"], "max_attempts": 1,
                       "budget_seconds": 120, "attempt_seconds": 60, "capabilities": ["python"]},
            "model_policy": {"endpoint": "http://127.0.0.1:9000/v1/chat/completions",
                             "model": "fixture", "token_ref": "env:ADMISSION_TEST_PROVIDER",
                             "max_calls": 4, "max_output_tokens": 1024, "max_total_output_tokens": 4096,
                             "request_timeout_seconds": 5},
        }
        marker = self.bundle / "preparation.json"
        marker.write_text("{}\n")
        marker.chmod(0o600)
        _prepare_files(self.configuration, self.bundle, self.bundle)
        config = json.loads((self.bundle / "config/openjules.json").read_text())
        state = Path(config["state_directory"])
        # Actual build_app appends runtime/dispatcher/models under this state.
        self.paths = {
            "controller": self.bundle / "state/zpm.sqlite3",
            "authority": self.bundle / "state/polyphony/authority.sqlite3",
            "lifecycle": Path(config["lifecycle_database"]),
            "runtime": state / "runtime/journal.sqlite3",
            "dispatcher": state / "dispatcher/dispatch.sqlite3",
            "model": state / "models/inference.sqlite3",
        }
        self.assertEqual(self.paths["runtime"], self.bundle / "state/openjules/runtime/runtime/journal.sqlite3")
        self.connections = {}
        for name, path in self.paths.items():
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            connection.execute("PRAGMA journal_mode=WAL")
            if name != "controller":
                connection.executescript(COMPANION_DDL[name])
            self.connections[name] = connection
        key = (self.scope["deployment_id"], self.scope["repository"], self.profile)
        runtime = self.connections["runtime"]
        runtime.execute("INSERT INTO profiles VALUES(?,?,?,?)", (*key, "f" * 64))
        runtime.execute("INSERT INTO child_profiles VALUES(?,?,?,NULL)", key)
        runtime.commit()
        model = self.connections["model"]
        model.execute("INSERT INTO policies VALUES(?,?)", (canonical(key), "e" * 64))
        model.commit()

    def histories(self, expected=None):
        return read_histories(self.bundle, self.configuration, self.scope, self.profile, expected)

    def capabilities(self, ready=False):
        return {"schema_version": 1, **self.scope, "profile": self.profile, "operations": sorted(OPERATIONS),
                "profile_supported": True, "repository_access": ready, "managed_dispatch": ready,
                "reason": "ready" if ready else RUNTIME_REASON}

    def blocked_plan(self):
        capability = self.capabilities()

        class UnreadyProvider:
            def capabilities(self, *_):
                return capability

            def ensure(self, *_):
                raise AssertionError("an unready fixture unexpectedly dispatched")

        with Store(self.paths["controller"], self.scope["deployment_id"]) as store:
            store.change(self.scope["repository"], "target", 1)
            return Controller(store, UnreadyProvider(), max_workers=1).tick()

    def test_generated_paths_observe_committed_wal_and_reject_missing_schema(self):
        self.assertEqual(journal_paths(self.bundle, self.configuration), self.paths)
        self.histories()
        runtime = self.connections["runtime"]
        runtime.execute("INSERT INTO workers(worker_id,spec,profile_fingerprint,retired) VALUES('past','{}',?,1)",
                        ("f" * 64,))
        runtime.commit()
        # A retained terminal allocation in committed WAL cannot hide behind zero live capacity.
        with self.assertRaises(AssertionError):
            self.histories()
        runtime.execute("DELETE FROM workers")
        runtime.commit()
        self.histories()
        runtime.execute("ALTER TABLE attempts ADD COLUMN unexpected TEXT")
        runtime.commit()
        with self.assertRaises(AssertionError):
            self.histories()

    def test_missing_symlink_or_wrong_configured_journal_never_becomes_empty_evidence(self):
        self.histories()
        original = self.paths["runtime"]
        saved = original.with_name("retained.sqlite3")
        self.connections["runtime"].close()
        original.rename(saved)
        with self.assertRaises(AssertionError):
            self.histories()

        self.assertFalse(original.exists(), "witness must not create a missing journal")
        original.symlink_to(saved)
        with self.assertRaises(AssertionError):
            self.histories()
        original.unlink()
        saved.rename(original)
        config_path = self.bundle / "config/openjules.json"
        config = json.loads(config_path.read_text())
        config["state_directory"] = str(self.bundle / "state/openjules")
        config_path.write_text(json.dumps(config))
        with self.assertRaises(AssertionError):
            self.histories()

    def test_prior_runtime_schema_cannot_satisfy_the_new_pinned_witness(self):
        runtime = self.connections["runtime"]
        runtime.executescript("""
            ALTER TABLE workers RENAME TO retained_workers;
            CREATE TABLE workers AS SELECT worker_id,spec,profile_fingerprint,container_id,create_dispatched,
                start_dispatched,start_confirmed,retired,receipt FROM retained_workers;
        """)
        with self.assertRaises(AssertionError):
            self.histories()

    def test_any_protected_execution_or_model_history_in_any_scope_rejects(self):
        expected = self.histories()
        tables = {
            "controller": ("workers",),
            "authority": ("records", "artifacts", "events"),
            "lifecycle": ("managed_workers", "worker_operations", "worker_executions"),
            "runtime": ("workers", "attempts"),
            "dispatcher": ("deliveries",),
            "model": ("workers", "calls"),
        }
        for name, selected in tables.items():
            connection = self.connections[name]
            for table in selected:
                with self.subTest(journal=name, table=table):
                    columns = list(connection.execute('PRAGMA table_info("' + table + '")'))
                    values = [1 if "INT" in column[2].upper() or column[1] == "seq" else "foreign-history"
                              for column in columns]
                    connection.execute('INSERT INTO "' + table + '" VALUES('
                                       + ",".join("?" for _ in values) + ")", values)
                    connection.commit()
                    try:
                        with self.assertRaises(AssertionError):
                            self.histories(expected)
                    finally:
                        connection.execute('DELETE FROM "' + table + '"')
                        connection.commit()

    def test_changed_identity_and_even_valid_policy_fingerprint_reject(self):
        expected = self.histories()
        cases = (
            ("controller", "UPDATE metadata SET value=?", self.scope["deployment_id"], "foreign"),
            ("runtime", "UPDATE profiles SET repository=?", self.scope["repository"], "github:github.com/foreign/repo"),
            ("runtime", "UPDATE profiles SET fingerprint=?", "f" * 64, "d" * 64),
            ("runtime", "UPDATE child_profiles SET fingerprint=?", None, "c" * 64),
            ("model", "UPDATE policies SET fingerprint=?", "e" * 64, "d" * 64),
            ("model", "UPDATE policies SET scope=?", canonical([self.scope["deployment_id"], self.scope["repository"], self.profile]), "foreign"),
        )
        for name, sql, original, changed in cases:
            with self.subTest(journal=name, sql=sql):
                connection = self.connections[name]
                connection.execute(sql, (changed,))
                connection.commit()
                try:
                    with self.assertRaises(AssertionError):
                        self.histories(expected)
                finally:
                    connection.execute(sql, (original,))
                    connection.commit()

    def test_completed_controller_oracle_uses_actual_normalized_readiness(self):
        report = self.blocked_plan()
        self.assertEqual(report["repositories"][0]["validation"]["reason"], CONTROLLER_REASON)
        require_controller_plan(report, self.scope, self.profile, 1, False)
        mutations = (
            lambda value: value.update(deployment_id="foreign"),
            lambda value: value.update(max_workers=None),
            lambda value: value.update(reserved=False),
            lambda value: value["repositories"].append(deepcopy(value["repositories"][0])),
            lambda value: value["repositories"][0].update(effective_target=0),
            lambda value: value["repositories"][0].update(shortfall=True),
            lambda value: value["repositories"][0].update(enabled=False),
            lambda value: value["repositories"][0].update(workers=[{"state": "stopped"}]),
            lambda value: value["repositories"][0]["validation"].update(ready=True),
            lambda value: value["repositories"][0]["validation"].update(reason=RUNTIME_REASON),
            lambda value: value["repositories"][0]["reasons"].append("deployment capacity ceiling"),
        )
        for mutate in mutations:
            value = deepcopy(report)
            mutate(value)
            with self.subTest(report_mutation=mutations.index(mutate)), self.assertRaises(AssertionError):
                require_controller_plan(value, self.scope, self.profile, 1, False)

    def test_capabilities_and_snapshot_reject_wrong_scope_shape_and_boolean_aliases(self):
        capabilities = self.capabilities()
        require_runtime_capabilities(capabilities, self.scope, self.profile, False)
        for changes in (
            {"schema_version": True}, {"repository": "github:github.com/foreign/repo"},
            {"managed_dispatch": 0}, {"repository_access": True}, {"profile_supported": False},
            {"reason": "a different outage"}, {"operations": ["ensure"]},
        ):
            with self.subTest(capability=changes), self.assertRaises(AssertionError):
                require_runtime_capabilities(capabilities | changes, self.scope, self.profile, False)
        snapshot = {kind: [] for kind in RECORD_KINDS} | {"cursor": 0}
        require_empty_snapshot(snapshot)
        scoped = snapshot | {"schema_version": 1, **self.scope}
        require_empty_snapshot(scoped, self.scope)
        for changes in ({"cursor": False}, {"cursor": 1}, {"attempt": [{"id": "foreign"}]}, {"extra": []}):
            with self.subTest(snapshot=changes), self.assertRaises(AssertionError):
                require_empty_snapshot(snapshot | changes)
        for changes in ({"schema_version": True}, {"repository": "github:github.com/foreign/repo"}):
            with self.subTest(scoped=changes), self.assertRaises(AssertionError):
                require_empty_snapshot(scoped | changes, self.scope)
        incomplete = dict(scoped)
        del incomplete["candidate"]
        with self.assertRaises(AssertionError):
            require_empty_snapshot(incomplete, self.scope)

    def test_existing_owner_probe_and_no_policy_command_preserve_credentials(self):
        path = self.bundle / "secrets/polyphony.json"
        sealed = path.read_bytes()
        operator = json.loads((self.bundle / "secrets/operator.json").read_text())
        sensitive = set()
        owner = owner_probe_token(self.bundle, operator, sensitive)
        self.assertTrue(owner == operator["owner"] and owner in sensitive)
        self.assertTrue(path.read_bytes() == sealed)
        command = _command(self.bundle, "polyphony", self.configuration)
        variant = no_policy_command(command)
        offset = command.index("--managed-config")
        self.assertEqual(variant, command[:offset] + command[offset + 2:])
        with patch.dict(os.environ, {"ADMISSION_TEST_PROVIDER": "synthetic-provider-token"}):
            environment = _environment(self.bundle, "openjules", self.configuration)
        self.assertTrue(owner not in canonical(environment), "owner must remain outside the service environment")
        for bad in (command + ["--execution-config", "execution.json"],
                    command + ["--managed-config", "duplicate.json"],
                    command[:offset] + command[offset + 2:]):
            with self.subTest(invalid_command=bad), self.assertRaises(AssertionError):
                no_policy_command(bad)
        principals = json.loads(sealed)
        principals[owner]["repositories"] = [self.scope["repository"]]
        path.write_text(json.dumps(principals))
        with self.assertRaises(AssertionError):
            owner_probe_token(self.bundle, operator, sensitive)


class CompletedProcessAndTrafficTests(unittest.TestCase):
    def test_only_fresh_complete_successful_cli_output_can_establish_a_pass(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            command = ["python", "-m", "zpm", "reconcile", "--watch"]

            def run_once(name, argv, cwd, environment, timeout):
                calls.append((name, argv, timeout))
                (root / (name + ".log")).write_text('{"deployment_id":"expected"}\n')

            processes = SimpleNamespace(root=root, run_once=run_once)
            result = completed_reconcile(processes, command, root, {}, "fresh")
            self.assertEqual(result, {"deployment_id": "expected"})
            self.assertEqual(calls, [("fresh", command[:-1], 50)])
            with self.assertRaises(AssertionError):
                completed_reconcile(processes, command, root, {}, "fresh")
            self.assertEqual(len(calls), 1)
            for number, content in enumerate(("{", "{}\n{}", "[]", "x" * 1_000_001)):
                def malformed(name, *_):
                    (root / (name + ".log")).write_text(content)
                with self.subTest(output=number), self.assertRaises(AssertionError):
                    completed_reconcile(SimpleNamespace(root=root, run_once=malformed),
                                        command, root, {}, "malformed-" + str(number))

            def failed(name, *_):
                (root / (name + ".log")).write_text('{"otherwise":"valid"}')
                raise RuntimeError("process did not complete successfully")

            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                completed_reconcile(SimpleNamespace(root=root, run_once=failed), command, root, {}, "failed")
            with self.assertRaises(AssertionError):
                completed_reconcile(processes, command[:-1], root, {}, "not-watch")

    def test_rejected_model_traffic_and_new_or_removed_docker_objects_reject(self):
        model = SimpleNamespace(lock=threading.Lock(), requests=[], errors=[])
        inventory = {"containers": ["preexisting-container"], "volumes": ["preexisting-volume"]}
        with patch("check_authority_admission.docker_inventory", return_value=deepcopy(inventory)):
            require_idle_resources(model, inventory)
            for field in ("requests", "errors"):
                getattr(model, field).append("unexpected request")
                try:
                    with self.subTest(counter=field), self.assertRaises(AssertionError):
                        require_idle_resources(model, inventory)
                finally:
                    getattr(model, field).clear()
        for changed in (
            {"containers": [], "volumes": inventory["volumes"]},
            {"containers": inventory["containers"], "volumes": inventory["volumes"] + ["new-volume"]},
        ):
            with patch("check_authority_admission.docker_inventory", return_value=changed):
                with self.assertRaises(AssertionError):
                    require_idle_resources(model, inventory)

    def test_real_http_error_reader_retains_exact_bodies_and_denies_redirects(self):
        observed = []
        credential = "synthetic-admission-http-credential"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                observed.append((path, self.headers.get("Authorization"), body,
                                 parse_qs(urlsplit(self.path).query)))
                status, payload = 503, AUTHORITY_REASON.encode()
                if path == "/json":
                    payload = b'{"detail":"Managed runtime preflight is not ready"}'
                elif path == "/accepted":
                    status, payload = 200, b"{}"
                elif path == "/wrong-status":
                    status = 401
                elif path == "/oversized":
                    payload = b"x" * 4097
                elif path == "/malformed":
                    payload = b"{"
                elif path == "/unicode":
                    payload = b"\xff"
                elif path == "/redirect":
                    status = 302
                self.send_response(status)
                if path == "/redirect":
                    self.send_header("Location", "/accepted")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_POST = do_GET

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client("http://127.0.0.1:" + str(server.server_port), credential)
            query = {"repository": "github:github.com/trial/admission"}
            self.assertEqual(expected_error(client, "GET", "/text", query=query, text=True), AUTHORITY_REASON)
            self.assertEqual(expected_error(client, "POST", "/json", data={"schema_version": 1}),
                             {"detail": "Managed runtime preflight is not ready"})
            self.assertTrue(all(item[1] == "Bearer " + credential for item in observed))
            self.assertEqual(observed[0][3], {"repository": [query["repository"]]})
            self.assertEqual(json.loads(observed[1][2]), {"schema_version": 1})
            for path in ("/accepted", "/wrong-status", "/oversized", "/malformed", "/unicode"):
                with self.subTest(path=path), self.assertRaises(AssertionError):
                    expected_error(client, "GET", path)
            accepted_before = sum(row[0] == "/accepted" for row in observed)
            with self.assertRaises(AssertionError):
                expected_error(client, "GET", "/redirect")
            self.assertEqual(sum(row[0] == "/accepted" for row in observed), accepted_before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_cleanup_preserves_primary_failure_and_attempts_every_owned_service(self):
        calls = []
        finished = SimpleNamespace(poll=lambda: 0)

        def reset():
            calls.append("target")
            raise OSError("private reset detail")

        def stop(name):
            calls.append(name)
            if name == "admission-authority":
                raise OSError("private shutdown detail")

        processes = SimpleNamespace(
            running={"admission-authority": finished, "admission-openjules": finished}, stop=stop,
        )
        original = AssertionError("original admission failure")
        with self.assertRaises(AssertionError) as caught:
            with cleanup_phase(processes, reset):
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(calls, ["target", "admission-authority", "admission-openjules"])
        notes = " ".join(getattr(original, "__notes__", []))
        self.assertIn("target reset", notes)
        self.assertIn("admission-authority", notes)
        self.assertNotIn("private", notes)
        self.assertEqual(processes.running, {})
        with self.assertRaisesRegex(AssertionError, "admission cleanup failed"):
            with cleanup_phase(processes, reset):
                pass

    @unittest.skipUnless(hasattr(os, "killpg"), "owned process-group cleanup requires POSIX")
    def test_failed_normal_shutdown_still_reaps_the_exact_child(self):
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )

        def failed_stop(_):
            raise OSError("normal shutdown failed")

        processes = SimpleNamespace(running={"admission-authority": process}, stop=failed_stop)
        try:
            with self.assertRaises(OSError):
                stop_owned(processes, "admission-authority")
            self.assertIsNotNone(process.poll())
            self.assertEqual(processes.running, {})
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
