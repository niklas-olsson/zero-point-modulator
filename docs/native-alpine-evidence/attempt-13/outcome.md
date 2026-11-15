# Native Alpine attempt 13: missing installation-environment entry

**Actual native result: FAILED. Native qualification remains false; owned cleanup passed.**

[Original run 43742961182](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43742961182),
attempt 1, executed [source `b74ca07a767b63c184b7f91685ba996490a2ce16`](https://github.com/niklas-olsson/zero-point-modulator/commit/b74ca07a767b63c184b7f91685ba996490a2ce16)
under [exact admission 6898643009](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6898643009).
The consumed [plan13](../../../tests/contracts/native-alpine/active-plan-13.json)
is 9,263 bytes, SHA256
`17a4668b9bc5ac22b345dd3d769ccfdf05797ae60e7500fac05ba1db5bfd1324`.
Its original ref stays at that source and admits no replay.

[summary.json](summary.json) is the exact original artifact member.
[run.json](run.json) contains separate derived custody metadata. This outcome
interprets those originals; it is not a replacement native receipt.

## Observed result and uncertainty

The first failure is `STORAGE_OBSERVATION`, with the new closed context:

| Field | Original value |
| --- | --- |
| Operation | `ENTRY_LSTAT_MISSING` |
| Root role | `TRIAL` |
| Lexical subtree | `INSTALLATION_ENVIRONMENTS` |
| Errno class | `ENOENT` |

This identifies an entry enumerated under the installation-environments subtree
whose subsequent `lstat` raised `FileNotFoundError`, after the unchanged known-slot
reservation guard refused it. The exact entry, producer and cause were not
retained. The lexical label proves neither ownership nor guest progress.
It does not establish a preparation-stage rename, a pip/byte-compilation action,
an interpreter defect or a diagnosis of the earlier native12 operation.

| Original check | Result |
| --- | --- |
| Artifact bytes, APK trust, base-image identity | PASS |
| APK transaction and installed APK inventory | PASS |
| Host `interpreter_identity` stage | FAIL |
| Production prepare/verify, sealed bootstrap, native interfaces, repeat bytes | NOT_RUN in the retained report |
| Owned cleanup | PASS |

The host stage remains coarse: it is set before qualification-container
creation/start and spans the guest journey. A still-`RUNNING` guest result is
rejected before partial checks are copied. Thus the labels do not establish an
interpreter mismatch or that later guest work had not started. Guest progress
beyond the last accepted result remains unknown.

Source job 132899932487 passed 212 fixtures, source preflight and exact admissions.
Native job 132899994782 exited 1 and uploaded the summary successfully. There were
no cleanup failures, no pending mutation reservations and no report truncation.
Source and ordinary installed checks cannot convert this failure into a pass.

## Acquisition and resource observations

All 173 fixed artifacts completed in 178 requests with four existing CloudFront
redirects. Received body bytes were 209,315,064, comprising 209,309,575 binary
input bytes and 5,489 token bytes. Headers were 178,317 bytes and framing 13 bytes;
metadata reservation was 11,665,408 and body reservation 228,403,438 bytes.
No new origin, retry or credential route was admitted. These observations do not
identify native11's unretained redirect target or guarantee later vendor routing.

| Observation | Retained value |
| --- | ---: |
| Elapsed, including cleanup | 33,591 ms |
| Samples / maximum observed gap | 163 / 465 ms |
| Peak sampled logical owned storage | 1,635,609,659 bytes |
| Peak host RSS | 253,612,032 bytes |
| Peak container memory / PIDs | 217,108,480 bytes / 5 |
| Minimum observed host available memory | 15,338,704,896 bytes |
| Command-output bytes | 77,857 |
| Final pending mutation reservations | 0 |

These values are observations, not minimum machine requirements or a physical
storage quota. The existing sampled-file and conservative image-size scope is
unchanged. Aggregate owned cleanup does not establish separately unreported
per-response HTTP closure.

The report identifies Ubuntu 24 runner image 20261111.370.1, kernel
7.0.0-1017-azure x86_64, Python 3.12.3, Git 2.56.1, OpenSSL 3.0.13 and Docker
client/server 28.0.4 with Engine API 1.45. The candidate remains Alpine 3.24.4 /
official CPython 3.12.16 with the original ZPM/OpenJules/KP revisions in the summary.
No service, managed task, inference or public distribution is qualified here.

## Original custody

Root and an independent nonauthor agent acquired the archive and summary
separately. Original logs bind the harness and all three candidate checkouts.
The archive digest matches GitHub metadata and the original upload log.

| Original | Bytes | SHA256 |
| --- | ---: | --- |
| Artifact 14173541224 ZIP | 1427 | `d011249ad175bc62dfac6274b93d564d5b6fab993392d844ff0ae38fe3208656` |
| Sole regular member `summary.json` | 2282 | `283b1016cb908c15c1848da2bacce7eb6175f33adad65645a158bb78265e0d9d` |
| Decoded native-job log, original UTF8 BOM retained | 44622 | `8d6d921e0d74fcb45c5cd077b103d210e0fd7692b1d0d4e504fabcaad6ca2c85` |
| Decoded source-job log, original UTF8 BOM retained | 67039 | `60c0aae3c1454fc25b6af3eb7b14e3c79a8e406d6b6bb626856af0e4a580ea61` |

## Next decision and acceptance

The diagnostic establishes a narrower refusal category, without changing the
accounting decision or supplying an exact producer-path bound. The next activity
is an evaluation of the pinned environment producer and a sound accounting
contract under the unchanged 4-GiB total. Static wheel sizes alone are not a
proven upper bound for arbitrary build/import writes. Missing entries cannot
silently become zero, and a broad environment prefix is not ownership evidence.

No behavioral amendment or next native operation is admitted by this outcome.
All thirteen failed records, plans and consumed refs remain retained. Issue 63 and
PR69 remain open until actual acceptance supplies all eleven native checks and
owned cleanup. A separate source review, exact admission and original result are
required for any later operation; no retry or distribution claim follows here.
