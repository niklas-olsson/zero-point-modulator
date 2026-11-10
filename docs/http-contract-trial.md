# OpenJules HTTP contract trial

This optional trial runs the actual ZPM controller and HTTP provider against the
actual OpenJules worker lifecycle API. The runtime is OpenJules' explicitly named
`SubprocessFixture`, loaded from its test checkout. Five real Python subprocesses
start, emit output, and terminate during the scenario.

This is component integration evidence. It does not establish a production
OpenJules driver, real coding agents, Polyphony authorization, Docker confinement,
durable artifact storage, or the complete installation acceptance in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

## Run

Use a single Linux/Unix host with Python 3.11 or later. Both checkouts must contain
the proposed ZPM controller and OpenJules lifecycle changes. From the ZPM checkout,
in a disposable virtual environment:

```sh
python -m pip install -e . fastapi uvicorn
python tests/contracts/check_openjules.py --openjules-checkout ../OpenJules
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
4. A registered root and child occupy the busy worker without increasing replica
   demand. Drain closes admission. Recording root completion alone does not
   release the still unresolved child occupancy.
5. An injected pre-dispatch boundary leaves an OpenJules journal row with a worker
   ID and unknown state, but no process. After ZPM reopens, discovery must retain
   the pending ensure and resume the same launch key and worker incarnation.
6. A zero target can cancel a second journal-only launch. A later ensure using
   that original specification cannot recreate the cancelled incarnation.
7. All five real subprocesses terminate, and the expected output is present in
   their fixture artifact files before the temporary directory is removed.
8. Replacing the configured fixture with OpenJules' default unavailable runtime
   prevents provisioning through the same HTTP client and capability contract.

The pre-dispatch fault temporarily wraps the lifecycle resume method after its
durable journal commit. It is a deterministic injection of that crash boundary,
not a claim that the API server process was killed. The root/child records are
admission and capture-barrier fixtures; they are not spawned child agents.

## Isolation and limits

The HTTP server binds only to loopback on an ephemeral port. Requests bypass
external proxy configuration for this local trial. A random test credential is
stored under a newly generated environment-variable name and removed afterward;
existing environment values are not replaced. Credentials and worker output are
not included in the JSON result.

SQLite state and fixture artifacts live in a temporary directory. HTTP calls,
server startup and shutdown, and subprocess cleanup have bounded waits. The
fixture's launch records and cancellation tombstones remain in memory. This
trial therefore proves same-process backend behavior and ZPM journal recovery,
not backend durability across a runtime restart. Production launch fencing and
capture remain separate acceptance requirements.
