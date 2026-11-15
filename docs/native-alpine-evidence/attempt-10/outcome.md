# Native Alpine attempt 10: container-size observation deadline

Agent-authored by coordinating ChatGPT `/root`, with independent evidence review
by `/root/transition_integration`, 15 November 2026. **FAILED; native installation
and interfaces remain unqualified.** The original first failure is
`DOCKER_DEADLINE`, with the closed diagnostic `CONTAINER_SIZE / RESPONSE_HEADERS`.
Owned cleanup passed. This outcome admits no retry or changed limit.

## Original evidence

| Evidence | Identity |
| --- | --- |
| Actual run | [43724871728, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43724871728) |
| Harness commit / tree | `6537476681fafcf1d35bd4c4a52a4241192b200f` / `51904abd2a09f9be2f8a5ef3582f8ae7a7517756` |
| Exact plan | [active-plan-10.json](../../../tests/contracts/native-alpine/active-plan-10.json), 9,263 bytes; SHA256 `8ce3ef40b09add5f2371c026229d95f80c51d06e13e86bdfcc7d872650053440` |
| Source / native jobs | `132844521234` PASS / `132844576780` FAIL |
| Original archive | Artifact `14165108993`, 1,380 bytes; SHA256 `1218320970c8da2458bfd137693a39ac0079c50922801b149f9e417f63cad4d8` |
| Original summary | [summary.json](summary.json), 2,219 bytes; SHA256 `985a8538ced06dc827e9dd3505d55a67af0b9cf401c266b12b02385584183a4a` |
| Decoded native log | 44,622 UTF-8 bytes, including its initial byte-order mark; SHA256 `a1d06af2a222bd3bf7650b85553d49d6505b94d9cd0f855694e79e786c9bbc72` |
| Derived custody | [run.json](run.json), coordinator metadata rather than a native receipt |
| Recorded outcome | [ZPM #63](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6896214795) |

Root and the independent reader verified the archive against live metadata and
the upload log. Its sole ordinary member matches the retained summary exactly.
Source, plan, immutable run reference and attempt agree. The source job passed
165 tests, its inert preflight and exact plan admission. The native step exited
1 and uploaded its summary successfully. Only the unchanged closed summary and
derived custody are committed; the private decoded log and archive are not.

## Actual checks and limits

Artifact bytes, APK trust, base-image identity, the native APK transaction,
installed APK inventory and owned cleanup passed. Interpreter identity,
production prepare/verify, sealed bootstrap, native imports/interfaces and
unchanged repeat bytes remained `NOT_RUN`. Native qualification is false;
cleanup failures are empty and pending mutation reservations are zero.

All 173 artifacts completed in 178 requests with four approved CloudFront
redirects and 209,315,064 body bytes. No OCI refusal or hostname classification
occurred. This result supplies no evidence for widening redirect origins, and
does not identify earlier attempts' unknown targets.

The report records 21,629 ms elapsed, 132 samples, a 1,045 ms maximum sample gap,
231,882,752 bytes peak host RSS and 919,893,261 bytes peak logical owned storage.
Peak container memory was 219,443,200 bytes and peak container PIDs were two.
These are sampled observations, not physical storage quotas or a cause diagnosis.
The host reported Docker client/server 28.0.4, API 1.45, Python 3.12.3 and Ubuntu
runner image `20261111.370.1` with kernel `7.0.0-1017-azure`.

## Observed boundary and next investigation

The source maps the endpoint to the fixed owned-container inspection GET with
`size=true`. The failure surfaced after sending that request and while awaiting
response headers, under the existing maximum one-second observation bound and
the original work/cleanup deadline. The report does not identify the particular
container, assembly versus qualification stage, daemon-side operation or cause.
Native8 cannot be retrospectively assigned this endpoint.

The selected next investigation concerns Docker 28.0.4/API 1.45 size inspection,
prospective storage reservations and observation during owned mutations. A
correction needs source evidence, preserved storage/ownership/cleanup checks,
independent review and a new exact plan. Unavailable size cannot become zero;
this result does not authorize a timeout increase, dropped observation, changed
authority, origin expansion or another native operation.
