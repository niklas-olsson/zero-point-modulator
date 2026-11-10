"""Exercise the wire boundary using a local HTTP service, without agent code."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from zpm.models import ConfigurationError, ProviderConfig
from zpm.provider import OpenJulesProvider, ProviderError


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.headers = {}
        self.response = b'{"schema_version": 1, "workers": []}'
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


if __name__ == "__main__":
    unittest.main()
