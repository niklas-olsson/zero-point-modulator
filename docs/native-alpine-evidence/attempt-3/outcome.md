# Native Alpine attempt 3: acquisition observation refused

Agent-authored by ChatGPT `/root/assess_polyphony`, 14 November 2026.
**FAILED; native installation and interfaces remain unqualified.** The fixed
anonymous token and four permitted OCI redirects completed. The attempt then
refused `INPUT_CHANGED` during acquisition, before any candidate container or
package execution. Owned cleanup passed.

## Exact run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43621319674, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43621319674) |
| Harness commit / tree | `b2ab51a388e9d6dc0b677570a7e732da3b3a6dde` / `abfbf60a5822725f58c3ef1ec4aaf795a8aaff96` |
| Active plan | [active-plan-3.json](../../../tests/contracts/native-alpine/active-plan-3.json), 6,242 bytes; SHA-256 `6da9d7727ae89c35ac9d26c6437a5a3c549efb07961d43309cf619903520ea72` |
| Source / native jobs | `132527438658` PASS / `132527489644` FAIL |
| Artifact | `14124107687`, 1,303-byte ZIP; SHA-256 `fcc3e18ce91d43d3f01ac0ac973d42dbcd59c75cce1a8b145f387849042a143d` |
| Original closed summary | [summary.json](summary.json), 2,127 bytes; SHA-256 `ef90332e6de20cc5e1434fc65dd896c3c465d49cf78aba9c99afc7728d097fdb` |
| Decoded native job log | 44,624 UTF-8 bytes; SHA-256 `fd5f9208ed069b57d82dde3b274932bb48b731aebaba6e38c59df36db135b35b` |
| Derived binding | [run.json](run.json), coordinator metadata, not a native receipt |

The source investigator freshly fetched run/job/artifact metadata and the decoded job log.
The live artifact digest matches the retained ZIP; its sole `summary.json` member
matches the original summary exactly. The newly fetched UTF-8 log matches the
retained bytes, including its initial byte-order mark. The native command exited
1 and uploaded its summary. Binary inputs, private installation data and the full
job log are not republished in this source record. The Actions artifact is due to
expire on 28 November 2026; the exact closed summary remains here.

The third admission followed passing ordinary source workflows
[43620125503](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43620125503)
and [43620130896](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43620130896),
and dedicated source workflows 43620125561 and 43620130856. Those source checks do
not turn this failed native attempt into a qualified result.

## Observed outcome and limits

The summary records 102 completed artifacts out of the fixed 173, 108 requests,
132,038,377 observed body bytes, 151,204,147 reserved body bytes, 7,077,888 reserved
metadata bytes, 92,555 observed response-header bytes and 13 token-framing bytes.
These observations cover the five permitted acquisition origins. No automatic
retry, alternative artifact or candidate execution followed the refusal.

The first failure is `INPUT_CHANGED`. **The failing path and its before/after
metadata were not retained.** This evidence cannot identify the particular read
or prove that the race described below caused the hosted failure. The source and
local reproduction establish a concrete defect consistent with that failure.

The host reported Ubuntu image `20261111.370.1`, kernel `7.0.0-1017-azure`, Python
3.12.3, Git 2.56.1, OpenSSL 3.0.13 and Docker client/server 28.0.4 through API 1.45,
UID/GID 1001. Host Docker identity reads ran. No candidate container, package,
interpreter, installation, service, task, model or provider executed.

The report records 5,692 ms before final writing, 66 resource samples, a maximum
sample gap of 292 ms, sampled peak host RSS of 68,808,704 bytes, peak logical owned
storage of 151,275,590 bytes and minimum available host memory of 15,755,304,960
bytes. These are sampled observations, not complete CLI lifetime or physical
storage measurements. Container memory/PID/CPU values remain zero.
`artifact_bytes=FAIL`, `owned_cleanup=PASS`, the remaining nine checks are
`NOT_RUN`, the cleanup-failure list is empty and no mutation reservation remains.

## Source-supported race and bounded correction

The unchanged immutable-input reader, `candidate.read_bounded`, compares the
opened descriptor's size, modification time, change time and mode before and
after reading. Its `INPUT_CHANGED` refusal is appropriate for immutable source
and artifact files. The acquisition observer also used that reader on the live
`acquisition.json` record. Its sole acquisition subprocess publishes a new 0600
file with `atomic_private` and `os.replace`. The two guest-phase observers used
the same reader while their fixed guest publisher replaced those records.

Python documents [atomic replacement](https://docs.python.org/3.12/library/os.html#os.replace)
and distinguishes [content modification time from metadata change time](https://docs.python.org/3.12/library/os.html#os.stat_result.st_ctime_ns).
The new real-filesystem regression retains the old open descriptor while the
actual publisher replaces its pathname. The descriptor still returns the old
complete bytes with the same device, inode, size, mtime, mode and owner. Its link
count changes from one to zero and its ctime changes. The unchanged immutable
reader refuses that reproduction with `INPUT_CHANGED`.

Attempt 4 adds a separately code-bound snapshot reader for exactly three live
records, with their existing bounds: `acquisition.json` at 65,536 bytes and
`work/assembly-phase.json` / `work/qualification-phase.json` at 8,192 bytes each.
It performs one bounded read through one retained no-follow descriptor and never
reopens the successor or retries. The file must be regular, mode 0600, owned by
the invoking UID/GID, with link count zero or one. Device, inode, size, mtime,
mode, UID and GID must remain identical; the returned length must match the
original size. Ctime may advance only with an observed link-count retirement
from one to zero. Stable link count, including an inode already unlinked before
the first fstat, requires unchanged ctime. All other observed identity changes,
hard links, in-place mutation, short reads and missing/nonordinary files refuse.

The assembly guest sets the target UID/GID and 0600 mode before publication; the
qualification guest already runs as that same UID/GID. The original work or
cleanup observation deadline brackets the read without renewal. Existing JSON,
attempt, phase, monotone-progress and resource validators still decide whether
the complete old-or-current record is admissible. This contract relies on those
fixed sole publishers; it is not an immutable-file proof against a malicious
same-UID writer.

All artifact/source readers remain unchanged. Child completion files are read
after the original child is reaped. Successful guest-result reads follow a
confirmed container exit. The existing failure path may attempt a strict guest
result read before cleanup; a changed or incomplete result is ignored there
without clearing the first failure. That best-effort diagnostic path receives no
snapshot exception.

The distinct [fourth plan](../../../tests/contracts/native-alpine/active-plan-4.json)
binds only this correction and its new work/upload identity. Plans 1, 2 and 3 and
all original summaries remain unchanged. The 173 inputs, source tuple, guest
commands, authority, resource/output caps and whole-attempt deadlines are fixed.
Independent review and fresh hosted gates precede any separately admitted fourth
attempt. Local fixtures and `--check-plan` are not a native result.
