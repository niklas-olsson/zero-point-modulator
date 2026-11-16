"""Injected ownership/effect fixtures only; no image, mount, ioctl or process probe."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import unittest

from tests.contracts import check_alpine_sealed_candidate as candidate
from tests.contracts import private_ext4_observer as observer


def identity():
    return observer.OwnedFilesystem(
        owner="a" * 32, uid=1000,
        backing=observer.Backing(10, 101, 201, 1000, 0o100600, 1, observer.BACKING_BYTES),
        loop=observer.Loop(11, 102, 202, 701, 0o60660, 101, 201, 0, 0, False, False),
        mount=observer.Mount(12, 103, 2, 1000, 0o40700, 301, 104, 401, 701, "ext4", "b" * 32),
        control=observer.Control(13, 101, 203, 1000, 0o40700),
        daemon_namespace_dev=104, daemon_namespace_ino=401,
        work_bind_dev=103, work_bind_ino=2, unrelated_consumers=0, raw_writers=0)


class Fixture:
    def __init__(self):
        self.now = 100.0
        self.identity = identity()
        self.observed = self.identity
        self.effects = []
        self.checkpoints = []
        self.owner = observer.FrozenCensus(self.identity, started=self.now, clock=lambda: self.now)

    def observe(self):
        return self.observed

    def checkpoint(self, record):
        self.checkpoints.append(record)

    def dispatch(self, request):
        self.effects.append(request)

    def freeze(self, **changes):
        return self.owner.start_freeze(**{"observe": self.observe, "checkpoint": self.checkpoint,
                                         "dispatch": self.dispatch, **changes})

    def frozen(self):
        request = self.freeze()
        self.owner.complete_freeze(request, success=True, observe=self.observe)
        return request

    def census(self, contents=10, other=20):
        self.owner.census(observe=self.observe, collect=lambda fd, end: (contents, other))

    def thaw(self, **changes):
        return self.owner.start_thaw(**{"observe": self.observe, "checkpoint": self.checkpoint,
                                       "dispatch": self.dispatch, **changes})

    def released(self):
        request = self.thaw()
        self.owner.complete_thaw(request, success=True, observe=self.observe)
        return request


class PrivateExt4ObserverTests(unittest.TestCase):
    def refusal(self, code, function, *args, **kwargs):
        with self.assertRaises(observer.Refused) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.args, (code,))

    def test_default_cli_is_data_only_and_has_no_activation_option(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(observer.main([]), 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["status"], "SOURCE_ONLY_NOT_RUN")
        self.assertEqual(record["privileged_backend"], "NOT_IMPLEMENTED")
        self.assertFalse(record["native_harness_integration"])
        with redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            observer.main(["--run"])
        self.assertEqual(caught.exception.code, 2)

    def test_original_caps_remain_separate_from_new_backing_charge(self):
        self.assertEqual(observer.STORAGE_BYTES, candidate.CAPS["owned_storage_bytes"])
        self.assertEqual(observer.WALL_SECONDS, candidate.CAPS["wall_seconds"])
        self.assertEqual(observer.CLEANUP_SECONDS, candidate.CAPS["cleanup_reserve_seconds"])
        self.assertEqual(observer.BACKING_BYTES, 1024**3)

    def test_original_deadlines_and_first_failure_have_no_public_setter(self):
        f = Fixture()
        for key, value in (("started", 200), ("work_end", 99999), ("end", 99999),
                           ("first_failure", None), ("identity", identity())):
            with self.subTest(key=key), self.assertRaises(AttributeError):
                setattr(f.owner, key, value)

    def test_complete_original_cycle_counts_backing_and_contents_separately(self):
        f = Fixture()
        f.frozen()
        f.census(123, 456)
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")
        f.released()
        record = f.owner.snapshot()
        self.assertEqual(record["logical_total_bytes"], observer.BACKING_BYTES + 123 + 456)
        self.assertEqual(record["sample"], "DECLARED_CYCLE_COMPLETE")
        self.assertEqual(record["declared_barrier_release"], "ACKNOWLEDGED")
        self.assertEqual(record["filesystem_retirement"], "NOT_MEASURED")
        self.assertFalse(record["continuous_logical_bound"])
        self.assertFalse(record["native_execution_qualified"])
        self.assertEqual([r.operation for r in f.effects], ["FREEZE", "THAW"])
        self.assertEqual([r.root_fd for r in f.effects], [12, 12])
        self.assertEqual(f.checkpoints[0]["freeze"]["state"], "PREPARED")
        self.assertEqual(f.checkpoints[1]["thaw"]["state"], "PREPARED")

    def test_successful_cycle_has_finite_callback_counts(self):
        f = Fixture()
        counts = dict(clock=0, observe=0, checkpoint=0, dispatch=0, collect=0)

        def call(name, result=None):
            def callback(*args):
                counts[name] += 1
                return result
            return callback

        owner = observer.FrozenCensus(f.identity, started=100, clock=call("clock", 100))
        ports = dict(observe=call("observe", f.identity), checkpoint=call("checkpoint"), dispatch=call("dispatch"))
        request = owner.start_freeze(**ports)
        owner.complete_freeze(request, success=True, observe=ports["observe"])
        owner.census(observe=ports["observe"], collect=call("collect", (10, 20)))
        request = owner.start_thaw(**ports)
        owner.complete_thaw(request, success=True, observe=ports["observe"])
        self.assertEqual(counts, dict(clock=16, observe=6, checkpoint=2, dispatch=2, collect=1))

    def test_pending_regular_write_cannot_be_censused_before_original_ack(self):
        f = Fixture()
        request = f.freeze()
        self.refusal("CENSUS_NOT_ADMITTED", f.census)
        self.assertEqual(f.owner.snapshot()["freeze"]["state"], "PENDING")
        f.owner.complete_freeze(request, success=True, observe=f.observe)
        f.released()
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")
        self.assertEqual(f.owner.snapshot()["first_failure"], "CENSUS_NOT_ADMITTED")

    def test_injected_backend_drain_precedes_frozen_read_and_thaw_precedes_writes(self):
        # This is a causal-port fixture, not evidence about a real kernel.
        f = Fixture()
        files, writes = {"old": 5}, []
        request = f.freeze()
        files["old"] = 8  # Original pending write completes before acknowledgement.
        f.owner.complete_freeze(request, success=True, observe=f.observe)
        writes.append(("new", 9))  # Backend holds this mutation while frozen.

        def collect(fd, end):
            self.assertEqual(fd, 12)
            self.assertEqual(end, f.owner.work_end)
            self.assertEqual(files, {"old": 8})
            return sum(files.values()), 20

        f.owner.census(observe=f.observe, collect=collect)
        request = f.thaw()
        self.assertEqual(files, {"old": 8})
        f.owner.complete_thaw(request, success=True, observe=f.observe)
        files.update(writes)
        self.assertEqual(f.owner.snapshot()["contents_bytes"], 8)
        self.assertEqual(files, {"old": 8, "new": 9})

    def test_wrong_backing_loop_mount_namespace_or_unrelated_writer_refuses(self):
        original = identity()
        variants = [
            replace(original, backing=replace(original.backing, size=observer.BACKING_BYTES - 1)),
            replace(original, backing=replace(original.backing, mode=0o100644)),
            replace(original, backing=replace(original.backing, links=2)),
            replace(original, backing=replace(original.backing, mode=0o60600)),
            replace(original, loop=replace(original.loop, backing_ino=999)),
            replace(original, loop=replace(original.loop, offset=1)),
            replace(original, loop=replace(original.loop, size_limit=1)),
            replace(original, loop=replace(original.loop, autoclear=True)),
            replace(original, loop=replace(original.loop, read_only=True)),
            replace(original, loop=replace(original.loop, mode=0o100600)),
            replace(original, mount=replace(original.mount, fs_type="ext2")),
            replace(original, mount=replace(original.mount, source_rdev=999)),
            replace(original, mount=replace(original.mount, mode=0o40755)),
            replace(original, mount=replace(original.mount, uid=0)),
            replace(original, daemon_namespace_ino=999),
            replace(original, work_bind_ino=999),
            replace(original, unrelated_consumers=1),
            replace(original, raw_writers=1),
            replace(original, control=replace(original.control, dev=original.mount.dev)),
            replace(original, control=replace(original.control, fd=original.backing.fd)),
        ]
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(observer.Refused):
                observer.FrozenCensus(changed, started=100, clock=lambda: 100)

    def test_boolean_integer_alias_and_nonfinite_clock_are_refused(self):
        value = identity()
        with self.assertRaises(observer.Refused):
            observer.identity_key(replace(value, uid=True))
        for started in (True, float("nan"), float("inf"), -1, 10**1000):
            with self.subTest(type=type(started)), self.assertRaises(observer.Refused):
                observer.FrozenCensus(value, started=started, clock=lambda: 100)

    def test_identity_change_before_effect_never_controls_replacement(self):
        for field in ("backing", "loop", "mount", "control"):
            f = Fixture()
            row = getattr(f.identity, field)
            f.observed = replace(f.identity, **{field: replace(row, fd=row.fd + 20)})
            self.refusal("OWNERSHIP_CHANGED", f.freeze)
            self.assertEqual(f.effects, [])
            self.assertTrue(f.owner.snapshot()["ownership_lost"])

    def test_changed_identity_during_checkpoint_refuses_before_effect(self):
        f = Fixture()

        def checkpoint(record):
            f.checkpoints.append(record)
            f.observed = replace(f.identity, mount=replace(f.identity.mount, mount_id=999))

        self.refusal("OWNERSHIP_CHANGED", f.freeze, checkpoint=checkpoint)
        self.assertEqual(f.effects, [])

    def test_old_declaration_mutation_is_not_adopted(self):
        f = Fixture()
        object.__setattr__(f.identity.mount, "mount_id", 999)
        self.refusal("OWNERSHIP_CHANGED", f.freeze)
        self.assertEqual(f.effects, [])

    def test_late_identity_or_checkpoint_never_dispatches(self):
        for which in ("observe", "checkpoint"):
            f = Fixture()

            def delayed(*args):
                f.now = f.owner.work_end
                return f.identity

            self.refusal("ORIGINAL_DEADLINE", f.freeze, **{which: delayed})
            self.assertEqual(f.effects, [])
            self.assertEqual(f.owner.snapshot()["freeze"]["state"], "NOT_ISSUED")

    def test_expired_before_start_has_no_callback_or_effect(self):
        f = Fixture()
        f.now = f.owner.work_end
        self.refusal("ORIGINAL_DEADLINE", f.freeze)
        self.assertEqual(f.effects, [])
        self.assertEqual(f.checkpoints, [])

    def test_missing_ack_never_issues_speculative_thaw_or_second_freeze(self):
        f = Fixture()
        f.freeze()
        self.refusal("THAW_WITHOUT_RESOLVED_FREEZE", f.thaw)
        self.refusal("FREEZE_REPLAY", f.freeze)
        self.assertEqual(len(f.effects), 1)
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")

    def test_equal_copied_ticket_and_other_owner_ack_are_refused(self):
        for foreign in (False, True):
            f = Fixture()
            request = f.freeze()
            wrong = Fixture().freeze() if foreign else replace(request)
            self.assertEqual(request, wrong)  # Equal data grants no authority.
            self.refusal("UNOWNED_COMPLETION", f.owner.complete_freeze, wrong,
                         success=True, observe=f.observe)
            self.assertEqual(f.owner.snapshot()["freeze"]["state"], "PENDING")

    def test_original_ticket_field_mutation_refuses_completion_and_projection(self):
        for key, value in (("root_fd", 999), ("deadline", 99999), ("operation", "THAW"),
                           ("identity_sha256", "f" * 64)):
            f = Fixture()
            request = f.freeze()
            object.__setattr__(request, key, value)
            self.refusal("REQUEST_CHANGED", f.owner.complete_freeze, request,
                         success=True, observe=f.observe)
            self.refusal("REQUEST_CHANGED", f.owner.snapshot)
            self.assertEqual(len(f.effects), 1)

    def test_duplicate_or_malformed_ack_cannot_replay(self):
        f = Fixture()
        request = f.freeze()
        self.refusal("COMPLETION_TYPE", f.owner.complete_freeze, request,
                     success=1, observe=f.observe)
        f.owner.complete_freeze(request, success=True, observe=f.observe)
        self.refusal("UNOWNED_COMPLETION", f.owner.complete_freeze, request,
                     success=True, observe=f.observe)
        f.released()
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_refused_freeze_keeps_unknown_and_never_thaws(self):
        f = Fixture()
        request = f.freeze()
        self.refusal("FREEZE_REFUSED", f.owner.complete_freeze, request,
                     success=False, observe=f.observe)
        self.refusal("THAW_WITHOUT_RESOLVED_FREEZE", f.thaw)
        self.assertEqual(len(f.effects), 1)
        self.assertEqual(f.owner.snapshot()["first_failure"], "FREEZE_REFUSED")

    def test_dispatch_error_after_effect_keeps_original_pending_ticket(self):
        f = Fixture()

        def ambiguous(request):
            f.effects.append(request)
            raise OSError("PRIVATE-ERROR-NOT-EXPORTED")

        self.refusal("FREEZE_DISPATCH_UNKNOWN", f.freeze, dispatch=ambiguous)
        record = f.owner.snapshot()
        self.assertEqual(record["freeze"]["state"], "PENDING")
        self.assertNotIn("PRIVATE", json.dumps(record))
        f.owner.complete_freeze(f.effects[0], success=True, observe=f.observe)
        f.released()
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_checkpoint_error_has_no_effect_or_retry(self):
        f = Fixture()

        def failed(record):
            raise OSError("PRIVATE")

        self.refusal("CHECKPOINT_UNCONFIRMED", f.freeze, checkpoint=failed)
        self.assertEqual(f.effects, [])
        self.refusal("FREEZE_REPLAY", f.freeze)

    def test_late_original_freeze_may_only_release_within_original_cleanup_clock(self):
        f = Fixture()
        request = f.freeze()
        f.now = f.owner.work_end + 1
        self.refusal("ORIGINAL_DEADLINE", f.owner.complete_freeze, request,
                     success=True, observe=f.observe)
        self.refusal("CENSUS_NOT_ADMITTED", f.census)
        f.released()
        record = f.owner.snapshot()
        self.assertEqual(record["first_failure"], "ORIGINAL_DEADLINE")
        self.assertEqual(record["declared_barrier_release"], "ACKNOWLEDGED")
        self.assertEqual(record["census"], "NOT_RUN")
        self.assertEqual(record["sample"], "UNQUALIFIED")

    def test_dispatch_returns_late_without_ack_retains_pending_not_cancellation(self):
        f = Fixture()

        def delayed(request):
            f.effects.append(request)
            f.now = f.owner.work_end + 1

        self.refusal("ORIGINAL_DEADLINE", f.freeze, dispatch=delayed)
        self.assertEqual(f.owner.snapshot()["freeze"]["state"], "PENDING")
        self.refusal("THAW_WITHOUT_RESOLVED_FREEZE", f.thaw)
        self.refusal("ORIGINAL_DEADLINE", f.owner.complete_freeze, f.effects[0],
                     success=True, observe=f.observe)
        f.released()
        self.assertEqual(len(f.effects), 2)

    def test_late_original_ack_after_total_deadline_never_starts_thaw(self):
        f = Fixture()
        request = f.freeze()
        f.now = f.owner.end + 1
        self.refusal("ORIGINAL_DEADLINE", f.owner.complete_freeze, request,
                     success=True, observe=f.observe)
        self.refusal("ORIGINAL_DEADLINE", f.thaw)
        self.assertEqual(len(f.effects), 1)
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")

    def test_late_completion_retains_ack_without_late_identity_callback(self):
        for operation in ("FREEZE", "THAW"):
            f = Fixture()
            if operation == "THAW":
                f.frozen()
                request = f.thaw()
                finish = f.owner.complete_thaw
                f.now = f.owner.end
            else:
                request = f.freeze()
                finish = f.owner.complete_freeze
                f.now = f.owner.work_end
            reads = []
            self.refusal("ORIGINAL_DEADLINE", finish, request, success=True,
                         observe=lambda: reads.append("late") or f.identity)
            self.assertEqual(reads, [])
            self.assertEqual(f.owner.snapshot()[operation.lower()]["state"], "ACK")
            self.assertFalse(f.owner.snapshot()[operation.lower()]["timely_ack"])

    def test_census_failure_or_incomplete_type_can_release_without_qualifying(self):
        for failure in ("raise", "missing", "boolean", "negative"):
            f = Fixture()
            f.frozen()

            def collect(fd, end):
                if failure == "raise":
                    raise FileNotFoundError("PRIVATE")
                return {"missing": None, "boolean": (True, 0), "negative": (-1, 0)}[failure]

            code = "CENSUS_UNAVAILABLE" if failure == "raise" else "CENSUS_TYPE"
            self.refusal(code, f.owner.census, observe=f.observe, collect=collect)
            f.released()
            self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")
            self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_sparse_logical_overflow_retains_total_without_overlap_subtraction(self):
        f = Fixture()
        f.frozen()
        contents = 3 * 1024**3
        self.refusal("LOGICAL_STORAGE_BOUND", f.census, contents, 1)
        f.released()
        record = f.owner.snapshot()
        self.assertEqual(record["logical_total_bytes"], observer.STORAGE_BYTES + 1)
        self.assertEqual(record["first_failure"], "LOGICAL_STORAGE_BOUND")
        self.assertEqual(record["sample"], "UNQUALIFIED")

    def test_late_census_read_retains_observation_but_never_qualifies(self):
        f = Fixture()
        f.frozen()

        def collect(fd, end):
            f.now = end
            return 123, 456

        self.refusal("ORIGINAL_DEADLINE", f.owner.census, observe=f.observe, collect=collect)
        f.released()
        self.assertEqual(f.owner.snapshot()["contents_bytes"], 123)
        self.assertEqual(f.owner.snapshot()["census"], "OBSERVED")
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_census_and_thaw_revalidate_identity_after_original_pending_effect(self):
        for at in ("freeze_ack", "census_return", "thaw_start", "thaw_ack"):
            f = Fixture()
            changed = replace(f.identity, mount=replace(f.identity.mount, mount_id=999))
            if at == "freeze_ack":
                request = f.freeze()
                f.observed = changed
                self.refusal("OWNERSHIP_CHANGED", f.owner.complete_freeze, request,
                             success=True, observe=f.observe)
            else:
                f.frozen()
                if at == "census_return":
                    def collect(fd, end):
                        f.observed = changed
                        return 0, 0
                    self.refusal("OWNERSHIP_CHANGED", f.owner.census, observe=f.observe, collect=collect)
                elif at == "thaw_start":
                    f.observed = changed
                    self.refusal("OWNERSHIP_CHANGED", f.thaw)
                else:
                    request = f.thaw()
                    f.observed = changed
                    self.refusal("OWNERSHIP_CHANGED", f.owner.complete_thaw, request,
                                 success=True, observe=f.observe)
            f.observed = f.identity
            if at in ("freeze_ack", "census_return"):
                self.refusal("OWNERSHIP_LOST", f.thaw)
            self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")

    def test_missing_failed_or_late_thaw_never_confirms_release(self):
        for outcome in ("missing", "failed", "late"):
            f = Fixture()
            f.frozen()
            f.census()
            request = f.thaw()
            if outcome == "failed":
                self.refusal("THAW_REFUSED", f.owner.complete_thaw, request,
                             success=False, observe=f.observe)
            elif outcome == "late":
                f.now = f.owner.end
                self.refusal("ORIGINAL_DEADLINE", f.owner.complete_thaw, request,
                             success=True, observe=f.observe)
            self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")
            self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")
            self.assertEqual(len(f.effects), 2)
            self.refusal("THAW_WITHOUT_RESOLVED_FREEZE", f.thaw)

    def test_late_thaw_identity_read_never_dispatches(self):
        f = Fixture()
        f.frozen()

        def late():
            f.now = f.owner.end
            return f.identity

        self.refusal("ORIGINAL_DEADLINE", f.thaw, observe=late)
        self.assertEqual(len(f.effects), 1)

    def test_reentrant_control_cannot_start_another_effect(self):
        f = Fixture()

        def nested(request):
            f.effects.append(request)
            f.thaw()

        self.refusal("FREEZE_DISPATCH_UNKNOWN", f.freeze, dispatch=nested)
        self.assertEqual(f.owner.snapshot()["first_failure"], "REENTRANT_CONTROL")
        self.assertEqual(len(f.effects), 1)

    def caught_reentry(self, function):
        self.refusal("REENTRANT_CONTROL", function)

    def test_caught_reentrancy_in_freeze_preflight_never_dispatches(self):
        for boundary in ("clock", "checkpoint", "observe"):
            f = Fixture()

            def caught(*args):
                self.caught_reentry(f.freeze)
                return f.identity

            if boundary == "clock":
                def clock():
                    caught()
                    return f.now
                f.owner._clock = clock
                changes = {}
            else:
                changes = {boundary: caught}
            self.refusal("REENTRANT_CONTROL", f.freeze, **changes)
            self.assertEqual(f.effects, [])
            self.assertEqual(f.owner.first_failure, "REENTRANT_CONTROL")
            self.assertTrue(f.owner.snapshot()["control_misuse"])
            self.refusal("FREEZE_REPLAY", f.freeze)
            self.refusal("CENSUS_NOT_ADMITTED", f.census)

    def test_caught_reentrancy_before_collect_blocks_read_and_allows_matching_cleanup(self):
        for boundary in ("clock", "observe"):
            f = Fixture()
            f.frozen()
            attempted, collected = [], []

            def caught(*args):
                if not attempted:
                    attempted.append(True)
                    self.caught_reentry(f.census)
                return f.identity

            if boundary == "clock":
                def clock():
                    caught()
                    return f.now
                f.owner._clock = clock
                observe = f.observe
            else:
                observe = caught
            self.refusal("REENTRANT_CONTROL", f.owner.census, observe=observe,
                         collect=lambda fd, end: collected.append(fd) or (0, 0))
            self.assertEqual(collected, [])
            f.released()
            self.assertEqual([r.operation for r in f.effects], ["FREEZE", "THAW"])
            self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")
            self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")
            self.assertEqual(f.owner.first_failure, "REENTRANT_CONTROL")

    def test_caught_reentrancy_in_collect_retains_observation_and_matching_cleanup(self):
        f = Fixture()
        f.frozen()

        def collect(fd, end):
            self.caught_reentry(f.thaw)
            return 12, 34

        self.refusal("REENTRANT_CONTROL", f.owner.census, observe=f.observe, collect=collect)
        self.assertEqual(f.owner.snapshot()["census"], "OBSERVED")
        self.assertEqual(f.owner.snapshot()["contents_bytes"], 12)
        self.assertEqual(len(f.effects), 1)
        f.released()
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_caught_reentrancy_after_dispatch_keeps_pending_original_for_cleanup(self):
        f = Fixture()

        def caught(request):
            f.effects.append(request)
            self.caught_reentry(f.freeze)

        self.refusal("REENTRANT_CONTROL", f.freeze, dispatch=caught)
        self.assertEqual(f.owner.snapshot()["freeze"]["state"], "PENDING")
        f.owner.complete_freeze(f.effects[0], success=True, observe=f.observe)
        f.released()
        self.assertEqual(f.owner.first_failure, "REENTRANT_CONTROL")
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")

    def test_caught_reentrancy_during_ack_blocks_census_but_allows_matching_cleanup(self):
        f = Fixture()
        request = f.freeze()

        def caught():
            self.caught_reentry(f.census)
            return f.identity

        self.refusal("REENTRANT_CONTROL", f.owner.complete_freeze, request, success=True, observe=caught)
        self.assertFalse(f.owner.snapshot()["freeze"]["timely_ack"])
        self.refusal("CENSUS_NOT_ADMITTED", f.census)
        f.released()
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")

    def test_clock_mutation_after_identity_read_never_passes_changed_fd_to_collect(self):
        f = Fixture()
        request = f.frozen()
        calls, collected = [], []

        def clock():
            calls.append(True)
            if len(calls) == 2:
                object.__setattr__(f.identity.mount, "fd", 999)
            return f.now

        f.owner._clock = clock
        self.refusal("OWNERSHIP_CHANGED", f.owner.census, observe=f.observe,
                     collect=lambda fd, end: collected.append(fd) or (0, 0))
        self.assertEqual(collected, [])
        self.assertEqual(request.root_fd, 12)
        self.assertEqual(f.owner._root_fd, 12)
        self.assertTrue(f.owner.snapshot()["ownership_lost"])

    def test_mutated_accepted_freeze_ticket_never_admits_census(self):
        for boundary in ("before", "last_clock"):
            f = Fixture()
            request = f.frozen()
            calls, collected = [], []
            if boundary == "before":
                object.__setattr__(request, "root_fd", 999)
            else:
                def clock():
                    calls.append(True)
                    if len(calls) == 2:
                        object.__setattr__(request, "root_fd", 999)
                    return f.now
                f.owner._clock = clock
            self.refusal("REQUEST_CHANGED", f.owner.census, observe=f.observe,
                         collect=lambda fd, end: collected.append(fd) or (0, 0))
            self.assertEqual(collected, [])
            self.refusal("REQUEST_CHANGED", f.owner.snapshot)
            self.assertEqual(len(f.effects), 1)

    def test_caught_projection_refusal_and_restored_ticket_stop_outer_census(self):
        f = Fixture()
        request = f.frozen()
        calls, collected = [], []

        def clock():
            calls.append(True)
            if len(calls) == 2:
                object.__setattr__(request, "root_fd", 999)
                self.refusal("REQUEST_CHANGED", f.owner.snapshot)
                object.__setattr__(request, "root_fd", 12)
            return f.now

        f.owner._clock = clock
        self.refusal("REQUEST_CHANGED", f.owner.census, observe=f.observe,
                     collect=lambda fd, end: collected.append(fd) or (0, 0))
        self.assertEqual(collected, [])
        self.assertEqual(f.owner.snapshot()["census"], "NOT_RUN")
        self.assertEqual(f.owner.first_failure, "REQUEST_CHANGED")
        f.released()
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")
        self.assertEqual(f.owner.snapshot()["sample"], "UNQUALIFIED")

    def test_prior_projection_failure_allows_separate_original_matching_cleanup(self):
        f = Fixture()
        request = f.frozen()
        object.__setattr__(request, "root_fd", 999)
        self.refusal("REQUEST_CHANGED", f.owner.snapshot)
        object.__setattr__(request, "root_fd", 12)
        f.released()
        self.assertEqual([r.operation for r in f.effects], ["FREEZE", "THAW"])
        self.assertEqual(f.owner.first_failure, "REQUEST_CHANGED")
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "ACKNOWLEDGED")

    def test_caught_new_refusal_during_cleanup_is_not_hidden_by_older_failure(self):
        f = Fixture()
        request = f.frozen()
        self.refusal("LOGICAL_STORAGE_BOUND", f.census, observer.STORAGE_BYTES, 0)

        def checkpoint(record):
            object.__setattr__(request, "root_fd", 999)
            self.refusal("REQUEST_CHANGED", f.owner.snapshot)
            object.__setattr__(request, "root_fd", 12)

        self.refusal("REQUEST_CHANGED", f.thaw, checkpoint=checkpoint)
        self.assertEqual(len(f.effects), 1)
        self.assertEqual(f.owner.first_failure, "LOGICAL_STORAGE_BOUND")
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")

    def test_clock_failure_or_regression_never_creates_a_fresh_window(self):
        for bad in (99.0, float("nan"), float("inf"), True, 10**1000):
            f = Fixture()
            f.now = bad
            self.refusal("CLOCK_INVALID", f.freeze)
            self.assertEqual(f.effects, [])
        f = Fixture()
        def failed():
            raise RuntimeError("PRIVATE")
        f.owner = observer.FrozenCensus(f.identity, started=100, clock=failed)
        self.refusal("CLOCK_UNAVAILABLE", f.freeze)

    def test_clock_loss_cannot_be_repaired_to_authorize_a_later_effect(self):
        f = Fixture()
        request = f.freeze()
        f.now = 99
        self.refusal("CLOCK_INVALID", f.owner.complete_freeze, request,
                     success=True, observe=f.observe)
        f.now = 101
        self.refusal("CLOCK_UNTRUSTED", f.thaw)
        self.assertEqual(len(f.effects), 1)
        self.assertTrue(f.owner.snapshot()["clock_lost"])
        self.assertEqual(f.owner.snapshot()["declared_barrier_release"], "UNKNOWN")

    def test_exported_journal_copy_does_not_grant_completion_or_mutate_owner(self):
        f = Fixture()
        f.freeze()
        record = f.owner.snapshot()
        record["freeze"]["state"] = "ACK"
        record["identity"]["mount"]["fd"] = 999
        self.assertEqual(f.owner.snapshot()["freeze"]["state"], "PENDING")
        self.assertEqual(f.owner.snapshot()["identity"]["mount"]["fd"], 12)
        self.refusal("THAW_WITHOUT_RESOLVED_FREEZE", f.thaw)

    def test_maximum_width_declared_identity_and_overflow_record_remain_bounded(self):
        n = observer.UINT64
        value = observer.OwnedFilesystem(
            "f" * 32, n, observer.Backing(n, n, n, n, 0o100600, 1, observer.BACKING_BYTES),
            observer.Loop(n - 1, n, n, n, 0o60660, n, n, 0, 0, False, False),
            observer.Mount(n - 2, n - 1, n, n, 0o40700, n, n, n, n, "ext4", "f" * 32),
            observer.Control(n - 3, n, n, n, 0o40700), n, n, n - 1, n, 0, 0)
        f = Fixture()
        f.identity = f.observed = value
        f.owner = observer.FrozenCensus(value, started=2**52, clock=lambda: 2**52)
        f.frozen()
        self.refusal("LOGICAL_STORAGE_BOUND", f.census, n, n)
        f.released()
        raw = observer.encoded(f.owner.snapshot())
        self.assertLessEqual(len(raw), observer.RECORD_BYTES)
        self.assertEqual(f.owner.snapshot()["logical_total_bytes"], observer.BACKING_BYTES + 2 * n)

        def envelope(value):
            # All admitted scalar strings have <=64 ASCII characters; all
            # integers/sums and finite clock representations use <32 bytes.
            # Null counter/failure slots are bounded by the longer string case.
            if type(value) is dict:
                return {key: envelope(item) for key, item in value.items()}
            if type(value) is bool:
                return False
            if type(value) in (int, float):
                return 10**31
            return "X" * 64

        upper = observer.encoded(envelope(f.owner.snapshot()))
        self.assertLessEqual(len(raw), len(upper))
        self.assertLessEqual(len(upper), observer.RECORD_BYTES)


if __name__ == "__main__":
    unittest.main()
