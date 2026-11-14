# OpenJules HTTP contract trial

This optional trial runs the actual ZPM controller and HTTP provider against the
actual OpenJules worker lifecycle API. The runtime is OpenJules' explicitly named
`SubprocessFixture`, loaded from its test checkout. Five real Python subprocesses
start, emit output, and terminate during the scenario.

A second paired trial exercises typed capability observations through the actual
managed OpenJules service and ZPM HTTP provider. Its authority responses and
Docker preflight are named producer fixtures; Git, SQLite, service lifecycle,
local sockets and both HTTP boundaries are real. It makes no worker, protected
task or model call. The trials have separate reports and evidence scopes.

This is component integration evidence. It does not establish a production
OpenJules driver, real coding agents, Polyphony authorization, Docker confinement,
durable artifact storage, or the complete installation acceptance in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

## Continuous integration baseline

The `OpenJules lifecycle contract` CI job runs this trial after the existing
Python 3.11 and 3.12 unit, package-installation and CLI checks pass. It uses the
ZPM source checked out for that workflow event and an immutable OpenJules pin:

| Component | Revision checked |
| --- | --- |
| ZPM | The workflow checkout; a pull request uses GitHub's proposed merge commit |
| OpenJules | [`0dd5bf4e42b3677c3f2f564655bc87a97c4bf973`](https://github.com/niklas-olsson/OpenJules/commit/0dd5bf4e42b3677c3f2f564655bc87a97c4bf973) |

This is the same merged runtime used by all required installed-stack jobs, including
the explicit managed-public profile. The cold-session option, loopback bootstrap,
shared HTTP transport, bounded socket denial and scoped unsupported-capability
diagnostic remain in that source. This HTTP lifecycle job does not enable a
child session or a public profile, start a discussion participant, or supply a
restricted conversation service.
Their common merged Polyphony pin is
`6480e36355bf0d5de4e25a560d40e665fd76038c`;
this HTTP-only trial does not run Polyphony. Its merged ancestry includes
[durable control replay](https://github.com/niklas-olsson/OpenJules/pull/15)
after an accepted drain or stop encounters a busy runtime lock. The deterministic
regression below requires that replay without another client control request.
The same pins also pair OpenJules' opt-in initial child outcomes with Polyphony's
final-manifest validation. The separate installed child variant exercises that
callback/capture contract; this HTTP lifecycle trial does not.
The refreshed set requires OpenJules' full repository CI and all required jobs on
the changed ZPM revision: unit/package/CLI checks on both Python versions, this
HTTP lifecycle job and all required Docker installation jobs. Historical runs do
not qualify a different revision set. Record exact ZPM and component revisions,
CI links and remaining acceptance in [ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

Both revisions are printed in the job log. The contract job runs on Python 3.12,
installs FastAPI, Uvicorn and the dispatcher's HTTPX, Docker SDK and PyYAML import
dependencies, and expects all nine lifecycle checks to pass with five subprocess
fixtures. The paired diagnostic trial additionally imports cryptography through
the child journal's bounded public-record validators and uses pytest only to load
the producer's explicit existing test fixtures. It compares the exact shared
`tests/fixtures/capability-diagnostic-v1.json` bytes and records their SHA-256 plus
both source revisions, trees and dirty-state flags.
The SDK is imported without creating a Docker client. The trial does not use model,
repository-publication or authority credentials. The workflow has read-only
repository permissions, and the contract job does not retain checkout credentials.

To update the shared component pins:

1. Select an immutable OpenJules commit whose full repository CI passes. Use a
   clean checkout of that commit for the local trial below.
2. Change all operative checkout `ref` values in `.github/workflows/ci.yml`,
   `.github/workflows/managed-publication.yml` and
   `.github/workflows/managed-publication-execution.yml`, the matching revision
   constant in `tests/contracts/check_publication_stack.py`, its workflow-test
   pin in `tests/test_publication_execution_workflow.py`, and the current HTTP,
   command, model, child, isolation, shared-service, independent-public and
   installation guide references together. Keep one explicit three-repository
   delivery set. The separately
   labelled historical-authority negative checkout remains frozen at its own
   revision; retained historical results also keep their original revisions.
   Do not substitute a moving branch or tag.
3. Run the HTTP trial locally, then require the ZPM unit, package, CLI and all
   required integration jobs to pass for the proposed update before merging it.

The pin identifies the component contract intended for qualification. It does not promote the fixture
runtime to a production managed coding profile or complete the installation
acceptance gate.

## Run

Use a single Linux/Unix host with Python 3.11 or later. Both checkouts must contain
the ZPM controller and OpenJules lifecycle implementation. From the ZPM checkout,
in a disposable virtual environment:

```sh
python -m pip install -e . fastapi uvicorn httpx docker pyyaml cryptography pytest
python tests/contracts/check_openjules.py --openjules-checkout ../OpenJules
python tests/contracts/check_capability_diagnostics.py --openjules-checkout ../OpenJules
```

Pass the actual OpenJules checkout path if the repositories are elsewhere. The
script uses source from those checkouts, so record both commits and whether either
checkout has uncommitted changes when retaining its result. It imports OpenJules'
test fixture deliberately; a checkout without that fixture fails clearly.

The check is separate from `python -m unittest discover -v`. Its additional
dependencies and second checkout are optional; they are not ZPM runtime
dependencies. It makes no model, GitHub or Polyphony calls.

## Verified behavior

The trial fails with an assertion if any of these expectations is violated:

1. A reply is deliberately lost after a successful launch over HTTP. Reopening
   ZPM's SQLite store recovers the original worker before filling the remaining
   capacity deficit. Reconciliation does not launch an extra incarnation.
2. A credential scoped to the configured deployment cannot discover workers in
   a foreign deployment. The actual HTTP API returns authorization denial.
3. One repository changes through `0 → 2 → 3 → 1 → 0`, while a second repository
   retains its independent target. Idle workers retire before a busy worker.
4. While the worker's real lifecycle lock is held, HTTP drain acknowledges
   durable intent and ZPM clears its pending request. The trusted dispatcher is
   stopped, the lifecycle journal is reopened, and a new dispatcher delivers
   the exact original operation once. The key and payload remain unchanged;
   neither active work nor the other repository's reservation is released.
5. A registered root and child occupy the busy worker without increasing replica
   demand. Drain closes admission. Recording root completion alone does not
   release the still unresolved child occupancy.
6. An injected pre-dispatch boundary leaves an OpenJules journal row with a worker
   ID and unknown state, but no process. After ZPM reopens, discovery must retain
   the pending ensure and resume the same launch key and worker incarnation.
7. A zero target can cancel a second journal-only launch. A later ensure using
   that original specification cannot recreate the cancelled incarnation.
8. All five real subprocesses terminate, and the expected output is present in
   their fixture artifact files before the temporary directory is removed.
9. Replacing the configured fixture with OpenJules' default unavailable runtime
   prevents provisioning through the same HTTP client and capability contract.

The drain fault holds the production lifecycle lock across a real HTTP request.
The production dispatcher loops run in bounded threads and use the persisted
lifecycle and dispatch journals. Their authority sentinel rejects every task
request; no task authority is emulated or contacted. Only the subprocess runtime
is a fixture, and the API server process remains running during daemon restart.

The pre-dispatch fault temporarily wraps the lifecycle resume method after its
durable journal commit. It is a deterministic injection of that crash boundary,
not a claim that the API server process was killed. The root/child records are
admission and capture-barrier fixtures; they are not spawned child agents.

## Isolation and limits

The HTTP server binds only to loopback on an ephemeral port. Requests bypass
external proxy configuration for this local trial. A random test credential is
stored under a newly generated environment-variable name and removed afterward.
The trial temporarily sets both `no_proxy` and `NO_PROXY` to `*` and restores
their prior values on exit. Credentials and worker output are not included in
the JSON result.

SQLite state and fixture artifacts live in a temporary directory. HTTP calls,
server startup and shutdown, and subprocess cleanup have bounded waits. The
fixture's launch records and cancellation tombstones remain in memory. This
trial therefore proves same-process backend behavior and ZPM journal recovery,
not backend durability across a runtime restart. Production launch fencing and
capture remain separate acceptance requirements.

## Paired typed diagnostic trial

The producer's composed managed-service fixture performs real scoped authority
HTTP calls. The paired trial checks supported capability omission, the adopted
unsupported-authority object, changed human wording, a generic authority outage,
a wrong requested profile, and recovery. ZPM stores only the validated bounded
diagnostic, makes no replacement launch, and clears the classification when the
next response no longer supports it. It checks retained producer journals and
Docker-fixture counters for absence of new work. Focused consumer tests separately
cover malformed/unknown/null/oversized payloads, Boolean aliases, positive
contradictions, transport denial, journal reopen and original-key drain recovery.

The explicit `FakeDocker` supplies preflight image/daemon/topology facts only;
the authority HTTP responder supplies deterministic capability facts only.
Neither proves production confinement, actual installed Polyphony, two hosts,
browser execution or a live model. The installed child trial separately repeats
the typed refusal against the pinned historical Polyphony process. Local socket
restrictions can prevent the composed service from starting; retain that result
as a local limitation and require the unchanged native CI trial to pass.
