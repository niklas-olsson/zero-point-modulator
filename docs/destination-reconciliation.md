# Reconcile approved execution destinations

ZPM's explicit full destination mode places worker capacity across enrolled
OpenJules services using one local controller and journal. OpenJules retains
execution, admission, capture and recovery; Keystone Polyphony retains canonical
task allocation and acceptance. A destination choice does not assign a task.
The ordinary single-provider v1 controller remains available for v1 state.

This implements the consumer contract adopted in
[ZPM #50](https://github.com/niklas-olsson/zero-point-modulator/issues/50#issuecomment-6848027743)
and [OpenJules #47](https://github.com/niklas-olsson/OpenJules/issues/47#issuecomment-6848006932).
The separately scoped [role-local installer](role-installation.md) owns generated
bundles and their installed command trial. Actual two-host acceptance remains
[#51](https://github.com/niklas-olsson/zero-point-modulator/issues/51).
Two loopback services do not prove two independent hosts or a browser runtime.

## Explicit preparation and activation

Use Python 3.11 or later on a POSIX controller host. Keep the private SQLite
journal and controller lock on that one host. The controller needs only the
standard library. Each execution host needs its independently qualified
OpenJules full-mode installation, approved source/image, canonical authority
access and resources. Remote controller endpoints require verified HTTPS;
literal loopback HTTP is supported for local services. A browser is not an
execution environment qualified by this package.

Start with the existing [offline registry and approval procedure](offline-destination-registry.md).
Each destination starts with `enabled: false`. Obtain its original identity and
one-profile public approval through the producer's supported host-local workflow:
initialize a fresh schema-2 destination, preview the exact installed source/image
contract, review the complete public document, and approve it while the service
is stopped. Producer approval permits export of that document. It is not a
current readiness observation. OpenJules starts local admission closed; its host
operator must explicitly open it. ZPM cannot perform that host administration.

Import the exact public envelope into ZPM with `registry import-approval`.
A remote capability or execution-contract response cannot create or replace an
approval. A common profile name is insufficient; scope, original four-field
binding, manifest and digest must all match the retained approval.

Once all legacy reservations are released, all selected repository targets are
zero, and older controllers and configuration writers have stopped:

```sh
zpm --state .zpm/state.sqlite3 registry activate --offline
```

Activation atomically installs the full destination journal extension. It
preserves deployment identity, historical snapshots, slots, generations and
operation keys. Released v1 rows keep protocol 1 without an invented destination.
Pending or uncertain legacy work prevents activation. Merely pausing a nonzero
target is insufficient. Existing offline journals are readable without this
command and do not activate on open, approval import or status.

The `--offline` flag asserts an operator precondition; it cannot stop older
processes. The extension's exact-table/column fence refuses older v2 readers,
including an older v2 Store already open before activation. The original
[already-open v1 writer limitation](offline-destination-registry.md#migrate-released-legacy-state-offline)
still requires stopping those processes before the earlier v1-to-v2 migration.
Do not remove feature markers, copy a journal over live state or launch old
binaries as an automatic rollback.

Activation itself leaves every destination's enabled setting unchanged. After
activation, explicitly import a registry document with `enabled: true` for each
chosen destination, then enroll the existing repository binding with its desired
target. Registry syntax is unchanged except that active-mode import accepts an
exact boolean `enabled` value. `registry validate` continues to validate dormant
initialization inputs; active `registry import` validates its document before
committing it.

```sh
zpm --state .zpm/state.sqlite3 registry import private-enabled-destinations.json
zpm --state .zpm/state.sqlite3 enroll examples/repository.json
zpm --state .zpm/state.sqlite3 check --registry --max-actions 8
zpm --state .zpm/state.sqlite3 reconcile --registry --max-actions 16
zpm --state .zpm/state.sqlite3 reconcile --registry --watch --interval 5 --max-actions 16
```

Full mode is explicitly selected with `--registry`. The legacy `--provider`
path refuses v2 state. There is no fallback from a refused full request to a
legacy route, another endpoint or an identity-only worker.

## Readiness, budgets and capacity

A genuinely new reservation requires both current, scope-bound capability
checks and the exact approved execution-contract response. The existing
`managed_dispatch` and `repository_access` checks still apply. Full feature
versions must be exact integer 1; boolean aliases are refused. Capabilities
retain their existing extension/diagnostic behavior. The execution-contract,
admission, discovery and S/F snapshot modes have closed field sets.

The complete portable manifest must reproduce the operator-approved canonical
bytes and digest. Its public source base is distinct from the installed producer
Git revision. Host-local admission is outside the manifest and remains an
independent final producer decision.

Every remote read and mutation consumes the same bounded tick I/O budget.
New placement requires room for two readiness reads and one ensure, so its
minimum budget is **3**. A standalone destination check needs **2**. A budget of
1 can still observe or recover an original operation. Reports expose these
minimums and whether the selected limit can admit each kind of work. No partial
readiness sequence becomes permission for a later launch.

A persisted rotation covers destination/profile checks and original worker
operations. A refusing or unreachable destination consumes only its allotted
requests and cannot continually displace a healthy destination. Rotation is
retained across controller restarts. Current readiness is never a prerequisite
for recovering, observing, draining or stopping an original worker.

Before ensure I/O, one SQLite transaction rechecks enrollment revisions and
charges all unreleased reservations against deployment, repository and
destination ceilings. Starting, draining, failed-but-uncaptured and unknown
workers all remain charged. The same transaction records the original scope,
slot, generation, launch key, protocol, binding B, contract version, manifest H
and requested admission revision A. A concurrent target or configuration edit
cannot create a reservation under stale local permission.

The host may refuse at its final admission transaction even after positive
readiness. Its worker ceiling and close/reopen ordering are not replaced by
ZPM's local counts. ZPM does not use the admission observation as a release
receipt or as authority for protected task execution.

## Original recovery and retirement

Every ensure, discovery response and control response is checked against the
retained original scope and binding. Full rows additionally retain their original
H and A. Mixed inventory preserves original S/F modes and validates each row's
requested scope. Native worker IDs are scoped by destination and journal
incarnation; the same ID at another destination is not a collision or a substitute.

After a lost ensure reply, discovery addresses only the original destination,
launch key and qualification. An empty qualified response remains uncertain and
charged. A later fair turn may replay that exact ensure under its original tuple.
If current local policy has retired the reservation, ZPM continues original-key
discovery without resending ensure, then drains and stops a recovered original.
It cannot assume that an empty response proves absence of a delayed effect.

A permanent `never_admitted` receipt is different. It must retain the complete
original tuple and logical worker ID, exact terminal flags and empty execution
list, fixed outer reason `never_admitted`, and a valid typed cause/revision.
ZPM commits the complete validated receipt before releasing local capacity and
suppresses new keys for that destination for the remainder of the tick. A worker
previously observed as admitted cannot later become `never_admitted`. The
receipt proves that no effects or artifacts were created; it does not describe
a fabricated capture. Generic 409/503, timeout, missing state, changed identity,
closed admission and empty discovery never establish that result.

Normal release still requires the original terminal state, confirmed termination,
complete capture, closed worker admission and no active executions. Drain and
stop request keys remain stable across lost replies. Host admission closure does
not prevent original observation, drain, stop, capture or qualified recovery.
A guest/environment stop is not canonical task cancellation or acceptance.

Registry imports are **additive**. Omitting a destination or profile does not
remove or retire it and can leave it enabled. To retire a destination, import its
retained record with `enabled: false`; target and ceiling changes can also retire
excess capacity. Retired records and original reservations remain available for
recovery. Disabled credentials or an unreachable original endpoint can leave a
reservation uncertain indefinitely; they do not authorize moving it elsewhere.

An explicit endpoint or credential-reference update changes routing for the same
enrolled identity. Every response must still match the original journal
incarnation and binding. Replacing a host journal requires a new destination ID;
registry import cannot change an existing incarnation. Changing a selected policy
binding retires old capacity and requires its own approval before new placement.
Old approvals and operations remain retained.

## Observe without granting authority

`status`, `plan` and `registry status` read retained local records. Active output
reports the selected registry revision, destination ceilings, reserved and
uncertain counts, per-profile approval/readiness observations, repository targets,
healthy capacity, shortfall and pending original operations. An observation has a
check time and the enrollment revisions it used. A later enrollment change marks
it stale. The output explicitly states that status does not refresh or grant
execution. Endpoints and credential-reference names are omitted.

Controller state persists on the local controller host. Runtime execution,
workspaces and captured runtime state reside on the selected execution host;
canonical task/candidate records and accepted artifact storage reside in
Polyphony's configured authority installation. Any configured model path remains
the producer's approved host broker/provider path, not browser-local inference.
This package introduces no browser credential delivery, inference facility,
project UI, publication permission or task authority.

## Qualification and limits

`tests/test_destination_dispatch.py` exercises real SQLite and the actual
consumer transport/projection boundary with explicitly deterministic host
fixtures. It covers restart, lost replies before/after admission, tiny budgets,
fairness, original recovery after retirement, malformed identity/receipts,
aggregate ceilings and shrink, concurrent target edits, missing native state,
scoped IDs, closed host controls, legacy retention and old-reader fencing.
Fixtures do not establish installed execution.

`tests/contracts/check_destination_stack.py` extends the existing installed
managed-stack trial at its exact pins. It uses two real full-mode OpenJules
service processes with separate journals, real source/image inspection and
supported approval commands, one real canonical authority, the actual ZPM HTTP
adapter and real Docker execution. The finite scene checks lost-reply recovery,
service outage/reopen, original recovery while host admission is closed, native
stale-revision refusal, mixed retained inventory, canonical capture and reopened
results. Injected reply loss and the controlled admission race use the source
controller with the actual HTTP adapter. The final ordinary placement, check
and retirement phase uses the installed `zpm check/reconcile --registry` CLI.
Model calls and child delegations are zero. Trial commands have finite deadlines
and streaming output bounds. All service credentials and raw state remain
private; only the bounded aggregate report is exported.

The compatible component set is:

| Component | Qualification revision |
| --- | --- |
| ZPM | The exact workflow checkout; pull requests run GitHub's proposed merge revision. |
| OpenJules | `7135d0988dff050df63acb89f75562dafccacf79`, merged full destination producer from [#64](https://github.com/niklas-olsson/OpenJules/pull/64). |
| Keystone Polyphony | `6480e36355bf0d5de4e25a560d40e665fd76038c`, the unchanged canonical authority. |

The producer's [actual-main checks](https://github.com/niklas-olsson/OpenJules/actions/runs/43609471106)
passed at that exact revision. The consumer's native trial is a separate required
managed-stack gate, alongside ordinary package/CLI and existing installed v1
checks. Its report records the actual ZPM revision and this component set; a
producer-only pass or historical consumer report does not qualify a changed set.
The current run and closeout evidence are recorded in
[#50](https://github.com/niklas-olsson/zero-point-modulator/issues/50). No two-host, live model,
browser-local inference, portable distribution or independent semantic acceptance
claim follows from this consumer trial.
