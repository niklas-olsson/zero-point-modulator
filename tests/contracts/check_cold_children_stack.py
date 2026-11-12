"""Installed cold clarification over two ordinary canonical child grants.

Only model replies are deterministic fixtures. Actual generated services, Docker,
kernel UIDs, canonical HTTP allocation and final capture establish the result.
The existing default and initial-outcome trials remain separate and unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.error import URLError

from check_children_stack import (
    BASE_TOOLS, CHILD_TOOLS, CHILD_VERIFY, DENY_SOCKET, VERIFY,
    child_capture_observation, inference_reservations, read_artifacts,
    text_hash, verify_root_log,
)
from check_cleanup import journal_paths, original_capture, qualify_owned_cleanup, require_retained
from check_historical_authority import (
    CURRENT_REVISION, CURRENT_TREE, IMPORT_PROBE,
    _pinned_modules, source_provenance, verify_source,
)
from check_model_stack import ModelFixture, TOKEN_SOURCE
from check_stack import (
    Client, Processes, clean_environment, diagnostics, eventually, port,
    private_json, require, wait_for_capacity,
)
from operational_measurement import (
    Measurements, RequestMeasurements, confirm_idle_reconciliation, idle_process_sample, idle_worker_binding,
)


PROTOCOL = "cold-clarification-v1"
INSTRUCTION_PROTOCOL = "openjules-cold-clarification-v1"
MODEL_NAME = "deterministic-cold-children-stack-fixture"
INITIAL = "Consider left.txt, retain a provisional edit and ask which file to implement."
FOLLOWUP = "Implement only right.txt from the original base; preserve the first round separately."
PARTIAL = "left provisional clarification work\n"
CLARIFICATION = "Please clarify the assigned file. Keep this provisional question private: fråga."
COMPLETION = "Implemented right from its fresh approved base; request root review."
REQUIREMENTS = (
    "Use one cold clarification session. Preserve the first child's provisional left edit "
    "and clarification, explicitly ask the fresh second child to implement right, then "
    "apply only its returned patch. Complete left as root and verify the combined result."
)
ROOT_RULES = (
    "Use the approved cold clarification tools and exactly two separately granted children. "
    "Preserve each child's report and patch. Never copy the first child's checkout into "
    "the second. The root implements left only after applying the second child's right patch. "
    "Preserve AGENTS.md and the tests; run the combined tests before requesting review.\n"
)
SESSION_TOOLS = {"start_child_session", "continue_child_session", "child_session_status"}
SESSION_COLUMNS = (
    "worker_id", "session_id", "start_request_id", "binding", "binding_digest",
    "closure", "close_reason",
)
ROUND_COLUMNS = (
    "worker_id", "session_id", "ordinal", "request_id", "request_json",
    "request_digest", "grant_payload", "instructions_digest", "predecessor",
    "predecessor_digest", "cutoff", "boot_id", "state", "reason", "child_id",
    "grant_digest", "model_closed", "fence_established",
)
SESSION_SQL = (
    "CREATE TABLE cold_sessions("
    "worker_id TEXT NOT NULL,session_id TEXT NOT NULL,start_request_id TEXT NOT NULL,"
    "binding TEXT NOT NULL,binding_digest TEXT NOT NULL,closure TEXT,close_reason TEXT,"
    "PRIMARY KEY(worker_id,session_id),UNIQUE(worker_id,start_request_id))"
)
ROUND_SQL = (
    "CREATE TABLE cold_rounds("
    "worker_id TEXT NOT NULL,session_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
    "request_id TEXT NOT NULL,request_json TEXT NOT NULL,request_digest TEXT NOT NULL,"
    "grant_payload TEXT NOT NULL,instructions_digest TEXT NOT NULL,"
    "predecessor TEXT,predecessor_digest TEXT,cutoff REAL NOT NULL,boot_id TEXT NOT NULL,"
    "state TEXT NOT NULL,reason TEXT,child_id TEXT,grant_digest TEXT,"
    "model_closed INTEGER NOT NULL DEFAULT 0,"
    "fence_established INTEGER NOT NULL DEFAULT 0,"
    "PRIMARY KEY(worker_id,session_id,ordinal),UNIQUE(worker_id,request_id),"
    "UNIQUE(worker_id,child_id))"
)
OJ_MODULES = (
    "src.worker.managed_service", "src.worker.child_types",
    "src.worker.child_sessions", "src.worker.child_journal",
    "src.worker.child_supervisor", "src.worker.model_broker",
    "src.worker.child_broker", "src.worker.child_runtime",
)
STATUS_FIELDS = {
    "schema_version", "request_id", "ok", "protocol", "session_id",
    "root_attempt_id", "phase", "reason", "predecessor_digest",
    "followup_request_id", "model_fence", "logical_outcome",
    "all_child_receipts_final", "canonical_session", "release_authorized", "rounds",
}
ROUND_FIELDS = {
    "logical_round", "request_id", "child_id", "grant_digest", "instructions_digest",
    "exec_id", "runtime_state", "exit_code", "initial_outcome", "evidence", "artifacts",
}
OUTCOME_FIELDS = {
    "schema_version", "work_turn_id", "ordinal", "child_id", "root_attempt_id",
    "parent_attempt_id", "task_id", "requirements_revision", "grant_digest",
    "instructions_digest", "source", "report", "canonical_committed", "release_authorized",
}
HASH = re.compile(r"[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
SESSION_ID = re.compile(r"session_[0-9a-f]{64}")
CHILD_ID = re.compile(r"child_[0-9a-f]{32}")


class ColdRequestObservation:
    """Attribute one failed direct request without changing its execution."""

    OPERATIONS = frozenset({
        "enroll", "enroll-replay", "foreign-scope-refusal",
        "active-snapshot", "active-children", "active-worker",
    })

    def __init__(self):
        self._failure = None

    def request(self, operation, client, *args, **kwargs):
        require(operation in self.OPERATIONS, "unknown cold request observation operation")
        try:
            return client.request(*args, **kwargs)
        except BaseException as exc:
            if self._failure is None:
                kind = "other"
                for exception, label in (
                    (TimeoutError, "timeout"), (URLError, "url"),
                    (ConnectionError, "connection"), (AssertionError, "assertion"),
                    (OSError, "os"), (ValueError, "value"),
                ):
                    if isinstance(exc, exception):
                        kind = label
                        break
                self._failure = {"operation": operation, "exception_kind": kind}
            raise

    def report(self):
        return None if self._failure is None else self._failure.copy()


# The model response has already been returned before this fixed command runs.
# Each RPC uses only the remaining original poll interval. The image module,
# not a task checkout or PYTHONPATH module, validates the actual control frames.
POLL = """import hashlib,json,pathlib,runpy,sys,time
options=json.loads(sys.argv[1])
path=pathlib.Path('/opt/openjules/child_tools.py')
module=runpy.run_path(str(path))
control=module['ControlClient']('/run/openjules-model/control.sock',timeout_seconds=1)
deadline=time.monotonic()+15
while True:
 remaining=deadline-time.monotonic()
 assert remaining>0,'cold status poll deadline'
 control.timeout_seconds=min(1,remaining)
 status=control.request('session_status',session_id=options['session_id'])
 row=status['rounds'][-1]
 if row['logical_round']==options['round'] and row['runtime_state']=='exec_returned':
  assert type(row['exit_code']) is int and row['exit_code']==0
  break
 time.sleep(min(0.05,max(0,deadline-time.monotonic())))
remaining=deadline-time.monotonic()
assert remaining>0,'cold patch poll deadline'
control.timeout_seconds=min(1,remaining)
patch=control.request('result',child_id=row['child_id'],kind='patch',offset=0,limit=65536)
assert time.monotonic()<=deadline,'cold read completed after its original deadline'
value={'status':status,'patch':patch,'image_child_tools_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
output=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
assert len(output.encode())<=32768,'cold observation exceeds its fixed bound'
print(output)
"""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return text_hash(canonical(value))


def hashed(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite number")


def object_value(content, maximum=65536):
    try:
        require(isinstance(content, str) and len(content.encode("utf-8")) <= maximum,
                "cold observation exceeds its text bound")
        value = json.loads(content, object_pairs_hook=_pairs, parse_constant=_constant)
        require(isinstance(value, dict), "cold observation is not an object")
        canonical(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise AssertionError("cold observation is not bounded canonical-compatible JSON") from None
    return value


def effective_envelope(initial):
    """Read only the leading actual instruction object, retaining exact bytes."""
    try:
        require(isinstance(initial, str) and len(initial.encode("utf-8")) <= 131072,
                "cold child instruction context exceeds its bound")
        decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)
        envelope, end = decoder.raw_decode(initial)
        raw = initial[:end]
        require(isinstance(envelope, dict) and set(envelope) == {
            "schema_version", "protocol", "session_id", "logical_round",
            "initial_instructions", "followup", "predecessor",
        }, "cold child instruction fields changed")
        require(raw == canonical(envelope) and len(raw.encode("utf-8")) <= 65536
                and initial[end:] == "\n\nRoot repository instructions:\n" + ROOT_RULES,
                "cold child instruction or repository rules changed")
        require(type(envelope["schema_version"]) is int and envelope["schema_version"] == 1
                and envelope["protocol"] == INSTRUCTION_PROTOCOL
                and isinstance(envelope["session_id"], str)
                and SESSION_ID.fullmatch(envelope["session_id"])
                and type(envelope["logical_round"]) is int and envelope["logical_round"] in {1, 2}
                and envelope["initial_instructions"] == INITIAL,
                "cold child instruction binding changed")
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise AssertionError("cold child instruction is not a bounded envelope") from None
    return envelope, raw


def status_value(value, session_id=None):
    require(isinstance(value, dict) and set(value) == STATUS_FIELDS
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["ok"] is True and value["protocol"] == PROTOCOL
            and isinstance(value["session_id"], str) and SESSION_ID.fullmatch(value["session_id"])
            and identifier(value["request_id"]) and identifier(value["root_attempt_id"])
            and value["canonical_session"] is False and value["release_authorized"] is False
            and type(value["all_child_receipts_final"]) is bool
            and isinstance(value["rounds"], list) and 1 <= len(value["rounds"]) <= 2,
            "cold status changed its exact host identity or authority boundary")
    require(session_id is None or value["session_id"] == session_id,
            "cold status changed its original session")
    for ordinal, row in enumerate(value["rounds"], 1):
        require(isinstance(row, dict) and set(row) == ROUND_FIELDS
                and type(row["logical_round"]) is int and row["logical_round"] == ordinal
                and identifier(row["request_id"]) and hashed(row["instructions_digest"]),
                "cold status changed its logical-round identity")
        if row["child_id"] is not None:
            require(isinstance(row["child_id"], str) and CHILD_ID.fullmatch(row["child_id"])
                    and hashed(row["grant_digest"])
                    and (row["exec_id"] is None or identifier(row["exec_id"])),
                    "cold status contains an invalid physical child")
    return value


def returned_status(value, ordinal, session_id=None):
    value = status_value(value, session_id)
    require(len(value["rounds"]) == ordinal and value["all_child_receipts_final"] is False
            and value["predecessor_digest"] is not None and hashed(value["predecessor_digest"]),
            "cold returned status lacks its original provisional predecessor")
    expected = ("awaiting_followup", "open", None) if ordinal == 1 else ("closed", "established", "completed")
    require((value["phase"], value["model_fence"], value["logical_outcome"]) == expected
            and value["reason"] is None, "cold returned status has not reached the declared barrier")
    for index, row in enumerate(value["rounds"], 1):
        require(row["runtime_state"] == "exec_returned"
                and type(row["exit_code"]) is int and row["exit_code"] == 0
                and identifier(row["exec_id"]) and row["artifacts"] is None
                and row["evidence"] == "provisional",
                "cold logical outcome is not an actual successful exec return")
        outcome = row["initial_outcome"]
        expected_outcome = "needs_clarification" if index == 1 else "completed"
        expected_summary = CLARIFICATION if index == 1 else COMPLETION
        require(isinstance(outcome, dict) and set(outcome) == OUTCOME_FIELDS
                and type(outcome["schema_version"]) is int and outcome["schema_version"] == 1
                and type(outcome["ordinal"]) is int and outcome["ordinal"] == 1
                and outcome["child_id"] == row["child_id"]
                and outcome["root_attempt_id"] == outcome["parent_attempt_id"] == value["root_attempt_id"]
                and outcome["grant_digest"] == row["grant_digest"]
                and outcome["instructions_digest"] == row["instructions_digest"]
                and identifier(outcome["task_id"])
                and type(outcome["requirements_revision"]) is int and outcome["requirements_revision"] >= 1
                and outcome["canonical_committed"] is False and outcome["release_authorized"] is False
                and outcome["source"] == "child_report_via_bound_runtime",
                "cold outcome lost its original ordinal-one physical binding")
        report = outcome["report"]
        require(isinstance(report, dict) and set(report) == {"request_key", "outcome", "summary", "payload_digest"}
                and identifier(report["request_key"])
                and report["outcome"] == expected_outcome and report["summary"] == expected_summary
                and report["payload_digest"] == digest({key: report[key] for key in ("request_key", "outcome", "summary")})
                and outcome["work_turn_id"] == digest([
                    "polyphony-child-work-v1", row["child_id"], row["grant_digest"], 1,
                ]), "cold provisional report changed its immutable payload")
    first = value["rounds"][0]
    predecessor = {"exec_id": first["exec_id"], "work_outcome": first["initial_outcome"]}
    require(value["predecessor_digest"] == digest(predecessor),
            "cold status changed the original provisional predecessor")
    return value


def patch_value(page, child_id, actor):
    require(isinstance(page, dict) and page.get("ok") is True
            and page.get("child_id") == child_id and page.get("kind") == "patch"
            and page.get("final") is False and type(page.get("offset")) is int and page["offset"] == 0
            and isinstance(page.get("content"), str) and len(page["content"].encode()) <= 65536
            and type(page.get("total_bytes")) is int and page["total_bytes"] == len(page["content"].encode())
            and page.get("content_sha256") == page.get("artifact_sha256") == text_hash(page["content"]),
            "cold child patch is not a complete hashed provisional page")
    content = page["content"]
    expected = PARTIAL.rstrip("\n") if actor == "left" else "right implemented"
    require(content.count("diff --git ") == 1
            and f"diff --git a/{actor}.txt b/{actor}.txt" in content
            and "+" + expected in content and "-old" in content,
            "cold child patch changed an undeclared contribution")
    return content


class ColdFixture(ModelFixture):
    """One explicit follow-up, with its hold after both child inference loops."""

    root_limit, total_calls, compose_turn = 6, 10, 5

    def __init__(self, child_tools_sha256):
        super().__init__()
        require(hashed(child_tools_sha256), "cold fixture lacks the approved image module hash")
        self.child_tools_sha256 = child_tools_sha256
        self.measurements = RequestMeasurements(6, cold_session=True)
        self.root_turns = 0
        self.child_turns = {"left": 0, "right": 0}
        self.child_ids, self.patches, self.instructions, self.envelopes = {}, {}, {}, {}
        self.session_id = self.first_status = self.second_status = self.followup_status = None
        self.two_returned, self.release_root = threading.Event(), threading.Event()

    def __exit__(self, *args):
        self.release_root.set()
        return super().__exit__(*args)

    def _poll(self, result, ordinal):
        command = object_value(result)
        require(command.get("state") == "exited" and type(command.get("exit_code")) is int
                and command["exit_code"] == 0 and isinstance(command.get("output"), str),
                "cold status command did not finish within its original bound")
        observation = object_value(command["output"], 32768)
        require(set(observation) == {"status", "patch", "image_child_tools_sha256"}
                and observation["image_child_tools_sha256"] == self.child_tools_sha256,
                "cold poll did not use the approved trusted-image module")
        status = returned_status(observation["status"], ordinal, self.session_id)
        actor = "left" if ordinal == 1 else "right"
        row = status["rounds"][ordinal - 1]
        self.child_ids[actor] = row["child_id"]
        self.patches[actor] = patch_value(observation["patch"], row["child_id"], actor)
        return status

    def reply(self, payload):
        observed_ns = time.monotonic_ns()
        require(isinstance(payload, dict) and payload.get("model") == MODEL_NAME
                and payload.get("max_tokens") == 1024 and payload.get("stream") is False
                and payload.get("tool_choice") == "auto", "cold fixture request policy changed")
        messages = payload.get("messages", [])
        require(isinstance(messages, list) and len(messages) >= 2
                and messages[1].get("role") == "user"
                and isinstance(messages[1].get("content"), str), "cold fixture lacks initial context")
        initial = messages[1]["content"]
        envelope, raw = effective_envelope(initial) if initial.startswith("{") else (None, None)
        actor = ("left" if envelope["logical_round"] == 1 else "right") if envelope else "root"
        expected_tools = BASE_TOOLS | {"report_outcome"} if envelope else BASE_TOOLS | CHILD_TOOLS | SESSION_TOOLS
        require({row["function"]["name"] for row in payload.get("tools", [])} == expected_tools,
                "cold fixture tools changed or child received root controls")
        if not envelope:
            require(initial == REQUIREMENTS + "\n\nRoot repository instructions:\n" + ROOT_RULES,
                    "cold root requirements or repository rules changed")
        results = [row for row in messages if row.get("role") == "tool"]
        pause = False
        with self.lock:
            previous = self.root_turns if actor == "root" else self.child_turns[actor]
            require(len(results) == previous, "cold agent skipped or duplicated a tool result")
            if previous:
                require(results[-1].get("tool_call_id") == f"{actor}-{previous}"
                        and not results[-1].get("content", "").startswith("Tool error:"),
                        "cold preceding tool failed or changed identity")
            turn = previous + 1
            if envelope:
                require(turn <= 2, "cold child exceeded its original inference bound")
                require(self.session_id is None or envelope["session_id"] == self.session_id,
                        "cold child changed the logical session")
                if actor == "left":
                    require(envelope["followup"] is None and envelope["predecessor"] is None,
                            "first cold child acquired a fabricated predecessor")
                else:
                    require(self.first_status is not None and envelope["followup"] == FOLLOWUP
                            and envelope["predecessor"] == {
                                "exec_id": self.first_status["rounds"][0]["exec_id"],
                                "work_outcome": self.first_status["rounds"][0]["initial_outcome"],
                            }, "second cold child did not receive the frozen real predecessor")
                if turn == 1:
                    self.envelopes[actor], self.instructions[actor] = envelope, raw
                    self.measurements.session_instructions(actor, raw, observed_ns)
                else:
                    require(envelope == self.envelopes[actor] and raw == self.instructions[actor],
                            "cold child instruction changed within its two model calls")
                self.child_turns[actor] = turn
                if actor == "left":
                    tool, arguments = (
                        ("write_file", {"path": "left.txt", "content": PARTIAL}) if turn == 1 else
                        ("report_outcome", {"outcome": "needs_clarification", "summary": CLARIFICATION})
                    )
                else:
                    tool, arguments = (
                        ("write_file", {"path": "right.txt", "content": "right implemented\n"}) if turn == 1 else
                        ("finish", {"summary": COMPLETION})
                    )
            else:
                require(turn <= 6, "cold root exceeded its original inference bound")
                self.root_turns = turn
                if turn == 1:
                    tool, arguments = "start_child_session", {
                        "instructions": INITIAL, "seconds": 90, "max_turns": 2, "capabilities": ["python"],
                    }
                    self.measurements.session_prepared("left", time.monotonic_ns())
                elif turn == 2:
                    value = status_value(object_value(results[-1]["content"]))
                    require(len(value["rounds"]) == 1 and value["followup_request_id"] is None,
                            "cold start allocated another logical round")
                    self.session_id = value["session_id"]
                    if self.envelopes:
                        require(self.envelopes["left"]["session_id"] == self.session_id,
                                "actual start response differs from the first child envelope")
                    tool, arguments = "run_command", {"argv": [
                        "python3", "-I", "-S", "-c", POLL,
                        json.dumps({"session_id": self.session_id, "round": 1}),
                    ]}
                elif turn == 3:
                    self.first_status = self._poll(results[-1]["content"], 1)
                    tool, arguments = "continue_child_session", {
                        "session_id": self.session_id,
                        "predecessor_digest": self.first_status["predecessor_digest"],
                        "instructions": FOLLOWUP, "seconds": 90, "max_turns": 2,
                    }
                    self.measurements.session_prepared("right", time.monotonic_ns())
                elif turn == 4:
                    value = status_value(object_value(results[-1]["content"]), self.session_id)
                    require(len(value["rounds"]) == 2
                            and value["predecessor_digest"] == self.first_status["predecessor_digest"]
                            and identifier(value["followup_request_id"])
                            and value["rounds"][0]["child_id"] == self.child_ids["left"],
                            "cold follow-up changed its original predecessor or request")
                    self.followup_status = value
                    tool, arguments = "run_command", {"argv": [
                        "python3", "-I", "-S", "-c", POLL,
                        json.dumps({"session_id": self.session_id, "round": 2}),
                    ]}
                elif turn == 5:
                    self.second_status = self._poll(results[-1]["content"], 2)
                    require(self.second_status["rounds"][0] == self.first_status["rounds"][0]
                            and self.second_status["followup_request_id"] == self.followup_status["followup_request_id"]
                            and self.second_status["rounds"][1]["request_id"] == self.followup_status["rounds"][1]["request_id"]
                            and len(set(self.child_ids.values())) == 2
                            and self.child_turns == {"left": 2, "right": 2},
                            "cold completion replaced a predecessor, successor or model loop")
                    script = "import json,os,pathlib,socket,struct,subprocess\n" + DENY_SOCKET
                    script += "assert pathlib.Path('left.txt').read_text()=='old\\n'\n"
                    script += "assert pathlib.Path('right.txt').read_text()=='old\\n'\n"
                    script += "subprocess.run(['git','apply','-'],input=" + repr(self.patches["right"]) + ",text=True,check=True)\n"
                    script += "assert pathlib.Path('left.txt').read_text()=='old\\n'\n"
                    script += "pathlib.Path('left.txt').write_text('left implemented\\n')\n"
                    script += "assert os.getuid()==65532 and not os.access('/workspace/children',os.W_OK)\n"
                    script += "assert not pathlib.Path(" + repr("/run/openjules-model/children/" + self.child_ids["left"] + "/model.sock") + ").exists()\n"
                    script += "deny_socket(" + repr("/run/openjules-model/children/" + self.child_ids["right"] + "/model.sock") + ")\n"
                    script += "for child in " + repr(list(self.child_ids.values())) + ":\n"
                    script += " assert not os.access('/workspace/children/'+child+'/run',os.R_OK)\n"
                    script += " deny_socket('/run/openjules-model/children/'+child+'/outcome.sock')\n"
                    script += "subprocess.run(['python3','-m','unittest','discover','-v'],check=True)\n"
                    script += "print('ROOT_PRESERVED_COLD_PREDECESSOR_AND_COMPLETED_WORK')\n"
                    tool, arguments = "run_command", {"argv": ["python3", "-I", "-c", script]}
                    pause = True
                else:
                    result = object_value(results[-1]["content"])
                    require(result.get("state") == "exited" and type(result.get("exit_code")) is int
                            and result["exit_code"] == 0
                            and "ROOT_PRESERVED_COLD_PREDECESSOR_AND_COMPLETED_WORK" in result.get("output", ""),
                            "cold root did not compose and independently verify its declared work")
                    tool, arguments = "finish", {
                        "summary": "Preserved clarification and both physical captures; applied round2, completed left and passed combined tests; request independent review",
                    }
            self.measurements.request(actor, turn, payload, observed_ns)
            response = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"{actor}-{turn}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments)},
            }]}
            self.requests.append(payload)
        if pause:
            self.two_returned.set()
            require(self.release_root.wait(20), "cold observation gate was not released within its original bound")
        return response


def read_cold_rows(path, worker_id):
    """Read bounded committed rows, never export their private payloads."""
    from check_isolation_stack import read_journal

    with read_journal(path) as database:
        require(database.execute("PRAGMA user_version").fetchone()[0] == 1,
                "cold optional schema marker changed")
        for name, columns, sql in (
            ("cold_sessions", SESSION_COLUMNS, SESSION_SQL), ("cold_rounds", ROUND_COLUMNS, ROUND_SQL),
        ):
            found = database.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchall()
            require(len(found) == 1 and found[0][0] == sql
                    and tuple(row[1] for row in database.execute("PRAGMA table_info(" + name + ")")) == columns,
                    "cold optional schema does not match the qualified producer")
        result = {}
        for name, maximum in (("cold_sessions", 1), ("cold_rounds", 2), ("children", 2), ("intents", 2)):
            cursor = database.execute("SELECT * FROM " + name + " ORDER BY rowid")
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchmany(maximum + 1)
            require(len(rows) == maximum, "cold journal contains missing or extra controlled history")
            result[name] = [dict(zip(columns, row)) for row in rows]
            require(all(row["worker_id"] == worker_id for row in result[name]),
                    "cold journal contains foreign unaccounted execution history")
        cursor = database.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,))
        columns = [column[0] for column in cursor.description]
        workers = cursor.fetchmany(2)
        require(len(workers) == 1, "cold journal lacks its original worker")
        result["worker"] = dict(zip(columns, workers[0]))
    require(len(canonical(result).encode("utf-8")) <= 524288,
            "cold private journal observation exceeds its bound")
    return result


def finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def correlation(rows, model, scope, profile, task, attempt, canonical_children, *, final=False):
    """Compare independent parent, canonical and persisted runtime evidence."""
    session, rounds = rows["cold_sessions"][0], rows["cold_rounds"]
    worker = rows["worker"]["worker_id"]
    require(set(session) == set(SESSION_COLUMNS)
            and all(set(row) == set(ROUND_COLUMNS) for row in rounds)
            and [row["ordinal"] for row in rounds] == [1, 2]
            and session["worker_id"] == worker and session["session_id"] == model.session_id
            and all(row["worker_id"] == worker and row["session_id"] == model.session_id for row in rounds)
            and session["closure"] == "completed" and session["close_reason"] is None,
            "cold journal does not retain one completed two-round logical session")
    require(isinstance(canonical_children, list) and len(canonical_children) == 2,
            "cold session lost its two canonical children")
    canonical_by_id = {entry["child"]["id"]: entry["child"] for entry in canonical_children}
    require(len(canonical_by_id) == 2 and set(canonical_by_id) == set(model.child_ids.values()),
            "cold canonical child identity changed")
    root = object_value(rows["worker"]["context"], 262144)
    first_binding = canonical_by_id[model.child_ids["left"]]["binding"]
    start_key = model.first_status["rounds"][0]["request_id"]
    binding = object_value(session["binding"], 262144)
    expected_binding = {
        "schema_version": 1, "protocol": PROTOCOL, "session_id": model.session_id,
        **scope, "profile": profile, "worker_id": worker, "root_attempt_id": attempt,
        "parent_attempt_id": attempt, "task_id": task,
        "requirements_revision": first_binding["requirements_revision"],
        "runtime_principal": first_binding["runtime_principal"],
        "profile_digest": first_binding["profile_digest"],
        "child_policy_digest": rows["worker"]["policy_digest"],
        "start_request_id": start_key, "initial_instructions": INITIAL,
        "capabilities": ["python"], "max_rounds": 2,
    }
    require(canonical(binding) == canonical(expected_binding)
            and session["binding_digest"] == digest(expected_binding)
            and session["start_request_id"] == start_key
            and model.session_id == "session_" + digest([
                INSTRUCTION_PROTOCOL, scope["deployment_id"], scope["repository"],
                profile, worker, attempt, first_binding["requirements_revision"], start_key,
            ]), "cold retained binding differs from the real root and canonical scope")
    require(root["worker_id"] == worker and root["attempt_id"] == attempt
            and root["task_id"] == task and root["runtime_principal"] == first_binding["runtime_principal"]
            and root["profile_digest"] == first_binding["profile_digest"]
            and root["requirements_revision"] == first_binding["requirements_revision"]
            and finite(root["monotonic_deadline"]), "cold original root context changed")
    predecessor = {
        "exec_id": model.first_status["rounds"][0]["exec_id"],
        "work_outcome": model.first_status["rounds"][0]["initial_outcome"],
    }
    expected_predecessor = digest(predecessor)
    child_rows = {row["child_id"]: row for row in rows["children"]}
    intents = {row["request_id"]: row for row in rows["intents"]}
    require(len(child_rows) == len(intents) == 2 and set(child_rows) == set(model.child_ids.values()),
            "cold runtime history duplicated or lost a physical round")
    immutable_children, deadlines = {}, []
    for ordinal, actor in enumerate(("left", "right"), 1):
        row = rounds[ordinal - 1]
        child_id = model.child_ids[actor]
        child = child_rows[child_id]
        permission = canonical_by_id[child_id]
        grant = object_value(child["grant_json"], 262144)
        canonical_binding = permission["binding"]
        request_key = model.second_status["rounds"][ordinal - 1]["request_id"]
        expected_request = {
            "schema_version": 1, "request_id": request_key,
            "operation": "session_start" if ordinal == 1 else "session_followup",
            "instructions": INITIAL if ordinal == 1 else FOLLOWUP,
            "seconds": 90, "max_turns": 2,
            **({"capabilities": ["python"]} if ordinal == 1 else {
                "session_id": model.session_id, "predecessor_digest": expected_predecessor,
            }),
        }
        expected_payload = {
            "instructions": model.instructions[actor], "seconds": 90, "max_turns": 2,
            "capabilities": ["python"],
        }
        require(row["request_id"] == request_key and row["request_json"] == canonical(expected_request)
                and row["request_digest"] == digest(expected_request)
                and row["grant_payload"] == canonical(expected_payload)
                and row["instructions_digest"] == text_hash(model.instructions[actor])
                and row["child_id"] == child_id and row["grant_digest"] == canonical_binding["grant_digest"]
                and row["state"] == "linked" and row["reason"] is None
                and row["boot_id"] == child["boot_id"] == root["boot_id"],
                "cold round replaced its original request, grant or instruction")
        expected_snapshot = None if ordinal == 1 else canonical(predecessor)
        require(row["predecessor"] == expected_snapshot
                and row["predecessor_digest"] == (None if ordinal == 1 else expected_predecessor)
                and row["model_closed"] == (1 if ordinal == 1 else 0)
                and row["fence_established"] == (1 if ordinal == 1 else 0),
                "cold round lost its frozen predecessor or deliberate model-only fence")
        require(request_key in intents and intents[request_key]["payload"] == row["grant_payload"]
                and intents[request_key]["child_id"] == child_id
                and intents[request_key]["status"] == "bound",
                "cold round bypassed the original ordinary child delivery intent")
        require(grant["binding"] == canonical_binding
                and grant["instructions"] == model.instructions[actor]
                and canonical_binding["deployment_id"] == scope["deployment_id"]
                and canonical_binding["repository"] == scope["repository"]
                and canonical_binding["profile"] == profile
                and canonical_binding["worker_id"] == worker
                and canonical_binding["root_attempt_id"] == canonical_binding["parent_attempt_id"] == attempt
                and canonical_binding["task_id"] == task
                and canonical_binding["allocated_seconds"] == 90
                and type(canonical_binding["max_turns"]) is int and canonical_binding["max_turns"] == 2
                and canonical_binding["instructions_digest"] == row["instructions_digest"]
                and canonical_binding["capabilities"] == ["python"]
                and canonical_binding["launch_key"] == "polyphony-child:" + child_id
                and intents[request_key]["delivery_key"] == "openjules-child:" + digest([worker, attempt, request_key]),
                "cold canonical allocation does not match the original charged delivery")
        require(child["ordinal"] == ordinal - 1 and child["uid"] == 59999 + ordinal
                and child["exec_dispatched"] == 1 and identifier(child["exec_id"])
                and child["exec_id"] == model.second_status["rounds"][ordinal - 1]["exec_id"]
                and child["phase"] == ("completed" if final else "exec_returned")
                and type(child["exit_code"]) is int and child["exit_code"] == 0
                and all(finite(child[key]) for key in ("deadline", "armed_deadline"))
                and finite(row["cutoff"])
                and child["armed_deadline"] <= child["deadline"],
                "cold round lacks its original dispatched exec, UID or armed deadline")
        require(object_value(child["outcome_report"]) == model.second_status["rounds"][ordinal - 1]["initial_outcome"]["report"],
                "cold child changed its original private report")
        if final:
            require(child["capture"] is not None and child["artifacts"] is not None,
                    "cold final child lacks local capture references")
        else:
            require(child["capture"] is None and child["artifacts"] == "{}",
                    "cold provisional return was prematurely treated as final capture")
        immutable_children[child_id] = {key: child[key] for key in (
            "child_id", "worker_id", "ordinal", "uid", "grant_json", "deadline", "boot_id",
            "exec_id", "exec_dispatched", "armed_deadline", "outcome_report",
        )}
        deadlines.append({
            "logical_round": ordinal, "child_id": child_id, "uid": child["uid"],
            "exec_id": child["exec_id"], "canonical_allocated_seconds": 90,
            "local_permission_deadline": child["deadline"],
            "armed_deadline": child["armed_deadline"], "retained_round_cutoff": row["cutoff"],
        })
    first, second = deadlines
    require(first["armed_deadline"] < root["monotonic_deadline"]
            and second["local_permission_deadline"] > first["armed_deadline"]
            and second["retained_round_cutoff"] <= first["armed_deadline"]
            and second["armed_deadline"] <= first["armed_deadline"]
            and root["monotonic_deadline"] <= rounds[0]["cutoff"]
            and len({row["exec_id"] for row in deadlines}) == 2
            and len({row["grant_digest"] for row in rounds}) == 2
            and len({row["delivery_key"] for row in intents.values()}) == 2,
            "cold successor renewed an earlier watchdog or reused a physical allocation")
    return {
        "private": {
            "session": session, "rounds": rounds, "children": immutable_children,
            "root_context": root,
        },
        "report": {
            "session_id": model.session_id, "protocol": PROTOCOL, "logical_outcome": "completed",
            "canonical_session": False, "release_authorized": False,
            "predecessor_digest": expected_predecessor, "binding_digest": session["binding_digest"],
            "first_model_admission_closed": True, "model_fence": "established",
            "task_allocated_seconds": 360, "root_original_monotonic_deadline": rounds[0]["cutoff"],
            "root_observed_monotonic_deadline": root["monotonic_deadline"],
            "physical_rounds": deadlines,
            "same_key_correlations_sha256": digest({"session": session, "rounds": rounds}),
        },
    }


def require_same_correlation(original, current):
    before, after = original["private"], current["private"]
    require(after["root_context"]["monotonic_deadline"] <= before["root_context"]["monotonic_deadline"],
            "cold recovery extended the retained root cutoff")
    def frozen(value):
        return value | {"root_context": {key: item for key, item in value["root_context"].items()
                                         if key != "monotonic_deadline"}}
    require(frozen(after) == frozen(before),
            "cold finalization or recovery changed original session, allocation, report or deadline bytes")


def component_provenance(bundle, paths, commands, installation_data, source_environment, revisions):
    """Use generated interpreters/cwds; report only primary-source hashes."""
    import os
    from unittest.mock import patch
    from zpm.deployment import _environment

    result = {}
    for service in ("polyphony", "openjules", "zpm"):
        with patch.dict(os.environ, source_environment, clear=True):
            environment = _environment(bundle, service, installation_data)
        checkout = paths[service]
        tree = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD^{tree}"], text=True, timeout=15,
        ).strip()
        if service == "polyphony":
            require(revisions[service] == CURRENT_REVISION and tree == CURRENT_TREE,
                    "cold trial changed its separately approved positive authority")
            result[service] = source_provenance(
                checkout, commands[service][0], environment, CURRENT_REVISION, CURRENT_TREE, outcomes=True,
            )
            continue
        names = OJ_MODULES if service == "openjules" else (
            "zpm.deployment", "zpm.controller", "zpm.models", "zpm.store", "zpm.installation_context",
        )
        identity = verify_source(checkout, revisions[service], tree)
        pinned = _pinned_modules(checkout, revisions[service], names)
        response = subprocess.run(
            [commands[service][0], "-c", IMPORT_PROBE, json.dumps(names)], cwd=checkout,
            env=environment, capture_output=True, text=True, timeout=15,
        )
        require(response.returncode == 0 and 0 < len(response.stdout.encode()) <= 32768,
                "cold installed import provenance probe did not complete")
        value = object_value(response.stdout, 32768)
        require(set(value) == {"cwd", "executable", "modules"} and value["cwd"] == str(checkout)
                and Path(value["executable"]).absolute() == Path(commands[service][0]).absolute()
                and isinstance(value["modules"], dict) and set(value["modules"]) == set(names),
                "cold import provenance changed the generated interpreter or checkout")
        hashes = {}
        for name in names:
            path = checkout.joinpath(*name.split(".")).with_suffix(".py")
            entry = value["modules"][name]
            expected = hashlib.sha256(pinned[name]).hexdigest()
            require(isinstance(entry, dict) and set(entry) == {"origin", "sha256"}
                    and entry["origin"] == str(path) and entry["sha256"] == expected
                    and path.read_bytes() == pinned[name],
                    "cold service imported different pinned module bytes")
            hashes[name] = expected
        verify_source(checkout, revisions[service], tree)
        result[service] = identity | {"module_sha256": hashes}
    return result


def stop_installation(processes, installation):
    """Require the original manager's handled shutdown of all three services."""
    require(processes.running.get("installation") is installation,
            "cold installation manager identity changed before shutdown")
    processes.stop("installation")
    require(installation.poll() == 130,
            "cold original manager did not confirm handled service shutdown")


def close_owned(processes, release, original=None, *, reset=None, runtime=None, scope=None):
    """Attempt every owned cleanup and preserve the triggering failure."""
    from check_authority_admission import stop_owned

    release.set()
    failures = []
    if original is not None and reset is not None:
        try:
            reset()
        except BaseException:
            failures.append("target-zero")
    if original is not None and runtime is not None and scope is not None:
        try:
            found = runtime.request("GET", "/api/workers", query=scope)["workers"]
            require(isinstance(found, list) and len(found) <= 2,
                    "cold failure cleanup discovered unexpected owned workers")
            workers = [row["worker_id"] for row in found]
            require(len(workers) == len(set(workers)) and all(identifier(worker) for worker in workers),
                    "cold failure cleanup worker identity changed")
        except BaseException:
            workers = []
            failures.append("owned-worker-discovery")
        for worker in workers:
            for action in ("drain", "stop"):
                try:
                    runtime.request("POST", "/api/workers/" + worker + "/" + action, data={
                        "schema_version": 1, **scope, "request_key": "cold-failure-" + action + "-" + text_hash(worker)[:32],
                    })
                except BaseException:
                    failures.append("owned-worker-" + action)
    for name in reversed(list(processes.running)):
        owned = processes.running[name]
        try:
            stop_owned(processes, name)
        except BaseException:
            failures.append("owned-service-" + name)
        if name == "installation" and owned.poll() != 130:
            failures.append("manager-shutdown-unconfirmed")
    for handle in processes.handles:
        try:
            handle.close()
        except BaseException:
            failures.append("owned-log")
    if failures:
        if original is None:
            raise AssertionError("cold owned cleanup incomplete: " + ",".join(failures))
        original.add_note("cold owned cleanup incomplete: " + ",".join(failures))
    return failures


def publish_report(path, report, processes, release):
    """Publish a passed artifact only after every owned service closes cleanly."""
    close_owned(processes, release)
    private_json(path, report)


def configured_events(content):
    require(isinstance(content, str) and len(content.encode("utf-8")) <= 262144,
            "cold captured verification log exceeds its fixed bound")
    lines = content.splitlines()
    require(len(lines) <= 512, "cold captured verification log has too many records")
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(event, dict) and event.get("event") == "configured_verification":
            events.append(event)
    return events


def verify_child_log(actor, content, patch):
    """Keep semantic clarification separate from configured completion."""
    verifications = configured_events(content)
    if actor == "left":
        require(not verifications and "+" + PARTIAL.rstrip("\n") in patch,
                "cold clarification was relabeled as verified completion")
    else:
        require(actor == "right" and len(verifications) == 1
                and verifications[0].get("state") == "exited"
                and type(verifications[0].get("exit_code")) is int
                and verifications[0]["exit_code"] == 0
                and "CHILD_STACK_BOUNDARY_VERIFIED uid=60001" in verifications[0].get("output", ""),
                "cold second child lacks actual configured fresh-checkout and UID verification")


def archive_workspaces(path, model):
    """Observe the real bounded archive without extracting private work."""
    import tarfile

    expected = {
        "workspace/run/repo/left.txt": b"left implemented\n",
        "workspace/run/repo/right.txt": b"right implemented\n",
    }
    for actor, child_id in model.child_ids.items():
        prefix = "workspace/children/" + child_id + "/run/repo/"
        expected[prefix + "left.txt"] = PARTIAL.encode() if actor == "left" else b"old\n"
        expected[prefix + "right.txt"] = b"old\n" if actor == "left" else b"right implemented\n"
    with tarfile.open(path) as archive:
        require(len(archive.getmembers()) <= 4096, "cold archive exceeds its entry bound")
        for name, content in expected.items():
            member = archive.getmember(name)
            require(member.isfile() and member.size == len(content),
                    "cold archive lost its separate original checkout")
            with archive.extractfile(member) as stream:
                require(stream.read(len(content) + 1) == content,
                        "cold successor copied or changed the predecessor checkout")
        for actor, child_id in model.child_ids.items():
            member = archive.getmember("workspace/children/" + child_id + "/run/repo/.git/HEAD")
            require(member.isfile() and member.uid == (60000 if actor == "left" else 60001),
                    "cold physical checkout lost its separately owned UID")
    return {
        "distinct_child_checkouts": 2, "first_provisional_edit_retained": True,
        "second_started_from_original_base": True, "root_applied_only_second_patch": True,
    }


def run(args):
    zpm = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(zpm))
    from zpm.controller import Controller
    from zpm.deployment import MODEL_TOKEN_ENV, _command, prepare, verify
    from zpm.models import released, snapshot as worker_snapshot
    from zpm.store import Store

    root = args.work_dir.absolute()
    require(not root.exists(), "cold trial work directory must be new")
    root.mkdir(mode=0o700)
    paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
    revisions = {name: subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=15,
    ).strip() for name, path in paths.items()}
    guest_source = _pinned_modules(paths["openjules"], revisions["openjules"], ("src.agent.child_tools",))
    guest_hash = hashlib.sha256(guest_source["src.agent.child_tools"]).hexdigest()
    processes = Processes(root)
    checks, sensitive, capture_observations = [], set(), []
    request_observation = ColdRequestObservation()
    measurements = Measurements(cold_session=True)
    scope = {"deployment_id": "cold-children-trial-" + secrets.token_hex(8),
             "repository": "github:github.com/managed-cold-trial/repository"}
    profile = "approved-cold-children-v1"
    runtime, reset_target, original = None, None, None
    cleanup_completed = False
    with ColdFixture(guest_hash) as model:
        sensitive.add(model.token)
        try:
            image = subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True, timeout=30,
            ).strip()
            require(image == args.image and re.fullmatch(r"sha256:[0-9a-f]{64}", image),
                    "cold trial requires an existing immutable managed-agent image")
            source = root / "source"
            source.mkdir(mode=0o755)
            for name in ("left", "right"):
                (source / (name + ".txt")).write_text("old\n")
            (source / "test_combined.py").write_text(
                "from pathlib import Path\nimport unittest\n\nclass CombinedTests(unittest.TestCase):\n"
                "    def test_both_contributions(self):\n        for name in ('left','right'):\n"
                "            self.assertEqual(Path(name+'.txt').read_text(),name+' implemented\\n')\n")
            (source / "AGENTS.md").write_text(ROOT_RULES)
            (source / ".gitignore").write_text(".env\n__pycache__/\n")
            for command in (
                ["init", "-q"], ["add", "."],
                ["-c", "user.name=Cold Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled base"],
            ):
                subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True, timeout=15)
            (source / ".env").write_text("TRIAL_HOST_ONLY=must-not-enter-the-worker\n")
            base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
            authority_port, runtime_port = port(), port()
            while authority_port == runtime_port:
                runtime_port = port()
            bundle = root / "installation"
            measurements.start("prepare")
            prepare({
                "schema_version": 1, "deployment_id": scope["deployment_id"],
                "binding": {"schema_version": 1, "repository": scope["repository"], "profile": profile, "desired_workers": 0},
                "max_workers": 1,
                "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in paths.items()},
                "source": {"checkout": str(source), "revision": base}, "image": image,
                "command": [
                    "python3", "-I", "-S", "/opt/openjules/managed_root_runner.py",
                    "--socket", "/run/openjules-model/model.sock",
                    "--control-socket", "/run/openjules-model/control.sock",
                    "--max-turns", "6", "--verify-json", json.dumps(VERIFY),
                ],
                "ports": {"polyphony": authority_port, "openjules": runtime_port},
                "runtime": {"max_seconds": 240},
                "policy": {"checks": ["repository-tests"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 360, "attempt_seconds": 180, "capabilities": ["python"]},
                "model_policy": {
                    "endpoint": model.endpoint, "model": MODEL_NAME, "token_ref": "env:" + TOKEN_SOURCE,
                    "max_calls": 10, "max_output_tokens": 1024, "max_total_output_tokens": 10240,
                    "request_timeout_seconds": 30,
                },
                "child_policy": {
                    "max_children": 2, "max_child_seconds": 90, "max_child_turns": 2,
                    "capabilities": ["python"], "uid_start": 60000, "verify_argv": CHILD_VERIFY,
                    "outcome_protocol": "initial-v1", "session_protocol": PROTOCOL,
                },
            }, bundle)
            measurements.stop("prepare")
            measurements.start("verify")
            verify(bundle)
            measurements.stop("verify")
            environment_tokens = json.loads((bundle / "secrets/environment.json").read_text())
            operator_tokens = json.loads((bundle / "secrets/operator.json").read_text())
            sensitive.update(environment_tokens.values())
            sensitive.update(operator_tokens.values())
            runtime_config = json.loads((bundle / "config/openjules.json").read_text())
            canonical_config = json.loads((bundle / "config/polyphony.json").read_text())
            child_policy = runtime_config["children"][0]["policy"]
            canonical_profile = canonical_config["repositories"][scope["repository"]]["profiles"][profile]
            require(child_policy.get("session_protocol") == PROTOCOL
                    and child_policy.get("outcome_protocol") == "initial-v1"
                    and all(child_policy[key] == canonical_profile[key] for key in (
                        "max_children", "max_child_seconds", "max_child_turns",
                    )) and child_policy["capabilities"] == canonical_profile["child_capabilities"]
                    and "session_protocol" not in canonical_profile and "outcome_protocol" not in canonical_profile,
                    "cold installation changed the existing canonical child-policy projection")
            require(runtime_config["models"][0]["policy"]["token_ref"] == "env:" + MODEL_TOKEN_ENV
                    and runtime_config["profiles"][0]["environment_refs"] == {},
                    "cold installation exposed a provider credential to guest environment")
            state = bundle / "state/zpm.sqlite3"
            authority_endpoint = f"http://127.0.0.1:{authority_port}"
            authority = Client(authority_endpoint, environment_tokens["POLYPHONY_RUNTIME_TOKEN"])
            ingress = Client(authority_endpoint, operator_tokens["ingress"])
            runtime = Client(f"http://127.0.0.1:{runtime_port}", environment_tokens["OPENJULES_WORKER_TOKEN"])
            manager = [sys.executable, str(bundle / "manage.py")]
            manager_environment = clean_environment({TOKEN_SOURCE: model.token})
            installation_data = json.loads((bundle / "manifest.json").read_text())["configuration"]
            commands = {name: _command(bundle, name, installation_data) for name in ("polyphony", "openjules", "zpm")}
            retained_paths = journal_paths(bundle, runtime_config)
            measured_paths = retained_paths | {
                "canonical": Path(commands["polyphony"][commands["polyphony"].index("--db") + 1]),
            }
            captures = retained_paths["runtime"].parent / "captures"

            def storage_phase(label):
                measurements.storage_phase(label, bundle.resolve(), measured_paths, captures)

            def plan():
                with Store(state, scope["deployment_id"]) as store:
                    return Controller(store).plan()

            def change_target(count):
                with Store(state, scope["deployment_id"]) as store:
                    store.change(scope["repository"], "target", count)

            def target(count, **kwargs):
                change_target(count)
                return wait_for_capacity(plan, processes, count, **kwargs)

            reset_target = lambda: change_target(0)

            def snapshot():
                return authority.request("GET", "/managed/snapshot", query=scope)

            def children(attempt_id):
                return authority.request("GET", "/managed/children", query=scope | {"attempt_id": attempt_id})

            def installed_ready():
                result = subprocess.run(
                    manager + ["doctor"], cwd=bundle, env=clean_environment(),
                    capture_output=True, text=True, timeout=50,
                )
                require(result.returncode in {0, 3}, "generated cold doctor could not inspect its bundle")
                return json.loads(result.stdout).get("ready") is True

            storage_phase("prepared")
            measurements.start("bootstrap")
            processes.run_once("bootstrap", manager + ["bootstrap"], bundle, clean_environment(), 900)
            measurements.stop("bootstrap")
            provenance = component_provenance(
                bundle, paths, commands, installation_data, manager_environment, revisions,
            )
            measurements.start("initial_manager_ready")
            installation = processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "generated cold installation readiness", 60)
            measurements.stop("initial_manager_ready")
            require(plan()["reserved"] == 0, "cold readiness allocated a worker")
            with model.lock:
                require(not model.requests and not model.errors, "cold readiness requested inference")
            storage_phase("ready_target_zero")
            measurements.start("target_one_converged")
            target(1)
            measurements.stop("target_one_converged")
            idle_binding = idle_worker_binding(plan())
            require({key: idle_binding[key] for key in ("deployment_id", "repository", "profile")}
                    == scope | {"profile": profile}, "cold idle measurement changed its installed scope")
            with model.lock:
                idle_counts_before = {"requests": len(model.requests), "errors": len(model.errors)}
                require(not any(idle_counts_before.values()), "cold idle worker requested inference")
            # All prior doctor transports have returned and been reaped. Only
            # the original watch issues scoped lifecycle requests here.
            idle = idle_process_sample(
                installation, commands | {"manager": manager + ["start"]},
                {name: Path(installation_data["sources"][name]["checkout"]) for name in commands}
                | {"manager": bundle.resolve()},
                log_path=processes.root / "installation.log", binding=idle_binding,
            )
            idle_plan_after = plan()
            with model.lock:
                idle_counts_after = {"requests": len(model.requests), "errors": len(model.errors)}
            confirm_idle_reconciliation(idle, idle_binding, idle_plan_after, idle_counts_before, idle_counts_after)
            storage_phase("idle_before_enrollment")
            checks.append("the sealed opt-in bundle starts the real three services with the unchanged canonical child-policy projection and zero idle inference")
            request = {
                "schema_version": 1, **scope, "source": "github:issue:controlled-cold-trial",
                "request_key": "run-1", "title": "Controlled cold clarification", "requirements": REQUIREMENTS,
            }
            with model.lock:
                model.measurements.enrollment_started(time.monotonic_ns())
            task = request_observation.request(
                "enroll", ingress, "POST", "/managed/enroll", data=request,
            )["task"]["id"]
            require(request_observation.request(
                "enroll-replay", ingress, "POST", "/managed/enroll", data=request,
            )["task"]["id"] == task,
                    "cold ingress replay allocated a second task")
            request_observation.request(
                "foreign-scope-refusal", ingress, "POST", "/managed/enroll", data=request | {
                    "repository": "github:github.com/foreign/repository",
                }, expected=403,
            )

            def returned():
                require(not model.errors, "cold deterministic fixture rejected a request")
                return model.two_returned.is_set()

            eventually(returned, processes, "both cold physical child execs returned", 60)
            try:
                active = request_observation.request(
                    "active-snapshot", authority, "GET", "/managed/snapshot", query=scope,
                )
                require(len(active["task"]) == len(active["attempt"]) == 1,
                        "cold follow-up allocated another root task or attempt")
                active_attempt = active["attempt"][0]
                attempt_id = active_attempt["id"]
                worker = active_attempt["runtime_binding"]["worker_id"]
                active_children = request_observation.request(
                    "active-children", authority, "GET", "/managed/children",
                    query=scope | {"attempt_id": attempt_id},
                )["children"]
                require(len(active_children) == 2 and all(
                    row["launch_allowed"] is True and row["child"]["termination_confirmed"] is False
                    for row in active_children), "cold model-only closure changed generic canonical permission")
                cold_before = correlation(
                    read_cold_rows(retained_paths["children"], worker), model, scope, profile,
                    task, attempt_id, active_children,
                )
                first_socket = Path(runtime_config["state_directory"]) / "models" / worker / "children" / model.child_ids["left"] / "model.sock"
                try:
                    first_socket.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("cold predecessor model endpoint was recreated after its fence")
                observed = request_observation.request(
                    "active-worker", runtime, "GET", "/api/workers/" + worker, query=scope,
                )
                require(set(observed["active_executions"]) == {attempt_id, *model.child_ids.values()},
                        "cold exec return released a lifecycle execution reference")
                capacity = plan()
                row = capacity["repositories"][0]
                require(capacity["reserved"] == row["reserved"] == row["desired_workers"] == row["effective_target"] == 1
                        and [item["worker_id"] for item in row["workers"]] == [worker]
                        and active["task"][0]["allocated_seconds"] == 360,
                        "cold rounds changed the one-worker target or full canonical time charge")
                change_target(0)

                def draining():
                    require(not model.errors, "cold fixture failed during retained-capacity observation")
                    capacity = plan()
                    row = capacity["repositories"][0]
                    require(row["desired_workers"] == row["effective_target"] == 0
                            and capacity["reserved"] == row["reserved"] == 1
                            and [item["worker_id"] for item in row["workers"]] == [worker],
                            "cold logical completion released or replaced the active root worker")
                    observed = runtime.request("GET", "/api/workers/" + worker, query=scope)
                    require(set(observed["active_executions"]) == {attempt_id, *model.child_ids.values()},
                            "cold drain dropped a root or physical-child reference")
                    current = children(attempt_id)
                    require({row["child"]["id"] for row in current["children"]} == set(model.child_ids.values())
                            and all(row["launch_allowed"] is True
                                    and row["child"]["termination_confirmed"] is False for row in current["children"]),
                            "cold drain revoked or lost a separately charged canonical grant")
                    permission = authority.request("POST", "/managed/permission", data={
                        "schema_version": 1, **scope, "attempt_id": attempt_id,
                    })
                    require(permission["launch_allowed"] is True, "cold drain revoked the original root permission")
                    if (observed["state"] == row["workers"][0]["state"] == "draining"
                            and observed["admission_open"] is row["workers"][0]["admission_open"] is False
                            and current["admission_closed"] is True):
                        return {
                            "top_level_target": 0, "reserved_workers": 1, "active_executions": 3,
                            "worker_state": "draining", "worker_admission_open": False,
                            "canonical_child_admission_closed": True, "existing_permissions_allowed": True,
                            "predecessor_model_admission_closed": True,
                        }
                    return None

                drain_observation = eventually(draining, processes, "cold active-worker scale-to-zero", 12)
            finally:
                model.release_root.set()
            checks.append("one logical session has two real canonical grants, distinct physical identities and the full180+90+90 charge; the earlier predecessor watchdog still bounds the successor")
            checks.append("after both child execs return, target zero still retains one worker and three references until final capture; canonical permission remains separate from model-only closure")

            def captured():
                capacity = plan()
                current = snapshot()
                attempts = [row for row in current["attempt"] if row["task"] == task]
                candidates = [row for row in current["candidate"] if row["task"] == task]
                if capacity["reserved"] == 0:
                    require(len(attempts) == 1 and attempts[0].get("termination_confirmed") is True
                            and attempts[0].get("launch_fenced") is True
                            and set(attempts[0].get("captured_artifacts", {})) == {"patch", "log", "manifest"},
                            "cold capacity released before the canonical root final receipt")
                    final_children = children(attempt_id)["children"]
                    require(len(final_children) == 2 and all(
                        row["child"].get("termination_confirmed") is True
                        and row["child"].get("launch_fenced") is True
                        and set(row["child"].get("captured_artifacts", {})) == {"patch", "log", "manifest"}
                        for row in final_children), "cold capacity released before both child final receipts")
                require(not model.errors, "cold deterministic fixture rejected a request")
                return (current, attempts[0], candidates[0]) if len(attempts) == len(candidates) == 1 and attempts[0].get("termination_confirmed") else None

            evidence, attempt, candidate = eventually(captured, processes, "cold root composition and final captures", 100)
            require(len(evidence["task"]) == len(evidence["attempt"]) == 1
                    and len(model.requests) == 10 and model.root_turns == 6
                    and model.child_turns == {"left": 2, "right": 2}
                    and attempt.get("launch_fenced") is True,
                    "cold completion duplicated work or lost its root launch fence")
            artifacts = read_artifacts(authority, scope, attempt["captured_artifacts"], sensitive)
            root_manifest = object_value(artifacts["manifest"], 262144)
            require(root_manifest.get("worker_id") == worker and root_manifest.get("attempt_id") == attempt_id
                    and root_manifest.get("base_commit") == base
                    and root_manifest.get("termination_confirmed") is True
                    and root_manifest.get("launch_fenced") is True,
                    "cold root manifest changed its approved execution")
            verify_root_log(artifacts["log"], compose_turn=5)
            canonical_children = children(attempt_id)
            require(canonical_children["admission_closed"] is True
                    and len(canonical_children["children"]) == 2,
                    "cold root completion lost grants or reopened admission")
            cold_final = correlation(
                read_cold_rows(retained_paths["children"], worker), model, scope, profile,
                task, attempt_id, canonical_children["children"], final=True,
            )
            require_same_correlation(cold_before, cold_final)
            child_artifacts, child_evidence, lineage, policy_digests = {}, [], [], set()
            for permission in canonical_children["children"]:
                child = permission["child"]
                actor = next(name for name, identity in model.child_ids.items() if identity == child["id"])
                binding = child["binding"]
                require(child.get("termination_confirmed") is True and child.get("launch_fenced") is True
                        and child.get("state") == "exited" and permission["launch_allowed"] is False
                        and type(child["turns_allocated"]) is int and child["turns_allocated"] == 2
                        and binding["base"] == base, "cold canonical child lacks its separate final charge and fence")
                captured_child = read_artifacts(
                    authority, scope, child["captured_artifacts"], sensitive,
                    attempt_id=attempt_id, child_id=child["id"],
                )
                child_artifacts[child["id"]] = captured_child
                manifest = object_value(captured_child["manifest"], 262144)
                uid = 60000 if actor == "left" else 60001
                require(manifest.get("child_id") == child["id"] and manifest.get("root_attempt_id") == attempt_id
                        and manifest.get("worker_id") == worker and manifest.get("grant_digest") == binding["grant_digest"]
                        and type(manifest.get("uid")) is int and manifest["uid"] == uid
                        and manifest.get("exec_id") == cold_before["private"]["children"][child["id"]]["exec_id"]
                        and manifest.get("exec_start_dispatched") is True
                        and manifest.get("not_started") is False and manifest.get("workspace_present") is True,
                        "cold child capture lacks the exact physical launch and UID")
                require(manifest.get("archive_sha256") == root_manifest["archive_sha256"]
                        and manifest.get("root_manifest_sha256") == attempt["captured_artifacts"]["manifest"]
                        and manifest.get("termination_method") == "whole-container fence"
                        and manifest.get("termination_confirmed") is True and manifest.get("launch_fenced") is True
                        and manifest.get("patch_present") is True and manifest.get("log_present") is True
                        and manifest.get("armed_deadline_monotonic") == cold_before["private"]["children"][child["id"]]["armed_deadline"]
                        and manifest.get("deadline_monotonic") == cold_before["private"]["children"][child["id"]]["deadline"],
                        "cold final capture changed an original cutoff or archive fence")
                require(captured_child["patch"] == model.patches[actor],
                        "cold final patch differs from its original provisional result")
                observation = child_capture_observation(actor, uid, captured_child["log"])
                capture_observations.append(observation)
                verify_child_log(actor, captured_child["log"], captured_child["patch"])
                provisional = model.second_status["rounds"][0 if actor == "left" else 1]["initial_outcome"]
                retained_outcome = {key: value for key, value in provisional.items()
                                    if key not in {"canonical_committed", "release_authorized"}}
                require(manifest.get("work_outcome") == retained_outcome
                        and child.get("initial_outcome") == retained_outcome | {
                            "artifacts": child["captured_artifacts"], "canonical_committed": True, "release_authorized": False,
                        }, "cold final outcome replaced the original private ordinal-one report")
                lineage.append({"child_id": child["id"], "grant_digest": binding["grant_digest"],
                                "artifacts": child["captured_artifacts"]})
                policy_digests.add(manifest["child_policy"])
                child_evidence.append({
                    "logical_round": 1 if actor == "left" else 2, "child_id": child["id"], "uid": uid,
                    "exec_id": manifest["exec_id"], "turns": 2, "grant_digest": binding["grant_digest"],
                    "artifacts": child["captured_artifacts"], "semantic_outcome": retained_outcome["report"]["outcome"],
                    "canonical_committed": True, "canonical_ordinal": 1,
                    "configured_verification_passed": actor == "right",
                })
            lineage.sort(key=lambda value: value["child_id"])
            require(attempt.get("child_lineage") == candidate.get("child_lineage") == lineage
                    and candidate["patch"] == attempt["captured_artifacts"]["patch"]
                    and candidate["base"] == base and evidence["task"][0]["status"] == "awaiting_review"
                    and not evidence.get("admission"), "cold candidate replaced lineage or self-authorized acceptance")
            allowance = inference_reservations(runtime_config, worker, attempt_id, 10)
            require(all(summary not in json.dumps(evidence, ensure_ascii=False)
                        for summary in (CLARIFICATION, COMPLETION)), "cold private outcome leaked into broad canonical state")
            checks.append("six root and four child calls share exactly ten durable reservations; each physical child retains its own ordinal-one semantic report and final artifact lineage")
            checks.append("clarification retains an unverified provisional patch; the second child proves original-base isolation and configured verification before the root composes only its patch")

            verifier = root / "artifact-verification"
            subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(verifier)], check=True, timeout=15)
            subprocess.run(["git", "-C", str(verifier), "apply", "--check", "-"],
                           input=artifacts["patch"], text=True, check=True, timeout=10)
            subprocess.run(["git", "-C", str(verifier), "apply", "-"],
                           input=artifacts["patch"], text=True, check=True, timeout=10)
            changed = subprocess.check_output(["git", "-C", str(verifier), "diff", "--name-only"], text=True, timeout=10).splitlines()
            require(changed == ["left.txt", "right.txt"] and PARTIAL.rstrip("\n") not in artifacts["patch"],
                    "cold root candidate copied the provisional edit or changed other files")
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=verifier, check=True, timeout=10)
            require(all((source / (name + ".txt")).read_text() == "old\n" for name in ("left", "right")),
                    "cold agent changed the approved source checkout")
            storage_phase("capture_verified")
            wait_for_capacity(plan, processes, 0, retired_worker_ids={worker})
            require(plan()["repositories"][0]["desired_workers"] == 0
                    and released(worker_snapshot(runtime.request("GET", "/api/workers/" + worker, query=scope))),
                    "cold drained worker lacks durable retirement")
            archive = original_capture(retained_paths, worker, scope, artifacts)
            workspace_observation = archive_workspaces(archive["archive"], model)
            checks.append("the real root archive retains two separately owned checkouts; round2 starts from the approved base while the root independently completes the combined result")
            replacement = target(1, retired_worker_ids={worker})
            idle_workers = replacement["repositories"][0]["workers"]
            require(len(idle_workers) == 1 and idle_workers[0]["worker_id"] != worker,
                    "cold cleanup lacks a distinct untouched idle worker")
            idle_worker = idle_workers[0]["worker_id"]
            container_ids = subprocess.check_output(
                ["docker", "ps", "-aq", "--filter", "label=openjules.worker=" + worker], text=True, timeout=10,
            ).split()
            require(len(container_ids) == 1, "cold rounds used more than one worker Docker incarnation")
            container = json.loads(subprocess.check_output(["docker", "inspect", container_ids[0]], text=True, timeout=10))[0]
            require(container["Id"] == root_manifest["container_id"] and not container["State"]["Running"]
                    and container["HostConfig"]["NetworkMode"] == "none" and container["HostConfig"]["ReadonlyRootfs"] is True
                    and container["HostConfig"]["CapDrop"] == ["ALL"]
                    and policy_digests == {container["Config"]["Labels"]["openjules.child_policy"]},
                    "cold container isolation or immutable child policy changed")
            require(all(item["Destination"] != "/var/run/docker.sock" for item in container["Mounts"])
                    and all(token not in json.dumps(container) for token in sensitive),
                    "cold container metadata exposes a host credential or Docker socket")
            checks.append("all original root and child references release only after the shared container fence and exact canonical captures; no same-process child continuation is claimed")
            target(0)
            stop_installation(processes, installation)

            def retained_canonical():
                current = snapshot()
                require(all(current[kind] == evidence[kind] for kind in ("task", "attempt", "candidate")),
                        "cold cleanup changed canonical root evidence")
                current_children = children(attempt_id)
                require(current_children["admission_closed"] is True
                        and {row["child"]["id"]: row["child"] for row in current_children["children"]}
                        == {row["child"]["id"]: row["child"] for row in canonical_children["children"]},
                        "cold cleanup changed canonical child evidence")
                require(read_artifacts(authority, scope, attempt["captured_artifacts"], sensitive) == artifacts,
                        "cold cleanup changed canonical root artifact bytes")
                for child in child_evidence:
                    require(read_artifacts(authority, scope, child["artifacts"], sensitive,
                                           attempt_id=attempt_id, child_id=child["child_id"])
                            == child_artifacts[child["child_id"]], "cold cleanup changed child artifact bytes")
                return True

            cleanup = qualify_owned_cleanup(
                bundle, paths, scope, worker, idle_worker, manager_environment,
                processes, artifacts, retained_canonical,
                canonical_proof={"binding": attempt["runtime_binding"],
                                 "runtime_receipt": attempt["runtime_receipt"],
                                 "child_lineage": attempt["child_lineage"]},
            )
            require(not processes.running, "cold cleanup observation requires stopped owned services")
            require_same_correlation(cold_before, correlation(
                read_cold_rows(retained_paths["children"], worker), model, scope, profile,
                task, attempt_id, canonical_children["children"], final=True,
            ))
            storage_phase("cleanup_stopped")
            checks.append("scoped installed cleanup and its exact replay preserve both cold session tables through the existing all-table journal digest, canonical captures and the idle non-target worker")
            verify(bundle)
            measurements.start("restart_manager_ready")
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "cold installation full restart", 60)
            measurements.stop("restart_manager_ready")
            restored_provenance = component_provenance(
                bundle, paths, commands, installation_data, manager_environment, revisions,
            )
            require(restored_provenance == provenance, "cold restart changed actual installed module provenance")
            authority = Client(authority_endpoint, environment_tokens["POLYPHONY_RUNTIME_TOKEN"])
            require(retained_canonical(), "cold restart changed original canonical evidence")
            replayed = runtime.request("POST", "/api/workers/ensure", data=cleanup["spec"])
            require(replayed.get("worker_id") == worker and released(worker_snapshot(replayed))
                    and not replayed.get("active_executions"), "cold original ensure replay resurrected a retired worker")
            require_retained(retained_paths, worker, cleanup)
            require_same_correlation(cold_before, correlation(
                read_cold_rows(retained_paths["children"], worker), model, scope, profile,
                task, attempt_id, children(attempt_id)["children"], final=True,
            ))
            require(plan()["reserved"] == 0 and len(model.requests) == 10 and not model.errors
                    and inference_reservations(runtime_config, worker, attempt_id, 10) == allowance,
                    "cold restart changed capacity or repeated inference")
            storage_phase("restart_replayed")
            checks.append("full service restart and original ensure replay preserve every original session/request/grant/report/cutoff without new allocation, inference or resource resurrection")
            with model.lock:
                request_measurements = model.measurements.report(
                    model.child_ids, canonical_children["children"],
                    worker_id=worker, task_id=task, attempt_id=attempt_id,
                )
            operational = measurements.report(idle, request_measurements)
            checks.append("bounded cold response-prepared latency and logical context observations bind actual instructions to canonical grants; the idle PID/starttime interval contains a complete controller cycle with zero inference and unchanged worker; six storage phases retain their original measurement limits")
            report = {
                "passed": True, "schema_version": 1, "deployment_id": scope["deployment_id"], "image": image,
                "component_revisions": revisions, "component_provenance": provenance,
                "restored_component_provenance": restored_provenance, "image_child_tools_sha256": guest_hash,
                "installation_bootstrap_and_doctor": "passed", "checks": checks,
                "canonical_task": task, "root_attempt": attempt_id, "candidate": candidate["id"],
                "captured_artifacts": attempt["captured_artifacts"],
                "children": sorted(child_evidence, key=lambda child: child["logical_round"]),
                "cold_session": cold_final["report"] | {"final_canonical_child_receipts": 2},
                "workspace_observation": workspace_observation,
                "active_observation": {"top_level_target": 1, "reserved_workers": 1, "active_executions": 3, "children": 2},
                "active_scale_to_zero_observation": drain_observation,
                "inference_allowance": allowance, "model_fixture_calls": len(model.requests), "final_reserved": plan()["reserved"],
                "operational_measurements": operational,
                "owned_cleanup": cleanup["report"] | {"original_ensure_replay": "same retired worker; no resurrection"},
                "proof": "actual generated installation, canonical HTTP grants, Docker cold children, original model fence and final captures; deterministic localhost model replies",
                "publication_compatibility": {
                    "qualified": False,
                    "reason": "root adds work beyond child patches; publication is not invoked",
                },
                "not_proven": [
                    "warm child process or context reuse", "live provider compatibility", "model reasoning quality",
                    "financial spend bound", "public discussion or actual A/C", "forge publication", "merge authorization",
                    "forced crash during installed follow-up", "automatic root-agent process resumption",
                ],
            }
            serialized = json.dumps(report, ensure_ascii=False)
            require(all(token not in serialized for token in sensitive)
                    and all(summary not in serialized for summary in (CLARIFICATION, COMPLETION)),
                    "cold shareable report contains a private credential or outcome summary")
            publish_report(root / "report.json", report, processes, model.release_root)
            cleanup_completed = True
            return report
        except BaseException as exc:
            original = exc
            try:
                diagnostic = diagnostics(
                    root, str(exc) if isinstance(exc, AssertionError) else type(exc).__name__,
                    checks, revisions, sensitive,
                )
                diagnostic["cold_capture_observations"] = capture_observations
                diagnostic["cold_request_failure"] = request_observation.report()
                pending = root / "cold-diagnostics.pending"
                private_json(pending, diagnostic)
                pending.replace(root / "diagnostics.json")
                print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
            except (OSError, ValueError):
                pass
            raise
        finally:
            if not cleanup_completed:
                close_owned(processes, model.release_root, original,
                            reset=reset_target, runtime=runtime, scope=scope)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument(f"--{name}-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="existing immutable managed-agent image ID")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private retained trial directory")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, AssertionError) else "cold trial failed; inspect retained private diagnostics"
        print(json.dumps({"passed": False, "error": message}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
