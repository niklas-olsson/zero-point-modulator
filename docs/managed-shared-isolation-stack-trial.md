# Two repositories in one generated installation

This trial prepares and bootstraps one schema-2 installation with two repository
entries, one controller, one OpenJules service and one Polyphony authority. It
runs actual command tasks in distinct containers on one Docker Engine. B's
original running task must keep progressing while A scales idle capacity,
removes enrollment during an active execution, captures, and explicitly
re-enrolls. The shared services restart only after both repositories finish
capture and have zero outstanding reservations.

The [separate-deployment trial](managed-isolation-stack-trial.md) remains an
independent regression. Its repository-local service outage scenario does not
apply to this shared topology: the service pair, authority and Docker Engine
are common failure domains. This trial does not inject a shared outage while
B is active or claim that one repository can survive it independently.

## Revision set and execution

The `managed-shared-isolation-stack` job uses the same exact companion revisions
as every other installed-stack and HTTP contract job:

| Component | Revision and status |
| --- | --- |
| OpenJules | `c85867c9a110f6c3ea00a53e6a721a15ce15782d`, merged sibling isolation, admission rejection, durable control replay and initial child outcomes |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66`, explicit [#122](https://github.com/niklas-olsson/keystone-polyphony/pull/122) authority candidate dependency, pending its required independent review gate |
| ZPM | The workflow checkout, recorded in the report; PR CI checks GitHub's proposed merge revision |

This is a candidate integration set until the Polyphony dependency is merged
under its policy and the complete delivery set has passed all integration jobs.
Record exact revisions and CI URLs in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).
A new harness, local oracle test or component CI result does not establish
shared-service runtime qualification. Update all pins and trial guides together.

Use clean sibling checkouts, Python 3.11 or newer, Linux and a local rootful
Docker Engine. The command image has no model or forge client behavior. Build
and identify it before running the harness:

```sh
docker build --iidfile /tmp/shared-isolation-image.id - < tests/contracts/Dockerfile.managed-trial
python tests/contracts/check_shared_isolation_stack.py \
  --polyphony-checkout ../keystone-polyphony \
  --openjules-checkout ../OpenJules \
  --image "$(cat /tmp/shared-isolation-image.id)" \
  --work-dir /tmp/managed-shared-isolation-trial
```

The work directory must be new and outside all source checkouts. Bootstrap
installs each component once in the generated private environments. The
generated manager first starts at zero targets and must pass its actual doctor.
The harness then runs exactly the generated service commands and environments
with those installed interpreters. It does not create an alternate service
configuration or runtime implementation.

## Required observations

1. Two separate source repositories, profile names and synthetic guest-secret
   references produce one bundle and one shared ownership journal. The seven
   generated role credentials must be unique. Both real containers run
   simultaneously with distinct workspace volumes and distinct read-only
   source snapshots. Unexpected persistent mounts fail the trial.
2. The shared controller and runtime tokens intentionally authorize both
   configured scopes. Each repository's ingress and reviewer tokens can read
   its own canonical scope and must receive HTTP 403 for the other. Foreign
   worker reads, drains and stops under the wrong repository receive HTTP 404.
   The shared tokens cannot access an unconfigured repository.
3. `src.liminal_bridge.managed_cli` runs as six sequential, separate installed processes:
   two deliveries for A's first request, two for B's first request, and two for
   A's request after re-enrollment. A and B both use `github:issue:1` and
   `controlled-shared-1` initially. Each within-scope replay returns the same
   task, while A and B receive distinct tasks. This establishes cross-process
   canonical enrollment behavior. It does not establish simultaneous ingress
   delivery or OpenJules GitHub event or webhook ingress.
4. B starts one held command. Before and after fresh progress in every A phase,
   witnesses require B's original task, attempt, complete worker specification,
   profile fingerprint, container, Docker exec, input hash and execution budget.
   B's desired target and reservation remain one. Read-only lifecycle/runtime
   journal reads reject any drain or stop history, even a completed operation.
   Controller and canonical state must retain the original binding. Ordinary
   execution-lock uncertainty is named explicitly and is never treated as a
   termination signal or healthy-state claim.
5. Idle A scales from one to two workers and back to one. Shared reservations
   must be three and then two. The oracle validates the complete two-scope plan,
   globally unique worker identities and launch keys, per-scope incarnation
   uniqueness, and exact aggregate reservation count before narrowing the plan
   for an existing per-repository witness. A valid B row cannot hide a foreign
   or duplicated worker under A.
6. A starts its own held task and is removed through the installed ZPM CLI.
   Its exact original drain key remains pending in the lifecycle journal;
   both active roots keep their reservations. A and B must both show fresh
   progress during that held state. Releasing A's private fixture barrier
   permits real runtime completion, final capture and canonical candidate
   publication. Only then can A's reservation reach zero, leaving B's one
   original reservation.
7. Repeated preparation reuses the sealed bundle, preserves credentials and
   leaves A removed. Explicit enrollment uses A's generated binding-file
   mapping. Target one creates a new A worker that cannot reuse a retired A
   worker or adopt B. A's new task captures before its target returns to zero;
   B continues to progress with its original execution throughout.
8. Wrong-scope canonical task, attempt and artifact operations return HTTP 409.
   Each task proves its own source and synthetic secret while excluding the
   sibling source, secret, host checkout, private installation, Docker socket
   and host control credentials. Actual captured log markers must confirm these
   assertions. Artifact bytes must match their canonical SHA-256 identifiers,
   and the approved host sources must remain unchanged.
9. After B also captures and shared reservations reach zero, the single service
   pair restarts from retained journals. Both exact canonical snapshots and
   all captured artifact bytes must survive. Sealed configuration, credentials
   and retained enrollment fields stay unchanged. Discovery retains disjoint
   scoped worker histories, one container per worker, on the same Docker Engine.

The maximum concurrent root reservation count is three. Commands retain the
existing 240-second private fixture barrier, 270-second canonical attempt budget
and 300-second runtime ceiling. The barriers only coordinate test commands;
they never fabricate readiness, lifecycle responses, capture or progress. The
report includes elapsed times to observed predicates without a performance
threshold or wall-clock fairness claim.

## Evidence and boundaries

The private work directory retains source checkouts, generated credentials,
service logs and durable journals for diagnosis. CI uploads only `report.json`
and bounded, credential-redacted `diagnostics.json`, retained for 14 days. The
report records exact component revisions, image/Engine identity, runtime
versions, scope-denial responses, actual task and artifact IDs, each held-phase
progress counter and durable identity witness, the pending drain and final
reservation count. Secret values are never evidence fields.

`tests/test_shared_isolation_trial.py` uses deterministic malformed plans and
mount records to ensure the oracle rejects hidden foreign scopes, reused
workers, incorrect totals and unexpected mounts. A real SQLite `Store` and
`Controller.plan()` regression checks reserved, dispatched and uncertain
launches before the runtime has supplied a worker identity. Those real empty
identity markers remain counted only with their exact pending launch state.
The reused isolation tests
cover progress freshness, exact journal identity and the separate terminal
capture/candidate publication barrier. These tests validate the test's failure
conditions; they do not run Docker or qualify runtime isolation.

The supported boundary is trusted services sharing one host. This does not
establish protection from a hostile service with Docker authority, recovery
from a shared outage during active work, a 100-repository workload, a scheduling
fairness bound, simultaneous ingress delivery, multiple deployments behind one API, live provider behavior,
independent semantic review or actual forge publication. Those outcomes remain
separate acceptance evidence in ZPM #3.
