#!/usr/bin/env python3
"""One separately admitted registry-header observation, never native execution.

Plan checking is data-only. The active path owns one short-lived worker and
contacts only the fixed anonymous token endpoint and first pinned registry blob.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import MappingProxyType
import urllib.error
import urllib.parse
import urllib.request


REPO = Path(__file__).resolve().parents[2]
PLAN_RELATIVE = "tests/contracts/oci-destination/observation-plan-1.json"
PLAN_PATH = REPO / PLAN_RELATIVE
SCHEMA = "zpm-oci-destination-observation/v1"
OPERATION = "oci-destination-1"
CURRENT_FAILURE_EXIT = 20
RUN_PREFIX = "refs/heads/qualification/oci-destination/run/"
TOKEN_URL = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"
BLOB_DIGEST = "sha256:881fc932939efef04ca120ad44bbd2c0917851eb247aa4995428afb1e80ede17"
BLOB_URL = "https://registry-1.docker.io/v2/library/python/blobs/" + BLOB_DIGEST
BLOB_BYTES = 3853673
RECIPE_PATH = "tests/contracts/native-alpine/inputs-v1.json"
RECIPE_BYTES = 245074
RECIPE_SHA = "e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f"
IMPORT_FILES = {
    "tests": "tests/__init__.py",
    "tests.contracts": None,
    "tests.contracts.check_alpine_sealed_runtime": "tests/contracts/check_alpine_sealed_runtime.py",
    "tests.contracts.check_alpine_sealed_candidate": "tests/contracts/check_alpine_sealed_candidate.py",
    "tests.contracts.check_musl_preparation": "tests/contracts/check_musl_preparation.py",
    "tests.contracts.alpine_sealed_guest": "tests/contracts/alpine_sealed_guest.py",
    "zpm": "zpm/__init__.py",
    "zpm.dependencies": "zpm/dependencies.py",
    "zpm.installation_context": "zpm/installation_context.py",
    "zpm.installation_roles": "zpm/installation_roles.py",
    "zpm.models": "zpm/models.py",
}
CODE_PATHS = tuple(sorted({path for path in IMPORT_FILES.values() if path} | {
    "tests/contracts/check_oci_destination.py", ".github/workflows/oci-destination-observation.yml"}))
LIMITS = {"requests": 2, "request_seconds": 10, "worker_seconds": 25, "total_seconds": 30,
          "response_metadata_bytes": 65536, "token_body_bytes": 1048576,
          "prospective_body_bytes": 1048576 + BLOB_BYTES, "report_bytes": 4096}
FIXED_PLAN = {
    "schema": "zpm-oci-destination-plan/v1", "operation": OPERATION,
    "recipe": {"path": RECIPE_PATH, "bytes": RECIPE_BYTES, "sha256": RECIPE_SHA},
    "requests": [{"method": "GET", "url": TOKEN_URL}, {"method": "GET", "url": BLOB_URL}],
    "limits": LIMITS,
    "policy": {"tls": "system-default-verification", "proxies": False, "redirect_follows": 0,
               "retries": 0, "blob_body_reads": 0, "token_retention": "worker-memory-only",
               "native_execution": False, "source_imports": "verified-captured-source-only",
               "run_identity": "plan-sha256/source-revision; attempt-1-only"},
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
REDIRECTS = {301, 302, 303, 307, 308}
HTTP_CLASSES = {"UNAVAILABLE", "OTHER", "DIRECT_200", "HTTP_401", "HTTP_403", "HTTP_404", "HTTP_429"} | {
    "REDIRECT_" + str(code) for code in REDIRECTS}
PREDICATES = ("location", "text", "scheme", "userinfo", "fragment", "port", "hostname", "authority", "token_reflection")
COUNTERS = ("requests", "body_bytes", "metadata_reserved_bytes", "reserved_body_bytes",
            "response_header_bytes", "response_framing_bytes", "artifacts_complete", "redirects")
FAILURES = frozenset({
    "PLAN_UNAVAILABLE", "PLAN_BOUND", "PLAN_CHANGED", "PLAN_JSON", "PLAN_HASH", "PLAN_FIELDS", "PLAN_IDENTITY",
    "SOURCE_UNAVAILABLE", "SOURCE_CHANGED", "SOURCE_IDENTITY", "IMPORT_CLOSURE", "RECIPE_IDENTITY",
    "RUN_REFERENCE", "RUN_ATTEMPT", "RUN_SOURCE", "RUN_IDENTITY", "GIT_UNAVAILABLE", "WORK_REUSED", "WORK_UNSAFE",
    "REQUEST_DEADLINE", "OPERATION_DEADLINE", "INTERRUPTED", "REQUEST_COUNT", "REQUEST_SCOPE", "REGISTRY_TOKEN",
    "HTTP_HEADER_BOUND", "HTTP_HEADER_OBSERVATION", "HTTP_STATUS", "HTTP_TRANSPORT", "RESPONSE_CLOSE",
    "ARTIFACT_HTTP_REFUSED", "ARTIFACT_HTTP_STATUS", "HTTP_ENCODING", "HTTP_FRAMING", "HTTP_TOKEN_FRAMING",
    "HTTP_TOKEN_READER", "HTTP_METADATA_BOUND", "HTTP_CHUNK_SYNTAX", "HTTP_BODY_INCOMPLETE", "HTTP_CHUNK_TRAILER",
    "HTTP_CONTENT_LENGTH", "ACQUISITION_BYTES", "ACQUISITION_DEADLINE", "TOKEN_SCOPE",
    "LOCATION_CARDINALITY", "LOCATION_TEXT", "LOCATION_SCHEME", "LOCATION_USERINFO", "LOCATION_FRAGMENT",
    "LOCATION_PORT", "LOCATION_HOSTNAME", "LOCATION_AUTHORITY", "TOKEN_REFLECTION",
    "CHILD_RESULT", "CHILD_EXIT", "CHILD_CLEANUP", "REPORT_BOUND", "REPORT_WRITE", "UNEXPECTED_FAILURE",
})


class ObserverRefused(RuntimeError):
    def __init__(self, code, predicates=(), helper_cleanup=None):
        self.code = code if type(code) is str and code in FAILURES else "UNEXPECTED_FAILURE"
        self.predicates = tuple(predicates)
        self.helper_cleanup = helper_cleanup if helper_cleanup in ("CONFIRMED", "UNKNOWN") else None
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise ObserverRefused(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "PLAN_JSON")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ObserverRefused("PLAN_JSON")))
    except (ValueError, UnicodeError, RecursionError):
        raise ObserverRefused("PLAN_JSON") from None


def read_file(path, maximum, *, private=False):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= maximum, "PLAN_BOUND")
        if private:
            require(stat.S_IMODE(before.st_mode) == 0o600 and before.st_uid == os.getuid()
                    and before.st_gid == os.getgid() and before.st_nlink == 1, "WORK_UNSAFE")
        raw = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
                for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")), "PLAN_CHANGED")
        return raw
    except OSError:
        raise ObserverRefused("PLAN_UNAVAILABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@dataclass(frozen=True)
class Admission:
    repo: Path
    plan_sha256: str
    sources: object
    plan_bytes: bytes = b""
    recipe_bytes: bytes = b""


def load_plan(path=PLAN_PATH, reviewed_sha256=None, repo=REPO):
    """Only bounded local reads and hashes, before repository imports or effects."""
    require(type(reviewed_sha256) is str and HEX.fullmatch(reviewed_sha256), "PLAN_HASH")
    raw = read_file(path, 65536)
    require(hashlib.sha256(raw).hexdigest() == reviewed_sha256, "PLAN_HASH")
    plan = decode(raw)
    require(type(plan) is dict and set(plan) == set(FIXED_PLAN) | {"code"}, "PLAN_FIELDS")
    require(encoded({key: plan[key] for key in FIXED_PLAN}) == encoded(FIXED_PLAN), "PLAN_IDENTITY")
    require(type(plan["code"]) is dict and set(plan["code"]) == set(CODE_PATHS), "PLAN_FIELDS")
    repo = Path(repo).resolve()
    sources = {}
    for name in CODE_PATHS:
        row = plan["code"][name]
        require(type(row) is dict and set(row) == {"bytes", "sha256"}
                and type(row["bytes"]) is int and 0 <= row["bytes"] <= 262144
                and type(row["sha256"]) is str and HEX.fullmatch(row["sha256"]), "PLAN_FIELDS")
        target = repo / name
        require(all(not parent.is_symlink() for parent in (target, *target.parents) if parent != repo.parent),
                "SOURCE_UNAVAILABLE")
        content = read_file(target, row["bytes"])
        require(len(content) == row["bytes"] and hashlib.sha256(content).hexdigest() == row["sha256"], "SOURCE_IDENTITY")
        sources[name] = content
    recipe = read_file(repo / RECIPE_PATH, RECIPE_BYTES)
    require(len(recipe) == RECIPE_BYTES and hashlib.sha256(recipe).hexdigest() == RECIPE_SHA, "RECIPE_IDENTITY")
    first = decode(recipe)["oci"]["layers"][0]
    require(first["digest"] == BLOB_DIGEST and first["compressed_bytes"] == BLOB_BYTES, "RECIPE_IDENTITY")
    return Admission(repo, reviewed_sha256, MappingProxyType(sources), raw, recipe)


class _SourceLoader(importlib.abc.Loader):
    def __init__(self, path, raw, package=False):
        self.path, self.raw, self.package = path, raw, package

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = str(self.path)
        if self.package:
            module.__path__ = [str(self.path.parent)]
        exec(compile(self.raw, str(self.path), "exec", dont_inherit=True), module.__dict__)


class _SourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, admission):
        self.admission = admission

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in IMPORT_FILES:
            if fullname == "tests" or fullname.startswith("tests.") or fullname == "zpm" or fullname.startswith("zpm."):
                raise ObserverRefused("IMPORT_CLOSURE")
            return None
        name = IMPORT_FILES[fullname]
        if name is None:
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [str(self.admission.repo / "tests/contracts")]
            return spec
        package = name.endswith("/__init__.py")
        loader = _SourceLoader(self.admission.repo / name, self.admission.sources[name], package)
        return importlib.util.spec_from_loader(fullname, loader, is_package=package)


def load_runtime(admission):
    """Import captured, verified source; never read checkout bytecode or ambient packages."""
    selected = lambda name: name == "tests" or name.startswith("tests.") or name == "zpm" or name.startswith("zpm.")
    previous = {name: module for name, module in sys.modules.copy().items() if selected(name)}
    finder = _SourceFinder(admission)
    for name in previous:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    try:
        runtime = importlib.import_module("tests.contracts.check_alpine_sealed_runtime")
        require(set(name for name in sys.modules if selected(name)) <= set(IMPORT_FILES), "IMPORT_CLOSURE")
        for name, relative in IMPORT_FILES.items():
            if name in sys.modules and relative is not None:
                require(sys.modules[name].__file__ == str(admission.repo / relative), "IMPORT_CLOSURE")
        return runtime
    finally:
        sys.meta_path.remove(finder)
        for name in list(sys.modules):
            if selected(name):
                del sys.modules[name]
        sys.modules.update(previous)


def valid_hostname(value):
    if type(value) is not str or not 1 <= len(value) <= 253 or not value.isascii():
        return False
    labels = value.split(".")
    return (len(labels) >= 2 and not all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels)
            and all(DNS_LABEL.fullmatch(label) for label in labels))


def validate_location(raw, token):
    facts = {name: "NOT_CHECKED" for name in PREDICATES}
    facts["location"] = "PASS"
    def check(name, condition, code):
        facts[name] = "PASS" if condition else "FAIL"
        if not condition:
            raise ObserverRefused(code, facts.items())
    check("text", type(raw) is str and 0 < len(raw) <= 16384
          and all(33 <= ord(char) <= 126 for char in raw) and "\\" not in raw, "LOCATION_TEXT")
    check("scheme", raw.startswith("https://"), "LOCATION_SCHEME")
    check("fragment", "#" not in raw, "LOCATION_FRAGMENT")
    try:
        parts = urllib.parse.urlsplit(raw)
        check("scheme", parts.scheme == "https" and bool(parts.netloc), "LOCATION_SCHEME")
        check("userinfo", parts.username is None and parts.password is None and "@" not in parts.netloc, "LOCATION_USERINFO")
        check("port", parts.port in (None, 443), "LOCATION_PORT")
        hostname = parts.hostname
    except ObserverRefused:
        raise
    except (ValueError, UnicodeError):
        raise ObserverRefused("LOCATION_AUTHORITY", facts.items()) from None
    check("hostname", valid_hostname(hostname), "LOCATION_HOSTNAME")
    check("authority", parts.netloc in (hostname, hostname + ":443"), "LOCATION_AUTHORITY")
    check("token_reflection", type(token) is str and bool(token) and token.casefold() not in hostname.casefold(), "TOKEN_REFLECTION")
    return {"parsed_hostname": hostname, "predicates": facts,
            "current_hostname_match": hostname == "production.cloudfront.docker.com",
            "current_path_prefix_match": parts.path.startswith("/registry-v2/docker/registry/v2/blobs/sha256/")}


def blank_result():
    return {"schema": SCHEMA, "operation": OPERATION, "status": "FAILED", "first_failure": None,
            "http_status": "UNAVAILABLE", "parsed_hostname": None,
            "predicates": dict.fromkeys(PREDICATES, "NOT_CHECKED"),
            "current_hostname_match": None, "current_path_prefix_match": None,
            "counters": dict.fromkeys(COUNTERS), "counters_complete": False,
            "blob_body_consumed": None, "redirect_followed": None, "native_execution": "UNKNOWN",
            "response_cleanup": "UNKNOWN", "owned_child_cleanup": "UNKNOWN"}


def safe_result(value):
    """Revalidate the closed public projection, including after child transport."""
    result = blank_result()
    if type(value) is not dict:
        result["first_failure"] = "CHILD_RESULT"
        return result
    status = value.get("status")
    result["status"] = status if type(status) is str and status in {"OBSERVED", "INCONCLUSIVE", "REFUSED", "FAILED"} else "FAILED"
    failure = value.get("first_failure")
    result["first_failure"] = failure if failure is None or type(failure) is str and failure in FAILURES else "UNEXPECTED_FAILURE"
    http_status = value.get("http_status")
    result["http_status"] = http_status if type(http_status) is str and http_status in HTTP_CLASSES else "UNAVAILABLE"
    facts = value.get("predicates")
    if type(facts) is dict:
        for name in PREDICATES:
            if type(facts.get(name)) is str and facts[name] in {"PASS", "FAIL", "NOT_CHECKED"}:
                result["predicates"][name] = facts[name]
    hostname = value.get("parsed_hostname")
    if (valid_hostname(hostname) and result["http_status"] == "REDIRECT_307"
            and all(result["predicates"][name] == "PASS" for name in PREDICATES)):
        result["parsed_hostname"] = hostname
        result["current_hostname_match"] = hostname == "production.cloudfront.docker.com"
        if type(value.get("current_path_prefix_match")) is bool:
            result["current_path_prefix_match"] = value["current_path_prefix_match"]
    counters = value.get("counters")
    if type(counters) is dict:
        for name in COUNTERS:
            item = counters.get(name)
            if type(item) is int and 0 <= item <= 8 * 1024**2:
                result["counters"][name] = item
    result["counters_complete"] = value.get("counters_complete") is True and all(type(item) is int for item in result["counters"].values())
    for name in ("blob_body_consumed", "redirect_followed"):
        if value.get(name) is False:
            result[name] = False
    if value.get("native_execution") == "NOT_RUN":
        result["native_execution"] = "NOT_RUN"
    for name in ("response_cleanup", "owned_child_cleanup"):
        result[name] = "CONFIRMED" if value.get(name) == "CONFIRMED" else "UNKNOWN"
    counters = result["counters"]
    complete = (all(type(item) is int for item in counters.values())
        and counters["requests"] in (0, 1, 2)
        and counters["metadata_reserved_bytes"] == counters["requests"] * 65536
        and counters["reserved_body_bytes"] == (0, 1048576, 1048576 + BLOB_BYTES)[counters["requests"]]
        and counters["body_bytes"] <= 1048577
        and counters["response_header_bytes"] + counters["response_framing_bytes"] <= counters["metadata_reserved_bytes"] + 1
        and counters["artifacts_complete"] == 0 and counters["redirects"] == 0)
    successful = result["status"] in {"OBSERVED", "INCONCLUSIVE"}
    contradictory = (value.get("blob_body_consumed") not in (None, False)
        or value.get("redirect_followed") not in (None, False)
        or value.get("native_execution") not in (None, "UNKNOWN", "NOT_RUN")
        or value.get("counters_complete") is True and not complete
        or result["status"] == "OBSERVED" and result["parsed_hostname"] is None
        or result["status"] == "INCONCLUSIVE" and (result["http_status"] in {"UNAVAILABLE", "REDIRECT_307"} or hostname is not None)
        or successful and (result["first_failure"] is not None or not result["counters_complete"]
            or counters["requests"] != 2 or counters["body_bytes"] == 0
            or result["response_cleanup"] != "CONFIRMED" or result["blob_body_consumed"] is not False
            or result["redirect_followed"] is not False or result["native_execution"] != "NOT_RUN"))
    if contradictory:
        result["status"], result["first_failure"] = "FAILED", result["first_failure"] or "CHILD_RESULT"
        result["parsed_hostname"] = None
        result["current_hostname_match"] = result["current_path_prefix_match"] = None
        result["counters_complete"] = False
    if result["status"] in {"FAILED", "REFUSED"} and result["first_failure"] is None:
        result["first_failure"] = "CHILD_RESULT"
    return result


def failure_code(error, runtime=None):
    if isinstance(error, ObserverRefused):
        return error.code
    if runtime is not None and isinstance(error, runtime.HeaderBound):
        return "HTTP_HEADER_BOUND"
    if runtime is not None and type(error) is runtime.Refused:
        value = str(error)
        return value if value in FAILURES else "HTTP_TRANSPORT"
    return "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "HTTP_TRANSPORT"


class _ClosingOpener:
    """Keep the real response type while making cleanup preserve an earlier failure."""
    def __init__(self, opener, runtime):
        self.opener, self.runtime, self.responses = opener, runtime, []

    def _own(self, response):
        state = {"attempted": False, "closed": False}
        original_close = response.close
        self.responses.append((response, state))
        def close():
            if state["attempted"]:
                return
            state["attempted"] = True
            original_error = sys.exc_info()[1]
            try:
                original_close()
                state["closed"] = True
            except BaseException as error:
                if original_error is None:
                    if isinstance(error, (ObserverRefused, self.runtime.Refused)) or not isinstance(error, Exception):
                        raise
                    raise ObserverRefused("RESPONSE_CLOSE") from None
        response.close = close
        return response

    def open(self, request, timeout):
        try:
            return self._own(self.opener.open(request, timeout=timeout))
        except urllib.error.HTTPError as error:
            self._own(error)
            raise

    def close_all(self):
        original_error = sys.exc_info()[1]
        failure = None
        for response, state in self.responses:
            if not state["attempted"]:
                try:
                    response.close()
                except BaseException as error:
                    failure = failure or error
        if original_error is None and failure is not None:
            raise failure

    def confirmed(self):
        return bool(self.responses) and all(state["closed"] for _, state in self.responses)


def observe(admission, work, deadline, *, opener=None, runtime=None):
    """Run only in the owned worker. Test openers never change active CLI inputs."""
    runtime = runtime or load_runtime(admission)
    directory = Path(work) / "private"
    directory.mkdir(mode=0o700)
    raw_opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), runtime.NoRedirect(), runtime.BoundedHTTPSHandler())
    owned = _ClosingOpener(raw_opener, runtime)
    class Transport(runtime.Acquisition):
        def persist(self):
            original = sys.exc_info()[1]
            try:
                super().persist()
            except BaseException:
                if original is not None:
                    raise original
                raise

        def reserve(self, url, maximum):
            index = self.record["requests"]
            require(index < 2, "REQUEST_COUNT")
            expected = [(TOKEN_URL, 1048576), (BLOB_URL, BLOB_BYTES)][index]
            require((url, maximum) == expected, "REQUEST_SCOPE")
            super().reserve(url, maximum)
    transport = Transport(directory, deadline, opener=owned)
    result = blank_result()
    result.update(blob_body_consumed=False, redirect_followed=False, native_execution="NOT_RUN")
    token = None
    try:
        with runtime.absolute_timeout(min(deadline, time.monotonic() + 10), "REQUEST_DEADLINE"):
            _, _, raw = transport.receive(transport.open(TOKEN_URL, 1048576), 1048576)
            value = runtime.candidate.decode(raw)
            token = value.get("token") if type(value) is dict else None
            require(type(token) is str and 1 <= len(token) <= 16384
                    and re.fullmatch(r"[A-Za-z0-9._~-]+", token), "REGISTRY_TOKEN")
        with runtime.absolute_timeout(min(deadline, time.monotonic() + 10), "REQUEST_DEADLINE"):
            transport.reserve(BLOB_URL, BLOB_BYTES)
            headers = {"Accept-Encoding": "identity", "User-Agent": "zpm-native-alpine-qualification/1",
                       "Authorization": "Bearer " + token}
            response = None
            try:
                try:
                    response = owned.open(urllib.request.Request(BLOB_URL, headers=headers),
                                          timeout=min(10, max(.001, deadline - time.monotonic())))
                except runtime.HeaderBound as error:
                    transport.record["response_header_bytes"] += error.observed
                    transport.persist()
                    raise
                except urllib.error.HTTPError as error:
                    response = error
                transport.header_observation(response.fp if isinstance(response, urllib.error.HTTPError) else response)
                status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
                require(type(status) is int and 100 <= status <= 599, "HTTP_STATUS")
                result["http_status"] = ("REDIRECT_" + str(status) if status in REDIRECTS else
                    "DIRECT_200" if status == 200 else "HTTP_" + str(status) if status in {401, 403, 404, 429} else "OTHER")
                locations = response.headers.get_all("Location", []) if status == 307 else []
            finally:
                if response is not None:
                    response.close()
            if status == 307:
                result["predicates"]["location"] = "PASS" if len(locations) == 1 else "FAIL"
                require(len(locations) == 1, "LOCATION_CARDINALITY")
                result.update(validate_location(locations[0], token))
                result["status"] = "OBSERVED"
            else:
                result["status"] = "INCONCLUSIVE"
        result["counters_complete"] = True
    except BaseException as error:
        result["first_failure"] = failure_code(error, runtime)
        result["status"] = "REFUSED" if isinstance(error, (ObserverRefused, runtime.Refused)) else "FAILED"
        if isinstance(error, ObserverRefused):
            result["predicates"].update(dict(error.predicates))
    finally:
        try:
            owned.close_all()
        except BaseException as error:
            result["first_failure"] = result["first_failure"] or failure_code(error, runtime)
            result["status"] = "FAILED"
        result["response_cleanup"] = "CONFIRMED" if owned.confirmed() else "UNKNOWN"
        result["counters"] = {name: transport.record[name] for name in COUNTERS}
    if result["response_cleanup"] != "CONFIRMED" and result["first_failure"] is None:
        result["status"], result["first_failure"] = "FAILED", "RESPONSE_CLOSE"
    return safe_result(result)


def write_private(path, value, *, deadline=None):
    if deadline is not None and time.monotonic() >= deadline:
        value["first_failure"] = value.get("first_failure") or "OPERATION_DEADLINE"
        value["status"] = "FAILED"
    raw = encoded(value)
    require(len(raw) <= LIMITS["report_bytes"], "REPORT_BOUND")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            if deadline is not None and time.monotonic() >= deadline and value.get("status") in {"OBSERVED", "INCONCLUSIVE"}:
                value["first_failure"] = value.get("first_failure") or "OPERATION_DEADLINE"
                value["status"] = "FAILED"
                raw = encoded(value)
                require(len(raw) <= LIMITS["report_bytes"], "REPORT_BOUND")
                stream.seek(0)
                stream.truncate()
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
    except OSError:
        raise ObserverRefused("REPORT_WRITE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _new_work(path):
    path = Path(path)
    require(path.is_absolute() and path.parent.is_dir() and not path.parent.is_symlink(), "WORK_UNSAFE")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        raise ObserverRefused("WORK_REUSED") from None
    except OSError:
        raise ObserverRefused("WORK_UNSAFE") from None
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.getuid() and info.st_gid == os.getgid(), "WORK_UNSAFE")
    return path


def _signal_child(pid, signum):
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


@contextmanager
def _control_atomic():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield previous
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def supervise(worker, work, *, clock=time.monotonic, total_seconds=30, worker_seconds=25, started_at=None):
    """One forked worker; shortened direct values exist only for local fixtures."""
    require(0 < worker_seconds < total_seconds <= 30 and worker_seconds <= 25, "RUN_IDENTITY")
    started = clock() if started_at is None else started_at
    worker_end, total_end = started + worker_seconds, started + total_seconds
    require(clock() < worker_end, "OPERATION_DEADLINE")
    work = _new_work(work)
    result, pid, waited, wait_status = blank_result(), None, False, None
    interrupted_pending = False
    gate_read = gate_write = None
    old_handlers = {}
    def interrupted(signum, frame):
        nonlocal interrupted_pending
        interrupted_pending = True
    def reap():
        nonlocal waited, wait_status
        with _control_atomic():
            found, status = os.waitpid(pid, os.WNOHANG)
            if found == pid:
                waited, wait_status = True, status
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, interrupted)
        gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
        with _control_atomic() as prior_mask:
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(gate_write)
                    os.setsid()
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        signal.signal(signum, signal.SIG_DFL)
                    signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
                    os.environ.clear()
                    os.environ.update(PATH="/usr/bin:/bin", LANG="C.UTF-8")
                    null = os.open(os.devnull, os.O_RDWR)
                    for descriptor in (0, 1, 2):
                        os.dup2(null, descriptor)
                    if null > 2:
                        os.close(null)
                    permitted = os.read(gate_read, 1) == b"1"
                    os.close(gate_read)
                    if not permitted:
                        os._exit(1)
                    value = safe_result(worker(work, worker_end))
                    write_private(work / "worker-result.json", value)
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            os.close(gate_read)
            gate_read = None
            if signal.sigpending() & {signal.SIGINT, signal.SIGTERM}:
                interrupted_pending = True
            if not interrupted_pending and clock() < worker_end:
                os.write(gate_write, b"1")
            os.close(gate_write)
            gate_write = None
        while clock() < worker_end:
            if interrupted_pending:
                result["first_failure"] = "INTERRUPTED"
                break
            reap()
            if waited:
                break
            time.sleep(min(.01, max(0, worker_end - clock())))
        if not waited:
            result["first_failure"] = result["first_failure"] or ("INTERRUPTED" if interrupted_pending else "OPERATION_DEADLINE")
    except BaseException as error:
        result["first_failure"] = result["first_failure"] or failure_code(error)
    finally:
        for descriptor in (gate_read, gate_write):
            if descriptor is not None:
                os.close(descriptor)
        if pid is not None and pid > 0 and not waited:
            try:
                _signal_child(pid, signal.SIGTERM)
                term_end = min(total_end, clock() + min(1, (total_end - worker_end) / 2))
                while clock() < term_end:
                    reap()
                    if waited:
                        break
                    time.sleep(min(.01, max(0, term_end - clock())))
                if not waited:
                    _signal_child(pid, signal.SIGKILL)
                while not waited and clock() < total_end:
                    reap()
                    if waited:
                        break
                    time.sleep(min(.01, max(0, total_end - clock())))
            except BaseException:
                result["first_failure"] = result["first_failure"] or "CHILD_CLEANUP"
        for signum, previous in old_handlers.items():
            signal.signal(signum, previous)
    if interrupted_pending:
        result["first_failure"] = result["first_failure"] or "INTERRUPTED"
    # A late result cannot erase the parent's original timeout/interruption.
    if waited and result["first_failure"] is None:
        try:
            require(os.waitstatus_to_exitcode(wait_status) == 0, "CHILD_EXIT")
            raw = decode(read_file(work / "worker-result.json", LIMITS["report_bytes"], private=True))
            projected = safe_result(raw)
            require(type(raw) is dict and raw == projected and raw.get("owned_child_cleanup") == "UNKNOWN", "CHILD_RESULT")
            result = projected
        except BaseException as error:
            result["first_failure"] = failure_code(error)
    if result["first_failure"] is not None and not result["counters_complete"]:
        try:
            ledger = decode(read_file(work / "acquisition.json", 65536, private=True))
            if type(ledger) is dict:
                result["counters"] = safe_result({"counters": ledger})["counters"]
        except BaseException:
            pass
    result["owned_child_cleanup"] = "CONFIRMED" if waited else "UNKNOWN"
    if not waited:
        result["first_failure"] = result["first_failure"] or "CHILD_CLEANUP"
    if clock() >= total_end:
        result["first_failure"] = result["first_failure"] or "OPERATION_DEADLINE"
    if result["first_failure"] is not None:
        result["status"] = "FAILED" if result["status"] in {"OBSERVED", "INCONCLUSIVE"} else result["status"]
    return safe_result(result)


def _git_output(repo, arguments, maximum, deadline, *, cleanup_deadline=None):
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_NO_LAZY_FETCH": "1"}
    process = None
    original = None
    result = None
    cleanup_end = deadline if cleanup_deadline is None else cleanup_deadline
    cleanup_known = True
    interrupted_pending = False
    previous_handlers = {}
    command_end = deadline - 1
    require(time.monotonic() < command_end, "GIT_UNAVAILABLE")
    def interrupted(signum, frame):
        nonlocal interrupted_pending
        interrupted_pending = True
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        with _control_atomic():
            process = subprocess.Popen(["/usr/bin/git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.allow=never", "-C", str(repo), *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, start_new_session=True)
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < command_end, "GIT_UNAVAILABLE")
                require(not interrupted_pending, "INTERRUPTED")
                for key, _ in selector.select(min(.05, max(0, command_end - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), maximum + 1 - len(output))
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(data)
                        require(len(output) <= maximum, "GIT_UNAVAILABLE")
            require(process.wait(timeout=max(.001, command_end - time.monotonic())) == 0, "GIT_UNAVAILABLE")
        result = bytes(output)
    except BaseException as error:
        original = error if isinstance(error, ObserverRefused) or not isinstance(error, Exception) else ObserverRefused("GIT_UNAVAILABLE")
    finally:
        if process is not None:
            try:
                with _control_atomic():
                    running = process.poll() is None
                if running:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=max(.001, cleanup_end - time.monotonic()))
            except BaseException:
                cleanup_known = False
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except BaseException:
                    cleanup_known = False
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
    if interrupted_pending and original is None:
        original = ObserverRefused("INTERRUPTED")
    if not cleanup_known:
        if original is None:
            original = ObserverRefused("GIT_UNAVAILABLE")
        if isinstance(original, ObserverRefused):
            original.helper_cleanup = "UNKNOWN"
        else:
            original = ObserverRefused(failure_code(original), helper_cleanup="UNKNOWN")
    if original is not None:
        raise original
    return result


def checkout_revision(repo, deadline, *, cleanup_deadline=None):
    raw = _git_output(repo, ("rev-parse", "--verify", "HEAD"), 128, deadline,
                      cleanup_deadline=cleanup_deadline)
    require(re.fullmatch(rb"[0-9a-f]{40}\n", raw) is not None, "GIT_UNAVAILABLE")
    return raw[:-1].decode("ascii")


def verify_tracked_inputs(admission, source_revision, deadline, *, cleanup_deadline=None):
    """The captured inputs must be regular blobs in the exact admitted commit."""
    expected = dict(admission.sources)
    require(set(expected) == set(CODE_PATHS) and type(admission.plan_bytes) is bytes
            and type(admission.recipe_bytes) is bytes and admission.plan_bytes and admission.recipe_bytes,
            "SOURCE_IDENTITY")
    expected[PLAN_RELATIVE] = admission.plan_bytes
    expected[RECIPE_PATH] = admission.recipe_bytes
    raw = _git_output(admission.repo, ("ls-tree", "-z", "--full-tree", source_revision, "--", *sorted(expected)),
                      16384, deadline, cleanup_deadline=cleanup_deadline)
    require(raw.endswith(b"\0"), "SOURCE_IDENTITY")
    found = {}
    for row in raw[:-1].split(b"\0"):
        match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})\t([^\0]+)", row)
        require(match is not None, "SOURCE_IDENTITY")
        try:
            name = match[3].decode("ascii")
        except UnicodeError:
            raise ObserverRefused("SOURCE_IDENTITY") from None
        require(name in expected and name not in found, "SOURCE_IDENTITY")
        content = expected[name]
        require(type(content) is bytes, "SOURCE_IDENTITY")
        blob = hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()
        require(match[2].decode("ascii") == blob, "SOURCE_IDENTITY")
        found[name] = blob
    require(set(found) == set(expected), "SOURCE_IDENTITY")


def admit_run(admission, run_ref, source_revision, run_attempt, run_id, *, deadline=None, cleanup_deadline=None):
    match = re.fullmatch(re.escape(RUN_PREFIX) + r"([0-9a-f]{64})/([0-9a-f]{40})", run_ref or "")
    require(match is not None and match[1] == admission.plan_sha256, "RUN_REFERENCE")
    require(run_attempt == "1", "RUN_ATTEMPT")
    require(type(source_revision) is str and COMMIT.fullmatch(source_revision) and match[2] == source_revision, "RUN_SOURCE")
    require(type(run_id) is str and re.fullmatch(r"[1-9][0-9]{0,19}", run_id), "RUN_IDENTITY")
    work_deadline = time.monotonic() + 5 if deadline is None else deadline
    require(checkout_revision(admission.repo, work_deadline, cleanup_deadline=cleanup_deadline) == source_revision, "RUN_SOURCE")
    verify_tracked_inputs(admission, source_revision, work_deadline, cleanup_deadline=cleanup_deadline)
    return {"plan_sha256": admission.plan_sha256, "source_revision": source_revision,
            "run_id": run_id, "run_attempt": 1, "run_ref": run_ref,
            "recipe_sha256": RECIPE_SHA, "registry_blob_digest": BLOB_DIGEST}


def mark_current_summary(path, result, *, deadline=None):
    """Validate this report before the command returns an upload-eligible status."""
    require(read_file(path, LIMITS["report_bytes"], private=True) == encoded(result), "REPORT_WRITE")
    require(deadline is None or time.monotonic() < deadline, "OPERATION_DEADLINE")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--reviewed-plan-sha256", required=True)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args(argv)
    started = time.monotonic()
    identity = result = None
    try:
        admission = load_plan(args.plan, args.reviewed_plan_sha256)
        if args.check_plan:
            print(encoded({"schema": SCHEMA, "operation": OPERATION, "status": "SOURCE_PLAN_ADMITTED",
                           "plan_sha256": admission.plan_sha256, "observation": "NOT_RUN"}).decode(), end="")
            return 0
        require(args.work is not None, "WORK_UNSAFE")
        identity = admit_run(admission, os.environ.get("GITHUB_REF"), os.environ.get("GITHUB_SHA"),
                            os.environ.get("GITHUB_RUN_ATTEMPT"), os.environ.get("GITHUB_RUN_ID"),
                            deadline=min(started + 25, time.monotonic() + 5), cleanup_deadline=started + 30)
        result = supervise(lambda work, deadline: observe(admission, work, deadline), args.work, started_at=started)
        result.update(identity)
        result["elapsed_ms"] = max(0, round((time.monotonic() - started) * 1000))
        report_current = False
        try:
            write_private(args.work / "summary.json", result, deadline=started + 30)
            mark_current_summary(args.work / "summary.json", result, deadline=started + 30)
            report_current = True
        except BaseException as error:
            result["first_failure"] = result["first_failure"] or failure_code(error)
            result["status"] = "FAILED"
            result["report_failure"] = "REPORT_WRITE"
        print(encoded(result).decode(), end="")
        if not report_current:
            return 2
        return 0 if result["status"] in {"OBSERVED", "INCONCLUSIVE"} and result["owned_child_cleanup"] == "CONFIRMED" else CURRENT_FAILURE_EXIT
    except BaseException as error:
        if identity is not None:
            result = safe_result(result) if result is not None else blank_result()
            result["first_failure"] = result["first_failure"] or failure_code(error)
            result["status"] = "FAILED"
            result.update(identity)
        else:
            # Pre-admission failures never echo supplied arguments or URLs.
            result = {"schema": SCHEMA, "operation": OPERATION, "status": "REFUSED",
                      "first_failure": failure_code(error), "observation": "NOT_QUALIFIED"}
        if isinstance(error, ObserverRefused) and error.helper_cleanup is not None:
            result["helper_cleanup"] = error.helper_cleanup
        print(encoded(result).decode(), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
