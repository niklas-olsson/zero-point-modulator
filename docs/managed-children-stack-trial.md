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
| OpenJules | `74429feda1f3991ce528678b74d6db5e3b3b250c`, merged cold clarification and public-discussion runtime |
| Keystone Polyphony | `0cf8b4772b04269505e3e1cd41324e41409b80f9`, merged restricted public profile, authority and managed-task refusal |
| ZPM | The checked-out workflow candidate, recorded in the report |

The paired initial-outcome changes are [OpenJules #16](https://github.com/niklas-olsson/OpenJules/pull/16)
and [Polyphony #122](https://github.com/niklas-olsson/keystone-polyphony/pull/122).
Both changes are merged. The initial outcome authority first merged at
`143d77c92ca8699ad7ccad4de98a540da6540a09`, tree `0a1462d969b739c4928667c1cd099a9e009369e1`.
Its [normal current-base CI](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43125461545)
and [controller publication](https://github.com/niklas-olsson/keystone-polyphony/actions/runs/43125433077/attempts/2)
remain evidence for that revision. The operative Polyphony pin now includes
[managed-task refusal on legacy paths](https://github.com/niklas-olsson/keystone-polyphony/pull/133)
and the [merged authority-boundary documentation](https://github.com/niklas-olsson/keystone-polyphony/pull/134).
The six probed authority modules and dependency requirements are unchanged.
Every changed component set must pass the complete installed workflow before promotion.
The OpenJules ancestry also contains
[durable control replay](https://github.com/niklas-olsson/OpenJules/pull/15).
The current pin adds [explicit owned cleanup](https://github.com/niklas-olsson/OpenJules/pull/17)
and [strict adapter and authentication checks](https://github.com/niklas-olsson/OpenJules/pull/18)
for the qualified creation transport. [Versioned persisted proof](https://github.com/niklas-olsson/OpenJules/pull/20)
keeps earlier unqualified creation markers ineligible after restart.
The operative OpenJules revision also includes the actual Docker
[combined-verification failure case](https://github.com/niklas-olsson/OpenJules/pull/21)
and component [claim-versus-drain regressions](https://github.com/niklas-olsson/OpenJules/pull/22).
Those component results do not replace this installed trial's exact-set qualification.

The current runtime pin adds [OpenJules #30](https://github.com/niklas-olsson/OpenJules/pull/30).
Its acceptor can return the existing UID denial while the one authorized effect
handler is waiting for a provider. It keeps one active handler, a finite queue,
the original socket deadlines and shared inference gate. Active work retains
service ownership through shutdown. This corrects the controlled held-provider
regression without changing this trial's child verification or fixture release.
The earlier ZPM #28 candidate `d74628bcb45385a28b86b65cfb4b5b9c83a70b8e` remained
unqualified: both child jobs reported a five-second timeout while the fixed
verification script read the first model-socket response header. Those failures
and the older cause-unknown `cf45f8b2` failure remain evidence for their original
revisions. Component regression success does not qualify the changed installed set.

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

### Admission against a genuine prior authority revision

Before any root enrollment or worker allocation in this variant, the trial also
runs Polyphony `473df5a0406accfe5cb550877e7b28a602cb116c` from a separate clean
checkout. Its tree is `389ec80cb6a99edf2993d59e4540f6bdeaaa5f18`.
This historical revision is the direct parent of the original outcome authority
commit `653c0a71a921c0d23b8e0171277524125e929e66`. The merged positive revision
in the component table contains that implementation through its later branch
ancestry. The six authority modules checked by
the import probe and `requirements.txt` match their original `653c0a71` Git blobs.
The full source trees differ: the later package exports the safe experimental
receipt class without import-time I/O or managed capability/schema registration,
and contains opt-in sidecar measurement and repository automation changes. The
operative revision also refuses managed task IDs through legacy advisory and
experimental task or baton paths. Those guards do not change the six probed
authority modules or dependency requirements. This phase still exercises the
actual canonical service, not each legacy entry point.
The additional historical checkout remains negative compatibility evidence and
does not replace the operative positive revision.

The historical coordinator accepts the original generated configuration,
credentials and empty canonical database. Its authenticated
`/managed/capabilities` returns HTTP 200 with the same repository, approved
base, profile digest, root dispatch and child-grant policy. It lacks
`child_initial_outcome`, which the selected `initial-v1` OpenJules profile
requires. The trial compares the entire old response to the actual current
response with exactly that field removed. It does not require old root
enrollment to fail, manufacture a capability response, or substitute a
health-only HTTP server.

The phase uses the installed Polyphony interpreter and unchanged generated
command and environment. Only the owned authority process's working directory
changes. Git revision/tree/cleanliness checks and module-origin/hash probes
under that same interpreter, environment and working directory verify both
source selections. Each probed module must match its pinned Git blob bytes
before and after the import probe, even if special Git index flags make a
changed worktree appear clean. Neither the sealed current checkout nor the
generated installation is rewritten.

One actual OpenJules process spans current-authority readiness, the historical
denial and restored current-authority readiness. Actual source/image/Docker and
child-transport preflight must first pass at target zero. With the old authority
running, two completed installed controller passes at target one and two
identical fresh ensure requests must remain unready. The runtime and the
completed controller reports must both identify the missing required extension
with `Canonical managed authority is unsupported: child_initial_outcome=1`.
ZPM accepts only that fixed runtime diagnostic after validating the lifecycle
response and its exact deployment, repository and profile. Arbitrary reasons,
malformed capabilities and HTTP/transport failures retain their existing generic
errors. The separate current-service/no-managed-policy503 scene still reports
`Canonical managed authority is unavailable` and `repository_access unavailable`.
Earlier historical-authority runs used those generic messages too; they proved
refusal and recovery but did not qualify this diagnostic distinction. The new
paired producer/consumer set must pass the unchanged actual historical phase
before claiming that operator-visible criterion.

Every observation must preserve empty
all-scope protected/runtime/child/model histories across seven read-only
journals, an empty capture directory, unchanged policy fingerprints and Docker
inventory, and zero accepted or rejected model requests.

Target zero is restored before the old authority is stopped and reaped. The
current authority must recover the original capability object, a completed
target-zero controller result and the unchanged empty state. Both helper-owned
services then stop; the normal generated manager starts the unchanged
twelve-call positive outcome scene described above. Cleanup attempts target
reset and both owned service shutdowns even after a failed assertion, preserving
the original failure. Only aggregate source hashes, capability/status facts and
history digests enter the shareable report.

For a manual initial-outcome run, supply the separate verified checkout:

```sh
python tests/contracts/check_children_stack.py \
  --initial-outcomes \
  --historical-polyphony-checkout /absolute/checkouts/polyphony-historical-473df5a \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /absolute/private/managed-children-image-id)" \
  --work-dir /absolute/private/managed-child-outcomes-trial
```

This establishes required-feature admission against actual older code once the
new exact-set CI passes. It does not establish populated-state downgrade
compatibility, absence of the core managed protocol, a literal `/health`
contract or live-provider behavior. The separate current-no-policy model trial
and old-shaped HTTP fixtures retain their narrower labels. No additional
inference allowance, worker, child grant, job or timeout extension is introduced.

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

## Bounded operational measurements

Both existing child variants add an **operational_measurements** object to the
aggregate report. These observations use the same installed services, workers,
two child grants and deterministic model replies already required by the trial.
They add no provider requests, worker incarnations, inference loop, workflow job
or package dependency. The ordinary variant still makes ten model calls; the
initial-outcome variant still makes twelve. The existing 90-second child,
180-second attempt, 240-second worker and CI timeout limits remain unchanged.

This is an operational measurement for this controlled profile. It does not
qualify live-provider latency, model reasoning, provider token usage, financial
cost, a warm reused-child session or a second private instruction round. Public
conversation and those session behaviors still need their own implementations
and qualification.

### Timing and causal identity

All durations use the host monotonic clock and are reported in nanoseconds.
The named intervals have the following exact boundaries:

| Report interval | Start | End |
| --- | --- | --- |
| prepare | Existing bundle preparation call | Successful return |
| verify | Existing initial offline verification call | Successful return |
| bootstrap | Existing installed bootstrap command | Successful command and owned-process cleanup |
| initial_manager_ready | Existing manager launch | Successful installed doctor readiness observation |
| target_one_converged | Existing target-one request | Existing capacity-convergence predicate succeeds |
| restart_manager_ready | Existing full manager restart | Successful installed doctor readiness observation |
| Enrollment to first root request | Immediately before the original enrollment POST | First validated root model-fixture request |
| spawn_response_prepared_to_first_request_ns | Existing spawn tool response is prepared, before returning it | Matching child's first validated model-fixture request |

These are observed end-to-end intervals. They do not isolate one controller tick,
backend call or operating-system process launch. Bootstrap is not a claim about
an empty package cache. The child interval includes canonical grant handling,
scheduling, UID/socket setup and the broker path. A child can make its first
request before its parent receives the spawn result, so the parent receipt is
not used as the starting clock edge.

The fixture records only its existing actor label, ordinal, timestamps and
instruction digest. Before publishing measurements, the trial reconciles each
label with the existing canonical child ID, grant digest, instruction digest,
root/parent attempt, worker and task binding. The existing model reservation and
final capture assertions remain required. A fixture label does not establish
canonical authority by itself.

### One idle sample

After the existing target-one capacity convergence and before enrollment, the
trial takes one fixed two-second idle sample. Model requests and fixture errors
must both be empty before and after it. This is a passive observation; it does
not invoke an extra reconciliation, model call or Docker statistics command.

The cohort is the owned installation manager and exactly its three direct
Polyphony, OpenJules and ZPM service children. Their actual command vectors and
working directories must match the generated installation. Each process is
fenced by its PID and Linux process start time, with parent identity and the
complete child cohort checked again at the second endpoint. Missing, replaced,
reparented or mismatched processes and counter regressions fail qualification.

The report retains user/system CPU tick deltas, the observed wall interval,
clock ticks per second, page size and RSS byte observations at both endpoints.
These are process self counters, including their own system CPU time. They
exclude descendants, the Docker daemon and unrelated host or kernel work.
RSS is an approximate endpoint observation, not a peak or precise memory
snapshot; Linux documents the asynchronous nature of this accounting in its
[proc filesystem reference](https://docs.kernel.org/filesystems/proc.html).
The sample does not prove that a complete controller cycle occurred within it.

### Context and storage units

Each already-retained fixture request contributes actor, ordinal, message count
and byte lengths for its messages, tools and full request. The encoding is
UTF-8 JSON with sorted keys, compact separators and ensure_ascii=False.
These logical JSON sizes are neither captured HTTP traffic nor provider tokens.
Prompts, tool payloads, instructions and credentials are omitted from the
measurement report. Existing output-token reservations remain separate from
observed token generation or monetary cost.

Storage is observed at six ordered boundaries: prepared bundle, ready at target
zero, idle before enrollment, verified final capture, stopped services after
owned cleanup, and the existing full restart plus original ensure replay.
The initial-outcome variant's interval from preparation to readiness also
contains its separately reported historical-authority admission phase.

For each of the seven existing canonical/controller/runtime/lifecycle/
dispatcher/model/child journals, the report records main, WAL and SHM files
separately, with presence and byte length. Paths come from the generated
configuration and existing runtime layout. The capture inventory records only
aggregate regular-file count and byte length under the owned runtime capture
directory, with a maximum of 4,096 entries and 16 directory levels.

Measurements use file metadata only. They create no SQLite connection and
perform no checkpoint, vacuum or other database action. Symlinks, special
files, paths outside the bundle or excessive inventories fail qualification.
Each file stat is a separate observation, not an atomic database snapshot,
allocated-block measurement or a combined main-plus-WAL durability proof.
Adjacent phase deltas are signed: WAL/SHM shrinkage or disappearance can be
normal. Capture paths, filenames, database rows and archive contents do not
appear in these aggregates.

### Validation boundary

The helper has deterministic negative tests for timing/order and canonical
lineage, boolean or nonfinite numbers, Unicode byte accounting, PID reuse and
counter regression, incomplete process cohorts, unsafe or excessive storage
inventories, and incomplete phase reports. A Linux owned-subprocess fixture
checks the actual process-reader interface; a real SQLite WAL fixture checks
that metadata observation preserves bytes without opening another connection.
Those tests do not substitute for the existing installed Docker child jobs.

The report uses schema version 1 and is bounded to 100,000 logical JSON bytes.
The existing privacy checks cover the added object before it is written.
There is no invented performance acceptance threshold. Actual measurements
must first be obtained from the exact delivered component revision set, and any
later threshold requires separately reviewed evidence.

## Explicit cold clarification

A separate invocation of `tests/contracts/check_cold_children_stack.py` qualifies
the opt-in `cold-clarification-v1` protocol. It uses the same existing child-job
image and a new private installation. The default ten-call scene and the
twelve-call initial-outcome scene above keep their original source, policies,
commands and measurements. Omitting `session_protocol` does not enable a session
or add its optional journal tables.

The sealed OpenJules child policy must explicitly select
`session_protocol: cold-clarification-v1`, `outcome_protocol: initial-v1`,
the trusted default child command and at least two child slots. ZPM retains that
opt-in in the immutable installation configuration. Polyphony receives only its
existing four-field child-policy projection. The session is a runtime
correlation over ordinary canonical grants, not another task, budget or
acceptance authority.

Run this third scene with a complete reviewed component set that includes the
cold producer and consumer:

```sh
python tests/contracts/check_cold_children_stack.py \
  --polyphony-checkout /path/to/polyphony \
  --openjules-checkout /path/to/openjules \
  --image "$IMAGE_ID" \
  --work-dir /new/private/managed-cold-children-trial
```

The existing `managed-children-stack` job runs it after the two original scenes.
All three reports are distinct required results; one successful scene does not
qualify another. The job retains the cold scene's own report and redacted
diagnostics without uploading private state. It adds no job, image build,
credential or provider service.

### Separate work, grants and original deadlines

The deterministic fixture supplies six root responses and two responses for each
physical child, for ten total calls. The root begins one logical session.
Round one writes a distinct provisional change to `left.txt` and explicitly asks
for clarification. Its actual exec returns zero and its patch and report are
retained, but no configured-verification success is claimed for that round.

The root receives the actual provisional status and predecessor digest, then
explicitly requests one follow-up for `right.txt`. The successor receives the
original instruction and frozen predecessor in its effective instruction
envelope. It has a new canonical child ID, grant, ordinary delivery intent,
physical exec, UID and checkout. Its configured test proves that `left.txt` is
still the original `old` value. The earlier provisional checkout is not copied
into the successor. The root applies only the second patch and implements
`left.txt` itself before independently testing the combined result.

| Allocation | Canonical allowance | Evidence |
| --- | --- | --- |
| Root attempt | 180 seconds | One original task and attempt |
| First physical child | 90 seconds and two turns | Separate grant, UID 60000, exec and provisional clarification |
| Second physical child | 90 seconds and two turns | Fresh grant, UID 60001, exec and configured verification |
| Task total | 360 seconds | Actual canonical task charge, including both complete child allocations |
| Model budget | Ten calls, at most 1,024 output tokens each | One shared durable allowance, 6 + 2 + 2 observed calls |

Both physical children have their own canonical work ordinal one. Logical
rounds one and two do not turn them into one reused child or a second root
attempt. The canonical `polyphony-child:` launch identity and the ordinary
`openjules-child:` delivery identity are checked against their separate original
formulas.

A fresh canonical allocation does not renew the predecessor's earlier local
watchdog. The trial freezes the first child's actual armed monotonic deadline
before final capture. The second round's retained cutoff and armed deadline must
not exceed it, even when the successor's nominal permission deadline and the
root's remaining cutoff are later. Subsequent observations may shorten the
root cutoff; they cannot extend the original round cutoffs or replace exec,
grant, request, instruction or report identities.

### Model fence, capacity and final evidence

The first model endpoint is deliberately closed and absent after follow-up
promotion. Its durable `model_closed` and `fence_established` fields must agree.
This is a model-admission fence, not canonical permission revocation. Both
outcome endpoints remain separate, and the successor's model endpoint remains
live. Actual root-UID probes must be denied by those live child endpoints.
The original first model socket must stay absent.

Read-only root commands poll status outside inference. The fixture holds the
fifth root response only after both physical execs have returned and all four
child model calls have finished. This avoids asking a child to make inference
while the root occupies their shared inference gate. At that barrier, the
actual canonical snapshot must still show live child permission, one reserved
worker and three unreleased execution references.

The target is then set to zero while the root remains held. The worker must
drain and reject new child admission without releasing those original
references. After the response is released, final root and child captures and
the shared container fence must complete before capacity reaches zero. The
report includes each physical child's separate artifact hashes and semantic
outcome, the root's review-pending candidate and the unchanged original task
charge. A semantic outcome alone does not authorize capacity release or
acceptance.

The actual retained runtime archive supplies a second checkout oracle: the
first provisional edit remains in the first private checkout, the second has
the original left file and its own completed right file, and the root contains
the combined result. The archive's child Git entries retain their distinct
UIDs. No raw archive content or private outcome summary enters the shared
report.

### Retention, shutdown and measurements

The trial freezes the complete private session/round correlation before final
capture and compares it again after capture, exact scoped cleanup and full
service restart. The existing all-table journal digest includes the optional
session tables. Cleanup must retain original request keys, predecessor bytes,
report payloads, grants, execs and armed cutoffs, along with canonical artifacts
and a distinct idle non-target worker. Original ensure replay must return the
same retired worker without resource resurrection or additional inference.

A stopped process-map entry alone is insufficient shutdown evidence. The exact
original installation manager must return its handled status 130 before
cleanup and restart, confirming its normal path through separate service
shutdown. Final owned cleanup is also required before a passed report is
written. Cleanup failures preserve the original failure and cannot leave a
new successful report artifact.

The same bounded measurements described above are reused in explicit cold mode.
For each child, `session_response_prepared_to_first_request_ns` starts when the
root fixture prepares the corresponding session-tool response. The final
effective instruction does not exist at that earlier point. Its digest is
bound once from the actual first child request, then checked against the real
canonical grant before publication. No invented early instruction envelope or
host-chosen child identity is used. Ordinary scenes keep their original
`spawn_response_prepared_to_first_request_ns` field.

Six ordered storage phases retain the same seven journals, signed main/WAL/SHM
differences and bounded capture inventories. The separate two-second idle
sample retains the manager and three service PID/start-time identities and self
CPU/RSS observations. It is not a claim of six process-cohort samples. Actual
module origins and direct pinned Git bytes are checked with the generated
interpreters before startup and again after restart; the report exports
component revisions, trees and module hashes, not private paths or credentials.

Focused tests cover literal committed SQLite WAL state, foreign or altered
history, independent delivery keys, predecessor replacement, cutoff extension,
real archive separation, the controlled HTTP fixture's sequencing, measurement
anchors and failed manager shutdown. They are harness tests. Only the actual
installed Docker job can qualify canonical allocation, kernel UIDs, model
fencing, final capture and retirement for a delivered revision set.

This scene does not qualify a warm child process, live provider judgment,
forced crash during installed follow-up, automatic root-agent resumption,
public-discussion peers, forge publication or merge authorization. Its root
intentionally adds work beyond the child patches, so it makes no exact-union
publication claim.

## Explicit failed child outcome and full recovery

A separate invocation of the initial-v1 trial covers an acknowledged semantic
failure from one actual confined child:

```bash
python tests/contracts/check_children_stack.py \
  --initial-outcomes \
  --right-outcome failed \
  --historical-polyphony-checkout /absolute/path/to/polyphony-historical \
  --polyphony-checkout /absolute/path/to/polyphony \
  --openjules-checkout /absolute/path/to/openjules \
  --image sha256:<already-built-managed-agent-image> \
  --work-dir /new/private/managed-child-failed-outcome-trial
```

The historical checkout is the same pinned prior revision used by the original
initial-outcome scene. This invocation repeats that pre-work denial and recovery
check; it does not bypass or redefine the authority requirement. The original
default, completed/declined and cold commands retain their behavior. Omitting
`--right-outcome` still selects decline for the initial scene. Either explicit
value, `declined` or `failed`, requires `--initial-outcomes` and is rejected before
trial effects when that flag is absent.

The allocation remains one root attempt of 180 seconds, two separate 90-second
child grants and a 360-second task charge. The same shared allowance covers
eight root and four child calls, at most 1,024 output tokens each, for 12,288
reserved output-token units. No provider, task, verification, polling, shutdown
or CI timeout is extended. The existing child job reuses its image and immutable
checkouts for this fourth scene.

The left child completes its assigned change. The right child reads its file,
then uses its actual bound `report_outcome` tool to submit `failed` with a fixed
private canary. The runner must receive the acknowledgement, retain exactly one
fixed failed-report event, skip configured verification and return exit code 2.
It must leave an empty patch. This is an explicit semantic decision in a normal
tool flow, not a fabricated provider, socket, Docker or verification outage.

The parent receives the same provisional failure and preserves it. It applies
the left patch, implements the right change itself, verifies the combined
repository and creates its ordinary review-pending candidate. Parent success
does not turn the child's report into completion. The child's final canonical
lifecycle state is `exited`; its distinct semantic outcome remains `failed`, with
its original ordinal-one instruction, payload digest and private capture
references. Neither semantic outcome grants release or acceptance authority.

While the original observation gate holds, target zero must retain the same
worker and all three execution references with unchanged existing permissions.
Final whole-worker fencing and both child receipts precede capacity release.
A read-only, all-row inspection of the original child journal independently
checks the actual left exit 0 and right exit 2, final local phase, UID/exec,
grant, report, artifact mapping, original delivery intent and monotonic cutoffs.
Missing, additional or foreign child histories fail the scene. The reader does
not checkpoint, create or repair the database.

The existing scoped cleanup includes every journal table and the original
archive. A complete installation restart uses a fresh authorized runtime client
to recover the identical committed private outcome and artifact bytes. Original
ensure replay must return the same retired worker. The local row projections,
canonical child records, task charge, model reservations and final zero capacity
must remain equal, with no new grants or inference.

The exact original manager must complete its handled shutdown with status 130
before cleanup and restart. The restarted manager must also return 130 and all
owned handles must close before a successful report is written. Killed or
incompletely handled cleanup cannot produce a passed report. Failure cleanup
preserves the primary error.

The report adds only `outcome_scenario: completed_and_failed` and bounded recovery
metadata: IDs, hashes, typed execution facts and comparison results. Private
instruction/report text and raw child output remain in the private retained work
directory. The fixed private summary is excluded from the broad snapshot and
shareable report and is redacted from exported failure diagnostics.

Focused standard-library tests use actual localhost HTTP, literal SQLite WAL
rows and owned test subprocesses. They distinguish omitted versus explicit
options, preserve original fixture responses, reject coherent changed records
and extra rows, and verify shutdown-before-report ordering. These are harness
oracles, not an installed authority or Docker result. The required installed gate
must pass on the exact source and component revisions before this semantic-failed
recovery is claimed. This scene does not prove forced cancellation, live-provider
judgment, warm sessions, public child discussion, forge publication or merge
authorization.

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
