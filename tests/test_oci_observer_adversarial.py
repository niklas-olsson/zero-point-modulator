"""Independent OCI observer faults: synthetic HTTP and owned local children only.

No external request, redirected-origin contact, Docker or candidate execution.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType, ModuleType
import unittest
import urllib.error
from unittest.mock import patch

from tests.contracts import check_alpine_sealed_runtime as runtime
from tests.contracts import check_oci_destination as observer


TOKEN = "fixture.token.credential"
HOSTNAME = "destination.example.test"
LOCATION = "https://" + HOSTNAME + "/private/object?signature=not-for-output"


class WireStream(io.BytesIO):
    """Record application reads without asserting anything about OS buffering."""

    def __init__(self, wire, *, forbid_body_from=None):
        super().__init__(wire)
        self.events = []
        self.closed_at = None
        self.forbid_body_from = forbid_body_from

    def checked(self, raw):
        if (raw and self.forbid_body_from is not None
                and self.tell() > self.forbid_body_from):
            raise AssertionError("raw blob body must not be consumed")
        return raw

    def read(self, size=-1):
        self.events.append(("read", size, self.tell()))
        return self.checked(super().read(size))

    def readline(self, size=-1):
        self.events.append(("readline", size, self.tell()))
        return self.checked(super().readline(size))

    def close(self):
        if not self.closed:
            self.closed_at = self.tell()
        super().close()


class WireSocket:
    def __init__(self, stream):
        self.stream = stream

    def makefile(self, mode):
        if mode != "rb":
            raise AssertionError("unexpected socket mode")
        return self.stream


class WireReply:
    """A real bounded HTTP parser fed solely from fixture bytes."""

    def __init__(self, status=200, headers=(), body=b"", *, forbid_body=False,
                 open_error=None, read_hook=None, close_hook=None):
        self.status = status
        self.headers = list(headers)
        self.body = body
        self.forbid_body = forbid_body
        self.open_error = open_error
        self.read_hook = read_hook
        self.close_hook = close_hook
        self.response = None
        self.stream = None
        self.response_reads = []
        self.body_start = None

    def open(self):
        if self.open_error is not None:
            raise self.open_error
        wire = (f"HTTP/1.1 {self.status} Fixture\r\n".encode("ascii")
                + b"".join(name.encode("ascii") + b": " + value.encode("ascii")
                           + b"\r\n" for name, value in self.headers)
                + b"\r\n" + self.body)
        self.body_start = len(wire) - len(self.body)
        self.stream = WireStream(
            wire, forbid_body_from=self.body_start if self.forbid_body else None)
        self.response = runtime.BoundedHTTPResponse(WireSocket(self.stream))
        try:
            self.response.begin()
        except BaseException:
            self.response.close()
            raise
        original_read = self.response.read
        original_close = self.response.close

        def checked_read(size=None):
            self.response_reads.append(size)
            if self.forbid_body:
                raise AssertionError("blob body must not be consumed")
            if type(size) is not int or size < 0:
                raise AssertionError("token reads must have a finite bound")
            if self.read_hook is not None:
                self.read_hook(len(self.response_reads))
            return original_read(size)

        self.response.read = checked_read
        if self.close_hook is not None:
            def checked_close():
                original_close()
                self.close_hook()
            self.response.close = checked_close
        if self.status >= 300:
            raise urllib.error.HTTPError(
                "https://registry-1.docker.io/fixture", self.status,
                "private exception message", self.response.headers, self.response)
        return self.response


class SequenceOpener:
    """Fail if a third request, redirect follow or retry is attempted."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append({"url": request.full_url,
                              "method": request.get_method(),
                              "headers": {key.lower(): value
                                          for key, value in request.header_items()},
                              "timeout": timeout})
        if len(self.requests) > len(self.replies):
            raise AssertionError("unselected request or retry")
        return self.replies[len(self.requests) - 1].open()


def token_reply(raw=None, *, headers=None):
    if raw is None:
        raw = json.dumps({"token": TOKEN}, separators=(",", ":")).encode("ascii")
    if headers is None:
        headers = [("Content-Length", str(len(raw)))]
    return WireReply(headers=headers, body=raw)


def blob_reply(status=307, *, locations=(LOCATION,), headers=()):
    body = b"private-blob-body-not-for-read"
    return WireReply(status=status,
                     headers=[*(('Location', value) for value in locations),
                              *headers, ("Content-Length", str(len(body)))],
                     body=body, forbid_body=True)


class LocationRefusalTests(unittest.TestCase):
    def refuses(self, raw):
        with self.assertRaises(observer.ObserverRefused) as caught:
            observer.validate_location(raw, TOKEN)
        self.assertRegex(str(caught.exception), r"\A[A-Z][A-Z0-9_]{0,63}\Z")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("not-for-output", str(caught.exception))

    def test_parser_normalization_cannot_turn_unsafe_input_into_a_hostname(self):
        rows = [
            "\thttps://" + HOSTNAME + "/p",
            "\x00https://" + HOSTNAME + "/p",
            "https://destina\ntion.example.test/p",
            "https://destination.\rexample.test/p",
            "HTTPS://" + HOSTNAME + "/p",
            "https://DESTINATION.example.test/p",
            "https://" + HOSTNAME + ":/p",
            "https://" + HOSTNAME + ":0443/p",
        ]
        for raw in rows:
            with self.subTest(raw=raw):
                self.refuses(raw)

    def test_userinfo_fragment_non_https_and_nonstandard_port_refuse(self):
        rows = [
            "https://user@" + HOSTNAME + "/p",
            "https://user:password@" + HOSTNAME + "/p",
            "https://@" + HOSTNAME + "/p",
            "https://" + HOSTNAME + "/p#private",
            "https://" + HOSTNAME + "/p#",
            "http://" + HOSTNAME + "/p",
            "ftp://" + HOSTNAME + "/p",
            "//" + HOSTNAME + "/p",
            "https://" + HOSTNAME + ":80/p",
            "https://" + HOSTNAME + ":65536/p",
        ]
        for raw in rows:
            with self.subTest(raw=raw):
                self.refuses(raw)

    def test_non_dns_hosts_and_ip_literals_cannot_be_public_hostname_evidence(self):
        hosts = [
            "", ".example.test", "destination..example.test",
            "-destination.example.test", "destination-.example.test",
            "destination_name.example.test", "destinati\u00f6n.example.test",
            "destination%2eexample.test", "x" * 64 + ".example.test",
            "127.0.0.1", "[::1]", "[::ffff:127.0.0.1]",
            "0x7f.1", "127.0x0.1", "0x7f.0.0.1", "0177.1", "127.1",
        ]
        for host in hosts:
            with self.subTest(host=host):
                self.refuses("https://" + host + "/p")

    def test_backslash_controls_oversize_and_non_string_locations_refuse(self):
        rows = [
            "https://" + HOSTNAME + "\\private/p",
            "https://" + HOSTNAME + "/p?value=private\\query",
            "https://" + HOSTNAME + "/p?value=private value",
            "https://" + HOSTNAME + "/p?value=private\x7fvalue",
            "https://" + HOSTNAME + "/" + "x" * 65536,
            None, True, b"https://destination.example.test/p", [], {},
        ]
        for index, raw in enumerate(rows):
            with self.subTest(case=index):
                self.refuses(raw)

    def test_a_token_reflected_into_the_hostname_is_never_exported(self):
        self.refuses("https://" + TOKEN + ".example.test/private")


class ObservationFixture(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".cache/oci-observer-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.work = Path(self.temporary.name)
        # Transport faults inject the unchanged runtime deliberately. Exact
        # plan/import/run admission is checked separately, without HTTP.
        self.admission = observer.Admission(observer.REPO, "a" * 64,
                                            MappingProxyType({}))

    def tearDown(self):
        self.temporary.cleanup()

    def observe(self, opener, *, deadline=None):
        return observer.observe(self.admission, self.work,
                                time.monotonic() + 20 if deadline is None else deadline,
                                opener=opener, runtime=runtime)

    def assert_private_values_absent(self, result):
        outputs = [json.dumps(result).encode()]
        outputs.extend(path.read_bytes() for path in self.work.rglob("*") if path.is_file())
        for raw in outputs:
            for private in (TOKEN, LOCATION, "not-for-output", "private-blob-body",
                            "private exception message"):
                self.assertNotIn(private.encode(), raw)

    def assert_no_blob_body(self, reply):
        self.assertIsNotNone(reply.response)
        self.assertEqual(reply.response_reads, [])
        self.assertTrue(reply.stream.closed)
        self.assertEqual(reply.stream.closed_at, reply.body_start)


class RequestAndBodyTests(ObservationFixture):
    def test_only_selected_token_and_blob_gets_with_no_follow_or_body_read(self):
        token, blob = token_reply(), blob_reply()
        opener = SequenceOpener(token, blob)
        result = self.observe(opener)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["http_status"], "REDIRECT_307")
        self.assertEqual(result["parsed_hostname"], HOSTNAME)
        self.assertIsNone(result["first_failure"])
        self.assertEqual(result["response_cleanup"], "CONFIRMED")
        self.assertEqual(result["owned_child_cleanup"], "UNKNOWN")
        self.assertTrue(result["counters_complete"])
        self.assertEqual([(r["method"], r["url"]) for r in opener.requests],
                         [("GET", observer.TOKEN_URL), ("GET", observer.BLOB_URL)])
        self.assertEqual(opener.requests[0]["headers"], {
            "accept-encoding": "identity", "user-agent": "zpm-native-alpine-qualification/1"})
        self.assertEqual(opener.requests[1]["headers"], {
            "accept-encoding": "identity", "user-agent": "zpm-native-alpine-qualification/1",
            "authorization": "Bearer " + TOKEN})
        self.assertTrue(all(0 < row["timeout"] <= 10 for row in opener.requests))
        self.assertEqual(result["counters"]["requests"], 2)
        self.assertEqual(result["counters"]["redirects"], 0)
        self.assertEqual(result["counters"]["artifacts_complete"], 0)
        self.assertEqual(result["counters"]["body_bytes"], len(token.body))
        self.assertEqual(result["counters"]["reserved_body_bytes"],
                         1048576 + observer.BLOB_BYTES)
        self.assertEqual(result["counters"]["metadata_reserved_bytes"], 2 * 65536)
        self.assert_no_blob_body(blob)
        self.assert_private_values_absent(result)

    def test_direct_200_and_other_statuses_cannot_produce_hostname_evidence(self):
        for status in (200, 204, 301, 302, 303, 308, 401, 403, 404, 429, 500):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=self.work) as name:
                token, blob = token_reply(), blob_reply(status)
                opener = SequenceOpener(token, blob)
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertIn(result["status"], {"INCONCLUSIVE", "REFUSED"})
                self.assertIsNone(result["parsed_hostname"])
                self.assertEqual(len(opener.requests), 2)
                self.assert_no_blob_body(blob)
                self.assert_private_values_absent(result)

    def test_missing_duplicate_and_unsafe_location_close_without_body_or_retry(self):
        cases = [
            ((), "LOCATION_CARDINALITY"),
            ((LOCATION, LOCATION), "LOCATION_CARDINALITY"),
            (("http://" + HOSTNAME + "/p",), "LOCATION_SCHEME"),
            (("https://user@" + HOSTNAME + "/p",), "LOCATION_USERINFO"),
            (("https://" + HOSTNAME + ":80/p",), "LOCATION_PORT"),
        ]
        for locations, failure in cases:
            with self.subTest(locations=locations), tempfile.TemporaryDirectory(dir=self.work) as name:
                blob = blob_reply(locations=locations)
                opener = SequenceOpener(token_reply(), blob)
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertEqual(result["status"], "REFUSED")
                self.assertEqual(result["first_failure"], failure)
                self.assertIsNone(result["parsed_hostname"])
                self.assertEqual(len(opener.requests), 2)
                self.assert_no_blob_body(blob)
                self.assert_private_values_absent(result)

    def test_untrusted_token_and_transport_errors_stop_before_the_blob(self):
        cases = [
            token_reply(b'{"token":"bad\\r\\ncredential"}'),
            token_reply(b'{"token":"first","token":"second"}'),
            token_reply(b'{"token":""}'),
            token_reply(b'{"access_token":"not-the-selected-field"}'),
            token_reply(b'{"token":null}'),
            WireReply(open_error=RuntimeError("private exception message " + TOKEN + " " + LOCATION)),
            WireReply(status=302, headers=[("Location", LOCATION)], forbid_body=True),
        ]
        for index, reply in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory(dir=self.work) as name:
                opener = SequenceOpener(reply)
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertIn(result["status"], {"FAILED", "REFUSED"})
                self.assertIn(result["first_failure"], observer.FAILURES)
                self.assertIsNone(result["parsed_hostname"])
                self.assertEqual(len(opener.requests), 1)
                self.assert_private_values_absent(result)

    def test_header_overflow_retains_observed_count_and_never_reads_blob_body(self):
        for where in ("token", "blob"):
            with self.subTest(where=where), tempfile.TemporaryDirectory(dir=self.work) as name:
                oversized = WireReply(status=307 if where == "blob" else 200,
                                      headers=[("X-Private", "x" * 65536)],
                                      body=b"private-blob-body", forbid_body=True)
                token = token_reply()
                opener = SequenceOpener(token, oversized) if where == "blob" else SequenceOpener(oversized)
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertEqual(result["first_failure"], "HTTP_HEADER_BOUND")
                self.assertNotEqual(result["status"], "OBSERVED")
                self.assertIsNone(result["parsed_hostname"])
                prior = token.response.response_header_bytes if where == "blob" else 0
                self.assertEqual(result["counters"]["response_header_bytes"], prior + 65537)
                self.assertEqual(len(opener.requests), 2 if where == "blob" else 1)
                self.assertEqual(oversized.response_reads, [])
                self.assertLess(oversized.stream.closed_at, oversized.body_start)
                self.assert_private_values_absent(result)


class TokenAndDeadlineTests(ObservationFixture):
    def test_exact_token_body_bound_passes_and_declared_overflow_reads_no_body(self):
        prefix = b'{"token":"' + TOKEN.encode() + b'","padding":"'
        exact = prefix + b"x" * (1048576 - len(prefix) - 2) + b'"}'
        self.assertEqual(len(exact), 1048576)
        token, blob = token_reply(exact), blob_reply()
        result = self.observe(SequenceOpener(token, blob))
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["counters"]["body_bytes"], 1048576)
        self.assertTrue(all(0 < size <= 65536 for size in token.response_reads))
        self.assert_no_blob_body(blob)
        with tempfile.TemporaryDirectory(dir=self.work) as name:
            overflow = token_reply(b"x", headers=[("Content-Length", "1048577")])
            opener = SequenceOpener(overflow)
            failed = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                      opener=opener, runtime=runtime)
            self.assertEqual(failed["first_failure"], "HTTP_CONTENT_LENGTH")
            self.assertEqual(overflow.response_reads, [])
            self.assertEqual(failed["counters"]["body_bytes"], 0)
            self.assertEqual(len(opener.requests), 1)

    def test_chunked_token_is_bounded_and_extensions_or_trailers_refuse(self):
        raw = b'{"token":"' + TOKEN.encode() + b'"}'
        chunks = f"{len(raw):x}\r\n".encode() + raw + b"\r\n0\r\n\r\n"
        for index, body in enumerate((chunks,
                chunks.replace(b"\r\n", b";private-extension\r\n", 1),
                chunks[:-2] + b"X-Private: not-for-output\r\n\r\n",
                b"100001\r\n")):
            with self.subTest(case=index), tempfile.TemporaryDirectory(dir=self.work) as name:
                token = token_reply(body, headers=[("Transfer-Encoding", "chunked")])
                opener = SequenceOpener(token, blob_reply())
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertTrue(token.stream.closed)
                if index == 0:
                    self.assertEqual(result["status"], "OBSERVED")
                    self.assertEqual(result["counters"]["body_bytes"], len(raw))
                    self.assertGreater(result["counters"]["response_framing_bytes"], 0)
                else:
                    self.assertNotEqual(result["status"], "OBSERVED")
                    self.assertIn(result["first_failure"],
                                  {"HTTP_CHUNK_SYNTAX", "HTTP_CHUNK_TRAILER", "ACQUISITION_BYTES"})
                    self.assertEqual(len(opener.requests), 1)
                self.assert_private_values_absent(result)

    def test_real_alarm_during_partial_token_body_keeps_bytes_and_no_second_request(self):
        raw = b'{"token":"' + TOKEN.encode() + b'","padding":"' + b"x" * 70000 + b'"}'
        token = token_reply(raw)
        token.read_hook = lambda count: signal.raise_signal(signal.SIGALRM) if count == 2 else None
        opener = SequenceOpener(token)
        result = self.observe(opener)
        self.assertEqual(result["first_failure"], "REQUEST_DEADLINE")
        self.assertEqual(result["counters"]["body_bytes"], 65536)
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse(result["counters_complete"])
        self.assertEqual(result["response_cleanup"], "CONFIRMED")
        self.assert_private_values_absent(result)

    def test_one_absolute_request_deadline_covers_headers_token_body_and_close(self):
        events, deadlines = [], []
        original_timeout = runtime.absolute_timeout

        @contextmanager
        def traced_timeout(deadline, code):
            deadlines.append((deadline, code))
            events.append("enter")
            with original_timeout(deadline, code):
                yield
            events.append("exit")

        token, blob = token_reply(), blob_reply()
        token.read_hook = lambda _: events.append("token-read")
        token.close_hook = lambda: events.append("token-close")
        blob.close_hook = lambda: events.append("blob-close")
        original_open = token.open
        def token_open():
            events.append("token-open")
            return original_open()
        token.open = token_open
        deadline = time.monotonic() + .5
        with patch.object(runtime, "absolute_timeout", side_effect=traced_timeout):
            result = self.observe(SequenceOpener(token, blob), deadline=deadline)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines, [(deadline, "REQUEST_DEADLINE")] * 2)
        first_exit = events.index("exit")
        self.assertLess(events.index("token-open"), first_exit)
        self.assertLess(events.index("token-read"), first_exit)
        self.assertLess(events.index("token-close"), first_exit)
        self.assertEqual(events[-2:], ["blob-close", "exit"])

    def test_real_alarm_in_close_preserves_original_deadline_or_earlier_refusal(self):
        for earlier in (None, "HTTP_CONTENT_LENGTH", "HTTP_ENCODING"):
            with self.subTest(earlier=earlier), tempfile.TemporaryDirectory(dir=self.work) as name:
                token = token_reply()
                if earlier == "HTTP_CONTENT_LENGTH":
                    token.headers = [("Content-Length", "0")]
                elif earlier == "HTTP_ENCODING":
                    token.headers.append(("Content-Encoding", "gzip"))
                token.close_hook = lambda: signal.raise_signal(signal.SIGALRM)
                opener = SequenceOpener(token)
                result = observer.observe(self.admission, Path(name), time.monotonic() + 20,
                                          opener=opener, runtime=runtime)
                self.assertEqual(result["first_failure"], earlier or "REQUEST_DEADLINE")
                self.assertEqual(result["response_cleanup"], "UNKNOWN")
                self.assertEqual(len(opener.requests), 1)
                self.assert_private_values_absent(result)


class ClosedProjectionTests(ObservationFixture):
    def valid_result(self):
        result = self.observe(SequenceOpener(token_reply(), blob_reply()))
        self.assertEqual(result["status"], "OBSERVED")
        return result

    def test_unhashable_json_fields_refuse_without_escaping_the_closed_projection(self):
        original = self.valid_result()
        for field in ("status", "first_failure", "http_status"):
            for invalid in ([], {}, [TOKEN, LOCATION]):
                with self.subTest(field=field, invalid=type(invalid).__name__):
                    value = copy.deepcopy(original)
                    value[field] = invalid
                    result = observer.safe_result(value)
                    self.assertNotEqual(result["status"], "OBSERVED")
                    self.assertIn(result["first_failure"], observer.FAILURES)
                    self.assert_private_values_absent(result)
        for invalid in ([], {}, [TOKEN, LOCATION]):
            value = copy.deepcopy(original)
            value["predicates"]["hostname"] = invalid
            result = observer.safe_result(value)
            self.assertIsNone(result["parsed_hostname"])
            self.assertNotEqual(result["status"], "OBSERVED")
            self.assert_private_values_absent(result)

    def test_a_failure_cannot_remain_an_observed_success(self):
        value = self.valid_result()
        value["first_failure"] = "REQUEST_DEADLINE"
        result = observer.safe_result(value)
        self.assertNotEqual(result["status"], "OBSERVED")
        self.assertEqual(result["first_failure"], "REQUEST_DEADLINE")

    def test_corrupt_counters_cannot_claim_complete_observation(self):
        original = self.valid_result()
        cases = [("requests", 3), ("requests", True), ("redirects", 1),
                 ("artifacts_complete", 1), ("body_bytes", -1),
                 ("body_bytes", [TOKEN]), ("reserved_body_bytes", 0)]
        for name, invalid in cases:
            with self.subTest(counter=name, invalid=type(invalid).__name__):
                value = copy.deepcopy(original)
                value["counters"][name] = invalid
                result = observer.safe_result(value)
                self.assertNotEqual(result["status"], "OBSERVED")
                self.assertFalse(result["counters_complete"])
                self.assert_private_values_absent(result)

    def test_child_claims_of_effects_are_not_rewritten_as_zero_effects(self):
        original = self.valid_result()
        for field in ("blob_body_consumed", "redirect_followed"):
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                value[field] = True
                result = observer.safe_result(value)
                self.assertNotEqual(result["status"], "OBSERVED")
                self.assertIsNot(result[field], False)

    def test_missing_counters_unknown_response_cleanup_and_non307_never_upgrade(self):
        original = self.valid_result()
        for change in ("missing-counter", "unknown-close", "wrong-redirect", "native-claim"):
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                if change == "missing-counter":
                    del value["counters"]["body_bytes"]
                elif change == "unknown-close":
                    value["response_cleanup"] = "UNKNOWN"
                elif change == "wrong-redirect":
                    value["http_status"] = "REDIRECT_302"
                else:
                    value["native_execution"] = "QUALIFIED"
                result = observer.safe_result(value)
                self.assertNotEqual(result["status"], "OBSERVED")
                self.assertIn(result["first_failure"], observer.FAILURES)
                if change == "missing-counter":
                    self.assertIsNone(result["counters"]["body_bytes"])
                    self.assertFalse(result["counters_complete"])
                elif change == "unknown-close":
                    self.assertEqual(result["response_cleanup"], "UNKNOWN")
                elif change == "wrong-redirect":
                    self.assertIsNone(result["parsed_hostname"])
                else:
                    self.assertEqual(result["native_execution"], "UNKNOWN")


class OwnedChildTests(ObservationFixture):
    def setUp(self):
        super().setUp()
        self.owned = []
        self.real_fork = os.fork
        self.real_waitpid = os.waitpid

    def fork(self):
        pid = self.real_fork()
        if pid > 0:
            self.owned.append(pid)
        return pid

    def tearDown(self):
        # A failing custody test must not itself leave an owned child behind.
        # Reconfirm the direct-child relation before any final test-only kill.
        for pid in self.owned:
            try:
                found, _ = self.real_waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if found == 0:
                os.kill(pid, signal.SIGKILL)
                self.real_waitpid(pid, 0)
        super().tearDown()

    def observed_worker(self, work, deadline):
        return observer.observe(self.admission, work, deadline,
                                opener=SequenceOpener(token_reply(), blob_reply()), runtime=runtime)

    def assert_reaped(self):
        self.assertEqual(len(self.owned), 1)
        with self.assertRaises(ChildProcessError):
            self.real_waitpid(self.owned[0], os.WNOHANG)

    def test_actual_worker_scrubs_proxy_credentials_and_tls_overrides(self):
        work = self.work / "operation"
        sensitive_names = ("HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
                           "SSL_CERT_DIR", "SSLKEYLOGFILE", "OPENAI_API_KEY")

        def worker(directory, deadline):
            def build(*handlers):
                proxy, redirect, tls = handlers
                facts = {
                    "environment_scrubbed": all(name not in os.environ for name in sensitive_names),
                    "proxies_disabled": isinstance(proxy, urllib.request.ProxyHandler) and proxy.proxies == {},
                    "redirects_disabled": isinstance(redirect, runtime.NoRedirect),
                    "hostname_check": tls.context.check_hostname is True,
                    "certificate_check": tls.context.verify_mode == ssl.CERT_REQUIRED,
                    "keylog_disabled": tls.context.keylog_filename is None,
                }
                (directory / "fixture-environment.json").write_text(json.dumps(facts))
                return SequenceOpener(token_reply(), blob_reply())

            with patch.object(observer.urllib.request, "build_opener", side_effect=build), \
                    patch.object(observer.urllib.request, "getproxies", side_effect=AssertionError("no inherited proxy discovery")):
                return observer.observe(self.admission, directory, deadline, runtime=runtime)

        inherited = {name: str(self.work / "private-override") for name in sensitive_names}
        with patch.dict(os.environ, inherited), patch.object(observer.os, "fork", side_effect=self.fork):
            result = observer.supervise(worker, work, total_seconds=2, worker_seconds=1)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        facts = json.loads((work / "fixture-environment.json").read_bytes())
        self.assertEqual(set(facts), {"environment_scrubbed", "proxies_disabled", "redirects_disabled",
                                    "hostname_check", "certificate_check", "keylog_disabled"})
        self.assertTrue(all(facts.values()))
        self.assertFalse((self.work / "private-override").exists())
        self.assert_reaped()

    def test_real_ignored_term_worker_is_killed_and_reaped_inside_original_budget(self):
        def worker(work, deadline):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(.01)

        started = time.monotonic()
        with patch.object(observer.os, "fork", side_effect=self.fork):
            result = observer.supervise(worker, self.work / "operation",
                                        total_seconds=.8, worker_seconds=.15)
        self.assertEqual(result["first_failure"], "OPERATION_DEADLINE")
        self.assertNotEqual(result["status"], "OBSERVED")
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_reaped()

    def test_interrupt_after_real_reap_never_signals_the_retired_pid(self):
        triggered = False

        def waitpid(pid, options):
            nonlocal triggered
            result = self.real_waitpid(pid, options)
            if result[0] == pid and not triggered:
                triggered = True
                signal.raise_signal(signal.SIGTERM)
            return result

        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(observer.os, "fork", side_effect=self.fork), \
                patch.object(observer.os, "waitpid", side_effect=waitpid), \
                patch.object(observer.os, "killpg", side_effect=AssertionError("retired group")) as kill_group, \
                patch.object(observer.os, "kill", side_effect=AssertionError("retired pid")) as kill_pid:
            result = observer.supervise(self.observed_worker, self.work / "operation",
                                        total_seconds=1, worker_seconds=.5)
        self.assertTrue(triggered)
        self.assertEqual(result["first_failure"], "INTERRUPTED")
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        kill_group.assert_not_called()
        kill_pid.assert_not_called()
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)
        self.assert_reaped()

    def test_interrupt_after_fork_keeps_child_custody_and_stops_before_worker_progress(self):
        gate_read, gate_write = os.pipe()
        signalled = False
        original_signal = observer._signal_child

        def fork():
            pid = self.fork()
            if pid == 0:
                os.close(gate_write)
                os.read(gate_read, 1)
                os.close(gate_read)
            else:
                signal.raise_signal(signal.SIGTERM)
            return pid

        def signal_child(pid, signum):
            nonlocal signalled
            original_signal(pid, signum)
            if not signalled:
                signalled = True
                os.write(gate_write, b"x")

        def worker(work, deadline):
            (work / "unwanted-progress").write_text("worker should not run")
            while True:
                time.sleep(.01)

        try:
            with patch.object(observer.os, "fork", side_effect=fork), \
                    patch.object(observer, "_signal_child", side_effect=signal_child):
                result = observer.supervise(worker, self.work / "operation",
                                            total_seconds=1, worker_seconds=.5)
            self.assertEqual(result["first_failure"], "INTERRUPTED")
            self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
            self.assertTrue(signalled)
            self.assertFalse((self.work / "operation/unwanted-progress").exists())
            self.assert_reaped()
        finally:
            os.close(gate_read)
            os.close(gate_write)

    def test_final_read_cannot_turn_a_result_after_total_deadline_into_success(self):
        current = [time.monotonic()]
        original_read = observer.read_file

        def delayed_read(path, maximum, **kwargs):
            raw = original_read(path, maximum, **kwargs)
            if Path(path).name == "worker-result.json":
                current[0] += 2
            return raw

        with patch.object(observer.os, "fork", side_effect=self.fork), \
                patch.object(observer, "read_file", side_effect=delayed_read):
            result = observer.supervise(self.observed_worker, self.work / "operation",
                                        clock=lambda: current[0], total_seconds=1, worker_seconds=.5)
        self.assertNotEqual(result["status"], "OBSERVED")
        self.assertEqual(result["first_failure"], "OPERATION_DEADLINE")
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        self.assert_reaped()

    def test_late_final_read_keeps_an_earlier_observed_request_failure(self):
        current = [time.monotonic()]
        original_read = observer.read_file

        def worker(work, deadline):
            return observer.observe(self.admission, work, deadline,
                                    opener=SequenceOpener(token_reply(b'{"token":""}')),
                                    runtime=runtime)

        def delayed_read(path, maximum, **kwargs):
            raw = original_read(path, maximum, **kwargs)
            if Path(path).name == "worker-result.json":
                current[0] += 2
            return raw

        with patch.object(observer.os, "fork", side_effect=self.fork), \
                patch.object(observer, "read_file", side_effect=delayed_read):
            result = observer.supervise(worker, self.work / "operation",
                                        clock=lambda: current[0], total_seconds=1, worker_seconds=.5)
        self.assertEqual(result["first_failure"], "REGISTRY_TOKEN")
        self.assertEqual(result["counters"]["requests"], 1)
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        self.assert_reaped()

    def test_child_cannot_attest_its_own_reaping(self):
        def worker(work, deadline):
            result = self.observed_worker(work, deadline)
            result["owned_child_cleanup"] = "CONFIRMED"
            return result

        with patch.object(observer.os, "fork", side_effect=self.fork):
            result = observer.supervise(worker, self.work / "operation",
                                        total_seconds=1, worker_seconds=.5)
        self.assertEqual(result["first_failure"], "CHILD_RESULT")
        self.assertNotEqual(result["status"], "OBSERVED")
        # The parent really did reap this child. Its proof, not the rejected
        # child assertion, supplies this independent cleanup result.
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        self.assert_reaped()


class RealGitAdmissionTests(ObservationFixture):
    def git(self, *arguments):
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
        return subprocess.check_output(
            ["/usr/bin/git", "-c", "core.hooksPath=" + os.devnull,
             "-c", "user.name=OCI observer fixture", "-c", "user.email=fixture@example.invalid",
             "-C", str(self.repo), *arguments], env=environment, stderr=subprocess.DEVNULL).decode().strip()

    def prepare_repo(self, *, commit=True):
        self.repo = self.work / "repository"
        self.repo.mkdir()
        self.git("init", "-q")
        for relative in (*observer.CODE_PATHS, observer.RECIPE_PATH):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((observer.REPO / relative).read_bytes())
        self.plan_path = self.repo / observer.PLAN_PATH.relative_to(observer.REPO)
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_plan()
        if commit:
            self.git("add", ".")
        else:
            (self.repo / "README.md").write_text("No selected inputs are committed.\n")
            self.git("add", "README.md")
        self.git("commit", "-q", "-m", "fixture source")
        self.revision = self.git("rev-parse", "HEAD")

    def write_plan(self):
        plan = copy.deepcopy(observer.FIXED_PLAN)
        plan["code"] = {}
        for relative in observer.CODE_PATHS:
            raw = (self.repo / relative).read_bytes()
            plan["code"][relative] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        raw = (json.dumps(plan, sort_keys=True, indent=2) + "\n").encode()
        self.plan_path.write_bytes(raw)
        self.plan_sha = hashlib.sha256(raw).hexdigest()

    def load_fixture_admission(self):
        return observer.load_plan(self.plan_path, self.plan_sha, self.repo)

    def admit(self, admission=None, *, revision=None):
        revision = revision or self.revision
        admission = admission or self.load_fixture_admission()
        return observer.admit_run(admission, observer.RUN_PREFIX + admission.plan_sha256 + "/" + revision,
                                  revision, "1", "123456", deadline=time.monotonic() + 5,
                                  cleanup_deadline=time.monotonic() + 6)

    def test_committed_selected_bytes_admit_with_unrelated_dirty_files(self):
        self.prepare_repo()
        (self.repo / "unrelated-local-note").write_text("not part of this operation")
        result = self.admit()
        self.assertEqual(result["source_revision"], self.revision)
        self.assertEqual(result["plan_sha256"], self.plan_sha)

    def test_matching_working_bytes_cannot_stand_in_for_untracked_commit_inputs(self):
        self.prepare_repo(commit=False)
        with patch.object(observer, "observe", side_effect=AssertionError("no worker before admission")), \
                self.assertRaises(observer.ObserverRefused):
            self.admit()

    def test_replanned_modified_bytes_cannot_claim_an_older_selected_commit(self):
        self.prepare_repo()
        selected = self.repo / "tests/contracts/check_oci_destination.py"
        selected.write_bytes(selected.read_bytes() + b"\n# modified after committed selection\n")
        self.write_plan()
        with self.assertRaises(observer.ObserverRefused):
            self.admit()

    def test_equivalent_external_plan_bytes_must_still_match_the_committed_plan(self):
        self.prepare_repo()
        original = self.plan_path.read_bytes()
        external = self.work / "alternate-plan.json"
        changed = json.dumps(json.loads(original), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        self.assertNotEqual(original, changed)
        external.write_bytes(changed)
        admission = observer.load_plan(external, hashlib.sha256(changed).hexdigest(), self.repo)
        with self.assertRaises(observer.ObserverRefused):
            self.admit(admission)

    def test_replace_refs_cannot_make_modified_source_match_the_original_commit(self):
        self.prepare_repo()
        original = self.revision
        selected = self.repo / "tests/contracts/check_oci_destination.py"
        selected.write_bytes(selected.read_bytes() + b"\n# replacement view\n")
        self.write_plan()
        self.git("add", ".")
        self.git("commit", "-q", "-m", "replacement source")
        replacement = self.git("rev-parse", "HEAD")
        self.git("reset", "--soft", original)
        self.git("replace", original, replacement)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        with self.assertRaises(observer.ObserverRefused):
            self.admit(revision=original)

    def test_captured_imports_ignore_ambient_modules_bytecode_and_later_source_changes(self):
        self.prepare_repo()
        admission = self.load_fixture_admission()
        self.admit(admission)
        marker = self.work / "unexpected-import"
        poison = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\nraise RuntimeError('poison')\n"
        (self.repo / "tests/contracts/__init__.py").write_text(poison)
        selected = self.repo / "zpm/dependencies.py"
        selected.write_text(poison)
        bytecode = selected.parent / "__pycache__/dependencies.cpython-312.pyc"
        bytecode.parent.mkdir(exist_ok=True)
        bytecode.write_bytes(b"not accepted bytecode")
        ambient = ModuleType("zpm.dependencies")
        with patch.dict(sys.modules, {"zpm.dependencies": ambient}):
            captured = observer.load_runtime(admission)
            self.assertIsNot(captured, runtime)
            self.assertIs(sys.modules["zpm.dependencies"], ambient)
        self.assertFalse(marker.exists())
        with self.assertRaises(observer.ObserverRefused):
            self.load_fixture_admission()
        self.assertFalse(marker.exists())


class AdmissionAndPublicationTests(ObservationFixture):
    def identity(self):
        return {"plan_sha256": self.admission.plan_sha256, "source_revision": "b" * 40,
                "run_id": "123456", "run_attempt": 1,
                "run_ref": observer.RUN_PREFIX + self.admission.plan_sha256 + "/" + "b" * 40,
                "recipe_sha256": observer.RECIPE_SHA, "registry_blob_digest": observer.BLOB_DIGEST}

    def github_output(self):
        path = self.work / "github-output"
        path.write_bytes(b"")
        path.chmod(0o600)
        return path

    def call_main(self, work):
        output = io.StringIO()
        with redirect_stdout(output):
            status = observer.main(["--run", "--reviewed-plan-sha256", self.admission.plan_sha256,
                                    "--work", str(work)])
        raw = output.getvalue().encode()
        for private in (TOKEN, LOCATION, "not-for-output"):
            self.assertNotIn(private.encode(), raw)
        self.assertLessEqual(len(raw), observer.LIMITS["report_bytes"])
        return status, json.loads(raw)

    def test_wrong_ref_source_or_attempt_refuses_before_any_git_or_worker(self):
        identity = self.identity()
        rows = [
            ("refs/heads/ordinary", "b" * 40, "1", "123456", "RUN_REFERENCE"),
            (observer.RUN_PREFIX + "c" * 64 + "/" + "b" * 40, "b" * 40, "1", "123456", "RUN_REFERENCE"),
            (identity["run_ref"], "c" * 40, "1", "123456", "RUN_SOURCE"),
            (identity["run_ref"], "b" * 40, "2", "123456", "RUN_ATTEMPT"),
            (identity["run_ref"], "b" * 40, "1", "0", "RUN_IDENTITY"),
        ]
        with patch.object(observer, "checkout_revision", side_effect=AssertionError("before Git")) as checkout, \
                patch.object(observer, "observe", side_effect=AssertionError("before HTTP")) as observe:
            for ref, source, attempt, run_id, code in rows:
                with self.subTest(code=code), self.assertRaises(observer.ObserverRefused) as caught:
                    observer.admit_run(self.admission, ref, source, attempt, run_id)
                self.assertEqual(caught.exception.code, code)
        checkout.assert_not_called()
        observe.assert_not_called()

    def test_reused_work_never_forks_overwrites_or_makes_old_summary_eligible(self):
        original = b'{"private-existing-report":"' + TOKEN.encode() + b'"}\n'
        summary = self.work / "summary.json"
        summary.write_bytes(original)
        output = self.github_output()
        with patch.object(observer, "load_plan", return_value=self.admission), \
                patch.object(observer, "admit_run", return_value=self.identity()), \
                patch.object(observer.os, "fork", side_effect=AssertionError("no child from reused work")) as fork, \
                patch.object(observer, "observe", side_effect=AssertionError("no HTTP from reused work")) as observe, \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            status, result = self.call_main(self.work)
        self.assertEqual(status, 2)
        self.assertEqual(result["first_failure"], "WORK_REUSED")
        self.assertEqual(summary.read_bytes(), original)
        self.assertEqual(output.read_bytes(), b"")
        fork.assert_not_called()
        observe.assert_not_called()

    def test_summary_write_failure_retains_original_admitted_failure_and_identity(self):
        original = self.observe(SequenceOpener(token_reply(b'{"token":""}')))
        self.assertEqual(original["first_failure"], "REGISTRY_TOKEN")
        # Orchestration fixture: real parent reaping is exercised above.
        original["owned_child_cleanup"] = "CONFIRMED"
        summary = self.work / "summary.json"
        summary.write_bytes(b"existing evidence must survive")
        output = self.github_output()
        with patch.object(observer, "load_plan", return_value=self.admission), \
                patch.object(observer, "admit_run", return_value=self.identity()), \
                patch.object(observer, "supervise", return_value=copy.deepcopy(original)), \
                patch.object(observer, "mark_current_summary", wraps=observer.mark_current_summary) as marker, \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            status, result = self.call_main(self.work)
        self.assertEqual(status, 2)
        self.assertEqual(result["first_failure"], "REGISTRY_TOKEN")
        self.assertEqual(result["report_failure"], "REPORT_WRITE")
        self.assertEqual(result["counters"], original["counters"])
        self.assertEqual(result["response_cleanup"], original["response_cleanup"])
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        for key, value in self.identity().items():
            self.assertEqual(result[key], value)
        self.assertEqual(summary.read_bytes(), b"existing evidence must survive")
        self.assertEqual(output.read_bytes(), b"")
        marker.assert_not_called()

    def test_current_success_and_failure_have_distinct_reserved_eligible_exits(self):
        self.assertEqual(observer.CURRENT_FAILURE_EXIT, 20)
        for success in (False, True):
            with self.subTest(success=success), tempfile.TemporaryDirectory(dir=self.work) as name:
                work = Path(name)
                opener = (SequenceOpener(token_reply(), blob_reply()) if success else
                          SequenceOpener(token_reply(b'{"token":""}')))
                original = observer.observe(self.admission, work, time.monotonic() + 20,
                                            opener=opener, runtime=runtime)
                original["owned_child_cleanup"] = "CONFIRMED"
                output = self.github_output()
                with patch.object(observer, "load_plan", return_value=self.admission), \
                        patch.object(observer, "admit_run", return_value=self.identity()), \
                        patch.object(observer, "supervise", return_value=copy.deepcopy(original)), \
                        patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                    status, result = self.call_main(work)
                self.assertEqual(status, 0 if success else 20)
                self.assertEqual(result["first_failure"], None if success else "REGISTRY_TOKEN")
                self.assertEqual(result, json.loads((work / "summary.json").read_bytes()))
                # Only the workflow interprets the reserved exit after Python
                # finishes. The producer does not emit an early upload marker.
                self.assertEqual(output.read_bytes(), b"")

    def test_delayed_final_marker_read_cannot_enable_a_saved_over_budget_success(self):
        original = self.observe(SequenceOpener(token_reply(), blob_reply()))
        original["owned_child_cleanup"] = "CONFIRMED"
        output = self.github_output()
        current = [time.monotonic()]
        original_read = observer.read_file

        def delayed_read(path, maximum, **kwargs):
            value = original_read(path, maximum, **kwargs)
            if Path(path).name == "summary.json":
                current[0] += 31
            return value

        with patch.object(observer, "load_plan", return_value=self.admission), \
                patch.object(observer, "admit_run", return_value=self.identity()), \
                patch.object(observer, "supervise", return_value=copy.deepcopy(original)), \
                patch.object(observer, "read_file", side_effect=delayed_read), \
                patch.object(observer.time, "monotonic", side_effect=lambda: current[0]), \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            status, result = self.call_main(self.work)
        self.assertEqual(status, 2)
        self.assertEqual(result["first_failure"], "OPERATION_DEADLINE")
        self.assertEqual(result["owned_child_cleanup"], "CONFIRMED")
        self.assertEqual(result["source_revision"], self.identity()["source_revision"])
        self.assertEqual(output.read_bytes(), b"")

    def test_oversized_private_report_is_refused_before_file_creation(self):
        path = self.work / "never-published.json"
        with self.assertRaises(observer.ObserverRefused) as caught:
            observer.write_private(path, {"raw": (TOKEN + LOCATION) * 1000})
        self.assertEqual(caught.exception.code, "REPORT_BOUND")
        self.assertFalse(path.exists())
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(LOCATION, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
