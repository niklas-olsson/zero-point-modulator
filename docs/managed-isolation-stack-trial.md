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
| Two repositories in one generated installation | Not established; the preparer accepts one binding/source/profile |
| One controller managing two repositories | Not exercised; this trial uses one controller per deployment |
| Two deployments behind one OpenJules API | Not supported by this trial; each OpenJules service has its own authority endpoint |
| Mutually hostile host services with Docker access | Not established; the trusted services have host-control authority |
| Live model behavior, independent review or forge publication | Separate acceptance gates |

OpenJules service configuration can describe several repository profiles and
Polyphony can authorize several repositories within one deployment. Those
component capabilities do not turn the generated single-repository bundle into
a multi-repository installation. The report names the tested topology explicitly;
it must not close the distinct installation and shared-API gaps in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).

## Exact candidate set

| Component | Revision |
| --- | --- |
| OpenJules | `7ca211ebb01d041827df5e480ad65c30cf7ba9ee`, initial child outcomes and durable control replay candidate |
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
   reservation; reconnect recovers A's original worker. B's command continues
   advancing its progress counter during that outage and recovery. Each held A
   phase takes a fresh counter baseline and requires a later increase; fresh
   worker HTTP observations before and after must retain B's exact scope,
   open admission and original active attempt.
4. A starts its own canonical task, scales `1 → 2 → 1`, then removes enrollment.
   Removal closes admission and retains A's active root until completion and
   capture. B keeps its exact target, worker, task and attempt, with open admission
   and an advancing command throughout these changes.
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

The local unit suite includes negative checks that reject replacement workers,
unchanged counts hiding changed scopes or targets, replaced/terminated attempts,
stale progress that predates the current held phase, closed admission hidden by
cached controller state, and credentials crossing diagnostic log-tail boundaries. These checks validate
the evidence assertions and redaction only; they do not run Docker.

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
Readiness timings end when the harness observes the required predicate; polling
and sequential observation are included. These are observations from this
controlled installation trial, not performance thresholds or a benchmark for
child startup, warm children, model response time or production throughput.

The controlled repositories establish actual Git/source/export behavior. They do
not establish access to a private production repository or a live model account.
No such external credentials are necessary to run this isolation trial.
