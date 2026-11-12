"""Failed-outcome trial controls over real HTTP, SQLite and owned subprocesses.

The local rows and model replies are explicit test fixtures. Only the separate
installed trial establishes actual authority, Docker execution and recovery.
"""

from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_children_stack as trial
finally:
    sys.path.pop(0)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CHILD_SCHEMA = (
    "CREATE TABLE children(child_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "uid INTEGER NOT NULL,grant_json TEXT NOT NULL,deadline REAL NOT NULL,boot_id TEXT NOT NULL,"
    "phase TEXT NOT NULL DEFAULT 'granted',exec_id TEXT,exec_dispatched INTEGER NOT NULL DEFAULT 0,"
    "armed_deadline REAL,exit_code INTEGER,revoked INTEGER NOT NULL DEFAULT 0,"
    "provisional TEXT NOT NULL DEFAULT '{}',capture TEXT,artifacts TEXT NOT NULL DEFAULT '{}',"
    "outcome_report TEXT,UNIQUE(worker_id,ordinal),UNIQUE(worker_id,uid))"
)
INTENT_SCHEMA = (
    "CREATE TABLE intents(worker_id TEXT NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,"
    "delivery_key TEXT NOT NULL,child_id TEXT,status TEXT NOT NULL DEFAULT 'pending',"
    "PRIMARY KEY(worker_id,request_id))"
)


class RetainedScene:
    """Literal already-captured rows, not an imitation of the canonical service."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.worker = "original-worker"
        self.runtime_config = {"state_directory": str(self.root)}
        directory = self.root / "children"
        directory.mkdir()
        self.path = directory / "children.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute(CHILD_SCHEMA)
        self.db.execute(INTENT_SCHEMA)
        self.expected = {}
        for name, ordinal, code, outcome in (
            ("left", 0, 0, "completed"), ("right", 1, 2, "failed"),
        ):
            child_id = "child_" + str(ordinal + 1) * 32
            instructions = "PRIVATE_INSTRUCTION_" + name
            binding = {
                "schema_version": 1, "child_id": child_id, "worker_id": self.worker,
                "root_attempt_id": "attempt-1", "parent_attempt_id": "attempt-1",
                "task_id": "task-1", "requirements_revision": 1, "base": "a" * 40,
                "grant_digest": str(ordinal + 3) * 64, "instructions_digest": sha(instructions),
                "allocated_seconds": 90, "max_turns": 2,
            }
            report = {"request_key": "outcome-" + name, "outcome": outcome,
                      "summary": "PRIVATE_REPORT_" + name}
            report["payload_digest"] = sha(canonical(report))
            artifacts = {kind: sha(name + ":" + kind) for kind in ("patch", "log", "manifest")}
            manifest = {
                "child_id": child_id, "worker_id": self.worker, "uid": 60000 + ordinal,
                "exec_id": "exec-" + name, "exec_start_dispatched": True,
                "deadline_monotonic": 190.0 + ordinal, "armed_deadline_monotonic": 189.0 + ordinal,
                "work_outcome": {"report": report},
            }
            self.expected[name] = {"binding": binding, "manifest": manifest, "artifacts": artifacts}
            grant = {"schema_version": 1, "binding": binding, "instructions": instructions,
                     "launch_allowed": True, "remaining_seconds": 90}
            payload = {"instructions": instructions, "seconds": 90, "max_turns": 2,
                       "capabilities": ["python"]}
            self.db.execute(
                "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id,"
                "phase,exec_id,exec_dispatched,armed_deadline,exit_code,revoked,capture,artifacts,outcome_report)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (child_id, self.worker, ordinal, 60000 + ordinal, canonical(grant),
                 manifest["deadline_monotonic"], "original-boot", "completed", manifest["exec_id"],
                 1, manifest["armed_deadline_monotonic"], code, 1, canonical({"captured": True}),
                 canonical(artifacts), canonical(report)),
            )
            delivery = "openjules-child:" + sha(canonical([self.worker, "attempt-1", "spawn-" + name]))
            self.db.execute("INSERT INTO intents VALUES(?,?,?,?,?,?)",
                            (self.worker, "spawn-" + name, canonical(payload), delivery, child_id, "bound"))
        self.db.commit()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def read(self):
        return trial.failed_child_rows(self.runtime_config, self.worker, self.expected)


def payload(actor, results, initial):
    user = (trial.REQUIREMENTS if actor == "root" else
            "CHILD_" + actor + ": change only " + actor + ".txt") + "\n" + trial.ROOT_RULES
    names = trial.BASE_TOOLS | (trial.CHILD_TOOLS if actor == "root" else
                               {"report_outcome"} if initial else set())
    return {"model": trial.MODEL_NAME, "max_tokens": 1024, "stream": False, "tool_choice": "auto",
            "tools": [{"type": "function", "function": {"name": name}} for name in sorted(names)],
            "messages": [{"role": "system", "content": "Deterministic test context"},
                         {"role": "user", "content": user}, *results]}


def post(fixture, actor, results):
    request = Request(fixture.endpoint, data=json.dumps(payload(
        actor, results, fixture.initial_outcomes,
    )).encode("utf-8"), headers={"Content-Type": "application/json",
                                "Authorization": "Bearer " + fixture.token}, method="POST")
    with urlopen(request, timeout=5) as response:
        message = json.loads(response.read())["choices"][0]["message"]
    call = message["tool_calls"][0]
    return call["function"]["name"], json.loads(call["function"]["arguments"])


def result(actor, ordinal, value):
    return {"role": "tool", "tool_call_id": f"{actor}-{ordinal}",
            "content": value if isinstance(value, str) else json.dumps(value)}


def drive_fixture(initial=False, right_outcome=None):
    """Exercise the real HTTP fixture's sequence without pretending to run tools."""
    with trial.ChildrenFixture(initial, right_outcome=right_outcome) as fixture:
        ids = {"left": "child_" + "1" * 32, "right": "child_" + "2" * 32}
        root_results, replies = [], {"root": [], "left": [], "right": []}
        replies["root"].append(post(fixture, "root", root_results))
        root_results.append(result("root", 1, {"child_id": ids["left"]}))
        replies["root"].append(post(fixture, "root", root_results))
        root_results.append(result("root", 2, {"child_id": ids["right"]}))
        held, errors = [], []

        def third():
            try:
                held.append(post(fixture, "root", root_results))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=third)
        thread.start()
        try:
            if not fixture.two_spawned.wait(2):
                raise AssertionError("fixture did not reach its real HTTP response gate")
            if not thread.is_alive() or held:
                raise AssertionError("fixture response escaped its original observation gate")
            for name in ("left", "right"):
                first = post(fixture, name, [])
                second = post(fixture, name, [result(name, 1, "old\n")])
                replies[name] = [first, second]
        finally:
            fixture.release_root.set()
            thread.join(5)
        if thread.is_alive() or errors or len(held) != 1:
            raise AssertionError("bounded fixture request did not complete")
        replies["root"].extend(held)
        patches = {"left": "diff --git a/left.txt b/left.txt\n+left implemented\n",
                   "right": "" if initial else "diff --git a/right.txt b/right.txt\n+right implemented\n"}
        for name, turn in (("left", 3), ("right", 4)):
            content = patches[name]
            page = {"child_id": ids[name], "kind": "patch", "final": False, "offset": 0,
                    "total_bytes": len(content.encode()), "content": content,
                    "content_sha256": sha(content), "artifact_sha256": sha(content)}
            root_results.append(result("root", turn, page))
            replies["root"].append(post(fixture, "root", root_results))
        if initial:
            for name, turn in (("left", 5), ("right", 6)):
                status = {"child_id": ids[name], "final": False,
                          "initial_outcome": {"canonical_committed": False, "release_authorized": False,
                                              "report": {"outcome": "completed" if name == "left" else fixture.right_outcome,
                                                         "summary": fixture.outcome_summaries[name]}}}
                root_results.append(result("root", turn, status))
                replies["root"].append(post(fixture, "root", root_results))
        marker = ("ROOT_PRESERVED_FAILURE_AND_COMPLETED_WORK" if right_outcome == "failed" else
                  "ROOT_PRESERVED_DECLINE_AND_COMPLETED_WORK" if initial else "ROOT_COMPOSED_BOTH_CHILD_PATCHES")
        root_results.append(result("root", fixture.compose_turn,
                                   {"state": "exited", "exit_code": 0, "output": marker}))
        replies["root"].append(post(fixture, "root", root_results))
        if fixture.errors:
            raise AssertionError("fixture returned an unexpected HTTP error")
        return {"replies": replies, "requests": deepcopy(fixture.requests),
                "outcomes": deepcopy(fixture.outcomes), "summaries": dict(fixture.outcome_summaries),
                "root_turns": fixture.root_turns, "child_turns": dict(fixture.child_turns)}


class FailedChildOutcomeTrialTests(unittest.TestCase):
    def test_omitted_and_explicit_outcome_selection(self):
        self.assertIsNone(trial.selected_right_outcome(False, None))
        self.assertEqual(trial.selected_right_outcome(True, None), "declined")
        self.assertEqual(trial.selected_right_outcome(True, "declined"), "declined")
        self.assertEqual(trial.selected_right_outcome(True, "failed"), "failed")
        for initial, value in ((False, "declined"), (False, "failed"), (True, "completed"),
                               (True, False), (True, []), (True, {})):
            with self.subTest(initial=initial, value=value):
                with patch.object(trial.ModelFixture, "__init__", side_effect=AssertionError("unexpected effect")) as effect:
                    with self.assertRaisesRegex(AssertionError, "^an explicit right outcome requires"):
                        trial.ChildrenFixture(initial, right_outcome=value)
                    effect.assert_not_called()

    def test_invalid_programmatic_and_cli_mode_refuses_before_effects(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "never-created"
            for value in ("declined", "failed"):
                with self.subTest(value=value):
                    with patch.object(trial, "Processes", side_effect=AssertionError("unexpected effect")) as effect:
                        with self.assertRaisesRegex(AssertionError, "^an explicit right outcome requires"):
                            trial.run(SimpleNamespace(initial_outcomes=False, right_outcome=value, work_dir=root))
                        effect.assert_not_called()
                    self.assertFalse(root.exists())
                    argv = ["check_children_stack.py", "--polyphony-checkout", "/not-read/polyphony",
                            "--openjules-checkout", "/not-read/openjules", "--image", "not-read",
                            "--work-dir", str(root), "--right-outcome", value]
                    with patch.object(sys, "argv", argv), patch.object(trial, "run") as run, \
                            patch.object(Path, "resolve", side_effect=AssertionError("unexpected path read")) as resolve, \
                            redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            trial.main()
                        self.assertEqual(raised.exception.code, 2)
                        run.assert_not_called()
                        resolve.assert_not_called()

    def test_original_http_default_and_initial_sequences_are_preserved(self):
        default = drive_fixture()
        initial = drive_fixture(True)
        explicit = drive_fixture(True, "declined")
        self.assertEqual((len(default["requests"]), default["root_turns"], default["child_turns"]),
                         (10, 6, {"left": 2, "right": 2}))
        self.assertEqual(default["replies"]["right"][1],
                         ("finish", {"summary": "Completed right; request root review"}))
        self.assertIn("ROOT_COMPOSED_BOTH_CHILD_PATCHES", default["replies"]["root"][-2][1]["argv"][-1])
        self.assertNotIn("ROOT_COMPLETED_DECLINED_RIGHT", default["replies"]["root"][-2][1]["argv"][-1])
        self.assertEqual(default["replies"]["root"][-1],
                         ("finish", {"summary": "Composed both child patches and passed combined tests; request independent review"}))
        self.assertEqual(len(initial["requests"]), 12)
        self.assertEqual(initial["replies"], explicit["replies"])
        self.assertEqual(initial["summaries"], {
            "left": "Completed left; request root review",
            "right": "I decline the right change. Preserve this private decision: b\u00e5de kritik och val.",
        })
        self.assertEqual(initial["replies"]["right"][1],
                         ("report_outcome", {"outcome": "declined", "summary": initial["summaries"]["right"]}))
        script = initial["replies"]["root"][-2][1]["argv"][-1]
        self.assertIn("ROOT_COMPLETED_DECLINED_RIGHT", script)
        self.assertIn("ROOT_PRESERVED_DECLINE_AND_COMPLETED_WORK", script)
        self.assertNotIn("ROOT_PRESERVED_FAILURE", script)
        self.assertEqual(initial["replies"]["root"][-1][1]["summary"],
                         "Preserved the right child's decline, composed left and completed right as root; passed combined tests; request independent review")

    def test_actual_http_failed_sequence_keeps_private_report_and_parent_composition(self):
        observed = drive_fixture(True, "failed")
        self.assertEqual((len(observed["requests"]), observed["root_turns"], observed["child_turns"]),
                         (12, 8, {"left": 2, "right": 2}))
        self.assertEqual(observed["replies"]["right"], [
            ("read_file", {"path": "right.txt"}),
            ("report_outcome", {"outcome": "failed", "summary": trial.FAILED_OUTCOME_SUMMARY}),
        ])
        self.assertEqual(observed["outcomes"]["right"]["report"],
                         {"outcome": "failed", "summary": trial.FAILED_OUTCOME_SUMMARY})
        self.assertIs(observed["outcomes"]["right"]["canonical_committed"], False)
        script = observed["replies"]["root"][-2][1]["argv"][-1]
        self.assertIn("assert pathlib.Path('right.txt').read_text()=='old\\n'", script)
        self.assertIn("pathlib.Path('right.txt').write_text('right implemented\\n')", script)
        self.assertIn("ROOT_COMPLETED_FAILED_RIGHT", script)
        self.assertIn("ROOT_PRESERVED_FAILURE_AND_COMPLETED_WORK", script)
        self.assertNotIn(trial.FAILED_OUTCOME_SUMMARY, script)
        self.assertEqual(observed["replies"]["root"][-1][1]["summary"],
                         "Preserved the right child's failure, composed left and completed right as root; passed combined tests; request independent review")

    def test_failed_runner_event_requires_exact_single_ack_and_no_verification(self):
        line = '{"event": "child_report_retained", "outcome": "failed"}'
        content = "ordinary bounded guest output\n" + line + "\n"
        self.assertEqual(trial.failed_outcome_log(content), {
            "retained_failed_events": 1, "configured_verification": False,
            "log_sha256": sha(content), "log_utf8_bytes": len(content.encode()),
        })
        invalid = [
            "", json.dumps(line), line + "\n" + line, line.replace("failed", "declined"),
            '{"event":"child_report_retained","outcome":"failed","extra":true}',
            line + '\n{"event":"configured_verification","state":"exited","exit_code":0}',
            line + "\n" * 513, "x" * 262145,
        ]
        for value in invalid:
            with self.subTest(value=value[:80]), self.assertRaises(AssertionError):
                trial.failed_outcome_log(value)

    def test_failed_scene_diagnostics_redact_private_canary_before_export(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "trial"
            args = SimpleNamespace(initial_outcomes=True, right_outcome="failed",
                                   work_dir=root, image="invalid")
            def reject_image(*_args, **_kwargs):
                (root / "installation.log").write_text("PRIVATE: " + trial.FAILED_OUTCOME_SUMMARY + "\n")
                return "invalid"
            with patch.object(subprocess, "check_output", side_effect=reject_image), \
                    redirect_stderr(io.StringIO()), self.assertRaisesRegex(AssertionError, "immutable managed-agent image"):
                trial.run(args)
            diagnostic = (root / "diagnostics.json").read_text()
            self.assertNotIn(trial.FAILED_OUTCOME_SUMMARY, diagnostic)
            self.assertIn("[redacted]", diagnostic)
            self.assertFalse(json.loads(diagnostic)["passed"])
            self.assertFalse((root / "report.json").exists())

    def test_literal_wal_rows_are_read_only_and_reopen_retains_exact_projection(self):
        with TemporaryDirectory() as temporary:
            scene = RetainedScene(temporary)
            try:
                paths = [scene.path, Path(str(scene.path) + "-wal")]
                before = {str(path): path.read_bytes() for path in paths}
                observed = scene.read()
                self.assertEqual({str(path): path.read_bytes() for path in paths}, before)
                self.assertEqual([row["exit_code"] for row in observed], [0, 2])
                self.assertEqual([row["phase"] for row in observed], ["completed", "completed"])
                self.assertEqual([row["uid"] for row in observed], [60000, 60001])
                public = json.dumps(observed)
                for private in ("PRIVATE_INSTRUCTION_left", "PRIVATE_INSTRUCTION_right",
                                "PRIVATE_REPORT_left", "PRIVATE_REPORT_right", "original-boot"):
                    self.assertNotIn(private, public)
                scene.close()
                self.assertEqual(scene.read(), observed)
            finally:
                scene.close()

    def test_wrong_exit_identity_or_cutoff_refuses_without_rewriting_rows(self):
        mutations = [
            ("exit_code", 0), ("exit_code", None), ("exec_id", "another-exec"),
            ("uid", 60002), ("phase", "exec_returned"), ("exec_dispatched", 0),
            ("deadline", 300.0), ("armed_deadline", 300.0), ("capture", None),
        ]
        for column, value in mutations:
            with self.subTest(column=column, value=value), TemporaryDirectory() as temporary:
                scene = RetainedScene(temporary)
                try:
                    scene.db.execute(f"UPDATE children SET {column}=? WHERE ordinal=1", (value,))
                    scene.db.commit()
                    before = scene.db.execute("SELECT * FROM children ORDER BY ordinal").fetchall()
                    with self.assertRaises(AssertionError):
                        scene.read()
                    self.assertEqual(scene.db.execute("SELECT * FROM children ORDER BY ordinal").fetchall(), before)
                finally:
                    scene.close()

    def test_missing_extra_and_foreign_rows_cannot_hide_behind_worker_filter(self):
        changes = [
            "DELETE FROM children WHERE ordinal=1",
            "DELETE FROM intents WHERE child_id=(SELECT child_id FROM children WHERE ordinal=1)",
            "UPDATE children SET worker_id='foreign' WHERE ordinal=1",
            "UPDATE intents SET worker_id='foreign' WHERE child_id=(SELECT child_id FROM children WHERE ordinal=1)",
            "INSERT INTO intents VALUES('foreign','extra','{}','key','child_extra','bound')",
            "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id) "
            "VALUES('child_extra','foreign',2,60002,'{}',300,'foreign-boot')",
        ]
        for change in changes:
            with self.subTest(change=change), TemporaryDirectory() as temporary:
                scene = RetainedScene(temporary)
                try:
                    scene.db.execute(change)
                    scene.db.commit()
                    with self.assertRaises(AssertionError):
                        scene.read()
                finally:
                    scene.close()

    def test_coherent_private_report_and_grant_rewrites_still_refuse_original_capture(self):
        for target in ("report", "grant"):
            with self.subTest(target=target), TemporaryDirectory() as temporary:
                scene = RetainedScene(temporary)
                try:
                    if target == "report":
                        report = deepcopy(scene.expected["right"]["manifest"]["work_outcome"]["report"])
                        report["outcome"], report["summary"] = "completed", "coherently changed"
                        report["payload_digest"] = sha(canonical({key: report[key] for key in ("request_key", "outcome", "summary")}))
                        scene.db.execute("UPDATE children SET outcome_report=? WHERE ordinal=1", (canonical(report),))
                    else:
                        grant = json.loads(scene.db.execute("SELECT grant_json FROM children WHERE ordinal=1").fetchone()[0])
                        grant["instructions"] = "coherently changed instruction"
                        grant["binding"]["instructions_digest"] = sha(grant["instructions"])
                        scene.db.execute("UPDATE children SET grant_json=? WHERE ordinal=1", (canonical(grant),))
                    scene.db.commit()
                    with self.assertRaises(AssertionError):
                        scene.read()
                finally:
                    scene.close()

    def test_reconnect_projection_detects_changed_original_boot_or_intent_key(self):
        for change in (
            "UPDATE children SET boot_id='new-boot' WHERE ordinal=1",
            "UPDATE intents SET request_id='new-request' WHERE request_id='spawn-right'",
            "UPDATE intents SET delivery_key='new-delivery' WHERE request_id='spawn-right'",
        ):
            with self.subTest(change=change), TemporaryDirectory() as temporary:
                scene = RetainedScene(temporary)
                try:
                    original = scene.read()
                    scene.db.execute(change)
                    scene.db.commit()
                    current = scene.read()
                    self.assertNotEqual(current, original)
                finally:
                    scene.close()

    def test_noncanonical_or_malformed_retained_objects_refuse(self):
        valid = '{"fixed":1}'
        self.assertEqual(trial.retained_object(valid), {"fixed": 1})
        for value in (None, "[]", "{", '{"fixed":1,"fixed":1}', '{"fixed":NaN}',
                      '{"fixed": 1}', '{"fixed":"\ud800"}', " " * 262145):
            with self.subTest(value=repr(value)[:80]), self.assertRaises(AssertionError):
                trial.retained_object(value)

    @unittest.skipUnless(hasattr(os, "killpg"), "requires owned POSIX process groups")
    def test_success_report_requires_actual_handled_manager_and_original_owner(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            processes = trial.Processes(root)
            marker = root / "ready"
            code = ("import pathlib,signal,time\n"
                    "def stop(*args): raise SystemExit(130)\n"
                    "signal.signal(signal.SIGTERM,stop)\n"
                    "pathlib.Path(" + repr(str(marker)) + ").write_text('ready')\n"
                    "while True: time.sleep(0.01)\n")
            process = processes.start("installation", [sys.executable, "-I", "-c", code], root, {})
            try:
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("owned manager fixture exited before readiness")
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                with self.assertRaisesRegex(AssertionError, "original installation process ownership"):
                    trial.stop_failed_installation(processes, object())
                self.assertIsNone(process.poll())
                report = root / "report.json"
                trial.publish_failed_report(report, {"passed": True}, processes, process)
                self.assertEqual(process.returncode, 130)
                self.assertEqual(json.loads(report.read_text()), {"passed": True})
                self.assertFalse(processes.running)
            finally:
                processes.close()

    @unittest.skipUnless(hasattr(os, "killpg"), "requires owned POSIX process groups")
    def test_unhandled_or_killed_manager_cannot_publish_success(self):
        for killed in (False, True):
            with self.subTest(killed=killed), TemporaryDirectory() as temporary:
                root = Path(temporary)
                processes = trial.Processes(root)
                code = "import time; time.sleep(30)" if killed else "raise SystemExit(0)"
                process = processes.start("installation", [sys.executable, "-I", "-c", code], root, {})
                try:
                    if killed:
                        os.killpg(process.pid, signal.SIGKILL)
                    self.assertEqual(process.wait(timeout=3), -9 if killed else 0)
                    report = root / "report.json"
                    with self.assertRaisesRegex(AssertionError, "handled status 130"):
                        trial.publish_failed_report(report, {"passed": True}, processes, process)
                    self.assertFalse(report.exists())
                    self.assertFalse(processes.running)
                finally:
                    processes.close()


if __name__ == "__main__":
    unittest.main()
