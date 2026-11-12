# Qualified cold-session measurements and initial performance targets

## Usable outcome and scope

The installed `cold-clarification-v1` fallback completes two private instruction
rounds in one logical OpenJules session. Each round uses a separate actual
canonical child grant, UID, execution and checkout. The first child asks for
clarification; the later instruction starts a fresh child, retains the
predecessor correlation and completes verified work. Cleanup, service restart
and original-key replay preserve the original outcomes and captures without
additional inference. Warm process or adapter-session reuse is unsupported.

This record publishes existing native qualification measurements and chooses
initial engineering targets for that exact controlled scene. It changes no
runtime, fixture, integration pin, deadline, token allowance or CI acceptance
threshold. The measured reconciliation interval is operator target-to-capacity
convergence. A complete controller tick is not separately timed.

The three observations below are separate head, relevant-base and actual-main
qualification runs of the same ZPM tree. They are not a statistical benchmark,
a latency percentile or a service-level promise. The model replies are
deterministic localhost fixtures; the installed services, Docker children,
canonical grants, filesystem operations and verification are real.
[The delivery record](https://github.com/niklas-olsson/zero-point-modulator/pull/34#issuecomment-6815205555)
records the complete source, native and installed qualification boundaries.

## Exact measured revisions and evidence

Every row uses OpenJules
`74429feda1f3991ce528678b74d6db5e3b3b250c` and Polyphony
`0cf8b4772b04269505e3e1cd41324e41409b80f9`.
The three ZPM revisions share tree
`79c9688e42d5389e2796db3f470488ce2363a0c5`.

| Observation | ZPM revision | Native run and child job | Original full cold report |
| --- | --- | --- | --- |
| Head | `94168ebb4d12882826ef443e72b2d448aa547d7b` | [43156522448 / 130977926531](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43156522448/job/130977926531) | [1a09429b](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/1a09429b5cdcb0dbe7c3b98457fc3fd4bdb9de2b) |
| Relevant-base merge candidate | `f01c243896edd8f55fca1ff1c5cddf82243b38bf` | [43156428796 / 130977669822](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43156428796/job/130977669822) | [37beb8b3](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/37beb8b3d191520b3101cb302aba1abaefb997a4) |
| Actual main | `3abded40fa9d3bcbdd8840bcb3eed2ac7d0c8f74` | [43157860591 / 130982172352](https://github.com/niklas-olsson/zero-point-modulator/actions/runs/43157860591/job/130982172352) | [afa5bdff](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/afa5bdff4da14140bdc211d3af6e3d39a30a2eb6) |

[Premerge report index](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/7aa4bf6285d81c1be2bc18f63bb6095e22e3c641)
and [actual-main report index](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/3004f4c99a79eb20b1c0a39429e39a27930a329c)
retain the other installed scenes. The
[root actual-main audit](https://api.github.com/repos/niklas-olsson/zero-point-modulator/git/blobs/0f96027c4a1f9df7bcc1ab2bc010da77130fd572)
independently reconciles the reports and all 460 storage differences for that
run. Later component-only OpenJules changes are not substituted into these
measured revisions.

## Startup, reconciliation and two cold rounds

All durations are seconds, derived from the original monotonic nanosecond
fields and rounded only for this table.

| Observed interval | Head | Merge candidate | Actual main |
| --- | ---: | ---: | ---: |
| Bundle preparation | 0.037200 | 0.038556 | 0.032879 |
| Offline integrity verification | 0.023037 | 0.024133 | 0.019922 |
| Dependency/bootstrap command | 44.858514 | 53.542584 | 39.106552 |
| Initial manager start to readiness | 1.004892 | 1.029270 | 0.893764 |
| Target 0 to 1 convergence | 5.770959 | 5.274125 | 8.028659 |
| Restart manager start to readiness | 0.972154 | 1.017138 | 0.863096 |
| Enrollment request to first root model request | 0.745532 | 1.288763 | 0.943153 |
| Start-session tool-call prepared to first child request | 1.363187 | 1.466266 | 1.107332 |
| Follow-up tool-call prepared to first successor request | 1.548629 | 1.517971 | 1.403167 |

Bootstrap includes the existing installation command's dependency/setup work.
Manager readiness includes polling and service startup. Target convergence
includes the target edit, controller polling, provider/runtime work and the
existing capacity predicate. It is an end-to-end reconciliation observation,
not the duration of one HTTP request or a whole controller pass.

The two child intervals begin when the deterministic provider fixture has
prepared the `start_child_session` or `continue_child_session` tool-call
arguments for its next model response, before that response is delivered or
host/canonical handling occurs. The reports explicitly retain
`canonical_session=false`: the logical session is OpenJules-owned. These
intervals include provider-response delivery, host scheduling, canonical grant
handling, execution startup and the first broker request. They are useful
cold-delegation latency observations, not isolated process-spawn measurements.
The reports bind each interval to the actual child ID, grant digest and
instruction digest.

Both rounds retain their original 90-second allocations inside the original
180+90+90=360 task allocation. The predecessor's already armed watchdog also
clips the successor. Parallel elapsed time is not summed into allocation time.
The performance targets below cannot extend these permissions.

## Context size and model cost boundary

All three reports have the same ten logical request sizes. The byte projection
uses UTF-8 JSON with sorted keys, compact separators and `ensure_ascii=false`.
It is not wire traffic, a tokenizer count, cache accounting or provider billing.

| Actor | Request ordinal | Messages | Message bytes | Tool bytes | Complete logical request bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| root | 1 | 2 | 1,205 | 5,299 | 6,632 |
| root | 2 | 4 | 3,250 | 5,299 | 8,677 |
| left | 1 | 2 | 1,503 | 1,844 | 3,475 |
| left | 2 | 4 | 1,827 | 1,844 | 3,799 |
| root | 3 | 6 | 8,256 | 5,299 | 13,683 |
| root | 4 | 8 | 11,228 | 5,299 | 16,655 |
| right | 1 | 2 | 2,632 | 1,844 | 4,604 |
| right | 2 | 4 | 2,941 | 1,844 | 4,913 |
| root | 5 | 10 | 17,888 | 5,299 | 23,315 |
| root | 6 | 12 | 20,185 | 5,299 | 25,612 |

The root sends 94,574 logical request bytes across six calls; the first child
sends 7,274 across two calls; the successor sends 9,517 across two calls.
The total is 111,365 bytes across ten calls. Repeated context contributes to
each request's count. The successor's context carries the original instruction,
follow-up and retained predecessor information through the existing cold
correlation envelope.

The same durable inference allowance reserves 1,024 output-token units per
call, 10,240 units overall. Reservations are limits, not measured provider usage
or financial cost. Cleanup, complete service restart and retained-key replay
produce zero additional model requests. No idle observation is allowed to
trigger inference.

## Idle overhead: defined process cohort

Each report samples the original manager and its three direct services for
approximately two seconds before task enrollment. Linux PID, start-time,
parent and command identity remain fixed. Clock resolution is 100 ticks per
second and page size is 4,096 bytes in these runs.

| Process | Minimum observed endpoint RSS bytes | Maximum observed endpoint RSS bytes | Self CPU seconds, head / merge / main |
| --- | ---: | ---: | --- |
| manager | 23,396,352 | 23,597,056 | 0.00 / 0.00 / 0.00 |
| polyphony | 41,582,592 | 41,865,216 | 0.00 / 0.00 / 0.00 |
| openjules | 75,227,136 | 79,966,208 | 0.11 / 0.16 / 0.09 |
| zpm | 18,329,600 | 18,595,840 | 0.00 / 0.00 / 0.00 |

The windows are 2.002424591, 2.002281018 and 2.001516421 seconds. The four-process
RSS sum at the end is 163,758,080, 163,753,984 and 161,714,176 bytes. These are
sums of approximate per-process RSS, which can count shared pages more than
once. They exclude descendants, Docker containers, the Docker daemon and
unrelated host/kernel work. They are not peak or total machine memory.

The fixture recorded zero model calls throughout readiness and this idle
window. Every report explicitly sets `completed_reconcile_tick_proven=false`
and `peak_rss_measured=false`. A two-second sample does not prove that the
five-second watch loop completed a full tick during that sample. The separate
target-convergence interval above supplies the reconciliation measurement;
do not describe the idle counters as cost per reconciliation cycle.

## Storage growth and restart

The actual-main report retains six ordered phases over seven journals:
canonical, children, controller, dispatcher, lifecycle, models and runtime.
The following totals add each observed main/WAL/SHM file length arithmetically;
the files are sampled separately.

| Actual-main phase | Sum of observed journal file lengths | Child journal main file | Capture bytes | Capture files |
| --- | ---: | ---: | ---: | ---: |
| prepared | 65,536 | 0 | 0 | 0 |
| ready_target_zero | 262,224 | 36,864 | 0 | 0 |
| idle_before_enrollment | 323,856 | 65,536 | 0 | 0 |
| capture_verified | 475,552 | 102,400 | 226,816 | 1 |
| cleanup_stopped | 606,912 | 102,400 | 229,376 | 2 |
| restart_replayed | 615,152 | 102,400 | 229,376 | 2 |

From ready target zero to completed restart/replay, the child main file grows
from 36,864 to 102,400 bytes, a 65,536-byte increase. The seven-journal observed
sum grows by 352,928 bytes in actual main; the other two runs each grow by
348,808 bytes. Capture files grow from zero to 229,376 bytes in all three runs.
The first verified task capture accounts for 226,816 bytes; the later untouched
replacement-worker capture adds 2,560 bytes.

These are logical file lengths, not allocated disk blocks or an atomic SQLite
snapshot. WAL/SHM files can grow, shrink or disappear with checkpointing and
connections. Final actual-main journal totals differ from head/merge by 4,120
controller-WAL bytes while immutable task evidence remains unchanged.
No disk quota, power-loss proof or whole-host storage bound is inferred.

## Initial engineering targets

These targets apply to future comparable executions of this exact fixed cold
scene. They are engineering choices made after the three observations, not
measurements or statistical confidence bounds. Use twice the largest observed
value as initial headroom, then round upward to the stated unit. For byte
targets, use the next power of two after doubling the observed maximum.
The RSS target uses 32 MiB increments.

| Metric and scope | Largest observation | Review target | Rounding after doubling |
| --- | ---: | ---: | --- |
| Preparation | 0.038556s | 0.10s | 0.05s |
| Offline verify | 0.024133s | 0.05s | 0.05s |
| Bootstrap | 53.542584s | 120s | 20s |
| Initial manager readiness | 1.029270s | 3s | 1s |
| Restart manager readiness | 1.017138s | 3s | 1s |
| Target 0 to 1 convergence | 8.028659s | 20s | 5s |
| Enrollment to first root request | 1.288763s | 3s | 1s |
| Either cold tool-call-prepared to first child request | 1.548629s | 4s | 1s |
| Four sampled processes' self CPU in the fixed two-second idle window | 0.16 CPU-seconds | 0.4 CPU-seconds | 0.1 CPU-seconds |
| Sum of the four endpoint RSS observations | 163,758,080 bytes | 320 MiB | 32 MiB |
| Largest root logical request | 25,612 bytes | 64 KiB | Power of two |
| Largest child logical request | 4,913 bytes | 16 KiB | Power of two |
| Sum of the ten logical requests | 111,365 bytes | 256 KiB | Power of two |
| Final child-journal main length | 102,400 bytes | 256 KiB | Power of two |
| Final observed seven-journal file-length sum | 615,152 bytes | 2 MiB | Power of two |
| Final capture file lengths | 229,376 bytes | 512 KiB | Power of two |

The exact functional cost targets remain zero idle/replay inference and ten
fixture calls for this scene. They are existing assertions, without headroom.

A future qualifying report should compare each applicable value to these
targets and explain any excess before treating it as a comparable baseline.
First check component revisions, fixture inputs, runner conditions and the
measurement's stated scope. Retain the original observation and investigate;
do not discard it, rerun until it passes, weaken an authority/lifetime limit or
silently increase a target. A revised target needs new measured evidence and
review.

These targets are recorded for engineering review; they are not new automatic
CI failure thresholds or production admission limits. The existing timeout and
cleanup gates remain in force. New public-discussion scenes, larger repositories
and live providers need their own measured comparisons.

## Warm behavior and acceptance boundary

No supported warm-session adapter is present in this delivered baseline, so
warm latency, warm context savings and warm storage growth are **not
applicable and not measured**. The existing issue permits the cold one-shot
fallback and requires warm reuse only when an adapter supports it and verified
cleanup/revocation preserves ownership. This record does not turn optional warm
implementation into a baseline dependency.

This record supplies measured startup/delegation, target-convergence
reconciliation, explicitly scoped idle overhead, cold two-round context and
storage behavior, and initial review targets for the applicable supported
path. It does not establish managed parent/child public conversation,
independent peer-control/private-read trial results, live-provider performance
or an actual generated forge proposal. Those acceptance gates remain separate
in [ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3) and
[OpenJules #5](https://github.com/niklas-olsson/OpenJules/issues/5).

See [the cold trial procedure](managed-children-stack-trial.md) for unchanged
commands, ownership and evidence interpretation.
