"""Real local HTTP/TLS fixtures for the lifecycle credential boundary.

The checked-in TLS certificate and key are public test data, never deployment
credentials. Their sole subject alternative name is the literal 127.0.0.1;
the test CA is trusted only by an explicitly configured test subprocess.
"""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from zpm.models import ConfigurationError, ProviderConfig
from zpm.provider import OpenJulesProvider, ProviderError


TLS = Path(__file__).with_name("fixtures") / "provider-tls"
TOKEN = "public-lifecycle-test-token-not-a-secret"


def configuration(endpoint):
    return {"schema_version": 1, "endpoint": endpoint,
            "token_ref": "env:ZPM_SECURITY_TEST_TOKEN", "max_workers": 2}


class EndpointPolicyTests(unittest.TestCase):
    def test_literal_loopback_http_and_https_prefixes_are_accepted_offline(self):
        endpoints = (
            "http://127.0.0.1:8000", "http://127.255.255.254:8000",
            "http://[::1]:8000", "http://[0:0:0:0:0:0:0:1]:8000",
            "https://worker.example/approved/runtime/", "https://[2001:db8::1]:443/runtime",
        )
        with patch("socket.getaddrinfo") as dns, patch("zpm.provider.subprocess.Popen") as process:
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(OpenJulesProvider(ProviderConfig.parse(configuration(endpoint))).config.endpoint,
                                     endpoint.rstrip("/"))
            dns.assert_not_called()
            process.assert_not_called()

    def test_remote_alias_ambiguous_and_scoped_http_are_rejected(self):
        for host in ("worker.example", "localhost", "localhost.", "192.168.1.2", "0.0.0.0",
                     "127.1", "2130706433", "0177.0.0.1", "0x7f000001", "127.0.0.1.",
                     "[::ffff:127.0.0.1]", "[::1%25lo]"):
            with self.subTest(host=host), self.assertRaises(ConfigurationError):
                ProviderConfig.parse(configuration("http://" + host))

    def test_ambiguous_urls_fail_with_a_fixed_credential_free_error(self):
        endpoints = (
            "ftp://worker.example", "https:///runtime", "https://@worker.example",
            "https://user:private-token@worker.example", "https://worker.example?",
            "https://worker.example#", "https://worker.example?token=private-token",
            "https://worker.example#private-token", "https://worker.example:invalid",
            "https://worker.example:", "https://worker.example:0", "https://worker.example:-1",
            "https://worker.example:65536", "http://[::1", "http://127.0.0.1\n",
            " http://127.0.0.1", "\x00http://127.0.0.1", "http://127.0.0.1/\x7f",
            "http://127.0.\t0.1", "https://worker.example/private token",
            "http://[::1]evil.example", "https://[::1]evil.example",
            "http://prefix[::1]", "https://prefix[::1]", "https://[::1]extra:443",
            "http://[::1]:80:90", "https://[v1.example]",
        )
        messages = set()
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigurationError) as raised:
                ProviderConfig.parse(configuration(endpoint))
            messages.add(str(raised.exception))
        self.assertEqual(messages, {"provider endpoint requires HTTPS or literal loopback HTTP, without credentials, query or fragment"})

    def test_direct_config_cannot_reach_credentials_process_or_dns(self):
        config = ProviderConfig(endpoint="http://remote.example", token_ref="env:ZPM_SECURITY_TEST_TOKEN", max_workers=2)
        with patch("zpm.provider.os.environ.get") as credential, \
                patch("zpm.provider.subprocess.Popen") as process, patch("socket.getaddrinfo") as dns:
            with self.assertRaises(ConfigurationError):
                OpenJulesProvider(config)
            credential.assert_not_called()
            process.assert_not_called()
            dns.assert_not_called()


class CredentialTransportTests(unittest.TestCase):
    @contextmanager
    def endpoint(self, *, tls=False, redirect=None, tunnel=None):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(302 if redirect else 200)
                if redirect:
                    self.send_header("Location", redirect)
                self.end_headers()
                self.wfile.write(b'{"schema_version":1,"workers":[]}')

            def do_CONNECT(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path != tunnel:
                    self.send_error(403)
                    return
                host, port = tunnel.split(":")
                with socket.create_connection((host, int(port)), timeout=3) as upstream:
                    self.send_response(200)
                    self.end_headers()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([self.connection, upstream], [], [], 0.1)
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(TLS / "server.pem")
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def environment(self, **extra):
        return patch.dict("os.environ", {"ZPM_SECURITY_TEST_TOKEN": TOKEN, **extra}, clear=True)

    def discover(self, endpoint):
        return OpenJulesProvider(ProviderConfig.parse(configuration(endpoint))).discover("deployment-a", "github:github.com/owner/project")

    def test_loopback_http_bypasses_ambient_proxies(self):
        with self.endpoint() as (origin, received), self.endpoint() as (proxy, intercepted):
            with self.environment(http_proxy=proxy, HTTP_PROXY=proxy, all_proxy=proxy, ALL_PROXY=proxy,
                                  no_proxy="", NO_PROXY=""):
                self.assertEqual(self.discover(origin), [])
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][1], "Bearer " + TOKEN)
            self.assertEqual(intercepted, [])

    def test_explicitly_trusted_matching_tls_certificate_accepts_bearer(self):
        with self.endpoint(tls=True) as (origin, received), self.environment(SSL_CERT_FILE=str(TLS / "ca.pem")):
            self.assertEqual(self.discover(origin + "/approved/runtime"), [])
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0][0].startswith("/approved/runtime/api/workers?"))
            self.assertEqual(received[0][1], "Bearer " + TOKEN)

    def test_untrusted_tls_certificate_receives_no_http_credentials(self):
        with self.endpoint(tls=True) as (origin, received), self.environment():
            with self.assertRaisesRegex(ProviderError, "transport unavailable") as raised:
                self.discover(origin)
            self.assertEqual(received, [])
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_trusted_tls_hostname_mismatch_receives_no_http_credentials(self):
        with self.endpoint(tls=True) as (origin, received), self.environment(SSL_CERT_FILE=str(TLS / "ca.pem")):
            with self.assertRaisesRegex(ProviderError, "transport unavailable"):
                self.discover(origin.replace("127.0.0.1", "localhost"))
            self.assertEqual(received, [])

    def test_https_redirect_cannot_forward_bearer_or_downgrade_to_http(self):
        with self.endpoint() as (other, forwarded), self.endpoint(tls=True, redirect=other) as (origin, received):
            with self.environment(SSL_CERT_FILE=str(TLS / "ca.pem")):
                with self.assertRaisesRegex(ProviderError, "HTTP 302"):
                    self.discover(origin)
            self.assertEqual(len(received), 1)
            self.assertEqual(forwarded, [])

    def test_https_keeps_explicit_proxy_and_ca_support(self):
        with self.endpoint(tls=True) as (origin, received):
            target = origin.removeprefix("https://")
            with self.endpoint(tunnel=target) as (proxy, connected):
                with self.environment(https_proxy=proxy, no_proxy="", NO_PROXY="", SSL_CERT_FILE=str(TLS / "ca.pem")):
                    self.assertEqual(self.discover(origin), [])
                self.assertEqual(connected, [(target, None)])
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0][1], "Bearer " + TOKEN)


if __name__ == "__main__":
    unittest.main()
