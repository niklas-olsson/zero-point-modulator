"""Real manager/SQLite coordination with explicitly synthetic native receipts.

The doctor subprocess, HTTP capability transport and retained journal are real.
Producer provenance/results remain fixtures here; installed qualification stays
in the pinned contract trials.
"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from tests import test_installation_preflight as fixtures
from zpm import deployment, installation_admission as admission
from zpm import installation_preflight as preflight
from zpm.models import ConfigurationError, OPERATIONS
from zpm.store import Store


class ObserverCoordinationTests(unittest.TestCase):
    setUp = fixtures.PairedPreflightTests.setUp
    tearDown = fixtures.PairedPreflightTests.tearDown
    read = fixtures.PairedPreflightTests.read
    prepared = fixtures.PairedPreflightTests.prepared
    bootstrapped_fixture = fixtures.PairedPreflightTests.bootstrapped_fixture
    native_fixtures = fixtures.PairedPreflightTests.native_fixtures

    @contextmanager
    def selection_lock(self, *, shared=False):
        descriptor = os.open(self.bundle / preflight.SELECTION, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def state_snapshot(self):
        return preflight._state_snapshot(self.bundle, preflight._Budget())

    def native_result(self, service, component):
        return preflight._observe(admission.encode(self.native_results[service]), service, component, 0)

    def real_doctor_fixture(self, port):
        # Copy current executable ZPM source into a clean temporary checkout.
        # This runs the real doctor command without depending on a dirty author
        # worktree or installing unrelated sibling dependencies.
        checkout = Path(self.config["sources"]["zpm"]["checkout"])
        shutil.rmtree(checkout / "zpm")
        shutil.copytree(Path(deployment.__file__).parent, checkout / "zpm",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "real doctor source"],
                       check=True, capture_output=True)
        self.config["sources"]["zpm"]["revision"] = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        self.config["ports"] = {"polyphony": port + 1 if port < 65535 else port - 1, "openjules": port}
        self.bootstrapped_fixture()
        binary = self.bundle / "environments/zpm/bin/python"
        binary.parent.mkdir()
        binary.symlink_to(sys.executable)

    @contextmanager
    def capability_fixture(self):
        requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, strict_parsing=True)
                expected = {
                    "deployment_id": fixture.data["deployment_id"],
                    "repository": fixture.data["binding"]["repository"],
                    "profile": fixture.data["binding"]["profile"],
                }
                if (parsed.path != "/api/workers/capabilities"
                        or query != {key: [value] for key, value in expected.items()}
                        or self.headers.get("Authorization") !=
                        "Bearer " + fixture.read("secrets/environment.json")["OPENJULES_WORKER_TOKEN"]):
                    self.send_error(403)
                    return
                requests.append(expected)
                raw = json.dumps({
                    "schema_version": 1, **expected, "operations": sorted(OPERATIONS),
                    "profile_supported": True, "repository_access": True, "managed_dispatch": True,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield server.server_port, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_omitted_option_observation_has_no_file_or_lock_effect(self):
        with patch.object(preflight.os, "open", side_effect=AssertionError("legacy observer opened a file")):
            with preflight.observe(self.root / "absent", {}):
                pass

    def test_missing_symlink_nonregular_or_public_selection_refuses_without_creation(self):
        self.prepared()
        path = self.bundle / preflight.SELECTION
        original = path.read_bytes()
        other = self.root / "private-selection-copy"
        other.write_bytes(original)
        other.chmod(0o600)
        for kind in ("missing", "symlink", "directory", "fifo", "public", "hardlink"):
            path.unlink()
            if kind == "symlink":
                path.symlink_to(other)
            elif kind == "directory":
                path.mkdir(mode=0o700)
            elif kind == "fifo":
                os.mkfifo(path, 0o600)
            elif kind == "public":
                path.write_bytes(original)
                path.chmod(0o644)
            elif kind == "hardlink":
                os.link(other, path)
            with self.subTest(kind=kind), self.assertRaises(preflight.PreflightError) as caught:
                with preflight.observe(self.bundle, self.data):
                    self.fail("unsafe selection admitted an observer")
            self.assertEqual(caught.exception.code, "input_unavailable")
            self.assertEqual(os.path.lexists(path), kind != "missing")
            if path.is_dir():
                path.rmdir()
            elif os.path.lexists(path):
                path.unlink()
            preflight._write_new(path, original)

    def test_observer_preserves_original_body_exceptions_and_releases_lock(self):
        self.prepared()
        for error in (preflight.PreflightError("zpm", "installation_changed"),
                      ConfigurationError("fixture configuration refusal"),
                      OSError("fixture I/O failure"), RuntimeError("fixture failure")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)) as caught:
                with preflight.observe(self.bundle, self.data):
                    raise error
            self.assertIs(caught.exception, error)
            with self.selection_lock():
                pass

    def test_selection_inode_replacement_is_refused_even_with_identical_bytes(self):
        self.prepared()
        path = self.bundle / preflight.SELECTION
        original = path.read_bytes()
        with self.assertRaises(preflight.PreflightError) as caught:
            with preflight.observe(self.bundle, self.data):
                replacement = path.with_name("replacement-selection")
                preflight._write_new(replacement, original)
                os.replace(replacement, path)
        self.assertEqual(caught.exception.code, "selection_mismatch")
        self.assertEqual(path.read_bytes(), original)

    def test_exclusive_admission_blocks_verify_before_sqlite_and_doctor_before_context(self):
        self.prepared()
        before = self.state_snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.selection_lock(), \
                patch.object(deployment.sqlite3, "connect", side_effect=AssertionError("busy verify opened SQLite")), \
                patch.object(deployment, "_installation_context", side_effect=AssertionError("busy doctor probed context")):
            with self.assertRaises(preflight.PreflightError) as caught:
                deployment.verify(self.bundle)
            self.assertEqual(caught.exception.code, "operation_busy")
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(deployment.manage(self.bundle, ["doctor"]), 3)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "ready": False, "reason": "installation preflight in progress", "remote_access_checked": False,
        })
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(self.state_snapshot(), before)

    def test_exclusive_lock_wait_uses_original_budget_and_never_calls_a_producer(self):
        self.bootstrapped_fixture()
        original = (self.bundle / preflight.SELECTION).read_bytes()
        budget_type = preflight._Budget
        with self.selection_lock(shared=True), patch.object(preflight, "OPERATION_SECONDS", 0.05), \
                patch.object(preflight, "_Budget", wraps=budget_type) as budgets, self.native_fixtures() as calls:
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        self.assertEqual(caught.exception.code, "operation_timeout")
        self.assertEqual(budgets.call_count, 1)
        calls["_probe"].assert_not_called()
        calls["_run_producer"].assert_not_called()
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        self.assertEqual((self.bundle / preflight.SELECTION).read_bytes(), original)

    def test_manager_verify_releases_observation_before_start_and_native_lock_before_services(self):
        self.bootstrapped_fixture()
        events = []

        def started(processes):
            self.assertEqual(set(processes), {"zpm"})
            with self.selection_lock():
                events.append("selection lock released before service observation")
            with self.assertRaisesRegex(ConfigurationError, "manager or bootstrap is already running"):
                with deployment._manager_lock(self.bundle):
                    self.fail("manager lifetime lock was released")

        def start(root, data):
            process = Mock()
            process.poll.return_value = 0
            with patch.object(deployment, "_installation_context", return_value=None), \
                    patch.object(deployment.subprocess, "Popen", return_value=process) as launch:
                result = deployment._start_services(root, data, ("zpm",), after_start=started)
            self.assertEqual(launch.call_count, 1)
            return result

        with self.native_fixtures(), patch.object(preflight, "OPERATION_SECONDS", 0.5), \
                patch.object(deployment, "_start", side_effect=start):
            self.assertEqual(deployment.manage(self.bundle, ["start"]), 1)
        self.assertEqual(events, ["selection lock released before service observation"])
        self.assertTrue((self.bundle / preflight.RECEIPT).exists())

    def test_actual_doctor_is_busy_during_native_snapshot_and_works_under_lifetime_lock(self):
        with self.capability_fixture() as (port, requests):
            self.real_doctor_fixture(port)
            with self.native_fixtures():
                initial = preflight.preflight(self.bundle, self.data)
            original = (self.bundle / preflight.RECEIPT).read_bytes()
            selected = (self.bundle / preflight.SELECTION).read_bytes()
            calls = []

            def producer(root, checkout, work, service, component, inputs, budget):
                if not calls:
                    before = self.state_snapshot()
                    # The real manager/verify/doctor path runs in another
                    # process. A busy observer must never reach its context
                    # probe, capability transport, or SQLite access.
                    program = (
                        "import sys\nfrom unittest.mock import patch\n"
                        "from zpm import deployment\n"
                        "with patch.object(deployment,'_installation_context',"
                        "side_effect=AssertionError('busy observer entered context')):\n"
                        "    raise SystemExit(deployment.manage(sys.argv[1],['doctor']))\n"
                    )
                    result = subprocess.run([sys.executable, "-B", "-c", program, str(self.bundle)],
                        cwd=Path(deployment.__file__).resolve().parents[1],
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {
                        "ready": False, "reason": "installation preflight in progress", "remote_access_checked": False,
                    })
                    self.assertEqual(self.state_snapshot(), before)
                    self.assertEqual(requests, [])
                    calls.append("busy")
                return self.native_result(service, component)

            with self.native_fixtures(_run_producer=producer):
                self.assertEqual(preflight.preflight(self.bundle, self.data), initial)
            self.assertEqual(calls, ["busy"])
            self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), original)
            self.assertEqual((self.bundle / preflight.SELECTION).read_bytes(), selected)

            before = self.state_snapshot()
            stdout, stderr = io.StringIO(), io.StringIO()
            # Bootstrap/context dependencies are explicit fixtures here. The
            # doctor, ZPM check process, scoped HTTP request and DB write are real.
            with deployment._manager_lock(self.bundle), \
                    patch.object(deployment, "_installation_context", return_value=None), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(deployment.manage(self.bundle, ["doctor"]), 0)
            report = json.loads(stdout.getvalue())
            self.assertTrue(report["ready"])
            self.assertTrue(report["remote_access_checked"])
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(len(requests), 1)
            self.assertNotEqual(self.state_snapshot(), before)
            with Store(self.bundle / "state/zpm.sqlite3", self.data["deployment_id"]) as store:
                self.assertEqual(store.bindings()[0]["validation"], {"ready": True, "reason": "ready"})
                self.assertEqual(store.workers(), [])
            self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), original)

    def test_real_uncoordinated_journal_write_still_refuses_success(self):
        self.bootstrapped_fixture()
        with self.native_fixtures():
            preflight.preflight(self.bundle, self.data)
        original = (self.bundle / preflight.RECEIPT).read_bytes()
        writes = []

        def producer(root, checkout, work, service, component, inputs, budget):
            if not writes:
                with Store(self.bundle / "state/zpm.sqlite3", self.data["deployment_id"]) as store:
                    row = store.bindings()[0]
                    store.validation(row["repository"], True, "uncoordinated fixture observation", row["revision"])
                writes.append("retained real SQLite mutation")
            return self.native_result(service, component)

        with self.native_fixtures(_run_producer=producer):
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        self.assertEqual(caught.exception.code, "installation_changed")
        self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), original)
        self.assertEqual(writes, ["retained real SQLite mutation"])
        self.assertEqual(self.read(preflight.STATUS)["phase"], "native-config-refused")

    def test_selection_replacement_during_native_validation_cannot_publish_receipt(self):
        selected = self.bootstrapped_fixture()
        path = self.bundle / preflight.SELECTION
        original = path.read_bytes()
        replacements = []

        def producer(root, checkout, work, service, component, inputs, budget):
            if not replacements:
                replacement = path.with_name("replacement-selection")
                preflight._write_new(replacement, original)
                os.replace(replacement, path)
                replacements.append("replaced")
            return self.native_result(service, component)

        with self.native_fixtures(_run_producer=producer):
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        self.assertEqual(caught.exception.code, "selection_mismatch")
        self.assertEqual(caught.exception.report["receipt_id"], selected["receipt_id"])
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        self.assertEqual(self.read(preflight.STATUS)["phase"], "native-config-refused")
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
