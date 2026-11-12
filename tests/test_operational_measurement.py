"""Measurement oracles, not installed runtime or live-provider qualification."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode

from zpm.controller import Controller
from zpm.store import Store
from tests.support import FixtureProvider

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import operational_measurement as measurement
finally:
    sys.path.pop(0)


PAYLOAD = {
    "messages": [{"role": "user", "content": "private å漢 instructions"}],
    "tools": [{"name": "private tool"}],
}


def request_fixture(root_limit=6):
    observed = measurement.RequestMeasurements(root_limit)
    observed.enrollment_started(100)
    observed.request("root", 1, PAYLOAD, 110)
    observed.spawn_prepared("left", "CHILD_left: only left", 120)
    # The actual child can call before the parent sees its spawn result.
    observed.request("left", 1, PAYLOAD, 125)
    observed.request("root", 2, PAYLOAD, 130)
    observed.spawn_prepared("right", "CHILD_right: only right", 140)
    observed.request("right", 1, PAYLOAD, 145)
    observed.request("left", 2, PAYLOAD, 150)
    observed.request("right", 2, PAYLOAD, 155)
    for ordinal in range(3, root_limit + 1):
        observed.request("root", ordinal, PAYLOAD, 140 + ordinal * 10)
    identities = {"left": "child_" + "1" * 32, "right": "child_" + "2" * 32}
    permissions = [{
        "child": {
            "id": identities[name],
            "binding": {
                "worker_id": "original-worker", "task_id": "original-task",
                "root_attempt_id": "original-attempt", "parent_attempt_id": "original-attempt",
                "instructions_digest": hashlib.sha256(
                    ("CHILD_" + name + ": only " + name).encode()).hexdigest(),
                "grant_digest": ("a" if name == "left" else "b") * 64,
            },
        },
    } for name in ("left", "right")]
    return observed, identities, permissions


def request_report(observed, identities, permissions):
    return observed.report(
        identities, permissions, worker_id="original-worker",
        task_id="original-task", attempt_id="original-attempt",
    )


class RequestMeasurementTests(unittest.TestCase):
    def test_child_before_parent_reply_has_causal_grant_bound_latency(self):
        observed, identities, permissions = request_fixture()
        report = request_report(observed, identities, permissions)
        self.assertEqual(report["request_count"], 10)
        self.assertEqual(report["enrollment_to_first_root_request_ns"], 10)
        self.assertEqual(
            [row["spawn_response_prepared_to_first_request_ns"] for row in report["fresh_children"]], [5, 5])
        self.assertEqual({row["child_id"] for row in report["fresh_children"]}, set(identities.values()))
        self.assertEqual(report["requests"][1]["actor"], "left")
        self.assertEqual(report["requests"][2]["actor"], "root")

    def test_unicode_counts_are_logical_utf8_bytes_without_retaining_private_context(self):
        observed, identities, permissions = request_fixture(8)
        report = request_report(observed, identities, permissions)
        row = report["requests"][0]
        self.assertEqual(row["message_count"], 1)
        self.assertEqual(row["messages_bytes"], len(
            '[{"content":"private å漢 instructions","role":"user"}]'.encode("utf-8")))
        self.assertEqual(row["tools_bytes"], len('[{"name":"private tool"}]'.encode("utf-8")))
        self.assertEqual(row["request_bytes"], len(
            '{"messages":[{"content":"private å漢 instructions","role":"user"}],"tools":[{"name":"private tool"}]}'
            .encode("utf-8")))
        self.assertEqual(report["request_count"], 12)
        self.assertNotIn("private", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("instructions", json.dumps(report["requests"]))
        self.assertIn("not HTTP bytes", report["byte_scope"])

    def test_retained_grant_mismatch_and_missing_or_duplicate_child_fail(self):
        observed, identities, permissions = request_fixture()
        for key, value in (
            ("worker_id", "foreign-worker"), ("task_id", "foreign-task"),
            ("root_attempt_id", "foreign-attempt"), ("parent_attempt_id", "foreign-parent"),
            ("instructions_digest", "0" * 64), ("grant_digest", True),
        ):
            changed = deepcopy(permissions)
            changed[0]["child"]["binding"][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                request_report(observed, identities, changed)
        for changed in (permissions[:1], [permissions[0], permissions[0]]):
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                request_report(observed, identities, changed)

    def test_bad_order_boolean_numbers_and_nonfinite_json_fail(self):
        for timestamp in (True, -1, float("inf")):
            with self.subTest(timestamp=timestamp), self.assertRaises(AssertionError):
                measurement.elapsed(timestamp, 100)
        observed = measurement.RequestMeasurements(6)
        with self.assertRaises(AssertionError):
            observed.request("left", 1, PAYLOAD, 100)
        with self.assertRaises(AssertionError):
            observed.request("root", True, PAYLOAD, 100)
        observed.spawn_prepared("left", "initial", 120)
        with self.assertRaises(AssertionError):
            observed.request("left", 1, PAYLOAD, 119)
        with self.assertRaises(AssertionError):
            observed.spawn_prepared("left", "replaced", 130)
        with self.assertRaises(AssertionError):
            measurement.json_bytes({"unsafe": float("nan")})
        with patch.object(measurement, "MAX_JSON_BYTES", 8), self.assertRaises(AssertionError):
            measurement.json_bytes(PAYLOAD)


def stat_line(pid, *, ppid=99, started=50, user=10, system=4, rss=3, state="S"):
    # Linux fields3..24: state,ppid,...,utime,stime,...,starttime,vsize,rss.
    fields = [state, str(ppid)] + ["0"] * 20
    fields[11], fields[12] = str(user), str(system)
    fields[19], fields[21] = str(started), str(rss)
    return str(pid) + " (name with spaces ) and ()) " + " ".join(fields) + "\n"


def ready_idle_plan(directory):
    # Use the real controller/store shape, with a labelled in-memory provider.
    with Store(directory / "idle.sqlite3", "idle-deployment") as store:
        store.enroll({"schema_version": 1, "repository": "owner/idle", "profile": "fixture", "desired_workers": 1})
        return Controller(store, FixtureProvider(), max_workers=1).tick()


def access_record(binding, event="capabilities", *, method="GET", status=200, extra_query=(), worker=None):
    query = [(name, binding[name]) for name in ("deployment_id", "repository")]
    path = "/api/workers/" + quote(worker or binding["worker_id"], safe="")
    if event == "capabilities":
        path = "/api/workers/capabilities"
        query.append(("profile", binding["profile"]))
    return ('INFO:     127.0.0.1:45231 - "' + method + " " + path + "?" + urlencode(query + list(extra_query))
            + ' HTTP/1.1" ' + str(status) + (" OK" if status == 200 else " Service Unavailable") + "\n").encode()


class ReaderClock:
    def __init__(self, during=None):
        self.now = 100
        self.during = during
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if not 0 < seconds <= 0.1:
            raise AssertionError("observer sleep was not bounded")
        self.sleeps.append(seconds)
        self.now += round(seconds * 1_000_000_000)
        if self.during is not None:
            self.during()


class IdleReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "installation.log"
        self.plan = ready_idle_plan(self.root)
        self.binding = measurement.idle_worker_binding(self.plan)
        self.manager = SimpleNamespace(poll=lambda: None)
        self.sequence = [access_record(self.binding, event) for event in (
            "capabilities", "worker", "capabilities", "worker", "capabilities",
        )]

    def observe(self, chunks, *, prefix=b"", action=None):
        self.log.write_bytes(prefix)
        chunks = iter(chunks)

        def during():
            if action is not None:
                action()
            value = next(chunks, b"")
            if value:
                with self.log.open("ab") as stream:
                    stream.write(value)

        self.clock = ReaderClock(during)
        with patch("socket.socket", side_effect=AssertionError("sampler opened a socket")), \
                patch("subprocess.Popen", side_effect=AssertionError("sampler started a process")), \
                patch("sqlite3.connect", side_effect=AssertionError("sampler opened SQLite")):
            return measurement._idle_reconciliation_witness(
                self.log, self.binding, self.manager, self.clock.now, self.clock, self.clock.sleep,
            )

    def test_complete_middle_cycle_uses_new_flushed_records_and_sanitized_offsets(self):
        stale = b"".join(self.sequence)
        report = self.observe(self.sequence, prefix=stale)
        self.assertEqual([row["event"] for row in report["events"]],
                         ["capabilities", "worker", "capabilities", "worker", "capabilities"])
        self.assertEqual(report["log_start_offset"], len(stale))
        self.assertEqual(report["log_bytes_read"], len(stale))
        self.assertEqual(report["complete_lines_examined"], 5)
        self.assertEqual(report["events"][0]["log_start_offset"], len(stale))
        self.assertTrue(all(row["reader_observed_after_start_ns"] > 0 for row in report["events"]))
        self.assertNotIn("HTTP", json.dumps(report))
        self.assertNotIn(str(self.log), json.dumps(report))

    def test_one_prior_worker_and_partial_prefix_never_supply_witness_events(self):
        partial = self.sequence[0][:30]
        report = self.observe([self.sequence[0][30:], self.sequence[1]] + self.sequence, prefix=partial)
        self.assertEqual(report["leading_worker_observation_ignored"], 1)
        self.assertEqual(report["discarded_partial_prefix_bytes"], len(self.sequence[0]) - len(partial))
        self.assertEqual(len(report["events"]), 5)
        self.assertEqual(report["events"][0]["log_start_offset"], len(self.sequence[0]) + len(self.sequence[1]))

    def test_old_records_or_two_capabilities_cannot_complete_proof(self):
        for records in ([], self.sequence[:3], self.sequence[:4]):
            with self.subTest(length=len(records)), self.assertRaisesRegex(AssertionError, "deadline"):
                self.observe(records, prefix=b"".join(self.sequence))
            self.assertEqual(self.clock.now, 60_000_000_100)

    def test_wrong_status_scope_worker_method_or_query_cannot_be_filtered_out(self):
        wrong_scope = self.binding | {"deployment_id": "other"}
        records = [
            access_record(self.binding, status=503), access_record(self.binding, method="POST"),
            access_record(wrong_scope), access_record(self.binding, "worker", worker="another-worker"),
            access_record(self.binding, extra_query=[("repository", self.binding["repository"])]),
            access_record(self.binding, extra_query=[("unknown", "value")]),
            access_record(self.binding, "worker", extra_query=[("profile", self.binding["profile"])]),
            self.sequence[0].replace(b"INFO:", b"not-an-access-record:"),
        ]
        for record in records:
            with self.subTest(record=record), self.assertRaises(AssertionError):
                self.observe([self.sequence[0], record] + self.sequence[1:])

    def test_duplicate_leading_worker_or_middle_capability_refuses(self):
        for records in ([self.sequence[1], self.sequence[1]] + self.sequence,
                        [self.sequence[0], self.sequence[0]] + self.sequence[1:]):
            with self.subTest(records=records[:2]), self.assertRaisesRegex(AssertionError, "sequence"):
                self.observe(records)

    def test_reordered_valid_query_pairs_and_unrelated_authority_records_are_safe(self):
        def reordered(record):
            prefix, tail = record.split(b"?", 1)
            query, suffix = tail.split(b" HTTP/", 1)
            return prefix + b"?" + b"&".join(reversed(query.split(b"&"))) + b" HTTP/" + suffix
        noise = b'INFO:     127.0.0.1:45122 - "GET /managed/snapshot HTTP/1.1" 200 OK\n'
        report = self.observe([noise + reordered(record) for record in self.sequence])
        self.assertEqual(report["complete_lines_examined"], 10)
        self.assertEqual(len(report["events"]), 5)

    def test_complete_records_can_span_multiple_reads(self):
        payload = b"".join(self.sequence)
        report = self.observe([payload[:11], payload[11:70], payload[70:-1], payload[-1:]])
        self.assertEqual(report["log_bytes_read"], len(payload))
        self.assertEqual(len(report["events"]), 5)

    def test_continuing_irrelevant_traffic_never_renews_cutoff(self):
        with self.assertRaisesRegex(AssertionError, "deadline"):
            self.observe([b"unrelated\n"] * 600)
        self.assertEqual(self.clock.now, 60_000_000_100)

    def test_log_truncation_or_replacement_cannot_reuse_an_offset(self):
        def truncate():
            self.log.write_bytes(b"")
        def replace():
            self.log.rename(self.root / "old.log")
            self.log.write_bytes(b"different inode\n")
        for action in (truncate, replace):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(AssertionError, "truncated|identity"):
                self.observe([], prefix=b"old line\n", action=action)

    def test_symlink_and_special_file_are_rejected_without_blocking(self):
        target = self.root / "target"
        target.write_bytes(b"")
        self.log.symlink_to(target)
        clock = ReaderClock()
        with self.assertRaises(OSError):
            measurement._idle_reconciliation_witness(self.log, self.binding, self.manager, clock.now, clock, clock.sleep)
        self.log.unlink()
        os.mkfifo(self.log)
        with self.assertRaisesRegex(AssertionError, "regular"):
            measurement._idle_reconciliation_witness(self.log, self.binding, self.manager, clock.now, clock, clock.sleep)

    def test_byte_line_and_record_bounds_apply_before_success(self):
        for payload, reason in (
            (b"x" * (measurement.MAX_IDLE_LOG_BYTES + 1), "byte"),
            (b"x" * measurement.MAX_IDLE_LOG_LINE, "line"),
            (b"x\n" * (measurement.MAX_IDLE_LOG_LINES + 1), "record"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(AssertionError, reason + " bound"):
                self.observe([payload + b"".join(self.sequence)])

    def test_dead_manager_and_regressed_clock_are_not_success(self):
        with self.assertRaisesRegex(AssertionError, "manager exited"):
            self.observe([], action=lambda: setattr(self.manager, "poll", lambda: 1))
        self.manager.poll = lambda: None
        self.log.write_bytes(b"")
        with self.assertRaisesRegex(AssertionError, "monotonic"):
            measurement._idle_reconciliation_witness(self.log, self.binding, self.manager, 100, lambda: 99, lambda _: None)

    def test_finalization_requires_exact_original_binding_and_zero_counts(self):
        witness = self.observe(self.sequence)
        sample = {"reconciliation_witness": witness, "completed_reconcile_tick_proven": False}
        for changes in ({"worker_id": "replacement"}, {"generation": 2}, {"launch_key": "replacement"},
                        {"state": "busy", "active_executions": ["task"]}, {"pending_operation": "drain"}):
            after = deepcopy(self.plan)
            after["repositories"][0]["workers"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                measurement.confirm_idle_reconciliation(sample, self.binding, after,
                    {"requests": 0, "errors": 0}, {"requests": 0, "errors": 0})
            self.assertFalse(sample["completed_reconcile_tick_proven"])
        for counts in ({"requests": 1, "errors": 0}, {"requests": 0, "errors": 1},
                       {"requests": False, "errors": 0}, {"requests": 0}):
            with self.subTest(counts=counts), self.assertRaises(AssertionError):
                measurement.confirm_idle_reconciliation(sample, self.binding, self.plan,
                    {"requests": 0, "errors": 0}, counts)
        measurement.confirm_idle_reconciliation(sample, self.binding, self.plan,
            {"requests": 0, "errors": 0}, {"requests": 0, "errors": 0})
        self.assertTrue(sample["completed_reconcile_tick_proven"])
        self.assertEqual(sample["fixture_counts"], {"before": {"requests": 0, "errors": 0},
                                                    "after": {"requests": 0, "errors": 0}})

    def test_non_idle_or_multiple_binding_plan_is_refused(self):
        for change in (
            lambda p: p.update(reserved=2),
            lambda p: p["repositories"].append(deepcopy(p["repositories"][0])),
            lambda p: p["repositories"][0].update(revision=0),
            lambda p: p["repositories"][0].update(desired_workers=0),
            lambda p: p["repositories"][0]["validation"].update(ready=False),
            lambda p: p["repositories"][0]["workers"][0].update(admission_open=False),
        ):
            value = deepcopy(self.plan)
            change(value)
            with self.assertRaises(AssertionError):
                measurement.idle_worker_binding(value)


class ProcessMeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.roles = {"manager": 100, "polyphony": 101, "openjules": 102, "zpm": 103}
        self.commands = {role: ["python", "-m", role] for role in self.roles}
        self.directories = {role: self.cwd for role in self.roles}
        self.manager = SimpleNamespace(pid=100, poll=lambda: None)
        self.reset_cohort()

    def reset_cohort(self):
        for role, pid in self.roles.items():
            directory = self.proc / str(pid)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "stat").write_text(stat_line(pid, ppid=99 if role == "manager" else 100))
            (directory / "cmdline").write_bytes(
                b"\0".join(value.encode() for value in self.commands[role]) + b"\0")
            link = directory / "cwd"
            if link.is_symlink():
                link.unlink()
            link.symlink_to(self.cwd, target_is_directory=True)
        parent = self.proc / "100/task/100"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "children").write_text("101 102 103 ")

    def sample(self, during):
        times = iter((100, 2_000_000_100))
        def sleep(seconds):
            self.assertEqual(seconds, 2)
            during()
        with patch.object(measurement.os, "sysconf", side_effect=lambda name: {
            "SC_CLK_TCK": 100, "SC_PAGE_SIZE": 4096,
        }[name]):
            return measurement.idle_process_sample(
                self.manager, self.commands, self.directories, proc=self.proc,
                clock=lambda: next(times), sleep=sleep,
            )

    def test_stat_command_delimiters_and_counter_units(self):
        parsed = measurement.proc_stat(stat_line(200, user=31, system=7, started=41, rss=11))
        self.assertEqual(parsed, {"pid": 200, "ppid": 99, "user_ticks": 31,
                                 "system_ticks": 7, "starttime_ticks": 41, "rss_pages": 11})
        def advance():
            for role, pid in self.roles.items():
                (self.proc / str(pid) / "stat").write_text(stat_line(
                    pid, ppid=99 if role == "manager" else 100, user=13, system=6, rss=5))
        result = self.sample(advance)
        self.assertEqual(result["observation_ns"], 2_000_000_000)
        self.assertEqual(result["clock_ticks_per_second"], 100)
        self.assertEqual(result["page_size_bytes"], 4096)
        self.assertEqual(set(result["processes"]), set(self.roles))
        for row in result["processes"].values():
            self.assertEqual((row["user_ticks_delta"], row["system_ticks_delta"]), (3, 2))
            self.assertEqual((row["rss_bytes_before"], row["rss_bytes_after"]), (12288, 20480))
        self.assertFalse(result["completed_reconcile_tick_proven"])
        self.assertFalse(result["peak_rss_measured"])

    def test_pid_reuse_counter_regression_or_parent_change_rejects_sample(self):
        for changes in ({"started": 51}, {"user": 9}, {"system": 3}, {"ppid": 999}, {"state": "Z"}):
            self.reset_cohort()
            def mutate():
                values = {"ppid": 100} | changes
                (self.proc / "102/stat").write_text(stat_line(102, **values))
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                self.sample(mutate)

    def test_missing_changed_or_extra_service_cannot_be_hidden(self):
        changes = [
            lambda: (self.proc / "102/stat").unlink(),
            lambda: (self.proc / "102/cmdline").write_bytes(b"another\0"),
            lambda: (self.proc / "100/task/100/children").write_text("101 102 103 104"),
        ]
        for change in changes:
            self.reset_cohort()
            with self.subTest(change=change), self.assertRaises((AssertionError, FileNotFoundError)):
                self.sample(change)
        self.reset_cohort()
        with patch.object(measurement.os, "sysconf", return_value=True), self.assertRaises(AssertionError):
            measurement.idle_process_sample(self.manager, self.commands, self.directories, proc=self.proc)

    def test_every_cpu_interval_contains_the_new_complete_witness(self):
        log = self.root / "installation.log"
        log.write_bytes(b"")
        plan = ready_idle_plan(self.root)
        binding = measurement.idle_worker_binding(plan)
        records = [access_record(binding, event) for event in ("capabilities", "worker", "capabilities", "worker", "capabilities")]
        pending = iter(records)
        order = []

        def during():
            record = next(pending)
            order.append("log")
            with log.open("ab") as stream:
                stream.write(record)

        clock = ReaderClock(during)
        actual_read = measurement._read_process

        def read_process(*args):
            order.append("counter")
            if len(order) <= 4:
                with log.open("ab") as stream:
                    stream.write(b"".join(records))  # Old evidence at every initial counter.
            else:
                self.assertEqual(order[4:9], ["log"] * 5)
            return actual_read(*args)

        with patch.object(measurement, "_read_process", side_effect=read_process):
            sample = measurement.idle_process_sample(self.manager, self.commands, self.directories, proc=self.proc,
                clock=clock, sleep=clock.sleep, log_path=log, binding=binding)
        self.assertEqual(order, ["counter"] * 4 + ["log"] * 5 + ["counter"] * 4)
        self.assertEqual(sample["reconciliation_witness"]["log_start_offset"], 4 * sum(map(len, records)))
        self.assertFalse(sample["completed_reconcile_tick_proven"])
        measurement.confirm_idle_reconciliation(sample, binding, plan, {"requests": 0, "errors": 0},
                                                {"requests": 0, "errors": 0})
        self.assertTrue(sample["completed_reconcile_tick_proven"])

    def test_initial_and_final_counter_collection_share_the_same_cutoff(self):
        log = self.root / "installation.log"
        log.write_bytes(b"")
        binding = measurement.idle_worker_binding(ready_idle_plan(self.root))
        for after in (False, True):
            records = b"".join(access_record(binding, event) for event in (
                "capabilities", "worker", "capabilities", "worker", "capabilities",
            ))

            def during():
                with log.open("ab") as stream:
                    stream.write(records)

            clock = ReaderClock(during)
            calls = 0
            actual_read = measurement._read_process

            def read_process(*args):
                nonlocal calls
                calls += 1
                if calls == (5 if after else 1):
                    clock.now += 60_000_000_000
                return actual_read(*args)

            with self.subTest(after=after), patch.object(measurement, "_read_process", side_effect=read_process):
                with self.assertRaisesRegex(AssertionError, "deadline"):
                    measurement.idle_process_sample(self.manager, self.commands, self.directories, proc=self.proc,
                        clock=clock, sleep=clock.sleep, log_path=log, binding=binding)

    def test_clock_regression_after_witness_cannot_export_impossible_interval(self):
        log = self.root / "installation.log"
        log.write_bytes(b"")
        binding = measurement.idle_worker_binding(ready_idle_plan(self.root))
        records = b"".join(access_record(binding, event) for event in (
            "capabilities", "worker", "capabilities", "worker", "capabilities",
        ))

        def during():
            with log.open("ab") as stream:
                stream.write(records)

        clock = ReaderClock(during)
        calls = 0
        actual_read = measurement._read_process

        def read_process(*args):
            nonlocal calls
            calls += 1
            if calls == 5:
                clock.now = 100  # Back before the witnessed C3, after every initial counter.
            return actual_read(*args)

        with patch.object(measurement, "_read_process", side_effect=read_process), \
                self.assertRaisesRegex(AssertionError, "regressed after"):
            measurement.idle_process_sample(self.manager, self.commands, self.directories, proc=self.proc,
                clock=clock, sleep=clock.sleep, log_path=log, binding=binding)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc ownership fixture")
    def test_real_owned_process_cohort_uses_linux_counters(self):
        commands = {role: [sys.executable, "-c", "import time; time.sleep(30)", role]
                    for role in ("polyphony", "openjules", "zpm")}
        code = (
            "import json,subprocess,sys,time\n"
            "children=[subprocess.Popen(command) for command in json.loads(sys.argv[1])]\n"
            "print('ready',flush=True)\n"
            "time.sleep(30)\n"
        )
        command = [sys.executable, "-c", code, json.dumps(list(commands.values()))]
        process = subprocess.Popen(command, cwd=self.cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, "owned process fixture did not start")
            self.assertEqual(process.stdout.readline(16), b"ready\n")
            result = measurement.idle_process_sample(
                process, commands | {"manager": command},
                {role: self.cwd for role in ("manager", "polyphony", "openjules", "zpm")},
            )
            self.assertGreaterEqual(result["observation_ns"], 2_000_000_000)
            self.assertEqual(result["processes"]["manager"]["pid"], process.pid)
            self.assertEqual(len({row["pid"] for row in result["processes"].values()}), 4)
            self.assertTrue(all(type(row["rss_bytes_before"]) is int and row["rss_bytes_before"] >= 0
                                for row in result["processes"].values()))
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            process.stdout.close()


class StorageMeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.bundle = Path(temporary.name)
        self.journals = {name: self.bundle / (name + ".sqlite3") for name in measurement.JOURNALS}
        self.captures = self.bundle / "runtime/captures"

    def snapshot(self):
        return measurement.storage_snapshot(self.bundle, self.journals, self.captures)

    def test_live_wal_stats_preserve_bytes_and_never_connect_or_checkpoint(self):
        database = sqlite3.connect(self.journals["controller"])
        self.addCleanup(database.close)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE retained(value TEXT)")
        database.execute("INSERT INTO retained VALUES('private retained bytes')")
        database.commit()
        paths = [Path(str(self.journals["controller"]) + suffix) for suffix in ("", "-wal", "-shm")]
        before = {path: path.read_bytes() for path in paths}
        with patch.object(sqlite3, "connect", side_effect=AssertionError("measurement opened SQLite")):
            report = self.snapshot()
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertEqual(report["journals"]["controller"], {
            label: {"present": True, "bytes": len(before[path])}
            for label, path in zip(("main", "wal", "shm"), paths)
        })
        self.assertEqual(report["journals"]["runtime"]["main"], {"present": False, "bytes": 0})
        self.assertNotIn("private retained bytes", json.dumps(report))

    def test_capture_stats_are_bounded_and_do_not_return_names_or_content(self):
        self.captures.mkdir(parents=True)
        (self.captures / "private-worker.tar").write_bytes(b"private archive")
        nested = self.captures / "owned"
        nested.mkdir()
        (nested / "private-result").write_bytes(b"abc")
        report = self.snapshot()
        self.assertEqual(report["captures"], {"present": True, "regular_files": 2, "bytes": 18})
        self.assertNotIn("private", json.dumps(report))
        with patch.object(measurement, "MAX_CAPTURE_ENTRIES", 2), self.assertRaises(AssertionError):
            self.snapshot()

    def test_symlinks_special_files_and_unowned_paths_are_rejected_without_opening(self):
        target = self.bundle / "private-target"
        target.write_text("private")
        selected = self.journals["canonical"]
        selected.symlink_to(target)
        with self.assertRaises(AssertionError):
            self.snapshot()
        selected.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(selected)
            with self.assertRaises(AssertionError):
                self.snapshot()
            selected.unlink()
        self.captures.mkdir(parents=True)
        (self.captures / "link").symlink_to(target)
        with self.assertRaises(AssertionError):
            self.snapshot()
        with self.assertRaises(AssertionError):
            measurement.storage_snapshot(
                self.bundle, self.journals | {"canonical": self.bundle.parent / "foreign.sqlite3"}, self.captures)
        with self.assertRaises(AssertionError):
            measurement.storage_snapshot(
                self.bundle, self.journals | {"canonical": self.journals["runtime"]}, self.captures)

    def test_signed_growth_tracks_distinct_files_and_natural_shrinkage(self):
        times = iter(range(12))
        measured = measurement.Measurements(clock=lambda: next(times))
        for name in measurement.TIMINGS:
            measured.start(name)
            measured.stop(name)
        main = self.journals["canonical"]
        main.write_bytes(b"abc")
        Path(str(main) + "-wal").write_bytes(b"12345")
        Path(str(main) + "-shm").write_bytes(b"1234567")
        for index, phase in enumerate(measurement.STORAGE_PHASES):
            if index == 1:
                Path(str(main) + "-wal").unlink()
            measured.storage_phase(phase, self.bundle, self.journals, self.captures)
        observed, identities, permissions = request_fixture()
        idle = {"observation_ns": 2_000_000_000}
        report = measured.report(idle, request_report(observed, identities, permissions))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["storage_deltas"][0]["journal_bytes"]["canonical"],
                         {"main": 0, "wal": -5, "shm": 0})
        self.assertFalse(report["storage"][1]["journals"]["canonical"]["wal"]["present"])
        self.assertEqual(report["observed_intervals_ns"], {name: 1 for name in measurement.TIMINGS})
        self.assertNotIn(str(self.bundle), json.dumps(report))

    def test_missing_phase_and_excessive_capture_depth_fail_closed(self):
        measured = measurement.Measurements()
        with self.assertRaises(AssertionError):
            measured.storage_phase("ready_target_zero", self.bundle, self.journals, self.captures)
        with self.assertRaises(AssertionError):
            measured.report({"observation_ns": 1}, {"request_count": 10})
        nested = self.captures
        for _ in range(18):
            nested = nested / "next"
        nested.mkdir(parents=True)
        with self.assertRaises(AssertionError):
            self.snapshot()
