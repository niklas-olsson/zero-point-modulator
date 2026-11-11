"""Qualify installed child delegation with real services and Docker execution.

Only model replies are a deterministic localhost fixture. Polyphony grants,
OpenJules execution, ZPM capacity, capture and restart use their real APIs and
generated installation. Export only report.json or redacted diagnostics.json.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading

from check_model_stack import ModelFixture, TOKEN_SOURCE
from check_stack import (
    Client, Processes, clean_environment, diagnostics, eventually, port,
    private_json, require, wait_for_capacity,
)


MODEL_NAME = "deterministic-children-stack-fixture"
REQUIREMENTS = (
    "Delegate left.txt and right.txt to two separate children within this worker. "
    "Each child must change its assigned file to '<name> implemented' followed by a newline. "
    "Compose both returned patches, run the combined repository tests, and request independent review."
)
ROOT_RULES = (
    "The root delegates one file to each child and composes their returned patches. "
    "Each child changes only its assigned file. Preserve AGENTS.md and the tests. "
    "The root runs the combined tests before requesting review.\n"
)
BASE_TOOLS = {"read_file", "write_file", "list_files", "run_command", "finish"}
CHILD_TOOLS = {"spawn_child", "child_status", "child_result", "cancel_child", "close_children"}
SECRET_NAMES = (
    "ZPM_TRIAL_PROVIDER_TOKEN", "ZPM_MODEL_PROVIDER_TOKEN", "POLYPHONY_RUNTIME_TOKEN",
    "OPENJULES_WORKER_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY",
)
BOUNDARY = (
    "assert not pathlib.Path('/source/.env').exists()\n"
    "assert not pathlib.Path('/source/.git').exists()\n"
    "assert not pathlib.Path('/var/run/docker.sock').exists()\n"
    "assert not os.access('/workspace/children',os.W_OK)\n"
    "assert not os.access('/run/openjules-model',os.W_OK)\n"
    "assert not os.access('/control/deadline',os.W_OK)\n"
    "assert not any(name in os.environ for name in " + repr(SECRET_NAMES) + ")\n"
)
DENY_SOCKET = (
    "def deny_socket(path):\n"
    " with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:\n"
    "  connection.settimeout(5);connection.connect(path)\n"
    "  header=b''\n"
    "  while len(header)<4:\n"
    "   part=connection.recv(4-len(header));assert part;header+=part\n"
    "  length=struct.unpack('!I',header)[0];assert 0<length<=65536;data=b''\n"
    "  while len(data)<length:\n"
    "   part=connection.recv(length-len(data));assert part;data+=part\n"
    "  assert json.loads(data)['error']=='not_authorized'\n"
)
CHILD_VERIFY = [
    "python3", "-I", "-c",
    "import json,os,pathlib,socket,struct\n"
    "assert os.getuid() in (60000,60001)\n"
    "assert not os.access('/workspace/run',os.R_OK)\n"
    + BOUNDARY + DENY_SOCKET
    + "deny_socket('/run/openjules-model/model.sock')\n"
    "deny_socket('/run/openjules-model/control.sock')\n"
    "name='left' if os.getuid()==60000 else 'right'\n"
    "other='right' if name=='left' else 'left'\n"
    "assert pathlib.Path(name+'.txt').read_text()==name+' implemented\\n'\n"
    "assert pathlib.Path(other+'.txt').read_text()=='old\\n'\n"
    "print('CHILD_STACK_BOUNDARY_VERIFIED uid='+str(os.getuid()))\n",
]
VERIFY = [
    "python3", "-I", "-c",
    "import os,pathlib,subprocess\nassert os.getuid()==65532\n" + BOUNDARY
    + "subprocess.run(['python3','-m','unittest','discover','-v'],check=True)\n"
    "print('CHILD_STACK_ROOT_VERIFIED')\n",
]


def text_hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


class ChildrenFixture(ModelFixture):
    """Ten real inference replies, with an observation gate after both grants."""

    def __init__(self):
        super().__init__()
        self.root_turns = 0
        self.child_turns = {"left": 0, "right": 0}
        self.child_ids = {}
        self.patches = {}
        self.two_spawned = threading.Event()
        self.release_root = threading.Event()

    def __exit__(self, *args):
        self.release_root.set()
        return super().__exit__(*args)

    def reply(self, payload):
        require(isinstance(payload, dict) and payload.get("model") == MODEL_NAME, "fixture model mismatch")
        require(payload.get("max_tokens") == 1024 and payload.get("stream") is False,
                "fixture output policy mismatch")
        require(payload.get("tool_choice") == "auto", "fixture tool policy mismatch")
        messages = payload.get("messages", [])
        require(len(messages) >= 2 and messages[1].get("role") == "user", "fixture task context missing")
        initial = messages[1].get("content", "")
        require(ROOT_RULES.strip() in initial, "repository instructions did not reach the agent")
        child_name = next((name for name in self.child_turns if initial.startswith("CHILD_" + name + ":")), None)
        expected_tools = BASE_TOOLS if child_name else BASE_TOOLS | CHILD_TOOLS
        require({item["function"]["name"] for item in payload.get("tools", [])} == expected_tools,
                "fixture tool schema mismatch or child received root controls")
        if child_name is None:
            require(REQUIREMENTS in initial, "canonical root requirements did not reach the agent")
        results = [item for item in messages if item.get("role") == "tool"]
        pause = False
        with self.lock:
            identity = child_name or "root"
            previous = self.child_turns[child_name] if child_name else self.root_turns
            require(len(results) == previous, "agent did not execute each preceding tool exactly once")
            if previous:
                require(results[-1].get("tool_call_id") == f"{identity}-{previous}", "tool result identity changed")
                require(not results[-1].get("content", "").startswith("Tool error:"), "preceding agent tool failed")
            turn = previous + 1
            if child_name:
                require(turn <= 2, "unexpected extra child model call")
                self.child_turns[child_name] = turn
                tool, arguments = (
                    ("write_file", {"path": child_name + ".txt", "content": child_name + " implemented\n"})
                    if turn == 1 else ("finish", {"summary": "Completed " + child_name + "; request root review"})
                )
            else:
                require(turn <= 6, "unexpected extra root model call")
                self.root_turns = turn
                if turn in {2, 3}:
                    child_id = json.loads(results[-1]["content"])["child_id"]
                    require(re.fullmatch(r"child_[0-9a-f]{32}", child_id) is not None, "invalid canonical child identity")
                    require(child_id not in self.child_ids.values(), "spawn reused a child identity")
                    self.child_ids["left" if turn == 2 else "right"] = child_id
                if turn in {4, 5}:
                    name = "left" if turn == 4 else "right"
                    page = json.loads(results[-1]["content"])
                    content = page.get("content", "")
                    require(page.get("child_id") == self.child_ids[name] and page.get("kind") == "patch"
                            and page.get("final") is False and page.get("offset") == 0
                            and page.get("total_bytes") == len(content.encode())
                            and page.get("content_sha256") == page.get("artifact_sha256") == text_hash(content),
                            "child result was not a complete hashed provisional patch page")
                    require(f"diff --git a/{name}.txt b/{name}.txt" in content
                            and content.count("diff --git ") == 1 and "+" + name + " implemented" in content,
                            "child provisional patch does not match its assigned file")
                    self.patches[name] = content
                if turn <= 2:
                    name = "left" if turn == 1 else "right"
                    tool, arguments = "spawn_child", {
                        "instructions": "CHILD_" + name + ": change only " + name + ".txt",
                        "seconds": 90, "max_turns": 2, "capabilities": ["python"],
                    }
                elif turn in {3, 4}:
                    name = "left" if turn == 3 else "right"
                    tool, arguments = "child_result", {"child_id": self.child_ids[name], "kind": "patch"}
                    pause = turn == 3
                elif turn == 5:
                    script = "import json,os,pathlib,socket,struct,subprocess\n" + DENY_SOCKET
                    script += "subprocess.run(['git','apply','-'],input=" + repr(self.patches["left"] + self.patches["right"]) + ",text=True,check=True)\n"
                    script += "assert os.getuid()==65532 and not os.access('/workspace/children',os.W_OK)\n"
                    script += "for child in " + repr(list(self.child_ids.values())) + ":\n"
                    script += " assert not os.access('/workspace/children/'+child+'/run',os.R_OK)\n"
                    script += " deny_socket('/run/openjules-model/children/'+child+'/model.sock')\n"
                    script += "subprocess.run(['python3','-m','unittest','discover','-v'],check=True)\n"
                    script += "print('ROOT_COMPOSED_BOTH_CHILD_PATCHES')\n"
                    tool, arguments = "run_command", {"argv": ["python3", "-I", "-c", script]}
                else:
                    verification = json.loads(results[-1]["content"])
                    require(verification.get("state") == "exited" and verification.get("exit_code") == 0
                            and "ROOT_COMPOSED_BOTH_CHILD_PATCHES" in verification.get("output", ""),
                            "root did not compose and verify both returned child patches")
                    tool, arguments = "finish", {"summary": "Composed both child patches and passed combined tests; request independent review"}
            self.requests.append(payload)
        if pause:
            self.two_spawned.set()
            require(self.release_root.wait(20), "harness did not release the bounded child observation gate")
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"{identity}-{turn}", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }]}


def read_artifacts(authority, scope, mapping, sensitive, *, attempt_id=None, child_id=None):
    require(set(mapping) == {"patch", "log", "manifest"}, "terminal capture is incomplete")
    artifacts = {}
    for kind, digest in mapping.items():
        path = "/managed/artifacts/" + digest
        query = scope
        if child_id:
            path = "/managed/children/" + child_id + "/artifacts/" + digest
            query = scope | {"attempt_id": attempt_id}
        content = authority.request("GET", path, query=query, text=True)
        require(text_hash(content) == digest, "canonical artifact hash mismatch")
        require(all(token not in content for token in sensitive), "captured artifact contains a host credential")
        artifacts[kind] = content
    return artifacts


def inference_reservations(runtime_config, worker, attempt):
    """Read only reservation metadata, never stored model inputs or responses."""
    database = Path(runtime_config["state_directory"]) / "models/inference.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT request_id,reserved_tokens,status FROM calls WHERE worker_id=? AND attempt_id=?",
            (worker, attempt),
        ).fetchall()
    require(len(rows) == 10 and all(tokens == 1024 and status == "complete" for _, tokens, status in rows),
            "root and children did not share exactly ten completed inference reservations")
    require(sum(request.startswith("/child/") for request, _, _ in rows) == 4,
            "child inference was not charged to the root allowance")
    return {"calls": len(rows), "child_calls": 4, "reserved_output_tokens": sum(row[1] for row in rows)}


def run(args):
    zpm = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(zpm))
    from zpm.controller import Controller
    from zpm.deployment import MODEL_TOKEN_ENV, prepare, verify
    from zpm.models import released, snapshot as worker_snapshot
    from zpm.store import Store

    root = args.work_dir.absolute()
    require(not root.exists(), "trial work directory must be new")
    root.mkdir(mode=0o700)
    processes = Processes(root)
    checks, revisions, sensitive = [], {}, set()
    with ChildrenFixture() as model:
        sensitive.add(model.token)
        try:
            image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
                                            text=True, timeout=30).strip()
            require(image == args.image and image.startswith("sha256:") and len(image) == 71,
                    "trial requires an already-present immutable managed-agent image ID")
            source = root / "source"
            source.mkdir(mode=0o755)
            for name in ("left", "right"):
                (source / (name + ".txt")).write_text("old\n")
            (source / "test_combined.py").write_text(
                "from pathlib import Path\nimport unittest\n\nclass CombinedTests(unittest.TestCase):\n"
                "    def test_both_children(self):\n        for name in ('left','right'):\n"
                "            self.assertEqual(Path(name+'.txt').read_text(),name+' implemented\\n')\n")
            (source / "AGENTS.md").write_text(ROOT_RULES)
            (source / ".gitignore").write_text(".env\n__pycache__/\n")
            for command in (["init", "-q"], ["add", "."], ["-c", "user.name=Children Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled base"]):
                subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True, timeout=15)
            (source / ".env").write_text("TRIAL_HOST_ONLY=must-not-enter-the-worker\n")
            base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
            paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
            revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"],
                                                      text=True, timeout=15).strip() for name, path in paths.items()}
            scope = {"deployment_id": "children-trial-" + secrets.token_hex(8),
                     "repository": "github:github.com/managed-children-trial/repository"}
            profile = "approved-children-agent-v1"
            authority_port, runtime_port = port(), port()
            while authority_port == runtime_port:
                runtime_port = port()
            bundle = root / "installation"
            prepare({
                "schema_version": 1, "deployment_id": scope["deployment_id"],
                "binding": {"schema_version": 1, "repository": scope["repository"], "profile": profile, "desired_workers": 0},
                "max_workers": 1, "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in paths.items()},
                "source": {"checkout": str(source), "revision": base}, "image": image,
                "command": ["python3", "-I", "-S", "/opt/openjules/managed_root_runner.py",
                            "--socket", "/run/openjules-model/model.sock", "--control-socket", "/run/openjules-model/control.sock",
                            "--max-turns", "6", "--verify-json", json.dumps(VERIFY)],
                "ports": {"polyphony": authority_port, "openjules": runtime_port},
                "runtime": {"max_seconds": 240},
                "policy": {"checks": ["repository-tests"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 360, "attempt_seconds": 180, "capabilities": ["python"]},
                "model_policy": {"endpoint": model.endpoint, "model": MODEL_NAME, "token_ref": "env:" + TOKEN_SOURCE,
                                 "max_calls": 10, "max_output_tokens": 1024, "max_total_output_tokens": 10240,
                                 "request_timeout_seconds": 30},
                "child_policy": {"max_children": 2, "max_child_seconds": 90, "max_child_turns": 2,
                                 "capabilities": ["python"], "uid_start": 60000, "verify_argv": CHILD_VERIFY},
            }, bundle)
            verify(bundle)
            environment_tokens = json.loads((bundle / "secrets/environment.json").read_text())
            operator_tokens = json.loads((bundle / "secrets/operator.json").read_text())
            sensitive.update(environment_tokens.values())
            sensitive.update(operator_tokens.values())
            runtime_config = json.loads((bundle / "config/openjules.json").read_text())
            canonical_config = json.loads((bundle / "config/polyphony.json").read_text())
            child_policy = runtime_config["children"][0]["policy"]
            canonical_profile = canonical_config["repositories"][scope["repository"]]["profiles"][profile]
            require(all(child_policy[key] == canonical_profile[key] for key in ("max_children", "max_child_seconds", "max_child_turns"))
                    and child_policy["capabilities"] == canonical_profile["child_capabilities"],
                    "installer produced mismatched runtime and canonical child limits")
            require(runtime_config["models"][0]["policy"]["token_ref"] == "env:" + MODEL_TOKEN_ENV
                    and runtime_config["profiles"][0]["environment_refs"] == {},
                    "installer did not isolate the model credential in its trusted service alias")
            state = bundle / "state/zpm.sqlite3"
            authority_endpoint = f"http://127.0.0.1:{authority_port}"
            authority = Client(authority_endpoint, environment_tokens["POLYPHONY_RUNTIME_TOKEN"])
            ingress = Client(authority_endpoint, operator_tokens["ingress"])
            runtime = Client(f"http://127.0.0.1:{runtime_port}", environment_tokens["OPENJULES_WORKER_TOKEN"])
            manager = [sys.executable, str(bundle / "manage.py")]
            manager_environment = clean_environment({TOKEN_SOURCE: model.token})

            def plan():
                with Store(state, scope["deployment_id"]) as store:
                    return Controller(store).plan()

            def target(count, **kwargs):
                with Store(state, scope["deployment_id"]) as store:
                    store.change(scope["repository"], "target", count)
                return wait_for_capacity(plan, processes, count, **kwargs)

            def snapshot():
                return authority.request("GET", "/managed/snapshot", query=scope)

            def children(attempt_id):
                return authority.request("GET", "/managed/children", query=scope | {"attempt_id": attempt_id})

            def installed_ready():
                result = subprocess.run(manager + ["doctor"], cwd=bundle, env=clean_environment(),
                                        capture_output=True, text=True, timeout=50)
                require(result.returncode in {0, 3}, "generated doctor could not inspect the child installation")
                return json.loads(result.stdout).get("ready") is True

            processes.run_once("bootstrap", manager + ["bootstrap"], bundle, clean_environment(), 900)
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "generated child installation readiness", 60)
            require(plan()["reserved"] == 0 and not model.requests, "readiness performed unrequested inference")
            target(1)
            require(not model.requests, "idle worker performed unrequested inference")
            checks.append("generated installation starts three real services with aligned child policies and no idle inference")

            request = {"schema_version": 1, **scope, "source": "github:issue:controlled-children-trial", "request_key": "run-1",
                       "title": "Controlled two-child coding", "requirements": REQUIREMENTS}
            task = ingress.request("POST", "/managed/enroll", data=request)["task"]["id"]
            require(ingress.request("POST", "/managed/enroll", data=request)["task"]["id"] == task,
                    "source replay created another canonical task")
            ingress.request("POST", "/managed/enroll", data=request | {"repository": "github:github.com/foreign/repository"}, expected=403)

            def spawned():
                require(not model.errors, "deterministic model fixture rejected an agent request")
                return model.two_spawned.is_set()

            eventually(spawned, processes, "both real child grants", 60)
            try:
                active = snapshot()
                require(len(active["attempt"]) == 1 and len(active["task"]) == 1, "child grant created another root task or attempt")
                active_attempt = active["attempt"][0]
                worker = active_attempt["runtime_binding"]["worker_id"]
                active_children = children(active_attempt["id"])["children"]
                require(len(active_children) == 2 and {item["child"]["id"] for item in active_children} == set(model.child_ids.values()),
                        "real canonical child grants do not match the root's spawn replies")
                require(all(item["launch_allowed"] and not item["child"]["termination_confirmed"] for item in active_children),
                        "child grants were not live during the accounting observation")
                observed = runtime.request("GET", "/api/workers/" + worker, query=scope)
                require(set(observed["active_executions"]) == {active_attempt["id"], *model.child_ids.values()},
                        "OpenJules did not retain the root and both children under one worker")
                capacity = plan()
                row = capacity["repositories"][0]
                require(capacity["reserved"] == row["reserved"] == row["desired_workers"] == row["effective_target"] == 1
                        and [item["worker_id"] for item in row["workers"]] == [worker],
                        "two children changed ZPM's one-worker target or reservation")
                require(active["task"][0]["allocated_seconds"] == 360, "child time was not charged to the root task budget")
                # Request a graceful scale-down without waiting for release:
                # the provider response stays paused while all three execution
                # references must continue to retain the worker reservation.
                with Store(state, scope["deployment_id"]) as store:
                    store.change(scope["repository"], "target", 0)

                def draining():
                    require(not model.errors, "fixture failed during the active-child drain observation")
                    capacity = plan()
                    row = capacity["repositories"][0]
                    require(row["desired_workers"] == row["effective_target"] == 0
                            and capacity["reserved"] == row["reserved"] == 1
                            and [item["worker_id"] for item in row["workers"]] == [worker],
                            "scale to zero released or replaced an active root/child worker")
                    observed = runtime.request("GET", "/api/workers/" + worker, query=scope)
                    require(set(observed["active_executions"]) == {active_attempt["id"], *model.child_ids.values()},
                            "scale to zero dropped an active root or child execution reference")
                    current_children = children(active_attempt["id"])
                    require({item["child"]["id"] for item in current_children["children"]} == set(model.child_ids.values())
                            and all(item["launch_allowed"] and not item["child"]["termination_confirmed"]
                                    for item in current_children["children"]),
                            "graceful drain revoked or lost an existing child grant")
                    root_permission = authority.request("POST", "/managed/permission", data={
                        "schema_version": 1, **scope, "attempt_id": active_attempt["id"],
                    })
                    require(root_permission["launch_allowed"] is True, "graceful drain revoked the existing root permission")
                    if (observed["state"] == row["workers"][0]["state"] == "draining"
                            and observed["admission_open"] is row["workers"][0]["admission_open"] is False
                            and current_children["admission_closed"] is True):
                        return {"top_level_target": 0, "reserved_workers": 1, "active_executions": 3,
                                "worker_state": "draining", "worker_admission_open": False,
                                "canonical_child_admission_closed": True, "existing_permissions_allowed": True}
                    return None

                draining_observation = eventually(draining, processes, "active-child scale-to-zero admission closure", 12)
            finally:
                model.release_root.set()
            checks.append("two real canonical child grants and three active executions share exactly one ZPM worker reservation and the root task budget")
            checks.append("target zero drains the active worker and closes canonical child admission while retaining its reservation, all three execution references and existing root/child permissions")

            def captured():
                require(not model.errors, "deterministic model fixture rejected an agent request")
                # Read capacity first. If it already reports release, a later
                # authority read must contain the final fenced root and children.
                capacity = plan()
                report = snapshot()
                attempts = [item for item in report["attempt"] if item["task"] == task]
                candidates = [item for item in report["candidate"] if item["task"] == task]
                if capacity["reserved"] == 0:
                    require(len(attempts) == 1 and attempts[0].get("termination_confirmed") is True
                            and attempts[0].get("launch_fenced") is True
                            and set(attempts[0].get("captured_artifacts", {})) == {"patch", "log", "manifest"},
                            "ZPM released capacity before the canonical root terminal receipt")
                    terminal_children = children(attempts[0]["id"])["children"]
                    require(len(terminal_children) == 2 and all(
                        item["child"].get("termination_confirmed") is True and item["child"].get("launch_fenced") is True
                        and set(item["child"].get("captured_artifacts", {})) == {"patch", "log", "manifest"}
                        for item in terminal_children), "ZPM released capacity before both canonical child terminal receipts")
                return (report, attempts[0], candidates[0]) if len(attempts) == len(candidates) == 1 and attempts[0].get("termination_confirmed") else None

            evidence, attempt, candidate = eventually(captured, processes, "root composition and canonical child capture", 100)
            require(len(evidence["task"]) == len(evidence["attempt"]) == 1 and len(model.requests) == 10
                    and model.root_turns == 6 and model.child_turns == {"left": 2, "right": 2},
                    "execution duplicated tasks, grants or model calls")
            require(attempt.get("launch_fenced") is True, "root capture lacks its final launch fence")
            artifacts = read_artifacts(authority, scope, attempt.get("captured_artifacts", {}), sensitive)
            root_manifest = json.loads(artifacts["manifest"])
            require(root_manifest.get("worker_id") == worker and root_manifest.get("attempt_id") == attempt["id"]
                    and root_manifest.get("base_commit") == base and root_manifest.get("termination_confirmed") is True
                    and root_manifest.get("launch_fenced") is True, "root manifest is not bound to the terminated approved execution")
            require("ROOT_COMPOSED_BOTH_CHILD_PATCHES" in artifacts["log"] and "CHILD_STACK_ROOT_VERIFIED" in artifacts["log"]
                    and '"event": "configured_verification"' in artifacts["log"], "root capture lacks combined test and isolation verification")
            canonical_children = children(attempt["id"])
            require(canonical_children["admission_closed"] is True and len(canonical_children["children"]) == 2,
                    "root completion did not close child admission or retain both grants")
            child_evidence, lineage, uids, child_policy_digests = [], [], set(), set()
            for permission in canonical_children["children"]:
                child = permission["child"]
                binding = child["binding"]
                name = next(name for name, identity in model.child_ids.items() if identity == child["id"])
                require(child.get("termination_confirmed") is True and child.get("launch_fenced") is True
                        and child.get("state") == "exited" and permission["launch_allowed"] is False
                        and child["turns_allocated"] == 2, "canonical child is not fenced, captured and charged for its two turns")
                require(binding["root_attempt_id"] == binding["parent_attempt_id"] == attempt["id"]
                        and binding["worker_id"] == worker and binding["task_id"] == task and binding["base"] == base
                        and binding["allocated_seconds"] == 90 and binding["max_turns"] == 2,
                        "canonical child binding changed its approved root, base or allowance")
                captured_child = read_artifacts(authority, scope, child.get("captured_artifacts", {}), sensitive,
                                                attempt_id=attempt["id"], child_id=child["id"])
                manifest = json.loads(captured_child["manifest"])
                uid = 60000 if name == "left" else 60001
                require(manifest.get("child_id") == child["id"] and manifest.get("root_attempt_id") == attempt["id"]
                        and manifest.get("worker_id") == worker and manifest.get("grant_digest") == binding["grant_digest"]
                        and manifest.get("uid") == uid and manifest.get("exec_id") and manifest.get("exec_start_dispatched") is True
                        and manifest.get("not_started") is False and manifest.get("workspace_present") is True,
                        "child manifest lacks its actual UID, dispatched exec or canonical lineage")
                require(manifest.get("archive_sha256") == root_manifest["archive_sha256"]
                        and manifest.get("root_manifest_sha256") == attempt["captured_artifacts"]["manifest"]
                        and manifest.get("termination_method") == "whole-container fence"
                        and manifest.get("termination_confirmed") is True and manifest.get("launch_fenced") is True
                        and manifest.get("patch_present") is True and manifest.get("log_present") is True,
                        "final child capture is not bound to the root's fenced archive")
                require(type(manifest.get("armed_deadline_monotonic")) in {int, float}
                        and 0 < manifest["armed_deadline_monotonic"] <= manifest["deadline_monotonic"],
                        "child manifest lacks its monotonically bounded armed deadline")
                require(captured_child["patch"] == model.patches[name]
                        and "CHILD_STACK_BOUNDARY_VERIFIED uid=" + str(uid) in captured_child["log"]
                        and '"event": "configured_verification"' in captured_child["log"],
                        "final child artifact differs from its composed result or lacks UID and boundary verification")
                uids.add(uid)
                child_policy_digests.add(manifest["child_policy"])
                lineage.append({"child_id": child["id"], "grant_digest": binding["grant_digest"], "artifacts": child["captured_artifacts"]})
                child_evidence.append({"child_id": child["id"], "uid": uid, "turns": child["turns_allocated"],
                                       "grant_digest": binding["grant_digest"], "artifacts": child["captured_artifacts"]})
            lineage.sort(key=lambda item: item["child_id"])
            require(uids == {60000, 60001} and attempt.get("child_lineage") == candidate.get("child_lineage") == lineage,
                    "root candidate does not bind both final child capture lineages")
            require(candidate["patch"] == attempt["captured_artifacts"]["patch"] and candidate["base"] == base
                    and evidence["task"][0]["status"] == "awaiting_review" and not evidence.get("admission"),
                    "root candidate changed its capture or self-authorized acceptance")
            allowance = inference_reservations(runtime_config, worker, attempt["id"])
            checks.append("six root and four child model calls share ten durable reservations; both final child manifests bind distinct UIDs, canonical grants and the fenced root archive")
            checks.append("children cannot read root work or use root model/control sockets; root cannot read child work or use child model sockets; captured candidate binds both final child lineages and awaits review")

            verifier = root / "artifact-verification"
            subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(verifier)], check=True, timeout=15)
            subprocess.run(["git", "-C", str(verifier), "apply", "--check", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
            subprocess.run(["git", "-C", str(verifier), "apply", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
            changed = subprocess.check_output(["git", "-C", str(verifier), "diff", "--name-only"], text=True, timeout=10).splitlines()
            require(changed == ["left.txt", "right.txt"], "root candidate changed files outside the two assigned contributions")
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=verifier, check=True, timeout=10)
            require(all((source / (name + ".txt")).read_text() == "old\n" for name in ("left", "right")),
                    "agent modified the approved source checkout")
            checks.append("independent checkout reapplies the canonical root patch to the exact approved base, preserves tests and instructions, and passes both contribution assertions")

            wait_for_capacity(plan, processes, 0, retired_worker_ids={worker})
            require(plan()["repositories"][0]["desired_workers"] == 0,
                    "active-child scale-down changed the requested zero target")
            require(released(worker_snapshot(runtime.request("GET", "/api/workers/" + worker, query=scope))),
                    "child worker lacks durable retirement evidence")
            checks.append("the drained root composes and captures normally; target zero releases the worker after all final canonical root/child receipts")
            target(1, retired_worker_ids={worker})
            container_ids = subprocess.check_output(["docker", "ps", "-aq", "--filter", "label=openjules.worker=" + worker],
                                                    text=True, timeout=10).split()
            require(len(container_ids) == 1, "root and children do not have exactly one worker Docker incarnation")
            inspected = json.loads(subprocess.check_output(["docker", "inspect", container_ids[0]], text=True, timeout=10))[0]
            require(inspected["Id"] == root_manifest["container_id"] and not inspected["State"]["Running"]
                    and inspected["HostConfig"]["NetworkMode"] == "none" and inspected["HostConfig"]["ReadonlyRootfs"] is True
                    and inspected["HostConfig"]["CapDrop"] == ["ALL"], "child worker violated its Docker isolation or fence")
            model_mounts = [item for item in inspected["Mounts"] if item["Destination"] == "/run/openjules-model"]
            require(len(model_mounts) == 1 and model_mounts[0]["RW"] is False
                    and model_mounts[0]["Source"] == str(Path(runtime_config["state_directory"]) / "models" / worker),
                    "model/control socket mount is not the exact worker-owned read-only directory")
            require(all(item["Destination"] != "/var/run/docker.sock" for item in inspected["Mounts"])
                    and all(token not in json.dumps(inspected) for token in sensitive), "worker Docker metadata exposes host control or credentials")
            require(child_policy_digests == {inspected["Config"]["Labels"]["openjules.child_policy"]},
                    "child captures do not identify the policy installed in their shared container")
            checks.append("completed root and children retire together; Docker confirms one stopped incarnation, disabled network, dropped capabilities, read-only root and no host credential or Docker socket")

            target(0)
            processes.stop("installation")
            verify(bundle)
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "child installation restart", 60)
            durable = snapshot()
            durable_children = children(attempt["id"])
            require(len(durable["attempt"]) == len(durable["candidate"]) == 1
                    and durable["candidate"][0] == candidate
                    and durable_children["admission_closed"] is True
                    and {row["child"]["id"]: row["child"] for row in durable_children["children"]}
                    == {row["child"]["id"]: row["child"] for row in canonical_children["children"]},
                    "restart lost, changed or duplicated canonical root/child evidence")
            require(read_artifacts(authority, scope, attempt["captured_artifacts"], sensitive) == artifacts,
                    "restart changed the canonical root artifact contents")
            for child in child_evidence:
                read_artifacts(authority, scope, child["artifacts"], sensitive,
                               attempt_id=attempt["id"], child_id=child["child_id"])
            require(plan()["reserved"] == 0 and len(model.requests) == 10 and not model.errors
                    and inference_reservations(runtime_config, worker, attempt["id"]) == allowance,
                    "restart allocated work or repeated root/child inference")
            checks.append("target zero releases all reservations; full installation restart preserves root and child captures without new model calls")

            report = {"passed": True, "schema_version": 1, "deployment_id": scope["deployment_id"], "image": image,
                      "component_revisions": revisions, "installation_bootstrap_and_doctor": "passed", "checks": checks,
                      "canonical_task": task, "root_attempt": attempt["id"], "candidate": candidate["id"],
                      "captured_artifacts": attempt["captured_artifacts"], "children": child_evidence,
                      "active_observation": {"top_level_target": 1, "reserved_workers": 1, "active_executions": 3, "children": 2},
                      "active_scale_to_zero_observation": draining_observation,
                      "inference_allowance": allowance, "model_fixture_calls": len(model.requests), "final_reserved": plan()["reserved"],
                      "proof": "real generated installation, canonical grants, Docker root/children, private brokers and durable capture; deterministic localhost model replies",
                      "not_proven": ["live provider compatibility", "model reasoning quality", "financial spend bound",
                                     "forced cancellation during active children", "independent semantic review", "forge publication", "merge authorization"]}
            require(all(token not in json.dumps(report) for token in sensitive), "verification report contains a credential")
            private_json(root / "report.json", report)
            return report
        except Exception as exc:
            diagnostic = diagnostics(root, str(exc) if isinstance(exc, AssertionError) else type(exc).__name__, checks, revisions, sensitive)
            print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
            raise
        finally:
            model.release_root.set()
            processes.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument(f"--{name}-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-present full sha256 ID from the managed-agent Dockerfile")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private directory, retained for recovery")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, AssertionError) else "child trial failed; inspect retained private service logs"
        print(json.dumps({"passed": False, "error": message, "work_dir": str(args.work_dir.absolute())}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
