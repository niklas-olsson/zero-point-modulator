"""Exercise the wire boundary using a local HTTP service, without agent code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from zpm.controller import Controller
from zpm.models import ConfigurationError, ProviderConfig
from zpm.provider import OpenJulesProvider, ProviderError
from zpm.store import Store

from .support import FixtureProvider


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.headers = {}
        self.response = b'{"schema_version": 1, "workers": []}'
        self.exchange = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else None
                fixture.requests.append({
                    "method": self.command,
                    "url": urlsplit(self.path),
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(body) if body else None,
                })
                if fixture.exchange is not None:
                    fixture.exchange(self, fixture.requests[-1])
                    return
                self.send_response(fixture.status)
                for key, value in fixture.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(fixture.response)

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            daemon=True,
        )
        self.thread.start()
        config = ProviderConfig.parse({
            "schema_version": 1,
            "endpoint": f"http://127.0.0.1:{self.server.server_port}",
            "token_ref": "env:ZPM_TEST_TOKEN",
            "max_workers": 3,
        })
        self.provider = OpenJulesProvider(config)
        self.environment = patch.dict("os.environ", {"ZPM_TEST_TOKEN": "fixture-secret"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_scoped_requests_preserve_immutable_keys(self):
        repository = "github:github.com/owner/project"
        scope = {"deployment_id": "deployment-a", "repository": repository}
        self.provider.capabilities(**scope, profile="approved")
        capabilities = self.requests[-1]
        self.assertEqual(capabilities["url"].path, "/api/workers/capabilities")
        self.assertEqual(parse_qs(capabilities["url"].query), {
            "deployment_id": ["deployment-a"],
            "repository": [repository],
            "profile": ["approved"],
        })
        spec = dict(scope, schema_version=1, profile="approved", slot_id="slot-0", generation=2, launch_key="launch-1")
        self.provider.ensure(spec)
        self.assertEqual(self.requests[-1]["body"], spec)
        self.assertEqual(self.requests[-1]["url"].path, "/api/workers/ensure")
        self.assertEqual(self.provider.discover(**scope, launch_key="launch-1"), [])
        self.assertEqual(parse_qs(self.requests[-1]["url"].query)["launch_key"], ["launch-1"])
        self.provider.observe(**scope, worker_id="worker/opaque")
        self.assertEqual(self.requests[-1]["url"].path, "/api/workers/worker%2Fopaque")
        for operation in ("drain", "stop"):
            getattr(self.provider, operation)(**scope, worker_id="worker-1", request_key="request-1")
            request = self.requests[-1]
            self.assertEqual(request["url"].path, "/api/workers/worker-1/" + operation)
            self.assertEqual(request["body"], dict(scope, schema_version=1, request_key="request-1"))
        self.assertTrue(all(item["authorization"] == "Bearer fixture-secret" for item in self.requests))

    def test_missing_credential_prevents_request(self):
        with patch.dict("os.environ", {"ZPM_TEST_TOKEN": ""}):
            with self.assertRaisesRegex(ProviderError, "credential reference is unresolved"):
                self.provider.capabilities("deployment-a", "owner/project", "approved")
        self.assertEqual(self.requests, [])

    def test_http_error_body_is_not_exposed(self):
        self.status = 403
        self.response = b'provider leaked fixture-secret and untrusted command output'
        with self.assertRaisesRegex(ProviderError, "OpenJules returned HTTP 403") as raised:
            self.provider.capabilities("deployment-a", "owner/project", "approved")
        self.assertNotIn("fixture-secret", str(raised.exception))
        self.assertNotIn("command output", str(raised.exception))

    def test_redirect_does_not_forward_authorization(self):
        self.status = 302
        self.headers["Location"] = "/unexpected-destination"
        with self.assertRaisesRegex(ProviderError, "HTTP 302"):
            self.provider.capabilities("deployment-a", "owner/project", "approved")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["url"].path, "/api/workers/capabilities")

    def test_malformed_or_unversioned_responses_are_rejected(self):
        self.response = b'not JSON: fixture-secret'
        with self.assertRaisesRegex(ProviderError, "invalid JSON response"):
            self.provider.discover("deployment-a", "owner/project")
        self.response = b'{"workers": []}'
        with self.assertRaisesRegex(ConfigurationError, "discovery response"):
            self.provider.discover("deployment-a", "owner/project")

    def timed_provider(self, timeout=1.0):
        return OpenJulesProvider(ProviderConfig.parse({
            **self.provider.config.__dict__, "timeout_seconds": timeout,
        }))

    def assert_helper_reaped(self, process):
        self.assertIsNotNone(process.poll())
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def drip(self, handler, headers=False, closed=None):
        """Send bytes well inside the inactivity timeout until the peer closes."""
        try:
            if headers:
                handler.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            else:
                handler.send_response(200)
                handler.send_header("Content-Length", "100000")
                handler.end_headers()
            for _ in range(100):
                handler.connection.sendall(b" ")
                time.sleep(0.025)
        except (BrokenPipeError, ConnectionResetError):
            if closed is not None:
                closed.set()

    def test_total_deadline_stops_trickled_headers_and_body_and_reaps_helper(self):
        original = subprocess.Popen
        for headers in (True, False):
            with self.subTest(headers=headers):
                children = []
                closed = threading.Event()
                self.exchange = lambda handler, request: self.drip(handler, headers, closed)

                def start(*args, **kwargs):
                    child = original(*args, **kwargs)
                    children.append(child)
                    return child

                began = time.monotonic()
                with patch("zpm.provider.subprocess.Popen", side_effect=start):
                    with self.assertRaisesRegex(ProviderError, "total request deadline.*outcome may be unknown"):
                        self.timed_provider().discover("deployment-a", "owner/project")
                self.assertLess(time.monotonic() - began, 2.0)
                self.assertTrue(closed.wait(1), "server did not observe closed transport")
                self.assertEqual(len(children), 1)
                self.assert_helper_reaped(children[0])

    def test_total_deadline_covers_blocked_dns_without_late_dispatch(self):
        original = subprocess.Popen
        children = []
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "dns-started"

            def start(args, **kwargs):
                # A deterministic resolver stall runs inside the real helper
                # process. It cannot be interrupted by urllib's socket timeout.
                script = (
                    "import pathlib, runpy, socket, time\n"
                    "def blocked(*args, **kwargs):\n"
                    f" pathlib.Path({str(marker)!r}).write_text('started')\n"
                    " time.sleep(10)\n"
                    " return []\n"
                    "socket.getaddrinfo = blocked\n"
                    f"runpy.run_path({args[-1]!r}, run_name='__main__')\n"
                )
                child = original(args[:3] + ["-c", script], **kwargs)
                children.append(child)
                return child

            began = time.monotonic()
            with patch("zpm.provider.subprocess.Popen", side_effect=start):
                with self.assertRaisesRegex(ProviderError, "total request deadline"):
                    self.timed_provider().discover("deployment-a", "owner/project")
            self.assertLess(time.monotonic() - began, 2.0)
            self.assertTrue(marker.exists())
            self.assertEqual(self.requests, [])
            self.assert_helper_reaped(children[0])

    def test_total_deadline_stops_stalled_tls_handshake(self):
        accepted, closed = threading.Event(), threading.Event()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(2)

            def withhold_handshake():
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    accepted.set()
                    while connection.recv(65536):
                        pass
                    closed.set()

            thread = threading.Thread(target=withhold_handshake, daemon=True)
            thread.start()
            provider = OpenJulesProvider(ProviderConfig.parse({
                **self.provider.config.__dict__, "timeout_seconds": 1.0,
                "endpoint": f"https://127.0.0.1:{listener.getsockname()[1]}",
            }))
            began = time.monotonic()
            with self.assertRaisesRegex(ProviderError, "total request deadline"):
                provider.discover("deployment-a", "owner/project")
            self.assertLess(time.monotonic() - began, 2.0)
            self.assertTrue(accepted.is_set())
            self.assertTrue(closed.wait(1), "TLS socket remains active after timeout")
            thread.join(1)
            self.assertFalse(thread.is_alive())

    def test_transport_startup_consumes_the_same_deadline(self):
        original = subprocess.Popen
        children = []
        self.exchange = lambda handler, request: self.drip(handler)

        def start(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            time.sleep(0.25)
            return child

        began = time.monotonic()
        with patch("zpm.provider.subprocess.Popen", side_effect=start):
            with self.assertRaisesRegex(ProviderError, "total request deadline"):
                self.timed_provider(0.4).discover("deployment-a", "owner/project")
        # The transport does not receive a fresh 0.4s after startup.
        self.assertLess(time.monotonic() - began, 0.6)
        self.assert_helper_reaped(children[0])

    def test_oversized_response_is_rejected_without_exposing_content(self):
        self.response = b"fixture-secret" + b" " * 1_000_000
        with self.assertRaisesRegex(ProviderError, "response exceeds the supported size") as raised:
            self.provider.discover("deployment-a", "owner/project")
        self.assertNotIn("fixture-secret", str(raised.exception))

    def test_helper_receives_only_transport_environment_and_private_request(self):
        original = subprocess.Popen
        children = []

        def start(args, **kwargs):
            self.assertNotIn("fixture-secret", " ".join(args))
            self.assertNotIn("UNRELATED_PROVIDER_SECRET", kwargs["env"])
            self.assertNotIn("ZPM_TEST_TOKEN", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertEqual(kwargs["env"]["no_proxy"], "127.0.0.1")
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(args[1], "-I")
            self.assertEqual(args[2], "-S")
            child = original(args, **kwargs)
            children.append(child)
            return child

        with patch.dict(os.environ, {"UNRELATED_PROVIDER_SECRET": "unrelated", "PYTHONPATH": "/untrusted",
                                     "no_proxy": "127.0.0.1"}):
            with patch("zpm.provider.subprocess.Popen", side_effect=start):
                self.assertEqual(self.provider.discover("deployment-a", "owner/project"), [])
        self.assert_helper_reaped(children[0])

    def test_ambiguous_mutations_keep_reservations_and_original_keys_through_restart(self):
        runtime = FixtureProvider()
        stalled = {"ensure", "drain", "stop"}

        def exchange(handler, request):
            operation = request["url"].path.rsplit("/", 1)[-1]
            query = {key: values[0] for key, values in parse_qs(request["url"].query).items()}
            if operation == "capabilities":
                response = runtime.capabilities(**query)
            elif operation == "workers":
                response = {"schema_version": 1, "workers": runtime.discover(**query)}
            elif operation == "ensure":
                response = runtime.ensure(request["body"])
            elif operation in {"drain", "stop"}:
                body = {key: value for key, value in request["body"].items() if key != "schema_version"}
                body["worker_id"] = request["url"].path.split("/")[-2]
                response = getattr(runtime, operation)(**body)
            else:
                response = runtime.observe(**query, worker_id=operation)
            if operation in stalled:
                stalled.remove(operation)
                self.drip(handler)
            else:
                raw = json.dumps(response).encode()
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)

        self.exchange = exchange
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.sqlite3"
            with Store(state, "deployment-a") as store:
                store.enroll({"schema_version": 1, "repository": "owner/project", "profile": "fixture", "desired_workers": 1})
                report = Controller(store, self.timed_provider(), 3).tick()
                first = store.workers()[0]
                self.assertEqual(report["reserved"], 1)
                self.assertEqual(first["pending_action"], "ensure")
                self.assertEqual(first["snapshot"]["state"], "unknown")
            with Store(state, "deployment-a") as store:
                controller = Controller(store, self.timed_provider(), 3)
                self.assertEqual(controller.tick()["repositories"][0]["healthy"], 1)
                self.assertEqual(store.workers()[0]["launch_key"], first["launch_key"])
                store.change("owner/project", "target", 0)
                self.assertEqual(controller.tick()["reserved"], 1)
                drained = store.workers()[0]
                self.assertEqual(drained["pending_action"], "drain")
            with Store(state, "deployment-a") as store:
                self.assertEqual(Controller(store, self.timed_provider(), 3).tick()["reserved"], 1)
                stopped = store.workers()[0]
                self.assertEqual(stopped["pending_action"], "stop")
            with Store(state, "deployment-a") as store:
                self.assertEqual(Controller(store, self.timed_provider(), 3).tick()["reserved"], 0)
                self.assertEqual(store.workers()[0]["launch_key"], first["launch_key"])
        self.assertEqual(len(runtime.records), 1)
        for operation in ("ensure", "drain", "stop"):
            self.assertEqual(len([row for row in runtime.calls if row[0] == operation]), 1)
        self.assertEqual(next(row for row in runtime.calls if row[0] == "drain")[-1], drained["action_key"])
        self.assertEqual(next(row for row in runtime.calls if row[0] == "stop")[-1], stopped["action_key"])

    def runtime_http(self, runtime, reject):
        """Real HTTP transport around a deterministic lifecycle fixture."""
        def exchange(handler, request):
            status = reject(request)
            if status is not None:
                payload = b"fixture-secret untrusted runtime diagnostics"
            else:
                operation = request["url"].path.rsplit("/", 1)[-1]
                query = {key: values[0] for key, values in parse_qs(request["url"].query).items()}
                if operation == "capabilities":
                    response = runtime.capabilities(**query)
                elif operation == "workers":
                    response = {"schema_version": 1, "workers": runtime.discover(**query)}
                elif operation == "ensure":
                    response = runtime.ensure(request["body"])
                elif operation in {"drain", "stop"}:
                    body = {key: value for key, value in request["body"].items() if key != "schema_version"}
                    body["worker_id"] = request["url"].path.split("/")[-2]
                    response = getattr(runtime, operation)(**body)
                else:
                    response = runtime.observe(**query, worker_id=operation)
                status, payload = 200, json.dumps(response).encode()
            handler.send_response(status)
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        self.exchange = exchange

    def bounded_tick(self, controller, expected_routes):
        before = len(self.requests)
        report = controller.tick(max_actions=1)
        requests = self.requests[before:]
        self.assertEqual([(row["method"], row["url"].path.rsplit("/", 1)[-1]) for row in requests],
                         expected_routes)
        self.assertLessEqual(sum(row["method"] == "POST" for row in requests), 1)
        return report

    def test_scoped_unsupported_reason_is_durable_without_releasing_or_adding_capacity(self):
        # This is a real HTTP/transport/Controller/Store test. Runtime lifecycle
        # behavior and the classified capability response remain named fixtures.
        reason = "Canonical managed authority is unsupported: child_initial_outcome=1"
        runtime = FixtureProvider()
        original_capabilities = runtime.capabilities
        unavailable = {"value": False}

        def capability(*args, **kwargs):
            value = original_capabilities(*args, **kwargs)
            if unavailable["value"]:
                value.update(repository_access=False, managed_dispatch=False, reason=reason)
            return value

        self.runtime_http(runtime, lambda request: None)
        capabilities = ("GET", "capabilities")
        with patch.object(runtime, "capabilities", side_effect=capability), tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.sqlite3"
            with Store(state, "deployment-a") as store:
                store.enroll({"schema_version": 1, "repository": "owner/project",
                              "profile": "fixture", "desired_workers": 1})
                controller = Controller(store, self.provider, 2)
                self.assertEqual(controller.check()["repositories"][0]["validation"],
                                 {"ready": True, "reason": "ready"})
                unavailable["value"] = True
                blocked = self.bounded_tick(controller, [capabilities])
                row = blocked["repositories"][0]
                self.assertEqual(row["validation"], {"ready": False, "reason": reason})
                self.assertEqual(row["reasons"], [reason])
                self.assertEqual((row["desired_workers"], row["effective_target"], row["permitted_target"],
                                  row["healthy"], row["reserved"], row["shortfall"]), (1, 1, 1, 0, 0, 1))
                self.assertEqual(store.workers(), [])
                self.assertEqual(runtime.records, {})

            with Store(state, "deployment-a") as store:
                before = len(self.requests)
                retained = Controller(store).plan()["repositories"][0]
                self.assertEqual(retained["validation"], {"ready": False, "reason": reason})
                self.assertEqual(retained["reasons"], [reason])
                self.assertEqual(len(self.requests), before, "offline status contacted the provider")
                controller = Controller(store, self.provider, 2)
                self.bounded_tick(controller, [capabilities])
                self.assertEqual(store.workers(), [])
                unavailable["value"] = False
                recovered = self.bounded_tick(controller, [capabilities, ("POST", "ensure")])
                self.assertEqual(recovered["repositories"][0]["validation"], {"ready": True, "reason": "ready"})
                self.assertEqual((recovered["reserved"], recovered["repositories"][0]["healthy"]), (1, 1))
                original = store.workers()[0]
                worker = original["snapshot"]["worker_id"]
                self.assertTrue(worker)

                # Losing the optional capability does not release an existing
                # incarnation or hide positive unmet demand behind target zero.
                store.change("owner/project", "target", 2)
                unavailable["value"] = True
                blocked = self.bounded_tick(controller, [capabilities, ("GET", worker)])
                row = blocked["repositories"][0]
                self.assertEqual(row["validation"], {"ready": False, "reason": reason})
                self.assertEqual(row["reasons"], [reason])
                self.assertEqual((row["desired_workers"], row["effective_target"], row["healthy"],
                                  row["reserved"], row["shortfall"]), (2, 2, 1, 1, 1))
                self.assertEqual(store.workers(), [original])

            with Store(state, "deployment-a") as store:
                self.assertEqual(Controller(store).plan()["repositories"][0]["validation"],
                                 {"ready": False, "reason": reason})
                store.change("owner/project", "target", 1)
                unavailable["value"] = False
                recovered = self.bounded_tick(Controller(store, self.provider, 2),
                                              [capabilities, ("GET", worker)])
                self.assertEqual(recovered["repositories"][0]["validation"], {"ready": True, "reason": "ready"})
                self.assertEqual(recovered["repositories"][0]["reasons"], [])
                self.assertEqual(store.workers(), [original])
            self.assertEqual(len(runtime.records), 1)
            self.assertEqual(len([call for call in runtime.calls if call[0] == "ensure"]), 1)
            self.assertFalse(any(call[0] in {"drain", "stop"} for call in runtime.calls))

    def test_unsupported_reason_requires_the_exact_negative_contract_and_requested_scope(self):
        reason = "Canonical managed authority is unsupported: child_initial_outcome=1"
        value = FixtureProvider().capabilities("deployment-a", "github:github.com/owner/project", "fixture")
        value.update(repository_access=False, managed_dispatch=False, reason=reason)
        scope_error = "provider capability response does not match the requested scope"
        cases = [
            ({}, reason),
            ({"deployment_id": "another-deployment"}, scope_error),
            ({"repository": "github:github.com/another/project"}, scope_error),
            ({"profile": "another-profile"}, scope_error),
            ({"repository": None}, scope_error),
            ({"schema_version": True}, "unsupported provider capability schema"),
            ({"operations": ["ensure", "discover", "observe", "drain"]}, "provider lacks required lifecycle operations"),
            ({"operations": "ensure discover observe drain stop"}, "provider lacks required lifecycle operations"),
            ({"operations": value["operations"] + [None]}, "repository_access unavailable"),
            ({"profile_supported": False}, "profile_supported unavailable"),
            ({"profile_supported": 1}, "profile_supported unavailable"),
            ({"repository_access": 0}, "repository_access unavailable"),
            ({"managed_dispatch": 0}, "repository_access unavailable"),
            ({"managed_dispatch": None}, "repository_access unavailable"),
            ({"managed_dispatch": True}, "repository_access unavailable"),
            ({"reason": reason + "\nfixture-secret"}, "repository_access unavailable"),
            ({"reason": "fixture-secret arbitrary remote diagnostic"}, "repository_access unavailable"),
            ({"reason": None}, "repository_access unavailable"),
            ({}, reason),
        ]
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "state.sqlite3", "deployment-a") as store:
            store.enroll({"schema_version": 1, "repository": "owner/project",
                          "profile": "fixture", "desired_workers": 1})
            controller = Controller(store, self.provider, 1)
            for changes, expected in cases:
                with self.subTest(changes=changes):
                    self.response = json.dumps(value | changes).encode()
                    report = self.bounded_tick(controller, [("GET", "capabilities")])
                    row = report["repositories"][0]
                    self.assertEqual(row["validation"], {"ready": False, "reason": expected})
                    self.assertEqual(row["reasons"], [expected])
                    self.assertEqual((row["effective_target"], row["shortfall"], report["reserved"]), (1, 1, 0))
                    self.assertEqual(store.workers(), [])
                    self.assertNotIn("fixture-secret", json.dumps(report))
                    self.assertNotIn("arbitrary remote diagnostic", json.dumps(report))

    def test_http_failure_body_cannot_supply_the_unsupported_diagnostic(self):
        self.response = json.dumps({
            "reason": "Canonical managed authority is unsupported: child_initial_outcome=1",
            "secret": "fixture-secret",
        }).encode()
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "state.sqlite3", "deployment-a") as store:
            store.enroll({"schema_version": 1, "repository": "owner/project",
                          "profile": "fixture", "desired_workers": 1})
            for status in (403, 503):
                with self.subTest(status=status):
                    self.status = status
                    report = self.bounded_tick(Controller(store, self.provider, 1), [("GET", "capabilities")])
                    self.assertEqual(report["repositories"][0]["validation"],
                                     {"ready": False, "reason": f"OpenJules returned HTTP {status}"})
                    self.assertEqual(store.workers(), [])
                    self.assertNotIn("child_initial_outcome", json.dumps(report))
                    self.assertNotIn("fixture-secret", json.dumps(report))

    def test_revoked_token_and_scope_keep_original_control_keys_until_access_recovers(self):
        runtime = FixtureProvider()
        access = {"token": True, "scope": True, "revoke_at_stop": False}

        def authorize(request):
            if request["authorization"] != "Bearer fixture-secret" or not access["token"]:
                return 401
            # Exercise revocation between successful preflight/observation and
            # the first stop. Every subsequent request then loses this scope.
            if access["revoke_at_stop"] and request["url"].path.endswith("/stop"):
                access.update(scope=False, revoke_at_stop=False)
            return None if access["scope"] else 403

        self.runtime_http(runtime, authorize)
        provider = self.timed_provider()
        capabilities = ("GET", "capabilities")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.sqlite3"
            with Store(state, "deployment-a") as store:
                store.enroll({"schema_version": 1, "repository": "owner/project",
                              "profile": "fixture", "desired_workers": 1})
                controller = Controller(store, provider, 2)
                initial = self.bounded_tick(controller, [capabilities, ("POST", "ensure")])
                self.assertEqual((initial["reserved"], initial["repositories"][0]["healthy"]), (1, 1))
                original = store.workers()[0]
                worker = original["snapshot"]["worker_id"]
                self.assertTrue(worker)
                owned = {key: original[key] for key in ("launch_key", "repository", "slot_id", "generation", "profile")}
                observe = ("GET", worker)

                def retained_denial(store, report, status, pending):
                    rows = store.workers()
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual({key: row[key] for key in owned}, owned)
                    self.assertEqual(row["snapshot"]["worker_id"], worker)
                    self.assertEqual(row["pending_action"], pending)
                    self.assertEqual(row["snapshot"]["state"], "unknown")
                    self.assertEqual(row["snapshot"]["reason"], f"OpenJules returned HTTP {status}")
                    self.assertFalse(row["snapshot"]["termination_confirmed"])
                    self.assertFalse(row["snapshot"]["artifacts_captured"])
                    self.assertFalse(row["snapshot"]["admission_open"])
                    self.assertEqual(report["reserved"], 1)
                    self.assertIn(f"OpenJules returned HTTP {status}", report["repositories"][0]["reasons"])
                    rendered = json.dumps(report)
                    self.assertNotIn("fixture-secret", rendered)
                    self.assertNotIn("untrusted runtime diagnostics", rendered)
                    self.assertEqual(len(runtime.records), 1)
                    return row

                # A formerly successful token is revoked. Even an unmet
                # positive target cannot allocate a second incarnation.
                access["token"] = False
                store.change("owner/project", "target", 2)
                denied = self.bounded_tick(controller, [capabilities, observe])
                retained_denial(store, denied, 401, None)
                unavailable = denied["repositories"][0]
                self.assertEqual((unavailable["desired_workers"], unavailable["healthy"],
                                  unavailable["reserved"], unavailable["shortfall"]), (2, 0, 1, 2))
                self.assertEqual(unavailable["validation"],
                                 {"ready": False, "reason": "OpenJules returned HTTP 401"})
                self.assertNotIn("deployment capacity ceiling", unavailable["reasons"])
                store.change("owner/project", "target", 0)
                denied = self.bounded_tick(controller, [capabilities, observe, ("POST", "drain")])
                drain_key = retained_denial(store, denied, 401, "drain")["action_key"]
                self.assertTrue(drain_key)

            with Store(state, "deployment-a") as store:
                controller = Controller(store, provider, 2)
                denied = self.bounded_tick(controller, [capabilities, observe, ("POST", "drain")])
                self.assertEqual(retained_denial(store, denied, 401, "drain")["action_key"], drain_key)
                self.assertEqual(len([call for call in runtime.calls if call[0] == "ensure"]), 1)
                self.assertFalse(any(call[0] in {"drain", "stop"} for call in runtime.calls))
                access["token"] = True
                recovered = self.bounded_tick(controller, [capabilities, observe, ("POST", "drain")])
                self.assertEqual(recovered["reserved"], 1)
                self.assertEqual(store.workers()[0]["snapshot"]["state"], "draining")
                self.assertIsNone(store.workers()[0]["pending_action"])
                # max_actions=1 keeps the acknowledged drain and stop in
                # separate completed passes, even when execution is idle.
                self.assertFalse(any(call[0] == "stop" for call in runtime.calls))
                self.assertEqual([call[-1] for call in runtime.calls if call[0] == "drain"], [drain_key])

                access["revoke_at_stop"] = True
                denied = self.bounded_tick(controller, [capabilities, observe, ("POST", "stop")])
                stop_key = retained_denial(store, denied, 403, "stop")["action_key"]
                self.assertTrue(stop_key)
                self.assertNotEqual(stop_key, drain_key)

            with Store(state, "deployment-a") as store:
                controller = Controller(store, provider, 2)
                denied = self.bounded_tick(controller, [capabilities, observe, ("POST", "stop")])
                self.assertEqual(retained_denial(store, denied, 403, "stop")["action_key"], stop_key)
                self.assertEqual(denied["repositories"][0]["validation"],
                                 {"ready": False, "reason": "OpenJules returned HTTP 403"})
                self.assertFalse(any(call[0] == "stop" for call in runtime.calls))
                access["scope"] = True
                recovered = self.bounded_tick(controller, [capabilities, observe, ("POST", "stop")])
                self.assertEqual(recovered["reserved"], 0)
                final = store.workers()[0]
                self.assertEqual({key: final[key] for key in owned}, owned)
                self.assertEqual(final["snapshot"]["worker_id"], worker)
                self.assertTrue(final["snapshot"]["termination_confirmed"])
                self.assertTrue(final["snapshot"]["artifacts_captured"])
                self.assertIsNone(final["pending_action"])
                self.assertEqual(store.binding("owner/project")["desired_workers"], 0)

            with Store(state, "deployment-a") as store:
                retained = Controller(store).plan()
                self.assertEqual(retained["reserved"], 0)
                self.assertEqual(len(store.workers()), 1)
                self.assertEqual(store.workers()[0]["snapshot"]["worker_id"], worker)
            self.assertEqual(len(runtime.records), 1)
            self.assertEqual(len([call for call in runtime.calls if call[0] == "ensure"]), 1)
            self.assertEqual([call[-1] for call in runtime.calls if call[0] == "stop"], [stop_key])
            for operation, key in (("drain", drain_key), ("stop", stop_key)):
                sent = [row["body"] for row in self.requests if row["url"].path.endswith("/" + operation)]
                self.assertEqual(len(sent), 3)
                self.assertTrue(all(body == sent[0] and body["request_key"] == key for body in sent))
            self.assertTrue(all(row["authorization"] == "Bearer fixture-secret" for row in self.requests))

    def test_quota_rejection_after_preflight_retries_the_original_uncertain_ensure(self):
        runtime = FixtureProvider()
        quota = {"blocked": True}

        def reject(request):
            if quota["blocked"] and request["url"].path.endswith("/ensure"):
                return 429
            return None

        self.runtime_http(runtime, reject)
        provider = self.timed_provider()
        capabilities = ("GET", "capabilities")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.sqlite3"
            original = None
            for attempt in range(3):
                with Store(state, "deployment-a") as store:
                    if attempt == 0:
                        store.enroll({"schema_version": 1, "repository": "owner/project",
                                      "profile": "fixture", "desired_workers": 1})
                    expected = [capabilities] + ([] if attempt == 0 else [("GET", "workers")]) + [("POST", "ensure")]
                    report = self.bounded_tick(Controller(store, provider, 1), expected)
                    row = store.workers()[0]
                    identity = {key: row[key] for key in ("launch_key", "repository", "slot_id", "generation", "profile")}
                    if original is None:
                        original = identity
                    self.assertEqual(identity, original)
                    self.assertEqual((report["reserved"], len(store.workers()), len(runtime.records)), (1, 1, 0))
                    self.assertEqual(row["pending_action"], "ensure")
                    self.assertEqual(row["snapshot"]["worker_id"], "")
                    self.assertEqual(row["snapshot"]["state"], "unknown")
                    self.assertEqual(row["snapshot"]["reason"], "OpenJules returned HTTP 429")
                    self.assertFalse(row["snapshot"]["termination_confirmed"])
                    self.assertFalse(row["snapshot"]["artifacts_captured"])
                    selected = report["repositories"][0]
                    self.assertTrue(selected["validation"]["ready"])
                    self.assertEqual((selected["desired_workers"], selected["healthy"], selected["shortfall"]), (1, 0, 1))
                    self.assertIn("OpenJules returned HTTP 429", selected["reasons"])
                    self.assertNotIn("fixture-secret", json.dumps(report))
                    self.assertNotIn("untrusted runtime diagnostics", json.dumps(report))
                    self.assertFalse(any(call[0] == "ensure" for call in runtime.calls))
            quota["blocked"] = False
            with Store(state, "deployment-a") as store:
                controller = Controller(store, provider, 1)
                recovered = self.bounded_tick(controller, [capabilities, ("GET", "workers"), ("POST", "ensure")])
                self.assertEqual((recovered["reserved"], recovered["repositories"][0]["healthy"]), (1, 1))
                row = store.workers()[0]
                self.assertEqual({key: row[key] for key in original}, original)
                self.assertIsNone(row["pending_action"])
                worker = row["snapshot"]["worker_id"]
                self.assertTrue(worker)
                self.bounded_tick(controller, [capabilities, ("GET", worker)])
                self.assertEqual(len(runtime.records), 1)
                self.assertEqual(len([call for call in runtime.calls if call[0] == "ensure"]), 1)
                store.change("owner/project", "target", 0)
                self.assertEqual(self.bounded_tick(controller, [capabilities, ("GET", worker), ("POST", "drain")])["reserved"], 1)
                self.assertEqual(self.bounded_tick(controller, [capabilities, ("GET", worker), ("POST", "stop")])["reserved"], 0)
            ensures = [row["body"] for row in self.requests if row["url"].path.endswith("/ensure")]
            self.assertEqual(len(ensures), 4)
            self.assertTrue(all(value == ensures[0] for value in ensures))
            discoveries = [row for row in self.requests if row["url"].path.endswith("/workers")]
            self.assertEqual(len(discoveries), 3)
            self.assertTrue(all(parse_qs(row["url"].query)["launch_key"] == [original["launch_key"]] for row in discoveries))


if __name__ == "__main__":
    unittest.main()
