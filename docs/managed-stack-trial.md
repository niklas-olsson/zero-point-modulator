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
and service commands for the recovery scenarios below. The generated-installation scenarios do not maintain a
separate fixture configuration schema. A final runtime-contract scene, described
below, deliberately uses two explicit OpenJules service configurations after the
generated installation has stopped.
It repeats preparation and bootstrap and requires the original credentials,
sealed manifest and completed-environment records to remain unchanged.
The dedicated OpenJules interpreter also proves the fixed managed-context
contract during bootstrap, repeated bootstrap, start and doctor. Before any
service starts, the harness invokes a syntactically valid legacy `src.cli run`
command with no credentials, requires the explicit managed-installation refusal,
and compares retained state hashes and the entire Docker container/volume
inventory. It preserves the selector bytes and then runs the normal canonical
execution and recovery scene. This proves the installed refusal boundary for
the tested source set, without treating an offline protocol fixture as an
actual legacy entry-point result.

The CI job currently pins:

| Component | Revision |
| --- | --- |
| OpenJules | `a1547a7743996d2db9866013135e5adb75c0ec7c`, merged public-discussion component and shared HTTP transport |
| Keystone Polyphony | `143d77c92ca8699ad7ccad4de98a540da6540a09`, merged initial child outcome authority |
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


## Two independent runtime services, one protected attempt

After the generated installation reaches zero and its services stop, the same
job runs a separate runtime-contract scene using the existing installed component
interpreters and immutable command image. A new private seed supplies one
canonical authority and approved command profile. The harness creates two
explicit OpenJules configurations with distinct ports, lifecycle/runtime/
dispatcher journals, controller credentials, runtime principals and worker
incarnations. It acts as the scoped lifecycle client. No ZPM watch loop operates
in this scene.

This is evidence for two actual managed runtime processes using one authority.
It does not add or qualify a generated replicated-OpenJules installation, a
shared-journal replica policy or a new capacity scheduler. The managed dispatcher
retains its existing exclusive service lock for each journal directory.

The scene requires all of the following:

1. Both actual managed apps report ready and own distinct idle Docker containers.
   Duplicate `issue_comment`, `pull_request_review` and
   `pull_request_review_comment` shaped deliveries to their legacy
   `/api/webhook` route all return 404 without changing protected histories.
   These are explicit route refusals, not successful webhook ingestion.
2. Two initialized, gated production Polyphony CLI processes deliver the same
   canonical enrollment and receive one task. This separate supported ingress
   path is not described as a webhook or public-conversation adapter.
3. A bounded loopback pass-through barrier holds two actual authenticated
   `/managed/claim` requests, one from each runtime principal and worker, before
   forwarding either to the real authority. Both original request keys are
   already durable in their own dispatcher journals. The unchanged real
   responses must be one 200 allocation and one 409 with the exact
   `claim_not_allocated` nonallocation body.
4. The winning controlled command remains active. All-scope read-only journals
   and the canonical snapshot must show one unresolved attempt and one original
   Docker exec, with its original input, binding, request key and deadlines. The
   losing runtime has no execution and clears only its explicitly rejected
   unbound claiming row. A remaining allocation and its time budget stay
   available, so a quota limit cannot explain the losing claim.
5. The harness kills and reaps only the losing service, then starts a fresh
   process with exactly its original command, environment and journals. It must
   retain its original worker/container and complete a fresh actual canonical
   snapshot pull. Before the replacement starts, the harness explicitly fences
   the shared fixture proxy: stop acceptance, close its listener/backlog, kill
   and reap the old loser, and finish every accepted old handler before reopening
   the same endpoint. Accepted socket and thread ownership is registered before
   each handler starts, and shutdown/reaping has one five-second deadline. An
   incomplete fence never reopens. Completion counters are scoped to the proxy
   generation, so an old queued or timed-out request cannot supply new-process
   evidence. The actual authority and winning service keep their original PIDs.
   The brief shared observer-transport interruption is deliberate harness
   behavior; it is not an authority outage or a new runtime replica policy.
   The winner's counter must advance and its frozen execution, input, deadlines
   and canonical history must remain unchanged across this transition.
6. The losing worker receives its own original scoped drain and stop requests,
   becomes terminal with capture, and still has no protected execution. Its
   exact request keys, payloads and completed journal records are checked. Only
   then is the winning command released. This ordering avoids the supported
   interval between terminal observation and candidate publication in which a
   later task attempt could otherwise become eligible.
7. The winner produces one canonical patch, log, manifest and review-pending
   candidate under its original attempt. The patch changes only the approved
   answer file, applies to the original source and passes the independent
   assertion. Both worker histories end fenced and captured, with no active
   execution. Original seed credentials and configuration remain unchanged.

The original generated-installation command scene keeps its 12-second allocation.
This separate contention profile explicitly uses a 60-second canonical attempt,
two allowed attempts and a 120-second task budget inside the existing 90-second
runtime watchdog. The initial architecture review rejected reusing 12 seconds
for the additional real process restart and Docker stop/capture sequence. This
was a design correction before runtime execution, not an observed product
failure. The five-second claim barrier remains below the existing ten-second
runtime authority-client deadline. Existing readiness, control and workflow
timeouts are unchanged. No timeout or exhausted quota counts as successful
contention evidence.

The report nests this scene under `two_runtime_contention`, including the actual
claim outcomes, worker/exec identities, original deadlines, proxy generation
fence, fresh restart pull and counter observations. Cleanup attempts both owned workers' scoped drain and
stop controls and reaps every owned service and proxy request. A cleanup error
fails qualification while preserving the original failure. Only a downstream
client disconnect during its explicit old-generation fence is recorded as a
closure without a successful response; upstream transport failures remain
qualification errors. Complete private
journals, captures and logs remain in the retained directory; exported failure
tails redact the scene's distinct credentials and are bounded to 4,000 UTF-8
bytes per service log.

The deterministic tests use real loopback HTTP and SQLite fixtures to challenge
the barrier, response/identity/history predicates, fresh-pull counter and cleanup
paths. The real HTTP regression holds an accepted request before its handler
thread starts, proves that it remains owned during shutdown, and requires the
old generation to finish before a fresh response can count. These checks do not
substitute for the actual Docker job. This scene does not
exercise loss of the winning claim reply, shared-journal replicas, successful
standalone legacy ingress, public conversation, model inference, independent
review or forge publication.

Source review first found that a queued old request could satisfy a single
post-restart completion counter. A proposed two-completion bound was also
rejected: serialized dispatcher ticks can abandon requests on timeout and queue
later requests, so a lifetime bound of one old request is unsupported. The final
transport-generation fence removes that assumption. These were test-evidence
findings before actual runtime qualification, not observed product failures.
