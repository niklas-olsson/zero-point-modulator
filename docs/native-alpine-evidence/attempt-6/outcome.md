# Native Alpine attempt 6: stats observation refused

Agent-authored by ChatGPT `/root/assess_polyphony`, 14 November 2026.
**FAILED; native installation and interfaces remain unqualified.** Artifact
bytes, APK trust and base-image identity passed. The assembly transaction failed
with `CONTAINER_STATS_MISSING`; owned cleanup passed. The original response,
rejected field and exact before/after container states were not retained. The
source defect below is consistent with the result, but does not establish that
the missing native payload followed this path.

## Exact run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43634727714, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43634727714) |
| Harness commit / tree | `897ad91299b8ffb6d3a9c37e6864688ff9495af4` / `69dc972c54bc2abc365e028005f1ce9bfc06707c` |
| Original active plan | [active-plan-6.json](../../../tests/contracts/native-alpine/active-plan-6.json), 8,235 bytes; SHA-256 `11907aa13d2300c092d69ff02cf7e1ce8c17aa38c414ec71bdd9b6ca284c55d6` |
| Source / native jobs | `132568916847` PASS / `132568970651` FAIL |
| Artifact | `14129905988`, 1,327-byte ZIP; SHA-256 `8d4737bed352d9f7bba09217b5225116ad0c7d19f2bfa3ff80fc8612b1d06201` |
| Original closed summary | [summary.json](summary.json), 2,133 bytes; SHA-256 `1d60b7dd00f323b644e8827a316d10dd536a2d1c9064434be1c9e048d89e706d` |
| Decoded native job log | 44,616 UTF-8 bytes including its initial byte-order mark; SHA-256 `bc9616f5cc177102c8202b070f78529f3c4ae68559b2cd369f1d9f9359a6815d` |
| Derived binding | [run.json](run.json), coordinator metadata rather than a native receipt |

The investigator independently retrieved the live job/artifact metadata, ZIP
and decoded log. The bounded ZIP matches GitHub's advertised digest and contains
exactly one ordinary `summary.json` member. ZIP, member and decoded log match the
coordinator's independent copies byte-for-byte. The native command exited 1 and
summary upload passed. The private Actions artifact expires 28 November 2026.
Only its exact closed summary is committed here; no command log, candidate binary
or private installation is published.

## Actual result and limits

All 173 fixed artifacts completed: 178 requests, 209,315,064 observed body bytes,
228,403,438 reserved body bytes, 11,665,408 reserved metadata bytes, 178,339
response-header bytes and 13 token-framing bytes. The token and four permitted
OCI redirects passed. The body count includes the token response; the fixed
artifact catalog still totals 209,309,575 bytes. No retry or replacement input
catalog was used.

`artifact_bytes`, `apk_trust`, `base_image_identity` and `owned_cleanup` passed.
`apk_transaction` failed. Installed APK inventory, interpreter identity,
production prepare/verify, sealed bootstrap, native imports/interfaces and
repeat-byte checks remain `NOT_RUN`. There were no final pending mutation
reservations or cleanup failures. Cleanup PASS does not qualify the failed
assembly transaction or establish the unretained container-state chronology.

The host reported Ubuntu image `20261111.370.1`, kernel `7.0.0-1017-azure`, Python
3.12.3, Git 2.56.1, OpenSSL 3.0.13, Docker client/server 28.0.4 and Engine API
1.45, UID/GID 1001. The record reports 20,444 ms before final writing, 129 resource
samples, a maximum sample gap of 405 ms, sampled peak host RSS 253,374,464 bytes,
peak logical owned storage 919,616,628 bytes and minimum available host memory
15,257,083,904 bytes. Command output was 71,498 bytes. Container memory/PID/CPU
counters remained zero because no accepted sample populated them; this does not
establish observed zero container use or an instantaneous peak.

## Pinned source diagnosis

The exact Docker Engine `v28.0.4` tag resolves to
`6430e49a55babd9b8f4d08e70ecb2b68900770fe`. Nine Engine files and one supporting
Go JSON source were read and checked against their upstream Git blob IDs. The
complete paths, byte counts and hashes are in [stats-primary.json](stats-primary.json).
The Go source documents encoding semantics, not the unobserved compiler or build
provenance of the installed Docker binary.

| Primary source | Established behavior |
| --- | --- |
| [Stats HTTP route](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/api/server/router/container/container_routes.go) | API 1.45 admits `one-shot=true` and passes that option and the requested container identifier directly to `ContainerStats`. |
| [Container lookup](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/container.go) and [inspect](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/inspect.go) | A full existing container ID takes the exact-ID lookup. The harness checks the full observed ID, owner, name and complete required configuration before requesting stats for that same full ID through its fixed local socket. |
| [One-shot dispatch](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/stats.go) | A successful one-shot encodes `GetContainerStats` directly. The assignment of `Name` and `ID` in `getStatJSON` belongs to the separate subscription path and does not run for one-shot. |
| [Linux stats construction](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/stats_unix.go) | The successful response starts with `Read`; the cgroup v1/v2 branches populate counters without assigning `Name` or `ID`. |
| [Stats JSON field tags](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/api/types/container/stats.go) and [encoding/json omission](https://github.com/golang/go/blob/go1.23.6/src/encoding/json/encode.go) | `Name` uses `json:"name,omitempty"`; `ID` uses `json:"id,omitempty"`. Empty strings are omitted by this encoder. Successful one-shot identity fields are therefore absent, not supplied empty strings. Memory usage/limit and PID current also have omission tags; their absence cannot be interpreted as an observed live zero. CPU total has its own explicit field. |
| [Start transaction](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/start.go), [state](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/container/state.go) and [task access](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/container/container.go) | Normal start holds the container lock through task creation/start and setting Running/task identity. Stats obtains the running task under that lock. This supplies no startup grace period or permission to omit a live counter. |

`ContainerStats` returns a sparse response containing name and ID for a
nonrunning/restarting container; `GetContainerStats` has a corresponding sparse
response for a conflict or not-found result. A container can exit between the
pre-inspection and collection. Missing counters therefore require a separate
exact owned-container inspection that confirms `Running=false` and status
`exited` or `dead`; this yields no sample, not a zero sample. Unknown or still
running state must continue to refuse.

The [executed harness](https://github.com/niklas-olsson/zero-point-modulator/blob/897ad91299b8ffb6d3a9c37e6864688ff9495af4/tests/contracts/check_alpine_sealed_runtime.py)
instead required a response ID even on the successful one-shot path. Its broad
`except Refused` then reclassified any identity, type or resource-limit failure
as `CONTAINER_STATS_MISSING` while the container remained running, or suppressed
it if the follow-up state was terminal. This is a source-proven observation
defect. It does not identify which condition occurred in attempt 6 because the
original payload and follow-up state are unavailable.

An inert reproduction extracted those two original methods from the exact
executed source, without importing or executing Docker or guest code. The pinned
successful one-shot shape produced `CONTAINER_STATS_IDENTITY`; a wrong response
ID or over-limit memory value was suppressed when a controlled following inspect
reported exit. The current regressions require those distinct failures to remain
fatal. These are controlled source observations, not recovered native payloads.

## Separate seventh source proposal

The [seventh plan](../../../tests/contracts/native-alpine/active-plan-7.json)
pins the observed server semantics to Docker 28.0.4 and API 1.45. A different
server version refuses before daemon-info, candidate-source checks or acquisition.
The existing complete owned inspection and full-ID fixed-socket GET bind the
sample. Absent `id`/`name` is accepted only under that versioned path; any supplied
ID must match the exact full ID and any supplied name must match the owned name,
including its leading slash. Empty, null, malformed or conflicting supplied
identities refuse. No identity is inserted into the native response.

Memory usage and exact memory limit, PID count and cumulative CPU nanoseconds
remain mandatory finite nonnegative integers; Boolean/type aliases refuse. All
present counters are checked before an incomplete sample is classified, so one
missing field cannot hide another field's type or limit violation. Identity,
type and resource-limit errors retain their original refusal and cannot be
replaced by a later exit. Only missing-counter results use the existing extra
inspection, now with the complete owner/configuration check as well as exact
terminal state. That case retains prior counters and the existing reservation.

Twelve new controlled fixtures exercise these response shapes, original caps,
the real caller's inspection/request order, supplied identities, exit handling,
failure preservation and the early version prerequisite. They perform no Docker
operation or candidate execution. The original 1-MiB response bound, one-second
operation ceiling, original work/cleanup deadlines and zero-retry policy remain
unchanged. The candidate, requested authority, guest commands, source tuple,
acquisition catalog, resource limits and all six historical plans remain intact.
A new work/upload identity and exact plan require independent source review,
fresh required gates and separate admission before any seventh native trial.
