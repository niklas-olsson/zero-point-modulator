"""Fixed two-child publication task; provider replies are explicit fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import threading

from check_model_stack import ModelFixture
from check_stack import require

REPOSITORY = "github:github.com/niklas-olsson/zero-point-modulator"
REPOSITORY_ID = 1453104676
TARGET_BRANCH = "main"
MODEL = "deterministic-publication-fixture"
FILES = {
    "runbook": "docs/managed-publication-stack-trial.md",
    "example": "examples/managed-publication.json",
}
EXAMPLE = {
    "schema_version": 1,
    "state_directory": "/absolute/private/openjules-publication",
    "source_path": "/absolute/checkouts/zero-point-modulator",
    "base_commit": "REPLACE_WITH_FULL_APPROVED_BASE_SHA",
    "authority": {
        "endpoint": "http://127.0.0.1:8765",
        "token_ref": "env:POLYPHONY_PUBLICATION_READ_TOKEN",
    },
    "forge": {
        "deployment_id": "REPLACE_WITH_DEPLOYMENT_ID",
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "target_branch": TARGET_BRANCH,
        "branch_prefix": "openjules/managed/qualification/",
        "token_ref": "env:GITHUB_PUBLICATION_TOKEN",
        "author_name": "OpenJules managed qualification",
        "author_email": "openjules-qualification@users.noreply.github.com",
    },
    "operation_seconds": 120,
}
RUNBOOK = """# Managed publication qualification

The manually dispatched Managed publication qualification workflow produces one
actual draft from a real managed root and two children against this repository's
exact approved main revision. Its model replies are deterministic fixtures.
The workflow qualifies execution, composition and forge delivery, not model
reasoning or live-provider compatibility.

## Before dispatch

Merge and review the workflow infrastructure first. Dispatch only its reviewed
default-branch revision. Confirm that the repository permits GitHub Actions to
create pull requests. The job requests contents and pull-requests write access.
The approved base must remain current through publication; a changed base is a
failure to reconcile, not permission to rebase an existing candidate.

The workflow has two separate steps. Execution has no forge-token environment.
It runs one parent and two children, completes the full repository unit suite in
the confined root, captures canonical results and stops every owned service and
the model fixture. A new publisher-only process then reopens the original
authority with an environment that excludes the forge credential. It invokes
the installed publisher twice against the same private journal and candidate.

## Operator configuration

The example at ../examples/managed-publication.json is a template, not an
executable deployment configuration. Replace its source, state, approved base,
deployment and authority endpoint with the original installation's values.
Keep distinct explicit host references for authority and forge credentials.
Never copy credential values into this file, Git URLs, command arguments,
workspace files or reports. The qualification workflow constructs its privileged
configuration from its sealed installation and fixed policy; generated task
files cannot choose those values.

The publisher must observe the actual repository ID, approved base, composite
commit/tree, provenance marker and one open draft. A second fresh publisher
process must return that same proposal. Existing-ready, closed, stale, uncertain
or unavailable results do not pass this new-draft trial.

## Review and recovery

The resulting draft is a normal review vehicle. Approve its GitHub Actions
workflows when GitHub requests writer approval, run the repository's complete
checks, and request an eligible independent reviewer against the actual head and
base. Publication grants no acceptance, readiness or merge authority.

An ambiguous result stops the trial. Do not rerun execution to manufacture a
replacement candidate or proposal. Inspect the original operation and actual
forge state. A workflow rerun has a new runner and cannot resume the original
private journal; automatic reruns are refused. A later manual dispatch requires
a separate decision after the original operation is reconciled.

Only safe aggregate IDs, observed commit/tree/PR identity and hashes are retained
as workflow evidence. Private databases, credentials, captures and publication
journals are not uploaded. Fresh-process replay is tested within one job.
Cross-job recovery and recovery after loss of the runner are not qualified.

The root's command guard is 120 seconds and the inner suite guard is 110 seconds.
Each child retains its original 90-second deadline until the whole-worker fence,
so the earliest child deadline can end the root sooner. Qualification must finish
within that effective bound. The report records the discovered and executed test
counts and actual suite elapsed time. A timeout fails without extending grants.
The complete suite log remains only in the private captured workspace; exported
evidence contains its hash and a bounded verification summary.
The suite uses a fresh private temporary directory in the owned root workspace,
outside the candidate checkout, so installation fixtures can execute their copied
interpreters while /tmp remains noexec. Success removes the empty directory;
failure retains it privately and never reuses it.

A passing draft does not prove the separate combined-test-failure scenario.
"""
CONTENTS = {"runbook": RUNBOOK, "example": json.dumps(EXAMPLE, indent=2, sort_keys=True) + "\n"}
REQUIREMENTS = (
    "Delegate the publication runbook and configuration example to separate children. "
    "Each child adds only its assigned file. Compose exactly both returned patches, "
    "run the full repository unittest suite through configured verification, and request "
    "independent review. Do not change existing files, publish, or accept the task."
)
FORGE_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PUBLICATION_TOKEN")
ROOT_LAUNCHER = r"""import functools,importlib.util,json,sys
path='/opt/openjules/managed_root_runner.py'
spec=importlib.util.spec_from_file_location('_publication_root_image',path)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
original=module._load_sibling
def load(name):
    sibling=original(name)
    if name=='managed_runner':
        factory=functools.partial(sibling.LocalTools,command_seconds=120)
        assert factory.keywords=={'command_seconds':120}
        sibling.LocalTools=factory
        print(json.dumps({'event':'publication_profile','command_seconds':factory.keywords['command_seconds']}),flush=True)
    return sibling
module._load_sibling=load
raise SystemExit(module.main(sys.argv[1:]))
"""
BOUNDARY = (
    "assert not any(name in os.environ for name in " + repr(FORGE_NAMES) + ")\n"
    "assert not pathlib.Path('/var/run/docker.sock').exists()\n"
    "assert not pathlib.Path('/source/.git').exists()\n"
    "assert not os.access('/run/openjules-model',os.W_OK)\n"
)
EXPECTED_HASHES = {FILES[name]: hashlib.sha256(text.encode()).hexdigest() for name, text in CONTENTS.items()}
ROOT_VERIFY = [
    "python3", "-I", "-c",
    "import hashlib,json,os,pathlib,re,stat,subprocess,tempfile,time,unittest\n"
    "assert os.getuid()==65532\n" + BOUNDARY
    + "expected=" + repr(EXPECTED_HASHES) + "\n"
    "assert all(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()==h for p,h in expected.items())\n"
    "def private_directory(path):\n"
    "    info=path.lstat()\n"
    "    assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==os.geteuid()\n"
    "    assert path.resolve()==path\n"
    "    return info.st_dev,info.st_ino\n"
    "run=pathlib.Path('/workspace/run')\n"
    "run_identity=private_directory(run)\n"
    "suite_tmp=run/'publication-unittest-tmp'\n"
    "suite_tmp.mkdir(mode=0o700)\n"
    "suite_tmp_identity=private_directory(suite_tmp)\n"
    "suite_environment=dict(os.environ,TMPDIR=str(suite_tmp))\n"
    "tempfile.tempdir=str(suite_tmp)\n"
    "expected_tests=unittest.TestLoader().discover('.').countTestCases()\n"
    "assert expected_tests>0\n"
    "started=time.monotonic()\n"
    "with open('/workspace/run/publication-unittest.log','w+b') as output:\n"
    "    result=subprocess.run(['python3','-m','unittest','discover','-v'],env=suite_environment,stdout=output,stderr=subprocess.STDOUT,timeout=110)\n"
    "    size=output.seek(0,2)\n"
    "    assert 0<size<=2000000\n"
    "    output.seek(0)\n"
    "    complete_bytes=output.read(2000001)\n"
    "    complete=complete_bytes.decode('utf-8',errors='replace')\n"
    "    tail=complete_bytes[-4096:].decode('utf-8',errors='ignore')\n"
    "print(tail,end='')\n"
    "matches=re.findall(r'Ran (\\d+) tests? in ([0-9.]+)s',complete)\n"
    "assert result.returncode==0 and matches and int(matches[-1][0])==expected_tests\n"
    "assert private_directory(run)==run_identity and private_directory(suite_tmp)==suite_tmp_identity\n"
    "suite_tmp.rmdir()\n"
    "print('PUBLICATION_COMBINED_RESULT '+json.dumps({'tests':int(matches[-1][0]),'expected_tests':expected_tests,'elapsed_seconds':time.monotonic()-started,'exit_code':result.returncode}),flush=True)\n",
]
CHILD_VERIFY = [
    "python3", "-I", "-c",
    "import hashlib,os,pathlib\n"
    "assert os.getuid() in (60000,60001)\n" + BOUNDARY
    + "expected=" + repr(EXPECTED_HASHES) + "\n"
    "present=[p for p in expected if pathlib.Path(p).exists()]\n"
    "assert len(present)==1 and hashlib.sha256(pathlib.Path(present[0]).read_bytes()).hexdigest()==expected[present[0]]\n"
    "print('PUBLICATION_CHILD_VERIFIED',flush=True)\n",
]


class PublicationFixture(ModelFixture):
    """Exactly six parent calls and two calls per independent child."""

    def __init__(self):
        require(not any(name in __import__("os").environ for name in FORGE_NAMES),
                "runtime/model phase must not receive a forge credential")
        super().__init__()
        self.root_turns = 0
        self.child_turns = dict.fromkeys(FILES, 0)
        self.child_ids, self.patches = {}, {}
        self.two_spawned, self.release_root = threading.Event(), threading.Event()
        self.total_calls = 10

    def __exit__(self, *args):
        self.release_root.set()
        return super().__exit__(*args)

    def reply(self, payload):
        require(payload.get("model") == MODEL and payload.get("max_tokens") == 1024
                and payload.get("stream") is False and payload.get("tool_choice") == "auto",
                "publication fixture policy mismatch")
        messages = payload.get("messages", [])
        require(len(messages) >= 2 and messages[1].get("role") == "user",
                "publication fixture lacks canonical instructions")
        initial = messages[1].get("content", "")
        name = next((key for key in FILES if initial.startswith("PUBLICATION_CHILD_" + key + ":")), None)
        expected_tools = {"read_file", "write_file", "list_files", "run_command", "finish"}
        if name is None:
            expected_tools |= {"spawn_child", "child_status", "child_result", "cancel_child", "close_children"}
            require(REQUIREMENTS in initial, "publication fixture lacks original root requirements")
        require({tool["function"]["name"] for tool in payload.get("tools", [])} == expected_tools,
                "publication fixture tool boundary changed")
        results = [item for item in messages if item.get("role") == "tool"]
        pause = False
        with self.lock:
            identity = name or "root"
            previous = self.child_turns[name] if name else self.root_turns
            require(len(results) == previous and (not previous or (
                results[-1].get("tool_call_id") == f"{identity}-{previous}"
                and not results[-1].get("content", "").startswith("Tool error:"))),
                "publication tool result is missing, repeated or failed")
            turn = previous + 1
            if name:
                require(turn <= 2, "publication child exceeded its two-call budget")
                self.child_turns[name] = turn
                tool, arguments = (
                    ("write_file", {"path": FILES[name], "content": CONTENTS[name]})
                    if turn == 1 else ("finish", {"summary": "Completed assigned publication artifact; request review"})
                )
            else:
                require(turn <= 6, "publication root exceeded its six-call budget")
                self.root_turns = turn
                names = list(FILES)
                if turn in (2, 3):
                    child_id = json.loads(results[-1]["content"])["child_id"]
                    require(isinstance(child_id, str) and re.fullmatch(r"child_[0-9a-f]{32}", child_id)
                            and child_id not in self.child_ids.values(), "publication child identity changed")
                    self.child_ids[names[turn - 2]] = child_id
                if turn in (4, 5):
                    key = names[turn - 4]
                    page = json.loads(results[-1]["content"])
                    content = page.get("content", "")
                    digest = hashlib.sha256(content.encode()).hexdigest()
                    path = FILES[key]
                    require(page.get("child_id") == self.child_ids[key] and page.get("kind") == "patch"
                            and page.get("offset") == 0 and page.get("total_bytes") == len(content.encode())
                            and page.get("content_sha256") == page.get("artifact_sha256") == digest
                            and f"diff --git a/{path} b/{path}" in content and content.count("diff --git ") == 1,
                            "publication child patch is incomplete or outside its assigned file")
                    self.patches[key] = content
                if turn <= 2:
                    key = names[turn - 1]
                    tool, arguments = "spawn_child", {
                        "instructions": "PUBLICATION_CHILD_" + key + ": add only " + FILES[key],
                        "seconds": 90, "max_turns": 2, "capabilities": ["python"],
                    }
                elif turn in (3, 4):
                    key = names[turn - 3]
                    tool, arguments = "child_result", {"child_id": self.child_ids[key], "kind": "patch"}
                    pause = turn == 3
                elif turn == 5:
                    script = (
                        "import hashlib,pathlib,subprocess\n"
                        "subprocess.run(['git','apply','-'],input=" + repr("".join(self.patches[key] for key in names))
                        + ",text=True,check=True)\nexpected=" + repr(EXPECTED_HASHES) + "\n"
                        "assert all(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()==h for p,h in expected.items())\n"
                        "print('PUBLICATION_CHILD_UNION_COMPOSED')\n"
                    )
                    tool, arguments = "run_command", {"argv": ["python3", "-I", "-c", script]}
                else:
                    result = json.loads(results[-1]["content"])
                    require(result.get("state") == "exited" and result.get("exit_code") == 0
                            and "PUBLICATION_CHILD_UNION_COMPOSED" in result.get("output", ""),
                            "publication root did not compose the exact children")
                    tool, arguments = "finish", {"summary": "Composed both children; run configured combined checks and request independent review"}
            self.requests.append(payload)
        if pause:
            self.two_spawned.set()
            require(self.release_root.wait(20), "publication observation gate timed out")
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"{identity}-{turn}", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }]}
