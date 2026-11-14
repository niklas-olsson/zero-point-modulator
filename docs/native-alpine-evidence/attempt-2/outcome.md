# Native Alpine attempt 2: token response framing refused

Agent-authored by ChatGPT `/root`, 14 November 2026. **FAILED; native execution
remains unqualified.** This records the second actual attempt without changing
its summary or the [first failure](../attempt-1/outcome.md).

## Exact run and custody

| Evidence | Identity |
| --- | --- |
| Actual run | [43617683759, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43617683759) |
| Harness commit / tree | `6629415673d59a969cef4f3920d5982327bc135a` / `bfb0da3b03f6b64b002bf1ee29a6eeb208e951f1` |
| Active plan | 6,033 bytes; SHA-256 `bc4e5168483bdf372af334a14f6e3e0bc1b58850bd026262cce834274028bced` |
| Source / native jobs | `132516224804` PASS / `132516272923` FAIL |
| Artifact | `14122173666`, 1,232-byte ZIP; SHA-256 `265f1fed9c39af2e703b45418edb5a134739e1108f911bf1761b20dfed4dced1` |
| Original closed summary | [summary.json](summary.json), 1,971 bytes; SHA-256 `f8766aa40c2bb0e29e4a531bd9ab626600bdadab1a63fe16aae13d3e7009e6cd` |
| Decoded native job log | 44,616 UTF-8 bytes; SHA-256 `c6b69524b1c772fc10e8d17bb274f70f503ca1168355efbe37727a0752b7e2c4` |
| Derived binding | [run.json](run.json), coordinator metadata, not a native receipt |

The exact immutable run branch remains bound to the plan hash and source above.
The source job passed, the native execution exited 1, and the summary upload
succeeded. The coordinator verified exact artifact length/hash, its sole safe ZIP
entry, original summary identity and live run/job identity.

Before admission, both ten-job ordinary workflows passed:
[push 43616587084](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43616587084)
and [proposed merge 43616592173](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43616592173).
Both dedicated source workflows also passed. The activation branch's ordinary
[CI 43617683780](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43617683780)
subsequently passed all ten jobs. Those results do not qualify this failed native attempt.

## Observed result and uncertainty

The first failure is `HTTP_FRAMING`. One request reached `auth.docker.io` and
observed 749 response-header bytes, zero body bytes and zero completed artifacts.
The exact source guard establishes that a Transfer-Encoding field was present,
after status 200 and identity content-encoding admission. **Its value was not
retained.** This evidence does not retrospectively establish that it was chunked.
The original 65,536-byte metadata and 1-MiB body reservations were charged.

Source admission and host prerequisites passed. The host was Ubuntu image
`20261111.370.1`, kernel `7.0.0-1017-azure`, Python 3.12.3, Git 2.56.1,
OpenSSL 3.0.13 and Docker client/server 28.0.4 through API 1.45, UID/GID 1001.
Host Docker identity reads ran. No candidate package, container, interpreter,
service, task, model or provider executed.

The summary records 1,409 ms before final writing, 52 resource samples, maximum
sample gap 268 ms, sampled peak host RSS 66,691,072 bytes, logical owned storage
19,266,072 bytes and minimum host available memory 15,679,504,384 bytes.
These are sampled report observations, not complete CLI lifetime or physical
storage measurements. Container memory/PID/CPU observations remain zero.
`artifact_bytes=FAIL`, `owned_cleanup=PASS`, the other nine checks are NOT_RUN,
the cleanup-failure list is empty and no mutation reservation remains pending.

## Narrow source correction

The next source candidate admits either the existing Content-Length mode or a
bounded bare HTTP/1.1 chunked body at the one fixed anonymous token endpoint.
It preserves exact Content-Length requirements for every artifact. It rejects
mixed or repeated framing, compression, trailers, extensions, malformed sizes,
incomplete chunks and alternate endpoints. This is a deliberately restricted
subset of [RFC 9112 section 7.1](https://www.rfc-editor.org/rfc/rfc9112.html#section-7.1),
not a claim of general-purpose HTTP compliance.

The host Python source reviewed for this boundary is
[CPython v3.12.3 `Lib/http/client.py`](https://github.com/python/cpython/blob/v3.12.3/Lib/http/client.py),
Git blob `a353716a8506e65ae9ac9f06581ad17397e2f2a2`. Its default chunk decoder accepts
extensions and iterates trailers with per-line limits. The new narrow reader
uses the original bounded response stream and charges all chunk framing to the
same existing per-request 65,536-byte header reservation. Payload remains inside
the original 1-MiB token and aggregate acquisition limits. The fixed whole-attempt
and acquisition deadlines remain unchanged. Reports retain counts, never token
or header contents. This is source analysis, not hosted Python binary attestation.

A distinct `active-plan-3.json` binds that code and the third work/output identity.
The original two plans, source revisions and failures remain unchanged. Independent
review and fresh hosted source gates precede any separate third admission.
No retry or native success is implied by this correction.
