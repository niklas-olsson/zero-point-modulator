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


if __name__ == "__main__":
    unittest.main()
