"""Independent native response-custody regressions for issue #63.

Real urllib dispatch and the bounded HTTP parser use only local fixture bytes.
No external requests, Docker, candidate imports or native activation occur.
Authored by ChatGPT /root/openjules_investigation, 15 November 2026.
"""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

from tests.contracts import check_alpine_sealed_runtime as runtime


ORIGIN = runtime.REGISTRY + "sha256:" + "a" * 64
DESTINATION = ("https://production.cloudfront.docker.com/registry-v2/docker/"
               "registry/v2/blobs/sha256/aa/data?opaque=private-signature")
TOKEN = "fixture-private-token"
ALIASES = (301, 302, 303, 307, 308)
BODY = b"fixture-artifact"


class WireStream(io.BytesIO):
    def __init__(self, wire, body_start, *, forbid_body):
        super().__init__(wire)
        self.body_start, self.forbid_body = body_start, forbid_body
        self.closed_at = None

    def checked(self, value):
        if value and self.forbid_body and self.tell() > self.body_start:
            raise AssertionError("redirect/error body was consumed")
        return value

    def read(self, size=-1):
        return self.checked(super().read(size))

    def readline(self, size=-1):
        return self.checked(super().readline(size))

    def close(self):
        if not self.closed:
            self.closed_at = self.tell()
        super().close()


class Reply:
    def __init__(self, status, *, locations=(), headers=(), body=BODY,
                 close_error=None, close_before_error=True):
        self.status, self.body = status, body
        self.headers = [*(('Location', value) for value in locations), *headers,
                        ("Content-Length", str(len(body)))]
        self.close_error, self.close_before_error = close_error, close_before_error
        self.close_calls = 0
        self.read_calls = []
        self.stream = self.response = self.original_close = None

    def open(self, url):
        wire = (f"HTTP/1.1 {self.status} Fixture\r\n".encode("ascii")
                + b"".join(name.encode("ascii") + b": " + value.encode("latin-1")
                           + b"\r\n" for name, value in self.headers)
                + b"\r\n" + self.body)
        self.stream = WireStream(wire, len(wire) - len(self.body),
                                 forbid_body=self.status != 200)
        stream = self.stream

        class Socket:
            def makefile(self, mode):
                if mode != "rb":
                    raise AssertionError("unexpected socket mode")
                return stream

        response = runtime.BoundedHTTPResponse(Socket())
        response.begin()
        response.url, response.msg = url, response.reason
        self.response, self.original_close = response, response.close
        original_read = response.read

        def read(size=None):
            self.read_calls.append(size)
            if self.status != 200:
                raise AssertionError("redirect/error body was consumed")
            if type(size) is not int or size < 0:
                raise AssertionError("body read must remain bounded")
            return original_read(size)

        def close():
            self.close_calls += 1
            if self.close_before_error:
                self.original_close()
            if self.close_error is not None:
                raise self.close_error
            if not self.close_before_error:
                self.original_close()

        response.read, response.close = read, close
        return response

    def cleanup(self):
        if self.response is not None:
            self.response.close = self.original_close
            self.original_close()


class Destination:
    """An owned output stream whose close remains observable after a read fault."""
    def __init__(self, close_error=None):
        self.stream = io.BytesIO()
        self.close_error = close_error
        self.closed = False

    def open(self, mode):
        if mode != "xb":
            raise AssertionError("destination creation mode changed")
        return self

    def write(self, value):
        return self.stream.write(value)

    def close(self):
        self.closed = True
        self.stream.close()
        if self.close_error is not None:
            raise self.close_error


class NativeRedirectCustodyTests(unittest.TestCase):
    @contextmanager
    def acquisition(self, *replies):
        requests = []
        original_build = urllib.request.build_opener

        class SyntheticHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                index = len(requests)
                requests.append(request)
                if index >= len(replies):
                    raise AssertionError("unexpected follow, retry or extra request")
                return replies[index].open(request.full_url)

        class NoHTTP(urllib.request.HTTPHandler):
            def http_open(self, request):
                raise AssertionError("non-HTTPS transport was attempted")

        def build(*handlers):
            # Replace transport only. Retain production redirect/error dispatch.
            handlers = [item for item in handlers
                        if not isinstance(item, urllib.request.HTTPSHandler)]
            return original_build(*handlers, SyntheticHTTPS(), NoHTTP())

        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / "inputs"
            directory.mkdir()
            try:
                with patch.object(runtime.urllib.request, "build_opener", side_effect=build), \
                        patch.object(socket, "create_connection", side_effect=AssertionError("live network forbidden")), \
                        patch.object(socket, "getaddrinfo", side_effect=AssertionError("live DNS forbidden")):
                    acquisition = runtime.Acquisition(directory, time.monotonic() + 3)
                    yield acquisition, requests
            finally:
                for reply in replies:
                    reply.cleanup()

    def assert_closed_without_body(self, reply):
        self.assertGreater(reply.close_calls, 0)
        self.assertEqual(reply.read_calls, [])
        self.assertTrue(reply.stream.closed)
        self.assertEqual(reply.stream.closed_at, reply.stream.body_start)

    def assert_private_journal(self, acquisition):
        raw = (acquisition.directory.parent / "acquisition.json").read_bytes()
        self.assertEqual(json.loads(raw), acquisition.record)
        for value in (TOKEN, "private-signature", "private-close", "private-read",
                      "private-invalid.example"):
            self.assertNotIn(value.encode(), raw)

    def test_valid_existing_redirects_keep_single_hop_token_scope_and_exact_accounting(self):
        for status in ALIASES:
            with self.subTest(status=status):
                redirect, artifact = Reply(status, locations=(DESTINATION,)), Reply(200)
                with self.acquisition(redirect, artifact) as (acquisition, requests):
                    opened = acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertEqual(acquisition.receive(opened, len(BODY)),
                                     (len(BODY), hashlib.sha256(BODY).hexdigest(), BODY))
                    self.assertEqual([request.full_url for request in requests], [ORIGIN, DESTINATION])
                    self.assertEqual(requests[0].get_header("Authorization"), "Bearer " + TOKEN)
                    self.assertIsNone(requests[1].get_header("Authorization"))
                    self.assertIsNone(requests[1].get_header("Cookie"))
                    self.assert_closed_without_body(redirect)
                    self.assertTrue(artifact.stream.closed)
                    self.assertEqual(acquisition.record["requests"], 2)
                    self.assertEqual(acquisition.record["redirects"], 1)
                    self.assertEqual(acquisition.record["body_bytes"], len(BODY))
                    self.assertEqual(acquisition.record["reserved_body_bytes"], 2 * len(BODY))
                    self.assertEqual(acquisition.record["metadata_reserved_bytes"], 2 * 65536)
                    self.assertEqual(acquisition.record["response_header_bytes"],
                                     redirect.stream.body_start + artifact.stream.body_start)
                    self.assert_private_journal(acquisition)

    def test_real_urllib_malformed_location_is_closed_for_every_inherited_alias(self):
        for status in ALIASES:
            with self.subTest(status=status):
                reply = Reply(status, locations=("https://[broken/private",))
                with self.acquisition(reply) as (acquisition, requests):
                    with self.assertRaises(ValueError):
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertEqual(len(requests), 1)
                    self.assert_closed_without_body(reply)
                    self.assertEqual(acquisition.record["redirects"], 0)
                    self.assertEqual(acquisition.record["response_header_bytes"], reply.stream.body_start)
                    self.assert_private_journal(acquisition)

    def test_unsafe_or_ambiguous_original_location_refuses_without_repair_or_follow(self):
        cases = [
            (("http://production.cloudfront.docker.com/private",), (), "HTTPS_SCHEME"),
            (("https://private-invalid.example/private",), (), "HOSTNAME"),
            ((DESTINATION + "#private",), (), "FRAGMENT"),
            ((DESTINATION + " ",), (), "PRINTABLE_ASCII"),
            ((DESTINATION + "\\private",), (), "BACKSLASH"),
            ((DESTINATION + "\xe9",), (), "PRINTABLE_ASCII"),
            ((DESTINATION, DESTINATION), (), "LOCATION_CARDINALITY"),
            ((), (("URI", DESTINATION),), "LOCATION_CARDINALITY"),
        ]
        for locations, headers, predicate in cases:
            with self.subTest(predicate=predicate, locations=locations):
                reply = Reply(307, locations=locations, headers=headers)
                with self.acquisition(reply) as (acquisition, requests):
                    with self.assertRaises(runtime.Refused) as caught:
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertEqual(str(caught.exception), "OCI_REDIRECT_REFUSED")
                    self.assertEqual(runtime._oci_redirect_predicate(caught.exception), predicate)
                    self.assertEqual(len(requests), 1)
                    self.assert_closed_without_body(reply)
                    self.assert_private_journal(acquisition)

    def test_error_statuses_close_without_error_body_or_extra_request(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                reply = Reply(status)
                with self.acquisition(reply) as (acquisition, requests):
                    with self.assertRaisesRegex(runtime.Refused, "^ARTIFACT_HTTP_REFUSED$"):
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertEqual(len(requests), 1)
                    self.assert_closed_without_body(reply)
                    self.assert_private_journal(acquisition)

    def test_second_redirect_stays_refused_and_cannot_forward_the_registry_token(self):
        for status in ALIASES:
            with self.subTest(status=status):
                first = Reply(307, locations=(DESTINATION,))
                second = Reply(status, locations=(DESTINATION,))
                with self.acquisition(first, second) as (acquisition, requests):
                    with self.assertRaisesRegex(runtime.Refused, "^ARTIFACT_HTTP_REFUSED$"):
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertEqual(len(requests), 2)
                    self.assertIsNone(requests[1].get_header("Authorization"))
                    self.assertIsNone(requests[1].get_header("Cookie"))
                    self.assertEqual(acquisition.record["redirects"], 1)
                    self.assertEqual(acquisition.record["body_bytes"], 0)
                    self.assert_closed_without_body(first)
                    self.assert_closed_without_body(second)
                    self.assert_private_journal(acquisition)

    def test_original_redirect_refusal_survives_later_close_failure(self):
        real_redirect = runtime.redirect_url
        for close_error in (OSError("private-close"), KeyboardInterrupt("private-close")):
            for close_first in (False, True):
                with self.subTest(close_error=type(close_error).__name__, closed_first=close_first):
                    observed = []

                    def validate(*args):
                        try:
                            return real_redirect(*args)
                        except BaseException as error:
                            observed.append(error)
                            raise

                    reply = Reply(307, locations=("https://private-invalid.example/private",),
                                  close_error=close_error, close_before_error=close_first)
                    with self.acquisition(reply) as (acquisition, requests), \
                            patch.object(runtime, "redirect_url", side_effect=validate):
                        with self.assertRaises(BaseException) as caught:
                            acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                        self.assertIs(caught.exception, observed[0])
                        self.assertEqual(runtime._oci_redirect_predicate(caught.exception), "HOSTNAME")
                        self.assertGreater(reply.close_calls, 0)
                        self.assertEqual(reply.stream.closed, close_first)
                        self.assertEqual(reply.read_calls, [])
                        self.assertEqual(len(requests), 1)
                        self.assert_private_journal(acquisition)

    def test_original_parser_or_control_error_survives_later_close_failure(self):
        for original in (ValueError("private-read"), runtime.Refused("ACQUISITION_DEADLINE"),
                         KeyboardInterrupt("private-read"), SystemExit(19)):
            with self.subTest(error=type(original).__name__):
                reply = Reply(307, locations=(DESTINATION,), close_error=OSError("private-close"))
                with self.acquisition(reply) as (acquisition, requests), \
                        patch.object(runtime, "redirect_url", side_effect=original):
                    with self.assertRaises(BaseException) as caught:
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertIs(caught.exception, original)
                    self.assertEqual(len(requests), 1)
                    self.assert_closed_without_body(reply)
                    self.assert_private_journal(acquisition)

    def test_close_failure_without_prior_refusal_prevents_the_follow(self):
        for original in (OSError("private-close"), runtime.Refused("ACQUISITION_DEADLINE"),
                         KeyboardInterrupt("private-close")):
            with self.subTest(error=type(original).__name__):
                reply = Reply(307, locations=(DESTINATION,), close_error=original,
                              close_before_error=False)
                with self.acquisition(reply) as (acquisition, requests):
                    with self.assertRaises(BaseException) as caught:
                        acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    self.assertIs(caught.exception, original)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(acquisition.record["redirects"], 0)
                    self.assertGreater(reply.close_calls, 0)
                    self.assertFalse(reply.stream.closed)
                    self.assert_private_journal(acquisition)

    def test_original_success_response_validation_error_survives_close_failure(self):
        original_require, observed = runtime.require, []

        def require(condition, code):
            try:
                return original_require(condition, code)
            except BaseException as error:
                observed.append(error)
                raise

        reply = Reply(200, headers=(("Content-Encoding", "gzip"),),
                      close_error=OSError("private-close"))
        with self.acquisition(reply) as (acquisition, requests), \
                patch.object(runtime, "require", side_effect=require):
            with self.assertRaises(BaseException) as caught:
                acquisition.open(ORIGIN, len(BODY), TOKEN, True)
            self.assertIs(caught.exception, observed[0])
            self.assertEqual(str(caught.exception), "HTTP_ENCODING")
            self.assertEqual(len(requests), 1)
            self.assertEqual(reply.read_calls, [])
            self.assertTrue(reply.stream.closed)
            self.assert_private_journal(acquisition)

    def test_receive_failure_keeps_original_identity_and_closes_the_owned_output(self):
        for original in (runtime.Refused("ACQUISITION_DEADLINE"), KeyboardInterrupt("private-read"),
                         SystemExit(19), OSError("private-read")):
            with self.subTest(error=type(original).__name__):
                reply = Reply(200, close_error=OSError("private-close"), close_before_error=False)
                destination = Destination(close_error=RuntimeError("private-close"))
                with self.acquisition(reply) as (acquisition, requests):
                    opened = acquisition.open(ORIGIN, len(BODY), TOKEN, True)
                    with patch.object(opened, "read", side_effect=original):
                        with self.assertRaises(BaseException) as caught:
                            acquisition.receive(opened, len(BODY), destination)
                    self.assertIs(caught.exception, original)
                    self.assertTrue(destination.closed)
                    self.assertGreater(reply.close_calls, 0)
                    self.assertFalse(reply.stream.closed)
                    self.assertEqual(len(requests), 1)
                    self.assert_private_journal(acquisition)

    def test_unrelated_caller_exception_cannot_hide_the_successful_body_close_failure(self):
        original = OSError("private-close")
        reply = Reply(200, close_error=original, close_before_error=False)
        with self.acquisition(reply) as (acquisition, requests):
            opened = acquisition.open(ORIGIN, len(BODY), TOKEN, True)
            try:
                raise RuntimeError("unrelated caller exception")
            except RuntimeError:
                with self.assertRaises(BaseException) as caught:
                    acquisition.receive(opened, len(BODY))
            self.assertIs(caught.exception, original)
            self.assertEqual(acquisition.record["body_bytes"], len(BODY))
            self.assertGreater(reply.close_calls, 0)
            # HTTPResponse itself closed its stream on the completed fixed body;
            # the later explicit close still failed and cannot become success.
            self.assertTrue(reply.stream.closed)
            self.assertEqual(len(requests), 1)
            self.assert_private_journal(acquisition)


if __name__ == "__main__":
    unittest.main()
