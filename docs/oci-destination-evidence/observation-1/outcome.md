# OCI destination observation 1: CloudFront response observed

Agent-authored by ChatGPT `/root/transition_integration`, 15 November 2026.
**OBSERVED: the separately admitted GET returned HTTP 307 with parsed hostname
`production.cloudfront.docker.com`. Response closure and owned-child reaping
were confirmed. Native execution was `NOT_RUN`.**

This result belongs to this observation only. It does not identify the destination
rejected by native11, explain routing differences, qualify a native installation,
or authorize another origin, observation or native retry.

## Original evidence

| Evidence | Identity |
| --- | --- |
| Actual run | [43735682057, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43735682057) |
| Source commit / tree | `67e7d0dc6c3dd43e842d34e304f64bedfe414737` / `7f71f49b7d07403cc0e05d9f549b95ddb5a4480e` |
| Consumed plan | [observation-plan-1.json](../../../tests/contracts/oci-destination/observation-plan-1.json), 3,055 bytes; SHA256 `54844e18bbf22d75841b6bcbfd22e7d455c52f70e4743d9919bc23f145f0ca55` |
| Source / observation jobs | `132877665246` PASS / `132877706767` PASS |
| Original archive | Artifact `14170541779`, 850 bytes; SHA256 `1eac196df24ee8db4d2768467a86fcd84591dafc7f1bbe5a100c3397fd13c781` |
| Original summary | [summary.json](summary.json), 1,356 bytes; SHA256 `087487297d9e70935ca29f89fe3fca4b829c1f1295f59fb366b1eeaeb863aee5` |
| Decoded observation log | 16,881 UTF-8 bytes, including its initial BOM; SHA256 `4dc51577ff3a0339043c2354afacbf9d3ccb45f2f658cd7a7a73b7712b1f953f` |
| Decoded source log | 23,405 UTF-8 bytes, including its initial BOM; SHA256 `c5f748b2be8cb37e814b38e69e17bdbd75807853dbbfe05db2c9dbba3e23b734` |
| Derived custody | [run.json](run.json), derived metadata rather than an observation receipt |
| Selection / admission | [Accepted scope](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6897044327) / [exact operation admission](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6897635234) |
| Source review | [Publication and review checkpoint](https://github.com/niklas-olsson/zero-point-modulator/pull/69#issuecomment-6897629569) |

The archive's sole regular member matches the retained summary byte for byte.
Its digest matches artifact metadata and the upload log. Both job logs confirm
the exact source checkout; the observation log contains the same closed summary.
The source job passed 41 fixtures and the exact observer/native11 inert checks.
The observation step exited 0 and uploaded the current summary successfully.
These two jobs do not establish the outcome of ordinary repository CI, which is
tracked separately. Only the original closed summary and derived records are
committed; the archive, raw logs and acquisition secrets are not.

## Actual observation and limits

The report records two requests, zero followed redirects, zero completed
artifacts, 5,489 token-body bytes, 1,561 response-header bytes and 13 framing bytes.
Both the existing hostname comparison and path-prefix comparison passed; all nine
fixed Location predicates passed. Counters are complete and the first failure
is null. The observer reports 261 ms elapsed, separate from workflow setup time.
The 4,902,249-byte prospective body reservation stayed charged. No blob body was
consumed by the application, and no redirected origin was contacted. This is
not a claim of zero TLS/socket prefetch.

The observation job reported Ubuntu 24.04.5 LTS, image `20261111.370.1`, Azure
region `eastus2`, and the command `/usr/bin/python3.12 -I -B`. It did not emit a
Python microversion. These are this job's observations, not a stable environment
promise or an explanation for the native11 result.

Native11's `HOSTNAME / OTHER` destination remains unknown. The later current-host
and path-prefix matches provide no evidence about that earlier target, no new
allowed origin, and no artifact-content or native qualification. The next scope
remains subject to the recorded coordination and review process; this evidence
record selects no follow-up operation. Plans, executing inputs, workflows,
consumed references, earlier evidence, candidate bytes, pins, caps and authority
remain unchanged.
