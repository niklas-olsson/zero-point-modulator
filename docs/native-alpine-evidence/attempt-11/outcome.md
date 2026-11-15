# Native Alpine attempt 11: unidentified redirect destination

Agent-authored by ChatGPT `/root/zpm_investigation`, 15 November 2026. The
coordinator `/root` and independent evidence reader `/root/zpm_investigation`
separately acquired and verified the original result. **FAILED; native
installation and interfaces remain unqualified. No next attempt is selected.**

The original first failure is `OCI_REDIRECT_REFUSED`, predicate `HOSTNAME`, with
the closed hostname class `OTHER`. The rejected destination was not retained and
remains unknown. This class names no CDN, supported origin or routing cause.
The new Docker sampling context was not reached or emitted. Owned cleanup passed.

## Original evidence

| Evidence | Identity |
| --- | --- |
| Actual run | [43729353381, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43729353381) |
| Harness commit / tree | `e99e38d29e6f19b46596716e8011a09e8215cdae` / `47b3157d04bd23fefdd4fdda185cd433d5b362ad` |
| Exact plan | [active-plan-11.json](../../../tests/contracts/native-alpine/active-plan-11.json), 9,263 bytes; SHA256 `92c4d05be1388e058054d7bb45f90d9dcd4f272bb448db853a6f8819ee908645` |
| Source / native jobs | `132858149894` PASS / `132858239822` FAIL |
| Original archive | Artifact `14167523176`, 1,305 bytes; SHA256 `218956a46a5fbfe9cdad7a7667f0e5b6300394e54c1197cf5331f05ad96f5ee5` |
| Original summary | [summary.json](summary.json), 2,108 bytes; SHA256 `45d78f45b51251bfac796aef6298d02d65564681061a113b3eb599c2ebb82645` |
| Decoded native log | 44,622 UTF-8 bytes, including its initial BOM; SHA256 `31e8aafa3f47db57d648c9bab305c38583c7be598d745e87e5f4f31d9a1dce73` |
| Decoded source log | 60,752 UTF-8 bytes, including its initial BOM; SHA256 `134170208a5bb665a08ecd60e2d3273acf0e97473e7ebe009b35f5fe1c9852e5` |
| Derived custody | [run.json](run.json), derived metadata rather than a native receipt |
| Admission / outcome | [Exact admission](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6896761251) / [recorded outcome](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6896872795) |

The archive digest matches live artifact metadata and the upload log. Its sole
ordinary member matches the retained summary exactly. Native checkout logs
confirm the harness and all three frozen candidate revisions. The source job
passed 185 fixtures, `PRETRIAL_SOURCE_PASS` and both exact inert admission checks.
The native step exited 1 and uploaded the summary successfully. Only the closed
summary and derived records are committed; the full logs and archive are not.

## Actual checks and observations

`artifact_bytes` failed and `owned_cleanup` passed. APK trust, base-image identity,
APK transaction, installed APK inventory, interpreter identity, production
prepare/verify, sealed bootstrap, native imports/interfaces and repeat bytes all
remain `NOT_RUN`. Qualification is false; cleanup failures are empty, pending
mutation reservations are zero and the summary is not truncated.

Acquisition made two requests, followed no redirects, completed no artifacts and
read 5,489 body bytes. Only the original authentication and registry origins were
contacted. The report records 1,832 ms elapsed, 54 samples, a 265 ms maximum sample
gap, 69,070,848 bytes peak host RSS and 20,306,441 bytes peak logical owned storage.
These observations concern failed acquisition, not a native workload or physical
storage quota. The host reported Docker client/server 28.0.4, API 1.45, Python
3.12.3, Linux x86_64, Ubuntu runner image `20261111.127.1` and kernel
`7.0.0-1014-aws`.

## Official-host comparison and missing action

A read-only comparison found no typo or documented missing origin to correct.
Both historical diagnostic literals match the corrected entries in
[Docker/docs #21964](https://github.com/docker/docs/pull/21964), merged
5 February 2025. Docker explicitly removed both from its allowlist on
[8 June 2026](https://github.com/docker/docs/commit/466c7e537900a5efbf6a8bf30efc2b93d6b0908d).
The unchanged allowed CDN, `production.cloudfront.docker.com`, matches the
[current official allowlist](https://docs.docker.com/desktop/enterprise/allow-list/)
and Docker's [20 May 2026 infrastructure notice](https://docs.docker.com/docker-hub/release-notes/#2026-05-20).
These sources do not identify native11's `OTHER` value or support another origin.

The missing action is to establish the actual rejected destination and a
supported, explicitly reviewed redirect contract for the exact pinned blob under
anonymous acquisition. The original target cannot be recovered from this report.
A later distinct observation must retain its own identity and cannot be assigned
retrospectively to native11. No guessed origin expansion or blind native retry is
selected.

Executing source, workflows, plans 1–11, consumed run references, candidate bytes,
origins, budgets and authority remain unchanged. PR69 and #63 remain open because
the declared actual native acceptance has not passed. This outcome establishes
no browser, local-model, managed-project or distribution qualification.
