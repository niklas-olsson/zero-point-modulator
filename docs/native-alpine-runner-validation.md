# Native Alpine runner source validation

Agent-authored by ChatGPT `/root/assess_zpm`, 14 November 2026. This records
source checks for the [active runner](native-alpine-active-harness.md).
**Candidate acquisition, Docker, APK installation, sealed bootstrap and native
probes are NOT RUN.** Independent source/plan review and the exact hosted gates
remain prerequisites to any activation or merge.

The original inert plan, 173-artifact recipe and native-member catalog retain
SHA-256 `c89b824cb979e31eca9227c299e3de0f6c4f5877d5831612fbb5a537fc643a76`,
`e6b5c507ddc2f5b7bb3fe82db24f82d43f75c07b981477a5eb6690024b47878f` and
`6e8d0bc3e1257625b803a55c6f12282b5275d1beb96ee6d7667ada18779baef0`.
The separate active plan is 6,033 bytes, SHA-256
`8917e1b695e7154c9ecd60f40ab2defcd691ca715da81ae8b23b5dc60ebcb953`.
It remains unactivated. Its exact code manifest includes the workflow and the
complete local import set; no native result is inferred from that consistency.

## Local checks

| Check | Actual result and limit |
| --- | --- |
| Three focused candidate/runtime test modules | 68 tests PASS on the corrected source. Synthetic protocol/daemon/guest records and controlled ordinary host children only. |
| Isolated script `--check-plan` | Source plan admitted with native execution `NOT_RUN`; no directory, socket or child operation. |
| Python syntax, workflow YAML parsing and whitespace | PASS. |
| Full `python -B -m unittest discover -v` | 635 tests in 105.553 s: 634 passed, one existing environment error described below. This run preceded the final two-line elapsed-time refinement and checkout-configuration correction; the final focused set covers both changes and their added fixtures. |
| Offline local wheel build and isolated installation | PASS with CPython 3.12.14, setuptools 84.2.1 and wheel 0.48.0; no package index access. These local packaging tools are separate from the frozen Alpine candidate toolset. |
| Installed `zpm --help`, example validation, installed import path | PASS from the newly installed local wheel. |

The local wheel was `zero_point_modulator-0.1.0-py3-none-any.whl`, 70,934 bytes,
SHA-256 `a8fa596bfd6e5c112d7347b801d66b7ef941587492f79e7581f89bd9745607da`.
It is local packaging evidence, not a release or a qualified Alpine artifact.

The full-suite error was
`tests.test_operational_measurement.ProcessMeasurementTests.test_real_owned_process_cohort_uses_linux_counters`:
this workspace lacks the requested `/proc/<pid>/task/<pid>/children` entry.
The same one-test command reproduced `FileNotFoundError` on untouched baseline
`7cb9306`; its test and implementation files are unchanged by this work. This is
recorded separately from regressions. It is not a waiver of the full hosted CI
gate. Raw local full-suite and baseline logs are retained privately, SHA-256
`b4c1329137da8fd4d990ddbd811e574af7795e473d3554e55746d960a12b7760` and
`44ba573eab1e615c05e610e138fe3d6876c5b15b8f1e94e28f34a3a434268301`.

## Checkout compatibility review correction

Root review identified that the supported `actions/checkout@v4` writes local
`gc.auto=0`. Its `tryDisableAutomaticGarbageCollection` implementation invokes
only `git config --local gc.auto 0` ([action source](https://github.com/actions/checkout/blob/v4/src/git-command-manager.ts));
the reviewed source blobs are `2d3513897655e0ea2eb4efc2b2c9d6132a69549f`
and `9c789ac803e4d3b99463037d0ada654b79d8b863`. The runner now admits that
exact optional setting while refusing other values, keys and subsections. Two
new controlled tests cover detached and branch checkout configurations and
negative variants, including an attempted configuration include. The code-bound
plan is refreshed; inputs, caps, workflow and native status are unchanged.

## Ownership mapping and next evidence

The original draft author separately checked the eight fixed probes and the two
sensitive owner calls against actual ZPM `c5fb355` and OpenJules `0dd5bf4` source.
The managed-context probe uses its installed interpreter's `sys.prefix` selector,
without a substitute selector input. Production repeat bootstrap checks retained
completion and the unchanged source/wheel identities before scratch, build or
installation work. Read-only source mounts are compatible with those APIs.
This is a bounded mapping sanity check, not independent approval of that author's
draft, the completed runner or a native outcome.

Root and a reviewer who did not author the completed changes must review the exact
code/plan/commands and source evidence. Only then may one immutable source/plan
activation be admitted. Keep issue #63 open until the actual eleven native checks,
original resource bounds and owned cleanup are demonstrated. The result would
qualify only this tuple's native bootstrap and interfaces, not managed tasks,
services, models, browser execution or a public demonstrator distribution.
