# Two installed deployments on one Docker Engine

This trial prepares and bootstraps two private installations through the existing
single-host installer. Each has its own ZPM controller, OpenJules API, Polyphony
authority, source repository, approved profile, ports, credentials and journals.
Their workers run simultaneously on the same real Docker Engine.

The trial uses controlled Python commands and synthetic credentials. It makes no
model-provider or forge request. The commands verify their repository and
credential boundaries, write a small change, and expose a bounded private
progress counter. The trusted harness releases each command's fixture barrier
through Docker as the task UID. It does not supply lifecycle receipts, change
runtime admission, or replace canonical execution and capture.

## Qualification boundary

| Topology or outcome | What this trial establishes when it passes |
| --- | --- |
| Two generated single-repository deployments sharing Docker | Actual installation, API scope separation, independent capacity and recovery, isolated task inputs and canonical capture |
| Two repositories in one generated installation | Not exercised; this harness prepares two separate schema-1 bundles |
| One controller managing two repositories | Not exercised; this trial uses one controller per deployment |
| Two deployments behind one OpenJules API | Not supported by this trial; each OpenJules service has its own authority endpoint |
| Mutually hostile host services with Docker access | Not established; the trusted services have host-control authority |
| Live model behavior, independent review or forge publication | Separate acceptance gates |

The [schema-2 installer](single-host-installation.md) can generate multiple
repository profiles and canonical policies within one deployment and service
pair. This harness still prepares separate schema-1 bundles. Its evidence does
not qualify shared-service runtime isolation; the separate
[two-repository shared-service trial](managed-multi-repository-stack-trial.md)
requires its own actual Docker result. This report names its tested topology
explicitly and cannot supply the shared-service acceptance evidence in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

## Exact candidate set

| Component | Revision |
| --- | --- |
| OpenJules | `e50d501cc81975d25e367be3ba1d6de9169207c1`, merged scoped unsupported-capability diagnostics and managed-context runtime |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66`, initial outcome authority candidate |
| ZPM | The workflow's checked-out revision, recorded in the report |

The new job uses the same companion pins as every other installed-stack job.
Update the workflow and all trial guides together when selecting a different
candidate. A new test, local assertion check or component CI result does not
qualify this topology. Require the actual Docker job and all existing gates to
pass on the delivery revision, and record their URLs in the integrated issue.

## Run on a supported Linux Docker host

Use clean sibling application checkouts at exact revisions, Python 3.11 or newer,
and the host prerequisites in the
[installation guide](single-host-installation.md). Build the existing small
command-trial image once and pass its immutable image ID:

```sh
docker build --iidfile /absolute/private/isolation-image-id \
  - < tests/contracts/Dockerfile.managed-trial
python tests/contracts/check_isolation_stack.py \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /absolute/private/isolation-image-id)" \
  --work-dir /absolute/private/managed-isolation-trial
```

The work directory must be new. The harness creates two actual Git repositories,
then independently prepares and bootstraps both bundles. Each generated manager
starts at zero capacity and must pass its doctor check. Subsequent outage and
restart checks reuse the exact generated configuration, service commands and
installed interpreters. No service map or alternate installation schema is used.

The CI job has a 35-minute limit. Each bundle is bootstrapped once, the test image
is shared, and no model inference is billed. At most three workers are requested
concurrently: two in A and one in B. Private command barriers have a four-minute
deadline inside the existing managed runtime deadline. Failed trials retain
state and Docker evidence for diagnosis instead of deleting unresolved workers.

## Required assertions

1. Both generated installations pass actual scoped doctor checks. Their initial
   Docker containers are simultaneously running, with distinct workspace volumes,
   worker identities, repositories, profiles and generated credentials.
2. Both APIs reject sibling credentials. Valid local credentials cannot discover
   foreign scopes, observe a sibling worker, or drain or stop it. Scoped ingress
   cannot create a task in the sibling deployment.
3. B starts one canonical task on its original worker. Its command verifies only
   B's source and synthetic credential are present. A runtime outage retains A's
   reservation; reconnect recovers A's original idle worker. B's command continues
   advancing during that outage and recovery. Each held A phase takes a fresh
   counter baseline and requires a later increase. Fresh HTTP, durable journal and
   actual container witnesses bracket that interval as described below.
4. While B remains active, A scales idle capacity `1 → 2 → 1`.
   The survivor must belong to A's verified pair and exclude B; either idle A
   incarnation may retire. A then starts its own canonical task on that survivor
   and removes enrollment. Removal closes admission while its original root
   continues under the exact owned pending drain intent. The harness observes
   fresh A progress before explicit release, then requires termination, canonical
   capture and released capacity. B keeps its exact target, worker, task and
   attempt and advances throughout these changes.
5. A reaches zero reservations, stops and restarts all three services, preserving
   removed state, credentials, canonical records and artifact bytes. Explicit
   re-enrollment creates a fresh owned worker and runs another task while B's
   original task continues.
6. Each real command verifies that the sibling repository, control credentials,
   host source metadata and Docker socket are absent. Synthetic credential values
   are compared in memory; only success markers reach the captured log. Each
   authority retains its own content-hashed patch, log and manifest and leaves
   acceptance awaiting review.
7. Valid local runtime credentials cannot claim foreign tasks, use foreign
   attempts or read the sibling's canonical artifacts. Final runtime histories
   have disjoint identities and one Docker container per worker. Both deployments
   finish with zero unresolved reservations on the original engine.

### Interpreting an active command's observation

The pinned OpenJules command-only runtime holds its backend execution lock until
the root completes. During that interval, a fresh lifecycle observation may be
`unknown` with the exact reason `runtime operation is in progress` and closed
admission. This trial records that conservative classification explicitly. It
does not call unknown capacity healthy or interpret closed admission as open.
An unrelated unknown reason, a missing active attempt or terminal evidence fails
the trial.

B is observed before A's first outage and around every subsequent progress
interval. Each sample requires its original HTTP scope, worker and active root;
an unchanged canonical task and attempt; no pending controller operation; and
the same actual running container. A missing controller pending field also fails
the witness, so an undelivered drain or stop cannot hide behind absent backend
history. Read-only SQLite connections use `mode=ro` and observe live WAL data. They require
the original lifecycle and runtime specification, profile, container and Docker
exec binding, with no termination, capture, cancellation or retirement. B's
durable draining flag must stay zero and **all** drain/stop records for its worker
must be absent, including completed or foreign-scoped records. These monotonic
fences detect an accepted control even when its task would temporarily continue.

A's active removal has a different witness. Its enrollment must be disabled with
effective target zero, its requested target retained and its original reservation
still occupied. The HTTP observation must report the exact pending drain plus
execution-lock reason, closed admission and the original active root. Its
lifecycle journal must retain draining intent and exactly one scoped pending
drain operation with the original worker and payload. The request key is frozen
at the first durable lifecycle observation and must remain unchanged. ZPM has no
durable action history after acknowledgment; when its pending field is present
it must match that key, and a cleared pending field does not prove backend
delivery. The runtime must remain bound to the original unretired execution and
running container until explicit fixture release. The report labels this as
pending intent, with backend delivery unconfirmed. Actual capture and reservation
release are required afterward.

The unit suite includes negative checks for replacement workers and executions,
changed scopes or targets, generic unknown observations, stale progress from a
previous phase, wrong or completed controls, retired/captured outcomes, changed
controller keys, container changes and credentials crossing diagnostic log-tail
boundaries. SQLite fixtures prove reads see a committed live WAL, reject writes
and do not create missing journals. Their setup uses the actual ZPM bundle
generator and controller schema; companion journals remain synthetic. Journal
paths come from the generated OpenJules configuration, including the managed
service's additional runtime subdirectory. These tests validate evidence
assertions and redaction; they do not run Docker.

Canonical terminal capture and candidate publication are separate operations.
The capture barrier reads one fresh scoped snapshot per poll and uses the same
45-second deadline for both. A valid terminal attempt may precede its candidate;
the barrier retains its exact attempt identity, runtime binding, launch fence and
artifact hashes while waiting. An existing malformed, duplicate or mismatched
candidate fails immediately. Completion requires that exact candidate to be the
task's current review request with status `awaiting_review`, without admission.
Artifact byte hashes, credential exclusion, expected command output and unchanged
approved source are still checked afterward. Timeout diagnostics distinguish
terminal capture from candidate publication without exporting snapshot contents.

The deterministic capture tests drive the real polling loop with a controlled
clock and separately delivered snapshots. They cover delayed publication, the
unchanged deadline, immediate rejection of invalid candidates and changes to an
already observed capture. They also exercise the installation's subsequent
artifact checks. They do not establish HTTP scheduling or actual runtime success.

### Retained qualification and source-review evidence

The first [PR run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43029634925/job/130553309385)
at head `98e38e31ee52eb2a55ba69f1c4a283e020e9a296` exposed the harness reading and
releasing its private barrier outside the command's actual checkout directory.
The path correction retained the deadlines and progress requirement.

The next [PR run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43030761022/job/130557147817)
tested synthetic merge `b0982d07dbf377e47ffe1a69c88a3eaa5db80fa4` for head
`42518e0f7b6a0d51a7530478067073e405b08d89`. It passed preparation, scoped doctor,
simultaneous distinct workers and API denial checks, then rejected B's admission
observation. The [push run](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43030753095/job/130557111711)
failed at the same assertion. Source review and a separate deterministic runtime
fixture reproduced the documented execution-lock uncertainty. This led to the
strict witnesses and idle-resize ordering above, without changing production
locking or classifying generic uncertainty as success.

Independent source review also rejected unreferenced tree
`f777855eff8f44f6e36c0674e1b931c1c3462ac5`: its runtime journal path omitted the
managed service's additional runtime directory, and its B witness omitted the
controller's pending operation. The replacement derives journal locations from
generated configuration, uses the actual generator in its fixture, and rejects
pending or missing controller evidence. The rejected tree was never committed
or published to a branch, and no test execution was claimed for it.

Those failed runs used the companion revisions listed above. Their partial
evidence did not qualify the remaining isolation criteria. The corrected
strict-witness head `f17f5b2e9978dea2860840a56c32359e0d217f41` subsequently
[passed the actual isolation job](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43038690070/job/130584589634)
at synthetic merge `64d1e63e0aec37d3cb2e673d50741c5d23bd4755`.

A later schema-2 installer head, `0ed9cb6073eee9f3cfb01a3b37ee0c86aed187d3`,
had a split result. All seven [PR jobs](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43042193464)
passed, including the [isolation job](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43042193464/job/130596918671)
at synthetic merge `06c481f0ffe6d64f8fdbaf264f047f409055c1f3`. The same head's
[push isolation job](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43042176907/job/130596874632)
failed with `candidate changed its source base or capture`; its other six jobs
passed. Diagnostics artifact `13866405863` retains the failure and exact component
revisions, but it does not contain the observed candidate count or values. The
historical cause therefore remains unproven.

Pinned OpenJules
[`_publish_capture`](https://github.com/niklas-olsson/OpenJules/blob/7ca211ebb01d041827df5e480ad65c30cf7ba9ee/src/worker/managed_dispatch.py)
records terminal artifacts before a separate candidate request. Pinned Polyphony
[`managed_observe` and `managed_candidate`](https://github.com/niklas-olsson/keystone-polyphony/blob/653c0a71a921c0d23b8e0171277524125e929e66/src/core/collaboration/managed_runtime.py)
preserve those separate calls. The earlier harness waited only for terminal
artifacts, then immediately required a candidate. The complete-capture barrier
above corrects that demonstrated observation gap, without asserting a confirmed
historical product failure or changing either component. Its exact published
revision must pass all existing CI gates; source review and unexecuted fixtures
are not passing results.

## Retained evidence

The private directory contains both bundles, their synthetic credentials,
journals, service logs and captures. Do not upload it wholesale. Only the root
`report.json` and `diagnostics.json` are CI artifacts. The report identifies the
topology, exact component and source revisions, image and engine IDs, denied
operations, owned worker histories and canonical artifact hashes. It contains no
credential values. Failure diagnostics aggregate bounded service-log tails after
redacting every generated credential and synthetic secret from both installations.

The report also records monotonic elapsed seconds for the existing preparation,
bootstrap, doctor, zero-to-ready, A scaling/drain/recovery and complete-trial
operations, together with the host Git, Docker and service Python versions.
It records each before/after HTTP classification, the fresh progress increment,
aggregate durable control fences and container identity, plus A's original pending
drain key. It never exports raw journal rows or Docker environment data.
Readiness timings end when the harness observes the required predicate; polling
and sequential observation are included. These are observations from this
controlled installation trial, not performance thresholds or a benchmark for
child startup, warm children, model response time or production throughput.

The controlled repositories establish actual Git/source/export behavior. They do
not establish access to a private production repository or a live model account.
No such external credentials are necessary to run this isolation trial.
