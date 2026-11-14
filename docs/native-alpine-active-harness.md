# Native Alpine active-harness contract

**Status: corrected attempt-3 source candidate for independent review.**
The [first attempt](native-alpine-evidence/attempt-1/outcome.md) failed at source
metadata admission. The [second attempt](native-alpine-evidence/attempt-2/outcome.md)
passed that boundary and refused token response framing before reading a body.
No candidate binary, container or interpreter has run. Attempt 3 has not been
admitted; it retains every original authority/resource cap.
This is the next bounded slice of [ZPM #63](https://github.com/niklas-olsson/zero-point-modulator/issues/63),
following the inert catalog/compiler delivered in [PR #65](https://github.com/niklas-olsson/zero-point-modulator/pull/65).
It changes only the ZPM qualification fixture. The original
[candidate, caps and ownership](native-alpine-qualification.md) remain its input
contract, not evidence of a native result.

## Subject and acceptance

Keep `plan-v1.json` unchanged at SHA-256
`c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76` and the exact
173-artifact recipe at
`e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f`.
The tested production tuple stays ZPM `c5fb355f35b73041925e99590d6a18e71038548a`,
OpenJules `0dd5bf4e42b3677c3f2f564655bc87a97c4bf973` and Polyphony
`6480e36355bf0d5de4e25a560d40e665fd76038c`. The harness head is recorded
separately. This result could not qualify later source revisions implicitly.

Success requires all eleven existing native checks to pass on one bounded
attempt: artifact bytes, APK trust, base image identity, native APK transaction,
complete installed APK inventory, observed interpreter identity, production
prepare/verify, sealed bootstrap, native imports/interfaces, unchanged repeat
bytes and confirmed owned cleanup. No new native validator, broker, authority
mapping or installer is introduced. Polyphony #139 and paired installation
admission remain separate, unimplemented producer dependencies.

ZPM's current `AGENTS.md` requires focused changes, independent review and its
ordinary unit/package/CLI and affected integration checks. It adds no ensemble
publication prerequisite for this ZPM-only fixture. No Polyphony source change
or architecture-gated feature is part of this design.

## Proposed source boundaries

| File or area | Responsibility |
| --- | --- |
| Existing `check_alpine_sealed_candidate.py` | Retain the inert CLI, exact metadata admission and data-only archive helpers. No active flag is added to its command. |
| New `check_alpine_sealed_runtime.py` | Explicit active entry point, plan admission, host process/resource guardian, bounded acquisition, Docker custody, stages, safe result reduction and cleanup. |
| New `alpine_sealed_guest.py` | Fixed assembly-inventory and nonroot qualification commands. Use the original production APIs, separate owner interpreters and finite probe IDs. |
| `native-alpine/active-plan-3.json` | Bind the original plan/recipe, distinct third attempt, exact commands, source manifests and unchanged authority/resource limits. Both prior plans remain unchanged historical evidence. |
| New focused tests | Synthetic transport/archive/process/Docker/guest results and failure injection. No Docker daemon, candidate acquisition, package install or candidate import in the fixture lane. |
| Existing candidate workflow and canonical guide | Source checks on candidate changes, plus an explicit reviewed-plan native job and truthful result documentation. Ordinary required CI remains unchanged. |

The host and guest programs are fixture drivers, not a new reusable execution
framework. Reuse the existing APK signature/archive helpers, production wheel
identity parser, exact source checks and established owned-resource custody
patterns. Do not import sibling application modules in the host process.

## Admission and source custody

The active entry point first compares the selected active-plan bytes with the
required reviewed SHA-256, then admits its inherited immutable plan and catalog.
A missing/mismatched hash refuses before creating a work/output directory,
opening a network request or contacting Docker. A valid hash identifies reviewed
bytes; it is not itself proof that an independent review occurred.

Require one new private work directory, nonzero invoking UID/GID, Linux amd64,
the existing host memory/storage prerequisites, and finite supported tool/API
checks. The workflow supplies clean exact checkouts with credential persistence
and submodule initialization disabled. Check their commit/tree, all tracked blob
identities and dependency-file hashes, the sole `kp -> polyphony` symlink and the
two uninitialized gitlinks. Read-only mounts expose only these declared clean
checkouts, frozen inputs and the selected harness. Git metadata and actual
source files count toward owned logical storage; checkout/setup transport is a
separately reported workflow prerequisite.

Local Git configuration remains closed. The supported checkout action's optional
`gc.auto=0` setting is admitted exactly; other garbage-collection settings, values
or subsections are refused before any Git command, as are configuration includes,
filters and persisted HTTP credentials.

Use a private empty Docker configuration and the literal supported local Unix
socket. Do not inherit contexts, TLS endpoints, registry credentials, proxies,
credential helpers, provider variables or arbitrary guest environment. Record
actual host Python, Git, OpenSSL, Docker client/server and runner-image versions.
No new host privilege or container capability is obtained to make a prerequisite
pass.

## Finite acquisition and image assembly

The acquisition subprocess accepts only the frozen 173 artifact identities.
It streams bounded bodies to newly created files, accounts partial bodies and
reserves metadata before each request. Four transfers are the maximum, not a
requirement. The original 600-second/512-MiB/180-request envelope remains.
One fixed anonymous Docker Hub pull-token request is memory-only. That endpoint
alone may use an exact Content-Length or a bounded bare HTTP/1.1 chunked body.
Chunk framing and headers share the existing 65,536-byte per-request reservation;
payload shares the original 1-MiB token and aggregate body limits. Mixed framing,
extensions, trailers, malformed or incomplete chunks refuse. All artifacts still
require a single exact Content-Length. No permissive default chunk decoder is
used, and neither token contents nor framing text enters a report. Registry
authorization is confined to the exact `library/python` blob requests; the one
allowed CloudFront redirect uses a fresh credential-free request. APK and wheel
redirects, automatic retries, mutable tag pulls, index refreshes and resolution
are refused. No signed-query or token value enters a report.

Reverify each whole-file identity, every OCI diff ID and archive member bound,
wheel metadata/native-member catalog, and each APK's control signature and
authenticated payload hash. Obtain only the pinned APK trust-key members from
the verified OCI bytes, writing those fixed destinations without archive path
extraction. Frozen signed-index records remain historical provenance; the active
run does not re-fetch a mutable index or claim to reverify unavailable index
bytes. Native `apk` signature verification remains enabled independently.

Build one finite Docker-load archive from the exact config and the verified
uncompressed layer bytes, preserving order and diff IDs. The decoded layers
total 51,195,904 bytes. The original config has no entrypoint or declared volume;
the runner must verify that before load/create. Record custody before daemon
mutation. Refuse conflicting owned names; distinguish a preexisting immutable
base from a newly introduced image and never delete someone else's reference.

Assembly is the separately reviewed root container: network `none`,
no-new-privileges, cap-drop `ALL`, cap-add only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`,
and the original 2-GiB/128-PID/two-CPU bounds. Its command passes all 63 exact APK
paths to `apk add --no-network --no-cache --repositories-file /dev/null`.
Scripts and signature checks stay enabled. No daemon or service is started by
the fixture. A script/capability/solver failure ends the attempt.

Read the native installed database and require the complete expected 64-record
inventory, including the unchanged `.python-rundeps` record and dependencies.
Commit only the owned assembled result under an opaque unique fixture tag and
ownership label; record the actual image ID. Remove the assembly container and
confirm its absence before starting qualification. The base or assembled image
is never published or described as a managed-agent image.

## Nonroot production qualification

Create the qualification container with the invoking nonzero UID/GID, read-only
root, no-new-privileges, cap-drop `ALL`, no added capabilities, network `none`,
and the same hard container limits. Use fixed read-only input/source/harness
mounts and one fresh owned writable work mount. Disable the Docker log driver;
capture attached output into the bounded private host log. Add no device,
namespace sharing, control socket, volume or external endpoint.

Inspect the exact native container configuration before start. Inside it, verify
UID/GID, effective/permitted/bounding/ambient capabilities, no-new-privileges,
the declared filesystem mounts and network/process namespace facts. Network
`none` retains loopback; do not reuse the existing namespace probe's stronger
claim that every IP connection, including loopback, is unavailable.

Before production operations, verify Alpine/Python identity and the pinned
Python/libpython/musl file bytes. Call the real frozen
`zpm.dependencies.interpreter_identity()` and bind its complete observed output
to the existing sealed dependency-plan schema. Do not infer the ABI, cache tag
or platform. Use the original 106 wheel identities and per-owner lists: 3 for
ZPM, 83 for OpenJules, 70 for Polyphony. Shared wheels do not merge environments.

Call production `prepare` and `verify` for a disposable zero-capacity
installation, then execute its generated `manage.py bootstrap`. The necessary
task/image fields are explicitly unqualified fixture inputs, with no task or
service command dispatched. Keep pip 25.3, setuptools 80.9.0 and wheel 0.45.1.
Require the existing sealed completion, installed distribution/file/RECORD and
dependency checks for each independent environment.

The existing eight probe IDs map to fixed commands only:

| Probe | Actual operation |
| --- | --- |
| Three owner native-import probes | Each environment imports its own catalog subset in a separate process. Verify the loaded extension path and bytes against that environment's installed catalog. The 42 unique import targets and 11 support libraries remain distinct. |
| `zpm-cli-help` | Installed ZPM CLI `--help`. |
| `openjules-managed-service-help` | Its installed Python runs the frozen `src.worker.managed_service --help`. |
| `polyphony-coordinator-help` | Its installed Python runs the frozen `src.liminal_bridge.coordinator_service --help`. |
| `zpm-binding-validation` | Installed ZPM validates the fixed zero-capacity fixture binding. |
| `openjules-managed-context` | Its installed Python runs `src.worker.execution_context --probe-managed-installation`; the original selector must match and legacy execution must remain refused. |

Use each owner's exact source path with its own interpreter. Never combine the
two `src` namespaces, import an arbitrary user-supplied module, or substitute a
fixture return value for a native result. Keep the original 15 seconds per probe
and 120 seconds combined. A timeout is a retained failure.

Repeat production prepare/verify/bootstrap within the 60-second repeat cap.
Require identical original configuration, private secret and selector bytes,
all three sealed completion files and installed distribution sets. Retain these
comparisons privately; exported evidence reports equality verdicts without
credential-bearing values or digests.

## Observation and cleanup boundary

Keep the original 1,800-second total, reserving its final 90 seconds for cleanup.
Every stage uses the smaller of its declared sublimit and remaining work budget;
stage limits are not additive extensions. An owned child-process guardian bounds
DNS/transfer shutdown, command output and host subprocesses. Reconcile retained
PID/start identities through reparenting and refuse reuse or missing required
measurements. No unbounded thread shutdown or daemon command runs after the
total deadline under a fresh implicit budget.

The loop sleeps at most 250 ms between attempted samples of the host runner/child
RSS cohort and the active owned container's memory, PID and CPU counters. Container memory/swap/PID/
CPU limits are Docker-enforced settings; summed host RSS is a sampled stop limit.
Bounded collection work also takes time. Record its actual maximum sample gap;
do not describe the loop interval as a hard periodic or instantaneous observation.
Use the supported Engine stats operation with `stream=false&one-shot=true`
through the same fixed local socket, with bounded responses and deadlines. The
[pinned Moby implementation](https://github.com/moby/moby/blob/06db1734267bf7770914b417bff6556d835bf8cc/daemon/stats.go)
collects one immediate sample for this form; the
[API definition](https://github.com/moby/moby/blob/06db1734267bf7770914b417bff6556d835bf8cc/api/swagger.yaml)
documents the parameter. Refuse an unsupported observed API or missing live
counter. Empty stats after exit require a separately confirmed exact container
exit; they are not a zero-usage live sample.

The unchanged 4-GiB storage stop is sampled logical accounting: owned regular
files, source/Git copies, acquired bodies, generated archives, private work/logs,
and conservative owned image sizes plus writable container sizes. Count shared
image ancestry separately per owned image as the existing fixture does, disable
daemon logs, and remeasure at every stage boundary and final report write. This
does not establish a physical daemon/filesystem quota, account opaque daemon
caches, or prove an instantaneous peak. The implementation review must keep
that distinction explicit rather than claiming complete physical accounting.
The 1-GiB host RSS, 4-GiB available-memory prerequisite, 2-GiB container memory
and unchanged archive/output bounds remain separate stop limits.

Keep a synced ownership journal before each create/load/commit. Cleanup is only
for these exact owned container IDs/names and image references. Preserve a
preexisting base image; remove introduced fixture references/images as
applicable, with exact identity/label checks and confirmed absence. Never prune
the daemon. Keep the first workload failure and every cleanup uncertainty.
Final resource/report observations run before the original deadline; a failed
measurement, surviving owned process/resource or unsettled cleanup cannot leave
a passing verdict.

## Reports, workflow and review gates

Retain a new bounded private report with the exact attempt/harness/production
identities, all eleven PASS/FAIL/NOT_RUN checks, measured limits and fixed failure
codes. Preserve the first failure even if later cleanup also fails. Enforce the
256-KiB serialized limit including LF, with a deterministic bounded failure
fallback. Private command logs have the existing 1-MiB per-command and 16-MiB
aggregate limits. No private installation, raw log, token, selector or secret
hash is uploaded from the public repository's Actions job.

Export only a closed credential-free summary: public source/artifact identities,
observed environment facts, check/cleanup verdicts and aggregate resource data.
Source fixtures and historical artifact flags cannot become native checks. Keep
an unsuccessful native result and require a fresh reviewed plan for another
attempt. No binary release or operative downstream pin change occurs here.

Candidate pushes and PRs run only source admission and synthetic fixtures. The
active job uses the repository's supported hosted Docker runner with read-only
repository permissions and no model/provider credential. It starts only for an
explicit manual reviewed-plan input or dedicated same-source run reference
containing that exact hash, after source checks. A documentation push or an
empty/manual default must not acquire artifacts. Existing full CI, package/CLI
checks and independent review still precede merge; the separate reviewed native
attempt supplies the new environment evidence.

Before activation, reviewers must see the exact code/plan/command manifest and
fixtures for admission-before-effects, interrupted body accounting, redirect
credential confinement, archive/path rejection, source drift, absent/live/reused
processes, Docker identity/config drift, missing counters, deadline exhaustion,
command/log overflow, partial image/container mutation, report failure and
cleanup uncertainty. There is no currently missing artifact identity. Native
fit, APK execution, interpreter ABI, imports and cleanup remain unobserved until
that one admitted run.

## Implemented source boundaries awaiting activation

The active plan binds the workflow and complete local import set, including the
valid empty `tests/__init__.py` blob. Direct isolated-script invocation checks
the same exact hashes as module invocation. The source-only gate may calculate a
hash to validate its own checked-out data; that does not assert a review. Native
admission uses the separately supplied hash or exact immutable run-ref suffix,
rejects their disagreement and refuses a GitHub rerun attempt. Work directories
must be fresh and disjoint from all four source checkouts. Production and harness
tracked bytes share the original 128-MiB allowance.

All 173 bodies total 209,309,575 bytes. At the maximum four allowed OCI
redirects, the fixed request sequence reserves 240,068,846 bytes: each original
body, each redirected layer's full body again, the 1-MiB token-response allowance,
and 65,536 bytes for each of 178 exchanges. This remains inside 512 MiB and 180
requests. Header lines are bounded before their accumulation; duplicate length,
nonidentity encoding, transfer framing and second redirects refuse. Partial or
offending observed counters remain recorded before failure. The token and signed
redirect query are never persisted, and the redirected request has no registry
Authorization or cookies.

Docker mutations carry full prospective charges before their CLI starts:

| Exact owned resource | Prospective logical charge |
| --- | ---: |
| Base image | 51,195,904 bytes |
| One writable container layer | 536,870,912 bytes |
| Assembled image, including its base | 588,066,816 bytes |
| Thirteen exact fixture atomic `.new` paths | 3,407,872 bytes combined |
| Final report publication reservation | 1,048,576 bytes |

The peak fixed reservations for two images, one container, temporary slots and
final publication total 1,180,590,080 bytes. Ordinary input/source/work/log files
are charged separately inside the unchanged 4-GiB stop. A native image or
writable layer larger than its reservation refuses. This is prospective logical
accounting, not a physical allocation guarantee or daemon quota. A mutation
whose command outcome is unknown remains unqualified even if its exact owned
object can later be found and removed. A preexisting immutable base is observed
conservatively and never deleted.

The thirteen temporary paths stay fully charged for the whole attempt and are
explicit in the active plan. A disappearing
entry is tolerated only for that exact declared path, retaining its full charge.
An existing entry must be an ordinary single-link private file within its size
bound. Only the two assembly receipt/phase temporary files may briefly belong
to root before the guest's exact `fchown`; their final evidence must belong to
the invoking UID/GID with mode 0600. Unknown missing paths, traversal/read errors,
nonordinary entries or oversized slots fail. Other concurrent package/producer
filesystem mutations have no blanket observation exemption.

The Docker CLI and observer both use API 1.45 on the one fixed local socket.
Before start, the runner compares exact commands, user, working directory,
closed environment, labels, capabilities, namespace/log/restart settings and
bind mounts. It verifies the committed root assembly image before creating the
nonroot read-only qualification container. Host secrets, proxy controls and
arbitrary guest variables are absent. Only the documented base environment and
fixed private-home/Git/Python overrides are admitted.

Each directly owned host child is registered by PID/start and pidfd before a
general process scan. A forked acquisition/parser worker waits behind a parent
observer barrier. An initial scan refusal still terminates and reaps the known
original; unknown process observations remain failures. Historical PID reuse
cannot adopt a foreign process. One locked 8-byte output ledger applies the
existing 16-MiB allowance across host, OpenSSL and guest writers. A failed sample
latches refusal before the next workload command, while exact cleanup remains
available inside the original deadline.

The guest and host share a monotone fixed phase history: assembly 300 s, initial
bootstrap 900 s, all eight probes 120 s, repeat 60 s, each shortened by the original
work deadline. Individual probes keep 15 s. Host checks cover blocked native calls
that cannot promptly run the guest's Python timer. No later phase resets an
earlier clock. Positive guest checks are copied only after binding, order, phase,
custody, authority, installed-count and exact probe-list checks; an inconsistent
failed record cannot contribute unsupported positives. Final resource, cleanup,
deadline or report-publication failure prevents both a native PASS and a
successful runner exit.
