# Zero Point Modulator

Zero Point Modulator (ZPM) stores repository enrollment and reconciles an
operator-selected number of OpenJules workers. OpenJules owns worker execution
and bounded child agents. Keystone Polyphony owns collaboration and protected
task authority. ZPM does not assign tasks, supervise children, or maintain a
second task ledger.

This first implementation supplies a Python CLI, durable enrollment and
operation journal, a conservative single-host controller, and an OpenJules HTTP
adapter. The controller can be exercised against deterministic fixtures. A
production coding swarm still requires the authenticated managed-dispatch
contract and a real capable OpenJules driver. Missing capabilities block worker
creation. An HTTP health response or a passing fixture test does not establish
production readiness.

## Install and inspect

Python 3.11 or newer on a POSIX host is required. The package has no runtime
dependencies outside the Python standard library. SQLite state and the
controller lock must reside on the same local host.

```sh
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/zpm --help
.venv/bin/zpm validate examples/repository.json
```

`python -m zpm` exposes the same commands directly from this checkout. `validate`
checks syntax and normalization only. It does not create state, resolve
credentials, contact OpenJules, run repository setup, or make model calls.

Create a stable deployment identity and enroll a repository:

```sh
zpm --state .zpm/state.sqlite3 --deployment-id my-deployment init
zpm --state .zpm/state.sqlite3 enroll examples/repository.json
zpm --state .zpm/state.sqlite3 plan
zpm --state .zpm/state.sqlite3 status
```

Retain the same state file across restarts. Omitting `--deployment-id` at first
initialization creates a UUID. Supplying it on subsequent commands verifies the
stored identity. The OpenJules API credential must authorize that exact
deployment and canonical repository pair.

`status` and `plan` report locally recorded observations; they do not contact a
provider or launch workers. They distinguish desired and effective targets,
healthy and reserved capacity, pending operations, shortfall, and blocking
reasons. Local output is not a fresh readiness check. A plan without a provider
configuration reports `max_workers: null`, since it does not know the deployment
admission ceiling used by the next controller run.

## Configuration ownership

The version 1 repository binding is intentionally small:

```json
{
  "schema_version": 1,
  "repository": "github:github.com/example/project",
  "profile": "approved-coding",
  "desired_workers": 2,
  "max_workers": 6
}
```

| Field | Owner and meaning |
| --- | --- |
| `repository` | ZPM canonical enrollment key. Version 1 accepts GitHub `owner/repo`, HTTPS URLs, or the canonical form shown above. |
| `desired_workers` | ZPM operator-selected, nonnegative top-level worker target. Busy workers count; child agents do not add replicas. |
| `profile` | An approved OpenJules profile reference. OpenJules resolves execution settings, authority/discovery binding, repository access, and runtime/model secret references. Changing this reference retires old-profile workers before replacing them. |
| `max_workers` | Optional ZPM repository ceiling. A smaller ceiling reports the unmet desired capacity and drains excess owned workers. |
| `enabled`, `paused` | ZPM enrollment policy; optional booleans defaulting to `true` and `false`. Either disabled or paused makes the effective target zero. |
| `schema_version` | Required configuration version; unsupported versions and unknown fields are rejected. |

Profile definitions and execution settings stay in OpenJules. The profile name
must resolve to an approved immutable configuration there; arbitrary commands,
images, authority URLs, or secret values are not launch input supplied by ZPM.
No production profile is created by this repository.

The shared provider configuration is separate from individual enrollments:

```json
{
  "schema_version": 1,
  "endpoint": "http://127.0.0.1:8000",
  "token_ref": "env:OPENJULES_WORKER_TOKEN",
  "max_workers": 8,
  "timeout_seconds": 10
}
```

`endpoint` identifies the OpenJules service root. `token_ref` names an environment
variable; it is not the token itself. Credentials are resolved only when making
a request and are not stored in the state journal. The provider rejects
redirects and does not copy error-response bodies into diagnostics. Use HTTPS
for a remote service. The HTTP example assumes a local host boundary.

The deployment `max_workers` is a required admission ceiling across all owned
reservations, including starting, draining, and unknown workers. It is not a
hidden fixed replica limit. Lowering this ceiling prevents new dispatch; use
repository targets or their ceilings to select existing workers for drain.
When existing reservations exceed a newly reduced deployment ceiling,
reconciliation reports the `over_capacity` count and a reason for every binding.
It does not silently change another repository's selected target.
`timeout_seconds` sets the HTTP socket I/O timeout and must be greater than zero
and at most 120. It is not a hard elapsed-time deadline for an entire response;
an endpoint that continually sends partial data can extend a request. A timeout
is an uncertain outcome, never evidence of termination.

## Check and reconcile

Provide `OPENJULES_WORKER_TOKEN` through your environment's secret mechanism and
use an OpenJules service implementing the version 1 lifecycle contract:

```sh
zpm check --provider examples/provider.json
zpm reconcile --provider examples/provider.json
zpm reconcile --provider examples/provider.json --watch --interval 5
```

`check` performs a scoped remote preflight without launching a worker. It
requires the lifecycle operations, profile support, actual repository access,
and authenticated managed dispatch. Its response must echo the requested
deployment, repository, and profile. Authorization to call the API is not a
substitute for source access or task-authority connectivity. Each reconciliation
tick refreshes this check before making new launch decisions.

An unavailable or incomplete managed runtime reports `ready: false` and a
reason. Provisioning remains blocked until OpenJules implements the required
profile and protected dispatch. Existing owned workers can still be observed
and drained when launch readiness is unavailable.

`--max-actions` defaults to 16 mutations per tick. It bounds a reconciliation
batch and does not cap the selected worker count. Repositories share that
budget across retries, drains, stops and launches. A persistent rotating cursor
allocates small batches fairly, so one repository's repeated failures cannot
consume every mutation slot. Unused per-repository credits expire after the
tick. Watch mode holds the
single-controller lock and polls at a bounded interval. Neither observation nor
idle reconciliation makes model calls.

Change capacity or enrollment using the same persistent state:

```sh
zpm target example/project 3
zpm target example/project 0
zpm pause example/project
zpm resume example/project 2
zpm remove example/project
```

`target 0` retains an enabled binding. `pause` retains the selected target but
makes its effective target zero. `resume` requires an explicit new target.
`remove` disables enrollment while retaining binding and worker history. Each
change takes effect when the controller next reconciles. Re-enroll a removed
binding before resuming it. Repeating an identical normalized enrollment is
idempotent; edits increment its revision.

ZPM does not delete task-authority state, artifacts, or foreign resources during
any of these commands. Store the authority database and recoverable artifacts
outside scalable workers so that zero capacity does not erase pending work.

## Recovery and capacity rules

Each launch has a stable deployment/repository scope, logical slot, increasing
generation, immutable profile, and launch key. OpenJules returns an immutable
worker incarnation. The controller rejects changed specifications, cross-scope
observations, and incarnation reuse across reservations. It never adopts or
drains workers absent from its own journal.

The journal distinguishes a reservation that has never been dispatched from a
committed `ensure` intent. A SQLite transaction rechecks current enrollment,
profile, and capacity before committing dispatch permission. A pause or target
edit committed before that transaction prevents launch. An edit after dispatch
commit may race a launch already authorized under the old intent; subsequent
reconciliation discovers and drains it. ZPM cannot label that outcome as
"never started" simply because a response was lost.

After an uncertain ensure, discovery uses the original launch key. A retry can
use only that same immutable specification and key. Empty discovery does not
release the reservation or justify a fresh generation. A worker whose
observation fails retains its reservation and reports an unknown state.
Discovery of an incarnation with an unresolved launch also retains its ensure
intent: when still authorized, reconciliation retries that same idempotent
ensure so a backend crash before dispatch can recover. If policy instead
retires the known incarnation, a scoped drain supersedes the launch intent and
must establish the runtime cancellation fence before capacity is released.

Healthy capacity includes ready and busy workers. Launch deficit is calculated
against **all unreleased reservations**, not healthy capacity alone. A missing
heartbeat, active child, or temporary outage therefore cannot create replacement
replicas that exceed the target or quota. Starting, draining, and uncertain
capacity are reported separately from healthy capacity.

Scale-down prefers idle workers. Drain closes admission for new roots and new
children in OpenJules; it does not promise that every current task will succeed.
The controller waits for all reported execution references to clear before
requesting stop. A reservation is released only after the provider confirms
termination and artifact capture, or local state proves that dispatch never
occurred. Failed capture and unknown termination hold the reservation. Lost
drain and stop replies retain the original request keys through restart.

The controller lock covers provider I/O, so another local controller cannot
dispatch against the same state file concurrently. Run one active controller
for one deployment. Copying a database to another host or another path does not
create shared ownership. Cross-host failover and network-filesystem SQLite WAL
are unsupported. Back up state with a SQLite-aware backup or after cleanly
stopping the controller; retain the deployment identity when restoring it.

## OpenJules wire contract

All operations require an authorized bearer credential scoped to deployment
and repository. JSON request/response bodies use `schema_version: 1`.

| Operation | HTTP interface |
| --- | --- |
| Capabilities | `GET /api/workers/capabilities?deployment_id=...&repository=...&profile=...` |
| Ensure | `POST /api/workers/ensure` with deployment, repository, slot, generation, launch key and profile |
| Discover | `GET /api/workers?deployment_id=...&repository=...&launch_key=...` |
| Observe | `GET /api/workers/{worker_id}?deployment_id=...&repository=...` |
| Drain / stop | `POST /api/workers/{worker_id}/drain` or `/stop` with deployment, repository and request key |

Capabilities return the echoed scope, `operations`, `managed_dispatch`,
`profile_supported`, `repository_access`, and a reason. Discovery returns
`{"schema_version": 1, "workers": [...]}`. Other lifecycle operations return a
snapshot containing the immutable specification, `worker_id`, state, reason,
`admission_open`, `active_executions`, `termination_confirmed`, and
`artifacts_captured`.

The provider interface and transport are in `zpm/provider.py`; strict response
validation is in `zpm/models.py`. This contract is implemented with OpenJules
[lifecycle issue #3](https://github.com/niklas-olsson/OpenJules/issues/3).

## Validation and remaining integration

```sh
python -m unittest discover -v
```

The deterministic tests cover enrollment/restart, 0 → 2 → 3 → 1 → 0, duplicate
and lost launches, uncertain observations, immutable identity, scope isolation,
controller contention, concurrent policy edits, quota reservations, child-aware
drain, and failed artifact capture. The test provider performs no inference or
real coding. The CI workflow also installs the package and checks the CLI on
Python 3.11 and 3.12.

An optional [OpenJules HTTP contract trial](docs/http-contract-trial.md) exercises
the actual client and lifecycle API together, including lost replies, recovery
before dispatch, cancellation, and five real subprocess fixtures. It is separate
from the unit suite and does not establish production agent or confinement proof.

This implementation advances [enrollment #1](https://github.com/niklas-olsson/zero-point-modulator/issues/1)
and [reconciliation #2](https://github.com/niklas-olsson/zero-point-modulator/issues/2).
The complete installation and workflow trial remains tracked in
[#3](https://github.com/niklas-olsson/zero-point-modulator/issues/3). It requires
the real OpenJules [managed adapter](https://github.com/niklas-olsson/OpenJules/issues/2),
approved isolated runtime, and Polyphony's
[canonical authority](https://github.com/niklas-olsson/keystone-polyphony/issues/109)
and [scoped delegated identity](https://github.com/niklas-olsson/keystone-polyphony/issues/112).
No passing local test substitutes for the separate-process, authenticated,
artifact-preserving trial defined there.
