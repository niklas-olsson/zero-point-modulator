# Native Alpine attempt 4: storage observation refused

Agent-authored by ChatGPT `/root/assess_polyphony`, 14 November 2026.
**FAILED; native installation and interfaces remain unqualified.** All 173
declared acquisitions completed, including the fixed anonymous token and four
permitted OCI redirects. The first recorded failure is `STORAGE_OBSERVATION`.
The exact failed path and cause were not retained. Owned cleanup passed; no
candidate container, package transaction or interpreter executed.

## Exact run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43627350916, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43627350916) |
| Harness commit / tree | `7a94c09eba16c2fb6095077f3ac254a115ba19bb` / `4fb5d6bcace3c4ace8fa1811fb9d1bfc6594925e` |
| Active plan | [active-plan-4.json](../../../tests/contracts/native-alpine/active-plan-4.json), 6,885 bytes; SHA-256 `929ffd1c1b6b8bcd471673e0a8fdc1b78b3a6809aa96618411a1008399c074c0` |
| Source / native jobs | `132546117242` PASS / `132546202937` FAIL |
| Artifact | `14126597521`, 1,318-byte ZIP; SHA-256 `88367f58a252767c2f226a322345fa521d6a852dab13da748771469039d8579f` |
| Original closed summary | [summary.json](summary.json), 2,138 bytes; SHA-256 `847856544ccdac33b7b35719b497df5af817a56837a1392b3852b18bfba12616` |
| Decoded native job log | 44,616 UTF-8 bytes, including its initial byte-order mark; SHA-256 `cd82035951ac3d64b30132a8b5a7d659796dabaa4c5316897462f30fd3ab4296` |
| Derived binding | [run.json](run.json), coordinator metadata, not a native receipt |

The investigator independently fetched run/job/artifact metadata, the ZIP and
the decoded log. The advertised artifact digest matches the ZIP, whose sole
`summary.json` member matches the retained summary exactly. All three original
files also match the coordinator's independent retrieval byte-for-byte. The
native command exited 1 and uploaded its summary. The Actions artifact expires
28 November 2026; the exact closed summary remains here. Private inputs,
installations and command logs are not republished in this source record.

Admission followed passing ordinary workflows
[43625118827](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43625118827)
and [43625125545](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43625125545),
each with ten passing jobs, and a separate explicit review/admission. Their
success does not turn this failed native attempt into a qualified result.

## Observed effects and uncertainty

The summary records 173 completed artifacts, 178 requests, 209,315,064 observed
body bytes, 228,403,438 reserved body bytes, 11,665,408 reserved metadata bytes,
177,994 observed response-header bytes and 13 token-framing bytes. These cover
the five permitted acquisition origins and four allowed OCI redirects. The
original fixed artifact total remains 209,309,575 bytes; the aggregate body
counter also includes the token response. No retry or replacement artifact
followed this refusal.

`artifact_bytes=FAIL`, `owned_cleanup=PASS`, and the other nine native checks are
`NOT_RUN`. In particular, successful independent APK trust verification is not
established. The runner groups acquisition and host artifact verification under
the `artifact_bytes` stage. Therefore these counters alone do not locate the
failed sample within acquisition completion versus later host verification, or
show which host signature commands completed. The later image mutation and
candidate execution stages were not entered. Host prerequisite Docker identity
reads did run; no candidate container, APK transaction, interpreter, bootstrap,
service, task, model or provider ran.

The host reported Ubuntu image `20261111.370.1`, kernel `7.0.0-1017-azure`, Python
3.12.3, Git 2.56.1, OpenSSL 3.0.13 and Docker client/server 28.0.4 through API 1.45,
UID/GID 1001. The summary records 15,973 ms before final writing, 106 resource
samples, a maximum sample gap of 290 ms, sampled peak host RSS 250,863,616 bytes,
peak logical owned storage 280,158,552 bytes and minimum available host memory
15,454,453,760 bytes. Observed command-output bytes are 69,328. These are sampled
logical measurements, not physical disk quotas, instantaneous peaks or the
complete workflow duration. Container CPU/memory/PID values and outstanding
mutation reservations are zero; cleanup failures are empty. A cleanup PASS does
not mean retained private evidence files were erased.

## Reproduced source defect, without retrodiagnosing the run

The exact executed source's
[`verify_package`](https://github.com/niklas-olsson/zero-point-modulator/blob/7a94c09eba16c2fb6095077f3ac254a115ba19bb/tests/contracts/check_musl_preparation.py)
creates a `TemporaryDirectory` named `signature-*` under `keys.parent`, writes
`signature` and `control.gz`, invokes the fixed OpenSSL verifier, and leaves the
context, which removes those files and the directory. The active runner's
[`logical_files`](https://github.com/niklas-olsson/zero-point-modulator/blob/7a94c09eba16c2fb6095077f3ac254a115ba19bb/tests/contracts/check_alpine_sealed_runtime.py)
concurrently enumerates that same tree and refuses a disappearing nonreserved
entry as `STORAGE_OBSERVATION`.

A controlled local reproduction used the actual helper and storage scanner. It
let `os.walk` enumerate the helper's directory, then allowed the helper's normal
context cleanup to complete before the scanner's `lstat`. The helper completed;
the scanner refused `STORAGE_OBSERVATION`. The crypto callback returned an
explicit synthetic result. No OpenSSL, candidate artifact, network or Docker
execution was needed for this chronology. This establishes a real source defect
consistent with the hosted failure code. It does not establish the missing
hosted path or prove that this was that run's cause.

## Separate fifth source proposal

Attempt 5 explicitly selects retained signature inputs in the shared verifier.
For each of the existing 63 APK digests it exclusively creates one private
0700 directory, with two exclusive, no-follow, ordinary 0600 files owned by the
invoking UID/GID. Existing directories/files, hard links and symlinks are refused
before replacement. The inputs stay linked after success and failure; the
helper performs no early deletion. The ordinary shared musl fixture keeps its
original temporary lifecycle by default.

The maximum signature size remains 1,024 bytes per APK. Each compressed control
stream is contained in its original verified APK. The fixed APK total of
78,915,719 bytes plus 63 signatures gives a conservative retention bound of
**78,980,231 bytes**, at most 63 directories and 126 files. Actual linked bytes
remain counted by the unchanged strict scanner within the original 4-GiB stop.
No missing-path exemption, retry, cap increase, deadline renewal or privilege
change is introduced. Files stay observed through final resource checks and
remain private until the owner retires the entire attempt directory.

The distinct [fifth plan](../../../tests/contracts/native-alpine/active-plan-5.json)
binds the changed helper, host caller, policy and workflow. Plans 1 through 4,
the original summaries, candidate/production tuple, artifacts, authority, guest
commands and every resource/output cap remain unchanged. The new fixtures cover
real enumeration/completion ordering, retained success/failure, original-error
preservation, collisions/links, mode/owner checks, runtime opt-in, original
deadline propagation and refusal, final accounting, and explicit owner
retirement. Source consistency and fixtures are not native evidence. Independent
review and fresh hosted gates must precede any separately admitted fifth run.
