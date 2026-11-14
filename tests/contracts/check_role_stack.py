"""Generated controller-authority and execution bundles on one native host.

Only the installed operator commands prepare, qualify and control these roles.
The two TLS forwarders inject one lost reply and one admission race. Read-only
journals, /proc and Docker inspection are explicitly harness observations, not
new control policy. Private bundles and raw command output are never exported.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit

from check_stack import bootstrap_command, clean_environment, port, private_json
from check_isolation_stack import require_live_container


TOTAL_SECONDS = 900
CLEANUP_SECONDS = 60
JSON_LIMIT = 131072
PIPE_LIMIT = 1_048_576
HTTP_LIMIT = 1_048_576
REPORT_LIMIT = 65536
ORDINARY_HTTP_SECONDS = 3
RACE_CLOSE_SECONDS = 15
RACE_REQUEST_SECONDS = 20
RACE_CASE_SECONDS = 45
REQUIREMENTS = "Set the controlled answer to42 once after the role barrier."
TASK_PROGRAM = """from pathlib import Path
import os, sys, time
assert os.getuid() == 65532
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/openjules-model').exists()
assert not any(name.startswith('ZPM_INSTALL_') for name in os.environ)
assert not any(name in os.environ for name in ('GITHUB_TOKEN', 'POLYPHONY_RUNTIME_TOKEN', 'OPENJULES_WORKER_TOKEN'))
assert sys.stdin.read() == 'Set the controlled answer to42 once after the role barrier.'
assert Path('answer.py').read_text() == 'answer = 0\\n'
with Path('.role-start').open('x') as stream:
    stream.write('one protected invocation\\n')
deadline = time.monotonic() + 30
while not Path('.role-release').exists():
    assert time.monotonic() < deadline, 'role barrier deadline'
    time.sleep(0.05)
Path('answer.py').write_text('answer = 42\\n')
assert Path('.role-start').read_text() == 'one protected invocation\\n'
print('ROLE_INSTALLED_CAPTURED', flush=True)
"""


class RoleCheck(AssertionError):
    """Only fixed harness assertions are eligible for structured diagnostics."""
    def __init__(self, message, *, command=None, returncode=None):
        super().__init__(message)
        self.check = message
        self.command = command
        self.returncode = returncode


def require(value, message):
    if not value:
        raise RoleCheck(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_private(path, maximum=HTTP_LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= maximum,
                "role evidence is not a bounded owned private file")
        raw = source.read(maximum + 1)
    require(len(raw) == info.st_size and len(raw) <= maximum, "role evidence changed during its read")
    return raw


def proc_identity(pid):
    """Linux oracle only. A different start time means the original exited."""
    path = Path("/proc") / str(pid)
    try:
        raw = (path / "stat").read_bytes()
        require(len(raw) <= 8192, "owned process stat exceeded its bound")
        fields = raw[raw.rindex(b")") + 2:].split()
        command = (path / "cmdline").read_bytes()
        require(len(command) <= 32768, "owned process argv exceeded its bound")
        return {"pid": pid, "start_ticks": int(fields[19]), "ppid": int(fields[1]),
                "pgid": int(fields[2]), "session": int(fields[3]), "state": fields[0].decode(),
                "uid": path.stat().st_uid,
                "argv": tuple(part.decode() for part in command.rstrip(b"\0").split(b"\0"))}
    except FileNotFoundError:
        return None


def original_live(identity):
    current = proc_identity(identity["pid"])
    return current is not None and current["start_ticks"] == identity["start_ticks"]


class RoleTrialFailure(AssertionError):
    def __init__(self, phase, checks, error, cleanup):
        super().__init__("role installation trial failed during " + phase)
        # Closed, credential-free fields only. Never include str(error), argv,
        # nested native stderr, private file paths or any credential catalog.
        self.role_diagnostic = {"schema_version": 1, "passed": False, "phase": phase,
            "completed_checks": list(checks), "error_type": type(error).__name__,
            "cleanup": cleanup, "private_recovery_state_retained": True}
        if isinstance(error, RoleCheck):
            self.role_diagnostic["check"] = error.check
            if error.command is not None:
                self.role_diagnostic["command"] = error.command
            if error.returncode is not None:
                self.role_diagnostic["returncode"] = error.returncode


class Trial:
    """One absolute work/cleanup budget shared by every owned operation."""

    def __init__(self, root):
        self.root = root
        self.started = time.monotonic()
        self.deadline = self.started + TOTAL_SECONDS
        self.work_deadline = self.deadline - CLEANUP_SECONDS
        self.case_deadline = self.work_deadline
        self.cleanup_deadline = self.deadline
        self.managers = {}
        self.history = []
        self.command_unknown = False
        self.command_number = 0
        self.command_lock = threading.Lock()
        self.frontends = []
        self.ensure_calls = 0
        self.ensure_lock = threading.Lock()

    def limit(self, seconds, *, cleanup=False):
        remaining = (self.cleanup_deadline if cleanup else min(self.work_deadline, self.case_deadline)) - time.monotonic()
        require(remaining > 0, "role trial exhausted its absolute time budget")
        return min(seconds, remaining)

    def command(self, argv, cwd, environment, *, seconds=45, allowed=(0,), parse=True, cleanup=False, label=None):
        # Bound while reading, including pip output, rather than truncating an
        # unbounded communicate() result after the command has finished.
        end = time.monotonic() + self.limit(seconds, cleanup=cleanup)
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        buffers = (bytearray(), bytearray())
        limit = JSON_LIMIT if parse else PIPE_LIMIT
        completed = False
        try:
            with selectors.DefaultSelector() as selector:
                for stream, buffer in zip((process.stdout, process.stderr), buffers):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, buffer)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    require(remaining > 0, "installed role command exceeded its finite deadline")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        raw = os.read(key.fd, min(65536, limit - len(key.data) + 1))
                        if raw:
                            key.data.extend(raw)
                            require(len(key.data) <= limit, "installed role command exceeded its pipe bound")
                        else:
                            selector.unregister(key.fileobj)
                remaining = end - time.monotonic()
                require(remaining > 0, "installed role command completion exceeded its deadline")
                code = process.wait(timeout=remaining)
                require(time.monotonic() <= end, "installed role command reap exceeded its deadline")
            completed = True
            if code not in allowed:
                raise RoleCheck("installed role command returned an unexpected status", command=label, returncode=code)
            if parse:
                try:
                    value = json.loads(buffers[0])
                except (ValueError, UnicodeError):
                    raise RoleCheck("installed role command returned invalid JSON", command=label, returncode=code) from None
                require(type(value) is dict, "installed role command returned an unsupported result")
                return value
            return bytes(buffers[0])
        finally:
            if not completed:
                # Native command helpers may own separate sessions. Reaping
                # this immediate process cannot prove all effects completed.
                self.command_unknown = True
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                remaining = min(1, max(0, self.deadline - time.monotonic()))
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=min(1, max(0, self.deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
            process.stdout.close()
            process.stderr.close()
            with self.command_lock:
                self.command_number += 1
                number = self.command_number
            # These bounded raw files remain under the private companion root.
            path = self.root / ("command-" + str(number) + ".log")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(buffers[0] + b"\n[stderr]\n" + buffers[1])

    def wait(self, predicate, description, seconds=30):
        end = time.monotonic() + self.limit(seconds)
        previous, self.case_deadline = self.case_deadline, end
        try:
            while time.monotonic() < end:
                self.check()
                try:
                    result = predicate()
                except (ConnectionError, OSError, http.client.HTTPException):
                    result = None
                if result:
                    require(time.monotonic() <= end, "role observation crossed its case deadline")
                    return result
                time.sleep(min(0.1, max(0, end - time.monotonic())))
            raise AssertionError("deadline waiting for " + description)
        finally:
            self.case_deadline = previous

    def check(self):
        self.limit(1)
        for record in self.managers.values():
            require(record["process"].poll() is None, "owned role manager exited unexpectedly")
            require(not record["overflow"], "owned role service exceeded its log bound")
            require(all(original_live(row) for row in record["services"]), "owned role service exited unexpectedly")
        for frontend in self.frontends:
            require(not frontend.errors, "role TLS forwarder encountered a bounded harness failure")

    def start(self, label, bundle, environment, services, *, authority_only=False):
        require(label not in self.managers, "role manager already has an owner")
        self.limit(1)
        command = [sys.executable, str(bundle / "manage.py"), "start"] + (["--authority-only"] if authority_only else [])
        record = {"process": None, "identity": None, "services": [], "services_observed": False,
                  "output": bytearray(), "overflow": False, "reader_stop": threading.Event(), "reader": None}
        process = subprocess.Popen(command, cwd=bundle, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        # The returned immediate child belongs to this Popen object even when
        # the next /proc observation fails. Record ownership before that read.
        record["process"] = process
        self.managers[label] = record
        self.history.append(record)
        record["identity"] = proc_identity(process.pid)
        require(record["identity"] is not None, "new role manager identity was unavailable")

        def read_output():
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                while not record["reader_stop"].is_set():
                    for key, _ in selector.select(0.1):
                        raw = os.read(key.fd, 65536)
                        if not raw:
                            return
                        if len(record["output"]) + len(raw) > PIPE_LIMIT:
                            record["overflow"] = True
                        else:
                            record["output"].extend(raw)
        reader = threading.Thread(target=read_output, name="role-log-" + label, daemon=True)
        record["reader"] = reader
        reader.start()

        def observed():
            require(process.poll() is None, "role manager exited before its services were observed")
            path = Path("/proc") / str(process.pid) / "task" / str(process.pid) / "children"
            raw = path.read_text()
            require(len(raw) <= 4096, "role manager children exceeded the ownership bound")
            children = [proc_identity(int(pid)) for pid in raw.split()]
            found = []
            for command in services:
                matches = [row for row in children if row is not None and row["argv"] == tuple(command)]
                if len(matches) != 1:
                    for row in found:
                        if row not in record["services"]:
                            record["services"].append(row)
                    return None
                row = matches[0]
                require(row["ppid"] == process.pid and row["uid"] == os.getuid()
                        and row["pgid"] == row["session"] == row["pid"],
                        "role service is not the exact owned separate-session child")
                found.append(row)
            require(all(row in found for row in record["services"]), "role service changed during startup ownership observation")
            record["services"] = found
            record["services_observed"] = True
            return found
        self.wait(observed, "exact installed role service ownership", 30)

    def stop(self, label):
        """Normal manager TERM, with actual owned service exit before restart."""
        record = self.managers[label]
        end = time.monotonic() + self.limit(45)
        process = record["process"]
        require(process.poll() is None and original_live(record["identity"]), "manager stop ownership was lost")
        os.kill(process.pid, signal.SIGTERM)
        while time.monotonic() < end:
            code = process.poll()
            if code is not None and all(not original_live(row) for row in record["services"]):
                require(code == 130, "role manager did not complete its normal shutdown path")
                del self.managers[label]
                return {"manager_returncode": code, "owned_services_exited": True,
                        "service_count": len(record["services"]), "method": "normal manager TERM"}
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        raise AssertionError("role manager shutdown did not prove all owned services exited")

    def cleanup(self):
        # Signal every manager before waiting; serial 15/20 second service
        # shutdowns share this one remaining cleanup deadline.
        end = self.cleanup_deadline
        uncertain = self.command_unknown
        for record in self.history:
            try:
                process = record["process"]
                uncertain |= record["identity"] is None or not record["services_observed"]
                if process.poll() is None:
                    if record["identity"] is None:
                        # This is the still-unreaped child returned by our own
                        # Popen, not a rediscovered PID or an inferred group.
                        process.terminate()
                    elif original_live(record["identity"]):
                        try:
                            os.kill(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
            except BaseException:
                uncertain = True
        for frontend in self.frontends:
            try:
                frontend.begin_stop()
            except BaseException:
                uncertain = True
        def services_exited():
            nonlocal uncertain
            result = True
            for record in self.history:
                for row in record["services"]:
                    try:
                        result &= not original_live(row)
                    except BaseException:
                        uncertain, result = True, False
            return result
        while time.monotonic() < end:
            if all(record["process"].poll() is not None for record in self.history) and services_exited():
                break
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        managers_reaped = all(record["process"].poll() is not None for record in self.history)
        children_exited = services_exited()
        for record in self.history:
            try:
                record["reader_stop"].set()
                if record["reader"] is not None:
                    record["reader"].join(timeout=max(0, end - time.monotonic()))
                    uncertain |= record["reader"].is_alive()
                if record["reader"] is None or not record["reader"].is_alive():
                    record["process"].stdout.close()
            except BaseException:
                uncertain = True
        stopped = []
        for frontend in self.frontends:
            try:
                stopped.append(frontend.finish_stop(end))
            except BaseException:
                stopped.append(False)
        frontends_stopped = all(stopped)
        return {"managers_reaped": managers_reaped, "recorded_services_exited": children_exited,
                "frontends_stopped": frontends_stopped,
                "outcome": "observed" if managers_reaped and children_exited and frontends_stopped and not uncertain else "unknown"}


def exchange(endpoint, method, path, *, token, budget, query=None, data=None, raw_body=None, expected=200, text=False, ca=None):
    """Fixed loopback transport with a total time and body byte bound."""
    parsed = urlsplit(endpoint)
    require(parsed.hostname == "127.0.0.1" and parsed.path == "" and parsed.scheme in {"http", "https"},
            "role trial endpoint is not a declared loopback root")
    end = time.monotonic() + budget.limit(3)
    arguments = {"timeout": max(0.001, end - time.monotonic())}
    if parsed.scheme == "https":
        require(ca is not None, "role TLS requires its declared test CA")
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port,
            context=ssl.create_default_context(cafile=str(ca)), **arguments)
    else:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, **arguments)
    require(data is None or raw_body is None, "role HTTP request supplied conflicting bodies")
    require(raw_body is None or type(raw_body) is bytes, "role captured replay body is not exact bytes")
    body = raw_body if raw_body is not None else (canonical(data).encode() if data is not None else None)
    require(body is None or len(body) <= HTTP_LIMIT, "role HTTP request exceeds its bound")
    try:
        connection.request(method, path + ("?" + urlencode(query) if query else ""), body=body,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Connection": "close"})
        response = connection.getresponse()
        raw = bytearray()
        while True:
            remaining = end - time.monotonic()
            require(remaining > 0, "role HTTP response exceeded its deadline")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, HTTP_LIMIT - len(raw) + 1))
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= HTTP_LIMIT, "role HTTP response exceeded its byte bound")
        require(time.monotonic() <= end and response.status == expected, "role HTTP response did not match its declared result")
        return raw.decode() if text else json.loads(raw)
    finally:
        connection.close()


class LifecycleTLS:
    """One owned fixed upstream; no fabricated lifecycle results or retries."""

    def __init__(self, trial, upstream_port, listen_port, certificate):
        self.trial, self.upstream_port = trial, upstream_port
        self.lock, self.errors = threading.Lock(), []
        self.requests, self.handlers, self.sockets = 0, [], {}
        self.closed = threading.Event()
        self.drop_next_ensure = False
        self.before_ensure = None
        self.lost_replies = self.admission_races = 0
        self.ensure_payloads = []
        self.ensure_bodies = []
        self.thread = None
        self.thread_start_unknown = False
        self.close_callback_elapsed_seconds = None
        owner = self
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def get_request(self):
                raw, address = self.socket.accept()
                raw.settimeout(3)
                try:
                    return context.wrap_socket(raw, server_side=True), address
                except BaseException:
                    raw.close()
                    raise

            def process_request(self, request, address):
                thread = threading.Thread(target=self.process_request_thread, args=(request, address), daemon=True)
                with owner.lock:
                    if owner.closed.is_set() or owner.requests >= 512 or len(owner.sockets) >= 4:
                        owner.errors.append("request_budget")
                        self.shutdown_request(request)
                        return
                    owner.requests += 1
                    owner.handlers.append(thread)
                    owner.sockets[thread] = request
                try:
                    thread.start()
                except BaseException:
                    with owner.lock:
                        owner.sockets.pop(thread, None)
                        owner.errors.append("handler_start")
                    self.shutdown_request(request)
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    with owner.lock:
                        owner.sockets.pop(threading.current_thread(), None)

            def handle_error(self, *_):
                with owner.lock:
                    owner.errors.append("handler_error")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                connection = None
                try:
                    request_started = time.monotonic()
                    end = request_started + owner.trial.limit(ORDINARY_HTTP_SECONDS)
                    require(self.path.startswith("/api/v2/workers") and len(self.path) <= 8192,
                            "role forwarder received a foreign path")
                    require(not self.headers.get("Transfer-Encoding"), "role forwarder requires a bounded body")
                    length = int(self.headers.get("Content-Length", "0"))
                    require(0 <= length <= HTTP_LIMIT, "role forwarded request exceeds its bound")
                    body = self.rfile.read(length)
                    require(len(body) == length, "role forwarded body is incomplete")
                    ensure = self.command == "POST" and urlsplit(self.path).path == "/api/v2/workers/ensure"
                    if ensure:
                        with owner.trial.ensure_lock:
                            require(owner.trial.ensure_calls < 8, "role trial exhausted its native ensure budget")
                            owner.trial.ensure_calls += 1
                        with owner.lock:
                            owner.ensure_payloads.append(json.loads(body))
                            owner.ensure_bodies.append(bytes(body))
                    before, drop, request_seconds = owner.request_controls(ensure)
                    if before is not None:
                        # Only the single armed fixture ensure may wait for an
                        # installed, fully verified sealed admin command. All
                        # other lifecycle requests retain their three seconds.
                        end = min(request_started + request_seconds,
                                  time.monotonic() + owner.trial.limit(request_seconds))
                        close_started = time.monotonic()
                        before()
                        close_elapsed = time.monotonic() - close_started
                        require(close_elapsed <= RACE_CLOSE_SECONDS, "generated close callback exceeded its declared allowance")
                        owner.close_callback_elapsed_seconds = round(close_elapsed, 6)
                        owner.admission_races += 1
                    remaining = end - time.monotonic()
                    require(remaining > 0, "role forwarder exhausted its request deadline")
                    connection = http.client.HTTPConnection("127.0.0.1", owner.upstream_port,
                                                            timeout=min(ORDINARY_HTTP_SECONDS, remaining))
                    connection.request(self.command, self.path, body=body if self.command == "POST" else None,
                        headers={"Authorization": self.headers.get("Authorization", ""),
                                 "Content-Type": "application/json", "Connection": "close"})
                    response = connection.getresponse()
                    payload = bytearray()
                    while True:
                        remaining = end - time.monotonic()
                        require(remaining > 0, "role forwarder response exceeded its deadline")
                        if connection.sock is not None:
                            connection.sock.settimeout(min(ORDINARY_HTTP_SECONDS, remaining))
                        raw = response.read1(min(65536, HTTP_LIMIT - len(payload) + 1))
                        if not raw:
                            break
                        payload.extend(raw)
                        require(len(payload) <= HTTP_LIMIT, "role forwarder response exceeded its byte bound")
                    if drop:
                        require(response.status == 200 and json.loads(payload).get("worker_id"),
                                "lost reply fixture did not observe an actual native ensure")
                        owner.lost_replies += 1
                        self.close_connection = True
                        return
                    require(time.monotonic() <= end, "role forwarder completed after its deadline")
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.connection.settimeout(max(0.001, end - time.monotonic()))
                    self.wfile.write(payload)
                except (ConnectionError, OSError, http.client.HTTPException):
                    # An intentionally stopped fixed upstream is a transport
                    # failure, not evidence that its original operation ended.
                    self.close_connection = True
                except BaseException:
                    with owner.lock:
                        owner.errors.append("forwarding_contract")
                    self.close_connection = True
                finally:
                    if connection is not None:
                        connection.close()

        self.server = Server(("127.0.0.1", listen_port), Handler)
        # Listener ownership begins when construction returns, before the
        # fallible thread creation/start boundary.
        trial.frontends.append(self)
        self.server.timeout = 0.1
        def serve():
            while not self.closed.is_set():
                try:
                    self.server.handle_request()
                except (OSError, ValueError):
                    if not self.closed.is_set():
                        with self.lock:
                            self.errors.append("listener_failure")
                    return
        self.thread = threading.Thread(target=serve, name="role-tls-front", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.thread_start_unknown = True
            self.begin_stop()
            raise

    def begin_stop(self):
        self.closed.set()
        self.server.server_close()
        with self.lock:
            for owned in self.sockets.values():
                try:
                    owned.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def request_controls(self, ensure):
        if not ensure:
            return None, False, ORDINARY_HTTP_SECONDS
        with self.lock:
            before, self.before_ensure = self.before_ensure, None
            drop, self.drop_next_ensure = self.drop_next_ensure, False
        return before, drop, RACE_REQUEST_SECONDS if before is not None else ORDINARY_HTTP_SECONDS

    def finish_stop(self, deadline):
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(timeout=max(0, deadline - time.monotonic()))
        for thread in self.handlers:
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        return (not self.thread_start_unknown and (self.thread is None or not self.thread.is_alive())
                and all(not thread.is_alive() for thread in self.handlers))


def role_dependency_plan(root, original, services):
    if original is None:
        return None
    raw = read_private(Path(original["manifest"]))
    require(digest(raw) == original["sha256"], "source dependency plan digest changed")
    plan = json.loads(raw)
    require(plan["schema_version"] == 1, "role trial requires the original three-service sealed plan")
    selected = {name: plan["environments"][name] for name in services}
    used = {filename for row in selected.values() for filename in row["wheels"]}
    role = dict(plan, schema_version=2, environments=selected,
                artifacts=[row for row in plan["artifacts"] if row["filename"] in used])
    require({row["filename"] for row in role["artifacts"]} == used, "role dependency union is incomplete")
    path = root / ("dependencies-" + "-".join(services) + ".json")
    private_json(path, role)
    return {"manifest": str(path), "sha256": digest(read_private(path)), "artifacts": original["artifacts"]}


def verify_role_environments(bundle, data):
    """Distinguish service directories from the sealed bootstrap's own intent files."""
    from zpm import dependencies
    from zpm.installation_roles import services

    selected = set(services(data))
    directory = bundle / "environments"
    expected = set(selected)
    intents = {}
    if "dependency_plan" in data:
        reference = data["dependency_plan"]
        # Reuse the publisher's bounded private read and exact canonical bytes,
        # including the newline. This does not ignore arbitrary extra files.
        plan = dependencies._json(dependencies._read(bundle / "dependencies/plan.json", dependencies.PLAN_LIMIT,
                                  expected=reference["sha256"], private=True))
        require(type(plan["schema_version"]) is int and plan["schema_version"] == 2
                and set(plan["environments"]) == selected, "role sealed plan has a foreign environment shape")
        identity = {"schema_version": 1, "plan_sha256": reference["sha256"], "interpreter": plan["interpreter"],
                    "sources": {name: value["revision"] for name, value in data["sources"].items()}}
        intents["sealed-intent.json"] = identity
        intents.update({name + "-sealed-intent.json": identity | {"service": name} for name in selected})
        expected.update(intents)
    require({path.name for path in directory.iterdir()} == expected,
            "role bootstrap contains a foreign service environment or unknown retained entry")
    for name in selected:
        info = (directory / name).lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(),
                "role service environment is not an owned directory")
    for name, identity in intents.items():
        require(dependencies._read(directory / name, dependencies.PLAN_LIMIT, private=True) == dependencies._encoded(identity),
                "role sealed intent changed its original plan, interpreter, source revisions or service")


def rows(path, table, maximum=8):
    """Small read-only native-journal oracle, never a state writer."""
    require(table in {"workers", "deliveries", "managed_workers", "worker_operations", "worker_executions", "attempts", "calls", "registry_revisions"},
            "undeclared role journal table")
    require(path.is_file() and not path.is_symlink(), "role journal oracle requires an existing regular file")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as database:
        database.row_factory = sqlite3.Row
        result = database.execute('SELECT * FROM "' + table + '" ORDER BY rowid LIMIT ?', (maximum + 1,)).fetchall()
    require(len(result) <= maximum, "role journal history exceeded its declared cardinality")
    return [dict(row) for row in result]


def check_registry_timeout(path, expected):
    revisions = rows(path, "registry_revisions", maximum=16)
    require(revisions and json.loads(revisions[-1]["policy"])["timeout_seconds"] == expected,
            "retained registry did not confirm its declared current request allowance")


def operation(row):
    return {key: row[key] for key in ("launch_key", "repository", "slot_id", "generation", "profile",
                                     "destination_binding", "execution_qualification")}


def run_contract(parent_root, installed_bundle, original_data, source_paths, revisions, image, *, deny_bootstrap_network=False):
    root = parent_root / "role-installation-contract"
    require(not root.exists(), "role installation trial directory must be fresh")
    root.mkdir(mode=0o700)
    trial = Trial(root)
    phase, checks, failure, report = "declared role inputs", [], None, None
    bundles, inputs, manifests, sealed, approvals = {}, {}, {}, {}, {}
    authority = None
    placement_intended = False
    cleanup = {"outcome": "unknown"}
    fixture = Path(source_paths["zpm"]) / "tests/fixtures/provider-tls"
    ca, certificate = fixture / "ca.pem", fixture / "server.pem"
    environment = clean_environment({"SSL_CERT_FILE": str(ca)})
    environment.pop("SSL_CERT_DIR", None)
    bootstrap_environment = clean_environment()
    selected = {"control": ("zpm", "polyphony"), "a": ("zpm", "openjules"), "b": ("zpm", "openjules")}
    supplied = {"ZPM_INSTALL_" + kind + "_" + label.upper(): secrets.token_urlsafe(32)
                for kind in ("RUNTIME", "LIFECYCLE") for label in ("a", "b")}
    sensitive = set(supplied.values())
    native, frontends = {}, {}
    contexts = {}

    def cli(label, *arguments, allowed=(0,), seconds=45, env=None, cleanup=False):
        return trial.command([sys.executable, str(bundles[label] / "manage.py"), *map(str, arguments)],
            bundles[label], environment if env is None else env, seconds=seconds, allowed=allowed, cleanup=cleanup,
            label=label + ":" + arguments[0])

    def status():
        return cli("control", "status")

    def tick():
        return cli("control", "reconcile", "--max-actions", "6")

    def histories():
        return rows(bundles["control"] / "state/zpm.sqlite3", "workers")

    def settle(count):
        value = tick()
        return value if value["reserved"] == count and value["repositories"][0]["healthy"] == count else None

    def service_commands(label, authority_only=False):
        bundle, data = bundles[label], manifests[label]["configuration"]
        # Exact generated argv is an ownership oracle, not a second launcher.
        from zpm.deployment import _command
        names = ("polyphony",) if authority_only else (("polyphony", "zpm") if label == "control" else ("openjules",))
        return [_command(bundle, name, data) for name in names]

    def start(label, authority_only=False):
        trial.start(label, bundles[label], environment, service_commands(label, authority_only), authority_only=authority_only)
        for child in trial.managers[label]["services"]:
            with (Path("/proc") / str(child["pid"]) / "environ").open("rb") as source:
                raw = source.read(HTTP_LIMIT + 1)
            require(len(raw) <= HTTP_LIMIT and original_live(child), "owned role environment observation is incomplete")
            values = dict(item.decode().split("=", 1) for item in raw.rstrip(b"\0").split(b"\0") if item)
            if label != "control":
                expected = {"ZPM_INSTALL_RUNTIME_" + label.upper(): supplied["ZPM_INSTALL_RUNTIME_" + label.upper()]}
            elif "zpm" in child["argv"]:
                expected = {name: supplied[name] for name in ("ZPM_INSTALL_LIFECYCLE_A", "ZPM_INSTALL_LIFECYCLE_B")}
            else:
                expected = {}
            require({name: value for name, value in values.items() if name.startswith("ZPM_INSTALL_")} == expected
                    and not (set(values.values()) & (sensitive - set(expected.values()))),
                    "actual role service environment crossed its declared scoped credential boundary")

    def registry(enabled, *, timeout_seconds=ORDINARY_HTTP_SECONDS):
        result = {"schema_version": 2, "max_workers": 2, "timeout_seconds": timeout_seconds, "destinations": [], "profiles": []}
        for label in ("a", "b"):
            binding = approvals[label]["destination_binding"]
            result["destinations"].append({"destination_id": binding["destination_id"],
                "journal_incarnation": binding["journal_incarnation"], "endpoint": endpoints[label],
                "token_ref": "env:ZPM_INSTALL_LIFECYCLE_" + label.upper(), "max_workers": 1,
                "enabled": label in enabled})
            result["profiles"].append({"destination_id": binding["destination_id"], "repository": repository,
                "profile": profile, "policy_binding_id": binding["policy_binding_id"], "policy_binding_version": 1})
        path = root / ("registry-" + digest(canonical(result).encode()) + ".json")
        if not path.exists():
            private_json(path, result)
        report = cli("control", "registry-import", path)
        check_registry_timeout(bundles["control"] / "state/zpm.sqlite3", timeout_seconds)
        return report

    def lifecycle(label, path, *, query=None, token=None, expected=200):
        return exchange(endpoints[label], "GET", path, token=token or supplied["ZPM_INSTALL_LIFECYCLE_" + label.upper()],
                        budget=trial, query=query, expected=expected, ca=ca)

    try:
        require(not deny_bootstrap_network or "dependency_plan" in original_data,
                "denied role bootstrap requires an explicit already-acquired sealed plan")
        engine_id = trial.command(["docker", "info", "--format", "{{.ID}}"], root, environment,
                                  seconds=15, parse=False).strip()
        require(0 < len(engine_id) <= 512, "native Docker Engine identity is unavailable")
        host_witness = {"docker_engine_id_sha256": digest(engine_id),
                        "boot_id_sha256": digest(Path("/proc/sys/kernel/random/boot_id").read_bytes())}
        source = root / "source"
        source.mkdir(mode=0o755)
        (source / "answer.py").write_text("answer = 0\n")
        (source / ".gitignore").write_text(".role-*\n")
        for arguments in (["init", "-q"], ["add", "."], ["-c", "user.name=Role Trial",
                "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled role source"]):
            trial.command(["git", "-C", str(source), *arguments], source, environment, seconds=15, parse=False)
        base = trial.command(["git", "-C", str(source), "rev-parse", "HEAD"], source, environment, seconds=15, parse=False).decode().strip()
        deployment, repository, profile = "roles-" + secrets.token_hex(8), "github:github.com/role-trial/repository", "controlled"
        scope = {"deployment_id": deployment, "repository": repository}
        ports = set()
        while len(ports) < 5:
            trial.limit(1)
            ports.add(port())
        authority_port, port_a, port_b, tls_a, tls_b = sorted(ports)
        authority_endpoint = "http://127.0.0.1:" + str(authority_port)
        endpoints = {"a": "https://127.0.0.1:" + str(tls_a), "b": "https://127.0.0.1:" + str(tls_b)}
        common = {"schema_version": 3, "deployment_id": deployment,
            "binding": {"schema_version": 1, "repository": repository, "profile": profile, "desired_workers": 0},
            "timeout_seconds": 3}
        for label in selected:
            data = common | {"role": "controller-authority" if label == "control" else "execution",
                             "sources": {name: original_data["sources"][name] for name in selected[label]}}
            reference = role_dependency_plan(root, original_data.get("dependency_plan"), selected[label]) if label != "b" else inputs["a"].get("dependency_plan")
            if reference is not None:
                data["dependency_plan"] = reference
            if label == "control":
                data.update(port=authority_port, base_commit=base, max_workers=2,
                    policy={"checks": ["controlled-test"], "reviewers": ["operator"], "max_attempts": 1,
                            "budget_seconds": 120, "attempt_seconds": 60, "capabilities": ["python"]},
                    runtime_principals=[{"identity": "runtime-" + name, "token_ref": "env:ZPM_INSTALL_RUNTIME_" + name.upper()} for name in ("a", "b")],
                    destinations=[{"destination_id": "host-" + name, "token_ref": "env:ZPM_INSTALL_LIFECYCLE_" + name.upper()} for name in ("a", "b")])
                credentials = supplied
            else:
                data.update(port=port_a if label == "a" else port_b, source={"checkout": str(source), "revision": base},
                    image=image, command=["python3", "-I", "-c", TASK_PROGRAM],
                    runtime={"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000, "pids_limit": 64,
                             "root_timeout_ms": 60000, "max_archive_bytes": 8388608},
                    destination_id="host-" + label, worker_ceiling=1, authority_endpoint=authority_endpoint,
                    runtime_principal="runtime-" + label, runtime_token_ref="env:ZPM_INSTALL_RUNTIME_" + label.upper(),
                    lifecycle_token_ref="env:ZPM_INSTALL_LIFECYCLE_" + label.upper())
                credentials = {name: value for name, value in supplied.items() if name.endswith("_" + label.upper())}
            inputs[label], bundles[label] = data, root / ("bundle-" + label)
            input_path = root / ("input-" + label + ".json")
            private_json(input_path, data)
            prepare = [str(installed_bundle / "environments/zpm/bin/zpm"), "deployment", "prepare", str(input_path),
                       "--directory", str(bundles[label])]
            trial.command(prepare, source_paths["zpm"], environment | credentials, label=label + ":prepare")
            cli(label, "verify")
            manifests[label] = json.loads(read_private(bundles[label] / "manifest.json"))
            sealed[label] = {name: read_private(bundles[label] / name) for name in manifests[label]["files"]
                             if name.startswith(("config/", "secrets/"))}
            # Existing-bundle preparation receives no ambient supplied tokens.
            trial.command(prepare, source_paths["zpm"], environment, label=label + ":prepare-repeat")
            phase = "fresh role bootstrap " + label
            for suffix, seconds in (("first", 240), ("repeat", 60)):
                command = bootstrap_command(bundles[label], root, "role-" + label + "-" + suffix,
                                            deny_network=deny_bootstrap_network)
                trial.command(command, bundles[label], bootstrap_environment, seconds=seconds, parse=False,
                              label=label + ":bootstrap-" + suffix)
                if label != "control":
                    context = read_private(bundles[label] / "environments/openjules/openjules-managed-installation.json")
                    if suffix == "first":
                        contexts[label] = context
                    else:
                        require(contexts[label] == context, "repeated role bootstrap replaced native installation context")
            verify_role_environments(bundles[label], manifests[label]["configuration"])
            require(sealed[label] == {name: read_private(bundles[label] / name) for name in sealed[label]},
                    "repeated role preparation/bootstrap changed scoped credentials or configuration")
            cli(label, "verify")
        checks.append("three fresh generated bundles prepare and bootstrap only their role services; repeat preparation uses retained credentials")

        phase = "role credential separation and explicit native approval"
        operators = json.loads(sealed["control"]["secrets/operator.json"])
        sensitive.update(operators.values())
        require(set(operators) == {"owner", "reviewer", "ingress"}, "controller operator catalog changed")
        require(set(json.loads(sealed["control"]["secrets/environment.json"])) == {"ZPM_INSTALL_LIFECYCLE_A", "ZPM_INSTALL_LIFECYCLE_B"},
                "controller runtime environment contains foreign authority credentials")
        for label in ("a", "b"):
            expected_files = {"config/openjules.json", "config/repository.json", "secrets/openjules.json", "secrets/environment.json"}
            require(set(sealed[label]) == expected_files, "execution role contains a controller configuration or credential file")
            require(json.loads(sealed[label]["secrets/environment.json"]) == {"ZPM_INSTALL_RUNTIME_" + label.upper(): supplied["ZPM_INSTALL_RUNTIME_" + label.upper()]},
                    "execution role environment crossed its runtime principal boundary")
            own = {supplied["ZPM_INSTALL_RUNTIME_" + label.upper()], supplied["ZPM_INSTALL_LIFECYCLE_" + label.upper()]}
            require(not any(token.encode() in b"".join(sealed[label].values()) for token in sensitive - own),
                    "execution role received another principal or operator credential")
            require(not (bundles[label] / "state/zpm.sqlite3").exists(), "execution role contains a controller journal")
            inspected = cli(label, "initialize-destination")
            repeated = cli(label, "initialize-destination")
            require(inspected == repeated, "repeat initialization changed the original destination")
            cli(label, "inspect-destination")
            path = root / ("approval-" + label + ".json")
            preview = cli(label, "preview-execution-contract", "--output", path)
            require(preview["approved"] is False, "preview performed implicit approval")
            approvals[label] = json.loads(read_private(path))
            cli(label, "approve-execution-contract", path)
            require(cli(label, "inspect-admission")["new_effects_open"] is False, "fresh host admission was open")
            require(cli(label, "set-worker-ceiling", "1")["worker_ceiling"] == 1, "host ceiling differs")
            native[label] = json.loads(sealed[label]["config/openjules.json"])
            require(not native[label].get("models") and not native[label].get("children"), "role fixture enables inference or children")
        require(approvals["a"]["destination_binding"] != approvals["b"]["destination_binding"]
                and approvals["a"]["destination_binding"]["journal_incarnation"] != approvals["b"]["destination_binding"]["journal_incarnation"],
                "execution bundles share a native journal incarnation")
        require(approvals["a"]["execution_manifest_sha256"] == approvals["b"]["execution_manifest_sha256"],
                "the same portable source/image profile produced different H")
        registry(set())
        for label in ("a", "b"):
            cli("control", "import-approval", root / ("approval-" + label + ".json"))
        cli("control", "activate", "--offline")
        require(status()["reserved"] == 0, "offline format activation placed work")
        start("control", authority_only=True)
        authority = lambda path="/managed/snapshot", **kw: exchange(authority_endpoint, "GET", path,
            token=supplied["ZPM_INSTALL_RUNTIME_A"], budget=trial, query=scope, **kw)
        trial.wait(lambda: authority(), "role authority readiness", 30)
        for label, listen, upstream in (("a", tls_a, port_a), ("b", tls_b, port_b)):
            start(label)
            frontends[label] = LifecycleTLS(trial, upstream, listen, certificate)
            ready_query = {"schema_version": 2, **scope, "profile": profile, **approvals[label]["destination_binding"]}
            trial.wait(lambda label=label, query=ready_query: lifecycle(label, "/api/v2/workers/capabilities", query=query),
                       "native lifecycle listener readiness", 30)
            trial.wait(lambda label=label: cli(label, "doctor", allowed=(0, 3))["qualification_complete"], "native role readiness", 30)
        closed_doctor = cli("control", "doctor", allowed=(3,))
        require(closed_doctor["qualification_complete"] is True and closed_doctor["ready"] is False
                and closed_doctor["placement_enabled"] is False and status()["reserved"] == 0,
                "closed host qualification implicitly enabled placement or admission")
        checks.append("generated native initialization, preview, approval, registry import and offline activation retain distinct B and equal portable H with target zero and closed admission")
        checks.append("role-only files, installed environments and actual owned service environments keep operator and peer credentials out of execution roles")

        phase = "actual lifecycle TLS, credential and identity refusals"
        query = {"schema_version": 2, **scope, "profile": profile, **approvals["a"]["destination_binding"]}
        lifecycle("a", "/api/v2/workers/capabilities", query=query, token=secrets.token_urlsafe(32), expected=401)
        foreign = query | approvals["b"]["destination_binding"]
        lifecycle("a", "/api/v2/workers/capabilities", query=foreign, expected=409)
        no_ca = {name: value for name, value in environment.items() if name not in {"SSL_CERT_FILE", "SSL_CERT_DIR"}}
        before_requests = sum(front.requests for front in frontends.values())
        refused_doctor = cli("control", "doctor", allowed=(3,), env=no_ca)
        require(refused_doctor["qualification_complete"] is False and refused_doctor["ready"] is False
                and sum(front.requests for front in frontends.values()) == before_requests,
                "missing-CA doctor reached a credentialed HTTP handler or qualified the host")
        require(cli("control", "doctor", allowed=(3,))["qualification_complete"] is True,
                "restored explicit CA did not requalify exact role bindings")
        checks.append("real lifecycle HTTPS accepts only the declared CA and native scoped credential/binding; authority remains explicitly loopback HTTP")

        phase = "lost original ensure reply and normal service restart while closed"
        cli("a", "open-admission")
        cli("b", "open-admission")
        registry({"a"})
        # Retain this before the positive target command: even a lost or failed
        # reply can have invited placement and still needs cleanup intent.
        placement_intended = True
        cli("control", "target", "1")
        frontends["a"].drop_next_ensure = True
        first = cli("control", "reconcile", "--max-actions", "3")
        original = histories()
        require(first["reserved"] == 1 and len(original) == 1 and json.loads(original[0]["snapshot"])["state"] == "unknown"
                and frontends["a"].lost_replies == 1, "lost actual ensure reply did not retain the original unknown reservation")
        frozen = operation(original[0])
        original_payload = dict(frontends["a"].ensure_payloads[0])
        cli("a", "close-admission")
        shutdown = trial.stop("a")
        unavailable = tick()
        require(unavailable["reserved"] == 1 and operation(histories()[0]) == frozen,
                "normal service outage released or replaced the original uncertain operation")
        registry({"a", "b"})
        cli("control", "target", "2")
        trial.wait(lambda: tick()["reserved"] == 2, "independent second destination progress", 30)
        require(operation(next(row for row in histories() if row["launch_key"] == frozen["launch_key"])) == frozen,
                "healthy peer progress rebound the original destination")
        start("a")
        require(cli("a", "inspect-admission")["new_effects_open"] is False, "restarting silently opened host admission")
        trial.wait(lambda: settle(2), "same original destination recovery while closed", 30)
        recovered = next(row for row in histories() if row["launch_key"] == frozen["launch_key"])
        require(operation(recovered) == frozen and len(histories()) == 2,
                "role service restart created substitute capacity")
        require(frontends["a"].ensure_payloads == [original_payload], "original recovery silently resent or replaced ensure")
        checks.append("one dropped real ensure reply, normal owned service shutdown and closed restart preserve original scope/key/B/H/A while the second destination progresses")

        phase = "generated host admission race and retained permanent refusal"
        cli("control", "target", "0")
        trial.wait(lambda: settle(0), "initial role workers drain and capture", 30)
        cli("a", "open-admission")
        registry({"a"}, timeout_seconds=RACE_REQUEST_SECONDS)
        cli("control", "target", "1")
        frontends["a"].before_ensure = lambda: cli("a", "close-admission", seconds=RACE_CLOSE_SECONDS)
        def refused():
            tick()
            matching = [row for row in histories() if json.loads(row["snapshot"]).get("disposition") is not None]
            return matching[0] if len(matching) == 1 else None
        refusal = trial.wait(refused, "actual stale-admission refusal", RACE_CASE_SECONDS)
        refusal_snapshot = json.loads(refusal["snapshot"])
        require(frontends["a"].admission_races == 1 and refusal_snapshot["disposition"]["reason"] == "stale_admission_revision"
                and refusal_snapshot["disposition"]["kind"] == "never_admitted"
                and refusal_snapshot["termination_confirmed"] and refusal_snapshot["artifacts_captured"]
                and refusal_snapshot["active_executions"] == [], "native race did not yield the exact never-admitted result")
        refused_bodies = [body for payload, body in zip(frontends["a"].ensure_payloads, frontends["a"].ensure_bodies)
                          if payload["launch_key"] == refusal["launch_key"]]
        require(len(refused_bodies) == 1, "native refusal lacks one captured original ensure body")
        refused_body = refused_bodies[0]
        cli("control", "target", "0")
        cli("a", "open-admission")
        bound_query = {"schema_version": 2, **scope, **approvals["a"]["destination_binding"]}
        observed = lifecycle("a", "/api/v2/workers/" + refusal_snapshot["worker_id"], query=bound_query)
        require(observed == refusal_snapshot, "reopening changed the original refusal observation")
        # Fixture-only idempotence replay of the exact captured original body.
        # It passes through the same HTTPS frontend and native producer, and is
        # counted by the common eight-ensure-call budget like any other ensure.
        replayed = exchange(endpoints["a"], "POST", "/api/v2/workers/ensure",
            token=supplied["ZPM_INSTALL_LIFECYCLE_A"], budget=trial, ca=ca, raw_body=refused_body)
        require(replayed == refusal_snapshot and status()["reserved"] == 0
                and status()["repositories"][0]["desired_workers"] == 0
                and cli("a", "inspect-admission")["unreleased_workers"] == 0,
                "exact original refusal replay changed identity, disposition or zero capacity")
        require(next(row for row in histories() if row["launch_key"] == refusal["launch_key"])["snapshot"] == refusal["snapshot"],
                "controller lost its original refusal history")
        checks.append("generated close-admission between real readiness and original ensure produces a permanent native never-admitted row; exact captured-body ensure replay after reopening preserves it at zero capacity")

        phase = "generated foreground roles and two autonomous canonical claims"
        require(status()["reserved"] == 0 and status()["repositories"][0]["desired_workers"] == 0,
                "role mode transition requires proven zero target and reservations")
        for label in ("a", "b"):
            require(cli(label, "close-admission")["new_effects_open"] is False, "mode transition left a host admission open")
        # Registry import owns the same lock as the lifetime watch reconciler.
        # Enable the declared destinations at target zero with hosts closed,
        # before starting that one ordinary writer.
        registry({"a", "b"})
        check_registry_timeout(bundles["control"] / "state/zpm.sqlite3", ORDINARY_HTTP_SECONDS)
        require(status()["reserved"] == 0 and status()["repositories"][0]["desired_workers"] == 0,
                "enabled registry import silently placed role work")
        trial.stop("control")
        start("control")
        trial.wait(lambda: authority(), "retained authority under the ordinary generated manager", 30)
        require(status()["reserved"] == 0, "ordinary manager startup ignored zero target or closed hosts")
        for label in ("a", "b"):
            cli(label, "open-admission")
        cli("control", "target", "2")
        def watch_settled():
            value = status()
            return value if value["reserved"] == value["repositories"][0]["healthy"] == 2 else None
        trial.wait(watch_settled, "ordinary generated reconciler places two qualified workers", 30)
        checks.append("enabled registry import restores a 3-second request allowance at target0/reserved0 with both hosts closed before ordinary watch starts; explicit host opening and target2 follow startup")
        tasks = []
        for index in (1, 2):
            task = exchange(authority_endpoint, "POST", "/managed/enroll", token=operators["ingress"], budget=trial,
                data={"schema_version": 1, **scope, "source": "github:issue:" + str(index),
                      "request_key": "role-code-change-" + str(index), "title": "Controlled role work " + str(index),
                      "requirements": REQUIREMENTS})
            tasks.append(task["task"]["id"])
        def active_pair():
            snapshot = authority()
            attempts = [row for row in snapshot["attempt"] if row["task"] in tasks]
            if len(attempts) != 2:
                return None
            require({row["runtime_binding"]["runtime_principal"] for row in attempts} == {"runtime-a", "runtime-b"},
                    "two canonical tasks did not belong to distinct declared runtime principals")
            require(all(row.get("termination_confirmed") is False for row in attempts), "fixture barrier did not retain both active originals")
            containers = []
            for attempt in attempts:
                label = attempt["runtime_binding"]["runtime_principal"].removeprefix("runtime-")
                state = Path(native[label]["state_directory"])
                deliveries = rows(state / "dispatcher/dispatch.sqlite3", "deliveries")
                delivery = next((row for row in deliveries if row["task_id"] == attempt["task"]), None)
                if delivery is None or delivery["binding"] is None or delivery["phase"] != "dispatching":
                    return None
                binding = json.loads(delivery["binding"])
                require(binding["binding"] == attempt["runtime_binding"]
                        and binding["attempt"]["id"] == attempt["id"]
                        and binding["binding"]["attempt_id"] == attempt["id"],
                        "role dispatcher changed the original canonical task/attempt binding")
                drivers = rows(state / "runtime/journal.sqlite3", "workers")
                driver = next((row for row in drivers if row["worker_id"] == delivery["worker_id"]), None)
                if driver is None or not driver["container_id"] or driver["start_confirmed"] != 1:
                    return None
                executions = rows(state / "runtime/journal.sqlite3", "attempts")
                execution = next((row for row in executions if row["worker_id"] == driver["worker_id"]
                                  and row["attempt_id"] == attempt["id"]), None)
                if execution is None or execution["exec_dispatched"] != 1 or not execution["exec_id"]:
                    return None
                require(execution["cancelled"] == 0 and execution["exit_code"] is None
                        and 0 < execution["seconds"] <= 60 and execution["input_sha256"] == digest(REQUIREMENTS.encode()),
                        "role Docker execution changed its original input, state or bounded attempt")
                local = rows(Path(native[label]["lifecycle_database"]), "worker_executions")
                require([row for row in local if row["worker_id"] == driver["worker_id"]] == [{
                    "worker_id": driver["worker_id"], "execution_id": attempt["id"], "parent_id": None,
                    "termination_confirmed": 0, "artifacts_captured": 0}],
                    "role lifecycle did not retain the exact original canonical execution")
                actual = json.loads(trial.command(["docker", "inspect", driver["container_id"]], root, environment,
                                                  seconds=10, parse=False))
                require(len(actual) == 1, "role worker container is missing or ambiguous")
                witness = {"container_id": driver["container_id"], "worker_id": driver["worker_id"],
                    "spec": json.loads(driver["spec"]), "profile_fingerprint": driver["profile_fingerprint"]}
                require_live_container(actual[0], witness)
                host = actual[0]["HostConfig"]
                require(actual[0]["Image"] == image and host["NetworkMode"] == "none"
                        and host["Memory"] == 536870912 and host["CpuQuota"] == 100000 and host["PidsLimit"] == 64,
                        "actual role container differs from the declared immutable image or resource policy")
                marker = trial.command(["docker", "exec", "--user", "65532", driver["container_id"], "python3", "-I", "-c",
                    "from pathlib import Path; print('ready' if Path('/workspace/run/repo/.role-start').is_file() else 'waiting')"],
                    root, environment, seconds=3, parse=False).strip()
                require(marker in {b"ready", b"waiting"}, "role execution marker oracle returned an unsupported observation")
                if marker != b"ready":
                    return None
                containers.append(driver["container_id"])
            require(len(set(containers)) == 2, "the two original role tasks share a container")
            return snapshot, attempts, containers
        active, attempts, containers = trial.wait(active_pair, "two independently pulled canonical tasks", 30)
        for container in containers:
            # Explicit fixture-only barrier release, after exact native journal
            # and Docker labels establish ownership. No task is assigned here.
            trial.command(["docker", "exec", "--user", "65532", container, "touch", "/workspace/run/repo/.role-release"],
                          root, environment, seconds=10, parse=False)
        def captured_pair():
            snapshot = authority()
            done = [row for row in snapshot["attempt"] if row["task"] in tasks]
            candidates = [row for row in snapshot["candidate"] if row["task"] in tasks]
            return (snapshot, done, candidates) if len(done) == len(candidates) == 2 and all(row.get("termination_confirmed") for row in done) else None
        captured, completed, candidates = trial.wait(captured_pair, "two native captured canonical results", 60)
        artifacts = {}
        held_attempts = {row["task"]: row["id"] for row in attempts}
        retained_candidates = {}
        for attempt in completed:
            require(attempt["id"] == held_attempts[attempt["task"]], "canonical capture replaced the original held attempt")
            require(attempt["launch_fenced"] is True and set(attempt["captured_artifacts"]) == {"patch", "log", "manifest"},
                    "role canonical result lacks native capture/fence evidence")
            own_candidates = [row for row in candidates if row["task"] == attempt["task"]]
            require(len(own_candidates) == 1 and own_candidates[0]["attempt"] == attempt["id"]
                    and own_candidates[0]["patch"] == attempt["captured_artifacts"]["patch"]
                    and own_candidates[0]["base"] == base,
                    "role candidate is not bound to the original attempt, captured patch and approved base")
            retained_candidates[attempt["task"]] = {key: own_candidates[0][key] for key in ("id", "task", "attempt", "patch", "base")}
            contents = {}
            for kind, identity in attempt["captured_artifacts"].items():
                content = authority("/managed/artifacts/" + identity, text=True)
                require(digest(content.encode()) == identity and not any(token in content for token in sensitive),
                        "role canonical artifact changed identity or exposed a scoped credential")
                artifacts[identity] = content
                contents[kind] = content
            require("+answer = 42" in contents["patch"] and "ROLE_INSTALLED_CAPTURED" in contents["log"],
                    "generated role execution did not capture its actual controlled change")
        require({row["task"] for row in completed} == set(tasks) and {row["task"] for row in candidates} == set(tasks)
                and all(row["status"] == "awaiting_review" and row["attempts"] == 1 for row in captured["task"])
                and not captured.get("admission"), "role capacity invented canonical acceptance or duplicated an attempt")
        require((source / "answer.py").read_text() == "answer = 0\n", "role execution modified the shared approved source")
        cli("control", "target", "0")
        trial.wait(lambda: status()["reserved"] == 0, "ordinary generated reconciler drains all capacity", 60)
        checks.append("both installed runtimes voluntarily pull different real canonical tasks, produce captured code/log/manifest under distinct principals and drain to reserved zero")

        phase = "retained role reopening at zero capacity"
        for label in ("a", "b"):
            cli(label, "close-admission")
        trial.stop("control")
        trial.stop("a")
        trial.stop("b")
        for label in ("a", "b"):
            require(cli(label, "inspect-destination")["profiles"][0]["destination_binding"] == approvals[label]["destination_binding"],
                    "normal role shutdown changed the original initialized destination")
            cli(label, "verify")
            start(label)
        start("control")
        trial.wait(lambda: authority(), "retained canonical authority reopening", 30)
        require(status()["reserved"] == 0 and status()["repositories"][0]["desired_workers"] == 0,
                "retained role restart regenerated capacity")
        reopened = authority()
        require({row["task"]: {key: row[key] for key in ("id", "task", "attempt", "patch", "base")}
                 for row in reopened["candidate"]} == retained_candidates,
                "role reopening lost or changed original canonical candidate bindings")
        for identity, content in artifacts.items():
            require(authority("/managed/artifacts/" + identity, text=True) == content, "role reopening changed retained captured results")
        for label in selected:
            require(sealed[label] == {name: read_private(bundles[label] / name) for name in sealed[label]},
                    "installed journey changed its approved role configuration or credentials")
            if label != "control":
                require(contexts[label] == read_private(bundles[label] / "environments/openjules/openjules-managed-installation.json"),
                        "role journey replaced its original native installation context")
                for facility in ("models", "children"):
                    require(not (Path(native[label]["state_directory"]) / facility).exists(),
                            "command-only role unexpectedly created a model or child facility")
        require(trial.command(["docker", "info", "--format", "{{.ID}}"], root, environment,
                              seconds=15, parse=False).strip() == engine_id,
                "role journey switched the actual Docker Engine")
        checks.append("normal role restart reopens original destination identity and captured canonical results with closed hosts and zero target")
        checks.append("both unconfigured model and child facilities remain absent throughout this command-only native journey")

        bootstrap_evidence = {}
        for label in selected:
            reference = inputs[label].get("dependency_plan")
            item = {"services": list(selected[label]), "mode": "sealed" if reference else "ordinary ranged install",
                    "bootstrap_ip_network_denied": deny_bootstrap_network, "network_observations": []}
            if reference:
                item["plan_sha256"] = reference["sha256"]
                item["environments"] = {}
                for service in selected[label]:
                    receipt = json.loads(read_private(bundles[label] / "environments" / service / "sealed-complete.json"))
                    require(receipt["plan_sha256"] == reference["sha256"] and receipt["source_revision"] == revisions[service],
                            "role sealed completion differs from its explicit plan or component pin")
                    item["environments"][service] = {"source_revision": receipt["source_revision"],
                        "installed_set_sha256": digest(canonical(receipt["installed"]).encode()),
                        "distribution_count": len(receipt["installed"])}
            if deny_bootstrap_network:
                for suffix in ("first", "repeat"):
                    record = json.loads((root / ("role-" + label + "-" + suffix + "-network.json")).read_text())
                    require(record["bootstrap_returncode"] == 0 and record["ambient_target_unused"]
                            and record["network_namespace_distinct"] and record["process_namespace_init"],
                            "role package bootstrap lacks the declared network-denial evidence")
                    item["network_observations"].append(record)
            bootstrap_evidence[label] = item
        report = {"schema_version": 1, "passed": True, "component_revisions": revisions, "image": image,
            "source_revision": base, "topology": "one host, one Docker Engine, three fresh generated role bundles",
            "host_witness": host_witness,
            "locations": {"execution": "two Docker runtimes and their two journals on the same native host",
                "inference": "none", "persistence": "private role directories and the local canonical authority database",
                "authority_transport": "literal 127.0.0.1 HTTP", "lifecycle_transport": "two verified HTTPS loopback test frontends with explicit ZPM CA"},
            "checks": checks, "bootstrap": bootstrap_evidence,
            "destination_bindings": {label: approvals[label]["destination_binding"] for label in ("a", "b")},
            "execution_manifest_sha256": approvals["a"]["execution_manifest_sha256"],
            "original_unknown_operation": frozen, "normal_service_restart": shutdown,
            "never_admitted": {"launch_key": refusal["launch_key"], "disposition": refusal_snapshot["disposition"]},
            "admission_close_elapsed_seconds": frontends["a"].close_callback_elapsed_seconds,
            "canonical_results": [{"task": row["task"], "attempt": row["id"],
                "candidate": retained_candidates[row["task"]]["id"], "approved_base": base,
                "runtime_principal": row["runtime_binding"]["runtime_principal"],
                "captured_artifacts": row["captured_artifacts"]} for row in completed],
            "final_reserved": 0, "model_calls": 0, "child_delegations": 0,
            "bounds": {"total_seconds": TOTAL_SECONDS, "cleanup_reserve_seconds": CLEANUP_SECONDS,
                "native_ensure_calls": trial.ensure_calls, "maximum_native_ensure_calls": 8,
                "maximum_concurrent_roots": 2, "root_timeout_ms": 60000, "container_memory_bytes": 536870912,
                "ordinary_lifecycle_request_seconds": ORDINARY_HTTP_SECONDS,
                "one_gated_ensure_request_seconds": RACE_REQUEST_SECONDS,
                "admission_close_command_seconds": RACE_CLOSE_SECONDS, "admission_race_case_seconds": RACE_CASE_SECONDS,
                "container_uid": 65532, "container_cpu_quota": 100000, "container_pids_limit": 64},
            "harness_only": ["bounded lifecycle HTTPS pass-through with one actual-reply loss and one generated close-admission race",
                "one exact captured-body permanent-refusal ensure replay after reopening",
                "read-only native journals and exact /proc service ownership", "label-verified Docker barrier release"],
            "not_proven": ["two independent hosts or Docker Engines", "authority private-CA support or remote authority TLS",
                "public distribution", "browser execution", "model inference", "canonical semantic acceptance", "abrupt producer loss"]}
    except BaseException as error:
        failure = error
    finally:
        trial.cleanup_deadline = min(trial.deadline, time.monotonic() + CLEANUP_SECONDS)
        # A failed scene never asserts task cancellation or destroys retained
        # native resources. Set project intent to zero if the installed control
        # path is available, then signal all owned managers on a common clock.
        if failure is not None and placement_intended and "control" in bundles and (bundles["control"] / "environments/zpm/bin/python").is_file():
            try:
                cli("control", "target", "0", seconds=3, cleanup=True)
            except BaseException:
                pass
        try:
            cleanup = trial.cleanup()
        except BaseException:
            cleanup = {"outcome": "unknown", "ownership_observation_failed": True}
        if failure is None and cleanup.get("outcome") != "observed":
            phase, failure = "owned role cleanup", RuntimeError("owned role cleanup is unknown")
    if failure is not None:
        raise RoleTrialFailure(phase, checks, failure, cleanup) from None
    report["cleanup"] = cleanup
    report["elapsed_seconds"] = round(time.monotonic() - trial.started, 3)
    encoded = canonical(report).encode()
    if len(encoded) > REPORT_LIMIT or any(token.encode() in encoded for token in sensitive) or time.monotonic() >= trial.deadline:
        raise RoleTrialFailure("safe report completion", checks, RuntimeError("report boundary"), cleanup)
    private_json(root / "safe-report.json", report)
    return report
