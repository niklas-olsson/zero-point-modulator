"""One retained freeze/census/thaw cycle; no privileged or filesystem backend.

Only injected callbacks can observe identities, checkpoint or dispatch effects.
The default CLI emits source policy data. Nothing imports this into native13.
See docs/native-alpine-private-ext4-observer.md before supplying any callbacks.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math


BACKING_BYTES = 1024**3
STORAGE_BYTES = 4 * 1024**3
WALL_SECONDS = 1800
CLEANUP_SECONDS = 90
RECORD_BYTES = 8192
UINT64 = 2**64 - 1


class Refused(ValueError):
    """Closed source-helper codes; never format an injected error."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= UINT64


def finite(value):
    return type(value) in (int, float) and 0 <= value < 2**53 and math.isfinite(value)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class Backing:
    fd: int
    dev: int
    ino: int
    uid: int
    mode: int
    links: int
    size: int


@dataclass(frozen=True)
class Loop:
    fd: int
    dev: int
    ino: int
    rdev: int
    mode: int
    backing_dev: int
    backing_ino: int
    offset: int
    size_limit: int
    read_only: bool
    autoclear: bool


@dataclass(frozen=True)
class Mount:
    fd: int
    dev: int
    ino: int
    uid: int
    mode: int
    mount_id: int
    namespace_dev: int
    namespace_ino: int
    source_rdev: int
    fs_type: str
    fs_uuid: str


@dataclass(frozen=True)
class Control:
    fd: int
    dev: int
    ino: int
    uid: int
    mode: int


@dataclass(frozen=True)
class OwnedFilesystem:
    owner: str
    uid: int
    backing: Backing
    loop: Loop
    mount: Mount
    control: Control
    daemon_namespace_dev: int
    daemon_namespace_ino: int
    work_bind_dev: int
    work_bind_ino: int
    unrelated_consumers: int
    raw_writers: int


def identity_key(value):
    """Validate a bounded declaration, not collect or attest kernel evidence."""
    require(type(value) is OwnedFilesystem, "IDENTITY_TYPE")
    require(type(value.owner) is str and len(value.owner) == 32
            and all(c in "0123456789abcdef" for c in value.owner), "OWNER_DECLARATION")
    require(integer(value.uid, 1), "OWNER_UID")
    for row, expected in ((value.backing, Backing), (value.loop, Loop),
                          (value.mount, Mount), (value.control, Control)):
        require(type(row) is expected, "IDENTITY_TYPE")
        require(integer(row.fd, 3) and integer(row.dev, 1) and integer(row.ino, 1), "FD_IDENTITY")
    b, loop, mount, control = value.backing, value.loop, value.mount, value.control
    require(type(b.mode) is int and b.mode == 0o100600 and type(b.links) is int and b.links == 1
            and type(b.size) is int and b.size == BACKING_BYTES
            and type(b.uid) is int and b.uid == value.uid, "BACKING_DECLARATION")
    require(integer(loop.rdev, 1) and type(loop.mode) is int and 0 <= loop.mode <= 0o177777
            and loop.mode & 0o170000 == 0o060000
            and type(loop.backing_dev) is int and loop.backing_dev == b.dev
            and type(loop.backing_ino) is int and loop.backing_ino == b.ino
            and type(loop.offset) is int and loop.offset == 0
            and type(loop.size_limit) is int and loop.size_limit == 0
            and loop.read_only is False and loop.autoclear is False, "LOOP_BINDING")
    require(type(mount.uid) is int and mount.uid == value.uid
            and type(mount.mode) is int and mount.mode == 0o40700
            and integer(mount.mount_id, 1) and integer(mount.namespace_dev, 1)
            and integer(mount.namespace_ino, 1) and type(mount.source_rdev) is int
            and mount.source_rdev == loop.rdev and type(mount.fs_type) is str
            and mount.fs_type == "ext4" and type(mount.fs_uuid) is str
            and len(mount.fs_uuid) == 32 and all(c in "0123456789abcdef" for c in mount.fs_uuid),
            "MOUNT_BINDING")
    require(type(control.uid) is int and control.uid == value.uid
            and type(control.mode) is int and control.mode == 0o40700
            and control.dev != mount.dev and b.dev != mount.dev, "CONTROL_OUTSIDE_FILESYSTEM")
    require(type(value.daemon_namespace_dev) is int and value.daemon_namespace_dev == mount.namespace_dev
            and type(value.daemon_namespace_ino) is int and value.daemon_namespace_ino == mount.namespace_ino
            and type(value.work_bind_dev) is int and value.work_bind_dev == mount.dev
            and type(value.work_bind_ino) is int and value.work_bind_ino == mount.ino, "WORK_BINDING")
    require(type(value.unrelated_consumers) is int and value.unrelated_consumers == 0
            and type(value.raw_writers) is int and value.raw_writers == 0, "EXCLUSIVE_FILESYSTEM")
    require(len({b.fd, loop.fd, mount.fd, control.fd}) == 4, "FD_ALIAS")
    return encoded(asdict(value))


@dataclass(frozen=True)
class Request:
    """Original opaque ticket, handed only to an injected effect dispatcher."""
    operation: str
    root_fd: int
    identity_sha256: str
    deadline: float


class FrozenCensus:
    """Single cycle. No retries, polling, restore/adoption or resource teardown.

    observe() must collect exact current ownership; checkpoint(record) must save
    outside the selected filesystem; dispatch(request) owns its original async
    operation and later returns that SAME request to complete_*(). There is no
    real implementation of those ports here. Their missing host proof obligations
    are explicit in the contract, not silently delegated to the native harness.
    """

    def __init__(self, identity, *, started, clock):
        self._identity_key = identity_key(identity)
        require(finite(started) and finite(started + WALL_SECONDS) and callable(clock), "ORIGINAL_CLOCK")
        self._identity = identity
        self._root_fd = identity.mount.fd
        self._identity_sha256 = hashlib.sha256(self._identity_key).hexdigest()
        self._started = started
        self._work_end = started + WALL_SECONDS - CLEANUP_SECONDS
        self._end = started + WALL_SECONDS
        self._clock = clock
        self._last_time = started
        self._first_failure = None
        self._identity_lost = False
        self._clock_lost = False
        self._control_misuse = False
        self._current_misuse = False
        self._busy = False
        self._freeze = self._thaw = None
        self._freeze_state = self._thaw_state = "NOT_ISSUED"
        self._freeze_timely = self._thaw_timely = False
        self._census = "NOT_RUN"
        self._contents = self._other = self._total = None

    @property
    def identity(self):
        return self._identity

    @property
    def started(self):
        return self._started

    @property
    def work_end(self):
        return self._work_end

    @property
    def end(self):
        return self._end

    @property
    def first_failure(self):
        return self._first_failure

    def _refuse(self, code):
        if self._first_failure is None:
            self._first_failure = code
        raise Refused(code)

    def _time(self, end=None):
        if self._clock_lost:
            self._refuse("CLOCK_UNTRUSTED")
        try:
            now = self._clock()
        except BaseException:
            self._clock_lost = True
            self._refuse("CLOCK_UNAVAILABLE")
        self._after_callback()
        if not finite(now) or now < self._last_time:
            self._clock_lost = True
            self._refuse("CLOCK_INVALID")
        self._last_time = now
        if end is not None and now >= end:
            self._refuse("ORIGINAL_DEADLINE")
        return now

    def _observe(self, observe):
        if self._identity_lost:
            self._refuse("OWNERSHIP_LOST")
        try:
            current = observe()
        except BaseException:
            self._identity_lost = True
            self._refuse("IDENTITY_UNAVAILABLE")
        self._after_callback()
        try:
            key = identity_key(current)
        except BaseException:
            self._identity_lost = True
            self._refuse("IDENTITY_UNAVAILABLE")
        if key != self._identity_key:
            self._identity_lost = True
            self._refuse("OWNERSHIP_CHANGED")

    def _enter(self):
        if self._busy:
            self._control_misuse = self._current_misuse = True
            self._refuse("REENTRANT_CONTROL")
        self._current_misuse = False
        self._busy = True

    def _after_callback(self):
        """Pure retained-data fence, with no additional observation callback."""
        if self._current_misuse:
            self._refuse("REENTRANT_CONTROL")
        try:
            original = identity_key(self._identity)
        except BaseException:
            self._identity_lost = True
            self._refuse("IDENTITY_UNAVAILABLE")
        if original != self._identity_key:
            self._identity_lost = True
            self._refuse("OWNERSHIP_CHANGED")
        for request, kind in ((self._freeze, "FREEZE"), (self._thaw, "THAW")):
            if request is not None and not self._request_valid(request, kind):
                self._refuse("REQUEST_CHANGED")

    def _request_valid(self, request, kind):
        end = self._work_end if kind == "FREEZE" else self._end
        return (type(request) is Request and type(request.operation) is str and request.operation == kind
                and type(request.root_fd) is int and request.root_fd == self._root_fd
                and type(request.identity_sha256) is str and request.identity_sha256 == self._identity_sha256
                and finite(request.deadline) and request.deadline == end)

    def _start(self, kind, *, observe, checkpoint, dispatch):
        self._enter()
        request = None
        issued = False
        try:
            if kind == "FREEZE":
                if self._freeze is not None or self._first_failure is not None:
                    self._refuse("FREEZE_REPLAY")
                end = self._work_end
            else:
                if self._freeze_state != "ACK" or self._thaw is not None or self._census == "PENDING":
                    self._refuse("THAW_WITHOUT_RESOLVED_FREEZE")
                end = self._end
            self._after_callback()
            self._time(end)
            request = Request(kind, self._root_fd, self._identity_sha256, end)
            if kind == "FREEZE":
                self._freeze, self._freeze_state = request, "PREPARED"
            else:
                self._thaw, self._thaw_state = request, "PREPARED"
            # Pending intent is visible before either the checkpoint or effect.
            try:
                checkpoint(self.snapshot())
            except BaseException:
                self._refuse("CHECKPOINT_UNCONFIRMED")
            self._after_callback()
            self._time(end)
            self._observe(observe)
            self._time(end)  # A delayed identity read never admits a late effect.
            if kind == "FREEZE":
                self._freeze_state = "PENDING"
            else:
                self._thaw_state = "PENDING"
            issued = True  # An exception can occur after the real effect starts.
            try:
                dispatch(request)
            except BaseException:
                self._refuse(kind + "_DISPATCH_UNKNOWN")
            self._after_callback()
            self._time(end)
            return request
        finally:
            if request is not None and not issued:
                if kind == "FREEZE":
                    self._freeze_state = "NOT_ISSUED"
                else:
                    self._thaw_state = "NOT_ISSUED"
            self._busy = False

    def start_freeze(self, *, observe, checkpoint, dispatch):
        return self._start("FREEZE", observe=observe, checkpoint=checkpoint, dispatch=dispatch)

    def start_thaw(self, *, observe, checkpoint, dispatch):
        return self._start("THAW", observe=observe, checkpoint=checkpoint, dispatch=dispatch)

    def _complete(self, kind, request, success, observe):
        self._enter()
        try:
            original = self._freeze if kind == "FREEZE" else self._thaw
            state = self._freeze_state if kind == "FREEZE" else self._thaw_state
            if request is not original or original is None or state != "PENDING":
                self._refuse("UNOWNED_COMPLETION")
            if not self._request_valid(request, kind):
                self._refuse("REQUEST_CHANGED")
            if type(success) is not bool:
                self._refuse("COMPLETION_TYPE")
            # Retain the original acknowledgement even if validation later fails.
            if kind == "FREEZE":
                self._freeze_state = "ACK" if success else "REFUSED"
            else:
                self._thaw_state = "ACK" if success else "REFUSED"
            if not success:
                self._refuse(kind + "_REFUSED")
            end = self._work_end if kind == "FREEZE" else self._end
            self._time(end)  # Late original acknowledgements add no new reads.
            self._observe(observe)
            self._time(end)
            if kind == "FREEZE":
                self._freeze_timely = True
            else:
                self._thaw_timely = True
        finally:
            self._busy = False

    def complete_freeze(self, request, *, success, observe):
        self._complete("FREEZE", request, success, observe)

    def complete_thaw(self, request, *, success, observe):
        self._complete("THAW", request, success, observe)

    def census(self, *, observe, collect):
        self._enter()
        try:
            if (self._first_failure is not None or self._freeze_state != "ACK" or not self._freeze_timely
                    or self._thaw is not None or self._census != "NOT_RUN"):
                self._refuse("CENSUS_NOT_ADMITTED")
            self._after_callback()
            self._time(self._work_end)
            self._observe(observe)
            self._time(self._work_end)
            self._census = "PENDING"
            try:
                values = collect(self._root_fd, self._work_end)
            except BaseException:
                self._census = "FAILED"
                self._refuse("CENSUS_UNAVAILABLE")
            if (type(values) is not tuple or len(values) != 2
                    or not all(integer(v) for v in values)):
                self._census = "FAILED"
                self._refuse("CENSUS_TYPE")
            self._contents, self._other = values
            self._total = BACKING_BYTES + self._contents + self._other
            self._census = "OBSERVED"
            self._after_callback()
            self._time(self._work_end)
            self._observe(observe)
            self._time(self._work_end)
            if self._total > STORAGE_BYTES:
                self._refuse("LOGICAL_STORAGE_BOUND")
            self._census = "COMPLETE"
        finally:
            self._busy = False

    def snapshot(self):
        """Bounded private journal projection; never restore arbitrary records."""
        for request, kind in ((self._freeze, "FREEZE"), (self._thaw, "THAW")):
            if request is not None and not self._request_valid(request, kind):
                self._refuse("REQUEST_CHANGED")
        released = (self._freeze_state == "ACK" and self._thaw_state == "ACK"
                    and self._thaw_timely and not self._identity_lost and not self._clock_lost)
        complete = released and self._freeze_timely and self._census == "COMPLETE" and self._first_failure is None
        record = {
            "schema": "zpm-private-ext4-census-source/v1", "scope": "ONE_INJECTED_CYCLE",
            "native_execution_qualified": False, "filesystem_retirement": "NOT_MEASURED",
            "sample": "DECLARED_CYCLE_COMPLETE" if complete else "UNQUALIFIED",
            "actual_kernel_evidence": "NOT_COLLECTED",
            "first_failure": self._first_failure, "identity_sha256": self._identity_sha256,
            "identity": json.loads(self._identity_key), "ownership_lost": self._identity_lost,
            "clock_lost": self._clock_lost,
            "control_misuse": self._control_misuse,
            "work_deadline": self._work_end, "total_deadline": self._end,
            "freeze": {"state": self._freeze_state, "timely_ack": self._freeze_timely},
            "thaw": {"state": self._thaw_state, "timely_ack": self._thaw_timely},
            "declared_barrier_release": "ACKNOWLEDGED" if released else "UNKNOWN",
            "census": self._census, "backing_bytes": BACKING_BYTES,
            "contents_bytes": self._contents, "other_original_charges_bytes": self._other,
            "logical_total_bytes": self._total, "storage_cap_bytes": STORAGE_BYTES,
            "continuous_logical_bound": False,
        }
        require(len(encoded(record)) <= RECORD_BYTES, "RECORD_BOUND")
        return record


def source_policy():
    return {
        "schema": "zpm-private-ext4-observer-policy/v1", "status": "SOURCE_ONLY_NOT_RUN",
        "privileged_backend": "NOT_IMPLEMENTED", "native_harness_integration": False,
        "new_authority_required": "reviewed host CAP_SYS_ADMIN loop/mount/freeze helper",
        "cycles_per_owner": 1, "freeze_operations": 1, "matching_thaw_operations": 1,
        "wall_seconds": WALL_SECONDS, "cleanup_reserve_seconds": CLEANUP_SECONDS,
        "backing_bytes": BACKING_BYTES, "storage_cap_bytes": STORAGE_BYTES,
        "storage": "backing plus measured contents plus all original other charges; no overlap deduction",
        "record_bytes": RECORD_BYTES, "continuous_logical_bound": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(encoded(source_policy()).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
