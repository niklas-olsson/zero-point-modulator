# Native Alpine runner implementation checkpoint

This file preserves the earlier unfinished checkpoints. The completed source
candidate is now described in the [canonical native guide](native-alpine-qualification.md)
and [active-harness contract](native-alpine-active-harness.md). It awaits
independent review and has no native execution result.

## Original draft at 7cb9306

This is an unfinished source-only checkpoint for issue #63. It follows the
reviewed [active-harness design](native-alpine-active-harness.md). No artifact
acquisition, Docker command, candidate interpreter, bootstrap, import probe or
native qualification has run. The original compiler, 173 input identities,
production tuple and workflow remain unchanged.

The two draft fixture drivers now contain explicit hash admission, bounded
artifact transport, exact source checks, process identity reconciliation,
Docker ownership journaling and configuration checks, finite guest operations,
existing producer preparation/bootstrap calls and a safe result projection.
There is deliberately **no active plan file** and no workflow invocation of the
draft. Its active entry point therefore refuses before effects. The focused
checkpoint tests exercise pure functions and refusals only.

The next implementer must finish the following source work before requesting
independent source/plan review:

- Close observation gaps around a container or image mutation that succeeds
  before its CLI result is read, and around removal before confirmed absence.
  Preserve conservative accounting through those transitions.
- Complete exact image environment and container configuration comparisons,
  including the assembly-to-qualification transition.
- Bound every guest phase, including in-process producer calls and repeat
  validation, within its original stage and total deadlines. Enforce the one
  aggregate private command-output allowance across host and guest logs.
- Make root assembly receipts/logs readable only by the invoking owner after
  the stage, without broadening mount permissions or capabilities.
- Handle empty tracked source blobs while retaining exact modes, link and
  clean-tree admission. Audit Git metadata and argument boundaries.
- Finish fault fixtures for partial transport, source/config drift, interrupted
  create/load/commit, cleanup uncertainty, report limits and final resource
  observations. Audit report fallback and failure preservation against each.
- Generate the exact inert active plan from the completed code manifest, add
  the source-default/exact-reviewed-hash workflow gate, then run the repository
  suite and package checks. Independent source/input review still precedes
  any native attempt.

The sampled storage metric remains logical owned regular-file bytes plus
conservative image/writable-layer sizes. It is not a physical quota or an
observation of opaque daemon caches. This checkpoint supplies no native PASS
and does not remove any existing unqualified or blocked estate status.

## Continued source checkpoint, 14 November 2026

ChatGPT `/root/assess_zpm` continued the draft in an isolated worktree. It remains
unactivated: no active plan or native workflow job exists. The original inert
compiler, candidate inputs and caps are unchanged. Forty-three focused
controlled checks pass; they are source fixtures, not native qualification.

The draft now retains prospective image/container charges across uncertain
CLI outcomes and pending removal, compares declared image/container authority
and environments, shares one locked command-output counter across host and
guest writers, transfers only selected assembly receipt/log files to the
invoking owner, observes fixed guest phases from the host, and adds guest and
host absolute deadline checks. Empty tracked blobs are admitted; local Git
configuration and metadata indirection are checked before Git source commands.
Known host child identities are retained before a general process scan. A
report overflow or late resource failure cannot return a successful verdict.

The proposed mutation charges are 51,195,904 bytes for the base image, 512 MiB
for the one owned writable container layer, and their sum for the assembled
image. A further 1 MiB covers final report publication. All consume the original
4-GiB logical-storage envelope, are prospective conservative charges rather
than physical quotas, and still require independent source/plan review.

Remaining work before a complete candidate freeze: audit the new first-scan
process cleanup and native transport paths with fault fixtures; finish
assembly-to-qualification and final-report evidence validation; add the exact
code-bound inactive plan and source-default/exact-hash workflow; update the
canonical guide/map; and run repository and package checks. The current checks
do not substitute for any of those remaining gates or admit an active trial.

## Completed source candidate, 14 November 2026

The listed implementation gaps are now covered by the separate runner, its exact
code-bound plan and source-default workflow. Sixty-six focused source/fault
checks and the offline local package/CLI checks pass. The full local suite's one
existing `/proc/.../children` error was reproduced on untouched `7cb9306` and is
kept separate from regressions. The [source-validation record](native-alpine-runner-validation.md)
retains commands, exact plan/input identities and the limits of this evidence.
No candidate/native operation has run. Root and a nonauthor must review this
complete source candidate, then obtain the required hosted gates before an exact
native admission. The original frozen input documents remain unchanged.
