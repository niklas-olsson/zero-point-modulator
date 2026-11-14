# Native Alpine attempt 1: source admission failed

Agent-authored by coordinating ChatGPT `/root`, 14 November 2026.
**The admitted attempt failed before candidate acquisition.** The original
source, plan, closed summary and workflow result agree on failure. This is
neither a successful Alpine installation nor evidence of a corrupt artifact.

## Exact retained evidence

| Item | Identity |
| --- | --- |
| Executed source | `11fe6e8312ce2f5cdb3e6f6f659be35e27ed70b6` |
| Executed tree | `2954102e426d82076469885689b244450b69a43e` |
| Source parent | `4841e3b6d705365452e94c92f09d88a382a55a39` |
| Original active plan | 6,033 bytes; SHA-256 `8917e1b695e7154c9ecd60f40ab2defcd691ca715da81ae8b23b5dc60ebcb953` |
| Workflow | [43614433582, attempt 1](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43614433582) |
| Native job | [132506342797](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43614433582/job/132506342797); execution exit 1 |
| Original artifact | `14121363150`, 1,085 bytes; SHA-256 `f81196a6b6d3befe317e6fccd9775aa849464e00f38065cf86cfdddc6eb00968` |
| Exact public summary | [summary.json](summary.json), 1,684 bytes; SHA-256 `41ac6389a0ac65ee38bd971a3aff9dd377d4829c63e4d0f6bf84115161b90b00` |
| Source, job and custody binding | [run.json](run.json), coordinator-derived metadata, not a native receipt |

The source/admission job passed before the native job's four clean checkouts.
The native job failed at its execution step and successfully uploaded its sole
closed summary. Root and a separate reviewer independently checked ZIP/summary
hashes and live run/job/artifact identities. The reviewer freshly fetched the
decoded native job log and matched the retained bytes.

Both ten-job source workflows passed before admission:
[push 43613223466](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43613223466)
and [proposed merge 43613294306](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43613294306).
Those existing installed trials and source gates do not turn this separate
failed Alpine attempt into success.

## What actually happened

The first failure is `SOURCE_GIT_INDIRECTION`. The checker reached the source
metadata guard before Git commands or candidate requests. The public summary
does not identify which of its four refused relative paths was present.

The actual host was image `20261111.127.1`, kernel `7.0.0-1014-aws`, x86_64,
system Python 3.12.3 and UID/GID 1001. The job log records Git 2.56.1 and executed
checkout revision `11d5960a326750d5838078e36cf38b85af677262`.

The summary records 54 ms and three samples, with maximum sample gap 14 ms,
sampled host RSS 34,652,160 bytes and logical owned storage 19,161,796 bytes.
These are the report's observations, not full CLI lifetime, physical storage
quota or an Alpine capacity result. Artifact requests, body bytes, completed
artifacts and container CPU/memory/PID observations are zero. No candidate
package, interpreter, service, managed task, model or provider ran.

`artifact_bytes=FAIL` names the source/input-admission stage. It does not mean
downloaded bytes failed their hashes: there were no downloads. The other nine
qualification checks are NOT_RUN. `owned_cleanup=PASS`, the cleanup failure
list is empty and pending mutation reservations are zero. The whole attempt
remains FAILED and `native_execution_qualified` is false.

## Source-backed checkout correction

A fresh local Git 2.52.0 reproduction of the checkout sequence creates an
inactive `config.worktree` with exactly `core.sparseCheckout=false`,
`core.sparseCheckoutCone=false` and `index.sparse=false`. The checkout action
runs `sparse-checkout disable` and then removes `extensions.worktreeConfig`.
The exact executed action and Git 2.56.1 sources establish that write sequence;
the latter is source evidence, not a binary attestation of the hosted Git build.

| Primary source | Git blob |
| --- | --- |
| [checkout source provider](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/src/git-source-provider.ts) | `2d3513897655e0ea2eb4efc2b2c9d6132a69549f` |
| [checkout command manager](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/src/git-command-manager.ts) | `9c789ac803e4d3b99463037d0ada654b79d8b863` |
| [Git sparse checkout](https://github.com/git/git/blob/v2.56.1/builtin/sparse-checkout.c) | `cb4a037b77029156f1bf5dc25f307e83224a87ee` |
| [Git worktree initialization](https://github.com/git/git/blob/v2.56.1/worktree.c) | `8cb8637b189ab825c9ffb63848982a20411b35cd` |
| [Git sparse index](https://github.com/git/git/blob/v2.56.1/sparse-index.c) | `3d77dadae56d08697a153fc78718191959efe6ea` |
| [Git worktree config writer](https://github.com/git/git/blob/v2.56.1/config.c) | `d9019e7e6c34b07905b9d8948176c4c7dd9b64d2` |
| [Git extension semantics](https://github.com/git/git/blob/v2.56.1/Documentation/config/extensions.adoc) | `be6678bb5b558555474da32aabdf020b3af14d7e` |

This demonstrates a concrete incompatibility in the original blanket guard.
It supports the narrowly corrected admission rule; it does not retrospectively
identify the exact rejected member in the first receipt. The corrected source
accepts only that three-false ordinary single-link file, after the main config
has excluded the worktree extension. Includes, additional settings, enabled
sparsity, active extensions, malformed data, links and other indirection still
fail before Git. The source checker rewrites no Git configuration.

The original plan and failed summary remain unchanged. A second source plan
has its own identity and requires independent review, fresh hosted gates and
one separate explicit admission. No retry, capability increase, limit increase,
candidate substitution or positive native result follows from this record.
