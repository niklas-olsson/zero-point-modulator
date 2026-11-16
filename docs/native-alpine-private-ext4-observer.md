# Source-only private ext4 census owner

This is a reviewable, injected-effect prototype for the existing owner of
[#63](https://github.com/niklas-olsson/zero-point-modulator/issues/63). It does not
select a native14 plan, change PR69's consumed native13 inputs, or implement a
privileged backend. No filesystem, loop device, mount, Docker container, ioctl,
process probe or downloaded input is used by this source slice.

The implementation is [private_ext4_observer.py](../tests/contracts/private_ext4_observer.py)
and its [injected fixtures](../tests/test_private_ext4_observer.py). Its default
command emits one bounded source-policy JSON value to stdout; `--run` is not an
option:

```sh
python3 -I -B tests/contracts/private_ext4_observer.py
python3 -B -m unittest tests.test_private_ext4_observer -v
```

## Actual guarantee and storage charge

The original [native13 contract](https://github.com/niklas-olsson/zero-point-modulator/blob/b74ca07a767b63c184b7f91685ba996490a2ce16/docs/native-alpine-active-harness.md#L554-L590)
specifies a sampled logical 4-GiB stop, with collection latency and reported
maximum gaps. It disclaims an instantaneous peak and physical quota. The
[plan](../tests/contracts/native-alpine/active-plan-13.json) and
[runtime](../tests/contracts/check_alpine_sealed_runtime.py) retain that scope:
missing unreserved entries refuse rather than becoming zero. Docker mutations,
thirteen named atomic temporary paths and final publication have prospective
reservations. Ordinary generated work files are sampled separately; no
whole-producer reservation is present. A stable census can address that sampled
requirement without proving a continuous logical-byte ceiling.

This prototype selects one **1,073,741,824-byte backing logical charge** per
declaration. It creates no image or hypothetical binary hash. Its census callback
returns `(contents_bytes, other_original_charges_bytes)`, where contents excludes
the backing image and the other term excludes both backing and contents, but
retains every original input/source/Docker/transient/report/log charge. The owner
always adds **backing + contents + other** and refuses totals above
4,294,967,296. The backing image and its contents are charged separately; their
storage overlap is never deducted. An overflow counter remains in the record.

A 1-GiB image can limit allocated inner filesystem space, including filesystem
metadata, but sparse logical file lengths can exceed its capacity. Between thaws
the original metric remains sampled. The largest completed native13 sample,
1,635,609,659 bytes, is neither a bound for its failed census nor proof that a
producer completes within the candidate image. Fit, performance, tool and kernel
eligibility remain unqualified. Open-unlinked files, physical block reclaim,
opaque daemon caches and an atomic multi-root/Docker snapshot are not added to
the original metric.

## Retained declaration and one causal cycle

`OwnedFilesystem` contains only a bounded immutable declaration. `identity_key`
checks exact types and its internal graph: one 0600 single-link regular backing
file with its original FD/dev/inode/UID/size, one block-device loop FD/dev/inode/
rdev bound to that backing at zero offset and unrestricted size, one private
0700 ext4 root FD/dev/inode/UID/mount ID/source rdev/UUID/mount-namespace identity,
and a separate 0700 control-directory FD on another filesystem. Autoclear and
read-only loop declarations refuse. The work-bind source must name the same
root and the declared Docker daemon namespace must match the mounted namespace.
Nonzero raw-writer or unrelated-consumer declarations refuse.

Those checks establish only declaration consistency. Caller-supplied metadata,
zero writer counts, UUIDs and Boolean acknowledgements are **not kernel evidence
or authority**. No collector, mount provisioner, exclusivity proof, restore or
adoption path exists here. A future reviewed existing-Trial adapter must retain
actual original descriptors, prove the mount is visible to the real daemon,
verify the complete guest bind/configuration and exclude unrelated consumers and
raw backing writers. A mount created only in an isolated helper namespace is
insufficient. Keep the original filesystem owner; do not construct another owner
to bypass an unresolved cycle or adopt a projected journal.

Each `FrozenCensus` accepts the original attempt start and monotonic clock; its
work deadline is start + 1,710 seconds and total deadline start + 1,800 seconds.
It cannot renew either clock. A clock exception, nonfinite observation or
regression permanently blocks later effects. One owner can dispatch at most one
freeze and one matching thaw, perform one census and retain one first failure.
The successful cycle invokes at most six identity callbacks, two checkpoints,
two dispatchers and one census callback. There is no scheduler or polling loop.
Full-trial sample frequency, episode count, local ioctl time bounds and added
helper costs remain separate future source-review obligations inside the
unchanged original total/cleanup/resource limits.

Before a dispatch, the owner retains its original opaque `Request`, projects
`PREPARED` intent through an injected checkpoint outside the frozen filesystem,
then verifies identity and the original deadline again. A checkpoint that fails
or returns late admits no effect or replay. A checkpoint's retained PREPARED
record is conservative intent, not proof that no later dispatch occurred. The
dispatcher must synchronously retain that exact ticket before beginning an
operation, and must check its immutable absolute deadline at the actual effect
boundary. It must never turn a delayed queue callback into a fresh relative
timeout.

Each callback return also checks the sealed declaration and original tickets in
memory, without an extra host read. Census collection always receives the cached
original root FD. A changed accepted freeze ticket or declaration cannot admit
a census through an intervening clock callback. Reentrant control latches a
first failure even if the callback catches the refusal. The first refusal in
each active call is latched separately from the original first failure, including
a caught public-projection refusal after ticket mutation followed by repair.
The outer operation then stops before
any new work effect. A later, separate matching cleanup thaw remains
possible for an already-acknowledged original freeze. Misuse during the cleanup
call itself still stops its continuation; it grants no retry.

Dispatch exceptions or late returns retain PENDING uncertainty. Only completion
of that same object is accepted; equal copied tickets, another owner's ticket,
duplicates and non-Boolean completions refuse. A late original acknowledgement
is retained without issuing a new identity read. A positive late freeze can
permit one matching cleanup thaw if identity can still be verified within the
original total deadline, but it can never qualify a census. No thaw is issued
while the original freeze is pending, after a refusal or after total expiry.
An identity mismatch/unavailable observation permanently blocks further effects;
no restored path or fresh FD repairs the latch.

The census is admitted only after a timely original freeze acknowledgement. It
revalidates identity before and after collection and checks the same work clock
after each callback. Missing, malformed, negative, Boolean or late counters
refuse. It does not run the existing `Trial.run` command loop, whose polling
would recurse into sampling. It must not acquire the guest output-budget flock
or perform phase/control writes under `/work` while frozen. Those operations
resume only after the matching original thaw. Census failure/overflow can still
release the declared barrier within the original cleanup budget; the original
failure remains first.

Snapshot projections are limited to 8,192 bytes and contain a copy of the bounded
declaration, original deadlines, exact stage states and counters. They never
contain callback errors, paths or stacks. Fixture success is labeled
`DECLARED_CYCLE_COMPLETE`; `actual_kernel_evidence` remains `NOT_COLLECTED`,
`native_execution_qualified` is false and filesystem retirement is `NOT_MEASURED`.
`declared_barrier_release=ACKNOWLEDGED` records only matching injected successful
acknowledgements before the deadline; it is not actual filesystem-release
evidence. Missing, failed, late or ownership-invalid release stays `UNKNOWN`.
The projected journal grants no completion, restore or privileged authority.
The fixed-key scalar envelope is 3,365 bytes: all admitted string values are
closed ASCII of at most 64 characters, numbers use fewer than 32 representation
bytes, and nullable slots retain the conservative longer bound. A maximum-width
identity/overflow fixture and the envelope both remain below the enforced cap.

## New authority and missing backend proof

Real setup requires a separately reviewed host helper with loop/mount and
`CAP_SYS_ADMIN` authority in the selected superblock's user namespace. That is
new host authority, absent from native13. Guest capabilities, read-only inputs,
network policy and all consumed plans remain unchanged here. No host device or
control socket is proposed inside the guest.

The actual backing image and private ext4 mount would have to be established and
journaled before native13's existing `/work/output-budget.bin` seed and Docker
bind creation. Formatting/raw setup writers must finish before barrier admission.
The original unprivileged child guardian must not be assumed able to stop or
drain a root helper. The backend must bind the exact original mount FD, operation
and privileged process custody, persist intent outside the filesystem, and
attempt bounded matching release on every census outcome. No ordinary host-root
or unrelated superblock may be frozen.

There is no hard cancellation promise. Kernel freeze may block in write
protection, journal or I/O waits. Timeout, helper exit, a closed FD or killing a
waiting client does not prove that an already-issued ioctl could not complete
later. A thaw must never race ahead of an unresolved freeze. Pending operations,
late/failed thaw, extra holders or unverifiable identities leave original
filesystem release unknown. They do not permit a speculative thaw, a new helper,
unmount/detach/retry, cap increase or claim that job teardown proved cleanup.
Unmount, loop detach, backing retirement and full owned-container cleanup are
outside this one-cycle source slice and need their own original-owner evidence.

## Pinned primary-source basis

These are reference-source facts at Linux
`028ef9c96e96197026887c0f092424679298aae8`, not attestation of native13's kernel.

- [FIFREEZE/FITHAW](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/ioctl.c#L385-L412)
  operate on the retained file's superblock and check `CAP_SYS_ADMIN`. The
  userspace holder is NULL; neither operation is an FD-close auto-thaw lease.
- [Superblock freeze](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/super.c#L2070-L2157)
  drains normal writes, page faults and internal filesystem writers, syncs and
  calls the filesystem freeze operation before COMPLETE. Its
  [write wait](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/super.c#L1848)
  has no userspace absolute-deadline argument. Matching
  [thaw](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/super.c#L2167-L2211)
  can retain holds or fail; it cannot be inferred from client termination.
- [Ext4 freeze](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/ext4/super.c#L6476-L6508)
  locks journal updates, flushes the journal and commits its superblock before
  acknowledging success.
- Legacy regular-file AIO
  [starts write protection](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/aio.c#L1607-L1634)
  before write_iter and
  [ends it on completion](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/fs/aio.c#L1490-L1507).
  [kiocb_start/end_write](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/include/linux/fs.h#L2740-L2767)
  hold real superblock write protection across asynchronous completion; the
  intermediate release/reacquire is lockdep bookkeeping. This supports a
  filesystem writer barrier despite the task-freezer/AIO gap, without claiming
  all AIO contexts destroyed, raw-device writers drained or actual13 causation.

## Source acceptance and next boundary

Injected fixtures cover original-ticket custody, pending/late/ambiguous dispatch,
deadline expiry before and after callbacks, refused/missing/late thaw, ownership
and namespace/FD changes, reentrancy, immutable public deadlines and first
failure, sparse logical overflow, double charging, record copying and a bounded
maximum-width projection. A simulated pending write is drained by the injected
backend before its acknowledgement; this tests causal ordering, not Linux.

Native13's original lstat ENOENT, missing path/cause details and cleanup PASS are
preserved. The prototype does not diagnose that failure or qualify the candidate.
The existing #63 owner must independently review this slice, then choose or
reject a concrete collector, privileged original-operation backend, lifecycle
and exact host/tool/cap plan. A separately admitted native operation would still
be required; no active workflow or native14 admission is added here.
