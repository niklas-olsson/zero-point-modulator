# Native Alpine attempt 8: Docker observation deadline

Agent-authored by ChatGPT `/root/zpm_investigation`, coordinating `/root`,
15 November 2026. **FAILED; installed Python and interfaces remain unqualified.**
The APK transaction and installed APK inventory passed for the first time in
this candidate sequence. The first retained failure was `DOCKER_DEADLINE`;
owned cleanup passed. The exact Docker endpoint, transport phase and daemon-side
cause were not retained and remain unknown.

## Original run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43715051395, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43715051395) |
| Harness commit / tree | `bf9bfaf8f92d9069c7893c905c00912569d68c9a` / `abfd72e2496a5615124e22711628754c8eb53287` |
| Original plan | [active-plan-8.json](../../../tests/contracts/native-alpine/active-plan-8.json), 9,262 bytes; SHA256 `07788eb17025b3eb73fffc6da808b6512949325359ea1ba4a4218ddeed8644d2` |
| Source / native jobs | `132814651712` PASS / `132814728236` FAIL |
| Artifact | `14162860038`, 1,332-byte ZIP; SHA256 `dee0719756936ff5cb699c42b6ca951bcd83a8be0f8676b3523be3cd8b714ffb` |
| Original summary | [summary.json](summary.json), 2,140 bytes; SHA256 `839c2bb9e08946776a45358280ed81de12c1eed6787289bcbda379ba4ccb4e15` |
| Decoded native job log | 44,617 UTF-8 bytes including the initial byte-order mark; SHA256 `1b4c13c0c034ebda33d7882dd59a0a13d78f8cc21fb2a7c2cba4ebde7db985de` |
| Derived custody | [run.json](run.json), coordinator metadata rather than a native receipt |

The original ZIP digest matches live GitHub artifact metadata. Its sole ordinary
member matches the retained summary byte-for-byte. The investigator independently
hashed the coordinator's ZIP, summary and decoded log and checked live run/job
metadata. Native execution exited 1 and summary upload succeeded. The original
artifact expires 29 November 2026. Only the unchanged closed summary and derived
metadata are committed; no private command log or installation is published.

## Actual checks and resources

All 173 artifacts completed in 178 requests with four accepted redirects and
209,315,063 body bytes. Artifact bytes, APK trust, base-image identity, APK
transaction, installed APK inventory and owned cleanup passed. Interpreter
identity, production prepare/verify, sealed bootstrap, native imports/interfaces
and repeat bytes remain `NOT_RUN`. The overall result is `FAILED`, with
`native_execution_qualified: false`, no cleanup failures and zero pending
mutation reservations. Elapsed time was 23,679 ms.

The host reported Docker client/server 28.0.4, API 1.45, Python 3.12.3 and Ubuntu
runner image `20261111.370.1`. Observed peaks were 230,350,848 bytes host RSS,
919,742,987 bytes logical owned storage, 204,816,384 bytes container memory and
two container PIDs. Minimum available host memory was 15,438,008,320 bytes;
maximum sampling gap was 1,044 ms across 139 samples. These observations do not
establish a minimum host size, instantaneous peaks, or an exhausted resource cap.

## What the source establishes

`docker_json` applies the existing one-second GET ceiling within the original
work or cleanup deadline. `DOCKER_DEADLINE` is shared by its budget check and
absolute-timeout boundary. Several image, container, size and stats observations
use that function. The source stage ordering places the work failure before the
qualification stage assignment, but completed assembly checks may be recovered
by the exception handler. They do not identify the failed GET or prove that the
normal post-assembly sequence reached image commit.

The generic code and 1,044-ms sample gap do not establish a daemon defect or
authorize a longer timeout. No exact endpoint, transport phase, response or
command chronology can be reconstructed from the retained summary or job log.

## Separate prospective source follow-up

The [ninth plan](../../../tests/contracts/native-alpine/active-plan-9.json) binds
a closed endpoint-family and transport-phase diagnostic at the original Docker
refusal boundary. It preserves existing error codes, first-latched failure
ordering, requests, deadlines, cleanup, candidate revisions and resource limits.
It does not diagnose this historical failure. All plans 1 through 8 remain
unchanged. Native attempt 9 is `NOT_RUN`; independent review and fresh source
gates precede any separate exact admission. No automatic retry, timeout increase,
resource expansion or qualification follows from this source change.
