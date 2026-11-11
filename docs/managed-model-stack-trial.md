# Model-backed root installation trial

This trial installs all three actual services and runs OpenJules' real managed
coding agent in Docker. Model replies come from a deterministic localhost HTTP
fixture created by the trial. The agent reads the canonical task requirements
and repository instructions, reads a source file, changes it through its coding
tools, runs tests and submits a captured candidate for review.

No provider account, live model credential or paid inference is used. A passing
result qualifies this installation and protocol path. It does not qualify a
provider's API behavior, model reasoning quality, delegated children, semantic
review, forge publication or merge authorization.

## Components and host

The separate `managed-model-stack` CI job pins these revisions:

| Component | Revision |
| --- | --- |
| OpenJules | `c85867c9a110f6c3ea00a53e6a721a15ce15782d`, merged initial child outcomes and durable control replay |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66`, initial child outcome authority candidate over the merged grants and conversation baseline |
| ZPM | The checked-out workflow candidate, recorded in the report |

All installation gates use this same candidate set. The changed combination is
unqualified until every required ZPM CI job passes on the exact candidate
revision. Record the successful run and exact revisions in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3); local
HTTP fixtures and historical component tests do not replace these Docker runs.

Use clean sibling checkouts at these revisions, Python 3.11 or newer, Linux
procfs and a local rootful Docker Engine. This slice requires the host and guest
task UID mapping used by Unix peer credentials. Rootless Docker and user
namespace remapping require separate qualification; the peer check must remain
enabled. The trusted OpenJules service controls the local Docker Engine.

The existing [command-based trial](managed-stack-trial.md) remains a separate CI
gate using the same component pins. It continues to exercise API crashes, watchdog
termination, uncertain reservations, capture recovery and capacity changes
through zero, two, three, one and zero workers.

## Build and run

Build the production managed-agent Dockerfile from the approved OpenJules
checkout. The Dockerfile copies its stdlib runner into the image. The CI example
uses a mutable Python tag only as a fixture build input; runtime identity is the
resulting immutable image ID. Use an approved base digest for production builds.

```sh
docker build --build-arg PYTHON_BASE=python:3.12-slim \
  --iidfile /absolute/private/managed-model-image-id \
  -f /absolute/checkouts/OpenJules/docker/Dockerfile.managed-agent \
  /absolute/checkouts/OpenJules

python tests/contracts/check_model_stack.py \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /absolute/private/managed-model-image-id)" \
  --work-dir /absolute/private/managed-model-stack-trial
```

The work directory must be new and outside the application checkouts. The trial
generates a disposable repository, distinct service credentials and a synthetic
provider token. It supplies that token to the generated manager under an explicit
source reference. The installer maps it to an OpenJules-only alias. No credential
is put into the agent image, command argv or guest environment.

The fixture endpoint is always freshly allocated on `127.0.0.1`; there is no
argument that selects a remote model. It validates the expected model, tool
schema and output limit, requires the exact canonical requirements and root
repository instructions, and rejects extra calls or a failed preceding tool.
Four deterministic replies direct read, write, test and finish operations.
The agent and broker run their production implementations.

## Acceptance evidence

The test fails unless all of the following occur through the real service
boundaries:

1. `prepare`, generated `bootstrap`, generated `start` and scoped `doctor` work
   with the optional model policy. Readiness and an idle worker make no model
   calls.
2. Scoped ingress creates one canonical task. Replayed delivery returns that
   task and another repository's request is denied.
3. The real agent uses its worker's Unix socket to make exactly four authorized
   fixture requests. It receives canonical requirements and root `AGENTS.md`,
   changes `answer.py` and passes the repository tests.
4. Polyphony retains a fenced terminal attempt, content-hashed patch, log and
   manifest, plus a candidate awaiting review. The task is not self-accepted.
5. A separate checkout applies the canonical patch to the exact approved base
   and independently passes the repository tests. The original source remains
   unchanged.
6. The completed worker is retired and replaced. Docker inspection confirms
   its stopped state, disabled networking, read-only root filesystem and exact
   worker-owned read-only model directory. Guest verification checks that the
   provider and control credentials, host source metadata and Docker socket are
   absent. Captured artifacts and Docker metadata contain no generated secrets.
7. Target zero releases all reservations. Restarting the complete generated
   installation preserves canonical evidence without starting work or replaying
   model inference.

The success report records the three revisions, immutable image ID, canonical
task and candidate IDs, artifact hashes, fixture call count and final reservation
count. These are evidence for the controlled trial, not a semantic review of
arbitrary model output or a financial spend guarantee.

## Retained state and diagnostics

The trial retains its private installation, journals, service logs and Docker
tombstones on both success and failure. Do not upload that directory wholesale
or delete unresolved runtime resources. On a disposable CI runner the host is
discarded after the job.

CI uploads only `report.json` and, on failure, bounded token-redacted
`diagnostics.json`. The provider token and all generated service credentials
are included in redaction. Raw conversations, configuration, secrets, databases
and workspace archives are not exported.
