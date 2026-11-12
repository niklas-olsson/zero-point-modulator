"""Focused installed-scene oracles, using named local fixtures.

These tests do not claim an installed authority, real A transport, guest execution
or Ed25519 verification. Native contract jobs supply those separate proofs.
"""

from contextlib import contextmanager
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

CONTRACTS = Path(__file__).resolve().parent / "contracts"
sys.path.insert(0, str(CONTRACTS))
import check_managed_public_stack as trial


def status(byte="11"):
    public = byte * 32
    swarm = "9" * 64
    return {
        "schema_version": 1, "profile": "polyphony-public-conversation",
        "topic": trial.TOPIC, "swarm_id": swarm,
        "session_id": trial.digest(["polyphony-public-conversation-v1", swarm, trial.TOPIC]),
        "author": hashlib.sha256(bytes.fromhex(public)).hexdigest()[:16],
        "public_key": public, "stream_id": "8" * 32,
        "visibility": "public", "execution_authorized": False,
    }


def record(key="proposal", *, actor=None, reply_to=None, target=None, request_key=None):
    actor = status() if actor is None else actor
    output = trial.PUBLIC_OUTPUTS[key]
    body = {
        "schema_version": 1, "swarm_id": actor["swarm_id"], "session_id": actor["session_id"],
        "topic": trial.TOPIC, "kind": output["kind"], "content": output["content"],
        "reply_to": reply_to, "target": target, "references": [],
        "request_key": request_key or "oj-public:" + "a" * 64,
        "visibility": "public", "author": actor["author"], "public_key": actor["public_key"],
        "timestamp": 42.0,
    }
    # Deliberately structural only. The harness's separate installed verifier
    # must verify actual signatures before declaring a native scene successful.
    return {"message_id": trial.digest(body), "body": body, "signature": "0" * 128}


def receipt(record):
    return {
        "schema_version": 1, "message_id": record["message_id"],
        "session_id": record["body"]["session_id"], "author": record["body"]["author"],
        "stored": True, "visibility": "public", "delivery": "submitted",
        "execution_authorized": False, "record": record,
    }


def proposal():
    actor = status()
    item = record()
    slot = {
        "slot_id": "a" * 64,
        "actor": {"role": "root", "child_id": None, "binding_digest": "b" * 64,
                  "status": actor, "admission_closed": False},
        "activation_request_id": "mpub1:root-proposal", "persona_key": "root",
        "instruction_key": "proposal", "selected_message_ids": [], "reply_to": None,
        "expires_at": time.time() + 30, "state": "published", "reason": None,
        "context_digest": trial.digest([]), "model_call_digest": "c" * 64,
        "publication_request_key": "oj-public:" + "a" * 64, "receipt": receipt(item),
    }
    return {"slot": slot, "execution_authorized": False}


def public_body(key="proposal", context=()):
    persona = "root" if key in {"proposal", "followup"} else "left" if key == "criticism" else "right"
    return {
        "model": trial.MODEL_NAME, "stream": False, "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": trial.PUBLIC_SYSTEM + trial.PUBLIC_PERSONAS[persona]},
            {"role": "user", "content": trial.canonical({
                "instruction": trial.PUBLIC_INSTRUCTIONS[key], "public_context": list(context),
            }).decode("ascii")},
        ],
    }


def fixture():
    return trial.ManagedPublicFixture(
        {"child_tools.py": "d" * 64, "public_tools.py": "e" * 64}, status("22"),
    )


def database(path):
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.executescript(
        "CREATE TABLE workers(worker_id TEXT PRIMARY KEY,attempt_id TEXT);"
        "CREATE TABLE calls(worker_id TEXT,attempt_id TEXT,request_id TEXT,payload_hash TEXT,"
        "reserved_tokens INTEGER,status TEXT,response TEXT,"
        "PRIMARY KEY(worker_id,attempt_id,request_id));"
    )
    connection.execute("INSERT INTO workers VALUES('owned-worker','original-attempt')")
    connection.commit()
    return connection


def reserve(connection, index, *, committed=True, status_value="reserved"):
    connection.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?)", (
        "owned-worker", "original-attempt", "request-" + str(index),
        str(index) * 64, 1024, status_value, None if status_value == "reserved" else "{}",
    ))
    if committed:
        connection.commit()


def post(model, payload, *, token=None):
    request = Request(model.endpoint, data=trial.canonical(payload), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (model.token if token is None else token),
    })
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.code, json.loads(response.read(524289))


def failure_observation_after_response(model):
    """Unit-only bounded wait for the completed HTTP handler to release its lock."""
    cutoff = time.monotonic() + 2
    while True:
        value = trial.fixture_failure_observation(model)
        if value.get("available") is True:
            return value
        if time.monotonic() >= cutoff:
            raise AssertionError("named HTTP fixture did not release its observation lock")
        time.sleep(0.01)


@contextmanager
def scope_server(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.connection.settimeout(1)
            url = urlsplit(self.path)
            request = (self.headers.get("Authorization"), url.path, parse_qs(url.query))
            requests.append(request)
            code, value = respond(*request)
            raw = value if isinstance(value, bytes) else trial.canonical(value)
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        yield trial.Client(endpoint, "runtime"), trial.Client(endpoint, "ingress"), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        if thread.is_alive():
            raise AssertionError("named scope fixture did not close")




PRIVATE_CHILD_SCHEMA = (
    "CREATE TABLE children(child_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "uid INTEGER NOT NULL,grant_json TEXT NOT NULL,deadline REAL NOT NULL,boot_id TEXT NOT NULL,"
    "phase TEXT NOT NULL DEFAULT 'granted',exec_id TEXT,exec_dispatched INTEGER NOT NULL DEFAULT 0,"
    "armed_deadline REAL,exit_code INTEGER,revoked INTEGER NOT NULL DEFAULT 0,"
    "provisional TEXT NOT NULL DEFAULT '{}',capture TEXT,artifacts TEXT NOT NULL DEFAULT '{}',"
    "outcome_report TEXT,UNIQUE(worker_id,ordinal),UNIQUE(worker_id,uid))"
)
PRIVATE_INTENT_SCHEMA = (
    "CREATE TABLE intents(worker_id TEXT NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,"
    "delivery_key TEXT NOT NULL,child_id TEXT,status TEXT NOT NULL DEFAULT 'pending',"
    "PRIMARY KEY(worker_id,request_id))"
)


class PrivateExecutionFixture:
    """Literal unchanged ordinary child rows; public storage is not simulated."""

    def __init__(self, directory):
        self.path = Path(directory) / "children.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute(PRIVATE_CHILD_SCHEMA)
        self.db.execute(PRIVATE_INTENT_SCHEMA)
        self.scope = {"deployment_id": "public-fixture", "repository": "github:github.com/fixture/repo"}
        self.task, self.attempt, self.worker, self.profile = "task-1", "attempt-1", "worker-1", "profile-1"
        self.fixture = SimpleNamespace(child_ids={}, outcomes={})
        self.children = []
        for actor, ordinal in (("left", 0), ("right", 1)):
            child_id = "child_" + str(ordinal + 1) * 32
            self.fixture.child_ids[actor] = child_id
            binding = {
                **self.scope, "schema_version": 1, "child_id": child_id,
                "worker_id": self.worker, "task_id": self.task, "root_attempt_id": self.attempt,
                "parent_attempt_id": self.attempt, "profile": self.profile, "requirements_revision": 1,
                "launch_key": "polyphony-child:" + child_id,
                "instructions_digest": trial.text_hash(trial.PRIVATE_INSTRUCTIONS[actor]),
                "allocated_seconds": 90, "max_turns": 3, "capabilities": ["python"],
            }
            report = {"request_key": "original-" + actor, "outcome": "completed",
                      "summary": trial.PRIVATE_SUMMARIES[actor]}
            report["payload_digest"] = trial.digest(report)
            self.fixture.outcomes[actor] = {"report": report}
            artifacts = {kind: trial.text_hash(actor + ":" + kind) for kind in ("patch", "log", "manifest")}
            self.children.append({"child": {"id": child_id, "binding": binding, "captured_artifacts": artifacts}})
            grant = {"schema_version": 1, "binding": binding, "instructions": trial.PRIVATE_INSTRUCTIONS[actor],
                     "launch_allowed": True, "remaining_seconds": 90}
            self.db.execute(
                "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id,phase,"
                "exec_id,exec_dispatched,armed_deadline,exit_code,outcome_report) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (child_id, self.worker, ordinal, 60000 + ordinal, trial.canonical(grant).decode(),
                 190.0 + ordinal, "original-boot", "exec_returned", "exec-" + actor,
                 1, 189.0 + ordinal, 0, trial.canonical(report).decode()),
            )
            request = "managed-public-spawn-" + actor
            payload = {"instructions": trial.PRIVATE_INSTRUCTIONS[actor], "seconds": 90,
                       "max_turns": 3, "capabilities": ["python"]}
            self.db.execute("INSERT INTO intents VALUES(?,?,?,?,?,?)", (
                self.worker, request, trial.canonical(payload).decode(),
                "openjules-child:" + trial.digest([self.worker, self.attempt, request]), child_id, "bound",
            ))
        self.db.commit()

    def read(self, *, final=False):
        return trial.private_execution_rows(
            self.path, self.fixture, self.scope, self.task, self.attempt, self.worker, self.children,
            profile=self.profile, final=final,
        )

    def capture(self):
        for permission in self.children:
            child = permission["child"]
            self.db.execute(
                "UPDATE children SET phase='completed',capture=?,artifacts=? WHERE child_id=?",
                (trial.canonical({"contents": {"manifest": "retained"}, "state": "exited"}).decode(),
                 trial.canonical(child["captured_artifacts"]).decode(), child["id"]),
            )
        self.db.commit()

    def close(self):
        self.db.close()




class PublicRowsFixture:
    """Literal private WAL rows for reader/projection oracles, not a host runtime."""

    def __init__(self, directory):
        self.path = Path(directory) / "public-rows.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute("PRAGMA user_version=2")
        self.db.executescript(
            "CREATE TABLE public_workers(worker_id TEXT PRIMARY KEY,data TEXT NOT NULL,digest TEXT NOT NULL);"
            "CREATE TABLE public_actors(worker_id TEXT NOT NULL,actor_id TEXT NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(worker_id,actor_id));"
            "CREATE TABLE public_requests(worker_id TEXT NOT NULL,request_id TEXT NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(worker_id,request_id));"
            "CREATE TABLE public_slots(worker_id TEXT NOT NULL,slot_id TEXT NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(worker_id,slot_id));"
            "CREATE TABLE public_records(worker_id TEXT NOT NULL,message_id TEXT NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(worker_id,message_id));"
            "CREATE TABLE public_processes(worker_id TEXT NOT NULL,process_id TEXT NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(worker_id,process_id));"
        )
        self.worker = "owned-worker"
        self.actors = []
        for role, who, uid, key in (
            ("root", "root", 65532, "11"),
            ("child", "child-left", 60000, "22"),
            ("child", "child-right", 60001, "33"),
        ):
            self.actors.append({
                "actor_id": who,
                "identity": {"role": role, "child_id": None if role == "root" else who,
                             "uid": uid, "exec_id": "private-exec-" + who},
                "binding_digest": trial.digest(["original-binding", who]),
                "status": status(key), "closed": True,
                "private_configuration": "PRIVATE_CONFIG_CANARY",
            })
        self.slots = []
        for index, (who, instruction) in enumerate((
            ("root", "proposal"), ("child-left", "criticism"),
            ("child-right", "dissent"), ("root", "followup"),
        )):
            self.slots.append({
                "slot_id": trial.digest(["original-slot", index]), "actor_id": who,
                "activation_request_id": "mpub1:activation-" + str(index),
                "persona_key": who, "instruction_key": instruction,
                "selected_message_ids": [], "reply_to": None, "expires_at": 150.0,
                "state": "declined" if instruction == "dissent" else "published",
                "reason": None, "context_digest": trial.digest([]),
                "model_call_digest": trial.digest(["original-model", index]),
                "publication_intent": {"private_fixture_only": index},
                "receipt": {"message_id": trial.digest(["original-record", index])},
                "private_model_key": "/public/private-" + str(index),
            })
        self.requests = [{"request_id": slot["activation_request_id"],
                          "payload_digest": trial.digest(["original-payload", index])}
                         for index, slot in enumerate(self.slots)]
        self.records = [{"message_id": trial.digest(["original-record", index]),
                         "fixture_record": index} for index in range(6)]
        self.processes = []
        for index, who in enumerate(("root", "child-left", "child-right")):
            for stage_index, stage in enumerate(("probe", "server")):
                pid = 100 + index * 2 + stage_index
                identity = {"pid": pid, "pgid": pid, "starttime": 1000 + pid, "boot_id": "original-boot"}
                self.processes.append({
                    "process_id": trial.digest([who, 1, stage]), "actor_id": who,
                    "generation": 1, "stage": stage, "state": "stopped",
                    "identity": identity, "cleanup": {
                        "identity": identity, "leader_terminated": True,
                        "leader_reaped": True, "group_terminated": True,
                    },
                })
        self.header = {
            "schema_version": 1, "protocol": trial.PROTOCOL, "root": {"worker_id": self.worker},
            "policy": {"topic": trial.TOPIC, "private_path": "PRIVATE_SCOPE_PATH"},
            "policy_digest": trial.digest("original-policy"), "closed": True,
            "actors": 3, "requests": 4, "slots": 4, "records": 6, "processes": 6,
            "history_charges": 4, "page_charges": 4, "post_reservations": 5,
            "transcript_bytes": sum(len(trial.canonical(item)) for item in self.records),
        }
        # A literal frozen projection deliberately independent of the reader.
        # Other production validation belongs to the installed host, not this fixture.
        frozen = {
            "schema_version": 1, "protocol": trial.PROTOCOL, "topic": trial.TOPIC,
            "policy_digest": self.header["policy_digest"],
            "actors": [{name: actor[name] for name in (
                "actor_id", "identity", "binding_digest", "status", "closed",
            )} for actor in self.actors],
            "slots": [{name: slot[name] for name in (
                "slot_id", "actor_id", "activation_request_id", "persona_key", "instruction_key",
                "selected_message_ids", "reply_to", "expires_at", "state", "reason", "context_digest",
                "model_call_digest", "publication_intent", "receipt",
            )} for slot in self.slots],
            "records": self.records, "request_digest": trial.digest(self.requests),
            "process_digest": trial.digest(self.processes),
            "counts": {name: self.header[name] for name in (
                "actors", "requests", "slots", "records", "processes", "history_charges",
                "page_charges", "post_reservations", "transcript_bytes",
            )},
        }
        self.header.update(frozen=frozen, freeze_digest=trial.digest(frozen))
        self.save("workers", None, self.header)
        for short, key, values in (
            ("actors", "actor_id", self.actors), ("requests", "request_id", self.requests),
            ("slots", "slot_id", self.slots), ("records", "message_id", self.records),
            ("processes", "process_id", self.processes),
        ):
            for value in values:
                self.save(short, value[key], value)
        self.db.commit()

    def save(self, short, key, value, *, worker=None):
        row = (worker or self.worker,)
        if key is not None:
            row += (key,)
        row += (trial.canonical(value).decode("ascii"), trial.digest(value))
        placeholders = ",".join("?" for _ in row)
        conflict = "worker_id" + ("" if key is None else "," + {
            "actors": "actor_id", "requests": "request_id", "slots": "slot_id",
            "records": "message_id", "processes": "process_id",
        }[short])
        self.db.execute("INSERT INTO public_" + short + " VALUES(" + placeholders + ")"
                        + " ON CONFLICT(" + conflict + ") DO UPDATE SET data=excluded.data,digest=excluded.digest", row)

    def read(self, *, idle_worker=None):
        return trial.read_public_rows(self.path, self.worker, idle_worker=idle_worker)

    def close(self):
        self.db.close()



class CorrelatedPublicFixture(PublicRowsFixture):
    """Literal cross-journal identities plus real WAL rows, without host effects."""

    def __init__(self, directory):
        super().__init__(directory)
        for short in ("workers", "actors", "requests", "slots", "records", "processes"):
            self.db.execute("DELETE FROM public_" + short)
        self.attempt_id = "original-attempt"
        self.scope = {"deployment_id": "correlated-fixture", "repository": "github:github.com/fixture/repository"}
        self.profile, self.base = "profile-1", "a" * 40
        self.policy = {
            "protocol": trial.PROTOCOL, "public_approved": True, "topic": trial.TOPIC,
            "server": {"python": "/fixture/venv/bin/python", "cwd": "/fixture/authority",
                       "scope_key_file": "/fixture/private-scope", "scope_key_sha256": "9" * 64},
            "personas": trial.PUBLIC_PERSONAS, "instructions": trial.PUBLIC_INSTRUCTIONS,
            "invitations": {"independent": {"target": status("44")["author"], "content": trial.INVITATION}},
        }
        self.policy_digest = trial.digest({"fixture_full_child_policy": self.policy})
        spec = {"schema_version": 1, **self.scope, "profile": self.profile,
                "slot_id": "slot-1", "generation": 1, "launch_key": "original-launch"}
        self.root = {
            "worker_id": self.worker, "spec": spec, "attempt_id": self.attempt_id, "task_id": "task-1",
            "runtime_principal": "runtime-original", "profile_digest": "b" * 64,
            "requirements_revision": 1, "base": self.base, "deadline": 10000.0,
            "monotonic_deadline": 300.0, "boot_id": "original-boot",
        }
        self.root_row = {
            "worker_id": self.worker, "attempt_id": self.attempt_id, "exec_id": "original-root-exec",
            "input_sha256": trial.text_hash("original retained Docker input with private instructions"),
            "deadline": 300.0, "spec": spec,
        }
        self.attempt = {
            "id": self.attempt_id, "task": "task-1",
            "runtime_binding": {name: self.root[name] for name in (
                "runtime_principal", "profile_digest", "requirements_revision", "base", "deadline",
            )},
        }
        self.context = {
            "scope": self.scope, "profile": self.profile, "base": self.base,
            "runtime_config": {"children": [{"policy": {"public_discussion": self.policy}}]},
        }
        self.fixture = SimpleNamespace(
            child_ids={"left": "child_" + "1" * 32, "right": "child_" + "2" * 32},
            observations={"compose": {}}, peer_status=status("44"), requests=[],
            child_contexts={"left": {"pages": 1}, "right": {"pages": 1}},
        )
        self.private = {}
        self.actors = []
        for role, name, who, uid, key in (
            ("root", "root", "root", 65532, "11"),
            ("child", "left", self.fixture.child_ids["left"], 60000, "22"),
            ("child", "right", self.fixture.child_ids["right"], 60001, "33"),
        ):
            grant = {
                "grant_digest": trial.digest(["original-grant", name]),
                "instructions_digest": trial.text_hash(trial.PRIVATE_INSTRUCTIONS.get(name, "")),
            }
            if role == "child":
                self.private[who] = {
                    "uid": uid, "exec_id": "original-exec-" + name, "boot_id": "original-boot",
                    "armed_deadline": 189.0 if name == "left" else 190.0,
                    "grant_json": trial.canonical({"binding": grant}).decode("ascii"),
                }
            identity = {
                "schema_version": 1, "protocol": trial.PROTOCOL, "worker_id": self.worker,
                "root_attempt_id": self.attempt_id, "task_id": self.attempt["task"],
                "requirements_revision": 1, "profile_digest": self.root["profile_digest"],
                "child_policy_digest": self.policy_digest, "role": role,
                "child_id": None if role == "root" else who, "uid": uid,
                "exec_id": self.root_row["exec_id"] if role == "root" else self.private[who]["exec_id"],
                "grant_digest": None if role == "root" else grant["grant_digest"],
                "instructions_digest": self.root_row["input_sha256"] if role == "root" else grant["instructions_digest"],
            }
            actor_status = status(key)
            binding = {"identity": identity, "status": actor_status}
            self.actors.append({
                "actor_id": who, "identity": identity, "identity_digest": trial.digest(identity),
                "binding": binding, "binding_digest": trial.digest(binding), "status": actor_status,
                "closed": True, "leave_slot": None, "generations": 1,
                "config": {"sha256": trial.digest(["original-config", name])},
            })
        self.actor_by_id = {actor["actor_id"]: actor for actor in self.actors}
        self.slots, self.model_rows, self.records, self.requests = [], [], [], []
        self.projections = {}
        self.add_slot("root", "root", "proposal", "mpub1:root-proposal", [], None)
        proposal_id = self.projections["proposal"]["slot"]["receipt"]["message_id"]
        invitation_record = record(actor=self.actor_by_id["root"]["status"],
                                   request_key="oj-public-invite:fixture-original")
        invitation_record["body"].update(kind="invite", content=trial.INVITATION,
                                         target=self.fixture.peer_status["author"])
        invitation_record["message_id"] = trial.digest(invitation_record["body"])
        peer_record = record(actor=self.fixture.peer_status, reply_to=invitation_record["message_id"],
                             request_key="independent-peer-decline")
        peer_record["body"].update(kind="decline", content=trial.PEER_DECLINE)
        peer_record["message_id"] = trial.digest(peer_record["body"])
        self.add_slot(self.fixture.child_ids["left"], "left", "criticism", "mpub1:activate-left",
                      [self.records[0]], proposal_id)
        self.add_slot(self.fixture.child_ids["right"], "right", "dissent", "mpub1:activate-right",
                      [self.records[0]], proposal_id)
        self.records.extend([invitation_record, peer_record])
        selected = [self.records[0], self.records[1], self.records[2], peer_record]
        self.add_slot("root", "root", "followup", "mpub1:root-followup", selected, proposal_id)
        self.fixture.observations["compose"] = {
            "proposal": self.projections["proposal"], "followup": self.projections["followup"],
            "public_slots": {"left": self.projections["criticism"], "right": self.projections["dissent"]},
            "invitation": {"receipt": receipt(invitation_record)}, "peer_decline": peer_record,
            "history_observation": {"pages": 1},
        }
        request_roles = [
            ("mpub1:root-proposal", "root", "public_turn"),
            ("mpub1:root-followup", "root", "public_turn"),
            ("mpub1:independent-invitation", "root", "public_invite"),
            ("mpub1:root-history-0", "root", "public_read"),
        ]
        for name in ("left", "right"):
            who = self.fixture.child_ids[name]
            request_roles.extend([
                ("mpub1:activate-" + name, "root", "public_activate"),
                ("mpub1:status-" + name, "root", "public_status"),
                ("mpub1:pending-" + name, who, "public_pending"),
                ("mpub1:pull-" + name, who, "public_pull"),
                ("mpub1:child-" + name + "-history-0", who, "public_read"),
            ])
        for key, who, operation in request_roles:
            payload = {"schema_version": 1, "operation": operation, "request_id": key}
            self.requests.append({
                "request_id": key, "actor_id": who, "payload": payload,
                "payload_digest": trial.digest(payload), "expires_at": 9999.0,
                "monotonic_cutoff": 140.0, "boot_id": "original-boot", "state": "complete",
                "error": None, "result": {}, "slot_id": None, "invitation": None,
                "history_charges": int(operation == "public_read"),
                "page_charges": int(operation == "public_read"),
            })
        self.processes = []
        for index, actor in enumerate(self.actors):
            who = actor["actor_id"]
            for stage_number, stage in enumerate(("probe", "server")):
                pid = 100 + index * 2 + stage_number
                identity = {"pid": pid, "pgid": pid, "starttime": 1000 + pid, "boot_id": "original-boot"}
                intent = {
                    "schema_version": 1, "stage": stage, "launch_path": self.policy["server"]["python"],
                    "cwd": self.policy["server"]["cwd"], "config_sha256": actor["config"]["sha256"],
                    "argv_sha256": trial.digest(["fixture-argv", who, stage]),
                    "environment_sha256": trial.digest(["fixture-environment"]),
                    "monotonic_cutoff": 125.0, "boot_id": "original-boot",
                }
                self.processes.append({
                    "process_id": trial.digest([who, 1, stage]), "actor_id": who, "generation": 1,
                    "stage": stage, "intent": intent, "intent_digest": trial.digest(intent),
                    "state": "stopped", "identity": identity,
                    "cleanup": {"identity": copy.deepcopy(identity), "leader_terminated": True,
                                "leader_reaped": True, "group_terminated": True},
                })
        self.header = {
            "schema_version": 1, "protocol": trial.PROTOCOL, "policy": self.policy,
            "policy_digest": self.policy_digest, "root": self.root, "closed": True,
            "actors": 3, "requests": len(self.requests), "slots": 4, "records": 6, "processes": 6,
            "history_charges": 7, "page_charges": 7, "post_reservations": 5,
            "transcript_bytes": sum(len(trial.canonical(row)) for row in self.records),
        }
        self.commit()

    def add_slot(self, who, persona, key, activation, selected, reply_to):
        selected_ids = [item["message_id"] for item in selected]
        slot_id = trial.digest([
            trial.PROTOCOL, self.worker, self.attempt_id, who, activation, self.policy_digest,
            persona, key, selected_ids, reply_to,
        ])
        model_key = "/public/" + trial.digest([
            trial.PROTOCOL, self.worker, self.attempt_id, who, activation, slot_id,
        ])
        provider = public_body(key, selected)
        actor = self.actor_by_id[who]
        model_payload = {
            "schema_version": 1, "request_id": model_key, "public_protocol": trial.PROTOCOL,
            "public_actor": actor["binding_digest"], "public_slot": slot_id, "messages": provider["messages"],
        }
        item = record(key, actor=actor["status"], reply_to=reply_to, request_key="oj-public:" + slot_id)
        self.fixture.requests.append(provider)
        self.records.append(item)
        row = {
            "slot_id": slot_id, "actor_id": who, "activation_request_id": activation,
            "activation_actor_id": "root", "persona_key": persona, "instruction_key": key,
            "selected_message_ids": selected_ids, "reply_to": reply_to, "expires_at": 9990.0,
            "monotonic_cutoff": 130.0, "boot_id": "original-boot",
            "state": "declined" if key == "dissent" else "published", "reason": None,
            "context": copy.deepcopy(selected), "context_digest": trial.digest(selected),
            "model_key": model_key, "model_call_digest": trial.digest(model_payload),
            "response": trial.PUBLIC_OUTPUTS[key],
            "publication_intent": {"request_key": "oj-public:" + slot_id}, "receipt": receipt(item),
            "history_charges": 1, "page_charges": 1, "decision_request_id": activation,
        }
        self.slots.append(row)
        observed = {name: copy.deepcopy(row[name]) for name in (
            "slot_id", "activation_request_id", "persona_key", "instruction_key",
            "selected_message_ids", "reply_to", "expires_at", "state", "reason",
            "context_digest", "model_call_digest", "receipt",
        )}
        observed["actor"] = {
            "role": actor["identity"]["role"], "child_id": actor["identity"]["child_id"],
            "binding_digest": actor["binding_digest"], "status": actor["status"], "admission_closed": False,
        }
        observed["publication_request_key"] = "oj-public:" + slot_id
        self.projections[key] = {"slot": observed, "execution_authorized": False}
        self.model_rows.append([self.worker, self.attempt_id, model_key, row["model_call_digest"], 1024])

    def commit(self):
        frozen = {
            "schema_version": 1, "protocol": trial.PROTOCOL, "topic": trial.TOPIC,
            "policy_digest": self.header["policy_digest"],
            "actors": [{name: row[name] for name in (
                "actor_id", "identity", "binding_digest", "status", "closed",
            )} for row in self.actors],
            "slots": [{name: row[name] for name in (
                "slot_id", "actor_id", "activation_request_id", "persona_key", "instruction_key",
                "selected_message_ids", "reply_to", "expires_at", "state", "reason", "context_digest",
                "model_call_digest", "publication_intent", "receipt",
            )} for row in self.slots],
            "records": self.records, "request_digest": trial.digest(self.requests),
            "process_digest": trial.digest(self.processes),
            "counts": {name: self.header[name] for name in (
                "actors", "requests", "slots", "records", "processes", "history_charges",
                "page_charges", "post_reservations", "transcript_bytes",
            )},
        }
        self.header.update(frozen=frozen, freeze_digest=trial.digest(frozen))
        self.save("workers", None, self.header)
        for short, key, values in (
            ("actors", "actor_id", self.actors), ("requests", "request_id", self.requests),
            ("slots", "slot_id", self.slots), ("records", "message_id", self.records),
            ("processes", "process_id", self.processes),
        ):
            for row in values:
                self.save(short, row[key], row)
        self.db.commit()

    def correlate(self):
        return trial.public_identity_evidence(
            self.read(), self.fixture, self.context, self.attempt, self.private,
            self.root_row, self.model_rows,
        )


class NamedCleanupOwner:
    """A callback-order fixture; it does not stand in for actual A containment."""

    def __init__(self, events, *, fail=False):
        self.events, self.fail = events, fail
        self.commands, self.adopted = [], {}

    def _owned_signal(self, pid, signum, identity):
        self.events.append("bootstrap-signal")
        return True

    def wait(self, entries, maximum, *, cutoff):
        self.events.append("bootstrap-wait")
        return [{"returncode": 0}]

    def cleanup(self, cutoff):
        self.events.append("containment")
        if self.fail:
            raise RuntimeError("PRIVATE_CLEANUP_CANARY")


class NamedCleanupPeer:
    def __init__(self, events, directory):
        self.events, self.directory = events, directory
        self.bootstrap = {"process": SimpleNamespace(pid="named-bootstrap"), "identity": {}}

    def request_close(self):
        self.events.append("peer-close")

    def finish_after_services(self, *, cutoff):
        self.events.append("peer-reap")
        return {"confirmed": True, "active_processes": 0}


class NamedCleanupFixture:
    def __init__(self, events, *, fail=False):
        self.events, self.fail = events, fail
        self.release_root = threading.Event()

    def close(self, cutoff):
        self.events.append("model-close")
        if self.fail:
            raise RuntimeError("PRIVATE_FIXTURE_CANARY")


def manager_fixture(processes, root):
    marker = root / "manager-ready"
    code = (
        "import pathlib,signal,time\n"
        "def stop(*args): raise SystemExit(130)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "pathlib.Path(" + repr(str(marker)) + ").write_text('ready')\n"
        "while True: time.sleep(0.01)\n"
    )
    process = processes.start("installation", [sys.executable, "-I", "-c", code], root, {})
    cutoff = time.monotonic() + 3
    try:
        while not marker.exists() and time.monotonic() < cutoff:
            if process.poll() is not None:
                raise AssertionError("owned manager fixture exited before its signal handler was ready")
            time.sleep(0.01)
        trial.require(marker.exists(), "owned manager fixture readiness deadline")
        return process
    except BaseException:
        processes.stop("installation", kill=True)
        raise


class ManagedPublicOracles(unittest.TestCase):
    def test_direct_entry_resolves_its_checkout_before_any_trial_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreign = root / "unrelated-working-directory"
            prior = root / "retained-prior-trial"
            evidence = root / "existing-evidence"
            for path in (foreign, prior, evidence):
                path.mkdir()
                (path / "sentinel").write_bytes(path.name.encode("ascii"))
            before = {str(path.relative_to(root)): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            # Direct-file CI invocation must resolve its own package without
            # repository cwd, PYTHONPATH, user-site or an installed wheel.
            completed = subprocess.run(
                [sys.executable, "-E", "-S", "-B",
                 str(CONTRACTS / "check_managed_public_stack.py"),
                 "--managed-stack-dir", str(prior),
                 "--image", "sha256:" + "1" * 64,
                 "--work-dir", str(evidence)],
                cwd=foreign, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(json.loads(completed.stdout), {
                "passed": False,
                "error": "managed public evidence directory must be new",
            })
            self.assertEqual(
                {str(path.relative_to(root)): path.read_bytes()
                 for path in root.rglob("*") if path.is_file()},
                before,
            )
            self.assertEqual(sorted(path.name for path in root.iterdir()),
                             sorted((foreign.name, prior.name, evidence.name)))

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_disposable_git_commit_keeps_automatic_maintenance_out_of_the_shared_owner(self):
        # Only Git 2.56.x is source-qualified for the exact --detach witness:
        # Git 2.43 runs these maintenance tasks in the foreground instead.
        # Measure before dispatch, never skip after a failed baseline premise.
        # The corrected real helper always runs, while the existing dedicated
        # orphan test keeps unknown-child refusal independent of Git version.
        import re

        version = subprocess.run(
            ["git", "--version"], env=trial.PROFILE_ENVIRONMENT,
            capture_output=True, timeout=5,
        )
        self.assertEqual(version.returncode, 0, version.stderr.decode(errors="replace")[-500:])
        match = re.fullmatch(rb"git version ([0-9]+)\.([0-9]+)\.[^\s]+", version.stdout.strip())
        self.assertIsNotNone(match, "installed Git version is missing or unparseable")
        git_series = (int(match[1]), int(match[2]))
        modes = ("baseline", "corrected") if git_series == (2, 56) else ("corrected",)
        # The baseline's real hook holds the exact detached lifetime alive.
        # The corrected run calls the same helper used by prepared_installation.
        program = r"""import json,os,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
from check_public_discussion_stack import children_of,exit_info,kernel_has_no_children
directory=pathlib.Path(sys.argv[2]);mode=sys.argv[3]
source=directory/'source';source.mkdir()
owner=t.OwnedTrial(dedicated_process=True)
release=directory/'hook-release';ready=directory/'hook-ready.json'
peer=None;maintenance=None;hook_identity=None
observed={'baseline_detached_maintenance_refused':False,'corrected_commit':False,
          'same_live_peer_retained':False,'repository_policy_unchanged':False}
def git(*args):
 return t.command(owner,'fixture-git-'+str(len(owner.commands)),['git',*args],source)
def same_lifetime(current,original):
 return current is not None and all(current[key]==original[key]
  for key in ('pid','group','session','starttime'))
try:
 git('init','-q')
 for key,value in (('maintenance.auto','true'),('maintenance.autoDetach','true'),
                   ('maintenance.gc.enabled','false'),('maintenance.loose-objects.enabled','true'),
                   ('maintenance.loose-objects.auto','-1'),('commit.gpgsign','false')):
  git('config','--',key,value)
 (source/'fixture.txt').write_text('controlled fixture bytes\n')
 hook=source/'.git/hooks/pre-auto-gc'
 hook.write_text('#!'+sys.executable+'\n'+r'''import json,os,pathlib,time
root=pathlib.Path(__file__).resolve().parents[2].parent
def identity(pid):
 fields=pathlib.Path('/proc/'+str(pid)+'/stat').read_bytes().rsplit(b')',1)[1].split()
 return {'pid':pid,'parent':int(fields[1]),'group':int(fields[2]),
         'session':int(fields[3]),'starttime':fields[19].decode()}
marker=root/'hook-ready.json';temporary=root/'hook-ready.tmp'
temporary.write_text(json.dumps({'maintenance':identity(os.getppid()),'hook':identity(os.getpid())}))
os.replace(temporary,marker)
cutoff=time.monotonic()+8
while not (root/'hook-release').exists():
 if time.monotonic()>=cutoff:raise SystemExit(4)
 time.sleep(.01)
''')
 hook.chmod(0o700)
 git('add','fixture.txt')
 peer=owner.launch('registered-live-peer',
  [sys.executable,'-I','-S','-c',
   "import signal,sys;signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));print('ready',flush=True);signal.pause()"],
  source,t.PROFILE_ENVIRONMENT)
 assert owner.line(peer)==b'ready'
 original_peer=dict(peer['identity'])
 if mode=='baseline':
  entry=owner.launch('unmodified-fixture-commit',
   ['git','-c','user.name=Managed Public Trial','-c','user.email=trial@example.invalid',
    'commit','-qm','controlled private base'],source,t.PROFILE_ENVIRONMENT)
  cutoff=time.monotonic()+5
  while not ready.exists() or exit_info(entry['process'].pid) is None:
   assert time.monotonic()<cutoff,'actual Git maintenance witness did not arrive'
   owner.tick(cutoff=cutoff);time.sleep(.01)
  witness=json.loads(ready.read_text())
  maintenance=witness['maintenance'];hook_identity=witness['hook']
  current=t.process_info(maintenance['pid'])
  assert same_lifetime(current,maintenance) and current['parent']==os.getpid()
  assert current['group']==current['session']==current['pid']
  assert same_lifetime(t.process_info(hook_identity['pid']),hook_identity)
  assert t.process_info(hook_identity['pid'])['parent']==maintenance['pid']
  # These are fixture-local reads only. No raw command line is exported.
  argv=pathlib.Path('/proc/'+str(maintenance['pid'])+'/cmdline').read_bytes().split(b'\0')[:-1]
  assert pathlib.Path(os.fsdecode(argv[0])).name=='git'
  assert argv[1:]==[b'maintenance',b'run',b'--auto',b'--quiet',b'--detach']
  registered={item['process'].pid for item in owner.commands if item['result'] is None}
  assert set(children_of(os.getpid(),cutoff))-registered=={maintenance['pid']}
  try:owner.wait([entry],5,cutoff=cutoff)
  except AssertionError as error:
   assert str(error)=='command left an adopted descendant'
  else:raise AssertionError('shared owner accepted an actual detached maintenance child')
  assert entry['result'] is None and same_lifetime(t.process_info(maintenance['pid']),maintenance)
  observed['baseline_detached_maintenance_refused']=True
 else:
  assert mode=='corrected'
  t.commit_private_source(owner,source)
  assert not ready.exists(),'command-local policy still launched automatic maintenance'
  assert git('show','HEAD:fixture.txt')==b'controlled fixture bytes\n'
  assert git('log','-1','--format=%s').strip()==b'controlled private base'
  assert git('config','--get','maintenance.auto').strip()==b'true'
  assert git('config','--get','maintenance.autoDetach').strip()==b'true'
  assert set(children_of(os.getpid(),owner.deadline))=={peer['process'].pid}
  observed['corrected_commit']=True
  observed['repository_policy_unchanged']=True
 current=t.process_info(peer['process'].pid)
 assert same_lifetime(current,original_peer) and current['parent']==original_peer['parent']==os.getpid()
 assert exit_info(peer['process'].pid) is None
 observed['same_live_peer_retained']=True
finally:
 try:release.write_text('release')
 finally:owner.cleanup(time.monotonic()+t.FINAL_CLEANUP_SECONDS)
assert kernel_has_no_children() and all(item['process'].returncode is not None for item in owner.commands)
for original in (maintenance,hook_identity):
 if original is not None:
  assert not same_lifetime(t.process_info(original['pid']),original)
  try:exit_info(original['pid'])
  except ChildProcessError:pass
  else:raise AssertionError('original detached lifetime was not reaped')
observed['all_owned_children_reaped']=True
print(json.dumps(observed,sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in modes:
                with self.subTest(mode=mode, git_series=git_series):
                    location = root / mode
                    location.mkdir()
                    result = subprocess.run(
                        [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS), str(location), mode],
                        env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1600:])
                    self.assertEqual(json.loads(result.stdout), {
                        "baseline_detached_maintenance_refused": mode == "baseline",
                        "corrected_commit": mode == "corrected",
                        "same_live_peer_retained": True,
                        "repository_policy_unchanged": mode == "corrected",
                        "all_owned_children_reaped": True,
                    })

    def test_http_reply_requires_a_committed_shared_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.sqlite3"
            connection = database(path)
            try:
                reserve(connection, 1, committed=False)
                model = fixture()
                model.reservation_path = path
                model.start()
                try:
                    code, _ = post(model, public_body())
                    self.assertEqual(code, 400)
                    self.assertEqual(model.requests, [])
                    self.assertEqual(model.reservations, {})
                finally:
                    model.close(time.monotonic() + 5)
                connection.commit()
                model = fixture()
                model.reservation_path = path
                model.start()
                try:
                    code, response = post(model, public_body())
                    self.assertEqual(code, 200)
                    self.assertEqual(response["choices"][0]["finish_reason"], "stop")
                    self.assertEqual(json.loads(response["choices"][0]["message"]["content"]),
                                     trial.PUBLIC_OUTPUTS["proposal"])
                    self.assertEqual(len(model.reservations), 1)
                    self.assertEqual(model.metrics[0]["committed_reservation_observed"], True)
                finally:
                    model.close(time.monotonic() + 5)
            finally:
                connection.close()

    def test_reservation_observer_rejects_extra_unresolved_and_rewritten_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.sqlite3"
            connection = database(path)
            try:
                reserve(connection, 1)
                first = trial.reservation_arrival(path, {})
                reserve(connection, 2)
                with self.assertRaisesRegex(AssertionError, "preceding"):
                    trial.reservation_arrival(path, {first[:3]: first})
                connection.execute("UPDATE calls SET status='complete',response='{}' WHERE request_id='request-1'")
                connection.commit()
                second = trial.reservation_arrival(path, {first[:3]: first})
                self.assertEqual(second[2], "request-2")
                connection.execute("UPDATE calls SET payload_hash=? WHERE request_id='request-1'", ("f" * 64,))
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "preceding"):
                    trial.reservation_arrival(path, {first[:3]: first})
            finally:
                connection.close()


    def test_fixture_failure_observation_distinguishes_rejected_body_and_reserved_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = database(root / "calls.sqlite3")
            model = fixture()
            model.reservation_path = root / "calls.sqlite3"
            model.start()
            try:
                reserve(connection, 1)
                payload = public_body()
                payload["messages"][0]["content"] = "PRIVATE_WRONG_PERSONA_CANARY"
                code, response = post(model, payload)
                self.assertEqual((code, response), (
                    400, {"error": "managed public fixture rejected the request"},
                ))
                observed = failure_observation_after_response(model)
                self.assertEqual(observed, {
                    "available": True,
                    "first_rejection_label": "public model did not use its exact approved persona",
                    "error_count": 1, "error_count_truncated": False,
                    "accepted_requests": 0, "reservation_observations": 1,
                    "accepted_public_requests": 0, "root_role_progress": 0,
                    "child_role_progress": {"left": 0, "right": 0},
                })
                self.assertEqual(connection.execute(
                    "SELECT status,response FROM calls"
                ).fetchall(), [("reserved", None)])
                self.assertFalse(model.requests)
                # A later error cannot replace the already-frozen first cause.
                with model.lock:
                    model.errors.insert(0, "PRIVATE_LATER_CLEANUP_CANARY")
                    model.root_turns = 5
                self.assertEqual(observed["root_role_progress"], 0)
                error = AssertionError("managed public model fixture rejected a request")
                cleanup = {"confirmed": True}
                value = trial.write_failure_diagnostic(
                    root, error, [], {}, set(), action_elapsed=1.0, cleanup=cleanup,
                    fixture_observation=observed,
                )
                self.assertEqual(value["error"], error.args[0])
                self.assertIs(value["passed"], False)
                self.assertIs(value["full_run_completed"], False)
                self.assertEqual(value["model_fixture"], observed)
                self.assertEqual(json.loads((root / "diagnostics.json").read_bytes()), value)
                self.assertEqual((root / "diagnostics.json").stat().st_mode & 0o777, 0o600)
                self.assertFalse((root / "report.json").exists())
                exported = trial.canonical(value).decode("ascii")
                for private in ("PRIVATE_WRONG_PERSONA_CANARY", "PRIVATE_LATER_CLEANUP_CANARY",
                                model.token, "owned-worker", "original-attempt", "request-1"):
                    self.assertNotIn(private, exported)
            finally:
                model.close(time.monotonic() + 5)
                connection.close()

    def test_fixture_failure_observation_never_exports_unknown_or_secret_errors(self):
        errors = (
            AssertionError("PRIVATE_EXCEPTION_CANARY /private/path credential=value"),
            AssertionError("managed fixture credential changed PRIVATE_SUFFIX_CANARY"),
            RuntimeError("PRIVATE_RUNTIME_EXCEPTION_CANARY"),
        )
        for error in errors:
            with self.subTest(error_kind=type(error).__name__):
                model = fixture().start()
                try:
                    # A named failing reply callback still crosses the actual HTTP
                    # handler. No request text or arbitrary exception becomes a label.
                    with patch.object(model, "_public_reply", side_effect=error):
                        self.assertEqual(post(model, public_body())[0], 400)
                    observed = failure_observation_after_response(model)
                    self.assertEqual(observed["first_rejection_label"], (
                        "unknown fixture rejection" if isinstance(error, AssertionError)
                        else "managed fixture internal failure"
                    ))
                    self.assertEqual(observed["accepted_requests"], 0)
                    self.assertEqual(observed["reservation_observations"], 0)
                    self.assertEqual(observed["error_count"], 1)
                    exported = trial.canonical(observed).decode("ascii")
                    for private in (str(error), "PRIVATE_", "/private/path", model.token):
                        self.assertNotIn(private, exported)
                    with model.lock:
                        model.errors[:] = ["PRIVATE_COUNT_CANARY"] * 65
                        model.requests[:] = [public_body()] * 14
                        model.root_turns = True
                    bounded = failure_observation_after_response(model)
                    self.assertEqual(bounded["error_count"], 64)
                    self.assertIs(bounded["error_count_truncated"], True)
                    self.assertIsNone(bounded["accepted_requests"])
                    self.assertIsNone(bounded["root_role_progress"])
                    self.assertNotIn("PRIVATE_", trial.canonical(bounded).decode("ascii"))
                finally:
                    model.close(time.monotonic() + 5)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT")
                         and hasattr(signal, "ITIMER_REAL"), "Linux bounded failure cleanup")
    def test_fixture_failure_snapshot_never_waits_or_replaces_primary_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processes = trial.Processes(root)
            events = []
            owner = NamedCleanupOwner(events, fail=True)
            model = fixture().start()
            original = AssertionError("original installed scene failure")
            try:
                manager = manager_fixture(processes, root)
                model.lock.acquire()
                try:
                    # A blocking snapshot would hit the real timer and return the
                    # wrong fallback. This test cannot hang on its own held lock.
                    with trial.ActionFence(time.monotonic() + 0.5):
                        observed = trial.fixture_failure_observation(model)
                    self.assertEqual(observed, {
                        "available": False, "reason": "fixture_lock_busy",
                    })
                finally:
                    model.lock.release()
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                with model.lock:
                    model.errors.append("PRIVATE_AFTER_SNAPSHOT_CANARY")
                cleanup = trial.close_trial(
                    processes, owner, None, model, installation=manager, original=original,
                )
                self.assertEqual(manager.returncode, 130)
                self.assertFalse(processes.running)
                self.assertTrue(all(handle.closed for handle in processes.handles))
                self.assertFalse(model.thread.is_alive())
                self.assertTrue(model.release_root.is_set())
                self.assertEqual(events, ["containment"])
                self.assertEqual(original.args, ("original installed scene failure",))
                self.assertIn("owned-final-containment", "\n".join(original.__notes__))
                value = trial.write_failure_diagnostic(
                    root, original, [], {}, set(), action_elapsed=0.5, cleanup=cleanup,
                    fixture_observation=observed,
                )
                self.assertEqual(value["model_fixture"], {
                    "available": False, "reason": "fixture_lock_busy",
                })
                self.assertEqual(value["error"], "original installed scene failure")
                self.assertEqual(value["cleanup_notes"], original.__notes__)
                self.assertNotIn("PRIVATE_", trial.canonical(value).decode("ascii"))
                self.assertEqual(json.loads((root / "diagnostics.json").read_bytes()), value)
                self.assertFalse((root / "report.json").exists())
                self.assertEqual((root / "diagnostics.json").stat().st_mode & 0o777, 0o600)
            finally:
                model.close(time.monotonic() + 5)
                processes.close()

    def test_http_fixture_refuses_duplicate_public_effect_and_wrong_credential(self):
        model = fixture()
        model.start()
        try:
            self.assertEqual(post(model, public_body())[0], 200)
            self.assertEqual(post(model, public_body())[0], 400)
            self.assertEqual(len(model.requests), 1)
            self.assertTrue(model.errors)
        finally:
            model.close(time.monotonic() + 5)
        model = fixture()
        model.start()
        try:
            self.assertEqual(post(model, public_body(), token="not-approved")[0], 400)
            self.assertEqual(model.requests, [])
        finally:
            model.close(time.monotonic() + 5)


    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux original peer lifetimes")
    def test_live_peer_refuses_an_original_exit_marker_discovered_during_tick(self):
        from check_public_discussion_stack import OwnedTrial
        import check_public_discussion_stack as shared

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processes = []
            try:
                for _ in range(3):
                    processes.append(subprocess.Popen(
                        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                        env=trial.PROFILE_ENVIRONMENT, start_new_session=True,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    ))
                live = {
                    name: trial.process_info(process.pid)
                    for name, process in zip(("profile", "node"), processes[1:])
                }
                self.assertTrue(all(value is not None and value["state"] not in {"Z", "X"}
                                    for value in live.values()))
                config, scope = root / "profile.json", root / "scope"
                config.write_bytes(b"original profile binding")
                scope.write_bytes(b"original private scope bytes")
                hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (config, scope)}
                original_read = shared.bounded_file

                for kind in ("profile", "node"):
                    with self.subTest(marked_role=kind):
                        # Real otherwise-live process identities and real binding
                        # files establish the positive. Only the denied read and
                        # PF_EXITING stat below are named source-join projections;
                        # the separate C tests supply actual kernel EACCES.
                        owner = object.__new__(OwnedTrial)
                        owner.closing = False
                        owner.deadline = time.monotonic() + 10
                        original_deadline = owner.deadline
                        observed = {
                            (value["pid"], value["starttime"]): copy.deepcopy(value) | {"kind": name}
                            for name, value in live.items()
                        }
                        target = live[kind]
                        key = (target["pid"], target["starttime"])
                        different_lifetime = (key[0], str(int(key[1]) + 1))
                        entry = {"process": processes[0], "observed": copy.deepcopy(observed),
                                 "exiting_observations": {different_lifetime}}
                        peer = object.__new__(trial.IndependentPeer)
                        peer.owner, peer.entry, peer.live_identity = owner, entry, copy.deepcopy(live)
                        peer.config, peer.scope_file = config, scope
                        peer.config_hash, peer.scope_hash = hashes[config], hashes[scope]
                        scheduled = [False]
                        ticks = []

                        def projected_stat(path, maximum):
                            raw = original_read(path, maximum)
                            if str(path) == f"/proc/{key[0]}/stat":
                                prefix, tail = raw.rsplit(b")", 1)
                                fields = tail.split()
                                # This is not an observed pre-Z exit. It models
                                # that exact guard input while the real task lives.
                                fields[0] = b"S"
                                fields[6] = str(int(fields[6]) | 4).encode("ascii")
                                return prefix + b") " + b" ".join(fields) + b"\n"
                            return raw

                        def denied_read():
                            raise PermissionError(13, "named terminal metadata projection")

                        def tick(*, cutoff):
                            ticks.append(cutoff)
                            if scheduled[0]:
                                scheduled[0] = False
                                with patch.object(shared, "bounded_file", side_effect=projected_stat):
                                    value = owner._observe_metadata(
                                        entry, target, kind + "_environment", denied_read,
                                    )
                                self.assertIsNone(value)
                                self.assertIn(key, entry["exiting_observations"])

                        owner.tick = tick
                        self.assertEqual(peer.check_live(), live)
                        self.assertEqual(entry["exiting_observations"], {different_lifetime})
                        scheduled[0] = True
                        with self.assertRaisesRegex(AssertionError, "replaced or stopped"):
                            peer.check_live()
                        # A guard evaluated only before tick would miss the new
                        # marker and incorrectly accept these same live identities.
                        self.assertFalse(scheduled[0])
                        self.assertEqual(entry["exiting_observations"], {different_lifetime, key})
                        self.assertEqual(entry["observed"], observed)
                        self.assertTrue(all(process.poll() is None for process in processes))
                        self.assertTrue(all(trial.process_info(value["pid"])["state"] not in {"Z", "X"}
                                            for value in live.values()))
                        with self.assertRaisesRegex(AssertionError, "replaced or stopped"):
                            peer.check_live()
                        self.assertEqual(entry["exiting_observations"], {different_lifetime, key})
                        self.assertEqual(entry["observed"], observed)
                        self.assertEqual(ticks, [original_deadline] * 3)
                        self.assertEqual(owner.deadline, original_deadline)
                        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest()
                                          for path in hashes}, hashes)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            self.assertTrue(all(process.returncode is not None for process in processes))
            for process in processes:
                with self.assertRaises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)

    def test_public_body_excludes_tools_private_data_and_unselected_records(self):
        original = proposal()
        correct = original["slot"]["receipt"]["record"]
        valid = public_body("criticism", [correct])
        changed = []
        with_tool = copy.deepcopy(valid)
        with_tool["tools"] = []
        changed.append(with_tool)
        with_private = copy.deepcopy(valid)
        with_private["messages"][1]["content"] = json.dumps({
            "instruction": trial.PUBLIC_INSTRUCTIONS["criticism"],
            "public_context": [correct], "private": trial.PRIVATE_INSTRUCTIONS["left"],
        })
        changed.append(with_private)
        other = copy.deepcopy(correct)
        other["body"]["content"] = "A different signed-looking contribution"
        other["message_id"] = trial.digest(other["body"])
        changed.append(public_body("criticism", [other]))
        changed.append(public_body("criticism", [correct, other]))
        for payload in changed:
            with self.subTest(payload=trial.digest(payload)):
                model = fixture()
                model.observations["proposal"] = {"proposal": original}
                try:
                    with self.assertRaises(AssertionError):
                        model.reply(payload)
                    self.assertEqual(model.requests, [])
                finally:
                    model.close(time.monotonic() + 5)
        model = fixture()
        model.observations["proposal"] = {"proposal": original}
        try:
            response = model.reply(valid)
            self.assertEqual(json.loads(response["content"]), trial.PUBLIC_OUTPUTS["criticism"])
            self.assertEqual(set(model.public_calls), {"criticism"})
        finally:
            model.close(time.monotonic() + 5)

    def test_followup_answers_the_original_public_question_and_retains_both_declines(self):
        original = proposal()
        root_record = original["slot"]["receipt"]["record"]
        proposal_id = root_record["message_id"]
        left = record("criticism", actor=status("33"), reply_to=proposal_id,
                      request_key="oj-public:" + "b" * 64)
        right = record("dissent", actor=status("44"), reply_to=proposal_id,
                       request_key="oj-public:" + "c" * 64)
        invitation = record(actor=status(), target=status("22")["author"],
                            request_key="oj-public-invite:original")
        invitation["body"].update(kind="invite", content=trial.INVITATION)
        invitation["message_id"] = trial.digest(invitation["body"])
        peer = record("dissent", actor=status("22"), reply_to=invitation["message_id"],
                      request_key="independent-original-decline")
        peer["body"]["content"] = trial.PEER_DECLINE
        peer["message_id"] = trial.digest(peer["body"])
        question = (
            "The proposal should not equate agreement with correctness; preserve my criticism. "
            "Does considering contributions separately mean either can be rejected independently?"
        )
        answer = (
            "Yes, either contribution can be rejected independently. I retain the left child's criticism, "
            "the right child's decline and the independent peer's decline as distinct contributions; "
            "I do not claim consensus."
        )
        # Independent literals make an old question-free/answer-free fixture fail.
        self.assertEqual(left["body"]["content"], question)
        self.assertEqual(len({item["body"]["author"] for item in (root_record, left, right, peer)}), 4)
        selected = [root_record, left, right, peer]
        observed = {
            "proposal": original,
            "public_slots": {"left": {"slot": {"receipt": receipt(left)}},
                             "right": {"slot": {"receipt": receipt(right)}}},
            "peer_decline": peer, "history": [root_record, invitation, left, right, peer],
        }
        changed_question = copy.deepcopy(left)
        changed_question["body"]["content"] = "Can agreement substitute for independent assessment?"
        changed_question["message_id"] = trial.digest(changed_question["body"])
        # _root_result reaches this exact-content guard before a follow-up.
        # Coherent record/receipt rewrites must fail even without a history mismatch.
        trial.terminal_receipt(receipt(left), kind="message", content=question,
                               author=left["body"]["author"], reply_to=proposal_id)
        for replacement in (
            "The proposal should not equate agreement with correctness; preserve my criticism.",
            changed_question["body"]["content"],
        ):
            with self.subTest(question=replacement):
                rewritten = copy.deepcopy(left)
                rewritten["body"]["content"] = replacement
                rewritten["message_id"] = trial.digest(rewritten["body"])
                with self.assertRaisesRegex(AssertionError, "declared contribution"):
                    trial.terminal_receipt(receipt(rewritten), kind="message", content=question,
                                           author=left["body"]["author"], reply_to=proposal_id)
        # These HTTP negatives separately bind the already retained selection.
        for mode, records in (
            ("missing-question", [root_record, right, peer]),
            ("rehashed-question", [root_record, changed_question, right, peer]),
            ("missing-child-decline", [root_record, left, peer]),
            ("missing-peer-decline", [root_record, left, right]),
        ):
            with self.subTest(mode=mode):
                model = fixture()
                model.observations["observe"] = copy.deepcopy(observed)
                model.start()
                try:
                    self.assertEqual(post(model, public_body("followup", records))[0], 400)
                    self.assertEqual(model.requests, [])
                    self.assertEqual(model.public_calls, {})
                finally:
                    model.close(time.monotonic() + 5)
        model = fixture()
        model.observations["observe"] = copy.deepcopy(observed)
        model.start()
        try:
            code, response = post(model, public_body("followup", selected))
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(response["choices"][0]["message"]["content"]),
                             {"kind": "message", "content": answer})
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(set(model.public_calls), {"followup"})
            self.assertEqual(model.public_calls["followup"]["context_sha256"], trial.digest(selected))
        finally:
            model.close(time.monotonic() + 5)
        followup = record("followup", reply_to=proposal_id, request_key="oj-public:" + "d" * 64)
        trial.terminal_receipt(receipt(followup), kind="message", content=answer,
                               author=root_record["body"]["author"], reply_to=proposal_id)
        for replacement in (
            "I retain the criticism and both declines as distinct contributions; I do not claim consensus.",
            "Yes, either contribution can be rejected independently. All participants now agree.",
        ):
            with self.subTest(replacement=replacement):
                rewritten = copy.deepcopy(followup)
                rewritten["body"]["content"] = replacement
                rewritten["message_id"] = trial.digest(rewritten["body"])
                with self.assertRaisesRegex(AssertionError, "declared contribution"):
                    trial.terminal_receipt(receipt(rewritten), kind="message", content=answer,
                                           author=root_record["body"]["author"], reply_to=proposal_id)

    def test_terminal_receipt_requires_original_hash_scope_kind_and_reply(self):
        value = proposal()
        trial.slot_value(value, "proposal", terminal=True)
        for mode in ("pending", "different-kind", "different-reply", "receipt-copy", "private"):
            changed = copy.deepcopy(value)
            slot = changed["slot"]
            record_value = slot["receipt"]["record"]
            if mode == "pending":
                slot.update(state="publication_pending", receipt=None)
            elif mode == "different-kind":
                record_value["body"]["kind"] = "decline"
                record_value["message_id"] = trial.digest(record_value["body"])
                slot["receipt"]["message_id"] = record_value["message_id"]
            elif mode == "different-reply":
                record_value["body"]["reply_to"] = "f" * 64
                record_value["message_id"] = trial.digest(record_value["body"])
                slot["receipt"]["message_id"] = record_value["message_id"]
            elif mode == "receipt-copy":
                slot["receipt"]["author"] = status("22")["author"]
            else:
                record_value["body"]["content"] = trial.PRIVATE_SUMMARIES["left"]
                record_value["message_id"] = trial.digest(record_value["body"])
                slot["receipt"]["message_id"] = record_value["message_id"]
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                trial.slot_value(changed, "proposal", terminal=True)

    def test_slot_progress_preserves_original_cutoff_selection_and_bound_identity(self):
        current = proposal()["slot"]
        offered = copy.deepcopy(current)
        offered.update(state="offered", context_digest=None, model_call_digest=None, receipt=None)
        offered["actor"].update(binding_digest=None, status=None)
        trial.same_slot(offered, current)
        closed = copy.deepcopy(current)
        closed["actor"]["admission_closed"] = True
        trial.same_slot(current, trial.slot_value(
            {"slot": closed, "execution_authorized": False}, "proposal", terminal=True,
        ))
        for mode in ("deadline", "selection", "actor", "receipt", "reopen"):
            before, after = copy.deepcopy(current), copy.deepcopy(current)
            if mode == "deadline":
                after["expires_at"] += 30
            elif mode == "selection":
                after["selected_message_ids"] = ["f" * 64]
            elif mode == "actor":
                after["actor"]["status"] = status("22")
                after["actor"]["binding_digest"] = "f" * 64
            elif mode == "receipt":
                after["receipt"]["record"]["body"]["content"] = "Replacement"
                item = after["receipt"]["record"]
                item["message_id"] = trial.digest(item["body"])
                after["receipt"]["message_id"] = item["message_id"]
            else:
                before["actor"]["admission_closed"] = True
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                trial.same_slot(before, after)

    def test_fixture_record_publication_is_complete_and_cannot_replace_prior_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observation.json"
            original_link = os.link
            observations = []

            def link(source, destination, **kwargs):
                self.assertFalse(Path(destination).exists())
                self.assertEqual(json.loads(Path(source).read_bytes()), {"stage": "complete"})
                self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
                result = original_link(source, destination, **kwargs)
                observations.append(trial.optional_json(path))
                return result

            with patch.object(os, "link", side_effect=link):
                trial.atomic_json(path, {"stage": "complete"})
            self.assertEqual(observations, [{"stage": "complete"}])
            self.assertEqual(trial.optional_json(path), {"stage": "complete"})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                trial.atomic_json(path, {"stage": "replacement"})
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_name("observation.json.writing").exists())

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_private_fixture_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fifo"
            os.mkfifo(path, 0o600)
            program = (
                "import sys;sys.path.insert(0,sys.argv[1]);"
                "from pathlib import Path;import check_managed_public_stack as t\n"
                "try:t.optional_json(Path(sys.argv[2]))\n"
                "except AssertionError:raise SystemExit(0)\n"
                "raise SystemExit(3)\n"
            )
            result = subprocess.run([sys.executable, "-c", program, str(CONTRACTS), str(path)],
                                    env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_scoped_http_trickle_is_cut_off_and_exact_reader_is_reaped(self):
        # Every body chunk arrives well inside urllib's three-second inactivity
        # timeout. The caller's original, shorter cutoff must still terminate it.
        entered, stopped = threading.Event(), threading.Event()
        chunks, children, received = [], [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.connection.settimeout(0.25)
                self.send_response(200)
                self.send_header("Content-Length", "200")
                self.end_headers()
                entered.set()
                for index in range(200):
                    if stopped.is_set():
                        break
                    try:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        chunks.append(index)
                    except OSError:
                        break
                    if stopped.wait(0.05):
                        break

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        client = trial.Client(f"http://127.0.0.1:{server.server_port}", "private-fixture-bearer")
        real_popen = subprocess.Popen

        def launched(argv, **options):
            self.assertEqual(argv[:4], [sys.executable, "-I", "-S", "-c"])
            self.assertEqual(argv[4:], [trial.HTTP_READ])
            self.assertNotIn(client.token, "\n".join(argv))
            self.assertNotIn(client.token, repr(options.get("env")))
            process = real_popen(argv, **options)
            children.append(process)
            return process

        try:
            with patch.object(trial.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(AssertionError, "deadline expired"):
                    trial.http_observation(client, "/trickle", {}, cutoff=time.monotonic() - 1)
                launch.assert_not_called()
            started = time.monotonic()
            with patch.object(trial.subprocess, "Popen", side_effect=launched):
                with self.assertRaisesRegex(AssertionError, "deadline expired"):
                    trial.http_observation(client, "/trickle", {}, cutoff=started + 2)
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(entered.is_set())
            self.assertGreaterEqual(len(chunks), 2)
            self.assertLess(len(chunks), 200)
            self.assertEqual(received, ["Bearer " + client.token])
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].returncode, -signal.SIGKILL)
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0].pid, os.WNOHANG)
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            for process in children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_live_private_read_requires_existent_original_bytes_and_exact_role_denial(self):
        scope = {"deployment_id": "owned", "repository": "github:github.com/fixture/repo"}
        child_id, attempt = "child_" + "1" * 32, "original-attempt"
        instructions = trial.PRIVATE_INSTRUCTIONS["left"]
        binding = {"child_id": child_id, "instructions_digest": trial.text_hash(instructions)}
        query = scope | {"attempt_id": attempt}
        value = {"schema_version": 1, **scope, "root_attempt_id": attempt, "children": [
            {"binding": binding, "child": {"id": child_id, "instructions": instructions},
             "instructions": instructions},
        ]}
        mode = {"name": "valid"}

        def respond(token, path, actual_query):
            self.assertEqual(path, "/managed/children/" + child_id)
            self.assertEqual(actual_query, {name: [item] for name, item in query.items()})
            if token == "Bearer ingress":
                return (404 if mode["name"] == "missing-denial" else 403), b"forbidden"
            if mode["name"] == "missing-positive":
                return 404, b"absent"
            response = copy.deepcopy(value)
            if mode["name"] == "changed-private":
                response["children"][0]["instructions"] = "changed"
            if mode["name"] == "boolean-schema":
                response["schema_version"] = True
            return 200, response

        with scope_server(respond) as (authority, ingress, requests):
            result = trial.live_private_scope(authority, ingress, scope, attempt, child_id, binding, instructions,
                                              cutoff=time.monotonic() + 10)
            self.assertEqual((result["runtime_status"], result["ingress_status"]), (200, 403))
            self.assertEqual(len(requests), 2)
            for name in ("missing-positive", "changed-private", "boolean-schema", "missing-denial"):
                mode["name"] = name
                with self.subTest(mode=name), self.assertRaises(AssertionError):
                    trial.live_private_scope(authority, ingress, scope, attempt, child_id, binding, instructions,
                                              cutoff=time.monotonic() + 10)

    def test_final_private_scope_requires_both_positive_controls_and_source_owned409(self):
        scope = {"deployment_id": "owned", "repository": "github:github.com/fixture/repo"}
        child_id, attempt = "child_" + "2" * 32, "original-attempt"
        summary = trial.PRIVATE_SUMMARIES["left"]
        child = json.dumps({"work_outcome": {"summary": summary}})
        root = json.dumps({"kind": "root", "manifest": "owned"})
        child_hash, root_hash = trial.text_hash(child), trial.text_hash(root)
        child_path = "/managed/children/" + child_id + "/artifacts/" + child_hash
        mode = {"name": "valid"}

        def respond(token, path, query):
            if path == child_path:
                self.assertEqual(query, {name: [item] for name, item in (scope | {"attempt_id": attempt}).items()})
                if token == "Bearer runtime":
                    return (404, b"absent") if mode["name"] == "missing-child" else (200, child.encode())
                return (404 if mode["name"] == "404-denial" else 403), b"forbidden"
            self.assertEqual(query, {name: [item] for name, item in scope.items()})
            self.assertEqual(token, "Bearer ingress")
            if path == "/managed/artifacts/" + child_hash:
                return 409, {"error": "other reason" if mode["name"] == "different409" else
                             "artifact is outside the authorized scope"}
            if path == "/managed/artifacts/" + root_hash:
                return (404, b"absent") if mode["name"] == "missing-root" else (200, root.encode())
            return 404, b"unknown"

        with scope_server(respond) as (authority, ingress, requests):
            result = trial.final_private_scope(authority, ingress, scope, attempt, child_id, child, root, summary,
                                               cutoff=time.monotonic() + 10)
            self.assertEqual([result[name] for name in (
                "runtime_child_status", "ingress_child_status", "ingress_root_route_child_status",
                "ingress_root_artifact_status",
            )], [200, 403, 409, 200])
            self.assertEqual(len(requests), 4)
            for name in ("missing-child", "404-denial", "different409", "missing-root"):
                mode["name"] = name
                with self.subTest(mode=name), self.assertRaises(AssertionError):
                    trial.final_private_scope(authority, ingress, scope, attempt, child_id, child, root, summary,
                                               cutoff=time.monotonic() + 10)


    def test_bounded_client_preserves_actual_post_and_original_owner_cutoff(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(1)
                value = trial.object_value(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers.get("Authorization"), value))
                raw = trial.canonical({"echo": value})
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        try:
            owner = SimpleNamespace(deadline=time.monotonic() + 5)
            client = trial.BoundedClient(f"http://127.0.0.1:{server.server_port}", "original", owner)
            payload = {"schema_version": 1, "requirements": "exact UTF-8: fråga", "enabled": False}
            self.assertEqual(client.request("POST", "/enroll", query={"scope": "original space"}, data=payload),
                             {"echo": payload})
            self.assertEqual(received, [
                ("/enroll?scope=original+space", "Bearer original", payload),
            ])
            owner.deadline = time.monotonic() - 1
            with self.assertRaisesRegex(AssertionError, "deadline expired"):
                client.request("POST", "/enroll", data=payload)
            self.assertEqual(len(received), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_fixture_close_interrupts_owned_incomplete_header_and_retains_error(self):
        model = fixture().start()
        connection = socket.create_connection(("127.0.0.1", model.server.server_port), timeout=1)
        try:
            connection.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\n")
            cutoff = time.monotonic() + 2
            observed = False
            while time.monotonic() < cutoff:
                with model.lock:
                    observed = model.active_connection is not None
                if observed:
                    break
                time.sleep(0.01)
            self.assertTrue(observed)
            model.close(time.monotonic() + 2)
            self.assertFalse(model.thread.is_alive())
            self.assertTrue(model.errors)
            self.assertFalse(model.requests)
        finally:
            connection.close()
            model.close(time.monotonic() + 2)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_success_report_follows_handled_manager_and_all_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processes = trial.Processes(root)
            events = []
            owner, peer = NamedCleanupOwner(events), NamedCleanupPeer(events, root)
            model = NamedCleanupFixture(events)
            process = manager_fixture(processes, root)
            try:
                with self.assertRaisesRegex(AssertionError, "original installation process ownership"):
                    trial.stop_installation(processes, object())
                self.assertIsNone(process.poll())
                path = root / "report.json"
                value = trial.publish_report(
                    path, {"passed": True}, processes, owner, peer, model, process, None,
                )
                self.assertEqual(process.returncode, 130)
                self.assertFalse(processes.running)
                self.assertTrue(all(handle.closed for handle in processes.handles))
                self.assertTrue(model.release_root.is_set())
                self.assertEqual(events, [
                    "peer-close", "model-close", "peer-reap", "bootstrap-signal",
                    "bootstrap-wait", "containment",
                ])
                self.assertEqual(value, json.loads(path.read_bytes()))
                self.assertEqual(value["final_owned_cleanup"]["manager_handled_status"], 130)
                self.assertIs(value["final_owned_cleanup"]["confirmed"], True)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                processes.close()

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_unhandled_or_killed_final_manager_leaves_no_success_report(self):
        for killed in (False, True):
            with self.subTest(killed=killed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                processes = trial.Processes(root)
                events = []
                owner, peer = NamedCleanupOwner(events), NamedCleanupPeer(events, root)
                model = NamedCleanupFixture(events)
                code = "import time;time.sleep(30)" if killed else "raise SystemExit(0)"
                process = processes.start("installation", [sys.executable, "-I", "-c", code], root, {})
                try:
                    if killed:
                        os.killpg(process.pid, signal.SIGKILL)
                    self.assertEqual(process.wait(timeout=3), -9 if killed else 0)
                    path = root / "report.json"
                    with self.assertRaisesRegex(AssertionError, "manager-shutdown-unconfirmed"):
                        trial.publish_report(
                            path, {"passed": True}, processes, owner, peer, model, process, None,
                        )
                    self.assertFalse(path.exists())
                    self.assertFalse(processes.running)
                    self.assertTrue(all(handle.closed for handle in processes.handles))
                    self.assertIn("model-close", events)
                    self.assertIn("peer-reap", events)
                    self.assertEqual(events[-1], "containment")
                finally:
                    processes.close()

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux owned-process qualification")
    def test_cleanup_failures_preserve_primary_and_attempt_remaining_owned_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processes = trial.Processes(root)
            events = []
            owner = NamedCleanupOwner(events, fail=True)
            peer = NamedCleanupPeer(events, root)
            model = NamedCleanupFixture(events, fail=True)
            process = manager_fixture(processes, root)
            original = AssertionError("original installed scene failure")
            try:
                trial.close_trial(processes, owner, peer, model, installation=process, original=original)
                self.assertEqual(original.args, ("original installed scene failure",))
                notes = "\n".join(original.__notes__)
                self.assertIn("model-fixture-close", notes)
                self.assertIn("owned-final-containment", notes)
                self.assertNotIn("PRIVATE_", notes)
                self.assertEqual(process.returncode, 130)
                self.assertFalse(processes.running)
                self.assertTrue(all(handle.closed for handle in processes.handles))
                self.assertEqual(events, ["model-close", "containment"])
            finally:
                processes.close()



    def test_private_wal_rows_bind_original_exec_and_detect_all_row_or_coherent_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = PrivateExecutionFixture(directory)
            try:
                observed_files = [scene.path, Path(str(scene.path) + "-wal")]
                before = {str(path): path.read_bytes() for path in observed_files}
                original = scene.read()
                self.assertEqual({str(path): path.read_bytes() for path in observed_files}, before)
                self.assertEqual({row["exit_code"] for row in original.values()}, {0})
                scene.capture()
                self.assertEqual(scene.read(final=True), original)
                scene.db.execute("UPDATE children SET exec_id='replacement' WHERE ordinal=1")
                scene.db.commit()
                self.assertNotEqual(scene.read(final=True), original)
                scene.db.execute("UPDATE children SET exec_id='exec-right' WHERE ordinal=1")
                scene.db.execute("UPDATE children SET armed_deadline=188.0 WHERE ordinal=1")
                scene.db.commit()
                self.assertNotEqual(scene.read(final=True), original)
            finally:
                scene.close()
        changes = [
            "UPDATE children SET exit_code=2 WHERE ordinal=1",
            "UPDATE children SET armed_deadline=deadline+1 WHERE ordinal=1",
            "UPDATE children SET capture='{}' WHERE ordinal=1",
            "UPDATE intents SET delivery_key='polyphony-child:'||child_id",
            "INSERT INTO intents VALUES('foreign-worker','foreign-key','{}','foreign-delivery','foreign-child','bound')",
            "INSERT INTO children(child_id,worker_id,ordinal,uid,grant_json,deadline,boot_id) "
            "VALUES('foreign-child','foreign-worker',0,60000,'{}',100,'foreign-boot')",
        ]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                scene = PrivateExecutionFixture(directory)
                try:
                    scene.db.execute(change)
                    scene.db.commit()
                    before = scene.db.execute("SELECT * FROM children ORDER BY child_id").fetchall()
                    with self.assertRaises(AssertionError):
                        scene.read()
                    self.assertEqual(scene.db.execute("SELECT * FROM children ORDER BY child_id").fetchall(), before)
                finally:
                    scene.close()

    def test_public_slot_digests_bind_shared_rows_and_original_held_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.sqlite3"
            connection = database(path)
            model = fixture()
            roles = [
                ("ordinary", "root", "1"), ("public", "root", "proposal"),
                ("ordinary", "left", "1"), ("ordinary", "right", "1"),
                ("ordinary", "root", "2"), ("public", "left", "criticism"),
                ("public", "right", "dissent"), ("ordinary", "left", "2"),
                ("ordinary", "right", "2"), ("ordinary", "root", "3"),
                ("public", "root", "followup"), ("ordinary", "root", "4"),
                ("ordinary", "root", "5"),
            ]
            slots = {}
            try:
                for index, (family, role, turn) in enumerate(roles):
                    request = ("/public/" + turn if family == "public" else
                               "/child/" + str(index) if role != "root" else "root-" + turn)
                    payload_hash = trial.digest([family, role, turn])
                    row = ("owned-worker", "original-attempt", request, payload_hash, 1024,
                           "reserved" if index == 12 else "complete", 0 if index == 12 else 1)
                    model.reservations[row[:3]] = row
                    model.metrics.append({"family": family, "role": role, "turn": turn})
                    connection.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?)",
                                       (*row[:6], None if index == 12 else "{}"))
                    if family == "public":
                        slots[turn] = {"slot": {"model_call_digest": payload_hash}}
                connection.commit()
                model.observations["compose"] = {
                    "proposal": slots["proposal"], "followup": slots["followup"],
                    "public_slots": {"left": slots["criticism"], "right": slots["dissent"]},
                }
                held = trial.common_model_rows(path, model, "owned-worker", "original-attempt", held=True)
                connection.execute("UPDATE calls SET status='complete',response='{}' WHERE request_id='root-5'")
                connection.commit()
                self.assertEqual(trial.common_model_rows(path, model, "owned-worker", "original-attempt"), held)
                with self.assertRaisesRegex(AssertionError, "unresolved original reservation"):
                    trial.common_model_rows(path, model, "owned-worker", "original-attempt", held=True)
                slots["criticism"]["slot"]["model_call_digest"] = "f" * 64
                with self.assertRaisesRegex(AssertionError, "original shared model reservation"):
                    trial.common_model_rows(path, model, "owned-worker", "original-attempt")
                slots["criticism"]["slot"]["model_call_digest"] = trial.digest(["public", "left", "criticism"])
                connection.execute("INSERT INTO calls VALUES('foreign','attempt','extra',?,1024,'complete','{}')",
                                   ("e" * 64,))
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "thirteen actual HTTP arrivals"):
                    trial.common_model_rows(path, model, "owned-worker", "original-attempt")
            finally:
                connection.close()
                model.close(time.monotonic() + 5)



    @unittest.skipUnless(sys.platform == "linux" and hasattr(signal, "ITIMER_REAL"), "POSIX original action fence")
    def test_action_fence_refuses_expired_entry_and_preserves_an_existing_timer(self):
        program = r"""import json,pathlib,signal,sys,time
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
def previous(signum,frame):raise RuntimeError('pre-existing timer fired')
original=signal.signal(signal.SIGALRM,previous)
entered=[];expired=False;active=False
try:
 try:
  with t.ActionFence(time.monotonic()-1):entered.append('expired')
 except t.ActionDeadline:expired=True
 assert signal.getsignal(signal.SIGALRM) is previous
 assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)
 signal.setitimer(signal.ITIMER_REAL,5.0)
 before=signal.getitimer(signal.ITIMER_REAL)[0]
 try:
  with t.ActionFence(time.monotonic()+1):entered.append('active')
 except AssertionError as error:
  active=str(error)=='managed public action fence refuses an existing process timer'
 after=signal.getitimer(signal.ITIMER_REAL)[0]
 assert 0<after<=before<=5
 assert signal.getsignal(signal.SIGALRM) is previous
 print(json.dumps({'expired':expired,'active_refused':active,'entered':entered,
                   'original_timer_preserved':True,'handler_preserved':True}))
finally:
 signal.setitimer(signal.ITIMER_REAL,0)
 signal.signal(signal.SIGALRM,original)
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS)],
            env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1000:])
        self.assertEqual(json.loads(result.stdout), {
            "expired": True, "active_refused": True, "entered": [],
            "original_timer_preserved": True, "handler_preserved": True,
        })

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT")
                         and hasattr(signal, "ITIMER_REAL"), "Linux original action fence and owned reaping")
    def test_real_action_fence_stops_later_action_and_preserves_failure_through_owned_reap(self):
        program = r"""import json,os,pathlib,signal,sys,time
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
directory=pathlib.Path(sys.argv[2])
owner=t.OwnedTrial(dedicated_process=True)
processes=t.Processes(directory)
def previous(signum,frame):raise RuntimeError('unexpected prior handler')
old=signal.signal(signal.SIGALRM,previous)
process=None;original=None
cutoff=time.monotonic()+1.5
try:
 try:
  with t.ActionFence(cutoff):
   marker=directory/'ready'
   child_code=("import pathlib,signal,time\n"
               "def stop(*args):raise SystemExit(130)\n"
               "signal.signal(signal.SIGTERM,stop)\n"
               "pathlib.Path("+repr(str(marker))+").write_text('ready')\n"
               "while True:time.sleep(0.01)\n")
   process=processes.start('installation',[sys.executable,'-I','-S','-c',child_code],directory,{})
   while not marker.exists():
    assert process.poll() is None
    time.sleep(.01)
   time.sleep(10)
   (directory/'forbidden-after-cutoff').write_text('bad')
 except BaseException as error:
  original=error
 assert isinstance(original,t.ActionDeadline)
 assert str(original)=='managed public original action budget expired'
 assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)
 assert signal.getsignal(signal.SIGALRM) is previous
 t.close_trial(processes,owner,None,None,installation=process,original=original)
 assert process is not None and process.returncode==130
 assert not processes.running and all(handle.closed for handle in processes.handles)
 assert not (directory/'forbidden-after-cutoff').exists()
 reaped=False
 try:os.waitpid(process.pid,os.WNOHANG)
 except ChildProcessError:reaped=True
 assert reaped
 assert original.args==('managed public original action budget expired',)
 assert not getattr(original,'__notes__',[])
 print(json.dumps({'expired':True,'manager_returncode':130,'reaped':reaped,
                   'after_cutoff_action':False,'original_failure_retained':True,
                   'handler_preserved':True,'timer_disarmed_before_cleanup':True}))
finally:
 signal.setitimer(signal.ITIMER_REAL,0)
 signal.signal(signal.SIGALRM,old)
 for process in list(processes.running.values()):
  if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
  process.wait(timeout=2)
 for handle in processes.handles:handle.close()
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS), directory],
                env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=8,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1000:])
        self.assertEqual(json.loads(result.stdout), {
            "expired": True, "manager_returncode": 130, "reaped": True,
            "after_cutoff_action": False, "original_failure_retained": True,
            "handler_preserved": True, "timer_disarmed_before_cleanup": True,
        })




    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT")
                         and hasattr(signal, "ITIMER_REAL"), "Linux capture cancellation and owned reaping")
    def test_real_action_alarm_escapes_actual_recoverable_capture_parser(self):
        program = r"""import json,os,pathlib,signal,sys,time
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
directory=pathlib.Path(sys.argv[2])
owner=t.OwnedTrial(dedicated_process=True)
processes=t.Processes(directory)
scene=SimpleNamespace(child_ids={'left':'child-left','right':'child-right'},
                      patches={'left':'left patch','right':'right patch'})
captured={}
for actor,uid in (('left',60000),('right',60001)):
 event={'event':'configured_verification','state':'exited','exit_code':0,
        'output':'CHILD_STACK_BOUNDARY_VERIFIED uid='+str(uid)}
 captured['child-'+actor]={'patch':scene.patches[actor],
                          'log':'not JSON\n'+json.dumps(event)+'\n'}
# The real helper first proves that ordinary malformed log text is recoverable.
assert len(t.verify_private_children_log(scene,captured))==2
entered=[]
def pause_actual_parser(frame,event,arg):
 if (event=='call' and frame.f_code is t.object_value.__code__
     and frame.f_locals.get('raw')==b'not JSON'):
  entered.append('actual-object-value')
  # Delay only the real parser call inside the real try/except AssertionError.
  # The alarm, cancellation class and handler are not simulated or replaced.
  time.sleep(10)
def previous(signum,frame):raise RuntimeError('unexpected prior handler')
old=signal.signal(signal.SIGALRM,previous)
process=None;original=None
cutoff=time.monotonic()+1.5
try:
 try:
  with t.ActionFence(cutoff):
   marker=directory/'ready'
   child_code=("import pathlib,signal,time\n"
               "def stop(*args):raise SystemExit(130)\n"
               "signal.signal(signal.SIGTERM,stop)\n"
               "pathlib.Path("+repr(str(marker))+").write_text('ready')\n"
               "while True:time.sleep(0.01)\n")
   process=processes.start('installation',[sys.executable,'-I','-S','-c',child_code],directory,{})
   while not marker.exists():
    assert process.poll() is None
    time.sleep(.01)
   sys.setprofile(pause_actual_parser)
   t.verify_private_children_log(scene,captured)
   (directory/'forbidden-after-recoverable-handler').write_text('bad')
 except BaseException as error:
  original=error
 finally:
  sys.setprofile(None)
 assert isinstance(original,t.ActionDeadline)
 assert entered==['actual-object-value']
 assert not (directory/'forbidden-after-recoverable-handler').exists()
 assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)
 assert signal.getsignal(signal.SIGALRM) is previous
 t.close_trial(processes,owner,None,None,installation=process,original=original)
 assert process is not None and process.returncode==130
 assert not processes.running and all(handle.closed for handle in processes.handles)
 reaped=False
 try:os.waitpid(process.pid,os.WNOHANG)
 except ChildProcessError:reaped=True
 assert reaped and original.args==('managed public original action budget expired',)
 assert not getattr(original,'__notes__',[])
 print(json.dumps({'ordinary_parse_positive':True,'actual_parser_entered':True,
                   'following_action':False,'manager_returncode':130,'reaped':True,
                   'original_failure_retained':True,'timer_disarmed_before_cleanup':True}))
finally:
 sys.setprofile(None)
 signal.setitimer(signal.ITIMER_REAL,0)
 signal.signal(signal.SIGALRM,old)
 for process in list(processes.running.values()):
  if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
  process.wait(timeout=2)
 for handle in processes.handles:handle.close()
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS), directory],
                env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=8,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1000:])
        self.assertEqual(json.loads(result.stdout), {
            "ordinary_parse_positive": True, "actual_parser_entered": True,
            "following_action": False, "manager_returncode": 130, "reaped": True,
            "original_failure_retained": True, "timer_disarmed_before_cleanup": True,
        })


    def test_actual_public_wal_reader_binds_all_rows_schema_and_idle_non_target(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = PublicRowsFixture(directory)
            try:
                paths = [scene.path, Path(str(scene.path) + "-wal")]
                before = {str(path): path.read_bytes() for path in paths}
                value = scene.read()
                self.assertEqual({str(path): path.read_bytes() for path in paths}, before)
                self.assertEqual([actor["actor_id"] for actor in value["actors"]],
                                 ["root", "child-left", "child-right"])
                scene.save("requests", "mpub1:hidden", {"request_id": "mpub1:hidden"}, worker="foreign")
                scene.db.commit()
                with self.assertRaisesRegex(AssertionError, "unknown worker"):
                    scene.read()
                scene.db.execute("DELETE FROM public_requests WHERE worker_id='foreign'")
                idle = copy.deepcopy(scene.header)
                idle.update(root=None, frozen=None, freeze_digest=None)
                for name in ("actors", "requests", "slots", "records", "processes",
                             "history_charges", "page_charges", "post_reservations", "transcript_bytes"):
                    idle[name] = 0
                scene.save("workers", None, idle, worker="idle-worker")
                scene.db.commit()
                self.assertEqual(scene.read(idle_worker="idle-worker"), value)
                with self.assertRaisesRegex(AssertionError, "unknown worker"):
                    scene.read()
                scene.db.execute("PRAGMA user_version=3")
                with self.assertRaisesRegex(AssertionError, "schema marker"):
                    scene.read(idle_worker="idle-worker")
                scene.db.execute("PRAGMA user_version=2")
                scene.db.execute("CREATE TABLE public_shadow(value TEXT)")
                with self.assertRaisesRegex(AssertionError, "table inventory"):
                    scene.read(idle_worker="idle-worker")
            finally:
                scene.close()

    def test_exact_public_freeze_projection_excludes_private_map_and_requires_reaping(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = PublicRowsFixture(directory)
            try:
                public, children = trial.public_extension_projections(scene.read())
                self.assertEqual([actor["role"] for actor in public["actors"]], ["root", "child-1", "child-2"])
                self.assertEqual(set(children), {"child-left", "child-right"})
                self.assertEqual(len(public["records"]), 6)
                self.assertEqual(len(public["slots"]), 4)
                self.assertEqual(children["child-left"]["actor"]["identity"]["uid"], 60000)
                self.assertEqual(children["child-left"]["actor"]["identity"]["exec_id"], "private-exec-child-left")
                self.assertEqual(children["child-left"]["snapshot_sha256"], public["snapshot_sha256"])
                serialized = trial.canonical(public).decode()
                for private in ("child-left", "child-right", '"uid":', "private-exec",
                                "PRIVATE_CONFIG_CANARY", "PRIVATE_SCOPE_PATH", "private_model_key"):
                    self.assertNotIn(private, serialized)
                # Coherently rehash a retained request, without changing its original freeze.
                scene.requests[0]["payload_digest"] = "f" * 64
                scene.save("requests", scene.requests[0]["request_id"], scene.requests[0])
                scene.db.commit()
                with self.assertRaisesRegex(AssertionError, "frozen snapshot changed"):
                    trial.public_extension_projections(scene.read())
                # Updating the frozen process digest cannot turn an unreaped leader into proof.
                scene.header["frozen"]["request_digest"] = trial.digest(scene.requests)
                scene.processes[-1]["cleanup"]["leader_reaped"] = False
                scene.header["frozen"]["process_digest"] = trial.digest(scene.processes)
                scene.header["freeze_digest"] = trial.digest(scene.header["frozen"])
                scene.save("workers", None, scene.header)
                scene.save("processes", scene.processes[-1]["process_id"], scene.processes[-1])
                scene.db.commit()
                with self.assertRaisesRegex(AssertionError, "actual owned process reaping"):
                    trial.public_extension_projections(scene.read())
            finally:
                scene.close()


    def test_integer_cleanup_flags_fail_even_after_coherent_public_freeze_rehash(self):
        for name in ("leader_terminated", "leader_reaped", "group_terminated"):
            with self.subTest(flag=name), tempfile.TemporaryDirectory() as directory:
                scene = PublicRowsFixture(directory)
                try:
                    self.assertIs(scene.processes[-1]["cleanup"][name], True)
                    trial.public_extension_projections(scene.read())
                    scene.processes[-1]["cleanup"][name] = 1
                    scene.header["frozen"]["process_digest"] = trial.digest(scene.processes)
                    scene.header["freeze_digest"] = trial.digest(scene.header["frozen"])
                    scene.save("processes", scene.processes[-1]["process_id"], scene.processes[-1])
                    scene.save("workers", None, scene.header)
                    scene.db.commit()
                    with self.assertRaisesRegex(AssertionError, "actual owned process reaping"):
                        trial.public_extension_projections(scene.read())
                    self.assertEqual(json.loads(scene.db.execute(
                        "SELECT data FROM public_processes WHERE process_id=?",
                        (scene.processes[-1]["process_id"],),
                    ).fetchone()[0])["cleanup"][name], 1)
                finally:
                    scene.close()


    def test_actual_http_connect_failure_preserves_only_transport_readiness_retry(self):
        from urllib.error import URLError

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unlistened:
            unlistened.bind(("127.0.0.1", 0))
            client = trial.Client("http://127.0.0.1:" + str(unlistened.getsockname()[1]), "named-token")
            with self.assertRaisesRegex(URLError, "scoped HTTP transport unavailable"):
                trial.http_observation(client, "/managed/snapshot", {}, cutoff=time.monotonic() + 3)
        with scope_server(lambda *_: (403, {"error": "not allowed"})) as (runtime, _, calls):
            code, body = trial.http_observation(runtime, "/managed/snapshot", {}, cutoff=time.monotonic() + 3)
            self.assertEqual(code, 403)
            self.assertEqual(json.loads(body), {"error": "not allowed"})
            self.assertEqual(len(calls), 1)


    def test_correlated_public_processes_require_distinct_original_lifetimes(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = CorrelatedPublicFixture(directory)
            try:
                original = scene.correlate()
                self.assertEqual(original["aggregate"]["owned_probe_and_server_rows"], 6)
                self.assertEqual(original["aggregate"]["explicit_context_reads"], 3)
                trial.public_extension_projections(scene.read())
                first, later = scene.processes[0], scene.processes[-1]
                later["identity"] = copy.deepcopy(first["identity"])
                later["cleanup"]["identity"] = copy.deepcopy(first["identity"])
                scene.commit()  # Rehash the row, frozen process list and frozen header.
                trial.public_extension_projections(scene.read())
                before = scene.db.execute("SELECT data,digest FROM public_processes ORDER BY rowid").fetchall()
                with self.assertRaisesRegex(AssertionError, "reuse one original process identity"):
                    scene.correlate()
                self.assertEqual(scene.db.execute("SELECT data,digest FROM public_processes ORDER BY rowid").fetchall(), before)
                # Numeric PID reuse is valid when the original kernel starttime differs.
                later["identity"]["starttime"] += 1
                later["cleanup"]["identity"] = copy.deepcopy(later["identity"])
                scene.commit()
                self.assertEqual(scene.correlate()["aggregate"], original["aggregate"])
                trial.public_extension_projections(scene.read())
            finally:
                scene.close()

    def test_correlated_child_cutoff_requires_the_same_original_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = CorrelatedPublicFixture(directory)
            try:
                original = scene.correlate()
                who = scene.fixture.child_ids["left"]
                scene.private[who]["boot_id"] = "different-valid-boot"
                with self.assertRaisesRegex(AssertionError, "another grant, UID, exec or instruction"):
                    scene.correlate()
                scene.private[who]["boot_id"] = scene.root["boot_id"]
                self.assertEqual(scene.correlate(), original)
                scene.private[who]["armed_deadline"] = 129.0
                with self.assertRaisesRegex(AssertionError, "original cutoff"):
                    scene.correlate()
            finally:
                scene.close()

    def test_correlation_joins_original_root_input_and_independently_derived_model_key(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = CorrelatedPublicFixture(directory)
            try:
                original = scene.correlate()
                retained_input = scene.root_row["input_sha256"]
                scene.root_row["input_sha256"] = trial.text_hash("changed Docker input")
                with self.assertRaisesRegex(AssertionError, "actual Docker exec or input"):
                    scene.correlate()
                scene.root_row["input_sha256"] = retained_input
                self.assertEqual(scene.correlate(), original)
                slot = scene.slots[0]
                provider = scene.fixture.requests[0]
                changed_key = "/public/" + "f" * 64
                changed_digest = trial.digest({
                    "schema_version": 1, "request_id": changed_key, "public_protocol": trial.PROTOCOL,
                    "public_actor": scene.actor_by_id["root"]["binding_digest"],
                    "public_slot": slot["slot_id"], "messages": provider["messages"],
                })
                slot["model_key"], slot["model_call_digest"] = changed_key, changed_digest
                scene.projections["proposal"]["slot"]["model_call_digest"] = changed_digest
                scene.model_rows[0][2:4] = [changed_key, changed_digest]
                scene.commit()  # The frozen hashes and observed digest also agree.
                trial.public_extension_projections(scene.read())
                before = scene.db.execute("SELECT data,digest FROM public_slots ORDER BY rowid").fetchall()
                with self.assertRaisesRegex(AssertionError, "original provider, publication and guest receipt"):
                    scene.correlate()
                self.assertEqual(scene.db.execute("SELECT data,digest FROM public_slots ORDER BY rowid").fetchall(), before)
            finally:
                scene.close()

    def test_actual_root_wal_input_and_exec_remain_original_across_terminal_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.executescript(
                "CREATE TABLE workers(worker_id TEXT PRIMARY KEY,spec TEXT,profile_fingerprint TEXT,container_id TEXT);"
                "CREATE TABLE attempts(worker_id TEXT PRIMARY KEY,attempt_id TEXT UNIQUE,input_sha256 TEXT,"
                "seconds REAL,deadline REAL,exec_id TEXT,exec_dispatched INTEGER,cancelled INTEGER,exit_code INTEGER);"
            )
            spec = {"schema_version": 1, "deployment_id": "fixture", "repository": "fixture-repository",
                    "slot_id": "original-slot", "generation": 1, "launch_key": "original-launch", "profile": "original"}
            digest = trial.text_hash("actual original input fixture")
            connection.execute("INSERT INTO workers VALUES(?,?,?,?)",
                               ("owned", trial.canonical(spec).decode(), "pinned-profile", "a" * 64))
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)",
                               ("owned", "attempt", digest, 180.0, 300.0, "original-exec", 1, 0, None))
            connection.commit()
            try:
                original = trial.root_execution_row(path, "owned", "attempt")
                self.assertEqual(original["input_sha256"], digest)
                self.assertEqual(original["exec_id"], "original-exec")
                self.assertEqual(original["deadline"], 300.0)
                connection.execute("UPDATE attempts SET exit_code=0")
                connection.commit()
                self.assertEqual(trial.root_execution_row(path, "owned", "attempt", final=True), original)
                connection.execute("UPDATE attempts SET input_sha256=?", (trial.text_hash("changed input"),))
                connection.commit()
                self.assertNotEqual(trial.root_execution_row(path, "owned", "attempt", final=True), original)
                connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)",
                                   ("foreign", "foreign-attempt", "b" * 64, 1, 300, "foreign-exec", 1, 0, 0))
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "missing or duplicated"):
                    trial.root_execution_row(path, "owned", "attempt", final=True)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)
            finally:
                connection.close()


    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT")
                         and hasattr(signal, "ITIMER_REAL"), "Linux interrupted owned shutdown")
    def test_real_alarm_during_manager_wait_retains_ownership_and_complete_failure_evidence(self):
        program = r"""import json,os,pathlib,signal,sys,threading,time
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
directory=pathlib.Path(sys.argv[2])
owner=t.OwnedTrial(dedicated_process=True)
processes=t.RetainedProcesses(directory)
events=[]
class NamedFailingFixture:
 def __init__(self):self.release_root=threading.Event()
 def close(self,cutoff):
  events.append('model-close')
  raise RuntimeError('PRIVATE_CLEANUP_CANARY')
fixture=NamedFailingFixture()
processes_started=[]
original=None
try:
 for name in ('installation','other-owned'):
  ready=directory/(name+'-ready')
  signalled=directory/(name+'-signalled')
  hold_first=name=='installation'
  code=("import pathlib,signal,time\n"
        "count=0\n"
        "def stop(*args):\n"
        " global count\n"
        " count+=1\n"
        " pathlib.Path("+repr(str(signalled))+").write_text(str(count))\n"
        " if count>1 or not "+repr(hold_first)+":raise SystemExit(130)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "pathlib.Path("+repr(str(ready))+").write_text('ready')\n"
        "while True:time.sleep(.01)\n")
  process=processes.start(name,[sys.executable,'-I','-S','-c',code],directory,{})
  processes_started.append(process)
  limit=time.monotonic()+3
  while not ready.exists():
   assert process.poll() is None and time.monotonic()<limit
   time.sleep(.01)
 manager=processes.running['installation']
 wait_entered=[]
 def observe_actual_wait(frame,event,arg):
  if (event=='call' and frame.f_code is t.subprocess.Popen.wait.__code__
      and frame.f_locals.get('self') is manager):
   wait_entered.append(True)
 try:
  sys.setprofile(observe_actual_wait)
  with t.ActionFence(time.monotonic()+1.5):
   t.stop_installation(processes,manager)
   (directory/'forbidden-after-interrupted-stop').write_text('bad')
 except BaseException as error:
  original=error
 finally:
  sys.setprofile(None)
 assert wait_entered==[True]
 assert isinstance(original,t.ActionDeadline)
 assert (directory/'installation-signalled').read_text()=='1'
 assert manager.poll() is None
 assert processes.running['installation'] is manager
 assert not (directory/'forbidden-after-interrupted-stop').exists()
 assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)
 cleanup=t.close_trial(processes,owner,None,fixture,installation=manager,original=original)
 assert manager.returncode==130 and all(process.returncode==130 for process in processes_started)
 assert not processes.running and all(handle.closed for handle in processes.handles)
 assert (directory/'installation-signalled').read_text()=='2'
 assert (directory/'other-owned-signalled').read_text()=='1'
 assert events==['model-close'] and fixture.release_root.is_set()
 assert original.args==('managed public original action budget expired',)
 assert 'model-fixture-close' in '\n'.join(original.__notes__)
 assert 'PRIVATE_' not in '\n'.join(original.__notes__)
 reaped=0
 for process in processes_started:
  try:os.waitpid(process.pid,os.WNOHANG)
  except ChildProcessError:reaped+=1
 assert reaped==2
 secret='PRIVATE_LOG_SECRET_'+('q'*64)
 (directory/'installation.log').write_text('prefix:'+secret+('.'*19995))
 evidence=t.write_failure_diagnostic(
  directory,original,['original-stop-entered'],{}, {'PRIVATE_CLEANUP_CANARY',secret},
  action_elapsed=1.5,cleanup=cleanup)
 stored=json.loads((directory/'diagnostics.json').read_bytes())
 assert stored==evidence and stored['error']==str(original)
 assert secret not in json.dumps(stored) and 'q'*16 not in json.dumps(stored)
 assert stored['cleanup_notes']==original.__notes__
 assert stored['action_elapsed_seconds']==1.5 and stored['full_run_completed'] is False
 assert not (directory/'report.json').exists()
 assert (directory/'diagnostics.json').stat().st_mode&0o777==0o600
 print(json.dumps({'interrupted_after_sigterm':True,'retained_same_popen':True,
                   'following_action':False,'manager_returncode':130,'reaped':reaped,
                   'remaining_close_attempted':True,'original_failure_retained':True,
                   'complete_failure_artifact':True,'passed_artifact':False}))
finally:
 sys.setprofile(None)
 signal.setitimer(signal.ITIMER_REAL,0)
 for process in processes_started:
  if process.poll() is None:
   os.killpg(process.pid,signal.SIGKILL)
  process.wait(timeout=2)
 for handle in processes.handles:handle.close()
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS), directory],
                env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=12,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1200:])
        self.assertEqual(json.loads(result.stdout), {
            "interrupted_after_sigterm": True, "retained_same_popen": True,
            "following_action": False, "manager_returncode": 130, "reaped": 2,
            "remaining_close_attempted": True, "original_failure_retained": True,
            "complete_failure_artifact": True, "passed_artifact": False,
        })


    def test_shared_allowance_accepts_only_the_identified_idle_cleanup_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.sqlite3"
            connection = database(path)
            model = fixture()
            try:
                for index in range(13):
                    row = ("owned-worker", "original-attempt", "original-" + str(index),
                           trial.digest(["original-http-reservation", index]), 1024, "reserved", 0)
                    model.reservations[row[:3]] = row
                    model.requests.append({"named_fixture_call": index})
                    connection.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?)", (*row[:5], "complete", "{}"))
                connection.commit()
                model.root_turns, model.child_turns = 5, {"left": 2, "right": 2}
                model.public_calls = dict(trial.PUBLIC_OUTPUTS)
                original = trial.model_accounting(path, model, "owned-worker", "original-attempt")
                connection.execute("INSERT INTO workers VALUES('original-idle',NULL)")
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "one shared13-call allowance"):
                    trial.model_accounting(path, model, "owned-worker", "original-attempt")
                self.assertEqual(trial.model_accounting(
                    path, model, "owned-worker", "original-attempt", idle_worker="original-idle",
                ), original)
                connection.execute("UPDATE workers SET attempt_id='unexpected-root' WHERE worker_id='original-idle'")
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "one shared13-call allowance"):
                    trial.model_accounting(path, model, "owned-worker", "original-attempt", idle_worker="original-idle")
                connection.execute("UPDATE workers SET attempt_id=NULL WHERE worker_id='original-idle'")
                connection.execute("INSERT INTO workers VALUES('foreign-idle',NULL)")
                connection.commit()
                with self.assertRaisesRegex(AssertionError, "one shared13-call allowance"):
                    trial.model_accounting(path, model, "owned-worker", "original-attempt", idle_worker="original-idle")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 13)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM workers").fetchone()[0], 3)
            finally:
                connection.close()
                model.close(time.monotonic() + 5)

    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux two-manager ownership")
    def test_final_report_requires_the_second_exact_manager_after_first_handled_stop(self):
        for nominate_first in (False, True):
            with self.subTest(nominate_first=nominate_first), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                processes = trial.RetainedProcesses(root)
                first = manager_fixture(processes, root)
                second = None
                events = []
                owner, peer = NamedCleanupOwner(events), NamedCleanupPeer(events, root)
                model = NamedCleanupFixture(events)
                try:
                    self.assertEqual(trial.stop_installation(processes, first), 130)
                    self.assertEqual(first.returncode, 130)
                    self.assertNotIn("installation", processes.running)
                    (root / "manager-ready").unlink()
                    second = manager_fixture(processes, root)
                    self.assertIsNot(second, first)
                    self.assertNotEqual(second.pid, first.pid)
                    path = root / "report.json"
                    if nominate_first:
                        with self.assertRaisesRegex(AssertionError, "original-manager-identity"):
                            trial.publish_report(
                                path, {"passed": True, "first_manager_status": 130}, processes,
                                owner, peer, model, first, None,
                            )
                        self.assertFalse(path.exists())
                    else:
                        result = trial.publish_report(
                            path, {"passed": True, "first_manager_status": 130}, processes,
                            owner, peer, model, second, None,
                        )
                        self.assertEqual(result["first_manager_status"], 130)
                        self.assertEqual(result["final_owned_cleanup"]["manager_handled_status"], 130)
                        self.assertEqual(json.loads(path.read_bytes()), result)
                    self.assertEqual(first.returncode, 130)
                    self.assertEqual(second.returncode, 130)
                    self.assertFalse(processes.running)
                    self.assertEqual(events[-1], "containment")
                finally:
                    processes.close()


    @unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT") and hasattr(os, "fork"),
                         "Linux original public leader/group observation")
    def test_kernel_absence_rejects_live_originals_and_orphan_group_before_fallback(self):
        program = r"""import copy,json,os,pathlib,signal,sys,time
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parents[1]))
sys.path.insert(0,sys.argv[1])
import check_managed_public_stack as t
directory=pathlib.Path(sys.argv[2])
owner=t.OwnedTrial(dedicated_process=True)
processes=t.RetainedProcesses(directory)
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
rows={'header':{'root':{'boot_id':boot}},'processes':[]}
leaders=[]
def refused(value,message):
 try:t.public_process_absence(value,cutoff=time.monotonic()+2)
 except AssertionError as error:assert message in str(error)
 else:raise AssertionError('kernel negative did not refuse')
def start(name,orphan=False):
 ready=directory/(name+'-ready')
 child_ready=directory/'orphan-ready'
 code=("import os,pathlib,signal,time\n"
       "def stop(*args):raise SystemExit(130)\n"
       "signal.signal(signal.SIGTERM,stop)\n"
       "if "+repr(orphan)+":\n"
       " child=os.fork()\n"
       " if child==0:\n"
       "  pathlib.Path("+repr(str(child_ready))+").write_text(str(os.getpid()))\n"
       "  while True:time.sleep(.01)\n"
       " while not pathlib.Path("+repr(str(child_ready))+").exists():time.sleep(.01)\n"
       "pathlib.Path("+repr(str(ready))+").write_text('ready')\n"
       "while True:time.sleep(.01)\n")
 process=processes.start(name,[sys.executable,'-I','-S','-c',code],directory,{})
 deadline=time.monotonic()+3
 while not ready.exists():
  assert process.poll() is None and time.monotonic()<deadline
  time.sleep(.01)
 info=t.process_info(process.pid)
 assert info['parent']==os.getpid() and info['group']==process.pid
 return process,{'pid':process.pid,'pgid':process.pid,'starttime':int(info['starttime']),'boot_id':boot}
try:
 with t.ActionFence(time.monotonic()+15):
  for index in range(6):
   process,identity=start('owner-'+str(index),orphan=index==0)
   leaders.append(process)
   rows['processes'].append({'identity':identity})
  refused(rows,'original leader is still present')
  for process in leaders:
   # These exact owned leaders exit; the first's child remains in its old group.
   process.send_signal(signal.SIGTERM)
   assert process.wait(timeout=3)==130
  orphan=int((directory/'orphan-ready').read_text())
  deadline=time.monotonic()+3
  while True:
   orphan_identity=t.process_info(orphan)
   assert orphan_identity is not None and time.monotonic()<deadline
   if orphan_identity['parent']==os.getpid():break
   time.sleep(.01)
  assert orphan_identity['group']==leaders[0].pid
  refused(rows,'original process group remains')
  assert owner._owned_signal(orphan,signal.SIGTERM,orphan_identity)
  deadline=time.monotonic()+3
  code=None
  while code is None and time.monotonic()<deadline:
   code=owner._reap(orphan)
   if code is None:time.sleep(.01)
  assert code==130
  positive=t.public_process_absence(rows,cutoff=time.monotonic()+2)
  assert positive=={'owned_lifetimes':6,'original_leaders_absent':True,
                    'original_groups_absent':True,'signal_operations':0}
  replacement,new_identity=start('different-lifetime')
  try:
   # This is a named different-starttime projection, not a claim that the
   # kernel actually recycled a numeric PID during this short unit fixture.
   prior=copy.deepcopy(rows)
   prior['processes'][0]['identity']=dict(new_identity,starttime=new_identity['starttime']-1)
   assert t.public_process_absence(prior,cutoff=time.monotonic()+2)==positive
   assert replacement.poll() is None
   prior['processes'][0]['identity']=new_identity
   refused(prior,'original leader is still present')
   assert replacement.poll() is None
  finally:
   replacement.send_signal(signal.SIGTERM)
   assert replacement.wait(timeout=3)==130
  assert all(process.returncode==130 for process in leaders)
  print(json.dumps({'live_leader_refused':True,'orphan_group_refused':True,
                    'all_originals_reaped_positive':True,'different_starttime_projection':True,
                    'new_owned_lifetime_untouched':True,'observer_signal_operations':0}))
finally:
 cutoff=time.monotonic()+5
 try:
  for process in processes.running.values():
   if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
  for process in processes.running.values():
   process.wait(timeout=max(.001,cutoff-time.monotonic()))
  for handle in processes.handles:handle.close()
 finally:owner.cleanup(cutoff)
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", program, str(CONTRACTS), directory],
                env=trial.PROFILE_ENVIRONMENT, capture_output=True, timeout=25,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1200:])
        self.assertEqual(json.loads(result.stdout), {
            "live_leader_refused": True, "orphan_group_refused": True,
            "all_originals_reaped_positive": True, "different_starttime_projection": True,
            "new_owned_lifetime_untouched": True, "observer_signal_operations": 0,
        })


    def test_actual_public_source_probe_requires_every_host_module_from_its_pinned_blob(self):
        import check_cold_children_stack as cold
        import check_historical_authority as historical

        old_names = (
            "src.worker.managed_public_types", "src.worker.managed_public_journal",
            "src.worker.discussion_types", "src.polyphony.conversation_client",
        )
        host_names = (
            "src.worker.managed_public", "src.worker.managed_public_broker",
            "src.worker.managed_public_profiles",
        )
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory).resolve() / "openjules"
            checkout.mkdir()
            contents = {}
            for name in (*old_names, *host_names):
                path = checkout.joinpath(*name.split(".")).with_suffix(".py")
                path.parent.mkdir(parents=True, exist_ok=True)
                # Named module fixtures. The actual Python resolver and Git
                # object comparison run; no installed host behavior is claimed.
                contents[name] = ("FIXTURE_MODULE = " + repr(name) + "\n").encode()
                path.write_bytes(contents[name])
            for package in ("src", "src/worker", "src/polyphony"):
                (checkout / package / "__init__.py").write_text("")
            (checkout / ".gitignore").write_text("__pycache__/\n*.pyc\n")

            def git(*arguments):
                return subprocess.run(
                    ["git", "-C", str(checkout), *arguments], capture_output=True,
                    text=True, timeout=10, check=True,
                ).stdout.strip()

            def commit():
                git("add", ".")
                git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "named public source fixture")
                peer.revisions["openjules"] = git("rev-parse", "HEAD")
                peer.trees["openjules"] = git("rev-parse", "HEAD^{tree}")

            git("init", "-q")
            peer = SimpleNamespace(checkouts={"openjules": checkout}, revisions={}, trees={})
            commit()
            environment = {key: value for key, value in os.environ.items()
                           if key in {"PATH", "LANG", "LC_ALL"}}
            environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
            context = {
                "bundle": Path(directory).resolve() / "named-bundle", "data": {},
                "commands": {"openjules": [sys.executable]},
                "manager_environment": environment,
            }
            baseline = {"openjules": {"module_sha256": {"existing.service": "a" * 64}},
                        "polyphony": {"module_sha256": {"existing.authority": "b" * 64}}}
            expected = baseline["openjules"]["module_sha256"] | {
                name: hashlib.sha256(content).hexdigest() for name, content in contents.items()
            }
            with patch.object(cold, "component_provenance", side_effect=lambda *args: copy.deepcopy(baseline)), patch(
                    "zpm.deployment._environment", return_value=environment):
                result = trial.component_provenance(context, peer)
                self.assertEqual(result["openjules"]["module_sha256"], expected)
                self.assertEqual(result["polyphony"], baseline["polyphony"])
                self.assertNotIn(str(checkout), json.dumps(result))
                for name in host_names:
                    path = checkout.joinpath(*name.split(".")).with_suffix(".py")
                    relative = str(path.relative_to(checkout))
                    with self.subTest(module=name, fault="committed absent module"):
                        path.unlink()
                        commit()
                        self.assertEqual(historical.verify_source(
                            checkout, peer.revisions["openjules"], peer.trees["openjules"],
                        ), {"revision": peer.revisions["openjules"], "tree": peer.trees["openjules"]})
                        # The prior four-module probe really succeeds here.
                        prior = subprocess.run(
                            [sys.executable, "-c", historical.IMPORT_PROBE, json.dumps(old_names)],
                            cwd=checkout, env=environment, capture_output=True,
                            text=True, timeout=15, check=True,
                        )
                        self.assertEqual(set(json.loads(prior.stdout)["modules"]), set(old_names))
                        self.assertTrue(all(json.loads(prior.stdout)["modules"].values()))
                        with self.assertRaisesRegex(AssertionError, "pinned module is unavailable"):
                            trial.component_provenance(context, peer)
                        path.write_bytes(contents[name])
                        commit()
                        self.assertEqual(trial.component_provenance(
                            context, peer,
                        )["openjules"]["module_sha256"], expected)
                    with self.subTest(module=name, fault="wrong tracked bytes hidden from status"):
                        git("update-index", "--assume-unchanged", relative)
                        try:
                            path.write_bytes(b"FIXTURE_MODULE = 'wrong retained implementation'\n")
                            self.assertEqual(git("status", "--porcelain", "--untracked-files=all"), "")
                            self.assertEqual(historical.verify_source(
                                checkout, peer.revisions["openjules"], peer.trees["openjules"],
                            ), {"revision": peer.revisions["openjules"], "tree": peer.trees["openjules"]})
                            with self.assertRaisesRegex(AssertionError, "pinned Git blob"):
                                trial.component_provenance(context, peer)
                        finally:
                            path.write_bytes(contents[name])
                            git("update-index", "--no-assume-unchanged", relative)
                        self.assertEqual(trial.component_provenance(
                            context, peer,
                        )["openjules"]["module_sha256"], expected)



    def test_original_public_generation_cannot_renew_from_a_later_request_or_slot(self):
        for selected in ("root", "left", "right"):
            with self.subTest(actor=selected), tempfile.TemporaryDirectory() as directory:
                scene = CorrelatedPublicFixture(directory)
                try:
                    who = "root" if selected == "root" else scene.fixture.child_ids[selected]
                    requests = {row["request_id"]: row for row in scene.requests}
                    opening_key = ("mpub1:root-proposal" if selected == "root"
                                   else "mpub1:child-" + selected + "-history-0")
                    if selected == "root":
                        opening_slot = next(row for row in scene.slots if row["instruction_key"] == "proposal")
                        later_slot = next(row for row in scene.slots if row["instruction_key"] == "followup")
                        requests["mpub1:root-followup"]["monotonic_cutoff"] = 160.0
                        later_slot["monotonic_cutoff"] = 150.0
                        ceiling = min(requests[opening_key]["monotonic_cutoff"], opening_slot["monotonic_cutoff"])
                        later_ceiling = later_slot["monotonic_cutoff"]
                        execution_ceiling = scene.root_row["deadline"]
                    else:
                        requests[opening_key]["monotonic_cutoff"] = 115.0
                        requests[opening_key]["expires_at"] = 9985.0
                        later_slot = next(row for row in scene.slots if row["actor_id"] == who)
                        ceiling = requests[opening_key]["monotonic_cutoff"]
                        later_ceiling = min(
                            requests["mpub1:pull-" + selected]["monotonic_cutoff"],
                            later_slot["monotonic_cutoff"],
                        )
                        execution_ceiling = scene.private[who]["armed_deadline"]
                    owned = {row["stage"]: row for row in scene.processes if row["actor_id"] == who}
                    probe, server = owned["probe"], owned["server"]

                    def retain():
                        for row in (probe, server):
                            row["intent_digest"] = trial.digest(row["intent"])
                        scene.commit()  # Rehash SQL rows, request/process lists and the frozen header.
                        trial.public_extension_projections(scene.read())

                    # Permission or startup can shorten either original ceiling.
                    # The positive must not require equality with a later slot.
                    probe["intent"]["monotonic_cutoff"] = ceiling - 2.0
                    server["intent"]["monotonic_cutoff"] = ceiling - 1.0
                    retain()
                    accepted = scene.correlate()["aggregate"]
                    self.assertEqual(accepted["first_open_monotonic_cutoffs_verified"], 3)
                    self.assertLess(probe["intent"]["monotonic_cutoff"], server["intent"]["monotonic_cutoff"])
                    self.assertLess(server["intent"]["monotonic_cutoff"], ceiling)
                    self.assertLess(ceiling, later_ceiling)

                    # This altered owner fits the later operation and private
                    # watchdog, so it still fits the previous execution-only bound.
                    renewed = ceiling + 1.0
                    self.assertLess(renewed, min(later_ceiling, execution_ceiling))
                    server["intent"]["monotonic_cutoff"] = renewed
                    retain()
                    before = scene.db.execute(
                        "SELECT data,digest FROM public_processes ORDER BY rowid",
                    ).fetchall()
                    with self.assertRaisesRegex(AssertionError, "renewed its first opening cutoff"):
                        scene.correlate()
                    self.assertEqual(scene.db.execute(
                        "SELECT data,digest FROM public_processes ORDER BY rowid",
                    ).fetchall(), before)

                    # The probe is also a shorter startup bound, never a later
                    # lifetime than the same original generation's server.
                    server["intent"]["monotonic_cutoff"] = ceiling - 1.0
                    probe["intent"]["monotonic_cutoff"] = ceiling - 0.5
                    retain()
                    self.assertLess(probe["intent"]["monotonic_cutoff"], ceiling)
                    with self.assertRaisesRegex(AssertionError, "probe extends its original server cutoff"):
                        scene.correlate()
                    probe["intent"]["monotonic_cutoff"] = ceiling - 2.0
                    retain()
                    self.assertEqual(scene.correlate()["aggregate"], accepted)
                finally:
                    scene.close()


if __name__ == "__main__":
    unittest.main()
