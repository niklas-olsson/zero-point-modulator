# Native Alpine runner source validation

Agent-authored by ChatGPT `/root/assess_zpm`, 14 November 2026. This records
source checks for the [active runner](native-alpine-active-harness.md).
The [first admitted attempt](native-alpine-evidence/attempt-1/outcome.md) failed
at source admission; the [second](native-alpine-evidence/attempt-2/outcome.md)
passed source and host prerequisites, then refused token framing before body
reading. Host Docker identity reads ran; no candidate container, interpreter,
installation or native probe ran. The [third](native-alpine-evidence/attempt-3/outcome.md)
completed 102 artifact acquisitions before `INPUT_CHANGED`; its exact failed
path is unobserved and owned cleanup passed. The
[fourth](native-alpine-evidence/attempt-4/outcome.md) completed all 173 acquisitions
then refused `STORAGE_OBSERVATION`; its exact path and cause remain unknown and
owned cleanup passed. The original checks below retain their source scope.
The [fifth](native-alpine-evidence/attempt-5/outcome.md) passed artifact bytes,
APK trust and base-image identity, then failed assembly capability inspection
with an unknown create outcome and cleanup `FAIL`. The sixth plan requires
fresh review and hosted gates. Earlier source-check sections remain historical.

The original inert plan, 173-artifact recipe and native-member catalog retain
SHA-256 `c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76`,
`e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f` and
`6e8d0bc3e1257625b803a55c6f12282b5275d1beb96ee6d7667ada18779baef0`.
The separate active plan is 6,033 bytes, SHA-256
`8917e1b695e7154c9ecd60f40ab2defcd691ca715da81ae8b23b5dc60ebcb953`.
That exact first plan was admitted once at published source `11fe6e8312ce2f5cdb3e6f6f659be35e27ed70b6`
after both ten-job hosted workflows passed. Its native run failed at source
metadata admission. The file remains unchanged; it is not rebound to later code.
Its exact code manifest includes the workflow and complete local import set.

## Coordinating correction after attempt 1

Agent-authored by ChatGPT `/root`. The new `active-plan-2.json` is 6,033 bytes,
SHA-256 `bc4e5168483bdf372af334a14f6e3e0bc1b58850bd026262cce834274028bced`.
The production tuple, original plan/recipe, guest code, commands, authority,
resource limits and all input identities are unchanged. Only the current host
source and workflow bindings change, with a distinct second attempt/work/output
identity. The first raw summary and its source/plan/run binding are retained.

Seventy-three focused fixtures pass, including a real local Git 2.52.0 reproduction
of the checkout sequence without network or candidate work. The isolated
`--check-plan` command admits this exact source plan with native execution NOT_RUN.
Five added tests cover the inactive three-false sparse file, active extensions,
unexpected values/sections/includes, duplicates, missing fields, bounds, invalid
encoding, links, FIFO/directory metadata and retained indirection refusals.
Rejected metadata descriptors are closed even before a stream can be constructed.
The complete root fixture log has SHA-256
`21a003790d431c2bfeedee0b4152bc32f887cccf28f207dfa9e0a0202958fdf9`.

The supported-checkout explanation follows seven pinned action/Git primary
sources listed in the outcome record. The historical failure code did not name
the rejected relative path, so the exact first-run member remains unobserved.
No corrected native attempt is inferred from source tests or this explanation.

## Coordinating correction after attempt 2

Agent-authored by ChatGPT `/root`. The new `active-plan-3.json` is 6,242 bytes,
SHA-256 `6da9d7727ae89c35ac9d26c6437a5a3c549efb07961d43309cf619903520ea72`.
Both original plans and raw failed summaries remain unchanged. The candidate,
173 inputs, production tuple, guest commands, authority and resource caps remain
fixed. The workflow binds the third attempt's work and summary identities.

Only the fixed anonymous token endpoint gains a bounded bare HTTP/1.1 chunked
reader. The original response stream supplies strict hex sizes and CRLF, with
header plus framing counts bounded by the same 64-KiB reservation. No extensions,
trailers, mixed framing, content compression or alternate endpoint is admitted.
Artifact Content-Length and byte/hash checks remain unchanged. Partial payload
and framing observations are retained before refusal; final framing arriving
beyond the original deadline cannot complete successfully. Token/header contents
are not persisted. The outcome record links the exact CPython source and RFC.

All 81 focused fixtures pass on this exact source, including eight new transport
fixtures. The final root fixture log has SHA-256
`14e876e12719cf4b0f5027405866c4a811f5dd5bb0f9e82c329ad4809a756a71`.
The isolated `--check-plan` command admits the exact plan with native execution
NOT_RUN; Python syntax, workflow YAML and whitespace checks pass. Independent
review and fresh hosted gates remain separate. The new fixtures cover original-stream
framing, mixed/duplicate/old-HTTP refusals, endpoint and artifact isolation,
malformed/truncated bodies, early advertised overflow, shared metadata exhaustion,
partial payload deadline expiry and a final delimiter arriving after the deadline.
No real network, candidate package or container is used by these added fixtures.

## Snapshot correction after attempt 3

Agent-authored by ChatGPT `/root/assess_polyphony`. The first source snapshot of
`active-plan-4.json`, before its unactivated main integration below, was 6,740 bytes, SHA-256
`11d09421c5bd419cffe7be0624bd961bd0fadc3c76606dcf06f64f548ec51daf`.
It binds the closed three-path live-record reader and the distinct fourth
work/upload identity. The immutable candidate reader, guest publisher, original
three plans, exact failed summaries, production sources, commands and all caps
remain unchanged. The [third outcome](native-alpine-evidence/attempt-3/outcome.md)
separates the freshly verified hosted failure from the locally reproduced
atomic-publication race; it does not invent the missing failed path.

All **93 focused fixtures PASS**, including 12 new snapshot checks. The real
filesystem cases use the actual host and guest atomic publishers: a single open
inode retires from link count one to zero with unchanged content/mtime, and a
replacement before the first fstat leaves an already-unlinked stable inode.
The unchanged immutable reader refuses the same replacement. Other fixtures
reject in-place writes, restored-mtime changes, owner/group/mode/identity drift,
hard links, wrong paths, nonordinary files, oversized/short reads and expired
original deadlines. Actual caller fixtures retain acquisition and both guest
phase monotonicity. No Docker or candidate acquisition occurs in these checks.

The exact isolated `--check-plan` command returns `SOURCE_PLAN_ADMITTED` with
native execution `NOT_RUN`. Independent review, fresh hosted source gates and
any fourth native admission remain separate requirements.

The focused fixture log is 18,034 bytes, SHA-256
`7670e68e3566c1073d5d5c0cae521580e75db3acdc2e41a37d25763eabfd6ce7`.
Python syntax, workflow YAML and whitespace checks pass. The workflow differs
only in its fourth plan/work/upload identity. All three historical plans, the
immutable candidate reader and the guest program were compared byte-for-byte
against the actual parent `b2ab51a388e9d6dc0b677570a7e732da3b3a6dde`.

The full local suite ran **687 tests in 118.954 seconds: 686 passed and one
errored**. The unchanged
`test_real_owned_process_cohort_uses_linux_counters` could not read
`/proc/<pid>/task/<pid>/children`. The same single test failed in a separate clean
worktree at the actual parent commit. This preserves the existing environment
limitation; the required hosted full suite is not waived. No production observer
or fixture gate was weakened to turn it green.

Offline wheel construction, installation into a new isolated environment,
installed `zpm --help`, example validation and the installed module path all
passed. The local wheel is 83,051 bytes, SHA-256
`1a8962f16190aabf30158729e646ff11bbdd5608f9322359ade67b2a6181a01a`.
Those checks use host CPython 3.12.14 and do not qualify the frozen Alpine
CPython 3.12.16 candidate or publish a release.

## Main integration before attempt 4

The fourth attempt had not been activated when main advanced to
`e8cb65ea7c1717cc5b49c1a6199960a51ac99b9f` with the role-local installer.
That actual main is merged into the reviewed fourth-attempt source
`ba9d8137f5d4363f75d5aba6c55550fad0b85c40`, preserving both histories and every
prior plan and outcome. The original three plans remain byte-identical.

The host candidate compiler imports `wheel_identity` from `zpm.dependencies`.
The merged dependency module now imports the pure `zpm.installation_roles`
selector, which imports only the already-bound `zpm.models`. The executing-source
closure therefore gains that one file, and rebinds the changed dependency and
host runner bytes. The active plan is now **6,885 bytes**, SHA-256
`929ffd1c1b6b8bcd471673e0a8fdc1b78b3a6809aa96618411a1008399c074c0`.
Its eleven code identities include the workflow and ten Python source files.
The production guest checkouts, immutable candidate/recipe/member identities,
commands, capacities, authority and acquisition policy remain unchanged. This
integration does not switch the frozen three-service guest to a role-local
deployment or qualify the role installer on Alpine.

All **98 focused fixtures pass** under host CPython 3.12.14. Two new regressions
observe the actual cold host import closure with socket, subprocess and write
effects denied, and refuse a changed imported role selector at exact source
admission. Existing role dependency tests preserve the legacy three-source
version-one plan while keeping each separate role's version-two selection
closed. The isolated `--check-plan` command returns `SOURCE_PLAN_ADMITTED` and
`native_execution: NOT_RUN` for the new hash. An initial local fixture invocation
used system CPython 3.13.5; three role-dependency subcases correctly refused the
unsupported interpreter tuple. Its separate failed log is retained, and no
production check was weakened.

The combined full local suite ran **749 tests in 115.893 seconds: 748 passed
and the same one `/proc/<pid>/task/<pid>/children` read errored**. Both that
measurement helper and its test are byte-identical to the reviewed parent;
the earlier clean-parent reproduction remains applicable. The required hosted
full suite is not waived. Offline wheel construction, fresh isolated
installation, installed CLI help/example validation and installed dependency
and role-selector imports all passed. The 98,587-byte local wheel has SHA-256
`e6b0c8f7d0575d837748717c299bfaf1bd065ae2c6efa28bc02b96db9198bee3`;
it is host packaging evidence, not an Alpine installation or release.

Fresh required hosted gates and independent review of the combined source and
new plan remain required before a fourth native admission. No new native
acquisition, container, guest execution or installed result is inferred here.

## Retained verification inputs after attempt 4

Agent-authored by ChatGPT `/root/assess_polyphony`. The actual fourth run and its
unchanged [summary/custody](native-alpine-evidence/attempt-4/outcome.md) are retained
separately from this source proposal. The exact failing path remains unknown.
The new `active-plan-5.json` is **7,591 bytes**, SHA-256
`017c6ffb7c0a15b8efbc3046da53e3978d45f9b3bb8bda1d264d579f638575cf`.
It binds opt-in retained APK signature inputs, the host caller, distinct fifth
workflow identity and complete unchanged eleven-path executing-source closure.
All previous plans, original summaries, immutable input readers, guest source,
candidate/production tuple, authority, commands and caps remain byte-identical.

All **127 focused fixtures PASS**, including eleven new controlled checks and
the existing shared musl signature/primitive fixtures. The new checks exercise
the actual helper-completion versus parent-enumeration chronology, retained
success and failure, original-error preservation, exclusive directory/file
creation, link/nonordinary collisions, exact mode and owner/group checks,
default temporary cleanup, the native caller's explicit opt-in, unchanged
deadline propagation and expired-deadline refusal, finite catalog accounting,
and explicit owner retirement after the final observation. The added fixtures
use synthetic crypto results; the existing shared verifier fixtures separately
exercise real signatures over locally generated fixture bytes. No candidate
artifact acquisition, Docker or Alpine code ran locally.

The focused log is 24,820 bytes, SHA-256
`81ec931b2545dedb741d84f4e9c460fecebb69e670a0ce357430fa456dbab9f2`.
The isolated `--check-plan` returns `SOURCE_PLAN_ADMITTED` with native execution
`NOT_RUN`. Python syntax, workflow YAML and whitespace checks pass. Source-only
branches and ordinary pull requests remain inert; no fifth run reference or
manual admission has been created.

The full local suite ran **760 tests in 107.257 seconds: 759 passed and the same
existing `/proc/<pid>/task/<pid>/children` observation errored**. The measurement
helper and test remain byte-identical to the actual parent `7a94c09`; the earlier
unchanged-parent reproduction still applies. The raw full log is 149,683 bytes,
SHA-256 `5d14e6685467d84e6d569464072c2bb045bdd07679b30d275a83a1cd8f1af4f2`.
The required hosted full gate remains required.

Offline wheel build, fresh isolated installation and installed CLI help/example
validation pass under host CPython 3.12.14. The local wheel is 98,587 bytes,
SHA-256 `6bd3360c8106f1835102c0060be6cf23d8ff0a83fbcd12fc82a1ff27fc2cb380`.
This is host packaging evidence, not an Alpine installation or release.
Independent review, fresh hosted gates and a separately admitted plan are still
required before any fifth native run.

## Exact inspect representation after attempt 5

Agent-authored by ChatGPT `/root/assess_polyphony`. The original fifth
[summary/custody](native-alpine-evidence/attempt-5/outcome.md) remains `FAILED`;
its actual capability payload and precise failed call site remain unknown. Ten
pinned Docker CLI/Engine 28.0.4 primary files and a controlled exact-source
reproduction establish the inspect-spelling defect independently of that
unretained payload.

The distinct `active-plan-6.json` is **8,235 bytes**, SHA-256
`11907aa13d2300c092d69ff02cf7e1ce8c17aa38c414ec71bdd9b6ca284c55d6`.
Its eleven-path executing closure changes only the host runner and workflow.
The original authority, requested CLI capabilities, guest code, commands, caps,
all five prior plans, artifact catalog and uncertainty/cleanup behavior remain
unchanged. Only the explicit canonical capability-inspection policy and sixth
attempt/work/upload identity are new.

The workflow's four-module source lane passes **111 tests**, including five new
controlled capability/custody cases. They use literal Docker canonical response
data rather than copying the authority's CLI spelling. They exercise exact
positive assembly and empty qualification forms; missing, falsey, noncanonical,
extra, duplicate and reordered refusals; the real owner create path's observation
before CLI acknowledgement; and confirmed matching removal that still retains
an unknown original create result and cleanup `FAIL`. No Docker daemon,
candidate artifact or native guest is used. The 21,750-byte focused log has
SHA-256 `ee09244a430fec0e86a69ccc5ad43ee67e747dfe858c4c34289ddad9bc6f004b`.
The isolated `--check-plan` reports `SOURCE_PLAN_ADMITTED` with native execution
`NOT_RUN`. Python syntax, workflow YAML, all five shell blocks and whitespace
checks pass.

The full local suite ran **765 tests in 111.383 seconds: 764 passed and the same
existing `/proc/<pid>/task/<pid>/children` observation errored**. The measurement
helper and test are byte-identical to actual parent `a4565d9`; the earlier
unchanged-parent reproduction remains applicable. The raw 150,759-byte full log
has SHA-256 `aa318ed80691223c010d294e8fb34ee3787ec70a0f6103c99e86db2ae155db19`.
This is not a waiver of the required hosted full gate.

Offline wheel construction, fresh isolated installation, installed CLI help and
example validation pass. The 98,587-byte host wheel has SHA-256
`c122a2f27fefc51c5fc1b6a59619ee31a3c3389fd4199931049617136ac3f857`.
It is local packaging evidence, not an Alpine installation or release.
Independent review and fresh hosted gates must precede a separate sixth-plan
admission. This source freeze activates no further native work.

## Original completed-source local checks

| Check | Actual result and limit |
| --- | --- |
| Three focused candidate/runtime test modules | 68 tests PASS on the corrected source. Synthetic protocol/daemon/guest records and controlled ordinary host children only. |
| Isolated script `--check-plan` | Source plan admitted with native execution `NOT_RUN`; no directory, socket or child operation. |
| Python syntax, workflow YAML parsing and whitespace | PASS. |
| Full `python -B -m unittest discover -v` | 635 tests in 105.553 s: 634 passed, one existing environment error described below. This run preceded the final two-line elapsed-time refinement and checkout-configuration correction; the final focused set covers both changes and their added fixtures. |
| Offline local wheel build and isolated installation | PASS with CPython 3.12.14, setuptools 84.2.1 and wheel 0.48.0; no package index access. These local packaging tools are separate from the frozen Alpine candidate toolset. |
| Installed `zpm --help`, example validation, installed import path | PASS from the newly installed local wheel. |

The local wheel was `zero_point_modulator-0.1.0-py3-none-any.whl`, 70,934 bytes,
SHA-256 `a8fa596bfd6e5c112d7347b801d66b7ef941587492f79e7581f89bd9745607da`.
It is local packaging evidence, not a release or a qualified Alpine artifact.

The full-suite error was
`tests.test_operational_measurement.ProcessMeasurementTests.test_real_owned_process_cohort_uses_linux_counters`:
this workspace lacks the requested `/proc/<pid>/task/<pid>/children` entry.
The same one-test command reproduced `FileNotFoundError` on untouched baseline
`7cb9306`; its test and implementation files are unchanged by this work. This is
recorded separately from regressions. It is not a waiver of the full hosted CI
gate. Raw local full-suite and baseline logs are retained privately, SHA-256
`b4c1329137da8fd4d990ddbd811e574af7795e473d3554e55746d960a12b7760` and
`44ba573eab1e615c05e610e138fe3d6876c5b15b8f1e94e28f34a3a434268301`.

## Checkout compatibility review correction

Root review identified that the supported `actions/checkout@v4` writes local
`gc.auto=0`. Its `tryDisableAutomaticGarbageCollection` implementation invokes
only `git config --local gc.auto 0` ([action source](https://github.com/actions/checkout/blob/v4/src/git-command-manager.ts));
the reviewed source blobs are `2d3513897655e0ea2eb4efc2b2c9d6132a69549f`
and `9c789ac803e4d3b99463037d0ada654b79d8b863`. The runner now admits that
exact optional setting while refusing other values, keys and subsections. Two
new controlled tests cover detached and branch checkout configurations and
negative variants, including an attempted configuration include. The code-bound
plan is refreshed; inputs, caps, workflow and native status are unchanged.

## Ownership mapping and next evidence

The original draft author separately checked the eight fixed probes and the two
sensitive owner calls against actual ZPM `c5fb355` and OpenJules `0dd5bf4` source.
The managed-context probe uses its installed interpreter's `sys.prefix` selector,
without a substitute selector input. Production repeat bootstrap checks retained
completion and the unchanged source/wheel identities before scratch, build or
installation work. Read-only source mounts are compatible with those APIs.
This is a bounded mapping sanity check, not independent approval of that author's
draft, the completed runner or a native outcome.

Root and a reviewer who did not author the completed changes must review the exact
code/plan/commands and source evidence. Only then may one immutable source/plan
activation be admitted. Keep issue #63 open until the actual eleven native checks,
original resource bounds and owned cleanup are demonstrated. The result would
qualify only this tuple's native bootstrap and interfaces, not managed tasks,
services, models, browser execution or a public demonstrator distribution.
