# Managed three-service trial

This trial starts the actual Polyphony authority, OpenJules managed service and
ZPM watch loop in separate processes. It uses a real Docker Engine, an explicitly
identified local image, fresh scoped credentials and a newly created Git source
repository. It does not load a fake runtime, import a second `src` package into
the first service, or use the legacy OpenJules executor.

The task command makes a controlled Python code change and asserts its result.
It does not call a model provider, send a GitHub request, create a pull request,
perform semantic peer review or exercise child delegation. A passing result is
evidence of the managed root execution and recovery path, with these limits.

## Run on a Linux Docker host

Use clean checkouts at exact reviewed revisions of all three repositories
containing the managed service changes. Place the application checkouts beside
one another, not inside another checkout. The trial records their exact revisions
and uses the same source-pin verification as the installer. Run the trial with
Python 3.11 or newer.

The trial prepares a real private installation bundle, explicitly bootstraps its
three Python environments, starts its generated manager at a zero-worker target
and requires its actual `doctor` capability check to pass. It then stops that
manager and reuses the bundle's exact configuration, credentials, interpreters
and service commands for the recovery scenarios below. It does not maintain a
separate fixture configuration schema.
It repeats preparation and bootstrap and requires the original credentials,
sealed manifest and completed-environment records to remain unchanged.

The CI job currently pins:

| Component | Revision |
| --- | --- |
| OpenJules | `3e62230b8cfca02b4aaae19ec7a194f919fa822c`, merged owned cleanup, initial child outcomes and durable control replay |
| Keystone Polyphony | `653c0a71a921c0d23b8e0171277524125e929e66`, initial child outcome authority candidate over the merged grants and conversation baseline |
| ZPM | The workflow's checked-out candidate revision, recorded in the report |

All installed-stack jobs use this same three-repository candidate set. The HTTP
fixture and component CI do not qualify the changed installation: all ZPM jobs,
including the command, model, child, two-installation isolation and shared-service
Docker trials, must pass on the exact candidate revision. Qualification and CI evidence remain tracked in
[ZPM #3](https://github.com/niklas-olsson/zero-point-modulator/issues/3).
Update all component pins and trial guides together with the workflow, then
rerun all integration gates.

Build the small test-only image and record the resulting immutable image ID:

```sh
docker build --iidfile /absolute/private/managed-trial-image-id \
  - < tests/contracts/Dockerfile.managed-trial
```

The Dockerfile's default upstream Python tag is a test build input. It is not
an approved production profile reference. The trial records and uses the
resulting full `sha256:` image ID. A stricter build can supply an approved
digest through `--build-arg BASE_IMAGE=python@sha256:...`.

Run the trial with a new, retained working directory:

```sh
python tests/contracts/check_stack.py \
  --polyphony-checkout /absolute/checkouts/keystone-polyphony \
  --openjules-checkout /absolute/checkouts/OpenJules \
  --image "$(cat /absolute/private/managed-trial-image-id)" \
  --work-dir /absolute/private/managed-stack-trial
```

All listeners bind loopback. The harness disables proxy inheritance for its
authenticated HTTP clients and passes only each service's required test
credential to that process. It never prints tokens. The explicit work directory
contains credential files, databases, Docker capture archives and service logs.
It remains in place on both success and failure so a failed capture can be
investigated without deleting its recovery state. Do not upload that directory
wholesale. The successful `report.json` contains no credential values. On failure,
`diagnostics.json` contains only bounded log tails with the generated credentials
removed. CI retains these two selected files. It never uploads complete service
logs, configuration, secret files, journals or workspace archives.

## Assertions

The harness fails unless the generated installation's bootstrap, manager and
doctor succeed, and it observes all of these through the real boundaries:

1. ZPM's zero target creates no worker and target two produces two distinct
   Docker-backed worker incarnations.
2. Killing the OpenJules API leaves two uncertain reservations. Restarting
   OpenJules and ZPM with the same journals recovers the original identities.
3. Repeated ingress delivery returns one canonical task. A changed retry is
   rejected, as are another repository and another deployment.
4. Ignored host `.env` data and the host's `.git` directory do not enter the
   task's exported source. The task actually changes `answer.py`, runs its controlled assertion and
   terminates. Polyphony retains the patch, log and manifest under verified
   SHA-256 content references and a fenced runtime receipt.
5. The patch applies to a fresh checkout at the approved base and the resulting
   Python function independently returns the expected value.
6. The result becomes a candidate awaiting review. No runtime credential
   produces acceptance or repository admission.
7. A second controlled task modifies a file and then sleeps. The harness kills
   OpenJules while the task is running. The container's independent watchdog
   terminates it at its existing deadline without the API process.
8. Capacity remains reserved while capture is unavailable. After restart, the
   same canonical attempt receives a verified capture, including a durable
   workspace archive containing the interrupted file. It does not become a
   successful candidate.
9. Completed worker incarnations retire. Capacity reaches three, then one, then
   zero. An authority service restart preserves both attempts and the candidate.
10. With two workers running, the installed CLI removes enrollment and the actual
    controller drains both to zero. Repeated preparation keeps enrollment
    disabled. A full service restart, repeated bootstrap and explicit
    re-enrollment resume capacity with new worker incarnations and the original
    credentials, tasks, attempts, candidate and canonical artifact contents.

The repeat/removal scenario qualifies reuse of a complete retained installation.
Interrupted preparation recovery, version upgrades and destructive deletion are
separate remaining acceptance cases.

The harness intentionally does not remove retained container and volume
tombstones. On a disposable CI runner the host is discarded after the job. On a
developer host, retain the work directory until the task and artifact outcomes
have been inspected, and use only verified ownership when retiring test data.

The separate [HTTP lifecycle trial](http-contract-trial.md) remains useful for
deterministically injecting lost replies and pre-dispatch interruptions. Its
subprocess fixtures are a different proof from this actual Docker execution
trial. Neither result implies that every child-agent, model-access, review and
publication feature has shipped.
