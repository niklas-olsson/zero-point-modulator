# Native Alpine attempt 9: OCI hostname refusal

Agent-authored by ChatGPT `/root/transition_integration`, coordinating `/root`,
15 November 2026. **FAILED; native installation and interfaces remain unqualified.**
The original `OCI_REDIRECT_REFUSED` guard retained `HOSTNAME` as its first failed
predicate. The actual hostname was not retained. No redirect was followed and
no candidate artifact completed. Owned cleanup passed.

## Original run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43720525897, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43720525897) |
| Harness commit / tree | `72931e1cb1e5ead9b3e35159126b5708798516d8` / `a789d5d3104e8d71ea46e326bc2ab75fca9ff77a` |
| Original plan | [active-plan-9.json](../../../tests/contracts/native-alpine/active-plan-9.json), 9,262 bytes; SHA256 `767dd8998d2e65448ce7ec9f63689c7665c280142da511da29059032127b6b8d` |
| Source / native jobs | `132831316097` PASS / `132831409416` FAIL |
| Artifact | `14164277491`, 1,285-byte ZIP; SHA256 `67fe187a9e92457e0035b5b9b4778fc7f99b0b52c9fb6e2a7f153018b2cf609f` |
| Original summary | [summary.json](summary.json), 2,069 bytes; SHA256 `96a8b0551a36d042dcc1854a368742b885ad68f7795d82ee07b0cf2ae3cef418` |
| Decoded native job log | 44,617 UTF-8 bytes including the initial byte-order mark; SHA256 `1d55c5ffab782b566cfe0eda354691ac63660483bcd9148232385671be7a94a2` |
| Derived custody | [run.json](run.json), coordinator metadata rather than a native receipt |
| Recorded outcome | [ZPM #63](https://github.com/niklas-olsson/zero-point-modulator/issues/63#issuecomment-6895375406) |

The author independently hashed the coordinator-supplied original ZIP, summary
and decoded log. The ZIP's sole ordinary member matches the retained summary
byte-for-byte. The summary matches the original source and plan; the decoded
log confirms exit 1 and successful upload of the named artifact. Only the
unchanged closed summary and derived metadata are committed. No private command
log, installation, token or redirect target is published.

## Actual checks and limits

Two requests consumed 5,489 body bytes, 2,783 response-header bytes and 13 framing
bytes, with 131,072 metadata bytes and 4,902,249 body bytes reserved. The only
contacted origins recorded were `auth.docker.io` and `registry-1.docker.io`.
Artifacts completed and followed redirects were both zero. Artifact bytes failed;
owned cleanup passed; the other nine native checks remained `NOT_RUN`.
`native_execution_qualified` is false, with no cleanup failures and zero pending
mutation reservations. Elapsed time was 1,910 ms.

The host reported Docker client/server 28.0.4, API 1.45, Python 3.12.3 and Ubuntu
runner image `20261111.127.1`. Observed peaks were 68,784,128 bytes host RSS and
19,984,654 bytes logical owned storage. The minimum available host memory was
15,427,854,336 bytes; the maximum sampling gap was 266 ms across 54 samples.
No container resource usage was recorded. These observations do not identify a
network cause or demonstrate native execution.

## What is known about the redirect

The unchanged guard evaluated one Location value, the original registry prefix
and HTTPS scheme before failing the exact hostname comparison. The later port,
userinfo, fragment, path and URL-text predicates were short-circuited. A missing
hostname also fails this comparison. A different hostname can fail before an
invalid port or other later field is examined. No hostname, target, path, query,
status or response body can be reconstructed from the retained evidence.

[Attempt 8](../attempt-8/outcome.md) accepted four CloudFront redirects and all
173 artifacts through the same redirect policy. It ran on a different reported
runner image. That difference and the shared two-request/5,489-byte prefix of
attempts 7 and 9 do not establish a cause or authorize another destination.

The current [Docker Desktop allowlist](https://docs.docker.com/desktop/enterprise/allow-list/)
lists `production.cloudfront.docker.com` for image pulls and pushes. Docker's
[Hub release notes](https://docs.docker.com/docker-hub/release-notes/#2026-05-20)
record its addition in May 2026. The official
[GetBlob contract](https://docs.docker.com/reference/api/registry/latest/operations/GetBlob/)
permits a 307 Location to storage or a CDN, but does not specify an exhaustive
redirect-host list or a signed-query grammar. These documents do not reveal this
attempt's actual target.

Docker's historical allowlist included `production.cloudflare.docker.com` and
`docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com`.
The R2 entry was introduced in [Docker docs PR #21964](https://github.com/docker/docs/pull/21964).
Both Cloudflare entries were removed in
[PR #25304](https://github.com/docker/docs/pull/25304), at commit
[`466c7e537900a5efbf6a8bf30efc2b93d6b0908d`](https://github.com/docker/docs/commit/466c7e537900a5efbf6a8bf30efc2b93d6b0908d).
These are historical classification literals, not current support guarantees
and not permission to contact them.

## Separate prospective diagnostic

The [tenth plan](../../../tests/contracts/native-alpine/active-plan-10.json) binds
one optional `oci_redirect_hostname_class` beside the original `HOSTNAME`
refusal. It classifies only the already-read hostname operand as `MISSING`,
`HISTORICAL_CLOUDFLARE`, `HISTORICAL_DOCKER_R2`, `OTHER` or `UNAVAILABLE`.
The fixed value follows the existing child-result and first-failure custody;
the summary includes it only when that original OCI `HOSTNAME` refusal wins.
It cannot recover the missing hostname of attempt 9 or describe unevaluated
predicates.

No raw target, hostname, path, query, arbitrary-target hash, header, token or
exception text is exported. The same exact CloudFront-only guard, credential
stripping, request counts, no-retry rule, deadlines, resource limits and cleanup
remain in force. Plans 1 through 9, original inputs and candidate revisions are
unchanged. Attempt 10 is `NOT_RUN`; independent review, fresh source gates and
separate exact admission are still required. This source diagnostic provides no
behavior fix or native qualification.
