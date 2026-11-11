# Two repositories in one installed service pair

This trial prepares a fresh schema-2 installation with two repositories, one ZPM
controller, one OpenJules API and one Polyphony authority. Both workers use the
same real Docker Engine. The deployment ceiling is two top-level reservations.

The scene uses controlled Python commands and synthetic credentials, with no
model-provider or forge requests. The repositories have distinct source
checkouts, approved profiles, guest credential references and private workspace
volumes. Each command verifies its own source and credential, rejects the other
repository's credential, host control-token names and every `ZPM_REPOSITORY_`
alias, and checks that private installation paths are absent. It writes a small
change and advances a private counter until the trusted harness releases its
fixture barrier through Docker as the task UID.

The harness does not fabricate execution receipts, change runtime admission or
replace canonical execution and capture. A passing report establishes only the
topology and observations below.

## Exact candidate set

| Component | Revision used by the job |
| --- | --- |
| OpenJules | `7ca211ebb01d041827df5e480ad65c30cf7ba9ee` |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66` |
| ZPM | The workflow's checked-out commit, recorded in the report |
| Controlled image | The supplied immutable local image ID, recorded in the report |

These are the same companion candidates as the other installed-stack jobs.
They are an explicit integration revision set, separate from default-branch or
merge status. A component test result does not qualify a different set.
Installed service Python versions are recorded; dependency-resolution files
remain in the private bundle. This is not a complete dependency lock or a
reproducible-image-build claim.

## Scene and evidence

| Phase | Held condition | Required observations |
| --- | --- | --- |
| Installation | A0, B0, one service pair | Generated manager starts, authenticated doctor succeeds, no worker allocation |
| Concurrent workers | A1 idle, B1 executing | Exact A/B rows, distinct containers and volumes, correct scoped credentials |
| A source unavailable | A target 1, A reservations 0, B reservations 1 | Exact readiness failure with genuinely free capacity, two completed controller passes, unchanged retained A allocation history, fresh B progress |
| Global ceiling | A1 idle, B1 executing, A target 2, global maximum 2 | Exactly two reservations, A shortfall 1 and deployment-ceiling reason, no extra historical allocation, actual doctor output |
| A pause and resume | A remains idle through capacity changes | Enabled A retains target 1 while paused at effective zero, then resumes unpaused with a fresh incarnation; B retains its execution |
| A removal | A's original root remains active | Disabled A admission, one retained owned drain key and exact execution binding until real capture; B remains uncontrolled |
| B completion | B target zero is committed while its root remains held | Release/capture without a desired-one replacement race; global reservations reach zero |
| Recovery | Both roots captured, A source restored | Full restart retains credentials, removed A, zero-target B, exact scoped records and artifact bytes |

Every use of a one-repository witness follows validation of the complete shared
plan. The plan must contain A and B exactly once, the correct deployment and
configured maximum, consistent counters, no shared worker identity, and a global
reservation total equal to the sum of the two rows. Filtering cannot conceal
extra enrollment or workers.

### Completed controller decisions

The harness's internal `plan()` uses the existing controller with the configured
ceiling from the sealed provider file. It reads local journal observations.
It does not prove that reconciliation evaluated a new target or that runtime
readiness is fresh.

The source-failure and quota phases therefore use a normal product boundary:

1. Confirm the watch controller is running, then stop and reap only that process.
   OpenJules, Polyphony and B's original command remain running.
2. Change the target through the installed CLI.
3. Run the exact generated installed controller command with only its final
   `--watch` argument removed. Keep the same working directory, provider file
   and generated environment.
4. Require successful completion before parsing the complete private JSON log.
   The ordinary CLI has executed `Controller.tick()` under its controller lock.
5. Compare every retained A launch key and lifecycle/runtime identity across a
   fresh B progress window and a second completed controller pass.
6. Resume the exact watch command after the held window succeeds.

There is never more than one controller operating the journal. A test hook,
journal mutation, arbitrary sleep or counter increment does not stand in for a
completed allocation decision. Failed and released history remains visible, so
briefly creating and retiring a worker cannot be hidden by final live counts.

Doctor also runs during the restored-source quota phase. It checks authenticated
capabilities and shows the configured ceiling with journaled capacity. Its
`ready` flag is not a separate target-convergence receipt.

### Source failure boundary

The harness first retires idle A, leaving only B's one reservation. It renames
A's approved fixture checkout and requests one A worker. The pinned OpenJules
source preparation checks the approved path before using a cached export, so the
scene exercises source readiness without changing runtime configuration or
deleting caches.

The direct A capability response must report
`approved source/base or local Docker image is unavailable`, while B's own
capabilities still succeed. ZPM separately normalizes this to
`repository_access unavailable` in each completed controller report. The
report records both meanings as `runtime_reason` and `controller_reason`.

The original path and exact Git revision are restored before whole-bundle
verification, doctor or restart. Those manager operations revalidate all approved
sources and fail closed while A is absent. This is evidence about an already
running stack. It does not establish cold restart during source failure or
survival of a shared API, authority, Docker or host outage.

### B's execution and A's active drain

Every B checkpoint takes a fresh counter baseline after entering the held phase
and requires a strictly larger value. Before and after that increment, the
harness checks B's original worker specification, profile fingerprint, container
and Docker exec IDs, canonical task/attempt, input digest, budget and deadline.
It reads the actual generated lifecycle and nested runtime journals without
creating replacement files.

B must have explicit `pending_operation: null`, no lifecycle drain or stop in
any scope or completion state, no runtime retirement or cancellation, and no
termination or capture fence. Its entire retained allocation history stays
unchanged. An `unknown` HTTP observation is accepted only for the specific
nonblocking execution-lock reason `runtime operation is in progress`. That
uncertainty is recorded rather than called healthy readiness.

A's removal freezes the first durable lifecycle drain request key. Its active
root remains in the original container and exec while pending drain closes
admission. An acknowledged controller intent may already be cleared; this is
not proof that backend drain completed. Releasing the fixture barrier is followed
by actual terminal capture and canonical candidate publication.

B's uncontrolled-peer interval ends before its own target-zero command.
Its later intentional drain is not described as cross-repository interference.
No B progress claim spans the final shared-service shutdown.

### Authentication and canonical visibility

Repository ingress and reviewer credentials first succeed on their own managed
capabilities and snapshot routes. The sibling scope must then fail with 403.
An accepted ingress enrollment is retried with only the repository changed and
must also fail with 403.

Controller and runtime credentials intentionally cover both repositories. Their
successful access to both scopes is expected. Resource tests instead change only
the requested scope: own worker reads succeed, sibling-scope reads fail with 404;
an active own attempt permission succeeds, the other allowed scope fails with
409; each exact captured artifact is readable in its owning scope and fails
with 409 in the other. These controls do not rely on an already-active task
claim, expired budget or unrelated schema error.

Final scoped snapshots contain exactly the expected task and attempt per
repository. Fresh authorized clients retrieve unchanged records and captured
bytes after restart. Work remains `awaiting_review` with its canonical candidate
and no admission. Different reviewer tokens still identify the same operator;
they do not establish independent review, acceptance or publication.

## Run the trial

Use Linux, an accessible rootful Docker Engine, Python 3.11 or newer, Git and
clean checkouts at the intended revisions. The work directory must be new,
private and outside every source checkout.

```sh
docker build --iidfile /tmp/multi-repository-image.id - \
  < tests/contracts/Dockerfile.managed-trial

python tests/contracts/check_multi_repository_stack.py \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /tmp/multi-repository-image.id)" \
  --work-dir /absolute/private/managed-multi-repository-trial
```

Preparation, bootstrap, installed commands and service processes are real.
The command fails if Docker, a service, an authorization boundary or an evidence
predicate is unavailable. There is no fixture substitute for this runtime job.

The work directory contains synthetic secrets, journals, source fixtures,
dependency records, service logs and retained Docker identities. Keep it private.
Failure cleanup attempts to restore a successfully displaced source and always
stops/reaps services with bounded cleanup, including when restoration fails.
It refuses to replace an unexpected original path. Failed initial displacement
does not authorize a later restoration attempt. Cleanup does not delete
recoverable worker state or claim termination because a service stopped.

## CI and interpretation

The `managed-multi-repository-stack` job runs after the unit/package jobs,
using the same exact companion pins and existing controlled command image.
It uploads only `report.json` or bounded, credential-redacted
`diagnostics.json`.

The report contains topology and revisions, image and Engine identity, canonical
identifiers and artifact hashes, completed controller-pass counts, history
digests, source/quota evidence, scope statuses, fresh progress checkpoints and
observed timings. It contains no credential values, raw environment, source
contents or artifact bodies. Timings describe this controlled scene, not
throughput, fairness or production workload guarantees. No model policy or
inference broker is configured.

The deterministic oracle tests cover hidden or malformed topology, misleading
capacity/source predicates, transient retired allocations, committed SQLite WAL
rows, missing/foreign journals and cleanup after failed source restoration.
Their ZPM schema and generated paths come from the production writer with an
explicit synthetic private preparation marker. OpenJules rows are synthetic
reader fixtures. These tests establish assertion behavior, not runtime isolation.

The [two-installation trial](managed-isolation-stack-trial.md) remains a separate
topology with two controllers and service pairs sharing Docker. Neither trial
establishes mutually hostile host isolation, multiple deployments behind one
OpenJules API, live repository addition, schema migration, provider behavior,
independent review or forge publication. Record an exact passing run and revision
set in [ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3)
before closing an applicable acceptance criterion.
