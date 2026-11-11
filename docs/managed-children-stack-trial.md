# Managed child installation trial

This trial installs ZPM, Keystone Polyphony and OpenJules from their real source
checkouts, then runs a root agent and two child agents in one Docker worker.
Polyphony grants and accounts for the children. OpenJules launches their actual
processes under separate Unix identities. ZPM continues to reserve one top-level
worker. The root retrieves both provisional patches, applies their actual
contents, runs the combined repository tests and produces a captured candidate
that awaits independent review.

The model endpoint is a deterministic HTTP fixture on a freshly allocated
localhost port. No provider account, paid inference, production repository or
live GitHub operation is used. A passing result qualifies this installed runtime
path, including canonical child grants, isolation, composition and capture. It
does not establish model reasoning quality, provider compatibility or permission
to publish or merge the resulting candidate.

## Components and prerequisites

The separate `managed-children-stack` CI job uses these immutable revisions:

| Component | Revision |
| --- | --- |
| OpenJules | `cf079b90ee081e84b29b6179ad1aaebdb8156365`, merged final-composition and publication implementation |
| Keystone Polyphony | `473df5a0406accfe5cb550877e7b28a602cb116c`, merged child grants and durable conversation baseline |
| ZPM | The checked-out workflow candidate, recorded in the report |

The pinned OpenJules revision passed
[its post-merge CI](https://github.com/niklas-olsson/OpenJules/actions/runs/42987159785).
Its Git tree matches the previously qualified PR12 head
`8a40e04478ef25c128364b6c989a77735cca21eb`. This historical component evidence
does not qualify the changed ZPM revision: all required ZPM jobs must pass for
the current candidate set before it is promoted. Record the exact revisions
and successful CI run in [ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).
The publication CLI is present in this installation; this trial does not invoke
forge publication. Update all workflow pins and corresponding trial guides
together when qualifying another component revision. The installed child trial
remains a separate required result; a component's passing tests alone do not
qualify this bundle.

Use clean sibling checkouts, Python 3.11 or newer, Linux procfs and a local
rootful Docker Engine with the host and guest UID mapping used by Unix peer
credentials. Rootless Docker and user namespace remapping require separate
qualification. Keep peer credential checks enabled. The trusted OpenJules host
service owns Docker control; the root and children do not receive that socket.

The existing [controlled command trial](managed-stack-trial.md) and
[model-backed root trial](managed-model-stack-trial.md) use the same component
pins and retain their independent CI gates.

## Build and run

Build the production managed-agent Dockerfile from the approved OpenJules
checkout. It includes both the root runner with child controls and the child
runner with coding tools. The CI fixture uses a mutable Python tag as a build
input and records the resulting immutable image ID. Production builds should
use an operator-approved base digest and include the repository's dependencies;
tasks cannot fetch dependencies through the disabled container network.

```sh
docker build --build-arg PYTHON_BASE=python:3.12-slim \
  --iidfile /absolute/private/managed-children-image-id \
  -f /absolute/checkouts/OpenJules/docker/Dockerfile.managed-agent \
  /absolute/checkouts/OpenJules

python tests/contracts/check_children_stack.py \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /absolute/private/managed-children-image-id)" \
  --work-dir /absolute/private/managed-children-stack-trial
```

The work directory must be new and outside all three checkouts. The trial
creates a disposable Git repository containing two files, combined tests and
repository instructions. Both files initially contain `old`. The combined tests
require both contributions, so applying only one child patch cannot pass them.
Ignored host-only source data is deliberately present and must stay outside the
worker.

The trial calls the actual installer to generate a bundle, verifies that bundle,
bootstraps its three service environments, starts the generated manager and
checks scoped readiness through the generated doctor. Its explicit model policy
uses a synthetic credential. The manager maps that credential into the trusted
OpenJules process; neither guest environments nor command arguments contain it.
There is no option to choose a remote model endpoint for this test.

## Bounded execution and accounting

The installed trial uses the following deliberately explicit allowances:

| Allowance | Trial value |
| --- | --- |
| Desired ZPM workers | Zero, one, zero during active children, one replacement, finally zero |
| Deployment worker ceiling | One |
| Root attempts | One |
| Root attempt allocation | 180 seconds |
| Lifetime child grants for the root | Two |
| Allocation per child | 90 seconds |
| Canonical task time budget | 360 seconds, covering root and both children |
| Root model turns | Six |
| Child model turns | Two each |
| Shared inference reservations | Ten calls, 1,024 output tokens reserved per call |
| Guest identities | Root UID 65532; child UIDs 60000 and 60001 |

Six deterministic root replies spawn the left child, spawn the right child,
retrieve each patch, apply both returned patches and verify their combined
effect, then finish. Each child receives two replies: write its assigned file,
then finish. The child control client retries a result that is not ready inside
its bounded control call. Model polling is not used and extra calls fail the
trial.

After both spawn replies, the fixture pauses the third root model response for
at most 20 seconds. While the response is paused, the harness reads the real
Polyphony child API and OpenJules worker API, then checks ZPM's journal. It
requires two live canonical grants, three active executions under the same
worker and exactly one top-level target and reservation. Before draining, it
also waits for both exact child IDs to have a nonempty Docker exec ID, dispatched
exec start and a running or exec-returned phase in the private runtime journal.
This reads launch metadata only. A grant or admitted lifecycle reference can
precede actual exec launch when the worker lock is busy, so those references
alone do not establish the intended active-child scenario.

It then sets ZPM's desired target to zero without waiting for reservation release. While the root
response remains paused, it requires the same worker to enter draining, close
worker admission and receive Polyphony's acknowledgement that child admission
is closed. The root and both children must retain their execution references,
existing canonical permissions and the single worker reservation. The fixture
releases the root after those observations. The response deadline is 30 seconds
and child deadlines are 90 seconds.

This is a graceful drain. Existing work completes under its current deadlines;
the target change does not cancel it. While waiting for completion, any observed
zero reservation must already have final canonical root and child receipts.
After verifying their captures, the trial waits for zero reservations before
requesting an idle replacement worker and scaling that replacement down again.

An exec returning does not cancel its watchdog or prove that every descendant
has stopped. The runtime retains child execution reservations until whole-worker
termination and final capture. Child deadlines remain armed; they are not
extended by model or control retries.

## Required evidence

The trial fails unless all of these assertions pass through the actual service
and container boundaries:

1. The generated installation has matching Polyphony and OpenJules child
   limits. Readiness and an idle worker make no inference calls. Replayed ingress
   delivery returns the same task, and foreign repository ingress is denied.
2. Two child grants retain the same root attempt, worker, task and approved Git
   base. Their allocations consume the existing canonical task budget. Children
   do not create another ZPM worker reservation. An explicit target-zero change
   during active children closes admission and drains the worker while retaining
   all three execution references and its one reservation. Existing root and
   child permissions remain valid until completion or their existing deadlines.
3. The child runners receive coding tools only; the root receives child
   controls as well. The provider fixture validates canonical requirements,
   repository instructions, preceding tool identities and the exact ten-call
   sequence. The private broker journal confirms ten completed reservations
   sharing the root attempt, including four child calls.
4. Actual guest verification confirms distinct child UIDs, denial of root
   workspace access, and denial of root model and control socket authority.
   Root verification confirms denial of child workspace access and both child
   model sockets. Root and child verification checks for absent provider and
   control credentials, host source metadata and the Docker socket.
5. Child result pages are explicitly provisional. The fixture checks their
   child identities, byte bounds, complete patch contents and page and artifact
   hashes. The root applies those returned bytes and runs the combined tests.
   The fixture checks the composition command's actual tool result before its
   finish reply. The captured root log records the command's tool event at turn
   five and the configured verification's successful exit and boundary marker.
   Ordinary tool output is intentionally omitted from that log.
6. Polyphony retains all three content-hashed child captures for both children:
   patch, log and manifest. Their final manifests identify their distinct UIDs,
   dispatched Docker execs, immutable grants, armed deadlines and the same
   fenced archive as the root. Their final patches match the provisional
   patches actually composed by the root. Canonical child turns total four.
7. The root's final attempt and candidate bind both final child capture
   lineages. Child admission is closed. The candidate remains awaiting review
   and no admission record is created. These checks require final child
   receipts before root candidate publication through the canonical authority.
8. An independent checkout applies the captured root patch to the exact
   approved base, changes only the two assigned files and passes both combined
   assertions. The original source, tests and repository instructions remain
   unchanged.
9. The drained root completes composition and capture normally. Its reservation
   reaches zero after final root and child receipts, then target one creates an
   idle replacement.
   Docker inspection confirms exactly one incarnation for the completed worker,
   now stopped, with disabled networking, all capabilities dropped, a read-only
   root and its exact worker-owned read-only model/control directory. Captures
   and Docker metadata contain no generated secret values.
10. Target zero releases every reservation. Restarting the whole generated
    installation preserves the root candidate, both child records, their
    captures and the ten inference reservations. No work or inference is
    replayed.

The success report records exact component revisions, the image ID, canonical
identities, grant and artifact hashes, UID and allowance counts, the observed
one-worker/two-child accounting and the active-child target-zero drain. It contains no raw model conversations, private
instructions, provider credentials or workspace archives.

## Qualification limits and retained state

This trial exercises graceful scale-to-zero while children are active. It does
not exercise forced cancellation during that work. Cancellation and watchdog
behavior have separate OpenJules tests; they must not be represented as an
installed-stack forced-cancellation result.

This trial also does not exercise distributed peer discovery, dynamic recursive
delegation, live providers, independent semantic review, forge publication or
merge authorization. Ordinary Polyphony conversation and managed child control
remain separate paths. A local fixture or syntax check cannot substitute for the
Docker gate. Missing Docker, Unix sockets, service readiness, child execution or
canonical capture fails the job instead of skipping it.

The work directory retains private installation files, service logs, journals,
artifacts and recovery evidence on success and failure. Do not upload it
wholesale or discard unresolved runtime resources. The disposable CI host is
removed after the job. CI uploads only the credential-free `report.json` and,
on failure, bounded token-redacted `diagnostics.json`. All generated control and
provider tokens are included in redaction.
