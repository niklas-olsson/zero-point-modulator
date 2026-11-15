# Native Alpine attempt 12: storage observation refused

**Actual native result: FAILED. Native qualification remains false; owned cleanup passed.**

The [original run 43739611680](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43739611680),
attempt 1, executed [source `aa842e8ddecfeb13c3ff8d980230cbcc550728ba`](https://github.com/niklas-olsson/zero-point-modulator/commit/aa842e8ddecfeb13c3ff8d980230cbcc550728ba)
under [exact admission 6898176025](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6898176025).
The consumed plan is [active-plan-12.json](../../../tests/contracts/native-alpine/active-plan-12.json),
9,263 bytes, SHA256 `989ea9f36d1dabdca4cba44e92f10dd26c0d86d69f1d30deac870625b94f375e`.
Its original ref remains at that source. It is not moved, relabeled or replayed.

[summary.json](summary.json) is the exact original artifact member.
[run.json](run.json) contains derived custody metadata. This outcome is an
interpretation of those originals, not a replacement native receipt.

## Observed result and its limits

| Original check | Result |
| --- | --- |
| Artifact bytes, APK trust, base-image identity | PASS |
| APK transaction and installed APK inventory | PASS |
| Host `interpreter_identity` stage | FAIL |
| Production prepare/verify, sealed bootstrap, native interfaces, repeat bytes | NOT_RUN in the retained report |
| Owned cleanup | PASS |

The first failure was `STORAGE_OBSERVATION`. There were no cleanup failures
or pending mutation reservations, and the report was not truncated. Source job
132889644583 passed 196 tests, the inert compiler and exact admissions. Native job
132889714559 exited 1; summary upload succeeded. A green source job does not
qualify this failed native operation.

The stage label is coarse. The
[original host source](https://github.com/niklas-olsson/zero-point-modulator/blob/aa842e8ddecfeb13c3ff8d980230cbcc550728ba/tests/contracts/check_alpine_sealed_runtime.py)
sets `interpreter_identity` before qualification-container creation/start
and leaves it current throughout the guest journey. A still-`RUNNING` guest
result is rejected before partial checks are copied. Therefore the report does
not prove an interpreter mismatch, or establish that prepare/bootstrap had not
begun. Guest progress beyond the last accepted result remains unknown.

`STORAGE_OBSERVATION` combines the `os.walk` error callback, an
unreserved entry disappearing before `lstat`, and another `lstat` error.
The actual operation, path, errno and cause were not retained. No later source
reproduction can retrospectively supply those facts.

## Acquisition and resources

All 173 fixed artifacts completed. The operation made 178 requests, followed four
existing CloudFront redirects and received 209,315,064 body bytes, 177,939 header
bytes and 13 framing bytes. Binary inputs account for 209,309,575 bytes and the
anonymous token response for 5,489. Metadata reservation was 11,665,408 bytes and
body reservation 228,403,438 bytes.

This run passed its original acquisition predicates. It does not identify the
different, unretained destination refused by native11 or prove permanent vendor
routing. No new origin, retry or credential route was admitted.

| Observation | Retained value |
| --- | ---: |
| Elapsed, including cleanup | 34,163 ms |
| Samples / maximum observed gap | 162 / 479 ms |
| Peak sampled logical owned bytes | 1,645,817,802 |
| Peak host RSS | 252,084,224 bytes |
| Peak container memory / PIDs | 239,276,032 bytes / 5 |
| Minimum observed host available memory | 15,348,604,928 bytes |
| Command-output bytes | 77,258 |
| Final pending mutation reservations | 0 |

These are observations, not minimum machine requirements or a physical storage
quota. Logical accounting retains its documented sampled-file and conservative
image-size scope. Aggregate owned cleanup covers owned processes, containers and
images; separately unreported HTTP-response closure cannot be inferred from it.

The report records Ubuntu 24 runner image 20261111.370.1, kernel
7.0.0-1017-azure x86_64, Python 3.12.3, Git 2.56.1, OpenSSL 3.0.13 and Docker
client/server 28.0.4 with Engine API 1.45. The original candidate remains
Alpine 3.24.4 / official CPython 3.12.16, with the same ZPM `c5fb355f…`,
OpenJules `0dd5bf4e…` and KP `6480e363…` pins recorded in the summary.
No managed task, service, inference or public distribution is qualified here.

## Original custody

Root and an independent agent acquired and checked the original archive and
summary separately. Actual checkout logs bind the harness and all three
candidate revisions. The ZIP digest agrees with the original upload log and
GitHub artifact metadata.

| Original | Bytes | SHA256 |
| --- | ---: | --- |
| Artifact 14172360206 ZIP | 1336 | `479879550190f9ab742521944fc9214760d3cc85535bf6b3ae0b6f4717aef900` |
| Sole regular member `summary.json` | 2143 | `d6e4546861e69889dd4b6b76293765edf0dcd7a6e10313519583c1b0ee88e2d0` |
| Decoded native-job log, original UTF8 BOM retained | 44621 | `d1e9466a89cc1cb61c3c43613db5c48a059b476c92a0a8c7678ea6cbc948207d` |
| Decoded source-job log, original UTF8 BOM retained | 63448 | `2f3a3bcab863f390964fc4ceb1b18fb787f551f62cb3779a2b6edd87a4e1757c` |

Both ordinary head/proposed runs 43738112471/43738120807 passed all ten jobs,
including 954 tests per Python version. They are separate installed/source
evidence and cannot convert this native failure into a pass.

## Source follow-up and remaining acceptance

A controlled local reproduction used actual `os.walk` and atomically
renamed a preparation directory after enumeration but before `lstat`.
The same 36 bytes remained intact at the destination, while the unchanged sampler
refused. The pinned production preparation uses this staging/publication shape.
This identifies a source-supported interaction, not native12's actual cause.

[The selected next source unit](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6898312803)
retains fixed storage-error context at the already-failing call. It changes no
syscall, read, retry, accounting rule, exclusion, refusal, deadline, cap or
authority. Source selection alone activates no new operation.

A behavioral accounting amendment requires exact bounded producer-path and
ownership evidence. Ignoring missing paths, accepting broad temporary prefixes
or treating unknown bytes as zero is not selected. All twelve failed outcomes
remain retained. Issue 63 and PR69 remain open until a separately admitted actual
operation satisfies all eleven native checks and owned cleanup. No new
qualification or distribution claim follows from this record.
