"""Cold installed-trial oracles over literal SQLite and deterministic fixtures.

These tests do not execute Polyphony, an OpenJules service or Docker. The installed
trial must independently establish those effects with the same source and pins.
"""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_cold_children_stack as cold
    import operational_measurement as measurement
finally:
    sys.path.pop(0)


# Literal writer schemas from the producer, independent of the reader constants.
SCHEMA = (
    "CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT NOT NULL,"
    "policy_digest TEXT NOT NULL,context TEXT,closed INTEGER NOT NULL DEFAULT 0,"
    "close_reason TEXT,close_delivered INTEGER NOT NULL DEFAULT 0,retire INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE intents(worker_id TEXT NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,"
    "delivery_key TEXT NOT NULL,child_id TEXT,status TEXT NOT NULL DEFAULT 'pending',"
    "PRIMARY KEY(worker_id,request_id))",
    "CREATE TABLE children(child_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "uid INTEGER NOT NULL,grant_json TEXT NOT NULL,deadline REAL NOT NULL,boot_id TEXT NOT NULL,"
    "phase TEXT NOT NULL DEFAULT 'granted',exec_id TEXT,exec_dispatched INTEGER NOT NULL DEFAULT 0,"
    "armed_deadline REAL,exit_code INTEGER,revoked INTEGER NOT NULL DEFAULT 0,"
    "provisional TEXT NOT NULL DEFAULT '{}',capture TEXT,artifacts TEXT NOT NULL DEFAULT '{}',"
    "UNIQUE(worker_id,ordinal),UNIQUE(worker_id,uid))",
    "CREATE TABLE cold_sessions(worker_id TEXT NOT NULL,session_id TEXT NOT NULL,start_request_id TEXT NOT NULL,"
    "binding TEXT NOT NULL,binding_digest TEXT NOT NULL,closure TEXT,close_reason TEXT,"
    "PRIMARY KEY(worker_id,session_id),UNIQUE(worker_id,start_request_id))",
    "CREATE TABLE cold_rounds(worker_id TEXT NOT NULL,session_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "request_id TEXT NOT NULL,request_json TEXT NOT NULL,request_digest TEXT NOT NULL,"
    "grant_payload TEXT NOT NULL,instructions_digest TEXT NOT NULL,predecessor TEXT,predecessor_digest TEXT,"
    "cutoff REAL NOT NULL,boot_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT,child_id TEXT,grant_digest TEXT,"
    "model_closed INTEGER NOT NULL DEFAULT 0,fence_established INTEGER NOT NULL DEFAULT 0,"
    "PRIMARY KEY(worker_id,session_id,ordinal),UNIQUE(worker_id,request_id),UNIQUE(worker_id,child_id))",
)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def value_hash(value):
    return sha(encoded(value))


class Scene:
    """Named local evidence fixture; final() does not simulate canonical receipt."""

    def __init__(self):
        self.scope = {"deployment_id": "fixture-deployment", "repository": "github:github.com/fixture/repo"}
        self.profile, self.worker, self.task, self.attempt = "fixture-cold", "worker-1", "task-1", "attempt-1"
        self.keys = ("start-1", "followup-1")
        self.ids = {"left": "child_" + "1" * 32, "right": "child_" + "2" * 32}
        self.session = "session_" + value_hash([
            "openjules-cold-clarification-v1", self.scope["deployment_id"], self.scope["repository"],
            self.profile, self.worker, self.attempt, 1, self.keys[0],
        ])
        spec = {
            **self.scope, "profile": self.profile, "slot_id": "slot-1", "generation": 1,
            "launch_key": "root-launch-1", "schema_version": 1,
        }
        root = {
            "worker_id": self.worker, "spec": spec, "attempt_id": self.attempt,
            "task_id": self.task, "requirements_revision": 1, "runtime_principal": "runtime-1",
            "profile_digest": "b" * 64, "base": "f" * 40, "deadline": 1180.0,
            "monotonic_deadline": 180.0, "boot_id": "boot-1",
        }
        binding = {
            "schema_version": 1, "protocol": "cold-clarification-v1", "session_id": self.session,
            **self.scope, "profile": self.profile, "worker_id": self.worker,
            "root_attempt_id": self.attempt, "parent_attempt_id": self.attempt, "task_id": self.task,
            "requirements_revision": 1, "runtime_principal": "runtime-1", "profile_digest": "b" * 64,
            "child_policy_digest": "c" * 64, "start_request_id": self.keys[0],
            "initial_instructions": cold.INITIAL, "capabilities": ["python"], "max_rounds": 2,
        }
        self.rows = {
            "worker": {
                "worker_id": self.worker, "spec": encoded(spec), "policy_digest": "c" * 64, "context": encoded(root),
                "closed": 0, "close_reason": None, "close_delivered": 0, "retire": 0,
            },
            "cold_sessions": [{
                "worker_id": self.worker, "session_id": self.session, "start_request_id": self.keys[0],
                "binding": encoded(binding), "binding_digest": value_hash(binding),
                "closure": "completed", "close_reason": None,
            }],
            "cold_rounds": [], "children": [], "intents": [],
        }
        self.permissions, self.instructions, self.rounds = [], {}, []
        predecessor = None
        for ordinal, actor in enumerate(("left", "right"), 1):
            key, child = self.keys[ordinal - 1], self.ids[actor]
            envelope = {
                "schema_version": 1, "protocol": "openjules-cold-clarification-v1",
                "session_id": self.session, "logical_round": ordinal, "initial_instructions": cold.INITIAL,
                "followup": None if ordinal == 1 else cold.FOLLOWUP, "predecessor": predecessor,
            }
            instructions = encoded(envelope)
            self.instructions[actor] = instructions
            grant_digest = ("a" if ordinal == 1 else "d") * 64
            child_binding = {
                **self.scope, "profile": self.profile, "worker_id": self.worker, "task_id": self.task,
                "root_attempt_id": self.attempt, "parent_attempt_id": self.attempt,
                "requirements_revision": 1, "runtime_principal": "runtime-1", "profile_digest": "b" * 64,
                "grant_digest": grant_digest, "instructions_digest": sha(instructions), "allocated_seconds": 90,
                "max_turns": 2, "capabilities": ["python"], "launch_key": "polyphony-child:" + child,
                "base": "f" * 40, "child_id": child, "deadline": 1110.0 if ordinal == 1 else 1120.0,
            }
            grant_digest = value_hash({key: value for key, value in child_binding.items() if key != "grant_digest"})
            child_binding["grant_digest"] = grant_digest
            request = {
                "schema_version": 1, "request_id": key,
                "operation": "session_start" if ordinal == 1 else "session_followup",
                "instructions": cold.INITIAL if ordinal == 1 else cold.FOLLOWUP, "seconds": 90, "max_turns": 2,
                **({"capabilities": ["python"]} if ordinal == 1 else {
                    "session_id": self.session, "predecessor_digest": value_hash(predecessor),
                }),
            }
            payload = {"instructions": instructions, "seconds": 90, "max_turns": 2, "capabilities": ["python"]}
            report = {
                "request_key": "outcome-" + str(ordinal),
                "outcome": "needs_clarification" if ordinal == 1 else "completed",
                "summary": cold.CLARIFICATION if ordinal == 1 else cold.COMPLETION,
            }
            report["payload_digest"] = value_hash(report)
            outcome = {
                "schema_version": 1, "work_turn_id": value_hash(["polyphony-child-work-v1", child, grant_digest, 1]),
                "ordinal": 1, "child_id": child, "root_attempt_id": self.attempt, "parent_attempt_id": self.attempt,
                "task_id": self.task, "requirements_revision": 1, "grant_digest": grant_digest,
                "instructions_digest": sha(instructions), "source": "child_report_via_bound_runtime",
                "report": report, "canonical_committed": False, "release_authorized": False,
            }
            self.rows["cold_rounds"].append({
                "worker_id": self.worker, "session_id": self.session, "ordinal": ordinal, "request_id": key,
                "request_json": encoded(request), "request_digest": value_hash(request),
                "grant_payload": encoded(payload), "instructions_digest": sha(instructions),
                "predecessor": None if predecessor is None else encoded(predecessor),
                "predecessor_digest": None if predecessor is None else value_hash(predecessor),
                "cutoff": 180.0 if ordinal == 1 else 109.0, "boot_id": "boot-1",
                "state": "linked", "reason": None, "child_id": child, "grant_digest": grant_digest,
                "model_closed": 1 if ordinal == 1 else 0, "fence_established": 1 if ordinal == 1 else 0,
            })
            self.rows["children"].append({
                "child_id": child, "worker_id": self.worker, "ordinal": ordinal - 1, "uid": 59999 + ordinal,
                "grant_json": encoded({"binding": child_binding, "instructions": instructions}),
                "deadline": 110.0 if ordinal == 1 else 120.0, "boot_id": "boot-1", "phase": "exec_returned",
                "exec_id": "exec-" + str(ordinal), "exec_dispatched": 1, "armed_deadline": 109.0,
                "exit_code": 0, "revoked": 0, "provisional": "{}", "capture": None,
                "artifacts": "{}", "outcome_report": encoded(report),
            })
            self.rows["intents"].append({
                "worker_id": self.worker, "request_id": key, "payload": encoded(payload),
                "delivery_key": "openjules-child:" + value_hash([self.worker, self.attempt, key]),
                "child_id": child, "status": "bound",
            })
            self.permissions.append({"child": {"id": child, "binding": child_binding}})
            self.rounds.append({
                "logical_round": ordinal, "request_id": key, "child_id": child, "grant_digest": grant_digest,
                "instructions_digest": sha(instructions), "exec_id": "exec-" + str(ordinal),
                "runtime_state": "exec_returned", "exit_code": 0, "initial_outcome": outcome,
                "evidence": "provisional", "artifacts": None,
            })
            if ordinal == 1:
                predecessor = {"exec_id": "exec-1", "work_outcome": deepcopy(outcome)}
        common = {
            "schema_version": 1, "request_id": "status-1", "ok": True, "protocol": "cold-clarification-v1",
            "session_id": self.session, "root_attempt_id": self.attempt, "reason": None,
            "predecessor_digest": value_hash(predecessor), "all_child_receipts_final": False,
            "canonical_session": False, "release_authorized": False,
        }
        first = common | {
            "phase": "awaiting_followup", "followup_request_id": None, "model_fence": "open",
            "logical_outcome": None, "rounds": deepcopy(self.rounds[:1]),
        }
        second = common | {
            "phase": "closed", "followup_request_id": self.keys[1], "model_fence": "established",
            "logical_outcome": "completed", "rounds": deepcopy(self.rounds),
        }
        self.model = SimpleNamespace(
            session_id=self.session, first_status=first, second_status=second,
            child_ids=deepcopy(self.ids), instructions=deepcopy(self.instructions),
        )

    def correlate(self, rows=None, *, final=False):
        return cold.correlation(
            self.rows if rows is None else rows, self.model, self.scope, self.profile,
            self.task, self.attempt, self.permissions, final=final,
        )

    def final(self):
        rows = deepcopy(self.rows)
        for child in rows["children"]:
            child.update(phase="completed", capture='{"fixture_terminal":true}', artifacts='{"fixture_capture":true}')
        return rows

    def database(self, path):
        database = sqlite3.connect(path)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA wal_autocheckpoint=0")
        for sql in SCHEMA:
            database.execute(sql)
        database.execute("ALTER TABLE children ADD COLUMN outcome_report TEXT")
        database.execute("PRAGMA user_version=1")
        for name in ("worker", "cold_sessions", "cold_rounds", "children", "intents"):
            rows = [self.rows[name]] if name == "worker" else self.rows[name]
            table = "workers" if name == "worker" else name
            for row in rows:
                database.execute(
                    "INSERT INTO " + table + "(" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")",
                    tuple(row.values()),
                )
        database.commit()
        return database


def database_snapshot(database):
    names = [row[0] for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {
        "marker": database.execute("PRAGMA user_version").fetchone()[0],
        "schema": database.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(),
        "rows": {name: database.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall() for name in names},
    }


def patch_page(scene, actor):
    content = (
        "diff --git a/" + actor + ".txt b/" + actor + ".txt\n"
        "--- a/" + actor + ".txt\n+++ b/" + actor + ".txt\n@@ -1 +1 @@\n-old\n+"
        + (cold.PARTIAL if actor == "left" else "right implemented\n")
    )
    return {
        "ok": True, "child_id": scene.ids[actor], "kind": "patch", "final": False, "offset": 0,
        "content": content, "total_bytes": len(content.encode("utf-8")),
        "content_sha256": sha(content), "artifact_sha256": sha(content),
    }


def model_payload(initial, results, tools):
    return {
        "model": cold.MODEL_NAME, "max_tokens": 1024, "stream": False, "tool_choice": "auto",
        "messages": [{"role": "system", "content": "named fixture"}, {"role": "user", "content": initial}]
        + results,
        "tools": [{"type": "function", "function": {"name": name}} for name in sorted(tools)],
    }


def result(actor, turn, content):
    return {"role": "tool", "tool_call_id": actor + "-" + str(turn), "content": content}


class ColdJournalTests(unittest.TestCase):
    def test_literal_committed_wal_is_read_without_mutation_and_final_projection_is_frozen(self):
        scene = Scene()
        with TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "children.sqlite3"
            database = scene.database(path)
            try:
                self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
                before = database_snapshot(database)
                rows = cold.read_cold_rows(path, scene.worker)
                self.assertEqual(rows, scene.rows)
                self.assertEqual(database_snapshot(database), before)
                original = scene.correlate(rows)
                self.assertEqual(original["report"]["task_allocated_seconds"], 360)
                self.assertEqual([row["canonical_allocated_seconds"] for row in original["report"]["physical_rounds"]], [90, 90])
                self.assertEqual([row["uid"] for row in original["report"]["physical_rounds"]], [60000, 60001])
                self.assertNotIn(cold.CLARIFICATION, encoded(original["report"]))
                final = scene.correlate(scene.final(), final=True)
                cold.require_same_correlation(original, final)
                self.assertEqual(database_snapshot(database), before)
            finally:
                database.close()

    def test_missing_changed_and_foreign_optional_history_fail_without_repair(self):
        mutations = (
            "PRAGMA user_version=0",
            "DROP TABLE cold_rounds",
            "ALTER TABLE cold_rounds ADD COLUMN unexpected TEXT",
            "DELETE FROM intents WHERE request_id='followup-1'",
            "UPDATE children SET worker_id='foreign-worker' WHERE ordinal=1",
            "INSERT INTO intents VALUES('foreign-worker','foreign-key','{}','foreign-delivery',NULL,'pending')",
            "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id)"
            " VALUES('foreign-child','foreign-worker',0,60000,'{}',100,'foreign-boot')",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                scene = Scene()
                path = Path(temporary).resolve() / "children.sqlite3"
                database = scene.database(path)
                try:
                    database.execute(mutation)
                    database.commit()
                    before = database_snapshot(database)
                    with self.assertRaises(AssertionError):
                        cold.read_cold_rows(path, scene.worker)
                    self.assertEqual(database_snapshot(database), before)
                finally:
                    database.close()

    def test_canonical_launch_and_runtime_delivery_are_distinct_immutable_namespaces(self):
        scene = Scene()
        scene.correlate()
        for ordinal in (0, 1):
            with self.subTest(ordinal=ordinal, changed="delivery"):
                rows = deepcopy(scene.rows)
                launch = scene.permissions[ordinal]["child"]["binding"]["launch_key"]
                self.assertNotEqual(rows["intents"][ordinal]["delivery_key"], launch)
                rows["intents"][ordinal]["delivery_key"] = launch
                with self.assertRaisesRegex(AssertionError, "charged delivery"):
                    scene.correlate(rows)
            with self.subTest(ordinal=ordinal, changed="launch"):
                changed = Scene()
                binding = changed.permissions[ordinal]["child"]["binding"]
                binding["launch_key"] = changed.rows["intents"][ordinal]["delivery_key"]
                grant = json.loads(changed.rows["children"][ordinal]["grant_json"])
                grant["binding"] = deepcopy(binding)
                changed.rows["children"][ordinal]["grant_json"] = encoded(grant)
                with self.assertRaisesRegex(AssertionError, "charged delivery"):
                    changed.correlate()

    def test_successor_cannot_extend_earlier_arm_or_replace_original_allocation(self):
        scene = Scene()
        original = scene.correlate()
        self.assertLess(109, 180)
        self.assertGreater(120, 109)
        for table, index, key, value in (
            ("cold_rounds", 1, "cutoff", 110.0),
            ("children", 1, "armed_deadline", 110.0),
            ("children", 1, "exec_id", "exec-1"),
            ("children", 1, "uid", 60000),
            ("children", 1, "ordinal", 0),
            ("children", 0, "exit_code", False),
            ("children", 0, "capture", "{}"),
            ("children", 0, "artifacts", '{"patch":"premature"}'),
            ("cold_rounds", 0, "model_closed", 0),
            ("cold_rounds", 0, "fence_established", 0),
        ):
            with self.subTest(table=table, key=key, value=value):
                rows = deepcopy(scene.rows)
                rows[table][index][key] = value
                with self.assertRaises(AssertionError):
                    scene.correlate(rows)
        shorter = scene.final()
        context = json.loads(shorter["worker"]["context"])
        context["monotonic_deadline"] = 175.0
        shorter["worker"]["context"] = encoded(context)
        cold.require_same_correlation(original, scene.correlate(shorter, final=True))
        extended = deepcopy(original)
        extended["private"]["root_context"]["monotonic_deadline"] = 181.0
        with self.assertRaisesRegex(AssertionError, "extended"):
            cold.require_same_correlation(original, extended)

    def test_original_predecessor_request_and_report_survive_finalization(self):
        scene = Scene()
        original = scene.correlate()
        for table, index, key, value in (
            ("cold_rounds", 1, "predecessor", "{}"),
            ("cold_rounds", 1, "predecessor_digest", "0" * 64),
            ("cold_rounds", 1, "request_id", "different-key"),
            ("cold_rounds", 0, "instructions_digest", "0" * 64),
            ("intents", 1, "status", "pending"),
            ("children", 0, "outcome_report", '{"summary":"replacement"}'),
        ):
            with self.subTest(table=table, key=key):
                rows = deepcopy(scene.rows)
                rows[table][index][key] = value
                with self.assertRaises(AssertionError):
                    scene.correlate(rows)
        changed = deepcopy(original)
        changed["private"]["rounds"][1]["request_json"] += " "
        with self.assertRaisesRegex(AssertionError, "changed original"):
            cold.require_same_correlation(original, changed)


class ColdObservationTests(unittest.TestCase):
    def test_effective_context_preserves_exact_envelope_and_repository_suffix(self):
        scene = Scene()
        suffix = "\n\nRoot repository instructions:\n" + cold.ROOT_RULES
        for actor in ("left", "right"):
            raw = scene.instructions[actor]
            envelope, observed = cold.effective_envelope(raw + suffix)
            self.assertEqual(observed, raw)
            self.assertEqual(envelope, json.loads(raw))
        raw = scene.instructions["left"]
        bad = [
            raw + "\n" + suffix,
            json.dumps(json.loads(raw), indent=2) + suffix,
            raw[:-1] + ',"schema_version":1}' + suffix,
            raw + suffix + "unapproved repository instruction",
            '{"schema_version":1,"secret":"\ud800"}' + suffix,
        ]
        for field, value in (("schema_version", True), ("logical_round", True),
                             ("protocol", "other"), ("session_id", "foreign")):
            envelope = json.loads(raw)
            envelope[field] = value
            bad.append(encoded(envelope) + suffix)
        for value in bad:
            with self.subTest(value=repr(value)[:70]), self.assertRaises(AssertionError):
                cold.effective_envelope(value)

    def test_provisional_status_never_grants_canonical_authority_or_final_capture(self):
        scene = Scene()
        cold.returned_status(scene.model.first_status, 1, scene.session)
        cold.returned_status(scene.model.second_status, 2, scene.session)
        for field, value in (
            ("canonical_session", True), ("release_authorized", True),
            ("all_child_receipts_final", True), ("predecessor_digest", "0" * 64),
            ("schema_version", True), ("session_id", "session_" + "0" * 64),
        ):
            with self.subTest(field=field):
                status = deepcopy(scene.model.second_status)
                status[field] = value
                with self.assertRaises(AssertionError):
                    cold.returned_status(status, 2, scene.session)
        for field, value in (("exit_code", False), ("runtime_state", "running"),
                             ("artifacts", {}), ("logical_round", True)):
            with self.subTest(round_field=field):
                status = deepcopy(scene.model.second_status)
                status["rounds"][0][field] = value
                with self.assertRaises(AssertionError):
                    cold.returned_status(status, 2, scene.session)
        changed = deepcopy(scene.model.second_status)
        changed["rounds"][0]["initial_outcome"]["report"]["summary"] = "replacement"
        with self.assertRaises(AssertionError):
            cold.returned_status(changed, 2, scene.session)

    def test_patch_and_semantic_verification_keep_clarification_distinct(self):
        scene = Scene()
        left, right = patch_page(scene, "left"), patch_page(scene, "right")
        self.assertEqual(cold.patch_value(left, scene.ids["left"], "left"), left["content"])
        self.assertEqual(cold.patch_value(right, scene.ids["right"], "right"), right["content"])
        for field, value in (("offset", False), ("total_bytes", 1),
                             ("content_sha256", "0" * 64), ("final", True)):
            with self.subTest(field=field):
                changed = deepcopy(left)
                changed[field] = value
                with self.assertRaises(AssertionError):
                    cold.patch_value(changed, scene.ids["left"], "left")
        verification = {
            "event": "configured_verification", "state": "exited", "exit_code": 0,
            "output": "CHILD_STACK_BOUNDARY_VERIFIED uid=60001",
        }
        cold.verify_child_log("left", '{"event":"explicit_outcome"}\n', left["content"])
        cold.verify_child_log("right", encoded(verification) + "\n", right["content"])
        with self.assertRaisesRegex(AssertionError, "clarification"):
            cold.verify_child_log("left", encoded(verification), left["content"])
        for change in ({"exit_code": False}, {"exit_code": 1}, {"state": "running"}, {"output": "no marker"}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                cold.verify_child_log("right", encoded(verification | change), right["content"])
        for content in ("", encoded(verification) + "\n" + encoded(verification), "\n" * 513, "x" * 262145):
            with self.subTest(length=len(content)), self.assertRaises(AssertionError):
                cold.verify_child_log("right", content, right["content"])

    def test_real_archive_separates_provisional_fresh_and_root_workspaces(self):
        scene = Scene()
        expected = {
            "workspace/run/repo/left.txt": (b"left implemented\n", 65532),
            "workspace/run/repo/right.txt": (b"right implemented\n", 65532),
        }
        for actor, child_id in scene.ids.items():
            prefix = "workspace/children/" + child_id + "/run/repo/"
            uid = 60000 if actor == "left" else 60001
            expected[prefix + "left.txt"] = (cold.PARTIAL.encode() if actor == "left" else b"old\n", uid)
            expected[prefix + "right.txt"] = (b"old\n" if actor == "left" else b"right implemented\n", uid)
            expected[prefix + ".git/HEAD"] = (b"ref: refs/heads/main\n", uid)

        def write_archive(path, entries, symlink=None):
            with tarfile.open(path, "w") as archive:
                for name, (content, uid) in entries.items():
                    member = tarfile.TarInfo(name)
                    member.uid = uid
                    if name == symlink:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "outside"
                        archive.addfile(member)
                    else:
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.tar"
            write_archive(path, expected)
            self.assertEqual(cold.archive_workspaces(path, scene.model), {
                "distinct_child_checkouts": 2, "first_provisional_edit_retained": True,
                "second_started_from_original_base": True, "root_applied_only_second_patch": True,
            })
            second_left = "workspace/children/" + scene.ids["right"] + "/run/repo/left.txt"
            changed = deepcopy(expected)
            changed[second_left] = (cold.PARTIAL.encode(), 60001)
            write_archive(path, changed)
            with self.assertRaises(AssertionError):
                cold.archive_workspaces(path, scene.model)
            changed = deepcopy(expected)
            second_head = "workspace/children/" + scene.ids["right"] + "/run/repo/.git/HEAD"
            changed[second_head] = (expected[second_head][0], 60000)
            write_archive(path, changed)
            with self.assertRaisesRegex(AssertionError, "UID"):
                cold.archive_workspaces(path, scene.model)
            write_archive(path, expected, symlink=second_left)
            with self.assertRaises(AssertionError):
                cold.archive_workspaces(path, scene.model)


class ColdFixtureTests(unittest.TestCase):
    def test_actual_http_fixture_holds_only_after_four_child_calls_and_returns_ten_responses(self):
        from concurrent.futures import ThreadPoolExecutor
        from urllib.request import ProxyHandler, Request, build_opener

        scene = Scene()
        image_hash = "e" * 64
        root_initial = cold.REQUIREMENTS + "\n\nRoot repository instructions:\n" + cold.ROOT_RULES
        root_tools = cold.BASE_TOOLS | cold.CHILD_TOOLS | cold.SESSION_TOOLS
        child_tools = cold.BASE_TOOLS | {"report_outcome"}
        root_results = []

        with cold.ColdFixture(image_hash) as model:
            model.measurements.enrollment_started(1)

            def send(payload):
                opener = build_opener(ProxyHandler({}))
                request = Request(
                    model.endpoint, data=json.dumps(payload).encode(), method="POST",
                    headers={"Authorization": "Bearer " + model.token, "Content-Type": "application/json"},
                )
                with opener.open(request, timeout=5) as response:
                    return json.loads(response.read())["choices"][0]["message"]["tool_calls"][0]["function"]

            def root_call():
                return send(model_payload(root_initial, list(root_results), root_tools))

            def child_calls(actor):
                initial = scene.instructions[actor] + "\n\nRoot repository instructions:\n" + cold.ROOT_RULES
                first = send(model_payload(initial, [], child_tools))
                self.assertEqual(first["name"], "write_file")
                second = send(model_payload(initial, [result(actor, 1, '{"ok":true}')], child_tools))
                self.assertEqual(second["name"], "report_outcome" if actor == "left" else "finish")

            def poll_result(ordinal):
                actor = "left" if ordinal == 1 else "right"
                status = scene.model.first_status if ordinal == 1 else scene.model.second_status
                return encoded({
                    "state": "exited", "exit_code": 0,
                    "output": encoded({"status": status, "patch": patch_page(scene, actor),
                                       "image_child_tools_sha256": image_hash}),
                })

            self.assertEqual(root_call()["name"], "start_child_session")
            child_calls("left")
            root_results.append(result("root", 1, encoded(scene.model.first_status)))
            self.assertEqual(root_call()["name"], "run_command")
            root_results.append(result("root", 2, poll_result(1)))
            followup = root_call()
            self.assertEqual(followup["name"], "continue_child_session")
            self.assertEqual(json.loads(followup["arguments"])["predecessor_digest"],
                             scene.model.first_status["predecessor_digest"])
            child_calls("right")
            root_results.append(result("root", 3, encoded(scene.model.second_status)))
            self.assertEqual(root_call()["name"], "run_command")
            root_results.append(result("root", 4, poll_result(2)))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(root_call)
                try:
                    self.assertTrue(model.two_returned.wait(2))
                    self.assertFalse(future.done())
                    with model.lock:
                        self.assertEqual(model.child_turns, {"left": 2, "right": 2})
                        self.assertEqual(model.root_turns, 5)
                        self.assertEqual(len(model.requests), 9)
                        self.assertFalse(model.errors)
                finally:
                    model.release_root.set()
                compose = future.result(timeout=3)
            self.assertEqual(compose["name"], "run_command")
            command = json.loads(compose["arguments"])["argv"][-1]
            self.assertIn("right implemented", command)
            self.assertNotIn(cold.PARTIAL.rstrip("\n"), command)
            self.assertIn("left implemented", command)
            root_results.append(result("root", 5, encoded({
                "state": "exited", "exit_code": 0,
                "output": "ROOT_PRESERVED_COLD_PREDECESSOR_AND_COMPLETED_WORK",
            })))
            self.assertEqual(root_call()["name"], "finish")
            with model.lock:
                self.assertEqual(len(model.requests), 10)
                self.assertEqual(model.child_turns, {"left": 2, "right": 2})
                self.assertEqual(model.root_turns, 6)
                self.assertFalse(model.errors)
                report = model.measurements.report(
                    scene.ids, scene.permissions, worker_id=scene.worker,
                    task_id=scene.task, attempt_id=scene.attempt,
                )
            self.assertEqual(report["request_count"], 10)
            self.assertEqual(report["child_session_protocol"], "cold-clarification-v1")
            self.assertEqual([row["instructions_digest"] for row in report["fresh_children"]],
                             [sha(scene.instructions[actor]) for actor in ("left", "right")])
            self.assertNotIn(cold.CLARIFICATION, encoded(report))
            self.assertNotIn(model.token, encoded(report))

    def test_hold_refuses_completed_projection_before_actual_fixture_child_loop(self):
        scene = Scene()
        with cold.ColdFixture("e" * 64) as model:
            model.session_id = scene.session
            model.root_turns = 4
            model.child_turns = {"left": 2, "right": 1}
            model.first_status = deepcopy(scene.model.first_status)
            model.followup_status = deepcopy(scene.model.second_status)
            model.child_ids["left"] = scene.ids["left"]
            model.patches["left"] = patch_page(scene, "left")["content"]
            observed = encoded({
                "state": "exited", "exit_code": 0,
                "output": encoded({"status": scene.model.second_status, "patch": patch_page(scene, "right"),
                                   "image_child_tools_sha256": "e" * 64}),
            })
            results = [result("root", ordinal, "{}" if ordinal < 4 else observed) for ordinal in range(1, 5)]
            payload = model_payload(
                cold.REQUIREMENTS + "\n\nRoot repository instructions:\n" + cold.ROOT_RULES,
                results, cold.BASE_TOOLS | cold.CHILD_TOOLS | cold.SESSION_TOOLS,
            )
            with self.assertRaisesRegex(AssertionError, "model loop"):
                model.reply(payload)
            self.assertFalse(model.two_returned.is_set())
            self.assertEqual(model.requests, [])

    def test_cold_anchor_binds_actual_instructions_once_and_preserves_ordinary_output(self):
        from tests.test_operational_measurement import request_fixture, request_report

        observed, identities, permissions = request_fixture()
        ordinary = request_report(observed, identities, permissions)
        self.assertNotIn("child_session_protocol", ordinary)
        self.assertTrue(all("spawn_response_prepared_to_first_request_ns" in row
                            and "session_response_prepared_to_first_request_ns" not in row
                            for row in ordinary["fresh_children"]))
        payload = {"messages": [{"role": "user", "content": "private fixture"}], "tools": []}
        probe = measurement.RequestMeasurements(6, cold_session=True)
        with self.assertRaises(AssertionError):
            probe.session_instructions("left", "actual instructions", 100)
        probe.session_prepared("left", 120)
        self.assertEqual(probe.spawns["left"], {"prepared_ns": 120, "instructions_digest": None})
        with self.assertRaises(AssertionError):
            probe.request("left", 1, payload, 125)
        with self.assertRaises(AssertionError):
            probe.session_instructions("left", "actual instructions", 119)
        probe.session_instructions("left", "actual instructions", 125)
        probe.request("left", 1, payload, 125)
        self.assertEqual(probe.spawns["left"], {
            "prepared_ns": 120, "instructions_digest": sha("actual instructions"),
        })
        for action in (
            lambda: probe.session_prepared("left", 126),
            lambda: probe.session_instructions("left", "changed", 126),
            lambda: probe.spawn_prepared("right", "ordinary", 126),
            lambda: measurement.RequestMeasurements(8, cold_session=True),
            lambda: observed.session_prepared("left", 126),
        ):
            with self.assertRaises(AssertionError):
                action()


class FakeProcess:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeProcesses:
    def __init__(self, manager_code=130, fail=None):
        self.running = {"first": FakeProcess(0), "installation": FakeProcess(manager_code), "last": FakeProcess(0)}
        self.original = self.running["installation"]
        self.handles, self.stopped = [], []
        self.fail = fail

    def stop(self, name):
        self.stopped.append(name)
        self.running.pop(name, None)
        if name == self.fail:
            raise RuntimeError("private fixture cleanup error")


class ColdRequestObservationTests(unittest.TestCase):
    def test_success_forwards_each_direct_request_once_and_preserves_result_identity(self):
        payload = {"private": "fixture payload"}
        query = {"repository": "private fixture repository"}
        calls = (
            ("enroll", "POST", "/managed/enroll", {"data": payload}),
            ("enroll-replay", "POST", "/managed/enroll", {"data": payload}),
            ("foreign-scope-refusal", "POST", "/managed/enroll", {"data": payload, "expected": 403}),
            ("active-snapshot", "GET", "/managed/snapshot", {"query": query}),
            ("active-children", "GET", "/managed/children", {"query": query}),
            ("active-worker", "GET", "/api/workers/private-worker", {"query": query}),
        )
        observation = cold.ColdRequestObservation()
        for operation, method, path, options in calls:
            with self.subTest(operation=operation):
                result = object()
                client = SimpleNamespace(request=Mock(return_value=result))
                self.assertIs(observation.request(operation, client, method, path, **options), result)
                client.request.assert_called_once_with(method, path, **options)
                for name, value in options.items():
                    self.assertIs(client.request.call_args.kwargs[name], value)
                self.assertIsNone(observation.report())

    def test_exception_categories_are_fixed_and_original_objects_propagate(self):
        from urllib.error import URLError

        for original, kind in (
            (TimeoutError("private timeout"), "timeout"),
            (URLError("private endpoint"), "url"),
            (ConnectionError("private connection"), "connection"),
            (AssertionError("private response body"), "assertion"),
            (OSError("private path"), "os"),
            (ValueError("private JSON"), "value"),
            (KeyboardInterrupt("private interrupt"), "other"),
            (SystemExit("private exit"), "other"),
        ):
            with self.subTest(kind=kind):
                observation = cold.ColdRequestObservation()
                client = SimpleNamespace(request=Mock(side_effect=original))
                with self.assertRaises(type(original)) as caught:
                    observation.request("enroll", client, "POST", "/managed/enroll", data={})
                self.assertIs(caught.exception, original)
                client.request.assert_called_once_with("POST", "/managed/enroll", data={})
                self.assertEqual(observation.report(), {"operation": "enroll", "exception_kind": kind})

    def test_first_failure_stays_private_and_frozen_across_later_calls_and_report_mutation(self):
        class PrivateCredentialException(Exception):
            def __str__(self):
                raise AssertionError("exception rendering must not run")

        secret = "secret fixture credential/path/body/worker-id"
        original = PrivateCredentialException(secret)
        later = TimeoutError(secret)
        result = object()
        client = SimpleNamespace(request=Mock(side_effect=[original, later, result]))
        observation = cold.ColdRequestObservation()
        with self.assertRaises(PrivateCredentialException) as caught:
            observation.request("enroll", client, "POST", secret, data={secret: secret})
        self.assertIs(caught.exception, original)
        expected = {"operation": "enroll", "exception_kind": "other"}
        self.assertEqual(observation.report(), expected)
        altered = observation.report()
        altered["operation"] = secret
        with self.assertRaises(TimeoutError) as caught:
            observation.request("active-worker", client, "GET", secret, query={secret: secret})
        self.assertIs(caught.exception, later)
        self.assertIs(observation.request("enroll-replay", client, "POST", secret), result)
        self.assertEqual(client.request.call_count, 3)
        self.assertEqual(observation.report(), expected)
        serialized = encoded(observation.report())
        for private in (secret, "PrivateCredentialException", "TimeoutError"):
            self.assertNotIn(private, serialized)

    def test_unknown_operation_is_rejected_without_exporting_it_or_calling_client(self):
        observation = cold.ColdRequestObservation()
        client = SimpleNamespace(request=Mock())
        with self.assertRaisesRegex(AssertionError, "^unknown cold request observation operation$"):
            observation.request("private dynamic operation", client, "GET", "/private")
        client.request.assert_not_called()
        self.assertIsNone(observation.report())

    def test_real_loopback_timeout_keeps_original_three_second_client_call_and_no_retry(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        release = threading.Event()
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                release.wait(10)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        client = cold.Client(f"http://127.0.0.1:{server.server_port}", "private fixture token")
        observation = cold.ColdRequestObservation()
        try:
            with patch.object(client.opener, "open", wraps=client.opener.open) as opened:
                with self.assertRaises(TimeoutError):
                    observation.request("active-worker", client, "GET", "/api/workers/private-worker")
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(opened.call_args.kwargs, {"timeout": 3})
            self.assertEqual(received, ["/api/workers/private-worker"])
            self.assertEqual(observation.report(), {
                "operation": "active-worker", "exception_kind": "timeout",
            })
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertFalse(thread.is_alive())

    def test_observed_failure_survives_original_owned_cleanup_errors(self):
        original = TimeoutError("private request detail")
        observation = cold.ColdRequestObservation()
        client = SimpleNamespace(request=Mock(side_effect=original))
        processes = FakeProcesses(manager_code=-9, fail="last")
        release = threading.Event()
        caught_original = None
        with self.assertRaises(TimeoutError) as caught:
            try:
                observation.request("enroll", client, "POST", "/managed/enroll", data={})
            except BaseException as exc:
                caught_original = exc
                raise
            finally:
                cold.close_owned(processes, release, caught_original)
        self.assertIs(caught.exception, original)
        self.assertTrue(release.is_set())
        self.assertEqual(processes.stopped, ["last", "installation", "first"])
        self.assertEqual(processes.running, {})
        self.assertEqual(original.__notes__, [
            "cold owned cleanup incomplete: owned-service-last,manager-shutdown-unconfirmed",
        ])
        self.assertEqual(observation.report(), {"operation": "enroll", "exception_kind": "timeout"})


class ColdCleanupTests(unittest.TestCase):
    def test_original_manager_exit_confirms_shutdown_after_bookkeeping_is_empty(self):
        accepted = FakeProcesses()
        cold.stop_installation(accepted, accepted.original)
        self.assertNotIn("installation", accepted.running)
        for code in (-9, 0, 1):
            with self.subTest(code=code):
                rejected = FakeProcesses(manager_code=code)
                with self.assertRaisesRegex(AssertionError, "handled service shutdown"):
                    cold.stop_installation(rejected, rejected.original)
                self.assertNotIn("installation", rejected.running)
        replaced = FakeProcesses()
        with self.assertRaisesRegex(AssertionError, "identity changed"):
            cold.stop_installation(replaced, FakeProcess(130))
        self.assertEqual(replaced.stopped, [])

    def test_cleanup_attempts_every_owned_control_and_preserves_original_failure(self):
        release = threading.Event()
        processes = FakeProcesses(manager_code=-9, fail="last")
        calls = []

        class Runtime:
            def request(self, method, path, **kwargs):
                calls.append((method, path, kwargs))
                if method == "GET":
                    return {"workers": [{"worker_id": "worker-1"}, {"worker_id": "worker-2"}]}
                if path == "/api/workers/worker-1/drain":
                    raise RuntimeError("private control error")
                return {}

        def reset():
            raise RuntimeError("private reset error")

        original = AssertionError("original trial assertion")
        failures = cold.close_owned(
            processes, release, original, reset=reset, runtime=Runtime(),
            scope={"deployment_id": "fixture-deployment", "repository": "fixture-repository"},
        )
        self.assertTrue(release.is_set())
        self.assertEqual(processes.stopped, ["last", "installation", "first"])
        self.assertEqual(processes.running, {})
        self.assertEqual([(method, path) for method, path, _ in calls], [
            ("GET", "/api/workers"),
            ("POST", "/api/workers/worker-1/drain"), ("POST", "/api/workers/worker-1/stop"),
            ("POST", "/api/workers/worker-2/drain"), ("POST", "/api/workers/worker-2/stop"),
        ])
        self.assertEqual(set(failures), {
            "target-zero", "owned-worker-drain", "owned-service-last", "manager-shutdown-unconfirmed",
        })
        self.assertEqual(str(original), "original trial assertion")
        self.assertEqual(len(original.__notes__), 1)
        self.assertNotIn("private", original.__notes__[0])
        for _, _, options in calls[1:]:
            self.assertEqual(options["data"]["schema_version"], 1)
            self.assertEqual(options["data"]["repository"], "fixture-repository")

    def test_success_artifact_is_created_only_after_final_owned_shutdown(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            failed = FakeProcesses(manager_code=-9)
            with self.assertRaisesRegex(AssertionError, "manager-shutdown-unconfirmed"):
                cold.publish_report(path, {"passed": True}, failed, threading.Event())
            self.assertFalse(path.exists())
            self.assertEqual(failed.stopped, ["last", "installation", "first"])
            self.assertEqual(failed.running, {})
            accepted = FakeProcesses()
            release = threading.Event()
            cold.publish_report(path, {"passed": True}, accepted, release)
            self.assertEqual(json.loads(path.read_text()), {"passed": True})
            self.assertEqual(accepted.running, {})
            self.assertTrue(release.is_set())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cleanup_failure_without_prior_error_is_never_reported_as_success(self):
        processes = FakeProcesses(manager_code=-9)
        with self.assertRaisesRegex(AssertionError, "manager-shutdown-unconfirmed"):
            cold.close_owned(processes, threading.Event())
        self.assertEqual(processes.running, {})
        accepted = FakeProcesses()
        self.assertEqual(cold.close_owned(accepted, threading.Event()), [])
        self.assertEqual(accepted.running, {})


if __name__ == "__main__":
    unittest.main()
