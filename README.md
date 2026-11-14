# Zero Point Modulator

Zero Point Modulator (ZPM) stores repository enrollment and reconciles an
operator-selected number of OpenJules workers. OpenJules owns worker execution
and bounded child agents. Keystone Polyphony owns collaboration and protected
task authority. ZPM does not assign tasks, supervise children, or maintain a
second task ledger.

This implementation supplies a Python CLI, durable enrollment and
operation journal, a conservative single-host controller, and an OpenJules HTTP
adapter. The single-host deployment path connects the actual authenticated
Polyphony authority and OpenJules Docker runtime. An explicit optional model
policy enables a root coding agent through OpenJules' host-owned inference
broker. Missing capabilities block worker creation. Provider qualification,
child execution, independent review and publication have their own boundaries.
The [offline destination registry](docs/offline-destination-registry.md) retains
operator-enrolled identities, exact public execution approvals and released legacy
history. Separately, [explicit full destination mode](docs/destination-reconciliation.md)
places capacity through approved v2 services while retaining every original
operation and uncertain outcome. Approval import itself performs no remote checks
and does not enable execution. The [role-local installer](docs/role-installation.md)
prepares separate controller/authority and execution bundles. Its one-host
installed trial and actual two-host acceptance remain distinct evidence.

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
For v2 state, these commands report dormant registry records or, after explicit
activation, retained destination capacity and validation observations. The v1
controller refuses both. See [offline preparation](docs/offline-destination-registry.md)
and [full destination reconciliation](docs/destination-reconciliation.md).

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
Enrollment itself creates no execution profile. The optional single-host
installer writes an approved root profile from explicit operator choices of
source revision, image, command and limits. An optional model policy stays in
the trusted OpenJules service and supplies no credentials to the container.

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
redirects and does not copy error-response bodies into diagnostics. Remote
services require HTTPS with certificate and hostname verification. Plain HTTP
is accepted only for literal loopback IPs (IPv4 `127.0.0.0/8` or IPv6 `::1`),
and ignores proxy settings so that its bearer credential stays on the local
route. `localhost`, other DNS aliases, ambiguous numeric addresses and scoped
IPv6 addresses are not HTTP exceptions. Use a literal loopback IP for a local
service, or configure a verified HTTPS reverse proxy for remote access.
Embedded URL credentials, query/fragment delimiters, whitespace/control/format
characters and invalid ports are rejected before credential resolution or
transport. Host and port text cannot use percent escapes; percent-encoded path
prefixes remain supported. For example, `http://[::1]:8000` selects an IPv6
loopback listener, and `https://worker.example:8443/approved/runtime` preserves
the HTTPS service prefix before ZPM appends `/api/workers`.

The deployment `max_workers` is a required admission ceiling across all owned
reservations, including starting, draining, and unknown workers. It is not a
hidden fixed replica limit. Lowering this ceiling prevents new dispatch; use
repository targets or their ceilings to select existing workers for drain.
When existing reservations exceed a newly reduced deployment ceiling,
reconciliation reports the `over_capacity` count and a reason for every binding.
It does not silently change another repository's selected target.
`timeout_seconds` sets one total HTTP request deadline and must be greater than
zero and at most 120. The deadline includes transport-process startup, DNS,
connection/TLS setup, request transmission, and response headers/body. A
short-lived standard-library helper isolates each request; expiry kills and
reaps it so trickled responses cannot leave active local I/O holding the
controller. Process cleanup and OS scheduling can add a small return delay.
Credentials pass through a private pipe, never process arguments. HTTPS proxy
and TLS certificate environment settings retain their normal transport behavior;
unrelated credentials are not inherited. Each request incurs interpreter startup
overhead, and requests remain sequential. This is a per-request bound, not a
deadline for an entire reconciliation tick. A timeout is an uncertain remote
outcome, never evidence of worker termination or permission to release capacity.

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

Capabilities may include the optional OpenJules diagnostic adopted in
[OpenJules #51](https://github.com/niklas-olsson/OpenJules/issues/51):

```json
{
  "schema_version": 1,
  "producer": "openjules",
  "dimension": "canonical_authority",
  "code": "required_capability_unsupported",
  "requirement": {"name": "child_initial_outcome", "version": 1}
}
```

The outer response carries this object under `diagnostic`. ZPM validates its
exact keys, integer versions, vocabulary and 1,024-byte UTF-8 limit after the
outer schema, lifecycle operations and requested deployment/repository/profile.
This one classified refusal requires `profile_supported: true`,
`repository_access: false` and `managed_dispatch: false`. ZPM derives its display
reason from the validated code and retains the bounded object in
`validation.diagnostic`; remote human `reason` text is never interpreted or copied.

Positive and generic-negative responses omit the field. A legacy positive can
remain ready without it, while a legacy negative remains generically blocked.
A present `null`, malformed or unknown diagnostic, a scope mismatch, or
contradictory capability flags blocks readiness and retains no classified fact.
The next check replaces the observation, including removing a prior diagnostic
after generic failure or recovery. Offline `plan` and `status` retain the last
observation across restart; they do not imply freshness, a permission grant,
successful launch or completed execution. A refusal does not release uncertain
capacity or change the original observation, drain and stop keys. The v1
controller still refuses v2 destination state.

The provider interface and transport are in `zpm/provider.py`; strict response
validation is in `zpm/models.py`. This contract is implemented with OpenJules
[lifecycle issue #3](https://github.com/niklas-olsson/OpenJules/issues/3).

## Installation and validation

Use the [single-host installation guide](docs/single-host-installation.md) for
configuration, bootstrap, generated commands, model and child policies, cleanup,
and publication. It covers the [single-repository](examples/deployment.json) and
[multiple-repository](examples/deployment-multiple.json) forms, plus the
[model](examples/deployment-model.json) and
[child](examples/deployment-children.json) examples.

Both deployment forms accept an optional [sealed Python dependency
plan](docs/single-host-installation.md#sealed-local-python-artifacts). It binds
reviewed local wheel bytes, an explicit pip seed, producer revisions and an
exact Linux CPython 3.11/3.12 tuple. Bootstrap uses private sealed copies and a
local ZPM build with the admitted toolset; it checks installed contents and
retains the same identity on retry. Without that opt-in, the existing ranged
installation remains supported. Neither mode qualifies browser execution or a
different interpreter/platform.

The [schema-3 role installation guide](docs/role-installation.md) covers the
separate [controller-authority](examples/deployment-controller.json) and
[execution](examples/deployment-execution.json) configurations. Each contains
only its two required sources/environments and scoped credentials. The host
operator explicitly initializes and approves a destination, starts admission
closed, and opens it separately from controller activation and capacity.
Role-local sealed plans use version 2 with exactly that role's artifact set;
legacy deployment plans retain version 1. Actual independent-host acceptance
remains in [#51](https://github.com/niklas-olsson/zero-point-modulator/issues/51).

The [Alpine sealed-bootstrap candidate](docs/native-alpine-qualification.md)
freezes one Alpine 3.24.4 / official CPython 3.12.16 input set for native
qualification. Its source-only checker and synthetic archive fixtures do not
acquire packages or run the candidate; native qualification remains NOT RUN.

The [installation-admission data API](docs/single-host-installation.md#installation-admission-contract-fixtures)
binds exact selected inputs and compares producer contract examples. Its private
receipt remains `offline-prepared`; fixture matches do not validate an installed
producer, change bootstrap, or authorize execution.

`zpm deployment prepare` and `verify` are offline. Preparation does not establish
managed-runtime readiness or run an agent; the generated `doctor` checks actual
scoped capabilities. The bundle requires supported managed service entry points
in both other repositories. Keep its state, credentials and deployment identity
across restarts. Identical preparation reuses a verified complete bundle without
resetting current enrollment. For an interrupted attempt, follow
[preparation recovery](docs/single-host-installation.md#retry-interrupted-preparation);
do not treat an incomplete destination as a usable installation. Live schema
conversion, adding repositories to an existing bundle and version upgrades
remain unsupported acceptance cases.

The [cold-session measurements](docs/cold-session-measurements.md) explain the
qualified cold clarification fallback, retained measurements and initial
performance targets. A later private instruction uses a fresh correlated child
execution. Warm reuse remains unsupported.

Run the deterministic suite with:

```sh
python -m unittest discover -v
```

The [CI workflow](.github/workflows/ci.yml) also installs the package and checks
the CLI on Python 3.11 and 3.12. Tests cover enrollment and capacity recovery,
uncertain transport outcomes, preparation process death and artifact retention.
Process-death tests do not establish storage hardware power-loss behavior.
The following guides own each separate integration procedure and its evidence:

| Trial | What its evidence covers |
| --- | --- |
| [HTTP lifecycle](docs/http-contract-trial.md) | Actual client/API contract, lost replies and recovery with real subprocess fixtures; not production confinement. |
| [Managed stack](docs/managed-stack-trial.md) | Installed capacity, crashes, watchdog termination and durable capture. |
| [Independent public discussion](docs/public-discussion-stack-trial.md) | Separate public participants through the retained installed interpreters. |
| [Model-backed root](docs/managed-model-stack-trial.md) | Real coding agent and broker with deterministic localhost provider replies. |
| [Children and managed public discussion](docs/managed-children-stack-trial.md) | Private child execution, outcomes, cold clarification, capture, scoped cleanup/restart and bounded public contributions. |
| [Deployment isolation](docs/managed-isolation-stack-trial.md) | Two independent deployments on one Docker Engine; not a shared-service installation. |
| [Shared-service installation](docs/managed-multi-repository-stack-trial.md) | Two repositories under one controller/service pair, scoped credentials, capacity and recovery. |
| [Role-local installation](docs/role-installation.md#qualification-and-remaining-host-acceptance) | Three generated bundles on one host; separate role custody, explicit host approval/admission, installed command recovery and retained results. |

The [retained shared-service qualification](docs/managed-multi-repository-stack-trial.md#retained-qualification)
remains evidence for its recorded revisions. Current integrated acceptance and
remaining criteria belong in [#3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).
A passing component test or prepared bundle does not qualify another revision
set. Installed trials use controlled commands or deterministic model replies;
live-provider behavior, independent review and actual forge publication remain
separate outcomes. Capturing a candidate does not authorize its publication or merge.

## Contributor map

Start with the source and documentation home for the behavior being changed.
The table is navigation, not another protocol specification.

| Change | Representative source | Documentation home |
| --- | --- | --- |
| Enrollment, configuration and CLI | [models](zpm/models.py), [CLI](zpm/cli.py), [store](zpm/store.py) | [Install and inspect](#install-and-inspect), [configuration ownership](#configuration-ownership) and [commands](#check-and-reconcile) in this README |
| Disabled destination registry, public approvals and offline state transition | [registry](zpm/registry.py), [public manifest syntax](zpm/portable_manifest.py), [approval retention](zpm/approvals.py), [store](zpm/store.py), [CLI](zpm/cli.py) | [Offline destination registry](docs/offline-destination-registry.md); [#50](https://github.com/niklas-olsson/zero-point-modulator/issues/50) for the consumer contract |
| Full destination capacity and original recovery | [destination controller](zpm/destination_controller.py), [bound journal](zpm/destination_state.py), [exact response contracts](zpm/destination_contract.py) | [Destination reconciliation](docs/destination-reconciliation.md); [installed two-service trial](tests/contracts/check_destination_stack.py) |
| Capacity, durable intent and recovery | [controller](zpm/controller.py), [store](zpm/store.py) | [Recovery and capacity rules](#recovery-and-capacity-rules) in this README |
| Provider transport and lifecycle responses | [provider](zpm/provider.py), [HTTP transport](zpm/_http_transport.py), [response models](zpm/models.py) | [Wire contract](#openjules-wire-contract) in this README; [HTTP trial](docs/http-contract-trial.md) |
| Installation, bootstrap and generated configuration | [deployment](zpm/deployment.py), [sealed dependencies](zpm/dependencies.py), [interpreter context](zpm/installation_context.py) | [Single-host installation](docs/single-host-installation.md) |
| Role-local installation and original host identity | [role schema/rendering](zpm/deployment_roles.py), [service-set projection](zpm/installation_roles.py), [role commands](zpm/role_manager.py) | [Role-local installation](docs/role-installation.md) |
| Offline installation receipt and contract examples | [pure admission data API](zpm/installation_admission.py), [attributed fixtures](tests/fixtures/installation-admission) | [Installation-admission contract fixtures](docs/single-host-installation.md#installation-admission-contract-fixtures); [#56](https://github.com/niklas-olsson/zero-point-modulator/issues/56) |
| Installed trials and component pins | [trial harnesses](tests/contracts), [CI workflow](.github/workflows/ci.yml) | The corresponding trial guide above; [#3](https://github.com/niklas-olsson/zero-point-modulator/issues/3) for integrated acceptance |
| Native Alpine sealed-bootstrap investigation | [source admission](tests/contracts/check_alpine_sealed_candidate.py), [frozen public inputs](tests/contracts/native-alpine) | [Native candidate, limits and pending execution](docs/native-alpine-qualification.md); [#63](https://github.com/niklas-olsson/zero-point-modulator/issues/63) |
| Cold-session and idle observations | [operational measurements](tests/contracts/operational_measurement.py), [cold trial](tests/contracts/check_cold_children_stack.py) | [Cold-session measurements](docs/cold-session-measurements.md) |
| Installed publication procedure and trial | [publication trial](tests/contracts/check_publication_stack.py), [execution-only workflow](.github/workflows/managed-publication-execution.yml), [full publication workflow](.github/workflows/managed-publication.yml) | [Publish a final child composition](docs/single-host-installation.md#publish-a-final-child-composition); OpenJules owns the publisher |

Update the canonical explanation when behavior, commands, configuration,
recovery, guarantees or evidence change, and update this map when routing
changes. Internal refactoring needs no cosmetic prose or routine "no docs"
declaration. Reuse, simplify or remove duplication while preserving exact
identifiers, historical evidence, uncertainty and attributed contributions.

For a change spanning the repositories, follow enrollment and capacity here,
[Polyphony's canonical authority](https://github.com/niklas-olsson/keystone-polyphony/blob/main/docs/managed-runtime.md)
and [child grants](https://github.com/niklas-olsson/keystone-polyphony/blob/main/docs/child-grants.md),
then [OpenJules execution and capture](https://github.com/niklas-olsson/OpenJules/blob/main/docs/managed-docker-runtime.md)
and [publication](https://github.com/niklas-olsson/OpenJules/blob/main/docs/managed-publication.md).
For example, an uncertain ensure retains ZPM's original launch key and capacity
reservation; OpenJules discovers or recovers that execution, while Polyphony
retains protected-task authority. A timeout does not establish termination or
permit ZPM to release capacity.

[Enrollment #1](https://github.com/niklas-olsson/zero-point-modulator/issues/1)
and [reconciliation #2](https://github.com/niklas-olsson/zero-point-modulator/issues/2)
retain their implementation decisions. Use the existing
[#3 acceptance record](https://github.com/niklas-olsson/zero-point-modulator/issues/3)
for the exact integrated revision set and unresolved work.
