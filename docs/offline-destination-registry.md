# Prepare a disabled destination registry

ZPM can retain operator-approved execution-destination identities in a private
version 2 journal. This is an **offline preparation feature**. It does not check
an OpenJules service, resolve a credential, qualify an execution policy, admit a
worker or enable distributed reconciliation. Every destination stays disabled.
The existing version 1 installation and controller remain the execution path.

The consumer work is tracked in [#50](https://github.com/niklas-olsson/zero-point-modulator/issues/50),
following the [adopted OpenJules identity contract](https://github.com/niklas-olsson/OpenJules/issues/47#issuecomment-6846632568).
Portable execution approval, durable local admission/refusal, v2 transport and
bounded multi-destination reconciliation are later work. Actual two-host
installation and acceptance belong to [#51](https://github.com/niklas-olsson/zero-point-modulator/issues/51).

## Explicit configuration

Start from [destination-registry.json](../examples/destination-registry.json).
Its identities and endpoint are illustrative placeholders, not verified service
claims. Obtain real identity values through the producer's supported host-local
inspection and operator approval when that producer feature is available. A
remote response must not enroll its own identity.

| Record | Fields |
| --- | --- |
| Registry policy | Required `schema_version: 2`, nonnegative `max_workers`, and `timeout_seconds` greater than zero and at most 120. These are retained inputs for future execution; this slice makes no requests. |
| Each `destinations` entry | `destination_id`, `journal_incarnation`, `endpoint`, `token_ref`, nonnegative `max_workers`, and explicit `enabled: false`. |
| Each `profiles` entry | `destination_id`, canonicalizable GitHub `repository`, existing OpenJules `profile`, `policy_binding_id`, and integer `policy_binding_version: 1`. |

All fields are required. Unknown and duplicate JSON fields, duplicate destination
or profile identities, boolean version aliases and nonfinite numbers are
rejected. Destination IDs use 1–128 ASCII letters, digits, periods, underscores
or hyphens, beginning with a letter or digit. Incarnation and policy-binding IDs
contain exactly 32 lowercase hexadecimal characters. They are opaque retained
identifiers, not private configuration hashes or portable execution digests.
Each imported profile must name a destination included in the same import.

Endpoint and credential-reference validation reuse the existing provider rules:
verified HTTPS remotely, literal loopback HTTP locally, and `env:VARIABLE_NAME`
references. Configuration stores reference names, never their environment values.
Keep the input and journal private. Ordinary registry output omits endpoints and
credential-reference names.

## Initialize and inspect without remote access

```sh
zpm registry validate examples/destination-registry.json
zpm --state .zpm/offline-registry.sqlite3 --deployment-id my-deployment \
  registry init examples/destination-registry.json
zpm --state .zpm/offline-registry.sqlite3 registry status
zpm --state .zpm/offline-registry.sqlite3 plan
```

Validation does not create a journal. Registry initialization requires a new
file and refuses an existing file or symlink. The local SQLite journal uses mode
0600; initialization syncs the file and parent directory before reporting success.
Existing v2 journals must already contain their original identity and
complete supported format; opening them does not reconstruct missing state.
Use the same local journal across restarts. Concurrent copies, cross-host SQLite
and automatic journal replacement are unsupported.

Status reports the deployment identity, registry/destination/profile revisions,
expected identities, configured limits and retained legacy-history count. It
always reports `execution_enabled: false`, `ready: false` and
`remote_access_checked: false`. These are local records, not verified readiness
or available capacity. The ordinary `plan` and `status` commands select this same
local projection for v2 state.

The v1 `check`, `reconcile`, enrollment/capacity mutations and controller API
reject v2 state before credentials or provider I/O. The v1 HTTP adapter also
rejects destination-bound input rather than sending it to the legacy route.
There is no enable command or v2 launch fallback in this release.

## Import changes while retaining history

```sh
zpm --state .zpm/offline-registry.sqlite3 \
  registry import private-destination-updates.json
```

An explicit import updates the registry policy and supplied records atomically.
An identical normalized import is idempotent. A changed import increments the
registry revision and retains each changed destination/profile record at that
revision. Omitting a destination or profile does not delete it. Imports cannot
enable destinations or change an enrolled destination's journal incarnation.
A replacement journal requires a new destination ID in this initial version.
An existing opaque policy-binding ID cannot be reused for a different profile
identity within the same destination.

Endpoint/reference changes and selection of a new opaque policy-binding ID are
stored as disabled local revisions. Earlier transport/profile records remain
available in the private journal. Importing a candidate does not validate it,
contact it, rotate a remote credential, release a reservation or retire a worker.
Remote validation, admission and operational retirement require the later
consumer implementation.

## Migrate released legacy state offline

Migration is one-way for this release. Keep using v1 while any worker or operation
remains unresolved. Before migration, set every repository's selected target to
zero, reconcile through the existing supported provider, and confirm that every
reservation meets its original termination/capture/admission/execution release
conditions. A paused nonzero selected target is insufficient. Unknown outcomes,
failed capture and pending operations prevent conversion.

**Stop every old controller and every configuration writer before conversion.**
This includes watch processes and already-open library clients. The `--offline`
flag asserts that operator precondition; it cannot prove it. The controller lock
excludes another supported controller, but old enrollment/configuration commands
did not take that lock.

With the state quiescent, choose a new backup filename in an existing local
directory:

```sh
zpm --state .zpm/state.sqlite3 registry migrate private-destination-updates.json \
  --backup .zpm/state-before-v2.sqlite3 --offline
```

Migration first checks eligibility, creates a new 0600 SQLite backup using the
backup API so committed WAL contents are included, verifies its integrity and
logical contents, and syncs the backup file and its parent directory before
conversion. An existing backup or symlink is refused. Conversion then
rechecks eligibility and changes schema/registry records in one transaction.
Failure before commit leaves the original journal unconverted; an already
created backup is retained. Use a different new backup name for another attempt.

Deployment identity, repository records, worker snapshots, slots, generations,
launch keys and action keys remain unchanged. Historical worker rows are marked
protocol 1 with no destination binding; migration does not guess where they ran.
The original metadata table is retained with a changed value-column name and an
explicit journal version. A fresh old reader fails at its mandatory metadata
read before it can construct a provider, rather than silently ignoring v2 data
or creating a replacement deployment identity.

That fence does **not** stop an old Store object already open across conversion.
Such a process can still change preserved legacy tables. This is why live
migration and old running binaries are unsupported. New-code Store/controller
objects recheck the actual format at their legacy entry points, including objects
opened before the transition. Do not restore an older copied journal over live
bound state or run an old binary as an automatic rollback.

## Evidence and scope

`tests/test_registry.py` exercises real local SQLite/CLI behavior, a WAL-aware
backup, interrupted-conversion rollback, preserved released history, disabled
imports and refusal before credentials/provider access. Its frozen
`tests/fixtures/legacy_store_a2a9f9b.py` is the exact published Store source from
ZPM `a2a9f9bad1e42c575b5c1bbae97fa4c94527e5f4`; the test verifies its SHA-256 before
using it to prove fresh-reader refusal and the already-open-writer limitation.
Worker history is generated by the existing deterministic provider fixture.
These tests do not establish runtime execution, two-service interoperability,
two-host installation, portable policy qualification or external publication.
