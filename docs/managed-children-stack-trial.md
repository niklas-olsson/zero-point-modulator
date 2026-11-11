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
| OpenJules | `11842db8fc573b461121dc321a1537a9a148ad90`, merged owned cleanup, initial child outcomes and durable control replay |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66`, initial child outcome authority candidate over the merged grants and conversation baseline |
| ZPM | The checked-out workflow candidate, recorded in the report |

The paired initial-outcome changes are [OpenJules #16](https://github.com/niklas-olsson/OpenJules/pull/16)
and [Polyphony #122](https://github.com/niklas-olsson/keystone-polyphony/pull/122).
OpenJules #16 is merged; the Polyphony pin remains the authority candidate
from Polyphony #122. The merged OpenJules revision also contains
[durable control replay](https://github.com/niklas-olsson/OpenJules/pull/15).
The current pin adds [explicit owned cleanup](https://github.com/niklas-olsson/OpenJules/pull/17)
and [strict adapter and authentication checks](https://github.com/niklas-olsson/OpenJules/pull/18)
for the qualified creation transport. [Versioned persisted proof](https://github.com/niklas-olsson/OpenJules/pull/20)
keeps earlier unqualified creation markers ineligible after restart.
Its component Docker tests cover process death between removal and journal
acknowledgement with a fixture authority. This installed trial separately requires
cleanup against the real retained Polyphony authority before the set is qualified.
The previous integrated candidate could acknowledge a drain while its runtime
lock was busy, then fail to close canonical child admission. The unchanged
active-child trial exposed that race in
[this CI job](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43021172587/job/130524660321).
The new deterministic HTTP regression covers accepted-intent recovery, while
this Docker trial must still establish actual canonical closure and capture.
OpenJules' full repository CI and all required ZPM jobs must pass for the current
candidate set before promotion. Record the exact revisions
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

## Explicit initial outcomes

The same CI job also runs a fresh installation with `--initial-outcomes` and a
different work directory. This variant enables `outcome_protocol: "initial-v1"`
on the approved child policy. The default run continues to check omitted-policy
compatibility and two successfully contributed patches.

In the outcome variant the left child changes its file and explicitly finishes.
The right child reads its assigned file, then calls `report_outcome` with a
private decline. It leaves an empty patch. The root retrieves both provisional
patches, reads both typed reports, applies the left contribution and explicitly
completes the right change itself. Combined verification still requires both
files. The final record preserves the right child's refusal, separate from the
root's successful work and from runtime termination.

Because the root adds the right change itself, this variant is not compatible
with the existing child-only composition publisher. That publisher requires the
tree composed from child patches to equal the canonical root candidate and
would reject this variant with `candidate_tree_mismatch`. That is an inference
from the publisher contract; this trial does not invoke publication. The default two-patch trial
continues to exercise that composition shape. Outcome recovery does not relax
the publication boundary or force a declining child to accept the work.

Eight root calls and four child calls share exactly twelve inference
reservations and 12,288 reserved output tokens. Callback delivery, canonical
capture, reconnect and idle waiting add no model calls. The child allocation,
deadlines, one-worker capacity, active drain and whole-worker capture barriers
remain the same as in the default scenario.

The trial compares each provisional report with the exact final manifest and
the canonical child projection, including the original grant/instruction
binding, Unicode payload digest, logical initial-turn identity and final artifact
references. It checks that private summaries are absent from broad snapshots and
the shareable report. After every service restarts, a new authorized runtime
client retrieves identical child records and artifact bytes without re-executing
the children or inference. Local reports explicitly lack canonical commitment;
the final records remain `release_authorized: false`.

This is a deterministic initial-instruction demonstration. It does not prove
live-provider reasoning, independent semantic review, two rounds with a live
child, a managed public conversation bridge or automatic resumption of a dead
parent agent loop. The provider fixtures choose the completion and decline;
actual processes, UID-bound callbacks, service journals and captures execute
the protocol. The actual root process also attempts both children's outcome
sockets and must receive UID-based authorization denial.

## Bounded execution and accounting

The default scenario uses the following deliberately explicit allowances:

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

The default scenario fails unless all of these assertions pass through the actual service
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
11. While the owned bundle is stopped, the trial reopens only its original
    installed Polyphony authority on the same database, credentials, policy and
    port. The installed OpenJules host CLI cleans the original fully captured
    worker. Exactly its container and workspace volume disappear; the existing
    idle replacement's exact container and volume identities remain. The host
    archive, all six local logical journal snapshots and real canonical root/child
    records and artifact bytes remain unchanged, except for the intended runtime
    cleanup progress. That progress must exactly bind the original worker, spec,
    resources, archive and canonical root/child proof, with all completion flags
    true; missing, malformed or changed progress cannot qualify. A second CLI invocation returns the identical completed
    receipt. The temporary authority is stopped through its owned process handle
    even if cleanup or verification fails.
12. After the normal full-service restart, replaying the saved original ensure
    payload returns the same retired worker with no active executions. Neither
    resource is recreated, the non-target resources remain, retained journal
    history and capture bytes are unchanged, and target/reservations stay zero.
    Default and initial-outcome variants retain ten and twelve model calls.

The success report records exact component revisions, the image ID, canonical
identities, grant and artifact hashes, UID and allowance counts, the observed
one-worker/two-child accounting and the active-child target-zero drain. It contains no raw model conversations, private
instructions, provider credentials or workspace archives.
Its `owned_cleanup` section records the exact scoped completion result,
identical replay, retained archive hash/size, retained journal categories and
the original-ensure no-resurrection result. It exports no journal rows or host
capture paths.

## Explicit cleanup boundary

Cleanup uses OpenJules' existing host command with the generated private config
and installed runtime environment. It does not add a ZPM cleanup API. The
foreground bundle manager normally stops all three services together, so the
trial reopens only the same generated authority while OpenJules and the
controller remain stopped. It reuses the original environments and commands;
there is no additional bootstrap, authority database, principal or worker.

The supported first slice requires an executed, completely captured worker and
exactly one durable create dispatch and acknowledgement for both resources,
recorded through Docker SDK 7.2.0's stock Unix transport with retries and redirects
disabled. Idle workers, legacy rows and uncertain or retried creation remain
retained. Managed container and volume names remain permanently reserved after
cleanup, including after a lost delete response. Never recycle those names or
edit journals to make an ineligible worker qualify. Archives and receipts are
retained; this operation does not implement archive expiry or automatic collection.

This phase establishes installed CLI behavior against the real canonical
authority when its actual Docker run passes. It does not repeat the separately
qualified OpenJules subprocess-death experiment, prove arbitrary storage failure
recovery, invoke a live provider or publish a forge candidate.

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
