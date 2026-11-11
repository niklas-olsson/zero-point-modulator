"""Install and exercise the model-backed root agent with a localhost fixture.

The model endpoint, replies, repository and credential are created by this
trial. No live provider is contacted. The retained work directory contains
private installation state; export only report.json or redacted diagnostics.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading

from check_authority_admission import docker_inventory, qualify_unmanaged_authority

from check_stack import (
    Client, Processes, clean_environment, diagnostics, eventually, port,
    private_json, require, wait_for_capacity,
)


MODEL_NAME = "deterministic-model-stack-fixture"
TOKEN_SOURCE = "ZPM_TRIAL_PROVIDER_TOKEN"
REQUIREMENTS = "Change answer() to return 42, run the repository tests, and request independent review."
ROOT_RULES = "Change only answer.py. Read it first, run the existing tests, and preserve this instruction file.\n"
ANSWER = "def answer():\n    return 42\n"
VERIFY = [
    "python3", "-I", "-c",
    "import os,pathlib,runpy,subprocess; "
    "assert os.getuid()==65532; "
    "assert runpy.run_path('answer.py')['answer']()==42; "
    "assert not pathlib.Path('/source/.env').exists(); "
    "assert not pathlib.Path('/source/.git').exists(); "
    "assert not pathlib.Path('/var/run/docker.sock').exists(); "
    "assert pathlib.Path('/run/openjules-model/model.sock').is_socket(); "
    "assert not os.access('/run/openjules-model',os.W_OK); "
    "assert not os.access('/control/deadline',os.W_OK); "
    "assert not any(name in os.environ for name in "
    "['ZPM_TRIAL_PROVIDER_TOKEN','ZPM_MODEL_PROVIDER_TOKEN',"
    "'POLYPHONY_RUNTIME_TOKEN','OPENJULES_WORKER_TOKEN',"
    "'OPENAI_API_KEY','ANTHROPIC_API_KEY','OPENROUTER_API_KEY',"
    "'GEMINI_API_KEY','GOOGLE_API_KEY','AZURE_OPENAI_API_KEY']); "
    "subprocess.run(['python3','-m','unittest','discover','-v'],check=True); "
    "print('MODEL_STACK_BOUNDARY_VERIFIED')",
]


class ModelFixture(AbstractContextManager):
    """A bounded deterministic HTTP fixture, independent of OpenJules code."""

    def __init__(self):
        self.token = "fixture-" + secrets.token_urlsafe(32)
        self.requests = []
        self.errors = []
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    self.connection.settimeout(5)
                    require(self.path == "/v1/chat/completions", "fixture endpoint mismatch")
                    require(self.headers.get("Authorization") == "Bearer " + fixture.token,
                            "fixture provider credential mismatch")
                    length = int(self.headers.get("Content-Length", "0"))
                    require(0 < length <= 524288, "fixture request size exceeded")
                    payload = json.loads(self.rfile.read(length))
                    reply = fixture.reply(payload)
                    content = json.dumps({"choices": [{"message": reply}]}).encode()
                    self.send_response(200)
                except Exception as exc:
                    with fixture.lock:
                        fixture.errors.append(str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
                    content = b'{"error":"deterministic fixture rejected the request"}'
                    self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except OSError:
                    pass

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def reply(self, payload):
        require(isinstance(payload, dict) and payload.get("model") == MODEL_NAME, "fixture model mismatch")
        require(payload.get("max_tokens") == 1024 and payload.get("stream") is False,
                "fixture output policy mismatch")
        require(payload.get("tool_choice") == "auto", "fixture tool policy mismatch")
        tools = payload.get("tools", [])
        require({item["function"]["name"] for item in tools} == {
            "read_file", "write_file", "list_files", "run_command", "finish",
        }, "fixture tool schema mismatch")
        messages = payload.get("messages", [])
        user_text = "\n".join(item.get("content", "") for item in messages if item.get("role") == "user")
        require(REQUIREMENTS in user_text and ROOT_RULES.strip() in user_text,
                "canonical requirements or root repository instructions did not reach the agent")
        sequence = [
            ("read_file", {"path": "answer.py"}),
            ("write_file", {"path": "answer.py", "content": ANSWER}),
            ("run_command", {"argv": VERIFY}),
            ("finish", {"summary": "Changed answer.py, ran verification and requested independent review"}),
        ]
        with self.lock:
            index = len(self.requests)
            require(index < len(sequence), "unexpected extra model inference")
            results = [item for item in messages if item.get("role") == "tool"]
            require(len(results) == index, "agent did not execute each previous fixture tool exactly once")
            if index:
                require(results[-1].get("tool_call_id") == f"fixture-{index}", "agent tool result identity changed")
                require(not results[-1].get("content", "").startswith("Tool error:"), "agent tool operation failed")
            if index == 1:
                require("return 0" in results[-1]["content"], "agent did not read the original source")
            if index == 3:
                verification = json.loads(results[-1]["content"])
                require(verification.get("state") == "exited" and verification.get("exit_code") == 0,
                        "agent did not pass its tool verification")
                require("MODEL_STACK_BOUNDARY_VERIFIED" in verification.get("output", ""),
                        "agent verification did not inspect the execution boundary")
            self.requests.append(payload)
        tool, arguments = sequence[index]
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"fixture-{index + 1}", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }]}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


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
    with ModelFixture() as model:
        sensitive.add(model.token)
        try:
            image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
                                            text=True, timeout=30).strip()
            require(image == args.image and image.startswith("sha256:") and len(image) == 71,
                    "trial requires an already-present immutable managed-agent image ID")
            source = root / "source"
            source.mkdir(mode=0o755)
            (source / "answer.py").write_text("def answer():\n    return 0\n")
            (source / "test_answer.py").write_text(
                "import unittest\nfrom answer import answer\n\nclass AnswerTests(unittest.TestCase):\n"
                "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n")
            (source / "AGENTS.md").write_text(ROOT_RULES)
            (source / ".gitignore").write_text(".env\n__pycache__/\n")
            for command in (["init", "-q"], ["add", "."], ["-c", "user.name=Model Trial", "-c", "user.email=trial@example.invalid", "commit", "-qm", "controlled base"]):
                subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True, timeout=15)
            (source / ".env").write_text("TRIAL_HOST_ONLY=must-not-enter-the-worker\n")
            base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=15).strip()
            paths = {"zpm": zpm, "openjules": args.openjules_checkout, "polyphony": args.polyphony_checkout}
            revisions = {name: subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"],
                                                      text=True, timeout=15).strip() for name, path in paths.items()}
            scope = {"deployment_id": "model-trial-" + secrets.token_hex(8),
                     "repository": "github:github.com/managed-model-trial/repository"}
            profile = "approved-model-agent-v1"
            authority_port, runtime_port = port(), port()
            while authority_port == runtime_port:
                runtime_port = port()
            bundle = root / "installation"
            prepare({
                "schema_version": 1, "deployment_id": scope["deployment_id"],
                "binding": {"schema_version": 1, "repository": scope["repository"], "profile": profile, "desired_workers": 0},
                "max_workers": 1, "sources": {name: {"checkout": str(path), "revision": revisions[name]} for name, path in paths.items()},
                "source": {"checkout": str(source), "revision": base}, "image": image,
                "command": ["python3", "-I", "-S", "/opt/openjules/managed_runner.py", "--socket", "/run/openjules-model/model.sock",
                            "--max-turns", "4", "--verify-json", json.dumps(VERIFY)],
                "ports": {"polyphony": authority_port, "openjules": runtime_port},
                "runtime": {"max_seconds": 90},
                "policy": {"checks": ["repository-tests"], "reviewers": ["operator"], "max_attempts": 1,
                           "budget_seconds": 120, "attempt_seconds": 60, "capabilities": ["python"]},
                "model_policy": {"endpoint": model.endpoint, "model": MODEL_NAME, "token_ref": "env:" + TOKEN_SOURCE,
                                 "max_calls": 4, "max_output_tokens": 1024, "max_total_output_tokens": 4096,
                                 "request_timeout_seconds": 5},
            }, bundle)
            verify(bundle)
            environment_tokens = json.loads((bundle / "secrets/environment.json").read_text())
            operator_tokens = json.loads((bundle / "secrets/operator.json").read_text())
            sensitive.update(environment_tokens.values())
            sensitive.update(operator_tokens.values())
            runtime_config = json.loads((bundle / "config/openjules.json").read_text())
            require(runtime_config["models"][0]["policy"]["token_ref"] == "env:" + MODEL_TOKEN_ENV,
                    "installer did not rebind the provider reference to its trusted service alias")
            require(runtime_config["profiles"][0]["environment_refs"] == {}, "installer injected guest model credentials")
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

            def installed_ready():
                result = subprocess.run(manager + ["doctor"], cwd=bundle, env=clean_environment(),
                                        capture_output=True, text=True, timeout=50)
                require(result.returncode in {0, 3}, "generated doctor could not inspect the model installation")
                return json.loads(result.stdout).get("ready") is True

            processes.run_once("bootstrap", manager + ["bootstrap"], bundle, clean_environment(), 900)
            preflight_inventory = docker_inventory()
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "generated model installation readiness", 60)
            require(plan()["reserved"] == 0 and not model.requests, "preparation or readiness performed unrequested inference")
            processes.stop("installation")
            verify(bundle)
            admission = qualify_unmanaged_authority(
                bundle, paths, scope, profile, model, processes, manager_environment, sensitive, preflight_inventory,
            )
            checks.append("a responsive current coordinator without managed policy rejects two completed controller passes, repeated ensure and enrollment without protected work, Docker changes or inference; the original authority then recovers")
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "generated model installation after admission recovery", 60)
            target(1)
            require(not model.requests, "idle worker performed unrequested inference")
            checks.append("generated installation bootstraps and starts three real services; scoped readiness and idle capacity make no model calls")

            request = {"schema_version": 1, **scope, "source": "github:issue:controlled-model-trial", "request_key": "run-1",
                       "title": "Controlled model-backed root coding", "requirements": REQUIREMENTS}
            task = ingress.request("POST", "/managed/enroll", data=request)["task"]["id"]
            require(ingress.request("POST", "/managed/enroll", data=request)["task"]["id"] == task,
                    "source replay created another canonical task")
            ingress.request("POST", "/managed/enroll", data=request | {"repository": "github:github.com/foreign/repository"}, expected=403)

            def captured():
                require(not model.errors, "deterministic model fixture rejected an agent request")
                report = snapshot()
                attempts = [item for item in report["attempt"] if item["task"] == task]
                candidates = [item for item in report["candidate"] if item["task"] == task]
                return (report, attempts[0], candidates[0]) if len(attempts) == len(candidates) == 1 and attempts[0].get("termination_confirmed") else None

            evidence, attempt, candidate = eventually(captured, processes, "model-backed code change and canonical capture", 90)
            require(len(evidence["task"]) == len(evidence["attempt"]) == 1, "model dispatch duplicated canonical execution")
            require(len(model.requests) == 4 and not model.errors, "agent did not complete the exact bounded model sequence")
            require(attempt.get("launch_fenced") is True and set(attempt.get("captured_artifacts", {})) == {"patch", "log", "manifest"},
                    "canonical model capture lacks terminal evidence or required artifacts")
            artifacts = {}
            for kind, digest in attempt["captured_artifacts"].items():
                content = authority.request("GET", "/managed/artifacts/" + digest, query=scope, text=True)
                require(hashlib.sha256(content.encode()).hexdigest() == digest, "model artifact content hash mismatch")
                require(all(token not in content for token in sensitive), "captured artifact contains a host credential")
                artifacts[kind] = content
            require("+    return 42" in artifacts["patch"] and "answer.py" in artifacts["patch"], "model patch lacks the requested change")
            require("MODEL_STACK_BOUNDARY_VERIFIED" in artifacts["log"] and '"event": "configured_verification"' in artifacts["log"],
                    "agent capture lacks configured test and boundary verification")
            require(candidate["patch"] == attempt["captured_artifacts"]["patch"] and candidate["base"] == base,
                    "model candidate is not bound to its exact captured patch and approved base")
            require(evidence["task"][0]["status"] == "awaiting_review" and not evidence.get("admission"),
                    "model execution self-authorized acceptance")
            require((source / "answer.py").read_text() == "def answer():\n    return 0\n", "agent modified the approved source checkout")
            checks.append("canonical ingress launches the real managed agent through its private broker socket; four deterministic model calls produce a hashed review-pending candidate")

            verifier = root / "artifact-verification"
            subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(verifier)], check=True, timeout=15)
            subprocess.run(["git", "-C", str(verifier), "apply", "--check", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
            subprocess.run(["git", "-C", str(verifier), "apply", "-"], input=artifacts["patch"], text=True, check=True, timeout=10)
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=verifier, check=True, timeout=10)
            checks.append("an independent checkout applies the canonical patch to the exact approved base and passes the repository tests")

            worker = attempt["runtime_binding"]["worker_id"]
            target(1, retired_worker_ids={worker})
            require(released(worker_snapshot(runtime.request("GET", "/api/workers/" + worker, query=scope))),
                    "model worker lacks durable retirement evidence")
            container_ids = subprocess.check_output(["docker", "ps", "-aq", "--filter", "label=openjules.worker=" + worker],
                                                    text=True, timeout=10).split()
            require(len(container_ids) == 1, "model worker does not have exactly one Docker incarnation")
            inspected = json.loads(subprocess.check_output(["docker", "inspect", container_ids[0]], text=True, timeout=10))[0]
            require(not inspected["State"]["Running"] and inspected["HostConfig"]["NetworkMode"] == "none"
                    and inspected["HostConfig"]["ReadonlyRootfs"] is True, "model container violated its isolation boundary")
            mounts = inspected["Mounts"]
            model_mounts = [item for item in mounts if item["Destination"] == "/run/openjules-model"]
            require(len(model_mounts) == 1 and model_mounts[0]["RW"] is False
                    and model_mounts[0]["Source"] == str(Path(runtime_config["state_directory"]) / "models" / worker),
                    "model socket mount does not match the exact worker-owned private directory")
            require(all(item["Destination"] != "/var/run/docker.sock" for item in mounts), "worker received Docker control authority")
            require(all(token not in json.dumps(inspected) for token in sensitive), "Docker metadata contains a host credential")
            checks.append("Docker inspection confirms the retired worker had no network, a read-only root, its own read-only model socket directory and no host credentials or Docker socket")

            target(0)
            processes.stop("installation")
            verify(bundle)
            processes.start("installation", manager + ["start"], bundle, manager_environment)
            eventually(installed_ready, processes, "model installation restart", 60)
            durable = snapshot()
            require(len(durable["attempt"]) == 1 and durable["candidate"][0]["id"] == candidate["id"],
                    "model installation restart lost or duplicated canonical evidence")
            require(plan()["reserved"] == 0 and len(model.requests) == 4, "restart allocated work or repeated inference")
            checks.append("capacity reaches zero and a full installation restart retains canonical evidence without replaying inference")

            report = {"passed": True, "schema_version": 1, "deployment_id": scope["deployment_id"], "image": image,
                      "component_revisions": revisions, "installation_bootstrap_and_doctor": "passed", "checks": checks,
                      "canonical_task": task, "candidate": candidate["id"], "captured_artifacts": attempt["captured_artifacts"],
                      "model_fixture_calls": len(model.requests), "final_reserved": plan()["reserved"],
                      "authority_admission": admission,
                      "proof": "real generated installation, canonical authority, Docker agent and broker; deterministic localhost model responses",
                      "not_proven": ["live provider compatibility", "model reasoning quality", "financial spend bound",
                                     "child delegation", "independent semantic review", "forge publication", "merge authorization",
                                     "an installed historical authority release", "a literal legacy HTTP health endpoint"]}
            require(all(token not in json.dumps(report) for token in sensitive), "verification report contains a credential")
            private_json(root / "report.json", report)
            return report
        except Exception as exc:
            diagnostic = diagnostics(root, str(exc) if isinstance(exc, AssertionError) else type(exc).__name__, checks, revisions, sensitive)
            print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
            raise
        finally:
            processes.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("polyphony", "openjules"):
        parser.add_argument(f"--{name}-checkout", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-present full sha256 ID built from the managed-agent Dockerfile")
    parser.add_argument("--work-dir", type=Path, required=True, help="new private directory, retained for recovery")
    args = parser.parse_args()
    args.polyphony_checkout = args.polyphony_checkout.resolve(strict=True)
    args.openjules_checkout = args.openjules_checkout.resolve(strict=True)
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, AssertionError) else "model trial failed; inspect retained private service logs"
        print(json.dumps({"passed": False, "error": message, "work_dir": str(args.work_dir.absolute())}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
