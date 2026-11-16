# Native Alpine active-harness contract

**Status: attempt 13 FAILED at STORAGE_OBSERVATION; cleanup PASS.
Closed context is ENTRY_LSTAT_MISSING / TRIAL / INSTALLATION_ENVIRONMENTS / ENOENT.
The exact entry, producer, cause and guest progress remain unknown. Native
qualification has not passed; no next operation is admitted.**
The [first attempt](native-alpine-evidence/attempt-1/outcome.md) failed at source
metadata admission. The [second attempt](native-alpine-evidence/attempt-2/outcome.md)
passed that boundary and refused token response framing before reading a body.
The [third attempt](native-alpine-evidence/attempt-3/outcome.md) completed 102
artifact acquisitions before `INPUT_CHANGED`, with cleanup PASS. The exact failed
read was not retained. The [fourth attempt](native-alpine-evidence/attempt-4/outcome.md)
completed all 173 acquisitions before `STORAGE_OBSERVATION`, with cleanup PASS;
its exact failed path and cause remain unknown. The
[fifth attempt](native-alpine-evidence/attempt-5/outcome.md) passed artifact bytes,
APK trust and base-image identity, then failed assembly capability inspection.
Its actual payload remains unknown and cleanup explicitly failed with an
unconfirmed create outcome. The [sixth attempt](native-alpine-evidence/attempt-6/outcome.md)
passed artifact bytes, APK trust and base-image identity, then failed stats
observation during assembly with cleanup PASS. Its actual response/state
chronology remains unknown. The
[seventh attempt](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43639051137)
refused acquisition with `OCI_REDIRECT_REFUSED` after two requests and 5,489 body
bytes, with zero completed artifacts or redirects and cleanup PASS. Its target
and failed predicate remain unknown. The [eighth attempt](native-alpine-evidence/attempt-8/outcome.md)
passed acquisition, APK trust, base-image identity, APK transaction and installed
APK inventory, then failed `DOCKER_DEADLINE`. Cleanup passed with zero pending
mutations. Its exact request and transport phase remain unknown. The
[ninth attempt](native-alpine-evidence/attempt-9/outcome.md) failed OCI `HOSTNAME`
after two requests and 5,489 body bytes, with zero completed artifacts or followed
redirects and cleanup PASS. Its actual hostname was not retained; later redirect
predicates were not evaluated. The [tenth attempt](native-alpine-evidence/attempt-10/outcome.md)
records artifact acquisitions, APK transaction and installed inventory PASS,
with `DOCKER_DEADLINE` at `CONTAINER_SIZE / RESPONSE_HEADERS` and cleanup PASS.
Its sampling location, owned stage and storage backend remain unknown.
The [eleventh attempt](native-alpine-evidence/attempt-11/outcome.md) failed
`OCI_REDIRECT_REFUSED / HOSTNAME / OTHER`, with zero completed artifacts or
followed redirects and cleanup PASS. The actual destination remains unknown;
the Docker sampling context was not reached. Its distinct plan added only that
fixed context and bound the separately delivered dependency source on main.
All eleven plans, candidate bytes, allowed origins, authority/resource caps and
deadlines remain unchanged. The separately admitted [destination observation](oci-destination-evidence/observation-1/outcome.md)
later recorded HTTP 307, `production.cloudfront.docker.com`, both current
hostname/path-prefix comparisons true and confirmed cleanup. It performed no
native execution and does not identify native11's rejected destination or a
routing cause. No origin expansion, observation repeat or additional native
attempt is selected by this result.
This [ZPM #63](https://github.com/niklas-olsson/zero-point-modulator/issues/63)
qualification fixture follows the inert catalog/compiler delivered in
[PR #65](https://github.com/niklas-olsson/zero-point-modulator/pull/65). The original
[candidate, caps and ownership](native-alpine-qualification.md) remain its input
contract, not evidence of a native result.

The [twelfth attempt](native-alpine-evidence/attempt-12/outcome.md) completed all
173 acquisitions and the APK checks, then refused `STORAGE_OBSERVATION`, with
cleanup PASS. The host's coarse `interpreter_identity` label does not prove an
interpreter mismatch or that later guest work never began. The failing storage
operation, path and errno remain unknown. The [thirteenth attempt](native-alpine-evidence/attempt-13/outcome.md)
retains the closed missing-entry context above, with unchanged accounting and
cleanup PASS. It does not identify the exact producer or diagnose native12.

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
admission remain separate work and are not exercised by this frozen tuple.

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
| `native-alpine/active-plan-7.json` | Historical seventh-attempt binding of the original plan/recipe, commands, source manifests and authority/resource limits. It and all six prior plans remain unchanged; it cannot admit the diagnostic source revision. |
| `native-alpine/active-plan-8.json` | Historical eighth-attempt binding of the OCI diagnostic and distinct workflow/work/report identities. It and all earlier plans remain unchanged. |
| `native-alpine/active-plan-9.json` | Historical ninth-attempt binding of the Docker-observation diagnostic and distinct workflow/work/report identities. It and all earlier plans remain unchanged. |
| `native-alpine/active-plan-10.json` | Historical tenth-attempt binding of the closed OCI hostname-class diagnostic and distinct workflow/work/report identities. It and all earlier plans remain unchanged. |
| `native-alpine/active-plan-11.json` | Consumed eleventh-attempt binding of fixed Docker sampling context and distinct workflow/work/report identities. It also binds the delivered `zpm/dependencies.py` from main; the other eight executing inputs, candidate, commands, authority and caps remain unchanged. Its [actual run failed during OCI acquisition](native-alpine-evidence/attempt-11/outcome.md); this plan admits no additional attempt. |
| `native-alpine/active-plan-12.json` | Consumed binding of the response-custody correction and distinct native12 workflow/work/report identities. Only the runtime and native workflow change among the eleven executing inputs. Its [actual operation failed at storage observation](native-alpine-evidence/attempt-12/outcome.md), with cleanup PASS. All earlier plans, candidate, policy, commands, authority and caps remain unchanged; this plan admits no additional operation. |
| `native-alpine/active-plan-13.json` | Consumed binding of closed storage-observation context and distinct native13 workflow/work/report identities. Only the runtime and native workflow changed among the eleven executing inputs. Its [original operation failed](native-alpine-evidence/attempt-13/outcome.md) at an unreserved lstat ENOENT under the lexical installation-environments subtree, with cleanup PASS. Earlier plans, observer1, candidate, policy, commands, authority and caps remain unchanged; this plan admits no additional operation. |
| New focused tests | Synthetic transport/archive/process/Docker/guest results and failure injection. No Docker daemon, candidate acquisition, package install or candidate import in the fixture lane. |
| Existing candidate workflow and canonical guide | Source checks on candidate changes, plus an explicit reviewed-plan native job and truthful result documentation. Ordinary required CI remains unchanged. |

The host and guest programs are fixture drivers, not a new reusable execution
framework. Reuse the existing APK signature/archive helpers, production wheel
identity parser, exact source checks and established owned-resource custody
patterns. Do not import sibling application modules in the host process.

The separate [private ext4 census prototype](native-alpine-private-ext4-observer.md)
models one retained freeze/census/thaw cycle with injected effects only. It has no
privileged backend, original-harness integration or native14 plan. Its declared
acknowledgements are not kernel evidence; all consumed native13 inputs, caps,
authority and failed outcomes remain unchanged.

## Separate destination observation

The [accepted observation scope](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6897044327)
adds `check_oci_destination.py`, `oci-destination/observation-plan-1.json` and a
separate `oci-destination-observation.yml` workflow. Its [actual observation](oci-destination-evidence/observation-1/outcome.md)
completed as `OBSERVED` in run `43735682057`, attempt 1, at source
`67e7d0dc6c3dd43e842d34e304f64bedfe414737` and plan
`54844e18bbf22d75841b6bcbfd22e7d455c52f70e4743d9919bc23f145f0ca55`.
The exact observation plan and ref are consumed. It changes none of the eleven
consumed native inputs, existing plans, native workflow, candidate, allowed
origins or caps. Its result belongs only to this operation and cannot reconstruct
the destination rejected in attempt 11 or qualify a native installation.

The selected Ubuntu 24.04/Python 3.12 job permits at most two sequential HTTPS
requests: the existing anonymous token GET, then a GET of the exact first pinned
`library/python` blob. There is no HEAD fallback, manifest lookup, retry or
redirect follow. System TLS verification stays enabled; proxies and inherited
credentials are absent. The worker keeps the anonymous token only in memory and
sends it only to the fixed registry endpoint. Each request has one ten-second
deadline across its response work, 65,536 bytes of bounded metadata, and the
token response has a 1 MiB body limit. The blob's existing 3,853,673-byte
prospective reservation remains charged, but its response closes without any
application body read or artifact retention. TLS/socket prefetch is not claimed
to be zero. No Docker, guest, service, package or model operation runs.

Only HTTP 307 can produce `OBSERVED` and a parsed hostname. Exactly one Location
must pass the fixed raw ASCII, HTTPS, DNS, port, userinfo, fragment and token
reflection checks. The hostname is a bounded lowercase DNS name, excluding IP
literals and normalization/repair cases. The report also retains fixed predicate
results, status classes, counters, cleanup outcomes and comparisons against the
unchanged current hostname/path predicates. Other statuses, including other
redirect codes, are inconclusive with no hostname; unavailable or malformed
evidence refuses. No received URL, path, query, userinfo, header, token, raw
exception or hash of an arbitrary target is exported. A newly observed hostname
is evidence for subsequent review, not an allowed origin or redirect contract.

Source admission captures the exact bounded import closure, observer, workflow,
recipe and plan bytes, then requires all fourteen selected files to be regular
blobs in the admitted Git commit. Git replacement objects, global/system
configuration, hooks, file monitors and transport are disabled for the bounded
local plumbing. Imports execute only
the captured repository sources, without ambient sibling modules or bytecode.
The immutable observation ref is
`qualification/oci-destination/run/<plan-sha256>/<source-commit>`; its commit must
match the actual checkout and `GITHUB_SHA`. Only Actions attempt 1 and a fresh
private work directory are admitted. A target commit that disagrees with the
ref's encoded source identity, reused work or a changed selected input refuses
before HTTP. Creating the ref once, without moving, deleting or recreating it,
remains the recorded activation protocol; the program does not claim historical
ref-replay detection. Ordinary source publication runs only synthetic tests and
data-only `--check-plan` checks in this separate workflow. Ordinary repository CI
remains independent and may run its installed/Docker gates on the same push.
Root admission and creation of the distinct exact observation ref are separate
actions.

The original total budget is thirty seconds, with worker work ending at
twenty-five seconds and the remaining time reserved for owned termination and
reaping. Git helpers and the worker share that original deadline. Local file
operations are bounded by size and checked cooperatively; this is not an
interrupting wall-clock guarantee for every filesystem syscall. Unknown cleanup
and partial counters remain unknown. The CLI returns 0 only for a validated
current observed/inconclusive report, reserved exit 20 for a validated current
failure report, and 2 when no current report is eligible. The workflow creates
the fixed upload marker only after receiving 0 or 20, then translates 20 to a
failed step. Generic interpreter failures, stale summaries, report-write errors
and a final validation that crosses the original deadline cannot enable upload.

After the native12 correction, the observer workflow checks the current observer
fixtures, then checks out original source
`67e7d0dc6c3dd43e842d34e304f64bedfe414737` in a separate directory. It verifies that
HEAD, reruns the original observer fixtures and admits the unchanged observer1
plan `54844e18bbf22d75841b6bcbfd22e7d455c52f70e4743d9919bc23f145f0ca55` and native11
plan `92c4d05be1388e058054d7bb45f90d9dcd4f272bb448db853a6f8819ee908645` against their
original bytes. The separate current-tree native13 check is inert source
validation. Native12 and native13 retain their consumed original plans, sources
and failed results.
These historical checks preserve evidence; they do not qualify changed source or
reactivate a consumed operation.

## Admission and source custody

The active entry point first compares the selected active-plan bytes with the
required reviewed SHA-256, then admits its inherited immutable plan and catalog.
A missing/mismatched hash refuses before creating a work/output directory,
opening a network request or contacting Docker. A valid hash identifies reviewed
bytes; it is not itself proof that an independent review occurred.
The existing active-run guard also rejects an Actions `GITHUB_RUN_ATTEMPT` other
than `1` before plan admission; a new source plan does not authorize a replay.

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

## Live-record observation

The sole acquisition subprocess publishes `acquisition.json` through
`atomic_private`; the selected assembly/qualification guest publishes its own
phase file through `guest.write`. Both writers create a new file and atomically
replace the published pathname. The assembly guest assigns the invoking UID/GID
before publication; qualification already runs under that identity. Files are
0600. No publisher edits the old inode in place.

Only three relative paths enter `read_live_record`: `acquisition.json` at
65,536 bytes, `work/assembly-phase.json` and `work/qualification-phase.json` at
8,192 bytes each. A snapshot uses one no-follow, nonblocking retained descriptor
and one read of at most the existing bound plus one byte. No retry, wait for a
newer publication, successor reopen or deadline renewal is allowed. The original
work or cleanup observation deadline is checked before open, before read and
after the descriptor metadata check.

Admission requires a regular file, 0600 mode, invoking UID/GID and link count
zero or one. Both fstats must agree on device, inode, size, mtime, complete mode,
UID and GID; the read must have the original exact size. With stable link count,
ctime must also remain identical. The sole exception permits ctime to advance
when the opened inode's link count retires from one to zero during replacement.
If replacement occurred before the first fstat, both counts may be zero but
ctime must then remain unchanged. Hard links, observed in-place changes,
nonordinary files, identity drift, short reads and unknown observations refuse.
This is a snapshot contract for the fixed sole publishers, not proof against an
adversarial same-UID writer.

The existing JSON schema, attempt binding, monotone acquisition/phase history
and resource/phase deadline validators still check the returned complete
old-or-current record. Partial or rewound progress never becomes success.
Artifact and source readers keep their strict unchanged-ctime checks. Child
terminal records are read after reaping. Successful guest-result reads follow
confirmed container exit; the existing failure-only guest-result read may race
remaining guest work and therefore remains strict, best-effort evidence that
cannot erase the latched failure. The separate locked output counter is unchanged.

## Retained APK verification inputs

The active runner explicitly selects retained signature inputs in the existing
APK verifier. The ordinary shared musl fixture keeps its original temporary
directory behavior. No APK signature, checksum, control-identity, payload-hash
or archive check is removed, and the fixed OpenSSL command remains unchanged.

Each of the existing 63 package digests owns one exclusively created directory
`signature-<apk-sha256>` with mode 0700 and invoking UID/GID. Its `signature` and
`control.gz` files are exclusively created, no-follow ordinary files, mode 0600,
single-link and owned by that identity. Existing directories/files or links are
not reused or overwritten. Both inputs remain on successful verification and
failure. The verifier does not unlink them or their directory while the parent
is measuring storage.

The original signature bound is 1,024 bytes per package. Each compressed control
stream is part of its hash-verified APK and cannot exceed that APK's original
byte size. The fixed APK total is 78,915,719 bytes, so 63 signatures and controls
have a conservative combined ceiling of **78,980,231 bytes**, across at most
63 directories and 126 files. The new plan records this finite retention policy.
All these bytes remain ordinary linked files in the existing strict storage
scan and the unchanged 4-GiB stop; they receive no disappearance exception or
unmeasured storage allowance. Original stage, command, output and whole-attempt
deadlines remain in force.

The parent observes these files through final resource checks. The private
attempt directory remains retained after process/container/image cleanup, as
before; a reported owned-cleanup PASS does not mean retained evidence files were
deleted. Only the closed summary is uploaded. The owner can retire the complete
private directory after observations end. The local chronological regression
uses real helper creation/completion and filesystem enumeration, with an
explicit synthetic crypto result. It is not a native signature qualification or
proof of the original hosted failure's exact path.

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

The [selected native12 correction](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6897794009)
transfers all five native redirect statuses into the owned HTTP-error path before
urllib can parse or repair Location. The original destination predicates then
evaluate the untouched value. Malformed or unsafe responses receive a close
attempt without a follow; a valid allowed redirect is followed only after its
response closes successfully. No accepted status, hostname, path, credential, single-hop,
request, byte, hash or deadline rule changes. This defect is not a diagnosis of
native11, which reached the hostname guard.

Acquisition cleanup attempts both already-owned response and destination-stream
closure. A later close failure cannot replace an earlier refusal, parser error,
read error or control exception. If closure fails first, that exception stops
the operation. Per-response closure is not separately reported; an incomplete
close remains unconfirmed and cannot be inferred from the aggregate
`owned_cleanup` result, which retains its process/container/image meaning. The
public report schema and its closed diagnostic projection remain unchanged.

An existing `OCI_REDIRECT_REFUSED` guard can retain one optional
`oci_redirect_predicate` in the existing 4,096-byte acquisition-child result and
256-KiB final summary. Its closed values are `LOCATION_CARDINALITY`,
`ORIGINAL_REGISTRY`, `HTTPS_SCHEME`, `HOSTNAME`, `PORT`, `USERNAME`, `PASSWORD`,
`FRAGMENT`, `PATH_PREFIX`, `URL_LENGTH`, `BACKSLASH`, `PRINTABLE_ASCII` and
`UNAVAILABLE`. The value names the first failed predicate in the original
short-circuit order. It does not record a redirect target, HTTP status, query,
token, header, body or arbitrary exception text. There is no additional request,
parse, property read, follow attempt or resource allowance.

The diagnostic is attached privately to the exact original guard exception;
parser and operand exceptions are not reclassified. A fixed value crosses the
existing child result into the parent's original refusal. A mutable exception
attribute or changed child dictionary cannot replace its privately retained
first value. Optional projection failure yields `UNAVAILABLE` without replacing
the refusal. Once the original OCI refusal is already latched as the trial's
`first_failure`, later failures or cleanup errors cannot replace its diagnostic.
Existing child finalization can instead latch another failure before the parent
handles the original OCI refusal. In that case the child result retains its
original OCI diagnostic, but the final summary follows the existing first-failure
policy and omits it. This diagnostic does not change that ordering. Existing
response closure, request accounting, credential stripping and redirect policy
remain in force.

When that existing guard fails at `HOSTNAME`, the same already-read operand may
also produce `oci_redirect_hostname_class`. Its only values are `MISSING`,
`HISTORICAL_CLOUDFLARE`, `HISTORICAL_DOCKER_R2`, `OTHER` and `UNAVAILABLE`.
The two historical values compare exact fixed public literals documented in the
[ninth outcome](native-alpine-evidence/attempt-9/outcome.md#what-is-known-about-the-redirect).
They remain refused destinations. Classification occurs after the original
guard raises and performs no new parse, property read, normalization, request,
follow or retry. The existing parsed hostname operand is not exported or hashed.
Only its fixed class follows the same immutable exception association, child
transfer, first-latched failure and bounded summary/overflow projection. A
missing or unrelated predicate, another stage, an earlier first failure, or a
forged exception attribute cannot attach this class. Optional diagnostic failure
yields `UNAVAILABLE` and preserves the original refusal. No later predicate is
evaluated or inferred from a hostname class.

This is a source diagnostic, not a qualified redirect change. Native attempt 7
retains its original generic refusal; its failed predicate and target remain
unknown. Historical plans 1 through 7 are unchanged. Against the diagnostic-only
revision `1abee085cb19e3c2248ee1038d68d18c87c54b66`, plan 7 rejects the larger
runtime at `SOURCE_FILE_BOUND`, before the unchanged `ACTIVE_CODE_IDENTITY` hash
check. The attempt-8 runner instead rejected those earlier attempt
identities before reading their code inputs. Its new exact plan can pass the
inert source check; its actual native result is retained separately. The current
attempt-13 runner rejects all twelve earlier plan identities before their code
reads. Native execution still requires independent review, fresh required gates
and a separate admission.

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

## Docker observation diagnostic

An original `docker_json` refusal can retain one optional `docker_observation`
object with exactly `endpoint` and `phase`. It applies to the unchanged
`DOCKER_DEADLINE` code and the existing `DOCKER_OBSERVATION` conversion of
`OSError` or HTTP protocol exceptions. This includes a socket timeout that races
the absolute timer; neither error is reclassified. Unrelated errors remain
unchanged and acquire no diagnostic.

Endpoint values are `DAEMON_INFO`, `IMAGE_INSPECT`, `CONTAINER_INSPECT`,
`CONTAINER_SIZE`, `CONTAINER_STATS` and `UNAVAILABLE`. Only the existing fixed
path grammars are classified. Phase values are `BUDGET`, `REQUEST`,
`RESPONSE_HEADERS`, `RESPONSE_BODY`, `RESPONSE_VALIDATE`, `DECODE`, `COMPLETE`
and `UNAVAILABLE`. The phase states where the refusal surfaced. It does not
identify the daemon-side cause; an earlier enclosing deadline can surface during
a Docker GET. No raw path, container/image identity, socket address, query,
headers, body or exception message enters the object.

The immutable pair is associated privately with the exact original refusal.
Mutable exception attributes, later calls and returned summary copies cannot
replace it. Optional classification failure retains the refusal with a bounded
unavailable pair. The existing first-latched ordering is unchanged: metadata is
forwarded at the original latch points, including delayed cleanup latching.
Later cleanup or sampling failures cannot overwrite an earlier failure's
diagnostic, and no context is attached retroactively to attempt 8.

The original timeout calculation, nested absolute timer, GET/read/close sequence,
response-byte bound and unknown-outcome rules remain intact. Classification adds
no transport call, property read, retry, sleep or deadline renewal. The object
fits within the existing 256-KiB report and fixed overflow projection. This is
evidence retention for a future separately admitted operation, not a longer
timeout, stronger host prerequisite or successful candidate trial.

### Container-size sampling context

Plan11 adds a separate optional `docker_sample_context` only when the existing
owned-container size GET in `observe_mutations()` produces an original tagged
Docker refusal. Its exact keys are `location`, `owned_stage` and `prior_driver`.
The context is attached to that same exception before unwinding, then follows
the original first-failure latch and bounded public/overflow projection. Later
samples, cleanup failures, mutable exception attributes and returned copies
cannot replace the original tuple. Optional diagnostic faults preserve the
original refusal. No context is reconstructed for historical attempts.

`location` identifies a fixed coordinator call site, not an inferred daemon
operation. Its values are `COMMAND_POLL`, `CREATE_POLL`, `START_ATTACH_POLL`,
`COMMIT_POLL`, `REMOVE_POLL`, `BASE_LOAD_POLL`, `IMAGE_REMOVE_POLL`, `STAGE_BEFORE`,
`STAGE_WAIT`, `STAGE_COMPLETE`, `RECOVERY_SAMPLE`, `SOURCE_CHECK`, `PREREQUISITES`,
`PRE_CREATE`, `POST_CREATE`, `PRE_BASE_LOAD`, `POST_BASE`, `PRE_COMMIT`, `CLEANUP`,
`FINALIZATION` and `UNAVAILABLE`. Labels wrap the existing sample call and are
restored after success, failure or nesting. Poll labels identify where sampling
occurred; they do not prove that a child was still running at the failed GET.
Before an original failure exists, refusals and control interruptions propagate
through diagnostic setup and prior-driver capture. A control interruption during
restoration after a successful sample also propagates. While an original failure
is unwinding, a later optional restoration fault cannot replace that failure.

`owned_stage` is `ASSEMBLY`, `QUALIFICATION` or `UNAVAILABLE`, taken from the
exact row whose size is being inspected. It names the coordinator's recorded
intent, including a pending create. It does not establish the guest's current
running state, exit result or task outcome.

`prior_driver` is `OVERLAY2`, `OVERLAYFS`, `OTHER` or `UNAVAILABLE`. It classifies
only the exact `Driver` literal from the already received daemon `/info`
response, after all existing platform, resource-limit and runtime checks pass.
It is an earlier observation, not part of the failed response. The first two
values recognize `overlay2` and `overlayfs`; they do not infer an external
containerd version or a general backend implementation. No new request, raw
driver string, daemon dictionary, identifier, path or argument is retained.

The one-second GET limit, nested timer, 1,800-second total, size validation,
512-MiB prospective writable-layer reservation, sampled storage cap and cleanup
are unchanged. A previous driver or size observation never supplies a missing
current response. No unavailable size becomes zero, and no live writer is
treated as quiescent. Docker's pinned [overlay2 size path](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/graphdriver/overlay2/overlay.go)
can walk the writable directory; the [containerd snapshotter contract](https://github.com/containerd/containerd/blob/v2.0.4/core/snapshots/snapshotter.go)
allows active-usage calculation time to depend on resource size. Neither proves
the cause or backend of attempt 10. Its retained peak also excludes a completed
pre-commit sample: the container and assembled-image reservations alone total
1,124,937,728 bytes, above the recorded maximum of 919,893,261 bytes. A failure
within that sample or earlier assembly sampling remains possible.
APK PASS entries can be recovered from guest records after the failure and do
not locate the failed sample or establish how far normal control flow reached.

The integrated `zpm/dependencies.py` includes the separate paired-preflight
opt-in delivered on main. This frozen native recipe omits that option, retaining
its original dependency/import path. Plan11 binds the actual integrated source
instead of reusing the consumed plan10 identity. This is source evidence only;
native qualification still requires a separately admitted actual run.

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
through the same fixed local socket, with one bounded response, no retry and
the existing one-second/original-deadline limits. The versioned contract requires
observed Docker server 28.0.4 and API 1.45 before candidate work. Its
[pinned stats implementation](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/stats.go)
collects one immediate sample, and its
[HTTP route](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/api/server/router/container/container_routes.go)
admits the parameter on that API. The successful path does not populate name/ID;
the exact [JSON tags](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/api/types/container/stats.go)
omit those empty strings. Complete owned inspection immediately precedes the
full-ID request. Omitted response identity is allowed under this pinned path;
any supplied ID/name must exactly match. No native response is rewritten.

Require actual nonnegative integer memory usage, exact memory limit, PID count
and CPU total; Boolean/type aliases and resource-limit violations refuse.
Validate every present counter before classifying a missing one. Identity,
type and limit refusals stay fatal and are not replaced by a later exit. Only
missing counters may join a separate full owned/configuration inspection proving
`Running=false` with `exited` or `dead` status. That observation yields no new
sample, preserves prior counters and retains the original reservation. Empty
stats are never a zero-usage live sample. The
[sixth outcome](native-alpine-evidence/attempt-6/outcome.md) keeps the native
payload uncertainty separate from this source proof.

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

### Storage observation diagnostic

The [selected source correction](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6898312803)
adds optional `storage_observation` only when `STORAGE_OBSERVATION` is the
original first failure. It preserves the three existing refusal branches:

| Field | Closed meaning |
| --- | --- |
| `operation` | `WALK_ENUMERATION`, `ENTRY_LSTAT_MISSING`, `ENTRY_LSTAT_ERROR`, or `UNAVAILABLE`. A missing entry is classified only after the unchanged reservation guard refuses. |
| `root_role` | `TRIAL`, `HARNESS`, `ZPM_SOURCE`, `OPENJULES_SOURCE`, `POLYPHONY_SOURCE`, or `UNAVAILABLE`, from the already selected root operands. |
| `subtree` | A fixed lexical class for trial inputs, work/staging/installation directories, host temporary files/logs, or source Git metadata/files. Unknown context is `UNAVAILABLE`. |
| `errno_class` | `ENOENT`, `EACCES`, `EPERM`, `ENOTDIR`, `EIO`, `ELOOP`, `ESTALE`, `OTHER`, or `UNAVAILABLE`, from the built-in `OSError` field. No overridden exception accessor or formatting supplies the value. |

Root/subtree labels classify already-known lexical operands. In particular,
`PREPARATION_STAGE` matches the direct `work/.zpm-prepare-*` prefix. It establishes
no producer identity, ownership proof, actual guest progress or accounting
exception. The walk callback has no local entry operand, so its subtree stays
`UNAVAILABLE`; it does not read an exception filename. Paths, filenames, hashes
of arbitrary values and raw exception data are neither retained nor exported.

An immutable private weak association binds the context to the original refusal.
Only that first failure can carry it into the closed summary and bounded overflow
fallback; later cleanup failures cannot replace it. Invalid or unavailable
classification yields fixed unavailable values, and optional diagnostic failure
cannot replace the original refusal. Control exceptions from the original walk
or lstat operation still propagate unchanged.

There is no new syscall, read, traversal, retry, exclusion, allowance or deadline.
Known temporary slots keep their full reservations; a missing unreserved entry
still refuses. The local fixture exercises an actual atomic directory rename
between enumeration and lstat while the same retained bytes survive. This is a
source-supported failure shape, not a diagnosis of native12 or native
qualification. A behavioral accounting amendment still needs exact bounded
producer and ownership evidence.

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
cleanup uncertainty. The frozen artifact identities remain complete, but attempt
11 did not retain its rejected redirect destination. Qualification requires all
eleven checks to pass in one admitted attempt; partial APK and cleanup successes
from earlier attempts do not establish that result.

## Implemented source boundaries and retained outcomes

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

The fixed CLI requests remain drop `ALL` and assembly additions `CHOWN`,
`DAC_OVERRIDE`, `FOWNER`. Docker CLI 28.0.4 canonicalizes those additions before
the create request to `CAP_CHOWN`, `CAP_DAC_OVERRIDE`, `CAP_FOWNER`; Engine API
1.45 returns that stored HostConfig. The inspect check requires this exact list
and exact `ALL`, without normalizing observed names or deduplicating a response.
For qualification, only an explicit null or empty list represents no additions;
a missing field or falsey scalar refuses. These are observation spellings, not
new capabilities. The [pinned source proof and failed fifth outcome](native-alpine-evidence/attempt-5/outcome.md)
remain separate from native qualification. The corrected fixture also observes
the created container while its CLI response is still pending. Full reservation
and `mutation_confirmed=false` survive that window. If the reply is lost, exact
owned removal and absence do not erase the original uncertainty or permit a
cleanup PASS.

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
