# Alpine sealed-bootstrap qualification candidate

**Status: complete source candidate; independent activation review pending.
Native execution is NOT RUN.** This [#63](https://github.com/niklas-olsson/zero-point-modulator/issues/63)
fixture keeps the original inert compiler and adds a separate, explicitly gated
native runner. It changes no installer, Python admission, operative integration
pin or production execution authority. The original 173 inputs and limits remain
unchanged. Source fixtures cannot qualify their native installation.

## Frozen subject and evidence

The candidate is Alpine **3.24.4**, Docker Official CPython **3.12.16** under
`/usr/local`, Linux/amd64. Its official
[recipe](https://github.com/docker-library/python/blob/3d7e95597e56a415d9e4abc0f15877e005d2cb2e/3.12/alpine3.24/Dockerfile)
and immutable OCI metadata are retained with the input recipe:

| Subject | Exact identity |
| --- | --- |
| Python amd64 manifest | `sha256:a61cbd76a882645de7f91423329fe43a67eed60d7da24f9e5331414bb3618b20` |
| Python config/image ID | `sha256:4076016aea5420e5c87eb52280edfc527ace1ce31dc1c9288b1cc4ef9a0e8905` |
| Public input recipe SHA-256 | `e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f` |
| ZPM production source | `c5fb355f35b73041925e99590d6a18e71038548a` |
| OpenJules production source | `0dd5bf4e42b3677c3f2f564655bc87a97c4bf973` |
| Polyphony production source | `6480e36355bf0d5de4e25a560d40e665fd76038c` |

The [public input recipe](../tests/contracts/native-alpine/inputs-v1.json)
contains four OCI layers, 63 APKs and 106 unique wheels: **209,309,575 binary
bytes**. Historical verification flags describe the prior artifact-inspection
phase, not execution by this source checker. A native attempt must reverify the
actual bytes and trust bindings. No image, APK or wheel binary is shipped here.

The [machine plan](../tests/contracts/native-alpine/plan-v1.json) keeps production
sources separate from the harness revision. Newer main branches do not silently
replace this tuple. The three trees contain 7,945,111 tracked blob bytes.
Polyphony's exact `kp -> polyphony` link is preserved; its `third_party/emsdk` and
`third_party/llama-cpp-wasm` gitlinks remain uninitialized. Fresh, clean checkouts
with credential persistence and submodule initialization disabled are required.
Verify tree identities, dependency-file hashes and special entries before use.

The original OCI APK database has 37 real packages plus virtual package
`.python-rundeps`, version `20261105.193119`, checksum
`Q1p4YXIaFk/uirdRB47MhRqHJriv8=`. Overlaying the selected 63 real APK identities
gives **64 expected installed records**, including the unchanged virtual record.
This is an expected solver result, not an observed native transaction.

## Source-only command and report

Run from the repository root with ordinary Python. The exact plan hash binds the
source being checked; providing it does not assert independent approval:

```sh
python -B -m tests.contracts.check_alpine_sealed_candidate \
  --plan-sha256 c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76 \
  --report /absolute/new-pretrial-report.json
python -B -m unittest tests.test_alpine_sealed_candidate -v
```

The command checks frozen metadata, OCI config/layer agreement, public origins,
APK trust-key bindings, finite totals, original sources, separate wheel sets,
caps and the fixed extension catalog. It reads no candidate binary and starts
no subprocess, container or service. There is no `--run` mode.
`PRETRIAL_SOURCE_PASS` means internal source consistency. Native checks remain
`NOT_RUN`, with `native_execution_qualified: false` and
`active_trial_implemented: false`.

Private report schema `zpm-native-alpine-pretrial-report/v1` retains plan/recipe
hashes, a separately supplied harness revision, original production revisions,
input counts, caps and the first fixed failure code. It excludes workspace paths,
URL tokens, credentials, private installations and raw candidate output. Reports
are new 0600 files, synced with their parent, at most 256 KiB including the final
newline. Existing files and symlinks are not overwritten. A bounded failure
fallback preserves identities, the first safe failure code and explicit unexecuted
verdict. It cannot serialize an oversized success.

The source workflow runs this command and synthetic fixtures. This inert report
remains job-local; no private installation or binary is uploaded. Ordinary
required unit, package, CLI and integration gates remain unchanged.

## Separate explicit native entry point

The [active plan](../tests/contracts/native-alpine/active-plan-1.json) binds the
host/guest drivers, their local imports, the workflow, original plan/recipe,
commands, authority and caps. The original `plan-v1.json` and inert checker retain
their historical `active_trial_implemented: false` fields. They do not become
native evidence merely because the separate runner exists.

Check active-plan source consistency without acquisition, Docker or candidate
execution using an externally recorded exact hash:

```sh
python -I -B tests/contracts/check_alpine_sealed_runtime.py \
  --check-plan --reviewed-plan-sha256 "$reviewed_plan_sha256"
```

For an admitted native attempt, the existing workflow accepts either an explicit
manual `reviewed_plan_sha256` or one immutable branch
`qualification/native-alpine/run/<exact-plan-sha256>`. Ordinary qualification
branches, pull requests and an empty manual request run source checks only. The
source job checks any requested admission before the native job's production
checkouts. The runner repeats admission before work allocation, subprocesses,
artifact requests or Docker contact. An explicit hash and run-ref hash must
agree. A GitHub rerun attempt greater than one refuses; a failed native attempt
requires a newly reviewed plan and source, preserving its original evidence.

The active lane uses Ubuntu 24.04 amd64, existing system CPython 3.12, Git, OpenSSL
and an existing Docker Engine supporting API 1.45, `runc`, memory/swap and PID
limits. It records actual tool/runner versions and refuses missing prerequisites.
It installs no host package and requests no host sudo, device or added privilege.
Four clean sibling checkouts separate the current harness from all three frozen
production revisions. Checkout/action traffic is a declared workflow setup
prerequisite outside the 173-artifact envelope.

Only the closed `summary.json` projection is exported. It contains fixed public
identities, check/cleanup verdicts, actual tool facts and aggregate observations.
Private logs, installation/configuration/secret bytes, selector digests and raw
guest receipts stay job-local. A summary alone cannot establish success: the
exact native job must succeed and the record must report every required native
check as `PASS`, without a first failure or unconfirmed cleanup.

The [source-validation record](native-alpine-runner-validation.md) distinguishes
the focused checks, local packaging result and reproduced environment limitation
from the pending hosted and native gates.
The [active-harness contract](native-alpine-active-harness.md) explains mutation
custody, stage deadlines, exact configurations and failure handling. Its source
and fault fixtures include controlled idle children and fake protocol/daemon
records. They do not run Docker, acquire the candidate or execute its packages.

## Native sequence, pending activation review

1. **Admission.** Independently review the exact plan and active-runner head.
   The separate active entry point requires the reviewed plan hash before output
   allocation, acquisition or Docker effects. Record host tool versions, invoking
   nonzero UID/GID and clean production/harness revisions.
2. **Finite acquisition.** Fetch only the listed 173 artifacts. Use HTTPS, no
   APK/wheel redirects, mutable image pull, index refresh or re-resolution. One
   OCI blob redirect to `production.cloudfront.docker.com` may use a fresh
   credential-free request. A fixed anonymous `library/python:pull` registry token
   stays confined to `registry-1.docker.io` and is never retained. One token request,
   four registry requests, up to four redirects and 169 APK/wheel requests fit the
   180-request bound. There is no automatic retry or substitute artifact.
3. **Verify and load.** Reverify all sizes/hashes, OCI uncompressed diff IDs,
   archive paths, wheel metadata/native members and APK signatures. Reuse the
   existing native musl signature/archive checks and production wheel identity
   parser. Do not extract archive-controlled paths into the host. Compose a bounded
   Docker-load archive from the exact config and unmodified layers; record owned
   resource intent before load and verify the image ID afterwards.
4. **Offline assembly.** Use one owned root container with network `none`,
   no-new-privileges, cap-drop `ALL`, and only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`.
   Pass all 63 exact APK paths individually to `apk add --no-network --no-cache
   --repositories-file /dev/null`. Keep signatures and package scripts enabled.
   Compare all 64 installed records, checksums and virtual-package dependencies.
   A mismatch or capability refusal ends the attempt. Commit only the owned result
   to a fresh fixture image. Stop/remove assembly before qualification starts.
5. **Native identity.** Qualification uses a nonzero UID/GID, read-only root,
   cap-drop `ALL`, no added caps, no-new-privileges and network `none`. Inputs,
   sources and harness are read-only; one fresh owned work directory is writable.
   No host/control socket, host namespace or added device enters the container.
   Network `none` retains loopback. Hash pinned Python/libpython/musl files, then
   call the real `zpm.dependencies.interpreter_identity()` through
   `/usr/local/bin/python3.12`. SOABI/cache-tag/platform are observed, never guessed.
6. **Production installation.** Construct the existing dependency-plan schema
   from that actual identity, original dependency hashes and finite local wheels.
   Preserve pip 25.3, setuptools 80.9.0 and wheel 0.45.1. Run production prepare,
   verify and generated bootstrap for a disposable zero-capacity installation.
   Require three separate environments, exact installed file/RECORD digests,
   dependency checks and sealed receipts. Any required image selector is only an
   explicitly unqualified installation fixture. Do not start a worker or claim
   the Python base is a managed-agent image.
7. **Fixed probes and repeat.** The eight fixed probe IDs cover each environment's
   native imports, three owner CLI help interfaces, pure ZPM binding validation
   and OpenJules's existing managed-installation-context probe. The
   [extension catalog](../tests/contracts/native-alpine/native-extensions-v1.json)
   contains 53 native shared objects and 42 Python extension import targets,
   selected per environment's wheel list. Use separate service processes; never
   combine the two `src` namespaces. Bundled support libraries are file-verified
   and used through the loader, not claimed as independently exercised interfaces.
   Repeat prepare/verify/bootstrap and require unchanged configuration, secrets,
   selector and original sealed-completion bytes.
8. **Evidence and cleanup.** Preserve the first failure and bounded private
   diagnostics. Remove only recorded owned containers and fixture images and
   confirm absence. Retain ambiguous cleanup as uncertainty, never success. Do not
   prune shared resources, erase history to force a retry or increase limits after
   failure. A safe exported summary stays separate from installation secrets and
   raw logs. No binary release is part of this qualification.

## Fixed proposed limits

| Scope | Limit |
| --- | --- |
| Whole measured attempt, including cleanup | 1,800 s; final 90 s reserved for cleanup |
| Acquisition | 600 s, 512 MiB bodies plus 65,536 reserved bytes per request; at most 180 requests and four simultaneous transfers |
| Metadata response | 1 MiB, included in acquisition accounting |
| Host process RSS / preflight available RAM | 1 GiB observed RSS / at least 4 GiB `MemAvailable`; missing measurements refuse |
| All owned storage | 4 GiB, including inputs, archive, work, source copies and conservative owned Docker image/container accounting |
| Tracked source copies | 128 MiB combined; setup/Git traffic reported separately as workflow prerequisites |
| Either candidate container | One running container; hard 2 GiB memory and memory+swap, 128 PIDs, two CPUs |
| APK / initial bootstrap / repeat | 300 / 900 / 60 s, each shortened by remaining total budget |
| Fixed probes | 15 s each, 120 s combined |
| Output | 1 MiB per command, 16 MiB aggregate private logs, 256 KiB safe report |
| Observation loop | At most 250 ms sleep between sample attempts; bounded collection latency and actual maximum gap are reported |

Archive member/decoded limits are explicit in the machine plan. These are stop
limits, not fit claims. Transfer, sampled RSS, Docker hard limits and conservative
storage accounting are distinct. The active runner must count interrupted bodies,
reconcile owned PID/start identities, sample container memory/CPU/PID evidence and
include cleanup in its final verdict.

## Remaining prerequisite

No exact artifact identity is currently missing from this native closure. Actual
interpreter identity, APK solving, installation, imports, resource use and cleanup
remain unobserved. The completed source candidate's acquisition, archive
composition, Docker commands, measurement, report reduction and cleanup require
independent review before the first native attempt. Source-only success does not
satisfy that gate.

The [active-harness contract](native-alpine-active-harness.md) records separate
plan admission, finite host acquisition and custody,
offline assembly, the fixed nonroot production probes, sampled accounting and
bounded cleanup. The code-bound plan is reviewable source data until one exact
native attempt is explicitly admitted. It is not a native result.

This can qualify native bootstrap/interfaces only. It cannot qualify three running
services, a daemon inside a guest, protected tasks, models, capture/release,
browser durability or public distribution. Keep #63 open until actual native
acceptance is met, and preserve each failed attempt instead of replacing its record.
